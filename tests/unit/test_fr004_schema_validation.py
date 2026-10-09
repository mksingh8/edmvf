from pathlib import Path

import pytest

from edmvf.config.loader import load_config
from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.runner import run_scenario
from edmvf.validation.schema import validate_csv_dataset, validate_csv_schema


def _write_csv(tmp_path: Path, filename: str, content: str) -> Path:
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    return path


def test_valid_header_only_csv_passes_structural_validation(tmp_path):
    csv_path = _write_csv(tmp_path, "source.csv", "id,name\n")

    result = validate_csv_dataset(csv_path, "source", ["structural_validation"])

    assert result.passed is True
    assert result.issues == []
    assert result.dataset == "source"


def test_empty_csv_fails_structure_validation(tmp_path):
    csv_path = _write_csv(tmp_path, "empty.csv", "")

    result = validate_csv_dataset(csv_path, "source", ["structural_validation"])

    assert result.passed is False
    assert any(issue.kind == "structural" and "empty" in issue.message.lower() for issue in result.issues)


def test_duplicate_or_blank_headers_fail_schema_validation(tmp_path):
    duplicate_path = _write_csv(tmp_path, "duplicate_headers.csv", "id,name,id\n1,Alpha,2\n")
    blank_path = _write_csv(tmp_path, "blank_headers.csv", "id,,email\n1,Alpha,test@example.com\n")

    duplicate_result = validate_csv_dataset(duplicate_path, "source", ["schema_validation"])
    blank_result = validate_csv_dataset(blank_path, "source", ["schema_validation"])

    assert duplicate_result.passed is False
    assert any("duplicate" in issue.message.lower() for issue in duplicate_result.issues)
    assert blank_result.passed is False
    assert any("blank" in issue.message.lower() for issue in blank_result.issues)


def test_irregular_row_width_fails_structural_validation(tmp_path):
    csv_path = _write_csv(tmp_path, "bad_rows.csv", "id,name\n1\n2,Alpha,Extra\n")

    result = validate_csv_dataset(csv_path, "source", ["structural_validation"])

    assert result.passed is False
    assert any(issue.kind == "structural" and "width" in issue.message.lower() for issue in result.issues)


def test_file_access_failure_is_reported_as_file_access(tmp_path):
    missing_path = tmp_path / "missing.csv"

    result = validate_csv_dataset(missing_path, "source", ["schema_validation"])

    assert result.passed is False
    assert any(issue.kind == "file_access" for issue in result.issues)


def test_malformed_csv_quoting_is_reported_separately_from_file_access(tmp_path):
    csv_path = _write_csv(tmp_path, "malformed.csv", 'id,name\n1,"unterminated\n')

    result = validate_csv_dataset(csv_path, "source", ["structural_validation", "schema_validation"])

    assert result.passed is False
    assert any(issue.kind == "parse_error" for issue in result.issues)
    assert not any(issue.kind == "file_access" for issue in result.issues)


def test_schema_validation_stops_before_comparison_when_headers_are_invalid(tmp_path):
    source_path = _write_csv(tmp_path, "source.csv", "id,name,id\n1,Alpha,2\n")
    target_path = _write_csv(tmp_path, "target.csv", "name,id\nAlpha,1\n")

    result = validate_csv_schema(source_path, target_path, ["schema_validation"])

    assert result.passed is False
    assert any("duplicate" in issue.message.lower() for issue in result.issues)
    assert not any("missing" in issue.message.lower() for issue in result.issues)
    assert not any("unexpected" in issue.message.lower() for issue in result.issues)


def test_schema_validation_ignores_column_order_and_reports_missing_or_unexpected_columns(tmp_path):
    source_path = _write_csv(tmp_path, "source.csv", "id,name,age\n1,Alpha,30\n")
    target_path = _write_csv(tmp_path, "target.csv", "age,id,email\n30,1,test@example.com\n")

    result = validate_csv_schema(source_path, target_path, ["schema_validation"])

    assert result.passed is False
    assert any("missing" in issue.message.lower() for issue in result.issues)
    assert any("unexpected" in issue.message.lower() for issue in result.issues)


