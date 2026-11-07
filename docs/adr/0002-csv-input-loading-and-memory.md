# ADR 0002: CSV input loading: one read per input per run, and how the data is held in memory

- Status: Accepted (project owner, 2026-11-07)
- Related: IR-005, IR-012, FR-004; partial evidence for NFR-001, NFR-003 and NFR-004 only

## Context

- With both `structural_validation` and `schema_validation` selected, each input file was read, decoded and parsed twice (4 reads and 4 opens for a source/target pair). A single check already read each file once.
- `read_csv_dataset` decoded the whole file to a `str`, wrapped it in `io.StringIO` (a second internal copy at up to 4 bytes per character) and then built the rows. The schema and both-check runs also kept both datasets alive at once.
- Measured on a synthetic 100,000-row, 10-column file (9.79 MB) with `tracemalloc`: one `read_csv_dataset` peaked at 134.2 MB, and a schema or both-check run at 218.5 MB, about 1.63 times a single read.
- Two earlier decisions constrain the fix: an invalid UTF-8 byte must be reported with its absolute byte offset and must take precedence over a CSV error (IR-003), and issues carry the logical record ordinal and the physical line span (IR-009).

## Options

A. **Remove the duplicate reads only.** Load both datasets once and keep them. Reads halve; the peak does not improve.

B. **Validate the whole byte buffer as UTF-8 first, parse through a chunked text stream over the same bytes, and process each input sequentially.** The decoded text is discarded after validation, so no full-size `str` or `StringIO` copy sits next to the rows. Each input is read once, all selected checks run on that read, and only its headers and issues are kept before the other input is loaded. `CsvDataset` is unchanged (it still holds all rows and line spans).

C. **Full streaming.** Parse incrementally from the file with an incremental decoder, possibly keeping only row widths. Lowest memory, but it needs offset tracking and error-precedence handling, a second parse path or a change to what `CsvDataset` holds, and it conflicts with the record matching planned in FR-005.

## Decision

Option B.

- The public functions keep their signatures and results; `schema.py` still reads through its own module-level `read_csv_dataset`.
- Each input is opened once, never rewound, and read once per run, whatever combination of checks is selected. A source and a target that are the same path are read once per role.
- Findings, messages, locations and error classification are unchanged. The reader is checked by a differential test against a frozen copy of the previous reader (including chunk-boundary inputs and seeded random inputs). The scenario-level results are checked against the public path-based validators, which share the new helper functions, so that check shows consistency and not independence; the equivalence of the whole pipeline with the previous release was verified by a one-off differential run of the previous and the new code (15,718 outcomes, 0 differences), and the older FR-004 tests pin the findings with hand-written expectations.

### How memory is measured (used by the tests and by `benchmarks/`)

The same definitions are repeated in `benchmarks/README.md` and in the docstring of `tests/unit/test_nfr004_memory_bounds.py`; the harness and the tests use one method.

All figures are `tracemalloc` (Python allocations only, not process RSS), started right after `gc.collect()` and stopped when the call returns, with the call's return value still referenced.

- **peak**: the `tracemalloc` peak during the call.
- **retained**: `tracemalloc` current memory when the call returns (the returned object).
- **transient**: `peak - retained`: memory allocated during the call and freed before it returned (the file bytes, decoded text, text-stream buffers, parser state), excluding the returned object.
- **Transient reader memory** guard: `transient` of `read_csv_dataset` is at most 2.0 times the input size in bytes.
- **"1.25 times a single read"** guard: the `peak` of a `run_scenario` (or `validate_csv_schema`) call on a source and a target of the same size is at most 1.25 times the `peak` of `read_csv_dataset` on one input of that size, measured the same way. It holds only if one input's dataset is released before the other is loaded.

## Rationale

Written by the project owner: sequential processing reduces repeated reads and peak memory while preserving the existing dataset interface and diagnostic behavior. Full streaming adds complexity beyond the current need; reassess storage when record matching requires it.

## Consequences

- Memory still grows with file size (a dataset holds all rows). Record matching (FR-005) will need both sides' keys at once and should revisit per-side release, for example by keeping a key index.
- The structural and schema results of one run are always consistent with each other, because they come from the same read.
- The synthetic `benchmarks/` harness is the place to measure runtime and memory; it does not verify a non-functional requirement by itself.
- The decoded text is deliberately discarded after validation; removing that step would change which error wins and the reported byte offset.
