"""Regressions for run identity, frozen inputs, and lifecycle failure handling."""

import copy
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import yaml

from conftest import BLOCK


@pytest.fixture
def lifecycle(adapter, tmp_path, monkeypatch):
    block = tmp_path / 'root/blocks/terminal-curator'
    scripts = block / 'scripts'
    scripts.mkdir(parents=True)
    (scripts / 'example.sh').write_text('exit 0\n')
    config = yaml.safe_load((BLOCK / 'config.yaml').read_text())
    config['runtime_info']['input']['source'].update(type='tasks', path='input')
    (block / 'input').mkdir()
    profile = block / 'config.local.yaml'
    profile.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(adapter, 'BLOCK', block)
    monkeypatch.setattr(adapter, 'ARTIFACTS', block / 'artifacts')
    monkeypatch.setenv('TERMINAL_CURATOR_PYTHON', sys.executable)
    return SimpleNamespace(block=block, artifacts=block / 'artifacts', scripts=scripts,
                           config=config, profile=profile)


def test_archive_copies_its_exact_report_and_frozen_effective_configuration(adapter, lifecycle):
    started = '2026-01-01T00:00:00+00:00'
    run = lifecycle.artifacts / 'runs/first-run'
    config = copy.deepcopy(lifecycle.config)
    config['runtime_info']['input']['llm_api'].update(
        model='original-model', api_key='fake-fixture-original-config-key')
    adapter.snapshot_config(config, run / 'config.yaml')
    report = {'run_id': 'first-run', 'status': 'completed', 'started_at': started,
              'verified_tasks': ['task-one'], 'rejected': {}, 'repos': {'terminal-lego': 'a' * 40}}
    adapter.write_json(run / 'report.json', report)
    edited = copy.deepcopy(config)
    edited['runtime_info']['input']['llm_api']['model'] = 'edited-after-launch'
    lifecycle.profile.write_text(yaml.safe_dump(edited))
    adapter.write_json(lifecycle.artifacts / 'reports/latest.json',
                       {**report, 'run_id': 'later-run', 'verified_tasks': ['different-task']})
    adapter.archive(lifecycle.profile, edited, 0, started, run_id='first-run')
    archived = lifecycle.artifacts / 'archives/run_001'
    assert json.loads((archived / 'report.json').read_text()) == report
    text = (archived / 'config.yaml').read_text()
    assert yaml.safe_load(text)['runtime_info']['input']['llm_api']['model'] == 'original-model'
    assert 'fake-fixture-original-config-key' not in text
    metadata = yaml.safe_load((archived / 'metadata.yaml').read_text())
    assert metadata['run_id'] == 'first-run' and metadata['verified_count'] == 1
    assert metadata['repos'] == report['repos']


def test_archived_code_is_frozen_and_does_not_copy_private_profiles(adapter, lifecycle):
    config = copy.deepcopy(lifecycle.config)
    secret = 'fake-fixture-private-profile-secret'
    config['runtime_info']['input']['llm_api']['api_key'] = secret
    # --config accepts any file name; even a script-like suffix is excluded.
    selected = lifecycle.scripts / 'selected-profile.py'
    selected.write_text(yaml.safe_dump(config))
    (lifecycle.scripts / 'private-profile.local.yaml').write_text(yaml.safe_dump(config))
    (lifecycle.scripts / '.env').write_text('PRIVATE_VALUE=' + secret)
    (lifecycle.scripts / 'profile-link.py').symlink_to(selected)
    code = lifecycle.scripts / 'example.sh'
    code.write_text('echo original-code\n')
    run = lifecycle.artifacts / 'runs/frozen-code'
    adapter.snapshot_run_inputs(config, run, selected)
    code.write_text('echo modified-after-start\n')
    adapter.archive(selected, config, 0, '2026-01-01T00:00:00Z', run_id='frozen-code')
    archived = lifecycle.artifacts / 'archives/run_001'
    assert (archived / 'scripts/example.sh').read_text() == 'echo original-code\n'
    assert not (archived / 'scripts/selected-profile.py').exists()
    for path in archived.rglob('*'):
        assert not path.is_symlink()
        if path.is_file():
            assert secret not in path.read_text()


