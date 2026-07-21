---
name: setup
description: >
  Bootstrap the rl block from a fresh clone to "/rl:check passes":
  preflight tooling (uv, git, system python3 + PyYAML, docker or kubectl
  depending on backend), check out the repos under repos/ at their pinned
  commits (harbor-verl-train @ ydu_dev, harbor @ 9f98f9d, verl @ bcb638649
  with the verl_bcb638649.patch applied by setup_env.sh), build or verify
  the venv at repos/harbor-verl-train/.venv via setup_env.sh — or wire up
  an existing venv through runtime_info.input.environment.venv_path after
  verifying its editable installs point at this tree — then fill in
  runtime_info.input gaps (model_path, data indexes, k8s kubeconfig or
  Docker mode fields, wandb mode), prompting only for unset or placeholder
  values and keeping the wandb key out of config.yaml. Idempotent; ends by
  handing off to /rl:check (which owns the dryrun + preflight report)
  rather than re-running dryrun itself. Triggers on phrases like "set up
  rl", "bootstrap rl", "install the rl venv", "prepare rl before training",
  "wire up rl config".
---

# /rl:setup

Brings the rl block from a fresh clone to "**`/rl:check` passes**". The
skill is idempotent: any step already satisfied is skipped. It never runs
training — that's `/rl:run`.

`/root:setup` delegates here when the user opts into recursive bootstrap
from the repo root (it hands off to `/<block>:setup` per subblock). The
behaviour is identical either way — the Step 0 CWD check still applies, so
the caller is expected to invoke this skill from inside `subblock/rl/`.

## Step 0 — Orient

Run only from inside the rl block. Validate `./config.yaml` exists and
`meta_info.name == 'rl'`; otherwise abort ("run from `subblock/rl/`").
Read `config.yaml` and `CLAUDE.md`.

Resolve the venv path once:
`VENV = runtime_info.input.environment.venv_path` if set, else the default
`repos/harbor-verl-train/.venv`. `PY = $VENV/bin/python`.

## Step 1 — Tooling preflight

The build scripts and the inline config readers depend on these:

- **`uv`** — `setup_env.sh` builds the venv with it. If missing on PATH:
  install to a writable location
  (`curl -LsSf https://astral.sh/uv/install.sh | sh`; on shared hosts where
  `~/.local/bin` is root-owned, use `UV_INSTALL_DIR="$HOME/.uv/bin"
  UV_UNMANAGED_INSTALL=1` and persist `PATH="$HOME/.uv/bin:$PATH"`).
- **`git`** — submodule checkout and the verl patch application.
- **system `python3` + PyYAML** — `scripts/train_1node_cc.sh` and
  `scripts/dryrun.sh` read `config.yaml` through an inline python `cfg`
  helper. `python3 -c 'import yaml'` must succeed; `pip install --user
  pyyaml` if not.
- **backend CLI** — `kubectl` if `config.yaml` is in K8s mode
  (`harbor_agent.environment_import_path` pointing at the k8s environment),
  or a reachable Docker daemon if in Docker mode. Don't install cluster
  tooling silently — if it's missing, tell the user.
- **network** — `setup_env.sh` pulls pinned `vllm`, `flash_attn`, `cupy`,
  `transformers` wheels. If the host is offline, stop here — don't
  half-build the venv.

## Step 2 — Repos at pinned commits

The repos under `repos/` are git submodules; a fresh clone leaves them
empty. Expected pins (see `CLAUDE.md` → Repos):

| Repo | Pin |
|---|---|
| `repos/harbor-verl-train` | branch `ydu_dev` |
| `repos/harbor` | `9f98f9d` (branch `ydu_dev`) |
| `repos/verl` | `bcb638649` + `harbor-verl-train/patches/verl_bcb638649.patch` |

1. If a `repos/<path>` is empty or missing, initialise it from the repo
   root:
   ```bash
   git submodule update --init subblock/rl/repos/harbor-verl-train \
     subblock/rl/repos/harbor subblock/rl/repos/verl
   ```
   Private repos with SSH remotes fail on token-only hosts — override the
   submodule URL to `https://<TOKEN>@github.com/...` first if needed.
