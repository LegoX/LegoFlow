"""Curator targeting gate (RSI_LOOP_PLAN.md §5.4 A3, §5.7, §9) — unit tests.

Covers blocks/curator/scripts/apply_targeting.py: supply-check caps, the
noise gate, learning-progress ranking, quota slew clamping, deterministic
holdout sampling, pre-registration refusal semantics, open-verdict refusal,
verdict closure (two-part + supply-limited-null) and futility freezing.

No network, no Docker; everything runs on tmp_path fixtures.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "blocks" / "curator" / "scripts" / "apply_targeting.py"


def load_module(name: str = "apply_targeting"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AT = load_module()


# --- fixtures -----------------------------------------------------------------


def write_task(pool: Path, lang_dir: str, task_id: str, tags: list[str],
               score: float | None = None, owner: str = "", repo: str = "") -> None:
    task = pool / lang_dir / task_id
    task.mkdir(parents=True, exist_ok=True)
    lines = [
        'schema_version = "1.1"',
        "",
        "[metadata]",
        'author_name = "test"',
        'category = "debugging"',
        f"tags = [{', '.join(json.dumps(t) for t in tags)}]",
        'difficulty = "unknown"',
    ]
    if owner and repo:
        lines += ["", "[metadata.extra]", f'user = "{owner}"', f'repo = "{repo}"']
    lines += [""]
    if score is not None:
        lines += ["[scoring]", f"difficulty_score = {score}", ""]
    (task / "task.toml").write_text("\n".join(lines), encoding="utf-8")


def make_pool(root: Path) -> Path:
    """3-language pool: py (6), go (4), rust (2) verified tasks."""
    pool = root / "swe_tasks"
    for task_id in [f"acme__pyproj-{n}" for n in range(100, 106)]:
        write_task(pool, "py-cc", task_id, ["Python", "backend", "flask", "wrong-default"], 5.5)
    for task_id in [f"acme__goproj-{n}" for n in range(200, 204)]:
        write_task(pool, "go-cc", task_id, ["Go", "cli", "kubernetes", "missing-fallback"], 8.0)
    for task_id in [f"acme__rustproj-{n}" for n in range(300, 302)]:
        write_task(pool, "rust-cc", task_id, ["Rust", "library", "serde", "incomplete-validation"],
                   2.0)
    for lang in ("py", "go", "rust"):
        ids = sorted(p.name for p in (pool / f"{lang}-cc").iterdir() if p.is_dir())
        (pool / f"{lang}-cc" / "verifiable_tasks.txt").write_text(
            "\n".join(ids) + "\n", encoding="utf-8")
    return pool


def tag(key: str, n: int, pass_rate: float, delta: float | None, lp: float | None) -> dict:
    return {"key": key, "n": n, "resolved": int(n * pass_rate), "pass_rate": pass_rate,
            "delta_vs_prev": delta, "learning_progress": lp, "failure_mix": {}}


def make_report(path: Path, tags: list[dict], generated_at: str = "2026-10-30T00:00:00Z",
                noise: dict | None = None, tasks: dict | None = None) -> Path:
    report = {
        "schema": "legoflow.weakness.v1",
        "dataset": "swebench-verified",
        "checkpoint_ref": "ckpt-r0",
        "eval_jobs": ["job-r1", "job-r2", "job-r3"],
        "generated_at": generated_at,
        "k": 3,
        "noise": noise or {"k": 3, "between_repeat_sd": 0.02,
                           "per_tag_sd": {t["key"]: 0.01 for t in tags}},
        "audit_excluded_ids_sha256": "0" * 64,
        "tagger_version": AT.EXPECTED_TAGGER_VERSION,
        "tags": tags,
        "tasks": tasks if tasks is not None else {
            f"task-{i}": {"reward_mean": rate, "per_repeat": [rate] * 3, "exceptions": 0}
            for i, rate in enumerate([0.6, 0.5, 0.5, 0.4, 0.5, 0.5, 0.4, 0.5])},
        "directives_blocked": [],
    }
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


DEFAULT_TAGS = [
    # weak, under-supplied (4 in pool), clears the 2 x max(0.01, 0.02) = 0.04 gate
    tag("language:go", 40, 0.25, -0.08, 0.05),
    # weak, best learning progress -> ranks first
    tag("area:cli", 50, 0.30, -0.06, 0.09),
    # weak but n below the floor
    tag("language:js", 10, 0.20, -0.09, 0.04),
    # sub-noise delta (|0.001| < 0.04)
    tag("language:rust", 30, 0.28, -0.001, 0.01),
    # not weak (0.90 >= report mean 0.5)
    tag("language:TypeScript", 60, 0.90, -0.30, 0.10),
]
SUPPLIED_TAGS = DEFAULT_TAGS + [tag("language:py", 60, 0.31, -0.07, 0.03)]


@pytest.fixture
def workspace(tmp_path):
    pool = make_pool(tmp_path / "pool-root")
    report = make_report(tmp_path / "weakness_report.json", DEFAULT_TAGS)
    return {
        "root": tmp_path,
        "pool": pool,
        "report": report,
        "ledger": tmp_path / "targeting" / "hypothesis_ledger.yaml",
        "out_dir": tmp_path / "targeting",
    }


def run_targeting(workspace, *extra, report=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT),
         "--report", str(report or workspace["report"]),
         "--pool-root", str(workspace["pool"]),
         "--ledger", str(workspace["ledger"]),
         "--out-dir", str(workspace["out_dir"]),
         *extra],
        capture_output=True, text=True)


# --- supply check ----------------------------------------------------------------


def gate_blocks(ledger_path: Path) -> dict[str, dict]:
    """Blocked-tag decisions of the NEWEST round in the ledger."""
    row = yaml.safe_load(ledger_path.read_text())[-1]
    return {entry["key"]: entry for entry in row["gate_blocked"]}


def null_round_blocks(result) -> dict[str, dict]:
    """Blocked-tag decisions of a null round (no ledger row written): the gate
    prints the decisions as a trailing JSON object instead."""
    payload = json.loads(result.stdout[result.stdout.index("{"):])
    return {entry["key"]: entry for entry in payload["blocked"]}


def test_weak_but_supplied_tag_is_capped_at_baseline(workspace):
    ws = workspace
    make_report(ws["report"], SUPPLIED_TAGS)
    result = run_targeting(ws, "--supply-satisfaction-threshold", "6",
                           "--min-tag-n", "20")
    assert result.returncode == 0, result.stdout + result.stderr
    blocked = gate_blocks(ws["ledger"])
    assert blocked["language:py"]["reason"] == "already_supplied"
    assert blocked["language:py"]["supply"] == 6
    ledger = yaml.safe_load(ws["ledger"].read_text())
    targets = ledger[0]["hypothesis"]["target_tags"]
    assert "language:py" not in targets


def test_supplied_language_gets_no_quota_of_its_own(workspace):
    ws = workspace
    # The only weak tag belongs to a language this pool already supplies (6 py tasks).
    make_report(ws["report"], [tag("language:py", 60, 0.31, -0.07, 0.03)])
    result = run_targeting(ws, "--supply-satisfaction-threshold", "6")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no directives emitted" in result.stdout or not ws["ledger"].exists()


# --- noise gate --------------------------------------------------------------------


def test_subnoise_delta_is_blocked(workspace):
    result = run_targeting(workspace, "--min-tag-n", "20")
    assert result.returncode == 0, result.stdout + result.stderr
    blocked = gate_blocks(workspace["ledger"])
    assert blocked["language:rust"]["reason"] == "below_noise_gate"
    assert blocked["language:rust"]["delta"] == pytest.approx(-0.001)
    # 2 x max(tag sd 0.01, global between-repeat sd 0.02, 1/n resolution 1/30):
    # the one-instance floor keeps the threshold nonzero even at zero sd.
    assert blocked["language:rust"]["threshold"] == pytest.approx(2.0 / 30)


def test_noise_gate_reads_per_tag_and_global_sd():
    report = {"noise": {"per_tag_sd": {"language:go": 0.07}}, "tags": [{"key": "language:go"}]}
    assert AT.per_tag_sd(report)["language:go"] == pytest.approx(0.07)
    assert AT.global_sd({"noise": {"between_repeat_sd": 0.05}}) == pytest.approx(0.05)
    assert AT.per_tag_sd({"noise": {"per_tag_sd": [{"key": "a", "sd": 0.03}]}, "tags": []}
                         )["a"] == pytest.approx(0.03)


ZERO_NOISE = {"k": 1, "between_repeat_sd": 0.0,
              "per_tag_sd": {"language:go": 0.0, "language:rust": 0.0}}


def test_zero_sd_report_still_blocks_zero_and_sub_resolution_deltas(workspace):
    """§5.4 A2 with a k=1 / all-identical-repeats report: the measured spread is
    0, but the 1/n one-instance floor keeps the gate threshold nonzero, so a
    0.0 delta and any sub-1/n wiggle are below_noise_gate, never auto-cleared."""
    make_report(workspace["report"], [
        tag("language:go", 40, 0.25, 0.0, 0.0),      # delta exactly 0.0
        tag("language:rust", 30, 0.28, -0.01, 0.01),  # sub-1/n wiggle
    ], noise=ZERO_NOISE)
    result = run_targeting(workspace, "--min-tag-n", "20")
    assert result.returncode == 0, result.stdout + result.stderr
    blocked = null_round_blocks(result)
    assert blocked["language:go"]["reason"] == "below_noise_gate"
    assert blocked["language:go"]["delta"] == 0.0
    assert blocked["language:go"]["threshold"] == pytest.approx(2.0 / 40)  # 2 x 1/n
    assert blocked["language:rust"]["reason"] == "below_noise_gate"
    assert blocked["language:rust"]["threshold"] == pytest.approx(2.0 / 30)
    assert not workspace["ledger"].exists()  # nothing cleared the gate -> no writes


def test_noise_gate_uses_stored_spread_from_prior_rounds(workspace, tmp_path):
    """§2.1 three-operand floor: the stored_identical_config_spread from prior
    rounds' hypotheses gates a quiet report even when its own measured sd is 0."""
    rows = [{
        "round": 1,
        "created_at": "2026-10-29T00:00:00Z",
        "source_report_sha256": "r1",
        "source_report_path": str(workspace["report"]),
        "report_generated_at": "2026-10-29T00:00:00Z",
        "hypothesis": {"target_tags": ["language:go"],
                       "actuation": {"holdout_tags": []},
                       "noise_floor": {"multiplier": 2.0,
                                       "global_between_repeat_sd": 0.02,
                                       "per_tag_sd": {"language:go": 0.12}}},
        "gate_blocked": [],
        "directives_file": "directives_round_001.yaml",
        "applied_config_diff": None,
        "verdict": {"intended_effect": {"tags": {"language:go": {"moved_expected": True}},
                                        "moved": True}},
    }]
    ledger = tmp_path / "ledger-hist.yaml"
    ledger.write_text(yaml.safe_dump(rows), encoding="utf-8")
    make_report(workspace["report"], [
        tag("language:go", 40, 0.25, 0.05, 0.05),  # quiet: own sd 0, delta 0.05
    ], noise={"k": 1, "between_repeat_sd": 0.02, "per_tag_sd": {"language:go": 0.0}})
    ws = {**workspace, "ledger": ledger, "out_dir": tmp_path / "out"}
    result = run_targeting(ws, "--round", "2")
    assert result.returncode == 0, result.stdout + result.stderr
    blocked = null_round_blocks(result)
    assert blocked["language:go"]["reason"] == "below_noise_gate"
    # 2 x max(0.0 own sd, 0.02 global, 0.12 stored, 1/40 resolution) = 0.24
    assert blocked["language:go"]["threshold"] == pytest.approx(0.24)


