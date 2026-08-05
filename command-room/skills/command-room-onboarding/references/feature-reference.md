# Feature Reference — Onboarding M1 (2026-05-23)

## Features That Must Be Reflected in Onboarding

Updated table for M1's 6-phase architecture (scheduled-task generation stripped 2026-06 — see the scheduled-task rows). Features in M1's surface where shown; features that previously fired in onboarding Chat 1 (live briefing, project deep-dive, person deep-dive, guided two-prompt run) have moved to M2 or to the operator-driven follow-on arc after Chat 1 ends. **Onboarding registers no scheduled tasks** — the daily/weekly scheduled chats are an opt-in the customer runs separately via `set up command room schedules` after the call.

| Feature | Where It Appears (M1) |
|---|---|
| **AI naming + employee framing** | Phase 0 widget Q3 (`workspace.brain_name` captured; default "Penelope"). Read by Chat 4's opening line + every scheduled-task signature + the coach skill. |
| **Team Intelligence / _people/** | Phase 1a (auto-detect from recurring 1:1s in the connector scan), Phase 1a workspace build (profiles created). |
| **CLAUDE.md hot-cache memory** | Phase 1a workspace build (written with narration). Referenced in Chat 4 by name with a why-it-matters anchor ("the file Penelope reads at the start of every conversation"). |
| **Morning Briefing** | Not set up during onboarding. Becomes a daily scheduled chat once the customer opts in via `set up command room schedules` (post-call); also available on demand via `what's going on`. |
| **MASTER_TRACKER with rolling backup** | Phase 1a workspace build (tracker built live), backup infra created silently. |
| **Tiered interaction log compression** | Silent — built into person profiles. Mentioned if customer asks about data retention. |
| **Meeting Notes processing** | On-demand `process the call`. Becomes the Past Meetings daily chat once the customer opts into schedules. |
| **Weekly Recap / Friday Wrap** | On-demand `weekly-recap` (covers the 7-day window at full depth, produces .docx + inline summary) — this is also the deeper last-week read onboarding points the customer to in Phase 2b. Becomes the `friday-wrap` weekly scheduled chat once the customer opts into schedules. |
| **Decision Log** | Phase 1a (created via view regeneration from events.jsonl); enriched on demand as the customer logs decisions and runs `weekly-recap`. |
| **Communication Profile / Brand Voice** | Phase 1a workspace build (deep voice scan over 60d of sent emails — mechanics measured, lexicon + moves extracted, verbatim examples pulled, traits ranked, confidence flagged); proven twice in Chat 4 — Phase 2a Voice contrast (three-way memory proof) and Phase 5b Brand voice calibration proof (voice profile mirrored from `BRAND_VOICE.md`, then generic vs. voice-calibrated email side-by-side). |
| **People CRM** | Phase 1a workspace build (PEOPLE.md populated from scan); deep dive via Phase 5 training command 2 (`tell me about [person]`). |
| **Intel Intake** | Not surfaced in M1 — customer discovers organically. |
| **Call Prep** | Phase 5 training command 1 (`prep me for [meeting]`). Becomes the Upcoming Meetings daily chat once the customer opts into schedules. |
| **Workspace Map (sidebar artifact)** | Customer-opened Chat 3 at Phase 1b (`install workspace map`). Refresh from manual `↻` button on the artifact. |
| **Quick Commands (sidebar artifact)** | Phase 1a (silent install after entities.json populated). |
| **Project deep-dive** | DEFERRED to M2 (`go [Project]`). |
| **Person deep-dive** | Phase 5 training command 2 (`tell me about [person]`), then on-demand thereafter. Deeper deep-dive lives in M2. |
| **Scheduled tasks (5 first-install)** | NOT registered by onboarding (stripped 2026-06). The customer opts in after the call by running `set up command room schedules` in a fresh chat → `enable-command-room-schedules` registers `morning-brief`, `past-meetings`, `inbox` (surfaced as `inbox-triage`), `upcoming-meetings`, `friday-wrap` (surfaced as `weekly-recap`). The remaining 2 (`commitments`, `pulse`) deferred to a follow-up session. Phase 6 points the customer to it. |
| **cr-m1-backfill (one-shot deep read)** | REMOVED from onboarding (2026-06). Onboarding no longer registers a deep-read scheduled task; the equivalent last-7-days read is available on demand via `weekly-recap`. The orchestrator body at `references/m1-backfill-orchestrator.md` is retained for reference but unwired. |
| **Beats (Mirror v1 → Voice contrast → Insights)** | Chat 4 (the AI's home chat on Opus). Phase 2a delivers Mirror v1 + Voice contrast immediately. Phase 2b's Insights are user-triggered by typing `show me what's next` — computed from the 60-day metadata scan (no deep-read wait). There is no Mirror v2 deep-specifics pass in onboarding; Phase 2b points the customer to `weekly-recap` for the sharper read. |
| **Compounding-loop framing** | Phase 3 in Chat 4 (every meeting / decision / follow-up / `weekly-recap` compounds on the 60-day baseline). |
| **Run Now ritual** | REMOVED (2026-06). Onboarding registers nothing, so there is nothing to Run Now. The customer authorizes the scheduled chats themselves after opting in via `set up command room schedules`. |
| **Training prompts** | Phase 5 in Chats 11/12/13. Three commands the customer fires themselves in new chats. |
| **Historical 12-month backfill** | NOT auto-fired in M1. Customers extend per-project with `backfill [N] months on [project]` on demand. |
| **Coach handoff (command-room-coach)** | Phase 6 in Chat 4. Chat 4 becomes the customer's permanent home with their AI. Subsequent visits to Chat 4 (or coach trigger phrases anywhere) re-enter `command-room-coach`. |

---

## Post-Onboarding (Deferred Items)

These happen later via M2/M3/M4 meetings or operator-driven follow-up sessions:

- **M2 — projects + people deep-dive.** `go [Project A]` first-time deep-load; `tell me about [Person]` for richer relationship enrichment beyond what the M1 training command rendered.
- **M3 — inbox + commitments calibration.** Operator tunes the Inbox triage thresholds; the Commitments scheduled task gets added once enough workspace signal exists.
- **M4 — handoff + custom skill + value retrospective.** Customer becomes autonomous; one custom skill scaffolded for a recurring workflow.
- **Local file scan:** offered in a follow-up session via `workspace-ingest` (folder-mode).
- **Project-org-cleanup:** post-meeting customer dictates corrections to org tree / project list / people via mic. Backlog as of 2026-05-23.
- *(The Pulse scheduled task was retired in LIFECYCLE1 — never offered. Quiet-project questions live on demand under `stalled projects`.)*

---

## Principles

1. **Scan first, show second, ask third.** The scan IS the pitch.
2. **The AI is a named operator, not "your brain."** Folder = `[Name]'s Brain`; interaction = `[Name]`.
3. **Their words, not yours.** Mirror their language in every file.
4. **No empty scaffolding.** Real content or don't create it.
5. **Build fast, narrate the build.** Workspace lands in front of them in Phase 1a.
6. **One focused task per chat.** M1 distributes work across several chats, not stacks it in one.
7. **Three escalating beats in Chat 4.** I see you → I can produce work for you → I notice things you don't.
8. **The daily rhythm is opt-in, not auto-installed.** Onboarding registers no scheduled tasks; the customer turns on their daily/weekly chats when ready via `set up command room schedules` (post-call). Most palatable for letting the product run for anyone.
9. **Phase 6 hands off to coach.** Chat 4 is `command-room-coach`'s permanent home from there.
10. **Privacy matters.** Everything stays in the customer's chosen folder. Exclusion domains are no longer collected at onboarding (removed v5, 2026-06-30) — a customer sets one anytime via "add [domain] to my exclusion list" (`workspace-manager`), and every skill respects the list.
11. **Output Quality Rules apply ONLY to the Chat 1 + Chat 4 demo surfaces.** Daily-use scheduled tasks aren't subject — over-application produces bloat. Demo surfaces are one-shot; richness here is the proof, richness in daily morning briefs is noise.
12. **No wow language. No time promises. No value-math.** Strip "wow" / "in 30 seconds" / "this paid for a month's subscription" from customer-facing copy. The customer feels the wow; we don't announce it.
13. **WHY-not-WHAT for file mentions.** Every named workspace file gets a why-it-matters anchor.
