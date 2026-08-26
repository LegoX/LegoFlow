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
    if command -v docker >/dev/null 2>&1; then
      docker run --rm -v "$ROOT:/workspace:rw" alpine:3 \
        sh -c 'rm -rf -- "/workspace/$1"' sh "$relative"
    else
      rm -rf -- "$path"
    fi
  fi
done
