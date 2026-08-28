# pcb-verify

Verification for **human-drawn KiCad schematics**. A human draws the circuit; the machine
checks it against manufacturer pin tables and computes whether it may be released.

This is Phase 0 of a phased build. It does not generate schematics and should not be asked
to — see [Phase discipline](#phase-discipline).

```
human draws in KiCad  ->  normalize  ->  deterministic checks  ->  findings
                                                                     |
             human fixes  <-  agent/human interprets  <--------------+
```

## Where things are written down

| Document | What it holds |
|---|---|
| [`docs/SPECIFICATION.md`](docs/SPECIFICATION.md) | The charter — what this system must do, set by the project owner |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why it looks like this: alternatives rejected, and what would justify revisiting each |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | What is built, what is next, and the 26 known-bad cases (3 exist) |
| [`CLAUDE.md`](CLAUDE.md) | The engineering rules — read before changing anything |
| [`LOCAL_SETUP.md`](LOCAL_SETUP.md) | Running it on your own machine |

New here? Read `SPECIFICATION.md` for intent, then `DECISIONS.md` for why the implementation
diverges from the obvious approach in several places.

## Quick start

```bash
cd pcb-verify
python -m pip install -e ".[dev]"

pcbv validate                                  # every schema and artifact
pcbv corpus                                    # known-good + known-bad cases
pcbv gates                                     # the canonical gate model
pcbv check tests/corpus/known_bad/wrong_symbol_pin -v
pcbv release tests/corpus/known_good/minimal_ldo
python -m pytest -q
```

`pcbv release` currently prints `NOT_RELEASED`, and that is correct: most gates have no
implementing check yet, and an unimplemented gate evaluates to `BLOCKED` rather than `PASS`.

## What actually works today

| Capability | Status |
|---|---|
| JSON Schemas for all 16 artifact types, validated in CI | working |
| Strict units (`{value, unit}`), SI normalization, dimension guards | working |
| S-expression reader for `.kicad_sch` / `.kicad_sym` / `.kicad_mod` | working |
| Normalized design model; hierarchical sheets; multi-unit merge | working |
| Canonical gate model: 26 gates, dependency graph, topological order | working |
| Derived `NOT_APPLICABLE` from `project.yaml` | working |
| Derived release verdict; `LLM_ASSERTED` alone can never pass a gate | working |
| **CHK-PIN-MAP**: datasheet pin table ↔ symbol ↔ footprint pads | working |
| CHK-PROJECT-SCHEMA: requirements validity, rail-name drift | working |
| Corpus: 1 known-good + 3 known-bad, contract-matched | working |
| Fixtures verified loadable by real KiCad (7.0.11 and **10.0.5**) | working |
| Readers cross-checked against KiCad's own netlist as oracle | working |
| KiCad ERC runs and parses; fixtures are ERC-error-free | working |
| Everything else in `gates.yaml` (24 gates) | **not implemented** |

152 tests, of which 30 use KiCad as an oracle.

### What ERC proved

Running KiCad 10's ERC over the corpus produced **zero errors on all three deliberately
broken designs**. A regulator output on an unbonded pad, a package with transposed pin
numbering, a power connector wired backwards — ERC sees none of them, because it only knows
pin electrical types and connectivity, never what the datasheet says. That result is
asserted as a test (`test_erc_is_blind_to_every_defect_in_the_corpus`), and it is the
empirical case for this project existing.

The 24 unimplemented gates are declared with `implemented: false`, so they report `BLOCKED`.
Nothing here reports a vacuous pass.

## Layout

```
pcb-verify/
├── CLAUDE.md              engineering constitution -- read first
├── gates/gates.yaml       THE canonical process definition
├── schemas/               16 artifact schemas + shared defs
├── src/pcbv/
│   ├── sexpr.py           read-only KiCad s-expression reader
│   ├── units.py           structured quantities
│   ├── schema.py          schema registry and validation
│   ├── model.py           the normalized design model
│   ├── gatemodel.py       dependency graph, applicability, release verdict
│   ├── library.py         verified part library loading
│   ├── corpus.py          corpus discovery and contract matching
│   ├── findings.py        findings (only construction path)
│   ├── kicad/             format readers (the only place raw KiCad is touched)
│   └── checks/            deterministic checks
└── tests/corpus/
    ├── _shared/           example library + stand-in datasheets
    ├── known_good/        false-positive control
    └── known_bad/         one injected defect per case
```

## The rules that matter

**One canonical process.** `gates/gates.yaml` is the single source of truth. The checklist,
ordering and signoff tables are generated from it (`pcbv gates --checklist`). Four
overlapping taxonomies is how a process drifts out of sync with itself.

**Status and basis are different.** Every gate carries both. `PASS` alone means nothing;
`PASS / MACHINE_CHECKED` is strong, `PASS / LLM_ASSERTED` is an opinion, and
`LLM_ASSERTED` on its own can never satisfy a gate.

**Nothing is verified by silence.** Every fact starts `UNKNOWN`. A check that cannot cover
something emits an `INFO` finding recording the gap rather than passing over it.

**Citations must be re-checkable.** Every requirement carries a verbatim snippet plus the
source's SHA-256. A script re-greps the snippet; an unverifiable citation fails and counts
as no evidence at any level. Minimum evidence per severity is enforced by schema —
a `CRITICAL` requirement resting on inference is rejected, not flagged.

**Pin tables are package-qualified.** Never keyed by part family. The
`wrong_package_pin_table` corpus case exists to prove this: both packages are real variants
of one MPN whose pin numbering differs, and a family-level lookup would report a clean pass
on a design that grounds its own supply pin.

**Humans sign safety gates.** `POWER`, `BATTERY`, `RF`, `FINAL_REVIEW` require a stored
approval naming a reviewer from the project roster. Claude must never write an approval
record or invent a reviewer.

**Release is computed, not claimed.** There is no input to `evaluate_all` by which a caller
can assert release. CI recomputes it on a clean checkout, and CI is the authority.

**No check without a corpus case.** `test_every_implemented_check_has_a_known_bad_case`
enforces it. A verification suite with no corpus is untested software, and "all gates PASS"
from untested software carries no information.

## Phase discipline

| Phase | Scope | State |
|---|---|---|
| 0 | Foundation: schemas, gates, corpus, first checker | **done** |
| 1 | Core deterministic checks: startup states, power budget, ERC, netlist diff, boot straps, back-power, programming access, logic levels | next |
| 2 | Datasheet ingestion with hash-pinned sources and snippet verification | later |
| 3 | Gate engine hardening: staleness, waiver expiry, signoff generation | later |
| 4 | Review agents, including a red-team reviewer | later |
| 5 | Advisory review: RF, analog, thermal, SI, EMC, protection, mechanical | later |
| 6 | **Maybe** generation — evaluate `atopile` / `SKiDL` first, never a custom emitter | gated |

Phase 6 is gated on the verification suite proving itself against real boards and the
known-bad corpus. Do not build a `.kicad_sch` writer.

## Known limitations

- **Connectivity is not used by any check yet.** `CHK-PIN-MAP` compares pin *identity*, not
  what is wired to what. `pcbv.kicad.cli.netlist_nets` extracts connectivity and the
  known-good fixture's netlist is asserted, but no check consumes it — that is Phase 1.
  Several corpus cases record the gap in their `not_yet_detected` sections.
- **ERC needs KiCad 8 or newer.** `kicad-cli sch erc` does not exist in KiCad 7, which is
  what Ubuntu 24.04 ships. Availability is established by probing `sch erc --help`, not by
  inferring it from a version string, and `run_erc` raises rather than returning an empty
  result when it is unavailable — "no violations" and "never ran" must never be the same
  value. `CHK-ERC` itself is still unimplemented (the plumbing exists; the check and its
  corpus case are Phase 1), so the `ERC` gate remains `BLOCKED`.
- **`kicad-cli` is optional locally.** Without it the 22 oracle tests skip with an explicit
  "fixture validity is UNVERIFIED" message, and CI fails the job if they skip there.
- **The example parts are fictional.** `EXAMPLE-LDO-3V3` and `EXAMPLE-CONN-2P` do not exist.
  Their stand-in datasheets are plain text with real SHA-256 hashes so citations are
  genuinely greppable; no number in them belongs in a real design.

## Toolchain provenance

Verified against **KiCad 7.0.11** (Ubuntu 24.04 archive) and **KiCad 10.0.5** (built from
source, GitLab tag `10.0.5`, commit `18fb9289`). Fixtures load and produce identical
netlists under both.

KiCad 10 could not be installed from any packaged source in the development container:
`kicad.org`, `downloads.kicad.org`, `ppa.launchpadcontent.net`, Flathub, Snapcraft and
Anaconda are all denied by the environment's egress policy, and no Ubuntu suite carries
KiCad 10 (26.10 devel still ships 9.0.8). GitLab is permitted, so it was built from source.
On a machine that can reach the PPA, `ppa:kicad/kicad-10.0-releases` is a one-line install
and this is unnecessary.

Building 10.0.5 on Ubuntu 24.04 needs one dependency cmake does not catch:
`libpoppler-glib-dev`. Configuration succeeds with only poppler core and cpp present, then
compilation fails ~190 files in on a missing `poppler/glib/poppler.h`. OCC and ngspice are
unconditionally required in KiCad 10 — there is no option to build without them.

## Authoring corpus fixtures

Two traps, both of which produced a fixture that loaded fine and tested nothing:

1. **KiCad derives connectivity from wire *endpoints*.** A pin lying part-way along a wire
   segment does **not** connect, even with a junction dot drawn on it. Split every wire at
   every connection point. The symptom is a fixture that loads cleanly and whose netlist
   quietly shows each pin on its own `unconnected-(...)` net.
2. **A `no_connect`-type pin never joins a net at all.** Wiring one up looks right in the
   file and is silently dropped.
3. **Nets carrying a `power_in` pin need a `power_out` driver or ERC errors.** The corpus
   header's contacts are `passive`, so VIN and GND get `PWR_FLAG` symbols — which is honest
   here, since VIN genuinely arrives from off-board. Their `#FLG` references are skipped by
   the schematic reader, so they never reach the checks.

Parse netlists with `pcbv.sexpr`, never with regexes over the text. KiCad 7 emitted
`(comp (ref "U1")` on one line; KiCad 10 pretty-prints every element on its own. Any
line-shape assumption silently stops matching on a toolchain upgrade, and a parser that
returns nothing looks exactly like a design with no components.

Symbol-local pin coordinates map to schematic coordinates as `(px + lx, py - ly)` for an
unrotated instance. Do not trust that from memory — probe it: drop uniquely-named global
labels at candidate coordinates, export the netlist, and read which pin joined which label.

Prefer letting KiCad canonicalize what you write: `kicad-cli sym upgrade` and
`kicad-cli fp upgrade` rewrite libraries into KiCad's own formatting, so the committed
fixture is the reference implementation's output rather than our guess at its syntax. There
is no equivalent for schematics, so those stay hand-written and are covered by the oracle
tests instead.
