"""Tests for the canonical gate model.

Two properties are load-bearing and are asserted here directly, because if either broke the
whole pipeline would still look like it worked:

* ``NOT_APPLICABLE`` is derived from project requirements, not assertable.
* ``SCHEMATIC_RELEASED`` is computed, and cannot be reached while any mandatory gate is
  FAIL or BLOCKED.
"""

from __future__ import annotations

import pytest

from pcbv.gatemodel import (
    DEFERRAL_BASIS,
    EvaluationInputs,
    Gate,
    GateModelError,
    GateGraph,
    _topological_order,
    evaluate_all,
    evaluate_gate,
    load_gates,
    resolve_pointer,
)

GRAPH = load_gates()


def _project(**extra):
    base = {
        "project": {"name": "t", "revision": "A"},
        "pcb": {"layers": 2},
        "reviewers": [{"name": "A Person", "role": "ee"}],
        "power": {
            "sources": [{"name": "USB", "kind": "usb_c"}],
            "rails": [{"name": "V3V3", "voltage": {"value": 3.3, "unit": "V"}}],
        },
    }
    base.update(extra)
    return base


def test_gates_load_and_sort():
    assert len(GRAPH) > 0
    assert GRAPH.order[0] == "REQ", "REQ has no dependencies and must sort first"
    assert GRAPH.order[-1] == "FINAL_REVIEW", "FINAL_REVIEW depends on everything"


def test_dependencies_precede_dependents():
    position = {gid: i for i, gid in enumerate(GRAPH.order)}
    for gate in GRAPH:
        for dep in gate.dependencies:
            assert position[dep] < position[gate.gate_id], (
                f"{gate.gate_id} sorts before its dependency {dep}"
            )


def test_cycle_is_detected():
    a = Gate(gate_id="A", name="a", description="a", mandatory=True,
             basis_requirements=("MACHINE_CHECKED",), dependencies=("B",))
    b = Gate(gate_id="B", name="b", description="b", mandatory=True,
             basis_requirements=("MACHINE_CHECKED",), dependencies=("A",))
    with pytest.raises(GateModelError, match="cycle"):
        _topological_order({"A": a, "B": b})


def test_permitted_deferrals_are_deferral_values():
    for gate in GRAPH:
        assert set(gate.permitted_deferrals) <= DEFERRAL_BASIS


def test_json_pointer_resolution():
    doc = {"a": {"b": [10, 20]}, "x~y": 1, "p/q": 2}
    assert resolve_pointer(doc, "/a/b/1") == (True, 20)
    assert resolve_pointer(doc, "/a/missing") == (False, None)
    assert resolve_pointer(doc, "/a/b/9") == (False, None)
    assert resolve_pointer(doc, "/x~0y") == (True, 1)
    assert resolve_pointer(doc, "/p~1q") == (True, 2)
    assert resolve_pointer(doc, "") == (True, doc)


# ------------------------------------------------------- derived applicability


def test_rf_gate_not_applicable_without_rf_section():
    outcomes, _ = evaluate_all(GRAPH, EvaluationInputs(project=_project()))
    by_id = {o.gate_id: o for o in outcomes}
    assert by_id["RF"].status == "NOT_APPLICABLE"
    assert "project.yaml" in by_id["RF"].not_applicable_reason


def test_rf_gate_applicable_with_rf_section():
    outcomes, _ = evaluate_all(GRAPH, EvaluationInputs(project=_project(rf={"wifi": True})))
    by_id = {o.gate_id: o for o in outcomes}
    assert by_id["RF"].status != "NOT_APPLICABLE"


def test_battery_gate_derives_from_project_and_forces_approval():
    project = _project(battery={"chemistry": "lipo"})
    outcomes, _ = evaluate_all(GRAPH, EvaluationInputs(project=project))
    by_id = {o.gate_id: o for o in outcomes}
    assert by_id["BATTERY"].status != "NOT_APPLICABLE"
    assert GRAPH["BATTERY"].needs_human_approval(project) is True


