#!/usr/bin/env python3
"""
从 HuggingFace 参考数据集中提取所有 repo 名称，保存到文本文件。

输出文件每行一个 ``owner/repo``，可直接传给各转换脚本的 ``--exclude-repos-file`` 参数。

用法：
    cd /path/to/LegoFactory/subblock/trainer
    artifacts/env/lf/bin/python scripts/generate_excluded_repos.py
    artifacts/env/lf/bin/python scripts/generate_excluded_repos.py -o /tmp/excluded.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BLOCK_DIR = Path(__file__).resolve().parent.parent
LOCAL_SRC = BLOCK_DIR / "repos" / "swe_data_process" / "src"
if LOCAL_SRC.exists():
    sys.path.insert(0, str(LOCAL_SRC))

from swe_data_process.utils import (
    DEFAULT_REFERENCE_DATASETS,
    load_reference_repos_from_hf,
)

DEFAULT_OUTPUT = BLOCK_DIR / "artifacts" / "data" / "excluded_repos.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 HuggingFace 参考数据集提取需要排除的 repo 列表"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"输出文件路径，默认 {DEFAULT_OUTPUT}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("正在从 HuggingFace 加载参考数据集…")
    repos = load_reference_repos_from_hf(DEFAULT_REFERENCE_DATASETS)

    if not repos:
        print("WARNING: 未能获取任何 repo，请检查网络连接或数据集名称。")
        return

    sorted_repos = sorted(repos)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        f.write(
            "# 参考数据集 repo 列表（自动生成，请勿手动编辑）\n"
            "# 来源: SWE-bench_Verified, SWE-bench_Pro, SWE-bench_Multilingual\n"
            "# 每行一个 owner/repo，在转换脚本中传入 --exclude-repos-file 使用\n"
        )
        for repo in sorted_repos:
            f.write(repo + "\n")

    print(f"\n已保存 {len(sorted_repos)} 个 repo 到: {args.output}")


if __name__ == "__main__":
    main()
