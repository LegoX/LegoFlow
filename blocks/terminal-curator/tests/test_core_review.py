"""Regression checks for independently reproduced pipeline and publication bugs."""

import copy
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import BLOCK


@pytest.fixture
def core_case(adapter, fixture_tasks, tmp_path, monkeypatch):
    block = tmp_path / 'block'
    source = block / 'input/task-one'
    shutil.copytree(fixture_tasks / 'terminal-jsonl-summary', source)
    config = yaml.safe_load((BLOCK / 'config.yaml').read_text())
    config['runtime_info']['input']['source'].update(type='tasks', path=str(source.parent), max_questions=1)
    config_path = block / 'config.yaml'
    monkeypatch.setattr(adapter, 'BLOCK', block)
    monkeypatch.setattr(adapter, 'ARTIFACTS', block / 'artifacts')

    def load():
        config_path.write_text(yaml.safe_dump(config))
        return adapter.load_config(config_path)

    return SimpleNamespace(block=block, config=config, source=source, load=load,
                           pool=block / 'artifacts/tasks')


@pytest.mark.parametrize('path', ['artifacts/tasks', 'artifacts/tasks/registry.json',
                                'artifacts/tasks/task-one/task.toml'])
def test_report_cannot_overwrite_verified_outputs(adapter, core_case, path):
    core_case.config['runtime_info']['output']['report']['path'] = path
    with pytest.raises(ValueError):
        core_case.load()


@pytest.mark.parametrize('output', ['report', 'harbor_tasks_dir'])
@pytest.mark.parametrize('path', ['state.json', 'index.yaml', 'run.lock', 'launch.lock', 'archive.lock',
                                'run.pid', 'run.process.json', 'runs/report.json',
                                'env/report.json', 'archives/report.json'])
def test_outputs_cannot_corrupt_runtime_control_files(core_case, output, path):
    outputs = core_case.config['runtime_info']['output']
    outputs[output]['path'] = 'artifacts/' + path
    if output == 'harbor_tasks_dir':
        outputs['eval_registry']['path'] = outputs[output]['path'] + '/registry.json'
    with pytest.raises(ValueError, match='reserved'):
        core_case.load()


def test_report_alias_cannot_overwrite_state(core_case):
    artifacts = core_case.block / 'artifacts'
    artifacts.mkdir()
    (artifacts / 'state.json').write_text('{}')
    (artifacts / 'report-alias.json').symlink_to('state.json')
    core_case.config['runtime_info']['output']['report']['path'] = 'artifacts/report-alias.json'
    with pytest.raises(ValueError, match='reserved'):
        core_case.load()


def test_cannot_write_inside_previous_immutable_publication(core_case):
    core_case.config['runtime_info']['output']['harbor_tasks_dir']['path'] = 'artifacts/tasks-versions/old/broken'
    core_case.config['runtime_info']['output']['eval_registry']['path'] = 'artifacts/tasks-versions/old/broken/registry.json'
    with pytest.raises(ValueError, match='immutable'):
        core_case.load()


def test_existing_public_pool_and_custom_consistent_outputs_remain_valid(adapter, core_case):
    adapter.publish_tasks([core_case.source], core_case.pool)
    assert core_case.load()
    outputs = core_case.config['runtime_info']['output']
    outputs['harbor_tasks_dir']['path'] = 'artifacts/custom/pool'
    outputs['eval_registry']['path'] = 'artifacts/custom/pool/registry.json'
    assert core_case.load()


def test_registry_path_cannot_claim_an_unwritten_output(core_case):
    core_case.config['runtime_info']['output']['eval_registry']['path'] = 'artifacts/somewhere-else.json'
    with pytest.raises(ValueError, match='eval_registry'):
        core_case.load()


