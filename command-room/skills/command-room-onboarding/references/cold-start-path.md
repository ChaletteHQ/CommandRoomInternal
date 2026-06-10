# Cold Start Path (No Connectors Available)

Adapted for onboarding-v2 (2026-05-17). Same 5-step structure as the connected flow; Step 2's connector-derived sub-beats become a guided interview.

If no tools are connected and the customer chose to proceed without them:

1. **Step 1 runs unchanged** — the v3.4.1+ Step 1c setup widget (role / day-to-day / email exclusions / timezone) renders identically; cold start has no connector dependency, so all 4 items work the same way.
2. **Step 2 — Scan becomes Scan-by-interview.**
   - 2a connector inventory still fires: surface the "Not detected: Gmail, Calendar, Slack, Drive, [transcript source]" line explicitly so the customer knows what they're working without.
   - 2b extract is skipped (nothing to extract).
   - 2c orientation beats still run (orientation is connector-independent).
   - 2d analyze + classify becomes a conversational interview. Pull the org + project + people structure verbally instead of from connector signals. Use the interview prompts below:
     - *"Tell me about your world right now. What's the stuff that follows you home — projects, deals, people you're juggling?"*
     - For each named project: *"Active, exploring, or on pause?"*
     - *"Which of these are high-stakes?"*
     - *"Who are the key people in your world? Names + roles + what they do for you."*
     - *"Anyone you talk to weekly or more? Those become your team layer."*
   - 2e find one specific finding — adapt: since there's no scan data, ask: *"What's one thing that's bothering you right now that you'd want me to track from day one?"* Capture as the finding for Step 3a.
   - 2f rank by signal density — adapt: pick the top 3 projects + top 3 people from what the customer named in 2d, by their own emphasis ("which feels most urgent" / "who do you talk to most").
   - 2g skipped (Quick Commands install moved to Step 4d anyway).
3. **Step 3 — Reveal compressed.**
   - 3a opens with the interview-captured finding from 2e.
   - 3b + 3c show the org tree and project list the customer just told you, written to entities.json.
   - 3d voice contrast → **Branch C (no data)** by default since there are no sent emails to calibrate from. Surface the explicit defer message: *"I don't have enough sent emails yet to calibrate your voice. As you write your first emails through me, I'll lock onto your voice and run the side-by-side then."* Skip the three-way render.
   - 3e save profile silently — write skeleton COMMUNICATION_PROFILE.md + BRAND_VOICE.md with neutral professional defaults, flagged for refinement.
4. **Step 4 — Build workspace files** runs unchanged. Files get seeded from interview answers instead of scan data.
5. **Step 5 — Built summary + handoff seed** runs unchanged, but 5b's deep-dive candidates come from the customer-named top 3 from 2f rather than event-density ranking.
6. **Sell the connectors at the close (NEW v2 cold-start tail):** *"Right now I'm working from what you told me. When you connect your email and calendar, the system jumps forward months in one day. Everything I couldn't scan today — contacts, meeting history, open threads — floods in automatically. Say `re-scan` after you connect anything and I'll absorb it."*

The cold-start flow still ends with a built workspace + scheduled-task threads (operator opens Chat A at Step 2c2, v3.4.1+, in parallel with the interview) + a handoff seed for the Chat 3+ demo arc. The demo arc itself is thinner without connector data — focus on `go [project]` for the customer's named projects + `tell me about [person]` for the customer's named key people, both of which work against the entities.json seed data even with no event history.
