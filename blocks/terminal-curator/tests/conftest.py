"""Load the block adapter without putting vendored code on Python's path."""

import importlib.util
import sys
from pathlib import Path

import pytest

BLOCK = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures/tasks'
collect_ignore = ['fixtures']


@pytest.fixture(scope='session')
def adapter():
    path = BLOCK / 'scripts/terminal_curator.py'
    spec = importlib.util.spec_from_file_location('terminal_curator_under_test', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fixture_tasks():
    return FIXTURES
