# Harbor Job Dashboard

Harbor 任务 Dashboard 的 Web UI，用于浏览 Harbor job 结果、分析报告和 agent 执行轨迹。

## 功能

- **Overview**：汇总所有 job 的统计信息，例如 scaffold、dataset、model、resolve rate。
- **All jobs**：展示所有 job 的可排序表格和关键指标。
- **Single job**：查看单个 job 的分析报告、主要失败分布、任务 breakdown、trial 详情。
- **Compare**：并排比较多个 job。可以通过侧边栏 job 旁边的 `+` 按钮加入对比集合。
- **Trajectory viewer**：逐步查看 agent 执行过程，包括 message、tool call 和 observation。

界面风格参考 LLaMA-Factory webui：slate-950 深色主题、浅色主题切换、侧边栏导航、indigo 强调色。

## 快速开始

```bash
cd webui
python3 server.py --port 8092
```

然后在浏览器打开：

```text
http://localhost:8092
```

服务默认自动发现 `../jobs/` 下的 job。也可以通过 `--jobs-dir` 自定义 jobs 目录。

## 使用 Cloudflare Pages 长期公开

如果需要长期公网访问，推荐把 dashboard 的动态数据导出成静态 JSON/HTML，然后部署到 Cloudflare Pages。

公网版本仍然是交互式的：可以搜索、排序、筛选、对比、看图表、点进 job、查看 trajectory。区别是公网数据只会在每次导出/部署后更新，而不是每次请求都实时读取本机 `jobs/`。

### 免费无 R2 trajectory 模式

如果不想启用 Cloudflare R2，可以使用静态 trajectory 分块。导出器会把多条 trajectory 打包成有大小上限的 chunk JSON，并生成 manifest。打开首页或点进 job 时不会下载 trajectory；只有点击某个 trial 时，浏览器才会下载包含这条 trajectory 的 chunk，同一个 chunk 里的其他 trial 会复用浏览器缓存。

```bash
cd /home/ywxzml3j/ywxzml3juser57/code/harbor-dev/webui
python3 export_static.py --output-dir site --trajectory-target chunks --trajectory-chunk-mb 8
python3 -m http.server 8093 --bind 127.0.0.1 --directory site
```

可以调小 chunk，让单次点击下载更轻；也可以调大 chunk，减少文件数量：

```bash
python3 export_static.py --output-dir site --trajectory-target chunks --trajectory-chunk-mb 5
python3 export_static.py --output-dir site --trajectory-target chunks --trajectory-chunk-mb 12
```

部署生成的 `site/` 到 Cloudflare Pages：

```bash
cd /home/ywxzml3j/ywxzml3juser57/code/harbor-dev/webui
set -a
. /home/ywxzml3j/ywxzml3juser57/.config/harbor_webui_cloudflare.env
set +a
npx --yes wrangler pages project create "$PROJECT_NAME" --production-branch "$BRANCH_NAME" || true
npx --yes wrangler pages deploy site \
  --project-name "$PROJECT_NAME" \
  --branch "$BRANCH_NAME" \
  --commit-dirty=true \
  --commit-message "Update Harbor dashboard $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
```

长期同步脚本默认使用这个免费 chunk 模式：

```bash
cd /home/ywxzml3j/ywxzml3juser57/code/harbor-dev/webui
bash run_cloudflare_pages_sync.sh
```

常用覆盖变量：

```bash
LOOP_SECONDS=600 bash run_cloudflare_pages_sync.sh
TRAJECTORY_CHUNK_MB=5 bash run_cloudflare_pages_sync.sh
PUBLIC_DIR=/tmp/harbor-webui-site bash run_cloudflare_pages_sync.sh
```

### 可选 R2 trajectory 模式

如果想要每次点击 trial 只下载一条 trajectory，可以启用 Cloudflare R2。R2 模式会让 Pages site 更小、单条 trajectory 加载更快，但可能需要在 Cloudflare Dashboard 里启用 R2/billing。

### 配置文件

创建配置文件：

```text
~/.config/harbor_webui_cloudflare.env
```

