#!/usr/bin/env bash
# CI test 09: dashboard privacy, run-status, R2 shards, and smoke config.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$BLOCK_DIR" <<'PY'
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
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
    smoke_cfg["meta_info"]["repositories"]["harbor"]["commit"]
    != prod_cfg["meta_info"]["repositories"]["harbor"]["commit"]
):
    raise AssertionError("block smoke Harbor pin does not match production")
if (
    smoke_cfg["meta_info"]["repositories"]["legoflow_trace_crafter"]["commit"]
    != prod_cfg["meta_info"]["repositories"]["legoflow_trace_crafter"]["commit"]
):
    raise AssertionError("block smoke legoflow_trace_crafter pin does not match production")

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
    (dataset_dir / "lf.stats.json").write_text('{"count": 1}\n', encoding="utf-8")
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

    # Pass rate follows Harbor's mean verifier reward. A successful verifier
    # result remains a pass-rate success even when the trial also records an
    # agent exception; errors are reported independently.
    reward_facts = [
        {"task_name": "owner__repo-1", "job": "reward-job", "status": "error", "reward": 1.0},
        {"task_name": "owner__repo-1", "job": "reward-job", "status": "error", "reward": 0.0},
        {"task_name": "owner__repo-1", "job": "reward-job", "status": "pass", "reward": 1.0},
    ]
    reward_analysis = dashboard.build_analysis({}, reward_facts, [], [])
    if reward_analysis["summary"].get("pass_rate") != 0.6667:
        raise AssertionError(f"global pass rate did not follow mean reward: {reward_analysis['summary']}")
    reward_segment = next(
        row for row in reward_analysis["segments"]
        if row.get("dim") == "job" and row.get("value") == "reward-job"
    )
    if (
        reward_segment.get("pass_rate") != 0.6667
        or reward_segment.get("passed") != 2
        or reward_segment.get("errors") != 2
    ):
        raise AssertionError(f"segment pass/error metrics used the wrong semantics: {reward_segment}")
    authoritative_analysis = dashboard.build_analysis(
        {},
        reward_facts,
        [],
        [{
            "job": "reward-job",
            "primary_mean": 0.625,
            "primary_reward_1_count": 5,
            "n_trials": 8,
            "n_errors": 4,
            "finished_at": None,
        }],
    )
    authoritative_segment = next(
        row for row in authoritative_analysis["segments"]
        if row.get("dim") == "job" and row.get("value") == "reward-job"
    )
    if (
        authoritative_segment.get("pass_rate") != 0.625
        or authoritative_segment.get("avg_reward") != 0.625
        or authoritative_segment.get("trials") != 8
        or authoritative_segment.get("passed") != 5
        or authoritative_segment.get("errors") != 4
    ):
        raise AssertionError(f"job segment did not prefer Harbor's authoritative mean: {authoritative_segment}")
    if (
        authoritative_analysis["summary"].get("pass_rate") != 0.625
        or authoritative_analysis["summary"].get("avg_reward") != 0.625
    ):
        raise AssertionError(
            f"global reward metrics did not use the authoritative job aggregate: "
            f"{authoritative_analysis['summary']}"
        )
    weighted_analysis = dashboard.build_analysis(
        {},
        reward_facts,
        [],
        [
            {
                "job": "reward-job",
                "primary_mean": 0.625,
                "primary_reward_1_count": 5,
                "n_trials": 8,
                "n_errors": 4,
                "finished_at": None,
            },
            {
                "job": "aggregate-only-job",
                "primary_mean": 0.25,
                "primary_reward_1_count": 1,
                "n_trials": 4,
                "n_errors": 1,
                "finished_at": None,
            },
        ],
    )
    if weighted_analysis["summary"].get("pass_rate") != 0.5:
        raise AssertionError(f"global job means were not trial-weighted: {weighted_analysis['summary']}")
    aggregate_only_segment = next(
        row for row in weighted_analysis["segments"]
        if row.get("dim") == "job" and row.get("value") == "aggregate-only-job"
    )
    if (
        aggregate_only_segment.get("trials") != 4
        or aggregate_only_segment.get("passed") != 1
        or aggregate_only_segment.get("errors") != 1
        or aggregate_only_segment.get("pass_rate") != 0.25
    ):
        raise AssertionError(f"aggregate-only job metrics were missing: {aggregate_only_segment}")
    reward_instances = dashboard.build_instance_index({}, reward_facts, [])
    if (
        len(reward_instances) != 1
        or reward_instances[0].get("pass_rate") != 0.6667
        or reward_instances[0].get("pass_count") != 2
        or reward_instances[0].get("fail_count") != 1
        or reward_instances[0].get("error_count") != 2
    ):
        raise AssertionError(f"instance pass rate did not follow mean reward: {reward_instances}")

    source_summary = dashboard.build_traj_source_summary(
        [{**quality_facts[0], "job": "quality-dataset", "dataset": "quality-dataset"}],
        [{"job": "quality-dataset", "tool_call_errors": {}, "token_lens": {"mean": 12345}}],
        [{"job": "quality-dataset", "primary_mean": 0.375, "primary_reward_1_count": 1}],
    )
    if (
        len(source_summary) != 1
        or source_summary[0].get("pass_rate") != 0.375
        or source_summary[0].get("avg_tokens") != 12345
    ):
        raise AssertionError(f"trajectory source pass rate did not follow Harbor mean: {source_summary}")

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

    quality_exports = dashboard.write_embedded_trajectory_shards(
        data_dir,
        quality_cards,
        limit=1,
        max_total_bytes=10_000,
    )
    if len(quality_exports) != 1 or not quality_cards[0].get("embedded_available"):
        raise AssertionError(f"valid SFT trajectory was not made available to Samples: {quality_cards}")
    quality_embedded_row = json.loads((tmp / "site" / quality_exports[0]).read_text(encoding="utf-8"))
    if quality_embedded_row.get("id") != quality_cards[0].get("id"):
        raise AssertionError(f"valid SFT full trace could not be resolved by card id: {quality_embedded_row}")
    quality_server = dashboard.start_http_server(tmp / "site", "127.0.0.1", 0)
    try:
        quality_port = quality_server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{quality_port}/{quality_exports[0]}") as response:
            served_quality_row = json.loads(response.read().decode("utf-8"))
    finally:
        quality_server.shutdown()
        quality_server.server_close()
    if served_quality_row.get("id") != quality_cards[0].get("id"):
        raise AssertionError(f"served valid SFT full trace did not resolve by card id: {served_quality_row}")

    jobs_dir = tmp / "harbor-jobs"
    trial_dir = jobs_dir / "test-job" / "owner__repo-1"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "task_name": "owner__repo-1",
                "trial_name": "owner__repo-1",
                "agent_info": {
                    "name": "custom-claude-code",
                    "model_info": {"name": "test-model", "provider": "test-provider"},
                },
                "started_at": "2026-08-19T10:00:00+08:00",
                "finished_at": "2026-08-19T10:00:03+08:00",
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
    if (
        trial_cards[0].get("provider") != "test-provider"
        or trial_cards[0].get("started_at") != "2026-08-19T10:00:00+08:00"
        or trial_cards[0].get("finished_at") != "2026-08-19T10:00:03+08:00"
    ):
        raise AssertionError(f"trial metadata was dropped before the sampler card: {trial_cards[0]}")
    balanced_fixture = []
    for source in ("source-a", "source-b"):
        balanced_fixture.extend(
            [
                {"id": f"{source}:quality", "job": source, "kind": "quality", "status": "scored"},
                {"id": f"{source}:pass", "job": source, "kind": "trial", "status": "pass"},
                {"id": f"{source}:fail", "job": source, "kind": "trial", "status": "fail"},
                {"id": f"{source}:error", "job": source, "kind": "trial", "status": "error"},
            ]
        )
    balanced_fixture.extend(
        {"id": f"extra-error-{idx}", "job": "source-a", "kind": "trial", "status": "error"}
        for idx in range(12)
    )
    balanced_cards = dashboard.select_sampler_cards(balanced_fixture, limit=8)
    balanced_pairs = {
        (
            card.get("job"),
            "quality" if card.get("kind") == "quality" else card.get("status"),
        )
        for card in balanced_cards
    }
    expected_pairs = {
        (source, kind)
        for source in ("source-a", "source-b")
        for kind in ("quality", "pass", "fail", "error")
    }
    if balanced_pairs != expected_pairs:
        raise AssertionError(
            f"sampler payload was not balanced across source and outcome: {balanced_cards}"
        )
    embedded = dashboard.write_embedded_trajectory_shards(
        data_dir,
        trial_cards,
        limit=1,
        max_total_bytes=10_000,
    )
    if len(embedded) != 1:
        raise AssertionError(f"raw LiteLLM trajectory artifact was not embedded: {embedded}")
    if not trial_cards[0].get("embedded_available"):
        raise AssertionError(f"embedded trajectory card was not marked available: {trial_cards[0]}")
    if trial_cards[0].get("embedded_path") != embedded[0]:
        raise AssertionError(f"embedded trajectory card points at the wrong shard: {trial_cards[0]}")
    embedded_row = json.loads((tmp / "site" / embedded[0]).read_text(encoding="utf-8"))
    if embedded_row.get("record") != [{"request": 1}, {"request": 2}]:
        raise AssertionError(f"embedded JSONL trajectory was not parsed as records: {embedded_row}")
    server = dashboard.start_http_server(tmp / "site", "127.0.0.1", 0)
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/{embedded[0]}") as response:
            served_row = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    if served_row.get("id") != trial_cards[0].get("id") or served_row.get("record") != embedded_row.get("record"):
        raise AssertionError(f"served full trace did not resolve by trajectory card id: {served_row}")

    oversized_path = tmp / "oversized-trajectory.json"
    oversized_path.write_text(json.dumps({"blob": "x" * 1000}), encoding="utf-8")
    small_paths = []
    for idx in range(2):
        path = tmp / f"small-trajectory-{idx}.json"
        path.write_text(json.dumps({"idx": idx}), encoding="utf-8")
        small_paths.append(path)
    candidate_cards = [
        {"id": "oversized", "job": "a", "status": "error", "trajectory_path": str(oversized_path)},
        {"id": "small-0", "job": "b", "status": "pass", "trajectory_path": str(small_paths[0])},
        {"id": "small-1", "job": "c", "status": "pass", "trajectory_path": str(small_paths[1])},
    ]
    continued_exports = dashboard.write_embedded_trajectory_shards(
        data_dir,
        candidate_cards,
        limit=2,
        max_total_bytes=10_000,
        max_record_bytes=200,
    )
    if len(continued_exports) != 1:
        raise AssertionError(f"smaller trajectories after an oversized candidate were not exported: {continued_exports}")
    embedded_ids = {card["id"] for card in candidate_cards if card.get("embedded_available")}
    if embedded_ids != {"small-0", "small-1"}:
        raise AssertionError(f"embed limit counted failed attempts instead of successful records: {candidate_cards}")

    im_batch_path = tmp / "batch-im.jsonl"
    im_batch_path.write_text(
        "".join(
            json.dumps({"index": idx, "content": "x" * (500 if idx < 9 else 1)}) + "\n"
            for idx in range(12)
        ),
        encoding="utf-8",
    )
    im_batch_cards = [
        {
            "id": f"im-{idx:02d}",
            "job": "batch",
            "kind": "quality",
            "status": "scored",
            "im_path": str(im_batch_path),
            "index": idx,
        }
        for idx in range(12)
    ]
    unopened_reader = dashboard.IndexedImRecordReader(im_batch_cards)
    try:
        if unopened_reader.files or unopened_reader.offsets:
            raise AssertionError("IM fallback indexed files before a candidate was requested")
        if unopened_reader.get(str(im_batch_path), 0).get("index") != 0:
            raise AssertionError("incremental IM fallback could not read its first candidate")
        if unopened_reader.scan_state[str(im_batch_path)][0] != 1:
            raise AssertionError(
                f"IM fallback scanned beyond the requested candidate: {unopened_reader.scan_state}"
            )
    finally:
        unopened_reader.close()
    observed_im_reads = []
    original_im_get = dashboard.IndexedImRecordReader.get

    def tracked_im_get(self, path, index):
        observed_im_reads.append(index)
        return original_im_get(self, path, index)

    dashboard.IndexedImRecordReader.get = tracked_im_get
    try:
        batched_exports = dashboard.write_embedded_trajectory_shards(
            data_dir,
            im_batch_cards,
            limit=2,
            max_total_bytes=10_000,
            max_record_bytes=200,
        )
    finally:
        dashboard.IndexedImRecordReader.get = original_im_get
    if not batched_exports:
        raise AssertionError("lazy IM fallback did not reach smaller later records")
    if observed_im_reads != list(range(11)):
        raise AssertionError(f"IM fallback parsed records beyond the successful limit: {observed_im_reads}")
    batched_ids = {card["id"] for card in im_batch_cards if card.get("embedded_available")}
    if batched_ids != {"im-09", "im-10"}:
        raise AssertionError(f"lazy IM fallback selected the wrong records: {im_batch_cards}")

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
    rendered_html = (tmp / "public-site" / "index.html").read_text(encoding="utf-8")
    for forbidden in (
        'data-page="operations"',
        'id="operations"',
        'id="sftSearch"',
        'id="sftScaffold"',
        "function applyFilters()",
        'id="openSampler"',
        "function openSampler()",
        'id="trajPanel"',
        'class="traj-box"',
        'data-sample-source=',
        '<th class="num">Samples</th>',
        "function openTrajectorySamples",
    ):
        if forbidden in rendered_html:
            raise AssertionError(f"removed dashboard UI remains in generated HTML: {forbidden}")
    for required in (
        'data-page="overview"',
        'data-page="instances"',
        'data-page="trajectories"',
        "rgba(var(--matrix-heat-rgb),",
        "Trajectory Sampler",
        'id="trajSearch"',
        'id="trajSource"',
        'id="trajLanguage"',
        'id="trajMode"',
        'id="trajSampleSize"',
        '<option value="20" selected>',
        '<option value="low">Lowest score / failures</option>',
        '<option value="high">Highest score</option>',
        '<option value="error">Most errors</option>',
        '<option value="clean">Clean / pass</option>',
        '<option value="random">Random sample</option>',
        'id="trajResample"',
        'class="traj-layout"',
        "function trajSortCards(cards, mode)",
        "raw === null || raw === undefined",
        "pool.filter(card => card.status === 'pass' || card.status === 'scored')",
        "function selectTrajectory(card, button)",
        "void loadFullTrajectory(card, loadToken)",
        "const fullTrajectoryCache = {}",
        "function fetchFullTrajectory(card)",
        "loadToken !== trajLoadToken",
        "Retry full trace",
        "R2_API_AVAILABLE",
        "function loadEmbeddedTrajectory(card)",
        "The embedded full trace could not be loaded",
        "TRACE_MODEL_START",
        "function analyzeLiteLLMEvents(record)",
        "function analyzeTrajectoryRecord(record)",
        ">Timeline</button>",
        ">Raw JSON</button>",
        ">Expand all</button>",
        ">Collapse all</button>",
        "function trajectoryMetaHtml(card)",
        "Detected errors",
        "Provenance and storage",
        "TRACE_MARKDOWN_MODEL_START",
        "function traceMarkdownBlocks(value)",
        "function renderTraceRichText(value)",
        "item.event?.failure || item.event?.error || item.event?.exception",
        "item.event?.success !== false",
        "trace-code-block",
        "trace-code-copy",
        "text-align: left",
        "code.textContent = block.text || ''",
        "parent.appendChild(document.createTextNode(plain))",
        '<option value="mixed" selected>Balanced mix</option>',
        "if (mode === 'mixed') return trajMixedCards(pool)",
        "if (source && trajSourceName(card) !== source) return false",
        "trajCardData.map(trajSourceName)",
        'id="trajectorySamplerPanel"',
        "renderTrajectorySourceTable();",
        "scrollIntoView({behavior: 'smooth', block: 'start'})",
        ".trace-code-pre code { display: block; padding: 0; border-radius: 0; background: transparent; color: inherit; font: inherit; }",
    ):
        if required not in rendered_html:
            raise AssertionError(f"expected dashboard content is missing: {required}")
    if "rgba(99,102,241," in rendered_html:
        raise AssertionError("Trajectory Quality Score Matrix still uses the old purple heat color")
    node = shutil.which("node")
    if node:
        executable_scripts = re.findall(r"<script>(.*?)</script>", rendered_html, flags=re.DOTALL)
        if not executable_scripts:
            raise AssertionError("generated dashboard has no executable inline script")
        generated_script = tmp / "generated-dashboard.js"
        generated_script.write_text(executable_scripts[-1], encoding="utf-8")
        subprocess.run([node, "--check", str(generated_script)], check=True)
        start = rendered_html.index("// TRACE_MODEL_START")
        end = rendered_html.index("// TRACE_MODEL_END", start)
        trace_model = rendered_html[start:end]
        fixture = [
            {
                "timestamp": "2026-08-19T10:00:00Z",
                "duration_ms": 100,
                "success": True,
                "session_id": "session-1",
                "request_body": {
                    "model": "fixture-model",
                    "custom_llm_provider": "fixture-provider",
                    "messages": [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "task"},
                    ],
                },
                "response_body": {
                    "choices": [{
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "inspect",
                            "content": "",
                            "tool_calls": [{
                                "id": "call-1",
                                "function": {"name": "shell", "arguments": '{"cmd":"pwd"}'},
                            }],
                        },
                    }],
                },
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.01},
            },
            {
                "timestamp": "2026-08-19T10:00:01Z",
                "duration_ms": 200,
                "success": True,
                "request_body": {
                    "model": "fixture-model",
                    "messages": [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "task"},
                        {
                            "role": "assistant",
                            "reasoning_content": "inspect",
                            "content": "",
                            "tool_calls": [{
                                "id": "call-1",
                                "function": {"name": "shell", "arguments": '{"cmd":"pwd"}'},
                            }],
                        },
                        {"role": "tool", "tool_call_id": "call-1", "content": "/tmp"},
                    ],
                },
                "response_body": {
                    "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}],
                },
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24, "cost": 0.02},
            },
        ]
        node_script = (
            f"eval({json.dumps(trace_model)});"
            f"const trace=analyzeTrajectoryRecord({json.dumps(fixture)});"
            "process.stdout.write(JSON.stringify({format:trace.format,messages:trace.messages.length,"
            "turns:trace.summary.turns,calls:trace.summary.tool_calls,results:trace.summary.tool_results,"
            "tokens:trace.summary.tokens,cost:trace.summary.cost_usd,provider:trace.summary.provider}));"
        )
        parsed = json.loads(subprocess.check_output([node, "-e", node_script], text=True))
        expected = {
            "format": "LiteLLM events",
            "messages": 5,
            "turns": 2,
            "calls": 1,
            "results": 1,
            "tokens": 39,
            "cost": 0.03,
            "provider": "fixture-provider",
        }
        if parsed != expected:
            raise AssertionError(f"LiteLLM event normalization duplicated or dropped trace data: {parsed}")
        missing_metrics_fixture = json.loads(json.dumps(fixture))
        missing_metrics_fixture[0]["usage"]["total_tokens"] = None
        missing_metrics_fixture[0]["usage"]["cost"] = None
        missing_metrics_fixture[0]["duration_ms"] = None
        missing_metrics_fixture[1]["usage"]["total_tokens"] = ""
        missing_metrics_fixture[1]["usage"]["cost"] = "   "
        missing_metrics_fixture[1]["duration_ms"] = ""
        missing_metrics_script = (
            f"eval({json.dumps(trace_model)});"
            f"const trace=analyzeTrajectoryRecord({json.dumps(missing_metrics_fixture)});"
            "process.stdout.write(JSON.stringify({tokens:trace.summary.tokens,"
            "cost:trace.summary.cost_usd,duration:trace.summary.llm_duration_sec,"
            "nullValue:traceNumber(null),emptyValue:traceNumber('   ')}));"
        )
        missing_metrics = json.loads(
            subprocess.check_output([node, "-e", missing_metrics_script], text=True)
        )
        if missing_metrics != {
            "tokens": 39,
            "cost": None,
            "duration": None,
            "nullValue": None,
            "emptyValue": None,
        }:
            raise AssertionError(f"missing trace metrics were converted to zero: {missing_metrics}")
        sort_start = rendered_html.index("function trajNumeric")
        sort_end = rendered_html.index("function topCountLabel", sort_start)
        sort_model = rendered_html[sort_start:sort_end]
        sort_fixture = [
            {"id": "reward-one", "status": "pass", "score": None, "reward": 1},
            {"id": "reward-zero", "status": "pass", "score": None, "reward": 0},
            {"id": "failed", "status": "fail", "score": None, "reward": 1},
            {"id": "scored", "status": "scored", "score": 0.5, "reward": None},
        ]
        sort_script = (
            "const R2_API_AVAILABLE=false; let trajSampleSeed=1;"
            f"eval({json.dumps(sort_model)});"
            f"const cards={json.dumps(sort_fixture)};"
            "process.stdout.write(JSON.stringify({"
            "high:trajSortCards(cards,'high').map(card=>card.id),"
            "clean:trajSortCards(cards,'clean').map(card=>card.id)}));"
        )
        sorted_cards = json.loads(
            subprocess.check_output([node, "-e", sort_script], text=True)
        )
        pass_score_order = [
            card_id for card_id in sorted_cards["high"] if card_id != "failed"
        ]
        if pass_score_order != ["reward-one", "scored", "reward-zero"]:
            raise AssertionError(f"null scores still mask rewards: {sorted_cards}")
        if set(sorted_cards["clean"]) != {"reward-one", "reward-zero", "scored"}:
            raise AssertionError(f"Clean mode includes failed trajectories: {sorted_cards}")
        failure_fixture = fixture + [
            {
                "timestamp": "2026-08-19T10:00:02Z",
                "success": False,
                "request_body": fixture[-1]["request_body"],
                "failure": {"message": "rate limited"},
            }
        ]
        failure_script = (
            f"eval({json.dumps(trace_model)});"
            f"const trace=analyzeTrajectoryRecord({json.dumps(failure_fixture)});"
            "const retry=trace.events[trace.events.length-1];"
            "process.stdout.write(JSON.stringify({failures:trace.summary.api_failures,"
            "incoming:retry.incoming.length,assistant:Boolean(retry.assistant),"
            "failure:retry.event.failure}));"
        )
        failed_retry = json.loads(
            subprocess.check_output([node, "-e", failure_script], text=True)
        )
        if failed_retry != {
            "failures": 1,
            "incoming": 0,
            "assistant": False,
            "failure": {"message": "rate limited"},
        }:
            raise AssertionError(f"repeated failed API attempt was dropped: {failed_retry}")
        markdown_start = rendered_html.index("// TRACE_MARKDOWN_MODEL_START")
        markdown_end = rendered_html.index("// TRACE_MARKDOWN_MODEL_END", markdown_start)
        markdown_model = rendered_html[markdown_start:markdown_end]
        tick = chr(96)
        markdown_fixture = (
            "# Heading\n\n"
            f"Left-aligned **prose** with <unsafe> and {tick}inline{tick} code.\n\n"
            f"{tick * 3}python\nprint('<unsafe>')\n{tick * 3}\n\n"
            "- first\n- second"
        )
        markdown_script = (
            f"eval({json.dumps(markdown_model)});"
            f"const blocks=traceMarkdownBlocks({json.dumps(markdown_fixture)});"
            "const open=traceMarkdownBlocks('~~~javascript\\nconst answer = 42;');"
            "process.stdout.write(JSON.stringify({blocks,open}));"
        )
        markdown_parsed = json.loads(
            subprocess.check_output([node, "-e", markdown_script], text=True)
        )
        if [block.get("type") for block in markdown_parsed["blocks"]] != [
            "prose",
            "code",
            "prose",
        ]:
            raise AssertionError(
                f"trace Markdown did not separate prose and fenced code: {markdown_parsed}"
            )
        code_block = markdown_parsed["blocks"][1]
        if (
            code_block.get("language") != "python"
            or code_block.get("text") != "print('<unsafe>')"
            or code_block.get("closed") is not True
        ):
            raise AssertionError(f"fenced code metadata/content was not preserved: {code_block}")
        if (
            len(markdown_parsed["open"]) != 1
            or markdown_parsed["open"][0].get("type") != "code"
            or markdown_parsed["open"][0].get("closed") is not False
        ):
            raise AssertionError(
                f"an unclosed fence was not handled as a bounded code block: {markdown_parsed['open']}"
            )
    exported_quality = [
        json.loads(line)
        for line in (tmp / "public-site" / "data" / "quality_fact.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if exported_quality[0].get("preview"):
        raise AssertionError("--no-include-samples still exported a quality preview")

    full_site_args = dashboard.parse_args(
        [
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
            str(tmp / "full-site" / "index.html"),
            "--cache-file",
            str(tmp / "full-dashboard-cache.json"),
            "--embedded-traj-limit",
            "1",
            "--embedded-traj-max-bytes",
            "10000",
        ]
    )
    dashboard.run_once(full_site_args, 60)
    full_cards = [
        json.loads(line)
        for line in (tmp / "full-site" / "data" / "traj_cards.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sampleable_cards = [
        card
        for card in full_cards
        if card.get("kind") == "quality" and card.get("embedded_available") is True
    ]
    if len(sampleable_cards) != 1:
        raise AssertionError(f"generated sampler has no loadable valid trajectory: {sampleable_cards}")
    full_server = dashboard.start_http_server(tmp / "full-site", "127.0.0.1", 0)
    try:
        full_port = full_server.server_address[1]
        with urllib.request.urlopen(
            f"http://127.0.0.1:{full_port}/{sampleable_cards[0]['embedded_path']}"
        ) as response:
            served_full_row = json.loads(response.read().decode("utf-8"))
    finally:
        full_server.shutdown()
        full_server.server_close()
    if served_full_row.get("id") != sampleable_cards[0].get("id"):
        raise AssertionError(f"generated sampler full trace failed its HTTP lookup: {served_full_row}")

    demo_html = (block / "docs" / "public" / "dashboard_demo" / "tracer" / "index.html").read_text(
        encoding="utf-8"
    )
    demo_cards_match = re.search(
        r'<script id="trajCardData" type="application/json">(.*?)</script>',
        demo_html,
        flags=re.DOTALL,
    )
    if not demo_cards_match:
        raise AssertionError("checked-in dashboard demo has no trajectory card payload")
    demo_cards = json.loads(demo_cards_match.group(1))
    if any(card.get("embedded_available") or card.get("embedded_path") for card in demo_cards):
        raise AssertionError("checked-in dashboard demo advertises trajectory shards it does not ship")

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
