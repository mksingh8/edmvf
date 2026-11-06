"""IR-004 adversarial edge cases: YAML 1.1 implicit typing, odd key types,
post-load mutation, error ordering, and message hygiene."""

import copy
import dataclasses
import math

import pytest

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

TEMPLATE = """\
name: NAME
version: VERSION
source:
  type: csv
  path: SRC
target:
  type: csv
  path: TGT
matching:
  keys: KEYS
validation:
  checks: CHECKS
output:
  directory: OUT
"""

DEFAULTS = {
    "NAME": "edge_scenario",
    "VERSION": "1",
    "SRC": "source.csv",
    "TGT": "target.csv",
    "KEYS": "[id]",
    "CHECKS": "[schema_validation]",
    "OUT": "./output",
}


def _write(tmp_path, **overrides):
    values = {**DEFAULTS, **overrides}
    text = TEMPLATE
    # Replace longest placeholders first so SRC/TGT/OUT do not clash.
    for placeholder in sorted(values, key=len, reverse=True):
        text = text.replace(f": {placeholder}\n", f": {values[placeholder]}\n")
    path = tmp_path / "scenario.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _write_raw(tmp_path, text):
    path = tmp_path / "scenario.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _valid_scenario(**overrides) -> Scenario:
    fields = dict(
        name="edge",
        version=1,
        source=DataSource(type="csv", path="s.csv"),
        target=DataSource(type="csv", path="t.csv"),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=["schema_validation"]),
        output=OutputConfig(directory="out"),
    )
    fields.update(overrides)
    return Scenario(**fields)


# --------------------------------------------------------------------------
# YAML 1.1 implicit types
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("override", "pattern"),
    [
        # name
        ({"NAME": "yes"}, r"scenario\.name must be a string, got bool"),
        ({"NAME": "No"}, r"scenario\.name must be a string, got bool"),
        ({"NAME": "on"}, r"scenario\.name must be a string, got bool"),
        ({"NAME": "off"}, r"scenario\.name must be a string, got bool"),
        ({"NAME": "null"}, r"scenario\.name is required"),
        ({"NAME": "~"}, r"scenario\.name is required"),
        ({"NAME": "007"}, r"scenario\.name must be a string, got int"),
        ({"NAME": "0x1A"}, r"scenario\.name must be a string, got int"),
        ({"NAME": "1_000"}, r"scenario\.name must be a string, got int"),
        ({"NAME": "1:30"}, r"scenario\.name must be a string, got int"),
        ({"NAME": "1.0e+3"}, r"scenario\.name must be a string, got float"),
        ({"NAME": ".inf"}, r"scenario\.name must be a string, got float"),
        ({"NAME": "-.inf"}, r"scenario\.name must be a string, got float"),
        ({"NAME": ".nan"}, r"scenario\.name must be a string, got float"),
        ({"NAME": "2026-11-06"}, r"scenario\.name must be a string, got date"),
        ({"NAME": "2026-11-06T10:00:00"}, r"scenario\.name must be a string, got datetime"),
        # version
        ({"VERSION": "yes"}, r"scenario\.version must be an integer or a string, got bool"),
        ({"VERSION": "off"}, r"scenario\.version must be an integer or a string, got bool"),
        ({"VERSION": ".nan"}, r"scenario\.version must be an integer or a string, got float"),
        ({"VERSION": ".inf"}, r"scenario\.version must be an integer or a string, got float"),
        ({"VERSION": "-.inf"}, r"scenario\.version must be an integer or a string, got float"),
        ({"VERSION": "1.0e+3"}, r"scenario\.version must be an integer or a string, got float"),
        ({"VERSION": "2026-11-06"}, r"scenario\.version must be an integer or a string, got date"),
        ({"VERSION": "2026-11-06T10:00:00"}, r"scenario\.version must be an integer or a string, got datetime"),
        ({"VERSION": "[1, 2]"}, r"scenario\.version must be an integer or a string, got list"),
        ({"VERSION": "{a: 1}"}, r"scenario\.version must be an integer or a string, got dict"),
        # paths and directory
        ({"SRC": "0x1A"}, r"source\.path must be a string, got int"),
        ({"SRC": "no"}, r"source\.path must be a string, got bool"),
        ({"SRC": "~"}, r"source\.path is required"),
        ({"TGT": "007"}, r"target\.path must be a string, got int"),
        ({"TGT": "1.5"}, r"target\.path must be a string, got float"),
        ({"TGT": "null"}, r"target\.path is required"),
        ({"TGT": "2026-11-06"}, r"target\.path must be a string, got date"),
        ({"OUT": "007"}, r"output\.directory must be a string, got int"),
        ({"OUT": "on"}, r"output\.directory must be a string, got bool"),
        ({"OUT": "~"}, r"output\.directory is required"),
        ({"OUT": ".nan"}, r"output\.directory must be a string, got float"),
        # keys
        ({"KEYS": "[~]"}, r"matching\.keys\[0\] must be a string, got NoneType"),
        ({"KEYS": "[null]"}, r"matching\.keys\[0\] must be a string, got NoneType"),
        ({"KEYS": "[id, off]"}, r"matching\.keys\[1\] must be a string, got bool"),
        ({"KEYS": "[0x1A]"}, r"matching\.keys\[0\] must be a string, got int"),
        ({"KEYS": "[.nan]"}, r"matching\.keys\[0\] must be a string, got float"),
        ({"KEYS": "[.inf]"}, r"matching\.keys\[0\] must be a string, got float"),
        ({"KEYS": "[2026-11-06]"}, r"matching\.keys\[0\] must be a string, got date"),
        ({"KEYS": "[2026-11-06T10:00:00]"}, r"matching\.keys\[0\] must be a string, got datetime"),
        ({"KEYS": "~"}, r"matching\.keys is required"),
        ({"KEYS": "id"}, r"matching\.keys must be a list, got str"),
        ({"KEYS": "007"}, r"matching\.keys must be a list, got int"),
        # checks
        ({"CHECKS": "[yes]"}, r"validation\.checks\[0\] must be a string, got bool"),
        ({"CHECKS": "[~]"}, r"validation\.checks\[0\] must be a string, got NoneType"),
        ({"CHECKS": "[1e3]"}, r"unsupported validation checks"),  # "1e3" is a str in PyYAML
        ({"CHECKS": "[1.0e+3]"}, r"validation\.checks\[0\] must be a string, got float"),
        ({"CHECKS": "[schema_validation, 007]"}, r"validation\.checks\[1\] must be a string, got int"),
        ({"CHECKS": "schema_validation"}, r"validation\.checks must be a list, got str"),
        ({"CHECKS": "{schema_validation: true}"}, r"validation\.checks must be a list, got dict"),
    ],
    ids=lambda v: str(v) if isinstance(v, dict) else None,
)
def test_yaml_1_1_implicit_scalars_are_rejected_with_exact_message(tmp_path, override, pattern):
    with pytest.raises(ValueError, match=pattern):
        load_config(_write(tmp_path, **override))