def test_small_n_tag_is_blocked(workspace):
    assert run_targeting(workspace).returncode == 0
    assert gate_blocks(workspace["ledger"])["language:js"]["reason"] == "n_too_small"


# --- ranking / allocation -------------------------------------------------------------


def test_targets_ranked_by_learning_progress(workspace):
    assert run_targeting(workspace).returncode == 0
    ledger = yaml.safe_load(workspace["ledger"].read_text())
    targets = ledger[0]["hypothesis"]["target_tags"]
    assert targets.index("area:cli") < targets.index("language:go")  # lp 0.09 > 0.05


def test_language_and_cross_language_tags_map_to_quota_deltas(workspace):
    result = run_targeting(workspace, "--budget", "16")
    assert result.returncode == 0, result.stdout + result.stderr
    ledger = yaml.safe_load(workspace["ledger"].read_text())
    quotas = ledger[0]["hypothesis"]["actuation"]["language_quotas"]
    assert quotas["go"]["delta"] > 0            # language:go targets go directly
    assert quotas["go"]["delta"] >= quotas["py"]["delta"]
    assert quotas["go"]["new_value"] == quotas["go"]["baseline"] + quotas["go"]["delta"]


def test_slew_limit_clamps_quota_deltas():
    quotas, unallocated = AT.apply_slew_limit({"py": 999, "go": 3}, {"py": 10, "go": 50}, 0.2)
    assert quotas["py"]["delta"] == 2            # 20% of baseline 10
    assert quotas["py"]["clamped"] is True
    assert quotas["go"]["delta"] == 3            # within 20% of baseline 50
    assert quotas["go"]["clamped"] is False
    assert unallocated == 997
    assert quotas["py"]["new_value"] == 12


