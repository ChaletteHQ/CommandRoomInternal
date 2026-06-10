# Runtime Debugging — Cowork as Diagnostic Surface (v2.14.19+)

**Purpose:** when a Command Room widget renders something wrong (bad date, missing button, hand-built shape, etc.), the fastest way to diagnose is to ask Cowork's chat to dump raw state from disk + memory and self-diagnose. This file is a paste-ready prompt library so the next debug session takes 5 minutes, not 50.

## Why this works

The same model that runs the orchestrators can introspect them. It has direct read access to `_hq/data/events.jsonl`, `_hq/data/entities.json`, the plugin source, the renderer code, and Granola transcripts via the native MCP. Asking it to "dump raw state" with explicit file paths and field names produces actionable debug output without firing the expensive scheduled task again.

## Debugging principles

1. **Always run diagnostics in a fresh chat (or the main workspace-manager chat).** Never paste into a specific scheduled-task thread — the diagnostic context can drift into the next fire.
2. **Always ask for raw output, no summarization.** The model's natural instinct is to summarize; insist on verbatim JSON / source / transcript.
3. **Always close with a "tell me your read in one paragraph" diagnosis.** The model is good at self-diagnosing once you've forced it to lay out the raw inputs.
4. **Always explicitly say "don't fix anything; just diagnose."** Otherwise the model may try to apply a fix mid-diagnosis.

## Prompt templates

### Template A — A widget rendered the wrong data

Use when a scheduled-task widget shows wrong dates, wrong counts, missing items, or items that don't match what's in events.jsonl.

```
I'm debugging the [TASK NAME] scheduled-task widget. [Describe the visible
problem in one sentence.] Pull the following four things and dump raw, no
summaries.

1. The current orchestrator file. Find the file at
   skills/enable-command-room-schedules/references/orchestrator-[NAME].md.
   Show me Phase 2 (the build / render section) verbatim. I specifically
   need to see whether it calls render_chat_output_widget(data_view) like
   the other orchestrators or whether it hand-builds the widget HTML.

2. The data view that just rendered this widget. The orchestrator builds
   a data_view dict and passes it to either the renderer or
   mcp__visualize__show_widget. Show me that dict verbatim, including the
   top-level header, sections, items, AND any custom keys like counters,
   summary, bottom_buttons, etc. that wouldn't be in the canonical
   shared/CHAT_ACTION_WIDGET.md schema.

3. The relevant events. Read _hq/data/events.jsonl. Find every event of
   type [TYPE — meeting / commitment / interaction / etc.] from the last
   [WINDOW] that mentions [PERSON / TOPIC]. Dump full event JSON for each.

4. The relevant predicate (if applicable). Read shared/scripts/build_*.py
   and shared/scripts/cru_match.py. Search for the filter expression that
   decides which items make it into [BUCKET]. Show me the verbatim
   expression.

After dumping all 4, tell me your read in one paragraph: where in the
chain (extraction → events.jsonl → orchestrator data view → renderer) did
the wrong [thing] enter the pipeline. Don't fix anything; just diagnose.
```

### Template B — Structural audit across multiple orchestrators

Use when you suspect a class of bug spans multiple scheduled tasks.

```
I'm doing a structural audit of every Command Room scheduled-task
orchestrator. Don't fire any tasks; just read source.

1. List every orchestrator file under
   skills/enable-command-room-schedules/references/. For each
   orchestrator-*.md, list filename and scheduled-task ID.

2. Render-call inventory. For each, search for: render_chat_output_widget,
   mcp__visualize__show_widget, validate_chat_output, inline <div class="cr-
   patterns, and any hardcoded button labels not in CANONICAL_ACTIONS
   (verify against shared/scripts/chat_output_renderer.py top of file).
   Verdict per file: CANONICAL / HAND-BUILT / MIXED.

3. [TARGETED FILTER — describe the predicate / shape you want consistent
   across orchestrators, e.g., "date predicates," "action sets," "empty-state
   handling"]. List per orchestrator.

4. Summary verdict: which orchestrators are clean, which need full migration,
   which need only minor fixes.

Don't fix anything. Just inventory + diagnose.
```

### Template C — A widget rendered the wrong shape

Use when the widget shape itself is off (wrong layout, missing buttons, hand-built feel).

```
I'm debugging the [TASK NAME] scheduled-task widget. The widget shape is
[describe — e.g., "all-clear card with no per-item actions" / "bottom-row
nav buttons that don't match other widgets"]. I need to know what's
producing that shape.

1. The orchestrator file [path]. Show me the rendering section verbatim.

2. The data view that just rendered this widget. Show me the dict verbatim,
   including any custom keys outside the canonical CHAT_ACTION_WIDGET.md
   schema (counters, bottom_buttons, summary cards, etc.).

3. CANONICAL_ACTIONS verification. Read shared/scripts/chat_output_renderer.py.
   Show me the exact CANONICAL_ACTIONS frozenset contents. Then list every
   button label visible in the data view above. Flag any that's not in
   CANONICAL_ACTIONS.

4. Render-call check. Did the orchestrator call render_chat_output_widget()?
   Did the validators (_validate_canonical_actions, validate_chat_output)
   actually run? Or was the widget HTML hand-built and passed straight to
   mcp__visualize__show_widget?

After dumping all 4, tell me your read in one paragraph: (a) is the
orchestrator on canonical pipeline or hand-built? (b) where do the
non-canonical buttons / custom shape pieces come from — hardcoded in the
orchestrator prompt, model-improvised, or read from some config? Don't fix
anything; just diagnose.
```

### Template D — Cross-meeting / cross-context fusion suspected

Use when commitments or decisions seem attributed to the wrong source meeting/email.

```
I'm debugging a possible cross-meeting fusion. [Describe what looks
attributed to the wrong source.] Pull the following three things.

1. The events. Read _hq/data/events.jsonl. Find the [TYPE] event that
   produced the suspect item. Show me data.title, data.source_ref, and
   any data.source_event_seq fields verbatim.

2. The transcripts. From the source_ref in #1, pull the actual transcript
   via Granola native MCP. Search the verbatim text for the key phrases
   that appear in data.title — show me each match with 1-2 lines of
   context. If the text doesn't contain those phrases, search the
   ADJACENT meetings (same week, similar attendees) and report which one
   actually contains the phrases.

3. The attribution chain. Show me how the orchestrator decided which
   meeting to attribute this to. If the source_ref points at meeting A
   but the language is in meeting B, the bug is cross-meeting fusion at
   extraction time.

Tell me your read: which meeting is the actual source, and what (if
anything) prevented the extractor from attributing correctly.
```

## When to NOT use these prompts

- **Production scheduled-task fires.** Don't paste these into a Commitments / Inbox / Pulse chat that fires on cron. Fresh chat or main workspace chat only.
- **As a fix vehicle.** These are read-only diagnostics. The "don't fix anything" instruction is critical. Apply fixes via a separate session after diagnosis.
- **For code-quality questions.** Use `/simplify` instead — it's the dedicated multi-batch review pattern.

## Adding a new template

When a new bug class shows up, write a new template here. Convention:
- Always 3-4 numbered raw dumps
- Always close with "tell me your read in one paragraph"
- Always end with "Don't fix anything; just diagnose"
- Always specify file paths verbatim — the model is bad at guessing where things live

---

End of runtime debugging runbook. v2.14.19+.