@pytest.mark.parametrize(
    ("version_text", "expected"),
    [
        ("007", 7),  # YAML 1.1 octal -> int 7; allowed because ints are allowed
        ("0x1A", 26),
        ("1_000", 1000),
        ("1e3", "1e3"),  # PyYAML keeps this as a str; must stay untouched
        ("'1.10'", "1.10"),
        ("'  1  '", "  1  "),  # non-blank str preserved verbatim, no strip
        ("100000000000000000000000000000000000000000", 10**41),
        ("-5", -5),
        ("0", 0),
        ("~", None),
        ("null", None),
    ],
)
def test_valid_version_forms_are_preserved_exactly(tmp_path, version_text, expected):
    scenario = load_config(_write(tmp_path, VERSION=version_text))

    assert scenario.version == expected
    assert type(scenario.version) is type(expected)


def test_very_large_integer_version_is_kept_without_overflow_or_conversion():
    big = 10**400
    scenario = _valid_scenario(version=big)

    assert validate_scenario(scenario).version == big


@pytest.mark.parametrize("version", [math.nan, math.inf, -math.inf])
def test_direct_nan_and_infinite_versions_are_rejected(version):
    with pytest.raises(ValueError, match=r"scenario\.version must be an integer or a string, got float"):
        validate_scenario(_valid_scenario(version=version))


# --------------------------------------------------------------------------
# Type subclass tricks
# --------------------------------------------------------------------------

def test_bool_valued_int_subclass_tricks_are_rejected_for_version():
    for value in (True, False):
        with pytest.raises(ValueError, match=r"got bool"):
            validate_scenario(_valid_scenario(version=value))


def test_str_subclass_is_accepted_and_kept_as_the_same_object():
    class Tagged(str):
        pass

    name = Tagged("tagged")
    scenario = validate_scenario(_valid_scenario(name=name))

    assert scenario.name is name


def test_str_subclass_in_list_items_is_accepted():
    class Tagged(str):
        pass

    scenario = _valid_scenario(matching=MatchingConfig(keys=[Tagged("id")]))

    assert validate_scenario(scenario) is scenario


