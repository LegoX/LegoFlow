#!/usr/bin/env bash
# Root case 13: every runnable block exposes a valid Codex plugin and agent
# contract alongside its existing Claude Code integration.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
plugins = {
    "root": root / ".codex/plugins/root-plugin",
    "curator": root / ".codex/plugins/curator-plugin",
    "tracer": root / ".codex/plugins/tracer-plugin",
    "trainer": root / ".codex/plugins/trainer-plugin",
    "evaluator": root / ".codex/plugins/evaluator-plugin",
}

for name, plugin in plugins.items():
    manifest_path = plugin / ".codex-plugin/plugin.json"
    if not manifest_path.is_file():
        raise SystemExit(f"FAIL: missing Codex manifest for {name}: {manifest_path.relative_to(root)}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("name") != f"legoflow-{name}":
        raise SystemExit(f"FAIL: unexpected Codex plugin name for {name}")
    if manifest.get("author", {}).get("name") != "LegoX":
        raise SystemExit(f"FAIL: Codex plugin author is not LegoX for {name}")
    skills_dir = plugin / "skills"
    skills = list(skills_dir.glob("*/SKILL.md"))
    if not skills:
        raise SystemExit(f"FAIL: Codex plugin has no skills for {name}")
    for skill in skills:
        text = skill.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "description:" not in text:
            raise SystemExit(f"FAIL: malformed Codex skill: {skill.relative_to(root)}")

marketplace = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
names = {entry["name"] for entry in marketplace["plugins"]}
expected = {f"legoflow-{name}" for name in plugins}
if names != expected:
    raise SystemExit(f"FAIL: marketplace entries {sorted(names)} != {sorted(expected)}")

for path in [root / "AGENTS.md", *sorted((root / "blocks").glob("*/AGENTS.md"))]:
    if "LegoX" not in path.read_text(encoding="utf-8"):
        raise SystemExit(f"FAIL: missing LegoX ownership in {path.relative_to(root)}")

print("PASS: Codex plugins, marketplace, and AGENTS.md contracts")
PY
