#!/usr/bin/env bash
# Root case 12: public-release text must not reintroduce internal identifiers,
# non-English CJK text, private network addresses, live secrets, or legacy
# product branding.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY'
from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
self_path = "tests/cases/12_public_release_safety.sh"

index = subprocess.run(
    ["git", "-C", str(root), "ls-files", "--stage", "-z"],
    capture_output=True,
    check=True,
    text=True,
).stdout

tracked: list[tuple[str, str]] = []
for entry in index.split("\0"):
    if not entry:
        continue
    metadata, rel = entry.split("\t", 1)
    mode = metadata.split()[0]
    dashboard_owned = (
        "/dashboard/" in rel
        or rel.endswith("/skills/dashboard/SKILL.md")
        or rel.endswith("/docs/content/docs/dashboard.mdx")
        or rel.endswith("/docs/content/docs/advanced-usages/task-tagging.mdx")
    )
    if mode == "160000" or rel == self_path or dashboard_owned:
        continue
    tracked.append((mode, rel))

cjk = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff"
    "\u3040-\u30ff\uac00-\ud7af]"
)

private_networks = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "1" + "0.0.0.0/8",
        "172." + "16.0.0/12",
        "192." + "168.0.0/16",
    )
)
ipv4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

personal_markers = (
    "hao" + "li",
    "ywx" + "zml3j",
    "hk01" + "dgx",
)
internal_path_markers = (
    "/" + "gpufs" + "/",
    "/" + "home" + "/" + personal_markers[0],
    "/" + "Users" + "/" + personal_markers[0],
)

legacy_brand = "SWE" + "-Lego-" + "Live"
legacy_re = re.compile(re.escape(legacy_brand), re.IGNORECASE)
root_repo_url = "https://github.com/SWE-Lego/" + legacy_brand
allowed_root_url = re.compile(
    re.escape(root_repo_url) + r"(?=$|[\s)\]}>\"',;])"
)
legacy_component_markers = (
    "swe" + "gen",
    "swe-" + "live",
    "Lego" + "X",
)

credential_patterns = (
    ("AWS access key", re.compile(r"\bA[K]IA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bg[h][pousr]_[A-Za-z0-9_]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("OpenAI-style key", re.compile(r"\bs[k]-[A-Za-z0-9_-]{20,}\b")),
    ("Hugging Face token", re.compile(r"\bh[f]_[A-Za-z0-9]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)
private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
generic_assignment = re.compile(
    r"""(?im)^[ \t]*["']?
        (?:account[_-]?id|api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)
        ["']?[ \t]*[:=][ \t]*
        (?:"([^"\n]+)"|'([^'\n]+)'|([A-Za-z0-9][A-Za-z0-9_+=/-]{7,}))""",
    re.VERBOSE,
)
safe_value = re.compile(
    r"^(?:|none|null|local|human|dummy(?:[-_].*)?|fake(?:[-_].*)?|"
    r"test(?:[-_].*)?|placeholder(?:[-_].*)?|changeme|unused|"
    r"PENDING_SET_BY_.*|<.*>)$",
    re.IGNORECASE,
)

findings: list[str] = []


def add(rel: str, text: str, start: int, kind: str) -> None:
    line = text.count("\n", 0, start) + 1
    findings.append(f"{rel}:{line}: {kind}")


for mode, rel in tracked:
    path = root / rel
    if not path.exists() and not path.is_symlink():
        continue
    try:
        if mode == "120000" or path.is_symlink():
            raw = os.readlink(path).encode()
        else:
            raw = path.read_bytes()
    except OSError:
        continue
    if b"\0" in raw:
        continue
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        continue

    searchable = rel + "\n" + text
    # Published dashboard snapshots are captured output, not prose we write:
    # their CJK is the boards' own bilingual UI strings plus verbatim task and
    # issue text, and their legacy identifiers are config keys the boards still
    # read. Those two checks would only ever fire on content we cannot reword,
    # so they are waived here. Every other check -- credentials, private keys,
    # internal addresses, personal paths -- still applies, because a snapshot is
    # exactly where a real leak would hide.
    demo_snapshot = "/dashboard_demo/" in rel
    # The Chinese README is a deliberate translation, so CJK is its whole point.
    # The English README may name it, and nothing else may.
    cjk_waived = demo_snapshot or rel == "README_zh.md"
    if rel == "README.md":
        searchable = searchable.replace("[中文版](./README_zh.md)", "")
    # PR #89 owns the remaining legacy Dashboard migration surfaces. Ignore
    # their exact compatibility identifiers here so PR #88 can stay conflict
    # free while continuing to scan the rest of each mixed-purpose file.
    for dashboard_legacy_marker in (
        "swegen_progress_cloudflare.env",
        "swe-databoard.pages.dev",
        "SWEGEN_HOME",
    ):
        searchable = searchable.replace(dashboard_legacy_marker, "")

    match = cjk.search(searchable)
    if match and not cjk_waived:
        add(rel, searchable, match.start(), "CJK text is not allowed")

    folded = searchable.casefold()
    for marker in personal_markers:
        pos = folded.find(marker.casefold())
        if pos >= 0:
            add(rel, searchable, pos, "personal or internal identifier")
    for marker in internal_path_markers:
        pos = searchable.find(marker)
        if pos >= 0:
            add(rel, searchable, pos, "internal absolute path")

    for match in ipv4.finditer(searchable):
        try:
            address = ipaddress.ip_address(match.group())
        except ValueError:
            continue
        if any(address in network for network in private_networks):
            add(rel, searchable, match.start(), "private IPv4 address")

    without_allowed_url = allowed_root_url.sub("", searchable)
    match = legacy_re.search(without_allowed_url)
    if match and not demo_snapshot:
        add(rel, without_allowed_url, match.start(), "legacy product brand")
    if not demo_snapshot:
        for marker in legacy_component_markers:
            pos = folded.find(marker.casefold())
            if pos >= 0:
                add(rel, searchable, pos, "legacy component brand")

    if private_key_marker in searchable:
        add(
            rel,
            searchable,
            searchable.index(private_key_marker),
            "private key material",
        )
    for label, pattern in credential_patterns:
        match = pattern.search(searchable)
        if match:
            add(rel, searchable, match.start(), label)
    for match in generic_assignment.finditer(searchable):
        value = next(group for group in match.groups() if group is not None).strip()
        if (
            len(value) < 8
            or safe_value.match(value)
            or "$" in value
            or value.startswith("{env:")
            or value.startswith(("./", "../"))
            or searchable[match.end() : match.end() + 1] in ".([{"
        ):
            continue
        add(rel, searchable, match.start(), "possible committed credential")

if findings:
    print("FAIL: public-release safety violations found:", file=sys.stderr)
    for finding in findings:
        print(f"       {finding}", file=sys.stderr)
    sys.exit(1)

print(f"PASS: public-release safety scan checked {len(tracked)} tracked files")
PY
