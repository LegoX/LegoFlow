#!/usr/bin/env python3
"""Verify that `import harbor` resolves to the managed repos/harbor checkout."""

from __future__ import annotations

import importlib
import os
from pathlib import Path


def main() -> int:
    repo_raw = os.environ.get("HARBOR_EDITABLE_ROOT")
    if not repo_raw:
        print("missing_env:HARBOR_EDITABLE_ROOT")
        return 0

    repo = Path(repo_raw).resolve()
    try:
        harbor = importlib.import_module("harbor")
    except Exception as exc:  # pragma: no cover - diagnostic script
        print(f"import_error:{exc}")
        return 0

    module_path = Path(harbor.__file__).resolve()
    try:
        module_path.relative_to(repo)
    except ValueError:
        print(f"mismatch:{module_path}")
    else:
        print(f"ok:{module_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
