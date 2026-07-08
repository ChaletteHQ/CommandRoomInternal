# First-Run Personalization Protocol (SPEC FRP1)

Every major skill's FIRST fire ends with 2–3 one-tap decisions that make the skill
personally theirs — delivered **output-first, always**, stored in the existing
`skill_config` layer, re-tunable forever. The first-run moment must feel like the
product getting smarter, never like a settings form.

`stalled-projects/SKILL.md` is the shipped **Reference Implementation** (v3.14.1) this
protocol generalizes. When in doubt, copy its shape.

---

## The storage layer (already exists — do NOT rebuild)

`shared/scripts/skill_config_writer.py`, path `_hq/data/skill_config/<skill>.json`:

- `is_configured(ws, skill)` — has this skill been configured? **Gates the first-run block forever.**
- `save_skill_config(ws, skill, config, *, is_reconfigure=None, origin=None)` — atomic write
  + emits `skill_first_run_configured` / `skill_reconfigured` with `data.origin`.
- `get_config(ws, skill, defaults)` — **the read path.** Deep-merges the saved config OVER
  `defaults`, so a v+1 skill that adds a decision never breaks an old config (new key falls
  back to its default; saved choices honored). Always read config through this.
- `wipe_skill_config(ws, skill)` — reset; next fire is a first-fire again.

`origin` values (on the event, for usage-report/coach to read): `first_fire_defaults`
(silent default acceptance) · `first_fire_override` (changed a knob at first fire) ·
`m1_batch` (set during onboarding) · `tune` (later reconfigure) · `drift_reoffer`
(accepted a re-offered knob). Default is auto: `first_fire_defaults` on first fire,
`tune` on reconfigure.

---

## The 4 modes (dispatch, lifted from stalled-projects)

Every adopting skill carries these four modes. The trigger decides which fires:

1. **Detect** — the normal fire. On the FIRST fire only: `save_skill_config(ws, skill,
   DEFAULTS)` immediately (defaults are applied-and-persisted, not pending), produce the
   real output, THEN append the first-run block (below). `is_configured` gates the block —
   it appears exactly once, ever.
2. **Show settings** — `show [skill] settings` → render the current config in plain English
   (no questionnaire), each value with its options.
3. **Tune** — `tune [skill]` → the pre-filled re-questionnaire (current value marked), OR a
   freeform natural-language tune ("be more aggressive" → the skill's mapping table lowers
   the relevant threshold). `save_skill_config(..., is_reconfigure=True)` → re-render.
4. **Reset** — `reset [skill] to defaults` → `wipe_skill_config` → next fire is a first-fire.

---

## Show-then-tune (STT) is the default. Ask-first (AF) is the narrow exception.

**STT (default):** apply defaults, show the output, offer one-tap changes AFTER. Used for
anything that only changes what the user **reads**. Never blocks the first output behind a
question (CONTRACT Rule 17, speed over perfection).

**AF (reserved for irreversible / outward-acting defaults only):** ask ONE question, only on
the first relevant request, with a working default-escape. The entire AF class:
- **email-writer draft posture** — gates whether anything auto-queues to Gmail (outbound). Closes the long-standing CLAUDE.md "draft posture: TBD".
- **follow-up-ritual send timing** — gates outbound email.
- **contract-review standard-terms** — already AF; keeps its skip-escape.
- **workspace-manager relationship_type** — already AF; permanently shapes the entity graph.

Everything else is STT. If you're unsure, it's STT.

---

## The render — transport split (resolves CHAT_ACTION_WIDGET MUST-NOT rule 5)

**On-demand skills:** a 2–4 line footer + micro-widget after the output —
> *"First time doing this for you. I set 3 defaults: **[A]** · **[B]** · **[C]**. Tap any
> to change it, or say 'tune [natural skill name]' later."*

**Footer language rules (PL.8 2026-07-02 — the family had drifted; this is the one shape):**
- **"I set N defaults"**, never "I made N calls" — "calls" collides with phone/meeting vocabulary
  (call-prep's old footer read "First time prepping you. I made 2 calls" — about a meeting prep).
- **Each default described in plain English**, never config-key or jargon shorthand: "how eagerly
  I discard junk" not "discard aggressiveness"; "formal tone (for outside readers) · signed close"
  not "external-formal register · signed"; "flag someone after twice their usual gap, or 30 days"
  not "2× cadence + 30-day threshold".
- **The tune hint uses the natural spoken form** ("tune the dormant scan", "tune meeting notes"),
  verified to route; the hyphenated skill ID stays an accepted trigger but is never advertised.
- **stalled-projects' footer is the reference implementation** — warm, lists the defaults in
  plain words, ends with "or just tell me what you'd change and I'll figure out which setting
  that maps to."

**Scheduled orchestrators:** the choices ride as `fr1`/`fr2`/`fr3` items in a **"Make this
yours"** section at the BOTTOM of the existing all-batch widget — NO second surface (that's
what would violate MUST-NOT rule 5).

