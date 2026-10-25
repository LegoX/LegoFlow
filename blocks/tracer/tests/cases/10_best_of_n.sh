#!/usr/bin/env bash
# CI test 10: best-of-n sampling knob and cross-job trajectory selection.
#
# Part (a) checks the config default (samples_per_task: 1 = off). Part (b)
# builds two synthetic sample jobs under a temp sft-root, where task A is
# behaviorally identical across jobs (same tool-call sequence, same
# whitespace-normalized assistant text, different raw bytes and metadata) and
# asserts scripts/select_trajectories.py dedups it, keeps B and C, and writes
# lf.json / lf.stats.json / selection_manifest.json with the exact schemas the
# converter and dashboard use. A cap-1 run over two distinct-behavior records
# must keep only the higher composite_score copy. A pair with identical text
# but a different tool-call ORDER must survive as two records, guarding the
# tool-sequence component of the fingerprint.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"
SELECT="$BLOCK_DIR/scripts/select_trajectories.py"

# (a) config: harbor_job.samples_per_task present with default 1 (off).
grep -Eq '^[[:space:]]*samples_per_task:[[:space:]]*1([[:space:]]|$)' "$CONFIG" || {
  echo "FAIL: harbor_job.samples_per_task default 1 not found in config.yaml" >&2
  exit 1
}
python3 - "$CONFIG" <<'PY' || exit 1
import sys
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed in runner's python3", file=sys.stderr)
    raise SystemExit(1)
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
harbor_job = ((cfg.get("runtime_info") or {}).get("input") or {}).get("harbor_job") or {}
if harbor_job.get("samples_per_task") != 1:
    print(f"FAIL: harbor_job.samples_per_task must default to 1 (got {harbor_job.get('samples_per_task')!r})", file=sys.stderr)
    raise SystemExit(1)
PY
[[ -x "$SELECT" ]] || { echo "FAIL: $SELECT is missing or not executable" >&2; exit 1; }

# (b) + (c) functional selection over synthetic fixtures.
python3 - "$SELECT" <<'PY' || exit 1
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

select = Path(sys.argv[1])


def lf_record(instance, composite, tokens, assistant):
    return {
        "messages": [
            {"role": "user", "content": f"fix {instance}"},
            {"role": "assistant", "content": assistant},
        ],
        "meta_info": {
            "unique_info": {
                "_instance_id": instance,
                "_agent_type": "claude_code",
                "_score": {
                    "composite_score": composite,
                    "assistant_turns": 2,
                    "total_tool_calls": 3,
                    "c1_tool_success_rate": 0.9,
                },
                "_usage": {"total_tokens": tokens},
            }
        },
    }


def im_record(instance, assistant, tools):
    """IM variant of an LF record: same meta_info, tool_calls on messages."""
    record = lf_record(instance, 0, 0, assistant)
    messages = [{"role": "user", "content": f"fix {instance}"}]
    for tool in tools:
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call-{tool}",
                "type": "function",
                "function": {"name": tool, "arguments": "{}"},
            }],
        })
    messages.append({"role": "assistant", "content": assistant})
    record["messages"] = messages
    record["version"] = "2.1.118"
    return record


def write_job(root, job, lf_records, im_records):
    job_dir = root / job
    job_dir.mkdir(parents=True)
    (job_dir / "lf.json").write_text(json.dumps(lf_records, indent=2) + "\n", encoding="utf-8")
    (job_dir / "im.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in im_records), encoding="utf-8"
    )


