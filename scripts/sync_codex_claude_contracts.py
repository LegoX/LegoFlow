#!/usr/bin/env python3
"""Generate Codex entry skills and AGENTS.md references from Claude files."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BLOCKS = ("curator", "tracer", "trainer", "evaluator")


def write_codex_skill(codex_path: Path, claude_path: Path, description: str) -> None:
    relative_claude = claude_path.relative_to(ROOT).as_posix()
    codex_path.parent.mkdir(parents=True, exist_ok=True)
    codex_path.write_text(
        "---\n"
        f"name: {codex_path.parent.name}\n"
        f"description: {description}\n"
        "---\n\n"
        "# Canonical LegoFlow Skill\n\n"
        f"Read and follow `{relative_claude}` in full. It is the canonical "
        "workflow shared by Claude Code and Codex; do not duplicate or "
        "reinterpret its safety gates, reporting requirements, or runtime "
        "procedure here.\n\n"
        "## Native Codex Invocation\n\n"
        f"Invoke this Codex skill as `${codex_path.parent.parent.parent.name}-{codex_path.parent.name}`.\n",
        encoding="utf-8",
    )


def main() -> None:
    roots = [("root", ROOT / ".claude/plugins/root-plugin", ROOT / "plugins/root")]
    roots.extend(
        (
            block,
            ROOT / f"blocks/{block}/.claude/plugins/{block}-plugin",
            ROOT / f"plugins/{block}",
        )
        for block in BLOCKS
    )

    for name, claude_plugin, codex_plugin in roots:
        for claude_skill in sorted((claude_plugin / "skills").glob("*/SKILL.md")):
            skill_name = claude_skill.parent.name
            description = f"Run the canonical {name} {skill_name} workflow."
            write_codex_skill(
                codex_plugin / "skills" / skill_name / "SKILL.md",
                claude_skill,
                description,
            )

    (ROOT / "AGENTS.md").write_text(
        "# Agent Instructions\n\n"
        "Read and follow `CLAUDE.md` in this repository. It is the single "
        "canonical instruction source for Claude Code and Codex.\n",
        encoding="utf-8",
    )
    for block in BLOCKS:
        (ROOT / f"blocks/{block}/AGENTS.md").write_text(
            f"# {block.title()} Agent Instructions\n\n"
            f"Read and follow `blocks/{block}/CLAUDE.md` in full. It is the "
            "single canonical instruction source for Claude Code and Codex "
            f"when operating the {block} block.\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
