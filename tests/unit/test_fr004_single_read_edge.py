"""IR-005 / IR-012 adversarial edge cases for the single-read validators."""

import copy
import csv
import io
import itertools
import os
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import edmvf.validation.schema as schema_module
from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.data.csv_dataset import CsvDataset, CsvParseError, read_csv_dataset
from edmvf.runner import run_scenario
from edmvf.validation.schema import validate_csv_dataset, validate_csv_scenario, validate_csv_schema


class FrameworkBoom(Exception):
    """Stands in for a MemoryError-like framework failure."""


def scenario_for(source, target, checks):
    return Scenario(
        name="edge",
        version=1,
        source=DataSource("csv", str(source)),
        target=DataSource("csv", str(target)),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=list(checks)),
        output=OutputConfig(directory="./out"),
    )


def write(path, content):
    path.write_bytes(content)
    return path


def as_dicts(results):
    return {name: result.to_dict() for name, result in results.items()}


@pytest.fixture
def reads(monkeypatch):
    calls = []
    real = schema_module.read_csv_dataset

    def spy(path):
        calls.append(str(path))
        return real(path)

    monkeypatch.setattr(schema_module, "read_csv_dataset", spy)
    return calls


def fake_reader(monkeypatch, outcomes):
    """Install a reader that returns/raises outcomes[i] on the i-th call."""
    calls = []

    def fake(path):
        outcome = outcomes[len(calls)]
        calls.append(str(path))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(schema_module, "read_csv_dataset", fake)
    return calls


GOOD = b"id,name\n1,a\n"
SELECTIONS = [
    ["structural_validation"],
    ["schema_validation"],
    ["structural_validation", "schema_validation"],
    ["schema_validation", "structural_validation"],
]


# --- framework errors propagate, whichever side and whichever check ---------------


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("exc_type", [RuntimeError, FrameworkBoom, MemoryError])
@pytest.mark.parametrize("checks", SELECTIONS)
def test_framework_error_propagates_from_either_side_scenario(tmp_path, monkeypatch, which, exc_type, checks):
    src, tgt = write(tmp_path / "s.csv", GOOD), write(tmp_path / "t.csv", GOOD)
    ds = CsvDataset(headers=["id", "name"], rows=[["1", "a"]], line_spans=[(1, 1), (2, 2)])
    outcomes = [ds, ds]
    outcomes[which] = exc_type("boom")
    fake_reader(monkeypatch, outcomes)
    with pytest.raises(exc_type, match="boom"):
        validate_csv_scenario(scenario_for(src, tgt, checks))


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("checks", [["schema_validation"], ["structural_validation", "missing_records"]])
def test_framework_error_propagates_through_run_scenario(tmp_path, monkeypatch, which, checks):
    src, tgt = write(tmp_path / "s.csv", GOOD), write(tmp_path / "t.csv", GOOD)
    ds = CsvDataset(headers=["id", "name"], rows=[["1", "a"]], line_spans=[(1, 1), (2, 2)])
    outcomes = [ds, ds]
    outcomes[which] = RuntimeError("boom")
    fake_reader(monkeypatch, outcomes)
    with pytest.raises(RuntimeError, match="boom"):
        run_scenario(scenario_for(src, tgt, checks))


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("checks", [None, ["schema_validation"], ("schema_validation", "structural_validation"), {"schema_validation"}, frozenset({"schema_validation"})])
def test_framework_error_propagates_through_validate_csv_schema(tmp_path, monkeypatch, which, checks):
    ds = CsvDataset(headers=["id"], rows=[], line_spans=[(1, 1)])
    outcomes = [ds, ds]
    outcomes[which] = FrameworkBoom("boom")
    fake_reader(monkeypatch, outcomes)
    with pytest.raises(FrameworkBoom):
        validate_csv_schema(tmp_path / "s.csv", tmp_path / "t.csv", checks)


