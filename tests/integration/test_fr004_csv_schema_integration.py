from pathlib import Path

import pytest

from edmvf.config.loader import load_config
from edmvf.runner import run_scenario


def _write_csv(tmp_path: Path, filename: str, content: str) -> Path:
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    return path


def test_fr004_integration_valid_csv_pair_passes_schema_validation(tmp_path):
    source_path = _write_csv(tmp_path, "source.csv", "id,name\n1,Alpha\n")
    target_path = _write_csv(tmp_path, "target.csv", "name,id\nAlpha,1\n")
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        f"""
name: fr004_valid_csv_pair
version: 1
source:
  type: csv
  path: {source_path}
target:
  type: csv
  path: {target_path}
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

    scenario = load_config(config_path)
    result = run_scenario(scenario)

    assert result["status"] == "ready"
    assert result["schema_validation"]["passed"] is True
    assert result["structural_validation"]["passed"] is True


def test_fr004_integration_invalid_csv_pair_reports_schema_and_structural_issues(tmp_path):
    source_path = _write_csv(tmp_path, "source.csv", "id,name\n1\n")
    target_path = _write_csv(tmp_path, "target.csv", "id,email\n1,one@example.com\n")
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        f"""
name: fr004_invalid_csv_pair
version: 1
source:
  type: csv
  path: {source_path}
target:
  type: csv
  path: {target_path}
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

    scenario = load_config(config_path)
    result = run_scenario(scenario)

    assert result["status"] == "failed"
    assert result["structural_validation"]["passed"] is False
    assert result["schema_validation"]["passed"] is False
    assert any(issue["kind"] == "structural" for issue in result["structural_validation"]["issues"])
    assert any(issue["kind"] == "schema" for issue in result["schema_validation"]["issues"])


# --- IR-003: invalid UTF-8 through the full config -> runner path ---

def _scenario_for(tmp_path: Path, source_path: Path, target_path: Path):
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        f"""
name: ir003_invalid_utf8
version: 1
source:
  type: csv
  path: {source_path}
target:
  type: csv
  path: {target_path}
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
    return load_config(config_path)


@pytest.mark.parametrize("bad_side", ["source", "target"])
def test_ir003_run_scenario_reports_invalid_utf8_as_failed_not_exception(tmp_path, bad_side):
    bad = tmp_path / f"{bad_side}.csv"
    bad.write_bytes(b"id,name\n1,Caf\xe9\n")
    good = _write_csv(tmp_path, "good.csv", "id,name\n1,Cafe\n")
    source_path, target_path = (bad, good) if bad_side == "source" else (good, bad)

    result = run_scenario(_scenario_for(tmp_path, source_path, target_path))

    assert result["status"] == "failed"
    for check in ("schema_validation", "structural_validation"):
        assert result[check]["passed"] is False
        issues = result[check]["issues"]
        assert any(i["kind"] == "parse_error" and i["dataset"] == bad_side for i in issues)
        assert not any(i["kind"] == "file_access" for i in issues)
