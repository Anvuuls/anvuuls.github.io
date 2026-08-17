"""The canonical gate model: dependency graph, derived applicability, derived release status.

Two properties matter here and both are enforced in code, not by convention:

1. ``NOT_APPLICABLE`` is *derived* from ``project.yaml`` via JSON Pointer rules. Nothing --
   no agent, no human editing a status file -- can assert that a gate does not apply.
2. Release status is *computed*. There is no function in this module that writes
   ``SCHEMATIC_RELEASED`` from an input; it is only ever the result of evaluating every
   mandatory gate. A gate with no implementing check evaluates to ``BLOCKED``, so an
   incomplete pipeline cannot produce a release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .schema import load_and_validate

GATES_FILE = Path(__file__).resolve().parents[2] / "gates" / "gates.yaml"

#: Basis values that describe deferral rather than establishment.
DEFERRAL_BASIS = frozenset(
    {
        "DEFERRED_TO_SIMULATION",
        "DEFERRED_TO_LAYOUT",
        "DEFERRED_TO_BRINGUP",
        "DEFERRED_TO_LAB_TEST",
    }
)

#: Severities that block a gate when found and unwaived.
BLOCKING_SEVERITIES = frozenset({"CRITICAL", "HIGH"})


class GateModelError(Exception):
    """The gate definition file is internally inconsistent."""


# ---------------------------------------------------------------------------- pointers


def resolve_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    """Resolve an RFC 6901 JSON Pointer. Returns ``(found, value)``.

    ``found`` is False when any segment is missing, which is what lets an absent
    ``/battery`` section make the BATTERY gate NOT_APPLICABLE without special-casing.
    """
    if pointer in ("", "/"):
        return True, document
    if not pointer.startswith("/"):
        raise GateModelError(f"JSON Pointer must start with '/': {pointer!r}")

    current = document
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, list):
            try:
                index = int(token)
            except ValueError:
                return False, None
            if not 0 <= index < len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def _is_meaningful(value: Any) -> bool:
    """Whether a resolved value counts as 'present'.

    ``False``, ``None`` and empty containers do not: a project writing ``motor: false``
    means no motor, and treating that as presence would make gates apply spuriously.
    """
    if value is None or value is False:
        return False
    if isinstance(value, (dict, list, str, tuple, set)) and len(value) == 0:
        return False
    return True


def _any_truthy(value: Any) -> bool:
    """True if the value, or any nested leaf of it, is truthy."""
    if isinstance(value, dict):
        return any(_any_truthy(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_any_truthy(v) for v in value)
    return bool(value)


def evaluate_rule(project: dict[str, Any], rule: dict[str, Any]) -> bool:
    """Evaluate one applicability/approval rule against project requirements."""
    pointer = rule["pointer"]
    condition = rule.get("condition", "present")
    found, value = resolve_pointer(project, pointer)

    if condition == "present":
        return found and _is_meaningful(value)
    if condition == "absent":
        return not (found and _is_meaningful(value))
    if condition == "truthy":
        return found and _any_truthy(value)
    if condition == "equals":
        return found and value == rule.get("value")
    if condition == "in":
        expected = rule.get("value")
        if not isinstance(expected, (list, tuple, set)):
            raise GateModelError(f"rule for {pointer} uses condition 'in' but 'value' is not a list")
        return found and value in expected
    raise GateModelError(f"unknown rule condition {condition!r}")  # pragma: no cover


# ---------------------------------------------------------------------------- model


@dataclass(frozen=True)
class Gate:
    """One canonical gate definition."""

    gate_id: str
    name: str
    description: str
    mandatory: bool
    basis_requirements: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    owner_agent: str | None = None
    permitted_deferrals: tuple[str, ...] = ()
    human_approval_required: bool = False
    severity: str = "HIGH"
    applicability_rules: tuple[dict[str, Any], ...] = ()
    approval_rules: tuple[dict[str, Any], ...] = ()
    phase: int | None = None
    implemented: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Gate":
        return cls(
            gate_id=raw["gate_id"],
            name=raw["name"],
            description=raw["description"],
            mandatory=raw["mandatory"],
            basis_requirements=tuple(raw["basis_requirements"]),
            dependencies=tuple(raw.get("dependencies", ())),
            inputs=tuple(raw.get("inputs", ())),
            checks=tuple(raw.get("checks", ())),
            required_artifacts=tuple(raw.get("required_artifacts", ())),
            output_artifacts=tuple(raw.get("output_artifacts", ())),
            owner_agent=raw.get("owner_agent"),
            permitted_deferrals=tuple(raw.get("permitted_deferrals", ())),
            human_approval_required=raw.get("human_approval_required", False),
            severity=raw.get("severity", "HIGH"),
            applicability_rules=tuple(raw.get("applicability_rules", ())),
            approval_rules=tuple(raw.get("approval_rules", ())),
            phase=raw.get("phase"),
            implemented=raw.get("implemented", False),
        )

    def is_applicable(self, project: dict[str, Any]) -> tuple[bool, str]:
        """Derive applicability. Returns ``(applicable, reason)``.

        No rules means always applicable. With rules, ANY match makes the gate apply -- each
        rule names a feature whose presence makes the gate relevant.
        """
        if not self.applicability_rules:
            return True, "always applicable"
        for rule in self.applicability_rules:
            if evaluate_rule(project, rule):
                return True, f"project.yaml {rule['pointer']} satisfies '{rule.get('condition', 'present')}'"
        pointers = ", ".join(r["pointer"] for r in self.applicability_rules)
        return False, f"derived from project.yaml: none of [{pointers}] present"

    def needs_human_approval(self, project: dict[str, Any], extra_gates: Sequence[str] = ()) -> bool:
        """Whether a stored human approval is required for this gate."""
        if self.human_approval_required or self.gate_id in extra_gates:
            return True
        return any(evaluate_rule(project, rule) for rule in self.approval_rules)


@dataclass
class GateGraph:
    """The loaded gate set, validated for internal consistency."""

    gates: dict[str, Gate]
    order: list[str]

    def __getitem__(self, gate_id: str) -> Gate:
        return self.gates[gate_id]

    def __iter__(self):
        return iter(self.gates[g] for g in self.order)

    def __len__(self) -> int:
        return len(self.gates)

    def mandatory_ids(self) -> list[str]:
        return [g for g in self.order if self.gates[g].mandatory]

    def dependents_of(self, gate_id: str) -> set[str]:
        """Every gate transitively depending on ``gate_id`` -- the invalidation set."""
        out: set[str] = set()
        frontier = [gate_id]
        while frontier:
            current = frontier.pop()
            for candidate in self.gates.values():
                if current in candidate.dependencies and candidate.gate_id not in out:
                    out.add(candidate.gate_id)
                    frontier.append(candidate.gate_id)
        return out

    def declared_checks(self) -> set[str]:
        return {check for gate in self.gates.values() for check in gate.checks}


def _topological_order(gates: dict[str, Gate]) -> list[str]:
    """Kahn's algorithm, with deterministic tie-breaking and cycle reporting."""
    indegree = {gid: 0 for gid in gates}
    for gate in gates.values():
        for dep in gate.dependencies:
            indegree[gate.gate_id] += 1

    ready = sorted(gid for gid, deg in indegree.items() if deg == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for gate in sorted(gates.values(), key=lambda g: g.gate_id):
            if current in gate.dependencies:
                indegree[gate.gate_id] -= 1
                if indegree[gate.gate_id] == 0:
                    ready.append(gate.gate_id)
        ready.sort()

    if len(order) != len(gates):
        stuck = sorted(set(gates) - set(order))
        raise GateModelError(f"dependency cycle among gates: {', '.join(stuck)}")
    return order


def load_gates(path: str | Path = GATES_FILE) -> GateGraph:
    """Load and validate gates.yaml, checking IDs, dependencies and acyclicity."""
    data = load_and_validate("gate_definition", path)

    gates: dict[str, Gate] = {}
    for raw in data["gates"]:
        gate = Gate.from_dict(raw)
        if gate.gate_id in gates:
            raise GateModelError(f"duplicate gate_id {gate.gate_id}")
        gates[gate.gate_id] = gate

    for gate in gates.values():
        unknown = [d for d in gate.dependencies if d not in gates]
        if unknown:
            raise GateModelError(f"gate {gate.gate_id} depends on unknown gate(s): {', '.join(unknown)}")
        if gate.gate_id in gate.dependencies:
            raise GateModelError(f"gate {gate.gate_id} depends on itself")
        stray = set(gate.permitted_deferrals) - DEFERRAL_BASIS
        if stray:
            raise GateModelError(
                f"gate {gate.gate_id} lists non-deferral basis in permitted_deferrals: "
                f"{', '.join(sorted(stray))}"
            )

    return GateGraph(gates=gates, order=_topological_order(gates))


# ---------------------------------------------------------------------------- evaluation


@dataclass
class GateOutcome:
    """The computed result for one gate. Build output; never authored by hand."""

    gate_id: str
    status: str
    basis: list[str] = field(default_factory=list)
    applicable: bool = True
    not_applicable_reason: str | None = None
    checks_run: list[str] = field(default_factory=list)
    checks_missing: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    blocking_findings: list[str] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    approval_missing: bool = False
    waivers: list[str] = field(default_factory=list)
    unknown_critical: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "gate_id": self.gate_id,
            "status": self.status,
            "basis": self.basis,
            "applicable": self.applicable,
        }
        if self.not_applicable_reason:
            out["not_applicable_reason"] = self.not_applicable_reason
        for key, value in (
            ("checks_run", self.checks_run),
            ("checks_missing", self.checks_missing),
            ("findings", self.findings),
            ("blocking_findings", self.blocking_findings),
            ("approvals", self.approvals),
            ("waivers", self.waivers),
            ("unknown_critical", self.unknown_critical),
            ("notes", self.notes),
        ):
            if value:
                out[key] = value
        if self.approval_missing:
            out["approval_missing"] = True
        return out


