# ⛔ STOP CONTRACT (v3.5.0+ canonical location; was inlined in each orchestrator pre-v3.5.0)

**Every widget orchestrator (commitments, inbox, past-meetings, upcoming-meetings, pulse) MUST read + obey this file as the first action of every fire.** Morning-brief is the markdown-mode exception — it carries an adapted version inline because its surface isn't a widget.

This file consolidates prose that was duplicated across 5 orchestrators pre-v3.5.0 — when v2.14.x extensions landed, the same block had to be patched 5x and drift was inevitable. Extracted here so future amendments edit one file.

---

## The contract

**The widget IS the chat turn. After `mcp__visualize__show_widget` posts (plus post-widget Briefs / Sources sections, where applicable), YOU STOP. No exceptions, no edge cases.**

This applies to FIRST FIRES and to RE-RUNS ("regenerate with real data" / "show me populated" / "ignoring prior surfacing" / any similar phrasing). Agent freelancing on re-run is the dominant bug class — this block exists to stop it.

### Forbidden — zero tolerance

1. **No HAND-writing the widget HTML or any rendered chat output to disk.** Not to `_hq/scheduled_outputs/`, not to `_hq/insights/`, not to `_hq/staging/`, not anywhere. The widget surface is `show_widget` ONLY. (The sanctioned disk writes are the ones the transport chain performs ITSELF, both under `_hq/.system/widgets/`: `widget_transport.render_and_persist`'s rendered page — the validation gate + audit trail, with `transport["html"]` as the deliverable relayed as `widget_code`, T2 — and `page_snapshot.save_pageset`'s page-set JSON under `pagesets/`, which is the frozen view pages 2+ slice so `show more` never re-reads live substrate (PAGESNAP). Neither is ever written by hand, and neither is ever offered to the user as a deliverable.)

2. **No narrating what's in the widget.** Phrases like `Regenerated with N items`, `What's in the widget above`, `Total scan results: X persons flagged`, `Files saved to _hq/...`, `Saved the standalone HTML at...`, `5 actionable items` are FORBIDDEN. The user can see the widget. Explaining it duplicates the surface.

3. **No post-widget summary block.** The chat turn ends after the widget + Briefs/Sources (where applicable). Anything after that is forbidden.

4. **The directory `_hq/scheduled_outputs/` does not exist in the spec.** Do NOT create it. Do NOT save anything to it.

5. **Re-runs use the same code path.** When the user asks `regenerate with real events`, `show me with populated data`, `re-fire`, `run it again with X` — re-execute Phase 1 onward through the SAME pipeline (renderer → `show_widget`). Do NOT switch to file-write mode. Do NOT save intermediate outputs.

6. **Do NOT improvise a "save the output so the user can reopen it later" mode.** The widget is live in chat history. Saving rendered HTML to disk does NOT improve UX — the saved HTML's buttons aren't wired to Cowork's `sendPrompt`, so the artifact is dead on click.

7. **No skipping `show_widget` after a clean transport call (transport-updated T2).** If `widget_transport.render_and_persist()` returns without raising (it runs the full validator chain internally), you MUST call `mcp__visualize__show_widget` with `transport["html"]` as `widget_code` (the persisted page's validated bytes, verbatim). Narrating that the widget "couldn't transmit," "hit a session payload limit," "exceeded the live widget surface," "was too large," "render validated but..." or any other reason is FORBIDDEN — none of those conditions exist on this path, they are pure agent improvisation, and pagination (`page=N`, ~10 rows) keeps every page inside the relay budget. The clean transport call IS the contract — the page ships. If `show_widget` itself errors or is unavailable, SAY SO in plain English and STOP: surface the error string verbatim. Do not paraphrase, do not improvise a hand-built or compact widget, do not "summarize what the widget would have shown," do not chat-list the items as a substitute (the FS-08 silent-improvisation failure).

8. **No markdown lists as a substitute for widget rendering (v2.14.37+).** If a user follow-up asks you to "surface past commitments" / "show what's open" / "list the X" — any kind of "render these items in chat" ask — the path is `render_and_persist` → `show_widget` with `transport["html"]` as `widget_code`. Emitting a markdown bullet list of items in chat is FORBIDDEN, even when the prior widget was empty-state, even when the user explicitly asked for "a list," even when you think markdown is "lighter weight." Re-fire through the canonical path with the appropriate `data_view` (e.g., adjust filter threshold to surface previously-noise-filtered items as `tracked_items`).

### Self-check before posting anything

If you're about to write text in chat that comes AFTER a `show_widget` call, ask yourself: "is this required by the post-widget Briefs/Sources spec?" If no → DO NOT POST IT. Stop. The chat turn is over.

---

## What's NOT in scope of this contract

This file covers the **post-widget output surface** rules — what can / cannot follow `show_widget`. It does NOT cover:

- The ZERO-MANIPULATION CONTRACT for the renderer output bytes (lives in each orchestrator's Phase 9 / equivalent — that's about what happens BETWEEN the `render_and_persist` transport call and `show_widget`).
- The canonical-action validator rules (lives in `chat_output_renderer.py::CANONICAL_ACTIONS`).
- The leak-scanner pattern list (lives in `chat_output_renderer.py::_LEAK_PATTERNS`).

If a future amendment needs to consolidate those too, extract here in a sibling section and update each orchestrator to reference the new sections.
