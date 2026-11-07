"""IR-005: each input is read once per run and shared by every selected check.

Reads are counted at the single point where the validators load data:
`edmvf.validation.schema.read_csv_dataset`. Physical file opens, rewinds and
bytes read are verified separately, in an isolated subprocess, by
test_nfr004_physical_reads.py.
"""

import itertools
from pathlib import Path

import pytest

import edmvf.validation.schema as schema_module
from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.data.csv_dataset import CsvDataset
from edmvf.runner import run_scenario
from edmvf.validation.schema import validate_csv_dataset, validate_csv_scenario, validate_csv_schema

# Input kinds shared with the equivalence tests: one clean file and one per kind of defect.
KINDS = {
    "clean": b"id,name\n1,Alpha\n2,Beta\n",
    "reordered": b"name,id\nAlpha,1\nBeta,2\n",
    "drift": b"id,email\n1,a@example.com\n",
    "width": b"id,name\n1,Alpha,extra\n",
    "dup_header": b"id,id\n1,2\n",
    "blank_header": b"id,\n1,2\n",
    "empty": b"",
    "header_only": b"id,name\n",
    "bad_utf8": b"id,name\n1,Caf\xe9\n",
    "bad_quote": b'id,name\n1,"a"b\n',
    "unterminated_quote": b'id,name\n1,"abc\n',
    "multiline_wide": b'id,name\n1,"a\nb",extra\n',
    "missing": None,
    "directory": "directory",
}


def materialise(tmp_path: Path, kind: str, stem: str) -> Path:
    path = tmp_path / f"{stem}.csv"
    content = KINDS[kind]
    if content == "directory":
        path.mkdir()
    elif content is not None:
        path.write_bytes(content)
    return path


def scenario_for(source: Path, target: Path, checks: list) -> Scenario:
    return Scenario(
        name="single_read",
        version=1,
        source=DataSource("csv", str(source)),
        target=DataSource("csv", str(target)),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=checks),
        output=OutputConfig(directory="./out"),
    )


@pytest.fixture
def reads(monkeypatch):
    calls = []
    real = schema_module.read_csv_dataset

    def spy(path):
        calls.append(str(path))
        return real(path)

    monkeypatch.setattr(schema_module, "read_csv_dataset", spy)
    return calls


IMPLEMENTED = ["structural_validation", "schema_validation"]
UNIMPLEMENTED = ["missing_records", "duplicate_records"]


def _selections():
    for size in (1, 2):
        for combo in itertools.permutations(IMPLEMENTED, size):
            for extra in ([], UNIMPLEMENTED[:1], UNIMPLEMENTED):
                yield [*combo, *extra]


SELECTIONS = list(_selections())
DEFECT_PAIRS = [
    ("clean", "clean"),
    ("clean", "width"),
    ("missing", "clean"),
    ("clean", "directory"),
    ("bad_utf8", "bad_quote"),
    ("dup_header", "multiline_wide"),
    ("empty", "unterminated_quote"),
]


@pytest.mark.parametrize("checks", SELECTIONS, ids=["+".join(c) for c in SELECTIONS])
@pytest.mark.parametrize(("source_kind", "target_kind"), DEFECT_PAIRS)
def test_run_scenario_reads_each_input_once(tmp_path, reads, checks, source_kind, target_kind):
    source = materialise(tmp_path, source_kind, "source")
    target = materialise(tmp_path, target_kind, "target")

    run_scenario(scenario_for(source, target, checks))

    assert reads == [str(source), str(target)]


def test_run_scenario_with_only_unimplemented_checks_reads_nothing(tmp_path, reads):
    source = materialise(tmp_path, "clean", "source")
    target = materialise(tmp_path, "clean", "target")

    run_scenario(scenario_for(source, target, UNIMPLEMENTED))

    assert reads == []


@pytest.mark.parametrize(
    "make_selection",
    [lambda: None, lambda: list(IMPLEMENTED), lambda: tuple(IMPLEMENTED), lambda: set(IMPLEMENTED), lambda: frozenset(IMPLEMENTED)],
    ids=["none", "list", "tuple", "set", "frozenset"],
)
def test_validate_csv_scenario_reads_each_input_once(tmp_path, reads, make_selection):
    source = materialise(tmp_path, "clean", "source")
    target = materialise(tmp_path, "width", "target")

    results = validate_csv_scenario(scenario_for(source, target, list(IMPLEMENTED)), make_selection())

    assert list(results) == ["structural_validation", "schema_validation"]
    assert reads == [str(source), str(target)]


def test_validate_csv_scenario_reads_nothing_for_an_invalid_or_empty_selection(tmp_path, reads):
    source = materialise(tmp_path, "clean", "source")
    scenario = scenario_for(source, source, UNIMPLEMENTED)

    assert validate_csv_scenario(scenario) == {}
    with pytest.raises(ValueError):
        validate_csv_scenario(scenario_for(source, source, list(IMPLEMENTED)), [])
    assert reads == []


