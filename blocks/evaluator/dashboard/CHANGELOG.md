# Harbor Job Dashboard — Changelog

## 2026-06-02

### Added

The single-job detail page now includes the following analyses:

#### 1. Difficulty breakdown
- Source: `analysis/instance_analysis/summary.json` → `tables.difficulty_label`
- Shows resolved and unresolved counts for each difficulty level (such as easy and medium) as a split bar chart.
- Format: `[unresolved | resolved]`, including percentages and absolute counts.
- The complete contingency table (`contingency_difficulty_label.txt`) is available in an expandable section.

#### 2. Project-type (`tag2`) breakdown
- Source: `analysis/instance_analysis/summary.json` → `tables.tag2`
- Shows resolved and unresolved counts for project types such as library, backend, framework, CLI, and testing.
- Uses a red unresolved segment on the left and a green resolved segment on the right.
- The complete contingency table (`contingency_tag2.txt`) is available in an expandable section.

#### 3. Correlations with resolve rate
- Source: `analysis/instance_analysis/correlations.json`
- Reported metrics:
  - `difficulty_score` — aggregate task-difficulty score
  - `metrics.patch_lines` — changed lines
  - `metrics.patch_files` — changed files
  - `score_dimensions.patch_scope.score` — patch-scope score
  - `score_dimensions.context_breadth.score` — context-breadth score
- Each row shows Spearman's rho, p-value, significance marker (`***`, `**`, or `*`), `mean_resolved`, and `mean_unresolved`.
- A negative correlation means that a higher metric value is associated with a lower resolve rate.
- Significance thresholds: `***` p < 0.001, `**` p < 0.01, `*` p < 0.05.

#### 4. Rule-based composite score
- Source: `analysis/traj_analysis/score_comparison.json`
- Shows mean `composite_score`, `oec_score`, and `iac_score` values for resolved and unresolved groups.
- Displays both groups side by side.

### API

Added:

```text
GET /api/jobs/<name>/rule_score_instances?kind=resolved&limit=50
```

The endpoint returns the first N resolved or unresolved instance metadata records, including each rule score's `unique_info`.

### Frontend

`renderJobDetail()` in `app.js` now:
- Reads `tag_analysis_summary`, `tag_correlations`, `contingency_difficulty`, and `contingency_tag2` from the API.
- Renders split bar charts with unresolved values on the left and resolved values on the right.
- Renders correlation tables and highlights negative correlations in red.
- Uses expandable `<details>` sections for raw contingency tables.

### Styling

Added to `styles.css`:
- `.bar-chart.split` — split red/green comparison tracks
- `.code-block` — monospaced blocks for contingency tables
- `.muted-card` — muted cards with dashed borders

### Testing

Start the server, then inspect a job response:

```bash
JOB_NAME="<job-directory-under-artifacts/jobs>"
curl "http://127.0.0.1:8092/api/jobs/$JOB_NAME" \
  | jq '.tag_analysis_summary.tables.difficulty_label'
```

Open <http://localhost:8092>, select any job, and verify the new analysis sections.
