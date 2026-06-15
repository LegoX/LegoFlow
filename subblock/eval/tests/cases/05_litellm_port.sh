#!/usr/bin/env bash
# CI test 05: LiteLLM proxy port either free or held by current uid.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

PORT="$(python3 -c "import yaml; d=yaml.safe_load(open('$CONFIG')) or {}; print(d.get('runtime_info',{}).get('input',{}).get('litellm_proxy',{}).get('port') or '')")"
[[ -n "$PORT" ]] || { echo "FAIL: litellm_proxy.port is unset"; exit 1; }

# Try ss first (more informative); fall back to a Python socket bind if ss is missing.
if command -v ss >/dev/null 2>&1; then
  HOLDER="$(ss -ltnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p"$" {print $0; exit}')"
  if [[ -z "$HOLDER" ]]; then
    echo "PASS: LiteLLM port $PORT is free"; exit 0
  fi
  # `users:` only resolves for processes the running uid owns.
  if [[ "$HOLDER" == *"users:"* ]]; then
    echo "PASS: LiteLLM port $PORT held by current uid (process-info visible): $HOLDER"
    exit 0
  fi
  echo "FAIL: LiteLLM port $PORT held by another uid — start.sh would fail to bind"
  echo "       $HOLDER"
  exit 1
fi

PYRESULT="$(PORT="$PORT" python3 - <<'PY'
import os, socket
p = int(os.environ["PORT"])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", p))
    print("FREE")
except OSError as e:
    print(f"BUSY:{e.errno}")
finally:
    s.close()
PY
)"
[[ "$PYRESULT" == "FREE" ]] && { echo "PASS: LiteLLM port $PORT is free (socket bind)"; exit 0; }
echo "FAIL: LiteLLM port $PORT not bindable ($PYRESULT)"
exit 1
