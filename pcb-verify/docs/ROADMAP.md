# Roadmap

Where this is and what comes next. Phase 0 is complete; everything below it is not started.

The ordering rule throughout: **build the check and its known-bad corpus case in the same
commit.** A check without a case is untested software, and `gates.yaml` keeps its gate
`BLOCKED` until the check is registered anyway.

---

## Phase 0 — Foundation — **DONE**

Repo structure, 16 JSON schemas, strict units, canonical gate model with dependency graph and
derived applicability, derived release status, KiCad readers, normalized model,
`CHK-PIN-MAP`, `CHK-PROJECT-SCHEMA`, corpus of 1 known-good + 3 known-bad, 152 tests, CI, and
KiCad-as-oracle verification against 7.0.11 and 10.0.5.

---

## Phase 1 — Core deterministic verification — **NEXT**

The checks most likely to prevent a dead Rev-A board. Build in this order; each depends on
what precedes it.

### 1.1 Connectivity in the normalized model — *do this first*

Nothing else in Phase 1 is possible without it. `pcbv.kicad.cli.netlist_nets` already
extracts nets from a KiCad netlist; the work is to fold connectivity into `pcbv.model` so
checks can ask "what is on net X" and "what does pin U1.3 connect to" without touching KiCad.

Decide deliberately: derive connectivity by running `kicad-cli sch export netlist` (correct,
but makes KiCad a hard dependency of most checks), or compute it from wire geometry in the
schematic (no dependency, but reimplements KiCad's connectivity rules — including the two
traps in the README, and it *will* be subtly wrong). **Recommendation: use kicad-cli**, mark
those checks `requires_kicad_cli`, and let them report unavailable rather than guess.

### 1.2 `CHK-ERC` — KiCad ERC as a gate

Plumbing exists (`cli.run_erc`). Needs: findings mapped from ERC violations, a waiver path so
warnings are resolved or explicitly waived rather than bulk-suppressed, and a corpus case.
Requires KiCad 8+; on older KiCad the gate stays `BLOCKED` rather than passing.

Note from Phase 0: ERC found **zero errors** on all three known-bad designs. It is a
low-value gate on its own — keep it, but do not let a green ERC imply anything.

### 1.3 `CHK-STARTUP-STATE` — the highest-value artifact in the whole design

Most first-bring-up failures are floating enables, straps fought by peripherals, and
back-powering. `startup_states.schema.json` is written and models what is needed:
per-phase state, what holds each net, criticality, and receiver rails.

The check compares actual `states` against `required_state` per phase and fails when a signal
with `criticality: enables_current` is `FLOATING` or `UNKNOWN` before firmware runs.

### 1.4 `CHK-BACKPOWER`

For each interface crossing a power-domain boundary, evaluate with each side unpowered.
Driving a signal into an unpowered device forward-biases its ESD clamp and sneak-powers the
part. `receiver_devices[].tolerant_when_unpowered` defaults false and requires a citation to
set true.

### 1.5 `CHK-POWER-BUDGET` and `CHK-REGULATOR-MARGIN`

`power_budget.schema.json` is written. Sum worst-case per rail honouring
`simultaneity_groups`, compare against derated supply capability, and **block on any load
whose current is `UNKNOWN`** rather than treating it as zero. Never let an LLM sum the column.

### 1.6 `CHK-NETLIST-DIFF`

Export, normalize, diff against the previous revision. Raw `.kicad_sch` diffs are not
reviewable; a normalized netlist diff is the correct unit of hardware code review. Run it in
CI on pull requests.

### 1.7 The rest of Phase 1

`CHK-BOOT-STRAP`, `CHK-PIN-CONFLICT`, `CHK-UNUSED-PINS`, `CHK-PROGRAMMING-ACCESS`,
`CHK-LOGIC-LEVELS`, `CHK-I2C-ADDRESS`, `CHK-I2C-PULLUP`, `CHK-SPI-CS`, `CHK-TEST-POINTS`.
Schemas for the pin and interface matrices already exist.

---

## Phase 2 — Datasheet ingestion

Start with **three** real parts, not thirty: one MCU or module, one regulator, one peripheral.