def test_list_subclass_is_accepted_as_a_list():
    class Keys(list):
        pass

    scenario = _valid_scenario(matching=MatchingConfig(keys=Keys(["id"])))

    assert validate_scenario(scenario) is scenario


@pytest.mark.parametrize(
    "value",
    [("id",), {"id"}, frozenset({"id"}), (k for k in ["id"]), {"id": 1}, b"id", bytearray(b"id")],
    ids=["tuple", "set", "frozenset", "generator", "dict", "bytes", "bytearray"],
)
def test_non_list_containers_are_rejected_for_keys(value):
    with pytest.raises(ValueError, match=r"matching\.keys must be a list, got "):
        validate_scenario(_valid_scenario(matching=MatchingConfig(keys=value)))


def test_bytes_items_are_not_strings():
    with pytest.raises(ValueError, match=r"matching\.keys\[0\] must be a string, got bytes"):
        validate_scenario(_valid_scenario(matching=MatchingConfig(keys=[b"id"])))


# --------------------------------------------------------------------------
# Key preservation: no stripping or normalisation
# --------------------------------------------------------------------------

def test_padded_and_unicode_keys_are_preserved_exactly(tmp_path):
    keys = ["id ", " order id", "Café", "ßeta", "a\tb", "col,with\"quote"]
    path = tmp_path / "scenario.yaml"
    import yaml

    path.write_text(
        yaml.safe_dump(
            {
                "name": " padded name ",
                "version": " v1 ",
                "source": {"type": "csv", "path": " s.csv "},
                "target": {"type": "csv", "path": "t.csv"},
                "matching": {"keys": keys},
                "validation": {"checks": ["schema_validation"]},
                "output": {"directory": " out "},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    scenario = load_config(path)

    assert scenario.matching.keys == keys
    assert [len(k) for k in scenario.matching.keys] == [len(k) for k in keys]
    assert scenario.name == " padded name "
    assert scenario.version == " v1 "
    assert scenario.source.path == " s.csv "
    assert scenario.output.directory == " out "


def test_padded_key_survives_into_run_scenario_payload(tmp_path):
    src = tmp_path / "s.csv"
    src.write_text("id ,x\n1,2\n", encoding="utf-8")
    scenario = _valid_scenario(
        source=DataSource(type="csv", path=str(src)),
        target=DataSource(type="csv", path=str(src)),
        matching=MatchingConfig(keys=["id "]),
    )

    result = run_scenario(scenario)

    assert result["matching_keys"] == ["id "]


@pytest.mark.parametrize("blank", [" ", "　", "\t", "\n", " \t\n ", " "])
def test_unicode_whitespace_only_values_are_blank(blank):
    with pytest.raises(ValueError, match=r"matching\.keys\[0\] must not be blank"):
        validate_scenario(_valid_scenario(matching=MatchingConfig(keys=[blank])))
    with pytest.raises(ValueError, match=r"scenario\.name is required"):
        validate_scenario(_valid_scenario(name=blank))
    with pytest.raises(ValueError, match=r"scenario\.version must not be blank"):
        validate_scenario(_valid_scenario(version=blank))
    with pytest.raises(ValueError, match=r"output\.directory is required"):
        validate_scenario(_valid_scenario(output=OutputConfig(directory=blank)))


def test_padded_source_type_is_unsupported_not_stripped():
    with pytest.raises(ValueError, match=r"unsupported source type: csv "):
        validate_scenario(_valid_scenario(source=DataSource(type="csv ", path="s.csv")))
    with pytest.raises(ValueError, match=r"unsupported target type: CSV"):
        validate_scenario(_valid_scenario(target=DataSource(type="CSV", path="t.csv")))


def test_padded_check_name_is_unsupported_not_stripped():
    with pytest.raises(ValueError, match=r"unsupported validation checks: \['schema_validation '\]"):
        validate_scenario(
            _valid_scenario(validation=ValidationConfig(checks=["schema_validation "]))
        )


# --------------------------------------------------------------------------
# Odd YAML structure
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        ("", r"configuration file is empty"),
        ("# only a comment\n", r"configuration file is empty"),
        ("---\n", r"configuration file is empty"),
        ("null\n", r"configuration file is empty"),
        ("- a\n- b\n", r"must contain a dictionary at the root level"),
        ("just a string\n", r"must contain a dictionary at the root level"),
        ("42\n", r"must contain a dictionary at the root level"),
        ("true\n", r"must contain a dictionary at the root level"),
        ("[]\n", r"must contain a dictionary at the root level"),
        ("{}\n", r"source is required"),
    ],
)
def test_odd_root_documents_raise_value_error(tmp_path, text, pattern):
    with pytest.raises(ValueError, match=pattern):
        load_config(_write_raw(tmp_path, text))


@pytest.mark.parametrize("section", ["source", "target", "matching", "validation", "output"])
@pytest.mark.parametrize(
    "bad",
    ["[]", "[a, b]", "text", "42", "1.5", "true", "''", "0", "false"],
)
def test_non_mapping_sections_raise_value_error_naming_the_section(tmp_path, section, bad):
    base = {
        "source": "{type: csv, path: s.csv}",
        "target": "{type: csv, path: t.csv}",
        "matching": "{keys: [id]}",
        "validation": "{checks: [schema_validation]}",
        "output": "{directory: out}",
    }
    base[section] = bad
    text = "name: n\n" + "".join(f"{k}: {v}\n" for k, v in base.items())

    with pytest.raises(ValueError, match=rf"^{section} must be a mapping"):
        load_config(_write_raw(tmp_path, text))


@pytest.mark.parametrize(
    ("section", "pattern"),
    [
        ("source", r"source\.type is required"),
        ("target", r"target\.type is required"),
        ("matching", r"matching\.keys is required"),
        ("validation", r"validation\.checks is required"),
        ("output", r"output\.directory is required"),
    ],
)
def test_empty_mapping_sections_are_reported_as_missing_fields(tmp_path, section, pattern):
    base = {
        "source": "{type: csv, path: s.csv}",
        "target": "{type: csv, path: t.csv}",
        "matching": "{keys: [id]}",
        "validation": "{checks: [schema_validation]}",
        "output": "{directory: out}",
    }
    base[section] = "{}"
    text = "name: n\n" + "".join(f"{k}: {v}\n" for k, v in base.items())

    with pytest.raises(ValueError, match=pattern):
        load_config(_write_raw(tmp_path, text))


@pytest.mark.parametrize("section", ["source", "target", "matching", "validation", "output"])
def test_null_or_absent_section_is_required_error(tmp_path, section):
    base = {
        "source": "{type: csv, path: s.csv}",
        "target": "{type: csv, path: t.csv}",
        "matching": "{keys: [id]}",
        "validation": "{checks: [schema_validation]}",
        "output": "{directory: out}",
    }
    absent = dict(base)
    del absent[section]
    nulled = dict(base)
    nulled[section] = "~"
    for variant in (absent, nulled):
        text = "name: n\n" + "".join(f"{k}: {v}\n" for k, v in variant.items())
        with pytest.raises(ValueError, match=rf"^{section} is required"):
            load_config(_write_raw(tmp_path, text))


@pytest.mark.parametrize(
    ("extra_line", "expected_list"),
    [
        ("1: x", "['1']"),
        ("true: x", "['True']"),
        ("~: x", "['None']"),
        ("1.5: x", "['1.5']"),
        ("2026-11-06: x", "['2026-11-06']"),
        ("? [a, b]\n: x", None),  # tuple key (complex key): must not crash in an odd way
    ],
)
def test_non_string_root_keys_are_reported_as_unknown_without_crashing(tmp_path, extra_line, expected_list):
    text = TEMPLATE
    for placeholder, value in DEFAULTS.items():
        text = text.replace(f": {placeholder}\n", f": {value}\n")
    path = _write_raw(tmp_path, text + extra_line + "\n")

    if expected_list is None:
        # Complex keys are unhashable lists; PyYAML itself refuses to build them.
        # YAML syntax/constructor errors are not ValueError yet (see IR-019).
        with pytest.raises(Exception) as excinfo:
            load_config(path)
        assert not isinstance(excinfo.value, (AttributeError, KeyError, UnboundLocalError))
        return

    with pytest.raises(ValueError, match=r"unknown configuration key\(s\) in scenario") as excinfo:
        load_config(path)
    assert expected_list in str(excinfo.value)


def test_mixed_key_types_are_sorted_and_reported_deterministically(tmp_path):
    text = TEMPLATE
    for placeholder, value in DEFAULTS.items():
        text = text.replace(f": {placeholder}\n", f": {value}\n")
    path = _write_raw(tmp_path, text + "zeta: 1\n2: x\nalpha: 3\n~: 4\n")

    with pytest.raises(ValueError) as excinfo:
        load_config(path)

    assert str(excinfo.value).endswith("in scenario: ['2', 'None', 'alpha', 'zeta']")


@pytest.mark.parametrize("section", ["source", "target", "matching", "validation", "output"])
def test_non_string_section_keys_are_reported_as_unknown(tmp_path, section):
    base = {
        "source": "{type: csv, path: s.csv}",
        "target": "{type: csv, path: t.csv}",
        "matching": "{keys: [id]}",
        "validation": "{checks: [schema_validation]}",
        "output": "{directory: out}",
    }
    base[section] = base[section][:-1] + ", 1: x, ~: y}"
    text = "name: n\n" + "".join(f"{k}: {v}\n" for k, v in base.items())

    with pytest.raises(ValueError) as excinfo:
        load_config(_write_raw(tmp_path, text))

    assert str(excinfo.value) == f"unknown configuration key(s) in {section}: ['1', 'None']"


def test_unknown_key_case_variants_are_rejected_not_matched_loosely(tmp_path):
    text = TEMPLATE
    for placeholder, value in DEFAULTS.items():
        text = text.replace(f": {placeholder}\n", f": {value}\n")
    path = _write_raw(tmp_path, text.replace("name:", "Name:", 1))

    with pytest.raises(ValueError, match=r"unknown configuration key\(s\) in scenario: \['Name'\]"):
        load_config(path)


def test_duplicate_yaml_keys_never_crash_and_never_coerce(tmp_path):
    # Documents CURRENT behaviour (see IR-019). PyYAML silently keeps the last duplicate.
    # Whatever the contract later decides,
    # the outcome must be either a ValueError or the last value, unchanged.
    text = TEMPLATE
    for placeholder, value in DEFAULTS.items():
        text = text.replace(f": {placeholder}\n", f": {value}\n")
    path = _write_raw(tmp_path, "name: first\n" + text)

    try:
        scenario = load_config(path)
    except ValueError:
        return
    assert scenario.name == "edge_scenario"


def test_duplicate_yaml_key_with_bad_last_value_is_rejected(tmp_path):
    text = TEMPLATE
    for placeholder, value in DEFAULTS.items():
        text = text.replace(f": {placeholder}\n", f": {value}\n")
    path = _write_raw(tmp_path, text + "name: 123\n")

    with pytest.raises(ValueError, match=r"scenario\.name must be a string, got int"):
        load_config(path)


# --------------------------------------------------------------------------
# Mutation after load, then run
# --------------------------------------------------------------------------

def test_frozen_dataclasses_refuse_normal_assignment(tmp_path):
    scenario = load_config(_write(tmp_path))

    with pytest.raises(dataclasses.FrozenInstanceError):
        scenario.name = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        scenario.matching.keys = ("id",)


@pytest.mark.parametrize(
    ("mutate", "pattern"),
    [
        (lambda s: s.matching.keys.append(1), r"matching\.keys\[1\] must be a string, got int"),
        (lambda s: s.matching.keys.append(None), r"matching\.keys\[1\] must be a string, got NoneType"),
        (lambda s: s.matching.keys.append(""), r"matching\.keys\[1\] must not be blank"),
        (lambda s: s.matching.keys.__setitem__(0, ["id"]), r"matching\.keys\[0\] must be a string, got list"),
        (lambda s: s.matching.keys.clear(), r"matching\.keys must contain at least one"),
        (lambda s: s.validation.checks.append(7), r"validation\.checks\[1\] must be a string, got int"),
        (lambda s: s.validation.checks.append("bogus"), r"unsupported validation checks: \['bogus'\]"),
        (lambda s: s.validation.checks.clear(), r"validation\.checks must contain at least one"),
    ],
)
def test_in_place_list_mutation_after_load_is_caught_by_run_scenario(tmp_path, mutate, pattern):
    scenario = load_config(_write(tmp_path))
    mutate(scenario)

    with pytest.raises(ValueError, match=pattern):
        run_scenario(scenario)


@pytest.mark.parametrize(
    ("target_attr", "section_attr", "new_value", "pattern"),
    [
        ("matching", "keys", ("id",), r"matching\.keys must be a list, got tuple"),
        ("validation", "checks", ("schema_validation",), r"validation\.checks must be a list, got tuple"),
        ("matching", "keys", "id", r"matching\.keys must be a list, got str"),
        ("validation", "checks", {"schema_validation"}, r"validation\.checks must be a list, got set"),
        ("source", "path", 5, r"source\.path must be a string, got int"),
        ("target", "type", None, r"target\.type is required"),
        ("output", "directory", ["out"], r"output\.directory must be a string, got list"),
    ],
)
def test_replacing_fields_with_wrong_types_after_load_is_caught_by_run_scenario(
    tmp_path, target_attr, section_attr, new_value, pattern
):
    scenario = load_config(_write(tmp_path))
    object.__setattr__(getattr(scenario, target_attr), section_attr, new_value)

    with pytest.raises(ValueError, match=pattern):
        run_scenario(scenario)


@pytest.mark.parametrize(
    ("attr", "value", "pattern"),
    [
        ("name", 5, r"scenario\.name must be a string, got int"),
        ("version", 1.5, r"scenario\.version must be an integer or a string, got float"),
        ("version", True, r"scenario\.version must be an integer or a string, got bool"),
    ],
)
def test_replacing_scenario_scalars_after_load_is_caught_by_run_scenario(tmp_path, attr, value, pattern):
    scenario = load_config(_write(tmp_path))
    object.__setattr__(scenario, attr, value)

    with pytest.raises(ValueError, match=pattern):
        run_scenario(scenario)


@pytest.mark.parametrize("bad", [None, {}, "scenario", 5, [], ("a",), object()])
def test_non_scenario_argument_raises_type_error_in_validator_and_runner(bad):
    with pytest.raises(TypeError, match=r"scenario must be a Scenario instance"):
        validate_scenario(bad)
    with pytest.raises(TypeError, match=r"scenario must be a Scenario instance"):
        run_scenario(bad)


def test_scenario_subclass_is_still_validated():
    @dataclasses.dataclass(frozen=True)
    class Derived(Scenario):
        pass

    scenario = Derived(
        name=5,
        version=None,
        source=DataSource("csv", "s.csv"),
        target=DataSource("csv", "t.csv"),
        matching=MatchingConfig(["id"]),
        validation=ValidationConfig(["schema_validation"]),
        output=OutputConfig("out"),
    )

    with pytest.raises(ValueError, match=r"scenario\.name must be a string, got int"):
        validate_scenario(scenario)


def test_section_subclass_objects_of_wrong_kind_are_rejected():
    scenario = _valid_scenario()
    object.__setattr__(scenario, "source", OutputConfig(directory="x"))

    with pytest.raises(ValueError, match=r"source must be a DataSource, got OutputConfig"):
        validate_scenario(scenario)


# --------------------------------------------------------------------------
# Fail before data access / existing behaviour retained
# --------------------------------------------------------------------------

def test_config_error_wins_over_missing_files(tmp_path):
    scenario = _valid_scenario(
        source=DataSource("csv", str(tmp_path / "absent-source.csv")),
        target=DataSource("csv", str(tmp_path / "absent-target.csv")),
        matching=MatchingConfig(keys=("id",)),
    )

    with pytest.raises(ValueError, match=r"matching\.keys must be a list, got tuple"):
        run_scenario(scenario)


def test_config_error_does_not_open_data_files(tmp_path, monkeypatch):
    import edmvf.validation.schema as schema_module

    reads = []

    def spy(path):
        reads.append(str(path))
        raise AssertionError("data was read although the configuration is invalid")

    src = tmp_path / "s.csv"
    src.write_text("id\n1\n", encoding="utf-8")
    scenario = _valid_scenario(
        source=DataSource("csv", str(src)),
        target=DataSource("csv", str(src)),
        output=OutputConfig(directory=5),
    )
    monkeypatch.setattr(schema_module, "read_csv_dataset", spy)

    with pytest.raises(ValueError, match=r"output\.directory must be a string, got int"):
        run_scenario(scenario)

    assert reads == []


def test_missing_files_with_valid_config_are_file_access_issues_not_config_errors(tmp_path):
    scenario = _valid_scenario(
        source=DataSource("csv", str(tmp_path / "absent-source.csv")),
        target=DataSource("csv", str(tmp_path / "absent-target.csv")),
        validation=ValidationConfig(checks=["structural_validation"]),
    )

    result = run_scenario(scenario)

    assert result["status"] == "failed"
    issues = result["structural_validation"]["issues"]
    assert sorted((i["kind"], i["dataset"]) for i in issues) == [
        ("file_access", "source"),
        ("file_access", "target"),
    ]


def test_missing_source_with_schema_validation_reports_only_source_file_access_until_ir009(tmp_path):
    # Documents CURRENT behaviour (schema_validation stops at the first unreadable
    # file). IR-009 will change this to report both sides.
    scenario = _valid_scenario(
        source=DataSource("csv", str(tmp_path / "absent-source.csv")),
        target=DataSource("csv", str(tmp_path / "absent-target.csv")),
    )

    result = run_scenario(scenario)

    assert result["status"] == "failed"
    issues = result["schema_validation"]["issues"]
    assert [(i["kind"], i["dataset"]) for i in issues] == [("file_access", "source")]


def test_missing_target_only_is_a_target_file_access_issue(tmp_path):
    src = tmp_path / "s.csv"
    src.write_text("id,x\n1,2\n", encoding="utf-8")
    scenario = _valid_scenario(
        source=DataSource("csv", str(src)),
        target=DataSource("csv", str(tmp_path / "absent-target.csv")),
    )

    result = run_scenario(scenario)

    assert result["status"] == "failed"
    issues = result["schema_validation"]["issues"]
    assert [(i["kind"], i["dataset"]) for i in issues] == [("file_access", "target")]


def test_clean_scenario_still_passes_end_to_end(tmp_path):
    src = tmp_path / "s.csv"
    tgt = tmp_path / "t.csv"
    src.write_text("id,x\n1,2\n", encoding="utf-8")
    tgt.write_text("x,id\n2,1\n", encoding="utf-8")
    scenario = _valid_scenario(
        source=DataSource("csv", str(src)),
        target=DataSource("csv", str(tgt)),
        validation=ValidationConfig(checks=["schema_validation", "structural_validation"]),
    )

    result = run_scenario(scenario)

    assert result["status"] == "ready"
    assert result["matching_keys"] == ["id"]
    assert result["schema_validation"]["passed"] is True
    assert result["schema_validation"]["issues"] == []


def test_load_config_missing_file_still_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"configuration file not found"):
        load_config(tmp_path / "nope.yaml")


