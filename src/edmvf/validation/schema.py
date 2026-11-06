from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

from edmvf.config.models import Scenario
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


def validate_csv_dataset(csv_path: str | Path, dataset_name: str, checks: Iterable[str] | None = None) -> SchemaValidationResult:
    selected_checks = set(checks or [])
    result = SchemaValidationResult(dataset=dataset_name, passed=True, source_path=str(csv_path))

    dataset, issues = _load_dataset(csv_path, dataset_name)
    result.issues.extend(issues)
    if dataset is not None:
        result.issues.extend(_dataset_issues(dataset, dataset_name, selected_checks))

    if result.issues:
        result.passed = False

    return result


def validate_csv_schema(source_path: str | Path, target_path: str | Path, checks: Iterable[str] | None = None) -> SchemaValidationResult:
    selected_checks = set(checks or [])
    result = SchemaValidationResult(dataset="schema_comparison", passed=True, source_path=str(source_path), target_path=str(target_path))

    if "schema_validation" not in selected_checks:
        return result

    # Read each file exactly once and report every defect on both sides,
    # source first, so one unreadable file never masks the other side.
    source_dataset, source_issues = _load_dataset(source_path, "source")
    target_dataset, target_issues = _load_dataset(target_path, "target")
    if source_dataset is not None:
        source_issues.extend(_dataset_issues(source_dataset, "source", selected_checks))
    if target_dataset is not None:
        target_issues.extend(_dataset_issues(target_dataset, "target", selected_checks))

    result.issues.extend(source_issues)
    result.issues.extend(target_issues)

    # A column comparison is meaningless against an unreadable or ambiguous header.
    if result.issues:
        result.passed = False
        return result

    source_headers = set(source_dataset.headers)
    target_headers = set(target_dataset.headers)

    missing_columns = sorted(source_headers - target_headers)
    unexpected_columns = sorted(target_headers - source_headers)

    # Column differences are findings about the target (the dataset under test).
    if missing_columns:
        result.issues.append(_issue("schema", "target", f"Missing columns in target: {missing_columns}."))
    if unexpected_columns:
        result.issues.append(_issue("schema", "target", f"Unexpected columns in target: {unexpected_columns}."))

    if result.issues:
        result.passed = False

    return result


def validate_csv_scenario(scenario: Scenario, checks: Iterable[str] | None = None) -> dict[str, SchemaValidationResult]:
    selected_checks = list(checks or scenario.validation.checks)
    results: dict[str, SchemaValidationResult] = {}

    if "structural_validation" in selected_checks:
        source_result = validate_csv_dataset(scenario.source.path, "source", ["structural_validation"])
        target_result = validate_csv_dataset(scenario.target.path, "target", ["structural_validation"])
        combined = SchemaValidationResult(
            dataset="structural_validation",
            passed=source_result.passed and target_result.passed,
            issues=[*source_result.issues, *target_result.issues],
            source_path=scenario.source.path,
            target_path=scenario.target.path,
        )
        if combined.issues:
            combined.passed = False
        results["structural_validation"] = combined

    if "schema_validation" in selected_checks:
        schema_result = validate_csv_schema(scenario.source.path, scenario.target.path, ["schema_validation"])
        results["schema_validation"] = schema_result

    return results
