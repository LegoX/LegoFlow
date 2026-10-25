#!/usr/bin/env bash
# CI test 10: stratified selection knob and select_stratified_tasks.py.
#
# Part (a) checks the config default (selection.count: null = whole pool).
# Part (b) builds a synthetic merged pool (14 tasks, 6 repos, 6 strata incl.
# an unknown-difficulty/untagged task) and asserts the selection algorithm:
# stratum floors first, repo coverage, sqrt-proportional top-up (a second,
# 16-vs-4 pool pins the 7/3 sqrt split), deterministic idempotent output,
# symlink staging, report fields, and loud failures (incl. manifest ids with
# no task directory skipped with a WARN, never staged as dangling symlinks).
# Part (c) asserts tracer's prepare_tasks.sh honors task_source.pool:
# stratified (empty dir fails before staging; a real dir passes the pool gate,
# with Harbor checks sandboxed so the stop is deterministic on every machine).

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"
SELECT="$BLOCK_DIR/scripts/select_stratified_tasks.py"
PREPARE="$BLOCK_DIR/../tracer/scripts/prepare_tasks.sh"
TRACER_CONFIG="$BLOCK_DIR/../tracer/config.yaml"

# (a) config: runtime_info.input.selection.count present, defaulting to null.
python3 - "$CONFIG" <<'PY' || exit 1
import sys
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed in the runner's python3", file=sys.stderr)
    raise SystemExit(1)
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
selection = ((cfg.get("runtime_info") or {}).get("input") or {}).get("selection") or {}
if "count" not in selection:
    print("FAIL: runtime_info.input.selection.count missing from config.yaml", file=sys.stderr)
    raise SystemExit(1)
if selection["count"] is not None:
    print(f"FAIL: selection.count must default to null (got {selection['count']!r})", file=sys.stderr)
    raise SystemExit(1)
PY
[[ -x "$SELECT" ]] || { echo "FAIL: $SELECT is missing or not executable" >&2; exit 1; }

# (b) + (c) functional selection over a synthetic pool.
python3 - "$SELECT" "$PREPARE" "$TRACER_CONFIG" <<'PY' || exit 1
import json
import subprocess
import sys
import tempfile
from pathlib import Path

select = Path(sys.argv[1])
prepare = Path(sys.argv[2])
tracer_config = Path(sys.argv[3])

# owner__repo-<pr> ids; scoring difficulty_score + metadata tags drive strata.
FIXTURES = [
    # (id, repo, language tag, score)
    ("ownA__repoa-1", "ownA/repoa", "python", 2.0),
    ("ownA__repoa-2", "ownA/repoa", "python", 3.0),
    ("ownA__repoa-3", "ownA/repoa", "python", 4.0),
    ("ownB__repob-1", "ownB/repob", "python", 5.0),
    ("ownB__repob-2", "ownB/repob", "python", 6.0),
    ("ownB__repob-3", "ownB/repob", "python", 7.0),
    ("ownC__repoc-1", "ownC/repoc", "go", 8.0),
    ("ownC__repoc-2", "ownC/repoc", "go", 9.0),
    ("ownD__repod-1", "ownD/repod", "rust", 1.5),
    ("ownD__repod-2", "ownD/repod", "rust", 2.5),
    ("ownE__repoe-1", "ownE/repoe", "go", 5.5),
    ("ownE__repoe-2", "ownE/repoe", "go", 6.5),
    ("ownE__repoe-3", "ownE/repoe", "go", 7.5),
    ("ownU__repou-1", "ownU/repou", None, None),  # no metadata -> unknown stratum
]


def write_pool(root: Path) -> None:
    root.mkdir(parents=True)
    for task_id, _repo, lang, score in FIXTURES:
        task_dir = root / task_id
        task_dir.mkdir()
        (task_dir / "task.toml").write_text(
            '[metadata]\n'
            + (f'tags = ["{lang}", "backend", "auth", "logic-error"]\n' if lang else '')
            + "\n[scoring]\n"
            + (f'difficulty_score = {score}\n' if score is not None else ''),
            encoding="utf-8",
        )
    (root / "verifiable_tasks.txt").write_text(
        "\n".join(sorted(t[0] for t in FIXTURES)) + "\n", encoding="utf-8"
    )