# --------------------------------------------------------------------------
# Deterministic error ordering (first error wins)
# --------------------------------------------------------------------------

def _bad_all():
    return dict(
        name=1,
        version=1.5,
        source=DataSource(type=1, path=1),
        target=DataSource(type=1, path=1),
        matching=MatchingConfig(keys="id"),
        validation=ValidationConfig(checks="x"),
        output=OutputConfig(directory=1),
    )


# Each step fixes the first remaining error; the next one must surface.
ORDER = [
    (r"scenario\.name must be a string", {"name": "n"}),
    (r"scenario\.version must be an integer or a string", {"version": 1}),
    (r"source\.type must be a string", {"source": DataSource("csv", 1)}),
    (r"source\.path must be a string", {"source": DataSource("csv", "s.csv")}),
    (r"target\.type must be a string", {"target": DataSource("csv", 1)}),
    (r"target\.path must be a string", {"target": DataSource("csv", "t.csv")}),
    (r"matching\.keys must be a list", {"matching": MatchingConfig(["id"])}),
    (r"validation\.checks must be a list", {"validation": ValidationConfig(["schema_validation"])}),
    (r"output\.directory must be a string", {"output": OutputConfig("out")}),
]


def test_first_error_wins_in_fixed_field_order():
    fields = _bad_all()
    for pattern, fix in ORDER:
        scenario = Scenario(**fields)
        with pytest.raises(ValueError, match=pattern):
            validate_scenario(scenario)
        # repeat: same input, same error
        with pytest.raises(ValueError, match=pattern):
            validate_scenario(scenario)
        fields.update(fix)

    assert validate_scenario(Scenario(**fields)).name == "n"


