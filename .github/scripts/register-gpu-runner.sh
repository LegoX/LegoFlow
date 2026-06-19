#!/usr/bin/env bash
# Register a GitHub Actions self-hosted runner ON THE CURRENT (GPU) MACHINE,
# carrying the `swe-lego-gpu` label that the CI `sft-smoke` job is pinned to.
#
# Run this on the GPU host itself — the runner binds to whatever machine it is
# started on, and sft-smoke needs the 8× GPUs that live here. Every other CI
# job stays on the generic `swe-lego-ci` pool; this box also gets `swe-lego-ci`
# so it can help with those too.
#
# Usage (on the GPU machine):
#   REG_TOKEN=<token> bash .github/scripts/register-gpu-runner.sh
#
#   token: repo Settings → Actions → Runners → New self-hosted runner, or
#     gh api -X POST repos/SWE-Lego/SWE-Lego-Live/actions/runners/registration-token -q .token
#
# Env knobs:
#   REG_TOKEN     (required) runner registration token
#   RUNNER_DIR    install dir (default: ./actions-runner-gpu under CWD)
#   RUNNER_NAME   runner name (default: gpu-<hostname>)
#   LABELS        labels (default: swe-lego-ci,swe-lego-gpu)
#   RUNNER_VERSION  actions/runner version to download (default: 2.323.0)
set -euo pipefail

REPO_URL="https://github.com/SWE-Lego/SWE-Lego-Live"
: "${REG_TOKEN:?set REG_TOKEN (registration token from the repo Actions settings)}"
RUNNER_DIR="${RUNNER_DIR:-$PWD/actions-runner-gpu}"
RUNNER_NAME="${RUNNER_NAME:-gpu-$(hostname)}"
LABELS="${LABELS:-swe-lego-ci,swe-lego-gpu}"
RUNNER_VERSION="${RUNNER_VERSION:-2.323.0}"

GPU_COUNT="$(command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l | tr -d ' ' || echo 0)"
if [ "${GPU_COUNT:-0}" -lt 8 ]; then
  if [ "${ALLOW_NON_GPU:-0}" = "1" ]; then
    echo "WARNING: host reports ${GPU_COUNT} GPU(s) (<8) but ALLOW_NON_GPU=1 — registering anyway." >&2
  else
    echo "ERROR: this host reports ${GPU_COUNT} GPU(s) (<8). The 'swe-lego-gpu' label is" >&2
    echo "       reserved for the 8-GPU training host — sft-smoke is scheduled ONLY by it." >&2
    echo "       Registering a CPU box here would let the gated smoke SKIP and the job go" >&2
    echo "       GREEN without ever running training. Aborting." >&2
    echo "       Run this on the real 8-GPU machine, or set ALLOW_NON_GPU=1 to override." >&2
    exit 1
  fi
fi

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

if [ ! -x ./config.sh ]; then
  arch="x64"; case "$(uname -m)" in aarch64|arm64) arch="arm64";; esac
  tgz="actions-runner-linux-${arch}-${RUNNER_VERSION}.tar.gz"
  echo "=== downloading actions/runner ${RUNNER_VERSION} (${arch}) ==="
  curl -fsSLO "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${tgz}"
  tar xzf "$tgz" && rm -f "$tgz"
fi

echo "=== configuring runner '$RUNNER_NAME' with labels '$LABELS' ==="
./config.sh --unattended --replace \
  --url "$REPO_URL" \
  --token "$REG_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$LABELS" \
  --work _work

cat <<EOF

Runner '$RUNNER_NAME' configured in $RUNNER_DIR with labels: $LABELS
Start it one of two ways (from $RUNNER_DIR):
  ./run.sh                                  # foreground / tmux
  sudo ./svc.sh install && sudo ./svc.sh start   # as a systemd service

Once online it shows up under repo Settings → Actions → Runners with the
'swe-lego-gpu' label, and the CI sft-smoke job will schedule onto it.
EOF
