---
name: report-bug
description: "Diagnose a Command Room misfire end-to-end and either self-fix it or draft a fully-diagnosed support email to the maintainer. Fires on: 'report bug', 'report a bug', 'this isn't working', 'something's wrong', 'it broke', 'something is off', 'this is broken', 'why isn't this working', 'report this', 'send this to support'. Auto-collects version, last skill fired, workspace state, and active connectors; pattern-matches known issues (about half of reports self-fix in-session); otherwise drafts the pre-diagnosed Gmail to the support address for your review and send — never auto-sent. Does NOT fire on conversational frustration without report-it intent, or how-does-this-work questions (the relevant skill answers those). Diagnostic flow and known-issue patterns: Routing section in the body."
---

# report-bug

Self-diagnose-then-draft skill. Customer hits a Command Room misfire; this skill either tells them how to fix it themselves OR drafts a pre-diagnosed email to the maintainer so he can verify-and-fix instead of investigate-from-scratch.

## What this is and is not

**Is:** a structured triage flow that captures one user sentence, auto-collects context, pattern-matches against known fixes, and produces either (a) a one-line self-fix or (b) a Gmail draft to the maintainer that includes the full diagnosis.

**Is not:** a generic "feedback" surface. Not for feature requests ("I wish it could…"), not for praise, not for billing questions. Pattern-strict on the trigger phrases.

## Why it exists

Without this skill, customer support flow is: user emails the maintainer → the maintainer asks 4 diagnostic questions → user answers across multiple round-trips → the maintainer finally has enough context to verify the bug. Three days, four threads.

With this skill: user hits the failure, says "report this," answers ONE question, the skill assembles the diagnostic payload, and the email that lands in the maintainer's inbox already contains the verification surface. The maintainer opens it, reads it, confirms or rejects in one pass.

Half the bugs fixed in v3.x have been one-keypress user-side fixes (full-quit Cowork, rerun update, retry). Those should never reach the maintainer. The known-issue-patterns reference file catches them before escalation.

## Behavior

### Step 1 — Acknowledge + one diagnostic question

Open with one line, no preamble:

> *"On it. Quick — what were you trying to do, and what happened instead? One sentence each is fine."*

Wait for the user's response. Accept any shape; don't reformat. The two answers fill `WHAT_TRYING` and `WHAT_HAPPENED` in the diagnosis payload.

### Step 2 — Auto-collect context (no user action)

In parallel, while the user is typing Step 1's answer (or immediately after if they answered fast):

1. **Plugin version** — read `.claude-plugin/plugin.json` from the plugin root. Capture `version` field.
2. **Workspace shape** — read `_hq/data/entities.json`. Capture: org count, person count, project count, last `tz_set_at` value. Used to confirm workspace isn't empty.
3. **Last 5 events** — read tail of `_hq/data/events.jsonl`. Capture: type, source_skill, ts for each — EXCEPT personal-lane rows: any row where `personal_leak.is_personal(row)` is true (reminder-family personal rows, the balance lane, `tie: personal`) is captured as `(personal-lane event) · (masked) · [ts]`, never its real type or source_skill. This table lands in an OUTBOUND email (Step 4b) and the Step-5 chat fallback — a personal event-type token in it is a firewall fingerprint leaving the workspace. This is the skill that most likely fired right before the failure.
4. **Active connectors** — list the MCP connectors present in this session (gmail, calendar, granola, drive, slack, github — whatever is mounted). Don't include token counts, just names.
5. **User's email** — from `entities.json` workspace block, or fall back to `_hq/CLAUDE.md` "Identity" section. Used as the `From` address on the draft.

Surface NOTHING to the user during this step. The context collection is silent — they answered one question, the email gets drafted, that's the user-facing surface.

### Step 3 — Pattern-match against known issues

