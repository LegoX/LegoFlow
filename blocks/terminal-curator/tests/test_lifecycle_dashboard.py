"""Exercise local lifecycle, collection and dashboard boundaries without services."""

import copy
import fcntl
import http.client
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import BLOCK


@pytest.fixture
def committed_repo(tmp_path):
    repo = tmp_path / 'offline-git-source'
    repo.mkdir()
    env = {
        'PATH': os.environ['PATH'],
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_AUTHOR_NAME': 'Terminal Curator Offline Test',
        'GIT_AUTHOR_EMAIL': 'terminal-curator-test@example.invalid',
        'GIT_COMMITTER_NAME': 'Terminal Curator Offline Test',
        'GIT_COMMITTER_EMAIL': 'terminal-curator-test@example.invalid',
        'GIT_AUTHOR_DATE': '2000-01-01T00:00:00Z',
        'GIT_COMMITTER_DATE': '2000-01-01T00:00:00Z',
    }

    def git(*args, directory=repo):
        return subprocess.run(
            ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
             '-C', str(directory), *args],
            env=env, check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()

    git('init', '--quiet')
    (repo / 'README.txt').write_text('Independent offline repository metadata fixture.\n')
    git('add', 'README.txt')
    git('commit', '--quiet', '-m', 'Record offline fixture source')
    return SimpleNamespace(path=repo, sha=git('rev-parse', 'HEAD'), git=git)


@pytest.fixture
def local_block(adapter, tmp_path, monkeypatch):
    block = tmp_path / 'isolated-block'
    artifacts = block / 'artifacts'
    (block / 'scripts').mkdir(parents=True)
    (block / 'scripts/start.sh').write_text('#!/bin/bash\necho isolated-test-script\n')
    config = yaml.safe_load((BLOCK / 'config.yaml').read_text())
    config['runtime_info']['input']['source'].update(type='tasks', path='input-tasks')
    (block / 'input-tasks').mkdir()
    config_path = block / 'overlay.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(adapter, 'BLOCK', block)
    monkeypatch.setattr(adapter, 'ARTIFACTS', artifacts)
    return SimpleNamespace(block=block, artifacts=artifacts,
                           config=config, config_path=config_path)


@pytest.fixture
def dashboard(adapter, local_block, monkeypatch):
    # The dashboard imports the same adapter module whose roots are isolated.
    monkeypatch.setitem(sys.modules, 'terminal_curator', adapter)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    spec = importlib.util.spec_from_file_location(
        'terminal_curator_dashboard_under_test', BLOCK / 'dashboard/progress_monitor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'BLOCK', local_block.block)
    return module


@pytest.mark.parametrize('output_path', ['../outside', '/tmp/terminal-curator-output',
                                         'artifacts/../outside', 'artifacts'])
def test_config_output_cannot_escape_artifacts(adapter, local_block, output_path):
    config = copy.deepcopy(local_block.config)
    config['runtime_info']['output']['harbor_tasks_dir']['path'] = output_path
    local_block.config_path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='artifacts'):
        adapter.load_config(local_block.config_path)


def test_config_rejects_output_escape_through_symlink(adapter, local_block, tmp_path):
    local_block.artifacts.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (local_block.artifacts / 'linked').symlink_to(outside, target_is_directory=True)
    config = copy.deepcopy(local_block.config)
    config['runtime_info']['output']['harbor_tasks_dir']['path'] = 'artifacts/linked/tasks'
    local_block.config_path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='artifacts'):
        adapter.load_config(local_block.config_path)


@pytest.mark.parametrize('container', ['object', 'list'])
def test_json_collection_deduplicates_and_limits_answered_seeds(
        adapter, local_block, monkeypatch, container):
    def question(qid, answer='answer', score=50):
        value = {'question_id': qid, 'title': f'question {qid}', 'body': 'question',
                 'score': score, 'tags': ['bash']}
        if answer is not None:
            value.update(accepted_answer_id=qid + 1000, accepted_answer={'body': answer})
        return value

    records = [question(1, None), question(2, score=1),
               question(3), question(3), question(4), question(5)]
    source_path = local_block.block / 'questions.json'
    source_path.write_text(json.dumps({'questions': records} if container == 'object' else records))
    source = {'type': 'json', 'path': str(source_path), 'max_questions': 2, 'min_score': 10}
    monkeypatch.setattr(adapter, 'so_request', lambda *args, **kwargs: pytest.fail('JSON collection used the network'))
    selected = adapter.collect_questions(source, {})
    assert [item['question_id'] for item in selected] == [3, 4]


