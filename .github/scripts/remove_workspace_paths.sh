#!/usr/bin/env bash
# Remove checkout-local paths without traversing shared-runtime repositories.
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$(git rev-parse --show-toplevel)}"

for relative in "$@"; do
  case "$relative" in
    /*|..|../*|*/../*)
      echo "ERROR: refusing unsafe workspace path: $relative" >&2
      exit 2
      ;;
  esac

  path="$ROOT/$relative"
  if [[ -L "$path" || -f "$path" ]]; then
    rm -f -- "$path"
  elif [[ -d "$path" ]]; then
    stale="$path.legoflow-stale-${GITHUB_RUN_ID:-manual}-$$"
    if mv -- "$path" "$stale"; then
      stale_relative="${stale#"$ROOT/"}"
      if command -v docker >/dev/null 2>&1; then
        if ! timeout --signal=TERM --kill-after=5s 30s docker run --detach --rm \
          -v "$ROOT:/workspace:rw" alpine:3 \
          sh -c 'rm -rf -- "/workspace/$1"' sh "$stale_relative" >/dev/null 2>&1; then
          echo "WARN: detached cleanup could not start for $stale_relative" >&2
          rm -rf -- "$stale" >/dev/null 2>&1 &
        fi
      else
        rm -rf -- "$stale" >/dev/null 2>&1 &
      fi
    elif command -v docker >/dev/null 2>&1; then
      timeout --signal=TERM --kill-after=10s 120s docker run --rm \
        -v "$ROOT:/workspace:rw" alpine:3 \
        sh -c 'rm -rf -- "/workspace/$1"' sh "$relative" || true
    else
      rm -rf -- "$path"
    fi
    [[ ! -e "$path" ]] || {
      echo "ERROR: could not remove workspace path: $relative" >&2
      exit 1
    }
  fi
done
