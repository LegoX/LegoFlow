#!/usr/bin/env python3
"""Read Harbor `task.toml` files out of a batch directory.

Language, difficulty and the 4-tag schema are read from each task's own
`task.toml`, never inferred from the directory name — a task sitting in `py-cc/`
that declares Go counts as Go.

Ported from `blocks/tracer/dashboard/progress_monitor.py:698-806`, which already
solves this and is proven in production. Blocks are independent units and must
not import across block boundaries, so the logic is duplicated here on purpose;
keep the function names aligned with the tracer copy so fixes can be carried
across by inspection.

Two directory layouts are handled by the same walk, which is what lets one
config entry point at either shape:

    <batch>/<task_id>/task.toml                 flat      (merged_swe_tasks)
    <batch>/<lang>-cc/<task_id>/task.toml       nested    (swe_tasks)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    import tomli as tomllib  # type: ignore[no-redef]

LANGUAGE_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("Python", ("python", "py", "django", "flask", "fastapi", "pytest", "pandas", "numpy")),
    ("JavaScript", ("javascript", "node", "npm", "react", "vue", "webpack", "babel", "js")),
    ("TypeScript", ("typescript", "ts", "angular", "next", "vite", "eslint")),
    ("Go", ("go", "golang", "kubernetes", "k8s", "prometheus", "terraform", "helm")),
    ("Rust", ("rust", "cargo", "tokio", "serde")),
    ("Java", ("java", "maven", "gradle", "spring", "android")),
    ("C++", ("c++", "cpp", "cmake", "llvm")),
    ("C", ("c", "clang")),
]

LANGUAGE_ALIASES = {
    "py": "Python", "python": "Python",
    "js": "JavaScript", "javascript": "JavaScript",
    "ts": "TypeScript", "typescript": "TypeScript",
    "go": "Go", "golang": "Go",
    "rs": "Rust", "rust": "Rust",
    "java": "Java",
    "c": "C",
    "cpp": "C++", "c++": "C++",
}

# `language` in metadata is sometimes the *human* language of the instruction;
# those values must not be mistaken for a programming language.
NATURAL_LANGUAGE_CODES = {"en", "zh", "zh-cn", "zh_cn", "ja", "ko", "fr", "de", "es", "ru"}


def normalize_instance_id(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"__mirror.*$", "", text)
    return text


def normalize_language(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    return LANGUAGE_ALIASES.get(text.lower(), text)


def compact_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x is not None]
    if isinstance(value, str) and value:
        return [value]
    return []


def task_repo(task_name: str) -> str:
    parts = task_name.split("__")
    if len(parts) < 2:
        return task_name
    repo = re.sub(r"-\d+$", "", parts[1])
    return f"{parts[0]}/{repo}"


def infer_language(task_name: str, metadata: dict[str, Any]) -> str:
    """`[metadata] tags` is the harbor 4-tuple [language, area, topic, bug_class],
    so tags[0] is the authoritative language when present."""
    tags = compact_list(metadata.get("tags"))
    if tags and str(tags[0]).lower() not in NATURAL_LANGUAGE_CODES:
        return normalize_language(tags[0])
    explicit = metadata.get("language")
    if explicit and str(explicit).lower() not in NATURAL_LANGUAGE_CODES:
        return normalize_language(explicit)
    hay = " ".join([task_name, str(metadata.get("category") or ""), " ".join(tags)]).lower()
    for language, hints in LANGUAGE_HINTS:
        if any(re.search(rf"(^|[^a-z0-9+]){re.escape(h)}([^a-z0-9+]|$)", hay) for h in hints):
            return language
    return "unknown"


def infer_difficulty(data: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, float | None]:
    """Return (label, raw score). swegen scores difficulty inline during
    `swegen create`, so a numeric `[scoring] difficulty_score` may be present —
    when it is, pooled order statistics become computable."""
    scoring = data.get("scoring") if isinstance(data.get("scoring"), dict) else {}
    score: float | None = None
    for source, key in ((scoring, "difficulty_score"), (metadata, "difficulty_score")):
        value = source.get(key) if isinstance(source, dict) else None
        if value not in {None, ""}:
            try:
                score = float(value)
                break
            except (TypeError, ValueError):
                pass
    for source, key in (
        (metadata, "difficulty"),
        (metadata, "difficulty_label"),
        (scoring, "difficulty_label"),
        (scoring, "difficulty"),
    ):
        value = source.get(key) if isinstance(source, dict) else None
        if value not in {None, ""}:
            return str(value).strip().lower(), score
    return "unknown", score


def iter_task_toml_files(tasks_dir: Path) -> list[Path]:
    """Every task.toml one or two levels below `tasks_dir` (flat and nested layouts)."""
    if not tasks_dir.is_dir():
        return []
    paths: list[Path] = []
    try:
        children = sorted(tasks_dir.iterdir(), key=str)
    except OSError:
        return []
    for child in children:
        direct = child / "task.toml"
        if direct.is_file():
            paths.append(direct)
        if not child.is_dir():
            continue
        try:
            grandchildren = sorted(child.iterdir(), key=str)
        except OSError:
            continue
        for grandchild in grandchildren:
            nested = grandchild / "task.toml"
            if nested.is_file():
                paths.append(nested)
    return sorted(paths, key=str)


def load_language_map(tasks_dir: Path) -> dict[str, str]:
    """Optional `language_map.json` override, at the batch root or one level down."""
    paths = [tasks_dir / "language_map.json", *sorted(tasks_dir.glob("*/language_map.json"))]
    out: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARN: failed to parse language map {path}: {exc}", file=sys.stderr)
            continue
        if isinstance(data, dict):
            for key, value in data.items():
                out[normalize_instance_id(key)] = normalize_language(value)
    return out


def collect_task_dim(tasks_dir: Path) -> dict[str, dict[str, Any]]:
    """task_name -> {repo, language, area, topic, bug_class, difficulty, score, tagged}."""
    tasks: dict[str, dict[str, Any]] = {}
    if not tasks_dir.is_dir():
        return tasks
    language_map = load_language_map(tasks_dir)

    for task_file in iter_task_toml_files(tasks_dir):
        try:
            data = tomllib.loads(task_file.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"WARN: failed to parse {task_file}: {exc}", file=sys.stderr)
            continue
        task_name = task_file.parent.name
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}

        tags = compact_list(metadata.get("tags"))
        override = language_map.get(normalize_instance_id(task_name))
        language = normalize_language(override) if override else infer_language(task_name, metadata)
        label, score = infer_difficulty(data, metadata)

        # An adapter that records the repository explicitly is more reliable than
        # splitting the directory name, which assumes the "owner__repo-N" shape.
        extra = metadata.get("extra") if isinstance(metadata.get("extra"), dict) else {}
        owner, repo_name = str(extra.get("user") or ""), str(extra.get("repo") or "")
        repo = f"{owner}/{repo_name}" if owner and repo_name else (repo_name or task_repo(task_name))

        tasks[task_name] = {
            "task_name": task_name,
            "repo": repo,
            "language": language,
            # harbor 4-tuple: [language, area, topic, bug_class]
            "area": tags[1].strip().lower() if len(tags) >= 2 and tags[1] else "",
            "topic": tags[2].strip().lower() if len(tags) >= 3 and tags[2] else "",
            "bug_class": tags[3].strip().lower() if len(tags) >= 4 and tags[3] else "",
            "difficulty": label,
            "score": score,
            # "tagged" means the semantic tags are filled in, not merely that the
            # file parsed — an untagged task must never be counted as tagged.
            "tagged": len(tags) >= 2 and bool(tags[1]),
            "path": str(task_file),
        }
    return tasks


def read_verified_ids(batch_path: Path) -> set[str]:
    """Union of every verifiable_tasks.txt at the batch root or one level below,
    mirroring the two layouts iter_task_toml_files walks."""
    out: set[str] = set()
    if not batch_path.is_dir():
        return out
    candidates = [batch_path / "verifiable_tasks.txt", *sorted(batch_path.glob("*/verifiable_tasks.txt"))]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    out.add(line)
        except OSError as exc:
            print(f"WARN: failed to read {path}: {exc}", file=sys.stderr)
    return out
