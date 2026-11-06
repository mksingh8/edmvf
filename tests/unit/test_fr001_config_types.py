"""IR-004: configuration values are type-checked, never silently coerced.

YAML-loaded and directly constructed scenarios must obey one contract.
"""

import copy
import datetime
import re
from pathlib import Path

import pytest
import yaml

from edmvf.config.loader import load_config
from edmvf.config.models import (
    DataSource,
    MatchingConfig,
    OutputConfig,
    Scenario,
    ValidationConfig,
)
from edmvf.config.validator import validate_scenario
from edmvf.runner import run_scenario

VALID = {
    "name": "typed_scenario",
    "version": 1,
    "source": {"type": "csv", "path": "./data/source.csv"},
    "target": {"type": "csv", "path": "./data/target.csv"},
    "matching": {"keys": ["id"]},
    "validation": {"checks": ["schema_validation"]},
    "output": {"directory": "./output"},
}

_MISSING = object()


def _payload(path: str, value):
    """VALID with the dotted `path` replaced (or removed when value is _MISSING)."""
    payload = copy.deepcopy(VALID)
    node = payload
    *parents, leaf = path.split(".")
    for part in parents:
        node = node[part]
    if value is _MISSING:
        node.pop(leaf)
    else:
        node[leaf] = value
    return payload


def _direct(payload: dict) -> Scenario:
    """Build a Scenario straight from raw values, bypassing from_mapping."""
    return Scenario(
        name=payload["name"],
        version=payload.get("version"),
        source=DataSource(**payload["source"]),
        target=DataSource(**payload["target"]),
        matching=MatchingConfig(**payload["matching"]),
        validation=ValidationConfig(**payload["validation"]),
        output=OutputConfig(**payload["output"]),
    )


def _load_yaml(tmp_path: Path, payload: dict) -> Scenario:
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return load_config(config_path)


# (dotted field, bad value, regex the message must match)
BAD_LEAVES = [
    # scenario.name
    ("name", 123, r"scenario\.name must be a string, got int"),
    ("name", 1.5, r"scenario\.name must be a string, got float"),
    ("name", True, r"scenario\.name must be a string, got bool"),
    ("name", ["a", "b"], r"scenario\.name must be a string, got list"),
    ("name", {"a": 1}, r"scenario\.name must be a string, got dict"),
    ("name", datetime.date(2026, 11, 6), r"scenario\.name must be a string, got date"),
    ("name", None, r"scenario\.name is required"),
    ("name", "", r"scenario\.name is required"),
    ("name", "   ", r"scenario\.name is required"),
    # scenario.version (missing/None stays allowed)
    ("version", True, r"scenario\.version must be an integer or a string, got bool"),
    ("version", 1.5, r"scenario\.version must be an integer or a string, got float"),
    ("version", [1], r"scenario\.version must be an integer or a string, got list"),
    ("version", {"a": 1}, r"scenario\.version must be an integer or a string, got dict"),
    ("version", "", r"scenario\.version must not be blank"),
    ("version", "  ", r"scenario\.version must not be blank"),
    # source / target type and path
    ("source.type", 1, r"source\.type must be a string, got int"),
    ("source.type", ["csv"], r"source\.type must be a string, got list"),
    ("target.type", True, r"target\.type must be a string, got bool"),
    ("source.type", None, r"source\.type is required"),
    ("source.path", 123, r"source\.path must be a string, got int"),
    ("source.path", True, r"source\.path must be a string, got bool"),
    ("source.path", ["a.csv"], r"source\.path must be a string, got list"),
    ("target.path", {"a": 1}, r"target\.path must be a string, got dict"),
    ("source.path", None, r"source\.path is required"),
    ("source.path", "", r"source\.path is required"),
    ("target.path", "   ", r"target\.path is required"),
    # matching.keys
    ("matching.keys", "id", r"matching\.keys must be a list, got str"),
    ("matching.keys", {"a": 1}, r"matching\.keys must be a list, got dict"),
    ("matching.keys", None, r"matching\.keys is required"),
    ("matching.keys", [], r"matching\.keys must contain at least one key field"),
    ("matching.keys", ["id", 1], r"matching\.keys\[1\] must be a string, got int"),
    ("matching.keys", [None], r"matching\.keys\[0\] must be a string, got NoneType"),
    ("matching.keys", [True], r"matching\.keys\[0\] must be a string, got bool"),
    ("matching.keys", [["id"]], r"matching\.keys\[0\] must be a string, got list"),
    ("matching.keys", [""], r"matching\.keys\[0\] must not be blank"),
    ("matching.keys", ["id", "  "], r"matching\.keys\[1\] must not be blank"),
    # validation.checks
    ("validation.checks", "schema_validation", r"validation\.checks must be a list, got str"),
    ("validation.checks", None, r"validation\.checks is required"),
    ("validation.checks", [], r"validation\.checks must contain at least one"),
    ("validation.checks", [1], r"validation\.checks\[0\] must be a string, got int"),
    ("validation.checks", [["schema_validation"]], r"validation\.checks\[0\] must be a string, got list"),
    ("validation.checks", [""], r"validation\.checks\[0\] must not be blank"),
    # output.directory
    ("output.directory", 5, r"output\.directory must be a string, got int"),
    ("output.directory", True, r"output\.directory must be a string, got bool"),
    ("output.directory", ["out"], r"output\.directory must be a string, got list"),
    ("output.directory", None, r"output\.directory is required"),
    ("output.directory", "  ", r"output\.directory is required"),
]


