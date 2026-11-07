"""IR-006 adversarial tests: check-selection semantics (config and lower-level validators)."""

import re
from pathlib import Path

import pytest

from edmvf.config.loader import load_config
from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.config.validator import validate_scenario
from edmvf.runner import run_scenario
from edmvf.validation.schema import validate_csv_dataset, validate_csv_scenario, validate_csv_schema

CLEAN = "id,name\n1,Alpha\n"
WIDTH_BAD = "id,name\n1,Alpha,extra\n"
SECRET = "SECRETCONTENT-9731"

ALL_CHECKS = ["schema_validation", "structural_validation", "missing_records", "duplicate_records", "mismatched_values"]


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8", newline="")
    return path


def _scenario(source, target, checks, keys=("id",)) -> Scenario:
    return Scenario(
        name="ir006_sel",
        version=1,
        source=DataSource("csv", str(source)),
        target=DataSource("csv", str(target)),
        matching=MatchingConfig(keys=list(keys)),
        validation=ValidationConfig(checks=checks),
        output=OutputConfig(directory="./out"),
    )


@pytest.fixture
def spy_reads(monkeypatch):
    """Fail the test if any CSV is read."""
    calls = []

    def _spy(path):
        calls.append(path)
        raise AssertionError(f"read_csv_dataset must not be called (path={path})")

    monkeypatch.setattr("edmvf.validation.schema.read_csv_dataset", _spy)
    return calls


@pytest.fixture
def files(tmp_path):
    return _write(tmp_path, "s.csv", CLEAN + SECRET + "\n"), _write(tmp_path, "t.csv", CLEAN)


# --------------------------------------------------------------------------
# validate_scenario: duplicates, ordering of checks, case/whitespace
# --------------------------------------------------------------------------

def _validate_checks(checks):
    return validate_scenario(_scenario("s.csv", "t.csv", checks))


def test_duplicate_checks_rejected_with_first_seen_order_and_each_name_once():
    checks = ["schema_validation", "structural_validation", "schema_validation", "structural_validation", "schema_validation"]
    with pytest.raises(ValueError) as info:
        _validate_checks(checks)
    assert str(info.value) == (
        "validation.checks contains duplicate entries: ['schema_validation', 'structural_validation']"
    )


def test_duplicate_listing_uses_first_seen_order_not_sorted_order():
    checks = ["structural_validation", "schema_validation", "schema_validation", "structural_validation"]
    with pytest.raises(ValueError) as info:
        _validate_checks(checks)
    assert str(info.value) == (
        "validation.checks contains duplicate entries: ['structural_validation', 'schema_validation']"
    )


@pytest.mark.parametrize("name", ["missing_records", "duplicate_records", "mismatched_values"])
def test_duplicate_unimplemented_check_is_still_a_duplicate(name):
    with pytest.raises(ValueError) as info:
        _validate_checks([name, name])
    assert str(info.value) == f"validation.checks contains duplicate entries: ['{name}']"


def test_unsupported_check_wins_over_duplicate_check():
    with pytest.raises(ValueError) as info:
        _validate_checks(["bogus", "bogus", "schema_validation", "schema_validation"])
    assert str(info.value) == "unsupported validation checks: ['bogus', 'bogus']"


def test_unsupported_mixed_with_duplicated_supported_reports_unsupported_only():
    with pytest.raises(ValueError) as info:
        _validate_checks(["schema_validation", "schema_validation", "missing_record"])
    assert str(info.value) == "unsupported validation checks: ['missing_record']"


@pytest.mark.parametrize(
    "variant",
    ["Schema_Validation", "schema_validation ", " schema_validation", "SCHEMA_VALIDATION", "schema_validation\n", "schema-validation"],
)
def test_case_or_whitespace_variants_are_unsupported_not_duplicates(variant):
    with pytest.raises(ValueError) as info:
        _validate_checks(["schema_validation", variant])
    assert str(info.value) == f"unsupported validation checks: {[variant]}"


