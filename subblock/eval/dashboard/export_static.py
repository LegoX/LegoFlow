#!/usr/bin/env python3
"""Export Harbor Job Dashboard as a static Cloudflare Pages site."""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from server import (
    DEFAULT_JOBS,
    HERE,
    STATIC_DIR,
    JobsRepo,
    _count_jsonl_records,
    _extracted_report_path,
    _extracted_trace_path,
    _json_default,
    percent,
)

LOG = logging.getLogger("harbor.webui.export_static")

STATE_FILE_NAME = ".export_state.json"
STATE_VERSION = 3

STATIC_ASSETS = ("index.html", "app.js", "styles.css", "favicon.svg")
WORKER_JS = r'''export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const trajectory = matchTrajectory(url);

    if (trajectory) {
      if (!env.TRAJECTORIES) {
        return jsonResponse({ error: "R2 binding TRAJECTORIES is not configured" }, 500);
      }
      const key = `${trajectory.job}/${trajectory.trial}/trajectory_${trajectory.kind}.json`;
      const object = await env.TRAJECTORIES.get(key);
      if (!object) {
        return jsonResponse({ error: "trajectory not found", key }, 404);
      }
      const headers = new Headers(object.httpMetadata || {});
      headers.set("Content-Type", "application/json; charset=utf-8");
      headers.set("Cache-Control", "public, max-age=86400");
      return new Response(object.body, { headers });
    }

    return env.ASSETS.fetch(request);
  },
};

function matchTrajectory(url) {
  let match = url.pathname.match(/^\/api\/jobs\/([^/]+)\/trials\/([^/]+)\/trajectory$/);
  if (match) {
    return {
      job: decodeURIComponent(match[1]),
      trial: decodeURIComponent(match[2]),
      kind: safeKind(url.searchParams.get("kind") || "agent"),
    };
  }

  match = url.pathname.match(/^\/api\/jobs\/([^/]+)\/trials\/([^/]+)\/trajectory_([^/]+)\.json$/);
  if (match) {
    return {
      job: decodeURIComponent(match[1]),
      trial: decodeURIComponent(match[2]),
      kind: safeKind(decodeURIComponent(match[3])),
    };
  }

  return null;
}

function safeKind(kind) {
  return String(kind || "agent").replace(/[^a-zA-Z0-9_-]/g, "") || "agent";
}

function jsonResponse(payload, status) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}
'''


def encoded_segment(value: str) -> str:
    return quote(value, safe="")


