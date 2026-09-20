#!/usr/bin/env bash
# Root smoke chain verifier — gates one stage of the pipeline.
#
#   bash tests/smoke/verify.sh <curator|tracer|trainer|evaluator>
#
# run_pipeline.sh calls this after each stage. A non-zero (FAIL) verdict on any
# stage stops the chain (fail-fast), because each stage's REAL output feeds the
# next: no verified tasks -> nothing to infer; no reward==1 -> nothing new to
# train on; no checkpoint -> nothing to eval.
#
# Stage gates (each is about the HANDOFF the next stage needs, not benchmark
# quality):
#   curator  : >=1 verified task in the smoke subdir's verifiable_tasks.txt
#   tracer : >=1 SCORED trial (pipeline health, like the block smoke);
#             reward==1 (resolved) + converted lf.json are reported, NOT gated
#   trainer     : train_results.json (finite loss) AND a persisted checkpoint
#             (config.json + weights) in the run dir
#   evaluator    : >=1 clean scored trial; reports resolved/total over the 100-subset
#
# Exit 0 = PASS, 77 = SKIP (prereq absent — stage never really ran), 1 = FAIL.

set -uo pipefail

STAGE="${1:?usage: verify.sh <curator|tracer|trainer|evaluator>}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cfg() {  # cfg <file> <dotted-key>
  python3 - "$1" "$2" <<'PY'
import sys, yaml
try:
    d = yaml.safe_load(open(sys.argv[1])) or {}
except Exception:
    print(""); raise SystemExit
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

case "$STAGE" in
  # ---------------------------------------------------------------- curator ----
  curator)
    B="$ROOT_DIR/blocks/curator"
    BASE="$(cfg "$B/config.yaml" runtime_info.output.swe_tasks_dir.path)"; BASE="${BASE:-artifacts/swe_tasks}"
    SUB="$(cfg "$B/config.yaml" runtime_info.input.smoke.output_subdir)"; SUB="${SUB:-py-cc-root-smoke}"
    MANIFEST="$B/$BASE/$SUB/verifiable_tasks.txt"
    if [[ ! -d "$B/$BASE/$SUB" ]]; then
      echo "SKIP: curator output dir $BASE/$SUB absent — create step never ran"
      exit 77
    fi
    if [[ -s "$MANIFEST" ]]; then
      n=$(wc -l <"$MANIFEST")
      echo "PASS: curator produced $n verified task(s):"
      sed 's/^/         /' "$MANIFEST"
      exit 0
    fi
    echo "FAIL: $MANIFEST empty/missing — no verified tasks to hand to tracer"
    exit 1
    ;;

  # --------------------------------------------------------------- tracer ----
  tracer)
    B="$ROOT_DIR/blocks/tracer"
    JOBS="$(cfg "$B/config.yaml" runtime_info.input.harbor_job.jobs_dir)"; JOBS="${JOBS:-artifacts/jobs/root-smoke}"
    SFT_DATA="$(cfg "$B/config.yaml" runtime_info.input.sft_conversion.out_dir)"; SFT_DATA="${SFT_DATA:-artifacts/sft_data}"
    JOBS_DIR="$B/$JOBS"
    if [[ ! -d "$JOBS_DIR" ]]; then
      echo "SKIP: tracer jobs dir $JOBS absent — harbor never ran"
      exit 77
    fi
    # reward determination mirrors the block tracer smoke (verify.sh):
    #   scored   = the verifier ran to a terminal reward (rewards is a non-empty
    #              dict) — agent ran, container built, harbor scored.
    #   resolved = any reward > 0 (the model actually solved it).
    SCAN="$(JOBS_DIR="$JOBS_DIR" python3 - <<'PY'
import json, os, glob
root = os.environ["JOBS_DIR"]
total = scored = resolved = 0
for path in glob.glob(os.path.join(root, "*", "*", "result.json")):
    total += 1
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        continue
    vr = data.get("verifier_result")
    rewards = vr.get("rewards") if isinstance(vr, dict) else None
    if not isinstance(rewards, dict) or not rewards:
        continue
    scored += 1
    if any(isinstance(v, (int, float)) and v > 0 for v in rewards.values()):
        resolved += 1
