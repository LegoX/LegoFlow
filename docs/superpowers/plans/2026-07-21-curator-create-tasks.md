# Curator `create-tasks` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/curator:create-tasks` the canonical task-generation command, retain a thin `/curator:run` root-protocol adapter, and publish the updated Curator docs at `swe-swegen-docs.pages.dev`.

**Architecture:** The existing full run skill moves unchanged in behavior to a new `create-tasks` skill. A minimal `run` skill remains only to preserve the uniform `/<block>:run` contract used by `/root:run`; it delegates all arguments and decisions to the canonical skill. Maintained Fumadocs source stays under `subblock/curator/docs`, whose deploy target changes to the existing `swe-swegen-docs` Pages project.

**Tech Stack:** Claude Code plugin skills (Markdown frontmatter), Bash contract tests, Fumadocs/Next.js, Cloudflare Pages/Wrangler, GitHub CLI.

## Global Constraints

- The current namespace is `/curator:*`; do not restore `subblock/swegen` or `/swegen:*`.
- `/curator:create-tasks` is the only user-facing task-generation command.
- `/curator:run` remains only as a compatibility adapter for `/root:run`.
- Do not change task-generation scripts, PR collection behavior, or artifact formats.
- Keep all documentation in English and make only command/documentation/deployment changes required by the design.
- The prior PR #60 is merged; delivery uses the same `swegen` branch in a new follow-up PR to `dev`.
- Deploy production docs only after the follow-up PR is merged.

---

### Task 1: Add the canonical command and compatibility adapter

**Files:**
- Create: `subblock/curator/tests/cases/07_create_tasks_skill.sh`
- Move: `subblock/curator/.claude/plugins/curator-plugin/skills/run/SKILL.md` → `subblock/curator/.claude/plugins/curator-plugin/skills/create-tasks/SKILL.md`
- Create: `subblock/curator/.claude/plugins/curator-plugin/skills/run/SKILL.md`
- Modify: `subblock/curator/.claude/plugins/curator-plugin/.claude-plugin/plugin.json`
- Modify: `subblock/curator/.claude/plugins/.claude-plugin/marketplace.json`
- Modify: `subblock/curator/.claude/plugins/curator-plugin/README.md`

**Interfaces:**
- Consumes: root dispatch through `/curator:run`, with arbitrary natural-language arguments.
- Produces: canonical `/curator:create-tasks`; compatibility `/curator:run` forwards the request without its own workflow.

- [ ] **Step 1: Write the failing command-surface test**

Create `subblock/curator/tests/cases/07_create_tasks_skill.sh`:

```bash
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
```

- [ ] **Step 2: Run the test and verify the missing command failure**

Run:

```bash
bash subblock/curator/tests/cases/07_create_tasks_skill.sh
```

Expected: exit `1` with `FAIL: missing .../skills/create-tasks/SKILL.md`.

- [ ] **Step 3: Move the full workflow to `create-tasks`**

Move the directory:

```bash
mv subblock/curator/.claude/plugins/curator-plugin/skills/run \
   subblock/curator/.claude/plugins/curator-plugin/skills/create-tasks
```

In the moved `SKILL.md`, make these exact interface changes while preserving the
rest of the workflow:

```diff
-name: run
+name: create-tasks

-# /curator:run
+# /curator:create-tasks
```

Replace every self-reference and task-generation trigger in that file from
`/curator:run` to `/curator:create-tasks`; do not change `/curator:collect-prs`
or any shell command. Use task-specific triggers such as:

```text
"create Curator tasks", "generate Curator SWE tasks",
"start Curator task generation", "smoke-test Curator task creation"
```

- [ ] **Step 4: Add the complete compatibility skill**

Create `subblock/curator/.claude/plugins/curator-plugin/skills/run/SKILL.md`:

