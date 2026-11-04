#!/usr/bin/env python3
"""Curator-side targeting gate: evaluator weakness findings -> next-run mining directives.

This is the agent gate of the RSI loop (with the stability
guards and the §9 zero-yield / futility deadlock fixes). It never edits
`config.yaml` and never mines anything itself; it reads one
`legoflow.weakness.v1` report (Edge 1), checks it against this block's OWN
pool supply, and emits `directives_round_NNN.yaml` for the human+agent to
apply to the next run's config/profile under confirmation (§5.7 step 2).

Gate order (a tag must clear every step to become a target):

  1. ledger discipline   no round may open while a prior verdict is open
                         (--force overrides; --close-round closes)
  2. sample size         n >= min_tag_n                      -> n_too_small
  3. freezes             saturated at zero learning progress -> saturated_zero_lp
                         per-tag retry cap exhausted          -> futility_freeze
                         held out by the prior closed round   -> holdout_quarantine
  4. supply check        supply >= supply_satisfaction_threshold
                                                         -> already_supplied
                         (weak-but-supplied tags are capped at baseline quota,
                          i.e. they receive a delta of 0)
  5. noise gate          |delta_vs_prev| >= noise_gate_multiplier x
                         max(tag_sd, global_sd,
                             stored_identical_config_spread,
                             1/n one-instance resolution)
                                                         -> below_noise_gate
                         (the stored spread is the max measured sd of prior
                          rounds' hypotheses; the 1/n term mirrors the report's
                          own noise_floor, so a zero-SD / k=1 report can never
                          clear every nonzero delta by default)
                         no delta at all (round 1)            -> no_baseline
  6. weakness            pass_rate below the report-wide mean -> not_weak

Survivors are ranked by learning_progress (I29 — a ranking feature, never a
controller on its own), the mining budget is distributed across languages as
`languages.<lang>.params.max_verified_tasks` deltas under the stability guards
(quota slew <= slew-limit/round, per-tag allocation <= tag-exposure-cap of the
budget, per-repo pool exposure alarm at repo-exposure-cap), a deterministic
holdout subset of the targets is reserved (§5.4 A3 step 4), and only THEN is
the hypothesis pre-registered into the append-only ledger and the directives
file written. A directives write without a registered hypothesis is refused —
the ledger row is written and read back before the directives file exists.

A tag held out in round N is additionally quarantined from round N+1's
directive derivation (§5.4 A3 step 4): its supply counts and the report's
deltas still carry round N's own actuation output, so re-targeting it
immediately would grade the round on its own homework.

Closure (§5.7 step 6 / §9): `--close-round --round N --new-report ...` fills the
two-part verdict {mean_direction, intended_effect} (holdout tags are excluded
from intended_effect — they are quarantined from verdict derivation). The
report-side tag keys are canonicalized at close time the same way open time
canonicalizes them, so an evaluator that spells `language:python` against the
curator's `language:py` still produces a real verdict. A round
whose post-create supply never materialized closes with
`--closure supply-limited-null` (no eval spend) or `--closure futility-freeze`.

Exit codes: 0 ok (including "nothing to actuate" — the loop must survive its
own null), 1 operational error, 2 gate refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

BLOCK_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOCK_DIR / "dashboard"))
import task_toml  # noqa: E402  (same-block import; blocks never import across boundaries)

REPORT_SCHEMA = "legoflow.weakness.v1"
AUDIT_READOUT_SCHEMA = "legoflow.audit_readout.v1"
TAG_DIMENSIONS = ("language", "area", "topic", "bug_class", "difficulty_tier")
# difficulty_bin() can only ever emit these three tiers; a report carrying any
# other spelling (e.g. a raw gold difficulty string like `very_hard`) is
# vocabulary drift, not a targetable tag.
DIFFICULTY_TIERS = ("easy", "medium", "hard")
# The evaluator's report tagger (build_weakness_report.py TAGGER_VERSION). A
# report tagged by any other version is refused: the curator's tag vocabulary
# only matches the taxonomy this gate was built against.
EXPECTED_TAGGER_VERSION = "gold_toml_tags+analysis_join_v1"
# §5.6 signal firewall: the evaluator owns the policy; this gate invokes its
# checker as a subprocess (blocks never import across boundaries) and refuses
# to write any directive-feeding payload the firewall rejects.
SIGNAL_GUARD = BLOCK_DIR.parent / "evaluator" / "scripts" / "signal_guard.py"

# config.yaml language keys (read_params.py) <-> the display names task.toml
# metadata and the weakness report carry. Both sides canonicalize to the key.
LANGUAGE_ALIASES = {
    "python": "py", "py": "py",
    "javascript": "js", "js": "js",
    "typescript": "ts", "ts": "ts",
    "go": "go", "golang": "go",
    "rust": "rust", "rs": "rust",
    "java": "java",
    "c": "c",
    "cpp": "cpp", "c++": "cpp",
}

# Mirrors scripts/read_params.py's default and select_stratified_tasks.py's bins.
DEFAULT_BASELINE_QUOTA = 10
EASY_MAX = 4.0
MEDIUM_MAX = 7.0
SATURATED_PASS_RATE = 0.95
# §9 zero-yield deadlock: after this many closed rounds without the intended
# tag moving, a tag is futility-frozen and never re-targeted.
FUTILITY_MAX_RETRIES = 2
# §5.4 A3 step 4 — holdout fraction of the target tags (deterministic sample).
HOLDOUT_FRACTION = 0.25
# decontam_check.py owns this file; directives only reference it.
DEFAULT_SKIP_FILE = "artifacts/decontam/eval_overlap_pr_ids.txt"


def die(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def refuse(message: str) -> None:
    die(message, code=2)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- report -------------------------------------------------------------------


def load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read weakness report {path}: {exc}")
    if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
        refuse(f"{path} is not a {REPORT_SCHEMA} report "
               f"(schema={report.get('schema') if isinstance(report, dict) else None!r})")
    if not isinstance(report.get("tags"), list):
        die(f"{path} has no tags[] list")
    tagger = report.get("tagger_version")
    if tagger != EXPECTED_TAGGER_VERSION:
        refuse(f"{path} was tagged by {tagger!r} but this gate expects "
               f"{EXPECTED_TAGGER_VERSION!r} — the tag vocabulary has drifted; "
               f"regenerate the report (or update EXPECTED_TAGGER_VERSION deliberately)")
    return report


def per_tag_sd(report: dict[str, Any]) -> dict[str, float]:
    """noise.per_tag_sd as key -> sd; accepts a mapping or a list of {key, sd}."""
    noise = report.get("noise") if isinstance(report.get("noise"), dict) else {}
    raw = noise.get("per_tag_sd")
    out: dict[str, float] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            out[str(key)] = float(value or 0.0)
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and "key" in entry:
                out[str(entry["key"])] = float(entry.get("sd") or 0.0)
    # A per-tag sd carried on the tag row itself wins (most specific).
    for tag in report["tags"]:
        key = str(tag.get("key") or "")
        for field in ("sd", "tag_sd", "between_repeat_sd"):
            if isinstance(tag.get(field), (int, float)):
                out[key] = float(tag[field])
                break
    return out


def global_sd(report: dict[str, Any]) -> float:
    noise = report.get("noise") if isinstance(report.get("noise"), dict) else {}
    value = noise.get("between_repeat_sd")
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def overall_pass_rate(report: dict[str, Any]) -> float | None:
    """Report-wide mean reward over the tasks map (Wilson-free, simple mean)."""
    tasks = report.get("tasks") if isinstance(report.get("tasks"), dict) else {}
    means = []
    for entry in tasks.values():
        if isinstance(entry, dict) and isinstance(entry.get("reward_mean"), (int, float)):
            means.append(float(entry["reward_mean"]))
    if means:
        return sum(means) / len(means)
    # Fall back to an n-weighted mean of tag pass rates.
    total_n = weighted = 0.0
    for tag in report["tags"]:
        n, rate = tag.get("n"), tag.get("pass_rate")
        if isinstance(n, (int, float)) and isinstance(rate, (int, float)):
            total_n += float(n)
            weighted += float(n) * float(rate)
    return weighted / total_n if total_n else None


def canonical_report_key(key: str) -> str:
    """Weakness-report tag key -> canonical hypothesis key
    ('language:Python' -> 'language:py'); unknown dimensions and bare values
    pass through unchanged. The evaluator emits report keys verbatim from gold
    tags (always colon-form), so the vocabulary-free branch of canonicalize_key
    suffices here — close_round needs no pool access to match keys."""
    key = str(key).strip()
    if ":" not in key:
        return key
    dimension, value = key.split(":", 1)
    dimension, value = dimension.strip().lower(), value.strip()
    if dimension not in TAG_DIMENSIONS:
        return key
    value = value.lower()
    if dimension == "language":
        value = canonical_language(value)
    return f"{dimension}:{value}"


def tag_pass_rate(report: dict[str, Any], key: str) -> float | None:
    """Pass rate of the tag whose key canonicalizes to `key` — both sides are
    normalized so an evaluator spelling ('language:python') matches a ledger
    target ('language:py')."""
    want = canonical_report_key(key)
    for tag in report["tags"]:
        if canonical_report_key(str(tag.get("key") or "")) == want \
                and isinstance(tag.get("pass_rate"), (int, float)):
            return float(tag["pass_rate"])
    return None


def tag_n_map(report: dict[str, Any]) -> dict[str, int]:
    """Pooled n per canonical tag key (max across reports when merged by the
    caller) — the 1/n resolution floor of each tag's deadband."""
    out: dict[str, int] = {}
    for tag in report["tags"]:
        key = canonical_report_key(str(tag.get("key") or ""))
        if isinstance(tag.get("n"), (int, float)) and int(tag["n"]) > 0:
            out[key] = max(out.get(key, 0), int(tag["n"]))
    return out