def _case_id(case):
    field, value, _ = case
    return f"{field}={value!r}"


@pytest.mark.parametrize("case", BAD_LEAVES, ids=_case_id)
def test_ir004_yaml_and_direct_construction_reject_bad_leaf_values_identically(tmp_path, case):
    field, value, pattern = case
    payload = _payload(field, value)

    with pytest.raises(ValueError, match=pattern) as yaml_error:
        _load_yaml(tmp_path, payload)

    # Direct construction: same value, same contract, same message.
    # (datetime.date and similar survive yaml round-trips as-is.)
    with pytest.raises(ValueError, match=pattern) as direct_error:
        validate_scenario(_direct(payload))
    with pytest.raises(ValueError, match=pattern) as runner_error:
        run_scenario(_direct(payload))

    assert str(yaml_error.value) == str(direct_error.value) == str(runner_error.value)


@pytest.mark.parametrize("version", [1, 0, 2, "1.0", "v2", "2026-11"])
def test_ir004_valid_versions_are_kept_unchanged(tmp_path, version):
    scenario = _load_yaml(tmp_path, _payload("version", version))

    assert scenario.version == version
    assert type(scenario.version) is type(version)


def test_ir004_missing_version_is_still_allowed(tmp_path):
    scenario = _load_yaml(tmp_path, _payload("version", _MISSING))

    assert scenario.version is None


def test_ir004_valid_quoted_values_are_preserved_without_coercion(tmp_path):
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        """
name: "123"
version: "1.0"
source:
  type: csv
  path: "2026"
target:
  type: csv
  path: target.csv
matching:
  keys: ["007", "Customer ID"]
validation:
  checks: [schema_validation]
output:
  directory: "5"
""".strip(),
        encoding="utf-8",
    )

    scenario = load_config(config_path)

    assert scenario.name == "123"
    assert scenario.version == "1.0"
    assert scenario.source.path == "2026"
    assert scenario.matching.keys == ["007", "Customer ID"]
    assert scenario.output.directory == "5"


@pytest.mark.parametrize(
    ("yaml_fragment", "pattern"),
    [
        ("keys: [007]", r"matching\.keys\[0\] must be a string, got int"),
        ("keys: [on]", r"matching\.keys\[0\] must be a string, got bool"),
        ("keys: [yes]", r"matching\.keys\[0\] must be a string, got bool"),
    ],
)
def test_ir004_unquoted_yaml_scalars_in_keys_are_rejected_not_converted(tmp_path, yaml_fragment, pattern):
    text = yaml.safe_dump(_payload("matching.keys", ["id"]), sort_keys=False).replace("keys:\n  - id", yaml_fragment)
    assert yaml_fragment in text
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=pattern):
        load_config(config_path)


def test_ir004_unquoted_yaml_name_scalars_are_rejected_not_converted(tmp_path):
    for raw, pattern in [("yes", "got bool"), ("2026-11-06", "got date"), ("123", "got int")]:
        text = yaml.safe_dump(VALID, sort_keys=False).replace("name: typed_scenario", f"name: {raw}")
        config_path = tmp_path / "scenario.yaml"
        config_path.write_text(text, encoding="utf-8")

        with pytest.raises(ValueError, match=rf"scenario\.name must be a string, {pattern}"):
            load_config(config_path)


def test_ir004_unquoted_yaml_version_1_10_is_a_float_and_rejected(tmp_path):
    text = yaml.safe_dump(VALID, sort_keys=False).replace("version: 1", "version: 1.10")
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=r"scenario\.version must be an integer or a string, got float"):
        load_config(config_path)


