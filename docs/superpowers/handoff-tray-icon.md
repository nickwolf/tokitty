# Handoff prompt — Tokitty tray icon (#21)

Paste the block below into a fresh session to execute the plan.

---

Execute the Tokitty tray-icon plan (issue #21) in this repo: C:\Tools\tokitty
(github.com/nickwolf/tokitty). Work with Nick (owner). You orchestrate; delegate
each task to a fresh Sonnet implementer. Review cadence (Nick's choice):
**one implementer per task, NO per-task reviewer — a single whole-branch review
at the end, before opening the PR.** Use superpowers:subagent-driven-development
for the dispatch mechanics but skip the per-task review loop; the plan is written
to be self-runnable, so don't re-brainstorm or re-plan.

THE PLAN (complete, TDD, exact code per step — this is your source of truth):
  docs/superpowers/plans/2026-07-21-tray-icon.md
Design rationale, if you need the "why":
  docs/superpowers/specs/2026-07-21-tray-icon-design.md

STATE: branch `tray-icon` already exists and holds the spec + plan commits
(branched off main). Just start implementing Task 1. Don't create a new branch.

WHAT IT BUILDS: a system-tray icon (pystray + Pillow — Tokitty's FIRST runtime
deps) whose menu mirrors the window's right-click menu from one source of truth;
on by default with a persisted toggle; disabled cleanly where the backend is
unavailable. 7 tasks: sprite_raster → settings → menu → ui → tray → wiring → PR.

LANDMINES (the plan handles all of these — don't undo them):
- NO module-scope `import pystray` / `from PIL import ...` on any path the test
  suite imports. All pystray/PIL access is lazy, inside functions in tray.py.
  One stray top-level import reddens the CI matrix. (Grep gate is in Task 5.)
- pystray evaluates the menu's checked/radio getters on ITS OWN thread → those
  getters read plain-Python shadow state only (`_always_on_top_bool`, `pane._coat`),
  never tkinter Vars. Every tray→UI *action* is marshaled via `root.after(0, …)`.
- Task 1 refactor must keep committed PNGs byte-identical — the plan has a
  `git status --short docs/media` gate after regenerating. Don't skip it.
- Tray is constructed only in run_gui(), never on the --debug/xvfb-smoke path,
  so the smoke test never touches pystray.

COMMITS: authored by Nick alone — NO Co-Authored-By / AI-attribution / session-URL
lines (repo + global CLAUDE.md enforce this).

FINAL REVIEW: after all 6 code tasks are committed and green locally, run one
whole-branch review (superpowers:requesting-code-review, most-capable model) over
the full `main..tray-icon` diff. Dispatch ONE fix subagent with the complete
findings list; don't fix per-finding.

CI GATE (keep all 8 green): then push → open PR with body "Closes #21" →
`gh pr checks --watch` → test matrix (ubuntu/macos/windows × py3.10/3.14) +
smoke (xvfb) + lint (ruff==0.15.22). The matrix's `pip install -e ".[dev]"` step
doubles as the dependency-resolve gate — watch py3.14 Pillow/pystray wheel
availability specifically. NEVER merge red.

MANUAL WINDOWS GATE (do NOT merge on CI-green alone — this has a visual + native
surface): hand off to Nick to run `pythonw.exe -m tokitty` on the real desktop
(check the process SessionId matches explorer.exe — launching without error does
NOT mean the tray appeared). Confirm: icon appears; right-click shows the menu;
items act; "Show tray icon" toggle disables the icon and re-enables from the
window's right-click menu; Exit quits with no orphaned process. Expect iterative
visual feedback on the icon (content-sitting pose, ~56px). Only then rebase-merge
+ delete branch, and update the tokitty-v2 project memory (#21 done → next is
coat #32/#38).

Environment note (WSL2↔Windows): Windows-GUI-from-WSL can land in invisible
Session 0 — use pythonw.exe for the windowed run and check SessionId vs
explorer.exe. WSL has python3-tk, python3-pil; sudo/apt/gh all work.
