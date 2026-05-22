#!/usr/bin/env python3
"""Generate dashboard/status.mdx from current training state.

Can be run standalone or in a loop by train.sh.
Usage:
    python scripts/update_status.py --block-dir /path/to/block
    python scripts/update_status.py --block-dir /path/to/block --loop 30   # refresh every 30s
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml


def load_config(block_dir: Path) -> dict:
    with open(block_dir / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["runtime_info"]["input"]


def read_last_log_entry(log_path: Path) -> dict | None:
    if not log_path.exists():
        return None
    last = None
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    pass
    return last


def read_train_results(output_dir: Path) -> dict | None:
    p = output_dir / "train_results.json"
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def resolve_output_dir(block_dir: Path, output_dir: str) -> Path:
    path = Path(output_dir)
    if path.is_absolute():
        return path
    return block_dir / "artifacts" / "model" / path.name


def atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def fmt_seconds(s):
    if s is None:
        return "-"
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m}m {sec}s"


def generate_status(block_dir: Path) -> str:
    cfg = load_config(block_dir)
    data_name = cfg["conversion"]["data_name"]
    dataset_name = cfg.get("dataset", {}).get("name") or data_name
    model = Path(cfg["model"]["model_name_or_path"]).name
    output_dir_name = cfg["training"]["output_dir"]
    template = cfg["training"]["template"]
    lr = cfg["training"]["learning_rate"]
    epochs = cfg["training"]["num_train_epochs"]
    batch = cfg["training"]["per_device_train_batch_size"]
    accum = cfg["training"]["gradient_accumulation_steps"]
    n_gpus = cfg["infrastructure"]["n_gpus_per_node"]
    gbs = batch * accum * n_gpus

    output_dir = resolve_output_dir(block_dir, output_dir_name)
    trainer_log = output_dir / "trainer_log.jsonl"
    train_results = read_train_results(output_dir)
    last_entry = read_last_log_entry(trainer_log)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append("# 训练状态")
    lines.append("")
    lines.append(f"更新时间: {now}")
    lines.append("")

    # Status
    if train_results:
        status = "已完成"
    elif last_entry and last_entry.get("percentage", 0) > 0:
        status = "训练中"
    else:
        status = "未开始 / 等待中"

    lines.append(f"状态: **{status}**")
    lines.append("")

    # Config summary
    lines.append("## 配置摘要")
    lines.append("")
    lines.append(f"| 项目 | 值 |")
    lines.append(f"|---|---|")
    lines.append(f"| 基座模型 | {model} |")
    lines.append(f"| 数据集 | {dataset_name} |")
    if dataset_name != data_name:
        lines.append(f"| 数据文件名 | {data_name} |")
    lines.append(f"| 模板 | {template} |")
    lines.append(f"| 学习率 | {lr} |")
    lines.append(f"| 训练轮数 | {epochs} |")
    lines.append(f"| 全局 batch size | {gbs} (per_device={batch} x accum={accum} x gpu={n_gpus}) |")
    lines.append(f"| 输出目录 | {output_dir_name} |")
    lines.append("")

    # Progress
    if last_entry:
        lines.append("## 训练进度")
        lines.append("")
        cur = last_entry.get("current_steps", "-")
        total = last_entry.get("total_steps", "-")
        pct = last_entry.get("percentage", "-")
        epoch = last_entry.get("epoch", "-")
        elapsed = last_entry.get("elapsed_time", "-")
        remaining = last_entry.get("remaining_time", "-")
        loss = last_entry.get("loss", "-")
        cur_lr = last_entry.get("lr", "-")

        lines.append(f"| 指标 | 值 |")
        lines.append(f"|---|---|")
        lines.append(f"| 步数 | {cur} / {total} |")
        lines.append(f"| 进度 | {pct}% |")
        lines.append(f"| Epoch | {epoch} |")
        lines.append(f"| Loss | {loss} |")
        lines.append(f"| 学习率 | {cur_lr} |")
        lines.append(f"| 已用时间 | {elapsed} |")
        lines.append(f"| 预计剩余 | {remaining} |")
        lines.append("")

    # Final results
    if train_results:
        lines.append("## 最终结果")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|---|---|")
        lines.append(f"| 最终 loss | {train_results.get('train_loss', '-')} |")
        lines.append(f"| 总训练时间 | {fmt_seconds(train_results.get('train_runtime'))} |")
        lines.append(f"| 样本吞吐 | {train_results.get('train_samples_per_second', '-')} samples/s |")
        lines.append(f"| 步数吞吐 | {train_results.get('train_steps_per_second', '-')} steps/s |")
        lines.append(f"| 总 epoch | {train_results.get('epoch', '-')} |")
        lines.append("")

    # Loss plot
    loss_plot = output_dir / "training_loss.png"
    if loss_plot.exists():
        lines.append("## Loss 曲线")
        lines.append("")
        try:
            rel = loss_plot.relative_to(block_dir)
            plot_path = f"../{rel}"
        except ValueError:
            plot_path = str(loss_plot)
        lines.append(f"![training_loss]({plot_path})")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--block-dir", required=True)
    parser.add_argument("--loop", type=int, default=0,
                        help="Refresh interval in seconds (0 = run once)")
    args = parser.parse_args()

    block = Path(args.block_dir)
    status_path = block / "dashboard" / "status.mdx"

    while True:
        try:
            content = generate_status(block)
            atomic_write_text(status_path, content)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Updated {status_path}")
        except Exception as e:
            print(f"WARNING: status update failed: {e}", file=sys.stderr)

        if args.loop <= 0:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