def test_source_data_problem_does_not_skip_target_framework_error(tmp_path, monkeypatch):
    # Source is a data problem (missing file), target raises a framework error.
    from edmvf.data.csv_dataset import CsvFileAccessError

    fake_reader(monkeypatch, [CsvFileAccessError("denied"), RuntimeError("boom")])
    with pytest.raises(RuntimeError, match="boom"):
        validate_csv_scenario(scenario_for(tmp_path / "s.csv", tmp_path / "t.csv", ["structural_validation", "schema_validation"]))


# --- fake datasets: edge values, shared objects, aliasing -------------------------


def test_empty_headers_and_no_spans_have_no_location(tmp_path, monkeypatch):
    empty = CsvDataset(headers=[], rows=[])
    fake_reader(monkeypatch, [empty, empty])
    result = validate_csv_scenario(scenario_for(tmp_path / "s.csv", tmp_path / "t.csv", SELECTIONS[2]))
    for name in ("structural_validation", "schema_validation"):
        issues = [i.to_dict() for i in result[name].issues]
        assert [(i["kind"], i["dataset"], i["row_number"], i["start_line"], i["end_line"]) for i in issues] == [
            ("structural", "source", None, None, None),
            ("structural", "target", None, None, None),
        ]
        assert result[name].passed is False


def test_none_header_is_blank_and_not_a_duplicate(tmp_path, monkeypatch):
    ds = CsvDataset(headers=[None, "a", None], rows=[], line_spans=[(1, 3)])
    fake_reader(monkeypatch, [ds, ds])
    result = validate_csv_schema(tmp_path / "s.csv", tmp_path / "t.csv")
    got = [(i.kind, i.dataset, i.message, i.row_number, i.column_name, i.start_line, i.end_line) for i in result.issues]
    expected = [("schema", side, "CSV header contains blank column names.", 1, None, 1, 3) for side in ("source", "target")]
    assert got == expected


def test_huge_header_list_clean_and_drift(tmp_path, monkeypatch):
    names = [f"c{i}" for i in range(10_000)]
    src = CsvDataset(headers=names, rows=[], line_spans=[(1, 1)])
    tgt = CsvDataset(headers=names[:-1] + ["zz"], rows=[], line_spans=[(1, 1)])
    fake_reader(monkeypatch, [src, tgt])
    result = validate_csv_schema(tmp_path / "s.csv", tmp_path / "t.csv")
    assert [(i.kind, i.dataset, i.message) for i in result.issues] == [
        ("schema", "target", "Missing columns in target: ['c9999']."),
        ("schema", "target", "Unexpected columns in target: ['zz']."),
    ]


def test_same_dataset_object_twice_is_not_mutated_and_results_do_not_alias(tmp_path, monkeypatch):
    ds = CsvDataset(headers=["id", "id"], rows=[["1"], ["1", "2"]], line_spans=[(1, 1), (2, 2), (3, 3)])
    snapshot = copy.deepcopy(ds)
    fake_reader(monkeypatch, [ds, ds])
    result = validate_csv_scenario(scenario_for(tmp_path / "s.csv", tmp_path / "t.csv", SELECTIONS[2]))
    assert ds == snapshot

    struct, schema = result["structural_validation"], result["schema_validation"]
    assert struct.issues is not schema.issues
    before_schema = schema.to_dict()
    struct.issues.clear()
    assert schema.to_dict() == before_schema
    assert len(schema.issues) == 2 and all(i.kind == "schema" for i in schema.issues)

    schema.issues.append(schema.issues[0])
    assert struct.issues == []


def test_load_failure_issues_equal_by_value_but_lists_independent(tmp_path):
    missing = tmp_path / "nope.csv"
    tgt = write(tmp_path / "t.csv", GOOD)
    result = validate_csv_scenario(scenario_for(missing, tgt, SELECTIONS[2]))
    struct, schema = result["structural_validation"], result["schema_validation"]
    expected = [
        {
            "kind": "file_access",
            "dataset": "source",
            "message": f"CSV file not found: {missing}",
            "row_number": None,
            "column_name": None,
            "start_line": None,
            "end_line": None,
        }
    ]
    assert [i.to_dict() for i in struct.issues] == expected
    assert [i.to_dict() for i in schema.issues] == expected
    assert struct.issues is not schema.issues
    struct.issues.clear()
    assert [i.to_dict() for i in schema.issues] == expected


