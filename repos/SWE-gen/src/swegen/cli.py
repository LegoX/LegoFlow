from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
from enum import Enum
from collections import deque
from importlib.metadata import PackageNotFoundError as _PkgNotFound
from importlib.metadata import version as _pkg_version
from pathlib import Path
import re
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from datetime import UTC, datetime
from typing import Any

import typer
from dotenv import load_dotenv
from harbor.models.environment_type import EnvironmentType
from openai import OpenAI
import requests
from rich.console import Console

from swegen.config import CreateConfig, FarmConfig
from swegen.create import MissingIssueError, TrivialPRError
from swegen.create.create import (
    CaseTimeoutError,
    append_verifiable_task,
    run_reversal_with_timeout,
)
from swegen.create.file_lock import file_lock, file_semaphore
from swegen.create.task_completion import (
    TaskCompletionState,
    classify_task_dir_state,
    get_incomplete_task_reasons,
    task_has_completed_files,
)
from swegen.farm import StreamFarmer
from swegen.scoring import score_task, update_task_toml_difficulty
from swegen.analyze import AnalyzeArgs, run_analyze
from swegen.analyze.classifier import VERDICT_MODEL
from swegen.llm_env import get_openai_compatible_config, hydrate_cross_provider_env
from swegen.tools.validate import ValidateArgs, run_validate
from swegen.tools.validate_utils import ValidationError, run_nop_oracle, check_validation_passed

load_dotenv()

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Task generation CLI")


def _run_create_docker_maintenance(console: Console, state_dir: Path) -> bool:
    """Run a conservative Docker cleanup for create workloads.

    Uses a short cross-process lock plus a cooldown file so multiple create jobs
    on the same node do not all prune at once.
    """
    if shutil.which("docker") is None:
        return False

    cooldown_minutes = int(os.environ.get("SWEGEN_DOCKER_MAINTENANCE_COOLDOWN_MINUTES", "30"))
    builder_until = os.environ.get("SWEGEN_DOCKER_MAINTENANCE_BUILDER_UNTIL", "24h")
    lock_path = state_dir / "locks" / "docker" / "maintenance.lock"
    stamp_path = state_dir / "locks" / "docker" / "maintenance.last"
    now = time.time()

    try:
        with file_lock(lock_path, timeout=0.1):
            if stamp_path.exists():
                try:
                    last_epoch = float(stamp_path.read_text(encoding="utf-8").strip() or "0")
                except Exception:
                    last_epoch = 0.0
                if last_epoch > 0 and (now - last_epoch) < (cooldown_minutes * 60):
                    return False

            console.print(
                "[dim]  → Running Docker maintenance "
                f"(cooldown {cooldown_minutes}m, builder until {builder_until})...[/dim]"
            )
            commands = [
                ["docker", "container", "prune", "-f"],
                ["docker", "image", "prune", "-f"],
                ["docker", "network", "prune", "-f"],
                ["docker", "builder", "prune", "-af", "--filter", f"until={builder_until}"],
            ]
            for cmd in commands:
                subprocess.run(cmd, check=False, capture_output=True, timeout=600)

            stamp_path.parent.mkdir(parents=True, exist_ok=True)
            stamp_path.write_text(str(now), encoding="utf-8")
            return True
    except TimeoutError:
        return False
    except Exception as exc:
        console.print(f"[yellow]Docker maintenance skipped: {exc}[/yellow]")
        return False


def _maybe_prune_create_docker(
    console: Console,
    *,
    state_dir: Path,
    environment: EnvironmentType,
    completed_count: int,
    docker_prune_batch: int,
) -> bool:
    if environment != EnvironmentType.DOCKER:
        return False
    if docker_prune_batch <= 0:
        return False
    if completed_count <= 0 or completed_count % docker_prune_batch != 0:
        return False
    return _run_create_docker_maintenance(console, state_dir)


def _task_has_completed_files(task_dir: Path) -> bool:
    """Check if a task directory has completed CC output (no TODOs) ready for validation."""
    return task_has_completed_files(task_dir)


class CaseResumeAction(str, Enum):
    NONE = "none"
    REVALIDATE = "revalidate"
    RERUN = "rerun"


def _default_task_id(repo: str, pr: int) -> str:
    return f"{repo.replace('/', '__').lower()}-{pr}"


def _find_existing_task_dir_for_case(
    output: Path,
    repo: str,
    pr: int,
    preferred_task_id: str | None = None,
    *,
    allow_named_tasks: bool = False,
) -> Path | None:
    repo_slug = repo.replace("/", "__").lower()
    candidate_ids: list[str] = []

    if preferred_task_id:
        candidate_ids.append(preferred_task_id)

    default_task_id = _default_task_id(repo, pr)
    if default_task_id not in candidate_ids:
        candidate_ids.append(default_task_id)

    for task_id in candidate_ids:
        task_dir = output / task_id
        if task_dir.exists():
            return task_dir

    if not allow_named_tasks:
        return None

    prefix = f"{repo_slug}-"
    ref_fragment = f"refs/pull/{pr}/head"
    for task_dir in sorted(output.glob(f"{prefix}*")):
        if not task_dir.is_dir():
            continue
        dockerfile = task_dir / "environment" / "Dockerfile"
        if not dockerfile.exists():
            continue
        try:
            if ref_fragment in dockerfile.read_text(encoding="utf-8", errors="replace"):
                return task_dir
        except Exception:
            continue

    return None


def _detect_resume_action_for_case(
    output: Path,
    repo: str,
    pr: int,
    preferred_task_id: str | None = None,
    *,
    allow_named_tasks: bool = False,
) -> tuple[CaseResumeAction, str | None]:
    task_dir = _find_existing_task_dir_for_case(
        output,
        repo,
        pr,
        preferred_task_id,
        allow_named_tasks=allow_named_tasks,
    )
    if task_dir is None:
        return CaseResumeAction.NONE, None

    state = classify_task_dir_state(task_dir)
    if state == TaskCompletionState.COMPLETE:
        return CaseResumeAction.REVALIDATE, task_dir.name
    if state == TaskCompletionState.INCOMPLETE:
        return CaseResumeAction.RERUN, task_dir.name
    return CaseResumeAction.NONE, task_dir.name


def _try_validate_existing_task(
    task_id: str, output: Path, state_dir: str, environment: Any
) -> str | None:
    """If a task dir has completed CC files but wasn't verified, run NOP+Oracle only.

    Returns task_id on success, None if validation fails or files are incomplete.
    """
    from contextlib import nullcontext

    task_dir = output / task_id
    if not task_dir.exists() or not _task_has_completed_files(task_dir):
        return None

    jobs_dir = Path(state_dir) / "harbor-jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    # Gate Docker validation behind the same semaphore used by create.py
    lock_ctx: Any = nullcontext()
    if environment == EnvironmentType.DOCKER:
        lock_dir = Path(state_dir) / "locks" / "docker"
        lock_ctx = file_semaphore(lock_dir, timeout=1800)

    try:
        with lock_ctx:
            nop_reward, oracle_reward, _ = run_nop_oracle(
                task_id=task_id,
                dataset_path=output,
                jobs_dir=jobs_dir,
                environment=environment,
            )
            if check_validation_passed(nop_reward, oracle_reward):
                return task_id
    except Exception:
        pass
    return None


