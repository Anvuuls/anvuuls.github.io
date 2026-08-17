"""CHK-PIN-MAP: datasheet pin table -> KiCad symbol -> assigned footprint.

The first check implemented, because a pin-number mismatch on a downloaded symbol is the
classic cause of a dead first-revision board, it is invisible to ERC, and it is entirely
deterministic. Three representations must agree:

    manufacturer pin table  ->  symbol pin numbers/names/types  ->  footprint pad numbers

Everything is compared per *package*: two packages of one die routinely number pins
differently, so a family-level comparison would be worse than none.

Where verification is impossible -- no part record, no declared package -- the check emits an
INFO finding recording the coverage gap rather than passing over it silently. Silence is
never a pass.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..findings import Finding
from ..kicad.footprint import find_footprint
from ..kicad.symbol_lib import read_symbol_library
from ..model import TYPE_EQUIVALENCE, Component, Footprint, Symbol
from . import CheckContext, register

CHECK_ID = "CHK-PIN-MAP"

#: Overbar/active-low spellings that mean the same signal.
_ACTIVE_LOW = (
    re.compile(r"^~\{(?P<base>.+)\}$"),
    re.compile(r"^~(?P<base>.+)$"),
    re.compile(r"^/(?P<base>.+)$"),
    re.compile(r"^!(?P<base>.+)$"),
    re.compile(r"^(?P<base>.+)#$"),
    re.compile(r"^(?P<base>.+)_N$"),
)


def normalize_pin_name(name: str) -> tuple[str, bool]:
    """Return ``(canonical_name, active_low)``.

    KiCad writes an overbar as ``~{RESET}`` while datasheets use ``RESET#``, ``nRESET`` or
    ``/RESET``. Without normalization every active-low pin would report a false mismatch,
    which would train reviewers to ignore this check -- the worst possible outcome.
    """
    cleaned = name.strip().upper().replace(" ", "")
    active_low = False
    changed = True
    while changed:
        changed = False
        for pattern in _ACTIVE_LOW:
            match = pattern.match(cleaned)
            if match:
                cleaned = match.group("base")
                active_low = True
                changed = True
                break
    return cleaned, active_low


def _names_match(datasheet_name: str, aliases: list[str], symbol_name: str) -> bool:
    """Whether a symbol pin name matches the datasheet name or any declared alias."""
    target, target_low = normalize_pin_name(symbol_name)
    candidates = [datasheet_name, *aliases]
    for candidate in candidates:
        base, low = normalize_pin_name(candidate)
        if base == target and low == target_low:
            return True
        # A datasheet name like 'GPIO0/BOOT' legitimately abbreviates to either half.
        for piece in re.split(r"[/,]", base):
            if piece and piece == target and low == target_low:
                return True
    return False


def _types_compatible(datasheet_type: str, symbol_pin_type: str) -> bool:
    """Whether the symbol's electrical type is acceptable for the datasheet pin type."""
    if datasheet_type == symbol_pin_type:
        return True
    allowed = TYPE_EQUIVALENCE.get(datasheet_type)
    if allowed and symbol_pin_type in allowed:
        return True
    # 'unspecified' in the symbol is weak but not wrong; report it as a type mismatch only
    # for pins where the type carries real meaning (power and ground).
    if symbol_pin_type == "unspecified" and datasheet_type not in ("power_in", "power_out", "ground"):
        return True
    return False


def _load_external_symbol(context: CheckContext, lib_id: str) -> Symbol | None:
    """Find a symbol in the on-disk libraries, loading each library at most once."""
    library_nickname, _, name = lib_id.rpartition(":")
    if library_nickname not in context.symbol_libraries:
        for candidate in _candidate_symbol_files(context, library_nickname):
            try:
                context.symbol_libraries[library_nickname] = read_symbol_library(candidate)
                break
            except Exception:  # noqa: BLE001 - a broken library is reported by its own finding
                continue
        else:
            context.symbol_libraries[library_nickname] = {}
    return context.symbol_libraries[library_nickname].get(name)


