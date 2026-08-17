"""Tests for the s-expression reader, units, and the KiCad readers.

These are the edge of the system: everything downstream trusts them. A reader that silently
returns an empty pin list would make CHK-PIN-MAP pass vacuously, so the "no pins" cases are
tested explicitly.
"""

from __future__ import annotations

import pytest

from pcbv.kicad.footprint import find_footprint, read_footprint_library
from pcbv.kicad.schematic import read_schematic
from pcbv.kicad.symbol_lib import read_symbol_library
from pcbv.corpus import SHARED_DIR, CORPUS_DIR
from pcbv.sexpr import SexprError, loads
from pcbv.units import KNOWN_UNITS, Quantity, UnitError, sum_quantities

SYMBOL_LIB = SHARED_DIR / "library" / "symbols" / "pcbv_example.kicad_sym"
FOOTPRINT_LIB = SHARED_DIR / "library" / "footprints" / "pcbv_example.pretty"
GOOD_SCH = CORPUS_DIR / "known_good" / "minimal_ldo" / "schematic" / "minimal_ldo.kicad_sch"


# ------------------------------------------------------------------ sexpr


def test_parses_nested_and_quoted():
    node = loads('(root (a 1) (b "text with (parens) and \\"quotes\\"") (c -2.54))')
    assert node.head == "root"
    assert node.find("a").atom(0) == 1
    assert node.find("b").str_atom(0) == 'text with (parens) and "quotes"'
    assert node.find("c").atom(0) == pytest.approx(-2.54)


def test_numbers_and_identifiers_are_distinguished():
    node = loads("(x 1 2.5 yes power_in -3)")
    assert node.atoms == [1, pytest.approx(2.5), "yes", "power_in", -3]


def test_descendants_finds_at_any_depth():
    node = loads("(a (b (c 1)) (d (e (c 2))))")
    assert len(list(node.descendants("c"))) == 2


def test_line_comments_are_ignored():
    assert loads("(a ; comment here\n (b 1))").find("b").atom(0) == 1


@pytest.mark.parametrize(
    "text",
    [
        "(unclosed",
        "(a))",
        '(a "unterminated',
        "",
        "(a) (b)",
        "((no-head))",
    ],
)
def test_malformed_input_raises(text):
    with pytest.raises(SexprError):
        loads(text)


# ------------------------------------------------------------------ units


def test_schema_unit_enum_matches_code():
    """The schema enum and pcbv.units must never drift apart."""
    import json

    from pcbv.schema import SCHEMA_DIR

    defs = json.loads((SCHEMA_DIR / "common.defs.json").read_text(encoding="utf-8"))
    schema_units = set(defs["$defs"]["unit"]["enum"])
    assert schema_units == set(KNOWN_UNITS), (
        f"only in schema: {sorted(schema_units - set(KNOWN_UNITS))}; "
        f"only in code: {sorted(set(KNOWN_UNITS) - schema_units)}"
    )


def test_si_normalization_and_comparison():
    assert Quantity(500, "mA") == Quantity(0.5, "A")
    assert Quantity(1, "kohm").si == pytest.approx(1000)
    assert Quantity(100, "nF") < Quantity(1, "uF")


def test_unit_in_string_is_rejected():
    with pytest.raises(UnitError, match="string"):
        Quantity.parse("3.3V")


def test_cross_dimension_arithmetic_is_rejected():
    with pytest.raises(UnitError, match="cannot add"):
        Quantity(3.3, "V") + Quantity(1, "A")
    with pytest.raises(UnitError, match="cannot compare"):
        _ = Quantity(3.3, "V") < Quantity(1, "A")


def test_unknown_unit_is_rejected():
    with pytest.raises(UnitError, match="unknown unit"):
        Quantity(1, "furlong")


def test_sum_quantities_mixes_prefixes_correctly():
    total = sum_quantities([Quantity(120, "mA"), Quantity(0.4, "A"), Quantity(5000, "uA")], unit="mA")
    assert total.value == pytest.approx(525)


