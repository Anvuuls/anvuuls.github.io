"""Wrapper around the ``kicad-cli`` binary.

Two jobs. First, it lets the test suite use KiCad as an **oracle** for our own readers: if
KiCad and ``pcbv.kicad.*`` disagree about a schematic's components or a symbol's pins, our
reader is wrong, and no amount of internally-consistent testing would have revealed it.

Second, it gates version-dependent features honestly. ``sch erc`` does not exist before
KiCad 8.0, so a check that needs it must report "unavailable" rather than silently reporting
a pass on a schematic nobody ran ERC over.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..sexpr import Node, loads

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


@lru_cache(maxsize=1)
def erc_subcommand_available() -> bool:
    """Probe whether ``sch erc`` actually exists, rather than inferring it from a version.

    Version sniffing is a guess about a build; this asks the binary. A distribution could
    ship a patched or partial build, and a check that assumed ERC was present would then
    report a pass for an ERC that never ran.
    """
    if kicad_cli_path() is None:
        return False
    try:
        result = _run(["sch", "erc", "--help"], timeout=60)
    except (KicadCliError, subprocess.SubprocessError, OSError):
        return False
    # KiCad 7 has no 'erc' subcommand and exits non-zero with a usage error listing only
    # the subcommands it does have. A zero exit from `sch erc --help` means it exists.
    return result.returncode == 0


@dataclass(frozen=True)
class ErcResult:
    """Parsed ERC report."""

    violations: list[dict]
    raw: dict

    def by_severity(self, severity: str) -> list[dict]:
        return [v for v in self.violations if v.get("severity") == severity]

    @property
    def errors(self) -> list[dict]:
        return self.by_severity("error")

    @property
    def warnings(self) -> list[dict]:
        return self.by_severity("warning")


def run_erc(schematic: Path, output: Path, *, timeout: int = DEFAULT_TIMEOUT) -> ErcResult:
    """Run KiCad ERC and return the parsed report.

    Raises when ERC is unavailable instead of returning an empty result: "no violations"
    and "never ran" must never be the same value.
    """
    if not erc_subcommand_available():
        version = kicad_version()
        raise KicadCliError(
            f"kicad-cli {version or '(absent)'} has no 'sch erc' subcommand; ERC cannot be "
            f"reported as passing"
        )

    result = _run(
        [
            "sch", "erc",
            "--output", str(output),
            "--format", "json",
            "--severity-all",
            str(schematic),
        ],
        timeout=timeout,
    )
    combined = f"{result.stdout}{result.stderr}"
    if "Failed to load" in combined or "Unable to load" in combined:
        raise KicadCliError(f"{schematic}: kicad-cli could not load the schematic for ERC: {combined.strip()}")
    if not output.is_file():
        raise KicadCliError(f"{schematic}: ERC produced no report ({combined.strip()})")

    report = json.loads(output.read_text(encoding="utf-8"))
    violations: list[dict] = []
    for sheet in report.get("sheets", []):
        for violation in sheet.get("violations", []):
            enriched = dict(violation)
            enriched.setdefault("sheet", sheet.get("path", "/"))
            violations.append(enriched)
    return ErcResult(violations=violations, raw=report)


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

# Parsed with the project's own s-expression reader rather than regexes. KiCad 7 emitted
# `(comp (ref "U1")` on one line while KiCad 10 pretty-prints each element on its own, so
# any line-shape assumption silently stops matching on a toolchain upgrade -- and a parser
# that returns nothing looks exactly like a design with no components.


def _parse_netlist(netlist: str) -> Node:
    root = loads(netlist)
    if root.head != "export":
        raise KicadCliError(f"expected a KiCad netlist starting with (export ...), got ({root.head} ...)")
    return root


def netlist_components(netlist: str) -> list[str]:
    """Reference designators KiCad found, sorted."""
    root = _parse_netlist(netlist)
    components = root.find("components")
    if components is None:
        return []
    refs = []
    for comp in components.find_all("comp"):
        ref = comp.child_str("ref")
        if ref:
            refs.append(str(ref))
    return sorted(set(refs))


def netlist_libpart_pins(netlist: str) -> dict[str, list[tuple[str, str, str]]]:
    """Map ``lib:part`` to its ``(number, name, type)`` pins, as KiCad reports them.

    This is the oracle for our symbol reader: KiCad's own view of every pin in every symbol
    the design uses.
    """
    root = _parse_netlist(netlist)
    libparts = root.find("libparts")
    out: dict[str, list[tuple[str, str, str]]] = {}
    if libparts is None:
        return out

    for libpart in libparts.find_all("libpart"):
        lib = libpart.child_str("lib") or ""
        part = libpart.child_str("part") or ""
        pins_node = libpart.find("pins")
        pins: list[tuple[str, str, str]] = []
        if pins_node is not None:
            for pin in pins_node.find_all("pin"):
                pins.append(
                    (
                        str(pin.child_str("num") or ""),
                        str(pin.child_str("name") or ""),
                        str(pin.child_str("type") or ""),
                    )
                )
        out[f"{lib}:{part}"] = pins
    return out


def netlist_nets(netlist: str) -> dict[str, list[tuple[str, str]]]:
    """Map net name to the ``(refdes, pin)`` nodes on it.

    This is the input Phase 1's connectivity checks need, and the canonical review unit for
    a hardware change: raw .kicad_sch diffs are not human-reviewable, normalized netlists are.
    """
    root = _parse_netlist(netlist)
    nets = root.find("nets")
    out: dict[str, list[tuple[str, str]]] = {}
    if nets is None:
        return out

    for net in nets.find_all("net"):
        name = str(net.child_str("name") or "")
        nodes = [
            (str(node.child_str("ref") or ""), str(node.child_str("pin") or ""))
            for node in net.find_all("node")
        ]
        out[name] = sorted(nodes)
    return out
