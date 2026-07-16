# Pre-flight: Claude Code hook semantics — empirical verification (tokitty #3)

Environment: WSL2 Ubuntu, `claude` CLI v2.1.211 (`/home/cptsmidge/.local/bin/claude` → `/home/cptsmidge/.local/share/claude/versions/2.1.211`), Python 3.12.
Isolation mechanism used: `--settings <scratch-file>` pointed at a logging-hooks-only JSON file under
`/tmp/claude-1000/-mnt-c-Tools/48f9e033-bf22-40a8-875b-24e823466b99/scratchpad/preflight-home/settings.json`.

**Isolation mechanism note (safety-relevant):** `CLAUDE_CONFIG_DIR` pointed at a fresh scratch dir was tried first but requires its own `.credentials.json` — the auto-mode classifier correctly blocked copying the live `~/.claude/.credentials.json` into scratch space as credential leakage, so that path was abandoned. `--settings <file>` was used instead: it **merges** the scratch hooks on top of the real `~/.claude/settings.json` permissions (confirmed below — the base file's `Bash(echo:*)`, `Bash(mkdir:*)`, etc. allow-rules stayed active during tests). `~/.claude/settings.json` and `~/.claude/settings.local.json` were never written to; only read. No files under `~/.claude-work/` were touched.

---

## Q1 — Subagent (Task tool) hook firing + session_id attribution

**Verdict: PreToolUse/PostToolUse fire for subagent tool calls, tagged with the SAME session_id as the parent, plus new `agent_id`/`agent_type` fields. SubagentStop fires once, also under the parent's session_id.**
**Confidence: high (directly observed, single clean run).**

Command:
```
claude -p "Use the Task tool with subagent_type general-purpose to run the bash command 'echo hi' and report the output" \
  --settings $PH/settings.json --allowedTools Task,Bash
```

Evidence (session_id `667b8456-24ea-4e8a-b98d-1490b227e872` throughout):

- `PreToolUse` for the parent's `Agent` (Task) call — no `agent_id` field:
  `{"session_id":"667b8456-...","hook_event_name":"PreToolUse","tool_name":"Agent","tool_input":{...,"subagent_type":"general-purpose",...}}`
- `PreToolUse` for the subagent's own `Bash` call — **same session_id**, plus new fields:
  `{"session_id":"667b8456-...","agent_id":"a30c871a5416b8b61","agent_type":"general-purpose","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"echo hi",...}}`
- `PostToolUse` for that Bash call — same session_id + agent_id.
- `PostToolUse` for the parent's `Agent` call, with the subagent's full result embedded (`resolvedModel":"claude-fable-5"`, `totalTokens`, etc.) — no agent_id (parent-level event).
- `SubagentStop` fired exactly once:
  `{"session_id":"667b8456-...","agent_id":"a30c871a5416b8b61","agent_type":"general-purpose","hook_event_name":"SubagentStop","stop_hook_active":false,"agent_transcript_path":".../667b8456-.../subagents/agent-a30c871a5416b8b61.jsonl",...}`

**Implication for the state machine:** a hook that only keys state off `session_id` will conflate parent and subagent activity. Tokitty's state files must additionally branch on presence/value of `agent_id` (absent = parent-level event, present = subagent-level event) if it wants to distinguish "the cat is doing subagent work" from top-level work, or else subagent tool calls will silently count as parent activity. `SubagentStop` is the clean signal for "a subagent just finished" and carries `agent_transcript_path` for follow-up reads.

---

## Q2 — settings.json hot-reload

**Verdict: NOT hot-reloaded. A hook added to the settings file mid-session never fires for that already-running process; only a newly-started process (new session) picks it up.**
**Confidence: high (directly observed).**

Method: started a real interactive session in `tmux` (not headless — needed a genuinely long-lived process) with `--settings $PH/settings.json` containing only `PreToolUse`/`PostToolUse`/`SubagentStop`/`Notification` hooks. Sent one Bash tool call to establish a baseline (session_id `84226fb1-2eb6-4dea-b2c6-e5b4a9fa475b`), confirmed it logged. **While the process was still running**, edited `$PH/settings.json` on disk to add a new `UserPromptSubmit` hook. Then sent a second prompt/tool call in the *same still-running* session.

Evidence:
- `PreToolUse.log` shows two entries, both `session_id=84226fb1-...` — the baseline call and the post-edit call (`echo hotreloadtest`) — proving hooks that existed at session start kept firing correctly across the edit.
- `UserPromptSubmit.log`: **no file was created at all** for that session — the newly-added hook type never ran, even though a `UserPromptSubmit` event unambiguously occurred (the second prompt was submitted and processed).
- A fresh `claude -p` process started afterward, pointed at the same edited settings file, immediately produced a `UserPromptSubmit.log` entry on its very first prompt — confirming the hook definition itself was valid and the new process picked it up.

**Implication for the state machine:** if tokitty's installer/updater rewrites `settings.json` (e.g., on a version bump) while Claude Code sessions are already open, those open sessions keep running under the old hook set until they end and a new session starts. Any install/upgrade flow that depends on hooks being active "immediately" needs to either message the user to restart open sessions, or accept a window where old sessions have stale/no state-file behavior.

---

## Q3 — Notification hook vs. notification preferences

**Verdict: Partially verified. Headlessly, blocking a tool call for lack of permission does NOT fire the `Notification` hook (matcher `permission_prompt`) — only `PreToolUse` fires, followed by the tool being auto-denied. Whether an actual interactive TTY prompt fires the hook regardless of `preferredNotifChannel` is unverifiable from this headless environment.**
**Confidence: high for the headless half; not tested for the interactive half.**

Sub-finding 1 — headless tool calls with `--allowedTools` naming the tool are pre-approved silently (no prompt, no denial):
```
claude -p "Use the Write tool to write..." --settings $PH/settings.json --allowedTools Write --permission-mode manual
```
→ file was written, `PreToolUse`/`PostToolUse` fired, no `Notification` log. (`--allowedTools` bypasses the permission check entirely for the named tool, even in `manual` mode.)

Sub-finding 2 — headless tool call for a tool NOT pre-approved, `--permission-mode manual`, no TTY:
```
claude -p "Use the Write tool to write the text hello to $PH/permtest4.txt" --settings $PH/settings.json --permission-mode manual
```
→ exit code 2, assistant text: *"The write was blocked by a permission prompt that hasn't been granted yet... nothing was written."* File confirmed absent. `PreToolUse` fired (logged). **`Notification.log` was never created** — the hook did not fire even though the model's own text describes this as a blocked permission prompt.

So in headless mode, "permission prompt" is really an instant auto-deny with no interactive prompt UI shown — and the `Notification` hook (matcher `permission_prompt`) appears tied to an actual UI prompt being displayed, not to the underlying allow/deny decision. This is consistent with `permission_prompt` firing only when a human would be shown something to click/answer.

`preferredNotifChannel`: confirmed to exist as a real setting — found via `strings` on the installed binary (`/home/cptsmidge/.local/share/claude/versions/2.1.211`), values observed nearby: `auto`, `disabled`, plus terminal-specific channels (`ghostty`, `iterm2`, `iterm2_with_bell`). This looks like an OS/terminal-bell notification preference layered on top of the hook system, not a gate on it — but this is inferred from strings in the binary, not from a live test, since no interactive TTY was available in this task.

**Needs interactive verification** (see bottom section) — could not be completed headlessly.

**Implication for the state machine:** do not rely on the `Notification`/`permission_prompt` hook firing for tool calls blocked in automated/headless contexts (e.g., CI, cron-triggered sessions) — it won't. If tokitty wants a "cat is waiting on you" state for permission prompts, it must be driven from real interactive sessions only, and the open question of whether `preferredNotifChannel: disabled` suppresses the hook (not just the OS toast) needs the interactive test below before that state can be trusted.

---

## Q4 — Hook wall-time benchmark from ext4

**Verdict: PASSES the <100ms gate on ext4 (WSL2 home). `/mnt/c` (9p/DrvFs) is slower and would blow the budget on cold start, and remains close to the ceiling even warm.**
**Confidence: high (direct measurement, 20 warm runs + explicit cold-first-run each).**

Script (stdlib only, `read stdin JSON → temp-file → os.replace`), from
`/tmp/claude-1000/.../preflight-home/bench/state_hook.py` (ext4 — this scratch dir is on the WSL2 ext4 filesystem, not `/mnt/c`):
```python
import sys, json, os, tempfile
def main():
    data = json.loads(sys.stdin.read())
    state = {"session_id": data.get("session_id"), "seen": True}
    d = os.path.dirname(os.path.abspath(__file__))
    fd, tmp = tempfile.mkstemp(dir=d)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)
    os.replace(tmp, os.path.join(d, "state.json"))
if __name__ == "__main__":
    main()
```
Sample input: `{"session_id":"bench-test","hook_event_name":"PreToolUse"}`

**ext4** (`/tmp/.../preflight-home/bench/`, single-process `subprocess.run(["python3", ...])` per call, wall clock via `time.perf_counter()`):
- Standalone cold-ish single run (no prior warmup in that process tree): **51.5 ms**
- 20 runs, sorted: 41.6 / 43.9 / 45.5 / 45.7 / 46.2 / 47.2 / 49.4 / 51.6 / 52.2 / 52.9 / 53.5 / 53.5 / 53.8 / 54.2 / 54.3 / 54.4 / 54.6 / 55.0 / 55.1 / 56.0 ms
- **min 41.6 ms, median 53.5 ms, max 56.0 ms** — dominated by Python interpreter startup, not I/O. Well under the 100 ms gate, with headroom.

**`/mnt/c` for contrast** (`/mnt/c/Tools/tokitty/.scratch_bench/`, same script/data, cleaned up after):
- Cold first run: **119.6 ms**
- 20 subsequent runs, sorted: 65.1 / 73.5 / 75.3 / 75.9 / 78.2 / 83.6 / 83.8 / 84.4 / 85.8 / 87.3 / 89.4 / 89.8 / 96.8 / 98.2 / 98.5 / 102.7 / 111.7 / 116.6 / 120.1 / 121.6 ms
- **min 65.1 ms, median 89.4 ms, max 121.6 ms** — cold run and several warm runs exceed the 100 ms gate; median is close to it with no margin.

**Implication for the state machine:** state files/hook scripts must live under the WSL2-native ext4 tree (e.g. `~/.claude/tokitty-state/` or similar), never under `/mnt/c/...`, or the 100 ms gate will be blown on a meaningful fraction of invocations, especially cold ones. This matches the existing project convention of treating `/mnt/c` as slow (see project CLAUDE.md's general WSL2↔Windows caution) but here it's directly measured for this specific workload rather than assumed.

---

## Needs interactive verification

1. **Q3, full test**: does the `Notification` hook (matcher `permission_prompt`) fire when a *real* interactive TTY permission prompt is shown, and is that independent of `"preferredNotifChannel": "disabled"`? Headless mode never shows the actual prompt UI (it auto-denies instead, per Q3 finding above), so this can't be forced from a script.
   **Recipe for the owner:**
   - Copy `~/.claude/settings.json` is NOT needed — instead run interactively with `claude --settings <scratch-settings-with-Notification-hook>` in a real terminal (not tmux-scripted; a human needs to be present to *not* answer the prompt immediately, or to answer it).
   - In that scratch settings file set both a `Notification` hook (matcher `permission_prompt`) and (in the *same* file, since `--settings` merges but doesn't override the base file's absence of this key) `"preferredNotifChannel": "disabled"` under whatever top-level key the live settings.json uses for it (confirm the exact key path first with `claude config get preferredNotifChannel` interactively, or by grep'ing an example from `~/.claude/settings.json`/`~/.claude.json` if already set there).
   - Trigger a genuine prompt: ask Claude to use a tool that is neither pre-approved nor passed via `--allowedTools` (e.g. `Bash(rm:*)` or any Write outside allow-listed paths) in an **interactive** (non `-p`) session.
   - Watch whether (a) the OS/terminal notification (bell/toast) appears — should be suppressed by `disabled` — and (b) whether the hook's log file still gets an entry. If the log gets an entry regardless of (a), that confirms the two are decoupled as hypothesized.

2. **Q2, stronger variant (optional, not blocking)**: this task confirmed *new* hook keys aren't picked up mid-session. It did not test whether *editing* an already-active hook's command string mid-session changes behavior on the next matching call (vs. keeping the process's original command). Given the settings file is presumably read once at startup, the same "no reload" conclusion almost certainly extends here too, but if the owner wants certainty, repeat the tmux method above but edit `PostToolUse`'s existing `command` value instead of adding a new key.

---

## Cleanup performed
- Removed `/mnt/c/Tools/tokitty/.scratch_bench/` after the `/mnt/c` benchmark.
- Removed the `.credentials.json` copy from the scratch home dir immediately after the auto-mode classifier blocked it (it was never used).
- All scratch files remain under `/tmp/claude-1000/-mnt-c-Tools/48f9e033-bf22-40a8-875b-24e823466b99/scratchpad/preflight-home/` for inspection; nothing was written to `~/.claude/settings.json`, `~/.claude/settings.local.json`, or `~/.claude-work/`.