def test_tag_exposure_cap_bounds_any_single_tag():
    candidates = [{"key": "area:cli", "learning_progress": 0.9},
                  {"key": "language:go", "learning_progress": 0.0}]
    shares, overflow = AT.allocate(candidates, budget=32, tag_cap=8)
    assert all(share <= 8 for share in shares.values())
    assert sum(shares.values()) + overflow == 32  # capped budget is released, not lost


def test_largest_remainder_splits_deterministically():
    assert AT.largest_remainder({"a": 1.0, "b": 1.0}, 7) == {"a": 4, "b": 3}
    assert AT.largest_remainder({}, 7) == {}
    assert AT.largest_remainder({"a": 3.0, "b": 1.0}, 0) == {"a": 0, "b": 0}


# --- holdouts -------------------------------------------------------------------------


def test_holdout_sampling_is_deterministic():
    first = AT.sample_holdouts(["a", "b", "c", "d", "e", "f", "g", "h"], 3, "ff")
    second = AT.sample_holdouts(["a", "b", "c", "d", "e", "f", "g", "h"], 3, "ff")
    assert first == second
    assert len(first) == 2  # ceil(0.25 x 8)
    assert AT.sample_holdouts([], 3, "ff") == []
    assert AT.sample_holdouts(["only"], 3, "ff") == []  # never hold out a lone target
    both = AT.sample_holdouts(["a", "b"], 3, "ff")
    assert len(both) == 1  # never hold out every target


def test_holdout_tags_are_stable_across_identical_runs(workspace):
    first = run_targeting(workspace)
    assert first.returncode == 0, first.stdout + first.stderr
    directives = yaml.safe_load(
        (workspace["out_dir"] / "directives_round_001.yaml").read_text())
    # Same inputs again (verdict closed first) reproduce the same holdout set.
    ws2 = {**workspace, "ledger": workspace["root"] / "t2" / "hypothesis_ledger.yaml",
           "out_dir": workspace["root"] / "t2"}
    second = run_targeting(ws2)
    assert second.returncode == 0, second.stdout + second.stderr
    directives2 = yaml.safe_load(
        (ws2["out_dir"] / "directives_round_001.yaml").read_text())
    assert directives["holdout_tags"] == directives2["holdout_tags"]
    assert set(directives["holdout_tags"]) <= set(
        yaml.safe_load(workspace["ledger"].read_text())[0]["hypothesis"]["target_tags"])


# --- ledger discipline ------------------------------------------------------------------


def test_directives_carry_the_contract_keys_only(workspace):
    result = run_targeting(workspace)
    assert result.returncode == 0, result.stdout + result.stderr
    directives = yaml.safe_load(
        (workspace["out_dir"] / "directives_round_001.yaml").read_text())
    assert set(directives) == {"round", "source_report_sha256", "language_quotas",
                               "filter_overrides", "skip_pr_files", "holdout_tags", "placebo"}
    assert directives["placebo"] is False
    assert directives["round"] == 1
    assert directives["skip_pr_files"]


def test_new_round_is_refused_while_a_verdict_is_open(workspace):
    assert run_targeting(workspace).returncode == 0
    again = run_targeting(workspace)
    assert again.returncode == 2
    assert "open verdict" in again.stderr
    assert "has not landed" in again.stderr      # same report -> the round is still in flight
    landed = run_targeting(workspace,
                           report=improved_report(workspace["root"] / "r2.json"))
    assert landed.returncode == 2
    assert "has landed" in landed.stderr and "--close-round" in landed.stderr


def test_stale_report_does_not_count_as_landed(workspace):
    """expected_data_landed's 'ours >= theirs' branch: a DIFFERENT report that
    predates the round's registration is still in flight — the gate demands
    waiting, not --close-round."""
    assert run_targeting(workspace).returncode == 0   # round 1 pinned at 2026-10-30
    stale = make_report(workspace["root"] / "stale.json", DEFAULT_TAGS,
                        generated_at="2026-10-29T00:00:00Z")
    result = run_targeting(workspace, report=stale)
    assert result.returncode == 2
    assert "has not landed" in result.stderr and "round 1" in result.stderr


