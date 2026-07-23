#!/usr/bin/env python3
"""Validate block config.yaml files against the block contract.

Usage:
  python3 scripts/validate_config.py --root <repo_root>
      Validate the root config plus every subblock, including cross-block
      dependency resolution.
  python3 scripts/validate_config.py --block <block_dir> [--config <alt.yaml>]
      Validate one block and its outgoing dependencies (sibling configs are
      read but not validated). --config substitutes an overlay config file
      (smoke tests) for the block's own config.yaml.

Output: one finding per line, "[OK]|[WARN]|[FAIL] <label> <message>".
Exit 0 iff no FAIL findings.

Contract summary (full text: .claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md):
  - Top-level sections: meta_info and runtime_info only. Anything else — notably
    the legacy `status:`, `evolving:`, and top-level `environment:` — is an error.
  - Every block declares `meta_info.dependencies: {from: {...}, to: {...}}` (both
    keys always present; use {} for a direction with no edges):
      - `from` keys are dot-paths into this block's own runtime_info.input; values
        are either "<src>.output.<key>" or
        {from: "<src>.output.<key>", when: {...}, required: bool}.
      - `to` keys are this block's own runtime_info.output keys; values are either
        "<consumer>.input.<path>" or {to: "<consumer>.input.<path>", when: {...}}.
        `to.when` keys are fully-qualified `<consumer>.input.<path>` (the condition
        lives on the consumer's own state, not this block's).
    Each edge is declared independently by both ends (consumer's `from`, producer's
    `to`); the validator cross-checks the two declarations for drift
    (`dep:link-mismatch`).
  - The root config's subblocks entries carry roles only, never dependencies.
  - runtime_info.input fill markers: `human` = must fill (FAIL until replaced);
    "" = auto-derived or supplied via env/file; anything else is a real value.
  - runtime_info.output entries are mappings with `path` (static) and/or
    `value` (run-produced, null until a run writes it).
"""

import argparse
import os
import re
import sys

try:
    import yaml
except ImportError:
    print("[FAIL] schema:parse-error PyYAML is required (pip install pyyaml)")
    sys.exit(1)

ALLOWED_TOP_LEVEL = {"meta_info", "runtime_info"}
PLACEHOLDER_PATTERNS = [
    re.compile(r"^<.*>$"),
    re.compile(r"REPLACE_ME"),
    re.compile(r"YOUR_API_KEY", re.IGNORECASE),
    re.compile(r"^changeme$", re.IGNORECASE),
    re.compile(r"^xxx+$", re.IGNORECASE),
    re.compile(r"ghp_YOUR_TOKEN_HERE"),
]

findings = []


def emit(level, label, msg):
    findings.append((level, label, msg))
    print(f"[{level}] {label} {msg}")


def ok(label, msg):
    emit("OK", label, msg)


def warn(label, msg):
    emit("WARN", label, msg)


