// Harbor Job Dashboard — single-page client.
// State, routing, rendering for: overview / jobs / single-job / compare / trajectory.

const State = {
  jobs: [],
  jobsDetail: "none",     // none | lite | full
  jobsLitePromise: null,
  jobsFullPromise: null,
  jobsLoading: false,
  filter: "",
  selected: null,         // job name shown in single-job view
  view: "overview",       // overview | jobs | job | compare | trajectory
  compareSet: new Set(JSON.parse(localStorage.getItem("harbor.compare") || "[]")),
  detailCache: {},
  trialDetail: null,
  trajectory: null,
  activeStep: 0,
  theme: localStorage.getItem("harbor.theme") || "dark",
  lang: localStorage.getItem("harbor.lang") || "en",
  charts: [],
  chartTimers: [],
  trajectoryChunkManifest: null,
  trajectoryChunkManifestPromise: null,
  trajectoryChunkCache: {},
  trialDetailChunkManifest: null,
  trialDetailChunkManifestPromise: null,
  trialDetailChunkCache: {},
};

const NAV = [
  { id: "overview", label: "Overview", labelZh: "总览", icon: "▦" },
  { id: "jobs", label: "All jobs", labelZh: "全部任务", icon: "▤" },
  { id: "job", label: "Single job", labelZh: "单个任务", icon: "◧", needsJob: true },
  { id: "compare", label: "Compare", labelZh: "对比", icon: "◊" },
  { id: "trajectory", label: "Trajectory", labelZh: "轨迹", icon: "↗", needsJob: true },
];

const I18N = {
  en: {
    jobs: "Jobs",
    filterJobs: "Filter jobs…",
    compare: "Compare",
    openCompare: "Open compare",
    clear: "Clear",
    compareHelp: "Use the + button next to a job to add it to Compare.",
    reload: "Reload",
    toggleTheme: "Toggle theme",
    switchLanguage: "切换到中文",
    langButton: "中",
  },
  zh: {
    jobs: "任务",
    filterJobs: "筛选任务…",
    compare: "对比",
    openCompare: "打开对比",
    clear: "清空",
    compareHelp: "点击任务旁边的 + 按钮加入对比。",
    reload: "刷新",
    toggleTheme: "切换主题",
    switchLanguage: "Switch to English",
    langButton: "EN",
  },
};

// -- utilities ----------------------------------------------------------------

function $(sel, root = document) { return root.querySelector(sel); }
function $$(sel, root = document) { return [...root.querySelectorAll(sel)]; }
function t(key) { return (I18N[State.lang] || I18N.en)[key] || I18N.en[key] || key; }
function navLabel(item) { return State.lang === "zh" ? (item.labelZh || item.label) : item.label; }
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null || c === false || c === true) continue;
    if (typeof c === "string" || typeof c === "number") node.appendChild(document.createTextNode(c));
    else node.appendChild(c);
  }
  return node;
}

function destroyCharts() {
  // Cancel pending deferred chart renders too, so timers scheduled by a previous
  // view don't fire after re-render and draw onto a detached canvas / leak.
  for (const t of State.chartTimers) clearTimeout(t);
  State.chartTimers = [];
  for (const c of State.charts) { try { c.destroy(); } catch (_) {} }
  State.charts = [];
}

// Defer a chart render to the next tick (so its canvas is in the DOM) while
// tracking the timer id, so destroyCharts() can cancel it on re-render.
function scheduleChart(fn) {
  const id = setTimeout(() => {
    State.chartTimers = State.chartTimers.filter(t => t !== id);
    fn();
  }, 0);
  State.chartTimers.push(id);
}

function jobsDetailRank(detail) {
  return detail === "full" ? 2 : detail === "lite" ? 1 : 0;
}

function syncJobsLoading() {
  State.jobsLoading = Boolean(State.jobsLitePromise || State.jobsFullPromise);
}

async function api(path) {
  const resp = await fetch(path);
  if (resp.ok) {
    const contentType = resp.headers.get("content-type") || "";
    if (contentType.includes("application/json")) return await resp.json();

    // Cloudflare Pages may serve index.html with HTTP 200 for extensionless
    // API-like paths such as /api/overview. Treat that as a static-site miss
    // and fall back to the exported JSON file below.
    if (!path.startsWith("/api/")) return await resp.json();
  }

  const staticPath = staticApiPath(path);
  if (staticPath && staticPath !== path) {
    const staticResp = await fetch(staticPath);
    if (staticResp.ok) return await staticResp.json();
    const chunkPayload = await staticApiChunkPayload(path);
    if (chunkPayload) return chunkPayload;
    const txt = await staticResp.text();
    throw new Error(`${staticPath}: ${staticResp.status} ${txt.slice(0, 120)}`);
  }

  const txt = await resp.text();
  throw new Error(`${path}: ${resp.status} ${txt.slice(0, 120)}`);
}

function staticApiPath(path) {
  if (!path.startsWith("/api/")) return null;
  const url = new URL(path, location.origin);
  const parts = url.pathname.split("/").filter(Boolean);

  if (url.pathname === "/api/jobs") {
    return url.searchParams.get("detail") === "lite" ? "/api/jobs_lite.json" : "/api/jobs.json";
  }
  if (url.pathname === "/api/overview") return "/api/overview.json";

  if (parts[0] === "api" && parts[1] === "jobs" && parts[2]) {
    const job = encodeURIComponent(decodeURIComponent(parts[2]));
    if (parts.length === 3) return `/api/jobs/${job}/index.json`;
    if (parts[3] === "trials") {
      if (parts.length === 4) return `/api/jobs/${job}/trials.json`;
      const trial = encodeURIComponent(decodeURIComponent(parts[4] || ""));
      if (parts.length === 5) return `/api/jobs/${job}/trials/${trial}.json`;
      if (parts[5] === "trajectory") {
        const kind = url.searchParams.get("kind") || "agent";
        return `/api/jobs/${job}/trials/${trial}/trajectory_${encodeURIComponent(kind)}.json`;
      }
    }
    if (parts[3] === "rule_score_instances") {
      const kind = url.searchParams.get("kind") || "resolved";
      const limit = url.searchParams.get("limit") || "50";
      return `/api/jobs/${job}/rule_score_instances_${encodeURIComponent(kind)}_${encodeURIComponent(limit)}.json`;
    }
  }

  return null;
}

function trialDetailRequest(path) {
  if (!path.startsWith("/api/")) return null;
  const url = new URL(path, location.origin);
  const parts = url.pathname.split("/").filter(Boolean);
  if (parts[0] === "api" && parts[1] === "jobs" && parts[2] && parts[3] === "trials" && parts.length === 5) {
    return {
      job: decodeURIComponent(parts[2]),
      trial: decodeURIComponent(parts[4] || ""),
    };
  }
  return null;
}

async function staticApiChunkPayload(path) {
  const trial = trialDetailRequest(path);
  if (trial) return await loadTrialDetailFromChunks(trial.job, trial.trial);
  return null;
}

async function loadTrialDetailFromChunks(jobName, trialName) {
  if (!State.trialDetailChunkManifestPromise) {
    State.trialDetailChunkManifestPromise = fetch("/trial_detail_chunks/manifest.json")
      .then(async (resp) => {
        if (!resp.ok) throw new Error(`trial detail chunk manifest: ${resp.status}`);
        State.trialDetailChunkManifest = await resp.json();
        return State.trialDetailChunkManifest;
      });
  }

  const manifest = await State.trialDetailChunkManifestPromise;
  const entry = manifest?.jobs?.[jobName]?.[trialName];
  if (!entry?.path) throw new Error("trial detail chunk not found");

  if (!State.trialDetailChunkCache[entry.path]) {
    State.trialDetailChunkCache[entry.path] = fetch(entry.path).then(async (resp) => {
      if (!resp.ok) throw new Error(`${entry.path}: ${resp.status}`);
      return await resp.json();
    });
  }

  const chunk = await State.trialDetailChunkCache[entry.path];
  const detail = chunk?.trials?.[trialName];
  if (!detail) throw new Error(`trial detail not found in ${entry.path}`);
  return detail;
}

function fmtNum(n) {
  if (n == null) return "—";
  if (typeof n !== "number") return String(n);
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toFixed(2);
}

function fmtPct(n) { return n == null ? "—" : `${(+n).toFixed(1)}%`; }

function fmtDuration(sec) {
  if (sec == null) return "—";
  if (sec < 60) return `${sec.toFixed(1)}s`;
  if (sec < 3600) return `${(sec / 60).toFixed(1)}m`;
  return `${(sec / 3600).toFixed(2)}h`;
}

function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1000) return `${n}`;
  if (n < 1e6) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1e6).toFixed(2)}M`;
}

function shortLabel(text, maxLength = 28) {
  const value = String(text || "");
  if (value.length <= maxLength) return value;
  return `${value.slice(0, Math.max(0, maxLength - 3))}...`;
}

function fmtScore(n) {
  return n == null ? "—" : `${(+n).toFixed(3)}`;
}

function fmtSignedScore(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n > 0 ? "+" : ""}${(+n).toFixed(3)}`;
}

function fmtPValue(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return n < 0.001 ? "<0.001" : (+n).toFixed(3);
}

function exceptionTypeOfInfo(exceptionInfo) {
  if (!exceptionInfo) return null;
  if (typeof exceptionInfo === "string") {
    const head = exceptionInfo.trim().split(/\r?\n/)[0] || "";
    return head.split(":", 1)[0] || null;
  }
  if (typeof exceptionInfo === "object") {
    return exceptionInfo.exception_type || exceptionInfo.type || exceptionInfo.class || exceptionInfo.name || "Exception";
  }
  return String(exceptionInfo);
}

function exceptionTypeOfTrial(trial) {
  return trial?.exception_type || exceptionTypeOfInfo(trial?.exception_info);
}

