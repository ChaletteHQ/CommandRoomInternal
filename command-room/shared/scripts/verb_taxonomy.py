"""Canonical action-verb taxonomy — ONE table, every widget renders from it.

v4.5.2 S2 (F-59 — M directive "clean this all up"; F-13 P2a; F-58; F-17).

The dogfood found the same event wearing two names on different surfaces
(Resolved vs Done, Push to vs Defer), two mute verbs that never stated their
duration (Skip = 24h, Snooze = 3d), and a spec'd verb (`promote`) that
rendered nowhere. This module is the fix: one machine-readable row per
canonical action verb — wire id, THE display label, what it dispatches,
where it renders, and its mute duration when it is a mute.

Contract:

- `chat_output_renderer` builds `CANONICAL_ACTIONS` and every button's
  display label FROM this table. Nothing renders a verb that has no row;
  no surface may relabel a verb locally.
- Prose is the same vocabulary: a SKILL.md that names an action names the
  row's `verb`, letter for letter (F-13 P2a — users looked for a "done"
  button that didn't exist because chat prose and button labels diverged).
- Mute verbs SAY their duration on the button ("Snooze (1 day)",
  "Not relevant (60 days)", "Never track (permanent)") — no more
  one-way-door clicks with invisible TTLs.
- Wire ids (`action_id`, the `data-action` attribute + apply-choices
  payload token) are FROZEN for back-compat with in-flight widgets and the
  dispatch handlers. Renames happen at the display layer only: `resolved`
  displays "Done", `push to [date]` displays "Defer", `skip` displays
  "Snooze (1 day)". apply-choices keeps dispatching on the wire id.

Adding a verb: add a row here + a handler in skills/apply-choices/SKILL.md
+ (if it writes a new event type) register the type in
shared/data-schemas/events.schema.json. `shared/CHAT_ACTION_WIDGET.md`'s
action-reference tables are the human-readable view of THIS table — update
them together; the table wins on conflict.

Row fields:
  action_id      wire token (lowercase, brackets mark an input placeholder)
  verb           THE display label — buttons AND prose use exactly this
  event          primary event type the dispatch writes (None when the
                 action produces no substrate event — e.g. `send` sends
                 mail; `investigate` is a read)
  effect         one plain-English line of what happens (always present)
  surfaces       source_skill ids the verb renders on ("*" = every
                 all-batch surface)
  mute_ttl_days  int days for mutes, "permanent" for never-track,
                 None for non-mutes. The verb label must state it.
  input          "none" | "optional" | "required" — `required` means the
                 selection is invalid until the input has a value; the
                 widget must say what is missing and hold Apply with the
                 reason visible (F-17: a Defer without a date silently
                 blocked a whole batch; M concluded the button was dead)
  family         verb family for tests/grouping
  notes          per-surface variance, history, hand-off pointers
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# The table.
# ---------------------------------------------------------------------------

def _row(action_id, verb, event, effect, surfaces, *, mute_ttl_days=None,
         input="none", family, notes=""):
    return {
        "action_id": action_id,
        "verb": verb,
        "event": event,
        "effect": effect,
        "surfaces": tuple(surfaces),
        "mute_ttl_days": mute_ttl_days,
        "input": input,
        "family": family,
        "notes": notes,
    }


VERB_TAXONOMY = (
    # --- Commitment lifecycle (F-59 core) ----------------------------------
    _row("resolved", "Done", "commitment_resolved",
         "Close the item as completed (single closure path, undo-able).",
         ("commitments", "commitment-triage", "show-my-list", "dont-forget"),
         family="commitment",
         notes="Display was 'Resolved' on the daily chat and 'Done' on triage "
               "— same event, two names (F-59). 'Done' wins everywhere. On the "
               "(now-retired) Pulse surface the same click meant 'this alert isn't open "
               "anymore' (14-day suppression, no commitment event)."),
    _row("mark done", "Done", "commitment_resolved",
         "Close a self-commitment as completed.",
         ("commitments", "scaffold-automation"),
         family="commitment",
         notes="Self-commitment twin of `resolved`; scaffold-automation's "
               "deployed-yet check writes automation_deployed instead."),
    _row("already done", "Already done", "commitment_resolved",
         "You already did this one — it's confirmed and closed as completed.",
         ("needs-your-call", "cr-brain"), family="commitment",
         notes="DONE1. The queue-only twin of `resolved`, and deliberately "
               "NOT that wire id: apply-choices Step 4 routes `resolved` "
               "straight to close_commitment, which would close a "
               "pending_review row without confirming it and without the "
               "bulk-accept fence. Dispatch: needs_review_queue.done_items — "
               "clear_review_flags THEN close_commitment(resolution='done'), "
               "evidence = the user's own attestation, never a match. "
               "Per-item only: every id must be individually named (see "
               "done_items). Renders on BOTH queue surfaces from the one "
               "needs_review_queue.QUEUE_ROW_ACTIONS constant — the "
               "on-demand widget (source_skill needs-your-call) and the "
               "staff-meeting FROM YOUR MEETINGS fold (source_skill "
               "cr-brain, stamped by brain_proposals.build_card_view)."),
    _row("push to [date]", "Later…", "commitment_updated",
         "Deal with it later — type a date or a number of days. Your own "
         "item moves its due date; someone else's just leaves the view "
         "until then.",
         ("commitments", "commitment-triage"),
         input="required", family="commitment",
         notes="t3 FB-3 (M ruling 2026-07-16): Defer + Snooze read as two "
               "time-kicking verbs on one row — merged into ONE 'Later…' "
               "option. Wire id frozen; display + dispatch only. The "
               "when-input accepts a date OR a bare number of days "
               "(commitment_state.parse_later_when). Dispatch AUTO-ROUTES "
               "via commitment_state.later_route: the user's own item → "
               "commitment_updated due-date shift (the old Defer); "
               "owed-to-you / unowned → chat_dismissal with "
               "data.snooze_until via the mute ledger (the item stays open, "
               "it just stops rendering until the date). The renderer drops "
               "a row's separate skip/snooze option when this verb is "
               "present ('Later…' covers it). History: display was "
               "'Push to' on the daily chat, 'Defer' after F-59. The when "
               "is REQUIRED — an empty one is the F-17 silent-block; the "
               "widget names the missing input and holds Apply."),
    _row("drop", "Drop", "commitment_resolved",
         "Close as deliberately let go (resolution: dropped) — distinct from Done.",
         ("commitment-triage", "commitments"), family="commitment",
         notes="Added to the daily chat's confirm section (v4.6.1 W4b) — same "
               "dispatch as triage."),
    _row("not mine", "Not mine", "commitment_resolved",
         "Close as someone else's item (cross-attendee capture).",
         ("commitment-triage",), family="commitment",
         notes="Bare 'not mine' (no name) still closes as dropped. When the "
               "user NAMES the real owner ('that's actually Quinn's'), the "
               "item ROUTES instead via `reassign to [name]` (S4) — W4b's "
               "Theirs → [name] confirm verb dispatches the same event."),
    _row("fix wording [text]", "Fix wording", "commitment_updated",
         "Correct a mis-extracted title/summary; the item re-renders with "
         "the new text and the original stays in history.",
         ("commitments", "commitment-triage"), input="required",
         family="commitment",
         notes="S4. Dispatch: commitment_state.edit_commitment_wording — the "
               "projector folds data.new_title/new_summary, newest wins per "
               "field. Chat phrase: 'fix the wording on #N: <text>'."),
    _row("reassign to [name]", "Reassign", "commitment_reassigned",
         "Route the item to its real owner — it leaves your list and lands "
         "on theirs; nothing is chased until the new owner is confirmed.",
         ("commitments", "commitment-triage"), input="required",
         family="commitment",
         notes="S4 — 'not mine' used to DISCARD; this ROUTES. Dispatch: "
               "commitment_state.reassign_commitment (confirmed=True only "
               "for an explicit user action naming the person — the chat "
               "phrase or W4b's Theirs → [name] confirm verb; anything "
               "inferred stays pending_review and never enters chase)."),
    _row("split into [items]", "Split", "commitment_superseded",
         "Split one captured item into its real parts — each becomes its own "
         "commitment; the original closes with a note naming them.",
         ("commitment-triage",), input="required", family="commitment",
         notes="S4 (M decision 2026-07-09): extraction pre-split stays the "
               "doctrine; this is the MANUAL correction path. Dispatch: "
               "commitment_state.split_commitment. Chat phrase: 'split that "
               "into A / B / C'."),
    _row("add subitems [items]", "Add sub-items", "commitment",
         "Break the item into named steps you check off one at a time — the "
         "item itself stays open as the one commitment of record.",
         ("commitment-triage", "commitments"), input="required",
         family="commitment",
         notes="SUB1 (M ruling 2026-07-16). Dispatch: commitment_state."
               "add_subitems — DECOMPOSITION, not a split: the parent stays "
               "open; `split into [items]` closes it (peers, not steps). "
               "Input parsing identical to split's (newlines / semicolons / "
               "' / '); >=1 item is valid here (unlike split's >=2). Cap 12 "
               "open sub-items per parent (loud writer error above). Chat "
               "phrases: 'break #N into: A / B / C', 'add sub-items to #N: "
               "…', 'steps for #N: …'. User-initiated ONLY — extraction/"
               "sweeps never mint hierarchies."),
    _row("make task", "Turn into a task", "commitment_reclassified",
         "Reclassify promise → task: stays open, stops being chased, ages on "
         "the triage surface only.",
         ("commitment-triage", "commitments"), family="commitment",
         notes="Added to the daily chat's confirm section (v4.6.1 W4b). "
               "UXR1 D7b (M ruling 2026-07-21, UX review finding 8): the old "
               "label 'Make task' read as CREATE-a-second-item; the verb "
               "CONVERTS the existing row. Same wire id, same handler — "
               "display only; 'Make task' joined LEGACY_DISPLAY_LABELS."),
    _row("promote", "Make it a commitment", "commitment_reclassified",
         "Reclassify task → promise when a counterparty appears; enters chase.",
         ("commitment-triage", "commitments"), family="commitment",
         notes="F-59 flagged this verb as spec'd-but-rendered-nowhere. It "
               "SHIPS (task rows on triage render it); W4b's confirm flow "
               "reuses it for kind auto-promotion proposals ('Make it a "
               "commitment?' — PROPOSE only, the click reclassifies)."),
    # --- W4b confirm flow (v4.6.1 — the daily confirm section's verbs) ------
    _row("mine", "Mine", "commitment_updated",
         "Confirm the item is yours — you become the owner, the confirm flag "
         "clears, and it joins your you-owe list.",
         ("commitments", "commitment-triage"), family="commitment",
         notes="W4b. Dispatch: commitment_state.confirm_commitment_owner "
               "(owner = the primary user, owner_confirmed stamp). The "
               "counterpart of Theirs — Mine CLAIMS, Theirs ROUTES."),
    _row("theirs to [name]", "Theirs", "commitment_reassigned",
         "Confirm the item belongs to the person you name — it routes to "
         "them and leaves your list; nothing is chased on their behalf.",
         ("commitments", "commitment-triage"), input="required",
         family="commitment",
         notes="W4b. Dispatch: commitment_state.reassign_commitment with "
               "confirmed=True — the typed/tapped name IS the explicit "
               "confirmation. S4's `reassign to [name]` is the chat-phrase "
               "twin (same event, same dispatch)."),
    _row("merge", "Merge", "commitment_superseded",
         "Fold a suspected duplicate into the item it duplicates — one item "
         "survives carrying both sources.",
         ("commitments", "commitment-triage"), family="commitment",
         notes="W4b/C4. Dispatch: commitment_state.supersede_commitment("
               "survivor=the row's suspected_duplicate_of target, "
               "superseded=the row, user_confirmed=True). The row embeds "
               "both ids — no input needed. Never auto-merge: the flag is a "
               "question, this click is the verdict."),
    _row("keep both", "Keep both", "commitment_updated",
         "The suspected duplicate is a real, separate item — the flag clears "
         "and both stay open.",
         ("commitments", "commitment-triage"), family="commitment",
         notes="W4b/C4. Dispatch: commitment_state.clear_review_flags "
               "(note 'confirmed distinct')."),
    _row("never track this", "Never track (permanent)", "commitment_resolved",
         "Close the item AND append a suppression rule so extractors never "
         "capture this shape again. Permanent — not a timed mute.",
         ("commitment-triage",), mute_ttl_days="permanent", family="mute",
         notes="The label says (permanent) — the ONLY remaining one-way door "
               "by design (a suppression rule, not a timed mute; edit "
               "_hq/config/commitment-rules.md to lift it). Every TIMED mute "
               "is reversible via the S4 ledger: `show muted` + Unmute."),
    _row("set date [when]", "Set date", "commitment_updated",
         "Give a vague-timing capture a concrete due date.",
         ("past-meetings", "meeting-notes"), input="required",
         family="commitment"),
    _row("mark received", "Mark received", "thread_resolved",
         "The counterparty delivered — close the owed-to-you item.",
         ("commitments",), family="commitment"),
    _row("mark received all", "Mark received all", "thread_resolved",
         "Close every sub-item of a grouped chase at once.",
         ("commitments",), family="commitment"),
    _row("mark received from [name]", "Mark received from",
         "commitment_partial_received",
         "Record that ONE recipient of a multi-party item delivered — the "
         "item stays open until everyone has, then closure is proposed.",
         ("commitments", "commitment-triage"), input="required",
         family="commitment",
         notes="MC1. Dispatch: commitment_state.mark_partial_received (per "
               "OUTSTANDING counterparty of a multi-counterparty commitment — "
               "'send the deck to the board'). Received counterparties drop "
               "from the chase fan-out; when the last is marked the ack "
               "PROPOSES closure — the item never auto-closes. Distinct from "
               "`mark received` (which closes a single owed-to-you item)."),
    _row("add to my list", "Add to my list", "commitment_to_discuss",
         "Flag for later review — no state change; grouped by person under "
         "`show my list`.",
         ("commitments", "past-meetings", "dont-forget"),
         family="commitment",
         notes="RETIRED (MLK1, M ruling 2026-07-21 — UX review finding 1: "
               "the third 'my ___' lane beside My Plate and my reminders, "
               "sibling of `add to my plate` on the same rows; the confusion "
               "IS the coexistence). NO surface emits it and NO capture path "
               "writes new `commitment_to_discuss` items; the id stays "
               "registered so persisted old widgets still dispatch (the "
               "`edit then send` precedent) — the stale-widget click keeps "
               "its original meaning (a fossil `commitment_to_discuss` "
               "write), NEVER aliased to a different action. Display label "
               "is in LEGACY_DISPLAY_LABELS (banned on new renders). "
               "Remaining open items drain read-only via `show my list`. "
               "(History: capture-then-curate design, M v2.14.4; reaffirmed "
               "F-59; killed 2026-07-21.)"),
    _row("add to my plate", "Add to My Plate", "commitment",
         "Turn this into a task you own — it lands on My Plate.",
         ("commitments",), family="commitment",
         notes="CTS1FIX/D1. Dispatch: commitment_state.create_personal_task "
               "(owner-me, kind=task) — surfaces via surface_split `personal`. "
               "The one own-it-later verb since MLK1 retired `add to my "
               "list` (its old confusable sibling on the same rows)."),

    # --- Mutes (every label states its duration — F-59) ---------------------
    _row("skip", "Snooze (1 day)", "chat_dismissal",
         "Mute this item for 1 day; it resurfaces tomorrow.",
         ("*",), mute_ttl_days=1, family="mute",
         notes="Wire id `skip` frozen (in-flight widgets + crSkipAll + every "
               "dispatch handler). F-59: Skip never said it was a 24h mute — "
               "now the label does. One mute verb, visible durations. "
               "t3 FB-3: on rows that ALSO carry `push to [date]` the "
               "renderer suppresses this option from the dropdown — 'Later…' "
               "covers the kick-it case with an explicit date. The wire stays "
               "dispatchable (chat phrase, in-flight widgets, Snooze-rest "
               "footer button — all unchanged)."),
    _row("skip all", "Snooze rest (1 day)", "chat_dismissal",
         "Mute every unselected item for 1 day (bulk).",
         ("*",), mute_ttl_days=1, family="mute",
         notes="Also the footer bulk button (was 'Dismiss rest'/'Skip all')."),
    _row("snooze 3d", "Snooze (3 days)", "chat_dismissal",
         "Mute this alert for 3 days.",
         ("dont-forget", "inbox", "commitments"), mute_ttl_days=3,
         family="mute",
         notes="FB-17 (M, 2026-07-19): the email card's third primary button "
               "(Send / Draft / Snooze). 'Deal with it later' — the card mutes "
               "for 3 days. Also the Waiting On chase deferral verb "
               "(v2.14.38+, replacing the old `skip`; its one-time sibling "
               "`add to my list` retired at MLK1)."),
    _row("snooze 7d", "Snooze (7 days)", "chat_dismissal",
         "Re-surface the deployed-yet check in a week.",
         ("scaffold-automation", "balance"), mute_ttl_days=7, family="mute",
         notes="BAL1: on the Balance reconnect card this is the 'not this "
               "week' verb — the tie re-ranks next Sunday; the 7d TTL "
               "matches the surface's own per-tie dedupe window."),
    _row("snooze 14d", "Snooze (14 days) — hide until then", "chat_dismissal",
         "Check back in two weeks.",
         ("dont-forget", "stalled-projects", "cr-pipeline", "cr-brain",
          "cr-objectives"),
         mute_ttl_days=14, family="mute",
         notes="On the intro-followup check this writes intro_followup_check "
               "(a scheduled re-emit, not a dismissal) — see apply-choices. "
               "UXR1 D7a (M ruling 2026-07-21, UX review finding 8): label "
               "differentiated from `hold` by INTENT — snooze is time-based "
               "disappearance ('hide until then'); hold is parked-while-"
               "deciding. Wire id + mechanism unchanged; the label still "
               "states its duration (the F-59 mute contract)."),
    _row("snooze 30d", "Snooze (30 days)", "decision_revisit_scheduled",
         "Push the decision-revisit window out 30 days.",
         ("decision-revisit",), mute_ttl_days=30, family="mute"),
    _row("hold", "Hold — parked till you answer (14 days)", "chat_dismissal",
         "Park this until you answer; it stops re-rendering for 14 days.",
         ("staff-meeting", "cr-brain"), mute_ttl_days=14, family="mute",
         notes="UXR1 D7a (M ruling 2026-07-21, UX review finding 8): the old "
               "'Hold (14 days)' rendered indistinguishably from 'Snooze "
               "(14 days)' — same duration, no intent. The label now carries "
               "the hold INTENT (parked while you decide; cleared early the "
               "moment the item is answered) and still states its duration "
               "(the F-59 mute contract — the 14d re-render mute is real). "
               "Wire id + mechanism unchanged; 'Hold (14 days)' joined "
               "LEGACY_DISPLAY_LABELS. "
               "FB-19 (M, 2026-07-16). Distinct from `snooze 14d` by INTENT, "
               "identical in mechanism (a 14d chat_dismissal via "
               "mute_ledger.hold_item, reason='held'): snooze is 'not now', "
               "hold is 'I'm deciding — stop asking until I answer'. The live "
               "case: two rows parked in chat re-rendered the next fire as if "
               "nothing had been said, which reads as the system not "
               "listening. Cleared early by mute_ledger.clear_dismissal the "
               "moment the item IS answered — a hold outlives the question "
               "only if the question goes unanswered. The row that offers it "
               "MUST say the duration (the label does)."),
    _row("not relevant", "Not relevant (60 days)", "chat_dismissal",
         "Reject the proposal / dismiss the item; it won't re-surface for 60 days.",
         ("dont-forget", "inbox", "past-meetings", "commitments", "cr-brain"),
         mute_ttl_days=60,
         family="mute",
         notes="The 60-day cooldown was previously hidden by design "
               "('duration NEVER shown') — F-59 reverses that: every mute "
               "states its TTL at click time, AND the apply-time ack repeats "
               "it (S4: 'Muted for 60 days — say show muted to bring it "
               "back early')."),
    _row("unmute", "Unmute", "chat_dismissal_cleared",
         "Lift a mute before its time runs out — the item re-surfaces on its "
         "next scheduled chat.",
         ("show-my-list",), family="mute",
         notes="S4 mute ledger — renders on `show muted` / `show snoozed` "
               "rows (each row states its remaining time). Dispatch: "
               "mute_ledger.clear_dismissal. The triage batch-undo clears "
               "its batch's mutes the same way (F-20 P3a)."),

    # --- Reminders (W4a deferred the widget verbs to this table) ------------
    _row("reminder done", "Done", "reminder_cleared",
         "Clear the reminder — it leaves the brief's Pinned block. Clearing "
         "never touches a referenced commitment.",
         ("morning-brief", "show-my-reminders"), family="reminder"),
    _row("reminder push [date]", "Later…", "reminder_updated",
         "Move the pin date; the reminder re-pins from the new day (re-arms a "
         "cleared one-shot).",
         ("morning-brief", "show-my-reminders"), input="required",
         family="reminder",
         notes="Chat phrase 'push it to Friday' keeps working; the button "
               "says Later… — same word as the commitment lane's move-the-date "
               "verb (was 'Defer' pre-t3 FB-3; F-59's one-label rule keeps "
               "the lanes in lockstep). Dispatch: "
               "reminders.build_reminder_updated_event("
               "action='push', remind_from=<date>)."),
    _row("reminder keep", "Keep", "reminder_updated",
         "Acknowledge without clearing — resets the escalation clock, stays "
         "pinned.",
         ("morning-brief", "show-my-reminders"), family="reminder",
         notes="Dispatch: reminders.build_reminder_updated_event("
               "action='keep')."),

    # --- Email-shaped --------------------------------------------------------
    _row("send", "Send", None,
         "Send the draft as-is (Zapier first, native Gmail fallback).",
         ("inbox", "commitments", "dont-forget"), family="email"),
    # FB-17 (M, 2026-07-19): `edit then send` RETIRED — the FB-10 inline
    # contenteditable body obsoletes the To/Cc/Subject/Body popup editor. The
    # email card is now Send / Draft / Snooze (3 days), no dropdown. The wire id
    # stays a DEPRECATED_ALIAS (→ `send`) so in-flight widgets still dispatch,
    # and "Edit then send" joins LEGACY_DISPLAY_LABELS so no new render shows it.
    _row("draft", "Draft", None,
         "Review/edit, then save to Gmail Drafts (consolidated v2.14.4 verb).",
         ("inbox", "commitments", "dont-forget"), input="optional",
         family="email"),
    _row("add email then send", "Add email then send", "contact_email_captured",
         "Type the recipient's address (none is on file), then send.",
         ("inbox", "commitments"), input="required", family="email",
         notes="Bug #44 recovery verb. Address is REQUIRED — an empty field "
               "holds Apply with the reason, same F-17 contract as dates."),
    _row("escalate to memo", "Escalate to memo", None,
         "Promote to memo-writer when an email reply isn't enough.",
         ("inbox",), family="email"),
    _row("accept", "Accept", None, "Accept the calendar invite.",
         ("inbox",), family="email"),
    _row("propose [time]", "Propose", None,
         "Propose a different meeting time.",
         ("inbox",), input="required", family="email"),
    _row("decline", "Decline", None, "Decline the calendar invite.",
         ("inbox",), family="email"),
    _row("decline [reason]", "Decline", None,
         "Decline with a short note.",
         ("inbox",), input="optional", family="email"),

    # --- Balance (SPEC BAL1 — the personal white-space surface, m_facing) ----
    _row("book", "Book it", None,
         "Hold the evening as a tentative personal-calendar event and stage "
         "the venue outreach as a draft — this click is the consent; nothing "
         "books, sends, or spends on its own.",
         ("balance",), input="optional", family="work",
         notes="BAL1 D4 propose-and-confirm. Dispatch: the tentative hold "
               "routes through calendar-writer's Phase 5/6 consent path "
               "(never a direct calendar write) and any venue outreach "
               "through the email-writer chain, queued to Drafts per the "
               "draft posture — never auto-sent. Optional input = a venue "
               "name/correction. The whole verb is user-click-gated: no "
               "autonomous reservation, payment, or send exists (D4 hard "
               "line, not a v1 shortcut)."),
    _row("propose other night", "Another night", None,
         "Pick a different evening — type a date, or leave it empty to see "
         "the other open evenings.",
         ("balance",), input="optional", family="work",
         notes="BAL1 D8. A typed date is VALIDATED via availability."
               "has_conflict against the same busy set before anything is "
               "drafted; empty input re-renders the remaining open_slots "
               "from the fire's own computation (never re-fetched ad hoc)."),

    # --- Work / deep-context -------------------------------------------------
    _row("prep deep work", "Prep deep work", None,
         "Generate a context-loaded prompt for doing the work yourself.",
         ("commitments", "dont-forget"), family="work"),
    _row("follow-up call", "Follow-up call", None,
         "Draft a 15-min sync invite instead of an email chase.",
         ("commitments",), family="work"),
    _row("investigate", "Investigate", None,
         "Read-only cross-reference pull ('tell me about …').",
         ("dont-forget",), family="work"),
    _row("draft re-engagement", "Draft re-engagement", None,
         "Draft a re-engagement email (nothing sends until you do).",
         ("dont-forget", "stalled-projects", "cr-pipeline"), family="work",
         notes="On the pipeline surface this is the deal follow-up draft — "
               "hands to email-writer with the deal thread's context; "
               "draft-never-send preserved."),
    _row("schedule catchup [when]", "Schedule catchup", None,
         "Draft the request (+ tentative invite when you type a time).",
         ("dont-forget",), input="optional", family="work"),
    _row("status check", "Status check", None,
         "Draft an internal status-check email to the project owner.",
         ("dont-forget", "stalled-projects"), family="work"),
    _row("mark paused", "Mark paused", None,
         "Move the project to paused status (via workspace-manager's writer).",
         ("dont-forget", "stalled-projects"), family="work"),

    # --- Review / proposal confirmations -------------------------------------
    _row("confirm", "Confirm", None,
         "Apply the proposed change to the person record.",
         ("dont-forget", "commitments", "cr-brain"), family="review"),
    _row("edit [change]", "Edit", None,
         "Type the corrected value; it applies instead of the proposal.",
         ("dont-forget", "decision-memo-composer"), input="optional",
         family="review"),
    _row("add [text]", "Add", None,
         "Accept the proposal (empty) or fold in your corrections (typed).",
         ("dont-forget",), input="optional", family="review"),
    _row("confirm [type]", "Confirm", None,
         "Confirm the entity proposal; override inferred details if typed.",
         ("dont-forget", "cr-brain"), input="optional", family="review"),
    _row("edit [type]", "Edit", None,
         "Flip the inferred relationship type before confirming.",
         ("dont-forget",), input="optional", family="review"),
    _row("active", "Active", None,
         "Keep the project active (14-day re-propose cooldown).",
         ("dont-forget", "cr-brain"), family="review"),
    _row("keep paused", "Keep paused", None, "Already paused — no change.",
         ("dont-forget",), family="review"),
    # STAFFCUT: `cr-brain` added to the surfaces field. This verb has been
    # dispatched for `kind: dormancy` rows on the brain rail since LB1, but the
    # adapter shipped those rows with EMPTY action_tuples, so it never actually
    # rendered there and the documentation kept describing a dont-forget-only
    # verb. The row now carries it (`brain_proposals._DORMANCY_ACTIONS`), and
    # this field is documentation of where a verb appears — recording it changes
    # no enforcement (nothing validates a verb against its surfaces list).
    _row("archive", "Archive", None,
         "Archive the project outright (skip the dormant step).",
         ("dont-forget", "cr-brain"), family="review"),
    # --- W4b confirm flow — unknown-person rows (v4.6.1) ---------------------
    _row("add person", "Add person", "person_proposal_resolved",
         "Create the contact from the proposal (details inferred; type to "
         "correct them) — future captures of this name resolve to them.",
         ("commitments", "cr-brain"), input="optional", family="review",
         notes="W4b. Dispatch: people_writer.create_person via apply-choices "
               "Step 3a (dedup-first, disambiguation on MultipleCandidates), "
               "then the proposal tombstone (confirm_flow."
               "build_person_proposal_resolved_event, resolution "
               "person_added) so it stops re-surfacing."),
    _row("same as [existing]", "Same as", "person_proposal_resolved",
         "This name is an existing contact — saves the spelling as a "
         "shortcut so it resolves to them forever.",
         ("commitments", "cr-brain"), input="required", family="review",
         notes="W4b. Dispatch: resolve the typed name via the standard "
               "entity path (ambiguous → ask, never guess), then "
               "people_writer.add_person_alias (aliases.json mapping + the "
               "person record) + the proposal tombstone (resolution "
               "same_as). Permanent resolution improvement — the F-13 "
               "P2b/F-56 misattribution class shrinks with every alias."),
    _row("proposal not relevant", "Not relevant (permanent)",
         "person_proposal_resolved",
         "The name isn't worth tracking — the proposal is retired for good "
         "(nothing else is written).",
         ("commitments", "cr-brain"), family="review",
         notes="W4b. A proposal tombstone, NOT a timed mute — the label says "
               "permanent (F-59 rule). Commitment rows keep the 60-day "
               "`not relevant` mute; this verb renders ONLY on "
               "unknown-person proposal rows."),
    _row("merge person records", "Merge records (permanent)", "person_merged",
         "Fold the duplicate contact into the record it duplicates — one "
         "record survives carrying both histories. Permanent: a record "
         "merge has no undo.",
         ("cr-brain",), family="review",
         notes="PID1 D4b — the reconciler's duplicate-suspect rows (kind "
               "person_merge). Dispatch: people_writer.merge_person_into("
               "workspace_root, keep_id=<row data.keep_id verbatim>, "
               "duplicate_id=<row data.duplicate_id verbatim>) then "
               "brain_proposals.resolve_proposal(..., 'applied'). CONFIRM-"
               "ONLY FOREVER: person_merge is never in AUTO_ALLOWED and "
               "merge_person_into has NO registered reverser — no code "
               "path merges without this click. Distinct from the "
               "commitment `merge` verb (commitment_superseded)."),
    _row("add as person to [org]", "Add as person to org", None,
         "Create a person record under the org you type.",
         ("past-meetings", "meeting-notes"), input="required", family="review"),
    _row("add as new org", "Add as new org", None,
         "Create the candidate org as a new tracked entity.",
         ("past-meetings", "meeting-notes"), family="review",
         notes="Specific-name variants ('add as person to <Org>', 'add as "
               "new org <Org>') resolve to these rows — see "
               "is_canonical_action in the renderer."),
    _row("add context [text]", "Add context", None,
         "Seed an interactive entity-creation flow with your free-form note.",
         ("past-meetings", "meeting-notes"), input="optional", family="review"),

    # --- Deals (SPEC PIPE1 — the cr-pipeline widget; dispatch via deal_state) --
    _row("move to [stage]", "Move stage", "deal_stage_changed",
         "Move the deal to the stage you pick (backward moves allowed — "
         "deals regress; days-in-stage resets).",
         ("cr-pipeline",), input="required", family="deal",
         notes="PIPE1. Dispatch: deal_state.set_stage — validates the fixed "
               "v1 stage enum (lead/qualified/proposal_sent/negotiating); "
               "won/lost are NOT stages, they dispatch mark won / mark lost."),
    _row("set next step [text]", "Set next step", "commitment",
         "Type the deal's next step with a date — it becomes a tracked "
         "commitment on the deal thread (D3: the next step IS a commitment).",
         ("cr-pipeline",), input="required", family="deal",
         notes="PIPE1. Dispatch: a standard commitment capture with "
               "primary_thread_id = the deal thread; closes only via "
               "commitment_state.close_commitment. Clears the deal's "
               "no-next-step flag on the next render."),
    _row("mark won", "Mark won", "deal_won",
         "Close the deal as won (thread resolves). On a prospect org the ack "
         "offers the one-tap client conversion — nothing flips silently.",
         ("cr-pipeline",), family="deal",
         notes="PIPE1. Dispatch: deal_state.close_deal(outcome='won') — "
               "idempotent; already_closed acks honestly. The widget verb "
               "never passes convert_prospect=True; only the explicit "
               "'[Name] signed' utterance family does (D6)."),
    _row("mark lost [reason]", "Mark lost", "deal_lost",
         "Close the deal as lost — pick the reason (thread archives).",
         ("cr-pipeline",), input="required", family="deal",
         notes="PIPE1. Dispatch: deal_state.close_deal(outcome='lost', "
               "loss_reason=<pick>) — reason REQUIRED (F-17 hold-with-reason "
               "when empty); enum: no_decision/price/competitor/diy/timing/"
               "bad_fit/other, no_decision listed first (it's ~61% of real "
               "losses)."),
    _row("track deal", "Track deal", "deal_created",
         "Adopt a pre-existing deal thread into the pipeline (attaches stage "
         "tracking; nothing else changes).",
         ("cr-pipeline",), family="deal",
         notes="PIPE1 real-data shape: kind='deal' threads that predate the "
               "deal object render as untracked rows with this one-tap "
               "adoption. Dispatch: deal_state.adopt_deal."),

    # --- Objectives (SPEC OBJ1, DRAFT — the cr-objectives widget; dispatch
    # via objective_state, the single writer) --------------------------------
    _row("report [status]", "Report status", "objective_report",
         "Your word on where it stands — pick on track / at risk / "
         "off track / blocked.",
         ("cr-objectives",), input="required", family="objective",
         notes="OBJ1. Dispatch: objective_state.record_report — the owner's "
               "word, valid on any binding (the weekly touch only ASKS for "
               "self-bound ones). Enum enforced at the writer; never an "
               "inferred status."),
    _row("mark complete", "Mark complete", "objective_completed",
         "Close the objective as done (thread resolves).",
         ("cr-objectives",), family="objective",
         notes="OBJ1. Dispatch: objective_state.complete_objective — "
               "idempotent; already_closed acks honestly."),
    _row("archive [reason]", "Archive", "objective_archived",
         "Retire it — no longer an objective (the record and reason are "
         "kept).",
         ("cr-objectives",), input="optional", family="objective",
         notes="OBJ1. Dispatch: objective_state.archive_objective — the "
               "graceful-death landing too ('is this still an objective?' → "
               "archive). Typed input becomes the outcome note."),
    _row("rebind", "Fix tracking", "objective_updated",
         "Re-pick how it's tracked (or point it at a renamed meeting).",
         ("cr-objectives",), family="objective",
         notes="OBJ1. Opens the objectives skill's rebind flow — path "
               "toggle + proposed target + confirm; the write lands via "
               "objective_state.rebind_objective."),
    _row("confirm objective", "Add it", "objective_created",
         "Confirm a proposed objective — it goes on the board as drafted "
         "(path pre-selected, one binding).",
         ("cr-objectives",), input="optional", family="objective",
         notes="OBJ1 cold start. Dispatch: objective_state.create_objective "
               "with the card's pre-filled statement/binding/owner; typed "
               "input edits the statement first. Skipped proposals stay "
               "away 60 days (receipt trail)."),

    # --- LB1 Living Brain card ("Needs your eyes" / Staff Meeting) -----------
    # Generic verbs for brain-family (bp_*) proposal rows. Legacy-family rows
    # on the same card keep their OWN shipped verbs (add person / same as /
    # proposal not relevant for person rows; confirm / not relevant / hold for
    # CRU review rows; confirm [type] / not relevant for org and project rows;
    # active / archive / snooze 14d for dormancy rows) — dispatch table in
    # skills/apply-choices/SKILL.md Step 2 `cr-brain`.
    #
    # STAFFCUT corrected this list twice over. It named `confirm [type] /
    # active / snooze 14d` as one set for "dont-forget rows", which conflated
    # two different row kinds and named neither one's real set; and all three of
    # those kinds shipped with EMPTY action_tuples, so the verbs it described
    # rendered nowhere. The kinds and their sets are spelled out separately now.
    # A DIGEST row (STAFFCUT — `n` starts with `digest:`) carries the verbs of
    # the kind it groups and fans them out per member id; it adds no verb of its
    # own, because a bulk verb nobody registered is exactly what STOP rule 7
    # forbids.
    _row("confirm proposal", "Confirm", "brain_proposal_resolved",
         "Apply the proposed change through its standard writer and retire "
         "the proposal.",
         ("cr-brain",), input="optional", family="review",
         notes="LB1. Dispatch per kind: deal_update → deal_state.set_stage/"
               "update_deal/close_deal; deal_creation → deal_state."
               "create_deal; then brain_proposals.resolve_proposal "
               "(user_action applied — or edited when input was typed). "
               "Typed input corrects inferred details before applying; an "
               "empty input applies the proposal as-is and says so (F-17: "
               "never blocks the batch)."),
    _row("dismiss proposal", "Not relevant (60 days)", "brain_proposal_resolved",
         "Decline the proposal — the same suggestion stays away for 60 days.",
         ("cr-brain",), family="review",
         notes="LB1. Dispatch: brain_proposals.resolve_proposal(user_action="
               "'declined') — the tombstone plus the shared proposal_feedback"
               ".jsonl cooldown row (60d fingerprint suppression at the "
               "source). Deal-kind declines also write deal_update_dismissed "
               "for the PIPE1-named consumers. Visible TTL per F-59."),
    _row("snooze proposal 7d", "Snooze (7 days)", "chat_dismissal",
         "Set the proposal aside for a week — it re-surfaces after.",
         ("cr-brain",), mute_ttl_days=7, family="mute",
         notes="LB1. The existing chat_dismissal TTL machinery (target_id = "
               "the proposal id). The proposal's own TTL clock keeps running "
               "— an ignored proposal still expires silently."),

    # --- Decisions ------------------------------------------------------------
    _row("revisit", "Revisit now", None,
         "Open deliberation — decision-memo-composer pre-filled with the "
         "original framing.",
         ("decision-revisit",), family="decision"),
    _row("still valid", "Still valid", "decision_reaffirmed",
         "Reaffirm the original decision.",
         ("decision-revisit",), family="decision"),
    _row("replace", "Replace it", "decision_superseded",
         "Capture the new decision and supersede the original.",
         ("decision-revisit",), family="decision"),
    _row("decide [text]", "Decide", "decision",
         "Log the decision (your text folds into the rationale).",
         ("decision-memo-composer", "past-meetings"), input="optional",
         family="decision"),

    # --- Intro follow-up (domain verbs) ---------------------------------------
    _row("landed", "Landed", "intro_followup_check",
         "The intro connected — feeds the relationship graph.",
         ("dont-forget",), family="review"),
    _row("didnt land", "Didn't land", "intro_followup_check",
         "The intro didn't connect — pattern data for future framing.",
         ("dont-forget",), family="review"),

    # --- Meetings --------------------------------------------------------------
    _row("context [text]", "Context", None,
         "Add context or ask a question — routed intent-aware at apply time.",
         ("upcoming-meetings",), input="optional", family="meeting"),
    _row("push meeting [date]", "Push meeting", None,
         "Draft the reschedule email (proposes the time you type, or asks "
         "for availability when blank).",
         ("upcoming-meetings",), input="optional", family="meeting"),

    # --- WG1-A grammar verbs (fleet widget grammar + row quarantine) ---------
    # SPEC_WG1-A (M ruling 2026-07-20, big-test Findings Ledger row 13/13b/10b).
    _row("nudge", "Nudge", None,
         "Chase a delegated item — composes the nudge email on click (draft "
         "posture; nothing sends until you do).",
         ("commitments",), family="work",
         notes="WG1-A D-A4. The delegated row's ruled PRIMARY verb (was a "
               "compose-on-demand draft with no standing verb). Compose-on-"
               "CLICK, not compose-at-render: apply-choices routes it to the "
               "chase-draft / email-writer chain (draft posture) so scheduled "
               "fires stay connector-free (same discipline as the moves "
               "adapter). No substrate event — the draft's own email_drafted "
               "append is written by the email-writer chain when it runs."),
    _row("show why", "Show why", None,
         "Explain why this row was withheld — names the source so you can fix "
         "the underlying data. Read-only.",
         ("*",), family="review",
         notes="WG1-A D-A6. The row-quarantine placeholder's sole action. A "
               "defective row (failed the per-row leak scan) degrades to an "
               "honest placeholder carrying this verb instead of blocking the "
               "whole page; the click dispatches a chat explanation naming the "
               "seq/source of the defect. Read-only, no substrate event."),

    # --- Bulk row ---------------------------------------------------------------
    _row("send all", "Send all", None,
         "Sequential sends across all non-noise items.", ("*",), family="bulk"),
    _row("to drafts all", "To drafts all", None,
         "Bulk save to Gmail Drafts.", ("*",), family="bulk"),
    _row("show more", "Show more", None,
         "Re-render with the next chunk of items.", ("*",), family="bulk"),
)

# Deprecated wire-id aliases: accepted at dispatch/validation for in-flight
# widgets, NEVER emitted by new renders. Maps alias → replacement action_id.
DEPRECATED_ALIASES = {
    "snooze [duration]": "snooze 3d",           # pre-v2.14.38 free-text snooze
    "add more context [text]": "context [text]",  # v2.12.4–v2.14.36
    "ask question [text]": "context [text]",      # v2.14.14–v2.14.36
    "edit then send": "send",                     # FB-17 — inline editing (FB-10)
                                                  # obsoletes the popup editor;
                                                  # accepted for in-flight widgets,
                                                  # never emitted by new renders.
}

# Display labels that MUST NOT appear on any newly rendered button — the
# pre-taxonomy names the dogfood caught wearing two hats (F-59 / F-13 P2a /
# F-18). The rendered-widget regression scan enforces this list.
LEGACY_DISPLAY_LABELS = frozenset({
    "Resolved",        # → Done
    "Push to",         # → Defer (itself retired at t3 FB-3) → Later…
    "Defer",           # → Later… (t3 FB-3 — the merged Defer/Snooze option)
    "Skip",            # → Snooze (1 day)
    "Skip all",        # → Snooze rest (1 day)
    "Dismiss rest",    # old footer bulk label
    "Snooze",          # bare snooze without a stated duration
    "Never track this",  # → Never track (permanent)
    "Not relevant",    # → Not relevant (60 days) — bare form hides the TTL
    "Make it a task",  # → Make task
    "Edit then send",  # FB-17 — retired; the FB-10 inline body replaces it
    "Merge records",   # UXC1 2026-07-21 — label now carries "(permanent)";
                       # the bare form must not appear on any new render
                       # (batch acks teach "say undo", and a merge is the one
                       # thing undo can never reverse)
    "Add to my list",  # MLK1 2026-07-21 — the verb is retired (M ruling, UX
                       # review finding 1): no new render may offer it. The
                       # wire id stays registered above so persisted old
                       # widgets still dispatch with their original meaning.
    "Make task",       # UXR1 D7b 2026-07-21 — read as create-new; the verb
                       # CONVERTS the row → "Turn into a task"
    "Hold (14 days)",  # UXR1 D7a 2026-07-21 — indistinguishable from Snooze
                       # (14 days) → "Hold — parked till you answer (14 days)"
    "Snooze (14 days)",  # UXR1 D7a 2026-07-21 — bare form carries no intent
                         # → "Snooze (14 days) — hide until then". Exact-label
                         # ban only (the new label CONTAINS this string as a
                         # prefix by design — the scan matches whole labels).
})

# ---------------------------------------------------------------------------
# Derived lookups (what the renderer imports).
# ---------------------------------------------------------------------------

_BY_ID = {row["action_id"]: row for row in VERB_TAXONOMY}
if len(_BY_ID) != len(VERB_TAXONOMY):
    raise RuntimeError("verb_taxonomy: duplicate action_id rows")

CANONICAL_ACTION_IDS = frozenset(_BY_ID) | frozenset(DEPRECATED_ALIASES)

# action_id → THE display label (aliases resolve to their replacement's label)
DISPLAY_LABELS = {aid: row["verb"] for aid, row in _BY_ID.items()}
for _alias, _repl in DEPRECATED_ALIASES.items():
    DISPLAY_LABELS[_alias] = _BY_ID[_repl]["verb"]

# action_ids whose input is REQUIRED before Apply may fire (F-17 contract)
REQUIRED_INPUT_ACTION_IDS = frozenset(
    row["action_id"] for row in VERB_TAXONOMY if row["input"] == "required"
)

# What a required input IS, in the user's words — the inline reason says
# "<Verb> needs a <thing>". Derived from the bracket placeholder.
_PLACEHOLDER_THING = {
    "[date]": "date", "[when]": "time", "[time]": "time",
    "[org]": "org name",
}


_THING_OVERRIDES = {
    "move to [stage]": "stage",
    "set next step [text]": "next step (with a date)",
    "mark lost [reason]": "loss reason",
    "add email then send": "email address",
    "set date [when]": "date",
    "reminder push [date]": "date",
    "fix wording [text]": "corrected wording",
    "reassign to [name]": "name",
    "theirs to [name]": "name",
    "report [status]": "status (on track / at risk / off track / blocked)",
    "same as [existing]": "name",
    "split into [items]": "list of items",
    "add subitems [items]": "list of items",
    "mark received from [name]": "name",
}


def required_input_thing(action_id: str) -> str:
    """Plain word for what a required-input action is missing ('date',
    'time', 'email address', …) — used in the inline validation reason."""
    a = (action_id or "").lower()
    if a in _THING_OVERRIDES:
        return _THING_OVERRIDES[a]
    for ph, thing in _PLACEHOLDER_THING.items():
        if ph in a:
            return thing
    return "value"


def taxonomy_row(action_id: str):
    """The row for a wire id (aliases resolve to their replacement).
    Returns None for unknown ids — callers decide whether that's an error."""
    a = (action_id or "").lower()
    if a in _BY_ID:
        return _BY_ID[a]
    if a in DEPRECATED_ALIASES:
        return _BY_ID[DEPRECATED_ALIASES[a]]
    return None


def display_label(action_id: str):
    """THE display label for a wire id, or None when the id has no row
    (specific-name variants like 'add as person to <Org>' fall back to the
    renderer's default label pass)."""
    return DISPLAY_LABELS.get((action_id or "").lower())


def mute_ttl_days(action_id: str):
    """Mute duration for a wire id: int days, 'permanent', or None."""
    row = taxonomy_row(action_id)
    return row["mute_ttl_days"] if row else None


__all__ = [
    "VERB_TAXONOMY",
    "DEPRECATED_ALIASES",
    "LEGACY_DISPLAY_LABELS",
    "CANONICAL_ACTION_IDS",
    "DISPLAY_LABELS",
    "REQUIRED_INPUT_ACTION_IDS",
    "taxonomy_row",
    "display_label",
    "mute_ttl_days",
    "required_input_thing",
]
