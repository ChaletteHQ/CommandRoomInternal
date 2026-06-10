---
name: research
description: "Produce a verified, cited research brief on any company, person, market, or topic — then fold the findings into the CEO's workspace so they compound instead of evaporating. Reads entities.json + events.jsonl FIRST to frame the question through the CEO's own projects, people, and threads, then runs fan-out web search with source verification. Uses Tavily for deeper web search and clean page extraction when that connector is present, and the Vibe Prospecting enrichment tools to verify firmographics, surface funding / hiring / leadership trigger events, and identify real decision-makers when that connector is present; both are optional upgrades over built-in web search, and the brief honestly labels which sources were used. Hands verified findings to intel-intake to save and to people-crm to record any decision-makers. Use when the CEO says 'research [company]', 'look into [company]', 'dig into [company]', 'background on [person]', 'what do we know about [company]', 'pull together research on [topic]', 'do some research on [topic]', 'research brief on [topic]', 'research [person] before my call'. DOES NOT fire when the CEO already has the source in hand and says break this down or parse this — that is intel-intake; research is for when there is no source yet. DOES NOT fire on what did anyone say about [topic] or transcript search — that is transcript-search, which searches the CEO's own meetings, not the web. DOES NOT fire on prep me for my meeting (call-prep) or one-pager on [topic] (one-pager-composer)."
---

## Recommended Model

**Default: Opus.** Framing the question against the workspace, judging source quality, cross-checking claims, and deciding what is worth saving are all judgment calls — this is exactly where Opus earns its cost. The brief is read and acted on by the CEO, and bad synthesis is worse than no brief.

---

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