def test_schema_validation_allows_same_columns_in_different_order(tmp_path):
    source_path = _write_csv(tmp_path, "source.csv", "id,name\n1,Alpha\n")
    target_path = _write_csv(tmp_path, "target.csv", "name,id\nAlpha,1\n")

    result = validate_csv_schema(source_path, target_path, ["schema_validation"])

    assert result.passed is True
    assert result.issues == []


def test_run_scenario_respects_selected_fr004_checks(tmp_path):
    source_path = _write_csv(tmp_path, "source.csv", "id,name\n1,Alpha\n")
    target_path = _write_csv(tmp_path, "target.csv", "id,name\n1,Alpha\n")
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        f"""
name: fr004_selected_checks
version: 1
source:
  type: csv
  path: {source_path}
target:
  type: csv
  path: {target_path}
matching:
  keys:
    - id
validation:
  checks:
    - schema_validation
output:
  directory: ./output
""".strip(),
        encoding="utf-8",
    )

    scenario = load_config(config_path)
    result = run_scenario(scenario)

    assert result["status"] == "ready"
    assert result["schema_validation"]["passed"] is True


def test_run_scenario_marks_unimplemented_checks_as_not_implemented(tmp_path):
    source_path = _write_csv(tmp_path, "source.csv", "id,name\n1,Alpha\n")
    target_path = _write_csv(tmp_path, "target.csv", "id,name\n1,Alpha\n")
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        f"""
name: fr004_unimplemented_only
version: 1
source:
  type: csv
  path: {source_path}
target:
  type: csv
  path: {target_path}
matching:
  keys:
    - id
validation:
  checks:
    - missing_records
output:
  directory: ./output
""".strip(),
        encoding="utf-8",
    )

    scenario = load_config(config_path)
    result = run_scenario(scenario)

    assert result["status"] == "not_implemented"
    assert result["missing_records"]["status"] == "not_implemented"
    assert "schema_validation" not in result
    assert "structural_validation" not in result


def test_run_scenario_reports_mixed_implemented_and_unimplemented_checks(tmp_path):
    source_path = _write_csv(tmp_path, "source.csv", "id,name\n1,Alpha\n")
    target_path = _write_csv(tmp_path, "target.csv", "id,name\n1,Alpha\n")
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        f"""
name: fr004_mixed_checks
version: 1
source:
  type: csv
  path: {source_path}
target:
  type: csv
  path: {target_path}
matching:
  keys:
    - id
validation:
  checks:
    - schema_validation
    - missing_records
output:
  directory: ./output
""".strip(),
        encoding="utf-8",
    )

    scenario = load_config(config_path)
    result = run_scenario(scenario)

    assert result["status"] == "not_implemented"
    assert result["schema_validation"]["passed"] is True
    assert result["missing_records"]["status"] == "not_implemented"


def test_run_scenario_rejects_unsupported_source_target_types_at_boundary():
    scenario = Scenario(
        name="bad_runner_boundary",
        version=1,
        source=DataSource(type="json", path="./data/source.json"),
        target=DataSource(type="csv", path="./data/target.csv"),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=["schema_validation"]),
        output=OutputConfig(directory="./output"),
    )

    with pytest.raises(ValueError, match="unsupported source type"):
        run_scenario(scenario)


def test_run_scenario_rejects_unknown_checks_at_boundary():
    scenario = Scenario(
        name="bad_runner_checks",
        version=1,
        source=DataSource(type="csv", path="./data/source.csv"),
        target=DataSource(type="csv", path="./data/target.csv"),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=["definitely_not_real"]),
        output=OutputConfig(directory="./output"),
    )

    with pytest.raises(ValueError, match="unsupported validation checks"):
        run_scenario(scenario)


def test_run_scenario_rejects_invalid_nested_configuration_at_boundary():
    scenario = Scenario(
        name="bad_runner_nested",
        version=1,
        source=DataSource(type="csv", path="./data/source.csv"),
        target=DataSource(type="csv", path="./data/target.csv"),
        matching=MatchingConfig(keys=[]),
        validation=ValidationConfig(checks=["schema_validation"]),
        output=OutputConfig(directory="./output"),
    )

    with pytest.raises(ValueError, match="matching.keys"):
        run_scenario(scenario)
