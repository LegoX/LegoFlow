#!/usr/bin/env python3
"""Difficulty scoring + 4-tag metadata tagging for dashboard datasets.

Ported from harbor `scripts/task_analysis/tag_task_metadata.py`, adapted to run
over the dashboard's unified JSONL datasets (datasets/<id>/tasks.jsonl) instead
of on-disk Harbor task directories.

Methodology (identical to harbor):
  * Difficulty: 5-dimension weighted, log-scaled model
      patch_scope / logic_complexity / context_breadth /
      test_complexity / instruction_complexity  ->  1.0-10.0, easy/medium/hard
  * Tags: exactly 4 in order [language, area, topic, bug_class] via an LLM,
      where area ∈ {backend, frontend, fullstack, cli, library, framework}.

Each record in tasks.jsonl provides: instance_id, problem_statement, patch, and
optionally test_patch. Output is written to datasets/<id>/tags.jsonl, one JSON
object per task; the run is resumable (already-tagged instance_ids are skipped).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, TypeVar

import requests

DASHBOARD_ROOT = Path(__file__).parent
DATASETS_DIR = DASHBOARD_ROOT / "datasets"

DATASET_IDS = ["self_made", "scale_swe", "swe_rebench_filtered", "swe_rebench_test"]

AREA_TAGS = {"backend", "frontend", "fullstack", "cli", "library", "framework"}
TAG_COUNT = 4

MAX_INSTRUCTION_CHARS = 4000
MAX_PATCH_CHARS = 12000
MAX_TEST_CHARS = 8000
TAG_COMPLETION_TOKENS = 1024
T = TypeVar("T")

WEIGHTS = {
    "patch_scope": 0.30,
    "logic_complexity": 0.25,
    "context_breadth": 0.20,
    "test_complexity": 0.15,
    "instruction_complexity": 0.10,
}

TAG_SYSTEM_PROMPT = """You generate SWE task tags.

IMPORTANT: You MUST respond with a valid JSON object ONLY. No markdown, no explanation outside JSON.
Do not think step by step. Output the JSON object immediately.

TAGS:
Generate exactly 4 tags in this order:
1. Primary programming language, e.g. "python", "javascript", "typescript", "go", "rust", "java", "ruby", "cpp"
2. Tier/area. Choose ONE from: "backend", "frontend", "fullstack", "cli", "library", "framework"
3. Framework/library name, e.g. "fastapi", "django", "react", "nextjs", "axios", "express"; OR a specific topic, e.g. "http", "async", "testing"
4. Bug class: a domain-independent short label for the defect mechanism, e.g. "missing-fallback", "incomplete-validation", "wrong-default", "type-handling-inconsistency", "missing-metadata-propagation"

The bug class should describe the logical failure mode, not the affected framework.

Examples:
- FastAPI backend bug caused by missing default fallback: ["python", "backend", "fastapi", "missing-fallback"]
- Next.js UI bug caused by incorrect state propagation: ["typescript", "frontend", "nextjs", "missing-state-propagation"]
- Python CLI bug caused by incomplete option parsing: ["python", "cli", "argparse", "incomplete-parsing"]

