#!/usr/bin/env python3
"""Fill a smoke config's blank credential fields from the environment.

The tracked smoke configs under tests/smoke/<block>/ and
blocks/<block>/tests/smoke/ carry the *structure* of a run — every field the
production config has — but never the values. Endpoints, keys and remote hosts
live outside the repo and are supplied through the runner environment (for
example by an operator-managed env file).

so a checkout can be shared, forked or opened by anyone without leaking them,
while the configs keep tracking schema changes like any other source file.

Contract, and the reason this is safe to run on any config:

  * A field is filled ONLY when its current value is the empty string. A value
    already present — a local loopback URL, a PENDING_SET_BY_* marker another
    script rewrites, a real default — is never touched.
  * A blank field whose env var is unset stays blank. Missing credentials must
    surface as the check's own SKIP, not as a half-substituted config.

Usage:
    python3 scripts/inject_smoke_secrets.py <config.yaml> [<config.yaml> ...]
    python3 scripts/inject_smoke_secrets.py --check <config.yaml>   # report only

Exit codes: 0 = done (or, with --check, nothing missing), 1 = usage/parse error.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("ERROR: PyYAML is required", file=sys.stderr)
    raise SystemExit(1)

# dotted path inside the config  ->  env var suffix that supplies it
#
# Blocks do not share an endpoint: curator drives a PR-evaluation model, tracer
# drives an agent that needs tool calling and a long context, evaluator points at
# a locally served checkpoint. So each name is looked up per block first:
#
#     SMOKE_<BLOCK>_<SUFFIX>     e.g. SMOKE_TRACER_LLM_BASE_URL
#     SMOKE_<SUFFIX>             shared fallback, e.g. SMOKE_LLM_BASE_URL
#
# The block name comes from the config's own meta_info.name, not the file path,
# so a config works the same wherever it is copied to.
FIELD_ENV = {
    "runtime_info.input.llm_api.api_key": "LLM_API_KEY",
    "runtime_info.input.llm_api.api_base_url": "LLM_BASE_URL",
    "runtime_info.input.llm_api.anthropic_base_url": "LLM_ANTHROPIC_BASE_URL",
    "runtime_info.input.llm_api.model": "LLM_MODEL",
    "runtime_info.input.llm_api.pr_model": "LLM_PR_MODEL",
    "runtime_info.input.llm_api.task_model": "LLM_TASK_MODEL",
    "runtime_info.input.credentials.wandb_api_key": "WANDB_API_KEY",
    "runtime_info.input.credentials.hf_token": "HF_TOKEN",
    "meta_info.resources.ip": "REMOTE_IP",
    "meta_info.resources.user": "REMOTE_USER",
    "meta_info.resources.key": "REMOTE_KEY",
    "meta_info.resources.port": "REMOTE_PORT",
    "meta_info.resources.directory": "REMOTE_DIR",
}


def resolve(suffix: str, block: str) -> tuple[str, str]:
    """Return (value, env_var_consulted). Block-specific wins over shared."""
    names = []
    if block:
        names.append(f"SMOKE_{block.upper()}_{suffix}")
    names.append(f"SMOKE_{suffix}")
    for name in names:
        value = os.environ.get(name, "")
        if value != "":
            return value, name
    return "", " or ".join(f"${n}" for n in names)


def get_parent(data: dict, dotted: str):
    """Return (parent_dict, leaf_key) if the path's parent exists, else None."""
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    if not isinstance(cur, dict):
        return None
    return cur, parts[-1]


def assert_not_a_template(path: Path) -> None:
    """Refuse to write secrets back into a tracked smoke template.

    The configs under tests/smoke/ are the tracked, value-free templates. Filling
    one in place would put a live endpoint or key into the working tree, one
    `git add -A` away from the history this whole mechanism exists to keep clean.
    Callers must copy the template to its run location first, then inject there.
    """
    parts = path.resolve().parts
    if "smoke" in parts and "tests" in parts and parts.index("tests") + 1 == parts.index("smoke"):
        raise SystemExit(
            f"ERROR: refusing to inject into the tracked template {path}.\n"
            "  Copy it to the run location first, then inject into the copy:\n"
            "    cp <template> <block>/config.yaml && "
            "python3 scripts/inject_smoke_secrets.py <block>/config.yaml"
        )


def process(path: Path, check_only: bool) -> tuple[list[str], list[str]]:
    """Returns (filled, missing) as lists of dotted paths."""
    if not check_only:
        assert_not_a_template(path)
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    filled, missing = [], []
    block = str(((data.get("meta_info") or {}).get("name") or "")).strip()

    for dotted, suffix in FIELD_ENV.items():
        found = get_parent(data, dotted)
        if found is None:
            continue
        parent, leaf = found
        if leaf not in parent:
            continue
        # Only ever fill a blank. Never overwrite a value the config states.
        if parent[leaf] != "":
            continue
        value, source = resolve(suffix, block)
        if value == "":
            missing.append(f"{dotted} (needs {source})")
            continue
        parent[leaf] = value
        filled.append(f"{dotted} <- ${source}" if source.startswith("SMOKE_") else dotted)

    if filled and not check_only:
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return filled, missing


def main() -> int:
    args = sys.argv[1:]
    check_only = "--check" in args
    targets = [a for a in args if a != "--check"]
    if not targets:
        print(__doc__.strip(), file=sys.stderr)
        return 1

    for target in targets:
        path = Path(target)
        if not path.is_file():
            print(f"ERROR: no such config: {target}", file=sys.stderr)
            return 1
        filled, missing = process(path, check_only)
        verb = "would fill" if check_only else "filled"
        if filled:
            print(f"  {path}: {verb} {len(filled)} field(s): {', '.join(filled)}")
        if missing:
            for m in missing:
                print(f"  {path}: blank and unset — {m}")
        if not filled and not missing:
            print(f"  {path}: nothing to inject")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
