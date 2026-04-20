from __future__ import annotations

import subprocess
from pathlib import Path

from harbor.models.environment_type import EnvironmentType
from rich.console import Console

import swegen.cli as cli
from swegen.create.pr_fetcher import GitHubPRFetcher
from swegen.create.repo_cache import RepoCache


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | list, headers: dict[str, str] | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self) -> dict | list:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_pr_fetcher_round_robins_tokens_before_403(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_TOKENS", "tok-1,tok-2")
    monkeypatch.setattr("swegen.create.pr_fetcher.random.shuffle", lambda seq: None)

    seen_tokens: list[str] = []

    def fake_get(url: str, headers: dict[str, str], timeout: int = 30):
        seen_tokens.append(headers["Authorization"].split()[-1])
        return _FakeResponse(
            200,
            {"ok": True},
            headers={"X-RateLimit-Remaining": "4999", "X-RateLimit-Reset": "9999999999"},
        )

    monkeypatch.setattr("swegen.create.pr_fetcher.requests.get", fake_get)

    fetcher = GitHubPRFetcher("owner/repo", 123)
    fetcher._api_get("/repos/owner/repo/pulls/123")
    fetcher._api_get("/repos/owner/repo/pulls/123/files")

    assert seen_tokens == ["tok-1", "tok-2"]


def test_pr_fetcher_retries_other_token_on_403(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_TOKENS", "tok-1,tok-2")
    monkeypatch.setattr("swegen.create.pr_fetcher.random.shuffle", lambda seq: None)

    seen_tokens: list[str] = []

    def fake_get(url: str, headers: dict[str, str], timeout: int = 30):
        token = headers["Authorization"].split()[-1]
        seen_tokens.append(token)
        if token == "tok-1":
            return _FakeResponse(
                403,
                {"message": "rate limit exceeded"},
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"},
                text="API rate limit exceeded",
            )
        return _FakeResponse(
            200,
            {"ok": True},
            headers={"X-RateLimit-Remaining": "4999", "X-RateLimit-Reset": "9999999999"},
        )

    monkeypatch.setattr("swegen.create.pr_fetcher.requests.get", fake_get)

    fetcher = GitHubPRFetcher("owner/repo", 123)
    payload = fetcher._api_get("/repos/owner/repo/pulls/123")

    assert payload == {"ok": True}
    assert seen_tokens == ["tok-1", "tok-2"]


def test_repo_cache_ensure_commit_available_prefers_targeted_fetch(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    cat_file_checks = 0

    def fake_run(cmd: list[str], cwd: str | None = None, check: bool = False, capture_output: bool = False):
        nonlocal cat_file_checks
        commands.append(cmd)
        if cmd[:3] == ["git", "cat-file", "-e"]:
            cat_file_checks += 1
            return subprocess.CompletedProcess(cmd, 0 if cat_file_checks > 1 else 1)
        if cmd[:3] == ["git", "fetch", "--no-tags"]:
            return subprocess.CompletedProcess(cmd, 0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("swegen.create.repo_cache.subprocess.run", fake_run)

    cache = RepoCache(tmp_path)
    cache._ensure_commit_available(tmp_path, "deadbeef", pr_number=123)

    assert ["git", "fetch", "--no-tags", "--depth", "1", "origin", "deadbeef"] in commands
    assert not any("--all" in part for cmd in commands for part in cmd)


def test_repo_cache_ensure_commit_available_falls_back_to_pr_ref(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    cat_file_checks = 0

    def fake_run(cmd: list[str], cwd: str | None = None, check: bool = False, capture_output: bool = False):
        nonlocal cat_file_checks
        commands.append(cmd)
        if cmd[:3] == ["git", "cat-file", "-e"]:
            cat_file_checks += 1
            return subprocess.CompletedProcess(cmd, 0 if cat_file_checks > 2 else 1)
        if cmd[:3] == ["git", "fetch", "--no-tags"]:
            if cmd[-1] == "deadbeef":
                raise subprocess.CalledProcessError(1, cmd, stderr=b"sha fetch failed")
            return subprocess.CompletedProcess(cmd, 0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("swegen.create.repo_cache.subprocess.run", fake_run)

    cache = RepoCache(tmp_path)
    cache._ensure_commit_available(tmp_path, "deadbeef", pr_number=321)

    assert [
        "git",
        "fetch",
        "--no-tags",
        "--depth",
        "1",
        "origin",
        "+refs/pull/321/head:refs/remotes/origin/pr/321",
    ] in commands


def test_maybe_prune_create_docker_respects_batch_and_env(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[Path, str]] = []

    def fake_maintenance(console: Console, state_dir: Path) -> bool:
        calls.append((state_dir, "called"))
        return True

    monkeypatch.setattr(cli, "_run_create_docker_maintenance", fake_maintenance)
    console = Console(record=True)

    assert not cli._maybe_prune_create_docker(
        console,
        state_dir=tmp_path,
        environment=EnvironmentType.DOCKER,
        completed_count=1,
        docker_prune_batch=5,
    )
    assert not cli._maybe_prune_create_docker(
        console,
        state_dir=tmp_path,
        environment=EnvironmentType.DAYTONA,
        completed_count=5,
        docker_prune_batch=5,
    )
    assert cli._maybe_prune_create_docker(
        console,
        state_dir=tmp_path,
        environment=EnvironmentType.DOCKER,
        completed_count=5,
        docker_prune_batch=5,
    )
    assert calls == [(tmp_path, "called")]


def test_run_create_docker_maintenance_honors_cooldown(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setenv("SWEGEN_DOCKER_MAINTENANCE_COOLDOWN_MINUTES", "60")

    def fake_run(cmd: list[str], check: bool, capture_output: bool, timeout: int):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    console = Console(record=True)

    assert cli._run_create_docker_maintenance(console, tmp_path)
    assert len(commands) == 4
    assert not cli._run_create_docker_maintenance(console, tmp_path)
    assert len(commands) == 4
