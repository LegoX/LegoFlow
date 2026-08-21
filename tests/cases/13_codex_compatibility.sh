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

claude_plugin_dirs = {
    "root": root / ".claude/plugins/root-plugin",
    "curator": root / "blocks/curator/.claude/plugins/curator-plugin",
    "tracer": root / "blocks/tracer/.claude/plugins/tracer-plugin",
    "trainer": root / "blocks/trainer/.claude/plugins/trainer-plugin",
    "evaluator": root / "blocks/evaluator/.claude/plugins/evaluator-plugin",
}

for name, plugin in plugins.items():
    manifest_path = plugin / ".codex-plugin/plugin.json"
    if not manifest_path.is_file():
        raise SystemExit(f"FAIL: missing Codex manifest for {name}: {manifest_path.relative_to(root)}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("name") != name:
        raise SystemExit(f"FAIL: unexpected Codex plugin name for {name}")
    if manifest.get("author", {}).get("name") != "LegoX":
        raise SystemExit(f"FAIL: Codex plugin author is not LegoX for {name}")
    skills_dir = plugin / "skills"
    skills = list(skills_dir.glob("*/SKILL.md"))
    if not skills:
        raise SystemExit(f"FAIL: Codex plugin has no skills for {name}")
    claude_manifest = json.loads(
        (claude_plugin_dirs[name] / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    if manifest.get("name") != claude_manifest.get("name"):
        raise SystemExit(f"FAIL: Claude/Codex plugin names differ for {name}")
    claude_skills = {path.parent.name for path in claude_plugin_dirs[name].glob("skills/*/SKILL.md")}
    codex_skills = {path.parent.name for path in skills}
    if claude_skills != codex_skills:
        raise SystemExit(f"FAIL: Claude/Codex skill names differ for {name}")
    for skill in skills:
        text = skill.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "description:" not in text:
            raise SystemExit(f"FAIL: malformed Codex skill: {skill.relative_to(root)}")
        if "./bin/legoflow" not in text:
            raise SystemExit(f"FAIL: Codex skill does not reference shared CLI: {skill.relative_to(root)}")
        command = f"/{name}:{skill.parent.name}"
        if command not in text:
            raise SystemExit(f"FAIL: Codex skill does not document its slash command: {command}")

marketplace = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
names = {entry["name"] for entry in marketplace["plugins"]}
expected = set(plugins)
if names != expected:
    raise SystemExit(f"FAIL: marketplace entries {sorted(names)} != {sorted(expected)}")

for path in [root / "AGENTS.md", *sorted((root / "blocks").glob("*/AGENTS.md"))]:
    if "LegoX" not in path.read_text(encoding="utf-8"):
        raise SystemExit(f"FAIL: missing LegoX ownership in {path.relative_to(root)}")

print("PASS: Codex plugins, marketplace, and AGENTS.md contracts")
PY