def test_non_finite_values_rejected():
    with pytest.raises(UnitError):
        Quantity(float("nan"), "V")


# ------------------------------------------------------------------ symbols


def test_symbol_pins_are_read_from_unit_children():
    """Pins live in <NAME>_<unit>_<style> children; reading only the parent yields none."""
    symbols = read_symbol_library(SYMBOL_LIB)
    ldo = symbols["EXAMPLE-LDO-3V3"]
    assert ldo.pin_numbers == ["1", "2", "3", "4", "5"]
    assert [p.name for p in ldo.pins] == ["VIN", "GND", "EN", "NC", "VOUT"]
    assert ldo.pin("1").electrical_type == "power_in"
    assert ldo.pin("4").electrical_type == "no_connect"


def test_packages_of_one_part_have_different_pin_numbering():
    """The premise of package-qualified pin tables, asserted on the fixtures themselves."""
    symbols = read_symbol_library(SYMBOL_LIB)
    sot23 = symbols["EXAMPLE-LDO-3V3"]
    sot89 = symbols["EXAMPLE-LDO-3V3-SOT89"]
    assert sot23.pin("1").name == "VIN"
    assert sot89.pin("1").name == "GND"


def test_ground_pins_use_power_in_by_convention():
    """KiCad has no 'ground' pin type, which the type-equivalence table must accommodate."""
    symbols = read_symbol_library(SYMBOL_LIB)
    assert symbols["EXAMPLE-LDO-3V3"].pin("2").canonical_type == "power_in"


def test_no_duplicate_pins_in_fixture_symbols():
    for symbol in read_symbol_library(SYMBOL_LIB).values():
        assert not symbol.duplicate_pin_numbers()


# ------------------------------------------------------------------ footprints


def test_footprint_pads_are_read():
    library = read_footprint_library(FOOTPRINT_LIB)
    assert library["SOT-23-5"].pad_numbers == ["1", "2", "3", "4", "5"]
    assert library["SOT-89-3"].pad_numbers == ["1", "2", "3"]
    assert library["CONN-1x02-P2.54"].pad_numbers == ["1", "2"]


def test_footprint_resolution_by_lib_id():
    footprint = find_footprint([SHARED_DIR / "library"], "pcbv_example:SOT-23-5")
    assert footprint is not None
    assert footprint.pad_numbers == ["1", "2", "3", "4", "5"]


def test_unresolvable_footprint_returns_none():
    assert find_footprint([SHARED_DIR / "library"], "pcbv_example:DOES-NOT-EXIST") is None


# ------------------------------------------------------------------ schematic


def test_schematic_components_and_properties():
    design = read_schematic(GOOD_SCH)
    assert sorted(c.refdes for c in design.components) == ["C1", "C2", "J1", "U1"]

    u1 = design.component("U1")
    assert u1.mpn == "EXAMPLE-LDO-3V3"
    assert u1.package == "SOT-23-5"
    assert u1.footprint == "pcbv_example:SOT-23-5"
    assert u1.lib_id == "pcbv_example:EXAMPLE-LDO-3V3"
    assert u1.is_populated
    assert not design.parse_warnings


def test_power_flag_symbols_are_excluded():
    """References beginning with '#' are virtual and must not be treated as components."""
    design = read_schematic(GOOD_SCH)
    assert all(not c.refdes.startswith("#") for c in design.components)


def test_embedded_symbol_is_available_for_each_component():
    design = read_schematic(GOOD_SCH)
    for component in design.components:
        symbol = design.symbol_for(component)
        assert symbol is not None, f"no cached symbol for {component.refdes}"
        assert symbol.pins, f"cached symbol for {component.refdes} has no pins"


def test_capacitors_have_no_mpn_in_fixture():
    """Documents the deliberate coverage gap the known-good case records as INFO findings."""
    design = read_schematic(GOOD_SCH)
    assert design.component("C1").mpn == ""
