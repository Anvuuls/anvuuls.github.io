"""CHK-PROJECT-SCHEMA: the project requirements file is present, valid and self-consistent.

Backs the REQ gate. Cheap, but it is the gate every other gate depends on: if rails are
declared inconsistently or the reviewer roster is empty, downstream checks would be
comparing against nonsense.
"""

from __future__ import annotations

from ..findings import Finding
from ..schema import problems_for
from . import CheckContext, register

CHECK_ID = "CHK-PROJECT-SCHEMA"


@register(
    CHECK_ID,
    gates=["REQ"],
    description="project.yaml validates and its rail naming is internally consistent",
)
def check_project(context: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    project = context.project

    for problem in problems_for("project", project):
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="PROJECT_SCHEMA_INVALID",
                severity="CRITICAL",
                message=f"project.yaml is invalid at {problem.path or '(root)'}: {problem.message}",
                location={"file": "project.yaml", "detail": problem.path},
            )
        )
    if findings:
        # Later checks in this function assume a well-formed document.
        return findings

    findings.extend(_check_rail_naming(project))
    findings.extend(_check_library_problems(context))
    return findings


def _check_rail_naming(project: dict) -> list[Finding]:
    """Every declared rail needs a canonical net name, and aliases must not collide.

    This is what stops 3V3 / +3V3 / 3.3V / VDD_3V3 drifting apart across a design: the
    canonical name is declared once, and any other spelling has to be an intentional alias.
    """
    findings: list[Finding] = []
    rail_naming = project.get("rail_naming", {})
    declared_rails = [r["name"] for r in project.get("power", {}).get("rails", [])]

    for rail in declared_rails:
        if rail not in rail_naming:
            findings.append(
                Finding(
                    check_id=CHECK_ID,
                    code="RAIL_NAMING_UNDECLARED",
                    severity="MEDIUM",
                    rail=rail,
                    message=(
                        f"rail {rail} has no rail_naming entry, so no canonical net name is "
                        f"pinned and spelling drift cannot be detected"
                    ),
                    remediation=f"Add a rail_naming entry for {rail} naming its canonical net",
                    location={"file": "project.yaml"},
                )
            )

    seen: dict[str, str] = {}
    for rail, entry in sorted(rail_naming.items()):
        for name in [entry["net"], *entry.get("aliases", [])]:
            if name in seen and seen[name] != rail:
                findings.append(
                    Finding(
                        check_id=CHECK_ID,
                        code="RAIL_NET_NAME_COLLISION",
                        severity="HIGH",
                        rail=rail,
                        net=name,
                        message=(
                            f"net name {name!r} is claimed by both rail {seen[name]} and rail "
                            f"{rail}"
                        ),
                        location={"file": "project.yaml"},
                    )
                )
            seen[name] = rail

    return findings


def _check_library_problems(context: CheckContext) -> list[Finding]:
    """Surface part-library loading problems as findings rather than swallowing them."""
    return [
        Finding(
            check_id=CHECK_ID,
            code="PART_LIBRARY_PROBLEM",
            severity="HIGH",
            message=f"part library problem: {problem}",
            location={"file": "library/parts"},
        )
        for problem in context.library_problems
    ]
