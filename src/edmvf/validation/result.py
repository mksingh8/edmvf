from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ValidationIssue:
    kind: str
    dataset: str
    message: str
    row_number: int | None = None
    column_name: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "dataset": self.dataset,
            "message": self.message,
            "row_number": self.row_number,
            "column_name": self.column_name,
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
