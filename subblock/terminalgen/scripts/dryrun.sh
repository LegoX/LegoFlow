#!/bin/bash
# Validate environment, submodule pin, and inputs before running. No side effects.
set -uo pipefail
cd "$(dirname "$0")/.." || { echo "ERROR: cannot cd to block root"; exit 1; }

# Activate the terminalgen venv if present (the only dep is `requests`).
if [[ -f artifacts/envs/terminalgen-env/bin/activate ]]; then
    # shellcheck disable=SC1091
    source artifacts/envs/terminalgen-env/bin/activate
fi

# shellcheck disable=SC1091
source scripts/load_runtime_env.sh
load_runtime_env

echo "=== terminalgen dryrun ==="
RC=0

# --- shared block-contract validation (schema, deps, fill markers) ------------
REPO_ROOT="$(cd ../.. && pwd)"
if [[ -f "$REPO_ROOT/scripts/validate_config.py" ]]; then
  python3 "$REPO_ROOT/scripts/validate_config.py" --block "$(pwd)" || RC=1
else
  echo "WARN: shared validator not found — skipping contract validation"
fi

# 1) config.yaml parses and identity is correct (needs PyYAML in the active python).
python3 -c "import yaml" 2>/dev/null || { echo "ERROR: PyYAML missing in active python — run: pip install -r requirements.txt"; echo "=== dryrun complete (rc=1) ==="; exit 1; }
python3 - <<'PY' || RC=1
import yaml, sys
cfg = yaml.safe_load(open("config.yaml"))
assert cfg["meta_info"]["name"] == "terminalgen", "meta_info.name != terminalgen"
doms = cfg["runtime_info"]["input"]["domains"]
assert len(doms) >= 1, "no domains configured"
print(f"config.yaml: OK ({len(doms)} domains)")
PY

# 2) terminal-lego submodule present and pinned.
if [[ -f repos/terminal-lego/scraper/so_scraper.py ]]; then
    HEAD="$(git -C repos/terminal-lego rev-parse HEAD 2>/dev/null || echo unknown)"
    PIN="$(python3 -c "import yaml;print(yaml.safe_load(open('config.yaml'))['meta_info']['repos']['terminal-lego']['commit_id'])" 2>/dev/null)"
    if [[ "$HEAD" == "$PIN" ]]; then
        echo "repos/terminal-lego: OK (pinned ${HEAD:0:10})"
    else
        echo "WARN: repos/terminal-lego HEAD ${HEAD:0:10} != pin ${PIN:0:10}"
    fi
else
    echo "ERROR: repos/terminal-lego not initialized — run: git submodule update --init repos/terminal-lego"
    RC=1
fi

# 3) requests importable.
python3 -c "import requests; print('requests: OK')" || { echo "ERROR: pip install -r requirements.txt"; RC=1; }

# 4) Env vars.
for var in OPENAI_API_KEY OPENAI_API_BASE_URL MODEL_NAME; do
    val="${!var:-}"
    [ -n "$val" ] && echo "${var}: set" || { echo "WARN: ${var} not set"; }
done
[ -n "${SO_API_KEY:-}" ] && echo "SO_API_KEY: set (10000/day)" || echo "WARN: SO_API_KEY not set (300/day shared)"

# 5) Docker.
docker run --rm hello-world >/dev/null 2>&1 && echo "Docker: OK" || echo "WARN: Docker not available"

echo "=== dryrun complete (rc=${RC}) ==="
exit $RC
