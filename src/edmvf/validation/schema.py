from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

from edmvf.config.models import Scenario
from edmvf.data.csv_dataset import CsvDataset, CsvFileAccessError, CsvParseError, read_csv_dataset
from edmvf.validation.result import SchemaValidationResult, ValidationIssue


def _issue(kind: str, dataset: str, message: str, row_number: int | None = None, column_name: str | None = None) -> ValidationIssue:
    return ValidationIssue(kind=kind, dataset=dataset, message=message, row_number=row_number, column_name=column_name)


def _header_integrity_issues(dataset: CsvDataset, dataset_name: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not dataset.headers:
        issues.append(_issue("structural", dataset_name, "CSV is empty or missing a header row."))
        return issues

    blank_headers = [index for index, header in enumerate(dataset.headers) if header is None or str(header).strip() == ""]
    if blank_headers:
        issues.append(_issue("schema", dataset_name, "CSV header contains blank column names.", column_name=dataset.headers[blank_headers[0]]))

    duplicates = [name for name, count in Counter(header for header in dataset.headers if header is not None).items() if count > 1]
    if duplicates:
        issues.append(_issue("schema", dataset_name, f"CSV header contains duplicate column names: {duplicates}.", column_name=duplicates[0]))

    return issues


def validate_csv_dataset(csv_path: str | Path, dataset_name: str, checks: Iterable[str] | None = None) -> SchemaValidationResult:
    selected_checks = set(checks or [])
    result = SchemaValidationResult(dataset=dataset_name, passed=True, source_path=str(csv_path))

    try:
        dataset = read_csv_dataset(csv_path)
    except (FileNotFoundError, CsvFileAccessError) as exc:
        result.passed = False
        result.issues.append(_issue("file_access", dataset_name, str(exc)))
        return result
    except CsvParseError as exc:
        result.passed = False
        result.issues.append(_issue("parse_error", dataset_name, str(exc)))
        return result

    if "schema_validation" in selected_checks or "structural_validation" in selected_checks:
        result.issues.extend(_header_integrity_issues(dataset, dataset_name))

    if "structural_validation" in selected_checks:
        header_length = len(dataset.headers)
        for row_number, row in enumerate(dataset.rows, start=2):
            if len(row) != header_length:
                result.issues.append(
                    _issue(
                        "structural",
                        dataset_name,
                        f"Row {row_number} has inconsistent width: {len(row)} values for {header_length} columns.",
                        row_number=row_number,
                    )
                )

    if result.issues:
        result.passed = False

    return result


def validate_csv_schema(source_path: str | Path, target_path: str | Path, checks: Iterable[str] | None = None) -> SchemaValidationResult:
    selected_checks = set(checks or [])
    result = SchemaValidationResult(dataset="schema_comparison", passed=True, source_path=str(source_path), target_path=str(target_path))

    if "schema_validation" not in selected_checks:
        return result

    try:
        source_dataset = read_csv_dataset(source_path)
    except (FileNotFoundError, CsvFileAccessError) as exc:
        result.passed = False
        result.issues.append(_issue("file_access", "source", str(exc)))
        return result
    except CsvParseError as exc:
        result.passed = False
        result.issues.append(_issue("parse_error", "source", str(exc)))
        return result

    try:
        target_dataset = read_csv_dataset(target_path)
    except (FileNotFoundError, CsvFileAccessError) as exc:
        result.passed = False
        result.issues.append(_issue("file_access", "target", str(exc)))
        return result
    except CsvParseError as exc:
        result.passed = False
        result.issues.append(_issue("parse_error", "target", str(exc)))
        return result

    source_result = validate_csv_dataset(source_path, "source", selected_checks)
    target_result = validate_csv_dataset(target_path, "target", selected_checks)

    result.issues.extend(source_result.issues)
    result.issues.extend(target_result.issues)

    if source_result.issues or target_result.issues:
        result.passed = False
        return result

    source_headers = set(source_dataset.headers)
    target_headers = set(target_dataset.headers)

    missing_columns = sorted(source_headers - target_headers)
    unexpected_columns = sorted(target_headers - source_headers)

    if missing_columns:
        result.issues.append(_issue("schema", "source", f"Missing columns in target: {missing_columns}."))
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
