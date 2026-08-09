#!/bin/bash
# Full databoard pipeline: export -> prepare metadata -> validate and render.
set -euo pipefail
cd "$(dirname "$0")"
DASH="$(pwd)"
LOG="$DASH/pipeline.log"
: > "$LOG"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

# Credentials stay outside the repository. Callers may export them directly or
# point LEGOFLOW_CURATOR_ENV_FILE at a private shell-compatible env file.
ENV_FILE="${LEGOFLOW_CURATOR_ENV_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/legoflow/curator.env}"
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
export TAGGING_API_BASE_URL="${TAGGING_API_BASE_URL:-}"
export TAGGING_API_KEY="${TAGGING_API_KEY:-}"
export TAGGING_MODEL="${TAGGING_MODEL:-}"
TAGGER="$DASH/../repos/legoflow-curator/tools/tag_task_metadata.py"
JOBS="${TAGGING_JOBS:-64}"

mapfile -t REGISTRY_ROWS < <(python3 dataset_registry.py)
if [ "${#REGISTRY_ROWS[@]}" -eq 0 ]; then
  echo "ERROR: dataset registry is empty" >&2
  exit 2
fi
declare -a DATASETS=()
declare -A EXPORT_SCRIPT=()
declare -A METADATA_STRATEGY=()
for row in "${REGISTRY_ROWS[@]}"; do
  IFS=$'\t' read -r dataset_id exporter strategy <<<"$row"
  DATASETS+=("$dataset_id")
  EXPORT_SCRIPT["$dataset_id"]="$exporter"
  METADATA_STRATEGY["$dataset_id"]="$strategy"
done

# 1. Export every dataset to datasets/<id>/tasks.jsonl
log "=== EXPORT PHASE ==="
for ds in "${DATASETS[@]}"; do
  if [ "${METADATA_STRATEGY[$ds]}" = "task_toml" ] \
    && [ -s "datasets/$ds/tasks.jsonl" ] \
    && [ -s "datasets/$ds/tags.jsonl" ]; then
    log "export $ds: tasks.jsonl and task.toml-derived tags.jsonl already present, skipping"
    continue
  fi
  if [ "${METADATA_STRATEGY[$ds]}" != "task_toml" ] \
    && [ -s "datasets/$ds/tasks.jsonl" ]; then
    log "export $ds: already present ($(wc -l < datasets/$ds/tasks.jsonl) tasks), skipping"
    continue
  fi
  log "export $ds via ${EXPORT_SCRIPT[$ds]} ..."
  if ! python3 "${EXPORT_SCRIPT[$ds]}" >>"$LOG" 2>&1; then
    log "export $ds FAILED"
    exit 1
  fi
  log "export $ds done: $(wc -l < "datasets/$ds/tasks.jsonl") tasks"
done

# 2. Self-made tags already came from task.toml. Only external datasets use the
# canonical tagger, during preparation rather than rendering.
log "=== METADATA PREPARATION PHASE ==="
for ds in "${DATASETS[@]}"; do
  tasks="datasets/$ds/tasks.jsonl"
  tags="datasets/$ds/tags.jsonl"
  if [ ! -s "$tasks" ]; then
    log "metadata $ds: missing tasks.jsonl"
    exit 1
  fi
  total=$(wc -l < "$tasks")

  if [ "${METADATA_STRATEGY[$ds]}" = "task_toml" ]; then
    if [ ! -s "$tags" ]; then
      log "metadata $ds: missing task.toml-derived tags.jsonl"
      exit 1
    fi
    done_ct=$(wc -l < "$tags")
    if [ "$done_ct" -ne "$total" ]; then
      log "metadata $ds: task/tag count mismatch ($total tasks, $done_ct tags)"
      exit 1
    fi
    log "metadata $ds: using $done_ct precomputed task.toml records (no LLM)"
    continue
  fi

  if [[ -z "$TAGGING_API_BASE_URL" || -z "$TAGGING_MODEL" ]]; then
    echo "ERROR: external tagging endpoint not configured. Set TAGGING_API_BASE_URL" >&2
    echo "       and TAGGING_MODEL directly or through $ENV_FILE" >&2
    exit 2
  fi
  for pass in $(seq 1 30); do
    done_ct=$( [ -f "$tags" ] && wc -l < "$tags" || echo 0 )
    if [ "$done_ct" -ge "$total" ]; then
      log "tag $ds: complete ($done_ct/$total)"; break
    fi
    log "tag $ds pass $pass: $done_ct/$total tagged, running tagger ..."
    python3 "$TAGGER" --dataset "$ds" --datasets-dir datasets \
      --jobs "$JOBS" --retries 5 >>"$LOG" 2>&1 || log "tag $ds pass $pass returned nonzero (will re-check)"
  done
  final=$( [ -f "$tags" ] && wc -l < "$tags" || echo 0 )
  if [ "$final" -ne "$total" ]; then
    log "tag $ds: incomplete after retries ($final/$total)"
    exit 1
  fi
  python3 metadata_records.py --dataset "$ds" --tags-file "$tags" >>"$LOG" 2>&1
  log "tag $ds: complete and provenance annotated ($final/$total)"
done

# 3. The renderer only reads and validates prepared tags.jsonl files.
log "=== VALIDATE AND RENDER PHASE ==="
if ! python3 progress_monitor_multi.py --output-html site/index.html >>"$LOG" 2>&1; then
  log "render FAILED"
  exit 1
fi
log "render done"

log "=== PIPELINE COMPLETE ==="
for ds in "${DATASETS[@]}"; do
  t=$( [ -f "datasets/$ds/tasks.jsonl" ] && wc -l < "datasets/$ds/tasks.jsonl" || echo 0 )
  g=$( [ -f "datasets/$ds/tags.jsonl" ] && wc -l < "datasets/$ds/tags.jsonl" || echo 0 )
  log "  $ds: tasks=$t tagged=$g"
done
