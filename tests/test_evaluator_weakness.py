"""RSI-loop evaluator measurement tests.

Fixture-driven tests for blocks/evaluator/scripts/:
  * build_weakness_report.py — pooled pass rates, per-repeat SD, Wilson CI,
    audit-slice exclusion, n_too_small blocking, placebo blinding, history /
    response-matrix / ledger append semantics, signal-firewall integration.
  * build_identity_index.py  — repo/pr parse, normalized patch sha stability.
  * make_audit_slice.py      — seeded stratified determinism, refusal on n > available.

All fixtures are fabricated mini job dirs / gold datasets under tmp_path:
per-trial result.json files (task_name + verifier_result.rewards.reward), a
mini analysis/ dir (instances.jsonl with online-eligible fields plus gold-aware
fields that must be ignored, task_instances.jsonl), gold task.toml files, and a
mini audit_slice.txt. No network, no Docker.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "blocks" / "evaluator" / "scripts"

TASK_A = "django__django-13741"        # python / backend / orm / null-pointer, easy
TASK_B = "astropy__astropy-12349"      # python / backend / io / encoding, easy
TASK_C = "sympy__sympy-24152"          # python / library / algebra / wrong-type, hard
TASK_D = "pvlib__pvlib-python-1234"    # no task.toml -> language-only fallback (audited)

# Round-1 rewards (repeat1, repeat2); D is excluded by the audit slice.
REWARDS_R1 = {TASK_A: (1, 1), TASK_B: (0, 0), TASK_C: (1, 0), TASK_D: (1, 0)}
# Round-2 rewards: everything solved.
REWARDS_R2 = {TASK_A: (1, 1), TASK_B: (1, 1), TASK_C: (1, 1), TASK_D: (1, 1)}
PRIMARY_FAILURE = {TASK_A: "implementation", TASK_B: "diagnosis", TASK_C: "tool_usage"}
TIER = {TASK_A: "easy", TASK_B: "easy", TASK_C: "hard"}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


weakness = _load("build_weakness_report")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
def write_gold_dataset(root: Path) -> Path:
    dataset_dir = root / "gold" / "swebench-test"
    tags = {
        TASK_A: ["python", "backend", "orm", "null-pointer"],
        TASK_B: ["python", "backend", "io", "encoding"],
        TASK_C: ["python", "library", "algebra", "wrong-type"],
    }
    difficulty = {TASK_A: "easy", TASK_B: "easy", TASK_C: "hard"}
    for task, tag_list in tags.items():
        task_dir = dataset_dir / task
        (task_dir / "tests").mkdir(parents=True, exist_ok=True)
        owner, _rest = task.split("__")
        short = _rest.split("-")[0]
        patch = (
            f"--- a/{short}/models.py\n+++ b/{short}/models.py\n"
            "@@ -10,4 +10,5 @@ def f(x):\n     return x\n+    return x + 1\n"
            f"--- a/{short}/utils.py\n+++ b/{short}/utils.py\n"
            "@@ -1,2 +1,3 @@\n old\n+new\n context\n"
        )
        (task_dir / "tests" / "config.json").write_text(json.dumps({
            "instance_id": task,
            "repo": f"{owner}/{short}",
            "patch": patch,
            "test_patch": f"--- a/tests/test_{short}.py\n+++ b/tests/test_{short}.py\n"
                          "@@ -1,2 +1,3 @@\n def test_x():\n+    assert True\n",
            "FAIL_TO_PASS": [f"tests/test_{short}.py::test_x"],
        }), encoding="utf-8")
        (task_dir / "task.toml").write_text(
            "[metadata]\n"
            f'tags = ["{tag_list[0]}", "{tag_list[1]}", "{tag_list[2]}", "{tag_list[3]}"]\n'
            f'difficulty = "{difficulty[task]}"\n'
            "[verifier]\ntimeout_sec = 600\n",
            encoding="utf-8",
        )
    # TASK_D: gold config but no task.toml (language-only fallback), audited out
    task_dir = dataset_dir / TASK_D
    (task_dir / "tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "tests" / "config.json").write_text(json.dumps({
        "instance_id": TASK_D, "repo": "pvlib/pvlib-python", "patch": "",
    }), encoding="utf-8")
    return dataset_dir


def write_job(job_dir: Path, rewards: dict, receipt: dict | None = None) -> None:
    """One repeat job: per-trial result.json + analysis/ + config snapshot."""
    job_dir.mkdir(parents=True)
    for idx, (task, value) in enumerate(sorted(rewards.items())):
        reward = value if isinstance(value, (int, float)) else value[0]
        trial_dir = job_dir / f"{task[:32]}__{hashlib.md5(task.encode()).hexdigest()[:7]}"
        trial_dir.mkdir()
        result = {
            "task_name": task,
            "verifier_result": {"rewards": {"reward": reward}},
        }
        if task == TASK_A:
            result["agent_result"] = {"n_input_tokens": 100, "n_output_tokens": 50}
        if task == TASK_B and receipt:
            result["exception_info"] = {
                "exception_type": "EnvironmentError",
                "exception_message": "docker blew up",
            }
        (trial_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    # Job-level aggregate must be ignored by the per-trial scan.
    (job_dir / "result.json").write_text(
        json.dumps({"metrics": {"mean": 0.5}}), encoding="utf-8")
    (job_dir / "config.yaml").write_text(yaml.safe_dump({
        "runtime_info": {"input": {"agent": {"name": "custom-openhands-sdk"}}},
    }), encoding="utf-8")
    analysis = job_dir / "analysis"
    analysis.mkdir()
    with (analysis / "instances.jsonl").open("w", encoding="utf-8") as fh:
        for task in sorted(rewards):
            fh.write(json.dumps({
                "instance_id": task,
                "resolved": bool(rewards[task]),
                "scaffold": "openhands-sdk",
                "primary_failure": PRIMARY_FAILURE.get(task, "unknown"),
                "axes": {"localization": "partial", "diagnosis": "wrong",
                         "implementation": "no_edit", "tool_usage": "ok",
                         "long_horizon": "ok"},
                "flags": ["empty_patch"],
                # Gold-aware fields exist in the real records: they must be
                # ignored by the report and would be refused by the firewall.
                "deterministic_features": {
                    "C1_file_read": 1, "C4_func_alignment": 0,
                    "diff_hunk_overlap": 0.75, "c5_test_executed": 1,
                },
                "tier1_labels": {"localization_quality": "partial"},
            }) + "\n")
    with (analysis / "task_instances.jsonl").open("w", encoding="utf-8") as fh:
        for task in sorted(rewards):
            fh.write(json.dumps({
                "instance_id": task, "difficulty_tier": TIER.get(task, "unknown"),
                "gold_files_modified": 2,
            }) + "\n")
    if receipt:
        (job_dir / "round_receipt.json").write_text(json.dumps(receipt),
                                                    encoding="utf-8")


def build_round(root: Path, round_no: int) -> list[Path]:
    """Create a k=2 repeat job pair for the given round number."""
    rewards = REWARDS_R1 if round_no == 1 else REWARDS_R2
    jobs = []
    for repeat in (1, 2):
        job_dir = root / f"round{round_no}" / f"job_r{repeat}"
        per_task = {t: vals[repeat - 1] for t, vals in rewards.items()}
        receipt = {"round": round_no,
                   "checkpoint_sha256": f"{round_no:064d}"[::-1]} if repeat == 1 else None
        write_job(job_dir, per_task, receipt=receipt)
        jobs.append(job_dir)
    return jobs


def write_audit_slice(root: Path, ids: list[str]) -> Path:
    path = root / "reserve" / "audit_slice.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(ids) + "\n", encoding="utf-8")
    return path


def run_weakness(root: Path, jobs: list[Path], *, min_tag_n=2, placebo=None,
                 audit_slice=None, dataset_dir=None, expect_ok=True,
                 receipt=None, prev_directives=None):
    out = root / "weakness" / "weakness_report.json"
    history = root / "weakness" / "history"
    matrix = root / "response_matrix.jsonl"
    ledger = root / "loop" / "ledger.yaml"
    dataset_dir = dataset_dir or write_gold_dataset(root)
    audit_slice = audit_slice or write_audit_slice(root, [TASK_D])
    argv = [sys.executable, str(SCRIPTS_DIR / "build_weakness_report.py"),
            "--jobs", *[str(j) for j in jobs],
            "--dataset", "swebench-test",
            "--audit-slice", str(audit_slice),
            "--out", str(out),
            "--history-dir", str(history),
            "--response-matrix", str(matrix),
            "--ledger", str(ledger),
            "--dataset-dir", str(dataset_dir),
            "--min-tag-n", str(min_tag_n)]
    if placebo is not None:
        argv += ["--placebo", str(placebo)]
    if receipt is not None:
        argv += ["--receipt", str(receipt)]
    if prev_directives is not None:
        argv += ["--prev-directives", str(prev_directives)]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if expect_ok:
        assert proc.returncode == 0, proc.stderr
    return {"proc": proc, "out": out, "history": history,
            "matrix": matrix, "ledger": ledger}


def wilson_ref(resolved: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Independent textbook Wilson score interval for cross-checking."""
    p = resolved / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom, (centre + margin) / denom


