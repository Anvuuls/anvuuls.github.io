"""Schema loading and validation.

Every machine-readable artifact in this repository is validated against a JSON Schema
before anything reads it. Schemas are registered by their ``$id`` so relative ``$ref``s
such as ``common.defs.json#/$defs/quantity`` resolve offline -- CI has no network, and a
schema that silently fails to resolve its refs would validate nothing while appearing to
pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"

#: Artifact kind -> schema filename. The only place this mapping lives.
SCHEMA_FILES: dict[str, str] = {
    "project": "project.schema.json",
    "part": "part.schema.json",
    "pins": "pins.schema.json",
    "requirement": "requirement.schema.json",
    "gate_definition": "gate_definition.schema.json",
    "gate_result": "gate_result.schema.json",
    "findings": "findings.schema.json",
    "expected_findings": "expected_findings.schema.json",
    "approval": "approval.schema.json",
    "waiver": "waiver.schema.json",
    "calculation": "calculation.schema.json",
    "power_budget": "power_budget.schema.json",
    "startup_states": "startup_states.schema.json",
    "pin_matrix": "pin_matrix.schema.json",
    "interface_matrix": "interface_matrix.schema.json",
    "fault_analysis": "fault_analysis.schema.json",
}


class SchemaError(Exception):
    """A schema file is itself invalid, or an unknown artifact kind was requested."""


@dataclass(frozen=True)
class ValidationProblem:
    """One schema violation, located by JSON path so a human can find it fast."""

    path: str
    message: str
    validator: str

    def __str__(self) -> str:
        where = self.path or "(root)"
        return f"{where}: {self.message}"


class ValidationFailed(Exception):
    """Raised when an artifact does not satisfy its schema."""

    def __init__(self, source: str, problems: list[ValidationProblem]) -> None:
        self.source = source
        self.problems = problems
        detail = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"{source} failed schema validation:\n{detail}")


def _schema_paths() -> list[Path]:
    if not SCHEMA_DIR.is_dir():
        raise SchemaError(f"schema directory not found: {SCHEMA_DIR}")
    return sorted(SCHEMA_DIR.glob("*.json"))


@lru_cache(maxsize=1)
def _registry() -> tuple[Registry, dict[str, dict[str, Any]]]:
    """Load every schema once, keyed by ``$id`` and by filename."""
    resources: list[tuple[str, Resource]] = []
    by_filename: dict[str, dict[str, Any]] = {}

    for path in _schema_paths():
        try:
            contents = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SchemaError(f"{path.name} is not valid JSON: {exc}") from exc

        schema_id = contents.get("$id")
        if not schema_id:
            raise SchemaError(f"{path.name} has no $id; refs to it could not resolve")

        resources.append((schema_id, Resource.from_contents(contents)))
        by_filename[path.name] = contents

    # Each schema declares $schema, so from_contents detects its dialect; no default needed.
    return Registry().with_resources(resources), by_filename


@lru_cache(maxsize=None)
def validator_for(kind: str) -> Draft202012Validator:
    """Return a validator for an artifact kind, checking the schema itself is well formed."""
    if kind not in SCHEMA_FILES:
        known = ", ".join(sorted(SCHEMA_FILES))
        raise SchemaError(f"unknown artifact kind {kind!r}; known kinds: {known}")

    registry, by_filename = _registry()
    filename = SCHEMA_FILES[kind]
    if filename not in by_filename:
        raise SchemaError(f"schema file {filename} missing from {SCHEMA_DIR}")

    schema = by_filename[filename]
    # A malformed schema silently accepts everything, so check it before use.
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def check_all_schemas() -> list[str]:
    """Validate every schema file against the JSON Schema metaschema.

    Returns the schema filenames checked. Raises :class:`SchemaError` on the first
    malformed schema.
    """
    _registry.cache_clear()
    registry, by_filename = _registry()
    checked: list[str] = []
    for filename, schema in sorted(by_filename.items()):
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # noqa: BLE001 - re-raised with file context
            raise SchemaError(f"{filename}: invalid JSON Schema: {exc}") from exc
        checked.append(filename)
    _ = registry
    return checked


def problems_for(kind: str, data: Any) -> list[ValidationProblem]:
    """Return every validation problem for ``data``, sorted by location."""
    validator = validator_for(kind)
    problems = []
    for error in validator.iter_errors(data):
        path = "/".join(str(part) for part in error.absolute_path)
        problems.append(
            ValidationProblem(path=path, message=error.message, validator=str(error.validator))
        )
    return sorted(problems, key=lambda p: (p.path, p.message))


def validate(kind: str, data: Any, *, source: str = "<data>") -> Any:
    """Validate in-memory data, raising :class:`ValidationFailed` with every problem."""
    problems = problems_for(kind, data)
    if problems:
        raise ValidationFailed(source, problems)
    return data


def load_yaml(path: str | Path) -> Any:
    """Read a YAML document with duplicate-key detection.

    PyYAML silently keeps the last of duplicated keys, which in an artifact like a pin
    table means a quietly dropped pin. That must be an error, not a shrug.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")

    class _StrictLoader(yaml.SafeLoader):
        pass

    def _no_duplicates(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.YAMLError(f"{p}: duplicate key {key!r} at line {key_node.start_mark.line + 1}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    _StrictLoader.add_constructor(  # type: ignore[attr-defined]
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
    )
    return yaml.load(text, Loader=_StrictLoader)


def load_and_validate(kind: str, path: str | Path) -> Any:
    """Load a YAML (or JSON) artifact and validate it against its schema."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"artifact not found: {p}")
    data = load_yaml(p)
    if data is None:
        raise ValidationFailed(str(p), [ValidationProblem("", "file is empty", "presence")])
    return validate(kind, data, source=str(p))


def validate_tree(pairs: Iterable[tuple[str, Path]]) -> dict[Path, list[ValidationProblem]]:
    """Validate many artifacts, returning problems per file rather than failing fast.

    Used by ``pcbv validate`` so a contributor sees every schema error in one run.
    """
    results: dict[Path, list[ValidationProblem]] = {}
    for kind, path in pairs:
        try:
            data = load_yaml(path)
            results[path] = problems_for(kind, data)
        except yaml.YAMLError as exc:
            results[path] = [ValidationProblem("", f"YAML error: {exc}", "yaml")]
    return results
