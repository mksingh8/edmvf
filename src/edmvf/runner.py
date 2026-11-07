from __future__ import annotations

from collections.abc import Iterable

from .config.models import Scenario
from .config.validator import IMPLEMENTED_VALIDATION_CHECKS, UNIMPLEMENTED_VALIDATION_CHECKS, validate_scenario

# Per-check status: passed | failed | not_implemented.
# Overall status:   failed      - at least one executed check failed;
#                   incomplete  - nothing failed, but at least one configured check
#                                 is not implemented (or nothing executed at all);
#                   passed      - every configured check executed and passed.
# `complete` is True only when every configured check actually executed.


KNOWN_CHECK_STATUSES = ("passed", "failed", "not_implemented")


def _overall(statuses: Iterable[str]) -> tuple[str, bool]:
    """Aggregate per-check statuses into (overall status, complete)."""
    statuses = list(statuses)
    unknown = [status for status in statuses if status not in KNOWN_CHECK_STATUSES]
    if unknown:
        # Never let a status this function does not understand count as a pass.
        # repr() keeps the message safe for unorderable or unhashable values.
        raise ValueError(f"unknown check status: {sorted({repr(status) for status in unknown})}")
    complete = bool(statuses) and all(status in ("passed", "failed") for status in statuses)
    if "failed" in statuses:
        return "failed", complete
    if not statuses or "not_implemented" in statuses:
        return "incomplete", False
    return "passed", True


def run_scenario(scenario: Scenario) -> dict:
    if not isinstance(scenario, Scenario):
        raise TypeError("scenario must be a Scenario instance")

    validate_scenario(scenario)

    selected_checks = list(scenario.validation.checks)
    implemented_checks = [check for check in selected_checks if check in IMPLEMENTED_VALIDATION_CHECKS]
    unimplemented_checks = [check for check in selected_checks if check in UNIMPLEMENTED_VALIDATION_CHECKS]

    entries: dict[str, dict] = {}
    statuses: list[str] = []

    if implemented_checks:
        from .validation.schema import validate_csv_scenario

        for name, result in validate_csv_scenario(scenario, implemented_checks).items():
            status = "passed" if result.passed else "failed"
            entries[name] = {"status": status, **result.to_dict()}
            statuses.append(status)

    for check in unimplemented_checks:
        entries[check] = {
            "status": "not_implemented",
            "message": f"Validation check '{check}' is configured but not implemented in this version.",
        }
        statuses.append("not_implemented")

    status, complete = _overall(statuses)

    return {
        "scenario_name": scenario.name,
        "source_type": scenario.source.type,
        "target_type": scenario.target.type,
        "status": status,
        "complete": complete,
        "matching_keys": list(scenario.matching.keys),
        "validation_checks": selected_checks,
        **entries,
    }
