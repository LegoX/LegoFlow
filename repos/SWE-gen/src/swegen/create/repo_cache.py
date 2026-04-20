from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .file_lock import file_lock


class RepoCache:
    """Manages local clones of repositories for CC analysis.

    Uses a shared clone per repo for fetching, and per-caller git worktrees
    for isolated checkouts.  This keeps the repo-level lock held only during
    the (fast) ``git fetch`` phase rather than for the entire processing
    duration, dramatically reducing contention under high concurrency.
    """

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = (cache_dir or Path(".cache/repos")).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("swegen")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_clone(
        self,
        repo: str,
        head_sha: str,
        repo_url: str | None = None,
        pr_number: int | None = None,
    ) -> Path:
        """Get cached repo or clone it. Checkout the specified commit.

        This is the original (locking) interface kept for backward
        compatibility with callers that do not need concurrent access.
        """
        owner, name = self._parse_repo(repo)
        repo_path = self.cache_dir / owner / name

        if repo_url is None:
            repo_url = f"https://github.com/{repo}.git"

        lock_path = self.cache_dir / "_locks" / f"{owner}__{name}.lock"
        with file_lock(lock_path, timeout=1800):
            if repo_path.exists() and (repo_path / ".git").exists():
                self.logger.debug("Using cached repo: %s", repo_path)
                self._fetch_and_checkout(repo_path, head_sha, pr_number=pr_number)
            else:
                self.logger.info("Cloning repo to cache: %s -> %s", repo, repo_path)
                self._clone(repo_url, repo_path, head_sha, pr_number=pr_number)

        return repo_path

    @contextmanager
    def get_worktree(
        self,
        repo: str,
        head_sha: str,
        repo_url: str | None = None,
        pr_number: int | None = None,
    ) -> Iterator[Path]:
        """Yield an isolated git worktree checked out at *head_sha*.

        The repo-level lock is held only for the brief ``git fetch`` / initial
        clone, then released.  The returned worktree path is private to the
        caller and safe for concurrent use.

        The worktree is automatically removed when the context manager exits.
        """
        owner, name = self._parse_repo(repo)
        repo_path = self.cache_dir / owner / name

        if repo_url is None:
            repo_url = f"https://github.com/{repo}.git"

        # --- Phase 1: ensure the shared clone is up-to-date (under lock) ---
        lock_path = self.cache_dir / "_locks" / f"{owner}__{name}.lock"
        with file_lock(lock_path, timeout=600):
            if repo_path.exists() and (repo_path / ".git").exists():
                self.logger.debug("Fetching into shared clone: %s", repo_path)
                self._fetch(repo_path, head_sha, pr_number=pr_number)
            else:
                self.logger.info("Cloning repo to cache: %s -> %s", repo, repo_path)
                self._clone(repo_url, repo_path, head_sha=None)
                self._ensure_commit_available(repo_path, head_sha, pr_number=pr_number)
        # Lock released — other workers can now fetch concurrently.

        # --- Phase 2: create a private worktree (no lock needed) ---
        wt_id = uuid.uuid4().hex[:12]
        wt_dir = self.cache_dir / "_worktrees" / f"{owner}__{name}_{wt_id}"
        wt_dir.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._create_worktree(repo_path, wt_dir, head_sha)
            yield wt_dir
        finally:
            self._remove_worktree(repo_path, wt_dir)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_repo(self, repo: str) -> tuple[str, str]:
        """Parse 'owner/repo' into (owner, repo) tuple."""
        if repo.startswith("https://"):
            repo = repo.replace("https://github.com/", "").rstrip(".git")
        if repo.startswith("git@"):
            repo = repo.replace("git@github.com:", "").rstrip(".git")

        parts = repo.split("/")
        if len(parts) != 2:
            raise ValueError(f"Invalid repo format: {repo}. Expected 'owner/repo'")
        return parts[0], parts[1]

    def _clone(
        self,
        repo_url: str,
        repo_path: Path,
        head_sha: str | None,
        pr_number: int | None = None,
    ) -> None:
        """Clone a repository and optionally checkout a commit."""
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger.debug("Cloning %s...", repo_url)
        subprocess.run(
            ["git", "clone", "--no-tags", repo_url, str(repo_path)],
            check=True,
            capture_output=True,
        )
        if head_sha is not None:
            self._checkout(repo_path, head_sha, pr_number=pr_number)

    def _has_commit(self, repo_path: Path, sha: str) -> bool:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(repo_path),
            capture_output=True,
        )
        return result.returncode == 0

    def _run_fetch(self, repo_path: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "fetch", "--no-tags", *args],
            cwd=str(repo_path),
            check=check,
            capture_output=True,
        )

    def _ensure_commit_available(
        self,
        repo_path: Path,
        head_sha: str,
        pr_number: int | None = None,
    ) -> None:
        """Fetch only what is needed to materialize a target commit."""
        if self._has_commit(repo_path, head_sha):
            return

        fetch_attempts: list[list[str]] = [
            ["--depth", "1", "origin", head_sha],
        ]
        if pr_number is not None:
            fetch_attempts.append(
                [
                    "--depth",
                    "1",
                    "origin",
                    f"+refs/pull/{pr_number}/head:refs/remotes/origin/pr/{pr_number}",
                ]
            )
        fetch_attempts.append(["origin"])

        last_error: subprocess.CalledProcessError | None = None
        for args in fetch_attempts:
            try:
                self._run_fetch(repo_path, args)
            except subprocess.CalledProcessError as exc:
                last_error = exc
            if self._has_commit(repo_path, head_sha):
                return

        stderr = ""
        if last_error and last_error.stderr:
            stderr = last_error.stderr.decode()
        raise RuntimeError(
            f"Cannot fetch commit {head_sha[:8]}. It may have been force-pushed or deleted. Error: {stderr}"
        )

    def _fetch(self, repo_path: Path, head_sha: str, pr_number: int | None = None) -> None:
        """Fetch just enough refs to materialize *head_sha*."""
        self._ensure_commit_available(repo_path, head_sha, pr_number=pr_number)

    def _fetch_and_checkout(
        self,
        repo_path: Path,
        head_sha: str,
        pr_number: int | None = None,
    ) -> None:
        """Fetch latest and checkout the specified commit."""
        self.logger.debug("Fetching updates for %s...", repo_path)
        self._fetch(repo_path, head_sha, pr_number=pr_number)
        self._checkout(repo_path, head_sha, pr_number=pr_number)

    def _create_worktree(self, repo_path: Path, wt_dir: Path, head_sha: str) -> None:
        """Create a git worktree at *wt_dir* checked out to *head_sha*."""
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(wt_dir), head_sha],
                cwd=str(repo_path),
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else ""
            self.logger.error("Failed to create worktree for %s: %s", head_sha[:8], stderr)
            raise RuntimeError(
                f"Cannot create worktree at {head_sha[:8]}. Error: {stderr}"
            ) from e

        # Update submodules if any
        try:
            subprocess.run(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd=str(wt_dir),
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            self.logger.debug("Submodule update skipped or failed (non-fatal)")

    def _remove_worktree(self, repo_path: Path, wt_dir: Path) -> None:
        """Remove a git worktree, cleaning up on disk."""
        if not wt_dir.exists():
            return
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_dir)],
                cwd=str(repo_path),
                capture_output=True,
                timeout=60,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            self.logger.debug("git worktree remove failed, falling back to rm")
            try:
                shutil.rmtree(wt_dir, ignore_errors=True)
            except Exception:
                pass
        # Prune stale worktree references
        try:
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(repo_path),
                capture_output=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    def _clean_repo(self, repo_path: Path) -> None:
        """Thoroughly clean the repository, including submodules."""
        subprocess.run(
            ["git", "submodule", "deinit", "--all", "-f"],
            cwd=str(repo_path),
            capture_output=True,
        )
        subprocess.run(
            ["git", "reset", "--hard"],
            cwd=str(repo_path),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "clean", "-ffdx"],
            cwd=str(repo_path),
            check=True,
            capture_output=True,
        )

    def _checkout(self, repo_path: Path, sha: str, pr_number: int | None = None) -> None:
        """Checkout a specific commit, fetching if needed."""
        try:
            self._clean_repo(repo_path)
            subprocess.run(
                ["git", "checkout", sha],
                cwd=str(repo_path),
                check=True,
                capture_output=True,
            )
            self.logger.debug("Checked out %s", sha[:8])
        except subprocess.CalledProcessError as e:
            self.logger.debug(
                "Commit %s not found, fetching... (stderr: %s)",
                sha[:8],
                e.stderr.decode() if e.stderr else "",
            )
            try:
                self._ensure_commit_available(repo_path, sha, pr_number=pr_number)
                self._clean_repo(repo_path)
                subprocess.run(
                    ["git", "checkout", sha],
                    cwd=str(repo_path),
                    check=True,
                    capture_output=True,
                )
                self.logger.debug("Fetched and checked out %s", sha[:8])
            except subprocess.CalledProcessError as fetch_err:
                stderr = fetch_err.stderr.decode() if fetch_err.stderr else ""
                self.logger.error("Failed to checkout %s: %s", sha[:8], stderr)
                raise RuntimeError(
                    f"Cannot checkout commit {sha[:8]}. It may have been force-pushed or deleted. Error: {stderr}"
                ) from fetch_err

        try:
            subprocess.run(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd=str(repo_path),
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            self.logger.debug("Submodule update skipped or failed (non-fatal)")
