import { useState, useEffect, useCallback, useRef } from "react";
import type { RunInfo, MetricsData } from "../types";
import { fetchRuns, fetchMetrics } from "../api/client";

export function useRuns(refreshInterval: number) {
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchRuns();
      setRuns(data);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, refreshInterval * 1000);
    return () => clearInterval(id);
  }, [load, refreshInterval]);

  return { runs, error, reload: load };
}

export function useMetrics(runId: string | null, refreshInterval: number) {
  const [data, setData] = useState<MetricsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prevRunId = useRef(runId);

  const load = useCallback(async () => {
    if (!runId) return;
    if (prevRunId.current !== runId) {
      setData(null);
      prevRunId.current = runId;
    }
    try {
      setLoading(true);
      const result = await fetchMetrics(runId);
      setData(result);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    load();
    const id = setInterval(load, refreshInterval * 1000);
    return () => clearInterval(id);
  }, [load, refreshInterval]);

  return { data, loading, error, reload: load };
}
