from pathlib import Path

import pytest
import yaml

from edmvf.config.loader import load_config
from edmvf.config.validator import validate_scenario
from edmvf.config.models import (
    DataSource,
    MatchingConfig,
    OutputConfig,
    Scenario,
    ValidationConfig,
)


def _write_config(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def _write_yaml_config(tmp_path: Path, payload: dict) -> Path:
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return config_path


def test_load_valid_yaml_scenario(tmp_path):
    config_path = _write_config(
        tmp_path,
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
    )

    scenario = load_config(config_path)

    assert isinstance(scenario, Scenario)
    assert scenario.name == "csv_basic_validation"
    assert scenario.source.type == "csv"
    assert scenario.target.type == "csv"
    assert scenario.matching.keys == ["id"]
    assert scenario.validation.checks == ["missing_records", "duplicate_records"]


def test_missing_required_fields_fails(tmp_path):
    config_path = _write_config(
        tmp_path,
        """
name: incomplete_scenario
source:
  type: csv
  path: ./data/source.csv
""".strip(),
    )

    with pytest.raises(ValueError, match="target"):
        load_config(config_path)


def test_unknown_validations_are_rejected(tmp_path):
    config_path = _write_config(
        tmp_path,
        """
name: invalid_check_scenario
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
    - not_a_real_check
output:
  directory: ./output
""".strip(),
    )

    with pytest.raises(ValueError, match="validation checks"):
        load_config(config_path)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (
            {
                "name": "unsupported_source_type",
                "version": 1,
                "source": {"type": "json", "path": "./data/source.csv"},
                "target": {"type": "csv", "path": "./data/target.csv"},
                "matching": {"keys": ["id"]},
                "validation": {"checks": ["missing_records"]},
                "output": {"directory": "./output"},
            },
            "unsupported source type",
        ),
        (
            {
                "name": "unsupported_target_type",
                "version": 1,
                "source": {"type": "csv", "path": "./data/source.csv"},
                "target": {"type": "json", "path": "./data/target.csv"},
                "matching": {"keys": ["id"]},
                "validation": {"checks": ["missing_records"]},
                "output": {"directory": "./output"},
            },
            "unsupported target type",
        ),
    ],
)
def test_unsupported_source_target_type_rejected(tmp_path, payload, match):
    config_path = _write_yaml_config(tmp_path, payload)

    with pytest.raises(ValueError, match=match):
        load_config(config_path)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (
            {
                "name": "missing_source_path",
                "version": 1,
                "source": {"type": "csv"},
                "target": {"type": "csv", "path": "./data/target.csv"},
                "matching": {"keys": ["id"]},
                "validation": {"checks": ["missing_records"]},
                "output": {"directory": "./output"},
            },
            "source.path is required",
        ),
        (
            {
                "name": "empty_target_path",
                "version": 1,
                "source": {"type": "csv", "path": "./data/source.csv"},
                "target": {"type": "csv", "path": ""},
                "matching": {"keys": ["id"]},
                "validation": {"checks": ["missing_records"]},
                "output": {"directory": "./output"},
            },
            "path is required",
        ),
    ],
)
def test_missing_or_empty_source_target_path_rejected(tmp_path, payload, match):
    config_path = _write_yaml_config(tmp_path, payload)

    with pytest.raises(ValueError, match=match):
        load_config(config_path)


@pytest.mark.parametrize(
    "missing_section",
    ["source", "target", "matching", "validation", "output"],
)
def test_required_config_sections_rejected_when_absent(tmp_path, missing_section):
    payload = {
        "name": "missing_required_section",
        "version": 1,
        "source": {"type": "csv", "path": "./data/source.csv"},
        "target": {"type": "csv", "path": "./data/target.csv"},
        "matching": {"keys": ["id"]},
        "validation": {"checks": ["missing_records"]},
        "output": {"directory": "./output"},
    }
    payload.pop(missing_section)

    config_path = _write_yaml_config(tmp_path, payload)

    with pytest.raises(ValueError, match=missing_section):
        load_config(config_path)


def test_validate_scenario_rejects_empty_key_fields():
    scenario = Scenario(
        name="bad_keys",
        version=1,
        source=DataSource(type="csv", path="./data/source.csv"),
        target=DataSource(type="csv", path="./data/target.csv"),
        matching=MatchingConfig(keys=[]),
        validation=ValidationConfig(checks=["missing_records"]),
        output=OutputConfig(directory="./output"),
    )

    with pytest.raises(ValueError, match="keys"):
        validate_scenario(scenario)


