# Specification

The project charter, as set by the owner. This is the requirements document the
implementation answers to; `DECISIONS.md` records how and why each requirement was realised,
and `ROADMAP.md` tracks what is built.

Where the implementation departs from or has not yet reached a requirement, it says so.

---

## Purpose

A repeatable, production-grade PCB schematic **verification** system that treats schematic
design like an engineering CI pipeline: structured requirements, authoritative datasheets,
calculations, automated validation, independent review, and hard release gates.

Reusable across projects. KiCad is the schematic environment.

The goal is **not** to generate a schematic from a prompt.

## Priorities

Correctness, traceability, testability, repeatability, auditability.

Explicitly **not**: fewest files, fewest agents, most reports, or appearing comprehensive
without real verification.

---

## 1. Verification first, generation later

Human draws schematic → machine normalizes → verification runs → findings reviewed → human
fixes → pipeline re-runs.

No custom `.kicad_sch` emitter. KiCad files are not the primary internal data model.
Generation is reconsidered only after the verification pipeline has proven itself against
real boards and deliberately broken test cases, and only after evaluating atopile, SKiDL and
similar existing tools.

## 2. Source of truth

The human-drawn schematic is authoritative for intent. Deterministic checks operate on
normalized representations derived from it once: netlist, component list, connectivity graph,
pin mapping, power model, interface model, startup-state model. Raw s-expressions are not
re-parsed per check. The internal model is queryable and stable.

## 3. One canonical gate model

Gates are defined once, with: `gate_id`, name, description, inputs, checks,
`required_artifacts`, `owner_agent`, `basis_requirements`, `human_approval_required`,
severity, dependencies, `applicability_rules`, `output_artifacts`. The checklist, workflow
ordering, agent tasks and signoff tables are generated from these definitions. No parallel
taxonomies.

## 4. Status and basis are different

Status: `PASS`, `FAIL`, `BLOCKED`, `NOT_APPLICABLE`.

Basis: `MACHINE_CHECKED`, `HUMAN_REVIEWED`, `LLM_ASSERTED`, `DEFERRED_TO_SIMULATION`,
`DEFERRED_TO_LAYOUT`, `DEFERRED_TO_BRINGUP`, `DEFERRED_TO_LAB_TEST`.

A `PASS` alone is not sufficient. `LLM_ASSERTED` alone must never satisfy a mandatory release
requirement. A gate must state what it verified at schematic stage and what it deferred.

## 5. Human sign-off

The system must not autonomously release safety-relevant hardware on LLM review alone.
Minimum: `POWER`, `BATTERY`, `RF` (when present), `FINAL`. Optionally `USB_PD`,
`HIGH_CURRENT`, `MOTOR`, `HIGH_VOLTAGE`, `CHARGING` by project configuration. Approvals are
stored records naming a real reviewer; the reviewer identity is never fabricated.

## 6. Battery is a first-class gate

Not a checklist item. Chemistry, charge voltage and current, termination, precharge,
protection, overcurrent, reverse polarity, temperature monitoring, charging thermal
behaviour, charging while operating, power-path management, connector polarity, fault cases.
Human review mandatory for Li-ion/LiPo.

## 7. UNKNOWN is the default

Every engineering fact begins unknown until verified. A verified value carries source ID,
page, section, verbatim snippet and source hash. Critical UNKNOWN values block dependent
gates. **Silence must never be interpreted as PASS.**

## 8. Strict units

Structured units throughout (`{value: 3.3, unit: V}`), never unit-in-string. Validation
fails CI on unit mistakes. Rail naming is standardised; uncontrolled duplication
(`3V3` / `+3V3` / `3.3V` / `VDD_3V3`) is rejected unless aliases are intentionally declared.

## 9. Schemas first

Schemas exist before agents, covering: project, component requirements, datasheet evidence,
pin tables, power budget, pin matrix, interface matrix, startup-state matrix, gate
definitions, gate results, waivers, human approvals, calculations, fault review, expected
findings, final signoff. All machine-readable files validate in CI.