function exceptionStatsEntries(stats) {
  return Object.entries(stats || {})
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function aggregateExceptionStats(trials) {
  const stats = {};
  for (const trial of trials || []) {
    const type = exceptionTypeOfTrial(trial);
    if (type) stats[type] = (stats[type] || 0) + 1;
  }
  return stats;
}

function aggregateTruncationStats(trials) {
  const stats = {};
  for (const trial of trials || []) {
    const truncation = trial?.truncation;
    if (truncation) stats[truncation] = (stats[truncation] || 0) + 1;
  }
  return stats;
}

function renderExceptionPill(type, count) {
  if (!type) return el("span", { class: "muted" }, ["—"]);
  return el("span", { class: "pill exception", title: count == null ? type : `${type}: ${count}` }, [
    count == null ? type : `${type} × ${count}`,
  ]);
}

function renderTruncationPill(truncation, count) {
  if (!truncation) return el("span", { class: "muted" }, ["—"]);
  const cls = truncation === "max length" ? "pill bad" : "pill warn";
  return el("span", { class: cls, title: count == null ? truncation : `${truncation}: ${count}` }, [
    count == null ? truncation : `${truncation} × ${count}`,
  ]);
}

function trialTokenTotal(trial) {
  return trial?.n_tokens ?? trial?.total_tokens ?? trial?.token_summary?.total_tokens;
}

function trajectoryTokenTotal(trajectory) {
  return trajectory?.token_summary?.total_tokens ?? trajectory?.n_tokens ?? trajectory?.total_tokens;
}

function avgNumeric(values) {
  const nums = (values || []).filter(v => typeof v === "number" && Number.isFinite(v));
  if (nums.length === 0) return null;
  return nums.reduce((sum, v) => sum + v, 0) / nums.length;
}

function avgPositiveNumeric(values) {
  const nums = (values || []).filter(v => typeof v === "number" && Number.isFinite(v) && v > 0);
  if (nums.length === 0) return null;
  return nums.reduce((sum, v) => sum + v, 0) / nums.length;
}

function averageTrialMetrics(trials) {
  return {
    turns: avgPositiveNumeric((trials || []).map(t => t.turn_count)),
    duration: avgNumeric((trials || []).map(t => t.duration_sec)),
    tokens: avgPositiveNumeric((trials || []).map(t => trialTokenTotal(t))),
  };
}

function trialLimitCounts(trials) {
  const rows = trials || [];
  return {
    hitMaxTurn: rows.filter(t => t.hit_max_turn).length,
    hitMaxLength: rows.filter(t => t.hit_max_length).length,
  };
}

function renderTrialSummaryMetrics(metrics, limits) {
  return el("div", { class: "avg-metrics" }, [
    el("div", { class: "avg-row" }, [
      el("span", {}, ["Avg Turns"]),
      el("span", { class: "mono" }, [fmtNum(metrics.turns)]),
    ]),
    el("div", { class: "avg-row" }, [
      el("span", {}, ["Avg Tokens"]),
      el("span", { class: "mono" }, [fmtBytes(metrics.tokens)]),
    ]),
    el("div", { class: "avg-row" }, [
      el("span", {}, ["Avg Duration"]),
      el("span", { class: "mono" }, [fmtDuration(metrics.duration)]),
    ]),
    el("div", { class: "avg-row" }, [
      el("span", {}, ["Hit Max Turn"]),
      el("span", { class: "mono" }, [fmtNum(limits.hitMaxTurn)]),
    ]),
    el("div", { class: "avg-row" }, [
      el("span", {}, ["Hit Max Length"]),
      el("span", { class: "mono" }, [fmtNum(limits.hitMaxLength)]),
    ]),
  ]);
}

function chartPercentYAxisRange(values) {
  const nums = (values || []).filter(v => typeof v === "number" && !Number.isNaN(v));
  if (nums.length === 0) return { min: 0, max: 100 };
  const minValue = Math.min(...nums);
  const maxValue = Math.max(...nums);
  if (minValue === maxValue) {
    const pad = Math.max(5, Math.abs(maxValue) * 0.12);
    return {
      min: Math.max(0, Math.floor((minValue - pad) / 5) * 5),
      max: Math.min(100, Math.ceil((maxValue + pad) / 5) * 5),
    };
  }
  const span = maxValue - minValue;
  const pad = Math.max(2, span * 0.15);
  const rawMin = Math.max(0, minValue - pad);
  const rawMax = Math.min(100, maxValue + pad);
  const step = span <= 20 ? 5 : 10;
  return {
    min: Math.max(0, Math.floor(rawMin / step) * step),
    max: Math.min(100, Math.ceil(rawMax / step) * step),
  };
}

function niceChartStep(value) {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
  const normalized = value / magnitude;
  if (normalized <= 1) return magnitude;
  if (normalized <= 2) return 2 * magnitude;
  if (normalized <= 5) return 5 * magnitude;
  return 10 * magnitude;
}

function chartNumericYAxisRange(values) {
  const nums = (values || []).filter(v => typeof v === "number" && Number.isFinite(v));
  if (nums.length === 0) return { min: 0, max: 1 };
  const minValue = Math.min(...nums);
  const maxValue = Math.max(...nums);
  const span = maxValue - minValue;
  const pad = span === 0
    ? (maxValue === 0 ? 1 : Math.abs(maxValue) * 0.12)
    : Math.max(span * 0.15, Math.abs(maxValue) * 0.02);
  const rawMin = Math.max(0, minValue - pad);
  let rawMax = maxValue + pad;
  if (rawMax <= rawMin) rawMax = rawMin + (maxValue === 0 ? 1 : Math.abs(maxValue) * 0.1);
  const step = niceChartStep((rawMax - rawMin) / 5);
  return {
    min: Math.max(0, Math.floor(rawMin / step) * step),
    max: Math.ceil(rawMax / step) * step,
  };
}

function startCase(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function fmtDiffPct(value) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${(+value).toFixed(1)}pp`;
}

function pickTopComparisonRows(failedMap, resolvedMap, limit = 6, preferredKeys = []) {
  const keys = new Set([
    ...Object.keys(failedMap || {}),
    ...Object.keys(resolvedMap || {}),
  ]);
  const rows = [...keys].map((key) => {
    const failed = failedMap?.[key] ?? 0;
    const resolved = resolvedMap?.[key] ?? 0;
    return {
      key,
      failed,
      resolved,
      diff: resolved - failed,
      absDiff: Math.abs(resolved - failed),
      preferredRank: preferredKeys.indexOf(key),
    };
  });
  rows.sort((a, b) => {
    const aPreferred = a.preferredRank === -1 ? Number.MAX_SAFE_INTEGER : a.preferredRank;
    const bPreferred = b.preferredRank === -1 ? Number.MAX_SAFE_INTEGER : b.preferredRank;
    if (aPreferred !== bPreferred) return aPreferred - bPreferred;
    if (b.absDiff !== a.absDiff) return b.absDiff - a.absDiff;
    return a.key.localeCompare(b.key);
  });
  return rows.slice(0, limit);
}

const INVERTED_ERROR_METRIC_KEYS = new Set([
  "loop_detected",
  "tool_error_storm",
  "context_truncation",
]);

const EXCLUDED_ERROR_METRIC_KEYS = new Set([
  "premature_stop",
]);

function normalizeErrorMetricKey(key) {
  return String(key || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function isInvertedErrorMetric(key) {
  return INVERTED_ERROR_METRIC_KEYS.has(normalizeErrorMetricKey(key));
}

function isExcludedErrorMetric(key) {
  return EXCLUDED_ERROR_METRIC_KEYS.has(normalizeErrorMetricKey(key));
}

function displayErrorMetricValue(key, value) {
  const num = Number(value);
  const safe = Number.isFinite(num) ? num : 0;
  return isInvertedErrorMetric(key) ? 100 - safe : safe;
}

function buildDeterministicFeatureRows(scoreComp) {
  const failedMap = scoreComp?.feature_averages?.failed || {};
  const resolvedMap = scoreComp?.feature_averages?.resolved || {};
  const keys = [...new Set([...Object.keys(failedMap), ...Object.keys(resolvedMap)])]
    .filter(key => !isExcludedErrorMetric(key));
  return keys.map((key) => {
    const failed = displayErrorMetricValue(key, failedMap[key] ?? 0);
    const resolved = displayErrorMetricValue(key, resolvedMap[key] ?? 0);
    return {
      key,
      failed,
      resolved,
      average: (failed + resolved) / 2,
      diff: resolved - failed,
      absDiff: Math.abs(resolved - failed),
    };
  });
}

const COMPARE_ERROR_METRIC_KEYS = [
  "c1_file_read",
  "c2_func_read",
  "c3_file_alignment",
  "c4_func_alignment",
  "c5_test_executed",
  "diff_hunk_overlap",
  "loop_detected",
  "tool_error_storm",
  "context_truncation",
];

function hexToRgba(hex, alpha) {
  const value = String(hex || "").trim();
  const match = value.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
  if (!match) return value;
  const r = parseInt(match[1], 16);
  const g = parseInt(match[2], 16);
  const b = parseInt(match[3], 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function compareJobPalette(styles = getComputedStyle(document.documentElement)) {
  return [
    // Categorical slots from DASHBOARD_PALETTE.md, in their fixed order. The brand
    // accent and the status colors are deliberately not used here — neither may
    // stand in for a series.
    styles.getPropertyValue("--c-series-1").trim() || "#d95926",
    styles.getPropertyValue("--c-series-2").trim() || "#199e70",
    styles.getPropertyValue("--c-series-3").trim() || "#3987e5",
    styles.getPropertyValue("--c-series-4").trim() || "#c98500",
    styles.getPropertyValue("--c-series-5").trim() || "#d55181",
    styles.getPropertyValue("--c-series-6").trim() || "#008300",
    styles.getPropertyValue("--c-series-7").trim() || "#9085e9",
  ];
}

function getDeterministicFeatureMeta(key) {
  const rawKey = String(key || "");
  const normalized = rawKey.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const explicit = {
    c1_file_read: {
      label: "C1_file_read",
      description: "Viewed a gold-patch file. Higher is better.",
    },
    c2_func_read: {
      label: "C2_func_read",
      description: "Viewed a gold-patch function/class. Higher is better.",
    },
    c3_file_alignment: {
      label: "C3_file_edit",
      description: "Edited a gold-patch file. Higher is better.",
    },
    c4_func_alignment: {
      label: "C4_func_edit",
      description: "Edited a gold-patch function/class. Higher is better.",
    },
    c5_test_executed: {
      label: "C5_test_executed",
      description: "Ran test commands. Higher is better.",
    },
    diff_hunk_overlap: {
      label: "diff_hunk_overlap",
      description: "Hunk overlap with the gold patch. Higher is better.",
    },
    diff_line_delta: {
      label: "diff_line_delta",
      description: "Line-count delta from the gold patch. Lower is closer.",
    },
    diff_file_delta: {
      label: "diff_file_delta",
      description: "File-count delta from the gold patch. Lower is closer.",
    },
    modified_test_file: {
      label: "modified_test_file",
      description: "Changed tests; possible reward-hack signal.",
    },
    trajectory_length: {
      label: "trajectory_length",
      description: "Number of trajectory steps.",
    },
    max_iterations: {
      label: "max_iterations",
      description: "Maximum iteration budget.",
    },
    file_overlap_count: {
      label: "file_overlap_count",
      description: "Files shared with the gold patch.",
    },
    func_overlap_count: {
      label: "func_overlap_count",
      description: "Functions/classes shared with the gold patch.",
    },
    loop_detected: {
      label: "loop_detected",
      description: "No action loops. Higher is better.",
    },
    tool_error_storm: {
      label: "tool_error_storm",
      description: "No tool error storms. Higher is better.",
    },
    context_truncation: {
      label: "context_truncation",
      description: "No context truncation. Higher is better.",
    },
    model_patch_exists: {
      label: "model_patch_exists",
      description: "Produced a non-empty model patch. Higher is better.",
    },
  };

  if (explicit[normalized]) {
    return explicit[normalized];
  }

  if (normalized.includes("overlap")) {
    return {
      label: rawKey,
      description: "Gold-patch overlap metric. Usually higher is better.",
    };
  }
  if (normalized.includes("pathology")) {
    return {
      label: rawKey,
      description: "Trajectory health metric. Higher is better.",
    };
  }

  return {
    label: rawKey,
    description: "",
  };
}

function buildKeyFindings(scoreComp) {
  return buildDeterministicFeatureRows(scoreComp)
    .filter((row) => row.absDiff > 0)
    .sort((a, b) => b.absDiff - a.absDiff || a.key.localeCompare(b.key))
    .slice(0, 4)
    .map((row) => ({
      label: getDeterministicFeatureMeta(row.key).label,
      text: `resolved ${row.resolved.toFixed(1)}% vs failed ${row.failed.toFixed(1)}% (${fmtDiffPct(row.diff)})`,
    }));
}

function fmtDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (part) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function setStatus(msg) { $("#status").textContent = msg || ""; }

function persistCompare() {
  localStorage.setItem("harbor.compare", JSON.stringify([...State.compareSet]));
  $("#compare-count").textContent = State.compareSet.size;
}

function refreshCompareUi() {
  persistCompare();
  if (State.view === "compare") render();
  else renderJobList();
}

function toggleCompareJob(jobName) {
  if (State.compareSet.has(jobName)) State.compareSet.delete(jobName);
  else State.compareSet.add(jobName);
  refreshCompareUi();
}

function applyLanguage() {
  document.documentElement.setAttribute("lang", State.lang === "zh" ? "zh-CN" : "en");
  const jobsLabel = $("#jobs-section-label");
  if (jobsLabel) jobsLabel.textContent = t("jobs");
  const jobFilter = $("#job-filter");
  if (jobFilter) jobFilter.placeholder = t("filterJobs");
  const compareLabel = $("#compare-label");
  if (compareLabel) compareLabel.textContent = t("compare");
  const openCompare = $("#open-compare");
  if (openCompare) openCompare.textContent = t("openCompare");
  const clearCompare = $("#clear-compare");
  if (clearCompare) clearCompare.textContent = t("clear");
  const compareHelp = $("#compare-help");
  if (compareHelp) compareHelp.textContent = t("compareHelp");
  const reload = $("#reload");
  if (reload) reload.title = t("reload");
  const themeToggle = $("#theme-toggle");
  if (themeToggle) themeToggle.title = t("toggleTheme");
  const langToggle = $("#lang-toggle");
  if (langToggle) {
    langToggle.textContent = t("langButton");
    langToggle.title = t("switchLanguage");
  }
}

// -- routing ------------------------------------------------------------------

function readHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  if (!raw) return { view: "overview" };
  const parts = raw.split("/");
  if (parts[0] === "job") return { view: "job", job: decodeURIComponent(parts[1] || "") };
  if (parts[0] === "compare") return { view: "compare" };
  if (parts[0] === "jobs") return { view: "jobs" };
  if (parts[0] === "trajectory")
    return {
      view: "trajectory",
      job: decodeURIComponent(parts[1] || ""),
      trial: decodeURIComponent(parts[2] || ""),
    };
  return { view: "overview" };
}

function setHash(parts) {
  location.hash = "#/" + parts.map(encodeURIComponent).join("/");
}

window.addEventListener("hashchange", () => { applyHash(); });

async function applyHash() {
  const r = readHash();
  State.view = r.view;
  if (r.view === "job") State.selected = r.job;
  if (r.view === "trajectory") {
    State.selected = r.job;
    await loadTrajectory(r.job, r.trial);
  }
  await render();
}

// -- data loaders -------------------------------------------------------------

function applyJobsPayload(data, detail) {
  if (jobsDetailRank(detail) >= jobsDetailRank(State.jobsDetail)) {
    State.jobs = data.jobs || [];
    State.jobsDetail = detail;
  }
  renderJobList();
  if (detail === "full") {
    setStatus(`${State.jobs.length} jobs · ${data.jobs_dir}`);
  } else if (State.jobs.length > 0) {
    setStatus(`${State.jobs.length} jobs · sidebar ready`);
  }
}

async function loadJobs(detail = "lite") {
  if (jobsDetailRank(State.jobsDetail) >= jobsDetailRank(detail)) return State.jobs;

  if (detail === "full") {
    if (State.jobsFullPromise) return State.jobsFullPromise;
    const promise = api("/api/jobs?detail=full")
      .then((data) => {
        applyJobsPayload(data, "full");
        return data.jobs || [];
      })
      .finally(() => {
        State.jobsFullPromise = null;
        syncJobsLoading();
        renderJobList();
      });
    State.jobsFullPromise = promise;
    syncJobsLoading();
    renderJobList();
    return promise;
  }

  if (State.jobsLitePromise) return State.jobsLitePromise;
  const promise = api("/api/jobs?detail=lite")
    .then((data) => {
      applyJobsPayload(data, "lite");
      return data.jobs || [];
    })
    .finally(() => {
      State.jobsLitePromise = null;
      syncJobsLoading();
      renderJobList();
    });
  State.jobsLitePromise = promise;
  syncJobsLoading();
  renderJobList();
  return promise;
}

async function getJobDetail(name) {
  if (State.detailCache[name]) return State.detailCache[name];
  const d = await api(`/api/jobs/${encodeURIComponent(name)}`);
  State.detailCache[name] = d;
  return d;
}

async function loadJobTrialsForCompare(name) {
  try {
    const payload = await api(`/api/jobs/${encodeURIComponent(name)}/trials`);
    if (Array.isArray(payload?.trials)) return payload.trials;
  } catch (_) {
    // Fall back to job detail below. Static deployments may serve either shape.
  }
  try {
    const detail = await getJobDetail(name);
    return Array.isArray(detail?.trials) ? detail.trials : [];
  } catch (_) {
    return [];
  }
}

async function ensureCompareTrialData(data, names) {
  const jobs = await Promise.all((data.jobs || []).map(async (job) => {
    if (Array.isArray(job.trials) && job.trials.length > 0) return job;
    const name = job.name;
    if (!name || !names.includes(name)) return job;
    return { ...job, trials: await loadJobTrialsForCompare(name) };
  }));
  return { ...data, jobs };
}

async function loadTrajectoryFromChunks(jobName, trialName) {
  if (!State.trajectoryChunkManifestPromise) {
    State.trajectoryChunkManifestPromise = fetch("/trajectory_chunks/manifest.json")
      .then(async (resp) => {
        if (!resp.ok) throw new Error(`trajectory chunk manifest: ${resp.status}`);
        State.trajectoryChunkManifest = await resp.json();
        return State.trajectoryChunkManifest;
      });
  }

  const manifest = await State.trajectoryChunkManifestPromise;
  const entry = manifest?.jobs?.[jobName]?.[trialName];
  if (!entry?.path) throw new Error("trajectory chunk not found");

  if (!State.trajectoryChunkCache[entry.path]) {
    State.trajectoryChunkCache[entry.path] = fetch(entry.path).then(async (resp) => {
      if (!resp.ok) throw new Error(`${entry.path}: ${resp.status}`);
      return await resp.json();
    });
  }

  const chunk = await State.trajectoryChunkCache[entry.path];
  const trajectory = chunk?.trajectories?.[trialName];
  if (!trajectory) throw new Error(`trajectory not found in ${entry.path}`);
  return trajectory;
}

async function loadTrajectory(jobName, trialName) {
  setStatus(`Loading trajectory ${trialName}…`);
  try {
    let data;
    try {
      data = await api(
        `/api/jobs/${encodeURIComponent(jobName)}/trials/${encodeURIComponent(trialName)}/trajectory?kind=agent`
      );
    } catch (apiError) {
      data = await loadTrajectoryFromChunks(jobName, trialName);
    }
    State.trajectory = { job: jobName, trial: trialName, data };
    State.activeStep = 0;
    setStatus(`${data?.step_count ?? 0} steps`);
  } catch (e) {
    State.trajectory = { job: jobName, trial: trialName, error: e.message };
    setStatus(e.message);
  }
}

async function buildStaticCompareData(names) {
  const jobs = await Promise.all(names.map(async (name) => {
    try {
      const detail = await getJobDetail(name);
      return {
        name,
        summary: detail.summary,
        score_comparison: detail.score_comparison,
        rule_score: detail.rule_score,
        task_analysis: detail.task_analysis,
        tag_analysis_summary: detail.tag_analysis_summary,
        primary_axes: {
          failed: detail.report_failed?.primary_distribution?.rows || [],
          resolved: detail.report_resolved?.primary_distribution?.rows || [],
        },
        axis_distributions_failed: detail.report_failed?.axis_distributions || {},
        axis_distributions_resolved: detail.report_resolved?.axis_distributions || {},
        trials: detail.trials || [],
      };
    } catch (e) {
      return { name, error: e.message };
    }
  }));
  return { jobs };
}

// -- render -------------------------------------------------------------------

async function render() {
  destroyCharts();
  renderNav();
  renderJobList();
  renderCrumbs();
  persistCompare();

  const content = $("#content");
  content.innerHTML = "";

  // Wrap per-view data loads: a single failed fetch must not leave the content
  // pane permanently blank — show a visible error card instead.
  try {
    if (State.view === "overview") {
      const ov = await api("/api/overview");
      content.appendChild(renderOverview(ov));
    } else if (State.view === "jobs") {
      if (State.jobsDetail !== "full") {
        await loadJobs("full");
      }
      content.appendChild(renderJobsPanel());
    } else if (State.view === "job" && State.selected) {
      const detail = await getJobDetail(State.selected);
      content.appendChild(renderJobDetail(detail));
    } else if (State.view === "compare") {
      if (State.compareSet.size > 0) {
        const names = [...State.compareSet];
        let data;
        try {
          data = await api(`/api/compare?${names.map(n => `name=${encodeURIComponent(n)}`).join("&")}`);
        } catch (_) {
          data = await buildStaticCompareData(names);
        }
        data = await ensureCompareTrialData(data, names);
        content.appendChild(renderCompare(data));
      } else {
        content.appendChild(renderCompareEmpty());
      }
    } else if (State.view === "trajectory" && State.trajectory) {
      content.appendChild(renderTrajectory());
    } else {
      content.appendChild(el("div", { class: "empty" }, ["Select a job or view from the sidebar"]));
    }
  } catch (err) {
    content.innerHTML = "";
    content.appendChild(el("div", { class: "empty" }, [
      el("div", {}, ["Failed to load this view."]),
      el("div", { class: "muted", style: "margin-top:8px" }, [String((err && err.message) || err)]),
    ]));
  }
}

function renderNav() {
  const nav = $("#nav");
  nav.innerHTML = "";
  for (const item of NAV) {
    if (item.needsJob && !State.selected && item.id !== "compare") continue;
    const btn = el("button", {
      class: "nav-item" + (State.view === item.id ? " active" : ""),
      onclick: () => {
        if (item.id === "overview") setHash([]);
        else if (item.id === "jobs") setHash(["jobs"]);
        else if (item.id === "job" && State.selected) setHash(["job", State.selected]);
        else if (item.id === "compare") setHash(["compare"]);
        else if (item.id === "trajectory" && State.trajectory)
          setHash(["trajectory", State.trajectory.job, State.trajectory.trial]);
        else if (item.id === "trajectory" && State.selected) setHash(["job", State.selected]);
      },
    }, [item.icon + " " + navLabel(item)]);
    nav.appendChild(btn);
  }
}

function renderJobList() {
  const list = $("#job-list");
  list.innerHTML = "";
  if (State.jobs.length === 0 && State.jobsLoading) {
    list.appendChild(el("div", { class: "empty", style: "padding: 20px 10px; font-size: 11px;" }, ["Loading jobs…"]));
    return;
  }
  const flt = State.filter.toLowerCase();
  const filtered = flt ? State.jobs.filter(j => j.name.toLowerCase().includes(flt)) : State.jobs;
  for (const j of filtered) {
    const isActive = State.selected === j.name;
    const isCompare = State.compareSet.has(j.name);
    const row = el("div", {
      class: "job-row" + (isActive ? " active" : "") + (isCompare ? " compare-on" : ""),
    }, [
      el("button", {
        class: "job-main",
        type: "button",
        title: j.name,
        onclick: (e) => {
          if (e.shiftKey) {
            toggleCompareJob(j.name);
            return;
          }
          State.selected = j.name;
          setHash(["job", j.name]);
        },
      }, [
        el("span", { class: "job-name" }, [j.name]),
        el("div", { class: "job-meta" }, [
          j.scaffold && el("span", { class: "pill" }, [j.scaffold]),
          j.dataset && el("span", { class: "pill" }, [j.dataset]),
          j.analysis && el("span", { class: "pill " + (j.analysis.resolve_rate >= 50 ? "good" : "bad") }, [
            `${fmtPct(j.analysis.resolve_rate)}`
          ]),
        ]),
      ]),
      el("button", {
        class: "compare-toggle" + (isCompare ? " on" : ""),
        type: "button",
        title: isCompare ? `Remove ${j.name} from compare` : `Add ${j.name} to compare`,
        "aria-pressed": isCompare ? "true" : "false",
        onclick: () => toggleCompareJob(j.name),
      }, [isCompare ? "✓" : "+"]),
    ]);
    list.appendChild(row);
  }
  if (filtered.length === 0) {
    list.appendChild(el("div", { class: "empty", style: "padding: 20px 10px; font-size: 11px;" }, ["No jobs match filter"]));
  }
}

function renderCrumbs() {
  const crumbs = $("#crumbs");
  crumbs.innerHTML = "";
  if (State.view === "overview") {
    crumbs.appendChild(el("span", { class: "here" }, ["Overview"]));
  } else if (State.view === "jobs") {
    crumbs.appendChild(el("span", { class: "here" }, ["All jobs"]));
  } else if (State.view === "job" && State.selected) {
    crumbs.appendChild(el("span", {}, ["Job"]));
    crumbs.appendChild(el("span", { class: "sep" }, ["›"]));
    crumbs.appendChild(el("span", { class: "here" }, [State.selected]));
  } else if (State.view === "compare") {
    crumbs.appendChild(el("span", { class: "here" }, ["Compare"]));
  } else if (State.view === "trajectory" && State.trajectory) {
    crumbs.appendChild(el("button", {
      class: "crumb-link",
      type: "button",
      onclick: () => setHash(["job", State.trajectory.job]),
      title: `Back to ${State.trajectory.job}`,
    }, [State.trajectory.job]));
    crumbs.appendChild(el("span", { class: "sep" }, ["›"]));
    crumbs.appendChild(el("span", { class: "here" }, [State.trajectory.trial]));
  }
}

// -- view: overview -----------------------------------------------------------

function renderOverview(ov) {
  const summary = el("div", { class: "grid grid-4", style: "margin-bottom: 20px;" }, [
    el("div", { class: "card" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v" }, [fmtNum(ov.job_count)]),
        el("div", { class: "l" }, ["Jobs"]),
      ]),
    ]),
    el("div", { class: "card" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v" }, [fmtNum(ov.analyzed_count)]),
        el("div", { class: "l" }, ["Analyzed"]),
      ]),
    ]),
    el("div", { class: "card" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v good" }, [fmtNum(ov.total_resolved)]),
        el("div", { class: "l" }, ["Resolved"]),
      ]),
    ]),
    el("div", { class: "card" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v" + (ov.overall_resolve_rate >= 50 ? " good" : " bad") }, [fmtPct(ov.overall_resolve_rate)]),
        el("div", { class: "l" }, ["Rate"]),
      ]),
    ]),
  ]);

  const countsRow = el("div", { class: "grid grid-3", style: "margin-bottom: 20px;" }, [
    el("div", { class: "card" }, [
      el("div", { class: "card-title" }, ["Scaffolds"]),
      el("div", { class: "bar-chart" }, (ov.scaffolds || []).map(([k, n]) =>
        el("div", { class: "bar" }, [
          el("div", { class: "label" }, [k]),
          el("div", { class: "track" }, [el("div", { class: "fill", style: `width: ${percent(n, ov.job_count)}%` })]),
          el("div", { class: "num" }, [String(n)]),
        ])
      )),
    ]),
    el("div", { class: "card" }, [
      el("div", { class: "card-title" }, ["Datasets"]),
      el("div", { class: "bar-chart" }, (ov.datasets || []).map(([k, n]) =>
        el("div", { class: "bar" }, [
          el("div", { class: "label" }, [k]),
          el("div", { class: "track" }, [el("div", { class: "fill", style: `width: ${percent(n, ov.job_count)}%` })]),
          el("div", { class: "num" }, [String(n)]),
        ])
      )),
    ]),
    el("div", { class: "card" }, [
      el("div", { class: "card-title" }, ["Models"]),
      el("div", { class: "bar-chart" }, (ov.models || []).map(([k, n]) =>
        el("div", { class: "bar" }, [
          el("div", { class: "label" }, [k]),
          el("div", { class: "track" }, [el("div", { class: "fill", style: `width: ${percent(n, ov.job_count)}%` })]),
          el("div", { class: "num" }, [String(n)]),
        ])
      )),
    ]),
  ]);

  return el("div", {}, [summary, countsRow]);
}

function percent(n, total) { return total > 0 ? (100 * n / total).toFixed(1) : 0; }

// -- chart helpers ------------------------------------------------------------

function renderBreakdownChart(canvasId, tbl, cats) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return;
  // sort categories by total count desc for stable display
  const sorted = [...cats].sort((a, b) => {
    const ta = (tbl.counts[a] || {}).total || 0;
    const tb = (tbl.counts[b] || {}).total || 0;
    return tb - ta;
  });
  const labels = sorted;
  const rates = sorted.map(c => {
    const p = (tbl.proportions_by_category || {})[c] || {};
    return +(p.resolved || 0) * 100;
  });
  const totals = sorted.map(c => (tbl.counts[c] || {}).total || 0);
  const resolved = sorted.map(c => (tbl.counts[c] || {}).resolved || 0);
  const styles = getComputedStyle(document.documentElement);
  const grid = styles.getPropertyValue("--chart-grid").trim() || "#302a24";
  const tick = styles.getPropertyValue("--chart-tick").trim() || "#8a847a";
  const fg = styles.getPropertyValue("--c-fg").trim() || "#f0ede7";
  // Single series → every bar wears slot 1, not the brand accent (which stays out
  // of plots) and not a status color. The title names the measure, so no legend.
  const accent = styles.getPropertyValue("--c-series-1").trim() || "#d95926";
  const yRange = chartPercentYAxisRange(rates);
  const chart = new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Resolve rate (%)",
          data: rates,
          backgroundColor: accent,
          borderRadius: 4,
          maxBarThickness: 48,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const i = ctx.dataIndex;
              return [
                `Rate: ${rates[i].toFixed(1)}%`,
                `Resolved: ${resolved[i]} / ${totals[i]}`,
              ];
            },
          },
        },
      },
      scales: {
        y: {
          min: yRange.min,
          max: yRange.max,
          ticks: { color: tick, callback: (v) => v + "%" },
          grid: { color: grid },
          title: { display: true, text: "Resolve rate (%)", color: fg, font: { size: 11 } },
        },
        x: {
          ticks: { color: tick, font: { size: 11 } },
          grid: { display: false },
        },
      },
    },
  });
  State.charts.push(chart);
}

function getCompareBreakdownMeta(jobs, tableKey) {
  const comparableJobs = jobs.filter(j => {
    const tbl = j.tag_analysis_summary?.tables?.[tableKey];
    return tbl && (tbl.categories || []).length > 0;
  });
  if (comparableJobs.length === 0) {
    return { comparableJobs: [], labels: [] };
  }

  const categoryTotals = new Map();
  for (const job of comparableJobs) {
    const tbl = job.tag_analysis_summary.tables[tableKey];
    for (const cat of (tbl.categories || [])) {
      const total = (tbl.counts?.[cat] || {}).total || 0;
      categoryTotals.set(cat, (categoryTotals.get(cat) || 0) + total);
    }
  }

  const labels = [...categoryTotals.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([cat]) => cat);

  return { comparableJobs, labels };
}

function renderCompareBreakdownChart(canvasId, jobs, tableKey) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return;

  const { comparableJobs, labels } = getCompareBreakdownMeta(jobs, tableKey);
  if (labels.length === 0) return;

  const styles = getComputedStyle(document.documentElement);
  const grid = styles.getPropertyValue("--chart-grid").trim() || "#302a24";
  const tick = styles.getPropertyValue("--chart-tick").trim() || "#8a847a";
  const fg = styles.getPropertyValue("--c-fg").trim() || "#f0ede7";
  const palette = [
    // Categorical slots from DASHBOARD_PALETTE.md, in their fixed order. The brand
    // accent and the status colors are deliberately not used here — neither may
    // stand in for a series.
    styles.getPropertyValue("--c-series-1").trim() || "#d95926",
    styles.getPropertyValue("--c-series-2").trim() || "#199e70",
    styles.getPropertyValue("--c-series-3").trim() || "#3987e5",
    styles.getPropertyValue("--c-series-4").trim() || "#c98500",
    styles.getPropertyValue("--c-series-5").trim() || "#d55181",
    styles.getPropertyValue("--c-series-6").trim() || "#008300",
    styles.getPropertyValue("--c-series-7").trim() || "#9085e9",
  ];

  const datasets = comparableJobs.map((job, index) => {
    const tbl = job.tag_analysis_summary.tables[tableKey];
    return {
      label: job.name,
      data: labels.map(cat => {
        const proportions = (tbl.proportions_by_category || {})[cat];
        if (!proportions || proportions.resolved == null) return null;
        return +(proportions.resolved * 100).toFixed(1);
      }),
      backgroundColor: palette[index % palette.length],
      borderRadius: 4,
      maxBarThickness: 30,
    };
  });
  const yRange = chartPercentYAxisRange(datasets.flatMap(ds => ds.data).filter(v => v != null));

  const chart = new Chart(canvas, {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: "index",
        intersect: false,
      },
      plugins: {
        legend: {
          display: true,
          position: "bottom",
          labels: {
            color: fg,
            boxWidth: 10,
            usePointStyle: true,
            pointStyle: "rectRounded",
          },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const job = comparableJobs[ctx.datasetIndex];
              const tbl = job.tag_analysis_summary.tables[tableKey];
              const counts = (tbl.counts || {})[ctx.label] || {};
              if (ctx.raw == null) return `${job.name}: no data`;
              return `${job.name}: ${ctx.raw.toFixed(1)}% (${counts.resolved || 0}/${counts.total || 0} resolved)`;
            },
          },
        },
      },
      scales: {
        y: {
          min: yRange.min,
          max: yRange.max,
          ticks: { color: tick, callback: (v) => v + "%" },
          grid: { color: grid },
          title: { display: true, text: "Resolve rate (%)", color: fg, font: { size: 11 } },
        },
        x: {
          ticks: { color: tick, font: { size: 11 } },
          grid: { display: false },
        },
      },
    },
  });
  State.charts.push(chart);
}

function renderCompareBreakdownCountsTable(jobs, tableKey) {
  const { comparableJobs, labels } = getCompareBreakdownMeta(jobs, tableKey);
  if (comparableJobs.length === 0 || labels.length === 0) return null;

  return el("div", { class: "scroll-x", style: "margin-top: 14px;" }, [
    el("table", { class: "tbl" }, [
      el("thead", {}, [
        el("tr", {}, [
          el("th", {}, ["Category"]),
          ...comparableJobs.map(job => el("th", { title: job.name, style: "text-align: right;" }, [job.name.slice(0, 18)])),
        ]),
      ]),
      el("tbody", {}, labels.map(cat => el("tr", {}, [
        el("td", { class: "mono" }, [cat]),
        ...comparableJobs.map(job => {
          const counts = job.tag_analysis_summary?.tables?.[tableKey]?.counts?.[cat];
          return el("td", { class: "num mono" }, [
            counts ? `${counts.resolved || 0}/${counts.total || 0}` : "—",
          ]);
        }),
      ]))),
    ]),
  ]);
}

// -- view: jobs ---------------------------------------------------------------

function datasetSortRank(dataset) {
  return dataset === "swebench-verified" ? 0 : 1;
}

function renderJobsPanel() {
  const sortedJobs = [...State.jobs].sort((a, b) => {
    const datasetRank = datasetSortRank(a.dataset) - datasetSortRank(b.dataset);
    if (datasetRank !== 0) return datasetRank;
    const dataset = (a.dataset || "—").localeCompare(b.dataset || "—");
    if (dataset !== 0) return dataset;
    const scaffold = (a.scaffold || "—").localeCompare(b.scaffold || "—");
    if (scaffold !== 0) return scaffold;
    const ar = a.analysis?.resolve_rate ?? -1;
    const br = b.analysis?.resolve_rate ?? -1;
    if (br !== ar) return br - ar;
    return a.name.localeCompare(b.name);
  });
  const bodyRows = [];
  let lastGroup = null;
  for (const j of sortedJobs) {
    const a = j.analysis;
    const group = `${j.dataset || "—"} · ${j.scaffold || "—"}`;
    if (group !== lastGroup) {
      bodyRows.push(el("tr", { class: "group-row" }, [
        el("td", { colspan: "8" }, [
          el("span", { class: "group-title" }, [group]),
          el("span", { class: "muted small" }, [
            ` ${sortedJobs.filter(item => `${item.dataset || "—"} · ${item.scaffold || "—"}` === group).length} jobs`,
          ]),
        ]),
      ]));
      lastGroup = group;
    }
    bodyRows.push(el("tr", { onclick: () => { setHash(["job", j.name]); } }, [
      el("td", { class: "mono" }, [j.name]),
      el("td", {}, [j.dataset || "—"]),
      el("td", {}, [j.scaffold || "—"]),
      el("td", { class: "mono" }, [j.model ? j.model.replace(/^hosted_vllm\//, "") : "—"]),
      el("td", { class: "num" }, [a ? fmtNum(a.total) : "—"]),
      el("td", { class: "num" }, [a ? fmtNum(a.resolved_total) : "—"]),
      el("td", { class: "num" }, [a ? fmtPct(a.resolve_rate) : "—"]),
      el("td", {}, [j.started_at ? new Date(j.started_at).toLocaleString() : "—"]),
    ]));
  }
  return el("div", { class: "card" }, [
    el("div", { class: "card-title" }, ["All jobs", el("span", { class: "hint" }, ["Grouped by Dataset · Scaffold, sorted by Rate"])]),
    el("div", { class: "scroll-x" }, [
      el("table", { class: "tbl" }, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", {}, ["Job"]),
            el("th", {}, ["Dataset"]),
            el("th", {}, ["Scaffold"]),
            el("th", {}, ["Model"]),
            el("th", { style: "text-align: right;" }, ["Total"]),
            el("th", { style: "text-align: right;" }, ["Resolved"]),
            el("th", { style: "text-align: right;" }, ["Rate"]),
            el("th", {}, ["Started"]),
          ]),
        ]),
        el("tbody", {}, bodyRows),
      ]),
    ]),
  ]);
}

// -- view: job detail ---------------------------------------------------------

function renderJobDetail(d) {
  const s = d.summary || {};
  const a = s.analysis || {};
  const scoreComp = d.score_comparison;
  const container = el("div", {});

  // summary cards
  const exceptionEntries = exceptionStatsEntries(
    Object.keys(s.exception_stats || {}).length > 0 ? s.exception_stats : aggregateExceptionStats(d.trials)
  );
  const truncationEntries = exceptionStatsEntries(aggregateTruncationStats(d.trials));
  const allTrials = d.trials || [];
  const resolvedTrials = allTrials.filter(t => t.resolved === true);
  const failedTrials = allTrials.filter(t => t.resolved === false);
  const allAverages = averageTrialMetrics(allTrials);
  const resolvedAverages = averageTrialMetrics(resolvedTrials);
  const failedAverages = averageTrialMetrics(failedTrials);
  const allLimits = trialLimitCounts(allTrials);
  const resolvedLimits = trialLimitCounts(resolvedTrials);
  const failedLimits = trialLimitCounts(failedTrials);
  const resolvedCount = a.resolved_total ?? resolvedTrials.length;
  const failedCount = a.failed_total ?? failedTrials.length;
  const terminalCount = resolvedCount + failedCount;
  const resolveRate = a.resolve_rate ?? (terminalCount > 0 ? (100 * resolvedCount) / terminalCount : null);
  const metricCards = [
    el("div", { class: "card" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v" }, [fmtNum(s.trial_count)]),
        el("div", { class: "l" }, ["Trials"]),
      ]),
      renderTrialSummaryMetrics(allAverages, allLimits),
    ]),
    el("div", { class: "card" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v good" }, [fmtNum(resolvedCount)]),
        el("div", { class: "l" }, ["Resolved"]),
      ]),
      renderTrialSummaryMetrics(resolvedAverages, resolvedLimits),
    ]),
    el("div", { class: "card" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v bad" }, [fmtNum(failedCount)]),
        el("div", { class: "l" }, ["Failed"]),
      ]),
      renderTrialSummaryMetrics(failedAverages, failedLimits),
    ]),
    el("div", { class: "card" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v" + (resolveRate == null ? "" : resolveRate >= 50 ? " good" : " bad") }, [resolveRate == null ? "—" : fmtPct(resolveRate)]),
        el("div", { class: "l" }, ["Rate"]),
      ]),
    ]),
  ];
  if (exceptionEntries.length > 0) {
    metricCards.push(el("div", { class: "card exception-summary" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v bad" }, [fmtNum(exceptionEntries.reduce((sum, [, count]) => sum + count, 0))]),
        el("div", { class: "l" }, ["Exceptions"]),
      ]),
      el("div", { class: "chip-row exception-chips" }, exceptionEntries.map(([type, count]) => renderExceptionPill(type, count))),
    ]));
  }
  if (truncationEntries.length > 0) {
    metricCards.push(el("div", { class: "card exception-summary" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v bad" }, [fmtNum(truncationEntries.reduce((sum, [, count]) => sum + count, 0))]),
        el("div", { class: "l" }, ["Truncation"]),
      ]),
      el("div", { class: "chip-row exception-chips" }, truncationEntries.map(([type, count]) => renderTruncationPill(type, count))),
    ]));
  }
  const summaryCards = el("div", { class: "grid grid-auto", style: "margin-bottom: 20px;" }, metricCards);
  container.appendChild(summaryCards);

  // tag analysis - difficulty + tag1 + tag2 breakdown as bar charts (y = resolve rate)
  if (d.tag_analysis_summary && d.tag_analysis_summary.tables) {
    const tables = d.tag_analysis_summary.tables;
    const breakdowns = [
      { key: "difficulty_label", title: "Difficulty breakdown", contingency: d.contingency_difficulty },
      { key: "tag1", title: "Language breakdown", contingency: d.contingency_tag1 },
      { key: "tag2", title: "Area breakdown", contingency: d.contingency_tag2 },
    ];
    const breakdownGrid = el("div", { class: "grid grid-3", style: "margin-bottom: 20px;" });
    let appended = 0;
    for (const b of breakdowns) {
      const tbl = tables[b.key];
      if (!tbl) continue;
      const cats = tbl.categories || [];
      if (cats.length === 0) continue;
      const canvasId = `breakdown-${b.key}`;
      const card = el("div", { class: "card" }, [
        el("div", { class: "card-title" }, [b.title]),
        el("div", { style: "position: relative; height: 240px;" }, [
          el("canvas", { id: canvasId }),
        ]),
        b.contingency
          ? el("details", { style: "margin-top: 12px;" }, [
              el("summary", { style: "cursor: pointer; font-size: 11px; color: var(--c-fg-mute);" }, ["Show contingency table"]),
              el("pre", { class: "code-block", style: "margin-top: 8px; font-size: 10.5px;" }, [b.contingency]),
            ])
          : null,
      ]);
      breakdownGrid.appendChild(card);
      // schedule chart render after element is in DOM
      scheduleChart(() => renderBreakdownChart(canvasId, tbl, cats));
      appended++;
    }
    if (appended > 0) container.appendChild(breakdownGrid);
  }

  // rule score summary
  if (d.rule_score) {
    const rs = d.rule_score;
    const activeComponents = (rs.active_components || []).filter(component => component.weight > 0);
    const compositeCorrelation = rs.correlations?.composite_score;
    const rsCard = el("div", { class: "card", style: "margin-bottom: 20px;" }, [
      el("div", { class: "card-title" }, [
        "Rule-based trajectory score",
        activeComponents.length > 0 && el("span", { class: "hint" }, ["Trajectory score summary with component breakdown"]),
      ]),
      el("div", { class: "grid grid-2", style: "margin-bottom: 16px;" }, [
        el("div", {}, [
          el("div", { class: "section-h" }, ["Resolved (n=" + (rs.resolved?.n || 0) + ")"]),
          el("div", { class: "kv" }, [
            el("div", { class: "k" }, ["Composite mean"]),
            el("div", { class: "v" }, [fmtScore(rs.resolved?.metrics?.composite_score?.mean)]),
            el("div", { class: "k" }, ["Composite median"]),
            el("div", { class: "v" }, [fmtScore(rs.resolved?.metrics?.composite_score?.median)]),
          ]),
        ]),
        el("div", {}, [
          el("div", { class: "section-h" }, ["Failed (n=" + (rs.unresolved?.n || 0) + ")"]),
          el("div", { class: "kv" }, [
            el("div", { class: "k" }, ["Composite mean"]),
            el("div", { class: "v" }, [fmtScore(rs.unresolved?.metrics?.composite_score?.mean)]),
            el("div", { class: "k" }, ["Composite median"]),
            el("div", { class: "v" }, [fmtScore(rs.unresolved?.metrics?.composite_score?.median)]),
          ]),
        ]),
      ]),
      compositeCorrelation && el("div", { class: "muted-card", style: "margin-bottom: 16px;" }, [
        `Trajectory score correlation with resolved: Pearson r ${fmtSignedScore(compositeCorrelation.pearson_r)}, p(pearson) ${fmtPValue(compositeCorrelation.pearson_p)}`,
      ]),
      activeComponents.length > 0 && el("div", { class: "scroll-x" }, [
        el("table", { class: "tbl" }, [
          el("thead", {}, [
            el("tr", {}, [
              el("th", {}, ["Component"]),
              el("th", { style: "text-align: right;" }, ["Weight"]),
              el("th", { style: "text-align: right;" }, ["Resolved mean"]),
              el("th", { style: "text-align: right;" }, ["Failed mean"]),
              el("th", { style: "text-align: right;" }, ["Δmean"]),
              el("th", { style: "text-align: right;" }, ["Pearson r"]),
              el("th", { style: "text-align: right;" }, ["p(pearson)"]),
            ]),
          ]),
          el("tbody", {}, activeComponents.map(component => {
            const resolvedMean = rs.resolved?.metrics?.[component.key]?.mean;
            const unresolvedMean = rs.unresolved?.metrics?.[component.key]?.mean;
            const deltaMean = resolvedMean != null && unresolvedMean != null ? resolvedMean - unresolvedMean : null;
            const correlation = rs.correlations?.[component.key];
            return el("tr", {}, [
              el("td", { title: component.description || "" }, [
                el("div", {}, [component.label || component.key]),
                el("div", { class: "muted small mono" }, [component.key]),
              ]),
              el("td", { class: "num mono" }, [fmtScore(component.weight)]),
              el("td", { class: "num mono" }, [fmtScore(resolvedMean)]),
              el("td", { class: "num mono" }, [fmtScore(unresolvedMean)]),
              el("td", { class: "num mono" }, [fmtSignedScore(deltaMean)]),
              el("td", { class: "num mono" }, [fmtSignedScore(correlation?.pearson_r)]),
              el("td", { class: "num mono" }, [fmtPValue(correlation?.pearson_p)]),
            ]);
          })),
        ]),
      ]),
    ]);
    container.appendChild(rsCard);
  }

  if (scoreComp) {
    const featureRows = buildDeterministicFeatureRows(scoreComp);
    const keyFindings = buildKeyFindings(scoreComp);

    if (keyFindings.length > 0 || featureRows.length > 0) {
      container.appendChild(el("div", { class: "card", style: "margin-bottom: 20px;" }, [
        el("div", { class: "card-title" }, [
          "Error Analysis",
          el("span", { class: "hint" }, ["Key findings plus per-metric failed vs resolved rates (all higher is better)"]),
        ]),
        keyFindings.length > 0 ? el("div", { style: featureRows.length > 0 ? "margin-bottom: 16px;" : "" }, [
          el("div", { class: "muted small", style: "margin-bottom: 8px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;" }, ["Key Findings"]),
          el("div", { class: "kv" }, keyFindings.flatMap(item => [
            el("div", { class: "k mono" }, [item.label]),
            el("div", { class: "v" }, [item.text]),
          ])),
        ]) : null,
        featureRows.length > 0 ? el("div", { class: "scroll-x" }, [
          el("table", { class: "tbl" }, [
            el("thead", {}, [
              el("tr", {}, [
                el("th", {}, ["Metric"]),
                el("th", { style: "text-align: right;" }, ["Resolved"]),
                el("th", { style: "text-align: right;" }, ["Failed"]),
                el("th", { style: "text-align: right;" }, ["Δ"]),
              ]),
            ]),
            el("tbody", {}, featureRows.map(row => {
              const meta = getDeterministicFeatureMeta(row.key);
              return el("tr", {}, [
                el("td", { title: meta.description || "" }, [
                  el("div", { class: "mono" }, [meta.label]),
                  meta.description ? el("div", { class: "muted small" }, [meta.description]) : null,
                ]),
                el("td", { class: "num mono" }, [`${row.resolved.toFixed(1)}%`]),
                el("td", { class: "num mono" }, [`${row.failed.toFixed(1)}%`]),
                el("td", { class: "num mono" }, [fmtDiffPct(row.diff)]),
              ]);
            })),
          ]),
        ]) : null,
      ]));
    }
  }

  // trials table
  const trialsCard = el("div", { class: "card" }, [
    el("div", { class: "card-title" }, ["Trials", el("span", { class: "hint" }, ["Click to view trajectory"])]),
    el("div", { class: "scroll-x" }, [
      el("table", { class: "tbl" }, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", {}, ["Task"]),
            el("th", {}, ["Status"]),
            el("th", {}, ["Exception"]),
            el("th", {}, ["Truncation"]),
            el("th", { style: "text-align: right;" }, ["Turns"]),
            el("th", { style: "text-align: right;" }, ["Tokens"]),
            el("th", { style: "text-align: right;" }, ["Duration"]),
            el("th", {}, ["Finished"]),
          ]),
        ]),
        el("tbody", {}, (d.trials || []).map(t => {
          const excType = exceptionTypeOfTrial(t);
          return el("tr", {
            class: excType ? "has-exception" : "",
            onclick: t.has_trajectory ? () => { setHash(["trajectory", s.name, t.trial_name]); } : null,
            style: t.has_trajectory ? "cursor: pointer;" : "cursor: default; opacity: 0.6;",
          }, [
            el("td", { class: "mono", title: t.trial_name }, [t.task_name || t.trial_name || "—"]),
            el("td", {}, [
              t.resolved === true
                ? el("span", { class: "pill good" }, ["✓ resolved"])
                : t.resolved === false
                  ? el("span", { class: "pill bad" }, ["✗ failed"])
                  : el("span", { class: "pill" }, ["—"]),
            ]),
            el("td", {}, [excType ? renderExceptionPill(excType) : el("span", { class: "muted" }, ["—"])]),
            el("td", {}, [renderTruncationPill(t.truncation)]),
            el("td", { class: "num" }, [fmtNum(t.turn_count)]),
            el("td", { class: "num" }, [fmtBytes(trialTokenTotal(t))]),
            el("td", { class: "num" }, [fmtDuration(t.duration_sec)]),
            el("td", {}, [t.finished_at ? new Date(t.finished_at).toLocaleString() : "—"]),
          ]);
        })),
      ]),
    ]),
  ]);
  container.appendChild(trialsCard);

  return container;
}

// -- view: compare ------------------------------------------------------------

function renderCompareEmpty() {
  return el("div", { class: "card" }, [
    el("div", { class: "card-title" }, ["Compare"]),
    el("div", { class: "empty", style: "padding: 24px 12px;" }, [
      "No jobs selected for comparison yet. Use the + button next to a job in the sidebar, then open Compare."
    ]),
  ]);
}

const COMPARE_RULE_SCORE_METRIC_KEYS = [
  "composite_score",
  "sub_score",
  "stp_score",
  "tvr_score",
  "fec_score",
  "dpi_score",
];

function ruleScoreMetricLabel(row) {
  const label = row?.label || row?.key || "";
  const short = String(label).split(" - ")[0];
  if (short) return short;
  return String(row?.key || "").replace(/_score$/, "");
}

function ruleScoreMeanPct(metrics, key) {
  const mean = metrics?.[key]?.mean;
  return mean == null || Number.isNaN(mean) ? null : mean * 100;
}

function buildRuleScoreFeatureRows(ruleScore) {
  if (!ruleScore) return [];

  const activeComponents = (ruleScore.active_components || []).filter(component => component.weight > 0);
  const metricDefs = [
    { key: "composite_score", label: "Composite", description: "Weighted composite trajectory score. Higher is better." },
    ...activeComponents.map(component => ({
      key: component.key,
      label: component.label || component.key,
      description: component.description || "",
    })),
  ];

  const seen = new Set();
  const rows = [];
  for (const def of metricDefs) {
    if (seen.has(def.key)) continue;
    seen.add(def.key);

    const resolved = ruleScoreMeanPct(ruleScore.resolved?.metrics, def.key);
    const unresolved = ruleScoreMeanPct(ruleScore.unresolved?.metrics, def.key);
    let average = null;
    if (resolved != null && unresolved != null) average = (resolved + unresolved) / 2;
    else if (resolved != null) average = resolved;
    else if (unresolved != null) average = unresolved;
    if (average == null) continue;

    rows.push({
      key: def.key,
      label: def.label,
      description: def.description,
      resolved,
      unresolved,
      average,
    });
  }
  return rows;
}

function ruleScoreMetricLabelForKey(rowsByJob, key) {
  for (const rows of rowsByJob.values()) {
    const row = rows.find(item => item.key === key);
    if (row) return ruleScoreMetricLabel(row);
  }
  return String(key).replace(/_score$/, "");
}

function renderCompareRuleScoreChart(canvasId, comparable, metricKeys, rowsByJob) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return;

  const styles = getComputedStyle(document.documentElement);
  const grid = styles.getPropertyValue("--chart-grid").trim() || "#302a24";
  const tick = styles.getPropertyValue("--chart-tick").trim() || "#8a847a";
  const fg = styles.getPropertyValue("--c-fg").trim() || "#f0ede7";
  const palette = compareJobPalette(styles);
  const labels = metricKeys.map(key => ruleScoreMetricLabelForKey(rowsByJob, key));
  const datasets = comparable.map((job, index) => {
    const color = palette[index % palette.length];
    const rows = rowsByJob.get(job.name) || [];
    return {
      label: job.name,
      data: metricKeys.map(key => {
        const row = rows.find(item => item.key === key);
        return row?.average == null ? null : +row.average.toFixed(1);
      }),
      borderColor: color,
      backgroundColor: hexToRgba(color, 0.14),
      pointBackgroundColor: color,
      pointBorderColor: color,
      pointHoverRadius: 4,
      borderWidth: 2,
      spanGaps: true,
    };
  });
  const values = datasets.flatMap(ds => ds.data).filter(v => v != null);
  const rRange = chartPercentYAxisRange(values);

  const chart = new Chart(canvas, {
    type: "radar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      elements: { line: { tension: 0.18 } },
      plugins: {
        legend: {
          display: true,
          position: "bottom",
          labels: {
            color: fg,
            boxWidth: 10,
            usePointStyle: true,
            pointStyle: "circle",
          },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.raw == null ? "—" : ctx.raw.toFixed(1) + "%"}`,
            afterLabel: (ctx) => {
              const job = comparable[ctx.datasetIndex];
              const row = (rowsByJob.get(job.name) || []).find(item => item.key === metricKeys[ctx.dataIndex]);
              if (!row) return "";
              const resolved = row.resolved == null ? "—" : `${row.resolved.toFixed(1)}%`;
              const unresolved = row.unresolved == null ? "—" : `${row.unresolved.toFixed(1)}%`;
              return `resolved ${resolved} · unresolved ${unresolved}`;
            },
          },
        },
      },
      scales: {
        r: {
          min: rRange.min,
          max: rRange.max,
          ticks: {
            color: tick,
            backdropColor: "transparent",
            callback: (v) => v + "%",
          },
          grid: { color: grid },
          angleLines: { color: grid },
          pointLabels: { color: fg, font: { size: 11 } },
        },
      },
    },
  });
  State.charts.push(chart);
}

