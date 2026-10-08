from __future__ import annotations

import csv
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
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle, strict=True)
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
    except csv.Error as exc:
        raise CsvParseError(csv_path, f"Malformed CSV content in '{csv_path}': {exc}") from exc
