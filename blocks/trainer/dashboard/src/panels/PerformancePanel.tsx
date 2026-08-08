import { useState, useEffect } from "react";
import ChartPanel from "../components/Chart";
import type { MetricPoint, RunConfig } from "../types";
import { fetchRunConfig } from "../api/client";
import { useT } from "../i18n";

interface Props {
  data: MetricPoint[];
  runId: string | null;
}

function fmtRuntime(sec: number): string {
  if (sec >= 3600) return `${(sec / 3600).toFixed(1)}h`;
  if (sec >= 60) return `${(sec / 60).toFixed(1)}m`;
  return `${sec.toFixed(0)}s`;
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-slate-900/80 border border-slate-800/60 p-4">
      <span className="text-xs text-slate-400 font-medium">{label}</span>
      <div className="text-2xl font-semibold text-slate-100 font-mono mt-2 truncate">{value}</div>
    </div>
  );
}

export default function PerformancePanel({ data, runId }: Props) {
  const { t } = useT();
  const [config, setConfig] = useState<RunConfig | null>(null);

  useEffect(() => {
    setConfig(null);
    if (!runId) return;
    fetchRunConfig(runId)
      .then(setConfig)
      .catch(() => setConfig(null));
  }, [runId]);

  const s = config?.summary ?? {};
  const hasSummary = Object.keys(s).length > 0;

  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-100 mb-1">{t("perf.title")}</h2>
      <p className="text-xs text-slate-500 mb-4">{t("perf.desc")}</p>

      {hasSummary ? (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-6">
          <SummaryCard label={t("perf.samplesPerSec")} value={s.train_samples_per_second?.toFixed(3) ?? "--"} />
          <SummaryCard label={t("perf.stepsPerSec")} value={s.train_steps_per_second?.toFixed(4) ?? "--"} />
          <SummaryCard label={t("perf.runtime")} value={s.train_runtime ? fmtRuntime(s.train_runtime) : "--"} />
          <SummaryCard label={t("perf.totalFlos")} value={s.total_flos ? s.total_flos.toExponential(2) : "--"} />
          <SummaryCard label={t("perf.finalLoss")} value={s.train_loss?.toFixed(4) ?? "--"} />
        </div>
      ) : (
        <p className="text-xs text-slate-600 mb-6">{t("perf.summaryNote")}</p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartPanel title={t("perf.stepTimeChart")} data={data} keys={["step_time_sec"]} colors={["#fab219"]} yAxisLabel="seconds" showArea />
        <ChartPanel title={t("perf.elapsedChart")} data={data} keys={["elapsed_sec"]} colors={["#efa07c"]} />
        <ChartPanel title={t("perf.etaChart")} data={data} keys={["remaining_sec"]} colors={["#199e70"]} />
      </div>
    </div>
  );
}
