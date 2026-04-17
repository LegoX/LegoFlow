# Training Program

## Purpose
The Training module executes model training on the dataset produced by data_composer. It supports two training methodologies:
- **SFT** (Supervised Fine-Tuning): trains on oracle trajectories or curated demonstrations
- **RL** (Reinforcement Learning): uses Harbor reward signals for policy optimization via LF (Learning from Feedback) or VeRL

It produces model checkpoints that are passed to Harbor for evaluation, closing the feedback loop.

## WorkSpace Definition

```yaml
name: training
summary: >
  Idle. Last run: SFT on dataset v0.1, checkpoint saved at artifacts/checkpoints/v0.1-sft/.
  Next: evaluate checkpoint with Harbor, then decide on RL iteration.

repo: null

program: program.md

inputs:
  dataset:
    description: Versioned training dataset from data_composer
    source: data_composer.outputs.dataset_version
  base_model:
    description: Base model to fine-tune (model name or local path)
    source: workspace.config.base_model
  eval_baseline:
    description: Previous evaluation result to compare against
    source: harbor.outputs.job_result

config:
  training_method:
    description: Training methodology to use (sft | lf | verl)
    editable_by: [human, agent]
  learning_rate:
    description: Learning rate for the optimizer
    editable_by: [human, agent]
  batch_size:
    description: Training batch size
    editable_by: [human, agent]
  num_epochs:
    description: Number of training epochs (SFT) or iterations (RL)
    editable_by: [human, agent]
  gradient_accumulation_steps:
    description: Gradient accumulation steps for effective batch size scaling
    editable_by: [human, agent]
  checkpoint_dir:
    description: Directory to save model checkpoints
    editable_by: [human]

status:
  phase:
    description: idle | running | completed | failed
  current_epoch:
    description: Current training epoch or RL iteration
  total_epochs:
    description: Total planned epochs or iterations
  train_loss:
    description: Most recent training loss
  eval_loss:
    description: Most recent evaluation loss (SFT only)
  elapsed_seconds:
    description: Wall-clock time elapsed in the current run

outputs:
  model_checkpoint:
    description: Path to the trained model checkpoint for Harbor evaluation
  training_summary:
    description: Final training metrics (loss curve, convergence status)
  scientific_findings:
    description: Agent-generated bullet-point summary of key observations from this training run

artifacts:
  - type: model_checkpoint
    path: artifacts/checkpoints/<version>/
    producer: training
    description: Trained model weights and tokenizer
  - type: training_log
    path: artifacts/logs/<run_id>/training.log
    producer: training
    description: Full training log with loss curves and hyperparameters
  - type: findings_report
    path: memory/findings_<run_id>.md
    producer: training
    description: Scientific findings and key observations from this training run

memory:
  path: memory/
  description: Accumulated training findings, hyperparameter experiments, and convergence notes

subspace:
  sft:
    description: SFT-specific configuration and run history
  rl:
    description: RL-specific configuration and run history
```

## Notes
- Upstream dependencies: data_composer dataset version; GPU cluster access
- Failure modes: OOM during training, divergent loss, checkpoint corruption
- Open questions: which base model to start from; SFT-first vs RL-first strategy; VeRL vs LF for RL phase
