"""Scaffold-disagreement proposals for one multi-scaffold tracer campaign.

Covers the four solve bands (saturated / flaky / reaudit / frontier), scaffold
identity resolution (config.json > job name > --scaffold), the advice-only
contract (exit 0, pure post-processing), deterministic output, and that the
proposal files feed the existing exclusion namespace.

No network, no Docker: fixtures are synthetic Harbor job directories.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "blocks" / "tracer" / "scripts" / "scaffold_disagreement.py"
RESOLVER = REPO_ROOT / "blocks" / "tracer" / "scripts" / "resolve_exclude_tasks.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_trial(job_dir: Path, trial_dir: str, task: str, reward, *, agent: str = "claude-code", exception: str | None = None) -> None:
    """One Harbor `<trial-dir>/result.json`; trial dirs deliberately do NOT encode the task id."""
    trial = job_dir / trial_dir
    trial.mkdir(parents=True, exist_ok=True)
    data = {
        "task_name": task,
        "trial_name": f"{task}-t",
        "verifier_result": ({"rewards": {"reward": reward}} if reward is not None else None),
        "agent_info": {"name": agent, "version": "1.0", "model_info": None},
        "started_at": "2026-10-30T00:00:00",
        "finished_at": "2026-10-30T00:01:00",
    }
    if exception:
        data["exception_info"] = {"type": exception, "message": "boom"}
    (trial / "result.json").write_text(json.dumps(data), encoding="utf-8")


def write_job_config(job_dir: Path, agent_name: str | None, import_path: str | None = None) -> None:
    agent = {"name": agent_name}
    if import_path:
        agent["import_path"] = import_path
    (job_dir / "config.json").write_text(
        json.dumps({"job_name": job_dir.name, "agents": [agent]}), encoding="utf-8"
    )


@pytest.fixture
def campaign(tmp_path: Path) -> dict[str, Path]:
    """Two scaffold jobs over the same task batch, one row per documented case."""
    jobs = tmp_path / "jobs"
    # Scaffold identity provenance differs on purpose: this one is named via
    # its Harbor config.json (no scaffold hint in the directory name).
    claude = jobs / "job-a"
    claude.mkdir(parents=True)
    write_job_config(claude, "claude-code")
    # This one carries no config.json -> falls back to the job name pattern.
    openhands = jobs / "custom-openhands-sdk-0.1-qwen3-20260101"
    openhands.mkdir(parents=True)

    write_trial(claude, "a1f" * 10 + "01", "t-sat", 1.0, agent="openhands-sdk")  # mismatch warn only
    write_trial(openhands, "b2e" * 10 + "01", "t-sat", 1.0, agent="openhands-sdk")

    write_trial(claude, "a1f" * 10 + "02", "t-reaudit", 0.0)
    write_trial(openhands, "b2e" * 10 + "02", "t-reaudit", 0.0, agent="openhands-sdk")

    write_trial(claude, "a1f" * 10 + "03", "t-frontier", 1.0)
    write_trial(openhands, "b2e" * 10 + "03", "t-frontier", 0.0, agent="openhands-sdk")

    write_trial(claude, "a1f" * 10 + "04", "t-flaky", 1.0)
    write_trial(openhands, "b2e" * 10 + "04", "t-flaky", 0.0, agent="openhands-sdk", exception="AgentTimeoutError")

    write_trial(claude, "a1f" * 10 + "05", "t-partial", 1.0)  # only one scaffold ran it
    # One scaffold, verified failure: partial coverage is missing data — it
    # must NOT become reaudit evidence (§5.4 A5 wants f==0 across ALL scaffolds).
    write_trial(claude, "a1f" * 10 + "10", "t-partial-fail", 0.0)

    # Both trials crashed before verification: missing data, not "too hard".
    write_trial(claude, "a1f" * 10 + "06", "t-unverified", None)
    write_trial(openhands, "b2e" * 10 + "06", "t-unverified", None, agent="openhands-sdk")

    # Two samples under one scaffold: per-scaffold outcome is the mean (0.5),
    # which clears the default 0.5 solve threshold.
    write_trial(claude, "a1f" * 10 + "07", "t-mean", 1.0)
    write_trial(claude, "a1f" * 10 + "08", "t-mean", 0.0)
    write_trial(openhands, "b2e" * 10 + "07", "t-mean", 1.0, agent="openhands-sdk")

    # Mixed with an exception but low outcome variance -> not flaky.
    write_trial(claude, "a1f" * 10 + "09", "t-lowvar", 1.0, exception="Veriftimeout")
    write_trial(openhands, "b2e" * 10 + "09", "t-lowvar", 0.4, agent="openhands-sdk")

    return {"root": tmp_path, "jobs": jobs, "claude": claude, "openhands": openhands}


def run(*argv: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv], capture_output=True, text=True
    )


def read_report(out_dir: Path) -> dict:
    return json.loads((out_dir / "disagreement.json").read_text(encoding="utf-8"))


def flags_of(report: dict) -> dict[str, str]:
    return {row["task_id"]: row["flag"] for row in report["tasks"]}


def proposal_ids(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_four_flags_and_proposal_files(campaign):
    out = campaign["root"] / "out"
    result = run(
        "--jobs", str(campaign["claude"]), str(campaign["openhands"]),
        "--campaign", "smoke-campaign", "--out-dir", str(out),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    report = read_report(out)
    assert report["schema"] == "legoflow.scaffold_disagreement.v1"
    assert report["campaign"] == "smoke-campaign"
    assert report["advice_only"] is True
    assert report["scaffolds"] == ["claude_code", "openhands_sdk"]
    # Identity provenance: config.json first, job name as the fallback.
    sources = {job["scaffold"]: job["scaffold_source"] for job in report["jobs"]}
    assert sources == {"claude_code": "config.json", "openhands_sdk": "job-name"}

    assert flags_of(report) == {
        "t-sat": "saturated",
        "t-mean": "saturated",
        "t-reaudit": "reaudit",
        "t-flaky": "flaky",
        "t-frontier": "frontier",
        "t-partial": "frontier",
        "t-partial-fail": "frontier",
        "t-unverified": "frontier",
        "t-lowvar": "frontier",
    }
    assert report["summary"]["flags"] == {
        "saturated": 2, "flaky": 1, "reaudit": 1, "frontier": 5,
    }
    assert report["summary"]["n_tasks"] == 9
    assert report["summary"]["partial_coverage_tasks"] == 2
    assert report["summary"]["unverified_tasks"] == 1

    # Trial dirs do not encode task ids: the join key really is task_name.
    row = next(r for r in report["tasks"] if r["task_id"] == "t-sat")
    assert row["outcomes"] == {"claude_code": 1.0, "openhands_sdk": 1.0}
    assert row["solve_fraction"] == 1.0

    mean_row = next(r for r in report["tasks"] if r["task_id"] == "t-mean")
    assert mean_row["outcomes"]["claude_code"] == 0.5
    assert mean_row["trials_per_scaffold"] == {"claude_code": 2, "openhands_sdk": 1}

    flaky_row = next(r for r in report["tasks"] if r["task_id"] == "t-flaky")
    assert flaky_row["exception_scaffolds"] == ["openhands_sdk"]
    assert flaky_row["outcome_sd"] >= 0.35
    assert flaky_row["cross_scaffold_agreement"] == 0.0

    unverified = next(r for r in report["tasks"] if r["task_id"] == "t-unverified")
    assert unverified["missing_reward_trials"] == 2
    assert unverified["verified_trials"] == 0

    partial = next(r for r in report["tasks"] if r["task_id"] == "t-partial")
    assert partial["n_scaffolds"] == 1  # never saturated on one scaffold
    assert partial["n_campaign_scaffolds"] == 2

    assert proposal_ids(out / "saturation_proposals.txt") == ["t-mean", "t-sat"]
    assert proposal_ids(out / "flaky_proposals.txt") == ["t-flaky"]
    assert proposal_ids(out / "reaudit_proposals.txt") == ["t-reaudit"]
    # The partial-coverage verified failure stays out of every proposal file.
    assert "t-partial-fail" not in proposal_ids(out / "reaudit_proposals.txt")
    assert "t-partial-fail" not in proposal_ids(out / "saturation_proposals.txt")
    assert "t-partial-fail" not in proposal_ids(out / "flaky_proposals.txt")
    for name in ("saturation", "flaky", "reaudit"):
        header = (out / f"{name}_proposals.txt").read_text(encoding="utf-8").splitlines()
        assert header[0].startswith("#"), "proposal files carry a header comment"

    # The per-trial agent name mismatch is a warning only; grouping is by job.
    assert "WARN" in result.stderr and "openhands_sdk" in result.stderr


def test_deterministic_output_and_zero_extra_rollouts(campaign):
    before = snapshot(campaign["jobs"])
    out1 = campaign["root"] / "out1"
    out2 = campaign["root"] / "out2"
    common = ("--jobs", str(campaign["claude"]), str(campaign["openhands"]))
    first = run(*common, "--out-dir", str(out1))
    second = run(*common, "--out-dir", str(out2))
    assert first.returncode == 0 and second.returncode == 0

    for name in ("disagreement.json", "trial_index.jsonl", "saturation_proposals.txt", "flaky_proposals.txt", "reaudit_proposals.txt"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), name
    # Pure post-processing: the campaign's own bytes are untouched.
    assert snapshot(campaign["jobs"]) == before


def test_proposals_feed_the_exclusion_namespace(campaign):
    out = campaign["root"] / "out"
    assert run("--jobs", str(campaign["claude"]), str(campaign["openhands"]), "--out-dir", str(out)).returncode == 0
    resolver = load_module("resolver_excl", RESOLVER)
    assert resolver.text_ids(out / "flaky_proposals.txt") == {"t-flaky"}
    assert resolver.text_ids(out / "saturation_proposals.txt") == {"t-mean", "t-sat"}


def test_scaffold_override_pairing_and_fail_closed(tmp_path):
    jobs = tmp_path / "jobs"
    left, right = jobs / "job-x", jobs / "job-y"  # no config.json, no name hint
    for job, task, reward in ((left, "t", 1.0), (right, "t", 0.0)):
        job.mkdir(parents=True)
        write_trial(job, "abc" * 10, task, reward)

    # Unresolvable identity is an input error, not a silent "unknown" column.
    missing = run("--jobs", str(left), str(right), "--out-dir", str(tmp_path / "out"))
    assert missing.returncode != 0
    assert "--scaffold" in missing.stderr

    paired = run(
        "--scaffold", "claude-code", "--jobs", str(left),
        "--scaffold", "openhands-sdk", "--jobs", str(right),
        "--out-dir", str(tmp_path / "out"),
    )
    assert paired.returncode == 0, paired.stderr
    report = read_report(tmp_path / "out")
    assert report["scaffolds"] == ["claude_code", "openhands_sdk"]
    # Mixed with high variance and total disagreement, but NO exception:
    # frontier, not flaky.
    assert flags_of(report) == {"t": "frontier"}
    assert report["jobs"][0]["scaffold_source"] == "--scaffold"

    # Unbalanced pairing is a usage error.
    bad = run("--scaffold", "claude-code", "--jobs", str(left), "--jobs", str(right), "--out-dir", str(tmp_path / "o2"))
    assert bad.returncode == 2


def test_single_scaffold_campaign_is_refused(campaign):
    result = run("--jobs", str(campaign["claude"]), "--out-dir", str(campaign["root"] / "out"))
    assert result.returncode != 0
    assert "2 distinct scaffolds" in result.stderr


def test_missing_job_dir_is_an_input_error(tmp_path):
    result = run("--jobs", str(tmp_path / "nope"), "--out-dir", str(tmp_path / "out"))
    assert result.returncode != 0
    assert "not found" in result.stderr


def test_empty_campaign_directory_is_an_input_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run("--jobs", str(empty), "--out-dir", str(tmp_path / "out")).returncode != 0


def test_solve_threshold_moves_the_band(campaign):
    out = campaign["root"] / "strict"
    result = run(
        "--jobs", str(campaign["claude"]), str(campaign["openhands"]),
        "--solve-threshold", "0.9", "--out-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    flags = flags_of(read_report(out))
    assert flags["t-mean"] == "frontier"  # 0.5 mean no longer counts as solved
    assert flags["t-sat"] == "saturated"
    assert read_report(out)["thresholds"]["solve_threshold"] == 0.9


def test_default_campaign_name_joins_scaffolds(campaign):
    out = campaign["root"] / "named"
    assert run("--jobs", str(campaign["claude"]), str(campaign["openhands"]), "--out-dir", str(out)).returncode == 0
    assert read_report(out)["campaign"] == "claude_code-openhands_sdk"


def test_classify_helpers():
    module = load_module("scaffold_disagreement", SCRIPT)
    assert module.population_sd([1.0, 0.0]) == 0.5
    assert module.population_sd([1.0, 1.0]) == 0.0
    assert module.cross_scaffold_agreement([True, False]) == 0.0
    assert module.cross_scaffold_agreement([True, True, False]) == round(1 / 3, 6)
    assert module.cross_scaffold_agreement([True]) is None
    thresholds = {"solve_threshold": 0.5, "flaky_min_sd": 0.35, "flaky_max_agreement": 0.5}
    # All four bands are reachable purely from the row's own counters.
    assert module.classify(
        {"solve_fraction": 1.0, "n_scaffolds": 2, "n_campaign_scaffolds": 2, "verified_trials": 2},
        thresholds,
    ) == "saturated"
    assert module.classify(
        {"solve_fraction": 0.0, "n_scaffolds": 2, "n_campaign_scaffolds": 2, "verified_trials": 1},
        thresholds,
    ) == "reaudit"
    flaky_row = {
        "solve_fraction": 0.5, "n_scaffolds": 2, "n_campaign_scaffolds": 2, "verified_trials": 2,
        "outcome_sd": 0.5, "cross_scaffold_agreement": 0.0,
        "exception_scaffolds": ["openhands_sdk"],
    }
    assert module.classify(flaky_row, thresholds) == "flaky"
    no_exception = dict(flaky_row, exception_scaffolds=[])
    assert module.classify(no_exception, thresholds) == "frontier"
    low_variance = dict(flaky_row, outcome_sd=0.2)
    assert module.classify(low_variance, thresholds) == "frontier"
    # Partial campaign coverage is missing data: no action flag may fire on
    # it, whatever the one attempted scaffold observed.
    partial = {"n_scaffolds": 1, "n_campaign_scaffolds": 2, "verified_trials": 1}
    assert module.classify({**flaky_row, **partial}, thresholds) == "frontier"
    assert module.classify(dict(partial, solve_fraction=1.0), thresholds) == "frontier"
    assert module.classify(dict(partial, solve_fraction=0.0), thresholds) == "frontier"


def test_trial_index_persisted_per_campaign(campaign):
    out = campaign["root"] / "out"
    result = run(
        "--jobs", str(campaign["claude"]), str(campaign["openhands"]),
        "--campaign", "smoke-campaign", "--out-dir", str(out),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    report = read_report(out)
    lines = (out / "trial_index.jsonl").read_text(encoding="utf-8").splitlines()
    # One row per collected trial — the second half of the M2 seed (§6.2).
    assert len(lines) == sum(row["n_trials"] for row in report["tasks"])
    rows = [json.loads(line) for line in lines]
    assert all(row["schema"] == "legoflow.trial_index.v1" for row in rows)
    assert all(row["campaign"] == "smoke-campaign" for row in rows)
    task_ids = [row["task_id"] for row in rows]
    assert task_ids == sorted(task_ids)  # sorted, hence deterministic, order

    # Every row carries scaffold + job provenance (sample jobs of one scaffold).
    assert {(row["scaffold"], row["job"]) for row in rows} == {
        ("claude_code", str(campaign["claude"])),
        ("openhands_sdk", str(campaign["openhands"])),
    }
    # A fixture trial round-trips: reward / exception / task / trial name.
    flaky = next(r for r in rows if r["task_id"] == "t-flaky" and r["scaffold"] == "openhands_sdk")
    assert flaky["reward"] == 0.0 and flaky["exception"] is True
    assert flaky["trial_index"] == "t-flaky-t"
    unverified = next(r for r in rows if r["task_id"] == "t-unverified")
    assert unverified["reward"] is None and unverified["exception"] is False
