"""IR-006: consistent check-selection semantics across entry points.

- Duplicate / empty / unknown selections are rejected (never silently ignored).
- `checks=None` at the lower-level validators means the documented default;
  an explicit empty selection never falls back to a default.
- Every rejection happens before any file is read.
"""

import copy
import re
from pathlib import Path

import pytest
import yaml

import edmvf.validation.schema as schema_module
from edmvf.config.loader import load_config
from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.config.validator import validate_scenario
from edmvf.runner import run_scenario
from edmvf.validation.schema import validate_csv_dataset, validate_csv_scenario, validate_csv_schema

GOOD = "id,name\n1,Alpha\n"
WIDTH_BAD = "id,name\n1,Alpha,extra\n"


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8", newline="")
    return path


def _scenario(source: Path, target: Path, checks: list) -> Scenario:
    return Scenario(
        name="ir006",
        version=1,
        source=DataSource("csv", str(source)),
        target=DataSource("csv", str(target)),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=checks),
        output=OutputConfig(directory="./out"),
    )


@pytest.fixture
def read_spy(monkeypatch):
    reads = []
    real = schema_module.read_csv_dataset

    def spy(path):
        reads.append(str(path))
        return real(path)

    monkeypatch.setattr(schema_module, "read_csv_dataset", spy)
    return reads


# --- duplicates in validation.checks -------------------------------------------


@pytest.mark.parametrize(
    ("checks", "duplicates"),
    [
        (["schema_validation", "schema_validation"], "['schema_validation']"),
        (["schema_validation", "structural_validation", "schema_validation"], "['schema_validation']"),
        (["missing_records", "duplicate_records", "missing_records", "duplicate_records"], "['missing_records', 'duplicate_records']"),
    ],
)
def test_duplicate_checks_are_rejected_for_direct_scenarios(tmp_path, read_spy, checks, duplicates):
    scenario = _scenario(tmp_path / "s.csv", tmp_path / "t.csv", checks)
    expected = re.escape(f"validation.checks contains duplicate entries: {duplicates}")

    with pytest.raises(ValueError, match=expected):
        validate_scenario(scenario)
    with pytest.raises(ValueError, match=expected):
        run_scenario(scenario)
    assert read_spy == []


def test_duplicate_checks_are_rejected_from_yaml_identically(tmp_path, read_spy):
    config = {
        "name": "dup_checks",
        "version": 1,
        "source": {"type": "csv", "path": str(tmp_path / "s.csv")},
        "target": {"type": "csv", "path": str(tmp_path / "t.csv")},
        "matching": {"keys": ["id"]},
        "validation": {"checks": ["schema_validation", "schema_validation"]},
        "output": {"directory": "./out"},
    }
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match=r"validation\.checks contains duplicate entries"):
        load_config(path)
    assert read_spy == []


def test_unsupported_checks_are_reported_before_duplicates(tmp_path):
    scenario = _scenario(tmp_path / "s.csv", tmp_path / "t.csv", ["bogus", "bogus"])

    with pytest.raises(ValueError, match="unsupported validation checks"):
        validate_scenario(scenario)


def test_duplicate_matching_keys_are_left_to_ir017(tmp_path):
    scenario = _scenario(tmp_path / "s.csv", tmp_path / "t.csv", ["schema_validation"])
    object.__setattr__(scenario.matching, "keys", ["id", "id"])

    assert validate_scenario(scenario) is scenario  # unchanged here; tracked as IR-017


# --- explicit selections at the lower-level validators --------------------------

BAD_SELECTIONS = [
    pytest.param([], id="empty"),
    pytest.param("structural_validation", id="bare-string"),
    pytest.param(["bogus_check"], id="unknown"),
    pytest.param(["missing_records"], id="unimplemented-name"),
    pytest.param(["schema_validation", "schema_validation"], id="duplicate"),
    pytest.param([1], id="non-string"),
    pytest.param(("schema_validation", "schema_validation"), id="duplicate-tuple"),
]


