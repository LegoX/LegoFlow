#!/usr/bin/env python3
"""Signal firewall guard for the LegoFlow RSI loop (normative).

Checks a JSON payload against blocks/evaluator/signal_policy.yaml and refuses
(non-zero exit, offending-key list) when it carries fields that are not allowed
in the payload's destination context:

  online  - directive-feeding artifacts (weakness report, response matrix rows,
            loop ledger rows, curator directives). Default.
  offline - evaluator-local analysis products / dashboards.
  eval    - evaluator-internal measurement (audit-slice readouts, unblinding).

Field classification lives entirely in signal_policy.yaml (git-tracked, so
loosening it is a reviewable diff, never a runtime accident).

Usage (CLI):
    python3 signal_guard.py check --payload report.json                # online
    python3 signal_guard.py check --payload report.json --context offline
    python3 signal_guard.py check --payload - < payload.json --strict
    python3 signal_guard.py check --payload report.json --policy /path/to/policy.yaml

Exit codes:
    0  payload clean (warnings may still be printed for unknown keys)
    1  usage / IO / policy error
    2  violation: payload carries keys (or enum values) not allowed in context

Importable:
    from signal_guard import SignalPolicy, check_payload
    violations = check_payload({"diff_hunk_overlap": 0.3})   # -> non-empty list
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

import yaml

DEFAULT_POLICY = Path(__file__).resolve().parent.parent / "signal_policy.yaml"

CONTEXTS = ("online", "offline", "eval")
VIOLATION_EXIT = 2
ERROR_EXIT = 1


def _normalize(key: str) -> str:
    """Lowercase and collapse every non-alphanumeric run to a single underscore.

    'C1_file_read' -> 'c1_file_read'; 'diff-hunk overlap' -> 'diff_hunk_overlap'.
    """
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _squash(key: str) -> str:
    """Alphanumeric-only form, so camelCase or space-free aliases still match."""
    return re.sub(r"[^a-z0-9]+", "", key.lower())


class SignalPolicy:
    """Compiled view of signal_policy.yaml."""

    def __init__(self, raw: dict, path: Path):
        self.path = path
        self.raw = raw
        tiers = raw.get("tiers") or {}
        if not isinstance(tiers, dict) or not tiers:
            raise ValueError(f"{path}: no tiers defined")
        self.tiers: dict[str, dict] = {}
        for name, spec in tiers.items():
            names = [_normalize(f) for f in (spec.get("fields") or [])]
            self.tiers[name] = {
                "names": set(names),
                "squashes": {_squash(n) for n in names},
                "patterns": [str(p).lower() for p in (spec.get("patterns") or [])],
                "allowed": tuple(spec.get("allowed_contexts") or ()),
                "description": spec.get("description") or "",
            }
        self.enum_tiers: dict[str, dict[str, set[str]]] = {}
        for list_key, groups in (raw.get("enum_value_tiers") or {}).items():
            compiled = {}
            for tier_name, values in (groups or {}).items():
                compiled[tier_name] = {_normalize(v) for v in (values or [])}
            self.enum_tiers[_normalize(list_key)] = compiled
        # Map-valued fields whose SUB-KEYS are ids or tag labels, not field
        # names (report['tasks'] task ids; noise.per_tag_sd "dim:value" tag
        # keys). Sub-keys of these maps are data and are never classified, so
        # a task id like "x__gold_rush-1" or a tag key like "topic:gold-rush"
        # cannot trip a pattern tier; the values under them are still walked
        # normally.
        self.id_carriers = {_normalize(f) for f in (raw.get("id_carrier_maps") or [])}
        self.strict = bool(raw.get("strict", False))
        self.contexts = raw.get("contexts") or {}
        for name, spec in self.tiers.items():
            if not spec["allowed"]:
                raise ValueError(f"{path}: tier '{name}' has no allowed_contexts")

    # -- classification ----------------------------------------------------
    def classify(self, key: str) -> tuple[str | None, str]:
        """Return (tier, matched_rule) for a field key; (None, 'unknown') if unclassified.

        Explicit field names always win over patterns, across all tiers, so a
        deliberately online field (c5_test_executed) can never be captured by an
        offline catch-all pattern (c[1-4]_*).
        """
        norm = _normalize(key)
        squash = _squash(key)
        for name, spec in self.tiers.items():
            if norm in spec["names"] or squash in spec["squashes"]:
                return name, f"field:{norm}"
        for name, spec in self.tiers.items():
            for pattern in spec["patterns"]:
                if fnmatch.fnmatch(norm, pattern) or fnmatch.fnmatch(squash, pattern):
                    return name, f"pattern:{pattern}"
        return None, "unknown"

    def classify_enum(self, list_key: str, value: str) -> tuple[str | None, str]:
        """Classify an element of a known enum-list field (e.g. flags: ["hack"])."""
        groups = self.enum_tiers.get(_normalize(list_key))
        if not groups:
            return None, "not-an-enum-list"
        norm = _normalize(str(value))
        for tier_name, values in groups.items():
            if norm in values:
                return tier_name, f"enum:{_normalize(list_key)}:{norm}"
        return None, "unknown-enum"

    def allowed(self, tier: str, context: str) -> bool:
        return context in self.tiers[tier]["allowed"]

    # -- payload walk ------------------------------------------------------
    def violations(self, payload, context: str = "online") -> tuple[list[dict], list[dict]]:
        """Walk payload; return (violations, warnings) as sorted lists of dicts."""
        if context not in CONTEXTS:
            raise ValueError(f"unknown context '{context}' (expected one of {CONTEXTS})")
        violations: list[dict] = []
        warnings: list[dict] = []

        def visit(node, path: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    child = f"{path}.{key}" if path else str(key)
                    tier, rule = self.classify(str(key))
                    if tier is None:
                        warnings.append(
                            {"path": child, "key": str(key), "tier": None, "rule": rule}
                        )
                    elif not self.allowed(tier, context):
                        violations.append(
                            {"path": child, "key": str(key), "tier": tier, "rule": rule}
                        )
                    elif (
                        isinstance(value, list)
                        and _normalize(str(key)) in self.enum_tiers
                    ):
                        for item in value:
                            if isinstance(item, str):
                                etier, erule = self.classify_enum(str(key), item)
                                if etier is not None and not self.allowed(etier, context):
                                    violations.append(
                                        {
                                            "path": f"{child}[]={item}",
                                            "key": str(key),
                                            "tier": etier,
                                            "rule": erule,
                                        }
                                    )
                    if (isinstance(value, dict)
                            and _normalize(str(key)) in self.id_carriers):
                        # Id-carrier map: the sub-KEYS are ids / tag labels,
                        # not field names — skip classifying them, but keep
                        # walking their values (nested fields are still
                        # checked normally).
                        for sub_id, sub_value in value.items():
                            visit(sub_value, f"{child}.{sub_id}")
                    else:
                        visit(value, child)
            elif isinstance(node, list):
                for idx, item in enumerate(node):
                    visit(item, f"{path}[{idx}]")

        visit(payload, "")
        violations.sort(key=lambda v: v["path"])
        warnings.sort(key=lambda w: w["path"])
        return violations, warnings


def load_policy(path: Path | str | None = None) -> SignalPolicy:
    policy_path = Path(path) if path else DEFAULT_POLICY
    if not policy_path.is_file():
        raise FileNotFoundError(
            f"signal policy not found at {policy_path} (pass --policy or restore "
            "blocks/evaluator/signal_policy.yaml)"
        )
    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"unparseable signal policy {policy_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"signal policy {policy_path} is not a mapping")
    return SignalPolicy(raw, policy_path)


def check_payload(
    payload,
    policy: SignalPolicy | Path | str | None = None,
    context: str = "online",
    strict: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Check a decoded JSON payload; returns (violations, warnings).

    strict=True (or policy strict: true) promotes unknown keys to violations —
    use it when introducing a new artifact schema.
    """
    pol = load_policy(policy) if not isinstance(policy, SignalPolicy) else policy
    violations, warnings = pol.violations(payload, context=context)
    if strict or pol.strict:
        violations = violations + warnings
        violations.sort(key=lambda v: v["path"])
        warnings = []
    return violations, warnings


