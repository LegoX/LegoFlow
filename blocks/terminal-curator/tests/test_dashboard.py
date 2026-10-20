"""Dashboard evidence projection, independent of the lifecycle implementation tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest
import yaml

BLOCK = Path(__file__).resolve().parents[1]


@pytest.fixture
def board(tmp_path, monkeypatch, fixture_tasks):
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location('terminal_dashboard_projection', BLOCK / 'dashboard/progress_monitor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    block = tmp_path / 'block'
    block.mkdir()
    artifacts = block / 'artifacts'
    config = yaml.safe_load((BLOCK / 'config.yaml').read_text())
    config['runtime_info']['input']['source'].update(type='tasks', path='input')
    config_path = block / 'config.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(module, 'BLOCK', block)
    return SimpleNamespace(module=module, root=block, artifacts=artifacts, config=config,
                           config_path=config_path, fixtures=fixture_tasks)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def publish(board, name='batch-one', run='test-run', ids=None):
    ids = ids or ['terminal-csv-reconcile', 'terminal-jsonl-summary']
    batch = board.artifacts / 'tasks-versions' / name
    batch.mkdir(parents=True)
    for task in ids:
        shutil.copytree(board.fixtures / task, batch / task)
        write_json(batch / task / 'provenance.json', {'run_id': run, 'kind': 'tasks'})
    (batch / 'verifiable_tasks.txt').write_text('\n'.join(ids)+'\n')
    pointer = board.artifacts / 'tasks'
    if pointer.is_symlink():
        pointer.unlink()
    pointer.symlink_to(batch, target_is_directory=True)
    return batch


def verdict(board, task, agent, reward, passed=True):
    trial = board.artifacts / 'runs/test-run/validation' / agent / task
    write_json(trial / 'result.json', {'task_name': task, 'exception_info': None,
        'verifier_result': {'rewards': {'reward': reward}}})
    (trial / 'verifier').mkdir()
    (trial / 'verifier/junit.xml').write_text(
        f'<testsuite tests="4" failures="{2 if agent == "nop" else 0}" errors="0" skipped="0"/>')
    return {'passed': passed, 'reason': '', 'trial_path': str(trial)}


def test_empty_workspace_is_unknown_not_zero_validation(board):
    data = board.module.snapshot(board.config_path)
    assert data['status'] == 'never-run'
    assert data['total_candidates'] is None
    assert data['tasks'] == data['history'] == data['batches'] == []
    assert data['published_task_count'] == 0
    assert data['validation']['oracle'] == {'recorded': 0, 'passed': 0, 'failed': 0}
    assert data['availability']['state'] == 'missing'
    assert str(board.root) not in json.dumps(data)


def test_cli_reads_the_same_environment_selected_profile_as_the_run(board, monkeypatch, capsys):
    publish(board)
    alternate = board.config.copy()
    alternate['runtime_info'] = {'output': {'harbor_tasks_dir': {'path': 'artifacts/other-pool'}}}
    profile = board.root / 'alternate.local.yaml'
    profile.write_text(yaml.safe_dump(alternate))
    monkeypatch.setenv('TERMINAL_CURATOR_CONFIG', profile.name)
    monkeypatch.setattr(sys, 'argv', ['progress_monitor.py'])
    board.module.main()
    data = json.loads(capsys.readouterr().out)
    assert data['published_task_count'] == 0
    assert board.module.snapshot(board.config_path)['published_task_count'] == 2


def test_running_generation_exposes_measured_usage_without_waiting_for_a_candidate(board):
    write_json(board.artifacts / 'state.json', {'run_id': 'live-generation', 'status': 'running',
               'stage': 'generate', 'source': 'stackoverflow', 'generation_completed': 0})
    write_json(board.artifacts / 'runs/live-generation/api_token_usage.json',
               {'call_count': 4, 'total_tokens': 12345, 'records': [{'model': 'private-model-value'}],
                'api_key': 'fake-private-generation-key'})
    data = board.module.snapshot(board.config_path)
    assert data['generation_completed'] == 0
    assert data['token_usage'] == {'call_count': 4, 'total_tokens': 12345}
    assert 'private-model-value' not in json.dumps(data)
    assert 'fake-private-generation-key' not in json.dumps(data)


def test_metadata_tags_are_labels_not_filesystem_identifiers(board):
    batch = publish(board, ids=['terminal-jsonl-summary'])
    path = batch / 'terminal-jsonl-summary/task.toml'
    original = path.read_text()
    tags = 'tags = ["c++", ".net", "file paths", "文件处理"]'
    path.write_text('\n'.join(tags if line.startswith('tags = ') else line
                              for line in original.splitlines()) + '\n')
    row = board.module.snapshot(board.config_path)['tasks'][0]
    assert row['tags'] == ['c++', '.net', 'file paths', '文件处理']


def test_runtime_evidence_and_batch_metadata_are_measured(board):
    publish(board)
    ids = ['terminal-csv-reconcile', 'terminal-jsonl-summary']
    state = {'run_id': 'test-run', 'source': 'tasks', 'status': 'completed', 'stage': 'completed',
             'total_candidates': 2, 'verified_tasks': ids, 'validation': {}}
    for agent, reward in [('oracle', 1), ('nop', 0)]:
        state['validation'][agent] = {task: verdict(board, task, agent, reward) for task in ids}
    write_json(board.artifacts / 'state.json', state)
    data = board.module.snapshot(board.config_path)
    assert data['published_task_count'] == 2
    assert data['batches'][0]['current'] is True
    assert data['batches'][0]['difficulty_counts'] == {'medium': 2}
    assert data['batches'][0]['source_counts'] == {'tasks': 2}
    assert len(data['tasks']) == 2
    for row in data['tasks']:
        assert row['status'] == 'published'
        assert row['oracle']['reward'] == 1 and row['nop']['reward'] == 0
        assert row['oracle']['tests'] == 4 and row['nop']['failures'] == 2
        assert row['oracle']['basis'] == row['nop']['basis'] == 'raw'
    assert data['validation']['oracle']['passed'] == 2
    encoded = json.dumps(data)
    for private in ['trial_path', str(board.root), 'Reconcile the contact exports', 'solve.sh']:
        assert private not in encoded


def test_failed_attempt_does_not_replace_or_inflate_existing_publication(board):
    publish(board, run='older-run')
    write_json(board.artifacts / 'reports/latest.json', {
        'run_id': 'older-run', 'status': 'completed', 'total_candidates': 999,
        'verified_tasks': ['older-hidden'], 'error': 'old private exception'})
    write_json(board.artifacts / 'state.json', {
        'run_id': 'new-run', 'status': 'failed', 'stage': 'failed', 'source': 'json',
        'total_candidates': 1, 'verified_tasks': [], 'rejected': {'new-candidate': 'private error with endpoint'},
        'validation': {'oracle': {'new-candidate': {'passed': False, 'reason': 'expected reward 1'}}}})
    data = board.module.snapshot(board.config_path)
    assert data['status'] == 'failed'
    assert data['total_candidates'] == 1
    assert data['published_task_count'] == 2 and data['verified_tasks'] == []
    assert len(data['tasks']) == 3
    rejected = next(row for row in data['tasks'] if row['id'] == 'new-candidate')
    assert rejected['status'] == 'rejected'
    assert rejected['oracle']['reason'] == 'reward-mismatch'
    assert 'older-hidden' not in json.dumps(data)
    assert 'private error' not in json.dumps(data)


def test_raw_evidence_missing_and_conflicting_are_distinguished(board):
    publish(board, ids=['terminal-csv-reconcile'])
    task = 'terminal-csv-reconcile'
    oracle = verdict(board, task, 'oracle', 0)
    write_json(board.artifacts / 'state.json', {
        'run_id': 'test-run', 'status': 'completed', 'source': 'tasks', 'verified_tasks': [task],
        'validation': {'oracle': {task: oracle}, 'nop': {task: {'passed': True, 'reason': '', 'trial_path': '/not/read'}}}})
    row = board.module.snapshot(board.config_path)['tasks'][0]
    assert row['oracle']['status'] == 'conflict'
    assert row['oracle']['reason'] == 'evidence-conflict'
    assert row['nop']['status'] == 'passed' and row['nop']['basis'] == 'report'
    assert row['nop']['reward'] is None and row['nop']['tests'] is None


def test_history_keeps_failed_runs_and_has_no_raw_metadata(board):
    board.artifacts.mkdir()
    (board.artifacts / 'index.yaml').write_text(yaml.safe_dump({'runs': [
        {'id': 'run_001', 'status': 'completed', 'started_at': '2026-01-01T00:00:00+00:00',
         'completed_at': '2026-01-01T00:01:30+00:00', 'exit_code': 0, 'notes': 'private notes',
         'archive': '/private/archive'},
        {'id': 'run_002', 'status': 'failed', 'started_at': '2026-01-02T00:00:00+00:00',
         'completed_at': '2026-01-02T00:00:20+00:00', 'exit_code': 1}]}))
    write_json(board.artifacts / 'archives/run_001/report.json', {
        'run_id': 'older-run', 'source': 'tasks', 'total_candidates': 2,
        'verified_tasks': ['task-a', 'task-b'], 'config': {'api_key': 'do-not-include'}})
    data = board.module.snapshot(board.config_path)
    assert [run['id'] for run in data['history']] == ['run_002', 'run_001']
    assert data['history'][0]['verified_count'] is None
    assert data['history'][1]['verified_count'] == 2
    assert data['history'][1]['duration_sec'] == 90
    encoded = json.dumps(data)
    for private in ['private notes', '/private/archive', 'do-not-include', 'api_key']:
        assert private not in encoded


def test_retained_batches_are_distinct_from_current_tasks(board):
    publish(board, name='old', run='old-run', ids=['terminal-csv-reconcile'])
    publish(board, name='new', run='new-run', ids=['terminal-jsonl-summary'])
    data = board.module.snapshot(board.config_path)
    assert data['batch_count'] == 2 and data['published_task_count'] == 1
    assert data['published_tasks'] == ['terminal-jsonl-summary']
    assert {row['status'] for row in data['tasks']} == {'published', 'retained'}


def test_partial_files_and_invalid_manifests_do_not_crash_or_leak(board):
    batch = publish(board, ids=['terminal-csv-reconcile'])
    (batch / 'verifiable_tasks.txt').write_text('terminal-csv-reconcile\n../private\nterminal-csv-reconcile\n')
    (board.artifacts / 'state.json').write_text('{truncated private backend data')
    (board.artifacts / 'index.yaml').write_text('runs: [invalid:')
    data = board.module.snapshot(board.config_path)
    assert data['status'] == 'unavailable'
    assert data['availability']['state'] == data['availability']['index'] == 'unavailable'
    assert data['availability']['ignored_manifest_entries'] == 2
    assert data['published_task_count'] == 1
    assert 'private' not in json.dumps(data)


def test_public_projection_rejects_untrusted_shapes_and_config_secret_smuggling(board, monkeypatch):
    secret = 'fixture-opaque-key-value'
    board.config['runtime_info']['input']['llm_api']['api_key'] = secret
    board.config_path.write_text(yaml.safe_dump(board.config))
    write_json(board.artifacts / 'state.json', {
        'run_id': secret, 'status': '<script>bad()</script>', 'stage': '/private/path',
        'source': {'api_key': secret}, 'total_candidates': True,
        'verified_tasks': ['../../outside', secret], 'rejected': [],
        'token_usage': {'total_tokens': float('nan'), 'call_count': True, 'records': [secret]},
        'config': {'password': secret}, 'jobs': {'oracle': '/private/jobs'}})
    data = board.module.snapshot(board.config_path)
    assert data['status'] == 'unavailable' and data['source'] is None
    assert data['total_candidates'] is None and data['token_usage'] == {}
    assert secret not in json.dumps(data) and '/private' not in json.dumps(data)


def test_export_contains_same_curator_chrome_and_inert_embedded_data(board):
    attack = '</script><img src=x onerror=alert(1)>&'
    page = board.module.render({'status': attack, 'run_id': attack, 'published_tasks': [attack]})
    assert '<img src=x' not in page and '&lt;img' in page
    assert '<script id="dashboard-data" type="application/json">' in page
    assert '\\u003c/script' in page
    assert 'class="sidebar"' in page and 'class="topbar"' in page
    assert '--c-bg: #fafaf7' in page and '--c-accent: #b3431f' in page
    assert 'width: 240px' in page
    assert 'data-live="false"' in page
    assert 'data-live="true"' in board.module.render({'status': 'running'}, live=True)


def test_configured_output_symlink_cannot_read_outside_artifacts(board, tmp_path):
    outside = tmp_path / 'private-pool'
    outside.mkdir()
    (outside / 'verifiable_tasks.txt').write_text('private-data-task\n')
    board.artifacts.mkdir()
    (board.artifacts / 'tasks').symlink_to(outside, target_is_directory=True)
    data = board.module.snapshot(board.config_path)
    assert data['availability']['config'] == 'unavailable'
    assert data['published_task_count'] is None
    assert 'private-data-task' not in json.dumps(data)


def test_generation_progress_exposes_real_counts_but_never_raw_reasons(board):
    write_json(board.artifacts / 'state.json', {
        'run_id': 'generation-run', 'status': 'running', 'stage': 'generate', 'source': 'json',
        'collected_questions': 3, 'generation_completed': 2, 'generation_failed': 1,
        'total_candidates': 1, 'candidate_ids': ['candidate-ready'],
        'task_progress': {'candidate-ready': {'stage': 'generate', 'status': 'completed'},
                          'candidate-pending': {'stage': 'generate', 'status': 'running', 'reason': 'private prompt'}},
        'rejected': {'candidate-failed': 'private exception'}})
    data = board.module.snapshot(board.config_path)
    assert data['collected_questions'] == 3
    assert data['generation_completed'] == 2 and data['generation_failed'] == 1
    assert {task['id'] for task in data['tasks']} == {'candidate-ready', 'candidate-pending', 'candidate-failed'}
    running = next(row for row in data['tasks'] if row['id'] == 'candidate-pending')
    assert running['status'] == 'running' and running['phase'] == 'generate'
    assert 'private' not in json.dumps(data)


def test_harbor_checksum_linkage_never_uses_the_block_file_digest(board):
    publish(board, ids=['terminal-csv-reconcile'])
    task = 'terminal-csv-reconcile'
    entry = verdict(board, task, 'oracle', 1)
    result_path = Path(entry['trial_path']) / 'result.json'
    raw = json.loads(result_path.read_text())
    raw['task_checksum'] = 'a' * 64
    write_json(result_path, raw)
    state = {'run_id': 'test-run', 'status': 'completed', 'source': 'tasks',
             'harbor_task_checksums': {task: 'a' * 64}, 'task_checksums': {task: 'b' * 64},
             'validation': {'oracle': {task: entry}}}
    write_json(board.artifacts / 'state.json', state)
    row = board.module.snapshot(board.config_path)['tasks'][0]
    assert row['harbor_checksum'] == 'a' * 64 and row['block_checksum'] == 'b' * 64
    assert row['oracle']['checksum_matches'] is True and row['oracle']['status'] == 'passed'
    state['harbor_task_checksums'][task] = 'c' * 64
    write_json(board.artifacts / 'state.json', state)
    row = board.module.snapshot(board.config_path)['tasks'][0]
    assert row['oracle']['checksum_matches'] is False
    assert row['oracle']['status'] == 'conflict' and row['oracle']['reason'] == 'checksum-mismatch'


def test_missing_profile_does_not_silently_publish_the_default_pool(board):
    publish(board)
    board.config_path.unlink()
    data = board.module.snapshot(board.config_path)
    assert data['availability']['config'] == 'missing'
    assert data['availability']['manifest'] == 'unavailable'
    assert data['published_task_count'] is None and data['batches'] == []


def test_version_directory_symlink_cannot_expose_external_batch_names(board, tmp_path):
    private = tmp_path / 'private-versions'
    (private / 'sensitive-batch-name').mkdir(parents=True)
    board.artifacts.mkdir()
    (board.artifacts / 'tasks-versions').symlink_to(private, target_is_directory=True)
    data = board.module.snapshot(board.config_path)
    assert data['batch_count'] == 0 and data['batches'] == []
    assert 'sensitive-batch-name' not in json.dumps(data)


@pytest.mark.parametrize('expected_run,report_start', [
    ('different-attempt', '2026-01-02T00:00:01+00:00'),
    ('stale-attempt', '2026-01-01T00:00:00+00:00'),
])
def test_archived_stale_report_cannot_supply_success_counts(board, expected_run, report_start):
    board.artifacts.mkdir()
    (board.artifacts / 'index.yaml').write_text(yaml.safe_dump({'runs': [
        {'id': 'run_001', 'run_id': expected_run, 'status': 'failed',
         'started_at': '2026-01-02T00:00:00+00:00', 'completed_at': '2026-01-02T00:01:00+00:00'}]}))
    write_json(board.artifacts / 'archives/run_001/report.json', {
        'run_id': 'stale-attempt', 'started_at': report_start, 'status': 'completed',
        'source': 'tasks', 'total_candidates': 99, 'verified_tasks': ['stale-task']})
    row = board.module.snapshot(board.config_path)['history'][0]
    assert row['status'] == 'failed' and row['run_id'] is None
    assert row['candidates'] is None and row['verified_count'] is None


def test_concurrent_publication_cannot_mix_one_manifest_with_another_batch(board, monkeypatch):
    old = publish(board, name='before', run='old-run', ids=['terminal-csv-reconcile'])
    new = publish(board, name='after', run='new-run', ids=['terminal-jsonl-summary'])
    pointer = board.artifacts / 'tasks'
    pointer.unlink()
    pointer.symlink_to(old, target_is_directory=True)
    original = board.module._manifest
    first = True

    def retarget_after_manifest(path, artifacts):
        nonlocal first
        result = original(path, artifacts)
        if first:
            first = False
            pointer.unlink()
            pointer.symlink_to(new, target_is_directory=True)
        return result

    monkeypatch.setattr(board.module, '_manifest', retarget_after_manifest)
    data = board.module.snapshot(board.config_path)
    assert data['published_tasks'] == ['terminal-csv-reconcile']
    current = next(batch for batch in data['batches'] if batch['current'])
    assert current['id'] == 'before' and current['available_count'] == 1
    assert next(row for row in data['tasks'] if row['status'] == 'published')['run_id'] == 'old-run'


def test_private_process_metadata_is_never_a_dashboard_input(board):
    write_json(board.artifacts / 'run.process.json', {
        'pid': 99999, 'host': 'private-node.invalid', 'config': '/private/profile',
        'api_key': 'private-process-key'})
    data = board.module.snapshot(board.config_path)
    assert data['status'] == 'never-run'
    for private in ['99999', 'private-node', '/private/profile', 'private-process-key', 'pid']:
        assert private not in json.dumps(data)


def test_malformed_history_is_unavailable_instead_of_zero_runs(board):
    board.artifacts.mkdir()
    (board.artifacts / 'index.yaml').write_text('runs: not-a-list\n')
    data = board.module.snapshot(board.config_path)
    assert data['archive_count'] is None
    assert data['availability']['index'] == 'unavailable'


def test_exact_archive_index_counts_survive_unavailable_archived_report(board):
    board.artifacts.mkdir()
    (board.artifacts / 'index.yaml').write_text(yaml.safe_dump({'runs': [
        {'id': 'run_001', 'run_id': 'exact-runtime-id', 'status': 'completed',
         'verified_count': 2, 'rejected_count': 1}]}))
    row = board.module.snapshot(board.config_path)['history'][0]
    assert row['run_id'] == 'exact-runtime-id'
    assert row['verified_count'] == 2 and row['rejected_count'] == 1
    assert row['source'] is None and row['candidates'] is None


def load_demo_builder():
    spec = importlib.util.spec_from_file_location('terminal_synthetic_demo_builder', BLOCK / 'dashboard/build_demo.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tracked_synthetic_demo_is_deterministic_and_current(monkeypatch):
    builder = load_demo_builder()
    monkeypatch.setenv('TERMINAL_CURATOR_TEST_SECRET', 'fixture-demo-environment-private-value')
    first = builder.render_demo()
    assert first == builder.render_demo()
    assert first == (builder.DEMO / 'index.html').read_text()
    assert '<strong>Synthetic demo only.</strong>' in first
    assert 'not a real curation, validation or solving result' in first
    assert 'data-live="false"' in first
    assert 'fixture-demo-environment-private-value' not in first


@pytest.mark.parametrize('mutate', [
    lambda data: data.update(demo=False),
    lambda data: data.update(run_id='actual-run-identifier'),
    lambda data: data['tasks'][0].update(trial_path='/private/local/trial'),
    lambda data: data['tasks'][0].update(id='actual-task-identifier'),
])
def test_docs_demo_rejects_real_identity_or_private_field_shapes(mutate):
    builder = load_demo_builder()
    data = json.loads((builder.DEMO / 'fixture.json').read_text())
    mutate(data)
    with pytest.raises(ValueError):
        builder.validate_fixture(data)
