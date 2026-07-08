---
name: advisor-export
description: "Distill a person into a portable Advisor Profile — a structured read of how they think, decide, and argue — that a boardroom seat can use as a real 'guest director'. Fires on: 'forge my advisor profile', 'export my advisor profile', 'create my board persona' (high-fidelity, from your own history, shareable), 'model [name] as an advisor', 'add [name] as an advisor' (local read of a colleague from your transcripts — flagged as your read, never shareable out), 'import advisor profile', 'load advisor profile', 'show my guest bench', 'who's on my guest bench'. Does NOT fire on 'convene the board' / 'configure my board' (boardroom — the seats), 'add [name] to my contacts' / 'who is [name]' (people-crm), or 'draft an email as [name]' (email-writer). Fidelity tiers and file contract: Routing section in the body."
---

## Recommended Model

**Default: Opus.** Forging a faithful thinking-model from substrate — distilling decision heuristics, stated positions, and blind spots without putting words in someone's mouth — is judgment-heavy. Sonnet is acceptable for the mechanical `import` / `show my guest bench` modes.

## Entity-resolve + canonical-helper enforcement

This skill has name-bearing triggers ("model [name] as an advisor", "export [name]'s profile"). Before resolving any named person you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` per `shared/ENTITY_RESOLVE_PROTOCOL.md`. Fall back to substring grep ONLY if `resolve_all` returns no candidates. If the name resolves to multiple people, disambiguate before forging — never guess. All person reads/writes go through `shared/scripts/people_writer.py`; never hand-edit `entities.json`.

## Skill Boundary (v2.1)

- **Use advisor-export for:** turning a person into a portable reasoning persona (Advisor Profile) for the boardroom — forge self, model a colleague locally, export, import, list.
- **Use `boardroom` for:** actually convening and seating these personas against a subject.
- **Use `people-crm` for:** the relationship record (role, org, contact info, last interaction) — who someone is, not how they think.
- **Use `email-writer` for:** drafting in a person's writing voice — a communication clone, not a reasoning model used to argue a position.

## Writer Contract (substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`. All writes go through `shared/scripts/advisor_profile_writer.py`, which uses the atomic helpers (`atomic_write_json`, `atomic_append_jsonl`) — no raw `open(path, "w")`. New substrate file schema: `shared/data-schemas/advisor_profile.schema.json`.

**Primary writer for:**
- Local guest bench packs at `_hq/data/advisors/<slug>.json` via `write_local_advisor()` / `import_advisor()`.
- Exported shareable packs at `_hq/advisors/exported/AdvisorProfile_<Name>_<YYYY-MM-DD>.json` via `export_advisor()` (self-fidelity + shareable only — the writer refuses observed/non-shareable packs).
- Events on `_hq/data/events.jsonl`: `advisor_profile_exported`, `advisor_profile_imported`, `advisor_profile_modeled`. Sole writer of all three.

**Consumed by:** `boardroom` reads `_hq/data/advisors/*.json` (via `list_advisors()`) and the `advisor_profile_imported` / `advisor_profile_modeled` events to offer persona seats. This skill's own `show my guest bench` mode reads `advisor_profile_exported` events to show each self-profile's "shared on `<date>`" history. Every new event type therefore has a consumer.

**Reads from:** `entities.json` (person record + `workspace.user_first_name` + `brain_name`); the operator's voice profile and `decision` events for self-forge fidelity; `transcript-search` for a person's stated positions (the same compose-time mechanism `decision-memo-composer` uses); `_hq/data/advisors/*.json` for list/import dedup.

**Privacy invariant (load-bearing):** a pack travels to workspaces where local IDs are meaningless. `advisor_profile_writer.scrub_internal_ids()` strips any `person_NNN` / `project_NNN` / `org_NNN` / seq tokens at the writer boundary, and `validate_pack()` blocks a write that still contains one. Exported packs carry counts only in `source_signal_summary` — never source content.

**Conflict boundary:** sole writer of the three `advisor_profile_*` events and of `_hq/data/advisors/`. No writes to `entities.json` (people-crm owns person records) — this skill only reads them.

## What It Doesn't Do

