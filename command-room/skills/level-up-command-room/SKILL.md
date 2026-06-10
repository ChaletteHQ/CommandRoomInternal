---
name: level-up-command-room
description: "Umbrella menu for optional Layer 2 sidebar dashboards. As of v3.11.0, no Layer 2 dashboards are currently shipped — the Commitment Cockpit was retired and folded into the Commitments scheduled chat. This skill remains as the canonical entry point so the trigger phrases keep working and so future Layer 2 additions have a home. Triggers: 'level up command room', 'level up my command room', 'show me dashboards', 'what dashboards can I install', 'show me what I can enable', 'add more dashboards', 'level me up'. DOES NOT fire on individual 'enable [X]' phrases (those route directly to the matching enable-* skill when one exists), 'rebuild [artifact]' (refresh, not enable), 'install command room' (that's command-room-onboarding)."
---

# Level Up Command Room — Layer 2 umbrella router (v3.11.0 — empty menu state)

## What this skill does today

Surfaces what Layer 2 dashboards are available to install. As of v3.11.0, **the menu is empty** — there are currently no Layer 2 add-on dashboards shipped.

The two Layer 1 default dashboards (Workspace Map, Quick Commands) auto-install via `command-room-update-bridge` and are not opt-in.

## Why the menu is empty (v3.11.0)

Layer 2 historically housed opt-in dashboards that complemented the daily scheduled chats. The Commitment Cockpit was retired in v3.11.0 — its kanban surface (you owe / they owe / both stuck) fragmented the truth, since the Commitments scheduled chat (`commitments` taskId, fires 8:30 AM weekdays) is the single source for the same data. Pay Attention To was scoped but never shipped.

The architectural call: scheduled-task chat threads beat snapshot-only Live Artifacts for commitment-shaped surfaces, because the chat threads accumulate context over time and support inline action (resolved / not relevant / add to my list) — the Cockpit only rendered, requiring the user to bounce back to chat to act.

---

## Response to the trigger phrases

When the user fires one of the trigger phrases, surface plain English:

> *"There aren't any extra dashboards to add right now — the menu is empty.*
>
> *Your two default dashboards (Workspace Map + Quick Commands) are already pinned to your sidebar.*
>
> *The places where you'll find your day-to-day action items are your scheduled chats: Morning Brief, Upcoming Meetings, Inbox, Commitments, Pulse, Past Meetings, and Friday Wrap. Open Cowork's Scheduled section to see them."*

Stop. No menu, no numbered list, no install actions.

If the user asks about the Commitment Cockpit specifically (because they had it pinned previously):

> *"The Commitment Cockpit isn't a thing anymore — its content (what you owe / what they owe / what's stuck) now lives in your daily Commitments chat, where you can act on items inline. If it's still pinned to your sidebar, you can unpin it; it won't refresh anymore."*

---

## Log the visit (optional, informational)

Append one event to `_hq/data/events.jsonl` if you want to track menu visits in the empty-menu era — useful for `release-readiness` to detect "M visited the level-up menu, surfaced empty" patterns that might justify shipping a new Layer 2 dashboard:

```jsonl
{"type":"levelup_session","picks":[],"skipped":[],"ran_at":"<ISO>","menu_state":"empty"}
```

Skip the log if writes would error — this is informational, not load-bearing.

---

## When this skill becomes relevant again

When a future Layer 2 dashboard ships, the skill grows to:

1. Read `_hq/data/events.jsonl` for `artifact_installed` events to detect installed state.
2. Build a menu of available Layer 2 dashboards with status pills.
3. Parse the user's pick + route to the matching `enable-*` skill.

The historical Phase 1-5 flow lives in the v3.10.x version of this file in git history if reviving is needed.

---

## What this skill does NOT do

- **Does not show Layer 1 artifacts (Workspace Map, Quick Commands).** Those are always-on at onboarding, not opt-in. Listing them here would confuse the install model.
- **Does not handle uninstall.** "Unpin [X]" is a manual Cowork sidebar action, not a skill flow.
- **Does not fabricate an install path.** If no `enable-*` skill exists for an artifact name, surface plain English — never invent one.
