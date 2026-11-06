"""IR-009: correct source/target attribution and a defined record-location contract.

Record numbering: `row_number` is the 1-based ordinal of the logical CSV record
as csv.reader yields it (header = record 1, first data record = 2). Line breaks
inside quoted fields do not advance it. `start_line`/`end_line` are the
1-based physical lines the record occupies (inclusive); they are null when an
issue has no record location.
"""

from pathlib import Path

import pytest

import edmvf.validation.schema as schema_module
from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.runner import run_scenario
from edmvf.validation.result import ValidationIssue
from edmvf.validation.schema import validate_csv_dataset, validate_csv_scenario, validate_csv_schema


def _write(tmp_path: Path, name: str, content) -> Path:
    path = tmp_path / name
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8", newline="")
    else:
        path.write_bytes(content)
    return path


def _pairs(result):
    return [(issue.kind, issue.dataset) for issue in result.issues]


BAD_UTF8 = b"id,name\n1,Caf\xe9\n"
BAD_QUOTE = 'id,name\n1,"a"b\n'
GOOD = "id,name\n1,Alpha\n"


def _scenario(source: Path, target: Path) -> Scenario:
    return Scenario(
        name="ir009",
        version=1,
        source=DataSource("csv", str(source)),
        target=DataSource("csv", str(target)),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=["schema_validation", "structural_validation"]),
        output=OutputConfig(directory="./out"),
    )


# --- A. both sides are reported, each attributed to its own side -------------


def test_missing_files_on_both_sides_report_source_and_target(tmp_path):
    result = validate_csv_schema(tmp_path / "s.csv", tmp_path / "t.csv", ["schema_validation"])

    assert result.passed is False
    assert _pairs(result) == [("file_access", "source"), ("file_access", "target")]


def test_invalid_utf8_on_both_sides_reports_source_and_target(tmp_path):
    result = validate_csv_schema(
        _write(tmp_path, "s.csv", BAD_UTF8), _write(tmp_path, "t.csv", BAD_UTF8), ["schema_validation"]
    )

    assert _pairs(result) == [("parse_error", "source"), ("parse_error", "target")]


def test_malformed_quoting_on_both_sides_reports_source_and_target(tmp_path):
    result = validate_csv_schema(
        _write(tmp_path, "s.csv", BAD_QUOTE), _write(tmp_path, "t.csv", BAD_QUOTE), ["schema_validation"]
    )

    assert _pairs(result) == [("parse_error", "source"), ("parse_error", "target")]


def test_missing_source_and_malformed_target_are_attributed_per_side(tmp_path):
    result = validate_csv_schema(tmp_path / "absent.csv", _write(tmp_path, "t.csv", BAD_QUOTE), ["schema_validation"])

    assert _pairs(result) == [("file_access", "source"), ("parse_error", "target")]


def test_malformed_source_and_unreadable_target_are_attributed_per_side(tmp_path):
    directory = tmp_path / "a_directory"
    directory.mkdir()

    result = validate_csv_schema(_write(tmp_path, "s.csv", BAD_QUOTE), directory, ["schema_validation"])

    assert _pairs(result) == [("parse_error", "source"), ("file_access", "target")]


def test_unreadable_source_does_not_mask_target_header_defect(tmp_path):
    result = validate_csv_schema(
        tmp_path / "absent.csv", _write(tmp_path, "t.csv", "id,id\n1,2\n"), ["schema_validation"]
    )

    assert _pairs(result) == [("file_access", "source"), ("schema", "target")]


def test_unreadable_side_skips_the_column_comparison(tmp_path):
    result = validate_csv_schema(
        tmp_path / "absent.csv", _write(tmp_path, "t.csv", "other\n1\n"), ["schema_validation"]
    )

    assert _pairs(result) == [("file_access", "source")]
    assert not any("columns" in issue.message.lower() for issue in result.issues)


def test_issue_order_is_source_then_target_and_repeatable(tmp_path):
    source = _write(tmp_path, "s.csv", "id,id\n1,2\n")
    target = _write(tmp_path, "t.csv", "name,name\n1,2\n")

    first = validate_csv_schema(source, target, ["schema_validation"])
    second = validate_csv_schema(source, target, ["schema_validation"])

    assert _pairs(first) == [("schema", "source"), ("schema", "target")]
    assert first.to_dict() == second.to_dict()


def test_validate_csv_schema_reads_each_file_once(tmp_path, monkeypatch):
    source = _write(tmp_path, "s.csv", GOOD)
    target = _write(tmp_path, "t.csv", GOOD)
    reads = []
    real = schema_module.read_csv_dataset

    def counting(path):
        reads.append(str(path))
        return real(path)

    monkeypatch.setattr(schema_module, "read_csv_dataset", counting)

    result = validate_csv_schema(source, target, ["schema_validation"])

    assert result.passed is True
    assert sorted(reads) == sorted([str(source), str(target)])