示例内容：

```bash
CLOUDFLARE_API_TOKEN="..."
CLOUDFLARE_ACCOUNT_ID="..."
PROJECT_NAME="harbor-dashboard"
BRANCH_NAME="harbor-webui"
LOOP_SECONDS="3600"
HARBOR_JOBS_DIR="/home/ywxzml3j/ywxzml3juser57/code/harbor-dev/jobs"
R2_BUCKET_NAME="harbor-trajectories"
R2_UPLOAD_WORKERS="8"
```

### 一次性创建 R2 bucket

```bash
cd /home/ywxzml3j/ywxzml3juser57/code/harbor-dev/webui
set -a
. /home/ywxzml3j/ywxzml3juser57/.config/harbor_webui_cloudflare.env
set +a
npx --yes wrangler r2 bucket create "$R2_BUCKET_NAME"
```

如果 bucket 已存在，可以忽略创建失败。

### 生成轻量静态站点

不把 trajectory 写入 `site/`，只生成 Pages 需要的轻量文件：

```bash
cd /home/ywxzml3j/ywxzml3juser57/code/harbor-dev/webui
python3 export_static.py --output-dir site --trajectory-target none
python3 -m http.server 8093 --bind 127.0.0.1 --directory site
```

然后打开：

```text
http://127.0.0.1:8093
```

注意：本地 `http.server` 不能模拟 Cloudflare Pages Worker 读取 R2，所以本地预览主要用于检查首页、job 列表和 job 详情。trajectory 按需读取需要部署到 Cloudflare Pages 后验证。

### 上传 trajectory 到 R2

```bash
cd /home/ywxzml3j/ywxzml3juser57/code/harbor-dev/webui
set -a
. /home/ywxzml3j/ywxzml3juser57/.config/harbor_webui_cloudflare.env
set +a
python3 upload_trajectories_r2.py \
  --jobs-dir "$HARBOR_JOBS_DIR" \
  --bucket "$R2_BUCKET_NAME" \
  --workers "$R2_UPLOAD_WORKERS"
```

R2 中的 trajectory object key 格式为：

```text
<job-name>/<trial-name>/trajectory_agent.json
```

### 部署到 Cloudflare Pages

```bash
cd /home/ywxzml3j/ywxzml3juser57/code/harbor-dev/webui
set -a
. /home/ywxzml3j/ywxzml3juser57/.config/harbor_webui_cloudflare.env
set +a
npx --yes wrangler pages project create "$PROJECT_NAME" --production-branch "$BRANCH_NAME" || true
npx --yes wrangler pages deploy site \
  --project-name "$PROJECT_NAME" \
  --branch "$BRANCH_NAME" \
  --commit-dirty=true \
  --commit-message "Update Harbor dashboard $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
```

部署成功后，wrangler 会输出 Cloudflare Pages 公网地址。

### 长期同步脚本

启动长期同步：

```bash
cd /home/ywxzml3j/ywxzml3juser57/code/harbor-dev/webui
bash run_cloudflare_pages_sync.sh
```

脚本会循环执行：

1. 导出轻量 Pages site；
2. 确认 R2 bucket 存在；
3. 上传 trajectories 到 R2；
4. 部署 `site/` 到 Cloudflare Pages；
5. 等待 `LOOP_SECONDS` 后再次更新。

常用覆盖变量：

```bash
LOOP_SECONDS=600 bash run_cloudflare_pages_sync.sh
PUBLIC_DIR=/tmp/harbor-webui-site bash run_cloudflare_pages_sync.sh
R2_UPLOAD_WORKERS=16 bash run_cloudflare_pages_sync.sh
```

### 说明

- `site/_worker.js` 负责处理公网 trajectory 请求，并从 `TRAJECTORIES` R2 binding 中读取对应 object。
- Cloudflare Pages 的公网 URL 对 project/branch 是稳定的，不像免费 Pinggy URL 那样容易变化。
- 新增或更新的 job/trajectory 会在下一轮同步完成后出现在公网。
- `python3 export_static.py --trajectory-target pages` 只建议本地调试使用；它会把全部 trajectory 写进 `site/`，可能生成数 GB 文件，不适合 Pages 部署。