@pytest.mark.parametrize('key,value', [
    ('question_ids', '59838'), ('question_ids', 59838), ('question_ids', False),
    ('question_ids', ['../59838']), ('question_ids', [True]),
    ('tags', 'bash'), ('tags', [None]), ('tags', ['']),
    ('min_score', True), ('min_score', '10'), ('min_score', 1.5),
])
def test_source_selectors_cannot_silently_change_the_authorized_query(adapter, core_case, monkeypatch, key, value):
    source = core_case.config['runtime_info']['input']['source']
    source.update(type='stackoverflow')
    source[key] = value
    with pytest.raises(ValueError):
        core_case.load()
    calls = []
    monkeypatch.setattr(adapter, 'so_request', lambda *args: calls.append(args))
    with pytest.raises(ValueError):
        adapter.collect_questions(source, {})
    assert calls == [], 'invalid selectors must fail before contacting Stack Exchange'


def test_explicit_ids_are_whole_values_and_deduplicated(adapter, monkeypatch):
    calls = []
    def api(creds, endpoint, params):
        calls.append(endpoint)
        return {'items': []}
    monkeypatch.setattr(adapter, 'so_request', api)
    source = {'type': 'stackoverflow', 'max_questions': 2, 'question_ids': ['59838', 59838, 59895],
              'tags': [], 'min_score': -1}
    assert adapter.collect_questions(source, {}) == []
    assert calls == ['/questions/59838;59895']


def test_report_directory_is_rejected_before_validation_or_publication(core_case):
    (core_case.block / 'artifacts/reports').mkdir(parents=True)
    core_case.config['runtime_info']['output']['report']['path'] = 'artifacts/reports'
    with pytest.raises(ValueError, match='name a file'):
        core_case.load()


def test_report_cannot_overwrite_an_imported_task(core_case):
    source = core_case.block / 'artifacts/imported/task-one'
    shutil.copytree(core_case.source, source)
    core_case.config['runtime_info']['input']['source']['path'] = str(source.parent)
    core_case.config['runtime_info']['output']['report']['path'] = str(source / 'instruction.md')
    with pytest.raises(ValueError, match='source input'):
        core_case.load()


@pytest.mark.parametrize('section,key', [('generation', 'request_timeout_sec'),
                                       ('validation', 'timeout_multiplier'),
                                       ('validation', 'job_timeout_sec')])
@pytest.mark.parametrize('value', [None, 0, -1, True, '180', float('nan'), float('inf')])
def test_unbounded_or_invalid_timeouts_fail_before_a_run(core_case, section, key, value):
    core_case.config['runtime_info']['input'][section][key] = value
    with pytest.raises(ValueError, match='positive finite'):
        core_case.load()


@pytest.mark.parametrize('value', [None, -1, True, 1.5])
def test_generation_retries_are_a_nonnegative_integer(core_case, value):
    core_case.config['runtime_info']['input']['generation']['max_retries'] = value
    with pytest.raises(ValueError, match='nonnegative integer'):
        core_case.load()


def test_zero_retries_is_a_valid_budget(core_case):
    core_case.config['runtime_info']['input']['generation']['max_retries'] = 0
    assert core_case.load()


def test_declared_vendor_pin_matches_the_executed_adapter(core_case):
    core_case.config['meta_info']['repos']['terminal-lego']['commit_id'] = '0' * 40
    with pytest.raises(ValueError, match='pin'):
        core_case.load()


@pytest.mark.parametrize('answer', [None, [], 'not an answer object', {'body': ''}, {'body': '  '}])
def test_unusable_answer_does_not_abort_or_consume_json_quota(adapter, tmp_path, answer):
    path = tmp_path / 'questions.json'
    path.write_text(json.dumps({'questions': [
        {'question_id': 1, 'score': 50, 'accepted_answer_id': 100, 'accepted_answer': answer},
        {'question_id': '2', 'score': 50, 'accepted_answer': {'body': 'valid source answer'}},
    ]}))
    rows = adapter.collect_questions({'type': 'json', 'path': str(path), 'max_questions': 1, 'min_score': 0}, {})
    assert [row['question_id'] for row in rows] == [2]