def test_json_answer_id_without_body_does_not_consume_batch_quota(adapter, local_block):
    source_path = local_block.block / 'questions.json'
    source_path.write_text(json.dumps({'questions': [
        {'question_id': 1, 'score': 50, 'accepted_answer_id': 101},
        {'question_id': 2, 'score': 50, 'accepted_answer': {'body': 'usable answer'}},
        {'question_id': 3, 'score': 50, 'accepted_answer': {'body': 'another answer'}},
    ]}))
    selected = adapter.collect_questions(
        {'type': 'json', 'path': str(source_path), 'max_questions': 2, 'min_score': 10}, {})
    assert [item['question_id'] for item in selected] == [2, 3]


@pytest.mark.parametrize('data', [
    {'files': {None: 'contents'}, 'directories': []},
    {'files': {7: 'contents'}, 'directories': []},
    {'files': {}, 'directories': [None]},
    {'files': {}, 'directories': [7]},
    {'files': [], 'directories': []},
    None,
])
def test_generated_environment_rejects_non_string_paths(adapter, data):
    with pytest.raises(ValueError):
        adapter.safe_environment(data)


def test_generated_environment_size_limit_counts_utf8_bytes(adapter):
    with pytest.raises(ValueError, match='10 MB'):
        adapter.safe_environment({'files': {'task_file/large.txt': '\u754c' * 3_333_334},
                                  'directories': []})


def test_dashboard_snapshot_excludes_raw_config_credentials_and_errors(dashboard, local_block):
    local_block.artifacts.mkdir()
    state = {
        'run_id': 'fixture-run', 'status': 'failed', 'stage': 'validate-oracle',
        'source': 'tasks', 'total_candidates': 2, 'verified_tasks': [],
        'error': 'private error and stack trace',
        'config': {'api_key': 'fake-fixture-config-private-value'},
        'llm_api': {'api_key': 'fake-fixture-api-private-value', 'base_url': 'private.invalid'},
        'rejected': {'bad-task': 'private rejection details'},
        'token_usage': {'total_prompt_tokens': 123, 'total_completion_tokens': 45,
                        'total_tokens': 168, 'call_count': 2},
    }
    (local_block.artifacts / 'state.json').write_text(json.dumps(state))
    data = dashboard.snapshot(local_block.config_path)
    assert data['status'] == 'failed'
    assert data['rejected_count'] == 1
    assert data['token_usage']['total_tokens'] == 168
    encoded = json.dumps(data)
    for value in ['private error', 'private rejection', 'fake-fixture-config-private-value',
                  'fake-fixture-api-private-value', 'private.invalid']:
        assert value not in encoded
    assert not {'config', 'error', 'llm_api', 'rejected'} & set(data)


def test_dashboard_token_usage_cannot_smuggle_prompts_or_credentials(dashboard, local_block):
    local_block.artifacts.mkdir()
    (local_block.artifacts / 'state.json').write_text(json.dumps({
        'status': 'completed', 'token_usage': {
            'total_tokens': 12, 'call_count': 1,
            'records': [{'prompt': 'private prompt contents'}],
            'api_key': 'fake-fixture-nested-private-value',
            'error': 'private backend error',
        },
    }))
    snapshot = dashboard.snapshot(local_block.config_path)
    assert snapshot['token_usage']['total_tokens'] == 12
    encoded = json.dumps(snapshot)
    assert 'private prompt' not in encoded
    assert 'fake-fixture-nested-private-value' not in encoded
    assert 'private backend' not in encoded


def test_dashboard_render_escapes_task_and_status_values(dashboard, monkeypatch):
    attack = '\"><img src=x onerror="alert(1)"> & <script>bad()</script>'
    secret = 'fake-fixture-render-private-value'
    monkeypatch.setenv('TERMINAL_CURATOR_TEST_SECRET', secret)
    page = dashboard.render({'status': attack, 'stage': secret,
                             'published_tasks': [attack], 'run_id': attack})
    assert attack not in page
    assert '<img src=x' not in page
    assert '<script>bad()' not in page
    assert '&lt;img' in page and '&amp;' in page
    assert secret not in page


