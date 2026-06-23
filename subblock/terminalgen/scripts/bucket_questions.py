#!/usr/bin/env python3
"""Bucket a terminal-lego so_data_r{N}.json into per-domain question files.

A question enters a domain's bucket if its `tags` intersect that domain's
`tag_filter` (from config.yaml). A question may land in multiple buckets; that is
fine — downstream generation/validation dedups by task. Questions matching no
domain fall back to `core-terminal-os`.

Output: artifacts/collected_questions/{domain}_so_data.json, in the same shape
terminal-lego's generator expects ({"metadata": {...}, "questions": [...]}).

Usage:
    python scripts/bucket_questions.py \
        --raw artifacts/collected_questions/_raw_r904/so_data_r904.json \
        --config config.yaml \
        --out artifacts/collected_questions
"""
import argparse
import json
import sys
import time
from pathlib import Path

import yaml

FALLBACK_DOMAIN = "core-terminal-os"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, help="terminal-lego so_data_r{N}.json")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="artifacts/collected_questions")
    args = ap.parse_args()

    raw_path = Path(args.raw)
    if not raw_path.exists():
        print(f"Error: {raw_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(args.config) as f:
        cfg = yaml.safe_load(f) or {}
    domains = cfg.get("runtime_info", {}).get("input", {}).get("domains", {})
    if not domains:
        print("Error: no domains in config", file=sys.stderr)
        sys.exit(1)

    # domain -> set(tags); preserve config order so the fallback is deterministic.
    domain_tags = {d: set(v.get("tag_filter", [])) for d, v in domains.items() if v.get("enabled", True)}
    if not domain_tags:
        print("Error: no enabled domains in config", file=sys.stderr)
        sys.exit(1)
    # Fallback for questions matching no domain: core-terminal-os if enabled, else
    # the first enabled domain (never silently drop questions).
    fallback = FALLBACK_DOMAIN if FALLBACK_DOMAIN in domain_tags else next(iter(domain_tags))

    with open(raw_path) as f:
        raw = json.load(f)
    questions = raw.get("questions", [])

    buckets = {d: [] for d in domain_tags}
    n_fallback = 0
    for q in questions:
        qtags = set(q.get("tags", []))
        matched = [d for d, tags in domain_tags.items() if qtags & tags]
        if not matched:
            matched = [fallback]
            n_fallback += 1
        for d in matched:
            buckets[d].append(q)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    summary = {}
    for d, qs in buckets.items():
        out_file = out_dir / f"{d}_so_data.json"
        payload = {
            "metadata": {
                "total": len(qs),
                "with_answers": sum(1 for q in qs if q.get("accepted_answer")),
                "domain": d,
                "updated": stamp,
            },
            "questions": qs,
        }
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        summary[d] = len(qs)

    print(f"Bucketed {len(questions)} questions into {len(buckets)} domains "
          f"({n_fallback} matched no tag_filter → {fallback}):")
    for d, n in sorted(summary.items(), key=lambda kv: -kv[1]):
        print(f"  {d:24s} {n}")


if __name__ == "__main__":
    main()
