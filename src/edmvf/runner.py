from __future__ import annotations

from .config.models import Scenario
from .config.validator import IMPLEMENTED_VALIDATION_CHECKS, UNIMPLEMENTED_VALIDATION_CHECKS, validate_scenario


def run_scenario(scenario: Scenario) -> dict:
    if not isinstance(scenario, Scenario):
        raise TypeError("scenario must be a Scenario instance")

    validate_scenario(scenario)

    payload = {
        "scenario_name": scenario.name,
        "source_type": scenario.source.type,
        "target_type": scenario.target.type,
        "status": "ready",
        "matching_keys": list(scenario.matching.keys),
        "validation_checks": list(scenario.validation.checks),
    }

    selected_checks = list(scenario.validation.checks)
    implemented_checks = [check for check in selected_checks if check in IMPLEMENTED_VALIDATION_CHECKS]
    unimplemented_checks = [check for check in selected_checks if check in UNIMPLEMENTED_VALIDATION_CHECKS]

    if implemented_checks:
        from .validation.schema import validate_csv_scenario

        schema_results = validate_csv_scenario(scenario, implemented_checks)
        for name, result in schema_results.items():
            payload[name] = result.to_dict()

        payload["status"] = "ready" if all(result.passed for result in schema_results.values()) else "failed"

    for check in unimplemented_checks:
        payload[check] = {
            "status": "not_implemented",
            "disposition": "not_implemented",
            "message": f"Validation check '{check}' is configured but not implemented in the current FR-004 scope.",
        }

    if unimplemented_checks:
        payload["status"] = "not_implemented"

    return payload