def test_dashboard_http_reads_live_state_and_serves_only_allowed_paths(
        dashboard, local_block, monkeypatch):
    local_block.artifacts.mkdir()
    state_path = local_block.artifacts / 'state.json'
    state_path.write_text(json.dumps({'status': 'running', 'stage': 'collect'}))
    ready = threading.Event()
    servers = []
    server_class = dashboard.ThreadingHTTPServer

    def create_server(address, handler):
        server = server_class(('127.0.0.1', 0), handler)
        servers.append(server)
        ready.set()
        return server

    monkeypatch.setattr(dashboard, 'ThreadingHTTPServer', create_server)
    monkeypatch.setattr(sys, 'argv', ['progress_monitor.py', '--config',
                                    str(local_block.config_path), '--serve', '--port', '0'])
    worker = threading.Thread(target=dashboard.main, daemon=True)
    worker.start()
    assert ready.wait(5), 'dashboard did not bind a loopback socket'
    server = servers[0]

    def get(path):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    try:
        status, headers, body = get('/api/status')
        assert status == 200
        assert headers['Cache-Control'] == 'no-store'
        assert headers['Content-Type'] == 'application/json'
        assert json.loads(body)['stage'] == 'collect'
        state_path.write_text(json.dumps({'status': 'completed', 'stage': 'completed',
                                         'error': 'private raw exception'}))
        status, _, body = get('/api/status')
        assert status == 200 and json.loads(body)['status'] == 'completed'
        assert b'private raw exception' not in body
        for path in ['/config.yaml', '/artifacts/state.json', '/../config.yaml',
                     '/.env', '/api/status?raw=1', '/%2e%2e/config.yaml']:
            status, _, _ = get(path)
            assert status == 404, path
        for path in ['/', '/index.html']:
            status, headers, body = get(path)
            assert status == 200 and headers['Content-Type'].startswith('text/html')
            assert b'private raw exception' not in body
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert not worker.is_alive()


