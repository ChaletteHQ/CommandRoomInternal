---
name: check-deliverables
surfaces: both
description: "Pre-send sweep of what Command Room produced — flags anything that does not sound like the CEO or leaks internal language, BEFORE it gets forwarded. Fires on: 'check my deliverables', 'scan my deliverables', 'scan before I send' / 'scan before sending' / 'scan this before I send', 'check my output before I send', 'voice and privacy check', 'did this pass the quality check', 'scan my output for voice tells'. Read-only and flag-only — never edits, moves, or deletes; findings feed the voice-corrections corpus silently. Does NOT fire on 'automation scan' / 'where am I wasting time' (automation-scanner), 'weekly cleanup' (cleanup — runs this same sweep weekly as backstop), or 'value receipt' (value-receipt). Detector list and flag format: Routing section in the body."
---

# check-deliverables

The on-demand quality sweep. The CEO fires it before forwarding something —
"scan before I send" — and it reads what was actually produced and flags any
voice tell or privacy/internal leak it finds, by filename, in plain English.

## Skill Boundary (v2.1)

- **Use check-deliverables for:** the on-demand voice + privacy sweep over what Command Room actually produced, before it gets forwarded. Read-only and flag-only.
- **Use `cleanup` for:** "weekly cleanup" / "clean up my workspace" — cleanup runs this same sweep as its weekly backstop; this skill is the on-demand version the CEO fires themselves.
- **Use `automation-scanner` for:** "automation scan" / "where am I wasting time" — opportunity scanning, not output checking.
- **Use `dormant-customer-scan` for:** "dormant customer scan" — relationship detection, not output checking.
- **Use `value-receipt` for:** "value receipt" — the forwardable ROI counts, not a quality check.

## Why this exists (and why it's a skill, not automatic)

The save-time voice + leak gates only run when a deliverable routes through the
normal writer (`brief_writer.make_brief`). Live testing proved that route is not
guaranteed: given a busy batch, the model routinely hand-rolls a Word doc (or
drafts an email/memo as a markdown file, saves a premium-HTML brief/scorecard,
or drafts as chat text) and the gates never fire. Automatic same-turn enforcement was attempted and is not reachable in the
Cowork runtime (plugin and user-scope hooks both proved dead). So enforcement
moves to **detection the CEO invokes**: one command that reads the produced
output and flags what didn't pass — which IS reliable, because the runtime
honors skills. The scanner opens the file itself, so a hand-rolled doc is caught
exactly like a normally-written one.

Honest framing: this **detects and flags** voice/privacy issues in what was
produced, before it leaves the CEO's hands. It does not claim bad output cannot
be produced — a model with code access can always hand-roll a file; reading the
produced file is what makes the issue catchable.

## What it checks for

- **Voice tells** — generic-assistant phrasing that breaks the illusion the CEO
  wrote it (canned openers, fillers, sign-offs), plus structural tells.
- **Privacy / internal leaks** — internal record ids, substrate file paths, and
  process-narration words that should never appear in something forwarded out.

Both come from the validated engine (`docx_leak_scanner` +
`voice_tell_detector`) via `shared/scripts/deliverable_sweep.py`. The findings
are the engine's — this skill is the trigger and the plain-English surface.

## Hard rules

- **Read-only, flag-only.** Never edit, move, rename, or delete a file. The only
  writes are CR-owned telemetry (one audit event + a findings record under the
  system dir), and they can never block or touch a user file. Safe on every live
  client workspace.
