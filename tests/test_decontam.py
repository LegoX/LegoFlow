"""Curator decontamination gate (RSI_LOOP_PLAN.md §2.3, §10 fix 1) — unit tests.

Covers blocks/curator/scripts/decontam_check.py: the identity match cascade
(repo+PR exact -> patch-path Jaccard -> shared test path -> normalized patch
sha / token Jaccard), both stages (pr_ids pre-create filter, pool manifest
scrub), the fail-closed missing-index behavior, quarantine evidence, the
durable `owner__repo-N` skip-file format (normative fix 1, including the
cli.py suppression smoke test), and append-only manifest ordering.

No network, no Docker; everything runs on tmp_path fixtures.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "blocks" / "curator" / "scripts" / "decontam_check.py"


def load_module():
    spec = importlib.util.spec_from_file_location("decontam_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DC = load_module()


def patch(paths: list[str], body: str = "fix the handler") -> str:
    return "".join(
        f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1,2 +1,3 @@\n"
        f" context line\n+ {body} {i}\n"
        for i, p in enumerate(paths))


SHA_PATCH = patch(["unique/sha.py"], "repair the parser")
TOK_PATCH = patch(["tok/different.py"], "adjust the parser")
# One identifier differs: token Jaccard stays >= 0.85 while the normalized
# sha256 changes, so this exercises the token rule and not the hash rule.
TOK_PATCH_NEAR = TOK_PATCH.replace("adjust", "adjust2")

SKIP_LINE_RE = re.compile(r"^[a-z0-9_.-]+__[a-z0-9_.-]+-\d+$")


def make_index(path: Path) -> Path:
    data = {
        "dataset": "swebench-verified",
        "version": "1.0",
        "entries": [
            {"task_id": "swe__exact-101", "repo": "Owner/Exact", "pr_number": 101,
             "patch_file_paths": ["src/exact.py"],
             "test_file_paths": ["tests/test_exact.py"],
             "norm_patch_sha256": DC.norm_patch_sha256(patch(["src/exact.py"]))},
            {"task_id": "swe__exact-102", "repo": "owner/exact", "pr_number": 102,
             "patch_file_paths": ["src/other.py"], "test_file_paths": ["tests/test_other.py"],
             "norm_patch_sha256": "0" * 64},
            {"task_id": "swe__paths-202", "repo": "owner/paths", "pr_number": 202,
             "patch_file_paths": ["src/a.py", "src/b.py", "src/c.py"],
             "test_file_paths": ["tests/test_paths.py"], "norm_patch_sha256": "1" * 64},
            {"task_id": "swe__tests-303", "repo": "owner/tst", "pr_number": 303,
             "patch_file_paths": ["x/one.py"], "test_file_paths": ["tests/shared_test.py"],
             "norm_patch_sha256": "2" * 64},
            {"task_id": "swe__sha-404", "repo": "owner/sha", "pr_number": 404,
             "patch_file_paths": ["unique/sha.py"], "test_file_paths": ["tests/test_sha.py"],
             "norm_patch_sha256": DC.norm_patch_sha256(SHA_PATCH)},
            {"task_id": "swe__tok-505", "repo": "owner/tok", "pr_number": 505,
             "patch_file_paths": ["tok/different.py"], "test_file_paths": ["tests/test_tok.py"],
             "norm_patch_sha256": "3" * 64, "patch_text": TOK_PATCH},
        ],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def make_task(pool_dir: Path, task_id: str, owner: str, repo: str,
              bug_patch: str | None = None, test_files: list[str] | None = None) -> Path:
    task = pool_dir / task_id
    (task / "environment").mkdir(parents=True, exist_ok=True)
    (task / "solution").mkdir(exist_ok=True)
    (task / "tests").mkdir(exist_ok=True)
    (task / "task.toml").write_text(
        'schema_version = "1.1"\n\n[metadata]\nauthor_name = "test"\n'
        'category = "debugging"\ntags = ["Python"]\n\n'
        f'[metadata.extra]\nuser = "{owner}"\nrepo = "{repo}"\n', encoding="utf-8")
    if bug_patch is not None:
        (task / "environment" / "bug.patch").write_text(bug_patch, encoding="utf-8")
    for name in test_files or ["tests-default.sh"]:
        (task / "tests" / name).write_text("#!/bin/sh\n", encoding="utf-8")
    return task


@pytest.fixture
def env(tmp_path):
    pool = tmp_path / "py-cc"
    make_task(pool, "owner__exact-101", "owner", "exact",
              bug_patch=patch(["src/exact.py"]), test_files=["test_exact.sh"])
    make_task(pool, "owner__paths-999", "owner", "paths",
              bug_patch=patch(["src/a.py", "src/b.py"]), test_files=["test_paths.sh"])
    make_task(pool, "owner__othert-777", "owner", "othert",
              bug_patch=patch(["src/z.py"]), test_files=["shared_test.py"])
    make_task(pool, "owner__near-888", "owner", "near",
              bug_patch=SHA_PATCH, test_files=["test_near.sh"])
    make_task(pool, "owner__tok2-909", "owner", "tok2",
              bug_patch=TOK_PATCH_NEAR, test_files=["test_tok2.sh"])
    make_task(pool, "owner__clean-111", "owner", "clean",
              bug_patch=patch(["src/clean.py"]), test_files=["test_clean.sh"])
    # Append-order preserved (oldest first) with one duplicate line.
    (pool / "verifiable_tasks.txt").write_text(
        "owner__exact-101\nowner__clean-111\nowner__paths-999\nowner__othert-777\n"
        "owner__clean-111\nowner__near-888\nowner__tok2-909\n", encoding="utf-8")
    index = make_index(tmp_path / "bench.identity.json")
    return {
        "root": tmp_path,
        "pool": pool,
        "manifest": pool / "verifiable_tasks.txt",
        "index": index,
        "quarantine": tmp_path / "decontam" / "quarantine.yaml",
        "skip": tmp_path / "decontam" / "eval_overlap_pr_ids.txt",
    }


def run_stage(env, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--index", str(env["index"]),
         "--quarantine", str(env["quarantine"]), "--skip-file", str(env["skip"]), *extra],
        capture_output=True, text=True)


def run_pool(env, *extra):
    return run_stage(env, "--stage", "pool", "--pool-dir", str(env["pool"]), *extra)


def run_pr_ids(env, pr_ids: Path, *extra):
    return run_stage(env, "--stage", "pr_ids", "--pr-ids", str(pr_ids), "--lang", "py", *extra)


# --- pool stage: the match cascade -------------------------------------------------


def test_pool_stage_matches_every_cascade_level(env):
    result = run_pool(env)
    assert result.returncode == 0, result.stdout + result.stderr
    quarantine = yaml.safe_load(env["quarantine"].read_text())
    basis = {entry["task_id"]: entry["match_basis"] for entry in quarantine["entries"]}
    assert basis["owner__exact-101"] == ["repo+pr_number"]
    assert basis["owner__paths-999"] == ["repo+patch_path_jaccard"]
    assert basis["owner__othert-777"] == ["shared_test_path"]
    assert basis["owner__near-888"] == ["norm_patch_sha256"]
    assert basis["owner__tok2-909"] == ["norm_token_jaccard"]
    assert set(basis) == {"owner__exact-101", "owner__paths-999", "owner__othert-777",
                          "owner__near-888", "owner__tok2-909"}
    assert "owner__clean-111" not in basis


def test_pool_stage_quarantine_carries_evidence(env):
    run_pool(env)
    entries = {e["task_id"]: e for e in yaml.safe_load(env["quarantine"].read_text())["entries"]}
    exact = entries["owner__exact-101"]
    assert exact["matched_eval_task"] == "swe__exact-101"
    assert exact["similarity"] == 1.0
    assert exact["pr_ref"] == "owner__exact-101"
    paths = entries["owner__paths-999"]
    assert paths["matched_eval_task"] == "swe__paths-202"
    assert paths["similarity"] == pytest.approx(2 / 3)  # {a,b} vs {a,b,c}
    tok = entries["owner__tok2-909"]
    assert tok["similarity"] >= 0.85
    assert "similarity_estimate_minhash" in tok   # minhash evidence on by default
    sha = entries["owner__near-888"]
    assert sha["matched_eval_task"] == "swe__sha-404"


def test_pool_stage_preserves_manifest_order_of_survivors(env):
    before = env["manifest"].read_text().splitlines()
    run_pool(env)
    after = env["manifest"].read_text().splitlines()
    assert after == ["owner__clean-111"]            # order kept, duplicate collapsed
    assert len(before) == 7


def test_pool_stage_does_not_touch_task_directories(env):
    before = sorted(p.name for p in env["pool"].iterdir() if p.is_dir())
    contents = {p: p.read_bytes() for p in env["pool"].glob("*/task.toml")}
    run_pool(env)
    assert sorted(p.name for p in env["pool"].iterdir() if p.is_dir()) == before
    assert all(p.read_bytes() == data for p, data in contents.items())


def test_pool_stage_skip_file_lines_are_task_id_form(env):
    run_pool(env)
    lines = [line for line in env["skip"].read_text().splitlines() if line.strip()]
    assert lines, "expected skip lines for every hit with an owner/repo/pr"
    for line in lines:
        assert SKIP_LINE_RE.match(line), f"not an owner__repo-N task-id line: {line}"
    assert "owner__exact-101" in lines


def test_pool_stage_is_idempotent(env):
    assert run_pool(env).returncode == 0
    manifest_after_first = env["manifest"].read_text()
    skip_after_first = env["skip"].read_text()
    second = run_pool(env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "no benchmark overlap" in second.stdout
    assert env["manifest"].read_text() == manifest_after_first
    assert env["skip"].read_text() == skip_after_first  # no duplicate skip lines


def test_pool_stage_missing_manifest_fails(env, tmp_path):
    empty_pool = tmp_path / "empty-cc"
    empty_pool.mkdir()
    result = run_stage(env, "--stage", "pool", "--pool-dir", str(empty_pool))
    assert result.returncode == 1
    assert "verifiable_tasks.txt" in result.stderr
    missing = run_stage(env, "--stage", "pool", "--pool-dir", str(tmp_path / "nope-cc"))
    assert missing.returncode == 1
    assert "--pool-dir" in missing.stderr


def test_minhash_off_changes_evidence_not_verdicts(env):
    run_pool(env, "--no-minhash")
    entries = {e["task_id"]: e for e in yaml.safe_load(env["quarantine"].read_text())["entries"]}
    assert "similarity_estimate_minhash" not in entries["owner__tok2-909"]
    assert set(entries) == {"owner__exact-101", "owner__paths-999", "owner__othert-777",
                            "owner__near-888", "owner__tok2-909"}
    assert env["manifest"].read_text().splitlines() == ["owner__clean-111"]


# --- pr_ids stage ----------------------------------------------------------------------


@pytest.fixture
def pr_ids(env):
    path = env["root"] / "py_pr_ids.txt"
    path.write_text(
        "owner/other:pr-1\n"
        "owner/exact:pr-101\n"
        "owner__exact-pr-102\n"
        "owner__clean-111\n"
        "\n"
        "owner/other:pr-1\n",  # duplicate collapses
        encoding="utf-8")
    return path


def test_pr_ids_stage_filters_benchmark_prs(env, pr_ids):
    result = run_pr_ids(env, pr_ids)
    assert result.returncode == 0, result.stdout + result.stderr
    survivors = pr_ids.read_text().splitlines()
    # Bare task-id input lines are canonicalized to the collector's format
    # (`legoflow-curator create --input-ids-file` expects owner/repo:pr-N).
    assert survivors == ["owner/other:pr-1", "owner/clean:pr-111"]


def test_pr_ids_stage_skip_lines_and_evidence(env, pr_ids):
    run_pr_ids(env, pr_ids)
    lines = [line for line in env["skip"].read_text().splitlines() if line.strip()]
    assert sorted(lines) == ["owner__exact-101", "owner__exact-102"]
    for line in lines:
        assert SKIP_LINE_RE.match(line)
    entries = {e["task_id"]: e for e in yaml.safe_load(env["quarantine"].read_text())["entries"]}
    assert entries["owner__exact-101"]["match_basis"] == ["repo+pr_number"]
    assert entries["owner__exact-101"]["matched_eval_task"] == "swe__exact-101"
    assert entries["owner__exact-101"]["pr_ref"] == "owner/exact:pr-101"


def test_pr_ids_stage_reports_no_hits_cleanly(env, pr_ids):
    (env["root"] / "empty_index.json").write_text(
        json.dumps({"dataset": "d", "entries": [
            {"task_id": "x", "repo": "someone/else", "pr_number": 9,
             "patch_file_paths": [], "test_file_paths": [], "norm_patch_sha256": "0" * 64}]}),
        encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--index", str(env["root"] / "empty_index.json"),
         "--stage", "pr_ids", "--pr-ids", str(pr_ids), "--lang", "py",
         "--quarantine", str(env["quarantine"]), "--skip-file", str(env["skip"])],
        capture_output=True, text=True)
    assert result.returncode == 0
    assert "no benchmark overlap" in result.stdout
    assert not env["skip"].exists()
    assert "owner/exact:pr-101" in pr_ids.read_text()  # nothing filtered


def test_pr_ids_stage_out_flag_writes_elsewhere(env, pr_ids):
    out = env["root"] / "filtered.txt"
    result = run_pr_ids(env, pr_ids, "--out", str(out))
    assert result.returncode == 0
    assert out.read_text().splitlines() == ["owner/other:pr-1", "owner/clean:pr-111"]
    assert "owner/exact:pr-101" in pr_ids.read_text()  # input untouched with --out
    # The rewrite is atomic (tmp+replace): no leftover .tmp beside either file.
    assert not out.with_name(out.name + ".tmp").exists()
    assert not pr_ids.with_name(pr_ids.name + ".tmp").exists()


def test_pr_ids_stage_flags_missing_requirements(env):
    result = run_stage(env, "--stage", "pr_ids")
    assert result.returncode == 1
    assert "--pr-ids" in result.stderr


# --- fail-closed on the index ----------------------------------------------------------


@pytest.mark.parametrize("index_content", [
    None,                       # file missing entirely
    "{not json",                # unparseable
    '{"dataset": "d"}',         # no entries key
    '{"dataset": "d", "entries": []}',  # empty entries
])
def test_fail_closed_on_bad_index(env, pr_ids, index_content):
    index = env["root"] / "bad.identity.json"
    if index_content is not None:
        index.write_text(index_content, encoding="utf-8")
    survivors_before = pr_ids.read_text()
    result = run_stage(env, "--stage", "pr_ids", "--pr-ids", str(pr_ids),
                       "--index", str(index))
    assert result.returncode == 1
    assert "fail-closed" in result.stderr
    assert pr_ids.read_text() == survivors_before       # nothing touched
    assert not env["skip"].exists() and not env["quarantine"].exists()


# --- normative fix 1: the skip file must actually suppress a PR ------------------------

CURATOR_CLI = (REPO_ROOT / "blocks" / "curator" / "repos" / "legoflow-curator" / "src"
               / "legoflow_curator" / "cli.py")


def pinned_cli_parsers():
    """Exec the pinned cli.py's own `_entry_key_norm` + `_parse_task_id_line` —
    the REAL suppression logic, not a hand-copied re-implementation, so a
    submodule repin that changes task-id parsing fails here instead of letting
    emitted skip lines silently stop suppressing anything."""
    if not CURATOR_CLI.is_file():
        pytest.skip("legoflow-curator submodule not checked out")
    src = CURATOR_CLI.read_text(encoding="utf-8")

    def block(name: str) -> str:
        match = re.search(rf"^    def {name}\(.*?(?=^    (?:def |@|[A-Za-z_#]))",
                          src, re.S | re.M)
        assert match, f"{name} not found in pinned cli.py — parser moved; update this extraction"
        return "\n".join(line[4:] if line.startswith("    ") else line
                         for line in match.group(0).splitlines())

    class BadParameter(Exception):
        pass

    namespace = {"typer": types.SimpleNamespace(BadParameter=BadParameter),
                 "Path": Path, "re": re}
    exec(block("_entry_key_norm"), namespace)
    exec(block("_parse_task_id_line"), namespace)
    return namespace["_parse_task_id_line"], namespace["_entry_key_norm"]


def test_skip_lines_actually_suppress_their_prs(env, pr_ids):
    """Normative fix 1 smoke test: every emitted skip line suppresses the PR it
    was quarantined for, under the pinned cli.py's own parsing rules."""
    parse, norm = pinned_cli_parsers()
    run_pr_ids(env, pr_ids)
    expected_keys = {f"{owner}/{repo}".lower() + f"#{pr}" for owner, repo, pr in
                     [("owner", "exact", 101), ("owner", "exact", 102),
                      ("owner", "other", 1), ("owner", "clean", 111)]}
    for line in env["skip"].read_text().splitlines():
        if not line.strip():
            continue
        assert norm(*parse(line, CURATOR_CLI, 1)) in expected_keys, line
    # And the format the plan sketches literally (owner__repo-pr-N) would NOT
    # suppress anything: cli.py happily parses it, but as repository "exact-pr".
    assert norm(*parse("owner__exact-pr-101", CURATOR_CLI, 1)) == "owner/exact-pr#101"
    assert "owner/exact-pr#101" not in expected_keys
    # Collector-format lines (owner/repo:pr-N) can never suppress: the real
    # parser raises BadParameter on anything without the `__` task-id shape.
    with pytest.raises(Exception):
        parse("owner/exact:pr-101", CURATOR_CLI, 1)