@pytest.mark.parametrize('qid', ['../../escape', '/tmp/escape', True, 1.5, 0, -1, None])
def test_json_question_id_cannot_become_an_unsafe_or_aliased_task_name(adapter, tmp_path, qid):
    path = tmp_path / 'questions.json'
    path.write_text(json.dumps({'questions': [
        {'question_id': qid, 'score': 50, 'accepted_answer': {'body': 'source answer'}},
    ]}))
    with pytest.raises(ValueError, match='positive integer'):
        adapter.collect_questions({'type': 'json', 'path': str(path), 'max_questions': 1, 'min_score': 0}, {})


def test_normalize_restores_user_inherited_from_a_docker_stage(adapter, core_case):
    dockerfile = core_case.source / 'environment/Dockerfile'
    dockerfile.write_text('FROM python:3.12-slim AS build\nUSER 1001:1002\n'
                         'FROM build AS final\nWORKDIR /app\nCOPY task_file /app/task_file\n')
    result = adapter.normalize_task(core_case.source, core_case.block / 'normalized', {})
    assert (result / 'environment/Dockerfile').read_text().rstrip().endswith('USER 1001:1002')


def test_unknown_external_base_user_is_not_silently_changed_to_root(adapter, core_case):
    dockerfile = core_case.source / 'environment/Dockerfile'
    dockerfile.write_text('FROM example.invalid/prebuilt-nonroot:unit\nWORKDIR /app\nCOPY task_file /app/task_file\n')
    result = adapter.normalize_task(core_case.source, core_case.block / 'normalized', {})
    assert '\nUSER root\n' not in (result / 'environment/Dockerfile').read_text()


@pytest.mark.parametrize('path', ['/app/task_file/solve.sh', '/app/task_file/input/solve.sh', 'solve.sh'])
def test_reference_guard_allows_legitimate_task_filenames(adapter, core_case, path):
    (core_case.source / 'instruction.md').write_text(f'Create the task output at {path}.')
    (core_case.source / 'tests/test_outputs.py').write_text(
        f'from pathlib import Path\ndef test_output():\n    assert Path({path!r}).is_file()\n')
    assert adapter.normalize_task(core_case.source, core_case.block / 'normalized', {}).is_dir()


@pytest.mark.parametrize('expression', [
    "subprocess.run(['bash', '/solution/solve.sh'])",
    '''subprocess.run("bash -c 'cat /solution/solve.sh'", shell=True)''',
    "(Path('/solution') / 'solve.sh').read_text()",
])
def test_reference_guard_rejects_literal_or_joined_oracle_access(adapter, core_case, expression):
    (core_case.source / 'tests/test_outputs.py').write_text(
        'import subprocess\nfrom pathlib import Path\ndef test_output():\n    ' + expression + '\n')
    with pytest.raises(ValueError, match='/solution'):
        adapter.normalize_task(core_case.source, core_case.block / 'normalized', {})


def test_reference_path_in_real_docstrings_is_documentation(adapter, core_case):
    (core_case.source / 'tests/test_outputs.py').write_text(
        '"""Do not read /solution/solve.sh."""\n'
        '# /solution is inaccessible to a legitimate verifier.\n'
        'def test_output():\n    """Check state without /solution access."""\n    assert True\n')
    assert adapter.normalize_task(core_case.source, core_case.block / 'normalized', {}).is_dir()


def fake_checksum_rpc(config, directory):
    # A deterministic stand-in for the pinned Harbor subprocess. Production
    # delegates to Harbor's Task.checksum; this test only simulates changing bytes.
    return {task.name: hashlib.sha256((task / 'instruction.md').read_bytes()).hexdigest()
            for task in directory.iterdir() if task.is_dir()}


