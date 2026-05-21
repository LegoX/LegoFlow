# SWE-gen 进度监控 Dashboard

这个目录保存 SWE-gen 进度监控网页的生成和部署代码。网页内容格式沿用 SWE-gen
项目里的原始 dashboard。生成器默认读取 `$SWEGEN_HOME/SWE-gen` 下的实时数据，
并把运行时文件写到当前 `dashboard/swegen/` 目录下。

## 本地生成

在本仓库根目录执行：

```bash
python3 dashboard/swegen/progress_monitor_all.py \
  --output-html dashboard/swegen/site/index.html \
  --state-file dashboard/swegen/.progress_monitor_all_state.jsonl \
  --cache-file dashboard/swegen/.progress_monitor_all_cache.json
```

生成后可以打开 `dashboard/swegen/site/index.html`，也可以启动本地服务：

```bash
python3 dashboard/swegen/progress_monitor_all.py --serve
```

## 状态文件和缓存文件

仓库中包含两个生成器运行状态文件：

- `.progress_monitor_all_state.jsonl`：历史快照文件。每次运行生成器时都会追加一行 JSON，记录当时各语言的 PR 数、已处理数、可验证任务数等概要数据。页面里的 1 小时和 24 小时增量来自这个文件。
- `.progress_monitor_all_cache.json`：增量缓存文件。生成器扫描任务目录、batch 状态和轨迹文件时会把文件签名和统计结果写入这里，下次运行时可复用未变化文件的统计，避免每次都全量解析大量任务和轨迹数据。

这两个文件由下面的命令生成或更新：

```bash
python3 dashboard/swegen/progress_monitor_all.py \
  --output-html dashboard/swegen/site/index.html \
  --state-file dashboard/swegen/.progress_monitor_all_state.jsonl \
  --cache-file dashboard/swegen/.progress_monitor_all_cache.json
```

同步脚本 `dashboard/swegen/run_cloudflare_pages_sync.sh` 内部也会调用同一个生成器，因此运行同步脚本时也会更新这两个文件。

## Cloudflare Pages 同步

同步脚本默认读取下面这个环境变量文件：

```bash
~/.config/swegen_progress_cloudflare.env
```

如果你的文件放在其他位置，可以启动时通过 `ENV_FILE` 覆盖。

该文件至少需要包含：

```bash
CLOUDFLARE_API_TOKEN="..."
CLOUDFLARE_ACCOUNT_ID="..."
```

必填变量说明：

- `CLOUDFLARE_API_TOKEN`：Cloudflare API Token，供 `wrangler` 创建 Cloudflare Pages 项目和部署静态页面使用。该 token 需要能访问目标 Cloudflare 账户，并具备 Pages 项目编辑/部署相关权限。
- `CLOUDFLARE_ACCOUNT_ID`：Cloudflare 账户 ID，用于告诉 `wrangler` 把 Pages 项目创建和部署到哪个账户。

可选变量说明：

- `PROJECT_NAME`：Cloudflare Pages 项目名，默认 `swe-databoard`。
- `BRANCH_NAME`：Cloudflare Pages 部署分支名，默认 `swegen`。
- `LOOP_SECONDS`：生成和部署循环间隔秒数，默认 `3600`。
- `PORT`：本地预览 HTTP 服务端口，默认 `8000`。

启动同步：

```bash
bash dashboard/swegen/run_cloudflare_pages_sync.sh
```

脚本每轮会先生成 `dashboard/swegen/site/index.html`，然后用 `wrangler pages deploy`
部署到 Cloudflare Pages。

## `--serve` 和同步脚本的区别

- `python3 dashboard/swegen/progress_monitor_all.py --serve`：只在本机生成和预览页面，会启动一个本地 HTTP 服务，适合调试或查看本地结果；它不会部署到 Cloudflare。
- `bash dashboard/swegen/run_cloudflare_pages_sync.sh`：用于长期在线同步。它会循环生成页面、启动本地预览服务，并通过 `wrangler pages deploy` 把 `dashboard/swegen/site/` 发布到 Cloudflare Pages。

## 数据路径覆盖

生成器支持以下环境变量覆盖默认路径：

- `SWEGEN_HOME`：基础 home 目录，默认使用当前用户的 home 目录。
- `SWEGEN_DATA_ROOT`：SWE-gen 数据和代码根目录，默认 `$SWEGEN_HOME/SWE-gen`。
- `SWEGEN_TASK_ROOT`：任务输出根目录，默认 `$SWEGEN_DATA_ROOT/tasks/March`。
- `SWEGEN_PR_DIR`：PR ID 文件目录，默认 `$SWEGEN_DATA_ROOT/collected_prs`。
- `SWEGEN_DASHBOARD_ROOT`：Dashboard 运行时输出目录，默认当前 `dashboard/swegen/`。
- `SWEGEN_TRAJ_DIR`：轨迹 JSONL 文件目录。
