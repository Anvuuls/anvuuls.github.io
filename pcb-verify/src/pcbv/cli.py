"""``pcbv`` command line entry point.

Subcommands are deliberately read-and-report only. Nothing here writes into a design, and
``release`` computes a verdict rather than recording one -- the signoff is build output, and
CI on a clean checkout is the authority.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .checks import registered_checks, run_checks
from .corpus import CORPUS_DIR, SHARED_DIR, build_context, discover_cases, match_case, run_case
from .findings import FindingList, SUBSTANTIVE_SEVERITIES, severity_rank
from .gatemodel import EvaluationInputs, evaluate_all, load_gates
from .schema import SCHEMA_FILES, check_all_schemas, load_and_validate

_SEVERITY_MARK = {
    "CRITICAL": "!!",
    "HIGH": " !",
    "MEDIUM": " ~",
    "LOW": " -",
    "INFO": " i",
}


def _print_findings(findings: list, *, verbose: bool) -> None:
    if not findings:
        print("  no findings")
        return
    for finding in sorted(findings, key=lambda f: (severity_rank(f.severity), f.check_id, f.component or "")):
        mark = _SEVERITY_MARK.get(finding.severity, "  ")
        where = finding.component or "-"
        pin = f" pin {finding.pin}" if finding.pin else ""
        print(f"  {mark} {finding.severity:8} {finding.code:30} {where}{pin}")
        print(f"       {finding.message}")
        if verbose:
            if finding.expected is not None:
                print(f"       expected: {finding.expected}")
            if finding.actual is not None:
                print(f"       actual:   {finding.actual}")
            if finding.remediation:
                print(f"       fix:      {finding.remediation}")


# ------------------------------------------------------------------ subcommands


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate every schema, then every artifact discoverable in a design directory."""
    print(f"schemas: {len(check_all_schemas())} valid")

    failures = 0
    targets: list[tuple[str, Path]] = []

    root = Path(args.path) if args.path else CORPUS_DIR
    for kind, pattern in (
        ("project", "**/project.yaml"),
        ("part", "**/part.yaml"),
        ("pins", "**/pins*.yaml"),
        ("expected_findings", "**/expected_findings.yaml"),
        ("power_budget", "**/power_budget.yaml"),
        ("startup_states", "**/startup_states.yaml"),
        ("pin_matrix", "**/pin_matrix.yaml"),
        ("interface_matrix", "**/interface_matrix.yaml"),
        ("fault_analysis", "**/fault_analysis.yaml"),
        ("calculation", "**/calculations.yaml"),
        ("waiver", "**/waivers.yaml"),
        ("approval", "**/approvals.yaml"),
    ):
        targets.extend((kind, p) for p in sorted(root.glob(pattern)))

    for kind, path in targets:
        try:
            load_and_validate(kind, path)
            print(f"  ok   {kind:18} {path.relative_to(root)}")
        except Exception as exc:  # noqa: BLE001 - report every failure, do not stop
            failures += 1
            print(f"  FAIL {kind:18} {path.relative_to(root)}\n       {exc}")

    print(f"\n{len(targets)} artifact(s) checked, {failures} failed")
    return 1 if failures else 0


def cmd_check(args: argparse.Namespace) -> int:
    """Run deterministic checks against a design directory."""
    design_dir = Path(args.design)
    context = build_context(design_dir, shared_dir=Path(args.shared) if args.shared else SHARED_DIR)

    findings, checks_run, errors = run_checks(context, only=args.only or None)
    collected = FindingList()
    collected.extend(findings)

    print(f"design:       {context.design.name}")
    print(f"schematic:    {context.design.root_file}")
    print(f"components:   {len(context.design.components)}")
    print(f"checks run:   {', '.join(checks_run) or 'none'}")
    if errors:
        print(f"checks ERRORED: {errors}")
    print()
    _print_findings(collected.findings, verbose=args.verbose)

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                collected.to_dict(design=context.design.name, checks_run=checks_run, errors=errors),
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")

    substantive = [f for f in collected.findings if f.severity in SUBSTANTIVE_SEVERITIES]
    print(f"\n{len(substantive)} substantive finding(s), {len(collected) - len(substantive)} informational")
    if errors:
        return 2
    return 1 if substantive else 0


def cmd_gates(args: argparse.Namespace) -> int:
    """Show the canonical gate model, or generate the review checklist from it."""
    graph = load_gates()

    if args.checklist:
        print("# Review checklist\n")
        print("Generated from gates/gates.yaml. Do not edit by hand.\n")
        for gate in graph:
            flags = []
            if gate.mandatory:
                flags.append("mandatory")
            if gate.human_approval_required:
                flags.append("human approval")
            if not gate.implemented:
                flags.append(f"NOT IMPLEMENTED (phase {gate.phase})")
            print(f"## {gate.gate_id} -- {gate.name}")
            if flags:
                print(f"*{', '.join(flags)}*\n")
            print(f"{gate.description.strip()}\n")
            if gate.checks:
                print(f"- checks: {', '.join(gate.checks)}")
            if gate.dependencies:
                print(f"- depends on: {', '.join(gate.dependencies)}")
            if gate.permitted_deferrals:
                print(f"- may defer: {', '.join(gate.permitted_deferrals)}")
            print()
        return 0

    available = set(registered_checks())
    print(f"{len(graph)} gates in dependency order:\n")
    print(f"  {'GATE':16} {'IMPL':5} {'MAND':5} {'APPR':5} CHECKS")
    for gate in graph:
        impl = "yes" if gate.implemented else "no"
        mand = "yes" if gate.mandatory else "no"
        appr = "yes" if gate.human_approval_required else "-"
        checks = ", ".join(
            f"{c}{'' if c in available else '*'}" for c in gate.checks
        ) or "-"
        print(f"  {gate.gate_id:16} {impl:5} {mand:5} {appr:5} {checks}")
    print("\n  * check is declared in gates.yaml but not yet registered in code")
    return 0


