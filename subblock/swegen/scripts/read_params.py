#!/usr/bin/env python3
"""Read per-language params from inputs.yaml and output shell variables."""
import argparse
import sys
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", required=True, help="Language key (py, js, ts, go, c, cpp, java, rust)")
    parser.add_argument("--inputs-yaml", default="inputs.yaml", help="Path to inputs.yaml")
    args = parser.parse_args()

    yaml_path = Path(args.inputs_yaml)
    if not yaml_path.exists():
        print(f"Error: {yaml_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(yaml_path) as f:
        config = yaml.safe_load(f)

    langs = config.get("languages", {})
    if args.lang not in langs:
        print(f"Error: language '{args.lang}' not found in {yaml_path}", file=sys.stderr)
        sys.exit(1)

    params = langs[args.lang].get("params", {})
    print(f"TIMEOUT={params.get('timeout', 3200)}")
    print(f"CC_TIMEOUT={params.get('cc_timeout', 2400)}")
    print(f"N_CONCURRENT={params.get('n_concurrent', 16)}")


if __name__ == "__main__":
    main()
