#!/usr/bin/env bash
# CI test 09: the launch gate must validate a real completion response and
# classify local authentication failures as fatal.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

cat >"$TMP_DIR/server.py" <<'PY'
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

root = Path(sys.argv[1])


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not self.path.endswith("/v1/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return
        mode = (root / "mode").read_text().strip()
        if mode == "ok":
            status, payload = 200, {"choices": [{"message": {"content": "pong"}}]}
        elif mode == "bad200":
            status, payload = 200, {"error": "not a completion"}
        elif mode == "unavailable":
            status, payload = 502, {"error": "origin down"}
        else:
            status, payload = 401, {"error": "unauthorized"}
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
(root / "port").write_text(str(server.server_port))
server.serve_forever()
PY

echo ok >"$TMP_DIR/mode"
python3 "$TMP_DIR/server.py" "$TMP_DIR" &
SERVER_PID=$!
for _ in {1..50}; do
  [[ -s "$TMP_DIR/port" ]] && break
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "FAIL: mock server exited"; exit 1; }
  sleep 0.1
done
[[ -s "$TMP_DIR/port" ]] || { echo "FAIL: mock server did not start"; exit 1; }
PORT="$(cat "$TMP_DIR/port")"
BASE_URL="http://127.0.0.1:$PORT/v1"

bash "$BLOCK_DIR/scripts/probe_llm_completion.sh" "$BASE_URL" openai/mock key >/dev/null

echo bad200 >"$TMP_DIR/mode"
set +e
bash "$BLOCK_DIR/scripts/probe_llm_completion.sh" "$BASE_URL" openai/mock key >/dev/null
BAD200_RC=$?
echo unauthorized >"$TMP_DIR/mode"
bash "$BLOCK_DIR/scripts/probe_llm_completion.sh" "$BASE_URL" openai/mock key >/dev/null
AUTH_RC=$?
echo unavailable >"$TMP_DIR/mode"
bash "$BLOCK_DIR/scripts/probe_llm_completion.sh" "$BASE_URL" openai/mock key >/dev/null
UNAVAILABLE_RC=$?
echo unauthorized >"$TMP_DIR/mode"
EVAL_GATEWAY_HOST_SUFFIX="example.com" \
HTTP_PROXY="http://127.0.0.1:$PORT" http_proxy="http://127.0.0.1:$PORT" \
NO_PROXY="" no_proxy="" \
  bash "$BLOCK_DIR/scripts/probe_llm_completion.sh" \
    "http://gateway.example.com/v1" openai/mock key >/dev/null
GATEWAY_WARN_RC=$?
set -e

[[ "$BAD200_RC" == "1" ]] || { echo "FAIL: malformed 200 returned rc=$BAD200_RC, expected 1"; exit 1; }
[[ "$AUTH_RC" == "1" ]] || { echo "FAIL: local 401 returned rc=$AUTH_RC, expected 1"; exit 1; }
[[ "$UNAVAILABLE_RC" == "1" ]] || { echo "FAIL: HTTP 502 returned rc=$UNAVAILABLE_RC, expected 1"; exit 1; }
[[ "$GATEWAY_WARN_RC" == "77" ]] || { echo "FAIL: gateway 401 returned rc=$GATEWAY_WARN_RC, expected 77"; exit 1; }
echo "PASS: completion launch gate validates success, malformed 2xx, local auth, 5xx, and gateway warning"