@pytest.fixture()
def round1(tmp_path):
    jobs = build_round(tmp_path, 1)
    result = run_weakness(tmp_path, jobs)
    result["report"] = json.loads(result["out"].read_text(encoding="utf-8"))
    return result


# ---------------------------------------------------------------------------
# Rates / SD / Wilson
# ---------------------------------------------------------------------------
def test_report_schema_and_identity(round1):
    report = round1["report"]
    assert report["schema"] == "legoflow.weakness.v1"
    assert report["k"] == 2
    assert report["dataset"] == "swebench-test"
    assert report["round"] == 1
    assert report["eval_jobs"] == ["job_r1", "job_r2"]
    assert report["checkpoint_ref"].startswith("round1:")
    assert report["checkpoint_sha"] == f"{1:064d}"[::-1]
    assert report["generated_at"]
    for key in ("noise", "audit_excluded_ids_sha256", "tagger_version",
                "tags", "tasks", "directives_blocked"):
        assert key in report


def test_pooled_rate_and_wilson(round1):
    tags = {t["key"]: t for t in round1["report"]["tags"]}
    lang = tags["language:python"]
    # A:(1,1) B:(0,0) C:(1,0), D audit-excluded -> 3/6
    assert lang["n"] == 6
    assert lang["resolved"] == 3
    assert lang["pass_rate"] == pytest.approx(0.5, abs=1e-6)
    lo, hi = wilson_ref(3, 6)
    assert lang["ci95_wilson"][0] == pytest.approx(lo, abs=1e-6)
    assert lang["ci95_wilson"][1] == pytest.approx(hi, abs=1e-6)
    # per-repeat rates: 2/3 and 1/3
    assert lang["per_repeat"] == [pytest.approx(2 / 3, abs=1e-6),
                                  pytest.approx(1 / 3, abs=1e-6)]


def test_between_repeat_sd(round1):
    tags = {t["key"]: t for t in round1["report"]["tags"]}
    # sd([2/3, 1/3], ddof=1) = sqrt(1/18)
    assert tags["language:python"]["tag_sd"] == pytest.approx(math.sqrt(1 / 18), abs=1e-6)
    # single-instance-per-repeat cell C: sd([1.0, 0.0]) = sqrt(0.5)
    assert tags["area:library"]["tag_sd"] == pytest.approx(math.sqrt(0.5), abs=1e-6)
    # A+B cell: [0.5, 0.5] -> 0
    assert tags["area:backend"]["tag_sd"] == 0.0
    noise = round1["report"]["noise"]
    assert noise["k"] == 2
    assert noise["between_repeat_sd"] == pytest.approx(math.sqrt(1 / 18), abs=1e-6)
    assert noise["per_tag_sd"]["language:python"] == pytest.approx(math.sqrt(1 / 18),
                                                                   abs=1e-6)


def test_tag_dimensions_from_toml_and_analysis_join(round1):
    keys = {t["key"] for t in round1["report"]["tags"]}
    for expected in ("language:python", "area:backend", "area:library",
                     "topic:orm", "bug_class:null-pointer",
                     "difficulty_tier:easy", "difficulty_tier:hard"):
        assert expected in keys, expected
    # The audited task has no task.toml and is excluded anyway.
    assert "language:unknown" not in keys


