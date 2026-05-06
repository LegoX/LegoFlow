# Training Decisions

## 2026-04: Use port 8001 for LiteLLM (not 8002 as in rllm-harbor)
**Decision:** This repo standardizes on LiteLLM port 8001.
**Why:** Avoids port conflict when both repos' processes may run on the same node during
testing. Scripts in this repo are updated consistently; do not mix configs across repos.

## 2026-04: Keep val_before_train=True always
**Decision:** Never set `val_before_train=False` even for quick debugging.
**Why:** The validation step also initializes vLLM SPMD workers. Skipping it causes
`ValueError: Pointer argument (cpu tensor?)` on the first training step. Use a
tiny 2-row val parquet for fast validation instead.

## 2026-04: raise_on_error=False in AgentWorkflowEngine
**Decision:** Set `raise_on_error=False` so a permanently failing task is dropped
rather than crashing the entire training run.
**Why:** SWE-bench tasks can fail for transient K8s/network reasons unrelated to the
model. Crashing the run loses all accumulated steps. Dropping and continuing is safer.

## 2026-03: Binary reward (0/1) from SWE-bench grader
**Decision:** Use the official SWE-bench grader binary signal as the sole reward.
**Why:** Partial-credit rewards require additional engineering and may introduce noise.
The binary signal is clean and auditable. Revisit if training signal is too sparse.

## 2026-03: 2-node TP=16 as the primary setup
**Decision:** Run with 2 nodes (TP=16) as the default for production experiments.
**Why:** 1-node TP=8 is too slow for the 877-instance training set at 64 batch size.
The 2-node setup doubles rollout throughput while keeping the model on a single tensor
parallel group (DP=1).

## 2026-03: sc4to7 difficulty tier for training data
**Decision:** Train on 877 instances from difficulty tiers sc4–sc7 only.
**Why:** Easier tiers (sc1–sc3) have near-100% base-model solve rates and provide no
gradient signal. Hard tiers provide a meaningful learning target.

## 2026-04: WandB run resume (run_id: 3qi5pkgu)
**Decision:** Resume the existing WandB run across experiment iterations rather than
starting a new one.
**Why:** Keeps all reward curves on a single plot for easier comparison. Use
`WANDB_RESUME=allow` + `WANDB_RUN_ID=3qi5pkgu` in the training script.