function buildCompareRuleScoreCard(jobs) {
  const comparable = (jobs || []).filter(job =>
    job.rule_score?.resolved?.metrics || job.rule_score?.unresolved?.metrics
  );
  if (comparable.length === 0) return null;

  const rowsByJob = new Map(comparable.map(job => [job.name, buildRuleScoreFeatureRows(job.rule_score)]));
  const availableKeys = new Set([...rowsByJob.values()].flatMap(rows => rows.map(row => row.key)));
  const metricKeys = COMPARE_RULE_SCORE_METRIC_KEYS.filter(key => availableKeys.has(key));
  for (const key of [...availableKeys].sort((a, b) => a.localeCompare(b))) {
    if (metricKeys.length >= 8) break;
    if (!metricKeys.includes(key)) metricKeys.push(key);
  }
  if (metricKeys.length === 0) return null;

  const canvasId = "compare-rule-score-radar";
  scheduleChart(() => renderCompareRuleScoreChart(canvasId, comparable, metricKeys, rowsByJob));

  return el("div", { class: "card", style: "margin-top: 20px;" }, [
    el("div", { class: "card-title" }, [
      "Rule-based trajectory score",
      el("span", { class: "hint" }, ["Radar: average of resolved and unresolved means (higher is better)"]),
    ]),
    el("div", { class: "compare-radar-wrap" }, [
      el("canvas", { id: canvasId }),
    ]),
  ]);
}

