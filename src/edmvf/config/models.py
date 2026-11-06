from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Models map raw configuration values; they never coerce. Type and value rules
# live in config.validator.validate_scenario so that YAML-loaded and directly
# constructed scenarios obey one contract.


def _reject_unknown_keys(mapping: dict[Any, Any], allowed: set[str], section: str) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise ValueError(f"unknown configuration key(s) in {section}: {unknown}")


@dataclass(frozen=True)
class DataSource:
    type: str
    path: str

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None, role: str = "source") -> "DataSource":
        if not isinstance(mapping, dict):
            raise ValueError(f"{role} must be a mapping with type and path, got {type(mapping).__name__}")
        _reject_unknown_keys(mapping, {"type", "path"}, role)
        return cls(type=mapping.get("type"), path=mapping.get("path"))


@dataclass(frozen=True)
class MatchingConfig:
    keys: list[str]

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "MatchingConfig":
        if not isinstance(mapping, dict):
            raise ValueError(f"matching must be a mapping with key fields, got {type(mapping).__name__}")
        _reject_unknown_keys(mapping, {"keys"}, "matching")
        return cls(keys=mapping.get("keys"))


@dataclass(frozen=True)
class ValidationConfig:
    checks: list[str]

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "ValidationConfig":
        if not isinstance(mapping, dict):
            raise ValueError(f"validation must be a mapping with checks, got {type(mapping).__name__}")
        _reject_unknown_keys(mapping, {"checks"}, "validation")
        return cls(checks=mapping.get("checks"))


@dataclass(frozen=True)
class OutputConfig:
    directory: str

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "OutputConfig":
        if not isinstance(mapping, dict):
            raise ValueError(f"output must be a mapping with a directory, got {type(mapping).__name__}")
        _reject_unknown_keys(mapping, {"directory"}, "output")
        return cls(directory=mapping.get("directory"))


@dataclass(frozen=True)
class Scenario:
    name: str
    version: int | str | None
    source: DataSource
    target: DataSource
    matching: MatchingConfig
    validation: ValidationConfig
    output: OutputConfig

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "Scenario":
        if not isinstance(mapping, dict):
            raise ValueError(f"scenario configuration must be a mapping, got {type(mapping).__name__}")
        _reject_unknown_keys(
            mapping,
            {"name", "version", "source", "target", "matching", "validation", "output"},
            "scenario",
        )

        for section in ("source", "target", "matching", "validation", "output"):
            if mapping.get(section) is None:
                raise ValueError(f"{section} is required")

        return cls(
            name=mapping.get("name"),
            version=mapping.get("version"),
            source=DataSource.from_mapping(mapping["source"], role="source"),
            target=DataSource.from_mapping(mapping["target"], role="target"),
            matching=MatchingConfig.from_mapping(mapping["matching"]),
            validation=ValidationConfig.from_mapping(mapping["validation"]),
            output=OutputConfig.from_mapping(mapping["output"]),
        )
