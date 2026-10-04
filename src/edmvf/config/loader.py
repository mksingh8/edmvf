from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import Scenario
from .validator import validate_scenario


def load_config(path: str | Path) -> Scenario:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        payload: Any = yaml.safe_load(handle)

    if payload is None:
        raise ValueError("configuration file is empty")
    if not isinstance(payload, dict):
        raise ValueError("configuration file must contain a dictionary at the root level")

    scenario = Scenario.from_mapping(payload)
    validate_scenario(scenario)
    return scenario
