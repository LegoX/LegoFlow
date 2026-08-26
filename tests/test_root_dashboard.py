from pathlib import Path

from scripts.root_dashboard import output_rows, rows


def test_root_dashboard_lists_all_declared_blocks():
    root = Path(__file__).resolve().parents[1]
    dashboard_rows = rows(root)

    assert [row[0] for row in dashboard_rows] == ["curator", "tracer", "trainer", "evaluator"]
    assert all(len(row) == 6 for row in dashboard_rows)


def test_root_dashboard_lists_static_output_paths():
    root = Path(__file__).resolve().parents[1]
    dashboard_outputs = output_rows(root)

    names = {row[0] for row in dashboard_outputs}
    assert "curator.swe_tasks_dir" in names
    assert "tracer.raw_trajectories_dir" in names
    assert "evaluator.eval_results_dir" in names