@pytest.mark.parametrize(
    ("field", "bad_object", "pattern"),
    [
        ("source", {"type": "csv", "path": "s.csv"}, r"source must be a DataSource, got dict"),
        ("source", ("csv", "s.csv"), r"source must be a DataSource, got tuple"),
        ("source", None, r"source is required"),
        ("target", MatchingConfig(keys=["id"]), r"target must be a DataSource, got MatchingConfig"),
        ("matching", None, r"matching is required"),
        ("matching", {"keys": ["id"]}, r"matching must be a MatchingConfig, got dict"),
        ("validation", None, r"validation is required"),
        ("validation", {"checks": ["schema_validation"]}, r"validation must be a ValidationConfig, got dict"),
        ("output", None, r"output is required"),
        ("output", {"directory": "out"}, r"output must be an OutputConfig, got dict"),
    ],
)
def test_ir004_direct_construction_rejects_wrong_section_objects(field, bad_object, pattern):
    scenario = _direct(copy.deepcopy(VALID))
    object.__setattr__(scenario, field, bad_object)

    with pytest.raises(ValueError, match=pattern):
        validate_scenario(scenario)
    with pytest.raises(ValueError, match=pattern):
        run_scenario(scenario)


@pytest.mark.parametrize("role", ["source", "target"])
def test_ir004_yaml_non_mapping_section_names_the_correct_role(tmp_path, role):
    with pytest.raises(ValueError, match=rf"^{role} must be a mapping"):
        _load_yaml(tmp_path, _payload(role, "not-a-mapping"))


def test_ir004_direct_construction_rejects_tuple_and_pathlike_values():
    scenario = _direct(copy.deepcopy(VALID))
    object.__setattr__(scenario.matching, "keys", ("id",))
    with pytest.raises(ValueError, match=r"matching\.keys must be a list, got tuple"):
        validate_scenario(scenario)

    scenario = _direct(copy.deepcopy(VALID))
    object.__setattr__(scenario.source, "path", Path("source.csv"))
    with pytest.raises(ValueError, match=r"source\.path must be a string, got (PosixPath|WindowsPath)"):
        validate_scenario(scenario)


@pytest.mark.parametrize("role", ["source", "target"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_ir004_blank_path_is_configuration_error_not_file_access(role, blank):
    payload = _payload(f"{role}.path", blank)

    with pytest.raises(ValueError, match=rf"{role}\.path is required"):
        run_scenario(_direct(payload))


def test_ir004_run_scenario_rejects_malformed_scenario_before_any_data_access(tmp_path):
    # Files do not exist: a returned payload (rather than ValueError) would mean
    # the runner got as far as reading data.
    payload = _payload("source.path", 123)
    payload["target"]["path"] = str(tmp_path / "does-not-exist.csv")

    with pytest.raises(ValueError, match=r"source\.path must be a string"):
        run_scenario(_direct(payload))


def test_ir004_former_attribute_and_type_error_leaks_are_value_errors():
    cases = []
    s = _direct(copy.deepcopy(VALID))
    object.__setattr__(s, "source", {"type": "csv", "path": "s.csv"})
    cases.append(s)
    s = _direct(copy.deepcopy(VALID))
    object.__setattr__(s.source, "type", ["csv"])
    cases.append(s)
    s = _direct(copy.deepcopy(VALID))
    object.__setattr__(s, "matching", {"keys": ["id"]})
    cases.append(s)
    s = _direct(copy.deepcopy(VALID))
    object.__setattr__(s.validation, "checks", [["schema_validation"]])
    cases.append(s)
    s = _direct(copy.deepcopy(VALID))
    object.__setattr__(s.source, "path", 123)
    cases.append(s)

    for scenario in cases:
        with pytest.raises(ValueError):
            validate_scenario(scenario)
        with pytest.raises(ValueError):
            run_scenario(scenario)


@pytest.mark.parametrize(
    ("section", "extra"),
    [
        (None, "verison"),
        ("source", "encoding"),
        ("target", "encoding"),
        ("matching", "key"),
        ("validation", "check"),
        ("output", "dir"),
    ],
)
def test_ir004_unknown_config_keys_are_rejected(tmp_path, section, extra):
    payload = copy.deepcopy(VALID)
    (payload if section is None else payload[section])[extra] = "x"
    label = "scenario" if section is None else section

    with pytest.raises(ValueError, match=rf"unknown configuration key\(s\) in {label}: \['{extra}'\]"):
        _load_yaml(tmp_path, payload)


def test_ir004_unknown_keys_are_reported_sorted(tmp_path):
    payload = copy.deepcopy(VALID)
    payload["zeta"] = 1
    payload["alpha"] = 2

    with pytest.raises(ValueError, match=re.escape("['alpha', 'zeta']")):
        _load_yaml(tmp_path, payload)


def test_ir004_error_messages_do_not_echo_container_values(tmp_path):
    secret = "super-secret-value"

    with pytest.raises(ValueError) as excinfo:
        _load_yaml(tmp_path, _payload("matching.keys", [[secret]]))

    assert secret not in str(excinfo.value)
