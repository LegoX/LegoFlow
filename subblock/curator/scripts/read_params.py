#!/usr/bin/env python3
"""Read per-language params from config.yaml and output shell variables."""
import argparse
import sys
from pathlib import Path

import yaml


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
    max_verified = params.get("max_verified_tasks", 10)
    print(f"MAX_VERIFIED_TASKS={'' if max_verified == 'all' else max_verified}")


if __name__ == "__main__":
    main()
