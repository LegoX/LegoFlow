#!/bin/bash
# Full databoard data pipeline: export all datasets -> tag (retry until complete)
# -> regenerate site/index.html. Designed to run in the background.
set -uo pipefail
cd "$(dirname "$0")"
DASH="$(pwd)"
LOG="$DASH/pipeline.log"
: > "$LOG"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

# Credentials
export HF_TOKEN="$(grep -oP '(?<=HF_TOKEN=)[^ ]+' ~/.bashrc | tr -d '"'"'"'' | head -1)"
export TAGGING_API_BASE_URL="http://llm.jierungogogo.com/v1"
export TAGGING_API_KEY="dummy-cf"
export TAGGING_MODEL="Qwen3.6-35B-A3B"
TAGGER="$DASH/../repos/swegen/tools/tag_task_metadata.py"
JOBS=64

DATASETS=(self_made swe_rebench swe_rebench_v2 openswe_filtered scale_swe)

# 1. Export every dataset to datasets/<id>/tasks.jsonl
log "=== EXPORT PHASE ==="
declare -A EXPORT_SCRIPT=(
  [self_made]=export_self_made.py
  [swe_rebench]=export_swe_rebench.py
  [swe_rebench_v2]=export_swe_rebench_v2.py
  [openswe_filtered]=export_openswe_filtered.py
  [scale_swe]=export_scale_swe.py
)
for ds in "${DATASETS[@]}"; do
  if [ -s "datasets/$ds/tasks.jsonl" ]; then
    log "export $ds: already present ($(wc -l < datasets/$ds/tasks.jsonl) tasks), skipping"
    continue
  fi
  log "export $ds via ${EXPORT_SCRIPT[$ds]} ..."
  python3 "${EXPORT_SCRIPT[$ds]}" >>"$LOG" 2>&1 \
    && log "export $ds done: $(wc -l < datasets/$ds/tasks.jsonl 2>/dev/null || echo 0) tasks" \
    || log "export $ds FAILED"
done

# 2. Tag each dataset; retry until tags.jsonl count == tasks.jsonl count.
log "=== TAG PHASE (jobs=$JOBS, retry until complete) ==="
for ds in "${DATASETS[@]}"; do
  tasks="datasets/$ds/tasks.jsonl"
  tags="datasets/$ds/tags.jsonl"
  [ -s "$tasks" ] || { log "tag $ds: no tasks.jsonl, skipping"; continue; }
  total=$(wc -l < "$tasks")
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
  log "tag $ds: final $final/$total"
done

# 3. Regenerate the dashboard HTML
log "=== REGENERATE PHASE ==="
python3 progress_monitor_multi.py --output-html site/index.html >>"$LOG" 2>&1 \
  && log "regenerate done" || log "regenerate FAILED"

log "=== PIPELINE COMPLETE ==="
for ds in "${DATASETS[@]}"; do
  t=$( [ -f "datasets/$ds/tasks.jsonl" ] && wc -l < "datasets/$ds/tasks.jsonl" || echo 0 )
  g=$( [ -f "datasets/$ds/tags.jsonl" ] && wc -l < "datasets/$ds/tags.jsonl" || echo 0 )
  log "  $ds: tasks=$t tagged=$g"
done
