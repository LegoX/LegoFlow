# SWE-Lego-Live

A self-evolving LLM development pipeline. It generates coding-agent training data from real GitHub PRs, runs agent trajectories, and feeds the results into SFT and RL training — all coordinated by an AI agent that monitors progress and tunes parameters automatically.

## Pipeline Architecture

```
GitHub PRs
    │
    ▼
┌─────────┐     verified SWE tasks      ┌──────────┐     raw trajectories     ┌─────┐     ┌────┐
│ swegen  │ ─────────────────────────► │ trajgen  │ ──────────────────────► │ sft │ ──► │ rl │
│         │   artifacts/swe_tasks/      │          │   artifacts/jobs/        │     │     │    │
└─────────┘                            └──────────┘                          └─────┘     └────┘
  remote node                           remote node                         (planned)  (planned)
  192.168.35.240                        192.168.35.240
```

**swegen** converts GitHub PRs into verified SWE-Bench tasks (Docker-validated, with bug patch + fix patch + test script). **trajgen** runs a Claude Code agent on each task via Harbor and records the full LiteLLM trajectory. sft and rl are planned for a future phase.

## Quick Start

### Prerequisites

- SSH access to the remote node (`192.168.35.240`) as `root`
- Docker available on the remote node
- Python 3.10+ locally (for config reading in scripts)
- A GitHub token with `repo` read scope
- An OpenAI-compatible LLM API endpoint

### 1. Clone

```bash
git clone --recurse-submodules <repo-url> SWE-Lego-Live
cd SWE-Lego-Live
```

If you already cloned without `--recurse-submodules`, run `git submodule update --init --recursive`.

### 2. Configure

Edit `subblock/swegen/config.yaml` and `subblock/trajgen/config.yaml`. Fill in the `runtime_info.input` section of each:

**swegen** (`subblock/swegen/config.yaml`):
```yaml
runtime_info:
  input:
    github_tokens: ghp_YOUR_TOKEN_HERE
    llm_api:
      api_key: YOUR_API_KEY
      api_base_url: https://your-llm-endpoint/v1
      pr_model: openai/your-model
      task_model: openai/your-model
```

**trajgen** (`subblock/trajgen/config.yaml`):
```yaml
runtime_info:
  input:
    llm_api:
      api_key: YOUR_API_KEY
      api_base_url: https://your-llm-endpoint/v1
      model: openai/your-model
```

### 3. Validate

```bash
bash scripts/dryrun.sh
```

This checks local config, SSH reachability, and required fields. Pass `--full` to also run each subblock's own dryrun remotely.

### 4. Start

```bash
bash scripts/start.sh
```

This syncs code to the remote node and launches swegen and trajgen in named tmux sessions. Use `--swegen-only` or `--trajgen-only` to start one at a time.

### 5. Monitor

```bash
ssh root@192.168.35.240
tmux ls                        # list sessions
tmux attach -t swegen-py       # watch swegen
tmux attach -t trajgen         # watch trajgen
```

## Hand Off To An AI Agent

Once steps 1–2 above (clone + configure) are done, you can delegate the actual operating to an AI agent (Claude Code, Codex, etc.). Paste the prompt below into the agent's system prompt or first user turn — it tells the agent everything it needs to know to run this repo without prior context.