def test_failed_concurrent_start_cannot_overwrite_running_pid(
        adapter, local_block, monkeypatch):
    local_block.artifacts.mkdir()
    pid = local_block.artifacts / 'run.pid'
    pid.write_text('424242')
    monkeypatch.setattr(sys, 'argv', ['terminal_curator.py', '--config',
                                    str(local_block.config_path), 'run'])
    monkeypatch.setattr(adapter, 'run', lambda config: pytest.fail('run bypassed its lock'))
    with (local_block.artifacts / 'run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert adapter.main() == 1
        assert pid.read_text() == '424242'


@pytest.mark.parametrize('fail', [False, True])
def test_run_pid_is_owned_during_work_and_removed_after_exit(
        adapter, local_block, monkeypatch, fail):
    monkeypatch.setattr(sys, 'argv', ['terminal_curator.py', '--config',
                                    str(local_block.config_path), 'run'])
    monkeypatch.setattr(adapter.signal, 'signal', lambda *args: None)

    def fake_run(config, run_id=None, config_path=None):
        assert (local_block.artifacts / 'run.pid').read_text() == str(os.getpid())
        process = json.loads((local_block.artifacts / 'run.process.json').read_text())
        assert process['run_id'] == run_id and process['pid'] == os.getpid()
        assert config_path == local_block.config_path
        if fail:
            raise ValueError('injected early run failure')
        return {'run_id': 'fixture-run', 'verified_tasks': ['fixture-task']}

    monkeypatch.setattr(adapter, 'run', fake_run)
    assert adapter.main() == (1 if fail else 0)
    assert not (local_block.artifacts / 'run.pid').exists()
    assert not (local_block.artifacts / 'run.process.json').exists()


def test_archive_preserves_effective_overlay_and_redacts_credentials(
        adapter, local_block, committed_repo, monkeypatch):
    monkeypatch.setattr(adapter, 'REPO', committed_repo.path)
    assert committed_repo.sha != adapter.PIN
    config = copy.deepcopy(local_block.config)
    config['runtime_info']['input']['llm_api'].update(
        api_key='fake-fixture-overlay-private-key', model='fixture-overlay-distinct-model',
        api_base_url='https://private-overlay.invalid/v1')
    config['runtime_info']['input']['stackoverflow']['api_key'] = 'fake-fixture-stack-private-key'
    local_block.config_path.write_text(yaml.safe_dump(config))
    adapter.archive(local_block.config_path, config, 0, '2026-10-20T00:00:00+00:00')
    archive = local_block.artifacts / 'archives/run_001'
    snapshot = yaml.safe_load((archive / 'config.yaml').read_text())
    assert snapshot['runtime_info']['input']['llm_api']['model'] == 'fixture-overlay-distinct-model'
    raw = (archive / 'config.yaml').read_text()
    for secret in ['fake-fixture-overlay-private-key', 'fake-fixture-stack-private-key', 'private-overlay.invalid']:
        assert secret not in raw
    metadata = yaml.safe_load((archive / 'metadata.yaml').read_text())
    assert metadata['repos']['terminal-lego'] == committed_repo.sha
    assert metadata['status'] == 'completed' and metadata['exit_code'] == 0
    assert (archive / 'scripts/start.sh').read_text().endswith('echo isolated-test-script\n')
    index = yaml.safe_load((local_block.artifacts / 'index.yaml').read_text())
    assert index['runs'][-1]['id'] == 'run_001'


def test_empty_uninitialized_repo_directories_do_not_record_the_parent_head(
        adapter, committed_repo, monkeypatch):
    terminal_repo = committed_repo.path / 'blocks/terminal-curator/repos/terminal-lego'
    harbor_repo = committed_repo.path / 'blocks/tracer/repos/harbor'
    terminal_repo.mkdir(parents=True)
    harbor_repo.mkdir(parents=True)
    assert not (terminal_repo / '.git').exists()
    assert not (harbor_repo / '.git').exists()
    # Git itself walks up from either empty directory to the enclosing repo.
    assert committed_repo.git('rev-parse', 'HEAD', directory=terminal_repo) == committed_repo.sha
    assert committed_repo.git('rev-parse', 'HEAD', directory=harbor_repo) == committed_repo.sha
    monkeypatch.setattr(adapter, 'ROOT', committed_repo.path)
    monkeypatch.setattr(adapter, 'REPO', terminal_repo)

    assert adapter.repo_commits() == {}


@pytest.mark.parametrize('report_started,expected_copy', [
    ('2020-01-01T00:00:00+00:00', False),
    ('2026-10-20T12:00:01+00:00', False),
])
def test_preflight_failure_archive_does_not_adopt_old_report(
        adapter, local_block, report_started, expected_copy):
    report_path = local_block.artifacts / 'reports/latest.json'
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps({'run_id': 'prior-or-current-run',
                                      'started_at': report_started, 'status': 'completed'}))
    adapter.archive(local_block.config_path, local_block.config, 23,
                    '2026-10-20T12:00:00+00:00')
    archive = local_block.artifacts / 'archives/run_001'
    assert (archive / 'report.json').exists() is expected_copy
    metadata = yaml.safe_load((archive / 'metadata.yaml').read_text())
    assert metadata['status'] == 'failed' and metadata['exit_code'] == 23
    assert json.loads(report_path.read_text())['started_at'] == report_started