def test_run_scenario_payload_issue_lists_are_independent(tmp_path):
    missing = tmp_path / "nope.csv"
    tgt = write(tmp_path / "t.csv", GOOD)
    result = run_scenario(scenario_for(missing, tgt, SELECTIONS[2]))
    assert result["structural_validation"]["issues"] == result["schema_validation"]["issues"]
    assert len(result["schema_validation"]["issues"]) == 1
    before = copy.deepcopy(result["schema_validation"])
    result["structural_validation"]["issues"].clear()
    assert result["schema_validation"] == before


# --- reads and equivalence to the public validators -------------------------------


@pytest.mark.parametrize("checks", [
    ("structural_validation", "schema_validation"),
    ("schema_validation", "structural_validation"),
    {"structural_validation", "schema_validation"},
    frozenset({"schema_validation"}),
    ("structural_validation",),
])
def test_every_selection_type_reads_each_input_once_and_orders_results(tmp_path, reads, checks):
    src = write(tmp_path / "s.csv", b"id,name\n1,a,x\n")
    tgt = write(tmp_path / "t.csv", b"id,other\n1\n")
    result = validate_csv_scenario(scenario_for(src, tgt, ["schema_validation"]), checks)
    assert reads == [str(src), str(tgt)]
    expected_keys = [c for c in ("structural_validation", "schema_validation") if c in set(checks)]
    assert list(result) == expected_keys


def test_same_path_for_source_and_target_attributes_both_sides(tmp_path, reads):
    both = write(tmp_path / "x.csv", b"id,name\n1,a,extra\n")
    result = validate_csv_scenario(scenario_for(both, both, SELECTIONS[2]))
    got = [(i.kind, i.dataset, i.row_number, i.start_line, i.end_line) for i in result["structural_validation"].issues]
    assert got == [("structural", "source", 2, 2, 2), ("structural", "target", 2, 2, 2)]
    assert result["schema_validation"].issues == [] and result["schema_validation"].passed is True
    assert len(reads) == 2


CONTENTS = {
    "header_only": b"id,name\n",
    "empty": b"",
    "blank_first": b"\nid,name\n1,a\n",
    "header_multiline_only": b'"a\nb",c\n',
    "dup_wide": ("x," * 3000 + "y\n" + "1\n").encode(),
    "wide": (",".join(f"c{i}" for i in range(10_000)) + "\n" + ",".join("v" for _ in range(10_000)) + "\n").encode(),
    "bad_utf8": b"id\n\xff\n",
    "bad_quote": b'id,name\n1,"a"b\n',
    "cr_only": b"id,name\r1,a\r2,b,c\r",
    "lone_cr_end": b"id,name\n1,a\r",
    "nul": b"id,name\n1,a\x00b\n",
    "bom": b"\xef\xbb\xbfid,name\n1,a\n",
    "u2028": "id,name\n1,a b\n2,c\n".encode(),
}


@pytest.mark.parametrize("kind", sorted(CONTENTS))
def test_scenario_equals_composition_of_public_validators(tmp_path, reads, kind):
    src = write(tmp_path / "s.csv", CONTENTS[kind])
    tgt = write(tmp_path / "t.csv", b"id,name\n1,a\n")
    for s, t in ((src, tgt), (tgt, src)):
        result = as_dicts(validate_csv_scenario(scenario_for(s, t, SELECTIONS[2])))
        s_res = validate_csv_dataset(s, "source", ["structural_validation"])
        t_res = validate_csv_dataset(t, "target", ["structural_validation"])
        assert result["structural_validation"]["issues"] == [i.to_dict() for i in [*s_res.issues, *t_res.issues]]
        assert result["structural_validation"]["passed"] is (not s_res.issues and not t_res.issues)
        assert result["schema_validation"] == validate_csv_schema(s, t).to_dict()
        assert list(result) == ["structural_validation", "schema_validation"]