function renderCompareErrorAnalysisChart(canvasId, comparable, metricKeys, rowsByJob) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return;

  const styles = getComputedStyle(document.documentElement);
  const grid = styles.getPropertyValue("--chart-grid").trim() || "#302a24";
  const tick = styles.getPropertyValue("--chart-tick").trim() || "#8a847a";
  const fg = styles.getPropertyValue("--c-fg").trim() || "#f0ede7";
  const palette = compareJobPalette(styles);
  const labels = metricKeys.map(key => getDeterministicFeatureMeta(key).label);
  const datasets = comparable.map((job, index) => {
    const color = palette[index % palette.length];
    const rows = rowsByJob.get(job.name) || [];
    return {
      label: job.name,
      data: metricKeys.map(key => {
        const row = rows.find(item => item.key === key);
        return row ? +row.average.toFixed(1) : null;
      }),
      borderColor: color,
      backgroundColor: hexToRgba(color, 0.14),
      pointBackgroundColor: color,
      pointBorderColor: color,
      pointHoverRadius: 4,
      borderWidth: 2,
      spanGaps: true,
    };
  });
  const values = datasets.flatMap(ds => ds.data).filter(v => v != null);
  const rRange = chartPercentYAxisRange(values);

  const chart = new Chart(canvas, {
    type: "radar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      elements: { line: { tension: 0.18 } },
      plugins: {
        legend: {
          display: true,
          position: "bottom",
          labels: {
            color: fg,
            boxWidth: 10,
            usePointStyle: true,
            pointStyle: "circle",
          },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.raw == null ? "—" : ctx.raw.toFixed(1) + "%"}`,
            afterLabel: (ctx) => {
              const job = comparable[ctx.datasetIndex];
              const row = (rowsByJob.get(job.name) || []).find(item => item.key === metricKeys[ctx.dataIndex]);
              return row ? `failed ${row.failed.toFixed(1)}% · resolved ${row.resolved.toFixed(1)}%` : "";
            },
          },
        },
      },
      scales: {
        r: {
          min: rRange.min,
          max: rRange.max,
          ticks: {
            color: tick,
            backdropColor: "transparent",
            callback: (v) => v + "%",
          },
          grid: { color: grid },
          angleLines: { color: grid },
          pointLabels: { color: fg, font: { size: 11 } },
        },
      },
    },
  });
  State.charts.push(chart);
}

function buildCompareErrorAnalysisCard(jobs) {
  const comparable = (jobs || []).filter(j => j.score_comparison?.feature_averages);
  if (comparable.length === 0) return null;
  const rowsByJob = new Map(comparable.map(job => [job.name, buildDeterministicFeatureRows(job.score_comparison)]));
  const availableKeys = new Set([...rowsByJob.values()].flatMap(rows => rows.map(row => row.key)));
  const metricKeys = COMPARE_ERROR_METRIC_KEYS.filter(key => availableKeys.has(key));
  for (const key of [...availableKeys].sort((a, b) => a.localeCompare(b))) {
    if (metricKeys.length >= 10) break;
    if (!metricKeys.includes(key) && !isExcludedErrorMetric(key)) metricKeys.push(key);
  }
  if (metricKeys.length === 0) return null;

  const canvasId = "compare-error-analysis-radar";
  scheduleChart(() => renderCompareErrorAnalysisChart(canvasId, comparable, metricKeys, rowsByJob));

  return el("div", { class: "card", style: "margin-top: 20px;" }, [
    el("div", { class: "card-title" }, [
      "Error Analysis",
      el("span", { class: "hint" }, ["Radar: average of resolved and unresolved per metric (all higher is better)"]),
    ]),
    el("div", { class: "compare-radar-wrap" }, [
      el("canvas", { id: canvasId }),
    ]),
  ]);
}

function buildCompareTrialMetricRows(jobs) {
  return (jobs || []).map(job => {
    const trials = job.trials || [];
    const averages = averageTrialMetrics(trials);
    const limits = trialLimitCounts(trials);
    const exceptionCount = Object.values(aggregateExceptionStats(trials)).reduce((sum, count) => sum + count, 0);
    const truncationCount = Object.values(aggregateTruncationStats(trials)).reduce((sum, count) => sum + count, 0);
    const total = trials.length;
    return {
      name: job.name,
      avgTurns: averages.turns,
      avgTokens: averages.tokens,
      avgDuration: averages.duration,
      hitMaxTurnRate: total > 0 ? (limits.hitMaxTurn / total) * 100 : null,
      hitMaxLengthRate: total > 0 ? (limits.hitMaxLength / total) * 100 : null,
      exceptionCount,
      truncationCount,
    };
  });
}

const COMPARE_TRIAL_METRICS = [
  { key: "avgTurns", label: "Avg Turns", formatter: fmtNum, yTitle: "Turns" },
  { key: "avgTokens", label: "Avg Tokens", formatter: fmtBytes, yTitle: "Tokens" },
  { key: "avgDuration", label: "Avg Duration", formatter: fmtDuration, yTitle: "Duration" },
  { key: "hitMaxTurnRate", label: "Hit Max Turn Rate", formatter: fmtPct, yTitle: "Rate (%)", isPercent: true },
  { key: "hitMaxLengthRate", label: "Hit Max Length Rate", formatter: fmtPct, yTitle: "Rate (%)", isPercent: true },
];

function renderCompareTrialMetricChart(canvasId, rows, metric) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return;

  const styles = getComputedStyle(document.documentElement);
  const grid = styles.getPropertyValue("--chart-grid").trim() || "#302a24";
  const tick = styles.getPropertyValue("--chart-tick").trim() || "#8a847a";
  const fg = styles.getPropertyValue("--c-fg").trim() || "#f0ede7";
  const palette = compareJobPalette(styles);
  const values = rows.map(row => row[metric.key]);
  const numericValues = values.filter(v => typeof v === "number" && Number.isFinite(v));
  const yRange = metric.isPercent
    ? chartPercentYAxisRange(numericValues)
    : chartNumericYAxisRange(numericValues);
  const chart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: rows.map((_, index) => `#${index + 1}`),
      datasets: [{
        label: metric.label,
        data: values,
        backgroundColor: rows.map((_, index) => palette[index % palette.length]),
        borderRadius: 4,
        maxBarThickness: 42,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => rows[items[0]?.dataIndex]?.name || "",
            label: (ctx) => `${metric.label}: ${ctx.raw == null ? "—" : metric.formatter(ctx.raw)}`,
          },
        },
      },
      scales: {
        y: {
          min: yRange.min,
          max: yRange.max,
          ticks: { color: tick, callback: (v) => metric.isPercent ? `${v}%` : metric.formatter(v) },
          grid: { color: grid },
          title: { display: true, text: metric.yTitle, color: fg, font: { size: 11 } },
        },
        x: {
          ticks: { color: tick, font: { size: 10 } },
          grid: { display: false },
        },
      },
    },
  });
  State.charts.push(chart);
}

