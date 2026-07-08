# Fuzzy Router Protocol

**Owner:** `workspace-manager` (invoked on every turn that doesn't obviously match a specialist skill)

**Purpose:** Real CEOs do not say clean trigger phrases. They say "pull up the NorthStar stuff", "what's Acme doing", "I've got the Bowie call in ten", "clear my inbox", "who's ghosting me". The product must work from **intent and context**, not from literal command-phrase matching.

This protocol defines how workspace-manager catches loose input, identifies what the user wants, auto-loads relevant context, and either routes silently to the right specialist skill or asks ONE clarifying question. It is the glue that turns a collection of skills into a product.

**Substrate note (v3.12.0):** This protocol references `_hq/ALIASES.md` and `_hq/PEOPLE.md` as name-lookup tables. Per `references/SOURCE_OF_TRUTH.md`, those are Tier 2 regenerated views (canonical alias data lives in `_hq/data/aliases.json`; canonical person data in `_hq/data/entities.json`). Name resolution is **name-lookup only** — workspace-manager uses these views to map "NorthStar" → `org_*` id, then routes to the right specialist. The specialist then reads canonical state from events.jsonl per the overlay rule. No state decisions ride on the Tier 2 views in this protocol.

---

## Core Principle

> **The published trigger vocabulary is a cheat card, not a contract.**

We publish ~15 clean triggers so disciplined users can memorize them. But the product must also work when the user says something entirely different. Every skill's description carries a **semantic intent clause** that Claude's LLM-level matcher uses to recognize the underlying request, regardless of surface phrasing.

When semantic matching still leaves ambiguity, workspace-manager steps in as the router of last resort.

---

## The Workspace Model: Primary Focus + Side Stuff

Real CEOs live in a hierarchy, not a flat project list. The workspace schema reflects this:

- **Primary-focus orgs** — the CEO's day-to-day businesses. Usually one, sometimes two or three for portfolio operators. Holds most of their people, projects, decisions, daily activity. Default context for everything unless explicitly flagged otherwise.
- **Non-primary orgs** — board seats, advisory gigs, investments, side deals. Have their own people and cadence. Smaller footprint; different stakes.
- **Personal** — family, health, personal finances, relationships. Modeled as a dedicated org with `relationship_type: beneficiary` or a personal org entry. Minimal by default, but present.

Every org in `_hq/data/entities.json` carries two fields that drive routing:
- `is_primary_focus: boolean` — bubbles the org to the top of briefings and default context.
- `relationship_type` — `operating | partner | board | advisory | investment | client | portfolio_company | beneficiary | other`.

Projects inherit affiliation via their parent `org_id`. Persons carry `affiliation_org_ids[]` — a person can belong to multiple orgs.

> **Legacy mapping:** v1 used `org_affiliation: home | side-[name] | personal`. v2.2+ splits that into `is_primary_focus` + `relationship_type` + `parent_org_id`. The DEPRECATED `type` field on orgs retains legacy values for migration readers. `ORG_AND_THREAD_MODEL.md` has the full mapping.

### Why this matters for routing

1. **Name resolution is affiliation-aware.** When the CEO says "Bowie", workspace-manager resolves to the Bowie whose `affiliation_org_ids` overlap the current conversational org context (primary-focus org by default; non-primary org if actively in a side-deal project). If both are plausibly relevant, disambiguate.

2. **Briefings default to primary-focus first.** "What's going on" renders primary-focus orgs prominently; non-primary orgs get a collapsed secondary section at the bottom. CEO's eye goes to primary-focus by default — that's where their day actually is.

3. **New-project default is primary-focus.** When the CEO says "new project BrightLabs", workspace-manager asks one question: "Which org — [primary-focus org name] or another?" Default answer = primary-focus. Only requires a tap to confirm; `parent_org_id` gets recorded on creation.

4. **Cross-org collisions are a real thing.** If "Acme" is a primary-focus-org customer AND "Acme" is an advisory-gig company, the router can't just pick one. It asks: "Which Acme — the customer deal or the advisory gig?"

---

## The Four Layers of Routing

Every user turn goes through these layers, in order:

### Layer 1 — Exact trigger match
If the user said one of the clean triggers ("triage my inbox", "follow up on that call", "brief me"), the corresponding skill fires directly. No fuzzy routing needed.

### Layer 2 — Semantic intent match
Re-baselined against the live frontmatter 2026-07-02 (P2 routing lattice) — when a fence changes in a skill's description, update the matching row here IN THE SAME COMMIT; this file arbitrates ties and must never contradict the frontmatter it arbitrates.

If the user's phrasing isn't exact but clearly maps to a single skill's intent clause ("clean up my email" → inbox-triage; "draft the post-call emails" → follow-up-ritual), the skill fires via Claude's natural description matching.

### Layer 3 — Name-mention routing (workspace-manager)
If the input mentions a project name, person name, or alias (from `_hq/ALIASES.md` and `_hq/PEOPLE.md`), workspace-manager:
1. Recognizes the entity
2. Loads that project/person's context
3. Decides: is this a context-load request (route nowhere, just load) or does the rest of the sentence imply an action (route to the matching specialist)?

### Layer 4 — Disambiguation (workspace-manager)
If the input is genuinely ambiguous ("catch me up", "what should I do", "help"), workspace-manager asks exactly ONE clarifying question with 2-4 concrete options. Never a free-text "what do you mean?" — always a multiple-choice menu.

---

## Layer 3 — Name-Mention Routing (Detailed)

### Name Detection

On every turn, workspace-manager scans the user's input for:

1. **Project names and aliases** from `_hq/ALIASES.md` — these map things like "NorthStar" ↔ "northstar-ops" ↔ "NS deal"
2. **Person names** from `_hq/PEOPLE.md` — first names, last names, nicknames, email local-parts
3. **Company names** from `_hq/PEOPLE.md` organization field and project context
4. **Home-org initiative names** — internal projects/departments/deals inside the home org
5. **Recent-context names** — entities named in the last 3 turns of SESSION_NOTES, even if not in ALIASES yet

Matching rules:
- Case-insensitive
- Word-boundary only (don't match "Bowie" inside "bobcat")
- If multiple matches, first apply affiliation filter: prefer the entity whose `affiliation_org_ids` intersect the current conversational org context. Primary-focus org is the default context unless the prior turns or stated subject indicate a non-primary org.
- Within the same org, prefer the most recently active (latest `last_interaction` on the project, or latest SESSION_NOTES appearance for a person)
- If a name collides with a common word ("May" as a person vs the month), require additional signal — or disambiguate
- If a name has plausible matches in both a primary-focus org AND a non-primary org, and neither has strong recency bias, disambiguate with one question ("Which Acme — the customer deal or the advisory gig?")

### Intent Shape After Name Detection

Once a name is detected, check the rest of the sentence for intent signals:

| If the sentence contains... | Route to... |
|---|---|
| "prep", "ahead of", "before the call", "what do I need" | `call-prep` |
| "follow up on that call/meeting", "close the loop", "recap the call" | `follow-up-ritual` |
| "follow up with [name] about [topic]", "send a note" (no meeting in context) | `email-writer` |
| "status", "where are we", "what's going on", "pull up", "show me" | `workspace-manager` (project load only, no further routing) |
| "email", "draft an email", "reply", "message [name]" | `email-writer` (owns ALL email drafting) |
| "one-pager", "leave-behind", "write up a note on" | `one-pager-composer` |
| "last call", "meeting with", "what did we discuss" | `meeting-notes` |
| "stress test", "what could go wrong", "risk" | `stress-test` |
| "competitors", "landscape", "how do we compare" | `research` (no competitive-intel skill ships in this plugin) |
| (no action verb, just a name) | `workspace-manager` (context-load only) |

### Examples

| User says | Detected | Action |
|---|---|---|
| "pull up the NorthStar stuff" | project: NorthStar | Load NorthStar context. No further action. |
| "what's Acme doing" | project: Acme | Load Acme context. Show 1-paragraph status. |
| "Bowie call in ten, what do I need" | person: Bowie + intent: prep | Route to call-prep with Bowie as subject. |
| "follow up with Skyler on pricing" | person: Skyler + intent: outbound draft | Route to email-writer (no meeting in context — meeting-shaped follow-ups go to follow-up-ritual). |
| "what did we decide with Sam last week" | person: Sam + intent: decision retrieval | Route to decision-log, scoped to decisions involving Sam in the last 7 days. |
| "NorthStar" (just the word) | project: NorthStar | Load context, await next instruction. |

---

## Layer 4 — Disambiguation (Detailed)

### When to Ask

Workspace-manager asks a clarifying question when:
- No name detected AND no intent detected ("help", "catch me up", "what now")
- Name detected but intent spans multiple skills ("let's do Acme" — load? prep? follow up?)
- Intent detected but no target ("draft something" — what, for whom?)

### How to Ask

**Rule:** ONE question, 2-4 concrete options, each option is an action the user can confirm or reject with a single word.

Template:
```
Sounds like you want me to either (A) [most likely option],
(B) [second most likely], or (C) [third]. Which?
```

Never:
- Ask "what do you mean?" (open-ended — user has to type)
- Ask more than one question
- Ask for clarification when a reasonable default exists (pick the default, act, tell the user what you picked so they can correct)

### Examples

| User says | workspace-manager asks |
|---|---|
| "catch me up" | "Sounds like you want either (A) a morning briefing, (B) status on the active projects, or (C) a triage of the overnight inbox. Which?" |
| "help me with Acme" | "Acme is loaded. Want me to (A) prep you for the next Acme meeting, (B) draft a follow-up from the last call, or (C) just pull up the current status?" |
| "do something about email" | "Email-wise, I can (A) triage the inbox into reply-now/decide/FYI buckets, (B) draft follow-ups from yesterday's calls, or (C) clear newsletter clutter. Which?" |
| "what now" | "You have 3 overdue commitments and the Bo call in 45 min. Want me to (A) prep the Bo call, (B) clear the overdue items, or (C) something else?" |

### Default-and-tell

When a reasonable default exists, prefer acting over asking. Examples:

- User says "NorthStar" → load NorthStar context, tell user: "NorthStar loaded. Last touched 3 days ago, next action is [X]. What do you want to do?"
- User says "follow up" with no target → assume the most recent meeting. Act. Report: "Drafted follow-ups for the Acme call this morning — is that the one you meant?"

This keeps the conversation moving. A wrong default that's easy to correct beats a correct question that slows the user down.

---

## Name Resolution File — `_hq/ALIASES.md`

Workspace-manager maintains `_hq/ALIASES.md` as a simple markdown table mapping canonical names to all their aliases. Updated on:
- "new project" — add project name + user-stated aliases
- "end session" — if user referred to the project by a new alias, append it

Format:
```
| canonical | aliases | org_id | type |
|---|---|---|---|
| [PrimaryOrg] | the company, HQ, us, internal | org_primaryorg | org |
| Q3 Pricing Reset | pricing reset, the pricing thing | org_primaryorg | project |
| Acme Customer Deal | Acme, acme corp, the Acme deal | org_primaryorg | project |
| NorthStar Advisory | NorthStar, PO, the the gig | org_northstar | project |
| Fund II | the fund, LP stuff | org_fund_ii | project |
| Bowie Sample | Bowie, BC, bowie@example.com | org_primaryorg | person |
| Bowie Halen | Bowie H, bowie.h@example.com | org_northstar | person |
| [portfolio CEO] | Bo, DW, drew@example.com | org_fund_ii | person |
```

This file is the lookup table for Layer 3 name detection. The `org_id` column drives the affiliation-aware disambiguation — when the CEO says "Bowie", workspace-manager uses conversational context + recency + the target entity's org(s) to pick the right one, or asks if truly ambiguous. `org_id` values must match ids in `entities.json` so the router can look up `is_primary_focus` and `relationship_type` for ranking.

This file is the lookup table for Layer 3 name detection.

---

## What Breaks If We Get This Wrong

- **Under-matching:** CEO says "NorthStar" and nothing happens → product feels dead. Losing trust within the first week.
- **Over-matching:** CEO says "nothing" and the system loads 5 projects → noise. Losing trust differently.
- **Wrong routing:** CEO says "Bowie call prep" and follow-up-ritual fires → visibly broken. Losing trust the fastest.
- **Asking too much:** CEO says anything fuzzy and we ask 3 questions before acting → the product feels like a slow intern. Losing patience.

The router's job is to make the common case invisible (just works) and the rare ambiguous case resolvable in one question.

---

## Implementation Notes

- workspace-manager runs the fuzzy-router check on every turn where no specialist skill fired cleanly.
- Name detection is cheap (single pass over input string against `_hq/ALIASES.md` + `_hq/PEOPLE.md`). Run it on every turn, even when a specialist skill matched — the detected name is still useful context for that skill.
- When routing, workspace-manager invokes the specialist by adding a system note to the turn: "User said X. Detected name: Y. Routing to skill: Z. Context loaded." This makes failures traceable.
- When a router decision turns out wrong (user corrects it), append to `_hq/ROUTER_MISSES.md`. Use the miss log to improve intent descriptions weekly.

---

## What This Protocol Does Not Do

- Does not replace skill descriptions — every skill still carries its own intent clause.
- Does not override specialist skills when they match cleanly — Layer 1 and 2 run first.
- Does not auto-commit to file changes or send messages on ambiguous input — ambiguity always routes to "ask or default-and-tell," never to action.
- Does not try to be a chatbot — if the input is pure conversation ("thanks", "ok"), workspace-manager stays silent and lets the session continue.