def test_force_overrides_open_verdict(workspace):
    assert run_targeting(workspace).returncode == 0
    forced = run_targeting(workspace, "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    rows = yaml.safe_load(workspace["ledger"].read_text())
    assert [row["round"] for row in rows] == [1, 2]


def test_duplicate_round_number_is_refused(workspace):
    assert run_targeting(workspace).returncode == 0
    duplicate = run_targeting(workspace, "--round", "1", "--force")
    assert duplicate.returncode == 2
    assert "append-only" in duplicate.stderr


def test_dry_run_writes_nothing(workspace):
    result = run_targeting(workspace, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not workspace["ledger"].exists()
    assert not workspace["out_dir"].exists()


def test_directives_write_is_refused_without_a_registered_hypothesis(
        workspace, monkeypatch, tmp_path):
    summary = {"hypothesis": {"target_tags": ["language:go"], "round": 1},
               "directives": {"round": 1, "source_report_sha256": "0" * 64},
               "blocked": [], "_report_generated_at": ""}
    monkeypatch.setattr(AT, "save_ledger", lambda *a, **k: None)  # registration "fails"
    with pytest.raises(SystemExit) as raised:
        AT.pre_register(tmp_path / "ledger.yaml", [], summary, 1,
                        workspace["report"], tmp_path / "directives.yaml")
    assert raised.value.code == 2


def test_ledger_row_shape(workspace):
    assert run_targeting(workspace).returncode == 0
    row = yaml.safe_load(workspace["ledger"].read_text())[0]
    assert row["verdict"] is None
    assert row["applied_config_diff"] is None
    hypothesis = row["hypothesis"]
    assert hypothesis["expected_direction"] == "increase"
    assert hypothesis["supply_counts"]["language:go"] == 4
    assert hypothesis["noise_floor"]["multiplier"] == 2.0
    assert hypothesis["actuation"]["exposure_caps"] == {"repo": 0.15, "tag": 0.25}
    assert row["directives_file"].endswith("directives_round_001.yaml")


# --- closure ------------------------------------------------------------------------------


def improved_report(path: Path) -> Path:
    tags = [
        tag("language:go", 40, 0.45, 0.20, 0.11),   # +0.20 >= deadband -> moved
        tag("area:cli", 50, 0.32, 0.02, 0.02),      # +0.02 < deadband -> not moved
    ]
    report = make_report(path, tags, generated_at="2026-10-31T00:00:00Z")
    data = json.loads(report.read_text())
    data["tasks"] = {f"task-{i}": {"reward_mean": rate, "per_repeat": [rate] * 3, "exceptions": 0}
                     for i, rate in enumerate([0.7, 0.65, 0.6, 0.6, 0.65, 0.6, 0.65, 0.6])}
    report.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return report


def close_round(workspace, new_report=None, *extra):
    command = [sys.executable, str(SCRIPT), "--close-round", "--round", "1",
               "--ledger", str(workspace["ledger"]), "--pool-root", str(workspace["pool"])]
    if new_report is not None:
        command += ["--new-report", str(new_report)]
    return subprocess.run(command + list(extra), capture_output=True, text=True)


def test_two_part_verdict_closure_opens_the_next_round(workspace):
    assert run_targeting(workspace).returncode == 0
    directives = yaml.safe_load(
        (workspace["out_dir"] / "directives_round_001.yaml").read_text())
    holdout = set(directives["holdout_tags"])
    targets = set(yaml.safe_load(workspace["ledger"].read_text())[0]["hypothesis"]
                  ["target_tags"])
    measured = targets - holdout
    new_report = improved_report(workspace["root"] / "weakness_report_r2.json")
    closed = close_round(workspace, new_report)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    verdict = yaml.safe_load(workspace["ledger"].read_text())[0]["verdict"]
    assert verdict["mean_direction"]["direction"] == "improved"
    tags = verdict["intended_effect"]["tags"]
    assert set(tags) == measured            # holdout tags are quarantined from the verdict
    if "language:go" in tags:
        assert tags["language:go"]["moved_expected"] is True    # +0.20 clears the deadband
    if "area:cli" in tags:
        assert tags["area:cli"]["moved_expected"] is False      # +0.02 does not
    assert verdict["holdout_tags_excluded"] == sorted(holdout & targets)
    # With the verdict closed, the next round opens without --force.
    nxt = run_targeting(workspace, report=new_report)
    assert nxt.returncode == 0, nxt.stdout + nxt.stderr
    rows = yaml.safe_load(workspace["ledger"].read_text())
    assert [row["round"] for row in rows] == [1, 2]


def test_supply_limited_null_closes_without_eval_spend(workspace):
    assert run_targeting(workspace).returncode == 0
    closed = close_round(workspace, None, "--closure", "supply-limited-null")
    assert closed.returncode == 0, closed.stdout + closed.stderr
    row = yaml.safe_load(workspace["ledger"].read_text())[0]
    assert row["verdict"]["closure_type"] == "supply-limited-null"
    assert row["verdict"]["post_create_supply_counts"] == {"language:go": 4, "area:cli": 4}
    # The deadlock is broken: the next round may open.
    assert run_targeting(workspace).returncode == 0


def test_supply_limited_null_warns_when_supply_exists(workspace):
    assert run_targeting(workspace).returncode == 0
    closed = close_round(workspace, None, "--closure", "supply-limited-null")
    assert closed.returncode == 0, closed.stdout + closed.stderr
    # Both target tags have pool supply (4 each): the null must be auditable.
    assert "supply-limited-null asserted but some target tags have pool supply" \
        in closed.stderr


def test_zero_sd_close_round_reads_flat_and_not_moved(workspace):
    """A zero-SD round with byte-identical rates: the 1/n resolution floors keep
    both deadbands nonzero, so the verdict records flat / moved_expected=False
    instead of certifying a 0.0 delta."""
    make_report(workspace["report"], DEFAULT_TAGS, noise={
        "k": 1, "between_repeat_sd": 0.0,
        "per_tag_sd": {t["key"]: 0.0 for t in DEFAULT_TAGS}})
    assert run_targeting(workspace).returncode == 0
    flat = make_report(workspace["root"] / "flat.json", DEFAULT_TAGS,
                       generated_at="2026-10-31T00:00:00Z",
                       noise={"k": 1, "between_repeat_sd": 0.0,
                              "per_tag_sd": {t["key"]: 0.0 for t in DEFAULT_TAGS}})
    closed = close_round(workspace, flat)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    verdict = yaml.safe_load(workspace["ledger"].read_text())[0]["verdict"]
    assert verdict["mean_direction"]["direction"] == "flat"
    assert verdict["mean_direction"]["deadband"] > 0.0
    for result in verdict["intended_effect"]["tags"].values():
        assert result["moved_expected"] is False
        assert result["deadband"] > 0.0


def test_regressed_verdict_counts_as_failed_retry(workspace):
    """Rates dropping past the deadband: direction 'regressed', the tag does not
    count as moved, and the failure accrues toward the futility freeze."""
    assert run_targeting(workspace).returncode == 0
    worse = make_report(workspace["root"] / "worse.json", [
        tag("language:go", 40, 0.05, -0.20, -0.5),
        tag("area:cli", 50, 0.10, -0.20, -0.5),
    ], generated_at="2026-10-31T00:00:00Z",
        tasks={f"task-{i}": {"reward_mean": rate, "per_repeat": [rate] * 3, "exceptions": 0}
               for i, rate in enumerate([0.1] * 8)})
    closed = close_round(workspace, worse)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    rows = yaml.safe_load(workspace["ledger"].read_text())
    verdict = rows[0]["verdict"]
    assert verdict["mean_direction"]["direction"] == "regressed"
    assert verdict["intended_effect"]["moved"] is False
    assert all(not r["moved_expected"] for r in verdict["intended_effect"]["tags"].values())
    # Every MEASURED tag (holdouts are excluded from the verdict) failed.
    _frozen, failures = AT.historical_freezes(rows)
    assert failures == {key: 1 for key in verdict["intended_effect"]["tags"]}


def test_close_round_resolves_raw_evaluator_language_keys(workspace):
    """The ledger stores curator keys (language:py) while reports carry the
    evaluator's spelling verbatim (language:python): the two-part verdict must
    still resolve both sides and record a real moved_expected."""
    make_report(workspace["report"], [
        tag("language:python", 40, 0.25, -0.08, 0.05),  # raw evaluator spelling
    ])
    assert run_targeting(workspace).returncode == 0
    rows = yaml.safe_load(workspace["ledger"].read_text())
    assert rows[0]["hypothesis"]["target_tags"] == ["language:py"]
    improved = make_report(workspace["root"] / "improved_raw.json", [
        tag("language:python", 40, 0.45, 0.20, 0.5),   # +0.20 clears the 0.04 deadband
    ], generated_at="2026-10-31T00:00:00Z",
        tasks={f"task-{i}": {"reward_mean": rate, "per_repeat": [rate] * 3, "exceptions": 0}
               for i, rate in enumerate([0.7, 0.65, 0.6, 0.6, 0.65, 0.6, 0.65, 0.6])})
    closed = close_round(workspace, improved)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    rows = yaml.safe_load(workspace["ledger"].read_text())
    tags = rows[0]["verdict"]["intended_effect"]["tags"]
    assert set(tags) == {"language:py"}                  # canonical key, resolved
    assert tags["language:py"]["moved_expected"] is True
    assert tags["language:py"]["baseline"] == pytest.approx(0.25)
    assert tags["language:py"]["current"] == pytest.approx(0.45)
    _frozen, failures = AT.historical_freezes(rows)
    assert failures == {}                                # a real move, no failed retry


def test_holdout_tag_is_quarantined_next_round_only(workspace):
    """§5.4 A3 step 4: round N's holdout tag is blocked from round N+1's
    directive derivation (holdout_quarantine) and targetable again in round N+2."""
    assert run_targeting(workspace).returncode == 0
    directives = yaml.safe_load(
        (workspace["out_dir"] / "directives_round_001.yaml").read_text())
    held_out = directives["holdout_tags"]
    assert held_out                                        # 2 targets -> 1 holdout
    # Both tags strong enough to clear the gate, so round 2 always registers.
    strong = make_report(workspace["root"] / "strong.json", [
        tag("language:go", 40, 0.45, 0.20, 0.5),
        tag("area:cli", 50, 0.30, 0.20, 0.5),
    ], generated_at="2026-10-31T00:00:00Z",
        tasks={f"task-{i}": {"reward_mean": rate, "per_repeat": [rate] * 3, "exceptions": 0}
               for i, rate in enumerate([0.7, 0.65, 0.6, 0.6, 0.65, 0.6, 0.65, 0.6])})
    closed = close_round(workspace, strong)
    assert closed.returncode == 0, closed.stdout + closed.stderr

    second = run_targeting(workspace, report=strong)      # round 2
    assert second.returncode == 0, second.stdout + second.stderr
    blocked = gate_blocks(workspace["ledger"])
    assert blocked[held_out[0]]["reason"] == "holdout_quarantine"
    assert blocked[held_out[0]]["prior_round"] == 1

    # Round 3 (after closing round 2 without eval spend) may target it again:
    # the quarantine covers exactly the next round.
    closed2 = subprocess.run(
        [sys.executable, str(SCRIPT), "--close-round", "--round", "2",
         "--ledger", str(workspace["ledger"]), "--pool-root", str(workspace["pool"]),
         "--closure", "supply-limited-null"], capture_output=True, text=True)
    assert closed2.returncode == 0, closed2.stdout + closed2.stderr
    third = run_targeting(workspace, report=strong)       # round 3
    assert third.returncode == 0, third.stdout + third.stderr
    blocked3 = gate_blocks(workspace["ledger"])
    assert blocked3.get(held_out[0], {}).get("reason") != "holdout_quarantine"


def make_audit_readout(path: Path, audited_resolve: float, audited_n: int = 10,
                       between_repeat_sd: float = 0.02) -> Path:
    path.write_text(json.dumps({
        "schema": "legoflow.audit_readout.v1",
        "round": 1,
        "eval_jobs": ["job-r1"],
        "k": 1,
        "audited_resolve": audited_resolve,
        "audited_n": audited_n,
        "between_repeat_sd": between_repeat_sd,
        "per_tag": {},
    }, indent=2), encoding="utf-8")
    return path


def close_round_cmd(workspace, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--close-round", "--round", "1",
         "--ledger", str(workspace["ledger"]), "--pool-root", str(workspace["pool"]),
         *extra], capture_output=True, text=True)


def test_close_round_records_audited_direction_and_warns_on_absence(tmp_path):
    """§7.6(b): with --audit-readout at open AND close time the verdict carries
    an audited_direction block; without any, the two-part close warns loudly."""
    pool = make_pool(tmp_path / "p1")
    ws1 = {"root": tmp_path / "p1", "pool": pool,
           "report": make_report(tmp_path / "p1" / "report.json", DEFAULT_TAGS),
           "ledger": tmp_path / "p1" / "ledger.yaml", "out_dir": tmp_path / "p1" / "out"}
    baseline_readout = make_audit_readout(tmp_path / "p1" / "audit_base.json", 0.20)
    assert run_targeting(ws1, "--audit-readout", str(baseline_readout)).returncode == 0
    new_readout = make_audit_readout(tmp_path / "p1" / "audit_new.json", 0.50)
    new_report = improved_report(tmp_path / "p1" / "r2.json")
    closed = close_round_cmd(ws1, "--new-report", str(new_report),
                             "--audit-readout", str(new_readout))
    assert closed.returncode == 0, closed.stdout + closed.stderr
    verdict = yaml.safe_load(ws1["ledger"].read_text())[0]["verdict"]
    assert verdict["audit_readout_sha256"] == AT.sha256_file(new_readout)
    audited = verdict["audited_direction"]
    assert audited["baseline"] == pytest.approx(0.20)
    assert audited["current"] == pytest.approx(0.50)
    assert audited["delta"] == pytest.approx(0.30)
    assert audited["direction"] == "improved"   # 0.30 >= 2 x max(0.02, 1/10) = 0.2
    assert "internal-only" not in closed.stderr

    # Without any audit readout the close still works but warns (§7.6(b)).
    ws2 = {"root": tmp_path / "p2", "pool": make_pool(tmp_path / "p2"),
           "report": make_report(tmp_path / "p2" / "report.json", DEFAULT_TAGS),
           "ledger": tmp_path / "p2" / "ledger.yaml", "out_dir": tmp_path / "p2" / "out"}
    assert run_targeting(ws2).returncode == 0
    bare = close_round_cmd(ws2, "--new-report",
                           str(improved_report(tmp_path / "p2" / "r2.json")))
    assert bare.returncode == 0, bare.stdout + bare.stderr
    assert "internal-only" in bare.stderr and "7.6(b)" in bare.stderr
    verdict2 = yaml.safe_load(ws2["ledger"].read_text())[0]["verdict"]
    assert "audit_readout_sha256" not in verdict2


def test_close_round_prefers_paired_task_statistic(tmp_path):
    """§2.1 'compare paired at task level': pooled rates improve while the
    majority of shared tasks regresses -> the paired direction wins."""
    rates_base = [0.5] * 25
    rates_new = ([0.5 + 0.5] * 10) + ([0.5 - 0.2] * 15)   # mean +0.08, but 15 down / 10 up
    base = make_report(tmp_path / "base.json", DEFAULT_TAGS, tasks={
        f"task-{i}": {"reward_mean": r, "per_repeat": [r] * 3, "exceptions": 0}
        for i, r in enumerate(rates_base)})
    new = make_report(tmp_path / "new.json", DEFAULT_TAGS, generated_at="2026-10-31T00:00:00Z",
                      tasks={f"task-{i}": {"reward_mean": r, "per_repeat": [r] * 3,
                                          "exceptions": 0}
                             for i, r in enumerate(rates_new)})
    ws = {"root": tmp_path, "pool": make_pool(tmp_path / "pool"), "report": base,
          "ledger": tmp_path / "ledger.yaml", "out_dir": tmp_path / "out"}
    assert run_targeting(ws).returncode == 0
    closed = close_round_cmd(ws, "--new-report", str(new))
    assert closed.returncode == 0, closed.stdout + closed.stderr
    mean_direction = yaml.safe_load(ws["ledger"].read_text())[0]["verdict"]["mean_direction"]
    assert mean_direction["delta"] > 0                       # the pooled rate improved
    assert mean_direction["direction_source"] == "paired"    # n_both 25 >= min_tag_n 20
    assert mean_direction["direction"] == "regressed"        # but 15 tasks went down
    assert mean_direction["paired"]["n_both"] == 25
    assert mean_direction["paired"]["improved"] == 10
    assert mean_direction["paired"]["regressed"] == 15


def test_already_closed_round_cannot_close_again(workspace):
    assert run_targeting(workspace).returncode == 0
    assert close_round(workspace, None, "--closure", "futility-freeze").returncode == 0
    again = close_round(workspace, None, "--closure", "futility-freeze")
    assert again.returncode == 1
    assert "already closed" in again.stderr


def test_repeated_failed_verdicts_freeze_a_tag(workspace, tmp_path):
    # A ledger history in which language:go failed to move twice (frozen) while
    # area:cli recovered in round 2 (only one failure, still eligible).
    rows = []
    for round_no, cli_moved in ((1, False), (2, True)):
        rows.append({
            "round": round_no,
            "created_at": f"2026-10-{round_no:02d}T00:00:00Z",
            "source_report_sha256": f"r{round_no}",
            "source_report_path": str(workspace["report"]),
            "report_generated_at": "2026-10-30T00:00:00Z",
            "hypothesis": {"target_tags": ["language:go", "area:cli"],
                           "actuation": {"holdout_tags": []}},
            "directives_file": f"directives_round_{round_no:03d}.yaml",
            "applied_config_diff": None,
            "verdict": {"mean_direction": {"direction": "flat"},
                        "intended_effect": {"tags": {
                            "language:go": {"moved_expected": False},
                            "area:cli": {"moved_expected": cli_moved}},
                            "moved": cli_moved}},
        })
    ledger = tmp_path / "ledger-history.yaml"
    ledger.write_text(yaml.safe_dump(rows), encoding="utf-8")
    frozen, failures = AT.historical_freezes(rows)
    assert failures == {"language:go": 2, "area:cli": 1}
    assert frozen == set()

    ws = {**workspace, "ledger": ledger, "out_dir": tmp_path / "out"}
    result = run_targeting(ws, "--round", "3")
    assert result.returncode == 0, result.stdout + result.stderr
    blocked = gate_blocks(ledger)
    assert blocked["language:go"]["reason"] == "futility_freeze"
    assert "area:cli" not in blocked or blocked["area:cli"]["reason"] != "futility_freeze"


def test_futility_freeze_closure_freezes_all_targets(workspace):
    assert run_targeting(workspace).returncode == 0
    assert close_round(workspace, None, "--closure", "futility-freeze").returncode == 0
    frozen, _failures = AT.historical_freezes(yaml.safe_load(workspace["ledger"].read_text()))
    assert "language:go" in frozen and "area:cli" in frozen


def test_saturated_zero_lp_tag_is_frozen(workspace):
    make_report(workspace["report"], [
        tag("language:go", 40, 0.25, -0.08, 0.05),
        tag("area:cli", 50, 1.0, -0.05, 0.0),  # solved and not improving
    ])
    assert run_targeting(workspace).returncode == 0
    assert gate_blocks(workspace["ledger"])["area:cli"]["reason"] == "saturated_zero_lp"


def test_no_targets_means_no_writes_and_zero_exit(workspace):
    make_report(workspace["report"], [tag("language:rust", 30, 0.28, -0.001, 0.01)])
    result = run_targeting(workspace)
    assert result.returncode == 0
    assert "no directives emitted" in result.stdout.lower()
    assert not workspace["ledger"].exists()


# --- guards on inputs -----------------------------------------------------------------------


def test_missing_report_fails_cleanly(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--report", str(tmp_path / "nope.json"),
         "--pool-root", str(tmp_path), "--ledger", str(tmp_path / "l.yaml")],
        capture_output=True, text=True)
    assert result.returncode == 1


