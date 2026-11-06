"""IR-009 adversarial tests: line-ending handling, ordinal vs line drift, both-side
attribution matrix, determinism, single-read spy, error propagation, JSON shape."""

import csv
import io
import itertools
import json
from pathlib import Path

import pytest

import edmvf.validation.schema as schema_module
from edmvf.config.loader import load_config
from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.runner import run_scenario
from edmvf.validation.schema import validate_csv_dataset, validate_csv_scenario, validate_csv_schema

STRUCT = ["structural_validation"]
BOTH = ["schema_validation", "structural_validation"]
GOOD = "id,name\n1,Alpha\n"


def _write(tmp_path: Path, name: str, content) -> Path:
    path = tmp_path / name
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8", newline="")
    else:
        path.write_bytes(content)
    return path


def _locs(tmp_path, content, checks=STRUCT):
    """(kind, row_number, start_line, end_line) of every issue from one file."""
    path = _write(tmp_path, "one.csv", content)
    result = validate_csv_dataset(path, "source", checks)
    return [(i.kind, i.row_number, i.start_line, i.end_line) for i in result.issues]


# --- line endings -------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    ["a,b\n1\n", "a,b\r\n1\r\n", "a,b\r1\r", "a,b\n1", "a,b\r\n1"],
    ids=["lf", "crlf", "lone_cr", "lf_no_trailing_newline", "crlf_no_trailing_newline"],
)
def test_width_issue_location_is_independent_of_line_ending_style(tmp_path, content):
    assert _locs(tmp_path, content) == [("structural", 2, 2, 2)]


def test_mixed_line_endings_each_end_a_physical_line(tmp_path):
    # L1 header, L2 "1" (bad), L3 "2,x", L4 "3" (bad), ended by lf/crlf/cr/none
    content = "a,b\n1\r\n2,x\r3"
    assert _locs(tmp_path, content) == [("structural", 2, 2, 2), ("structural", 4, 4, 4)]


def test_quoted_crlf_inside_field_spans_lines_without_advancing_ordinal(tmp_path):
    content = 'a,b\r\n1,"x\r\ny"\r\n2\r\n'
    assert _locs(tmp_path, content) == [("structural", 3, 4, 4)]


def test_quoted_lone_cr_inside_field_counts_as_a_physical_line(tmp_path):
    content = 'a,b\n1,"x\ry"\n2\n'
    assert _locs(tmp_path, content) == [("structural", 3, 4, 4)]


def test_bad_record_with_multiline_quoted_field_reports_full_span(tmp_path):
    content = 'a,b\n"x\ny\nz"\n1,2\n'
    assert _locs(tmp_path, content) == [("structural", 2, 2, 4)]


def test_record_whose_quoted_field_ends_exactly_at_eof(tmp_path):
    content = 'a,b\n"x\ny"'
    assert _locs(tmp_path, content) == [("structural", 2, 2, 3)]


def test_very_long_multiline_field_span(tmp_path):
    body = "\n".join(["line"] * 5000)
    content = f'a,b\n"{body}"\n1,2\n'
    assert _locs(tmp_path, content) == [("structural", 2, 2, 5001)]


def test_many_multiline_records_do_not_drift_ordinal_or_line(tmp_path):
    good = '1,"l1\nl2\nl3"\n'  # 3 physical lines, 1 record, width 2
    content = "a,b\n" + good * 50 + "9\n" + good + "8\n"
    # header L1; records 2..51 occupy L2..L151; "9" is record 52 on L152;
    # 1 more good (L153-155) = record 53; "8" is record 54 on L156
    assert _locs(tmp_path, content) == [("structural", 52, 152, 152), ("structural", 54, 156, 156)]


def test_multiple_width_mismatches_in_order_with_correct_spans(tmp_path):
    content = 'a,b\n1\n1,2\n"p\nq",x,y\n\n1,2\n1,2,3\n'
    # r2 L2 (1 val), r3 ok L3, r4 L4-5 (3 vals), r5 blank L6 (0 vals), r6 ok L7, r7 L8 (3 vals)
    assert _locs(tmp_path, content) == [
        ("structural", 2, 2, 2),
        ("structural", 4, 4, 5),
        ("structural", 5, 6, 6),
        ("structural", 7, 8, 8),
    ]


def test_width_issue_messages_use_the_same_ordinal_as_row_number(tmp_path):
    path = _write(tmp_path, "m.csv", 'a,b\n"x\ny",1\n7\n')
    issue = validate_csv_dataset(path, "target", STRUCT).issues[0]
    assert issue.row_number == 3
    assert "3" in issue.message and (issue.start_line, issue.end_line) == (4, 4)


# --- blank lines, header-only, empty ------------------------------------------


