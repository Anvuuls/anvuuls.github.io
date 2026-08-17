"""Reader for ``.kicad_sch`` schematics, producing a normalized :class:`Design`.

Hierarchical sheets are followed so a design's components are collected across every sheet
rather than only the root. A multi-unit part appears as several ``(symbol ...)`` instances
sharing one reference designator; those are merged into a single :class:`Component` with the
units it occupies, because reporting "U1" three times would make findings unreadable.
"""

from __future__ import annotations

from pathlib import Path

from ..model import Component, Design
from ..sexpr import Node, SexprError, load
from .symbol_lib import read_embedded_symbols


class SchematicError(SexprError):
    """The schematic could not be interpreted."""


def _bool_flag(node: Node, head: str, *, default: bool) -> bool:
    """Read a KiCad boolean, tolerating both ``(dnp yes)`` and bare ``(dnp)`` forms."""
    child = node.find(head)
    if child is None:
        return default
    atom = child.opt_str_atom(0)
    if atom is None:
        return True
    return str(atom).lower() in ("yes", "true", "1")


def _instance_properties(node: Node) -> dict[str, str]:
    props: dict[str, str] = {}
    for prop in node.find_all("property"):
        atoms = prop.atoms
        if len(atoms) >= 2:
            props[str(atoms[0])] = str(atoms[1])
        elif len(atoms) == 1:
            props[str(atoms[0])] = ""
    return props


#: Property names commonly used for a manufacturer part number, in preference order.
_MPN_KEYS = ("MPN", "Mpn", "mpn", "Manufacturer Part Number", "MANUFACTURER_PART_NUMBER", "PartNumber")
_MFR_KEYS = ("Manufacturer", "MANUFACTURER", "Mfr", "MFR")
_PACKAGE_KEYS = ("Package", "PACKAGE", "Pkg")


def _first_present(props: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = props.get(key)
        if value:
            return value
    return ""


def _read_sheet(
    path: Path,
    *,
    sheet_path: str,
    design: Design,
    visited: set[Path],
    instances: dict[str, list[tuple[Node, str]]],
) -> None:
    """Recursively read one sheet file, accumulating symbol instances and sub-sheets."""
    resolved = path.resolve()
    if resolved in visited:
        design.parse_warnings.append(f"sheet {path} referenced more than once; not re-read")
        return
    visited.add(resolved)
    design.sheets.append(path)

    root = load(path)
    if root.head != "kicad_sch":
        raise SchematicError(f"{path}: expected (kicad_sch ...), got ({root.head} ...)")

    lib_symbols = root.find("lib_symbols")
    if lib_symbols is not None:
        for lib_id, symbol in read_embedded_symbols(lib_symbols, source=path).items():
            existing = design.embedded_symbols.get(lib_id)
            if existing is None:
                design.embedded_symbols[lib_id] = symbol
            elif existing.pin_numbers != symbol.pin_numbers:
                # Two sheets caching different versions of one symbol is a real defect:
                # KiCad would net them differently depending on which sheet a part is on.
                design.parse_warnings.append(
                    f"symbol {lib_id} cached inconsistently across sheets "
                    f"({existing.source_file} vs {path})"
                )

    for node in root.find_all("symbol"):
        lib_id = node.child_str("lib_id")
        if lib_id is None:
            continue  # a graphic-only symbol; nothing to verify
        props = _instance_properties(node)
        refdes = props.get("Reference", "")
        if not refdes or refdes.startswith("#"):
            continue  # power-flag and other virtual symbols carry '#' references
        instances.setdefault(refdes, []).append((node, sheet_path))

    for sheet_node in root.find_all("sheet"):
        sheet_props = _instance_properties(sheet_node)
        filename = sheet_props.get("Sheetfile") or sheet_props.get("Sheet file")
        sheet_name = sheet_props.get("Sheetname") or sheet_props.get("Sheet name") or ""
        if not filename:
            continue
        child_path = path.parent / filename
        if not child_path.is_file():
            design.parse_warnings.append(f"sub-sheet file not found: {child_path}")
            continue
        _read_sheet(
            child_path,
            sheet_path=f"{sheet_path}{sheet_name}/",
            design=design,
            visited=visited,
            instances=instances,
        )


def read_schematic(path: str | Path, *, name: str | None = None) -> Design:
    """Read a schematic and every sheet below it into a normalized :class:`Design`."""
    root_path = Path(path)
    if not root_path.is_file():
        raise SchematicError(f"schematic not found: {root_path}")

    design = Design(name=name or root_path.stem, root_file=root_path)
    instances: dict[str, list[tuple[Node, str]]] = {}
    _read_sheet(root_path, sheet_path="/", design=design, visited=set(), instances=instances)

    for refdes, placements in sorted(instances.items()):
        design.components.append(_merge_instances(refdes, placements, design))

    return design


def _merge_instances(
    refdes: str, placements: list[tuple[Node, str]], design: Design
) -> Component:
    """Collapse the placements sharing one reference designator into a Component."""
    first_node, first_sheet = placements[0]
    props = _instance_properties(first_node)

    lib_ids = {str(node.child_str("lib_id")) for node, _ in placements}
    if len(lib_ids) > 1:
        design.parse_warnings.append(
            f"{refdes} placed with conflicting lib_ids: {', '.join(sorted(lib_ids))}"
        )

    units: list[int] = []
    uuids: list[str] = []
    for node, _ in placements:
        unit_node = node.find("unit")
        if unit_node is not None:
            try:
                units.append(int(float(unit_node.str_atom(0))))
            except (ValueError, SexprError):
                pass
        uuid_node = node.find("uuid")
        if uuid_node is not None:
            uuids.append(uuid_node.str_atom(0))

    return Component(
        refdes=refdes,
        lib_id=str(first_node.child_str("lib_id")),
        value=props.get("Value", ""),
        footprint=props.get("Footprint", ""),
        mpn=_first_present(props, _MPN_KEYS),
        manufacturer=_first_present(props, _MFR_KEYS),
        package=_first_present(props, _PACKAGE_KEYS),
        dnp=any(_bool_flag(node, "dnp", default=False) for node, _ in placements),
        exclude_from_bom=not all(_bool_flag(node, "in_bom", default=True) for node, _ in placements),
        on_board=all(_bool_flag(node, "on_board", default=True) for node, _ in placements),
        units=tuple(sorted(set(units))) or (1,),
        sheet=first_sheet,
        uuids=tuple(uuids),
        properties=props,
    )
