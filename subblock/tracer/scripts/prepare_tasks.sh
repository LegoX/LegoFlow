#!/usr/bin/env bash
# Prepare Harbor task directories from config.yaml task_source.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"
OVERWRITE=0

usage() {
  cat <<'EOF'
Usage:
  bash scripts/prepare_tasks.sh
  bash scripts/prepare_tasks.sh --config config.yaml
  bash scripts/prepare_tasks.sh --config config.some-variant.yaml --overwrite

Prepares Harbor task directories under artifacts/tasks/<dataset>.
Existing valid task directories are reused. Existing invalid directories require
--overwrite before they are replaced.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      [[ $# -ge 2 ]] || { echo "ERROR: --config requires a value" >&2; exit 2; }
      if [[ "$2" = /* ]]; then
        CONFIG="$2"
      else
        CONFIG="$BLOCK_DIR/$2"
      fi
      shift 2
      ;;
    --overwrite)
      OVERWRITE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cfg() {
  python3 - "$CONFIG" "$1" <<'PY'
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required", file=sys.stderr)
    sys.exit(2)

config_path, dotted_key = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}

value = data
for part in dotted_key.split("."):
    if not isinstance(value, dict):
        value = None
        break
    value = value.get(part)

if value is None:
    print("")
elif isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

abspath() {
  local p="$1"
  if [[ "$p" = /* ]]; then
    echo "$p"
  else
    echo "$BLOCK_DIR/$p"
  fi
}

validate_tasks() {
  local root="$1"
  python3 - "$root" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
if not root.is_dir():
    print("missing")
    raise SystemExit(1)

required_files = ["task.toml", "instruction.md"]
required_dirs = ["environment", "tests"]

valid = []
for child in sorted(p for p in root.iterdir() if p.is_dir()):
    if all((child / name).is_file() for name in required_files) and all(
        (child / name).is_dir() for name in required_dirs
    ):
        valid.append(child.name)

if not valid:
    print("invalid")
    raise SystemExit(1)

print(f"valid:{len(valid)}")
PY
}

copy_harbor_tasks() {
  local src="$1"
  local dst="$2"
  python3 - "$src" "$dst" <<'PY'
from pathlib import Path
import shutil
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
dst.parent.mkdir(parents=True, exist_ok=True)
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst, symlinks=True)
PY
}

copy_harbor_tasks_filtered() {
  local src="$1"
  local dst="$2"
  local manifest="$3"
  python3 - "$src" "$dst" "$manifest" <<'PY'
from pathlib import Path
import shutil
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
manifest = Path(sys.argv[3])

with manifest.open(encoding="utf-8") as fh:
    allowed = {line.strip() for line in fh if line.strip() and not line.startswith("#")}

if not allowed:
    print(f"ERROR: manifest is empty: {manifest}", file=sys.stderr)
    sys.exit(1)

dst.parent.mkdir(parents=True, exist_ok=True)
if dst.exists():
    shutil.rmtree(dst)
dst.mkdir(parents=True)

copied = 0
missing = []
for task_id in sorted(allowed):
    src_task = src / task_id
    if not src_task.is_dir():
        missing.append(task_id)
        continue
    shutil.copytree(src_task, dst / task_id, symlinks=True)
    copied += 1

for task_id in missing:
    print(f"WARN: manifest entry missing from source: {task_id}", file=sys.stderr)
print(f"filtered:{copied}/{len(allowed)} copied (manifest={manifest.name})")
PY
}

find_valid_task_root() {
  local root="$1"
  local dataset_name="$2"
  python3 - "$root" "$dataset_name" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
dataset_name = sys.argv[2].split("/")[-1]
required_files = ["task.toml", "instruction.md"]
required_dirs = ["environment", "tests"]

def is_task_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    for child in path.iterdir():
        if not child.is_dir():
            continue
        if all((child / name).is_file() for name in required_files) and all(
            (child / name).is_dir() for name in required_dirs
        ):
            return True
    return False

candidates = [root, root / dataset_name]
candidates.extend([p for p in root.iterdir() if p.is_dir()])
for candidate in candidates:
    if is_task_root(candidate):
        print(candidate)
        raise SystemExit(0)

print("")
PY
}

extract_archives() {
  local src="$1"
  local dst="$2"
  python3 - "$src" "$dst" <<'PY'
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import zipfile

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
if dst.exists():
    shutil.rmtree(dst)
dst.mkdir(parents=True, exist_ok=True)

archive_suffixes = (".tar.gz", ".tgz", ".tar", ".zip")
archives = [
    p for p in src.rglob("*")
    if p.is_file() and any(str(p).lower().endswith(suffix) for suffix in archive_suffixes)
]

def ensure_within_dir(base: Path, candidate: Path) -> None:
    base_resolved = base.resolve()
    candidate_resolved = candidate.resolve(strict=False)
    try:
        candidate_resolved.relative_to(base_resolved)
    except ValueError:
        raise ValueError(f"archive member escapes extraction directory: {candidate}")

def safe_extract_tar(tf: tarfile.TarFile, out_dir: Path) -> None:
    for member in tf.getmembers():
        target = out_dir / member.name
        ensure_within_dir(out_dir, target)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise ValueError(f"refusing to extract non-file archive member: {member.name}")
        source = tf.extractfile(member)
        if source is None:
            raise ValueError(f"could not read archive member: {member.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with source, open(target, "wb") as fh:
            shutil.copyfileobj(source, fh)

def safe_extract_zip(zf: zipfile.ZipFile, out_dir: Path) -> None:
    for member in zf.infolist():
        target = out_dir / member.filename
        ensure_within_dir(out_dir, target)
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if file_type not in (0, stat.S_IFREG):
            raise ValueError(f"refusing to extract non-file archive member: {member.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as source, open(target, "wb") as fh:
            shutil.copyfileobj(source, fh)

for archive in archives:
    name = archive.name
    for suffix in archive_suffixes:
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    out_dir = dst / name
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        if tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tf:
                safe_extract_tar(tf, out_dir)
        elif zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                safe_extract_zip(zf, out_dir)
        else:
            continue
    except Exception as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        print(f"ERROR extracting {archive}: {exc}", file=sys.stderr)
        continue
    print(out_dir)
PY
}

[[ -f "$CONFIG" ]] || { echo "ERROR: config not found: $CONFIG" >&2; exit 1; }

PROVIDER="$(cfg runtime_info.input.task_source.provider)"
DATASET_NAME="$(cfg runtime_info.input.task_source.dataset_name)"
SPLIT="$(cfg runtime_info.input.task_source.split)"
HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"
HARBOR_UV_RAW="$(cfg meta_info.environment.harbor_uv)"
TARGET_RAW="$(cfg runtime_info.input.harbor_job.dataset_path)"

[[ -n "$PROVIDER" ]] || { echo "ERROR: runtime_info.input.task_source.provider is empty" >&2; exit 1; }
[[ -n "$DATASET_NAME" ]] || { echo "ERROR: runtime_info.input.task_source.dataset_name is empty" >&2; exit 1; }
[[ -n "$HARBOR_PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.harbor.path is empty" >&2; exit 1; }
[[ -n "$HARBOR_UV_RAW" ]] || { echo "ERROR: meta_info.environment.harbor_uv is empty" >&2; exit 1; }

DATASET_BASENAME="$(basename "$DATASET_NAME")"
if [[ -z "$TARGET_RAW" ]]; then
  TARGET_RAW="artifacts/tasks/$DATASET_BASENAME"
fi

TARGET_DIR="$(abspath "$TARGET_RAW")"
HARBOR_DIR="$(abspath "$HARBOR_PATH_RAW")"
HARBOR_UV_DIR="$(abspath "$HARBOR_UV_RAW")"
HARBOR_PYTHON="$HARBOR_UV_DIR/bin/python"
CACHE_DIR="$BLOCK_DIR/artifacts/tasks/.cache/$DATASET_BASENAME"
EXTRACT_DIR="$BLOCK_DIR/artifacts/tasks/.cache/${DATASET_BASENAME}_extracted"

echo "=== tracer prepare tasks ==="
echo "Config:   $CONFIG"
echo "Provider: $PROVIDER"
echo "Dataset:  $DATASET_NAME"
[[ -n "$SPLIT" ]] && echo "Split:    $SPLIT"
echo "Target:   $TARGET_RAW"

if [[ -d "$TARGET_DIR" ]]; then
  if validate_tasks "$TARGET_DIR" >/tmp/tracer_prepare_validate.$$ 2>/dev/null; then
    STATUS="$(cat /tmp/tracer_prepare_validate.$$)"
    rm -f /tmp/tracer_prepare_validate.$$
    echo "Target already contains Harbor task directories ($STATUS)."
    exit 0
  fi
  rm -f /tmp/tracer_prepare_validate.$$
  if [[ "$OVERWRITE" != "1" ]]; then
    echo "ERROR: target exists but does not look like Harbor task directories: $TARGET_RAW" >&2
    echo "Re-run with --overwrite to replace it." >&2
    exit 1
  fi
  rm -rf "$TARGET_DIR"
fi

[[ -e "$HARBOR_DIR/.git" ]] || { echo "ERROR: Harbor repo missing at $HARBOR_PATH_RAW; run scripts/update_repos.sh first" >&2; exit 1; }
[[ -x "$HARBOR_PYTHON" ]] || { echo "ERROR: Harbor Python env missing at $HARBOR_PYTHON; install environment first" >&2; exit 1; }

case "$PROVIDER" in
  local)
    SRC_DIR="$(abspath "$DATASET_NAME")"
    [[ -d "$SRC_DIR" ]] || { echo "ERROR: local task source not found: $DATASET_NAME" >&2; exit 1; }
    VALID_ROOT="$(find_valid_task_root "$SRC_DIR" "$DATASET_NAME")"
    if [[ -z "$VALID_ROOT" ]]; then
      echo "ERROR: local source is not a Harbor task directory: $DATASET_NAME" >&2
      echo "Expected child task dirs with task.toml, instruction.md, environment/, and tests/." >&2
      exit 1
    fi
    MANIFEST="$VALID_ROOT/verifiable_tasks.txt"
    if [[ -f "$MANIFEST" ]]; then
      echo "Manifest found: $MANIFEST — copying only listed tasks."
      copy_harbor_tasks_filtered "$VALID_ROOT" "$TARGET_DIR" "$MANIFEST"
    else
      echo "No verifiable_tasks.txt in source — copying all task dirs."
      copy_harbor_tasks "$VALID_ROOT" "$TARGET_DIR"
    fi
    ;;
  huggingface)
    mkdir -p "$(dirname "$CACHE_DIR")"
    rm -rf "$CACHE_DIR"
    echo "Downloading Hugging Face dataset snapshot to artifacts/tasks/.cache/$DATASET_BASENAME ..."
    if ! "$HARBOR_PYTHON" - "$DATASET_NAME" "$CACHE_DIR" <<'PY'
import sys
from huggingface_hub import snapshot_download

repo_id, local_dir = sys.argv[1:3]
snapshot_download(
    repo_id=repo_id,
    repo_type="dataset",
    local_dir=local_dir,
    local_dir_use_symlinks=False,
)
PY
    then
      echo "ERROR: failed to download Hugging Face dataset '$DATASET_NAME'." >&2
      echo "If this dataset is private, authenticate with Hugging Face in the Harbor env before retrying." >&2
      exit 1
    fi

    VALID_ROOT="$(find_valid_task_root "$CACHE_DIR" "$DATASET_NAME")"
    if [[ -z "$VALID_ROOT" ]]; then
      echo "Snapshot root is not a Harbor task directory; checking archives ..."
      mapfile -t EXTRACTED_DIRS < <(extract_archives "$CACHE_DIR" "$EXTRACT_DIR")
      for extracted in "${EXTRACTED_DIRS[@]:-}"; do
        candidate="$(find_valid_task_root "$extracted" "$DATASET_NAME")"
        if [[ -n "$candidate" ]]; then
          VALID_ROOT="$candidate"
          break
        fi
      done
      if [[ -z "$VALID_ROOT" ]]; then
        echo "ERROR: downloaded dataset is not a prebuilt Harbor task directory." >&2
        echo "Checked both raw snapshot and extracted archives under artifacts/tasks/.cache/." >&2
        echo "Expected child task dirs with task.toml, instruction.md, environment/, and tests/." >&2
        echo "If this dataset needs conversion, add an explicit adapter flow to config and prepare_tasks.sh." >&2
        exit 1
      fi
    fi

    copy_harbor_tasks "$VALID_ROOT" "$TARGET_DIR"
    ;;
  *)
    echo "ERROR: unsupported task_source.provider: $PROVIDER" >&2
    echo "Supported providers: local, huggingface" >&2
    exit 1
    ;;
esac

validate_tasks "$TARGET_DIR" >/tmp/tracer_prepare_validate.$$
STATUS="$(cat /tmp/tracer_prepare_validate.$$)"
rm -f /tmp/tracer_prepare_validate.$$
echo "Prepared Harbor tasks at $TARGET_RAW ($STATUS)."
