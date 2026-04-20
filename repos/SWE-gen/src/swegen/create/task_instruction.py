from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from openai import OpenAI

from swegen.api_logging import init_api_logger
from swegen.llm_env import get_openai_compatible_config
from .utils import CombinedPRTaskEvaluation

# API logging setup
_api_logger_available, log_completion, _ = init_api_logger(__file__)

MAX_LINKED_ISSUES = 5
MAX_ISSUE_BODY_LENGTH = 2500
MAX_PR_BODY_LENGTH = 2500
MAX_TEST_FILE_LENGTH = 3000  # Max chars per test file
MAX_TOTAL_TEST_LENGTH = 10000  # Max total chars for all test files
MIN_INSTRUCTION_LENGTH = 100
OPENAI_API_TIMEOUT = 90.0
MAX_COMPLETION_TOKENS = 4096
# Support environment variable for model name (for local model deployment)
MODEL_NAME = os.getenv("OPENAI_MODEL") or os.getenv("ANTHROPIC_MODEL") or "gpt-5.2"
DEBUG_REASON_TRUNCATE_LENGTH = 100

COMBINED_SYSTEM_PROMPT = """You are evaluating GitHub pull requests and converting substantial ones into SWE-bench tasks.

IMPORTANT: You MUST respond with a valid JSON object ONLY. No markdown, no explanation outside JSON. Start your response with `{` and end with `}`.

Your job has TWO PHASES:

PHASE 1 - Evaluate Substantiality:
Determine if the PR is substantial enough to generate a coding task.

SKIP (is_substantial=false) ONLY if the PR is:
- Pure documentation updates (README, docs/, markdown files, doc examples)
- Only dependency/package updates (requirements.txt, package.json version bumps)
- Simple typo or formatting fixes with no functional changes
- CI/config changes only (.github/workflows, .travis.yml, etc.)
- Version bumps or release commits
- Purely cosmetic refactoring (only renaming variables or reformatting)

KEEP (is_substantial=true) if the PR:
- Fixes a real bug (even in a single file) with meaningful logic changes
- Adds or modifies functional tests AND implements corresponding source code changes
- Implements a feature or enhancement with real behavioral changes
- Has meaningful behavioral changes affecting one or more components
- Involves non-trivial logic: conditionals, error handling, data transformations, API changes

IMPORTANT: Single-file changes CAN be substantial if they fix a real bug with meaningful logic.
A one-file fix that adds edge-case handling, fixes incorrect algorithm logic, or changes
non-trivial control flow IS substantial. Only skip truly trivial changes (typos, formatting, version bumps).

PHASE 2 - Generate Task (ONLY if substantial):
If is_substantial=true, write a DETAILED bug report that an engineer can solve.

SOURCE PRIORITY:
1. Linked issues (if available) - for the problem description
2. PR title and body - for context and details
3. Test files - for expected behavior and API specifications

CRITICAL INSTRUCTIONS:
- Write a clear description of the PROBLEM that needs to be solved
- Include specific function/class/method names IF they appear in tests or issues
- Include exact error messages that users see or that tests expect
- Include expected behavior vs actual behavior
- If tests show specific API calls, mention them (e.g., "implement validate_email() method")

IMPORTANT - ABOUT TEST FILES:
You may see test file contents to help you understand what needs to be implemented. However:
✗ DO NOT mention the test files themselves (e.g., "from the test sample", "the test fixture", "the provided test")
✗ DO NOT reference test file names or paths
✗ DO NOT say things like "the test shows" or "according to the tests"

Instead, write as if describing the problem from a user/issue perspective:
✓ "When calling foo() with X, it should return Y but currently returns Z"
✓ "The function should handle these cases: ..."
✓ "Expected behavior: ... Actual behavior: ..."

The agent solving this task will NOT see the test files, so any reference to them will be confusing.

WHAT TO INCLUDE:
✓ Problem description from issue/PR
✓ Expected behavior vs actual behavior
✓ Error messages users see
✓ Function/method/class names that tests call or issue mentions
✓ Expected return values or outputs
✓ Code examples showing the bug (if in issue/PR)
✓ Specific scenarios/cases that should work (derived from tests, but written as requirements)

WHAT TO EXCLUDE:
✗ File paths or module locations (e.g., "fix in utils/validators.py")
✗ Test file names, paths, or references (e.g., "test_foo.py", "the test fixture")
✗ Phrases like "from the test", "the test shows", "according to the tests"
✗ Implementation approaches (e.g., "use a try-catch", "add caching")
✗ How the PR fixed it (e.g., "I changed X to Y")
✗ Internal implementation details not visible in tests/issue

FORMAT RULES:
- Be clear and specific enough that an engineer knows what to implement
- Include code snippets from issues/tests if they clarify the expected behavior
- DO NOT use sections like "Impact:", "Acceptance criteria:", "Notes:", "Additional considerations:"
- Write naturally, as if explaining to a colleague

EXAMPLE GOOD INSTRUCTION:
"The email validation is failing for valid email addresses. When calling user.validate_email('test@example.com'), 
it should return True, but currently returns False for addresses with subdomains. The validation should accept 
any email matching the pattern <local>@<domain>.<tld> including subdomains like test@mail.example.com."

EXAMPLE BAD INSTRUCTION:
"Fix the email validator in utils/auth.py by changing the regex pattern to support subdomains using a more 
permissive regex."

TAGS:
Generate exactly 3 tags in this order:
1. Primary programming language (e.g., "python", "javascript", "typescript", "go", "rust", "java", "ruby", "cpp")
2. Tier/area: Choose ONE from: "backend", "frontend", "fullstack", "cli", "library", "framework"
3. Framework/library name (e.g., "fastapi", "django", "react", "nextjs", "axios", "express") OR a specific category (e.g., "http", "async", "testing")

Examples:
- FastAPI backend project: ["python", "backend", "fastapi"]
- Next.js frontend: ["typescript", "frontend", "nextjs"]
- Ripgrep CLI tool: ["rust", "cli", "regex"]

IMPORTANT: Generate exactly 3 tags.

If NOT substantial, set instruction to null and provide a brief reason.

TASK NAME (optional):
If the user prompt says "Task name requested: yes", generate a short task_name.
- 1-3 words, lowercase ASCII, dash-separated (e.g., "fix-http-header")
- Do not include the repo name or PR number
- Keep it descriptive of the behavior change
If task name is NOT requested, set task_name to null.
"""


