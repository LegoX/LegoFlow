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
        <MetricCard label={t("overview.step")} metricKey="step" data={data} format="int" color="#efa07c" />
        <MetricCard label={t("overview.epoch")} metricKey="epoch" data={data} format="number" color="#9085e9" />
        <MetricCard label={t("overview.progress")} metricKey="percentage" data={data} format="pct100" color="#199e70" />
        <MetricCard label={t("overview.loss")} metricKey="loss" data={data} color="#e66767" />
        <MetricCard label={t("overview.evalLoss")} metricKey="eval_loss" data={data} color="#4a9440" />
        <MetricCard label={t("overview.lr")} metricKey="lr" data={data} color="#fab219" />
        <MetricCard label={t("overview.gradNorm")} metricKey="grad_norm" data={data} color="#c98500" />
        <MetricCard label={t("overview.stepTime")} metricKey="step_time_sec" data={data} format="duration" color="#d55181" />
        <MetricCard label={t("overview.eta")} metricKey="remaining_sec" data={data} format="duration" color="#3987e5" />
      </div>
    </div>
  );
}
