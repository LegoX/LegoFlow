#!/usr/bin/env bash
# Convert Harbor trajectories under artifacts/jobs/<job>/ into IM + LF SFT data
# using the swe_data_process repo. Output is written under
# runtime_info.input.sft_conversion.out_dir/<job>/.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/convert_trajectories.sh
  bash scripts/convert_trajectories.sh --job <name|latest>
  bash scripts/convert_trajectories.sh --job latest --scaffold claude_code
  bash scripts/convert_trajectories.sh --job <name> --out-dir artifacts/sft_data --max-instances 100
  bash scripts/convert_trajectories.sh --job latest --skip-unchanged

Converts a Harbor job's trajectory logs into:
  <out_dir>/<job>/im.jsonl   (intermediate OpenAI-style messages)
  <out_dir>/<job>/lf.json    (LLaMA-Factory ShareGPT array)

Defaults are read from runtime_info.input.sft_conversion in config.yaml.
--job latest resolves to the most recently modified directory under artifacts/jobs.
--skip-unchanged exits early (no reconversion) when the job's resolved
  (reward=1.0) instance set and conversion inputs are unchanged since the last
  run, tracked via <out_dir>/<job>/.convert_sig.json. Useful for polling loops.
EOF
}

JOB_ARG="latest"
SCAFFOLD_OVERRIDE=""
OUT_DIR_OVERRIDE=""
MAX_INSTANCES_OVERRIDE=""
EXCLUDE_REPOS_OVERRIDE=""
EXCLUDE_REPOS_OVERRIDE_SET=0
SKIP_UNCHANGED=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --job)
      [[ $# -ge 2 ]] || { echo "ERROR: --job requires a value" >&2; exit 2; }
      JOB_ARG="$2"
      shift 2
      ;;
    --skip-unchanged)
      SKIP_UNCHANGED=1
      shift
      ;;
    --scaffold)
      [[ $# -ge 2 ]] || { echo "ERROR: --scaffold requires a value" >&2; exit 2; }
      SCAFFOLD_OVERRIDE="$2"
      shift 2
      ;;
    --out-dir)
      [[ $# -ge 2 ]] || { echo "ERROR: --out-dir requires a value" >&2; exit 2; }
      OUT_DIR_OVERRIDE="$2"
      shift 2
      ;;
    --max-instances)
      [[ $# -ge 2 ]] || { echo "ERROR: --max-instances requires a value" >&2; exit 2; }
      MAX_INSTANCES_OVERRIDE="$2"
      shift 2
      ;;
    --exclude-repos-file)
      [[ $# -ge 2 ]] || { echo "ERROR: --exclude-repos-file requires a value" >&2; exit 2; }
      EXCLUDE_REPOS_OVERRIDE="$2"
      EXCLUDE_REPOS_OVERRIDE_SET=1
      shift 2
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

derive_scaffold() {
  # Map runtime_info.input.agent.name -> swe_data_process scaffold key.
  local agent_name="$1"
  case "$agent_name" in
    custom-claude-code|claude-code|claude_code)
      echo "claude_code"
      ;;
    open-code|opencode|open_code)
      echo "open_code"
      ;;
    openhands-sdk|openhands_sdk|openhands)
      echo "openhands_sdk"
      ;;
    terminus2|terminus-2)
      echo "terminus2"
      ;;
    *)
      echo ""
      ;;
  esac
}

scaffold_module() {
  case "$1" in
    claude_code)   echo "swe_data_process.claudecode_opencode.convert_cc_to_im" ;;
    open_code)     echo "swe_data_process.claudecode_opencode.convert_oc_to_im" ;;
    openhands_sdk) echo "swe_data_process.openhands.convert_openhands_sdk_to_im" ;;
    terminus2)     echo "swe_data_process.terminus2.convert_terminus2_to_im" ;;
    *)             echo "" ;;
  esac
}

[[ -f "$CONFIG" ]] || { echo "ERROR: config.yaml not found at $CONFIG" >&2; exit 1; }

SWE_DP_PATH_RAW="$(cfg meta_info.repositories.swe_data_process.path)"
SWE_DP_UV_RAW="$(cfg meta_info.environment.swe_data_process_uv)"
HARBOR_JOBS_DIR_RAW="$(cfg runtime_info.input.harbor_job.jobs_dir)"
AGENT_NAME="$(cfg runtime_info.input.agent.name)"
SCAFFOLD_CFG="$(cfg runtime_info.input.sft_conversion.scaffold)"
OUT_DIR_CFG="$(cfg runtime_info.input.sft_conversion.out_dir)"
MAX_INSTANCES_CFG="$(cfg runtime_info.input.sft_conversion.max_instances)"
EXCLUDE_REPOS_CFG="$(cfg runtime_info.input.sft_conversion.exclude_repos_file)"