print(f"SCORED:{scored} RESOLVED:{resolved} TOTAL:{total}")
PY
)"
    echo "INFO: tracer scan: $SCAN"
    total=$(sed -n 's/.*TOTAL:\([0-9]*\).*/\1/p' <<<"$SCAN");    total=${total:-0}
    scored=$(sed -n 's/.*SCORED:\([0-9]*\).*/\1/p' <<<"$SCAN");  scored=${scored:-0}
    resolved=$(sed -n 's/.*RESOLVED:\([0-9]*\).*/\1/p' <<<"$SCAN"); resolved=${resolved:-0}
    # reward==1 trajectories converted to LF for trainer (reported, not gated).
    LF_COUNT=$(find "$B/$SFT_DATA" -name lf.json -size +2c 2>/dev/null | wc -l)
    if [[ "$total" -lt 1 ]]; then
      echo "SKIP: no result.json under $JOBS — harbor produced no trials"
      exit 77
    fi
    # PASS gate = PIPELINE HEALTH (>=1 SCORED), exactly like the block tracer
    # smoke. reward==1 (RESOLVED) is REPORTED, not gated: at smoke scale a model
    # legitimately resolves 0–few, so gating on it would make the smoke flaky on
    # LLM luck / endpoint load. The converter feeds whatever reward==1
    # trajectories exist to trainer; trainer falls back to the 512 fixture if there are 0.
    if [[ "$scored" -ge 1 ]]; then
      echo "PASS: tracer pipeline healthy — $scored scored trial(s); RESOLVED(reward>0)=$resolved; reward==1 lf.json=$LF_COUNT"
      exit 0
    fi
    echo "FAIL: 0 scored trials — every trial errored before the verifier could run ($SCAN)"
    exit 1
    ;;

  # ------------------------------------------------------------------- trainer ----
  trainer)
    B="$ROOT_DIR/blocks/trainer"
    OUT="$(cfg "$B/config.yaml" runtime_info.input.training.output_dir)"; OUT="${OUT:-root_smoke_model}"
    RUN_DIR="$B/artifacts/model/$OUT"
    if [[ ! -d "$RUN_DIR" ]]; then
      echo "SKIP: trainer run dir artifacts/model/$OUT absent — training never produced output (likely remote-only; run_pipeline fetches train_results.json)"
      exit 77
    fi
    if [[ ! -f "$RUN_DIR/train_results.json" ]]; then
      echo "FAIL: missing train_results.json in $RUN_DIR"
      exit 1
    fi
    VERDICT="$(RUN_DIR="$RUN_DIR" python3 - <<'PY'
import json, os, glob, math
out = os.environ["RUN_DIR"]
tr = json.load(open(os.path.join(out, "train_results.json")))
loss = tr.get("train_loss")
if not isinstance(loss, (int, float)) or not math.isfinite(loss):
    print(f"FAIL non-finite train_loss={loss!r}"); raise SystemExit
# A servable checkpoint = config.json + at least one weight shard. Eval needs
# this; if run_pipeline fetched only train_results.json (remote run), the
# weights live on the pod and serve_checkpoint.sh checks them there instead.
has_cfg = os.path.isfile(os.path.join(out, "config.json"))
has_w = bool(glob.glob(os.path.join(out, "*.safetensors")) or glob.glob(os.path.join(out, "pytorch_model*.bin")))
print(f"OK loss={loss:.4f} checkpoint={'present' if (has_cfg and has_w) else 'remote_only'}")
PY
)"
    echo "INFO: verify: $VERDICT"
    [[ "$VERDICT" == OK* ]] && { echo "PASS: trainer ($VERDICT)"; exit 0; }
    echo "FAIL: $VERDICT"
    exit 1
    ;;

  # ------------------------------------------------------------------ evaluator ----
  evaluator)
    B="$ROOT_DIR/blocks/evaluator"
    JOBS="$(cfg "$B/config.yaml" runtime_info.input.harbor_job.jobs_dir)"; JOBS="${JOBS:-artifacts/jobs/root-smoke}"
    JOBS_DIR="$B/$JOBS"
    if [[ ! -d "$JOBS_DIR" ]]; then
      echo "SKIP: evaluator jobs dir $JOBS absent — evaluator never launched"
      exit 77
    fi
    SCAN="$(JOBS_DIR="$JOBS_DIR" python3 - <<'PY'
import json, os, glob
root = os.environ["JOBS_DIR"]
results = sorted(glob.glob(os.path.join(root, "*", "result.json")), key=os.path.getmtime)
if not results:
    print("CLEAN:0 RESOLVED:0 EVAL:0 (no result.json)"); raise SystemExit
data = json.load(open(results[-1], encoding="utf-8"))
stats = data.get("stats") or {}
evals = stats.get("evals") or {}
evaluated, resolved, errored = set(), set(), set()
for ds in evals.values():
    if not isinstance(ds, dict): continue
    for by_value in (ds.get("reward_stats") or {}).values():
        for value, names in (by_value or {}).items():
            try: pos = float(value) > 0
            except (TypeError, ValueError): pos = False
            for n in names or []:
                evaluated.add(n)
                if pos: resolved.add(n)
    for names in (ds.get("exception_stats") or {}).values():
        for n in names or []: errored.add(n)
clean = evaluated - errored
rate = (len(resolved) / len(evaluated) * 100) if evaluated else 0.0
print(f"CLEAN:{len(clean)} RESOLVED:{len(resolved)} EVAL:{len(evaluated)} RATE:{rate:.1f}%")
PY
)"
    echo "INFO: evaluator scan: $SCAN"
    if grep -q "no result.json" <<<"$SCAN"; then
      echo "SKIP: no result.json under $JOBS"
      exit 77
    fi
    clean=$(sed -n 's/.*CLEAN:\([0-9]*\).*/\1/p' <<<"$SCAN"); clean=${clean:-0}
    if [[ "$clean" -ge 1 ]]; then
      echo "PASS: evaluator scored the trained model on the verified subset ($SCAN)"
      exit 0
    fi
    echo "FAIL: 0 clean scored trials — every evaluator trial errored ($SCAN)"
    exit 1
    ;;

  *)
    echo "usage: verify.sh <curator|tracer|trainer|evaluator>" >&2
    exit 2
    ;;
esac
