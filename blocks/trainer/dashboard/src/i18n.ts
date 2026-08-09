import { createContext, useContext } from "react";

export type Lang = "en";

const dict: Record<string, string> = {
  "app.title": "LegoFlow Trainer",
  "app.subtitle": "Training Dashboard",
  "app.runs": "Runs",
  "app.refresh": "Refresh",
  "app.noRuns": "No runs found",
  "app.steps": "steps",
  "app.live": "Live",
  "app.hardRefresh": "Hard refresh",
  "app.lightMode": "Switch to light mode",
  "app.darkMode": "Switch to dark mode",

  "nav.overview": "Overview",
  "nav.training": "Training",
  "nav.eval": "Evaluation",
  "nav.performance": "Performance",
  "nav.compare": "Compare",
  "nav.analysis": "AI Analysis",
  "nav.logs": "Logs",
  "nav.explorer": "Explorer",
  "nav.settings": "Settings",

  "overview.title": "Overview",
  "overview.step": "Step",
  "overview.epoch": "Epoch",
  "overview.progress": "Progress",
  "overview.loss": "Train Loss",
  "overview.evalLoss": "Eval Loss",
  "overview.lr": "Learning Rate",
  "overview.gradNorm": "Grad Norm",
  "overview.stepTime": "Step Time",
  "overview.eta": "ETA",

  "train.title": "Training",
  "train.desc":
    "Loss, learning rate, gradient norm, and epoch progress over steps",
  "train.loss": "Training Loss",
  "train.lossLog": "Training Loss (smoothed)",
  "train.lossVsEval": "Train vs Eval Loss",
  "train.lr": "Learning Rate",
  "train.gradNorm": "Gradient Norm",
  "train.epoch": "Epoch Progress",

  "eval.title": "Evaluation",
  "eval.desc": "Validation loss measured periodically during training",
  "eval.bestLoss": "Best Eval Loss",
  "eval.latestLoss": "Latest Eval Loss",
  "eval.bestStep": "Best Step",
  "eval.count": "Evaluations",
  "eval.lossChart": "Eval Loss",
  "eval.vsTrain": "Eval vs Train Loss",
  "eval.empty":
    "No evaluation results for this run. Set val_size / eval_steps in your training config to enable validation.",

  "perf.title": "Performance",
  "perf.desc": "Throughput, per-step timing, and total compute",
  "perf.samplesPerSec": "Samples / s",
  "perf.stepsPerSec": "Steps / s",
  "perf.runtime": "Train Runtime",
  "perf.totalFlos": "Total FLOPs",
  "perf.finalLoss": "Final Train Loss",
  "perf.stepTimeChart": "Time per Step (s)",
  "perf.elapsedChart": "Elapsed Time (s)",
  "perf.etaChart": "Estimated Remaining (s)",
  "perf.summaryNote":
    "Summary scalars are available once the run has finished (all_results.json).",

  "explorer.search": "Search metrics...",
  "explorer.noMetrics": "No metrics available yet",
  "explorer.noMatch": "No metrics match your search",

  "logs.filter": "Filter logs...",
  "logs.resume": "Resume",
  "logs.pause": "Pause",
  "logs.scrollBottom": "Scroll to bottom",
  "logs.noMatch": "No matching lines",
  "logs.noOutput": "No log output yet",

  "settings.title": "Settings",
  "settings.desc":
    "Configure LLM API profiles for AI-powered analysis reports. Keys are stored in your browser only.",
  "settings.noProfiles": "No profiles yet. Add one to enable AI analysis.",
  "settings.addProfile": "Add Profile",
  "settings.editProfile": "Edit Profile",
  "settings.newProfile": "New Profile",
  "settings.name": "Name",
  "settings.baseUrl": "API Base URL",
  "settings.model": "Model",
  "settings.apiKey": "API Key",
  "settings.apiKeyHint":
    "Stored in localStorage only. Never sent to our server — goes directly to the LLM API endpoint you configured.",
  "settings.customPrompt": "Custom Analysis Directions",
  "settings.customPromptPlaceholder":
    "e.g. Focus on whether the loss curve shows signs of overfitting after epoch 2, and whether the learning-rate schedule decays too fast.",
  "settings.customPromptHint":
    "Optional. Describe your research focus or improvement directions. The report will include a dedicated section addressing your goals.",
  "settings.save": "Save",
  "settings.cancel": "Cancel",
  "settings.saved": "Saved!",

  "analysis.title": "AI Analysis",
  "analysis.desc": "Generate professional diagnostic reports using LLM analysis",
  "analysis.generate": "Generate Report",
  "analysis.generating": "Generating...",
  "analysis.noProfile":
    "No LLM profile configured. Go to Settings to add your API key and model to generate reports.",
  "analysis.selectRun":
    "Select a run from the sidebar to generate an analysis report.",
  "analysis.buildingCtx": "Building analysis context...",
  "analysis.callingLLM": "Calling LLM API (this may take 1-2 minutes)...",
  "analysis.failed": "Generation failed",
  "analysis.download": "Download as markdown",
  "analysis.delete": "Delete report",
  "analysis.yourReports": "Your Reports",
  "analysis.demoReports": "Demo Reports",
  "analysis.noReports":
    'No reports yet. Select a run and click "Generate Report" to get an AI-powered diagnostic analysis.',

  "compare.title": "Run Comparison",
  "compare.desc": "Overlay metrics from multiple runs on the same charts",
  "compare.selectRuns": "Select runs to compare",
  "compare.selectMetrics": "Metrics",
  "compare.noRuns": "Select at least 2 runs from the list above to compare.",
  "compare.loading": "Loading metrics...",
  "compare.clear": "Clear",
  "compare.downloadPNG": "PNG",
  "compare.downloadCSV": "CSV",

  "analysis.confirmTitle": "Confirm Report Generation",
  "analysis.confirmMsg":
    "Generate an AI analysis report for the following run?",
  "analysis.confirmRun": "Run",
  "analysis.confirmModel": "Model",
  "analysis.confirmProfile": "Profile",
  "analysis.confirmCustom": "Custom directions",
  "analysis.confirmNone": "(none)",
  "analysis.confirmBtn": "Confirm & Generate",
  "analysis.cancelBtn": "Cancel",
  "analysis.recoTitle": "Top-Priority Recommendations",
  "analysis.recoHint":
    "These are the highest-impact suggestions distilled from the full analysis. Scroll down for detailed observations.",
  "analysis.userDirectedTitle": "Your Research Directions",
  "analysis.userDirectedHint":
    "Focused analysis based on your custom directions configured in Settings.",
};

export type TFunc = (key: string) => string;

export function createT(): TFunc {
  return (key: string) => dict[key] ?? key;
}

export const I18nContext = createContext<{ lang: Lang; t: TFunc }>({
  lang: "en",
  t: createT(),
});

export function useT() {
  return useContext(I18nContext);
}
