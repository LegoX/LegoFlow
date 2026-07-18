#!/usr/bin/env bash
# Run the Harbor job_analysis pipeline on a completed Harbor job and write the
# results into <job_dir>/analysis/ — the exact layout the dashboard (server.py)
# reads (report_failed/resolved.json, traj_analysis/, instance_analysis/, ...).
#
# Usage:
#   bash scripts/analyze_job.sh <job_dir>     # analyze a specific job
#   bash scripts/analyze_job.sh               # analyze the newest job under jobs_dir
#
# Design notes:
#   * Safe to re-run and safe to run on old jobs (idempotent: overwrites analysis/).
#   * Non-fatal by intent when called from start.sh (caller appends `|| true`),
#     so a post-hoc analysis failure never fails the evaluation run.
#   * Harbor is a read-only managed dependency: we only `cd` into
#     repos/harbor/scripts/job_analysis for its `from src...` imports. The
#     generated config and ALL output live under the (writable) job dir.
#   * judge is disabled => zero LLM cost, pure CPU. Enable with JOB_ANALYSIS_JUDGE=1
#     (requires ANTHROPIC_API_KEY).
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${EVAL_CONFIG:-$BLOCK_DIR/config.yaml}"

cfg() {
  python3 - "$CONFIG" "$1" <<'PY'
import sys
import yaml

data = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
value = data
for part in sys.argv[2].split("."):
    value = value.get(part) if isinstance(value, dict) else None
print("" if value is None else value)
PY
}

