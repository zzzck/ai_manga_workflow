from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .schemas import PipelineConfig, ProjectBrief


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a mapping in {path}")
    return data


def load_project(path: Path) -> ProjectBrief:
    return ProjectBrief.model_validate(load_yaml(path))


def load_config(path: Path) -> PipelineConfig:
    return PipelineConfig.model_validate(load_yaml(path))


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=True)
