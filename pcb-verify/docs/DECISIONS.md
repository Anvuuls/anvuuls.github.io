# Decision record

Why this project looks the way it does. Each entry names the alternatives that were
rejected and **what would justify revisiting** — that last field matters most, because it is
what stops a reasonable decision from hardening into dogma after its context has changed.

Decisions marked **[owner]** were made by the project owner. Decisions marked **[review]**
came out of a critique of the original plan and were then adopted.

---

## D1. Verify human-drawn schematics; do not generate them **[owner]**

**Decision.** A human draws the schematic in KiCad. The machine normalizes, checks, and
reports. There is no `.kicad_sch` writer and there must not be one.

**Rejected.** Generating schematics from structured requirements, which was the original
goal of the plan this project came from.

**Why.** Three reasons compounded. KiCad has no supported API for *creating* schematics —
`pcbnew` is board-only, and the IPC API's schematic write coverage lags. Emitting
s-expressions by hand means owning UUIDs, embedded symbol instances, sheet instances and
wire coordinates across format changes. And auto-placement produces schematics no human can
review, which defeats the point of a schematic. The "update" case is worse than the "create"
case: regenerating clobbers human edits, and merging generated with hand-drawn KiCad files
is unsolved.

The deeper reason: **generation is the flashy half and the low-value half.** Verification
prevents dead boards. Generation mostly moves work around.

**Revisit when.** All of these can be answered yes: can humans review the generated output;
can human edits coexist; does it round-trip safely; is it stable across KiCad versions; does
it save more engineering time than it creates; can the verification model stay independent
of the generator. Evaluate **atopile** and **SKiDL** before writing anything custom — both
already compile text circuit descriptions to KiCad netlists. Also look at **KiBot**, which
is already a CI pipeline for KiCad and may replace parts of `scripts/`.

---

## D2. KiCad files are inputs; the normalized model is the verification interface **[owner]**

**Decision.** `pcbv.kicad.*` parses KiCad files exactly once at the edge. Every check queries
`pcbv.model` dataclasses. A check that opens a `.kicad_sch` is a bug.

**Rejected.** Letting each check parse the files it needs.

**Why.** A format change then breaks one reader instead of fifteen checks. This was not
hypothetical: KiCad 10 changed netlist line formatting and broke regex-based parsing that
assumed KiCad 7's shape (see D9).

---

## D3. One canonical gate model; everything else is generated from it **[review]**

**Decision.** `gates/gates.yaml` is the single source of truth. The checklist, ordering,
agent tasks and signoff tables derive from it.

**Rejected.** The original plan's four parallel taxonomies — 64 workflow steps, 21 release
gates, 55 checklist sections, 12 agent definitions — each maintained separately.

**Why.** Four descriptions of one process drift apart within weeks, and then nobody knows
which is authoritative.

---

## D4. Gates carry both a status and a basis **[review]**

**Decision.** Status is `PASS/FAIL/BLOCKED/NOT_APPLICABLE`. Basis is `MACHINE_CHECKED`,
`HUMAN_REVIEWED`, `LLM_ASSERTED`, or one of the `DEFERRED_TO_*` values. Both are required,
and `LLM_ASSERTED` alone can never satisfy a gate — enforced in `gatemodel.py` independently
of what a gate's own `basis_requirements` say, so a mis-declared gate cannot open the hole.

**Rejected.** A single pass/fail per gate.

**Why.** Otherwise "symbol pin 4 maps to pad 5, verified" and "EMC reviewed, looks fine"
render identically in a signoff. The first is a computed fact; the second is an opinion.
The failure mode being prevented is **rigor theatre**: internally-consistent reports that
carry more authority than their inputs deserve, which is worse than no process because it
suppresses the doubt that would otherwise prompt a manual check.

---

## D5. `NOT_APPLICABLE` is derived, never asserted **[review]**

**Decision.** Applicability comes from JSON Pointer rules evaluated against `project.yaml`.
No RF section means the RF gate is automatically N/A.

**Rejected.** Letting an agent or engineer mark a gate as not applicable.

**Why.** "Not applicable" is the cheapest way to make a hard gate disappear. Deriving it from
declared requirements means the only way to skip RF review is to declare the board has no RF.

Note `false` and empty containers do not count as present — `motor: false` means no motor.

---

## D6. Release status is computed, never claimed **[review]**

**Decision.** `evaluate_all` takes no parameter by which a caller can assert release. The
verdict is a pure function of gate outcomes. CI recomputes it on a clean checkout and is the
authority. An unimplemented gate evaluates to `BLOCKED`.

**Rejected.** The original plan's approach of blocking the agent from writing a
`RELEASE_READY` marker via hooks.

**Why.** Hook-based enforcement against your own agent is soft — it can use a different tool,
a different filename, or edit the checker. Making release a derived value means there is
nothing to forge. The `implemented: false` default means a half-built pipeline reports
`NOT_RELEASED` with reasons rather than a vacuous green.

---

## D7. Citations must be mechanically re-checkable **[review]**

**Decision.** Every requirement carries a verbatim snippet plus the source's SHA-256. A
script re-greps the snippet against cached extracted text. An unverifiable citation fails and
counts as no evidence at any level. Minimum evidence per severity is enforced in the schema:
a `CRITICAL` requirement resting on inference is rejected, not flagged.

**Rejected.** The original plan's requirement to record source/page/section — on the honour
system.

**Why.** A fabricated "datasheet p.42 §7.3" is indistinguishable from a real one in every
downstream report, and hallucinated page citations are the classic LLM documentation failure.
Unverified, the evidence hierarchy *launders a guess into evidence level A* — worse than no
citation, because it manufactures confidence.

---

## D8. Pin tables are package-qualified, with no family-level fallback **[owner]**

