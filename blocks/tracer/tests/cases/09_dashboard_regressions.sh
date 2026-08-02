#!/usr/bin/env bash
# CI test 09: dashboard privacy, run-status, R2 shards, and smoke config.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$BLOCK_DIR" <<'PY'
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed in runner's python3", file=sys.stderr)
    raise SystemExit(1)

block = Path(sys.argv[1])
dashboard_path = block / "dashboard" / "progress_monitor.py"
spec = importlib.util.spec_from_file_location("progress_monitor", dashboard_path)
assert spec and spec.loader
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)

for cli in (
    ["--local-mode", "public"],
    ["--public-no-samples"],
    ["--no-include-samples"],
):
    args = dashboard.parse_args(cli)
    include_samples = bool(args.include_samples)
    if args.local_mode == "public" or args.public_no_samples:
        include_samples = False
    limits = dashboard.effective_embedded_trajectory_limits(
        args,
        include_samples=include_samples,
    )
    if limits != (0, 0):
        raise AssertionError(f"{cli} still permits embedded trajectories: {limits}")

full_args = dashboard.parse_args([])
if dashboard.effective_embedded_trajectory_limits(full_args, include_samples=True) != (
    dashboard.DEFAULT_EMBEDDED_TRAJ_LIMIT,
    dashboard.DEFAULT_EMBEDDED_TRAJ_MAX_BYTES,
):
    raise AssertionError("full local mode unexpectedly disables embedded trajectories")
if "HARBOR_JOBS_DIR" not in os.environ and full_args.harbor_jobs_dir != dashboard.DEFAULT_JOBS:
    raise AssertionError(
        f"default Harbor jobs directory differs from the configured jobs directory: {full_args.harbor_jobs_dir}"
    )

smoke_cfg = yaml.safe_load((block / "tests" / "smoke" / "config.yaml").read_text()) or {}
smoke_sft = smoke_cfg["runtime_info"]["input"]["sft_conversion"]
prod_cfg = yaml.safe_load((block / "config.yaml").read_text()) or {}
prod_sft = prod_cfg["runtime_info"]["input"]["sft_conversion"]
if smoke_sft.get("tokenizer_name") != prod_sft.get("tokenizer_name"):
    raise AssertionError("block smoke tokenizer_name does not match production")
if (
    smoke_cfg["meta_info"]["repositories"]["swe_data_process"]["commit"]
    != prod_cfg["meta_info"]["repositories"]["swe_data_process"]["commit"]
):
    raise AssertionError("block smoke swe_data_process pin does not match production")