function renderCompareJobLegend(rows) {
  const palette = compareJobPalette();
  return el("div", { class: "compare-job-legend" }, rows.map((row, index) =>
    el("span", { class: "compare-job-legend-item", title: row.name }, [
      el("span", {
        class: "compare-job-legend-marker",
        style: `background: ${palette[index % palette.length]};`,
      }),
      el("span", { class: "mono" }, [`#${index + 1}`]),
      el("span", {}, [shortLabel(row.name, 48)]),
    ])
  ));
}

function buildCompareTrialMetricsCard(rows) {
  if (!rows.some(row => COMPARE_TRIAL_METRICS.some(metric => row[metric.key] != null))) return null;
  const charts = COMPARE_TRIAL_METRICS.map(metric => {
    const canvasId = `compare-trial-metric-${metric.key}`;
    scheduleChart(() => renderCompareTrialMetricChart(canvasId, rows, metric));
    return el("div", { class: "compare-metric-chart" }, [
      el("div", { class: "section-label", style: "padding: 0 0 8px;" }, [metric.label]),
      el("div", { style: "position: relative; height: 240px;" }, [
        el("canvas", { id: canvasId }),
      ]),
    ]);
  });

  return el("div", { class: "card", style: "margin-bottom: 20px;" }, [
    el("div", { class: "card-title" }, [
      "Trial Metrics",
      el("span", { class: "hint" }, ["Bars compare per-job averages and max-limit rates"]),
    ]),
    renderCompareJobLegend(rows),
    el("div", { class: "compare-metrics-grid" }, charts),
  ]);
}

