#!/usr/bin/env python3
"""Merge verified terminal tasks from all domains into one flat directory.

Optional convenience step: reads each domain's verifiable_tasks.txt (the
authoritative manifest) and copies every listed task into a flat
artifacts/merged_terminal_tasks/ directory, **verbatim** — no format change.
Tasks stay in terminal-lego's native v1.0 schema, which is what downstream
trajgen/sft/eval consume (harbor's task loader reads v1.0 directly).

Merged ids are prefixed with the domain (`{domain}__{task_id}`) so per-domain
`task_00000` ids don't collide in the flat directory.

Downstream can equally read the per-domain dirs in place
(artifacts/terminal_tasks/{domain}-tl/), gated by verifiable_tasks.txt; this
merge just offers a single flat root for tools that expect one.
"""

import shutil
from pathlib import Path

DOMAINS = [
    "core-terminal-os", "versioning-containers", "networking-services",
    "file-text-processing", "python-ecosystem", "ml-data", "databases-storage",
    "web-automation-apis", "security-cryptography", "debugging-reliability",
    "algorithms-concurrency", "media-scientific", "build-editor-tooling",
]
BLOCK_DIR = Path(__file__).resolve().parents[1]
TASKS_ROOT = BLOCK_DIR / "artifacts" / "terminal_tasks"
OUTPUT_DIR = BLOCK_DIR / "artifacts" / "merged_terminal_tasks"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    merged_manifest = []
    stats = {}
    total = 0

    for domain in DOMAINS:
        domain_dir = TASKS_ROOT / f"{domain}-tl"
        vt_file = domain_dir / "verifiable_tasks.txt"
        if not vt_file.exists():
            stats[domain] = 0
            continue

        task_ids = [ln.strip() for ln in vt_file.read_text().splitlines() if ln.strip()]
        copied = 0
        for task_id in task_ids:
            src = domain_dir / task_id
            if not src.is_dir():
                print(f"  WARN: {domain}/{task_id} not found, skipping")
                continue
            merged_id = f"{domain}__{task_id}"  # domain prefix avoids task_00000 collisions
            dst = OUTPUT_DIR / merged_id
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            merged_manifest.append(merged_id)
            copied += 1

        stats[domain] = copied
        total += copied

    (OUTPUT_DIR / "verifiable_tasks.txt").write_text(
        "\n".join(merged_manifest) + ("\n" if merged_manifest else "")
    )

    print(f"\n{'Domain':<24} {'Merged':<10}")
    print("-" * 34)
    for domain in DOMAINS:
        print(f"{domain:<24} {stats.get(domain, 0):<10}")
    print("-" * 34)
    print(f"{'TOTAL':<24} {total:<10}")
    print(f"\nOutput (flat v1.0): {OUTPUT_DIR}")
    print(f"Merged manifest:    {OUTPUT_DIR / 'verifiable_tasks.txt'}")


if __name__ == "__main__":
    main()
