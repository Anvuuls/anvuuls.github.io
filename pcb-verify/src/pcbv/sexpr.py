"""Minimal reader for the s-expression syntax KiCad uses in its file formats.

This is deliberately read-only. There is no writer, and there must not be one -- see
``CLAUDE.md``: KiCad files are inputs drawn by a human, never machine output.

The parser is intentionally dumb about semantics. It produces nested :class:`Node` values
and knows nothing about symbols, pads, or nets; that interpretation lives in
``pcbv.kicad.*`` so that format knowledge stays in one layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence


class SexprError(ValueError):
    """Raised on malformed s-expression input, with a byte offset when known."""


Atom = str | float | int


@dataclass
class Node:
    """A parenthesised list: a head token plus atom and child-node values."""

    head: str
    values: list[Atom | "Node"] = field(default_factory=list)

    # -- child access ---------------------------------------------------------------

    @property
    def children(self) -> list["Node"]:
        return [v for v in self.values if isinstance(v, Node)]

    @property
    def atoms(self) -> list[Atom]:
        return [v for v in self.values if not isinstance(v, Node)]

    def find_all(self, head: str) -> list["Node"]:
        """Direct children with the given head."""
        return [c for c in self.children if c.head == head]

    def find(self, head: str) -> "Node | None":
        """First direct child with the given head, or ``None``."""
        for child in self.children:
            if child.head == head:
                return child
        return None

    def descendants(self, head: str) -> Iterator["Node"]:
        """Every node with the given head at any depth below this one."""
        for child in self.children:
            if child.head == head:
                yield child
            yield from child.descendants(head)

    # -- value access ---------------------------------------------------------------

    def atom(self, index: int = 0) -> Atom:
        """Positional atom, e.g. ``"1"`` from ``(number "1")``."""
        atoms = self.atoms
        if index >= len(atoms):
            raise SexprError(f"({self.head} ...) has no atom at index {index}")
        return atoms[index]

    def str_atom(self, index: int = 0) -> str:
        return str(self.atom(index))

    def opt_str_atom(self, index: int = 0) -> str | None:
        atoms = self.atoms
        return str(atoms[index]) if index < len(atoms) else None

    def child_str(self, head: str, index: int = 0) -> str | None:
        """First atom of the first child with ``head``, or ``None`` if absent."""
        node = self.find(head)
        return None if node is None else node.opt_str_atom(index)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Node({self.head!r}, {len(self.values)} values)"


_WHITESPACE = frozenset(" \t\r\n")
_DELIMITERS = frozenset('()"') | _WHITESPACE


def _parse_number(token: str) -> Atom:
    """Return an int or float for numeric tokens, otherwise the token unchanged.

    Bare tokens in KiCad files are a mix of identifiers (``yes``, ``input``) and numbers
    (``1``, ``-2.54``). Numbers become numeric so geometry can be used directly; anything
    else stays a string.
    """
    try:
        if any(c in token for c in ".eE") and token.lower() not in {"e", "ee"}:
            return float(token)
        return int(token)
    except ValueError:
        try:
            return float(token)
        except ValueError:
            return token


def loads(text: str) -> Node:
    """Parse a document containing exactly one top-level s-expression."""
    nodes, pos = _parse_forms(text, 0, top_level=True)
    if not nodes:
        raise SexprError("empty document: expected one top-level s-expression")
    if len(nodes) > 1:
        raise SexprError(f"expected one top-level s-expression, found {len(nodes)}")
    node = nodes[0]
    if not isinstance(node, Node):
        raise SexprError("top level must be a parenthesised list")
    trailing = text[pos:].strip()
    if trailing:
        raise SexprError(f"trailing content after top-level expression at offset {pos}")
    return node


def load(path: str | Path) -> Node:
    """Parse a KiCad file from disk."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SexprError(f"{p}: not valid UTF-8: {exc}") from exc
    try:
        return loads(text)
    except SexprError as exc:
        raise SexprError(f"{p}: {exc}") from exc


def _parse_forms(text: str, pos: int, *, top_level: bool = False) -> tuple[list[Atom | Node], int]:
    """Parse forms until a closing paren (or end of text at top level)."""
    out: list[Atom | Node] = []
    length = len(text)
    while pos < length:
        char = text[pos]

        if char in _WHITESPACE:
            pos += 1
            continue

        if char == ";":  # line comment
            newline = text.find("\n", pos)
            pos = length if newline == -1 else newline + 1
            continue

        if char == "(":
            node, pos = _parse_node(text, pos)
            out.append(node)
            continue

        if char == ")":
            if top_level:
                raise SexprError(f"unbalanced ')' at offset {pos}")
            return out, pos

        if char == '"':
            value, pos = _parse_quoted(text, pos)
            out.append(value)
            continue

        start = pos
        while pos < length and text[pos] not in _DELIMITERS:
            pos += 1
        if pos == start:  # pragma: no cover - defensive
            raise SexprError(f"cannot make progress at offset {pos}")
        out.append(_parse_number(text[start:pos]))

    if not top_level:
        raise SexprError("unexpected end of input: missing ')'")
    return out, pos


def _parse_node(text: str, pos: int) -> tuple[Node, int]:
    assert text[pos] == "("
    open_at = pos
    pos += 1
    length = len(text)

    while pos < length and text[pos] in _WHITESPACE:
        pos += 1
    if pos >= length:
        raise SexprError(f"unterminated list opened at offset {open_at}")

    # The head token. KiCad heads are bare tokens, but tolerate a quoted head.
    if text[pos] == '"':
        head, pos = _parse_quoted(text, pos)
    elif text[pos] in "()":
        raise SexprError(f"list opened at offset {open_at} has no head token")
    else:
        start = pos
        while pos < length and text[pos] not in _DELIMITERS:
            pos += 1
        head = text[start:pos]

    values, pos = _parse_forms(text, pos)
    if pos >= length or text[pos] != ")":
        raise SexprError(f"unterminated list ({head} ...) opened at offset {open_at}")
    return Node(head=str(head), values=values), pos + 1


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _parse_quoted(text: str, pos: int) -> tuple[str, int]:
    assert text[pos] == '"'
    open_at = pos
    pos += 1
    chunks: list[str] = []
    length = len(text)
    while pos < length:
        char = text[pos]
        if char == "\\":
            if pos + 1 >= length:
                raise SexprError(f"string opened at offset {open_at} ends with a backslash")
            nxt = text[pos + 1]
            chunks.append(_ESCAPES.get(nxt, nxt))
            pos += 2
            continue
        if char == '"':
            return "".join(chunks), pos + 1
        chunks.append(char)
        pos += 1
    raise SexprError(f"unterminated string opened at offset {open_at}")


def require(node: Node | None, head: str, *, context: str) -> Node:
    """Assert a node exists, for readable errors when a file lacks a required section."""
    if node is None:
        raise SexprError(f"{context}: missing required ({head} ...) section")
    return node


def first_atom_of(nodes: Sequence[Node]) -> list[str]:
    """First atom of each node as a string; handy for pin-number style lists."""
    return [n.str_atom(0) for n in nodes]
