#!/usr/bin/env python3
"""Identity-based decontamination gate for the curator pool.

Mined PRs and SWE-bench-style benchmarks are the same population — a mined task
can literally *be* a benchmark task, so n-gram checks are not enough. This gate
matches the pool against the evaluator's benchmark identity index
(`bench_identity/<base>.identity.json`, Edge 2) and quarantines every hit with
full evidence; in the per-language pool it never deletes a task directory and
never silently filters — but pool-stage hits ARE removed from the derived
merged/stratified exports tracer consumes (quarantine.yaml is the taint
authority; re-running extract_verified_tasks.py alone can never purge them
because its manifest is presence-derived).

Two stages, both fail-closed on a missing/unparseable index:

  pr_ids   BEFORE `legoflow-curator create` — filters `{lang}_pr_ids.txt` so the
           expensive Docker Oracle/NOP minutes are never spent on a benchmark
           PR (normative fix 6). Only repo+PR identity is knowable at this
           stage; the path/patch rules need the pool stage.
  pool     AFTER create — scrubs `verifiable_tasks.txt` in place: the rewrite is
           a pure filter that preserves the manifest's append-only ordering
           (survivors keep their relative order, duplicates collapse) and takes
           the same `.verifiable_tasks.lock` `legoflow-curator create` appends
           under, so a concurrent create append is unioned in, never lost.

Match cascade, cheapest first (§2.3); the first level that hits wins:

  (a) repo + pr_number exact
  (b) repo exact + patch-file-path Jaccard >= 0.6
  (c) >= 1 shared normalized test-file path
  (d) identifier-normalized patch sha256 equal OR normalized-token
      Jaccard >= 0.85 (cross-repo: cherry-picked near-duplicate PRs)

Every hit lands in `quarantine.yaml` with {task_id, pr_ref, matched_eval_task,
match_basis[], similarity} and appends a durable skip-file line so the PR stays
excluded in later `create` runs via `--skip-pr-files`.

Skip-file line format — normative fix 1: `legoflow-curator create --skip-pr-files`
reads *task-id* lines parsed as `owner__repo-<PR>` (cli.py `_default_task_id`
and `_parse_task_id_line`: `owner, right = line.split("__"); repo, pr =
right.rsplit("-")`). A literal `owner__repo-pr-N` line would parse its repo as
`repo-pr` and never suppress anything, so this script emits the
double-underscore task-id form with the bare PR number and additionally accepts
`owner/repo:pr-N` and `owner__repo-pr-N` on INPUT, normalizing both.

--minhash (default on) adds a stdlib minhash similarity *estimate* to the
quarantine evidence; the exact token Jaccard above always decides the verdict,
so disabling it changes only the reported evidence, never the outcome.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import shutil
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

BLOCK_DIR = Path(__file__).resolve().parents[1]
DEFAULT_QUARANTINE = BLOCK_DIR / "artifacts" / "decontam" / "quarantine.yaml"
DEFAULT_SKIP_FILE = BLOCK_DIR / "artifacts" / "decontam" / "eval_overlap_pr_ids.txt"
QUARANTINE_SCHEMA = "legoflow.decontam.quarantine.v1"

PATCH_PATH_JACCARD_MIN = 0.6
NORM_TOKEN_JACCARD_MIN = 0.85
MINHASH_PERMUTATIONS = 64

COLON_PR_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+):pr-(\d+)\s*$")
USCORE_PR_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)__([A-Za-z0-9_.-]+?)-pr-(\d+)\s*$")
USCORE_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)__([A-Za-z0-9_.-]+?)-(\d+)\s*$")


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- identity normalization (contract-shared with build_identity_index.py) ------


def normalize_repo_path(path: str) -> str:
    """lowercase, drop leading ./, a/, b/ and leading slashes (repeatedly)."""
    text = str(path).strip().lower()
    while True:
        stripped = False
        for prefix in ("./", "a/", "b/", "/"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                stripped = True
        if not stripped:
            return text.strip()


def normalize_identifiers(text: str) -> str:
    """lowercase; every non-[a-z0-9] run collapses to a single space."""
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def parse_hunks(patch_text: str) -> list[tuple[str, str]]:
    """(normalized file, normalized hunk body) pairs; header lines excluded.

    Byte-for-byte the same algorithm as the evaluator's
    build_identity_index.parse_hunks — the two ends of Edge 2 must agree on
    the serialization or norm_patch_sha256 equality is meaningless.
    """
    hunks: list[tuple[str, str]] = []
    file_name = ""
    body: list[str] = []
    started = False

    def flush() -> None:
        nonlocal body
        if started and body:
            hunks.append((normalize_identifiers(file_name), normalize_identifiers("\n".join(body))))
        body = []

    for line in str(patch_text).splitlines():
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
        if started and line.startswith(("+", "-", " ")):
            body.append(line)
    flush()
    return hunks


def norm_patch_sha256(patch_text: str) -> str:
    """sha256 over the identifier-normalized, sorted-hunk-stable serialization
    (shared contract, CLI item 5)."""
    payload = "\n".join(f"{name}\n{body}" for name, body in sorted(parse_hunks(patch_text)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def norm_tokens(patch_text: str) -> frozenset[str]:
    return frozenset(normalize_identifiers(patch_text).split())


def jaccard(a: frozenset[str] | set[str], b: frozenset[str] | set[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    shared = len(a & b)
    return shared / (len(a) + len(b) - shared)


def patch_file_paths(patch_text: str) -> set[str]:
    """Paths touched by a unified diff, from its headers."""
    paths: set[str] = set()
    for line in str(patch_text).splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 3:
                paths.add(normalize_repo_path(parts[2]))
        elif line.startswith(("+++ ", "--- ")):
            target = line[4:].split("\t")[0].strip()
            if target and target != "/dev/null":
                paths.add(normalize_repo_path(target))
    return {p for p in paths if p}


def minhash_signature(tokens: frozenset[str],
                      permutations: int = MINHASH_PERMUTATIONS) -> list[int]:
    signature = []
    for seed in range(permutations):
        lowest = None
        for token in sorted(tokens):
            digest = hashlib.sha1(f"{seed}|{token}".encode("utf-8")).digest()
            value = int.from_bytes(digest[:8], "big")
            if lowest is None or value < lowest:
                lowest = value
        signature.append(lowest or 0)
    return signature


def minhash_estimate(a: list[int], b: list[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


# --- identity index ----------------------------------------------------------------


def load_index(path: Path) -> dict[str, Any]:
    """Fail-closed load: a gate that cannot see its index must not pass anything."""
    if not path.is_file():
        die(f"benchmark identity index not found: {path} (fail-closed; build it with the "
            f"evaluator's build_identity_index.py before enabling decontam)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot parse benchmark identity index {path}: {exc} (fail-closed)")
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        die(f"benchmark identity index {path} has no entries[] (fail-closed)")
    return data


class IdentityIndex:
    """Precomputed lookups for the cheapest-first cascade."""

    def __init__(self, data: dict[str, Any], with_minhash: bool = True):
        self.dataset = data.get("dataset")
        self.entries: list[dict[str, Any]] = []
        self.by_repo_pr: dict[tuple[str, int], list[int]] = {}
        self.by_repo: dict[str, list[int]] = {}
        self.by_test_path: dict[str, list[int]] = {}
        self.tokenized_entries = 0
        for raw in data["entries"]:
            entry = {
                "task_id": str(raw.get("task_id") or ""),
                "repo": str(raw.get("repo") or "").lower(),
                "pr_number": raw.get("pr_number"),
                "patch_file_paths": frozenset(
                    normalize_repo_path(p) for p in (raw.get("patch_file_paths") or []) if p),
                "test_file_paths": frozenset(
                    normalize_repo_path(p) for p in (raw.get("test_file_paths") or []) if p),
                "norm_patch_sha256": str(raw.get("norm_patch_sha256") or ""),
            }
            tokens = norm_tokens(str(raw.get("patch_text") or ""))
            entry["tokens"] = tokens if tokens else None
            entry["minhash"] = (minhash_signature(tokens)
                                if with_minhash and tokens else None)
            if entry["tokens"]:
                self.tokenized_entries += 1
            self.entries.append(entry)
            index = len(self.entries) - 1
            if isinstance(entry["pr_number"], int):
                self.by_repo_pr.setdefault((entry["repo"], entry["pr_number"]), []).append(index)
            if entry["repo"]:
                self.by_repo.setdefault(entry["repo"], []).append(index)
            for test_path in entry["test_file_paths"]:
                self.by_test_path.setdefault(test_path, []).append(index)

    def match(self, subject: dict[str, Any]) -> dict[str, Any] | None:
        """Cheapest-first cascade; returns evidence for the first level that hits."""
        repo = str(subject.get("repo") or "").lower()
        pr = subject.get("pr_number")

        # (a) repo + pr_number exact
        if isinstance(pr, int):
            for index in sorted(self.by_repo_pr.get((repo, pr), [])):
                entry = self.entries[index]
                return self._evidence(entry, ["repo+pr_number"], 1.0, subject)

        # (b) repo exact + patch-file-path Jaccard
        best = None
        for index in sorted(self.by_repo.get(repo, [])) if repo else []:
            entry = self.entries[index]
            similarity = jaccard(subject.get("patch_file_paths", frozenset()),
                                 entry["patch_file_paths"])
            if similarity >= PATCH_PATH_JACCARD_MIN and (best is None or similarity > best[1]):
                best = (entry, similarity)
        if best:
            return self._evidence(best[0], ["repo+patch_path_jaccard"], best[1], subject)

        # (c) >= 1 shared normalized test-file path
        candidates: dict[int, int] = {}
        for test_path in sorted(subject.get("test_file_paths", frozenset())):
            for index in self.by_test_path.get(test_path, []):
                candidates[index] = candidates.get(index, 0) + 1
        if candidates:
            index = min(candidates,
                        key=lambda i: (-candidates[i], str(self.entries[i]["task_id"])))
            entry = self.entries[index]
            shared = candidates[index]
            denominator = min(max(len(subject.get("test_file_paths", frozenset())), 1),
                              max(len(entry["test_file_paths"]), 1))
            return self._evidence(entry, ["shared_test_path"], shared / denominator, subject,
                                  detail={"shared_test_paths": shared})

        # (d) normalized patch sha256 equal OR normalized-token Jaccard
        subject_shas = set(subject.get("norm_patch_shas") or ())
        best = None
        for index, entry in enumerate(self.entries):
            if entry["norm_patch_sha256"] and entry["norm_patch_sha256"] in subject_shas:
                return self._evidence(entry, ["norm_patch_sha256"], 1.0, subject)
            if not subject.get("tokens") or not entry["tokens"]:
                continue
            similarity = jaccard(subject["tokens"], entry["tokens"])
            if similarity >= NORM_TOKEN_JACCARD_MIN and (best is None or similarity > best[1]):
                best = (entry, similarity)
        if best:
            return self._evidence(best[0], ["norm_token_jaccard"], best[1], subject)
        return None

    def _evidence(self, entry: dict[str, Any], basis: list[str], similarity: float,
                  subject: dict[str, Any], detail: dict[str, Any] | None = None) -> dict[str, Any]:
        evidence = {
            "matched_eval_task": entry["task_id"],
            "matched_repo": entry["repo"],
            "matched_pr_number": entry["pr_number"],
            "match_basis": basis,
            "similarity": round(float(similarity), 6),
        }
        if detail:
            evidence.update(detail)
        if subject.get("minhash") and entry.get("minhash"):
            evidence["similarity_estimate_minhash"] = round(
                minhash_estimate(subject["minhash"], entry["minhash"]), 6)
        return evidence


# --- subject identities -------------------------------------------------------------


def parse_pr_line(line: str) -> tuple[str, str, int] | None:
    """owner/repo:pr-N | owner__repo-pr-N | owner__repo-N -> (owner, repo, pr)."""
    for pattern in (COLON_PR_RE, USCORE_PR_RE, USCORE_RE):
        match = pattern.match(line)
        if match:
            return match.group(1), match.group(2), int(match.group(3))
    return None


def task_id_line(owner: str, repo: str, pr: int) -> str:
    """The line `--skip-pr-files` actually matches (cli.py `_default_task_id`)."""
    return f"{owner.strip().lower()}__{repo.strip().lower()}-{int(pr)}"


def load_patch_texts(task_dir: Path) -> list[str]:
    texts = []
    for relative in ("environment/bug.patch", "solution/fix.patch"):
        path = task_dir / relative
        if path.is_file():
            try:
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return texts


def is_test_path(path: str) -> bool:
    """A patch path counts as a test path by segment/stem (test, tests,
    __tests__ directories; test_*/ *_test file stems) — not by substring, so
    `testing/foo.py` or `contest/x.py` are source paths, not test paths."""
    segments = [segment for segment in str(path).split("/") if segment]
    stem = segments[-1].rsplit(".", 1)[0] if segments else ""
    return (any(segment in ("test", "tests", "__tests__") for segment in segments)
            or stem.startswith("test_") or stem.endswith("_test"))


def pool_task_identity(task_dir: Path, task_id: str, with_minhash: bool = True) -> dict[str, Any]:
    """Identity of one pool task from its own task.toml + patch files."""
    repo = ""
    extra: dict[str, Any] = {}
    try:
        data = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        extra = metadata.get("extra") if isinstance(metadata.get("extra"), dict) else {}
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        pass
    owner = str(extra.get("user") or "").strip()
    repo_name = str(extra.get("repo") or "").strip()
    if owner and repo_name:
        repo = f"{owner}/{repo_name}".lower()
    else:
        parts = task_id.split("__")
        suffix = re.sub(r"-(?:pr-)?\d+$", "", parts[1]) if len(parts) >= 2 else ""
        repo = f"{parts[0]}/{suffix}".lower() if suffix else ""

    pr_number: int | None = None
    match = re.search(r"-(?:pr-)?(\d+)$", task_id)
    if match:
        pr_number = int(match.group(1))
    for raw in (extra.get("pr_number"), extra.get("pr")):
        try:
            pr_number = int(raw)
            break
        except (TypeError, ValueError):
            continue

    texts = load_patch_texts(task_dir)
    patch_text = "\n".join(texts)
    paths: set[str] = set()
    for text in texts:
        paths |= patch_file_paths(text)
    test_paths = {normalize_repo_path(str(p.relative_to(task_dir)))
                  for p in sorted((task_dir / "tests").rglob("*")) if p.is_file()} \
        if (task_dir / "tests").is_dir() else set()
    test_paths |= {p for p in paths if is_test_path(p)}
    tokens = norm_tokens(patch_text)
    return {
        "task_id": task_id,
        "repo": repo,
        "pr_number": pr_number,
        "patch_file_paths": frozenset(paths),
        "test_file_paths": frozenset(p for p in test_paths if p),
        "norm_patch_shas": {norm_patch_sha256(text) for text in texts} | (
            {norm_patch_sha256(patch_text)} if texts else set()),
        "tokens": tokens if tokens else None,
        "minhash": minhash_signature(tokens) if with_minhash and tokens else None,
    }


def subject_from_pr(owner: str, repo: str, pr: int) -> dict[str, Any]:
    """A PR id line knows only repo+pr; the path/patch cascade stays inert."""
    return {
        "task_id": task_id_line(owner, repo, pr),
        "repo": f"{owner}/{repo}".lower(),
        "pr_number": pr,
        "patch_file_paths": frozenset(),
        "test_file_paths": frozenset(),
        "norm_patch_shas": set(),
        "tokens": None,
        "minhash": None,
    }


# --- evidence / skip-file writes ------------------------------------------------------


def append_quarantine(path: Path, stage: str, index_path: Path, dataset: Any, lang: str,
                      entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for entry in loaded.get("entries") or []:
                if isinstance(entry, dict) and entry.get("task_id"):
                    existing[str(entry["task_id"])] = entry
        except (OSError, yaml.YAMLError) as exc:
            die(f"cannot read existing quarantine {path}: {exc}")
    for entry in entries:
        previous = existing.get(entry["task_id"])
        if previous:
            # Merge, never overwrite: the first insert's provenance
            # (stage/language/index) and the best similarity are preserved;
            # only match_basis unions across runs.
            merged = dict(previous)
            merged["match_basis"] = sorted(set(previous.get("match_basis") or [])
                                           | set(entry.get("match_basis") or []))
            try:
                merged["similarity"] = max(float(previous.get("similarity") or 0.0),
                                           float(entry.get("similarity") or 0.0))
            except (TypeError, ValueError):
                pass
            existing[entry["task_id"]] = merged
        else:
            existing[entry["task_id"]] = entry
    payload = {
        "schema": QUARANTINE_SCHEMA,
        "stage": stage,
        "index": str(index_path),
        "index_dataset": dataset,
        "language": lang,
        "generated_at": now_iso(),
        "entries": [existing[key] for key in sorted(existing)],
    }
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=True, allow_unicode=True)
    tmp.replace(path)


def append_skip_lines(path: Path, lines: list[str]) -> int:
    """Append new unique skip lines (existing order untouched)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    known: list[str] = []
    if path.exists():
        known = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.startswith("#")]
    added = 0
    with path.open("a", encoding="utf-8") as fh:
        for line in lines:
            if line in known:
                continue
            fh.write(line + "\n")
            known.append(line)
            added += 1
    return added


