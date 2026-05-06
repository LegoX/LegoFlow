#!/usr/bin/env bash
set -euo pipefail

# Execute the full pipeline orchestration.

echo "=== Starting Block: swe_lego_live ==="

# TODO: implement block-specific start behavior
# 1. Validate runtime_info.input is filled in config.yaml
# 2. Coordinate execution across subblocks: swegen → trajgen → sft → rl
# 3. Update status section in config.yaml throughout execution
# 4. Archive results to artifacts/archives/run_NNN/ after completion
# 5. Update artifacts/index.yaml with new run entry

echo "Start script not yet implemented."
