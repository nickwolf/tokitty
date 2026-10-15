# Pixel-exact Qt reference images (plan)

Spec: `docs/superpowers/specs/2026-10-14-qt-pixel-tests-design.md` (read it whole; decision numbers below refer to it). Issue #128. Branch `qt-pixel-tests-128`, worktree `.worktrees/qt-pixel-tests-128`.

## Environment

- Run tests from the worktree through `/tmp/tk128_pytest.py` (puts the worktree first on `sys.path` and drops the user-site editable finder that maps `tokitty` to the main checkout): `QT_QPA_PLATFORM=offscreen /tmp/tokitty-qt-venv/bin/python /tmp/tk128_pytest.py -m qt -q`, and without `-m qt` for the unmarked suite. Baseline at 3d57edd: 502 qt passed, 3 skipped; 2858 unmarked.
- Scripts run the same way: `/tmp/tokitty-qt-venv/bin/python -c "import sys; sys.path.insert(0,'.'); sys.meta_path[:]=[f for f in sys.meta_path if 'Editable' not in repr(f)]; ..."`, or have `pixel_check.py` insert its own repo root at `sys.path[0]` when run as a script (it does not need to drop the finder if the root comes first, but check `tokitty.__file__`).
- Native Windows check: `/mnt/c/tmp/qtspike/venv/Scripts/python.exe` (PySide6 6.12.0, Pillow, pytest) with the worktree's Windows path `C:\Tools\tokitty\.worktrees\qt-pixel-tests-128` first on `sys.path`. Run it as its own Bash call, not chained after `cd`.
- `ruff check .` must pass. No em-dashes in code, comments or docs. Match the surrounding comment density (one-line docstrings are the norm in `widget.py`).
- Commit with explicit paths, never `git add -A` (the sandbox shows phantom files).

## Slice 1+2: seams, harness, cat sheets, layouts (one implementer run)

1. `tokitty/qt/widget.py`: `_make_font(points, bold)` used by `_font_set` (product behaviour unchanged); route the status text and account label through `_text` (no halo for them, as today). Existing tests must still pass unchanged.
2. `tokitty/streamdock/render.py`: `_font` sets `PreferFullHinting` and `NoSubpixelAntialias`; correct the module docstring (decision 5).
3. `tests/qt/pixel_check.py`: the test widget subclass (decision 1 font: Aileron via `render.register_font()`, `setPixelSize(round(points*96/72))`, full hinting, no subpixel AA); a text recorder that wraps `_text` and collects `p.boundingRect(rect, flags, text)` mapped through `p.transform()` (decision 2); grab helpers; RGBA comparison with an optional mask; diff image writer (old | new | difference); indexed-PNG writer with an exact palette and a round-trip assertion (fall back to RGBA PNG above 256 colours); the `update [name ...]` command (decision 7; refuse on macOS); the cat sheet builder (decision 3: one-pane widget, `cat_home="on_top"`, empty tags, coats in `COLORWAYS` x `PATTERNS` order as rows, the state's frames as columns, palette via `pane.set_appearance(palette=sprites.resolve_palette(c, p))`, frames via `render()` then `draw_next_frame()`; check which frame index is shown right after `render()` and order columns by frame index); the six layouts (decision 4) with fixed inputs.
4. `tests/qt/test_pixel_refs.py` (`pytestmark = pytest.mark.qt`): cat sheets at dpr 1 in-process and at dpr 2 in one subprocess with `QT_SCALE_FACTOR=2` (follow `test_cat_pixels_dpr2_in_a_subprocess`); layouts at dpr 1 and 2 likewise; masks per decision 2 (macOS: all layouts; everywhere: the 20% layout); on failure write actual/reference/diff into `build/pixel-diff/` or `$TOKITTY_PIXEL_DIFF_DIR` and assert with count, bbox and first coordinates (decision 8). The suite never writes references.
5. Generate the references with `update` on WSL, then confirm they pass unchanged on native Windows. Record files and bytes.
6. `.gitattributes`: `tests/qt/pixel_refs/** binary`.

Acceptance: full qt and unmarked suites pass in WSL; the new module passes on native Windows; mutating one cell of one sprite template (then reverting) fails exactly that state's two sheets and `update` reports exactly those two files; a second `update` run reports everything unchanged.

## Slice 3: Stream Dock keys, fit check, report, CI

1. Key references per decision 5 (skip on macOS with the reason), in `tests/qt/pixel_refs/keys/`.
2. Product-font fit check (decision 4a).
3. `pixel_check.py report <base>` and the `ci.yml` changes (decision 9: Linux `qt` job on pull requests, `fetch-depth: 0`, artifact always, step summary; failure artifact upload of `build/pixel-diff/` on all three OSes).
4. Remove `tests/qt/probe_128.py` and the probe step in `ci.yml` (drop by reverting the two probe commits' content, not by rewriting history).
5. `tests/qt/pixel_refs/README.md`: update procedure, drift triage, PySide6 version and the machine the references were made on.

Acceptance: as slice 1+2, plus the PR's CI green on all three OSes with the report listing every reference as new.

## Review

Each finished slice: I review the diff, then Codex (gpt-6-sol, high, read-only) reviews it with a cold-start brief. Findings go back to the implementer.
