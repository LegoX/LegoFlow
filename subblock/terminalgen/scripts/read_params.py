#!/usr/bin/env python3
"""Read per-domain params from config.yaml and output shell variables.

Usage:
    eval $(python scripts/read_params.py --domain security-cryptography --config-yaml config.yaml)
    echo $GEN_WORKERS $VAL_WORKERS $VAL_TIMEOUT $TAG_FILTER
"""
import argparse
import sys
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True, help="Domain key (e.g. security-cryptography)")
    parser.add_argument("--config-yaml", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    yaml_path = Path(args.config_yaml)
    if not yaml_path.exists():
        print(f"Error: {yaml_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(yaml_path) as f:
        config = yaml.safe_load(f)

    domains = config.get("runtime_info", {}).get("input", {}).get("domains", {})

    if args.domain not in domains:
        print(f"Error: domain '{args.domain}' not found in {yaml_path}", file=sys.stderr)
        sys.exit(1)

    domain_config = domains[args.domain]
    params = domain_config.get("params", {})
    tag_filter = domain_config.get("tag_filter", [])
    print(f"GEN_WORKERS={params.get('gen_workers', 4)}")
    print(f"VAL_WORKERS={params.get('val_workers', 4)}")
    print(f"VAL_TIMEOUT={params.get('val_timeout', 300)}")
    # Comma-joined tag list (used by bucketing/diagnostics).
    print(f"TAG_FILTER={','.join(tag_filter)}")


if __name__ == "__main__":
    main()
