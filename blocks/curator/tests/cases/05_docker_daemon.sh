#!/usr/bin/env bash
# CI test 05: Docker socket reachable.

set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker CLI not on PATH"
  exit 1
fi

: "${DOCKER_HOST:=unix:///var/run/docker.sock}"
export DOCKER_HOST

if ! docker info >/dev/null 2>&1; then
  echo "FAIL: docker info failed (DOCKER_HOST=$DOCKER_HOST). Daemon not running or socket not accessible."
  exit 1
fi

VER="$(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo unknown)"
echo "PASS: Docker daemon reachable (server=$VER, DOCKER_HOST=$DOCKER_HOST)"
