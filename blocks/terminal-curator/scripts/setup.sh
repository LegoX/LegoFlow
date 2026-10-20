#!/usr/bin/env bash
# Install into artifacts/, preserving both pinned repositories.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
parse_config "$@"; shift "$CONFIG_ARGS_CONSUMED"
[[ $# -eq 0 ]] || { echo 'Usage: setup.sh [--config FILE]' >&2; exit 2; }
command -v uv >/dev/null || { echo 'ERROR: uv is required' >&2; exit 1; }
for repo in "$BLOCK_DIR/repos/terminal-lego" "$ROOT_DIR/blocks/tracer/repos/harbor"; do
  if [[ -e "$repo/.git" ]] && [[ -n "$(git -C "$repo" status --porcelain)" ]]; then
    echo 'ERROR: managed repo has local edits; setup will not reset vendored code' >&2
    exit 1
  fi
done
git -C "$ROOT_DIR" submodule update --init -- blocks/terminal-curator/repos/terminal-lego blocks/tracer/repos/harbor
expected=1a2a6048a0d568891407ed3c6a441c995f4491b0
actual="$(git -C "$BLOCK_DIR/repos/terminal-lego" rev-parse HEAD)"
[[ "$actual" = "$expected" ]] || { echo 'ERROR: Terminal-Lego pin mismatch' >&2; exit 1; }
ENV_DIR="$BLOCK_DIR/artifacts/env/terminal-curator"
[[ -x "$ENV_DIR/bin/python" ]] || uv venv "$ENV_DIR" --python 3.12
uv pip install --python "$ENV_DIR/bin/python" -r "$BLOCK_DIR/scripts/requirements.txt"
HARBOR_BIN="$("$ENV_DIR/bin/python" - "$CONFIG" "$BLOCK_DIR" <<'PY'
from pathlib import Path
import sys, yaml
p=Path(yaml.safe_load(Path(sys.argv[1]).read_text())['runtime_info']['input']['validation']['harbor_bin'])
print(p if p.is_absolute() else (Path(sys.argv[2])/p).resolve())
PY
)"
HARBOR_ENV="$(dirname "$(dirname "$HARBOR_BIN")")"
HARBOR_REPO="$ROOT_DIR/blocks/tracer/repos/harbor"
HARBOR_PYTHON="$HARBOR_ENV/bin/python"
[[ -x "$HARBOR_PYTHON" ]] || uv venv "$HARBOR_ENV" --python 3.12
# Frozen lock, no dev/cloud extras; uv's environment is outside repos/.
UV_PROJECT_ENVIRONMENT="$HARBOR_ENV" uv sync --project "$HARBOR_REPO" --frozen --no-dev
"$HARBOR_PYTHON" -c 'import harbor; from harbor.models.task.config import TaskConfig'
git -C "$BLOCK_DIR/repos/terminal-lego" diff --quiet
git -C "$HARBOR_REPO" diff --quiet
echo 'Setup complete. Run scripts/dryrun.sh on the configured execution node.'