@dataclass
class EvaluationInputs:
    """Everything gate evaluation reads. All of it comes from files on disk."""

    project: dict[str, Any]
    findings: list[dict[str, Any]] = field(default_factory=list)
    checks_run: set[str] = field(default_factory=set)
    checks_errored: dict[str, str] = field(default_factory=dict)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    waivers: list[dict[str, Any]] = field(default_factory=list)
    available_artifacts: set[str] = field(default_factory=set)
    unknown_critical: dict[str, list[str]] = field(default_factory=dict)
    llm_reviewed: set[str] = field(default_factory=set)


def _waiver_matches(waiver: dict[str, Any], finding: dict[str, Any]) -> bool:
    """Whether a waiver covers a finding. Every declared target field must match.

    Matching is conjunctive so a waiver cannot accidentally widen: a waiver naming only a
    check_id covers that check, but adding a component narrows it to that component.
    """
    target = waiver.get("target", {})
    if not target:
        return False
    for key, artifact_key in (
        ("check_id", "check_id"),
        ("code", "code"),
        ("component", "component"),
        ("pin", "pin"),
        ("net", "net"),
    ):
        if key in target and target[key] != finding.get(artifact_key):
            return False
    return True


def _approvals_for(gate_id: str, approvals: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        a
        for a in approvals
        if a.get("gate") == gate_id
        and a.get("decision") in {"APPROVED", "APPROVED_WITH_CONDITIONS"}
    ]


