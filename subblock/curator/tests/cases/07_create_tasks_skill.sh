#!/usr/bin/env bash
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGIN_DIR="$BLOCK_DIR/.claude/plugins/curator-plugin"
CANONICAL="$PLUGIN_DIR/skills/create-tasks/SKILL.md"
COMPAT="$PLUGIN_DIR/skills/run/SKILL.md"
MANIFEST="$PLUGIN_DIR/.claude-plugin/plugin.json"

python3 - "$CANONICAL" "$COMPAT" "$MANIFEST" <<'PY'
import json
import re
import sys
from pathlib import Path

canonical, compat, manifest = map(Path, sys.argv[1:])
for path in (canonical, compat, manifest):
    if not path.is_file():
        raise SystemExit(f"FAIL: missing {path}")

canonical_text = canonical.read_text(encoding="utf-8")
compat_text = compat.read_text(encoding="utf-8")
manifest_data = json.loads(manifest.read_text(encoding="utf-8"))

if not re.search(r"^name:\s*create-tasks\s*$", canonical_text, re.MULTILINE):
    raise SystemExit("FAIL: canonical skill frontmatter is not create-tasks")
if "# /curator:create-tasks" not in canonical_text:
    raise SystemExit("FAIL: canonical skill heading is stale")
if not re.search(r"^name:\s*run\s*$", compat_text, re.MULTILINE):
    raise SystemExit("FAIL: compatibility skill frontmatter is not run")
if "/curator:create-tasks" not in compat_text:
    raise SystemExit("FAIL: run compatibility skill does not delegate")
if "scripts/start_with_" in compat_text or "swegen create" in compat_text:
    raise SystemExit("FAIL: run compatibility skill duplicates implementation")
if "/curator:create-tasks" not in manifest_data.get("description", ""):
    raise SystemExit("FAIL: plugin manifest omits canonical command")

print("PASS: curator create-tasks command surface")
PY
