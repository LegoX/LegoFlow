---
name: check
description: >
  Preflight the trainer block: answer "is it safe to launch training right
  now?" Runs every deterministic check through scripts/dryrun.sh (config
  schema, uv env, repos, source-specific data fields, converter module, dataset
  registration, base-model path, WandB mode, GPU count), adds the few live
  checks a script can't judge (is a training job already running? are the
  GPUs held by a foreign process? will an existing checkpoint be
  overwritten?), and always ends with one structured report: a
  SAFE-TO-RUN verdict, a status table, the run configuration, and numbered
  next steps. Read-only — never edits config.yaml, never launches. Triggers
  on "check trainer", "preflight trainer", "is trainer ready", "diagnose trainer",
  "validate trainer config", "sanity check trainer before launch".
---

# /trainer:check — preflight the trainer block

Answers one question: **is it safe to launch training right now?**

It runs the deterministic checks via `scripts/dryrun.sh`, adds the handful
of live checks a script can't judge, and prints **one structured report**
ending in a clear YES / NO. Read-only — it never edits config or launches
anything.

## How it works

```
/trainer:check
   │
   ├─ 1. dryrun.sh ...... deterministic checks  → [OK] / [FAIL] / [WARN] lines
   │
   ├─ 2. live probes .... only what needs judgment (training already
   │                       running? GPUs ours or foreign? checkpoint clobber?)
   │
   └─ 3. report ......... verdict + status table + run config + next steps
```

Every check lives in **exactly one** layer:

| Layer | Run by | Covers |
|---|---|---|
| **Deterministic** | `scripts/dryrun.sh` | config schema · uv env + python · repos · source-specific Harbor/HF/local fields · converter when applicable · data paths · dataset registration (including exact `hf_file_name`) · base model · `output_dir` + train-YAML target · WandB mode/key · GPU count · Cloudflare quick tunnel binary (optional, dashboard-only) · shared Cloudflare/registry credentials from root `config.yaml` (informational — trainer needs neither: its tunnel is anonymous and it pulls no images) |
| **Live (judgment)** | this skill | is a training process already alive? · are the GPUs idle / ours / foreign? · will training overwrite an existing checkpoint? |

---

## Step 0 — Orient

Run only from inside the trainer block. If `./config.yaml` is missing or
`meta_info.name != 'trainer'`, or `./scripts/dryrun.sh` is missing → **abort**,
but still print the Step 3 report (heading + a `NO` verdict stating why).