def _sanitize_for_openai(text: str | None) -> str:
    """Sanitize text for OpenAI API calls by replacing problematic Unicode characters.
    
    HTTP headers must be ASCII-only. This function replaces Unicode LINE SEPARATOR
    (U+2028) and PARAGRAPH SEPARATOR (U+2029) which can leak into headers and cause
    UnicodeEncodeError when httpx tries to encode them as ASCII.
    
    Args:
        text: Input text that may contain problematic Unicode characters (can be None)
        
    Returns:
        Sanitized text with U+2028 replaced by '\n' and U+2029 replaced by '\n\n'
        Returns empty string if input is None or not a string
    """
    if not isinstance(text, str):
        return ""
    
    # Replace LINE SEPARATOR (U+2028) with newline
    text = text.replace('\u2028', '\n')
    # Replace PARAGRAPH SEPARATOR (U+2029) with double newline
    text = text.replace('\u2029', '\n\n')
    
    return text


def _format_user_prompt(
    pr_title: str,
    pr_body: str,
    repo: str,
    changed_files: list[str],
    linked_issues: list[dict] | None = None,
    force_generate_instruction: bool = False,
    test_contents: dict[str, str] | None = None,
    generate_task_name: bool = False,
) -> str:
    """Format user prompt for combined evaluation + task generation.

    Prioritizes linked issues and avoids leaking solution details (files, diff, commits).
    """
    # Calculate basic stats for evaluation (no file names - just counts)
    total = len(changed_files or [])
    tests = sum(1 for p in (changed_files or []) if "test" in (p or "").lower())
    docs = sum(
        1
        for p in (changed_files or [])
        if any(seg in (p or "").lower() for seg in ("docs/", "doc/"))
    )
    source_files = total - tests - docs

    # Modify ending instruction based on force_generate_instruction flag
    if force_generate_instruction:
        ending_instruction = (
            "\nIMPORTANT: Generate a detailed instruction for this PR regardless of complexity.\n"
            "You should ALWAYS set is_substantial=true and write a comprehensive bug report/task instruction.\n"
            "Even if the PR seems simple, treat it as a valid task and describe the problem that was fixed.\n"
            "Include specific function/method/class names that appear in the tests or issue.\n"
            "Focus on what needs to be implemented, not where or how to implement it.\n"
            "REMEMBER: Do NOT mention test files - the agent won't see them. Write from a user/issue perspective."
        )
    else:
        ending_instruction = (
            "\nFirst, evaluate if this PR is substantial enough to generate a task.\n"
            "Focus on whether the change fixes a REAL bug or implements meaningful functionality.\n"
            "Single-file changes that fix real bugs with non-trivial logic ARE substantial.\n"
            "Only skip truly trivial changes: typos, formatting, version bumps, pure docs.\n"
            "If substantial, write a detailed bug report describing the PROBLEM and what needs to be implemented.\n"
            "Include specific function/method/class names from tests or issues, but NOT file paths or implementation details.\n"
            "REMEMBER: Do NOT mention test files - the agent won't see them. Write from a user/issue perspective.\n"
            "If not substantial, explain why briefly and set instruction to null."
        )

    # Build task name request section
    task_name_section = ""
    if generate_task_name:
        task_name_section = (
            "Task name requested: yes\n"
            "Provide task_name as 1-3 lowercase words with dashes (ASCII letters/digits only).\n"
            "Do not include repo name or PR number.\n\n"
        )

    # Build test contents section if provided
    # NOTE: Tests help the LLM understand expected behavior, but it should NOT
    # mention test files in the instruction since the agent won't see them
    test_section = ""
    if test_contents and len(test_contents) > 0:
        test_lines = ["Test Files (for understanding behavior - do NOT reference these in your instruction):"]
        total_length = 0
        
        # Sort by file size (smaller first) to prioritize including more files
        sorted_tests = sorted(test_contents.items(), key=lambda x: len(x[1]))
        
        for test_file, content in sorted_tests:
            # Truncate individual file if too long
            if len(content) > MAX_TEST_FILE_LENGTH:
                content = content[:MAX_TEST_FILE_LENGTH] + "\n... (truncated)"
            
            # Check if adding this file would exceed total limit
            if total_length + len(content) > MAX_TOTAL_TEST_LENGTH:
                test_lines.append(f"\n... ({len(test_contents) - len(test_lines) + 1} more test files omitted)")
                break
            
            test_lines.append(f"\n--- {test_file} ---")
            test_lines.append(content)
            total_length += len(content)
        
        test_section = "\n".join(test_lines) + "\n\n"

    # MODE 1: Linked issues exist - use issue + PR body + tests
    if linked_issues and len(linked_issues) > 0:
        # Sort by body length (longer = more detail = more useful), take top N
        sorted_issues = sorted(
            linked_issues, key=lambda x: len(x.get("body", "") or ""), reverse=True
        )[:MAX_LINKED_ISSUES]

        issue_lines = []
        for issue in sorted_issues:
            issue_num = issue.get("number", "")
            issue_title = issue.get("title", "")
            issue_repo = issue.get("repo", "")
            issue_body = (issue.get("body", "") or "").strip()
            # Truncate issue body if too long
            if len(issue_body) > MAX_ISSUE_BODY_LENGTH:
                issue_body = issue_body[:MAX_ISSUE_BODY_LENGTH] + "\n...(truncated)"

            # Include repo in issue reference if different from PR repo (cross-repo reference)
            if issue_repo and issue_repo.lower() != repo.lower():
                issue_lines.append(f"Issue {issue_repo}#{issue_num}: {issue_title}")
            else:
                issue_lines.append(f"Issue #{issue_num}: {issue_title}")
            if issue_body:
                issue_lines.append(f"{issue_body}\n")

        issues_section = "\n".join(issue_lines)

        # Include PR body for additional context
        pr_body_truncated = (pr_body or "").strip()
        if len(pr_body_truncated) > MAX_PR_BODY_LENGTH:
            pr_body_truncated = pr_body_truncated[:MAX_PR_BODY_LENGTH] + "\n...(truncated)"
        
        pr_body_section = ""
        if pr_body_truncated:
            pr_body_section = f"PR Description (for additional context):\n{pr_body_truncated}\n\n"

        return (
            f"Repository: {repo}\n"
            f"PR Title: {pr_title}\n\n"
            f"Linked Issue(s):\n{issues_section}\n\n"
            + pr_body_section
            + task_name_section
            + test_section
            + f"Scope (for evaluation only): {source_files} source files, {tests} test files changed\n"
            + ending_instruction
        )

    # MODE 2: No linked issue - use PR title + body + tests
    pr_body_truncated = (pr_body or "").strip()
    if len(pr_body_truncated) > MAX_PR_BODY_LENGTH:
        pr_body_truncated = pr_body_truncated[:MAX_PR_BODY_LENGTH] + "\n...(truncated)"

    return (
        f"Repository: {repo}\n"
        f"PR Title: {pr_title}\n\n"
        + (f"PR Description:\n{pr_body_truncated}\n\n" if pr_body_truncated else "")
        + task_name_section
        + test_section
        + f"Scope (for evaluation only): {source_files} source files, {tests} test files changed\n\n"
        + ending_instruction
    )


