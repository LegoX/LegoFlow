"""Offline regressions for boundaries that could publish unusable tasks."""

import json
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


def trial(job, name='terminal-jsonl-summary', *, reward=1.0,
          exception=None, tests=3, failures=0, errors=0, exit_code='0', suffix='one'):
    directory = job / f'{name}__{suffix}'
    (directory / 'verifier').mkdir(parents=True)
    (directory / 'agent').mkdir()
    (directory / 'result.json').write_text(json.dumps({
        'task_name': name,
        'exception_info': exception,
        'verifier_result': {'rewards': {'reward': reward}},
    }))
    suites = ET.Element('testsuites')
    suite = ET.SubElement(suites, 'testsuite', tests=str(tests),
                          failures=str(failures), errors=str(errors))
    for index in range(tests):
        case = ET.SubElement(suite, 'testcase', name=f'test_{index}', time='0.001')
        if index < failures:
            ET.SubElement(case, 'failure', message='required output missing')
        if index < errors:
            ET.SubElement(case, 'error', message='verifier import failed')
    ET.ElementTree(suites).write(directory / 'verifier/junit.xml', encoding='utf-8')
    if exit_code is not None:
        (directory / 'agent/exit-code.txt').write_text(exit_code)
    return directory


def test_safe_environment_accepts_normal_data(adapter):
    data = {'files': {'task_file/input/data.json': '{"value":1}\n'},
            'directories': ['task_file/input', 'task_file/output']}
    adapter.safe_environment(data)


@pytest.mark.parametrize('path', [
    '/tmp/escape', '../escape', 'task_file/../../escape',
    'task_file/../escape', 'Dockerfile', './Dockerfile',
])
def test_safe_environment_rejects_file_escapes_and_reserved_files(adapter, path):
    with pytest.raises((ValueError, RuntimeError)):
        adapter.safe_environment({'files': {path: 'payload'}, 'directories': []})


@pytest.mark.parametrize('path', ['/tmp/escape', '../escape', 'task_file/../escape'])
def test_safe_environment_rejects_directory_escapes(adapter, path):
    with pytest.raises((ValueError, RuntimeError)):
        adapter.safe_environment({'files': {}, 'directories': [path]})


def test_normalize_installs_real_pytest_gate_and_preserves_source(
        adapter, fixture_tasks, tmp_path):
    source = fixture_tasks / 'terminal-jsonl-summary'
    before = {str(p.relative_to(source)): p.read_bytes()
              for p in source.rglob('*') if p.is_file()}
    target = adapter.normalize_task(source, tmp_path / 'candidate', {
        'source_type': 'hand-authored-offline-fixture',
        'source_id': 'terminal-jsonl-summary',
    })
    assert isinstance(target, Path) and target.is_dir()
    config = tomllib.loads((target / 'task.toml').read_text())
    assert 'schema_version' in config
    assert 'memory_mb' in config['environment']
    script = (target / 'tests/test.sh').read_text()
    assert '/tests/test_outputs.py' in script
    assert '--junitxml=/logs/verifier/junit.xml' in script
    assert '/logs/verifier/reward.txt' in script
    assert 'pytest' in script
    assert list((target / 'tests').glob('*.upstream'))
    assert before == {str(p.relative_to(source)): p.read_bytes()
                      for p in source.rglob('*') if p.is_file()}


def test_normalize_twice_preserves_bootstrap_and_original_upstream_runner(
        adapter, fixture_tasks, tmp_path):
    source = fixture_tasks / 'terminal-jsonl-summary'
    original_runner = (source / 'tests/test.sh').read_bytes()
    original_dockerfile = (source / 'environment/Dockerfile').read_bytes()
    first = adapter.normalize_task(source, tmp_path / 'first', {'normalization': 1})
    second = adapter.normalize_task(first, tmp_path / 'second', {'normalization': 2})
    first_dockerfile = (first / 'environment/Dockerfile').read_bytes()
    assert b'/opt/terminal-curator-verifier' in first_dockerfile
    assert b'pytest==8.4.2' in first_dockerfile
    assert first_dockerfile.count(b'# LegoFlow verifier runtime:') == 1
    assert (second / 'environment/Dockerfile').read_bytes() == first_dockerfile
    assert (first / 'tests/test.sh.upstream').read_bytes() == original_runner
    assert (second / 'tests/test.sh.upstream').read_bytes() == original_runner
    assert (second / 'tests/test.sh').read_bytes() == (first / 'tests/test.sh').read_bytes()
    assert (source / 'environment/Dockerfile').read_bytes() == original_dockerfile
    assert (source / 'tests/test.sh').read_bytes() == original_runner