def test_wrong_schema_is_refused(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "something.else", "tags": []}), encoding="utf-8")
    pool = make_pool(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--report", str(bad), "--pool-root", str(pool),
         "--ledger", str(tmp_path / "l.yaml"), "--out-dir", str(tmp_path / "o")],
        capture_output=True, text=True)
    assert result.returncode == 2
    assert "legoflow.weakness.v1" in result.stderr


def test_language_keys_canonicalize_across_alias_forms():
    supply = {"vocabulary": {dim: set() for dim in AT.TAG_DIMENSIONS}}
    supply["vocabulary"]["language"] = {"py", "go", "rust"}
    assert AT.canonicalize_key("language:Python", supply["vocabulary"]) == ("language", "py")
    assert AT.canonicalize_key("language:py", supply["vocabulary"]) == ("language", "py")
    assert AT.canonicalize_key("Go", supply["vocabulary"]) == ("language", "go")
    assert AT.canonicalize_key("nosuchdimension:x:y", supply["vocabulary"]) is None
    # The evaluator's language-only fallback key must never become a target.
    assert AT.canonicalize_key("language:unknown", supply["vocabulary"]) is None


def test_pool_supply_counts_verified_tasks_by_tag(tmp_path):
    supply = AT.pool_supply(make_pool(tmp_path))
    assert supply["verified_tasks"] == 12
    assert supply["tag_counts"]["language:py"] == 6
    assert supply["tag_counts"]["area:cli"] == 4
    assert supply["tag_counts"]["difficulty_tier:hard"] == 4  # go scores 8.0
    assert supply["tag_lang_counts"][("area:cli", "go")] == 4


