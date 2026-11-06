from pathlib import Path

import pytest

from edmvf.config.loader import load_config
from edmvf.runner import run_scenario


def test_valid_scenario_is_passed_to_execution_boundary(tmp_path):
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        """
name: csv_basic_validation
version: 1
source:
  type: csv
  path: ./data/source.csv
target:
  type: csv
  path: ./data/target.csv
matching:
  keys:
    - id
validation:
  checks:
    - missing_records
    - duplicate_records
output:
  directory: ./output
""".strip(),
        encoding="utf-8",
    )

    scenario = load_config(config_path)
    result = run_scenario(scenario)

    assert result["scenario_name"] == "csv_basic_validation"
    assert result["source_type"] == "csv"
    assert result["target_type"] == "csv"
    assert result["status"] == "not_implemented"
    assert result["missing_records"]["status"] == "not_implemented"
    assert result["duplicate_records"]["status"] == "not_implemented"


def test_fr002_source_and_target_identities_remain_available_at_execution_boundary(tmp_path):
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        """
name: csv_source_target_boundary
version: 1
source:
  type: csv
  path: ./data/source.csv
target:
  type: csv
  path: ./data/target.csv
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

    assert scenario.source.path == "./data/source.csv"
    assert scenario.target.path == "./data/target.csv"
    assert scenario.source.type == "csv"
    assert scenario.target.type == "csv"
    assert result["source_type"] == "csv"
    assert result["target_type"] == "csv"
    assert result["status"] == "not_implemented"
    assert result["missing_records"]["status"] == "not_implemented"


def test_same_config_reloads_to_equivalent_scenario(tmp_path):
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        """
name: csv_basic_validation
version: 1
source:
  type: csv
  path: ./data/source.csv
target:
  type: csv
  path: ./data/target.csv
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

    first = load_config(config_path)
    second = load_config(config_path)

    assert first == second
    assert first.source.path == second.source.path
    assert first.matching.keys == second.matching.keys


def test_ir004_quoted_values_load_and_reach_execution_boundary(tmp_path):
    source_path = tmp_path / "source.csv"
    target_path = tmp_path / "target.csv"
    source_path.write_text("007,name\n1,Alpha\n", encoding="utf-8")
    target_path.write_text("007,name\n1,Alpha\n", encoding="utf-8")
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        f"""
name: "123"
version: "1.0"
source:
  type: csv
  path: {source_path}
target:
  type: csv
  path: {target_path}
matching:
  keys: ["007"]
validation:
  checks: [schema_validation]
output:
  directory: ./output
""".strip(),
        encoding="utf-8",
    )

    result = run_scenario(load_config(config_path))

    assert result["scenario_name"] == "123"
    assert result["matching_keys"] == ["007"]
    assert result["status"] == "ready"


def test_ir004_wrong_types_in_yaml_fail_at_load_not_at_run(tmp_path):
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        """
name: wrong_types
version: 1
source:
  type: csv
  path: 12345
target:
  type: csv
  path: target.csv
matching:
  keys: [id]
validation:
  checks: [schema_validation]
output:
  directory: ./output
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"source\.path must be a string, got int"):
        load_config(config_path)