## 10. Datasheet evidence must be verified

Per requirement: requirement ID, MPN, package, source document, revision, PDF SHA-256, page,
section, verbatim snippet, parsed value, units, severity, evidence level.

A deterministic script verifies the snippet exists in the referenced source's extracted text.
If it cannot be verified: `FAIL`, and it does not count as evidence level A.

## 11. Extract datasheets once and cache

PDF → SHA-256 → extract text once → cache → agent extracts structured requirements →
citation verification → store verified requirements. Reuse while the hash is unchanged;
invalidate affected requirements when a revision changes. Do not repeatedly send whole PDFs
through agents.

## 12. Authoritative document sets

A component may require several authoritative documents: datasheet, hardware design
guidelines, application notes, reference designs, errata, package drawings, antenna design
guidelines, regulatory integration guides. The datasheet alone is often insufficient.

## 13. Package-qualified pin tables

Never store pin mappings by part family. Store manufacturer, MPN, package name, manufacturer
package code, pin number, pin name, pin type, function, alternate functions. A different
package variant is a separate pin table unless proven identical.

## 14. Verified symbol/footprint library as a first-class asset

A reusable versioned `library/` shared across projects, holding part records, pin tables,
symbol and footprint verification, and evidence. Not temporary project scratch. One of the
most valuable outputs of the system.

## 15. Deterministic checks before elaborate agents

First priority: symbol → datasheet → footprint pin mapping; startup/reset/boot state
analysis; worst-case power budget; KiCad ERC; normalized netlist diff.

Then: programming and debug accessibility, logic-level compatibility, I2C address conflicts,
I2C pull-up analysis, SPI chip-select uniqueness, boot/strap conflicts, duplicate MCU pin
assignments, required test-point presence, required protection presence, BOM completeness,
requirement coverage.

Claude interprets deterministic findings. Claude does not fabricate them.

## 16. Known-bad corpus — mandatory

An intentionally broken schematic test corpus, built **before** trusting the checker. Each
case carries a schematic, project configuration and `expected_findings.yaml`, asserted by an
automated test framework. Known-good examples included to detect false positives.

A verification system without a known-bad corpus must not be trusted simply because all gates
show PASS.

*(The 26 specified cases are tracked in `ROADMAP.md`; 3 exist.)*

## 17. Power budget

Per load: rail, device, mode, typical/maximum/peak current, peak duration, simultaneous-
operation conditions, evidence. Compute `TOTAL_TYPICAL`, `TOTAL_MAXIMUM`, `TOTAL_PEAK`. Per
supply: rated current, derated current, continuous margin, peak margin, thermal implications.
Never let the LLM sum currents a script can sum. Default continuous margin 25%, overridable
per project — not a universal law.

## 18. Calculation provenance

Every important calculation records formula, each input with its source requirement or
source calculation, and its result. Changed inputs mark dependents stale for re-run.

## 19. Startup-state matrix

First-class artifact, built early. Per important signal across `POWER_OFF`, `POWER_APPLIED`,
`RESET`, `BOOT`, `FIRMWARE_INIT`, `NORMAL`, `SLEEP`, `FAULT`: signal, driver, pull source,
state, device behaviour, safe/unsafe, boot conflict, back-power risk, notes, evidence.
Unsafe startup states block release. Hardware defaults to safe states before firmware runs.

## 20. Back-power analysis

Dedicated structured review. Per interface between independently powered domains: each side
powered/unpowered, reset state, GPIO clamp paths, pull-up rail ownership, external USB power,
battery-only, charging-only. Critical unresolved back-power paths block release.

## 21. Netlist diff

The hardware code-review primitive, because raw `.kicad_sch` diffs are not reviewable.
Export → normalize → compare against the previous revision, reporting added/removed nets,
changed pin connections, added/removed components, value and footprint changes. Runs in CI.

