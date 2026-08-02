#!/usr/bin/env bash
# CI test 06: agent.runtime_image already pulled locally.
# CI must not pay a multi-GB docker pull mid-launch; the runner is expected
# to have warmed the image up front.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

IMAGE="$(python3 -c "import yaml; d=yaml.safe_load(open('$CONFIG')) or {}; print(d.get('runtime_info',{}).get('input',{}).get('agent',{}).get('runtime_image') or '')")"
[[ -n "$IMAGE" ]] || { echo "FAIL: agent.runtime_image is empty"; exit 1; }

command -v docker >/dev/null 2>&1 || { echo "FAIL: docker CLI not on PATH"; exit 1; }
: "${DOCKER_HOST:=unix:///var/run/docker.sock}"; export DOCKER_HOST
docker info >/dev/null 2>&1 || { echo "FAIL: docker daemon not reachable (DOCKER_HOST=$DOCKER_HOST)"; exit 1; }

if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "PASS: agent.runtime_image present locally ($IMAGE)"
else
  echo "FAIL: agent.runtime_image not pulled — docker pull $IMAGE"
  exit 1
fi