def _candidate_symbol_files(context: CheckContext, nickname: str) -> list[Path]:
    roots = [context.design_dir, *context.footprint_roots]
    seen: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        seen.extend(sorted(root.rglob(f"{nickname}.kicad_sym")))
    return seen


def _resolve_footprint(context: CheckContext, footprint_id: str) -> Footprint | None:
    roots = [context.design_dir, *context.footprint_roots]
    return find_footprint(roots, footprint_id)


@register(
    CHECK_ID,
    gates=["SYMBOL", "FOOTPRINT"],
    description="Datasheet pin table vs KiCad symbol pins vs assigned footprint pads",
)
def check_pin_mapping(context: CheckContext) -> list[Finding]:
    findings: list[Finding] = []

    for component in sorted(context.design.components, key=lambda c: c.refdes):
        if not component.is_populated:
            continue
        findings.extend(_check_component(context, component))

    return findings


def _check_component(context: CheckContext, component: Component) -> list[Finding]:
    findings: list[Finding] = []
    where = {"file": str(context.design.root_file.name), "sheet": component.sheet}

    if not component.mpn:
        return [
            Finding(
                check_id=CHECK_ID,
                code="SKIPPED_NO_MPN",
                severity="INFO",
                component=component.refdes,
                message=(
                    f"{component.refdes} declares no MPN property, so its pin mapping cannot be "
                    f"verified against a manufacturer pin table"
                ),
                remediation="Add an MPN property and a library/parts record (gate PART, CHK-PART-RECORDS)",
                location=where,
            )
        ]

    part = context.library.parts.get(component.mpn)
    if part is None:
        return [
            Finding(
                check_id=CHECK_ID,
                code="PART_RECORD_MISSING",
                severity="HIGH",
                component=component.refdes,
                mpn=component.mpn,
                message=(
                    f"{component.refdes} uses MPN {component.mpn} but no library/parts record "
                    f"exists, so nothing authoritative constrains its pinout"
                ),
                remediation=f"Create library/parts/{component.mpn}/part.yaml with a package-qualified pin table",
                location=where,
            )
        ]

    if not component.package:
        available = context.library.packages_for(component.mpn)
        return [
            Finding(
                check_id=CHECK_ID,
                code="PACKAGE_UNDECLARED",
                severity="HIGH",
                component=component.refdes,
                mpn=component.mpn,
                message=(
                    f"{component.refdes} declares no Package property; pin tables are "
                    f"package-qualified so the correct one cannot be selected"
                ),
                expected=f"one of: {', '.join(available)}" if available else None,
                remediation="Add a Package property matching a package in the part record",
                location=where,
            )
        ]

    package_entry = context.library.package_entry(component.mpn, component.package)
    if package_entry is None:
        return [
            Finding(
                check_id=CHECK_ID,
                code="PACKAGE_NOT_IN_PART_RECORD",
                severity="CRITICAL",
                component=component.refdes,
                mpn=component.mpn,
                package=component.package,
                message=(
                    f"{component.refdes} is drawn as package {component.package}, which the part "
                    f"record for {component.mpn} does not define"
                ),
                expected=context.library.packages_for(component.mpn),
                actual=component.package,
                remediation="Correct the Package property, or add the package (with its own pin table) to the part record",
                location=where,
            )
        ]

    pin_table = context.library.pin_table(component.mpn, component.package)
    if pin_table is None:
        return [
            Finding(
                check_id=CHECK_ID,
                code="PIN_TABLE_MISSING",
                severity="CRITICAL",
                component=component.refdes,
                mpn=component.mpn,
                package=component.package,
                message=(
                    f"no pin table loaded for {component.mpn} / {component.package}; pin mapping "
                    f"is unverifiable and must not be assumed correct"
                ),
                remediation="Add the package-qualified pins.yaml for this package",
                location=where,
            )
        ]

    findings.extend(_check_declared_pin_count(component, package_entry, pin_table, where))
    findings.extend(_check_declared_bindings(component, package_entry, where))

    symbol = context.design.symbol_for(component)
    if symbol is None:
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="SYMBOL_NOT_FOUND",
                severity="CRITICAL",
                component=component.refdes,
                mpn=component.mpn,
                message=f"{component.refdes} references symbol {component.lib_id}, which is not cached in the schematic",
                location=where,
            )
        )
    else:
        findings.extend(_check_symbol_against_table(context, component, symbol, pin_table, where))

    footprint = _resolve_footprint(context, component.footprint) if component.footprint else None
    if component.footprint and footprint is None:
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="FOOTPRINT_NOT_FOUND",
                severity="CRITICAL",
                component=component.refdes,
                mpn=component.mpn,
                package=component.package,
                message=f"{component.refdes} assigns footprint {component.footprint}, which could not be resolved",
                remediation="Check the footprint library path and nickname",
                location=where,
            )
        )
    elif not component.footprint:
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="FOOTPRINT_UNASSIGNED",
                severity="HIGH",
                component=component.refdes,
                mpn=component.mpn,
                message=f"{component.refdes} has no footprint assigned",
                location=where,
            )
        )

    if footprint is not None:
        findings.extend(_check_footprint_against_table(component, footprint, pin_table, where))
        if symbol is not None:
            findings.extend(_check_symbol_against_footprint(component, symbol, footprint, where))

    return findings