def test_single_bad_width_row_after_20000_rows(tmp_path):
    rows = [b"id,name\n"] + [b"%d,n\n" % i for i in range(19_999)] + [b"x,y,z\n"]
    src = write(tmp_path / "s.csv", b"".join(rows))
    tgt = write(tmp_path / "t.csv", GOOD)
    result = validate_csv_scenario(scenario_for(src, tgt, ["structural_validation"]))
    issues = result["structural_validation"].issues
    assert [(i.kind, i.dataset, i.row_number, i.start_line, i.end_line, i.message) for i in issues] == [
        ("structural", "source", 20_001, 20_001, 20_001, "Row 20001 has inconsistent width: 3 values for 2 columns.")
    ]


def test_header_only_with_multiline_header_span(tmp_path):
    src = write(tmp_path / "s.csv", b'"a\nb",\n')
    tgt = write(tmp_path / "t.csv", GOOD)
    issues = validate_csv_scenario(scenario_for(src, tgt, ["schema_validation"]))["schema_validation"].issues
    assert [(i.kind, i.dataset, i.row_number, i.column_name, i.start_line, i.end_line) for i in issues] == [
        ("schema", "source", 1, "", 1, 2)
    ]


def test_files_changing_between_runs_are_reread(tmp_path):
    src, tgt = write(tmp_path / "s.csv", GOOD), write(tmp_path / "t.csv", GOOD)
    scenario = scenario_for(src, tgt, SELECTIONS[2])
    assert all(r.passed for r in validate_csv_scenario(scenario).values())
    write(tgt, b"id,name\n1\n")
    again = validate_csv_scenario(scenario)
    assert [(i.dataset, i.row_number) for i in again["structural_validation"].issues] == [("target", 2)]
    assert again["schema_validation"].passed is True


# --- reader-level equivalence against an independent in-memory parse --------------


def oracle(data: bytes):
    text = data.decode("utf-8")
    records, spans, prev = [], [], 0
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        for record in reader:
            records.append(record)
            spans.append((prev + 1, reader.line_num))
            prev = reader.line_num
    except csv.Error:
        return ("error", len(records) + 1, prev + 1, reader.line_num)
    if not records:
        return ("ok", [], [], [])
    return ("ok", records[0], records[1:], spans)


BIG = "x" * 30_000
READER_CONTENTS = {
    "chunk_boundary_quote": f'id,name\n1,"{BIG}\n{BIG}"\n2,"{"y" * 8191}"\n'.encode(),
    "boundary_cr_pair": ("a," + "z" * 8190 + "\r\nb,c\r\n").encode(),
    "boundary_multibyte": ("é" * 8191 + ",x\n1,2\n").encode(),
    "boundary_bad_quote": ("h\n" + "q" * 8190 + '"a"b\n').encode(),
    **{k: v for k, v in CONTENTS.items() if k != "bad_utf8"},
}


@pytest.mark.parametrize("kind", sorted(READER_CONTENTS))
def test_reader_matches_independent_parse(tmp_path, kind):
    data = READER_CONTENTS[kind]
    path = write(tmp_path / "f.csv", data)
    expected = oracle(data)
    if expected[0] == "error":
        with pytest.raises(CsvParseError) as info:
            read_csv_dataset(path)
        assert (info.value.row_number, info.value.start_line, info.value.end_line) == expected[1:]
    else:
        ds = read_csv_dataset(path)
        assert (ds.headers, ds.rows, ds.line_spans) == expected[1:]


def test_invalid_utf8_offset_is_absolute_even_past_first_chunk(tmp_path):
    data = b"a,b\n" + b"1,2\n" * 5000 + b"3,\xff\n"
    path = write(tmp_path / "f.csv", data)
    offset = data.index(b"\xff")
    with pytest.raises(CsvParseError, match=f"byte offset {offset}:"):
        read_csv_dataset(path)


def test_invalid_utf8_takes_precedence_over_csv_error(tmp_path):
    data = b'a\n"x"y\n\xff\n'
    path = write(tmp_path / "f.csv", data)
    with pytest.raises(CsvParseError, match="not valid UTF-8"):
        read_csv_dataset(path)


