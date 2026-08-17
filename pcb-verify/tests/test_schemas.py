"""Schema tests, including the policy rules the schemas are supposed to enforce.

Testing that a schema *rejects* bad input matters more than testing it accepts good input: a
schema with an unresolvable ``$ref`` or a mis-nested ``allOf`` validates nothing while
appearing to pass, which would silently disable the evidence policy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pcbv.schema import (
    SCHEMA_DIR,
    SCHEMA_FILES,
    ValidationFailed,
    check_all_schemas,
    load_and_validate,
    problems_for,
    validate,
    validator_for,
)


def test_all_schemas_are_valid_jsonschema():
    checked = check_all_schemas()
    assert len(checked) >= len(SCHEMA_FILES)


@pytest.mark.parametrize("kind", sorted(SCHEMA_FILES))
def test_every_kind_builds_a_validator(kind):
    assert validator_for(kind) is not None


def test_every_schema_file_is_mapped_or_shared():
    """No orphan schema files: an unmapped schema is one nothing validates against."""
    mapped = set(SCHEMA_FILES.values()) | {"common.defs.json"}
    on_disk = {p.name for p in SCHEMA_DIR.glob("*.json")}
    assert on_disk == mapped, f"unmapped: {sorted(on_disk - mapped)}, missing: {sorted(mapped - on_disk)}"


def test_refs_actually_resolve():
    """A quantity ref must reject a unit-in-string value.

    If relative $refs silently failed to resolve, this would pass validation and every
    physical value in the repository would be unchecked.
    """
    problems = problems_for(
        "project",
        {
            "project": {"name": "x", "revision": "A"},
            "pcb": {"layers": 2},
            "reviewers": [{"name": "A Person", "role": "ee"}],
            "power": {
                "sources": [{"name": "USB", "kind": "usb_c"}],
                "rails": [{"name": "V3V3", "voltage": "3.3V"}],
            },
        },
    )
    assert problems, "unit-in-string was accepted; a $ref is probably not resolving"


# --------------------------------------------------------------- evidence policy


def _requirement(level: str, severity: str = "CRITICAL", **extra):
    evidence = {"source_id": "DS1", "snippet": "supply voltage 3.0 to 3.6 V", "evidence_level": level}
    evidence.update(extra)
    return {
        "schema_version": 1,
        "mpn": "ABC-123",
        "requirements": [
            {
                "requirement_id": "ESP-PWR-001",
                "category": "power",
                "parameter": "vdd",
                "severity": severity,
                "mandatory": True,
                "evidence": [evidence],
                "verification_status": "NOT_CHECKED",
            }
        ],
    }


@pytest.mark.parametrize("level", ["D", "E", "F"])
def test_critical_requirement_rejects_weak_evidence(level):
    """CRITICAL admits only manufacturer-grade evidence; inference is forbidden."""
    assert problems_for("requirement", _requirement(level))


@pytest.mark.parametrize("level", ["A", "B", "C"])
def test_critical_requirement_accepts_manufacturer_evidence(level):
    assert not problems_for("requirement", _requirement(level))


def test_low_severity_may_rest_on_inference():
    assert not problems_for("requirement", _requirement("F", severity="LOW"))


def test_pass_requires_a_verified_snippet():
    """A requirement may not claim PASS on a citation nothing has confirmed."""
    doc = _requirement("A")
    doc["requirements"][0]["verification_status"] = "PASS"
    assert problems_for("requirement", doc)

    doc = _requirement("A", snippet_verified=True)
    doc["requirements"][0]["verification_status"] = "PASS"
    assert not problems_for("requirement", doc)


def test_verified_value_requires_value_and_evidence():
    """UNKNOWN must stay null, and VERIFIED must carry both a number and a citation."""
    base = {
        "schema_version": 1,
        "rails": [
            {
                "rail": "V3V3",
                "voltage": {"value": 3.3, "unit": "V"},
                "supply": {"refdes": "U2", "kind": "ldo", "rated_current": None},
                "loads": [],
            }
        ],
    }

    base["rails"][0]["supply"]["rated_current"] = {
        "value": None,
        "unit": "A",
        "status": "VERIFIED",
        "evidence": [],
    }
    assert problems_for("power_budget", base), "VERIFIED with a null value was accepted"

    base["rails"][0]["supply"]["rated_current"] = {"value": 0.15, "unit": "A", "status": "VERIFIED"}
    assert problems_for("power_budget", base), "VERIFIED without evidence was accepted"

    base["rails"][0]["supply"]["rated_current"] = {
        "value": 0.5,
        "unit": "A",
        "status": "UNKNOWN",
    }
    assert problems_for("power_budget", base), "UNKNOWN with a non-null value was accepted"

    base["rails"][0]["supply"]["rated_current"] = {"value": None, "unit": "A", "status": "UNKNOWN"}
    assert not problems_for("power_budget", base)


def test_fault_without_mitigation_cannot_pass():
    """A fault with no mitigation may not be marked PASS by anyone."""
    doc = {
        "schema_version": 1,
        "faults": [
            {
                "fault_id": "F-001",
                "subsystem": "usb",
                "failure_mode": "VBUS_SHORT_TO_GND",
                "cause": "cable damage",
                "local_effect": "input collapses",
                "system_effect": "board loses external power",
                "mitigation_present": False,
                "status": "PASS",
            }
        ],
    }
    assert problems_for("fault_analysis", doc)
    doc["faults"][0]["status"] = "BLOCKED"
    assert not problems_for("fault_analysis", doc)


def test_fire_and_injury_require_human_risk_acceptance():
    doc = {
        "schema_version": 1,
        "faults": [
            {
                "fault_id": "F-002",
                "subsystem": "battery",
                "failure_mode": "OVERCHARGE",
                "cause": "charger regulation failure",
                "local_effect": "cell voltage exceeds maximum",
                "system_effect": "thermal runaway",
                "harm_class": "fire",
                "mitigation_present": True,
                "mitigation": ["redundant OVP comparator"],
                "status": "PASS",
            }
        ],
    }
    assert problems_for("fault_analysis", doc), "a fire-class fault passed with no risk acceptance"
    doc["faults"][0]["risk_acceptance"] = {"required": True, "approved_by": None}
    assert not problems_for("fault_analysis", doc)


def test_approval_with_conditions_requires_conditions():
    doc = {
        "schema_version": 1,
        "approvals": [
            {
                "approval_id": "APR-POWER-001",
                "gate": "POWER",
                "reviewer": "A Person",
                "date": "2026-08-17",
                "decision": "APPROVED_WITH_CONDITIONS",
                "revision": "A",
                "scope_commit": "abc1234",
            }
        ],
    }
    assert problems_for("approval", doc)
    doc["approvals"][0]["conditions"] = ["re-check thermal margin after layout"]
    assert not problems_for("approval", doc)


def test_safety_waiver_needs_a_review_condition():
    doc = {
        "schema_version": 1,
        "waivers": [
            {
                "waiver_id": "WVR-001",
                "target": {"check_id": "CHK-PIN-MAP"},
                "reason": "rail is supplied externally through connector J4",
                "scope": "single_finding",
                "approver": "A Person",
                "date": "2026-08-17",
                "safety_related": True,
            }
        ],
    }
    assert problems_for("waiver", doc)
    doc["waivers"][0]["review_condition"] = "revisit if J4 becomes a board-powered input"
    assert not problems_for("waiver", doc)


def test_calculation_input_without_provenance_must_be_flagged_as_assumption():
    doc = {
        "schema_version": 1,
        "calculations": [
            {
                "calculation_id": "PWR-REG-003",
                "formula": {
                    "type": "linear_regulator_dissipation",
                    "expression": "(Vin_max - Vout) * Iout_max",
                },
                "inputs": {"Vin_max": {"value": 5.25, "unit": "V"}},
                "status": "OK",
            }
        ],
    }
    assert problems_for("calculation", doc), "an unsourced input was accepted without assumption:true"

    doc["calculations"][0]["inputs"]["Vin_max"]["assumption"] = True
    doc["calculations"][0]["inputs"]["Vin_max"]["assumption_rationale"] = "USB 5 V +5 percent"
    assert not problems_for("calculation", doc)


# --------------------------------------------------------------- corpus artifacts


def _corpus_files(pattern: str) -> list[Path]:
    root = Path(__file__).resolve().parent / "corpus"
    return sorted(root.rglob(pattern))


@pytest.mark.parametrize(
    "path", _corpus_files("project.yaml"), ids=lambda p: str(p.parent.name)
)
def test_corpus_projects_validate(path):
    load_and_validate("project", path)


@pytest.mark.parametrize(
    "path", _corpus_files("part.yaml"), ids=lambda p: str(p.parent.name)
)
def test_corpus_parts_validate(path):
    load_and_validate("part", path)


@pytest.mark.parametrize("path", _corpus_files("pins*.yaml"), ids=lambda p: p.name)
def test_corpus_pin_tables_validate(path):
    load_and_validate("pins", path)


@pytest.mark.parametrize(
    "path", _corpus_files("expected_findings.yaml"), ids=lambda p: str(p.parent.name)
)
def test_corpus_expectations_validate(path):
    load_and_validate("expected_findings", path)


def test_gates_file_validates():
    load_and_validate("gate_definition", Path(__file__).resolve().parents[1] / "gates" / "gates.yaml")


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    """PyYAML keeps the last duplicate silently; in a pin table that is a dropped pin."""
    bad = tmp_path / "dup.yaml"
    bad.write_text("mpn: A\nmpn: B\n", encoding="utf-8")
    with pytest.raises(Exception):
        load_and_validate("part", bad)


def test_validate_reports_source_in_error():
    with pytest.raises(ValidationFailed) as excinfo:
        validate("project", {}, source="here.yaml")
    assert "here.yaml" in str(excinfo.value)


def test_schema_ids_are_unique():
    ids = []
    for path in SCHEMA_DIR.glob("*.json"):
        ids.append(json.loads(path.read_text(encoding="utf-8"))["$id"])
    assert len(ids) == len(set(ids))
