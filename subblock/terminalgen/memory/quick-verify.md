# Terminal-gen Quick Verification

This document helps a fresh AI agent decide, in 5–10 minutes, whether the
`SWE-Lego-Live/subblock/terminalgen` block can run end to end, and quickly
attribute failures to the environment, the LLM, Docker, or the terminal-lego
pipeline itself.

> **First, look at `artifacts/examples/`** (`artifacts/examples/README.md`) for
> committed known-good verified tasks in both output formats — it shows exactly
> what a successful task looks like before you run anything.

## Goal

Verification has three layers:

1. Environment preflight: submodule, LLM, Docker, (optional) SO API key are usable.
2. Deterministic fixture replay: a known verified task (`https-nginx-cert-setup`)
   re-validates to reward=1 through Docker (spends no LLM / SO quota).
3. Small-sample generation: generate and validate at least one task from a
   2-question sample.

## 1. Environment preflight

In the `subblock/terminalgen` block, first confirm the submodule is initialised
and pinned correctly:

```bash
git submodule update --init repos/terminal-lego
git -C repos/terminal-lego rev-parse HEAD   # expect 1a2a6048a0d568891407ed3c6a441c995f4491b0
```

> **Never modify `repos/terminal-lego/`** — it is a read-only upstream dependency.

Install dependencies (requests + PyYAML):

```bash
python3 -m venv artifacts/envs/terminalgen-env
source artifacts/envs/terminalgen-env/bin/activate
pip install -r requirements.txt
```

Set the LLM / SO environment variables. Never write keys into repo files:

```bash
export OPENAI_API_KEY="..."
export OPENAI_API_BASE_URL="https://your-openai-compatible-endpoint/v1"
export MODEL_NAME="deepseek-v4-flash"
export SO_API_KEY="rl_xxx"        # optional; 300/day without it
export DOCKER_HOST=unix:///var/run/docker.sock
```

> **Important**: terminal-lego's generator reads the endpoint from `--api-base`,
> which `scripts/create_domain.sh` sets to `$OPENAI_API_BASE_URL` (NOT
> `OPENAI_API_BASE`). Set the `_URL` form.

Check the LLM (the endpoint the generator actually hits):

```bash
python - <<'PY'
import os, json, urllib.request
base=os.environ["OPENAI_API_BASE_URL"].rstrip("/"); key=os.environ["OPENAI_API_KEY"]
req=urllib.request.Request(base+"/chat/completions",
    data=json.dumps({"model":os.environ.get("MODEL_NAME","deepseek-v4-flash"),
        "messages":[{"role":"user","content":"ping"}],"max_tokens":16}).encode(),
    headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"})
print("llm_status", urllib.request.urlopen(req,timeout=30).status)
PY
```

Check Docker: `docker info --format '{{.ServerVersion}}'`

Or run everything at once: `bash scripts/dryrun.sh`.

## 2. Deterministic fixture replay

```bash
bash tests/smoke/verify.sh
```

Pass criterion: `PASS: fixture validated with reward=1.0`. This stages the
known-good golden task `tests/smoke/fixtures/https-nginx-cert-setup` as
`task_00000` and feeds it to the terminal-lego validator, confirming the local
Docker build → solve → test → reward loop works. **It spends no LLM / SO quota,
is deterministic, and is the right first check before any real generation.**

## 3. Small-sample generation

```bash
TESTS_WITH_SMOKE=1 bash tests/run.sh
# or directly:
bash tests/smoke/10_so_demo.sh
```

Generates one task from `tests/smoke/fixtures/so_data_sample.json` (2 questions)
and Docker-validates it. Pass criterion: `validation_report.json` reports
`passed >= 1`.

Run a real domain:

```bash
bash scripts/scrape_so_questions.sh 1 200
bash scripts/create_domain.sh security-cryptography
test -s artifacts/terminal_tasks/security-cryptography-tl/verifiable_tasks.txt
```

Optional — flatten verified tasks into one dir (same v1.0 format):

```bash
python scripts/extract_verified_tasks.py
ls artifacts/merged_terminal_tasks/
```

## 4. Common failure attribution

| Symptom | Check first |
|---|---|
| `OPENAI_API_BASE_URL unset` | Must set the `_URL` form; the generator only reads `--api-base` |
| `401 Invalid token` | Is `OPENAI_API_KEY` valid; are stale env vars leaking in |
| `403 unsupported_country_region` | Hitting the official OpenAI endpoint instead of a proxy/compatible one |
| Scrape stalls with no output | StackExchange 429 rate limit (300/day shared IP without `SO_API_KEY`); set a real key |
| Tasks generated but all reward=0 | Generator picked topics needing live network/credentials (e.g. real Let's Encrypt issuance); narrow that domain's `tag_filter` toward self-contained tasks |
| `Docker daemon not running` but `docker info` works | Set `DOCKER_HOST=unix:///var/run/docker.sock` |
| Validator finds no tasks | Only `task_*`-prefixed dirs are discovered; check candidate naming |

## 5. Recommended judgment

If `tests/smoke/verify.sh` passes (fixture reward=1), the local Docker
validation loop works; then run `10_so_demo.sh` to validate the LLM generation
loop. If both pass, terminalgen's main flow, LLM, and Docker are all ready.

If quick verification fails, **do not modify `repos/terminal-lego/` code**.
First use the table above to separate environment, quota, and topic-selection
issues from Docker parameters; only after a failure reproduces stably should you
adjust this block's `scripts/` or `config.yaml` (`tag_filter` / `params`).
