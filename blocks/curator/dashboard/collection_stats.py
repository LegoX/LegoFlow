#!/usr/bin/env python3
"""Statistics for the PR-collection stage.

Two sources under `artifacts/collected_prs/`:

* `filtering_report.md` — the collector's own funnel: repos searched → candidates
  → repos with qualifying PRs, and PRs scanned → merged → qualifying, per language,
  each with the reasons rows were dropped. This is the authoritative account of the
  collection stage and is preferred whenever present.
* `{language}_pr_ids.txt` — flat `owner/repo:pr-NUMBER` list of what survived. Cheap
  to read and gives the per-repo distribution the report does not carry.

`{language}_prs.jsonl` is deliberately ignored: it holds full PR payloads and runs to
hundreds of MB per language, and carries nothing the two sources above lack.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from task_toml import normalize_language

# Collection writes full language names; task directories use short ones.
COLLECT_TO_TASK_LANG = {
    "python": "py", "javascript": "js", "typescript": "ts",
    "go": "go", "c": "c", "cpp": "cpp", "java": "java", "rust": "rust",
}

LINE_RE = re.compile(r"^(?P<owner>[^/\s]+)/(?P<repo>[^:\s]+):pr-(?P<pr>\d+)\s*$")
_METRIC_RE = re.compile(r"^-\s*(?P<key>[A-Za-z ]+?):\s*(?P<n>[\d,]+)(?:\s*\(([\d.]+)%\))?\s*$")
_REASON_RE = re.compile(r"^\s+-\s*(?P<key>[\w_]+):\s*(?P<n>[\d,]+)\s*$")
_OVERALL_ROW_RE = re.compile(r"^\|\s*(?P<key>[A-Za-z ]+?)\s*\|\s*(?P<n>[\d,]+)\s*\|")


def _int(text: str) -> int:
    return int(str(text).replace(",", ""))


def parse_pr_file(path: Path) -> tuple[list[str], int]:
    """Return (repo full names, malformed line count)."""
    repos: list[str] = []
    malformed = 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return repos, malformed
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = LINE_RE.match(line)
        if not m:
            malformed += 1
            continue
        repos.append(f"{m.group('owner')}/{m.group('repo')}")
    return repos, malformed


def parse_filtering_report(path: Path) -> dict[str, Any]:
    """Parse the collector's funnel report into {overall, languages{}}."""
    out: dict[str, Any] = {"overall": {}, "languages": {}, "generated_at": None}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out

    gen = re.search(r"^Generated at:\s*(.+)$", text, re.M)
    if gen:
        out["generated_at"] = gen.group(1).strip()

    section = None   # None | "overall" | language name
    bucket = None    # "repo" | "pr"
    for line in text.splitlines():
        if line.startswith("## Overall Summary"):
            section, bucket = "overall", None
            continue
        if line.startswith("### "):
            section = normalize_language(
                COLLECT_TO_TASK_LANG.get(line[4:].strip().lower(), line[4:].strip())
            )
            out["languages"].setdefault(
                section,
                {"language": section, "repo": {}, "pr": {},
                 "repo_dropped": {}, "pr_dropped": {}},
            )
            bucket = None
            continue
        if "Repository Filtering" in line:
            bucket = "repo"
            continue
        if "PR Filtering" in line:
            bucket = "pr"
            continue

        if section == "overall":
            m = _OVERALL_ROW_RE.match(line)
            if m and m.group("key").strip() != "Metric":
                out["overall"][m.group("key").strip()] = _int(m.group("n"))
            continue

        if section and bucket:
            entry = out["languages"][section]
            m = _METRIC_RE.match(line)
            if m:
                entry[bucket][m.group("key").strip().lower()] = _int(m.group("n"))
                continue
            m = _REASON_RE.match(line)
            if m:
                entry[f"{bucket}_dropped"][m.group("key")] = _int(m.group("n"))
    return out


