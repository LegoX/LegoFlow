# SWE-gen 任务进度监控 Dashboard

这个目录保存 SWE-gen 任务进度监控网页的生成和部署代码。生成器默认读取
`$SWEGEN_HOME/SWE-gen` 下的实时 SWE 任务数据，并把运行时文件写到当前
`subblock/swegen/dashboard/` 目录下。

页面风格参考 `SWE-Lego-Live` 的 `yuxin/eval` 分支中
`subblock/eval/dashboard` 的 MDX dashboard contract：克制的文档式布局、
清晰的 Overview / Inputs & Outputs / Status / Method Notes 分区、紧凑表格
和运维交接说明。看板只展示 SWE 任务进度，不展示其他数据面板。

## 配置变量

Cloudflare 同步脚本默认从 `~/.config/swegen_progress_cloudflare.env` 读取配置。
如果配置文件在其他位置，启动时设置 `ENV_FILE=/path/to/file`。

```bash
CLOUDFLARE_API_TOKEN="..."
CLOUDFLARE_ACCOUNT_ID="..."
PROJECT_NAME="swe-databoard"
BRANCH_NAME="swegen"
LOOP_SECONDS="3600"
PORT="8000"
SWEGEN_HOME="$HOME"
SWEGEN_DATA_ROOT="$SWEGEN_HOME/SWE-gen"
SWEGEN_TASK_ROOT="$SWEGEN_DATA_ROOT/tasks/March"
SWEGEN_PR_DIR="$SWEGEN_DATA_ROOT/collected_prs"
SWEGEN_DASHBOARD_ROOT="subblock/swegen/dashboard"
```

| 变量 | 怎么填 | 默认值 |
| --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API Token，需要能访问目标账户并具备 Pages 项目编辑/部署权限。 | 无，必填 |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Account ID，在 Cloudflare 控制台账户页面获取。 | 无，必填 |
| `PROJECT_NAME` | Cloudflare Pages 项目名。 | `swe-databoard` |
| `BRANCH_NAME` | Cloudflare Pages 部署分支名。 | `swegen` |
| `LOOP_SECONDS` | 同步脚本每轮生成和部署之间的等待秒数。 | `3600` |
| `PORT` | 本地预览 HTTP 服务端口。 | `8000` |
| `SWEGEN_HOME` | SWE-gen 数据所在机器的基础 home 目录。 | 当前用户的 `$HOME` |
| `SWEGEN_DATA_ROOT` | SWE-gen 数据和代码根目录。 | `$SWEGEN_HOME/SWE-gen` |
| `SWEGEN_TASK_ROOT` | SWE-gen 任务输出目录。 | `$SWEGEN_DATA_ROOT/tasks/March` |
| `SWEGEN_PR_DIR` | PR ID 文件目录。 | `$SWEGEN_DATA_ROOT/collected_prs` |
| `SWEGEN_DASHBOARD_ROOT` | Dashboard 运行时输出目录。 | 当前脚本所在目录，即 `subblock/swegen/dashboard` |

## 本地生成

在本仓库根目录执行：

```bash
python3 subblock/swegen/dashboard/progress_monitor_all.py \
  --output-html subblock/swegen/dashboard/site/index.html \
  --state-file subblock/swegen/dashboard/memory/.progress_monitor_all_state.jsonl \
  --cache-file subblock/swegen/dashboard/memory/.progress_monitor_all_cache.json
```

生成后可以打开 `subblock/swegen/dashboard/site/index.html`，也可以启动本地服务：

```bash
python3 subblock/swegen/dashboard/progress_monitor_all.py --serve
```

## 状态文件和缓存文件

仓库中包含两个生成器运行状态文件：

- `memory/.progress_monitor_all_state.jsonl`：历史快照文件。每次运行生成器时都会追加一行 JSON，记录当时各语言的 PR 数、已处理数、可验证任务数等概要数据。页面里的 1 小时和 24 小时增量来自这个文件。
- `memory/.progress_monitor_all_cache.json`：增量缓存文件。生成器扫描任务目录和 batch 状态时会把文件签名和统计结果写入这里，下次运行时可复用未变化文件的统计，避免每次都全量解析大量任务数据。

这两个文件由下面的命令生成或更新：

```bash
python3 subblock/swegen/dashboard/progress_monitor_all.py \
  --output-html subblock/swegen/dashboard/site/index.html \
  --state-file subblock/swegen/dashboard/memory/.progress_monitor_all_state.jsonl \
  --cache-file subblock/swegen/dashboard/memory/.progress_monitor_all_cache.json
```

同步脚本 `subblock/swegen/dashboard/run_cloudflare_pages_sync.sh` 内部也会调用同一个生成器，因此运行同步脚本时也会更新这两个文件。

## Cloudflare Pages 同步

启动同步：

```bash
bash subblock/swegen/dashboard/run_cloudflare_pages_sync.sh
```

脚本每轮会先生成 `subblock/swegen/dashboard/site/index.html`，然后用 `wrangler pages deploy`
部署到 Cloudflare Pages。

## `--serve` 和同步脚本的区别

- `python3 subblock/swegen/dashboard/progress_monitor_all.py --serve`：只在本机生成和预览页面，会启动一个本地 HTTP 服务，适合调试或查看本地结果；它不会部署到 Cloudflare。
- `bash subblock/swegen/dashboard/run_cloudflare_pages_sync.sh`：用于长期在线同步。它会循环生成页面、启动本地预览服务，并通过 `wrangler pages deploy` 把 `subblock/swegen/dashboard/site/` 发布到 Cloudflare Pages。
