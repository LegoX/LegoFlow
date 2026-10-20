#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import csv
from pathlib import Path

root = Path('/app/task_file')
winners = {}
for path in sorted((root / 'input').glob('*.csv')):
    with path.open(encoding='utf-8', newline='') as stream:
        for row_number, row in enumerate(csv.DictReader(stream), 1):
            email = row['email'].strip().casefold()
            timestamp = row['updated_epoch'].strip()
            if not email or not timestamp.isdecimal():
                continue
            order = (int(timestamp), path.name, row_number)
            if email not in winners or order > winners[email][0]:
                winners[email] = (order, row)
(root / 'output').mkdir(exist_ok=True)
with (root / 'output/contacts.csv').open('w', encoding='utf-8', newline='') as stream:
    writer = csv.writer(stream)
    writer.writerow(['email', 'name', 'updated_epoch'])
    for email, (order, row) in sorted(winners.items()):
        if row['status'].strip().casefold() != 'deleted':
            writer.writerow([email, row['name'].strip(), order[0]])
PY
