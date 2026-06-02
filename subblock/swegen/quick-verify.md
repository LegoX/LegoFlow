# SWEgen 快速验证流程

本文档用于帮助新的 AI agent 在 5 到 10 分钟内判断当前 `SWE-Lego-Live/swegen` 接入的 SWEgen 是否能跑通，并快速区分问题属于环境、LLM、Docker/Harbor，还是 SWEgen 代码。

## 目标

快速验证分三层：

1. 环境 preflight：GitHub、LLM、Docker 可用。
2. Harbor 快速验证：已知 verified task 的 NOP/Oracle 能跑通。
3. 小样本生成验证：固定 10 个 Python PR 中至少生成并验证 1 个 task，`verifiable_tasks.txt` 写入 task ID。

## 1. 环境 preflight

在 `SWE-Lego-Live/subblock/swegen` 所在 block 中执行前，先确认 submodule 已初始化：

```bash
git submodule update --init subblock/swegen/repos/swegen
```

本流程使用 submodule 固定携带的少量对照数据作为输入和已验证样本：

- PR 输入：`repos/swegen/artifacts/collected_prs/python_pr_ids.txt`
- 已验证 Python task：`repos/swegen/artifacts/swe_tasks/py-cc/tox-dev__tox-3813`
- 已验证 manifest：`repos/swegen/artifacts/swe_tasks/py-cc/verifiable_tasks.txt`

安装 SWEgen：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e repos/swegen/
```

检查 GitHub token：

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

检查 LLM API。不要把 key 写入仓库文件；只通过环境变量传入：

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="$OPENAI_API_KEY"
export OPENAI_API_BASE_URL="https://yunwu.ai/"
export ANTHROPIC_BASE_URL="https://yunwu.ai/"
export OPENAI_MODEL="gpt-5.4"
export ANTHROPIC_MODEL="claude-opus-4-6"
```

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
    max_tokens=1,
)
print("llm_preflight=ok")
PY
```

检查 Docker：

```bash
docker info --format '{{.ServerVersion}}'
```

当前机器建议显式设置 Docker socket，避免 Harbor 默认检查 `/tmp/podman-fresh.sock`：

```bash
export DOCKER_HOST=unix:///var/run/docker.sock
```

## 2. Harbor 快速验证

先用 submodule 内置的 Python verified task 验证 Harbor/NOP/Oracle 链路：

```bash
swegen validate \
  repos/swegen/artifacts/swe_tasks/py-cc \
  --task tox-dev__tox-3813 \
  --jobs-dir artifacts/experiments/quick-verify/harbor-jobs-quick \
  --env docker
```

成功标准：

```text
NOP reward=0
Oracle reward=1
```

如果 `docker info` 成功但 Harbor 报：

```text
Docker daemon is not running. Please start Docker and try again.
```

优先检查：

```bash
echo "$DOCKER_HOST"
export DOCKER_HOST=unix:///var/run/docker.sock
```

如果 Harbor 找不到 task，确认命令参数：

- dataset root 必须是包含 task 子目录的父目录，例如 `repos/swegen/artifacts/swe_tasks/py-cc`
- 本地 task 过滤必须使用 `-i/--include-task-name`
- 不要把本地 task id 传给 Harbor 的 `-t/--task`

## 3. 小样本生成验证

submodule 已内置 10 个 Python PR 输入，可先确认内容：

```bash
sed -n '1,20p' repos/swegen/artifacts/collected_prs/python_pr_ids.txt
```

运行小样本。输入读取 submodule 的固定 PR 列表，输出写入当前 block 的 `artifacts/experiments/quick-verify/`，避免修改 submodule：

```bash
swegen create \
  --input-ids-file repos/swegen/artifacts/collected_prs/python_pr_ids.txt \
  --max-pr 1 \
  --n-concurrent 1 \
  --output artifacts/experiments/quick-verify/swe_tasks/py-cc \
  --state-dir artifacts/experiments/quick-verify/state \
  --timeout 2400 \
  --cc-timeout 1800 \
  --no-require-issue \
  --min-source-files 1 \
  --max-source-files 10 \
  --docker-prune-batch 0 \
  --verbose
```

成功标准：

```bash
test -s artifacts/experiments/quick-verify/swe_tasks/py-cc/verifiable_tasks.txt
```

`verifiable_tasks.txt` 应至少包含一个 task ID，例如：

```text
tox-dev__tox-3813
```

## 4. 常见失败归因

| 现象 | 优先检查 |
|---|---|
| `LLM API preflight failed` | `OPENAI_API_KEY`、`OPENAI_API_BASE_URL`、`OPENAI_MODEL`、`ANTHROPIC_MODEL` 是否匹配同一服务 |
| `401 Invalid token` | API key 是否有效，是否误用了旧环境变量 |
| `403 unsupported_country_region_territory` | 是否误走官方 OpenAI endpoint，而不是代理/兼容 endpoint |
| `Docker daemon is not running` 但 `docker info` 成功 | 设置 `DOCKER_HOST=unix:///var/run/docker.sock` |
| Harbor 找不到本地 task | dataset root 是否正确；是否使用 `-i/--include-task-name` |
| 某个 PR NOP 过但 Oracle 不过 | 可能是候选 PR 环境复杂或 test command 不完整，先换轻量 PR 验证主流程 |
| C++ extension 编译 OOM | 候选 PR 资源需求过高，不宜作为 quick verification 样本 |

## 5. 推荐判断

如果 `tox-dev/tox:pr-3813` 能通过 `swegen create --max-pr 1` 并写入 `verifiable_tasks.txt`，说明当前 SWEgen 主流程、LLM API、Claude SDK、Docker/Harbor 本地验证链路都可用。

如果 quick verification 失败，不要立即修改 SWEgen 代码。先根据上表判断是环境、候选 PR，还是 Harbor/Docker 参数问题；只有确认同一问题在多个轻量 PR 上稳定复现，才进入代码调试。