def test_false_feature_flag_does_not_make_gate_applicable():
    """'motor: false' means no motor; treating it as presence would apply gates spuriously."""
    project = _project(analog={"adc": False, "dac": False})
    outcomes, _ = evaluate_all(GRAPH, EvaluationInputs(project=project))
    by_id = {o.gate_id: o for o in outcomes}
    assert by_id["ANALOG"].status == "NOT_APPLICABLE"

    project = _project(analog={"adc": True})
    outcomes, _ = evaluate_all(GRAPH, EvaluationInputs(project=project))
    by_id = {o.gate_id: o for o in outcomes}
    assert by_id["ANALOG"].status != "NOT_APPLICABLE"


# ------------------------------------------------------- status and basis


def _single_gate_graph(**overrides) -> tuple[GateGraph, Gate]:
    defaults = dict(
        gate_id="TESTG",
        name="test",
        description="test gate",
        mandatory=True,
        basis_requirements=("MACHINE_CHECKED",),
        checks=("CHK-X",),
        implemented=True,
    )
    defaults.update(overrides)
    gate = Gate(**defaults)  # type: ignore[arg-type]
    return GateGraph(gates={gate.gate_id: gate}, order=[gate.gate_id]), gate


def test_unimplemented_gate_is_blocked_not_passed():
    _, gate = _single_gate_graph(implemented=False, checks=())
    outcome = evaluate_gate(gate, EvaluationInputs(project=_project()))
    assert outcome.status == "BLOCKED"
    assert "no implemented check" in outcome.notes[0]


def test_gate_passes_when_check_ran_clean():
    _, gate = _single_gate_graph()
    outcome = evaluate_gate(
        gate, EvaluationInputs(project=_project(), checks_run={"CHK-X"})
    )
    assert outcome.status == "PASS"
    assert outcome.basis == ["MACHINE_CHECKED"]


def test_missing_check_blocks():
    _, gate = _single_gate_graph()
    outcome = evaluate_gate(gate, EvaluationInputs(project=_project(), checks_run=set()))
    assert outcome.status == "BLOCKED"


def test_errored_check_blocks_and_is_not_a_clean_result():
    _, gate = _single_gate_graph()
    outcome = evaluate_gate(
        gate,
        EvaluationInputs(
            project=_project(), checks_run={"CHK-X"}, checks_errored={"CHK-X": "boom"}
        ),
    )
    assert outcome.status == "BLOCKED"
    assert any("errored" in note for note in outcome.notes)


def test_blocking_finding_fails_the_gate():
    _, gate = _single_gate_graph()
    findings = [
        {"finding_id": "X-001", "check_id": "CHK-X", "code": "BAD", "severity": "CRITICAL",
         "gate_id": "TESTG", "message": "m"}
    ]
    outcome = evaluate_gate(
        gate, EvaluationInputs(project=_project(), checks_run={"CHK-X"}, findings=findings)
    )
    assert outcome.status == "FAIL"
    assert outcome.blocking_findings == ["X-001"]


def test_waived_finding_does_not_fail_the_gate():
    _, gate = _single_gate_graph()
    findings = [
        {"finding_id": "X-001", "check_id": "CHK-X", "code": "BAD", "severity": "CRITICAL",
         "gate_id": "TESTG", "message": "m", "component": "U1"}
    ]
    waivers = [{"waiver_id": "WVR-1", "target": {"check_id": "CHK-X", "component": "U1"}}]
    outcome = evaluate_gate(
        gate,
        EvaluationInputs(
            project=_project(), checks_run={"CHK-X"}, findings=findings, waivers=waivers
        ),
    )
    assert outcome.status == "PASS"
    assert outcome.waivers == ["WVR-1"]


def test_waiver_does_not_widen_beyond_its_declared_target():
    """A waiver naming U1 must not cover the same defect on U2."""
    _, gate = _single_gate_graph()
    findings = [
        {"finding_id": "X-001", "check_id": "CHK-X", "code": "BAD", "severity": "CRITICAL",
         "gate_id": "TESTG", "message": "m", "component": "U2"}
    ]
    waivers = [{"waiver_id": "WVR-1", "target": {"check_id": "CHK-X", "component": "U1"}}]
    outcome = evaluate_gate(
        gate,
        EvaluationInputs(
            project=_project(), checks_run={"CHK-X"}, findings=findings, waivers=waivers
        ),
    )
    assert outcome.status == "FAIL"


