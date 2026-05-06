#!/usr/bin/env bash
set -euo pipefail

# Validates that this block can be read, resolved, and exercised safely without side effects.

echo "=== Block Dryrun: swe_lego_live ==="

# Check required files exist
echo "Checking required files..."
required_files=(
  "config.yaml"
  "CLAUDE.md"
  "dashboard/overview.mdx"
  "dashboard/memory.md"
  "artifacts/index.yaml"
)

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "ERROR: Missing required file: $file"
    exit 1
  fi
done

# Check required directories exist
echo "Checking required directories..."
required_dirs=(
  "dashboard"
  "artifacts"
  "scripts"
  "subblock"
)

for dir in "${required_dirs[@]}"; do
  if [[ ! -d "$dir" ]]; then
    echo "ERROR: Missing required directory: $dir"
    exit 1
  fi
done

# Validate config.yaml structure
echo "Validating config.yaml structure..."
if ! grep -q "meta_info:" config.yaml; then
  echo "ERROR: config.yaml missing meta_info section"
  exit 1
fi

if ! grep -q "runtime_info:" config.yaml; then
  echo "ERROR: config.yaml missing runtime_info section"
  exit 1
fi

if ! grep -q "status:" config.yaml; then
  echo "ERROR: config.yaml missing status section"
  exit 1
fi

echo "✓ All checks passed"
echo "Next: Fill runtime_info.input in config.yaml, then run scripts/start.sh"
