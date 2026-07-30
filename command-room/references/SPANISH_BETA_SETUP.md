# Spanish Beta Setup — Running a Command Room in Spanish

**Version:** 2.0 (2026-07-15) — updated for `command-room-es` on core v4.6.3.
**Status:** Beta runbook. With this build the language switch is **productized**
(onboarding question + `language.json` + `{{LANGUAGE_PREF}}` slot). The manual
steps below still work as a fallback or for retrofitting an existing workspace.
**Companion to:** [`SPANISH_BUILD_PLAN.md`](SPANISH_BUILD_PLAN.md), [`claude-md-template.md`](claude-md-template.md), [`../shared/VOICE_CALIBRATION.md`](../shared/VOICE_CALIBRATION.md)

---

## What activates when a workspace goes Spanish

Two layers, both additive (English is never removed):

1. **LLM output layer** — briefings, meeting notes, and chat come back in
   Spanish, driven by the `Idioma / Language` line in the workspace `CLAUDE.md`
   Preferences. Outbound drafts are the exception: they **reply in the language
   of the thread** (an English thread gets an English draft); Spanish is the
   default only when there is no thread to match.
2. **Deterministic scanner layer** — the commitment/decision/prospect phrase
   matchers become `English ∪ Spanish` and entity resolution folds accents
   (José/Jose, Peña/Pena). Driven by `_hq/data/skill_config/language.json`.

Claude reads Spanish natively, so **ingestion already works with zero setup** —
Spanish transcripts and emails classify, summarize, and extract commitments/
people correctly even on an English install. The two layers above are what make
the **output** and the **deterministic auto-resolve** paths Spanish-aware.

---

## The productized path (this build)

Onboarding is seed-first and opt-in: a pre-call `ONBOARDING_SEED.json` carrying
`language: "es"` configures both layers with no question asked, and with no seed
the one-line language question is asked **only when the customer's own material
signals Spanish** (their sent mail, transcripts, or the operator's brief). Pick
**Español/bilingüe** and both layers are configured: onboarding writes
`language.json` via the atomic writer and fills the `{{LANGUAGE_PREF}}` slot.
Default English writes nothing — the install stays byte-identical. Nothing else
to do.

To flip an already-onboarded workspace, tell its Command Room:
*"cambia mi Command Room a español"* / *"switch my Command Room to Spanish"* — or
apply the two manual steps below.

---

## Manual path (fallback / retrofit)

### Step 1 — Turn on the scanner overlay

Create `_hq/data/skill_config/language.json` (atomic write; or just ask the
workspace to *"add Spanish to my language config"*):

```json
{ "version": 1, "languages": ["en", "es"], "last_writer": "manual-setup" }
```

### Step 2 — Add the language preference to `CLAUDE.md`

Paste into the `## Preferences` block:

```markdown
- **Idioma / Language:** Respond in Spanish (Mexican / es-MX). Briefings, meeting
  notes, and chat replies in Spanish. **Outbound drafts match the recipient:
  reply in the language of the thread** — an English thread gets an English
  draft, a Spanish thread a Spanish one; Spanish is the default only when there
  is no thread to match (fresh outreach to a Spanish-speaking contact). Keep
  English product names, proper nouns, and established technical terms as-is —
  do not force-translate. Mixing in English is fine when the Spanish would be
  awkward or a term has no clean translation.
```

### Step 3 — Add the Spanish voice guard (recommended)

The voice system (`../shared/VOICE_CALIBRATION.md`) is **English-only**, so its
banned-phrase list will not catch robotic Spanish. Add this alongside the
language line:

```markdown
- **Voz en español (drafts):** Avoid stiff, machine-translated Spanish business
  clichés unless the recipient actually writes that way. Watch for and rewrite:
  "Quedo atento/a a sus comentarios", "No dude en contactarme", "Espero que se
  encuentre muy bien", "Por medio de la presente", "Reciba un cordial saludo",
  "Sin otro particular". Prefer direct, warm, lead-with-the-point Spanish. Match
  usted vs. tú to the recipient — default to usted for external/business contacts.
```

---

## Test plan (5 steps)

1. **Ingest** — paste a real Spanish meeting transcript. Confirm `meeting-notes`
   returns Spanish notes and extracts commitments + people.
2. **Auto-resolve (scanner layer)** — with `language.json` active, feed the CRU a
   Spanish completion ("ya te lo envié") against an open commitment and confirm it
   auto-resolves. This is the deterministic overlay, not just the LLM.
3. **Briefing** — run a morning briefing. Confirm it comes back in Spanish.
4. **Inbox** — point `inbox-triage` at a Spanish email. Confirm classification +
   summary in Spanish and any commitment captured.
5. **Draft (the weak spot)** — draft a reply to a Spanish email. Read the tone
   critically: this is where English-only voice calibration shows. Note stiff
   phrasing; those notes drive whether a full Spanish voice layer is worth building.

---

## Known limitations (watch during the beta)

- **Voice calibration is English-only.** Spanish drafts function but aren't
  polished against Spanish LLM tells. Step 3 partially covers this.
- **Banned-phrase list does not fire in Spanish** (matched against English strings).
- **`es.json` is pre-GA** — a solid Mexican-Spanish draft, native review pending.
- **`pursuit_only` markers are inert** until the core reconnects `_PURSUIT_ONLY`.
- **Formality (usted/tú) and regional variant** rely entirely on the Preferences
  lines above — not modeled in code.

---

## If the beta succeeds — productization checklist

1. Native business-Spanish review + expansion of `es.json`.
2. Promote the Spanish voice guard into a proper per-language companion to
   `VOICE_CALIBRATION.md`, wired so writing skills consult it when the workspace
   language is non-English.
3. Decide whether view files (`MASTER_TRACKER.md`, etc.) localize or stay
   English-internal with Spanish only at the surface.
4. Reconnect `pursuit_only` if/when the core reuses the pursuit gate.
