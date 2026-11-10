#!/usr/bin/env python3
"""Build the benchmark identity index for RSI-loop decontamination.

Contamination is decided by TASK IDENTITY, not
n-gram overlap. This script walks the evaluator's gold dataset (the same
artifacts/datasets/<base>/ tree scripts/prepare_dataset.sh produces) and emits
one entry per task:

  {task_id, repo, pr_number, patch_file_paths[], test_file_paths[],
   norm_patch_sha256, patch_text}

Consumed by the curator's decontam_check.py match cascade (repo+pr exact ->
patch-path Jaccard -> shared test paths -> identifier-normalized patch sha ->
normalized-token overlap on the raw patch text).

Identity rules:
  task_id   the Harbor task dir name (SWE instance ids encode owner__repo-pr-N;
            some benchmarks use owner__repo-N)
  repo      prefer tests/config.json "repo" ("owner/repo"); else parsed from the
            task_id owner__repo split
  pr_number trailing integer of the task_id (with or without the "pr-" marker)
  *_file_paths  new-side paths parsed from the unified diffs in
            tests/config.json "patch" / "test_patch", plus the path part of
            FAIL_TO_PASS / PASS_TO_PASS test node ids
  norm_patch_sha256  sha256 over an identifier-normalized, sorted-hunk-stable
            serialization of the gold patch: lowercase, every non-[a-z0-9] run
            collapsed to a single space, hunks (file + content) sorted before
            hashing so hunk/file reordering inside one patch cannot dodge a
            match.

Fail-closed: a missing dataset dir, or a dataset dir that yields zero entries,
is an error (an empty index would silently disable decontamination).
Individual unparsable tests/config.json files are skipped with a warning and
counted in the output's "skipped" field.

Usage:
  python3 build_identity_index.py --dataset swebench-verified \
      --dataset-dir artifacts/datasets/swebench-verified \
      --out artifacts/bench_identity/swebench-verified.identity.json [--version 1.0]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "legoflow.bench_identity.v1"
DEFAULT_VERSION = "1.0"
TRAILING_NUM_RE = re.compile(r"-(?:pr-)?(\d+)$")
OWNER_REPO_RE = re.compile(r"^(?P<owner>[^-_]+)__(?P<repo>.+?)-(?:pr-)?\d+$")


def norm_text(text: str) -> str:
    """Identifier normalization: lowercase, non-[a-z0-9] runs -> single space."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def parse_diff_paths(diff_text: str) -> list[str]:
    """New-side file paths from a unified diff (the +++ side, b/ prefix stripped)."""
    paths = []
    for line in diff_text.splitlines():
        if not line.startswith("+++ "):
            continue
        target = line[4:].strip()
        if target == "/dev/null":
            continue
        target = target.split("\t")[0].strip()
        for prefix in ("b/", "a/"):
            if target.startswith(prefix):
                target = target[len(prefix):]
                break
        if target:
            paths.append(target)
    return paths


