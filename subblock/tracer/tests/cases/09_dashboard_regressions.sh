#!/usr/bin/env bash
# CI test 09: dashboard privacy, run-status, R2 shards, and smoke config.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$BLOCK_DIR" <<'PY'
import importlib.util
import json
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

smoke_cfg = yaml.safe_load((block / "tests" / "smoke" / "config.yaml").read_text()) or {}
smoke_sft = smoke_cfg["runtime_info"]["input"]["sft_conversion"]
prod_cfg = yaml.safe_load((block / "config.yaml").read_text()) or {}
prod_sft = prod_cfg["runtime_info"]["input"]["sft_conversion"]
if smoke_sft.get("tokenizer_name") != prod_sft.get("tokenizer_name"):
    raise AssertionError("subblock smoke tokenizer_name does not match production")
if (
    smoke_cfg["meta_info"]["repositories"]["swe_data_process"]["commit"]
    != prod_cfg["meta_info"]["repositories"]["swe_data_process"]["commit"]
):
    raise AssertionError("subblock smoke swe_data_process pin does not match production")

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

print("PASS: dashboard privacy, latest-run status, R2 shards, and smoke config")
PY
