from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CsvDataset:
    headers: list[str]
    rows: list[list[str]]


class CsvFileAccessError(OSError):
    pass


class CsvParseError(ValueError):
    def __init__(self, path: str | Path, message: str, headers: list[str] | None = None, rows: list[list[str]] | None = None):
        self.path = str(path)
        self.message = message
        self.headers = list(headers or [])
        self.rows = [list(row) for row in (rows or [])]
        super().__init__(message)


def read_csv_dataset(path: str | Path) -> CsvDataset:
    csv_path = Path(path)

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    try:
        # Decode the whole file at once so a UnicodeDecodeError carries the
        # absolute byte offset (incremental text-mode decoding reports an offset
        # relative to an internal buffer chunk).
        text = csv_path.read_bytes().decode("utf-8")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            headers = next(reader)
        except StopIteration:
            return CsvDataset(headers=[], rows=[])

        rows: list[list[str]] = []
        for row in reader:
            rows.append(row)
        return CsvDataset(headers=list(headers), rows=rows)
    except OSError as exc:
        raise CsvFileAccessError(f"Unable to read CSV file '{csv_path}': {exc}") from exc
    except UnicodeDecodeError as exc:
        # Content that cannot be decoded is a data problem, not an access problem.
        # Report the byte offset and codec reason only; never echo file content.
        raise CsvParseError(
            csv_path,
            f"CSV file '{csv_path}' is not valid UTF-8 (byte offset {exc.start}: {exc.reason}).",
        ) from exc
    except csv.Error as exc:
        raise CsvParseError(csv_path, f"Malformed CSV content in '{csv_path}': {exc}") from exc