def test_failure_mix_uses_online_fields_only(round1):
    tags = {t["key"]: t for t in round1["report"]["tags"]}
    mix = tags["language:python"]["failure_mix"]
    assert mix == {"diagnosis": pytest.approx(1 / 3, abs=1e-6),
                   "implementation": pytest.approx(1 / 3, abs=1e-6),
                   "tool_usage": pytest.approx(1 / 3, abs=1e-6)}
    # gold-aware fields present in instances.jsonl must not leak anywhere
    dumped = json.dumps(round1["report"])
    for banned in ("diff_hunk_overlap", "C1_file_read", "c4_func_alignment",
                   "tier1_labels", "gold_files_modified"):
        assert banned not in dumped


def test_tasks_block_and_exceptions(round1):
    tasks = round1["report"]["tasks"]
    assert set(tasks) == {TASK_A, TASK_B, TASK_C}   # D audit-excluded
    assert tasks[TASK_A]["per_repeat"] == [1.0, 1.0]
    assert tasks[TASK_B]["reward_mean"] == 0.0
    assert tasks[TASK_A]["exceptions"] == []
    assert tasks[TASK_B]["exceptions"] == ["EnvironmentError"]


# ---------------------------------------------------------------------------
# Audit slice / blocking / placebo
# ---------------------------------------------------------------------------
def test_audit_slice_exclusion_and_hash(round1):
    report = round1["report"]
    expected_hash = hashlib.sha256(TASK_D.encode()).hexdigest()  # one sorted id
    assert report["audit_excluded_ids_sha256"] == expected_hash
    rows = [json.loads(line) for line in
            round1["matrix"].read_text(encoding="utf-8").splitlines()]
    assert {r["task_id"] for r in rows} == {TASK_A, TASK_B, TASK_C}


def test_missing_audit_slice_warns_and_keeps_task(tmp_path):
    jobs = build_round(tmp_path, 1)
    result = run_weakness(tmp_path, jobs,
                          audit_slice=tmp_path / "nope" / "audit_slice.txt")
    assert "WARNING" in result["proc"].stderr
    report = json.loads(result["out"].read_text(encoding="utf-8"))
    assert TASK_D in report["tasks"]
    assert report["audit_excluded_ids_sha256"] == hashlib.sha256(b"").hexdigest()
    tags = {t["key"]: t for t in report["tags"]}
    # D has no task.toml, so it joins via the language-only fallback cell.
    assert tags["language:unknown"]["n"] == 2
    assert tags["language:unknown"]["pass_rate"] == pytest.approx(0.5, abs=1e-6)
    assert tags["language:python"]["n"] == 6


def test_n_too_small_blocks_directives(tmp_path):
    jobs = build_round(tmp_path, 1)
    result = run_weakness(tmp_path, jobs, min_tag_n=20)
    report = json.loads(result["out"].read_text(encoding="utf-8"))
    blocked = {b["key"]: b["reason"] for b in report["directives_blocked"]}
    assert blocked  # every cell has n < 20 here
    assert set(blocked.values()) == {"n_too_small"}
    assert "language:python" in blocked


def test_sub_noise_delta_gate_fires_and_boundary_is_strict(tmp_path):
    # Report gate runs at 1x max(tag_sd, global_sd) — deliberately looser
    # than the curator actuation gate (apply_targeting.py applies
    # noise_gate_multiplier, default 2.0, times max(sd, global_sd, 0)).
    # Case 1 — fires: round-2 rewards identical to round 1 -> every
    # delta == 0.0 while tag_sd/global_sd stay at sqrt(1/18) > 0.
    run_weakness(tmp_path, build_round(tmp_path, 1))
    jobs2 = []
    for repeat in (1, 2):
        job_dir = tmp_path / "round2b" / f"job_r{repeat}"
        per_task = {t: vals[repeat - 1] for t, vals in REWARDS_R1.items()}
        write_job(job_dir, per_task,
                  receipt={"round": 2, "checkpoint_sha256": f"{2:064d}"[::-1]}
                  if repeat == 1 else None)
        jobs2.append(job_dir)
    report = json.loads(run_weakness(tmp_path, jobs2)["out"].read_text(encoding="utf-8"))
    blocked = {b["key"]: b["reason"] for b in report["directives_blocked"]}
    assert blocked and set(blocked.values()) == {"sub_noise_delta"}
    lang = {t["key"]: t for t in report["tags"]}["language:python"]
    assert lang["delta_vs_prev"] == 0.0                       # pins threshold inputs
    assert lang["tag_sd"] == pytest.approx(math.sqrt(1 / 18), abs=1e-6)
    assert report["noise"]["between_repeat_sd"] == pytest.approx(math.sqrt(1 / 18), abs=1e-6)
    assert "language:python" in blocked

    # Case 2 — strict boundary: default round 2 (all rewards 1) has
    # tag_sd == global_sd == 0 and TASK_A-only cells with delta == 0.0,
    # i.e. |delta| == threshold exactly; strict '<' must NOT block.
    root = tmp_path / "boundary"
    root.mkdir()
    run_weakness(root, build_round(root, 1))
    rep2 = json.loads(run_weakness(root, build_round(root, 2))["out"].read_text(encoding="utf-8"))
    assert rep2["directives_blocked"] == []


def test_placebo_is_blind_in_report_and_registered_in_history(tmp_path):
    jobs = build_round(tmp_path, 1)
    result = run_weakness(tmp_path, jobs, placebo=7)
    report = json.loads(result["out"].read_text(encoding="utf-8"))
    # round 1 has null deltas, so first run a round-2 placebo over real deltas
    jobs2 = build_round(tmp_path, 2)
    result2 = run_weakness(tmp_path, jobs2, placebo=7)
    report2 = json.loads(result2["out"].read_text(encoding="utf-8"))
    dumped = json.dumps(report2).lower()
    assert "placebo" not in dumped          # blind: the report is unmarked
    registration = yaml.safe_load(
        (result2["history"] / "placebo_rounds.yaml").read_text(encoding="utf-8"))
    assert isinstance(registration, list) and registration[-1]["seed"] == 7
    assert registration[-1]["round"] == 2
    assert registration[-1]["value_swap"]
    ledger = yaml.safe_load(result2["ledger"].read_text(encoding="utf-8"))
    assert ledger[-1]["placebo"] is True