def test_repo_metadata_extra_feeds_the_repo_exposure_alarm(tmp_path):
    """make_pool(repo=...) writes [metadata.extra] user/repo, which the supply
    reader picks up: a dominant repo warns and lands in the ledger row."""
    pool = tmp_path / "swe_tasks"
    for i in range(10):
        write_task(pool, "py-cc", f"mega__bigproj-{i}",
                   ["Python", "backend", "flask", "wrong-default"], 5.5,
                   owner="mega", repo="bigproj")
    write_task(pool, "go-cc", "tiny__smallproj-1",
               ["Go", "cli", "kubernetes", "missing-fallback"], 5.0,
               owner="tiny", repo="smallproj")
    for lang in ("py", "go"):
        ids = sorted(p.name for p in (pool / f"{lang}-cc").iterdir() if p.is_dir())
        (pool / f"{lang}-cc" / "verifiable_tasks.txt").write_text("\n".join(ids) + "\n")
    supply = AT.pool_supply(pool)
    assert supply["repo_counts"] == {"mega/bigproj": 10, "tiny/smallproj": 1}
    ws = {"root": tmp_path, "pool": pool,
          "report": make_report(tmp_path / "report.json", [
              tag("language:go", 40, 0.25, -0.08, 0.05)]),
          "ledger": tmp_path / "ledger.yaml", "out_dir": tmp_path / "out"}
    result = run_targeting(ws)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "repo exposure" in result.stderr and "mega/bigproj" in result.stderr
    row = yaml.safe_load(ws["ledger"].read_text())[0]
    alarms = {a["alarm"]: a for a in row["stability_alarms"]}
    assert alarms["repo_exposure"]["repo"] == "mega/bigproj"
    assert alarms["repo_exposure"]["share"] == pytest.approx(10 / 11, abs=0.001)


