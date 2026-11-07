"""IR-006: run status and completeness contract.

Per-check status: passed | failed | not_implemented.
Overall status:   any failed -> failed; else any not_implemented -> incomplete;
                  else passed. `complete` is True only when every configured
                  check actually executed (passed or failed).
The legacy value "ready" is gone.
"""

import json
from pathlib import Path

import pytest
import yaml

from edmvf.config.loader import load_config
from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
from edmvf.runner import run_scenario

CLEAN = "id,name\n1,Alpha\n"
DRIFT = "id,email\n1,a@example.com\n"          # column drift vs CLEAN
WIDTH_BAD = "id,name\n1,Alpha,extra\n"          # structural defect


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8", newline="")
    return path


def _scenario(source, target, checks) -> Scenario:
    return Scenario(
        name="ir006_status",
        version=1,
        source=DataSource("csv", str(source)),
        target=DataSource("csv", str(target)),
        matching=MatchingConfig(keys=["id"]),
        validation=ValidationConfig(checks=list(checks)),
        output=OutputConfig(directory="./out"),
    )


def _run(tmp_path, source_content, target_content, checks):
    source = _write(tmp_path, "s.csv", source_content) if source_content is not None else tmp_path / "absent-s.csv"
    target = _write(tmp_path, "t.csv", target_content) if target_content is not None else tmp_path / "absent-t.csv"
    return run_scenario(_scenario(source, target, checks))


# (label, source, target, checks, overall, complete, per-check statuses)
PRECEDENCE = [
    ("all_pass", CLEAN, CLEAN, ["schema_validation", "structural_validation"], "passed", True,
     {"schema_validation": "passed", "structural_validation": "passed"}),
    ("one_fails", CLEAN, DRIFT, ["schema_validation"], "failed", True, {"schema_validation": "failed"}),
    ("pass_and_fail", CLEAN, WIDTH_BAD, ["schema_validation", "structural_validation"], "failed", True,
     {"schema_validation": "passed", "structural_validation": "failed"}),
    ("unimplemented_only", CLEAN, CLEAN, ["missing_records"], "incomplete", False,
     {"missing_records": "not_implemented"}),
    ("pass_plus_unimplemented", CLEAN, CLEAN, ["schema_validation", "missing_records"], "incomplete", False,
     {"schema_validation": "passed", "missing_records": "not_implemented"}),
    ("fail_plus_unimplemented", CLEAN, DRIFT, ["schema_validation", "missing_records"], "failed", False,
     {"schema_validation": "failed", "missing_records": "not_implemented"}),
    ("missing_source_plus_unimplemented", None, CLEAN, ["structural_validation", "duplicate_records"], "failed", False,
     {"structural_validation": "failed", "duplicate_records": "not_implemented"}),
    ("many_unimplemented", CLEAN, CLEAN, ["missing_records", "duplicate_records", "mismatched_values"], "incomplete", False,
     {"missing_records": "not_implemented", "duplicate_records": "not_implemented", "mismatched_values": "not_implemented"}),
]


@pytest.mark.parametrize("case", PRECEDENCE, ids=[case[0] for case in PRECEDENCE])
def test_overall_status_precedence_and_completeness(tmp_path, case):
    _, source, target, checks, overall, complete, per_check = case

    result = _run(tmp_path, source, target, checks)

    assert result["status"] == overall
    assert result["complete"] is complete
    assert {name: result[name]["status"] for name in checks} == per_check


def test_failing_check_plus_unimplemented_check_is_failed_and_incomplete_from_yaml(tmp_path):
    _write(tmp_path, "s.csv", CLEAN)
    _write(tmp_path, "t.csv", DRIFT)
    config = {
        "name": "mixed",
        "version": 1,
        "source": {"type": "csv", "path": str(tmp_path / "s.csv")},
        "target": {"type": "csv", "path": str(tmp_path / "t.csv")},
        "matching": {"keys": ["id"]},
        "validation": {"checks": ["schema_validation", "missing_records"]},
        "output": {"directory": "./out"},
    }
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    result = run_scenario(load_config(path))

    assert (result["status"], result["complete"]) == ("failed", False)
    assert result["schema_validation"]["passed"] is False
    assert result["missing_records"]["status"] == "not_implemented"


