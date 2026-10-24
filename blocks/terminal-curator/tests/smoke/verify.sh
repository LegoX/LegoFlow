#!/usr/bin/env bash
# Independently check raw Harbor evidence, not just the adapter's passed flags.
set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="${TERMINAL_CURATOR_CONFIG:-tests/smoke/config.yaml}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) [[ $# -ge 2 ]] || exit 2; CONFIG="$2"; shift 2 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done
PYTHON="${TERMINAL_CURATOR_TEST_PYTHON:-$BLOCK_DIR/artifacts/env/terminal-curator/bin/python}"
if [[ ! -x "$PYTHON" ]]; then PYTHON=python3; fi
"$PYTHON" - "$BLOCK_DIR" "$CONFIG" <<'PY'
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import yaml

block = Path(sys.argv[1])


def resolve(value):
    path = Path(value)
    return path if path.is_absolute() else block / path


def require(condition, message):
    if not condition:
        raise AssertionError(message)


try:
    config = yaml.safe_load(resolve(sys.argv[2]).read_text())
    inputs = config['runtime_info']['input']
    outputs = config['runtime_info']['output']
    require(inputs['source']['type'] == 'tasks', 'fixture smoke must use source.type=tasks')
    source = resolve(inputs['source']['path'])
    expected = sorted(path.name for path in source.iterdir() if path.is_dir())[:inputs['source']['max_questions']]
    require(len(expected) >= 2, 'fixture smoke requires both nontrivial tasks')
    report_path = resolve(outputs['report']['path'])
    report = json.loads(report_path.read_text())
    require(report['status'] == 'completed', 'latest smoke run did not complete')
    require(report['source'] == 'tasks', 'report does not describe an offline-fixture run')
    require(report['total_candidates'] == len(expected), 'candidate count differs from smoke source')
    require(sorted(report['verified_tasks']) == expected, 'not every fixture passed both gates')
    started_at = os.environ.get('TERMINAL_CURATOR_SMOKE_STARTED_AT')
    if started_at:
        require(datetime.fromisoformat(report['started_at']).timestamp() >= int(started_at),
                'report predates the requested smoke launch')
    pool = resolve(outputs['harbor_tasks_dir']['path'])
    manifest = (pool / 'verifiable_tasks.txt').read_text().splitlines()
    require(manifest == expected, 'published manifest is missing, stale, duplicated, or includes extra tasks')
    for name in expected:
        require((pool / name / 'task.toml').is_file(), f'{name}: published task missing')
        provenance = json.loads((pool / name / 'provenance.json').read_text())
        require(provenance['run_id'] == report['run_id'], f'{name}: publication belongs to another run')
    for agent, reward in (('oracle', 1.0), ('nop', 0.0)):
        job = resolve(report['jobs'][agent])
        raw = {}
        for result_path in job.glob('*/result.json'):
            result = json.loads(result_path.read_text())
            name = result.get('task_name')
            require(name not in raw, f'{agent}: duplicate trials for {name}')
            raw[name] = (result_path.parent, result)
        require(sorted(raw) == expected, f'{agent}: missing or unexpected task results')
        for name in expected:
            trial, result = raw[name]
            require(not result.get('exception_info'), f'{agent}/{name}: Harbor trial exception')
            actual = (result.get('verifier_result') or {}).get('rewards', {}).get('reward')
            require(type(actual) in (int, float) and actual == reward,
                    f'{agent}/{name}: reward must be exactly {reward}')
            root = ET.parse(trial / 'verifier/junit.xml').getroot()
            suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
            counts = {key: sum(int(suite.get(key, 0)) for suite in suites)
                      for key in ('tests', 'errors', 'failures', 'skipped')}
            require(counts['tests'] > counts['skipped'], f'{agent}/{name}: no tests executed')
            require(counts['errors'] == 0, f'{agent}/{name}: verifier infrastructure errors')
            require((counts['failures'] == 0) if agent == 'oracle' else (counts['failures'] > 0),
                    f'{agent}/{name}: JUnit does not substantiate expected reward')
            exit_file = trial / 'agent/exit-code.txt'
            if agent == 'oracle' and exit_file.exists():
                require(exit_file.read_text().strip() == '0', f'{name}: reference solution failed')
            require(report['validation'][agent][name]['passed'] is True,
                    f'{agent}/{name}: report disagrees with raw evidence')
    print(f'PASS: {len(expected)} hand-authored offline tasks passed independent Harbor Oracle/NOP checks.')
    print(f'Evidence: {report_path}')
except (OSError, ValueError, KeyError, TypeError, AssertionError, ET.ParseError) as exc:
    print(f'FAIL: {exc}', file=sys.stderr)
    raise SystemExit(1)
PY
