import type { RunInfo, MetricPoint, MetricsData, RunConfig } from "../types";

const BASE = "/api";

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function fetchRuns(): Promise<RunInfo[]> {
  return fetchJson(`${BASE}/runs`);
}

export async function fetchMetrics(
  runId: string,
  keys?: string[],
): Promise<MetricsData> {
  const params = new URLSearchParams();
  if (keys?.length) params.set("keys", keys.join(","));
  const qs = params.toString();
  return fetchJson(`${BASE}/runs/${runId}/metrics${qs ? `?${qs}` : ""}`);
}

export async function fetchLatest(runId: string): Promise<MetricPoint | null> {
  return fetchJson(`${BASE}/runs/${runId}/latest`);
}

export async function fetchConfig(): Promise<{
  save_dirs: string[];
  log_dirs: string[];
  wandb_entity: string;
  wandb_project: string;
  data_source: string;
}> {
  return fetchJson(`${BASE}/config`);
}

export async function fetchRunConfig(runId: string): Promise<RunConfig> {
  return fetchJson(`${BASE}/runs/${runId}/config`);
}
