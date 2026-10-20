#!/usr/bin/env python3
"""Rebuild the explicitly synthetic docs demo; never discover runtime artifacts."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re

BLOCK = Path(__file__).resolve().parents[1]
DEMO = BLOCK / 'docs/public/dashboard_demo/terminal-curator'
NOTICE = (
    '<div role="note" style="padding:11px 16px;background:var(--c-accent);color:white;'
    'text-align:center;font-size:13px;line-height:1.5">'
    '<strong>Synthetic demo only.</strong> All tasks, runs, dates and metrics are illustrative '
    'fixtures. This is not a real curation, validation or solving result.</div>'
)


def validate_fixture(data):
    if not isinstance(data, dict) or data.get('demo') is not True or data.get('block') != 'terminal-curator':
        raise ValueError('docs demo must explicitly declare a synthetic terminal-curator fixture')
    private_keys = {'api_key', 'api_token', 'password', 'prompt', 'trace', 'instruction',
                    'solution', 'endpoint', 'host', 'pid', 'path', 'trial_path'}

    def visit(value, key=''):
        if isinstance(value, dict):
            for name, item in value.items():
                if name in private_keys:
                    raise ValueError('private content is not allowed in the docs demo fixture')
                visit(item, name)
        elif isinstance(value, list):
            for item in value:
                visit(item, key)
        elif isinstance(value, str):
            if value.startswith(('/', '~')) or '://' in value or re.search(r'\b(?:sk-|gh[pousr]_)[\w-]{12,}', value):
                raise ValueError('paths, URLs and credential-shaped values are not allowed in demo data')
            if key in {'id', 'run_id', 'batch_id'} and not value.startswith('demo-'):
                raise ValueError('every demo identity must use the demo- prefix')
            if key.endswith('_at') and not value.startswith('2025-01-01T'):
                raise ValueError('demo timestamps must use the declared fixed fixture date')
    visit(data)
    if data['published_task_count'] != len(data['published_tasks']):
        raise ValueError('demo publication count disagrees with its declared IDs')
    if any(not task_id.startswith('demo-') for task_id in data['published_tasks'] + data['verified_tasks']):
        raise ValueError('demo task lists must not contain real task IDs')


def render_demo():
    fixture = json.loads((DEMO / 'fixture.json').read_text())
    validate_fixture(fixture)
    spec = importlib.util.spec_from_file_location('terminal_dashboard_demo_renderer', BLOCK / 'dashboard/progress_monitor.py')
    board = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(board)
    # Only the checked-in, validated fixture reaches render. Never call snapshot
    # or inspect the operator's config/artifacts/environment to populate a demo.
    # Public fixture rendering must be identical on hosts with different secrets.
    board.redact = lambda value: value
    document = board.render(fixture, live=False)
    document = document.replace('<div class="layout">', NOTICE + '<div class="layout">', 1)
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Fail when the tracked demo needs regeneration.')
    args = parser.parse_args()
    target = DEMO / 'index.html'
    document = render_demo()
    if args.check:
        if not target.is_file() or target.read_text() != document:
            raise SystemExit('Synthetic dashboard demo is stale; run dashboard/build_demo.py.')
        print('Synthetic dashboard demo matches its fixture and current renderer.')
    else:
        target.write_text(document)
        print('Rebuilt the declared synthetic dashboard demo; no runtime artifacts were read.')


if __name__ == '__main__':
    main()