def test_rejected_concurrent_archive_preserves_active_run_state(adapter, local_block):
    local_block.artifacts.mkdir()
    state_path = local_block.artifacts / 'state.json'
    state_path.write_text(json.dumps({
        'run_id': 'active-worker-run', 'status': 'running', 'stage': 'validate-oracle',
        'started_at': '2026-10-20T12:00:00+00:00', 'total_candidates': 2,
        'verified_tasks': [], 'rejected': {},
    }, indent=2))
    original = state_path.read_bytes()
    with (local_block.artifacts / 'run.lock').open('w') as active_lock:
        fcntl.flock(active_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        adapter.archive(local_block.config_path, local_block.config, 1,
                        '2026-10-20T12:01:00+00:00')
        assert state_path.read_bytes() == original
    index = yaml.safe_load((local_block.artifacts / 'index.yaml').read_text())
    assert index['runs'][-1]['status'] == 'failed'
    assert index['runs'][-1]['exit_code'] == 1


@pytest.mark.parametrize('exit_code,status', [(1, 'failed'), (130, 'interrupted'),
                                            (143, 'interrupted')])
def test_archive_finalizes_orphaned_running_state(adapter, local_block, exit_code, status):
    local_block.artifacts.mkdir()
    state_path = local_block.artifacts / 'state.json'
    state_path.write_text(json.dumps({
        'run_id': 'terminated-worker-run', 'status': 'running', 'stage': 'validate-nop',
        'started_at': '2026-10-20T12:00:01+00:00', 'total_candidates': 2,
        'verified_tasks': [], 'rejected': {'fixture-task': 'recorded verdict'},
        'updated_at': '2026-10-20T12:00:02+00:00',
    }))
    adapter.archive(local_block.config_path, local_block.config, exit_code,
                    '2026-10-20T12:00:00+00:00', run_id='terminated-worker-run')
    state = json.loads(state_path.read_text())
    assert state['status'] == status and state['stage'] == status
    assert state['run_id'] == 'terminated-worker-run'
    assert state['started_at'] == '2026-10-20T12:00:01+00:00'
    assert state['total_candidates'] == 2
    assert state['rejected'] == {'fixture-task': 'recorded verdict'}
    assert state['updated_at'] != '2026-10-20T12:00:02+00:00'


def test_clean_flags_preserve_published_versions_until_explicit_all(
        adapter, local_block, fixture_tasks, monkeypatch):
    scripts = local_block.block / 'scripts'
    for name in ['common.sh', 'clean.sh']:
        shutil.copy2(BLOCK / 'scripts' / name, scripts / name)
    monkeypatch.setenv('TERMINAL_CURATOR_PYTHON', sys.executable)
    pool = local_block.artifacts / 'tasks'
    adapter.publish_tasks([fixture_tasks / 'terminal-jsonl-summary'], pool)
    first_batch = pool.resolve(strict=True)
    adapter.publish_tasks([fixture_tasks / 'terminal-csv-reconcile'], pool)
    second_batch = pool.resolve(strict=True)
    temporary = local_block.artifacts / 'runs/temporary/candidate.json'
    temporary.parent.mkdir(parents=True)
    temporary.write_text('{}')
    keep = [local_block.artifacts / 'env/keep', local_block.artifacts / 'archives/keep']
    for path in keep:
        path.parent.mkdir(exist_ok=True)
        path.write_text('keep')

    def clean(*arguments):
        result = subprocess.run(['bash', str(scripts / 'clean.sh'), *arguments],
                                text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        return result

    assert 'Would remove' in clean('--dry-run').stdout
    assert temporary.exists()
    clean()
    assert not temporary.exists()
    assert pool.resolve(strict=True) == second_batch
    assert first_batch.is_dir() and second_batch.is_dir()
    assert all(path.exists() for path in keep)
    assert 'Would remove' in clean('--all', '--yes', '--dry-run').stdout
    assert first_batch.is_dir() and second_batch.is_dir()
    clean('--all', '--yes')
    assert not pool.is_symlink()
    assert not first_batch.exists() and not second_batch.exists()
    assert not any(path.exists() for path in keep)


def test_stop_refuses_a_pid_owned_by_an_unrelated_local_process(local_block, monkeypatch):
    scripts = local_block.block / 'scripts'
    for name in ['common.sh', 'stop.sh']:
        shutil.copy2(BLOCK / 'scripts' / name, scripts / name)
    monkeypatch.setenv('TERMINAL_CURATOR_PYTHON', sys.executable)
    local_block.artifacts.mkdir()
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        (local_block.artifacts / 'run.pid').write_text(str(unrelated.pid))
        result = subprocess.run(['bash', str(scripts / 'stop.sh')],
                                text=True, capture_output=True, timeout=10)
        assert result.returncode != 0
        assert 'refusing to signal' in result.stderr
        assert unrelated.poll() is None, 'stop.sh signaled a process it does not own'
    finally:
        if unrelated.poll() is None:
            unrelated.terminate()
        unrelated.wait(timeout=5)


@pytest.mark.parametrize('later_run_starts,already_exited', [(False, False), (True, False), (False, True)])
def test_stop_removes_only_current_run_compose_containers_and_networks(
        local_block, tmp_path, monkeypatch, later_run_starts, already_exited):
    scripts = local_block.block / 'scripts'
    for name in ['common.sh', 'stop.sh']:
        shutil.copy2(BLOCK / 'scripts' / name, scripts / name)
    monkeypatch.setenv('TERMINAL_CURATOR_PYTHON', sys.executable)
    local_block.artifacts.mkdir()
    run_id = 'owned-offline-run'
    trial_name = 'terminal-jsonl-summary__OwnedTest'
    owned_project = trial_name.lower()
    unrelated_project = owned_project + '-other'
    for run, trial in [(run_id, trial_name), ('previous-run', unrelated_project)]:
        config = local_block.artifacts / 'runs' / run / 'validation/oracle' / trial / 'config.json'
        config.parent.mkdir(parents=True)
        config.write_text('{}')
    (local_block.artifacts / 'state.json').write_text(json.dumps({'run_id': run_id, 'status': 'running'}))

    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    docker_log = tmp_path / 'fake-docker-calls.jsonl'
    fake_docker = fake_bin / 'docker'
    fake_docker.write_text(f'#!{sys.executable}\n' + r'''
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\n')
owned = os.environ['FAKE_OWNED_PROJECT']
other = owned + '-other'
if args[:1] == ['ps']:
    print('a11111111111\t' + owned)
    print('b22222222222\t' + other)
elif args[:2] == ['network', 'ls']:
    print('c33333333333\t' + owned)
    print('d44444444444\t' + other)
elif args[:1] == ['rm'] or args[:2] == ['network', 'rm']:
    pass
else:
    raise SystemExit('unsupported fake docker command')
''')
    fake_docker.chmod(0o755)
    monkeypatch.setenv('PATH', str(fake_bin) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('FAKE_DOCKER_CALLS', str(docker_log))
    monkeypatch.setenv('FAKE_OWNED_PROJECT', owned_project)

    ready = tmp_path / 'owned-run-ready'
    runner = scripts / 'terminal_curator.py'
    changed_state = str(local_block.artifacts / 'state.json')
    handler = ('import signal, json\n'
               'def stopped(*args):\n'
               f'    Path({changed_state!r}).write_text(json.dumps({{"run_id":"previous-run","status":"running"}}))\n'
               '    raise SystemExit(0)\n'
               'signal.signal(signal.SIGTERM,stopped)\n') if later_run_starts else ''
    runner.write_text('import sys, time\nfrom pathlib import Path\n' + handler +
                      'Path(sys.argv[2]).write_text("ready")\n'
                      'while True: time.sleep(1)\n')
    owned = subprocess.Popen([sys.executable, str(runner), 'run', str(ready)])
    reaper = threading.Thread(target=owned.wait, daemon=True)
    reaper.start()
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), 'isolated owned run did not start'
        (local_block.artifacts / 'run.pid').write_text(str(owned.pid))
        ticks = Path(f'/proc/{owned.pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
        (local_block.artifacts / 'run.process.json').write_text(json.dumps(
            {'pid': owned.pid, 'start_ticks': ticks, 'run_id': run_id}))
        if already_exited:
            owned.terminate()
            reaper.join(timeout=5)
            assert owned.poll() is not None
        result = subprocess.run(['bash', str(scripts / 'stop.sh')],
                                text=True, capture_output=True, timeout=25)
        assert result.returncode == 0, result.stderr
        reaper.join(timeout=5)
        assert owned.poll() is not None, 'stop.sh did not terminate its owned run'
        assert docker_log.exists(), 'stop.sh did not inspect Docker sidecars'
        calls = [json.loads(line) for line in docker_log.read_text().splitlines()]
        assert any(call[:1] == ['ps'] for call in calls)
        assert any(call[:2] == ['network', 'ls'] for call in calls)
        container_removals = [call for call in calls if call[:1] == ['rm']]
        network_removals = [call for call in calls if call[:2] == ['network', 'rm']]
        assert len(container_removals) == len(network_removals) == 1
        assert [arg for arg in container_removals[0][1:] if not arg.startswith('-')] == ['a11111111111']
        assert [arg for arg in network_removals[0][2:] if not arg.startswith('-')] == ['c33333333333']
        assert all('b22222222222' not in call and 'd44444444444' not in call
                   for call in calls)
        assert not any('prune' in call or 'rmi' in call or 'image' in call for call in calls)
        assert 'b22222222222' not in result.stdout + result.stderr
        assert 'd44444444444' not in result.stdout + result.stderr
    finally:
        if owned.poll() is None:
            owned.terminate()
        owned.wait(timeout=5)
        reaper.join(timeout=5)
