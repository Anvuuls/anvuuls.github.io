"""Deterministic check registry.

A check is a pure function from a :class:`CheckContext` to a list of findings. Checks never
open KiCad files themselves and never decide gate status -- they report facts, the gate
engine decides consequences, and a human or agent interprets.

A check that raises is recorded as *errored*, which is deliberately not the same as a check
that ran and found nothing: a crashed check must never read as a clean result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from ..findings import Finding
from ..model import Design, PartLibrary


@dataclass
class CheckContext:
    """Everything a check may read. All of it derives from files on disk."""

    design: Design
    library: PartLibrary
    project: dict
    design_dir: Path
    symbol_libraries: dict[str, dict] = field(default_factory=dict)
    footprint_roots: list[Path] = field(default_factory=list)
    library_problems: list[str] = field(default_factory=list)


CheckFunction = Callable[[CheckContext], list[Finding]]


@dataclass(frozen=True)
class RegisteredCheck:
    check_id: str
    gate_ids: tuple[str, ...]
    description: str
    function: CheckFunction
    requires_kicad_cli: bool = False


_REGISTRY: dict[str, RegisteredCheck] = {}


def register(
    check_id: str,
    *,
    gates: Iterable[str],
    description: str,
    requires_kicad_cli: bool = False,
) -> Callable[[CheckFunction], CheckFunction]:
    """Register a check under a stable ID and attribute it to one or more gates."""

    def decorator(function: CheckFunction) -> CheckFunction:
        if check_id in _REGISTRY:
            raise RuntimeError(f"check {check_id} registered twice")
        _REGISTRY[check_id] = RegisteredCheck(
            check_id=check_id,
            gate_ids=tuple(gates),
            description=description,
            function=function,
            requires_kicad_cli=requires_kicad_cli,
        )
        return function

    return decorator


def registered_checks() -> dict[str, RegisteredCheck]:
    _load_builtin_checks()
    return dict(_REGISTRY)


def _load_builtin_checks() -> None:
    """Import check modules so their registrations happen."""
    from . import pin_mapping, project_schema  # noqa: F401  (import for side effect)


def run_checks(
    context: CheckContext, *, only: Iterable[str] | None = None
) -> tuple[list[Finding], list[str], dict[str, str]]:
    """Run checks, returning ``(findings, checks_run, errors)``.

    Each check's findings are tagged with the gate it belongs to so the gate engine can
    attribute blocking findings without a second mapping table.
    """
    checks = registered_checks()
    selected = sorted(checks) if only is None else [c for c in sorted(checks) if c in set(only)]

    findings: list[Finding] = []
    ran: list[str] = []
    errors: dict[str, str] = {}

    for check_id in selected:
        check = checks[check_id]
        try:
            produced = check.function(context)
        except Exception as exc:  # noqa: BLE001 - a crashed check must be visible, not fatal
            errors[check_id] = f"{type(exc).__name__}: {exc}"
            continue

        primary_gate = check.gate_ids[0] if check.gate_ids else None
        for finding in produced:
            if finding.gate_id is None:
                finding.gate_id = primary_gate
        findings.extend(produced)
        ran.append(check_id)

    return findings, ran, errors
