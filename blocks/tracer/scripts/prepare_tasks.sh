#!/usr/bin/env bash
# Prepare Harbor task directories from config.yaml task_source.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${TRAJGEN_CONFIG:-$BLOCK_DIR/config.yaml}"
[[ "$CONFIG" = /* ]] || CONFIG="$BLOCK_DIR/$CONFIG"
OVERWRITE=0
RESULT_FILE=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/prepare_tasks.sh
  bash scripts/prepare_tasks.sh --config config.yaml
  bash scripts/prepare_tasks.sh --config config.some-variant.yaml --overwrite

Stages Harbor task directories under artifacts/tasks/<dataset>.

A local source (provider: local) is staged by symlinking each task into place —
one link per task id, so a verifiable_tasks.txt manifest still filters what is
staged, and nothing is duplicated on disk. A huggingface source is copied out of
its downloaded snapshot.

Publishes an atomic pointer to an owned version directory. The selected source,
manifest, settings, and file metadata determine cache reuse. Existing real dataset
directories or unrelated symlinks are never replaced, including with --overwrite.
Choose a new empty dataset_path or move old staging yourself after checking jobs.
--overwrite forces a new Hugging Face download; local sources are always checked.
--result-file PATH records the fixed dataset path for this invocation.
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
    --result-file)
      [[ $# -ge 2 ]] || { echo "ERROR: --result-file requires a value" >&2; exit 2; }
      RESULT_FILE="$2"
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

find_valid_task_root() {
  local root="$1"
  local dataset_name="$2"
  python3 - "$root" "$dataset_name" <<'PY'
from pathlib import Path
import sys

# Freeze a producer's published pointer before reading its manifest. A later
# publication must not combine one batch's IDs with another batch's task dirs.
root = Path(sys.argv[1]).resolve()
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

PROVIDER="$(python3 "$BLOCK_DIR/scripts/task_source.py" --config "$CONFIG" --field provider)"
DATASET_NAME="$(python3 "$BLOCK_DIR/scripts/task_source.py" --config "$CONFIG" --field dataset_name)"
SPLIT="$(cfg runtime_info.input.task_source.split)"
HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"
HARBOR_UV_RAW="$(cfg meta_info.environment.harbor_uv)"
TARGET_RAW="$(cfg runtime_info.input.harbor_job.dataset_path)"

[[ -n "$PROVIDER" ]] || { echo "ERROR: runtime_info.input.task_source.provider is empty" >&2; exit 1; }
[[ -n "$DATASET_NAME" ]] || { echo "ERROR: selected task source is empty" >&2; exit 1; }
[[ -n "$HARBOR_PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.harbor.path is empty" >&2; exit 1; }
[[ -n "$HARBOR_UV_RAW" ]] || { echo "ERROR: meta_info.environment.harbor_uv is empty" >&2; exit 1; }

DATASET_BASENAME="$(cfg runtime_info.input.harbor_job.dataset)"
if [[ -z "$DATASET_BASENAME" ]]; then
  DATASET_BASENAME="$(python3 "$BLOCK_DIR/scripts/task_source.py" --config "$CONFIG" --field dataset_id)"
fi
if [[ -z "$TARGET_RAW" ]]; then
  TARGET_RAW="artifacts/tasks/$DATASET_BASENAME"
fi

TARGET_DIR="$(abspath "$TARGET_RAW")"
HARBOR_DIR="$(abspath "$HARBOR_PATH_RAW")"
HARBOR_UV_DIR="$(abspath "$HARBOR_UV_RAW")"
HARBOR_PYTHON="$HARBOR_UV_DIR/bin/python"
STAGE_ARGS=(--block-dir "$BLOCK_DIR" --config "$CONFIG" --target "$TARGET_DIR")
[[ -z "$RESULT_FILE" ]] || STAGE_ARGS+=(--result-file "$RESULT_FILE")

echo "=== tracer prepare tasks ==="
echo "Config:   $CONFIG"
echo "Provider: $PROVIDER"
echo "Dataset:  $DATASET_NAME"
[[ -n "$SPLIT" ]] && echo "Split:    $SPLIT"
echo "Target:   $TARGET_RAW"

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
    python3 "$BLOCK_DIR/scripts/stage_tasks.py" "${STAGE_ARGS[@]}" --source "$VALID_ROOT"
    ;;
  huggingface)
    python3 "$BLOCK_DIR/scripts/stage_tasks.py" "${STAGE_ARGS[@]}" --check-target
    if [[ "$OVERWRITE" != 1 ]]; then
      if python3 "$BLOCK_DIR/scripts/stage_tasks.py" "${STAGE_ARGS[@]}" --reuse-copy; then
        exit 0
      else
        status=$?
        [[ "$status" == 1 ]] || exit "$status"
      fi
    fi
    # Download only into a newly allocated directory. Never remove a user's old
    # dataset/cache based on its basename or an untrusted target configuration.
    mkdir -p "$BLOCK_DIR/artifacts/tasks/.downloads"
    DOWNLOAD_DIR="$(mktemp -d "$BLOCK_DIR/artifacts/tasks/.downloads/snapshot-XXXXXXXX")"
    trap 'rm -rf -- "$DOWNLOAD_DIR"' EXIT
    CACHE_DIR="$DOWNLOAD_DIR/snapshot"
    EXTRACT_DIR="$DOWNLOAD_DIR/extracted"
    echo "Downloading Hugging Face dataset snapshot to a private temporary directory ..."
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
        echo "Checked both the raw snapshot and its safely extracted archives." >&2
        echo "Expected child task dirs with task.toml, instruction.md, environment/, and tests/." >&2
        echo "If this dataset needs conversion, add an explicit adapter flow to config and prepare_tasks.sh." >&2
        exit 1
      fi
    fi

    python3 "$BLOCK_DIR/scripts/stage_tasks.py" "${STAGE_ARGS[@]}" --source "$VALID_ROOT" --copy
    ;;
  *)
    echo "ERROR: unsupported task_source.provider: $PROVIDER" >&2
    echo "Supported providers: local, huggingface" >&2
    exit 1
    ;;
esac

echo "Prepared Harbor tasks at $TARGET_RAW."