def _check_declared_pin_count(
    component: Component, package_entry: dict, pin_table: dict, where: dict
) -> list[Finding]:
    """The part record's declared pin count must match its own pin table length."""
    declared = package_entry["pin_count"]
    actual = len(pin_table["pins"])
    if declared == actual:
        return []
    return [
        Finding(
            check_id=CHECK_ID,
            code="PIN_COUNT_VS_DECLARED",
            severity="HIGH",
            component=component.refdes,
            mpn=component.mpn,
            package=component.package,
            message=(
                f"part record declares pin_count {declared} for {component.package} but its pin "
                f"table lists {actual} pins"
            ),
            expected=declared,
            actual=actual,
            remediation="Fix whichever is wrong; a transcription gap here invalidates every pin check",
            location=where,
        )
    ]


def _check_declared_bindings(component: Component, package_entry: dict, where: dict) -> list[Finding]:
    """The schematic must use the symbol and footprint the part record prescribes."""
    findings: list[Finding] = []
    declared = package_entry["kicad"]

    if component.lib_id != declared["symbol"]:
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="SYMBOL_BINDING_MISMATCH",
                severity="CRITICAL",
                component=component.refdes,
                mpn=component.mpn,
                package=component.package,
                message=(
                    f"{component.refdes} is drawn with symbol {component.lib_id} but the verified "
                    f"part record prescribes {declared['symbol']}"
                ),
                expected=declared["symbol"],
                actual=component.lib_id,
                location=where,
            )
        )

    if component.footprint and component.footprint != declared["footprint"]:
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="FOOTPRINT_BINDING_MISMATCH",
                severity="CRITICAL",
                component=component.refdes,
                mpn=component.mpn,
                package=component.package,
                message=(
                    f"{component.refdes} assigns footprint {component.footprint} but the verified "
                    f"part record prescribes {declared['footprint']} for package {component.package}"
                ),
                expected=declared["footprint"],
                actual=component.footprint,
                location=where,
            )
        )

    return findings


