"""Synthetic benchmark for CSV validation reads, runtime and memory.

Subcommands: generate, run, compare, probe (see benchmarks/README.md).
Standard library only. Measures a synthetic envelope; it verifies no requirement.
"""
from __future__ import annotations

import argparse
import builtins
import datetime
import gc
import hashlib
import io
import json
import os
import pathlib
import platform
import random
import subprocess
import sys
import time
import tracemalloc

VARIANTS = ["clean", "width", "dup_header", "bad_utf8_late", "multiline"]
OPERATIONS = {
    "read": None,
    "structural": ["structural_validation"],
    "schema": ["schema_validation"],
    "both": ["structural_validation", "schema_validation"],
}
COLUMNS = ["id", "customer_code", "first_name", "last_name", "email", "country", "status", "amount", "created_on", "note"]
FIRST = ["Asha", "Ben", "Chen", "Dara", "Eli", "Farah", "Gus", "Hana"]
LAST = ["Stone", "Rivera", "Okafor", "Novak", "Silva", "Tanaka", "Weber", "Young"]
COUNTRY = ["US", "DE", "IN", "BR", "JP", "NG", "PL", "AU"]
STATUS = ["A", "B", "C", "D"]
LF = bytes([10])
SEED = 20260101


def code_hash(package_init: str) -> str:
    """sha256 over the package's .py files (relative path + LF-normalised content), so the
    measured code can be identified again from a commit (git archive) or the working tree."""
    root = os.path.dirname(os.path.abspath(package_init))
    digest = hashlib.sha256()
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name.endswith(".py"):
                full = os.path.join(folder, name)
                digest.update(os.path.relpath(full, root).replace(os.sep, "/").encode())
                with open(full, "rb") as handle:
                    digest.update(handle.read().replace(bytes([13, 10]), bytes([10])))
    return digest.hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- generate