@pytest.mark.parametrize('linked', [False, True])
def test_setup_does_not_install_into_an_existing_nonvirtual_terminal_environment(
        lifecycle, tmp_path, monkeypatch, linked):
    for name in ('common.sh', 'setup.sh'):
        shutil.copy2(BLOCK / 'scripts' / name, lifecycle.scripts / name)
    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    calls = tmp_path / 'uv-calls.txt'
    uv = fake_bin / 'uv'
    uv.write_text('#!/bin/bash\nprintf called >> "$FAKE_UV_CALLS"\n')
    uv.chmod(0o755)
    monkeypatch.setenv('FAKE_UV_CALLS', str(calls))
    monkeypatch.setenv('PATH', str(fake_bin) + os.pathsep + os.environ['PATH'])
    expected = lifecycle.artifacts / 'env/terminal-curator'
    target = tmp_path / 'foreign-system' if linked else expected
    (target / 'bin').mkdir(parents=True)
    (target / 'bin/python').symlink_to(sys.executable)
    sentinel = target / 'keep.txt'
    sentinel.write_text('owned elsewhere')
    if linked:
        expected.parent.mkdir(parents=True)
        expected.symlink_to(target, target_is_directory=True)
    result = subprocess.run(['bash', str(lifecycle.scripts / 'setup.sh'), '--config', str(lifecycle.profile)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert not calls.exists(), 'setup invoked uv before checking its target environment'
    assert sentinel.read_text() == 'owned elsewhere'


def test_archive_counter_respects_manual_index_records(adapter, lifecycle):
    lifecycle.artifacts.mkdir()
    (lifecycle.artifacts / 'index.yaml').write_text(yaml.safe_dump(
        {'runs': [{'id': 'run_007', 'status': 'completed', 'notes': 'retained manual entry'}]}))
    adapter.archive(lifecycle.profile, lifecycle.config, 0, '2026-01-01T00:00:00Z')
    index = yaml.safe_load((lifecycle.artifacts / 'index.yaml').read_text())
    assert [entry['id'] for entry in index['runs']] == ['run_007', 'run_008']
    assert index['runs'][0]['notes'] == 'retained manual entry'


def test_concurrent_archives_keep_every_record_with_unique_ids(adapter, lifecycle):
    def archive(number):
        adapter.archive(lifecycle.profile, lifecycle.config, 0,
                        '2026-01-01T00:00:00Z', run_id=f'run-{number}')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(archive, range(8)))
    index = yaml.safe_load((lifecycle.artifacts / 'index.yaml').read_text())
    assert len({entry['id'] for entry in index['runs']}) == 8
    assert {entry['run_id'] for entry in index['runs']} == {f'run-{i}' for i in range(8)}
    assert len(list((lifecycle.artifacts / 'archives').glob('run_*/metadata.yaml'))) == 8


def test_older_failed_exit_does_not_finalize_a_later_run(adapter, lifecycle):
    state = {'run_id': 'new-run', 'started_at': '2026-01-01T00:01:00Z',
             'status': 'running', 'stage': 'generate'}
    adapter.write_json(lifecycle.artifacts / 'state.json', state)
    adapter.archive(lifecycle.profile, lifecycle.config, 1,
                    '2026-01-01T00:00:00Z', run_id='old-run')
    assert json.loads((lifecycle.artifacts / 'state.json').read_text()) == state


def test_malformed_configuration_is_archived_without_echoing_private_source(
        adapter, lifecycle, monkeypatch, capsys):
    secret = 'fake-fixture-sensitive-yaml-value'
    lifecycle.profile.write_text('meta_info: {api_key: "' + secret + '}\n')
    monkeypatch.setattr(sys, 'argv', ['terminal_curator.py', '--config', str(lifecycle.profile), 'check'])
    assert adapter.main() == 1
    captured = capsys.readouterr()
    assert secret not in captured.err + captured.out
    monkeypatch.setattr(sys, 'argv', ['terminal_curator.py', '--config', str(lifecycle.profile),
                                    'archive', '--exit-code', '1', '--run-id', 'failed-preflight'])
    assert adapter.main() == 0
    metadata = yaml.safe_load((lifecycle.artifacts / 'archives/run_001/metadata.yaml').read_text())
    assert metadata['status'] == 'failed' and metadata['config_snapshot'] == 'unavailable'
    assert not (lifecycle.artifacts / 'archives/run_001/config.yaml').exists()


@pytest.mark.parametrize('run_id', ['../outside', '/absolute', 'bad space', '.'])
def test_archive_cannot_read_evidence_outside_run_directory(adapter, lifecycle, run_id):
    with pytest.raises(ValueError, match='run ID'):
        adapter.archive(lifecycle.profile, lifecycle.config, 0, '2026-01-01T00:00:00Z', run_id=run_id)
    assert not (lifecycle.artifacts / 'archives').exists()


def test_environment_profile_path_is_block_relative_from_any_cwd(lifecycle, tmp_path, monkeypatch):
    shutil.copy2(BLOCK / 'scripts/common.sh', lifecycle.scripts / 'common.sh')
    monkeypatch.setenv('TERMINAL_CURATOR_CONFIG', 'config.with spaces.local.yaml')
    result = subprocess.run(['bash', '-c', 'source "$1"; printf "%s" "$CONFIG"',
                             '_', str(lifecycle.scripts / 'common.sh')],
                            cwd=tmp_path, capture_output=True, text=True, check=True)
    assert result.stdout == str(lifecycle.block / 'config.with spaces.local.yaml')


def test_successful_worker_does_not_hide_archive_failure(lifecycle):
    for name in ('common.sh', 'start.sh', 'stop.sh'):
        shutil.copy2(BLOCK / 'scripts' / name, lifecycle.scripts / name)
    (lifecycle.scripts / 'dryrun.sh').write_text('exit 0\n')
    (lifecycle.scripts / 'terminal_curator.py').write_text(
        'import sys\nraise SystemExit(23 if "archive" in sys.argv else 0)\n')
    result = subprocess.run(['bash', str(lifecycle.scripts / 'start.sh')],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 23 and 'archive failed' in result.stderr


@pytest.mark.parametrize('worker_rc,cleanup_fails,expected_rc',
                         [(0, False, 0), (17, False, 17), (0, True, 1)])
def test_launcher_recovers_sidecars_after_worker_exit(
        lifecycle, tmp_path, monkeypatch, worker_rc, cleanup_fails, expected_rc):
    """A reaped worker has dropped process metadata but still owns trial sidecars."""
    for name in ('common.sh', 'start.sh', 'stop.sh'):
        shutil.copy2(BLOCK / 'scripts' / name, lifecycle.scripts / name)
    (lifecycle.scripts / 'dryrun.sh').write_text('exit 0\n')
    (lifecycle.scripts / 'terminal_curator.py').write_text('''
import json, os, sys
from pathlib import Path
artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
run_id = sys.argv[sys.argv.index('--run-id') + 1]
if 'archive' in sys.argv:
    (artifacts / 'archive-result.json').write_text(json.dumps({
        'run_id': run_id, 'exit_code': int(sys.argv[sys.argv.index('--exit-code') + 1]),
        'cleanup_before_archive': Path(os.environ['FAKE_DOCKER_CALLS']).is_file()}))
    raise SystemExit(0)
trial = artifacts / 'runs' / run_id / 'validation/oracle/owned-trial/config.json'
trial.parent.mkdir(parents=True)
trial.write_text('{}')
other = artifacts / 'runs/unrelated-run/validation/oracle/unrelated-trial/config.json'
other.parent.mkdir(parents=True)
other.write_text('{}')
(artifacts / 'state.json').write_text(json.dumps({'run_id': 'unrelated-run'}))
(artifacts / 'worker-result.json').write_text(json.dumps({'run_id': run_id, 'pid': os.getpid()}))
(artifacts / 'run.pid').write_text(str(os.getpid()))
(artifacts / 'run.process.json').write_text(json.dumps({'pid': os.getpid(), 'run_id': run_id}))
# Match the real worker's finally: process records are gone before start's EXIT.
(artifacts / 'run.pid').unlink()
(artifacts / 'run.process.json').unlink()
raise SystemExit(int(os.environ['FAKE_WORKER_RC']))
''')
    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    docker_log = tmp_path / 'docker-calls.jsonl'
    docker = fake_bin / 'docker'
    docker.write_text(f'#!{sys.executable}\n' + '''
import json, os, sys
args = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:1] == ['ps']:
    print('a11111111111\\towned-trial')
    print('b22222222222\\tunrelated-trial')
elif args[:2] == ['network', 'ls']:
    print('c33333333333\\towned-trial')
    print('d44444444444\\tunrelated-trial')
elif args[:1] == ['rm'] or args[:2] == ['network', 'rm']:
    raise SystemExit(31 if os.environ['FAKE_CLEANUP_FAILS'] == '1' else 0)
else:
    raise SystemExit('unexpected Docker command')
''')
    docker.chmod(0o755)
    monkeypatch.setenv('PATH', str(fake_bin) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('FAKE_DOCKER_CALLS', str(docker_log))
    monkeypatch.setenv('FAKE_WORKER_RC', str(worker_rc))
    monkeypatch.setenv('FAKE_CLEANUP_FAILS', '1' if cleanup_fails else '0')
    result = subprocess.run(['bash', str(lifecycle.scripts / 'start.sh')],
                            text=True, capture_output=True, timeout=15)
    assert docker_log.exists(), 'EXIT skipped sidecar recovery after reaping its worker'
    calls = [json.loads(line) for line in docker_log.read_text().splitlines()]
    assert ['rm', '-f', 'a11111111111'] in calls
    assert ['network', 'rm', 'c33333333333'] in calls
    assert all('b22222222222' not in call and 'd44444444444' not in call for call in calls)
    assert result.returncode == expected_rc, result.stderr
    worker = json.loads((lifecycle.artifacts / 'worker-result.json').read_text())
    archived = json.loads((lifecycle.artifacts / 'archive-result.json').read_text())
    assert archived == {'run_id': worker['run_id'], 'exit_code': expected_rc,
                        'cleanup_before_archive': True}
    assert not Path(f'/proc/{worker["pid"]}').exists()
    assert not (lifecycle.artifacts / 'run.process.json').exists()
    if cleanup_fails:
        assert 'sidecar cleanup failed' in result.stderr


@pytest.mark.parametrize('active_lock,reason', [('archive.lock', 'active archive'),
                                              ('launch.lock', 'active launcher')])
def test_clean_refuses_to_remove_evidence_during_archiving(lifecycle, active_lock, reason):
    for name in ('common.sh', 'clean.sh'):
        shutil.copy2(BLOCK / 'scripts' / name, lifecycle.scripts / name)
    evidence = lifecycle.artifacts / 'runs/retained/report.json'
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{}')
    with (lifecycle.artifacts / active_lock).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(['bash', str(lifecycle.scripts / 'clean.sh')],
                                capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and reason in result.stderr
    assert evidence.exists()


def test_stop_refuses_reused_pid_with_stale_start_identity(lifecycle):
    for name in ('common.sh', 'stop.sh'):
        shutil.copy2(BLOCK / 'scripts' / name, lifecycle.scripts / name)
    lifecycle.artifacts.mkdir()
    runner = lifecycle.scripts / 'terminal_curator.py'
    runner.write_text('import time\ntime.sleep(30)\n')
    child = subprocess.Popen([sys.executable, str(runner), 'run'])
    try:
        (lifecycle.artifacts / 'run.pid').write_text(str(child.pid))
        (lifecycle.artifacts / 'run.process.json').write_text(json.dumps(
            {'pid': child.pid, 'start_ticks': '0', 'run_id': 'old-worker'}))
        result = subprocess.run(['bash', str(lifecycle.scripts / 'stop.sh')],
                                capture_output=True, text=True, timeout=5)
        assert result.returncode != 0 and 'stale process identity' in result.stderr
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_launcher_interrupt_bounds_a_worker_that_ignores_term(lifecycle):
    for name in ('common.sh', 'start.sh', 'stop.sh'):
        shutil.copy2(BLOCK / 'scripts' / name, lifecycle.scripts / name)
    (lifecycle.scripts / 'dryrun.sh').write_text('exit 0\n')
    (lifecycle.scripts / 'terminal_curator.py').write_text('''
import json, os, signal, sys, time
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'artifacts'
if 'archive' in sys.argv: raise SystemExit(0)
signal.signal(signal.SIGTERM,signal.SIG_IGN)
run_id=sys.argv[sys.argv.index('--run-id')+1]
ticks=Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')',1)[1].split()[19]
(root/'run.process.json').write_text(json.dumps({'pid':os.getpid(),'start_ticks':ticks,'run_id':run_id}))
(root/'run.pid').write_text(str(os.getpid()))
while True: time.sleep(1)
''')
    launcher = subprocess.Popen(['bash', str(lifecycle.scripts / 'start.sh')],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    worker_pid = None
    try:
        deadline = time.monotonic() + 5
        while not (lifecycle.artifacts / 'run.pid').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        worker_pid = int((lifecycle.artifacts / 'run.pid').read_text())
        launcher.terminate()
        _, error = launcher.communicate(timeout=18)
        assert launcher.returncode == 143, error
        assert not Path(f'/proc/{worker_pid}').exists()
    finally:
        if launcher.poll() is None:
            launcher.kill()
            launcher.communicate(timeout=3)
        if worker_pid is not None and Path(f'/proc/{worker_pid}').exists():
            os.kill(worker_pid, 9)
