# workspace-manager — bug & design history

Narrative context moved out of SKILL.md (A7 diet). **Rules live in SKILL.md; stories live here.**
A behavioral rule (MUST / NEVER / FORBIDDEN / HARD gate) is never satisfied by an entry here —
SKILL.md keeps the rule plus a breadcrumb like `(Bug #86 — see references/HISTORY.md)`.

| ID | Date | One-line story | Rule it produced (SKILL.md anchor) | CHANGELOG |
|---|---|---|---|---|
| Bug #82 (vocative) | 2026-05-26 | Naming the Brain gave the router no signal; addressing the AI by name fell to whichever specialist's substring trigger happened to match (usually nothing). Descriptions are static at install time, so the custom brain_name could never be a positive trigger phrase. | "MUST-language gate — brain-name vocative routing" | v3.13.8.4 |
| Bug #82 (new-project) | 2026-05-26 | A bare "new project" followed by a paste of context in the same/next turn didn't match the inline-name trigger `new project [Name]`, so the turn went freeform and the project was never created. M: "a lot of times it doesn't build a new command room project until I say 'new project in command room'." The gate makes the trigger a one-way door — only the name is collected after it fires. | "MUST-language gate — new-project lifecycle" | v3.13.8.4 |
| Bug #11 (S22) | — | `entity_resolve.py` shipped v3.13.0 + hardened v3.13.6, but a live-trace showed ZERO of its three stated consumers (workspace-manager, people-crm, transcript-search) actually invoked it — the LLM substituted substring grep under time pressure. Queries worked "by luck of grep" on M's matured alias graph; new customers with empty graphs hit 4 clarifying questions per misspelled name from Day 1. | "MUST-language gate (canonical resolver dispatch)" | v3.13.7 |
| Routing-miss | 2026-05-20 | Asked 4 open-ended clarifying questions for "my conversation with Elon" when Elan was a known person — the resolver would have matched. Origin of the strict Step-5 single-question shape (4-clarifying-question shape is the regression). | "Step 5 — Ambiguity handling (strict shape)" / step-3 candidate rule | v3.13.1 |
| Truncation incidents | Apr 2026 | Hand-rolled `write_text()` / `open(..,"w")` writes produced truncated-file corruption in v2.7–v2.10.4. Evidence: `_hq/data/_backups/entities.json.pre-rewrite-20260427-223852.backup`; the Apr 28 cracks-watch fire that detected mid-file truncation; the Apr 29 bridge fire that read a stale Drive-sync view of a partial write. | "Atomic-write requirement (FORBIDDEN hand-rolled writes)" | v2.10.5 |
| Overlay bug class | — | A tracker that hadn't regenerated in 10 days reported "quiet since April 25" for threads with activity today, because MASTER_TRACKER.md is a regenerated projection, not the source. Same class the v3.11.3 morning-brief overlay fixed. | "Step 1a — Overlay events.jsonl on top of the tracker" | v3.11.4 |
| Bug #86 | — | v3.18.1 rendered the `<!-- LIVE-STATE:people -->` block into PROJECT_BRAIN but never surfaced it in the response — proposed people (e.g. an umbrella-split inference) were written but the CEO never saw the "Proposed — confirm to add" line, so the confirm-people workflow dead-ended. | "Surface the rendered block — mandatory" | v3.18.2 |
| First-go default | 2026-05-17 | The v2.10.2 default of 12 months hit 250–400K tokens on a heavy CEO's busy project — context-window pressure during the first-call demo. New default 1 month (~30–40K tokens) keeps the demo light; customers extend with `backfill` or `set first-go to N months` (cap 36). | "First-`go` lazy deep-load — default `workspace.first_go_months` = 1" | onboarding v2 |
| Bug #83 | — | New-prospect creation hand-wrote the org "same shape as [other prospect]" and routed through an `org_added`-only path, skipping the engagement edge entirely — and sometimes wrote a `stage` field that doesn't exist on an org. Deal status belongs ONLY in the engagement label. | "new prospect — HARD gate bash block" | v3.18.2 |
| Bug #91 | — | prospect→client conversion was offered by `new prospect` but never implemented — a dead promise. A prospect that closed stayed stuck at `relationship_type: prospect` with stale "proposal sent" notes and often no engagement edge. | "[Name] is now a client — conversion HARD gate" | v3.18.6 |
| Bug #72 | 2026-05-24 | Master plan §5 spec'd a `brain_name_prompt` migration for the v3.13.8 update bridge, but the shipped `shared/releases/v3.13.8.json` had 11 items and none was the brain-name prompt. Every v3.11.x → v3.13.8.x upgrade customer kept the impersonal "Command Room" framing because the prompt never fired. | "name my AI [name]" command | v3.13.8.2 |

---

## Appendix — brain_name read consumers (v3.13.8.4 personification sweep)

Reference list only; not a behavioral rule. The SKILL.md rule is the one-liner: **consumers read
`workspace.brain_name` fresh at render time via `shared/scripts/personification.py::get_brain_name(workspace_root)`
(default "Penelope" if unset); renames propagate forward only, never retroactively into prior artifacts.**

Scheduled tasks + onboarding (original v3.13.8 / v3.13.8.2 set):
- All five scheduled-task orchestrators (Morning Brief, Upcoming Meetings, Past Meetings, Inbox, Friday Wrap) — signature line
- `command-room-coach` chat intro
- `command-room-onboarding` Phase 0–6 chat copy substitutions (`<BrainName>` template token)
- `m1-backfill-orchestrator.md` recap copy

Customer-facing chat + deliverables (v3.13.8.4 personification sweep):
- `workspace-manager` session-start confirmation (`let's work` / `I'm here` / `what's going on`) — "[Name] here." framing
- `workspace-manager` `go [project]` first-response header — author the loaded brief in voice
- `workspace-manager` `end session` summary — sign off the wrap-up
- `workspace-manager` `new project` confirmation ("[Name] here — set up [X] for you")
- `morning-briefing` chat intro line (in addition to the signature)
- `call-prep` .docx cover line — "Prepared by [Name] for [M's first name]"
- `meeting-notes` acknowledgment line after processing a meeting
- `follow-up-ritual` draft signatures on outbound email drafts
- `cleanup` summary intro
- `decision-memo-composer` author byline in the .docx
- `board-pack-assembler` intro paragraph in the .docx
- `list-active` footer ("— [Name]")