def run_select(out: Path, *cli: str, source: Path) -> dict:
    run = subprocess.run(
        [sys.executable, str(select), "--source", str(source), "--out", str(out), *cli],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        raise AssertionError(f"select failed (rc={run.returncode}): {run.stderr.strip()}")
    return json.loads((out / "selection_report.json").read_text(encoding="utf-8"))


def manifest_of(out: Path) -> list[str]:
    return sorted(
        line.strip()
        for line in (out / "verifiable_tasks.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


with tempfile.TemporaryDirectory() as raw_tmp:
    tmp = Path(raw_tmp)
    source = tmp / "merged_swe_tasks"
    write_pool(source)

    # count=6 == the 6 stratum floors: one per (bin, language) stratum,
    # including the unknown-difficulty/untagged task (never silently dropped),
    # covering all 6 repos.
    out6 = tmp / "out6"
    report6 = run_select(out6, "--count", "6", source=source)
    expected6 = ["ownA__repoa-1", "ownB__repob-1", "ownC__repoc-1", "ownD__repod-1",
                 "ownE__repoe-1", "ownU__repou-1"]
    got6 = manifest_of(out6)
    if got6 != sorted(expected6):
        raise AssertionError(f"count=6 must select one per stratum (incl. unknown), got {got6}")
    if report6["repos_covered"] != 6 or report6["repos_total"] != 6:
        raise AssertionError(f"count=6 must cover every repo: {report6['repos_covered']}/{report6['repos_total']}")
    if report6["difficulty_unknown_selected"] != 1 or report6["language_unknown_selected"] != 1:
        raise AssertionError(f"unknown-stratum task must be selected exactly once: {report6}")
    for field in ("count_selected", "pool_total", "strata", "manifest_sha256", "generated_at"):
        if field not in report6:
            raise AssertionError(f"selection_report.json missing field {field!r}: {report6}")

    # Symlinks resolve back into the source pool and stay readable.
    link = out6 / "ownA__repoa-1"
    if not link.is_symlink() or not (link / "task.toml").is_file():
        raise AssertionError(f"selected tasks must be readable symlinks: {link}")

    # count=8: floors + two sqrt-proportional top-ups. Real strata (E-3 at 7.5
    # is hard, B-3 at 7.0 is medium): after the 6 floors, shares are
    # easy|Python 2/sqrt(3) == hard|Go 2/sqrt(3) == medium|Python 2/sqrt(3)
    # > easy|Rust 1/sqrt(2) == medium|Go 1/sqrt(2). Ties break on stratum
    # name, so the extras land in easy|Python (repoA's slot 2) then hard|Go —
    # whose round-robin hands the slot to the OTHER repo (ownE), spreading
    # repos inside a stratum by design.
    out8 = tmp / "out8"
    run_select(out8, "--count", "8", source=source)
    expected8 = sorted(expected6 + ["ownA__repoa-2", "ownE__repoe-3"])
    got8 = manifest_of(out8)
    if got8 != expected8:
        raise AssertionError(f"count=8 sqrt-proportional top-up picked wrong tasks, got {got8}")

    # Unequal pools pin the sqrt-proportional semantics: with pools 16 vs 4
    # (sqrt ratio 4:2), a count=10 pick must land 7/3 (picked_n ~ sqrt(pool_n),
    # largest-remainder) — not dump every top-up slot into the large stratum.
    pool2 = tmp / "unequal_pool"
    pool2.mkdir()
    for i in range(1, 17):
        d = pool2 / f"ownA__repoa-{i}"
        d.mkdir()
        (d / "task.toml").write_text(
            '[metadata]\ntags = ["python", "backend", "auth", "logic-error"]\n\n[scoring]\ndifficulty_score = 2.0\n',
            encoding="utf-8")
    for i in range(1, 5):
        d = pool2 / f"ownB__repob-{i}"
        d.mkdir()
        (d / "task.toml").write_text(
            '[metadata]\ntags = ["go", "backend", "auth", "logic-error"]\n\n[scoring]\ndifficulty_score = 5.0\n',
            encoding="utf-8")
    ids2 = sorted([f"ownA__repoa-{i}" for i in range(1, 17)] + [f"ownB__repob-{i}" for i in range(1, 5)])
    (pool2 / "verifiable_tasks.txt").write_text("\n".join(ids2) + "\n", encoding="utf-8")
    out_u = tmp / "out_unequal"
    report_u = run_select(out_u, "--count", "10", source=pool2)
    strata_u = report_u["strata"]
    if strata_u.get("easy|Python", {}).get("selected") != 7 or strata_u.get("medium|Go", {}).get("selected") != 3:
        raise AssertionError(f"sqrt-proportional top-up must split count=10 as 7/3 for pools 16/4, got {strata_u}")

    # Manifest ids without a task directory are skipped loudly (WARN +
    # missing_task_dirs in the report), never staged as dangling symlinks.
    (pool2 / "verifiable_tasks.txt").write_text("\n".join(sorted(ids2 + ["ownX__repox-9"])) + "\n", encoding="utf-8")
    out_m = tmp / "out_missing"
    report_m = run_select(out_m, "--count", "all", source=pool2)
    if report_m["missing_task_dirs"] != 1 or report_m["count_selected"] != 20:
        raise AssertionError(f"dangling manifest id must be skipped loudly: {report_m}")
    if (out_m / "ownX__repox-9").exists() or (out_m / "ownX__repox-9").is_symlink():
        raise AssertionError("missing task dir must not be staged")

    # Determinism: same inputs -> byte-identical manifest on rerun.
    out6b = tmp / "out6b"
    run_select(out6b, "--count", "6", source=source)
    if (out6 / "verifiable_tasks.txt").read_bytes() != (out6b / "verifiable_tasks.txt").read_bytes():
        raise AssertionError("rerunning the same selection must reproduce the manifest byte-for-byte")

    # count = all -> the whole pool.
    outall = tmp / "outall"
    report_all = run_select(outall, "--count", "all", source=source)
    if manifest_of(outall) != sorted(t[0] for t in FIXTURES) or report_all["count_selected"] != 14:
        raise AssertionError("count=all must select the entire pool")

    # Loud failures: count=0 and a pool without a manifest.
    bad = subprocess.run(
        [sys.executable, str(select), "--source", str(source), "--out", str(tmp / "nope"),
         "--count", "0"], capture_output=True, text=True)
    if bad.returncode == 0 or "count" not in bad.stderr:
        raise AssertionError(f"--count 0 must fail loudly: rc={bad.returncode} stderr={bad.stderr!r}")
    empty = tmp / "empty_pool"
    empty.mkdir()
    bad2 = subprocess.run(
        [sys.executable, str(select), "--source", str(empty), "--out", str(tmp / "nope2"),
         "--count", "5"], capture_output=True, text=True)
    if bad2.returncode == 0 or "verifiable_tasks.txt" not in bad2.stderr:
        raise AssertionError(f"manifest-less pool must fail loudly: rc={bad2.returncode} stderr={bad2.stderr!r}")

    # (c) tracer prepare_tasks.sh honors task_source.pool: stratified.
    try:
        import yaml
    except ImportError:
        print("SKIP: PyYAML unavailable for the tracer-side check", file=sys.stderr)
        raise SystemExit(77)
    base_cfg = yaml.safe_load(tracer_config.read_text(encoding="utf-8"))

    def tracer_cfg(pool: str, stratified_dir: str) -> Path:
        cfg = json.loads(json.dumps(base_cfg))  # deep copy without yaml anchors
        cfg["runtime_info"]["input"]["task_source"]["pool"] = pool
        cfg["runtime_info"]["input"]["task_source"]["stratified_dataset_dir"] = stratified_dir
        # Sandbox the tracer-side run: stage into the temp dir and point the
        # Harbor repo/env at paths that do not exist, so the run always stops
        # at "Harbor repo missing" regardless of machine state — and can never
        # write into the real tracer block's artifacts/.
        cfg["runtime_info"]["input"]["harbor_job"]["dataset_path"] = str(tmp / "staged-tasks")
        cfg["meta_info"]["repositories"]["harbor"]["path"] = str(tmp / "harbor-missing")
        cfg["meta_info"]["environment"]["harbor_uv"] = str(tmp / "harbor-uv-missing")
        path = tmp / f"tracer-{pool}-{abs(hash(stratified_dir)) % 1000}.yaml"
        path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        return path

    # Empty stratified dir + pool=stratified fails BEFORE staging.
    run = subprocess.run(["bash", str(prepare)], capture_output=True, text=True,
                         env={**__import__("os").environ,
                              "TRAJGEN_CONFIG": str(tracer_cfg("stratified", ""))})
    if run.returncode == 0 or "stratified_dataset_dir is empty" not in run.stderr:
        raise AssertionError(f"empty stratified dir must fail loud and early: rc={run.returncode} "
                             f"stderr={run.stderr.strip()[-300:]!r}")

    # A real stratified pool passes the pool gate (then dies on this machine's
    # missing Harbor checkout, which is environmental) — proving the override
    # was accepted.
    run2 = subprocess.run(["bash", str(prepare)], capture_output=True, text=True,
                          env={**__import__("os").environ,
                               "TRAJGEN_CONFIG": str(tracer_cfg("stratified", str(out6.resolve())))})
    combined = run2.stdout + run2.stderr
    if "Pool:     stratified" not in combined:
        raise AssertionError(f"prepare_tasks.sh must announce the stratified pool override: {combined[:300]!r}")
    if run2.returncode == 0 or "Harbor repo missing" not in combined:
        raise AssertionError(f"expected the environmental Harbor-missing stop after the pool gate, "
                             f"got rc={run2.returncode}: {combined[-300:]!r}")

print("ok")
PY

echo "PASS: stratified selection knob, select_stratified_tasks.py, and tracer pool override"
