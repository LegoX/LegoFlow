from __future__ import annotations

import importlib.util
from pathlib import Path


def load_dashboard_module():
    module_path = Path(__file__).with_name("progress_monitor_all.py")
    spec = importlib.util.spec_from_file_location("dashboard_progress_monitor_all", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dashboard_defaults_read_block_artifacts_and_write_local_runtime_files():
    module = load_dashboard_module()
    dashboard_root = Path(__file__).resolve().parent
    block_root = dashboard_root.parent

    assert module.DASHBOARD_ROOT == dashboard_root
    assert module.BLOCK_ROOT == block_root
    assert module.TASKS_ROOT == block_root / "artifacts" / "terminal_tasks"
    assert module.QUESTIONS_ROOT == block_root / "artifacts" / "collected_questions"
    assert module.DEFAULT_HTML == dashboard_root / "site" / "index.html"
    assert module.DEFAULT_STATE == dashboard_root / "memory" / ".progress_monitor_all_state.jsonl"
    assert module.DEFAULT_CACHE == dashboard_root / "memory" / ".progress_monitor_all_cache.json"


def test_collect_returns_one_row_per_configured_domain():
    module = load_dashboard_module()
    rows = module.collect()
    domains = module._domains()
    assert {r["domain"] for r in rows} == set(domains)
    for r in rows:
        assert set(r) >= {"domain", "scraped", "generated", "verified", "rate"}
