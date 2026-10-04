from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DataSource:
    type: str
    path: str

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "DataSource":
        if not isinstance(mapping, dict):
            raise ValueError("source must be a mapping with type and path")
        source_type = mapping.get("type")
        source_path = mapping.get("path")
        if not source_type:
            raise ValueError("source.type is required")
        if not source_path:
            raise ValueError("source.path is required")
        return cls(type=str(source_type), path=str(source_path))


@dataclass(frozen=True)
class MatchingConfig:
    keys: list[str]

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "MatchingConfig":
        if not isinstance(mapping, dict):
            raise ValueError("matching must be a mapping with key fields")
        keys = mapping.get("keys")
        if not isinstance(keys, list) or not keys:
            raise ValueError("matching.keys must contain at least one key field")
        return cls(keys=[str(key) for key in keys])


@dataclass(frozen=True)
class ValidationConfig:
    checks: list[str]

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "ValidationConfig":
        if not isinstance(mapping, dict):
            raise ValueError("validation must be a mapping with checks")
        checks = mapping.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("validation.checks must contain at least one check")
        return cls(checks=[str(check) for check in checks])


@dataclass(frozen=True)
class OutputConfig:
    directory: str

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "OutputConfig":
        if not isinstance(mapping, dict):
            raise ValueError("output must be a mapping with a directory")
        directory = mapping.get("directory")
        if not directory:
            raise ValueError("output.directory is required")
        return cls(directory=str(directory))


@dataclass(frozen=True)
class Scenario:
    name: str
    version: int | str
    source: DataSource
    target: DataSource
    matching: MatchingConfig
    validation: ValidationConfig
    output: OutputConfig

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "Scenario":
        if not isinstance(mapping, dict):
            raise ValueError("scenario configuration must be a mapping")

        name = mapping.get("name")
        version = mapping.get("version")
        if not name:
            raise ValueError("scenario.name is required")

        if "source" not in mapping or mapping.get("source") is None:
            raise ValueError("source is required")
        if "target" not in mapping or mapping.get("target") is None:
            raise ValueError("target is required")
        if "matching" not in mapping or mapping.get("matching") is None:
            raise ValueError("matching is required")
        if "validation" not in mapping or mapping.get("validation") is None:
            raise ValueError("validation is required")
        if "output" not in mapping or mapping.get("output") is None:
            raise ValueError("output is required")

        source = DataSource.from_mapping(mapping.get("source"))
        target = DataSource.from_mapping(mapping.get("target"))
        matching = MatchingConfig.from_mapping(mapping.get("matching"))
        validation = ValidationConfig.from_mapping(mapping.get("validation"))
        output = OutputConfig.from_mapping(mapping.get("output"))

        return cls(
            name=str(name),
            version=version,
            source=source,
            target=target,
            matching=matching,
            validation=validation,
            output=output,
        )
