"""Reader for ``.kicad_sym`` symbol libraries.

KiCad nests the pins of a symbol inside per-unit child symbols named
``<PARENT>_<unit>_<bodystyle>``, so pins must be gathered from those children and tagged
with the unit they came from. Reading only the top-level node returns zero pins, which
would make a pin-mapping check pass vacuously -- the exact class of false confidence this
pipeline exists to prevent, so the reader raises instead when a symbol yields no pins.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..model import Symbol, SymbolPin
from ..sexpr import Node, SexprError, load

#: ``NAME_1_1`` -> unit 1, body style 1.
_UNIT_SUFFIX = re.compile(r"^(?P<parent>.+)_(?P<unit>\d+)_(?P<style>\d+)$")


class SymbolLibraryError(SexprError):
    """The symbol library could not be interpreted."""


def _pins_from(node: Node, unit: int) -> list[SymbolPin]:
    pins: list[SymbolPin] = []
    for pin_node in node.find_all("pin"):
        atoms = pin_node.atoms
        electrical = str(atoms[0]) if atoms else "unspecified"
        number = pin_node.child_str("number")
        name = pin_node.child_str("name")
        if number is None:
            raise SymbolLibraryError(f"pin with no (number ...) in symbol unit {unit}")
        pins.append(
            SymbolPin(
                number=str(number),
                name=str(name) if name is not None else "",
                electrical_type=electrical,
                unit=unit,
                hide=pin_node.find("hide") is not None,
            )
        )
    return pins


def _properties_from(node: Node) -> dict[str, str]:
    props: dict[str, str] = {}
    for prop in node.find_all("property"):
        atoms = prop.atoms
        if len(atoms) >= 2:
            props[str(atoms[0])] = str(atoms[1])
    return props


def parse_symbol(node: Node, *, library: str | None, source: Path | None) -> Symbol:
    """Build a :class:`Symbol` from one top-level ``(symbol ...)`` node."""
    name = node.str_atom(0)
    extends_node = node.find("extends")
    symbol = Symbol(
        name=name,
        library=library,
        extends=extends_node.str_atom(0) if extends_node else None,
        source_file=source,
        properties=_properties_from(node),
    )

    # Pins directly on the parent are unusual but legal.
    symbol.pins.extend(_pins_from(node, unit=1))

    for child in node.find_all("symbol"):
        child_name = child.str_atom(0)
        match = _UNIT_SUFFIX.match(child_name)
        unit = int(match.group("unit")) if match else 1
        symbol.pins.extend(_pins_from(child, unit=unit))

    return symbol


def read_symbol_library(path: str | Path) -> dict[str, Symbol]:
    """Read a ``.kicad_sym`` file, returning symbols keyed by name.

    The library nickname is taken from the filename, which is how KiCad's library table
    normally resolves ``lib_id`` prefixes.
    """
    p = Path(path)
    root = load(p)
    if root.head != "kicad_symbol_lib":
        raise SymbolLibraryError(f"{p}: expected (kicad_symbol_lib ...), got ({root.head} ...)")

    library = p.stem
    symbols: dict[str, Symbol] = {}
    for node in root.find_all("symbol"):
        symbol = parse_symbol(node, library=library, source=p)
        if symbol.name in symbols:
            raise SymbolLibraryError(f"{p}: duplicate symbol {symbol.name!r}")
        symbols[symbol.name] = symbol

    _resolve_extends(symbols, p)
    return symbols


def _resolve_extends(symbols: dict[str, Symbol], source: Path) -> None:
    """Copy pins into derived symbols that ``extends`` a parent.

    A derived symbol inherits its parent's pins and must be checked against the pin table
    with them, otherwise it appears to have none.
    """
    for symbol in symbols.values():
        if not symbol.extends or symbol.pins:
            continue
        seen: set[str] = set()
        current = symbol
        while current.extends:
            if current.extends in seen:
                raise SymbolLibraryError(f"{source}: extends cycle at {current.name!r}")
            seen.add(current.extends)
            parent = symbols.get(current.extends)
            if parent is None:
                raise SymbolLibraryError(
                    f"{source}: symbol {symbol.name!r} extends unknown symbol {current.extends!r}"
                )
            if parent.pins:
                symbol.pins = list(parent.pins)
                break
            current = parent


def read_embedded_symbols(lib_symbols: Node, *, source: Path | None) -> dict[str, Symbol]:
    """Read the ``(lib_symbols ...)`` block cached inside a ``.kicad_sch``.

    Keyed by full ``lib_id`` (``library:NAME``) because that is how schematic instances
    refer to them.
    """
    out: dict[str, Symbol] = {}
    for node in lib_symbols.find_all("symbol"):
        full = node.str_atom(0)
        library, _, name = full.rpartition(":")
        symbol = parse_symbol(node, library=library or None, source=source)
        symbol.name = name or full
        out[full] = symbol
    _resolve_embedded_extends(out)
    return out


def _resolve_embedded_extends(symbols: dict[str, Symbol]) -> None:
    by_name = {s.name: s for s in symbols.values()}
    for symbol in symbols.values():
        if symbol.extends and not symbol.pins:
            parent = by_name.get(symbol.extends)
            if parent is not None and parent.pins:
                symbol.pins = list(parent.pins)