def test_bad_name_and_bad_path_reports_name(tmp_path):
    scenario = _valid_scenario(name=5, source=DataSource("csv", 5))

    with pytest.raises(ValueError) as excinfo:
        validate_scenario(scenario)

    assert str(excinfo.value) == "scenario.name must be a string, got int"


def test_unsupported_source_type_is_reported_before_bad_source_path():
    scenario = _valid_scenario(source=DataSource("parquet", 5))

    with pytest.raises(ValueError, match=r"unsupported source type: parquet"):
        validate_scenario(scenario)


def test_unsupported_source_type_is_reported_before_target_problems():
    scenario = _valid_scenario(
        source=DataSource("parquet", "s"), target=DataSource(5, 5)
    )

    with pytest.raises(ValueError, match=r"unsupported source type: parquet"):
        validate_scenario(scenario)


def test_yaml_shape_errors_precede_type_errors(tmp_path):
    # name is wrong-typed AND source is not a mapping: shape (from_mapping) wins.
    text = (
        "name: 123\n"
        "source: nope\n"
        "target: {type: csv, path: t.csv}\n"
        "matching: {keys: [id]}\n"
        "validation: {checks: [schema_validation]}\n"
        "output: {directory: out}\n"
    )

    with pytest.raises(ValueError, match=r"^source must be a mapping"):
        load_config(_write_raw(tmp_path, text))