def test_placebo_permutation_is_deterministic(tmp_path):
    swaps = []
    for _ in range(2):
        root = tmp_path / f"fresh{len(swaps)}"
        jobs1 = build_round(root, 1)
        run_weakness(root, jobs1)
        jobs2 = build_round(root, 2)
        result = run_weakness(root, jobs2, placebo=7)
        registration = yaml.safe_load(
            (result["history"] / "placebo_rounds.yaml").read_text(encoding="utf-8"))
        swaps.append(registration[-1]["value_swap"])
    assert swaps[0] == swaps[1]


def test_placebo_permutes_but_preserves_multiset(tmp_path):
    root = tmp_path
    jobs1 = build_round(root, 1)
    run_weakness(root, jobs1)
    jobs2 = build_round(root, 2)
    result = run_weakness(root, jobs2, placebo=7)
    report = json.loads(result["out"].read_text(encoding="utf-8"))
    # Round-2 deltas per cell: pooled cells +0.5, TASK_A-only cells +0.0
    # (already 1.0), TASK_B-only cells +1.0 (was 0.0), TASK_C-only cells +0.5.
    deltas = sorted(t["delta_vs_prev"] for t in report["tags"])
    assert deltas == sorted([0.5] * 7 + [0.0, 0.0, 1.0, 1.0])
    registration = yaml.safe_load(
        (result["history"] / "placebo_rounds.yaml").read_text(encoding="utf-8"))
    swap = registration[-1]["value_swap"]
    assert len(swap) == len(report["tags"])
    assert set(swap.values()) == {t["key"] for t in report["tags"]}


def test_placebo_report_keeps_real_report_invariants(tmp_path):
    """A placebo report must be indistinguishable from a real one: only the
    deltas move, and learning_progress / directives_blocked are re-derived
    against the destination tag's own noise floor. (The round-2 fixture has
    heterogeneous 1/n floors — n ranges 2..6 — so a permuted (delta, LP)
    *pair* breaks the learning_progress == delta / max(tag_sd, global_sd, 1/n)
    invariant and is caught here.)"""
    root = tmp_path
    run_weakness(root, build_round(root, 1))
    result = run_weakness(root, build_round(root, 2), placebo=7)
    report = json.loads(result["out"].read_text(encoding="utf-8"))
    global_sd = report["noise"]["between_repeat_sd"]
    blocked = {b["key"]: b["reason"] for b in report["directives_blocked"]}
    assert len(blocked) == len(report["directives_blocked"])
    for tag in report["tags"]:
        delta = tag["delta_vs_prev"]
        if delta is None:
            assert tag["learning_progress"] is None
        else:
            # bit-identical to a real report's derivation from stored values
            floor = max(tag["tag_sd"], global_sd, 1.0 / tag["n"])
            assert tag["learning_progress"] == round(delta / floor, 6), tag["key"]
        # min_tag_n=2 here, so membership must follow exactly the permuted
        # sub-noise rule (never the pre-placebo deltas).
        expect_blocked = (delta is not None
                          and abs(delta) < max(tag["tag_sd"], global_sd))
        assert (tag["key"] in blocked) == expect_blocked, tag["key"]
        if expect_blocked:
            assert blocked[tag["key"]] == "sub_noise_delta"


