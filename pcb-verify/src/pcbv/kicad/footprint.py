"""Reader for ``.kicad_mod`` footprints and ``.pretty`` footprint libraries."""

from __future__ import annotations

from pathlib import Path

from ..model import Footprint, Pad
from ..sexpr import Node, SexprError, load


class FootprintError(SexprError):
    """The footprint could not be interpreted."""


def parse_footprint(root: Node, *, library: str | None, source: Path | None) -> Footprint:
    """Build a :class:`Footprint` from a parsed ``.kicad_mod`` document."""
    if root.head not in ("footprint", "module"):
        raise FootprintError(f"expected (footprint ...) or (module ...), got ({root.head} ...)")

    name = root.opt_str_atom(0) or (source.stem if source else "")
    # A footprint id may be stored as 'lib:NAME'; keep only the name part.
    if ":" in name:
        name = name.split(":", 1)[1]

    pads: list[Pad] = []
    for pad_node in root.find_all("pad"):
        atoms = pad_node.atoms
        if not atoms:
            raise FootprintError(f"{source}: (pad ...) with no number")
        number = str(atoms[0])
        pad_type = str(atoms[1]) if len(atoms) > 1 else "smd"
        shape = str(atoms[2]) if len(atoms) > 2 else ""
        pads.append(Pad(number=number, pad_type=pad_type, shape=shape))

    return Footprint(name=name, library=library, pads=pads, source_file=source)


def read_footprint(path: str | Path, *, library: str | None = None) -> Footprint:
    """Read a single ``.kicad_mod`` file.

    The library nickname defaults to the containing ``.pretty`` directory's stem, matching
    how KiCad resolves footprint ids.
    """
    p = Path(path)
    if library is None:
        parent = p.parent.name
        library = parent[:-7] if parent.endswith(".pretty") else parent or None
    return parse_footprint(load(p), library=library, source=p)


def read_footprint_library(directory: str | Path) -> dict[str, Footprint]:
    """Read every ``.kicad_mod`` in a directory, keyed by footprint name."""
    d = Path(directory)
    if not d.is_dir():
        raise FootprintError(f"footprint library directory not found: {d}")
    library = d.name[:-7] if d.name.endswith(".pretty") else d.name

    out: dict[str, Footprint] = {}
    for path in sorted(d.glob("*.kicad_mod")):
        footprint = read_footprint(path, library=library)
        # Trust the filename over the internal name: KiCad resolves footprints by filename,
        # so a stale internal name must not shadow the file actually referenced.
        footprint.name = path.stem
        out[footprint.name] = footprint
    return out


def find_footprint(search_paths: list[Path], footprint_id: str) -> Footprint | None:
    """Resolve a ``library:NAME`` footprint id against candidate library directories.

    Roots are searched in precedence order, and within a root the correctly-named
    ``.pretty`` directory wins over any other match, so a case-local override resolves
    ahead of the shared library and a same-named footprint in the wrong library cannot be
    picked up by accident.
    """
    _, _, name = footprint_id.rpartition(":")
    library = footprint_id.rpartition(":")[0]

    for root in search_paths:
        if not root.is_dir():
            continue

        candidates: list[Path] = []
        if library:
            # Exact library match at any depth, e.g. library/footprints/<lib>.pretty/.
            candidates.extend(sorted(root.rglob(f"{library}.pretty/{name}.kicad_mod")))
        candidates.append(root / f"{name}.kicad_mod")
        candidates.extend(sorted(root.rglob(f"*.pretty/{name}.kicad_mod")))

        for candidate in candidates:
            if candidate.is_file():
                footprint = read_footprint(candidate, library=library or None)
                footprint.name = name
                return footprint
    return None
