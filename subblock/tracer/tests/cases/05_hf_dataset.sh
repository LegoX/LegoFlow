#!/usr/bin/env bash
# CI test 05: when task_source.provider=huggingface, the dataset is reachable
# (and any required HF token works). SKIP for local providers.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

PROVIDER="$(python3 -c "import yaml; d=yaml.safe_load(open('$CONFIG')) or {}; print((d.get('runtime_info',{}).get('input',{}).get('task_source',{}).get('provider') or '').strip())")"
DATASET="$(python3 -c "import yaml; d=yaml.safe_load(open('$CONFIG')) or {}; print((d.get('runtime_info',{}).get('input',{}).get('task_source',{}).get('dataset_name') or '').strip())")"

if [[ "$PROVIDER" != "huggingface" ]]; then
  echo "SKIP: task_source.provider=$PROVIDER (not huggingface)"
  exit 77
fi

[[ -n "$DATASET" ]] || { echo "FAIL: task_source.dataset_name is empty"; exit 1; }

HF_TOKEN_PATH="${HF_HOME:-$HOME/.cache/huggingface}/token"
RESULT="$(HF_DATASET_ID="$DATASET" HF_TOKEN_FILE="$HF_TOKEN_PATH" python3 - <<'PY'
import os, urllib.request, urllib.error
ds = os.environ["HF_DATASET_ID"]
tok_path = os.environ["HF_TOKEN_FILE"]
token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or ""
if not token and os.path.exists(tok_path):
    with open(tok_path) as fh: token = fh.read().strip()
hdrs = {"User-Agent": "curl/8.5.0"}
if token: hdrs["Authorization"] = f"Bearer {token}"
req = urllib.request.Request(f"https://huggingface.co/api/datasets/{ds}", headers=hdrs)
try:
    with urllib.request.urlopen(req, timeout=15) as r: print(f"OK:{r.status}")
except urllib.error.HTTPError as e: print(f"HTTP:{e.code}")
except Exception as e: print(f"NET:{type(e).__name__}:{e}")
PY
)"
case "$RESULT" in
  OK:*)         echo "PASS: HF dataset reachable ($DATASET)" ;;
  HTTP:401|HTTP:403) echo "FAIL: HF dataset auth failed ($RESULT) — set HF token at $HF_TOKEN_PATH"; exit 1 ;;
  HTTP:404)     echo "FAIL: HF dataset not found: $DATASET"; exit 1 ;;
  HTTP:*)       echo "FAIL: HF dataset probe HTTP ${RESULT#HTTP:}"; exit 1 ;;
  NET:*)        echo "FAIL: HF dataset network error: ${RESULT#NET:}"; exit 1 ;;
esac
