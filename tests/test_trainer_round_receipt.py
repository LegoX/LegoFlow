"""Trainer round receipt (RSI loop graft) — tests.

Covers blocks/trainer/scripts/round_receipt.py as a real CLI plus the guarded
STEP 3 wiring in blocks/trainer/scripts/train.sh:

  * deterministic checkpoint manifest hash over a mixed small/large fixture
  * large files (>= 4 MiB) contribute size only, small files content sha256
  * checkpoint_sha256 matches an independent hashlib recomputation
  * missing inputs (LF file / stats sidecar / trainer log) tolerated as nulls
  * --job-dir LF discovery prefers lf.json, then sidecar-backed json files
  * round resolution (--round > $LEGOFLOW_RSI_ROUND > 1)
  * the receipt's own --out is excluded from the manifest (idempotent re-runs)
  * train.sh passes `bash -n` and carries the guarded STEP 3 receipt call

No network, no Docker, no GPU; everything runs in tmp_path.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "blocks" / "trainer" / "scripts" / "round_receipt.py"
TRAIN_SH = REPO_ROOT / "blocks" / "trainer" / "scripts" / "train.sh"

HASH_SIZE_LIMIT = 4 * 1024 * 1024
ROUND_ENV = "LEGOFLOW_RSI_ROUND"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_receipt(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != ROUND_ENV}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *[str(a) for a in args]],
        capture_output=True,
        text=True,
        env=env,
    )


def recompute_manifest_sha(ckpt: Path, exclude: Path | None = None) -> str:
    """Independent reimplementation of the manifest hash (never the script's)."""
    exclude_resolved = exclude.resolve() if exclude is not None else None
    files = sorted(
        (p for p in ckpt.rglob("*") if p.is_file() and p.resolve() != exclude_resolved),
        key=lambda p: p.relative_to(ckpt).as_posix(),
    )
    lines = []
    for path in files:
        size = path.stat().st_size
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if size < HASH_SIZE_LIMIT else "-"
        rel = path.relative_to(ckpt).as_posix()
        lines.append(f"{rel}\t{size}\t{digest}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def make_run(tmp_path: Path, *, with_trainer_log: bool = True) -> tuple[Path, Path]:
    """(run_dir, checkpoint_dir) with mixed small/large files, like a real run."""
    run_dir = tmp_path / "model" / "demo_run"
    ckpt = run_dir / "checkpoint-100"
    (ckpt / "tokenizer").mkdir(parents=True)
    (ckpt / "config.json").write_text(json.dumps({"model_type": "qwen3", "architectures": ["Qwen3ForCausalLM"]}))
    (ckpt / "trainer_state.json").write_text(json.dumps({"global_step": 100, "epoch": 1.0}))
    (ckpt / "tokenizer" / "tokenizer_config.json").write_text(json.dumps({"vocab_size": 151936}))
    (ckpt / "model-00001-of-00002.safetensors").write_bytes(b"\0" * (HASH_SIZE_LIMIT + 1024))
    if with_trainer_log:
        make_trainer_log(run_dir)
    return run_dir, ckpt


def make_trainer_log(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    entries = [
        {"current_step": 10, "total_steps": 30, "loss": 0.9, "lr": 1e-4, "epoch": 0.33},
        {"current_step": 20, "total_steps": 30, "loss": 0.5, "lr": 1e-4, "epoch": 0.66},
        {"current_step": 30, "total_steps": 30, "loss": 0.123, "lr": 1e-4, "epoch": 1.0},
    ]
    (run_dir / "trainer_log.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )


def make_job_dir(tmp_path: Path) -> Path:
    """Tracer-style sft_data job dir: lf.json + its .stats.json sidecar."""
    job_dir = tmp_path / "sft_data" / "job1"
    job_dir.mkdir(parents=True)
    (job_dir / "lf.json").write_text(
        json.dumps([{"messages": [{"role": "user", "content": "hi"}]}]), encoding="utf-8"
    )
    (job_dir / "lf.stats.json").write_text(
        json.dumps({"count": 42, "total_tokens": 123456, "token_lens": {"max": 9, "min": 1, "mean": 3}}),
        encoding="utf-8",
    )
    return job_dir


def load_receipt(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Receipt content, determinism, sha correctness
# ---------------------------------------------------------------------------

def test_receipt_fields_sha_correctness_and_determinism(tmp_path):
    run_dir, ckpt = make_run(tmp_path)
    job_dir = make_job_dir(tmp_path)

    out1 = tmp_path / "round_receipt.json"
    out2 = tmp_path / "round_receipt_again.json"
    common = [
        "--checkpoint-path", str(ckpt),
        "--job-dir", str(job_dir),
    ]
    result1 = run_receipt(*common, "--out", str(out1), "--round", 3)
    result2 = run_receipt(*common, "--out", str(out2), "--round", 3)

    assert result1.returncode == 0, result1.stderr
    assert result2.returncode == 0, result2.stderr
    assert out1.is_file() and out2.is_file()

    receipt1 = load_receipt(out1)
    receipt2 = load_receipt(out2)

    # Determinism: identical receipt modulo the timestamp.
    receipt1.pop("generated_at")
    receipt2.pop("generated_at")
    assert receipt1 == receipt2

    assert receipt1["schema"] == "legoflow.round_receipt.v1"
    assert receipt1["round"] == 3
    assert receipt1["checkpoint_path"] == str(ckpt)

    # checkpoint_sha256 == independent hashlib recomputation over the same tree.
    assert receipt1["checkpoint_sha256"] == recompute_manifest_sha(ckpt)
    manifest = receipt1["checkpoint_manifest"]
    assert manifest["files"] == 4
    assert manifest["content_hashed"] == 3
    assert manifest["size_only"] == 1
    assert manifest["hash_size_limit"] == HASH_SIZE_LIMIT

    # mix_manifest_sha256 == sha256 of the LF data file bytes.
    lf_bytes = (job_dir / "lf.json").read_bytes()
    assert receipt1["lf_file"] == str(job_dir / "lf.json")
    assert receipt1["mix_manifest_sha256"] == hashlib.sha256(lf_bytes).hexdigest()

    # n_instances / tokens from the stats sidecar, final_loss from last log entry.
    assert receipt1["n_instances"] == 42
    assert receipt1["tokens"] == 123456
    assert receipt1["final_loss"] == pytest.approx(0.123)
    assert receipt1["trainer_log_path"] == str(run_dir / "trainer_log.jsonl")


def test_manifest_matches_independent_recompute_for_varied_shapes(tmp_path):
    """Nested dirs, an empty file, several large shards, a binary blob."""
    ckpt = tmp_path / "shaped" / "checkpoint-7"
    (ckpt / "a" / "b").mkdir(parents=True)
    (ckpt / "a" / "empty.txt").write_bytes(b"")
    (ckpt / "a" / "b" / "small.bin").write_bytes(bytes(range(256)) * 100)
    (ckpt / "shard-0.safetensors").write_bytes(b"\xff" * (HASH_SIZE_LIMIT + 1))
    (ckpt / "shard-1.safetensors").write_bytes(b"\xff" * HASH_SIZE_LIMIT)  # exactly at the limit -> size-only
    (ckpt / "trainer_state.json").write_text(json.dumps({"global_step": 7}))
    out = tmp_path / "receipt.json"

    result = run_receipt("--checkpoint-path", str(ckpt), "--out", str(out))
    assert result.returncode == 0, result.stderr

    receipt = load_receipt(out)
    assert receipt["checkpoint_sha256"] == recompute_manifest_sha(ckpt)
    # size-only applies at exactly 4 MiB too (strictly-smaller files are hashed)
    assert receipt["checkpoint_manifest"]["size_only"] == 2
    assert receipt["checkpoint_manifest"]["content_hashed"] == 3


def test_large_file_size_only_semantics(tmp_path):
    ckpt = tmp_path / "run" / "checkpoint-1"
    ckpt.mkdir(parents=True)
    large = ckpt / "model.safetensors"
    size = HASH_SIZE_LIMIT + 512
    large.write_bytes(b"\0" * size)
    out = tmp_path / "receipt.json"

    first = run_receipt("--checkpoint-path", str(ckpt), "--out", str(out))
    assert first.returncode == 0, first.stderr
    hash_before = load_receipt(out)["checkpoint_sha256"]

    # Same size, different content -> invisible to the manifest hash (documented
    # limitation of size-only treatment for files >= 4 MiB).
    large.write_bytes(b"\x01" * size)
    second = run_receipt("--checkpoint-path", str(ckpt), "--out", str(out))
    assert second.returncode == 0, second.stderr
    assert load_receipt(out)["checkpoint_sha256"] == hash_before

    # Any size change in the large file does move the hash.
    with large.open("ab") as fh:
        fh.write(b"x")
    third = run_receipt("--checkpoint-path", str(ckpt), "--out", str(out))
    assert third.returncode == 0, third.stderr
    assert load_receipt(out)["checkpoint_sha256"] != hash_before


def test_receipt_out_excluded_from_manifest_and_idempotent(tmp_path):
    run_dir, ckpt = make_run(tmp_path)
    out = ckpt / "round_receipt.json"  # deliberately inside the checkpoint dir

    first = run_receipt("--checkpoint-path", str(ckpt), "--out", str(out))
    second = run_receipt("--checkpoint-path", str(ckpt), "--out", str(out))
    assert first.returncode == 0 and second.returncode == 0

    r1 = load_receipt(out)
    r2 = load_receipt(out)
    assert r2["checkpoint_sha256"] == r1["checkpoint_sha256"]
    assert r2["checkpoint_sha256"] == recompute_manifest_sha(ckpt, exclude=out)


# ---------------------------------------------------------------------------
# Tolerated missing inputs
# ---------------------------------------------------------------------------

def test_missing_inputs_tolerated_with_nulls(tmp_path):
    run_dir, ckpt = make_run(tmp_path, with_trainer_log=False)
    out = tmp_path / "receipt.json"

    result = run_receipt("--checkpoint-path", str(ckpt), "--out", str(out))
    assert result.returncode == 0, result.stderr
    receipt = load_receipt(out)

    assert receipt["schema"] == "legoflow.round_receipt.v1"
    assert receipt["lf_file"] is None
    assert receipt["mix_manifest_sha256"] is None
    assert receipt["n_instances"] is None
    assert receipt["tokens"] is None
    assert receipt["final_loss"] is None
    assert receipt["trainer_log_path"] is None
    # The checkpoint hash still pins identity.
    assert receipt["checkpoint_sha256"] == recompute_manifest_sha(ckpt)


def test_stats_sidecar_missing_leaves_counts_null(tmp_path):
    run_dir, ckpt = make_run(tmp_path)
    job_dir = tmp_path / "lf_only"
    job_dir.mkdir()
    (job_dir / "lf.json").write_text(json.dumps([{"messages": []}]), encoding="utf-8")
    out = tmp_path / "receipt.json"

    result = run_receipt(
        "--checkpoint-path", str(ckpt), "--job-dir", str(job_dir), "--out", str(out)
    )
    assert result.returncode == 0, result.stderr
    receipt = load_receipt(out)
    assert receipt["lf_file"] == str(job_dir / "lf.json")
    assert receipt["mix_manifest_sha256"] == hashlib.sha256((job_dir / "lf.json").read_bytes()).hexdigest()
    assert receipt["n_instances"] is None
    assert receipt["tokens"] is None
    assert receipt["final_loss"] == pytest.approx(0.123)


def test_final_loss_skips_entries_without_numeric_loss(tmp_path):
    run_dir, ckpt = make_run(tmp_path)
    (run_dir / "trainer_log.jsonl").write_text(
        "".join(
            json.dumps(e) + "\n"
            for e in [
                {"current_step": 10, "loss": 0.9},
                {"current_step": 20, "loss": 0.5},
                {"current_step": 20, "loss": None},
                {"current_step": 20, "epoch": 1.0},
                "not json at all",
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "receipt.json"
    result = run_receipt("--checkpoint-path", str(ckpt), "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert load_receipt(out)["final_loss"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# --job-dir LF discovery
# ---------------------------------------------------------------------------

def test_job_dir_discovery_prefers_lf_json_over_sidecar_backed_peers(tmp_path):
    run_dir, ckpt = make_run(tmp_path)
    job_dir = tmp_path / "lf_data"
    job_dir.mkdir()
    (job_dir / "dataset_info.json").write_text("{}", encoding="utf-8")
    (job_dir / "aaa.json").write_text(json.dumps([{"messages": []}]), encoding="utf-8")
    (job_dir / "aaa.stats.json").write_text(json.dumps({"count": 1, "total_tokens": 2}), encoding="utf-8")
    (job_dir / "lf.json").write_text(json.dumps([{"messages": []}, {"messages": []}]), encoding="utf-8")
    (job_dir / "lf.stats.json").write_text(json.dumps({"count": 2, "total_tokens": 9}), encoding="utf-8")
    out = tmp_path / "receipt.json"

    result = run_receipt("--checkpoint-path", str(ckpt), "--job-dir", str(job_dir), "--out", str(out))
    assert result.returncode == 0, result.stderr
    receipt = load_receipt(out)
    assert receipt["lf_file"] == str(job_dir / "lf.json")
    assert receipt["n_instances"] == 2
    assert receipt["tokens"] == 9


def test_job_dir_discovery_sorted_names_and_registry_skipped(tmp_path):
    run_dir, ckpt = make_run(tmp_path)
    job_dir = tmp_path / "lf_data"
    job_dir.mkdir()
    (job_dir / "dataset_info.json").write_text("{}", encoding="utf-8")
    (job_dir / "beta.json").write_text(json.dumps([{"messages": []}]), encoding="utf-8")
    (job_dir / "beta.stats.json").write_text(json.dumps({"count": 8, "total_tokens": 80}), encoding="utf-8")
    (job_dir / "alpha.json").write_text(json.dumps([{"messages": []}]), encoding="utf-8")
    (job_dir / "alpha.stats.json").write_text(json.dumps({"count": 5, "total_tokens": 50}), encoding="utf-8")
    out = tmp_path / "receipt.json"

    result = run_receipt("--checkpoint-path", str(ckpt), "--job-dir", str(job_dir), "--out", str(out))
    assert result.returncode == 0, result.stderr
    receipt = load_receipt(out)
    # No lf.json -> first sidecar-backed json by sorted name; the registry is never a candidate.
    assert receipt["lf_file"] == str(job_dir / "alpha.json")
    assert receipt["n_instances"] == 5
    assert receipt["tokens"] == 50


def test_lf_file_flag_wins_over_job_dir(tmp_path):
    run_dir, ckpt = make_run(tmp_path)
    job_dir = make_job_dir(tmp_path)
    pinned = tmp_path / "elsewhere" / "pinned_lf.json"
    pinned.parent.mkdir(parents=True)
    pinned.write_text(json.dumps([{"messages": []}] * 3), encoding="utf-8")
    (pinned.with_suffix(".stats.json")).write_text(
        json.dumps({"count": 3, "total_tokens": 30}), encoding="utf-8"
    )
    out = tmp_path / "receipt.json"

    result = run_receipt(
        "--checkpoint-path", str(ckpt),
        "--job-dir", str(job_dir),
        "--lf-file", str(pinned),
        "--out", str(out),
    )
    assert result.returncode == 0, result.stderr
    receipt = load_receipt(out)
    assert receipt["lf_file"] == str(pinned)
    assert receipt["mix_manifest_sha256"] == hashlib.sha256(pinned.read_bytes()).hexdigest()
    assert receipt["n_instances"] == 3


def test_nonexistent_lf_file_flag_falls_back_to_nulls(tmp_path):
    run_dir, ckpt = make_run(tmp_path)
    out = tmp_path / "receipt.json"
    result = run_receipt(
        "--checkpoint-path", str(ckpt),
        "--lf-file", str(tmp_path / "missing.json"),
        "--out", str(out),
    )
    assert result.returncode == 0, result.stderr
    receipt = load_receipt(out)
    assert receipt["lf_file"] is None
    assert receipt["mix_manifest_sha256"] is None


# ---------------------------------------------------------------------------
# round resolution
# ---------------------------------------------------------------------------

def test_round_resolution_flag_env_and_default(tmp_path):
    run_dir, ckpt = make_run(tmp_path)
    out = tmp_path / "receipt.json"
    base = ["--checkpoint-path", str(ckpt), "--out", str(out)]

    assert run_receipt(*base).returncode == 0
    assert load_receipt(out)["round"] == 1  # default

    assert run_receipt(*base, env_extra={ROUND_ENV: "3"}).returncode == 0
    assert load_receipt(out)["round"] == 3  # env

    assert run_receipt(*base, "--round", "7", env_extra={ROUND_ENV: "3"}).returncode == 0
    assert load_receipt(out)["round"] == 7  # flag beats env

    assert run_receipt(*base, env_extra={ROUND_ENV: "not-a-number"}).returncode == 0
    assert load_receipt(out)["round"] == 1  # invalid env falls back with a warning


# ---------------------------------------------------------------------------
# Hard failure modes
# ---------------------------------------------------------------------------

def test_missing_checkpoint_dir_fails(tmp_path):
    out = tmp_path / "receipt.json"
    result = run_receipt("--checkpoint-path", str(tmp_path / "nope"), "--out", str(out))
    assert result.returncode != 0
    assert "ERROR" in result.stderr
    assert not out.exists()


# ---------------------------------------------------------------------------
# train.sh STEP 3 wiring
# ---------------------------------------------------------------------------

def step3_section() -> str:
    text = TRAIN_SH.read_text(encoding="utf-8")
    marker = "STEP 3: Update config.yaml runtime_info.output"
    idx = text.find(marker)
    assert idx != -1, "train.sh lost its STEP 3 marker"
    return text[idx:]


def test_train_sh_passes_bash_n():
    result = subprocess.run(["bash", "-n", str(TRAIN_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_train_sh_step3_has_guarded_round_receipt_call():
    step3 = step3_section()

    # The call exists, passes the contract flags, and is guarded: any failure
    # only warns and can never fail training.
    assert "round_receipt.py" in step3
    assert "--checkpoint-path" in step3
    assert '"--out"' in step3
    assert "WARNING: round receipt" in step3
    assert "except Exception" in step3

    # It must sit after the existing checkpoint_path writeback, not before it.
    writeback = step3.find("write_runtime_output_preserving_comments(config_path, outputs)")
    receipt_call = step3.find("round_receipt.py")
    assert writeback != -1, "STEP 3 lost its comment-preserving writeback"
    assert receipt_call > writeback, "round receipt must be invoked after the checkpoint writeback"

    # The exact LF file this run trained on is handed to the receipt
    # (empty when the dataset came from the Hub at train time).
    assert "LF_FILE_FOR_RECEIPT" in step3
    assert "--lf-file" in step3


def test_train_sh_step3_still_writes_checkpoint_path_and_uses_lock():
    step3 = step3_section()
    assert 'outputs.setdefault("checkpoint_path", {})' in step3
    assert "checkpoint_entry[\"value\"] = checkpoint" in step3
    assert "fcntl.flock(lock, fcntl.LOCK_EX)" in step3
    # The writeback heredoc invocation gained exactly one new argument.
    assert 'CONFIG" "$LF_FILE_FOR_RECEIPT" <<\'PYEOF\'' in step3
