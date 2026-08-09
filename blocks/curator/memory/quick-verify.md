# SWEgen Quick Verification

This document helps a new AI agent decide, in 5-10 minutes, whether the SWEgen
checkout wired into `LegoFlow/curator` can run end to end, and quickly
tell whether a problem is in the environment, the LLM, Docker/Harbor, or the
SWEgen code itself.

This is a manual block-local verification flow. It is distinct from
`/curator:create-tasks` smoke mode, which reads the bundled PR list under
`repos/swegen/artifacts/` and writes under `artifacts/experiments/quick-verify/`.

## Goal

Quick verification has three layers:

1. Environment preflight: GitHub, LLM, and Docker are reachable.
2. Harbor quick check: NOP/Oracle runs for a known verified task.
3. Small-sample generation: from a fixed set of Python PRs, generate and
   verify at least 1 task, writing its task ID to `verifiable_tasks.txt`.

## 1. Environment preflight

Before running inside the `LegoFlow/blocks/curator` block, make sure the
submodule is initialized:

```bash
git submodule update --init blocks/curator/repos/swegen
```

Install SWEgen:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e repos/swegen/
```

Check the GitHub token:

```bash
python - <<'PY'
import os
import requests

token = os.getenv("GITHUB_TOKEN") or (os.getenv("GITHUB_TOKENS", "").split(",")[0] or "")
assert token, "missing GITHUB_TOKEN/GITHUB_TOKENS"
r = requests.get(
    "https://api.github.com/rate_limit",
    headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
    timeout=20,
)
print("github_status", r.status_code)
print("remaining", r.json().get("resources", {}).get("core", {}).get("remaining"))
PY
```

Check the LLM API. Never write the key into a repository file; pass it only
through environment variables:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="$OPENAI_API_KEY"
export OPENAI_API_BASE_URL="https://your-openai-compatible-endpoint/v1"
export ANTHROPIC_BASE_URL="https://your-anthropic-compatible-endpoint"
export OPENAI_MODEL="..."
export ANTHROPIC_MODEL="..."
export CLAUDE_CONFIG_DIR="$PWD/artifacts/claude-config/swegen-clean"
mkdir -p "$CLAUDE_CONFIG_DIR"
```

The OpenAI-compatible path (above) covers PR evaluation. The Claude Code path
(`ANTHROPIC_BASE_URL`) is what writes `verifiable_tasks.txt`. For OpenAI-only
providers (Qwen / GLM / sglang / vLLM), `ANTHROPIC_BASE_URL` must point at a
local LiteLLM proxy, not the raw provider URL — see the "LLM provider modes"
section of `CLAUDE.md`. Verify the OpenAI path with a real completion:

```bash
PYTHONPATH=repos/swegen/src python - <<'PY'
from openai import OpenAI
from swegen.llm_env import hydrate_cross_provider_env, get_openai_compatible_config

hydrate_cross_provider_env()
model, key, base = get_openai_compatible_config()
print("model", model)
print("base", base)
client = OpenAI(api_key=key, base_url=base, timeout=60)
client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "ping"}],
    max_tokens=16,
)
print("llm_preflight=ok")
PY
```

If `cc_provider_mode` is `openai_proxy`, also confirm the proxy is up:

```bash
curl -sf "${ANTHROPIC_BASE_URL%/}/health" >/dev/null && echo "cc proxy ok" || echo "cc proxy DOWN"
```

Check Docker:

```bash
docker info --format '{{.ServerVersion}}'
```

On this machine, set the Docker socket explicitly so Harbor does not probe
`/tmp/podman-fresh.sock`:

```bash
export DOCKER_HOST=unix:///var/run/docker.sock
```

## 2. Harbor quick check

If a Python verified task already exists, validate the known sample first:

```bash
swegen validate \
  artifacts/swe_tasks/py-cc \
  --task tox-dev__tox-3813 \
  --jobs-dir artifacts/swe_tasks/.swegen/harbor-jobs-quick \
  --env docker
```

Success criteria:

```text
NOP reward=0
Oracle reward=1
```

If `docker info` succeeds but Harbor reports:

```text
Docker daemon is not running. Please start Docker and try again.
```

check first:

```bash
echo "$DOCKER_HOST"
export DOCKER_HOST=unix:///var/run/docker.sock
```

If Harbor cannot find the task, confirm the command arguments:

- the dataset root must be the parent directory containing the task subdirs,
  e.g. `artifacts/swe_tasks/py-cc`
- local task filtering must use `-i/--include-task-name`
- do not pass a local task id to Harbor's `-t/--task`

## 3. Small-sample generation

Prepare 10 Python PR inputs. Put a lightweight, already-verified PR such as
`tox-dev/tox:pr-3813` on the first line of
`artifacts/collected_prs/python_pr_ids.txt`:

```text
tox-dev/tox:pr-3813
tox-dev/tox:pr-3814
AnswerDotAI/RAGatouille:pr-157
tox-dev/tox:pr-3810
electricitymaps/electricitymaps-contrib:pr-8113
tox-dev/tox:pr-3803
morpheus65535/bazarr:pr-2691
tox-dev/tox:pr-3804
tox-dev/tox:pr-3800
electricitymaps/electricitymaps-contrib:pr-8119
```

Run the small sample:

```bash
swegen create \
  --input-ids-file artifacts/collected_prs/python_pr_ids.txt \
  --max-pr 1 \
  --n-concurrent 1 \
  --output artifacts/swe_tasks/py-cc \
  --state-dir artifacts/state/swegen-py \
  --timeout 2400 \
  --cc-timeout 1800 \
  --no-require-issue \
  --min-source-files 1 \
  --max-source-files 10 \
  --docker-prune-batch 0 \
  --verbose
```

Success criterion:

```bash
test -s artifacts/swe_tasks/py-cc/verifiable_tasks.txt
```

`verifiable_tasks.txt` should contain at least one task ID, e.g.:

```text
tox-dev__tox-3813
```

## 4. Common failure attribution

| Symptom | Check first |
|---|---|
| No `verifiable_tasks.txt` written, but batch state shows success | CC path: `cc_provider_mode`; is the LiteLLM proxy up for OpenAI-only providers? (silent CC failure) |
| `LLM API preflight failed` | Whether `OPENAI_API_KEY`, `OPENAI_API_BASE_URL`, `OPENAI_MODEL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL` match the same provider; whether stale env vars pollute the run |
| `401 Invalid token` | Whether the API key is valid, or an old env var is in use |
| `403 unsupported_country_region_territory` | Whether you hit the official OpenAI endpoint instead of the proxy/compatible endpoint |
| `Docker daemon is not running` but `docker info` succeeds | Set `DOCKER_HOST=unix:///var/run/docker.sock` |
| Harbor cannot find the local task | Whether the dataset root is correct; whether `-i/--include-task-name` is used |
| A PR passes NOP but not Oracle | The candidate PR may have a complex environment or incomplete test command; try a lighter PR to verify the main flow |
| C++ extension build OOM | The candidate PR needs too many resources; not suitable as a quick-verification sample |

## 5. Recommended judgement

If `tox-dev/tox:pr-3813` passes `swegen create --max-pr 1` and writes to
`verifiable_tasks.txt`, the SWEgen main flow, LLM API, Claude SDK, and
Docker/Harbor local validation chain are all working.

If quick verification fails, do not immediately edit SWEgen code. First use
the table above to decide whether it is an environment, candidate-PR, or
Harbor/Docker argument issue; only enter code debugging once the same problem
reproduces consistently across several lightweight PRs.