def test_public_path_based_functions_read_as_documented(tmp_path, reads):
    source = materialise(tmp_path, "clean", "source")
    target = materialise(tmp_path, "clean", "target")

    validate_csv_dataset(source, "source")
    assert reads == [str(source)]
    reads.clear()

    validate_csv_schema(source, target)
    assert reads == [str(source), str(target)]


def test_same_path_on_both_sides_is_read_once_per_role(tmp_path, reads):
    path = materialise(tmp_path, "clean", "both")

    run_scenario(scenario_for(path, path, list(IMPLEMENTED)))

    assert reads == [str(path), str(path)]


def test_all_checks_in_one_run_share_one_read(tmp_path, monkeypatch):
    """If the content differed between reads, the two checks would disagree.

    The fake returns a duplicate-header dataset on the first read of each path
    and a clean one on any later read. One read per input means both checks see
    the duplicate header; two reads would show it to only one of them.
    """
    source = materialise(tmp_path, "clean", "source")
    target = materialise(tmp_path, "clean", "target")
    seen: dict[str, int] = {}

    def fake(path):
        seen[str(path)] = seen.get(str(path), 0) + 1
        if seen[str(path)] == 1:
            return CsvDataset(headers=["id", "id"], rows=[["1", "2"]], line_spans=[(1, 1), (2, 2)])
        return CsvDataset(headers=["id", "name"], rows=[["1", "Alpha"]], line_spans=[(1, 1), (2, 2)])

    monkeypatch.setattr(schema_module, "read_csv_dataset", fake)

    result = run_scenario(scenario_for(source, target, list(IMPLEMENTED)))

    for check in IMPLEMENTED:
        pairs = [(i["kind"], i["dataset"]) for i in result[check]["issues"]]
        assert pairs == [("schema", "source"), ("schema", "target")], check
    assert seen == {str(source): 1, str(target): 1}


def test_repeated_runs_are_identical(tmp_path):
    for source_kind, target_kind in DEFECT_PAIRS:
        folder = tmp_path / f"{source_kind}-{target_kind}"
        folder.mkdir()
        scenario = scenario_for(
            materialise(folder, source_kind, "source"), materialise(folder, target_kind, "target"), list(IMPLEMENTED)
        )

        first = run_scenario(scenario)
        second = run_scenario(scenario)

        assert first == second


# --- findings are unchanged: the scenario result equals the composition of the public validators ---


def composed_reference(source: Path, target: Path, checks: list) -> dict:
    """The orchestration as it was before single-read loading: each public,
    path-based validator reads for itself."""
    from edmvf.validation.result import SchemaValidationResult

    results = {}
    if "structural_validation" in checks:
        source_result = validate_csv_dataset(source, "source", ["structural_validation"])
        target_result = validate_csv_dataset(target, "target", ["structural_validation"])
        issues = [*source_result.issues, *target_result.issues]
        results["structural_validation"] = SchemaValidationResult(
            dataset="structural_validation",
            passed=not issues,
            issues=issues,
            source_path=str(source),
            target_path=str(target),
        )
    if "schema_validation" in checks:
        results["schema_validation"] = validate_csv_schema(source, target, ["schema_validation"])
    return {name: result.to_dict() for name, result in results.items()}


MATRIX_SELECTIONS = [["structural_validation"], ["schema_validation"], list(IMPLEMENTED), list(reversed(IMPLEMENTED))]


@pytest.mark.parametrize("source_kind", KINDS)
def test_validate_csv_scenario_equals_the_composition_of_the_public_validators(tmp_path, source_kind):
    for target_kind in KINDS:
        folder = tmp_path / target_kind
        folder.mkdir()
        source = materialise(folder, source_kind, "source")
        target = materialise(folder, target_kind, "target")
        for checks in MATRIX_SELECTIONS:
            scenario = scenario_for(source, target, list(checks))

            actual = {name: result.to_dict() for name, result in validate_csv_scenario(scenario).items()}

            assert actual == composed_reference(source, target, checks), (source_kind, target_kind, checks)
            assert list(actual) == [name for name in IMPLEMENTED if name in checks]  # fixed execution order


def test_public_interface_is_unchanged():
    import inspect

    from edmvf.validation.result import SchemaValidationResult, ValidationIssue

    expected = {
        "validate_csv_dataset": "(csv_path: 'str | Path', dataset_name: 'str', checks: 'CheckSelection | None' = None) -> 'SchemaValidationResult'",
        "validate_csv_schema": "(source_path: 'str | Path', target_path: 'str | Path', checks: 'CheckSelection | None' = None) -> 'SchemaValidationResult'",
        "validate_csv_scenario": "(scenario: 'Scenario', checks: 'CheckSelection | None' = None) -> 'dict[str, SchemaValidationResult]'",
    }
    for name, signature in expected.items():
        assert str(inspect.signature(getattr(schema_module, name))) == signature, name
    assert str(inspect.signature(run_scenario)) == "(scenario: 'Scenario') -> 'dict'"
    assert list(ValidationIssue("k", "d", "m").to_dict()) == [
        "kind", "dataset", "message", "row_number", "column_name", "start_line", "end_line",
    ]
    assert list(SchemaValidationResult("d", True).to_dict()) == ["dataset", "passed", "issues", "source_path", "target_path"]
