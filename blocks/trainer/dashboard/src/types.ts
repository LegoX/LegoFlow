export interface RunInfo {
  id: string;
  name: string;
  state: "running" | "finished" | "crashed" | "unknown";
  created_at: string;
  source: "log" | "wandb";
}

export interface MetricPoint {
  step: number;
  [key: string]: number;
}

export interface MetricsData {
  run: RunInfo;
  metrics: MetricPoint[];
  available_keys: string[];
}

export interface PanelConfig {
  id: string;
  title: string;
  icon: string;
  metrics: ChartConfig[];
}

export interface ChartConfig {
  title: string;
  keys: string[];
  colors?: string[];
  yAxisLabel?: string;
  format?: "number" | "percent" | "duration" | "int";
}

export interface RunConfig {
  run_id: string;
  summary: Record<string, number>;
  model_config: Record<string, unknown>;
}

export type DataSource = "auto" | "log" | "wandb";

export interface DashboardSettings {
  dataSource: DataSource;
  refreshInterval: number;
  wandbEntity: string;
  wandbProject: string;
  wandbApiKey: string;
  logDir: string;
  selectedRun: string;
}
