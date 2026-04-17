# Data Composer Program

## Purpose
Data Composer aggregates the task corpus from SWE-gen with any external datasets, applies quality filtering, and produces a versioned training mixture for the training engine. It is the quality gate between raw task generation and model training.

It manages:
- Ingestion of SWE-gen task output
- Optional external dataset registration
- Quality filtering (difficulty, validation status, category balance)
- Mixture policy (source weights, sampling ratios)
- Dataset versioning and manifest generation

## WorkSpace Definition

```yaml
name: data_composer
summary: >
  Idle. Current dataset: v0.1 (N tasks).
  Next: ingest latest SWE-gen output and rebalance mixture.

repo: null

program: program.md

inputs:
  generated_tasks:
    description: Harbor task corpus produced by SWE-gen
    source: swe_gen.outputs.task_corpus
  external_datasets:
    description: Optional external datasets to mix in (e.g. SWE-bench, aider-polyglot)
    source: workspace.config.external_data_sources

config:
  mixture_policy:
    description: Source weights for the training mixture (e.g. swe_gen=0.7, external=0.3)
    editable_by: [human, agent]
  quality_threshold:
    description: Minimum quality score for keeping a task (based on validation status and difficulty)
    editable_by: [human, agent]
  difficulty_filter:
    description: Which difficulty levels to include (easy | medium | hard | all)
    editable_by: [human, agent]
  max_tasks_per_repo:
    description: Cap on tasks from a single repo to avoid distribution skew
    editable_by: [human, agent]
  dataset_version:
    description: Version tag for the output dataset (e.g. v0.1)
    editable_by: [human]

status:
  phase:
    description: idle | ingesting | filtering | composing | completed | failed
  tasks_ingested:
    description: Total tasks ingested from all sources
  tasks_after_filter:
    description: Tasks remaining after quality filtering
  current_version:
    description: Version tag of the most recently produced dataset

outputs:
  dataset_version:
    description: Versioned dataset ready for training (path + manifest)
  task_count:
    description: Number of tasks in the output dataset
  rejected_tasks:
    description: Tasks excluded during filtering, with rejection reasons
  mixture_summary:
    description: Breakdown of source contributions and difficulty distribution

artifacts:
  - type: dataset_manifest
    path: artifacts/datasets/<version>/manifest.json
    producer: data_composer
    description: Full manifest of the versioned dataset including task paths and metadata
  - type: mixture_report
    path: artifacts/datasets/<version>/mixture_report.md
    producer: data_composer
    description: Human-readable summary of source weights, filtering decisions, and distribution stats

memory:
  path: memory/
  description: Notes on mixture experiments, quality issues observed, and filtering decisions

subspace: {}
```

## Notes
- Upstream dependencies: SWE-gen task corpus; optional external dataset paths
- Failure modes: empty corpus after filtering (thresholds too strict), version collision
- Open questions: exact mixture policy for v0.1; whether to include SWE-bench as an external source
