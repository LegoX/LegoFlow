# Harbor Program

## Purpose
Harbor is the execution bridge for the pipeline. It runs coding agents (claude-code, openhands, aider, codex, etc.) against Harbor-format tasks in isolated containerized environments (Docker, Daytona, Modal, E2B, GKE). It produces trial results and reward signals used by both evaluation and training.

Harbor is used in two roles within this pipeline:
1. **Validation** — SWE-gen uses Harbor internally to validate generated tasks (NOP agent should fail, Oracle agent should pass).
2. **Evaluation** — The orchestrator runs Harbor jobs to benchmark the current model against the task corpus.

The underlying repo is `harbor`.

## WorkSpace Definition

```yaml
name: harbor
summary: >
  Idle. No active job running.
  Last job: N trials, pass@1=X.XX on dataset Y.
  Next: trigger evaluation job for the latest model checkpoint.

repo:
  path: ../harbor

program: program.md

inputs:
  task_corpus:
    description: Harbor-format tasks to evaluate against
    source: swe_gen.outputs.task_corpus
  model_checkpoint:
    description: Model checkpoint or API endpoint to evaluate
    source: training.outputs.model_checkpoint
  agent_config:
    description: Agent name and model to use (e.g. claude-code + anthropic/claude-opus-4-6)
    source: workspace.config.agent_config

config:
  environment_type:
    description: Execution environment (docker | daytona | modal | e2b | gke)
    editable_by: [human, agent]
  n_concurrent_trials:
    description: Number of trials to run in parallel
    editable_by: [human, agent]
  n_attempts:
    description: Number of retry attempts per trial
    editable_by: [human, agent]
  timeout_multiplier:
    description: Global timeout scaling factor
    editable_by: [human, agent]

status:
  phase:
    description: idle | running | completed | failed
  current_job_id:
    description: UUID of the active job
  n_trials_total:
    description: Total number of trials in the current job
  n_trials_completed:
    description: Trials completed so far
  n_trials_failed:
    description: Trials that errored out
  job_dir:
    description: Path to the current job output directory

outputs:
  job_result:
    description: Aggregated job statistics including pass@k and reward distributions per agent/dataset
  trial_results:
    description: Per-trial results with agent trajectory, verifier reward, and timing
  pass_at_1:
    description: Primary evaluation metric (fraction of tasks solved on first attempt)

artifacts:
  - type: job_result
    path: jobs/<job_id>/job_result.json
    producer: harbor
    description: Aggregated job statistics
  - type: trial_logs
    path: jobs/<job_id>/trials/
    producer: harbor
    description: Per-trial logs, trajectories, and verifier outputs

memory:
  path: memory/
  description: Notes on evaluation runs, agent performance trends, and environment issues

subspace: {}
```

## CLI Reference

```bash
# Run an evaluation job
harbor run --dataset swe-lego-live@latest --agent claude-code --model anthropic/claude-opus-4-6

# Run with a config file
harbor job start --config eval_job.yaml

# List available datasets
harbor dataset list

# Check job status
harbor job status <job_id>
```

## Notes
- Upstream dependencies: Docker (or cloud environment credentials), agent API keys
- Failure modes: container build failures, agent timeouts, environment provisioning errors
- Open questions: which environment type to use for scale; cost/latency tradeoffs between Docker and cloud environments
