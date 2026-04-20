from __future__ import annotations

from enum import Enum
from pathlib import Path
import re


class TaskCompletionState(str, Enum):
    MISSING = "missing"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    OTHER = "other"


_COMMENT_TODO_RE = re.compile(r"(?im)^\s*#\s*todo(?:\b|:)")
_UNFILLED_TEST_COMMAND_RE = re.compile(
    r'(?im)^\s*echo\s+"error:\s*test command not filled in!'
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def dockerfile_has_template_placeholders(path: Path) -> bool:
    if not path.exists():
        return False
    return bool(_COMMENT_TODO_RE.search(_read_text(path)))


def test_sh_has_template_placeholders(path: Path) -> bool:
    if not path.exists():
        return False
    content = _read_text(path)
    return bool(_COMMENT_TODO_RE.search(content) or _UNFILLED_TEST_COMMAND_RE.search(content))


def get_incomplete_task_reasons(task_dir: Path) -> list[str]:
    reasons: list[str] = []
    dockerfile = task_dir / "environment" / "Dockerfile"
    test_sh = task_dir / "tests" / "test.sh"
    fix_patch = task_dir / "solution" / "fix.patch"

    if not task_dir.exists():
        return ["task directory missing"]

    if not dockerfile.exists():
        reasons.append("missing environment/Dockerfile")
    elif dockerfile_has_template_placeholders(dockerfile):
        reasons.append("environment/Dockerfile still has swegen template placeholders")

    if not test_sh.exists():
        reasons.append("missing tests/test.sh")
    elif test_sh_has_template_placeholders(test_sh):
        reasons.append("tests/test.sh still has swegen template placeholders")

    if not fix_patch.exists():
        reasons.append("missing solution/fix.patch")

    return reasons


def _looks_like_swegen_task_dir(task_dir: Path) -> bool:
    markers = (
        task_dir / "instruction.md",
        task_dir / "task.toml",
        task_dir / "environment",
        task_dir / "tests",
        task_dir / "solution",
        task_dir / "environment" / "bug.patch",
        task_dir / "solution" / "fix.patch",
    )
    return any(marker.exists() for marker in markers)


def classify_task_dir_state(task_dir: Path) -> TaskCompletionState:
    if not task_dir.exists():
        return TaskCompletionState.MISSING

    if not get_incomplete_task_reasons(task_dir):
        return TaskCompletionState.COMPLETE

    if _looks_like_swegen_task_dir(task_dir):
        return TaskCompletionState.INCOMPLETE

    return TaskCompletionState.OTHER


def task_has_completed_files(task_dir: Path) -> bool:
    return classify_task_dir_state(task_dir) == TaskCompletionState.COMPLETE


def task_is_retryable_incomplete(task_dir: Path) -> bool:
    return classify_task_dir_state(task_dir) == TaskCompletionState.INCOMPLETE