# --- ledger -------------------------------------------------------------------
# Append-only by discipline: rows are never reordered, removed, or rewritten
# except that --close-round fills the `verdict: null` of its own round
# (the curator ledger owns verdicts).


def load_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        die(f"cannot read hypothesis ledger {path}: {exc}")
    if rows in (None, ""):
        return []
    if not isinstance(rows, list):
        die(f"hypothesis ledger {path} must be a YAML list of round rows")
    return rows


def save_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(rows, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


def open_rounds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("verdict", None) is None]


def expected_data_landed(row: dict[str, Any], report: dict[str, Any], report_sha: str) -> bool:
    """Has the eval that should close this round produced a new report?

    The report that generated the round is pinned by sha; a *different* report
    generated at/after the round was registered is the expected data having
    landed. Anything else means the round is simply still in flight.
    """
    if report_sha == row.get("source_report_sha256"):
        return False
    theirs = str(row.get("report_generated_at") or "")
    ours = str(report.get("generated_at") or "")
    return not theirs or ours >= theirs


def check_no_open_round(rows: list[dict[str, Any]], report: dict[str, Any], report_sha: str,
                        force: bool) -> None:
    for row in open_rounds(rows):
        round_no = row.get("round")
        if expected_data_landed(row, report, report_sha):
            message = (f"round {round_no} has an open verdict and its expected report has "
                       f"landed — close it first: apply_targeting.py --close-round --round "
                       f"{round_no} --new-report <weakness_report.json>")
        else:
            message = (f"round {round_no} still has an open verdict and its expected report "
                       f"has not landed — wait, or close without eval spend: --close-round "
                       f"--round {round_no} --closure supply-limited-null")
        if force:
            print(f"WARN: --force overriding open round: {message}", file=sys.stderr)
            return
        refuse(message)


# --- pool supply ----------------------------------------------------------------


def difficulty_bin(dim: dict[str, Any]) -> str:
    """Same bins as scripts/select_stratified_tasks.py (easy <= 4 < medium <= 7 < hard)."""
    score = dim.get("score")
    if isinstance(score, (int, float)):
        if score <= EASY_MAX:
            return "easy"
        if score <= MEDIUM_MAX:
            return "medium"
        return "hard"
    label = str(dim.get("difficulty") or "").strip().lower()
    return label if label in ("easy", "medium", "hard") else "unknown"


def canonical_language(value: str) -> str:
    return LANGUAGE_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())


def pool_supply(pool_root: Path) -> dict[str, Any]:
    """Count verified tasks per canonical tag key using this block's own manifests.

    Membership follows the authoritative contract (verifiable_tasks.txt), and
    tags come from each task's own task.toml — the same reader the dashboard
    uses, so the supply vocabulary cannot drift from the pool's real taxonomy.
    """
    verified = task_toml.read_verified_ids(pool_root)
    dims = task_toml.collect_task_dim(pool_root)
    tag_counts: dict[str, int] = {}
    tag_lang_counts: dict[tuple[str, str], int] = {}
    lang_counts: dict[str, int] = {}
    repo_counts: dict[str, int] = {}
    vocabulary: dict[str, set[str]] = {dim: set() for dim in TAG_DIMENSIONS}
    matched = 0
    for task_id in sorted(verified & set(dims)):
        dim = dims[task_id]
        matched += 1
        language = canonical_language(str(dim.get("language") or "unknown"))
        keys = [
            ("language", language),
            ("area", str(dim.get("area") or "").strip().lower() or None),
            ("topic", str(dim.get("topic") or "").strip().lower() or None),
            ("bug_class", str(dim.get("bug_class") or "").strip().lower() or None),
            ("difficulty_tier", difficulty_bin(dim)),
        ]
        for dimension, value in keys:
            if not value or value == "unknown":
                continue
            vocabulary.setdefault(dimension, set()).add(value)
            tag_counts[f"{dimension}:{value}"] = tag_counts.get(f"{dimension}:{value}", 0) + 1
            tag_lang_counts[(f"{dimension}:{value}", language)] = (
                tag_lang_counts.get((f"{dimension}:{value}", language), 0) + 1)
        lang_counts[language] = lang_counts.get(language, 0) + 1
        repo = str(dim.get("repo") or "")
        if repo:
            repo_counts[repo] = repo_counts.get(repo, 0) + 1
    return {
        "root": str(pool_root),
        "verified_tasks": len(verified),
        "tagged_tasks": matched,
        "tag_counts": tag_counts,
        "tag_lang_counts": tag_lang_counts,
        "lang_counts": lang_counts,
        "repo_counts": repo_counts,
        "vocabulary": vocabulary,
    }