Read these from `config.yaml` (don't echo the whole file):

| Variable | Source | Default |
|---|---|---|
| `N_GPUS` | `runtime_info.input.infrastructure.n_gpus_per_node` | 8 |
| `OUTPUT_DIR` | `runtime_info.input.training.output_dir` | — |
| `DATA_NAME` | `runtime_info.input.conversion.data_name` | — |
| `WANDB_MODE` | `runtime_info.input.experiment.wandb_mode` | offline |

The resolved checkpoint dir is `OUTPUT_DIR` if absolute, else
`artifacts/model/$(basename OUTPUT_DIR)`.

## Step 1 — Deterministic checks

```bash
bash ./scripts/dryrun.sh 2>&1; echo "EXIT=$?"
```

`dryrun.sh` prints one line per check. Read them as-is — never re-run a
probe or overrule an `[OK]`. The bracketed prefix is the status:

| Prefix | Status | Blocks launch? |
|---|:---:|:---:|
| `[OK]` | pass | — |
| `[FAIL]` | fail | **yes** |
| `[WARN]` | warning | no |
| `[INFO]` | note | — |

The final summary line is `PASS: <n>   WARN: <n>   FAIL: <n>`; `dryrun.sh`
exits non-zero iff `FAIL > 0`. Two lines need follow-up:

- for `source.type=harbor_job`, any `[WARN] source.job_dir not found ...` → note it for Step 2 (the data
  source may live on another node; conversion fails later if it's truly
  absent and no IM/LF output is cached).
- any `[WARN] nvidia-smi found <N> GPU(s), config expects <N_GPUS>` → hand
  to **Step 2b**.

`dryrun.sh` has no single "Run Configuration Summary" block; build the
run-config table in Step 3 from the `[INFO]`/`[OK]` lines it printed
(scaffold, data_name, dataset, model path, template/epochs/lr, output_dir,
WandB mode, GPU count).

## Dependency wiring (cross-checked inside dryrun)

`scripts/dryrun.sh` runs `scripts/validate_config.py --block .`, which
cross-checks `meta_info.dependencies` against the real `runtime_info` on **both**
ends of every edge. These findings are easy to lose in the dryrun output, and
they are exactly what breaks a hand-off silently — surface them in the report.

| Finding | Meaning | Verdict |
|---|---|---|
| `dep:bad-key` | a `from` key is not a real dot-path in this block's own `runtime_info.input`, or a `to` key is not a declared `runtime_info.output` key | FAIL |
| `dep:bad-ref` | malformed ref, or the named block / output key / input path does not exist | FAIL |
| `dep:link-mismatch` | the edge is declared by only one end — the other end does not point back | FAIL |
| `dep:unresolved` | a required upstream output has neither `value` nor `path` yet | FAIL (WARN when the edge is `required: false`) |
| `dep:path-mismatch` | this block's configured value resolves outside the producer's declared output path — usually a stale path after a rename | WARN |
| `output:orphan` | an output with no `dependencies.to` entry; normal for a terminal output, suspicious for one that is supposed to feed the next stage | WARN |
| `dep:smoke-overlay` | a root smoke currently holds some block's config, so the tree mixes two config sets; every cross-block finding above is downgraded to a warning for the duration | INFO |

Any `dep:*` FAIL blocks `SAFE TO RUN` — it means this block is wired to something
the other end does not actually provide. The one exception is when
`dep:smoke-overlay` is present: those findings are artifacts of the running
smoke, not real drift, and must not be reported as such.

## Step 2 — Live checks

Things `dryrun.sh` can't decide from a static snapshot. Judge them here.
Only two outcomes block launch — a foreign job holding the GPUs
(`gpu:foreign`) and an already-running trainer training process
(`job:running`); the checkpoint-clobber check is advisory.

### 2a — Is a trainer training run already in flight?

A second concurrent run on the same GPUs will OOM or corrupt both. Probe:

```bash
pgrep -af 'llamafactory.cli train'   # the training launcher
pgrep -af 'scripts/train.sh'         # the wrapping pipeline
```

| Observation | Name | Status |
|---|---|:---:|
| a `llamafactory.cli train` / `train.sh` process is alive | `job:running` | ✗ blocks |
| nothing matches | `job:none` | ✓ |

On `job:running`, report the PID + `etime` (`ps -p <pid> -o pid=,etime=`)
and tell the user to let it finish or stop it (`kill <pid>`), not to launch
a second run.

### 2b — Are the GPUs free, ours, or foreign?

```bash
nvidia-smi --query-compute-apps=pid,used_memory,process_name --format=csv,noheader 2>/dev/null
```

| Owner | Name | Status |
|---|---|:---:|
| no compute apps listed | `gpu:idle` | ✓ |
| PIDs trace to *our* training run (2a `job:running`) | `gpu:mine` | ⚠ |
| a foreign PID holds GPU memory | `gpu:foreign` | ✗ blocks |

`gpu:foreign` means the run won't get the GPUs it needs — block, report
PID + memory, and let the user decide whether to wait or stop it. If
`dryrun.sh` already `[FAIL]`ed on `nvidia-smi not found`, emit `gpu:no-smi`
(✗) and skip this probe.

### 2c — Will training overwrite an existing checkpoint?

The scripts reject a non-empty output directory unless a checkpoint resume is
configured or destructive overwrite is explicitly acknowledged. Check it:

```bash
ls -d <resolved_output_dir>/checkpoint-* 2>/dev/null
```

| Observation | Name | Status |
|---|---|:---:|
| dir absent or empty | `ckpt:clean` | ✓ |
| checkpoints present without resume | `ckpt:clobber` | ✗ blocks |

Block and ask the user to rename `output_dir` or set a valid
`resume_from_checkpoint`. Only an explicit destructive request may combine
`overwrite_output_dir: true` with `SFT_ALLOW_OVERWRITE_OUTPUT=1`. Note that STEP 0/1 of
`train.sh` are idempotent: existing Harbor conversion outputs are reused,
exact Hub files use the Hugging Face cache, and dataset registration is updated
only when its desired entry differs.

---

## Step 3 — The report  (always the last thing you print)

The report **is** the deliverable. Print it every single time — even on an
abort (then: heading + a `NO` verdict whose reason is the abort message,
nothing else). Fill this template exactly; drop only truly inapplicable
rows.

````
## trainer block check — CWD=<relative path>

**SAFE TO RUN: <✅ YES | ❌ NO>** — <R> required · <A> advisory · <W> warnings

| Layer | Check | Status | Detail |
|-------|-------|:------:|--------|
| det  | config · uv-env · repos · imports · converter · paths · dataset · model · output_dir | ✓ | ok=<N> |
| det  | <each FAIL/WARN det check> | <✗/⚠> | <verbatim dryrun line> |
| det  | dependency wiring | <✓/✗> | <ok: N edges, both ends \| dep:link-mismatch … \| suppressed: smoke overlay> |
| det  | wandb         | <✓/✗>   | <mode=offline/online/disabled · key set?> |
| det  | gpu-count     | <✓/⚠>   | <nvidia-smi N vs config N_GPUS> |
| det  | cloudflare tunnel (optional) | <✓/⚠> | <cloudflared found \| missing, dashboard TUNNEL=true will be local-only> |
| det  | cloudflare account (optional) | <✓/·> | <shared credentials present (source: …) \| not configured — the quick tunnel needs none> |
| live | job           | <✓/✗>   | <job:none / job:running pid=<P>> |
| live | gpu           | <✓/⚠/✗> | <gpu:idle / gpu:mine / gpu:foreign pid=<P> mem=<M>> |
| live | checkpoint    | <✓/⚠>   | <ckpt:clean / ckpt:clobber: <dir>> |

**Run configuration**
```
source:     <source.type>
source_ref: <harbor scaffold+job_dir | hf_hub_url[/hf_file_name] | local lf_path>
data_name:  <conversion.data_name>   dataset: <dataset.name or auto>
model:      <model.model_name_or_path>
training:   template=<t> epochs=<e> lr=<lr> gbs=<pbs×accum×gpus> cutoff=<cutoff_len>
output_dir: <training.output_dir>
wandb:      mode=<wandb_mode> run_id=<wandb_run_id or auto>
gpus:       <n_gpus_per_node>
```

**Next steps**
1. <one per failure, required first; quote the dryrun [FAIL] line verbatim>
2. ...
Re-run `/trainer:check`.
````

**The three invariants:**

1. **Verdict** — `✅ YES` iff `R == 0`, where `R` = dryrun `[FAIL]` count
   **+** any `job:running` **+** any `gpu:foreign`/`gpu:no-smi`. Advisory
   failures (`ckpt:clobber`) and warnings *never* change it.
2. **Glyphs** — `✓` pass · `✗` blocks · `⚠` advisory/warning · `·` skipped.
3. **Collapse** — fold all passing `det` checks into the first row; add a
   row only for each `det` check that is `✗` or `⚠`.

---

## Guardrails — never do these

- edit `config.yaml`, fill in `runtime_info.input.*`, or write to
  `artifacts/` / `dashboard/`
- run `start.sh`, `train.sh`, `dataprep.sh`, `install_env.sh`, or
  `clean.sh`
- re-implement or override a `dryrun.sh` check (add only the live layer)
- modify anything under `repos/` — it is pinned, read-only code (the Repos
  rule in `BLOCK_DEFINITION.md`)
- `kill` a process — surface the conflict, let the user decide
- SSH to other nodes — this block runs locally (`meta_info.resources.ip:
  null`); `/trainer:check` is head-node only

---

## Config reference (moved from config.yaml — do not re-add as comments)

- **overwrite_output_dir: false** means an existing `training.output_dir` will NOT be replaced — an explicit env override is required to overwrite a previous run. Treat an existing output dir + false as "will refuse", not as an error.
- **credentials.hf_token** is only required for private `source.type: hf_lf` datasets; empty is normal for public ones. `wandb_api_key` stays empty — the key flows via `$WANDB_API_KEY`.
- **runtime_info.output** is written back by `scripts/train.sh` STEP 3 (comment-preserving, flock-guarded). Its pre-run shape (value: null entries) is the contract — do not "fix" the nulls.
## Shared LegoFlow CLI

The canonical execution command for this skill is `./bin/legoflow check trainer`. Claude Code and Codex use this same command; this skill supplies the agent-specific confirmation, reporting, and artifact-analysis workflow around it.