with tempfile.TemporaryDirectory() as raw_tmp:
    tmp = Path(raw_tmp)
    index = tmp / "index.yaml"
    index.write_text(
        "runs:\n"
        "- id: old-run\n"
        "  completed_at: '2026-07-27T01:00:00+08:00'\n"
        "  status: failed\n"
        "- id: latest-run\n"
        "  completed_at: '2026-07-28T01:00:00+08:00'\n"
        "  status: completed\n"
        "  archive: artifacts/archives/latest-run/\n"
        "  notes: 'latest notes'\n",
        encoding="utf-8",
    )
    status = dashboard.read_status(index)
    if status.get("id") != "latest-run" or status.get("status") != "completed":
        raise AssertionError(f"latest archived run was not selected: {status}")

    sft_dir = tmp / "sft"
    dataset_dir = sft_dir / "quality-dataset"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "im.jsonl").write_text(
        json.dumps(
            {
                "meta_info": {
                    "category": "repair",
                    "query_source": "unit-test",
                    "unique_info": {
                        "_instance_id": "owner__repo-1",
                        "_score": {
                            "composite_score_v4": 0,
                            "composite_score_v3": 0.9,
                        },
                    },
                },
                "messages": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    quality_facts = dashboard.collect_quality_facts(
        sft_dir,
        {},
        max_records_per_dataset=0,
        preview_chars=100,
        include_previews=False,
    )
    if len(quality_facts) != 1 or quality_facts[0].get("score") != 0:
        raise AssertionError(f"zero-valued v4 quality score was not preserved: {quality_facts}")
    sample = dashboard.summarize_sample(
        json.loads((dataset_dir / "im.jsonl").read_text(encoding="utf-8")),
        dataset_dir.name,
        "im.jsonl",
        0,
        100,
        12,
    )
    if sample.get("score") != 0:
        raise AssertionError(f"zero-valued v4 sample score was not preserved: {sample}")
    _, quality_cards = dashboard.build_traj_cards([], quality_facts)
    if not quality_cards or quality_cards[0].get("full_available"):
        raise AssertionError(f"quality card incorrectly advertises an unavailable full payload: {quality_cards}")
    analysis = dashboard.build_analysis({}, [], quality_facts, [])
    if not {"category", "source"}.issubset(analysis.get("dims", [])):
        raise AssertionError(f"category/source segment dimensions are missing: {analysis.get('dims')}")
    segment_keys = {(row.get("dim"), row.get("value")) for row in analysis.get("segments", [])}
    if ("category", "repair") not in segment_keys or ("source", "unit-test") not in segment_keys:
        raise AssertionError(f"category/source segment buckets are missing: {segment_keys}")

    data_dir = tmp / "site" / "data"
    data_dir.mkdir(parents=True)
    stale = data_dir / "traj_embedded.000.jsonl"
    stale.write_text('{"secret": true}\n', encoding="utf-8")
    exports = dashboard.write_embedded_trajectory_shards(
        data_dir,
        [],
        limit=0,
        max_total_bytes=0,
    )
    if exports or stale.exists():
        raise AssertionError("disabled trajectory embedding did not remove stale exports")

    jobs_dir = tmp / "harbor-jobs"
    trial_dir = jobs_dir / "test-job" / "owner__repo-1"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "task_name": "owner__repo-1",
                "trial_name": "owner__repo-1",
                "agent_info": {"name": "custom-claude-code"},
                "verifier_result": {"rewards": {"reward": 1}},
            }
        ),
        encoding="utf-8",
    )
    raw_trajectory = agent_dir / "litellm-trajectory.jsonl"
    raw_trajectory.write_text(
        json.dumps({"request": 1}) + "\n" + json.dumps({"request": 2}) + "\n",
        encoding="utf-8",
    )
    trial_facts = dashboard.collect_trial_facts(
        jobs_dir,
        ["test-job"],
        {},
        max_trials_per_job=0,
    )
    if len(trial_facts) != 1 or trial_facts[0].get("trajectory_path") != str(raw_trajectory):
        raise AssertionError(f"raw LiteLLM trajectory artifact was not discovered: {trial_facts}")
    _, trial_cards = dashboard.build_traj_cards(trial_facts, [])
    embedded = dashboard.write_embedded_trajectory_shards(
        data_dir,
        trial_cards,
        limit=1,
        max_total_bytes=10_000,
    )
    if len(embedded) != 1:
        raise AssertionError(f"raw LiteLLM trajectory artifact was not embedded: {embedded}")
    embedded_row = json.loads((tmp / "site" / embedded[0]).read_text(encoding="utf-8"))
    if embedded_row.get("record") != [{"request": 1}, {"request": 2}]:
        raise AssertionError(f"embedded JSONL trajectory was not parsed as records: {embedded_row}")

    empty_jobs = tmp / "jobs"
    empty_tasks = tmp / "tasks"
    empty_jobs.mkdir()
    empty_tasks.mkdir()
    no_samples_args = dashboard.parse_args(
        [
            "--no-include-samples",
            "--jobs-dir",
            str(empty_jobs),
            "--sft-dir",
            str(sft_dir),
            "--tasks-dir",
            str(empty_tasks),
            "--harbor-jobs-dir",
            str(empty_jobs),
            "--index-file",
            str(index),
            "--output-html",
            str(tmp / "public-site" / "index.html"),
            "--cache-file",
            str(tmp / "dashboard-cache.json"),
        ]
    )
    dashboard.run_once(no_samples_args, 60)
    exported_quality = [
        json.loads(line)
        for line in (tmp / "public-site" / "data" / "quality_fact.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if exported_quality[0].get("preview"):
        raise AssertionError("--no-include-samples still exported a quality preview")

    trajectories = []
    for idx in range(3):
        path = tmp / f"trajectory-{idx}.json"
        path.write_text(json.dumps({"idx": idx}), encoding="utf-8")
        trajectories.append(path)
    shards = [data_dir / "trial_fact.000.jsonl", data_dir / "trial_fact.001.jsonl"]
    shards[0].write_text(
        "".join(
            json.dumps({"id": f"row-{idx}", "trajectory_path": str(trajectories[idx])}) + "\n"
            for idx in range(2)
        ),
        encoding="utf-8",
    )
    shards[1].write_text(
        json.dumps({"id": "row-2", "trajectory_path": str(trajectories[2])}) + "\n",
        encoding="utf-8",
    )
    manifest_script = block / "dashboard" / "export_r2_manifest.py"
    output = subprocess.check_output(
        [sys.executable, str(manifest_script), *(str(path) for path in shards)],
        text=True,
    ).splitlines()
    if len(output) != 3:
        raise AssertionError(f"expected all three rows from two shards, got {output}")
    limited = subprocess.check_output(
        [sys.executable, str(manifest_script), *(str(path) for path in shards), "--limit", "2"],
        text=True,
    ).splitlines()
    if len(limited) != 2:
        raise AssertionError(f"global R2 upload limit was not respected: {limited}")
    advanced = subprocess.check_output(
        [
            sys.executable,
            str(manifest_script),
            *(str(path) for path in shards),
            "--offset",
            "2",
            "--limit",
            "1",
        ],
        text=True,
    ).splitlines()
    if len(advanced) != 1 or "row-2.json" not in advanced[0]:
        raise AssertionError(f"R2 upload cursor did not advance across shards: {advanced}")

    mirrored = {"task_name": "owner__repo-1__mirror-attempt-2"}
    if dashboard.fact_instance_key(mirrored) != "owner__repo-1":
        raise AssertionError("mirrored trial task name was not normalized before joining")

print("PASS: dashboard privacy, latest-run status, R2 shards, and smoke config")
PY