# ---------------------------------------------------------------------------
# History / response matrix / ledger append semantics
# ---------------------------------------------------------------------------
def test_round2_delta_learning_progress_and_appends(tmp_path):
    jobs1 = build_round(tmp_path, 1)
    first = run_weakness(tmp_path, jobs1)
    round1_bytes = (first["history"] / "weakness_report_001.json").read_bytes()

    jobs2 = build_round(tmp_path, 2)
    second = run_weakness(tmp_path, jobs2)
    report2 = json.loads(second["out"].read_text(encoding="utf-8"))
    assert report2["round"] == 2

    # History: append-only; round 1 untouched, round 2 added.
    assert (first["history"] / "weakness_report_001.json").read_bytes() == round1_bytes
    assert (second["history"] / "weakness_report_002.json").is_file()

    tags = {t["key"]: t for t in report2["tags"]}
    lang = tags["language:python"]
    assert lang["delta_vs_prev"] == pytest.approx(0.5, abs=1e-6)
    # round 2: all rewards 1 -> tag_sd = global_sd = 0 -> floor = 1/n = 1/6
    assert lang["learning_progress"] == pytest.approx(0.5 * 6, abs=1e-6)
    assert lang["noise_floor"] == pytest.approx(1 / 6, abs=1e-6)
    # round 1 had null progress
    round1 = json.loads(round1_bytes)
    assert all(t["delta_vs_prev"] is None and t["learning_progress"] is None
               for t in round1["tags"])

    # Response matrix: rows from both rounds, keyed by checkpoint sha.
    rows = [json.loads(line) for line in
            second["matrix"].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 12  # 2 rounds x 2 repeats x 3 tasks
    rounds = {r["round"] for r in rows}
    assert rounds == {1, 2}
    for row in rows:
        assert row["scaffold"] == "openhands-sdk"
        assert row["repeat_idx"] in (0, 1)
        assert row["checkpoint_sha"] == f"{row['round']:064d}"[::-1]
    row_a = next(r for r in rows if r["task_id"] == TASK_A and r["round"] == 1)
    assert row_a["tokens"] == 150
    row_c = next(r for r in rows if r["task_id"] == TASK_C and r["round"] == 1)
    assert row_c["tokens"] is None

    # Ledger: one measurement row per round, prior row preserved verbatim.
    ledger = yaml.safe_load(second["ledger"].read_text(encoding="utf-8"))
    assert isinstance(ledger, list) and len(ledger) == 2
    row1, row2 = ledger
    assert row1["round"] == 1 and row2["round"] == 2
    assert row1["internal_resolve"] == pytest.approx(0.5, abs=1e-6)
    assert row2["internal_resolve"] == 1.0
    assert row1["directives_sha256"] is None and row1["verdict"] is None
    assert row1["eval_jobs"] == ["job_r1", "job_r2"]
    assert row1["placebo"] is False
    assert row1["between_repeat_sd"] == pytest.approx(math.sqrt(1 / 18), abs=1e-6)


# ---------------------------------------------------------------------------
# Empty-repeat handling, audit-slice parsing, ledger durability
# ---------------------------------------------------------------------------
def test_empty_repeat_excluded_from_noise_floor(tmp_path):
    """A repeat whose Harbor run wrote no per-trial results must not fabricate
    a 0.0 global rate — a phantom rate would inflate between_repeat_sd and
    every tag's noise floor; the repeat is excluded and recorded in
    noise.empty_repeats (the gate uses measured spread)."""
    populated = tmp_path / "round1" / "job_r1"
    empty = tmp_path / "round1" / "job_r2"
    write_job(populated, REWARDS_R1)   # tuple values -> repeat-1 rewards
    empty.mkdir(parents=True)          # Harbor failed before any trial result
    (empty / "config.yaml").write_text(yaml.safe_dump(
        {"runtime_info": {"input": {"agent": {"name": "custom-openhands-sdk"}}}}),
        encoding="utf-8")
    result = run_weakness(tmp_path, [populated, empty])

    assert "repeat job index 1" in result["proc"].stderr
    report = json.loads(result["out"].read_text(encoding="utf-8"))
    assert report["k"] == 2                    # both joined job dirs documented
    assert report["noise"]["empty_repeats"] == [1]
    # Only the populated repeat feeds the spread: one real rate -> sd 0.0
    # (k==1 semantics) instead of sd([2/3, 0.0]) > 0.
    assert report["noise"]["between_repeat_sd"] == 0.0
    tags = {t["key"]: t for t in report["tags"]}
    lang = tags["language:python"]
    assert lang["n"] == 3                       # A=1, B=0, C=1; D audit-excluded
    assert lang["noise_floor"] == pytest.approx(1 / 3, abs=1e-6)  # floor = 1/n
    # Tasks still document the empty repeat as a missing observation.
    assert report["tasks"][TASK_A]["per_repeat"] == [1.0, None]
    # The response matrix gains rows only from the populated repeat.
    rows = [json.loads(line) for line in
            result["matrix"].read_text(encoding="utf-8").splitlines()]
    assert {r["repeat_idx"] for r in rows} == {0}
    ledger = yaml.safe_load(result["ledger"].read_text(encoding="utf-8"))
    assert ledger[-1]["between_repeat_sd"] == 0.0


def test_audit_slice_indented_comment_is_not_an_id(tmp_path):
    """An indented comment line must not leak into the exclusion set (and from
    there into audit_excluded_ids_sha256, breaking cross-round comparability)."""
    jobs = build_round(tmp_path, 1)
    slice_path = tmp_path / "reserve" / "audit_slice.txt"
    slice_path.parent.mkdir(parents=True)
    slice_path.write_text(
        "  # indented comment\n\t# tab-indented comment\n" + TASK_D + "\n",
        encoding="utf-8")
    result = run_weakness(tmp_path, jobs, audit_slice=slice_path)
    report = json.loads(result["out"].read_text(encoding="utf-8"))
    assert report["audit_excluded_ids_sha256"] == hashlib.sha256(
        TASK_D.encode()).hexdigest()
    assert TASK_D not in report["tasks"]
    assert all("#" not in task for task in report["tasks"])


def test_multi_id_slice_hash_serialization_and_exclusion(tmp_path):
    """The exclusion-set hash is sha256 over the sorted '\\n'-joined ids —
    deduplicated, order-independent — and every listed id is dropped from
    both the report's tasks and the response matrix (plan §5.4 A1 step 2)."""
    jobs = build_round(tmp_path, 1)
    slice_path = tmp_path / "reserve" / "audit_slice.txt"
    slice_path.parent.mkdir(parents=True)
    slice_path.write_text(f"{TASK_D}\n{TASK_C}\n{TASK_D}\n  # note\n",
                          encoding="utf-8")
    result = run_weakness(tmp_path, jobs, audit_slice=slice_path)
    report = json.loads(result["out"].read_text(encoding="utf-8"))
    expected = hashlib.sha256(
        "\n".join(sorted({TASK_D, TASK_C})).encode("utf-8")).hexdigest()
    assert report["audit_excluded_ids_sha256"] == expected
    assert TASK_C not in report["tasks"] and TASK_D not in report["tasks"]
    rows = [json.loads(line) for line in
            result["matrix"].read_text(encoding="utf-8").splitlines()]
    assert {r["task_id"] for r in rows} == {TASK_A, TASK_B}


def test_ledger_append_is_atomic_and_leaves_no_tmp(tmp_path):
    """append_yaml_list writes through tmp+replace: prior rows survive each
    append and no .tmp sidecar is left behind (a crash mid-write can never
    truncate the measurement ledger)."""
    path = tmp_path / "loop" / "ledger.yaml"
    weakness.append_yaml_list(path, {"round": 1, "between_repeat_sd": 0.1})
    weakness.append_yaml_list(path, {"round": 2, "between_repeat_sd": 0.0})
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [r["round"] for r in rows] == [1, 2]
    assert sorted(p.name for p in path.parent.iterdir()) == ["ledger.yaml"]


# ---------------------------------------------------------------------------
# Re-invocation / re-measure semantics (no phantom rounds, no double appends)
# ---------------------------------------------------------------------------
def test_builder_reinvocation_refuses_phantom_round(tmp_path):
    """Invoking the builder twice over the same job dirs must not mint a
    phantom round-2 measured against an identical copy of itself."""
    jobs = build_round(tmp_path, 1)
    first = run_weakness(tmp_path, jobs)
    out_bytes = first["out"].read_bytes()
    again = run_weakness(tmp_path, jobs, expect_ok=False)   # same jobs
    assert again["proc"].returncode != 0
    assert "already measured" in again["proc"].stderr
    # Nothing was touched by the refused re-run.
    assert first["out"].read_bytes() == out_bytes
    assert sorted(p.name for p in first["history"].iterdir()) == \
        ["weakness_report_001.json"]
    assert len(first["matrix"].read_text().splitlines()) == 6
    assert len(yaml.safe_load(first["ledger"].read_text())) == 1


def test_reinvoking_oldest_round_refuses_phantom_round(tmp_path):
    """Regression: the re-measure dedup used to compare only against the NEWEST
    history copy, so re-invoking round 1's exact command with >=2 rounds on
    file silently minted a phantom round 3 — a new history copy, duplicate
    response-matrix rows, and a third ledger row, all with exit 0."""
    root = tmp_path
    jobs1 = build_round(root, 1)
    run_weakness(root, jobs1)
    jobs2 = build_round(root, 2)
    second = run_weakness(root, jobs2)
    assert sorted(p.name for p in second["history"].iterdir()) == [
        "weakness_report_001.json", "weakness_report_002.json"]
    out_bytes = second["out"].read_bytes()
    matrix_lines = second["matrix"].read_text().splitlines()
    ledger = yaml.safe_load(second["ledger"].read_text())

    again = run_weakness(root, jobs1, expect_ok=False)   # OLDEST round's command
    assert again["proc"].returncode != 0
    assert "already measured" in again["proc"].stderr
    assert "round 001" in again["proc"].stderr
    # The refused re-measure mints no phantom round and touches nothing.
    assert again["out"].read_bytes() == out_bytes
    assert sorted(p.name for p in again["history"].iterdir()) == [
        "weakness_report_001.json", "weakness_report_002.json"]
    assert again["matrix"].read_text().splitlines() == matrix_lines
    assert yaml.safe_load(again["ledger"].read_text()) == ledger


def test_remeasure_after_history_prune_is_idempotent(tmp_path):
    """The documented re-measure remedy (move the history copy aside) over the
    SAME jobs must complete without duplicating any append: matrix rows and
    the ledger row for that round are recognized and skipped."""
    jobs = build_round(tmp_path, 1)
    first = run_weakness(tmp_path, jobs)
    (first["history"] / "weakness_report_001.json").unlink()

    again = run_weakness(tmp_path, jobs)   # history slot free again
    rows = [json.loads(line) for line in
            again["matrix"].read_text(encoding="utf-8").splitlines()]
    keys = [(r["round"], r["task_id"], r["repeat_idx"]) for r in rows]
    assert len(keys) == len(set(keys)) == 6   # exactly one row per key
    ledger = yaml.safe_load(again["ledger"].read_text(encoding="utf-8"))
    assert [row["round"] for row in ledger] == [1]   # one row per round
    # The history copy is re-created identically (minus generated_at drift).
    assert (again["history"] / "weakness_report_001.json").is_file()


def test_remeasure_with_changed_rewards_refuses_and_writes_nothing(tmp_path):
    """After pruning history, re-measuring the round with DIFFERENT rewards
    must refuse before any write — otherwise the response matrix silently
    holds two conflicting measurements of round N."""
    jobs = build_round(tmp_path, 1)
    first = run_weakness(tmp_path, jobs)
    out_bytes = first["out"].read_bytes()
    matrix_bytes = first["matrix"].read_bytes()
    (first["history"] / "weakness_report_001.json").unlink()

    # Flip one reward: same (round, task, repeat, scaffold) key, new content.
    trial = jobs[0] / f"{TASK_A[:32]}__{hashlib.md5(TASK_A.encode()).hexdigest()[:7]}"
    result_path = trial / "result.json"
    data = json.loads(result_path.read_text(encoding="utf-8"))
    data["verifier_result"]["rewards"]["reward"] = 0
    result_path.write_text(json.dumps(data), encoding="utf-8")

    again = run_weakness(tmp_path, jobs, expect_ok=False)
    assert again["proc"].returncode != 0
    assert "response matrix" in again["proc"].stderr
    assert "prun" in again["proc"].stderr
    # A refused re-measure leaves every artifact intact.
    assert again["out"].read_bytes() == out_bytes
    assert again["matrix"].read_bytes() == matrix_bytes
    assert len(yaml.safe_load(again["ledger"].read_text())) == 1
    assert list(again["history"].iterdir()) == []


# ---------------------------------------------------------------------------
# Signal firewall integration (normative fix 8)
# ---------------------------------------------------------------------------
def test_written_report_passes_signal_firewall(round1, tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "signal_guard.py"),
         "check", "--payload", str(round1["out"])],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_guard_refuses_to_write_dirty_payload(round1):
    dirty = {"schema": "legoflow.weakness.v1",
             "tags": [{"key": "language:python", "diff_hunk_overlap": 0.9}]}
    with pytest.raises(SystemExit) as excinfo:
        weakness.guard_payloads(dirty, [], {})
    assert excinfo.value.code == 3