def make_rows(n: int) -> list[bytes]:
    """Clean data rows (no trailing LF). Only Random.random() is used (stable across versions)."""
    rng = random.Random(SEED)
    base = datetime.date(2020, 1, 1).toordinal()
    rows = []
    for i in range(1, n + 1):
        a, b, c, d, e = (rng.random() for _ in range(5))
        first, last = FIRST[int(a * 8)], LAST[int(b * 8)]
        cents = int(c * 1000000)
        fields = [str(i), "CUST%07d" % i, first, last,
                  "%s.%s%d@example.com" % (first.lower(), last.lower(), i), COUNTRY[int(d * 8)],
                  STATUS[int(e * 4)], "%d.%02d" % (cents // 100, cents % 100),
                  datetime.date.fromordinal(base + i % 1500).isoformat(),
                  "b%02d r%06d note" % (i % 97, int(a * 1000000))]
        rows.append(",".join(fields).encode("ascii"))
    return rows


def variant_lines(variant: str, header: bytes, rows: list[bytes]) -> list[bytes]:
    """Lines (without LF) of the target file for one variant; defects are target-only."""
    header, rows = header, list(rows)
    if variant == "width":
        rows = [r.rsplit(b",", 1)[0] if (i + 1) % 1000 == 0 else r for i, r in enumerate(rows)]
    elif variant == "dup_header":
        header = header.rsplit(b",", 1)[0] + b",country"
    elif variant == "bad_utf8_late" and rows:
        rows[-1] = rows[-1].replace(b"@example", b"@exa" + bytes([255]) + b"mple")
    elif variant == "multiline":
        quote = bytes([34])
        for i in range(99, len(rows), 100):
            head, note = rows[i].rsplit(b",", 1)
            rows[i] = head + b"," + quote + note + LF + b"continued line" + quote
    return [header] + rows


def cmd_generate(args) -> int:
    os.makedirs(args.out, exist_ok=True)
    header = ",".join(COLUMNS).encode("ascii")
    manifest = {"seed": SEED, "columns": COLUMNS, "rows": args.rows, "variants": VARIANTS, "files": {}}
    for n in args.rows:
        folder = os.path.join(args.out, "data_%d" % n)
        os.makedirs(folder, exist_ok=True)
        rows = make_rows(n)
        files = {"source.csv": variant_lines("clean", header, rows)}
        for variant in VARIANTS:
            files["target_%s.csv" % variant] = variant_lines(variant, header, rows)
        for name, lines in files.items():
            path = os.path.join(folder, name)
            with open(path, "wb") as handle:
                handle.write(LF.join(lines) + LF)
            entry = {"sha256": sha256_file(path), "bytes": os.path.getsize(path)}
            manifest["files"]["data_%d/%s" % (n, name)] = entry
            print("%s  %10d  data_%d/%s" % (entry["sha256"], entry["bytes"], n, name))
    with open(os.path.join(args.out, "manifest.json"), "w") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
    return 0


# ------------------------------------------------------------- I/O probe
class _CountingFile:
    """Thin proxy over a file object counting seek() calls and bytes returned by reads."""

    def __init__(self, raw, stats):
        self._raw, self._stats = raw, stats

    def _count(self, data):
        # Supported: text read in strict mode, or with errors="surrogateescape" (the result is
        # unchanged and counted exactly). Not supported: text holding lone surrogates outside
        # the escape range U+DC80 to U+DCFF (for example from errors="surrogatepass"); counting
        # such text raises. EDMVF itself reads bytes, so it never produces them.
        self._stats["bytes_read"] += len(data) if isinstance(data, (bytes, bytearray)) else len(data.encode("utf-8", "surrogateescape"))
        return data

    def seek(self, *a, **k):
        self._stats["seeks"] += 1
        return self._raw.seek(*a, **k)

    def read(self, *a, **k):
        return self._count(self._raw.read(*a, **k))

    def read1(self, *a, **k):
        return self._count(self._raw.read1(*a, **k))

    def readline(self, *a, **k):
        return self._count(self._raw.readline(*a, **k))

    def readinto(self, buf):
        n = self._raw.readinto(buf)
        self._stats["bytes_read"] += n or 0
        return n

    def readinto1(self, buf):
        n = self._raw.readinto1(buf)
        self._stats["bytes_read"] += n or 0
        return n

    def readall(self):
        return self._count(self._raw.readall())

    def __next__(self):
        return self._count(next(self._raw))

    def readlines(self, *a, **k):
        lines = self._raw.readlines(*a, **k)
        for line in lines:
            self._count(line)
        return lines

    def __iter__(self):
        for line in self._raw:
            yield self._count(line)

    def __enter__(self):
        self._raw.__enter__()
        return self

    def __exit__(self, *exc):
        return self._raw.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._raw, name)


# Audit hooks cannot be removed, so one hook per process dispatches to the active probe. The
# active probe is kept on `sys` (not in this module) so that every loaded copy of this module,
# for example one loaded by file path in a test, shares it with the hook installed by the first.
def _audit_dispatch(event, args):
    probe = getattr(sys, "_edmvf_active_probe", None)
    if probe is not None:
        probe._audit(event, args)


