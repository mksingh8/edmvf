"""IR-005: each input is physically opened once, never rewound, and read once.

A logical-read count alone cannot show this: one `open` could still be followed
by a seek back to the start and a second pass. The probe in
benchmarks/csv_validation_benchmark.py runs one `run_scenario` in a SEPARATE
interpreter, with an audit hook plus a thin file proxy installed there (never in
the pytest process), and reports for each input path:

* opens      - `open` events for that path (audit hook);
* seeks      - seek() calls on the opened file objects (a rewind would show here);
* bytes_read - total bytes returned by read/readinto/readline/iteration;
* size       - the file size on disk.

One sequential pass means opens == 1, seeks == 0 and bytes_read == size.
The probe sees `open`, `io.open`, `pathlib.Path.open` and the audit `open` event
(so `os.open` opens are counted, though not their reads or seeks); it does not see
memory maps (see benchmarks/README.md).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "csv_validation_benchmark.py"

CLEAN = b"id,name\n1,Alpha\n2,Beta\n"
FILES = {
    "clean": CLEAN,
    "width": b"id,name\n1,Alpha,extra\n",
    "dup_header": b"id,id\n1,2\n",
    "bad_utf8": b"id,name\n1,Caf\xe9\n",
    "bad_quote": b'id,name\n1,"a"b\n',
    "multiline_wide": b'id,name\n1,"a\nb",extra\n',
}

CASES = [
    (["structural_validation"], "clean", "clean"),
    (["schema_validation"], "clean", "clean"),
    (["structural_validation", "schema_validation"], "clean", "clean"),
    (["schema_validation", "structural_validation"], "clean", "width"),
    (["structural_validation", "schema_validation"], "bad_utf8", "bad_quote"),
    (["structural_validation", "schema_validation"], "dup_header", "multiline_wide"),
]


def probe(source: Path, target: Path, checks: list) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "probe", "--source", str(source), "--target", str(target), "--checks", *checks],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


@pytest.mark.parametrize(
    ("checks", "source_kind", "target_kind"),
    CASES,
    ids=["+".join(c) + f"|{s}|{t}" for c, s, t in CASES],
)
def test_each_input_is_opened_once_never_rewound_and_read_once(tmp_path, checks, source_kind, target_kind):
    source = tmp_path / "source.csv"
    target = tmp_path / "target.csv"
    source.write_bytes(FILES[source_kind])
    target.write_bytes(FILES[target_kind])

    report = probe(source, target, checks)

    assert report["reads"] == 2
    for path in (source, target):
        entry = report["per_path"][key(path)]
        assert entry["opens"] == 1, entry
        assert entry["seeks"] == 0, entry
        assert entry["bytes_read"] == entry["size"] == path.stat().st_size, entry
