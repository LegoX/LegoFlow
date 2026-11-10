#!/usr/bin/env python3
"""Write the trainer round receipt — the plant identity of one SFT round.

The RSI loop compares evaluation rounds that must be attributable to a
specific (checkpoint, training mix) pair. This script stamps that identity
next to the run output (`artifacts/model/<run>/round_receipt.json`) so the
loop ledger can key measurement rows onto exactly what was trained:

  round                 loop round (flag/env, see --help)
  checkpoint_sha256     cheap deterministic manifest hash of the checkpoint dir
  mix_manifest_sha256   sha256 of the LF data file (lf.json) actually used
  n_instances, tokens   from the converter's `<lf>.stats.json` sidecar
  final_loss            `loss` of the last trainer_log.jsonl entry

Every input except the checkpoint dir is optional: a missing LF file, stats
sidecar, or trainer log is tolerated and recorded as null rather than failing
— the receipt must never be the reason a finished training run looks failed.

Determinism: the manifest hash depends only on (relative path, size, and —
for files below the size limit — content) of the files inside the checkpoint
dir, in sorted order. Re-running over an unchanged checkpoint reproduces the
same receipt (modulo `generated_at`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "legoflow.round_receipt.v1"

# Files strictly smaller than this are content-hashed; anything at or above it
# contributes (name, size) only. 4 MiB keeps the manifest cheap on multi-GB
# checkpoints (model shards dominate) while configs, tokenizer files, and
# trainer state are still hashed byte-for-byte.
HASH_SIZE_LIMIT = 4 * 1024 * 1024

# Manifest line: "<relative posix path>\t<size>\t<sha256 or ->\n"; the digest of
# the concatenated lines is checkpoint_sha256.
SIZE_ONLY_MARKER = "-"

ROUND_ENV = "LEGOFLOW_RSI_ROUND"
STATS_SUFFIX = ".stats.json"
LF_SKIP_NAMES = {"datasetinfo.json", "dataset_info.json"}

# The shared LF writer (legoflow_trace_crafter.utils) emits `count` and
# `total_tokens`; aliases keep the receipt robust to sibling writers.
N_INSTANCES_KEYS = ("count", "n_instances", "num_instances", "num_samples", "n_samples")
TOKENS_KEYS = ("total_tokens", "tokens", "total_token_count")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checkpoint_manifest_sha256(
    checkpoint: Path, exclude: Path | None = None
) -> tuple[str, dict[str, Any]]:
    """Hash a checkpoint directory into one deterministic digest.

    Files are visited in sorted relative-path order; each contributes one
    manifest line. The returned summary records how many files were
    content-hashed vs size-only (see HASH_SIZE_LIMIT for the limitation).
    """
    exclude_resolved = exclude.resolve() if exclude is not None else None
    files = sorted(
        (p for p in checkpoint.rglob("*") if p.is_file() and p.resolve() != exclude_resolved),
        key=lambda p: p.relative_to(checkpoint).as_posix(),
    )

    lines: list[str] = []
    content_hashed = 0
    size_only = 0
    total_bytes = 0
    for path in files:
        size = path.stat().st_size
        total_bytes += size
        if size < HASH_SIZE_LIMIT:
            digest = sha256_file(path)
            content_hashed += 1
        else:
            digest = SIZE_ONLY_MARKER
            size_only += 1
        rel = path.relative_to(checkpoint).as_posix()
        lines.append(f"{rel}\t{size}\t{digest}\n")

    manifest = "".join(lines).encode("utf-8")
    return hashlib.sha256(manifest).hexdigest(), {
        "files": len(files),
        "content_hashed": content_hashed,
        "size_only": size_only,
        "total_bytes": total_bytes,
        "hash_size_limit": HASH_SIZE_LIMIT,
    }


def find_lf_file(job_dir: Path | None, lf_file: str | None) -> Path | None:
    """Resolve the LF data file this run trained on, or None.

    An explicit --lf-file wins. Otherwise --job-dir is searched
    deterministically: the tracer convention `lf.json` first, then the
    first (name-sorted) `*.json` with a matching `.stats.json` sidecar, then
    the first remaining `*.json`. Registry files (dataset_info.json) and
    stats sidecars themselves are never candidates.
    """
    if lf_file:
        path = Path(lf_file)
        return path if path.is_file() else None
    if job_dir is None or not job_dir.is_dir():
        return None

    exact = job_dir / "lf.json"
    if exact.is_file():
        return exact

    candidates = sorted(
        p
        for p in job_dir.glob("*.json")
        if p.name not in LF_SKIP_NAMES and not p.name.endswith(STATS_SUFFIX)
    )
    with_sidecar = [p for p in candidates if p.with_suffix(STATS_SUFFIX).is_file()]
    pool = with_sidecar or candidates
    return pool[0] if pool else None


def _first_key(stats: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in stats and stats[key] is not None:
            return stats[key]
    return None


def read_mix_stats(lf_file: Path | None) -> tuple[int | None, int | None]:
    """(n_instances, tokens) from the LF stats sidecar; (None, None) if absent."""
    if lf_file is None:
        return None, None
    stats_path = lf_file.with_suffix(STATS_SUFFIX)
    if not stats_path.is_file():
        return None, None
    try:
        with stats_path.open(encoding="utf-8") as fh:
            stats = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(stats, dict):
        return None, None

    n_instances = _first_key(stats, N_INSTANCES_KEYS)
    tokens = _first_key(stats, TOKENS_KEYS)
    try:
        n_instances = int(n_instances) if n_instances is not None else None
    except (TypeError, ValueError):
        n_instances = None
    try:
        tokens = int(tokens) if tokens is not None else None
    except (TypeError, ValueError):
        tokens = None
    return n_instances, tokens


def final_loss_from_trainer_log(checkpoint: Path) -> tuple[float | None, Path | None]:
    """`loss` of the last usable trainer_log.jsonl entry near the checkpoint.

    The log lives in the run dir (artifacts/model/<run>/), i.e. usually the
    checkpoint's parent; when the run produced no checkpoint-* subdir the
    checkpoint IS the run dir. Entries without a numeric `loss` (summary or
    truncated lines) are skipped. Returns (None, None) when no log exists.
    """
    seen: set[Path] = set()
    for candidate in (checkpoint, checkpoint.parent):
        if candidate in seen:
            continue
        seen.add(candidate)
        log = candidate / "trainer_log.jsonl"
        if not log.is_file():
            continue
        loss: float | None = None
        with log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                value = entry.get("loss")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    loss = float(value)
        if loss is not None:
            return loss, log
    return None, None


def resolve_round(value: int | None) -> int:
    if value is not None:
        return value
    env = os.environ.get(ROUND_ENV, "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            print(
                f"WARNING: {ROUND_ENV}={env!r} is not an integer; defaulting round to 1",
                file=sys.stderr,
            )
    # Silent default made every receipt read round: 1 — cross-round
    # attribution then collapsed all rounds onto one number. Warn loudly.
    print(
        f"WARNING: {ROUND_ENV} is unset; stamping round 1. Set it to the RSI "
        f"round this training serves (read the evaluator's loop ledger) so "
        f"weakness-report rows and receipts key to the same round.",
        file=sys.stderr,
    )
    return 1


def build_receipt(args: argparse.Namespace) -> dict[str, Any]:
    checkpoint: Path = args.checkpoint_path
    out: Path = args.out

    manifest_sha, manifest_summary = checkpoint_manifest_sha256(checkpoint, exclude=out)

    lf_file = find_lf_file(args.job_dir, args.lf_file)
    mix_manifest_sha = sha256_file(lf_file) if lf_file is not None else None
    n_instances, tokens = read_mix_stats(lf_file)
    final_loss, trainer_log = final_loss_from_trainer_log(checkpoint)

    return {
        "schema": SCHEMA,
        "round": resolve_round(args.round),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": manifest_sha,
        "checkpoint_manifest": manifest_summary,
        "lf_file": str(lf_file) if lf_file is not None else None,
        "mix_manifest_sha256": mix_manifest_sha,
        "n_instances": n_instances,
        "tokens": tokens,
        "final_loss": final_loss,
        "trainer_log_path": str(trainer_log) if trainer_log is not None else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "checkpoint_sha256 limitation: it is a MANIFEST hash, not a content "
            f"hash of the whole tree. Files at or above {HASH_SIZE_LIMIT} bytes "
            "(4 MiB) contribute filename+size only, so a content change that "
            "preserves the size of such a file (typically a model shard) is "
            "invisible to it; smaller files are hashed byte-for-byte. Manifest "
            f'line: "<relative posix path>\\t<size>\\t<sha256 or {SIZE_ONLY_MARKER}>\\n"; '
            "the receipt file itself is excluded from the manifest so re-runs "
            "are idempotent.\n"
            "\n"
            "Missing inputs are tolerated with nulls: no LF file => "
            "mix_manifest_sha256 null; no <lf>.stats.json sidecar => "
            "n_instances/tokens null; no trainer_log.jsonl => final_loss null.\n"
            "\n"
            f"round defaults to ${ROUND_ENV} when set, else 1.\n"
            "\n"
            "n_instances/tokens keys read from the stats sidecar (first hit "
            f"wins): {N_INSTANCES_KEYS} / {TOKENS_KEYS}."
        ),
    )
    parser.add_argument(
        "--checkpoint-path",
        type=Path,
        required=True,
        help="Checkpoint directory (artifacts/model/<run>/checkpoint-*, or the run dir itself)",
    )
    parser.add_argument(
        "--job-dir",
        type=Path,
        default=None,
        help="Directory holding the LF data file used and its .stats.json sidecar",
    )
    parser.add_argument(
        "--lf-file",
        default=None,
        help="Exact LF json trained on (wins over --job-dir discovery)",
    )
    parser.add_argument("--out", type=Path, required=True, help="Receipt output path")
    parser.add_argument(
        "--round",
        type=int,
        default=None,
        help=f"RSI loop round (default: ${ROUND_ENV} env, else 1)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.checkpoint_path.is_dir():
        print(f"ERROR: checkpoint path not found: {args.checkpoint_path}", file=sys.stderr)
        sys.exit(1)

    receipt = build_receipt(args)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    manifest = receipt["checkpoint_manifest"]
    print(f"=== Round receipt ({SCHEMA}) ===")
    print(f"    round:               {receipt['round']}")
    print(f"    checkpoint:          {receipt['checkpoint_path']}")
    print(
        f"    checkpoint_sha256:   {receipt['checkpoint_sha256']}"
        f" ({manifest['files']} files: {manifest['content_hashed']} hashed,"
        f" {manifest['size_only']} size-only)"
    )
    print(f"    lf_file:             {receipt['lf_file']}")
    print(f"    mix_manifest_sha256: {receipt['mix_manifest_sha256']}")
    print(f"    n_instances:         {receipt['n_instances']}")
    print(f"    tokens:              {receipt['tokens']}")
    print(f"    final_loss:          {receipt['final_loss']}")
    print(f"    written: {args.out}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
    except Exception as exc:  # keep parity with config_value.py's error surface
        print(f"ERROR: failed to write round receipt: {exc}", file=sys.stderr)
        raise