def canonicalize_key(key: str, vocabulary: dict[str, set[str]]) -> tuple[str, str] | None:
    """'language:Python' / 'language:py' -> ('language', 'py'); bare values are
    matched against the pool vocabulary, else rejected as unknown."""
    key = str(key).strip()
    if ":" in key:
        dimension, value = key.split(":", 1)
        dimension, value = dimension.strip().lower(), value.strip()
        if dimension not in TAG_DIMENSIONS or not value:
            return None
        if dimension == "language":
            value = canonical_language(value)
        else:
            value = value.strip().lower()
        # difficulty_tier has a closed vocabulary (difficulty_bin can only emit
        # easy/medium/hard); a raw gold difficulty string ('very_hard', '4')
        # is taxonomy drift and can never map to a pool key.
        if dimension == "difficulty_tier" and value not in DIFFICULTY_TIERS:
            return None
        # The evaluator's language-only fallback emits `language:unknown`; an
        # unresolvable value can never be mapped to a config language key, so
        # it must not become a target.
        if not value or value == "unknown":
            return None
        return dimension, value
    lowered = key.strip().lower()
    if lowered in LANGUAGE_ALIASES or canonical_language(lowered) in vocabulary["language"]:
        return "language", canonical_language(lowered)
    for dimension in ("area", "topic", "bug_class", "difficulty_tier"):
        if lowered in vocabulary.get(dimension, set()):
            return dimension, lowered
    return None


# --- freezes (derived from ledger history — no extra state) ---------------------


def historical_freezes(rows: list[dict[str, Any]]) -> tuple[set[str], dict[str, int]]:
    """Tags that may not be re-targeted: explicit futility-freeze closures plus
    per-tag failed-verdict retries (a closed round whose intended tag did not
    move in the expected direction counts as one failed retry for that tag)."""
    frozen: set[str] = set()
    failures: dict[str, int] = {}
    for row in rows:
        verdict = row.get("verdict")
        if not isinstance(verdict, dict):
            continue
        targets = ((row.get("hypothesis") or {}).get("target_tags") or [])
        holdouts = set(((row.get("hypothesis") or {}).get("actuation") or {}).get("holdout_tags")
                       or [])
        if str(verdict.get("closure_type") or "") == "futility-freeze":
            frozen.update(str(t) for t in targets)
        intended = verdict.get("intended_effect")
        tags = intended.get("tags") if isinstance(intended, dict) else None
        if not isinstance(tags, dict):
            continue
        for target in targets:
            key = str(target)
            if key in holdouts or key not in tags:
                continue
            if not tags[key].get("moved_expected", False):
                failures[key] = failures.get(key, 0) + 1
    return frozen, failures


def historical_noise_floor(rows: list[dict[str, Any]]) -> tuple[dict[str, float], float]:
    """§2.1 / §5.4 A2 third operand, stored_identical_config_spread: the max
    measured spread carried by prior rounds' pre-registered hypotheses
    (noise_floor.per_tag_sd / global_between_repeat_sd) — the raw sds, never
    the stored thresholds, so the current round's multiplier still applies."""
    per_tag: dict[str, float] = {}
    global_max = 0.0
    for row in rows:
        floor = (row.get("hypothesis") or {}).get("noise_floor")
        if not isinstance(floor, dict):
            continue
        value = floor.get("global_between_repeat_sd")
        if isinstance(value, (int, float)):
            global_max = max(global_max, float(value))
        raw = floor.get("per_tag_sd")
        if isinstance(raw, dict):
            for key, sd in raw.items():
                if isinstance(sd, (int, float)):
                    key = str(key)
                    per_tag[key] = max(per_tag.get(key, 0.0), float(sd))
    return per_tag, global_max


def prior_round_holdouts(rows: list[dict[str, Any]]) -> tuple[set[str], int | None]:
    """§5.4 A3 step 4: tags held out in the most recent closed round are
    quarantined from this round's directive derivation — their supply counts
    and the report's deltas still carry that round's own actuation output.
    Scope is exactly the next round: round N+2 may re-target the tag."""
    closed = [row for row in rows if isinstance(row.get("verdict"), dict)]
    if not closed:
        return set(), None
    latest = max(closed, key=lambda r: int(r.get("round") or 0))
    holdouts = set(((latest.get("hypothesis") or {}).get("actuation") or {})
                   .get("holdout_tags") or [])
    return {str(t) for t in holdouts}, latest.get("round")


# --- candidate gate --------------------------------------------------------------


