#!/usr/bin/env bash

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLI="$ROOT_DIR/bin/legoflow"

[[ -x "$CLI" ]] || { echo "FAIL: CLI is not executable: $CLI" >&2; exit 1; }
help_output="$($CLI --help)"
for command in check setup setup-all run dashboard dashboard-all create collect-prs create-tasks; do
  grep -q "${command}" <<<"$help_output" || { echo "FAIL: CLI help omits ${command}" >&2; exit 1; }
done

python3 - "$ROOT_DIR" <<'PY'
from importlib.machinery import SourceFileLoader
import sys
from pathlib import Path

root = Path(sys.argv[1])
module = SourceFileLoader("legoflow_cli", str(root / "bin/legoflow")).load_module()
assert module.BLOCKS == ("curator", "tracer", "trainer", "evaluator")
parser = module.build_parser()
assert parser.parse_args(["check", "curator"]).block == "curator"
assert parser.parse_args(["run", "tracer", "--", "--no-dryrun"]).args == ["--no-dryrun"]
assert parser.parse_args(["create"]).command == "create"
assert module.normalize_slash_command(["/root:check"]) == ["check", "--full"]
assert module.normalize_slash_command(["/root:setup"]) == ["setup-all"]
assert module.normalize_slash_command(["/root:run"]) == ["run"]
assert module.normalize_slash_command(["/root:dashboard"]) == ["dashboard-all"]
assert module.normalize_slash_command(["/root:create"]) == ["create"]
assert module.normalize_slash_command(["/curator:check"]) == ["check", "curator"]
assert module.normalize_slash_command(["/curator:setup"]) == ["setup", "curator"]
assert module.normalize_slash_command(["/tracer:run", "--", "--no-dryrun"]) == ["run", "tracer", "--", "--no-dryrun"]
assert module.normalize_slash_command(["/curator:collect-prs"]) == ["collect-prs"]
for block in ("tracer", "trainer", "evaluator"):
    for action in ("check", "setup", "run", "dashboard"):
        assert module.normalize_slash_command([f"/{block}:{action}"])[:2] == [action, block]
print("PASS: shared CLI parser and command surface")
PY
