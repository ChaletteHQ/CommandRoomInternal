---
name: intro-broker
description: "Draft introductions between two people you know — voice-calibrated to your past intros, tuned to both sides' context, and logged into the relationship graph so future people-crm queries surface 'you connected them on [date]'. Use when the CEO says 'intro [A] to [B]', 'intro [A] and [B]', 'connect [A] and [B]', 'make an intro between [A] and [B]', 'introduce [A] to [B]', 'broker an intro', 'set up an intro', 'introduce [name] and [name]'. Produces two drafts (double-opt-in and direct-forward) so the CEO can pick the right intro shape. Reads both people's full records from people-crm + recent interactions with each + past intro_made events as voice samples. Writes intro_made event linking both people, updates entities.json connections graph, scheduled intro_followup_check 30d out to verify landing. DOES NOT fire on 'email [name] about [topic]' (email-writer — single recipient), 'follow up with the intro' (email-writer or follow-up-ritual), or 'who should I introduce to whom' (no skill yet — could be insight-generator pass)."
---

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

Before resolving the two people in the intro request, you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` for EACH name. Multi-candidate results MUST surface a disambiguation widget — do NOT silently pick the first match. Only after `resolve_all` returns no candidates for a name may you fall back to grep, and that fallback MUST be flagged to the user. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract.

## Skill Boundary

- **Use intro-broker for:** drafting an introduction email between two people you know. Two-sided draft work with relationship-graph writes.
- **Use `email-writer` for:** single-recipient email drafts (no intro framing).
- **Use `people-crm` for:** looking up either person before deciding to intro.

## Writer Contract (v3.8.0+ — substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Appends to:**
- `_hq/data/events.jsonl` — event type `intro_made` with TOP-LEVEL `person_ids: [person_a_id, person_b_id]` (canonical Event shape per events.schema.json) plus `data: {person_a_id, person_b_id, draft_style, why_intro_summary, email_drafted_event_seq, scheduled_followup_check_ts}`. The top-level `person_ids` array is what apply-choices reads when the Pulse intro-followup-check resolves to `landed` / `didnt land`; the `data.person_a_id` / `data.person_b_id` scalars are preserved for downstream skills that want explicit A/B roles. v3.13.6+ — pre-v3.13.6 only the scalars were written; the missing top-level array silently severed the relationship-graph closure (apply-choices Step 3c read `parent["person_ids"]` and got `None`).
- `_hq/data/events.jsonl` — event type `intro_followup_check` scheduled 30 days out (writes a future-dated event Pulse picks up). Carries `{intro_event_seq, scheduled_for, check_question: "did either reply / did the meeting happen"}`. The check event itself does NOT carry person_ids — they live on the parent `intro_made` event (apply-choices Step 3c fetches them via the `intro_event_seq` reference).
- `_hq/data/entities.json` — both people's records get a `connections[]` entry pointing at the other's `person_id`, with `connection_source: "intro_made"`, `connection_event_seq`, `connected_ts`. Both records bump `last_touched_at`.

**Reads from:**
- `_hq/data/entities.json` — both people's full records: org, role, relationship strength tier, prior interactions, decision context where they appear.
- `_hq/data/events.jsonl` — `type == "interaction"` events with each person to compute relationship depth and recent context.
- `_hq/data/events.jsonl` — `type == "intro_made"` events you've sent before, used as voice samples for the draft style. The user's past intros are the canonical training corpus for "how you actually write intros" — better than the generic Voice Block.
- `_hq/data/events.jsonl` — `type == "decision"` events that mention either person or their org in `data.context` — surfaces "your stated positions" about either side that should inform the framing.

**Conflict boundary:** sole writer of `intro_made` and `intro_followup_check` events. People-crm owns entities.json person records; this skill writes ONLY to the `connections[]` sub-array (namespaced) and `last_touched_at` field — no collision per the people-crm convention.

---

# intro-broker

The introduction is one of the CEO's most-leveraged communication moves and also one of the easiest to do badly. A generic intro template ("X meet Y, you should know each other") is why people get bad intros. A good intro requires knowing both relationships well enough to frame the value prop tuned to each side — exactly what the Command Room substrate has.

## What It Does

For an intro request between Person A and Person B, this skill:

1. Loads both people's full context from people-crm + interaction events.
2. Identifies the "why this intro" angle by intersecting: A's needs/projects vs B's offerings/portfolio (or vice versa).
3. Drafts TWO emails — a double-opt-in version (recommended) and a direct-forward version (faster) — so the CEO picks the right intro shape.
4. Pre-drafts a companion note for the double-opt-in flow (sent after the first recipient says yes).
5. Logs the intro to the relationship graph so future `people-crm` queries surface "you connected them on [date]" automatically.
6. Schedules a 30-day follow-up check (Pulse picks up; surfaces "did either reply / did the meeting happen?")

## How to Use

```
"intro Bo to Rio"
"connect Bo and Rio"
"make an intro between Bo and Rio"
"introduce Bo to Rio"
"broker an intro between Bo and Rio"
"set up an intro: Bo ↔ Rio"
```

If only one name is given ("intro Bo to..." or "make an intro for Bo"), the skill asks who the other side should be.

If either name is ambiguous (multiple matches in entities.json), the skill asks which person.

## How It Works

### Phase 1 — Resolve both people

Resolve A and B via `aliases.json` + `entities.json` person records. If ambiguous, surface a disambiguation prompt. If either is not in entities.json, ask whether the user wants to add them via `people-crm` first.

### Phase 2 — Build "why this intro"

Intersect A's context with B's context:
- A's open projects / needs (from project tags + recent commitments + active deals)
- B's offerings (from B's role + org + portfolio if known)
- Any explicit signal in your interactions with either ("X mentioned looking for distribution help" / "Y's portfolio includes operator-led SaaS")

Compose a 1-2 sentence "why this intro makes sense" framing. This is the differentiator from a generic intro template.

### Phase 3 — Voice-calibrate from past intros

Read `_hq/data/events.jsonl` for prior `intro_made` events you've sent (up to last 20). For each, read the linked `email_drafted` event's content. These become the voice corpus — your actual intro style, not a generic template.

Extract patterns: opener style, length, signoff, "vouching" language, whether you typically do double-opt-in vs direct-forward.

### Phase 4 — Draft both styles

**Draft 1 — Double-opt-in (recommended):**
- Email to A asking permission, vouching for B, explaining why
- Companion note pre-drafted to B (sent after A's yes)

**Draft 2 — Direct-forward:**
- Single email to A and B
- You frame both ends, then exit ("take it from here")

Voice-calibrated via past intros (Phase 3) + this skill's Voice Block fallback.

### Phase 5 — Render widget + capture choice

**v3.13.0+ — surface via the canonical chat action widget per `shared/EMAIL_DRAFT_PROTOCOL.md` (universal scope as of v3.13.0).** Both drafts go into a single widget as two items so the user picks one (or edits before sending). Data view shape:

```python
data_view = {
    "widget_mode": "all_batch_widget",
    "header": f"Intro: {person_a_name} ↔ {person_b_name}",
    "sub_header": "Two draft styles below — pick the one that fits, edit if needed, send.",
    "sections": [{
        "title": None,
        "count": None,
        "items": [
            {
                "n": 1,
                "icon": "🤝",
                "name": "Draft 1 — Double opt-in (recommended)",
                # metadata is LIST OF [key, value] PAIRS — required for the
                # data-shape validator to recognize this as email-shaped.
                "metadata": [
                    ["To", person_a_email],
                    ["Subject", draft_1_subject],
                ],
                "context_tag": "Asks the first side for permission before looping the other in",
                "body_lines": [f"> {line}" for line in draft_1_body.split(chr(10))],
                "actions": ["1 send", "1 edit then send", "1 draft", "1 skip"],
            },
            {
                "n": 2,
                "icon": "🤝",
                "name": "Draft 2 — Direct forward",
                "metadata": [
                    ["To", person_a_email],
                    ["Subject", draft_2_subject],
                ],
                "context_tag": "Faster — connects both sides in one note",
                "body_lines": [f"> {line}" for line in draft_2_body.split(chr(10))],
                "actions": ["2 send", "2 edit then send", "2 draft", "2 skip"],
            },
        ],
    }],
}
html = render_chat_output_widget(data_view, wrapper="fragment")
validate_rendered_widget(html)
# Pass html byte-for-byte to mcp__visualize__show_widget — no post-processing.
```

Per `EMAIL_DRAFT_PROTOCOL.md` §1, the user clicking `N send` triggers Gmail-send via the canonical dispatch order (Zapier-threaded → native threaded → standalone fallback). The double-opt-in companion note (Draft 1) ships separately on confirmation of A's yes; that second-stage workflow stays as documented in this skill — it's just the FIRST stage (which draft to send to A) that's now widget-driven instead of plain-text-driven.

### Phase 6 — Log to substrate

On send:
1. Chain to `email-writer` for the actual send (writes `email_drafted` + `email_sent`)
2. Append `intro_made` event linking both people + the `email_drafted_event_seq`
3. Update both people's `connections[]` in entities.json
4. Schedule `intro_followup_check` 30d out

## Output Structure (widget)

```
Intro: Bo Sample ↔ Rio Sample