def install_fake_runtime(adapter, monkeypatch, mismatch=False, mutate=False):
    monkeypatch.setattr(adapter, 'check', lambda config: [])
    monkeypatch.setattr(adapter, 'credentials', lambda inputs: {})
    monkeypatch.setattr(adapter, 'repo_commits', lambda: {})
    monkeypatch.setattr(adapter, 'harbor_task_checksums', fake_checksum_rpc)

    def run_harbor(config, candidates, run_dir, agent):
        job = run_dir / 'validation' / agent
        for name, checksum in fake_checksum_rpc(config, candidates).items():
            trial = job / (name + '__trial')
            (trial / 'verifier').mkdir(parents=True)
            reward = 1.0 if agent == 'oracle' else 0.0
            (trial / 'result.json').write_text(json.dumps({
                'task_name': name, 'task_checksum': '0' * 64 if mismatch and agent == 'nop' else checksum,
                'exception_info': None, 'verifier_result': {'rewards': {'reward': reward}},
            }))
            failure = '' if agent == 'oracle' else '<failure/>'
            (trial / 'verifier/junit.xml').write_text(
                f'<testsuite tests="1" errors="0" failures="{int(bool(failure))}">'
                f'<testcase name="state">{failure}</testcase></testsuite>')
        if mutate:
            (next(candidates.iterdir()) / 'instruction.md').write_text('changed during validation')
        return job

    monkeypatch.setattr(adapter, 'run_harbor', run_harbor)


def test_pipeline_uses_supplied_run_id_and_checksum_bound_results(adapter, core_case, monkeypatch):
    install_fake_runtime(adapter, monkeypatch)
    report = adapter.run(core_case.load(), run_id='unit-batch')
    assert report['status'] == 'completed'
    assert report['run_id'] == 'unit-batch'
    assert (core_case.block / 'artifacts/runs/unit-batch/config.yaml').is_file()
    assert report['harbor_task_checksums'] == fake_checksum_rpc({}, core_case.pool)
    assert report['tree_fingerprints'] == adapter.task_tree_fingerprints(core_case.pool)
    proof = adapter._publication.verify_publication(core_case.pool)
    assert proof['run_id'] == report['run_id']
    assert proof['harbor_task_checksums'] == report['harbor_task_checksums']


def test_one_publication_binds_multiple_verified_task_ids(adapter, core_case, monkeypatch):
    install_fake_runtime(adapter, monkeypatch)
    second = core_case.source.with_name('second-task')
    shutil.copytree(core_case.source, second)
    core_case.config['runtime_info']['input']['source']['max_questions'] = 2
    report = adapter.run(core_case.load())
    proof = adapter._publication.verify_publication(core_case.pool)
    assert proof['verified_tasks'] == sorted([core_case.source.name, second.name])
    assert proof['harbor_task_checksums'] == report['harbor_task_checksums']
    assert len(proof['tree_fingerprints']) == 2


@pytest.mark.parametrize('change', ['instruction', 'provenance', 'permissions', 'empty-directory', 'manifest', 'registry', 'proof'])
def test_consumers_reject_a_publication_changed_after_validation(adapter, core_case, monkeypatch, change):
    install_fake_runtime(adapter, monkeypatch)
    report = adapter.run(core_case.load())
    task = core_case.pool / report['verified_tasks'][0]
    if change in ('instruction', 'provenance'):
        (task / ('instruction.md' if change == 'instruction' else 'provenance.json')).write_text('changed')
    elif change == 'permissions':
        (task / 'instruction.md').chmod(0o600)
    elif change == 'empty-directory':
        (task / 'new-empty-directory').mkdir()
    elif change == 'manifest':
        (core_case.pool / 'verifiable_tasks.txt').write_text('another-task\n')
    elif change == 'registry':
        (core_case.pool / 'registry.json').write_text('[]')
    else:
        (core_case.pool / 'validation.json').unlink()
    with pytest.raises(ValueError):
        adapter._publication.verify_publication(core_case.pool)


@pytest.mark.parametrize('mismatch,mutate', [(True, False), (False, True)])
def test_pipeline_rejects_different_tasks_before_switching_publication(
        adapter, core_case, monkeypatch, mismatch, mutate):
    adapter.publish_tasks([core_case.source], core_case.pool)
    old_batch = core_case.pool.resolve()
    install_fake_runtime(adapter, monkeypatch, mismatch, mutate)
    with pytest.raises(ValueError, match='no candidates passed|changed'):
        adapter.run(core_case.load())
    assert core_case.pool.resolve() == old_batch


