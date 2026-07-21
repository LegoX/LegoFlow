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
    block / "CLAUDE.md",
    block / ".claude/plugins/curator-plugin/README.md",
    block / ".claude/plugins/curator-plugin/skills/check/SKILL.md",
    block / ".claude/plugins/curator-plugin/skills/collect-prs/SKILL.md",
    block / ".claude/plugins/curator-plugin/skills/dashboard/SKILL.md",
    block / ".claude/plugins/curator-plugin/skills/setup/SKILL.md",
    block / "memory/quick-verify.md",
    block / "tests/smoke/verify.sh",
]
files.extend(sorted((block / "docs/content/docs").glob("*.mdx")))

stale = []
for path in files:
    if "/curator:run" in path.read_text(encoding="utf-8"):
        stale.append(str(path.relative_to(root)))
if stale:
    raise SystemExit("FAIL: stale public /curator:run references: " + ", ".join(stale))

deploy = (block / "docs/deploy_cloudflare_pages.sh").read_text(encoding="utf-8")
readme = (block / "docs/README.md").read_text(encoding="utf-8")
if 'PROJECT_NAME="${PROJECT_NAME:-swe-swegen-docs}"' not in deploy:
    raise SystemExit("FAIL: deploy target is not swe-swegen-docs")
if "https://swe-swegen-docs.pages.dev" not in readme:
    raise SystemExit("FAIL: docs README does not name the production site")

print("PASS: curator create-tasks documentation")
PY