def test_unknown_root_key_precedes_missing_section(tmp_path):
    with pytest.raises(ValueError, match=r"unknown configuration key\(s\) in scenario: \['extra'\]"):
        load_config(_write_raw(tmp_path, "extra: 1\nname: n\n"))


def test_missing_sections_are_reported_in_fixed_order(tmp_path):
    with pytest.raises(ValueError, match=r"^source is required"):
        load_config(_write_raw(tmp_path, "name: n\n"))
    with pytest.raises(ValueError, match=r"^target is required"):
        load_config(_write_raw(tmp_path, "name: n\nsource: {type: csv, path: s}\n"))


# --------------------------------------------------------------------------
# Message hygiene
# --------------------------------------------------------------------------

SECRET = "TOP-SECRET-TOKEN-12345"


@pytest.mark.parametrize(
    "scenario_factory",
    [
        lambda: _valid_scenario(name=[SECRET]),
        lambda: _valid_scenario(name={"k": SECRET}),
        lambda: _valid_scenario(version=[SECRET]),
        lambda: _valid_scenario(version={SECRET: SECRET}),
        lambda: _valid_scenario(source=DataSource("csv", [SECRET])),
        lambda: _valid_scenario(source=DataSource([SECRET], "s")),
        lambda: _valid_scenario(target=DataSource("csv", {"p": SECRET})),
        lambda: _valid_scenario(matching=MatchingConfig(keys=[[SECRET]])),
        lambda: _valid_scenario(matching=MatchingConfig(keys={SECRET: 1})),
        lambda: _valid_scenario(matching=MatchingConfig(keys=(SECRET,))),
        lambda: _valid_scenario(matching=MatchingConfig(keys=[{"k": SECRET}])),
        lambda: _valid_scenario(validation=ValidationConfig(checks=[[SECRET]])),
        lambda: _valid_scenario(validation=ValidationConfig(checks={SECRET})),
        lambda: _valid_scenario(output=OutputConfig(directory=[SECRET])),
        lambda: _valid_scenario(source={"type": "csv", "path": SECRET}),
        lambda: _valid_scenario(matching={"keys": [SECRET]}),
    ],
)
def test_error_messages_never_echo_container_or_nested_values(scenario_factory):
    scenario = scenario_factory()

    for call in (validate_scenario, run_scenario):
        with pytest.raises(ValueError) as excinfo:
            call(scenario)
        assert SECRET not in str(excinfo.value)
        assert SECRET not in repr(excinfo.value)


