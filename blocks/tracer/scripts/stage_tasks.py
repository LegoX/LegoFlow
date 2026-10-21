#!/usr/bin/env python3
"""Publish owned, versioned Harbor staging without overwriting user datasets."""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import uuid

import yaml

from task_source import resolve


OWNER = "legoflow-tracer-staging-v1"
CACHE_NAME = ".legoflow-staging-v1"


def absolute(path: Path) -> Path:
    """Normalize the parent without following an existing public pointer."""
    return path.parent.resolve() / path.name


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


def task_layout(path: Path) -> bool:
    return all((path / name).is_file() for name in ("task.toml", "instruction.md")) and all(
        (path / name).is_dir() for name in ("environment", "tests")
    )


def settings(config: dict) -> dict:
    source = resolve(config)
    inputs = config["runtime_info"]["input"]
    label = (inputs.get("harbor_job") or {}).get("dataset") or source["dataset_id"]
    if not isinstance(label, str) or label in {"", ".", ".."} or any(c in label for c in ("/", "\\", "\0", "\n", "\r")):
        raise ValueError("Harbor dataset label must be a single directory name")
    return {
        "provider": source["provider"],
        "local_block": source["local_block"] if source["provider"] == "local" else None,
        "dataset_name": source["dataset_name"],
        "split": (inputs.get("task_source") or {}).get("split"),
        "manifest_required": source["manifest_required"],
        "dataset_id": label,
    }


def cache_directory(block: Path) -> Path:
    cache = absolute(block / "artifacts/tasks" / CACHE_NAME)
    if cache.is_symlink():
        raise ValueError("the staging version cache must not be a symlink")
    return cache


def ensure_cache(cache: Path) -> None:
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".tracer-cache-", dir=cache.parent))
        try:
            write_json(temporary / "owner.json", {"owner": OWNER})
            try:
                temporary.rename(cache)
            except OSError as exc:
                if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    if not cache.is_dir() or json.loads((cache / "owner.json").read_text()).get("owner") != OWNER:
        raise ValueError("existing staging cache is not owned by this tracer")


def owned_target(target: Path, cache: Path):
    if not target.exists() and not target.is_symlink():
        return None
    guidance = "choose an empty harbor_job.dataset_path, or move the old directory yourself after checking active jobs"
    if not target.is_symlink():
        raise ValueError(f"refusing to replace an unowned real dataset directory/file: {target}; {guidance}")
    data = target.resolve()
    try:
        relative = data.relative_to(cache)
    except ValueError:
        raise ValueError(f"existing dataset symlink is not owned staging: {target}; {guidance}") from None
    if len(relative.parts) != 3 or relative.parts[0] != digest(str(target)):
        raise ValueError(f"existing dataset pointer belongs to another staging target: {target}")
    metadata = json.loads((data.parent / "stage.json").read_text())
    if metadata.get("owner") != OWNER or metadata.get("target") != str(target) or metadata.get("dataset_id") != data.name:
        raise ValueError(f"existing dataset pointer has invalid ownership metadata: {target}")
    return data, metadata


def boundaries(source: Path | None, target: Path, cache: Path) -> None:
    if target == cache or target.is_relative_to(cache) or cache.is_relative_to(target):
        raise ValueError("dataset staging target must not overlap the private version cache")
    if source is None:
        return
    current = target.resolve()
    if source == current or source.is_relative_to(current) or current.is_relative_to(source):
        raise ValueError("task source and staging directory must not overlap or alias each other")
    if source == cache or source.is_relative_to(cache) or cache.is_relative_to(source):
        raise ValueError("task source must not overlap or alias the private staging cache")


def selected_tasks(source: Path, strict: bool):
    manifest = source / "verifiable_tasks.txt"
    manifest_hash = None
    if manifest.is_file():
        content = manifest.read_bytes()
        manifest_hash = hashlib.sha256(content).hexdigest()
        ids = sorted({line.strip() for line in content.decode().splitlines() if line.strip() and not line.strip().startswith("#")})
        if not ids:
            raise ValueError(f"manifest is empty: {manifest}")
    elif strict:
        raise ValueError(f"terminal-curator source requires verifiable_tasks.txt: {source}")
    else:
        ids = sorted(path.name for path in source.iterdir() if path.is_dir() and task_layout(path))
    tasks = {}
    for name in ids:
        if name in {".", ".."} or any(c in name for c in ("/", "\\", "\0", "\n", "\r")):
            raise ValueError("manifest task IDs must be direct directory names")
        task = source / name
        if strict and (task.is_symlink() or task.resolve().parent != source):
            raise ValueError(f"manifest task is missing or outside the source: {name}")
        if not task_layout(task):
            if strict:
                raise ValueError(f"manifest task is missing or has an invalid Harbor layout: {name}")
            print(f"WARN: skipping unavailable/invalid manifest task: {name}", file=sys.stderr)
            continue
        tasks[name] = task.resolve()
    if not tasks:
        raise ValueError("source contains no selected Harbor tasks")
    if strict:
        script = Path(__file__).resolve().parents[2] / "terminal-curator/scripts/publication.py"
        spec = importlib.util.spec_from_file_location("terminal_publication", script)
        publication = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publication)
        publication.verify_publication(source)
    return tasks, manifest_hash


