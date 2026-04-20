"""Difficulty scoring for SWE-gen tasks.

Scores tasks on a 1-10 scale using 5 static dimensions extracted from
fix.patch, tests/, and instruction.md. Zero API calls required.

Dimensions (weighted):
  1. patch_scope (30%): files modified + lines changed
  2. logic_complexity (25%): new functions/classes/methods in patch
  3. test_complexity (20%): test file count + test lines
  4. context_breadth (15%): directory spread of changes
  5. instruction_complexity (10%): instruction length

Score mapping: 1-3 = easy, 4-7 = medium, 8-10 = hard
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class DimensionScore(BaseModel):
    name: str
    raw: float = 0.0
    score: int = 1  # 1-5
    detail: str = ""


class TaskScore(BaseModel):
    task_id: str
    score: float = 0.0  # 1-10
    label: str = "medium"  # easy/medium/hard
    dimensions: list[DimensionScore] = Field(default_factory=list)
    patch_files: int = 0
    patch_lines: int = 0
    test_files: int = 0
    test_lines: int = 0
    instruction_chars: int = 0
    directories: int = 0


# Weights for each dimension
WEIGHTS = {
    "patch_scope": 0.30,
    "logic_complexity": 0.25,
    "test_complexity": 0.20,
    "context_breadth": 0.15,
    "instruction_complexity": 0.10,
}

# Patterns for detecting new function/class/method definitions across languages
NEW_DEFINITION_PATTERNS = [
    r"^\+\s*def\s+\w+",                    # Python
    r"^\+\s*(public|private|protected)?\s*(static\s+)?\w+\s+\w+\s*\(",  # Java/C/C++
    r"^\+\s*func\s+\w+",                   # Go
    r"^\+\s*(pub\s+)?fn\s+\w+",            # Rust
    r"^\+\s*(export\s+)?(async\s+)?function\s+\w+",  # JS/TS
    r"^\+\s*(export\s+)?class\s+\w+",      # Python/JS/TS/Java
    r"^\+\s*struct\s+\w+",                 # Rust/Go/C
    r"^\+\s*impl\s+",                      # Rust
    r"^\+\s*interface\s+\w+",              # Go/Java/TS
    r"^\+\s*(const|let|var)\s+\w+\s*=\s*(async\s+)?\(",  # JS/TS arrow functions
]


def _parse_patch(patch_path: Path) -> dict:
    """Parse a unified diff patch file for metrics."""
    if not patch_path.exists():
        return {"files": 0, "additions": 0, "deletions": 0, "dirs": set(), "new_defs": 0}

    text = patch_path.read_text(errors="replace")
    files = set()
    dirs = set()
    additions = 0
    deletions = 0
    new_defs = 0

    for line in text.splitlines():
        if line.startswith("diff --git"):
            # Extract file path: diff --git a/path b/path
            parts = line.split()
            if len(parts) >= 4:
                fpath = parts[3].lstrip("b/")
                files.add(fpath)
                parent = str(Path(fpath).parent)
                if parent != ".":
                    dirs.add(parent)
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
            for pattern in NEW_DEFINITION_PATTERNS:
                if re.match(pattern, line):
                    new_defs += 1
                    break
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    return {
        "files": len(files),
        "additions": additions,
        "deletions": deletions,
        "dirs": dirs,
        "new_defs": new_defs,
    }


def _count_test_files(task_dir: Path) -> tuple[int, int]:
    """Count test files and total test lines in tests/ directory."""
    tests_dir = task_dir / "tests"
    if not tests_dir.exists():
        return 0, 0

    test_files = 0
    test_lines = 0
    for f in tests_dir.rglob("*"):
        if f.is_file() and f.name != "test.sh":
            test_files += 1
            try:
                test_lines += len(f.read_text(errors="replace").splitlines())
            except Exception:
                pass
    return test_files, test_lines


def _score_patch_scope(n_files: int, n_lines: int) -> DimensionScore:
    """Score based on number of files and lines changed."""
    total_lines = n_lines
    if n_files <= 1 and total_lines < 10:
        s = 1
    elif n_files <= 1 and total_lines < 30:
        s = 2
    elif n_files <= 3 and total_lines < 50:
        s = 3
    elif n_files <= 5 and total_lines < 100:
        s = 4
    else:
        s = 5
    return DimensionScore(
        name="patch_scope", score=s,
        detail=f"{n_files} files, {total_lines} lines",
    )


def _score_logic_complexity(new_defs: int, additions: int, deletions: int) -> DimensionScore:
    """Score based on new definitions and change ratio."""
    # More additions than deletions suggests new logic, not just cleanup
    net_additions = max(0, additions - deletions)
    if new_defs == 0 and net_additions < 5:
        s = 1
    elif new_defs == 0 and net_additions < 15:
        s = 2
    elif new_defs <= 2:
        s = 3
    elif new_defs <= 5:
        s = 4
    else:
        s = 5
    return DimensionScore(
        name="logic_complexity", score=s,
        detail=f"{new_defs} new defs, +{additions}/-{deletions}",
    )


def _score_test_complexity(n_test_files: int, n_test_lines: int) -> DimensionScore:
    """Score based on test file count and lines."""
    if n_test_files <= 1 and n_test_lines < 20:
        s = 1
    elif n_test_files <= 1 and n_test_lines < 60:
        s = 2
    elif n_test_files <= 3 and n_test_lines < 100:
        s = 3
    elif n_test_files <= 3:
        s = 4
    else:
        s = 5
    return DimensionScore(
        name="test_complexity", score=s,
        detail=f"{n_test_files} test files, {n_test_lines} lines",
    )


def _score_context_breadth(n_dirs: int) -> DimensionScore:
    """Score based on directory spread of changes."""
    if n_dirs <= 1:
        s = 1
    elif n_dirs == 2:
        s = 2
    elif n_dirs == 3:
        s = 3
    elif n_dirs <= 5:
        s = 4
    else:
        s = 5
    return DimensionScore(
        name="context_breadth", score=s,
        detail=f"{n_dirs} directories",
    )


def _score_instruction_complexity(n_chars: int) -> DimensionScore:
    """Score based on instruction length."""
    if n_chars < 150:
        s = 1
    elif n_chars < 300:
        s = 2
    elif n_chars < 500:
        s = 3
    elif n_chars < 800:
        s = 4
    else:
        s = 5
    return DimensionScore(
        name="instruction_complexity", score=s,
        detail=f"{n_chars} chars",
    )


def score_task(task_dir: Path, task_id: Optional[str] = None) -> TaskScore:
    """Score a single task directory.

    Args:
        task_dir: Path to task directory containing solution/fix.patch, tests/, instruction.md
        task_id: Optional task ID (defaults to directory name)

    Returns:
        TaskScore with 1-10 score and easy/medium/hard label
    """
    tid = task_id or task_dir.name

    # Parse patch
    patch_path = task_dir / "solution" / "fix.patch"
    patch_info = _parse_patch(patch_path)

    # Count tests
    test_files, test_lines = _count_test_files(task_dir)

    # Read instruction
    instr_path = task_dir / "instruction.md"
    instr_chars = 0
    if instr_path.exists():
        try:
            instr_chars = len(instr_path.read_text(errors="replace"))
        except Exception:
            pass

    # Score each dimension
    dims = [
        _score_patch_scope(patch_info["files"], patch_info["additions"] + patch_info["deletions"]),
        _score_logic_complexity(patch_info["new_defs"], patch_info["additions"], patch_info["deletions"]),
        _score_test_complexity(test_files, test_lines),
        _score_context_breadth(len(patch_info["dirs"])),
        _score_instruction_complexity(instr_chars),
    ]

    # Weighted sum: raw 1-5 → mapped to 1-10
    weight_keys = list(WEIGHTS.keys())
    weighted_sum = sum(dims[i].score * WEIGHTS[weight_keys[i]] for i in range(len(dims)))
    # weighted_sum is 1.0-5.0, map to 1-10
    final_score = round((weighted_sum - 1.0) * (9.0 / 4.0) + 1.0, 1)
    final_score = max(1.0, min(10.0, final_score))

    # Label
    if final_score <= 3.0:
        label = "easy"
    elif final_score <= 7.0:
        label = "medium"
    else:
        label = "hard"

    return TaskScore(
        task_id=tid,
        score=final_score,
        label=label,
        dimensions=dims,
        patch_files=patch_info["files"],
        patch_lines=patch_info["additions"] + patch_info["deletions"],
        test_files=test_files,
        test_lines=test_lines,
        instruction_chars=instr_chars,
        directories=len(patch_info["dirs"]),
    )


def score_tasks_batch(
    task_dirs: list[Path],
    task_ids: Optional[list[str]] = None,
) -> list[TaskScore]:
    """Score multiple tasks.

    Args:
        task_dirs: List of task directory paths
        task_ids: Optional list of task IDs (defaults to dir names)

    Returns:
        List of TaskScore objects
    """
    results = []
    for i, td in enumerate(task_dirs):
        tid = task_ids[i] if task_ids and i < len(task_ids) else None
        try:
            results.append(score_task(td, tid))
        except Exception as e:
            print(f"  Warning: failed to score {td.name}: {e}")
    return results


def update_task_toml_difficulty(task_dir: Path, score: TaskScore) -> None:
    """Update the difficulty field in task.toml with the computed score.

    Also adds a [scoring] section with the numeric score.
    """
    toml_path = task_dir / "task.toml"
    if not toml_path.exists():
        return

    content = toml_path.read_text()

    # Update difficulty field
    import re as _re
    content = _re.sub(
        r'difficulty\s*=\s*"[^"]*"',
        f'difficulty = "{score.label}"',
        content,
    )

    # Add or update scoring section
    scoring_section = f"\n[scoring]\ndifficulty_score = {score.score}\ndifficulty_label = \"{score.label}\"\n"
    if "[scoring]" in content:
        content = _re.sub(
            r'\[scoring\].*?(?=\n\[|\Z)',
            scoring_section.strip() + "\n",
            content,
            flags=_re.DOTALL,
        )
    else:
        content = content.rstrip() + "\n" + scoring_section

    toml_path.write_text(content)