If the skill is invoked with a name-bearing trigger ("research [company]", "background on [person]", "look into [client]"), you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` FIRST to resolve the named scope to an existing person / org / project before any web search or substrate query. This is what lets the brief say "Acme Co — the prospect from your Northstar intro, last touched 12d ago" instead of treating it as a cold name. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract. If `resolve_all` returns no match, proceed with research and stub the entity only at save-time via the canonical writers (never hand-edit entities.json).

## Skill Boundary (v2.1)

- **Use research for:** when the CEO needs to learn something that is NOT already in the workspace or their meetings — go find it on the public web (plus optional enrichment), verify it, brief it, and save what matters.
- **Use `intel-intake` for:** when the CEO already HAS the source — a link, a YouTube URL, a pasted article — and wants it structured. research delegates its save step to intel-intake; intel-intake is the one-way "source → intel" flow, research is the "no source yet → go find one" flow that precedes it.
- **Use `transcript-search` for:** finding what was said inside the CEO's own meeting transcripts. research never searches the meeting corpus; it searches the world.
- **Use `people-crm` for:** "who is [person]" / "tell me about [person]" lookups scoped to the existing workspace. research delegates to people-crm to RECORD newly-discovered decision-makers, but does not field the lookup intent itself.
- **Use `call-prep` for:** assembling a pre-meeting brief from existing workspace context. If the CEO says "research [company] before my call", research runs (web + enrichment) and can hand its findings forward; "prep me for my meeting" with no research ask stays with call-prep.

## Writer Contract

research does NOT write substrate directly. It READS and DELEGATES every write to a canonical owner:

- **Reads:** `entities.json` (people / orgs / projects / threads, via `resolve_all`) and `events.jsonl` (recent interactions, prior `intel_logged`, meetings) to frame the question. Defensive reads only — handle both `entities.threads` and legacy `entities.projects` shapes, flat and nested.
- **Saves findings via `intel-intake`:** verified brief content is handed to intel-intake, which writes the `intel_logged` event and the `_hq/intel/` artifact with entity cross-references. research adds no new event type.
- **Records decision-makers via `people-crm`:** any person discovered through enrichment is passed to `people_writer` (dedup-first: `find_existing_person` → `update_person` or `create_person`). This is the ONLY place enriched contact PII lands — never loose notes.

**Atomic-write enforcement:** research itself emits no raw writes. All persistence flows through `intel-intake` and `people_writer.py`, which use the atomic helpers (`atomic_append_jsonl`, `atomic_write_json`) per `cr-substrate-helpers.md`. No `open(...).write()` anywhere in this skill.

**Source-of-truth honesty:** every claim in the brief carries its origin — `web`, `enrichment` (Vibe Prospecting), or `workspace`. The brief states which sources were available so the CEO can weigh confidence. Never present an unverified web claim as fact.

## What It Doesn't Do

- Does NOT require any paid MCP. Built-in web search is the always-available, zero-setup floor and the full skill works on it alone. Tavily (deeper web) and Vibe Prospecting (structured enrichment) are independent detected upgrade branches — each lights up only if the client connected it, and the skill runs clean and labels its depth honestly when either or both are absent.
- Does NOT bulk-pull enrichment. When Vibe Prospecting is present it enriches only the specific company / people in the question, and surfaces estimated credit cost before any large or multi-entity pull. It never spends the CEO's credits silently.
- Does NOT save anything without showing it first. The brief is produced and shown; saving to the workspace (intel + contacts) is offered as a confirm-choice, not done automatically.
- Does NOT draft emails, memos, or replies in the CEO's voice — it produces a research brief (a report). Composition stays with email-writer / memo-writer / one-pager-composer.
- Does NOT invent sources or fill gaps with plausible guesses. If the web is thin, it says so and flags low confidence rather than padding.

## How to Use

```
research Acme Co
look into Acme Co before my call Thursday
background on Sam Sample
what do we know about Northstar Partners
dig into the freight-brokerage market
pull together research on AI pricing in logistics
do some research on Category Company's main competitors
research brief on warehouse automation vendors
```

## How It Works

Four steps. Built-in web search is the floor, but it is NOT the default when better tools are connected — actively reach for Tavily (deeper web) and Vibe Prospecting (structured enrichment) whenever those connectors are present. Settling for built-in web when an upgrade connector is available is the one failure mode this skill must avoid.

### Step 1 — Frame from the workspace (always)

Before searching, resolve the subject against the CEO's own world so the research is aimed, not generic.

1. If the trigger is name-bearing, call `resolve_all(workspace_root, query)`.
2. If it resolves to a known entity, pull the relevant context — what thread it sits in, last contact, open commitments, prior intel — and let that shape the question. "Research pricing for Acme Co" becomes "research pricing for Acme Co, given they are a mid-stage prospect in the Northstar thread and the last call flagged budget."
3. If it does not resolve, treat it as a new subject and continue — you may stub the entity at save-time.

### Step 2 — Search and verify

Use the fan-out research pattern: decompose the question into several angles, search each, **fetch the actual pages** (don't synthesize from snippets), **cross-check every material claim across at least two independent sources**, cite each claim, and tag low-confidence items explicitly. The verify discipline is the same no matter which engine runs underneath.

**Reach for the strongest tools BY DEFAULT — this is the most important rule in this step.** Built-in `WebSearch` / `WebFetch` is the *fallback floor*, not the default. When an upgrade connector is present, you MUST use it; do not quietly settle for built-in web just because it is always sitting there and can answer. The whole value of this skill over a plain web search is that it actively reaches for the better tools. Two things follow:

1. **Actively look for the tools — don't assume they're absent.** The host may load connector tools on demand, so a tool you don't see in your immediate set may still be connected. For any research task, actively surface/look up the Tavily and (for company/person subjects) Vibe Prospecting tools before concluding they aren't available. "I didn't reach for it" is not the same as "it isn't connected" — and silently treating the first as the second is the failure this rule exists to prevent.
2. **If you genuinely fall back to built-in web** because an upgrade connector really isn't connected, say so in one line of the chat reply (not just the HTML badge): e.g., "Ran on built-in web search — connect Tavily for deeper results." The CEO should never have to wonder which tier ran.

**Tavily (deeper web) — use whenever the connector is present.** For *any* web research, prefer `tavily_search` (agent-tuned recall, relevance scoring) over built-in `WebSearch`, and `tavily_extract` to pull clean full-page content — it beats `WebFetch` on messy or JS-heavy pages, strengthening the fetch-the-real-page step. For a deep company dive, `tavily_crawl` / `tavily_map` pull a whole site (about, pricing, docs). Tavily's free tier is generous and search is cheap — use it freely; only surface cost intent before a large multi-page `tavily_crawl`. (`tavily_research`, the agentic deep-research endpoint, is an opt-in "deep mode" for big market questions — still hold the cite-everything and cross-check discipline yourself when you use it.) Tavily improves *how well you search the web*; it does not replace the verify discipline or the workspace framing.

**Vibe Prospecting (structured enrichment) — use whenever the connector is present AND the subject is a company or a person.** Don't skip it just because built-in web already produced an answer — structured data is what *verifies* and *corrects* the web (it's how you catch a stale headcount or an unconfirmed title). Resolve the subject with `match-business` / `match-prospects`, then use `enrich-business` / `enrich-prospects` to verify the scraped claims against structured data (headcount, revenue band, industry, location) and `fetch-businesses-events` / `fetch-prospects-events` to surface trigger events (funding, hiring, leadership change) that web search misses or serves stale.

- **Cost guard:** Vibe is metered. For anything beyond a single-entity lookup, call `estimate-cost` and surface the estimated credit spend before pulling. Enrich surgically — only the entities in the question, never a speculative bulk pull. (This is a spend guard, NOT a reason to skip enrichment on a normal single-company/person research task — those should enrich by default.)
- For a pure market/topic question with no specific company or person, enrichment usually doesn't apply — skip it and lean on Tavily/web.

**If a connector truly isn't connected:** skip that branch cleanly, run on the next tier down, and label honestly. No dead tool references, no error.

### Step 3 — Render the branded brief, then offer to save

The default output is a **self-contained, Command-Room-branded HTML brief**, rendered into `shared/templates/research_brief.html` and surfaced in the preview panel (with an H2 link via `brief_path.get_brief_artifact_url()` — never a raw path). The template carries the brand inline, so it renders anywhere with no asset server.

**Adaptive depth — fill only the blocks that fit the ask:**
- **Quick lookup** ("what do we know about Acme Co") → bottom line + a few cited key findings + sources. Omit the optional sections.
- **Deep / account brief** ("research Acme Co before my call") → add `Who & what matters` (decision-makers, likely buyer highlighted), `Recent signals` (trigger events), and a narrative section.
- The badges are NOT optional: emit exactly one source badge reflecting the strongest tier actually used — `Verified · Vibe Prospecting` (enrichment ran) > `Deep web · Tavily` (Tavily ran, no enrichment) > `Web sources only` (built-in web) — plus one confidence chip (`high` / `medium` / `low`). Never claim a tier whose tools were absent. The footer source-summary names the engines used honestly (e.g., "5 sources via Tavily + Vibe Prospecting enrichment").

Then offer to fold the findings into the workspace as a confirm-choice surface (rendered via `render_chat_output_widget` — never markdown numbered actions):

- **Save the brief** → hand verified content to `intel-intake` (writes `intel_logged` + `_hq/intel/` artifact, cross-referenced to the resolved thread).
- **Add decision-makers** → pass any enrichment-discovered people to `people-crm` (`people_writer`, dedup-first). Enriched contact PII lands here and nowhere else.
- **Skip** → discard; nothing is written.

If the CEO wants a portable copy, also export the brief as a `.docx` via `brief_writer.make_brief()` and surface it as a second H2 link — same content, document form.

### Step 4 — Confidence and honesty

Close with a one-line confidence read tied to source coverage. Web-only firmographics are medium-confidence by default; enrichment-verified firmographics are high. Say which, and name the gaps the CEO should confirm.

### Worked example — "research Acme Co before my call Thursday"

(Acme Co already a prospect thread, so Step 1 pulls that context either way.)

- **Web-only path:** "Freight brokerage, Midwest. ~200 employees (LinkedIn, may be stale). CEO Rio Sample. No funding info found. Confidence medium on the company, low on size and on who the buyer is — confirm headcount and decision-maker on the call." Decent, but it evaporates unless saved.
- **Vibe Prospecting present:** corrects the stale headcount (~200 → 312 verified, i.e. growing faster than the public footprint shows), catches a $15M growth round that web search missed, surfaces 7 open ops roles incl. a VP RevOps hire, and names the real buyer — Bo Stone, COO (owns ops tooling), not the homepage CEO — alongside Skyler Sample, VP Finance (budget). Sources line: `web (3) + Vibe Prospecting enrichment`. Reports `~3 credits` spent. On save: brief → intel-intake, the three decision-makers → people-crm. Next week's call-prep and brief inherit all of it.

Same skill, same request. The branch simply turns on when the tools are there.

## Output

- **Branded HTML brief (default):** a self-contained Command-Room-branded artifact rendered from `shared/templates/research_brief.html`, adaptive in depth, surfaced in the preview panel + an H2 link via `get_brief_artifact_url()`. Source badge + confidence chip always present; enrichment-only sections appear only when there's enrichment to show.
- **Confirm-choice widget:** save-the-brief / add-decision-makers / skip, via `render_chat_output_widget`.
- **Substrate (on save, delegated):** `intel_logged` + `_hq/intel/` artifact via intel-intake; `person_*` records via people-crm.
- **Portable copy (on request):** a `.docx` via `brief_writer.make_brief()`, surfaced as a second H2 link.