[[ -n "$SWE_DP_PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.swe_data_process.path is empty" >&2; exit 1; }
[[ -n "$SWE_DP_UV_RAW" ]] || { echo "ERROR: meta_info.environment.swe_data_process_uv is empty" >&2; exit 1; }
[[ -n "$HARBOR_JOBS_DIR_RAW" ]] || { echo "ERROR: runtime_info.input.harbor_job.jobs_dir is empty" >&2; exit 1; }
[[ -n "$AGENT_NAME" ]] || { echo "ERROR: runtime_info.input.agent.name is empty" >&2; exit 1; }
[[ -n "$SCAFFOLD_CFG" ]] || SCAFFOLD_CFG="auto"
[[ -n "$OUT_DIR_CFG" ]] || OUT_DIR_CFG="artifacts/sft_data"

SWE_DP_DIR="$(abspath "$SWE_DP_PATH_RAW")"
SWE_DP_UV_ABS="$(abspath "$SWE_DP_UV_RAW")"
HARBOR_JOBS_DIR="$(abspath "$HARBOR_JOBS_DIR_RAW")"

[[ -e "$SWE_DP_DIR/.git" ]] || { echo "ERROR: swe_data_process repo missing at $SWE_DP_PATH_RAW; run scripts/update_repos.sh --repo swe_data_process" >&2; exit 1; }
[[ -x "$SWE_DP_UV_ABS/bin/python" ]] || { echo "ERROR: swe_data_process uv env missing or broken at $SWE_DP_UV_ABS; run bash scripts/setup_swe_data_process_env.sh" >&2; exit 1; }
[[ -d "$HARBOR_JOBS_DIR" ]] || { echo "ERROR: Harbor jobs dir not found: $HARBOR_JOBS_DIR" >&2; exit 1; }

if [[ "$JOB_ARG" == "latest" ]]; then
  # Most recently modified *directory* under the jobs root. Restrict to dirs
  # (following symlinks) so stray non-directory entries can't be selected.
  JOB_NAME="$(
    find -L "$HARBOR_JOBS_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@\t%f\n' 2>/dev/null \
      | sort -rn | head -n 1 | cut -f2-
  )"
  [[ -n "$JOB_NAME" ]] || { echo "ERROR: no job directories found under $HARBOR_JOBS_DIR" >&2; exit 1; }
else
  JOB_NAME="$JOB_ARG"
fi
JOB_DIR="$HARBOR_JOBS_DIR/$JOB_NAME"
[[ -d "$JOB_DIR" ]] || { echo "ERROR: job dir not found: $JOB_DIR" >&2; exit 1; }

if [[ -n "$SCAFFOLD_OVERRIDE" ]]; then
  SCAFFOLD="$SCAFFOLD_OVERRIDE"
else
  SCAFFOLD="$SCAFFOLD_CFG"
fi
if [[ "$SCAFFOLD" == "auto" ]]; then
  SCAFFOLD="$(derive_scaffold "$AGENT_NAME")"
  [[ -n "$SCAFFOLD" ]] || { echo "ERROR: cannot auto-derive scaffold from agent.name='$AGENT_NAME'; set sft_conversion.scaffold explicitly (claude_code|open_code|openhands_sdk|terminus2)" >&2; exit 1; }
fi
MODULE="$(scaffold_module "$SCAFFOLD")"
[[ -n "$MODULE" ]] || { echo "ERROR: unknown scaffold '$SCAFFOLD'" >&2; exit 1; }

if [[ -n "$OUT_DIR_OVERRIDE" ]]; then
  OUT_DIR_RAW="$OUT_DIR_OVERRIDE"
else
  OUT_DIR_RAW="$OUT_DIR_CFG"
fi
OUT_DIR_BASE="$(abspath "$OUT_DIR_RAW")"
OUT_DIR="$OUT_DIR_BASE/$JOB_NAME"
IM_OUTPUT="$OUT_DIR/im.jsonl"
LF_OUTPUT="$OUT_DIR/lf.json"
mkdir -p "$OUT_DIR"

if [[ -n "$MAX_INSTANCES_OVERRIDE" ]]; then
  MAX_INSTANCES="$MAX_INSTANCES_OVERRIDE"
else
  MAX_INSTANCES="$MAX_INSTANCES_CFG"
fi

if [[ "$EXCLUDE_REPOS_OVERRIDE_SET" == "1" ]]; then
  EXCLUDE_REPOS_FILE="$EXCLUDE_REPOS_OVERRIDE"
else
  EXCLUDE_REPOS_FILE="$EXCLUDE_REPOS_CFG"
fi

