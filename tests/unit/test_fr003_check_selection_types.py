"""IR-006: accepted collection types for Python check selections.

The Python validators accept a list, tuple, set or frozenset of check names and
apply the same rules to all of them. A bare string, bytes, a dict, a generator
or any other type is rejected. YAML and Scenario.validation.checks stay lists.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import edmvf.validation.schema as schema_module
from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.validation.schema import validate_csv_dataset, validate_csv_scenario, validate_csv_schema

CLEAN = "id,name\n1,Alpha\n"
WIDTH_BAD = "id,name\n1,Alpha,extra\n"
DRIFT = "id,email\n1,a\n"

COLLECTIONS = {"list": list, "tuple": tuple, "set": set, "frozenset": frozenset}


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8", newline="")
    return path


def _scenario(source: Path, target: Path, checks: list) -> Scenario:
    return Scenario(
        name="types",
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


# --- every accepted type behaves identically ------------------------------------


@pytest.mark.parametrize("kind", COLLECTIONS)
def test_validate_csv_dataset_accepts_every_collection_type(tmp_path, kind):
    path = _write(tmp_path, "d.csv", WIDTH_BAD)
    selection = COLLECTIONS[kind](["schema_validation", "structural_validation"])

    result = validate_csv_dataset(path, "source", selection)

    assert result.passed is False
    assert [i.kind for i in result.issues] == ["structural"]


@pytest.mark.parametrize("kind", COLLECTIONS)
def test_validate_csv_dataset_single_check_selection_for_every_type(tmp_path, kind):
    path = _write(tmp_path, "d.csv", WIDTH_BAD)

    structural = validate_csv_dataset(path, "source", COLLECTIONS[kind](["structural_validation"]))
    schema_only = validate_csv_dataset(path, "source", COLLECTIONS[kind](["schema_validation"]))

    assert [i.kind for i in structural.issues] == ["structural"]
    assert schema_only.passed is True  # width is a structural check


@pytest.mark.parametrize("kind", COLLECTIONS)
def test_validate_csv_schema_accepts_every_collection_type(tmp_path, kind):
    source = _write(tmp_path, "s.csv", CLEAN)
    drifted = _write(tmp_path, "t.csv", DRIFT)
    selection = COLLECTIONS[kind](["schema_validation"])

    assert validate_csv_schema(source, source, selection).passed is True
    failing = validate_csv_schema(source, drifted, selection)
    assert [(i.kind, i.dataset) for i in failing.issues] == [("schema", "target"), ("schema", "target")]


@pytest.mark.parametrize("kind", COLLECTIONS)
def test_validate_csv_scenario_accepts_every_collection_type(tmp_path, kind):
    path = _write(tmp_path, "d.csv", WIDTH_BAD)
    scenario = _scenario(path, path, ["structural_validation", "schema_validation"])

    results = validate_csv_scenario(scenario, COLLECTIONS[kind](["schema_validation", "structural_validation"]))

    assert list(results) == ["structural_validation", "schema_validation"]  # fixed execution order
    assert results["structural_validation"].passed is False
    assert results["schema_validation"].passed is True


def test_all_collection_types_give_identical_results(tmp_path):
    source = _write(tmp_path, "s.csv", WIDTH_BAD)
    target = _write(tmp_path, "t.csv", WIDTH_BAD)
    outcomes = {
        kind: (
            validate_csv_dataset(source, "source", make(["schema_validation", "structural_validation"])).to_dict(),
            validate_csv_schema(source, target, make(["schema_validation", "structural_validation"])).to_dict(),
        )
        for kind, make in COLLECTIONS.items()
    }

    assert outcomes["tuple"] == outcomes["list"] == outcomes["set"] == outcomes["frozenset"]


# --- the same rejection rules apply to every accepted type ----------------------

BAD_BY_TYPE = {
    "empty": lambda make: make([]),
    "unknown": lambda make: make(["bogus_check"]),
    "unimplemented": lambda make: make(["missing_records"]),
    "non_string": lambda make: make([1]),
    "none_item": lambda make: make([None]),
    "nested": lambda make: make([["schema_validation"]]),
    "case_variant": lambda make: make(["Schema_Validation"]),
}


def _constructible(kind: str, problem: str) -> bool:
    try:
        BAD_BY_TYPE[problem](COLLECTIONS[kind])
    except TypeError:  # e.g. a set cannot hold a nested list
        return False
    return True


INVALID_CONTENT_CASES = [(k, p) for k in COLLECTIONS for p in BAD_BY_TYPE if _constructible(k, p)]


@pytest.mark.parametrize(("kind", "problem"), INVALID_CONTENT_CASES)
@pytest.mark.parametrize("function", ["dataset", "schema", "scenario"])
def test_invalid_contents_are_rejected_for_every_type_before_any_read(tmp_path, read_spy, kind, problem, function):
    path = _write(tmp_path, "d.csv", CLEAN)
    selection = BAD_BY_TYPE[problem](COLLECTIONS[kind])

    with pytest.raises(ValueError, match="checks"):
        if function == "dataset":
            validate_csv_dataset(path, "source", selection)
        elif function == "schema":
            validate_csv_schema(path, path, selection)
        else:
            validate_csv_scenario(_scenario(path, path, ["schema_validation"]), selection)
    assert read_spy == []


@pytest.mark.parametrize("make", [list, tuple], ids=["list", "tuple"])
@pytest.mark.parametrize("function", ["dataset", "schema", "scenario"])
def test_duplicates_are_rejected_for_ordered_types(tmp_path, read_spy, make, function):
    path = _write(tmp_path, "d.csv", CLEAN)
    selection = make(["schema_validation", "structural_validation", "schema_validation"])

    with pytest.raises(ValueError, match=r"duplicate entries: \['schema_validation'\]"):
        if function == "dataset":
            validate_csv_dataset(path, "source", selection)
        elif function == "schema":
            validate_csv_schema(path, path, selection)
        else:
            validate_csv_scenario(_scenario(path, path, ["schema_validation"]), selection)
    assert read_spy == []


@pytest.mark.parametrize("make", [tuple, frozenset, set], ids=["tuple", "frozenset", "set"])
def test_validate_csv_schema_requires_schema_validation_for_every_type(tmp_path, read_spy, make):
    path = _write(tmp_path, "d.csv", CLEAN)

    with pytest.raises(ValueError, match="must include 'schema_validation'"):
        validate_csv_schema(path, path, make(["structural_validation"]))
    assert read_spy == []


# --- types that are always rejected ---------------------------------------------


def _generator():
    yield "schema_validation"


@pytest.mark.parametrize(
    ("selection", "type_name"),
    [
        ("schema_validation", "str"),
        (b"schema_validation", "bytes"),
        ({"schema_validation": True}, "dict"),
        (_generator(), "generator"),
        (iter(["schema_validation"]), "list_iterator"),
        (range(2), "range"),
        (42, "int"),
    ],
    ids=["str", "bytes", "dict", "generator", "iterator", "range", "int"],
)
@pytest.mark.parametrize("function", ["dataset", "schema", "scenario"])
def test_unsupported_selection_types_are_rejected_with_the_accepted_types_named(
    tmp_path, read_spy, function, selection, type_name
):
    path = _write(tmp_path, "d.csv", CLEAN)

    with pytest.raises(ValueError, match=rf"list, tuple, set or frozenset of check names, got {type_name}"):
        if function == "dataset":
            validate_csv_dataset(path, "source", selection)
        elif function == "schema":
            validate_csv_schema(path, path, selection)
        else:
            validate_csv_scenario(_scenario(path, path, ["schema_validation"]), selection)
    assert read_spy == []


# --- sets: deterministic regardless of hash randomization -----------------------


def test_set_selection_error_messages_are_sorted_and_deterministic(tmp_path):
    path = _write(tmp_path, "d.csv", CLEAN)
    selection = {"zeta_check", "alpha_check", "mid_check"}

    messages = set()
    for _ in range(3):
        with pytest.raises(ValueError) as excinfo:
            validate_csv_dataset(path, "source", selection)
        messages.add(str(excinfo.value))

    assert len(messages) == 1
    assert "['alpha_check', 'mid_check', 'zeta_check']" in messages.pop()


def test_set_selection_messages_do_not_depend_on_the_hash_seed(tmp_path):
    path = _write(tmp_path, "d.csv", CLEAN)
    script = textwrap.dedent(
        """
        import sys
        from edmvf.validation.schema import validate_csv_dataset
        try:
            validate_csv_dataset(sys.argv[1], "source", {"zeta_check", "alpha_check", "mid_check", "beta_check"})
        except ValueError as exc:
            print(exc)
        """
    )
    outputs = set()
    for seed in ("0", "1", "2", "12345"):
        completed = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            env={**__import__("os").environ, "PYTHONHASHSEED": seed},
        )
        assert completed.returncode == 0, completed.stderr
        outputs.add(completed.stdout)

    assert len(outputs) == 1
    (output,) = outputs
    assert "unsupported checks ['alpha_check', 'beta_check', 'mid_check', 'zeta_check']" in output


def test_set_selection_does_not_change_the_fixed_execution_order(tmp_path):
    path = _write(tmp_path, "d.csv", CLEAN)
    scenario = _scenario(path, path, ["schema_validation", "structural_validation"])

    orders = {
        tuple(validate_csv_scenario(scenario, {"schema_validation", "structural_validation"}))
        for _ in range(5)
    }

    assert orders == {("structural_validation", "schema_validation")}


# --- sets are rejected exactly like lists, whatever the element ------------------


class _ReprBomb:
    def __repr__(self):
        raise RuntimeError("repr must not be called on a rejected element")


@pytest.mark.parametrize("make", [list, tuple, set, frozenset], ids=["list", "tuple", "set", "frozenset"])
@pytest.mark.parametrize(
    "element",
    [10**5000, _ReprBomb(), None, 1.5, b"x"],
    ids=["huge_int", "repr_bomb", "none", "float", "bytes"],
)
@pytest.mark.parametrize("function", ["dataset", "schema", "scenario"])
def test_non_string_elements_are_rejected_identically_for_every_type(tmp_path, read_spy, make, element, function):
    path = _write(tmp_path, "d.csv", CLEAN)
    selection = make([element])

    with pytest.raises(ValueError, match=r"checks must be strings, got") as excinfo:
        if function == "dataset":
            validate_csv_dataset(path, "source", selection)
        elif function == "schema":
            validate_csv_schema(path, path, selection)
        else:
            validate_csv_scenario(_scenario(path, path, ["schema_validation"]), selection)
    assert type(element).__name__ in str(excinfo.value)
    assert read_spy == []


def test_mixed_element_types_in_a_set_are_rejected_deterministically(tmp_path):
    path = _write(tmp_path, "d.csv", CLEAN)
    messages = set()
    for _ in range(3):
        with pytest.raises(ValueError) as excinfo:
            validate_csv_dataset(path, "source", {1, "schema_validation", None, b"x", (1,)})
        messages.add(str(excinfo.value))

    assert messages == {"validate_csv_dataset: checks must be strings, got NoneType"}


# --- the selection is never mutated, and later mutation is irrelevant -----------


@pytest.mark.parametrize("kind", ["list", "set"])
def test_selection_is_not_mutated(tmp_path, kind):
    path = _write(tmp_path, "d.csv", CLEAN)
    selection = COLLECTIONS[kind](["schema_validation", "structural_validation"])
    snapshot = COLLECTIONS[kind](selection)

    validate_csv_dataset(path, "source", selection)
    validate_csv_schema(path, path, selection)

    assert selection == snapshot


# --- YAML and Scenario.validation.checks stay lists -----------------------------


@pytest.mark.parametrize("make", [tuple, set, frozenset], ids=["tuple", "set", "frozenset"])
def test_scenario_validation_checks_must_still_be_a_list(tmp_path, read_spy, make):
    from edmvf.config.validator import validate_scenario
    from edmvf.runner import run_scenario

    path = _write(tmp_path, "d.csv", CLEAN)
    scenario = _scenario(path, path, make(["schema_validation"]))

    with pytest.raises(ValueError, match=r"validation\.checks must be a list"):
        validate_scenario(scenario)
    with pytest.raises(ValueError, match=r"validation\.checks must be a list"):
        run_scenario(scenario)
    assert read_spy == []
