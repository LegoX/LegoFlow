# inputs.yaml 参数说明

运行任何脚本前都需要先编辑此文件。路径默认相对于 block 根目录，也可以写绝对路径。
编辑完成后建议先跑 `bash scripts/dryrun.sh` 验证配置。

---

## source — 原始轨迹数据来源

| 参数 | 类型 | 说明 |
|---|---|---|
| `provider` | 字符串 | 数据提供者格式。`jierun` 或 `chaofan`，决定后续使用哪个转换脚本 |
| `scaffold` | 字符串 | agent 脚手架类型。可选：`openhands-sdk`、`claude-code`、`open-code`、`terminus2`、`openhands`（仅 chaofan） |
| `job_dir` | 路径 | jierun 格式专用。harbor job 目录的完整路径，传给转换脚本的 `--job-dir` |
| `trajs_dir` | 路径 | jierun 格式下 cc/oc/openhands-sdk 的轨迹日志目录。留空则自动使用 `{job_dir}_trajs_via_logger` |
| `source_dir` | 路径 | chaofan 格式专用。completions 源目录路径，传给转换脚本的 `--source-dir` |

provider + scaffold 的组合决定了调用哪个转换脚本，具体映射见 `train.sh` 中的 `case` 分支。

---

## conversion — 数据转换

| 参数 | 类型 | 说明 |
|---|---|---|
| `max_instances` | 整数 | 最大转换实例数。0 或负数表示不限制 |
| `exclude_repos_file` | 路径 | repo 排除列表文件，用于过滤 SWE-bench Verified/Pro/Multilingual 中的评测 repo。设为空字符串则不过滤 |
| `data_name` | 字符串 | 数据集命名，同时决定输出路径：IM 输出 → `artifacts/data/im_data/{data_name}.jsonl`，LF 输出 → `artifacts/data/lf_data/{data_name}.json` |

---

## dataset — LLaMA-Factory 数据集注册

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | 字符串 | 在 `dataset_info.json` 中注册的 key。留空则自动从 `data_name` 派生（推荐） |

---

## model — 模型配置

| 参数 | 类型 | 说明 |
|---|---|---|
| `model_name_or_path` | 路径 | 基座模型路径或 HuggingFace 模型名 |
| `trust_remote_code` | 布尔 | 是否信任模型仓库中的自定义代码 |

---

## training — 训练超参数

### 训练方法

| 参数 | 类型 | 说明 |
|---|---|---|
| `stage` | 字符串 | 训练阶段，固定为 `sft` |
| `finetuning_type` | 字符串 | 微调类型，`full` 为全参数微调 |
| `deepspeed` | 路径 | DeepSpeed 配置文件路径。可选 ds_z0/z2/z3/z2_offload/z3_offload |

### 数据处理

| 参数 | 类型 | 说明 |
|---|---|---|
| `template` | 字符串 | 对话模板 |
| `cutoff_len` | 整数 | 序列最大长度（token 数） |
| `rope_scaling` | 字符串 | RoPE 缩放方式。`cutoff_len` 超过 32768 时必须设为 `yarn` |
| `max_samples` | 整数 | 最大训练样本数，设大数即为不限制 |
| `preprocessing_num_workers` | 整数 | 数据预处理并行 worker 数 |
| `dataloader_num_workers` | 整数 | DataLoader 并行 worker 数 |

### 输出控制

| 参数 | 类型 | 说明 |
|---|---|---|
| `output_dir` | 字符串 | 输出目录名，实际路径为 `artifacts/model/{output_dir}`。命名规范：`{模型}_{数据名}_{超参}_{think模式}` |
| `logging_steps` | 整数 | 每隔多少步记录一次日志 |
| `save_strategy` | 字符串 | 保存策略：`steps`（按步数）或 `epoch`（按轮次） |
| `save_steps` | 整数 | `save_strategy=steps` 时每隔多少步保存一次 checkpoint |
| `overwrite_output_dir` | 布尔 | 是否覆盖已有的输出目录 |
| `save_only_model` | 布尔 | `true` 只保存模型权重；`false` 同时保存优化器状态（支持断点续训） |
| `resume_from_checkpoint` | 路径/null | 断点续训的 checkpoint 目录路径，`null` 表示从头训练 |

### 训练参数

| 参数 | 类型 | 说明 |
|---|---|---|
| `per_device_train_batch_size` | 整数 | 每张 GPU 的 batch size |
| `gradient_accumulation_steps` | 整数 | 梯度累积步数。全局 batch = per_device × accum × GPU 数 |
| `learning_rate` | 浮点 | 学习率 |
| `weight_decay` | 浮点 | 权重衰减 |
| `max_grad_norm` | 浮点 | 梯度裁剪阈值 |
| `num_train_epochs` | 浮点 | 训练轮数 |
| `lr_scheduler_type` | 字符串 | 学习率调度器类型，如 `cosine` |
| `warmup_ratio` | 浮点 | 预热比例（占总步数的比例） |
| `bf16` | 布尔 | 是否使用 bfloat16 混合精度 |
| `ddp_timeout` | 整数 | DDP 超时时间（毫秒），长序列训练需要设大 |
| `enable_liger_kernel` | 布尔 | 是否启用 Liger Kernel（高效 Triton 算子） |
| `use_unsloth_gc` | 布尔 | 是否启用 Unsloth 梯度检查点（省显存） |
| `flash_attn` | 字符串 | Flash Attention 模式：`fa2`（启用）、`disabled`（禁用）、`auto` |

---

## infrastructure — 基础设施

| 参数 | 类型 | 说明 |
|---|---|---|
| `n_gpus_per_node` | 整数 | 当前计算节点的 GPU 数量 |

---

## experiment — 实验追踪

| 参数 | 类型 | 说明 |
|---|---|---|
| `run_name` | 字符串 | WandB run 名称。留空则自动从 `output_dir` 的 basename 派生（推荐） |
| `wandb_mode` | 字符串 | WandB 模式：`online`（上传）、`offline`（本地记录）、`disabled`（关闭） |
| `wandb_run_id` | 字符串 | 用于恢复已有 WandB run，留空则新建 |

---

## credentials — 凭证

| 参数 | 类型 | 说明 |
|---|---|---|
| `wandb_api_key` | 字符串 | WandB API Key。`wandb_mode=online` 时必填，`offline` 时可选，`disabled` 时忽略 |
