---
name: run
description: >
  Launch the terminalgen pipeline via `scripts/start.sh` after preflight
  passes: StackOverflow scrape + domain bucketing → LLM-driven task
  generation per domain (terminal-lego generator) → Docker round-trip
  verification → append to `verifiable_tasks.txt` → convert verified tasks
  to harbor 1.1 in `artifacts/merged_terminal_tasks/`. Per-domain launches
  use `bash scripts/create_domain.sh <domain>` (tuned gen_workers,
  val_workers, val_timeout). Long-running (minutes-to-hours per domain).
  For a first-time end-to-end smoke before committing to a full run, this
  skill replays the known-good `https-nginx-cert-setup` fixture to confirm
  the Docker validator works, then can drive a small single-domain flow.
  Stamps live state into `artifacts/index.yaml` via `scripts/archive_run.sh`.
  Triggers on phrases like "run terminalgen", "launch terminalgen",
  "generate terminal tasks", "start the terminalgen pipeline",
  "smoke-test terminalgen".
---

# /terminalgen:run

Preflight and launch terminal task generation. terminalgen is a leaf block:
this skill runs commands inside `subblock/terminalgen/` and does not dispatch to
child blocks.

## Step 0 - Orient

Validate:

1. Current directory is `subblock/terminalgen/`.
2. `config.yaml` has `meta_info.name == "terminalgen"`.
3. `scripts/start.sh`, `scripts/scrape_so_questions.sh`, and
   `scripts/create_domain.sh` exist.
4. `repos/terminal-lego/` is initialized at the pinned commit.

Read `config.yaml`, `CLAUDE.md`, and `memory/quick-verify.md` before choosing a
mode.

## Step 1 - Refuse duplicate live runs

Before launching, check for existing terminalgen work owned by this block. Scope
the match to the current block path so older jobs in a separate checkout such as
`$HOME/terminal-lego` do not block this run:

```bash
BLOCK_DIR="$(pwd -P)"
pgrep -af 'task_generator.py|validate_tasks.py|scripts/create_domain|scripts/start.sh' \
  | grep -F "$BLOCK_DIR" || true
```

If a run is alive, refuse to start another. Report the PID, elapsed time, and
likely log path under `artifacts/logs/terminalgen-create/`. Tell the user to let
it finish or stop it before retrying.

## Step 2 - Choose the run mode

Resolve the user's natural-language request into one mode:

| Mode | Use when | Command shape |
| --- | --- | --- |
| `smoke` | First run, "quick verify", "smoke", "one task". | `bash tests/smoke/verify.sh` — replays the `https-nginx-cert-setup` fixture through the Docker validator (no LLM, no SO calls, deterministic). Confirms build→solve→test→reward=1 works on this host. |
| `single-domain` | The user names one domain (e.g. `security-cryptography`). | `bash scripts/create_domain.sh <domain> [limit] [start]` after confirming tuned params from `scripts/read_params.py` and that the domain's question bucket exists. |
| `batch` | The user wants N verified tasks per domain on a budget. | `CHUNK=6 CAND_CAP=24 bash scripts/batch_verify.sh <target> [domain ...]` — generates in chunks, stops each domain at the target or candidate cap. Cost-controlled; no run archiving. |
| `full` | The user says all domains, pipeline, or gives no narrower scope. | `bash scripts/start.sh`, which scrapes (if pool low), runs `scripts/create_all_bg.sh`, and archives on exit. |

If the request implies config changes, such as "more workers" or "longer
timeout", show the exact proposed config/env override and wait for confirmation
before changing or launching.

## Step 3 - Preflight

Run `/terminalgen:check` logic first. Abort on any blocking failure:

- config/submodule pin failed
- LLM completion ping failed (for `single-domain`/`full`)
- Docker unavailable
- `scripts/dryrun.sh` failed
- requested smoke validation failed

The LLM completion ping is mandatory before generation; never proceed to the
generator after only `scripts/dryrun.sh`. A missing `SO_API_KEY` is a warning,
not a blocker (pipeline runs at 300/day). If the LLM ping returns `401 Invalid
token`, ask for a replacement API key, export it only in the current shell, and
rerun preflight before launch. Do not edit inputs to make preflight pass.

## Step 4 - Show run configuration and confirm

Print a compact summary and ask for explicit confirmation:

```text
terminalgen run configuration
  mode             : <smoke|single-domain|full>
  domains          : <list>
  scrape round      : <N or n/a for smoke>
  input questions   : <path>
  output tasks      : <path>
  gen_workers       : <per-domain>
  val_workers       : <per-domain>
  val_timeout       : <per-domain>
  validation        : Docker round-trip (build → solve → test → reward)
  conversion        : extract_verified_tasks.py → harbor 1.1
  logs              : artifacts/logs/terminalgen-create/
  archive           : scripts/archive_run.sh -> artifacts/index.yaml
```

Never launch a full or single-domain run without an explicit "yes". Smoke can
run in the foreground after confirmation; long runs default to background.

## Step 5 - Launch

### Smoke

Replay the known-good fixture through the real Docker validator:

```bash
bash tests/smoke/verify.sh
```

Success means the fixture task validates with reward=1, confirming Docker
build/solve/test works on this host. This is deterministic and spends no LLM or
SO quota — the right first check before a real generation run.

### Single domain

Ensure the question bucket exists (scrape first if absent), then run the domain
script so it honors `config.yaml` via `scripts/read_params.py`:

```bash
# scrape + bucket if artifacts/collected_questions/<domain>_so_data.json is missing:
bash scripts/scrape_so_questions.sh <round> 200
bash scripts/create_domain.sh <domain>
```

The script generates candidates, Docker-validates them, appends verified task
ids to `artifacts/terminal_tasks/<domain>-tl/verifiable_tasks.txt`, and logs
under `artifacts/logs/terminalgen-create/`. Follow with
`python scripts/extract_verified_tasks.py` to materialize harbor 1.1 output.

### Full run

Use:

```bash
bash scripts/start.sh
```

`start.sh` scrapes when the pool is low, launches all enabled domains via
`scripts/create_all_bg.sh`, and installs an EXIT trap that invokes
`scripts/archive_run.sh`. Do not pre-write entries to `artifacts/index.yaml`;
the scripts own run archiving.

## Step 6 - Report and monitor

After launch, print:

- command or background PID
- log path
- output path
- how to stop or attach
- next `/terminalgen:dashboard` command

For foreground smoke, report the resulting reward or the failing phase. For
background runs, poll once after a short delay to ensure the process started,
then hand off to `/terminalgen:dashboard`.

## Guardrails

- Do not run multiple terminalgen generation jobs concurrently in the same block.
- Do not write secrets to repository files.
- **Never modify `repos/terminal-lego/`** while running data generation.
- Do not delete generated tasks, candidate dirs, or logs unless the user asks
  for cleanup.
