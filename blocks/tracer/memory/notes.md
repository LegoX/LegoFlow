# Tracer Notes

## Repository Policy

`repos/harbor` is a managed local dependency. It is intentionally ignored by
the Live repository and should not be edited as part of this block.

Use `scripts/update_repos.sh` to clone, fetch, and checkout the configured
Harbor ref. If Harbor code needs changes, make them in the Harbor repository
itself and update this block to the resulting branch, tag, or commit.

## Downstream Contract

The SFT block already expects `source_block: tracer` for raw trajectories.
The handoff fields are:

- `config.yaml -> harbor_job.jobs_dir`
- derived latest job dir: `{harbor_job.jobs_dir}/latest`
- derived producer/output format: `tracer` / `litellm_logger_v0.1`
- derived trajectory file pattern: `{job_dir}/<task-id>/agent/litellm-trajectory.jsonl`

Copy those values into `blocks/trainer/inputs.yaml` when preparing an SFT run.
