"""Loading and matching of the known-good / known-bad corpus.

The corpus is what makes the verification suite itself trustworthy. Without it, "all gates
PASS" is output from untested software and carries no information. Every check must be
proven to fire on a design that contains its defect, and proven not to fire on one that
does not.

Each case may carry a partial ``library/`` that shadows the shared example library, so a
case's diff against the known-good baseline is just its injected defect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checks import CheckContext, run_checks
from .findings import Finding, SUBSTANTIVE_SEVERITIES
from .kicad.schematic import read_schematic
from .library import load_part_library
from .schema import load_and_validate

CORPUS_DIR = Path(__file__).resolve().parents[2] / "tests" / "corpus"
SHARED_DIR = CORPUS_DIR / "_shared"


@dataclass
class CorpusCase:
    """One test design plus the contract describing what checkers must say about it."""

    name: str
    kind: str
    directory: Path
    expectation: dict[str, Any]

    @property
    def is_known_good(self) -> bool:
        return self.kind == "known_good"

    @property
    def exhaustive(self) -> bool:
        return bool(self.expectation.get("exhaustive", False))


def discover_cases(corpus_dir: Path = CORPUS_DIR) -> list[CorpusCase]:
    """Find every corpus case, validating its expectation file."""
    cases: list[CorpusCase] = []
    for kind in ("known_good", "known_bad"):
        kind_dir = corpus_dir / kind
        if not kind_dir.is_dir():
            continue
        for case_dir in sorted(d for d in kind_dir.iterdir() if d.is_dir()):
            expectation_file = case_dir / "expected_findings.yaml"
            if not expectation_file.is_file():
                raise FileNotFoundError(
                    f"corpus case {case_dir} has no expected_findings.yaml; a case without an "
                    f"explicit contract tests nothing"
                )
            expectation = load_and_validate("expected_findings", expectation_file)

            if expectation["design"] != case_dir.name:
                raise ValueError(
                    f"{expectation_file}: design {expectation['design']!r} does not match "
                    f"directory {case_dir.name!r}; a mismatch would silently test the wrong design"
                )
            if expectation["kind"] != kind:
                raise ValueError(
                    f"{expectation_file}: kind {expectation['kind']!r} does not match its "
                    f"location under {kind}/"
                )

            cases.append(
                CorpusCase(
                    name=case_dir.name,
                    kind=kind,
                    directory=case_dir,
                    expectation=expectation,
                )
            )
    return cases


def build_context(case_dir: Path, *, shared_dir: Path = SHARED_DIR) -> CheckContext:
    """Assemble a :class:`CheckContext` for a corpus case."""
    project = load_and_validate("project", case_dir / "project.yaml")

    schematic_rel = project["project"].get("schematic_root")
    if not schematic_rel:
        raise ValueError(f"{case_dir}/project.yaml does not declare project.schematic_root")
    design = read_schematic(case_dir / schematic_rel, name=project["project"]["name"])

    # Case-local library first so a case can shadow exactly the part it breaks.
    library, problems = load_part_library(
        case_dir / "library" / "parts",
        shared_dir / "library" / "parts",
    )

    return CheckContext(
        design=design,
        library=library,
        project=project,
        design_dir=case_dir,
        footprint_roots=[case_dir / "library", shared_dir / "library"],
        library_problems=problems,
    )


@dataclass
class CaseResult:
    """Outcome of running the checks over one corpus case."""

    case: CorpusCase
    findings: list[Finding]
    checks_run: list[str]
    errors: dict[str, str]

    def substantive(self) -> list[Finding]:
        return [f for f in self.findings if f.severity in SUBSTANTIVE_SEVERITIES]


def run_case(case: CorpusCase, *, shared_dir: Path = SHARED_DIR) -> CaseResult:
    """Run every registered check against one corpus case."""
    context = build_context(case.directory, shared_dir=shared_dir)
    findings, checks_run, errors = run_checks(context)
    return CaseResult(case=case, findings=findings, checks_run=checks_run, errors=errors)


# ------------------------------------------------------------------------- matching


def _matches(finding: Finding, pattern: dict[str, Any]) -> bool:
    """Whether a finding satisfies an expectation pattern.

    Only stable fields are compared. ``message`` is matched solely via the optional
    ``message_contains``, so rewording a message never silently breaks a test.
    """
    if finding.check_id != pattern["check_id"]:
        return False
    if "code" in pattern and finding.code != pattern["code"]:
        return False
    for key in ("component", "pin", "net", "signal", "severity"):
        if key in pattern and getattr(finding, key) != pattern[key]:
            return False
    if "message_contains" in pattern and pattern["message_contains"] not in finding.message:
        return False
    return True


@dataclass
class MatchReport:
    """Differences between what a case expected and what the checks produced."""

    case_name: str
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    count_errors: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    errored_checks: list[str] = field(default_factory=list)
    regressed_gaps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.missing
            or self.unexpected
            or self.count_errors
            or self.forbidden
            or self.errored_checks
            or self.regressed_gaps
        )

    def describe(self) -> str:
        lines = [f"corpus case {self.case_name}:"]
        for label, items in (
            ("expected but not produced", self.missing),
            ("wrong number produced", self.count_errors),
            ("produced but forbidden", self.forbidden),
            ("produced but not expected (case is exhaustive)", self.unexpected),
            ("checks errored", self.errored_checks),
            ("recorded coverage gap now detected -- update not_yet_detected", self.regressed_gaps),
        ):
            for item in items:
                lines.append(f"  {label}: {item}")
        return "\n".join(lines)


def _describe_pattern(pattern: dict[str, Any]) -> str:
    parts = [pattern["check_id"], pattern.get("code", "<any code>")]
    for key in ("component", "pin", "severity"):
        if key in pattern:
            parts.append(f"{key}={pattern[key]}")
    return " ".join(parts)


def _describe_finding(finding: Finding) -> str:
    bits = [finding.check_id, finding.code, finding.severity]
    if finding.component:
        bits.append(f"component={finding.component}")
    if finding.pin:
        bits.append(f"pin={finding.pin}")
    return " ".join(bits) + f" :: {finding.message}"


def match_case(result: CaseResult) -> MatchReport:
    """Compare a case's actual findings against its expectation contract."""
    report = MatchReport(case_name=result.case.name)
    expectation = result.case.expectation

    for check_id, error in sorted(result.errors.items()):
        report.errored_checks.append(f"{check_id}: {error}")

    findings = result.findings
    consumed: set[int] = set()

    for pattern in expectation.get("expect", []):
        hits = [i for i, f in enumerate(findings) if _matches(f, pattern)]
        minimum = pattern.get("min_count", 1)
        maximum = pattern.get("max_count")

        if len(hits) < minimum:
            report.missing.append(
                f"{_describe_pattern(pattern)} (wanted at least {minimum}, got {len(hits)})"
            )
        elif maximum is not None and len(hits) > maximum:
            report.count_errors.append(
                f"{_describe_pattern(pattern)} (wanted at most {maximum}, got {len(hits)})"
            )
        consumed.update(hits)

    for pattern in expectation.get("forbid", []):
        for finding in findings:
            if _matches(finding, pattern):
                report.forbidden.append(_describe_finding(finding))

    # A recorded coverage gap that starts being detected is good news, but the corpus must
    # be updated to assert it -- otherwise the gap list rots into fiction.
    for gap in expectation.get("not_yet_detected", []):
        planned = gap["planned_check"]
        if any(f.check_id == planned for f in findings):
            report.regressed_gaps.append(
                f"{planned} now produces findings for '{gap['description']}'; move it from "
                f"not_yet_detected into expect"
            )

    if result.case.exhaustive:
        # INFO findings record coverage rather than defects, so they never make a
        # known-good design fail exhaustiveness.
        for index, finding in enumerate(findings):
            if index in consumed:
                continue
            if finding.severity not in SUBSTANTIVE_SEVERITIES:
                continue
            report.unexpected.append(_describe_finding(finding))

    return report