> You are the operator agent for the SWE-Lego-Live pipeline. Your job is to drive the two active subblocks (swegen → trajgen) on the configured CPU node, keep them healthy, and surface status to me.
>
> ### Read first (in this order)
> 1. `CLAUDE.md` — root block contract and producer→consumer manifest rule
> 2. `BLOCK_DEFINITION.md` — block system specification
> 3. `subblock/swegen/CLAUDE.md` + `subblock/swegen/config.yaml` — producer
> 4. `subblock/trajgen/CLAUDE.md` + `subblock/trajgen/config.yaml` — consumer
> 5. `subblock/trajgen/artifacts/consumption_ledger.yaml` — what trajgen has already processed
>
> ### Hard constraints
> - All execution happens on `meta_info.resources.ip` (currently `192.168.35.240`). If you are not on that host, SSH in and operate inside named tmux sessions. If you are already on it, create local tmux sessions — never run these scripts in a one-shot foreground process.
> - `subblock/swegen/artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt` is the **only** manifest trajgen is allowed to consume. The directory next to it contains hundreds of partially-built skeletons from failed CC sessions; never have trajgen run those. `scripts/prepare_tasks.sh` filters by this manifest when it sees it — do not bypass it.
> - Tasks already in `consumption_ledger.yaml` with status `done`, `failed`, or `skipped` must appear in `subblock/trajgen/config.yaml` → `environment.extra.HARBOR_EXCLUDE_TASKS` before any trajgen restart, so Harbor doesn't burn cycles re-running them.
>
> ### Bring-up
> 1. Confirm `meta_info.resources.ip` matches the host you're on (`hostname -I`); SSH there if not.
> 2. `bash scripts/dryrun.sh` — must pass before you start.
> 3. Launch swegen in tmux: `tmux new-session -d -s swegen-py -x 220 -y 50` then `tmux send-keys -t swegen-py "cd subblock/swegen && bash scripts/create_py.sh" Enter`.
> 4. For trajgen: ensure base Python has PyYAML (`pip install pyyaml`); confirm `subblock/trajgen/repos/harbor/.git` exists (submodule gitlink is fine — `update_repos.sh` errors on gitlinks, that's safe to skip if dryrun confirms the pin matches). Then `tmux new-session -d -s trajgen -x 220 -y 50` and `tmux send-keys -t trajgen "cd subblock/trajgen && bash scripts/start.sh" Enter`. Set `TRAJGEN_PREPARE_TASKS=1` first if you need to resync new swegen-verified tasks.
> 5. Verify the LiteLLM proxy is bound: `ss -ntlp | grep 4001`. Verify swegen is producing skeletons (tmux pane scrolls).
>
> ### Routine cycle (every 30 min)
> - Snapshot: `wc -l subblock/swegen/artifacts/swe_tasks/py-cc/verifiable_tasks.txt`, swegen-py tmux pane tail, current trajgen job dir (`ls subblock/trajgen/artifacts/jobs/$(ls -t subblock/trajgen/artifacts/jobs | head -1)/`), and `ss -ntlp | grep 4001`. Report deltas vs the prior snapshot.
> - For any task that just finished in the current Harbor job: update `consumption_ledger.yaml` (status + trajectory_path + reward) and add its ID to `HARBOR_EXCLUDE_TASKS` before the next trajgen restart.
> - If swegen's per-language `success_rate < 0.15` for two consecutive cycles, tune `timeout`/`cc_timeout` per `subblock/swegen/CLAUDE.md` → "Adaptive Parameter Tuning". Log every decision to `subblock/swegen/artifacts/logs/adaptive_decisions.jsonl`.
>
> ### Stop and ask me before
> - Killing tmux sessions or Docker containers you didn't start
> - Running `prepare_tasks.sh --overwrite` (it rebuilds the entire trajgen task source)
> - Changing the LLM endpoint, model, or Harbor's pinned commit in any config
> - Force-pushing, deleting branches, or anything else destructive

You can shorten the prompt if your agent already has `CLAUDE.md` files indexed — the "Read first" list is what does most of the work.

## Block Overview

| Block | Role | Remote node | Status |
|-------|------|-------------|--------|
| `subblock/swegen/` | Converts GitHub PRs → verified SWE tasks | 192.168.35.240 | Active |
| `subblock/trajgen/` | Runs agent on SWE tasks → raw trajectories | 192.168.35.240 | Active |
| `subblock/sft/` | SFT training on trajectories | TBD (8× GPU) | Planned |
| `subblock/rl/` | Online RL from trajectory rewards | TBD (8× GPU) | Planned |

Each subblock has its own `CLAUDE.md` with its full agent contract and `config.yaml` with live status.

## Directory Layout

```
SWE-Lego-Live/
├── CLAUDE.md                  # root block agent contract
├── BLOCK_DEFINITION.md        # block system specification
├── scripts/
│   ├── dryrun.sh              # validate root block
│   ├── start.sh               # launch swegen + trajgen
│   └── clean.sh               # remove temp files
├── dashboard/
│   └── overview.mdx           # human-readable current state
├── artifacts/
│   ├── index.yaml             # append-only run index
│   └── archives/              # per-run snapshots
└── subblock/
    ├── swegen/                # SWE task generation block
    ├── trajgen/               # trajectory generation block
    ├── sft/                   # SFT training block (planned)
    └── rl/                    # RL training block (planned)
```

## Further Reading

- `BLOCK_DEFINITION.md` — full block system specification
- `subblock/swegen/CLAUDE.md` — swegen agent contract, workflow, adaptive tuning
- `subblock/trajgen/CLAUDE.md` — trajgen agent contract, Harbor setup, scripts
- `dashboard/overview.mdx` — current pipeline state and new-user quickstart
