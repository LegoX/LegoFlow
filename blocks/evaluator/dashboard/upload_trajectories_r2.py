#!/usr/bin/env python3
"""Upload Harbor trajectory JSON payloads to Cloudflare R2."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

from server import DEFAULT_JOBS, JobsRepo, _json_default

LOG = logging.getLogger("harbor.webui.upload_trajectories_r2")


def r2_key(job_name: str, trial_name: str, kind: str = "agent") -> str:
    return f"{job_name}/{trial_name}/trajectory_{kind}.json"


def write_payload(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, default=_json_default, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def upload_with_wrangler(bucket: str, key: str, path: Path) -> None:
    subprocess.run(
        [
            "npx",
            "--yes",
            "wrangler",
            "r2",
            "object",
            "put",
            f"{bucket}/{key}",
            "--file",
            str(path),
            "--content-type",
            "application/json; charset=utf-8",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def upload_one(repo: JobsRepo, bucket: str, job_name: str, trial_name: str, tmp_dir: Path) -> tuple[bool, str, int | None, str | None]:
    key = r2_key(job_name, trial_name)
    safe_name = quote(job_name, safe="") + "__" + quote(trial_name, safe="") + ".json"
    tmp_path = tmp_dir / safe_name
    try:
        payload = repo.trajectory(job_name, trial_name, "agent")
        write_payload(tmp_path, payload)
        size = tmp_path.stat().st_size
        upload_with_wrangler(bucket, key, tmp_path)
        return True, key, size, None
    except Exception as exc:  # noqa: BLE001
        return False, key, None, str(exc)
    finally:
        tmp_path.unlink(missing_ok=True)


def collect_trajectories(repo: JobsRepo) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for job in repo.list_jobs(detail="lite"):
        job_name = job.get("name")
        if not job_name:
            continue
        for trial in repo.list_trials(job_name):
            trial_name = trial.get("trial_name")
            if trial_name and trial.get("has_trajectory"):
                out.append((job_name, trial_name))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload Harbor trajectories to Cloudflare R2")
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=Path(os.environ.get("HARBOR_JOBS_DIR", str(DEFAULT_JOBS))),
        help="Harbor jobs directory",
    )
    parser.add_argument("--bucket", default=os.environ.get("R2_BUCKET_NAME", "harbor-trajectories"))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("R2_UPLOAD_WORKERS", "8")))
    parser.add_argument("--limit", type=int, default=0, help="Upload only the first N trajectories for testing")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")

    repo = JobsRepo(args.jobs_dir.resolve())
    tasks = collect_trajectories(repo)
    if args.limit > 0:
        tasks = tasks[: args.limit]
    total = len(tasks)
    LOG.info("found %s trajectories to upload to R2 bucket %s", total, args.bucket)

    uploaded = 0
    failed = 0
    bytes_uploaded = 0
    with tempfile.TemporaryDirectory(prefix="harbor-r2-trajectories-") as tmp:
        tmp_dir = Path(tmp)
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [pool.submit(upload_one, repo, args.bucket, job, trial, tmp_dir) for job, trial in tasks]
            for future in as_completed(futures):
                ok, key, size, error = future.result()
                if ok:
                    uploaded += 1
                    bytes_uploaded += size or 0
                else:
                    failed += 1
                    LOG.warning("failed to upload %s: %s", key, error)
                done = uploaded + failed
                if done % 100 == 0 or done == total:
                    LOG.info("progress %s/%s uploaded=%s failed=%s bytes=%s", done, total, uploaded, failed, bytes_uploaded)

    if failed:
        raise SystemExit(f"failed to upload {failed} trajectories")
    LOG.info("done: uploaded %s trajectories (%s bytes)", uploaded, bytes_uploaded)


if __name__ == "__main__":
    main()
