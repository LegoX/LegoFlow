#!/usr/bin/env bash
# CI test 03: the two evaluator envs exist with the expected installs.
#   harbor uv  -> `import harbor` resolves under repos/harbor (editable)
#   litellm    -> CLI present AND installed version == the pinned 1.83.14
# (evaluator has no swe_data_process env — it does not convert trajectories.)

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

cfg() { python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

abspath() {
  if [[ "$1" = /* ]]; then printf '%s\n' "$1"; else printf '%s\n' "$BLOCK_DIR/$1"; fi
}

HARBOR_UV="$(abspath "$(cfg meta_info.environment.harbor_uv)")"
LITELLM_UV="$(abspath "$(cfg meta_info.environment.litellm_uv)")"
LITELLM_PIN="$(cfg meta_info.environment.litellm.litellm_version)"
[[ -n "$LITELLM_PIN" ]] || LITELLM_PIN="1.83.14"

fail=0

# --- harbor uv: editable install resolves under repos/harbor ---------------
HARBOR_PY="$HARBOR_UV/bin/python"
if [[ ! -x "$HARBOR_PY" ]]; then
  echo "FAIL: harbor: python missing at $HARBOR_PY"; fail=$((fail+1))
else
  HARBOR_DIR="$(abspath "$(cfg meta_info.repositories.harbor.path)")"
  if ! "$HARBOR_PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
    echo "FAIL: harbor: Python must be >=3.12"; fail=$((fail+1))
  fi
  for pkg in litellm datasets; do
    if ! "$HARBOR_PY" -c "import $pkg" >/dev/null 2>&1; then
      echo "FAIL: harbor: package not importable: $pkg"; fail=$((fail+1))
    fi
  done
  (
    cd "$HARBOR_DIR"
    HARBOR_EDITABLE_ROOT="$HARBOR_DIR" \
    UV_PROJECT_ENVIRONMENT="$HARBOR_UV" \
      "$HARBOR_PY" "$BLOCK_DIR/scripts/check_harbor_editable.py" 2>&1
  ) >/tmp/eval_test_harbor_editable.$$ 2>&1 || true
  out="$(cat /tmp/eval_test_harbor_editable.$$)"; rm -f /tmp/eval_test_harbor_editable.$$
  case "$out" in
    ok:*)           echo "INFO: harbor editable from $(cfg meta_info.repositories.harbor.path)" ;;
    mismatch:*)     echo "FAIL: harbor: import not from repos/harbor (${out#mismatch:})"; fail=$((fail+1)) ;;
    import_error:*) echo "FAIL: harbor: ${out#import_error:}"; fail=$((fail+1)) ;;
    *)              echo "FAIL: harbor: editable check inconclusive: $out"; fail=$((fail+1)) ;;
  esac
  if [[ ! -x "$HARBOR_UV/bin/harbor" ]]; then
    echo "FAIL: harbor CLI missing at $HARBOR_UV/bin/harbor"; fail=$((fail+1))
  elif ! "$HARBOR_UV/bin/harbor" --help >/dev/null 2>&1; then
    echo "FAIL: harbor CLI --help failed"; fail=$((fail+1))
  fi
fi

# --- litellm venv: CLI present AND installed version == the pin -------------
# Note: do NOT use `import litellm; litellm.__version__` — litellm raises
# AttributeError on __version__ by design. Read the dist metadata instead.
LITELLM_PY="$LITELLM_UV/bin/python"
if [[ ! -x "$LITELLM_PY" ]]; then
  echo "FAIL: litellm: python missing at $LITELLM_PY"; fail=$((fail+1))
elif [[ ! -x "$LITELLM_UV/bin/litellm" ]]; then
  echo "FAIL: litellm: CLI missing at $LITELLM_UV/bin/litellm"; fail=$((fail+1))
else
  ver="$("$LITELLM_PY" -c "import importlib.metadata as m; print(m.version('litellm'))" 2>/dev/null || true)"
  if [[ -z "$ver" ]]; then
    echo "FAIL: litellm: could not read installed version in $LITELLM_PY"; fail=$((fail+1))
  elif [[ "$ver" != "$LITELLM_PIN" ]]; then
    echo "FAIL: litellm: installed $ver != pinned $LITELLM_PIN"; fail=$((fail+1))
  else
    echo "INFO: litellm env OK (version $ver)"
  fi
fi

if [[ "$fail" -gt 0 ]]; then
  echo "FAIL: $fail env(s) unhealthy"
  exit 1
fi
echo "PASS: harbor (editable) + litellm ($LITELLM_PIN) envs healthy"
