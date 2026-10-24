#!/usr/bin/env python3
"""Verify the terminal producer's accepted bytes before downstream consumption."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

def task_tree_fingerprint(path: Path) -> str:
    """Supplement Harbor's content hash with empty directories and permissions.

    Native Harbor checksums remain the role-binding identity. This independent
    fingerprint protects filesystem semantics that dirhash intentionally omits.
    """
    digest = hashlib.sha256()
    for entry in [path, *sorted(path.rglob("*"), key=lambda item: os.fsencode(item.relative_to(path)))]:
        metadata = entry.lstat()
        relative = entry.relative_to(path).as_posix()
        if stat.S_ISDIR(metadata.st_mode):
            kind, content = "directory", ""
        elif stat.S_ISREG(metadata.st_mode):
            kind = "file"
            file_hash = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(entry, flags), "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("candidate entry changed its file type while hashing")
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    file_hash.update(chunk)
            content = file_hash.hexdigest()
        else:
            raise ValueError("candidate tree must contain only real directories and regular files")
        record = [relative, kind, stat.S_IMODE(metadata.st_mode), content]
        digest.update(json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def task_tree_fingerprints(candidates: Path) -> dict:
    return {task.name: task_tree_fingerprint(task) for task in sorted(candidates.iterdir()) if task.is_dir()}


def verify_publication(pool: Path) -> dict:
    try:
        pool = pool.resolve(strict=True)
        if any((pool / name).is_symlink() for name in ('validation.json', 'verifiable_tasks.txt', 'registry.json')):
            raise ValueError("terminal publication metadata must be regular files")
        record = json.loads((pool / "validation.json").read_text())
        ids = (pool / "verifiable_tasks.txt").read_text().splitlines()
        if (not isinstance(record, dict) or type(record.get("schema_version")) is not int or record["schema_version"] != 1
                or not isinstance(record.get("run_id"), str) or not record["run_id"]
                or not ids or ids != sorted(set(ids))
                or any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", n) for n in ids)
                or record.get("verified_tasks") != ids):
            raise ValueError("invalid terminal publication manifest or validation record")
        for role, reward in (("oracle", 1), ("nop", 0)):
            gate = record.get("validation", {}).get(role, {})
            if (gate.get("passed") is not True or type(gate.get("reward")) is not int
                    or gate["reward"] != reward):
                raise ValueError("terminal publication lacks accepted Oracle/NOP gates")
        for key in ("harbor_task_checksums", "tree_fingerprints"):
            hashes = record.get(key)
            if (not isinstance(hashes, dict) or set(hashes) != set(ids)
                    or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in hashes.values())):
                raise ValueError("invalid terminal publication task identities")
        for name in ids:
            task = pool / name
            if task.is_symlink() or task_tree_fingerprint(task) != record["tree_fingerprints"][name]:
                raise ValueError("terminal publication changed after validation; re-import a mutable copy and revalidate")
        registry = json.loads((pool / "registry.json").read_text())
        expected = [{"name": name, "path": str(pool / name)} for name in ids]
        if (not isinstance(registry, list) or len(registry) != 1
                or registry[0].get("name") != "terminal-curator" or registry[0].get("version") != "1.0"
                or registry[0].get("tasks") != expected):
            raise ValueError("terminal registry does not describe the validated publication")
        return record
    except (OSError, RuntimeError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("missing or invalid terminal publication evidence; re-import and revalidate") from exc


def verify_staging(staging: Path) -> dict:
    staging = staging.resolve(strict=True)
    tasks = sorted(staging.iterdir())
    if not tasks or any(not p.is_symlink() for p in tasks):
        raise ValueError("terminal staging must link to one validated publication")
    pools = {p.resolve(strict=True).parent for p in tasks}
    if len(pools) != 1:
        raise ValueError("terminal staging mixes publications")
    pool = pools.pop()
    record = verify_publication(pool)
    if [p.name for p in tasks] != record["verified_tasks"] or any(p.resolve() != pool / p.name for p in tasks):
        raise ValueError("terminal staging differs from the validated manifest")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pool", type=Path)
    group.add_argument("--registry", type=Path)
    group.add_argument("--staging", type=Path)
    args = parser.parse_args()
    try:
        if args.staging:
            verify_staging(args.staging)
        else:
            pool = args.pool if args.pool else args.registry.resolve(strict=True).parent
            if args.registry and args.registry.resolve().name != "registry.json":
                raise ValueError("terminal registry must belong to its validated publication")
            verify_publication(pool)
        return 0
    except (OSError, RuntimeError, ValueError):
        print("ERROR: terminal publication integrity check failed; preserve the batch, re-import a mutable copy and revalidate", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