Your read on each side:
  Bo:  Building founder-led distribution. Met 2026-03-12.
       Live thread about partnership structure.
  Rio: Northstar Partners — invests in operator-class SaaS.
       You've sent her 3 intros that closed; she trusts your picks.

Why this intro: Rio's portcos need exactly the distribution model
Bo's building. Specific, not generic.

——— Draft 1 — Double opt-in (recommended) ———

  To: bo@example.com
  Subject: Quick ask — intro to Rio Sample?

  Bo —

  Quick one: would you want an intro to Rio Sample at Northstar
  Partners? She invests in operator-class SaaS and her portcos need
  exactly the founder-led distribution you're building.

  I'd vouch for both ends. If you're game, I'll send a separate note
  to her first, then loop you in once she's a yes.

  Matthew

  (Companion note to Rio is pre-drafted and ready to send once Bo says yes.)

——— Draft 2 — Direct forward (faster, connects both sides at once) ———

  [...]

[Send Draft 1]  [Send Draft 2]  [Edit either]  [Cancel]
```

## DOES NOT

- Auto-send. All drafts go to Gmail Drafts via `email-writer`; user reviews and clicks Send.
- Create a person record. If A or B isn't in entities.json, the user must add them via `people-crm` first.
- Re-introduce two people already connected. If both already have each other in `connections[]`, surface "you already connected them on [date]" and ask whether to re-introduce.
