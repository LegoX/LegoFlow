#!/usr/bin/env bash
# Generate a Harbor gold dataset under artifacts/datasets/<gold_base>/ for use by
# the job_analysis pipeline (gold_source: harbor_dataset), in two steps:
#
#   1. ADAPTER  — repos/harbor/adapters/<adapter>/run_adapter.py converts the
#                 upstream benchmark (from HuggingFace) into per-task Harbor dirs
#                 (task.toml, instruction.md, tests/config.json = the gold).
#   2. TAGGER   — repos/harbor/scripts/task_analysis/tag_task_metadata.py rewrites
#                 each task.toml's tags into the 4-part schema the dashboard reads:
#                 [language, area, topic, bug_class] + difficulty. Needs an
#                 OpenAI-compatible LLM (job_analysis.tag_llm, then llm_api).
#
# Usage:
#   bash scripts/prepare_dataset.sh <dataset_name>     # e.g. swebench-verified
#   bash scripts/prepare_dataset.sh                    # dataset_name from config.yaml
#
# Env overrides:
#   PREP_LIMIT=N            cap number of tasks generated (default: all)
#   PREP_FORCE=1            re-generate even if the dataset dir is already populated
#   PREP_SKIP_TAGGING=1     generate gold only; skip the LLM tagging step
#   PREP_TAG_JOBS=N         tagger concurrency (default 8)
#   PREP_TAG_MODEL=...      override model (default: job_analysis.tag_llm, then llm_api)
#   PREP_TAG_API_KEY=...    override key (default: job_analysis.tag_llm, then llm_api)
#   PREP_TAG_BASE_URL=...   override URL (default: job_analysis.tag_llm, then llm_api)
#   PREP_TAG_RETRIES=N      retry each failed tag call N times (default 2)
#   PREP_TAG_RETRY_DELAY_SEC=N  base retry backoff in seconds (default 2)
#
# Harbor is read-only: we only `cd` into adapter / script dirs for their relative
# imports; all output lands under the writable artifacts/datasets/.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${EVAL_CONFIG:-$BLOCK_DIR/config.yaml}"
DATASETS_ROOT="$BLOCK_DIR/artifacts/datasets"

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
HARBOR_DIR="$(abspath "$HARBOR_PATH_RAW")"
HARBOR_UV_RAW="$(cfg meta_info.environment.harbor_uv)"
[[ -n "$HARBOR_UV_RAW" ]] || HARBOR_UV_RAW="artifacts/env/harbor-uv"
PY="$(abspath "$HARBOR_UV_RAW")/bin/python"
[[ -x "$PY" ]] || PY="python3"

DATASET_NAME="${1:-}"
if [[ -z "$DATASET_NAME" ]]; then
  DATASET_NAME="$("$PY" - "$CONFIG" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print(""); sys.exit(0)
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
print((((d.get("runtime_info") or {}).get("input") or {}).get("task_source") or {}).get("dataset_name") or "")
PY
)"
fi
[[ -n "$DATASET_NAME" ]] || { echo "ERROR: no dataset_name (pass as arg or set in config.yaml)" >&2; exit 2; }

# dataset_name -> (gold_base, adapter dir). Strip the -100 subset suffix and the
# -nohack hardened suffix: gold comes from the ordinary adapter either way.
BASE="$DATASET_NAME"
[[ "$BASE" == *-100 ]] && BASE="${BASE%-100}"
[[ "$BASE" == *-nohack ]] && BASE="${BASE%-nohack}"
case "$BASE" in
  swebench-verified)      GOLD_BASE="swebench-verified";      ADAPTER="swebench" ;;
  swebench_multilingual)  GOLD_BASE="swebench_multilingual";  ADAPTER="swebench_multilingual" ;;
  swebenchpro)            GOLD_BASE="swebenchpro";            ADAPTER="swebenchpro" ;;
  *)
    echo "ERROR: no adapter mapping for dataset '$DATASET_NAME'." >&2
    echo "       Supported: swebench-verified, swebench_multilingual, swebenchpro (and -100 / -nohack variants)." >&2
    echo "       Generate manually from repos/harbor/adapters/<name>/run_adapter.py." >&2
    exit 2 ;;
esac

DATASET_DIR="$DATASETS_ROOT/$GOLD_BASE"
ADAPTER_DIR="$HARBOR_DIR/adapters/$ADAPTER"
[[ -d "$ADAPTER_DIR" ]] || { echo "ERROR: adapter not found: $ADAPTER_DIR; run scripts/update_repos.sh" >&2; exit 1; }

echo "=== prepare dataset ==="
echo "dataset_name: $DATASET_NAME"
echo "gold_base:    $GOLD_BASE  (adapter: $ADAPTER)"
echo "output:       $DATASET_DIR"
echo "python:       $PY"
echo ""

# ---------------------------------------------------------------------------
# Step 1: adapter -> gold task dirs (idempotent unless PREP_FORCE=1).
# ---------------------------------------------------------------------------
count_gold() { find "$1" -maxdepth 3 -name config.json -path '*/tests/config.json' 2>/dev/null | wc -l | tr -d ' '; }
EXISTING="$(count_gold "$DATASET_DIR" || true)"
EXISTING="${EXISTING:-0}"
if [[ "$EXISTING" -gt 0 && "${PREP_FORCE:-0}" != "1" ]]; then
  echo "Step 1/2 adapter: SKIP — $DATASET_DIR already has $EXISTING task(s). Set PREP_FORCE=1 to rebuild."
