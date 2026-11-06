from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ValidationIssue:
    kind: str
    dataset: str
    message: str
    row_number: int | None = None
    column_name: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    # row_number is the 1-based ordinal of the logical CSV record (header = 1,
    # first data record = 2); line breaks inside quoted fields do not advance
    # it. start_line/end_line are the inclusive 1-based physical lines that
    # record occupies; only "\n", "\r\n" and a lone "\r" end a line. For a
    # CSV parse error, end_line is the line where parsing stopped, which can
    # precede the real end of the record. All three are None when an issue has
    # no record location.

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "dataset": self.dataset,
            "message": self.message,
            "row_number": self.row_number,
            "column_name": self.column_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass
class SchemaValidationResult:
    dataset: str
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    source_path: str | None = None
    target_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "passed": self.passed,
            "issues": [issue.to_dict() for issue in self.issues],
            "source_path": self.source_path,
            "target_path": self.target_path,
        }