def test_executed_entries_have_status_and_keep_their_existing_fields(tmp_path):
    result = _run(tmp_path, CLEAN, CLEAN, ["schema_validation", "structural_validation"])

    for name in ("schema_validation", "structural_validation"):
        entry = result[name]
        assert entry["status"] == "passed"
        assert entry["passed"] is True
        assert {"dataset", "passed", "issues", "source_path", "target_path"} <= set(entry)
    failing = _run(tmp_path, CLEAN, DRIFT, ["schema_validation"])["schema_validation"]
    assert (failing["status"], failing["passed"]) == ("failed", False)


def test_unimplemented_entry_shape_and_message(tmp_path):
    entry = _run(tmp_path, CLEAN, CLEAN, ["missing_records"])["missing_records"]

    assert set(entry) == {"status", "message"}
    assert entry["status"] == "not_implemented"
    assert "'missing_records'" in entry["message"]
    assert "FR-" not in entry["message"]


def test_no_executed_check_never_reports_passed(tmp_path):
    result = _run(tmp_path, CLEAN, CLEAN, ["missing_records", "duplicate_records"])

    assert result["status"] != "passed"
    assert result["complete"] is False


def test_legacy_ready_value_never_appears(tmp_path):
    for checks, target in [(["schema_validation"], CLEAN), (["schema_validation"], DRIFT), (["missing_records"], CLEAN)]:
        result = _run(tmp_path, CLEAN, target, checks)
        statuses = [result["status"]] + [v["status"] for v in result.values() if isinstance(v, dict)]
        assert "ready" not in statuses
        assert result["status"] in {"passed", "failed", "incomplete"}


def test_payload_is_deterministic_and_json_serializable(tmp_path):
    first = _run(tmp_path, CLEAN, DRIFT, ["missing_records", "schema_validation", "structural_validation"])
    second = _run(tmp_path, CLEAN, DRIFT, ["missing_records", "schema_validation", "structural_validation"])

    assert json.dumps(first) == json.dumps(second)
    assert json.loads(json.dumps(first)) == first
    assert list(first)[:7] == [
        "scenario_name", "source_type", "target_type", "status", "complete", "matching_keys", "validation_checks",
    ]


def test_missing_target_plus_unimplemented_is_failed_not_raised(tmp_path):
    result = _run(tmp_path, CLEAN, None, ["structural_validation", "missing_records"])

    assert (result["status"], result["complete"]) == ("failed", False)
    issues = result["structural_validation"]["issues"]
    assert [(i["kind"], i["dataset"]) for i in issues] == [("file_access", "target")]


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], ("incomplete", False)),
        (["passed"], ("passed", True)),
        (["failed"], ("failed", True)),
        (["not_implemented"], ("incomplete", False)),
        (["passed", "not_implemented"], ("incomplete", False)),
        (["failed", "not_implemented"], ("failed", False)),
        (["not_implemented", "failed", "passed"], ("failed", False)),
        (["passed", "passed", "failed"], ("failed", True)),
    ],
)
def test_overall_aggregation_table(statuses, expected):
    from edmvf.runner import _overall

    assert _overall(statuses) == expected


@pytest.mark.parametrize(
    "statuses", [["error"], ["passed", "skipped"], ["failed", "bogus"], [""], ["passed", None, 1], ["passed", ["x"]]]
)
def test_overall_never_treats_an_unknown_status_as_a_pass(statuses):
    from edmvf.runner import _overall

    with pytest.raises(ValueError, match="unknown check status"):
        _overall(statuses)
