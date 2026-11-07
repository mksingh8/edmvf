"""IR-012: memory guards for loading a CSV (partial evidence for NFR-004 only).

How memory is measured (the same method as benchmarks/csv_validation_benchmark.py):

* tracemalloc is started immediately after gc.collect() and stopped when the
  call has returned; only Python allocations are counted (not process RSS).
* ``peak``      - tracemalloc peak between start and the moment the call returns,
                  with the call's return value still referenced.
* ``retained``  - tracemalloc current memory at that moment (the returned object).
* ``transient`` - peak - retained: allocations made during the call and freed
                  before it returned (the file bytes, decoded text, text-stream
                  buffers, parser state), excluding the returned object.

The two guards:

1. TRANSIENT READER MEMORY: ``transient`` of ``read_csv_dataset(path)`` is at most
   2.0 x the size of the input file in bytes. (Before the change: about 5x.)
2. 1.25x A SINGLE READ: the ``peak`` of ``run_scenario`` (or of
   ``validate_csv_schema``) on a source and a target of the same size is at most
   1.25 x the ``peak`` of ``read_csv_dataset`` on one input of that size. This
   holds only if one side's dataset is released before the other is loaded.
   (Before the change: about 1.64x.)

These guards use wide margins on a 10K-row file; they depend on CPython's
allocator and are not a performance benchmark (see benchmarks/).
"""

import gc
import tracemalloc
from pathlib import Path

import pytest

from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.data.csv_dataset import read_csv_dataset
from edmvf.runner import run_scenario
from edmvf.validation.schema import validate_csv_schema

ROWS = 10_000
TRANSIENT_LIMIT = 2.0
RUN_VS_READ_LIMIT = 1.25


def measure(call):
    """Return (result, peak, retained, transient) in bytes using the documented method."""
    gc.collect()
    tracemalloc.start()
    try:
        result = call()
        retained, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, peak, retained, peak - retained


def write_dataset(path: Path, rows: int = ROWS) -> int:
    lines = ["id,customer_code,first_name,last_name,email,country,status,amount,created_on,note"]
    for n in range(rows):
        lines.append(
            f"{n},C{n:07d},First{n % 997},Last{n % 991},user{n}@example.com,"
            f"{'US' if n % 3 else 'CA'},{'active' if n % 5 else 'inactive'},{n * 7 % 10000}.{n % 100:02d},"
            f"2026-{n % 12 + 1:02d}-{n % 28 + 1:02d},note number {n} with some padding text"
        )
    data = ("\n".join(lines) + "\n").encode("utf-8")
    path.write_bytes(data)
    return len(data)


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    folder = tmp_path_factory.mktemp("memory")
    source, target = folder / "source.csv", folder / "target.csv"
    size = write_dataset(source)
    assert write_dataset(target) == size
    return source, target, size


def scenario(source: Path, target: Path, checks: list) -> Scenario:
    return Scenario(
        name="memory",
        version=1,
        source=DataSource("csv", str(source)),
        target=DataSource("csv", str(target)),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=checks),
        output=OutputConfig(directory="./out"),
    )


def test_reader_transient_memory_is_bounded_by_file_size(files):
    source, _, size = files

    dataset, peak, retained, transient = measure(lambda: read_csv_dataset(source))

    assert len(dataset.rows) == ROWS
    assert transient <= TRANSIENT_LIMIT * size, f"transient {transient} > {TRANSIENT_LIMIT} x {size}"


@pytest.mark.parametrize(
    "checks",
    [["structural_validation"], ["schema_validation"], ["structural_validation", "schema_validation"]],
    ids=["structural", "schema", "both"],
)
def test_run_scenario_peak_is_at_most_1_25_times_a_single_read(files, checks):
    source, target, _ = files
    _, read_peak, _, _ = measure(lambda: read_csv_dataset(source))

    result, peak, _, _ = measure(lambda: run_scenario(scenario(source, target, checks)))

    assert result["status"] == "passed"
    assert peak <= RUN_VS_READ_LIMIT * read_peak, f"run peak {peak} > {RUN_VS_READ_LIMIT} x read peak {read_peak}"


def test_validate_csv_schema_peak_is_at_most_1_25_times_a_single_read(files):
    source, target, _ = files
    _, read_peak, _, _ = measure(lambda: read_csv_dataset(source))

    result, peak, _, _ = measure(lambda: validate_csv_schema(source, target, ["schema_validation"]))

    assert result.passed is True
    assert peak <= RUN_VS_READ_LIMIT * read_peak, f"schema peak {peak} > {RUN_VS_READ_LIMIT} x read peak {read_peak}"
