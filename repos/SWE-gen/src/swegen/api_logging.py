from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _find_project_root(anchor_file: Path) -> Path | None:
    """Find the SWE-gen project root from file location and current working directory."""
    env_root = os.getenv("SWEGEN_PROJECT_ROOT")
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if (candidate / "local_api_logger").is_dir():
            return candidate

    search_roots = [anchor_file.resolve(), Path.cwd().resolve()]
    for start in search_roots:
        for parent in [start, *start.parents]:
            if (parent / "local_api_logger").is_dir() and (parent / "pyproject.toml").exists():
                return parent
    return None


def _noop_log_completion(*args: Any, **kwargs: Any) -> None:
    """Fallback noop logger when local_api_logger is unavailable."""
    return None


def init_api_logger(
    anchor_file: str | Path,
) -> tuple[bool, Callable[..., Any], Callable[[str], int] | None]:
    """
    Initialize local API logger with a stable log directory.

    Returns:
        (available, log_completion_fn, estimate_tokens_fn)
    """
    root = _find_project_root(Path(anchor_file))
    if root is not None:
        root_str = str(root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)

    try:
        from local_api_logger import estimate_tokens, log_completion, set_log_dir
    except ImportError:
        return False, _noop_log_completion, None

    log_dir = os.getenv("SWEGEN_API_LOG_DIR")
    if log_dir:
        resolved_log_dir = Path(log_dir).expanduser().resolve()
    elif root is not None:
        resolved_log_dir = (root / "api_logs").resolve()
    else:
        # Last-resort fallback for unknown execution contexts.
        resolved_log_dir = (Path.cwd() / "api_logs").resolve()

    set_log_dir(str(resolved_log_dir))
    return True, log_completion, estimate_tokens
