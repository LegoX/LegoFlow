# Harbor Job Dashboard — 更新记录

## 2026-06-02 更新

### 新增功能

已为 single job detail 页面添加以下细致分析：

#### 1. **Difficulty breakdown (难度分布)**
- 数据来源: `analysis/instance_analysis/summary.json` → `tables.difficulty_label`
- 展示: 每个难度级别（easy/medium）的resolved vs unresolved split bar chart
- 格式: `[unresolved | resolved]` 对比条，显示比例和绝对数
- 可展开查看完整 contingency table (`contingency_difficulty_label.txt`)

#### 2. **Project type (tag2) breakdown**
- 数据来源: `analysis/instance_analysis/summary.json` → `tables.tag2`
- 展示: 项目类型（library/backend/framework/cli/testing）的resolved vs unresolved split bar
- 格式: 同上，左侧红色 unresolved，右侧绿色 resolved
- 可展开查看 contingency table (`contingency_tag2.txt`)

#### 3. **Correlations with resolve rate (相关性分析)**
- 数据来源: `analysis/instance_analysis/correlations.json`
- 展示指标:
  - `difficulty_score` — 任务难度综合评分
  - `metrics.patch_lines` — 补丁代码行数
  - `metrics.patch_files` — 修改文件数
  - `score_dimensions.patch_scope.score` — 补丁范围评分
  - `score_dimensions.context_breadth.score` — 上下文广度评分
- 每行显示: Spearman ρ（相关系数）, p-value, 显著性标记(*** / ** / *), mean_resolved, mean_unresolved
- 负相关 = 该指标越高，resolve rate 越低
- 统计显著性: *** p<0.001, ** p<0.01, * p<0.05

#### 4. **Rule-based composite score**
- 数据来源: `analysis/traj_analysis/score_comparison.json`
- 展示: resolved vs unresolved 的 composite_score, oec_score, iac_score 均值
- 对比: 两组数据并排展示

### API 增强

新增端点:
```
GET /api/jobs/<name>/rule_score_instances?kind=resolved&limit=50
```
返回 resolved 或 unresolved 的前 N 条 instance metadata（包含 rule score 的 unique_info）

### 前端渲染

`app.js` 中 `renderJobDetail()` 函数已扩展:
- 从 API 读取 `tag_analysis_summary`, `tag_correlations`, `contingency_difficulty`, `contingency_tag2`
- 渲染 split bar chart (两条轨道，left=unresolved, right=resolved)
- 渲染 correlation 表格，negative correlation 用红色标注
- 支持 `<details>` 折叠查看原始 contingency table

### 样式

`styles.css`:
- `.bar-chart.split` — 左右分开的轨道，红色/绿色对比
- `.code-block` — 用于展示 contingency table 的等宽字体 pre 块
- `.muted-card` — 灰色虚线边框的提示卡片

## 测试

服务器已重启（端口 8092），新数据已可访问:
```bash
JOB_NAME="<artifacts/jobs 下的 job 目录名>"
curl "http://127.0.0.1:8092/api/jobs/$JOB_NAME" \
  | jq '.tag_analysis_summary.tables.difficulty_label'
```

打开浏览器访问 http://localhost:8092，点击任意 job，查看新增的分析模块。