```markdown
---
name: run
description: >
  Compatibility entry point for the root block's uniform `/<block>:run`
  protocol. Forward the user's complete request to `/curator:create-tasks`
  without changing arguments or duplicating its workflow.
---

# /curator:run (compatibility)

This command exists only so `/root:run` can dispatch Curator through the
repository-wide `/<block>:run` interface.

Immediately invoke `/curator:create-tasks` with the user's complete request and
arguments. Do not repeat preflight, confirmation, mode selection, launch, or
reporting logic here; the canonical skill owns all behavior.

For direct Curator operation, tell users to invoke `/curator:create-tasks`.
```

- [ ] **Step 5: Update plugin command metadata**

Make the manifest description enumerate `/curator:create-tasks` as the public
generation command and describe `/curator:run` as root compatibility:

```json
"description": "curator block skills — env/venv bootstrap, preflight, separate PR collection, task creation + verification, dashboard, and a root-protocol run adapter. Provides /curator:setup, /curator:check, /curator:collect-prs, /curator:create-tasks, /curator:dashboard; /curator:run is compatibility-only."
```

Change the marketplace description to:

```json
"description": "curator block skills — setup, check, collect-prs, create-tasks, dashboard; run is a root compatibility adapter."
```

Update the plugin README command table to list `create-tasks` as the generation
workflow and omit `run` from the public command table. In the layout, label
`run/SKILL.md` as the root compatibility adapter without presenting it as a
direct user command.

- [ ] **Step 6: Run command-surface and root-contract tests**

Run:

```bash
bash subblock/curator/tests/cases/07_create_tasks_skill.sh
bash tests/cases/02_uniform_scripts.sh
```

Expected:

```text
PASS: curator create-tasks command surface
PASS: uniform script contract satisfied (root + 5 subblocks)
```

- [ ] **Step 7: Commit the command surface**

```bash
git add subblock/curator/.claude/plugins subblock/curator/tests/cases/07_create_tasks_skill.sh
git commit -m "feat(curator): add explicit create-tasks command"
```

### Task 2: Migrate operational and web documentation

**Files:**
- Create: `subblock/curator/tests/cases/08_create_tasks_docs.sh`
- Modify: `subblock/curator/CLAUDE.md`
- Modify: `subblock/curator/.claude/plugins/curator-plugin/skills/{check,collect-prs,dashboard,setup}/SKILL.md`
- Modify: `subblock/curator/memory/quick-verify.md`
- Modify: `subblock/curator/tests/README.md`
- Modify: `subblock/curator/tests/smoke/verify.sh`
- Modify: `subblock/curator/docs/content/docs/getting-started.mdx`
- Modify: `subblock/curator/docs/content/docs/run-generation.mdx`
- Modify: `subblock/curator/docs/README.md`
- Modify: `subblock/curator/docs/deploy_cloudflare_pages.sh`
- Modify: `docs/content/docs/example-usages.mdx`

**Interfaces:**
- Consumes: canonical command and compatibility adapter from Task 1.
- Produces: English user guidance that recommends only `/curator:create-tasks`;
  deployment defaults for the `swe-swegen-docs` Pages project.

- [ ] **Step 1: Write the failing documentation contract test**

Create `subblock/curator/tests/cases/08_create_tasks_docs.sh`:

```bash
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
```

- [ ] **Step 2: Run the test and verify stale references are detected**

Run:

```bash
bash subblock/curator/tests/cases/08_create_tasks_docs.sh
```

Expected: exit `1`, listing current files that still contain `/curator:run`.

- [ ] **Step 3: Replace public command references**

In every file listed above, replace user-facing `/curator:run` references with
`/curator:create-tasks`. Preserve `/root:run` unchanged. Use these canonical
phrases:

```text
/curator:collect-prs    # collect PR IDs and wait for completion
/curator:create-tasks   # generate and verify tasks from existing PR IDs
```

Update check/setup/dashboard hand-offs and smoke wording to name
`create-tasks`. In `docs/content/docs/example-usages.mdx`, explain that
`/root:run curator` reaches the internal `/curator:run` compatibility adapter,
while direct users should choose `/curator:create-tasks`.

