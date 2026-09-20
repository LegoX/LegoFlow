#!/usr/bin/env bash
# Archive this block's run: snapshot config + scripts + repo commit SHAs into
# artifacts/archives/run_NNN/ and append an entry to artifacts/index.yaml.
#
# Invoked by scripts/start.sh's EXIT trap; safe to invoke manually too.
#
#   Usage: scripts/archive_run.sh [exit_code] [started_at_iso8601] [notes]
#
# Defaults: exit_code=0, started_at=now, notes="".
# Always exits 0 — must not mask the original script's exit code.
set -u

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS_DIR="$BLOCK_DIR/artifacts"
ARCHIVES_DIR="$ARTIFACTS_DIR/archives"
INDEX_FILE="$ARTIFACTS_DIR/index.yaml"

EXIT_CODE="${1:-0}"
STARTED_AT="${2:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
NOTES="${3:-}"
COMPLETED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Map exit code → status label.
case "$EXIT_CODE" in
    0)        STATUS="completed" ;;
    130|143)  STATUS="interrupted" ;;   # SIGINT / SIGTERM
    *)        STATUS="failed" ;;
esac

mkdir -p "$ARCHIVES_DIR"

# Next run id = max(existing archives/run_NNN/, existing index.yaml run ids) + 1.
next_id=1
shopt -s nullglob
for d in "$ARCHIVES_DIR"/run_*/; do
    n="$(basename "$d")"
    n="${n#run_}"
    [[ "$n" =~ ^[0-9]+$ ]] || continue
    n=$((10#$n))    # force base-10 (strip leading zeros)
    if (( n + 1 > next_id )); then next_id=$((n + 1)); fi
done
shopt -u nullglob
if [[ -f "$INDEX_FILE" ]]; then
    # Grep for `id: run_NNN` and `id: 'run_NNN'` / `id: "run_NNN"` forms.
    while read -r n; do
        [[ "$n" =~ ^[0-9]+$ ]] || continue
        n=$((10#$n))
        if (( n + 1 > next_id )); then next_id=$((n + 1)); fi
    done < <(grep -oE 'id:[[:space:]]*["'\'']?run_[0-9]+' "$INDEX_FILE" 2>/dev/null \
             | sed -E 's/.*run_0*([0-9]+).*/\1/')
fi
RUN_ID="$(printf 'run_%03d' "$next_id")"
RUN_DIR="$ARCHIVES_DIR/$RUN_ID"
mkdir -p "$RUN_DIR"

# Snapshot config.yaml and scripts/ (top-level files + non-hidden subdirs only —
# skip any hidden state dir a tool may leave inside scripts/).
[[ -f "$BLOCK_DIR/config.yaml" ]] && python3 "$BLOCK_DIR/../../scripts/redact_archive_config.py" "$BLOCK_DIR/config.yaml" "$RUN_DIR/config.yaml"
if [[ -d "$BLOCK_DIR/scripts" ]]; then
    mkdir -p "$RUN_DIR/scripts"
    shopt -s nullglob
    for entry in "$BLOCK_DIR/scripts"/*; do
        cp -Rp "$entry" "$RUN_DIR/scripts/"
    done
    shopt -u nullglob
fi

# Record repo commit SHAs (cheap, vs full tree copy).
REPOS_LINES=""
if [[ -d "$BLOCK_DIR/repos" ]]; then
    shopt -s nullglob
    for repo in "$BLOCK_DIR/repos"/*/; do
        name="$(basename "$repo")"
        if sha="$(git -C "$repo" rev-parse HEAD 2>/dev/null)"; then
            REPOS_LINES+="  $name: $sha"$'\n'
        fi
    done
    shopt -u nullglob
fi

# Write metadata.yaml.
{
    echo "id: $RUN_ID"
    echo "block: $(basename "$BLOCK_DIR")"
    echo "started_at: \"$STARTED_AT\""
    echo "completed_at: \"$COMPLETED_AT\""
    echo "status: $STATUS"
    echo "exit_code: $EXIT_CODE"
    if [[ -n "$REPOS_LINES" ]]; then
        echo "repos:"
        printf '%s' "$REPOS_LINES"
    else
        echo "repos: {}"
    fi
    if [[ -n "$NOTES" ]]; then
        notes_escaped="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$NOTES" 2>/dev/null)"
        [[ -z "$notes_escaped" ]] && notes_escaped="\"\""
        echo "notes: $notes_escaped"
    else
        echo "notes: \"\""
    fi
} >"$RUN_DIR/metadata.yaml"

# Append entry to index.yaml. Prefer PyYAML for a clean round-trip; fall back
# to a plain text append if PyYAML isn't available.
ARCHIVE_INDEX_FILE="$INDEX_FILE" \
ARCHIVE_RUN_ID="$RUN_ID" \
ARCHIVE_STARTED_AT="$STARTED_AT" \
ARCHIVE_COMPLETED_AT="$COMPLETED_AT" \
ARCHIVE_STATUS="$STATUS" \
ARCHIVE_NOTES="$NOTES" \
python3 - <<'PY' 2>/dev/null
import os, sys
from pathlib import Path
try:
    import yaml
except ImportError:
    sys.exit(2)

path = Path(os.environ["ARCHIVE_INDEX_FILE"])
text = path.read_text() if path.exists() else ""
data = yaml.safe_load(text) if text.strip() else {}
if not isinstance(data, dict):
    data = {}
runs = data.get("runs")
if not isinstance(runs, list):
    runs = []
runs.append({
    "id":           os.environ["ARCHIVE_RUN_ID"],
    "started_at":   os.environ["ARCHIVE_STARTED_AT"],
    "completed_at": os.environ["ARCHIVE_COMPLETED_AT"],
    "status":       os.environ["ARCHIVE_STATUS"],
    "archive":      f"artifacts/archives/{os.environ['ARCHIVE_RUN_ID']}/",
    "notes":        os.environ["ARCHIVE_NOTES"],
})
data["runs"] = runs
path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
PY

if [[ "$?" -ne 0 ]]; then
    # PyYAML unavailable — append a plain block. May not be valid YAML if the
    # existing file has an unusual structure, but at least the run is recorded.
    {
        echo ""
        echo "  - id: $RUN_ID"
        echo "    started_at: \"$STARTED_AT\""
        echo "    completed_at: \"$COMPLETED_AT\""
        echo "    status: $STATUS"
        echo "    archive: artifacts/archives/$RUN_ID/"
        echo "    notes: \"$NOTES\""
    } >>"$INDEX_FILE"
fi

echo "Archived run: $RUN_DIR  (status=$STATUS, exit=$EXIT_CODE)" >&2
exit 0
