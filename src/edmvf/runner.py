from __future__ import annotations

from .config.models import Scenario


def run_scenario(scenario: Scenario) -> dict:
    if not isinstance(scenario, Scenario):
        raise TypeError("scenario must be a Scenario instance")

    payload = {
        "scenario_name": scenario.name,
        "source_type": scenario.source.type,
        "target_type": scenario.target.type,
        "status": "ready",
        "matching_keys": list(scenario.matching.keys),
        "validation_checks": list(scenario.validation.checks),
    }

    selected_checks = set(scenario.validation.checks)
    fr004_checks = {"schema_validation", "structural_validation"}
    if selected_checks.intersection(fr004_checks):
        from .validation.schema import validate_csv_scenario

        schema_results = validate_csv_scenario(scenario, list(scenario.validation.checks))
        for name, result in schema_results.items():
            payload[name] = result.to_dict()

        payload["status"] = "ready" if all(result.passed for result in schema_results.values()) else "failed"

    return payload