Each decision is a fixed-option button row with the **saved default rendered in a "current"
visual state** — a DOCUMENTED exception to the no-preselect rule (it's not a pending
selection, it's the already-saved state; the buttons are an override surface). The exception
is written into `shared/CHAT_ACTION_WIDGET.md` itself. Tap → standard apply-choices
`{n:"fr1", action, sub?, input?}` payload → `save_skill_config(..., is_reconfigure=True,
origin="first_fire_override")` → one-line ack ("Done — tomorrow's brief runs full-detail.").
Free-text is never required; `[text]` only for optional extras (e.g. a VIP-sender add).

---

## The per-skill decision catalog (max 3 decisions, max 4 options each)

More than 3 decisions, or more than 4 options, belongs in freeform tune — not the first-run block.

| Skill | Decisions (default first) | Mode |
|---|---|---|
| email-writer | **draft posture: show-first / auto-queue** · sign-off · length | posture AF, rest STT |
| morning-briefing | depth · what-leads · going-quiet on/off | STT (fr-items) |
| inbox-triage | discard aggressiveness · seed top-5 VIPs · default action | STT |
| call-prep | depth · auto-fire timing | STT |
| meeting-notes | commitment capture silent/confirm · verbosity · new-person handling | STT |
| follow-up-ritual | **send timing** · recipient default | timing AF, rest STT |
| weekly-recap | theme-led / numbers-led · internal-backlog split/external | STT |
| dormant-customer-scan | threshold · revenue weighting · watch-list | STT |
| stalled-projects | (Reference Implementation) | STT |
| decision-log | auto-log / confirm · revisit reminders | STT |
| commitments orchestrator | group by person/project · chase tone | STT (fr-items) |
| memo / one-pager | default register · signed/unsigned | STT |

---

## Lifecycle

- **Trigger family** (in every adopting skill's description): `tune [skill]` ·
  `show [skill] settings` · `reset [skill] to defaults`. FUZZY_ROUTER carries one generic
  rule: bare `tune X` → skill X.
- **Freeform tune is first-class** — each skill carries a natural-language → config mapping
  table (per the stalled-projects precedent).
- **Un-tuned high-use** (coach / insight-generator, monthly): a skill with >N fires whose
  only config event has `origin: first_fire_defaults` gets ONE offer EVER — *"You've used
  inbox-triage 30 times on factory settings — want the 60-second tune?"*
- **Override-drift** (cleanup, weekly): config > 6 months old AND ≥ 5 contradicting signals
  (corrections rows changing the configured sign-off; apply-choices repeatedly overriding a
  configured default) → emit a `note` event; the next coach session re-offers THAT KNOB only.
  **Cleanup is READ-ONLY on prefs — it never writes config, only the re-offer note.**
- **Reset** → wipe → next fire is a first-fire again.
- **Multi-workspace:** prefs are per-workspace, full stop (a holding-co operator legitimately
  wants different inbox aggressiveness per entity).

---

## Collision rules (encoded in triggers.yaml)

- `tune`/`reset` vs change-schedule / enable-command-room-schedules: the **object**
  disambiguates. Scheduled-task names → schedule skills; skill names → tune. The dangerous
  phrase is `reset morning-brief` (task AND skill): bare `reset [task-name]` → schedules;
  `reset [skill] to defaults` (**suffix required**) → prefs. Both directions are tested.
- The no-preselect exception lives in `CHAT_ACTION_WIDGET.md`, not only here.
- The footer-vs-fr-item split (on-demand footer / orchestrator fr-items) is stated in BOTH
  this doc and CHAT_ACTION_WIDGET.md.

---

## Anti-settings-form guardrails (why this doesn't feel like a form)

Output-first always · "I set 3 defaults" framing (the product decided, you adjust) ·
one-tap only · rendered exactly once ever · re-render-after-tune where cheap. If a first-run
block reads like a questionnaire the user must complete before getting value, it's wrong —
the value already shipped above it.

---

## Adoption status

`skill_config` is adopted by **stalled-projects** (the Reference Implementation). The per-skill
adoption of the catalog above proceeds per SPEC FRP1 §5 (email-writer / morning-briefing /
inbox-triage first, then the rest). The storage + read path + `origin` + the `fr*` dispatch
rails are complete (S1); each skill adopts by adding its DEFAULTS, the first-run block, the
freeform tune table, and the trigger family to its SKILL.md.
