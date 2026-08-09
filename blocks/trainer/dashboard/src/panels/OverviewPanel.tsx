import MetricCard from "../components/MetricCard";
import type { MetricPoint } from "../types";
import { useT } from "../i18n";

interface Props {
  data: MetricPoint[];
}

export default function OverviewPanel({ data }: Props) {
  const { t } = useT();
  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-100 mb-4">{t("overview.title")}</h2>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
        <MetricCard label={t("overview.step")} metricKey="step" data={data} format="int" color="#6366f1" />
        <MetricCard label={t("overview.epoch")} metricKey="epoch" data={data} format="number" color="#8b5cf6" />
        <MetricCard label={t("overview.progress")} metricKey="percentage" data={data} format="pct100" color="#06b6d4" />
        <MetricCard label={t("overview.loss")} metricKey="loss" data={data} color="#f43f5e" />
        <MetricCard label={t("overview.evalLoss")} metricKey="eval_loss" data={data} color="#10b981" />
        <MetricCard label={t("overview.lr")} metricKey="lr" data={data} color="#f59e0b" />
        <MetricCard label={t("overview.gradNorm")} metricKey="grad_norm" data={data} color="#84cc16" />
        <MetricCard label={t("overview.stepTime")} metricKey="step_time_sec" data={data} format="duration" color="#ec4899" />
        <MetricCard label={t("overview.eta")} metricKey="remaining_sec" data={data} format="duration" color="#0ea5e9" />
      </div>
    </div>
  );
}
