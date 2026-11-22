#!/usr/bin/env bash
# CI test 11: multi-scaffold campaign — config default-off, merge_sft_data.py
# functional behavior, and start.sh's fail-loud scaffolds validation (through
# TRAJGEN_CONFIG sandboxes; no Docker/network needed).

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

work="$(mktemp -d "${TMPDIR:-/tmp}/ms11XXXXXX")"
trap 'rm -rf "$work"' EXIT

python3 - "$BLOCK_DIR" "$work" <<'PY'
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed in runner's python3", file=sys.stderr)
    raise SystemExit(1)

block = Path(sys.argv[1])
work = Path(sys.argv[2])
helper = block / "scripts" / "validate_scaffolds.py"
merge = block / "scripts" / "merge_sft_data.py"
start = block / "scripts" / "start.sh"


def run(cmd, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def out_of(result):
    return result.stdout + result.stderr


# --- (a) shipped config: campaign knob defaults to off -----------------------
shipped = yaml.safe_load((block / "config.yaml").read_text(encoding="utf-8"))
scaffolds = shipped["runtime_info"]["input"]["agent"].get("scaffolds", None)
if scaffolds is not None:
    raise AssertionError(f"agent.scaffolds must default to null/absent, got {scaffolds!r}")
result = run([sys.executable, str(helper)])
if result.returncode != 0 or result.stdout or result.stderr:
    raise AssertionError(f"helper must exit 0 silently on the shipped config: {out_of(result)}")

# --- (b) merge_sft_data.py functional -----------------------------------------
def record(iid, scaffold, comp, tokens, turns=6, calls=10, rate=0.9):
    score = {"assistant_turns": turns, "total_tool_calls": calls, "c1_tool_success_rate": rate}
    if comp is not None:
        score["composite_score"] = comp
    if scaffold is not None:
        # Real converter output records the scaffold here; _agent_type is a
        # converter constant ("main"), not the scaffold identity.
        score["scaffold"] = scaffold
    return {
        "messages": [{"role": "user", "content": f"fix {iid}"}] * turns,
        "meta_info": {"unique_info": {
            "_instance_id": iid,
            "_agent_type": "main",
            "_score": score,
            "_usage": {"total_tokens": tokens},
        }},
    }


# Scaffold-bearing job names, as a real campaign produces them: records with
# no _score.scaffold must get their label derived from the job name.
JOB_A = "merged-custom-claude-code-2.1.118-m-20260101000000"
JOB_B = "merged-custom-openhands-sdk-1.33.0-m-20260101000000"

# Overlapping _instance_ids across two scaffolds; task-2 is a full tie broken
# by the scaffold label, task-4 a same-composite pair with no _score.scaffold
# (labels derived from the job names) again broken by the label.
job_a_rows = [
    record("task-1", "claude_code", 0.9, 1000, turns=6),
    record("task-2", "claude_code", 0.5, 500, turns=4),
    record("task-4", None, 0.4, 400, turns=5),
]
job_b_rows = [
    record("task-1", "openhands_sdk", 0.7, 800, turns=5),
    record("task-2", "openhands_sdk", 0.5, 600, turns=3),
    record("task-3", "openhands_sdk", None, 700, turns=7),
    record("task-4", None, 0.4, 300, turns=5),
]
all_rows = job_a_rows + job_b_rows
sft = work / "sft"
for name, rows in ((JOB_A, job_a_rows), (JOB_B, job_b_rows)):
    (sft / name).mkdir(parents=True)
    (sft / name / "lf.json").write_text(json.dumps(rows), encoding="utf-8")

result = run([sys.executable, str(merge),
              "--jobs", f"{JOB_A},{JOB_B}",
              "--sft-root", str(sft), "--out-dir", str(work / "merged")])
if result.returncode != 0:
    raise AssertionError(f"plain merge failed: {out_of(result)}")

merged_dir = work / "merged"
lf = json.loads((merged_dir / "lf.json").read_text(encoding="utf-8"))
if len(lf) != len(all_rows):
    raise AssertionError(f"plain concat must keep every record: {len(lf)} != {len(all_rows)}")
manifest = json.loads((merged_dir / "merge_manifest.json").read_text(encoding="utf-8"))


def scaffolds_for(task):
    return sorted(entry["scaffold"] for entry in manifest["per_task"][task])


for task in ("task-1", "task-2"):
    if scaffolds_for(task) != ["claude_code", "openhands_sdk"]:
        raise AssertionError(f"{task} must keep BOTH scaffolds' records: {scaffolds_for(task)}")
if scaffolds_for("task-4") != ["claude_code", "openhands_sdk"]:
    raise AssertionError(f"task-4 labels must derive from the job names when _score.scaffold "
                         f"is absent: {scaffolds_for('task-4')}")
if scaffolds_for("task-3") != ["openhands_sdk"]:
    raise AssertionError(f"task-3 single-scaffold record was altered: {scaffolds_for('task-3')}")

stats = json.loads((merged_dir / "lf.stats.json").read_text(encoding="utf-8"))
for field in ("count", "token_lens", "n_turns", "scores", "total_tokens", "tool_call_errors"):
    if field not in stats:
        raise AssertionError(f"lf.stats.json is missing dashboard field '{field}': {stats}")
if stats["count"] != len(all_rows):
    raise AssertionError(f"stats count {stats['count']} != {len(all_rows)}")
tokens = [row["meta_info"]["unique_info"]["_usage"]["total_tokens"] for row in all_rows]
if stats["token_lens"] != {"min": min(tokens), "max": max(tokens),
                           "mean": round(sum(tokens) / len(tokens), 2)}:
    raise AssertionError(f"token_lens aggregates wrong: {stats['token_lens']}")
if stats["total_tokens"] != sum(tokens):
    raise AssertionError(f"total_tokens {stats['total_tokens']} != {sum(tokens)}")
turns = [row["meta_info"]["unique_info"]["_score"]["assistant_turns"] for row in all_rows]
if stats["n_turns"] != {"min": float(min(turns)), "max": float(max(turns)),
                        "mean": round(sum(turns) / len(turns), 2)}:
    raise AssertionError(f"n_turns aggregates wrong: {stats['n_turns']}")
expected_rate = round(sum(10 * (1 - 0.9) for _ in all_rows) / (10 * len(all_rows)), 4)
if stats["tool_call_errors"].get("error_rate") != expected_rate:
    raise AssertionError(f"tool_call_errors.error_rate {stats['tool_call_errors']} != {expected_rate}")

if manifest["per_scaffold_counts"] != {"claude_code": 3, "openhands_sdk": 4}:
    raise AssertionError(f"per_scaffold_counts wrong: {manifest['per_scaffold_counts']}")
digest = hashlib.sha256((merged_dir / "lf.json").read_bytes()).hexdigest()
if manifest["sha256_lf"] != digest:
    raise AssertionError("manifest sha256_lf does not match lf.json bytes")

# --per-task-cap 1: highest composite per task; ties via the scaffold label
# then source job.
result = run([sys.executable, str(merge),
              "--jobs", f"{JOB_A},{JOB_B}",
              "--sft-root", str(sft), "--out-dir", str(work / "cap1"),
              "--per-task-cap", "1"])
if result.returncode != 0:
    raise AssertionError(f"capped merge failed: {out_of(result)}")
capped = json.loads((work / "cap1" / "lf.json").read_text(encoding="utf-8"))
if len(capped) != 4:
    raise AssertionError(f"per-task-cap 1 must keep exactly one record per task: {len(capped)}")
cap_manifest = json.loads((work / "cap1" / "merge_manifest.json").read_text(encoding="utf-8"))
expected_keep = {
    "task-1": ("claude_code", JOB_A),    # 0.9 > 0.7
    "task-2": ("claude_code", JOB_A),    # tie 0.5/0.5 -> scaffold label asc
    "task-3": ("openhands_sdk", JOB_B),  # only record; missing score counts as 0
    "task-4": ("claude_code", JOB_A),    # job-name-derived labels tie on composite -> label asc
}
for task, (want_scaffold, want_job) in expected_keep.items():
    entry = cap_manifest["per_task"][task][0]
    if (entry["scaffold"], entry["source_job"]) != (want_scaffold, want_job):
        raise AssertionError(f"per-task-cap pick for {task} wrong: {entry} != "
                             f"({want_scaffold!r}, {want_job!r})")
if cap_manifest["selected_count"] != 4 or cap_manifest["per_task_cap"] != 1:
    raise AssertionError(f"capped manifest header wrong: {cap_manifest}")
if [e["rank"] for e in cap_manifest["per_task"]["task-2"]] != [1]:
    raise AssertionError(f"capped per_task ranks wrong: {cap_manifest['per_task']}")

# Error cases must exit non-zero with a clear message.
result = run([sys.executable, str(merge), "--jobs", "missing-job",
              "--sft-root", str(sft), "--out-dir", str(work / "x1")])
if result.returncode == 0 or "missing-job" not in out_of(result):
    raise AssertionError(f"unknown job must fail loudly: {out_of(result)}")
bad_dir = sft / "bad-job"
bad_dir.mkdir()
(bad_dir / "lf.json").write_text(json.dumps([{"messages": []}]), encoding="utf-8")
result = run([sys.executable, str(merge), "--jobs", "bad-job",
              "--sft-root", str(sft), "--out-dir", str(work / "x2")])
if result.returncode == 0 or "bad-job" not in out_of(result):
    raise AssertionError(f"malformed record must fail loudly: {out_of(result)}")
result = run([sys.executable, str(merge), "--jobs", JOB_A,
              "--sft-root", str(sft), "--out-dir", str(work / "x3"),
              "--per-task-cap", "-1"])
if result.returncode == 0:
    raise AssertionError("negative --per-task-cap must fail")

# --- (c) start.sh campaign validation via TRAJGEN_CONFIG sandbox --------------
CC = {"name": "custom-claude-code", "version": "2.1.118",
      "runtime_image": "docker.io/jierun/c-cc-2.1.118:v0.1",
      "runtime_host_path": "artifacts/agent-runtime/claude-code"}
OC = {"name": "custom-opencode", "version": "1.18.7",
      "runtime_image": "docker.io/jierun/c-oc-1.18.7:v0.1",
      "runtime_host_path": "artifacts/agent-runtime/opencode"}


def temp_config(name, entries):
    path = work / name
    doc = yaml.safe_load((block / "config.yaml").read_text(encoding="utf-8"))
    doc["runtime_info"]["input"]["agent"]["scaffolds"] = entries
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def start_with(config):
    env = dict(os.environ, TRAJGEN_CONFIG=str(config), TRAJGEN_PREPARE_TASKS="0")
    return run(["bash", str(start), "--dry-run-command"], env=env)

# Unknown scaffold name fails LOUD before any side effect (no preflight, no
# silent fall-through to the claude-code default branch).
bad_cfg = temp_config("bad.yaml", [CC, dict(CC, name="custom-terminus", version="9.9.9")])
result = start_with(bad_cfg)
combined = out_of(result)
if result.returncode == 0:
    raise AssertionError("start.sh must fail on an unknown scaffold name")
for needed in ("custom-terminus", "not a supported scaffold"):
    if needed not in combined:
        raise AssertionError(f"fail-loud message missing '{needed}': {combined}")
if "tracer preflight" in combined or "multi-scaffold campaign" in combined:
    raise AssertionError(f"validation must fail before preflight/staging: {combined}")

# A valid 2-scaffold campaign prints the banner with distinct job names and the
# ledger note (the run may still fail later at preflight on a repo-less runner;
# only the banner contract is asserted here).
good_cfg = temp_config("good.yaml", [CC, OC])
result = start_with(good_cfg)
combined = out_of(result)
if "=== tracer multi-scaffold campaign ===" not in combined:
    raise AssertionError(f"campaign banner missing: {combined}")
job_lines = [line for line in combined.splitlines() if "job name:" in line]
if len(job_lines) != 2:
    raise AssertionError(f"expected 2 job-name lines, got: {job_lines}")
if job_lines[0] == job_lines[1]:
    raise AssertionError(f"campaign job names must be distinct: {job_lines}")
if "custom-claude-code-2.1.118" not in job_lines[0] or "custom-opencode-1.18.7" not in job_lines[1]:
    raise AssertionError(f"job names must carry each scaffold's name+version: {job_lines}")
if "resolved once at launch" not in combined and "exclusion" not in combined:
    raise AssertionError(f"banner must state the ledger-exclusion semantics: {combined}")

# Helper rules: duplicate names and missing fields also fail loudly; --tsv
# drives one job per line with the identity fields.
result = run([sys.executable, str(helper), "--config", str(temp_config("dup.yaml", [CC, CC]))])
if result.returncode == 0 or "duplicated" not in out_of(result):
    raise AssertionError(f"duplicate scaffold names must fail: {out_of(result)}")
result = run([sys.executable, str(helper),
              "--config", str(temp_config("missing.yaml", [dict(CC, runtime_image="")]) )])
if result.returncode == 0 or "runtime_image" not in out_of(result):
    raise AssertionError(f"missing identity field must fail: {out_of(result)}")
# A two-component version written as a bare YAML number loses its trailing
# zero (1.20 -> 1.2) and would corrupt job names — must be refused.
result = run([sys.executable, str(helper),
              "--config", str(temp_config("floatver.yaml", [dict(CC, version=1.20)]) )])
if result.returncode == 0 or "quote" not in out_of(result):
    raise AssertionError(f"float version must be refused with a quote hint: {out_of(result)}")
# runtime_host_path: null is refused, empty is accepted (mount from image).
result = run([sys.executable, str(helper),
              "--config", str(temp_config("nullhost.yaml", [dict(CC, runtime_host_path=None)]) )])
if result.returncode == 0 or "runtime_host_path" not in out_of(result):
    raise AssertionError(f"null runtime_host_path must fail: {out_of(result)}")
result = run([sys.executable, str(helper),
              "--config", str(temp_config("emptyhost.yaml", [dict(CC, runtime_host_path="")]) )])
if result.returncode != 0 or "mounted from the image" not in result.stdout:
    raise AssertionError(f"empty runtime_host_path must validate and announce image mounting: {out_of(result)}")
result = run([sys.executable, str(helper), "--config", str(good_cfg), "--tsv"])
if result.returncode != 0:
    raise AssertionError(f"--tsv must succeed on a valid campaign: {out_of(result)}")
lines = result.stdout.strip().splitlines()
if len(lines) != 2 or any(len(line.split("\t")) != 4 for line in lines):
    raise AssertionError(f"--tsv must print one 4-field line per scaffold: {lines}")

print("PASS: multi-scaffold knob default-off, merge_sft_data.py, and fail-loud validation")
PY
