"""Smoke tests for the synthetic CSV validation benchmark harness (benchmarks/)."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "csv_validation_benchmark.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("csv_validation_benchmark", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def test_generate_is_deterministic(tmp_path):
    harness = _load_harness()
    manifests = []
    for name in ("a", "b"):
        assert harness.main(["generate", "--out", str(tmp_path / name), "--rows", "50"]) == 0
        manifests.append(json.loads((tmp_path / name / "manifest.json").read_text())["files"])
    assert manifests[0] == manifests[1]
    expected = {"data_50/source.csv"} | {f"data_50/target_{v}.csv" for v in harness.VARIANTS}
    assert set(manifests[0]) == expected
    assert (tmp_path / "a" / "data_50" / "target_bad_utf8_late.csv").read_bytes().count(bytes([255])) == 1


def test_run_records_expected_keys(tmp_path):
    assert _cli("generate", "--out", tmp_path, "--rows", "50").returncode == 0
    out = tmp_path / "result.json"
    proc = _cli("run", "--data", tmp_path, "--rows", "50", "--repeats", "1", "--variant", "clean", "--json", out, "--label", "t")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text())
    assert {"platform", "processor", "machine", "python", "cpu_count"} <= set(data["machine"])
    assert data["edmvf_file"] and data["dataset_hashes"] and data["label"] == "t"
    assert {r["op"] for r in data["results"]} == {"read", "structural", "schema", "both"}
    for r in data["results"]:
        assert {"reads", "peak", "retained", "transient", "digest", "wall_best_s", "status", "issue_count", "io"} <= set(r)
        assert r["transient"] == r["peak"] - r["retained"]
        for stats in r["io"].values():
            assert {"opens", "seeks", "bytes_read", "size"} <= set(stats)


def test_probe_prints_json(tmp_path):
    assert _cli("generate", "--out", tmp_path, "--rows", "50").returncode == 0
    folder = tmp_path / "data_50"
    proc = _cli("probe", "--source", folder / "source.csv", "--target", folder / "target_clean.csv",
                "--checks", "structural_validation", "schema_validation")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert len(report["per_path"]) == 2 and report["status"]
    for stats in report["per_path"].values():
        assert {"opens", "seeks", "bytes_read", "size"} <= set(stats)


def test_compare_fails_when_digests_differ(tmp_path):
    def result(digest):
        io_ = {"p": {"opens": 1, "seeks": 0, "bytes_read": 10, "size": 10}}
        return {"results": [{"rows": 5, "variant": "clean", "op": "both", "wall_best_s": 1.0, "peak": 100, "retained": 10,
                             "transient": 90, "reads": 4, "io": io_, "digest": digest}]}
    for name, digest in (("before", "aaa"), ("after", "bbb"), ("same", "aaa")):
        (tmp_path / f"{name}.json").write_text(json.dumps(result(digest)))
    assert _cli("compare", tmp_path / "before.json", tmp_path / "after.json").returncode != 0
    assert _cli("compare", tmp_path / "before.json", tmp_path / "same.json").returncode == 0


def test_probe_sees_reads_made_through_pathlib(tmp_path):
    """Regression for Python 3.10, where pathlib binds io.open at import time."""
    harness = _load_harness()
    path = tmp_path / "data.csv"
    path.write_bytes(b"id,name\n1,a\n")

    with harness.IoProbe([path]) as probe:
        assert path.read_bytes() == b"id,name\n1,a\n"
        assert path.read_text(encoding="utf-8") == "id,name\n1,a\n"

    (stats,) = probe.stats.values()
    assert stats["opens"] == 2
    assert stats["bytes_read"] == 2 * stats["size"]
    assert harness.blind_paths(probe.stats) == []


def test_probe_counts_rewinds_and_second_passes(tmp_path):
    harness = _load_harness()
    path = tmp_path / "data.csv"
    path.write_bytes(b"id,name\n1,a\n")

    with harness.IoProbe([path]) as probe:
        with open(path, "rb") as handle:
            handle.read()
            handle.seek(0)
            handle.read()

    (stats,) = probe.stats.values()
    assert (stats["opens"], stats["seeks"], stats["bytes_read"]) == (1, 1, 2 * stats["size"])


def test_a_blind_probe_is_reported_not_trusted(tmp_path):
    """Reads through os.read are not counted; the opens are, so the probe must call itself blind."""
    harness = _load_harness()
    path = tmp_path / "data.csv"
    path.write_bytes(b"id,name\n1,a\n")

    with harness.IoProbe([path]) as probe:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        os.read(descriptor, 100)
        os.close(descriptor)

    assert harness.blind_paths(probe.stats) == list(probe.stats)


def test_run_refuses_a_dataset_file_changed_after_generate(tmp_path):
    assert _cli("generate", "--out", tmp_path, "--rows", "50").returncode == 0
    target = tmp_path / "data_50" / "target_clean.csv"
    target.write_bytes(target.read_bytes() + b"extra,row\n")

    proc = _cli("run", "--data", tmp_path, "--rows", "50", "--repeats", "1", "--variant", "clean", "--json", tmp_path / "r.json")

    assert proc.returncode == 2
    assert "changed since generate" in proc.stderr


def test_code_hash_identifies_the_measured_code_by_content(tmp_path):
    import shutil

    import edmvf

    harness = _load_harness()
    package = Path(edmvf.__file__).parent
    copy = tmp_path / "edmvf"
    shutil.copytree(package, copy, ignore=shutil.ignore_patterns("__pycache__"))
    original = harness.code_hash(str(package / "__init__.py"))

    assert harness.code_hash(str(copy / "__init__.py")) == original
    (copy / "runner.py").write_bytes((copy / "runner.py").read_bytes() + b"\n# changed\n")
    assert harness.code_hash(str(copy / "__init__.py")) != original


def test_probe_handles_a_missing_input_and_a_directory_like_the_validators_do(tmp_path):
    assert _cli("generate", "--out", tmp_path, "--rows", "50").returncode == 0
    source = tmp_path / "data_50" / "source.csv"
    for target in (tmp_path / "data_50" / "nope.csv", tmp_path / "data_50"):
        proc = _cli("probe", "--source", source, "--target", target, "--checks", "structural_validation")
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["status"] == "failed"  # a file_access issue, not a probe error


def test_a_later_probe_restores_the_real_functions(tmp_path):
    import builtins
    import io
    import pathlib

    harness = _load_harness()
    path = tmp_path / "data.csv"
    path.write_bytes(b"id\n1\n")
    originals = (io.open, builtins.open, pathlib.Path.open)

    with harness.IoProbe([path]):
        second = harness.IoProbe([path])  # created while another probe is active
    with second:
        pass

    assert (io.open, builtins.open, pathlib.Path.open) == originals


def test_byte_counting_does_not_change_surrogateescape_reads(tmp_path):
    # Supported guarantee: text read in strict mode or with errors="surrogateescape" is returned
    # unchanged and counted exactly. Other lone surrogates are a documented limitation.
    harness = _load_harness()
    path = tmp_path / "data.csv"
    path.write_bytes(b"id\n1\xff\n")

    with harness.IoProbe([path]) as probe:
        text = open(path, encoding="utf-8", errors="surrogateescape").read()

    assert text == "id\n1\udcff\n"
    assert probe.stats[next(iter(probe.stats))]["bytes_read"] == len(b"id\n1\xff\n")


def test_lone_surrogates_outside_the_escape_range_are_a_documented_limitation(tmp_path):
    """Documents the limit of the guarantee above; EDMVF reads bytes and cannot produce such text."""
    harness = _load_harness()
    path = tmp_path / "data.csv"
    path.write_bytes(bytes([0xED, 0xA0, 0x80]))  # UTF-8 encoding of the lone surrogate U+D800
    assert open(path, encoding="utf-8", errors="surrogatepass").read() == chr(0xD800)  # fine without the probe

    with harness.IoProbe([path]):
        with pytest.raises(UnicodeEncodeError):
            open(path, encoding="utf-8", errors="surrogatepass").read()


def test_probes_cannot_be_nested_or_reentered_and_a_failed_entry_changes_nothing(tmp_path):
    import builtins
    import io
    import pathlib

    harness = _load_harness()
    path = tmp_path / "data.csv"
    path.write_bytes(b"id,name\n1,a\n")
    originals = (io.open, builtins.open, pathlib.Path.open)

    outer = harness.IoProbe([path])
    with outer:
        installed = (io.open, builtins.open, pathlib.Path.open)
        with pytest.raises(RuntimeError, match="already active"):
            harness.IoProbe([path]).__enter__()
        with pytest.raises(RuntimeError, match="already active"):
            outer.__enter__()  # the same probe, re-entered while active
        assert (io.open, builtins.open, pathlib.Path.open) == installed  # nothing was replaced
        assert path.read_bytes() == b"id,name\n1,a\n"  # the outer probe still counts

    (stats,) = outer.stats.values()
    assert (stats["opens"], stats["bytes_read"]) == (1, stats["size"])
    assert (io.open, builtins.open, pathlib.Path.open) == originals
    with harness.IoProbe([path]):  # and a new probe works after the failed attempts
        pass
    assert (io.open, builtins.open, pathlib.Path.open) == originals


def test_size_is_taken_from_the_normalised_path_so_odd_spellings_stay_checked(tmp_path):
    harness = _load_harness()
    path = tmp_path / "data.csv"
    path.write_bytes(b"id,name\n1,a\n")
    spellings = [str(path) + os.sep, str(tmp_path / "sub" / ".." / "data.csv"), os.fsencode(str(path)), path]

    for spelling in spellings:
        with harness.IoProbe([spelling]) as probe:
            assert path.read_bytes() == b"id,name\n1,a\n"
        (stats,) = probe.stats.values()
        assert stats["size"] == path.stat().st_size, spelling
        assert (stats["opens"], stats["bytes_read"]) == (1, stats["size"]), spelling
        assert harness.blind_paths(probe.stats) == []