function renderCompare(data) {
  const jobs = data.jobs || [];
  const container = el("div", {});
  const trialMetricRows = buildCompareTrialMetricRows(jobs);

  // summary table
  const summaryRows = jobs.map(j => {
    const a = (j.summary || {}).analysis;
    const metricRow = trialMetricRows.find(row => row.name === j.name) || {};
    return el("tr", {}, [
      el("td", { class: "mono" }, [j.name]),
      el("td", {}, [(j.summary || {}).scaffold || "—"]),
      el("td", {}, [(j.summary || {}).dataset || "—"]),
      el("td", { class: "num" }, [a ? fmtNum(a.total) : "—"]),
      el("td", { class: "num" }, [a ? fmtNum(a.resolved_total) : "—"]),
      el("td", { class: "num" }, [a ? fmtPct(a.resolve_rate) : "—"]),
      el("td", { class: "num" }, [fmtNum(metricRow.exceptionCount)]),
      el("td", { class: "num" }, [fmtNum(metricRow.truncationCount)]),
    ]);
  });
  const summaryCard = el("div", { class: "card", style: "margin-bottom: 20px;" }, [
    el("div", { class: "card-title" }, ["Summary"]),
    el("div", { class: "scroll-x" }, [
      el("table", { class: "tbl" }, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", {}, ["Job"]),
            el("th", {}, ["Scaffold"]),
            el("th", {}, ["Dataset"]),
            el("th", { style: "text-align: right;" }, ["Total"]),
            el("th", { style: "text-align: right;" }, ["Resolved"]),
            el("th", { style: "text-align: right;" }, ["Rate"]),
            el("th", { style: "text-align: right;" }, ["Exceptions"]),
            el("th", { style: "text-align: right;" }, ["Truncation"]),
          ]),
        ]),
        el("tbody", {}, summaryRows),
      ]),
    ]),
  ]);
  container.appendChild(summaryCard);

  const trialMetricsCard = buildCompareTrialMetricsCard(trialMetricRows);
  if (trialMetricsCard) container.appendChild(trialMetricsCard);

  const breakdowns = [
    { key: "difficulty_label", title: "Difficulty breakdown" },
    { key: "tag1", title: "Language breakdown" },
    { key: "tag2", title: "Area breakdown" },
  ];
  const breakdownGrid = el("div", { class: "grid grid-3" });
  let appended = 0;
  for (const breakdown of breakdowns) {
    const { comparableJobs } = getCompareBreakdownMeta(jobs, breakdown.key);
    const availableJobs = comparableJobs;
    if (availableJobs.length === 0) continue;
    const canvasId = `compare-${breakdown.key}`;
    breakdownGrid.appendChild(el("div", { class: "card" }, [
      el("div", { class: "card-title" }, [
        breakdown.title,
        el("span", { class: "hint" }, ["Bars: resolve rate · Table: resolved/total"]),
      ]),
      el("div", { style: "position: relative; height: 280px;" }, [
        el("canvas", { id: canvasId }),
      ]),
      renderCompareBreakdownCountsTable(availableJobs, breakdown.key),
    ]));
    scheduleChart(() => renderCompareBreakdownChart(canvasId, availableJobs, breakdown.key));
    appended++;
  }
  if (appended > 0) {
    container.appendChild(breakdownGrid);
  } else {
    container.appendChild(el("div", { class: "card" }, [
      el("div", { class: "card-title" }, ["Compare breakdowns"]),
      el("div", { class: "empty", style: "padding: 24px 12px;" }, [
        "No difficulty or project-type breakdown data is available for the selected jobs."
      ]),
    ]));
  }

  const ruleScoreCard = buildCompareRuleScoreCard(jobs);
  if (ruleScoreCard) container.appendChild(ruleScoreCard);

  const errorAnalysisCard = buildCompareErrorAnalysisCard(jobs);
  if (errorAnalysisCard) container.appendChild(errorAnalysisCard);

  return container;
}

