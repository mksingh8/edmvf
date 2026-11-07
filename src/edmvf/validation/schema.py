from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

from edmvf.config.models import Scenario
from edmvf.config.validator import IMPLEMENTED_VALIDATION_CHECKS, validate_scenario
from edmvf.data.csv_dataset import CsvDataset, CsvFileAccessError, CsvParseError, read_csv_dataset
from edmvf.validation.result import SchemaValidationResult, ValidationIssue


def _issue(
    kind: str,
    dataset: str,
    message: str,
    row_number: int | None = None,
    column_name: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        kind=kind,
        dataset=dataset,
        message=message,
        row_number=row_number,
        column_name=column_name,
        start_line=start_line,
        end_line=end_line,
    )


# Accepted types for the `checks` argument of the Python validators: list, tuple,
# set or frozenset of check names. Any other type (for example a bare string,
# bytes, a dict, a generator or a range) is outside the supported types and is
# rejected. `None` means each function's documented default. The same rules apply
# to every accepted type. A set has no order, so its names are sorted to keep
# error messages deterministic; a list or tuple keeps the order given. Duplicates
# can only occur in a list or tuple (a set or frozenset has already removed them).
# YAML configuration and Scenario.validation.checks must be a list.
CheckSelection = list[str] | tuple[str, ...] | set[str] | frozenset[str]
SELECTION_TYPES = (list, tuple, set, frozenset)

DATASET_CHECKS = ("schema_validation", "structural_validation")


def _select_checks(checks: CheckSelection | None, default: list[str], allowed: Iterable[str], function: str) -> list[str]:
    """Resolve a check selection for a lower-level validator.

    `None` means the documented default of `function`. An explicit selection is
    never replaced by a default: an empty, unknown, unsupported or duplicated
    selection raises ValueError before any file is read.
    """
    if checks is None:
        return list(default)
    if not isinstance(checks, SELECTION_TYPES):
        raise ValueError(
            f"{function}: checks must be a list, tuple, set or frozenset of check names, got {type(checks).__name__}"
        )
    is_unordered = isinstance(checks, (set, frozenset))
    selected = list(checks)
    if not selected:
        raise ValueError(f"{function}: checks must contain at least one check")
    # Type-check every element before sorting or printing anything, so a set is
    # rejected exactly like a list (never via repr() or a sort comparison).
    non_strings = [item for item in selected if not isinstance(item, str)]
    if non_strings:
        # A set has no order: name the alphabetically first offending type.
        type_name = min(type(item).__name__ for item in non_strings) if is_unordered else type(non_strings[0]).__name__
        raise ValueError(f"{function}: checks must be strings, got {type_name}")
    if is_unordered:
        # Sets are unordered; sort so messages do not depend on hash randomization.
        selected.sort()
    allowed_names = set(allowed)
    unsupported = [item for item in selected if item not in allowed_names]
    if unsupported:
        raise ValueError(f"{function}: unsupported checks {unsupported}; supported: {sorted(allowed_names)}")
    if len(set(selected)) != len(selected):
        duplicates = [item for item in dict.fromkeys(selected) if selected.count(item) > 1]
        raise ValueError(f"{function}: checks contain duplicate entries: {duplicates}")
    return selected


def _load_dataset(path: str | Path, dataset_name: str) -> tuple[CsvDataset | None, list[ValidationIssue]]:
    """Read one CSV once. Data problems become issues attributed to `dataset_name`.

    Only file-access and parse failures are data problems; any other exception is
    a framework error and propagates (NFR-009).
    """
    try:
        return read_csv_dataset(path), []
    except (FileNotFoundError, CsvFileAccessError) as exc:
        return None, [_issue("file_access", dataset_name, str(exc))]
    except CsvParseError as exc:
        return None, [
            _issue(
                "parse_error",
                dataset_name,
                str(exc),
                row_number=exc.row_number,
                start_line=exc.start_line,
                end_line=exc.end_line,
            )
        ]