def read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        die(f"cannot read {path}: {exc}")


def rewrite_manifest(manifest: Path, survivors: list[str], known: set[str]) -> None:
    """Filter in place under the manifest's OWN append lock: keep the
    append-only ordering (survivors keep relative order, duplicates collapse),
    atomic replace. `known` is the id set the caller read BEFORE matching; ids
    appended since then (a concurrent `legoflow-curator create` shard holding
    the same lock) are unioned in after the survivors — never silently
    dropped."""
    manifest.parent.mkdir(parents=True, exist_ok=True)
    lock_path = manifest.with_name(f".{manifest.stem}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_fh:  # never truncate the lock
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            late: list[str] = []
            late_seen: set[str] = set()
            for line in read_lines(manifest):
                task_id = line.strip()
                if not task_id or task_id.startswith("#") or task_id in late_seen:
                    continue
                if task_id in known:
                    continue  # the caller already classified this id
                late_seen.add(task_id)
                late.append(task_id)  # unmatched newcomer: kept, like the no-dir WARN path
            tmp = manifest.with_name(manifest.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for task_id in dict.fromkeys(survivors + late):
                    fh.write(task_id + "\n")
            tmp.replace(manifest)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


# --- stages ---------------------------------------------------------------------------


def _config_output_dir(key: str, default: str) -> Path:
    """Output dir from config.yaml (runtime_info.output.<key>.path), falling
    back to the block-relative historical default — the same resolution
    extract_verified_tasks.py uses for the merged pool."""
    try:
        with open(BLOCK_DIR / "config.yaml", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        p = (((data.get("runtime_info") or {}).get("output") or {}).get(key) or {}).get("path")
    except OSError:
        p = None
    if not p:
        return BLOCK_DIR / default
    path = Path(str(p))
    return path if path.is_absolute() else BLOCK_DIR / path


def _remove_pool_entry(path: Path) -> bool:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return True
    if path.is_dir():
        shutil.rmtree(path)
        return True
    return False


def scrub_derived_pools(quarantined_ids: list[str], merged_dir: Path,
                        stratified_dir: Path) -> None:
    """Propagate pool-stage quarantine into the derived exports tracer consumes.

    extract_verified_tasks.py derives the merged manifest from DIRECTORY
    PRESENCE (deliberately — see its own comment about the 28 lost tasks), so
    it can never remove a quarantined task once copied; select_stratified_tasks
    symlinks into merged. The scrub itself is therefore the one writer for
    removals: delete each tainted id from both derived pools and rewrite their
    manifests. No-ops for any derived pool that does not exist.
    """
    if merged_dir.is_dir():
        removed = [tid for tid in quarantined_ids if _remove_pool_entry(merged_dir / tid)]
        if removed:
            present = sorted(p.name for p in merged_dir.iterdir() if p.is_dir())
            (merged_dir / "verifiable_tasks.txt").write_text(
                "\n".join(present) + "\n", encoding="utf-8")
            print(f"            merged export pruned ({len(removed)} task dir(s)): "
                  f"{merged_dir}")
            print("            NOTE: active tracer staging symlinking a pruned task "
                  "now dangles; re-stage before the next rollout", file=sys.stderr)
    if stratified_dir.is_dir():
        removed = [tid for tid in quarantined_ids if _remove_pool_entry(stratified_dir / tid)]
        if removed:
            manifest = stratified_dir / "verifiable_tasks.txt"
            if manifest.is_file():
                kept = [ln for ln in read_lines(manifest)
                        if ln.startswith("#") or ln.strip() not in set(quarantined_ids)]
                manifest.write_text("\n".join(kept) + "\n", encoding="utf-8")
            print(f"            stratified export pruned ({len(removed)} link(s)): "
                  f"{stratified_dir}")
            print("            re-run scripts/select_stratified_tasks.py to re-derive "
                  "quotas (selection_report.json counts are now stale)")


def run_pr_ids(args: argparse.Namespace, index: IdentityIndex) -> int:
    pr_ids_path = Path(args.pr_ids)
    if not pr_ids_path.is_file():
        die(f"--pr-ids file not found: {pr_ids_path}")
    hits: list[dict[str, Any]] = []
    survivors: list[str] = []
    seen: set[str] = set()
    unparseable = 0
    for line in read_lines(pr_ids_path):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parsed = parse_pr_line(stripped)
        if parsed is None:
            unparseable += 1
            print(f"WARN: unparseable PR id line kept as-is: {stripped!r}", file=sys.stderr)
            if stripped not in seen:
                survivors.append(stripped)
                seen.add(stripped)
            continue
        owner, repo, pr = parsed
        canonical = f"{owner}/{repo}:pr-{pr}"
        if canonical in seen:
            continue
        seen.add(canonical)
        subject = subject_from_pr(owner, repo, pr)
        evidence = index.match(subject)
        if evidence is None:
            survivors.append(canonical)
            continue
        hits.append({
            "task_id": subject["task_id"],
            "pr_ref": canonical,
            "stage": "pr_ids",
            "language": args.lang or "",
            "index": str(Path(args.index)),
            **evidence,
        })
    if unparseable:
        print(f"NOTE: {unparseable} line(s) did not match any known PR id format "
              f"('owner/repo:pr-N', 'owner__repo-pr-N', 'owner__repo-N') and were kept")

    out_path = Path(args.out) if args.out else pr_ids_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text("\n".join(survivors) + ("\n" if survivors else ""), encoding="utf-8")
    tmp.replace(out_path)
    quarantine = Path(args.quarantine) if args.quarantine else DEFAULT_QUARANTINE
    skip_file = Path(args.skip_file) if args.skip_file else DEFAULT_SKIP_FILE
    if hits:
        append_quarantine(quarantine, "pr_ids", Path(args.index), index.dataset,
                          args.lang or "", hits)
        added = append_skip_lines(skip_file, [h["task_id"] for h in hits])
        print(f"pr_ids stage: {len(hits)} benchmark PR(s) quarantined -> {quarantine}")
        print(f"             skip file {skip_file} (+{added} line(s))")
    else:
        print("pr_ids stage: no benchmark overlap found")
    print(f"             {len(survivors)} PR id(s) kept -> {out_path}")
    for hit in hits:
        print(f"  hit {hit['pr_ref']:<40} {','.join(hit['match_basis'])} "
              f"(eval task {hit['matched_eval_task']}, similarity {hit['similarity']})")
    return 0


def run_pool(args: argparse.Namespace, index: IdentityIndex) -> int:
    pool_dir = Path(args.pool_dir)
    if not pool_dir.is_dir():
        die(f"--pool-dir not found: {pool_dir}")
    manifest = Path(args.verifiable) if args.verifiable else pool_dir / "verifiable_tasks.txt"
    if not manifest.is_file():
        die(f"verifiable_tasks.txt not found: {manifest} (the manifest is the authoritative "
            f"verified-task contract; refusing to scrub an unknown pool)")
    task_ids = [line.strip() for line in read_lines(manifest)
                if line.strip() and not line.startswith("#")]
    if index.tokenized_entries == 0:
        print("WARN: identity index has no patch_text on any entry — level (d) "
              "token-Jaccard/minhash similarity is disabled; rebuild the index "
              "with the evaluator's build_identity_index.py", file=sys.stderr)

    hits: list[dict[str, Any]] = []
    skip_lines: list[str] = []
    survivors: list[str] = []
    seen: set[str] = set()
    for task_id in task_ids:
        if task_id in seen:
            continue  # dedup while preserving first-seen order
        seen.add(task_id)
        task_dir = pool_dir / task_id
        if not task_dir.is_dir():
            print(f"WARN: manifest id has no task directory, keeping it listed: {task_id}",
                  file=sys.stderr)
            survivors.append(task_id)
            continue
        subject = pool_task_identity(task_dir, task_id, args.minhash)
        evidence = index.match(subject)
        if evidence is None:
            survivors.append(task_id)
            continue
        pr_ref = subject["repo"]
        if subject["pr_number"] is not None and subject["repo"] and "/" in subject["repo"]:
            owner, repo = subject["repo"].split("/", 1)
            pr_ref = task_id_line(owner, repo, subject["pr_number"])
            skip_lines.append(pr_ref)
        else:
            print(f"WARN: hit {task_id} has no owner/repo/pr — recorded in quarantine "
                  f"only, no skip-file line", file=sys.stderr)
        hits.append({"task_id": task_id, "pr_ref": pr_ref, "stage": "pool",
                     "language": pool_dir.name, "index": str(Path(args.index)),
                     **evidence})

    if hits:
        rewrite_manifest(manifest, survivors, set(task_ids))
        quarantine = Path(args.quarantine) if args.quarantine else DEFAULT_QUARANTINE
        skip_file = Path(args.skip_file) if args.skip_file else DEFAULT_SKIP_FILE
        append_quarantine(quarantine, "pool", Path(args.index), index.dataset,
                          pool_dir.name, hits)
        added = append_skip_lines(skip_file, skip_lines)
        merged_dir = getattr(args, "merged_dir", None)
        stratified_dir = getattr(args, "stratified_dir", None)
        scrub_derived_pools(
            [h["task_id"] for h in hits],
            Path(merged_dir) if merged_dir
            else _config_output_dir("merged_tasks_dir", "artifacts/merged_swe_tasks"),
            Path(stratified_dir) if stratified_dir
            else _config_output_dir("stratified_tasks_dir", "artifacts/stratified_swe_tasks"))
        print(f"pool stage: {len(hits)} task(s) quarantined out of {len(task_ids)} listed")
        print(f"            manifest rewritten in place (order preserved, deduped): {manifest}")
        print(f"            quarantine {quarantine}; skip file {skip_file} (+{added} line(s))")
    else:
        print(f"pool stage: no benchmark overlap among {len(task_ids)} manifest task(s); "
              f"manifest untouched")
    for hit in hits:
        print(f"  hit {hit['task_id']:<44} {','.join(hit['match_basis'])} "
              f"(eval task {hit['matched_eval_task']}, similarity {hit['similarity']})")
    return 0


# --- CLI ---------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", required=True,
                    help="benchmark identity index JSON (evaluator bench_identity output)")
    ap.add_argument("--stage", required=True, choices=["pr_ids", "pool"],
                    help="pr_ids = pre-create PR filter; pool = post-create manifest scrub")
    ap.add_argument("--pr-ids", help="[pr_ids] the {lang}_pr_ids.txt file to filter")
    ap.add_argument("--lang", default="", help="[pr_ids] language tag recorded in quarantine")
    ap.add_argument("--out", default=None,
                    help="[pr_ids] rewritten PR id file (default: rewrite in place via "
                         "tmp+replace; passing --out is the recommended target so the "
                         "original stays recoverable as survivors + quarantine entries)")
    ap.add_argument("--pool-dir", help="[pool] e.g. artifacts/swe_tasks/py-cc")
    ap.add_argument("--merged-dir", default=None,
                    help="[pool] merged export to prune quarantined ids from (default: "
                         "runtime_info.output.merged_tasks_dir.path)")
    ap.add_argument("--stratified-dir", default=None,
                    help="[pool] stratified export to prune quarantined ids from (default: "
                         "runtime_info.output.stratified_tasks_dir.path or "
                         "artifacts/stratified_swe_tasks)")
    ap.add_argument("--verifiable", default=None,
                    help="[pool] manifest path (default: <pool-dir>/verifiable_tasks.txt)")
    ap.add_argument("--quarantine", default=None,
                    help=f"quarantine evidence file (default: {DEFAULT_QUARANTINE})")
    ap.add_argument("--skip-file", default=None,
                    help=f"durable skip file for --skip-pr-files (default: {DEFAULT_SKIP_FILE})")
    ap.add_argument("--minhash", action=argparse.BooleanOptionalAction, default=True,
                    help="attach a stdlib minhash similarity estimate to the evidence; the "
                         "exact cascade always decides (default: %(default)s)")
    return ap


def warn_stale_index(index_path: Path, data: dict[str, Any]) -> None:
    """Best-effort staleness WARN: the identity index is derived from the gold
    dataset next to it — if the gold dir has files newer than the index's
    generated_at, path/sha evidence may be outdated. Never fatal."""
    generated = str(data.get("generated_at") or "")
    try:
        stamp = datetime.strptime(generated, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return
    base = index_path.stem
    if base.endswith(".identity"):
        base = base[: -len(".identity")]
    gold_dir = index_path.parent.parent / "datasets" / base
    if not gold_dir.is_dir():
        return
    newest = max((path.stat().st_mtime for path in gold_dir.rglob("*") if path.is_file()),
                 default=0.0)
    if newest and stamp.timestamp() < newest:
        print(f"WARN: identity index {index_path.name} predates the newest file in "
              f"{gold_dir} — rebuild it with the evaluator's build_identity_index.py "
              f"or the level (b)/(c) path evidence may be stale", file=sys.stderr)


def main() -> int:
    args = build_parser().parse_args()
    index_path = Path(args.index)
    index_data = load_index(index_path)
    warn_stale_index(index_path, index_data)
    index = IdentityIndex(index_data, with_minhash=args.minhash)
    if args.stage == "pr_ids":
        if not args.pr_ids:
            die("--stage pr_ids requires --pr-ids <file>")
        return run_pr_ids(args, index)
    if not args.pool_dir:
        die("--stage pool requires --pool-dir <swe_tasks/{lang}-cc>")
    return run_pool(args, index)


if __name__ == "__main__":
    sys.exit(main())