def json_text(payload: Any) -> str:
    return json.dumps(payload, default=_json_default, ensure_ascii=False, separators=(",", ":"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json_text(payload),
        encoding="utf-8",
    )


def build_overview(repo: JobsRepo) -> dict:
    jobs = repo.list_jobs(detail="full")
    scaffolds: Counter[str] = Counter()
    datasets: Counter[str] = Counter()
    models: Counter[str] = Counter()
    analyzed = 0
    total_resolved = 0
    total_failed = 0
    for job in jobs:
        if job.get("scaffold"):
            scaffolds[job["scaffold"]] += 1
        if job.get("dataset"):
            datasets[job["dataset"]] += 1
        if job.get("model"):
            models[job["model"]] += 1
        analysis = job.get("analysis") or {}
        if job.get("has_analysis"):
            analyzed += 1
            total_resolved += analysis.get("resolved_total") or 0
            total_failed += analysis.get("failed_total") or 0
    return {
        "job_count": len(jobs),
        "analyzed_count": analyzed,
        "scaffolds": scaffolds.most_common(),
        "datasets": datasets.most_common(),
        "models": models.most_common(),
        "total_resolved": total_resolved,
        "total_failed": total_failed,
        "overall_resolve_rate": percent(total_resolved, total_resolved + total_failed),
    }


def copy_static_assets(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in STATIC_ASSETS:
        shutil.copy2(STATIC_DIR / name, output_dir / name)
    (output_dir / "_worker.js").write_text(WORKER_JS, encoding="utf-8")


def stat_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def latest_mtime(paths: list[Path]) -> float | None:
    mtimes = [mtime for path in paths if (mtime := stat_mtime(path)) is not None]
    return max(mtimes) if mtimes else None


def compute_job_fingerprint(job_dir: Path) -> dict[str, Any]:
    trial_count = 0
    trial_latest_mtime = 0.0
    analysis_paths: list[Path] = []
    trace_path = _extracted_trace_path(job_dir)
    report_path = _extracted_report_path(job_dir)

    analysis_paths.append(job_dir / "analysis")
    try:
        analysis_paths.extend((job_dir / "analysis").rglob("*"))
    except OSError:
        pass

    try:
        children = list(job_dir.iterdir())
    except OSError:
        children = []
    for child in children:
        if child.name == "analysis":
            continue
        if not child.is_dir():
            continue
        try:
            is_trial = (child / "result.json").is_file() or (child / "config.json").is_file() or (child / "agent").is_dir() or (child / "verifier").is_dir()
        except OSError:
            is_trial = False
        if not is_trial:
            continue
        trial_count += 1
        trial_paths = [
            child,
            child / "result.json",
            child / "config.json",
            child / "agent" / "trajectory.json",
            child / "agent" / "litellm-trajectory.jsonl",
            child / "agent" / "trajectory_agent.json",
            child / "verifier" / "report.json",
            child / "verifier" / "reward.txt",
            child / "verifier" / "test-stdout.txt",
        ]
        trial_latest_mtime = max(trial_latest_mtime, latest_mtime(trial_paths) or 0.0)

    if trace_path:
        if trial_count == 0:
            trial_count = _count_jsonl_records(trace_path)
        trial_latest_mtime = max(trial_latest_mtime, stat_mtime(trace_path) or 0.0)

    return {
        "job_dir_mtime": stat_mtime(job_dir),
        "config_mtime": stat_mtime(job_dir / "config.json"),
        "extracted_trace_mtime": stat_mtime(trace_path) if trace_path else None,
        "extracted_trace_size": trace_path.stat().st_size if trace_path else None,
        "extracted_report_mtime": stat_mtime(report_path) if report_path else None,
        "extracted_report_size": report_path.stat().st_size if report_path else None,
        "trial_count": trial_count,
        "trial_latest_mtime": trial_latest_mtime,
        "analysis_latest_mtime": latest_mtime(analysis_paths),
    }


def export_config(trajectory_target: str, trajectory_chunk_mb: float, trial_detail_target: str, trial_detail_chunk_mb: float) -> dict[str, Any]:
    return {
        "trajectory_target": trajectory_target,
        "trajectory_chunk_mb": trajectory_chunk_mb if trajectory_target == "chunks" else None,
        "trial_detail_target": trial_detail_target,
        "trial_detail_chunk_mb": trial_detail_chunk_mb if trial_detail_target == "chunks" else None,
    }


def load_export_state(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / STATE_FILE_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        LOG.warning("failed to load export state %s: %s", path, exc)
        return None
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        LOG.info("export state missing or version changed; full rebuild required")
        return None
    return data


def save_export_state(output_dir: Path, state: dict[str, Any]) -> None:
    path = output_dir / STATE_FILE_NAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json_text(state), encoding="utf-8")
    tmp_path.replace(path)


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def clean_generated_outputs(output_dir: Path) -> None:
    for name in ("api", "trajectory_chunks", "trial_detail_chunks"):
        remove_path(output_dir / name)


def cached_job_output_exists(output_dir: Path, api_dir: Path, job_name: str, previous_job: dict[str, Any]) -> bool:
    encoded = encoded_segment(job_name)
    if not (api_dir / "jobs" / encoded / "index.json").exists():
        return False
    if not (api_dir / "jobs" / encoded / "trials.json").exists():
        return False

    for key in ("trajectory_manifest_entries", "trial_detail_manifest_entries"):
        entries = previous_job.get(key)
        if not entries:
            continue
        paths = {entry.get("path") for entry in entries.values() if isinstance(entry, dict) and entry.get("path")}
        for path in paths:
            if not (output_dir / str(path).lstrip("/")).exists():
                return False
    return True


def state_matches_config(state: dict[str, Any] | None, config: dict[str, Any]) -> bool:
    return bool(state and state.get("config") == config)


class TrajectoryChunkWriter:
    def __init__(self, output_dir: Path, chunk_target_bytes: int) -> None:
        self.output_dir = output_dir
        self.base_dir = output_dir / "trajectory_chunks"
        self.chunk_target_bytes = chunk_target_bytes
        self.manifest: dict[str, Any] = {
            "version": 1,
            "chunk_target_bytes": chunk_target_bytes,
            "jobs": {},
        }
        self._states: dict[str, dict[str, Any]] = {}
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def skip_job(self, job_name: str, cached_entries: dict[str, Any] | None) -> None:
        if cached_entries:
            self.manifest["jobs"][job_name] = cached_entries

    def begin_job(self, job_name: str) -> None:
        remove_path(self.base_dir / encoded_segment(job_name))

    def add(self, job_name: str, trial_name: str, trajectory: Any) -> None:
        state = self._states.setdefault(job_name, {"index": 0, "bytes": 0, "trajectories": {}})
        trajectory_text = json_text(trajectory)
        trajectory_bytes = len(trajectory_text.encode("utf-8"))
        estimated_wire_bytes = len(gzip.compress(trajectory_text.encode("utf-8"), compresslevel=6))
        if state["trajectories"] and state["bytes"] + trajectory_bytes > self.chunk_target_bytes:
            self.flush_job(job_name)
            state = self._states[job_name]

        chunk_path = self._chunk_path(job_name, state["index"])
        state["trajectories"][trial_name] = trajectory
        state["bytes"] += trajectory_bytes
        self.manifest["jobs"].setdefault(job_name, {})[trial_name] = {
            "path": "/" + chunk_path.relative_to(self.output_dir).as_posix(),
            "chunk": state["index"],
            "size_bytes": trajectory_bytes,
            "estimated_gzip_bytes": estimated_wire_bytes,
        }

    def flush_job(self, job_name: str) -> None:
        state = self._states.setdefault(job_name, {"index": 0, "bytes": 0, "trajectories": {}})
        if not state["trajectories"]:
            return
        chunk_path = self._chunk_path(job_name, state["index"])
        payload = {
            "job": job_name,
            "chunk": state["index"],
            "trajectories": state["trajectories"],
        }
        write_json(chunk_path, payload)
        state["index"] += 1
        state["bytes"] = 0
        state["trajectories"] = {}

    def finish(self) -> dict[str, Any]:
        for job_name in list(self._states):
            self.flush_job(job_name)
        chunk_files = list(self.base_dir.glob("**/chunk-*.json")) if self.base_dir.exists() else []
        self.manifest["chunk_count"] = len(chunk_files)
        self.manifest["trajectory_count"] = sum(len(trials) for trials in self.manifest["jobs"].values())
        write_json(self.base_dir / "manifest.json", self.manifest)
        return self.manifest

    def _chunk_path(self, job_name: str, index: int) -> Path:
        return self.base_dir / encoded_segment(job_name) / f"chunk-{index:04d}.json"


class TrialDetailChunkWriter:
    def __init__(self, output_dir: Path, chunk_target_bytes: int) -> None:
        self.output_dir = output_dir
        self.base_dir = output_dir / "trial_detail_chunks"
        self.chunk_target_bytes = chunk_target_bytes
        self.manifest: dict[str, Any] = {
            "version": 1,
            "chunk_target_bytes": chunk_target_bytes,
            "jobs": {},
        }
        self._states: dict[str, dict[str, Any]] = {}
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def skip_job(self, job_name: str, cached_entries: dict[str, Any] | None) -> None:
        if cached_entries:
            self.manifest["jobs"][job_name] = cached_entries

    def begin_job(self, job_name: str) -> None:
        remove_path(self.base_dir / encoded_segment(job_name))

    def add(self, job_name: str, trial_name: str, trial_detail: Any) -> None:
        state = self._states.setdefault(job_name, {"index": 0, "bytes": 0, "trials": {}})
        detail_text = json_text(trial_detail)
        detail_bytes = len(detail_text.encode("utf-8"))
        estimated_wire_bytes = len(gzip.compress(detail_text.encode("utf-8"), compresslevel=6))
        if state["trials"] and state["bytes"] + detail_bytes > self.chunk_target_bytes:
            self.flush_job(job_name)
            state = self._states[job_name]

        chunk_path = self._chunk_path(job_name, state["index"])
        state["trials"][trial_name] = trial_detail
        state["bytes"] += detail_bytes
        self.manifest["jobs"].setdefault(job_name, {})[trial_name] = {
            "path": "/" + chunk_path.relative_to(self.output_dir).as_posix(),
            "chunk": state["index"],
            "size_bytes": detail_bytes,
            "estimated_gzip_bytes": estimated_wire_bytes,
        }

    def flush_job(self, job_name: str) -> None:
        state = self._states.setdefault(job_name, {"index": 0, "bytes": 0, "trials": {}})
        if not state["trials"]:
            return
        chunk_path = self._chunk_path(job_name, state["index"])
        payload = {
            "job": job_name,
            "chunk": state["index"],
            "trials": state["trials"],
        }
        write_json(chunk_path, payload)
        state["index"] += 1
        state["bytes"] = 0
        state["trials"] = {}

    def finish(self) -> dict[str, Any]:
        for job_name in list(self._states):
            self.flush_job(job_name)
        chunk_files = list(self.base_dir.glob("**/chunk-*.json")) if self.base_dir.exists() else []
        self.manifest["chunk_count"] = len(chunk_files)
        self.manifest["trial_count"] = sum(len(trials) for trials in self.manifest["jobs"].values())
        write_json(self.base_dir / "manifest.json", self.manifest)
        return self.manifest

    def _chunk_path(self, job_name: str, index: int) -> Path:
        return self.base_dir / encoded_segment(job_name) / f"chunk-{index:04d}.json"


def export_job(repo: JobsRepo, api_dir: Path, job_name: str, trajectory_target: str, chunk_writer: TrajectoryChunkWriter | None = None, trial_chunk_writer: TrialDetailChunkWriter | None = None) -> dict:
    job_dir = api_dir / "jobs" / encoded_segment(job_name)
    detail = repo.job_detail(job_name)
    trials = detail.get("trials") or repo.list_trials(job_name)

    write_json(job_dir / "index.json", detail)
    write_json(job_dir / "trials.json", {"trials": trials})

    exported_trials = 0
    exported_trajectories = 0
    available_trajectories = 0
    for trial in trials:
        trial_name = trial.get("trial_name")
        if not trial_name:
            continue
        trial_path = job_dir / "trials" / encoded_segment(trial_name)
        # Append ".json" instead of with_suffix(): an encoded trial name may
        # contain a literal "." (quote() does not escape it), and with_suffix
        # would strip the trailing ".ext" and collide distinct trials.
        trial_detail_path = trial_path.with_name(trial_path.name + ".json")
        try:
            trial_detail = repo.trial_detail(job_name, trial_name)
            if trial_chunk_writer is None:
                write_json(trial_detail_path, trial_detail)
            else:
                trial_chunk_writer.add(job_name, trial_name, trial_detail)
            exported_trials += 1
        except Exception as exc:  # noqa: BLE001
            LOG.warning("failed to export trial %s/%s: %s", job_name, trial_name, exc)
            if trial_chunk_writer is None:
                write_json(trial_detail_path, {"error": str(exc)})
            else:
                trial_chunk_writer.add(job_name, trial_name, {"error": str(exc)})

        if trial.get("has_trajectory"):
            available_trajectories += 1
            if trajectory_target in {"pages", "chunks"}:
                try:
                    trajectory = repo.trajectory(job_name, trial_name, "agent")
                    if trajectory_target == "pages":
                        write_json(trial_path / "trajectory_agent.json", trajectory)
                    elif chunk_writer is not None:
                        chunk_writer.add(job_name, trial_name, trajectory)
                    exported_trajectories += 1
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("failed to export trajectory %s/%s: %s", job_name, trial_name, exc)
                    if trajectory_target == "pages":
                        write_json(trial_path / "trajectory_agent.json", {"error": str(exc)})

    for kind in ("resolved", "unresolved"):
        try:
            write_json(job_dir / f"rule_score_instances_{kind}_50.json", repo.rule_score_instances(job_name, kind, 50))
        except Exception as exc:  # noqa: BLE001
            write_json(job_dir / f"rule_score_instances_{kind}_50.json", {"kind": kind, "count": 0, "instances": [], "error": str(exc)})

    return {
        "name": job_name,
        "trial_count": len(trials),
        "exported_trials": exported_trials,
        "available_trajectories": available_trajectories,
        "exported_trajectories": exported_trajectories,
    }


def export_site(jobs_dir: Path, output_dir: Path, trajectory_target: str, trajectory_chunk_mb: float = 8.0, trial_detail_target: str = "chunks", trial_detail_chunk_mb: float = 8.0, full_rebuild: bool = False) -> dict:
    repo = JobsRepo(jobs_dir)
    api_dir = output_dir / "api"
    config = export_config(trajectory_target, trajectory_chunk_mb, trial_detail_target, trial_detail_chunk_mb)
    previous_state = None if full_rebuild else load_export_state(output_dir)
    if full_rebuild:
        LOG.info("full rebuild requested")
    elif previous_state is None:
        LOG.info("no compatible export state found; running full rebuild")
    elif not state_matches_config(previous_state, config):
        LOG.info("export config changed; running full rebuild")
        previous_state = None

    incremental = previous_state is not None
    if not incremental:
        clean_generated_outputs(output_dir)

    copy_static_assets(output_dir)
    api_dir.mkdir(parents=True, exist_ok=True)
    chunk_writer = None
    if trajectory_target == "chunks":
        chunk_writer = TrajectoryChunkWriter(output_dir, max(1, int(trajectory_chunk_mb * 1024 * 1024)))
    trial_chunk_writer = None
    if trial_detail_target == "chunks":
        trial_chunk_writer = TrialDetailChunkWriter(output_dir, max(1, int(trial_detail_chunk_mb * 1024 * 1024)))

    job_dirs = repo.list_job_dirs()
    current_job_names = {path.name for path in job_dirs}
    previous_jobs = (previous_state or {}).get("jobs", {})
    deleted_jobs = set(previous_jobs) - current_job_names
    for name in deleted_jobs:
        remove_path(api_dir / "jobs" / encoded_segment(name))
        remove_path(output_dir / "trajectory_chunks" / encoded_segment(name))
        remove_path(output_dir / "trial_detail_chunks" / encoded_segment(name))

    fingerprints = {path.name: compute_job_fingerprint(path) for path in job_dirs}
    dirty_jobs: set[str] = set()
    unchanged_jobs: set[str] = set()
    for name, fingerprint in fingerprints.items():
        previous_job = previous_jobs.get(name) if incremental else None
        if previous_job and previous_job.get("fingerprint") == fingerprint and cached_job_output_exists(output_dir, api_dir, name, previous_job):
            unchanged_jobs.add(name)
        else:
            dirty_jobs.add(name)

    LOG.info(
        "%s export: %s unchanged, %s dirty/new, %s deleted jobs",
        "incremental" if incremental else "full",
        len(unchanged_jobs),
        len(dirty_jobs),
        len(deleted_jobs),
    )

    jobs_full = repo.list_jobs(detail="full")
    jobs_lite = repo.list_jobs(detail="lite")
    write_json(api_dir / "jobs.json", {"jobs": jobs_full, "jobs_dir": str(repo.jobs_dir)})
    write_json(api_dir / "jobs_lite.json", {"jobs": jobs_lite, "jobs_dir": str(repo.jobs_dir)})
    write_json(api_dir / "overview.json", build_overview(repo))

    job_exports = []
    new_state_jobs: dict[str, Any] = {}
    for job in jobs_full:
        name = job.get("name")
        if not name:
            continue
        previous_job = previous_jobs.get(name, {})
        if name in unchanged_jobs:
            if chunk_writer is not None:
                chunk_writer.skip_job(name, previous_job.get("trajectory_manifest_entries"))
            if trial_chunk_writer is not None:
                trial_chunk_writer.skip_job(name, previous_job.get("trial_detail_manifest_entries"))
            cached_export = previous_job.get("export") or {"name": name, "skipped": True}
            job_exports.append(cached_export)
            new_state_jobs[name] = previous_job
            continue

        remove_path(api_dir / "jobs" / encoded_segment(name))
        if chunk_writer is not None:
            chunk_writer.begin_job(name)
        if trial_chunk_writer is not None:
            trial_chunk_writer.begin_job(name)
        try:
            export = export_job(repo, api_dir, name, trajectory_target, chunk_writer, trial_chunk_writer)
            job_exports.append(export)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("failed to export job %s: %s", name, exc)
            export = {"name": name, "error": str(exc)}
            job_exports.append(export)

        new_state_jobs[name] = {
            "fingerprint": fingerprints[name],
            "export": export,
            "trajectory_manifest_entries": (chunk_writer.manifest.get("jobs", {}).get(name) if chunk_writer is not None else None),
            "trial_detail_manifest_entries": (trial_chunk_writer.manifest.get("jobs", {}).get(name) if trial_chunk_writer is not None else None),
        }

    chunk_manifest = chunk_writer.finish() if chunk_writer is not None else None
    trial_chunk_manifest = trial_chunk_writer.finish() if trial_chunk_writer is not None else None
    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jobs_dir": str(jobs_dir),
        "job_count": len(jobs_full),
        "trajectory_target": trajectory_target,
        "include_trajectories": trajectory_target in {"pages", "chunks"},
        "trajectory_chunk_count": (chunk_manifest or {}).get("chunk_count", 0),
        "trajectory_chunk_mb": trajectory_chunk_mb if trajectory_target == "chunks" else None,
        "trial_detail_target": trial_detail_target,
        "trial_detail_chunk_count": (trial_chunk_manifest or {}).get("chunk_count", 0),
        "trial_detail_chunk_mb": trial_detail_chunk_mb if trial_detail_target == "chunks" else None,
        "available_trajectories": sum(j.get("available_trajectories", 0) for j in job_exports),
        "exported_trajectories": sum(j.get("exported_trajectories", 0) for j in job_exports),
        "incremental": incremental,
        "unchanged_job_count": len(unchanged_jobs),
        "dirty_job_count": len(dirty_jobs),
        "deleted_job_count": len(deleted_jobs),
        "jobs": job_exports,
    }
    write_json(api_dir / "manifest.json", manifest)
    save_export_state(output_dir, {
        "version": STATE_VERSION,
        "exported_at": manifest["exported_at"],
        "config": config,
        "jobs": new_state_jobs,
    })
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Harbor Job Dashboard as static files")
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=Path(os.environ.get("HARBOR_JOBS_DIR", str(DEFAULT_JOBS))),
        help="Harbor jobs directory",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=HERE / "site",
        help="Static site output directory",
    )
    parser.add_argument(
        "--trajectory-target",
        choices=("none", "pages", "chunks"),
        default="none",
        help="Where to export trajectory JSON. Use 'chunks' for a no-R2 Pages deployment, 'none' for R2-backed Pages, or 'pages' only for local/full debugging.",
    )
    parser.add_argument(
        "--trajectory-chunk-mb",
        type=float,
        default=8.0,
        help="Approximate max size for each static trajectory chunk when --trajectory-target chunks is used.",
    )
    parser.add_argument(
        "--trial-detail-target",
        choices=("files", "chunks"),
        default="chunks",
        help="Where to export per-trial detail JSON. Use 'chunks' to reduce Cloudflare Pages file count.",
    )
    parser.add_argument(
        "--trial-detail-chunk-mb",
        type=float,
        default=8.0,
        help="Approximate max size for each static trial-detail chunk when --trial-detail-target chunks is used.",
    )
    parser.add_argument(
        "--skip-trajectories",
        action="store_true",
        help="Deprecated alias for --trajectory-target none",
    )
    parser.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Ignore cached incremental export state and rebuild generated outputs",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")
    trajectory_target = "none" if args.skip_trajectories else args.trajectory_target
    manifest = export_site(
        args.jobs_dir.resolve(),
        args.output_dir.resolve(),
        trajectory_target,
        args.trajectory_chunk_mb,
        args.trial_detail_target,
        args.trial_detail_chunk_mb,
        args.full_rebuild,
    )
    LOG.info(
        "exported %s jobs to %s (%s available trajectories, %s written to Pages)",
        manifest["job_count"],
        args.output_dir.resolve(),
        manifest["available_trajectories"],
        manifest["exported_trajectories"],
    )


if __name__ == "__main__":
    main()
