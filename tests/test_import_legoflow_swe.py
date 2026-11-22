"""Curator LegoFlow-SWE attach gate + import contract.

`blocks/curator/scripts/import_legoflow_swe.py` is an EXPLICIT, DEFAULT-OFF
escape hatch: default pipeline behavior (loop starts from the Curator) must be
untouched unless the user passes `--attach legoflow-swe` AND sets
`LEGOFLOW_RSI_ATTACH=legoflow-swe`. These checks pin that contract:

  * the gate refuses (exit 2, message naming the default flow) unless BOTH
    switches are present
  * `--dry-run` writes nothing at all
  * a real import lands tasks in the existing `swe_tasks/{lang}-cc/` pool
    layout, appends ids to that pool's `verifiable_tasks.txt`, and records a
    provenance sidecar manifest under `artifacts/attach/`
  * re-running is idempotent (ids already listed are skipped, not doubled)
  * invalid / unmappable tasks are skipped with a recorded reason

Everything runs against a local fixture dataset through `--from-dir` — no
network, no Docker, no Hugging Face. Each case shells out to the real script
so argparse wiring, exit codes, and stdout are exercised exactly as shipped.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "blocks" / "curator" / "scripts" / "import_legoflow_swe.py"
ATTACH_ENV = "LEGOFLOW_RSI_ATTACH"
ATTACH_VALUE = "legoflow-swe"

# Fixture task ids, per subset and language.
PY_TASK = "ownera__repoa-101"
GO_TASK = "ownerb__repob-202"
BROKEN_TASK = "ownerc__repoc-303"        # missing solution/fix.patch
UNMAPPABLE_TASK = "ownerd__repod-404"    # tags carry no known language
ANTI_HACK_TASK = "ownere__repoe-505"     # tasks-anti-hack subset, javascript


def write_task(root: Path, task_id: str, tags: list[str], *, fix_patch: bool = True,
               test_sh: bool = True, solve_sh: bool = True) -> None:
    """One minimal-but-valid Harbor task directory in the dataset's layout."""
    task = root / task_id
    (task / "environment").mkdir(parents=True)
    (task / "solution").mkdir()
    (task / "tests").mkdir()
    (task / "task.toml").write_text(
        'version = "1.0"\n\n[metadata]\ndifficulty = "medium"\n'
        f'category = "bugfix"\ntags = [ {" ".join(chr(34) + t + chr(34) + "," for t in tags)} ]\n',
        encoding="utf-8",
    )
    (task / "instruction.md").write_text(f"# {task_id}\n\nFix the bug.\n", encoding="utf-8")
    (task / "environment" / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    (task / "environment" / "bug.patch").write_text("--- a/x.py\n+++ b/x.py\n", encoding="utf-8")
    if fix_patch:
        (task / "solution" / "fix.patch").write_text("--- a/x.py\n+++ b/x.py\n", encoding="utf-8")
    if solve_sh:
        (task / "solution" / "solve.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    if test_sh:
        (task / "tests" / "test.sh").write_text(
            "#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n", encoding="utf-8")


def make_dataset(root: Path) -> Path:
    """A miniature LegoFlow-SWE checkout: tasks/ + tasks-anti-hack/."""
    tasks = root / "tasks"
    tasks.mkdir(parents=True)
    write_task(tasks, PY_TASK, ["python", "library", "requests", "wrong-default"])
    write_task(tasks, GO_TASK, ["go", "cli", "cobra", "missing-fallback"])
    write_task(tasks, BROKEN_TASK, ["python", "backend", "flask", "incomplete-validation"],
               fix_patch=False)
    write_task(tasks, UNMAPPABLE_TASK, ["forth", "cli", "gforth", "wrong-default"])
    anti = root / "tasks-anti-hack"
    anti.mkdir()
    write_task(anti, ANTI_HACK_TASK, ["javascript", "frontend", "react", "wrong-default"])
    return root


def run_import(pool: Path, attach_dir: Path, *extra: str, attach: str | None = ATTACH_VALUE,
               env_flag: bool = True, dataset_dir: Path | None = None):
    """Shell out to the script; gate switches are opt-in per case."""
    env = dict(os.environ)
    env.pop(ATTACH_ENV, None)
    if env_flag:
        env[ATTACH_ENV] = ATTACH_VALUE
    cmd = [sys.executable, str(SCRIPT)]
    if attach is not None:
        cmd += ["--attach", attach]
    cmd += ["--pool-root", str(pool), "--attach-dir", str(attach_dir)]
    if dataset_dir is not None:
        cmd += ["--from-dir", str(dataset_dir)]
    cmd += list(extra)
    return subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=120)


def read_manifest(pool: Path, lang: str) -> list[str]:
    record = pool / f"{lang}-cc" / "verifiable_tasks.txt"
    if not record.exists():
        return []
    return [ln for ln in record.read_text(encoding="utf-8").splitlines() if ln.strip()]


def attach_manifest(attach_dir: Path) -> dict:
    return json.loads((attach_dir / "manifest.json").read_text(encoding="utf-8"))


# --- the gate -----------------------------------------------------------------


def test_gate_refuses_without_any_switch(tmp_path):
    result = run_import(tmp_path / "pool", tmp_path / "attach", attach=None, env_flag=False)
    assert result.returncode == 2, result.stderr
    assert "REFUSED" in result.stderr
    assert "Curator" in result.stderr
    assert not (tmp_path / "pool").exists()
    assert not (tmp_path / "attach").exists()


@pytest.mark.parametrize("attach,env_flag", [(ATTACH_VALUE, False), (None, True)])
def test_gate_refuses_when_only_one_switch_present(tmp_path, attach, env_flag):
    result = run_import(tmp_path / "pool", tmp_path / "attach", attach=attach, env_flag=env_flag)
    assert result.returncode == 2, result.stderr
    assert "default flow" in result.stderr
    assert not (tmp_path / "pool").exists()


def test_gate_refuses_wrong_attach_value(tmp_path):
    result = run_import(tmp_path / "pool", tmp_path / "attach", attach="legoflow-swe-extra")
    assert result.returncode == 2, result.stderr
    assert "must be exactly" in result.stderr


def test_gate_refusal_beats_missing_source(tmp_path):
    """A bad --from-dir never leaks past the gate (gate is checked first)."""
    result = run_import(tmp_path / "pool", tmp_path / "attach", attach=None, env_flag=False,
                        dataset_dir=tmp_path / "nope")
    assert result.returncode == 2, result.stderr
    assert "REFUSED" in result.stderr


# --- dry run ------------------------------------------------------------------


def test_dry_run_touches_nothing(tmp_path):
    dataset = make_dataset(tmp_path / "dataset")
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    result = run_import(pool, attach_dir, "--dry-run", dataset_dir=dataset)
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert not pool.exists()
    assert not attach_dir.exists()


def test_dry_run_reports_the_same_candidates_as_a_real_run(tmp_path):
    dataset = make_dataset(tmp_path / "dataset")
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    dry = run_import(pool, attach_dir, "--dry-run", dataset_dir=dataset)
    assert dry.returncode == 0, dry.stderr
    # same candidate accounting as a real run: 2 attachable, 2 skipped
    assert re.search(r"^NEW\s+2\s*$", dry.stdout, re.MULTILINE)
    assert re.search(r"^SKIPPED\s+2\s*$", dry.stdout, re.MULTILINE)
    assert "language-unmappable" in dry.stdout
    assert "missing solution/fix.patch" in dry.stdout


# --- a real import ------------------------------------------------------------


def test_import_writes_pool_layout_manifest_and_appends(tmp_path):
    dataset = make_dataset(tmp_path / "dataset")
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    result = run_import(pool, attach_dir, dataset_dir=dataset)
    assert result.returncode == 0, result.stderr
    assert "NEXT STEP" in result.stdout
    assert "extract_verified_tasks.py" in result.stdout

    # pool layout: {lang}-cc/<task_id>/ with the dataset files copied verbatim
    for task_id, lang in ((PY_TASK, "py"), (GO_TASK, "go")):
        copied = pool / f"{lang}-cc" / task_id
        assert (copied / "task.toml").is_file(), f"missing {copied}/task.toml"
        assert (copied / "instruction.md").read_text() == \
            (dataset / "tasks" / task_id / "instruction.md").read_text()
        assert (copied / "solution" / "fix.patch").is_file()
        assert read_manifest(pool, lang) == [task_id]

    # provenance sidecar manifest
    manifest = attach_manifest(attach_dir)
    assert manifest["attach_flag"] == ATTACH_VALUE
    assert manifest["subset"] == "tasks"
    assert manifest["dataset_dir"] == str(dataset)
    assert manifest["provenance"] == ["imported:legoflow-swe:tasks"]
    imported_ids = {e["task_id"]: e for e in manifest["imported"]}
    assert set(imported_ids) == {PY_TASK, GO_TASK}
    assert imported_ids[PY_TASK]["pool"] == "py-cc"
    assert imported_ids[PY_TASK]["provenance"] == "imported:legoflow-swe:tasks"
    assert manifest["counts"]["imported"] == 2
    assert manifest["counts"]["appended"] == 2
    assert manifest["counts"]["by_language"] == {"go": 1, "py": 1}


def test_invalid_and_unmappable_tasks_skipped_with_reason(tmp_path):
    dataset = make_dataset(tmp_path / "dataset")
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    result = run_import(pool, attach_dir, dataset_dir=dataset)
    assert result.returncode == 0, result.stderr

    manifest = attach_manifest(attach_dir)
    reasons = {e["task_id"]: e["reason"] for e in manifest["skipped"]}
    assert BROKEN_TASK in reasons and "fix.patch" in reasons[BROKEN_TASK]
    assert UNMAPPABLE_TASK in reasons and "language" in reasons[UNMAPPABLE_TASK]
    assert not (pool / "py-cc" / BROKEN_TASK).exists()
    assert all(BROKEN_TASK not in read_manifest(pool, lang) for lang in ("py", "go"))
    assert manifest["counts"]["skipped"] == 2


def test_import_is_idempotent(tmp_path):
    dataset = make_dataset(tmp_path / "dataset")
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    first = run_import(pool, attach_dir, dataset_dir=dataset)
    assert first.returncode == 0, first.stderr

    second = run_import(pool, attach_dir, dataset_dir=dataset)
    assert second.returncode == 0, second.stderr
    assert "Nothing new to attach" in second.stdout

    # no duplicated ids, and the latest manifest reports everything skipped
    assert read_manifest(pool, "py") == [PY_TASK]
    assert read_manifest(pool, "go") == [GO_TASK]
    manifest = attach_manifest(attach_dir)
    assert manifest["counts"]["imported"] == 0
    assert manifest["counts"]["appended"] == 0
    reasons = {e["task_id"]: e["reason"] for e in manifest["skipped"]}
    assert "already-imported" in reasons[PY_TASK]
    assert "already-imported" in reasons[GO_TASK]
    # invalid candidates are re-diagnosed on every run, never silently dropped
    assert "missing solution/fix.patch" in reasons[BROKEN_TASK]


def test_existing_unlisted_destination_is_never_clobbered(tmp_path):
    dataset = make_dataset(tmp_path / "dataset")
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    # a locally curated task dir with the same id that is NOT in the manifest
    local = pool / "py-cc" / PY_TASK
    local.mkdir(parents=True)
    (local / "task.toml").write_text('version = "1.0"\n', encoding="utf-8")

    result = run_import(pool, attach_dir, dataset_dir=dataset)
    assert result.returncode == 0, result.stderr
    assert (local / "task.toml").read_text() == 'version = "1.0"\n'  # untouched
    assert read_manifest(pool, "py") == []                            # not listed
    manifest = attach_manifest(attach_dir)
    entry = next(e for e in manifest["skipped"] if e["task_id"] == PY_TASK)
    assert "destination exists" in entry["reason"]


def test_lang_filter_and_limit(tmp_path):
    dataset = make_dataset(tmp_path / "dataset")
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"

    only_py = run_import(pool, attach_dir, "--lang-filter", "py", dataset_dir=dataset)
    assert only_py.returncode == 0, only_py.stderr
    assert read_manifest(pool, "py") == [PY_TASK]
    assert read_manifest(pool, "go") == []
    reasons = {e["task_id"]: e["reason"] for e in attach_manifest(attach_dir)["skipped"]}
    assert "lang-filter-excluded" in reasons[GO_TASK]

    limited = run_import(pool, attach_dir, "--limit", "1", dataset_dir=dataset)
    assert limited.returncode == 0, limited.stderr
    assert len(read_manifest(pool, "go")) == 1  # GO_TASK, the limit stopped after it


def test_subset_both_attaches_anti_hack_with_distinct_provenance(tmp_path):
    dataset = make_dataset(tmp_path / "dataset")
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    result = run_import(pool, attach_dir, "--subset", "both", dataset_dir=dataset)
    assert result.returncode == 0, result.stderr
    assert read_manifest(pool, "js") == [ANTI_HACK_TASK]
    manifest = attach_manifest(attach_dir)
    assert manifest["subset"] == "both"
    assert manifest["provenance"] == ["imported:legoflow-swe:tasks",
                                      "imported:legoflow-swe:tasks-anti-hack"]
    entry = next(e for e in manifest["imported"] if e["task_id"] == ANTI_HACK_TASK)
    assert entry["provenance"] == "imported:legoflow-swe:tasks-anti-hack"


def test_subset_twin_same_id_imported_once(tmp_path):
    """The real dataset re-issues the same ids in tasks-anti-hack/; one attach."""
    dataset = make_dataset(tmp_path / "dataset")
    # an anti-hack twin of the py task under the SAME id (hardened re-issue)
    write_task(dataset / "tasks-anti-hack", PY_TASK, ["python", "library", "requests",
                                                      "wrong-default"])
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    result = run_import(pool, attach_dir, "--subset", "both", dataset_dir=dataset)
    assert result.returncode == 0, result.stderr
    assert read_manifest(pool, "py") == [PY_TASK]          # listed exactly once
    assert len(list((pool / "py-cc").glob(PY_TASK))) == 1  # one directory
    manifest = attach_manifest(attach_dir)
    entries = [e for e in manifest["imported"] if e["task_id"] == PY_TASK]
    assert len(entries) == 1
    assert entries[0]["provenance"] == "imported:legoflow-swe:tasks"  # tasks/ wins
    twin = next(e for e in manifest["skipped"] if e["task_id"] == PY_TASK)
    assert "already-imported" in twin["reason"]


def test_missing_subset_dir_fails_cleanly(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    result = run_import(tmp_path / "pool", tmp_path / "attach", dataset_dir=dataset)
    assert result.returncode == 1, result.stdout
    assert "no ['tasks'] subset dir" in result.stderr
    assert not (tmp_path / "attach" / "manifest.json").exists()


def test_task_without_tests_test_sh_is_refused(tmp_path):
    """Curator parity: `create` only appends ids whose task dir carries
    tests/test.sh (the verifier), so the import must refuse one too — a task
    the pool's own gate would reject must never enter the verified manifest."""
    dataset = make_dataset(tmp_path / "dataset")
    # tests/ exists and is non-empty, but holds only notes.txt — no verifier.
    write_task(dataset / "tasks", "ownerf__repof-606", ["python", "cli", "tool", "typo"],
               test_sh=False)
    (dataset / "tasks" / "ownerf__repof-606" / "tests" / "notes.txt").write_text(
        "not a verifier\n", encoding="utf-8")
    # A zero-byte solve script is likewise refused (0-byte reward scripts).
    write_task(dataset / "tasks", "ownerg__repog-707", ["python", "cli", "tool", "typo"],
               solve_sh=False)
    (dataset / "tasks" / "ownerg__repog-707" / "solution" / "solve.sh").write_text(
        "", encoding="utf-8")

    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    result = run_import(pool, attach_dir, dataset_dir=dataset)
    assert result.returncode == 0, result.stderr
    manifest = attach_manifest(attach_dir)
    reasons = {e["task_id"]: e["reason"] for e in manifest["skipped"]}
    assert reasons["ownerf__repof-606"] == "missing tests/test.sh"
    assert reasons["ownerg__repog-707"] == "empty solution/solve.sh"
    for task_id in ("ownerf__repof-606", "ownerg__repog-707"):
        assert not (pool / "py-cc" / task_id).exists()
        assert task_id not in read_manifest(pool, "py")


def test_attach_manifest_is_cumulative_across_runs(tmp_path):
    """A later run must never erase the import history: imported[] from prior
    manifests is merged under the attach-dir lock before the rewrite."""
    dataset = make_dataset(tmp_path / "dataset")
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    assert run_import(pool, attach_dir, dataset_dir=dataset).returncode == 0
    assert run_import(pool, attach_dir, dataset_dir=dataset).returncode == 0  # no-op run
    manifest = attach_manifest(attach_dir)
    assert manifest["counts"]["imported"] == 0        # counts describe THIS run
    imported = {e["task_id"]: e for e in manifest["imported"]}
    assert PY_TASK in imported and GO_TASK in imported  # history preserved
    assert imported[PY_TASK]["provenance"] == "imported:legoflow-swe:tasks"
    assert (attach_dir / ".manifest.lock").is_file()


def test_failed_copy_does_not_poison_the_in_run_listing(tmp_path, monkeypatch):
    """A task whose copy fails mid-run is discarded from the listed cache too,
    so an anti-hack twin of the same id is imported (not mislabeled
    already-imported) and the skip reason stays accurate."""
    dataset = make_dataset(tmp_path / "dataset")
    unreadable = dataset / "tasks" / PY_TASK / "instruction.md"
    unreadable.chmod(0o000)
    # a readable anti-hack twin under the SAME id, processed later in one run
    write_task(dataset / "tasks-anti-hack", PY_TASK, ["python", "library", "requests",
                                                      "wrong-default"])
    pool, attach_dir = tmp_path / "pool", tmp_path / "attach"
    try:
        result = run_import(pool, attach_dir, "--subset", "both", dataset_dir=dataset)
    finally:
        unreadable.chmod(0o644)
    assert result.returncode == 0, result.stderr
    manifest = attach_manifest(attach_dir)
    reasons = {e["task_id"]: e["reason"] for e in manifest["skipped"]}
    assert "copy failed" in reasons[PY_TASK]
    imported = [e for e in manifest["imported"] if e["task_id"] == PY_TASK]
    assert len(imported) == 1
    assert imported[0]["provenance"] == "imported:legoflow-swe:tasks-anti-hack"
    assert read_manifest(pool, "py") == [PY_TASK]      # the twin landed, once


def test_dry_run_help_states_the_full_download(tmp_path):
    """--dry-run without --from-dir still downloads the whole HF snapshot —
    the help text must say so (the runbook points preview runs at it)."""
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert "still fully" in result.stdout and "downloaded" in result.stdout


def test_missing_from_dir_fails_cleanly(tmp_path):
    result = run_import(tmp_path / "pool", tmp_path / "attach",
                        dataset_dir=tmp_path / "nowhere")
    assert result.returncode == 1, result.stdout
    assert "--from-dir" in result.stderr
