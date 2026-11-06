from __future__ import annotations

from typing import Any

from .models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig

SUPPORTED_SOURCE_TYPES = {"csv"}
IMPLEMENTED_VALIDATION_CHECKS = {
    "schema_validation",
    "structural_validation",
}
UNIMPLEMENTED_VALIDATION_CHECKS = {
    "missing_records",
    "duplicate_records",
    "mismatched_values",
}
SUPPORTED_VALIDATION_CHECKS = IMPLEMENTED_VALIDATION_CHECKS | UNIMPLEMENTED_VALIDATION_CHECKS


def _type_name(value: Any) -> str:
    return type(value).__name__


def _require_instance(value: Any, cls: type, field: str) -> None:
    if value is None:
        raise ValueError(f"{field} is required")
    if not isinstance(value, cls):
        article = "an" if cls.__name__[0] in "AEIOU" else "a"
        raise ValueError(f"{field} must be {article} {cls.__name__}, got {_type_name(value)}")


def _require_text(value: Any, field: str, blank_message: str | None = None) -> None:
    if value is None:
        raise ValueError(f"{field} is required")
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {_type_name(value)}")
    if not value.strip():
        raise ValueError(blank_message or f"{field} is required")


def _require_text_list(value: Any, field: str, empty_message: str) -> None:
    if value is None:
        raise ValueError(f"{field} is required")
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list, got {_type_name(value)}")
    if not value:
        raise ValueError(empty_message)
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if item is None:
            raise ValueError(f"{item_field} must be a string, got NoneType")
        _require_text(item, item_field, blank_message=f"{item_field} must not be blank")


def _check_version(version: Any) -> None:
    if version is None:
        return  # version stays optional
    if isinstance(version, bool) or not isinstance(version, (int, str)):
        raise ValueError(f"scenario.version must be an integer or a string, got {_type_name(version)}")
    if isinstance(version, str) and not version.strip():
        raise ValueError("scenario.version must not be blank")


def _check_data_source(source: Any, role: str) -> None:
    _require_instance(source, DataSource, role)
    _require_text(source.type, f"{role}.type")
    if source.type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"unsupported {role} type: {source.type}")
    _require_text(source.path, f"{role}.path")


def validate_scenario(scenario: Scenario) -> Scenario:
    """Enforce the single configuration contract (types, shapes, allowed values).

    Raises TypeError for a non-Scenario argument and ValueError for every
    configuration violation, before any data is opened.
    """
    if not isinstance(scenario, Scenario):
        raise TypeError("scenario must be a Scenario instance")

    _require_text(scenario.name, "scenario.name")
    _check_version(scenario.version)

    _check_data_source(scenario.source, "source")
    _check_data_source(scenario.target, "target")

    _require_instance(scenario.matching, MatchingConfig, "matching")
    _require_text_list(
        scenario.matching.keys, "matching.keys", "matching.keys must contain at least one key field"
    )

    _require_instance(scenario.validation, ValidationConfig, "validation")
    _require_text_list(
        scenario.validation.checks,
        "validation.checks",
        "validation.checks must contain at least one validation check",
    )
    unsupported_checks = [check for check in scenario.validation.checks if check not in SUPPORTED_VALIDATION_CHECKS]
    if unsupported_checks:
        raise ValueError(f"unsupported validation checks: {unsupported_checks}")

    _require_instance(scenario.output, OutputConfig, "output")
    _require_text(scenario.output.directory, "output.directory")

    return scenario