@pytest.mark.parametrize(
    "fragment",
    [
        f"name: [{SECRET}]",
        f"name: {{k: {SECRET}}}",
        f"version: [{SECRET}]",
        f"source: [{SECRET}]",
        f"source: {{type: csv, path: [{SECRET}]}}",
        f"matching: {{keys: [[{SECRET}]]}}",
        f"matching: {{keys: {{{SECRET}: 1}}}}",
        f"validation: {{checks: [{{k: {SECRET}}}]}}",
        f"output: [{SECRET}]",
    ],
)
def test_yaml_error_messages_never_echo_container_values(tmp_path, fragment):
    key = fragment.split(":", 1)[0]
    lines = [
        "name: n",
        "version: 1",
        "source: {type: csv, path: s.csv}",
        "target: {type: csv, path: t.csv}",
        "matching: {keys: [id]}",
        "validation: {checks: [schema_validation]}",
        "output: {directory: out}",
    ]
    lines = [fragment if line.startswith(key + ":") else line for line in lines]

    with pytest.raises(ValueError) as excinfo:
        load_config(_write_raw(tmp_path, "\n".join(lines) + "\n"))

    assert SECRET not in str(excinfo.value)


def test_scalar_string_error_messages_do_not_expose_unrelated_fields(tmp_path):
    scenario = _valid_scenario(
        source=DataSource("csv", SECRET),
        matching=MatchingConfig(keys=[1]),
    )

    with pytest.raises(ValueError) as excinfo:
        validate_scenario(scenario)

    assert SECRET not in str(excinfo.value)


def test_validate_scenario_returns_the_same_object_and_does_not_mutate():
    scenario = _valid_scenario(matching=MatchingConfig(keys=["id ", "b"]))
    before = copy.deepcopy(scenario)

    returned = validate_scenario(scenario)

    assert returned is scenario
    assert scenario == before