def run_selection(out_dir, *cli):
    run = subprocess.run(
        [sys.executable, str(select), *cli, "--out-dir", str(out_dir)],
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        raise AssertionError(f"selection script failed (rc={run.returncode}): {run.stderr.strip()}")
    return run


with tempfile.TemporaryDirectory() as raw_tmp:
    tmp = Path(raw_tmp)

    # job1: A (0.9) + B (0.8). job2: A behaviorally identical to job1's A
    # (same tool sequence, same normalized text, different raw whitespace and
    # score) + C (0.6).
    sft = tmp / "sft"
    write_job(
        sft, "job1",
        [lf_record("A", 0.9, 1000, "Fixed A\nvia patch  work"), lf_record("B", 0.8, 900, "Fixed B via patch work")],
        [im_record("A", "Fixed A\nvia patch  work", ["shell", "edit"]), im_record("B", "Fixed B via patch work", ["shell"])],
    )
    write_job(
        sft, "job2",
        [lf_record("A", 0.7, 800, "Fixed A via  patch work"), lf_record("C", 0.6, 700, "Fixed C via patch work")],
        [im_record("A", "Fixed A via  patch work", ["shell", "edit"]), im_record("C", "Fixed C via patch work", ["shell", "shell"])],
    )

    out = tmp / "out"
    run_selection(out, "--jobs", "job1,job2", "--sft-root", str(sft), "--per-task-cap", "2")

    lf = json.loads((out / "lf.json").read_text(encoding="utf-8"))
    if not isinstance(lf, list):
        raise AssertionError(f"lf.json is not a JSON array: {type(lf)}")
    ids = [r["meta_info"]["unique_info"]["_instance_id"] for r in lf]
    if sorted(ids) != ["A", "B", "C"]:
        raise AssertionError(f"expected A deduped to one record plus B and C once each, got {ids}")
    kept_a = next(r for r in lf if r["meta_info"]["unique_info"]["_instance_id"] == "A")
    if kept_a["meta_info"]["unique_info"]["_usage"]["total_tokens"] != 1000:
        raise AssertionError(f"dedup kept the wrong A copy (expected job1's 1000-token record): {kept_a}")

    stats = json.loads((out / "lf.stats.json").read_text(encoding="utf-8"))
    for field in ("count", "token_lens", "n_turns", "scores", "total_tokens", "tool_call_errors"):
        if field not in stats:
            raise AssertionError(f"lf.stats.json is missing the dashboard field {field!r}: {stats}")
    if stats["count"] != 3 or stats["total_tokens"] != 2600:
        raise AssertionError(f"stats aggregates are wrong: {stats}")

    manifest = json.loads((out / "selection_manifest.json").read_text(encoding="utf-8"))
    if manifest["dropped_duplicates"] < 1:
        raise AssertionError(f"behaviorally identical A was not counted as dropped duplicate: {manifest}")
    if manifest["per_task"]["A"][0]["source_job"] != "job1":
        raise AssertionError(f"dedup did not keep the highest-composite A copy: {manifest['per_task']['A']}")
    digest = hashlib.sha256((out / "lf.json").read_bytes()).hexdigest()
    if manifest["sha256_lf"] != digest:
        raise AssertionError(f"manifest sha256_lf {manifest['sha256_lf']} != actual {digest}")

    # Unknown job names must exit non-zero with the job in the message.
    bad = subprocess.run(
        [sys.executable, str(select), "--jobs", "missing-job", "--sft-root", str(sft),
         "--out-dir", str(tmp / "nope")],
        capture_output=True, text=True,
    )
    if bad.returncode == 0 or "missing-job" not in bad.stderr:
        raise AssertionError(f"unknown job did not fail loudly: rc={bad.returncode} stderr={bad.stderr!r}")

    # Cap 1 over one task with two distinct-behavior records across jobs:
    # only the higher composite_score survives.
    sft2 = tmp / "sft2"
    write_job(
        sft2, "job3",
        [lf_record("X", 0.6, 500, "used shell first")],
        [im_record("X", "used shell first", ["shell", "edit"])],
    )
    write_job(
        sft2, "job4",
        [lf_record("X", 0.95, 650, "read the file first")],
        [im_record("X", "read the file first", ["read", "edit"])],
    )
    out1 = tmp / "out1"
    run_selection(out1, "--jobs", "job3,job4", "--sft-root", str(sft2), "--per-task-cap", "1")
    lf1 = json.loads((out1 / "lf.json").read_text(encoding="utf-8"))
    ids1 = [r["meta_info"]["unique_info"]["_instance_id"] for r in lf1]
    if ids1 != ["X"]:
        raise AssertionError(f"cap-1 selection must keep exactly one X record, got {ids1}")
    manifest1 = json.loads((out1 / "selection_manifest.json").read_text(encoding="utf-8"))
    if manifest1["per_task"]["X"][0]["composite_score"] != 0.95:
        raise AssertionError(f"cap-1 kept the lower-scoring behavior: {manifest1['per_task']['X']}")

    # Identical normalized text but a different tool-call ORDER: the
    # fingerprints must differ, so both records survive under cap 2 (guards
    # the tool-sequence half of the fingerprint, which text alone cannot
    # distinguish).
    sft3 = tmp / "sft3"
    write_job(
        sft3, "job5",
        [lf_record("Y", 0.7, 500, "same normalized text")],
        [im_record("Y", "same normalized text", ["shell", "edit"])],
    )
    write_job(
        sft3, "job6",
        [lf_record("Y", 0.8, 600, "same normalized text")],
        [im_record("Y", "same normalized text", ["edit", "shell"])],
    )
    out2 = tmp / "out2"
    run_selection(out2, "--jobs", "job5,job6", "--sft-root", str(sft3), "--per-task-cap", "2")
    lf2 = json.loads((out2 / "lf.json").read_text(encoding="utf-8"))
    ids2 = [r["meta_info"]["unique_info"]["_instance_id"] for r in lf2]
    if ids2 != ["Y", "Y"]:
        raise AssertionError(f"same text + different tool order must keep 2 records, got {ids2}")
    manifest2 = json.loads((out2 / "selection_manifest.json").read_text(encoding="utf-8"))
    if manifest2["dropped_duplicates"] != 0:
        raise AssertionError(f"different tool order was wrongly deduped: {manifest2}")

print("ok")
PY

echo "PASS: best-of-n samples_per_task knob and select_trajectories.py"
