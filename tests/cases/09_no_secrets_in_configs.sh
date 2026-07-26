#!/usr/bin/env bash
# Root case 09: no credential or host value is committed in any tracked config.
#
# The smoke configs are tracked so they keep tracking schema changes, which
# means .gitignore cannot protect them — the only thing standing between a
# filled-in local config and the permanent git history is this check. It exists
# because exactly that happened: a real LLM endpoint and a remote host + SSH key
# path lived in tests/smoke/ from 2026-06-19 until this case was written.
#
# Credential fields must hold one of:
#   ""      -> supplied at run time (scripts/inject_smoke_secrets.py)
#   human   -> the operator must fill it before a production run
#   null    -> semantically unset
#   a loopback URL or an explicit PENDING_SET_BY_* marker
#
# Anything else — an API key, a routable host, a non-local URL — fails.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$ROOT_DIR"
python3 - "$ROOT_DIR" <<'PY' || exit 1
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(1)

root = Path(sys.argv[1])
# Ask git directly: piping into this heredoc would fight it for stdin.
out = subprocess.run(
    ["git", "-C", str(root), "ls-files", "-z", "--", "*config.yaml", "*config.yml"],
    capture_output=True, text=True, check=True,
).stdout
paths = [p for p in out.split("\0") if p]

# Values that are fine to commit.
PLACEHOLDERS = {"", "human", "none", "null", "local"}
SECRET_KEYS = {
    "api_key", "api_base_url", "anthropic_base_url", "auth_token", "token",
    "password", "pwd", "wandb_api_key", "hf_token", "key", "ip", "user",
    "model", "pr_model", "task_model",
}
# Only these keys are host/URL-shaped; the model names are checked for keys only.
URL_KEYS = {"api_base_url", "anthropic_base_url"}
HOST_KEYS = {"ip"}
KEYISH = re.compile(r"^(sk-|ghp_|gho_|github_pat_|xox|AKIA|AIza)")


def is_local_url(v: str) -> bool:
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])(:\d+)?", v))


def is_private_host(v: str) -> bool:
    try:
        return ipaddress.ip_address(v).is_private or ipaddress.ip_address(v).is_loopback
    except ValueError:
        return False


def walk(node, trail, out):
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, trail + [str(k)], out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, trail + [str(i)], out)
    else:
        out.append((".".join(trail), node))


bad = []
for rel in paths:
    p = root / rel
    if not p.is_file():
        continue
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        continue  # schema cases own parse errors
    leaves = []
    walk(data, [], leaves)
    for dotted, value in leaves:
        # meta_info.dependencies is inter-block wiring: its values are
        # "<block>.output.<key>" references, not endpoints or credentials.
        if dotted.startswith("meta_info.dependencies"):
            continue
        leaf = dotted.rsplit(".", 1)[-1]
        if leaf not in SECRET_KEYS or not isinstance(value, str):
            continue
        v = value.strip()
        if v.lower() in PLACEHOLDERS or v.startswith("PENDING_SET_BY_"):
            continue
        if KEYISH.match(v):
            bad.append(f"{rel}: {dotted} looks like a live API key")
            continue
        if leaf in URL_KEYS:
            if not is_local_url(v):
                bad.append(f"{rel}: {dotted} = {v!r} is a non-local endpoint")
            continue
        if leaf in HOST_KEYS:
            if not is_private_host(v):
                bad.append(f"{rel}: {dotted} = {v!r} is a routable host")
            continue

if bad:
    print("FAIL: credential/host values found in tracked configs:", file=sys.stderr)
    for b in bad:
        print(f"       {b}", file=sys.stderr)
    print("", file=sys.stderr)
    print("       Move the value into /gpufs/haoli/cicd/shared/.env and leave the", file=sys.stderr)
    print("       field as \"\" — scripts/inject_smoke_secrets.py fills it at run time.", file=sys.stderr)
    sys.exit(1)

print(f"PASS: no credential/host values in {len(paths)} tracked configs")
PY