def test_variant_pair_without_exact_name_is_unsupported_for_both():
    with pytest.raises(ValueError) as info:
        _validate_checks(["Schema_Validation", "schema_validation "])
    assert str(info.value) == "unsupported validation checks: ['Schema_Validation', 'schema_validation ']"


def test_all_five_distinct_checks_are_accepted_in_any_order():
    for ordering in (ALL_CHECKS, list(reversed(ALL_CHECKS)), ALL_CHECKS[2:] + ALL_CHECKS[:2]):
        assert _validate_checks(list(ordering)).validation.checks == ordering


def test_duplicate_matching_keys_are_not_rejected_ir017(tmp_path):
    source = _write(tmp_path, "s.csv", CLEAN)
    target = _write(tmp_path, "t.csv", CLEAN)
    scenario = _scenario(source, target, ["schema_validation"], keys=("id", "id"))

    result = run_scenario(scenario)

    assert result["status"] == "passed"
    assert result["matching_keys"] == ["id", "id"]


def test_very_long_duplicate_list_reports_each_name_once():
    with pytest.raises(ValueError) as info:
        _validate_checks(["schema_validation"] * 20000)
    assert str(info.value) == "validation.checks contains duplicate entries: ['schema_validation']"


def test_very_long_unsupported_list_is_rejected_cleanly():
    checks = [f"check_{index}" for index in range(3000)]
    with pytest.raises(ValueError, match="unsupported validation checks"):
        _validate_checks(checks)


def test_very_long_valid_looking_list_with_one_late_duplicate():
    checks = ["schema_validation", "structural_validation"] + ["missing_records"] + ["duplicate_records"]
    checks = checks + ["mismatched_values"] + ["structural_validation"]
    with pytest.raises(ValueError) as info:
        _validate_checks(checks)
    assert str(info.value) == "validation.checks contains duplicate entries: ['structural_validation']"


# --------------------------------------------------------------------------
# Wrong container / element types on a directly built Scenario
# --------------------------------------------------------------------------

def _generator():
    yield "schema_validation"


@pytest.mark.parametrize(
    "bad_checks",
    [
        {"schema_validation"},
        frozenset({"schema_validation"}),
        ("schema_validation",),
        _generator(),
        {"schema_validation": 1},
        "schema_validation",
        b"schema_validation",
        42,
    ],
    ids=["set", "frozenset", "tuple", "generator", "dict", "str", "bytes", "int"],
)
def test_scenario_checks_container_must_be_a_list(tmp_path, spy_reads, bad_checks):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, bad_checks)

    with pytest.raises(ValueError, match=r"validation\.checks must be a list"):
        run_scenario(scenario)

    assert spy_reads == []


@pytest.mark.parametrize(
    "bad_checks, expected",
    [
        ([None], r"validation\.checks\[0\] must be a string, got NoneType"),
        (["schema_validation", None], r"validation\.checks\[1\] must be a string, got NoneType"),
        ([["schema_validation"]], r"validation\.checks\[0\] must be a string, got list"),
        ([b"schema_validation"], r"validation\.checks\[0\] must be a string, got bytes"),
        ([1], r"validation\.checks\[0\] must be a string, got int"),
        ([True], r"validation\.checks\[0\] must be a string, got bool"),
        ([""], r"validation\.checks\[0\] must not be blank"),
        (["schema_validation", "   "], r"validation\.checks\[1\] must not be blank"),
    ],
)
def test_scenario_check_element_must_be_a_nonblank_string(tmp_path, spy_reads, bad_checks, expected):
    source = _write(tmp_path, "s.csv", CLEAN)

    with pytest.raises(ValueError, match=expected):
        run_scenario(_scenario(source, source, bad_checks))

    assert spy_reads == []


