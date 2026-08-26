#!/usr/bin/env python3
"""Write a secret-redacted YAML configuration snapshot for run archives."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml


SECRET_KEY = re.compile(
    r"(^|_)(api_key|master_key|private_key|auth_token|access_token|github_token|hf_token|token|password|passwd|secret|credential|credentials|api_base|api_base_url|anthropic_base_url)$",
    re.IGNORECASE,
)
TOKEN_VALUE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|hf_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b"
)


def redact_string(value: str) -> str:
    value = TOKEN_VALUE.sub("<redacted>", value)
    if "://" not in value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if "@" not in parsed.netloc:
        return value
    host = parsed.netloc.rsplit("@", 1)[1]
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def redact(value, key: str = ""):
    if SECRET_KEY.search(key):
        if isinstance(value, dict):
            return {child_key: "" for child_key in value}
        if isinstance(value, list):
            return []
        return ""
    if isinstance(value, dict):
        return {child_key: redact(child_value, str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_string(value)
    return value


def main() -> int:
    source, destination = map(Path, sys.argv[1:3])
    config = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    destination.write_text(
        yaml.safe_dump(redact(config), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
