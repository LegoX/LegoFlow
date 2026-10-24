"""Execute the offline examples locally after relocating their /app root.

This validates fixture semantics without Docker, networking, or an LLM. It is
not a replacement for the real Harbor Oracle/NOP smoke test.
"""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('task_id', ['terminal-jsonl-summary', 'terminal-csv-reconcile'])
def test_fixture_fails_fresh_then_passes_reference_solution(fixture_tasks, tmp_path, task_id):
    task = fixture_tasks / task_id
    root = tmp_path / 'task_file'
    shutil.copytree(task / 'environment/task_file', root)
    namespace = {'__name__': f'fixture_{task_id.replace("-", "_")}'}
    tests = task / 'tests/test_outputs.py'
    exec(compile(tests.read_text(), str(tests), 'exec'), namespace)
    namespace['ROOT'] = root
    functions = [value for key, value in namespace.items()
                 if key.startswith('test_') and callable(value)]
    assert len(functions) >= 3
    failures = 0
    for function in functions:
        try:
            function()
        except (AssertionError, FileNotFoundError):
            failures += 1
    assert failures >= 1, 'Fresh input must require work before tests can pass'
    solution = (task / 'solution/solve.sh').read_text().replace('/app/task_file', str(root))
    result = subprocess.run(['bash'], input=solution, text=True, capture_output=True,
                            cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    for function in functions:
        function()


@pytest.mark.parametrize('task_id,output', [
    ('terminal-jsonl-summary', 'summary.json'),
    ('terminal-csv-reconcile', 'contacts.csv'),
])
def test_fixture_verifier_rejects_empty_output(fixture_tasks, tmp_path, task_id, output):
    task = fixture_tasks / task_id
    root = tmp_path / 'task_file'
    shutil.copytree(task / 'environment/task_file', root)
    (root / 'output').mkdir()
    (root / 'output' / output).write_text('[]\n' if output.endswith('.json') else 'email,name,updated_epoch\n')
    namespace = {'__name__': 'fixture_invalid_output'}
    tests = task / 'tests/test_outputs.py'
    exec(compile(tests.read_text(), str(tests), 'exec'), namespace)
    namespace['ROOT'] = root
    candidates = [value for key, value in namespace.items()
                  if key.startswith('test_') and callable(value) and 'input' not in key]
    with pytest.raises(AssertionError):
        for function in candidates:
            function()
