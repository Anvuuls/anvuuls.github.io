"""The normalized design model.

This is the *verification interface*. KiCad files are parsed exactly once, at the edge, by
``pcbv.kicad.*``; every check then queries these dataclasses. No check may open a
``.kicad_sch`` itself -- if a check needs something new, the field is added here.

That indirection is the whole point of the verification-first architecture: the checks stay
independent of KiCad's file format, so a format change breaks one reader rather than
fifteen checks, and the model stays queryable and stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

#: Pin electrical types as KiCad spells them, mapped to the pin-table vocabulary in
#: schemas/pins.schema.json. Kept explicit rather than guessed, because a wrong mapping
#: would make the type comparison in CHK-PIN-MAP quietly meaningless.
KICAD_PIN_TYPE_TO_CANONICAL: dict[str, str] = {
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "tri_state": "tri_state",
    "passive": "passive",
    "free": "unspecified",
    "unspecified": "unspecified",
    "power_in": "power_in",
    "power_out": "power_out",
    "open_collector": "open_collector",
    "open_emitter": "open_drain",
    "no_connect": "no_connect",
}

#: Canonical types that a datasheet pin table may legitimately model with a different but
#: electrically compatible KiCad type. Ground pins are the common case: KiCad has no
#: 'ground' pin type, so grounds are drawn as power_in by universal convention.
TYPE_EQUIVALENCE: dict[str, frozenset[str]] = {
    "ground": frozenset({"power_in", "passive"}),
    "thermal_pad": frozenset({"power_in", "passive", "unspecified"}),
    "analog_in": frozenset({"input", "passive", "unspecified", "bidirectional"}),
    "analog_out": frozenset({"output", "passive", "unspecified", "bidirectional"}),
    "rf": frozenset({"bidirectional", "passive", "unspecified", "input", "output"}),
    "crystal": frozenset({"bidirectional", "passive", "input", "output", "unspecified"}),
    "reserved": frozenset({"no_connect", "passive", "unspecified", "input"}),
    "no_connect": frozenset({"no_connect", "unspecified", "passive"}),
    "open_drain": frozenset({"open_collector", "open_drain", "bidirectional", "output"}),
    "unspecified": frozenset(KICAD_PIN_TYPE_TO_CANONICAL.values()),
}


@dataclass(frozen=True)
class SymbolPin:
    """One pin of a schematic symbol."""

    number: str
    name: str
    electrical_type: str
    unit: int = 1
    hide: bool = False

    @property
    def canonical_type(self) -> str:
        return KICAD_PIN_TYPE_TO_CANONICAL.get(self.electrical_type, "unspecified")


@dataclass
class Symbol:
    """A schematic symbol definition, with pins aggregated across all its units."""

    name: str
    library: str | None = None
    pins: list[SymbolPin] = field(default_factory=list)
    extends: str | None = None
    source_file: Path | None = None
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def lib_id(self) -> str:
        return f"{self.library}:{self.name}" if self.library else self.name

    @property
    def pin_numbers(self) -> list[str]:
        return [p.number for p in self.pins]

    def pin(self, number: str) -> SymbolPin | None:
        for p in self.pins:
            if p.number == number:
                return p
        return None

    def duplicate_pin_numbers(self) -> list[str]:
        """Pin numbers appearing more than once across units.

        Legitimate for multi-unit parts sharing a power pin, so callers decide severity;
        the model only reports the fact.
        """
        seen: dict[str, int] = {}
        for p in self.pins:
            seen[p.number] = seen.get(p.number, 0) + 1
        return sorted(n for n, count in seen.items() if count > 1)


@dataclass(frozen=True)
class Pad:
    """One pad of a footprint."""

    number: str
    pad_type: str
    shape: str

    @property
    def is_numbered(self) -> bool:
        """False for mechanical pads (mounting holes, fiducials) that carry no number."""
        return self.number not in ("", "~")


@dataclass
class Footprint:
    """A footprint definition."""

    name: str
    library: str | None = None
    pads: list[Pad] = field(default_factory=list)
    source_file: Path | None = None

    @property
    def footprint_id(self) -> str:
        return f"{self.library}:{self.name}" if self.library else self.name

    @property
    def pad_numbers(self) -> list[str]:
        """Distinct numbered pads, in first-seen order.

        Deduplicated because a single electrical pad is often drawn as several pad
        primitives -- segmented thermal pads especially -- and counting primitives instead
        of pads would produce false mismatches.
        """
        seen: list[str] = []
        for pad in self.pads:
            if pad.is_numbered and pad.number not in seen:
                seen.append(pad.number)
        return seen


@dataclass
class Component:
    """A placed component in the schematic: one reference designator."""

    refdes: str
    lib_id: str
    value: str = ""
    footprint: str = ""
    mpn: str = ""
    manufacturer: str = ""
    package: str = ""
    dnp: bool = False
    exclude_from_bom: bool = False
    on_board: bool = True
    units: tuple[int, ...] = (1,)
    sheet: str = "/"
    uuids: tuple[str, ...] = ()
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def is_populated(self) -> bool:
        return not self.dnp and self.on_board

    @property
    def symbol_name(self) -> str:
        return self.lib_id.split(":", 1)[-1]


@dataclass
class Design:
    """A normalized schematic design: what checks actually consume."""

    name: str
    root_file: Path
    components: list[Component] = field(default_factory=list)
    embedded_symbols: dict[str, Symbol] = field(default_factory=dict)
    sheets: list[Path] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)

    def component(self, refdes: str) -> Component | None:
        for c in self.components:
            if c.refdes == refdes:
                return c
        return None

    def populated_components(self) -> list[Component]:
        return [c for c in self.components if c.is_populated]

    def duplicate_refdes(self) -> list[str]:
        seen: dict[str, int] = {}
        for c in self.components:
            seen[c.refdes] = seen.get(c.refdes, 0) + 1
        return sorted(r for r, count in seen.items() if count > 1)

    def symbol_for(self, component: Component) -> Symbol | None:
        """The symbol as embedded in the schematic -- what is actually drawn.

        The embedded copy is authoritative for 'what this design does', because that is
        what KiCad's own netlist and ERC use. Drift between it and the external library is
        itself a defect worth reporting, not something to silently prefer one way.
        """
        return self.embedded_symbols.get(component.lib_id)


@dataclass
class PartLibrary:
    """Loaded library part records, keyed by MPN. Reusable across projects."""

    parts: dict[str, dict] = field(default_factory=dict)
    pin_tables: dict[tuple[str, str], dict] = field(default_factory=dict)
    roots: dict[str, Path] = field(default_factory=dict)

    def package_entry(self, mpn: str, package: str) -> dict | None:
        part = self.parts.get(mpn)
        if not part:
            return None
        for entry in part.get("packages", []):
            if entry["package"] == package:
                return entry
        return None

    def packages_for(self, mpn: str) -> list[str]:
        part = self.parts.get(mpn)
        return [e["package"] for e in part.get("packages", [])] if part else []

    def pin_table(self, mpn: str, package: str) -> dict | None:
        """The package-qualified pin table.

        Never falls back to another package: two packages of one die routinely number pins
        differently, and a silent fallback is exactly the failure this design forbids.
        """
        return self.pin_tables.get((mpn, package))

    def iter_parts(self) -> Iterable[tuple[str, dict]]:
        return sorted(self.parts.items())
