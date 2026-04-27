#!/bin/bash
# Remove temporary outputs. Does NOT delete checkpoints or wandb runs.
set -e

echo "[rl-train/clean] Removing Ray temp files..."
rm -rf /tmp/ray /tmp/trajectory_output_dir /tmp/trajectory_output_dir.txt 2>/dev/null || true

echo "[rl-train/clean] Done. Checkpoints and logs preserved."
