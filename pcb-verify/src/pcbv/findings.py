"""Findings: the output of deterministic checks.

Findings are produced by code and interpreted by humans or agents. Nothing else may author
one -- ``CLAUDE.md`` states the rule, and this module is the only construction path, so a
finding in a report is always traceable to a check that ran.

Matching in the corpus is on ``check_id`` + ``code`` + located object, never on ``message``,
so rewording a message never silently breaks a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Severities in descending order of seriousness.
SEVERITY_ORDER: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")

#: Severities that count toward exhaustive corpus matching. INFO records coverage
#: information -- what a check skipped and why -- so it is reportable but does not make a
#: known-good design "dirty".
SUBSTANTIVE_SEVERITIES: frozenset[str] = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW"})


def severity_rank(severity: str) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:  # pragma: no cover - schema constrains this
        return len(SEVERITY_ORDER)


@dataclass
class Finding:
    """One defect, or one recorded coverage gap."""

    check_id: str
    code: str
    severity: str
    message: str
    gate_id: str | None = None
    component: str | None = None
    mpn: str | None = None
    package: str | None = None
    pin: str | None = None
    net: str | None = None
    rail: str | None = None
    signal: str | None = None
    expected: Any = None
    actual: Any = None
    location: dict[str, str] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    remediation: str | None = None
    finding_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "finding_id": self.finding_id,
            "check_id": self.check_id,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        for key, value in (
            ("gate_id", self.gate_id),
            ("component", self.component),
            ("mpn", self.mpn),
            ("package", self.package),
            ("pin", self.pin),
            ("net", self.net),
            ("rail", self.rail),
            ("signal", self.signal),
            ("remediation", self.remediation),
        ):
            if value:
                out[key] = value
        if self.expected is not None:
            out["expected"] = self.expected
        if self.actual is not None:
            out["actual"] = self.actual
        if self.location:
            out["location"] = self.location
        if self.evidence:
            out["evidence"] = self.evidence
        return out


class FindingList:
    """Accumulator that assigns stable, ordered finding IDs."""

    def __init__(self) -> None:
        self._findings: list[Finding] = []
        self._counters: dict[str, int] = {}

    def add(self, finding: Finding) -> Finding:
        prefix = finding.check_id.removeprefix("CHK-")
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        finding.finding_id = f"{prefix}-{self._counters[prefix]:03d}"
        self._findings.append(finding)
        return finding

    def extend(self, findings: list[Finding]) -> None:
        for finding in findings:
            self.add(finding)

    def __iter__(self):
        return iter(self._findings)

    def __len__(self) -> int:
        return len(self._findings)

    @property
    def findings(self) -> list[Finding]:
        return list(self._findings)

    def substantive(self) -> list[Finding]:
        """Findings that represent defects rather than coverage notes."""
        return [f for f in self._findings if f.severity in SUBSTANTIVE_SEVERITIES]

    def sorted_by_severity(self) -> list[Finding]:
        return sorted(
            self._findings,
            key=lambda f: (severity_rank(f.severity), f.check_id, f.component or "", f.pin or ""),
        )

    def worst_severity(self) -> str | None:
        if not self._findings:
            return None
        return min((f.severity for f in self._findings), key=severity_rank)

    def to_dict(self, *, design: str, checks_run: list[str], errors: dict[str, str]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": 1,
            "design": design,
            "findings": [f.to_dict() for f in self.sorted_by_severity()],
        }
        if checks_run:
            out["checks_run"] = sorted(checks_run)
        if errors:
            out["checks_errored"] = [
                {"check_id": cid, "error": err} for cid, err in sorted(errors.items())
            ]
        return out
