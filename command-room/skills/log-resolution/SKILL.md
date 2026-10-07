---
name: log-resolution
surfaces: cowork
slack_fallback: "No shortcut needed here — just tell me what you resolved (for example 'mark the Acme deck done') and I'll log it."
description: "Legacy-only: fires on the per-click 'log resolved:' prompts an old Command Room dashboard artifact auto-sends — 'log resolved: [id]', 'log resolved: [id] ([kind])' — and logs a thread_resolved event to events.jsonl. Current scheduled-chat widgets and dashboards do NOT route here — their ✓ clicks travel in the consolidated 'apply choices: [...]' message handled by apply-choices. Fires silently in chat — minimal response, no clutter."
---

# log-resolution

Tiny event-writer skill — silently appends a `thread_resolved` event to `_hq/data/events.jsonl` when a dashboard's ✓ done button fires its sendPrompt.

This is the v2.8.0 replacement for the v2.7.x "copy batch + paste" dismiss flow. Each ✓ done click in an artifact now sends a small chat prompt (`log resolved: <id>`); this skill catches that prompt, writes the event, and confirms with a one-line response.

## Status — legacy surface only (no live producer)

No current plugin surface emits `log resolved:`. Today's scheduled-chat widgets and dashboards send every ✓ click through the consolidated `apply choices: [...]` payload, which `apply-choices` dispatches — not this skill. log-resolution stays registered ONLY for legacy pre-v2.14 artifacts a customer may still have open (their `logResolution()` JS fires the old per-click prompt) and the rare manually typed match. If the trigger arrives, handle it exactly as specced below — but never advertise `log resolved:` as a current command.

## Trigger patterns

The artifact's `logResolution()` JS function sends one of:

```
log resolved: <id>
log resolved: <id> (<kind>)
log resolved: <id> [<kind>] from <artifact>
```

Where:
- `<id>` is the matter/meeting/commitment id (e.g., `matter_priority_3`, `m_meeting_2026_04_28_jane`)
- `<kind>` is the item type (e.g., `priority`, `meeting`, `commitment`, `inbox`)
- `<artifact>` is which dashboard fired it (rarely included, but supported)

If a CEO types something matching this pattern manually (rare), this skill still fires and logs the event.

## Behavior

**Output guard (PL.10):** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.

- ❌ "thread_resolved appended (seq 4102) via atomic_append_jsonl"
- ✅ "Done — marked it resolved."

1. Parse the trigger to extract `<id>` and optionally `<kind>`.
2. **Infer `<kind>` from the `<id>` prefix if it's missing (v3.11.4+ — REQUIRED per `references/SOURCE_OF_TRUTH.md`).** Pre-v3.11.4, artifact UIs that fired `log resolved: <id>` without the `(<kind>)` suffix on a commitment-class id silently fell through the dual-write path in step 4 below and left the commitment counted as open elsewhere in the workspace. The id-prefix inference table:

   | Id pattern | Inferred kind |
   |---|---|
   | starts with `commitment_` | `commitment` |
   | starts with `m_meeting_` or `meeting_` | `meeting` |
   | starts with `matter_` or `priority_` | `priority` |
   | starts with `inbox_` | `inbox` |
   | pure integer (e.g. `1247` — a raw event seq) | inspect the referenced event's `type` and use that |
   | anything else | `unknown` |

   If the trigger DID supply `<kind>` explicitly, use it as-is — don't override.

3. Idempotency + write, per the kind:
   - **For `<kind> == "commitment"` (Stage B, 2026-07):** go straight to the "Manual commitment-close path" section below — `commitment_state.close_commitment()` handles idempotency itself over the FULL resolved-id set (the pre-Stage-B last-200-lines window could re-close anything older than the tail).
   - **For all other kinds:** read the last 200 lines of `_hq/data/events.jsonl` to check for an existing `thread_resolved` event with the same id (idempotency — re-firing the same dismissal must not create duplicates).
