import ChartPanel from "../components/Chart";
import MetricCard from "../components/MetricCard";
import type { MetricPoint } from "../types";
import { useT } from "../i18n";

interface Props {
  data: MetricPoint[];
}

export default function EvalPanel({ data }: Props) {
  const { t } = useT();
  const evals = data.filter((p) => p.eval_loss !== undefined);

  if (evals.length === 0) {
    return (
      <div>
        <h2 className="text-lg font-semibold text-slate-100 mb-1">{t("eval.title")}</h2>
        <p className="text-xs text-slate-500 mb-4">{t("eval.desc")}</p>
        <div className="rounded-xl bg-slate-900/80 border border-slate-800/60 p-8 text-center text-sm text-slate-500">
          {t("eval.empty")}
        </div>
      </div>
    );
  }

  const losses = evals.map((p) => p.eval_loss as number);
  const bestIdx = losses.indexOf(Math.min(...losses));
  const best = evals[bestIdx];

  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-100 mb-1">{t("eval.title")}</h2>
      <p className="text-xs text-slate-500 mb-4">{t("eval.desc")}</p>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <MetricCard label={t("eval.bestLoss")} metricKey="eval_loss" data={[best]} color="#4a9440" />
        <MetricCard label={t("eval.latestLoss")} metricKey="eval_loss" data={evals} color="#199e70" />
        <div className="rounded-xl bg-slate-900/80 border border-slate-800/60 p-4">
          <span className="text-xs text-slate-400 font-medium">{t("eval.bestStep")}</span>
          <div className="text-2xl font-semibold text-slate-100 font-mono mt-2">{best.step}</div>
        </div>
        <div className="rounded-xl bg-slate-900/80 border border-slate-800/60 p-4">
          <span className="text-xs text-slate-400 font-medium">{t("eval.count")}</span>
          <div className="text-2xl font-semibold text-slate-100 font-mono mt-2">{evals.length}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartPanel title={t("eval.lossChart")} data={evals} keys={["eval_loss"]} colors={["#4a9440"]} showArea />
        <ChartPanel title={t("eval.vsTrain")} data={data} keys={["loss", "eval_loss"]} colors={["#e66767", "#4a9440"]} />
      </div>
    </div>
  );
}
