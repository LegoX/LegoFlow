# CI self-hosted runners

All CI jobs run on the `SWE-Lego/SWE-Lego-Live` self-hosted runner pool. There
are two label tiers:

| Label | Runs | Where |
|---|---|---|
| `swe-lego-ci` | every cheap/CPU + Docker job (cases, swegen/trajgen/eval smokes) | the generic CI host(s) |
| `swe-lego-gpu` | **only** the `sft-smoke` job — real 8-GPU DeepSpeed ZeRO-3 training | the GPU machine (8× L20X) |

## Why `sft-smoke` is special

`sft-smoke` is the **one training job in CI**. It launches full-parameter
Qwen3-8B training at `cutoff_len=131072` across 8 GPUs (see
`subblock/sft/tests/smoke/10_train_demo.sh`). Every other CI job is CPU/Docker
work that any `swe-lego-ci` runner can take. The training smoke must land on the
host that actually has the 8 GPUs, so its `runs-on` in `.github/workflows/ci.yml`
is pinned to `[self-hosted, swe-lego-gpu]` — a label that **only the GPU
machine's runner carries**. If no GPU runner is online the job queues (gated to
`workflow_dispatch run_smoke=true` or pushes to `dev`/`main`) rather than risk
running on a CPU-only runner, where the smoke's own gate would SKIP anyway.

## Registering the GPU runner (on the GPU machine)

Run `register-gpu-runner.sh` **on the GPU host itself** (so the runner binds to
that machine and sees its GPUs). Get a registration token from
`https://github.com/SWE-Lego/SWE-Lego-Live/settings/actions/runners/new`
(or `gh api -X POST repos/SWE-Lego/SWE-Lego-Live/actions/runners/registration-token -q .token`):

```bash
REG_TOKEN=<token> bash .github/runners/register-gpu-runner.sh
```

It registers a runner with labels `swe-lego-ci,swe-lego-gpu` (so the box also
serves the generic pool) and a name derived from the host. Start it with
`./run.sh` in the runner dir, or install it as a service
(`sudo ./svc.sh install && sudo ./svc.sh start`).

To confirm the label is live: the runner appears under repo Settings → Actions →
Runners with a `swe-lego-gpu` label, and a manual `Run workflow` (Actions → CI →
Run workflow, *Also run smoke tests* = true) dispatches `sft-smoke` to it.
