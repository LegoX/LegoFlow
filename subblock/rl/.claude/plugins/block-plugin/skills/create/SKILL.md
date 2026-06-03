---
name: create
description: >
  Scaffold a new RL experiment slot inside the rl block — a directory under
  artifacts/runs/<exp_name>/ that captures the hypothesis, the config diff
  vs the current baseline, a snapshot of config.yaml at scaffold time, and
  an empty notes file. Does NOT launch training (use /block:run for that).
  Use whenever the user wants to try a new RL variant: a different model,
  a new algorithm setting, a parallelism sweep, a debugging re-run with
  one knob changed. Triggers on phrases like "create an experiment", "set
  up a new RL run", "make an experiment slot for X", "scaffold the next
  training variant", "scaffold a new rl experiment", "make a new run slot". Refuses if the current
  working directory is not the rl block (must contain rl-shaped config.yaml).
---

# /block:create

Scaffold one experiment slot for the RL block. Each slot is a self-contained
record of *intent + baseline*, written before launch — so when the run
finishes (or crashes), you can diff against this slot to understand what
was being tested. Does not modify `config.yaml` and does not launch.

## Step 0 — Orient

The "rl block" is the current working directory. Validate:

1. `./config.yaml` exists, parses, and has `meta_info.name == 'rl'`. If not,
   abort: "/block:create must be run from inside the rl block (`subblock/rl/`)."
2. `./artifacts/` exists; create `./artifacts/runs/` if missing.

Read these as context (don't load them into the user's response — just use them):
- `./config.yaml` — current baseline
- `./CLAUDE.md` — the block contract (knob plumbing, env-driven vs upstream-fixed tiers)
- `./artifacts/index.yaml` — past runs

## Step 1 — Interview

Collect in a single conversational message, accept short answers:

1. **Goal** — one sentence: what is this experiment testing?
2. **Hypothesis** — what do you expect to happen vs the last run?
3. **Knob changes** — for each change, `<dotted.path>: <new value>` (relative to `runtime_info.input`). Accept `none` if reproducing the baseline.
4. **Tag** — short kebab-case identifier (≤ 24 chars) that summarises the variant. Examples: `tp4-grpo-baseline`, `bigger-batch-128`, `tail-kill-on`. If the user is in a hurry, derive one from the knob changes.

If the user says "just scaffold it from the current config", fill `goal: "reproduce current baseline"`, `hypothesis: null`, `knob_changes: []`, and derive a tag from a timestamp.

## Step 2 — Generate exp_name

Format: `<tag>-<UTC YYYYMMDD-HHMMSS>`. Example: `tp4-grpo-baseline-20260522-153012`.

Check this directory does not already exist under `./artifacts/runs/`. If it
does (rare collision), append a `-2` suffix and retry.

## Step 3 — Write the slot

Create `./artifacts/runs/<exp_name>/` and write:

### `experiment.yaml`

```yaml
id: <exp_name>
tag: <tag>
created_at: "<UTC now ISO>"
created_from_config_commit: <git rev-parse --short HEAD of the rl block's parent repo, or "dirty" if working tree has uncommitted changes>
status: scaffolded         # scaffolded | running | completed | failed

goal: <goal sentence>
hypothesis: <hypothesis or null>

baseline:
  config_yaml_snapshot: config.yaml.snapshot   # see file below
  notable_knobs:                                # extract these from config.yaml for at-a-glance reference
    model_path: <runtime_info.input.model.model_path>
    train_batch_size: <runtime_info.input.training.train_batch_size>
    n_resp_per_prompt: <runtime_info.input.training.n_resp_per_prompt>
    vllm_gen_tp: <runtime_info.input.vllm.gen_tp>
    adv_estimator: <runtime_info.input.algorithm.adv_estimator>
    policy_loss_mode: <runtime_info.input.algorithm.policy_loss_mode>
    num_workers: <runtime_info.input.harbor_runtime.num_workers>

changes:
  # one entry per Step-1 knob change; empty list if "reproduce baseline"
  - path: runtime_info.input.<dotted.path>
    from: <baseline value>
    to: <new value>

expected_metrics:
  # optional — leave empty if the user didn't specify
  val_resolve_rate: null
  train_reward: null
  notes: null

actual:
  # filled by /block:run once the run completes — leave null here
  started_at: null
  completed_at: null
  exit_status: null
  final_val_resolve_rate: null
  log_path: null
  trajectory_dir: null
```

### `config.yaml.snapshot`

Verbatim copy of the rl block's `./config.yaml` at scaffold time. This is
the source of truth for what the run intends to use — if the user later
wants to apply the slot's knob changes, they can diff `config.yaml.snapshot`
against this file and apply the deltas to the live `config.yaml` before
`/block:run`. (`/block:create` does **not** mutate the live config.)

### `notes.md`

```markdown
# <exp_name>

## <today's date> — scaffolded

- Goal: <goal>
- Hypothesis: <hypothesis or "—">
- Changes vs baseline:
<bulleted list of changes from Step 1, or "none — reproducing baseline">

## TODO

- [ ] Apply the changes above to `subblock/rl/config.yaml` (or confirm baseline reproduction).
- [ ] Run `/block:check` to preflight.
- [ ] Run `/block:run` to launch.
- [ ] After launch, update `runtime_info.input.experiment.exp_name` to `<exp_name>` so the upstream log filename matches this slot.
```

## Step 4 — Report

Print a tight summary (≤ 10 lines):

```
Created: artifacts/runs/<exp_name>/
  experiment.yaml      (intent + baseline + planned changes)
  config.yaml.snapshot (frozen at scaffold time)
  notes.md             (running log; edit freely)

Next:
  1. Apply the knob changes to subblock/rl/config.yaml.
  2. Set runtime_info.input.experiment.exp_name: <exp_name>.
  3. /block:check, then /block:run.
```

## What this skill must NOT do

- Do not edit `./config.yaml` — only snapshot it. The user applies their own knob changes.
- Do not append to `./artifacts/index.yaml` — `/block:run` does that when the run actually starts.
- Do not launch training, set up venvs, or call any upstream script.
- Do not invent values for `goal` or `hypothesis`; if the user is reproducing baseline, write that literally.
