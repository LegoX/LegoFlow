#!/usr/bin/env bash
# Validate the root lego_factory block without side effects.
# Checks local files, subblock configs, required runtime inputs, and SSH reachability.
# Pass --full to also run each subblock's own dryrun on the remote node.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0
FAIL=0
WARN=0
FULL=0

ok()   { echo "  [OK]   $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $1"; WARN=$((WARN+1)); }
info() { echo "  [INFO] $1"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) FULL=1; shift ;;
    -h|--help)
      echo "Usage: bash scripts/dryrun.sh [--full]"
      echo "  --full   also run each subblock's dryrun.sh on the remote node"
      exit 0
      ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

cfg() {
  python3 - "$1" "$2" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("")
    sys.exit(0)
config_path, dotted_key = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
value = data
for part in dotted_key.split("."):
    if not isinstance(value, dict):
        value = None
        break
    value = value.get(part)
if value is None:
    print("")
else:
    print(str(value))
PY
}

echo "=== Block Dryrun: lego_factory ==="
echo ""

# ── 1. Local file checks ──────────────────────────────────────────────────────
echo "1. Local files"

for f in CLAUDE.md BLOCK_DEFINITION.md dashboard/overview.mdx artifacts/index.yaml; do
  if [[ -f "$ROOT_DIR/$f" ]]; then
    ok "$f"
  else
    fail "missing: $f"
  fi
done

for d in dashboard artifacts scripts subblock; do
  if [[ -d "$ROOT_DIR/$d" ]]; then
    ok "dir: $d/"
  else
    fail "missing dir: $d/"
  fi
done

# ── 2. Subblock config checks ─────────────────────────────────────────────────
echo ""
echo "2. Subblock configs"

SWEGEN_CFG="$ROOT_DIR/subblock/curator/config.yaml"
TRAJGEN_CFG="$ROOT_DIR/subblock/tracer/config.yaml"

for cfg_file in "$SWEGEN_CFG" "$TRAJGEN_CFG"; do
  label="${cfg_file#$ROOT_DIR/}"
  if [[ ! -f "$cfg_file" ]]; then
    fail "missing: $label"
    continue
  fi
  ok "$label exists"
  for section in meta_info runtime_info status; do
    if grep -q "^${section}:" "$cfg_file"; then
      ok "$label: has $section"
    else
      fail "$label: missing $section section"
    fi
  done
done

# ── 3. Runtime input checks ───────────────────────────────────────────────────
echo ""
echo "3. Runtime inputs"

if [[ -f "$SWEGEN_CFG" ]]; then
  gh_tokens="$(cfg "$SWEGEN_CFG" "runtime_info.input.github_tokens")"
  if [[ -n "$gh_tokens" && "$gh_tokens" != "null" ]]; then
    ok "curator: github_tokens set"
  else
    warn "curator: github_tokens is empty — set runtime_info.input.github_tokens in subblock/curator/config.yaml"
  fi

  sw_api_key="$(cfg "$SWEGEN_CFG" "runtime_info.input.llm_api.api_key")"
  sw_api_base="$(cfg "$SWEGEN_CFG" "runtime_info.input.llm_api.api_base_url")"
  sw_pr_model="$(cfg "$SWEGEN_CFG" "runtime_info.input.llm_api.pr_model")"
  [[ -n "$sw_api_key"  && "$sw_api_key"  != "null" ]] && ok "curator: llm_api.api_key set"      || warn "curator: llm_api.api_key is empty"
  [[ -n "$sw_api_base" && "$sw_api_base" != "null" ]] && ok "curator: llm_api.api_base_url set"  || warn "curator: llm_api.api_base_url is empty"
  [[ -n "$sw_pr_model" && "$sw_pr_model" != "null" ]] && ok "curator: llm_api.pr_model set"      || warn "curator: llm_api.pr_model is empty"
fi