def cmd_corpus(args: argparse.Namespace) -> int:
    """Run every corpus case and report contract mismatches."""
    cases = discover_cases()
    failures = 0
    for case in cases:
        result = run_case(case)
        report = match_case(result)
        status = "OK" if report.ok else "MISMATCH"
        substantive = len(result.substantive())
        print(f"[{status:8}] {case.kind:11} {case.name:28} {substantive} substantive finding(s)")
        if not report.ok:
            failures += 1
            print(report.describe())
        if args.verbose:
            _print_findings(result.findings, verbose=False)
    print(f"\n{len(cases)} case(s), {failures} mismatch(es)")
    return 1 if failures else 0


def cmd_release(args: argparse.Namespace) -> int:
    """Compute gate status and the release verdict for a design.

    The verdict is derived here and printed; it is never written into the design tree as an
    assertion. CI recomputes it on a clean checkout, and that recomputation is the authority.
    """
    design_dir = Path(args.design)
    graph = load_gates()
    context = build_context(design_dir, shared_dir=Path(args.shared) if args.shared else SHARED_DIR)

    findings, checks_run, errors = run_checks(context)
    collected = FindingList()
    collected.extend(findings)

    available = {"project.yaml"} | {
        str(p.relative_to(design_dir)) for p in design_dir.rglob("*.yaml")
    }

    approvals: list[dict] = []
    approvals_file = design_dir / "approvals.yaml"
    if approvals_file.is_file():
        approvals = load_and_validate("approval", approvals_file)["approvals"]

    waivers: list[dict] = []
    waivers_file = design_dir / "waivers.yaml"
    if waivers_file.is_file():
        waivers = load_and_validate("waiver", waivers_file)["waivers"]

    outcomes, release = evaluate_all(
        graph,
        EvaluationInputs(
            project=context.project,
            findings=[f.to_dict() for f in collected.findings],
            checks_run=set(checks_run),
            checks_errored=errors,
            approvals=approvals,
            waivers=waivers,
            available_artifacts=available,
        ),
    )

    print(f"PROJECT   {context.project['project']['name']}")
    print(f"REVISION  {context.project['project']['revision']}")
    print()

    by_basis: dict[str, list] = {}
    for outcome in outcomes:
        key = ", ".join(outcome.basis) if outcome.basis else "NO BASIS"
        by_basis.setdefault(key, []).append(outcome)

    for basis in sorted(by_basis):
        print(f"{basis}")
        print("-" * max(16, len(basis)))
        for outcome in by_basis[basis]:
            note = f"  ({outcome.notes[0]})" if outcome.notes else ""
            print(f"  {outcome.gate_id:16} {outcome.status}{note}")
        print()

    print(f"FINAL RESULT: {release['state']}")
    print(f"PROJECT STATE: {release['project_state']}")
    if release["reasons"]:
        print(f"\n{len(release['reasons'])} blocking reason(s):")
        for reason in release["reasons"][: args.max_reasons]:
            print(f"  - {reason}")
        if len(release["reasons"]) > args.max_reasons:
            print(f"  ... {len(release['reasons']) - args.max_reasons} more")

    if args.json:
        payload = {
            "schema_version": 1,
            "project": context.project["project"]["name"],
            "revision": context.project["project"]["revision"],
            "computed_at": args.now or "unset",
            "gates": [o.to_dict() for o in outcomes],
            "release": release,
        }
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    return 0 if release["state"] == "SCHEMATIC_RELEASED" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pcbv",
        description="Verification-first checks for human-drawn KiCad schematics",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate schemas and artifacts")
    p_validate.add_argument("path", nargs="?", help="root to scan (default: the corpus)")
    p_validate.set_defaults(func=cmd_validate)

    p_check = sub.add_parser("check", help="run deterministic checks on a design")
    p_check.add_argument("design", help="design directory containing project.yaml")
    p_check.add_argument("--only", nargs="*", help="restrict to these check IDs")
    p_check.add_argument("--shared", help="shared library root")
    p_check.add_argument("--json", help="write findings JSON here")
    p_check.add_argument("-v", "--verbose", action="store_true")
    p_check.set_defaults(func=cmd_check)

    p_gates = sub.add_parser("gates", help="show the canonical gate model")
    p_gates.add_argument("--checklist", action="store_true", help="generate the review checklist")
    p_gates.set_defaults(func=cmd_gates)

    p_corpus = sub.add_parser("corpus", help="run the known-good/known-bad corpus")
    p_corpus.add_argument("-v", "--verbose", action="store_true")
    p_corpus.set_defaults(func=cmd_corpus)

    p_release = sub.add_parser("release", help="compute gate status and release verdict")
    p_release.add_argument("design", help="design directory containing project.yaml")
    p_release.add_argument("--shared", help="shared library root")
    p_release.add_argument("--json", help="write the gate result JSON here")
    p_release.add_argument("--now", help="timestamp to record")
    p_release.add_argument("--max-reasons", type=int, default=12)
    p_release.set_defaults(func=cmd_release)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