- Does NOT export a model of someone else. Only a **self**-forged pack is shareable. A colleague you model locally (observed fidelity) is flagged as *your read of them* and is hard-blocked from export — it stays in your workspace.
- Does NOT silently scrape contacts into personas. Forging a colleague is an explicit, named request, and the resulting pack is labeled observed.
- Does NOT clone a writing voice. The pack is a reasoning model (how they decide and argue), not an email-style imitation — that's `email-writer`.
- Does NOT leak workspace structure. Internal IDs and raw source material never enter a pack; only the distilled judgment and signal counts do.
- Does NOT create or edit person records. It reads people-crm; it never writes `entities.json`.

## How to Use

```
"forge my advisor profile"            # self, high fidelity, shareable
"export my advisor profile"           # writes the shareable file to send a colleague
"model Sam Sample as an advisor"      # observed, local-only, flagged as your read
"add Sam Sample as an advisor"        # same as model
"import advisor profile <path>"       # load a colleague's shared pack into your guest bench
"show my guest bench"  /  "who's on my guest bench"
```

## How It Works

### Forge (self) — high fidelity
Read the operator's own substrate: voice/communication profile, `decision` events (to infer heuristics + risk posture), stated positions surfaced via `transcript-search`, role/org from their person record. Draft the Advisor Profile — `headline`, `mandate_default`, `decision_heuristics[]`, `priorities[]`, `risk_posture`, `known_positions[]`, `pushback_patterns[]`, `communication_style`, honest `blind_spots[]`. Show the user the draft for confirmation/edits (it's a portrait of them — they get final say). Provenance: `fidelity: "self"`, `shareable: true`, `forged_by_label` = brain name, `source_signal_summary` = counts. Persist locally via `write_local_advisor()`.

### Export (self only)
Call `export_advisor()`. The writer refuses anything not self+shareable. Surface the resulting `.json` path as a clickable link with a one-line "send this file to whoever's board you want a seat on; they run **import advisor profile**." Emits `advisor_profile_exported`.

### Model (colleague) — observed, local only
After `resolve_all` identifies the person, build the profile from **your** signal only: transcripts of meetings with them, emails, people-crm `communication_style`/notes. Be explicit about inference — `blind_spots[]` should note this is an outside read. Provenance: `fidelity: "observed"`, `shareable: false`, `workspace_origin_label` = your workspace. Persist via `write_local_advisor()` (emits `advisor_profile_modeled`). The pack is hard-blocked from export.

### Import (colleague's shared pack)
`import_advisor(path)` reads the file a colleague sent, scrubs + validates it, dedups against the local bench, and stores it at `_hq/data/advisors/<slug>.json`. Emits `advisor_profile_imported`. Tell the user it's now available as a persona seat in **configure my board**.

### List
`list_advisors()` renders the guest bench: name, role, and fidelity badge (`self`/shared vs `observed`/your-read) so the user knows how much to trust each seat. For self-profiles, it also reads prior `advisor_profile_exported` events to show when each was last shared out.

## Output

**Output guard (PL.10):** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.

- ❌ "Forged from your substrate: 214 decision events + voice corpus"
- ✅ "Built from 214 of your logged decisions and the way you actually write."

1. **Forge/model:** a chat summary of the distilled profile (headline + mandate + a few heuristics + the fidelity badge) — no internal IDs or jargon. The full pack is stored, not dumped in chat.
2. **Export:** an H2 clickable link to the shareable `.json` + the send-and-import instruction.
3. **Import/list:** a short confirmation / guest-bench list with fidelity badges.
4. The corresponding `advisor_profile_*` event appended to the substrate.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Distill a person into a portable Advisor Profile — a structured read of how they think, decide, and argue (their lens, decision heuristics, priorities, risk posture, stated positions, what they push back on) — that another Command Room can seat as a real 'guest director' in its boardroom. Two fidelities: forge YOURSELF from your own rich substrate (high fidelity, shareable as a file you send a colleague), or model a COLLEAGUE locally from your own transcripts and notes of them (lower fidelity, flagged as your read of them, never shareable out). Also imports a colleague's shared profile into your guest bench and lists who's loaded. Use when the CEO says 'forge my advisor profile', 'export my advisor profile', 'create my board persona', 'model [name] as an advisor', 'add [name] as an advisor', 'import advisor profile', 'load advisor profile', 'show my guest bench', 'who's on my guest bench'. DOES NOT fire on 'convene the board' / 'configure my board' / 'show my board' (boardroom — the bench of seats; this skill's 'show my guest bench' is the available personas, not the configured board), 'add [name] to my contacts' / 'who is [name]' (people-crm — relationship records, not thinking models), or 'draft an email as [name]' (email-writer — writing voice, not a reasoning persona).
