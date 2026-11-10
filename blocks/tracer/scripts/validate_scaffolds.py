#!/usr/bin/env python3
"""Validate the optional multi-scaffold campaign knob `agent.scaffolds`.

When `runtime_info.input.agent.scaffolds` is absent, null, or an empty list,
this is single-agent mode: exit 0 with no output so callers can run the helper
unconditionally.

When the knob is set, every entry must name one of the supported pinned
scaffolds and carry non-empty `name`, `version`, and `runtime_image`, with no
duplicate names. `runtime_host_path` may be empty (meaning: mount the runtime
from the image instead of a pre-extracted host directory — the same empty
value `runtime_info.input.agent.runtime_host_path` accepts in single-agent
mode). An unknown name MUST fail loud:
`start.sh`'s per-scaffold case dispatches silently fall through to the
claude-code default, so a typo would otherwise launch the wrong agent under a
misleading job name instead of erroring.

Modes:
  default  validate + print the campaign banner (scaffold list and the expected
           per-scaffold job-name prefixes; the trailing timestamp is stamped at
           each launch)
  --quiet  validate only
  --tsv    validate + print `name<TAB>version<TAB>runtime_image<TAB>runtime_host_path`
           per entry for callers that drive one job per scaffold

Job-name prefixes mirror start.sh's config-derived form
`${dataset}-${name}-${version}-${model}` (or `harbor_job.job_name_prefix` when
set — which start.sh refuses in campaign mode). Runtime env model overrides
(OPENAI_MODEL / ANTHROPIC_MODEL) are applied at launch, not here.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required", file=sys.stderr)
    sys.exit(2)

SUPPORTED_SCAFFOLDS = (
    "custom-claude-code",
    "custom-opencode",
    "custom-openhands-sdk",
)
REQUIRED_FIELDS = ("name", "version", "runtime_image")
# May be empty (= mount from image); anything non-empty must be a usable path.
OPTIONAL_PATH_FIELDS = ("runtime_host_path",)
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_PATH_FIELDS


def load_config(config_arg: str | None, block_dir: Path) -> Path:
    if config_arg:
        return Path(config_arg)
    env_config = os.environ.get("TRAJGEN_CONFIG")
    if env_config:
        return Path(env_config)
    return block_dir / "config.yaml"


def read_scaffolds(config_path: Path) -> Any:
    with config_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    agent = (((data.get("runtime_info") or {}).get("input") or {}).get("agent") or {})
    return agent.get("scaffolds")


def samples_per_task(config_path: Path) -> int:
    """harbor_job.samples_per_task for the banner; start.sh owns its validation."""
    with config_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    harbor_job = ((data.get("runtime_info") or {}).get("input") or {}).get("harbor_job") or {}
    value = harbor_job.get("samples_per_task")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def job_name_prefix(config_path: Path, name: str, version: str) -> str:
    """Mirror start.sh's config-derived job-name prefix for one scaffold."""
    with config_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    rt_input = (data.get("runtime_info") or {}).get("input") or {}
    harbor_job = rt_input.get("harbor_job") or {}
    explicit = harbor_job.get("job_name_prefix")
    if explicit:
        return str(explicit)
    # Same effective-source override start.sh applies: pool: stratified names
    # jobs after the stratified sub-pool, not task_source.dataset_name.
    task_source = rt_input.get("task_source") or {}
    source = task_source.get("dataset_name")
    if task_source.get("pool") == "stratified" and task_source.get("stratified_dataset_dir"):
        source = task_source.get("stratified_dataset_dir")
    dataset = harbor_job.get("dataset") or Path(str(source or "")).name
    agent = rt_input.get("agent") or {}
    model = agent.get("model_name")
    if not model:
        llm_api = rt_input.get("llm_api") or {}
        model = llm_api.get("model") or llm_api.get("upstream_model") or ""
    model_name = str(model).rsplit("/", 1)[-1]
    return f"{dataset}-{name}-{version}-{model_name}"