def test_returned_dataset_is_plain_lists(tmp_path):
    ds = read_csv_dataset(write(tmp_path / "f.csv", b"a,b\n1,2\n3,4\n"))
    assert type(ds.headers) is list and type(ds.rows) is list and type(ds.line_spans) is list
    assert all(type(r) is list for r in ds.rows)
    assert all(type(s) is tuple and len(s) == 2 for s in ds.line_spans)
    assert ds.rows[0] is not ds.rows[1]
    empty = read_csv_dataset(write(tmp_path / "e.csv", b""))
    assert (type(empty.headers), type(empty.rows), type(empty.line_spans)) == (list, list, list)


def test_concurrent_reads_of_same_path_share_no_state(tmp_path):
    path = write(tmp_path / "f.csv", b"".join(b"%d,v\n" % i for i in range(2000)).join([b"id,n\n", b""]))
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: read_csv_dataset(path), range(16)))
    first = results[0]
    assert len(first.rows) == 2000
    for other in results[1:]:
        assert other == first
        assert other.rows is not first.rows and other.headers is not first.headers


def test_no_handle_is_held_after_read(tmp_path):
    # Smoke test: it can only fail on Windows (renaming an open file is allowed on POSIX).
    path = write(tmp_path / "f.csv", GOOD)
    read_csv_dataset(path)
    moved = path.rename(tmp_path / "moved.csv")  # fails on Windows if a handle leaked
    moved.unlink()
    assert not path.exists()


# --- access classification --------------------------------------------------------


@pytest.mark.parametrize("exc_type", [FileNotFoundError, PermissionError, IsADirectoryError])
def test_read_bytes_oserror_after_exists_is_file_access(tmp_path, monkeypatch, exc_type):
    path = write(tmp_path / "f.csv", GOOD)

    def boom(self):
        raise exc_type("vanished")

    monkeypatch.setattr(Path, "read_bytes", boom)
    result = validate_csv_dataset(path, "source")
    assert [(i.kind, i.dataset, i.row_number, i.message) for i in result.issues] == [
        ("file_access", "source", None, f"Unable to read CSV file '{path}': vanished")
    ]
    scenario = validate_csv_scenario(scenario_for(path, path, SELECTIONS[2]))
    assert [(i.kind, i.dataset) for i in scenario["schema_validation"].issues] == [
        ("file_access", "source"),
        ("file_access", "target"),
    ]


def test_directory_path_is_file_access_not_exception(tmp_path):
    issues = validate_csv_dataset(tmp_path, "target").issues
    assert [(i.kind, i.dataset) for i in issues] == [("file_access", "target")]


class PathLike:
    def __init__(self, p):
        self.p = str(p)

    def __fspath__(self):
        return self.p

    def __str__(self):
        return self.p


@pytest.mark.parametrize("kind", ["wide", "bad_utf8", "cr_only"])
def test_str_path_pathlike_give_identical_results(tmp_path, kind):
    path = write(tmp_path / "f.csv", CONTENTS[kind])
    results = [validate_csv_dataset(variant, "source").to_dict() for variant in (str(path), path, PathLike(path))]
    assert results[0] == results[1] == results[2]
    others = [validate_csv_schema(v, v).to_dict() for v in (str(path), path, PathLike(path))]
    assert others[0] == others[1] == others[2]


def test_relative_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "r.csv", b"id\n1,2\n")
    issues = validate_csv_dataset("r.csv", "source").issues
    assert [(i.kind, i.row_number, i.start_line, i.end_line) for i in issues] == [("structural", 2, 2, 2)]
    missing = validate_csv_dataset("gone.csv", "target").issues
    assert [(i.kind, i.message) for i in missing] == [("file_access", "CSV file not found: gone.csv")]


# --- memory ------------------------------------------------------------------------


def test_tiny_input_peak_memory_stays_small(tmp_path):
    # Smoke test only: it guards against a gross regression on tiny inputs. The documented
    # memory contract is tested in test_nfr004_memory_bounds.py.
    path = write(tmp_path / "f.csv", GOOD)
    read_csv_dataset(path)  # warm caches
    tracemalloc.start()
    try:
        read_csv_dataset(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 256_000, peak
