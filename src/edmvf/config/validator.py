from __future__ import annotations

from .models import Scenario

SUPPORTED_SOURCE_TYPES = {"csv"}
SUPPORTED_VALIDATION_CHECKS = {
    "missing_records",
    "duplicate_records",
    "mismatched_values",
}


def validate_scenario(scenario: Scenario) -> Scenario:
    if not isinstance(scenario, Scenario):
        raise TypeError("scenario must be a Scenario instance")

    if not scenario.name or not str(scenario.name).strip():
        raise ValueError("scenario.name is required")

    if not scenario.source or not scenario.target:
        raise ValueError("source and target must be defined")

    if scenario.source.type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"unsupported source type: {scenario.source.type}")
    if scenario.target.type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"unsupported target type: {scenario.target.type}")

    if not scenario.matching.keys:
        raise ValueError("matching.keys must contain at least one key field")

    if not scenario.validation.checks:
        raise ValueError("validation.checks must contain at least one validation check")

    unsupported_checks = [check for check in scenario.validation.checks if check not in SUPPORTED_VALIDATION_CHECKS]
    if unsupported_checks:
        raise ValueError(f"unsupported validation checks: {unsupported_checks}")

    return scenario
