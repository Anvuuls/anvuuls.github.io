"""Tests that use KiCad itself as the oracle.

Everything else in this suite checks our code against our own expectations. These tests check
it against the reference implementation, which is the only way to catch the failure mode
that matters most here: fixtures that our parser accepts but KiCad does not, or a symbol
reader that is self-consistently wrong.

Skipped when ``kicad-cli`` is absent, and the skip says so explicitly rather than passing
quietly -- an invariant that silently stops being checked is worse than one that was never
claimed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcbv.corpus import CORPUS_DIR, SHARED_DIR, discover_cases
from pcbv.kicad import cli
from pcbv.kicad.schematic import read_schematic
from pcbv.kicad.symbol_lib import read_symbol_library
from pcbv.model import KICAD_PIN_TYPE_TO_CANONICAL

pytestmark = pytest.mark.requires_kicad

SCHEMATICS = sorted(CORPUS_DIR.rglob("*.kicad_sch"))
SYMBOL_LIBS = sorted(CORPUS_DIR.rglob("*.kicad_sym"))
PRETTY_DIRS = sorted(p for p in CORPUS_DIR.rglob("*.pretty") if p.is_dir())


def _require_kicad():
    version = cli.kicad_version()
    if version is None:
        pytest.skip("kicad-cli not on PATH: fixture validity against real KiCad is UNVERIFIED")
    return version


def test_kicad_cli_is_present_and_reports_a_version():
    version = _require_kicad()
    assert version.major >= 7, f"KiCad {version} is older than this project supports"


def test_corpus_is_discoverable():
    assert SCHEMATICS, "no fixture schematics found"
    assert SYMBOL_LIBS, "no fixture symbol libraries found"
    assert PRETTY_DIRS, "no fixture footprint libraries found"


@pytest.mark.parametrize("schematic", SCHEMATICS, ids=lambda p: p.stem)
def test_kicad_loads_every_fixture_schematic(schematic, tmp_path):
    """The check that closes the circularity: our hand-written fixtures are real KiCad files."""
    _require_kicad()
    netlist = cli.export_netlist(schematic, tmp_path / "out.net")
    assert "(export" in netlist


@pytest.mark.parametrize("library", SYMBOL_LIBS, ids=lambda p: f"{p.parent.parent.parent.name}/{p.name}")
def test_kicad_loads_every_fixture_symbol_library(library):
    _require_kicad()
    cli.load_symbol_library(library)


@pytest.mark.parametrize("pretty", PRETTY_DIRS, ids=lambda p: p.name)
def test_kicad_loads_every_fixture_footprint_library(pretty):
    _require_kicad()
    cli.load_footprint_library(pretty)


@pytest.mark.parametrize("schematic", SCHEMATICS, ids=lambda p: p.stem)
def test_our_reader_finds_the_same_components_as_kicad(schematic, tmp_path):
    """Our normalized model must agree with KiCad about what is on the board."""
    _require_kicad()
    netlist = cli.export_netlist(schematic, tmp_path / "out.net")
    theirs = cli.netlist_components(netlist)
    ours = sorted(c.refdes for c in read_schematic(schematic).components)
    assert ours == theirs


@pytest.mark.parametrize("schematic", SCHEMATICS, ids=lambda p: p.stem)
def test_our_symbol_reader_agrees_with_kicad_on_every_pin(schematic, tmp_path):
    """Pin numbers, names and electrical types must match KiCad's own view.

    This is the test that gives CHK-PIN-MAP its authority. The check compares symbol pins
    against a datasheet pin table; if our idea of "the symbol's pins" differed from KiCad's,
    the comparison would be against something the board does not actually use.
    """
    _require_kicad()
    netlist = cli.export_netlist(schematic, tmp_path / "out.net")
    theirs = cli.netlist_libpart_pins(netlist)
    assert theirs, "KiCad reported no libparts; the oracle produced nothing to compare against"

    design = read_schematic(schematic)
    compared = 0

    for lib_id, symbol in design.embedded_symbols.items():
        if lib_id not in theirs:
            continue
        expected = sorted(theirs[lib_id])
        actual = sorted(
            (p.number, p.name, KICAD_PIN_TYPE_TO_CANONICAL.get(p.electrical_type, p.electrical_type))
            for p in symbol.pins
        )
        # KiCad reports its own type spelling; compare through the same canonical mapping
        # the checker uses, so a wrong mapping entry fails here too.
        expected_canonical = sorted(
            (num, name, KICAD_PIN_TYPE_TO_CANONICAL.get(kind, kind)) for num, name, kind in expected
        )
        assert actual == expected_canonical, f"{lib_id}: ours={actual} kicad={expected_canonical}"
        compared += 1

    assert compared > 0, f"{schematic}: compared no symbols against KiCad"


def test_known_good_netlist_is_the_intended_circuit(tmp_path):
    """The known-good fixture must be a correct circuit, not merely a loadable file.

    A known-good case whose connectivity was accidentally broken would still produce zero
    pin-mapping findings, so it would keep passing while having stopped testing anything.
    Asserting the actual netlist is what stops that rotting silently.
    """
    _require_kicad()
    schematic = CORPUS_DIR / "known_good" / "minimal_ldo" / "schematic" / "minimal_ldo.kicad_sch"
    nets = cli.netlist_nets(cli.export_netlist(schematic, tmp_path / "out.net"))

    assert nets.get("VIN") == [("C1", "1"), ("J1", "1"), ("U1", "1"), ("U1", "3")], (
        "VIN must feed the LDO input, its input capacitor, and EN -- the datasheet forbids "
        "leaving EN floating"
    )
    assert nets.get("+3V3") == [("C2", "1"), ("U1", "5")], (
        "the regulated output must reach the output capacitor from pin 5, not pin 4"
    )
    assert nets.get("GND") == [("C1", "2"), ("C2", "2"), ("J1", "2"), ("U1", "2")]

    # Pin 4 is the datasheet's unbonded no-connect and must carry nothing.
    on_nets = {node for nodes in nets.values() for node in nodes}
    assert ("U1", "4") not in on_nets or all(
        name.startswith("unconnected-") for name, nodes in nets.items() if ("U1", "4") in nodes
    )


def test_defective_cases_still_load_and_keep_their_defect(tmp_path):
    """A known-bad fixture must be a *loadable* file with a *live* defect.

    If a defect injection broke the file, KiCad would reject it and the case would fail for
    the wrong reason; if the injection were lost, the case would pass while testing nothing.
    """
    _require_kicad()
    cases = {c.name: c for c in discover_cases() if c.kind == "known_bad"}

    netlist = cli.export_netlist(
        cases["wrong_symbol_pin"].directory / "schematic" / "wrong_symbol_pin.kicad_sch",
        tmp_path / "a.net",
    )
    pins = cli.netlist_libpart_pins(netlist)["pcbv_example:EXAMPLE-LDO-3V3"]
    by_number = {num: name for num, name, _ in pins}
    assert by_number["4"] == "VOUT" and by_number["5"] == "NC", (
        "the swapped-name defect is no longer present in the fixture"
    )

    netlist = cli.export_netlist(
        cases["reversed_connector_pinout"].directory
        / "schematic"
        / "reversed_connector_pinout.kicad_sch",
        tmp_path / "b.net",
    )
    pins = cli.netlist_libpart_pins(netlist)["pcbv_example:EXAMPLE-CONN-2P"]
    by_number = {num: name for num, name, _ in pins}
    assert by_number["1"] == "GND" and by_number["2"] == "VIN", (
        "the reversed-connector defect is no longer present in the fixture"
    )


def test_wrong_package_case_differs_only_in_the_package_property():
    """The package-variant defect must be exactly one property, or the case proves too much."""
    good = (CORPUS_DIR / "known_good" / "minimal_ldo" / "schematic" / "minimal_ldo.kicad_sch").read_text()
    bad = (
        CORPUS_DIR / "known_bad" / "wrong_package_pin_table" / "schematic"
        / "wrong_package_pin_table.kicad_sch"
    ).read_text()

    good_lines = good.splitlines()
    bad_lines = bad.splitlines()
    assert len(good_lines) == len(bad_lines)
    differing = [
        (a, b) for a, b in zip(good_lines, bad_lines) if a != b
    ]
    # One package property plus the title line that identifies the case.
    assert len(differing) == 2, f"expected 2 differing lines, got {len(differing)}: {differing}"
    assert any("SOT-89-3" in b and "Package" in b for _, b in differing)


def test_erc_availability_is_reported_honestly():
    """CHK-ERC needs KiCad 8+. On older KiCad the capability must read as unavailable."""
    version = _require_kicad()
    if version.major >= cli.ERC_MIN_MAJOR:
        assert version.supports_erc
    else:
        assert not version.supports_erc, (
            f"KiCad {version} has no 'sch erc' subcommand, so ERC must not be reported as available"
        )


def test_version_sniffing_agrees_with_the_capability_probe():
    """The declared version and the binary's actual subcommands must not disagree.

    Version sniffing is a guess about a build; the probe asks the binary. If they diverge,
    trust the probe and treat the version table as wrong -- a distribution shipping a
    patched build must not be able to make a check claim an ERC that cannot run.
    """
    version = _require_kicad()
    assert cli.erc_subcommand_available() == version.supports_erc, (
        f"KiCad {version} reports supports_erc={version.supports_erc} but the "
        f"`sch erc --help` probe says {cli.erc_subcommand_available()}"
    )


def test_erc_raises_rather_than_returning_empty_when_unavailable(tmp_path):
    """'No violations' and 'never ran' must never be the same value."""
    _require_kicad()
    if cli.erc_subcommand_available():
        pytest.skip("ERC is available here; the unavailable path cannot be exercised")
    with pytest.raises(cli.KicadCliError, match="no 'sch erc' subcommand"):
        cli.run_erc(SCHEMATICS[0], tmp_path / "erc.json")


@pytest.mark.parametrize("schematic", SCHEMATICS, ids=lambda p: p.stem)
def test_erc_runs_and_parses(schematic, tmp_path):
    """ERC executes and its JSON report parses into structured violations."""
    _require_kicad()
    if not cli.erc_subcommand_available():
        pytest.skip("kicad-cli has no 'sch erc' subcommand (needs KiCad 8+)")
    result = cli.run_erc(schematic, tmp_path / "erc.json")
    assert isinstance(result.violations, list)
    # Every violation must carry the fields a finding would need to be actionable.
    for violation in result.violations:
        assert violation.get("type"), f"ERC violation with no type: {violation}"
        assert violation.get("severity") in {"error", "warning", "ignore", "exclusion", "debug"}, violation


def test_known_good_erc_has_no_errors(tmp_path):
    """The known-good fixture must be electrically coherent to KiCad's own ERC.

    Warnings are tolerated here -- the fixture is a deliberately minimal two-part circuit and
    ERC legitimately complains about, for example, a power input with no explicit power flag.
    Errors are not: an error would mean the baseline is not a valid circuit, and a known-good
    case that is not actually good tests nothing.
    """
    _require_kicad()
    if not cli.erc_subcommand_available():
        pytest.skip("kicad-cli has no 'sch erc' subcommand (needs KiCad 8+)")
    schematic = CORPUS_DIR / "known_good" / "minimal_ldo" / "schematic" / "minimal_ldo.kicad_sch"
    result = cli.run_erc(schematic, tmp_path / "erc.json")
    errors = result.errors
    assert not errors, "ERC errors on the known-good fixture:\n" + "\n".join(
        f"  {v.get('type')}: {v.get('description')}" for v in errors
    )
