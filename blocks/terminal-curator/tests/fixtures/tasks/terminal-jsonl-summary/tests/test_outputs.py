import hashlib
import json
from pathlib import Path

ROOT = Path('/app/task_file')
INPUT_HASHES = {'current.jsonl': '6725d513c8a9c0bfac2c695619b99e08d9ca23413d5b4e8c100e616212ccda83',
 'nested/more.jsonl': '278a07279e4144882fab2b56ba459fcb649cc8bc4b3e1725f3a9b288fe528a20',
 'nested/percentile.jsonl': 'f814843e7596ca8dc56fdec11422af7aeca53030313aae24504b9b1483ca4cd0'}


def test_complete_service_report():
    report = json.loads((ROOT / 'output/summary.json').read_text(encoding='utf-8'))
    assert report == [
        {'service': 'api', 'requests': 5, 'errors': 2,
         'max_latency_ms': 100, 'p95_latency_ms': 100},
        {'service': 'café', 'requests': 2, 'errors': 1,
         'max_latency_ms': 80, 'p95_latency_ms': 80},
        {'service': 'metrics', 'requests': 21, 'errors': 0,
         'max_latency_ms': 21, 'p95_latency_ms': 20},
        {'service': 'worker', 'requests': 3, 'errors': 2,
         'max_latency_ms': 90, 'p95_latency_ms': 90},
    ]


def test_report_types_and_schema():
    report = json.loads((ROOT / 'output/summary.json').read_text(encoding='utf-8'))
    assert isinstance(report, list) and len(report) == 4
    for item in report:
        assert set(item) == {'service', 'requests', 'errors',
                             'max_latency_ms', 'p95_latency_ms'}
        for field in set(item) - {'service'}:
            assert type(item[field]) is int


def test_inputs_are_unchanged():
    expected = INPUT_HASHES
    actual = {
        str(path.relative_to(ROOT / 'input')): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / 'input').rglob('*') if path.is_file()
    }
    assert actual == expected
