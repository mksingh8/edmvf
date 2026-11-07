"""IR-006 adversarial tests: run status / completeness contract across every check subset.

Expectations are derived from the stated rule and from a hand-written table of what each
file pair does to each executed check, never from the implementation.
"""

import itertools
import json
from pathlib import Path

import pytest

from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.runner import run_scenario

CHECKS = ["schema_validation", "structural_validation", "missing_records", "duplicate_records", "mismatched_values"]
EXECUTED_ORDER = ["structural_validation", "schema_validation"]
UNIMPLEMENTED = {"missing_records", "duplicate_records", "mismatched_values"}
DATASET_NAME = {"structural_validation": "structural_validation", "schema_validation": "schema_comparison"}

HEAD = ["scenario_name", "source_type", "target_type", "status", "complete", "matching_keys", "validation_checks"]
EXECUTED_KEYS = {"status", "dataset", "passed", "issues", "source_path", "target_path"}

CLEAN = b"id,name\n1,Alpha\n"
DRIFT = b"id,email\n1,a@example.com\n"
WIDTH_BAD = b"id,name\n1,Alpha,extra\n"
BAD_UTF8 = b"id,name\n1,\xff\xfe\n"

# pair -> (source bytes or None for absent, target bytes or None, {check: (passed, {(kind, dataset)})})
PAIRS = {
    "clean": (CLEAN, CLEAN, {"structural_validation": (True, set()), "schema_validation": (True, set())}),
    "schema_drift": (
        CLEAN,
        DRIFT,
        {"structural_validation": (True, set()), "schema_validation": (False, {("schema", "target")})},
    ),
    "width_defect": (
        CLEAN,
        WIDTH_BAD,
        {"structural_validation": (False, {("structural", "target")}), "schema_validation": (True, set())},
    ),
    "missing_source": (
        None,
        CLEAN,
        {
            "structural_validation": (False, {("file_access", "source")}),
            "schema_validation": (False, {("file_access", "source")}),
        },
    ),
    "missing_target": (
        CLEAN,
        None,
        {
            "structural_validation": (False, {("file_access", "target")}),
            "schema_validation": (False, {("file_access", "target")}),
        },
    ),
    "bad_utf8": (
        CLEAN,
        BAD_UTF8,
        {
            "structural_validation": (False, {("parse_error", "target")}),
            "schema_validation": (False, {("parse_error", "target")}),
        },
    ),
}


def _scenario(source: Path, target: Path, checks) -> Scenario:
    return Scenario(
        name="ir006_edge",
        version=1,
        source=DataSource("csv", str(source)),
        target=DataSource("csv", str(target)),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=checks),
        output=OutputConfig(directory="./out"),
    )


def _materialise(tmp_path: Path, pair: str) -> tuple[Path, Path]:
    source_bytes, target_bytes, _ = PAIRS[pair]
    source, target = tmp_path / "s.csv", tmp_path / "t.csv"
    if source_bytes is not None:
        source.write_bytes(source_bytes)
    if target_bytes is not None:
        target.write_bytes(target_bytes)
    return source, target


def _subsets():
    for size in range(1, len(CHECKS) + 1):
        for subset in itertools.combinations(CHECKS, size):
            yield subset
            if size > 1:
                yield tuple(reversed(subset))


SUBSETS = list(_subsets())


def _expected(pair: str, configured):
    executed = {name: PAIRS[pair][2][name] for name in configured if name in EXECUTED_ORDER}
    statuses = {name: ("passed" if ok else "failed") for name, (ok, _) in executed.items()}
    statuses.update({name: "not_implemented" for name in configured if name in UNIMPLEMENTED})
    if "failed" in statuses.values():
        overall = "failed"
    elif "not_implemented" in statuses.values():
        overall = "incomplete"
    else:
        overall = "passed"
    complete = all(value != "not_implemented" for value in statuses.values())
    entry_order = [n for n in EXECUTED_ORDER if n in configured] + [n for n in configured if n in UNIMPLEMENTED]
    return overall, complete, statuses, entry_order, executed


@pytest.mark.parametrize("pair", list(PAIRS))
@pytest.mark.parametrize("configured", SUBSETS, ids=["+".join(c[:3] for c in s) for s in SUBSETS])
def test_every_subset_and_order_against_every_pair(tmp_path, pair, configured):
    source, target = _materialise(tmp_path, pair)
    overall, complete, statuses, entry_order, executed = _expected(pair, configured)

    result = run_scenario(_scenario(source, target, list(configured)))

    assert result["status"] == overall
    assert result["complete"] is complete
    assert result["validation_checks"] == list(configured)
    assert list(result) == HEAD + entry_order
    for name, status in statuses.items():
        assert result[name]["status"] == status
    for name, (passed, kinds) in executed.items():
        entry = result[name]
        assert set(entry) == EXECUTED_KEYS
        assert entry["passed"] is passed
        assert entry["dataset"] == DATASET_NAME[name]
        assert entry["source_path"] == str(source)
        assert entry["target_path"] == str(target)
        assert {(issue["kind"], issue["dataset"]) for issue in entry["issues"]} == kinds
        assert (entry["issues"] == []) is passed
    for name in configured:
        if name in UNIMPLEMENTED:
            assert set(result[name]) == {"status", "message"}
            assert name in result[name]["message"]
    assert result["status"] != "ready"


