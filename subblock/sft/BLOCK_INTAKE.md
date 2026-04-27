# Block Intake Form

Fill in this file and hand it to the agent. The agent will use it to scaffold your block directory.

You only need to answer the questions marked **required**. Leave optional fields as `null` if you don't know yet.

---

## 1. Identity (required)

```yaml
# A short, stable snake_case name. No spaces. e.g. data_curation, sft_training
name: sft_training

# A human-readable label. e.g. "Data Curation" (optional)
label: "SFT Training"

# One sentence: what does this block do?
role: >
  Converts raw agent trajectories from multiple scaffolds (Claude Code, OpenCode,
  OpenHands, OpenHands SDK, Terminus2) into LLaMA-Factory sharegpt format and trains
  coding models via LLaMA-Factory + DeepSpeed ZeRO-3 on SWE-bench trajectory data.
```

---

## 2. Position in the Tree (required)

```yaml
# Name of the parent block. Write null if this is the root block.
parent: swe_lego_live

# Names of direct child blocks this block owns. Write [] if none (leaf block).
children: []
```

---

## 3. Code Repositories (optional)

List any existing code repos that belong to this block.

```yaml
repos:
  - path: repos/LLaMA-Factory
    role: training framework (LLaMA-Factory + DeepSpeed ZeRO-3 distributed training)
  - path: repos/swe_data_process
    role: data processing package (trajectory converters for all scaffolds, rule/LLM quality scorers)
```

---

## 4. Input Dependencies (required)

What does this block need before it can run? Include both external inputs (API keys, datasets, human decisions) and inputs produced by other blocks.

```yaml
inputs:
  - name: raw_trajectories
    description: >
      Per-scaffold raw trajectory files in jierun or chaofan format. For jierun:
      a harbor job directory. For chaofan: a completions source directory.
    source_block: null
    required: true

  - name: base_model
    description: >
      Model checkpoint to initialize training from (e.g. Qwen3-8B). Must be
      accessible as a local directory on the compute node.
    source_block: null
    required: true
```

---

## 5. Output Dependencies (required)

What does this block produce? Who consumes it?

```yaml
outputs:
  - name: checkpoint_path
    description: >
      Path to the trained model checkpoint under artifacts/model/{run_name}/.
      Latest checkpoint subdirectory (checkpoint-N) or full output dir.
    consumer_block: null

  - name: training_curves
    description: >
      WandB run ID with loss, learning rate, and token throughput metrics.
      Available in online/offline mode; null when wandb_mode=disabled.
    consumer_block: null

  - name: train_results
    description: >
      train_results.json with final loss, runtime, and throughput metrics.
      Located at {checkpoint_path}/../train_results.json.
    consumer_block: null
```

---

## 6. Resources (optional)

The GPU/CPU node address and mounted storage path assigned for this block. Once the agent starts, it will automatically march to the assigned server.

```yaml
resources:
  ip: local        # single compute node with 8× A100/H100 GPUs
  user: root
  pwd: null
  gpu: "8× H200 per node"
```

---

## 7. Monitoring (required)

Tell how the agent how to monitor the progress and status of this block. What are the key results to be presented back to users. According to your requirement, the agent will integrate the `/logs`, `status.yaml`, all related stuff and present them on the html page.

```yaml
Present the following in dashboard/status.mdx (auto-refreshed every 30s during training):
  1. Config summary: base model, dataset name, template, lr, epochs, global batch size, output dir
  2. Live training progress: current step / total steps, % complete, epoch, loss, lr, elapsed time, ETA
  3. Final results (post-run): final loss, total runtime, samples/s, steps/s, total epochs
  4. Loss curve: training_loss.png embedded from artifacts/model/{run_name}/
  5. Experiment tracking: row appended to artifacts/实验追踪表.xlsx after each run
     (dataset label, scaffold, think mode, teacher model, token stats, turn stats, hyperparams, output path)
```

---

## 8. Evolving (optional)

Tell the agent what can be evolved, e.g., by adjusting what input parameters in inputs.yaml, and what are the results to be observed. What are the experiences that can be turned into `/memory`.

```yaml
Tunable parameters in inputs.yaml:
  - source.provider + source.scaffold: switch between jierun/chaofan and different agent scaffolds
  - source.job_dir / source.source_dir: point to a new trajectory batch
  - conversion.max_instances: control dataset size (64 = fast iteration, 0 = full dataset)
  - model.model_name_or_path: swap base model (Qwen3-8B → Qwen3-32B, etc.)
  - training.template: qwen3_nothink (no CoT) vs qwen3 (with chain-of-thought)
  - training.num_train_epochs / learning_rate / gradient_accumulation_steps: standard hyperparams
  - training.deepspeed: switch ZeRO stage (z0/z2/z3) based on model size and GPU memory
  - training.save_only_model: false enables checkpoint resumption

Observations to record in memory/notes.md:
  - Final loss per run and whether it correlates with downstream eval performance
  - Effect of think mode (qwen3 vs qwen3_nothink) on SWE-bench resolve rate
  - Data quality impact: rule-scored vs LLM-scored trajectories
  - Scaffold comparison: which scaffold (openhands-sdk, claude-code, etc.) produces better training signal
  - Optimal dataset size vs training time tradeoff
```
