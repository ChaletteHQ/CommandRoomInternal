# Feature Reference — Onboarding M1 (2026-05-23)

## Features That Must Be Reflected in Onboarding

Updated table for M1's 6-phase / 13-chat architecture. Features in M1's surface where shown; features that previously fired in onboarding Chat 1 (live briefing, project deep-dive, person deep-dive, guided two-prompt run) have moved to M2 or to the operator-driven Chat 3+ follow-on arc after Chat 1 ends.

| Feature | Where It Appears (M1) |
|---|---|
| **AI naming + employee framing** | Phase 0 widget Q4 (`workspace.brain_name` captured; default "Penelope"). Read by Chat 4's opening line + every scheduled-task signature + the coach skill. |
| **Team Intelligence / _people/** | Phase 1a (auto-detect from recurring 1:1s in the connector scan), Phase 1a workspace build (profiles created). |
| **CLAUDE.md hot-cache memory** | Phase 1a workspace build (written with narration). Referenced in Chat 4 by name with a why-it-matters anchor ("the file Penelope reads at the start of every conversation"). |
| **Morning Briefing** | Operator-opened Chat 2 at Phase 1a (during the scan), firing `set up command room schedules` — registers `morning-brief` as one of the 5 M1 first-install scheduled tasks. First fire happens via Run Now in Phase 4. |
| **MASTER_TRACKER with rolling backup** | Phase 1a workspace build (tracker built live), backup infra created silently. |
| **Tiered interaction log compression** | Silent — built into person profiles. Mentioned if customer asks about data retention. |
| **Meeting Notes processing** | Operator-opened Chat 7 (Past Meetings scheduled task) first fire in Phase 4; on-demand `process the call`. |
| **Weekly Recap / Friday Wrap** | Operator-opened Chat 10 (`friday-wrap`, surfaced to customer as `weekly-recap`) first fire in Phase 4 — covers 7-day window, produces .docx + inline summary. |
| **Decision Log** | Phase 1a (created via view regeneration from events.jsonl); enriched by `cr-m1-backfill` Phase 2 extraction. |
| **Communication Profile / Brand Voice** | Phase 1a workspace build (voice scan over 60d of sent emails); Phase 2a Voice contrast (three-way prompt-AND-output proof). |
| **People CRM** | Phase 1a workspace build (PEOPLE.md populated from scan); deep dive via Phase 5 training command 2 (`tell me about [person]`). |
| **Intel Intake** | Not surfaced in M1 — customer discovers organically. |
| **Call Prep** | Operator-opened Chat 9 (Upcoming Meetings scheduled task) first fire in Phase 4 + Phase 5 training command 1 (`prep me for [meeting]`). |
| **Workspace Map (sidebar artifact)** | Customer-opened Chat 3 at Phase 1b (`install workspace map`). Refresh from manual `↻` button on the artifact. |
| **Quick Commands (sidebar artifact)** | Phase 1a (silent install after entities.json populated). |
| **Project deep-dive** | DEFERRED to M2 (`go [Project]`). |
| **Person deep-dive** | Phase 5 training command 2 (`tell me about [person]`), then on-demand thereafter. Deeper deep-dive lives in M2. |
| **Scheduled tasks (5 first-install)** | Operator-opened Chat 2 at Phase 1a — registers `morning-brief`, `past-meetings`, `inbox` (surfaced as `inbox-triage`), `upcoming-meetings`, `friday-wrap` (surfaced as `weekly-recap`). The remaining 2 (`commitments`, `pulse`) deferred to a follow-up session. |
| **cr-m1-backfill (one-shot deep read)** | NEW M1. Auto-registered by Chat 1 at Phase 1b; customer clicks Run Now to authorize. Chat 5 runs on Haiku for 5–7 min, extracts commitments/decisions/follow-ups from the last 7 days at full content depth, emits a customer-readable structured recap, then auto-disables. Orchestrator at `references/m1-backfill-orchestrator.md`. |
| **Triple beat (Mirror v1 → Voice contrast → Insights → Mirror v2)** | Chat 4 (the AI's home chat on Opus). Phase 2a delivers Mirror v1 + Voice contrast immediately. Phase 2b's Insights + Mirror v2 are user-triggered by typing `show me what's next` after the backfill completes. |
| **Compounding-loop framing** | Phase 3 in Chat 4 (literal v1 vs v2 Mirror contrast, not abstract). |
| **Run Now ritual** | Phase 4 in Chat 4. 5 manual Run Now clicks in Cowork's Scheduled section (one per registered task); each first-run produces real output the customer reads before moving on. |
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
- **Pulse scheduled task:** added in a follow-up session once accumulated workspace signal makes it useful.

---

## Principles

1. **Scan first, show second, ask third.** The scan IS the pitch.
2. **The AI is a named operator, not "your brain."** Folder = `[Name]'s Brain`; interaction = `[Name]`.
3. **Their words, not yours.** Mirror their language in every file.
4. **No empty scaffolding.** Real content or don't create it.
5. **Build fast, narrate the build.** Workspace lands in front of them in Phase 1a.
6. **One focused task per chat.** M1 distributes work across 13 chats, not stacks it in one.
7. **Three escalating beats in Chat 4.** I see you → I can produce work for you → I notice things you don't.
8. **The customer authorizes their daily rhythm.** 5 Run Now clicks in Phase 4 — they see real output before they commit.
9. **Phase 6 hands off to coach.** Chat 4 is `command-room-coach`'s permanent home from there.
10. **Privacy matters.** Phase 0 widget (Q2) captures exclusion domains; everything else respects them.
11. **Output Quality Rules apply ONLY to the Chat 1 + Chat 4 demo surfaces.** Daily-use scheduled tasks aren't subject — over-application produces bloat. Demo surfaces are one-shot; richness here is the proof, richness in daily morning briefs is noise.
12. **No wow language. No time promises. No value-math.** Strip "wow" / "in 30 seconds" / "this paid for a month's subscription" from customer-facing copy. The customer feels the wow; we don't announce it.
13. **WHY-not-WHAT for file mentions.** Every named workspace file gets a why-it-matters anchor.