def evaluate_gate(gate: Gate, data: EvaluationInputs) -> GateOutcome:
    """Compute one gate's status and basis.

    Ordering of the rules matters: applicability first, then evaluability (is there even a
    check?), then evidence, then findings. A gate can only reach PASS by falling through
    every blocking condition.
    """
    extra_gates = data.project.get("policy", {}).get("extra_human_approval_gates", [])
    outcome = GateOutcome(gate_id=gate.gate_id, status="BLOCKED")

    applicable, reason = gate.is_applicable(data.project)
    outcome.applicable = applicable
    if not applicable:
        outcome.status = "NOT_APPLICABLE"
        outcome.not_applicable_reason = reason
        return outcome

    # Findings attributed to this gate, split into waived and blocking.
    gate_findings = [f for f in data.findings if f.get("gate_id") == gate.gate_id]
    outcome.findings = [f["finding_id"] for f in gate_findings]
    for finding in gate_findings:
        matched = [w for w in data.waivers if _waiver_matches(w, finding)]
        if matched:
            outcome.waivers.extend(w["waiver_id"] for w in matched)
        elif finding.get("severity") in BLOCKING_SEVERITIES:
            outcome.blocking_findings.append(finding["finding_id"])

    # Which of this gate's declared checks actually ran.
    outcome.checks_run = [c for c in gate.checks if c in data.checks_run]
    outcome.checks_missing = [
        c for c in gate.checks if c not in data.checks_run or c in data.checks_errored
    ]

    # Establish basis. This is the heart of the design: basis is derived from what
    # actually happened, so it cannot be inflated by assertion.
    basis: list[str] = []
    if gate.checks and not outcome.checks_missing:
        basis.append("MACHINE_CHECKED")
    approvals = _approvals_for(gate.gate_id, data.approvals)
    if approvals:
        basis.append("HUMAN_REVIEWED")
        outcome.approvals = [a["approval_id"] for a in approvals]
    if gate.gate_id in data.llm_reviewed:
        basis.append("LLM_ASSERTED")
    outcome.basis = basis

    outcome.unknown_critical = list(data.unknown_critical.get(gate.gate_id, []))

    missing_artifacts = [a for a in gate.required_artifacts if a not in data.available_artifacts]

    # --- blocking conditions, most fundamental first ---------------------------------
    if not gate.implemented:
        outcome.status = "BLOCKED"
        outcome.notes.append(
            f"no implemented check backs this gate yet (planned phase {gate.phase}); "
            f"reported BLOCKED rather than PASS so an incomplete pipeline cannot release"
        )
        return outcome

    if missing_artifacts:
        outcome.status = "BLOCKED"
        outcome.notes.append(f"required artifact(s) absent: {', '.join(missing_artifacts)}")
        return outcome

    errored = [c for c in gate.checks if c in data.checks_errored]
    if errored:
        outcome.status = "BLOCKED"
        outcome.notes.append(
            f"check(s) errored and produced no result: {', '.join(errored)} -- "
            f"a crashed check is not a clean result"
        )
        return outcome

    if outcome.checks_missing:
        outcome.status = "BLOCKED"
        outcome.notes.append(f"declared check(s) did not run: {', '.join(outcome.checks_missing)}")
        return outcome

    if outcome.unknown_critical:
        outcome.status = "BLOCKED"
        outcome.notes.append(
            f"critical value(s) still UNKNOWN: {', '.join(outcome.unknown_critical)}"
        )
        return outcome

    if gate.needs_human_approval(data.project, extra_gates) and not approvals:
        outcome.status = "BLOCKED"
        outcome.approval_missing = True
        outcome.notes.append("mandatory human approval record absent")
        return outcome

    missing_basis = [b for b in gate.basis_requirements if b not in basis]
    if missing_basis:
        outcome.status = "BLOCKED"
        outcome.notes.append(f"required basis not established: {', '.join(missing_basis)}")
        return outcome

    # An LLM opinion alone can never carry a gate, whatever its basis_requirements say.
    if basis == ["LLM_ASSERTED"]:
        outcome.status = "BLOCKED"
        outcome.notes.append("LLM_ASSERTED is the only basis; that can never satisfy a gate")
        return outcome

    if outcome.blocking_findings:
        outcome.status = "FAIL"
        outcome.notes.append(
            f"{len(outcome.blocking_findings)} unwaived blocking finding(s)"
        )
        return outcome

    outcome.status = "PASS"
    return outcome