- [ ] **Step 4: Point the maintained web source at the existing Pages project**

In `subblock/curator/docs/deploy_cloudflare_pages.sh`, change:

```diff
-# (swe-databoard); this one defaults to swe-curator-docs.
+# (swe-databoard); this one publishes at swe-swegen-docs.

-PROJECT_NAME="${PROJECT_NAME:-swe-curator-docs}"
+PROJECT_NAME="${PROJECT_NAME:-swe-swegen-docs}"
```

In `subblock/curator/docs/README.md`, change the project name, live URL, and
`PROJECT_NAME` default to `swe-swegen-docs` /
`https://swe-swegen-docs.pages.dev`.

- [ ] **Step 5: Update test documentation**

Add cases 07 and 08 to `subblock/curator/tests/README.md`, describing them as
static command-surface and public-documentation checks. Do not change runtime
or smoke expectations.

- [ ] **Step 6: Run documentation contracts**

Run:

```bash
bash subblock/curator/tests/cases/07_create_tasks_skill.sh
bash subblock/curator/tests/cases/08_create_tasks_docs.sh
```

Expected:

```text
PASS: curator create-tasks command surface
PASS: curator create-tasks documentation
```

- [ ] **Step 7: Build both documentation sites**

Run:

```bash
(cd docs && npm ci && npm run build)
(cd subblock/curator/docs && npm ci && npm run build)
```

Expected: both Next.js builds exit `0`, and Curator generates the
`/docs/getting-started` and `/docs/run-generation` pages.

- [ ] **Step 8: Commit the documentation migration**

```bash
git add CLAUDE.md README.md docs subblock/curator tests
git commit -m "docs(curator): make create-tasks the public workflow"
```

### Task 3: Verify the complete branch

**Files:**
- Verify only; modify scoped files only if a test or review finds a real defect.

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: a clean, reviewed branch ready for a follow-up PR.

- [ ] **Step 1: Scan for stale command references**

Run:

```bash
rg -n '/curator:run' --glob '*.{md,mdx,json,yaml,yml,sh,py}'
```

Expected: matches only in:

```text
docs/superpowers/specs/2026-07-20-curator-create-tasks-design.md
docs/superpowers/plans/2026-07-21-curator-create-tasks.md
subblock/curator/.claude/plugins/curator-plugin/.claude-plugin/plugin.json
subblock/curator/.claude/plugins/curator-plugin/skills/run/SKILL.md
docs/content/docs/example-usages.mdx
```

Inspect each match to confirm it explicitly describes compatibility rather than
recommending the old command.

- [ ] **Step 2: Run static and syntax verification**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 bash tests/run.sh
bash subblock/curator/tests/cases/01_config_schema.sh
bash -n subblock/curator/docs/deploy_cloudflare_pages.sh
git diff --check
python - <<'PY'
import json
from pathlib import Path
for path in [
    Path("subblock/curator/.claude/plugins/curator-plugin/.claude-plugin/plugin.json"),
    Path("subblock/curator/.claude/plugins/.claude-plugin/marketplace.json"),
]:
    json.loads(path.read_text())