def _extract_and_fix_json(text: str) -> str:
    """Extract and fix JSON from LLM response text.

    Handles common issues from non-Claude models (MiniMax, local models, etc.)
    without affecting well-formed JSON responses from Claude/GPT.

    Fixes applied:
    1. Extract JSON object from surrounding text/explanation
    2. Fix trailing commas before } or ]
    3. Fix single-quoted strings → double-quoted
    4. Handle unquoted field names
    5. Fix boolean/null casing (True→true, None→null)
    """
    if not text or not text.strip():
        return text

    s = text.strip()

    # Fast path: already valid JSON — return as-is (covers Claude/GPT)
    try:
        json.loads(s)
        return s
    except (json.JSONDecodeError, ValueError):
        pass

    # Step 1: Extract the outermost JSON object { ... } from surrounding text
    # This handles models that wrap JSON in explanatory text
    brace_start = s.find("{")
    if brace_start == -1:
        return s  # No JSON object found, return original for downstream error

    # Find matching closing brace (handle nested braces)
    depth = 0
    brace_end = -1
    in_string = False
    escape_next = False
    for i in range(brace_start, len(s)):
        c = s[i]
        if escape_next:
            escape_next = False
            continue
        if c == "\\":
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                brace_end = i
                break

    if brace_end == -1:
        # No matching brace — try appending one
        s = s[brace_start:] + "}"
    else:
        s = s[brace_start : brace_end + 1]

    # Step 2: Fix trailing commas (e.g. {"a": 1,} → {"a": 1})
    s = re.sub(r",\s*([}\]])", r"\1", s)

    # Step 3: Fix Python-style booleans/null (True→true, False→false, None→null)
    # Only replace when NOT inside a quoted string — use a simple heuristic:
    # replace only if preceded by ": " or "[ " or ", " (JSON value positions)
    s = re.sub(r'(?<=[\[:,\s])True(?=[\s,}\]])', 'true', s)
    s = re.sub(r'(?<=[\[:,\s])False(?=[\s,}\]])', 'false', s)
    s = re.sub(r'(?<=[\[:,\s])None(?=[\s,}\]])', 'null', s)

    # Step 4: Try parsing — if still fails, try one more aggressive fix
    try:
        json.loads(s)
        return s
    except (json.JSONDecodeError, ValueError):
        pass

    # Step 5: Fix single-quoted strings → double-quoted (risky, last resort)
    # Only do this if the string contains single quotes that look like JSON keys/values
    if "'" in s and '"' not in s:
        s = s.replace("'", '"')

    return s


