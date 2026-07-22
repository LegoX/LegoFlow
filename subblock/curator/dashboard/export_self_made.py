#!/usr/bin/env python3
"""导出自造数据集(Self-Made)为统一 JSONL 格式，供 tag_task_metadata.py 用同一套 LLM 打标。

Self-Made = 以下两个 HuggingFace 数据集的并集：
  1. SWE-Lego/swegen-selfmade-260301-260721-non-top5k
  2. SWE-Lego/swegen-selfmade-260301-260622-top5k

直接从两个数据集的 tasks.tar.gz 读取每个任务的 instruction.md (problem statement)
和 solution/fix.patch (patch)，保证导出内容精确等于这两块数据。
"""
import json
import os
import tarfile
from collections import Counter
from pathlib import Path

DASHBOARD_ROOT = Path(__file__).parent
DATASETS_DIR = DASHBOARD_ROOT / "datasets"
OUTPUT_DIR = DATASETS_DIR / "self_made"

HOME_DIR = Path(os.environ.get("SWEGEN_HOME", str(Path.home())))
REPO_ROOT = Path(os.environ.get("SWEGEN_DATA_ROOT", str(HOME_DIR / "SWE-gen")))
EXPORTS_ROOT = Path(os.environ.get("SWEGEN_EXPORTS_ROOT", str(REPO_ROOT / "exports_hf")))

# 组成 self_made 的两个数据集 tarball
SOURCE_TARBALLS = [
    EXPORTS_ROOT / "swegen-selfmade-260301-260721-non-top5k" / "tasks.tar.gz",
    EXPORTS_ROOT / "swegen-selfmade-260301-260622-top5k" / "tasks.tar.gz",
]

# 代码文件扩展名 -> 语言(用于按 patch 推断语言，仅用于 dashboard 分组展示)
EXT_TO_LANG = {
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hh": "cpp", ".hpp": "cpp", ".hxx": "cpp",
    ".go": "go",
    ".java": "java",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".py": "python", ".pyi": "python",
    ".rs": "rust",
    ".ts": "typescript", ".tsx": "typescript", ".cts": "typescript", ".mts": "typescript",
}


def infer_language_from_patch(patch: str) -> str:
    """从 fix.patch 的 diff 文件扩展名推断主要语言。"""
    counts: Counter = Counter()
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3]
                if path.startswith("b/"):
                    path = path[2:]
                ext = os.path.splitext(path)[1].lower()
                lang = EXT_TO_LANG.get(ext)
                if lang:
                    counts[lang] += 1
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def export():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_jsonl = OUTPUT_DIR / "tasks.jsonl"

    print(f"{'='*80}")
    print(f"导出自造数据集 (Self-Made)")
    for tb in SOURCE_TARBALLS:
        print(f"  源: {tb}  {'✓' if tb.exists() else '✗ 缺失'}")
    print(f"输出: {output_jsonl}")
    print(f"{'='*80}")

    count = 0
    skipped = 0
    seen_ids: set[str] = set()
    lang_counts: Counter = Counter()

    with output_jsonl.open("w", encoding="utf-8") as out:
        for tarball in SOURCE_TARBALLS:
            if not tarball.exists():
                print(f"WARN: 跳过缺失的 tarball: {tarball}")
                continue

            print(f"\n处理 {tarball.parent.name} ...")
            src_count = 0

            with tarfile.open(tarball, "r:gz") as tar:
                # 按 task_id 组织：instruction.md + solution/fix.patch
                # tarball 内布局: tasks/<task_id>/instruction.md, tasks/<task_id>/solution/fix.patch
                pending: dict[str, dict] = {}

                for member in tar:
                    if not member.isfile():
                        continue
                    parts = member.name.split("/")
                    # tasks/<task_id>/...
                    if len(parts) < 3 or parts[0] != "tasks":
                        continue
                    task_id = parts[1]
                    rel = "/".join(parts[2:])

                    if rel == "instruction.md":
                        f = tar.extractfile(member)
                        if f:
                            pending.setdefault(task_id, {})["problem"] = f.read().decode("utf-8", errors="replace")
                    elif rel == "solution/fix.patch":
                        f = tar.extractfile(member)
                        if f:
                            pending.setdefault(task_id, {})["patch"] = f.read().decode("utf-8", errors="replace")

                for task_id, data in pending.items():
                    if task_id in seen_ids:
                        continue  # 两个数据集去重（理论上不重叠）
                    patch = data.get("patch", "")
                    if not patch:
                        skipped += 1
                        continue
                    problem = data.get("problem", "")
                    language = infer_language_from_patch(patch)
                    lang_counts[language or "unknown"] += 1

                    unified = {
                        "instance_id": task_id,
                        "problem_statement": problem,
                        "patch": patch,
                        "repo": task_id.split("__", 1)[0] if "__" in task_id else "",
                        "language": language,
                        "dataset_source": "self_made",
                    }
                    out.write(json.dumps(unified, ensure_ascii=False) + "\n")
                    seen_ids.add(task_id)
                    count += 1
                    src_count += 1

            print(f"  -> {src_count} 个任务")

    print(f"{'='*80}")
    print(f"✓ 导出完成: {count} 个任务 (跳过 {skipped} 个无 patch)")
    print(f"语言分布:")
    for lang, n in lang_counts.most_common():
        print(f"  {lang:12s}: {n}")
    print(f"{'='*80}")


if __name__ == "__main__":
    export()