class IoProbe:
    """Counts opens (audit hook) plus seeks and bytes read (open() proxy) per watched path."""

    def __init__(self, paths):
        # size is 0 for a path that is not a regular file (missing, a directory): there is no
        # byte count to check it against, so it can never make the probe call itself blind.
        self.stats = {}
        for path in paths:
            key = self._key(path)  # also used to match opens, so the size must come from the same path
            self.stats[key] = {"opens": 0, "seeks": 0, "bytes_read": 0, "size": os.path.getsize(key) if os.path.isfile(key) else 0}
        self.active = False

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.abspath(os.fsdecode(path)))

    def _audit(self, event, args):
        if self.active and event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            stats = self.stats.get(self._key(args[0]))
            if stats is not None:
                stats["opens"] += 1

    def _open(self, file, *a, **k):
        handle = self._real_open(file, *a, **k)
        if self.active and isinstance(file, (str, bytes, os.PathLike)):
            stats = self.stats.get(self._key(file))
            if stats is not None:
                return _CountingFile(handle, stats)
        return handle

    def _path_open(self, path, *a, **k):
        # Python 3.10's pathlib binds io.open when it is imported, so replacing io.open
        # at run time never reaches Path.read_bytes()/read_text(); wrap Path.open too.
        handle = self._real_path_open(path, *a, **k)
        if self.active and not isinstance(handle, _CountingFile):
            stats = self.stats.get(self._key(path))
            if stats is not None:
                return _CountingFile(handle, stats)
        return handle

    def __enter__(self):
        # Probes do not nest: the real functions are captured on entry, so a second entry would
        # capture a wrapper and an out-of-order exit would leave the wrappers installed.
        if getattr(sys, "_edmvf_active_probe", None) is not None:
            raise RuntimeError("an I/O probe is already active; probes cannot be nested or re-entered")
        self._real_open = io.open
        self._real_path_open = pathlib.Path.open
        if not getattr(sys, "_edmvf_audit_installed", False):
            sys.addaudithook(_audit_dispatch)  # audit hooks cannot be removed; one per process
            sys._edmvf_audit_installed = True
        sys._edmvf_active_probe = self
        io.open = builtins.open = self._open
        probe = self

        def path_open(path, *args, **kwargs):  # a plain function, so it binds as a method
            return probe._path_open(path, *args, **kwargs)

        pathlib.Path.open = path_open
        self.active = True
        return self

    def __exit__(self, *exc):
        self.active = False
        sys._edmvf_active_probe = None
        io.open = builtins.open = self._real_open
        pathlib.Path.open = self._real_path_open


def blind_paths(stats):
    """Paths that were opened but for which no byte was counted: the probe could not see
    the reads (for example because the file was read through an interface it does not wrap).
    A probe in that state proves nothing, so callers must treat any result here as an error."""
    return sorted(path for path, s in stats.items() if s["opens"] > 0 and s["size"] > 0 and s["bytes_read"] == 0)


def make_scenario(source, target, checks):
    from edmvf.config.models import DataSource, MatchingConfig, OutputConfig, Scenario, ValidationConfig
    return Scenario(name="benchmark", version=1, source=DataSource("csv", source), target=DataSource("csv", target),
                    matching=MatchingConfig(keys=["id"]), validation=ValidationConfig(checks=list(checks)),
                    output=OutputConfig(directory="./out"))


def count_reads(fn):
    """Run fn() with edmvf.validation.schema.read_csv_dataset wrapped; return (calls or None, result)."""
    import edmvf.validation.schema as schema
    original = getattr(schema, "read_csv_dataset", None)
    if original is None:
        return None, fn()
    calls = [0]

    def wrapper(*a, **k):
        calls[0] += 1
        return original(*a, **k)
    schema.read_csv_dataset = wrapper
    try:
        result = fn()
    finally:
        schema.read_csv_dataset = original
    return calls[0], result


