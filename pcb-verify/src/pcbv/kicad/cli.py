"""Wrapper around the ``kicad-cli`` binary.

Two jobs. First, it lets the test suite use KiCad as an **oracle** for our own readers: if
KiCad and ``pcbv.kicad.*`` disagree about a schematic's components or a symbol's pins, our
reader is wrong, and no amount of internally-consistent testing would have revealed it.

Second, it gates version-dependent features honestly. ``sch erc`` does not exist before
KiCad 8.0, so a check that needs it must report "unavailable" rather than silently reporting
a pass on a schematic nobody ran ERC over.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: ``kicad-cli sch erc`` was introduced in KiCad 8.0.
ERC_MIN_MAJOR = 8

DEFAULT_TIMEOUT = 180


class KicadCliError(RuntimeError):
    """kicad-cli was unavailable, or failed in a way the caller must not ignore."""


@dataclass(frozen=True)
class KicadVersion:
    raw: str
    major: int
    minor: int

    @property
    def supports_erc(self) -> bool:
        return self.major >= ERC_MIN_MAJOR

    def __str__(self) -> str:
        return self.raw


def kicad_cli_path() -> str | None:
    return shutil.which("kicad-cli")


@lru_cache(maxsize=1)
def kicad_version() -> KicadVersion | None:
    """Version of the kicad-cli on PATH, or ``None`` if there is none."""
    binary = kicad_cli_path()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [binary, "version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None

    raw = (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr) else ""
    match = re.match(r"(\d+)\.(\d+)", raw)
    if not match:
        return None
    return KicadVersion(raw=raw, major=int(match.group(1)), minor=int(match.group(2)))


def available() -> bool:
    return kicad_version() is not None


def _run(args: list[str], *, timeout: int = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess:
    binary = kicad_cli_path()
    if binary is None:
        raise KicadCliError("kicad-cli is not on PATH")
    return subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)


def export_netlist(schematic: Path, output: Path, *, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Export a KiCad netlist, raising if the schematic could not be loaded.

    kicad-cli 7 exits 0 even when it printed "Failed to load schematic file", so the exit
    code alone is not a usable signal -- the output text and the produced file must both be
    checked, or an unreadable schematic would look like a success.
    """
    result = _run(
        ["sch", "export", "netlist", "--output", str(output), str(schematic)], timeout=timeout
    )
    combined = f"{result.stdout}{result.stderr}"
    if "Failed to load" in combined or "Unable to load" in combined:
        raise KicadCliError(f"{schematic}: kicad-cli could not load the schematic: {combined.strip()}")
    if result.returncode != 0:
        raise KicadCliError(f"{schematic}: kicad-cli exited {result.returncode}: {combined.strip()}")
    if not output.is_file() or output.stat().st_size == 0:
        raise KicadCliError(f"{schematic}: kicad-cli produced no netlist")
    return output.read_text(encoding="utf-8")


def load_symbol_library(library: Path, *, timeout: int = DEFAULT_TIMEOUT) -> None:
    """Assert KiCad can load a symbol library, without modifying the original.

    ``sym upgrade`` rewrites the file in place, so it runs against a temporary copy.
    """
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / library.name
        staged.write_bytes(library.read_bytes())
        result = _run(["sym", "upgrade", "--force", str(staged)], timeout=timeout)
        combined = f"{result.stdout}{result.stderr}"
        if "Unable to load" in combined or "Failed to load" in combined:
            raise KicadCliError(f"{library}: kicad-cli could not load the symbol library: {combined.strip()}")


def load_footprint_library(pretty_dir: Path, *, timeout: int = DEFAULT_TIMEOUT) -> None:
    """Assert KiCad can load a ``.pretty`` footprint library, without modifying it."""
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / pretty_dir.name
        staged.mkdir()
        for mod in pretty_dir.glob("*.kicad_mod"):
            (staged / mod.name).write_bytes(mod.read_bytes())
        result = _run(["fp", "upgrade", "--force", str(staged)], timeout=timeout)
        combined = f"{result.stdout}{result.stderr}"
        if "Unable to load" in combined or "Failed to load" in combined:
            raise KicadCliError(
                f"{pretty_dir}: kicad-cli could not load the footprint library: {combined.strip()}"
            )


# ------------------------------------------------------------------ netlist parsing

_COMP_RE = re.compile(r'\(comp \(ref "([^"]+)"\)')
_LIBPART_RE = re.compile(r'\(libpart \(lib "([^"]*)"\) \(part "([^"]*)"\)')
_PIN_RE = re.compile(r'\(pin \(num "([^"]*)"\) \(name "([^"]*)"\) \(type "([^"]*)"\)\)')
_NET_RE = re.compile(r'\(net \(code "[^"]*"\) \(name "([^"]*)"\)((?:\s*\(node [^\n]*\))*)')
_NODE_RE = re.compile(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)')


def netlist_components(netlist: str) -> list[str]:
    """Reference designators KiCad found, sorted."""
    return sorted(set(_COMP_RE.findall(netlist)))


def netlist_libpart_pins(netlist: str) -> dict[str, list[tuple[str, str, str]]]:
    """Map ``lib:part`` to its ``(number, name, type)`` pins, as KiCad reports them.

    This is the oracle for our symbol reader: KiCad's own view of every pin in every symbol
    the design uses.
    """
    out: dict[str, list[tuple[str, str, str]]] = {}
    blocks = netlist.split("(libpart ")
    for block in blocks[1:]:
        match = _LIBPART_RE.match("(libpart " + block)
        if not match:
            continue
        key = f"{match.group(1)}:{match.group(2)}"
        out[key] = _PIN_RE.findall(block)
    return out


def netlist_nets(netlist: str) -> dict[str, list[tuple[str, str]]]:
    """Map net name to the ``(refdes, pin)`` nodes on it.

    This is the input Phase 1's connectivity checks need, and the canonical review unit for
    a hardware change: raw .kicad_sch diffs are not human-reviewable, normalized netlists are.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for name, nodes_blob in _NET_RE.findall(netlist):
        out[name] = sorted(_NODE_RE.findall(nodes_blob))
    return out
