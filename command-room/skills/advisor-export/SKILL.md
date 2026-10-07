---
name: advisor-export
surfaces: both
description: "Distill a person into a portable Advisor Profile — a structured read of how they think, decide, and argue — that a boardroom seat can use as a real 'guest director'. Fires on: 'forge my advisor profile', 'export my advisor profile', 'create my board persona' (high-fidelity, from your own history, shareable), 'model [name] as an advisor', 'add [name] as an advisor' (local read of a colleague from your transcripts — flagged as your read, never shareable out), 'import advisor profile', 'load advisor profile', 'show my guest bench', 'who's on my guest bench'. Does NOT fire on 'convene the board' / 'configure my board' (boardroom — the seats), 'add [name] to my contacts' / 'who is [name]' (people-crm), or 'draft an email as [name]' (email-writer)."
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

**Reads from:** `entities.json` (person record + `workspace.user_first_name` + `brain_name`); the operator's voice profile and `decision` events for self-forge fidelity; `transcript-search` for a person's stated positions (the same compose-time mechanism `decision-memo-composer` uses); `_hq/data/advisors/*.json` for list/import dedup. **Every events.jsonl read in this skill goes through the org-scoped reader, never a raw load** (PGUARD1 — packs travel to OTHER workspaces, the same external-audience severity as the board pack): `from events_io import load_events_org_scoped; events, skipped = load_events_org_scoped(workspace_root)`. The account-scope mask and personal-lane drop apply by design, so a reclassified personal account's history or a personal-lane row can never inform an exported profile.

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

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file — and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised — a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Distill a person into a portable Advisor Profile — a structured read of how they think, decide, and argue (their lens, decision heuristics, priorities, risk posture, stated positions, what they push back on) — that another Command Room can seat as a real 'guest director' in its boardroom. Two fidelities: forge YOURSELF from your own rich substrate (high fidelity, shareable as a file you send a colleague), or model a COLLEAGUE locally from your own transcripts and notes of them (lower fidelity, flagged as your read of them, never shareable out). Also imports a colleague's shared profile into your guest bench and lists who's loaded. Use when the CEO says 'forge my advisor profile', 'export my advisor profile', 'create my board persona', 'model [name] as an advisor', 'add [name] as an advisor', 'import advisor profile', 'load advisor profile', 'show my guest bench', 'who's on my guest bench'. DOES NOT fire on 'convene the board' / 'configure my board' / 'show my board' (boardroom — the bench of seats; this skill's 'show my guest bench' is the available personas, not the configured board), 'add [name] to my contacts' / 'who is [name]' (people-crm — relationship records, not thinking models), or 'draft an email as [name]' (email-writer — writing voice, not a reasoning persona).

## The Access preamble this file refers to

Propagated by `scripts/dev/propagate_access_preamble.py`; the canonical copy is in `shared/WORKSPACE_ACCESS.md`.

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
```
