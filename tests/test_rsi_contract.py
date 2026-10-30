"""RSI-loop contract-surface checks for LegoFlow.

Validates the human/agent contract layer added for the RSI loop
(RSI_LOOP_PLAN.md / RSI_RUNBOOK.md):

- loop-memory artifact paths are un-ignored (git-tracked memory survives
  ``clean.sh --all``) while ordinary run artifacts stay ignored;
- each pipeline block's CLAUDE.md documents its RSI loop steps;
- the four canonical ``:run``/``create-tasks`` SKILL.md files carry the new
  loop steps (repeats + weakness/identity/audit-slice, targeting gate +
  decontam, scaffold disagreement, round receipt);
- RSI_RUNBOOK.md exists with the flag table, the delimited attach section,
  and resolvable links.

Read-only: uses ``git check-ignore`` / ``git ls-files`` in a subprocess; no
network, no Docker, no script execution.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

BLOCK_SKILLS = {
    "curator": REPO_ROOT
    / "blocks/curator/.claude/plugins/curator-plugin/skills/create-tasks/SKILL.md",
    "evaluator": REPO_ROOT
    / "blocks/evaluator/.claude/plugins/evaluator-plugin/skills/run/SKILL.md",
    "tracer": REPO_ROOT
    / "blocks/tracer/.claude/plugins/tracer-plugin/skills/run/SKILL.md",
    "trainer": REPO_ROOT
    / "blocks/trainer/.claude/plugins/trainer-plugin/skills/run/SKILL.md",
}

BLOCK_DOCS = {
    "curator": REPO_ROOT / "blocks/curator/CLAUDE.md",
    "evaluator": REPO_ROOT / "blocks/evaluator/CLAUDE.md",
    "tracer": REPO_ROOT / "blocks/tracer/CLAUDE.md",
    "trainer": REPO_ROOT / "blocks/trainer/CLAUDE.md",
}

# Loop-memory files that must be committable so scripts/clean.sh --all
# (which keeps only git-tracked files) never wipes loop history.
LOOP_MEMORY_TRACKABLE = [
    "blocks/curator/artifacts/targeting/hypothesis_ledger.yaml",
    "blocks/curator/artifacts/targeting/directives_round_001.yaml",
    "blocks/curator/artifacts/decontam/quarantine.yaml",
    "blocks/curator/artifacts/attach/manifest.json",
    "blocks/evaluator/artifacts/loop/ledger.yaml",
    "blocks/evaluator/artifacts/weakness/history/round_001.json",
    "blocks/evaluator/artifacts/reserve/audit_slice.meta.json",
    "blocks/evaluator/artifacts/reserve/audit_slice.txt",
    "blocks/evaluator/artifacts/response_matrix.jsonl",
]

# Ordinary run output that must stay ignored.
STILL_IGNORED = [
    "blocks/curator/artifacts/swe_tasks/py-cc/some-task",
    "blocks/curator/artifacts/merged_swe_tasks/some-task",
    "blocks/evaluator/artifacts/weakness/weakness_report.json",
    "blocks/evaluator/artifacts/reserve/other_file.json",
    "blocks/evaluator/artifacts/jobs/job-1/result.json",
    "blocks/evaluator/artifacts/datasets/swebench-verified/task-1",
    "blocks/tracer/artifacts/jobs/job-1/task-1/agent/traj.jsonl",
]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _is_ignored(rel_path: str) -> bool:
    return _git("check-ignore", "-q", rel_path).returncode == 0


# --- gitignore semantics ------------------------------------------------------


@pytest.mark.parametrize("rel_path", LOOP_MEMORY_TRACKABLE)
def test_loop_memory_not_ignored(rel_path: str):
    assert not _is_ignored(rel_path), (
        f"{rel_path} is git-ignored: clean.sh --all would destroy this loop-memory "
        "file. Add/repair the negation pattern in the owning .gitignore."
    )


@pytest.mark.parametrize("rel_path", STILL_IGNORED)
def test_run_artifacts_still_ignored(rel_path: str):
    assert _is_ignored(rel_path), f"{rel_path} leaked out of the artifacts ignores"


def test_tracer_excluded_tasks_tracked():
    out = _git("ls-files", "--", "blocks/tracer/excluded_tasks.txt")
    assert out.stdout.strip() == "blocks/tracer/excluded_tasks.txt", (
        "blocks/tracer/excluded_tasks.txt must stay git-tracked (loop retirement memory)"
    )


def test_evaluator_gitignore_has_no_bare_artifacts_dir_rule():
    """A bare `artifacts/` line excludes the parent dir itself and silently
    defeats every child negation (gitignore(5)); guard the artifacts/* form."""
    lines = [
        ln.strip()
        for ln in (REPO_ROOT / "blocks/evaluator/.gitignore").read_text().splitlines()
    ]
    assert "artifacts/" not in lines, (
        "blocks/evaluator/.gitignore must use `artifacts/*` + negations, not a bare "
        "`artifacts/` directory rule"
    )


# --- block CLAUDE.md contract surfaces ----------------------------------------


@pytest.mark.parametrize("name", sorted(BLOCK_DOCS))
def test_block_claude_documents_rsi_loop(name: str):
    text = BLOCK_DOCS[name].read_text()
    assert "RSI Loop" in text or "RSI loop" in text, (
        f"{BLOCK_DOCS[name]} lacks its RSI loop section"
    )
    assert "RSI_LOOP_PLAN.md" in text, (
        f"{BLOCK_DOCS[name]} should link the authoritative RSI_LOOP_PLAN.md"
    )
    assert "RSI_RUNBOOK.md" in text, (
        f"{BLOCK_DOCS[name]} should link the round procedure in RSI_RUNBOOK.md"
    )


@pytest.mark.parametrize(
    "name, expected_fragments",
    [
        ("curator", ["apply_targeting.py", "decontam_check.py", "owner__repo-<PR>"]),
        ("evaluator", ["harbor_job.repeats", "build_weakness_report.py", "audit_slice"]),
        ("tracer", ["scaffold_disagreement.py", "excluded_tasks.txt"]),
        ("trainer", ["round receipt", "mix_manifest_sha"]),
    ],
)
def test_block_claude_rsi_mechanisms(name: str, expected_fragments: list[str]):
    text = BLOCK_DOCS[name].read_text()
    for fragment in expected_fragments:
        assert fragment in text, f"{BLOCK_DOCS[name]} missing '{fragment}'"


# --- canonical SKILL.md workflow steps ----------------------------------------


@pytest.mark.parametrize("name", sorted(BLOCK_SKILLS))
def test_skill_file_exists(name: str):
    assert BLOCK_SKILLS[name].is_file(), f"missing canonical skill {BLOCK_SKILLS[name]}"


@pytest.mark.parametrize(
    "name, expected_fragments",
    [
        (
            "curator",
            [
                "apply_targeting.py",
                "decontam_check.py",
                "semantic-audit",
                "owner__repo-<PR>",
                "hypothesis_ledger.yaml",
            ],
        ),
        (
            "evaluator",
            [
                "harbor_job.repeats",
                "build_weakness_report.py",
                "build_identity_index.py",
                "audit_slice.txt",
                "--placebo",
                "task_name",
            ],
        ),
        (
            "tracer",
            [
                "scaffold_disagreement.py",
                "advice only",
                "excluded_tasks.txt",
            ],
        ),
        ("trainer", ["round receipt", "mix_manifest_sha", "from_trainer_checkpoint"]),
    ],
)
def test_skill_carries_rsi_steps(name: str, expected_fragments: list[str]):
    text = BLOCK_SKILLS[name].read_text()
    for fragment in expected_fragments:
        assert fragment in text, f"{BLOCK_SKILLS[name]} missing '{fragment}'"


def test_evaluator_skill_repeats_conventions():
    text = BLOCK_SKILLS["evaluator"].read_text()
    # Repeats multiply cost; the skill must say so at the confirm step and
    # enforce one proxy per repeat (trajectory root is fixed at fork time).
    assert "per repeat" in text
    assert "directives_blocked" in text


def test_curator_skill_targeting_gate_needs_confirmation():
    text = BLOCK_SKILLS["curator"].read_text()
    m = re.search(r"## Step 2b.*?(?=## Step)", text, flags=re.S)
    assert m, "curator create-tasks skill lacks the Step 2b targeting gate"
    gate = m.group(0)
    assert "wait for explicit user confirmation" in gate, (
        "the targeting gate must mirror the terminal-curator audit pattern: "
        "agent audit + explicit user confirmation before any config edit"
    )
    assert "next run" in gate or "next-run" in gate, (
        "directives apply to the NEXT run's config (one-shot semantics)"
    )


# --- evaluator serve script (Edge 3) -------------------------------------------


def test_serve_script_prefers_trainer_checkpoint():
    """Edge 3 (RSI_LOOP_PLAN.md §5.2/§10.2) is wired in config and claimed by
    three docs; its runtime consumer must actually read the leaf, so the
    doc/script drift cannot silently regress."""
    text = (REPO_ROOT / "blocks/evaluator/scripts/serve_local_model.sh").read_text()
    assert "from_trainer_checkpoint" in text


# --- RSI_RUNBOOK.md -----------------------------------------------------------


def test_runbook_exists_with_flag_table():
    runbook = REPO_ROOT / "RSI_RUNBOOK.md"
    assert runbook.is_file(), "missing RSI_RUNBOOK.md"
    text = runbook.read_text()
    for flag in (
        "LEGOFLOW_RSI_ATTACH",
        "harbor_job.repeats",
        "targeting.enabled",
        "decontam.enabled",
        "--placebo",
        "OPENAI_BASE_URL_HUAWEI",
        "OPENAI_API_KEY_HUAWEI",
    ):
        assert flag in text, f"RSI_RUNBOOK.md missing flag/knob '{flag}'"
    # Attach stays opt-in: the default must be stated as off.
    assert re.search(r"LEGOFLOW_RSI_ATTACH.*default", text) or "default off" in text


def test_runbook_attach_section_is_delimited():
    text = (REPO_ROOT / "RSI_RUNBOOK.md").read_text()
    begin = text.find("BEGIN ATTACH-MODE SECTION")
    end = text.find("END ATTACH-MODE SECTION")
    assert begin != -1 and end != -1 and begin < end, (
        "RSI_RUNBOOK.md needs the delimited attach section so concurrent appends merge"
    )


def test_runbook_links_resolve():
    runbook = REPO_ROOT / "RSI_RUNBOOK.md"
    for target in re.findall(r"\]\(([^)#]+?\.md)\)", runbook.read_text()):
        assert (runbook.parent / target).exists(), f"broken link in runbook: {target}"


@pytest.mark.parametrize("name", sorted(BLOCK_DOCS))
def test_block_claude_rsi_links_resolve(name: str):
    doc = BLOCK_DOCS[name]
    for target in re.findall(r"\]\(([^)#]+?\.md)\)", doc.read_text()):
        assert (doc.parent / target).exists(), f"broken link in {doc}: {target}"
