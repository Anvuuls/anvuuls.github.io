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
| Everything else in `gates.yaml` (24 gates) | **not implemented** |

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

- **Connectivity is not read yet.** `CHK-PIN-MAP` compares pin *identity*, not what is
  wired to what. Netlist extraction is Phase 1, and several corpus cases record this gap in
  their `not_yet_detected` sections.
- **The corpus fixtures were hand-written without KiCad available.** The `kicad-fixtures` CI
  job opens them with `kicad-cli` to prove they are real KiCad files rather than a private
  dialect our own parser happens to accept. Until that job has run green, treat fixture
  validity as unconfirmed.
- **`kicad-cli` is not required locally.** Checks needing it are Phase 1 and will be marked
  `requires_kicad_cli`, skipping cleanly rather than reporting a false pass.
- **The example parts are fictional.** `EXAMPLE-LDO-3V3` and `EXAMPLE-CONN-2P` do not exist.
  Their stand-in datasheets are plain text with real SHA-256 hashes so citations are
  genuinely greppable; no number in them belongs in a real design.
