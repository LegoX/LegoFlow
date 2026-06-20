#!/usr/bin/env bash
# CI test 03: three envs exist and the expected packages import.

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

HARBOR_UV="$BLOCK_DIR/$(cfg meta_info.environment.harbor_uv)"
LITELLM_UV="$BLOCK_DIR/$(cfg meta_info.environment.litellm_uv)"
SWE_DP_UV="$BLOCK_DIR/$(cfg meta_info.environment.swe_data_process_uv)"
SFT_ENABLED="$(cfg runtime_info.input.sft_conversion.enabled)"

fail=0

check_env() {
  local name="$1" env_dir="$2" pkg="$3" mod="${4:-$3}"
  local py="$env_dir/bin/python"
  if [[ ! -x "$py" ]]; then
    echo "FAIL: $name: python missing at $py"; fail=$((fail+1)); return
  fi
  # For harbor we additionally check the editable install resolves under repos/harbor
  if [[ "$name" == "harbor" ]]; then
    local harbor_dir
    harbor_dir="$BLOCK_DIR/$(cfg meta_info.repositories.harbor.path)"
    (
      cd "$harbor_dir"
      HARBOR_EDITABLE_ROOT="$harbor_dir" \
      UV_PROJECT_ENVIRONMENT="$env_dir" \
        "$py" "$BLOCK_DIR/scripts/check_harbor_editable.py" 2>/dev/null
    ) >/tmp/tracer_test_harbor_editable.$$ 2>&1 || true
    local out
    out="$(cat /tmp/tracer_test_harbor_editable.$$)"
    rm -f /tmp/tracer_test_harbor_editable.$$
    case "$out" in
      ok:*)         echo "INFO: harbor editable from $(cfg meta_info.repositories.harbor.path)" ;;
      mismatch:*)   echo "FAIL: harbor: import not from repos/harbor (${out#mismatch:})"; fail=$((fail+1)); return ;;
      import_error:*) echo "FAIL: harbor: ${out#import_error:}"; fail=$((fail+1)); return ;;
      *)            echo "FAIL: harbor: editable check inconclusive: $out"; fail=$((fail+1)); return ;;
    esac
  elif ! "$py" -c "import $mod" >/dev/null 2>&1; then
    if [[ "$name" == "swe_data_process" && "$SFT_ENABLED" != "true" ]]; then
      echo "SKIP: swe_data_process import failed but sft_conversion.enabled=false"
      return
    fi
    echo "FAIL: $name: import $mod failed in $py"; fail=$((fail+1)); return
  fi
  echo "INFO: $name env OK ($env_dir)"
}

check_env "harbor"           "$HARBOR_UV"  "harbor"           "harbor"
check_env "litellm"          "$LITELLM_UV" "litellm"          "litellm"
check_env "swe_data_process" "$SWE_DP_UV"  "swe_data_process" "swe_data_process"

if [[ "$fail" -gt 0 ]]; then
  echo "FAIL: $fail env(s) unhealthy"
  exit 1
fi
echo "PASS: all uv envs have expected editable installs"