def _preflight_github_token() -> None:
    """Fail fast when GITHUB_TOKEN is missing/invalid."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        typer.echo("[create] Missing GITHUB_TOKEN", err=True)
        raise typer.Exit(code=1)

    try:
        response = requests.get(
            "https://api.github.com/rate_limit",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"token {token}",
            },
            timeout=15,
        )
    except Exception as exc:
        typer.echo(f"[create] GitHub API preflight failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if response.status_code != 200:
        typer.echo(
            f"[create] Invalid GITHUB_TOKEN (status={response.status_code})", err=True
        )
        raise typer.Exit(code=1)

    print(f"[create] GitHub API preflight passed for token {token[:6]}, status code {response.status_code}")


def _preflight_llm_api() -> None:
    """Fail fast when LLM API credentials/base_url/model are invalid."""
    hydrate_cross_provider_env()
    model, api_key, base_url = get_openai_compatible_config()

    if not model:
        typer.echo("[create] Missing model (OPENAI_MODEL/ANTHROPIC_MODEL)", err=True)
        raise typer.Exit(code=1)
    if not api_key:
        typer.echo("[create] Missing LLM API key (OPENAI_API_KEY/ANTHROPIC_API_KEY)", err=True)
        raise typer.Exit(code=1)

    # Use a longer timeout and retries to avoid false negatives on slow but valid endpoints.
    client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": 90.0}
    if base_url:
        client_kwargs["base_url"] = base_url

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            client = OpenAI(**client_kwargs)
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return
        except Exception as exc:
            message = str(exc).lower()
            # Hard-fail for definitive credential/model errors.
            non_retryable_markers = (
                "invalid api key",
                "incorrect api key",
                "authentication",
                "unauthorized",
                "permission denied",
                "forbidden",
                "insufficient_quota",
                "quota exceeded",
                "model not found",
                "no such model",
                "404",
                "401",
                "403",
            )
            if any(marker in message for marker in non_retryable_markers):
                typer.echo(
                    f"[create] LLM API preflight failed for model '{model}' with base url {base_url}: {exc}",
                    err=True,
                )
                raise typer.Exit(code=1) from exc

            if attempt < max_attempts:
                wait_secs = min(6.0, 2.0 * attempt)
                typer.echo(
                    f"[create] LLM API preflight retry {attempt}/{max_attempts - 1} "
                    f"for model '{model}' after transient error: {exc}",
                    err=True,
                )
                time.sleep(wait_secs)
                continue

            typer.echo(
                f"[create] LLM API preflight failed for model '{model}' with base url {base_url}: {exc}",
                err=True,
            )
            raise typer.Exit(code=1) from exc


def _run_create_case_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one create case in a worker process and return a structured result."""
    t0 = time.perf_counter()
    try:
        cfg = CreateConfig(
            repo=str(payload["repo"]),
            pr=int(payload["pr"]),
            output=Path(str(payload["output"])),
            input_ids_file=None,
            max_pr=None,
            timeout=int(payload["timeout"]),
            cc_timeout=int(payload["cc_timeout"]),
            validate=bool(payload["validate"]),
            force=bool(payload["force"]),
            state_dir=Path(str(payload["state_dir"])),
            use_cache=bool(payload["use_cache"]),
            require_minimum_difficulty=bool(payload["require_minimum_difficulty"]),
            min_source_files=int(payload["min_source_files"]),
            max_source_files=int(payload["max_source_files"]),
            require_issue=bool(payload["require_issue"]),
            allow_unmerged=bool(payload["allow_unmerged"]),
            environment=EnvironmentType(str(payload["environment"])),
            generate_name=bool(payload["generate_name"]),
            verbose=bool(payload["verbose"]),
            quiet=bool(payload["quiet"]),
        )
        task_id = run_reversal_with_timeout(cfg, int(payload["timeout"]))
        if not isinstance(task_id, str) or not task_id.strip():
            return {
                "ok": False,
                "error_class": "FileExistsError",
                "error_message": (
                    f"Task skipped by local dedupe for {cfg.repo} PR #{cfg.pr}. "
                    "Use --force to regenerate."
                ),
                "elapsed_seconds": time.perf_counter() - t0,
            }
        return {
            "ok": True,
            "task_id": task_id,
            "elapsed_seconds": time.perf_counter() - t0,
        }
    except Exception as err:
        return {
            "ok": False,
            "error_class": err.__class__.__name__,
            "error_message": str(err),
            "elapsed_seconds": time.perf_counter() - t0,
        }


@app.callback(invoke_without_command=True)
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show swegen version and exit",
        is_eager=True,
    ),
) -> None:
    if version:
        try:
            typer.echo(f"swegen {_pkg_version('swe-gen')}")
        except _PkgNotFound:
            typer.echo("swegen (version unknown)")
        raise typer.Exit()


create_app = typer.Typer(
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
    help="Create a Harbor task from a merged PR and validate",
)