@pytest.mark.parametrize("selection", BAD_SELECTIONS)
def test_validate_csv_dataset_rejects_bad_selection_without_reading(tmp_path, read_spy, selection):
    path = _write(tmp_path, "d.csv", GOOD)

    with pytest.raises(ValueError, match="checks"):
        validate_csv_dataset(path, "source", selection)
    assert read_spy == []


@pytest.mark.parametrize("selection", [*BAD_SELECTIONS, pytest.param(["structural_validation"], id="without-schema")])
def test_validate_csv_schema_rejects_bad_selection_without_reading(tmp_path, read_spy, selection):
    path = _write(tmp_path, "d.csv", GOOD)

    with pytest.raises(ValueError, match="checks"):
        validate_csv_schema(path, path, selection)
    assert read_spy == []


@pytest.mark.parametrize("selection", BAD_SELECTIONS)
def test_validate_csv_scenario_rejects_bad_explicit_selection_without_reading(tmp_path, read_spy, selection):
    path = _write(tmp_path, "d.csv", GOOD)
    scenario = _scenario(path, path, ["schema_validation", "structural_validation"])

    with pytest.raises(ValueError, match="checks"):
        validate_csv_scenario(scenario, selection)
    assert read_spy == []


def test_empty_selection_never_falls_back_to_the_scenarios_checks(tmp_path, read_spy):
    path = _write(tmp_path, "d.csv", WIDTH_BAD)
    scenario = _scenario(path, path, ["structural_validation"])

    with pytest.raises(ValueError):
        validate_csv_scenario(scenario, [])
    assert read_spy == []


def test_validate_csv_scenario_rejects_an_invalid_scenario_like_validate_scenario(tmp_path, read_spy):
    path = _write(tmp_path, "d.csv", GOOD)
    scenario = _scenario(path, path, ["schema_validation"])
    object.__setattr__(scenario.source, "type", "xml")

    with pytest.raises(ValueError, match="unsupported source type"):
        validate_csv_scenario(scenario)
    assert read_spy == []


# --- documented defaults for checks=None -----------------------------------------


def test_dataset_default_runs_header_and_width_checks(tmp_path):
    path = _write(tmp_path, "d.csv", WIDTH_BAD)

    result = validate_csv_dataset(path, "source")

    assert result.passed is False
    assert [i.kind for i in result.issues] == ["structural"]


def test_schema_default_runs_the_column_comparison(tmp_path):
    source = _write(tmp_path, "s.csv", GOOD)
    target = _write(tmp_path, "t.csv", "id,email\n1,a\n")

    result = validate_csv_schema(source, target)

    assert result.passed is False
    assert [i.dataset for i in result.issues] == ["target", "target"]


def test_scenario_default_runs_the_configured_implemented_checks(tmp_path):
    path = _write(tmp_path, "d.csv", WIDTH_BAD)
    scenario = _scenario(path, path, ["structural_validation", "missing_records"])

    results = validate_csv_scenario(scenario)

    assert list(results) == ["structural_validation"]
    assert results["structural_validation"].passed is False


def test_scenario_default_with_only_unimplemented_checks_is_empty(tmp_path):
    path = _write(tmp_path, "d.csv", GOOD)

    assert validate_csv_scenario(_scenario(path, path, ["missing_records"])) == {}


def test_explicit_selection_runs_only_what_was_asked(tmp_path):
    path = _write(tmp_path, "d.csv", WIDTH_BAD)
    scenario = _scenario(path, path, ["structural_validation", "schema_validation"])

    assert list(validate_csv_scenario(scenario, ["schema_validation"])) == ["schema_validation"]


def test_valid_selections_are_not_mutated(tmp_path):
    path = _write(tmp_path, "d.csv", GOOD)
    selection = ["schema_validation", "structural_validation"]
    before = copy.deepcopy(selection)

    validate_csv_dataset(path, "source", selection)
    validate_csv_schema(path, path, selection)

    assert selection == before