def test_missing_job_dir_fails_closed(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "build_weakness_report.py"),
         "--jobs", str(tmp_path / "nope"), "--dataset", "x",
         "--audit-slice", str(tmp_path / "s.txt"), "--out", str(tmp_path / "r.json"),
         "--history-dir", str(tmp_path / "h"), "--response-matrix",
         str(tmp_path / "m.jsonl"), "--ledger", str(tmp_path / "l.yaml")],
        capture_output=True, text=True)
    assert proc.returncode != 0
    assert not (tmp_path / "r.json").exists()


# ---------------------------------------------------------------------------
# Checkpoint identity + directives provenance inputs
# ---------------------------------------------------------------------------
def test_receipt_flag_seeds_checkpoint_identity_without_job_copies(tmp_path):
    """The trainer receipt lives next to the served checkpoint, not in the job
    dirs: --receipt must close that hand-off (contract 8) and a round with no
    receipt anywhere must warn instead of failing silently."""
    jobs = build_round(tmp_path, 1)
    for job in jobs:
        receipt_in_job = job / "round_receipt.json"
        receipt_in_job.unlink(missing_ok=True)
    # Trainer-style location: <model run dir>/round_receipt.json.
    receipt = tmp_path / "model" / "run-0001" / "round_receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(
        {"round": 1, "checkpoint_sha256": f"{1:064d}"[::-1]}), encoding="utf-8")

    result = run_weakness(tmp_path, jobs, receipt=receipt)
    report = json.loads(result["out"].read_text(encoding="utf-8"))
    assert report["checkpoint_sha"] == f"{1:064d}"[::-1]
    assert report["checkpoint_ref"].startswith("round1:")
    assert "checkpoint identity is 'unknown'" not in result["proc"].stderr
    rows = [json.loads(line) for line in
            result["matrix"].read_text(encoding="utf-8").splitlines()]
    assert {r["checkpoint_sha"] for r in rows} == {f"{1:064d}"[::-1]}

    # Without any receipt the identity resolves to 'unknown' and WARNS.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    fresh_jobs = build_round(fresh, 1)
    for job in fresh_jobs:
        (job / "round_receipt.json").unlink(missing_ok=True)
    bare = run_weakness(fresh, fresh_jobs)
    assert "checkpoint identity is 'unknown'" in bare["proc"].stderr
    bare_report = json.loads(bare["out"].read_text(encoding="utf-8"))
    assert bare_report["checkpoint_sha"] == "unknown"