def content_identity(tasks: dict[str, Path]) -> str:
    """Use file metadata, not asset reads; local sources retain linked-content semantics."""
    rows = []
    for task_name, root in sorted(tasks.items()):
        for directory, directories, files in os.walk(root, followlinks=False):
            directories.sort()
            base = Path(directory)
            for name in sorted(directories + files):
                path = base / name
                info = path.lstat()
                relative = str(path.relative_to(root))
                link = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
                rows.append((task_name, relative, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, link))
    return digest(rows)


def valid_data(data: Path, metadata: dict) -> bool:
    if not data.is_dir():
        return False
    tasks = metadata.get("tasks") or {}
    if set(path.name for path in data.iterdir()) != set(tasks):
        return False
    for name, source in tasks.items():
        task = data / name
        if not task_layout(task):
            return False
        if metadata.get("mode") == "links" and (not task.is_symlink() or os.readlink(task) != source):
            return False
    if metadata.get("mode") == "copy":
        return content_identity({name: data / name for name in tasks}) == metadata.get("data_identity")
    return True


def point(target: Path, data: Path, cache: Path) -> None:
    # Recheck immediately before rename. Existing real directories are never removed.
    owned_target(target, cache)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + ".stage-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(data, target_is_directory=True)
        temporary.replace(target)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def stage(block: Path, source: Path, target: Path, options: dict, copy: bool) -> Path:
    source = source.resolve(strict=True)
    target = absolute(target)
    cache = cache_directory(block)
    boundaries(source, target, cache)
    owned_target(target, cache)
    tasks, manifest_hash = selected_tasks(source, options["manifest_required"])
    observed = content_identity(tasks)
    fingerprint = digest({"settings": options, "source": str(source), "manifest": manifest_hash,
                          "tasks": {name: str(path) for name, path in tasks.items()}, "content": observed,
                          "mode": "copy" if copy else "links"})
    ensure_cache(cache)
    bucket = cache / digest(str(target))
    bucket.mkdir(exist_ok=True)
    with (bucket / "stage.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        owned_target(target, cache)
        version = bucket / fingerprint
        data = version / options["dataset_id"]
        if version.exists():
            metadata = json.loads((version / "stage.json").read_text())
            if metadata.get("fingerprint") == fingerprint and valid_data(data, metadata):
                point(target, data, cache)
                return data
            version = bucket / (fingerprint + "-" + uuid.uuid4().hex)
        temporary = Path(tempfile.mkdtemp(prefix=".building-", dir=bucket))
        try:
            staged = temporary / options["dataset_id"]
            staged.mkdir()
            for name, task in tasks.items():
                if copy:
                    # HF snapshots may link into the hub cache. Materialize
                    # those files before the private download is cleaned up.
                    shutil.copytree(task, staged / name, symlinks=False)
                else:
                    (staged / name).symlink_to(task, target_is_directory=True)
            if content_identity(tasks) != observed:
                raise ValueError("task source changed while staging; retry with a stable source")
            metadata = {"owner": OWNER, "target": str(target), "dataset_id": options["dataset_id"],
                        "settings": options, "fingerprint": fingerprint, "source": str(source),
                        "manifest_identity": manifest_hash, "source_identity": observed,
                        "tasks": {name: str(path) for name, path in tasks.items()},
                        "mode": "copy" if copy else "links"}
            if copy:
                metadata["data_identity"] = content_identity({name: staged / name for name in tasks})
            write_json(temporary / "stage.json", metadata)
            temporary.rename(version)
            data = version / options["dataset_id"]
            point(target, data, cache)
            return data
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)


def reuse_copy(block: Path, target: Path, options: dict):
    target = absolute(target)
    cache = cache_directory(block)
    boundaries(None, target, cache)
    current = owned_target(target, cache)
    if current and current[1].get("mode") == "copy" and current[1].get("settings") == options and valid_data(*current):
        return current[0]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--copy", action="store_true")
    parser.add_argument("--reuse-copy", action="store_true")
    parser.add_argument("--check-target", action="store_true")
    parser.add_argument("--result-file", type=Path)
    args = parser.parse_args()
    try:
        options = settings(yaml.safe_load(args.config.read_text()) or {})
        if args.check_target:
            target = absolute(args.target)
            cache = cache_directory(args.block_dir)
            boundaries(None, target, cache)
            owned_target(target, cache)
            return 0
        if args.reuse_copy:
            data = reuse_copy(args.block_dir, args.target, options)
            if data is None:
                return 1
        else:
            if args.source is None:
                raise ValueError("--source is required for staging")
            data = stage(args.block_dir, args.source, args.target, options, args.copy)
        if args.result_file:
            write_json(args.result_file, {"dataset_path": str(data)})
        print(f"Prepared owned Harbor staging: {data}")
        return 0
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        print(f"ERROR: invalid YAML in {args.config}{location}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