## 使用 Pinggy 临时分享

Pinggy 仍然适合调试本地动态 Python API，但免费 tunnel 是临时的，可能超时或更换 URL。

先启动本地 dashboard：

```bash
cd /home/ywxzml3j/ywxzml3juser57/code/harbor-dev/webui
./start.sh
```

再在另一个终端启动 Pinggy tunnel：

```bash
cd /home/ywxzml3j/ywxzml3juser57/code/harbor-dev/webui
bash share_pinggy.sh
```

脚本会先检查：

```text
http://127.0.0.1:8092/
```

是否可访问，然后再打开 tunnel。如果免费 Pinggy tunnel 断开，脚本会等待一小段时间后自动重连。Pinggy 每次连接时会在终端打印当前公网 URL。

常用覆盖变量：

```bash
PORT=9000 bash share_pinggy.sh
RECONNECT_DELAY=10 bash share_pinggy.sh
PINGGY_HOST=a.pinggy.io bash share_pinggy.sh
```

注意：

- 免费 Pinggy URL 是临时的，重连后可能变化。
- 如果 Pinggy 要求输入密码，直接按 Enter。

## 架构

- **Backend**：`server.py`
  - 只依赖 Python 标准库的 HTTP server 和 JSON API。
- **Frontend**：`static/`
  - 原生 JavaScript SPA，无需构建步骤。
  - `index.html`：页面布局。
  - `app.js`：状态、路由和渲染逻辑。
  - `styles.css`：主题变量和组件样式。
  - `favicon.svg`：Harbor 图标。
- **Static export**：`export_static.py`
  - 把本地动态 dashboard 数据导出成 Cloudflare Pages 可部署的静态文件。
- **R2 trajectory upload**：`upload_trajectories_r2.py`
  - 把完整 trajectory JSON 上传到 Cloudflare R2。
- **Pages Worker**：`site/_worker.js`
  - 公网按需读取 R2 trajectory。

## API endpoints

动态服务提供以下 API。静态 Pages 版本会把其中大部分映射到静态 JSON，trajectory 则由 Worker 从 R2 按需返回。

- `GET /api/jobs`：列出所有 job 和概要统计。
- `GET /api/overview`：聚合概览，例如 job 数、scaffold、dataset、model、resolve rate。
- `GET /api/jobs/<name>`：单个 job 详情，包括配置、分析报告、trials。
- `GET /api/jobs/<name>/trials/<trial>`：单个 trial 详情。
- `GET /api/jobs/<name>/trials/<trial>/trajectory?kind=agent`：trajectory JSON。
- `GET /api/compare?name=<job1>&name=<job2>`：多个 job 的并排比较。

## 使用技巧

- 点击侧边栏 job 旁边的 `+` 按钮，可以加入 Compare 集合。
- 点击 trial 行可以查看对应 trajectory。
- 使用 job filter 输入框过滤侧边栏 job。
- 右上角按钮可以切换明暗主题。
- 浏览器 localStorage 会保存主题和 compare set。

## 开发说明

所有依赖都是 Python 标准库和 Chart.js CDN。没有 npm 构建步骤。

添加新面板时：

1. 在 `app.js` 的 `NAV` 数组添加 route。
2. 在 `render()` switch/分支中添加 case。
3. 实现对应的 `render<PanelName>()` 函数。
4. 如果 route 需要参数，更新 `applyHash()`。

分析数据 schema 主要来自 `src/harbor/analysis/` 输出：

- `report_failed.json` / `report_resolved.json`：primary/axis 分布。
- `score_comparison.json`：resolved 与 unresolved 指标对比。
- `task_analysis.json`：任务难度层级、domain/bug-type breakdown。
- `rule_score/`：每个 instance 的 composite scoring。
- `tag_analysis/`：相关性分析。

trajectory schema：ATIF v1.5，即 `agent/trajectory.json`。
