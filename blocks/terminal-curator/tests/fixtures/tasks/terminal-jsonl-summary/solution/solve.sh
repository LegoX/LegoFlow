#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json
import math
from collections import defaultdict
from pathlib import Path

root = Path('/app/task_file')
groups = defaultdict(list)
for path in sorted((root / 'input').rglob('*.jsonl')):
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        service = item.get('service')
        timestamp = item.get('timestamp')
        status = item.get('status')
        latency = item.get('latency_ms')
        if not isinstance(service, str) or not service.strip():
            continue
        if type(timestamp) is not int or timestamp < 1700000000:
            continue
        if type(status) is not int or not 100 <= status <= 599:
            continue
        if type(latency) is not int or latency < 0:
            continue
        groups[service.strip()].append((status, latency))
report = []
for service, rows in sorted(groups.items()):
    latencies = sorted(latency for _, latency in rows)
    report.append({
        'service': service,
        'requests': len(rows),
        'errors': sum(status >= 500 for status, _ in rows),
        'max_latency_ms': latencies[-1],
        'p95_latency_ms': latencies[math.ceil(0.95 * len(rows)) - 1],
    })
(root / 'output').mkdir(exist_ok=True)
(root / 'output' / 'summary.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
)
PY