def _check_symbol_against_table(
    context: CheckContext, component: Component, symbol: Symbol, pin_table: dict, where: dict
) -> list[Finding]:
    findings: list[Finding] = []
    table_pins = {p["number"]: p for p in pin_table["pins"]}
    symbol_numbers = symbol.pin_numbers

    duplicates = symbol.duplicate_pin_numbers()
    if duplicates:
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="SYMBOL_DUPLICATE_PIN",
                severity="HIGH",
                component=component.refdes,
                mpn=component.mpn,
                message=(
                    f"symbol {symbol.lib_id} defines pin number(s) {', '.join(duplicates)} more "
                    f"than once; legal only for multi-unit parts sharing a supply pin"
                ),
                actual=duplicates,
                location=where,
            )
        )

    missing = [n for n in table_pins if n not in set(symbol_numbers)]
    extra = [n for n in dict.fromkeys(symbol_numbers) if n not in table_pins]

    if len(set(symbol_numbers)) != len(table_pins):
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="SYMBOL_PIN_COUNT_MISMATCH",
                severity="CRITICAL",
                component=component.refdes,
                mpn=component.mpn,
                package=component.package,
                message=(
                    f"symbol {symbol.lib_id} has {len(set(symbol_numbers))} distinct pins but the "
                    f"{component.package} pin table lists {len(table_pins)}"
                ),
                expected=len(table_pins),
                actual=len(set(symbol_numbers)),
                location=where,
            )
        )

    for number in sorted(missing, key=_pin_sort_key):
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="SYMBOL_PIN_MISSING",
                severity="CRITICAL",
                component=component.refdes,
                mpn=component.mpn,
                package=component.package,
                pin=number,
                message=(
                    f"datasheet pin {number} ({table_pins[number]['name']}) has no pin in symbol "
                    f"{symbol.lib_id}"
                ),
                expected=table_pins[number]["name"],
                location=where,
            )
        )

    for number in sorted(extra, key=_pin_sort_key):
        symbol_pin = symbol.pin(number)
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="SYMBOL_PIN_EXTRA",
                severity="CRITICAL",
                component=component.refdes,
                mpn=component.mpn,
                package=component.package,
                pin=number,
                message=(
                    f"symbol {symbol.lib_id} defines pin {number} "
                    f"({symbol_pin.name if symbol_pin else '?'}), absent from the "
                    f"{component.package} pin table"
                ),
                actual=symbol_pin.name if symbol_pin else None,
                location=where,
            )
        )

    for number, table_pin in sorted(table_pins.items(), key=lambda kv: _pin_sort_key(kv[0])):
        symbol_pin = symbol.pin(number)
        if symbol_pin is None:
            continue

        if not _names_match(table_pin["name"], table_pin.get("aliases", []), symbol_pin.name):
            findings.append(
                Finding(
                    check_id=CHECK_ID,
                    code="SYMBOL_PIN_NAME_MISMATCH",
                    severity="CRITICAL",
                    component=component.refdes,
                    mpn=component.mpn,
                    package=component.package,
                    pin=number,
                    message=(
                        f"pin {number}: datasheet calls it {table_pin['name']!r}, symbol "
                        f"{symbol.lib_id} calls it {symbol_pin.name!r}"
                    ),
                    expected=table_pin["name"],
                    actual=symbol_pin.name,
                    evidence=_pin_evidence(table_pin),
                    remediation="Fix the symbol, or add the alternative spelling to the pin table's aliases if the manufacturer uses both",
                    location=where,
                )
            )

        if not _types_compatible(table_pin["type"], symbol_pin.canonical_type):
            findings.append(
                Finding(
                    check_id=CHECK_ID,
                    code="SYMBOL_PIN_TYPE_MISMATCH",
                    severity="MEDIUM",
                    component=component.refdes,
                    mpn=component.mpn,
                    package=component.package,
                    pin=number,
                    message=(
                        f"pin {number} ({table_pin['name']}): pin table type {table_pin['type']!r} "
                        f"is not compatible with symbol electrical type "
                        f"{symbol_pin.electrical_type!r}"
                    ),
                    expected=table_pin["type"],
                    actual=symbol_pin.electrical_type,
                    remediation="Wrong pin types make ERC's driver and power checks unreliable",
                    location=where,
                )
            )

    findings.extend(_check_symbol_drift(context, component, symbol, where))
    return findings


