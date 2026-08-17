"""Corpus-driven tests: the tests that make the verification suite itself trustworthy.

Every check must be proven to fire on a design containing its defect (known_bad) and proven
not to fire on one that does not (known_good). Until both hold, a green run from this suite
means nothing.
"""

from __future__ import annotations

import pytest

from pcbv.checks import registered_checks
from pcbv.corpus import discover_cases, match_case, run_case
from pcbv.gatemodel import load_gates

CASES = discover_cases()
CASE_IDS = [f"{c.kind}/{c.name}" for c in CASES]


def test_corpus_is_not_empty():
    """A corpus that has quietly become empty would make every other test here vacuous."""
    assert CASES, "no corpus cases discovered"
    assert any(c.kind == "known_good" for c in CASES), "corpus has no known-good case"
    assert any(c.kind == "known_bad" for c in CASES), "corpus has no known-bad case"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_case_matches_expectation(case):
    """Findings produced for each case exactly satisfy its recorded contract."""
    result = run_case(case)
    report = match_case(result)
    assert report.ok, "\n" + report.describe()


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_no_check_errors(case):
    """A crashed check must never be mistaken for a clean result."""
    result = run_case(case)
    assert not result.errors, f"checks errored: {result.errors}"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_schematic_parsed_without_warnings(case):
    """Sheet resolution and symbol caching produced no structural complaints."""
    from pcbv.corpus import build_context

    context = build_context(case.directory)
    assert not context.design.parse_warnings, context.design.parse_warnings
    assert not context.library_problems, context.library_problems
    assert context.design.components, "no components read from the schematic"
    assert not context.design.duplicate_refdes()


def test_every_known_bad_case_is_detected_by_some_check():
    """A known-bad case whose defect nothing detects must say so explicitly.

    Recording a coverage gap is acceptable; silently shipping an undetected defect case is
    not, because it inflates the apparent coverage of the suite.
    """
    for case in CASES:
        if case.kind != "known_bad":
            continue
        expects = case.expectation.get("expect", [])
        gaps = case.expectation.get("not_yet_detected", [])
        assert expects or gaps, (
            f"{case.name} asserts no findings and records no coverage gap; it tests nothing"
        )


def test_known_good_cases_are_exhaustive():
    """Known-good cases must assert cleanliness, not merely fail to assert dirtiness."""
    for case in CASES:
        if case.kind == "known_good":
            assert case.exhaustive, f"{case.name} must set exhaustive: true"
            assert not case.expectation.get("expect"), f"{case.name} must expect no findings"


def test_every_implemented_check_has_a_known_bad_case():
    """Adding a check without a known-bad case is not allowed.

    This is the rule that keeps the suite honest as it grows: an unexercised check is
    untested software whose PASS carries no information.
    """
    exercised = {
        pattern["check_id"]
        for case in CASES
        if case.kind == "known_bad"
        for pattern in case.expectation.get("expect", [])
    }
    # Checks whose findings are only reachable through artifacts no corpus case has yet.
    known_unexercised = {"CHK-PROJECT-SCHEMA"}

    missing = set(registered_checks()) - exercised - known_unexercised
    assert not missing, (
        f"these registered checks have no known-bad corpus case: {sorted(missing)}. "
        f"Add one, or add it to known_unexercised with a reason."
    )


def test_registered_checks_are_declared_in_gates():
    """Every implemented check belongs to a gate, and every gate's checks exist or are planned."""
    graph = load_gates()
    declared = graph.declared_checks()

    for check_id, check in sorted(registered_checks().items()):
        assert check_id in declared, (
            f"check {check_id} is registered but no gate in gates.yaml declares it, so its "
            f"findings would never affect a gate"
        )
        for gate_id in check.gate_ids:
            assert gate_id in graph.gates, f"check {check_id} claims unknown gate {gate_id}"

    # A gate marked implemented must actually have all of its checks available.
    available = set(registered_checks())
    for gate in graph:
        if gate.implemented:
            missing = [c for c in gate.checks if c not in available]
            assert not missing, (
                f"gate {gate.gate_id} is marked implemented but its check(s) {missing} are not "
                f"registered; it would report BLOCKED forever"
            )