**Decision.** Pin data is keyed by `(MPN, package)`. `PartLibrary.pin_table` never falls back
to another package.

**Why.** Two packages of one die routinely number pins differently. The
`wrong_package_pin_table` corpus case exists to prove this: both packages are real variants
of one MPN, and a family-level lookup reports a clean pass on a design that grounds its own
supply pin.

---

## D9. Parse netlists with the s-expression reader, never with regexes **[review]**

**Decision.** `netlist_components`, `netlist_libpart_pins` and `netlist_nets` parse via
`pcbv.sexpr`.

**Why.** This was a real bug, not a precaution. KiCad 7 emitted `(comp (ref "U1")` on one
line; KiCad 10 pretty-prints each element on its own. All four oracle comparisons silently
returned nothing — **which looks exactly like a design with no components.** Structural
parsing is immune to formatting.

---

## D10. KiCad is the oracle for our own readers **[review]**

**Decision.** `tests/test_kicad_cli.py` checks our readers against `kicad-cli`: same
components, same pins, same electrical types.

**Why.** Every other test checks our code against our own expectations. A self-consistently
wrong reader would make `CHK-PIN-MAP` compare a datasheet against something the board does
not use — and every test would still pass.

**Note.** `kicad-cli` 7 exits 0 even after printing "Failed to load schematic file", so the
wrapper inspects output text and the produced file rather than exit status.

---

## D11. Capabilities are probed, not inferred from version numbers **[review]**

**Decision.** ERC availability comes from running `sch erc --help`, not from parsing a
version string. `run_erc` raises when unavailable rather than returning an empty result.

**Why.** Version sniffing is a guess about a build; the probe asks the binary. And
"no violations" and "never ran" must never be the same value.

---

## D12. Fixtures are pinned to the oldest supported file format **[review]**

**Decision.** `.kicad_sch` 20230121, `.kicad_sym` 20220914, `.kicad_mod` 20221018, enforced
by a test.

**Why.** KiCad reads older formats forward but never newer ones backward. Pinning low keeps
the corpus loadable by KiCad 7 through 10, including the Ubuntu-archive build CI falls back
to. Running `kicad-cli sym upgrade` under a newer KiCad would silently rewrite them and drop
that support.

**Revisit when.** The oldest KiCad you intend to support moves. Change
`PINNED_FORMAT_VERSIONS` deliberately and note the new minimum in the README.

---

## D13. No check may be added without a known-bad corpus case **[review]**

**Decision.** `test_every_implemented_check_has_a_known_bad_case` enforces it. Known-good
cases assert exhaustively that nothing fires, to control false positives.

**Why.** "Test it on a small example board" proves the code runs, not that it catches
anything. Without a corpus you have an untested test system, and "all gates PASS" from
untested software carries no information.

This paid off immediately: the known-good fixture once passed 120 tests while its netlist put
every pin on its own unconnected net — it was testing nothing, and only asserting the actual
netlist exposed it.

---

## D14. Qualitative fault analysis; no RPN scoring **[owner]**

**Decision.** `fault_analysis.schema.json` has no severity/occurrence/detectability fields
and no RPN. It tracks coverage: is a mitigation present, and if not, has a named human
accepted the residual risk. `fire` and `injury` outcomes always require human acceptance.

**Rejected.** Classical DFMEA scoring.

**Why.** Multiplying three guesses produces a number that looks objective and is not. The
false precision is worse than an honest qualitative answer.

---

## D15. Human sign-off on safety gates **[review]**

**Decision.** `POWER`, `BATTERY`, `RF`, `FINAL_REVIEW` require a stored approval naming a
reviewer from the project roster. Claude must never write an approval record or invent a
reviewer. `BATTERY` is a first-class gate, not a checklist item.

**Why.** The original plan ran autonomously to release with an LLM as the only independent
reviewer, over a scope including LiPo charging, USB-PD to 20 V and motors — all with real
fire and injury modes.

---

## D16. Availability is time-decaying evidence **[review]**

**Decision.** Supplier stock is a timestamped snapshot with a status of
`AVAILABLE_AT_CHECK_TIME`. A fresh check is required before fabrication.

**Why.** LCSC/JLCPCB inventory changes daily and has no stable public API. A gate that stays
green forever on a one-time check is misinformation.

---

## D17. Agents communicate only through files **[owner]**

**Decision.** Agents read and write structured artifacts. Never "the power agent said the
regulator is fine."

**Why.** Subagents do not share context. Files as shared memory give reproducibility,
caching, diffability and auditability — and make every claim traceable.

---

## D18. Advisory domains never claim measurement **[owner]**

**Decision.** RF, analog, thermal, SI and EMC gates may verify that a matching network
*exists*, that tuning footprints *exist*, that a constraint is *documented*. They may not
report antenna impedance, S11, junction temperature, NFC Q or EMC margin. Those are
`DEFERRED_TO_*`, and each gate declares which deferrals are legitimate for it.

**Why.** These cannot be verified at schematic stage without simulation or measurement.
Junction temperature depends on finished copper; coil Q needs EM simulation or a VNA.

---

## Open questions

- **CHK-ERC is not implemented.** The plumbing exists and ERC runs, but no check consumes it
  and no corpus case exercises it, so the `ERC` gate is `BLOCKED` per D13.
- **Connectivity is extracted but unused.** `netlist_nets` works; no check reads it. This
  blocks startup-state, back-power and netlist-diff checks — the highest-value remaining work.
- **The evidence-level policy is schema-enforced but has no verifier yet.** There is no
  `scripts/verify_citations` implementing the snippet re-grep D7 describes.
- **KiCad 10 was built from source here** because the environment's egress policy blocks
  every packaged route. On a normal machine the PPA is a one-line install.