def test_fr002_preserves_source_and_target_identities_in_loaded_scenario(tmp_path):
    config_path = _write_config(
        tmp_path,
        """
name: csv_source_target_identity
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
    )

    scenario = load_config(config_path)

    assert scenario.source.type == "csv"
    assert scenario.target.type == "csv"
    assert scenario.source.path == "./data/source.csv"
    assert scenario.target.path == "./data/target.csv"
    assert scenario.source != scenario.target


def test_fr003_supported_checks_can_be_defined_through_yaml(tmp_path):
    config_path = _write_config(
        tmp_path,
        """
name: csv_supported_checks
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
    )

    scenario = load_config(config_path)

    assert scenario.validation.checks == ["missing_records", "duplicate_records"]


def test_fr003_empty_validation_checks_are_rejected(tmp_path):
    config_path = _write_yaml_config(
        tmp_path,
        {
            "name": "empty_validation_checks",
            "version": 1,
            "source": {"type": "csv", "path": "./data/source.csv"},
            "target": {"type": "csv", "path": "./data/target.csv"},
            "matching": {"keys": ["id"]},
            "validation": {"checks": []},
            "output": {"directory": "./output"},
        },
    )

    with pytest.raises(ValueError, match="validation.checks"):
        load_config(config_path)


def test_fr003_different_check_selections_produce_distinct_configured_intent(tmp_path):
    first_payload = {
        "name": "first_check_selection",
        "version": 1,
        "source": {"type": "csv", "path": "./data/first_source.csv"},
        "target": {"type": "csv", "path": "./data/first_target.csv"},
        "matching": {"keys": ["id"]},
        "validation": {"checks": ["missing_records"]},
        "output": {"directory": "./output/first"},
    }
    second_payload = {
        "name": "second_check_selection",
        "version": 2,
        "source": {"type": "csv", "path": "./data/second_source.csv"},
        "target": {"type": "csv", "path": "./data/second_target.csv"},
        "matching": {"keys": ["id"]},
        "validation": {"checks": ["duplicate_records", "mismatched_values"]},
        "output": {"directory": "./output/second"},
    }

    first_path = _write_yaml_config(tmp_path, first_payload)
    second_path = tmp_path / "second_scenario.yaml"
    second_path.write_text(yaml.safe_dump(second_payload, sort_keys=False), encoding="utf-8")

    first_scenario = load_config(first_path)
    second_scenario = load_config(second_path)

    assert first_scenario.validation.checks == ["missing_records"]
    assert second_scenario.validation.checks == ["duplicate_records", "mismatched_values"]
    assert first_scenario != second_scenario


def test_two_valid_yaml_scenarios_load_as_distinct_scenario_models(tmp_path):
    first_payload = {
        "name": "first_valid_scenario",
        "version": 1,
        "source": {"type": "csv", "path": "./data/first_source.csv"},
        "target": {"type": "csv", "path": "./data/first_target.csv"},
        "matching": {"keys": ["id"]},
        "validation": {"checks": ["missing_records"]},
        "output": {"directory": "./output/first"},
    }
    second_payload = {
        "name": "second_valid_scenario",
        "version": 2,
        "source": {"type": "csv", "path": "./data/second_source.csv"},
        "target": {"type": "csv", "path": "./data/second_target.csv"},
        "matching": {"keys": ["customer_id"]},
        "validation": {"checks": ["duplicate_records"]},
        "output": {"directory": "./output/second"},
    }

    first_path = _write_yaml_config(tmp_path, first_payload)
    second_path = tmp_path / "second_scenario.yaml"
    second_path.write_text(yaml.safe_dump(second_payload, sort_keys=False), encoding="utf-8")

    first_scenario = load_config(first_path)
    second_scenario = load_config(second_path)

    assert first_scenario != second_scenario
    assert first_scenario.name == "first_valid_scenario"
    assert second_scenario.name == "second_valid_scenario"
    assert first_scenario.matching.keys == ["id"]
    assert second_scenario.matching.keys == ["customer_id"]
    assert first_scenario.output.directory == "./output/first"
    assert second_scenario.output.directory == "./output/second"