CMD=("$SWE_DP_UV_ABS/bin/python" "-m" "$MODULE"
     "--job-dir" "$JOB_DIR"
     "--im-output" "$IM_OUTPUT"
     "--lf-output" "$LF_OUTPUT")
if [[ -n "$MAX_INSTANCES" ]]; then
  CMD+=("--max-instances" "$MAX_INSTANCES")
fi
# Pass --exclude-repos-file explicitly only when user gave a value (incl. empty
# string to disable). Otherwise the converter falls back to its repo default.
if [[ "$EXCLUDE_REPOS_OVERRIDE_SET" == "1" || -n "$EXCLUDE_REPOS_FILE" ]]; then
  CMD+=("--exclude-repos-file" "$EXCLUDE_REPOS_FILE")
fi

SIG_FILE="$OUT_DIR/.convert_sig.json"

# Signature of the conversion inputs. Based on the resolved (reward=1.0)
# instance set (the only thing the converter consumes) plus scaffold/limits, so
# it is stable while result.json metadata churns during a running job.
compute_convert_sig() {
  python3 - "$JOB_DIR/result.json" "$SCAFFOLD" "${MAX_INSTANCES:-}" "${EXCLUDE_REPOS_FILE:-}" <<'PY'
import hashlib
import json
import sys

result_path, scaffold, max_instances, exclude_file = sys.argv[1:5]
parts = [scaffold, max_instances, exclude_file]
try:
    with open(result_path, encoding="utf-8") as fh:
        data = json.load(fh)
    resolved = []
    evals = (data.get("stats") or {}).get("evals") or {}
    for ev in evals.values():
        if not isinstance(ev, dict):
            continue
        reward = ((ev.get("reward_stats") or {}).get("reward") or {})
        for key in ("1.0", 1.0):
            vals = reward.get(key)
            if isinstance(vals, list):
                resolved.extend(str(v) for v in vals)
    parts.append(str(len(resolved)))
    parts.append("\n".join(sorted(resolved)))
except FileNotFoundError:
    parts.append("NO_RESULT_JSON")
print(hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest())
PY
}

NEW_SIG=""
if [[ "$SKIP_UNCHANGED" == "1" ]]; then
  NEW_SIG="$(compute_convert_sig)"
  OLD_SIG=""
  if [[ -f "$SIG_FILE" ]]; then
    OLD_SIG="$(python3 - "$SIG_FILE" <<'PY'
import json
import sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        print((json.load(fh) or {}).get("sig", ""))
except Exception:
    print("")
PY
)"
  fi
  if [[ -n "$NEW_SIG" && "$NEW_SIG" == "$OLD_SIG" && -f "$LF_OUTPUT" && -f "$OUT_DIR/lf.stats.json" ]]; then
    echo "=== trajgen: convert trajectories ==="
    echo "Job:       $JOB_NAME"
    echo "--skip-unchanged: resolved set and inputs unchanged; skipping reconversion."
    echo "LF file:   $LF_OUTPUT (unchanged)"
    exit 0
  fi
fi

echo "=== trajgen: convert trajectories ==="
echo "Job:       $JOB_NAME"
echo "Job dir:   $JOB_DIR"
echo "Scaffold:  $SCAFFOLD"
echo "Module:    $MODULE"
echo "IM out:    $IM_OUTPUT"
echo "LF out:    $LF_OUTPUT"
echo "Env:       $SWE_DP_UV_ABS"
echo ""

(
  cd "$SWE_DP_DIR"
  UV_PROJECT_ENVIRONMENT="$SWE_DP_UV_ABS" "${CMD[@]}"
)

# Record the input signature so a subsequent --skip-unchanged run can short-circuit.
if [[ "$SKIP_UNCHANGED" == "1" ]]; then
  [[ -n "$NEW_SIG" ]] || NEW_SIG="$(compute_convert_sig)"
  python3 - "$SIG_FILE" "$NEW_SIG" <<'PY'
import datetime
import json
import sys

sig_file, sig = sys.argv[1], sys.argv[2]
with open(sig_file, "w", encoding="utf-8") as fh:
    json.dump(
        {"sig": sig, "converted_at": datetime.datetime.now().astimezone().isoformat()},
        fh,
        ensure_ascii=False,
        indent=2,
    )
PY
fi

LF_COUNT=""
if [[ -f "$LF_OUTPUT" ]]; then
  LF_COUNT="$(python3 - "$LF_OUTPUT" <<'PY'
import json
import sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
    print(len(data) if isinstance(data, list) else "n/a")
except Exception as exc:
    print(f"error:{exc}")
PY
)"
fi
echo ""
echo "Conversion complete. LF records: ${LF_COUNT:-unknown}"
echo "LF file: $LF_OUTPUT"