def evaluate_candidates(report: dict[str, Any], supply: dict[str, Any],
                        rows: list[dict[str, Any]],
                        args: argparse.Namespace
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sd_by_key = per_tag_sd(report)
    sd_global = global_sd(report)
    overall = overall_pass_rate(report)
    frozen, failures = historical_freezes(rows)
    hist_sd, hist_global = historical_noise_floor(rows)
    quarantined, prior_holdout_round = prior_round_holdouts(rows)
    blocked: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for tag in report["tags"]:
        raw_key = str(tag.get("key") or "")
        if not raw_key:
            continue
        parsed = canonicalize_key(raw_key, supply["vocabulary"])
        if parsed is None:
            blocked.append({"key": raw_key, "reason": "unknown_tag_key"})
            continue
        dimension, value = parsed
        canonical = f"{dimension}:{value}"
        n = tag.get("n")
        delta = tag.get("delta_vs_prev")
        rate = tag.get("pass_rate")
        lp = tag.get("learning_progress")
        sd = max(sd_by_key.get(raw_key, 0.0), sd_by_key.get(canonical, 0.0))
        # §5.4 A2: multiplier x max(tag_sd, global_sd,
        # stored_identical_config_spread, 1/n one-instance resolution). The 1/n
        # term mirrors the report's published noise_floor, so the threshold is
        # never 0.0 — a k=1 / all-identical-repeats report cannot clear every
        # nonzero (or zero) delta by default.
        floor_n = 1.0 / int(n) if isinstance(n, (int, float)) and int(n) > 0 else 1.0
        threshold = args.noise_gate_multiplier * max(
            sd, sd_global, hist_sd.get(canonical, 0.0), hist_global, floor_n)
        supply_n = int(supply["tag_counts"].get(canonical, 0))

        if not isinstance(n, (int, float)) or n < args.min_tag_n:
            blocked.append({"key": raw_key, "reason": "n_too_small", "n": n,
                            "min_tag_n": args.min_tag_n})
            continue
        if canonical in frozen:
            blocked.append({"key": raw_key, "reason": "futility_freeze", "supply": supply_n})
            continue
        if failures.get(canonical, 0) >= FUTILITY_MAX_RETRIES:
            blocked.append({"key": raw_key, "reason": "futility_freeze",
                            "failed_retries": failures[canonical]})
            continue
        if canonical in quarantined:
            # §5.4 A3 step 4 — anti-self-deception control: the prior round
            # held this tag out of its verdict, but this round's supply counts
            # and delta_vs_prev already include the tasks that round mined.
            blocked.append({"key": raw_key, "reason": "holdout_quarantine",
                            "prior_round": prior_holdout_round, "supply": supply_n})
            continue
        if isinstance(rate, (int, float)) and isinstance(lp, (int, float)) \
                and rate >= SATURATED_PASS_RATE and lp <= 0:
            blocked.append({"key": raw_key, "reason": "saturated_zero_lp",
                            "pass_rate": rate, "learning_progress": lp})
            continue
        if supply_n >= args.supply_satisfaction_threshold:
            # Weak-but-supplied: reported, capped at baseline quota (delta 0).
            blocked.append({"key": raw_key, "reason": "already_supplied", "supply": supply_n,
                            "threshold": args.supply_satisfaction_threshold})
            continue
        if delta is None:
            blocked.append({"key": raw_key, "reason": "no_baseline", "supply": supply_n})
            continue
        if abs(float(delta)) < threshold:
            blocked.append({"key": raw_key, "reason": "below_noise_gate", "delta": float(delta),
                            "threshold": threshold, "supply": supply_n})
            continue
        if overall is not None and isinstance(rate, (int, float)) and float(rate) >= overall:
            blocked.append({"key": raw_key, "reason": "not_weak", "pass_rate": float(rate),
                            "overall_pass_rate": overall, "supply": supply_n})
            continue

        candidates.append({
            "key": canonical,
            "raw_key": raw_key,
            "dimension": dimension,
            "value": value,
            "n": int(n),
            "pass_rate": float(rate) if isinstance(rate, (int, float)) else None,
            "delta_vs_prev": float(delta),
            "learning_progress": float(lp) if isinstance(lp, (int, float)) else 0.0,
            "sd": sd,
            "noise_threshold": threshold,
            "supply": supply_n,
        })

    # Rank by learning progress (I29), ties by |delta| then key — fully deterministic.
    candidates.sort(key=lambda c: (-c["learning_progress"], -abs(c["delta_vs_prev"]), c["key"]))
    return candidates, blocked


# --- budget / stability guards ----------------------------------------------------


def largest_remainder(weights: dict[str, float], total: int) -> dict[str, int]:
    """Integer shares summing to `total`, proportional to positive weights."""
    live = {key: max(weight, 0.0) for key, weight in weights.items() if max(weight, 0.0) > 0}
    if not live:
        return {}
    weight_sum = sum(live.values())
    shares = {key: int(total * weight / weight_sum) for key, weight in live.items()}
    remainder = total - sum(shares.values())
    for key in sorted(live, key=lambda k: (-(total * live[k] / weight_sum - shares[k]), k)):
        if remainder <= 0:
            break
        shares[key] += 1
        remainder -= 1
    return shares


def allocate(candidates: list[dict[str, Any]], budget: int,
             tag_cap: int) -> tuple[dict[str, int], int]:
    """Budget across target tags under the per-tag exposure cap."""
    weights = {c["key"]: 1.0 + max(0.0, c["learning_progress"]) for c in candidates}
    shares = largest_remainder(weights, budget)
    cap = max(1, tag_cap)
    overflow = 0
    for key in sorted(shares):
        if shares[key] > cap:
            overflow += shares[key] - cap
            shares[key] = cap
    # Redistribute overflow to tags still under the cap (largest headroom first).
    while overflow > 0:
        room = {k: cap - v for k, v in shares.items() if cap - v > 0}
        if not room:
            break
        for key in sorted(room, key=lambda k: -room[k]):
            if overflow <= 0:
                break
            shares[key] += 1
            overflow -= 1
    return shares, overflow


def language_deltas(candidates: list[dict[str, Any]], shares: dict[str, int],
                    supply: dict[str, Any], threshold: int) -> dict[str, int]:
    """Map per-tag allocations to languages.<lang>.params.max_verified_tasks deltas.

    A language tag maps straight to its language. Any other tag is mined where
    it is most under-supplied: per-language deficit = threshold - in-language
    count (ties split evenly), falling back to pool-size-proportional when no
    language is deficient.
    """
    deltas: dict[str, int] = {}
    for candidate in candidates:
        share = shares.get(candidate["key"], 0)
        if share <= 0:
            continue
        key = candidate["key"]
        if candidate["dimension"] == "language":
            targets = {candidate["value"]: 1.0}
        else:
            deficits = {}
            for lang in supply["lang_counts"]:
                in_lang = supply["tag_lang_counts"].get((key, lang), 0)
                deficits[lang] = max(0, threshold - in_lang)
            if any(deficits.values()):
                targets = dict(deficits)
            else:
                targets = {lang: float(total) for lang, total in supply["lang_counts"].items()}
        for lang, extra in largest_remainder(targets, share).items():
            deltas[lang] = deltas.get(lang, 0) + int(extra)
    return deltas


def baseline_quotas() -> dict[str, int]:
    """Current languages.<lang>.params.max_verified_tasks from the block config."""
    quotas: dict[str, int] = {}
    try:
        with (BLOCK_DIR / "config.yaml").open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        languages = ((((cfg.get("runtime_info") or {}).get("input") or {})
                      .get("languages")) or {})
        for lang, entry in languages.items():
            params = (entry or {}).get("params") or {}
            value = params.get("max_verified_tasks", DEFAULT_BASELINE_QUOTA)
            try:
                quotas[str(lang)] = int(value)
            except (TypeError, ValueError):
                quotas[str(lang)] = DEFAULT_BASELINE_QUOTA
    except (OSError, yaml.YAMLError):
        pass
    return quotas


def apply_slew_limit(deltas: dict[str, int], baselines: dict[str, int],
                     slew_limit: float) -> tuple[dict[str, Any], int]:
    """Clamp every language's quota delta to slew_limit x its baseline quota."""
    quotas: dict[str, Any] = {}
    unallocated = 0
    for lang in sorted(set(deltas) | set(baselines)):
        desired = deltas.get(lang, 0)
        baseline = baselines.get(lang, DEFAULT_BASELINE_QUOTA)
        if desired <= 0:
            if baseline:
                quotas[lang] = {"baseline": baseline, "delta": 0, "new_value": baseline,
                                "clamped": False}
            continue
        limit = max(1, round(slew_limit * max(baseline, 1)))
        clamped = desired > limit
        delta = min(desired, limit)
        unallocated += desired - delta
        quotas[lang] = {"baseline": baseline, "delta": delta, "new_value": baseline + delta,
                        "clamped": clamped}
    return quotas, unallocated


def sample_holdouts(targets: list[str], round_no: int, report_sha: str) -> list[str]:
    """Deterministic holdout sample: sha256(round|report|tag) order, take the
    first ceil(fraction x n). Same inputs -> same holdouts, every time. A lone
    target is never held out — a round must always leave itself something to
    measure, so holdouts only exist from two targets up and never cover all of
    them."""
    if len(targets) < 2:
        return []
    ordered = sorted(targets, key=lambda t: (sha256_text(f"{round_no}|{report_sha}|{t}"), t))
    take = min(int(HOLDOUT_FRACTION * len(ordered) + 0.999999), len(ordered) - 1)
    return ordered[:take]


def repo_exposure_alarms(supply: dict[str, Any], cap: float) -> list[dict[str, Any]]:
    total = sum(supply["repo_counts"].values())
    if total <= 0:
        return []
    return [{"alarm": "repo_exposure", "repo": repo, "share": round(count / total, 4)}
            for repo, count in sorted(supply["repo_counts"].items(), key=lambda kv: -kv[1])
            if count / total > cap]


def tag_entropy_alarms(supply: dict[str, Any], floor: float) -> list[dict[str, Any]]:
    """§5.4 stability guard (bandit-concentration collapse mode): normalized
    entropy of the pool's language-tag distribution — the actuation surface the
    budget is distributed over. Warn only; the caps and slew limit enforce."""
    counts = [count for count in supply.get("lang_counts", {}).values() if count > 0]
    if len(counts) < 2:
        return []
    total = sum(counts)
    entropy = -sum((count / total) * math.log2(count / total) for count in counts)
    normalized = entropy / math.log2(len(counts))
    if normalized >= floor:
        return []
    return [{"alarm": "tag_entropy_floor", "normalized_entropy": round(normalized, 4),
             "floor": floor, "languages": len(counts)}]


def pass_k_diversity_alarms(report: dict[str, Any], floor: float) -> list[dict[str, Any]]:
    """§5.4 stability guard (distribution-narrowing collapse mode), computed
    from the persisted per-repeat rewards: the fraction of tasks whose reward
    varies across repeats. Below the floor the variance gate ranks on label
    noise, so the round warns loudly instead of silently trusting the deltas."""
    tasks = report.get("tasks") if isinstance(report.get("tasks"), dict) else {}
    measured = varying = 0
    for entry in tasks.values():
        if not isinstance(entry, dict):
            continue
        seq = [r for r in (entry.get("per_repeat") or []) if isinstance(r, (int, float))]
        if len(seq) < 2:
            continue
        measured += 1
        if len(set(seq)) > 1:
            varying += 1
    if measured < 2 or varying / measured >= floor:
        return []
    return [{"alarm": "pass_k_diversity", "varying_tasks": varying,
             "measured_tasks": measured, "floor": floor}]


# --- main mode --------------------------------------------------------------------


def plan_directives(report: dict[str, Any], report_path: Path, supply: dict[str, Any],
                    rows: list[dict[str, Any]], args: argparse.Namespace,
                    round_no: int) -> dict[str, Any]:
    candidates, blocked = evaluate_candidates(report, supply, rows, args)
    targets = [c["key"] for c in candidates]
    holdouts = sample_holdouts(targets, round_no, sha256_file(report_path)) if targets else []
    shares, overflow = (allocate(candidates, args.budget,
                                 int(args.tag_exposure_cap * args.budget)) if targets else ({}, 0))
    deltas = language_deltas(candidates, shares, supply, args.supply_satisfaction_threshold)
    quotas, slew_unallocated = apply_slew_limit(deltas, baseline_quotas(), args.slew_limit)
    unallocated = overflow + slew_unallocated
    alarms = repo_exposure_alarms(supply, args.repo_exposure_cap)
    stability = (alarms + tag_entropy_alarms(supply, args.tag_entropy_floor)
                 + pass_k_diversity_alarms(report, args.pass_k_diversity_floor))

    filter_overrides: dict[str, Any] = {}
    for candidate in candidates:
        if candidate["dimension"] == "difficulty_tier":
            # Bounded, documented knob: harder targets need larger diffs to mine.
            filter_overrides["create.min_source_files"] = (
                3 if candidate["value"] == "hard" else 1)
            break

    directives = {
        "round": round_no,
        "source_report_sha256": sha256_file(report_path),
        "language_quotas": quotas,
        "filter_overrides": filter_overrides,
        "skip_pr_files": [DEFAULT_SKIP_FILE],
        "holdout_tags": holdouts,
        "placebo": False,
    }
    hypothesis = {
        "round": round_no,
        "target_tags": targets,
        "expected_direction": "increase",
        "supply_counts": {c["key"]: c["supply"] for c in candidates},
        "noise_floor": {
            "multiplier": args.noise_gate_multiplier,
            "global_between_repeat_sd": global_sd(report),
            "per_tag_sd": {c["key"]: c["sd"] for c in candidates},
            "thresholds": {c["key"]: c["noise_threshold"] for c in candidates},
        },
        "actuation": {
            "budget": args.budget,
            "language_quotas": quotas,
            "exposure_caps": {"repo": args.repo_exposure_cap, "tag": args.tag_exposure_cap},
            "slew_limit": args.slew_limit,
            "holdout_tags": holdouts,
            "unallocated_budget": unallocated,
        },
    }
    summary = {
        "round": round_no,
        "report": str(report_path),
        "pool": supply,
        "targets": candidates,
        "holdout_tags": holdouts,
        "blocked": blocked,
        "directives": directives,
        "hypothesis": hypothesis,
        "repo_exposure_alarms": alarms,
        "stability_alarms": stability,
        "unallocated_budget": unallocated,
    }
    return summary


def enforce_signal_firewall(payload: dict[str, Any], what: str) -> None:
    """§5.6: nothing directive-feeding is written until the evaluator's signal
    firewall passes it for the online context. Invoked as a subprocess — blocks
    never import across boundaries — and fail-closed: a missing guard or a
    nonzero exit refuses the write."""
    if not SIGNAL_GUARD.is_file():
        die(f"signal firewall guard not found: {SIGNAL_GUARD} (fail-closed)")
    proc = subprocess.run(
        [sys.executable, str(SIGNAL_GUARD), "check", "--payload", "-",
         "--context", "online"],
        input=json.dumps(payload), capture_output=True, text=True)
    if proc.returncode == 0:
        return
    detail = (proc.stderr or proc.stdout or "").strip()
    refuse(f"signal firewall refused the {what} (guard exit {proc.returncode}): "
           f"{detail[:400]}")


def pre_register(path: Path, rows: list[dict[str, Any]], summary: dict[str, Any],
                 round_no: int, report_path: Path, directives_file: Path) -> None:
    """Append the hypothesis row, then verify it is readable back — the
    directives write is refused unless this succeeds (no unregistered writes)."""
    row = {
        "round": round_no,
        "created_at": now_iso(),
        "source_report_sha256": summary["directives"]["source_report_sha256"],
        "source_report_path": str(report_path),
        "report_generated_at": str(_report_generated_at(summary)),
        "hypothesis": summary["hypothesis"],
        "gate_blocked": summary["blocked"],
        "stability_alarms": summary.get("stability_alarms") or [],
        "directives_file": str(directives_file),
        "applied_config_diff": None,
        "verdict": None,
    }
    if summary.get("audit_readout"):
        row["audit_readout"] = summary["audit_readout"]
    enforce_signal_firewall(row, "hypothesis ledger row")
    save_ledger(path, rows + [row])
    registered = load_ledger(path)
    matching = [r for r in registered if r.get("round") == round_no
                and (r.get("hypothesis") or {}).get("target_tags") is not None]
    if not matching:
        refuse(f"refusing to write directives: hypothesis for round {round_no} did not "
               f"register in {path}")


def _report_generated_at(summary: dict[str, Any]) -> str:
    # The report is not carried inside the summary; re-read is avoided by
    # stashing it when the summary was built (see run_targeting).
    return summary.get("_report_generated_at") or ""


def run_targeting(args: argparse.Namespace) -> int:
    report_path = Path(args.report)
    if not report_path.is_file():
        die(f"weakness report not found: {report_path}")
    report = load_report(report_path)
    report_sha = sha256_file(report_path)

    ledger_path = Path(args.ledger)
    rows = load_ledger(ledger_path)
    check_no_open_round(rows, report, report_sha, args.force)

    existing_rounds = [int(r.get("round")) for r in rows
                       if isinstance(r.get("round"), int)]
    next_round = max(existing_rounds) + 1 if existing_rounds else 1
    round_no = args.round if args.round is not None else next_round
    if round_no in existing_rounds:
        refuse(f"round {round_no} already exists in {ledger_path}; ledger rows are append-only")

    pool_root = Path(args.pool_root)
    if not pool_root.is_dir():
        die(f"pool root not found: {pool_root}")
    supply = pool_supply(pool_root)
    if supply["verified_tasks"] == 0:
        die(f"no verified task ids found under {pool_root} "
            f"(looked for verifiable_tasks.txt at the root or one level down)")

    summary = plan_directives(report, report_path, supply, rows, args, round_no)
    summary["_report_generated_at"] = str(report.get("generated_at") or "")
    if args.audit_readout:
        # §5.3/§7.6(b): the audit-slice readout of THIS report — stamped into
        # the round's row so the two-part close can compare the closing readout
        # against the baseline one instead of internal-only deltas.
        audit_path = Path(args.audit_readout)
        if not audit_path.is_file():
            die(f"--audit-readout not found: {audit_path}")
        summary["audit_readout"] = {"path": str(audit_path),
                                    "sha256": sha256_file(audit_path)}

    directives = summary["directives"]
    targets = summary["hypothesis"]["target_tags"]
    print(f"Round {round_no} targeting gate — pool {supply['tagged_tasks']} tagged / "
          f"{supply['verified_tasks']} verified task(s) from {pool_root}")
    if summary["blocked"]:
        print(f"{'Blocked tags':<40} {'reason':<22} detail")
        for entry in summary["blocked"]:
            detail = ", ".join(f"{k}={v}" for k, v in entry.items() if k not in ("key", "reason"))
            print(f"  {entry['key']:<38} {entry['reason']:<22} {detail}")
    for alarm in summary["repo_exposure_alarms"]:
        print(f"WARN: repo exposure {alarm['share']:.0%} > {args.repo_exposure_cap:.0%} "
              f"for {alarm['repo']} in the current pool", file=sys.stderr)
    for alarm in summary["stability_alarms"]:
        if alarm.get("alarm") == "repo_exposure":
            continue  # printed above with its dedicated wording
        print(f"WARN: stability guard {alarm.get('alarm')}: "
              f"{json.dumps({k: v for k, v in alarm.items() if k != 'alarm'}, sort_keys=True)}",
              file=sys.stderr)
    if not targets:
        print("No tag cleared the gate — no directives emitted (the loop must survive "
              "its own null; nothing was written)")
        print(json.dumps({"round": round_no, "blocked": summary["blocked"]},
                         indent=2, sort_keys=True))
        return 0

    for candidate in summary["targets"]:
        print(f"  target {candidate['key']:<38} lp={candidate['learning_progress']:+.4f} "
              f"delta={candidate['delta_vs_prev']:+.4f} supply={candidate['supply']}")

    if args.dry_run:
        print("DRY-RUN: nothing written (no ledger append, no directives file)")
        print(json.dumps({"directives": directives, "hypothesis": summary["hypothesis"],
                          "blocked": summary["blocked"], "unallocated_budget":
                          summary["unallocated_budget"]}, indent=2, sort_keys=True,
                         default=str))
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    directives_file = out_dir / f"directives_round_{round_no:03d}.yaml"
    # §5.6: the directives payload itself is directive-feeding — the firewall
    # must pass it before anything is written.
    enforce_signal_firewall(directives, "directives payload")
    # Pre-registration refusal semantics: the hypothesis lands in the ledger
    # first and is read back; only then may the directives file be written.
    pre_register(ledger_path, rows, summary, round_no, report_path, directives_file)
    with directives_file.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(directives, fh, sort_keys=False, allow_unicode=True)
        fh.write("\n")

    print(f"Hypothesis pre-registered: {ledger_path} (round {round_no}, "
          f"{len(targets)} target tag(s), holdout {summary['holdout_tags']})")
    print(f"Directives written:        {directives_file}")
    if summary["unallocated_budget"]:
        print(f"NOTE: {summary['unallocated_budget']} of {args.budget} budget task(s) "
              f"unallocated (exposure caps / slew limits)")
    return 0


# --- close-round -------------------------------------------------------------------


def resolution_floor(report: dict[str, Any]) -> float:
    """1/n one-instance resolution, n = tasks x k pooled observations — the same
    floor the report's own noise_floor = max(tag_sd, global_sd, 1/n) applies,
    so a zero-SD report still cannot certify a 0.0 / rounding-level delta."""
    tasks = report.get("tasks") if isinstance(report.get("tasks"), dict) else {}
    k = report.get("k")
    if not isinstance(k, (int, float)) or k < 1:
        k = max((len(t.get("per_repeat") or []) for t in tasks.values()
                 if isinstance(t, dict)), default=1)
    n = int(len(tasks) * max(int(k), 1))
    return 1.0 / n if n > 0 else 1.0


def task_paired_readout(baseline_report: dict[str, Any], new_report: dict[str, Any]) \
        -> dict[str, Any]:
    """§2.1 'compare paired at task level': discordant per-task counts between
    the two reports' tasks maps. Pooled rates can improve while the majority of
    shared tasks regresses (set churn, big movers); the paired net delta uses a
    sign-noise deadband of 1/sqrt(n_both)."""
    base_map = baseline_report.get("tasks") if isinstance(baseline_report.get("tasks"), dict) else {}
    new_map = new_report.get("tasks") if isinstance(new_report.get("tasks"), dict) else {}
    n_both = improved = regressed = 0
    for task, entry in base_map.items():
        other = new_map.get(task)
        if not isinstance(entry, dict) or not isinstance(other, dict):
            continue
        before, after = entry.get("reward_mean"), other.get("reward_mean")
        if not (isinstance(before, (int, float)) and isinstance(after, (int, float))):
            continue
        n_both += 1
        if after > before:
            improved += 1
        elif after < before:
            regressed += 1
    if not n_both:
        return {}
    deadband = 1.0 / math.sqrt(n_both)
    net = (improved - regressed) / n_both
    return {"n_both": n_both, "improved": improved, "regressed": regressed,
            "delta": round(net, 6), "deadband": round(deadband, 6),
            "direction": ("improved" if net >= deadband
                          else "regressed" if net <= -deadband else "flat")}


def _as_float(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def audited_direction_block(row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """§5.3/§7.6(b): promotion decisions must read the audit slice, not
    internal-only deltas. With --audit-readout the verdict carries the audited
    DIRECTION (closing readout vs the baseline readout stamped at pre-register,
    sha-verified) under the same noise_gate_multiplier deadband — the direction
    decision only: raw audit-slice scores are eval_only under the signal policy
    and never enter the shared ledger. Without it the two-part verdict is
    internal-only — a loud WARN, never a silent pass."""
    if not args.audit_readout:
        print("WARN: closing round without --audit-readout — the verdict is "
              "internal-only (audit-excluded deltas); "
              "wants promotion decisions to read the audited slice",
              file=sys.stderr)
        return {}
    readout_path = Path(args.audit_readout)
    if not readout_path.is_file():
        die(f"--audit-readout not found: {readout_path}")
    try:
        readout = json.loads(readout_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read audit readout {readout_path}: {exc}")
    if not isinstance(readout, dict) or readout.get("schema") != AUDIT_READOUT_SCHEMA:
        die(f"{readout_path} is not a {AUDIT_READOUT_SCHEMA} audit readout")
    block: dict[str, Any] = {"audit_readout_sha256": sha256_file(readout_path)}
    baseline = row.get("audit_readout") if isinstance(row.get("audit_readout"), dict) else {}
    baseline_path = Path(str(baseline.get("path") or ""))
    sha_matches = (baseline_path.is_file() and baseline.get("sha256")
                   and sha256_file(baseline_path) == baseline.get("sha256"))
    if not sha_matches:
        print(f"WARN: no sha-matched baseline audit readout for round {row.get('round')} "
              f"— audited_direction not computable (pass --audit-readout at open "
              f"time too)", file=sys.stderr)
        return block
    try:
        baseline_readout = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"WARN: cannot re-read baseline audit readout {baseline_path} — "
              f"audited_direction not computable", file=sys.stderr)
        return block
    base_value = baseline_readout.get("audited_resolve")
    new_value = readout.get("audited_resolve")
    if not (isinstance(base_value, (int, float)) and isinstance(new_value, (int, float))):
        print("WARN: audit readout(s) carry no numeric audited_resolve — "
              "audited_direction not computable", file=sys.stderr)
        return block
    audited_n = 1
    for candidate in (readout.get("audited_n"), baseline_readout.get("audited_n")):
        try:
            audited_n = max(audited_n, int(candidate))
        except (TypeError, ValueError):
            continue
    deadband = args.noise_gate_multiplier * max(
        _as_float(baseline_readout.get("between_repeat_sd")),
        _as_float(readout.get("between_repeat_sd")),
        1.0 / audited_n)
    delta = float(new_value) - float(base_value)
    # Only the DECISION lands in the git-tracked ledger: audit-slice scores are
    # eval_only under the signal policy (pattern audited_*), and the loop's own
    # firewall must never flag its own ledger. The raw readouts stay in the
    # eval-side files pinned by audit_readout_sha256 for later audit.
    block["promotion_direction"] = {
        "direction": ("improved" if delta >= deadband
                      else "regressed" if delta <= -deadband else "flat"),
    }
    return block


def close_round(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger)
    rows = load_ledger(ledger_path)
    matching = [r for r in rows if r.get("round") == args.round]
    if not matching:
        die(f"round {args.round} not found in {ledger_path}")
    row = matching[0]
    if row.get("verdict") is not None:
        die(f"round {args.round} is already closed "
            f"(verdict={json.dumps(row['verdict'])[:120]}…)")

    hypothesis = row.get("hypothesis") or {}
    targets = [str(t) for t in (hypothesis.get("target_tags") or [])]
    holdouts = set((hypothesis.get("actuation") or {}).get("holdout_tags") or [])
    measured = [t for t in targets if t not in holdouts]

    if args.closure:
        if args.closure not in ("supply-limited-null", "futility-freeze"):
            die(f"unknown closure type {args.closure!r} "
                f"(supply-limited-null | futility-freeze)")
        verdict: dict[str, Any] = {
            "closure_type": args.closure,
            "closed_at": now_iso(),
        }
        if args.closure == "supply-limited-null":
            # Zero-yield deadlock fix (§9): record the observed post-create
            # supply so the null is auditable, not just asserted.
            supply = pool_supply(Path(args.pool_root)) if Path(args.pool_root).is_dir() else {}
            verdict["post_create_supply_counts"] = {
                t: int((supply.get("tag_counts") or {}).get(t, 0)) for t in targets
            }
            above = [t for t, n in verdict["post_create_supply_counts"].items() if n > 0]
            if above:
                print(f"WARN: supply-limited-null asserted but some target tags have pool "
                      f"supply: {above}", file=sys.stderr)
        row["verdict"] = verdict
        enforce_signal_firewall(row, "closed verdict (closure)")
        save_ledger(ledger_path, rows)
        print(f"Round {args.round} closed as {args.closure}; next round may now open")
        return 0

    if not args.new_report:
        die("--close-round needs --new-report (two-part verdict) or "
            "--closure supply-limited-null|futility-freeze")
    new_path = Path(args.new_report)
    new_report = load_report(new_path)
    new_sha = sha256_file(new_path)
    row_sha = str(row.get("source_report_sha256") or "")
    if row_sha and new_sha == row_sha:
        refuse(f"the opening report for round {args.round} cannot also close it "
               f"(sha256 {new_sha[:12]}… matches source_report_sha256); wait for the "
               f"next round's report or close with --closure supply-limited-null")
    baseline_path = Path(row.get("source_report_path") or "")
    if not baseline_path.is_file():
        die(f"baseline report {baseline_path} for round {args.round} is gone; close with "
            f"--closure supply-limited-null instead (history copies live in the "
            f"evaluator's weakness history dir)")
    # The pinned baseline sha is authoritative: the live report path recorded at
    # open time (the evaluator's weakness_report.json) is OVERWRITTEN IN PLACE by
    # every eval round, so re-reading it at close compares the round against
    # itself (every delta reads 0.0 -> false futility-freezes). Resolve the pin
    # against the live path first, then the immutable history copies beside it.
    baseline_source = str(baseline_path)
    if not row_sha:
        print(f"WARN: round {args.round} ledger row has no source_report_sha256; "
              f"trusting the recorded path {baseline_path} as-is (legacy ledger)",
              file=sys.stderr)
    else:
        candidates = [baseline_path] + sorted(
            (baseline_path.parent / "history").glob("weakness_report_*.json"))
        resolved = next(
            (c for c in candidates if c.is_file() and sha256_file(c) == row_sha), None)
        if resolved is None:
            die(f"baseline for round {args.round} no longer matches its pinned sha256 "
                f"{row_sha[:12]}…: {baseline_path} was overwritten and no matching copy "
                f"exists under {baseline_path.parent / 'history'}; close with --closure "
                f"supply-limited-null or restore the pinned report")
        if resolved != baseline_path:
            baseline_source = str(resolved)
            print(f"NOTE: live baseline {baseline_path} was overwritten in place; using "
                  f"sha-matched history copy {resolved} for round {args.round}",
                  file=sys.stderr)
    baseline_report = load_report(Path(baseline_source))
    baseline_overall = overall_pass_rate(baseline_report)
    current_overall = overall_pass_rate(new_report)
    multiplier = args.noise_gate_multiplier
    hist_sd, hist_global = historical_noise_floor(rows)
    sd_base, sd_new = global_sd(baseline_report), global_sd(new_report)
    # The same three-operand floor the open-time gate uses, plus each report's
    # 1/n resolution: a zero-SD round still reads a 0.0 delta as flat.
    mean_deadband = multiplier * max(sd_base, sd_new, hist_global,
                                     resolution_floor(baseline_report),
                                     resolution_floor(new_report))

    mean_direction: dict[str, Any] = {"expected_direction": "increase"}
    if baseline_overall is None or current_overall is None:
        mean_direction.update({"baseline": baseline_overall, "current": current_overall,
                               "delta": None, "direction": "unknown"})
    else:
        delta = current_overall - baseline_overall
        direction = ("improved" if delta >= mean_deadband
                     else "regressed" if delta <= -mean_deadband else "flat")
        mean_direction.update({"baseline": round(baseline_overall, 6),
                               "current": round(current_overall, 6),
                               "delta": round(delta, 6),
                               "deadband": round(mean_deadband, 6),
                               "direction": direction})
    # §2.1: paired-at-task comparison; the paired statistic wins once enough
    # tasks are shared between the two reports.
    paired = task_paired_readout(baseline_report, new_report)
    if paired:
        mean_direction["paired"] = paired
        mean_direction["direction_source"] = ("paired"
                                              if paired["n_both"] >= args.min_tag_n
                                              else "rate")
        if paired["n_both"] >= args.min_tag_n:
            mean_direction["direction"] = paired["direction"]

    # Report keys are canonicalized on both sides: the ledger stores curator
    # keys (language:py) while reports carry evaluator spellings verbatim.
    sd_map_base = {canonical_report_key(k): v for k, v in per_tag_sd(baseline_report).items()}
    sd_map_new = {canonical_report_key(k): v for k, v in per_tag_sd(new_report).items()}
    n_map: dict[str, int] = {}
    for _report in (baseline_report, new_report):
        for key, n in tag_n_map(_report).items():
            n_map[key] = max(n_map.get(key, 0), n)
    tag_results: dict[str, Any] = {}
    moved_count = 0
    for key in measured:
        base_rate = tag_pass_rate(baseline_report, key)
        new_rate = tag_pass_rate(new_report, key)
        deadband = multiplier * max(sd_map_base.get(key, 0.0), sd_map_new.get(key, 0.0),
                                    sd_base, sd_new, hist_sd.get(key, 0.0), hist_global,
                                    1.0 / max(n_map.get(key, 1), 1))
        if base_rate is None or new_rate is None:
            tag_results[key] = {"baseline": base_rate, "current": new_rate, "delta": None,
                                "deadband": round(deadband, 6), "moved_expected": False}
            continue
        delta = new_rate - base_rate
        moved = delta >= deadband
        moved_count += int(moved)
        tag_results[key] = {"baseline": round(base_rate, 6), "current": round(new_rate, 6),
                            "delta": round(delta, 6), "deadband": round(deadband, 6),
                            "moved_expected": moved}
    fraction = (moved_count / len(measured)) if measured else None

    verdict: dict[str, Any] = {
        "mean_direction": mean_direction,
        "intended_effect": {
            "tags": tag_results,
            "fraction_moved_expected": fraction,
            "moved": bool(fraction is not None and fraction >= 0.5),
        },
        "holdout_tags_excluded": sorted(holdouts & set(targets)),
        "closed_at": now_iso(),
        "closed_by_report_sha256": new_sha,
        "baseline_source": baseline_source,
    }
    verdict.update(audited_direction_block(row, args))
    row["verdict"] = verdict
    enforce_signal_firewall(row, "closed verdict (two-part)")
    save_ledger(ledger_path, rows)
    print(f"Round {args.round} verdict: mean={mean_direction.get('direction')} "
          f"intended_moved={row['verdict']['intended_effect']['moved']} "
          f"({moved_count}/{len(measured)} measured tags)")
    return 0


# --- CLI ---------------------------------------------------------------------------


def targeting_config_defaults() -> dict[str, Any]:
    """CLI-overridable defaults from runtime_info.input.targeting in the block
    config — the documented knobs (directives_path, min_tag_n,
    noise_gate_multiplier, supply_satisfaction_threshold) are real
    configuration, not documentation; tuning them in config.yaml must take
    effect without the operator mirroring every value into CLI flags. Explicit
    flags always win."""
    try:
        with open(BLOCK_DIR / "config.yaml", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError:
        return {}
    targeting = (((data.get("runtime_info") or {}).get("input") or {})
                 .get("targeting") or {})
    defaults: dict[str, Any] = {}
    for key, cast in (("min_tag_n", int), ("noise_gate_multiplier", float),
                      ("supply_satisfaction_threshold", int)):
        value = targeting.get(key)
        if value is not None:
            defaults[key] = cast(value)
    report = targeting.get("directives_path")
    if report:
        path = Path(str(report))
        defaults["report"] = str(path if path.is_absolute() else BLOCK_DIR / path)
    return defaults


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", help="weakness_report.json (legoflow.weakness.v1); "
                                     "default: runtime_info.input.targeting.directives_path")
    ap.add_argument("--pool-root", default=str(BLOCK_DIR / "artifacts" / "swe_tasks"),
                    help="curator task pool root (default: %(default)s)")
    ap.add_argument("--ledger", default=str(BLOCK_DIR / "artifacts" / "targeting"
                                            / "hypothesis_ledger.yaml"),
                    help="append-only hypothesis ledger (default: %(default)s)")
    ap.add_argument("--out-dir", default=str(BLOCK_DIR / "artifacts" / "targeting"),
                    help="where directives_round_NNN.yaml is written (default: %(default)s)")
    ap.add_argument("--round", type=int, default=None,
                    help="round number (default: newest ledger round + 1)")
    ap.add_argument("--budget", type=int, default=32,
                    help="max new verified tasks this round may direct (default: %(default)s)")
    ap.add_argument("--min-tag-n", type=int, default=20,
                    help="tags with fewer samples never receive directives (default: %(default)s)")
    ap.add_argument("--noise-gate-multiplier", type=float, default=2.0,
                    help="|delta| must clear multiplier x the noise floor (default: %(default)s)")
    ap.add_argument("--supply-satisfaction-threshold", type=int, default=40,
                    help="tags already supplied this many tasks are not targeted "
                         "(default: %(default)s)")
    ap.add_argument("--slew-limit", type=float, default=0.2,
                    help="max quota change per language per round, as a fraction of its "
                         "baseline quota (default: %(default)s)")
    ap.add_argument("--repo-exposure-cap", type=float, default=0.15,
                    help="pool repo-share alarm threshold (default: %(default)s)")
    ap.add_argument("--tag-exposure-cap", type=float, default=0.25,
                    help="max share of the budget any one tag may receive (default: %(default)s)")
    ap.add_argument("--tag-entropy-floor", type=float, default=0.5,
                    help="§5.4 stability guard: warn when the pool's language-tag "
                         "distribution has normalized entropy below this "
                         "(bandit-concentration collapse) (default: %(default)s)")
    ap.add_argument("--pass-k-diversity-floor", type=float, default=0.25,
                    help="§5.4 stability guard: warn when fewer than this fraction "
                         "of tasks vary across repeats (distribution-narrowing "
                         "collapse) (default: %(default)s)")
    ap.add_argument("--audit-readout", default=None, metavar="PATH",
                    help=f"audit-slice readout ({AUDIT_READOUT_SCHEMA}) for the report: "
                         "at open time it is stamped (path+sha256) into the round's "
                         "ledger row; with --close-round it is the closing readout the "
                         "verdict's audited_direction is computed from")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan without writing ledger or directives")
    ap.add_argument("--force", action="store_true",
                    help="override an open verdict (recorded in stdout as a warning)")
    ap.add_argument("--close-round", action="store_true",
                    help="close a round instead of opening one: fills the verdict of "
                         "--round from --new-report (two-part) or --closure")
    ap.add_argument("--new-report", default=None,
                    help="with --close-round: the round's post-eval weakness report")
    ap.add_argument("--closure", default=None,
                    choices=["supply-limited-null", "futility-freeze"],
                    help="with --close-round: close without a two-part verdict")
    ap.add_argument("--verify", default=None, metavar="DIRECTIVES_YAML",
                    help="verify an existing directives file against --report "
                         "(re-hash source_report_sha256; refuse on divergence) "
                         "instead of opening a round")
    # Config-owned knobs become argparse defaults; explicit flags still win.
    ap.set_defaults(**targeting_config_defaults())
    return ap


def verify_directives(args: argparse.Namespace) -> int:
    """§5.7 step 2 audit hook: a directives file is only trustworthy against
    the exact report it was derived from — re-hash and refuse on drift."""
    directives_path = Path(args.verify)
    report_path = Path(args.report)
    if not directives_path.is_file():
        die(f"directives file not found: {directives_path}")
    if not report_path.is_file():
        die(f"weakness report not found: {report_path}")
    try:
        directives = yaml.safe_load(directives_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        die(f"cannot parse directives {directives_path}: {exc}")
    expected = directives.get("source_report_sha256") if isinstance(directives, dict) else None
    actual = sha256_file(report_path)
    if expected != actual:
        refuse(f"directives {directives_path} were derived from a different report: "
               f"source_report_sha256={expected} != sha256({report_path})={actual}; "
               f"regenerate the directives before applying them")
    print(f"OK: {directives_path} matches {report_path} (source_report_sha256={actual})")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.verify:
        if args.close_round:
            die("--verify cannot be combined with --close-round")
        if not args.report:
            die("--verify requires --report <weakness_report.json>")
        return verify_directives(args)
    if args.close_round:
        if args.round is None:
            die("--close-round requires --round N")
        return close_round(args)
    if not args.report:
        die("--report is required (or use --close-round / --verify)")
    if args.new_report or args.closure:
        die("--new-report/--closure only apply with --close-round")
    return run_targeting(args)


if __name__ == "__main__":
    sys.exit(main())
