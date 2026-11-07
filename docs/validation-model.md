# Validation Model

This project will model validation around a few core checks:

- completeness
- correctness
- consistency
- transformation fidelity
- reconciliation outcome

## Validation flow

1. Load source and target datasets.
2. Normalize schemas and values.
3. Run validation checks.
4. Aggregate issues and severities.
5. Produce a report suitable for review or automation.

## Result contract (current behavior)

`run_scenario` returns a dictionary. Each configured check has its own entry, keyed by the check name.

| Per-check `status` | Meaning |
|---|---|
| `passed` | The check ran and found no issues. |
| `failed` | The check ran and found at least one issue. |
| `not_implemented` | The check name is valid but not implemented yet. It did not run. |

The top-level `status` summarizes the whole run:

| `status` | When |
|---|---|
| `failed` | At least one executed check failed. A failure is never hidden by an unimplemented check. |
| `incomplete` | Nothing failed, but at least one configured check did not run (or no check ran at all). |
| `passed` | Every configured check ran and passed. |

`complete` is `true` only when every configured check actually ran. A failing check next to an unimplemented one gives `status: "failed"` and `complete: false`.

Executed entries contain `status`, `dataset`, `passed`, `issues`, `source_path` and `target_path`. Unimplemented entries contain `status` and `message`. Configuration errors (including duplicate check names) raise `ValueError` before any file is read. Data problems, such as a missing file or a malformed CSV, are reported as issues and never raised.

Each issue has `kind`, `dataset` (`source` or `target`), `message`, `row_number`, `column_name`, `start_line` and `end_line`. `row_number` is the 1-based ordinal of the logical CSV record (the header is record 1); `start_line` and `end_line` are the physical lines that record occupies. They are `null` when an issue has no record location.

The earlier value `ready` is no longer produced. A separate `error` status for access or runtime failures is planned.

## Selecting checks

- **YAML configuration and `Scenario.validation.checks`** must be a list of unique check names. An empty list, a non-list, an unknown name or a duplicate is a configuration error (`ValueError`) raised before any file is read.
- **The Python validators** (`validate_csv_dataset`, `validate_csv_schema`, `validate_csv_scenario`) take an optional `checks` argument. It may be a `list`, `tuple`, `set` or `frozenset` of check names, and the same rules apply to all four: it must not be empty, must contain only strings, and must not contain duplicates, and every name must be a check that function can run (`validate_csv_dataset` and `validate_csv_schema` accept `schema_validation` and `structural_validation`; `validate_csv_scenario` accepts the implemented checks). An explicit selection for `validate_csv_schema` must include `schema_validation`. Any other type (for example a bare string, bytes, a dict, a generator or a range) is outside the supported types and is rejected. Duplicate detection applies to lists and tuples; a set or frozenset cannot hold duplicates because the collection has already removed them. Every violation raises `ValueError` before any file is read.
- **`checks=None`** means each function's documented default: `validate_csv_dataset` runs header integrity and row widths; `validate_csv_schema` runs the column comparison; `validate_csv_scenario` runs the scenario's configured checks that are implemented. An explicit empty selection is never replaced by a default.
- A set has no order, so its names are sorted wherever they appear in an error message. Checks always execute in a fixed order (structural, then schema), whatever order the selection was given in.