@create_app.callback(context_settings={"allow_extra_args": True})
def create_cmd(
    ctx: typer.Context,
    repo: str | None = typer.Option(None, help="GitHub repository (owner/repo or URL)"),
    pr: int | None = typer.Option(None, help="PR number"),
    input_ids_file: Path | None = typer.Option(
        None,
        "--input-ids-file",
        help='Batch mode input file. One line per PR: "owner/repo:pr-123"',
    ),
    skip_pr_files: list[Path] = typer.Option(
        [],
        "--skip-pr-files",
        help=(
            "Optional task-id files to skip in batch mode. "
            "Supports both repeated flag and single-flag multi-file forms: "
            "--skip-pr-files a.txt --skip-pr-files b.txt OR --skip-pr-files a.txt b.txt. "
            'Each line should be task id "owner__repo-pr".'
        ),
    ),
    max_pr: int | None = typer.Option(
        None,
        "--max-pr",
        help="In batch mode, stop after this many successfully generated/validated tasks",
    ),
    n_concurrent: int = typer.Option(
        1,
        "--n-concurrent",
        help="Batch mode concurrency (number of PRs processed in parallel)",
        show_default=True,
    ),
    output: Path = typer.Option(Path("tasks"), help="Output root", show_default=True),
    timeout: int = typer.Option(
        300, help="Timeout per single create case in seconds", show_default=True
    ),
    cc_timeout: int = typer.Option(
        3200, help="Timeout for CC session in seconds (~53 min default)", show_default=True
    ),
    validate: bool = typer.Option(
        True, help="Run Harbor validations; --no-validate skips validation"
    ),
    force: bool = typer.Option(False, help="Bypass local dedupe and regenerate"),
    state_dir: Path = typer.Option(
        Path(".swegen"), help="Local dedupe state dir", show_default=True
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Disable reusing cached Dockerfiles/test.sh from previous tasks"
    ),
    require_minimum_difficulty: bool = typer.Option(
        True,
        help="Require minimum difficulty (3+ source files); --no-require-minimum-difficulty to skip this check",
    ),
    min_source_files: int = typer.Option(
        3, help="Minimum number of source files required (tests excluded)", show_default=True
    ),
    max_source_files: int = typer.Option(
        10,
        help="Maximum number of source files to avoid large refactors (tests excluded)",
        show_default=True,
    ),
    generate_name: bool = typer.Option(
        False,
        "--generate-name",
        help="Generate a semantic task name (owner__repo-name) instead of using PR number",
    ),
    require_issue: bool = typer.Option(
        True,
        help="Require PR to have a linked issue (higher quality instructions); --no-require-issue uses PR body/title instead",
    ),
    allow_unmerged: bool = typer.Option(
        False,
        help="Allow processing unmerged PRs (for testing/preview); --allow-unmerged to enable",
    ),
    environment: str = typer.Option(
        "docker",
        "-e",
        "--env",
        help="Environment type for Harbor runs (docker|daytona|e2b|modal|runloop|gke)",
        show_default=True,
    ),
    docker_prune_batch: int = typer.Option(
        5,
        help="Run Docker maintenance after every N completed batch cases (0 to disable)",
        show_default=True,
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Increase output verbosity"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Reduce output verbosity"),
) -> None:
    _preflight_github_token()
    _preflight_llm_api()

    def _entry_key_norm(single_repo: str, single_pr: int) -> str:
        return f"{single_repo.lower()}#{single_pr}"

    def _parse_input_ids(path: Path) -> list[tuple[str, int]]:
        entries: list[tuple[str, int]] = []
        with open(path, encoding="utf-8") as f:
            for idx, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.fullmatch(r"([^:\s]+):pr-(\d+)", line)
                if not m:
                    raise typer.BadParameter(
                        f"Invalid line {idx} in {path}: {line!r}. "
                        'Expected format: "owner/repo:pr-123".'
                    )
                parsed_repo = m.group(1)
                if parsed_repo.count("/") != 1:
                    raise typer.BadParameter(
                        f"Invalid repo on line {idx} in {path}: {parsed_repo!r}. "
                        'Expected "owner/repo".'
                    )
                entries.append((parsed_repo, int(m.group(2))))
        return entries

    def _parse_task_id_line(text: str, src_path: Path, line_no: int) -> tuple[str, int]:
        if "__" not in text:
            raise typer.BadParameter(
                f"Invalid line {line_no} in {src_path}: {text!r}. "
                'Expected task id format: "owner__repo-pr".'
            )
        owner, right = text.split("__", maxsplit=1)
        if "-" not in right:
            raise typer.BadParameter(
                f"Invalid line {line_no} in {src_path}: {text!r}. "
                'Expected task id format: "owner__repo-pr".'
            )
        repo, pr_text = right.rsplit("-", maxsplit=1)
        if not owner or not repo or not pr_text.isdigit():
            raise typer.BadParameter(
                f"Invalid line {line_no} in {src_path}: {text!r}. "
                'Expected task id format: "owner__repo-pr".'
            )
        return f"{owner}/{repo}", int(pr_text)

    def _try_parse_task_id(text: str) -> tuple[str, int] | None:
        raw = text.strip()
        if "__" not in raw:
            return None
        owner, right = raw.split("__", maxsplit=1)
        if "-" not in right:
            return None
        repo, pr_text = right.rsplit("-", maxsplit=1)
        if not owner or not repo or not pr_text.isdigit():
            return None
        return f"{owner}/{repo}", int(pr_text)

    def _load_skip_entry_keys(paths: list[Path]) -> set[str]:
        skip_keys: set[str] = set()
        for path in paths:
            if not path.exists():
                raise typer.BadParameter(f"skip file does not exist: {path}")
            with open(path, encoding="utf-8") as f:
                for idx, raw_line in enumerate(f, start=1):
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    repo_from_task, pr_num = _parse_task_id_line(line, path, idx)
                    skip_keys.add(_entry_key_norm(repo_from_task, pr_num))
        return skip_keys

    def _build_config(
        single_repo: str, single_pr: int, *, force_override: bool | None = None
    ) -> CreateConfig:
        return CreateConfig(
            repo=single_repo,
            pr=single_pr,
        output=output,
            input_ids_file=input_ids_file,
            max_pr=max_pr,
            timeout=timeout,
        cc_timeout=cc_timeout,
        validate=validate,
        force=force if force_override is None else force_override,
        state_dir=state_dir,
        use_cache=not no_cache,
        require_minimum_difficulty=require_minimum_difficulty,
        min_source_files=min_source_files,
        max_source_files=max_source_files,
        require_issue=require_issue,
        allow_unmerged=allow_unmerged,
        environment=EnvironmentType(environment),
        generate_name=generate_name,
        verbose=verbose,
        quiet=quiet,
    )

    def _entry_key(single_repo: str, single_pr: int) -> str:
        return f"{single_repo}#{single_pr}"

    def _model_fingerprint() -> str:
        return (
            f"OPENAI_MODEL={os.environ.get('OPENAI_MODEL', '')};"
            f"ANTHROPIC_MODEL={os.environ.get('ANTHROPIC_MODEL', '')};"
            f"OPENAI_API_BASE_URL={os.environ.get('OPENAI_API_BASE_URL', '')};"
            f"ANTHROPIC_BASE_URL={os.environ.get('ANTHROPIC_BASE_URL', '')}"
        )

    def _run_fingerprint() -> str:
        return (
            f"{_model_fingerprint()};"
            f"validate={int(validate)};"
            f"require_issue={int(require_issue)};"
            f"require_minimum_difficulty={int(require_minimum_difficulty)};"
            f"min_source_files={int(min_source_files)};"
            f"max_source_files={int(max_source_files)};"
            f"allow_unmerged={int(allow_unmerged)}"
        )

    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    def _format_duration(seconds: float) -> str:
        total = max(int(seconds), 0)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _print_progress(
        console: Console,
        state_cases: dict[str, dict[str, Any]],
        entry_keys: list[str],
        target_successes: int,
        current_model: str,
        current_run_fingerprint: str,
    ) -> None:
        success_count = sum(
            1 for key in entry_keys if state_cases.get(key, {}).get("status") == "success"
        )
        completed_count = sum(
            1
            for key in entry_keys
            if state_cases.get(key, {}).get("status") in {"success", "failed", "skipped"}
            and not _should_run_case(
                state_cases.get(key, {}),
                current_model,
                current_run_fingerprint,
            )
        )
        remaining_needed = max(target_successes - success_count, 0)
        elapsed_total = float(state.get("total_elapsed_seconds", 0.0))
        avg_case_secs = elapsed_total / completed_count if completed_count > 0 else 0.0
        eta_secs = avg_case_secs * remaining_needed
        console.print(
            "[blue]Progress[/blue] "
            f"Success count / Remaining needed: {success_count}/{remaining_needed}, "
            f"Completed count: {completed_count}, "
            f"Elapsed total time: {_format_duration(elapsed_total)}, "
            f"Estimated time to completion: {_format_duration(eta_secs)}"
        )

    def _classify_error(err: Exception) -> tuple[str, str]:
        if isinstance(err, (CaseTimeoutError,)):
            return "infra", "timeout"
        if isinstance(err, TrivialPRError):
            return "policy", "trivial_pr"
        if isinstance(err, MissingIssueError):
            return "policy", "missing_issue"
        if isinstance(err, ValidationError):
            return "model", "validation"
        if isinstance(err, FileExistsError):
            return "other", "file_exists"

        lowered = str(err).lower()
        infra_markers = (
            "openai",
            "anthropic",
            "api",
            "rate limit",
            "429",
            "connection",
            "timed out",
            "timeout",
            "docker",
            "git",
            "network",
            "ssl",
            "tls",
            "dns",
            "service unavailable",
        )
        if any(marker in lowered for marker in infra_markers):
            return "infra", "infra_error"
        return "model", "workflow_error"

    def _classify_remote_error(error_class: str, error_message: str) -> tuple[str, str]:
        class_map = {
            "CaseTimeoutError": ("infra", "timeout"),
            "TrivialPRError": ("policy", "trivial_pr"),
            "MissingIssueError": ("policy", "missing_issue"),
            "ValidationError": ("model", "validation"),
            "FileExistsError": ("other", "file_exists"),
        }
        if error_class in class_map:
            return class_map[error_class]

        lowered = f"{error_class}: {error_message}".lower()
        infra_markers = (
            "openai",
            "anthropic",
            "api",
            "rate limit",
            "429",
            "connection",
            "timed out",
            "timeout",
            "docker",
            "git",
            "network",
            "ssl",
            "tls",
            "dns",
            "service unavailable",
        )
        if any(marker in lowered for marker in infra_markers):
            return "infra", "infra_error"
        return "model", "workflow_error"

    def _is_retryable_infra(error_type: str, error_message: str) -> bool:
        if error_type == "timeout":
            return True
        lowered = error_message.lower()
        non_retryable_markers = (
            "insufficient_quota",
            "remainquota",
            "quota exceeded",
            "额度已用尽",
            "authenticationerror",
            "invalid api key",
            "incorrect api key",
            "unauthorized",
            "permission denied",
            "forbidden",
            "401",
            "billing",
            "not enough credits",
        )
        return not any(marker in lowered for marker in non_retryable_markers)

    def _should_run_case(
        case_rec: dict[str, Any], current_model: str, current_run_fingerprint: str
    ) -> bool:
        if case_rec.get("status") == "success":
            return False
        if case_rec.get("status") != "failed":
            return True

        failure_kind = str(case_rec.get("failure_kind", ""))
        error_type = str(case_rec.get("error_type", ""))
        attempts = int(case_rec.get("attempts", 0))
        error_message = str(case_rec.get("error_message", "")).lower()

        # Trivial PR is intrinsic to the PR content, not affected by policy settings.
        # Never retry these regardless of fingerprint changes.
        if error_type == "trivial_pr":
            return False

        if failure_kind == "infra":
            # Lock-related timeouts: always retry — the worktree fix resolves the
            # root cause, so previous lock failures should now succeed.
            if "lock" in error_message:
                return True

            # GitHub 403/rate-limit: transient, always worth retrying.
            if "403" in error_message or "rate limit" in error_message:
                return True

            # Case timeout (5400s budget exceeded): check if the repo has ever
            # produced a success.  If not, the repo is likely inherently too slow
            # and retrying is wasteful.  If yes, this specific PR might succeed
            # (e.g. lock contention previously ate into the budget).
            if error_type == "timeout":
                repo = str(case_rec.get("repo", ""))
                repo_has_success = any(
                    r.get("status") == "success" and str(r.get("repo", "")) == repo
                    for r in state_cases.values()
                )
                if repo_has_success:
                    # Repo can succeed — give this PR one more round of retries.
                    return attempts < max_retry + 2
                else:
                    # Repo never succeeded — likely inherently too slow.
                    return attempts < max_retry

            # Other infra errors: cap at max_retry.
            return attempts < max_retry

        if error_type in {"missing_verifiable_record", "incomplete_task_files"}:
            return True

        # New records should compare full run fingerprint (model + policy knobs).
        # This lets resume automatically reprocess policy failures after flag changes.
        previous_run = str(case_rec.get("last_run_fingerprint", ""))
        if previous_run:
            return previous_run != current_run_fingerprint

        # Backward compatibility for old state records that only tracked model.
        # Special-case legacy "missing_issue" failures so switching to
        # --no-require-issue can take effect without manually deleting state.
        if case_rec.get("error_type") == "missing_issue" and not require_issue:
            return True

        previous_model = str(case_rec.get("last_model_fingerprint", ""))
        if not previous_model:
            return True
        return previous_model != current_model

    if timeout <= 0:
        raise typer.BadParameter("timeout must be > 0")
    if n_concurrent <= 0:
        raise typer.BadParameter("--n-concurrent must be > 0")
    if docker_prune_batch < 0:
        raise typer.BadParameter("--docker-prune-batch must be >= 0")

    if input_ids_file:
        if repo is not None or pr is not None:
            raise typer.BadParameter("Use either --repo/--pr or --input-ids-file, not both")
        if max_pr is not None and max_pr <= 0:
            raise typer.BadParameter("--max-pr must be > 0 when provided")
        if not input_ids_file.exists():
            raise typer.BadParameter(f"input ids file does not exist: {input_ids_file}")

        entries = _parse_input_ids(input_ids_file)
        if not entries:
            raise typer.BadParameter(f"No valid PR entries found in: {input_ids_file}")
        extra_skip_pr_files: list[Path] = []
        if ctx.args:
            if not skip_pr_files:
                raise typer.BadParameter(
                    f"Unexpected extra arguments: {' '.join(ctx.args)}. "
                    "Did you mean to use --skip-pr-files?"
                )
            for raw_arg in ctx.args:
                if raw_arg.startswith("-"):
                    raise typer.BadParameter(
                        f"Unexpected option-like extra argument: {raw_arg}. "
                        "When using --skip-pr-files with multiple files, place all file "
                        "paths immediately after --skip-pr-files."
                    )
                extra_skip_pr_files.append(Path(raw_arg))
        merged_skip_pr_files = [*skip_pr_files, *extra_skip_pr_files]

        if merged_skip_pr_files:
            skip_keys = _load_skip_entry_keys(merged_skip_pr_files)
            if skip_keys:
                before_count = len(entries)
                entries = [
                    (entry_repo, entry_pr)
                    for entry_repo, entry_pr in entries
                    if _entry_key_norm(entry_repo, entry_pr) not in skip_keys
                ]
                skipped_count = before_count - len(entries)
                if skipped_count > 0:
                    typer.echo(
                        f"Skip by --skip-pr-files: filtered {skipped_count} entries "
                        f"({len(entries)} remaining)"
                    )
                if not entries:
                    typer.echo("No entries left after --skip-pr-files filtering.")
                    return
        unique_entries: list[tuple[str, int]] = []
        seen_case_keys: set[str] = set()
        for r, p in entries:
            k = _entry_key(r, p)
            if k in seen_case_keys:
                continue
            seen_case_keys.add(k)
            unique_entries.append((r, p))
        entry_case_keys_in_order = [_entry_key(r, p) for r, p in unique_entries]
        entry_keys_set = set(entry_case_keys_in_order)

        console = Console()
        target_successes = max_pr if max_pr is not None else len(entries)
        max_retry = 3
        input_abs = input_ids_file.resolve()
        output.mkdir(parents=True, exist_ok=True)
        state_root = output / ".swegen-create-batch"
        state_root.mkdir(parents=True, exist_ok=True)
        state_file = state_root / f"{hashlib.sha1(str(input_abs).encode('utf-8')).hexdigest()}.json"

        state: dict[str, Any] = {
            "version": 1,
            "input_ids_file": str(input_abs),
            "input_prs_count": len(entries),
            "input_prs": [f"{repo}#{pr_num}" for repo, pr_num in entries],
            "max_retry": max_retry,
            "model_fingerprint": _model_fingerprint(),
            "total_elapsed_seconds": 0.0,
            "cases": {},
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        if state_file.exists():
            try:
                loaded = json.loads(state_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state.update(loaded)
                    if state.get("input_ids_file") != str(input_abs):
                        # Different input file should not reuse state.
                        state = {
                            "version": 1,
                            "input_ids_file": str(input_abs),
                            "input_prs_count": len(entries),
                            "input_prs": [f"{repo}#{pr_num}" for repo, pr_num in entries],
                            "max_retry": max_retry,
                            "model_fingerprint": _model_fingerprint(),
                            "total_elapsed_seconds": 0.0,
                            "cases": {},
                            "created_at": _now_iso(),
                            "updated_at": _now_iso(),
                        }
            except Exception:
                # Corrupted state should not break batch mode.
                state = {
                    "version": 1,
                    "input_ids_file": str(input_abs),
                    "input_prs_count": len(entries),
                    "input_prs": [f"{repo}#{pr_num}" for repo, pr_num in entries],
                    "max_retry": max_retry,
                    "model_fingerprint": _model_fingerprint(),
                    "total_elapsed_seconds": 0.0,
                    "cases": {},
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                }

        # Keep a full snapshot of current input file entries (order-preserving, including duplicates).
        state["input_prs_count"] = len(entries)
        state["input_prs"] = [f"{repo}#{pr_num}" for repo, pr_num in entries]

        def _save_batch_state() -> None:
            state["updated_at"] = _now_iso()
            tmp_file = state_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_file.replace(state_file)

        def _load_verifiable_success_state() -> tuple[set[str], set[str], dict[str, str]]:
            verifiable_file = output / "verifiable_tasks.txt"
            if not verifiable_file.exists():
                return set(), set(), {}
            task_ids: set[str] = set()
            entry_keys: set[str] = set()
            task_id_by_entry_key: dict[str, str] = {}
            with open(verifiable_file, encoding="utf-8") as f:
                for raw in f:
                    tid = raw.strip()
                    if tid:
                        task_ids.add(tid)
                        parsed = _try_parse_task_id(tid)
                        if parsed is not None:
                            parsed_repo, parsed_pr = parsed
                            entry_key = _entry_key_norm(parsed_repo, parsed_pr)
                            entry_keys.add(entry_key)
                            task_id_by_entry_key.setdefault(entry_key, tid)
            return task_ids, entry_keys, task_id_by_entry_key

        state_cases = state.setdefault("cases", {})
        current_model = _model_fingerprint()
        current_run = _run_fingerprint()
        state["model_fingerprint"] = current_model
        state["run_fingerprint"] = current_run
        # Keep case counters scoped to current input file content only.
        for existing_key in list(state_cases.keys()):
            if existing_key not in entry_keys_set:
                state_cases.pop(existing_key, None)

        for batch_repo, batch_pr in unique_entries:
            key = _entry_key(batch_repo, batch_pr)
            record = state_cases.get(key)
            if not isinstance(record, dict):
                record = {"repo": batch_repo, "pr": batch_pr, "attempts": 0}
                state_cases[key] = record

        # Reconcile success state against verifiable_tasks.txt.
        # This prevents stale success flags from incorrectly satisfying --max-pr.
        (
            verifiable_task_ids,
            verifiable_entry_keys,
            verifiable_task_id_by_entry_key,
        ) = _load_verifiable_success_state()
        for batch_repo, batch_pr in unique_entries:
            key = _entry_key(batch_repo, batch_pr)
            record = state_cases.get(key, {})
            default_task_id = f"{batch_repo.replace('/', '__').lower()}-{batch_pr}"
            task_id = str(record.get("task_id") or default_task_id)
            entry_key_norm = _entry_key_norm(batch_repo, batch_pr)
            in_verifiable = (
                task_id in verifiable_task_ids or entry_key_norm in verifiable_entry_keys
            )
            if in_verifiable:
                canonical_task_id = verifiable_task_id_by_entry_key.get(entry_key_norm, task_id)
                # Verify the task files are actually complete (no TODO skeletons)
                task_path = output / canonical_task_id
                if _task_has_completed_files(task_path):
                    if record.get("status") != "success":
                        record.update(
                            {
                                "status": "success",
                                "task_id": canonical_task_id,
                                "failure_kind": None,
                                "error_type": None,
                                "error_message": None,
                                "updated_at": _now_iso(),
                            }
                        )
                else:
                    # Task in verifiable_tasks.txt but files are incomplete/have TODOs
                    # — don't trust it, mark for reprocessing
                    record.update(
                        {
                            "status": "failed",
                            "failure_kind": "state",
                            "error_type": "incomplete_task_files",
                            "error_message": (
                                f"Task {canonical_task_id} in verifiable_tasks.txt but "
                                "Dockerfile/test.sh has TODO placeholders"
                            ),
                            "updated_at": _now_iso(),
                        }
                    )
            elif record.get("status") == "success":
                # Keep attempts/history but mark for reprocessing.
                record.update(
                    {
                        "status": "failed",
                        "failure_kind": "state",
                        "error_type": "missing_verifiable_record",
                        "error_message": (
                            "State had success but task id not found in verifiable_tasks.txt"
                        ),
                        "updated_at": _now_iso(),
                    }
                )

        # Resume old state files safely: when --no-require-issue is enabled, treat historical
        # missing-issue failures as pending so progress reflects current filtering policy.
        if not require_issue:
            reopened_missing_issue = 0
            for record in state_cases.values():
                if not isinstance(record, dict):
                    continue
                if record.get("status") != "failed" or record.get("error_type") != "missing_issue":
                    continue
                previous_run = str(record.get("last_run_fingerprint", ""))
                if previous_run and "require_issue=1" not in previous_run:
                    continue
                record.update(
                    {
                        "status": "pending",
                        "failure_kind": None,
                        "error_type": None,
                        "error_message": None,
                        "updated_at": _now_iso(),
                    }
                )
                reopened_missing_issue += 1
            if reopened_missing_issue > 0:
                console.print(
                    f"[dim]Reopened {reopened_missing_issue} legacy missing-issue cases "
                    "for --no-require-issue[/dim]"
                )
        _save_batch_state()

        prior_attempts = sum(
            1
            for key in entry_case_keys_in_order
            if isinstance(state_cases.get(key), dict)
            and int(state_cases.get(key, {}).get("attempts", 0)) > 0
        )
        if prior_attempts > 0:
            console.print(f"[cyan]Resume batch mode[/cyan] {input_abs}")
        else:
            console.print(f"[cyan]Start batch mode[/cyan] {input_abs}")
        _print_progress(
            console,
            state_cases,
            entry_case_keys_in_order,
            target_successes,
            current_model,
            current_run,
        )

        success_count = sum(
            1 for key in entry_case_keys_in_order if state_cases.get(key, {}).get("status") == "success"
        )
        if success_count >= target_successes:
            console.print(
                f"[green]Already reached --max-pr target ({success_count}/{target_successes}), nothing to do.[/green]"
            )
            return

        if n_concurrent > 1:
            console.print(f"[blue]Batch concurrency: {n_concurrent}[/blue]")
            case_map: dict[str, tuple[str, int]] = {}
            pending_keys: list[str] = []
            completed_this_run = 0
            for batch_repo, batch_pr in unique_entries:
                key = _entry_key(batch_repo, batch_pr)
                case_map[key] = (batch_repo, batch_pr)
                case_rec = state_cases.setdefault(
                    key, {"repo": batch_repo, "pr": batch_pr, "attempts": 0}
                )
                resume_action, _ = _detect_resume_action_for_case(
                    output,
                    batch_repo,
                    batch_pr,
                    str(case_rec.get("task_id") or ""),
                    allow_named_tasks=generate_name,
                )
                if resume_action != CaseResumeAction.NONE or _should_run_case(
                    case_rec, current_model, current_run
                ):
                    pending_keys.append(key)
            # Sort by repo so same-repo PRs are grouped together, reducing lock
            # contention when multiple workers would otherwise fight for the same
            # repo lock simultaneously.
            pending_keys.sort(key=lambda k: case_map[k][0].lower())
            pending_queue = deque(pending_keys)
            retry_not_before: dict[str, float] = {}

            payload_base = {
                "output": str(output),
                "timeout": timeout,
                "cc_timeout": cc_timeout,
                "validate": validate,
                "force": force,
                "state_dir": str(state_dir),
                "use_cache": (not no_cache),
                "require_minimum_difficulty": require_minimum_difficulty,
                "min_source_files": min_source_files,
                "max_source_files": max_source_files,
                "require_issue": require_issue,
                "allow_unmerged": allow_unmerged,
                "environment": environment,
                "generate_name": generate_name,
                "verbose": verbose,
                "quiet": quiet,
            }

            inflight: dict[Future[dict[str, Any]], tuple[str, int]] = {}
            with ProcessPoolExecutor(max_workers=n_concurrent) as executor:
                while True:
                    success_count = sum(
                        1
                        for key in entry_case_keys_in_order
                        if state_cases.get(key, {}).get("status") == "success"
                    )
                    if success_count >= target_successes and not inflight:
                        break

                    max_inflight = n_concurrent

                    schedulable = len(pending_queue)
                    checked = 0
                    while (
                        pending_queue
                        and len(inflight) < max_inflight
                        and success_count < target_successes
                        and checked < schedulable
                    ):
                        key = pending_queue.popleft()
                        checked += 1
                        not_before = retry_not_before.get(key, 0.0)
                        now = time.monotonic()
                        if not_before > now:
                            pending_queue.append(key)
                            continue
                        retry_not_before.pop(key, None)
                        repo_i, pr_i = case_map[key]
                        case_rec = state_cases.setdefault(
                            key, {"repo": repo_i, "pr": pr_i, "attempts": 0}
                        )
                        attempt_no = int(case_rec.get("attempts", 0)) + 1
                        case_rec["attempts"] = attempt_no
                        resume_action, existing_task_id = _detect_resume_action_for_case(
                            output,
                            repo_i,
                            pr_i,
                            str(case_rec.get("task_id") or ""),
                            allow_named_tasks=generate_name,
                        )
                        if resume_action == CaseResumeAction.REVALIDATE and existing_task_id:
                            recovered = _try_validate_existing_task(
                                existing_task_id,
                                output,
                                str(state_dir),
                                EnvironmentType(environment),
                            )
                        else:
                            recovered = None
                        if recovered:
                            console.print(
                                f"[green]Fast-path recovery:[/green] {existing_task_id} validated without CC"
                            )
                            append_verifiable_task(output, recovered)
                            try:
                                task_path = output / recovered
                                if task_path.exists():
                                    ts_score = score_task(task_path, recovered)
                                    update_task_toml_difficulty(task_path, ts_score)
                            except Exception:
                                pass
                            case_rec.update({
                                "status": "success",
                                "task_id": recovered,
                                "failure_kind": None,
                                "error_type": None,
                                "error_message": "fast-path revalidation",
                                "last_model_fingerprint": current_model,
                                "last_run_fingerprint": current_run,
                                "updated_at": _now_iso(),
                            })
                            _save_batch_state()
                            completed_this_run += 1
                            _maybe_prune_create_docker(
                                console,
                                state_dir=state_dir,
                                environment=EnvironmentType(environment),
                                completed_count=completed_this_run,
                                docker_prune_batch=docker_prune_batch,
                            )
                            continue
                        force_rerun = force
                        if resume_action == CaseResumeAction.REVALIDATE and existing_task_id:
                            console.print(
                                f"[yellow]Fast-path revalidation failed; regenerating[/yellow] {existing_task_id}"
                            )
                            force_rerun = True
                        elif resume_action == CaseResumeAction.RERUN and existing_task_id:
                            reasons = "; ".join(
                                get_incomplete_task_reasons(output / existing_task_id)
                            )
                            console.print(
                                f"[yellow]Incomplete generated task found; regenerating[/yellow] "
                                f"{existing_task_id}: {reasons}"
                            )
                            force_rerun = True
                        console.print(f"[cyan]Batch create:[/cyan] {repo_i} PR #{pr_i}")
                        payload = dict(payload_base)
                        payload["repo"] = repo_i
                        payload["pr"] = pr_i
                        payload["force"] = force_rerun
                        fut = executor.submit(_run_create_case_worker, payload)
                        inflight[fut] = (key, attempt_no)

                    if not inflight:
                        if pending_queue:
                            now = time.monotonic()
                            next_ready = min(retry_not_before.get(k, now) for k in pending_queue)
                            sleep_for = max(0.0, min(next_ready - now, 1.0))
                            if sleep_for > 0:
                                time.sleep(sleep_for)
                                continue
                        break

                    done, _ = wait(
                        list(inflight.keys()),
                        return_when=FIRST_COMPLETED,
                        timeout=1.0,
                    )
                    if not done:
                        continue
                    for fut in done:
                        key, attempt_no = inflight.pop(fut)
                        repo_i, pr_i = case_map[key]
                        case_rec = state_cases.setdefault(
                            key, {"repo": repo_i, "pr": pr_i, "attempts": attempt_no}
                        )

                        try:
                            result = fut.result()
                        except Exception as err:
                            result = {
                                "ok": False,
                                "error_class": err.__class__.__name__,
                                "error_message": str(err),
                                "elapsed_seconds": 0.0,
                            }

                        case_elapsed = float(result.get("elapsed_seconds", 0.0))
                        state["total_elapsed_seconds"] = (
                            float(state.get("total_elapsed_seconds", 0.0)) + case_elapsed
                        )

                        if bool(result.get("ok")):
                            raw_task_id = result.get("task_id")
                            task_id = (
                                raw_task_id.strip()
                                if isinstance(raw_task_id, str)
                                else ""
                            )
                            if task_id:
                                append_verifiable_task(output, task_id)
                                # Auto-score difficulty
                                try:
                                    task_path = output / task_id
                                    if task_path.exists():
                                        ts = score_task(task_path, task_id)
                                        update_task_toml_difficulty(task_path, ts)
                                except Exception:
                                    pass  # Scoring failure is non-fatal
                            case_rec.update(
                                {
                                    "status": "success",
                                    "task_id": task_id,
                                    "failure_kind": None,
                                    "error_type": None,
                                    "error_message": None,
                                    "last_model_fingerprint": current_model,
                                    "last_run_fingerprint": current_run,
                                    "updated_at": _now_iso(),
                                }
                            )
                        else:
                            error_class = str(result.get("error_class", "Exception"))
                            error_message = str(result.get("error_message", ""))
                            failure_kind, error_type = _classify_remote_error(
                                error_class, error_message
                            )
                            case_rec.update(
                                {
                                    "status": "failed",
                                    "failure_kind": failure_kind,
                                    "error_type": error_type,
                                    "error_message": error_message,
                                    "last_model_fingerprint": current_model,
                                    "last_run_fingerprint": current_run,
                                    "updated_at": _now_iso(),
                                }
                            )
                            should_retry = (
                                failure_kind == "infra"
                                and attempt_no <= max_retry
                                and _is_retryable_infra(error_type, error_message)
                            )
                            if should_retry:
                                backoff = min(
                                    12.0,
                                    (2 ** max(attempt_no - 1, 0)) + random.uniform(0.0, 1.0),
                                )
                                console.print(
                                    f"[yellow]Infra error, retry {attempt_no}/{max_retry}[/yellow] "
                                    f"{repo_i} PR #{pr_i}: {error_message} "
                                    f"(backoff {backoff:.1f}s)"
                                )
                                retry_not_before[key] = time.monotonic() + backoff
                                pending_queue.append(key)

                        completed_this_run += 1
                        _save_batch_state()
                        _maybe_prune_create_docker(
                            console,
                            state_dir=state_dir,
                            environment=EnvironmentType(environment),
                            completed_count=completed_this_run,
                            docker_prune_batch=docker_prune_batch,
                        )
                        _print_progress(
                            console,
                            state_cases,
                            entry_case_keys_in_order,
                            target_successes,
                            current_model,
                            current_run,
                        )

            success_count = sum(
                1
                for key in entry_case_keys_in_order
                if state_cases.get(key, {}).get("status") == "success"
            )
            if success_count < target_successes:
                raise SystemExit(1)
            return

        completed_this_run = 0
        for batch_repo, batch_pr in unique_entries:
            success_count = sum(
                1 for key in entry_case_keys_in_order if state_cases.get(key, {}).get("status") == "success"
            )
            if success_count >= target_successes:
                break
            key = _entry_key(batch_repo, batch_pr)
            case_rec = state_cases.setdefault(key, {"repo": batch_repo, "pr": batch_pr, "attempts": 0})
            resume_action, existing_task_id = _detect_resume_action_for_case(
                output,
                batch_repo,
                batch_pr,
                str(case_rec.get("task_id") or ""),
                allow_named_tasks=generate_name,
            )

            # Always skip previously successful cases on resume unless there is
            # explicit filesystem recovery work to do for this case.
            if resume_action == CaseResumeAction.NONE and not _should_run_case(
                case_rec, current_model, current_run
            ):
                continue

            console.print(f"[cyan]Batch create:[/cyan] {batch_repo} PR #{batch_pr}")
            recovered = None
            rerun_force = force
            if resume_action == CaseResumeAction.REVALIDATE and existing_task_id:
                recovered = _try_validate_existing_task(
                    existing_task_id,
                    output,
                    str(state_dir),
                    EnvironmentType(environment),
                )
            if recovered:
                console.print(
                    f"[green]Fast-path recovery:[/green] {existing_task_id} validated without CC"
                )
                append_verifiable_task(output, recovered)
                try:
                    task_path = output / recovered
                    if task_path.exists():
                        ts = score_task(task_path, recovered)
                        update_task_toml_difficulty(task_path, ts)
                except Exception:
                    pass
                case_rec.update(
                    {
                        "status": "success",
                        "task_id": recovered,
                        "failure_kind": None,
                        "error_type": None,
                        "error_message": "fast-path revalidation",
                        "last_model_fingerprint": current_model,
                        "last_run_fingerprint": current_run,
                        "updated_at": _now_iso(),
                    }
                )
                _save_batch_state()
                completed_this_run += 1
                _maybe_prune_create_docker(
                    console,
                    state_dir=state_dir,
                    environment=EnvironmentType(environment),
                    completed_count=completed_this_run,
                    docker_prune_batch=docker_prune_batch,
                )
                continue
            if resume_action == CaseResumeAction.REVALIDATE and existing_task_id:
                console.print(
                    f"[yellow]Fast-path revalidation failed; regenerating[/yellow] {existing_task_id}"
                )
                rerun_force = True
            elif resume_action == CaseResumeAction.RERUN and existing_task_id:
                reasons = "; ".join(get_incomplete_task_reasons(output / existing_task_id))
                console.print(
                    f"[yellow]Incomplete generated task found; regenerating[/yellow] "
                    f"{existing_task_id}: {reasons}"
                )
                rerun_force = True

            batch_config = _build_config(batch_repo, batch_pr, force_override=rerun_force)

            attempt = 0
            while True:
                attempt += 1
                case_start = time.perf_counter()
                case_rec["attempts"] = int(case_rec.get("attempts", 0)) + 1
                try:
                    task_id = run_reversal_with_timeout(batch_config, timeout)
                    append_verifiable_task(output, task_id)
                    try:
                        task_path = output / task_id
                        if task_path.exists():
                            ts = score_task(task_path, task_id)
                            update_task_toml_difficulty(task_path, ts)
                    except Exception:
                        pass
                    case_rec.update(
                        {
                            "status": "success",
                            "task_id": task_id,
                            "failure_kind": None,
                            "error_type": None,
                            "error_message": None,
                            "last_model_fingerprint": current_model,
                            "last_run_fingerprint": current_run,
                            "updated_at": _now_iso(),
                        }
                    )
                    break
                except (
                    TrivialPRError,
                    MissingIssueError,
                    ValidationError,
                    FileExistsError,
                    CaseTimeoutError,
                    Exception,
                ) as err:
                    failure_kind, error_type = _classify_error(err)
                    case_rec.update(
                        {
                            "status": "failed",
                            "failure_kind": failure_kind,
                            "error_type": error_type,
                            "error_message": str(err),
                            "last_model_fingerprint": current_model,
                            "last_run_fingerprint": current_run,
                            "updated_at": _now_iso(),
                        }
                    )
                    if (
                        failure_kind == "infra"
                        and attempt <= max_retry
                        and _is_retryable_infra(error_type, str(err))
                    ):
                        console.print(
                            f"[yellow]Infra error, retry {attempt}/{max_retry}[/yellow] "
                            f"{batch_repo} PR #{batch_pr}: {err}"
                        )
                        continue
                    break
                finally:
                    case_elapsed = time.perf_counter() - case_start
                    state["total_elapsed_seconds"] = (
                        float(state.get("total_elapsed_seconds", 0.0)) + case_elapsed
                    )
                    _save_batch_state()

            completed_this_run += 1
            _maybe_prune_create_docker(
                console,
                state_dir=state_dir,
                environment=EnvironmentType(environment),
                completed_count=completed_this_run,
                docker_prune_batch=docker_prune_batch,
            )
            _print_progress(
                console,
                state_cases,
                entry_case_keys_in_order,
                target_successes,
                current_model,
                current_run,
            )

        success_count = sum(
            1 for key in entry_case_keys_in_order if state_cases.get(key, {}).get("status") == "success"
        )
        if success_count < target_successes:
            raise SystemExit(1)
        return

    if max_pr is not None:
        raise typer.BadParameter("--max-pr only works with --input-ids-file")
    if repo is None or pr is None:
        raise typer.BadParameter("--repo and --pr are required for single-PR mode")

    resume_action, existing_task_id = _detect_resume_action_for_case(
        output,
        repo,
        pr,
        allow_named_tasks=generate_name,
    )
    rerun_force = force
    if resume_action == CaseResumeAction.REVALIDATE and existing_task_id:
        recovered = _try_validate_existing_task(
            existing_task_id,
            output,
            str(state_dir),
            EnvironmentType(environment),
        )
        if recovered:
            append_verifiable_task(output, recovered)
            try:
                task_path = output / recovered
                if task_path.exists():
                    ts = score_task(task_path, recovered)
                    update_task_toml_difficulty(task_path, ts)
            except Exception:
                pass
            return
        rerun_force = True
    elif resume_action == CaseResumeAction.RERUN and existing_task_id:
        rerun_force = True

    config = _build_config(repo, pr, force_override=rerun_force)
    try:
        task_id = run_reversal_with_timeout(config, timeout)
        append_verifiable_task(output, task_id)
        try:
            task_path = output / task_id
            if task_path.exists():
                ts = score_task(task_path, task_id)
                update_task_toml_difficulty(task_path, ts)
        except Exception:
            pass
    except (
        TrivialPRError,
        MissingIssueError,
        ValidationError,
        FileExistsError,
        CaseTimeoutError,
    ) as err:
        # These exceptions have already displayed user-friendly messages
        # Exit with error code but don't show traceback
        raise SystemExit(1) from err


app.add_typer(create_app, name="create")


@app.command(help="Validate an existing Harbor task by running NOP and ORACLE")
def validate(
    path: Path = typer.Argument(
        ...,
        help="Path to Harbor dataset root, specific task directory, or task ID when used with dataset root",
    ),
    task: str
    | None = typer.Option(None, "--task", "-t", help="Task ID when --path points to dataset root"),
    agent: str = typer.Option("both", help="Agent to run: both|nop|oracle", show_default=True),
    jobs_dir: Path = typer.Option(
        Path(".swegen/harbor-jobs"),
        help="Directory to store Harbor job artifacts",
        show_default=True,
    ),
    timeout_multiplier: float
    | None = typer.Option(None, help="Multiply default timeouts (e.g., 3.0)"),
    environment: str = typer.Option(
        "docker",
        "-e",
        "--env",
        help="Environment type for Harbor runs (docker|daytona|e2b|modal|runloop|gke)",
        show_default=True,
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Increase output verbosity"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Reduce output verbosity"),
    max_parallel: int = typer.Option(
        8, help="Maximum number of parallel validations (batch mode only)", show_default=True
    ),
    show_passed: bool = typer.Option(
        False,
        "--show-passed",
        help="Show passed tasks in output (batch mode: default shows only failures)",
    ),
    output: Path
    | None = typer.Option(
        None, "-o", "--output", help="Write results to file as they complete (batch mode only)"
    ),
    docker_prune_batch: int = typer.Option(
        5,
        help="Run docker cleanup after every N tasks (0 to disable, local docker only)",
        show_default=True,
    ),
) -> None:
    if agent not in ("both", "nop", "oracle"):
        raise typer.BadParameter("agent must be one of: both, nop, oracle")
    run_validate(
        ValidateArgs(
            path=path,
            task=task,
            jobs_dir=jobs_dir,
            agent=agent,
            timeout_multiplier=timeout_multiplier,
            verbose=verbose,
            quiet=quiet,
            environment=EnvironmentType(environment),
            max_parallel=max_parallel,
            show_passed=show_passed,
            output_file=output,
            docker_prune_batch=docker_prune_batch,
        )
    )


@app.command(help="Analyze a task by running agent trials and classifying outcomes")
def analyze(
    path: Path = typer.Argument(..., help="Path to the task directory to analyze"),
    agent: str = typer.Option(
        "claude-code", "-a", "--agent", help="Agent to run trials with", show_default=True
    ),
    model: str = typer.Option(
        "anthropic/claude-sonnet-4-5",
        "-m",
        "--model",
        help="Model to use for agent trials",
        show_default=True,
    ),
    n_trials: int = typer.Option(
        3, "-k", "--n-trials", help="Number of trials to run", show_default=True
    ),
    n_concurrent: int = typer.Option(
        3, "-n", "--n-concurrent", help="Number of concurrent trials (1=sequential, 3-5 recommended)", show_default=True
    ),
    jobs_dir: Path = typer.Option(
        Path(".swegen/analyze-jobs"),
        "--jobs-dir",
        help="Directory to store job artifacts",
        show_default=True,
    ),
    skip_quality_check: bool = typer.Option(
        False, "--skip-quality-check", help="Skip static quality check"
    ),
    skip_baseline: bool = typer.Option(
        False, "--skip-baseline", help="Skip baseline validation (nop/oracle)"
    ),
    skip_classify: bool = typer.Option(
        False, "--skip-classify", help="Skip LLM classification of trial outcomes"
    ),
    analysis_model: str = typer.Option(
        "claude-sonnet-4-5",
        "--analysis-model",
        help="Model for Claude Code classification",
        show_default=True,
    ),
    timeout_multiplier: float = typer.Option(
        1.0, "--timeout-multiplier", help="Multiply default timeouts", show_default=True
    ),
    environment: str = typer.Option(
        "docker",
        "-e",
        "--env",
        help="Environment type for Harbor runs (docker|daytona|e2b|modal|runloop|gke)",
        show_default=True,
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Increase output verbosity"),
    classification_timeout: int = typer.Option(
        300,
        "--classification-timeout",
        help="Timeout per trial classification in seconds",
        show_default=True,
    ),
    verdict_timeout: int = typer.Option(
        180,
        "--verdict-timeout",
        help="Timeout for verdict synthesis in seconds",
        show_default=True,
    ),
    verdict_model: str = typer.Option(
        VERDICT_MODEL,
        "--verdict-model",
        help="OpenAI model for verdict synthesis",
        show_default=True,
    ),
) -> None:
    """
    Analyze a Harbor task to determine if it's well-specified.

    This command classifies trial outcomes to identify TASK PROBLEMS vs AGENT PROBLEMS:

    1. Static quality check (Harbor's tasks check)
    2. Baseline validation (nop should fail, oracle should pass)
    3. Run N agent trials (default: 3 with Claude Code)
    4. Classify each trial outcome:
       - GOOD_SUCCESS: Agent solved it correctly
       - BAD_SUCCESS: Agent cheated or tests too permissive
       - GOOD_FAILURE: Agent failed due to its own limitations
       - BAD_FAILURE: Agent failed due to task issues
       - HARNESS_ERROR: Infrastructure problem
    5. Compute task verdict with recommendations

    The goal is to identify tasks that need fixing before release.

    Flags match Harbor CLI conventions:
        -k / --n-trials: Total number of trials to run
        -n / --n-concurrent: Number of trials to run concurrently (parallelism)

    Examples:
        # Sequential (default)
        swegen analyze tasks/my-task -k 5

        # Parallel (3 trials at once)
        swegen analyze tasks/my-task -k 10 -n 3
    """
    run_analyze(
        AnalyzeArgs(
            task_path=path,
            agent=agent,
            model=model,
            n_trials=n_trials,
            n_concurrent=n_concurrent,
            jobs_dir=jobs_dir,
            skip_quality_check=skip_quality_check,
            skip_baseline=skip_baseline,
            skip_classify=skip_classify,
            analysis_model=analysis_model,
            verdict_model=verdict_model,
            environment=environment,
            timeout_multiplier=timeout_multiplier,
            verbose=verbose,
            classification_timeout=classification_timeout,
            verdict_timeout=verdict_timeout,
        )
    )




@app.command(help="Continuous PR farming - stream through entire PR history")
def farm(
    repo: str = typer.Argument(
        ..., help="GitHub repository in owner/name format (e.g., fastapi/fastapi)"
    ),
    output: Path = typer.Option(
        Path("tasks"), help="Output directory for generated tasks", show_default=True
    ),
    state_dir: Path = typer.Option(
        Path(".swegen"), help="State directory for cache/logs", show_default=True
    ),
    force: bool = typer.Option(True, help="Regenerate even if task already exists"),
    timeout: int = typer.Option(300, help="Timeout per PR in seconds", show_default=True),
    cc_timeout: int = typer.Option(
        3200, help="Timeout for Claude Code session in seconds (~53 min default)", show_default=True
    ),
    api_delay: float = typer.Option(
        0.5, help="Delay between GitHub API calls in seconds", show_default=True
    ),
    task_delay: int = typer.Option(60, help="Delay between tasks in seconds", show_default=True),
    reset: bool = typer.Option(False, "--reset", help="Reset state and start from beginning"),
    resume_from: str
    | None = typer.Option(
        None, help="Resume from date (e.g., '2024-01-15' or '2024-01-15T10:30:00Z')"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Only show what would run (no task generation)"
    ),
    docker_prune_batch: int = typer.Option(
        5, help="Run docker cleanup after every N PRs (0 to disable)", show_default=True
    ),
    skip_list: str
    | None = typer.Option(None, help="Path to file with task IDs to skip (one per line)"),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Disable reusing cached Dockerfiles/test.sh"
    ),
    require_minimum_difficulty: bool = typer.Option(
        True,
        help="Require minimum difficulty (3+ source files); --no-require-minimum-difficulty to skip this check",
    ),
    min_source_files: int = typer.Option(
        3, help="Minimum number of source files required (tests excluded)", show_default=True
    ),
    max_source_files: int = typer.Option(
        10,
        help="Maximum number of source files to avoid large refactors (tests excluded)",
        show_default=True,
    ),
    environment: str = typer.Option(
        "docker",
        "-e",
        "--env",
        help="Environment type for Harbor runs (docker|daytona|e2b|modal|runloop|gke)",
        show_default=True,
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output"),
    require_issue: bool = typer.Option(
        True,
        help="Require PR to have a linked issue (higher quality); --no-require-issue to process all PRs",
    ),
    validate: bool = typer.Option(
        True, help="Run Harbor validation after CC; --no-validate to skip"
    ),
) -> None:
    """
    Continuously process merged GitHub PRs and convert them to Harbor tasks.
    Streams PRs page-by-page, processes them immediately, and maintains state for resumable operation.
    Uses a language-agnostic pipeline that works for any repository.
    """
    config = FarmConfig(
        repo=repo,
        output=output,
        state_dir=state_dir,
        force=force,
        timeout=timeout,
        cc_timeout=cc_timeout,
        api_delay=api_delay,
        task_delay=task_delay,
        reset=reset,
        resume_from=resume_from,
        dry_run=dry_run,
        docker_prune_batch=docker_prune_batch,
        skip_list=skip_list,
        no_cache=no_cache,
        require_minimum_difficulty=require_minimum_difficulty,
        min_source_files=min_source_files,
        max_source_files=max_source_files,
        environment=EnvironmentType(environment),
        verbose=verbose,
        require_issue=require_issue,
        validate=validate,
    )

    console = Console()
    farmer = StreamFarmer(config.repo, config, console)
    exit_code = farmer.run()
    raise typer.Exit(code=exit_code)