Return format:
{"tags": ["language", "tier-or-area", "framework-or-topic", "bug-class"]}
"""

@dataclass(frozen=True)
class TaggerConfig:
    model: str
    api_key: str
    base_url: str
    timeout_sec: float = 120.0
    retries: int = 3
    retry_delay_sec: float = 2.0


# ---------------------------------------------------------------------------
# Difficulty scoring (ported verbatim from harbor, operating on patch/test text)
# ---------------------------------------------------------------------------

def _parse_patch_text(patch_text: str) -> dict[str, Any]:
    files = 0
    hunks = 0
    additions = 0
    deletions = 0
    dirs: set[str] = set()
    new_defs = 0
    control_flow = 0
    control_flow_re = re.compile(
        r"\b(if|elif|else|for|while|try|except|with|match|case|switch|catch)\b"
    )
    new_def_re = re.compile(r"^\+\s*(def|async\s+def|class|function|const|let|var)\b")

    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            files += 1
            parts = line.split()
            if len(parts) >= 4:
                current_file = parts[3].removeprefix("b/")
                parent = str(Path(current_file).parent)
                if parent and parent != ".":
                    dirs.add(parent)
            continue
        if line.startswith("@@"):
            hunks += 1
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
            if new_def_re.search(line):
                new_defs += 1
            if control_flow_re.search(line):
                control_flow += 1
        elif line.startswith("-"):
            deletions += 1

    return {
        "files": files,
        "hunks": hunks,
        "additions": additions,
        "deletions": deletions,
        "dirs": dirs,
        "new_defs": new_defs,
        "control_flow": control_flow,
    }


def _scale(raw: float, *, easy: float, hard: float) -> float:
    if raw <= easy:
        return 1.0
    if raw >= hard:
        return 5.0
    ratio = math.log1p(raw - easy) / math.log1p(hard - easy)
    return round(1.0 + ratio * 4.0, 2)


def score_task(patch_text: str, test_text: str, instruction: str) -> dict[str, Any]:
    """Compute difficulty score/label from patch, test patch, and instruction text."""
    patch_info = _parse_patch_text(patch_text)
    test_info = _parse_patch_text(test_text) if test_text else {"additions": 0, "deletions": 0, "files": 0}
    test_lines = test_info["additions"] + test_info["deletions"]
    test_files = test_info["files"]
    instr_chars = len(instruction)

    patch_lines = patch_info["additions"] + patch_info["deletions"]
    patch_files = patch_info["files"]
    patch_hunks = patch_info["hunks"]
    directories = len(patch_info["dirs"])

    dim_scores = {
        "patch_scope": _scale(
            patch_lines + patch_files * 8 + patch_hunks * 3, easy=20, hard=260
        ),
        "logic_complexity": _scale(
            patch_info["new_defs"] * 10 + patch_info["control_flow"] * 4 + patch_lines,
            easy=20,
            hard=220,
        ),
        "context_breadth": _scale(
            directories * 15 + patch_files * 4 + patch_hunks, easy=15, hard=120
        ),
        "test_complexity": _scale(test_lines + test_files * 10, easy=35, hard=350),
        "instruction_complexity": _scale(instr_chars, easy=1500, hard=12000),
    }
    weighted_sum = sum(dim_scores[name] * WEIGHTS[name] for name in WEIGHTS)
    final_score = round(1.0 + (weighted_sum - 1.0) * 2.05, 1)
    final_score = max(1.0, min(10.0, final_score))
    if final_score <= 4.0:
        label = "easy"
    elif final_score <= 7.0:
        label = "medium"
    else:
        label = "hard"
    return {
        "difficulty_score": final_score,
        "difficulty_label": label,
        "patch_stats": {"lines": patch_lines, "hunks": patch_hunks, "files": patch_files},
    }


# ---------------------------------------------------------------------------
# LLM tag generation (ported from harbor)
# ---------------------------------------------------------------------------

def _patch_changed_files(patch_text: str) -> list[str]:
    files: list[str] = []
    for line in patch_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4:
            files.append(parts[3].removeprefix("b/"))
    return files


def _sanitize_for_openai(text: str) -> str:
    return text.replace("\x00", "")


def build_tag_prompt(
    record: dict[str, Any],
    *,
    max_instruction: int = MAX_INSTRUCTION_CHARS,
    max_patch: int = MAX_PATCH_CHARS,
    max_test: int = MAX_TEST_CHARS,
    max_files: int = 100,
) -> str:
    instruction = (record.get("problem_statement") or "")[:max_instruction]
    patch_text = record.get("patch") or ""
    if len(patch_text) > max_patch:
        patch_text = patch_text[:max_patch] + "\n... (truncated)"
    changed_files = _patch_changed_files(patch_text)[:max_files]
    test_text = (record.get("test_patch") or "")[:max_test]

    return _sanitize_for_openai(
        "\n".join(
            [
                f"Task name: {record.get('instance_id', '')}",
                "",
                "Instruction:",
                instruction,
                "",
                "Changed files from solution patch:",
                "\n".join(f"- {p}" for p in changed_files) or "- none detected",
                "",
                "Patch excerpt:",
                patch_text,
                "",
                "Test metadata:",
                test_text or "No test snippets available.",
                "",
                'Generate the 4 tags for this task using the required order. Return only: {"tags": [...]}',
            ]
        )
    )


# Progressive truncation levels for the tag prompt. Some endpoints hang on
# mid-size prompts (4-12 KB) even though the same task tags fine with a shorter
# excerpt; when a normal-size prompt fails we retry with tighter caps. The final
# level is an aggressive floor for tasks whose patch/tests are very large.
TRUNCATION_LEVELS = [
    {"max_instruction": MAX_INSTRUCTION_CHARS, "max_patch": MAX_PATCH_CHARS, "max_test": MAX_TEST_CHARS, "max_files": 100},
    {"max_instruction": 1500, "max_patch": 3000, "max_test": 1500, "max_files": 40},
    {"max_instruction": 800, "max_patch": 1200, "max_test": 600, "max_files": 20},
    {"max_instruction": 500, "max_patch": 500, "max_test": 0, "max_files": 10},
    {"max_instruction": 400, "max_patch": 400, "max_test": 0, "max_files": 5},
]


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json(content: str) -> str:
    stripped = _strip_code_fence(content)
    start = stripped.find("{")
    if start == -1:
        raise RuntimeError("LLM response did not contain a JSON object")
    # scan for the first balanced {...} object so trailing text is ignored
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]
    raise RuntimeError("LLM response did not contain a balanced JSON object")


def _normalize_tag(value: Any) -> str:
    if value is None:
        return ""
    tag = str(value).strip().lower()
    tag = re.sub(r"\s+", "-", tag)
    tag = re.sub(r"[^a-z0-9_.+-]+", "-", tag)
    tag = re.sub(r"-{2,}", "-", tag).strip("-")
    return tag


def normalize_tags(raw_tags: Any) -> list[str]:
    if not isinstance(raw_tags, list):
        raise RuntimeError("LLM response field 'tags' is not a list")
    if any(tag is None for tag in raw_tags):
        raise RuntimeError("LLM response field 'tags' contains null")
    tags = [_normalize_tag(tag) for tag in raw_tags]
    tags = [tag for tag in tags if tag]
    if len(tags) < TAG_COUNT:
        raise RuntimeError(f"LLM generated only {len(tags)} tags")
    tags = tags[:TAG_COUNT]
    if tags[1] not in AREA_TAGS:
        raise RuntimeError(f"LLM generated invalid area tag: {tags[1]}")
    return tags


def _chat_completion_content(config: TaggerConfig, system_prompt: str, user_prompt: str) -> str:
    base_url = config.base_url.rstrip("/")
    if not base_url:
        raise RuntimeError("OpenAI-compatible base URL is empty")
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "messages": [
                {"role": "system", "content": _sanitize_for_openai(system_prompt)},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": TAG_COMPLETION_TOKENS,
            # Qwen3.6 endpoint leaks reasoning into content unless thinking is
            # explicitly disabled; this yields clean JSON directly.
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=config.timeout_sec,
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("LLM response did not contain choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        # some endpoints emit reasoning_content when thinking mode leaks
        content = message.get("reasoning_content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM returned empty content")
    return content


def _with_retries(config: TaggerConfig, operation: Callable[[], T]) -> T:
    attempts = max(1, config.retries + 1)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(config.retry_delay_sec * attempt)
    raise last_error or RuntimeError("LLM operation failed")


def generate_tags_for_record(record: dict[str, Any], config: TaggerConfig) -> list[str]:
    """Generate 4 tags, falling back to progressively shorter prompts.

    The first level uses the full-size excerpt. If the endpoint fails (e.g. it
    hangs on a mid-size prompt), each subsequent level truncates the patch,
    instruction, and test text further so the request can still complete.
    """
    last_error: Exception | None = None
    for level in TRUNCATION_LEVELS:
        prompt = build_tag_prompt(record, **level)

        def _once() -> list[str]:
            content = _chat_completion_content(config, TAG_SYSTEM_PROMPT, prompt)
            parsed = json.loads(_extract_json(content))
            if isinstance(parsed, list):
                return normalize_tags(parsed)
            if isinstance(parsed, dict):
                return normalize_tags(parsed.get("tags"))
            raise RuntimeError("LLM response is neither an object nor a tag list")

        try:
            return _with_retries(config, _once)
        except Exception as exc:
            last_error = exc
    raise last_error or RuntimeError("tag generation failed at all truncation levels")


def tag_one_record(record: dict[str, Any], config: TaggerConfig) -> dict[str, Any]:
    """Score difficulty (static) + generate 4 tags (LLM). Raises on failure."""
    scored = score_task(
        record.get("patch") or "",
        record.get("test_patch") or "",
        record.get("problem_statement") or "",
    )
    tags = generate_tags_for_record(record, config)
    return {
        "instance_id": record["instance_id"],
        "difficulty_score": scored["difficulty_score"],
        "difficulty_label": scored["difficulty_label"],
        "tags": tags,
        "bug_class": tags[3],
        "patch_stats": scored["patch_stats"],
    }


# ---------------------------------------------------------------------------
# Dataset driver
# ---------------------------------------------------------------------------

def tag_dataset(dataset_id: str, config: TaggerConfig, jobs: int, max_count: int | None) -> list[str]:
    dataset_dir = DATASETS_DIR / dataset_id
    tasks_jsonl = dataset_dir / "tasks.jsonl"
    output_jsonl = dataset_dir / "tags.jsonl"

    if not tasks_jsonl.exists():
        print(f"ERROR: {tasks_jsonl} 不存在，跳过")
        return []

    print(f"\n{'='*80}\n打标数据集: {dataset_id}\n输入: {tasks_jsonl}\n输出: {output_jsonl}\n并发: {jobs}\n{'='*80}")

    tagged_ids: set[str] = set()
    if output_jsonl.exists():
        with output_jsonl.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    tagged_ids.add(json.loads(line)["instance_id"])
                except Exception:
                    pass
        print(f"已打标(跳过): {len(tagged_ids)}")

    records: list[dict[str, Any]] = []
    with tasks_jsonl.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["instance_id"] not in tagged_ids:
                records.append(rec)
                if max_count and len(records) >= max_count:
                    break

    if not records:
        print("无需打标，跳过")
        return []
    print(f"待打标: {len(records)}")

    completed = 0
    failed: list[str] = []
    write_lock = Lock()

    with output_jsonl.open("a", encoding="utf-8") as out:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
            futures = {executor.submit(tag_one_record, rec, config): rec for rec in records}
            for future in as_completed(futures):
                rec = futures[future]
                try:
                    tagged = future.result()
                    with write_lock:
                        out.write(json.dumps(tagged, ensure_ascii=False) + "\n")
                        out.flush()
                    completed += 1
                    if completed % 200 == 0:
                        print(f"  进度: {completed}/{len(records)} ({completed/len(records)*100:.1f}%)")
                except Exception as exc:
                    failed.append(rec["instance_id"])
                    if len(failed) <= 5:
                        print(f"  ERROR {rec['instance_id']}: {exc}")

    print(f"✓ {dataset_id}: 成功 {completed}, 失败 {len(failed)}")
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="harbor-style difficulty + 4-tag 打标 (JSONL 数据集)")
    parser.add_argument("--dataset", choices=[*DATASET_IDS, "all"], default="all")
    parser.add_argument("--model", default=os.environ.get("TAGGING_MODEL", "Qwen3.6-35B-A3B"))
    parser.add_argument("--api-key", default=os.environ.get("TAGGING_API_KEY", "dummy-cf"))
    parser.add_argument("--base-url", default=os.environ.get("TAGGING_API_BASE_URL", "http://llm.jierungogogo.com/v1"))
    parser.add_argument("--jobs", type=int, default=64)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max-count", type=int, default=None, help="每数据集上限(测试用)")
    args = parser.parse_args(argv)

    config = TaggerConfig(
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        retries=args.retries,
    )
    datasets = DATASET_IDS if args.dataset == "all" else [args.dataset]

    all_failed: dict[str, list[str]] = {}
    for dataset_id in datasets:
        failed = tag_dataset(dataset_id, config, args.jobs, args.max_count)
        if failed:
            all_failed[dataset_id] = failed

    # 自动重跑失败 case（低并发更稳）
    if all_failed:
        print(f"\n{'='*80}\n重跑失败 case (jobs=8)\n{'='*80}")
        retry_config = TaggerConfig(
            model=args.model, api_key=args.api_key, base_url=args.base_url, retries=5, retry_delay_sec=2.0
        )
        for dataset_id in list(all_failed):
            tag_dataset(dataset_id, retry_config, 8, None)

    print("\n" + "=" * 80 + "\n全部打标完成\n" + "=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