def test_prev_directives_flag_stamps_ledger_row(tmp_path):
    jobs = build_round(tmp_path, 1)
    directives = tmp_path / "directives_round_000.yaml"
    directives.write_text("directives: []\n", encoding="utf-8")
    result = run_weakness(tmp_path, jobs, prev_directives=directives)
    ledger = yaml.safe_load(result["ledger"].read_text(encoding="utf-8"))
    assert len(ledger) == 1
    assert ledger[0]["directives_sha256"] == hashlib.sha256(
        directives.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# build_identity_index.py
# ---------------------------------------------------------------------------
def test_identity_index_parses_repo_pr_and_paths(tmp_path):
    gold = write_gold_dataset(tmp_path)
    out = tmp_path / "bench_identity" / "swebench-test.identity.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "build_identity_index.py"),
         "--dataset", "swebench-test", "--dataset-dir", str(gold),
         "--out", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    index = json.loads(out.read_text(encoding="utf-8"))
    assert index["schema"] == "legoflow.bench_identity.v1"
    assert index["dataset"] == "swebench-test"
    entries = {e["task_id"]: e for e in index["entries"]}
    a = entries[TASK_A]
    assert a["repo"] == "django/django"
    assert a["pr_number"] == 13741
    assert a["patch_file_paths"] == ["django/models.py", "django/utils.py"]
    assert a["test_file_paths"] == ["tests/test_django.py"]
    assert a["norm_patch_sha256"]
    # Raw gold patch text ships for decontam_check.py's token-overlap rule
    # (firewall-exempt by design: consumed for exclusion only).
    assert a["patch_text"].startswith("--- a/django/models.py")
    assert "return x + 1" in a["patch_text"]
    d = entries[TASK_D]
    assert d["repo"] == "pvlib/pvlib-python"
    assert d["pr_number"] == 1234
    assert d["norm_patch_sha256"] == hashlib.sha256(b"").hexdigest()
    assert d["patch_text"] == ""


def test_identity_index_owner_repo_id_without_config_repo(tmp_path):
    task_dir = tmp_path / "gold" / "acme__widget-pr-42"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "tests" / "config.json").write_text(
        json.dumps({"instance_id": "acme__widget-pr-42", "patch": ""}),
        encoding="utf-8")
    out = tmp_path / "identity.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "build_identity_index.py"),
         "--dataset", "d", "--dataset-dir", str(tmp_path / "gold"),
         "--out", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    entry = json.loads(out.read_text(encoding="utf-8"))["entries"][0]
    assert entry["repo"] == "acme/widget"
    assert entry["pr_number"] == 42


def test_norm_patch_sha_is_reorder_and_format_stable(tmp_path):
    patch_a = (
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,3 @@\n old\n+new\n"
        "--- a/other.py\n+++ b/other.py\n@@ -5,2 +5,3 @@\n ctx\n+added\n"
    )
    patch_reordered = (
        "--- a/other.py\n+++ b/other.py\n@@ -5,2 +5,3 @@\n ctx\n+added\n"
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,3 @@\n old\n+new\n"
    )
    patch_reformatted = (
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,3 @@\n  old\n+  new\n"
        "--- a/other.py\n+++ b/other.py\n@@ -5,2 +5,3 @@\n  ctx\n+  added\n"
    )
    patch_different = (
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,3 @@\n old\n+COMPLETELY DIFFERENT\n"
        "--- a/other.py\n+++ b/other.py\n@@ -5,2 +5,3 @@\n ctx\n+added\n"
    )
    sha = _load("build_identity_index").norm_patch_sha256
    assert sha(patch_a) == sha(patch_reordered)
    assert sha(patch_a) == sha(patch_reformatted)   # identifier normalization
    assert sha(patch_a) != sha(patch_different)
    assert sha("") == hashlib.sha256(b"").hexdigest()


def test_identity_index_fails_closed_on_missing_or_empty_dir(tmp_path):
    out = tmp_path / "identity.json"
    missing = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "build_identity_index.py"),
         "--dataset", "d", "--dataset-dir", str(tmp_path / "nope"),
         "--out", str(out)], capture_output=True, text=True)
    assert missing.returncode == 1
    empty = tmp_path / "gold"
    empty.mkdir()
    empty_run = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "build_identity_index.py"),
         "--dataset", "d", "--dataset-dir", str(empty), "--out", str(out)],
        capture_output=True, text=True)
    assert empty_run.returncode == 1
    assert not out.exists()


# ---------------------------------------------------------------------------
# make_audit_slice.py
# ---------------------------------------------------------------------------
def write_slice_population(root: Path, counts: dict) -> Path:
    dataset_dir = root / "gold" / "pop"
    for language, n in counts.items():
        for i in range(n):
            task_dir = dataset_dir / f"{language}__{language}-{i:05d}"
            task_dir.mkdir(parents=True)
            (task_dir / "task.toml").write_text(
                '[metadata]\ntags = ["%s", "backend", "misc", "bug"]\n' % language,
                encoding="utf-8")
    return dataset_dir


def test_audit_slice_is_deterministic(tmp_path):
    dataset_dir = write_slice_population(tmp_path, {"python": 7, "go": 3})
    outs = []
    for run in (1, 2):
        out = tmp_path / f"slice{run}.txt"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "make_audit_slice.py"),
             "--dataset-dir", str(dataset_dir), "--n", "4", "--seed", "1",
             "--strata", "language", "--out", str(out),
             "--meta", str(tmp_path / f"slice{run}.meta.json")],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        outs.append(out.read_text(encoding="utf-8"))
    assert outs[0] == outs[1]
    ids = outs[0].split()
    assert len(ids) == 4
    assert set(ids) <= {p.name for p in dataset_dir.iterdir()}


def test_audit_slice_meta_records_proportional_allocation(tmp_path):
    dataset_dir = write_slice_population(tmp_path, {"python": 8, "go": 2})
    meta_path = tmp_path / "meta.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "make_audit_slice.py"),
         "--dataset-dir", str(dataset_dir), "--n", "5", "--seed", "3",
         "--out", str(tmp_path / "slice.txt"), "--meta", str(meta_path)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["seed"] == 3
    assert meta["strata"] == "language"
    assert sum(meta["sample_sizes"].values()) == 5
    assert meta["sample_sizes"]["python"] == 4  # 8/10 of 5, largest remainder
    assert meta["sample_sizes"]["go"] == 1
    assert meta["created_from"].endswith("pop")
    # n and the population hash pin the meta's serialization contract.
    assert meta["n"] == 5
    assert meta["population_size"] == 10
    assert meta["population_sha256"] == hashlib.sha256(
        "\n".join(sorted(p.name for p in dataset_dir.iterdir()))
        .encode("utf-8")).hexdigest()


