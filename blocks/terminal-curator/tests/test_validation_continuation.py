"""Batch interruption must not discard completed per-task validation evidence."""

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from test_core_review import core_case, fake_checksum_rpc, install_fake_runtime


def write_trial(job, name, checksum, reward, *, exit_code=0):
    trial = job / (name + '__fixture')
    (trial / 'agent').mkdir(parents=True)
    (trial / 'verifier').mkdir()
    (trial / 'agent/exit-code.txt').write_text(str(exit_code))
    (trial / 'result.json').write_text(json.dumps({
        'task_name': name, 'task_checksum': checksum, 'exception_info': None,
        'verifier_result': {'rewards': {'reward': reward}},
    }))
    failure = '<failure message="output missing"/>' if reward == 0 else ''
    (trial / 'verifier/junit.xml').write_text(
        f'<testsuite tests="1" failures="{int(reward == 0)}" errors="0" skipped="0">'
        f'<testcase name="test_output">{failure}</testcase></testsuite>')
    return trial


@pytest.mark.parametrize('ending', ['completed', 'timeout', 'nonzero'])
def test_nop_automatically_selects_full_oracle_gate(adapter, core_case, monkeypatch, ending):
    case = core_case
    names = ['bad-exit', 'unfinished', 'bad-reward', 'partial-json']
    for name in names:
        shutil.copytree(case.source, case.source.with_name(name))
    case.config['runtime_info']['input']['source']['max_questions'] = 5
    install_fake_runtime(adapter, monkeypatch)
    calls = []

    def harbor(config, pool, run_dir, agent):
        calls.append(agent)
        assert config['runtime_info']['input']['validation']['job_timeout_sec'] == 1800
        assert config['runtime_info']['input']['validation']['n_concurrent'] == 2
        hashes = fake_checksum_rpc(config, pool)
        job = run_dir / 'validation' / agent
        if agent == 'oracle':
            write_trial(job, 'task-one', hashes['task-one'], 1)
            write_trial(job, 'bad-exit', hashes['bad-exit'], 1, exit_code=1)
            write_trial(job, 'bad-reward', hashes['bad-reward'], 0)
            partial = job / 'partial-json__fixture'
            partial.mkdir()
            (partial / 'result.json').write_text('{"task_name":')
            if ending == 'timeout':
                raise subprocess.TimeoutExpired('harbor oracle', 1800)
            if ending == 'nonzero':
                raise adapter.HarborJobError('Harbor oracle job exited 1')
        else:
            assert sorted(hashes) == ['task-one']
            original = run_dir / 'normalized/task-one'
            assert adapter.task_tree_fingerprint(pool / 'task-one') == adapter.task_tree_fingerprint(original)
            write_trial(job, 'task-one', hashes['task-one'], 0)
        return job

    monkeypatch.setattr(adapter, 'run_harbor', harbor)
    monkeypatch.setattr(adapter, 'cleanup_harbor_job', lambda job: calls.append('cleanup-' + job.name))
    report = adapter.run(case.load(), run_id='automatic-continuation')
    expected = ['oracle', 'nop'] if ending == 'completed' else ['oracle', 'cleanup-oracle', 'nop']
    assert calls == expected
    assert report['status'] == 'completed'
    assert report['verified_tasks'] == ['task-one']
    assert set(report['rejected']) == set(names)
    assert set(report['validation']['nop']) == {'task-one'}
    assert report['job_status']['oracle']['status'] == {
        'completed': 'completed', 'timeout': 'timed_out', 'nonzero': 'failed'}[ending]
    assert report['validation']['oracle']['unfinished']['passed'] is False
    assert report['validation']['oracle']['partial-json']['passed'] is False
    assert adapter._publication.verify_publication(case.pool)['verified_tasks'] == ['task-one']


def test_oracle_timeout_without_passing_tasks_skips_nop(adapter, core_case, monkeypatch):
    install_fake_runtime(adapter, monkeypatch)
    calls = []

    def timeout(config, pool, run_dir, agent):
        calls.append(agent)
        raise subprocess.TimeoutExpired('harbor oracle', 1800)

    monkeypatch.setattr(adapter, 'run_harbor', timeout)
    with pytest.raises(ValueError, match='no candidates passed'):
        adapter.run(core_case.load(), run_id='no-completed-trials')
    assert calls == ['oracle']
    report = json.loads((core_case.block / 'artifacts/runs/no-completed-trials/report.json').read_text())
    assert report['job_status']['nop']['status'] == 'skipped'
    assert report['jobs']['oracle'].endswith('/validation/oracle')
    assert report['validation']['oracle']['task-one']['passed'] is False
    assert not core_case.pool.exists()


