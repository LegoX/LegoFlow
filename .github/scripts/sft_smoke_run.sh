#!/usr/bin/env bash
# Run the guarded SFT training smoke synchronously, locally or on a configured
# remote GPU host. Unlike the generic smoke launcher, every setup/preflight
# failure is propagated and the training process remains attached to CI.
set -euo pipefail

BUDGET="${1:-2700}"
REPO_ROOT="${GITHUB_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BLOCK_DIR="$REPO_ROOT/subblock/trainer"
LOG="$BLOCK_DIR/artifacts/logs/smoke-launch.log"
SHA="${GITHUB_SHA:-$(git -C "$REPO_ROOT" rev-parse HEAD)}"

mkdir -p "$(dirname "$LOG")"
: >"$LOG"

if [[ -n "${SHARED_RUNTIME:-}" ]]; then
  DEFAULT_REMOTE_ENV="${SHARED_RUNTIME%/runtime}/sft-remote.env"
else
  DEFAULT_REMOTE_ENV=""
fi
REMOTE_ENV_FILE="${SFT_REMOTE_ENV_FILE:-$DEFAULT_REMOTE_ENV}"
if [[ -n "$REMOTE_ENV_FILE" && -f "$REMOTE_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$REMOTE_ENV_FILE"
  set +a
fi

run_local() {
  cd "$BLOCK_DIR"
  bash tests/cases/02_repo_pins.sh
  bash tests/cases/03_uv_env_editable.sh
  SFT_CONFIG="$BLOCK_DIR/tests/smoke/config.yaml" bash scripts/dryrun.sh
  SFT_CONFIG="$BLOCK_DIR/tests/smoke/config.yaml" \
    SFT_SMOKE_CI_STRICT=1 \
    SFT_SMOKE_BUDGET="$BUDGET" \
    bash tests/smoke/10_train_demo.sh
}

if [[ -z "${SFT_REMOTE_HOST:-}" ]]; then
  run_local 2>&1 | tee -a "$LOG"
  exit "${PIPESTATUS[0]}"
fi

for name in SFT_REMOTE_USER SFT_REMOTE_KEY SFT_REMOTE_PORT SFT_REMOTE_DIR SFT_REMOTE_RUNTIME_DIR; do
  [[ -n "${!name:-}" ]] || { echo "ERROR: $name is required for remote SFT smoke" >&2; exit 1; }
done
[[ -f "$SFT_REMOTE_KEY" ]] || { echo "ERROR: SFT remote SSH key not found: $SFT_REMOTE_KEY" >&2; exit 1; }

SSH_ARGS=(
  -i "$SFT_REMOTE_KEY"
  -p "$SFT_REMOTE_PORT"
  -o StrictHostKeyChecking=accept-new
  -o BatchMode=yes
  -o ConnectTimeout=20
)

set +e
ssh "${SSH_ARGS[@]}" "$SFT_REMOTE_USER@$SFT_REMOTE_HOST" \
  bash -s -- "$SFT_REMOTE_DIR" "$SHA" "$BUDGET" "$SFT_REMOTE_RUNTIME_DIR" <<'REMOTE' 2>&1 | tee -a "$LOG"
set -euo pipefail
repo_dir="$1"
sha="$2"
budget="$3"
runtime_dir="$4"
shared_root="${runtime_dir%/runtime/sft}"
export PATH="$shared_root/uv/bin:/root/.local/bin:$PATH"

cd "$repo_dir"
git -c fetch.recurseSubmodules=false fetch origin "$sha"
git reset --hard "$sha"
git submodule sync -- \
  subblock/trainer/repos/LLaMA-Factory \
  subblock/trainer/repos/swe_data_process
# The managed GPU host authenticates GitHub over SSH. Keep the tracked
# developer-facing URLs as HTTPS, but use host-local SSH overrides here.
git config submodule.subblock/trainer/repos/LLaMA-Factory.url \
  git@github.com:SWE-Lego/LLaMA-Factory.git
git config submodule.subblock/trainer/repos/swe_data_process.url \
  git@github.com:SWE-Lego/swe_data_process.git
if git -C subblock/trainer/repos/LLaMA-Factory rev-parse --git-dir >/dev/null 2>&1; then
  git -C subblock/trainer/repos/LLaMA-Factory remote set-url origin \
    git@github.com:SWE-Lego/LLaMA-Factory.git
fi
if git -C subblock/trainer/repos/swe_data_process rev-parse --git-dir >/dev/null 2>&1; then
  git -C subblock/trainer/repos/swe_data_process remote set-url origin \
    git@github.com:SWE-Lego/swe_data_process.git
fi
git submodule update --init --recursive --force -- \
  subblock/trainer/repos/LLaMA-Factory \
  subblock/trainer/repos/swe_data_process

cd subblock/trainer
rm -rf artifacts/env artifacts/data/examples
mkdir -p artifacts/data
ln -s "$runtime_dir/artifacts/env" artifacts/env
ln -s "$runtime_dir/artifacts/data/examples" artifacts/data/examples
bash tests/cases/02_repo_pins.sh
bash tests/cases/03_uv_env_editable.sh
SFT_CONFIG="$PWD/tests/smoke/config.yaml" bash scripts/dryrun.sh
SFT_CONFIG="$PWD/tests/smoke/config.yaml" \
  SFT_SMOKE_CI_STRICT=1 \
  SFT_SMOKE_BUDGET="$budget" \
  bash tests/smoke/10_train_demo.sh
REMOTE
rc="${PIPESTATUS[0]}"
set -e
exit "$rc"