def test_scenario_checks_none_and_empty_are_rejected(tmp_path, spy_reads):
    source = _write(tmp_path, "s.csv", CLEAN)
    with pytest.raises(ValueError, match=r"validation\.checks is required"):
        run_scenario(_scenario(source, source, None))
    with pytest.raises(ValueError, match="at least one validation check"):
        run_scenario(_scenario(source, source, []))
    assert spy_reads == []


# --------------------------------------------------------------------------
# run_scenario boundary: mutation after load, config wins over files
# --------------------------------------------------------------------------

def test_run_scenario_rejects_non_scenario_before_anything_else(spy_reads):
    for bad in (None, {}, "scenario", 3):
        with pytest.raises(TypeError, match="Scenario instance"):
            run_scenario(bad)
    assert spy_reads == []


@pytest.mark.parametrize(
    "appended, message",
    [
        ("schema_validation", "validation.checks contains duplicate entries: ['schema_validation']"),
        ("bogus_check", "unsupported validation checks: ['bogus_check']"),
        ("Schema_Validation", "unsupported validation checks: ['Schema_Validation']"),
    ],
)
def test_checks_mutated_after_construction_are_revalidated_by_run_scenario(tmp_path, spy_reads, appended, message):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, ["schema_validation"])
    scenario.validation.checks.append(appended)

    with pytest.raises(ValueError) as info:
        run_scenario(scenario)

    assert str(info.value) == message
    assert spy_reads == []


def test_checks_mutated_after_load_config_are_revalidated(tmp_path, spy_reads):
    config = _yaml(tmp_path, ["schema_validation", "structural_validation"], source="s.csv", target="t.csv")
    scenario = load_config(config)
    scenario.validation.checks.append("structural_validation")

    with pytest.raises(ValueError, match=re.escape("['structural_validation']")):
        run_scenario(scenario)
    assert spy_reads == []


def test_config_error_wins_over_missing_files(tmp_path, spy_reads):
    scenario = _scenario(tmp_path / "no-s.csv", tmp_path / "no-t.csv", ["schema_validation", "schema_validation"])

    with pytest.raises(ValueError, match="duplicate entries"):
        run_scenario(scenario)
    assert spy_reads == []


def test_result_validation_checks_is_a_copy_of_the_configured_list(tmp_path):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, ["schema_validation", "missing_records"])

    result = run_scenario(scenario)
    result["validation_checks"].append("tampered")
    scenario.validation.checks.append("missing_records")

    assert scenario.validation.checks == ["schema_validation", "missing_records", "missing_records"]
    assert result["validation_checks"] == ["schema_validation", "missing_records", "tampered"]


def test_result_snapshot_does_not_follow_later_scenario_mutation(tmp_path):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, ["schema_validation"])

    result = run_scenario(scenario)
    scenario.validation.checks.append("missing_records")

    assert result["validation_checks"] == ["schema_validation"]
    assert "missing_records" not in result


# --------------------------------------------------------------------------
# YAML path
# --------------------------------------------------------------------------

def _yaml(tmp_path, checks, source="s.csv", target="t.csv", raw_checks=None):
    if raw_checks is None:
        raw_checks = "\n".join(f"    - {item}" for item in checks)
    path = tmp_path / "scenario.yaml"
    path.write_text(
        "name: ir006_yaml\n"
        "version: 1\n"
        f"source:\n  type: csv\n  path: {source}\n"
        f"target:\n  type: csv\n  path: {target}\n"
        "matching:\n  keys:\n    - id\n"
        f"validation:\n  checks:\n{raw_checks}\n"
        "output:\n  directory: ./out\n",
        encoding="utf-8",
    )
    return path


def test_yaml_duplicate_checks_rejected_by_load_config(tmp_path):
    path = _yaml(tmp_path, ["schema_validation", "structural_validation", "schema_validation"])

    with pytest.raises(ValueError) as info:
        load_config(path)

    assert str(info.value) == "validation.checks contains duplicate entries: ['schema_validation']"