2. Verify each checkout: `git -C repos/<path> rev-parse HEAD` against the
   pin. The verl **patch** is applied by `setup_env.sh` — do not apply it
   by hand, and do not "fix" a patched verl tree back to the pristine
   commit (that's expected drift).
3. If a worktree has *other* local edits, **report it and ask** — per
   `BLOCK_DEFINITION.md`, `repos/` is pinned, read-only code; `:setup`
   configures and pins, it does not patch.

## Step 3 — Build / verify the venv

The canonical builder is `repos/harbor-verl-train/scripts/setup_env.sh`:
it creates `repos/harbor-verl-train/.venv` via uv and `pip install -e`'s
harbor / verl / harbor-verl-train, then installs the pinned wheels.
(`scripts/start.sh` also auto-runs it when `.venv` is missing, but running
it here surfaces build errors before launch day.)

Decision:

- **Default venv missing** (`$PY` not executable, `environment.venv_path`
  empty) → run it:
  ```bash
  bash repos/harbor-verl-train/scripts/setup_env.sh
  ```
  This downloads several GB and takes a while — tell the user before
  starting it.
- **Reusing an existing venv** (`environment.venv_path` set, or the user
  points at a sibling block's venv) → **verify the editable installs point
  at this source tree** before accepting it:
  ```bash
  "$PY" -c "import harbor, verl, verl_patch; print(harbor.__file__); print(verl.__file__)"
  ```
  The printed paths must resolve under this block's `repos/`. A venv whose
  editable paths point elsewhere will silently run different code — refuse
  it and tell the user which tree it actually points at.
- **Venv present and imports pass** → leave it. Never rebuild a working
  venv without asking (the rebuild is destructive and slow).

**Docker mode extra**: Harbor needs the Python `docker` SDK in the venv
(`uv pip install --python $PY docker`). Verify with
`"$PY" -c "from docker import DockerClient; print('ok')"` — and watch for
namespace shadowing: a `docker/` directory on `sys.path` (e.g.
`verl/docker/`) can mask the real SDK.

## Step 4 — Fill `runtime_info.input` (prompt only for gaps)

Walk these keys; for each that is the literal `human` (the must-fill marker —
always prompt for these), or an obvious placeholder (a path that doesn't exist
on this host), prompt with the current value as default. `""` fields are
env/auto-supplied and `null` is a semantic default — do not prompt for those
unless their inline comment says otherwise. Write accepted values back into `config.yaml`, preserving comments
and formatting. Do **not** invent a model path, data index, or kubeconfig —
a missing input is the user's signal to provide one, never a signal to
fabricate a path.

| Key | What to ask / derive |
|---|---|
| `model.model_path` | local base-model dir (must exist; `dryrun.sh` checks it and validates `vllm.gen_tp` divides its KV heads) |
| `data.train_index` / `data.val_index` | harbor task index parquets (must exist) |
| `environment.venv_path` | empty = default `.venv` (recommended); else the verified venv from Step 3 |
| `k8s.kubeconfig` | only in K8s mode; must exist and `kubectl --kubeconfig <f> get nodes` should answer |
| `harbor_agent.environment_import_path` / `docker_host` | only when switching to Docker mode — see `CLAUDE.md` → Docker Mode for the exact values; never flip the backend without the user asking |
| `experiment.project_name` / `exp_name` | `exp_name` empty = auto-generated at launch (recommended) |
| `credentials.wandb_mode` | `online` \| `offline` \| `disabled` |
| `credentials.wandb_api_key` | **keep empty in `config.yaml`** — this block reads the key from the `$WANDB_API_KEY` env var at launch (`dryrun.sh` checks both sources). Tell the user to `export WANDB_API_KEY=...` in their shell, or set `wandb_mode: disabled`. Never write a real key into the config. |

## Step 5 — Hand off to `/rl:check`

Do **not** re-run `scripts/dryrun.sh` here — verification is `/rl:check`'s
job, and it already wraps the dryrun (plus the live job/port/GPU probes and
the SAFE-TO-RUN report). Running dryrun in `:setup` too would just print
the same checks twice.

Instead, tell the user setup is done and the next step is **`/rl:check`**.
Since check is read-only, you may invoke it yourself and surface its
verdict — that confirms the bootstrap landed without duplicating the
dryrun output. A `❌ NO` here typically means a Step 4 input still points
at a path that doesn't exist on this host (model dir, data index,
kubeconfig) — fix those, not the venv.

## Guardrails

- Idempotent: re-running must not duplicate work or destroy a working venv
  without asking.
- Never modify files under `repos/` beyond checking out the pinned commits
  (the verl patch belongs to `setup_env.sh`, not to you); report drift,
  don't paper over it.
- Never write a real wandb key into `config.yaml` — it comes from
  `$WANDB_API_KEY` at launch.
- Never run training, launch LiteLLM/Ray, or call `start.sh` — that's
  `/rl:run`.
- Local block: don't SSH anywhere (`meta_info.resources.ip` is null/local).

---

## Config reference (moved from config.yaml — do not re-add as comments)

### UPSTREAM-FIXED sections

`runtime_info.input.vllm`, `.training`, and `.algorithm` are documentation-only mirrors of the hardcoded values in `repos/harbor-verl-train/scripts/sync_1node_cc.sh` (only `test_freq` is env-driven). **Editing them in config.yaml does NOT change the run** — to override, edit the upstream script (or fork it). Keep the mirror in sync when the upstream script changes so config.yaml documents the live state.

### environment.venv_path

Empty → the default `repos/harbor-verl-train/.venv` (built by `setup_env.sh` on first `scripts/start.sh`). Point it at an existing venv (absolute or block-relative) to skip bootstrap — but the venv MUST have harbor / verl / harbor-verl-train installed **editable from this tree**; mismatched editable paths silently run the wrong code. Verify with:
`$VENV_PATH/bin/python -c "import harbor, verl, verl_patch; print(harbor.__file__)"`