## 22. KiCad ERC

Runs automatically; results parsed into structured findings. Warnings are not automatically
acceptable — each is resolved or explicitly waived.

## 23. Waivers

Structured: `waiver_id`, `finding_id`, reason, evidence, approver, date, scope, expiry or
review condition. A waiver is not an ignored warning. Safety-related waivers require human
approval.

## 24. Fault analysis

Qualitative and structured. No LLM-generated numeric RPN — severity × occurrence ×
detectability is false precision when the values are guesses. Per failure mode: cause, local
effect, system effect, mitigation present, mitigation, detection method, residual risk,
whether human risk acceptance is required, approval, status. No mitigation and no acceptance
means `BLOCKED`.

## 25. RF, analog, thermal, SI, EMC

Distinguish schematic-stage review from actual verification. May machine-check that a
matching network exists, tuning footprints exist, a reference design requirement is recorded,
required filters or references exist, thermal pads exist, test points exist, layout
constraints are documented.

Must **not** claim antenna impedance verified, EMC passed, NFC Q verified, RF match verified,
junction temperature verified, or radiated emissions passed, without simulation or
measurement. Use deferred states.

## 26. Regulatory scope

An LLM checklist is not certification. At schematic stage, record the decision that matters:
pre-certified module versus bare RF IC. Other requirements are tracked but never marked
verified without appropriate testing.

## 27. Availability is time-decaying

Supplier availability is a timestamped snapshot, not permanent engineering evidence. A fresh
procurement check is required before fabrication.

## 28. Agents communicate only through files

Never prose handoffs between agents. Artifacts are shared memory, giving reproducibility,
auditability, caching, diffability, debugging and context isolation.

## 29. Dependency graph, not a rigid linear pipeline

Part selection, power design, pin assignment, physical feasibility and interface selection
affect each other. Gate dependencies are explicit. The runner inspects dependencies,
topologically orders runnable gates, parallelizes independent work where safe, and
invalidates downstream gates when upstream artifacts change.

## 30. Derived release status

Clean checkout → validate schemas → deterministic checks → tests → load approved human
signoffs → evaluate waivers → evaluate mandatory gates → generate signoff. The marker is
build output. Claude does not create `RELEASE_READY` as a claim.

## 31. CI is the authority

Protected branches, required checks, test suite, clean checkout, reproducible environment.
Claude may modify source files. Claude does not get to redefine whether the result passes.

## 32. Project state machine

`NEW` → `REQUIREMENTS_READY` → `VERIFICATION_MODEL_READY` → `SCHEMATIC_UNDER_REVIEW` →
`SCHEMATIC_VERIFIED` → `SCHEMATIC_RELEASED`. Layout is a separate future pipeline; nothing
here implies a board is ready to build.

## 33. Minimum evidence by severity

Enforced mechanically. `CRITICAL`: A/B/C, LLM inference forbidden. `HIGH`: A/B/C or a
human-reviewed calculation. `MEDIUM`: lower evidence with an explicit flag. `LOW`: advisory
inference acceptable. Evidence level F must never silently satisfy a critical electrical
requirement.

Evidence levels: **A** manufacturer datasheet, **B** application note, **C** reference
design, **D** official distributor, **E** reputable third party, **F** inference.

## 34. Final signoff format

Generated automatically, grouped by evidence basis: machine-checked results, human-reviewed
approvals, LLM advisory findings, deferred-to-layout, deferred-to-bringup,
deferred-to-lab-test, waivers, unknown critical items. Final result is `SCHEMATIC_RELEASED`
or `NOT_RELEASED`.

## 35. Initial implementation priority

The first useful system does not need the full engineering checklist automated. Focus on what
prevents a dead Rev-A board: symbol/footprint/pin mapping, startup states, boot and strap
pins, power budget, regulator sizing, decoupling, programming and debug access, logic-level
compatibility, back-power paths, ERC, netlist diff, critical test points. Then expand.