def test_normalize_bootstrap_preserves_effective_final_stage_user(
        adapter, fixture_tasks, tmp_path):
    source = tmp_path / 'multistage-source'
    shutil.copytree(fixture_tasks / 'terminal-jsonl-summary', source)
    for index, final_user in enumerate(['2002:2003', None]):
        dockerfile = ('FROM ubuntu:22.04 AS build\n'
                      'USER 1001:1002\n'
                      'FROM ubuntu:22.04 AS runtime\n'
                      'WORKDIR /app\n'
                      'COPY task_file /app/task_file\n')
        if final_user is not None:
            dockerfile += f'USER {final_user}\n'
        (source / 'environment/Dockerfile').write_text(dockerfile)
        result = adapter.normalize_task(source, tmp_path / f'normalized-{index}', {})
        normalized = (result / 'environment/Dockerfile').read_text()
        final_stage = normalized.rsplit('\nFROM ', 1)[-1]
        users = [line.split(None, 1)[1] for line in final_stage.splitlines()
                 if line.strip().upper().startswith('USER ')]
        assert users[-1] == (final_user or 'root')
        assert '1001:1002' not in users, 'build-stage user leaked into the final image'


def test_normalize_rejects_symlink_payload(adapter, fixture_tasks, tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(fixture_tasks / 'terminal-jsonl-summary', source)
    outside = tmp_path / 'private.txt'
    outside.write_text('private fixture payload')
    (source / 'environment/task_file/stolen').symlink_to(outside)
    with pytest.raises((ValueError, RuntimeError)):
        adapter.normalize_task(source, tmp_path / 'normalized', {})


def test_normalize_allows_solution_name_in_comments_and_docstrings(
        adapter, fixture_tasks, tmp_path):
    source = tmp_path / 'documented-candidate'
    shutil.copytree(fixture_tasks / 'terminal-jsonl-summary', source)
    tests = source / 'tests/test_outputs.py'
    original = tests.read_text()
    tests.write_text('"""Verify state after Harbor executes solve.sh."""\n'
                     '# Never execute solve.sh from this verifier.\n' + original + '''


class TestDocumentedState:
    """Inspect files produced by solve.sh."""

    def test_input_directory(self):
        """The solve.sh reference solution must preserve input files."""
        assert (ROOT / 'input').is_dir()


def test_documented_input_directory():
    """Validate existing inputs after solve.sh has completed."""
    assert (ROOT / 'input').is_dir()
''')
    result = adapter.normalize_task(source, tmp_path / 'normalized', {})
    assert result.is_dir()
    assert (result / 'tests/test_outputs.py').read_text() == tests.read_text()


def test_normalize_still_rejects_verifier_executing_reference_solution(
        adapter, fixture_tasks, tmp_path):
    source = tmp_path / 'solution-calling-candidate'
    shutil.copytree(fixture_tasks / 'terminal-jsonl-summary', source)
    tests = source / 'tests/test_outputs.py'
    tests.write_text('import subprocess\n' + tests.read_text() + '''


def test_forbidden_solution_execution():
    subprocess.run(['bash', '/solution/solve.sh'], check=True)
''')
    with pytest.raises((ValueError, RuntimeError), match='solve.sh'):
        adapter.normalize_task(source, tmp_path / 'normalized', {})


def test_normalize_converts_legacy_harbor_limits(adapter, fixture_tasks, tmp_path):
    import tomli_w

    source = tmp_path / 'legacy'
    shutil.copytree(fixture_tasks / 'terminal-jsonl-summary', source)
    config = tomllib.loads((source / 'task.toml').read_text())
    config.pop('schema_version')
    config['version'] = '1.0'
    config['environment'].pop('memory_mb')
    config['environment'].pop('storage_mb')
    config['environment'].update(memory='1.5G', storage='512M')
    (source / 'task.toml').write_text(tomli_w.dumps(config))
    normalized = adapter.normalize_task(source, tmp_path / 'normalized', {'fixture': True})
    actual = tomllib.loads((normalized / 'task.toml').read_text())
    assert 'version' not in actual
    assert 'memory' not in actual['environment']
    assert 'storage' not in actual['environment']
    assert actual['environment']['memory_mb'] == 1536
    assert actual['environment']['storage_mb'] == 512
    assert json.loads((normalized / 'provenance.json').read_text())['fixture'] is True


@pytest.mark.parametrize('section', ['environment', 'agent', 'verifier', 'solution'])
def test_normalize_does_not_export_host_environment_into_task(
        adapter, fixture_tasks, tmp_path, section):
    import tomli_w

    source = tmp_path / 'candidate'
    shutil.copytree(fixture_tasks / 'terminal-jsonl-summary', source)
    config = tomllib.loads((source / 'task.toml').read_text())
    config.setdefault(section, {})['env'] = {'PRIVATE_VALUE': '${PRIVATE_VALUE}'}
    (source / 'task.toml').write_text(tomli_w.dumps(config))
    with pytest.raises((ValueError, RuntimeError)):
        adapter.normalize_task(source, tmp_path / 'normalized', {})


def test_smoke_config_keeps_production_schema_without_credentials(adapter):
    from conftest import BLOCK

    production = adapter.load_config(BLOCK / 'config.yaml')
    smoke = adapter.load_config(BLOCK / 'tests/smoke/config.yaml')

    def keys(value):
        if not isinstance(value, dict):
            return None
        return {key: keys(item) for key, item in value.items()}

    assert keys(smoke) == keys(production)
    inputs = smoke['runtime_info']['input']
    assert inputs['source']['type'] == 'tasks'
    assert inputs['source']['path'] == 'tests/fixtures/tasks'
    assert inputs['source']['max_questions'] == 2
    assert inputs['llm_api']['api_key'] == ''
    assert inputs['stackoverflow']['api_key'] == ''


@pytest.mark.parametrize('exit_code', ['0', None])
def test_oracle_accepts_real_tests_without_exceptions(adapter, tmp_path, exit_code):
    trial(tmp_path, exit_code=exit_code)
    assert adapter.evaluate_trials(tmp_path, 1.0)['terminal-jsonl-summary']['passed']


@pytest.mark.parametrize('kwargs', [
    {'reward': 0.0},
    {'reward': 0.999999},
    {'exception': {'exception_type': 'EnvironmentStartTimeoutError'}},
    {'tests': 0},
    {'errors': 1},
    {'failures': 1},
    {'exit_code': '1'},
    {'exit_code': 'invalid'},
])
def test_oracle_rejects_incomplete_or_contradictory_success(adapter, tmp_path, kwargs):
    trial(tmp_path, **kwargs)
    result = adapter.evaluate_trials(tmp_path, 1.0)['terminal-jsonl-summary']
    assert not result['passed']
    assert result['reason']


def test_nop_requires_observed_test_failures(adapter, tmp_path):
    trial(tmp_path, reward=0.0, failures=2, exit_code=None)
    assert adapter.evaluate_trials(tmp_path, 0.0)['terminal-jsonl-summary']['passed']


@pytest.mark.parametrize('kwargs', [
    {'reward': 0.0, 'failures': 0},
    {'reward': 0.0, 'errors': 1},
    {'reward': 0.0, 'tests': 0},
    {'reward': 1.0, 'failures': 1},
    {'reward': 0.0, 'failures': 1, 'exception': {'exception_type': 'VerifierTimeoutError'}},
])
def test_nop_does_not_accept_infrastructure_failure_as_negative_control(
        adapter, tmp_path, kwargs):
    trial(tmp_path, **kwargs)
    assert not adapter.evaluate_trials(tmp_path, 0.0)['terminal-jsonl-summary']['passed']


@pytest.mark.parametrize('missing', ['junit', 'reward'])
def test_missing_verification_evidence_is_rejected(adapter, tmp_path, missing):
    directory = trial(tmp_path)
    if missing == 'junit':
        (directory / 'verifier/junit.xml').unlink()
    else:
        data = json.loads((directory / 'result.json').read_text())
        data['verifier_result']['rewards'] = {}
        (directory / 'result.json').write_text(json.dumps(data))
    assert not adapter.evaluate_trials(tmp_path, 1.0)['terminal-jsonl-summary']['passed']


def test_duplicate_trial_cannot_silently_override_failed_trial(adapter, tmp_path):
    trial(tmp_path, suffix='first')
    trial(tmp_path, suffix='second', reward=0.0, failures=1)
    assert not adapter.evaluate_trials(tmp_path, 1.0)['terminal-jsonl-summary']['passed']


@pytest.mark.parametrize('reward,expected', [(True, 1.0), (False, 0.0)])
def test_boolean_is_not_a_numeric_harbor_reward(adapter, tmp_path, reward, expected):
    trial(tmp_path, reward=reward, failures=1 if expected == 0 else 0)
    assert not adapter.evaluate_trials(tmp_path, expected)['terminal-jsonl-summary']['passed']


def test_publish_only_accepted_tasks_and_replace_stale_manifest(
        adapter, fixture_tasks, tmp_path):
    pool = tmp_path / 'published'
    accepted = [fixture_tasks / 'terminal-csv-reconcile',
                fixture_tasks / 'terminal-jsonl-summary']
    adapter.publish_tasks(accepted, pool)
    assert (pool / 'verifiable_tasks.txt').read_text().splitlines() == sorted(
        path.name for path in accepted)
    adapter.publish_tasks([accepted[1]], pool)
    assert (pool / 'verifiable_tasks.txt').read_text().splitlines() == [accepted[1].name]
    assert not (pool / accepted[0].name).exists()
    assert (pool / accepted[1].name / 'task.toml').is_file()


def test_publication_retains_immutable_input_for_already_staged_tracer_tasks(
        adapter, fixture_tasks, tmp_path):
    first = fixture_tasks / 'terminal-jsonl-summary'
    pool = tmp_path / 'tasks'
    adapter.publish_tasks([first], pool)
    prior_batch = pool.resolve(strict=True)
    input_path = Path(first.name) / 'environment/task_file/input/current.jsonl'
    prior_bytes = (prior_batch / input_path).read_bytes()
    replacement = tmp_path / 'modified' / first.name
    shutil.copytree(first, replacement)
    replacement_bytes = prior_bytes + b'\n'
    (replacement / 'environment/task_file/input/current.jsonl').write_bytes(replacement_bytes)
    adapter.publish_tasks([replacement], pool)
    assert pool.is_symlink()
    assert pool.resolve(strict=True) != prior_batch
    assert (prior_batch / input_path).read_bytes() == prior_bytes
    assert (pool / input_path).read_bytes() == replacement_bytes
    assert (prior_batch / 'verifiable_tasks.txt').read_text().splitlines() == [first.name]


def test_publish_rejects_symlink_candidate_without_damaging_existing_pool(
        adapter, fixture_tasks, tmp_path):
    pool = tmp_path / 'published'
    accepted = fixture_tasks / 'terminal-jsonl-summary'
    adapter.publish_tasks([accepted], pool)
    original = (pool / 'verifiable_tasks.txt').read_bytes()
    link = tmp_path / 'outside-link'
    link.symlink_to(accepted, target_is_directory=True)
    with pytest.raises((ValueError, RuntimeError)):
        adapter.publish_tasks([link], pool)
    assert (pool / 'verifiable_tasks.txt').read_bytes() == original
    assert (pool / accepted.name / 'task.toml').is_file()


def test_publish_rejects_duplicate_names(adapter, fixture_tasks, tmp_path):
    first = fixture_tasks / 'terminal-jsonl-summary'
    second = tmp_path / 'copy' / first.name
    shutil.copytree(first, second)
    with pytest.raises((ValueError, RuntimeError)):
        adapter.publish_tasks([first, second], tmp_path / 'published')


def test_publish_rollback_preserves_prior_verified_tasks(
        adapter, fixture_tasks, tmp_path, monkeypatch):
    pool = tmp_path / 'published'
    old = fixture_tasks / 'terminal-jsonl-summary'
    replacement = fixture_tasks / 'terminal-csv-reconcile'
    adapter.publish_tasks([old], pool)
    rename = Path.rename

    def fail_publication(path, destination):
        if destination == pool and path.name.startswith('.published.') and '.previous.' not in path.name:
            raise OSError('injected publication failure')
        return rename(path, destination)

    monkeypatch.setattr(Path, 'rename', fail_publication)
    with pytest.raises(OSError, match='injected publication failure'):
        adapter.publish_tasks([replacement], pool)
    assert (pool / 'verifiable_tasks.txt').read_text().splitlines() == [old.name]
    assert (pool / old.name / 'instruction.md').is_file()
    assert not (pool / replacement.name).exists()


def test_publish_rejects_nested_symlink_payload(adapter, fixture_tasks, tmp_path):
    candidate = tmp_path / 'terminal-candidate'
    shutil.copytree(fixture_tasks / 'terminal-jsonl-summary', candidate)
    outside = tmp_path / 'private.txt'
    outside.write_text('private fixture payload')
    (candidate / 'environment/task_file/linked-input').symlink_to(outside)
    with pytest.raises((ValueError, RuntimeError)):
        adapter.publish_tasks([candidate], tmp_path / 'published')


def test_redaction_covers_environment_keys_and_secret_patterns(adapter, monkeypatch):
    fake_llm = 'sk-' + 'unit-test-secret-never-valid-123456789'
    fake_so = 'unit-test-stack-key-never-valid'
    monkeypatch.setenv('OPENAI_API_KEY', fake_llm)
    monkeypatch.setenv('SO_API_KEY', fake_so)
    raw = f'Authorization: Bearer {fake_llm}; https://example.invalid/?key={fake_so}'
    sanitized = adapter.redact(raw)
    assert fake_llm not in sanitized
    assert fake_so not in sanitized
    assert 'example.invalid' in sanitized


@pytest.mark.parametrize('mutation', [None, 'nop_reward', 'oracle_exception',
                                    'zero_tests', 'duplicate_manifest', 'stale_report'])
def test_smoke_verifier_checks_raw_evidence(
        adapter, fixture_tasks, tmp_path, monkeypatch, mutation):
    import yaml
    from datetime import datetime, timezone

    names = sorted(path.name for path in fixture_tasks.iterdir() if path.is_dir())
    pool = tmp_path / 'published'
    adapter.publish_tasks([fixture_tasks / name for name in names], pool)
    for name in names:
        (pool / name / 'provenance.json').write_text(json.dumps({'run_id': 'offline-unit-evidence'}))
    report = {
        'run_id': 'offline-unit-evidence', 'status': 'completed', 'source': 'tasks',
        'started_at': datetime.now(timezone.utc).isoformat(),
        'total_candidates': len(names), 'verified_tasks': names,
        'jobs': {}, 'validation': {},
    }
    raw_paths = {}
    for agent, reward in [('oracle', 1.0), ('nop', 0.0)]:
        job = tmp_path / agent
        report['jobs'][agent] = str(job)
        report['validation'][agent] = {}
        for name in names:
            raw_paths[agent, name] = trial(job, name, reward=reward,
                                          failures=0 if agent == 'oracle' else 1,
                                          exit_code='0' if agent == 'oracle' else None)
            report['validation'][agent][name] = {'passed': True, 'reason': ''}
    if mutation in ('nop_reward', 'oracle_exception'):
        agent = 'nop' if mutation == 'nop_reward' else 'oracle'
        path = raw_paths[agent, names[0]] / 'result.json'
        data = json.loads(path.read_text())
        if mutation == 'nop_reward':
            data['verifier_result']['rewards']['reward'] = 1.0
        else:
            data['exception_info'] = {'exception_type': 'VerifierTimeoutError'}
        path.write_text(json.dumps(data))
    elif mutation == 'zero_tests':
        (raw_paths['oracle', names[0]] / 'verifier/junit.xml').write_text(
            '<testsuite tests="0" errors="0" failures="0"/>')
    elif mutation == 'duplicate_manifest':
        (pool / 'verifiable_tasks.txt').write_text('\n'.join(names + [names[0]]) + '\n')
    elif mutation == 'stale_report':
        report['started_at'] = '2020-01-01T00:00:00+00:00'
    report_path = tmp_path / 'report.json'
    report_path.write_text(json.dumps(report))
    config = {
        'runtime_info': {
            'input': {'source': {'type': 'tasks', 'path': str(fixture_tasks), 'max_questions': 2}},
            'output': {'report': {'path': str(report_path)},
                       'harbor_tasks_dir': {'path': str(pool)}},
        },
    }
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv('TERMINAL_CURATOR_TEST_PYTHON', sys.executable)
    monkeypatch.setenv('TERMINAL_CURATOR_SMOKE_STARTED_AT', '1700000000')
    verifier = Path(__file__).parent / 'smoke/verify.sh'
    result = subprocess.run(['bash', str(verifier), '--config', str(config_path)],
                            text=True, capture_output=True, timeout=20)
    if mutation is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, 'The smoke must not trust passed flags over raw evidence'