@pytest.mark.parametrize("call", ["dataset", "schema"])
def test_unexpected_reader_exception_propagates_as_a_framework_error(tmp_path, monkeypatch, call):
    def boom(path):
        raise RuntimeError("framework bug")

    monkeypatch.setattr(schema_module, "read_csv_dataset", boom)
    source = _write(tmp_path, "s.csv", GOOD)

    with pytest.raises(RuntimeError, match="framework bug"):
        if call == "dataset":
            validate_csv_dataset(source, "source", ["structural_validation"])
        else:
            validate_csv_schema(source, source, ["schema_validation"])


def test_each_check_result_is_self_contained(tmp_path):
    scenario = _scenario(_write(tmp_path, "s.csv", "id,id\n1,2\n"), _write(tmp_path, "t.csv", GOOD))

    results = validate_csv_scenario(scenario, ["structural_validation", "schema_validation"])

    for name in ("structural_validation", "schema_validation"):
        assert _pairs(results[name]) == [("schema", "source")], name


def test_single_side_failures_are_unchanged(tmp_path):
    good = _write(tmp_path, "good.csv", GOOD)

    assert _pairs(validate_csv_schema(tmp_path / "x.csv", good, ["schema_validation"])) == [("file_access", "source")]
    assert _pairs(validate_csv_schema(good, tmp_path / "x.csv", ["schema_validation"])) == [("file_access", "target")]


def test_column_differences_are_attributed_to_the_target_dataset(tmp_path):
    result = validate_csv_schema(
        _write(tmp_path, "s.csv", "id,name\n1,a\n"),
        _write(tmp_path, "t.csv", "id,email\n1,a\n"),
        ["schema_validation"],
    )

    assert [(i.kind, i.dataset) for i in result.issues] == [("schema", "target"), ("schema", "target")]
    assert "Missing columns in target" in result.issues[0].message
    assert "Unexpected columns in target" in result.issues[1].message


# --- B. record numbering and physical line spans ------------------------------


def _width_issues(tmp_path, content):
    result = validate_csv_dataset(_write(tmp_path, "d.csv", content), "source", ["structural_validation"])
    return [i for i in result.issues if i.kind == "structural"]


@pytest.mark.parametrize(
    ("label", "content", "row_number", "lines"),
    [
        ("plain", "id,name\n1,x,extra\n", 2, (2, 2)),
        ("after_multiline_record", 'id,name\n1,"a\nb"\n2,x,extra\n', 3, (4, 4)),
        ("wide_multiline_record", 'id,name\n1,"a\nb",extra\n', 2, (2, 3)),
        ("header_spans_lines", '"i\nd",name\n1,x,extra\n', 2, (3, 3)),
        ("crlf_multiline", 'id,name\r\n1,"a\r\nb\r\nc"\r\n2,x,extra\r\n', 3, (5, 5)),
        ("blank_line_is_a_record", "id,name\n1,x\n\n", 3, (3, 3)),
    ],
)
def test_row_number_counts_logical_records_and_lines_are_physical(tmp_path, label, content, row_number, lines):
    issues = _width_issues(tmp_path, content)

    assert len(issues) == 1, label
    assert issues[0].row_number == row_number, label
    assert (issues[0].start_line, issues[0].end_line) == lines, label


@pytest.mark.parametrize(
    "case",
    ["absent", "empty", "bad_utf8"],
)
def test_issues_without_a_record_location_have_null_location_fields(tmp_path, case):
    if case == "absent":
        result = validate_csv_dataset(tmp_path / "nope.csv", "source", ["structural_validation"])
    elif case == "empty":
        result = validate_csv_dataset(_write(tmp_path, "e.csv", ""), "source", ["structural_validation"])
    else:
        result = validate_csv_dataset(_write(tmp_path, "b.csv", BAD_UTF8), "source", ["structural_validation"])

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert (issue.row_number, issue.start_line, issue.end_line) == (None, None, None)


def test_column_comparison_issues_have_null_location_fields(tmp_path):
    result = validate_csv_schema(
        _write(tmp_path, "s.csv", "id,name\n1,a\n"), _write(tmp_path, "t.csv", "id\n1\n"), ["schema_validation"]
    )

    assert result.issues
    assert all((i.row_number, i.start_line, i.end_line) == (None, None, None) for i in result.issues)