// -- view: trajectory ---------------------------------------------------------

function renderTrajectory() {
  const t = State.trajectory;
  if (!t) return el("div", { class: "empty" }, ["No trajectory loaded"]);
  if (t.error) return el("div", { class: "empty" }, [`Error: ${t.error}`]);
  const d = t.data;
  if (!d || !d.steps) return el("div", { class: "empty" }, ["No steps"]);

  // Group steps into turns: a turn is one assistant action (tool_calls or message)
  // followed by zero-or-more observations responding to its tool_calls.
  // Pre-action steps (system / user / agent thoughts before any tool call) are
  // rendered as standalone preface cards.
  const turns = buildTurns(d.steps);
  const toolUsage = new Map();
  for (const s of d.steps) {
    for (const tc of (s.tool_calls || [])) {
      const n = tc.name || "?";
      toolUsage.set(n, (toolUsage.get(n) || 0) + 1);
    }
  }
  const toolUsageList = [...toolUsage.entries()].sort((a, b) => b[1] - a[1]);

  // ensure expand state map exists for this trajectory
  if (!State.turnExpanded) State.turnExpanded = {};
  const expandKey = `${t.job}::${t.trial}`;
  if (!State.turnExpanded[expandKey]) {
    // default: expand first and last turn
    const init = {};
    if (turns.length > 0) init[0] = true;
    if (turns.length > 1) init[turns.length - 1] = true;
    State.turnExpanded[expandKey] = init;
  }
  const expandMap = State.turnExpanded[expandKey];

  const container = el("div", {});

  // header card
  const headerCard = el("div", { class: "card", style: "margin-bottom: 14px;" }, [
    el("div", { class: "grid grid-3" }, [
      el("div", { class: "metric" }, [
        el("div", { class: "v" }, [fmtNum(turns.length)]),
        el("div", { class: "l" }, ["Turns"]),
      ]),
      el("div", { class: "metric" }, [
        el("div", { class: "v" }, [d.agent?.name || "—"]),
        el("div", { class: "l" }, ["Agent"]),
      ]),
      el("div", { class: "metric" }, [
        el("div", { class: "v" }, [fmtBytes(trajectoryTokenTotal(d))]),
        el("div", { class: "l" }, ["Tokens"]),
      ]),
    ]),
    toolUsageList.length > 0 && el("div", { style: "margin-top: 14px;" }, [
      el("div", { class: "section-label", style: "padding: 0 0 6px;" }, ["Tool usage"]),
      el("div", { class: "chip-row" }, toolUsageList.map(([name, count]) =>
        el("span", { class: "pill accent", style: "cursor: default;" }, [`${name} × ${count}`])
      )),
    ]),
    el("div", { class: "toolbar", style: "margin-top: 14px;" }, [
      el("button", {
        class: "btn-mini ghost",
        onclick: () => {
          const allOpen = turns.every((_, i) => expandMap[i]);
          for (let i = 0; i < turns.length; i++) expandMap[i] = !allOpen;
          render();
        },
      }, [turns.every((_, i) => expandMap[i]) ? "Collapse all" : "Expand all"]),
      el("span", { class: "muted small" }, [`${turns.length} turns · ${d.steps.length} steps`]),
    ]),
  ]);
  container.appendChild(headerCard);

  // preface (system/user setup before turn 0)
  const prefaceSteps = d.steps.filter(s => {
    const src = s.source || "";
    return (src === "system" || src === "user") && !s.tool_calls;
  }).slice(0, 4); // cap to avoid noise
  if (prefaceSteps.length > 0) {
    const preface = el("div", { class: "section", style: "margin-bottom: 14px;" });
    for (const s of prefaceSteps) {
      preface.appendChild(renderPrefaceCard(s));
    }
    container.appendChild(preface);
  }

  // turns
  const turnsContainer = el("div", { class: "turns-list" });
  turns.forEach((turn, i) => {
    turnsContainer.appendChild(renderTurnCard(turn, i, expandMap));
  });
  container.appendChild(turnsContainer);

  return container;
}