# --- normalization units ----------------------------------------------------------------


def test_normalized_patch_sha_is_hunk_order_stable():
    a = patch(["x.py"]) + patch(["y.py"])
    b = patch(["y.py"]) + patch(["x.py"])   # same hunks, different order
    assert DC.norm_patch_sha256(a) == DC.norm_patch_sha256(b)
    assert DC.norm_patch_sha256(a) != DC.norm_patch_sha256(patch(["z.py"]))
    assert DC.norm_patch_sha256(patch(["a.py"], "Fix THE handler")) == \
        DC.norm_patch_sha256(patch(["a.py"], "fix the handler"))


def test_norm_patch_sha_matches_the_evaluator_index_builder():
    """Edge 2 interop: identical digests on both ends of the identity index."""
    evaluator_script = REPO_ROOT / "blocks" / "evaluator" / "scripts" / "build_identity_index.py"
    if not evaluator_script.is_file():
        pytest.skip("evaluator build_identity_index.py not present")
    spec = importlib.util.spec_from_file_location("identity_index_builder", evaluator_script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    for text in (SHA_PATCH, TOK_PATCH, patch(["src/x.py", "src/y.py"]), "not a diff at all"):
        assert DC.norm_patch_sha256(text) == builder.norm_patch_sha256(text)


def test_patch_file_paths_and_path_normalization():
    text = ("diff --git a/src/A.py b/src/A.py\n"
            "--- a/src/A.py\n"
            "+++ b/src/A.py\n"
            "--- /dev/null\n"
            "+++ b/./other.py\n")
    assert DC.patch_file_paths(text) == {"src/a.py", "other.py"}
    assert DC.normalize_repo_path("./a/./Tests/X.py") == "tests/x.py"
    assert DC.normalize_repo_path("b/tests/x.py") == "tests/x.py"


def test_token_jaccard_threshold_boundary():
    tokens_a = DC.norm_tokens(TOK_PATCH)
    tokens_b = DC.norm_tokens(TOK_PATCH_NEAR)
    assert DC.jaccard(tokens_a, tokens_b) >= 0.85
    assert DC.jaccard(tokens_a, frozenset()) == 0.0
    assert DC.jaccard(frozenset(), frozenset()) == 0.0
    # The normative thresholds themselves (RSI_LOOP_PLAN.md §2.3) are pinned:
    # a constant edit cannot silently co-move with a test expectation.
    assert DC.PATCH_PATH_JACCARD_MIN == 0.6
    assert DC.NORM_TOKEN_JACCARD_MIN == 0.85


def token_patch(words: list[str], path: str = "pkg/mod.rs") -> str:
    """A patch whose normalized token set is exactly the fixed header/path
    tokens (12) plus `words` — gives exact control over token Jaccard."""
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1,2 +1,3 @@\n context line\n+ {' '.join(words)}\n")


TOK_HIT_WORDS = [f"w{i}" for i in range(5)]      # 12 + 5 = 17 tokens
TOK_MISS_WORDS = TOK_HIT_WORDS[:4]               # 12 + 4 = 16 tokens
TOK_INDEX_WORDS = TOK_HIT_WORDS + ["x0", "x1", "x2"]  # 12 + 8 = 20 tokens


def test_cascade_threshold_boundaries_and_near_misses(tmp_path):
    """Boundary-equality and near-miss pins for both cascade thresholds, run
    through the real pool stage: 3/5 == 0.6 quarantines at level (b) and 3/6
    == 0.5 survives; 17/20 == 0.85 quarantines at level (d) and 16/20 == 0.8
    survives. Kills a 0.6->0.5 / 0.85->0.8 loosening and a >= -> > flip."""
    pool = tmp_path / "py-cc"
    # Level (b): same repo as the index entry (owner/bnd), 3 of 5 patch paths shared.
    make_task(pool, "owner__bnd-701", "owner", "bnd",
              bug_patch=patch(["p/a.py", "p/b.py", "p/c.py", "p/d.py", "p/e.py"]),
              test_files=["check_one.sh"])
    # Near-miss: 3 of 6 shared -> 0.5 -> must SURVIVE.
    make_task(pool, "owner__bnd-702", "owner", "bnd",
              bug_patch=patch(["p/a.py", "p/b.py", "p/c.py", "p/d.py", "p/e.py", "p/f.py"]),
              test_files=["check_two.sh"])
    # Level (d): cross-repo token Jaccard exactly 0.85 (17 of 20 union tokens).
    make_task(pool, "owner__nearx-801", "owner", "nearx",
              bug_patch=token_patch(TOK_HIT_WORDS), test_files=["check_three.sh"])
    # Near-miss: 16/20 = 0.8 -> must SURVIVE.
    make_task(pool, "owner__nearm-802", "owner", "nearm",
              bug_patch=token_patch(TOK_MISS_WORDS), test_files=["check_four.sh"])
    (pool / "verifiable_tasks.txt").write_text(
        "owner__bnd-701\nowner__bnd-702\nowner__nearx-801\nowner__nearm-802\n",
        encoding="utf-8")
    index = tmp_path / "boundary.identity.json"
    index.write_text(json.dumps({
        "dataset": "swebench-verified",
        "entries": [
            {"task_id": "swe__bnd-601", "repo": "owner/bnd", "pr_number": 601,
             "patch_file_paths": ["p/a.py", "p/b.py", "p/c.py"],
             "test_file_paths": ["tests/test_bnd.py"], "norm_patch_sha256": "9" * 64},
            {"task_id": "swe__tok-805", "repo": "owner/tok", "pr_number": 805,
             "patch_file_paths": ["pkg/mod.rs"], "test_file_paths": ["tests/test_tok.py"],
             "norm_patch_sha256": "8" * 64, "patch_text": token_patch(TOK_INDEX_WORDS)},
        ]}, indent=2), encoding="utf-8")

    # Unit pin on the fixture itself: the (d) pair is exactly 0.85 as doubles.
    assert DC.jaccard(DC.norm_tokens(token_patch(TOK_HIT_WORDS)),
                      DC.norm_tokens(token_patch(TOK_INDEX_WORDS))) == pytest.approx(0.85)
    assert DC.jaccard(DC.norm_tokens(token_patch(TOK_MISS_WORDS)),
                      DC.norm_tokens(token_patch(TOK_INDEX_WORDS))) == pytest.approx(0.8)

    quarantine = tmp_path / "decontam" / "quarantine.yaml"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--index", str(index), "--stage", "pool",
         "--pool-dir", str(pool), "--quarantine", str(quarantine),
         "--skip-file", str(tmp_path / "decontam" / "skip.txt")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    entries = {e["task_id"]: e for e in yaml.safe_load(quarantine.read_text())["entries"]}
    # Boundary equality holds (>=, not >): 3/5 == 0.6 exactly as doubles.
    assert entries["owner__bnd-701"]["match_basis"] == ["repo+patch_path_jaccard"]
    assert entries["owner__bnd-701"]["similarity"] == pytest.approx(0.6)
    assert entries["owner__nearx-801"]["match_basis"] == ["norm_token_jaccard"]
    assert entries["owner__nearx-801"]["similarity"] == pytest.approx(0.85)
    # Near-misses survive the manifest rewrite.
    assert pool.joinpath("verifiable_tasks.txt").read_text().splitlines() \
        == ["owner__bnd-702", "owner__nearm-802"]


def test_index_builder_output_feeds_level_d_end_to_end(tmp_path):
    """Edge 2 end-to-end: an index built by the EVALUATOR's real
    build_identity_index.py (not a synthetic dict) carries patch_text, and a
    cross-repo near-duplicate pool task is quarantined by the token rule."""
    builder = REPO_ROOT / "blocks" / "evaluator" / "scripts" / "build_identity_index.py"
    if not builder.is_file():
        pytest.skip("evaluator build_identity_index.py not present")
    gold = tmp_path / "datasets" / "dset" / "gold__bench-901" / "tests"
    gold.mkdir(parents=True)
    (gold / "config.json").write_text(json.dumps({
        "repo": "owner/gold",
        "patch": token_patch(TOK_INDEX_WORDS),
        "test_patch": "",
        "FAIL_TO_PASS": [], "PASS_TO_PASS": []}), encoding="utf-8")
    index = tmp_path / "gold.identity.json"
    built = subprocess.run(
        [sys.executable, str(builder), "--dataset", "dset",
         "--dataset-dir", str(gold.parent.parent), "--out", str(index)],
        capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    entry = json.loads(index.read_text())["entries"][0]
    assert entry["task_id"] == "gold__bench-901"
    assert entry["patch_text"] == token_patch(TOK_INDEX_WORDS)  # producer writes it

    pool = tmp_path / "py-cc"
    make_task(pool, "owner__nearx-801", "owner", "nearx",
              bug_patch=token_patch(TOK_HIT_WORDS), test_files=["check_three.sh"])
    (pool / "verifiable_tasks.txt").write_text("owner__nearx-801\n", encoding="utf-8")
    quarantine = tmp_path / "decontam" / "quarantine.yaml"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--index", str(index), "--stage", "pool",
         "--pool-dir", str(pool), "--quarantine", str(quarantine),
         "--skip-file", str(tmp_path / "decontam" / "skip.txt")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    entries = yaml.safe_load(quarantine.read_text())["entries"]
    assert entries[0]["match_basis"] == ["norm_token_jaccard"]
    assert entries[0]["similarity"] == pytest.approx(0.85)
    assert pool.joinpath("verifiable_tasks.txt").read_text().strip() == ""


def test_pool_stage_warns_when_no_entry_carries_patch_text(env, tmp_path):
    """Level (d) is only as alive as the index's patch_text: with none present
    the pool stage still runs (fail-closed on the other levels) but says so."""
    index = tmp_path / "no_patch_text.json"
    index.write_text(json.dumps({"dataset": "d", "entries": [
        {"task_id": "swe__exact-101", "repo": "owner/exact", "pr_number": 101,
         "patch_file_paths": ["src/exact.py"], "test_file_paths": ["tests/test_exact.py"],
         "norm_patch_sha256": "0" * 64}]}), encoding="utf-8")
    result = run_stage(env, "--index", str(index), "--stage", "pool",
                       "--pool-dir", str(env["pool"]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no patch_text on any entry" in result.stderr
    assert "token-Jaccard/minhash similarity is disabled" in result.stderr


def test_pool_rewrite_unions_concurrent_create_appends(env, monkeypatch):
    """A create append landing after run_pool's first read survives the
    rewrite: rewrite_manifest takes the same `.verifiable_tasks.lock` create
    appends under and unions ids this run never classified."""
    original = DC.pool_task_identity

    def appending_identity(task_dir, task_id, with_minhash=True):
        if task_id == "owner__exact-101":  # first matched task -> mid-loop append
            with env["manifest"].open("a", encoding="utf-8") as handle:
                handle.write("owner__late-555\n")
        return original(task_dir, task_id, with_minhash)

    monkeypatch.setattr(DC, "pool_task_identity", appending_identity)
    args = argparse.Namespace(pool_dir=str(env["pool"]), verifiable=None,
                              quarantine=str(env["quarantine"]), skip_file=str(env["skip"]),
                              index=str(env["index"]), minhash=True)
    assert DC.run_pool(args, DC.IdentityIndex(DC.load_index(env["index"]))) == 0
    assert env["manifest"].read_text().splitlines() \
        == ["owner__clean-111", "owner__late-555"]
    assert (env["pool"] / ".verifiable_tasks.lock").exists()  # create's own lock file


def test_quarantine_entries_keep_their_first_stage_and_language(env, pr_ids):
    """Quarantine provenance is stamped at first insert and preserved on merge
    (only match_basis/similarity union across runs)."""
    assert run_pr_ids(env, pr_ids).returncode == 0   # stage pr_ids, language py
    assert run_pool(env).returncode == 0             # stage pool, language py-cc
    entries = {e["task_id"]: e for e in yaml.safe_load(env["quarantine"].read_text())["entries"]}
    exact = entries["owner__exact-101"]
    assert exact["stage"] == "pr_ids"                # first insert wins
    assert exact["language"] == "py"
    assert exact["index"] == str(env["index"])
    assert exact["match_basis"] == ["repo+pr_number"]  # union added nothing new
    pool_only = entries["owner__paths-999"]
    assert pool_only["stage"] == "pool"
    assert pool_only["language"] == "py-cc"


def test_test_path_collection_matches_segment_and_stem():
    assert DC.is_test_path("tests/e2e/check.py")
    assert DC.is_test_path("__tests__/helper.js")
    assert DC.is_test_path("src/test_utils.py")
    assert DC.is_test_path("src/utils_test.py")
    assert not DC.is_test_path("testing/foo.py")   # substring, not a segment/stem
    assert not DC.is_test_path("contest/x.py")
    assert not DC.is_test_path("src/main.py")


def test_stale_index_warns_against_newer_gold_dataset(env, pr_ids, tmp_path):
    layout = tmp_path / "artifacts"
    index_dir = layout / "bench_identity"
    index_dir.mkdir(parents=True)
    gold_task = layout / "datasets" / "dset" / "task-1"
    gold_task.mkdir(parents=True)
    (gold_task / "task.toml").write_text('version = "1.0"\n', encoding="utf-8")
    index = index_dir / "dset.identity.json"
    index.write_text(json.dumps({
        "dataset": "dset", "generated_at": "2020-01-01T00:00:00Z",
        "entries": [{"task_id": "x", "repo": "someone/else", "pr_number": 9,
                     "patch_file_paths": [], "test_file_paths": [],
                     "norm_patch_sha256": "0" * 64}]}), encoding="utf-8")
    result = run_stage(env, "--index", str(index), "--stage", "pr_ids",
                       "--pr-ids", str(pr_ids))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "predates the newest file" in result.stderr
    # A fresh index (generated_at after the gold mtimes) stays quiet.
    fresh = index_dir / "fresh.identity.json"
    fresh.write_text(json.dumps({
        "dataset": "fresh", "generated_at": "2099-01-01T00:00:00Z",
        "entries": [{"task_id": "x", "repo": "someone/else", "pr_number": 9,
                     "patch_file_paths": [], "test_file_paths": [],
                     "norm_patch_sha256": "0" * 64}]}), encoding="utf-8")
    quiet = run_stage(env, "--index", str(fresh), "--stage", "pr_ids",
                      "--pr-ids", str(pr_ids))
    assert quiet.returncode == 0
    assert "predates the newest file" not in quiet.stderr


def test_pr_line_parsing_tolerates_all_three_forms():
    assert DC.parse_pr_line("owner/repo:pr-123") == ("owner", "repo", 123)
    assert DC.parse_pr_line("owner__repo-pr-123") == ("owner", "repo", 123)
    assert DC.parse_pr_line("owner__repo-123") == ("owner", "repo", 123)
    assert DC.parse_pr_line("not a pr line") is None
    assert DC.task_id_line("Owner", "Repo", 7) == "owner__repo-7"


def test_minhash_estimate_is_deterministic_and_close():
    tokens_a = DC.norm_tokens(TOK_PATCH)
    tokens_b = DC.norm_tokens(TOK_PATCH_NEAR)
    sig_a, sig_b = DC.minhash_signature(tokens_a), DC.minhash_signature(tokens_b)
    assert sig_a == DC.minhash_signature(tokens_a)
    estimate = DC.minhash_estimate(sig_a, sig_b)
    assert 0.0 <= estimate <= 1.0
    assert estimate == pytest.approx(DC.jaccard(tokens_a, tokens_b), abs=0.35)
    assert DC.minhash_estimate(sig_a, sig_a) == pytest.approx(1.0)


def test_repo_and_pr_fall_back_to_the_task_id(env):
    task = make_task(env["root"] / "bare", "pallets__flask-5231", "", "",
                     bug_patch=patch(["src/flask.py"]))
    identity = DC.pool_task_identity(task, "pallets__flask-5231")
    assert identity["repo"] == "pallets/flask"
    assert identity["pr_number"] == 5231