def test_difficulty_tier_targets_emit_the_filter_override(tmp_path):
    ws = {"root": tmp_path, "pool": make_pool(tmp_path / "pool"),
          "report": make_report(tmp_path / "report.json", [
              tag("difficulty_tier:hard", 40, 0.25, -0.08, 0.05)]),
          "ledger": tmp_path / "ledger.yaml", "out_dir": tmp_path / "out"}
    result = run_targeting(ws)
    assert result.returncode == 0, result.stdout + result.stderr
    directives = yaml.safe_load((ws["out_dir"] / "directives_round_001.yaml").read_text())
    assert directives["filter_overrides"] == {"create.min_source_files": 3}
    # easy-tier targets ask for the inclusive knob instead
    ws2 = {"root": tmp_path / "t2", "pool": make_pool(tmp_path / "t2" / "pool"),
           "report": make_report(tmp_path / "t2" / "report.json", [
               tag("difficulty_tier:medium", 40, 0.25, -0.08, 0.05)]),
           "ledger": tmp_path / "t2" / "ledger.yaml", "out_dir": tmp_path / "t2" / "out"}
    result2 = run_targeting(ws2)
    assert result2.returncode == 0, result2.stdout + result2.stderr
    directives2 = yaml.safe_load(
        (ws2["out_dir"] / "directives_round_001.yaml").read_text())
    assert directives2["filter_overrides"] == {"create.min_source_files": 1}