print("plugin metadata: valid")
PY
```

Expected: root suite has `FAIL=0`; Curator schema prints `PASS`; shell/metadata
checks and `git diff --check` exit `0`.

- [ ] **Step 3: Run the full Curator cheap test suite where credentials permit**

Run:

```bash
bash subblock/curator/tests/run.sh
```

Expected: cases 07 and 08 pass. If repo/venv/token/LLM checks fail for missing
runtime state, record them as pre-existing environment blockers; do not weaken
the tests.

- [ ] **Step 4: Request focused review**

Ask a reviewer to compare `origin/dev...HEAD` against the design spec, with
special attention to root compatibility, stale public command references, and
the Pages project target. Resolve every valid blocking or important finding.

- [ ] **Step 5: Confirm a clean branch**

Run:

```bash
git status --short --branch
git log --oneline origin/dev..HEAD
git diff --stat origin/dev...HEAD
```

Expected: no uncommitted files; commits include the design, command surface,
and documentation migration.

### Task 4: Create, validate, and merge the follow-up PR

**Files:**
- Git/GitHub state only.

**Interfaces:**
- Consumes: verified `swegen` branch.
- Produces: merged follow-up PR in `dev`.

- [ ] **Step 1: Refresh the base and verify ancestry**

Run:

```bash
git fetch --no-tags --recurse-submodules=no origin dev swegen
git merge-base --is-ancestor origin/dev HEAD
```

Expected: exit `0`. If `dev` advanced, merge or rebase it without rewriting
already-pushed history unless explicitly approved.

- [ ] **Step 2: Push the same remote branch**

Run:

```bash
git push -u origin swegen
```

Expected: remote `swegen` advances by fast-forward.

- [ ] **Step 3: Create a new PR to `dev`**

Create a follow-up PR titled:

```text
feat(curator): add explicit create-tasks command
```

The body must summarize the canonical command, compatibility adapter, website
source migration, and local verification. Use:

```bash
PR_URL="$(gh pr create --base dev --head swegen \
  --title "feat(curator): add explicit create-tasks command" \
  --body "$(cat <<'EOF'
## Summary
- add `/curator:create-tasks` as the canonical generation command
- retain a thin `/curator:run` adapter for root orchestration
- publish maintained Curator docs through the `swe-swegen-docs` project

## Test plan
- [ ] root and Curator static contract suites
- [ ] root and Curator documentation builds
- [ ] plugin metadata and shell syntax validation
EOF
)")"
printf '%s\n' "$PR_URL"
```

Return the new PR URL; do not claim that it is PR #60.

- [ ] **Step 4: Watch CI and review feedback**

Use:

```bash
PR_NUMBER="$(gh pr list --head swegen --base dev --state open \
  --json number --jq '.[0].number')"
gh pr checks "$PR_NUMBER" --watch --interval 10
```

Expected: required checks pass. Triage comments and fix only issues caused by
this PR.

- [ ] **Step 5: Merge and verify**

Run:

```bash
PR_NUMBER="$(gh pr list --head swegen --base dev --state open \
  --json number --jq '.[0].number')"
gh pr merge "$PR_NUMBER" --merge
git fetch --no-tags --recurse-submodules=no origin dev
git merge-base --is-ancestor HEAD origin/dev
```

Expected: PR state is `MERGED`, and the implementation commit is an ancestor of
`origin/dev`.

### Task 5: Deploy and verify the production documentation

**Files:**
- External Cloudflare Pages state only.

**Interfaces:**
- Consumes: merged Curator docs and credentials from
  `~/.config/swegen_docs_cloudflare.env` or exported Cloudflare variables.
- Produces: updated <https://swe-swegen-docs.pages.dev/>.

- [ ] **Step 1: Deploy the maintained Curator docs**

Run after merge:

```bash
bash subblock/curator/docs/deploy_cloudflare_pages.sh
```

Expected: build succeeds and Wrangler reports a successful deployment to
project `swe-swegen-docs` on branch `swegen`.

- [ ] **Step 2: Verify the live command documentation**

Run:

```bash
python3 - <<'PY'
from urllib.request import urlopen

url = "https://swe-swegen-docs.pages.dev/docs/run-generation/"
body = urlopen(url, timeout=30).read().decode("utf-8", errors="replace")
assert "/curator:create-tasks" in body, "canonical command missing from deployed site"
assert "/curator:run" not in body, "legacy public command still present"
print("production docs: create-tasks visible")
PY
```

Expected:

```text
production docs: create-tasks visible
```

- [ ] **Step 3: Report delivery**

Report the follow-up PR URL, merge commit, Pages deployment URL, live
verification result, and any unrelated environment-only test failures.
