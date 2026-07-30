#!/usr/bin/env python3
"""Read per-language params from config.yaml and output shell variables."""
import argparse
import shlex
import sys
from pathlib import Path

import yaml

DEFAULT_SWE_TASKS_DIR = "artifacts/swe_tasks"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", required=True, help="Language key (py, js, ts, go, c, cpp, java, rust)")
    parser.add_argument("--config-yaml", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    yaml_path = Path(args.config_yaml)
    if not yaml_path.exists():
        print(f"Error: {yaml_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(yaml_path) as f:
        config = yaml.safe_load(f)

    # Navigate to runtime_info.input.languages
    runtime_info = config.get("runtime_info", {})
    input_config = runtime_info.get("input", {})
    langs = input_config.get("languages", {})

    if args.lang not in langs:
        print(f"Error: language '{args.lang}' not found in {yaml_path}", file=sys.stderr)
        sys.exit(1)

    lang_config = langs[args.lang]
    params = lang_config.get("params", {})
    print(f"TIMEOUT={params.get('timeout', 3200)}")
    print(f"CC_TIMEOUT={params.get('cc_timeout', 2400)}")
    print(f"N_CONCURRENT={params.get('n_concurrent', 16)}")
    # `all` means no cap. swegen's --max-pr defaults to None = every entry, so
    # the wrapper must OMIT the flag; emitting "" made ${VAR:-5000} cap it at 5000.
    max_verified = params.get("max_verified_tasks", 10)
    print(f"MAX_VERIFIED_TASKS={'all' if max_verified == 'all' else max_verified}")

    # Emitted resolved so create_<lang>.sh uses the declared output pool
    # instead of hardcoding artifacts/swe_tasks.
    output = (config.get("runtime_info") or {}).get("output") or {}
    swe_tasks_dir = ((output.get("swe_tasks_dir") or {}).get("path")
                     or DEFAULT_SWE_TASKS_DIR)
    resolved = Path(swe_tasks_dir)
    if not resolved.is_absolute():
        resolved = yaml_path.resolve().parent / resolved
    print(f"SWE_TASKS_DIR={shlex.quote(str(resolved))}")


if __name__ == "__main__":
    main()