Load `{SKILL_DIR}/references/known-issue-patterns.md` (skill-local — it lives inside this skill's folder in the plugin, not in the workspace). Each entry has:
- **Signature** — keywords / phrases / skill names that match the user's complaint + auto-collected context
- **Likely cause** — one-line technical summary
- **User-side fix** — what the user can try first (if any), in plain English
- **Escalate?** — `yes` (always email the maintainer), `no` (one-keypress fix, don't email), or `try-first` (suggest user-fix, escalate if it doesn't work)

Apply the match in this order:
1. Exact skill-name match — if user says `meeting-notes is broken` and the last fired skill was `meeting-notes`, prefer the meeting-notes patterns.
2. Keyword match against user's `WHAT_HAPPENED` sentence.
3. Symptom match against auto-collected context (e.g., 0 commitments + 5+ meetings = likely commitment-pipeline gap).
4. No match → default to `escalate: yes` with `pattern_match: "no known pattern"`.

### Step 4a — Self-fix path (escalate=no, or escalate=try-first)

If the matched pattern's `User-side fix` is non-empty AND `escalate ∈ {no, try-first}`:

Surface the fix verbatim with a one-line explanation:

> *"Here's what's probably going on: [Likely cause — rephrased in plain English; no skill names, file paths, or internal tokens].*
>
> *Try this first: [User-side fix].*
>
> *If that doesn't work, say `still broken` and I'll draft the email to support."*

If the user replies `still broken` (or any equivalent — "didn't work", "same issue", "still doing it"), proceed to Step 4b. If the user replies positively ("fixed it", "thanks", "worked"), append a `bug_self_fixed` event to `events.jsonl` with the matched pattern's signature + `ts` and respond with one line:

> *"Glad. Logged it. The team already knows about this one."*

### Step 4b — Draft path (escalate=yes, or try-first didn't work)

Draft to the maintainer via the **declared mail backend** (connector-agnostic-v1 parity, N9) — resolve it with `tool_discovery.discover_for_category("email", "draft", tools, declared=connector_config.declared_backend("email"))`, falling back to `discover_mail_draft_tool(tools)` when no backend is declared (Gmail/Outlook/Superhuman all work; empty map = today's behavior). Do NOT name a provider tool directly. The draft must be saved as a draft — never sent. The user reviews and sends manually. If the backend is read-only (can't draft — e.g. a read-only Outlook, per the capability manifest), skip straight to the paste-text fallback below.

**Subject:** `[Command Room bug] [last_skill or "unknown"] — [first 8 words of WHAT_HAPPENED]`

**Body:** verbatim template below, filled with real values. No invented details. If a field is unknown, write `"(not detected)"` — never fabricate.

```
Hi,

Command Room hit something unexpected. Auto-diagnosis below — review and let me know.

WHAT I WAS DOING
[WHAT_TRYING from user, verbatim]

WHAT HAPPENED INSTEAD
[WHAT_HAPPENED from user, verbatim]

AUTO-DIAGNOSIS
• Plugin version: [version from plugin.json]
• Last skill fired: [source_skill from tail of events.jsonl] at [ts]
• Workspace shape: [N orgs, N people, N projects]
• Active connectors: [comma-separated list]
• Timezone: [user_timezone or "(not set)"]
• Pattern match: [matched pattern signature, or "no known pattern"]
• Suggested user-side fix (already tried): [User-side fix from pattern, or "(none — needs investigation)"]

LAST 5 EVENTS
1. [type] · [source_skill] · [ts]
2. ...
3. ...
4. ...
5. ...

— [User's first name], via Command Room (plugin v[version from plugin.json — the Step 2 auto-collected value, read at runtime; never hardcode])
```

After the draft is created, surface ONE line to the user:

> *"Drafted in your mail — review and send when ready. Subject line: `[subject from above]`. Support has everything they need to verify."*

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees in CHAT (the email body's AUTO-DIAGNOSIS block is the one sanctioned diagnostic surface — versions and skill names live there, not in chat lines) — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "This matches a known pattern: stale orchestrator taskId cache."
- Good: "Here's what's probably going on: my schedule needed a refresh."

Then append a `bug_reported` event to `events.jsonl`:
```json
{"type":"bug_reported","ts":"<ISO-now>","data":{"pattern_match":"<signature>","last_skill":"<skill>","plugin_version":"<version>"}}
```

**Append through the locked writer (SPEC GATE1 / A1):** write this event — and the `bug_self_fixed` event in Step 4a — via `atomic_append_jsonl(events_path, [event], holder="report-bug")` (omit `seq`; it is auto-stamped inside the lock), NOT a hand-rolled `next_seq`+`open('a')` or a raw `>>`. See `shared/WORKSPACE_API.md` → Append Protocol §3.

### Step 5 — If anything in the pipeline itself fails

If the resolved draft call fails (no mail connector mounted, auth expired, read-only backend, etc.), fall back to surfacing the full email body as a code block in chat with one line:

> *"Couldn't draft to your mail directly — copy this and send to matthew@chaletteholdings.com:"*

Then paste the full email body. Don't lose the diagnosis just because the connector path failed.

## Rules

1. **One question, never two.** The user already hit a frustrating bug — don't make them answer a quiz. One sentence each for trying / happened is the whole user-input surface.
2. **Never invent fields.** If `plugin.json` isn't readable, write `(not detected)`. If `events.jsonl` is empty, write `(no events captured)`. Fabricated diagnoses are worse than honest gaps.
3. **Pre-fill, don't pre-send.** The user reviews the draft in Gmail before it goes. The maintainer respects them; never auto-send.
4. **Pattern file is the moat.** Every bug the maintainer fixes from real customer feedback should be added to `{SKILL_DIR}/references/known-issue-patterns.md` so the next customer hits a one-line self-fix instead of an email.
5. **Silent context collection.** Don't narrate "I'm reading your plugin.json…" — the user answered one question, the draft appears, that's the surface. Internal steps stay internal.

## What it doesn't do

- Does not fire on praise, feature requests, or billing questions.
- Does not send the email — only drafts.
- Does not invoke other skills (no `meeting-notes`, no `workspace-manager`). Single-purpose surface.
- Does not log to telemetry or external systems. The `bug_reported` event in events.jsonl is the only side effect outside the Gmail draft.
- Does not run if no Gmail connector is mounted AND `_hq/data/events.jsonl` is empty (no useful diagnosis possible — tell the user to email the maintainer directly with a one-liner).

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Diagnose a Command Room misfire end-to-end and either self-fix it or draft a fully-diagnosed support email to the maintainer. Auto-collects plugin version, last skill that fired, workspace state, and active connectors. Pattern-matches against known issues ({SKILL_DIR}/references/known-issue-patterns.md — skill-local, ships with the plugin) — half of reports never need to leave the user because the fix is a one-liner. Use when the user says: 'report bug', 'report a bug', 'this isn't working', 'something's wrong', 'it broke', 'report this', 'send this to support', 'tell the team about this', 'something is off', 'this is broken', 'why isn't this working'. Output is a Gmail draft pre-filled to matthew@chaletteholdings.com (the maintainer's public support address) with the full diagnosis — user reviews and sends. DOES NOT fire on conversational frustration ('this is annoying' / 'ugh') without an explicit report-it intent. DOES NOT fire on questions about how something works (that's the relevant skill, not a bug).