def _header_integrity_issues(dataset: CsvDataset, dataset_name: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not dataset.headers:
        # A truly empty file has no record to point at. A blank first record
        # (headers == [] but line_spans present) is located at record 1.
        if dataset.line_spans:
            start_line, end_line = dataset.line_spans[0]
            issues.append(
                _issue(
                    "structural",
                    dataset_name,
                    "CSV is empty or missing a header row.",
                    row_number=1,
                    start_line=start_line,
                    end_line=end_line,
                )
            )
        else:
            issues.append(_issue("structural", dataset_name, "CSV is empty or missing a header row."))
        return issues

    header_start, header_end = dataset.line_spans[0] if dataset.line_spans else (None, None)

    blank_headers = [index for index, header in enumerate(dataset.headers) if header is None or str(header).strip() == ""]
    if blank_headers:
        issues.append(
            _issue(
                "schema",
                dataset_name,
                "CSV header contains blank column names.",
                row_number=1,
                column_name=dataset.headers[blank_headers[0]],
                start_line=header_start,
                end_line=header_end,
            )
        )

    duplicates = [name for name, count in Counter(header for header in dataset.headers if header is not None).items() if count > 1]
    if duplicates:
        issues.append(
            _issue(
                "schema",
                dataset_name,
                f"CSV header contains duplicate column names: {duplicates}.",
                row_number=1,
                column_name=duplicates[0],
                start_line=header_start,
                end_line=header_end,
            )
        )

    return issues


def _width_issues(dataset: CsvDataset, dataset_name: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    header_length = len(dataset.headers)
    # row_number is the logical record ordinal: header = 1, first data record = 2.
    for row_number, row in enumerate(dataset.rows, start=2):
        if len(row) != header_length:
            start_line, end_line = dataset.line_spans[row_number - 1] if dataset.line_spans else (None, None)
            issues.append(
                _issue(
                    "structural",
                    dataset_name,
                    f"Row {row_number} has inconsistent width: {len(row)} values for {header_length} columns.",
                    row_number=row_number,
                    start_line=start_line,
                    end_line=end_line,
                )
            )
    return issues


def _dataset_issues(dataset: CsvDataset, dataset_name: str, selected_checks: set[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if "schema_validation" in selected_checks or "structural_validation" in selected_checks:
        issues.extend(_header_integrity_issues(dataset, dataset_name))
    if "structural_validation" in selected_checks:
        issues.extend(_width_issues(dataset, dataset_name))
    return issues


def _read_side(
    path: str | Path, dataset_name: str, selections: dict[str, set[str]]
) -> tuple[list[str] | None, dict[str, list[ValidationIssue]]]:
    """Read one input once and run every requested per-dataset check on that read.

    `selections` maps a result name to the set of checks it needs. Returns the
    header row (None if the input could not be loaded) and the issues per result
    name. The rows are not returned, so they are released as soon as this returns.
    """
    dataset, load_issues = _load_dataset(path, dataset_name)
    if dataset is None:
        return None, {name: list(load_issues) for name in selections}
    issues = {name: _dataset_issues(dataset, dataset_name, checks) for name, checks in selections.items()}
    return list(dataset.headers), issues


def _schema_result(
    source_path: str | Path,
    target_path: str | Path,
    source_headers: list[str] | None,
    target_headers: list[str] | None,
    source_issues: list[ValidationIssue],
    target_issues: list[ValidationIssue],
) -> SchemaValidationResult:
    """Assemble the source/target comparison from what was read on each side."""
    result = SchemaValidationResult(dataset="schema_comparison", passed=True, source_path=str(source_path), target_path=str(target_path))
    result.issues.extend(source_issues)
    result.issues.extend(target_issues)

    # A column comparison is meaningless against an unreadable or ambiguous header.
    if result.issues:
        result.passed = False
        return result

    missing_columns = sorted(set(source_headers) - set(target_headers))
    unexpected_columns = sorted(set(target_headers) - set(source_headers))

    # Column differences are findings about the target (the dataset under test).
    if missing_columns:
        result.issues.append(_issue("schema", "target", f"Missing columns in target: {missing_columns}."))
    if unexpected_columns:
        result.issues.append(_issue("schema", "target", f"Unexpected columns in target: {unexpected_columns}."))

    if result.issues:
        result.passed = False

    return result


def validate_csv_dataset(csv_path: str | Path, dataset_name: str, checks: CheckSelection | None = None) -> SchemaValidationResult:
    """Validate one CSV. `checks=None` runs the default: header integrity and row widths."""
    selected_checks = set(_select_checks(checks, list(DATASET_CHECKS), DATASET_CHECKS, "validate_csv_dataset"))
    result = SchemaValidationResult(dataset=dataset_name, passed=True, source_path=str(csv_path))

    dataset, issues = _load_dataset(csv_path, dataset_name)
    result.issues.extend(issues)
    if dataset is not None:
        result.issues.extend(_dataset_issues(dataset, dataset_name, selected_checks))

    if result.issues:
        result.passed = False

    return result


def validate_csv_schema(source_path: str | Path, target_path: str | Path, checks: CheckSelection | None = None) -> SchemaValidationResult:
    """Compare source and target columns. `checks=None` runs `schema_validation`;
    an explicit selection must include `schema_validation`."""
    selected = _select_checks(checks, ["schema_validation"], DATASET_CHECKS, "validate_csv_schema")
    if "schema_validation" not in selected:
        raise ValueError("validate_csv_schema: checks must include 'schema_validation'")
    selected_checks = set(selected)

    # Read each file exactly once and report every defect on both sides,
    # source first, so one unreadable file never masks the other side. Each side
    # is checked and released before the other is loaded.
    selections = {"schema_validation": selected_checks}
    source_headers, source_issues = _read_side(source_path, "source", selections)
    target_headers, target_issues = _read_side(target_path, "target", selections)
    return _schema_result(
        source_path,
        target_path,
        source_headers,
        target_headers,
        source_issues["schema_validation"],
        target_issues["schema_validation"],
    )


def validate_csv_scenario(scenario: Scenario, checks: CheckSelection | None = None) -> dict[str, SchemaValidationResult]:
    """Run the implemented CSV checks. `checks=None` runs the scenario's configured
    implemented checks (possibly none); an explicit selection is never replaced
    by a default. Execution order is fixed: structural, then schema."""
    validate_scenario(scenario)
    if checks is None:
        selected_checks = [check for check in scenario.validation.checks if check in IMPLEMENTED_VALIDATION_CHECKS]
    else:
        selected_checks = _select_checks(checks, [], IMPLEMENTED_VALIDATION_CHECKS, "validate_csv_scenario")
    ordered_checks = [check for check in ("structural_validation", "schema_validation") if check in selected_checks]
    if not ordered_checks:
        return {}

    # Load each input once; every selected check shares that one read. Each side is
    # checked and released before the other is loaded, so only one dataset's rows
    # are in memory at a time.
    selections = {check: {check} for check in ordered_checks}
    source_headers, source_issues = _read_side(scenario.source.path, "source", selections)
    target_headers, target_issues = _read_side(scenario.target.path, "target", selections)

    results: dict[str, SchemaValidationResult] = {}
    if "structural_validation" in selections:
        issues = [*source_issues["structural_validation"], *target_issues["structural_validation"]]
        results["structural_validation"] = SchemaValidationResult(
            dataset="structural_validation",
            passed=not issues,
            issues=issues,
            source_path=scenario.source.path,
            target_path=scenario.target.path,
        )
    if "schema_validation" in selections:
        results["schema_validation"] = _schema_result(
            scenario.source.path,
            scenario.target.path,
            source_headers,
            target_headers,
            source_issues["schema_validation"],
            target_issues["schema_validation"],
        )

    return results
