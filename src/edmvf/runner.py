from __future__ import annotations

from .config.models import Scenario


def run_scenario(scenario: Scenario) -> dict:
    if not isinstance(scenario, Scenario):
        raise TypeError("scenario must be a Scenario instance")

    return {
        "scenario_name": scenario.name,
        "source_type": scenario.source.type,
        "target_type": scenario.target.type,
        "status": "ready",
        "matching_keys": list(scenario.matching.keys),
        "validation_checks": list(scenario.validation.checks),
    }