@pytest.mark.parametrize('change', ['instruction', 'permissions'])
def test_oracle_timeout_does_not_hide_changed_task_tree(adapter, core_case, monkeypatch, change):
    install_fake_runtime(adapter, monkeypatch)
    harbor = adapter.run_harbor
    calls = []

    def mutate(config, pool, run_dir, agent):
        calls.append(agent)
        harbor(config, pool, run_dir, agent)
        target = pool / 'task-one/instruction.md'
        if change == 'instruction':
            target.write_text('changed after Oracle')
        else:
            target.chmod(target.stat().st_mode ^ 0o100)
        raise subprocess.TimeoutExpired('harbor oracle', 1800)

    monkeypatch.setattr(adapter, 'run_harbor', mutate)
    with pytest.raises(ValueError, match='changed during Harbor validation'):
        adapter.run(core_case.load())
    assert calls == ['oracle']
    assert not core_case.pool.exists()


def test_nop_timeout_keeps_only_completed_valid_trials(adapter, core_case, monkeypatch):
    shutil.copytree(core_case.source, core_case.source.with_name('unfinished'))
    core_case.config['runtime_info']['input']['source']['max_questions'] = 2
    install_fake_runtime(adapter, monkeypatch)
    harbor = adapter.run_harbor

    def timeout(config, pool, run_dir, agent):
        if agent == 'oracle':
            return harbor(config, pool, run_dir, agent)
        job = run_dir / 'validation/nop'
        write_trial(job, 'task-one', fake_checksum_rpc(config, pool)['task-one'], 0)
        raise subprocess.TimeoutExpired('harbor nop', 1800)

    monkeypatch.setattr(adapter, 'run_harbor', timeout)
    report = adapter.run(core_case.load())
    assert report['verified_tasks'] == ['task-one']
    assert report['job_status']['nop']['status'] == 'timed_out'
    assert report['job_status']['nop']['cleanup'] == 'completed'
    assert report['validation']['nop']['unfinished']['passed'] is False


def test_cleanup_failure_preserves_evidence_and_stops_next_job(adapter, core_case, monkeypatch):
    install_fake_runtime(adapter, monkeypatch)
    harbor = adapter.run_harbor
    calls = []

    def timeout(config, pool, run_dir, agent):
        calls.append(agent)
        harbor(config, pool, run_dir, agent)
        raise subprocess.TimeoutExpired('harbor oracle', 1800)

    def failed_cleanup(job):
        raise subprocess.TimeoutExpired('docker ps --filter owned-project', 30)

    monkeypatch.setattr(adapter, 'run_harbor', timeout)
    monkeypatch.setattr(adapter, 'cleanup_harbor_job', failed_cleanup)
    with pytest.raises(subprocess.TimeoutExpired):
        adapter.run(core_case.load(), run_id='cleanup-failed')
    report = json.loads((core_case.block / 'artifacts/runs/cleanup-failed/report.json').read_text())
    assert calls == ['oracle']
    assert report['validation']['oracle']['task-one']['passed']
    assert report['job_status']['oracle']['cleanup'] == 'failed'
    assert not core_case.pool.exists()


def test_stopped_job_cleanup_removes_only_exact_owned_labels(adapter, tmp_path, monkeypatch):
    job = tmp_path / 'oracle'
    trial = job / 'task-one__trial'
    trial.mkdir(parents=True)
    (trial / 'config.json').write_text('{}')
    calls = []

    def docker(command, **kwargs):
        calls.append(command)
        assert kwargs['timeout'] == 30
        if '--filter' in command:
            assert 'label=com.docker.compose.project=task-one__trial' in command
            return SimpleNamespace(stdout='abc123\ttask-one__trial\ndef456\tforeign-project\n')
        assert command[-1] == 'abc123'
        assert 'def456' not in command
        return SimpleNamespace(stdout='')

    monkeypatch.setattr(adapter.subprocess, 'run', docker)
    adapter.cleanup_harbor_job(job)
    assert calls[1] == ['docker', 'rm', '-f', 'abc123']
    assert calls[3] == ['docker', 'network', 'rm', 'abc123']


def test_missing_job_has_no_docker_cleanup_calls(adapter, tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('no recorded Compose projects; Docker must not be contacted')

    monkeypatch.setattr(adapter.subprocess, 'run', unexpected)
    adapter.cleanup_harbor_job(tmp_path / 'missing')
