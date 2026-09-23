from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping: {path}")
    return value


def dump_yaml(value: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def write_json(value: Any, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_pipeline(root: str | Path | None = None) -> dict[str, Any]:
    base = Path(root).resolve() if root else project_root()
    return load_yaml(base / "config" / "pipeline.yaml")