@pytest.mark.parametrize(
    ("content", "row_number", "lines"),
    [
        ("id,id\n1,2\n", 1, (1, 1)),
        ('"a\nb","a\nb"\n1,2\n', 1, (1, 3)),
        ("id,\n1,2\n", 1, (1, 1)),
    ],
)
def test_header_issues_are_located_at_record_one(tmp_path, content, row_number, lines):
    result = validate_csv_dataset(_write(tmp_path, "h.csv", content), "target", ["schema_validation"])

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.kind == "schema"
    assert (issue.row_number, (issue.start_line, issue.end_line)) == (row_number, lines)


@pytest.mark.parametrize(
    ("content", "row_number", "start_line"),
    [
        ('id,name\n1,x\n2,"a"b\n', 3, 3),
        ('id,name\n1,"a\nb"\n2,"c"d\n', 3, 4),
        ('"a"b,name\n', 1, 1),
    ],
)
def test_csv_parse_errors_carry_the_failing_records_location(tmp_path, content, row_number, start_line):
    result = validate_csv_dataset(_write(tmp_path, "p.csv", content), "source", ["structural_validation"])

    assert [i.kind for i in result.issues] == ["parse_error"]
    issue = result.issues[0]
    assert issue.row_number == row_number
    assert issue.start_line == start_line
    assert issue.end_line is not None and issue.end_line >= start_line


def test_unterminated_quote_reports_where_the_record_started(tmp_path):
    result = validate_csv_dataset(_write(tmp_path, "u.csv", 'id,name\n1,"abc\nmore\n'), "source", ["structural_validation"])

    issue = result.issues[0]
    assert issue.kind == "parse_error"
    assert issue.row_number == 2
    assert issue.start_line == 2
    assert issue.end_line >= 3


# --- C. result shape stays backward compatible --------------------------------


def test_issue_to_dict_keeps_existing_keys_and_adds_line_fields():
    issue = ValidationIssue("structural", "source", "msg", 3, "col", 4, 5)

    assert issue.to_dict() == {
        "kind": "structural",
        "dataset": "source",
        "message": "msg",
        "row_number": 3,
        "column_name": "col",
        "start_line": 4,
        "end_line": 5,
    }


def test_validation_issue_positional_construction_without_line_fields_still_works():
    issue = ValidationIssue("structural", "source", "msg", 3, "col")

    assert (issue.row_number, issue.column_name, issue.start_line, issue.end_line) == (3, "col", None, None)
    assert issue.to_dict()["start_line"] is None


# --- end to end through run_scenario ------------------------------------------


@pytest.mark.parametrize(
    ("bad_content", "kind"),
    [(BAD_UTF8, "parse_error"), (BAD_QUOTE, "parse_error"), (None, "file_access")],
)
def test_ir009_run_scenario_reports_both_sides(tmp_path, bad_content, kind):
    source = tmp_path / "s.csv"
    target = tmp_path / "t.csv"
    if bad_content is not None:
        _write(tmp_path, "s.csv", bad_content)
        _write(tmp_path, "t.csv", bad_content)

    result = run_scenario(_scenario(source, target))

    assert result["status"] == "failed"
    for check in ("schema_validation", "structural_validation"):
        issues = result[check]["issues"]
        assert [(i["kind"], i["dataset"]) for i in issues] == [(kind, "source"), (kind, "target")], check


def test_ir009_multiline_width_location_through_the_runner(tmp_path):
    source = _write(tmp_path, "s.csv", GOOD)
    target = _write(tmp_path, "t.csv", 'id,name\n1,"a\nb"\n2,x,extra\n')

    result = run_scenario(_scenario(source, target))

    issues = result["structural_validation"]["issues"]
    assert len(issues) == 1
    assert issues[0]["dataset"] == "target"
    assert (issues[0]["row_number"], issues[0]["start_line"], issues[0]["end_line"]) == (3, 4, 4)


# --- reviewer follow-ups -------------------------------------------------------


def test_blank_first_record_is_located_at_record_one_but_empty_file_is_not(tmp_path):
    blank_first = validate_csv_dataset(_write(tmp_path, "b.csv", "\nid,name\n1,a\n"), "source", ["schema_validation"])
    empty = validate_csv_dataset(_write(tmp_path, "e.csv", ""), "source", ["schema_validation"])

    first = blank_first.issues[0]
    assert (first.kind, first.row_number, first.start_line, first.end_line) == ("structural", 1, 1, 1)
    assert first.message == "CSV is empty or missing a header row."
    assert (empty.issues[0].row_number, empty.issues[0].start_line) == (None, None)
    assert empty.issues[0].message == first.message


def test_width_issue_message_wording_is_unchanged(tmp_path):
    issues = _width_issues(tmp_path, "id,name\n1,x,extra\n")

    assert issues[0].message == "Row 2 has inconsistent width: 3 values for 2 columns."
