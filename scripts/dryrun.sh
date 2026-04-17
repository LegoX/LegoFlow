#!/usr/bin/env bash
set -euo pipefail

# Minimal block health check: verify the expected docs and index files exist.
test -f "CLAUDE.md"
test -f "docs/main.md"
test -f "inputs/index.yaml"
test -f "outputs/index.yaml"

echo "block dryrun ok"
