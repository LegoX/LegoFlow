#!/usr/bin/env python3
"""Check that a directory really holds a list of Harbor tasks.

A wrong path is the easy mistake to make in
`config.yaml -> runtime_info.input.dashboard.tasks`, and its symptom is a board
that renders fine but reports zero — which looks like "we have no tasks" rather
than "you pointed me at the wrong place". This tells the two apart.

Expected shape, one Harbor task per immediate child:

    <batch>/<task_id>/task.toml
    <batch>/<task_id>/instruction.md

`task.toml` + `instruction.md` is the same contract tracer enforces when it
consumes curator's tasks (blocks/tracer/scripts/prepare_tasks.sh:99).

Usage:
    python3 check_task_dir.py <path> [<path> ...]

Exit code 0 when every path is a usable batch, 1 otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REQUIRED = ("task.toml", "instruction.md")
SAMPLE = 5


def check_task_dir(path: Path) -> dict[str, Any]:
    """Classify a candidate batch directory.

    status is one of:
      ok        — immediate children are Harbor tasks
      nested    — tasks sit one level deeper (e.g. swe_tasks/<lang>-cc/<task>/)
      partial   — some children are tasks, some are not
      empty     — directory exists but holds no task at either depth
      missing   — path does not exist, or is not a directory
    """
    out: dict[str, Any] = {
        "path": str(path), "status": "missing", "tasks": 0, "nested_tasks": 0,
        "incomplete": [], "strays": [], "nested_parents": [], "hint": "",
    }
    if not path.exists():
        out["hint"] = "path does not exist"
        return out
    if not path.is_dir():
        out["status"] = "missing"
        out["hint"] = "path is a file, expected a directory"
        return out

    children = sorted(p for p in path.iterdir() if p.is_dir())
    tasks, strays, incomplete = [], [], []
    for child in children:
        if (child / "task.toml").is_file():
            missing = [f for f in REQUIRED if not (child / f).is_file()]
            (incomplete if missing else tasks).append((child.name, missing))
        else:
            strays.append(child)

    nested_parents = []
    nested_total = 0
    for stray in strays:
        n = sum(1 for g in stray.iterdir() if g.is_dir() and (g / "task.toml").is_file())
        if n:
            nested_parents.append((stray.name, n))
            nested_total += n

    out["tasks"] = len(tasks)
    out["nested_tasks"] = nested_total
    out["incomplete"] = [(n, m) for n, m in incomplete][:SAMPLE]
    out["nested_parents"] = nested_parents[:SAMPLE]
    out["strays"] = [s.name for s in strays if s.name not in dict(nested_parents)][:SAMPLE]

    if tasks and not strays:
        out["status"] = "ok"
    elif tasks or incomplete:
        out["status"] = "partial" if (strays or incomplete) else "ok"
    elif nested_total:
        out["status"] = "nested"
        out["hint"] = ("tasks are one level deeper than expected — point the config at "
                       f"a specific subdirectory, e.g. {path.name}/{nested_parents[0][0]}")
    else:
        out["status"] = "empty"
        out["hint"] = "no task.toml found at this level or the next"
    if out["status"] == "partial" and not out["hint"]:
        out["hint"] = "some children are not Harbor tasks; they are ignored"
    return out


def format_result(r: dict[str, Any]) -> str:
    lines = [f"[{r['status'].upper():7s}] {r['path']}"]
    if r["tasks"]:
        lines.append(f"          {r['tasks']:,} harbor task(s)")
    if r["nested_tasks"]:
        parents = ", ".join(f"{n} ({c:,})" for n, c in r["nested_parents"])
        lines.append(f"          {r['nested_tasks']:,} task(s) one level deeper under: {parents}")
    if r["incomplete"]:
        for name, missing in r["incomplete"]:
            lines.append(f"          incomplete: {name} — missing {', '.join(missing)}")
    if r["strays"]:
        lines.append(f"          not tasks: {', '.join(r['strays'])}")
    if r["hint"]:
        lines.append(f"          -> {r['hint']}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    bad = 0
    for raw in argv:
        r = check_task_dir(Path(raw).expanduser())
        print(format_result(r))
        if r["status"] not in {"ok", "partial"}:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