def make_concentrated_pool(root: Path) -> Path:
    """11 py + 1 rust: language entropy = 0.41 < 0.5 floor."""
    pool = root / "swe_tasks"
    for i in range(11):
        write_task(pool, "py-cc", f"acme__pyproj-{i}",
                   ["Python", "backend", "flask", "wrong-default"], 5.5)
    write_task(pool, "rust-cc", "acme__rustproj-1",
               ["Rust", "library", "serde", "incomplete-validation"], 2.0)
    for lang in ("py", "rust"):
        ids = sorted(p.name for p in (pool / f"{lang}-cc").iterdir() if p.is_dir())
        (pool / f"{lang}-cc" / "verifiable_tasks.txt").write_text("\n".join(ids) + "\n")
    return pool


def test_stability_guards_alarm_on_entropy_and_pass_k_diversity(tmp_path):
    ws = {"root": tmp_path, "pool": make_concentrated_pool(tmp_path / "pool"),
          "report": make_report(tmp_path / "report.json", [
              tag("language:rust", 40, 0.25, -0.08, 0.05)]),
          "ledger": tmp_path / "ledger.yaml", "out_dir": tmp_path / "out"}
    result = run_targeting(ws)
    assert result.returncode == 0, result.stdout + result.stderr
    row = yaml.safe_load(ws["ledger"].read_text())[0]
    alarms = {a["alarm"]: a for a in row["stability_alarms"]}
    assert alarms["tag_entropy_floor"]["normalized_entropy"] < 0.5
    # make_report's per_repeat lists are constant per task -> 0 varying tasks
    assert alarms["pass_k_diversity"] == {"alarm": "pass_k_diversity",
                                          "varying_tasks": 0, "measured_tasks": 8,
                                          "floor": 0.25}
    assert "tag_entropy_floor" in result.stderr and "pass_k_diversity" in result.stderr


def test_signal_firewall_guards_the_directives_and_ledger_writes():
    """§5.6 enforcement: an offline-only field in either payload refuses the
    write; the payloads this gate actually emits pass."""
    with pytest.raises(SystemExit) as raised:
        AT.enforce_signal_firewall({"round": 1, "gold_patch": "secret"}, "test payload")
    assert raised.value.code == 2
    with pytest.raises(SystemExit) as raised:
        AT.enforce_signal_firewall({"audited_resolve": 0.5}, "test payload")
    assert raised.value.code == 2
    # The real payloads (directives keys + ledger row shape) are online-clean.
    AT.enforce_signal_firewall(
        {"round": 1, "source_report_sha256": "0" * 64, "language_quotas": {},
         "filter_overrides": {}, "skip_pr_files": [], "holdout_tags": [], "placebo": False},
        "directives payload")
    AT.enforce_signal_firewall(
        {"round": 1, "created_at": "now", "source_report_sha256": "0" * 64,
         "hypothesis": {"target_tags": ["language:go"],
                        "noise_floor": {"multiplier": 2.0, "per_tag_sd": {},
                                        "between_repeat_sd": 0.02}},
         "gate_blocked": [{"key": "language:js", "reason": "n_too_small", "n": 3}],
         "verdict": None}, "hypothesis ledger row")


def test_directives_verify_rehashes_and_refuses_on_drift(workspace):
    assert run_targeting(workspace).returncode == 0
    directives = workspace["out_dir"] / "directives_round_001.yaml"
    ok = subprocess.run(
        [sys.executable, str(SCRIPT), "--verify", str(directives),
         "--report", str(workspace["report"])], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "OK" in ok.stdout and "matches" in ok.stdout
    drifted = make_report(workspace["root"] / "drifted.json", DEFAULT_TAGS,
                          generated_at="2026-11-01T00:00:00Z")
    refused = subprocess.run(
        [sys.executable, str(SCRIPT), "--verify", str(directives),
         "--report", str(drifted)], capture_output=True, text=True)
    assert refused.returncode == 2
    assert "derived from a different report" in refused.stderr


def test_report_with_wrong_tagger_version_is_refused(workspace):
    data = json.loads(workspace["report"].read_text())
    data["tagger_version"] = "some-other-tagger@2"
    workspace["report"].write_text(json.dumps(data, indent=2), encoding="utf-8")
    result = run_targeting(workspace)
    assert result.returncode == 2
    assert "tagger_version" in result.stderr or "tag vocabulary has drifted" in result.stderr


def test_difficulty_tier_vocabulary_drift_is_rejected():
    supply = {"vocabulary": {dim: set() for dim in AT.TAG_DIMENSIONS}}
    supply["vocabulary"]["difficulty_tier"] = {"easy", "medium", "hard"}
    # A raw gold difficulty string can never map to a pool difficulty_bin key.
    assert AT.canonicalize_key("difficulty_tier:very_hard", supply["vocabulary"]) is None
    assert AT.canonicalize_key("difficulty_tier:hard", supply["vocabulary"]) \
        == ("difficulty_tier", "hard")


def test_difficulty_tier_drift_tag_is_blocked_as_unknown(workspace):
    make_report(workspace["report"], [
        tag("difficulty_tier:very_hard", 40, 0.25, -0.08, 0.05),
    ])
    result = run_targeting(workspace)
    assert result.returncode == 0, result.stdout + result.stderr
    assert null_round_blocks(result)["difficulty_tier:very_hard"]["reason"] \
        == "unknown_tag_key"
