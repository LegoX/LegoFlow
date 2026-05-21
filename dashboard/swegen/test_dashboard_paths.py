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


def test_dashboard_defaults_read_swegen_data_and_write_local_runtime_files():
    module = load_dashboard_module()
    dashboard_root = Path(__file__).resolve().parent
    home_root = Path.home()

    assert module.DASHBOARD_ROOT == dashboard_root
    assert module.REPO_ROOT == home_root / "SWE-gen"
    assert module.ROOT == home_root / "SWE-gen" / "tasks" / "March"
    assert module.PR_DIR == home_root / "SWE-gen" / "collected_prs"
    assert module.DEFAULT_HTML == dashboard_root / "site" / "index.html"
    assert module.DEFAULT_STATE == dashboard_root / ".progress_monitor_all_state.jsonl"
    assert module.DEFAULT_CACHE == dashboard_root / ".progress_monitor_all_cache.json"