def _check_symbol_drift(
    context: CheckContext, component: Component, symbol: Symbol, where: dict
) -> list[Finding]:
    """Compare the schematic's cached symbol against the on-disk library.

    KiCad caches symbols inside the schematic. If the library is later corrected, the
    schematic keeps the stale copy and continues to net the old way, so a fixed library can
    coexist with a still-broken board.
    """
    external = _load_external_symbol(context, component.lib_id)
    if external is None or not external.pins:
        return []

    cached = {(p.number, normalize_pin_name(p.name)[0]) for p in symbol.pins}
    on_disk = {(p.number, normalize_pin_name(p.name)[0]) for p in external.pins}
    if cached == on_disk:
        return []

    return [
        Finding(
            check_id=CHECK_ID,
            code="SYMBOL_STALE_IN_SCHEMATIC",
            severity="HIGH",
            component=component.refdes,
            mpn=component.mpn,
            message=(
                f"the copy of {component.lib_id} cached in the schematic differs from the library "
                f"on disk; the schematic nets according to the cached copy"
            ),
            expected=sorted(f"{n}:{name}" for n, name in on_disk),
            actual=sorted(f"{n}:{name}" for n, name in cached),
            remediation="Update the symbol from the library in Eeschema, then re-run",
            location=where,
        )
    ]


def _check_footprint_against_table(
    component: Component, footprint: Footprint, pin_table: dict, where: dict
) -> list[Finding]:
    findings: list[Finding] = []
    table_numbers = {p["number"]: p for p in pin_table["pins"]}
    pad_numbers = set(footprint.pad_numbers)

    for number in sorted(set(table_numbers) - pad_numbers, key=_pin_sort_key):
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="FOOTPRINT_PAD_MISSING",
                severity="CRITICAL",
                component=component.refdes,
                mpn=component.mpn,
                package=component.package,
                pin=number,
                message=(
                    f"footprint {footprint.footprint_id} has no pad {number}, required by the "
                    f"{component.package} pin table ({table_numbers[number]['name']})"
                ),
                expected=number,
                location=where,
            )
        )

    for number in sorted(pad_numbers - set(table_numbers), key=_pin_sort_key):
        findings.append(
            Finding(
                check_id=CHECK_ID,
                code="FOOTPRINT_PAD_EXTRA",
                severity="HIGH",
                component=component.refdes,
                mpn=component.mpn,
                package=component.package,
                pin=number,
                message=(
                    f"footprint {footprint.footprint_id} has pad {number}, which the "
                    f"{component.package} pin table does not define"
                ),
                actual=number,
                location=where,
            )
        )

    return findings


def _check_symbol_against_footprint(
    component: Component, symbol: Symbol, footprint: Footprint, where: dict
) -> list[Finding]:
    """Symbol pin numbers must be exactly the footprint's numbered pads.

    This is the comparison that catches a symbol whose pins are correct against the
    datasheet but assigned to a footprint with different pad numbering.
    """
    symbol_numbers = set(symbol.pin_numbers)
    pad_numbers = set(footprint.pad_numbers)
    if symbol_numbers == pad_numbers:
        return []

    return [
        Finding(
            check_id=CHECK_ID,
            code="SYMBOL_FOOTPRINT_PAD_MISMATCH",
            severity="CRITICAL",
            component=component.refdes,
            mpn=component.mpn,
            package=component.package,
            message=(
                f"symbol {symbol.lib_id} pin numbers do not match footprint "
                f"{footprint.footprint_id} pad numbers"
            ),
            expected=sorted(pad_numbers, key=_pin_sort_key),
            actual=sorted(symbol_numbers, key=_pin_sort_key),
            remediation="Every symbol pin must land on a pad of the same number",
            location=where,
        )
    ]


def _pin_evidence(table_pin: dict) -> list[dict]:
    return list(table_pin.get("evidence", []))


def _pin_sort_key(number: str) -> tuple[int, int, str]:
    """Sort pin numbers naturally: 1, 2, 10 before A1, B2."""
    if number.isdigit():
        return (0, int(number), "")
    match = re.match(r"^([A-Za-z]+)(\d+)$", number)
    if match:
        return (1, int(match.group(2)), match.group(1).upper())
    return (2, 0, number)
