---
name: email-writer
surfaces: both
description: "Draft emails in the CEO's voice — short external, long external, internal, cold outreach, and intro emails. Fires on: 'draft an email to [name] about [topic]', 'email to [name]', 'write an email to the team', 'reply to [name]', 'follow up with [name] about [topic]' (single named follow-up), plus 'tune email-writer'. Reads the calibrated voice block and relationship context; output lands as a draft in the declared mail backend or a preview per the draft-posture setting — never auto-sent. Does NOT fire on 'follow up on that call' / 'draft follow-ups' (follow-up-ritual — per-attendee pack from a transcript), 'who should I reach out to' (relationship-moves), or intro requests between two contacts (intro-broker). Register table and full trigger family: Routing section in the body."

voice_block_last_refreshed: 2026-04-21
calibration_level: default
template_version: 1.0.0
---

**Customer voice-block override (B1):** before drafting, read `_hq/voice/voice-block-email-writer.md` if it exists — it supersedes this SKILL.md's `## Voice Block` section-by-section (override sections replace same-named defaults; absent sections fall through). The universal banned-phrase list still applies except where the override's Taboos explicitly carve out an item. Staleness reads the override's `Last refreshed:` first.

# Email Writer

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

Before resolving the recipient(s) from the trigger phrase, you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)`. Multi-candidate results MUST surface a disambiguation widget — do NOT silently pick the first match. Only after `resolve_all` returns no candidates may you fall back to grep or to asking the user, and that fallback MUST be flagged. For thread / recent-context lookup, use `shared/scripts/cru_match.py::load_open_commitments` to ground the substrate-aware agenda — do NOT hand-roll an events.jsonl scan — and pass it the org-scoped rows (`load_open_commitments(events_path, events=org_events)` where `org_events` comes from `events_io.load_events_org_scoped`; PGUARD2 D2 — drafts must not see personal-lane or masked-account commitments), then keep the confirmed half via `cru_match.split_pending_review(...)` (INTAKE — the org scope is not the pending filter; see the Reads bullet below). See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract. Once resolved, the greeting and every rendered mention of the recipient use the record's `canonical_name` spelling (or its nickname field for warm registers) — never a transcript/ASR or email-display-name spelling (F-50 P2b; protocol § Display names).

**For:** CEOs who send 20-50 emails a day and want a tool that produces drafts sounding exactly like them — not generic professional prose. Works on day 1 with a default voice; upgrades to calibrated voice via the Chalette customization service.

**Primary domain:** email. This skill is NOT for Slack, NOT for memos, NOT for board updates. Use the matching domain-specific skill for those.

## Skill Boundary (v2.1)

- **Use `email-writer` for:** any standalone email draft — external short, external long, internal, cold outreach, intro/connector.
- **Use `follow-up-ritual` for:** post-meeting follow-up emails specifically. That skill handles the full follow-up pack (summary + per-attendee action items + follow-up drafts) from a meeting transcript.
- **Use `inbox-triage` for:** processing overnight email with classification + selective replies.
- **Use `memo-writer` for:** internal memos, decision docs, scope docs.

## Chained invocation (invoked as a sub-step — SPEC EW1, hardened SPEC EW2+T)

Other skills and mid-task turns hand email drafts off to THIS skill instead of composing email text themselves (CONTRACT Rule 30; `shared/EMAIL_DRAFT_PROTOCOL.md` scope — thread-resurrection's chained revival draft is the original pattern). When invoked that way:

- **Skip trigger parsing** (Phase 1's input shapes) — the handoff carries recipient + topic + any source context (thread, meeting, document) instead of a trigger phrase.
- **Everything else follows the normal flow:** entity-resolve on the recipient (the mandate above), the voice block + customer override (B1), recipient context load (Phase 2), the two-pass critique + voice-tell gate (Phase 3), subject synthesis (Phase 3.5), and the editable-widget render per EMAIL_DRAFT_PROTOCOL (Phase 4 — lazy creation; event writes fire from apply-choices as usual).
- The chaining turn does not re-render, trim, or edit the draft after this skill returns — the widget IS the surface, and the user's click owns every state change.

**The chained back half is where live fires freelance (EW2+T — F-07/F-08: delegation landed 3/3, the protocol below held only 1/3). These four rules bind a chained fire exactly as hard as a direct one:**

1. **MUST render the editable widget** — via the Phase 4 transport (`widget_transport.render_and_persist` → `show_widget` (`transport["html"]` as `widget_code`)). The widget is the ONLY draft surface. Inline chat text, a markdown preview, a blockquoted "here's the draft:" — none of those is a render. If you have draft text on screen and no widget, you are mid-violation (this was 2 of 3 live fires).
2. **MUST NOT create any connector draft, send, or queue anything pre-click.** The MUST-language preamble below binds chained fires verbatim — "I'll just queue it to the mail backend's Drafts so it's ready" is the exact live failure (an eager Superhuman queue with no widget and no event). Nothing touches the mail backend until the user's widget click dispatches through apply-choices.
3. **Event writes stay at apply-choices** (the lazy contract, unchanged): chained invocation changes the ENTRY point, never the exit. No `email_drafted` append from this skill's own path — the click owns it.
4. **No-address recovery — never a guess (Bug #44 verb, pinned here after F-08).** When the resolved recipient has no actionable email on file, render the widget anyway with the `add email then send` recovery verb and an empty To:. **Address inference is BANNED:** never derive a recipient address from the org's email-address pattern, a coworker's address shape, a domain convention, or any "most likely" construction — not even presented as a suggestion behind a confirm. The one live guess gated behind a confirm and was still WRONG; a plausible wrong address is worse than an empty field, because the user rubber-stamps plausible. Investigate context, say plainly no address is on file, offer the recovery paths — that observed behavior is the contract.

## Writer Contract

Before writing, read `shared/WORKSPACE_API.md`.

**Primary writer for:**
- **The declared mail backend's Drafts (no file saved, click-gated).** Per CONTRACT Rule 27 (no .md deliverables) there is no draft file; and per the v3.13.7 lazy model the connector draft itself is created ONLY by the user's widget click, dispatched from `apply-choices` — the draft-create tool resolved through the seam (`tool_discovery.discover_for_category("email", …, declared=connector_config.declared_backend("email"))` → `discover_mail_draft_tool` fallback); sends dispatch per `shared/EMAIL_DRAFT_PROTOCOL.md` §0.5 (the Zapier reply-thread mechanism in `shared/scripts/zapier_send.py` is the gmail-only dispatch row when the native connector can't send). This skill's own path writes nothing to the mail backend — it renders the widget. The pre-v3.7.0 saved-draft-file pattern was vestigial; the deliverable is the draft the user's click puts in the connected mail client.
- `_hq/voice/corrections-email-writer.jsonl` — append on detected correction.

**Appends to:**
- `_hq/data/events.jsonl` — event type `email_drafted` with `recipient`, `topic`, the draft-identity fields from `connector_adapters.provenance.build_email_drafted_provenance(draft_id=…, provider=…, server_id=…, address=…)` (EW2+T F-12 / FB-plumbing item 5 — writes the `native_draft_id` field carrying the DECLARED BACKEND's native draft id, plus the structured `provenance` block; never hand-write the field name; readers use `native_draft_id_from_data`, which still accepts the legacy `gmail_draft_id` spelling on pre-rename events), `commitment_refs[]` (open commitments with recipient surfaced into the draft), `decision_refs[]` (decisions involving recipient that informed the draft). The substrate knows the draft exists and downstream skills (`insight-generator`, `cleanup`) can detect drafted-but-not-sent — on every backend, not just Gmail.
- `_hq/data/events.jsonl` — event type `email_sent` written when the user clicks Send via the widget action set (or the user marks the connector draft as sent and a future fire detects it). Carries `{recipient, topic, draft_event_seq}` PLUS the send-identity fields from `connector_adapters.provenance.build_email_sent_provenance(message_id=…, thread_id=…, provider=…, server_id=…, address=…)` — the builder dual-writes the legacy per-provider id fields (reader back-compat: email_outcomes, reconcile-sent, voice-corrections all read them today) AND the structured `provenance` block (R3 account scoping + format-proof dedup). Never hand-write the id field names; the builder owns the spelling. Links back to the original `email_drafted` event. This is the v3.7.1+ closure event — pre-v3.7.1 the substrate saw drafts but never confirmation of sends, so the drafted-but-not-sent gap couldn't be measured.

**Reads (v3.7.1+ substrate enrichment):** All `events.jsonl` reads below go through ONE org-scoped load — **read via the org-scoped reader, never a raw load** (PGUARD2 — the draft reaches a recipient, an external surface): `from events_io import load_events_org_scoped; org_events, skipped = load_events_org_scoped(workspace_root)`, then filter by `type` at the call site. The reader applies the account-scope mask and drops personal-lane rows by design, so a reclassified personal account's history never enters the draft prompt.
- `_hq/data/entities.json` — for recipient context (org, role, relationship strength).
- `_hq/data/events.jsonl` for prior interaction history with recipient (same as before) — from the org-scoped load, `type == "interaction"`.
- `_hq/data/events.jsonl` `type == "commitment"` events involving recipient with `status == "open"` — filter the org-scoped load, or pass it through the seam: `load_open_commitments(events_path, events=org_events)` (PGUARD2 D2 — the injection keeps personal-tie commitments out of the draft; never call the no-arg owner form here). **`data.pending_review` is NOT true — those are UNCONFIRMED extractions, not open commitments.** Use `cru_match.split_pending_review(...)` and keep the confirmed half. A pending row woven into outbound mail is the workspace asserting a promise nobody made, to the one person who would know it is wrong; they never enter the draft prompt and never land in `commitment_refs[]`. They belong to the needs-your-call queue (`needs your call`). The confirmed half gets surfaced into the draft prompt as "open commitments with this recipient" so the draft can naturally address them. Resolved seqs from that half go into `commitment_refs[]` on the emitted `email_drafted` event.
- `_hq/data/events.jsonl` `type == "decision"` events that name the recipient or their org in `data.context` / `data.affected_entities` — from the same org-scoped load — pulled into the draft prompt as "what we've decided about them" so the draft is consistent with prior positions. Seqs go into `decision_refs[]`.
- This skill's Voice Block (below).
- `shared/VOICE_CALIBRATION.md` — universal protocol.

**Never writes to:**
- This SKILL.md's own Voice Block (only `insight-generator` or Chalette refresh does that).
- Plugin source files outside this skill's folder.

---

## What It Does

Takes a prompt like "draft an email to Sam about the LOI we discussed" and produces a ready-to-send draft that sounds like the CEO wrote it. The draft surfaces as an editable widget — NOTHING is saved anywhere (no file, no connector draft) until the user clicks an action; the click is what creates the draft in the declared mail backend or sends it (v3.13.7 lazy model).

## How It Works

### MUST-language preamble (v3.13.7+ — enforcement gate)

Before doing anything in this skill, internalize this hard rule:

> **You MUST first render an editable widget. Then you stop and wait. The mail-backend tool call fires only from apply-choices on the user's `send` / `draft` click (or a deprecated in-flight `edit then send`) — never inline, never preemptively, never as part of generating the draft.**

Translation:
- `mcp__visualize__show_widget` is the FIRST end-user-visible side effect of this skill. The user sees the editable draft before anything changes in their mail client.
- No mail-backend draft-create / send / Zapier-send call inside this skill's main path. Those fire from `apply-choices` after the user clicks an action.
- "I'll just create the draft in their mail client so we have it in case the user wants it" is exactly the failure mode v3.13.7 ships to close. Resist it.

Why this is a hard rule (v3.13.6 → v3.13.7 trust-bomb fix): a paying customer asked "draft email to X" and saw a draft appear in their mail client without ever approving it. Loss-of-control feeling. Day-1 churn signal. The eager-draft model is reversed in v3.13.7 — same widget surface, lazy state change.

If anything below seems to contradict this preamble (older language, a screenshot, a habit from prior versions), the preamble wins.

### Phase 0 — First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`).
Always read config through `get_config` — never read the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "draft_posture": "show_first",   # AF — show_first | auto_queue
    "sign_off": "dash_first",        # STT — dash_first | thanks_first | first_only | custom
    "length": "short_direct",        # STT — short_direct | fuller
}
cfg = get_config(workspace_root, "email-writer", DEFAULTS)
```

`draft_posture` gates outbound behavior, so email-writer is an **ask-first (AF)** skill — the
one exception to output-first. **Both postures are QUEUE-ON-CLICK (FS-11, M ruling 2026-07-15):
the editable draft appears first and NOTHING touches the mail backend until the user clicks — a
draft M hasn't approved must not exist in the backend.** `show_first` (default) shows the draft
card with its actual controls — the **Send** / **Draft** / **Snooze (3 days)** one-tap buttons
(t3 FB-4, FB-17 — three primaries, no dropdown on the plain card) and the
directly-editable body (t3 FB-10 — click into it and type; no Edit button; FB-17 retired the
`Edit then send` popup form) — and the click
performs the write. Prose under the card names ONLY those visible controls, by those labels
(t3 FB-11 — a fire that says "pick Send, Edit then send, or Draft" over a card that shows
different chrome is the bug). `auto_queue` shows the same card with **Draft** as the emphasized
button, so a single tap queues it to the declared backend's Drafts. In BOTH cases the connector
write, the `email_drafted` event, AND the voice snapshot all fire **at that click**
(apply-choices Step 4B), never at render/compose. The pre-FS-11
"queue on render / auto-queue immediately" behavior is RETIRED — it contradicted the chained MUST-2
(zero pre-click connector writes) and stacked unreviewed drafts in the live dogfood.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "draft an email…" | first relevant request only: ask the ONE AF posture question (below) BEFORE drafting, then `save_skill_config(workspace_root, "email-writer", DEFAULTS)`; subsequent fires skip straight to drafting with the saved posture. |
| **Show settings** | "show email-writer settings" | render current config in plain English; no draft. |
| **Tune** | "tune my email drafts", "tune email-writer", "change my email draft posture" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → confirm. |
| **Reset** | "reset email-writer to defaults" | `wipe_skill_config(workspace_root, "email-writer")` → next draft is a first-fire again. |

**First fire — the ONE AF question (asked once, before the first draft, with a working default-escape):**

On the very first email-writer fire only (`not is_configured(workspace_root, "email-writer")`), before producing the draft, render a single fixed-option micro-widget:

> *First time drafting with me. How do you want me to handle drafts?*
> **[Show me first ▸ recommended]** — I'll show you the draft here; nothing touches your mail until you tap Send or Draft.
> **[One-click to `[drafts-name]`]** — same preview, but Draft is the emphasized button, so one tap drops it into `[drafts-name]`. Still nothing until you click.
> *Just go ahead and I'll use Show-me-first.*

**`[drafts-name]` resolves at render time — never hardcode a provider (§0.5 seam vocabulary).** Substitute the declared mail backend's name before rendering: from the `connector_config.declared_backend("email")` row, `label` → falling back to `provider` title-cased when `label` is unset (the row is written with `label: None` unless the operator named it) — + " Drafts" (a Gmail workspace sees "Gmail Drafts", a Superhuman workspace "Superhuman Drafts"). When no mail backend is declared (empty connectors map, R4 fallback) OR neither label nor provider yields a name, substitute the generic **"your mail drafts"**. The literal token `[drafts-name]` must never reach the user (plain-English rule, EMAIL_DRAFT_PROTOCOL §0), and neither must a raw `None`.

This is the documented current-state fixed-option row (`shared/CHAT_ACTION_WIDGET.md` preselect
exception). The default-escape means proceeding without answering applies `show_first`. After the
choice (or skip): `save_skill_config(workspace_root, "email-writer", {**DEFAULTS, "draft_posture": choice})`
with `origin="first_fire_override"` if they changed it, else plain DEFAULTS. Then continue to Phase 1.
sign-off and length are NOT asked at first run (one AF question max; email-writer ends in a draft
widget so a trailing footer would violate `CHAT_ACTION_WIDGET.md` MUST-NOT rule 5) — they are
discoverable via `tune email-writer` and the freeform table. The block renders exactly once ever.

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "auto-queue my drafts" / "put drafts straight in Gmail" | `draft_posture = auto_queue` |
| "always show me first" / "stop auto-queueing" | `draft_posture = show_first` |
| "sign off with just my first name" | `sign_off = first_only` |
| "use Thanks, [name]" | `sign_off = thanks_first` |
| "make my emails fuller" / "less terse" | `length = fuller` |
| "keep emails short and direct" | `length = short_direct` |

After applying: `save_skill_config(..., is_reconfigure=True)` + confirm in one line ("Done — drafts auto-queue to `[drafts-name]` now." — same render-time substitution as the first-fire copy above). `sign_off` and `length` shape Phase 3 (the Voice Block sign-off tier + target length); `draft_posture` gates Phase 4 (when `auto_queue`, apply-choices also creates the connector draft on render — still never *sends* without a click).

### Phase 1 — Input parsing

Accept one of these input shapes:

1. **Full instruction:** "Draft an email to Sam about pushing the LOI meeting to next Tuesday, keep it short."
2. **Recipient + topic:** "Email Sam about the LOI timeline."
3. **Reply context:** "Reply to [thread_id or subject] saying we're in for the Tuesday slot."
4. **Cold outreach:** "Cold email to [prospect] at [company] about [offer]."
5. **Intro/connector:** "Intro email connecting [person A] and [person B]."
6. **Multi-draft / multi-stage (v3.14.3+):** the upstream chat turn scaffolded multiple emails as options ("draft a stage 1 and a stage 2", "send these emails", with-intro vs without-intro variants, two recipients in parallel). Detect via plural noun (`emails`), explicit stage/version language, or a chat scaffold that already enumerated 2+ drafts. Render ALL drafts in a single n>1 widget per Phase 4 multi-draft mode — do NOT make the user type "send stage 2" to pick one.

Disambiguate ambiguous recipients via `aliases.json`. If multiple matches ("which Bowie — the customer or the advisor?"), ask ONE question.

### Phase 2 — Recipient context load

Read from `entities.json`:
- Recipient's role, org, affiliation
- Prior interaction history from `events.jsonl` (last 5 exchanges) — from the Reads section's org-scoped load (`load_events_org_scoped`), never a raw read
- Any `communication_style` field on their person record (formal / casual / terse)
- Recent decisions or commitments involving them

This context shapes register adjustments within the Voice Block's guardrails:
- Board-level recipient → tighter, more formal
- Long-standing peer → looser, more abbreviated
- First-time cold contact → more context, less shorthand

**No email on file → recover, never infer (EW2+T, F-08 — main path and chained path alike).** If the resolved person record carries no actionable email address, do NOT construct one — not from the org's address pattern, not from a coworker's format, not from a domain convention, and never as a "most likely" suggestion. Proceed to Phase 4 with an empty To: and the `add email then send` recovery verb (Bug #44); the user supplies the address, and the capture persists it for next time.

### Phase 3 — Voice-calibrated draft (two-step protocol)

#### Step 1 — Draft

Using the Voice Block + recipient context + banned-phrase list from `shared/VOICE_CALIBRATION.md`, produce the first draft.

Target length:
- **Short external:** 40-80 words, 2-3 short paragraphs max.
- **Long external:** 100-250 words, 3-5 paragraphs.
- **Internal:** 20-60 words. Terser than external.
- **Cold outreach:** 80-120 words. Must open with specific reason, not generic intro.
- **Intro/connector:** 60-100 words. 2-3 sentences per person. Clear ask.

#### Step 2 — Critique (two-pass)

**Learned chase policy (Phase 6 Loop 6).** For a follow-up / check-in draft on a thread that's gone quiet, consult the per-relationship-type chase policy insight-generator learned from outcomes: `from chase_policy import load_chase_policy, get_chase_window` → `chase_days, escalate = get_chase_window(policy, <recipient's org relationship_type>)`. Use it to tune the follow-up's timing cue and escalation: after `escalate` silent chases to a relationship class that historically goes quiet, the draft shifts from "just checking in" to proposing a short call (a `follow-up call` cue) rather than another email. Missing store → the default `(7, 3)`, so a fresh workspace drafts exactly as before. This shapes tone/timing only; the draft still never sends without a click.

Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md` (structure, specificity, floors — they do not override this skill's Voice Block on voice/tone/openers/taboos). Then run two passes in order.

**Pass 1 — structure (binary):**
- Opening sentence states the action / decision / info. If deleting it loses nothing, delete it.
- No sentence over 25 words. Break any that run longer.
- Banned-phrase sweep with named replacements (strip every hit from the universal list — no closer at all beats "hope this helps").

**Pass 2 — register (run only after Pass 1 is clean):**
- Peer = contractions and shorthand; warm it if it reads like a memo.
- Senior / customer = strip familiarity ("just wanted to check in" fails).
- Sign-off matches the tier.

Rewrite what fails each pass, re-check once, then return.

**Mechanical voice-tell gate (B2 — bash-gated, not prose).** After the two passes above, run the draft body through the deterministic detector. It hard-fails on the exact banned phrases in `shared/VOICE_CALIBRATION.md`; structural tells (em-dash pile-ups, tri-colons, hedging stacks, bullets-in-email) warn. This is enforced, not advisory — it does not replace the binary checklist, it backstops it:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
printf '%s' "$DRAFT_BODY" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context email
```

On exit 1 (`FAIL`), rewrite the flagged lines and re-run until the detector exits 0 (`pass` or `warn`). Never return a draft the detector still fails. A phrase the recipient's calibrated Voice Block demonstrably allows is exempt — feed it through `allow_phrases` (the Voice Block Taboos carve-out per `shared/VOICE_CALIBRATION.md`), never improvise the override.

**Turn-level backstop (SPEC GATE1, v3.20.x — this is enforced even if you skip the Step-2 call above).** Email drafts never reach `brief_writer.make_brief` (no .docx is saved), so the email surface relies on this Step-2 gate — which v3.20.0 verification showed the LLM treats as optional and skips. The deterministic backstop closes that hole: when the draft is rendered through `render_chat_output_widget` (Phase 4), `turn_backstop.scan_data_view_for_tells` automatically scans every email-shaped body for banned voice tells and emits a `gate_ran` (`surface="chat_email"`) audit event on any fail. It is NON-BLOCKING (the widget still renders) but a `gate_ran` with `result="fail"` is a detectable signal that a banned phrase shipped. Running the Step-2 detector above is still mandatory — the backstop is the safety net, not a substitute. Do NOT hand-type the email body straight into chat without rendering the widget; that bypasses both the gate AND the backstop.

### Phase 3.5 — Subject synthesis (v2.10.8+)

**Never ship a bare `Re:` subject.** <!-- provider-note-ok --> Gmail's threading algorithm falls back from `In-Reply-To` / `References` headers to subject-normalization in some workspace configurations; if the normalized subject is empty (bare `Re:` with nothing after it), Gmail assigns a fresh `threadId` to the outbound, splitting the thread. The recipient sees a new conversation; the chain breaks. Empirically verified Apr 29 — Sam Sample's whole correspondence chain has bare-`Re:` subjects, and M's reply landed in a sibling thread. (This paragraph is rationale prose — a documented provider quirk, not tool routing; the gate-1 scan allow-lists it via the marker.)

**Rule for replies:**

1. If the inbound thread's subject is non-empty (contains text after any `Re:`/`Fwd:` prefix), prepend `Re: ` to that subject and use it. Done.
2. If the inbound subject is empty OR is exactly `Re:` / `Re: ` / `Fwd:` / `Fwd: ` (bare prefix, no original subject text):
   - Walk back through prior messages in the thread looking for any non-empty subject. If found, use `Re: <recovered subject>`.
   - If all prior subjects are also empty, **synthesize one from the first non-greeting line of the inbound body** (5-8 words, sentence-cased, descriptive of the message's actual topic). Examples: `Re: Renderer pipeline diagnostic dump`, `Re: Q2 deck timing`, `Re: NetSuite mapping handoff`. Never synthesize from generic phrases ("Hey," "Hi Aria,"); skip greetings.
   - If body is too thin to synthesize from (one-liner, attachment-only, etc.), use `Re: Following up` as a last-resort fallback. Better than bare `Re:`.

This rule applies to all reply paths — `N send`, `N draft`, on-demand reply drafts. Synthesizing a subject ALSO improves Gmail's threading reliability for any send path that doesn't set `In-Reply-To` (the §3a fallback case in EMAIL_DRAFT_PROTOCOL), because the synthesized subject gives Gmail's subject-normalization fallback something to anchor to.

**DRAFTTHREAD1 — reply drafts are never patched in place (`shared/EMAIL_DRAFT_PROTOCOL.md` §3d, mandatory on every reply path this skill owns).** Any content change to a reply draft — a voice correction, a re-edit after the widget click already created the connector draft, anything — is a FRESH create carrying the reply-to reference, never the draft-update operation: that operation has no reply-to parameter, so it rebuilds the message without its threading headers and the backend silently moves the draft to a new thread while reporting success. After every reply-draft create, assert the threading took via `shared/scripts/draft_threading.py::assert_reply_threaded(created_draft, <conversation thread id>)` and surface a detachment loudly (both ids). A re-create supersedes the earlier connector draft — name it (subject + recipient) for hand deletion; the connector exposes no delete-draft tool.

**Subject voice gate (v4.6.1 S3 — MANDATORY, bash-gated like the body gate).** Every subject — synthesized here, fresh-composed, or carried through on a new outbound — runs the subject gate before the draft renders:

```bash
printf '%s' "$SUBJECT" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context subject
```

It hard-fails on any dash used as punctuation (em dash, en dash, spaced hyphen — the BRAND_VOICE hard rule the body gate never applied to subjects: F-47 P2d and F-53 shipped em-dash subjects twice in one day), and on the banned phrases + shared vocabulary words. On exit 1, rewrite the subject (comma, colon, or reword — "Q2 deck — status" becomes "Q2 deck: status") and re-run until it exits 0. `Re: <inbound subject>` replies are exempt from the dash rule ONLY for the carried-through inbound portion — never edit the recipient's own subject text, and never let the gate force a thread-splitting rewrite of a reply subject; when the inbound subject itself contains a dash, keep it verbatim and skip the gate for that reply.

The Zapier-threaded send path (§3c, v2.10.7+) sets `In-Reply-To` and `References` headers and is unaffected by subject — but until every workspace wires the Zap, subject synthesis prevents the thread split at the source for ALL paths.

### Phase 4 — Render the editable widget (lazy — NO mail-backend state change here)

**v3.13.7+ — Approval-gate enforcement.** The draft surfaces as an editable widget. NO mail-backend tool call fires in this phase. The user reviews the widget; their click is what creates the connector draft (or sends it). Mirrors the v3.13.0 calendar-writer approval-gate pattern. **Chained invocations run this exact phase** — a draft that arrived as a sub-step of a bigger task renders the same widget through the same transport (EW2+T).

This phase is render-only:

1. **No mail-backend call.** Do NOT call the connector's send tool, its draft-create tool, Zapier-send, or any other mail tool here. The draft body lives in memory until the user clicks an action.
2. **Build the data_view** in the shape below, passing the draft body lines + To / Subject metadata you've assembled in Phases 2-3.5.
3. **Render + persist** via the widget_code transport (EW2+T, F-15): `widget_transport.render_and_persist(data_view=…, wrapper="fragment", persist_dir=<WORKSPACE>/_hq/.system/widgets, name_hint="email-writer")` — all validators fire inside the call.
4. **Post** by handing `transport["html"]` to `mcp__visualize__show_widget`. Never hand-compose or post-process the HTML; never post-process `transport["html"]` (`shared/CHAT_ACTION_WIDGET.md` § Transport).
5. **Stop and wait.** This skill's main path ends with the widget on screen. The user's click hands off to `apply-choices`, which fires the matching seam-resolved mail tool lazily per the action semantics below.

This matches the contract scheduled tasks use for email drafts (per `shared/EMAIL_DRAFT_PROTOCOL.md` §1 — lazy draft creation) and means downstream skills that chain through email-writer (intro-broker, follow-up-ritual, thread-resurrection, inbox-triage's reply paths) inherit the editable-widget + approval-gate surface for free.

Pre-v3.13.0, email-writer surfaced drafts as a text block in chat — forcing back-and-forth chat-turn edits to revise the body. v3.13.0 added the widget but kept eager connector-draft creation as a back-compat carryover. v3.13.7 reverses the eager model: the widget is now the only end-user-visible side effect; mail-backend writes are click-gated through apply-choices. Same widget surface, lazy state change.

**Mode selection (n=1 vs n>1, v3.14.3+ — surfaced 2026-05-26 from a multi-stage outreach use case):**

If Phase 1 detected a multi-draft scenario (input shape #6 — multi-stage / multi-variant / "send these emails"), produce ONE widget with N items, each carrying its own `"N send"` / `"N draft"` / `"N snooze 3d"` actions (FB-17 — the Send / Draft / Snooze card; `edit then send` retired, the inline body is the edit surface). This is the n>1 batched-widget surface per `shared/EMAIL_DRAFT_PROTOCOL.md` §6.5 Path A — the same surface intro-broker uses for its two-style draft pick.

DO NOT render one widget per draft (forces the user through sequential confirms). DO NOT collapse multiple drafts into a single item with text like "Draft 1 / Draft 2 below" (forces the user to type which one to send). The whole point of the widget is per-draft visual choice + per-draft action — render every draft as its own item and let the user click `send` on the ones they want, `skip` on the ones they don't.

Voice + body construction (Phases 2-3.5) runs independently per draft. Subject synthesis (Phase 3.5) runs per draft. The widget collects all of them at the end.

**Data view shape (n=1 — single-draft all_batch_widget):**

```python
data_view = {
    "widget_mode": "all_batch_widget",
    "header": "Draft ready for {recipient_short_name}",
    "sub_header": "Review, edit, or send when you're ready.",
    "sections": [{
        "title": None,
        "count": None,
        "items": [{
            "n": 1,
            "icon": "✉️",
            "name": recipient_display_name,
            # metadata is a LIST OF [key, value] PAIRS (not a dict) — this is the
            # email-shaped item shape the data-shape validator recognizes (per
            # chat_output_renderer._is_email_shaped). At minimum populate To +
            # Subject; Cc optional. Without this shape, edit-then-send opens
            # with blank fields.
            "metadata": [
                ["To", recipient_email],
                ["Subject", subject],
            ],
            "context_tag": "Ready for your review — nothing is saved to your mail until you click",
            # v3.13.8+ — Bug #30 fix. Only prefix with `>` when this is a
            # REPLY to an existing thread (i.e. you have a Gmail thread_id
            # in scope). Fresh-draft bodies render WITHOUT the blockquote
            # prefix — pre-v3.13.8 emitted `> Hi Sam — ...` on every body,
            # which read as "quoting myself" and broke voice calibration.
            "body_lines": (
                [f"> {line}" for line in body_paragraphs]
                if in_reply_to_thread_id else list(body_paragraphs)
            ),
            "actions": ["1 send", "1 draft", "1 snooze 3d"],
        }],
    }],
}
```

**Data view shape (n>1 — multi-draft all_batch_widget, v3.14.3+):**

When Phase 1 detected multi-draft (shape #6), emit one item per draft, each numbered, each with its own canonical action set. The header summarizes the choice ("Two drafts — pick the ones to send"); each item's `name` labels what the draft is for; `context_tag` says the one-line "why this draft" so the user can choose without reading bodies in full.

```python
data_view = {
    "widget_mode": "all_batch_widget",
    "header": f"{len(drafts)} drafts ready",
    "sub_header": "Send the ones you want, skip the rest, or edit any of them.",
    "sections": [{
        "title": None,
        "count": None,
        "items": [
            {
                "n": i + 1,
                "icon": "✉️",
                "name": draft["label"],  # e.g. "Stage 1 — ask Rio about Mira"
                "metadata": [
                    ["To", draft["recipient_email"]],
                    ["Subject", draft["subject"]],
                ],
                "context_tag": draft["why"],  # e.g. "Asks Rio to confirm before the loop-in"
                "body_lines": (
                    [f"> {line}" for line in draft["body_paragraphs"]]
                    if draft.get("in_reply_to_thread_id") else list(draft["body_paragraphs"])
                ),
                "actions": [
                    f"{i + 1} send",
                    f"{i + 1} draft",
                    f"{i + 1} snooze 3d",
                ],
            }
            for i, draft in enumerate(drafts)
        ],
    }],
}
```

Each draft's `N send` lands in apply-choices the same way the n=1 case does — the per-item action verb is canonical lowercase; apply-choices dispatches through the seam-resolved backend per `EMAIL_DRAFT_PROTOCOL.md` §0.5 (the §3c Zapier ladder applies only on a Gmail backend). One `email_drafted` + one `email_sent` event is appended per draft the user actually sends — on every backend (EW2+T, F-12). Skipped drafts get a `chat_dismissal` event each.

**Render + post:**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from widget_transport import render_and_persist
data_view = json.loads('''<DATA_VIEW_JSON>''')
transport = render_and_persist(data_view=data_view, wrapper='fragment',
                               persist_dir='<WORKSPACE>/_hq/.system/widgets',
                               name_hint='email-writer')
print(transport['html'])
"
# Pass transport["html"] (the persisted page's validated bytes, verbatim) to
# mcp__visualize__show_widget as widget_code (T2 — shared/CHAT_ACTION_WIDGET.md
# § Transport). Never hand-compose or post-process the HTML.
```

**Action semantics (handled by apply-choices — mail-backend tool calls fire HERE, not in Phase 4):**

All three actions land in apply-choices. The seam-resolved mail tool (or the §3c Zapier row on a Gmail backend) is the FIRST place the mail client's state changes. Email-writer's main path produced only a rendered widget; nothing exists in the mail backend yet.

- `1 send` — apply-choices creates the connector draft AND sends it in one motion. Native path: the seam-resolved draft-create tool → the seam-resolved send tool (dispatch per `EMAIL_DRAFT_PROTOCOL.md` §0.5; a declared backend with no send capability degrades per §0.5 pt2). Zapier-threaded send path: `zapier_send.py` with `In-Reply-To` if known (gmail-only dispatch row, §3c). Logs `email_drafted` AND `email_sent` events (the draft is recorded for the substrate even though there's no human-visible draft state between create and send). Body edits happen directly on the card before Apply (FB-10 inline body; the FB-17-retired `edit then send` still dispatches from in-flight widgets as an alias → send, its edited payload honored).
- `1 draft` — apply-choices creates a connector draft via the seam-resolved draft-create tool. (There is NO Zapier draft path — `zapier_send.py` only sends; with no native mail connector the body stays in the widget and the ack says email isn't connected.) The draft now lives in the declared backend's Drafts for the user to find and send later. Logs `email_drafted` (no `email_sent` yet). Per v2.14.4 canonical-action consolidation, `to drafts` was renamed to `draft` — the action always opens an edit field before saving, so review-then-save is the default semantics.
- `1 snooze 3d` — no mail-backend tool call (because no draft was ever created). Records a `chat_dismissal` event with a 3-day mute so the card resurfaces later (FB-17 — "deal with it later").

**Undo-send (A6, feature-detected):** when the declared backend's capability manifest has `undo_send` (Superhuman-class — `connector_adapters.capabilities.supports(provider, "undo_send")`), the send acknowledgment adds one plain-English line: *"Sent. Say `undo` in the next minute to pull it back."* — and `undo` within the window fires the connector's undo tool and logs the reversal on the `email_sent` event (`data.undone: true`). Capability absent → the line simply doesn't render (silent skip); never promise an undo the backend can't do.

In the n>1 multi-draft case, the same three verbs apply per item (`N send`, `N draft`, `N snooze 3d`). The user can mix freely — e.g., `1 send 2 snooze 3d` ships draft 1, defers draft 2; edit any draft inline (FB-10) before sending. Apply-choices iterates the action set and fires the matching Gmail tool once per `N send` / `N draft` action.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "I render the draft; the Gmail draft fires on click."
- Good: "I'll show you the draft here; nothing touches your mail until you click Send or Save draft."

**Why lazy beats eager (v3.13.7 trust-bomb fix):** under the pre-v3.13.7 eager model, clicking `1 skip` still left a draft in the user's Gmail because the draft was created in Phase 4 before the user ever saw the widget. Same with the user closing the chat without clicking — silent draft persisted. Under the lazy model, skip and close both produce zero Gmail state change. The user has full control. The mental model "the widget is the draft; my click is what writes Gmail" matches reality.

### Phase 5 — Log `email_drafted` event (fires from apply-choices, not from this skill's main path)

**v3.13.7+ — relocated to apply-choices.** Under the lazy model, the Gmail draft only exists after the user clicks `send`, `edit then send`, or `draft`. The `email_drafted` event MUST be appended at that point — from `apply-choices`, not from email-writer's Phase 4. `source_skill` stays `"email-writer"` because email-writer authored the draft body; the write happens downstream.

Event shape (unchanged from v3.13.0):

```json
{
  "timestamp": "...",
  "type": "email_drafted",
  "source_skill": "email-writer",
  "primary_thread_id": "project_XXX",
  "person_ids": ["person_YYY"],
  "data": {
    "recipient": "...@example.com",
    "topic": "[topic]",
    ...connector_adapters.provenance.build_email_drafted_provenance(
        draft_id=<the connector's draft-create response id — Superhuman,
                  Gmail, or Outlook alike>,
        provider=<declared backend provider>, server_id=<its server-id>,
        address=<the drafting account address>),
    "commitment_refs": [<seqs of open commitments surfaced into the draft>],
    "decision_refs": [<seqs of decisions involving recipient that informed the draft>],
    "calibration_level": "default"
  }
}
```

The builder writes the draft identity (EW2+T, F-12; FB-plumbing item 5): the `native_draft_id` field (the VALUE is the declared backend's native draft id — the name is backend-neutral now, no longer the misleading `gmail_draft_id`) plus the structured `provenance` block. Never hand-write the field name; the builder owns the spelling, and readers go through `native_draft_id_from_data` (which still reads the legacy `gmail_draft_id` on pre-rename events — append-only history is never rewritten). The draft id is the canonical handle for the draft itself — Phase 6 reads it to detect "drafted but not sent" patterns. `commitment_refs[]` and `decision_refs[]` close the substrate loop: when the user later sends this email and the CRU layer scans for matching commitments, it has the explicit linkage instead of having to re-infer.

If the CEO edits the draft before sending (detected on next turn or via end-session reconciliation), append the correction to `_hq/voice/corrections-email-writer.jsonl` per the schema in `shared/VOICE_CALIBRATION.md`.

### Phase 6 — Log `email_sent` event (on send — fires from apply-choices)

Same relocation as Phase 5: when the user clicks `send` or `edit then send` in the widget, apply-choices creates the Gmail draft, sends it, and appends `email_sent` to `events.jsonl`. `source_skill` stays `"email-writer"` because email-writer authored the body; the send happens downstream. A later fire that detects a user-side send-from-Gmail-client also appends the same event shape.

```json
{
  "timestamp": "...",
  "type": "email_sent",
  "source_skill": "email-writer",
  "primary_thread_id": "project_XXX",
  "person_ids": ["person_YYY"],
  "data": {
    "recipient": "...@example.com",
    "topic": "[topic]",
    "draft_event_seq": <seq of the email_drafted event, read from the append RETURN value>,
    ...connector_adapters.provenance.build_email_sent_provenance(
        message_id=<the send response's message id>,
        thread_id=<the send response's thread id, when provided>,
        provider=<declared backend provider>, server_id=<its server-id>,
        address=<the sending account address>)
  }
}
```

The provenance builder DUAL-WRITES the send identity: the legacy per-provider id fields (reader back-compat — email_outcomes, reconcile-sent's thread fetch, and voice-corrections matching all key on them today) AND the structured `provenance` block (`{connector, provider, native_id, thread_native_id, account_id}` — R3 account scoping + format-proof dedup via the canonical key). Never hand-write the legacy field names in a skill; the builder owns the spelling (grep-gate 1).

`email_sent` fires regardless of dispatch path — the declared backend's native send, a native Outlook send, or the Zapier reply-thread send all converge here. `draft_event_seq` links back to the original `email_drafted` so the substrate has the full lifecycle. This is the v3.7.1+ closure event that lets `cleanup` count drafted-but-not-sent as a real signal instead of guessing. **B6:** when the send response includes a thread id, pass it to the builder — the outcome watch (reconcile-sent Step 6) uses it to look up replies directly; without it the watch falls back to a message-id-header lookup (the `message_id_lookup` intent, compiled per provider by `connector_adapters/mail.py`). (Zapier standalone sends often have neither — those age out of the 21-day outcome window, never guessed.)

---

## Voice Block

**Last refreshed:** 2026-04-21
**Calibration level:** default
**Sample count:** 0 (uncalibrated — generic professional defaults)

### Sentence cadence
- Typical length: 10-18 words
- Maximum before breaking: 25 words
- Short-punch frequency: occasional (1-2 per email)

### Openers
- Preferred: lead with purpose ("Quick note on X", "Following up on Y", "Two things:")
- Avoided: "I hope this finds you well", "I hope you're doing well", "I wanted to reach out"
- Never use: "Happy to help", "Great question", "I'd love to", "I wanted to circle back", "Just touching base"

### Vocabulary
- Uses: direct verbs (send, confirm, lock in, push to, flag)
- Avoids: "leverage", "synergies", "going forward", "touch base", "circle back", "reach out" (use "email" or "call" instead), "bandwidth"
- Domain-specific: none at default calibration

### Punctuation
- Em-dashes: occasional (max 2 per email)
- Semicolons: rare
- Parentheticals: occasional
- Ellipses: never (too casual for email)

### Structure
- Lead with: purpose or conclusion (the ask, the decision, the update)
- Paragraph length: short (1-3 sentences)
- Bullet use: acceptable for lists of 3+ items, avoided for flowing prose
- Sign-off: simple ("Mira", "Thanks, Mira", "—Mira")

### Tone markers
- Register: professional, direct, efficient
- Self-reference: first-person frequent, no third-person
- Hedging: minimal — commit or don't commit

### Taboos (per-skill)
- Never: "please don't hesitate", "let me know if", "feel free to", "I hope this helps"
- OK despite being on universal list: (none at default)

### Examples

**Example 1 — Short external reply:**
```
Tuesday 2pm works. I'll send the LOI draft by Monday EOD so you have time to
review before the call.

—Mira
```

**Example 2 — Internal update:**
```
Quick update on Sam:

Spec is locked, building out the amendments today. First draft of the
customization package ready tomorrow morning.

Mira
```

**Example 3 — Cold outreach:**
```
Hi Skyler,

Came across your post on AI in PE last week. The point about deal teams
using custom-tuned assistants resonated — that's exactly the work we're
doing at Chalette.

Would a 20-minute call this or next week make sense? I can walk you through
what we're shipping for two PE-adjacent clients right now.

Mira
```

---

## Staleness

If `voice_block_last_refreshed` is >12 months old, or corrections log has >20 rows since last refresh, emit at top of output:

```
Quick note: your writing voice profile hasn't been refreshed in a while (last update: [date]). When you have a moment, say "tune my email drafts" and we'll refresh it so your drafts stay tuned to how you actually write.
```

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Draft emails in the CEO's voice — short external, long external, internal, cold outreach, and intro/connector emails. Use when the CEO says 'draft an email', 'draft an email to [recipient]', 'email [name] about [topic]', 'email to', 'write an email', 'respond to [thread]', 'cold email to [prospect]', 'intro email', 'reply to [person]', 'write an email saying', 'send [recipient] an email about', 'follow up with [name] about [topic]', 'follow up with' (qualified below — a follow-up REQUEST, not a reminder), 'draft a check-in', 'draft a check-in to [name]' (a plain outbound draft, no meeting in context). Produces a ready-to-send draft saved to the recipient's thread or a staging folder. Runs voice calibration protocol per shared/VOICE_CALIBRATION.md with this skill's Voice Block. Also handles first-run personalization settings — use when the CEO says 'tune my email drafts', 'tune email-writer', 'show email-writer settings', 'reset email-writer to defaults', 'change my email draft posture'. DOES NOT fire on 'follow up on that call' / 'follow up on the meeting' / 'draft follow-ups' from a meeting (follow-up-ritual — meeting-shaped), 'triage my inbox' (inbox-triage), or 'write a memo' / 'recurring update' (memo-writer). DOES NOT fire on 'draft a Slack message' / 'text [name]' / 'draft a text to' (out of scope — this skill drafts email only; chat-message drafting has no owner today, say so plainly instead of improvising).
> DOES NOT fire on 'remind me to' / 'remind me about' phrasings, even when they contain a follow-up-with fragment — *remind me to follow up with Acme Friday* is a reminder capture (show-my-reminders), not a draft request. The bare follow-up-with trigger applies only when the utterance IS the ask to draft now (SPEC PIPE1 latent-collision fix, 2026-07-13).
