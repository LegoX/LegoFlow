#!/usr/bin/env python3
"""Cache a handful of whole tasks per batch so the board can show what one is.

Counts and distributions say how many tasks exist; they never show what one
actually looks like. This lifts a small sample out of each batch — the problem
statement, the verifier, the reference fix, and the container recipe — and writes
it beside the page as JSON, fetched only when a reader opens a sample.

Written to `<site>/data/samples-<batch>.json`; the files are the cache. Files are
read with a size cap so one enormous patch cannot bloat the payload.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

SAMPLES_PER_BATCH = 10
MAX_BYTES = 24_000

# label -> relative path inside a harbor task. Order is the order of the tabs.
PARTS: list[tuple[str, str]] = [
    ("Instruction", "instruction.md"),
    ("Verifier", "tests/test.sh"),
    ("Reference fix", "solution/fix.patch"),
    ("Bug patch", "environment/bug.patch"),
    ("Dockerfile", "environment/Dockerfile"),
]


def read_capped(path: Path, limit: int = MAX_BYTES) -> dict[str, Any] | None:
    """Read a text file, truncating at `limit` bytes and saying so."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    truncated = len(raw) > limit
    text = raw[:limit].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n… truncated at {limit:,} bytes of {len(raw):,}"
    return {"text": text, "bytes": len(raw), "truncated": truncated}


def sample_batch(tasks: dict[str, dict[str, Any]], limit: int = SAMPLES_PER_BATCH
                 ) -> list[dict[str, Any]]:
    """Take the first `limit` tasks in name order — stable across runs, so a
    reader who bookmarks a sample finds the same one next time."""
    out: list[dict[str, Any]] = []
    for task_name in sorted(tasks)[:limit]:
        meta = tasks[task_name]
        task_dir = Path(meta["path"]).parent
        parts = []
        for label, rel in PARTS:
            content = read_capped(task_dir / rel)
            if content:
                parts.append({"label": label, "file": rel, **content})
        out.append({
            "task_name": task_name,
            "repo": meta.get("repo"),
            "language": meta.get("language"),
            "difficulty": meta.get("difficulty"),
            "area": meta.get("area"),
            "topic": meta.get("topic"),
            "bug_class": meta.get("bug_class"),
            "parts": parts,
        })
    return out


def write_samples(batches: list[dict[str, Any]], site_dir: Path,
                  limit: int = SAMPLES_PER_BATCH) -> dict[str, dict[str, Any]]:
    """Write one JSON per batch; return the index the page embeds."""
    import json

    data_dir = site_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for stale in data_dir.glob("samples-*.json"):
        stale.unlink()

    index: dict[str, dict[str, Any]] = {}
    for batch in batches:
        samples = sample_batch(batch.get("tasks") or {}, limit)
        if not samples:
            continue
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in batch["name"])
        name = f"samples-{safe}.json"
        (data_dir / name).write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
        index[batch["name"]] = {
            "file": f"data/{name}",
            "count": len(samples),
            "tasks": [
                {"task_name": s["task_name"], "language": s["language"],
                 "difficulty": s["difficulty"]}
                for s in samples
            ],
        }
    return index
