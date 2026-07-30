# Command Room — Spanish (bilingual) build plan

**Version:** 2.0 (2026-07-15) — **rebuilt on current core (v4.6.3)** for the
`ChaletteHQ/CommandRoomES` marketplace. Supersedes the 1.0 as-built (2026-06-03),
which was stranded on the retired `chaletteholdings/commandroom` branch at core
v3.18.13 and never migrated forward. This document is the as-built record for the
current tree.
**Source base:** `ChaletteHQ/CommandRoomInternal` core (v4.6.3), **core only** —
Internal's private `command-room-internal-custom` layer is not carried into ES.
**Companion to:** [`SPANISH_BETA_SETUP.md`](SPANISH_BETA_SETUP.md), [`../shared/VOICE_CALIBRATION.md`](../shared/VOICE_CALIBRATION.md)

---

## Principles (unchanged from 1.0)

1. **English ships native and unchanged.** No English install changes behavior by
   a single byte. Spanish is inert data until a workspace opts in. Proven by the
   battery: all 191 English suites stay green, and `run_lexicon_overlay_test.py`
   asserts the no-config path returns each English constant object *by identity*.
2. **Bilingual, not Spanish-only.** Activation is *additive*: when `es` is on,
   every phrase list becomes `English ∪ Spanish`. A workspace processes mixed
   English/Spanish email and meetings. There is no "Spanish replaces English" mode.
3. **Opt-in via onboarding.** A single language question lights up both layers —
   the LLM output language (via `CLAUDE.md` Preferences) *and* the deterministic
   scanners (via `language.json`).
4. **Separate repo.** Ships as `command-room-es` from `ChaletteHQ/CommandRoomES`,
   installed by beta users instead of / alongside the English `command-room`.
   Production marketplace untouched.

---

## Config schema (the activation signal)

Reuses the existing convention (`_hq/data/skill_config/<name>.json`). New file,
written by onboarding through the atomic writer:

```
_hq/data/skill_config/language.json
{
  "version": 1,
  "languages": ["en", "es"],          // "en" always implicitly present; order = priority
  "last_writer": "command-room-onboarding"
}
```

- **Absent file ⇒ English-only** (production default; existing workspaces unaffected).
- `languages` is a list so "bilingual" is the literal default for a Spanish
  install, and `fr`, `pt`, etc. are a new pack file later with **no code change**.

---

## Shipped data: inert lexicon pack

```
shared/lexicons/es.json   (version 2 — Mexican-Spanish business pass)
```

Pure data, loaded by nothing unless `language.json` names it. Mirrors the exact
English constants it extends, keyed by scanner:

- `cru_match`: `completion_phrases`, `schedule_shift_phrases`, `new_ask_phrases`, `scheduling_phrases`
- `decision_match`: `completion_phrases`, `reversal_phrases`
- `prospect_conversion`: `conversion_markers`, `pursuit_only` *(inert — see note below)*
- top-level `stopwords`, plus `accent_fold: true`

---

## The loader: `shared/scripts/lexicon.py`

One small module. English-only path is a single cwd→root self-locate (memoized) +
a `language.json` existence check + early return of the caller's English default
⇒ **zero measurable cost and zero behavior change for production.** Packs and
active-language lists are cached per process. Public API:

```
load_lexicon_terms(scanner, key, english_default, workspace_root=None) -> same type as english_default
stopwords(english_default, workspace_root=None)                        -> merged set (folded when active)
accent_fold_enabled(workspace_root=None) -> bool
fold_accents(s) -> str        # NFKD strip combining marks; José -> jose
```

Return type mirrors the English default (`tuple`/`frozenset`), so every existing
`phrase in CONST` / set-membership call site is unaffected. English terms always
lead and are never dropped.

---

## Scanner wire-ins (the four scanners, current line-independent hooks)

Each change replaces the *use* of a hardcoded constant with a call through the
loader. The English constants stay in the files verbatim as the defaults, and
each scanner imports `lexicon` under a `try/except` so a missing module can never
change English-native behavior.

