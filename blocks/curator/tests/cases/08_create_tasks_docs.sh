#!/usr/bin/env bash
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(cd "$BLOCK_DIR/../.." && pwd)"

python3 - "$BLOCK_DIR" "$REPO_ROOT" <<'PY'
import sys
from pathlib import Path

block = Path(sys.argv[1])
root = Path(sys.argv[2])

files = [
    root / "README.md",
    block / "CLAUDE.md",
    block / ".claude/plugins/curator-plugin/README.md",
    block / ".claude/plugins/curator-plugin/skills/check/SKILL.md",
    block / ".claude/plugins/curator-plugin/skills/collect-prs/SKILL.md",
    block / ".claude/plugins/curator-plugin/skills/dashboard/SKILL.md",
    block / ".claude/plugins/curator-plugin/skills/setup/SKILL.md",
    block / "memory/quick-verify.md",
    block / "tests/smoke/verify.sh",
]
curator_docs_pages = sorted((block / "docs/content/docs").glob("*.mdx"))
root_docs_pages = sorted((root / "docs/content/docs").glob("*.mdx"))
# Curator's own docs site is exempt from the /curator:run ban: it documents the
# full plugin surface, adapter included, and describes /curator:run as a
# compatibility entry point rather than recommending it. The ban still holds
# everywhere the command would read as the way to operate Curator — the root
# README and docs, CLAUDE.md, the plugin README/skills, memory, and smoke.
files.extend(root_docs_pages)

stale = []
for path in files:
    if "/curator:run" in path.read_text(encoding="utf-8"):
        stale.append(str(path.relative_to(root)))
if stale:
    raise SystemExit("FAIL: stale public /curator:run references: " + ", ".join(stale))

example_usage = (root / "docs/content/docs/example-usages.mdx").read_text(
    encoding="utf-8"
)
example_words = " ".join(example_usage.split())
if "directly executes Curator's all-language `scripts/start.sh`" not in example_words:
    raise SystemExit("FAIL: root docs do not describe direct Curator start.sh execution")
if "/curator:create-tasks" not in example_usage:
    raise SystemExit("FAIL: root docs omit the canonical direct Curator command")

stale_pages_project = []
for path in curator_docs_pages:
    if "swe-curator-docs" in path.read_text(encoding="utf-8"):
        stale_pages_project.append(str(path.relative_to(root)))
if stale_pages_project:
    raise SystemExit(
        "FAIL: stale Curator docs Pages project references: "
        + ", ".join(stale_pages_project)
    )

deploy = (block / "docs/deploy_cloudflare_pages.sh").read_text(encoding="utf-8")
readme = (block / "docs/README.md").read_text(encoding="utf-8")
if 'PROJECT_NAME="${PROJECT_NAME:-swe-swegen-docs}"' not in deploy:
    raise SystemExit("FAIL: deploy target is not swe-swegen-docs")
if "https://swe-swegen-docs.pages.dev" not in readme:
    raise SystemExit("FAIL: docs README does not name the production site")

print("PASS: curator create-tasks documentation")
PY
