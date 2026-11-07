# CSV validation benchmark

A small, standard-library-only harness that measures how `read_csv_dataset` and `run_scenario` behave on deterministic synthetic CSV files: how many times each input is read, how long a run takes, and how much Python memory it allocates. It compares a "before" and an "after" version of the code using the same data, script, machine and interpreter.

This measures a synthetic envelope only. It does not verify any non-functional requirement by itself; a requirement is verified only by its own acceptance criteria and recorded evidence.

## 1. Generate the data

```
python benchmarks/csv_validation_benchmark.py generate --out <data_dir> --rows 1000 10000 100000
```

The output is deterministic (fixed seed, only `random.Random.random()`, LF line endings, written in binary), so the same arguments give byte-identical files. The command prints the sha256 and size of every file and writes `<data_dir>/manifest.json`. Generate once and reuse the same folder for both measurements.

| Variant (target file) | Defect, present only in the target; `source.csv` is always the clean file |
|---|---|
| `clean` | none |
| `width` | every 1000th data row has one value fewer |
| `dup_header` | the header repeats a column name |
| `bad_utf8_late` | an invalid byte (0xFF) inside the last row |
| `multiline` | every 100th `note` field is quoted and contains a line break |

Columns: `id, customer_code, first_name, last_name, email, country, status, amount, created_on, note` (about 98 bytes per row). Files are `data_<N>/source.csv` and `data_<N>/target_<variant>.csv`.

## 2. Run before and after

BEFORE uses a scratch copy of the earlier commit; AFTER uses the working tree. Use the same data folder, the same interpreter, the same machine and no other heavy processes.

```
git archive <commit> src | tar -x -C <scratch>        # scratch must exist
PYTHONPATH=<scratch>/src python benchmarks/csv_validation_benchmark.py run \
    --data <data_dir> --rows 1000 10000 100000 --repeats 5 --json before.json --label <commit>

python benchmarks/csv_validation_benchmark.py run \
    --data <data_dir> --rows 1000 10000 100000 --repeats 5 --json after.json --label <commit-or-branch>

python benchmarks/csv_validation_benchmark.py compare before.json after.json \
    --min-read-peak-reduction 25 --min-run-peak-reduction 50 --max-slowdown 10
```

Check that `edmvf_file` in each JSON points at the code you meant to measure. `--variant` limits the target variants.

Each (rows, variant, operation) configuration runs in a fresh subprocess. Operations: `read` (`read_csv_dataset` on `source.csv`, measured once per size), `structural`, `schema` and `both` (`run_scenario` on source and target with the named checks).

`compare` prints the percentage change of wall time, peak and transient memory, and logical reads and physical opens, seeks and bytes read before to after. It exits non-zero if any findings digest differs between the two files for the same configuration. The optional targets (percent) only add a FLAG note. `--min-read-peak-reduction` applies to the `read` operation; `--min-run-peak-reduction` applies to the `schema` and `both` operations, except for the `bad_utf8_late` variant (the decode fails before the second input is read, so a run never holds two datasets either way); `--max-slowdown` applies from 10,000 rows upward (smaller timings are noise). A single flagged wall-time figure should be re-measured, alternating before and after on an idle machine, before it is read as a regression.

`probe --source S --target T --checks ...` runs one `run_scenario` under the physical I/O probe and prints JSON `{reads, per_path, status}`.

## 3. What is measured

- **Wall time**: best of N runs (`time.perf_counter`), tracemalloc off, `gc.collect()` before each run.
- **peak**: `tracemalloc` peak bytes between `tracemalloc.start()` (called right after `gc.collect()`) and the moment the call returns, with the call's return value still referenced when the peak is read.
- **retained**: `tracemalloc` current bytes after the call returns with the return value still referenced.
- **transient**: `peak - retained`, the allocations made during the call and freed before it returned (bytes buffer, decoded text, text-stream buffers, parser state), excluding the returned object.
- **Ratio of run peak to a single read peak**: the tracemalloc peak of the call being measured divided by the tracemalloc peak of `read_csv_dataset` on one input of the same size, both measured by the same method.
- For `run_scenario` the return value is the result dict. These are Python allocations only, not process memory.
- **Logical reads** (`reads`): calls of `edmvf.validation.schema.read_csv_dataset`, counted by a wrapper on that module attribute (`null` if the attribute does not exist).
- **Peak RSS delta**: peak working set minus the value before the timing runs. Informational only; it covers the timing runs and is not comparable to tracemalloc figures.
- **Findings digest**: sha256 of `json.dumps(result, sort_keys=True, default=str)`; for `read` of `(headers, rows, line_spans)`. Equal digests mean identical findings.
- **Machine and code**: platform, processor, machine, Python version, CPU count, `edmvf.__file__`, `--label`, a `code_hash` (sha256 over the package's `.py` files, relative path plus line-ending-normalised content, so the measured code can be identified again from a commit or `git archive`) and the sha256 of every dataset file used, computed from the files actually measured. `run` stops with status 2 if a file no longer matches `manifest.json`.

### Physical I/O probe

In a separate run in the same subprocess (so timing and tracemalloc are not disturbed), an audit hook counts `open` events whose path equals an input path, and `io.open`/`builtins.open` are replaced by a wrapper that returns a thin proxy counting `seek()` calls and bytes returned by `read`, `readall`, `readinto`, `readinto1`, `read1`, `readline`, `readlines` and iteration (`next` included). Reported per input path: `opens`, `seeks`, `bytes_read`, `size`. A single sequential pass shows `opens == 1`, `seeks == 0` and `bytes_read == size`; opening twice or rewinding shows up as larger numbers. The proxy is installed on `open`, `io.open`, `builtins.open` and `pathlib.Path.open` (Python 3.10 binds `io.open` inside `pathlib` when it is imported, so `Path.open` is wrapped separately; the tests run on 3.10 and 3.11). Opens made through `os.open` are counted (they raise the same audit event) but their reads and seeks are not. Not seen: reads or seeks made on `.buffer` or `.raw` of an opened file, `peek`, and `os.lseek`/`os.pread` on a file descriptor. Bytes read from text-mode files are counted as the UTF-8 length of the returned text. Paths are compared after `abspath`/`normcase`, so hard links and symlinks to the same file are not merged. If a file is opened but no byte is counted for it, the probe reports itself blind and `probe` exits with status 2 (in `run`, the worker raises, `run` prints `worker failed` and exits with status 1) instead of reporting `seeks == 0`; a blind probe proves nothing. A path that is not a regular file (missing, or a directory) has size 0 and is never reported as blind; a regular file whose open fails is reported as blind, which is a loud failure and not a false pass.