def test_header_only_file_has_no_issues(tmp_path):
    assert _locs(tmp_path, "a,b\n") == []
    assert _locs(tmp_path, "a,b") == []


def test_header_followed_by_blank_lines_reports_each_blank_record(tmp_path):
    assert _locs(tmp_path, "a,b\n\n\n") == [("structural", 2, 2, 2), ("structural", 3, 3, 3)]


def test_trailing_blank_line_after_data_is_a_zero_width_record(tmp_path):
    assert _locs(tmp_path, "a,b\n1,2\n\n") == [("structural", 3, 3, 3)]


def test_empty_file_issue_has_no_location(tmp_path):
    assert _locs(tmp_path, "") == [("structural", None, None, None)]


def test_blank_and_duplicate_header_carry_row_one_and_header_span(tmp_path):
    assert _locs(tmp_path, 'a,\n1,2\n') == [("schema", 1, 1, 1)]
    assert _locs(tmp_path, 'a,a\n1,2\n') == [("schema", 1, 1, 1)]
    # multiline header: spans lines 1-2; data row follows on line 3
    assert _locs(tmp_path, 'a,"a\nb",a\n1,2,3\n') == [("schema", 1, 1, 2)]
    assert _locs(tmp_path, 'a,"b\nc",\n1\n') == [("schema", 1, 1, 2), ("structural", 2, 3, 3)]


def test_blank_and_duplicate_header_in_one_file_yield_two_issues(tmp_path):
    assert _locs(tmp_path, "a,a,\n1,2,3\n") == [("schema", 1, 1, 1), ("schema", 1, 1, 1)]


# --- parse errors --------------------------------------------------------------


@pytest.mark.parametrize(
    "content, expected",
    [
        ('a,b\n1,"a"b\n', (2, 2, 2)),  # junk after closing quote
        ('a,b\n1,"abc\n2,3\n', (2, 2, 3)),  # unterminated quote swallows to EOF
        ('"a,b\n1,2\n', (1, 1, 2)),  # unterminated inside header
        ('"a"x,b\n1,2\n', (1, 1, 1)),  # csv.Error inside header
        ('a,b\n1,2\nx,"oops\n', (3, 3, 3)),  # unterminated at last record
        ('a,b\n1,"p\nq"\n3,"r"s\n', (3, 4, 4)),  # after a multiline record: ordinal 3, physical line 4
        ('a,b\n1,"abc', (2, 2, 2)),  # unterminated, no trailing newline
        ('a,b\r\n1,"abc\r\n2,3\r\n', (2, 2, 3)),  # unterminated with CRLF
    ],
)
def test_parse_error_carries_ordinal_and_physical_span(tmp_path, content, expected):
    row, start, end = expected
    issues = _locs(tmp_path, content)
    assert issues == [("parse_error", row, start, end)]


def test_parse_error_stops_further_structural_issues_for_that_file(tmp_path):
    # width error before the parse error is not reported (file cannot be parsed)
    assert _locs(tmp_path, 'a,b\n1\n2,"x"y\n') == [("parse_error", 3, 3, 3)]


@pytest.mark.parametrize("content", ['a,b\n1,ab"c\n', "a,b\n1,\x00x\n"], ids=["mid_field_quote", "nul"])
def test_csv_module_quirks_follow_the_stdlib_verdict(tmp_path, content):
    try:
        list(csv.reader(io.StringIO(content, newline=""), strict=True))
        expected = []
    except csv.Error:
        expected = [("parse_error", 2, 2, 2)]
    assert _locs(tmp_path, content) == expected


def test_invalid_utf8_has_no_location_even_after_multiline_records(tmp_path):
    content = b'a,b\n1,"x\ny"\n2,Caf\xe9\n'
    assert _locs(tmp_path, content) == [("parse_error", None, None, None)]


# --- both sides: attribution matrix -------------------------------------------

KINDS = ["missing", "directory", "bad_utf8", "bad_quote", "empty", "dup_header", "blank_header", "width", "clean"]
EXPECTED_KIND = {
    "missing": ["file_access"],
    "directory": ["file_access"],
    "bad_utf8": ["parse_error"],
    "bad_quote": ["parse_error"],
    "empty": ["structural"],
    "dup_header": ["schema"],
    "blank_header": ["schema"],
    "width": ["structural"],
    "clean": [],
}


def _make(tmp_path, side, kind):
    path = tmp_path / f"{side}.csv"
    content = {
        "bad_utf8": b"id,name\n1,Caf\xe9\n",
        "bad_quote": 'id,name\n1,"a"b\n',
        "empty": "",
        "dup_header": "id,id\n1,2\n",
        "blank_header": "id,\n1,2\n",
        "width": "id,name\n1\n",
        "clean": GOOD,
    }
    if kind == "directory":
        path.mkdir()
    elif kind != "missing":
        _write(tmp_path, path.name, content[kind])
    return path


