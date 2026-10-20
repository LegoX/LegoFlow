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
ENV_DIR="$BLOCK_DIR/artifacts/env/terminal-curator"
python3 - "$ENV_DIR" "$BLOCK_DIR/artifacts" <<'PY'
from pathlib import Path
import sys
env, artifacts = map(Path, sys.argv[1:])
if artifacts.is_symlink() or not env.resolve().is_relative_to(artifacts.resolve()):
    raise SystemExit('ERROR: terminal-curator environment must stay inside its own artifacts directory')
if env.exists() and (not env.is_dir() or (any(env.iterdir()) and not (env/'pyvenv.cfg').is_file())):
    raise SystemExit('ERROR: refusing to install into a non-virtualenv terminal-curator directory')
PY
[[ -x "$ENV_DIR/bin/python" ]] || uv venv "$ENV_DIR" --python 3.12
uv pip install --python "$ENV_DIR/bin/python" -r "$BLOCK_DIR/scripts/requirements.txt"
HARBOR_BIN="$("$ENV_DIR/bin/python" - "$CONFIG" "$BLOCK_DIR" <<'PY'
from pathlib import Path
import sys, yaml
block=Path(sys.argv[2]); sys.path.insert(0, str(block/'scripts'))
from terminal_curator import load_config, resolve
try:
    config=load_config(Path(sys.argv[1]))
except yaml.YAMLError:
    raise SystemExit('ERROR: invalid YAML configuration (source text withheld)') from None
p=resolve(config['runtime_info']['input']['validation']['harbor_bin'])
if p.name!='harbor' or p.parent.name!='bin':
    raise SystemExit('ERROR: validation.harbor_bin must name an isolated environment bin/harbor')
env=p.parent.parent
if env==(block/'artifacts/env/terminal-curator').resolve():
    raise SystemExit('ERROR: Harbor and terminal-curator need separate environments')
for repos in block.parents[1].glob('blocks/*/repos'):
    if env.is_relative_to(repos.resolve()):
        raise SystemExit('ERROR: Harbor environment must stay outside vendored repos')
if env.exists() and (not env.is_dir() or (any(env.iterdir()) and not (env/'pyvenv.cfg').is_file())):
    raise SystemExit('ERROR: refusing to overwrite a non-virtualenv directory; choose an empty Harbor environment path')
print(p)
PY
)"
git -C "$ROOT_DIR" submodule update --init -- blocks/terminal-curator/repos/terminal-lego blocks/tracer/repos/harbor
expected=1a2a6048a0d568891407ed3c6a441c995f4491b0
actual="$(git -C "$BLOCK_DIR/repos/terminal-lego" rev-parse HEAD)"
[[ "$actual" = "$expected" ]] || { echo 'ERROR: Terminal-Lego pin mismatch' >&2; exit 1; }
HARBOR_ENV="$(dirname "$(dirname "$HARBOR_BIN")")"
HARBOR_REPO="$ROOT_DIR/blocks/tracer/repos/harbor"
harbor_expected="$("$ENV_DIR/bin/python" - "$ROOT_DIR/blocks/tracer/config.yaml" <<'PY'
import sys,yaml
from pathlib import Path
print(yaml.safe_load(Path(sys.argv[1]).read_text())['meta_info']['repositories']['harbor']['commit'])
PY
)"
[[ "$(git -C "$HARBOR_REPO" rev-parse HEAD)" = "$harbor_expected" ]] || { echo 'ERROR: Harbor pin mismatch' >&2; exit 1; }
HARBOR_PYTHON="$HARBOR_ENV/bin/python"
[[ -x "$HARBOR_PYTHON" ]] || uv venv "$HARBOR_ENV" --python 3.12
# Frozen lock, no dev/cloud extras; uv's environment is outside repos/.
UV_PROJECT_ENVIRONMENT="$HARBOR_ENV" uv sync --project "$HARBOR_REPO" --frozen --no-dev
"$HARBOR_PYTHON" -c 'import harbor; from harbor.models.task.config import TaskConfig'
git -C "$BLOCK_DIR/repos/terminal-lego" diff --quiet
git -C "$HARBOR_REPO" diff --quiet
echo 'Setup complete. Run scripts/dryrun.sh on the configured execution node.'
