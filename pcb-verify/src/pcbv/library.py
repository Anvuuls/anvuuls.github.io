"""Loading of the verified part library.

``library/parts/<MPN>/`` is a reusable, versioned asset shared across projects -- arguably
the most valuable durable output of this pipeline, since a verified symbol/footprint/pin
table compounds over every board that uses the part. It is deliberately not project scratch
space.
"""

from __future__ import annotations

from pathlib import Path

from .model import PartLibrary
from .schema import ValidationFailed, load_and_validate


class LibraryError(Exception):
    """The part library is inconsistent."""


def load_part_library(*roots: str | Path) -> tuple[PartLibrary, list[str]]:
    """Load every part record found under the given library roots.

    Roots are in **precedence order**: an MPN defined in an earlier root shadows the same
    MPN in a later one, which is how a corpus case overrides one part of the shared example
    library without duplicating all of it. A duplicate within a *single* root is still a
    problem, because there it is an accident rather than an override.

    Returns the library and a list of non-fatal problems. Problems are returned rather than
    raised so one malformed record does not hide the state of the rest.
    """
    library = PartLibrary()
    problems: list[str] = []
    root_of: dict[str, int] = {}

    for precedence, root in enumerate(roots):
        parts_dir = Path(root)
        if not parts_dir.is_dir():
            continue

        for part_file in sorted(parts_dir.glob("*/part.yaml")):
            try:
                record = load_and_validate("part", part_file)
            except (ValidationFailed, OSError) as exc:
                problems.append(str(exc))
                continue

            mpn = record["mpn"]
            if mpn in library.parts:
                if root_of[mpn] == precedence:
                    problems.append(
                        f"{part_file}: MPN {mpn} defined twice in the same library root "
                        f"({library.roots[mpn]})"
                    )
                continue
            root_of[mpn] = precedence

            if part_file.parent.name != mpn:
                problems.append(
                    f"{part_file}: directory name {part_file.parent.name!r} does not match "
                    f"mpn {mpn!r}; the directory name is how parts are located"
                )

            library.parts[mpn] = record
            library.roots[mpn] = part_file.parent

            problems.extend(_load_pin_tables(library, mpn, record, part_file.parent))

    return library, problems


def _load_pin_tables(
    library: PartLibrary, mpn: str, record: dict, part_dir: Path
) -> list[str]:
    """Load the package-qualified pin table for each package of a part."""
    problems: list[str] = []

    for package_entry in record.get("packages", []):
        package = package_entry["package"]
        relative = package_entry.get("pin_table", "pins.yaml")
        pin_file = part_dir / relative

        if not pin_file.is_file():
            problems.append(
                f"{part_dir}: package {package} declares pin_table {relative!r} which does not exist"
            )
            continue

        try:
            table = load_and_validate("pins", pin_file)
        except (ValidationFailed, OSError) as exc:
            problems.append(str(exc))
            continue

        if table["mpn"] != mpn:
            problems.append(f"{pin_file}: mpn {table['mpn']!r} does not match part record {mpn!r}")
            continue

        if table["package"] != package:
            problems.append(
                f"{pin_file}: declares package {table['package']!r} but was referenced as the "
                f"pin table for {package!r}; a pin table must never be shared across packages"
            )
            continue

        key = (mpn, package)
        if key in library.pin_tables:
            problems.append(f"{pin_file}: duplicate pin table for {mpn} / {package}")
            continue

        duplicates = _duplicate_pin_numbers(table)
        if duplicates:
            problems.append(
                f"{pin_file}: duplicate pin number(s) {', '.join(duplicates)} in the pin table"
            )

        library.pin_tables[key] = table

    return problems


def _duplicate_pin_numbers(table: dict) -> list[str]:
    seen: dict[str, int] = {}
    for pin in table.get("pins", []):
        number = pin["number"]
        seen[number] = seen.get(number, 0) + 1
    return sorted(n for n, count in seen.items() if count > 1)


def symbol_search_paths(*roots: str | Path) -> list[Path]:
    """Directories to search for ``.kicad_sym`` files."""
    out: list[Path] = []
    for root in roots:
        p = Path(root)
        if p.is_dir():
            out.append(p)
            out.extend(sorted(d for d in p.iterdir() if d.is_dir()))
    return out


def find_symbol_libraries(*roots: str | Path) -> list[Path]:
    """Every ``.kicad_sym`` file under the given roots."""
    out: list[Path] = []
    for root in roots:
        p = Path(root)
        if p.is_dir():
            out.extend(sorted(p.rglob("*.kicad_sym")))
    return out
