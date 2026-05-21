# SWE-gen 进度监控 Dashboard

这个目录保存 SWE-gen 进度监控网页的生成和部署代码。网页内容格式沿用原始目录
`/home/ywxzml3j/ywxzml3juser23/SWE-gen/public_progress_dashboard`，默认从
`/home/ywxzml3j/ywxzml3juser23/SWE-gen` 读取实时数据，并把运行时文件写到当前
`dashboard/swegen/` 目录下。

## 本地生成

在仓库根目录 `/home/ywxzml3j/ywxzml3juser23/SWE-Lego-Live` 执行：

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

## Cloudflare Pages 同步

同步脚本会读取下面这个环境变量文件：

```bash
/home/ywxzml3j/ywxzml3juser23/.config/swegen_progress_cloudflare.env
```

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

## 数据路径覆盖

生成器支持以下环境变量覆盖默认路径：

- `SWEGEN_HOME`：基础 home 目录，默认 `/home/ywxzml3j/ywxzml3juser23`。
- `SWEGEN_DATA_ROOT`：SWE-gen 数据和代码根目录，默认 `$SWEGEN_HOME/SWE-gen`。
- `SWEGEN_TASK_ROOT`：任务输出根目录，默认 `$SWEGEN_DATA_ROOT/tasks/March`。
- `SWEGEN_PR_DIR`：PR ID 文件目录，默认 `$SWEGEN_DATA_ROOT/collected_prs`。
- `SWEGEN_DASHBOARD_ROOT`：Dashboard 运行时输出目录，默认当前 `dashboard/swegen/`。
- `SWEGEN_TRAJ_DIR`：轨迹 JSONL 文件目录。

同步脚本额外支持 `ENV_FILE`、`PUBLIC_DIR`、`STATE_FILE`、`CACHE_FILE`、
`PROJECT_NAME`、`BRANCH_NAME`、`LOOP_SECONDS`、`PORT` 和 `LOCK_FILE`。
