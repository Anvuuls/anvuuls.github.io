# PCB Schematic Verification — Engineering Constitution

This directory verifies **human-drawn KiCad schematics**. It does not generate them.

Read this file before touching anything in `pcb-verify/`.

## Phase discipline

We are in the **verification-first** phase.

- A human draws the schematic in KiCad. KiCad is the design tool.
- The machine exports and normalizes design data, runs deterministic checks, and reports findings.
- Claude interprets findings. Claude does not produce findings.
- A human edits the schematic. The pipeline re-runs.

**Do not build a `.kicad_sch` emitter.** Do not add automatic schematic generation.
Generation is Phase 6 and is gated on the verification suite proving itself against
real boards and the known-bad corpus. If generation is ever revisited, evaluate
`atopile` and `SKiDL` before writing anything custom.

## Source of truth

| Thing | Authority |
|---|---|
| Intended circuit | the human-drawn `.kicad_sch` |
| Verification input | the **normalized model** derived from it (`pcbv.model`) |
| Component facts | `library/parts/<MPN>/` records, each backed by verified evidence |
| Release status | computed by CI from artifacts on a clean checkout |

Raw s-expressions are parsed **once**, at the edge, by `pcbv.kicad.*`. Checks consume
the normalized model only. A check that reaches for a `.kicad_sch` file directly is a
bug — add a field to the model instead.

## Never invent a value

Every engineering fact starts `UNKNOWN`. Silence is never a pass.

```yaml
max_current:
  value: null
  unit: A
  status: UNKNOWN
  evidence: []
```

A fact becomes `VERIFIED` only with evidence carrying a resolvable citation:
source document, revision, **SHA-256 of the exact PDF**, page, section, and a
**verbatim snippet**. A deterministic script re-greps that snippet against the cached
extracted text. If the snippet is not found, the requirement is `FAIL` and does not
count as evidence at any level.

An LLM-generated page number with no verifiable snippet is not evidence. It is a guess
wearing a citation.

## Evidence levels

```
A  manufacturer datasheet
B  manufacturer application note / hardware design guideline
C  manufacturer reference design
D  official distributor data
E  reputable third-party engineering source
F  inference
```

Minimum evidence is enforced mechanically by severity (see `schemas/requirement.schema.json`):

| Severity | Minimum | Notes |
|---|---|---|
| CRITICAL | A, B, or C | inference forbidden |
| HIGH | A, B, C, or a human-reviewed calculation | |
| MEDIUM | D or better, or E with explicit flag | |
| LOW | advisory inference permitted | |

Level F must never silently satisfy a critical electrical requirement.

Datasheets are not the only authority. A part may need its hardware design guideline,
application notes, reference design, **errata**, and package drawings. The part record
supports multiple sources; critical requirements may live outside the datasheet.

## Pin tables are package-qualified

Never store a pin mapping against a part family. Two packages of the "same" part can
number pins differently, and that silently poisons every downstream pin check.
A different package is a different pin table until proven identical.

## Status and basis are different things

Every gate carries both.

Status: `PASS` `FAIL` `BLOCKED` `NOT_APPLICABLE`

Basis: `MACHINE_CHECKED` `HUMAN_REVIEWED` `LLM_ASSERTED` `DEFERRED_TO_SIMULATION`
`DEFERRED_TO_LAYOUT` `DEFERRED_TO_BRINGUP` `DEFERRED_TO_LAB_TEST`

`PASS` alone means nothing. `PASS / MACHINE_CHECKED` is strong. `PASS / LLM_ASSERTED`
is an opinion, and **`LLM_ASSERTED` alone can never satisfy a mandatory gate** —
`pcbv.gatemodel` enforces this against each gate's `basis_requirements`.

Never claim a measurement that has not happened. At schematic stage you may verify that
a matching network *exists*, that tuning footprints *exist*, that a layout constraint is
*documented*. You may not report antenna impedance, S11, junction temperature, EMC
margin, or NFC Q. Those are `DEFERRED_*`.

`NOT_APPLICABLE` is **derived** from `project.yaml`, not asserted. No RF subsystem in the
requirements means the RF gate is automatically N/A. An LLM does not get to decide a gate
does not apply.

## Human sign-off

The system does not release safety-relevant hardware on LLM review alone.

Mandatory human approval: `POWER`, `BATTERY` (whenever a rechargeable cell is present),
`RF` (when present), `FINAL`. Conditionally: `USB_PD`, `HIGH_CURRENT`, `MOTOR`,
`HIGH_VOLTAGE`, `CHARGING` per project configuration.

Approvals are stored records with a named reviewer. **Claude must never write an approval
record, invent a reviewer name, or fill in an approver field.** A human commits their own
approval. CI checks that approvals are signed by a name on the project's reviewer roster.

## Waivers

A waiver is not an ignored warning. Every waived finding needs a waiver record with
`reason`, `evidence`, `approver`, `date`, `scope`, and a review condition. Safety-related
waivers require human approval. Nothing is silently suppressed — including ERC warnings.

## Calculations

Any engineering calculation records its formula, every input with the requirement or
calculation it came from, and its result. If an input changes, dependents go stale and
re-run. Never let an LLM sum a column of currents that a script can sum.

## Agents communicate only through files

Never "the power agent said the regulator is fine." An agent reads structured artifacts
and writes structured artifacts. Files are the shared memory — that is what makes this
reproducible, cacheable, diffable, and auditable.

## Release status is derived, never claimed

Claude may edit source files. Claude does not get to decide whether the result passes.
`SCHEMATIC_RELEASED` is computed by CI on a clean checkout from schemas + tests +
deterministic checks + stored human approvals + waivers. The signoff report is build
output, never a hand-written file.

Terminal state is `SCHEMATIC_RELEASED` or `NOT_RELEASED`. Layout and fabrication are a
separate future pipeline; nothing here implies a board is ready to build.

## The corpus is what makes any of this trustworthy

`tests/corpus/known_bad/` holds deliberately broken designs, each with an
`expected_findings.yaml`. A check is not considered to work until a known-bad case proves
it fires and the known-good case proves it does not false-positive.

**Adding a check without a known-bad case is not allowed.** A verification suite with no
corpus is untested software, and "all gates PASS" from untested software carries no
information.

## KiCad is the oracle, not our parser

`tests/test_kicad_cli.py` checks our readers against `kicad-cli` itself: same components,
same pins, same electrical types. Without it, a self-consistently wrong reader would make
every downstream check compare against something the board does not use.

When adding a fixture, verify it loads (`cli.export_netlist` raises if not) and assert its
netlist. Note that `kicad-cli` 7 exits 0 even after printing "Failed to load schematic
file", so exit status alone is not a usable signal — that is why the wrapper inspects the
output text and the produced file.

Version-dependent capabilities must be reported, never assumed: `sch erc` does not exist
before KiCad 8.0. A check whose tool is missing reports unavailable and leaves its gate
`BLOCKED`. It must never report a pass.
