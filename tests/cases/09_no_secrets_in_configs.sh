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
# Self-declaring non-secrets. Some endpoints (notably a locally served vLLM)
# reject an empty api_key, so the configs carry an obviously fake one — e.g.
# `dummy-key`. The prefix is the declaration of intent; a real credential never
# starts this way.
FAKE_VALUE = re.compile(r"^(dummy|fake|test|placeholder|unused|not-?used|changeme)[-_a-z0-9]*$",
                        re.IGNORECASE)
SECRET_KEYS = {
    "api_key", "api_base_url", "anthropic_base_url", "auth_token", "token",
    "password", "pwd", "wandb_api_key", "hf_token", "key", "ip", "user",
    "model", "pr_model", "task_model",
    # root config.yaml -> runtime_info.input.{cloudflare,docker}: tree-wide
    # optional credentials. They are read from the environment at run time, so
    # the committed value must always be empty.
    "account_id", "api_token", "username",
}
# Only these keys are host/URL-shaped; the model names are checked for keys only.
URL_KEYS = {"api_base_url", "anthropic_base_url"}
HOST_KEYS = {"ip"}
# Opaque credentials: no format to pattern-match against, so ANY non-placeholder
# value fails. Without this, a key that is in SECRET_KEYS but is neither URL- nor
# host-shaped falls through every branch below and is silently accepted unless it
# happens to start with a known vendor prefix — which a Cloudflare token
# (40+ chars of base62), a Cloudflare account id (32 hex chars), or a registry
# username never does.
OPAQUE_SECRET_KEYS = {
    "account_id", "api_token", "username", "password", "pwd",
    "api_key", "auth_token", "token", "wandb_api_key", "hf_token",
}
KEYISH = re.compile(r"^(sk-|ghp_|gho_|github_pat_|xox|AKIA|AIza)")


def is_local_url(v: str) -> bool:
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])(:\d+)?", v))


def is_private_host(v: str) -> bool:
    try:
        return ipaddress.ip_address(v).is_private or ipaddress.ip_address(v).is_loopback
    except ValueError:
        return False


def live_overlay(config_path: Path):
    """Is this config currently swapped out by a running smoke?

    A smoke overlays the production config with its own and injects live
    endpoints into the copy, so during a run the working tree legitimately holds
    values this check would otherwise reject. Both smoke flavours leave a backup
    next to (or beside) the config, named with the owning process's pid:

        subblock/<b>/config.yaml.root-smoke-bak.<pid>      root chain
        subblock/<b>/tests/smoke/.config.yaml.backup.<pid> block smoke

    The pid is what makes this safe: a backup whose process is gone is debris
    from a crashed run, and must NOT suppress the check — otherwise one crashed
    smoke would silently blind this case forever.
    """
    candidates = list(config_path.parent.glob(config_path.name + ".root-smoke-bak.*"))
    candidates += list((config_path.parent / "tests" / "smoke").glob(".config.yaml.backup.*"))
    for bak in candidates:
        suffix = bak.name.rsplit(".", 1)[-1]
        if not suffix.isdigit():
            continue
        if Path(f"/proc/{suffix}").exists():
            return bak.name
    return None


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
skipped = []
stale = []
for rel in paths:
    p = root / rel
    if not p.is_file():
        continue
    held_by = live_overlay(p)
    if held_by:
        skipped.append((rel, held_by))
        continue
    # A backup with a dead pid means a smoke died mid-run: the file may still
    # hold injected values, so it is checked normally, but say why it looks odd.
    for bak in list(p.parent.glob(p.name + ".root-smoke-bak.*")):
        stale.append((rel, bak.name))
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
        if v.lower() in PLACEHOLDERS or v.startswith("PENDING_SET_BY_") or FAKE_VALUE.match(v):
            continue
        if KEYISH.match(v):
            bad.append(f"{rel}: {dotted} looks like a live API key")
            continue
        if leaf in OPAQUE_SECRET_KEYS:
            # Never echo the value — this output goes to CI logs.
            bad.append(f"{rel}: {dotted} holds a non-empty value ({len(v)} chars); "
                       f"credentials must be supplied via the environment, not committed")
            continue
        if leaf in URL_KEYS:
            if not is_local_url(v):
                bad.append(f"{rel}: {dotted} = {v!r} is a non-local endpoint")
            continue
        if leaf in HOST_KEYS:
            if not is_private_host(v):
                bad.append(f"{rel}: {dotted} = {v!r} is a routable host")
            continue

for rel, bak in skipped:
    print(f"INFO: skipping {rel} — overlaid by a running smoke ({bak})")
for rel, bak in stale:
    print(f"WARN: {rel} has a leftover backup from a dead run ({bak}); "
          f"it may still hold injected values — checking it anyway")

if bad:
    print("FAIL: credential/host values found in tracked configs:", file=sys.stderr)
    for b in bad:
        print(f"       {b}", file=sys.stderr)
    print("", file=sys.stderr)
    print("       Move the value into /gpufs/haoli/cicd/shared/.env and leave the", file=sys.stderr)
    print("       field as \"\" — scripts/inject_smoke_secrets.py fills it at run time.", file=sys.stderr)
    sys.exit(1)

print(f"PASS: no credential/host values in {len(paths) - len(skipped)} tracked configs"
      + (f" ({len(skipped)} skipped: smoke in progress)" if skipped else ""))
PY