| File | Hook | Overlay |
|---|---|---|
| `shared/scripts/cru_match.py` | `_tokenize` + `detect_completion_signal` / `detect_schedule_shift_signal` / `detect_new_ask_signal` / `detect_scheduling_intent` | merged stop-words + accent-fold in the tokenizer; merged phrase lists in the four detectors (via `_phrases()`) |
| `shared/scripts/decision_match.py` | `detect_completion_signal` / `detect_reversal_signal` | merged phrase lists (via `_phrases()`); inherits the tokenizer overlay through `from cru_match import score_match` |
| `shared/scripts/prospect_conversion_detector.py` | `_has_conversion_language` + snippet select | merged `conversion_markers` (via `_conversion_markers()`) |
| `shared/scripts/entity_resolve.py` | `_normalize` | accent-fold when active; no-op + gated on ASCII/English |

**Note — `pursuit_only` is inert in v4.6.3.** The core's `_PURSUIT_ONLY` constant
is defined but no longer consumed by any code path, so the `pursuit_only` entries
in `es.json` are forward-compatible only — they activate if a future version
reconnects that gate. Flagged rather than silently shipped as if live.

---

## Onboarding + template

- `references/claude-md-template.md` — added the `{{LANGUAGE_PREF}}` slot to the
  `## Preferences` block. **English default = empty** (line collapses; CLAUDE.md
  byte-identical to English-native).
- `skills/command-room-onboarding/SKILL.md` — added a fenced **Language (Spanish
  beta)** step to the CLAUDE.md-generation beat (body only, no frontmatter/
  description change → no G11 budget impact). Default English is a no-op. Español
  writes `language.json` (atomic) and fills the slot with the `Idioma` + `Voz en
  español` lines. Honors a seed `language` field as anchor truth.

LLM-driven extraction (meeting-notes, inbox-triage commitment/decision scanning)
already works in Spanish with no pack — Claude reads Spanish natively.

---

## Tests

`tests/run_lexicon_overlay_test.py` (auto-discovered by `run_all.py`, unit tier):
- Loader fast path returns the English default **by identity** when no config.
- Active path merges `English ∪ Spanish`, preserves return type, never drops English.
- Stop-words merged + folded; `fold_accents` (José→jose, ASCII no-op).
- Wired scanners: Spanish fires **only** when `es` active; English **always** fires.
- `entity_resolve._normalize` folds accents only when active.
- **Regression guard:** English-only tokenizer keeps accents and does NOT apply
  Spanish stop-words — proves English-native is untouched.

Full battery: **192 passed, 0 failed** (191 base + this suite; the pre-existing
MC3 hardcoded-future-date test time bomb was fixed to a date-relative fixture —
see below).

---

## Rebasing onto a new core (the update recipe)

This repo is **not** in `cr1/_chalette/clients.json`, so it does **not** receive
the production fan-out. It is rebased by hand. Registering it in the fan-out
as-is would destroy the overlay: `promote_core_to_clients.py` does
`rmtree(command-room/)` then `copytree(core)`, and `core_skill_overrides`
protects whole **skill directories** only — it cannot protect `shared/` files or
the in-place scanner hooks.

To move the Spanish build to core version `X.Y.Z`:

