// Static-export mode.
//
// Normally the dashboard talks to `server.py`, which reads artifacts/model/ on
// every request. A Cloudflare Pages deploy has no such server — export_static.py
// snapshots the same payloads to files and rewrites index.html to set the flag
// below.
//
// The two layouts differ only in the URL shape, because a filesystem cannot hold
// both a file `api/runs` and a directory `api/runs/`. The export therefore writes
// `api/runs.json` alongside `api/runs/<id>/metrics.json`, and every request goes
// through `apiUrl()` so the same bundle serves both modes.
export const IS_STATIC =
  typeof window !== "undefined" &&
  (window as unknown as { __TRAINER_STATIC__?: boolean }).__TRAINER_STATIC__ ===
    true;

// Map a live API path to the exported file that holds the same payload. Query
// strings are dropped: a static host cannot vary a response by parameter, so the
// export always writes the unfiltered payload (all metric keys, the log tail).
export function apiUrl(path: string): string {
  if (!IS_STATIC) return path;
  const [base] = path.split("?");
  return `${base}.json`;
}