abspath() {
  if [[ "$1" = /* ]]; then
    printf '%s\n' "$1"
  else
    printf '%s\n' "$BLOCK_DIR/$1"
  fi
}

HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"
[[ -n "$HARBOR_PATH_RAW" ]] || HARBOR_PATH_RAW="repos/harbor"
JA_DIR="$(abspath "$HARBOR_PATH_RAW")/scripts/job_analysis"

# Prefer the Harbor uv env (has scipy + pyyaml); fall back to system python3.
HARBOR_UV_RAW="$(cfg meta_info.environment.harbor_uv)"
[[ -n "$HARBOR_UV_RAW" ]] || HARBOR_UV_RAW="artifacts/env/harbor-uv"
PY="$(abspath "$HARBOR_UV_RAW")/bin/python"
[[ -x "$PY" ]] || PY="python3"

[[ -d "$JA_DIR" ]] || { echo "ERROR: job_analysis not found at $JA_DIR; run scripts/update_repos.sh" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Resolve the target job directory (arg, or newest under runtime jobs_dir).
# ---------------------------------------------------------------------------
JOB_DIR="${1:-}"
if [[ -z "$JOB_DIR" ]]; then
  JOBS_DIR_RAW="$("$PY" - "$CONFIG" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print(""); sys.exit(0)
data = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
v = (((data.get("runtime_info") or {}).get("input") or {}).get("harbor_job") or {}).get("jobs_dir") or ""
print(v)
PY
)"
  [[ -n "$JOBS_DIR_RAW" ]] || JOBS_DIR_RAW="artifacts/jobs"
  [[ "$JOBS_DIR_RAW" = /* ]] || JOBS_DIR_RAW="$BLOCK_DIR/$JOBS_DIR_RAW"
  JOB_DIR="$(ls -dt "$JOBS_DIR_RAW"/*/ 2>/dev/null | head -1 || true)"
  [[ -n "$JOB_DIR" ]] || { echo "ERROR: no job dirs under $JOBS_DIR_RAW; pass a job dir explicitly" >&2; exit 1; }
fi
# Canonicalize, but check the dir exists first so a bad arg gives a clear error
# instead of an opaque set -e abort on the failed command substitution.
[[ -d "$JOB_DIR" ]] || { echo "ERROR: job dir not found: $JOB_DIR" >&2; exit 1; }
JOB_DIR="$(cd "$JOB_DIR" && pwd)"

# Prefer the per-job config snapshot (written by start.sh) so re-analyzing an old
# job uses that job's own settings; fall back to the live block config.
JOB_CONFIG="$JOB_DIR/config.yaml"
[[ -f "$JOB_CONFIG" ]] || JOB_CONFIG="$CONFIG"

ANALYSIS_DIR="$JOB_DIR/analysis"
GEN_CONFIG="$ANALYSIS_DIR/analysis_config.yaml"
# Generate the config to a temp path first; only commit it into analysis/ once we
# know the pipeline will actually run (so a skip leaves no stray analysis/ dir).
# Use mktemp + an EXIT trap so the temp file never leaks on any early exit.
TMP_CONFIG="$(mktemp "$JOB_DIR/.analysis_config.XXXXXX")"
trap 'rm -f "$TMP_CONFIG"' EXIT

# ---------------------------------------------------------------------------
# Generate the self-contained job-analysis config (absolute paths) and report
# whether the gold dataset is present.
# ---------------------------------------------------------------------------
JUDGE_ENABLED="false"
[[ "${JOB_ANALYSIS_JUDGE:-0}" == "1" ]] && JUDGE_ENABLED="true"

if ! GOLD_STATUS="$("$PY" - "$JOB_CONFIG" "$JOB_DIR" "$BLOCK_DIR" "$TMP_CONFIG" "$JUDGE_ENABLED" <<'PY'
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required (install into harbor-uv)", file=sys.stderr)
    sys.exit(2)

job_config, job_dir, block_dir, gen_config, judge_enabled = sys.argv[1:6]
data = yaml.safe_load(open(job_config, encoding="utf-8")) or {}
rin = ((data.get("runtime_info") or {}).get("input") or {})

dataset_name = (rin.get("task_source") or {}).get("dataset_name") or ""
agent_name = (rin.get("agent") or {}).get("name") or ""
max_iterations = (rin.get("agent") or {}).get("max_turns") or 200
model = (rin.get("llm_api") or {}).get("model") \
    or (rin.get("agent") or {}).get("model_name") \
    or "unknown"

# dataset_name -> gold dataset basename under artifacts/datasets/ (strip -100 subset suffix)
base = dataset_name[:-4] if dataset_name.endswith("-100") else dataset_name
gold_map = {
    "swebench-verified": "swebench-verified",
    "swebench_multilingual": "swebench_multilingual",
    "swebenchpro": "swebenchpro",
}
gold_base = gold_map.get(base, base)
dataset_dir = str(Path(block_dir) / "artifacts" / "datasets" / gold_base)

# agent.name -> job_analysis scaffold label
scaffold = {
    "custom-openhands-sdk": "openhands-sdk",
    "custom-claude-code": "claude-code",
    "custom-opencode": "opencode",
}.get(agent_name, "openhands-sdk")

cfg = {
    "data": {
        "log_dir": job_dir,
        "dataset_dir": dataset_dir,
        "trajectory_layout": "harbor_job",
        "gold_source": "harbor_dataset",
        "trajectory_subpath": "agent/litellm-trajectory.jsonl",
        "trial_result_file": "result.json",
        "trial_report_subpath": "verifier/report.json",
        "max_iterations_default": max_iterations,
    },
    "output": {
        "dir": str(Path(job_dir) / "analysis"),
        "instances_jsonl": "instances.jsonl",
        "report_json": "report.json",
    },
    "analysis": {
        "include_resolved": True,
        "include_errors": True,
        "include_empty_patch": True,
    },
    "features": {
        "loop_threshold": 3,
        "premature_stop_threshold": 0.3,
        "tool_error_storm_threshold": 5,
    },
    "judge": {
        "enabled": judge_enabled == "true",
        "model": "claude-sonnet-4-6",
        "max_trajectory_chars": 8000,
    },
    "hack_detector": {"enabled": True},
    "task_analysis": {"enabled": True},
    "instance_analysis": {"enabled": True, "out_subdir": "instance_analysis"},
    "traj_analysis": {"enabled": True, "out_subdir": "traj_analysis", "max_instances": None},
    "skip_main_pipeline": False,
    "scaffold": scaffold,
    "model": model,
    "taxonomy_version": "v1",
    "judge_version": "v1",
}

header = (
    "# Auto-generated by scripts/analyze_job.sh — DO NOT hand-edit.\n"
    f"# job:     {Path(job_dir).name}\n"
    f"# dataset: {dataset_name}  agent: {agent_name}  model: {model}\n"
)
with open(gen_config, "w", encoding="utf-8") as fh:
    fh.write(header)
    yaml.safe_dump(cfg, fh, sort_keys=False)

dataset_path = Path(dataset_dir)
has_gold = dataset_path.is_dir() and any(dataset_path.rglob("tests/config.json"))
status = "GOLD_OK" if has_gold else "GOLD_MISSING"
print(f"{status}|{dataset_name}|{dataset_dir}")
PY
)"; then
  echo "WARNING: failed to generate analysis config (PyYAML missing in $PY?); skipping job analysis." >&2
  exit 0
fi
# Expect "STATE|dataset_name|dataset_dir"; bail cleanly on anything else.
if [[ "$GOLD_STATUS" != *"|"*"|"* ]]; then
  echo "WARNING: unexpected analysis-config output; skipping job analysis." >&2
  exit 0
fi
GOLD_STATE="${GOLD_STATUS%%|*}"
GOLD_REST="${GOLD_STATUS#*|}"
DATASET_NAME="${GOLD_REST%%|*}"
DATASET_DIR="${GOLD_REST#*|}"

echo "=== job analysis ==="
echo "Job dir:   $JOB_DIR"
echo "Python:    $PY"
has_gold_dataset() {
  [[ -d "$1" && -n "$(find "$1" -path '*/tests/config.json' -print -quit 2>/dev/null)" ]]
}
# The pipeline requires a populated gold dataset (gold_source: harbor_dataset)
# and aborts hard if no tests/config.json files exist. When it is missing or
# empty, auto-generate it (adapter + tagger), then re-check.
if [[ "$GOLD_STATE" == "GOLD_MISSING" ]]; then
  if [[ "${JOB_ANALYSIS_PREPARE_DATASET:-1}" == "1" ]]; then
    echo ""
    echo "gold dataset missing at $DATASET_DIR — generating it (adapter + tagger)..."
    bash "$BLOCK_DIR/scripts/prepare_dataset.sh" "$DATASET_NAME" || \
      echo "WARNING: dataset preparation failed; see log above."
  fi
  if ! has_gold_dataset "$DATASET_DIR"; then
    rm -f "$TMP_CONFIG"
    echo ""
    echo "SKIP: gold dataset still not present at $DATASET_DIR"
    echo "      Generate it with: bash scripts/prepare_dataset.sh \"$DATASET_NAME\""
    echo "      (or set JOB_ANALYSIS_PREPARE_DATASET=1), then re-run analyze_job.sh."
    exit 0
  fi
fi

# Gold is present — commit the config into analysis/ and proceed.
mkdir -p "$ANALYSIS_DIR"
mv "$TMP_CONFIG" "$GEN_CONFIG"
echo "Config:    $GEN_CONFIG"
echo ""

# ---------------------------------------------------------------------------
# Run the pipeline. cwd must be the job_analysis dir for its `from src...`
# imports; the read-only repo is not written to (output goes to the job dir).
# ---------------------------------------------------------------------------
( cd "$JA_DIR" && "$PY" run.py --config "$GEN_CONFIG" )

echo ""
echo "job analysis complete. Output: $ANALYSIS_DIR"