def test_yaml_quoted_and_unquoted_same_name_are_duplicates(tmp_path):
    path = _yaml(tmp_path, ["schema_validation", '"schema_validation"', "'schema_validation'"])

    with pytest.raises(ValueError, match=re.escape("duplicate entries: ['schema_validation']")):
        load_config(path)


def test_yaml_quoted_and_unquoted_valid_names_load_identically(tmp_path):
    plain = load_config(_yaml(tmp_path, ["schema_validation", "structural_validation"]))
    quoted = load_config(_yaml(tmp_path, ['"schema_validation"', "'structural_validation'"]))

    assert plain.validation.checks == quoted.validation.checks == ["schema_validation", "structural_validation"]


@pytest.mark.parametrize("scalar, type_name", [("yes", "bool"), ("true", "bool"), ("null", "NoneType"), ("123", "int"), ("1.5", "float")])
def test_yaml_unquoted_scalar_check_names_are_typed_and_rejected(tmp_path, scalar, type_name):
    path = _yaml(tmp_path, ["schema_validation", scalar])

    with pytest.raises(ValueError, match=rf"validation\.checks\[1\] must be a string, got {type_name}"):
        load_config(path)


@pytest.mark.parametrize("scalar", ["yes", "true", "null", "123"])
def test_yaml_quoted_scalar_check_names_are_strings_but_unsupported(tmp_path, scalar):
    path = _yaml(tmp_path, ["schema_validation", f'"{scalar}"'])

    with pytest.raises(ValueError) as info:
        load_config(path)

    assert str(info.value) == f"unsupported validation checks: ['{scalar}']"


def test_yaml_checks_as_scalar_or_mapping_is_rejected(tmp_path):
    scalar = tmp_path / "a.yaml"
    mapping = tmp_path / "b.yaml"
    body = (
        "name: n\nversion: 1\nsource: {type: csv, path: s.csv}\ntarget: {type: csv, path: t.csv}\n"
        "matching: {keys: [id]}\noutput: {directory: ./out}\n"
    )
    scalar.write_text(body + "validation: {checks: schema_validation}\n", encoding="utf-8")
    mapping.write_text(body + "validation: {checks: {schema_validation: true}}\n", encoding="utf-8")

    for path in (scalar, mapping):
        with pytest.raises(ValueError, match=r"validation\.checks must be a list, got (str|dict)"):
            load_config(path)


def test_yaml_config_error_wins_over_missing_data_files(tmp_path, spy_reads):
    path = _yaml(tmp_path, ["schema_validation", "schema_validation"], source="absent_s.csv", target="absent_t.csv")

    with pytest.raises(ValueError, match="duplicate entries"):
        load_config(path)
    assert spy_reads == []


# --------------------------------------------------------------------------
# validate_csv_dataset
# --------------------------------------------------------------------------

def _generator_checks():
    yield "schema_validation"


BAD_SELECTIONS = [
    pytest.param([], id="empty_list"),
    pytest.param(set(), id="empty_set"),
    pytest.param(frozenset(), id="empty_frozenset"),
    pytest.param("schema_validation", id="str"),
    pytest.param(b"schema_validation", id="bytes"),
    pytest.param((), id="empty_tuple"),
    pytest.param({"schema_validation": True}, id="dict"),
    pytest.param(_generator_checks(), id="generator"),
    pytest.param([None], id="none_item"),
    pytest.param(["schema_validation", None], id="none_among_valid"),
    pytest.param([["schema_validation"]], id="nested_list"),
    pytest.param([b"schema_validation"], id="bytes_item"),
    pytest.param([1], id="int_item"),
    pytest.param(["Schema_Validation"], id="wrong_case"),
    pytest.param(["schema_validation "], id="trailing_space"),
    pytest.param(["missing_records"], id="unimplemented_name"),
    pytest.param(["bogus"], id="unknown_name"),
    pytest.param(["schema_validation", "schema_validation"], id="duplicate"),
    pytest.param(["structural_validation"] * 500, id="long_duplicate"),
    pytest.param(7, id="int_container"),
]