def validate(scaffolds: Any) -> list[dict[str, str]]:
    """Return the validated entries or raise SystemExit(1) with a loud error."""
    if not isinstance(scaffolds, list):
        raise SystemExit(
            "ERROR: runtime_info.input.agent.scaffolds must be a list of mappings "
            "{name, version, runtime_image, runtime_host_path}"
        )
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(scaffolds):
        if not isinstance(entry, dict):
            raise SystemExit(
                f"ERROR: agent.scaffolds[{index}] must be a mapping with "
                f"{', '.join(REQUIRED_FIELDS)} (got: {type(entry).__name__})"
            )
        name = str(entry.get("name") or "")
        if name not in SUPPORTED_SCAFFOLDS:
            raise SystemExit(
                f"ERROR: agent.scaffolds[{index}].name '{name or '<empty>'}' is not a "
                f"supported scaffold; supported names: {', '.join(SUPPORTED_SCAFFOLDS)}"
            )
        if name in seen:
            raise SystemExit(
                f"ERROR: agent.scaffolds[{index}].name '{name}' is duplicated — "
                "each scaffold may appear at most once per campaign"
            )
        seen.add(name)
        # A two-component version written as a bare YAML number (e.g. `1.20`)
        # loads as a float and str() drops the trailing zero ("1.2") — silently
        # corrupting job names and the --ak version= Harbor arg. Require the
        # quoted string form.
        if isinstance(entry.get("version"), float):
            raise SystemExit(
                f"ERROR: agent.scaffolds[{index}] ({name}) version {entry['version']!r} "
                "loaded as a YAML number — quote it (e.g. \"1.20\") so the job name and "
                "--ak version arg keep every component"
            )
        for field in REQUIRED_FIELDS:
            value = entry.get(field)
            if value is None or str(value).strip() == "":
                raise SystemExit(
                    f"ERROR: agent.scaffolds[{index}] ({name}) is missing a non-empty "
                    f"'{field}'"
                )
        for field in OPTIONAL_PATH_FIELDS:
            if entry.get(field) is None:
                raise SystemExit(
                    f"ERROR: agent.scaffolds[{index}] ({name}) is missing '{field}' "
                    "(set a path, or \"\" to mount the runtime from the image)"
                )
        entries.append({field: str(entry.get(field) or "") for field in ALL_FIELDS})
    return entries


def main() -> int:
    block_dir = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", default="", help="Config path (default: TRAJGEN_CONFIG or block config.yaml)")
    ap.add_argument("--quiet", action="store_true", help="Validate only; print nothing on success")
    ap.add_argument(
        "--tsv",
        action="store_true",
        help="Print 'name\\tversion\\truntime_image\\truntime_host_path' per entry",
    )
    args = ap.parse_args()

    config_path = load_config(args.config, block_dir)
    scaffolds = read_scaffolds(config_path)
    if scaffolds is None or scaffolds == []:
        return 0
    entries = validate(scaffolds)

    if args.tsv:
        for entry in entries:
            print("\t".join(entry[field] for field in ALL_FIELDS))
        return 0
    if args.quiet:
        return 0

    samples = samples_per_task(config_path)
    print("=== tracer multi-scaffold campaign ===")
    print(f"Scaffolds:  {len(entries)} (sequential jobs over the same staged batch; exclusions are")
    print("            resolved once at launch and reused, so every scaffold runs the same batch)")
    if samples > 1:
        print(f"Samples:    {samples} per scaffold ({len(entries) * samples} jobs in total; job names gain -s1 .. -s{samples})")
    for index, entry in enumerate(entries, start=1):
        prefix = job_name_prefix(config_path, entry["name"], entry["version"])
        runtime = (
            f"{entry['runtime_image']} ({entry['runtime_host_path']})"
            if entry["runtime_host_path"]
            else f"{entry['runtime_image']} (mounted from the image)"
        )
        print(
            f"  {index}. {entry['name']} {entry['version']}\n"
            f"     runtime: {runtime}\n"
            f"     job name: {prefix}-<timestamp>"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