if [[ -f "$TRAJGEN_CFG" ]]; then
  tj_api_key="$(cfg "$TRAJGEN_CFG" "runtime_info.input.llm_api.api_key")"
  tj_api_base="$(cfg "$TRAJGEN_CFG" "runtime_info.input.llm_api.api_base_url")"
  tj_model="$(cfg "$TRAJGEN_CFG" "runtime_info.input.llm_api.model")"
  [[ -n "$tj_api_key"  && "$tj_api_key"  != "null" ]] && ok "tracer: llm_api.api_key set"      || warn "tracer: llm_api.api_key is empty"
  [[ -n "$tj_api_base" && "$tj_api_base" != "null" ]] && ok "tracer: llm_api.api_base_url set"  || warn "tracer: llm_api.api_base_url is empty"
  [[ -n "$tj_model"    && "$tj_model"    != "null" ]] && ok "tracer: llm_api.model set"         || warn "tracer: llm_api.model is empty"
fi

# ── 4. SSH reachability ───────────────────────────────────────────────────────
echo ""
echo "4. SSH reachability"

REMOTE_IP="$(cfg "$SWEGEN_CFG" "meta_info.resources.ip" 2>/dev/null || echo "")"
REMOTE_USER="$(cfg "$SWEGEN_CFG" "meta_info.resources.user" 2>/dev/null || echo "root")"
REMOTE_DIR="$(cfg "$SWEGEN_CFG" "meta_info.resources.directory" 2>/dev/null || echo "")"

SSH_OK=0
if [[ -z "$REMOTE_IP" || "$REMOTE_IP" == "null" || "$REMOTE_IP" == "local" ]]; then
  ok "local execution (ip=${REMOTE_IP:-<unset>}) — SSH check not required"
else
  info "testing SSH to ${REMOTE_USER}@${REMOTE_IP} ..."
  if ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no \
       "${REMOTE_USER}@${REMOTE_IP}" echo ok &>/dev/null; then
    ok "SSH to ${REMOTE_USER}@${REMOTE_IP}"
    SSH_OK=1
  else
    warn "SSH to ${REMOTE_USER}@${REMOTE_IP} failed — node may be unreachable from this machine"
  fi
fi

# ── 5. Remote directory check ─────────────────────────────────────────────────
if [[ $SSH_OK -eq 1 && -n "$REMOTE_DIR" && "$REMOTE_DIR" != "null" ]]; then
  echo ""
  echo "5. Remote directory"
  REMOTE_REPO_DIR="${REMOTE_DIR%/}/LegoFactory"
  if ssh -o BatchMode=yes "${REMOTE_USER}@${REMOTE_IP}" \
       "test -d '${REMOTE_REPO_DIR}'" 2>/dev/null; then
    ok "remote dir exists: ${REMOTE_REPO_DIR}"
  else
    warn "remote dir not found: ${REMOTE_REPO_DIR} — run scripts/start.sh to sync"
  fi
fi

# ── 6. Full subblock dryruns (optional) ───────────────────────────────────────
if [[ $FULL -eq 1 && $SSH_OK -eq 1 ]]; then
  echo ""
  echo "6. Subblock dryruns (--full)"
  REMOTE_REPO_DIR="${REMOTE_DIR%/}/LegoFactory"

  for subblock in curator tracer; do
    info "running subblock/${subblock}/scripts/dryrun.sh on remote ..."
    if ssh -o BatchMode=yes "${REMOTE_USER}@${REMOTE_IP}" \
         "cd '${REMOTE_REPO_DIR}/subblock/${subblock}' && bash scripts/dryrun.sh" 2>&1 \
         | sed "s/^/    [${subblock}] /"; then
      ok "subblock/${subblock} dryrun passed"
    else
      fail "subblock/${subblock} dryrun failed"
    fi
  done
elif [[ $FULL -eq 1 && $SSH_OK -eq 0 ]]; then
  warn "--full requested but SSH unavailable; skipping subblock dryruns"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Summary: ${PASS} passed, ${WARN} warnings, ${FAIL} failed ==="

if [[ $FAIL -gt 0 ]]; then
  echo "RESULT: FAIL"
  exit 1
elif [[ $WARN -gt 0 ]]; then
  echo "RESULT: PASS (with warnings)"
else
  echo "RESULT: PASS"
fi
