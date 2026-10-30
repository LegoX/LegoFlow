"""Signal firewall tests (RSI_LOOP_PLAN §5.6 / §10 fix 8).

Covers blocks/evaluator/signal_policy.yaml + scripts/signal_guard.py:
  * module + CLI classification of offline-only / online-eligible / eval-only,
  * the normative case: a payload carrying diff_hunk_overlap is refused,
  * pattern rules (gold_*, c1-c4, *_patch), nesting, enum-list values,
  * unknown keys warn but do not violate (unless --strict),
  * context semantics (offline allows gold-aware fields; eval allows readouts).

No network, no Docker; runs against the git-tracked policy.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "blocks" / "evaluator" / "scripts"
POLICY_PATH = REPO_ROOT / "blocks" / "evaluator" / "signal_policy.yaml"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


signal_guard = _load("signal_guard")


@pytest.fixture(scope="module")
def policy():
    return signal_guard.load_policy(POLICY_PATH)


# --- module-level classification -------------------------------------------
def test_policy_loads_with_all_three_tiers(policy):
    assert set(policy.tiers) == {"offline_only", "online_eligible", "eval_only"}
    assert policy.allowed("offline_only", "offline")
    assert not policy.allowed("offline_only", "online")
    assert policy.allowed("online_eligible", "online")
    assert not policy.allowed("eval_only", "online")
    assert not policy.allowed("eval_only", "offline")


def test_gold_aware_features_classified_offline(policy):
    for key in ("diff_hunk_overlap", "C1_file_read", "c2_func_read",
                "C3_file_alignment", "c4_func_alignment", "tier1_labels",
                "hack_details", "gold_lines_changed", "test_patch",
                "model_patch", "FAIL_TO_PASS"):
        tier, _ = policy.classify(key)
        assert tier == "offline_only", key


def test_online_fields_pass(policy):
    for key in ("reward", "rewards", "resolved", "pass_rate", "c5_test_executed",
                "tool_error_storm", "loop_detected", "primary_failure",
                "axes", "flags", "learning_progress", "delta_vs_prev",
                "audit_excluded_ids_sha256", "noise", "ci95_wilson",
                "checkpoint_ref", "tokens"):
        tier, _ = policy.classify(key)
        assert tier == "online_eligible", key


def test_pattern_rules_and_normalization(policy):
    # gold_* / *_gold_* join products at any depth
    assert policy.classify("gold_tag_join")[0] == "offline_only"
    assert policy.classify("per-record-gold-difficulty")[0] == "offline_only"
    # camelCase and space aliases still resolve
    assert policy.classify("diffHunkOverlap")[0] == "offline_only"
    assert policy.classify("gold patch")[0] == "offline_only"


def test_eval_only_audit_readouts(policy):
    for key in ("audited_resolve", "audit_resolve_rate", "audited_pass_rate"):
        tier, _ = policy.classify(key)
        assert tier == "eval_only", key


# --- payload checking -------------------------------------------------------
def test_clean_online_payload_has_no_violations(policy):
    payload = {
        "schema": "legoflow.weakness.v1",
        "k": 3,
        "checkpoint_ref": "round1:abc",
        "audit_excluded_ids_sha256": "0" * 64,
        "noise": {"between_repeat_sd": 0.05, "per_tag_sd": {"language:python": 0.04}},
        "tags": [{"key": "language:python", "n": 60, "pass_rate": 0.5,
                  "failure_mix": {"implementation": 0.6, "diagnosis": 0.4}}],
        "tasks": {"t1": {"reward_mean": 1.0, "per_repeat": [1, 1, 1], "exceptions": []}},
    }
    violations, _ = signal_guard.check_payload(payload, policy=policy)
    assert violations == []


def test_payload_with_diff_hunk_overlap_is_blocked(policy):
    payload = {"tags": [{"key": "language:python",
                         "deterministic_features": {"diff_hunk_overlap": 0.42}}]}
    violations, _ = signal_guard.check_payload(payload, policy=policy)
    assert any(v["key"] == "diff_hunk_overlap" for v in violations)
    # nested path is reported so the offender can be traced
    assert any("deterministic_features.diff_hunk_overlap" in v["path"] for v in violations)


def test_nested_c_features_and_gold_fields_blocked(policy):
    payload = {"a": [{"b": {"c1_file_read": 1, "gold_patch": "diff --git ..."}}]}
    violations, _ = signal_guard.check_payload(payload, policy=policy)
    assert {v["key"] for v in violations} == {"c1_file_read", "gold_patch"}


def test_enum_list_values_are_checked(policy):
    payload = {"flags": ["environment_noise", "hack"]}
    violations, _ = signal_guard.check_payload(payload, policy=policy)
    assert any(v["rule"] == "enum:flags:hack" for v in violations)
    assert not any("environment_noise" in v["path"] for v in violations)


def test_secondary_failures_offline_enum_values_blocked(policy):
    # secondary_failures previously had no offline_only group: gold-aware
    # labels ("hack", "alternative_fix", "missing_gold") sailed through
    # online payloads while the same values under flags were caught.
    payload = {"secondary_failures": ["diagnosis", "hack",
                                      "alternative_fix", "missing_gold"]}
    violations, _ = signal_guard.check_payload(payload, policy=policy)
    rules = {v["rule"] for v in violations}
    assert "enum:secondary_failures:hack" in rules
    assert "enum:secondary_failures:alternative_fix" in rules
    assert "enum:secondary_failures:missing_gold" in rules
    assert not any("diagnosis" in v["path"] for v in violations)


def test_id_carrier_map_subkeys_are_ids_not_fields(policy):
    # Task ids under report['tasks'] and tag keys under noise.per_tag_sd are
    # DATA, not field names: a pattern-colliding id ("x__gold_rush-1" vs
    # "*_gold_*") must neither violate nor warn, while fields nested under an
    # id are still classified normally.
    payload = {
        "tasks": {"x__gold_rush-1": {"reward_mean": 1.0, "per_repeat": [1, 1]}},
        "noise": {"per_tag_sd": {"topic:gold-rush": 0.1}},
    }
    violations, warnings = signal_guard.check_payload(payload, policy=policy)
    assert violations == []
    assert not any(w["key"] == "x__gold_rush-1" for w in warnings)
    assert not any(w["key"] == "topic:gold-rush" for w in warnings)

    dirty = {"tasks": {"x__t-1": {"gold_patch": "diff --git ..."}}}
    violations, _ = signal_guard.check_payload(dirty, policy=policy)
    assert {v["key"] for v in violations} == {"gold_patch"}

    # The exemption is scoped to declared id-carrier maps only: the same
    # colliding key under a non-carrier map still trips the pattern tier.
    collide = {"results": {"x__gold_rush-1": 1}}
    violations, _ = signal_guard.check_payload(collide, policy=policy)
    assert any(v["key"] == "x__gold_rush-1" for v in violations)


def test_unknown_keys_warn_only_until_strict():
    payload = {"brand_new_field": 1}
    violations, warnings = signal_guard.check_payload(payload)
    assert violations == []
    assert warnings and warnings[0]["key"] == "brand_new_field"
    strict_violations, strict_warnings = signal_guard.check_payload(
        payload, strict=True)
    assert strict_violations and strict_violations[0]["key"] == "brand_new_field"
    assert strict_warnings == []


def test_context_semantics():
    payload = {"diff_hunk_overlap": 0.4, "audited_resolve": 0.9}
    online_v, _ = signal_guard.check_payload(payload)
    offline_v, _ = signal_guard.check_payload(payload, context="offline")
    eval_v, _ = signal_guard.check_payload(payload, context="eval")
    assert {v["key"] for v in online_v} == {"diff_hunk_overlap", "audited_resolve"}
    # offline analysis may hold gold-aware fields but never audit readouts
    assert {v["key"] for v in offline_v} == {"audited_resolve"}
    assert eval_v == []


def test_invalid_context_rejected():
    with pytest.raises(ValueError):
        signal_guard.check_payload({"reward": 1}, context="production")


# --- CLI ---------------------------------------------------------------------
def run_cli(*argv, stdin=None):
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "signal_guard.py"), *argv],
        capture_output=True, text=True, input=stdin, check=False)


def test_cli_exit_zero_on_clean_payload(tmp_path):
    payload = tmp_path / "clean.json"
    payload.write_text(json.dumps({"reward": 1, "resolved": True}), encoding="utf-8")
    proc = run_cli("check", "--payload", str(payload))
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_cli_nonzero_with_offending_key_list(tmp_path):
    payload = tmp_path / "dirty.json"
    payload.write_text(json.dumps({"tags": [{"diff_hunk_overlap": 0.9}]}),
                       encoding="utf-8")
    proc = run_cli("check", "--payload", str(payload))
    assert proc.returncode == 2
    assert "VIOLATION" in proc.stderr
    assert "diff_hunk_overlap" in proc.stderr
    assert "Offending keys:" in proc.stderr


def test_cli_stdin_and_policy_flag(tmp_path):
    proc = run_cli("check", "--payload", "-",
                   "--policy", str(POLICY_PATH),
                   stdin=json.dumps({"c5_test_executed": 1, "reward": 0}))
    assert proc.returncode == 0, proc.stderr


def test_cli_missing_policy_errors(tmp_path):
    proc = run_cli("check", "--payload", "-", "--policy",
                   str(tmp_path / "nope.yaml"), stdin="{}")
    assert proc.returncode == 1
    assert "ERROR" in proc.stderr


def test_cli_offline_context_allows_gold_fields(tmp_path):
    payload = tmp_path / "offline.json"
    payload.write_text(json.dumps({"gold_patch": "...", "diff_hunk_overlap": 0.1}),
                       encoding="utf-8")
    proc = run_cli("check", "--payload", str(payload), "--context", "offline")
    assert proc.returncode == 0, proc.stderr


def test_repo_policy_file_is_tracked_and_parsing():
    # The firewall is only normative if the policy ships with the repo.
    assert POLICY_PATH.is_file()
    text = POLICY_PATH.read_text(encoding="utf-8")
    assert "legoflow.signal_policy.v1" in text
