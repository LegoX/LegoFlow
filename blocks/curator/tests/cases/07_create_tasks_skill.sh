#!/usr/bin/env bash
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_DIR="$BLOCK_DIR/.claude/plugins/curator-plugin"
CANONICAL="$PLUGIN_DIR/skills/create-tasks/SKILL.md"
COMPAT="$PLUGIN_DIR/skills/run/SKILL.md"
MANIFEST="$PLUGIN_DIR/.claude-plugin/plugin.json"
MARKETPLACE="$BLOCK_DIR/.claude/plugins/.claude-plugin/marketplace.json"
README="$PLUGIN_DIR/README.md"

python3 - "$CANONICAL" "$COMPAT" "$MANIFEST" "$MARKETPLACE" "$README" <<'PY'
import json
import re
import sys
from pathlib import Path

canonical, compat, manifest, marketplace, readme = map(Path, sys.argv[1:])
for path in (canonical, compat, manifest, marketplace, readme):
    if not path.is_file():
        raise SystemExit(f"FAIL: missing {path}")

canonical_text = canonical.read_text(encoding="utf-8")
compat_text = compat.read_text(encoding="utf-8")
compat_words = " ".join(compat_text.split())
manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
marketplace_data = json.loads(marketplace.read_text(encoding="utf-8"))
readme_text = readme.read_text(encoding="utf-8")

if not re.search(r"^name:\s*create-tasks\s*$", canonical_text, re.MULTILINE):
    raise SystemExit("FAIL: canonical skill frontmatter is not create-tasks")
if "# /curator:create-tasks" not in canonical_text:
    raise SystemExit("FAIL: canonical skill heading is stale")
if not re.search(r"^name:\s*run\s*$", compat_text, re.MULTILINE):
    raise SystemExit("FAIL: compatibility skill frontmatter is not run")
if "/curator:create-tasks" not in compat_text:
    raise SystemExit("FAIL: run compatibility skill does not delegate")
if "scripts/start_with_" in compat_text or "legoflow-curator create" in compat_text:
    raise SystemExit("FAIL: run compatibility skill duplicates implementation")
if "uniform-interface compatibility adapter" not in compat_text:
    raise SystemExit("FAIL: run skill does not explain its uniform-layout purpose")
if "`/root:run curator` does not invoke this adapter" not in compat_words:
    raise SystemExit("FAIL: run skill misstates current root targeting")
if "/curator:create-tasks" not in manifest_data.get("description", ""):
    raise SystemExit("FAIL: plugin manifest omits canonical command")
metadata = [
    manifest_data.get("description", ""),
    marketplace_data["plugins"][0].get("description", ""),
    readme_text,
]
if any("uniform-interface compatibility adapter" not in text for text in metadata):
    raise SystemExit("FAIL: plugin metadata does not explain the uniform adapter")
if any("root compatibility adapter" in text or "root-protocol" in text for text in metadata):
    raise SystemExit("FAIL: plugin metadata still claims root owns the adapter")

print("PASS: curator create-tasks command surface")
PY
