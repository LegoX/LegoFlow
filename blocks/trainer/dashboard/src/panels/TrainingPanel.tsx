import ChartPanel from "../components/Chart";
import type { MetricPoint } from "../types";
import { useT } from "../i18n";

interface Props {
  data: MetricPoint[];
}

export default function TrainingPanel({ data }: Props) {
  const { t } = useT();
  const hasEval = data.some((p) => p.eval_loss !== undefined);
  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-100 mb-1">{t("train.title")}</h2>
      <p className="text-xs text-slate-500 mb-4">{t("train.desc")}</p>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartPanel
          title={t("train.loss")}
          data={data}
          keys={["loss"]}
          colors={["#e66767"]}
          showArea
        />
        {hasEval ? (
          <ChartPanel
            title={t("train.lossVsEval")}
            data={data}
            keys={["loss", "eval_loss"]}
            colors={["#e66767", "#4a9440"]}
          />
        ) : (
          <ChartPanel
            title={t("train.gradNorm")}
            data={data}
            keys={["grad_norm"]}
            colors={["#c98500"]}
            showArea
          />
        )}
        <ChartPanel
          title={t("train.lr")}
          data={data}
          keys={["lr"]}
          colors={["#fab219"]}
          showArea
        />
        {hasEval && (
          <ChartPanel
            title={t("train.gradNorm")}
            data={data}
            keys={["grad_norm"]}
            colors={["#c98500"]}
            showArea
          />
        )}
        <ChartPanel
          title={t("train.epoch")}
          data={data}
          keys={["epoch"]}
          colors={["#9085e9"]}
        />
      </div>
    </div>
  );
}
