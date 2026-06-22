from __future__ import annotations

import importlib.util
import json
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
    assert module.DEFAULT_STATE == dashboard_root / "memory" / ".progress_monitor_all_state.jsonl"
    assert module.DEFAULT_CACHE == dashboard_root / "memory" / ".progress_monitor_all_cache.json"


def test_render_html_is_swe_task_only_dashboard():
    module = load_dashboard_module()
    lang_rows = {}
    for lang, display, _, _ in module.LANGS:
        lang_rows[lang] = {
            "display": display,
            "pr_count": 10,
            "delta_1h_pr": 1,
            "delta_24h_pr": 2,
            "valid_count": 3,
            "delta_1h_valid": 0,
            "delta_24h_valid": 1,
            "batch": {
                "params": {
                    "OPENAI_MODEL": "openai/test",
                    "ANTHROPIC_MODEL": "claude/test",
                    "n_concurrent": "2",
                    "min_source_files": "1",
                    "max_source_files": "20",
                },
                "status_counts": {"success": 3, "failed": 1},
                "error_type_counts": {"validation": 1},
            },
            "patch": {"avg_lines": 4.0, "avg_hunks": 2.0, "avg_files": 1.0},
            "difficulty_labels": {"easy": 1, "medium": 1, "hard": 1},
            "difficulty_stats": {
                "count": 3,
                "min": 1.0,
                "p25": 2.0,
                "median": 3.0,
                "mean": 3.5,
                "p75": 4.0,
                "max": 5.0,
            },
            "tags": {"python": 2, "testing": 1},
            "tasks_with_tags": 3,
        }

    data = {
        "ts": "2026-06-22T00:00:00+08:00",
        "totals": {
            "pr_count": 80,
            "delta_1h_pr": 8,
            "delta_24h_pr": 16,
            "valid_count": 24,
            "delta_1h_valid": 1,
            "delta_24h_valid": 4,
            "success_rate": 75.0,
            "processed_count": 32,
            "global_tags": {"python": 4, "testing": 2},
            "difficulty_stats": {
                "count": 24,
                "min": 1.0,
                "p25": 2.0,
                "median": 3.0,
                "mean": 3.5,
                "p75": 4.0,
                "max": 5.0,
            },
        },
        "langs": lang_rows,
    }

    html = module.render_html(data, refresh_seconds=3600, output_path=Path("site/index.html"))

    assert "SWE Task Progress Dashboard" in html
    for required_section in (
        "Overview",
        "Inputs &amp; Outputs",
        "Method Notes",
        "Language Progress",
        "Run Parameters",
        "Failure Reason Breakdown",
        "fix.patch Complexity",
        "difficulty_label Distribution",
        "Global Top Tags",
        "Per-Language Tag Distribution",
    ):
        assert required_section in html

    for removed_text in (
        "Trajectory",
        "trajectory",
        "tab-trajectory",
        "switchTab",
        "SWEGEN_TRAJ_DIR",
        "--traj-dir",
    ):
        assert removed_text not in html


def test_dashboard_module_has_no_trajectory_runtime_state():
    module = load_dashboard_module()

    for removed_attr in (
        "TRAJ_DIR",
        "SCAFFOLD_ALIASES",
        "SCORE_FIELDS",
        "_tiktoken_enc",
        "render_trajectory_html",
    ):
        assert not hasattr(module, removed_attr)


def test_load_cache_drops_stale_trajectory_cache(tmp_path):
    module = load_dashboard_module()
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps({"version": module.CACHE_VERSION, "files": {}, "langs": {}, "traj": {"files": {}}}),
        encoding="utf-8",
    )

    cache = module.load_cache(cache_path)

    assert "traj" not in cache