1. Download and store sources; record SHA-256 and revision.
2. Extract text **once** and cache it. Never send whole PDFs through an agent repeatedly.
3. Datasheet agent extracts structured requirements with verbatim snippets.
4. `scripts/verify_citations` re-greps every snippet against the cached text — **the piece
   that makes the evidence model real rather than an honour system.** Unverifiable snippet →
   requirement fails.
5. Package-qualified pin tables extracted per package.
6. Invalidate on hash change.

Do not scale to more parts until the citation verifier is catching real mistakes.

---

## Phase 3 — Gate engine hardening

Staleness propagation when an upstream artifact changes, waiver expiry, approval staleness
against `netlist_digest`, and generated signoff reports grouped by evidence basis.

---

## Phase 4 — Review agents

Only after deterministic checks exist. Start with four, not twelve: `DATASHEET`,
`POWER_REVIEW`, `DIGITAL_REVIEW`, `RED_TEAM_REVIEW`. Each reads structured artifacts, writes
structured findings, cites evidence, and cannot satisfy a machine-checkable gate by assertion.

---

## Phase 5 — Advisory engineering review

RF, analog, thermal, signal integrity, EMC, protection, mechanical. Strictly separated into
verified now / requires layout / requires simulation / requires lab measurement. See D18.

---

## Phase 6 — Generation (only if justified)

Gated on the verification suite proving itself against real boards. Evaluate atopile and
SKiDL before writing anything custom. See D1 for the six questions to answer first.

---

## Known-bad corpus: 3 of 26 built

The corpus is the measure of what this system actually catches. Each case injects **exactly
one** defect and carries an `expected_findings.yaml` contract.

| # | Case | Status | Needs |
|---|---|---|---|
| 1 | Symbol pin 4 mapped to footprint pad 5 | **done** (`wrong_symbol_pin`) | — |
| 2 | Wrong package variant pin table | **done** (`wrong_package_pin_table`) | — |
| 3 | Connector pin order reversed | **done** (`reversed_connector_pinout`) | — |
| 4 | Floating MCU boot strap | to do | 1.3 |
| 5 | Peripheral pulling a boot pin to the wrong state | to do | 1.3 |
| 6 | I2C pull-ups tied to the wrong voltage | to do | 1.7 |
| 7 | Duplicate I2C address | to do | 1.7 |
| 8 | Excessively strong parallel I2C pull-ups | to do | 1.7 |
| 9 | Duplicate SPI chip select | to do | 1.7 |
| 10 | SPI CS floating during reset | to do | 1.3 |
| 11 | Regulator insufficient for maximum load | to do | 1.5 |
| 12 | Regulator insufficient for peak load | to do | 1.5 |
| 13 | MLCC nominal correct, effective capacitance below spec under DC bias | to do | Phase 2 |
| 14 | Back-powering an unpowered peripheral through a GPIO | to do | 1.4 |
| 15 | Missing decoupling capacitor | to do | 1.1 |
| 16 | Wrong capacitor voltage rating | to do | Phase 2 |
| 17 | Missing reset pull | to do | 1.3 |
| 18 | Programming pin conflict | to do | 1.7 |
| 19 | Required programming interface absent | to do | 1.7 |
| 20 | Missing USB ESD protection where the project requires it | to do | Phase 5 |
| 21 | Motor enable floating at boot | to do | 1.3 |
| 22 | Insufficient flyback protection for an inductive load | to do | Phase 5 |
| 23 | Missing test point on a critical rail | to do | 1.7 |
| 24 | Datasheet-to-symbol pin mismatch | **covered by #1** | — |
| 25 | Unverified evidence citation | to do | Phase 2 |
| 26 | Requirement marked verified below the minimum evidence level | to do | Phase 2 |

Cases 4, 5, 10, 17 and 21 all exercise `CHK-STARTUP-STATE` and would come as a batch with 1.3.
That group is the single highest-value block of work remaining.

Add known-good variants alongside the harder cases to control false positives — a checker
that cries wolf on correct designs gets ignored, which is worse than not having it.

## Tracking what is *not* caught

Corpus cases record undetected defects in `not_yet_detected` rather than omitting them, and a
test fails if a recorded gap silently starts passing. Keep doing this: the honest measure of
this system is not how many checks it has but how much it is known to miss.