def collect_pr_stats(collected_prs_dir: Path) -> dict[str, Any]:
    """Per-language PR/repo counts, joined with the collector's funnel report.

    Languages are keyed by the canonical name from normalize_language() so the
    result joins directly against task.toml-derived language counts.
    """
    per_language: dict[str, dict[str, Any]] = {}
    all_repos: set[str] = set()
    total_prs = 0
    malformed_total = 0

    if collected_prs_dir.is_dir():
        for path in sorted(collected_prs_dir.glob("*_pr_ids.txt")):
            raw_lang = path.name[: -len("_pr_ids.txt")]
            repos, malformed = parse_pr_file(path)
            if not repos and not malformed:
                continue
            canonical = normalize_language(COLLECT_TO_TASK_LANG.get(raw_lang, raw_lang))
            counts = Counter(repos)
            entry = per_language.setdefault(
                canonical, {"prs": 0, "per_repo": Counter(), "files": []}
            )
            entry["files"].append(path.name)
            entry["prs"] += len(repos)
            entry["per_repo"].update(counts)
            all_repos.update(counts)
            total_prs += len(repos)
            malformed_total += malformed

    report = parse_filtering_report(collected_prs_dir / "filtering_report.md")

    languages: dict[str, Any] = {}
    for canonical in sorted(
        set(per_language) | set(report["languages"]),
        key=lambda c: -per_language.get(c, {}).get("prs", 0),
    ):
        entry = per_language.get(canonical, {"prs": 0, "per_repo": Counter(), "files": []})
        rep = report["languages"].get(canonical, {})
        per_repo: Counter = entry["per_repo"]
        languages[canonical] = {
            "language": canonical,
            "files": entry["files"],
            "prs": entry["prs"],
            "repos": len(per_repo),
            "max_prs_per_repo": max(per_repo.values()) if per_repo else 0,
            "avg_prs_per_repo": (entry["prs"] / len(per_repo)) if per_repo else None,
            "top_repos": dict(per_repo.most_common(15)),
            "repos_searched": rep.get("repo", {}).get("searched"),
            "repos_candidate": rep.get("repo", {}).get("candidates"),
            "repos_qualifying": rep.get("repo", {}).get("with qualifying prs"),
            "prs_scanned": rep.get("pr", {}).get("scanned"),
            "prs_merged": rep.get("pr", {}).get("merged"),
            "prs_qualifying": rep.get("pr", {}).get("qualifying"),
            "repo_dropped": rep.get("repo_dropped", {}),
            "pr_dropped": rep.get("pr_dropped", {}),
        }

    return {
        "dir": str(collected_prs_dir),
        "exists": collected_prs_dir.is_dir(),
        "languages": languages,
        "total_prs": total_prs,
        "total_repos": len(all_repos),
        "malformed_lines": malformed_total,
        "overall": report["overall"],
        "report_generated_at": report["generated_at"],
        "has_report": bool(report["overall"] or report["languages"]),
    }


def collect_pr_stats_multi(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge several collection directories into one view.

    Each configured PR entry is a whole collection directory; its per-language
    files are discovered inside, so one entry normally covers every language.
    """
    if not entries:
        return {"dir": "", "dirs": [], "exists": False, "languages": {},
                "total_prs": 0, "total_repos": 0, "malformed_lines": 0,
                "overall": {}, "report_generated_at": None, "has_report": False}
    parts = [(e["name"], collect_pr_stats(Path(e["path"]))) for e in entries]
    if len(parts) == 1:
        only = parts[0][1]
        only["dirs"] = [{"name": parts[0][0], "dir": only["dir"], "exists": only["exists"]}]
        return only

    merged: dict[str, Any] = {
        "dir": ", ".join(p[1]["dir"] for p in parts),
        "dirs": [{"name": n, "dir": s_["dir"], "exists": s_["exists"]} for n, s_ in parts],
        "exists": any(s_["exists"] for _, s_ in parts),
        "languages": {}, "total_prs": 0, "total_repos": 0, "malformed_lines": 0,
        "overall": Counter(), "report_generated_at": None, "has_report": False,
    }
    langs: dict[str, dict[str, Any]] = {}
    for _, s_ in parts:
        merged["total_prs"] += s_["total_prs"]
        merged["total_repos"] += s_["total_repos"]
        merged["malformed_lines"] += s_["malformed_lines"]
        merged["overall"].update(s_["overall"])
        merged["has_report"] = merged["has_report"] or s_["has_report"]
        merged["report_generated_at"] = merged["report_generated_at"] or s_["report_generated_at"]
        for name, e in s_["languages"].items():
            cur = langs.setdefault(name, dict(e))
            if cur is not e:
                for k in ("prs", "repos"):
                    cur[k] = (cur.get(k) or 0) + (e.get(k) or 0)
                for k in ("repos_searched", "repos_candidate", "repos_qualifying",
                          "prs_scanned", "prs_merged", "prs_qualifying"):
                    if e.get(k) is not None:
                        cur[k] = (cur.get(k) or 0) + e[k]
    merged["languages"] = langs
    merged["overall"] = dict(merged["overall"])
    return merged
