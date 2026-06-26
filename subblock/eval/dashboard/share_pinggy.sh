#!/usr/bin/env bash
set -u

LOCAL_HOST="${LOCAL_HOST:-127.0.0.1}"
PORT="${PORT:-8092}"
SSH_PORT="${SSH_PORT:-443}"
PINGGY_USER="${PINGGY_USER:-qr}"
PINGGY_HOST="${PINGGY_HOST:-free.pinggy.io}"
RECONNECT_DELAY="${RECONNECT_DELAY:-5}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

check_local_server() {
  if ! curl -fsS --max-time 3 "http://${LOCAL_HOST}:${PORT}/" >/dev/null; then
    log "local dashboard is not reachable at http://${LOCAL_HOST}:${PORT}/"
    log "start it first with: ./start.sh"
    exit 1
  fi
}

start_tunnel() {
  log "sharing http://${LOCAL_HOST}:${PORT}/ through Pinggy"
  log "watch the Pinggy banner below for the public URL"
  log "free tunnels may time out after a while and reconnect with a new URL"

  ssh \
    -p "$SSH_PORT" \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -R "0:${LOCAL_HOST}:${PORT}" \
    "${PINGGY_USER}@${PINGGY_HOST}"
}

while true; do
  check_local_server
  start_tunnel
  status=$?
  log "Pinggy tunnel exited with status ${status}; reconnecting in ${RECONNECT_DELAY}s"
  sleep "$RECONNECT_DELAY"
done