def parse_test_list(raw) -> list[str]:
    """FAIL_TO_PASS / PASS_TO_PASS entries: list, python-repr string, or newline list."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    text = str(raw).strip()
    if not text:
        return []
    items: list[str] = []
    if text.startswith("["):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple)):
                return [str(x) for x in parsed]
        except (ValueError, SyntaxError):
            pass
    for chunk in re.split(r"[\n,]", text):
        chunk = chunk.strip().strip("'\"")
        if chunk:
            items.append(chunk)
    return items


def test_node_path(node: str) -> str | None:
    """Path part of a test node id ('tests/test_x.py::test_y[case]' -> 'tests/test_x.py')."""
    path = node.split("::")[0].strip()
    path = path.split(" ")[0].strip("\"'")
    if not path or ("/" not in path and "." not in path):
        return None
    return path


def parse_hunks(diff_text: str) -> list[tuple[str, str]]:
    """(normalized file, normalized hunk body) pairs; header lines excluded."""
    hunks: list[tuple[str, str]] = []
    file_name = ""
    body: list[str] = []
    started = False

    def flush() -> None:
        nonlocal body
        if started and body:
            hunks.append((norm_text(file_name), norm_text("\n".join(body))))
        body = []

    for line in diff_text.splitlines():
        if line.startswith(("+++ ", "--- ")):
            flush()
            started = False
            if line.startswith("+++ "):
                target = line[4:].strip().split("\t")[0]
                for prefix in ("b/", "a/"):
                    if target.startswith(prefix):
                        target = target[len(prefix):]
                        break
                file_name = target
            continue
        if line.startswith("@@"):
            flush()
            started = True
            continue
        if started and (line.startswith("+") or line.startswith("-") or line.startswith(" ")):
            body.append(line)
    flush()
    return hunks


def norm_patch_sha256(diff_text: str) -> str:
    """sha256 over the sorted-hunk-stable identifier-normalized patch serialization."""
    hunks = parse_hunks(diff_text or "")
    payload = "\n".join(f"{name}\n{body}" for name, body in sorted(hunks))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_task_id(task_id: str) -> tuple[str | None, int | None]:
    """owner__repo-pr-N (or owner__repo-N) -> (repo 'owner/repo', pr_number)."""
    number = None
    match = TRAILING_NUM_RE.search(task_id)
    if match:
        number = int(match.group(1))
    repo = None
    owner_repo = OWNER_REPO_RE.match(task_id)
    if owner_repo:
        repo = f"{owner_repo.group('owner')}/{owner_repo.group('repo')}"
    return repo, number


def build_index(dataset_dir: Path, dataset: str, version: str) -> dict:
    entries = []
    skipped = []
    task_dirs = sorted(p for p in dataset_dir.iterdir() if p.is_dir()) \
        if dataset_dir.is_dir() else []
    for task_dir in task_dirs:
        config_path = task_dir / "tests" / "config.json"
        if not config_path.is_file():
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: skipping unparsable {config_path}: {exc}", file=sys.stderr)
            skipped.append(task_dir.name)
            continue
        if not isinstance(config, dict):
            print(f"WARNING: skipping non-mapping {config_path}", file=sys.stderr)
            skipped.append(task_dir.name)
            continue
        patch = str(config.get("patch") or "")
        test_patch = str(config.get("test_patch") or "")
        id_repo, id_number = parse_task_id(task_dir.name)
        repo = str(config.get("repo") or "") or id_repo
        pr_number = id_number
        if pr_number is None:
            match = TRAILING_NUM_RE.search(str(config.get("instance_id") or ""))
            pr_number = int(match.group(1)) if match else None

        patch_files = sorted(set(parse_diff_paths(patch)))
        test_files = set(parse_diff_paths(test_patch))
        for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
            for node in parse_test_list(config.get(key)):
                path = test_node_path(node)
                if path:
                    test_files.add(path)
        entries.append({
            "task_id": task_dir.name,
            "repo": repo,
            "pr_number": pr_number,
            "patch_file_paths": sorted(patch_files),
            "test_file_paths": sorted(test_files),
            "norm_patch_sha256": norm_patch_sha256(patch),
            # Raw gold patch text for decontam_check.py's token-overlap rule.
            # Gold-derived by design and exempt from the signal firewall
            # (signal_policy.yaml header): consumed for EXCLUSION only.
            "patch_text": patch,
        })
    entries.sort(key=lambda e: e["task_id"])
    return {
        "schema": SCHEMA,
        "dataset": dataset,
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "skipped": sorted(skipped),
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_identity_index",
        description="Index gold-dataset task identities for decontamination.")
    parser.add_argument("--dataset", required=True,
                        help="dataset base name (recorded in the index)")
    parser.add_argument("--dataset-dir", required=True, metavar="DIR",
                        help="gold dataset dir with per-task task dirs")
    parser.add_argument("--out", required=True, metavar="PATH",
                        help="output identity.json path")
    parser.add_argument("--version", default=DEFAULT_VERSION,
                        help=f"dataset version recorded in the index (default: {DEFAULT_VERSION})")
    args = parser.parse_args(argv)

    dataset_dir = Path(args.dataset_dir)
    if not dataset_dir.is_dir():
        print(f"ERROR: dataset dir not found: {dataset_dir} (fail closed — an "
              "identity index must exist before decontam can be trusted)",
              file=sys.stderr)
        return 1

    index = build_index(dataset_dir, args.dataset, args.version)
    if not index["entries"]:
        print(f"ERROR: no tests/config.json entries found under {dataset_dir}; "
              "refusing to write an empty identity index.", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"identity index: {len(index['entries'])} entries "
          f"({len(index['skipped'])} skipped) -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
