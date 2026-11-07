# ADR 0001: Run result status and completeness contract

- Status: Proposed (the rationale below is still to be written)
- Accepted decision: tuple support for Python check selections (project owner, 2026-11-07). The Python validators accept a list, tuple, set or frozenset.
- Date: 2026-11-07
- Related: IR-006, FR-003, FR-004, FR-027 (partial)

## Context

`run_scenario` reported an overall status of `ready`, `failed` or `not_implemented`.

- A configured check that is not implemented replaced the overall status, so a real failure was hidden behind `not_implemented`.
- `ready` read as "about to run" rather than "validated".
- Executed and unavailable checks had different entry shapes. The lower-level validators handled a missing or empty check selection inconsistently: `validate_csv_schema` without `schema_validation` returned a pass without reading any file, `validate_csv_dataset` with no checks read the file but ran no check, and `validate_csv_scenario` with an empty selection silently fell back to the configured checks.
- There are no external consumers of the payload yet, so changing it now is cheap. The first release also needs a clear pass/fail outcome that automation can read.

## Options

1. **Keep the legacy strings, fix only the precedence.** Add a per-check `status` and a `complete` flag but keep `ready` and `not_implemented`. No existing assertion breaks. `ready` stays ambiguous and `not_implemented` keeps mixing a per-check reason with run completeness.
2. **Rename now.** Overall `passed` / `failed` / `incomplete` plus a `complete` flag; per-check `passed` / `failed` / `not_implemented`. A breaking change to the payload, with only a handful of test assertions affected. New statuses (`error`, `skipped`) can be added later without breaking anything.
3. **Rename and also version and nest the payload** (`result_version`, checks under a `checks` key). Future-proof, but it breaks every `result["<check>"]` access and has no persisted consumer until the JSON report exists.

## Decision

Option 2.

- Overall status: any `failed` gives `failed`; otherwise any `not_implemented` (or nothing executed) gives `incomplete`; otherwise `passed`. `complete` is true only when every configured check ran.
- Duplicate check names are rejected as a configuration error. An empty, unknown, unsupported or duplicated explicit selection at the lower-level validators raises `ValueError`; `None` means each function's documented default and an empty selection never falls back to it.
- The Python validators accept a list, tuple, set or frozenset of check names and apply the same rules to all of them; any other type (for example a bare string, bytes, a dict, a generator or a range) is outside the supported types and is rejected. Duplicate detection applies to lists and tuples, because a set or frozenset has already removed duplicates. YAML and `Scenario.validation.checks` stay lists. Sets are sorted in messages, and execution order is fixed.
- Calling `validate_csv_dataset` or `validate_csv_schema` without `checks` now runs the documented default (header and width checks; the column comparison) instead of running nothing or passing without reading.
- `error` (access or runtime failure, NFR-009), `result_version` and the nested layout are deferred.

## Rationale

To be written by the project owner.

## Consequences

- Consumers read `status` together with `complete`; `incomplete` never implies success.
- `error` can later rank above `failed` without changing existing values.
- A future CLI maps the overall status to exit codes; configuration errors remain exceptions.
- The documented FR-004 behavior "selecting neither check preserves prior behavior" now yields overall `incomplete` instead of `not_implemented`.