function buildTurns(steps) {
  // Each agent step typically contains BOTH tool_calls and the observation
  // returned for those tool_calls in the same record. We treat every step with
  // tool_calls as a new turn, attach an inline observation if present, and
  // also accept follow-up observation-only steps.
  const turns = [];
  let current = null;
  for (const s of steps) {
    const src = s.source || "";
    const hasTools = Array.isArray(s.tool_calls) && s.tool_calls.length > 0;
    const hasObs = !!s.observation;

    if (hasTools) {
      current = { action: s, observations: [], thought: extractThought(s) };
      if (hasObs) current.observations.push(s); // inline obs for this turn
      turns.push(current);
      continue;
    }

    if (hasObs) {
      // observation-only step — append to the current turn or orphan
      if (current) current.observations.push(s);
      else turns.push({ action: null, observations: [s], thought: "" });
      continue;
    }

    if (src === "agent" && hasMessageContent(s)) {
      turns.push({ action: s, observations: [], thought: extractThought(s) });
      current = null;
      continue;
    }

    // system/user are handled as preface; skip
  }
  return turns;
}

function hasMessageContent(s) {
  if (!s) return false;
  const m = s.message;
  if (!m) return false;
  if (typeof m === "string") return m.trim().length > 0;
  return true;
}

function extractThought(s) {
  // The agent's reasoning text accompanying tool calls (often empty for SDK).
  if (!s) return "";
  if (typeof s.message === "string") return s.message.trim();
  if (s.message && typeof s.message === "object") {
    if (typeof s.message.content === "string") return s.message.content.trim();
  }
  return "";
}

function renderPrefaceCard(s) {
  const src = s.source || "step";
  const text = typeof s.message === "string" ? s.message : JSON.stringify(s.message || {}, null, 2);
  const cls = src === "system" ? "preface-system" : "preface-user";
  return el("details", { class: "preface-card " + cls }, [
    el("summary", {}, [
      el("span", { class: "step-badge badge-" + src }, [src]),
      el("span", { class: "muted small" }, [`${text.length.toLocaleString()} chars`]),
      el("span", { class: "muted small" }, ["click to expand"]),
    ]),
    el("pre", { class: "preface-pre" }, [text]),
  ]);
}

function renderTurnCard(turn, idx, expandMap) {
  const expanded = !!expandMap[idx];
  const action = turn.action;
  const tools = (action && action.tool_calls) || [];
  const toolNames = tools.map(tc => tc.name || "?");
  const usage = action && action.usage;
  const completionTokens = usage ? (usage.completion_tokens ?? usage.total_completion_tokens ?? 0) : 0;

  const wrap = el("div", { class: "turn-card" + (expanded ? " open" : "") });
  const header = el("button", {
    class: "turn-head",
    onclick: () => { expandMap[idx] = !expandMap[idx]; render(); },
  }, [
    el("span", { class: "turn-chev" }, [expanded ? "▾" : "▸"]),
    el("span", { class: "turn-num" }, [`Turn ${idx + 1}`]),
    toolNames.length > 0 && el("span", { class: "turn-tools" }, [
      el("span", { class: "turn-tools-icon" }, ["⚙"]),
      ...toolNames.map(n => el("span", { class: "turn-tool-chip" }, [n])),
    ]),
    !action && turn.observations.length > 0 && el("span", { class: "muted small" }, ["(orphan observation)"]),
    el("span", { class: "turn-spacer" }, []),
    completionTokens > 0 && el("span", { class: "muted small mono" }, [`${completionTokens.toLocaleString()} tok`]),
    action?.timestamp && el("span", { class: "muted small mono", title: action.timestamp }, [fmtDateTime(action.timestamp)]),
  ]);
  wrap.appendChild(header);

  if (!expanded) return wrap;

  const body = el("div", { class: "turn-body" });

  if (turn.thought) {
    body.appendChild(el("div", { class: "block-thought" }, [
      el("div", { class: "block-head" }, [
        el("span", { class: "block-label" }, ["Thought"]),
      ]),
      el("pre", { class: "block-pre" }, [turn.thought]),
    ]));
  }

  // pair each tool call with its matching observation by source_call_id
  const obsByCallId = new Map();
  for (const o of turn.observations) {
    const obs = o.observation;
    if (obs && Array.isArray(obs.results)) {
      for (const r of obs.results) {
        if (r.source_call_id) obsByCallId.set(r.source_call_id, r);
      }
    }
  }

  tools.forEach((tc, ti) => {
    const actionBlock = el("div", { class: "block-action" }, [
      el("div", { class: "block-head" }, [
        el("span", { class: "block-label action" }, ["Action"]),
        el("span", { class: "block-tool-name" }, [tc.name || "?"]),
        tc.id && el("span", { class: "muted small mono" }, [tc.id]),
      ]),
      renderToolBody(tc),
    ]);
    body.appendChild(actionBlock);

    const matched = tc.id ? obsByCallId.get(tc.id) : null;
    // fallback to positional matching if no source_call_id
    const fallback = !matched && turn.observations[ti] && turn.observations[ti].observation;
    if (matched) {
      body.appendChild(renderObservationBlock(matched.content, matched.exit_code, matched.metadata));
    } else if (fallback) {
      const r = (fallback.results && fallback.results[ti]) || (fallback.results && fallback.results[0]);
      if (r) body.appendChild(renderObservationBlock(r.content, r.exit_code, r.metadata));
      else if (typeof fallback === "string") body.appendChild(renderObservationBlock(fallback));
    }
  });

  // unmatched observations (no tool calls or extras)
  if (tools.length === 0) {
    for (const o of turn.observations) {
      const obs = o.observation;
      if (obs && Array.isArray(obs.results)) {
        for (const r of obs.results) {
          body.appendChild(renderObservationBlock(r.content, r.exit_code, r.metadata));
        }
      } else if (typeof obs === "string") {
        body.appendChild(renderObservationBlock(obs));
      }
    }
  }

  if (!turn.thought && tools.length === 0 && hasMessageContent(action)) {
    const m = action.message;
    body.appendChild(el("div", { class: "block-thought" }, [
      el("div", { class: "block-head" }, [el("span", { class: "block-label" }, ["Message"])]),
      el("pre", { class: "block-pre" }, [typeof m === "string" ? m : JSON.stringify(m, null, 2)]),
    ]));
  }

  wrap.appendChild(body);
  return wrap;
}

function renderObservationBlock(content, exitCode, metadata) {
  const text = (content == null ? "" : (typeof content === "string" ? content : JSON.stringify(content, null, 2)));
  return el("div", { class: "block-observation" }, [
    el("div", { class: "block-head" }, [
      el("span", { class: "block-label observation" }, ["Observation"]),
      exitCode != null && el("span", { class: "pill " + (exitCode === 0 ? "good" : "bad") }, [`exit ${exitCode}`]),
      metadata && metadata.duration_ms != null && el("span", { class: "muted small mono" }, [`${(metadata.duration_ms / 1000).toFixed(2)}s`]),
    ]),
    el("pre", { class: "block-pre obs" }, [text || "(empty)"]),
  ]);
}

function renderToolBody(tc) {
  const name = (tc.name || "").toLowerCase();
  const args = tc.arguments_dict;
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return el("pre", { class: "block-pre args" }, [tc.arguments ? String(tc.arguments) : "(no args)"]);
  }
  const parts = [];

  if (name === "terminal" || name === "bash" || name === "shell") {
    if (args.command) parts.push(renderArgBlock("command", args.command, "shell"));
    if (args.is_input) parts.push(renderArgPill("is_input", "true (stdin)"));
    if (args.timeout != null) parts.push(renderArgPill("timeout", `${args.timeout}s`));
    if (args.reset) parts.push(renderArgPill("reset", "true"));
    if (args.summary) parts.push(renderArgPill("summary", args.summary));
  } else if (name === "file_editor" || name === "str_replace_editor") {
    const meta = el("div", { class: "arg-meta-row" }, [
      args.command && renderArgPill("command", args.command),
      (args.path || args.file_path) && renderArgPill("path", args.path || args.file_path),
      args.view_range && renderArgPill("view_range", JSON.stringify(args.view_range)),
      args.insert_line != null && renderArgPill("insert_line", String(args.insert_line)),
    ]);
    parts.push(meta);
    if (args.old_str !== undefined) parts.push(renderArgBlock("old_str", args.old_str, "diff-old"));
    if (args.new_str !== undefined) parts.push(renderArgBlock("new_str", args.new_str, "diff-new"));
    if (args.file_text !== undefined) parts.push(renderArgBlock("file_text", args.file_text));
  } else if (name === "think") {
    if (args.thought) parts.push(renderArgBlock("thought", args.thought));
  } else if (name === "finish") {
    Object.entries(args).forEach(([k, v]) => {
      parts.push(renderArgBlock(k, typeof v === "string" ? v : JSON.stringify(v, null, 2)));
    });
  } else {
    for (const [k, v] of Object.entries(args)) {
      if (v == null || v === false || v === "") continue;
      if (typeof v === "string" && (v.length > 60 || v.includes("\n"))) {
        parts.push(renderArgBlock(k, v));
      } else if (typeof v === "object") {
        parts.push(renderArgBlock(k, JSON.stringify(v, null, 2)));
      } else {
        parts.push(renderArgPill(k, String(v)));
      }
    }
  }
  return el("div", { class: "tool-body" }, parts.filter(Boolean));
}

function renderArgPill(key, value) {
  return el("div", { class: "arg-pill" }, [
    el("span", { class: "arg-key" }, [key]),
    el("span", { class: "arg-val mono" }, [value]),
  ]);
}

function renderArgBlock(key, value, variant) {
  const cls = "arg-block" + (variant ? " " + variant : "");
  return el("div", { class: cls }, [
    el("div", { class: "arg-key" }, [key]),
    el("pre", {}, [value]),
  ]);
}

function renderObservation(obs) {
  if (typeof obs === "string") {
    return el("div", { class: "obs-block" }, [el("pre", {}, [obs])]);
  }
  if (Array.isArray(obs.results)) {
    if (obs.results.length === 0) return el("div", { class: "obs-block muted" }, ["(empty)"]);
    return el("div", {}, obs.results.map((r, i) => {
      const head = el("div", { class: "obs-meta" }, [
        el("span", { class: "muted small" }, [`result #${i + 1}`]),
        r.source_call_id && el("span", { class: "muted small mono" }, [r.source_call_id]),
        r.exit_code != null && el("span", { class: "pill " + (r.exit_code === 0 ? "good" : "bad") }, [`exit ${r.exit_code}`]),
      ]);
      return el("div", { class: "obs-block" }, [
        head,
        el("pre", {}, [r.content || "(no content)"]),
      ]);
    }));
  }
  // generic
  return el("div", { class: "obs-block" }, [
    el("pre", {}, [JSON.stringify(obs, null, 2)]),
  ]);
}

// -- init ---------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  // theme
  document.documentElement.setAttribute("data-theme", State.theme);
  $("#theme-toggle").addEventListener("click", () => {
    State.theme = State.theme === "dark" ? "light" : "dark";
    localStorage.setItem("harbor.theme", State.theme);
    document.documentElement.setAttribute("data-theme", State.theme);
    $("#theme-toggle").textContent = State.theme === "dark" ? "☀" : "☾";
  });
  $("#theme-toggle").textContent = State.theme === "dark" ? "☀" : "☾";
  applyLanguage();
  $("#lang-toggle").addEventListener("click", async () => {
    State.lang = State.lang === "en" ? "zh" : "en";
    localStorage.setItem("harbor.lang", State.lang);
    applyLanguage();
    await render();
  });

  // reload
  $("#reload").addEventListener("click", () => { location.reload(); });

  // filter
  $("#job-filter").addEventListener("input", (e) => {
    State.filter = e.target.value;
    renderJobList();
  });

  // compare controls
  $("#open-compare").addEventListener("click", () => { setHash(["compare"]); });
  $("#clear-compare").addEventListener("click", () => {
    State.compareSet.clear();
    refreshCompareUi();
  });

  // initial load
  await applyHash();
  void loadJobs("lite");
});
