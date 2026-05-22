#!/bin/bash
# Start all language SWE task creation pipelines in background.
cd "$(dirname "$0")/.."
exec bash scripts/create_all_bg.sh