@pytest.mark.parametrize("selection", BAD_SELECTIONS)
def test_validate_csv_dataset_rejects_bad_selection_before_reading(files, spy_reads, selection):
    source, _ = files

    with pytest.raises(ValueError, match="validate_csv_dataset"):
        validate_csv_dataset(source, "source", selection)

    assert spy_reads == []


@pytest.mark.parametrize("selection", BAD_SELECTIONS)
def test_validate_csv_dataset_bad_selection_wins_over_missing_file(tmp_path, spy_reads, selection):
    with pytest.raises(ValueError, match="validate_csv_dataset"):
        validate_csv_dataset(tmp_path / "absent.csv", "source", selection)
    assert spy_reads == []


@pytest.mark.parametrize("selection", BAD_SELECTIONS)
def test_exception_messages_never_echo_file_content(files, selection):
    source, _ = files
    for call in (
        lambda: validate_csv_dataset(source, "source", selection),
        lambda: validate_csv_schema(source, source, selection),
    ):
        with pytest.raises(ValueError) as excinfo:
            call()
        assert SECRET not in str(excinfo.value)


def test_validate_csv_dataset_default_none_runs_header_and_width(tmp_path):
    source = _write(tmp_path, "s.csv", WIDTH_BAD)

    result = validate_csv_dataset(source, "source", None)

    assert result.passed is False
    assert [(i.kind, i.dataset, i.row_number) for i in result.issues] == [("structural", "source", 2)]


def test_validate_csv_dataset_default_without_argument_equals_none(tmp_path):
    source = _write(tmp_path, "s.csv", WIDTH_BAD)
    assert validate_csv_dataset(source, "source").to_dict() == validate_csv_dataset(source, "source", None).to_dict()


def test_validate_csv_dataset_schema_only_ignores_row_width(tmp_path):
    source = _write(tmp_path, "s.csv", WIDTH_BAD)

    result = validate_csv_dataset(source, "source", ["schema_validation"])

    assert result.passed is True
    assert result.issues == []


def test_validate_csv_dataset_header_integrity_runs_for_either_single_check(tmp_path):
    source = _write(tmp_path, "s.csv", "id,id\n1,2\n")

    for selection in (["schema_validation"], ["structural_validation"], {"schema_validation", "structural_validation"}, frozenset(["structural_validation"])):
        result = validate_csv_dataset(source, "source", selection)
        assert [(i.kind, i.dataset, i.row_number, i.column_name) for i in result.issues] == [("schema", "source", 1, "id")]


def test_validate_csv_dataset_selection_not_mutated_and_later_mutation_irrelevant(tmp_path):
    source = _write(tmp_path, "s.csv", WIDTH_BAD)
    selection = ["structural_validation"]

    first = validate_csv_dataset(source, "source", selection)
    selection.clear()
    selection.append("schema_validation")
    second = validate_csv_dataset(source, "source", ["structural_validation"])

    assert first.to_dict() == second.to_dict()
    assert first.passed is False


def test_validate_csv_dataset_data_problems_are_issues_not_exceptions(tmp_path):
    missing = validate_csv_dataset(tmp_path / "absent.csv", "target", ["structural_validation"])
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"id,name\n\xff\xfe,x\n")
    utf8 = validate_csv_dataset(bad, "target", ["schema_validation"])

    assert [(i.kind, i.dataset) for i in missing.issues] == [("file_access", "target")]
    assert [(i.kind, i.dataset) for i in utf8.issues] == [("parse_error", "target")]
    assert missing.passed is False and utf8.passed is False


def test_validate_csv_dataset_same_path_twice_gives_identical_results(tmp_path):
    source = _write(tmp_path, "s.csv", WIDTH_BAD)
    one = validate_csv_dataset(source, "source", ["structural_validation"])
    two = validate_csv_dataset(source, "source", ["structural_validation"])
    assert one.to_dict() == two.to_dict()


