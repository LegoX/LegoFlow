import { useState, useEffect, useCallback, useMemo } from "react";
import {
  Activity,
  LineChart,
  Gauge,
  RefreshCw,
  ChevronRight,
  Radio,
  Server,
  ScrollText,
  FlaskConical,
  Sparkles,
  Settings,
  ClipboardCheck,
  GitCompareArrows,
  Sun,
  Moon,
  Languages,
  Info,
} from "lucide-react";
import { useRuns, useMetrics } from "./hooks/useMetrics";
import type { RunInfo } from "./types";
import OverviewPanel from "./panels/OverviewPanel";
import TrainingPanel from "./panels/TrainingPanel";
import EvalPanel from "./panels/EvalPanel";
import PerformancePanel from "./panels/PerformancePanel";
import LogsPanel from "./panels/LogsPanel";
import ExplorerPanel from "./panels/ExplorerPanel";
import AnalysisPanel from "./panels/AnalysisPanel";
import ComparePanel from "./panels/ComparePanel";
import SettingsPanel from "./panels/SettingsPanel";
import {
  I18nContext,
  getSavedLang,
  saveLang,
  createT,
  type Lang,
} from "./i18n";

const PANELS = [
  { id: "overview", labelKey: "nav.overview", icon: Activity },
  { id: "training", labelKey: "nav.training", icon: LineChart },
  { id: "eval", labelKey: "nav.eval", icon: ClipboardCheck },
  { id: "performance", labelKey: "nav.performance", icon: Gauge },
  { id: "compare", labelKey: "nav.compare", icon: GitCompareArrows },
  { id: "analysis", labelKey: "nav.analysis", icon: Sparkles },
  { id: "logs", labelKey: "nav.logs", icon: ScrollText },
  { id: "explorer", labelKey: "nav.explorer", icon: FlaskConical },
  { id: "settings", labelKey: "nav.settings", icon: Settings },
] as const;

function RunSelector({
  runs,
  selected,
  onSelect,
}: {
  runs: RunInfo[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="space-y-1">
      {runs.map((r) => (
        <button
          key={r.id}
          onClick={() => onSelect(r.id)}
          className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-colors ${
            selected === r.id
              ? "bg-indigo-500/20 text-indigo-300 border border-indigo-500/30"
              : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-300 border border-transparent"
          }`}
        >
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full flex-shrink-0 ${
                r.state === "running"
                  ? "bg-emerald-400 animate-pulse"
                  : r.state === "finished"
                    ? "bg-slate-500"
                    : "bg-amber-500"
              }`}
            />
            <span className="truncate font-mono">{r.name}</span>
          </div>
          <div className="flex items-center gap-2 mt-1 ml-4">
            <span className="text-[10px] text-slate-500 uppercase">
              {r.source}
            </span>
            <span className="text-[10px] text-slate-600">{r.state}</span>
          </div>
        </button>
      ))}
      {runs.length === 0 && (
        <p className="text-xs text-slate-600 px-3 py-4 text-center">
          No runs found
        </p>
      )}
    </div>
  );
}

type Theme = "dark" | "light";

