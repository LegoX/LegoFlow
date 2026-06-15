import { createContext, useContext } from "react";

export type Lang = "en" | "zh";

const STORAGE_KEY = "lf-dashboard-lang";

export function getSavedLang(): Lang {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "zh" || saved === "en") return saved;
  return navigator.language.startsWith("zh") ? "zh" : "en";
}

export function saveLang(lang: Lang) {
  localStorage.setItem(STORAGE_KEY, lang);
}

const dict: Record<string, Record<Lang, string>> = {
  // App — sidebar / header
  "app.title": { en: "SWE-Lego-Live-SFT", zh: "SWE-Lego-Live-SFT" },
  "app.subtitle": { en: "Training Dashboard", zh: "训练看板" },
  "app.runs": { en: "Runs", zh: "运行记录" },
  "app.refresh": { en: "Refresh", zh: "刷新频率" },
  "app.noRuns": { en: "No runs found", zh: "暂无运行记录" },
  "app.steps": { en: "steps", zh: "步" },
  "app.live": { en: "Live", zh: "运行中" },
  "app.hardRefresh": { en: "Hard refresh", zh: "强制刷新" },
  "app.lightMode": { en: "Switch to light mode", zh: "切换亮色模式" },
  "app.darkMode": { en: "Switch to dark mode", zh: "切换暗色模式" },
  "app.langToggle": { en: "切换中文", zh: "Switch to EN" },

  // Nav panels
  "nav.overview": { en: "Overview", zh: "总览" },
  "nav.training": { en: "Training", zh: "训练曲线" },
  "nav.eval": { en: "Evaluation", zh: "验证评估" },
  "nav.performance": { en: "Performance", zh: "性能" },
  "nav.compare": { en: "Compare", zh: "对比" },
  "nav.analysis": { en: "AI Analysis", zh: "AI 分析" },
  "nav.logs": { en: "Logs", zh: "日志" },
  "nav.explorer": { en: "Explorer", zh: "指标浏览" },
  "nav.settings": { en: "Settings", zh: "设置" },

  // Overview panel
  "overview.title": { en: "Overview", zh: "总览" },
  "overview.step": { en: "Step", zh: "步数" },
  "overview.epoch": { en: "Epoch", zh: "轮次" },
  "overview.progress": { en: "Progress", zh: "进度" },
  "overview.loss": { en: "Train Loss", zh: "训练损失" },
  "overview.evalLoss": { en: "Eval Loss", zh: "验证损失" },
  "overview.lr": { en: "Learning Rate", zh: "学习率" },
  "overview.gradNorm": { en: "Grad Norm", zh: "梯度范数" },
  "overview.stepTime": { en: "Step Time", zh: "每步耗时" },
  "overview.eta": { en: "ETA", zh: "预计剩余" },

  // Training panel
  "train.title": { en: "Training", zh: "训练曲线" },
  "train.desc": {
    en: "Loss, learning rate, gradient norm, and epoch progress over steps",
    zh: "随训练步变化的损失、学习率、梯度范数与轮次进度",
  },
  "train.loss": { en: "Training Loss", zh: "训练损失" },
  "train.lossLog": { en: "Training Loss (smoothed)", zh: "训练损失（平滑）" },
  "train.lossVsEval": { en: "Train vs Eval Loss", zh: "训练 vs 验证损失" },
  "train.lr": { en: "Learning Rate", zh: "学习率" },
  "train.gradNorm": { en: "Gradient Norm", zh: "梯度范数" },
  "train.epoch": { en: "Epoch Progress", zh: "轮次进度" },

  // Eval panel
  "eval.title": { en: "Evaluation", zh: "验证评估" },
  "eval.desc": {
    en: "Validation loss measured periodically during training",
    zh: "训练过程中周期性评估的验证损失",
  },
  "eval.bestLoss": { en: "Best Eval Loss", zh: "最优验证损失" },
  "eval.latestLoss": { en: "Latest Eval Loss", zh: "最新验证损失" },
  "eval.bestStep": { en: "Best Step", zh: "最优步" },
  "eval.count": { en: "Evaluations", zh: "评估次数" },
  "eval.lossChart": { en: "Eval Loss", zh: "验证损失" },
  "eval.vsTrain": { en: "Eval vs Train Loss", zh: "验证 vs 训练损失" },
  "eval.empty": {
    en: "No evaluation results for this run. Set val_size / eval_steps in your training config to enable validation.",
    zh: "该运行没有验证结果。在训练配置中设置 val_size / eval_steps 即可开启验证。",
  },

  // Performance panel
  "perf.title": { en: "Performance", zh: "性能" },
  "perf.desc": {
    en: "Throughput, per-step timing, and total compute",
    zh: "吞吐量、每步耗时与总计算量",
  },
  "perf.samplesPerSec": { en: "Samples / s", zh: "样本/秒" },
  "perf.stepsPerSec": { en: "Steps / s", zh: "步/秒" },
  "perf.runtime": { en: "Train Runtime", zh: "训练总耗时" },
  "perf.totalFlos": { en: "Total FLOPs", zh: "总 FLOPs" },
  "perf.finalLoss": { en: "Final Train Loss", zh: "最终训练损失" },
  "perf.stepTimeChart": { en: "Time per Step (s)", zh: "每步耗时（秒）" },
  "perf.elapsedChart": { en: "Elapsed Time (s)", zh: "已用时（秒）" },
  "perf.etaChart": { en: "Estimated Remaining (s)", zh: "预计剩余（秒）" },
  "perf.summaryNote": {
    en: "Summary scalars are available once the run has finished (all_results.json).",
    zh: "运行结束后（all_results.json 生成）才有汇总标量。",
  },

  // Explorer panel
  "explorer.search": { en: "Search metrics...", zh: "搜索指标..." },
  "explorer.noMetrics": { en: "No metrics available yet", zh: "暂无指标数据" },
  "explorer.noMatch": { en: "No metrics match your search", zh: "无匹配的指标" },

  // Logs panel
  "logs.filter": { en: "Filter logs...", zh: "过滤日志..." },
  "logs.resume": { en: "Resume", zh: "继续" },
  "logs.pause": { en: "Pause", zh: "暂停" },
  "logs.scrollBottom": { en: "Scroll to bottom", zh: "滚动到底部" },
  "logs.noMatch": { en: "No matching lines", zh: "无匹配行" },
  "logs.noOutput": { en: "No log output yet", zh: "暂无日志输出" },

  // Settings panel
  "settings.title": { en: "Settings", zh: "设置" },
  "settings.desc": {
    en: "Configure LLM API profiles for AI-powered analysis reports. Keys are stored in your browser only.",
    zh: "配置 LLM API 档案以生成 AI 分析报告。密钥仅存储在浏览器本地。",
  },
  "settings.noProfiles": { en: "No profiles yet. Add one to enable AI analysis.", zh: "暂无档案，添加一个以启用 AI 分析。" },
  "settings.addProfile": { en: "Add Profile", zh: "添加档案" },
  "settings.editProfile": { en: "Edit Profile", zh: "编辑档案" },
  "settings.newProfile": { en: "New Profile", zh: "新建档案" },
  "settings.name": { en: "Name", zh: "名称" },
  "settings.baseUrl": { en: "API Base URL", zh: "API 基础地址" },
  "settings.model": { en: "Model", zh: "模型" },
  "settings.apiKey": { en: "API Key", zh: "API 密钥" },
  "settings.apiKeyHint": {
    en: "Stored in localStorage only. Never sent to our server — goes directly to the LLM API endpoint you configured.",
    zh: "仅存储在 localStorage 中，不会发送到我们的服务器 — 直接发往你配置的 LLM API。",
  },
  "settings.customPrompt": { en: "Custom Analysis Directions", zh: "自定义分析方向" },
  "settings.customPromptPlaceholder": {
    en: "e.g. Focus on whether the loss curve shows signs of overfitting after epoch 2, and whether the learning-rate schedule decays too fast.",
    zh: "例如：关注第 2 轮后损失曲线是否出现过拟合迹象，以及学习率调度是否衰减过快。",
  },
  "settings.customPromptHint": {
    en: "Optional. Describe your research focus or improvement directions. The report will include a dedicated section addressing your goals.",
    zh: "可选。描述你的研究重点或改进方向，报告中会专门增加一个章节来回应你的目标。",
  },
  "settings.save": { en: "Save", zh: "保存" },
  "settings.cancel": { en: "Cancel", zh: "取消" },
  "settings.saved": { en: "Saved!", zh: "已保存！" },

  // Analysis panel
  "analysis.title": { en: "AI Analysis", zh: "AI 分析" },
  "analysis.desc": {
    en: "Generate professional diagnostic reports using LLM analysis",
    zh: "使用 LLM 生成专业的训练诊断报告",
  },
  "analysis.generate": { en: "Generate Report", zh: "生成报告" },
  "analysis.generating": { en: "Generating...", zh: "生成中..." },
  "analysis.noProfile": {
    en: "No LLM profile configured. Go to Settings to add your API key and model to generate reports.",
    zh: "未配置 LLM 档案。请前往「设置」添加 API 密钥和模型以生成报告。",
  },
  "analysis.selectRun": {
    en: "Select a run from the sidebar to generate an analysis report.",
    zh: "从左侧栏选择一个运行记录来生成分析报告。",
  },
  "analysis.buildingCtx": { en: "Building analysis context...", zh: "正在构建分析上下文..." },
  "analysis.callingLLM": {
    en: "Calling LLM API (this may take 1-2 minutes)...",
    zh: "正在调用 LLM API（可能需要 1-2 分钟）...",
  },
  "analysis.failed": { en: "Generation failed", zh: "生成失败" },
  "analysis.download": { en: "Download as markdown", zh: "下载为 Markdown" },
  "analysis.delete": { en: "Delete report", zh: "删除报告" },
  "analysis.yourReports": { en: "Your Reports", zh: "我的报告" },
  "analysis.demoReports": { en: "Demo Reports", zh: "示例报告" },
  "analysis.noReports": {
    en: 'No reports yet. Select a run and click "Generate Report" to get an AI-powered diagnostic analysis.',
    zh: '暂无报告。选择一个运行记录并点击「生成报告」以获取 AI 诊断分析。',
  },

  // Compare panel
  "compare.title": { en: "Run Comparison", zh: "运行对比" },
  "compare.desc": { en: "Overlay metrics from multiple runs on the same charts", zh: "将多个运行的指标叠加在同一图表上对比" },
  "compare.selectRuns": { en: "Select runs to compare", zh: "选择要对比的运行" },
  "compare.selectMetrics": { en: "Metrics", zh: "指标" },
  "compare.noRuns": { en: "Select at least 2 runs from the list above to compare.", zh: "从上方列表中选择至少 2 个运行进行对比。" },
  "compare.loading": { en: "Loading metrics...", zh: "加载指标中..." },
  "compare.clear": { en: "Clear", zh: "清除" },
  "compare.downloadPNG": { en: "PNG", zh: "PNG" },
  "compare.downloadCSV": { en: "CSV", zh: "CSV" },

  // Analysis confirm modal
  "analysis.confirmTitle": { en: "Confirm Report Generation", zh: "确认生成报告" },
  "analysis.confirmMsg": { en: "Generate an AI analysis report for the following run?", zh: "确认为以下运行记录生成 AI 分析报告？" },
  "analysis.confirmRun": { en: "Run", zh: "运行记录" },
  "analysis.confirmModel": { en: "Model", zh: "模型" },
  "analysis.confirmProfile": { en: "Profile", zh: "档案" },
  "analysis.confirmCustom": { en: "Custom directions", zh: "自定义方向" },
  "analysis.confirmNone": { en: "(none)", zh: "（无）" },
  "analysis.confirmBtn": { en: "Confirm & Generate", zh: "确认并生成" },
  "analysis.cancelBtn": { en: "Cancel", zh: "取消" },
  "analysis.recoTitle": { en: "Top-Priority Recommendations", zh: "最优先建议" },
  "analysis.recoHint": {
    en: "These are the highest-impact suggestions distilled from the full analysis. Scroll down for detailed observations.",
    zh: "这些是从完整分析中提炼的最高影响力建议。向下滚动查看详细分析。",
  },
  "analysis.userDirectedTitle": { en: "Your Research Directions", zh: "你的研究方向" },
  "analysis.userDirectedHint": {
    en: "Focused analysis based on your custom directions configured in Settings.",
    zh: "基于你在「设置」中配置的自定义方向进行的定向分析。",
  },
};

export type TFunc = (key: string) => string;

export function createT(lang: Lang): TFunc {
  return (key: string) => dict[key]?.[lang] ?? dict[key]?.en ?? key;
}

export const I18nContext = createContext<{ lang: Lang; t: TFunc }>({
  lang: "en",
  t: createT("en"),
});

export function useT() {
  return useContext(I18nContext);
}