# --------------------------------------------------------------------------
# validate_csv_schema
# --------------------------------------------------------------------------

@pytest.mark.parametrize("selection", BAD_SELECTIONS)
def test_validate_csv_schema_rejects_bad_selection_before_reading(files, spy_reads, selection):
    source, target = files

    with pytest.raises(ValueError, match="validate_csv_schema"):
        validate_csv_schema(source, target, selection)

    assert spy_reads == []


def test_validate_csv_schema_explicit_selection_must_include_schema_validation(files, spy_reads):
    source, target = files
    for selection in (["structural_validation"], {"structural_validation"}, frozenset({"structural_validation"})):
        with pytest.raises(ValueError, match="must include 'schema_validation'"):
            validate_csv_schema(source, target, selection)
    assert spy_reads == []


def test_validate_csv_schema_default_is_schema_only_and_ignores_width(tmp_path):
    # Same columns on both sides, a width defect on the source: only the column
    # comparison runs, so this passes; it must read both files to know that.
    source = _write(tmp_path, "s.csv", WIDTH_BAD)
    target = _write(tmp_path, "t.csv", CLEAN)
    drifted = _write(tmp_path, "d.csv", "id,email\n1,a\n")

    for selection in (None, ["schema_validation"], {"schema_validation"}):
        result = validate_csv_schema(source, target, selection)
        assert result.passed is True and result.issues == []
        assert result.dataset == "schema_comparison"
        assert validate_csv_schema(source, drifted, selection).passed is False


def test_validate_csv_schema_with_structural_included_reports_width_on_each_side(tmp_path):
    source = _write(tmp_path, "s.csv", WIDTH_BAD)
    target = _write(tmp_path, "t.csv", "id,name\n1\n2,b,c\n")

    result = validate_csv_schema(source, target, ["schema_validation", "structural_validation"])

    assert [(i.kind, i.dataset, i.row_number) for i in result.issues] == [
        ("structural", "source", 2),
        ("structural", "target", 2),
        ("structural", "target", 3),
    ]


def test_validate_csv_schema_same_path_both_sides(tmp_path):
    path = _write(tmp_path, "same.csv", CLEAN)
    clean = validate_csv_schema(path, path)
    assert clean.passed is True and clean.issues == []

    dup = _write(tmp_path, "dup.csv", "id,id\n1,2\n")
    result = validate_csv_schema(dup, dup)
    assert [(i.kind, i.dataset, i.row_number) for i in result.issues] == [("schema", "source", 1), ("schema", "target", 1)]


def test_validate_csv_schema_missing_files_report_both_sides_source_first(tmp_path):
    result = validate_csv_schema(tmp_path / "a.csv", tmp_path / "b.csv", ["schema_validation"])
    assert [(i.kind, i.dataset) for i in result.issues] == [("file_access", "source"), ("file_access", "target")]


def test_validate_csv_schema_column_drift_is_reported_on_target_with_exact_columns(tmp_path):
    source = _write(tmp_path, "s.csv", "id,name\n1,a\n")
    target = _write(tmp_path, "t.csv", "id,email\n1,a\n")

    result = validate_csv_schema(source, target)

    assert [(i.kind, i.dataset, i.message) for i in result.issues] == [
        ("schema", "target", "Missing columns in target: ['name']."),
        ("schema", "target", "Unexpected columns in target: ['email']."),
    ]


# --------------------------------------------------------------------------
# validate_csv_scenario
# --------------------------------------------------------------------------

def test_validate_csv_scenario_none_with_only_unimplemented_checks_runs_nothing(tmp_path, spy_reads):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, ["missing_records", "duplicate_records"])

    assert validate_csv_scenario(scenario) == {}
    assert validate_csv_scenario(scenario, None) == {}
    assert spy_reads == []