def test_unknown_critical_value_blocks():
    _, gate = _single_gate_graph()
    outcome = evaluate_gate(
        gate,
        EvaluationInputs(
            project=_project(),
            checks_run={"CHK-X"},
            unknown_critical={"TESTG": ["U1 maximum transmit current"]},
        ),
    )
    assert outcome.status == "BLOCKED"
    assert outcome.unknown_critical


def test_missing_human_approval_blocks():
    _, gate = _single_gate_graph(
        human_approval_required=True,
        basis_requirements=("MACHINE_CHECKED", "HUMAN_REVIEWED"),
    )
    outcome = evaluate_gate(gate, EvaluationInputs(project=_project(), checks_run={"CHK-X"}))
    assert outcome.status == "BLOCKED"
    assert outcome.approval_missing


def test_human_approval_satisfies_gate():
    _, gate = _single_gate_graph(
        human_approval_required=True,
        basis_requirements=("MACHINE_CHECKED", "HUMAN_REVIEWED"),
    )
    approvals = [
        {"approval_id": "APR-1", "gate": "TESTG", "reviewer": "A Person", "date": "2026-08-17",
         "decision": "APPROVED", "revision": "A", "scope_commit": "abc1234"}
    ]
    outcome = evaluate_gate(
        gate,
        EvaluationInputs(project=_project(), checks_run={"CHK-X"}, approvals=approvals),
    )
    assert outcome.status == "PASS"
    assert "HUMAN_REVIEWED" in outcome.basis


def test_llm_assertion_alone_can_never_satisfy_a_gate():
    """The single most important rule in the basis system."""
    _, gate = _single_gate_graph(checks=(), basis_requirements=("LLM_ASSERTED",))
    outcome = evaluate_gate(
        gate, EvaluationInputs(project=_project(), llm_reviewed={"TESTG"})
    )
    assert outcome.status == "BLOCKED"
    assert any("LLM_ASSERTED" in note for note in outcome.notes)


def test_rejected_approval_does_not_count():
    _, gate = _single_gate_graph(
        human_approval_required=True,
        basis_requirements=("MACHINE_CHECKED", "HUMAN_REVIEWED"),
    )
    approvals = [
        {"approval_id": "APR-1", "gate": "TESTG", "reviewer": "A Person", "date": "2026-08-17",
         "decision": "REJECTED", "revision": "A", "scope_commit": "abc1234"}
    ]
    outcome = evaluate_gate(
        gate,
        EvaluationInputs(project=_project(), checks_run={"CHK-X"}, approvals=approvals),
    )
    assert outcome.status == "BLOCKED"


# ------------------------------------------------------- derived release


def test_release_is_not_reachable_with_blocked_mandatory_gates():
    _, release = evaluate_all(GRAPH, EvaluationInputs(project=_project()))
    assert release["state"] == "NOT_RELEASED"
    assert release["reasons"], "NOT_RELEASED with no reasons is a bug"


def test_release_requires_every_mandatory_gate():
    """A single-gate graph proves the verdict is a pure function of the outcomes."""
    graph, _ = _single_gate_graph()
    _, release = evaluate_all(graph, EvaluationInputs(project=_project(), checks_run={"CHK-X"}))
    assert release["state"] == "SCHEMATIC_RELEASED"

    _, release = evaluate_all(graph, EvaluationInputs(project=_project()))
    assert release["state"] == "NOT_RELEASED"


def test_errored_check_appears_in_release_reasons():
    graph, _ = _single_gate_graph()
    _, release = evaluate_all(
        graph,
        EvaluationInputs(
            project=_project(), checks_run={"CHK-X"}, checks_errored={"CHK-X": "kaboom"}
        ),
    )
    assert release["state"] == "NOT_RELEASED"
    assert any("kaboom" in r for r in release["reasons"])


def test_full_gate_set_currently_reports_not_released():
    """The repository must not be able to claim a release while Phase 0 is incomplete."""
    _, release = evaluate_all(GRAPH, EvaluationInputs(project=_project()))
    assert release["state"] == "NOT_RELEASED"
    assert release["project_state"] != "SCHEMATIC_RELEASED"