def evaluate_all(graph: GateGraph, data: EvaluationInputs) -> tuple[list[GateOutcome], dict[str, Any]]:
    """Evaluate every gate and derive the release verdict.

    The verdict is a pure function of the outcomes. There is deliberately no parameter,
    flag or input by which a caller can assert release.
    """
    outcomes = [evaluate_gate(graph[gate_id], data) for gate_id in graph.order]
    by_id = {o.gate_id: o for o in outcomes}

    reasons: list[str] = []
    for gate_id in graph.mandatory_ids():
        outcome = by_id[gate_id]
        if outcome.status in {"FAIL", "BLOCKED"}:
            detail = outcome.notes[0] if outcome.notes else outcome.status.lower()
            reasons.append(f"{gate_id}: {outcome.status} -- {detail}")

    for check_id, error in sorted(data.checks_errored.items()):
        reasons.append(f"check {check_id} errored: {error}")

    released = not reasons
    if released:
        state = "SCHEMATIC_RELEASED"
        project_state = "SCHEMATIC_RELEASED"
    else:
        state = "NOT_RELEASED"
        blocked_or_failed = sum(
            1 for g in graph.mandatory_ids() if by_id[g].status in {"FAIL", "BLOCKED"}
        )
        total = len(graph.mandatory_ids())
        project_state = (
            "SCHEMATIC_UNDER_REVIEW" if blocked_or_failed < total else "VERIFICATION_MODEL_READY"
        )

    return outcomes, {
        "state": state,
        "reasons": reasons,
        "project_state": project_state,
    }