def test_validate_csv_scenario_none_uses_configured_implemented_checks_in_fixed_order(tmp_path):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, ["schema_validation", "missing_records", "structural_validation"])

    assert list(validate_csv_scenario(scenario)) == ["structural_validation", "schema_validation"]


@pytest.mark.parametrize("selection", BAD_SELECTIONS)
def test_validate_csv_scenario_rejects_bad_explicit_selection_before_reading(tmp_path, spy_reads, selection):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, ["schema_validation"])

    with pytest.raises(ValueError, match="validate_csv_scenario|duplicate|checks"):
        validate_csv_scenario(scenario, selection)

    assert spy_reads == []


@pytest.mark.parametrize("name", ["missing_records", "duplicate_records", "mismatched_values"])
def test_validate_csv_scenario_explicit_unimplemented_name_is_rejected(tmp_path, spy_reads, name):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, ["schema_validation", name])

    with pytest.raises(ValueError, match="validate_csv_scenario: unsupported checks"):
        validate_csv_scenario(scenario, [name])
    assert spy_reads == []


def test_validate_csv_scenario_empty_selection_never_falls_back_to_configured(tmp_path, spy_reads):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, ["schema_validation", "structural_validation"])

    for empty in ([], set(), frozenset()):
        with pytest.raises(ValueError, match="at least one check"):
            validate_csv_scenario(scenario, empty)
    assert spy_reads == []


def test_validate_csv_scenario_validates_scenario_before_selection_and_reads(tmp_path, spy_reads):
    scenario = _scenario(tmp_path / "x.csv", tmp_path / "y.csv", ["schema_validation", "schema_validation"])

    with pytest.raises(ValueError) as info:
        validate_csv_scenario(scenario, ["schema_validation"])
    assert str(info.value) == "validation.checks contains duplicate entries: ['schema_validation']"

    with pytest.raises(TypeError, match="Scenario instance"):
        validate_csv_scenario(None, ["schema_validation"])
    assert spy_reads == []


def test_validate_csv_scenario_explicit_subset_of_configured_runs_only_that(tmp_path):
    source = _write(tmp_path, "s.csv", WIDTH_BAD)
    scenario = _scenario(source, source, ["schema_validation", "structural_validation"])

    only_schema = validate_csv_scenario(scenario, ["schema_validation"])
    only_structural = validate_csv_scenario(scenario, {"structural_validation"})

    assert list(only_schema) == ["schema_validation"] and only_schema["schema_validation"].passed is True
    assert list(only_structural) == ["structural_validation"]
    assert [(i.kind, i.dataset, i.row_number) for i in only_structural["structural_validation"].issues] == [
        ("structural", "source", 2),
        ("structural", "target", 2),
    ]


def test_validate_csv_scenario_explicit_order_does_not_change_result_order(tmp_path):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, ["schema_validation", "structural_validation"])

    assert list(validate_csv_scenario(scenario, ["schema_validation", "structural_validation"])) == [
        "structural_validation",
        "schema_validation",
    ]


def test_validate_csv_scenario_selection_list_mutation_after_call_has_no_effect(tmp_path):
    source = _write(tmp_path, "s.csv", CLEAN)
    scenario = _scenario(source, source, ["schema_validation", "structural_validation"])
    selection = ["schema_validation"]

    results = validate_csv_scenario(scenario, selection)
    selection.append("structural_validation")

    assert list(results) == ["schema_validation"]
    assert selection == ["schema_validation", "structural_validation"]


def test_validate_csv_scenario_missing_files_are_issues_not_exceptions(tmp_path):
    scenario = _scenario(tmp_path / "x.csv", tmp_path / "y.csv", ["schema_validation", "structural_validation"])

    results = validate_csv_scenario(scenario)

    assert [(i.kind, i.dataset) for i in results["structural_validation"].issues] == [
        ("file_access", "source"),
        ("file_access", "target"),
    ]
    assert [(i.kind, i.dataset) for i in results["schema_validation"].issues] == [
        ("file_access", "source"),
        ("file_access", "target"),
    ]
