# Trajgen Local Dashboard

A small, local-only HTML dashboard for the `trajgen` subblock. It scans two
artifact directories and renders a single self-contained HTML file you can
either open directly in a browser or serve over the loopback interface.

Modeled after [`dashboard/swegen/`](https://github.com/SWE-Lego/SWE-Lego-Live/tree/swegen/dashboard/swegen)
on the `swegen` branch, but stripped to stdlib-only (no `tiktoken`,
no `tomllib`). For remote viewing it can publish `site/` to Cloudflare
Pages via [`run_cloudflare_pages_sync.sh`](run_cloudflare_pages_sync.sh)
(see [Cloudflare Pages 同步](#cloudflare-pages-同步) below).

## Runtime

The script ships with [PEP 723](https://peps.python.org/pep-0723/) inline
script metadata and a `uv run` shebang, so the runtime Python is provided
by `uv` (no `pip install`, no shared venv). Requires `uv` on `PATH` and
a Python interpreter `>= 3.11` (uv will download one if needed).

```text
#!/usr/bin/env -S uv run --no-project --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
```

Because dependencies are empty (stdlib-only), there is no `dashboard-uv`
environment to maintain. If a third-party package is ever needed (e.g.
`tiktoken` for token accounting), add it to the `dependencies = [...]`
list and uv will resolve it transparently on the next run.

## What it monitors

| Source | Files | Shown as |
| --- | --- | --- |
| Harbor jobs | `../artifacts/jobs/<job>/result.json` (only jobs that actually have one) | Harbor Jobs table + per-job eval breakdown |
| SFT conversion | `../artifacts/sft_data/<job>/lf.stats.json` | SFT Datasets table |

Key fields surfaced for each Harbor job:

- `id`, `started_at`, `finished_at`, `n_total_trials`
- `stats.n_trials`, `stats.n_errors`
- For each `stats.evals.<eval>`: `n_trials`, `n_errors`, `metrics[0].mean`,
  number of `reward == 1.0` / `reward == 0.0` outcomes, and a summary of
  `exception_stats`.

Key fields surfaced for each SFT dataset:

- `count`
- `token_lens` (min / mean / max, `gt_128k`)
- `n_turns` (min / mean / max, `gte_100`)
- `scores` (min / mean / max)
- Sizes of `im.jsonl` and `lf.json` on disk.

## Files

```text
dashboard/
├── progress_monitor.py                     # generator (this script)
├── run_cloudflare_pages_sync.sh            # loop-generate + deploy to Cloudflare Pages
├── README.md                               # this file
├── site/index.html                         # generated, gitignored
└── memory/.progress_monitor_cache.json     # mtime-based parse cache, gitignored
```

`site/` and `memory/.progress_monitor_cache.json` are added to
[`../.gitignore`](../.gitignore) and wiped by
[`../scripts/clean.sh`](../scripts/clean.sh).

## How to run

From the subblock root (`subblock/trajgen/`). The script is executable;
prefer invoking it directly so the uv-run shebang takes effect:

```bash
./dashboard/progress_monitor.py                                  # one-shot generate
./dashboard/progress_monitor.py --serve --open                   # generate + local preview + open browser
./dashboard/progress_monitor.py --loop 60 --serve --port 8765    # continuous refresh every 60s
```

Equivalent explicit forms (useful in CI or when `./` execution is blocked):

```bash
uv run --no-project --script dashboard/progress_monitor.py [args...]
```

The plain `python3 dashboard/progress_monitor.py` invocation also still
works (the script is stdlib-only), but it bypasses uv's Python pinning.

Then either open the file directly:

```text
subblock/trajgen/dashboard/site/index.html
```

or visit the local server (default port `8765`):

```text
http://127.0.0.1:8765/index.html
```

The HTML also self-refreshes every `--refresh` seconds (default 60) so an
opened tab stays current while `--loop` keeps writing new snapshots.

## CLI flags

| Flag | Default | Purpose |
| --- | --- | --- |
| `--output-html` | `dashboard/site/index.html` | Where to write the HTML. |
| `--cache-file` | `dashboard/memory/.progress_monitor_cache.json` | Per-file mtime cache to skip re-parsing unchanged `result.json` / `lf.stats.json`. |
| `--jobs-dir` | `../artifacts/jobs` | Harbor jobs root. |
| `--sft-dir` | `../artifacts/sft_data` | SFT conversion root. |
| `--refresh` | `60` | Browser-side `<meta refresh>` interval (seconds). |
| `--loop [N]` | off (60 if bare flag) | Regenerate every N seconds in a loop. |
| `--serve` | off | Start `ThreadingHTTPServer` over `site/`. |
| `--host` / `--port` | `127.0.0.1` / `8765` | HTTP bind. |
| `--open` | off | Open the HTML in the default browser after the first write. |
| `--force-full-scan` | off | Ignore cache for this run. |

## Cloudflare Pages 同步

`--serve` 只在本机回环地址提供预览。要在其他地方也能看到进展，用
[`run_cloudflare_pages_sync.sh`](run_cloudflare_pages_sync.sh) 把生成的
`site/` 循环部署到 Cloudflare Pages（通过 `wrangler`），得到一个公开的
`*.pages.dev` 地址。

当前线上地址（始终指向最新部署）：**<https://swe-trajgen-databoard.pages.dev>**

启动同步（从 subblock 根目录 `subblock/trajgen/`）：

```bash
bash dashboard/run_cloudflare_pages_sync.sh
```

脚本每轮会先用 `progress_monitor.py` 重新生成 `dashboard/site/index.html`，
然后用 `wrangler pages deploy` 部署，并保持一个本地预览服务用于调试。

此外，循环还会按 `CONVERT_EVERY_SECONDS`（默认 2 小时）调用一次
`scripts/convert_trajectories.sh --skip-unchanged`，把最新 Harbor job 的轨迹
转换成 SFT 数据，让 dashboard 的 SFT 统计也能在线刷新。`--skip-unchanged`
会在 job 的已解决（reward=1.0）实例集合没有变化时直接跳过，不做重复转换。
scaffold 由 **job 名**自动识别（而不是 `config.yaml` 里的 `agent.name`，两者可能
不一致），转换失败不影响 HTML 的生成与部署。

配置默认从 `~/.config/trajgen_progress_cloudflare.env` 读取（用 `ENV_FILE`
覆盖路径）。`CLOUDFLARE_API_TOKEN` 和 `CLOUDFLARE_ACCOUNT_ID` 必填：

```bash
CLOUDFLARE_API_TOKEN="..."
CLOUDFLARE_ACCOUNT_ID="..."
PROJECT_NAME="swe-trajgen-databoard"
BRANCH_NAME="trajgen"
LOOP_SECONDS="3600"
PORT="8770"
CONVERT_ENABLED="1"
CONVERT_JOB="latest"
CONVERT_EVERY_SECONDS="7200"
```

| 变量 | 怎么填 | 默认值 |
| --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API Token，需要对目标账户有 Pages 项目编辑/部署权限。 | 无，必填 |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Account ID，在控制台账户页面获取。 | 无，必填 |
| `PROJECT_NAME` | Cloudflare Pages 项目名（决定公开 URL）。 | `swe-trajgen-databoard` |
| `BRANCH_NAME` | Cloudflare Pages 部署分支名。 | `trajgen` |
| `LOOP_SECONDS` | 每轮生成和部署之间的等待秒数。 | `3600` |
| `PORT` | 本地预览 HTTP 服务端口。 | `8770` |
| `CONVERT_ENABLED` | 是否在循环里自动跑 SFT 转换（`0` 关闭）。 | `1` |
| `CONVERT_JOB` | 要转换的 Harbor job 名，或 `latest`。 | `latest` |
| `CONVERT_EVERY_SECONDS` | 两次转换尝试之间的最小间隔秒数（与部署节奏解耦）。 | `7200` |
| `ENV_FILE` | 覆盖 env 文件路径。 | `~/.config/trajgen_progress_cloudflare.env` |

### 怎么创建 API Token

`wrangler pages deploy` 需要 **Account 级别的 `Cloudflare Pages: Edit`** 权限。
Cloudflare 的内置模板（Edit Cloudflare Workers、Read all resources 等）都没有
完全对应的，所以在 [API Tokens 页面](https://dash.cloudflare.com/profile/api-tokens)
选 **Create Custom Token（创建自定义令牌）**，按下面配置：

- **Permissions**：
  - `Account` → `Cloudflare Pages` → `Edit`（必需）
  - 可选 `Account` → `Account Settings` → `Read`（方便确认 Account ID）
- **Account Resources**：`Include` → 选你的账户
- **Zone Resources**：保持默认即可（Pages 不需要 zone 权限，除非要绑自定义域名）

生成的 Token 填入 `CLOUDFLARE_API_TOKEN`。若界面找不到自定义令牌，模板里最接近的是
`Edit Cloudflare Workers`（Workers 与 Pages 共用部署体系，通常也能部署 Pages），
但会附带用不到的多余权限，**最小权限仍推荐自定义令牌 + Cloudflare Pages: Edit**。

`CLOUDFLARE_ACCOUNT_ID` 不在 Token 里，需单独从 Cloudflare 控制台账户主页复制那串
32 位的 Account ID。

需要节点上有 `node`/`npx`（脚本用 `npx --yes` 按需自动安装 wrangler）和 `uv`。
脚本默认用 `wrangler@3`（变量 `WRANGLER_PKG`），因为最新 `wrangler`（v4+）要求
Node.js >= 22，而本节点目前是 Node 18；若以后装了新 Node，可用 `WRANGLER_PKG=wrangler`
覆盖为最新版。建议在 `trajgen` tmux 会话里运行，使其在断连后仍持续同步：

```bash
tmux new-session -d -s trajgen-cf "ENV_FILE=.env.cf bash dashboard/run_cloudflare_pages_sync.sh 2>&1 | tee /tmp/trajgen_cf_sync.log"
```

`--serve` 与同步脚本的区别：

- `./dashboard/progress_monitor.py --serve`：只在本机生成并预览，不部署。
- `bash dashboard/run_cloudflare_pages_sync.sh`：长期在线同步，循环生成并
  通过 `wrangler pages deploy` 把 `dashboard/site/` 发布到 Cloudflare Pages。

## Out of scope

- Per-trial trajectory browsing (only `result.json` aggregates are shown).
- Historical 1h / 24h deltas via a `state.jsonl` snapshot file.

If any of those are needed later, lift the corresponding helpers directly
from `dashboard/swegen/progress_monitor_all.py` on the `swegen` branch.
