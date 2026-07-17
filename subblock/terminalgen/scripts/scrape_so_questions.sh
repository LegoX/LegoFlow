#!/bin/bash
# Scrape StackOverflow via terminal-lego's scraper, then bucket by domain tag_filter.
#
# Usage: bash scripts/scrape_so_questions.sh <round> [count] [num_subrounds]
#   round          label for this scrape (output under _raw_r<round>/)
#   count          target questions PER subround (default 200)
#   num_subrounds  scrape this many deduped subrounds and union them (default 1)
#
# A single terminal-lego scrape round tends to be dominated by a few high-weight
# tags, so it is NOT diverse. Pass num_subrounds > 1 to scrape several rounds
# (the scraper reshuffles its tag order each round) deduped against each other,
# then bucket the union — this spreads coverage across the 13 domains.
set -euo pipefail

cd "$(dirname "$0")/.." || { echo "ERROR: cannot cd to block root"; exit 1; }

# shellcheck disable=SC1091
source scripts/load_runtime_env.sh
load_runtime_env

ROUND="${1:?usage: scrape_so_questions.sh <round> [count] [num_subrounds]}"
COUNT="${2:-200}"
SUBROUNDS="${3:-1}"

# terminal-lego's scraper requires an INTEGER --round; derive a numeric base from
# the round label so each subround gets a distinct integer (used only for the
# output filename so_data_r<int>.json inside RAW_DIR).
ROUND_BASE="$(printf '%s' "$ROUND" | tr -cd '0-9')"
[[ -n "$ROUND_BASE" ]] || ROUND_BASE=1

TL="repos/terminal-lego"
if [[ ! -f "${TL}/scraper/so_scraper.py" ]]; then
    echo "ERROR: ${TL}/scraper/so_scraper.py missing — run: git submodule update --init ${TL}" >&2
    exit 1
fi

PY="artifacts/envs/terminalgen-env/bin/python"
[[ -x "$PY" ]] || PY="python3"

RAW_DIR="artifacts/collected_questions/_raw_r${ROUND}"
mkdir -p "$RAW_DIR" artifacts/logs/terminalgen-create

echo "[scrape] round=${ROUND} count=${COUNT}/subround subrounds=${SUBROUNDS} (SO_API_KEY $([ -n "${SO_API_KEY:-}" ] && echo set || echo UNSET → 300/day))"
for sr in $(seq 1 "$SUBROUNDS"); do
    echo "[scrape] subround ${sr}/${SUBROUNDS}"
    # distinct integer round per subround for the output filename; --dedup-dir
    # RAW_DIR excludes question IDs already collected in earlier subrounds.
    "$PY" "${TL}/scraper/so_scraper.py" \
        --round "$(( ROUND_BASE * 100 + sr ))" \
        --output "$RAW_DIR" \
        --dedup-dir "$RAW_DIR" \
        --count "$COUNT" \
        --api-key "${SO_API_KEY:-}" \
        2>&1 | tee -a "artifacts/logs/terminalgen-create/scrape_r${ROUND}.txt"
done

# Union all subround JSONs into one combined file for bucketing.
COMBINED="${RAW_DIR}/so_data_combined.json"
"$PY" - "$RAW_DIR" "$COMBINED" <<'PY'
import json, sys, glob, os
raw_dir, out = sys.argv[1], sys.argv[2]
seen, merged = set(), []
for f in sorted(glob.glob(os.path.join(raw_dir, "so_data_r*.json"))):
    if os.path.basename(f) == os.path.basename(out):
        continue
    try:
        qs = json.load(open(f)).get("questions", [])
    except Exception:
        continue
    for q in qs:
        qid = q.get("question_id")
        if qid in seen:
            continue
        seen.add(qid)
        merged.append(q)
json.dump({"metadata": {"total": len(merged)}, "questions": merged}, open(out, "w"), ensure_ascii=False, indent=2)
print(f"[scrape] combined {len(merged)} unique questions across subrounds")
PY

if [[ ! -s "$COMBINED" ]]; then
    echo "ERROR: scraper produced no questions (likely rate-limited; see log)" >&2
    exit 2
fi

echo "[bucket] splitting ${COMBINED} by domain tag_filter"
"$PY" scripts/bucket_questions.py \
    --raw "$COMBINED" \
    --config config.yaml \
    --out artifacts/collected_questions