def test_copied_publication_is_checked_before_the_pointer_switch(adapter, core_case, monkeypatch):
    adapter.publish_tasks([core_case.source], core_case.pool)
    old_batch = core_case.pool.resolve()
    install_fake_runtime(adapter, monkeypatch)
    copytree = shutil.copytree

    def corrupt_publication(source, destination, *args, **kwargs):
        result = copytree(source, destination, *args, **kwargs)
        destination = Path(destination)
        if destination.parent.parent.name == 'tasks-versions' and (destination / 'instruction.md').exists():
            (destination / 'instruction.md').write_text('changed while copying the accepted task')
        return result

    monkeypatch.setattr(adapter.shutil, 'copytree', corrupt_publication)
    with pytest.raises(ValueError, match='publication bytes'):
        adapter.run(core_case.load())
    assert core_case.pool.resolve() == old_batch


def test_digest_io_failure_does_not_publish_a_failed_run(adapter, core_case, monkeypatch):
    adapter.publish_tasks([core_case.source], core_case.pool)
    old_batch = core_case.pool.resolve()
    install_fake_runtime(adapter, monkeypatch)

    def unreadable(path):
        raise OSError('candidate changed or became unreadable')

    monkeypatch.setattr(adapter, 'task_digest', unreadable)
    with pytest.raises(OSError, match='unreadable'):
        adapter.run(core_case.load())
    assert core_case.pool.resolve() == old_batch


def test_tree_fingerprint_covers_empty_directories_and_permission_bits(adapter, core_case):
    before = adapter.task_tree_fingerprint(core_case.source)
    legacy = adapter.task_digest(core_case.source)
    (core_case.source / 'environment/task_file/empty-needed-directory').mkdir()
    directory_changed = adapter.task_tree_fingerprint(core_case.source)
    assert directory_changed != before
    assert adapter.task_digest(core_case.source) == legacy
    helper = core_case.source / 'solution/solve.sh'
    helper.chmod(helper.stat().st_mode ^ 0o100)
    assert adapter.task_tree_fingerprint(core_case.source) != directory_changed
    assert adapter.task_digest(core_case.source) == legacy


def change_tree(task, change):
    if change == 'empty-directory':
        (task / 'environment/task_file/empty-needed-directory').mkdir()
    else:
        helper = task / 'solution/solve.sh'
        helper.chmod(helper.stat().st_mode ^ 0o100)


@pytest.mark.parametrize('change', ['empty-directory', 'permissions'])
def test_shape_or_mode_change_during_validation_is_not_hidden_by_harbor_hash(
        adapter, core_case, monkeypatch, change):
    adapter.publish_tasks([core_case.source], core_case.pool)
    old_batch = core_case.pool.resolve()
    install_fake_runtime(adapter, monkeypatch)
    fake_run = adapter.run_harbor

    def mutate(config, candidates, run_dir, agent):
        result = fake_run(config, candidates, run_dir, agent)
        before = fake_checksum_rpc(config, candidates)
        change_tree(next(candidates.iterdir()), change)
        assert fake_checksum_rpc(config, candidates) == before
        return result

    monkeypatch.setattr(adapter, 'run_harbor', mutate)
    with pytest.raises(ValueError, match='tree changed during'):
        adapter.run(core_case.load())
    assert core_case.pool.resolve() == old_batch


@pytest.mark.parametrize('change', ['empty-directory', 'permissions'])
def test_shape_or_mode_change_in_copied_batch_never_replaces_verified_pool(
        adapter, core_case, monkeypatch, change):
    adapter.publish_tasks([core_case.source], core_case.pool)
    old_batch = core_case.pool.resolve()
    install_fake_runtime(adapter, monkeypatch)
    copytree = shutil.copytree

    def corrupt(source, destination, *args, **kwargs):
        result = copytree(source, destination, *args, **kwargs)
        destination = Path(destination)
        if destination.parent.parent.name == 'tasks-versions' and (destination / 'instruction.md').exists():
            change_tree(destination, change)
        return result

    monkeypatch.setattr(adapter.shutil, 'copytree', corrupt)
    with pytest.raises(ValueError, match='publication tree differs'):
        adapter.run(core_case.load())
    assert core_case.pool.resolve() == old_batch