@pytest.mark.parametrize("src_kind,tgt_kind", list(itertools.product(KINDS, KINDS)))
def test_every_defect_combination_attributes_each_side_source_first(tmp_path, src_kind, tgt_kind):
    source, target = _make(tmp_path, "source", src_kind), _make(tmp_path, "target", tgt_kind)

    result = validate_csv_schema(source, target, BOTH)
    expected = [(k, "source") for k in EXPECTED_KIND[src_kind]] + [(k, "target") for k in EXPECTED_KIND[tgt_kind]]

    assert [(i.kind, i.dataset) for i in result.issues] == expected
    assert result.passed is (not expected)
    # deterministic across repeated runs
    assert validate_csv_schema(source, target, BOTH).to_dict() == result.to_dict()


@pytest.mark.parametrize("kind", ["missing", "directory", "bad_utf8", "empty"])
def test_issues_without_record_location_serialize_null_lines(tmp_path, kind):
    result = validate_csv_schema(_make(tmp_path, "source", kind), _make(tmp_path, "target", "clean"), BOTH)
    (issue,) = result.to_dict()["issues"]
    assert (issue["row_number"], issue["start_line"], issue["end_line"]) == (None, None, None)
    assert "start_line" in issue and "end_line" in issue


def test_column_comparison_issues_are_target_attributed_with_null_location(tmp_path):
    source = _write(tmp_path, "s.csv", "id,name,x\n1,a,b\n")
    target = _write(tmp_path, "t.csv", "id,name,y\n1,a,b\n")
    result = validate_csv_schema(source, target, ["schema_validation"])
    assert [(i.kind, i.dataset, i.row_number, i.start_line, i.end_line) for i in result.issues] == [
        ("schema", "target", None, None, None),
        ("schema", "target", None, None, None),
    ]
    assert "x" in result.issues[0].message and "y" in result.issues[1].message


def test_column_comparison_skipped_when_any_issue_exists_even_with_differing_headers(tmp_path):
    source = _write(tmp_path, "s.csv", "id,name,x\n1,a\n")  # width bad
    target = _write(tmp_path, "t.csv", "id,other\n1,a\n")
    result = validate_csv_schema(source, target, BOTH)
    assert [(i.kind, i.dataset, i.row_number, i.start_line, i.end_line) for i in result.issues] == [
        ("structural", "source", 2, 2, 2)
    ]


def test_both_sides_width_issues_each_have_own_location(tmp_path):
    source = _write(tmp_path, "s.csv", 'a,b\n"x\ny"\n')  # r2 L2-3
    target = _write(tmp_path, "t.csv", "a,b\n1,2\n3\n")  # r3 L3
    result = validate_csv_schema(source, target, BOTH)
    assert [(i.dataset, i.row_number, i.start_line, i.end_line) for i in result.issues] == [
        ("source", 2, 2, 3),
        ("target", 3, 3, 3),
    ]


def test_schema_validation_alone_does_not_report_width_issues(tmp_path):
    source = _write(tmp_path, "s.csv", "a,b\n1\n")
    target = _write(tmp_path, "t.csv", "a,b\n1,2\n")
    result = validate_csv_schema(source, target, ["schema_validation"])
    assert result.issues == [] and result.passed is True


def test_validate_csv_schema_without_schema_check_reads_nothing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(schema_module, "read_csv_dataset", lambda p: calls.append(p))
    result = validate_csv_schema(tmp_path / "a.csv", tmp_path / "b.csv", STRUCT)
    assert result.passed is True and result.issues == [] and calls == []


# --- single read (spy) ---------------------------------------------------------


@pytest.mark.parametrize("src_kind,tgt_kind", [("clean", "clean"), ("width", "bad_quote"), ("missing", "dup_header"), ("empty", "bad_utf8")])
def test_validate_csv_schema_reads_each_file_exactly_once(tmp_path, monkeypatch, src_kind, tgt_kind):
    source, target = _make(tmp_path, "source", src_kind), _make(tmp_path, "target", tgt_kind)
    calls = []
    real = schema_module.read_csv_dataset

    def spy(path):
        calls.append(str(path))
        return real(path)

    monkeypatch.setattr(schema_module, "read_csv_dataset", spy)
    validate_csv_schema(source, target, BOTH)
    assert sorted(calls) == sorted([str(source), str(target)])


def test_same_path_for_source_and_target_is_attributed_to_both_sides(tmp_path):
    path = _write(tmp_path, "same.csv", "a,b\n1\n")
    result = validate_csv_schema(path, path, BOTH)
    assert [(i.kind, i.dataset, i.row_number) for i in result.issues] == [
        ("structural", "source", 2),
        ("structural", "target", 2),
    ]