else
  echo "Step 1/2 adapter: generating into $DATASET_DIR ..."
  mkdir -p "$DATASET_DIR"
  ADAPTER_ARGS=(--task-dir "$DATASET_DIR" --all --overwrite)
  [[ -n "${PREP_LIMIT:-}" ]] && ADAPTER_ARGS+=(--limit "$PREP_LIMIT")
  ( cd "$ADAPTER_DIR" && "$PY" run_adapter.py "${ADAPTER_ARGS[@]}" )
  GENERATED="$(count_gold "$DATASET_DIR" || true)"
  echo "Step 1/2 adapter: done — ${GENERATED:-0} gold task(s)."
fi
echo ""

# ---------------------------------------------------------------------------
# Step 2: tagger -> complete task.toml [language, area, topic, bug_class].
# ---------------------------------------------------------------------------
if [[ "${PREP_SKIP_TAGGING:-0}" == "1" ]]; then
  echo "Step 2/2 tagger: SKIP (PREP_SKIP_TAGGING=1). Gold is usable; Language/Area"
  echo "                 breakdown will be limited until task.toml tags are completed."
  echo ""
  echo "prepare dataset complete (gold only): $DATASET_DIR"
  exit 0
fi

# LLM endpoint for tagging. Priority: PREP_TAG_* env > config job_analysis.tag_llm
# > llm_api. The dedicated tag_llm exists because the eval model may be a reasoning
# model that can't emit clean JSON; tagging needs one that can.
# Split on US (\x1f), a non-whitespace delimiter: this avoids word-splitting on
# spaces inside values AND preserves empty fields (an IFS-whitespace delimiter
# like tab would collapse consecutive separators and drop an empty api_key,
# shifting url into the wrong variable).
IFS=$'\x1f' read -r LA_MODEL LA_KEY LA_URL < <("$PY" - "$CONFIG" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("\x1f\x1f"); sys.exit(0)
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
rin = ((d.get("runtime_info") or {}).get("input") or {})
tag = ((rin.get("job_analysis") or {}).get("tag_llm") or {})
la = (rin.get("llm_api") or {})
# tag_llm.model is a bare served name; llm_api.model may carry an openai/ prefix.
model = tag.get("model") or (la.get("model") or "").split("/")[-1]
key = tag.get("api_key") or la.get("api_key")
url = tag.get("base_url") or la.get("api_base_url")
print("\x1f".join([model or "", key or "", url or ""]))
PY
)

TAG_MODEL="${PREP_TAG_MODEL:-$LA_MODEL}"
TAG_KEY="${PREP_TAG_API_KEY:-$LA_KEY}"
TAG_URL="${PREP_TAG_BASE_URL:-$LA_URL}"
TAG_JOBS="${PREP_TAG_JOBS:-8}"
TAG_RETRIES="${PREP_TAG_RETRIES:-2}"
TAG_RETRY_DELAY_SEC="${PREP_TAG_RETRY_DELAY_SEC:-2}"

if [[ -z "$TAG_MODEL" || -z "$TAG_URL" ]]; then
  echo "Step 2/2 tagger: SKIP — no LLM endpoint (set llm_api in config.yaml or PREP_TAG_* env)."
  echo "                 Gold is usable; complete tags later with scripts/task_analysis."
  echo ""
  echo "prepare dataset complete (gold only): $DATASET_DIR"
  exit 0
fi

echo "Step 2/2 tagger: completing task.toml tags via $TAG_URL (model=$TAG_MODEL, jobs=$TAG_JOBS, retries=$TAG_RETRIES) ..."
# Best-effort: the gold (step 1) is the critical output that unblocks analysis.
# Tag completion only enriches the dashboard's Language/Area breakdown and depends
# on the model returning clean JSON, so a tagging failure must not lose the gold.
TAG_RC=0
TAG_ARGS=(--datasets-root "$DATASETS_ROOT" --dataset "$GOLD_BASE"
          --model "$TAG_MODEL" --base-url "$TAG_URL" --jobs "$TAG_JOBS"
          --retries "$TAG_RETRIES" --retry-delay-sec "$TAG_RETRY_DELAY_SEC")
# Only pass --api-key if we actually have one; passing an empty/sentinel key
# would send a bogus credential rather than letting the tagger run keyless.
[[ -n "$TAG_KEY" ]] && TAG_ARGS+=(--api-key "$TAG_KEY")
( cd "$HARBOR_DIR" && "$PY" scripts/task_analysis/tag_task_metadata.py "${TAG_ARGS[@]}" ) || TAG_RC=$?
echo ""
if [[ "$TAG_RC" -ne 0 ]]; then
  echo "WARNING: tag completion reported errors (rc=$TAG_RC). Gold dataset is ready and"
  echo "         analysis will run, but Language/Area breakdown may be limited. This"
  echo "         usually means the model ($TAG_MODEL) did not return clean JSON (e.g. a"
  echo "         reasoning model emitting <think>...). Re-run with a JSON-clean endpoint:"
  echo "           PREP_TAG_BASE_URL=<url> PREP_TAG_MODEL=<model> PREP_TAG_API_KEY=<key> \\"
  echo "             bash scripts/prepare_dataset.sh \"$DATASET_NAME\""
fi
echo "prepare dataset complete: $DATASET_DIR"
