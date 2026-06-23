#!/usr/bin/env python3
"""Extract verified terminal tasks from all domains, convert each task's task.toml
from terminal-lego v1.0 schema to harbor 1.1 schema, and merge into a flat
artifacts/merged_terminal_tasks/ directory consumed by downstream trajgen/sft/eval.

Reads each domain's verifiable_tasks.txt (authoritative manifest). For every
listed task it copies instruction.md / environment/ / solution/ / tests/ verbatim
and rewrites task.toml to harbor 1.1. The conversion mirrors the proven mapping
validated in terminal-lego/data_e2e_run903/harbor_tasks/https-nginx-cert-setup/.

Dependency-free except for `tomllib` (Python 3.11+) or `tomli` fallback.
"""

import re
import shutil
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None

DOMAINS = [
    "core-terminal-os", "versioning-containers", "networking-services",
    "file-text-processing", "python-ecosystem", "ml-data", "databases-storage",
    "web-automation-apis", "security-cryptography", "debugging-reliability",
    "algorithms-concurrency", "media-scientific", "build-editor-tooling",
]
BLOCK_DIR = Path(__file__).resolve().parents[1]
TASKS_ROOT = BLOCK_DIR / "artifacts" / "terminal_tasks"
OUTPUT_DIR = BLOCK_DIR / "artifacts" / "merged_terminal_tasks"


def _mem_to_mb(val, default):
    """Convert '1G'/'512M'/1024 → integer MB."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return int(val)
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([GMK]?)i?B?\s*$", str(val), re.IGNORECASE)
    if not m:
        return default
    num, unit = float(m.group(1)), m.group(2).upper()
    factor = {"G": 1024, "M": 1, "K": 1 / 1024, "": 1}.get(unit, 1)
    return int(num * factor)


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _first_paragraph(instruction_path: Path, fallback: str) -> str:
    if not instruction_path.exists():
        return fallback
    text = instruction_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:200]
    return fallback


def convert_task_toml(v1: dict, domain: str, task_id: str, instruction_path: Path) -> str:
    """Render a harbor 1.1 task.toml string from a parsed terminal-lego v1.0 dict."""
    meta = v1.get("metadata", {})
    verifier = v1.get("verifier", {})
    agent = v1.get("agent", {})
    env = v1.get("environment", {})

    name = f"terminalgen/{domain}/{task_id}"
    description = _first_paragraph(instruction_path, f"Verified terminal task from {domain}.")
    tags = meta.get("tags", []) or []
    keywords = list(dict.fromkeys([domain] + tags))  # domain first, dedup

    mem_mb = _mem_to_mb(env.get("memory"), 1024)
    sto_mb = _mem_to_mb(env.get("storage"), 5120)

    def arr(items):
        return "[" + ", ".join(f'"{_toml_escape(str(x))}"' for x in items) + "]"

    lines = [
        'schema_version = "1.1"',
        "",
        "[task]",
        f'name = "{_toml_escape(name)}"',
        f'description = "{_toml_escape(description)}"',
        f"authors = {arr(['terminalgen'])}",
        f"keywords = {arr(keywords)}",
        "",
        "[metadata]",
        f'author_name = "{_toml_escape(meta.get("author_name", "StackOverflow Community"))}"',
        f'author_email = "{_toml_escape(meta.get("author_email", "community@stackoverflow.com"))}"',
        f'difficulty = "{_toml_escape(meta.get("difficulty", "medium"))}"',
        f'category = "{_toml_escape(meta.get("category", domain))}"',
        f"tags = {arr(tags)}",
        f'source_url = "{_toml_escape(meta.get("source_url", ""))}"',
        f'source_score = {int(meta.get("source_score", 0) or 0)}',
        f'source_domain = "{_toml_escape(domain)}"',
        "",
        "[verifier]",
        f"timeout_sec = {float(verifier.get('timeout_sec', 300.0))}",
        "",
        "[agent]",
        f"timeout_sec = {float(agent.get('timeout_sec', 600.0))}",
        "",
        "[environment]",
        f"build_timeout_sec = {float(env.get('build_timeout_sec', 300.0))}",
        f"cpus = {int(env.get('cpus', 1))}",
        f"memory_mb = {mem_mb}",
        f"storage_mb = {sto_mb}",
        "gpus = 0",
        "allow_internet = true",
        "mcp_servers = []",
        "",
        "[verifier.env]",
        "",
        "[environment.env]",
        "",
        "[solution.env]",
        "",
    ]
    return "\n".join(lines)


def main():
    if tomllib is None:
        print("ERROR: need tomllib (Python 3.11+) or `pip install tomli`", file=sys.stderr)
        sys.exit(1)

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
            # Globally-unique merged id (domain prefix avoids task_00000 collisions).
            merged_id = f"{domain}__{task_id}"
            dst = OUTPUT_DIR / merged_id
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)

            # Rewrite task.toml v1.0 → harbor 1.1.
            v1_toml = dst / "task.toml"
            try:
                v1 = tomllib.loads(v1_toml.read_text()) if v1_toml.exists() else {}
            except Exception as e:
                print(f"  WARN: {merged_id} task.toml parse failed ({e}); using defaults")
                v1 = {}
            harbor = convert_task_toml(v1, domain, task_id, dst / "instruction.md")
            v1_toml.write_text(harbor, encoding="utf-8")

            merged_manifest.append(merged_id)
            copied += 1

        stats[domain] = copied
        total += copied

    (OUTPUT_DIR / "verifiable_tasks.txt").write_text("\n".join(merged_manifest) + ("\n" if merged_manifest else ""))

    print(f"\n{'Domain':<24} {'Extracted':<10}")
    print("-" * 34)
    for domain in DOMAINS:
        print(f"{domain:<24} {stats.get(domain, 0):<10}")
    print("-" * 34)
    print(f"{'TOTAL':<24} {total:<10}")
    print(f"\nOutput (harbor 1.1): {OUTPUT_DIR}")
    print(f"Merged manifest:     {OUTPUT_DIR / 'verifiable_tasks.txt'}")


if __name__ == "__main__":
    main()
