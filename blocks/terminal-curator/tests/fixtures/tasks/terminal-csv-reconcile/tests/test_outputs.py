import csv
import hashlib
from pathlib import Path

ROOT = Path('/app/task_file')
INPUT_HASHES = {'a.csv': '4cdf23ce9363e2944bff6e4be0da2f59df12012ed8dbbbab732dcf7acfbf3f7d',
 'z.csv': '77d0f7922e52a6840ba1738d6a569e2b2a3c69ff1e2ed0fbc9bec3bbd7342200'}


def read_contacts():
    with (ROOT / 'output/contacts.csv').open(encoding='utf-8', newline='') as stream:
        return list(csv.reader(stream))


def test_winners_order_and_csv_quoting():
    assert read_contacts() == [
        ['email', 'name', 'updated_epoch'],
        ['alice@example.com', 'Alice, newest', '10'],
        ['bob@example.com', 'Bob current', '101'],
        ['carol@example.com', 'Carol\nrestored', '122'],
        ['new@example.com', 'New contact', '0'],
        ['tie@example.com', 'Tie source z last', '130'],
    ]


def test_deleted_and_invalid_rows_are_omitted():
    rows = read_contacts()
    emails = [row[0] for row in rows[1:]]
    assert len(emails) == len(set(emails)) == 5
    assert 'gone@example.com' not in emails
    assert not any(email.startswith(('invalid@', 'negative@', 'badplus@', 'badfloat@'))
                   for email in emails)


def test_inputs_are_unchanged():
    expected = INPUT_HASHES
    actual = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / 'input').iterdir() if path.is_file()
    }
    assert actual == expected