def fail(label, msg):
    emit("FAIL", label, msg)


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def dot_get(mapping, dotted):
    """Walk a dot-path through nested dicts; returns (found, value)."""
    node = mapping
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def iter_leaves(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from iter_leaves(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_leaves(v, f"{prefix}[{i}]")
    else:
        yield prefix, node


def parse_ref(ref, expected_middle):
    """'<block>.<expected_middle>.<rest>' -> (block, rest) or None."""
    if not isinstance(ref, str):
        return None
    parts = ref.split(".", 2)
    if len(parts) != 3 or parts[1] != expected_middle or not parts[0] or not parts[2]:
        return None
    return parts[0], parts[2]


def parse_output_ref(ref):
    """'<src>.output.<key>' -> (src, key) or None."""
    return parse_ref(ref, "output")


def parse_input_ref(ref):
    """'<consumer>.input.<path>' -> (consumer, path) or None."""
    return parse_ref(ref, "input")


def resolve_output(entry):
    """Return the handed-off value of a runtime_info.output entry, or None.

    `value` wins when present and non-null; `path` is the static fallback.
    """
    if not isinstance(entry, dict):
        return entry if entry is not None else None
    if entry.get("value") is not None:
        return entry["value"]
    if entry.get("path"):
        return entry["path"]
    return None


def sibling_config_path(block_dir, src_name):
    return os.path.join(os.path.dirname(os.path.abspath(block_dir)), src_name, "config.yaml")


def load_sibling(block_dir, other_name, is_root, sibling_cache):
    """Load another block's config by name, using/populating sibling_cache."""
    if is_root:
        cfg_path = os.path.join(block_dir, "subblock", other_name, "config.yaml")
    else:
        cfg_path = sibling_config_path(block_dir, other_name)
    if sibling_cache is not None and cfg_path in sibling_cache:
        return cfg_path, sibling_cache[cfg_path]
    cfg = None
    if os.path.isfile(cfg_path):
        try:
            cfg = load_yaml(cfg_path)
        except yaml.YAMLError:
            cfg = None
    if sibling_cache is not None:
        sibling_cache[cfg_path] = cfg
    return cfg_path, cfg


def check_block(block_dir, config_path=None, is_root=False, sibling_cache=None, schema_only=False):
    """Validate one block config. Returns the parsed config (or None)."""
    name = "root" if is_root else os.path.basename(os.path.normpath(block_dir))
    cfg_path = config_path or os.path.join(block_dir, "config.yaml")
    label_prefix = f"{name}:" if not is_root else "root:"

    if not os.path.isfile(cfg_path):
        fail("schema:parse-error", f"{label_prefix} config not found at {cfg_path}")
        return None
    try:
        cfg = load_yaml(cfg_path)
    except yaml.YAMLError as exc:
        fail("schema:parse-error", f"{label_prefix} {exc}")
        return None
    if not isinstance(cfg, dict):
        fail("schema:parse-error", f"{label_prefix} config is not a mapping")
        return None
    ok("schema:parse", f"{label_prefix} config parses")

    # --- top-level shape ---
    for section in ("meta_info", "runtime_info"):
        if section not in cfg:
            fail("schema:missing-section", f"{label_prefix} missing top-level `{section}`")
    if "status" in cfg:
        fail("schema:legacy-status", f"{label_prefix} top-level `status:` is retired — live state lives in artifacts/index.yaml")
    if "evolving" in cfg:
        fail("schema:legacy-evolving", f"{label_prefix} top-level `evolving:` is retired — nothing consumes it; remove the section")
    for key in cfg:
        if key in ("status", "evolving"):
            continue  # already reported as legacy sections
        if key not in ALLOWED_TOP_LEVEL:
            fail("schema:unknown-toplevel", f"{label_prefix} unexpected top-level key `{key}` (allowed: meta_info, runtime_info)")

    meta = cfg.get("meta_info") or {}
    runtime = cfg.get("runtime_info") or {}
    rt_input = runtime.get("input") or {}
    rt_output = runtime.get("output") or {}

    # --- name matches directory (subblocks only; root dir name is free) ---
    if not is_root:
        declared = meta.get("name")
        if declared != name:
            fail("schema:name-mismatch", f"{label_prefix} meta_info.name `{declared}` != directory name `{name}`")
        else:
            ok("schema:name", f"{label_prefix} meta_info.name matches directory")

    # --- root: subblocks carry roles only, and children exist ---
    if is_root:
        subblocks = meta.get("subblocks") or {}
        for child, spec in subblocks.items():
            if isinstance(spec, dict) and "dependencies" in spec:
                fail("schema:root-wiring", f"root: subblocks.{child} declares `dependencies` — wiring lives in the leaf block's own meta_info.dependencies")
            child_cfg = os.path.join(block_dir, "subblock", child, "config.yaml")
            if not os.path.isfile(child_cfg):
                fail("tree:missing-child", f"root: declared subblock `{child}` has no config at subblock/{child}/config.yaml")
        if subblocks:
            ok("tree:subblocks", f"root: {len(subblocks)} subblocks declared")

    # --- dependencies declaration: {from: {...}, to: {...}}, both keys required ---
    deps_raw = meta.get("dependencies", None)
    deps_from, deps_to = {}, {}
    if (
        not isinstance(deps_raw, dict)
        or set(deps_raw.keys()) != {"from", "to"}
        or not isinstance(deps_raw.get("from"), dict)
        or not isinstance(deps_raw.get("to"), dict)
    ):
        fail("dep:bad-shape", f"{label_prefix} meta_info.dependencies must be a mapping with exactly `from` and `to` keys, each a mapping (use {{}} for a direction with no edges)")
    else:
        deps_from = deps_raw["from"]
        deps_to = deps_raw["to"]
        if not deps_from and not deps_to:
            ok("dep:none", f"{label_prefix} no dependencies in either direction (explicit from: {{}}, to: {{}})")

    # --- from: this block's own upstream hand-offs ---
    for dep_key, dep_val in deps_from.items():
        # normalize string vs dict form
        if isinstance(dep_val, str):
            ref, when, required = dep_val, None, True
        elif isinstance(dep_val, dict):
            ref = dep_val.get("from")
            when = dep_val.get("when")
            required = dep_val.get("required", True)
        else:
            fail("dep:bad-ref", f"{label_prefix} dependencies.from.{dep_key} must be a `<src>.output.<key>` string or a {{from, when, required}} mapping")
            continue

        # key must be a dot-path into this block's own runtime_info.input
        found, consumer_val = dot_get(rt_input, dep_key)
        if not found:
            fail("dep:bad-key", f"{label_prefix} dependencies.from key `{dep_key}` is not a path in this block's runtime_info.input")

        parsed = parse_output_ref(ref)
        if parsed is None:
            fail("dep:bad-ref", f"{label_prefix} dependencies.from.{dep_key}: `{ref}` is not of the form <src>.output.<key>")
            continue
        src, out_key = parsed

        # producer config must exist and declare the output key (even for
        # inactive/optional deps — a dangling ref is always an authoring error)
        src_cfg_path, src_cfg = load_sibling(block_dir, src, is_root, sibling_cache)
        if src_cfg is None:
            fail("dep:bad-ref", f"{label_prefix} dependencies.from.{dep_key}: producer block `{src}` has no readable config at {src_cfg_path}")
            continue
        src_outputs = (src_cfg.get("runtime_info") or {}).get("output") or {}
        if out_key not in src_outputs:
            fail("dep:bad-ref", f"{label_prefix} dependencies.from.{dep_key}: `{src}.output.{out_key}` does not exist in {src}'s runtime_info.output")
            continue

        # cross-check: the producer's own `to` should declare this exact edge back
        src_deps = (src_cfg.get("meta_info") or {}).get("dependencies") or {}
        src_to = src_deps.get("to") if isinstance(src_deps, dict) else None
        back_ref = None
        if isinstance(src_to, dict):
            to_entry = src_to.get(out_key)
            if isinstance(to_entry, str):
                back_ref = to_entry
            elif isinstance(to_entry, dict):
                back_ref = to_entry.get("to")
        if back_ref != f"{name}.input.{dep_key}":
            warn("dep:link-mismatch", f"{label_prefix} dependencies.from.{dep_key} -> {src}.output.{out_key}, but {src}'s dependencies.to.{out_key} does not point back to {name}.input.{dep_key}")

        # `when` gate: dep only enforced while the named inputs hold the named values
        if when is not None:
            if not isinstance(when, dict):
                fail("dep:bad-ref", f"{label_prefix} dependencies.from.{dep_key}: `when` must be a mapping of input dot-path -> expected value")
                continue
            active = all(dot_get(rt_input, k) == (True, v) for k, v in when.items())
            if not active:
                ok("dep:inactive", f"{label_prefix} dependencies.from.{dep_key} inactive (when {when} does not match current input)")
                continue

        resolved = resolve_output(src_outputs[out_key])
        if resolved is None:
            if required:
                fail("dep:unresolved", f"{label_prefix} dependencies.from.{dep_key}: `{src}.output.{out_key}` has neither a non-null `value` nor a `path` yet")
            else:
                warn("dep:unresolved", f"{label_prefix} dependencies.from.{dep_key}: optional upstream `{src}.output.{out_key}` not produced yet")
            continue
        ok("dep:resolved", f"{label_prefix} dependencies.from.{dep_key} -> {src}.output.{out_key} = {resolved}")

        # path consistency: consumer's configured value should live under the
        # producer's declared output path (catches renamed-block stale paths)
        producer_entry = src_outputs[out_key]
        producer_path = producer_entry.get("path") if isinstance(producer_entry, dict) else None
        if (
            producer_path
            and isinstance(consumer_val, str)
            and "/" in consumer_val
            and "://" not in consumer_val
        ):
            producer_abs = os.path.abspath(os.path.join(os.path.dirname(src_cfg_path), producer_path))
            consumer_abs = os.path.abspath(os.path.join(block_dir, consumer_val))
            if not (consumer_abs == producer_abs or consumer_abs.startswith(producer_abs + os.sep)):
                warn("dep:path-mismatch", f"{label_prefix} runtime_info.input.{dep_key} = `{consumer_val}` resolves outside {src}'s declared output path `{producer_path}`")

    # --- to: this block's own downstream hand-offs ---
    for out_key, to_val in deps_to.items():
        # normalize string vs dict form
        if isinstance(to_val, str):
            to_ref, to_when = to_val, None
        elif isinstance(to_val, dict):
            to_ref = to_val.get("to")
            to_when = to_val.get("when")
        else:
            fail("dep:bad-ref", f"{label_prefix} dependencies.to.{out_key} must be a `<consumer>.input.<path>` string or a {{to, when}} mapping")
            continue

        if out_key not in rt_output:
            fail("dep:bad-key", f"{label_prefix} dependencies.to key `{out_key}` is not declared in this block's runtime_info.output")

        parsed = parse_input_ref(to_ref)
        if parsed is None:
            fail("dep:bad-ref", f"{label_prefix} dependencies.to.{out_key}: `{to_ref}` is not of the form <consumer>.input.<path>")
            continue
        consumer, consumer_path = parsed

        consumer_cfg_path, consumer_cfg = load_sibling(block_dir, consumer, is_root, sibling_cache)
        if consumer_cfg is None:
            fail("dep:bad-ref", f"{label_prefix} dependencies.to.{out_key}: consumer block `{consumer}` has no readable config at {consumer_cfg_path}")
            continue
        consumer_input = (consumer_cfg.get("runtime_info") or {}).get("input") or {}
        found, _ = dot_get(consumer_input, consumer_path)
        if not found:
            fail("dep:bad-ref", f"{label_prefix} dependencies.to.{out_key}: `{consumer}.input.{consumer_path}` is not a path in {consumer}'s runtime_info.input")
            continue

        # cross-check: the consumer's own `from` should declare this exact edge back
        consumer_deps = (consumer_cfg.get("meta_info") or {}).get("dependencies") or {}
        consumer_from = consumer_deps.get("from") if isinstance(consumer_deps, dict) else None
        back_ref = None
        if isinstance(consumer_from, dict):
            from_entry = consumer_from.get(consumer_path)
            if isinstance(from_entry, str):
                back_ref = from_entry
            elif isinstance(from_entry, dict):
                back_ref = from_entry.get("from")
        if back_ref != f"{name}.output.{out_key}":
            warn("dep:link-mismatch", f"{label_prefix} dependencies.to.{out_key} -> {consumer}.input.{consumer_path}, but {consumer}'s dependencies.from.{consumer_path} does not point back to {name}.output.{out_key}")

        # `to.when` gate: keys are fully-qualified <consumer>.input.<path> (the
        # condition lives on the consumer's own state, not this block's)
        active = True
        if to_when is not None:
            if not isinstance(to_when, dict):
                fail("dep:bad-ref", f"{label_prefix} dependencies.to.{out_key}: `when` must be a mapping of <consumer>.input.<path> -> expected value")
                active = False
            else:
                for cond_key, cond_val in to_when.items():
                    cond_parsed = parse_input_ref(cond_key)
                    if cond_parsed is None or cond_parsed[0] != consumer:
                        fail("dep:bad-ref", f"{label_prefix} dependencies.to.{out_key}: `when` key `{cond_key}` must be `{consumer}.input.<path>`")
                        active = False
                        continue
                    _, cond_path = cond_parsed
                    if dot_get(consumer_input, cond_path) != (True, cond_val):
                        active = False
        if to_when is not None:
            if active:
                ok("dep:to-declared", f"{label_prefix} dependencies.to.{out_key} -> {consumer}.input.{consumer_path} (active: when {to_when} matches)")
            else:
                ok("dep:to-inactive", f"{label_prefix} dependencies.to.{out_key} inactive (when {to_when} does not match {consumer}'s current input)")
        else:
            ok("dep:to-declared", f"{label_prefix} dependencies.to.{out_key} -> {consumer}.input.{consumer_path}")

    # --- input fill markers ---
    # `human` markers are expected on a fresh clone; --schema-only (CI schema
    # tests) downgrades them to warnings. Legacy placeholders always fail.
    for leaf_path, leaf_val in iter_leaves(rt_input):
        if leaf_val == "human":
            if schema_only:
                warn("input:unfilled", f"{label_prefix} runtime_info.input.{leaf_path} is `human` — must be filled before a run")
            else:
                fail("input:unfilled", f"{label_prefix} runtime_info.input.{leaf_path} is `human` — fill it before running")
        elif isinstance(leaf_val, str):
            for pat in PLACEHOLDER_PATTERNS:
                if pat.search(leaf_val):
                    fail("input:placeholder", f"{label_prefix} runtime_info.input.{leaf_path} = `{leaf_val}` is a legacy placeholder — use `human` for must-fill fields")
                    break

    # --- output shape ---
    for out_key, entry in rt_output.items():
        if not isinstance(entry, dict) or ("path" not in entry and "value" not in entry):
            warn("output:shape", f"{label_prefix} runtime_info.output.{out_key} should be a mapping with `path` (static) and/or `value` (run-produced)")

    return cfg


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--root", metavar="DIR", help="repo root containing config.yaml and subblock/")
    target.add_argument("--block", metavar="DIR", help="one block directory (e.g. subblock/tracer)")
    parser.add_argument("--config", metavar="FILE", help="overlay config file to validate instead of <block>/config.yaml")
    parser.add_argument("--schema-only", action="store_true", help="downgrade `human` fill markers to warnings (CI schema tests on fresh-clone configs)")
    args = parser.parse_args()

    if args.config and not args.block:
        parser.error("--config requires --block")

    sibling_cache = {}
    if args.root:
        root_dir = os.path.abspath(args.root)
        root_cfg = check_block(root_dir, is_root=True, sibling_cache=sibling_cache, schema_only=args.schema_only)
        children = ((root_cfg or {}).get("meta_info") or {}).get("subblocks") or {}
        subblock_dir = os.path.join(root_dir, "subblock")
        names = list(children) or sorted(
            d for d in (os.listdir(subblock_dir) if os.path.isdir(subblock_dir) else [])
            if os.path.isfile(os.path.join(subblock_dir, d, "config.yaml"))
        )
        for child in names:
            child_dir = os.path.join(subblock_dir, child)
            if os.path.isdir(child_dir):
                check_block(child_dir, sibling_cache=sibling_cache, schema_only=args.schema_only)
    else:
        check_block(os.path.abspath(args.block), config_path=args.config, sibling_cache=sibling_cache, schema_only=args.schema_only)

    n_fail = sum(1 for level, _, _ in findings if level == "FAIL")
    n_warn = sum(1 for level, _, _ in findings if level == "WARN")
    print(f"validate_config: {n_fail} FAIL, {n_warn} WARN, {len(findings) - n_fail - n_warn} OK")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