def evaluate_and_generate_task(
    metadata: dict,
    files: list[dict],
    repo: str,
    model: str = MODEL_NAME,
    api_key: str | None = None,
    linked_issues: list[dict] | None = None,
    force_generate_instruction: bool = False,
    test_contents: dict[str, str] | None = None,
    generate_task_name: bool = False,
) -> CombinedPRTaskEvaluation:
    """Evaluate PR substantiality and generate task description in one LLM call.

    Uses OpenAI's structured outputs with the parse() method for type-safe responses.

    Args:
        metadata: PR metadata dict
        files: List of changed files
        repo: Repository name
        model: OpenAI model to use
        api_key: Optional OpenAI API key
        linked_issues: Optional list of linked issue dicts (with 'title', 'body', 'number')
        force_generate_instruction: If True, always generate an instruction even if PR seems trivial
        test_contents: Optional dict mapping test file paths to their contents
        generate_task_name: If True, request a short semantic task name

    Returns:
        CombinedPRTaskEvaluation with evaluation and task details

    Raises:
        RuntimeError: If API key is missing or LLM call fails
    """
    logger = logging.getLogger("swegen")

    # Check API key
    if not (api_key or os.getenv("OPENAI_API_KEY")):
        raise RuntimeError("OPENAI_API_KEY not set")

    # Prepare prompt data
    # NOTE: We intentionally do NOT pass diff/commits to avoid leaking the solution
    # Sanitize all inputs to remove problematic Unicode characters before processing
    pr_title = _sanitize_for_openai(metadata.get("title", ""))
    pr_body = _sanitize_for_openai(metadata.get("body", ""))
    changed_files = [f.get("filename", "") for f in files]
    
    # Sanitize linked issues if present
    sanitized_linked_issues = None
    if linked_issues:
        sanitized_linked_issues = []
        for issue in linked_issues:
            sanitized_issue = issue.copy()
            sanitized_issue["title"] = _sanitize_for_openai(issue.get("title", ""))
            sanitized_issue["body"] = _sanitize_for_openai(issue.get("body", ""))
            sanitized_linked_issues.append(sanitized_issue)
    
    # Sanitize test contents if present
    sanitized_test_contents = None
    if test_contents:
        sanitized_test_contents = {
            path: _sanitize_for_openai(content) 
            for path, content in test_contents.items()
        }

    user_prompt = _format_user_prompt(
        pr_title,
        pr_body,
        _sanitize_for_openai(repo),  # Sanitize repo name as well
        changed_files,
        linked_issues=sanitized_linked_issues,
        force_generate_instruction=force_generate_instruction,
        test_contents=sanitized_test_contents,
        generate_task_name=generate_task_name,
    )

    # Support OpenAI-compatible and Anthropic-compatible env layouts.
    resolved_model, resolved_api_key, base_url = get_openai_compatible_config(model)
    client_kwargs = {
        "api_key": api_key or resolved_api_key,
        "timeout": OPENAI_API_TIMEOUT,  # Longer timeout for reasoning models
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    
    client = OpenAI(**client_kwargs)

    start_time = time.time()
    try:
        # Sanitize prompts to remove Unicode characters that break HTTP headers
        # (U+2028 LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR)
        sanitized_system_prompt = _sanitize_for_openai(COMBINED_SYSTEM_PROMPT)
        sanitized_user_prompt = _sanitize_for_openai(user_prompt)
        
        # Use chat.completions.create() for third-party API compatibility
        # Note: response_format={"type": "json_object"} is intentionally omitted
        # because some API proxies (e.g. OpenAI-compatible endpoints serving Claude)
        # return empty content when they don't support that parameter.
        # Instead, the system prompt instructs the model to respond with JSON only.
        completion = client.chat.completions.create(
            model=resolved_model or model,
            messages=[
                {"role": "system", "content": sanitized_system_prompt},
                {"role": "user", "content": sanitized_user_prompt},
            ],
            max_tokens=MAX_COMPLETION_TOKENS,
        )

        # Log API call
        if _api_logger_available and completion.usage:
            try:
                duration_ms = (time.time() - start_time) * 1000
                log_completion(
                    model=resolved_model or model,
                    request_data={
                        "model": resolved_model or model,
                        "messages": [
                            {"role": "system", "content": sanitized_system_prompt},
                            {"role": "user", "content": sanitized_user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": MAX_COMPLETION_TOKENS,
                    },
                    response_data={
                        "choices": [{"message": {"content": completion.choices[0].message.content}}],
                        "usage": {
                            "prompt_tokens": completion.usage.prompt_tokens,
                            "completion_tokens": completion.usage.completion_tokens,
                            "total_tokens": completion.usage.total_tokens,
                        },
                    },
                    user="swegen-task-instruction",
                    duration_ms=duration_ms,
                )
            except Exception:
                pass  # Don't fail if logging fails

        content = completion.choices[0].message.content

        # Guard against empty or None response
        if not content or not content.strip():
            raise RuntimeError("LLM returned empty content")

        # Extract JSON from potential markdown code blocks (e.g. ```json ... ```)
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            # Remove opening fence (``` or ```json)
            lines = lines[1:]
            # Remove closing fence if present
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()

        # Robust JSON extraction: handle models that wrap JSON in text or
        # return malformed JSON (e.g. MiniMax, local models).
        # This is a no-op for well-formed JSON responses (Claude, GPT).
        stripped = _extract_and_fix_json(stripped)

        parsed_data = json.loads(stripped) if isinstance(stripped, str) else stripped
        
        # Defensive: Add missing required fields if not present (common with local models)
        # This ensures compatibility with both local and third-party APIs
        # Note: This only adds fields if they are truly missing - it never overwrites existing values
        if isinstance(parsed_data, dict):
            fields_added = []
            
            # Add missing 'is_substantial' field if not present
            if "is_substantial" not in parsed_data:
                # Infer from instruction: if instruction exists and is not None, likely substantial
                instruction_value = parsed_data.get("instruction")
                if instruction_value is not None and instruction_value:
                    parsed_data["is_substantial"] = True
                else:
                    parsed_data["is_substantial"] = False
                fields_added.append("is_substantial")
                logger.warning(
                    "LLM response missing 'is_substantial' field, using inferred value: %s (from instruction: %s)",
                    parsed_data["is_substantial"],
                    "present" if instruction_value else "absent"
                )
            
            # Add missing 'reason' field if not present
            # Use the is_substantial value (either from API or just inferred above)
            if "reason" not in parsed_data:
                is_substantial = parsed_data.get("is_substantial", False)
                if is_substantial:
                    parsed_data["reason"] = "PR modifies multiple source files with substantial changes"
                else:
                    parsed_data["reason"] = "PR does not meet substantiality requirements"
                fields_added.append("reason")
                logger.warning("LLM response missing 'reason' field, using fallback value")
            
            # Only log debug info if we actually added fields (to avoid noise for third-party APIs)
            if fields_added:
                logger.debug("Added missing fields: %s. Final keys: %s", fields_added, list(parsed_data.keys()))
        
        result = CombinedPRTaskEvaluation.model_validate(parsed_data)
        if result is None:
            raise RuntimeError("LLM returned no parsed result")

        logger.debug(
            f"Combined evaluation: is_substantial={result.is_substantial}, reason={result.reason[:DEBUG_REASON_TRUNCATE_LENGTH]}..."
        )

        # Post-process: validate tags if substantial
        if result.is_substantial:
            if len(result.tags) < 1:
                logger.error(f"❌ LLM generated only {len(result.tags)} tags")
                raise RuntimeError(f"LLM generated only {len(result.tags)} tags")

            # Validate instruction length
            if not result.instruction or len(result.instruction.strip()) < MIN_INSTRUCTION_LENGTH:
                logger.error(
                    f"❌ LLM generated instruction too short: {len(result.instruction) if result.instruction else 0} chars"
                )
                raise RuntimeError(
                    f"Instruction too short: {len(result.instruction) if result.instruction else 0} chars (need {MIN_INSTRUCTION_LENGTH}+)"
                )

            # Ensure defaults
            if not result.difficulty:
                result.difficulty = "medium"
            if not result.category:
                result.category = "bugfix"

        return result

    except Exception as exc:
        # Log the specific exception type for better debugging
        exc_type = type(exc).__name__
        logger.error(f"Combined LLM call failed ({exc_type}): {exc}")
        raise RuntimeError(f"Combined LLM call failed: {exc}") from exc
