from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CsvDataset:
    headers: list[str]
    rows: list[list[str]]
    # line_spans[0] is the header's (start_line, end_line); line_spans[i] is the
    # span of rows[i - 1]. 1-based physical lines, inclusive. Empty for an empty file.
    line_spans: list[tuple[int, int]] = field(default_factory=list)


class CsvFileAccessError(OSError):
    pass


class CsvParseError(ValueError):
    def __init__(
        self,
        path: str | Path,
        message: str,
        headers: list[str] | None = None,
        rows: list[list[str]] | None = None,
        row_number: int | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ):
        self.path = str(path)
        self.message = message
        self.row_number = row_number
        self.start_line = start_line
        self.end_line = end_line
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
        records: list[list[str]] = []
        line_spans: list[tuple[int, int]] = []
        previous_line = 0
        try:
            for record in reader:
                records.append(record)
                line_spans.append((previous_line + 1, reader.line_num))
                previous_line = reader.line_num
        except csv.Error as exc:
            # Locate the failing record: its ordinal (header = 1) and the lines it spans.
            raise CsvParseError(
                csv_path,
                f"Malformed CSV content in '{csv_path}': {exc}",
                row_number=len(records) + 1,
                start_line=previous_line + 1,
                end_line=reader.line_num,
            ) from exc

        if not records:
            return CsvDataset(headers=[], rows=[])
        return CsvDataset(headers=list(records[0]), rows=records[1:], line_spans=line_spans)
    except OSError as exc:
        raise CsvFileAccessError(f"Unable to read CSV file '{csv_path}': {exc}") from exc
    except UnicodeDecodeError as exc:
        # Content that cannot be decoded is a data problem, not an access problem.
        # Report the byte offset and codec reason only; never echo file content.
        raise CsvParseError(
            csv_path,
            f"CSV file '{csv_path}' is not valid UTF-8 (byte offset {exc.start}: {exc.reason}).",
        ) from exc