def format_findings(findings: list[dict]) -> list[str]:
    return [
        f"  {f['path']}  (key={f['key']!r}, tier={f['tier']}, rule={f['rule']})"
        for f in findings
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="signal_guard",
        description="Check a JSON payload against the evaluator signal firewall policy.",
    )
    parser.add_argument("command", nargs="?", default="check", choices=["check"],
                        help="subcommand (default: check)")
    parser.add_argument("--payload", required=True,
                        help="path to a JSON payload, or '-' for stdin")
    parser.add_argument("--policy", default=None,
                        help=f"policy path (default: {DEFAULT_POLICY})")
    parser.add_argument("--context", default="online", choices=list(CONTEXTS),
                        help="destination context of the payload (default: online)")
    parser.add_argument("--strict", action="store_true",
                        help="treat unknown keys as violations")
    parser.add_argument("--quiet", action="store_true", help="suppress warnings")
    args = parser.parse_args(argv)

    try:
        policy = load_policy(args.policy)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return ERROR_EXIT

    raw = sys.stdin.read() if args.payload == "-" else Path(args.payload).read_text(
        encoding="utf-8"
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: payload is not valid JSON: {exc}", file=sys.stderr)
        return ERROR_EXIT

    try:
        violations, warnings = check_payload(
            payload, policy=policy, context=args.context, strict=args.strict
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return ERROR_EXIT

    if warnings and not args.quiet:
        print(f"WARNING: {len(warnings)} unclassified key(s) (not violations):", file=sys.stderr)
        for line in format_findings(warnings):
            print(line, file=sys.stderr)

    if violations:
        print(
            f"SIGNAL FIREWALL VIOLATION: {len(violations)} field(s) not allowed in "
            f"'{args.context}' payloads (policy: {policy.path}):",
            file=sys.stderr,
        )
        for line in format_findings(violations):
            print(line, file=sys.stderr)
        print(
            "Offending keys: " + ", ".join(sorted({v["key"] for v in violations})),
            file=sys.stderr,
        )
        return VIOLATION_EXIT

    print(f"signal firewall: OK ({args.context})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