@pytest.mark.parametrize("pair", list(PAIRS))
def test_all_five_checks_are_deterministic_and_json_round_trip(tmp_path, pair):
    source, target = _materialise(tmp_path, pair)
    scenario = _scenario(source, target, list(CHECKS))

    first = run_scenario(scenario)
    second = run_scenario(scenario)
    third = run_scenario(_scenario(source, target, list(CHECKS)))

    assert json.dumps(first) == json.dumps(second) == json.dumps(third)
    assert json.loads(json.dumps(first)) == first
    assert list(json.loads(json.dumps(first))) == list(first)


def test_missing_source_is_reported_without_masking_clean_target_and_counts_are_exact(tmp_path):
    source, target = _materialise(tmp_path, "missing_source")

    result = run_scenario(_scenario(source, target, ["structural_validation", "schema_validation"]))

    for name in EXECUTED_ORDER:
        issues = result[name]["issues"]
        assert len(issues) == 1
        assert (issues[0]["kind"], issues[0]["dataset"], issues[0]["row_number"]) == ("file_access", "source", None)


def test_both_files_missing_reports_source_then_target(tmp_path):
    result = run_scenario(_scenario(tmp_path / "a.csv", tmp_path / "b.csv", ["schema_validation", "missing_records"]))

    assert result["status"] == "failed"
    assert result["complete"] is False
    assert [(i["kind"], i["dataset"]) for i in result["schema_validation"]["issues"]] == [
        ("file_access", "source"),
        ("file_access", "target"),
    ]


def test_width_defect_issue_has_exact_row_and_lines(tmp_path):
    source, target = _materialise(tmp_path, "width_defect")

    result = run_scenario(_scenario(source, target, ["structural_validation"]))

    issues = result["structural_validation"]["issues"]
    assert [(i["kind"], i["dataset"], i["row_number"], i["start_line"], i["end_line"]) for i in issues] == [
        ("structural", "target", 2, 2, 2)
    ]
    assert result["status"] == "failed" and result["complete"] is True


def test_same_path_on_both_sides_runs_cleanly(tmp_path):
    path = tmp_path / "same.csv"
    path.write_bytes(CLEAN)

    result = run_scenario(_scenario(path, path, ["schema_validation", "structural_validation"]))

    assert result["status"] == "passed" and result["complete"] is True
    assert result["structural_validation"]["source_path"] == result["structural_validation"]["target_path"] == str(path)


def test_empty_and_header_only_files_have_exact_outcomes(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    header_only = tmp_path / "h.csv"
    header_only.write_bytes(b"id,name\n")

    on_empty = run_scenario(_scenario(empty, header_only, ["structural_validation", "schema_validation"]))
    on_header = run_scenario(_scenario(header_only, header_only, ["structural_validation", "schema_validation"]))

    assert on_empty["status"] == "failed" and on_empty["complete"] is True
    for name in EXECUTED_ORDER:
        assert [(i["kind"], i["dataset"], i["row_number"]) for i in on_empty[name]["issues"]] == [("structural", "source", None)]
    assert on_header["status"] == "passed" and on_header["complete"] is True


def test_repeated_runs_with_unimplemented_checks_keep_configured_order(tmp_path):
    source, target = _materialise(tmp_path, "clean")
    configured = ["mismatched_values", "schema_validation", "missing_records", "structural_validation", "duplicate_records"]

    for _ in range(3):
        result = run_scenario(_scenario(source, target, list(configured)))
        assert list(result)[7:] == [
            "structural_validation",
            "schema_validation",
            "mismatched_values",
            "missing_records",
            "duplicate_records",
        ]
        assert result["validation_checks"] == configured
        assert result["status"] == "incomplete" and result["complete"] is False


def test_unimplemented_only_never_reads_data_and_is_incomplete(tmp_path, monkeypatch):
    def _boom(path):
        raise AssertionError("no file may be read when nothing is executable")

    monkeypatch.setattr("edmvf.validation.schema.read_csv_dataset", _boom)

    result = run_scenario(_scenario(tmp_path / "absent-a.csv", tmp_path / "absent-b.csv", ["duplicate_records"]))

    assert result["status"] == "incomplete"
    assert result["complete"] is False
    assert list(result) == HEAD + ["duplicate_records"]
    assert set(result["duplicate_records"]) == {"status", "message"}


def test_framework_exceptions_propagate_and_are_not_reported_as_data_issues(tmp_path, monkeypatch):
    source, target = _materialise(tmp_path, "clean")

    def _bug(path):
        raise RuntimeError("framework bug")

    monkeypatch.setattr("edmvf.validation.schema.read_csv_dataset", _bug)

    with pytest.raises(RuntimeError, match="framework bug"):
        run_scenario(_scenario(source, target, ["schema_validation"]))


def test_result_top_level_types(tmp_path):
    source, target = _materialise(tmp_path, "clean")

    result = run_scenario(_scenario(source, target, ["schema_validation", "missing_records"]))

    assert result["scenario_name"] == "ir006_edge"
    assert result["source_type"] == "csv" and result["target_type"] == "csv"
    assert result["matching_keys"] == ["id"]
    assert isinstance(result["complete"], bool)
    assert result["status"] in {"passed", "failed", "incomplete"}
