"""Structured quantities with strict units.

Every physical value in this repository is written as a mapping::

    voltage:
      value: 3.3
      unit: V

Never as ``3.3V``, never as a bare number whose unit lives in the key name. Unit-in-string
is a recurring source of silent numeric errors, so schemas reject it and this module is the
only place allowed to interpret a unit.

Comparisons always go through :meth:`Quantity.si`, so ``500 mA`` and ``0.5 A`` compare
equal and a millivolt can never be accidentally summed with a volt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Physical dimensions this project understands. A quantity's dimension is derived from its
#: unit, never declared, so a unit typo becomes an error rather than a reinterpretation.
DIMENSIONS = (
    "voltage",
    "current",
    "resistance",
    "capacitance",
    "inductance",
    "power",
    "energy",
    "charge",
    "frequency",
    "time",
    "length",
    "temperature",
    "angle",
    "ratio",
)

# unit -> (dimension, multiplier to SI base, SI base unit)
_UNITS: dict[str, tuple[str, float, str]] = {}


def _register(dimension: str, base: str, prefixes: dict[str, float]) -> None:
    for suffix, mult in prefixes.items():
        _UNITS[suffix + base] = (dimension, mult, base)


_SMALL = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0}
_LARGE = {"": 1.0, "k": 1e3, "M": 1e6, "G": 1e9}
_FULL = {**_SMALL, **_LARGE}

_register("voltage", "V", _FULL)
_register("current", "A", _SMALL)
_register("resistance", "ohm", _LARGE)
_register("capacitance", "F", _SMALL)
_register("inductance", "H", _SMALL)
_register("power", "W", {**_SMALL, "k": 1e3})
_register("energy", "J", _FULL)
_register("charge", "C", _SMALL)
_register("frequency", "Hz", _LARGE)
_register("time", "s", _SMALL)
_register("length", "m", {"u": 1e-6, "m": 1e-3, "c": 1e-2, "": 1.0})

# Units that do not take SI prefixes.
_UNITS["degC"] = ("temperature", 1.0, "degC")
_UNITS["K"] = ("temperature", 1.0, "K")
_UNITS["deg"] = ("angle", 1.0, "deg")
_UNITS["ratio"] = ("ratio", 1.0, "ratio")
_UNITS["percent"] = ("ratio", 0.01, "ratio")
_UNITS["ppm"] = ("ratio", 1e-6, "ratio")

# Non-SI capacity unit that hardware people actually use; kept explicit rather than
# silently coerced, because mAh vs Ah confusion is a real battery-sizing bug.
_UNITS["mAh"] = ("charge", 3.6, "C")
_UNITS["Ah"] = ("charge", 3600.0, "C")

#: Every unit spelling the schemas accept. Exported so ``common.defs.json`` and this
#: module cannot drift apart -- ``tests/test_units.py`` asserts they match.
KNOWN_UNITS: tuple[str, ...] = tuple(sorted(_UNITS))


class UnitError(ValueError):
    """Raised for an unknown unit or a dimensionally invalid operation."""


@dataclass(frozen=True, order=False)
class Quantity:
    """A physical value with a unit, comparable only within its own dimension."""

    value: float
    unit: str

    def __post_init__(self) -> None:
        if self.unit not in _UNITS:
            raise UnitError(
                f"unknown unit {self.unit!r}; add it to pcbv.units and to "
                f"schemas/common.defs.json together"
            )
        if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
            raise UnitError(f"quantity value must be numeric, got {self.value!r}")
        if math.isnan(self.value) or math.isinf(self.value):
            raise UnitError(f"quantity value must be finite, got {self.value!r}")

    @property
    def dimension(self) -> str:
        return _UNITS[self.unit][0]

    @property
    def si(self) -> float:
        """Magnitude in the SI base unit for this dimension."""
        return self.value * _UNITS[self.unit][1]

    @property
    def si_unit(self) -> str:
        return _UNITS[self.unit][2]

    @classmethod
    def parse(cls, raw: Any, *, field: str = "quantity") -> "Quantity":
        """Build from a ``{value, unit}`` mapping, rejecting unit-in-string forms."""
        if isinstance(raw, Quantity):
            return raw
        if isinstance(raw, str):
            raise UnitError(
                f"{field}: {raw!r} is a string; write a mapping "
                f"{{value: <number>, unit: <unit>}} instead"
            )
        if not isinstance(raw, dict):
            raise UnitError(f"{field}: expected a mapping with value/unit, got {type(raw).__name__}")
        missing = {"value", "unit"} - set(raw)
        if missing:
            raise UnitError(f"{field}: missing {sorted(missing)}")
        return cls(value=float(raw["value"]), unit=str(raw["unit"]))

    def to(self, unit: str) -> "Quantity":
        """Convert within the same dimension."""
        if unit not in _UNITS:
            raise UnitError(f"unknown unit {unit!r}")
        target_dim, target_mult, _ = _UNITS[unit]
        if target_dim != self.dimension:
            raise UnitError(f"cannot convert {self.unit} ({self.dimension}) to {unit} ({target_dim})")
        return Quantity(self.si / target_mult, unit)

    def _check_same_dimension(self, other: "Quantity", op: str) -> None:
        if self.dimension != other.dimension:
            raise UnitError(
                f"cannot {op} {self.dimension} and {other.dimension} "
                f"({self.unit} vs {other.unit})"
            )

    def __add__(self, other: "Quantity") -> "Quantity":
        self._check_same_dimension(other, "add")
        return Quantity(self.si + other.si, self.si_unit).to(self.unit)

    def __sub__(self, other: "Quantity") -> "Quantity":
        self._check_same_dimension(other, "subtract")
        return Quantity(self.si - other.si, self.si_unit).to(self.unit)

    def __lt__(self, other: "Quantity") -> bool:
        self._check_same_dimension(other, "compare")
        return self.si < other.si

    def __le__(self, other: "Quantity") -> bool:
        self._check_same_dimension(other, "compare")
        return self.si <= other.si

    def __gt__(self, other: "Quantity") -> bool:
        self._check_same_dimension(other, "compare")
        return self.si > other.si

    def __ge__(self, other: "Quantity") -> bool:
        self._check_same_dimension(other, "compare")
        return self.si >= other.si

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        if self.dimension != other.dimension:
            return False
        return math.isclose(self.si, other.si, rel_tol=1e-12, abs_tol=0.0)

    def __hash__(self) -> int:
        return hash((self.dimension, round(self.si, 12)))

    def __str__(self) -> str:
        return f"{self.value:g} {self.unit}"

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "unit": self.unit}


def sum_quantities(items: list[Quantity], *, unit: str) -> Quantity:
    """Sum a homogeneous list of quantities into ``unit``.

    Used by the power budget so current totals are produced by arithmetic that the test
    suite covers, rather than by a language model adding up a column.
    """
    total = Quantity(0.0, unit)
    for index, item in enumerate(items):
        try:
            total = total + item
        except UnitError as exc:
            raise UnitError(f"item {index} ({item}) is not summable into {unit}: {exc}") from exc
    return total
