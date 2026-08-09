#!/usr/bin/env python3
"""Canonical registry for every dataset shown on the Curator dashboard."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DatasetSpec:
    id: str
    display_name: str
    source: str
    description: str
    exporter: str
    metadata_strategy: str


DATASETS = (
    DatasetSpec(
        id="self_made",
        display_name="LegoFlow Curator Instances",
        source="LegoFlow-SWE-Curator task directories or task tarballs",
        description="Instances created by LegoFlow Curator",
        exporter="export_self_made.py",
        metadata_strategy="task_toml",
    ),
    DatasetSpec(
        id="swe_rebench",
        display_name="SWE-rebench",
        source="nebius/SWE-rebench",
        description="Open-source dataset nebius/SWE-rebench",
        exporter="export_swe_rebench.py",
        metadata_strategy="canonical_tagger",
    ),
    DatasetSpec(
        id="swe_rebench_v2",
        display_name="SWE-rebench-V2",
        source="nebius/SWE-rebench-V2",
        description="Open-source dataset nebius/SWE-rebench-V2",
        exporter="export_swe_rebench_v2.py",
        metadata_strategy="canonical_tagger",
    ),
    DatasetSpec(
        id="openswe_filtered",
        display_name="OpenSWE-filtered",
        source="SWE-Lego/openswe_filtered_for_rl",
        description="Open-source dataset SWE-Lego/openswe_filtered_for_rl",
        exporter="export_openswe_filtered.py",
        metadata_strategy="canonical_tagger",
    ),
    DatasetSpec(
        id="scale_swe",
        display_name="Scale-SWE",
        source="AweAI-Team/Scale-SWE",
        description="Open-source dataset AweAI-Team/Scale-SWE",
        exporter="export_scale_swe.py",
        metadata_strategy="canonical_tagger",
    ),
)

DATASETS_BY_ID = {dataset.id: dataset for dataset in DATASETS}
DATASET_IDS = tuple(DATASETS_BY_ID)


def registry_tsv() -> str:
    """Return shell-friendly registry rows without duplicating them in Bash."""
    return "\n".join(
        f"{dataset.id}\t{dataset.exporter}\t{dataset.metadata_strategy}"
        for dataset in DATASETS
    )


if __name__ == "__main__":
    print(registry_tsv())
