#!/usr/bin/env bash
# CI test 02: repos/legoflow-curator pin + venv editable install.
# - If commit_id is non-null, assert HEAD matches.
# - Assert artifacts/envs/legoflow-curator-env exists.
# - Assert `python -c "import legoflow_curator"` succeeds inside that venv.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

cfg() {
  python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f: d = yaml.safe_load(f) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

REPO_PATH="$BLOCK_DIR/repos/legoflow-curator"
COMMIT_PIN="$(cfg meta_info.repos.legoflow-curator.commit_id)"
VENV_PATH_RAW="$(cfg meta_info.environment.venv_path)"
VENV_PATH="$BLOCK_DIR/$VENV_PATH_RAW"

[[ -e "$REPO_PATH/.git" ]] || { echo "FAIL: $REPO_PATH/.git missing — run /curator:setup"; exit 1; }

if [[ -n "$COMMIT_PIN" && "$COMMIT_PIN" != "null" ]]; then
  HEAD="$(git -C "$REPO_PATH" rev-parse HEAD 2>/dev/null || true)"
  if [[ "$HEAD" != "$COMMIT_PIN" ]]; then
    echo "FAIL: repos/legoflow-curator HEAD=$HEAD does not match config commit_id=$COMMIT_PIN"
    exit 1
  fi
  echo "INFO: repos/legoflow-curator pinned at $COMMIT_PIN"
else
  echo "INFO: meta_info.repos.legoflow-curator.commit_id is null (treated as latest); skipping pin check"
fi

[[ -x "$VENV_PATH/bin/python" ]] || { echo "FAIL: venv missing at $VENV_PATH_RAW — run /curator:setup"; exit 1; }

if "$VENV_PATH/bin/python" -c "import legoflow_curator" >/dev/null 2>&1; then
  echo "PASS: repos/legoflow-curator pin + legoflow-curator importable in venv"
else
  echo "FAIL: legoflow-curator not importable in $VENV_PATH/bin/python — run pip install -e repos/legoflow-curator/"
  exit 1
fi