- **Plain English out.** Name each flagged file by its filename and describe the
  problem the way a CEO would (the offending word, "a generic-assistant
  phrase"). Never surface internal token names, event names, or `_hq/` paths
  (CONTRACT Rule 4). The helper's `summarize_for_user` already does this — paste
  it; do not re-describe findings in your own words.
- **No false-positive noise.** Internal context/memory markdown (session notes,
  the workspace brief, views, specs, voice corpus) is deliberately NOT scanned —
  only deliverable-shaped output. A clean sweep says so in one line; it does not
  invent concerns.

## Behavior

### Step 1 — Determine the scope

| The CEO said | Scope |
|---|---|
| "scan before I send" / "check my deliverables" (no target) | Recent deliverables — everything produced in the **last ~24 hours** (default) |
| "...this week" / "everything I made this week" | Last **7 days** |
| points at a file or names a specific doc | **That file only** (point-at-target) |

If the CEO drafted a deliverable as **chat text this session** (an email or memo
written into the conversation, not saved to a file), include that text too — you
have it in context; the scanner can read it directly (Step 3).

### Step 2 — Run the sweep

Resolve the workspace per `shared/CONTRACT.md` Rule 22, then call the helper.
This is the only place findings come from:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && python3 -c "..."
```

**Default / time-window scan** (inside the `python3 -c` body, cwd is `$PLUGIN_ROOT`):

```python
import sys, json, time
sys.path.insert(0, "shared/scripts")
import deliverable_sweep as ds

ws = "<abs workspace root>"          # from $WORKSPACE
since = time.time() - 24 * 3600      # 24h default; use 7*86400 for "this week"
res = ds.sweep_workspace(ws, since_ts=since, emit=True, source="on_demand_sweep")
print(json.dumps({
    "scanned": res["scanned"],
    "violations": res["violation_count"],
    "warns": res["warn_count"],
    "errors": res["error_count"],
    "summary": ds.summarize_for_user(res),
}))
```

**Phase 6 Quick Win A (automatic, flag-only).** `sweep_workspace` / `sweep_targets` with `emit=True` also call `deliverable_sweep.feed_voice_corrections(ws, res)` internally: each FAIL-severity voice tell found in a produced deliverable is appended to the relevant `corrections-<skill>.jsonl` (attributed by filename) so insight-generator Pass 11 gets more training data for free. This is a CR-owned telemetry write under `_hq/voice/` — it NEVER edits, moves, or rewrites the user's deliverable, so the skill's read-only + flag-only contract is unchanged. Privacy/substrate leaks are NOT fed (they are not a voice pattern; they stay flag-only). Nothing to do here — it rides the existing sweep.

**Point-at-target scan** (when the CEO named a specific file):

```python
import sys, json
sys.path.insert(0, "shared/scripts")
import deliverable_sweep as ds

ws = "<abs workspace root>"
res = ds.sweep_targets(["<abs path to the file>"], workspace_root=ws, emit=True)
print(json.dumps({
    "scanned": res["scanned"],
    "violations": res["violation_count"],
    "warns": res["warn_count"],
    "errors": res["error_count"],
    "summary": ds.summarize_for_user(res),
}))
```

### Step 3 — Scan any chat-drafted deliverable text

If a deliverable was written as chat prose this session (no file), scan that
text directly — this is the path a file sweep can't see:

```python
import sys, json
sys.path.insert(0, "shared/scripts")
import deliverable_sweep as ds

chat = ds.scan_chat_text("<the deliverable text you drafted>", context="brief")
print(json.dumps({
    "has_violation": chat["has_violation"],
    "has_warn": chat["has_voice_warn"],
    "leaks": sorted({x["match"] for x in chat["leaks"]}),
    "voice_rules": sorted({x["rule"] for x in chat["voice"]["findings"]}),
}))
```

If `has_violation` (or `has_warn`) is true, fold it into the surface as "the
[email/memo] you drafted just now" with the offending word(s) named — same
plain-English style as the file findings. Do not print rule names or token names
to the CEO.

### Step 4 — Surface the result

- **Something flagged** (`violations > 0` or `errors > 0`, or chat text flagged):
  lead with the count, then paste the helper's `summary` string **verbatim** — it
  names each file by filename and the offending language in plain English. Add the
  chat-draft finding (Step 3) if any. One closing line, no pressure:
  > *"Worth a quick look before any of these go out. Say the word and I'll
  > rewrite the flagged lines in your voice."*
- **Only structural warnings** (`warns > 0`, no violations): a softer single line
  — *"Nothing that would embarrass you; a couple of spots read a little
  AI-shaped. Want me to smooth them?"*
- **Clean** (all zero, nothing in chat): one honest line —
  > *"Scanned [N] recent documents — all clean. Nothing reads AI-written and
  > nothing private leaked. Good to send."*
  (Use the real `scanned` count and pluralize naturally from it — "1 recent
  document" / "3 recent documents", never "document(s)". If `scanned` is 0,
  say there was nothing produced recently to check, and offer the
  point-at-target form.)

Never include a file path, an internal token, or an event name in any line the
CEO sees.

## What this skill does NOT do

- Does not edit, move, or delete any file — it flags; the CEO (or a follow-up
  rewrite) fixes.
- Does not scan internal context/memory markdown (session notes, views, specs,
  the workspace brief) — only deliverable-shaped output, to avoid noise.
- Does not read any connector — it reads only files already in the workspace and
  text already in the conversation. Zero external exposure.
- Does not guarantee bad output can't be produced — it detects and flags what
  was produced. The weekly `cleanup` sweep is the backstop; this is the version
  the CEO fires on demand, before forwarding.
- Does not invent findings on a clean document.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> An on-demand sweep that reads what Command Room actually produced and flags anything that does not sound like the CEO or that leaks private/internal language, BEFORE it gets forwarded. Reads the produced files themselves (Word docs, markdown, and HTML) plus any deliverable drafted as chat text this session, so it catches a document however it was made — including one hand-rolled outside the normal writer. Read-only and flag-only: it never edits, moves, or deletes a file. Triggers: 'check my deliverables', 'scan my deliverables', 'scan before I send', 'scan before sending', 'scan this before I send', 'check my output before I send', 'voice and privacy check', 'did this pass the quality check', 'scan my output for voice tells'. DOES NOT fire on 'automation scan' / 'where am I wasting time' (automation-scanner), 'scan everything' (a retired audit phrase — cleanup catches and redirects it), 'dormant customer scan' (dormant-customer-scan), 'weekly cleanup' / 'clean up my workspace' (cleanup — which runs this same sweep as its weekly backstop; this skill is the on-demand version you fire yourself), or 'value receipt' (value-receipt).
