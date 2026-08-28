# Running pcb-verify on your own machine

Self-contained. Unzip it anywhere — it does not need to live in a git repository, and it has
no connection to GitHub.

## 1. Python

Needs **Python 3.10 or newer**. Check with `python3 --version` (`python --version` on Windows).

From inside the `pcb-verify` folder:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The virtual environment keeps this project's two dependencies (`PyYAML`, `jsonschema`) out of
your system Python. Re-activate it with the same `source`/`activate` line in any new terminal.

## 2. Check it works

```bash
python -m pytest -q
```

You should see roughly **152 passed**, with about 30 tests skipped and a message saying
`kicad-cli not on PATH`. That skip is expected until step 3 — and it is deliberately loud,
because a silently skipped check is worse than a missing one.

Then try the tool itself:

```bash
pcbv corpus                                          # run the test designs
pcbv gates                                           # the 26-gate process model
pcbv check tests/corpus/known_bad/wrong_symbol_pin -v # see it catch a real defect
pcbv release tests/corpus/known_good/minimal_ldo     # release verdict (exits 1: NOT_RELEASED)
```

`pcbv release` printing `NOT_RELEASED` is correct, not a failure. Only 2 of 26 engineering
gates have implementing checks so far, and an unimplemented gate reports `BLOCKED` rather
than `PASS` so an incomplete pipeline can never claim a release.

## 3. KiCad (optional, but it is where the real verification happens)

30 tests use KiCad itself as an oracle — same components, same pins, same electrical types —
and run ERC. Without KiCad they skip; everything else still works.

Install KiCad 8, 9 or 10 (ERC needs 8+):

| Platform | Install | `kicad-cli` location |
|---|---|---|
| **macOS** | `brew install --cask kicad` or the installer from kicad.org | `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli` |
| **Windows** | Installer from kicad.org | `C:\Program Files\KiCad\10.0\bin\kicad-cli.exe` |
| **Ubuntu/Debian** | `sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt update && sudo apt install kicad` | on `PATH` already |
| **Flatpak** | `flatpak install flathub org.kicad.KiCad` | `flatpak run --command=kicad-cli org.kicad.KiCad` |

On macOS and Windows `kicad-cli` is not on your `PATH` by default. Add it:

```bash
# macOS — add to ~/.zshrc to make it permanent
export PATH="/Applications/KiCad/KiCad.app/Contents/MacOS:$PATH"
```

```powershell
# Windows PowerShell — current session
$env:Path += ";C:\Program Files\KiCad\10.0\bin"
```

Confirm, then re-run the tests:

```bash
kicad-cli version
python -m pytest -q          # now ~152 passed, 1 skipped
```

The one remaining skip is intentional: it covers the "ERC unavailable" code path, which
cannot be exercised on a machine where ERC *is* available.

## 4. Point it at your own schematic

Copy `tests/corpus/known_good/minimal_ldo/` as a starting template. A design directory needs:

```
my_board/
├── project.yaml                   # requirements, rails, reviewers
├── schematic/my_board.kicad_sch   # drawn by you, in KiCad
└── library/parts/<MPN>/           # optional; falls back to the shared example library
    ├── part.yaml                  # packages, sources, suppliers
    └── pins.yaml                  # package-qualified pin table
```

Then:

```bash
pcbv check path/to/my_board -v
```

For `CHK-PIN-MAP` to verify a component, its schematic symbol needs `MPN` and `Package`
properties and a matching record under `library/parts/<MPN>/`. Components without an `MPN`
produce an `INFO` finding recording that they could not be checked — the tool never stays
silent about what it did not cover.

Read `CLAUDE.md` before changing anything. It is the engineering constitution: what counts as
evidence, why `UNKNOWN` blocks, why release status is computed rather than claimed, and why
no check may be added without a known-bad test case.

## Troubleshooting

**`pcbv: command not found`** — the virtual environment is not active. Re-run the
`source .venv/bin/activate` line. Or skip the entry point entirely:
`python -m pcbv.cli corpus`.

**`ModuleNotFoundError: No module named 'pcbv'`** — the editable install did not run.
Re-run `python -m pip install -e ".[dev]"` from inside the `pcb-verify` folder.

**Tests fail with format-version errors** — something ran `kicad-cli sym upgrade` on the test
fixtures and rewrote them to a newer format. They are pinned deliberately to the oldest
format so they stay loadable by KiCad 7 through 10. Restore those files from your copy of the
zip.