def test_audit_slice_meta_version_refuses_silent_change(tmp_path):
    """meta records slice_version; replacing a meta that carries a different
    version is refused without --rotate (a silent version change would make
    same-version comparisons across rounds meaningless)."""
    dataset_dir = write_slice_population(tmp_path, {"python": 4})
    meta_path = tmp_path / "meta.json"
    base = [sys.executable, str(SCRIPTS_DIR / "make_audit_slice.py"),
            "--dataset-dir", str(dataset_dir), "--n", "2", "--seed", "1",
            "--out", str(tmp_path / "slice.txt"), "--meta", str(meta_path)]
    first = subprocess.run(base, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert json.loads(meta_path.read_text(encoding="utf-8"))["slice_version"] == 1

    bumped = subprocess.run(base + ["--slice-version", "2"],
                            capture_output=True, text=True)
    assert bumped.returncode == 1
    assert "slice_version" in bumped.stderr
    assert json.loads(meta_path.read_text(encoding="utf-8"))["slice_version"] == 1

    rotated = subprocess.run(base + ["--slice-version", "2", "--rotate"],
                             capture_output=True, text=True)
    assert rotated.returncode == 0, rotated.stderr
    assert json.loads(meta_path.read_text(encoding="utf-8"))["slice_version"] == 2


def test_audit_slice_refuses_n_above_population(tmp_path):
    dataset_dir = write_slice_population(tmp_path, {"python": 3, "go": 2})
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "make_audit_slice.py"),
         "--dataset-dir", str(dataset_dir), "--n", "6", "--seed", "1",
         "--out", str(tmp_path / "slice.txt")], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "exceeds" in proc.stderr
    assert not (tmp_path / "slice.txt").exists()


def test_audit_slice_different_seeds_differ(tmp_path):
    dataset_dir = write_slice_population(tmp_path, {"python": 30, "go": 20})
    slices = []
    for seed in (1, 2):
        out = tmp_path / f"slice{seed}.txt"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "make_audit_slice.py"),
             "--dataset-dir", str(dataset_dir), "--n", "10", "--seed", str(seed),
             "--out", str(out)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        slices.append(out.read_text(encoding="utf-8"))
    assert slices[0] != slices[1]


def test_audit_slice_emits_frozen_harbor_registry(tmp_path):
    """--emit-registry writes the frozen `legoflow-audit` Harbor registry over
    the slice's task dirs, mirroring terminal-curator's registry.json schema:
    a one-entry list of {name, version, tasks: [{name, path: absolute}]} whose
    tasks are exactly the slice."""
    dataset_dir = write_slice_population(tmp_path, {"python": 6, "go": 4})
    registry_path = tmp_path / "reserve" / "audit_registry.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "make_audit_slice.py"),
         "--dataset-dir", str(dataset_dir), "--n", "5", "--seed", "1",
         "--out", str(tmp_path / "slice.txt"), "--meta", str(tmp_path / "meta.json"),
         "--emit-registry", str(registry_path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    slice_ids = (tmp_path / "slice.txt").read_text(encoding="utf-8").split()
    assert len(slice_ids) == 5

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert isinstance(registry, list) and len(registry) == 1
    entry = registry[0]
    assert set(entry) == {"name", "version", "description", "tasks"}
    assert entry["name"] == "legoflow-audit"
    assert entry["version"] == "1.0"
    # The registry lists exactly the slice, in slice order, over existing dirs.
    assert [t["name"] for t in entry["tasks"]] == slice_ids
    for task in entry["tasks"]:
        assert set(task) == {"name", "path"}
        assert Path(task["path"]).is_absolute()
        assert Path(task["path"]) == (dataset_dir / task["name"]).resolve()
        assert Path(task["path"]).is_dir()

    # --registry-name selects the entry name (the loop's frozen dataset id).
    renamed = tmp_path / "reserve" / "audit_registry_renamed.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "make_audit_slice.py"),
         "--dataset-dir", str(dataset_dir), "--n", "5", "--seed", "1",
         "--out", str(tmp_path / "slice2.txt"),
         "--emit-registry", str(renamed), "--registry-name", "legoflow-audit-2"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(renamed.read_text(encoding="utf-8"))[0]["name"] == \
        "legoflow-audit-2"


def test_audit_slice_registry_refuses_missing_task_dir(tmp_path, monkeypatch, capsys):
    """--emit-registry fails closed when a sampled task dir is missing: a
    registry entry pointing at a nonexistent path would silently shrink the
    promotion readout, and nothing may be half-published."""
    dataset_dir = write_slice_population(tmp_path, {"python": 4})
    ids = sorted(p.name for p in dataset_dir.iterdir())[:2]
    shutil.rmtree(dataset_dir / ids[0])   # vanished after the draw
    module = _load("make_audit_slice")
    monkeypatch.setattr(module, "stratified_sample",
                        lambda population, n, seed: (ids, {"python": 2}))
    out = tmp_path / "slice.txt"
    registry_path = tmp_path / "reserve" / "audit_registry.json"
    rc = module.main(["--dataset-dir", str(dataset_dir), "--n", "2", "--seed", "1",
                      "--out", str(out), "--emit-registry", str(registry_path)])
    assert rc == 1
    assert "missing" in capsys.readouterr().err
    assert not out.exists()
    assert not registry_path.exists()


# ---------------------------------------------------------------------------
# prepare_dataset.sh — identity index tracks the dataset (plan §9)
# ---------------------------------------------------------------------------
def test_prepare_dataset_wires_identity_index_rebuild():
    """prepare_dataset.sh must rebuild artifacts/bench_identity/<base>.identity.json
    after generating/tagging the gold dataset, so curator-side decontamination
    cannot silently run against a stale index."""
    script = SCRIPTS_DIR / "prepare_dataset.sh"
    text = script.read_text(encoding="utf-8")
    assert "build_identity_index.py" in text
    assert "bench_identity" in text
    proc = subprocess.run(["bash", "-n", str(script)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