# --- framework errors propagate -----------------------------------------------


@pytest.mark.parametrize("failing", ["source", "target"])
def test_runtime_error_from_reader_propagates_not_turned_into_issue(tmp_path, monkeypatch, failing):
    source, target = _make(tmp_path, "source", "clean"), _make(tmp_path, "target", "clean")
    real = schema_module.read_csv_dataset

    def boom(path):
        if Path(path).name.startswith(failing):
            raise RuntimeError("framework bug")
        return real(path)

    monkeypatch.setattr(schema_module, "read_csv_dataset", boom)
    with pytest.raises(RuntimeError, match="framework bug"):
        validate_csv_schema(source, target, BOTH)
    with pytest.raises(RuntimeError, match="framework bug"):
        validate_csv_dataset(source if failing == "source" else target, failing, STRUCT)


# --- scenario level ------------------------------------------------------------


def _scenario(source: Path, target: Path, checks=None) -> Scenario:
    return Scenario(
        name="ir009_edge",
        version=1,
        source=DataSource("csv", str(source)),
        target=DataSource("csv", str(target)),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=checks or list(BOTH)),
        output=OutputConfig(directory="./out"),
    )


def test_scenario_both_checks_both_sides_bad_each_result_self_contained(tmp_path):
    source = _write(tmp_path, "s.csv", "id,id,name\n1,2\n")  # dup header + width (row 2)
    target = _write(tmp_path, "t.csv", 'id,name\n1,"a"b\n')  # parse error row 2

    results = validate_csv_scenario(_scenario(source, target))

    def view(result):
        return [(i.kind, i.dataset, i.row_number, i.start_line, i.end_line) for i in result.issues]

    assert view(results["structural_validation"]) == [
        ("schema", "source", 1, 1, 1),
        ("structural", "source", 2, 2, 2),
        ("parse_error", "target", 2, 2, 2),
    ]
    assert view(results["schema_validation"]) == [
        ("schema", "source", 1, 1, 1),
        ("parse_error", "target", 2, 2, 2),
    ]
    for result in results.values():
        assert result.passed is False
        keys = [json.dumps(i.to_dict(), sort_keys=True) for i in result.issues]
        assert len(keys) == len(set(keys))
    # deterministic
    again = validate_csv_scenario(_scenario(source, target))
    assert {k: v.to_dict() for k, v in again.items()} == {k: v.to_dict() for k, v in results.items()}


def test_scenario_clean_pair_passes_both_checks(tmp_path):
    source = _write(tmp_path, "s.csv", GOOD)
    target = _write(tmp_path, "t.csv", "name,id\r\nAlpha,1\r\n")
    results = validate_csv_scenario(_scenario(source, target))
    assert all(r.passed and r.issues == [] for r in results.values())


def test_run_scenario_yaml_end_to_end_json_serializable_with_locations(tmp_path):
    source = _write(tmp_path, "source.csv", 'id,name\r\n1,"multi\r\nline"\r\n2\r\n')  # r3 on L4
    target = _write(tmp_path, "target.csv", b"id,name\n1,Caf\xe9\n")
    config = tmp_path / "scenario.yaml"
    config.write_text(
        f"""
name: ir009_e2e
version: 1
source:
  type: csv
  path: {source.as_posix()}
target:
  type: csv
  path: {target.as_posix()}
matching:
  keys:
    - id
validation:
  checks:
    - schema_validation
    - structural_validation
output:
  directory: ./output
""".strip(),
        encoding="utf-8",
    )

    payload = run_scenario(load_config(config))
    roundtrip = json.loads(json.dumps(payload))

    assert roundtrip == payload
    assert payload["status"] == "failed"
    structural = payload["structural_validation"]["issues"]
    assert [(i["kind"], i["dataset"], i["row_number"], i["start_line"], i["end_line"]) for i in structural] == [
        ("structural", "source", 3, 4, 4),
        ("parse_error", "target", None, None, None),
    ]
    schema = payload["schema_validation"]["issues"]
    assert [(i["kind"], i["dataset"]) for i in schema] == [("parse_error", "target")]
    for issue in structural + schema:
        assert set(issue) == {"kind", "dataset", "message", "row_number", "column_name", "start_line", "end_line"}
    assert run_scenario(load_config(config)) == payload


def test_message_never_echoes_invalid_bytes_or_cell_content(tmp_path):
    path = _write(tmp_path, "s.csv", b"a,b\n1,SECRET\xe9\n")
    (issue,) = validate_csv_dataset(path, "source", STRUCT).issues
    assert "SECRET" not in issue.message