4. If already resolved → respond with a one-line `"that one was already closed"` and stop. (Do NOT reply with the bare string "already done": since DONE1 that is a registered WIRE ID — the needs-your-call queue's `already done` verb — and an ack that reads back as a verb is the F-13 label-collision class.)
5. If not yet resolved → append events per the kind:
   - **For `<kind> == "commitment"`:** the close_commitment call below (plus the back-compat `thread_resolved`).
   - **For all other kinds:** write through **`commitment_state.resolve_thread()`** — THE `thread_resolved` writer (PROV1). It owns the envelope this skill used to hand-compose, appends through the gate (which auto-stamps `seq`/`ts` inside the writer lock), and stamps the close-family source pointer. Never hand-roll the JSON, never `open('a')`, never a raw `>>`:
     ```python
     from commitment_state import resolve_thread
     resolve_thread(
         workspace_root, "<id>",
         source_skill="log-resolution",
         kind="<kind-or-unknown>",
         source_artifact="<artifact-or-None>",
         # PROV1 — the ✓ click's receipt. A dashboard click has no message
         # behind it, so the pointer is the session/artifact it fired from.
         # Pass nothing and the event still lands, marked as pointing at
         # nothing — a close is never blocked for want of a pointer.
         source_ref="session:<session-or-artifact-id>",
     )
     ```
     Resulting event shape (unchanged apart from the additive pointer field):
     ```json
     {"type":"thread_resolved","ts":"<ISO-now>","data":{"id":"<id>","kind":"<kind-or-unknown>","source_artifact":"<artifact-or-null>","source_ref":"session:<id>"}}
     ```
6. Respond with a one-line `"✓ done"` confirmation. **No verbose summary, no tangent, no follow-up question.**

## Why it exists

v2.7.x dismiss flow: user clicks ✓ done → JS pushes to dismissedQueue → user clicks "Copy batch" → user pastes into chat → bot processes the batch → events written. Worked but required user discipline. Auto-rebuild every 30 min (v2.8.0 architecture) would un-dismiss anything not yet pasted.

v2.8.0 dismiss flow: user clicks ✓ done → JS sends `log resolved: <id>` → this skill catches → event written instantly → next auto-rebuild's projector reads events.jsonl and pre-filters resolved items. Persistence guaranteed; no user action required beyond the click.

## Forbidden behaviors

- **Do NOT respond verbosely.** This is a silent log. The CEO didn't ask a question; they clicked a button. Confirmation should be one line maximum.
- **Do NOT acknowledge in a way that uses tokens.** Skip the "I've logged the resolution and will continue to monitor..." padding.
- **Do NOT log duplicate events.** Always check existing events.jsonl first.
- **Do NOT use this skill to log user-typed mark-as-done commands** (e.g., "mark X as resolved" — that's a different skill, scan-for-commitments or similar). This skill handles ONLY the artifact-fired pattern.

## Manual commitment-close path (Stage B 2026-07 — REQUIRED, supersedes the v3.11.1 build-and-append procedure)

When the trigger arrives with `<kind>` of `commitment` (or the parsed kind is `commitment`), the closure goes through **`shared/scripts/commitment_state.py::close_commitment()` — the single closure path (F2)**. It writes the canonical `commitment_resolved` event so the CRU consumers (`load_open_commitments`, MASTER_TRACKER aggregation, morning-brief Step 3b counts) recognize the closure, and it owns everything this skill used to hand-roll:

- **Legacy-id normalization.** The widget embeds the commitment's `data.id` verbatim (per `shared/CHAT_ACTION_WIDGET.md`), but historic artifacts fired bare seqs — `log resolved: 86`, `seq_86`, `event_086`, `commitment_seq_86`. close_commitment resolves ALL of those to the canonical id via seq lookup. (A bare-int closure written as-is was the bare-int dead-letter class: the tombstone `"86"` matched nothing and the item stayed open forever.)
- **Loud no-match.** If the id matches no commitment, close_commitment raises `CommitmentIdError` — do NOT write anything; respond `"⚠️ Couldn't find that item — it may have been re-captured. Say 'show my list' to see what's open."` No more orphan tombstones.
- **Full-set idempotency — judged on `commitment_resolved` ONLY.** Already closed (a `commitment_resolved` event anywhere in history, not just the last 200 lines) → the result's `status` field is `already_resolved` → respond `"that one was already closed"` and stop (do NOT re-emit `thread_resolved`). A lone pre-existing `thread_resolved` for the same id (legacy artifact wrote it without the canonical closure) does NOT count as already-resolved — close_commitment still runs and backfills the canonical `commitment_resolved` so CRU consumers finally see the closure.
- **pending_review floor.** The ✓ click IS an explicit user action, so pass `user_confirmed=True`.

Procedure for `<kind> == "commitment"`:

1. Parse `<id>` from the trigger — pass it to close_commitment AS RECEIVED (canonical `cmt_<ulid>` or any legacy seq spelling; the normalizer owns the mapping, never pre-convert it yourself).
2. Call:
   ```python
   from commitment_state import close_commitment, CommitmentIdError
   result = close_commitment(
       workspace_root, "<id-as-received>",
       resolved_by="<user_person_id from entities.json>",
       evidence="manual close via dashboard",
       source_skill="log-resolution",
       user_confirmed=True,   # explicit ✓ click
       # PROV1 — a dashboard close has no message id; its pointer is the
       # click's session / artifact receipt. Omit it and the close still
       # lands: since SPEC PROVMINT1 the writer mints
       # `session:log-resolution:<now>` marked `surface_minted`, which is the
       # true grain of a dashboard click and not the bare marker any more.
       # Pass the real receipt when you have one — minted refs count apart
       # from artifact-backed ones in the coverage split.
       source_ref="session:<session-or-artifact-id>",
   )
   ```
3. `result["status"] == "already_resolved"` → `"that one was already closed"` (never the bare string "already done" — that is the DONE1 wire id), stop. `"closed"` → ALSO append the `thread_resolved` event the rest of this skill emits (kept for backwards-compat with v2.7.x consumers that still read `thread_resolved`).
4. One-line confirmation as usual: `"✓ done"`. Silent otherwise.

For all other `<kind>` values (meeting, inbox, priority — note `matter_*` ids infer to `priority` per the Step 2 table) the behavior is unchanged — only `thread_resolved` is written.

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