def test_generation_retains_successes_and_reports_each_failed_future(adapter, tmp_path, monkeypatch):
    upstream = tmp_path / 'fake-upstream/generator'
    upstream.mkdir(parents=True)
    (upstream / 'task_generator.py').write_text('''
_config = {}
INSTRUCTION_PROMPT_TEMPLATE = ""
DOCKERFILE_PROMPT_TEMPLATE = ""
ENVIRONMENT_PROMPT_TEMPLATE = ""
SOLUTION_PROMPT_TEMPLATE = ""
TEST_PROMPT_TEMPLATE = ""
class SOQuestion:
    @classmethod
    def from_dict(cls, value): return value
class Tracker:
    def get_summary(self): return {"call_count": 0}
token_tracker = Tracker()
''')
    monkeypatch.setattr(adapter, 'REPO', upstream.parent)

    class Generator:
        def __init__(self, question, directory):
            if question['question_id'] == 1:
                raise ValueError('one malformed source question')
            self.question = question

        def generate(self):
            if self.question['question_id'] == 2:
                return False
            self.task_dir.mkdir()
            return True

    monkeypatch.setattr(adapter, 'generator_class', lambda module: Generator)
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    inputs = {'generation': {'workers': 2, 'request_timeout_sec': 1, 'max_retries': 0}}
    creds = {'OPENAI_API_BASE': 'https://example.invalid/v1', 'OPENAI_API_KEY': '', 'MODEL_NAME': 'unit'}
    questions = [{'question_id': qid, 'title': 'source', 'accepted_answer': {}} for qid in (1, 2, 3)]
    events = []
    candidates, usage = adapter.generate_questions(questions, run_dir, inputs, creds, progress=events.append)
    assert [candidate.name for candidate in candidates] == ['terminal-so-3']
    assert usage == {'call_count': 0}
    assert len(events) == 3
    assert {event['task_id']: event['status'] for event in events} == {
        'terminal-so-1': 'failed', 'terminal-so-2': 'failed', 'terminal-so-3': 'generated',
    }
    assert all(event['reason'] for event in events if event['status'] == 'failed')
    import sys
    assert sys.modules['terminal_lego_generator'].MAX_RETRIES == 1


def test_generation_progress_is_visible_before_the_batch_finishes(adapter, core_case, monkeypatch):
    install_fake_runtime(adapter, monkeypatch)
    core_case.config['runtime_info']['input']['source']['type'] = 'json'
    monkeypatch.setattr(adapter, 'collect_questions', lambda source, creds: [
        {'question_id': 1}, {'question_id': 2},
    ])
    candidate = core_case.source.with_name('terminal-so-2')
    core_case.source.rename(candidate)
    observed = []

    def generate(questions, run_dir, inputs, creds, progress=None):
        for event in [
            {'task_id': 'terminal-so-1', 'status': 'failed', 'reason': 'not approved'},
            {'task_id': 'terminal-so-2', 'status': 'generated', 'reason': ''},
        ]:
            progress(event)
            observed.append(json.loads((core_case.block / 'artifacts/state.json').read_text()))
        return [candidate], {'call_count': 0}

    monkeypatch.setattr(adapter, 'generate_questions', generate)
    report = adapter.run(core_case.load())
    assert observed[0]['stage'] == 'generate'
    assert observed[0]['generation_completed'] == observed[0]['generation_failed'] == 1
    assert observed[1]['generation_completed'] == 2
    assert report['collected_questions'] == 2
    assert report['generation_completed'] == 2 and report['generation_failed'] == 1
    assert report['candidate_ids'] == ['terminal-so-2']
    assert report['verified_tasks'] == ['terminal-so-2']
    assert report['rejected']['terminal-so-1'] == 'not approved'
    assert report['task_progress']['terminal-so-1']['status'] == 'failed'
    assert report['task_progress']['terminal-so-2'] == {'stage': 'publish', 'status': 'completed'}
