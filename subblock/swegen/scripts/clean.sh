#!/bin/bash
# Remove batch state directories created by create_*.sh scripts.
cd "$(dirname "$0")/.."
set -euo pipefail

echo "Cleaning batch state directories..."
for lang in py js ts go c cpp java rust; do
    dir=".swegen-${lang}"
    [ -d "$dir" ] && rm -rf "$dir" && echo "  removed ${dir}" || true
done
echo "Done."
