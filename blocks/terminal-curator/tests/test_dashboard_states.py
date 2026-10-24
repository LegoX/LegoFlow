"""A running or unreadable run must not be advertised as an idle pipeline."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest
import yaml

BLOCK = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('state,expected_status,expected_stage', [
    ('{incomplete', 'unavailable', None),
    ({'status': 'running'}, 'running', None),
    ({'status': 'completed'}, 'completed', 'completed'),
    (None, 'never-run', 'idle'),
])
def test_unknown_phase_stays_unavailable(tmp_path, monkeypatch, state, expected_status, expected_stage):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    spec = importlib.util.spec_from_file_location('terminal_dashboard_state_qa', BLOCK / 'dashboard/progress_monitor.py')
    board = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(board)
    monkeypatch.setattr(board, 'BLOCK', tmp_path)
    config_path = tmp_path / 'profile.yaml'
    config_path.write_text(yaml.safe_dump(yaml.safe_load((BLOCK / 'config.yaml').read_text())))
    if state is not None:
        (tmp_path / 'artifacts').mkdir()
        (tmp_path / 'artifacts/state.json').write_text(state if isinstance(state, str) else json.dumps(state))
    data = board.snapshot(config_path)
    assert data['status'] == expected_status
    assert data['stage'] == expected_stage
