from pathlib import Path

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