function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem("harbor-theme");
    return saved === "light" ? "light" : "dark";
  });

  useEffect(() => {
    document.body.setAttribute("data-theme", theme);
    localStorage.setItem("harbor-theme", theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggle };
}

export default function App() {
  const [activePanel, setActivePanel] = useState("overview");
  const [selectedRun, setSelectedRun] = useState("");
  const [infoOpen, setInfoOpen] = useState(false);
  const [refreshInterval, setRefreshInterval] = useState(15);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const { theme, toggle: toggleTheme } = useTheme();
  const [lang, setLang] = useState<Lang>(getSavedLang);
  const t = useMemo(() => createT(lang), [lang]);
  const toggleLang = useCallback(() => {
    const next = lang === "en" ? "zh" : "en";
    setLang(next);
    saveLang(next);
  }, [lang]);

  const { runs, error: runsError } = useRuns(refreshInterval);
  const { data, loading, error: metricsError } = useMetrics(
    selectedRun || null,
    refreshInterval,
  );

  useEffect(() => {
    if (!selectedRun && runs.length > 0) {
      const running = runs.find((r) => r.state === "running");
      setSelectedRun(running?.id || runs[0].id);
    }
  }, [runs, selectedRun]);

  const metrics = data?.metrics || [];
  const availableKeys = data?.available_keys || [];

  const handleRefresh = useCallback(() => {
    window.location.reload();
  }, []);

  return (
    <I18nContext.Provider value={{ lang, t }}>
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside
        className={`${
          sidebarOpen ? "w-64" : "w-0"
        } flex-shrink-0 transition-all duration-200 overflow-hidden`}
      >
        <div className="w-64 h-full flex flex-col bg-slate-900/50 border-r border-slate-800/60">
          {/* Logo */}
          <div className="px-4 py-4 border-b border-slate-800/60">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-indigo-500 flex items-center justify-center">
                <span className="text-white text-xs font-bold font-mono">
                  LF
                </span>
              </div>
              <div>
                <h1 className="text-sm font-semibold text-slate-100">
                  {t("app.title")}
                </h1>
                <p className="text-[10px] text-slate-500">{t("app.subtitle")}</p>
              </div>
            </div>
          </div>

          {/* Navigation */}
          <nav className="px-2 py-3 space-y-0.5">
            {PANELS.map(({ id, labelKey, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setActivePanel(id)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                  activePanel === id
                    ? "bg-slate-800/80 text-slate-100"
                    : "text-slate-400 hover:text-slate-300 hover:bg-slate-800/40"
                }`}
              >
                <Icon size={15} />
                {t(labelKey)}
              </button>
            ))}
          </nav>

          {/* Runs */}
          <div className="flex-1 overflow-y-auto border-t border-slate-800/60 px-2 py-3">
            <div className="flex items-center gap-1.5 px-3 mb-2">
              <Server size={12} className="text-slate-500" />
              <span className="text-[10px] uppercase tracking-wider text-slate-500 font-medium">
                {t("app.runs")}
              </span>
            </div>
            <RunSelector
              runs={runs}
              selected={selectedRun}
              onSelect={setSelectedRun}
            />
            {runsError && (
              <p className="text-[10px] text-rose-400 px-3 mt-2">
                {runsError}
              </p>
            )}
          </div>

          {/* Settings */}
          <div className="px-4 py-3 border-t border-slate-800/60">
            <div className="flex items-center justify-between">
              <label className="text-[10px] text-slate-500 uppercase tracking-wider">
                {t("app.refresh")}
              </label>
              <select
                value={refreshInterval}
                onChange={(e) => setRefreshInterval(Number(e.target.value))}
                className="bg-slate-800 text-slate-300 text-xs border border-slate-700 rounded px-1.5 py-0.5 focus:outline-none focus:border-indigo-500"
              >
                <option value={5}>5s</option>
                <option value={10}>10s</option>
                <option value={15}>15s</option>
                <option value={30}>30s</option>
                <option value={60}>60s</option>
              </select>
            </div>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="h-12 flex-shrink-0 flex items-center justify-between px-4 border-b border-slate-800/60 bg-slate-950/50">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="inline-flex items-center justify-center w-9 h-9 rounded-lg border border-slate-800 bg-slate-900/60 text-slate-400 hover:text-slate-200 hover:border-indigo-500 transition-colors"
            >
              <ChevronRight
                size={16}
                className={`transform transition-transform ${sidebarOpen ? "rotate-180" : ""}`}
              />
            </button>
            <div className="flex items-center gap-2 text-sm">
              <span className="text-slate-400">
                {t(PANELS.find((p) => p.id === activePanel)?.labelKey ?? "")}
              </span>
              {selectedRun && (
                <>
                  <span className="text-slate-600">/</span>
                  <span className="text-slate-300 font-mono text-xs">
                    {selectedRun}
                  </span>
                </>
              )}
            </div>
          </div>

          <div className="flex items-center gap-3">
            {loading && (
              <RefreshCw size={14} className="text-indigo-400 animate-spin" />
            )}
            {data?.run?.state === "running" && (
              <span className="flex items-center gap-1.5 text-xs text-emerald-400">
                <Radio size={12} className="animate-pulse" />
                {t("app.live")}
              </span>
            )}
            {metricsError && (
              <span className="text-xs text-rose-400">{metricsError}</span>
            )}
            <span className="text-xs text-slate-500 font-mono">
              {metrics.length} {t("app.steps")}
            </span>
            <div className="relative">
              <button
                onClick={() => setInfoOpen((v) => !v)}
                className="inline-flex items-center justify-center w-9 h-9 rounded-lg border border-slate-800 bg-slate-900/60 text-slate-400 hover:text-slate-200 hover:border-indigo-500 transition-colors"
                title={t("app.info")}
              >
                <Info size={14} />
              </button>
              {infoOpen && (
                <div
                  className="fixed inset-0 z-[200] flex items-center justify-center p-6 bg-black/60"
                  role="dialog"
                  aria-modal="true"
                  onClick={(e) => {
                    // backdrop only; clicks inside the box must not close it
                    if (e.target === e.currentTarget) setInfoOpen(false);
                  }}
                >
                  <div className="w-[min(720px,100%)] max-h-[78vh] flex flex-col rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl overflow-hidden text-left">
                    <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-slate-800">
                      <div>
                        <h3 className="text-base font-semibold">{t("app.info")}</h3>
                        <div className="text-xs text-slate-400 mt-1">resolved at load time</div>
                      </div>
                      <button
                        onClick={() => setInfoOpen(false)}
                        className="w-8 h-8 rounded-lg text-slate-400 hover:text-slate-200 hover:border hover:border-slate-800 text-lg leading-none"
                        aria-label="Close"
                      >
                        ×
                      </button>
                    </div>
                    <div className="px-5 py-4 overflow-auto">
                      <table className="w-full text-xs">
                        <tbody>
                          {[
                            ["runs found", String(runs.length)],
                            ["selected run", selectedRun || "\u2014"],
                            ["refresh interval", `${refreshInterval / 1000}s`],
                            ["API served from", location.origin],
                          ].map(([k, v]) => (
                            <tr key={k} className="border-b border-slate-800">
                              <td className="py-1.5 pr-2 text-slate-400">{k}</td>
                              <td className="py-1.5 font-mono break-all">{v}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              )}
            </div>
            <button
              onClick={toggleLang}
              className="inline-flex items-center justify-center w-9 h-9 rounded-lg border border-slate-800 bg-slate-900/60 text-slate-400 hover:text-slate-200 hover:border-indigo-500 transition-colors"
              title={t("app.langToggle")}
            >
              <Languages size={14} />
            </button>
            <button
              onClick={toggleTheme}
              className="inline-flex items-center justify-center w-9 h-9 rounded-lg border border-slate-800 bg-slate-900/60 text-slate-400 hover:text-slate-200 hover:border-indigo-500 transition-colors"
              title={theme === "dark" ? t("app.lightMode") : t("app.darkMode")}
            >
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
            </button>
            <button
              onClick={handleRefresh}
              className="inline-flex items-center justify-center w-9 h-9 rounded-lg border border-slate-800 bg-slate-900/60 text-slate-400 hover:text-slate-200 hover:border-indigo-500 transition-colors"
              title={t("app.hardRefresh")}
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </header>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6">
          {activePanel === "overview" && <OverviewPanel data={metrics} />}
          {activePanel === "training" && <TrainingPanel data={metrics} />}
          {activePanel === "eval" && <EvalPanel data={metrics} />}
          {activePanel === "performance" && (
            <PerformancePanel data={metrics} runId={selectedRun || null} />
          )}
          {activePanel === "compare" && <ComparePanel runs={runs} />}
          {activePanel === "analysis" && (
            <AnalysisPanel runId={selectedRun || null} />
          )}
          {activePanel === "logs" && (
            <LogsPanel
              runId={selectedRun || null}
              refreshInterval={refreshInterval}
            />
          )}
          {activePanel === "explorer" && (
            <ExplorerPanel data={metrics} availableKeys={availableKeys} />
          )}
          {activePanel === "settings" && <SettingsPanel />}
        </div>
      </main>
    </div>
    </I18nContext.Provider>
  );
}