def summarize(op, result):
    """Return (status, issue_count, digest) of a call result."""
    if op == "read":
        payload = [result.headers, result.rows, [list(s) for s in getattr(result, "line_spans", [])]]
        status, issues = "read", 0
    else:
        payload, status = result, result.get("status")

        def walk(node):
            if isinstance(node, dict):
                return (len(node["issues"]) if isinstance(node.get("issues"), list) else 0) + sum(walk(v) for v in node.values())
            return sum(walk(v) for v in node) if isinstance(node, list) else 0
        issues = walk(result)
    return status, issues, hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def peak_rss():
    """Peak process working set in bytes, or None when unavailable."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                                   "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                                   "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
            c = Counters()
            c.cb = ctypes.sizeof(c)
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi")
            psapi.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if not psapi.K32GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return None
            return int(c.PeakWorkingSetSize)
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(rss if sys.platform == "darwin" else rss * 1024)
    except Exception:
        return None


def measure(op, source, target, repeats):
    """Measure one configuration in this process (called inside a fresh subprocess)."""
    import edmvf
    from edmvf.data.csv_dataset import read_csv_dataset
    from edmvf.runner import run_scenario
    if op == "read":
        paths, fn = [source], lambda: read_csv_dataset(source)
    else:
        scenario = make_scenario(source, target, OPERATIONS[op])
        paths, fn = [source, target], lambda: run_scenario(scenario)
    rss0 = peak_rss()
    times = []
    for _ in range(repeats):
        gc.collect()
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    rss1 = peak_rss()
    gc.collect()
    tracemalloc.start()
    result = fn()
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    with IoProbe(paths) as probe:
        reads, _ = count_reads(fn)
    if blind_paths(probe.stats):
        raise RuntimeError("I/O probe is blind (opens counted, no bytes counted): %s" % blind_paths(probe.stats))
    status, issues, digest = summarize(op, result)
    return {"op": op, "wall_best_s": min(times), "wall_all_s": times, "peak": peak, "retained": retained,
            "transient": peak - retained, "reads": reads, "io": probe.stats, "status": status,
            "issue_count": issues, "digest": digest,
            "rss_peak_delta": None if rss0 is None or rss1 is None else rss1 - rss0, "edmvf_file": edmvf.__file__}


def cmd_worker(args) -> int:
    print(json.dumps(measure(args.op, args.source, args.target, args.repeats)))
    return 0


def cmd_probe(args) -> int:
    from edmvf.runner import run_scenario
    scenario = make_scenario(args.source, args.target, args.checks)
    with IoProbe([args.source, args.target]) as probe:
        reads, result = count_reads(lambda: run_scenario(scenario))
    if blind_paths(probe.stats):
        print("I/O probe is blind (opens counted, no bytes counted): %s" % blind_paths(probe.stats), file=sys.stderr)
        return 2
    print(json.dumps({"reads": reads, "per_path": probe.stats, "status": result.get("status")}, indent=2))
    return 0


# --------------------------------------------------------------- run
def cmd_run(args) -> int:
    manifest_path = args.data if args.data.endswith(".json") else os.path.join(args.data, "manifest.json")
    root = os.path.dirname(os.path.abspath(manifest_path))
    with open(manifest_path) as handle:
        manifest = json.load(handle)
    sizes = args.rows or manifest["rows"]
    variants = args.variant or VARIANTS
    import edmvf
    out = {"label": args.label, "repeats": args.repeats, "edmvf_file": edmvf.__file__, "code_hash": code_hash(edmvf.__file__),
           "machine": {"platform": platform.platform(), "processor": platform.processor(), "machine": platform.machine(),
                       "python": sys.version, "cpu_count": os.cpu_count()},
           "dataset_hashes": {}, "results": []}
    for n in sizes:
        folder = os.path.join(root, "data_%d" % n)
        source = os.path.join(folder, "source.csv")
        for variant in variants:
            target = os.path.join(folder, "target_%s.csv" % variant)
            for key in ("data_%d/source.csv" % n, "data_%d/target_%s.csv" % (n, variant)):
                actual = sha256_file(os.path.join(root, key))
                if actual != manifest["files"][key]["sha256"]:
                    print("dataset file changed since generate: %s" % key, file=sys.stderr)
                    return 2
                out["dataset_hashes"][key] = actual
            for op in OPERATIONS:
                if op == "read" and variant != "clean":
                    continue
                cmd = [sys.executable, os.path.abspath(__file__), "_worker", "--op", op, "--source", source,
                       "--target", target, "--repeats", str(args.repeats)]
                proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy())
                if proc.returncode != 0:
                    print("worker failed: rows=%d variant=%s op=%s%s%s" % (n, variant, op, os.linesep, proc.stderr[-2000:]), file=sys.stderr)
                    return 1
                record = json.loads(proc.stdout.strip().splitlines()[-1])
                record.update(rows=n, variant=variant)
                out["results"].append(record)
    with open(args.json, "w") as handle:
        json.dump(out, handle, indent=2)
    print_table(out["results"])
    return 0


def print_table(results):
    print("| rows | variant | op | best s | peak MB | retained MB | transient MB | reads | opens | seeks | bytes/size | status | issues |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        io_ = list(r["io"].values())
        ratio = "%.2f" % (sum(p["bytes_read"] for p in io_) / max(1, sum(p["size"] for p in io_)))
        print("| %d | %s | %s | %.3f | %.1f | %.1f | %.1f | %s | %s | %s | %s | %s | %d |" % (
            r["rows"], r["variant"], r["op"], r["wall_best_s"], r["peak"] / 1e6, r["retained"] / 1e6, r["transient"] / 1e6,
            r["reads"], "+".join(str(p["opens"]) for p in io_), sum(p["seeks"] for p in io_), ratio, r["status"], r["issue_count"]))


# ------------------------------------------------------------- compare
def pct(before, after):
    return None if not before else (after - before) * 100.0 / before


def cmd_compare(args) -> int:
    def load(path):
        with open(path) as handle:
            return {(r["rows"], r["variant"], r["op"]): r for r in json.load(handle)["results"]}
    before, after = load(args.before), load(args.after)
    mismatches = flags = 0
    fmt = lambda v: "n/a" if v is None else "%+.1f%%" % v
    print("| rows | variant | op | wall | peak | transient | reads | opens | seeks | bytes_read | digest |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for key in sorted(set(before) & set(after)):
        b, a = before[key], after[key]
        tot = lambda r, f: sum(p[f] for p in r["io"].values())
        same = b["digest"] == a["digest"]
        mismatches += not same
        wall, peak, trans = (pct(b[f], a[f]) for f in ("wall_best_s", "peak", "transient"))
        notes = []
        if args.min_read_peak_reduction is not None and key[2] == "read" and peak is not None and -peak < args.min_read_peak_reduction:
            notes.append("read peak reduction below target")
        # The run-peak target is defined for the schema and both-check runs (the ones that
        # held two datasets at once). A structural-only run held one dataset before and
        # after, and with a late invalid byte the decode fails before the second input is
        # read, so neither is expected to reach it: those are reported, not flagged.
        run_target_applies = key[2] in ("schema", "both") and key[1] != "bad_utf8_late"
        if args.min_run_peak_reduction is not None and run_target_applies and peak is not None and -peak < args.min_run_peak_reduction:
            notes.append("run peak reduction below target")
        # Wall times below 10K rows are milliseconds and dominated by noise: not flagged.
        if args.max_slowdown is not None and key[0] >= 10000 and wall is not None and wall > args.max_slowdown:
            notes.append("slowdown above limit")
        flags += bool(notes)
        print("| %d | %s | %s | %s | %s | %s | %s->%s | %d->%d | %d->%d | %d->%d | %s%s |" % (
            key + (fmt(wall), fmt(peak), fmt(trans), b["reads"], a["reads"], tot(b, "opens"), tot(a, "opens"),
                   tot(b, "seeks"), tot(a, "seeks"), tot(b, "bytes_read"), tot(a, "bytes_read"),
                   "same" if same else "DIFFERENT", " FLAG: " + "; ".join(notes) if notes else "")))
    print("\nfindings digest mismatches: %d; target flags: %d" % (mismatches, flags))
    return 1 if mismatches else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("generate")
    p.add_argument("--out", required=True)
    p.add_argument("--rows", type=int, nargs="+", default=[1000, 10000, 100000])
    p = sub.add_parser("run")
    p.add_argument("--data", required=True)
    p.add_argument("--rows", type=int, nargs="+")
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--json", required=True)
    p.add_argument("--variant", nargs="+", choices=VARIANTS)
    p.add_argument("--label", default=None)
    p = sub.add_parser("compare")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--min-read-peak-reduction", type=float)
    p.add_argument("--min-run-peak-reduction", type=float)
    p.add_argument("--max-slowdown", type=float)
    p = sub.add_parser("probe")
    p.add_argument("--source", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--checks", nargs="+", default=["structural_validation", "schema_validation"])
    p = sub.add_parser("_worker")
    p.add_argument("--op", choices=list(OPERATIONS), required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args(argv)
    return {"generate": cmd_generate, "run": cmd_run, "compare": cmd_compare, "probe": cmd_probe, "_worker": cmd_worker}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