1. **Clone cr1 at the release tag** into scratch (a real `git clone`, never a
   bare copy — several guards read `.git` and go falsely red without it; never a
   worktree either, G18's overlay is untracked and silently skips).
2. **Positive-control it first.** Run `python tests/run_all.py --tier guard` on
   the untouched clone and write the number down. Anything that fails later is
   yours only if this baseline was green.
3. **Copy the five additive files** in: `shared/lexicons/es.json`,
   `shared/scripts/lexicon.py`, `tests/run_lexicon_overlay_test.py`,
   `references/SPANISH_BETA_SETUP.md`, `references/SPANISH_BUILD_PLAN.md`.
4. **Re-apply the six in-place hooks** — see *Scanner wire-ins* above for the
   exact shape. Each scanner needs **both** halves: the guarded
   `try: import lexicon as _lex` block **and** the call-site rewiring. Missing
   the import block in `entity_resolve.py` is the easy mistake — it fails as
   `NameError: _lex is not defined` across ~10 unit suites, not at the edit site.
   Also: `references/claude-md-template.md` (`{{LANGUAGE_PREF}}` after
   `{{SCHEDULE_PREFS}}`) and the language step in
   `skills/command-room-onboarding/SKILL.md`.
5. **Check the hooks still have somewhere to attach.** Confirm the phrase-list
   constants and call sites named in *Scanner wire-ins* still exist, and sweep
   for **new** phrase-matching detectors the overlay does not cover yet
   (`grep -lE "for (phrase|m|marker|term) in [A-Z_]{4,}" shared/scripts/*.py`).
6. **Run all three tiers** (`guard`, `unit`, `runtime`) separately — a red guard
   makes the combined run fail fast and print no total.
7. **Prove English is untouched by differential**, not by assertion: stash the
   overlay, run the `unit` tier, unstash, run it again. The counts must differ by
   exactly the one added overlay test.
8. **Prove Spanish actually fires.** A workspace is only a workspace if
   `_hq/data/entities.json` exists — without that anchor `find_workspace_root`
   raises, the loader falls back to English, and every Spanish assertion quietly
   returns `False` while looking like a real result.
9. **Build the shipped payload with the contract, not by hand:** import
   `scripts/payload_contract.py` from the cr1 clone and use its
   `copytree_ignore`. It is what drops `tests/` and
   `references/PRIVACY_POLICY.md` — both of which carry real names and must
   never reach a client repo.
10. **Set identity last:** `.claude-plugin/plugin.json` → name `command-room-es`,
    version = the core version. Re-check that no plugin-prefix references break
    under the rename (`grep -rn "command-room:" skills/ shared/ references/` —
    only `command-room://schemas/…` `$id` URIs are expected, and those are fine).

## Status — as built (2026-07-15)

- ✅ Loader + inert `es.json` (v2), all four scanners wired, onboarding language
  step, `{{LANGUAGE_PREF}}` slot, overlay test. Battery **192 green**; English
  suites unchanged.
- ✅ Zero-cost English path (self-locate memoized by cwd; identity return on the
  no-config fast path).
- ✅ Fixed an **inherited** defect: `run_v46_mc3_slack_capture_test.py` hardcoded
  a "future" due date (`2026-07-11`) that had passed; made both fixtures
  date-relative. **Upstream `CommandRoomInternal` still carries this** — merge the
  same fix there.

## Status — rebased onto core v5.3.0 (2026-07-28)

- ✅ Re-ported onto **v5.3.0** from v4.6.3 (eleven releases). All six hook sites
  survived the drift; `decision_match.py` was unchanged upstream, `cru_match.py`
  and `entity_resolve.py` had moved substantially, so the hooks were re-applied
  to the new files rather than the old files carried forward.
- ✅ Battery **304 green / 0 red** across all three tiers (guard 35, unit 258,
  runtime 11).
- ✅ **English proven unchanged by differential:** pristine v5.3.0 unit tier =
  257 passed; with the overlay = 258 passed, 0 failed — exactly the one added
  overlay test, every pre-existing suite identical.
- ✅ **Spanish proven live**, not just unit-tested: with `language.json` active,
  `ya lo envié` → completion, `nos decidimos por` → decision, `firmamos el
  contrato` → conversion, `José Peña` → `jose pena`, Spanish stopwords filtered
  — while English detection kept working in the same workspace.
- ✅ Payload now built through `payload_contract.copytree_ignore`, so `tests/`
  and `references/PRIVACY_POLICY.md` no longer ship (the pre-v5.2.1 build sent
  both to the client; the name denylist inside `tests/` carries real names).

## Open items

1. **Native review of `es.json`** — phrase lists are a solid Mexican-Spanish
   first/second draft; a native business-writing pass is still wanted before GA.
2. **`pursuit_only` reconnection** — inert until the core reuses `_PURSUIT_ONLY`.
   Re-confirmed still unused at v5.3.0.
3. **Detector coverage has drifted.** The overlay covers the four scanners it was
   built for. Five phrase-matching detectors have since been added to the core
   and are English-only: `deal_signal_detector`, `entity_signal_detector`,
   `org_value_detector`, `person_backlog_sweep`, `exemplars` (plus
   `email_outcomes` and `voice_tell_detector`, which predate the overlay and were
   never covered). These degrade quietly — less detection on Spanish text, no
   misfires. Extending `es.json` to cover them wants the same native pass as (1).
4. **The overlay's durable home is cr1 core, not this repo.** It is inert for
   English by construction — a guarded import and a config-gated data load — so
   nothing about it needs to live in a fork. Folding it into core would end the
   hand-rebase, let this repo join the fan-out, and make Spanish a per-workspace
   setting any client could turn on. Until then, follow the rebase recipe above.
3. **Voice calibration is English-only** — Spanish drafts rely on the `Voz en
   español` Preferences guard, not on `VOICE_CALIBRATION.md`. Promoting a
   per-language banned-phrase layer is the productization step if the beta lands.
