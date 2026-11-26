#!/usr/bin/env python3
"""COCKPIT1 (cr1#608) - the cockpit page's engine: what the page shows, what
the page saved, and how a saved choice reaches the book.

WHAT THE COCKPIT IS
-------------------

One private artifact page the owner opens each morning. Six tabs in one row
since cr1#1537: Today, Inbox, Week, People, Work, Record (`HUB_TABS`). Work
holds five views since cr1#1540: Triage (opened first), Commitments, Priorities,
Work streams and Updates (`HUB_WORK_VIEWS`);
Record is the Done tab that came before it; the morning brief as written
(cr1#1310) opens from the Brief button and Settings from the gear; Overdue and
Review are the Late and To confirm filters inside Commitments since cr1#946
and cr1#1286. One Go button, behind the status line, one light per leg. The page
keeps its own small database on claude.ai: the feeds a run writes for it, the
status line per leg, and the choices the owner presses. Nothing on the page
writes the book. A run of the `cockpit-apply` task reads the choices back and
applies them through the same writers the chat widgets use.

THE RULE THIS BUILD REVERSED, AND THE THREE OBJECTIONS IT ANSWERS
---------------------------------------------------------------

The 2026-08-06 ruling rejected a queued page write for silent rot, verb
collapse and a lost undo (`artifact_board.py`, `apply-choices/SKILL.md`). The
owner reversed it for this page on 2026-11-03 (cr1#608), and each objection
gets a mechanism here rather than a promise:

  * silent rot: a choice carries `created_at`; `plan_apply` expires one older
    than `CHOICE_EXPIRY_DAYS` with a line instead of applying it, and the
    page shows how many choices wait and since when;
  * verb collapse: the page's verbs are the closed sets `COMMITMENT_VERBS`
    and `INBOX_VERBS`, each a SUBSET of the owning surface's own verbs. An
    unknown verb is refused by name, never guessed;
  * undo: a page-applied close is not in the chat's in-session batch cache,
    so the page offers Reopen (`reopen`, through
    `commitment_state.reopen_commitment`) and every applied batch is one
    `apply_choices_applied` audit row with a `cockpit:<batch>` label.

WHO WRITES WHAT
---------------

Helpers (read or compute, on `RUN_HELPER_ALLOWLIST`): `feed_today`,
`feed_brief`, `feed_inbox`, `plan_apply`, `status_doc`, `maintenance_status`,
`load_config`, `setup_check`, `render_page`, `capabilities`.

Writers (on `RUN_WRITER_ALLOWLIST`): `feed_plate` (building the plate mints
display numbers under the ledger lock, exactly as `morning_brief_helpers:
plate_lines` is a writer for that reason), `apply_commitment_choices` (the
closes, notes, snoozes and reopens, plus the one audit row),
`save_config` (the page address under `_hq/config/`) and `log_run_receipt`
(the run's one `pack_run` receipt under its own task id).

Nothing here publishes the page or republishes it. A triggered run that
republishes a page declaring capabilities waits for an approval nobody is
there to give; the page re-renders from its database, and only a typed
`update my cockpit` republishes it (cockpit-setup).

stdlib only.
"""
from __future__ import annotations

import contextvars
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TASK_ID = "cockpit"  # the one-task shape of #614; kept so its receipts parse forever
SOURCE_SKILL = "cockpit"
SETUP_SKILL = "cockpit-setup"

#: The four tasks (owner ruling 2026-11-03, cr1#619): one writer from the page
#: to the book, three producers from the connectors and the book to the page.
#: Each has its own trigger, cron (local), orchestrator and light. Cron order is
#: the morning's order. The ruling asked for apply every half hour; the cloud
#: scheduler refuses fires closer than an hour apart (`schedule_backend.
#: min_gap_minutes`), so apply and refresh run hourly, offset by half an hour.
COCKPIT_TASKS = {
    # cr1#1722: Apply and Refresh are one run. It saves what the owner pressed,
    # then rebuilds the page from the book, in that order, so nothing waits on
    # a second task. Hourly at the old Refresh's minute, and fired by the page
    # about two minutes after a press.
    "cockpit-update": {"leg": "update", "cron": "30 7-15 * * 1-5", "label": "half past, 7 AM to 3 PM weekdays",
                       "orchestrator": "orchestrator-cockpit-update.md"},
    # cr1#1722 step 2: the one Morning run. The inbox, meeting prep, the day's
    # first whole update and the morning brief, in that order, in one session.
    # It fires again an hour later; that run does only what a cut-off first
    # run left (D2, coordinator 2026-11-24), and ends in one line when nothing is.
    # ruling 129 (item 4): a third fire at 8:45, so a morning cut off after about
    # 7:10 (its last mark under 35 minutes old at 7:45) is taken over unpressed.
    "cockpit-morning": {"leg": "morning", "cron": "45 6-8 * * 1-5",
                        "label": "6:45 AM weekdays, and 7:45 and 8:45 to finish anything left",
                        "orchestrator": "orchestrator-cockpit-morning.md"},
}
COCKPIT_TASK_IDS = tuple(COCKPIT_TASKS)
#: cr1#1722: the two tasks the Update replaced. Set-up deletes their triggers
#: (`plan_cockpit_registration`'s `stale`) and registers none. A seat that has
#: not run set-up since still fires them from its old page, so their
#: orchestrators, lights and receipts stay as they were until it does.
RETIRED_TASKS = {
    "cockpit-apply": {"leg": "apply", "orchestrator": "orchestrator-cockpit-apply.md"},
    "cockpit-refresh": {"leg": "refresh", "orchestrator": "orchestrator-cockpit-refresh.md"},
    # cr1#1722 step 2, decision F: Hub Morning runs the inbox and meeting prep,
    # and every prep or inbox press fires it with its part on the light. The
    # two orchestrators stay: the Morning runs each as one of its parts.
    "cockpit-prep": {"leg": "prep", "orchestrator": "orchestrator-cockpit-prep.md"},
    "cockpit-inbox": {"leg": "inbox", "orchestrator": "orchestrator-cockpit-inbox.md"},
}
#: Their trigger names, as set-up finds them in the listing.
RETIRED_TASK_NAMES = {"cockpit-apply": "Hub Apply", "cockpit-refresh": "Hub Refresh",
                      "cockpit-prep": "Hub Prep", "cockpit-inbox": "Hub Inbox"}
#: The tasks a new Hub task takes over from: when the owner had paused any of
#: them, the new one is created paused, and set-up says so (cr1#914).
CARRIED_PAUSE_FROM: Dict[str, Tuple[str, ...]] = {
    "cockpit-update": ("cockpit-apply", "cockpit-refresh"),
    "cockpit-morning": ("cockpit-prep", "cockpit-inbox", "morning-brief"),
}
COMMITMENT_SRC = "commitment-triage"
INBOX_SRC = "cr-inbox"

CONFIG_REL = Path("_hq") / "config" / "cockpit.json"
TEMPLATE_REL = Path("shared") / "templates" / "cockpit" / "cockpit.html"

LEGS = ("prep", "inbox", "apply", "refresh", "update", "morning")
SEND_TOKEN_MIN_LEN = 24
STATUS_STATES = ("idle", "running", "done", "error", "no_answer", "waiting")
RUNNING_TIMEOUT_MINUTES = 15
#: A leg that reads and drafts a whole inbox runs longer than the rest; its
#: light is not called dead at 15 minutes while it is still working (cr1#678).
#: cr1#1722 step 2 (review round 1): the Morning marks its light as each part
#: starts and ends, so a morning with no mark for this long is a dead run's.
#: Longer than the longest measured part (prep, about 26 minutes) and shorter
#: than the 60 minutes between its fires (6:45, 7:45, 8:45), so the next fire
#: takes over a morning the one before it was cut off in. The page's own limit
#: is the same.
MORNING_STALE_MINUTES = 35
#: The page's own limit for a press it wrote and nobody answered (its
#: NO_START_MS): past it the page says the task did not start, and the light
#: is no longer a request any run adopts (second review of #1776, item 2).
MORNING_PAGE_NO_START_MINUTES = 8
RUNNING_TIMEOUT_BY_LEG = {"inbox": 30, "morning": MORNING_STALE_MINUTES}
#: The Refresh light the page sets to `waiting` while it waits for Apply;
#: after this long nothing will fire it, so the light stops saying it waits.
WAITING_TIMEOUT_MINUTES = 180
STATUS_WAIT_DROPPED_LINE = "Not refreshed: Apply took too long. Press Go."
CHOICE_EXPIRY_DAYS = 7
PREP_HOLD_MINUTES = 60
UPCOMING_HOURS = 24
PLATE_ROW_CAP = 150
#: Rows past their date that the Commitments list leaves out, sent for the
#: Overdue tab all the same (cr1#877), oldest first. Past this the feed counts
#: them and the tab says so.
OVERDUE_EXTRA_CAP = 150
FOCUS_CAP = 8
#: The top line's ranking (cr1#1290): each of the owner's own items scores on
#: its link to a confirmed priority, on being owed to a key person (one
#: starred on the People tab) and on being due inside the lead time its work
#: needs. Overdue only breaks ties; an item with no score never leads.
LEAD_CAP = 3
LEAD_PRIORITY_POINTS = 3
LEAD_PRIORITIES_COUNTED = 2
LEAD_KEY_PERSON_POINTS = 3
LEAD_WINDOW_POINTS = 2
#: Days ahead of its date an item surfaces: a plain item the day before, a
#: deliverable (an item with steps, or one whose title names a made thing)
#: three days before plus a day for each step past the first, at most six.
LEAD_PLAIN_DAYS = 1
LEAD_DELIVERABLE_DAYS = 3
LEAD_MAX_DAYS = 6
LEAD_WORK_RE = re.compile(
    r"(?i)\b(presentations?|decks?|slides|proposals?|reports?|plans?|drafts?|memos?|models?|agreements?|"
    r"contracts?|pitch|budgets?|analysis|write[- ]?ups?|briefs?|reviews?)\b")
CHASE_CAP = 6
SCHEDULE_CAP = 4
#: Lines from the owner's goals (cr1#1294, part b): the confirmed priorities
#: with no live item behind them, at most this many on the top line; the
#: count travels whole.
GOAL_LINES_CAP = 3

#: The page's verbs: a closed subset of the owning surface's own verbs.
COMMITMENT_VERBS = frozenset({"done", "drop", "context", "confirm", "snooze", "reopen",
                              # cr1#1289: the presses on a "No home yet" row
                              "assign", "notwork", "newstream",
                              # cr1#1341: Undo after Apply of Assign to / New work stream
                              "unassign",
                              # cr1#1540: a pile's press on an item owed to the owner
                              # that went quiet (it rests, still open), and its Undo
                              "park", "unpark",
                              # cr1#1658 change 6: move an item that has a home to another
                              # project (its Undo is `unassign`, as for Assign to)
                              "rehome"})
INBOX_VERBS = frozenset({"send", "draft", "snooze"})
#: cr1#1020: the inbox presses the owner's mail rules take (`mail_rules`),
#: applied in code by `apply_mail_choices`, never through a mail handler:
#: Drop and Teach on an inbox row, and Add, Remove and Bring back on the
#: rules list (`kind: "mailrule"`).
INBOX_RULE_VERBS = frozenset({"drop", "teach"})
MAILRULE_VERBS = frozenset({"add", "remove", "back", "keep", "unkeep"})
MAIL_SURFACE = "cockpit-mail"
#: cr1#1020 slice 3: the People tab's presses (`people_view`).
PERSON_VERBS = frozenset({"star", "unstar"})
PEOPLE_SURFACE = "cockpit-people"
#: cr1#1788: the Prep page's edits (agenda, Goal, call guide) write their audit rows under this.
PREPEDIT_SURFACE = "cockpit-prepedit"
#: cr1#1785: Focus, the list the owner picks, writes its audit rows under this.
FOCUS_SURFACE = "cockpit-focus"
#: What each page verb becomes on the apply-choices wire (inbox only; the
#: commitment verbs are applied here, in this module).
INBOX_WIRE_ACTION = {"send": "send", "draft": "draft", "snooze": "snooze 3d"}

#: The four tasks' names before the page was renamed Command Room Hub
#: (cr1#670). A seat set up before the rename still carries them until
#: set-up runs again, which switches them off (`stale`).
PRE_HUB_NAMES = {"cockpit-prep": "Cockpit Prep", "cockpit-inbox": "Cockpit Inbox",
                 "cockpit-apply": "Cockpit Apply", "cockpit-refresh": "Cockpit Refresh"}

DONE_EVIDENCE = "Marked done on the Command Room Hub"
DROP_EVIDENCE = "Dropped on the Command Room Hub"
DROP_PENDING_LINE = "That one is still waiting for your Confirm; it cannot be dropped from here."
#: cr1#1289: "Not work" on a "No home yet" row, the same one closure path as
#: Drop, recorded with its own reason.
NOT_WORK_EVIDENCE = "Set aside as not work on the Command Room Hub"
NOT_WORK_LINE = "Set aside: not work."
ASSIGN_REASON = "Given a home on the Command Room Hub"
ASSIGN_NO_STREAM_LINE = "That work stream is not on your book any more; nothing was written. Pick another."
ASSIGN_HAS_HOME_LINE = "That one already has a home; nothing was written."
#: cr1#1658 change 6: Move to another project.
REHOME_REASON = "Moved to another project on the Command Room Hub"
REHOME_SAME_LINE = "It is already there; nothing was written."
REHOME_NO_TARGET_LINE = "That project is not on your book any more; nothing was written. Pick another."
UNMOVE_LINE = "Moved back where it was."
#: review round 1 of #1700: what a move refuses, and its Undo's one refusal
REHOME_NO_HOME_LINE = "That one has no project yet, so it is given one under No home yet with Assign to; nothing was written."
REHOME_LANE_LINE = "That would move it across your personal line, so it is not moved; nothing was written."
REHOME_OFF_LIMITS_LINE = "That item is about someone you put off limits, so it is not moved from the Hub; nothing was written."
REHOME_FENCE_LINE = "Your account settings could not be read just now, so nothing was moved. Try again after the next Refresh."
UNMOVE_GONE_LINE = "Its old project is archived or gone, so it stays where it is; nothing was written."
TODO_NO_PROJECT_LINE = "Added as {level} priority, with no project: that project is no longer on your book."
TODO_PERSONAL_PROJECT_LINE = "Added as {level} priority, with no project: a to-do from the Hub is not put on a personal project."
#: cr1#1289 part two: Assign to on a line only heard tracks it first,
#: through the queue's own confirm; this is the line when that refuses.
ASSIGN_HEARD_FAILED_LINE = "That line could not be tracked, so it was not given a home; nothing was written."
#: cr1#1289 part two: New work stream, a name typed on a "No home yet" row.
NEW_STREAM_NAME_CAP = 80
NEW_STREAM_CHILD = "{name}: general"
NEW_STREAM_NAME_LINE = "Type a name for the new work stream; nothing was written."
NEW_STREAM_TAKEN_LINE = "{name} is already on your book and is not a work stream; nothing was written. Pick another name."
NEW_STREAM_FAILED_LINE = "The new work stream could not be set up; nothing was written."
#: cr1#1342: a name only an archived or closed namesake holds, never revived
#: from the Hub (the owner's `revive` in a chat is the one door back).
NEW_STREAM_ARCHIVED_LINE = ("{name} belongs to {what} you archived; nothing was written. "
                            "Say revive {name} in a chat to bring it back, or pick another name.")
NEW_STREAM_CLOSED_LINE = "{name} belongs to {what} that is closed; nothing was written. Pick another name."
#: cr1#1342: the stream's record landed but the book could not record that
#: the Hub made it, so it is neither finished nor claimed later.
NEW_STREAM_HALF_LINE = ("{name} was added to your book but could not be finished, so this has no home yet. "
                        "Ask in a chat to finish {name} or to put this elsewhere.")
NEW_STREAM_UNSURE_LINE = ("The work stream {name} could not be set up, and part of it may be on your book; "
                          "this has no home yet. Check {name} in a chat before pressing again.")
#: cr1#1582: a new work stream gets its folder in the same press, so a name
#: no folder can carry is asked about before anything is written.
NEW_STREAM_FOLDER_NAME_LINE = ("{name} cannot be a folder name on Windows or a Mac ({why}); "
                               "nothing was written. Pick another name.")
NEW_STREAM_FOLDER_PICK_LINE = ("More than one folder called {name} is already here ({where}); "
                               "nothing was written. Say in a chat which one {name} should use.")
NEW_STREAM_FOLDER_TAKEN_LINE = ("The folder {folder} is already {other}'s; nothing was written. "
                                "Pick another name.")
NEW_STREAM_NO_FOLDER_LINE = " Its folder could not be made, so it has none yet."
UNASSIGN_FOLDER_ARCHIVED_LINE = " Its folder had something in it, so it was moved whole to the archive."
UNASSIGN_FOLDER_STAYS_LINE = " Its folder could not be moved, so it stays."
#: A heard line already tracked when a later step refused (review of part two).
HEARD_TRACKED_ONLY_LINE = "The line is now tracked, but it was not given a home; it waits under No home yet."
NOT_WORK_FAILED_LINE = "That one could not be set aside; nothing was written."
#: cr1#1540: a pile cleared in one press on the Work tab. Each item is its own
#: choice row with the pile's existing verb; the Apply run asks the pile's
#: rule again before it writes a row (`hub_work.pile_sets`).
PARK_REASON = "Parked from the Command Room Hub"
PARK_EVIDENCE_LINE = "Parked: still open, off your working list."
PILE_VERB_LINE = "That press does not belong to a pile on your Work tab; nothing was written."
PILE_UNREADABLE_LINE = "The pile could not be checked just now, so nothing was written. Press Go, then try again."
PILE_LEFT_LINE = "That one is no longer in that pile, so it was left as it is; nothing was written."
PILE_DAY_LINE = "That day is today or already past, so it was left as it is; nothing was written."
#: cr1#1541: planning on the Week. A planned item is moved by the one
#: move-to-a-day press (a snooze to that date) with the time of day it was put
#: at (`at`, "HH:MM", and `mins`) and the plan it belongs to (`plan`); the
#: moves of one plan share one batch, so the Record tab's own Put it back
#: takes them all back together.
PLAN_BATCH_PREFIX = "hubplan:"
PLAN_ID_CAP = 60
_PLAN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")
PLAN_UNREADABLE_LINE = "That plan could not be read, so nothing moved. Plan it again on the Week."
#: cr1#1541 review round 1: a plan never moves an item's date when the item
#: already has a later date of its own, and never lands on a time gone.
PLAN_LATER_DUE_LINE = "{title} has its own later date, {due}, so it was not planned onto {day}; nothing moved."
PLAN_PAST_LINE = "That planned time has passed, so nothing moved. Plan it again on the Week."
UNPARK_REASON = "Un-parked on the Command Room Hub"
UNPARK_LINE = "Back on your working list."
UNPARK_NOT_HUB_LINE = "That one was not parked from the Hub, so it was left as it is; nothing was written."
UNPARK_ALREADY_LINE = "That one is not parked any more; nothing was written."
#: cr1#1540: an Undo of a pile's close or park names the write it takes back.
#: When the item was closed or parked again since, by anyone, that Undo is
#: not for what stands now, and writes nothing.
BACK_MOVED_LINE = "That one was changed again after the press this Undo is for, so it was left as it is; nothing was written."
PRESSED_BEFORE_UNPARK_LINE = ("You un-parked this item after that press, so it was not parked again; "
                              "press it again if you meant it.")
#: cr1#1341: Undo after Apply for Assign to and New work stream. The Undo is
#: its own `unassign` choice naming the press it takes back.
UNASSIGN_REASON = "Taken back on the Command Room Hub"
UNASSIGN_ARCHIVE_REASON = "Started on the Command Room Hub, then taken back there"
UNASSIGN_LINE = "Back under No home yet."
UNASSIGN_TRACKED_LINE = " It stays tracked."
UNASSIGN_ARCHIVED_LINE = "Back under No home yet; the work stream {name} the Hub started for it is archived."
UNASSIGN_KEPT_LINE = "Back under No home yet; {name} stays, because other work is on it now."
UNASSIGN_ARCHIVE_FAILED_LINE = "Back under No home yet; {name} could not be archived, so it stays."
UNASSIGN_CHILD_FAILED_LINE = ("Back under No home yet; the work stream {name} is archived, "
                              "but the thread under it could not be, so that thread stays.")
UNASSIGN_NO_PRESS_LINE = "That move is not on your book; nothing was written."
UNASSIGN_MOVED_LINE = "That one has moved since; nothing was written."
UNASSIGN_ALREADY_LINE = "That one is already back under No home yet; nothing was written."
CONFIRM_EVIDENCE = "Confirmed and closed on the Command Room Hub"
REOPEN_REASON = "Reopened from the Command Room Hub"
#: cr1#1462: a closing press made before the owner's latest reopen of the item.
PRESSED_BEFORE_REOPEN_LINE = ("You reopened this item after that press, so it was not closed again; "
                              "press it again if you meant it.")
#: cr1#1462: the presses that close an item.
_CLOSING_VERBS = ("done", "confirm", "drop", "notwork")
SNOOZE_REASON = "pushed to a later date from the Command Room Hub"

NO_ACCOUNT_LINE = ("This chat runs with no account of its own, so it cannot "
                   "set up a Hub; open a chat in the Claude app signed in "
                   "as the account that registered your scheduled chats.")
FOREIGN_ACCOUNT_LINE = ("This chat runs under a different account from the "
                        "one your scheduled chats write as, so the Hub "
                        "was not set up; switch to that account and say "
                        "`set up my hub` again.")
NO_TRIGGER_ID_LINE = "No task id was given, so this registration was not recorded."
NO_FOLDER_LINE = ("This chat could not tell which folder on your computer the "
                  "Hub tasks belong to, so nothing was registered; ask again "
                  "in a Command Room chat with that folder attached.")
NO_WORKSPACE_LINE = ("This chat has no Command Room folder attached, so "
                     "there is nothing to set a Hub up on.")

EXPIRED_LINE = "Waited more than {days} days without a run, so it was not applied; press it again if it still stands."
STALE_INBOX_LINE = "This inbox row is from an older inbox run; the inbox was checked again since, so pick it again on the new list."
SEND_NEEDS_CONFIRM_LINE = "Send needs the second press on the page before a run sends anything."
SEND_TOKEN_REUSED_LINE = "This send carries a token another choice already used, so it was not sent; press Send again on the page."
NEWER_MESSAGE_LINE = "A newer message arrived on this thread after the draft, so it was not sent; check the thread."
#: cr1#1251: a reply with others on it, on a mail service whose reply tool
#: answers the sender alone (Microsoft 365) or one the run cannot name.
GROUP_REPLY_LINE = ("This mail service's reply goes to the sender only, and this reply has others on it, "
                    "so it was not sent. Reply from your mail app so nobody is left off.")
GROUP_REPLY_DRAFT_LINE = ("This mail service's reply goes to the sender only, and this reply has others on it, "
                          "so no draft was made. Reply from your mail app so nobody is left off.")
GROUP_REPLY_UNKNOWN_LINE = ("The run cannot tell which mail service this reply would go through, so it cannot be sure "
                            "it reaches everyone on it and it was not sent. Reply from your mail app so nobody is left off.")
GROUP_REPLY_UNKNOWN_DRAFT_LINE = ("The run cannot tell which mail service this reply would go through, so it cannot be sure "
                                  "it reaches everyone on it and no draft was made. Reply from your mail app so nobody is left off.")
#: The mail services whose reply tool carries To and Cc itself (cr1#936).
REPLY_ALL_PLATFORMS = frozenset({"superhuman", "gmail"})


def _mail_platform(platform: Optional[str], workspace_root=None) -> str:
    """The mail service a reply goes through, as one word: `superhuman`,
    `gmail`, `outlook`, or "" when it cannot be named. Takes what the run
    passed (a provider slug, or a connector label such as "Superhuman
    Mail"); with nothing passed, the seat's declared email backend."""
    raw = str(platform or "").strip().lower()
    if not raw and workspace_root:
        try:
            import connector_config
            row = connector_config.declared_backend("email", workspace_root) or {}
            raw = str(row.get("provider") or "").strip().lower()
        except Exception:  # noqa: BLE001 - an unreadable declaration names nothing
            raw = ""
    if "outlook" in raw or "microsoft" in raw or raw in ("m365", "ms365", "office365"):
        return "outlook"
    for name in REPLY_ALL_PLATFORMS:
        if name in raw:
            return name
    return ""
UNKNOWN_VERB_LINE = "The page sent a verb this surface does not take ({verb}); nothing was written."
NO_ACTOR_LINE = "No owner is on record for this workspace, so nothing was written."
GONE_LINE = "That item is no longer on the book; nothing was written."
#: cr1#1377: a commitment choice that names no item. Said as it is, never as
#: gone: an empty id is a broken press, not a closed item.
NO_ID_LINE = "That choice names no item, so nothing was written."
NOT_COMMITMENT_LINE = "That choice is not on a commitment row ({kind}); nothing was written."
ALREADY_APPLIED_LINE = "That choice was already applied; nothing was written."
NO_CHOICE_ID_LINE = "That choice has no id of its own, so it cannot be marked applied; nothing was written."
#: cr1#1377, the owner's ruling of 2026-11-19 (option b): a raw page choice
#: another Apply run holds is refused here, and the press waits for that run
#: or the next one.
CLAIMED_ELSEWHERE_LINE = "Another Apply run holds this press, so nothing was written; it is applied by that run or the next one."
#: cr1#1377 review: a claimed press handed over by a call that names no run.
#: It waits, and the audit names it an error, so the Hub says Apply needs a
#: look instead of the press waiting unseen for ever.
NO_RUN_ID_LINE = "This Apply run did not say which run it is, so a claimed press was not applied; nothing was written."
ALREADY_CLOSED_LINE = "That one was already closed; nothing was written."
NOT_OPEN_LINE = "That one is not open; nothing to reopen."
EMPTY_NOTE_LINE = "The note was empty; nothing was written."
NO_HOME_LINE = "No person or project to attach the note to; nothing was written."
SUBITEMS_LINE = "That item has open sub-items; close them first in chat."
SNOOZE_NEEDS_DATE_LINE = "Later needs a day: a date, a day like Thursday, or a number of days; nothing moved."

STATUS_RUNNING_LINE = "Started."
STATUS_NO_ANSWER_LINE = "No answer for {minutes} minutes. Is your computer on?"


# ---------------------------------------------------------------------------
# clock
# ---------------------------------------------------------------------------

def _now(now_iso=None) -> _dt.datetime:
    if isinstance(now_iso, _dt.datetime):
        dt = now_iso
    elif isinstance(now_iso, str) and now_iso.strip():
        text = now_iso.strip().replace("Z", "+00:00")
        try:
            dt = _dt.datetime.fromisoformat(text)
        except ValueError:
            dt = _dt.datetime.now(_dt.timezone.utc)
    else:
        dt = _dt.datetime.now(_dt.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value) -> Optional[_dt.datetime]:
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


# ---------------------------------------------------------------------------
# the page address (`_hq/config/cockpit.json`, the board's own pattern)
# ---------------------------------------------------------------------------

def config_path(workspace_root) -> Path:
    return Path(workspace_root) / CONFIG_REL


def load_config(workspace_root) -> Dict[str, Any]:
    """`{found, url, trigger_id, server, basename, published_at,
    plugin_version, tz}`. A missing or corrupt file reads as not found; losing the
    memory costs one new page address, never a failed run. `tz` is the
    workspace's zone by name (early review of #1776, must 1: Hub Morning's
    helpers count a morning on the workspace's own day)."""
    out: Dict[str, Any] = {"found": False, "url": None, "trigger_id": None,
                           "server": None, "basename": None,
                           "published_at": None, "plugin_version": None, "tz": _zone_named(workspace_root)}
    try:
        data = json.loads(config_path(workspace_root).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - absent or corrupt reads as none
        return out
    if not isinstance(data, dict):
        return out
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        return out
    out.update(found=True, url=url.strip())
    for key in ("trigger_id", "server", "basename", "published_at",
                "plugin_version"):
        value = data.get(key)
        out[key] = value if isinstance(value, str) and value.strip() else None
    return out


#: The line the scheduled Morning Brief ends on when a Hub is set up (cr1#782):
#: the brief is folded into the Hub, and something still brings the owner there
#: each morning. A seat with no Hub gets no line and keeps its brief unchanged.
HUB_POINTER_LINE = "Your Command Room Hub has this morning's view and the three questions to answer: {url}"


#: cr1#1389: the one line the Hub and the Morning Brief show when the installed
#: plugin is newer than the version the Hub page was built from. The Hub's
#: Finish update link opens a chat with `finish update` typed in.
UPDATE_LINE = "Command Room updated to v{version}. Press Finish update."


def _version_tuple(value) -> Optional[Tuple[int, ...]]:
    """`6.2.1` or `v6.2.1` as numbers; anything else None."""
    text = str(value or "").strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    if not text or not all(part.isdigit() for part in text.split(".")):
        return None
    return tuple(int(part) for part in text.split("."))


def update_notice(workspace_root, *, installed_version: Optional[str] = None,
                  plugin_root=None) -> Dict[str, Any]:
    """READ (cr1#1389). `{show, installed, built, line}`: whether the installed
    plugin is newer than the version the Hub page was built from (the saved
    Hub config's `plugin_version`, which `save_config` stamps at every
    publish). `installed_version` is the installed plugin's, as the chat read
    it; with none handed in, the `plugin.json` beside this module. A seat with
    no Hub, or an install version that cannot be read, shows nothing. A Hub
    with no saved version counts as older: nothing proves it current, and a
    rebuild stamps it. Never raises."""
    out: Dict[str, Any] = {"show": False, "installed": None, "built": None, "line": ""}
    try:
        cfg = load_config(workspace_root) if workspace_root else {"found": False}
        installed = str(installed_version or "").strip() or plugin_version_here(plugin_root) or ""
        out["installed"] = installed or None
        out["built"] = cfg.get("plugin_version") if cfg.get("found") else None
        have = _version_tuple(installed)
        if not cfg.get("found") or have is None:
            return out
        built = _version_tuple(out["built"])
        if built is not None:  # 6.2 and 6.2.0 are one version, as on the page
            width = max(len(have), len(built))
            have, built = have + (0,) * (width - len(have)), built + (0,) * (width - len(built))
        if built is None or have > built:
            shown = installed[1:] if installed[:1] in ("v", "V") else installed
            out.update(show=True, line=UPDATE_LINE.format(version=shown))
    except Exception:  # noqa: BLE001 - a read that fails shows nothing
        return {"show": False, "installed": None, "built": None, "line": ""}
    return out


def hub_pointer(workspace_root) -> Dict[str, Any]:
    """READ. `{found, url, line, update_line, inbox_line}` for the Morning Brief's last
    line (cr1#782): the page address when `_hq/config/cockpit.json` holds one,
    else an empty `line`, so a seat that never set up the Hub posts its brief
    exactly as before. `update_line` (cr1#1389) is `update_notice`'s line when
    the installed plugin is newer than the page, else empty. `inbox_line`
    (cr1#1464) is `inbox_stop`'s line when the Hub inbox run built no page
    on its last run, else empty. Never raises: an absent or corrupt record
    reads as not found."""
    cfg = load_config(workspace_root) if workspace_root else {"found": False, "url": None}
    url = cfg.get("url") if cfg.get("found") else None
    update = update_notice(workspace_root) if url else {"line": ""}
    # cr1#1464: an inbox run that built no page is said in the brief too.
    stopped = inbox_stop(workspace_root) if url else None
    return {"found": bool(url), "url": url, "line": HUB_POINTER_LINE.format(url=url) if url else "",
            "update_line": update.get("line") or "",
            "inbox_line": (stopped or {}).get("line") or ""}


def save_config(workspace_root, *, url: str, trigger_id: Optional[str] = None,
                server: Optional[str] = None, basename: Optional[str] = None,
                published_at: Optional[str] = None,
                plugin_version: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. Remembers the page's stable address and its task's trigger
    id. Atomic, so a crash mid-write never leaves a half pointer that reads
    as no page and mints a second one. Fields left None keep the stored
    value."""
    from atomic_write import atomic_write_text

    if not isinstance(url, str) or not url.strip():
        return {"ok": False, "reason": "no_url",
                "line": "No page address was given, so nothing was saved."}
    prior = load_config(workspace_root)
    data = {
        "url": url.strip(),
        "trigger_id": trigger_id or prior.get("trigger_id"),
        "server": server or prior.get("server"),
        "basename": basename or prior.get("basename"),
        "published_at": published_at or _iso(_now()),
        "plugin_version": plugin_version or prior.get("plugin_version"),
        "task_id": "cockpit-update",  # the task `trigger_id` belongs to (the writer: Apply until cr1#1722)
    }
    path = config_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return {"ok": True, "rel": str(CONFIG_REL).replace("\\", "/"), **data}


# ---------------------------------------------------------------------------
# setup: one account, end to end
# ---------------------------------------------------------------------------

def _same_account_by_mark(root, environ: Dict[str, str]) -> bool:
    """cr1#1138: the declaration names THIS account by its mark. A writer id
    carries the folder's name and the organisation, so after a renamed folder
    or an organisation switch the ids differ while the account is the same;
    the mark has neither in it. False whenever either side has no mark."""
    try:
        import schedule_config as _sc
        import schedule_holder as _sh

        decl = _sc.scheduled_writer(root) or {}
        want = decl.get(_sh.KEY_ACCOUNT)
        here = _sh.account_mark(environ)
    except Exception:  # noqa: BLE001 - no mark, no verdict: today's check
        return False
    return bool(want and here and want == here)


def _acting_for_owner(root, environ: Dict[str, str]) -> bool:
    """cr1#1139 (ruling A5, D1 (a)): THIS account is one the owner named as
    acting for them (`connector_config.acting_account_marks`). False whenever
    this seat has no mark."""
    try:
        import connector_config as _cc
        import schedule_holder as _sh

        here = _sh.account_mark(environ)
        return bool(here and here in _cc.acting_account_marks(root))
    except Exception:  # noqa: BLE001
        return False


def setup_check(workspace_root, env: Optional[Dict[str, str]] = None,
                writer_pair=None) -> Dict[str, Any]:
    """Can THIS chat set the cockpit up? `{ok, line, writer_id, declared,
    basename}`.

    The page, its task, the connector the viewer fires through and the
    workspace's declared scheduled writer must be one account, or the page
    fires a task that refuses every write. So: no bakeable pair (a legacy or
    local seat, or no account) refuses; a bakeable pair that is not the
    declared scheduled writer refuses; a workspace with no declaration yet
    passes and the registration declares it.

    The pair is the registration's own (`schedule_config.registration_pair`):
    the caller's `writer_pair` when it has the right shape, else derived from
    this process's account, else the pair the door FORWARDED on the rendered
    line (`CR_WRITER_ID` / `CR_WRITER_DERIVATION`). A helper run beside the
    data on the customer's computer has no account of its own; the first
    build derived only, so a set-up whose check ran there refused a seat that
    can register (cr1#646)."""
    import os as _os

    import schedule_config as sc

    environ = dict(_os.environ) if env is None else dict(env)
    root = str(workspace_root or "").strip()
    if not root:
        return {"ok": False, "reason": "no_workspace", "line": NO_WORKSPACE_LINE,
                "writer_id": None, "declared": None, "basename": None}
    basename = root.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    pair = sc.registration_pair(root, writer_pair=writer_pair, env=environ)
    if not pair:
        return {"ok": False, "reason": "no_account", "line": NO_ACCOUNT_LINE,
                "writer_id": None, "declared": None, "basename": basename}
    declared = None
    try:
        from receipts import _declared_scheduled_writer

        declared = _declared_scheduled_writer(root)
    except Exception:  # noqa: BLE001 - a missing declaration is not a refusal
        declared = None
    if declared and declared != pair[0] and not _same_account_by_mark(root, environ):
        if _acting_for_owner(root, environ):
            # cr1#1139: an account acting for the owner sets the Hub up after
            # it takes the schedules over (`hand_over_schedules`), so the page
            # and its task write as the holder.
            return {"ok": True, "reason": "acting_for", "acting_for": True, "line": "",
                    "writer_id": pair[0], "declared": declared, "basename": basename}
        return {"ok": False, "reason": "foreign_account",
                "line": FOREIGN_ACCOUNT_LINE, "writer_id": pair[0],
                "declared": declared, "basename": basename}
    return {"ok": True, "reason": None, "line": "", "writer_id": pair[0],
            "declared": declared, "basename": basename}


# ---------------------------------------------------------------------------
# the page: capabilities and the template fill
# ---------------------------------------------------------------------------

def connector_display_name(server) -> str:
    """The connector's name as the artifact runtime spells it (cr1#636): the
    display name, with spaces. A scheduler listing and a tool prefix spell
    the built-in connector `Claude_Code_Remote`; a page declared under that
    spelling matched no connector, so it could not read a task and every
    button stayed off. A name that already carries a space is kept as it
    is."""
    name = str(server or "").strip()
    if name and " " not in name and "_" in name:
        name = name.replace("_", " ")
    return name


#: Direct send from the page (cr1#710, owner ruling 2026-11-04: the page
#: sends, through the seat's own declared mail connector). Per provider: the
#: claude.ai connector's usual display name, and the tools the page calls in
#: order: `read` re-reads the thread for the newer-mail check, `draft` makes
#: the reply in the thread (when the provider sends a draft), `send` sends,
#: `thread` shows the thread under a draft when the owner opens it (cr1#714;
#: read live, never stored), `new_draft` makes a mail that answers no thread (a
#: chase, cr1#219). A provider not listed here keeps Send on Apply.
#: Microsoft 365 only (cr1#1250): `reply_all_draft` makes a reply to a group
#: thread and `set_recipients` then sets its To and Cc to exactly the people
#: the owner saw (Bcc cleared) before `send`; its plain reply tool answers the
#: sender alone.
#: `list` (cr1#1538) lists the newest inbox mail while the page is open, read
#: only; its arguments per provider are in `MAIL_LIST`.
MAIL_DIRECT = {
    "superhuman": {"server": "Superhuman Mail", "read": "get_thread",
                   "draft": "create_or_update_draft", "send": "send_draft", "thread": "get_thread",
                   "new_draft": "create_or_update_draft", "list": "list_threads",
                   "discard": "discard_draft"},   # cr1#1745: the Undo of a follow-up put in Drafts
    "gmail": {"server": "Gmail", "read": "get_thread", "draft": None, "send": "reply",
              "thread": "get_thread", "new_draft": "create_draft", "list": "search_threads"},
    "outlook": {"server": "Microsoft 365", "read": "outlook_email_search",
                "draft": "outlook_create_reply_draft", "send": "outlook_send_draft",
                "thread": "read_resource", "new_draft": "outlook_create_draft",
                "reply_all_draft": "outlook_create_reply_all_draft", "set_recipients": "outlook_update_draft",
                "list": "outlook_email_search", "discard": "outlook_delete_draft"},
}
#: Live arrivals (cr1#1538, owner ruling 2026-11-21): how the page lists the
#: newest inbox mail on each provider, read only. `args` is the first call's
#: input; `account_key` names the argument that picks one of several linked
#: mailboxes (None: the connector fronts one mailbox, so a fetch plan that
#: names a mailbox cannot be kept and the page lists nothing); `cursor_in` is
#: the argument a later page is asked with and `cursor_out` the answer's key
#: that carries it (`offset_in` / `offset_out`: the same by number).
#: `args_verified` says the argument names were read from the connector's own
#: tool schema (Superhuman and Microsoft 365 were, on 2026-11-21); Gmail's
#: are the names this repo's inbox run uses and still need a seat to confirm
#: them. `answer_verified` is False everywhere: no answer was read from a
#: real connector (that would pull real mail into a build), so the page reads
#: each answer under every key it is known or described to use, and shows a
#: plain sentence when it can read none.
MAIL_LIST = {
    "superhuman": {"args": {"labels": ["INBOX"], "limit": 50, "sort": "newest"},
                   "account_key": "acting_email", "cursor_in": "cursor", "cursor_out": "next_cursor",
                   "args_verified": True, "answer_verified": False},
    "gmail": {"args": {"query": "in:inbox", "pageSize": 50},
              "account_key": None, "cursor_in": "pageToken", "cursor_out": "nextPageToken",
              "args_verified": False, "answer_verified": False},
    "outlook": {"args": {"order": "newest", "limit": 25},
                "account_key": None, "cursor_in": "cursor", "cursor_out": "nextCursor",
                "offset_in": "offset", "offset_out": "nextOffset",
                "args_verified": True, "answer_verified": False},
}
#: Chat messages on the Inbox (cr1#1538): one read-only tool per chat
#: provider, for direct and group messages of the last days. `after_key` is
#: the argument that bounds the read in time and `after_kind` its form
#: (`filter_date`: an `after:YYYY-MM-DD` search modifier inside that argument;
#: `iso`: an ISO date-time). Argument names were read from each connector's
#: own tool schema on 2026-11-21. Slack's answer was read once on a seat
#: (cr1#1571, format only, every value made up on the issue): a JSON object
#: whose `results` is one markdown text, a block per message, in the
#: `detailed` form, which the page asks for by name so a change of the
#: connector's default cannot change what it reads; `answer_form` names the
#: page's reader for it (`slack_text`). Teams' answer is still unread and is
#: read as a list of records. Nothing is sent or written on a chat connector
#: from the page.
CHAT_DIRECT = {
    "slack": {"server": "Slack", "list": "slack_search_public_and_private",
              "args": {"channel_types": "im,mpim", "sort": "timestamp", "limit": 20,
                       "include_context": False, "response_format": "detailed",
                       "content_types": "messages"},
              "after_key": "filters", "after_kind": "filter_date", "cursor_in": "cursor",
              "answer_form": "slack_text",
              "args_verified": True, "answer_verified": True},
    "ms365_teams": {"server": "Microsoft 365", "list": "chat_message_search",
                    "args": {"query": "*", "limit": 25},
                    "after_key": "afterDateTime", "after_kind": "iso", "offset_in": "offset",
                    "args_verified": True, "answer_verified": False},
}
#: How far back the page asks for chat messages.
CHAT_DAYS = 2
_UUIDISH_RE = re.compile(r"^[0-9a-f]{8}-", re.I)

#: Calendar answers from the page (cr1#723, owner ruling 2026-11-04): Accept,
#: Decline and Tentative on a meeting row, through the seat's declared
#: calendar connector, one map like `MAIL_DIRECT`. Per provider: the
#: connector's usual display name, the respond tool, the names of its event-id
#: and answer arguments, and the words the tool takes for each answer.
#: `verified` says whether the tool's contract was read from the connector
#: itself: Microsoft 365's was (`outlook_respond_to_event`: eventId, response
#: accept|decline|tentative, sendResponse); Google Calendar's names were read
#: from the connector's tool schema on 2026-11-24 (cr1#1800: `respond_to_event`
#: takes eventId and responseStatus accepted|declined|tentative), and stay
#: unverified until an answer pressed on a Google seat lands. Superhuman's
#: calendar has no respond tool, so its rows show no calendar answers.
#: `admin_line` is the one plain line a seat sees when the client's admin has
#: not switched writing on, in place of a failure.
CALENDAR_DIRECT = {
    "google_calendar": {"server": "Google Calendar", "respond": "respond_to_event",
                        "event_key": "eventId", "answer_key": "responseStatus",
                        "answers": {"accept": "accepted", "decline": "declined", "tentative": "tentative"},
                        "verified": False,
                        "admin_line": "Google refused this answer. Your Google Workspace admin may need to mark the Claude app as trusted; ask them, then press again. Nothing was changed."},
    "outlook_calendar": {"server": "Microsoft 365", "respond": "outlook_respond_to_event",
                         "event_key": "eventId", "answer_key": "response",
                         "answers": {"accept": "accept", "decline": "decline", "tentative": "tentative"},
                         "extra": {"sendResponse": True}, "verified": True,
                         "admin_line": "Your Microsoft 365 admin has to grant the Claude connector Calendars.ReadWrite and your Claude org owner has to enable write tools before the Hub can answer invitations. Nothing was changed."},
}
#: The mail-side admin line per provider (cr1#723), said on a send the
#: connector refuses for want of the switch, and by set-up when it can tell.
MAIL_ADMIN_LINE = {
    "gmail": "Gmail refused this send. Your Google Workspace admin may need to mark the Claude app as trusted; ask them, then press Send again. Nothing was sent.",
    "outlook": "Your Microsoft 365 admin has to grant the Claude connector Mail.Send and your Claude org owner has to enable write tools before the Hub can send. Nothing was sent.",
}
#: The scopes a Microsoft 365 seat needs for each action (cr1#723), as
#: `get_granted_scopes` names them. Google has no equivalent read: the first
#: press is the check.
MS_SCOPES = {"send": "Mail.Send", "calendar": "Calendars.ReadWrite"}
CALENDAR_ANSWERS = ("accept", "decline", "tentative")

#: Booking, moving and cancelling a meeting from the page (cr1#1539, owner
#: ruling 2026-11-21: "nothing changes on a calendar without my press"). One
#: map per calendar provider, beside `CALENDAR_DIRECT`: the tools the page
#: calls on the owner's press or drop, and the name of each argument.
#:   `time`   how the tool takes a time: `rfc3339` (a date-time with its offset
#:            plus an IANA zone argument) or `wall` (`{dateTime, timeZone}`).
#:   `free`   the read that finds free time for several people; `create`,
#:            `move`, `cancel` the three changes. `cancel: None` means the
#:            connector has no tool that deletes or cancels an event, so the
#:            page offers no Cancel and no Undo of a booking there.
#:   `notify` what the provider itself sends the people invited on each
#:            change: `off` (the call asks it to send nothing), `always` (the
#:            tool's own description says it sends, and no argument turns it
#:            off) or `unknown` (the tool has no argument for it and does not
#:            say). The page says the line for it BEFORE the press.
#:   `args_verified` on each tool: its argument names were read from the
#:            connector's own tool schema (Superhuman and Microsoft 365 were,
#:            on 2026-11-21). Google Calendar's could not be read in the
#:            build, so they are this repo's expected spelling, marked False,
#:            and `calendar_actions` switches nothing on for a tool marked
#:            False: an unverified contract never changes a calendar.
#:            `answer_verified` is False everywhere: no answer was read from
#:            a real connector, so the page reads each answer under every key
#:            it is described to use, and says so when it can read none.
#: No connector here can bring a cancelled event back, so a cancel is never
#: offered with an Undo: the second press says so before the owner confirms.
CALENDAR_ACTIONS = {
    "superhuman": {
        "server": "Superhuman Mail", "time": "rfc3339",
        "free": {"tool": "get_availability", "people_key": "participants", "from_key": "start_date",
                 "to_key": "end_date", "minutes_key": "duration_minutes", "tz_key": "timezone",
                 "bounds": "rfc3339", "args": {"working_hours_only": True},
                 "args_verified": True, "answer_verified": False},
        "create": {"tool": "create_or_update_event", "title_key": "title", "start_key": "start",
                   "end_key": "end", "tz_key": "timezone", "people_key": "attendees",
                   "people_form": "emails", "video_key": "conference",
                   "args_verified": True, "answer_verified": False},
        # the tool's schema requires the title on a change too
        "move": {"tool": "create_or_update_event", "event_key": "event_id", "title_key": "title",
                 "start_key": "start", "end_key": "end", "tz_key": "timezone",
                 "args_verified": True, "answer_verified": False},
        "cancel": None,
        "notify": {"create": "unknown", "move": "unknown"},
    },
    "outlook_calendar": {
        "server": "Microsoft 365", "time": "wall",
        "free": {"tool": "outlook_find_available_time", "people_key": "participants",
                 "from_key": "afterDateTime", "to_key": "beforeDateTime", "minutes_key": "durationMinutes",
                 "bounds": "utc", "args": {"maxCandidates": 10},
                 "args_verified": True, "answer_verified": False},
        "create": {"tool": "outlook_create_event", "title_key": "subject", "start_key": "start",
                   "end_key": "end", "people_key": "attendees", "people_form": "objects",
                   "video_key": "isOnlineMeeting", "args_verified": True, "answer_verified": False},
        "move": {"tool": "outlook_update_event", "event_key": "eventId", "start_key": "start",
                 "end_key": "end", "args_verified": True, "answer_verified": False},
        "cancel": {"tool": "outlook_delete_event", "event_key": "eventId",
                   "args_verified": True, "answer_verified": False},
        "notify": {"create": "always", "move": "always", "cancel": "always"},
    },
    # cr1#1800: the connector's schema (read 2026-11-24, no call made) asks for
    # a local time with no offset and the zone in `timeZone`, and marks
    # create_event's `attendeeEmails` deprecated for `attendees` (objects with an email).
    # suggest_time's schema was read the same evening (review of #1804):
    # attendeeEmails, startTime and endTime required, local time with no
    # offset, the zone in timeZone. Still unverified:
    # no Google seat has pressed Book, Move or a free-time look.
    "google_calendar": {
        "server": "Google Calendar", "time": "local",
        "free": {"tool": "suggest_time", "people_key": "attendeeEmails", "from_key": "startTime",
                 "to_key": "endTime", "minutes_key": "durationMinutes", "tz_key": "timeZone",
                 "bounds": "local", "args": {},
                 "args_verified": False, "answer_verified": False},
        "create": {"tool": "create_event", "title_key": "summary", "start_key": "startTime",
                   "end_key": "endTime", "tz_key": "timeZone", "people_key": "attendees",
                   "people_form": "email_objects", "video_key": "addGoogleMeetUrl",
                   "extra": {"notificationLevel": "NONE"},
                   "args_verified": False, "answer_verified": False},
        "move": {"tool": "update_event", "event_key": "eventId", "start_key": "startTime",
                 "end_key": "endTime", "tz_key": "timeZone", "extra": {"notificationLevel": "NONE"},
                 "args_verified": False, "answer_verified": False},
        "cancel": {"tool": "delete_event", "event_key": "eventId", "extra": {"notificationLevel": "NONE"},
                   "args_verified": False, "answer_verified": False},
        "notify": {"create": "off", "move": "off", "cancel": "off"},
    },
}
#: What the page says, before the press, about the provider's own notice.
CALENDAR_NOTIFY_LINES = {
    "create": {"always": "{server} sends its own invitation to the people you add.",
               "unknown": "{server} may send its own invitation to the people you add.",
               "off": "{server} is asked to send the people you add nothing."},
    "move": {"always": "{server} sends its own update to the people invited when a meeting moves.",
             "unknown": "{server} may send its own update to the people invited when a meeting moves.",
             "off": "{server} is asked to send the people invited nothing when a meeting moves."},
    "cancel": {"always": "{server} sends its own cancellation to the people invited, and that cannot be unsent.",
               "unknown": "{server} may send its own cancellation to the people invited.",
               "off": "{server} is asked to send the people invited nothing."},
}
CALENDAR_CHANGES = ("create", "move", "cancel")
#: Said where the seat's calendar connector has no tool that cancels (shortened by cr1#1591).
CALENDAR_NO_CANCEL_LINE = "Cancel it in {server}."
#: Said on every cancel before the confirming press: no connector restores one.
CALENDAR_NO_RESTORE_LINE = "A cancelled meeting cannot be brought back from the Hub."
#: Said on a booking where the connector cannot remove an event again (shortened by
#: cr1#1591; it stays in sight, a warning before a press that books).
CALENDAR_NO_UNBOOK_LINE = "Cannot be undone here. Cancel it in {server}."


#: The calendar read the page makes itself while it is open (cr1#1758, owner
#: ruling 111: a live Today). One map per calendar provider, beside
#: `CALENDAR_ACTIONS`: the connector's list tool and the name of each argument.
#:   `time`  how the tool takes the two ends of the window: `local` (the clock
#:           time in the zone named by `tz_key`, with no offset) or `rfc3339`.
#:   `args`  what every call carries; `size_key` the page size, `page_in` the
#:           argument a next page's token goes in.
#:   `args_verified` the argument names were read from the connector's own
#:           tool schema (Google Calendar's `list_events` was, on 2026-11-24).
#:           `calendar_live` switches nothing on for a tool marked False.
#:           Microsoft 365's connector has no list tool: its one list-shaped
#:           read, `outlook_calendar_search`, is a search (a query is required,
#:           25 a page, paged by an offset carried in a trailing item, times
#:           as wall-clock pairs) that this reader does not take, so it has no
#:           row. Superhuman's calendar has no list tool (a free-text question
#:           only), so it has no row either: a seat on either shows the built
#:           Today feed only.
#:           `answer_verified` is False everywhere: no answer was read from a
#:           real connector, so the page reads each answer under every key it
#:           is described to use, and shows the built feed when it reads none.
CALENDAR_LIVE = {
    "google_calendar": {"server": "Google Calendar", "list": "list_events", "time": "local",
                        "from_key": "startTime", "to_key": "endTime", "tz_key": "timeZone",
                        "size_key": "pageSize", "page_in": "pageToken",
                        "args": {"orderBy": "startTime", "pageSize": 250},
                        "args_verified": True, "answer_verified": False},
}
#: How far the page's own calendar read reaches: from this many hours back
#: (a meeting that just ended keeps its row) to this many days ahead (the
#: Week view's seven days).
CALENDAR_LIVE_BACK_HOURS = 2
CALENDAR_LIVE_AHEAD_DAYS = 7
#: The characters of the hash a join key keeps (128 bits of SHA-256).
EVENT_KEY_LEN = 32


def event_key(value: Any) -> str:
    """READ/COMPUTE. cr1#1758: the join key of one calendar event or series:
    the first `EVENT_KEY_LEN` hex characters of the SHA-256 of `ev:` and the
    id exactly as the calendar connector returned it (an id's capitals are
    part of it). `` for no id. The page hashes each live id the same way, so
    a built row and a live event meet without the feed carrying the id of a
    personal event: a key names an event and can be used to change nothing."""
    v = str(value if value is not None else "").strip()
    if not v or len(v) > 512:
        return ""
    return hashlib.sha256(("ev:" + v).encode("utf-8")).hexdigest()[:EVENT_KEY_LEN]


def calendar_key(value: Any) -> str:
    """READ/COMPUTE. cr1#1815: the key of the calendar one event was read
    from: `event_key` of `cal:` and the calendar's id in lower case (a
    calendar id is an address or a name, never told apart by its capitals).
    `` for no id. The feed never carries the id itself: a personal or a
    shared calendar's id can be someone's address."""
    v = str(value if value is not None else "").strip().lower()
    return event_key("cal:" + v) if v else ""


def calendar_kind(workspace_root) -> str:
    """READ ONLY. cr1#1815: the provider of the seat's declared calendar
    connector (`google_calendar`, ...) in lower case, or `` when none is
    declared or it cannot be read. Never raises."""
    if workspace_root is None:
        return ""
    try:
        import connector_config
        row = connector_config.declared_backend("calendar", workspace_root)
    except Exception:  # noqa: BLE001 - nothing declared is a supported state
        return ""
    return str(row.get("provider") or "").strip().lower() if isinstance(row, dict) else ""


def calendar_stamp(workspace_root) -> Dict[str, Any]:
    """READ ONLY. cr1#1815: what a build stamps each meeting with: `kind`, the
    seat's calendar connector (`calendar_kind`), and `one_account`, True only
    when the seat names exactly one account (then a main calendar the read
    calls `primary` can only be that account's). Never raises."""
    try:
        import connector_config
        one = len(connector_config.accounts(workspace_root)) == 1 if workspace_root is not None else False
    except Exception:  # noqa: BLE001 - unknown is not one
        one = False
    return {"kind": calendar_kind(workspace_root), "one_account": one}


def _calendar_of(m: Dict[str, Any], one_account: bool) -> str:
    """cr1#1815 review: the calendar one event was read from, as the stamp
    names it. A main calendar the read calls `primary` is named by its
    account's address where the event says it (the owner's own entry on it,
    one address only); with no address it stays `primary` only on a seat of
    one account. On a seat of more than one account two `primary` calendars
    are not the same calendar, so such an event is stamped with nothing."""
    cal = str(m.get("calendar_id") or "").strip().lower()
    if cal != "primary":
        return cal
    mine = {str(a.get("email") or "").strip().lower() for a in (m.get("attendees") or [])
            if isinstance(a, dict) and a.get("self") is True and str(a.get("email") or "").strip()}
    if len(mine) == 1:
        return mine.pop()
    return cal if one_account else ""


def _join_keys(m: Dict[str, Any], stamp: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """`{jk, sk}` for one event of the run's calendar read, each only when
    the read gave its id: `jk` from `event_id`, `sk` from `series_id` (the
    recurring series it belongs to). Every row carries them, a personal and
    an all-day one too (cr1#1758, coordinator decision 1).

    cr1#1815: with them `ck`, the key of the calendar the event was read
    from (`calendar_id`), and `ckind`, the connector it was read through,
    both only when the read named the calendar and the seat names its
    connector. The page strikes a single meeting it no longer finds only
    where its own read covers that calendar on that connector. The key of
    a shared calendar is a hash of its id, an address, unsalted: the same
    class as the wall's own hashes (`_calendar_of` names the calendar)."""
    out: Dict[str, str] = {}
    jk = event_key(m.get("event_id"))
    if jk:
        out["jk"] = jk
    sk = event_key(m.get("series_id"))
    if sk:
        out["sk"] = sk
    kind = str((stamp or {}).get("kind") or "")
    ck = calendar_key(_calendar_of(m, (stamp or {}).get("one_account") is True))
    if ck and kind:
        out["ck"] = ck
        out["ckind"] = kind
    return out


def calendar_live(workspace_root=None, *, include_unverified: bool = False) -> Optional[Dict[str, Any]]:
    """The page's plan for reading the calendar itself (cr1#1758): `{provider,
    server, list, time, from_key, to_key, tz_key, size_key, page_in, args,
    back_hours, ahead_days, key_len, tools}`, or None when the page reads no
    calendar and Today shows the built feed only: no calendar connector
    declared, a provider with no list map, a list tool whose argument names
    were not read from the connector (unless `include_unverified`, which only
    a suite passes), or a calendar connector the personal wall closes (one
    marked personal is never called, and a wall that cannot be read closes
    it too). READ ONLY; never raises."""
    if workspace_root is None:
        return None
    try:
        import connector_config
        row = connector_config.declared_backend("calendar", workspace_root)
    except Exception:  # noqa: BLE001 - nothing declared is a supported state
        row = None
    if not isinstance(row, dict):
        return None
    provider = str(row.get("provider") or "").strip().lower()
    plan = CALENDAR_LIVE.get(provider)
    if not plan or not (plan.get("args_verified") is True or include_unverified):
        return None
    try:
        import connector_reads
        cwall = connector_reads.chat_wall(workspace_root)
        if not cwall.get("readable") or any(str(c.get("category") or "") == "calendar"
                                            for c in cwall.get("connectors") or []):
            return None
    except Exception:  # noqa: BLE001 - fail closed
        return None
    sid = str(row.get("server_id") or "").strip()
    server = connector_display_name(sid) if sid and not _UUIDISH_RE.match(sid) else plan["server"]
    out: Dict[str, Any] = {k: json.loads(json.dumps(v)) for k, v in plan.items()
                           if k not in ("server", "args_verified", "answer_verified")}
    out.update({"provider": provider, "server": server, "back_hours": CALENDAR_LIVE_BACK_HOURS,
                "ahead_days": CALENDAR_LIVE_AHEAD_DAYS, "key_len": EVENT_KEY_LEN, "tools": [plan["list"]]})
    return out


def calendar_actions(workspace_root=None, *, include_unverified: bool = False) -> Optional[Dict[str, Any]]:
    """The page's plan for booking, moving and cancelling a meeting on this
    seat (cr1#1539): `{provider, server, time, free, create, move, cancel,
    notify, notify_lines, no_cancel_line, no_restore_line, no_unbook_line,
    tools, unverified}`, or None when the page may change nothing itself
    (its three buttons then open a new chat with the request typed in): no
    calendar connector declared, a provider with no map, a calendar connector
    the personal wall closes (one marked personal is never called, and a wall
    that cannot be read closes it too), or no change tool whose argument
    names were read from the connector. A tool marked `args_verified: False`
    is left out (named in `unverified`) unless `include_unverified`, which
    only a suite passes."""
    if workspace_root is None:
        return None
    try:
        import connector_config
        row = connector_config.declared_backend("calendar", workspace_root)
    except Exception:  # noqa: BLE001 - nothing declared is a supported state
        row = None
    if not isinstance(row, dict):
        return None
    provider = str(row.get("provider") or "").strip().lower()
    plan = CALENDAR_ACTIONS.get(provider)
    if not plan:
        return None
    try:
        import connector_reads
        cwall = connector_reads.chat_wall(workspace_root)
        if not cwall.get("readable") or any(str(c.get("category") or "") == "calendar"
                                            for c in cwall.get("connectors") or []):
            return None
    except Exception:  # noqa: BLE001 - fail closed
        return None
    sid = str(row.get("server_id") or "").strip()
    server = connector_display_name(sid) if sid and not _UUIDISH_RE.match(sid) else plan["server"]
    out: Dict[str, Any] = {"provider": provider, "server": server, "time": plan["time"]}
    tools: List[str] = []
    unverified: List[str] = []
    for key in ("free",) + CALENDAR_CHANGES:
        spec = plan.get(key)
        if isinstance(spec, dict) and not (spec.get("args_verified") is True or include_unverified):
            unverified.append(key)
            spec = None
        out[key] = json.loads(json.dumps(spec)) if isinstance(spec, dict) else None
        if out[key] and out[key]["tool"] not in tools:
            tools.append(out[key]["tool"])
    if not (out["create"] or out["move"] or out["cancel"]):
        return None
    notify = {k: v for k, v in (plan.get("notify") or {}).items() if out.get(k)}
    out["notify"] = notify
    out["notify_lines"] = {k: CALENDAR_NOTIFY_LINES[k][v].format(server=server)
                           for k, v in notify.items() if v in CALENDAR_NOTIFY_LINES.get(k, {})}
    out["no_cancel_line"] = CALENDAR_NO_CANCEL_LINE.format(server=server)
    out["no_restore_line"] = CALENDAR_NO_RESTORE_LINE
    out["no_unbook_line"] = CALENDAR_NO_UNBOOK_LINE.format(server=server)
    out["tools"] = tools
    out["unverified"] = unverified
    return out


def calendar_direct(workspace_root=None) -> Optional[Dict[str, Any]]:
    """The page's calendar-answer plan for this seat (cr1#723): `{provider,
    server, respond, event_key, answer_key, answers, extra, verified,
    admin_line, tools}`, or None when the seat declares no calendar connector
    or one with no respond tool (then the rows show no calendar answers)."""
    if workspace_root is None:
        return None
    try:
        import connector_config
        row = connector_config.declared_backend("calendar", workspace_root)
    except Exception:  # noqa: BLE001 - nothing declared is a supported state
        row = None
    if not isinstance(row, dict):
        return None
    provider = str(row.get("provider") or "").strip().lower()
    plan = CALENDAR_DIRECT.get(provider)
    if not plan:
        return None
    sid = str(row.get("server_id") or "").strip()
    server = connector_display_name(sid) if sid and not _UUIDISH_RE.match(sid) else plan["server"]
    return {"provider": provider, "server": server, "respond": plan["respond"], "event_key": plan["event_key"],
            "answer_key": plan["answer_key"], "answers": dict(plan["answers"]), "extra": dict(plan.get("extra") or {}),
            "verified": bool(plan.get("verified")), "admin_line": plan["admin_line"], "tools": [plan["respond"]]}


def admin_check(workspace_root=None, *, scopes: Optional[List[str]] = None) -> Dict[str, Any]:
    """READ. What the client's admin must still switch on for the page's Send
    and calendar answers (cr1#723). On a Microsoft 365 seat the caller hands
    in the scopes `get_granted_scopes` returned; a missing `Mail.Send` or
    `Calendars.ReadWrite` earns its one plain line. Google has no such read:
    the answer names the first press as the check. A seat with neither
    connector answers nothing to say. Answers `{ok, lines, send_ok,
    calendar_ok, provider_mail, provider_calendar}`."""
    mail = mail_direct(workspace_root)
    cal = calendar_direct(workspace_root)
    out: Dict[str, Any] = {"ok": True, "lines": [], "send_ok": None, "calendar_ok": None,
                           "provider_mail": (mail or {}).get("provider"), "provider_calendar": (cal or {}).get("provider")}
    granted = {str(s).strip() for s in (scopes or []) if str(s).strip()} if scopes is not None else None
    if mail and mail["provider"] == "outlook":
        if granted is not None:
            out["send_ok"] = MS_SCOPES["send"] in granted
            if not out["send_ok"]:
                out["lines"].append(MAIL_ADMIN_LINE["outlook"])
    elif mail and mail["provider"] == "gmail":
        out["lines"].append("Gmail has no switch the page can read: the first Send on the Hub is the check, and it says in one line what the admin must do if it is refused.")
    if cal and cal["provider"] == "outlook_calendar":
        if granted is not None:
            out["calendar_ok"] = MS_SCOPES["calendar"] in granted
            if not out["calendar_ok"]:
                out["lines"].append(cal["admin_line"])
    elif cal and cal["provider"] == "google_calendar":
        out["lines"].append("Google Calendar has no switch the page can read: the first Accept on the Hub is the check, and it says in one line what the admin must do if it is refused.")
    out["ok"] = not any(v is False for v in (out["send_ok"], out["calendar_ok"]))
    return out


def mail_direct(workspace_root=None) -> Optional[Dict[str, Any]]:
    """The page's direct-send plan for this seat: `{provider, server, read,
    draft, send, tools}`, or None when the seat declares no mail connector
    or one the page cannot send through (then Send stays on Apply). The
    server is the declared connector's display name; a declared id that is
    a UUID (it names no connector by display) takes the provider's usual
    name."""
    if workspace_root is None:
        return None
    try:
        import connector_config
        row = connector_config.declared_backend("email", workspace_root)
    except Exception:  # noqa: BLE001 - nothing declared is a supported state
        row = None
    if not isinstance(row, dict):
        return None
    provider = str(row.get("provider") or "").strip().lower()
    plan = MAIL_DIRECT.get(provider)
    if not plan:
        return None
    sid = str(row.get("server_id") or "").strip()
    server = connector_display_name(sid) if sid and not _UUIDISH_RE.match(sid) else plan["server"]
    tools: List[str] = []
    for t in (plan["read"], plan["draft"], plan["send"], plan.get("thread"), plan.get("new_draft"),
              plan.get("reply_all_draft"), plan.get("set_recipients"), plan.get("list")):
        if t and t not in tools:
            tools.append(t)
    out = {"provider": provider, "server": server, "read": plan["read"],
           "draft": plan["draft"], "send": plan["send"], "thread": plan.get("thread"),
           "new_draft": plan.get("new_draft"), "tools": tools, "admin_line": MAIL_ADMIN_LINE.get(provider)}
    if plan.get("reply_all_draft") and plan.get("set_recipients"):
        out["reply_all_draft"] = plan["reply_all_draft"]
        out["set_recipients"] = plan["set_recipients"]
    # cr1#1538: the read-only list tool for live arrivals, with its contract.
    if plan.get("list") and provider in MAIL_LIST:
        out["list"] = plan["list"]
        out["list_plan"] = json.loads(json.dumps(MAIL_LIST[provider]))
    return out


def chat_direct(workspace_root=None) -> Optional[Dict[str, Any]]:
    """The page's chat plan for this seat (cr1#1538): `{provider, server,
    list, args, after_key, after_kind, days, tools, ...}`, or None when the
    seat declares no chat connector, one the page has no tool for, or one the
    personal wall closes (`hub_live.wall`: a chat connector marked personal is
    never called, and a wall that cannot be read closes it too). None shows
    no chat lines and no error."""
    if workspace_root is None:
        return None
    try:
        import connector_config
        row = connector_config.declared_backend("chat", workspace_root)
    except Exception:  # noqa: BLE001 - nothing declared is a supported state
        row = None
    if not isinstance(row, dict):
        return None
    provider = str(row.get("provider") or "").strip().lower()
    plan = CHAT_DIRECT.get(provider)
    if not plan:
        return None
    try:
        import hub_live
        if not hub_live.wall(workspace_root).get("chat_ok"):
            return None
    except Exception:  # noqa: BLE001 - fail closed
        return None
    sid = str(row.get("server_id") or "").strip()
    server = connector_display_name(sid) if sid and not _UUIDISH_RE.match(sid) else plan["server"]
    out = json.loads(json.dumps(plan))
    out.update({"provider": provider, "server": server, "days": CHAT_DAYS, "tools": [plan["list"]]})
    return out


def capabilities(server: str, mail: Optional[Dict[str, Any]] = None,
                 calendar: Optional[Dict[str, Any]] = None,
                 chat: Optional[Dict[str, Any]] = None, *,
                 ask: bool = True, chat_server: bool = True,
                 calendar_actions: Optional[Dict[str, Any]] = None,
                 files: bool = True,
                 calendar_live: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The runtime capabilities the published page declares. The database
    rule: everything in it, feeds, status, config and choices, is readable
    and writable by the artifact's owner alone (plan item 2: the page shows
    the owner's own people and work, and nobody the page is shared with
    reads its data). The connector is named by its display name whatever
    spelling the caller passed (`connector_display_name`). `mail`, from
    `mail_direct`, adds the seat's mail connector with the tools the page
    sends through (cr1#710). `chat`, from `chat_direct`, adds the seat's chat
    connector with its one read-only tool (cr1#1538). `sample` lets the page
    ask Claude for the one-line read of a live arrival (cr1#1538): the viewer
    is asked on the first read, and a view without it shows "not read yet".
    `ask=False` leaves `sample` out and `chat_server=False` leaves the chat
    connector out, for an account whose publish refuses one of them
    (`render_page_file`'s `capabilities_without`); the page copes with
    either being absent. `calendar_actions`, from the function of that name,
    adds the calendar tools the page books, moves and cancels a meeting
    with (cr1#1539), on the calendar connector's own entry; left out (None),
    the declaration is what it was before, and the page's three buttons say
    they are not allowed yet. `assets` (cr1#1632, Attached) is the page's own
    file store, where a file the owner attaches to a meeting is kept: the
    owner alone uploads (the capability's default, Editors and up, on a page
    never shared); `files=False` leaves it out, for an account whose publish
    refuses it, and the page then offers a link only."""
    server = connector_display_name(server)
    servers = [{"server": str(server),
                "tools": ["list_triggers", "update_trigger", "fire_trigger"]}]
    if isinstance(mail, dict) and mail.get("server") and mail.get("tools"):
        servers.append({"server": str(mail["server"]), "tools": list(mail["tools"])})
    # cr1#723: the calendar connector, with its one respond tool. A seat whose
    # mail and calendar share one connector (Microsoft 365) declares it once.
    if isinstance(calendar, dict) and calendar.get("server") and calendar.get("tools"):
        for s in servers:
            if s["server"] == str(calendar["server"]):
                s["tools"] = list(s["tools"]) + [t for t in calendar["tools"] if t not in s["tools"]]
                break
        else:
            servers.append({"server": str(calendar["server"]), "tools": list(calendar["tools"])})
    # cr1#1539: the tools that book, move and cancel a meeting, on the same
    # connector as the answers (or its own entry on a seat with no answers).
    if isinstance(calendar_actions, dict) and calendar_actions.get("server") and calendar_actions.get("tools"):
        for s in servers:
            if s["server"] == str(calendar_actions["server"]):
                s["tools"] = list(s["tools"]) + [t for t in calendar_actions["tools"] if t not in s["tools"]]
                break
        else:
            servers.append({"server": str(calendar_actions["server"]), "tools": list(calendar_actions["tools"])})
    # cr1#1758: the calendar's one read-only list tool, for the live Today.
    if isinstance(calendar_live, dict) and calendar_live.get("server") and calendar_live.get("tools"):
        for s in servers:
            if s["server"] == str(calendar_live["server"]):
                s["tools"] = list(s["tools"]) + [t for t in calendar_live["tools"] if t not in s["tools"]]
                break
        else:
            servers.append({"server": str(calendar_live["server"]), "tools": list(calendar_live["tools"])})
    # cr1#1538: the chat connector, with its one read-only tool. Teams rides
    # the Microsoft 365 connector a seat's mail may already declare.
    if chat_server and isinstance(chat, dict) and chat.get("server") and chat.get("tools"):
        for s in servers:
            if s["server"] == str(chat["server"]):
                s["tools"] = list(s["tools"]) + [t for t in chat["tools"] if t not in s["tools"]]
                break
        else:
            servers.append({"server": str(chat["server"]), "tools": list(chat["tools"])})
    out: Dict[str, Any] = {
        "db": {
            "rules": [
                {"path": "", "read": "owner", "write": "owner"},
            ]
        },
        "user": {},
        "mcp": {"servers": servers},
    }
    if ask:
        out["sample"] = {}
    if files:
        out["assets"] = {}
    return out


def _script_safe(js: str) -> str:
    """JSON made safe inside a <script>: no value can close the tag."""
    return (js.replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")
#: Where the template takes the live calendar plan (cr1#1758).
CALENDAR_LIVE_MARK = "/*{{CALENDAR_LIVE_JSON}}*/null"


def render_page(template_text: str, *, server: str, basename: str,
                plugin_version: str, published_at: Optional[str] = None,
                mail: Optional[Dict[str, Any]] = None,
                calendar: Optional[Dict[str, Any]] = None,
                runtime_version: Optional[str] = None,
                chat: Optional[Dict[str, Any]] = None,
                calendar_actions: Optional[Dict[str, Any]] = None,
                calendar_live: Optional[Dict[str, Any]] = None) -> str:
    """Fill the template mechanically and refuse a page with a placeholder
    left over or a leak in its visible text. Nothing here is typed by hand:
    cockpit-setup calls this and publishes what it returns. The footer shows
    the installed plugin version and, when the seat has one, the workspace
    runtime version (cr1#118)."""
    from chat_output_renderer import scan_rendered_html

    fills = {
        "{{TRIGGER_SERVER}}": connector_display_name(server),
        "{{WORKSPACE_BASENAME}}": str(basename or ""),
        "{{PLUGIN_VERSION}}": str(plugin_version or ""),
        "{{RUNTIME_VERSION_LINE}}": (" · runtime " + str(runtime_version).strip()) if str(runtime_version or "").strip() else "",
        "{{PUBLISHED_AT}}": str(published_at or _iso(_now())),
    }
    # The mail plan rides as JSON only (cr1#710); null keeps Send on Apply.
    mail_js = json.dumps(mail if isinstance(mail, dict) else None)
    mail_js = _script_safe(mail_js)
    template_text = template_text.replace("{{MAIL_DIRECT_JSON}}", mail_js)
    # The calendar plan rides as JSON too (cr1#723); null shows no calendar answers.
    cal_js = _script_safe(json.dumps(calendar if isinstance(calendar, dict) else None))
    template_text = template_text.replace("{{CALENDAR_DIRECT_JSON}}", cal_js)
    # The chat plan rides as JSON too (cr1#1538); null shows no chat lines.
    chat_js = _script_safe(json.dumps(chat if isinstance(chat, dict) else None))
    template_text = template_text.replace("{{CHAT_DIRECT_JSON}}", chat_js)
    # The plan for booking, moving and cancelling rides as JSON too
    # (cr1#1539); null makes the three buttons open a chat.
    act_js = _script_safe(json.dumps(calendar_actions if isinstance(calendar_actions, dict) else None))
    template_text = template_text.replace("{{CALENDAR_ACTIONS_JSON}}", act_js)
    # cr1#1758: the plan for the page's own calendar read; null shows the built
    # Today feed only. Its mark sits in a comment before a `null`, so a page
    # filled by anything that does not know it still runs, with no live read.
    live_js = _script_safe(json.dumps(calendar_live if isinstance(calendar_live, dict) else None))
    template_text = template_text.replace(CALENDAR_LIVE_MARK, live_js)
    for key in ("{{TRIGGER_SERVER}}", "{{WORKSPACE_BASENAME}}"):
        if not fills[key]:
            raise ValueError(f"render_page: {key} is required")
    html = template_text
    # The script's own config takes JSON, never HTML-escaped text: a folder
    # named "R&D" read as "R&amp;D" there and matched none of its tasks
    # (cr1#679). `<`, `>` and `&` are escaped inside the JSON, so no value can
    # close the script.
    for key, value in fills.items():
        js = json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        html = html.replace(key[:-2] + "_JSON}}", js)
    for key, value in fills.items():
        html = html.replace(key, _html_escape(value))
    left = sorted(set(_PLACEHOLDER_RE.findall(html)))
    if left:
        raise ValueError(f"render_page: placeholders left unfilled: {left}")
    scan_rendered_html(html)
    return html


def _html_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def template_path(plugin_root=None) -> Path:
    here = Path(__file__).resolve().parent.parent.parent
    base = Path(plugin_root) if plugin_root else here
    return base / TEMPLATE_REL


def render_page_file(workspace_root=None, *, server: str = "Claude Code Remote",
                     basename: Optional[str] = None, plugin_version: str = "",
                     published_at: Optional[str] = None,
                     plugin_root=None, runtime_version: Optional[str] = None,
                     recipe: bool = False) -> Dict[str, Any]:
    """The door form of `render_page`: reads the shipped template beside this
    module (the staged runtime ships `shared/templates/`), fills it, and
    answers `{ok, html, bytes_len, sha256, capabilities, title}` for
    cockpit-setup to publish as it came back. Never typed by hand, never
    edited after.

    `recipe=True` (HUBPUB1, cr1#925) answers the same without `html`, and
    with `recipe`: every value the page was filled from, the workspace's
    reads included. On a merged seat the page is rendered on the computer
    and the publish tool attaches only the chat's own files, and a ~150 KB
    page does not come back through one reply; the chat renders the same
    page on its own side with `render_from_recipe` and checks `sha256`."""
    if not basename:
        basename = (str(workspace_root or "").replace("\\", "/").rstrip("/")
                    .rsplit("/", 1)[-1])
    path = template_path(plugin_root)
    mail = mail_direct(workspace_root)
    # cr1#118: `plugin_version` is the installed plugin's, as the chat read it
    # from its own plugin.json; the runtime version is the door's
    # CR_RUNTIME_VERSION, else the plugin.json beside this module, which on a
    # merged seat is the staged runtime's own copy.
    runtime_version = (runtime_version or os.environ.get("CR_RUNTIME_VERSION")
                       or plugin_version_here(plugin_root) or None)
    calendar = calendar_direct(workspace_root)
    chat = chat_direct(workspace_root)
    cal_actions = calendar_actions(workspace_root)
    cal_live = calendar_live(workspace_root)
    # cr1#925: the stamp is fixed here, so the chat's own render of the
    # recipe is byte-for-byte this page.
    published_at = published_at or _iso(_now())
    html = render_page(path.read_text(encoding="utf-8"), server=server,
                       basename=basename, plugin_version=plugin_version,
                       published_at=published_at, mail=mail, calendar=calendar,
                       runtime_version=runtime_version, chat=chat, calendar_actions=cal_actions,
                       calendar_live=cal_live)
    out = {"ok": True, "html": html, "bytes_len": len(html.encode("utf-8")),
            "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "capabilities": capabilities(server, mail, calendar, chat, calendar_actions=cal_actions,
                                         calendar_live=cal_live),
            "title": "Command Room Hub",
            "mail_direct": (mail or {}).get("provider"), "plugin_version": plugin_version,
            "runtime_version": runtime_version,
            "calendar_direct": (calendar or {}).get("provider"),
            "calendar_verified": (calendar or {}).get("verified"),
            "chat_direct": (chat or {}).get("provider"),
            "calendar_actions": (cal_actions or {}).get("provider"),
            "calendar_live": (cal_live or {}).get("provider"),
            # cr1#1538: the same declaration with one new part left out, for a
            # publish that is refused naming it. The page is the same page.
            # cr1#1539: `calendar_actions` leaves out the tools that book, move
            # and cancel (the page's three buttons then say they are not
            # allowed yet); `all_new` leaves out every part added since
            # cr1#1537, which is the declaration a seat already allowed.
            # cr1#1632: `assets` leaves out the file store (the meeting page
            # then attaches a link only).
            # cr1#1758: `calendar_live` leaves out the calendar's list tool (Today
            # then shows the built feed only); every other one keeps it.
            "capabilities_without": {
                "sample": capabilities(server, mail, calendar, chat, ask=False, calendar_actions=cal_actions,
                                       calendar_live=cal_live),
                "chat": capabilities(server, mail, calendar, chat, chat_server=False, calendar_actions=cal_actions,
                                     calendar_live=cal_live),
                "sample_and_chat": capabilities(server, mail, calendar, chat, ask=False, chat_server=False,
                                                calendar_actions=cal_actions, calendar_live=cal_live),
                "calendar_actions": capabilities(server, mail, calendar, chat, calendar_live=cal_live),
                "calendar_live": capabilities(server, mail, calendar, chat, calendar_actions=cal_actions),
                "assets": capabilities(server, mail, calendar, chat, calendar_actions=cal_actions, files=False,
                                       calendar_live=cal_live),
                "all_new": capabilities(server, mail, calendar, chat, ask=False, chat_server=False, files=False)},
            "template": str(TEMPLATE_REL).replace("\\", "/")}
    if recipe:
        out.pop("html")
        out["recipe"] = {"server": server, "basename": basename,
                         "plugin_version": plugin_version,
                         "published_at": published_at, "mail": mail,
                         "calendar": calendar, "runtime_version": runtime_version,
                         "chat": chat, "calendar_actions": cal_actions,
                         "calendar_live": cal_live}
    return out


#: The one sentence when the chat's own render is not the page the door
#: rendered (HUBPUB1, cr1#925): nothing is published. The usual cause is the
#: saved answer, not the plugin, so it names no update.
RECIPE_MISMATCH_LINE = ("The Hub page built here did not match the one prepared "
                        "on your computer, so nothing was published; say "
                        "`rebuild my hub` to try again.")
#: The saved answer is not a usable render answer (no recipe, no hash, a
#: refused door reply, or not JSON).
RECIPE_UNREADABLE_LINE = ("The Hub page could not be prepared here, so nothing "
                          "was published; say `rebuild my hub` to try again.")


def render_from_recipe(recipe: Dict[str, Any], *, expect_sha256: Optional[str] = None,
                       plugin_root=None) -> Dict[str, Any]:
    """COMPUTE ONLY, no workspace (HUBPUB1, cr1#925). The page `render_page_file`
    rendered, rebuilt from its `recipe` on the chat's own side from the
    template this plugin ships. Answers `{ok, html, bytes_len, sha256}`; with
    `expect_sha256`, a page that differs answers `ok: false` with
    `RECIPE_MISMATCH_LINE` and no html, so a stale plugin never publishes."""
    if not isinstance(recipe, dict):
        return {"ok": False, "line": RECIPE_UNREADABLE_LINE, "reason": "no_recipe"}
    html = render_page(template_path(plugin_root).read_text(encoding="utf-8"),
                       server=str(recipe.get("server") or "Claude Code Remote"),
                       basename=str(recipe.get("basename") or ""),
                       plugin_version=str(recipe.get("plugin_version") or ""),
                       published_at=recipe.get("published_at"),
                       mail=recipe.get("mail"), calendar=recipe.get("calendar"),
                       runtime_version=recipe.get("runtime_version"),
                       chat=recipe.get("chat"), calendar_actions=recipe.get("calendar_actions"),
                       calendar_live=recipe.get("calendar_live"))
    raw = html.encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    if expect_sha256 and sha != str(expect_sha256).strip().lower():
        return {"ok": False, "line": RECIPE_MISMATCH_LINE, "reason": "sha_mismatch",
                "sha256": sha}
    return {"ok": True, "html": html, "bytes_len": len(raw), "sha256": sha}


def _render_recipe_cli(argv: List[str]) -> int:
    """`cockpit_helpers.py render-recipe <answer.json> <out.html>`: the chat's
    own render (cr1#925). `<answer.json>` is the door's `render_page_file`
    answer (its `result`, or the whole envelope) saved in this session's own
    scratch; the page is written to `<out.html>` there, and one JSON line
    `{ok, path, sha256, bytes_len}` or `{ok: false, line}` is printed."""
    if len(argv) != 2:
        print(json.dumps({"ok": False, "line": "usage: render-recipe <answer.json> <out.html>"}))
        return 2
    src, dst = Path(argv[0]), Path(argv[1])
    if "_hq" in dst.parts:
        print(json.dumps({"ok": False, "line": RECIPE_UNREADABLE_LINE, "reason": "out_in_workspace"}))
        return 1
    try:
        answer = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(json.dumps({"ok": False, "line": RECIPE_UNREADABLE_LINE, "reason": "unreadable_answer"}))
        return 1
    if isinstance(answer, dict) and answer.get("ok") is False:
        # A refused door reply carries its own sentence; it is the answer.
        print(json.dumps({"ok": False, "line": str(answer.get("line") or RECIPE_UNREADABLE_LINE),
                          "reason": "door_refused"}))
        return 1
    if isinstance(answer, dict) and isinstance(answer.get("result"), dict):
        answer = answer["result"]
    if not isinstance(answer, dict) or answer.get("ok") is False:
        print(json.dumps({"ok": False, "line": str((answer or {}).get("line") or RECIPE_UNREADABLE_LINE)
                          if isinstance(answer, dict) else RECIPE_UNREADABLE_LINE,
                          "reason": "door_refused"}))
        return 1
    if not str(answer.get("sha256") or "").strip():
        # cr1#925 review: an answer with no hash is never published unchecked.
        print(json.dumps({"ok": False, "line": RECIPE_UNREADABLE_LINE, "reason": "no_sha256"}))
        return 1
    got = render_from_recipe(answer.get("recipe"), expect_sha256=answer.get("sha256"))
    if not got.get("ok"):
        print(json.dumps({k: v for k, v in got.items() if k != "html"}))
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(got["html"].encode("utf-8"))
    print(json.dumps({"ok": True, "path": str(dst), "sha256": got["sha256"],
                      "bytes_len": got["bytes_len"]}))
    return 0


def plugin_version_here(plugin_root=None) -> Optional[str]:
    """The version in the `plugin.json` shipped beside this module (or under
    `plugin_root`), else None. The one read every version answer makes."""
    base = Path(plugin_root) if plugin_root else Path(__file__).resolve().parent.parent.parent
    try:
        payload = json.loads((base / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - no file, no version
        return None
    version = payload.get("version") if isinstance(payload, dict) else None
    return str(version).strip() if isinstance(version, str) and version.strip() else None


VERSION_LINE = "You are on Command Room {plugin}{runtime}."
VERSION_UNKNOWN_LINE = "I cannot read the installed version from here."


def version_answer(workspace_root=None, *, plugin_version: Optional[str] = None,
                   runtime_version: Optional[str] = None, plugin_root=None) -> Dict[str, Any]:
    """READ. The one-line answer to `which version am I on` (cr1#118, owner
    ruling 2026-11-06), from the files the plugin ships: `plugin_version` is
    the installed plugin's, read by the chat from its own `plugin.json` and
    handed in (on a merged seat this helper runs from the staged runtime, so
    the copy beside it is the runtime's, not the installed plugin's); the
    runtime version is the door's `CR_RUNTIME_VERSION`, else the `plugin.json`
    beside this module. With nothing handed in, the copy beside this module
    is the installed version (a seat whose files are local). No fleet view,
    nothing reported home. Answers `{plugin_version, runtime_version, line}`."""
    here = plugin_version_here(plugin_root)
    plugin = str(plugin_version or "").strip() or here
    runtime = str(runtime_version or os.environ.get("CR_RUNTIME_VERSION") or "").strip() or here
    if not plugin:
        return {"plugin_version": None, "runtime_version": runtime, "line": VERSION_UNKNOWN_LINE}
    tail = "" if not runtime or runtime == plugin else f", workspace runtime {runtime}"
    return {"plugin_version": plugin, "runtime_version": runtime,
            "line": VERSION_LINE.format(plugin=plugin, runtime=tail)}


# ---------------------------------------------------------------------------
# the status lights
# ---------------------------------------------------------------------------

def status_doc(workspace_root=None, leg: str = "refresh", state: str = "idle",
               line: str = "", *, now_iso=None, run_id: Optional[str] = None,
               stop_reason: Optional[str] = None) -> Dict[str, Any]:
    """One status row for one leg: `{leg, state, line, at, run_id,
    stop_reason}`. States are `STATUS_STATES`; an unknown one is an error
    line, never a fourth state."""
    leg_id = str(leg or "").strip()
    if leg_id not in LEGS and leg_id != "maintenance":
        raise ValueError(f"status_doc: unknown leg {leg!r}")
    st = str(state or "").strip()
    if st not in STATUS_STATES:
        raise ValueError(f"status_doc: unknown state {state!r}")
    text = str(line or "").strip()
    if st == "running" and not text:
        text = STATUS_RUNNING_LINE
    return {"leg": leg_id, "state": st, "line": text[:300],
            "at": _iso(_now(now_iso)), "run_id": run_id or None,
            "stop_reason": (str(stop_reason)[:300] if stop_reason else None)}


def running_timed_out(doc: Dict[str, Any], *, now_iso=None,
                      minutes: int = RUNNING_TIMEOUT_MINUTES) -> bool:
    """A light stuck on running past the timeout has no answer: a bootloader
    stop posts a line and writes nothing (cr1#603), so the page cannot wait
    forever."""
    if not isinstance(doc, dict) or doc.get("state") != "running":
        return False
    at = _parse(doc.get("at"))
    if at is None:
        return True
    return (_now(now_iso) - at) >= _dt.timedelta(minutes=minutes)


def settle_statuses(workspace_root=None, docs: Optional[List[Dict[str, Any]]] = None,
                    *, now_iso=None) -> List[Dict[str, Any]]:
    """The refresh leg's first act: every light a dead run left on running
    turns to `no_answer` with its line, so a fresh run never inherits a stale
    running light."""
    out = []
    now = _now(now_iso)
    for doc in docs or []:
        if not isinstance(doc, dict):
            continue
        # review of #1664 (item 6): a light of a leg this version does not
        # know is left alone, never a ValueError out of status_doc
        if str(doc.get("leg") or "").strip() not in LEGS and str(doc.get("leg") or "").strip() != "maintenance":
            continue
        minutes = RUNNING_TIMEOUT_BY_LEG.get(str(doc.get("leg") or ""), RUNNING_TIMEOUT_MINUTES)
        if running_timed_out(doc, now_iso=now_iso, minutes=minutes):
            settled = _carry_full_at(doc, status_doc(workspace_root, doc.get("leg"), "no_answer",
                                                     STATUS_NO_ANSWER_LINE.format(minutes=minutes),
                                                     now_iso=now_iso, run_id=doc.get("run_id"),
                                                     stop_reason=doc.get("stop_reason")), now)
            # Fix check of #1776 (must): a dead morning's record of the parts it
            # did belongs to the morning, not to the run. Settling must keep `done`,
            # or the next Go runs every part again and the brief goes out twice.
            if doc.get("leg") == "morning" and isinstance(doc.get("done"), dict):
                settled["done"] = dict(doc["done"])
            out.append(settled)
        elif doc.get("state") == "waiting":
            # cr1#679: a Refresh light left waiting on an Apply that never
            # finished stops saying it waits.
            at = _parse(doc.get("at"))
            if at is None or (now - at) >= _dt.timedelta(minutes=WAITING_TIMEOUT_MINUTES):
                out.append(_carry_full_at(doc, status_doc(workspace_root, doc.get("leg"), "error",
                                                          STATUS_WAIT_DROPPED_LINE, now_iso=now_iso), now))
    return out


def _carry_full_at(was: Any, light: Dict[str, Any], now: Optional[_dt.datetime]) -> Dict[str, Any]:
    """cr1#1809: a light that replaces the Update's own carries its record of
    the last whole rebuild on (only a time no later than `now`), so a press
    after a run that never answered is not made a whole rebuild for nothing."""
    if isinstance(was, dict) and str(light.get("leg") or "") == "update":
        held = _full_at_of(was.get("full_at"), now)
        if held:
            light["full_at"] = held
    return light


#: The hours past which Maintenance counts as behind, carried on
#: `status/maintenance` as `behind_hours` (cr1#666). It is data only: the
#: Hub's Start runs Maintenance on every press whatever it says (cr1#682).
START_MAINTENANCE_BEHIND_HOURS = 6
MAINTENANCE_DONE_LINE = "Maintenance last finished."
MAINTENANCE_NONE_LINE = "No Maintenance run recorded yet."


def _maintenance_history(workspace_root):
    """`(newest_receipt_dt, last_completed, newest_heartbeat_data)` from the
    book. `last_completed` folds every `maintenance_run`: a job's own
    `completed_at` when the receipt has one; the receipt's own time only for a
    receipt that stamps nothing (from before cr1#666). A job a stamping receipt
    lists as completed without a stamp has no proven completion there
    (cr1#760), so a blocked job never reads fresh off a later fire."""
    import maintenance_dispatcher as md
    from receipts import iter_receipts

    newest = None
    newest_via = None
    last: Dict[str, _dt.datetime] = {}
    for r in iter_receipts(workspace_root, task_ids=[md.MAINTENANCE_TASK_ID]):
        if r.get("type") != md.RECEIPT_EVENT_TYPE or r.get("dt") is None:
            continue
        at = r["dt"]
        if newest is None or at > newest:
            newest = at
            # cr1#743: how the newest fire came about, so the Hub can say a
            # scheduled fire left the note, not only a typed run.
            newest_via = r.get("fired_via")
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        has_stamps = isinstance(data.get(md.COMPLETED_AT_FIELD), dict)
        stamped = data.get(md.COMPLETED_AT_FIELD) if has_stamps else {}
        for job in data.get("jobs_completed") or []:
            if not isinstance(job, str) or not job:
                continue
            when = _parse(stamped.get(job))
            if when is None:
                # cr1#760: a receipt that stamps its jobs and leaves this one
                # out never proved it ran (a job listed with no receipt of its
                # own); the fire's own time is only for a receipt from before
                # cr1#666, which stamped nothing.
                if has_stamps:
                    continue
                when = at
            if job not in last or when > last[job]:
                last[job] = when
    beat = None
    from events_io import load_events_org_scoped

    events, _skipped = load_events_org_scoped(workspace_root)
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == md.HEARTBEAT_EVENT_TYPE \
                and isinstance(ev.get("data"), dict):
            beat = ev["data"]
    return newest, last, beat, newest_via


def _fires(n: int) -> str:
    return f"{n} fire" if n == 1 else f"{n} fires"


#: cr1#1590: what a job or connector note offers: a new chat with the health
#: check typed in, never sent.
NOTE_ACT_RUNNING = {"label": "Is everything running?", "chat": "is everything running"}


def _few(names, what: str, *, runs: int = 0, many: str = "jobs") -> str:
    """cr1#1590: a note's few words for one or more names: "Morning Brief
    failing, 3 runs", "Morning Brief, Learning failing", "4 jobs failing"."""
    names = [str(n) for n in names if str(n or "").strip()]
    if not names:
        return ""
    if len(names) == 1:
        return f"{names[0]} {what}" + (f", {runs} {'run' if runs == 1 else 'runs'}" if runs else "")
    if len(names) == 2:
        return f"{names[0]}, {names[1]} {what}"
    return f"{len(names)} {many} {what}"




def _maintenance_trouble(workspace_root) -> Dict[str, Any]:
    """What the fires themselves say went wrong (cr1#822): each job failing and
    each connector not confirmed read, off the receipts, so a scheduled fire is
    read like a typed one. `fires` tells a failing job from one that only left
    a connector unread. A connector is one entry however many jobs read it, by
    the label `run maintenance` says. The receipt keeps no line between "not
    read" and "not confirmed as read", so the words claim only the second.
    `left_unread` (cr1#979) is the due connector jobs the newest run left
    unread; `silent` (cr1#1007) the sources that usually bring items in and
    have read nothing on their last runs. `chats` is always `{}`: the chat
    capture's "not being read" line (#1500) is never said to a client (owner
    ruling 2026-11-22 on cr1#1489, "Yes, hide it"; `session_sweep.
    chats_unread` is operator-only), and the field stays for the released
    pages, which read it empty as no note. Answers `{failing, unread,
    left_unread, silent, chats, line}`; `line` is empty when there is
    nothing to say, and an unreadable read says nothing.

    cr1#1590: `notes` holds one `{kind, short, act, line}` a sentence of
    `line`, for the Hub's note rows: `kind` is
    `fail` or `look`, `short` a few words, `act` the one thing to do (a new
    chat with a phrase typed in) and `line` that sentence, word for word."""
    import connector_reads as cr
    import maintenance_dispatcher as md
    from schedule_config import task_display_name

    try:
        rows = md.maintenance_failing_jobs(workspace_root)
    except Exception:  # noqa: BLE001 - the light keeps what it knows
        rows = []
    failing = [{"job_id": r["job_id"], "name": task_display_name(r["job_id"]), "fires": int(r.get("fires") or 0),
                "since": r.get("since")} for r in rows if r.get("fires")]
    gaps: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        for c in r.get("connectors_unread") or []:
            label = cr._labels(workspace_root, [c.get("category")])[0]
            gap = gaps.setdefault(label, {"label": label, "category": c.get("category"), "jobs": [], "fires": 0, "since": c.get("since")})
            gap["jobs"].append(r["job_id"])
            if int(c.get("fires") or 0) > gap["fires"]:  # the longest run, its start with it
                gap.update(fires=int(c.get("fires") or 0), since=c.get("since"))
    unread = [gaps[k] for k in sorted(gaps)]
    try:
        # cr1#979: the due connector jobs the newest run left unread.
        left = md.maintenance_left_unread(workspace_root)
    except Exception:  # noqa: BLE001
        left = []
    bits = []
    notes = []
    if failing:
        bits.append("Failing: " + ", ".join(f"{f['name']} ({_fires(f['fires'])})" for f in failing) + ".")
        notes.append({"kind": "fail", "short": _few([f["name"] for f in failing], "failing",
                                                     runs=failing[0]["fires"] if len(failing) == 1 else 0),
                      "act": dict(NOTE_ACT_RUNNING), "line": bits[-1]})
    if unread:
        bits.append("Not confirmed read: " + ", ".join(f"{u['label']} ({_fires(u['fires'])})" for u in unread) + ".")
        notes.append({"kind": "look", "short": _few([u["label"] for u in unread], "not confirmed read",
                                                     runs=unread[0]["fires"] if len(unread) == 1 else 0, many="connectors"),
                      "act": dict(NOTE_ACT_RUNNING), "line": bits[-1]})
    if left:
        bits.append("Due and not run on the last run: " + ", ".join(task_display_name(j) for j in left) + ".")
        notes.append({"kind": "look", "short": _few([task_display_name(j) for j in left], "did not run last time"),
                      "act": dict(NOTE_ACT_RUNNING), "line": bits[-1]})
    try:
        # cr1#1007: a source that usually brings items in and has read
        # nothing on its last runs, read live off the book.
        silent = md.maintenance_silent_sources(workspace_root)
    except Exception:  # noqa: BLE001
        silent = []
    if silent:
        bits.append(md.silent_sources_line(silent))
        quiet = md.silent_sources_note(silent)
        if quiet:
            notes.append(quiet)
    # cr1#1489 (owner ruling 2026-11-22): the chat capture's "not being read"
    # line and its typed-phrase advice are no client's to see; the field stays
    # empty for the released pages, which read `mt.chats.line`.
    return {"failing": failing, "unread": unread, "left_unread": left,
            "silent": silent, "chats": {}, "line": " ".join(b for b in bits if b), "notes": notes}


#: cr1#1489: the two phrases the hidden chat-capture rows offered (#1500).
HIDDEN_CHAT_ACTS = frozenset({"sweep my chats", "catch up my chat history"})


def scrub_chats_note(doc: Any) -> Optional[Dict[str, Any]]:
    """cr1#1489 (owner ruling 2026-11-22), PURE: a Maintenance document a page
    still holds from before the hide, with the chat capture's sentences taken
    out of `line`, its rows out of `notes` and its `chats` emptied; None when
    it carries none of them. A refresh that keeps the page's copy (an idle
    answer over a page that says done) sends this instead, so no page, and
    no run that reads the page, keeps the old advice."""
    if not isinstance(doc, dict):
        return None
    chats = doc.get("chats") if isinstance(doc.get("chats"), dict) else {}
    said = str(chats.get("line") or "").strip()
    notes = doc.get("notes") if isinstance(doc.get("notes"), list) else []

    def hidden(n: Any) -> bool:
        if not isinstance(n, dict):
            return False
        act = n.get("act") if isinstance(n.get("act"), dict) else {}
        if str(act.get("chat") or "").strip().lower() in HIDDEN_CHAT_ACTS:
            return True
        text = str(n.get("line") or "").strip()
        return bool(said and text and text in said)

    if not said and not any(hidden(n) for n in notes):
        return None
    out = {k: v for k, v in doc.items() if k not in ("doc_id", "id", "version")}
    line = str(doc.get("line") or "")
    if said:
        line = " ".join(line.replace(said, " ").split())
    out.update(chats={}, line=line, notes=[n for n in notes if not hidden(n)])
    return out


#: cr1#1378: how far back the Hub looks for an Apply that failed. A failure
#: older than this with no Apply since is not named any more.
APPLY_TROUBLE_HOURS = 24
#: cr1#1464: the Hub's sentence when its inbox run stopped without building
#: the page (`inbox_helpers.PAGE_NOT_BUILT_CODE`), and no inbox run has
#: landed a page since. The same look-back as an Apply that failed.
INBOX_STOP_LINE = "Hub Inbox could not build your inbox page on its last run."
#: The abort code that sentence answers (`inbox_helpers.PAGE_NOT_BUILT_CODE`,
#: kept as a literal so this module does not import the inbox module).
INBOX_PAGE_NOT_BUILT_CODE = "inbox_page_not_built"
#: cr1#1590: that sentence's few words on the Hub's note row, and its one
#: action, the page's own Check inbox.
INBOX_STOP_SHORT = "Inbox page not built"
INBOX_STOP_ACT = {"label": "Check inbox", "part": "inbox"}


def inbox_stop(workspace_root, *, now_iso=None) -> Optional[Dict[str, Any]]:
    """READ. cr1#1464: `{line, at, short, act}` when the Hub inbox run's newest stop
    record (`fire_stopped`, task `cockpit-inbox`) is from the last
    `APPLY_TROUBLE_HOURS` and no inbox receipt (`pack_run`) is newer, else
    None. The Hub's note and the Morning Brief both say `line`; the Hub's
    note row shows `short` with `act` (cr1#1590). Never
    raises: an unreadable book says nothing."""
    if not workspace_root:
        return None
    try:
        from events_io import load_events_org_scoped
        import receipts as _rc

        stop = _rc.last_fire_stop(workspace_root, "cockpit-inbox")
        # Only the page-not-built stop is this sentence; any other stop of
        # the inbox run (a bootloader gate) is said where it is said today.
        if str((stop or {}).get("abort_code") or "") != INBOX_PAGE_NOT_BUILT_CODE:
            return None
        at = _parse((stop or {}).get("at"))
        now = _now(now_iso)
        # A minute's grace either side of now: a stop stamped by another
        # computer's clock a moment ahead still counts.
        if at is None or not (now - _dt.timedelta(hours=APPLY_TROUBLE_HOURS) <= at
                              <= now + _dt.timedelta(minutes=1)):
            return None
        events, _skipped = load_events_org_scoped(workspace_root)
        for ev in events:
            if not isinstance(ev, dict) or ev.get("type") != "pack_run":
                continue
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if str(d.get("task_id") or "") != "cockpit-inbox":
                continue
            ran = _parse(ev.get("ts"))
            if ran is not None and ran > at:
                return None
    except Exception:  # noqa: BLE001 - the note keeps what it knows
        return None
    return {"line": INBOX_STOP_LINE, "at": _iso(at), "short": INBOX_STOP_SHORT, "act": dict(INBOX_STOP_ACT)}


#: cr1#1378: the surface words that are a decision, never a failure: a close
#: the fence held, a row already answered.
_APPLY_NOT_FAILED = ("refused", "already", "waiting")  # cr1#1377: a held press waits


def _apply_failed_rows(d: Dict[str, Any]) -> int:
    """cr1#1378: the rows of one audit row that failed: recorded as an error
    on a row the Hub writer answered, and not answered on the page as a
    refusal or as already done. A row with no `said` (a folded copy a snooze
    covered, or a row written before cr1#1378) is not counted: its error may
    be a refusal on purpose, and the Hub never names a failure on a guess."""
    return sum(1 for a in d.get("actions") or []
               if isinstance(a, dict) and a.get("outcome") == "error"
               and str(a.get("said") or "") and str(a.get("said")) not in _APPLY_NOT_FAILED)


def _apply_trouble(workspace_root, now: _dt.datetime) -> Optional[Dict[str, Any]]:
    """cr1#1378: the Hub's Apply runs that failed, off their own audit rows,
    so a run where rows failed is seen on the Hub and not only in the book.
    Reads the Hub's `apply_choices_applied` rows (a `cockpit` surface) from
    the last `APPLY_TROUBLE_HOURS`, newest first, and counts the runs in a
    row that had a failed row, stopping at the first clean one. Answers the
    `failing` entry `{job_id, name, fires, since, at, n_failed, n_choices}`
    for the newest such streak (`at` and the counts from the newest run,
    cr1#1385), or None. READ
    ONLY."""
    from events_io import load_events_org_scoped
    from schedule_config import task_display_name

    events, _skipped = load_events_org_scoped(workspace_root)
    start = now - _dt.timedelta(hours=APPLY_TROUBLE_HOURS)
    runs = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "apply_choices_applied":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        at = _parse(ev.get("ts"))
        if not str(d.get("surface") or "").startswith(SOURCE_SKILL) or at is None or not start <= at <= now:
            continue
        runs.append((at, d))
    runs.sort(key=lambda t: t[0], reverse=True)
    streak = []
    for at, d in runs:
        if not _apply_failed_rows(d):
            break
        streak.append((at, d))
    if not streak:
        return None
    newest = streak[0][1]
    # cr1#1722: the save is the Hub Update's now, so the note names that task.
    # The entry keeps its `job_id`: every released page finds it by that id.
    return {"job_id": "cockpit-apply", "name": task_display_name("cockpit-update"), "fires": len(streak),
            "since": _iso(streak[-1][0]), "at": _iso(streak[0][0]), "n_failed": _apply_failed_rows(newest),
            "n_choices": len([a for a in newest.get("actions") or [] if isinstance(a, dict)])}


def schedule_holder_status(workspace_root, env: Optional[Dict[str, str]] = None
                           ) -> Optional[Dict[str, Any]]:
    """cr1#1138: which computer and account hold this folder's schedules, for
    the Hub. `{line, short, computer, account, pending}`: `line` is the health
    check's own sentence (`task_watchdog.scheduled_writer_lines`, one
    producer), `short` its few words on the Hub's note row ("Schedules run
    on <computer>", cr1#1590; "" with no computer named, and the row says
    `line`), `computer` the holder's plain name or None, `account` `this` /
    `other` / None (relative to the reader; the account is never named),
    `pending` True while a hand-over has not yet taken effect. None when
    nothing is declared. READ ONLY, never raises."""
    try:
        import schedule_config as _sc
        import schedule_holder as _sh
        import task_watchdog as _tw

        decl = _sc.scheduled_writer(workspace_root)
        if not decl:
            return None
        lines = _tw.scheduled_writer_lines(workspace_root, env)
        held = _sh.holder_now(decl) or {}
        words = _sh.holder_account_words(decl, env)
    except Exception:  # noqa: BLE001 - the page keeps its old document
        return None
    if not lines:
        return None
    account = None
    for key, said in _sh.ACCOUNT_WORDS.items():
        if words == said:
            account = key
    computer = held.get("device")
    # cr1#1641 item 17 (cr1#1645): the few words name no machine; the sentence
    # naming the computer stays behind the information mark.
    return {"line": lines[0], "short": HOLDER_SHORT if computer else "", "computer": computer,
            "account": account, "pending": bool(held.get("pending"))}


#: cr1#1641 item 17: the Run details note's few words for where the schedules run.
HOLDER_SHORT = "Where your scheduled tasks run"


def maintenance_status(workspace_root=None, *, now_iso=None, review: Optional[Dict[str, Any]] = None,
                       projects: Optional[Dict[str, Any]] = None,
                       with_answer: bool = True) -> Dict[str, Any]:
    """The Hub's `status/maintenance` document (`_maintenance_doc`), with
    cr1#1585's one "is everything OK?" answer as `everything_ok`
    (`everything_ok.answer` over this very document, and `review` when the
    caller already built the To confirm feed, `projects` the projects feed
    with its tidy piles). `with_answer=False` is the document alone, which
    that answer reads when asked outside the Hub."""
    doc = _maintenance_doc(workspace_root, now_iso=now_iso)
    if with_answer and workspace_root:
        try:
            import everything_ok

            doc["everything_ok"] = everything_ok.answer(workspace_root, now_iso=_iso(_now(now_iso)),
                                                        maintenance=doc, review=review, projects=projects)
        except Exception:  # noqa: BLE001 - the Maintenance record never fails for the answer
            pass
    return doc


def _maintenance_first_run_pending(workspace_root) -> bool:
    """cr1#1585 review round 1: Maintenance has not had its first chance to
    run: registered on this seat (its own trigger's time, else the
    workspace's) after its second-most-recent slot, by the watchdog's own
    rule (`task_watchdog.registered_after_slot`). False when it cannot tell."""
    try:
        import maintenance_dispatcher as md
        import task_watchdog as tw

        cfg = tw.read_workspace_config(workspace_root)
        triggers = cfg.get("triggers") if isinstance(cfg.get("triggers"), dict) else {}
        ws_reg = tw._to_local_naive(tw.parse_ts(cfg.get("registered_at") or ""))
        reg = tw.task_registered_at(md.MAINTENANCE_TASK_ID, None, trigger_map=triggers,
                                    created_times=tw.schedule_created_times(workspace_root) if triggers else {},
                                    workspace_registered=ws_reg)
        cron = md.DEFAULT_SCHEDULES[md.MAINTENANCE_TASK_ID]["cron"]
        recent = tw.effective_fires(cron, now=tw._now_local(), count=2)
        return bool(reg is not None and tw.registered_after_slot(reg, recent, None))
    except Exception:  # noqa: BLE001 - no verdict is not a first run
        return False


def _maintenance_doc(workspace_root=None, *, now_iso=None) -> Dict[str, Any]:
    """The Hub's `status/maintenance` document (cr1#666), composed from the
    book, READ/COMPUTE ONLY: a `status_doc` for the `maintenance` leg plus
    `finished_at` (the newest heartbeat's finish time; the newest receipt's
    time on a book from before the heartbeat), `last_completed` (`{job_id:
    UTC ISO}`, each job's newest completion), `hours_behind`
    (`maintenance_dispatcher.maintenance_staleness`, from the newest receipt,
    as of `now_iso`), `behind_hours`, the hours past which it counts as
    behind, and `failing` and `unread` (cr1#822), `left_unread` (cr1#979) and
    `silent` (cr1#1007), which the line names too, and `chats` (cr1#1489),
    always `{}` since the owner's 2026-11-22 ruling. A Hub Apply that failed
    (cr1#1378) is a `failing` entry too, but never in the line: it is
    `apply_line`, its own sentence, and `apply_at`, its own run's time
    (cr1#1385), empty when none failed. cr1#1590: `notes` (Maintenance's
    own findings, one `{kind, short, act, line}` a sentence of `line`), and
    beside `apply_line` and `inbox_line` their few words and one action
    (`apply_short`, `apply_act`, `inbox_short`, `inbox_act`), for the Hub's
    note rows; every line stays as it was. It
    is data only: Start runs Maintenance on every press
    (cr1#682). A workspace with no Maintenance
    on record answers `idle` with nothing measured; an unreadable book
    answers the same, never raises."""
    now = _now(now_iso)
    doc = status_doc(workspace_root, "maintenance", "idle", MAINTENANCE_NONE_LINE, now_iso=now)
    doc.update({"finished_at": None, "last_completed": {}, "hours_behind": None,
                "behind_hours": START_MAINTENANCE_BEHIND_HOURS, "failing": [], "unread": [],
                "left_unread": [], "silent": [], "chats": {}, "apply_line": "", "apply_at": None,
                "inbox_line": "", "inbox_at": None, "notes": [], "apply_short": "", "apply_act": None,
                "inbox_short": "", "inbox_act": None})
    if not workspace_root or not (Path(str(workspace_root)) / "_hq" / "data").is_dir():
        return doc
    holder = schedule_holder_status(workspace_root)
    if holder:
        doc["holder"] = holder
    # cr1#1464: an inbox run that built no page is its own sentence and time,
    # like a failing Apply, whether or not Maintenance has run yet.
    stopped = inbox_stop(workspace_root, now_iso=now)
    if stopped:
        doc.update({"inbox_line": stopped["line"], "inbox_at": stopped["at"],
                    "inbox_short": stopped.get("short") or "", "inbox_act": stopped.get("act")})
    try:
        import maintenance_dispatcher as md

        newest, last, beat, newest_via = _maintenance_history(workspace_root)
        behind = md.maintenance_staleness(workspace_root, now=now)
        hours = behind.get("hours_behind")
    except Exception:  # noqa: BLE001 - the page keeps its old document
        return doc
    # cr1#1585 review round 1: the slot rule the typed surfaces use (its most
    # recent slot went unserved), beside the hours Start reads.
    doc["stale"] = bool(behind.get("stale"))
    if newest is None:
        # A brand-new seat: registered after its second-most-recent slot, so
        # Maintenance has not had its first chance yet (the watchdog's rule).
        doc["first_run_pending"] = _maintenance_first_run_pending(workspace_root)
        return doc
    finished = _parse((beat or {}).get("finished_at"))
    trouble = _maintenance_trouble(workspace_root)
    try:
        # cr1#1378: a Hub Apply whose rows failed is named with the jobs.
        apply_bad = _apply_trouble(workspace_root, now)
    except Exception:  # noqa: BLE001 - the note keeps what it knows
        apply_bad = None
    apply_line, apply_at, apply_short, apply_act = "", None, "", None
    if apply_bad:
        # cr1#1385: the Apply is its own sentence with its own run's time, never
        # in Maintenance's line, so the page's "After <run>" clause (which names
        # Maintenance's run) never reads as the cause of an Apply failure. The
        # entry stays in `failing`, so the page still shows the note.
        trouble = dict(trouble, failing=list(trouble["failing"]) + [apply_bad])
        apply_line = (f"{apply_bad['name']} could not save {apply_bad['n_failed']} of "
                      f"{apply_bad['n_choices']} changes on its last run.")
        apply_at = apply_bad.get("at")
        # cr1#1590: the note row's few words, and the top bar's own Apply
        # (it arms the refresh); the page offers it only while a choice waits,
        # and the health check otherwise.
        apply_short = f"Not saved: {apply_bad['n_failed']} of {apply_bad['n_choices']}"
        apply_act = {"label": "Save now", "part": "confirm"}
    # cr1#743: the document says which fire left it, and the page writes the
    # clause in the viewer's own time (one wording, on the page). A scheduled
    # fire's word reaches the page at the next Refresh or Start, as a typed run's.
    fired_via = str(newest_via or "").strip().lower() or None
    fired_at = _iso(newest)
    doc.update({
        "state": "done",
        # The line reads the same after a scheduled fire and a typed one
        # (cr1#822); which fire left it is its own field, and the page says it.
        "line": " ".join(x for x in (MAINTENANCE_DONE_LINE, trouble["line"]) if x),
        "failing": trouble["failing"], "unread": trouble["unread"],
        "left_unread": trouble["left_unread"], "silent": trouble["silent"],
        "chats": trouble.get("chats") or {},
        "notes": trouble.get("notes") or [],
        "fired_via": fired_via, "fired_at": fired_at,
        "apply_line": apply_line, "apply_at": apply_at,
        "apply_short": apply_short, "apply_act": apply_act,
        "finished_at": _iso(finished or newest),
        "last_completed": {job: _iso(when) for job, when in sorted(last.items())},
        "hours_behind": float(hours) if isinstance(hours, (int, float)) else None,
    })
    return doc


# ---------------------------------------------------------------------------
# feeds
# ---------------------------------------------------------------------------

#: The prep text the page shows in place, per meeting (cr1#655). A page on
#: claude.ai cannot open a file on the owner's computer, so the brief travels
#: in the feed; a long one is cut at a paragraph and says so.
PREP_TEXT_CAP = 16000
#: Where call-prep lands its briefs when a receipt names the file alone.
PREP_DIRS = ("_hq/meetings",)


def _prep_path(workspace_root, artifact: str) -> Optional[Path]:
    """The prep file a receipt names: the path as given, else the file name
    under the folders call-prep writes to. None when it is not on disk."""
    if not workspace_root or not artifact:
        return None
    root = Path(str(workspace_root))
    rel = str(artifact).strip().replace("\\", "/")
    cands = [root / rel] + [root / d / Path(rel).name for d in PREP_DIRS]
    for c in cands:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _docx_paragraphs(path: Path) -> List[str]:
    """Paragraph texts of a .docx, standard library only (the runtime beside
    the data has no python-docx): `word/document.xml`, one line per `w:p`,
    a heading style prefixed with `## `, a list paragraph with `- `."""
    import zipfile

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    from xml.etree import ElementTree as ET

    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    out = []
    for p in ET.fromstring(xml).iter(W + "p"):
        text = "".join((t.text or "") for t in p.iter(W + "t")).strip()
        if not text:
            continue
        style = p.find(W + "pPr/" + W + "pStyle")
        sval = (style.get(W + "val") if style is not None else "") or ""
        if sval.lower().startswith(("heading", "title")):
            text = "## " + text
        elif p.find(W + "pPr/" + W + "numPr") is not None or sval.lower().startswith("list"):
            text = "- " + text
        out.append(text)
    return out


def prep_text(workspace_root, artifact: str, cap: int = PREP_TEXT_CAP) -> Optional[str]:
    """The prep brief as plain lines for the page, or None when the file is
    not found or cannot be read. Markdown files are read as they are; Word
    files through `_docx_paragraphs`. Never raises."""
    path = _prep_path(workspace_root, artifact)
    if path is None:
        return None
    try:
        if path.suffix.lower() == ".docx":
            lines = _docx_paragraphs(path)
        else:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:  # noqa: BLE001 - an unreadable brief is shown as ready, not as text
        return None
    out, size = [], 0
    for line in lines:
        if size + len(line) + 1 > cap:
            out.append("(The brief continues in the Word file in your meetings folder.)")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out).strip() or None


#: cr1#1632 slice 1: the parts of a brief the live prep page reads, and how
#: many of each travel on the feed.
PREP_PART_KEYS = ("walk", "decide", "ask", "cover", "you_owe", "they_owe", "changed", "carried")
#: cr1#1788 review: marks laid over a part, never a part of their own. `ask_closed`
#: names the questions of `ask` that read as yes-or-no; each stays in `ask`.
#: `guide_ids` (cr1#1788 page, decision 146g) is `{"ask": [...], "cover": [...]}`,
#: one `hub_prep_edit.guide_id` per item, in the part's own order: the id a
#: call-guide remove names, so the page never hashes the words itself.
PREP_PART_FLAGS = ("ask_closed", "guide_ids")
#: The call guide's parts and the kind each item is to `hub_prep_edit`.
GUIDE_PART_KINDS = (("ask", "question"), ("cover", "topic"))


def prep_parts(workspace_root, artifact: str) -> Optional[Dict[str, Any]]:
    """cr1#1632 slice 1: a brief's parts as lists (`prep_pipeline.prep_parts`,
    saved beside the document by the brief writer), or None for a brief that
    has none (written before, or by a writer that keeps none): the page then
    shows the prose as before. Never raises; never writes."""
    try:
        from prep_pipeline import PARTS_LIST_CAP, PARTS_TEXT_CAP, parts_path

        path = _prep_path(workspace_root, parts_path(artifact))
        if path is None:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - no parts: the prose stands
        return None
    if not isinstance(raw, dict):
        return None
    txt = lambda x: " ".join(str(x or "").split())[:PARTS_TEXT_CAP]
    out: Dict[str, Any] = {"walk": txt(raw.get("walk"))}
    for k in PREP_PART_KEYS[1:]:
        items = raw.get(k) if isinstance(raw.get(k), list) else []
        if k == "decide":
            out[k] = [{"q": txt(d.get("q")), "opts": [txt(o)[:60] for o in (d.get("opts") or [])[:5] if txt(o)]}
                      for d in items[:PARTS_LIST_CAP] if isinstance(d, dict) and txt(d.get("q"))]
        else:
            out[k] = [txt(x) for x in items[:PARTS_LIST_CAP] if txt(x)]
    # a flag names only what its part carries
    flagged = raw.get("ask_closed") if isinstance(raw.get("ask_closed"), list) else []
    closed = [q for q in (txt(x) for x in flagged[:PARTS_LIST_CAP]) if q and q in out.get("ask", [])]
    if closed:
        out["ask_closed"] = closed
    if not any(out.get(k) for k in PREP_PART_KEYS):
        return None
    try:
        import hub_prep_edit

        ids = {part: [hub_prep_edit.guide_id(kind, x) for x in out.get(part) or []]
               for part, kind in GUIDE_PART_KINDS if out.get(part)}
    except Exception:  # noqa: BLE001 - no ids: the page offers no remove
        ids = {}
    if ids:
        out["guide_ids"] = ids
    return out


_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _workspace_tz(workspace_root):
    try:
        from tz import load_workspace_tz

        return load_workspace_tz(str(workspace_root)) or _dt.timezone.utc
    except Exception:  # noqa: BLE001
        return _dt.timezone.utc


def _zone_named(workspace_root) -> str:
    """Re-review of #1776 (must 1 held open): the zone the workspace record
    NAMES (`workspace.user_timezone`, both blocks as `tz.py` merges them),
    as written, even when this machine cannot load it (no tz database):
    Hub Morning's helpers then know the day is unreadable, never UTC's.
    "UTC" only when the record names no zone at all."""
    try:
        import tz as _tz

        root = _tz._resolve_workspace_path(str(workspace_root))
        data = json.loads((root / _tz._ENTITIES_REL).read_text(encoding="utf-8")) if root else {}
    except Exception:  # noqa: BLE001 - no record: the workspace names no zone
        return "UTC"
    inner = data.get("entities") if isinstance(data.get("entities"), dict) else {}
    ws = {**(inner.get("workspace") if isinstance(inner.get("workspace"), dict) else {}),
          **(data.get("workspace") if isinstance(data.get("workspace"), dict) else {})}
    name = ws.get("user_timezone")
    return name.strip() if isinstance(name, str) and name.strip() else "UTC"


def _zone_name(workspace_root) -> str:
    """The workspace's zone as an IANA name the page's Intl can read
    ("America/Los_Angeles"); "UTC" when none is set or it has no name."""
    tz = _workspace_tz(workspace_root)
    return str(getattr(tz, "key", "") or "UTC")


def _local_midnight(value, tzinfo) -> Optional[_dt.datetime]:
    """A date-only calendar value (an all-day event) as midnight where the
    owner is, never UTC midnight (cr1#679)."""
    try:
        d = _dt.date.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return _dt.datetime.combine(d, _dt.time(0), tzinfo)


#: The Week view on Today (cr1#1017, option D): seven day columns, today and
#: the six after it. Each day carries its meetings from the calendar read and
#: its due items from the open list; under the board, one lane per confirmed
#: priority across the same days. No money anywhere.
WEEK_DAYS = 7
#: Per day: how many meetings and how many due cards travel; the rest are
#: counted, never dropped silently.
WEEK_DAY_MEETINGS = 20
WEEK_DAY_CARDS = 12
#: How many priority lanes travel under the board.
WEEK_LANES = 8
#: A timed event that says time away marks its days only when it runs this
#: long; a short one ("away from desk, 2pm") is an ordinary meeting.
WEEK_AWAY_TIMED_HOURS = 20
#: An all-day event whose title says time away marks its days as away:
#: "Away, <title>". A business meeting booked on such a day is a clash.
WEEK_AWAY_RE = re.compile(
    r"(?i)\b(out of (the )?office|ooo|vacation|holiday|holidays|time off|day off|"
    r"days off|pto|annual leave|leave|off sick|sick day|away|travel(l)?ing|on leave)\b")
#: cr1#1641: an all-day event on the owner's personal calendar that runs
#: this many days or more (a stay, a trip) is his time away, whatever its
#: title, when it is his own and names no one else. A one-day personal
#: event ("an anniversary") is not.
WEEK_AWAY_PERSONAL_DAYS = 2
#: cr1#1641 (review round 1): the time-away words a colleague's own entry
#: uses ("<Name> OOO", "<Name> - PTO", "<Name> out of office"). Only before
#: one of these is a capitalised word read as a person's name; before
#: "vacation", "holiday" or "trip" it is a place ("Hawaii vacation").
WEEK_AWAY_COLLEAGUE_RE = re.compile(
    r"(?i)\b(out of (the )?office|ooo|pto|away|leave|annual leave|on leave|off sick|sick day)\b")
#: cr1#1641: words that may stand before the time-away word without naming
#: anyone ("Family vacation", "Christmas holiday", "Friday off"). Any other
#: capitalised word there reads as a person's name ("<Name> OOO"), so the
#: event is someone else's time away, not the owner's.
WEEK_AWAY_LEAD_WORDS = frozenset((
    "my", "our", "the", "a", "all", "full", "half", "whole", "personal", "family", "company", "team",
    "office", "public", "bank", "national", "federal", "state", "religious", "school", "summer",
    "winter", "spring", "fall", "autumn", "ski", "beach", "christmas", "xmas", "easter", "thanksgiving",
    "new", "year", "years", "labor", "labour", "memorial", "independence", "juneteenth", "presidents",
    "veterans", "boxing", "good", "friday", "monday", "tuesday", "wednesday", "thursday", "saturday",
    "sunday", "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "medical", "sick", "parental", "paternity", "maternity",
    "honeymoon", "wedding", "birthday", "annual", "mental", "health", "wellness", "recharge", "flex",
    "floating", "comp", "jury", "duty", "bereavement", "study", "sabbatical", "unpaid", "paid",
    "travel", "trip", "work", "remote", "conference"))


# ---------------------------------------------------------------------------
# what a meeting row carries for the page's calendar actions (cr1#1539)
# ---------------------------------------------------------------------------

#: The owner's answers to an invitation, as a row may carry them.
MEETING_ANSWERS = ("accepted", "declined", "tentative", "none")
#: The most people named on one meeting row, and the most a note is drafted to.
MEETING_PEOPLE_CAP = 12
#: The most attendees read from one event; a longer list names nobody (a cut
#: list would let through whoever was cut).
MEETING_PEOPLE_READ = 200
#: The day's ended meetings kept for "Past meetings".
PAST_MEETINGS_CAP = 40
# the most hidden wrap-up meetings the feed names for the press's claim (round 7)
PAST_HIDDEN_CAP = 200
# cr1#1668: Past meetings lists the meetings of the last seven days, which the
# prep run's calendar read starts that many days before today to cover
PAST_DAYS = 7
#: Working hours when the seat sets none (`workspace.working_hours`), and
#: the shortest gap worth offering.
WORK_HOURS = (7, 17)
FREE_MIN_MINUTES = 30
FREE_PER_DAY = 12
_TZ_NAME_RE = re.compile(r"^(UTC|[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+){1,2})$")


def _low(value: Any) -> str:
    return " ".join(str(value or "").split()).lower()


#: cr1#1830: said on Today when the off-limits list is there but cannot be read.
OFF_LIMITS_UNREAD_TODAY_LINE = "Your off-limits list could not be read, so nobody is named on your meetings."


def _meeting_wall(workspace_root) -> Optional[Dict[str, Any]]:
    """Who may be named on a meeting row (cr1#1539): the off-limits addresses
    and names from the SAME function the Hub's live wall is built from
    (`hub_live._off_limits`: the gate's own senders, and every address and
    full name of each person one of them belongs to), and the owner's own
    addresses. None when any of it cannot be read, or a list is too long to
    check in full: the rows then name nobody (fail closed)."""
    try:
        import connector_reads
        import hub_live
        import inbox_helpers

        # A book that cannot be read answers "nobody is off limits" from the
        # gate, which is no answer: the wall's own reader says so first.
        if not connector_reads.chat_wall(workspace_root).get("readable"):
            return None
        # cr1#1830: so does an off-limits list that is there but cannot be read.
        from account_scope_gate import off_limits_readable

        if not off_limits_readable(workspace_root):
            return None
        addrs, names = hub_live._off_limits(workspace_root)
        if max(len(addrs), len(names)) > hub_live.LIST_CAP:
            return None
        own = {_low(a) for a in inbox_helpers.own_addresses(str(workspace_root)) if a}
        return {"addrs": {_low(a) for a in addrs}, "names": {_low(n) for n in names}, "own": own}
    except Exception:  # noqa: BLE001 - fail closed: nobody is named
        return None


def _off_limits_readable(workspace_root) -> bool:
    try:
        from account_scope_gate import off_limits_readable

        return off_limits_readable(workspace_root)
    except Exception:  # noqa: BLE001 - unknown is unreadable
        return False


def _meeting_extras(m: Dict[str, Any], wall: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The optional fields a meeting row gains for the page's calendar
    actions (cr1#1539), each present only when the run's calendar read said
    it plainly: `event_id` (the event's id on the calendar, as the connector
    returned it), `link` (a join link, https only), `mine` (the owner
    organises it), `recurring`, `answer` (the owner's own answer), `tz` (the
    event's own zone), `people` (the other people's names) and `note_to`
    (their addresses, only on a meeting the owner organises: the one place a
    note to them can be drafted), `title_whole` (the row's title is the
    calendar's own, uncut and untrimmed) and `held` (True when anybody on it
    was left out below or could not be checked: the page then moves and
    cancels nothing on it).

    The personal wall and off-limits hold here. An event on a personal
    calendar gains nothing: no id, no link, no people, so the page offers
    nothing on it. A person who is off limits (by any address on file or by
    full name) is never named and never written to, and neither is anyone
    whose address the read did not give while somebody is off limits. When
    the wall cannot be read, nobody is named at all."""
    out: Dict[str, Any] = {}
    if str(m.get("calendar") or "").lower() == "personal":
        return out
    eid = str(m.get("event_id") or "").strip()
    if eid and len(eid) <= 512:
        out["event_id"] = eid
    link = str(m.get("link") or "").strip()
    if link.startswith("https://") and len(link) <= 2000 and not re.search(r"[\s<>\"']", link):
        out["link"] = link
    if isinstance(m.get("organiser"), bool):
        out["mine"] = m["organiser"]
    if isinstance(m.get("recurring"), bool):
        out["recurring"] = m["recurring"]
    answer = str(m.get("answer") or "").strip().lower()
    if answer in MEETING_ANSWERS:
        out["answer"] = answer
    tz = str(m.get("tz") or "").strip()
    if tz and len(tz) <= 64 and _TZ_NAME_RE.match(tz):
        out["tz"] = tz
    # The title as the row carries it is the calendar's own, character for
    # character, only when nothing here trimmed or cut it. A connector whose
    # change call takes the title is sent it only then.
    raw_title = m.get("title")
    out["title_whole"] = bool(isinstance(raw_title, str) and raw_title and raw_title == raw_title.strip()
                              and len(raw_title) <= 200)
    # `held`: somebody on this meeting was left out here (off limits, or not
    # checkable), or the wall or the list could not be read. The page then
    # changes nothing on it: a move or a cancel from the Hub would act on a
    # person the Hub may not show. False only when everyone was checked.
    out["held"] = True
    att = m.get("attendees")
    if wall is not None and not isinstance(att, list) and _no_one_else(m):
        out["held"] = False   # the read says nobody else is on it
        return out
    if not isinstance(att, list) or wall is None or len(att) > MEETING_PEOPLE_READ:
        return out
    people: List[str] = []
    to: List[str] = []
    left_out = False
    anyone_off = bool(wall["addrs"] or wall["names"])
    # Which entry is the owner has to be known, by the read's own mark or by
    # his addresses on file: otherwise he would be named as one of the others
    # and written to, so nobody is.
    if not wall["own"] and not any(isinstance(a, dict) and a.get("self") is True for a in att):
        return out
    for a in att:
        if not isinstance(a, dict):
            return out   # a list this cannot read names nobody
        email = _low(a.get("email"))
        name = " ".join(str(a.get("name") or "").split())[:80]
        if email and not re.match(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]+$", email):
            email = ""
        if a.get("self") is True or (email and email in wall["own"]):
            continue
        if (email and email in wall["addrs"]) or (name and _low(name) in wall["names"]):
            left_out = True
            continue
        if not email and anyone_off:
            left_out = True
            continue
        if name and name not in people:
            people.append(name)
        if email and email not in to:
            to.append(email)
    out["people"] = people[:MEETING_PEOPLE_CAP]
    out["held"] = left_out
    if out.get("mine") is True and to and len(to) <= MEETING_PEOPLE_CAP and not left_out:
        out["note_to"] = to
    return out


#: The invite's own text a meeting row carries for the Prep page (cr1#1788).
INVITE_TEXT_CAP = 2000
#: cr1#1788 review: only this many of Today's next meetings carry it, once
#: each, on `feed/today`'s `meetings` rows (never the Week's or the past's).
INVITE_ROWS_CAP = 6
#: cr1#1788 review: feed/today's own size budget, in characters of its JSON,
#: 85% of `REFRESH_DOC_LIMIT`. Past it the invite texts go first, from the
#: latest meeting back, counted in `invite_cut`.
TODAY_DOC_BUDGET = 182_500
#: fix check, required item a: what feed/today says when it shows less.
TODAY_INVITE_CUT_LINE = "Today is long, so the invite's own text is left off {n} of your meetings."
TODAY_OVER_BUDGET_LINE = "Today is too long to show in full; ask in a chat for the rest."
#: fix check, MUST 2: a meeting that moved and whose prep edits could not be matched to it.
PREPX_UNPLACED_LINE = ("Some of your prep edits were made for another time of their meeting and could not be "
                       "matched to it after a move; they are kept at that time, until two weeks after it.")
#: second check, SHOULD 3: a moved meeting's edits not shown because their words name someone hidden.
PREPX_MOVED_HELD_LINE = "Prep edits for a meeting that moved are not shown: their words name someone who cannot be named here."
#: second check, SHOULD 3: edits for a meeting this week that is no longer on the calendar under its id.
PREPX_GONE_LINE = ("Prep edits for {n} meetings this week are kept, but those meetings are no longer on your calendar "
                   "at that time (moved, cancelled or made again); they are kept until two weeks after their time.")


def _invite_screen(wall: Optional[Dict[str, Any]], hw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The words the invite's text is screened against: the edits' own
    (`followup_draft.hidden_words`: every way a hidden person is written,
    their addresses) and the meeting wall's off-limits addresses and names
    besides. None when either cannot be read. Pure."""
    if wall is None or hw is None:
        return None
    import followup_draft as fd

    return {"emails": set(hw.get("emails") or ()) | {fd._plain(a).strip().casefold() for a in wall.get("addrs") or () if a},
            "names": set(hw.get("names") or ()) | {f for f in (fd._flat(n) for n in wall.get("names") or ()) if f},
            "shown": set(hw.get("shown") or ())}


def _invite_text(value: Any, screen: Optional[Dict[str, Any]]) -> str:
    """The invite's description as the Prep page may show it: tags taken out,
    entities read, whitespace folded, capped; "" when there is none, when the
    screen (`_invite_screen`) cannot be read, or when the text names anyone
    hidden, by the same check the Prep page's edits pass
    (`followup_draft.names_hidden`: accents, case and first names alike).
    Pure."""
    import html as _html

    import followup_draft as fd

    if screen is None or not isinstance(value, str) or not value.strip():
        return ""
    text = re.sub(r"<[^>]*(?:>|$)", " ", value)
    text = re.sub(r"<[^>]*(?:>|$)", " ", _html.unescape(text)).replace("<", " ").replace(">", " ")
    text = " ".join(text.split())[:INVITE_TEXT_CAP]
    # second check: a name split by tags ("<span>Dus</span><span>tin</span>")
    # is screened too, the tags taken out with nothing in their place
    joined = re.sub(r"<[^>]*(?:>|$)", "", value)
    joined = " ".join(re.sub(r"<[^>]*(?:>|$)", "", _html.unescape(joined)).split())[:INVITE_TEXT_CAP]
    if not text or fd.names_hidden([text, joined], screen):
        return ""
    return text


def _today_invites(workspace_root, rows: List[Dict[str, Any]], descs: Dict[int, Any],
                   wall: Optional[Dict[str, Any]], now: Optional[_dt.datetime] = None) -> None:
    """cr1#1788: the invite's own text on the next `INVITE_ROWS_CAP` of
    Today's meetings that are business meetings with someone on them, once
    each. Nothing when the wall or who is hidden cannot be read."""
    ended = lambda r: now is not None and (_parse(r.get("end")) or now) <= now
    want = [r for r in rows if r.get("calendar") != "personal" and not r.get("all_day") and not r.get("solo")
            and not r.get("away") and not r.get("title_off") and not ended(r) and isinstance(descs.get(id(r)), str)
            and descs[id(r)].strip()][:INVITE_ROWS_CAP]
    if not want:
        return
    try:
        import followup_draft as fd

        screen = _invite_screen(wall, fd.hidden_words(Path(str(workspace_root))))
    except Exception:  # noqa: BLE001 - fail closed: no invite text
        return
    for r in want:
        it = _invite_text(descs[id(r)], screen)
        if it:
            r["invite_text"] = it


def _today_prepx(workspace_root, rows: List[Dict[str, Any]], past: List[Dict[str, Any]],
                 week: Dict[str, Any], now: Optional[_dt.datetime] = None) -> List[str]:
    """cr1#1788 fix check, MUST 2: a Today row whose meeting moved carries
    the edits made for its old time (`prepx_moved`: `{from, edit}`, the page
    lays them on and its next press there names `moved_from`); a row whose
    key has edits the read cannot place carries `prepx_unplaced`, and one
    whose moved edit names someone hidden `prepx_held`. The read is every row
    of this feed (Today, past and the Week); only edits from now to the end
    of the read can move (a past occurrence never does). Answers the
    document's lines. Never raises."""
    try:
        import hub_prep_edit

        read: Dict[str, List[str]] = {}
        every = list(rows) + list(past) + [x for d in (week or {}).get("days") or [] for x in d.get("meetings") or []]
        for r in every:
            if isinstance(r, dict) and r.get("jk") and r.get("start"):
                starts = read.setdefault(str(r["jk"]), [])
                if r["start"] not in starts:
                    starts.append(r["start"])
        lo = now or _now(None)
        # the read ends at the workspace's midnight after its last day (the
        # Week's own dates), and the day it is said from begins at midnight
        tzinfo = _workspace_tz(workspace_root)
        today = lo.astimezone(tzinfo).date()
        midnight = lambda d: _dt.datetime(d.year, d.month, d.day, tzinfo=tzinfo)
        hi = midnight(_week_dates(today)[-1] + _dt.timedelta(days=1))
        got_all = hub_prep_edit.today_places(workspace_root, rows, read, (lo, hi), say_from=midnight(today))
        placed = got_all.get("rows") or {}
    except Exception:  # noqa: BLE001 - feed/prepx still carries every edit by its own time
        return []
    unplaced = held = False
    for i, got in placed.items():
        if "edit" in got:
            rows[i]["prepx_moved"] = got
        elif got.get("held"):
            rows[i]["prepx_held"] = True
            held = True
        elif got.get("unplaced"):
            rows[i]["prepx_unplaced"] = int(got["unplaced"])
            unplaced = True
    lines = []
    if unplaced:
        lines.append(PREPX_UNPLACED_LINE)
    if held:
        lines.append(PREPX_MOVED_HELD_LINE)
    if got_all.get("gone"):
        lines.append(PREPX_GONE_LINE.format(n=int(got_all["gone"])))
    return lines


def _fit_today(doc: Dict[str, Any]) -> Dict[str, Any]:
    """cr1#1788 review: keep feed/today under `TODAY_DOC_BUDGET`, the invite
    texts going first from the latest meeting back (`invite_cut` says how
    many); `over_budget` when it is over all the same."""
    size = lambda: len(json.dumps(doc, separators=(",", ":"), default=str))
    if size() <= TODAY_DOC_BUDGET:
        return doc
    cut = 0
    for r in reversed(doc.get("meetings") or []):
        if isinstance(r, dict) and r.pop("invite_text", None) is not None:
            cut += 1
            if size() <= TODAY_DOC_BUDGET:
                break
    lines = list(doc.get("size_lines") or [])
    if cut:
        doc["invite_cut"] = cut
        lines.append(TODAY_INVITE_CUT_LINE.format(n=cut))
    if size() > TODAY_DOC_BUDGET:
        doc["over_budget"] = True
        lines.append(TODAY_OVER_BUDGET_LINE)
    if lines:
        doc["size_lines"] = lines
    return doc


def _title_names_off(title: Any, wall: Optional[Dict[str, Any]]) -> bool:
    """cr1#1789: True when a meeting's title names someone off limits, by a
    name on the wall as a whole word or phrase (case aside), or the wall
    cannot be read."""
    if wall is None:
        return True
    def fold(t: Any) -> str:
        # cr1#1845: accents folded off their letters and characters that print nothing taken out,
        # as hub_live._norm and the page's normOf read a name
        t = unicodedata.normalize("NFKD", str(t or ""))
        return "".join(c for c in t if unicodedata.category(c)[0] != "M" and unicodedata.category(c) != "Cf").lower()

    def words(t: Any) -> str:
        # for a name: every mark, a full stop and "@" included, separates words
        return " ".join(re.sub(r"[^\w]+", " ", fold(t)).split())

    named = " " + words(title) + " "
    addressed = " ".join(re.sub(r"[^\w@.]+", " ", fold(title)).split())
    return any((" " + words(n) + " ") in named for n in (wall.get("names") or ()) if words(n)) \
        or any(a and fold(a) in addressed for a in (wall.get("addrs") or ()))


def _work_hours(workspace_root) -> tuple:
    """The seat's working hours as whole hours `(start, end)`: the
    workspace's `working_hours` (`{"start": 8, "end": 18}`) when it sets
    them, else 7 AM to 5 PM."""
    try:
        import connector_config as cc

        ent = cc.load_entities(workspace_root)
        inner = ent.get("entities") if isinstance(ent.get("entities"), dict) else ent
        wh = (inner.get("workspace") or {}).get("working_hours")
        a, b = wh.get("start"), wh.get("end")
        if (isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool)
                and not isinstance(b, bool) and 0 <= a < b <= 24):
            return (a, b)
    except Exception:  # noqa: BLE001 - nothing set is the usual state
        pass
    return WORK_HOURS


def _free_slots(busy: List[tuple], lo: _dt.datetime, hi: _dt.datetime) -> List[Dict[str, str]]:
    """The gaps of `FREE_MIN_MINUTES` or more between `lo` and `hi` around
    the busy intervals, in order."""
    out: List[Dict[str, str]] = []
    at = lo
    for b0, b1 in sorted(busy):
        if b1 <= at:
            continue
        if b0 >= hi:
            break
        if b0 - at >= _dt.timedelta(minutes=FREE_MIN_MINUTES):
            out.append({"start": _iso(at), "end": _iso(min(b0, hi))})
        at = max(at, b1)
        if at >= hi:
            break
    if hi - at >= _dt.timedelta(minutes=FREE_MIN_MINUTES):
        out.append({"start": _iso(at), "end": _iso(hi)})
    return out[:FREE_PER_DAY]


def _week_dates(today: _dt.date) -> List[_dt.date]:
    return [today + _dt.timedelta(days=i) for i in range(WEEK_DAYS)]


def week_end(today: _dt.date) -> _dt.date:
    """THE last day of "this week" on the Hub (cr1#1657): one definition for
    the Week board and the Work tab's late pile (`hub_work.week_day`). A week
    runs Monday to Sunday, as the plate reads one (`plate_view.horizon_of`).
    On a Saturday or a Sunday that week has no working day left, so "this
    week" is the one ahead, as far as the board's seven days reach: their
    last day (Friday from a Saturday, Saturday from a Sunday). So the day is
    always one of the board's days (`_week_dates`) and never today: a move
    to the end of the week never lands on the day it was made."""
    if today.weekday() >= 5:
        return _week_dates(today)[-1]
    return today + _dt.timedelta(days=6 - today.weekday())


def _day_name(d: _dt.date, today: _dt.date) -> str:
    return "Today" if d == today else d.strftime("%A")


def _away_calendar_says(m: Dict[str, Any]) -> bool:
    """cr1#1641: the calendar itself marks the event out of office (the
    read's `out_of_office: true`, the connector's own event type). Only a
    plain True does; a read that says nothing leaves the title to decide."""
    return m.get("out_of_office") is True


def _away_someone_elses_event(m: Dict[str, Any]) -> bool:
    """cr1#1641: the event is someone else's, by what the read says of it:
    another person organises it, or other people are on it and the owner
    does not organise it (a colleague's out-of-office sent to the team).
    Nothing said leaves the title to decide."""
    if m.get("organiser") is False:
        return True
    if m.get("organiser") is True:
        return False
    n = m.get("others")
    if isinstance(n, int) and not isinstance(n, bool) and n > 0:
        return True
    return any(isinstance(a, dict) and a.get("self") is not True and str(a.get("name") or a.get("email") or "").strip()
               for a in (m.get("attendees") if isinstance(m.get("attendees"), list) else []))


def _away_names(workspace_root) -> Dict[str, List[str]]:
    """cr1#1641: `{own, others}`: the names the owner goes by, and every
    other person's on file, each as given. Read once per feed; {} on a book
    that cannot be read (the event's own attendees and the title's shape
    still decide)."""
    try:
        from people_names import people_names

        people = people_names(workspace_root)
        owner = _owner_id(workspace_root)
    except Exception:  # noqa: BLE001 - no names leaves the other checks
        return {"own": [], "others": []}
    own = [n for n in people.get(owner) or []] if owner else []
    others = [n for pid, ns in people.items() if pid != owner for n in ns or []]
    return {"own": own, "others": others}


def _name_forms(name: str) -> List[str]:
    """A name and its first word, when that word is a name of its own."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not name:
        return []
    first = name.split(" ")[0]
    return [name] + ([first] if first != name and len(first) >= 2 else [])


def _away_leading_name(title: str, own: set) -> bool:
    """cr1#1641: one to three capitalised words that name no one standing
    before the first time-away word ("<Name> OOO", "<First> <Last> - PTO")
    read as the person whose time away it is. A word that names no one
    ("Family vacation", "Friday off") or one of the owner's own names keeps
    it his; any word that is not a plain capitalised name ("2 day OOO",
    "back-to-back") leaves it his too: the rule only takes what is plainly
    a name."""
    hit = WEEK_AWAY_RE.search(title)
    if not hit or not WEEK_AWAY_COLLEAGUE_RE.fullmatch(hit.group(0)):
        return False
    lead = re.sub(r"[\-–—:|/(),.]+", " ", title[:hit.start()]).split()
    if not lead or len(lead) > 3:
        return False
    for w in lead:
        if not re.fullmatch(r"[A-Z][a-z]+(?:'s)?", w):
            return False
        bare = re.sub(r"'s$", "", w).casefold()
        if bare in own or bare in WEEK_AWAY_LEAD_WORDS:
            return False
    return True


def _away_title(title: str) -> str:
    """cr1#1641 (review round 1): the title as the time-away checks read it:
    a curly apostrophe is a plain one."""
    return str(title or "").replace("\u2019", "'").replace("\u2018", "'")


def _away_loose_words(title: str) -> set:
    """cr1#1641 (review round 1): the title's words written all in capitals
    or all in small letters ("DUSTIN OOO", "OOO: dustin"), folded, a
    possessive set aside: a first name on file matches these in any case."""
    out = set()
    for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", title):
        w = re.sub(r"(?i)'s$", "", w)
        if len(w) >= 2 and (w.isupper() or w.islower()):
            out.add(w.casefold())
    return out


def _away_names_someone(title: str, m: Dict[str, Any], names: Dict[str, List[str]],
                        leading: bool = True) -> bool:
    """cr1#1641: the title names someone other than the owner: a person on
    file or on the event (a whole name in any case, a first name as
    written, or in any case where the title writes the word all in capitals
    or all in small letters), a possessive ("<Name>'s vacation"), or, when
    `leading`, a name before a colleague's time-away word. The owner's own
    names never count, nor a name that is also a word that names no one
    ("Summer", "June": WEEK_AWAY_LEAD_WORDS)."""
    own = {f.casefold() for n in names.get("own") or [] for f in _name_forms(n)}
    for a in (m.get("attendees") if isinstance(m.get("attendees"), list) else []):
        if isinstance(a, dict) and a.get("self") is True:
            own |= {f.casefold() for f in _name_forms(str(a.get("name") or ""))}
    theirs = list(names.get("others") or [])
    theirs += [str(a.get("name") or "") for a in (m.get("attendees") if isinstance(m.get("attendees"), list) else [])
               if isinstance(a, dict) and a.get("self") is not True]
    loose = _away_loose_words(title)
    for n in theirs:
        forms = _name_forms(n)
        if not forms:
            continue
        whole = forms[0]
        if whole.casefold() not in own and " " in whole and re.search(
                r"(?i)(?<!\w)" + re.escape(whole) + r"(?!\w)", title):
            return True
        for f in forms:
            if f.casefold() in own or len(f) < 2 or f.casefold() in WEEK_AWAY_LEAD_WORDS:
                continue
            if re.search(r"(?<!\w)" + re.escape(f) + r"(?!\w)", title) or (" " not in f and f.casefold() in loose):
                return True
    for w in re.findall(r"(?<!\w)([A-Z][A-Za-z\-]*)'s(?!\w)", title):
        if w.casefold() not in own and w.casefold() not in WEEK_AWAY_LEAD_WORDS:
            return True
    return leading and _away_leading_name(title, own)


def _away_stay_is_his(m: Dict[str, Any]) -> bool:
    """cr1#1641 (review round 1): a personal stay counts only when the read
    says the owner organises it. A family calendar's days ("<someone>
    visiting", "School closed") are not his time away: when the Hub cannot
    tell, no clash."""
    return m.get("organiser") is True


def _away_leading_applies(m: Dict[str, Any], stay: bool) -> bool:
    """cr1#1641 (review round 1): the name-before-the-word guess is for an
    event that may be a colleague's; never for one the owner organises, one
    the read says has nobody else on it (`others` 0: "Offsite OOO" he
    blocked), nor for his own stay."""
    return not stay and m.get("organiser") is not True and not _no_one_else(m)


def _own_time_away(m: Dict[str, Any], title: str, names: Dict[str, List[str]], *,
                   personal_stay: bool = False) -> bool:
    """cr1#1641: the event is the owner's own time away, so it marks its
    days away and a work meeting on them clashes. Never someone else's
    event; then the calendar's own out-of-office mark, else a time-away
    word, or a personal all-day event of WEEK_AWAY_PERSONAL_DAYS or more
    that he organises (`personal_stay`), in a title that names no one
    else. When the Hub cannot tell, no clash."""
    if _away_someone_elses_event(m):
        return False
    if _away_calendar_says(m):
        return True
    title = _away_title(title)
    stay = personal_stay and _away_stay_is_his(m)
    if not (stay or WEEK_AWAY_RE.search(title)):
        return False
    return not _away_names_someone(title, m, names, leading=_away_leading_applies(m, stay))


def _away_kind(m: Dict[str, Any], title: str, names: Any, *, long: bool) -> Optional[str]:
    """cr1#1707: whose time away an event is, so Today and Am I ready leave
    it out: "own" (the owner's own, by #1641's rule), "other" (someone
    else's), else None (a meeting). The calendar's own out-of-office mark
    makes any event time away. A time-away word does on an all-day event or
    one of WEEK_AWAY_TIMED_HOURS or more (`long`); on a shorter one, only a
    colleague's own entry ("<Name> OOO" at six in the evening, a name on
    file or on the event, straight before the word) is, and only when the
    owner does not organise it; never a meeting that only says the word
    ("Sample PTO policy review"), nor one he organises with that colleague
    on it ("<Name> - PTO handover"), nor one that names them elsewhere
    ("<Name> / <Name>: team away day planning"). `names` is the names map,
    or a callable that reads it (read only when needed)."""
    title = str(title or "")
    cal = _away_calendar_says(m)
    if not (cal or WEEK_AWAY_RE.search(_away_title(title))):
        return None
    got = names() if callable(names) else names
    if cal or long:
        return "own" if _own_time_away(m, title, got) else "other"
    plain = _away_title(title)
    if not WEEK_AWAY_COLLEAGUE_RE.search(plain):
        return None
    # review round 1 of #1740: a meeting the owner organises is his meeting,
    # whoever it names
    if m.get("organiser") is True:
        return None
    # no guess from a capitalised word before the time-away word here: a
    # short event is a meeting unless a person on file or on it is named
    # straight before that word
    return "other" if _away_name_before(plain, m, got) else None


def _away_name_before(title: str, m: Dict[str, Any], names: Dict[str, List[str]]) -> bool:
    """cr1#1707 (review round 1 of #1740): a person other than the owner, on
    file or on the event, is named straight before the title's first
    colleague time-away word ("Dustin OOO", "Dustin (OOO) - back Monday",
    "Dustin Sample - PTO"): only separators stand between them. The owner's
    own names never count. Review round 2 of #1740: and the word ends the
    title or a separator follows it ("Dustin OOO (half day)"); a plain word
    after it makes it a meeting about leave ("Dustin - PTO handover",
    "Rio - away from desk", "Dustin away day prep")."""
    hit = WEEK_AWAY_COLLEAGUE_RE.search(title)
    if not hit:
        return False
    if not re.match(r"\s*(?:$|[(\-\u2013\u2014:),\[|/])", title[hit.end():]):
        return False
    lead = re.sub(r"[\s\-\u2013\u2014:|/(\[]+$", "", title[:hit.start()])
    if not lead:
        return False
    attendees = m.get("attendees") if isinstance(m.get("attendees"), list) else []
    own = {f.casefold() for n in names.get("own") or [] for f in _name_forms(n)}
    for a in attendees:
        if isinstance(a, dict) and a.get("self") is True:
            own |= {f.casefold() for f in _name_forms(str(a.get("name") or ""))}
    theirs = list(names.get("others") or []) + [str(a.get("name") or "") for a in attendees
                                                 if isinstance(a, dict) and a.get("self") is not True]
    for n in theirs:
        for f in _name_forms(n):
            if len(f) < 2 or f.casefold() in own or f.casefold() in WEEK_AWAY_LEAD_WORDS:
                continue
            if re.search(r"(?i)(?<!\w)" + re.escape(f) + r"$", lead):
                return True
    return False


def _week_days(workspace_root, meetings, now: _dt.datetime, wall: Any = False) -> Dict[str, Any]:
    """The seven days' meetings for the Week view, from the same calendar
    read as the Today list (the run reads seven days for it). Each day: its
    date, its name ("Today" for the first), `away` (the title of an all-day
    event that says time away, else None), its meetings in start order, each
    marked `personal` and, on an away day, `clash` for a business meeting
    with a time. Days are the owner's own, in the workspace's time zone.
    cr1#1299: a day's meetings are counted by the Today feed's rule
    (cr1#1293, `_no_one_else`): a timed event with nobody else on it is
    marked `solo`, stays on the day, is counted in `n_blocks` and not in
    `n_meetings`, and is never a clash.
    cr1#1539: a timed row also carries the page's calendar fields
    (`_meeting_extras`), and each day its `free` time: the gaps inside the
    working hours around every timed event read for it, a personal one
    included (it is time taken, and the gap says nothing of what took it).
    A day marked away has none; today's start at the next quarter hour."""
    ckind = calendar_stamp(workspace_root)   # cr1#1815
    tzinfo = _workspace_tz(workspace_root)
    today = now.astimezone(tzinfo).date()
    dates = _week_dates(today)
    first, last = dates[0], dates[-1]
    by_day: Dict[_dt.date, List[Dict[str, Any]]] = {d: [] for d in dates}
    away: Dict[_dt.date, str] = {}
    busy: Dict[_dt.date, List[tuple]] = {d: [] for d in dates}
    if wall is False:
        wall = _meeting_wall(workspace_root)
    # cr1#1641: the names a time-away title is checked against, read once
    # and only when some event might be time away.
    names_box: List[Dict[str, List[str]]] = []

    def names_now() -> Dict[str, List[str]]:
        if not names_box:
            names_box.append(_away_names(workspace_root))
        return names_box[0]

    def own_away(ev: Dict[str, Any], title: str, personal_stay: bool = False) -> bool:
        if not (personal_stay or _away_calendar_says(ev) or WEEK_AWAY_RE.search(title)):
            return False
        return _own_time_away(ev, title, names_now(), personal_stay=personal_stay)

    for m in meetings or []:
        if not isinstance(m, dict):
            continue
        title = str(m.get("title") or "").strip()[:200]
        personal = str(m.get("calendar") or "").lower() == "personal"
        all_day = bool(_DATE_ONLY_RE.match(str(m.get("start") or "").strip()))
        if all_day:
            s = _local_midnight(m.get("start"), tzinfo)
            if s is None:
                continue
            e = (_local_midnight(m.get("end"), tzinfo)
                 if _DATE_ONLY_RE.match(str(m.get("end") or "").strip()) else None)
            d0 = s.date()
            d1 = (e.date() if e is not None and e.date() > d0 else d0 + _dt.timedelta(days=1))
            span = []
            d = max(d0, first)
            while d < d1 and d <= last:
                span.append(d)
                d += _dt.timedelta(days=1)
            if not span:
                continue
            stay = personal and (d1 - d0).days >= WEEK_AWAY_PERSONAL_DAYS
            if own_away(m, title, stay):
                for d in span:
                    away.setdefault(d, title or "time away")
                continue
            kind = _away_kind(m, title, names_now, long=True)
            for d in span:
                by_day[d].append({"meeting_id": str(m.get("meeting_id") or "").strip(), "title": title,
                                  "start": None, "end": None, "all_day": True,
                                  "personal": personal, "clash": False, "solo": False})
                by_day[d][-1].update(_join_keys(m, ckind))   # cr1#1758
                if kind:
                    by_day[d][-1]["away"] = kind   # cr1#1707
            continue
        start = _parse(m.get("start"))
        if start is None:
            continue
        end = _parse(m.get("end")) or (start + _dt.timedelta(hours=1))
        if end <= start:
            end = start + _dt.timedelta(minutes=1)
        # Every day it covers, an event that began before today included
        # (a conference, a flight over midnight); the end is exclusive.
        ds = start.astimezone(tzinfo).date()
        de = (end - _dt.timedelta(microseconds=1)).astimezone(tzinfo).date()
        span = []
        d = max(ds, first)
        while d <= de and d <= last:
            span.append(d)
            d += _dt.timedelta(days=1)
        if not span:
            continue
        if end - start >= _dt.timedelta(hours=WEEK_AWAY_TIMED_HOURS) and (
                _away_calendar_says(m) or WEEK_AWAY_RE.search(title)):
            if own_away(m, title):
                for d in span:
                    away.setdefault(d, title)
                continue
            # cr1#1641: someone else's day-long time away is shown on its
            # days as an all-day row, so it takes none of the owner's free
            # time and is never a clash.
            for d in span:
                by_day[d].append({"meeting_id": str(m.get("meeting_id") or "").strip(), "title": title,
                                  "start": None, "end": None, "all_day": True,
                                  "personal": personal, "clash": False, "solo": False, "away": "other"})
                by_day[d][-1].update(_join_keys(m, ckind))   # cr1#1758
            continue
        extras = _meeting_extras(m, wall)
        # cr1#1707: a shorter event that is time away all the same (the
        # calendar's own mark, a colleague's "<Name> OOO") is no meeting
        away_kind = _away_kind(m, title, names_now, long=False)
        # cr1#1541: where it is, as a Today row carries it, only when the read
        # gave it; for a personal event only inside the Today list's own window
        # (the next UPCOMING_HOURS), so the Week shows no more of the personal
        # calendar than Today already does.
        place = str(m.get("place") or m.get("location") or "").strip()[:200]
        if personal and start > now + _dt.timedelta(hours=UPCOMING_HOURS):
            place = ""
        for d in span:
            row = {"meeting_id": str(m.get("meeting_id") or "").strip(), "title": title,
                   "start": _iso(start), "end": _iso(end), "all_day": False,
                   "personal": personal, "clash": False, "solo": _no_one_else(m)}
            row.update(_join_keys(m, ckind))   # cr1#1758
            row.update(extras)
            if away_kind:
                row["away"] = away_kind
            if place:
                row["place"] = place
            by_day[d].append(row)
            busy[d].append((start, end))
    # cr1#1833 (decision 141n, add-only): Coming up and the week view show titles, so the
    # week's rows carry the same mark as Today's (an unread off-limits list marks every one)
    for ms in by_day.values():
        for row in ms:
            if _title_names_off(row.get("title"), wall):
                row["title_off"] = True
    h0, h1 = _work_hours(workspace_root)
    days = []
    for d in dates:
        ms = by_day[d]
        if d in away:
            for x in ms:
                x["clash"] = bool(not x["all_day"] and not x["personal"] and not x["solo"])
        ms.sort(key=lambda x: (not x["all_day"], x["start"] or ""))
        free: List[Dict[str, str]] = []
        if d not in away:
            lo = _dt.datetime.combine(d, _dt.time(h0), tzinfo)
            hi = (_dt.datetime.combine(d, _dt.time(0), tzinfo) + _dt.timedelta(days=1)
                  if h1 >= 24 else _dt.datetime.combine(d, _dt.time(h1), tzinfo))
            if d == today:
                local = now.astimezone(tzinfo).replace(second=0, microsecond=0)
                local += _dt.timedelta(minutes=(-local.minute) % 15)
                lo = max(lo, local)
            if lo < hi:
                free = _free_slots(busy[d], lo, hi)
        days.append({"date": d.isoformat(), "name": _day_name(d, today), "away": away.get(d),
                     "meetings": ms[:WEEK_DAY_MEETINGS],
                     "n_meetings": sum(1 for x in ms if not x["solo"]),
                     "n_blocks": sum(1 for x in ms if x["solo"]),
                     "n_clashes": sum(1 for x in ms if x["clash"]),
                     "free": free})
    # cr1#1541: the zone the days, the hours and the free time are in, so the
    # Week plans on the workspace's own clock whatever clock the viewer has.
    return {"today": today.isoformat(), "days": days, "hours": {"start": h0, "end": h1},
            "tz": _zone_name(workspace_root)}


#: cr1#1541: how many planned blocks travel on the board feed.
PLANNED_CAP = 60


def _plan_moves(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """READ. cr1#1541: {commitment id: its planned time}, for each item whose
    newest date move (a `commitment_updated` that sets or clears its date)
    carries a planned time of day (`plan_at`, `plan_minutes`, which
    `commitment_state.apply_later` writes for a move planned on the Week):
    `{due, at, minutes, plan}`, `plan` being the plan the move's batch names.
    A later move with no time (a Put it back, another push) stands in its
    place, so a plan taken back is no longer one. `events` is the owner's
    ledger as the board already read it (one read serves both). Never writes."""
    import commitment_state as cs

    newest: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "commitment_updated":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if "new_due" not in d and not d.get("due_cleared"):
            continue
        cid = str(d.get("commitment_id") or d.get("target_id") or "")
        if cid:
            newest[cid] = d
    out: Dict[str, Dict[str, Any]] = {}
    for cid, d in newest.items():
        if "plan_at" not in d:
            continue
        try:
            f = cs.plan_fields({"at": d.get("plan_at"), "minutes": d.get("plan_minutes")})
        except ValueError:
            continue
        # Review of #1606: a put-back row carries its block's plan as `plan_batch_id`.
        batch = str(d.get("brain_batch_id") or d.get("plan_batch_id") or "")
        out[cid] = {"due": str(d.get("new_due") or "")[:10], "at": f["plan_at"], "minutes": f["plan_minutes"],
                    "plan": batch[len(PLAN_BATCH_PREFIX):] if batch.startswith(PLAN_BATCH_PREFIX) else ""}
    return out


def feed_board(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The Week view's board (cr1#1017, option D): the next seven days,
    today first, each with its due items as cards, and under the board one
    lane per confirmed priority (ruling B2: suggested ones are not on it)
    with its threads' cards on the same days. Cards come from the open
    list's confirmed rows both ways, never a second count of the log; a card
    past its date sits on today, marked `late`. The meetings come from the
    Today feed's `week` (the calendar read is the prep run's); the page joins
    them by date. An item on no thread names the seat's word ("No thread").
    cr1#1657: an item someone owes the owner that a Later hid until a day
    is drawn on that day, marked `check_back` (that day), never late.
    Never writes. No money anywhere."""
    import thread_cards as tc
    import words as _w

    now = _now(now_iso)
    root = Path(str(workspace_root))
    out: Dict[str, Any] = {"generated_at": _iso(now), "today": None, "days": [], "priorities": [],
                           "n_due": 0, "n_late": 0, "errors": []}
    try:
        seat = _w.load_words(root)
    except Exception:  # noqa: BLE001 - the defaults name the homeless item
        seat = None
    no_home = "No " + _w.label(seat, "thread")
    # cr1#1292 (decided under the owner's 2026-11-16 word): an item a Later
    # moved is hidden from the lists until its date, but the board is a
    # calendar of days, so it keeps the card, on its new day.
    # cr1#1541: the owner's ledger is read once here, for the moved items and
    # for the planned times below.
    try:
        from events_io import load_events_owner_scoped
        ledger: Optional[List[Dict[str, Any]]] = load_events_owner_scoped(str(root))[0]
    except Exception:  # noqa: BLE001 - each reader below falls back as it always did
        ledger = None
    try:
        from mute_ledger import workspace_moved_ids
        moved = workspace_moved_ids(root, _iso(now), events=ledger)
    except Exception:  # noqa: BLE001 - no read: the board shows what the lists show
        moved = set()
    # cr1#1657: an item someone owes the owner that a Later hid until a day
    # keeps their date, so the board draws it on that day, marked
    # `check_back`, never as late, instead of losing it until then.
    try:
        from mute_ledger import workspace_check_backs
        back: Dict[str, str] = workspace_check_backs(root, _iso(now), events=ledger)
    except Exception:  # noqa: BLE001 - no read: the board shows what the lists show
        back = {}
    keep = set(moved) | set(back)
    try:
        opens, today, _n, _from_log = tc._open_by_thread(root, _iso(now), keep_ids=keep)
    except Exception:  # noqa: BLE001 - an unread open list leaves the board empty and says so
        out["errors"].append("open")
        opens, today = {}, now.astimezone(_workspace_tz(workspace_root)).date()
    try:
        cards, _cl = tc.read_cards(root)
        names = {str(t): str(c.get("name") or "") for t, c in (cards.get("threads") or {}).items()}
    except Exception:  # noqa: BLE001 - a card that will not read leaves the thread unnamed
        out["errors"].append("cards")
        names = {}
    dates = _week_dates(today)
    last = dates[-1]
    on_day: Dict[str, List[Dict[str, Any]]] = {d.isoformat(): [] for d in dates}
    seen = set()
    on_threads: Dict[str, set] = {}

    def place(cid: str, due: str) -> Optional[tuple]:
        """`(day, late, check_back)` for a card, or None when it is not on
        the board: a check-back on its day (cr1#1657), never late; anything
        else on its due date, a late one on today."""
        try:
            d = _dt.date.fromisoformat(back.get(cid) or due)
        except ValueError:
            return None
        if d > last:
            return None
        if back.get(cid):
            d = max(d, today)
            return d, False, d.isoformat()
        return (today, True, "") if d < today else (d, False, "")

    for tid, v in opens.items():
        for direction in ("you_owe", "owed_to_you"):
            for r in v.get(direction) or []:
                cid = str(r.get("id") or "")
                if cid and tid:
                    on_threads.setdefault(cid, set()).add(str(tid))
                due = str(r.get("due") or "")[:10]
                if not cid or cid in seen or not (due or back.get(cid)):
                    continue
                spot = place(cid, due)
                if spot is None:
                    continue
                seen.add(cid)
                card = {
                    "id": cid, "title": str(r.get("title") or "")[:240], "due": due,
                    "direction": direction, "late": spot[1], "thread_id": str(tid or ""),
                    "thread": (names.get(str(tid)) or no_home)[:160] if tid else no_home,
                }
                if spot[2]:
                    card["check_back"] = spot[2]
                on_day[spot[0].isoformat()].append(card)
    # The items on no thread are not in the per-thread open list: they come
    # from the loose-ends list's unfiled kind, the same read as the Work
    # streams tab's "No home yet", confirmed promises only, like the rest.
    try:
        import loose_ends as le
        view = le.loose_ends_view(root, _iso(now), kind=le.KIND_UNFILED, keep_ids=keep)
    except Exception:  # noqa: BLE001 - the homeless items stay off, and it says so
        out["errors"].append("no_home")
        view = {}
    for r in view.get("rows") or []:
        cid = str(r.get("id") or "")
        direction = str(r.get("direction") or "")
        due = str(r.get("due") or "")[:10]
        if (not cid or cid in seen or not (due or back.get(cid)) or direction not in ("you_owe", "owed_to_you")
                or "unconfirmed" in (r.get("kinds") or [])):
            continue
        spot = place(cid, due)
        if spot is None:
            continue
        seen.add(cid)
        card = {"id": cid, "title": str(r.get("title") or "")[:240], "due": due, "direction": direction,
                "late": spot[1], "thread_id": "", "thread": no_home}
        if spot[2]:
            card["check_back"] = spot[2]
        on_day[spot[0].isoformat()].append(card)
    order = {"you_owe": 0, "owed_to_you": 1}
    all_cards: List[Dict[str, Any]] = []
    for d in dates:
        key = d.isoformat()
        cs = on_day[key]
        cs.sort(key=lambda c: (c["late"], c["due"], order.get(c["direction"], 2), c["title"].lower()))
        all_cards += cs
        out["days"].append({"date": key, "name": _day_name(d, today), "due": cs[:WEEK_DAY_CARDS],
                            "n_due": len(cs), "n_late": sum(1 for c in cs if c["late"]),
                            "n_more": max(0, len(cs) - WEEK_DAY_CARDS)})
    out["today"] = today.isoformat()
    # cr1#1208: the zone the board's days are in, so the page judges a
    # stale board on the workspace's day, never the viewer's.
    out["tz"] = _zone_name(workspace_root)
    out["n_due"] = len(all_cards)
    out["n_late"] = sum(1 for c in all_cards if c["late"])
    # cr1#1541: the items the owner planned on the Week, each at the time of
    # day he put it: a card on its day whose standing date move carries a
    # planned time and set the date the card has. New and optional: a page
    # from before this stage never reads it; no read leaves it out.
    try:
        if ledger is None:
            raise ValueError("no ledger read")
        moves = _plan_moves(ledger)
        planned = [{"id": c["id"], "title": c["title"], "date": c["due"], "at": moves[c["id"]]["at"],
                    "minutes": moves[c["id"]]["minutes"], "plan": moves[c["id"]]["plan"]}
                   for c in all_cards if not c["late"] and c["id"] in moves and moves[c["id"]]["due"] == c["due"]]
        out["planned"] = planned[:PLANNED_CAP]
    except Exception:  # noqa: BLE001 - an unread log leaves the planned blocks off
        pass
    try:
        pv = tc.priorities_view(root, _iso(now))
    except Exception:  # noqa: BLE001 - no priorities leaves the lanes off, and says so
        out["errors"].append("priorities")
        pv = {}
    for p in (pv.get("confirmed") or [])[:WEEK_LANES]:
        threads = {str(t) for t in p.get("built_from") or []}
        lane_days = []
        for d in dates:
            key = d.isoformat()
            lane_days.append({"date": key, "cards": [{"id": c["id"], "title": c["title"], "late": c["late"]}
                                                     for c in on_day[key] if (on_threads.get(c["id"]) or {c["thread_id"]}) & threads]})
        out["priorities"].append({"id": str(p.get("id") or ""), "statement": str(p.get("statement") or "")[:200],
                                  "days": lane_days, "n": sum(len(x["cards"]) for x in lane_days),
                                  "last_action": _priority_last_action(p)})
    return _with_words(workspace_root, out)


def _no_one_else(m: Dict[str, Any]) -> bool:
    """cr1#1293: the run's `others` (how many people besides the owner are on
    the event) says nobody else is on it. Only a plain 0 does: an event the
    read says nothing about, or a count that is not a whole number, stays a
    meeting, so a real call is never hidden."""
    n = m.get("others")
    return isinstance(n, int) and not isinstance(n, bool) and n == 0


#: cr1#1541: at most this many week rows carry a brief's own text (the Prep
#: task writes briefs for the next four meetings); a brief a Today row
#: already carries is never sent twice.
WEEK_PREP_TEXTS = 4


def _week_prep(workspace_root, week: Dict[str, Any], rows: List[Dict[str, Any]],
               hold: _dt.timedelta) -> None:
    """cr1#1541: each week meeting carries the prep state a Today row carries,
    read from the same `prep_brief` receipts: `prep` (the brief's path, when a
    receipt names one), `prep_at`, and `prep_shown_until` (an hour after it
    ends). The brief's own text (`prep_text`) rides the first row of a
    meeting only when no Today row carries that meeting's brief, so the Week
    can open it in place and a brief travels once. Every field is new and
    optional: a page from before this stage reads none of them. Writes the
    rows in place; reads only."""
    # A personal event gains none of it (the personal wall): the Prep task
    # briefs no personal call, and nothing of one travels to the Week.
    wrows = [r for d in (week or {}).get("days") or [] for r in d.get("meetings") or []
             if isinstance(r, dict) and r.get("meeting_id") and not r.get("personal")]
    if not wrows:
        return
    from receipts import prep_receipts

    newest: Dict[str, Dict[str, Any]] = {}
    for rec in prep_receipts(workspace_root, meeting_ids={r["meeting_id"] for r in wrows}):
        newest[rec["meeting_id"]] = rec   # oldest first, so the last wins
    on_today = {r["meeting_id"] for r in rows if r.get("prep_text")}
    texted: set = set()
    for r in wrows:
        rec = newest.get(r["meeting_id"])
        if not rec:
            continue
        art = rec.get("artifact")
        if isinstance(art, str) and art.strip():
            rel = art.strip().replace("\\", "/")
            found_path = _prep_path(workspace_root, rel)
            if found_path is not None:
                try:
                    rel = found_path.relative_to(Path(str(workspace_root))).as_posix()
                except ValueError:
                    pass
            r["prep"] = rel
            if (r["meeting_id"] not in on_today and r["meeting_id"] not in texted
                    and len(texted) < WEEK_PREP_TEXTS):
                text = prep_text(workspace_root, rel)
                if text:
                    r["prep_text"] = text
                    texted.add(r["meeting_id"])
        if rec.get("dt"):
            r["prep_at"] = _iso(rec["dt"])
        end = _parse(r.get("end"))
        if end is not None:
            r["prep_shown_until"] = _iso(end + hold)


def _occurrence_receipt(recs: List[Dict[str, Any]], start: Any, tz: Any = None) -> Optional[Dict[str, Any]]:
    """cr1#1632 review rounds 2 and 3: the newest `prep_brief` receipt
    (oldest first in `recs`) whose `meeting_start` is empty or names this
    row's start, the day read in the workspace's zone `tz`; None when none
    does (the series' briefs are all for other dates)."""
    import hub_meeting

    for rec in reversed(recs):
        data = (rec.get("raw") or {}).get("data") if isinstance(rec.get("raw"), dict) else None
        ms = (data or {}).get("meeting_start") if isinstance(data, dict) else None
        if ms and not hub_meeting._same_start(ms, start, tz):
            continue
        return rec
    return None


def _brief_rel(workspace_root, art: Any) -> Optional[str]:
    """A receipt's brief, folder-relative where it is found on disk."""
    if not (isinstance(art, str) and art.strip()):
        return None
    rel = art.strip().replace("\\", "/")
    found_path = _prep_path(workspace_root, rel)
    if found_path is not None:
        try:
            rel = found_path.relative_to(Path(str(workspace_root))).as_posix()
        except ValueError:
            pass
    return rel


def feed_today(workspace_root, meetings: Optional[List[Dict[str, Any]]] = None,
               *, now_iso=None, device_workspace: Optional[str] = None) -> Dict[str, Any]:
    """The Today feed: the next 24 hours of meetings as the run read them
    from the calendars (`{meeting_id, title, start, end, calendar, others}`
    each, `calendar` one of business or personal, `others` how many people
    besides the owner are on it). A timed event with `others` 0 is a block of
    the owner's own time (`solo`, cr1#1293): it stays on the list and is left
    out of `n_meetings`. Each row comes with its prep when a
    `prep_brief` receipt names one, shown until an hour after the meeting
    ends. The calendar read is the run's; this only joins it to the book.

    cr1#1539: each row also carries the page's calendar fields, where the
    read gave them (`_meeting_extras`: the event's id, a join link, the
    other people's names, whether the owner organises it, his answer). The
    day's meetings that ended more than that hour ago are kept apart, in
    `past`, so "Past meetings" lists the whole day and `meetings` (what the
    day's sentence, its count and its clash read) is the list it always was.
    Every new field is optional: an older page reads none of them."""
    ckind = calendar_stamp(workspace_root)   # cr1#1815
    now = _now(now_iso)
    horizon = now + _dt.timedelta(hours=UPCOMING_HOURS)
    hold = _dt.timedelta(minutes=PREP_HOLD_MINUTES)
    tzinfo = None
    rows = []
    past: List[Dict[str, Any]] = []
    ended: set = set()
    wall = _meeting_wall(workspace_root)
    descs: Dict[int, Any] = {}
    day_start = None
    # cr1#1707: the names a time-away title is checked against, read once and only when needed
    names_box: List[Dict[str, List[str]]] = []

    def names_now() -> Dict[str, List[str]]:
        if not names_box:
            names_box.append(_away_names(workspace_root))
        return names_box[0]

    for m in meetings or []:
        if not isinstance(m, dict):
            continue
        all_day = bool(_DATE_ONLY_RE.match(str(m.get("start") or "").strip()))
        if all_day:
            tzinfo = tzinfo or _workspace_tz(workspace_root)
            start = _local_midnight(m.get("start"), tzinfo)
            if start is None:
                continue
            end = (_local_midnight(m.get("end"), tzinfo) if _DATE_ONLY_RE.match(str(m.get("end") or "").strip())
                   else None) or (start + _dt.timedelta(days=1))
            if end <= now or start > horizon:
                continue
        else:
            start = _parse(m.get("start"))
            if start is None:
                continue
            end = _parse(m.get("end")) or (start + _dt.timedelta(hours=1))
            if end + hold < now:
                # cr1#1539: ended today, past the hour its row stays up.
                if day_start is None:
                    tzinfo = tzinfo or _workspace_tz(workspace_root)
                    day_start = now.astimezone(tzinfo).replace(hour=0, minute=0, second=0, microsecond=0)
                ended.add(id(m))
                # cr1#1668: the last PAST_DAYS days, which the read now covers
                if start >= day_start - _dt.timedelta(days=PAST_DAYS):
                    gone = {"meeting_id": str(m.get("meeting_id") or "").strip(),
                            "title": str(m.get("title") or "").strip()[:200],
                            "start": _iso(start), "end": _iso(end), "solo": _no_one_else(m)}
                    gone.update(_join_keys(m, ckind))   # cr1#1758
                    # a personal one says so, as a row does; any other names no calendar
                    if str(m.get("calendar") or "").lower() == "personal":
                        gone["calendar"] = "personal"
                    # who was there, as the read named them (an off-limits name never)
                    extras = _meeting_extras(m, wall)
                    people = extras.get("people")
                    if people:
                        gone["people"] = people
                    # cr1#1833 (decision 141n, add-only): the title names someone off
                    # limits, or the off-limits list could not be read. The page then
                    # shows the time and a tag only, offers nothing on it, and lists it
                    # nowhere as a follow-up. `held` keeps its own meaning.
                    if _title_names_off(gone["title"], wall):
                        gone["title_off"] = True
                    # decision 141n (add-only): the event was time away, which the page
                    # cannot tell from a meeting on a past row without this
                    away_kind = _away_kind(m, gone["title"], names_now,
                                           long=all_day or end - start >= _dt.timedelta(hours=WEEK_AWAY_TIMED_HOURS))
                    if away_kind:
                        gone["away"] = away_kind   # "own" or "other", as a current row carries it
                    past.append(gone)
                continue
            if start > horizon:
                continue
        rows.append({
            "meeting_id": str(m.get("meeting_id") or "").strip(),
            "title": str(m.get("title") or "").strip()[:200],
            "start": _iso(start), "end": _iso(end),
            "calendar": "personal" if str(m.get("calendar") or "").lower() == "personal" else "business",
            "place": str(m.get("place") or m.get("location") or "").strip()[:200] or None,
            "prep": None, "prep_text": None, "prep_on_pc": None, "prep_at": None,
            "prep_shown_until": _iso(end + hold), "all_day": all_day,
            "solo": not all_day and _no_one_else(m),
        })
        # cr1#1758: the keys the page joins its own calendar read on, on every
        # row: a personal and an all-day one too (a key is a hash, never the id)
        rows[-1].update(_join_keys(m, ckind))
        # cr1#1788: the invite's text, attached once below; it travels as
        # `invite_description`, the one key the door's path fence reads as words
        descs[id(rows[-1])] = m.get("invite_description")
        if not all_day:
            rows[-1].update(_meeting_extras(m, wall))
        # cr1#1833 (decision 141n, add-only): as on the past list
        if _title_names_off(rows[-1]["title"], wall):
            rows[-1]["title_off"] = True
        # cr1#1707: time away, the owner's or someone else's, is no meeting:
        # Today's big card, its Next list and the day's count leave it out
        kind = _away_kind(m, rows[-1]["title"], names_now,
                          long=all_day or end - start >= _dt.timedelta(hours=WEEK_AWAY_TIMED_HOURS))
        if kind:
            rows[-1]["away"] = kind
    ids = [r["meeting_id"] for r in rows if r["meeting_id"]]
    # cr1#1632: ONE read of the log, for the receipts and the saved meeting
    # pages both (the one-call refresh reads it once, HUBONECALL1).
    log: Optional[List[dict]] = None
    if rows or past:   # cr1#1668: the past rows' wrap-ups read it too
        try:
            from receipts import ledger_events

            log = ledger_events(workspace_root)
        except Exception:  # noqa: BLE001 - each reader then reads for itself, or not at all
            log = None
    if ids:
        try:
            from receipts import prep_receipts

            found = prep_receipts(workspace_root, meeting_ids=ids, events=log)
        except Exception:  # noqa: BLE001 - a bad book leaves the rows without prep
            found = []
        newest: Dict[str, Dict[str, Any]] = {}
        series: Dict[str, List[Dict[str, Any]]] = {}
        for rec in found:
            newest[rec["meeting_id"]] = rec  # oldest first, so the last wins
            series.setdefault(rec["meeting_id"], []).append(rec)
        ws_tz = _workspace_tz(workspace_root)
        for r in rows:
            rec = newest.get(r["meeting_id"])
            if not rec:
                continue
            # cr1#1632 review rounds 2 and 3: THIS occurrence's brief when the
            # series has one (a recurring series shares one id; another date's
            # brief would show its prose beside this one's parts, and save its
            # decisions and carry with nobody on them). With none for this
            # date, the series' newest stands for the prose, as before, and
            # there are no parts.
            own = _occurrence_receipt(series.get(r["meeting_id"]) or [], r.get("start"), ws_tz)
            rec = own or rec
            rel = _brief_rel(workspace_root, rec.get("artifact"))
            if rel:
                r["prep"] = rel
                # cr1#655: the brief itself, so the page opens it in place
                r["prep_text"] = prep_text(workspace_root, rel)
                # cr1#1632 slice 1: its parts as lists, for the live prep page
                parts = prep_parts(workspace_root, rel) if own and r.get("calendar") != "personal" else None
                if parts:
                    r["prep_parts"] = parts
                if device_workspace:
                    r["prep_on_pc"] = str(device_workspace).rstrip("/\\") + "/" + rel
            if rec.get("dt"):
                r["prep_at"] = _iso(rec["dt"])
    rows.sort(key=lambda r: r["start"])
    past.sort(key=lambda r: r["start"])
    # cr1#1788: the invite's own text, once per meeting, on the next few only
    _today_invites(workspace_root, rows, descs, wall, now)
    # cr1#1632 slice 1: a meeting whose live prep page was saved says so, with
    # the press its Undo names.
    try:
        import hub_meeting

        _evs = log or []
        _tzw = _workspace_tz(workspace_root) if rows else None
        for r in rows:
            sv = hub_meeting.saved_for(_evs, r["meeting_id"], r.get("start"), _tzw) if r.get("meeting_id") else None
            if sv:
                r["prep_saved"] = sv
        # cr1#1632 slice 3: what a past meeting's wrap-up page shows: the
        # decisions and open promises captured from it, and its wrap-up
        # review of #1669, item 7: a meeting that ended within the hour is
        # still on `meetings`; it gets its wrap-up too
        just = [r for r in rows if not r.get("all_day") and r.get("end") and (_parse(r["end"]) or now) <= now]
        if past or just:
            if not _evs:
                from events_io import load_events_owner_scoped

                _evs = load_events_owner_scoped(str(workspace_root))[0]
            _owner = _owner_id(workspace_root)
            _root = Path(str(workspace_root))
            _pers = hub_meeting.personal_fence(workspace_root)           # the one fence, once per feed (round 3, item 1)
            _off = _pers["off"] if _pers else None
            _tz = hub_meeting._ws_tz(_root)
            _open = hub_meeting.open_projection(workspace_root, _evs)   # once per feed (item 10)
            _idx = hub_meeting.index_by_ref(_evs)                        # once per feed (round 2, note 9)
            # round 2, item 2: only the rows that get a wrap-up claim notes
            # (a personal or solo copy of a meeting never takes its notes)
            ended_all = sorted([r for r in past + just if r.get("meeting_id") and not r.get("solo")
                                and r.get("calendar") != "personal"], key=lambda r: r["start"])
            # cr1#1668: each occurrence of a series ends where the next begins
            nxt: Dict[str, str] = {}
            for r in reversed(ended_all):
                if r.get("meeting_id"):
                    if r["meeting_id"] in nxt:
                        r["next_start"] = nxt[r["meeting_id"]]
                    nxt[r["meeting_id"]] = r["start"]
            # item 3: each receipt claimed by the one listed meeting it followed
            claims = hub_meeting.claim_receipts(_evs, ended_all)
            for i, r in enumerate(ended_all):
                r["wrap"] = hub_meeting.wrap_for(workspace_root, r, _evs, owner_id=_owner, off=_off,
                                                 ref=claims.get(i, ""), open_rows=_open, tz=_tz,
                                                 personal=_pers, index=_idx)
    except Exception:  # noqa: BLE001 - an unread log leaves the page as unsaved
        pass
    try:
        # The week keeps the read it always had: a meeting that ended more
        # than the hold ago is in `past`, and is no row and no clash here.
        week = _week_days(workspace_root, [m for m in meetings or [] if id(m) not in ended], now, wall)
    except Exception:  # noqa: BLE001 - the list never fails for the week's sake
        week = {"today": None, "days": [], "error": True}
    try:
        _week_prep(workspace_root, week, rows, hold)
    except Exception:  # noqa: BLE001 - a bad book leaves the week's rows without prep, as the list's
        pass
    prepx_lines = _today_prepx(workspace_root, rows, past, week, now)
    return _fit_today(_with_words(workspace_root, {"generated_at": _iso(now), "meetings": rows,
            "n_meetings": sum(1 for r in rows if not r["solo"]), "n_prepped": sum(1 for r in rows if r["prep"]),
            "week": week, "about": _today_about(workspace_root, now),
            # cr1#1788 fix check: edits a move left at another time of their meeting
            **({"prepx_line": " ".join(prepx_lines)} if prepx_lines else {}),
            # cr1#1830: why the rows name nobody, only when the list cannot be read
            **({"people_line": OFF_LIMITS_UNREAD_TODAY_LINE}
               if wall is None and not _off_limits_readable(workspace_root) else {}),
            "past": past[-PAST_MEETINGS_CAP:], "past_days": PAST_DAYS,
            # review of #1669, note 12: how many older ones the cap left out
            "past_more": max(0, len(past) - PAST_MEETINGS_CAP),
            # review of #1669 round 7: the wrap-up meetings the cap leaves off the
            # list, bare, so a press still claims over its hidden siblings (one list,
            # not one per row: a per-row list grows with the square of a title's count)
            # cr1#1833: a walled title is never carried here either
            "past_hidden": [{"meeting_id": r.get("meeting_id") or "", "title": "" if r.get("title_off") else r.get("title") or "", "start": r["start"]}
                            for r in past[:-PAST_MEETINGS_CAP] if r.get("wrap")][-PAST_HIDDEN_CAP:]}))


def _today_about(workspace_root, now: _dt.datetime) -> Optional[Dict[str, Any]]:
    """What the owner said today is about (cr1#1017, "one sentence on top"):
    `{for_date, items: [text, ...]}` from the one day-intent reader, or None.

    Only the owner's own word: a `proposed` guess never renders as fact
    (`load_day_intent`'s default), and a day with no statement leaves the
    page to say what the day holds instead. Read only."""
    try:
        from day_intent import load_day_intent, workspace_today

        day = workspace_today(workspace_root, now=now).isoformat()
        rec = load_day_intent(workspace_root, day)
    except Exception:  # noqa: BLE001 - no timezone or an unreadable book says nothing
        return None
    if not rec:
        return None
    items = [" ".join(str(i.get("text") or "").split())[:200] for i in rec.get("items") or []]
    items = [t for t in items if t]
    return {"for_date": rec.get("for_date"), "items": items} if items else None


def _plate_rows(dv: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    order = 0
    for sec in dv.get("sections") or []:
        title = str(sec.get("title") or "")
        project = title.split(" · ", 1)[1] if " · " in title else title
        for it in sec.get("items") or []:
            order += 1
            rows.append({
                "id": it.get("n"), "n": it.get("display_n"),
                "title": str(it.get("name") or "")[:240],
                "lane": sec.get("lane") or it.get("block") or "",
                "project": project[:120], "context": str(it.get("context_tag") or "")[:160],
                "horizon": it.get("horizon") or "", "pending": bool(it.get("pending")),
                "order": order, "folded_ids": list(it.get("folded_ids") or []),
            })
    return rows


#: Where a promise came from (cr1#725), read from the row's own record: the
#: source word by the pointer's scheme, and the line capped for the page.
ORIGIN_LINE_CAP = 300
_ORIGIN_SOURCES = {"granola": "Meeting", "gmail": "Email", "outlook": "Email", "superhuman": "Email",
                   "mail": "Email", "slack": "Slack"}
_ORIGIN_SKILLS = {"meeting-notes": "Meeting", "inbox-triage": "Email", "reconcile-sent": "Email",
                  "commitments": "Chat", "commitment-triage": "Chat", "session-sweep": "Chat"}


def _person_names(workspace_root) -> Dict[str, str]:
    from entities_io import entities_collection, read_entities_json

    try:
        ent = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return {p["id"]: str(p.get("display_name") or p.get("canonical_name") or p.get("name") or "")
            for p in entities_collection(ent, "people") if isinstance(p, dict) and p.get("id")}


def _row_origin(ev: Dict[str, Any], names: Dict[str, str], owner: str, tzinfo, ties=frozenset(),
                hp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`{source, date, captured, who, line, paraphrase}` for one commitment,
    from its own record. `who` reads by direction, because no record says who
    spoke: `from X` on a promise someone else owns, `to X` on the owner's own.
    A row with no meeting date shows the day it was captured, on the owner's
    calendar, and says it is that."""
    from capture_gate import granola_ref_ok

    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    ref = str(d.get("source_ref") or ev.get("source_ref") or "")
    scheme = ref.split(":", 1)[0].lower()
    if ref.startswith("user:hub_todo"):
        source = "Added on the Hub"
    elif scheme == "user" or (scheme == "granola" and not granola_ref_ok(ref)):
        source = "Added by you"
    elif scheme in ("session", "chat"):
        source = "Chat"
    else:
        source = (_ORIGIN_SOURCES.get(scheme)
                  or _ORIGIN_SKILLS.get(str(ev.get("source_skill") or "").split(":")[0]) or "Captured")
    # cr1#1658 change 4: an item made on the Hub from an Inbox message says
    # so; change 8: from a person's card or a meeting, too
    about = str(d.get("about_ref") or "")
    if about.startswith("mail:thread:"):
        source += ", from a message"
    elif about.startswith("person:"):
        # review round 1 of #1717: a person on the personal side (or ties unread: None) is never named here
        pid = about.split(":", 1)[1]
        source += ", from " + ((names.get(pid) if ties is not None and pid not in ties else "") or "a person's card")
    elif about.startswith("meeting:"):
        source += ", from a meeting"
    held = str(d.get("owner_id") or "")
    outside = str(d.get("owner_external") or "").strip()
    # cr1#1721: #1717's rule for the captured rows' who, too. Someone off
    # limits or on the personal side (`ties`) is never named, nor a free-text
    # name that is theirs; with the ties or the fence unread (`ties` None),
    # nobody is named at all.
    hidden = {str(names.get(i) or "").strip().lower() for i in (ties or ())} - {""}

    def _hid(pid: str) -> bool:
        return bool(pid) and pid != owner and (ties is None or pid in ties)

    def _text(value) -> str:
        value = str(value or "").strip()
        if ties is None or value.lower() in hidden:
            return ""
        # review of #1733: a first name or an alias of theirs is theirs too
        if hp is not None:
            import hub_work
            if hub_work.name_is_hidden(value, hp):
                return ""
        return value

    if outside or (held and held != owner):
        who = "" if _hid(held) else (str(names.get(held) or "") or _text(outside) or _text(d.get("owner_name")))
        who = "from " + who if who else ""
    else:
        cid = str(d.get("counterparty_id") or "")
        cp = "" if _hid(cid) else (("" if cid == owner else str(names.get(cid) or ""))
                                   or _text(d.get("counterparty_name"))
                                   or _text(next(iter(d.get("counterparty_names") or []), "")))
        who = "to " + str(cp) if cp else ""
    # review of #1733, item 3: the captured words can name them too; a row
    # with anyone hidden on it, or read with the fence unread, keeps none.
    typed = [outside, d.get("owner_name"), d.get("counterparty_name")] + list(d.get("counterparty_names") or [])
    veiled = (ties is None or _hid(held) or _hid(str(d.get("counterparty_id") or ""))
              or any(str(t or "").strip() and not _text(t) for t in typed))
    day = str(d.get("meeting_date") or "")[:10]
    captured = not day
    if captured:
        at = _parse(ev.get("ts"))
        day = at.astimezone(tzinfo).date().isoformat() if at else ""
    return {"source": source, "date": day, "captured": captured, "who": who[:120],
            "line": "" if veiled else str(d.get("evidence") or "").strip()[:ORIGIN_LINE_CAP],
            "paraphrase": (not veiled) and str(d.get("evidence_kind") or "") == "paraphrase"}


_CLOSER_WORDS = {
    "exit-silence": "aged out after going quiet",
    "calendar-close": "closed by a meeting that happened",
    "reconcile-sent": "closed by mail you sent",
    "inbox": "closed by replies in your inbox",
    "commitment-triage": "closed by you",
    "meeting-notes": "closed from meeting notes",
}
#: cr1#1636 item 4: a closer with no words of its own is said plainly, never by
#: its step's name (the line read "2 by commitment backlog sweep").
CLOSER_FOR_YOU = "closed for you"


def _changes(workspace_root, now: _dt.datetime, days: Optional[int] = None) -> Dict[str, Any]:
    """READ. What changed in the book in the last `days` days (default
    DONE_DAYS, the done strip's window), for Today's "what changed" line
    (cr1#1017 behaviour 4, which retires the fixed 24-hour overnight count):
    `{as_of, since, days, closed: [{ts, how}], opened: [ts], capped}`. The
    page counts from the owner's last look (the done strip's own mark)
    inside this window. Every count is the log's, never estimated, and
    follows the done strip's rules (review round 1): one close per
    commitment, its newest, dropped if the commitment was reopened since;
    one opening per commitment, its first; the personal lane kept, as the
    page is the owner's own. Unlike the strip, every close counts here
    (a tap, a silence close), with its closer in words. At most
    CHANGES_CAP of each travel, the newest; `capped` says so."""
    from events_io import load_events_org_scoped

    days = DONE_DAYS if days is None else days
    now = now.replace(microsecond=0)   # as_of is whole seconds; nothing past it travels
    since = now - _dt.timedelta(days=days)
    closes: Dict[str, Dict[str, str]] = {}   # commitment id -> its standing close
    first: Dict[str, _dt.datetime] = {}      # commitment id -> when it was first written
    try:
        events = load_events_org_scoped(str(workspace_root), drop_personal=False)[0]
    except Exception:  # noqa: BLE001 - no ledger reads as nothing changed
        events = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ts = _parse(ev.get("ts"))
        if kind == "commitment":
            cid = str(d.get("id") or "")
            if cid and ts is not None and (cid not in first or ts < first[cid]):
                first[cid] = ts
        elif kind == "commitment_resolved":
            cid = str(d.get("commitment_id") or d.get("id") or "")
            if cid and ts is not None:
                key = str(ev.get("source_skill") or "other").split(":")[0]
                closes[cid] = {"at": ts, "how": _CLOSER_WORDS.get(key, CLOSER_FOR_YOU)}
        elif kind == "commitment_reopened":
            closes.pop(str(d.get("commitment_id") or d.get("target_id") or ""), None)
    inside = lambda t: since <= t <= now  # noqa: E731
    closed = sorted(({"ts": _iso(c["at"]), "how": c["how"]} for c in closes.values() if inside(c["at"])),
                    key=lambda r: r["ts"])
    opened = sorted(_iso(t) for t in first.values() if inside(t))
    return {"as_of": _iso(now), "since": _iso(since), "days": days,
            "closed": closed[-CHANGES_CAP:], "opened": opened[-CHANGES_CAP:],
            "capped": len(closed) > CHANGES_CAP or len(opened) > CHANGES_CAP}


#: The Today tab's "Done for you" strip (cr1#1169 slice 2): how far back the
#: feed reaches (the page counts from when the owner last looked, inside
#: this), how many rows of each kind travel, and each kind's words and job
#: (A4). Every count is the log's, never estimated (the value-receipt rule).
DONE_DAYS = 7
#: Today's "what changed" line (cr1#1017 behaviour 4): at most this many
#: opened and closed times travel, the newest.
CHANGES_CAP = 1000
DONE_ROW_CAP = 100
DONE_KINDS = {
    "closed": {"one": "commitment closed", "many": "commitments closed",
               "job": "the log: each commitment_resolved"},
    "drafted": {"one": "reply drafted", "many": "replies drafted",
                "job": "the log: each email_drafted"},
    "meetings": {"one": "meeting captured", "many": "meetings captured",
                 "job": "the log: each meeting_processed"},
    "filed": {"one": "item filed to a thread", "many": "items filed to a thread",
              "job": "the log: each reclassification that gives an item a thread"},
    "reminders": {"one": "reminder set", "many": "reminders set",
                  "job": "the log: each reminder"},
    "filtered": {"one": "email kept out of the way", "many": "emails kept out of the way",
                 "job": "the last inbox read's receipt: hidden by a rule, noise, dropped by you"},
}
#: The closes that count as done for the owner: the value receipt's own
#: completion routes (`value_receipt.COMPLETION_ROUTES`, pinned equal by
#: HUBDONE1). Named here, not imported: `value_receipt` renders the Word
#: receipt, and importing it would put the Hub in the document-render family
#: (RUNTIMEGATE1's import-graph derivation).
DONE_COMPLETION_ROUTES = ("fact", "own_word")
#: The inbox receipt's counters the filtered kind adds up, and their names.
DONE_FILTER_KEYS = (("n_hidden_by_rule", "hidden_by_rule"), ("n_noise", "noise"),
                    ("n_dropped_by_preference", "dropped"))


#: Where a "Done for you" row came from, by its own type, when its pointer
#: names nothing (cr1#1184 follow-up).
_DONE_TYPE_SOURCES = {"email_drafted": "Email", "meeting_processed": "Meeting"}


def _done_source(ev: Dict[str, Any], d: Dict[str, Any], ref: Any = None) -> Dict[str, str]:
    """A "Done for you" row's source, in a word (C3; the thread panel's rule,
    `_card_source_word`): the pointer's kind, else the row's type, else the
    step that wrote it. Nothing known is no source line at all (A4), never
    "Captured" on a strip of things done. Never the pointer, the log row or
    the event id."""
    ev = ev if isinstance(ev, dict) else {}
    d = d if isinstance(d, dict) else {}
    src = {"source_ref": str(ref or d.get("source_ref") or ev.get("source_ref") or "")}
    word = _card_source_word(src, ev.get("type"))
    if word == "Captured":
        word = (_DONE_TYPE_SOURCES.get(str(ev.get("type") or ""))
                or _ORIGIN_SKILLS.get(str(ev.get("source_skill") or "").split(":")[0]) or "")
    return {"word": word} if word else {}


def _done_for_you(workspace_root, now: _dt.datetime, days: int = DONE_DAYS) -> Dict[str, Any]:
    """READ. What Command Room did for the owner in the last `days` days,
    from the log only: `{as_of, since, days, kinds: {kind: {n, ts_all, rows,
    job, one, many}}}`. Each row has its time, a title and its source (C3);
    `ts_all` holds every counted time, so the page counts exactly from when
    the owner last looked, never from the capped rows. A kind with nothing
    is absent, so a day with nothing done carries no kinds at all. No money.

    What counts (review round 1, the same rules the other readers use):
      closed     a close the system proved, by `flow_measure.route_of`'s
                 completion routes (`DONE_COMPLETION_ROUTES`), of a
                 commitment on file, once per commitment, and not reopened
                 since. A silence or lapse close, or the owner's own tap,
                 is not done for them.
      filed      an item whose newest move took it from no thread to one;
                 a bulk move (a batch stamp), a split or an undo is not.
      reminders  a reminder the owner set (`REMINDER_ORIGIN`), not cleared
                 or deleted.
    (Closes use `DONE_COMPLETION_ROUTES`, the value receipt's routes.)
      filtered   the newest inbox read's own counts, never summed across
                 reads (each read sees the same mail again)."""
    from event_gate import REMINDER_ORIGIN
    from events_io import load_events_org_scoped
    from flow_measure import route_of
    from plate_view import row_title
    from receipts import receipt_task_id

    since = now - _dt.timedelta(days=days)
    try:
        # The personal lane kept: the page is the owner's own (as feed_mine
        # reads reminders), and a reminder is personal by default.
        events = load_events_org_scoped(str(workspace_root), drop_personal=False)[0]
    except Exception:  # noqa: BLE001 - no ledger reads as nothing done
        events = []
    titles: Dict[str, str] = {}
    origins: Dict[str, str] = {}
    by_seq: Dict[Any, Dict[str, Any]] = {}
    closes: Dict[str, Dict[str, Any]] = {}      # commitment id -> its standing close
    moves: Dict[Any, Dict[str, Any]] = {}       # item seq -> its newest move
    reminders: Dict[str, Dict[str, Any]] = {}   # reminder id -> the reminder
    deleted_reminders: Dict[str, Dict[str, Any]] = {}   # cr1#1221: what a restore puts back
    inbox: Optional[Dict[str, Any]] = None

    def _when(ev):
        at = _parse(ev.get("ts"))
        return at if at is not None and since <= at <= now else None

    def _src(ev, d):
        # Its source in a word, never a pointer or a log row (cr1#1184 follow-up).
        return _done_source(ev, d)

    for ev in events:
        if not isinstance(ev, dict):
            continue
        by_seq[ev.get("seq")] = ev
        kind = ev.get("type")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if kind == "commitment":
            if d.get("id"):
                titles[str(d["id"])] = row_title(d.get("title"), d.get("summary"))
                origins[str(d["id"])] = str(d.get("source_ref") or "")
        elif kind == "commitment_resolved":
            cid = str(d.get("commitment_id") or d.get("id") or "")
            if cid:
                closes[cid] = {"ev": ev, "route": route_of(ev)}   # the newest close stands
        elif kind == "commitment_reopened":
            cid = str(d.get("commitment_id") or d.get("target_id") or "")
            closes.pop(cid, None)
        elif kind == "reclassification":
            item = d.get("supersedes_seq") or ev.get("supersedes_seq")
            if item is not None:
                moves[item] = ev
        elif kind == "reminder":
            if d.get("id") and d.get("origin", REMINDER_ORIGIN) == REMINDER_ORIGIN:
                reminders[str(d["id"])] = ev
        elif kind in ("reminder_cleared", "reminder_deleted"):
            rid_done = str(d.get("id") or d.get("reminder_id") or "")
            if kind == "reminder_deleted" and rid_done in reminders:
                deleted_reminders[rid_done] = reminders[rid_done]
            reminders.pop(rid_done, None)
        elif (kind == "reminder_updated" and d.get("action") == "restore"
              and d.get("origin") == REMINDER_ORIGIN):
            # cr1#1221: the owner's undo of a delete puts the reminder back.
            rid_back = str(d.get("reminder_id") or "")
            if rid_back in deleted_reminders:
                reminders[rid_back] = deleted_reminders.pop(rid_back)
        elif kind == "pack_run" and receipt_task_id(ev) == "inbox":
            at = _when(ev)
            got = {name: d.get(key) for key, name in DONE_FILTER_KEYS
                   if isinstance(d.get(key), int) and not isinstance(d.get(key), bool)}
            if at is not None and got and (inbox is None or _iso(at) >= inbox["ts"]):
                inbox = {"ts": _iso(at), "source": _src(ev, d), "by": got, "n": sum(got.values())}

    rows: Dict[str, List[Dict[str, Any]]] = {k: [] for k in DONE_KINDS}
    for cid, c in closes.items():
        ev = c["ev"]
        at = _when(ev)
        if at is None or c["route"] not in DONE_COMPLETION_ROUTES or cid not in titles:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        # Where the promise came from, in a word: "From a meeting" on a
        # closed row means the promise, never what closed it (review). What
        # closed it stands in only when the origin names nothing.
        src = (_done_source({"type": "commitment"}, {}, origins.get(cid)) if origins.get(cid) else {})             or _done_source(ev, d)
        rows["closed"].append({"ts": _iso(at), "source": src, "title": titles[cid][:240]})
    threads = _thread_names(workspace_root)
    for item, ev in moves.items():
        at = _when(ev)
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        new = str(d.get("new_primary_thread_id") or "")
        if at is None or not new or str(d.get("old_primary_thread_id") or "") \
                or d.get("brain_batch_id") or d.get("brain_change_class"):
            continue
        it = by_seq.get(item) or {}
        idata = it.get("data") if isinstance(it.get("data"), dict) else {}
        rows["filed"].append({"ts": _iso(at), "source": _src(ev, d),
                              "title": row_title(idata.get("title"), idata.get("summary"))[:240] or "An item",
                              "thread": threads.get(new) or None})
    for ev in reminders.values():
        at = _when(ev)
        if at is not None:
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            rows["reminders"].append({"ts": _iso(at), "source": _src(ev, d),
                                      "title": str(d.get("summary") or "A reminder")[:240]})
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") not in ("email_drafted", "meeting_processed"):
            continue
        at = _when(ev)
        if at is None:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev["type"] == "email_drafted":
            rows["drafted"].append({"ts": _iso(at), "source": _src(ev, d),
                                    "title": str(d.get("subject") or d.get("title") or "A reply")[:240]})
        else:
            named = str(d.get("title") or d.get("meeting_title") or "").strip()
            rows["meetings"].append({"ts": _iso(at), "source": _src(ev, d), "title": (named or "A meeting")[:240]})
            if not named:   # cr1#1668: the page lists such a record by its calendar meeting instead
                rows["meetings"][-1]["untitled"] = True
    kinds: Dict[str, Any] = {}
    for key, words in DONE_KINDS.items():
        if key == "filtered":
            if inbox and inbox["n"] > 0:
                kinds[key] = dict(words, n=inbox["n"], by=inbox["by"], ts=inbox["ts"],
                                  ts_all=[inbox["ts"]], source=inbox["source"], rows=[])
            continue
        found = sorted(rows[key], key=lambda r: r["ts"], reverse=True)
        if found:
            kinds[key] = dict(words, n=len(found), ts_all=[r["ts"] for r in found],
                              rows=found[:DONE_ROW_CAP], n_hidden=max(0, len(found) - DONE_ROW_CAP))
    return {"as_of": _iso(now), "since": _iso(since), "days": days, "kinds": kinds}


def _thread_names(workspace_root) -> Dict[str, str]:
    from entities_io import entities_collection, read_entities_json

    try:
        ent = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return {str(t["id"]): str(t.get("display_name") or t.get("canonical_name") or t.get("name") or "")[:160]
            for t in entities_collection(ent, "projects") if isinstance(t, dict) and t.get("id")}


#: The Work streams tab (cr1#660, rebuilt by cr1#1013 part 2): the order the
#: status groups sort in, and how many rows travel. What counts as live is
#: the thread cards' rule (`thread_cards.DONE_STATUSES`); `PROJECT_DONE` is
#: kept for the Done tab's own reads.
PROJECT_LIVE_ORDER = ("active", "scoping", "paused", "dormant")
PROJECT_DONE = ("archived", "completed", "resolved", "retired", "closed")
PROJECTS_CAP = 200
#: The words hook for the Hub's tabs (cr1#1013 part 2): option D's List view
#: tabs, in order, each with the label it shows. The page's nav is pinned to
#: this list (HUBLIST1). Train 8's per-seat word list (`words.py`) replaces
#: the labels here and nowhere else; the keys are wire names and never change
#: (`projects` is the Work streams tab, whose feed is still `feed/projects`).
#: Since cr1#1285 (owner ruling 2026-11-16) the page shows them in two rows:
#: the small row (Today, Brief, Done, Settings), then the work row. Brief
#: (cr1#1310) sits after Today: the morning's own text, read only. The
#: `week` tab is labelled Done since cr1#1223: it holds what was done this
#: month and what was done for the owner, not the week ahead.
#: Since cr1#1537 (owner ruling 2026-11-21) the page shows six tabs in one
#: row, `HUB_TABS`, and the page's nav is pinned to that list (HUBLIST1). Work
#: holds three views, `HUB_WORK_VIEWS`, each the body its own tab had; Record
#: is the Done tab's body (its feed is still `feed/week`); `week` now names
#: the week board, which sat inside Today. Brief and Settings left the tab
#: row for a button and the gear.
HUB_TABS = (("today", "Today"), ("inbox", "Inbox"), ("week", "Week"), ("people", "People"),
            ("work", "Work"), ("record", "Record"))
#: Since cr1#1540 (owner ruling 2026-11-21) Work opens on Triage and ends on
#: Updates; the three views between them are the old tabs, unchanged.
HUB_WORK_VIEWS = (("triage", "Triage"), ("plate", "Commitments"), ("priorities", "Priorities"),
                  ("projects", "Work streams"), ("updates", "Updates"))
#: What every feed carries as `words.tabs`, unchanged by cr1#1537: the nine
#: wire keys and labels a page published before it reads its tab labels from.
#: No feed changes shape, so an older page keeps its labels against a newer
#: feed. The page built since cr1#1537 reads only the three Work views from
#: it (`plate`, `priorities`, `projects`) and names its six tabs itself.
HUB_WORD_TABS = (("today", "Today"), ("brief", "Brief"), ("week", "Done"), ("settings", "Settings"),
                 ("inbox", "Inbox"), ("plate", "Commitments"), ("priorities", "Priorities"),
                 ("projects", "Work streams"), ("people", "People"))
#: The tabs whose label is a seat's word (cr1#1017 part 1b), by the key in
#: `words.DEFAULTS`; every other tab keeps its label from `HUB_WORD_TABS`.
HUB_TAB_WORDS = {"projects": "work_stream"}


def hub_words(workspace_root) -> Dict[str, Any]:
    """READ (cr1#1017 part 1b, ruling A6). The seat's words as every Hub feed
    carries them under `words`: the word for one piece of work and for a group
    of them, each `{one, many}`, whether the seat set them (`source`, `set`),
    and the tabs in `HUB_WORD_TABS` order with the seat's label on each. The page
    reads the labels from here (train 3); a seat with no list reads the ruled
    defaults, which are `HUB_WORD_TABS` as written. Never raises: a list that will
    not read costs the seat's words for one render, never the feed."""
    import words as _w
    try:
        doc = _w.load_words(workspace_root) if workspace_root else None
    except Exception:  # noqa: BLE001 - the feed renders on the defaults
        doc = None
    if not isinstance(doc, dict):
        doc = {"words": _w._defaults(), "source": "default", "set": []}
    w = doc["words"]
    tabs = [{"key": key, "label": (_w.label(w, HUB_TAB_WORDS[key], many=True, title=True)
                                   if key in HUB_TAB_WORDS else text)}
            for key, text in HUB_WORD_TABS]
    return {"thread": dict(w["thread"]), "work_stream": dict(w["work_stream"]),
            "source": doc.get("source") or "default", "set": list(doc.get("set") or []),
            "tabs": tabs}


def _with_words(workspace_root, feed, seat: Optional[Dict[str, Any]] = None):
    """Every feed's return: the feed with the seat's words on it (`seat`
    when the feed already read them)."""
    if isinstance(feed, dict):
        feed["words"] = seat if seat is not None else hub_words(workspace_root)
    return feed


def _seat_word(seat: Dict[str, Any], key: str, *, many: bool = False) -> str:
    import words as _w
    return _w.label(seat, key, many=many)


#: How many confirmed priorities travel on the page.
PRIORITIES_CAP = 40
#: cr1#1541: how many item ids travel with each of the five that lead.
PRIORITY_ITEMS_CAP = 300


#: Where a card line came from, in a word, by the row's own type when its
#: pointer names no source (cr1#1013 part 4). Words only: the panel never
#: shows a record id, an event id or a pointer.
_CARD_TYPE_SOURCES = {"meeting": "Meeting", "meeting_processed": "Meeting",
                      "email_sent": "Email", "email_received": "Email",
                      "interaction": "Contact", "note": "Note", "memo": "Memo",
                      "insight": "Note", "decision": "Decision",
                      "session_chapter": "Chat"}
#: How many open rows each way the thread panel lists (the card's own cap).
PANEL_OPEN_ROWS = 3
#: How many people from activity the thread panel names.
PANEL_PEOPLE = 5


def _card_source_word(src: Any, etype: Any = None) -> str:
    """One card line's source, in a word: the pointer's scheme when it names
    one, else the row's type. Never the pointer itself (ruling C3: the card
    is for display; the source is where recall goes)."""
    ref = str((src or {}).get("source_ref") or "") if isinstance(src, dict) else ""
    scheme = ref.split(":", 1)[0].lower() if ref else ""
    if scheme in ("session", "chat"):
        return "Chat"
    if scheme == "user":
        return "You"
    if scheme == "granola":
        # A meeting pointer that names no real meeting is the owner's own
        # entry, as Commitments' "Where this came from" reads it (cr1#725).
        from capture_gate import granola_ref_ok
        if not granola_ref_ok(ref):
            return "You"
    return (_ORIGIN_SOURCES.get(scheme) or _CARD_TYPE_SOURCES.get(str(etype or ""))
            or "Captured")


def _card_line(sec: Any, cap: int = 200) -> Optional[Dict[str, Any]]:
    """A card section's text and day, for a row's More."""
    if not isinstance(sec, dict) or not sec.get("text"):
        return None
    return {"text": str(sec["text"])[:cap], "day": sec.get("day") or None}


def _thread_panel(c: Dict[str, Any], mine: List[dict], theirs: List[dict],
                  late_known: bool) -> Dict[str, Any]:
    """The thread detail panel (cr1#1013 part 4): the card, each line with
    where it came from in a word. A section nothing fills is absent (ruling
    A4). Open rows go by title, linked on the page to the Commitments tab
    searched to them. No ids, no money."""
    out: Dict[str, Any] = {"status": str((c.get("status") or {}).get("value") or "active")}
    stands = [{"text": str(ln.get("text") or "")[:200], "day": ln.get("day") or None,
               "from": _card_source_word(ln.get("source"), ln.get("type"))}
              for ln in ((c.get("stands") or {}).get("lines") or []) if ln.get("text")]
    if stands:
        out["stands"] = stands[:3]
    dec = c.get("last_decision")
    if isinstance(dec, dict) and dec.get("text"):
        out["last_decision"] = {"text": str(dec["text"])[:200], "day": dec.get("day") or None,
                                "from": _card_source_word(dec.get("source"), "decision")}
    mv = c.get("last_moved")
    if isinstance(mv, dict) and mv.get("day"):
        out["moved"] = {"day": mv["day"], "from": _card_source_word(mv.get("source"), mv.get("type"))}
    people = [{"name": str(p.get("name") or "")[:120], "n": int(p.get("rows") or 0)}
              for p in ((c.get("people") or {}).get("rows") or []) if p.get("name")]
    if people:
        out["people"] = people[:PANEL_PEOPLE]
    if late_known:
        def lines(rows):
            return [{"title": str(r.get("title") or "")[:160], "due": str(r.get("due") or "")[:10] or None,
                     "overdue": bool(r.get("overdue"))} for r in rows[:PANEL_OPEN_ROWS]]
        if mine:
            out["you_owe"], out["n_you_owe"] = lines(mine), len(mine)
        if theirs:
            out["owed_to_you"], out["n_owed_to_you"] = lines(theirs), len(theirs)
    return out


def _priority_open(root: Path, now_iso: str, confirmed: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """READ. cr1#1294: {priority id: how many live items stand behind it},
    on its own thread, on every thread its objective links (live or not,
    `objective_math._linked_ids`, the objective's own next-move read), and
    on every thread under a linked one (a work stream's own threads). A
    live item is any open row there that is not a heard line: snoozed rows
    and rows waiting for the owner's yes count, so a priority with a step
    already on record is never called empty. None when the open list or the
    thread register cannot be read, and then no priority is called empty.
    Never writes. cr1#1541: the items themselves are `_priority_items`'s,
    so the count and the Week's grouping are one join."""
    items = _priority_items(root, now_iso, confirmed)
    return None if items is None else {pid: len(ids) for pid, ids in items.items()}


def _priority_items(root: Path, now_iso: str, confirmed: List[Dict[str, Any]]) -> Optional[Dict[str, set]]:
    """READ. cr1#1294's join of items to priorities, as ids: {priority id:
    the live items that stand behind it} (`_priority_open` counts them). None
    when the open list or the thread register cannot be read. Never writes."""
    import open_list as ol
    import open_list_index as oli
    from entities_io import entities_collection, read_entities_json
    from objective_math import _linked_ids
    try:
        tzinfo = ol._workspace_tz(root)
        now, _today = ol._clock(root, now_iso, tzinfo)
        threads, list_doc, _from_log = oli._read(root, "threads", oli.BY_THREAD_FILE, now, tzinfo)
        register = [t for t in entities_collection(read_entities_json(root / "_hq" / "data" / "entities.json"),
                                                   "threads") if isinstance(t, dict)]
    except Exception:  # noqa: BLE001 - an unread list never makes a priority look empty
        return None
    reg = {str(t.get("id")): t for t in register if t.get("id")}
    kids: Dict[str, set] = {}
    for t in register:
        if t.get("parent_thread_id") and t.get("id"):
            kids.setdefault(str(t["parent_thread_id"]), set()).add(str(t["id"]))
    by_id = {str(r.get("id")): r for r in list_doc.get("rows") or [] if isinstance(r, dict)}
    live: Dict[str, set] = {}
    for tid, e in (threads or {}).items():
        live[str(tid)] = {str(i) for k in ("you_owe", "owed_to_you", "unowned") for i in (e or {}).get(k) or []
                          if str(i) in by_id and not by_id[str(i)].get("observed")}
    out: Dict[str, set] = {}
    for p in confirmed:
        pid = str(p.get("id") or "")
        obj = (reg.get(pid) or {}).get("objective")
        linked = {pid} | {str(x) for x in p.get("built_from") or []} | {
            str(x) for x in (_linked_ids(obj) if isinstance(obj, dict) else []) if x}
        for t in list(linked):
            linked |= kids.get(t) or set()
        ids: set = set()
        for t in linked:
            ids |= live.get(t) or set()
        out[pid] = ids
    return out


def _goal_lines(workspace_root, now_iso: str) -> Dict[str, Any]:
    """READ. cr1#1294, part b: lines from the owner's goals, for the top
    line. Each confirmed priority with no live item behind it
    (`_priority_open`) becomes a line that asks for its next step, in the
    Priorities tab's own order, marked `personal` when it is on the
    personal lane (`personal_leak.business_thread_ids`). `{"rows", "n"}`; nothing when the open list
    cannot be read. Never writes."""
    import thread_cards as tc
    root = Path(str(workspace_root))
    confirmed = tc.priorities_view(root, now_iso).get("confirmed") or []
    counts = _priority_open(root, now_iso, confirmed)
    if counts is None:
        return {"rows": [], "n": 0}
    from personal_leak import business_thread_ids
    try:
        business = business_thread_ids(root)
    except Exception:  # noqa: BLE001 - an unread register fails closed: every line is personal
        business = frozenset()
    # `personal`: the priority sits on the personal lane, so the morning
    # email holds its words back (the page is the owner's own and shows them)
    rows = [{"id": str(p.get("id") or ""), "statement": str(p.get("statement") or "")[:200],
             "due": p.get("due") or None, "overdue": bool(p.get("overdue")),
             "personal": str(p.get("id") or "") not in business}
            for p in confirmed if counts.get(str(p.get("id") or "")) == 0]
    return {"rows": rows[:GOAL_LINES_CAP], "n": len(rows)}


#: cr1#1459: what the newest action on a priority was, in a word or two, by
#: its row's type. A type not named here falls back to its source in a word
#: (`_card_source_word`), and to "activity" when even that is unknown.
PRIORITY_ACTION_WORDS = {"email_sent": "email sent", "email_received": "email received",
                         "meeting": "meeting", "meeting_processed": "meeting",
                         "interaction": "contact", "note": "note added", "insight": "note added",
                         "memo": "memo", "decision": "decision", "session_chapter": "chat",
                         "thread_updated": "updated", "commitment_resolved": "closed",
                         "reclassification": "item filed", "objective_report": "status report",
                         "objective_review": "review"}
#: A fallback source word that reads as a thing done, not a name.
_PRIORITY_SOURCE_WORDS = {"You": "your own entry", "Captured": "activity"}


def _priority_last_action(p: Dict[str, Any]) -> Dict[str, Any]:
    """cr1#1459: the one line under a priority, as data the page words: the
    newest action on it or its threads (`thread_cards._priority_card`'s
    `last_moved`), `{what, on, title, at}`, names only, never an id or a
    pointer. With nothing related: `{at: None, added}`, the day the
    priority was set when known."""
    mv = p.get("last_moved") if isinstance(p.get("last_moved"), dict) else {}
    if not mv.get("day"):
        return {"at": None, "added": str(p.get("opened") or "")[:10] or None}
    etype = str(mv.get("type") or "")
    what = PRIORITY_ACTION_WORDS.get(etype)
    if not what:
        word = _card_source_word(mv.get("source"), etype)
        what = _PRIORITY_SOURCE_WORDS.get(word) or (word.lower() if word else "activity")
    out: Dict[str, Any] = {"what": what, "at": str(mv["day"])[:10]}
    if mv.get("thread_name"):
        out["on"] = str(mv["thread_name"])[:160]
    if mv.get("title"):
        out["title"] = str(mv["title"])[:160]
    return out


def _priority_owner_names(root: Path) -> Dict[str, str]:
    """cr1#1789: `{priority id: its owner's name}` for each open priority owned
    by someone other than the Hub's owner (`owner_person_id` on its thread,
    `objective_state.list_open_objectives`). Nobody off limits or personal is
    named; with the fence unread nobody is. Names only, never an id."""
    from objective_state import list_open_objectives

    hp = _hidden_people(root)
    if hp.get("unread"):
        return {}
    hidden = set(hp.get("ids") or ())
    owner, names = _owner_id(root), _person_names(root)
    out: Dict[str, str] = {}
    for row in list_open_objectives(root):
        pid = str(row.get("owner_person_id") or "")
        if row.get("thread_id") and pid and pid != owner and pid not in hidden and names.get(pid):
            out[str(row["thread_id"])] = str(names[pid])[:120]
    return out


def _priority_states(root: Path, now_iso: str, conf: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """cr1#1789: sets `state` and `state_words` on each confirmed priority
    (`people_followups.priority_states`, over the `last_action` already on the
    row, on the workspace's own calendar) and `owner` (`{name}`) where it is
    not the Hub's owner, in place. Answers the counts by state, or None when
    the states could not be read (the rows are then left as they were)."""
    try:
        import people_followups

        st = people_followups.priority_states(conf, now_iso=now_iso, tzinfo=_workspace_tz(root))
    except Exception:  # noqa: BLE001 - no state: the page shows none
        return None
    by = {r["id"]: r for r in st.get("rows") or []}
    try:
        owners = _priority_owner_names(root)
    except Exception:  # noqa: BLE001 - no owner named: the Hub's owner is assumed
        owners = {}
    for c in conf:
        r = by.get(c["id"])
        if r:
            c["state"], c["state_words"] = r["state"], r["words"]
        if owners.get(c["id"]):
            c["owner"] = {"name": owners[c["id"]]}
    return dict(st.get("counts") or {})


def _feed_priorities(tc, root: Path, now_iso: str) -> Dict[str, Any]:
    """The Priorities tab: the confirmed priorities and the suggested ones,
    apart (ruling B2), from the priority cards. No money. cr1#1294: each
    confirmed priority says how many live items stand behind it (`n_open`),
    left off when the open list cannot be read."""
    pv = tc.priorities_view(root, now_iso)
    # cr1#1541: the items behind each priority, read once; `n_open` counts them
    behind = _priority_items(root, now_iso, pv.get("confirmed") or [])
    counts = None if behind is None else {pid: len(v) for pid, v in behind.items()}
    conf = [dict({"id": str(p.get("id") or ""), "statement": str(p.get("statement") or "")[:200],
                  "due": p.get("due") or None, "horizon": str(p.get("horizon") or "")[:80] or None,
                  "overdue": bool(p.get("overdue")),
                  "threads": list(p.get("built_from") or []), "says": str(p.get("says") or ""),
                  "still_a_priority": (p.get("still_a_priority") or {}).get("ask"),
                  "last_action": _priority_last_action(p)},
                 **({"n_open": counts.get(str(p.get("id") or ""), 0)} if counts is not None else {}))
            for p in pv.get("confirmed") or []]
    sugg = [{"id": str(p.get("id") or ""), "statement": str(p.get("statement") or "")[:200],
             "company_id": str(p.get("company_id") or ""), "why": str(p.get("why") or ""),
             "threads": list(p.get("built_from") or []),
             "waits_for": str(p.get("waits_for") or "")}
            for p in pv.get("suggested") or []]
    # cr1#1789: each priority's state (moving, quiet this week, no signal),
    # from the `last_action` above and no second reading, and who owns it when
    # that is not the Hub's owner. Fields on a priority row only; the counts
    # are the Priorities bar's one line.
    states = _priority_states(root, now_iso, conf)
    out = {"confirmed": conf[:PRIORITIES_CAP], "n_confirmed": len(conf), "suggested": sugg}
    if states is not None:
        out["states"] = states
    # cr1#1540: the owner's own order for them, and which a star on the Hub
    # made (with the press its Undo names). Optional: the list above keeps
    # the order every other reader has; a page that knows `order` sorts by it.
    try:
        import hub_work
        from events_io import load_events_owner_scoped

        evs = load_events_owner_scoped(str(root))[0]
        ids = [c["id"] for c in conf]
        out["order"] = hub_work.ordered(ids, hub_work.stored_order(evs))[:PRIORITIES_CAP]
        out["ordered"] = bool(hub_work.stored_order(evs))
        out["starred"] = {k: v for k, v in hub_work.starred(evs).items() if k in ids}
        out["max_shown"] = hub_work.PRIORITY_SHOWN
        # cr1#1541: the items tied to each of the five that lead, in his stored
        # order, by the same join `n_open` counts (`_priority_items`), so the
        # Week's Plan panel can put them first. Only with an order he stored.
        if out["ordered"] and behind is not None:
            out["items"] = {pid: sorted(behind.get(pid) or set())[:PRIORITY_ITEMS_CAP]
                            for pid in out["order"][:hub_work.PRIORITY_SHOWN]}
    except Exception:  # noqa: BLE001 - no order: the page shows them as they came
        pass
    return out


#: The "No home yet" section of the Work streams tab (cr1#1013 part 3): how
#: many rows travel. The count travels whole; the head always shows it.
NO_HOME_CAP = 200
#: How each kind of row reads on the page, by where it came from.
NO_HOME_FROM = {"open_list": "open", "observed_tier": "heard"}


def _no_home_source(src: Any) -> Dict[str, str]:
    """A "No home yet" row's source, in a word (C3; the thread panel's rule,
    `_card_source_word`): the pointer's scheme when it names one, else the
    step that wrote the row, else "Captured". Never the pointer, the log row
    or the event id."""
    src = src if isinstance(src, dict) else {}
    word = _card_source_word(src)
    if word == "Captured":
        word = _ORIGIN_SKILLS.get(str(src.get("skill") or "").split(":")[0]) or word
    return {"word": word}


def _feed_no_home(root: Path, now_iso: str) -> Dict[str, Any]:
    """The "No home yet" section (cr1#1013 part 3): every open promise and
    every heard line on no thread, read from the loose-ends list's `unfiled`
    kind (`loose_ends.loose_ends_view`), never a second count of the log.
    Each row says which way it runs and who it is with (never the owner),
    its date and how long it has waited, and keeps its source (C3). The
    section is always present, empty or not, and never narrowed by the
    company filter: an item with no thread has no company. No money."""
    import loose_ends as le
    view = le.loose_ends_view(root, now_iso, kind=le.KIND_UNFILED)
    names = _person_names(root)
    owner = _owner_id(root)
    today = str(view.get("today") or "")
    rows = []
    for r in view.get("rows") or []:
        direction = str(r.get("direction") or "")
        # The other party, never the owner. Only a row that runs one way
        # names a side; a heard line or an unowned row names who it is with.
        if direction == "owed_to_you":
            cands = [r.get("owner_id")]
        elif direction == "you_owe":
            cands = list(r.get("counterparty_ids") or [])
        else:
            cands = [r.get("owner_id")] + list(r.get("counterparty_ids") or [])
        who_id = next((str(c) for c in cands if c and str(c) != owner and names.get(str(c))), "")
        who = names.get(who_id, "")
        due = str(r.get("due") or "")[:10]
        rows.append({
            "id": str(r.get("id") or ""),
            "title": str(r.get("title") or "")[:240],
            "from": NO_HOME_FROM.get(str(r.get("from") or ""), "open"),
            "direction": direction or None,
            "with": who[:120] or None,
            "due": due or None,
            "overdue": bool(due and today and due < today),
            "age_days": r.get("age_days"),
            "waiting_for_yes": "unconfirmed" in (r.get("kinds") or []),
            "source": _no_home_source(r.get("source")),
        })
    return {"rows": rows[:NO_HOME_CAP], "n": len(rows), "n_hidden": max(0, len(rows) - NO_HOME_CAP),
            "job": le.KIND_INFO[le.KIND_UNFILED]["job"],
            "counted_from_log": bool(view.get("counted_from_log"))}


def feed_projects(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The Work streams tab and the Priorities tab (option D's List
    view, cr1#1013 part 2), built only from the now layer: the thread,
    work-stream and priority cards (`thread_cards`) and the open list, never
    re-derived from the log here.

    Each row is one live thread's card: its name, its company and
    `company_id` (the per-company filter key, ruling B3), its status, the day
    it last moved, its open rows both ways and the next step (the open list,
    read live), its main contact worked out from activity (never the owner,
    cr1#981; the hand-typed key contact no longer shows, ruling A4), where it
    stands and its last decision. `work_stream` names the parent thread a row
    sits under; `work_streams` lists each work stream's threads; `companies`
    lists the companies the filter offers; `priorities` carries the
    Priorities tab; `no_home` carries the items on no thread (part 3,
    `_feed_no_home`), present even when the cards cannot be read. Never writes; a missing book answers an empty feed. No
    money anywhere."""
    import thread_cards as tc

    now = _now(now_iso)
    out: Dict[str, Any] = {"generated_at": _iso(now), "rows": [], "n_rows": 0,
                           "n_hidden": 0, "by_status": {}, "work_streams": [],
                           "companies": [], "errors": [],
                           "priorities": {"confirmed": [], "n_confirmed": 0, "suggested": []},
                           "no_home": {"rows": [], "n": 0, "n_hidden": 0}}
    root = Path(str(workspace_root))
    # cr1#1013 part 3: items with no thread are never hidden, so this is read
    # before (and apart from) the cards.
    try:
        out["no_home"] = _feed_no_home(root, _iso(now))
    except Exception:  # noqa: BLE001 - an unread loose-ends list says so, never a guessed empty
        out["errors"].append("no_home")
        out["no_home"] = {"rows": [], "n": None, "n_hidden": 0}
    # cr1#1584: the Tidy section's piles, read apart from the cards too: an
    # optional block an older page never reads.
    try:
        import hub_tidy

        nh = out["no_home"]
        out["tidy"] = hub_tidy.tidy_block(root, _iso(now), None if "no_home" in out["errors"]
                                          else list(nh.get("rows") or []))
    except Exception:  # noqa: BLE001 - an unread pile is offered nowhere, and said so
        out["errors"].append("tidy")
    try:
        cards, from_log = tc.read_cards(root)
    except Exception:  # noqa: BLE001 - no cards answers an empty tab, and says so
        out["errors"].append("cards")
        return _with_words(workspace_root, out)
    late_known = True
    today = now.date()
    try:
        opens, today, _n, list_from_log = tc._open_by_thread(root, _iso(now))
    except Exception:  # noqa: BLE001 - an unread open list leaves the counts off, never guessed
        opens, list_from_log, late_known = {}, True, False
        out["errors"].append("open")
    rows = []
    try:
        from personal_leak import business_thread_ids
        work_threads = set(business_thread_ids(workspace_root))
    except Exception:  # noqa: BLE001 - unread: none is on the work side, so the page offers no cross-line move
        work_threads = set()
    _hp = _hidden_people(workspace_root)  # cr1#1738: one read of who is hidden, for every card
    for tid, c in (cards.get("threads") or {}).items():
        v = opens.get(tid) or {}
        mine, theirs = list(v.get("you_owe") or []), list(v.get("owed_to_you") or [])
        nxt = tc.pick_next(mine, theirs, today)
        c = _card_without_hidden(c, _hp)
        company = c.get("company") or {}
        key = (c.get("people") or {}).get("key_contact") or {}
        rows.append({
            "id": tid,
            "name": str(c.get("name") or tid)[:160],
            "org": str(company.get("name") or "")[:120],
            "company_id": str(c.get("company_id") or ""),
            "status": str((c.get("status") or {}).get("value") or "active"),
            # review round 1 of #1700: which side of the personal line it is on, for Put in...
            "work_lane": tid in work_threads,
            "work_stream": str((c.get("parent") or {}).get("id") or ""),
            "last_activity": (c.get("last_moved") or {}).get("day") or None,
            "open": len(mine) + len(theirs) if late_known else None,
            "you_owe": len(mine), "owed_to_you": len(theirs),
            "overdue": sum(1 for r in mine + theirs if r.get("overdue")) if late_known else None,
            "next": ({"title": str(nxt.get("title") or "")[:160],
                      "due": str(nxt.get("due") or "")[:10] or None,
                      "overdue": bool(nxt.get("overdue")), "direction": nxt.get("direction")}
                     if nxt else None),
            "contact": str(key.get("name") or "")[:120] or None,
            # cr1#1540: who that is, so "Ask for an update" can open their draft
            "contact_id": str(key.get("id") or key.get("person_id") or "")[:80] or None,
            "stands": [{"text": str(ln.get("text") or "")[:200], "day": ln.get("day") or None}
                       for ln in ((c.get("stands") or {}).get("lines") or [])][:tc.STANDS_LINES],
            "last_decision": _card_line(c.get("last_decision")),
            "panel": _thread_panel(c, mine, theirs, late_known),
        })
    rank = {s: i for i, s in enumerate(PROJECT_LIVE_ORDER)}
    rows.sort(key=lambda r: str(r["last_activity"] or ""), reverse=True)
    rows.sort(key=lambda r: rank.get(r["status"], len(rank)))
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    shown = rows[:PROJECTS_CAP]
    seen = {r["id"] for r in shown}
    streams = []
    for wid, w in (cards.get("work_streams") or {}).items():
        if wid not in seen:
            continue
        streams.append({"id": wid, "name": str(w.get("name") or wid)[:160],
                        "company_id": str(w.get("company_id") or ""),
                        "threads": [t for t in (w.get("built_from") or []) if t in seen],
                        "says": str(w.get("says") or "")})
    streams.sort(key=lambda s: (s["name"].lower(), s["id"]))
    companies: Dict[str, Dict[str, Any]] = {}
    for r in shown:
        if r["company_id"]:
            e = companies.setdefault(r["company_id"], {"id": r["company_id"], "name": r["org"], "n": 0})
            e["n"] += 1
    try:
        out["priorities"] = _feed_priorities(tc, root, _iso(now))
    except Exception:  # noqa: BLE001 - an unread priority list leaves that tab empty, and says so
        out["errors"].append("priorities")
    # cr1#1540: the updates the owner posted on his threads, newest first.
    try:
        import hub_work
        from events_io import load_events_owner_scoped

        posted = hub_work.updates(load_events_owner_scoped(str(root))[0], {r["id"]: r["name"] for r in rows})
        out["updates"] = posted[:hub_work.UPDATES_CAP]
        out["n_updates"] = len(posted)
    except Exception:  # noqa: BLE001 - an unread log leaves the list off, and says so
        out["errors"].append("updates")
    out.update({
        "rows": shown, "n_rows": len(rows), "n_hidden": max(0, len(rows) - PROJECTS_CAP),
        "by_status": by_status, "work_streams": streams,
        "companies": sorted(companies.values(), key=lambda e: (e["name"].lower(), e["id"])),
        "counted_from_log": bool(from_log or list_from_log),
    })
    return _with_words(workspace_root, out)


#: The Review tab (cr1#779): at most this many rows are today's, the rest sit
#: under a fold, and this many rows travel on the page.
REVIEW_TODAY = 3
REVIEW_CAP = 60
REVIEW_EVIDENCE_CAP = 240
REVIEW_EMPTY_LINE = "Nothing needs your answer."
REVIEW_CHAT_LINE = "To accept this one, say `staff meeting` in a chat."
REVIEW_HELD_LINE = "Not closed: there is not enough to prove it, so it is on watch."
REVIEW_SURFACE = "cockpit-review"
#: What the Hub answers in code. A proposed close has its whole answer here.
#: Any other queue row can be declined or snoozed, and accepted through the
#: writer of its kind (cr1#818), named in `_REVIEW_ACCEPTS`. A kind with no
#: entry there stays in chat for the reason `REVIEW_CHAT_WHY` gives on its row.
REVIEW_CLOSE_VERBS = ("confirm", "not relevant", "hold")
REVIEW_ROW_VERBS = ("dismiss proposal", "snooze proposal 7d", "proposal not relevant")
REVIEW_ACCEPT_VERBS = ("confirm proposal", "add person", "confirm [type]")
#: cr1#775: Same as on a person row, when the row names one record on file.
REVIEW_SAME_AS_VERB = "same as [existing]"
#: cr1#913: the board-versus-owner question's answers, chat's own verbs: give
#: the row to the person the board names (`theirs to [name]`, or `mine` when
#: the board names the owner), or keep the owner's choice (the queue's
#: decline, worded as a keep, as an amend's No is "Keep the rule").
OWNER_Q_GIVE_VERB = "theirs to [name]"
OWNER_Q_MINE_VERB = "mine"
OWNER_Q_KEEP_VERB = "dismiss proposal"
REVIEW_VERBS = frozenset(REVIEW_CLOSE_VERBS + REVIEW_ROW_VERBS + REVIEW_ACCEPT_VERBS + (REVIEW_SAME_AS_VERB,)
                         + (OWNER_Q_GIVE_VERB, OWNER_Q_MINE_VERB))
#: A yes the row's kind words for itself.
REVIEW_YES_LABELS = {"learned_rule": "Yes", "archived_activity": "Bring it back",
                     "craft_amend": "Use the new text", "craft_rule": "Add the rule",
                     "craft_kind": "Add this kind of work", "craft_retire": "Retire it",
                     "past_ruling": "Add to decisions"}
#: cr1#401: a No on an amend keeps the rule, which is the owner confirming it.
REVIEW_NO_LABELS = {"craft_amend": "Keep the rule", "craft_retire": "Keep it"}
#: cr1#400: at most this many craft questions (new rules and amends) on the
#: tab at a time; the rest wait until these are answered or skipped.
#: cr1#1391 part B: a new kind of work and a retirement offer count too.
REVIEW_CRAFT_KINDS = ("craft_rule", "craft_amend", "craft_kind", "craft_retire")
#: Why an accept is not on the row. The rest of the row still answers here.
REVIEW_CHAT_WHY = {
    "project": "To accept this one, say `staff meeting` in a chat: a new project needs its folder and a first read of your connectors.",
    "person_merge": "To accept this one, say `staff meeting` in a chat: merging two records cannot be undone.",
    "thread_split": "To accept this one, say `staff meeting` in a chat: a split moves past records between threads.",
    "person_link": "To accept this one, say `staff meeting` in a chat: it names nobody on file to link to.",
}
REVIEW_PERSON_ON_FILE_LINE = "To accept this one, say `staff meeting` in a chat: it changes a record already on file."
REVIEW_PERSON_LIKE_LINE = "To accept this one, say `staff meeting` in a chat: someone with a name like this is on file, so it is add or same as."
REVIEW_PERSON_MANY_LINE = "To say who this is, say `staff meeting` in a chat: more than one person with a name like this is on file, and the page never picks one."
REVIEW_SAME_AS_GONE_LINE = "That row names nobody on file to link to; nothing was written."
#: cr1#1222: on an identity row ("is this that person?") the owner can say who
#: it really is. The registered `same as [existing]` verb with a typed name,
#: offered by the Hub on `person_link` rows; any note follows a colon.
REVIEW_SOMEONE_ELSE_LABEL = "Someone else"
REVIEW_SOMEONE_ELSE_ASK = "Who it is, as on file (a note can follow a colon)"
REVIEW_SOMEONE_NONE_LINE = ("Nobody on file is called {name}, so nothing was written. To add a new "
                            "person, say `staff meeting` in a chat.")
REVIEW_SOMEONE_MANY_LINE = ("More than one person on file is called {name}, so nothing was written. "
                            "Say who it is in a chat with `staff meeting`.")
REVIEW_SOMEONE_SAME_LINE = "That is the person it asked about; press Yes for that. Nothing was written."
REVIEW_SOMEONE_NO_SIGHTINGS_LINE = ("This one has no sightings to move, so nothing was written. Say who "
                                    "it is in a chat with `staff meeting`.")
#: The keys the writers read, off the proposal event, onto the row.
REVIEW_ROW_KEYS = ("proposed_money", "fact", "category", "proposed_role", "to_org_id",
                   "objective_id", "source_event_seq",
                   # cr1#401: an amend shows the old and the new text side by side
                   "old_text", "new_text", "craft_question", "craft_contradictions",
                   "craft_confidence", "craft_pending")
#: What only the writers read. It stays off the page.
REVIEW_WRITER_KEYS = REVIEW_ROW_KEYS + ("name", "source_ref", "skill", "knob", "target_id")
#: A row a press no longer finds in the queue, and the typed value a row lacks.
REVIEW_GONE_LINE = "That one was already answered; nothing was written."
REVIEW_NEED_TEXT_LINE = "That one needs the value typed on its row; nothing was written."
REVIEW_BAD_AMOUNT_LINE = "That amount is not a number; nothing was written."
REVIEW_DECLINE_DAYS = 60
#: How long a proposed close stays on the review list (`cru_match.
#: load_open_review_proposals`'s window); then only the question lapses.
REVIEW_CLOSE_WINDOW_DAYS = 7
REVIEW_SNOOZE_DAYS = 7
_DEFAULT_WORDS = {"apply_likely": "the likely answer applies", "let_go": "it is let go",
                  "track_it": "it stays on your plate, just not as a question",
                  "retract": "the offer ends and nothing changes"}

#: cr1#913 (owner ruling 2026-11-07): when the board keeps naming someone
#: other than the owner's own choice, the one question of cr1#901 waits under
#: To confirm, answerable there. It is worked out from the book on every
#: read and is never a queue row: nothing pending is put on the commitment,
#: so the board keeps following it and review expiry never drops it (why
#: PR #905 kept it off the confirm lane).
OWNER_Q_KIND = "board_owner"
OWNER_Q_CAP = 20
OWNER_Q_LINE = "On the board: {board}. Your choice: {chosen}."
OWNER_Q_GIVE_LABEL = "Give it to {board}"
OWNER_Q_MINE_LABEL = "Take it back"
OWNER_Q_KEEP_LABEL = "Keep it with {chosen}"
OWNER_Q_NO_UNDO_LINE = ("Once applied, giving it to {board} cannot be taken back here; "
                        "to change it again, reassign it in a chat.")
OWNER_Q_MINE_NO_UNDO_LINE = ("Once applied, taking it back cannot be undone here; "
                             "to change it again, reassign it in a chat.")
OWNER_Q_KEPT_LINE = "Kept with {chosen}. Nothing changed in your book."
OWNER_Q_GONE_LINE = "That question is no longer open; nothing was written."
OWNER_Q_NOT_YOURS = "nobody (you said it is not yours)"


def board_owner_questions(workspace_root) -> Optional[List[Dict[str, Any]]]:
    """READ. cr1#913: the open board-versus-owner questions
    (`tasks_follow.held_owner_questions`, which holds the rule, the fence and
    the fail-closed cases) in the page's words. Each answers `{id,
    commitment_id, board_id, title, board, chosen, line, no_undo, verbs, seq,
    thread_id}`. The id carries the board's value, so a board that names yet
    another person asks again (cr1#901, asked once per value), and the seq
    of the owner's newest hand act on the item, so any later act (in chat or
    here) makes an earlier press "no longer open". A question the Hub's own
    audit rows record as kept (an `apply_choices_applied` action, the keep
    verb, recorded ok) is not asked again. None when it cannot be told."""
    try:
        import tasks_follow as tf

        held = tf.held_owner_questions(workspace_root)
        me = _owner_id(workspace_root) if held else ""
        kept = _owner_q_kept(workspace_root) if held else set()
    except Exception:  # noqa: BLE001 - fail closed: no question
        return None
    if held is None or kept is None:
        return None
    out: List[Dict[str, Any]] = []
    for h in held:
        cid, bid, cur, row = h["commitment_id"], h["board_id"], h["owner_id"], h["row"]
        qid = f"{OWNER_Q_KIND}:{cid}:{bid}:{h.get('act_seq')}"
        if qid in kept:
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        said = tf._said_name(workspace_root, bid, "", me)
        chosen = tf._said_name(workspace_root, cur, "", me) if cur else OWNER_Q_NOT_YOURS
        give = OWNER_Q_MINE_VERB if bid == me else OWNER_Q_GIVE_VERB
        out.append({
            "id": qid, "commitment_id": cid, "board_id": bid,
            "title": str(data.get("title") or data.get("summary") or "")[:240],
            "board": said, "chosen": chosen,
            "line": OWNER_Q_LINE.format(board=said, chosen=chosen),
            "no_undo": OWNER_Q_MINE_NO_UNDO_LINE if give == OWNER_Q_MINE_VERB else OWNER_Q_NO_UNDO_LINE.format(board=said),
            "verbs": [{"action": OWNER_Q_KEEP_VERB, "label": OWNER_Q_KEEP_LABEL.format(chosen=chosen)},
                      {"action": give, "label": OWNER_Q_MINE_LABEL if give == OWNER_Q_MINE_VERB
                       else OWNER_Q_GIVE_LABEL.format(board=said)}],
            "seq": row.get("seq"), "thread_id": row.get("primary_thread_id") or None,
        })
    return out[:OWNER_Q_CAP]


def _owner_q_kept(workspace_root) -> Optional[set]:
    """cr1#913: the board-versus-owner question ids the Hub's own Apply runs
    recorded as kept: an `apply_choices_applied` row of the review surface
    whose action is the keep verb on a question id, recorded ok. No new
    writer: the audit row every Apply run writes is the record. None when
    the book cannot be read."""
    try:
        from events_io import iter_events_org_scoped

        out = set()
        for ev in iter_events_org_scoped(workspace_root):
            d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
            if ev.get("type") != "apply_choices_applied" or d.get("surface") != REVIEW_SURFACE:
                continue
            for a in d.get("actions") or []:
                n = str((a or {}).get("n") or "")
                if (n.startswith(OWNER_Q_KIND + ":") and (a or {}).get("action") == OWNER_Q_KEEP_VERB
                        and (a or {}).get("outcome") == "ok"):
                    out.add(n)
        return out
    except Exception:  # noqa: BLE001 - fail closed: no question
        return None


def _review_weight(item: Dict[str, Any], *, reason: Optional[str] = None) -> int:
    """Most consequential first (cr1#779): money, then a client, then a
    deadline, then the rest."""
    import question_ttl as qt

    if item.get("shape") == "money" or reason == qt.IMPORTANCE_MONEY:
        return 0
    if reason == qt.IMPORTANCE_CLIENT:
        return 1
    if reason in (qt.IMPORTANCE_OVERDUE, qt.IMPORTANCE_DUE_SOON):
        return 2
    return 3


def _newest_open_proposal_seq(workspace_root, commitment_id) -> Optional[int]:
    """cr1#1499: the seq of the item's ONE open close question, so "not
    relevant" on a row with no seq names the question it answers. With
    several open (or none, or an unreadable book) it answers None, and the
    dismissal is written with no seq: read in order, that closes every
    question on the item written before it, which is what a press on the
    item means. Naming only the newest would leave the rest asking."""
    try:
        import commitment_policy as _policy
        ledger = _policy.fold_proposals(_review_events(workspace_root))
        open_seqs = [s for s in ledger["open"]
                     if ledger["by_seq"][s]["commitment_id"] == str(commitment_id)]
        if len(open_seqs) != 1:
            return None
        return int(open_seqs[0])
    except Exception:  # noqa: BLE001 - the dismissal still lands
        return None


def _review_events(workspace_root) -> List[Dict[str, Any]]:
    from events_io import load_events_org_scoped

    try:
        return load_events_org_scoped(str(workspace_root))[0]
    except Exception:  # noqa: BLE001 - no log reads as nothing yet
        return []


def _review_rates(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """Accepted and reversed proposed closes per kind, from the one audit
    event each batch writes (`note` reads `review <kind> <accepted|reversed>`).
    An accepted close the owner later reopened counts as reversed. A held or
    failed answer settled nothing, so its note is neither."""
    reopened = set()
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "commitment_reopened":
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            reopened.add(str(d.get("commitment_id") or d.get("id") or ""))
    out: Dict[str, Dict[str, int]] = {}
    for ev in events:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if ev.get("type") != "apply_choices_applied" or d.get("surface") != REVIEW_SURFACE:
            continue
        for a in d.get("actions") or []:
            bits = str((a or {}).get("note") or "").split()
            if len(bits) != 3 or bits[0] != "review" or bits[2] not in ("accepted", "reversed"):
                continue
            how = bits[2]
            if how == "accepted" and str((a or {}).get("n") or "").split(":", 1)[-1] in reopened:
                how = "reversed"
            kind = out.setdefault(bits[1], {"accepted": 0, "reversed": 0})
            kind[how] += 1
    return out


def _review_same_as(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The populated Same as on a person row (cr1#775): the one record on file
    the adapter matched, with its id and name. None when the row has no such
    verb, or carries it unpopulated because the name collides with two or more
    records (IDM1): then the page never picks one, and chat does."""
    if str(item.get("kind") or "") != "person":
        return None
    for tup in item.get("action_tuples") or []:
        if isinstance(tup, dict) and tup.get("action") == REVIEW_SAME_AS_VERB:
            return tup if tup.get("person_id") else None
    return None


def _review_same_as_unpopulated(item: Dict[str, Any]) -> bool:
    return any(isinstance(tup, dict) and tup.get("action") == REVIEW_SAME_AS_VERB and not tup.get("person_id")
               for tup in item.get("action_tuples") or []) if str(item.get("kind") or "") == "person" else False


def _review_verbs(item: Dict[str, Any]) -> List[str]:
    """The verbs this row can be answered with on the Hub, in the row's order."""
    import brain_proposals as bp

    kind = str(item.get("kind") or "")
    own = bp._row_actions(item)
    if kind == "commitment_review":
        return [v for v in own if v in REVIEW_CLOSE_VERBS]
    keep = REVIEW_ROW_VERBS + (REVIEW_ACCEPT_VERBS if _review_accepts(item) else ())
    if _review_same_as(item):
        keep += (REVIEW_SAME_AS_VERB,)
    if str(item.get("id") or "").split(":", 1)[0] in _REVIEW_LEGACY_DECLINES:
        keep += ("not relevant",)
    out = [v for v in own if v in keep]
    if _review_someone_else(item) and REVIEW_SAME_AS_VERB not in out:
        out.append(REVIEW_SAME_AS_VERB)
    return out


def _review_someone_else(item: Dict[str, Any]) -> bool:
    """cr1#1222: an identity row whose sightings can be pointed at the right
    person: a `person_link` question with a spelling and sightings behind it."""
    return (str(item.get("kind") or "") == "person_link" and bool(str(item.get("alias_name") or "").strip())
            and bool(item.get("cluster_seqs") or item.get("cluster_fingerprints")))


def _review_detail(item: Dict[str, Any], by_seq: Dict[Any, Dict[str, Any]]) -> Dict[str, Any]:
    """The queue row plus what its writer reads off the proposal event: the
    projector carries ids, not `fact` or `proposed_money`. The row's own keys win."""
    src = by_seq.get(item.get("seq")) or {} if item.get("seq") is not None else {}
    data = src.get("data") if isinstance(src.get("data"), dict) else {}
    extra = {k: data[k] for k in REVIEW_WRITER_KEYS if data.get(k) is not None}
    return {**extra, **{k: v for k, v in item.items() if v not in (None, "", [], {})}}


def _review_is_add(item: Dict[str, Any]) -> bool:
    """An add row is a cluster of sightings; an update row never is, even with
    no `person_id` to attach to."""
    return not item.get("person_id") and bool(item.get("cluster_seqs") or item.get("cluster_fingerprints"))


def _review_own_verbs(item: Dict[str, Any]) -> List[str]:
    import brain_proposals as bp

    return bp._row_actions(item)


def _review_accepts(item: Dict[str, Any]) -> bool:
    """Whether this row's yes can run here: its kind has a writer, and the row
    holds what that writer needs (a typed value can supply a role, never a target)."""
    kind = str(item.get("kind") or "")
    if kind not in _REVIEW_ACCEPTS:
        return False
    if kind == "person":  # an add: an update row, or a name that may be on file, is a choice made in chat
        # (cr1#818 kept Add in chat for a row with a Same as; cr1#775 adds Same as itself when one
        # record matched, and keeps Add in chat there: the Add writer's gate is an exact-name check,
        # so one press on a first name with a fuller record on file would make a second record)
        return (bool(item.get("name")) and _review_is_add(item)
                and not any(str(v).startswith("same as") for v in _review_own_verbs(item)))
    if kind == "person_link":  # nobody matched yet: there is no one to link to
        return bool(item.get("person_id"))
    if kind == "person_update":
        how = item.get("proposal_kind")
        return bool(item.get("person_id")) and (how == "role_change" or (how == "org_change" and bool(item.get("to_org_id"))))
    if kind == "entity_fact":
        return bool(item.get("person_id") or item.get("org_id"))
    if kind == "org_money":
        return bool(item.get("org_id") and item.get("proposed_money"))
    return True


def _review_ask(item: Dict[str, Any], verb: str) -> Dict[str, Any]:
    """What a verb asks on the row. `required` when its writer has nothing without it."""
    kind = str(item.get("kind") or "")
    if verb == REVIEW_SAME_AS_VERB and kind == "person_link":
        return {"ask": REVIEW_SOMEONE_ELSE_ASK, "required": True}
    if verb not in REVIEW_ACCEPT_VERBS or verb == "add person":
        return {}
    if kind == "org_money":
        return {"ask": "A different amount, if this one is wrong"}
    if kind == "entity_fact":
        return {"ask": "Different wording, if this is not right", "required": not item.get("fact")}
    if kind == "org":
        return {"ask": "A different name, if this one is wrong"}
    if kind in ("craft_rule", "craft_kind"):  # cr1#400, cr1#1391: confirm, edit or dismiss
        return {"ask": "Your own words for the rule, if these are not right"}
    if kind == "past_ruling":  # cr1#1073: the owner's wording replaces the file's
        return {"ask": "Your own words for the ruling, if these are not right"}
    if kind == "person_update" and item.get("proposal_kind") == "role_change":
        return {"ask": "The role" if not item.get("proposed_role") else "A different role, if this one is wrong",
                "required": not item.get("proposed_role")}
    return {}


def _review_chat_line(item: Dict[str, Any], verbs: List[str]) -> str:
    kind = str(item.get("kind") or "")
    if kind == "commitment_review" or set(verbs) & set(REVIEW_ACCEPT_VERBS):
        return ""
    if kind == "person_rename":  # with an empty record to fold in, its verb is a merge
        return REVIEW_CHAT_WHY["person_merge"]
    if kind == "person" and not _review_is_add(item) and "add person" in _review_own_verbs(item):
        return REVIEW_PERSON_ON_FILE_LINE
    if kind == "person" and _review_same_as_unpopulated(item):
        return REVIEW_PERSON_MANY_LINE
    if kind == "person" and "add person" in _review_own_verbs(item):
        return REVIEW_PERSON_LIKE_LINE
    return REVIEW_CHAT_WHY.get(kind, REVIEW_CHAT_LINE)


def feed_review(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The Review tab (cr1#779): the open queue the Staff Meeting reads
    (`brain_proposals.load_open_proposals`), most consequential first, each row
    with its evidence, the verbs the Hub can answer it with, when it expires
    and what then truly happens. The first `REVIEW_TODAY` are today's. Never
    writes; an answer goes back through `apply_review_choices`. Answers
    `{generated_at, rows, n_open, n_hidden, line, accept_rates}`."""
    import brain_proposals as bp
    import question_ttl as qt
    from verb_taxonomy import display_label

    now = _now(now_iso)
    iso_now = _iso(now)
    try:
        items = bp.rank_proposals(bp.load_open_proposals(str(workspace_root), "staff-meeting", now_iso=iso_now))
    except Exception:  # noqa: BLE001 - an unreadable queue is said, never shown as empty
        return _with_words(workspace_root, {"generated_at": iso_now, "rows": [], "n_open": 0, "n_hidden": 0, "accept_rates": {},
                "line": "", "errors": ["review"]})
    events = _review_events(workspace_root)
    # The open projection, never the capture event: a due date moved since
    # capture is the one that counts (the reducer owns truth).
    behind: Dict[str, Dict[str, Any]] = {}
    try:
        from cru_match import _commitment_id, load_open_commitments

        ep = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
        for row in (load_open_commitments(ep) if ep.exists() else []):
            behind[str(_commitment_id(row))] = row
    except Exception:  # noqa: BLE001 - the rows then weigh what they carry
        behind = {}
    stamped = {ev.get("seq"): (ev.get("data") or {}).get("ttl_days") for ev in events
               if isinstance(ev, dict) and ev.get("type") == "commitment_review_proposed"}
    ids = qt._client_ids(workspace_root)
    by_seq = {ev.get("seq"): ev for ev in events if isinstance(ev, dict)}
    weighed = []
    for it in items:
        if it.get("digest_class") or str(it.get("id") or "").startswith("digest:"):
            continue
        # A proposed close weighs what its commitment weighs (the review row
        # carries no due date and no counterparty), and what its own words say.
        reason = None
        if it.get("commitment_id") and str(it["commitment_id"]) in behind:
            reason = qt.importance_hold(behind[str(it["commitment_id"])], now=now, client_ids=ids, workspace_root=workspace_root)
        reason = reason or qt.importance_hold(it, now=now, client_ids=ids, workspace_root=workspace_root)
        weighed.append((_review_weight(it, reason=reason), reason, it))
    # Weight first, and the queue's own order within a weight, except that the
    # people and project questions sit together by the read they came from,
    # people before the project questions of that read (cr1#775). Every other
    # kind keeps its place ahead of them within the weight.
    def _grouping(it: Dict[str, Any]):
        kind = str(it.get("kind") or "")
        if kind not in ("person", "project"):
            return (0, "", 0)
        return (1, str((by_seq.get(it.get("seq")) or {}).get("source_skill") or it.get("detector") or ""), 0 if kind == "person" else 1)
    weighed.sort(key=lambda w: (w[0],) + _grouping(w[2]))
    try:
        from craft_sweep import MAX_ON_REVIEW as _craft_cap
    except Exception:  # noqa: BLE001 - the owner's ruling, five per card
        _craft_cap = 5
    _n_craft, _kept = 0, []
    for w in weighed:
        if str(w[2].get("kind") or "") in REVIEW_CRAFT_KINDS:
            _n_craft += 1
            if _n_craft > _craft_cap:
                continue
        _kept.append(w)
    n_craft_waiting = max(0, _n_craft - _craft_cap)
    weighed = _kept
    rows: List[Dict[str, Any]] = []
    for n, (_w, reason, it) in enumerate(weighed[:REVIEW_CAP]):
        kind = str(it.get("kind") or "")
        opened = _parse(it.get("last_activity_at") or it.get("opened_at")) or now
        if kind == "commitment_review":
            ttl = stamped.get(it.get("seq"))
            days = min(int(ttl), REVIEW_CLOSE_WINDOW_DAYS) if isinstance(ttl, int) and ttl > 0 else REVIEW_CLOSE_WINDOW_DAYS
            ends = opened + _dt.timedelta(days=days)
            default_line = "this question lapses and the commitment stays open"
        else:
            klass = qt.question_class_of(it) or qt.CLASS_HYGIENE
            ends = opened + _dt.timedelta(days=qt.class_lifetime_days(klass, workspace_root))
            own = _parse(it.get("expires_at"))
            ends = min(ends, own) if own else ends  # the row leaves at the earlier
            default_line = ("it drops off this list" if reason else
                            _DEFAULT_WORDS.get(qt.class_spec(klass)["default"], "it settles on its own evidence"))
        full = _review_detail(it, by_seq)
        answers = _review_verbs(full)
        same_as = _review_same_as(full)
        verbs = [{"action": v,
                  "label": (REVIEW_YES_LABELS.get(kind, display_label(v) or v) if v == "confirm proposal"
                            else (REVIEW_SOMEONE_ELSE_LABEL if v == REVIEW_SAME_AS_VERB and kind == "person_link"
                            else (("Same as " + str(same_as.get("value") or "").strip()).strip() if v == REVIEW_SAME_AS_VERB and same_as
                                  else (REVIEW_NO_LABELS.get(kind, display_label(v) or v) if v == "dismiss proposal"
                                        else (display_label(v) or v))))),
                  **{k: w for k, w in _review_ask(full, v).items() if w}} for v in answers]
        # cr1#775: where the question came from, so a project question sits
        # beside the person questions from the same read.
        board = str((by_seq.get(it.get("seq")) or {}).get("source_skill") or it.get("detector") or "")
        rows.append({
            "id": it.get("id"), "kind": kind, "shape": it.get("shape") or "hygiene",
            "group": "close" if kind == "commitment_review" else ("rule" if "rule" in kind else "question"),
            "title": str(bp._row_name(it))[:240], "line": str(it.get("render_line") or "")[:300],
            "evidence": str(it.get("evidence") or "")[:REVIEW_EVIDENCE_CAP],
            "reason": reason, "commitment_id": it.get("commitment_id"), "seq": it.get("seq"),
            "thread_id": it.get("thread_id"), "verbs": verbs,
            "chat_line": _review_chat_line(full, answers),
            "expires_on": ends.date().isoformat() if ends else None, "default_line": default_line,
            "today": n < REVIEW_TODAY, "board": board or None,
            "data": {**bp._row_target_ids(it), **{k: full[k] for k in REVIEW_ROW_KEYS if k in full}},
        })
    # cr1#401: stale and never-tested craft files, and corrections attached to a
    # rule. A list with the phrase that acts on each, never a question.
    try:
        import craft_adjust

        craft = craft_adjust.review_list(workspace_root, now_iso=iso_now)
    except Exception:  # noqa: BLE001 - the questions still show
        craft = {"stale": [], "untested": [], "evidence": [], "errors": ["craft"]}
    # cr1#913: the board-versus-owner questions, a field of their own (an
    # older page ignores it). Unreadable is none (fail closed).
    owner_qs = board_owner_questions(workspace_root) or []
    return _with_words(workspace_root, {"generated_at": iso_now, "rows": rows, "n_open": len(weighed) + n_craft_waiting,
            "n_hidden": max(0, len(weighed) - REVIEW_CAP) + n_craft_waiting,
            "line": "" if rows else REVIEW_EMPTY_LINE, "accept_rates": _review_rates(events),
            "craft": craft, "owner_questions": owner_qs})


def _review_close(workspace_root, c: Dict[str, Any], actor: str, now) -> Dict[str, Any]:
    """One Confirm on a proposed close, through the shared fence, the one way
    a review row closes. Nothing is named individually: a press on the Hub is
    not the typed number or name that may override the fence, so a weak row is
    parked and closes nothing."""
    from watch_gate import confirm_review_rows

    row = dict(c.get("payload") or {}, id=c.get("n"))
    res = confirm_review_rows(str(workspace_root), [row], resolved_by=actor,
                              now_iso=_iso(now), source_skill=COMMITMENT_SRC)
    return res


def _review_tombstones(events_path, row: Dict[str, Any], resolution: str, *, batch_id: Optional[str] = None,
                       **kw) -> int:
    """One `person_proposal_resolved` per sighting behind a person row: the
    cluster's seqs and seq-less fingerprints, else the row's own seq.
    `batch_id` (cr1#1222) stamps them `person_proposal_tombstone` under one
    batch, so one `undo` reopens every sighting."""
    from confirm_flow import build_person_proposal_resolved_event as build
    from event_gate import append_event

    seqs = list(row.get("cluster_seqs") or [])
    prints = list(row.get("cluster_fingerprints") or [])
    if not seqs and not prints and isinstance(row.get("seq"), int) and not str(row.get("id")).startswith("bp_"):
        seqs = [row["seq"]]
    built = ([build(seq, resolution=resolution, source_skill=COMMITMENT_SRC, **kw) for seq in seqs]
             + [build(None, resolution=resolution, source_skill=COMMITMENT_SRC, proposal_fingerprint=fp, **kw)
                for fp in prints])
    for ev in built:
        if batch_id:
            ev["data"]["brain_batch_id"] = batch_id
            ev["data"]["brain_change_class"] = "person_proposal_tombstone"
        append_event(events_path, ev, holder="cockpit_helpers")
    return len(built)


def _accept_person(ws, row, text, actor, events_path, now):
    """`add person`: the same-name gate runs inside the writer; a shared name
    writes nothing and is answered in chat, where the choice can be made. An
    address is kept only when the sightings tie it to this person (cr1#769)."""
    from confirm_flow import load_open_person_proposals
    from identity_reconcile import cluster_observed_email
    from org_writer import attribute_person_to_org
    from people_writer import auto_add_person, get_person_emails

    seqs = {q for q in (row.get("cluster_seqs") or [row.get("seq")]) if q is not None}
    prints = set(row.get("cluster_fingerprints") or [])
    sightings = [p for p in load_open_person_proposals(events_path)
                 if p.get("seq") in seqs or (p.get("fingerprint") and p.get("fingerprint") in prints)]
    email, seen = cluster_observed_email({"name": row.get("name"), "add_rows": sightings})
    ref = (str((seen or {}).get("source_ref") or "") or f"proposal:{row.get('seq')}") if email else None
    captured = [e.get("data") for e in _review_events(ws)
                if isinstance(e, dict) and e.get("seq") in seqs and isinstance(e.get("data"), dict)]
    hint = next((d.get("org_hint") for d in captured if d.get("org_hint")), None)
    role = {"role": row["inferred_role"]} if row.get("inferred_role") else {}
    res = auto_add_person(ws, canonical_name=str(row.get("name") or ""), email=email,
                          email_provenance=ref, source_skill=COMMITMENT_SRC, **role)
    if res.get("status") != "added":
        return {"status": res.get("status")}, "refused", REVIEW_CHAT_LINE
    record = res.get("record") or {}
    _review_tombstones(events_path, row, "person_added", person_id=record.get("id"))
    try:  # a missing org link is a degradation, never a failed add
        domains = [e.rsplit("@", 1)[1].strip().lower() for e in get_person_emails(record) if "@" in e]
        attribute_person_to_org(ws, record["id"], work_domains=domains, org_hint=hint, source_skill=COMMITMENT_SRC)
    except Exception:  # noqa: BLE001
        pass
    return {"status": "added", "person_id": record.get("id")}, "applied", "Added."


def _accept_person_same_as(ws, row, text, actor, events_path, now):
    """Same as on a person row (cr1#775): the sightings are the one record on
    file the row named. The spelling becomes that record's alias unless it is
    the record's own name or another record's; the sightings close as that
    person, the way the chat's own handler does. An alias, never a merge."""
    from people_writer import add_person_alias, another_record_named, list_same_name_people

    tup = _review_same_as(row)
    if not tup:
        return {"status": "no_match"}, "refused", (REVIEW_PERSON_MANY_LINE if _review_same_as_unpopulated(row)
                                                   else REVIEW_SAME_AS_GONE_LINE)
    pid, alias = str(tup.get("person_id") or ""), str(row.get("name") or "")
    own = any(p.get("id") == pid and str(p.get("canonical_name") or "").strip().lower() == alias.strip().lower()
              for p in list_same_name_people(ws, alias))
    write = bool(alias) and not own and not another_record_named(ws, pid, alias)
    if write:
        add_person_alias(ws, pid, alias, source_skill=COMMITMENT_SRC, confirmed=True)
    _review_tombstones(events_path, row, "same_as", person_id=pid, alias=alias if write else None)
    return {"status": "linked", "person_id": pid}, "applied", "Same person; linked."


def _accept_person_link(ws, row, text, actor, events_path, now):
    """An alias, never a merge. A spelling that is another record's own name is
    not written; the sightings still close as that person."""
    from people_writer import add_person_alias, another_record_named, list_same_name_people

    pid, alias = str(row.get("person_id") or ""), str(row.get("alias_name") or "")
    own = any(p.get("id") == pid and str(p.get("canonical_name") or "").strip().lower() == alias.strip().lower()
              for p in list_same_name_people(ws, alias))
    write = alias and not own and not another_record_named(ws, pid, alias)
    if write:
        add_person_alias(ws, pid, alias, source_skill=COMMITMENT_SRC)
    _review_tombstones(events_path, row, "same_as", person_id=pid, alias=alias if write else None)
    return {"status": "linked"}, "applied", "Linked."


#: Records a typed name never resolves to: gone, folded into another, or put away.
_SOMEONE_DEAD_STATUSES = ("archived", "merged", "deleted")


def _someone_on_file(ws, name: str) -> List[Dict[str, Any]]:
    """Every live record whose own name, alias or nickname is exactly `name`,
    read over the whole people list (no candidate cap, so a second match is
    never cut off)."""
    from entities_io import entities_collection
    from people_writer import _load_entities, _normalize_name, get_person_display_names

    want = _normalize_name(name)
    if not want:
        return []
    return [p for p in entities_collection(_load_entities(Path(str(ws))), "people")
            if isinstance(p, dict) and p.get("id") and p.get("status") not in _SOMEONE_DEAD_STATUSES
            and any(_normalize_name(s) == want for s in get_person_display_names(p))]


def _person_link_someone_else(ws, row, text, actor, events_path, now):
    """cr1#1222: No, it is someone else. `text` names the right person as on
    file, with any note after a colon. The sightings close as THAT person
    (`same_as` tombstones under one Hub batch, so one `undo` reopens them);
    no alias is written, so no spelling is re-pointed for good; the question
    closes as declined, so the same pair is not asked again; the note is a
    fact on the person it names (`people_writer.record_person_fact`). A name
    that is nobody's, or two people's, or the person the row asked about,
    writes nothing.

    Order: every check, then the decline (a question already answered stops
    here, nothing written), then the sightings, then the note. An `undo` of
    the batch reopens the sightings; the No to the person it asked about and
    the note stay, as a No pressed alone and a fact said in chat would."""
    import brain_proposals as bp
    from people_writer import record_person_fact

    name, _sep, note = str(text or "").partition(":")
    name, note = " ".join(name.split()), " ".join(note.split())[:500]
    if not name:
        return {"status": "needs_input"}, "refused", REVIEW_NEED_TEXT_LINE
    if not _review_someone_else(row) or not str(row.get("id") or "").startswith("bp_"):
        return {"status": "no_sightings"}, "refused", REVIEW_SOMEONE_NO_SIGHTINGS_LINE
    found = _someone_on_file(ws, name)
    if not found:
        return {"status": "no_match"}, "refused", REVIEW_SOMEONE_NONE_LINE.format(name=name)
    if len(found) > 1:
        return {"status": "ambiguous"}, "refused", REVIEW_SOMEONE_MANY_LINE.format(name=name)
    person = found[0]
    pid = str(person.get("id") or "")
    if pid == str(row.get("person_id") or ""):
        return {"status": "same_person"}, "refused", REVIEW_SOMEONE_SAME_LINE
    done = bp.resolve_proposal(str(ws), str(row.get("id")), "declined", resolved_by=actor,
                               source_skill=COMMITMENT_SRC)
    if (done or {}).get("status") != "resolved":
        return {"status": (done or {}).get("status") or "not_resolved"}, "already", REVIEW_GONE_LINE
    batch_id = f"hub_{now.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"
    n = _review_tombstones(events_path, row, "same_as", batch_id=batch_id, person_id=pid,
                           note=f"hub review: someone else ({row.get('id')})")
    shown = str(person.get("canonical_name") or name)
    line = f"Linked to {shown}; the question is closed."
    handler: Dict[str, Any] = {"status": "linked_elsewhere", "person_id": pid, "batch_id": batch_id,
                               "n_sightings": n, "proposal": "declined"}
    if note:
        try:
            noted = record_person_fact(ws, pid, note, f"hub:review:{row.get('id')}", source_skill=COMMITMENT_SRC)
            handler["note_seq"] = (noted or {}).get("seq")
            line += " Note saved."
        except Exception as exc:  # noqa: BLE001 - the link stands; the note is said to be missing
            handler["note_error"] = type(exc).__name__
            line += " The note could not be saved; say it in a chat."
    return handler, "applied", line


def _accept_person_rename(ws, row, text, actor, events_path, now):
    from people_writer import confirm_person_rename

    res = confirm_person_rename(ws, str(row.get("id")), resolved_by=actor, source_skill=COMMITMENT_SRC)
    return ({"status": res.get("status"), "answered": True}, "applied" if res.get("ok") else "refused",
            str(res.get("ack") or ROW_ERROR_LINE))


def _accept_person_update(ws, row, text, actor, events_path, now):
    from people_writer import update_person

    if row.get("proposal_kind") == "org_change":
        update_person(ws, row["person_id"], primary_org_id=row["to_org_id"], source_skill=COMMITMENT_SRC)
        return {"status": "updated"}, "applied", "Updated."
    role = text or str(row.get("proposed_role") or "")
    if not role:
        return {"status": "needs_input"}, "refused", REVIEW_NEED_TEXT_LINE
    update_person(ws, row["person_id"], role=role, source_skill=COMMITMENT_SRC)
    return {"status": "updated"}, "applied", "Updated."


def _accept_entity_fact(ws, row, text, actor, events_path, now):
    fact = text or str(row.get("fact") or "")
    if not fact:
        return {"status": "needs_input"}, "refused", REVIEW_NEED_TEXT_LINE
    ref = str(row.get("source_ref") or "") or f"proposal:{row.get('id')}"
    if row.get("person_id"):
        from people_writer import record_person_fact

        record_person_fact(ws, row["person_id"], fact, ref, category=row.get("category"), source_skill=COMMITMENT_SRC)
    else:
        from org_writer import record_org_fact

        record_org_fact(ws, row["org_id"], fact, ref, category=row.get("category"), source_skill=COMMITMENT_SRC)
    return {"status": "recorded"}, "applied", "Saved to history."


def _accept_org_money(ws, row, text, actor, events_path, now):
    """The press is the confirm; a typed amount wins over the proposed one and
    keeps its source. Money is never estimated, so an unreadable amount stops."""
    from org_writer import set_org_money

    money = dict(row.get("proposed_money") or {})
    if text:
        try:
            amount = float(text.replace(",", "").replace("$", "").strip())
        except ValueError:
            amount = -1.0
        if not 0 <= amount < float("inf"):  # a NaN fails this too
            return {"status": "bad_amount"}, "refused", REVIEW_BAD_AMOUNT_LINE
        amount = int(amount) if amount == int(amount) else amount
        keys = [k for k, v in money.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        for k in keys[1:]:
            money.pop(k)
        money[keys[0] if keys else "account_value"] = amount
    money["source"] = money.get("source") or str(row.get("evidence") or "") or f"proposal:{row.get('id')}"
    set_org_money(ws, row["org_id"], money, source_skill=COMMITMENT_SRC, confirmed=True)
    return {"status": "saved"}, "applied", "Saved to the company."


def _accept_org(ws, row, text, actor, events_path, now):
    from org_writer import create_org, find_existing_org

    name = text or str(row.get("name") or row.get("title") or "")
    if not name:
        return {"status": "needs_input"}, "refused", REVIEW_NEED_TEXT_LINE
    if not find_existing_org(ws, name=name):
        create_org(ws, canonical_name=name, source_skill=COMMITMENT_SRC)
        out = {"status": "created"}, "applied", "Added."
    else:
        out = {"status": "already_on_file"}, "applied", "Already on file."
    asked = str(row.get("name") or row.get("title") or "")
    if asked.strip().lower() != name.strip().lower() and not str(row.get("id")).startswith("bp_"):
        # A legacy row retires when its own name is on file. Corrected, it never
        # would be, and a second press would add the misspelling too.
        from event_gate import append_event

        append_event(events_path, {"type": "org_proposal_declined", "source_skill": COMMITMENT_SRC,
                                   "data": {"name": asked, "note": f"corrected to {name}"}}, holder="cockpit_helpers")
    return out


def _no_question(ws, row: Dict[str, Any], handler: Dict[str, Any], line: str, now) -> tuple:
    """cr1#1171: a voice-rule or past-ruling writer that finds no question.
    Those writers answer `already_resolved` for a question answered elsewhere,
    so `no_question` means the writer and the queue disagree about the row.
    If the queue no longer holds it, nothing is owed: a no-op
    (`already_resolved`, the writer's word kept beside it). If the queue still
    holds it open, the press did not land: an error, never a suppressed row
    (`apply_audit`'s never-optimistic rule; review of cr1#1171)."""
    try:
        import brain_proposals as bp

        held = str(row.get("id")) in {str(p.get("id")) for p in bp.load_open_proposals(
            str(ws), "staff-meeting", now_iso=_iso(now) if now else None)}
    except Exception:  # noqa: BLE001 - cannot tell: never optimistic
        held = True
    if held:
        return handler, "error", ROW_ERROR_LINE
    return {**handler, "status": "already_resolved", "writer_status": "no_question"}, "already", line


def _accept_learned_rule(ws, row, text, actor, events_path, now):
    """The one yes `learning_pass` keeps for a voice question: it writes the
    rule and answers the row, so nothing else resolves it. The rule's Undo is on
    the Done tab."""
    from learning_pass import confirm_asked_rule

    res = confirm_asked_rule(ws, str(row.get("id")), resolved_by=actor, source_skill=COMMITMENT_SRC)
    status = res.get("status")
    handler = {"status": status, "answered": True}
    if status == "applied":
        return handler, "applied", str(res.get("line") or "")
    if status == "no_question":
        return _no_question(ws, row, handler, REVIEW_GONE_LINE, now)
    if status == "already_resolved":
        return handler, "already", REVIEW_GONE_LINE
    return handler, "refused" if status == "refused" else "error", str(res.get("line") or ROW_ERROR_LINE)


def _accept_craft_rule(ws, row, text, actor, events_path, now):
    """cr1#400: a rule the nightly sweep saw is saved through the capture
    writer. `confirm_rule` reads the proposal from the book and answers the
    row itself."""
    import craft_sweep

    res = craft_sweep.confirm_rule(ws, str(row.get("id")), resolved_by=actor, edit=text or None,
                                   now_iso=_iso(now), source_skill=COMMITMENT_SRC)
    handler = {"status": res.get("status"), "answered": True}
    if res.get("ok"):
        return handler, "applied", str(res.get("line") or "")
    if res.get("status") == "already_resolved":
        return handler, "already", REVIEW_GONE_LINE
    return handler, "refused", str(res.get("line") or ROW_ERROR_LINE)


def _accept_craft_kind(ws, row, text, actor, events_path, now):
    """cr1#1391 part B: a kind of work the nightly sweep found is added
    through `add_scope` with its first rule, saved by the capture writer.
    `confirm_kind` reads the proposal from the book and answers the row."""
    import craft_sweep

    res = craft_sweep.confirm_kind(ws, str(row.get("id")), resolved_by=actor, edit=text or None,
                                   now_iso=_iso(now), source_skill=COMMITMENT_SRC)
    handler = {"status": res.get("status"), "answered": True}
    if res.get("ok"):
        return handler, "applied", str(res.get("line") or "")
    if res.get("status") == "already_resolved":
        return handler, "already", REVIEW_GONE_LINE
    return handler, "refused", str(res.get("line") or ROW_ERROR_LINE)


def _accept_craft_retire(ws, row, text, actor, events_path, now):
    """cr1#1391 part B: the least used kind over the cap comes off the list;
    every file stays. `retire_kind` reads the proposal from the book."""
    import craft_sweep

    res = craft_sweep.retire_kind(ws, str(row.get("id")), resolved_by=actor, source_skill=COMMITMENT_SRC)
    handler = {"status": res.get("status"), "answered": True}
    if res.get("ok"):
        return handler, "applied", str(res.get("line") or "")
    if res.get("status") == "already_resolved":
        return handler, "already", REVIEW_GONE_LINE
    return handler, "refused", str(res.get("line") or ROW_ERROR_LINE)


def _accept_past_ruling(ws, row, text, actor, events_path, now):
    """cr1#1073 item 1: a past ruling pulled from the rulings files becomes a
    decision, dated the day it was ruled, with the rulings it replaces closed.
    `confirm_past_ruling` reads the proposal from the book and answers the
    row itself; a typed line is the owner's own wording for it."""
    import past_rulings

    res = past_rulings.confirm_past_ruling(ws, str(row.get("id")), resolved_by=actor,
                                           source_skill=COMMITMENT_SRC, text=text or None)
    status = res.get("status")
    handler = {"status": status, "answered": True,
               **{k: res[k] for k in ("decision_id", "replaced") if res.get(k)}}
    if status == "applied":
        return handler, "applied", str(res.get("line") or "")
    if status == "no_question":
        return _no_question(ws, row, handler, REVIEW_GONE_LINE, now)
    if status == "already_resolved":
        return handler, "already", str(res.get("line") or REVIEW_GONE_LINE)
    return handler, "error", str(res.get("line") or ROW_ERROR_LINE)


def _accept_craft_amend(ws, row, text, actor, events_path, now):
    """cr1#401: the new text goes into the craft file and the old is kept as
    superseded. `answer_amend` reads the proposal from the book and answers the
    row itself."""
    import craft_adjust

    res = craft_adjust.answer_amend(ws, str(row.get("id")), accept=True, resolved_by=actor,
                                    now_iso=_iso(now), source_skill=COMMITMENT_SRC)
    handler = {"status": res.get("status"), "answered": True}
    if res.get("ok"):
        return handler, "applied", str(res.get("line") or "")
    if res.get("status") == "already_resolved":
        return handler, "already", REVIEW_GONE_LINE
    return handler, "refused" if res.get("status") == "refused" else "error", str(res.get("line") or ROW_ERROR_LINE)


def _accept_config_drift(ws, row, text, actor, events_path, now):
    """One note the next coach session re-offers. No config is written here."""
    from event_gate import append_event

    append_event(events_path, {"type": "note", "source_skill": COMMITMENT_SRC,
                               "data": {"skill": row.get("skill"), "knob": row.get("knob"), "origin": "drift_reoffer",
                                        "summary": str(row.get("render_line") or "")}}, holder="cockpit_helpers")
    return {"status": "noted"}, "applied", "Noted for your next tune-up."


def _review_objective_pair(ws, row, events_path, now, *, confirmed: bool) -> bool:
    """The reclassification and its feedback row, which always land together.
    A yes keeps the event's envelope at full confidence; a no takes the
    objective off it. The event is never edited. A row with no source event
    has nothing to supersede and writes neither."""
    from atomic_write import atomic_append_jsonl
    from event_gate import append_event

    seq = row.get("source_event_seq")
    src = next((e for e in _review_events(ws) if isinstance(e, dict) and e.get("seq") == seq), None) if seq is not None else None
    if src is None:
        return False
    objective = row.get("objective_id")
    old_primary, old_related = src.get("primary_thread_id"), list(src.get("related_thread_ids") or [])
    new_primary, new_related = old_primary, list(old_related)
    if not confirmed:
        new_primary = None if old_primary == objective else old_primary
        new_related = [t for t in old_related if t != objective]
    said = "confirmed" if confirmed else "declined"
    reason = (f"user {said} objective-link proposal {row.get('id')}: "
              + (f"link to {objective}" if confirmed else f"unlink from {objective}"))
    # A retry after a failed feedback row finishes the pair, never doubles it.
    landed = any(e.get("type") == "reclassification" and e.get("supersedes_seq") == seq
                 and (e.get("data") or {}).get("reason") == reason for e in _review_events(ws) if isinstance(e, dict))
    if not landed:
        append_event(events_path, {
            "type": "reclassification", "source_skill": COMMITMENT_SRC, "supersedes_seq": seq,
            "primary_thread_id": new_primary, "related_thread_ids": new_related, "classification_confidence": 1.0,
            "data": {"old_primary_thread_id": old_primary, "new_primary_thread_id": new_primary,
                     "old_related_thread_ids": old_related, "new_related_thread_ids": new_related,
                     "reason": reason}},
            holder="cockpit_helpers")
    conf = src.get("classification_confidence")
    notes = f"objective link {said}: {objective}"
    feedback = Path(str(ws)) / "_hq" / "data" / "classifier_feedback.jsonl"
    if landed and feedback.exists() and any(
            (lambda d: d.get("event_seq") == seq and d.get("notes") == notes)(json.loads(ln))
            for ln in feedback.read_text(encoding="utf-8").splitlines() if ln.strip()):
        return True
    atomic_append_jsonl(feedback, {
        "ts": _iso(now), "event_seq": seq, "user_action": "confirmed" if confirmed else "changed",
        "old_primary": old_primary, "new_primary": new_primary, "signals_used": [],
        "confidence_before": conf if isinstance(conf, (int, float)) else 0.0,
        "notes": notes}, holder="cockpit_helpers")
    return True


def _accept_archived_activity(ws, row, text, actor, events_path, now):
    """A retired project moved and the owner said bring it back (cr1#753).
    `bring_back` answers the question itself."""
    import lifecycle_pass as lp

    res = lp.bring_back(ws, str(row.get("thread_id") or ""), actor=actor, source_skill=COMMITMENT_SRC,
                        reason="Brought back on the Command Room Hub")
    if res.get("status") != "updated":
        return res, "already", REVIEW_GONE_LINE
    return {**res, "answered": True}, "applied", "Back to active."


def _accept_objective_link(ws, row, text, actor, events_path, now):
    _review_objective_pair(ws, row, events_path, now, confirmed=True)
    return {"status": "linked"}, "applied", "Linked."


#: The writer of each kind's yes, the same one the chat's apply-choices names.
_REVIEW_ACCEPTS = {
    "person": _accept_person, "person_link": _accept_person_link, "person_rename": _accept_person_rename,
    "person_update": _accept_person_update, "entity_fact": _accept_entity_fact, "org_money": _accept_org_money,
    "org": _accept_org, "config_drift": _accept_config_drift, "objective_link": _accept_objective_link,
    "learned_rule": _accept_learned_rule, "archived_activity": _accept_archived_activity,
    "craft_amend": _accept_craft_amend, "craft_rule": _accept_craft_rule,
    "craft_kind": _accept_craft_kind, "craft_retire": _accept_craft_retire,
    "past_ruling": _accept_past_ruling,
}
#: The legacy rows whose `not relevant` is theirs, not a proposed close's.
_REVIEW_LEGACY_DECLINES = ("org", "project")


def _review_queue_answer(ws, verb: str, row: Dict[str, Any], text: str, actor: str, events_path, now):
    """One answer on a queue row that is not a proposed close. `row` is the
    book's own row: a page's payload never picks the target. Answers
    `(handler_result, outcome, line)`; the writer goes first, then the row."""
    import brain_proposals as bp
    from event_gate import append_event
    from mute_ledger import hold_item

    n = str(row.get("id"))
    kind = str(row.get("kind") or "")
    is_bp = n.startswith("bp_")
    if verb == REVIEW_SAME_AS_VERB and kind == "person_link":  # cr1#1222: the Hub offers it here
        return _person_link_someone_else(ws, row, text, actor, events_path, now)
    if verb not in bp._row_actions(row):
        return {"status": "not_this_row"}, "refused", UNKNOWN_VERB_LINE.format(verb=verb)
    if verb == REVIEW_SAME_AS_VERB:
        return _accept_person_same_as(ws, row, text, actor, events_path, now)
    if verb in REVIEW_ACCEPT_VERBS:
        if not _review_accepts(row):
            return {"status": "chat_only"}, "refused", _review_chat_line(row, [])
        handler, outcome, line = _REVIEW_ACCEPTS[kind](ws, row, text, actor, events_path, now)
        if outcome == "applied" and is_bp and not handler.pop("answered", False):
            done = bp.resolve_proposal(str(ws), n, "edited" if text else "applied", resolved_by=actor,
                                       source_skill=COMMITMENT_SRC)
            handler = done if kind == "objective_link" else handler
        return handler, outcome, line
    if verb == "not relevant":  # the legacy org and project rows
        if kind == "org":
            append_event(events_path, {"type": "org_proposal_declined", "source_skill": COMMITMENT_SRC,
                                       "data": {"name": str(row.get("title") or row.get("name") or "")}}, holder="cockpit_helpers")
            # cr1#1171: one org_proposal_declined row landed. `declined` is
            # a refusal word in the audit (orphan_note: nothing resolved), so
            # this landed decline answers `dismissed`, which the audit counts ok.
            return {"status": "dismissed"}, "applied", "Let go."
        handler = hold_item(str(ws), n, source_skill=COMMITMENT_SRC, surface=REVIEW_SURFACE,
                            ttl_days=REVIEW_DECLINE_DAYS, now_iso=_iso(now))
        return handler, "applied", "Let go."
    # A decline: `dismiss proposal` or `proposal not relevant`.
    if kind == "craft_amend":  # cr1#401: No keeps the rule, the owner's word on it
        import craft_adjust

        res = craft_adjust.answer_amend(ws, n, accept=False, resolved_by=actor, now_iso=_iso(now),
                                        source_skill=COMMITMENT_SRC)
        if res.get("ok"):
            return {"status": res.get("status")}, "applied", str(res.get("line") or "Kept.")
        if res.get("status") == "already_resolved":
            return {"status": "already_resolved"}, "already", REVIEW_GONE_LINE
        return {"status": res.get("status")}, "refused" if res.get("status") == "refused" else "error",             str(res.get("line") or ROW_ERROR_LINE)
    if kind == "objective_link":
        _review_objective_pair(ws, row, events_path, now, confirmed=False)
    if is_bp:
        handler = bp.resolve_proposal(str(ws), n, "declined", resolved_by=actor, source_skill=COMMITMENT_SRC)
        if kind == "person_link":
            _review_tombstones(events_path, row, "not_relevant")
        return handler, "applied", "Let go."
    _review_tombstones(events_path, row, "not_relevant")
    return {"status": "not_relevant"}, "applied", "Let go."


def apply_review_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                         *, resolved_by: str = "", now_iso=None) -> Dict[str, Any]:
    """A WRITER. The Review tab's answers, each through the row family's own
    writer, then ONE `apply_choices_applied` audit row (cr1#779):

      * `confirm` on a proposed close: `watch_gate.confirm_review_rows`, the
        shared fence; a weak row is parked and closes nothing. A row that
        names a `pile` (cr1#1540, the Work tab's Looks done) is first held to
        that pile's rule as it stands now (`_pile_gate`), which lets through
        only a question the fence would accept, so nothing is parked by it;
      * `not relevant`: one `commitment_review_dismissed`, the commitment stays
        open; `hold` and `snooze proposal 7d`: `mute_ledger.hold_item`;
      * `dismiss proposal`: `brain_proposals.resolve_proposal(declined)`;
      * a yes or a decline on any other queue row (cr1#818): the writer of its
        kind, reading the book's row, never the page's. A row no longer in the
        queue writes nothing;
      * `same as [existing]` on a person row (cr1#775): an alias on the one
        record the row named and a `same_as` tombstone per sighting; a row
        that names two or more records is answered in chat.

    Any other verb is refused. A refusal of one row never stops the rest. Each
    close's audit note says accepted or reversed, so the rate per kind can be
    read back. Answers `{ok, results: [{id, outcome, line}], n_applied, n_errors}`."""
    actor = str(resolved_by or "").strip() or _owner_id(workspace_root)
    rows = [c for c in (choices or []) if isinstance(c, dict)]
    if not actor:
        return {"ok": False, "reason": "no_actor", "line": NO_ACTOR_LINE,
                "results": [], "n_applied": 0, "n_errors": len(rows)}
    import brain_proposals as bp
    from apply_audit import build_apply_choices_applied_event
    from event_gate import append_event
    from mute_ledger import hold_item

    now = _now(now_iso)
    events_path = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
    results: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    pile_memo: Dict[str, Any] = {}  # cr1#1540: as in `apply_commitment_choices`
    reopens: Optional[Dict[str, _dt.datetime]] = None
    owner_answered: set = set()  # cr1#913: one answer per item in a run
    for c in rows:
        verb = str(c.get("action") or "").strip().lower()
        n = str(c.get("n") or "").strip()
        kind = str(c.get("review_kind") or "question").replace(" ", "_")
        r: Dict[str, Any] = {"id": c.get("id"), "action": verb, "n": n}
        handler: Any = None
        note = ""
        try:
            if verb not in REVIEW_VERBS or not n:
                r.update(outcome="error", line=UNKNOWN_VERB_LINE.format(verb=verb or "none"))
                handler = {"status": "error"}
            elif n.startswith(OWNER_Q_KIND + ":") or kind == OWNER_Q_KIND:
                # cr1#913: the board-versus-owner question, answered through
                # chat's own writer for the same answer.
                handler, outcome, line = _answer_owner_question(workspace_root, n, verb, actor, owner_answered)
                r.update(outcome=outcome, line=line)
            elif verb in (OWNER_Q_GIVE_VERB, OWNER_Q_MINE_VERB):
                # Only the board-versus-owner question answers with these here.
                r.update(outcome="error", line=UNKNOWN_VERB_LINE.format(verb=verb))
                handler = {"status": "error"}
            elif c.get("pile"):
                # cr1#1540: a Looks done pile's row. It is the review row's own
                # confirm, held first to the pile's rule as it stands now, and
                # then handed to the shared fence with the queue's own inputs
                # for that question, never the page's.
                pcid = str(c.get("commitment_id") or "")
                piled, held = _pile_gate(workspace_root, c, pcid, verb, now, pile_memo, row_kind="review")
                look = ((pile_memo.get("sets") or {}).get("looks") or {}).get(pcid) if piled else None
                if reopens is None:
                    reopens = _owner_reopens(workspace_root)
                pressed = _parse(c.get("created_at"))
                r["commitment_id"] = pcid
                if held is not None:
                    r.update(outcome=held[0], line=held[1], reason=held[2].get("error") or held[2].get("status"))
                    handler = held[2]
                elif not look or look.get("id") != n:
                    r.update(outcome="refused", line=PILE_LEFT_LINE, reason="pile_left")
                    handler = {"status": "refused", "error": "pile_left"}
                elif pressed is not None and pcid in reopens and pressed < reopens[pcid]:
                    r.update(outcome="refused", line=PRESSED_BEFORE_REOPEN_LINE, reason="pressed_before_reopen")
                    handler = {"status": "refused", "error": "pressed_before_reopen"}
                else:
                    fence = _review_close(workspace_root, dict(c, payload=dict(look)), actor, now)
                    if (fence or {}).get("n_confirmed"):
                        r.update(outcome="applied", line="Closed.")
                        note = f"review {kind} accepted"
                        handler = {"status": "closed"}
                        pile_memo.pop("sets", None)
                        mark = _standing_close(workspace_root, pcid)
                        if mark and mark.get("seq") is not None:
                            r["seq"] = mark["seq"]
                    else:
                        handler = ((fence or {}).get("results") or [{"status": "not_closed"}])[0]
                        if (fence or {}).get("n_held"):
                            r.update(outcome="refused", line=REVIEW_HELD_LINE)
                        elif handler.get("status") in ("not_open", "already_resolved"):
                            r.update(outcome="already", line=ALREADY_CLOSED_LINE)
                        else:
                            r.update(outcome="error", line=ROW_ERROR_LINE)
            elif (verb in REVIEW_ACCEPT_VERBS or verb == REVIEW_SAME_AS_VERB or verb == "proposal not relevant"
                  or (verb == "not relevant" and n.split(":", 1)[0] in _REVIEW_LEGACY_DECLINES)
                  or (verb == "dismiss proposal" and kind in ("objective_link", "person_link", "craft_amend"))):
                # Read per row: an earlier row in this batch may have answered it.
                by_seq = {e.get("seq"): e for e in _review_events(workspace_root) if isinstance(e, dict)}
                queue = {str(i.get("id")): i for i in bp.load_open_proposals(str(workspace_root), "staff-meeting", now_iso=_iso(now))}
                live = queue.get(n)
                if live is None:
                    # cr1#1161: answered already (another page, a double
                    # press). Nothing was written, so the audit records a
                    # no-op; `not_open` reads as an error there.
                    handler = {"status": "already_resolved"}
                    r.update(outcome="already", line=REVIEW_GONE_LINE)
                else:
                    text = str(c.get("input") or "").strip()
                    handler, outcome, line = _review_queue_answer(workspace_root, verb, _review_detail(live, by_seq),
                                                                  text, actor, events_path, now)
                    r.update(outcome=outcome, line=line)
            elif verb == "confirm":
                fence = _review_close(workspace_root, c, actor, now)
                if (fence or {}).get("n_confirmed"):
                    r.update(outcome="applied", line="Closed.")
                    note = f"review {kind} accepted"
                    handler = {"status": "closed"}
                else:
                    # The fence's own answer for the audit, never a made-up one.
                    handler = ((fence or {}).get("results") or [{"status": "not_closed"}])[0]
                    if (fence or {}).get("n_held"):
                        r.update(outcome="refused", line=REVIEW_HELD_LINE)
                    elif handler.get("status") in ("not_open", "already_resolved"):
                        r.update(outcome="already", line=ALREADY_CLOSED_LINE)
                    else:
                        r.update(outcome="error", line=ROW_ERROR_LINE)
            elif verb == "not relevant":
                from cru_match import build_commitment_review_dismissed_event

                cid = str(c.get("commitment_id") or n.split(":", 1)[-1])
                pseq = int(c["seq"]) if c.get("seq") is not None else _newest_open_proposal_seq(workspace_root, cid)
                ev = build_commitment_review_dismissed_event(
                    commitment_id=cid, primary_thread_id=str(c.get("thread_id") or ""),
                    source_skill=COMMITMENT_SRC, next_seq=None, proposal_seq=pseq)
                append_event(events_path, ev, holder="cockpit_helpers")
                handler = {"status": "dismissed"}
                r.update(outcome="applied", line="Left open.")
                note = f"review {kind} reversed"
            elif verb in ("hold", "snooze proposal 7d"):
                handler = hold_item(str(workspace_root), n, source_skill=COMMITMENT_SRC, surface=REVIEW_SURFACE,
                                    ttl_days=REVIEW_SNOOZE_DAYS if verb != "hold" else 14, now_iso=_iso(now))
                r.update(outcome="applied", line="Held for now.")
            else:  # dismiss proposal
                handler = bp.resolve_proposal(str(workspace_root), n, "declined", resolved_by=actor,
                                              source_skill=COMMITMENT_SRC)
                r.update(outcome="applied", line="Let go.")
        except Exception as exc:  # noqa: BLE001 - one row's failure never stops the batch
            r.update(_row_error(exc))
            handler = {"status": "error", "error": type(exc).__name__}
        results.append(r)
        entry = {"n": n, "action": verb, "handler_result": handler, "said": r.get("outcome")}  # cr1#1378
        if note:
            entry["note"] = note
        audit.append(entry)
    n_applied = sum(1 for r in results if r["outcome"] == "applied")
    n_errors = sum(1 for r in results if r["outcome"] == "error")
    if audit:
        ev = build_apply_choices_applied_event(source=COMMITMENT_SRC, actions=audit)
        ev["data"]["surface"] = REVIEW_SURFACE
        append_event(events_path, ev, holder="cockpit_helpers")
    return {"ok": True, "results": results, "n_applied": n_applied, "n_errors": n_errors}


def _answer_owner_question(workspace_root, n: str, verb: str, actor: str, answered: Optional[set] = None):
    """cr1#913: one answer to the board-versus-owner question, read again from
    the book, never from the page: a question no longer open (the board moved,
    the row closed, the owner acted on it since, in chat or here) writes
    nothing. `answered` holds the commitments this Apply run already answered
    a question on, so a Keep then a Give in one run refuses the second.

      * a keep writes nothing to the commitment; the Apply run's own audit
        row records it, and the question is not asked again for that board
        value and that hand act (`_owner_q_kept`);
      * a give is chat's `theirs to [name]` (or `mine` when the board names
        the owner) through `commitments_helpers.apply_row_action`, the
        Waiting On door chat uses for it, with the person the question names.

    Off limits is checked again here (`hub_edit._off_limits_item`, the row as
    shown now and as captured): an unreadable wall or an off-limits person
    writes nothing. Answers `(handler result, outcome, line)`."""
    q = next((x for x in board_owner_questions(workspace_root) or [] if x["id"] == n), None)
    if q is None or (answered is not None and q["commitment_id"] in answered):
        return {"status": "already_resolved"}, "already", OWNER_Q_GONE_LINE
    if verb not in [v["action"] for v in q["verbs"]]:
        return {"status": "error"}, "error", UNKNOWN_VERB_LINE.format(verb=verb or "none")
    import hub_edit
    import tasks_follow as tf

    held = next((h for h in tf.held_owner_questions(workspace_root) or []
                 if h["commitment_id"] == q["commitment_id"]), None)
    if held is None or hub_edit._off_limits_item(Path(str(workspace_root)), q["commitment_id"],
                                                 held["row"], held.get("capture")) is not False:
        return {"status": "already_resolved"}, "already", OWNER_Q_GONE_LINE
    if answered is not None:
        answered.add(q["commitment_id"])
    if verb == OWNER_Q_KEEP_VERB:
        return None, "applied", OWNER_Q_KEPT_LINE.format(chosen=q["chosen"])
    import commitments_helpers

    who = {"owner_id": q["board_id"], "owner_name": q["board"]} if verb == OWNER_Q_GIVE_VERB else {}
    res = commitments_helpers.apply_row_action(str(workspace_root), action=verb, commitment_id=q["commitment_id"],
                                               resolved_by=actor, **who)
    status = (res or {}).get("status")
    if status in ("reassigned", "confirmed"):
        return res, "applied", res.get("ack") or "Done."
    if status in ("not_open", "already_resolved"):
        return res, "already", ALREADY_CLOSED_LINE
    return res, "error", (res or {}).get("ack") or ROW_ERROR_LINE


#: The Done tab (cr1#780).
WEEK_DAYS = 7
#: cr1#1645 item 8: "decided", the word of Record's own heading ("Decided for
#: you"), never the internal "settled".
WEEK_EMPTY_LINE = "Nothing was decided for you this week."
WEEK_RULES_LINE = "Nothing else was decided for you this week."
WEEK_PARTIAL_LINE = "Part of this could not be put back; say `undo` in a chat."
WEEK_ALREADY_LINE = "That was already put back; nothing was written."
#: cr1#1322: every change refused, because the item changed after it.
WEEK_CHANGED_LINE = "That changed after, so it was left as it is; nothing was written."
#: cr1#1356: every change refused, said by the refusal's own `why` (the keys
#: `brain_undo._REFUSED_WHY` names). Refusals that give different reasons, or
#: a reason not named here, get the plain line, which claims no reason.
WEEK_REFUSED_LINES = {
    "changed": WEEK_CHANGED_LINE,
    "unrecorded": "What it held before is not on record, so it was left as it is; nothing was written.",
    "newer_date": "It has a newer date now, so it was left as it is; nothing was written.",
}
WEEK_REFUSED_LINE = "That could not be put back exactly, so it was left as it is; nothing was written."
#: cr1#1356: nothing went back because the change is no longer where it was
#: written (`not_found`), so what it did still stands.
WEEK_NOT_FOUND_LINE = "That is no longer where it was written, so it still stands; nothing was written."
WEEK_VERBS = frozenset({"undo", "bring back", "archive"})
WEEK_SURFACE = "cockpit-week"
WEEK_ITEMS_CAP = 12
WEEK_DECIDED_CAP = 40
#: cr1#1368: the owner's own changes this week, capped on their own so a busy
#: week of Laters never pushes the system's decisions out of view.
WEEK_YOURS_CAP = 40
def _owner_own_change(ev: Dict[str, Any]) -> bool:
    """cr1#1368: True when this change row is shown to be the owner's own
    act. It needs positive evidence, because a row that names no actor (a
    learned rule, an older rail) is not the owner's: a person stamp, a
    confirmed tap, a person named as the actor or as who gave the date, or a
    Later's own hide (`via` "later", which only `apply_later` writes). The
    one reader, `event_types.is_customer_act`, is asked first (CONTRACT Rule
    32), so a person stamp wins over a background source as it does there."""
    import event_types as et
    from commitment_state import LATER_SNOOZE_VIA

    if not et.is_customer_act(ev):
        return False
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return (d.get(et.ACTOR_KIND_KEY) == et.ACTOR_PERSON or d.get("user_confirmed") is True
            or et.is_person_actor(et.act_actor(ev)) or et.is_person_actor(d.get("pushed_by"))
            or (ev.get("type") == "chat_dismissal" and d.get("via") == LATER_SNOOZE_VIA))


_WEEK_DORMANT_VERBS = [{"action": "bring back", "label": "Bring back"}, {"action": "archive", "label": "Archive"}]
_WEEK_ARCHIVED_VERBS = _WEEK_DORMANT_VERBS[:1]


def _week_undone(events: List[Dict[str, Any]]) -> Dict[str, int]:
    """Reversal markers per batch, by the marker's own ref label, the one key
    every batch kind shares: one `brain_change_undone` per change undone.
    A commitment note taken back (cr1#1463, from the Hub or chat) writes a
    `commitment_note_retracted` instead, naming the note's row; it counts
    for the note's own batch (review of #1611)."""
    import brain_undo as bu
    from commitment_state import NOTE_RETRACT_TYPE

    out: Dict[str, int] = {}
    batch_of = {}
    for e in events:
        if not isinstance(e, dict):
            continue
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        if e.get("type") == "note" and d.get("brain_batch_id") and isinstance(e.get("seq"), int):
            batch_of[e["seq"]] = str(d["brain_batch_id"])
        if e.get("type") == "brain_change_undone":
            ref = str(d.get("batch_ref") or "")
            if ref:
                out[ref] = out.get(ref, 0) + 1
        elif e.get("type") == NOTE_RETRACT_TYPE and batch_of.get(d.get("retracts_seq")):
            ref = bu.batch_ref_label({"kind": "brain_batch", "batch_id": batch_of[d["retracts_seq"]]})
            out[ref] = out.get(ref, 0) + 1
    return out


#: The Done tab's month dashboard (cr1#1287, owner ruling 2026-11-16): what
#: was done this calendar month, in the workspace's zone, counted by kind,
#: then each item newest first. Read only. The kinds in the order the page
#: shows them, each with its words.
MONTH_KINDS = (
    ("closed", "commitment closed", "commitments closed"),
    ("sent", "reply sent", "replies sent"),
    ("prep", "prep made", "preps made"),
    ("decision", "decision recorded", "decisions recorded"),
    # cr1#1641 item 20 (cr1#1645): what was done, never the internal "choices".
    ("applied", "change made", "changes made"),
)
#: The closes that count as done this month: a fact, the owner's word, or
#: the owner's own tap (`flow_measure.route_of`). A silence or lapse close is
#: a letting go, not a thing done.
MONTH_CLOSE_ROUTES = ("fact", "own_word", "tap")
#: How many items travel on the page, newest first. Counts are never capped.
MONTH_ITEMS_CAP = 150
#: A prep slug's last part is the meeting's 8-hex hash (`prep_pipeline.prep_slug`).
_PREP_SLUG_HASH = re.compile(r"(?:^|-)[0-9a-f]{8}$")


def _month_start_iso(value) -> str:
    """cr1#1710 review: a meeting's start as the page compares it, in UTC
    ("Z"), as `feed/today` carries its meetings. A date with no time, or a
    value that does not parse, is kept as written."""
    raw = str(value or "")[:40]
    if "T" not in raw:
        return raw
    dt = _parse(raw)
    return _iso(dt) if dt else raw


def _month_start(workspace_root, now: _dt.datetime) -> _dt.datetime:
    """The first instant of `now`'s calendar month in the workspace's zone."""
    local = now.astimezone(_workspace_tz(workspace_root))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _month_done(workspace_root, events: List[Dict[str, Any]], now: _dt.datetime) -> Dict[str, Any]:
    """READ. What was done this calendar month, from the log only (cr1#1287):
    `{since, month, kinds: [{key, one, many, n}], items, n_items, threads,
    people}`. Each item is `{kind, ts, title, source, thread_id, thread,
    people: [{id, name}]}`, newest first; `threads` and `people` are the
    filter's options, from the items themselves. What counts:

      closed    a commitment on file whose standing close (not reopened since)
                fell this month by `MONTH_CLOSE_ROUTES`, once per commitment;
      sent      each `email_sent`;
      prep      each `prep_brief` that is not a refresh, once per meeting;
      decision  each `decision` that is not a restamp of an earlier one;
      applied   each answer an `apply_choices_applied` row records as ok,
                listed one row per Apply.

    No money, no pointer, no event id. Never writes."""
    from flow_measure import route_of
    from plate_view import row_title

    start = _month_start(workspace_root, now)
    owner = _owner_id(workspace_root)
    names = _person_names(workspace_root)
    threads = _thread_names(workspace_root)

    def inside(ev):
        at = _parse(ev.get("ts"))
        return at if at is not None and start <= at <= now else None

    def where(ev, d):
        tid = str(ev.get("primary_thread_id") or d.get("primary_thread_id") or d.get("thread_id") or "")
        ppl: List[Dict[str, str]] = []
        att = d.get("attendee_person_ids")
        for pid in (list(ev.get("person_ids") or []) + (list(att) if isinstance(att, list) else [])
                    + [d.get("owner_id"), d.get("counterparty_id")]):
            pid = str(pid or "")
            if pid and pid != owner and pid in names and pid not in [p["id"] for p in ppl]:
                ppl.append({"id": pid, "name": names[pid][:120]})
        return {"thread_id": tid if tid in threads else "", "thread": threads.get(tid) or "", "people": ppl[:6]}

    commitments: Dict[str, Dict[str, Any]] = {}
    closes: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        kind = ev.get("type")
        if kind == "commitment" and d.get("id"):
            commitments[str(d["id"])] = ev
        elif kind == "commitment_resolved":
            cid = str(d.get("commitment_id") or d.get("id") or "")
            if cid:
                closes[cid] = ev   # the newest close stands
        elif kind == "commitment_reopened":
            closes.pop(str(d.get("commitment_id") or d.get("target_id") or ""), None)

    items: List[Dict[str, Any]] = []
    counts = {k: 0 for k, _one, _many in MONTH_KINDS}
    for cid, ev in closes.items():
        at = inside(ev)
        c = commitments.get(cid)
        if at is None or c is None or route_of(ev) not in MONTH_CLOSE_ROUTES:
            continue
        cd = c.get("data") if isinstance(c.get("data"), dict) else {}
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        # Where the promise came from, as the Today strip says it.
        src = (_done_source({"type": "commitment"}, {}, cd.get("source_ref")) if cd.get("source_ref") else {}) \
            or _done_source(ev, d)
        counts["closed"] += 1
        items.append(dict(where(c, cd), kind="closed", ts=_iso(at), source=src,
                          title=row_title(cd.get("title"), cd.get("summary"))[:240] or "A commitment"))
    preps = set()
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind not in ("email_sent", "prep_brief", "decision", "apply_choices_applied"):
            continue
        at = inside(ev)
        if at is None:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if kind == "email_sent":
            counts["sent"] += 1
            items.append(dict(where(ev, d), kind="sent", ts=_iso(at), source=_done_source(ev, d) or {"word": "Email"},
                              title=str(d.get("subject") or d.get("topic") or "A reply")[:240]))
        elif kind == "prep_brief":
            # One meeting is its id and its start: a recurring series shares
            # the id (`receipts.log_prep_receipt`).
            mid = (str(d.get("meeting_id") or d.get("slug") or ev.get("seq") or ""), str(d.get("meeting_start") or ""))
            if d.get("refreshed") or mid in preps:
                continue
            preps.add(mid)
            slug = _PREP_SLUG_HASH.sub("", str(d.get("slug") or "")).replace("-", " ").replace("_", " ").strip()
            counts["prep"] += 1
            items.append(dict(where(ev, d), kind="prep", ts=_iso(at), source={"word": "Meeting"},
                              title=str(d.get("title") or d.get("meeting_title") or slug or "A meeting prep")[:240],
                              # cr1#1710: the calendar event, so the page can show its title
                              meeting_id=str(d.get("meeting_id") or "")[:200],
                              start=_month_start_iso(d.get("meeting_start"))))
        elif kind == "decision":
            if ev.get("supersedes_seq") is not None or d.get("supersedes_seq") is not None:
                continue
            counts["decision"] += 1
            items.append(dict(where(ev, d), kind="decision", ts=_iso(at), source=_done_source(ev, d) or {"word": "Chat"},
                              title=str(d.get("title") or d.get("decision") or d.get("summary") or "A decision")[:240]))
        else:
            ok = [a for a in (d.get("actions") or []) if isinstance(a, dict) and a.get("outcome") == "ok"]
            if not ok:
                continue
            counts["applied"] += len(ok)
            verbs: List[str] = []
            for a in ok:
                v = str(a.get("action") or "").strip()
                if v and v not in verbs:
                    verbs.append(v)
            hub = str(d.get("surface") or "").startswith("cockpit")
            items.append({"kind": "applied", "ts": _iso(at), "source": {"word": "Hub" if hub else "Chat"},
                          "title": (f"{len(ok)} change made" if len(ok) == 1 else f"{len(ok)} changes made")
                          + (": " + ", ".join(verbs[:4]) if verbs else ""),
                          "thread_id": "", "thread": "", "people": []})
    items.sort(key=lambda r: r["ts"], reverse=True)
    n_items = len(items)
    # cr1#1304: the filter offers only what the carried items name, so past
    # the cap no choice leads to an empty list.
    items = items[:MONTH_ITEMS_CAP]
    opt_threads = sorted({(r["thread_id"], r["thread"]) for r in items if r["thread_id"]}, key=lambda t: t[1].lower())
    opt_people: Dict[str, str] = {}
    for r in items:
        for p in r["people"]:
            opt_people.setdefault(p["id"], p["name"])
    return {"since": _iso(start), "month": start.strftime("%B %Y"),
            "kinds": [{"key": k, "one": one, "many": many, "n": counts[k]} for k, one, many in MONTH_KINDS],
            "items": items, "n_items": n_items,
            "threads": [{"id": t, "name": n} for t, n in opt_threads],
            "people": [{"id": p, "name": n} for p, n in sorted(opt_people.items(), key=lambda x: x[1].lower())]}


def feed_week(workspace_root, *, sections: Optional[Dict[str, Any]] = None, now_iso=None) -> Dict[str, Any]:
    """READ. The Done tab (cr1#780): every batch the system settled this week
    (`brain_undo.recent_auto_batches`), each named in its own words, with a way
    back where one is registered; each piece of work that went dormant and
    still is, with the owner's two steps; what the lifecycle pass archived on
    its own, shown and not hidden; and the recap. `sections` is what
    `friday_wrap_helpers:week_sections` answered: the run hands it in, because
    that module renders documents and this one must not import it. Its
    `decided_for_you` narrates what no batch can carry. Never writes. A batch
    whose every change is the owner's own act (`_owner_own_change`: a Later,
    a Hub date on their own item, a wait the owner set or ended) is listed
    under `yours`, not
    `decided`: it was not decided for them (cr1#1368). `rules`
    is every learned rule still in force with its own Undo (cr1#821), whatever
    its age; a batch shown there is not shown again under `decided`. `month`
    is what was done this calendar month, counted by kind and listed newest
    first (`_month_done`, cr1#1287); the page filters it and offers no press."""
    import brain_undo as bu
    import learning_pass as learn
    import lifecycle_pass as lp
    from entities_io import entities_collection, read_entities_json

    now = _now(now_iso)
    iso_now = _iso(now)
    since = now - _dt.timedelta(days=WEEK_DAYS)
    events = _review_events(workspace_root)
    errors: List[str] = []
    decided: List[Dict[str, Any]] = []
    n_decided = 0
    yours: List[Dict[str, Any]] = []
    n_yours = 0
    rules: List[Dict[str, Any]] = []
    try:
        for r in learn.learned_rules(str(workspace_root), now_iso=iso_now):
            # Only a ref that is this one change: a rule from before cr1#784
            # shares its run's batch, whose Undo would take the rest too. That
            # stays a batch, under `decided`, which says what it is.
            if r.get("status") == "live" and r.get("undo") and len(bu.resolve_batch(str(workspace_root), r["undo"])) == 1:
                rules.append({"id": bu.batch_ref_label(r["undo"]), "label": str(r.get("rule") or "")[:200],
                              "effect": str(r.get("effect") or "")[:300], "rests_on": str(r.get("rests_on") or "")[:300],
                              "at": str(r.get("applied_at") or "")[:10], "n": 1, "batch_ref": r["undo"]})
    except Exception:  # noqa: BLE001 - said on the page, never shown as no rules
        errors.append("rules")
    ruled = {r["id"] for r in rules}
    try:
        undone = _week_undone(events)
        asked = {str((e.get("data") or {}).get("proposal_id")): (e.get("data") or {}).get("title") or (e.get("data") or {}).get("render_line")
                 for e in events if isinstance(e, dict) and e.get("type") == "brain_proposal"}
        titled = {str((e.get("data") or {}).get("id")): (e.get("data") or {}).get("title")
                  for e in events if isinstance(e, dict) and e.get("type") == "commitment"}
        for b in bu.recent_auto_batches(str(workspace_root), days=WEEK_DAYS, now_iso=iso_now):
            ref = b.get("batch_ref") or {}
            key = bu.batch_ref_label(ref)
            bid = str(ref.get("batch_id") or "")
            n = int(b.get("n_changes") or 0)
            # A run counts its groups' changes too. One shown as a rule, or
            # already put back, leaves nothing here for this row to offer.
            kept = 0
            for child in b.get("children") or []:
                ck = bu.batch_ref_label(child.get("batch_ref") or {})
                cn = int(child.get("n_changes") or 0)
                kept += cn if ck in ruled or undone.get(ck, 0) >= max(cn, 1) else 0
            if undone.get(key, 0) >= max(n, 1) or key in ruled or (n and kept + undone.get(key, 0) >= n):
                continue
            items: List[str] = []
            classes = set()
            n_rows = n_owner = 0
            for ev in events:
                d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
                if bid and d.get("brain_batch_id") == bid:
                    if d.get("brain_change_class"):
                        classes.add(d["brain_change_class"])
                        n_rows += 1
                        n_owner += 1 if _owner_own_change(ev) else 0
                    title = (d.get("title") or d.get("name") or asked.get(str(d.get("proposal_id")))
                             or titled.get(str(d.get("commitment_id") or d.get("id")
                                               or d.get("target_id"))) or "")
                    if title and title not in items:
                        items.append(str(title)[:160])
            # cr1#1368: every change in it the owner's own act, so it was not
            # decided for them. A batch with no change rows of its own (a run
            # of groups, a sent-mail audit) stays under `decided`, as before.
            lane = yours if ref.get("kind") == "brain_batch" and n_rows and n_owner == n_rows else decided
            lane.append({"id": key, "label": str(b.get("label") or "")[:200], "n": n,
                            "at": str(b.get("ts") or "")[:10], "items": items[:WEEK_ITEMS_CAP],
                            "n_more": max(0, len(items) - WEEK_ITEMS_CAP),
                            "reversible": all(c in bu.REVERSERS for c in classes), "batch_ref": ref})
        n_decided = len(decided)
        decided = decided[:WEEK_DECIDED_CAP]
        n_yours = len(yours)
        yours = yours[:WEEK_YOURS_CAP]
    except Exception:  # noqa: BLE001 - said on the page, never shown as an empty week
        errors.append("decided")
    dormant: List[Dict[str, Any]] = []
    archived: List[Dict[str, Any]] = []
    try:
        ent = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
        projects = {p["id"]: p for p in entities_collection(ent, "projects") if isinstance(p, dict) and p.get("id")}
        seen = set()
        for ev in reversed(events):
            d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
            to = d.get("to_status")
            if ev.get("type") != "status_change" or to not in ("dormant", "archived"):
                continue
            tid = ev.get("primary_thread_id") or d.get("thread_id")
            at = _parse(ev.get("ts"))
            p = projects.get(tid)
            if tid in seen or at is None or at < since or not p or str(p.get("status") or "").lower() != to:
                continue
            seen.add(tid)
            row = {"id": tid, "name": str(p.get("display_name") or p.get("canonical_name") or p.get("name") or tid)[:160],
                   "reason": str(d.get("reason") or "")[:200], "at": str(ev.get("ts") or "")}
            if to == "dormant":
                dormant.append(dict(row, verbs=list(_WEEK_DORMANT_VERBS)))
            elif ev.get("source_skill") == lp.SOURCE_SKILL:
                archived.append(dict(row, verbs=list(_WEEK_ARCHIVED_VERBS)))
    except (FileNotFoundError, ValueError, OSError):
        errors.append("dormant")
    sec = sections if isinstance(sections, dict) else {}
    recap = {"changes": [str(x)[:300] for x in (sec.get("change_lines") or [])][:20],
             # The recap's own lines, as lines: its heading and list marks are
             # for a document, and the page draws its own.
             "decided_lines": [ln.strip().lstrip("-* ").strip()[:300] for ln in str(sec.get("decided_for_you") or "").splitlines()
                               if ln.strip() and not ln.strip().startswith("#")][:20],
             "still_waiting": str(sec.get("still_waiting") or "")[:600],
             "objectives": [str(x)[:200] for x in (sec.get("objective_rows") or [])][:12],
             "open_proposals": sec.get("open_proposals")}
    empty = not (decided or yours or dormant or archived or recap["changes"] or recap["decided_lines"])
    month: Optional[Dict[str, Any]] = None
    try:
        month = _month_done(workspace_root, events, now)   # cr1#1287: the month's dashboard
    except Exception:  # noqa: BLE001 - said on the page, never shown as an empty month
        errors.append("month")
    return _with_words(workspace_root, {"generated_at": iso_now, "month": month, "decided": decided, "n_decided": n_decided,
            "yours": yours, "n_yours": n_yours, "dormant": dormant, "archived": archived,
            "recap": recap, "rules": rules[:WEEK_DECIDED_CAP], "n_rules": len(rules),
            "line": (WEEK_RULES_LINE if rules else WEEK_EMPTY_LINE) if empty and not errors else "", "errors": errors})


def _bring_back(workspace_root, tid: str, actor: str) -> None:
    """A press: a dormant or a retired project to active, as the owner's act,
    through `lifecycle_pass.bring_back`, which reads the status it is leaving
    and answers the questions standing on the project."""
    import lifecycle_pass as lp

    lp.bring_back(workspace_root, tid, actor=actor, source_skill=COMMITMENT_SRC,
                  reason="Brought back on the Command Room Hub")


# ---------------------------------------------------------------------------
# cr1#1584: the Tidy section's presses, written with the Record tab's
# project and folder writes (`apply_week_choices`)
# ---------------------------------------------------------------------------

TIDY_ARCHIVE_REASON = "Archived from Tidy on the Command Room Hub"
TIDY_RESTORE_REASON = "Put back from Tidy on the Command Room Hub"
TIDY_UNMAKE_REASON = "Taken back from Tidy on the Command Room Hub"
TIDY_VERB_LINE = "That press is not one this pile has, so nothing was written."
TIDY_UNREADABLE_LINE = "That pile could not be checked just now, so nothing was written. Press Go, then try again."
TIDY_LEFT_LINE = "That one is no longer in the pile, so nothing was written."
TIDY_TAKEN_LINE = "A project by that name is there already, so nothing was written."
TIDY_NO_FOLDER_LINE = "That folder could not be found, so nothing was written."
TIDY_BACK_LINE = "An Undo needs the press it takes back, so nothing was written."
TIDY_CHANGED_LINE = "That one changed since that press, so the Undo wrote nothing."
TIDY_DONE_LINE = "Already done by this press; nothing more was written."


def _tidy_book(workspace_root) -> List[Dict[str, Any]]:
    from events_io import load_events_owner_scoped

    return [e for e in load_events_owner_scoped(str(workspace_root))[0] if isinstance(e, dict)]


def _tidy_last_status(events: List[Dict[str, Any]], tid: str) -> Optional[Dict[str, Any]]:
    """The newest `status_change` on a project: `{seq, from_status, to_status}`."""
    last = None
    for ev in events:
        if ev.get("type") == "status_change" and str(ev.get("primary_thread_id") or "") == tid:
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            last = {"seq": ev.get("seq"), "from_status": str(d.get("from_status") or ""),
                    "to_status": str(d.get("to_status") or "")}
    return last


def _tidy_written(events: List[Dict[str, Any]], choice_id: Any) -> Optional[Dict[str, Any]]:
    """What an earlier run wrote for this very press, read off the Apply
    run's own audit row (`apply_week_choices` names the press there):
    `{seq, thread_id}`, or None when no run wrote it. So a run handed a press
    whose result never reached the page answers `already` with the write's
    own row, and the page's Undo still names it (the #1342 rule for Assign)."""
    cid = str(choice_id or "")
    if not cid:
        return None
    for ev in reversed(events):
        d = ev.get("data") if ev.get("type") == "apply_choices_applied" and isinstance(ev.get("data"), dict) else {}
        if d.get("surface") != WEEK_SURFACE:
            continue
        for row in d.get("actions") or []:
            if isinstance(row, dict) and row.get("id") == cid and row.get("outcome") == "ok"                     and isinstance(row.get("seq"), int) and not isinstance(row.get("seq"), bool):
                return {"seq": row["seq"], "thread_id": str(row.get("thread_id") or "")}
    return None


def _tidy_gate(workspace_root, c: Dict[str, Any], verb: str, n: str, now_iso: str,
               memo: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """A Tidy press, asked against its pile's rule as the book stands now
    (`hub_tidy.tidy_sets`, read again after every write of the batch). None
    when the row may be written, else `(said, handler)`, writing nothing."""
    import hub_tidy

    kind = hub_tidy.pile_kind(c.get("pile"))
    if hub_tidy.TIDY_VERBS.get(kind) != verb:
        return ({"outcome": "refused", "line": TIDY_VERB_LINE}, {"status": "refused", "error": "pile_verb"})
    if "sets" not in memo:
        memo["sets"] = hub_tidy.tidy_sets(workspace_root, now_iso)
    ids = memo["sets"].get(kind)
    if ids is None:
        return ({"outcome": "refused", "line": TIDY_UNREADABLE_LINE}, {"status": "refused", "error": "pile_unreadable"})
    if n not in ids:
        return ({"outcome": "refused", "line": TIDY_LEFT_LINE}, {"status": "refused", "error": "pile_left"})
    return None


def _apply_tidy_row(workspace_root, c: Dict[str, Any], verb: str, n: str, actor: str, now_iso,
                    memo: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """cr1#1584: one Tidy row, through the owning writer. Answers `(what the
    result says, the audit's handler result)`.

      * `archive` (Gone quiet): the lifecycle question's own `archive`
        answer: `thread_archive.archive_thread`, then each lifecycle question
        still open on it resolved `applied`, as that answer resolves it.
        The result keeps the `seq` of the status row it wrote;
      * `restore` (its Undo): back to the status it had, through the
        lifecycle's own typed status write (`lifecycle_pass._set_status`,
        which `bring_back` uses), only while the archive that press wrote
        is the project's newest status change (`back_seq`);
      * `make project` (Folders with no project): `thread_writer.
        create_thread` with that folder, under the owner's own company; the
        result keeps the new project's id and the `seq` of its
        `thread_created`;
      * `unmake project` (its Undo): that project archived, only while
        nothing has landed on it since it was made.

    A press an earlier run already wrote answers `already` with that write's
    row (`_tidy_written`); one whose item left the pile is refused
    (`_tidy_gate`); an Undo answers `already` and writes nothing when the
    owner acted since."""
    import thread_archive as ta

    root = Path(str(workspace_root))
    iso_now = _iso(_now(now_iso))
    if not n:
        return {"outcome": "error", "line": UNKNOWN_VERB_LINE.format(verb=verb or "none")}, {"status": "error"}
    import hub_tidy

    if verb in set(hub_tidy.TIDY_VERBS.values()):
        done = _tidy_written(_tidy_book(root), c.get("id"))
        if done is not None:
            said = {"outcome": "already", "line": TIDY_DONE_LINE, "seq": done["seq"]}
            if done["thread_id"]:
                said["thread_id"] = done["thread_id"]
            return said, {"status": "already_written", "seq": done["seq"]}
        held = _tidy_gate(root, c, verb, n, iso_now, memo)
        if held is not None:
            return held
    elif verb in TIDY_BUCKET_BACKS:
        # cr1#1584 PR 2: a merge's or a tidy's Undo names its dated folder in `n`
        if not c.get("back_of"):
            return {"outcome": "refused", "line": TIDY_BACK_LINE}, {"status": "refused", "error": "no_press"}
    elif not c.get("back_of") or not isinstance(c.get("back_seq"), int) or isinstance(c.get("back_seq"), bool):
        return {"outcome": "refused", "line": TIDY_BACK_LINE}, {"status": "refused", "error": "no_press"}
    if verb in TIDY_MORE:
        return _apply_tidy_more(root, c, verb, n, memo, iso_now)

    if verb == "archive":
        import brain_proposals as bp

        res = ta.archive_thread(str(root), n, reason=TIDY_ARCHIVE_REASON, source_skill=COMMITMENT_SRC)
        memo.pop("sets", None)
        if res.get("status") == "already_archived":
            return {"outcome": "already", "line": "Already archived; nothing was written."}, {"status": "already_archived"}
        for p in bp.open_brain_proposals(str(root), now_iso=iso_now):
            if p.get("kind") == "dormancy" and str(p.get("thread_id") or "") == n:
                bp.resolve_proposal(str(root), str(p["id"]), "applied", resolved_by=actor,
                                    source_skill=COMMITMENT_SRC, note=TIDY_ARCHIVE_REASON)
        last = _tidy_last_status(_tidy_book(root), n) or {}
        return ({"outcome": "applied", "line": "Archived.", "seq": last.get("seq"),
                 "from_status": str(res.get("from_status") or "")},
                {"status": "archived", "seq": last.get("seq")})

    if verb == "restore":
        import lifecycle_pass as lp

        last = _tidy_last_status(_tidy_book(root), n)
        prev = str((last or {}).get("from_status") or "")
        if (last is None or last.get("seq") != c.get("back_seq") or last.get("to_status") != ta.ARCHIVED_STATUS
                or prev not in ("active", "dormant") or _tidy_status_of(root, n) != ta.ARCHIVED_STATUS):
            return {"outcome": "already", "line": TIDY_CHANGED_LINE}, {"status": "already_resolved"}
        lp._set_status(str(root), n, from_status=ta.ARCHIVED_STATUS, to_status=prev,
                       reason=TIDY_RESTORE_REASON, source_skill=COMMITMENT_SRC)
        return {"outcome": "applied", "line": "Put back as " + prev + "."}, {"status": "updated", "to_status": prev}

    if verb == "make project":
        import thread_writer as tw

        if not (root / n).is_dir() or tw.resolve_folder_name(root, n, n) != n:
            return {"outcome": "refused", "line": TIDY_NO_FOLDER_LINE}, {"status": "refused", "error": "no_folder"}
        try:
            made = _writer_create(root, canonical_name=n, folder_name=n, status="active",
                                  org_id=_own_org(root), source_skill=SOURCE_SKILL)
        except ValueError:
            return {"outcome": "refused", "line": TIDY_TAKEN_LINE}, {"status": "refused", "error": "name_taken"}
        finally:
            memo.pop("sets", None)
        tid = str(made.get("id") or "")
        seq = None
        for ev in _tidy_book(root):
            if ev.get("type") == "thread_created" and str((ev.get("data") or {}).get("primary_thread_id") or "") == tid:
                seq = ev.get("seq")
        return ({"outcome": "applied", "line": "Made a project.", "thread_id": tid, "seq": seq},
                {"status": "created", "thread_id": tid, "seq": seq})

    # `unmake project`: the project this row names, made by the press it takes back
    events = _tidy_book(root)
    made = next((ev for ev in events if ev.get("type") == "thread_created" and ev.get("seq") == c.get("back_seq")
                 and str((ev.get("data") or {}).get("primary_thread_id") or "") == n), None)
    if made is None or _tidy_status_of(root, n) != "active" or _tidy_touched_since(events, n, made.get("seq")):
        return {"outcome": "already", "line": TIDY_CHANGED_LINE}, {"status": "already_resolved"}
    ta.archive_thread(str(root), n, reason=TIDY_UNMAKE_REASON, source_skill=COMMITMENT_SRC)
    return {"outcome": "applied", "line": "Taken back: that project is archived."}, {"status": "archived"}


#: cr1#1584 PR 2: the presses `_apply_tidy_more` writes, and the two Undos
#: that name a dated folder rather than a row of the log.
TIDY_MORE = frozenset({"link folder", "unlink folder", "merge notes", "unmerge notes", "tidy clutter", "untidy clutter",
                       "file away", "put back"})
TIDY_BUCKET_BACKS = frozenset({"unmerge notes", "untidy clutter", "put back"})


def _tidy_updated_seq(events: List[Dict[str, Any]], tid: str) -> Optional[int]:
    """The seq of the newest `thread_updated` row on a project."""
    last = None
    for ev in events:
        if ev.get("type") == "thread_updated" and str((ev.get("data") or {}).get("primary_thread_id") or "") == tid:
            last = ev.get("seq")
    return last if isinstance(last, int) and not isinstance(last, bool) else None


def _tidy_folder_of(root: Path, tid: str) -> str:
    from entities_io import entities_collection, read_entities_json

    for p in entities_collection(read_entities_json(root / "_hq" / "data" / "entities.json"), "projects"):
        if isinstance(p, dict) and str(p.get("id") or "") == tid:
            return str(p.get("folder_name") or "")
    return ""


def _apply_tidy_more(root: Path, c: Dict[str, Any], verb: str, n: str,
                     memo: Dict[str, Any], iso_now: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """cr1#1584 PR 2: the relink, the notes merge and Command Room's own
    tidy, and each one's Undo, through the owning writer. A press reached
    here passed its pile's rule (`_tidy_gate`).

      * `link folder`: `thread_writer.update_thread(folder_name=)` to the
        project's same-named folder, as the pile reads it now (never a name
        the page sent); never makes a folder. Its Undo, `unlink folder`,
        clears it only while that write is the project's newest change;
      * `merge notes`: `notes_merge.merge_notes`, archive first; its Undo,
        `unmerge notes`, `notes_merge.unmerge_notes` on the dated folder the
        merge answered, only while the merged file is unchanged;
      * `tidy clutter`: `cleanup_actions.tidy_folder` (cr1#1019), archive
        first with a manifest; its Undo, `untidy clutter`,
        `cleanup_actions.undo_folder_tidy` on that tidy's dated folder."""
    import thread_writer as tw

    if verb == "link folder":
        import hub_tidy

        folder = str((hub_tidy.relink_counted(root)[0].get(n) or {}).get("where") or "")
        if not folder or not (root / folder).is_dir() or tw.resolve_folder_name(root, folder, folder) != folder:
            return {"outcome": "refused", "line": TIDY_NO_FOLDER_LINE}, {"status": "refused", "error": "no_folder"}
        tw.update_thread(root, n, folder_name=folder, source_skill=SOURCE_SKILL)
        memo.pop("sets", None)
        seq = _tidy_updated_seq(_tidy_book(root), n)
        return ({"outcome": "applied", "line": "Linked to its folder.", "seq": seq},
                {"status": "updated", "seq": seq})
    if verb == "unlink folder":
        if _tidy_updated_seq(_tidy_book(root), n) != c.get("back_seq") or not _tidy_folder_of(root, n):
            return {"outcome": "already", "line": TIDY_CHANGED_LINE}, {"status": "already_resolved"}
        tw.update_thread(root, n, folder_name=None, source_skill=SOURCE_SKILL)
        return {"outcome": "applied", "line": "Unlinked; the folder stays where it is."}, {"status": "updated"}
    if verb == "merge notes":
        import notes_merge

        from commitment_state import _now_calendar_day

        # the heading's date is the Apply run's own day, on the workspace's calendar
        day = _now_calendar_day(iso_now or _iso(_now(None)), workspace_path=str(root))
        res = notes_merge.merge_notes(root, n, today=day.isoformat() if day else None)
        memo.pop("sets", None)
        if res.get("status") != "merged":
            return {"outcome": "refused", "line": TIDY_LEFT_LINE}, {"status": "refused", "error": "not_merged"}
        return ({"outcome": "applied", "line": "Merged into " + str(res.get("ruled") or "one file") + ".",
                 "bucket": res.get("bucket")}, {"status": "merged"})
    if verb == "unmerge notes":
        import notes_merge

        res = notes_merge.unmerge_notes(root, n)
        if res.get("status") == "restored":
            line = "Put back." + (" Some could not be put back, their places being taken." if res.get("left") else "")
            return {"outcome": "applied", "line": line}, {"status": "undone"}
        if res.get("status") in ("changed", "already"):
            return {"outcome": "already", "line": TIDY_CHANGED_LINE}, {"status": "already_resolved"}
        return {"outcome": "refused", "line": TIDY_BACK_LINE}, {"status": "refused", "error": "no_merge"}
    if verb in ("file away", "put back"):
        # a loose file at a project's root into `deliverables/`, archive first,
        # and that move's Undo, named by the press it takes back
        import root_files

        if verb == "file away":
            res = root_files.move_to_deliverables(root, n, press=str(c.get("id") or ""))
            memo.pop("sets", None)
            if res.get("status") != "moved":
                return {"outcome": "refused", "line": TIDY_LEFT_LINE}, {"status": "refused", "error": "not_loose"}
            return ({"outcome": "applied", "line": "Moved into deliverables.", "bucket": str(c.get("id") or "")},
                    {"status": "updated"})
        res = root_files.put_back(root, str(c.get("back_of") or ""))
        if res.get("status") == "restored":
            return {"outcome": "applied", "line": "Put back."}, {"status": "undone"}
        if res.get("status") in ("changed", "already"):
            return {"outcome": "already", "line": TIDY_CHANGED_LINE}, {"status": "already_resolved"}
        if res.get("status") == "busy":
            return ({"outcome": "error", "line": "That file is open somewhere, so it was not moved back. Close it and press Undo again."},
                    {"status": "error", "error": "busy"})
        return {"outcome": "refused", "line": TIDY_BACK_LINE}, {"status": "refused", "error": "no_move"}
    import cleanup_actions as ca

    if verb == "tidy clutter":
        res = ca.tidy_folder(root)
        memo.pop("sets", None)
        if not res.get("bucket") or not res.get("moved"):
            return {"outcome": "already", "line": "Nothing to tidy; nothing was written."}, {"status": "noop"}
        return ({"outcome": "applied", "line": f"Tidied {int(res['moved'])}, kept in the archive.",
                 "bucket": res.get("bucket")}, {"status": "archived"})
    res = ca.undo_folder_tidy(root, n)
    if int(res.get("restored") or 0):
        return {"outcome": "applied", "line": "Put back."}, {"status": "undone"}
    return {"outcome": "already", "line": TIDY_CHANGED_LINE}, {"status": "already_resolved"}


def _tidy_status_of(root: Path, tid: str) -> str:
    from entities_io import entities_collection, read_entities_json

    for p in entities_collection(read_entities_json(root / "_hq" / "data" / "entities.json"), "projects"):
        if isinstance(p, dict) and str(p.get("id") or "") == tid:
            return str(p.get("status") or "").lower()
    return ""


def _tidy_touched_since(events: List[Dict[str, Any]], tid: str, seq: Any) -> bool:
    """Whether anything landed on a project after the row that made it: a
    row filed on it, a status change, or a change to its record."""
    if not isinstance(seq, int):
        return True
    for ev in events:
        s = ev.get("seq")
        if not isinstance(s, int) or s <= seq:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        named = {str(ev.get("primary_thread_id") or ""), str(d.get("primary_thread_id") or ""),
                 str(d.get("thread_id") or ""), str(d.get("target_id") or "")}
        named |= {str(x) for x in (ev.get("related_thread_ids") or []) if x}
        if tid in named:
            return True
    return False


def apply_week_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                       *, resolved_by: str = "", now_iso=None) -> Dict[str, Any]:
    """A WRITER. The Done tab's presses (cr1#780), each through its own rail,
    then ONE `apply_choices_applied` audit row:

      * `undo`: `brain_undo.undo_batch`, a reversal the owner pressed; a batch
        already reversed is left alone, and a partial one says so;
      * `bring back`: dormant or retired to active as the owner's act, the
        questions standing on it answered (`_bring_back`);
      * `archive`: `thread_archive.archive_thread`, the one archive path.

    A row of kind `tidy` (cr1#1584, the Tidy section's piles and their Undo)
    goes to `_apply_tidy_row`, which asks the pile's rule first.

    A project in any other state is left alone. A refusal of one row never
    stops the rest."""
    actor = str(resolved_by or "").strip() or _owner_id(workspace_root)
    rows = [c for c in (choices or []) if isinstance(c, dict)]
    if not actor:
        return {"ok": False, "reason": "no_actor", "line": NO_ACTOR_LINE,
                "results": [], "n_applied": 0, "n_errors": len(rows)}
    import brain_undo as bu
    import thread_archive as ta
    from apply_audit import build_apply_choices_applied_event
    from entities_io import entities_collection, read_entities_json
    from event_gate import append_event

    events_path = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"

    def status_of(tid: str) -> str:
        ent = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
        for p in entities_collection(ent, "projects"):
            if isinstance(p, dict) and p.get("id") == tid:
                return str(p.get("status") or "").lower()
        return ""

    results: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    tidy_memo: Dict[str, Any] = {}
    for c in rows:
        verb = str(c.get("action") or "").strip().lower()
        n = str(c.get("n") or "").strip()
        r: Dict[str, Any] = {"id": c.get("id"), "action": verb, "n": n}
        handler: Any = None
        try:
            if str(c.get("kind") or "") == "tidy":
                # cr1#1584: a Tidy pile's press or its Undo, asked against
                # the pile's own rule (or the write it takes back) first.
                said, handler = _apply_tidy_row(workspace_root, c, verb, n, actor, now_iso, tidy_memo)
                r.update(said)
            elif verb not in WEEK_VERBS or not n:
                r.update(outcome="error", line=UNKNOWN_VERB_LINE.format(verb=verb or "none"))
                handler = {"status": "error"}
            elif verb == "undo":
                ref = (c.get("payload") or {}).get("batch_ref")
                done = _week_undone(_review_events(workspace_root)).get(bu.batch_ref_label(ref or {}), 0)
                if done and done >= int((c.get("payload") or {}).get("n") or 1):
                    r.update(outcome="already", line=WEEK_ALREADY_LINE)
                    handler = {"status": "already_resolved"}
                else:
                    out = bu.undo_batch(str(workspace_root), ref, undone_by=actor, source_skill=COMMITMENT_SRC,
                                        user_confirmed=True) or {}
                    if int(out.get("n_undone") or 0) and not int(out.get("n_errors") or 0):
                        r.update(outcome="applied", line="Put back.")
                        handler = {"status": "undone"}
                    elif out.get("status") == "already" and not [
                            x for x in out.get("results") or []
                            if (x.get("result") or {}).get("status") == "not_open"]:
                        # Put back already, by a row that holds the same change.
                        r.update(outcome="already", line=WEEK_ALREADY_LINE)
                        handler = {"status": "already_resolved"}
                    elif not int(out.get("n_undone") or 0) and (out.get("results") or []) and all(
                            x.get("refused") for x in out.get("results") or [] if x.get("status") == "error"):
                        # cr1#1356: said by the refusal's own why.
                        # No refusal row at all keeps cr1#1322's line.
                        whys = {str(x.get("why") or "") for x in out.get("results") or [] if x.get("refused")}
                        why = "changed" if not whys else whys.pop() if len(whys) == 1 else ""
                        # cr1#1371: a refusal because the item is gone reads
                        # the not-found line, the same truth as below.
                        r.update(outcome="error", line=WEEK_NOT_FOUND_LINE if why == "not_found"
                                 else WEEK_REFUSED_LINES.get(why, WEEK_REFUSED_LINE))
                        handler = {"status": "error", "error": "changed_since" if why == "changed" else
                                   "not_found" if why == "not_found" else "refused_" + (why or "mixed")}
                    elif not int(out.get("n_undone") or 0) and [
                            x for x in out.get("results") or [] if x.get("status") == "error"] and all(
                            (x.get("result") or {}).get("status") == "not_found"
                            # cr1#1371: a refusal because the item is gone counts as gone too.
                            or (x.get("refused") and x.get("why") == "not_found")
                            for x in out.get("results") or [] if x.get("status") == "error"):
                        r.update(outcome="error", line=WEEK_NOT_FOUND_LINE)
                        handler = {"status": "error", "error": "not_found"}
                    else:
                        r.update(outcome="error", line=WEEK_PARTIAL_LINE)
                        handler = {"status": "error"}
            elif status_of(n) != "dormant" and not (verb == "bring back" and status_of(n) == "archived"):
                r.update(outcome="already", line="That one is no longer dormant; nothing was written.")
                handler = {"status": "already_resolved"}
            elif verb == "bring back":
                _bring_back(workspace_root, n, actor)
                r.update(outcome="applied", line="Back to active.")
                handler = {"status": "updated"}
            else:
                ta.archive_thread(str(workspace_root), n, reason="Archived on the Command Room Hub",
                                  source_skill=COMMITMENT_SRC)
                r.update(outcome="applied", line="Archived.")
                handler = {"status": "archived"}
        except Exception as exc:  # noqa: BLE001 - one row's failure never stops the batch
            r.update(_row_error(exc))
            handler = {"status": "error", "error": type(exc).__name__}
        results.append(r)
        entry = {"n": n, "action": verb, "handler_result": handler, "said": r.get("outcome")}  # cr1#1378
        if str(c.get("kind") or "") == "tidy":
            # cr1#1584: the press and the row its write made, so a run handed
            # the same press again answers from here (`_tidy_written`)
            entry.update(id=c.get("id"), seq=r.get("seq"), thread_id=r.get("thread_id"))
        audit.append(entry)
    if audit:
        ev = build_apply_choices_applied_event(source=COMMITMENT_SRC, actions=audit)
        ev["data"]["surface"] = WEEK_SURFACE
        append_event(events_path, ev, holder="cockpit_helpers")
    return {"ok": True, "results": results, "n_applied": sum(1 for r in results if r["outcome"] == "applied"),
            "n_errors": sum(1 for r in results if r["outcome"] == "error")}


# ---------------------------------------------------------------------------
# the Settings tab (cr1#781)
# ---------------------------------------------------------------------------

#: What the system has learned and every setting it works to, each with where
#: it came from, read from the same stores the skills read: never a second copy.
#: Learned rules and the question budget are editable from the first version
#: (owner ruling D3 of 2026-11-04); the rest is read-only here.
SETTINGS_VERBS = frozenset({"rule off", "rule delete", "rule edit", "budget"})
SETTINGS_SURFACE = "cockpit-settings"
SETTINGS_RULES_CAP = 60
SOURCE_SENTENCE = "your sentence"
SOURCE_CORRECTION = "a correction"
SOURCE_DEFAULT = "a default"
SOURCE_ON_FILE = "on file"
SOURCE_HUB = "your words on the Hub"
RULE_GONE_LINE = "That rule is no longer in force; nothing was written."
RULE_EDIT_NEEDS_TEXT_LINE = "An edit needs the new wording typed on its row; nothing was written."
RULE_NO_UNDO_LINE = "That rule shares its record with others and cannot be switched off from here; say `undo` in a chat."
BUDGET_UNKNOWN_LINE = "That is not one of the question budgets the system takes ({options}); nothing was written."
BUDGET_SAME_LINE = "The question budget already is that number; nothing was written."
HUB_EDIT_DETECTOR = "hub"


def _settings_source(stored: Optional[Dict[str, Any]]) -> str:
    """Where a skill_config value came from: the writer's own `origin`."""
    origin = str((stored or {}).get("origin") or "").strip().lower()
    if not stored:
        return SOURCE_DEFAULT
    if origin in ("tune", "asked", "user", "owner"):
        return SOURCE_SENTENCE
    if origin == "first_fire_defaults":
        return SOURCE_DEFAULT
    return SOURCE_ON_FILE if origin else SOURCE_SENTENCE


def _local_day(value, tzinfo) -> Optional[str]:
    """cr1#1643 review: a stored time as the day it fell on where the owner is,
    never its UTC day (its first ten characters): a rule confirmed on a
    Sunday evening in California is a Sunday rule. A date alone stays that
    day; nothing is None."""
    s = str(value or "").strip()
    if not s:
        return None
    at = _parse(s) if len(s) > 10 else None
    return at.astimezone(tzinfo).date().isoformat() if at is not None else (s[:10] or None)


def _rule_last_used(events: List[Dict[str, Any]], skill: str, applied_at) -> Optional[str]:
    """When a draft in the rule's skill was last made after the rule landed:
    the newest `email_drafted` row from that skill, else None."""
    best = None
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "email_drafted" or str(ev.get("source_skill") or "") != skill:
            continue
        at = _parse(ev.get("ts"))
        if at is None or (applied_at and at < applied_at):
            continue
        if best is None or at > best:
            best = at
    return _iso(best) if best else None


def _budget_options() -> List[Dict[str, Any]]:
    import quiet

    return [{"preset": p, "n": int(quiet.QUESTION_BUDGET[p])} for p in quiet.PRESET_LADDER]


def _apply_still_yours(workspace_root, rows, *, resolved_by: str = "", now_iso=None) -> Dict[str, Any]:
    """cr1#1706: the Apply run's "Still yours?" rows, through the review's
    own writer (`still_yours.review_apply`, on the door)."""
    import still_yours

    return still_yours.review_apply(workspace_root, rows, resolved_by=resolved_by, now_iso=now_iso)


def feed_stillyours(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. cr1#1706: the "Still yours?" review's document as its writers
    left it (`still_yours.feed`): at most the oldest 100 rows and the whole
    count, `stale` when built on an earlier day. Never builds it."""
    import still_yours

    return still_yours.feed(workspace_root, now_iso)


def _urgent_wall(workspace_root) -> Optional[Dict[str, Any]]:
    """Review round 1 of #1819 (must 1): the wall Urgent names people by, or
    None (Urgent holds). Since cr1#1830 the meeting wall itself holds on a
    mail-rules store that is there but cannot be parsed
    (`account_scope_gate.off_limits_readable`), so Urgent's wall is that one."""
    return _meeting_wall(workspace_root)


def _inbox_listed(feed: Any) -> set:
    """Review round 1 of #1819 (must 2): the threads and messages the Inbox
    feed just built lists, after the owner's hide and drop rules."""
    out: set = set()
    for r in (feed.get("items") or []) if isinstance(feed, dict) else []:
        if isinstance(r, dict):
            out |= {str(r.get(k) or "") for k in ("thread_id", "message_id")} - {""}
    return out


def _urgent_kept(items: Any, feed: Any) -> List[Dict[str, Any]]:
    """Review round 1 of #1819 (must 2): of the inbox read's raw items, only
    those the Inbox feed just built lists, so a sender the owner hid or a
    thread they dropped never reaches the Urgent store."""
    listed = _inbox_listed(feed)
    return [i for i in (items or []) if isinstance(i, dict)
            and ({str(i.get(k) or "") for k in ("thread_id", "message_id")} - {""}) & listed]


def feed_urgent(workspace_root=None, *, now_iso=None) -> Dict[str, Any]:
    """READ. cr1#1786: the Hub's Urgent document, `feed/urgent`. The ranking
    (`hub_urgent.urgent_view`) over the store the inbox part keeps
    (`hub_urgent_store`, ruling 127f): an item answered since (an
    `email_sent` on its thread) or older than the store's age limit drops
    out, and a deadline that has passed no longer counts. Up to three
    waiting-on-others rows always ride it (130f). Stamped `generated_at`.
    The wall again, as it stands now: nobody behind it is listed, and an
    unreadable wall lists nobody (`held`). No mail is read here (130e).

    Review round 1 of #1819: the wall is `_urgent_wall` (held when the
    hidden-people read is unread too), and `lines.held` is what the page says
    then, never "Nothing urgent". A calendar invite saved from mail is not
    ranked: the page offers invites from the calendar it holds, where the
    owner's answer is known, so an answered invite never ranks for the
    store's three days or doubles the calendar's row."""
    import hub_urgent as hu
    import hub_urgent_store as hus

    now_s = _iso(_now(now_iso))
    if workspace_root is None:
        out = hu.rank_urgent([], now=_now(now_s), owed_to_you=[])
        return dict(out, held=True, base_saved_at=None,
                    lines={"free": hu.NOTHING_URGENT_FREE_LINE, "clear": hu.NOTHING_URGENT_CLEAR_LINE})
    store = hus.load(workspace_root)
    wall = _urgent_wall(workspace_root)
    held = wall is None or store.get("held") is True
    try:
        import events_io

        events = events_io.load_events_org_scoped(workspace_root)[0]
    except Exception:  # noqa: BLE001 - an unread book answers nothing, so nothing counts as answered
        events = []
    items = [] if held else [i for i in hus.current(store, answered=hus.answered_threads(events), now_iso=now_s)
                             if not hus.walled(i, wall) and str(i.get("kind") or "") != "calendar_invite"]
    out = hu.urgent_view(workspace_root, items, owed=[] if held else None, now_iso=now_s)
    return dict(out, held=held, base_saved_at=store.get("saved_at"),
                lines={"free": hu.NOTHING_URGENT_FREE_LINE, "clear": hu.NOTHING_URGENT_CLEAR_LINE,
                       "held": hu.URGENT_HELD_LINE})


def feed_followups(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. cr1#1745: the follow-ups drafted after meetings and waiting for
    the owner's press (`followup_draft.feed_followups`): at most the newest
    few, each with its words, checked against the wall as it stands now. The
    words live in this doc only, never in the plate or the today feed."""
    import followup_draft

    return followup_draft.feed_followups(workspace_root, now_iso=now_iso)


def feed_pfollow(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. cr1#1789: Today's People follow-ups and key relationships, its
    own document (`feed/pfollow`) so no people row or plate row gains a
    field (ruling 111): `people_followups.build` over every person on file,
    fenced (not only the People tab's first rows), and the waiting follow-up drafts. It
    reads no Today feed and nothing of another task or session: the meetings
    that ended and are not written up, and Open prep, are joined on the page
    from its own Today feed (coordinator's ruling, cr1#1809's pattern). The
    module's order is the order; nothing is ranked here."""
    import people_followups

    return people_followups.build(workspace_root, now_iso=_iso(_now(now_iso)))


def feed_settings(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The Settings tab (cr1#781): every learned voice rule still in force
    or reversed (`learning_pass.learned_rules`), each with its one sentence,
    why, where it applies, when it was confirmed and when it was last used; the
    question budget as one number with the numbers it can be set to; and,
    read-only, each connector's kind, dials and lists, each mail account's
    role, the daily caps, the rest and let-go days, the learning bar, the chat
    persona and the voice blocks on file, and the usage guide (cr1#187). Every row names its source. Nothing
    here is a second copy: each section reads the store its skill reads. A
    section that cannot be read is named in `errors`, never shown empty."""
    import brain_proposals as bp
    import brain_undo as bu
    import chat_persona as cp
    import connector_config as cc
    import identity_reconcile as ir
    import learning_pass as learn
    import quiet
    from exit_doors import SILENCE_LET_GO_DAYS, SILENCE_PARK_DAYS
    import commitment_policy as policy
    from skill_config_writer import load_skill_config

    now = _now(now_iso)
    iso_now = _iso(now)
    ws = str(workspace_root)
    tzinfo = _workspace_tz(workspace_root)
    errors: List[str] = []
    events = _review_events(workspace_root)
    rules: List[Dict[str, Any]] = []
    try:
        for r in learn.learned_rules(ws, now_iso=iso_now):
            status = str(r.get("status") or "")
            if status not in ("live", "reversed"):
                continue  # a question is the Review tab's
            applied = _parse(r.get("applied_at"))
            own = bool(r.get("undo")) and len(bu.resolve_batch(ws, r["undo"])) == 1
            rid = bu.batch_ref_label(r["undo"]) if r.get("undo") else f"rule:{r.get('seq')}"
            rules.append({
                "id": rid, "seq": r.get("seq"), "status": status,
                "rule": str(r.get("rule") or "")[:200], "effect": str(r.get("effect") or "")[:300],
                "why": str(r.get("rests_on") or "")[:300], "where": str(r.get("skill") or ""),
                "op": str(r.get("op") or ""),
                "confirmed_at": _local_day(r.get("applied_at"), tzinfo),
                "asked_at": str(r.get("asked_at") or "")[:10] or None,
                "last_used": _rule_last_used(events, str(r.get("skill") or ""), applied),
                "source": SOURCE_HUB if r.get("corrections") == [] and str(r.get("rests_on") or "") == SOURCE_HUB else SOURCE_CORRECTION,
                "editable": status == "live" and own, "batch_ref": r.get("undo"),
                "verbs": ([{"action": "rule edit", "label": "Save wording", "ask": "The rule in your words"},
                           {"action": "rule off", "label": "Switch off"},
                           {"action": "rule delete", "label": "Delete"}] if status == "live" and own else []),
            })
    except Exception:  # noqa: BLE001 - said on the page, never shown as no rules
        errors.append("rules")
    budget: Dict[str, Any] = {}
    try:
        preset = quiet.effective_preset(ws)
        stored = quiet.stored_preset(ws)
        budget = {"preset": preset, "n": int(quiet.QUESTION_BUDGET[preset]), "options": _budget_options(),
                  "source": SOURCE_SENTENCE if stored else SOURCE_DEFAULT,
                  "stepped_down": bool(stored and stored != preset)}
    except Exception:  # noqa: BLE001
        errors.append("budget")
    connectors: List[Dict[str, Any]] = []
    accounts: List[Dict[str, Any]] = []
    try:
        for c in cc.declared_connector_settings(ws):
            connectors.append({"category": c.get("category"), "kind": c.get("kind"), "label": c.get("label") or c.get("provider") or c.get("server_id"),
                               "dials": dict(c.get("dials") or {}), "include": list(c.get("include") or []),
                               "exclude": list(c.get("exclude") or []), "scope": c.get("scope"), "source": SOURCE_SENTENCE})
        for a in cc.accounts(ws):
            role = str(a.get("role") or "unclassified")
            accounts.append({"address": str(a.get("address") or ""), "role": role,
                             "role_line": cc._ROLE_IN_ENGLISH.get(role, role), "dials": cc._dials_for(a),
                             "source": SOURCE_SENTENCE if role != "unclassified" else SOURCE_DEFAULT})
    except Exception:  # noqa: BLE001
        errors.append("connectors")
    caps = {"questions_a_day": {"n": int(bp.DAILY_CONFIRM_CAP), "source": SOURCE_DEFAULT},
            "connector_questions_a_day": {"n": int(bp.DAILY_CONNECTOR_QUESTION_CAP), "source": SOURCE_DEFAULT},
            "people_added_a_run": {"n": int(ir.STEADY_CAPS.get("auto_add", 0)), "source": SOURCE_DEFAULT},
            "merges_proposed_a_run": {"n": int(ir.STEADY_CAPS.get("merge_propose", 0)), "source": SOURCE_DEFAULT}}
    exits: Dict[str, Any] = {}
    try:
        silence_on = policy.flow_switch_enabled(ws, policy.EXIT_SILENCE_AGE_OUT_KEY)
        own_word_on = policy.flow_switch_enabled(ws, policy.EXIT_OWN_WORD_CLOSES_KEY)
        stored_policy = load_skill_config(ws, policy.PRESET_SKILL_KEY) or {}
        cfg = stored_policy.get("config") if isinstance(stored_policy.get("config"), dict) else {}
        exits = {"rests_after_days": int(SILENCE_PARK_DAYS), "let_go_after_days": int(SILENCE_LET_GO_DAYS),
                 "silence_age_out": silence_on, "own_word_closes": own_word_on,
                 "source": SOURCE_SENTENCE if any(k in cfg for k in (policy.EXIT_SILENCE_AGE_OUT_KEY, policy.EXIT_OWN_WORD_CLOSES_KEY)) else SOURCE_DEFAULT}
    except Exception:  # noqa: BLE001
        errors.append("exits")
    learning: Dict[str, Any] = {}
    try:
        th = learn.learning_thresholds(ws)
        stored_learn = load_skill_config(ws, learn.SETTINGS_NAME)
        learning = {"on": bool(learn.learning_enabled(ws)), "ask_at": int(th.get(learn.ASK_AT_KEY)),
                    "apply_at": int(th.get(learn.APPLY_AT_KEY)), "source": _settings_source(stored_learn)}
    except Exception:  # noqa: BLE001
        errors.append("learning")
    persona: List[Dict[str, Any]] = []
    try:
        saved = cp._load_saved(ws) or {}
        for knob, value in cp.get_chat_persona(ws).items():
            persona.append({"knob": knob, "value": value, "source": SOURCE_SENTENCE if knob in saved else SOURCE_DEFAULT})
    except Exception:  # noqa: BLE001
        errors.append("persona")
    voice: List[Dict[str, Any]] = []
    prep: List[Dict[str, Any]] = []
    try:
        import voice_corrections as vc

        for skill in vc.written_overrides(ws) or []:
            ov = vc.load_voice_block_override(ws, skill) or {}
            voice.append({"skill": skill, "last_refreshed": _local_day(ov.get("last_refreshed"), tzinfo),
                          "calibration_level": ov.get("calibration_level"), "sample_count": ov.get("sample_count"),
                          "source": SOURCE_CORRECTION})
        stored_prep = load_skill_config(ws, "call-prep") or {}
        pcfg = stored_prep.get("config") if isinstance(stored_prep.get("config"), dict) else {}
        for meeting_type, per_section in sorted((pcfg.get("section_weights") or {}).items()):
            if isinstance(per_section, dict):
                for section, weight in sorted(per_section.items()):
                    prep.append({"meeting_type": meeting_type, "section": section, "weight": weight, "source": SOURCE_CORRECTION})
    except Exception:  # noqa: BLE001
        errors.append("voice")
    guide: Dict[str, Any] = {}
    try:
        import usage_guide

        guide = usage_guide.guide(ws)
    except Exception:  # noqa: BLE001
        errors.append("guide")
    return _with_words(workspace_root, {"generated_at": iso_now, "rules": rules[:SETTINGS_RULES_CAP], "n_rules": len(rules), "guide": guide,
            "budget": budget, "connectors": connectors, "accounts": accounts, "caps": caps, "exits": exits,
            "learning": learning, "persona": persona, "voice": voice, "prep": prep, "errors": errors,
            # cr1#1389: the installed plugin version, which the page compares
            # with the version it was built from to show its Finish update bar.
            "installed_version": plugin_version_here(),
            # cr1#1784 part B: the workspace AI's name, for the Hub's greeting and
            # chat box only (ruling 130i); "" when none is set (the page says "Command Room").
            "ai_name": _settings_ai_name(workspace_root)})


def _settings_ai_name(workspace_root) -> str:
    """READ. `workspace.brain_name` from entities.json as the owner set it, or
    "" when unset or unreadable (never personification's default name)."""
    from entities_io import read_entities_json
    try:
        ent = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
    except Exception:  # noqa: BLE001
        return ""
    w = ent.get("workspace") if isinstance(ent, dict) else None
    name = w.get("brain_name") if isinstance(w, dict) else None
    return name.strip()[:40] if isinstance(name, str) else ""


def _rule_live(workspace_root, rid: str, now_iso: str) -> Optional[Dict[str, Any]]:
    """The learned rule the page named, from the book, only while it is live
    with its own record: a page's payload never picks the target."""
    import brain_undo as bu
    import learning_pass as learn

    for r in learn.learned_rules(str(workspace_root), now_iso=now_iso):
        if r.get("status") != "live" or not r.get("undo"):
            continue
        if bu.batch_ref_label(r["undo"]) == rid and len(bu.resolve_batch(str(workspace_root), r["undo"])) == 1:
            return r
    return None


def _rule_off(workspace_root, rule: Dict[str, Any], actor: str) -> Dict[str, Any]:
    import brain_undo as bu

    return bu.undo_batch(str(workspace_root), rule["undo"], undone_by=actor, source_skill=COMMITMENT_SRC,
                         user_confirmed=True) or {}


def _rule_edit(workspace_root, rule: Dict[str, Any], text: str, actor: str, events_path, now) -> Dict[str, Any]:
    """The owner's wording replaces the learned line: the learned change is
    taken back through its own reverser, then the sentence is written into the
    block under the rule's section with its own `voice_block_updated` record,
    so it is a live rule with its own Undo and `learned_rules` reads it back."""
    import voice_corrections as vc
    from event_gate import append_event
    from learning_pass import CLASS_VOICE, _baked_voice_block, _block_body

    undone = _rule_off(workspace_root, rule, actor)
    if not int(undone.get("n_undone") or 0) and undone.get("status") != "already":
        return {"status": "error", "undone": undone}
    skill = str(rule.get("skill") or "")
    op = str(rule.get("op") or "")
    existing = vc.load_voice_block_override(workspace_root, skill) or {}
    previous = existing.get("markdown") or None
    base = _block_body(previous) or _baked_voice_block(skill)
    section = vc.VOICE_OP_SECTIONS.get(op) or "Structure"
    bullet = "- " + text.strip().lstrip("-").strip()
    lines = base.split("\n")
    head = next((i for i, ln in enumerate(lines) if ln.strip().lower().startswith("### " + section.lower())), None)
    if head is None:
        lines += ["", "### " + section, bullet]
    else:
        end = head + 1
        while end < len(lines) and not lines[end].startswith("#"):
            end += 1
        while end > head + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, bullet)
    vc.write_voice_block_override(workspace_root, skill, "\n".join(lines),
                                  calibration_level=str(existing.get("calibration_level") or "calibrated"),
                                  sample_count=int(existing.get("sample_count") or 0))
    batch_id = f"hub_{now.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"
    append_event(events_path, {
        "type": "voice_block_updated", "source_skill": COMMITMENT_SRC,
        "data": {"skill": skill, "op": op, "section": section, "phrases": list(rule.get("items") or []) if op == vc.OP_BAN_PHRASE else [],
                 "delta": text, "rule": text, "effect": str(rule.get("effect") or ""), "rests_on": SOURCE_HUB,
                 "corrections": [], "item_fingerprints": [vc.voice_item_fingerprint(skill, op, item) for item in (rule.get("items") or [])],
                 "learned_line": bullet, "previous_markdown": previous, "had_override": bool(previous),
                 "fingerprint": vc.voice_proposal_fingerprint(skill, op), "proposal_id": rule.get("proposal_id"),
                 "confirmed_by": actor, "brain_batch_id": batch_id, "brain_change_class": CLASS_VOICE,
                 "detector": HUB_EDIT_DETECTOR}}, holder="cockpit_helpers")
    return {"status": "edited", "batch_id": batch_id}


def _delete_rule_items(workspace_root, rule: Dict[str, Any], events_path) -> int:
    """One `learned_line_deleted` row per item this rule learned (cr1#781):
    the item fingerprint is the key `learning_pass._deletion_times` reads, so
    the same correction never re-learns this rule, and nothing else in the
    skill is closed. The profile key rides on each row, as the profile page's
    own deletion rows carry it."""
    import voice_corrections as vc
    from event_gate import append_event
    from profile import DELETION_EVENT, DELETION_SOURCE_SKILL

    skill, op = str(rule.get("skill") or ""), str(rule.get("op") or "")
    prints = [vc.voice_item_fingerprint(skill, op, item) for item in (rule.get("items") or [])]
    rows = [{"type": DELETION_EVENT, "source_skill": DELETION_SOURCE_SKILL,
             "data": {"key": f"learned.voice.{skill}", "skill": skill, "op": op, "fingerprint": fp, "surface": SETTINGS_SURFACE}}
            for fp in prints if fp]
    if rows:
        append_event(events_path, rows, holder="cockpit_helpers")
    return len(rows)


def apply_settings_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                           *, resolved_by: str = "", now_iso=None) -> Dict[str, Any]:
    """A WRITER. The Settings tab's edits (cr1#781), each through the store's
    own writer, then ONE `apply_choices_applied` audit row:

      * `rule off`: `brain_undo.undo_batch` on the rule's own record, the
        registered reverser (the Done tab's Undo);
      * `rule delete`: the same, then one `learned_line_deleted` row per
        item the rule learned (its item fingerprints, the shape the learning
        job's reader excludes on), so THAT rule is not learned again from the
        same corrections and nothing wider is closed (the profile page's
        skill-wide deletion is deliberately not used here);
      * `rule edit`: the learned line taken back and the owner's wording
        written into the block with its own record (`_rule_edit`);
      * `budget`: `quiet.stamp_preset`, the one writer of the posture the
        budget is read from, undoable like every stamp.

    A rule no longer live, or one that shares its record with others, writes
    nothing. Any other verb is refused. A refusal of one row never stops the
    rest. Answers `{ok, results: [{id, outcome, line}], n_applied, n_errors}`."""
    actor = str(resolved_by or "").strip() or _owner_id(workspace_root)
    rows = [c for c in (choices or []) if isinstance(c, dict)]
    if not actor:
        return {"ok": False, "reason": "no_actor", "line": NO_ACTOR_LINE,
                "results": [], "n_applied": 0, "n_errors": len(rows)}
    import quiet
    from apply_audit import build_apply_choices_applied_event
    from event_gate import append_event

    now = _now(now_iso)
    iso_now = _iso(now)
    events_path = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
    results: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    for c in rows:
        verb = str(c.get("action") or "").strip().lower()
        n = str(c.get("n") or "").strip()
        text = str(c.get("input") or "").strip()
        r: Dict[str, Any] = {"id": c.get("id"), "action": verb, "n": n}
        handler: Any = None
        try:
            if verb not in SETTINGS_VERBS or not n:
                r.update(outcome="error", line=UNKNOWN_VERB_LINE.format(verb=verb or "none"))
                handler = {"status": "error"}
            elif verb == "budget":
                options = {str(o["n"]): o["preset"] for o in _budget_options()}
                preset = options.get(text or n.split(":", 1)[-1])
                if not preset:
                    r.update(outcome="refused", line=BUDGET_UNKNOWN_LINE.format(options=", ".join(sorted(options, key=int))))
                    handler = {"status": "refused"}
                else:
                    out = quiet.stamp_preset(str(workspace_root), preset, origin="tune",
                                             triggered_by=SETTINGS_SURFACE, now_iso=iso_now)
                    if out.get("ran"):
                        r.update(outcome="applied", line=f"Question budget set to {int(quiet.QUESTION_BUDGET[preset])} a week.")
                        handler = {"status": "updated", "preset": preset}
                    else:
                        r.update(outcome="already", line=BUDGET_SAME_LINE)
                        handler = {"status": "already_resolved"}
            else:
                rule = _rule_live(workspace_root, n, iso_now)
                if rule is None:
                    r.update(outcome="already", line=RULE_GONE_LINE)
                    handler = {"status": "already_resolved"}  # cr1#1161: a no-op, not an error
                elif verb == "rule edit" and not text:
                    r.update(outcome="refused", line=RULE_EDIT_NEEDS_TEXT_LINE)
                    handler = {"status": "needs_input"}
                elif verb == "rule edit":
                    handler = _rule_edit(workspace_root, rule, text, actor, events_path, now)
                    if handler.get("status") == "edited":
                        r.update(outcome="applied", line="Saved in your words.")
                    else:
                        r.update(outcome="error", line=ROW_ERROR_LINE)
                else:
                    out = _rule_off(workspace_root, rule, actor)
                    ok = bool(int(out.get("n_undone") or 0)) and not int(out.get("n_errors") or 0)
                    if not ok and out.get("status") != "already":
                        r.update(outcome="error", line=WEEK_PARTIAL_LINE)
                        handler = {"status": "error"}
                    elif verb == "rule delete":
                        n_rows = _delete_rule_items(workspace_root, rule, events_path)
                        r.update(outcome="applied", line="Deleted, and not learned again.")
                        handler = {"status": "deleted", "deletion_rows": n_rows}
                    else:
                        r.update(outcome="applied", line="Switched off.")
                        handler = {"status": "undone"}
        except Exception as exc:  # noqa: BLE001 - one row's failure never stops the batch
            r.update(_row_error(exc))
            handler = {"status": "error", "error": type(exc).__name__}
        results.append(r)
        audit.append({"n": n, "action": verb, "handler_result": handler, "said": r.get("outcome")})  # cr1#1378
    if audit:
        ev = build_apply_choices_applied_event(source=COMMITMENT_SRC, actions=audit)
        ev["data"]["surface"] = SETTINGS_SURFACE
        append_event(events_path, ev, holder="cockpit_helpers")
    return {"ok": True, "results": results, "n_applied": sum(1 for r in results if r["outcome"] == "applied"),
            "n_errors": sum(1 for r in results if r["outcome"] == "error")}


def _chase_offers(workspace_root, opens: List[Dict[str, Any]], origins: Dict[str, Any],
                  now: _dt.datetime, hp: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """`{commitment_id: offer}` for each late row owed to the owner, with a
    counterparty, that is not snoozed or waiting for the owner's yes
    (cr1#219): `chase_policy.chase_offer` over the book. Reads only; the offer
    is text the row carries until the owner presses."""
    import chase_policy as cp
    from commitment_parties import counterparty_count
    from commitment_state import BUCKET_OWED_TO_YOU, _now_calendar_day, bucket_of, is_overdue
    from contact_capture import own_addresses
    from cru_match import _commitment_field, _commitment_id, partition_subitems
    from due_reanchor import parse_date
    from entities_io import entities_collection, read_entities_json
    from events_io import load_events_owner_scoped
    from mute_ledger import active_dismissal_target_ids
    from people_writer import get_person_emails

    owner = _owner_id(workspace_root)
    if not owner:
        return {}
    day = _now_calendar_day(_iso(now), workspace_path=str(workspace_root))
    today = day or now.date()
    ent = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
    people = {p["id"]: p for p in entities_collection(ent, "people") if isinstance(p, dict) and p.get("id")}
    orgs = {o["id"]: o for o in entities_collection(ent, "orgs") if isinstance(o, dict) and o.get("id")}
    addresses = {pid: [a.strip().lower() for a in get_person_emails(p)] for pid, p in people.items()}
    own = {str(a).strip().lower() for a in own_addresses(workspace_root)} | set(addresses.get(owner) or [])
    events = load_events_owner_scoped(str(workspace_root))[0]
    muted = active_dismissal_target_ids(events, _iso(now))
    tzinfo = _workspace_tz(workspace_root)

    def day_of(value) -> Optional[_dt.date]:
        at = _parse(value)
        return at.astimezone(tzinfo).date() if at else None

    touches, heard = cp.chase_touches(events, addresses, day_of, today)
    chased = cp.row_chases(events, day_of)
    policy = cp.load_chase_policy(workspace_root)
    since = _iso(now - _dt.timedelta(days=cp.OUTCOME_WINDOW_DAYS))
    gaps: Dict[str, List[float]] = {}
    for o in cp.load_email_outcomes(workspace_root, since_iso=since):
        if o.get("outcome") == "replied" and isinstance(o.get("latency_days"), (int, float)):
            gaps.setdefault(str(o.get("recipient") or "").strip().lower(), []).append(float(o["latency_days"]))
    out: Dict[str, Dict[str, Any]] = {}
    # cr1#1012: a row is offered a chase as late only when the open list
    # counts it overdue.
    counted_ids = _counted_ids(workspace_root, now)
    for ev in partition_subitems(opens)[0]:
        cid = str(_commitment_id(ev))
        if bucket_of(ev, owner) != BUCKET_OWED_TO_YOU or cid in muted \
                or cid not in counted_ids \
                or not counterparty_count(ev, workspace_root=str(workspace_root)):
            continue
        pid = str(_commitment_field(ev, "owner_id") or "")
        # cr1#1738: no draft to someone off limits or personally tied; none at all with the fence unread
        if hp is not None and (hp.get("unread") or pid in (hp.get("ids") or ())):
            continue
        rec = people.get(pid)
        rtype = str((rec or {}).get("relationship_type") or (orgs.get((rec or {}).get("org_id")) or {}).get("relationship_type") or "")
        lat = [g for a in addresses.get(pid, []) for g in gaps.get(a, [])]
        window, escalate = cp.person_chase_days(rec, policy, lat, rtype), cp.get_chase_window(policy, rtype)[1]
        origin = origins.get(cid) or {}
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        row = {"title": d.get("title") or d.get("summary"), "due": parse_date(_commitment_field(ev, "due")),
               "source": origin.get("source") or "",
               "source_day": None if origin.get("captured") else parse_date(origin.get("date"))}
        mine = set(addresses.get(pid, []))
        out[cid] = cp.chase_offer(row, person=rec, addresses=addresses.get(pid, []), own_addresses=own, days=window,
                                  escalate_after=escalate, last_touch=touches.get(pid), last_heard=heard.get(pid),
                                  chases=[day for day, to in chased.get(cid, []) if to in mine], today=today)
    return out


def _counted_ids(workspace_root, now: _dt.datetime) -> set:
    """THE Hub's answer to "is this row overdue": the ids the open list's
    overdue number counts (`open_list.in_headline_overdue`, the owner's
    ruling of 2026-11-11). Every Hub path that marks, orders, counts or
    offers a row as late reads this, never a date test of its own
    (cr1#1012). Raises when the list cannot be read; each caller then shows
    no late answer rather than a guessed one."""
    from open_list import counted_ids
    return counted_ids(workspace_root, _iso(now))


def _overdue_facts(workspace_root, rows: List[Dict[str, Any]], opens: List[Dict[str, Any]],
                   origins: Dict[str, Any], now: _dt.datetime) -> Tuple[List[Dict[str, Any]], int]:
    """What the Overdue tab needs (cr1#877), from the set the plate draws.
    Stamps each row with `late` (the Projects row's own test, on the owner's
    local day), the book's direction (`bucket_of`), its day, the phrase that
    states its age, who is on the other end, and `snoozed` (a live mute: a
    snooze on a row owed to the owner hides it and leaves the date alone, and
    the plate does not read mutes). Answers `(late_rows, n_hidden)`: the rows
    past their date that `rows[:PLATE_ROW_CAP]` leaves out, because the list
    cut them, the cap parked them, or a copy folded them into a row that is
    not on the tab itself. Raises on a book it cannot read; the feed then says
    the tab has no answer, rather than a wrong one."""
    from cru_match import _commitment_field, _commitment_id, partition_subitems
    from commitment_parties import counterparty_ids, primary_counterparty_name
    from commitment_state import BUCKET_OWED_TO_YOU, _now_calendar_day, bucket_of, is_overdue
    from due_reanchor import parse_date, render_due_phrase
    from events_io import load_events_owner_scoped
    from mute_ledger import active_dismissal_target_ids
    from plate_view import NO_PROJECT_KEY, TIER_OBSERVED, _entities, _names_by_id, project_group, row_title

    owner = _owner_id(workspace_root)
    day = _now_calendar_day(_iso(now), workspace_path=str(workspace_root))
    today = day.isoformat() if day else _iso(now)[:10]
    muted = active_dismissal_target_ids(load_events_owner_scoped(str(workspace_root))[0], _iso(now))
    # cr1#1012: which rows the overdue number counts is the open list's own
    # verdict, stamped on each row, never re-derived here or on the page.
    counted_ids = _counted_ids(workspace_root, now)
    names = _person_names(workspace_root)
    threads = _names_by_id(_entities(Path(str(workspace_root))), "threads")

    def facts(ev: Dict[str, Any]) -> Dict[str, Any]:
        bucket = bucket_of(ev, owner)
        raw = _commitment_field(ev, "due")
        due = parse_date(raw)
        other = [str(_commitment_field(ev, "owner_id") or "")] if bucket == BUCKET_OWED_TO_YOU else counterparty_ids(ev)
        # review of #1733 round 3: an id not on file falls back to the typed name, as
        # _counterparty_line does, and only an id on file is the row's who_id
        on_file = bool(other) and bool(names.get(other[0]))
        # follow-up of #1733 round 3: a row owed to the owner falls back to its typed
        # owner_name, as plate_view's _counterparty_line does; any other to the counterparty's
        typed = (_commitment_field(ev, "owner_name") if bucket == BUCKET_OWED_TO_YOU
                 else primary_counterparty_name(ev, workspace_root=str(workspace_root)))
        who = (names.get(other[0], "") if on_file else "") or str(typed or "")
        return {"late": is_overdue(raw, today), "bucket": bucket, "due": due.isoformat() if due else "",
                "due_phrase": render_due_phrase(due.isoformat(), today) if due else "",
                "who": str(who)[:120], "snoozed": str(_commitment_id(ev)) in muted,
                # review of #1733 round 2: who it is, by id, so the row is judged by id, never by a namesake
                "who_id": str(other[0]) if on_file else "",
                "counted": str(_commitment_id(ev)) in counted_ids}

    top, _steps = partition_subitems(opens)
    by_id = {str(r.get("id")): r for r in rows}
    stamped = [(ev, facts(ev)) for ev in top]  # all or none: a raise leaves no row half stamped
    for ev, extra in stamped:
        row = by_id.get(str(_commitment_id(ev)))
        if row is not None:
            row.update(extra)
    # A copy folded into a late row is that row's "+1 more"; a counted row
    # says how many promises it stands for, so the panel's parts add up to
    # the count.
    # cr1#1012: only a COUNTED line covers its copies; a copy under a line
    # that is not counted (snoozed, or waiting for a yes) is listed itself.
    covered = {str(f) for r in rows if r.get("counted") for f in r.get("folded_ids") or []}
    for r in rows:
        if r.get("counted"):
            r["n_counted"] = 1 + sum(1 for f in r.get("folded_ids") or [] if str(f) in counted_ids)
    sent = {str(r.get("id")) for r in rows[:PLATE_ROW_CAP]}
    late: List[Dict[str, Any]] = []
    for ev, extra in stamped:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        cid = str(_commitment_id(ev))
        # cr1#1012: the panel lists what the overdue number counts, by the
        # open list's verdict; a row waiting for the owner's yes is not late
        # (the ruling of 2026-11-11) and stays on Commitments to confirm.
        if cid in sent or cid in covered or not extra["counted"]:
            continue
        late.append(by_id.get(cid) or {
            "id": cid, "n": None, "title": row_title(d.get("title"), d.get("summary"))[:240], "lane": "",
            "project": project_group(ev.get("primary_thread_id") or NO_PROJECT_KEY, threads)[1][:120],
            "context": " · ".join(x for x in (extra["due_phrase"], extra["who"]) if x)[:160],
            "horizon": "overdue", "pending": extra["bucket"] == "unconfirmed",
            "origin": origins.get(cid), **extra})
        late[-1]["n_counted"] = 1 + sum(1 for f in late[-1].get("folded_ids") or [] if str(f) in counted_ids)
    late.sort(key=lambda r: (str(r.get("due") or ""), str(r.get("id") or "")))
    return late[:OVERDUE_EXTRA_CAP], max(0, len(late) - OVERDUE_EXTRA_CAP)


def _due_day(due: Optional[_dt.date], today: _dt.date) -> str:
    """The words for an item's day on the top line: "today", "tomorrow",
    the weekday inside the week, else the month and day; "" when undated
    or past its date."""
    if due is None or due < today:
        return ""
    left = (due - today).days
    if left == 0:
        return "today"
    if left == 1:
        return "tomorrow"
    if left < 7:
        return due.strftime("%A")
    return f"{due.strftime('%B')} {due.day}"


#: cr1#1636 item 2: the statuses that end a line of work for the Today
#: headline. Not `thread_cards.DONE_STATUSES` whole (review of cr1#1636): a
#: `resolved` or `completed` thread can be a won deal (`deal_state.close_deal`)
#: whose next steps are live work.
NOT_LIVE_STATUSES = ("archived", "retired", "deleted", "merged")


def _dead_threads(workspace_root) -> set:
    """READ. The ids of every thread that is not live work (cr1#1636 item 2):
    its own status, or its company's, is one of `NOT_LIVE_STATUSES`. A
    thread's parent does not carry over (review of cr1#1636): the Work tab
    and the thread cards show a live child under a closed parent as live, and
    so does this. The company is read the canonical way, `org_id` then the
    older `affiliation_id` (`thread_writer.thread_org_id`). An unreadable
    record raises; the caller then leaves every row live, as before."""
    from entities_io import entities_collection, read_entities_json
    from thread_writer import thread_org_id

    ent = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
    orgs = {str(o["id"]): o for o in entities_collection(ent, "orgs") if isinstance(o, dict) and o.get("id")}

    def over(rec: Any) -> bool:
        return isinstance(rec, dict) and str(rec.get("status") or "").strip().lower() in NOT_LIVE_STATUSES

    return {str(t["id"]) for t in entities_collection(ent, "threads")
            if isinstance(t, dict) and t.get("id") and (over(t) or over(orgs.get(str(thread_org_id(t) or ""))))}


def _stamp_live(workspace_root, rows: List[Dict[str, Any]], opens: List[Dict[str, Any]]) -> None:
    """READ. Marks each plate row `live` (cr1#1636 item 2): False when the
    thread its commitment sits on is not live work (`_dead_threads`), else
    True; a row on no thread is live. The Today headline, Do this first and
    Needs you name live work only, and the top line's ranking leaves a row
    that is not live out (`_lead_rows`). Never writes."""
    from cru_match import _commitment_id

    dead = _dead_threads(workspace_root)
    thread_of = {str(_commitment_id(ev)): str(ev.get("primary_thread_id") or "") for ev in opens}
    for r in rows:
        r["live"] = thread_of.get(str(r.get("id")), "") not in dead


def _lead_rows(workspace_root, rows: List[Dict[str, Any]], opens: List[Dict[str, Any]],
               now: _dt.datetime) -> List[Dict[str, Any]]:
    """READ. The top line's ranked items (cr1#1290), from the owner's own
    rows (`rows`, the plate's do-it and confirm lanes). Each scores on how
    many confirmed priorities its threads are built into (the Week board's
    own test: the row's threads against each priority's `built_from`), on
    being owed to a person starred on the People tab, and on being due
    inside the lead time its work needs. Overdue only breaks ties. A row
    with no score is left out, so the page falls back to its first thing to
    move. Never writes."""
    import thread_cards as tc
    from commitment_parties import counterparty_ids
    from cru_match import _commitment_field, _commitment_id, partition_subitems
    from due_reanchor import parse_date
    from people_view import load_stars, starred_ids

    root = Path(str(workspace_root))
    today = now.astimezone(_workspace_tz(workspace_root)).date()
    owner = _owner_id(workspace_root)
    by_id = {str(_commitment_id(ev)): ev for ev in opens}
    steps: Dict[str, int] = {}
    for sub in partition_subitems(opens)[1]:
        pid = str((sub.get("data") or {}).get("parent_id") or "")
        steps[pid] = steps.get(pid, 0) + 1
    threads_of: Dict[str, set] = {}
    try:
        per_thread = tc._open_by_thread(root, _iso(now))[0]
    except Exception:  # noqa: BLE001 - an unread open list leaves the threads to each row's own record
        per_thread = {}
    for tid, v in per_thread.items():
        for key in ("you_owe", "owed_to_you", "unowned", "to_confirm"):
            for r in v.get(key) or []:
                if r.get("id"):
                    threads_of.setdefault(str(r.get("id")), set()).add(str(tid))
    priorities = [{str(t) for t in p.get("built_from") or []}
                  for p in tc.priorities_view(root, _iso(now)).get("confirmed") or []]
    stars = set(starred_ids(load_stars(root))) - {owner}
    out: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("live") is False:   # cr1#1636 item 2: never a row from work that is over
            continue
        cid = str(r.get("id"))
        ev = by_id.get(cid) or {}
        ths = set(threads_of.get(cid) or set())
        if ev.get("primary_thread_id"):
            ths.add(str(ev["primary_thread_id"]))
        n_prio = sum(1 for p in priorities if p & ths)
        key = any(pid in stars for pid in counterparty_ids(ev)) if ev else False
        due = parse_date(r.get("due") or (_commitment_field(ev, "due") if ev else None))
        n_steps = steps.get(cid, 0)
        work = n_steps > 0 or bool(LEAD_WORK_RE.search(str(r.get("title") or "")))
        lead_days = min(LEAD_MAX_DAYS, LEAD_DELIVERABLE_DAYS + max(0, n_steps - 1)) if work else LEAD_PLAIN_DAYS
        left = (due - today).days if due else None
        window = left is not None and 0 <= left <= lead_days
        score = (LEAD_PRIORITY_POINTS * min(n_prio, LEAD_PRIORITIES_COUNTED)
                 + (LEAD_KEY_PERSON_POINTS if key else 0) + (LEAD_WINDOW_POINTS if window else 0))
        if score <= 0:
            continue
        late = bool(r["counted"]) if "counted" in r else str(r.get("horizon") or "") == "overdue"
        out.append({"id": r.get("id"), "n": r.get("n"), "title": r.get("title"), "project": r.get("project"),
                    "due": due.isoformat() if due else "", "days_left": left, "due_day": _due_day(due, today),
                    "priorities": n_prio, "key_person": key, "lead_days": lead_days, "late": late,
                    "score": score, "order": r.get("order")})
    out.sort(key=lambda x: (-x["score"], not x["late"], x["due"] or "9999-12-31", x["order"] or 0))
    return out[:LEAD_CAP]


def feed_plate(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """A WRITER (building the plate mints display numbers). The Commitments
    tab, the Overdue tab and the Today tab's Move these today: the plate's own
    data view, serialized, never re-derived (`plate_view.plate_data_view` is
    the one grouping). Rows beyond `PLATE_ROW_CAP` are counted, not sent, but
    for the rows past their date, which ride `late_rows` for the Overdue tab
    (cr1#877; `_overdue_facts`)."""
    from plate_view import plate_data_view

    now = _now(now_iso)
    dv = plate_data_view(str(workspace_root), now_iso=_iso(now), measure_pages=False,
                         source_skill=COMMITMENT_SRC)
    rows = _plate_rows(dv)
    # cr1#725: each row's own record, read once, from the same open set the
    # plate draws (personal rows included: the page is the owner's own).
    # Nothing here is fetched live.
    opens: Optional[List[Dict[str, Any]]] = None
    _hp = _hidden_people(workspace_root)
    try:
        from cru_match import _commitment_id, load_open_commitments

        ep = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
        names = _person_names(workspace_root)
        owner = _owner_id(workspace_root)
        tzinfo = _workspace_tz(workspace_root)
        opens = load_open_commitments(ep) if ep.exists() else []
        # fix check of round 1 of #1717, cr1#1721: off limits and personal ties
        # are never named; an unread fence or ties names nobody (`hub_work.hidden_people`)
        _ties = None if _hp["unread"] else _hp["ids"]
        origins = {str(_commitment_id(ev)): _row_origin(ev, names, owner, tzinfo, _ties, _hp) for ev in opens}
    except Exception:  # noqa: BLE001 - a bad record leaves the note off, never the plate
        origins = {}
    # review round 1 of #1700: each row's project by id (the open list's), for Put in...
    try:
        projects = _projects_of(workspace_root, now)
    except Exception:  # noqa: BLE001
        projects = {}
    # ...and which side of the personal line the item is on: the fence's own
    # read, or a project that is not a work one (fix check of round 1)
    try:
        _fence, lane_personal = _lanes(workspace_root, projects)
    except Exception:  # noqa: BLE001
        _fence, lane_personal = None, set()
    for r in rows:
        r["origin"] = origins.get(str(r.get("id")))
        if projects.get(str(r.get("id"))):
            r["project_id"] = projects[str(r.get("id"))]
        r["personal_lane"] = str(r.get("id")) in lane_personal
    # cr1#1636 item 2: whether each row sits on live work, read before
    # anything ranks the rows (`_lead_rows` leaves one that is not out).
    try:
        _stamp_live(workspace_root, rows, opens or [])
    except Exception:  # noqa: BLE001 - an unread record leaves every row live, as before
        pass
    late_rows: List[Dict[str, Any]] = []
    n_late_hidden = 0
    overdue_ok = False
    try:
        if opens is not None and _owner_id(workspace_root):
            late_rows, n_late_hidden = _overdue_facts(workspace_root, rows, opens, origins, now)
            overdue_ok = True
    except Exception:  # noqa: BLE001 - the Overdue tab says it has no answer; the plate still draws
        pass
    # cr1#1658 change 5: what a Later hid until a day, for the Later view.
    # None when it cannot be read, so the page says so instead of "nothing".
    try:
        later_rows, n_later = _later_rows(workspace_root, opens or [], _owner_id(workspace_root), now) \
            if opens is not None else ([], None)
    except Exception:  # noqa: BLE001 - an unread ledger lists nothing and says so
        later_rows, n_later = [], None
    do_it = [r for r in rows if r["lane"] in ("do_it", "confirm")]
    # cr1#1012: a row leads "Move these today" as overdue only when the open
    # list counts it; a row past its date that is not counted (waiting for a
    # yes, snoozed) ranks with this week's rows. An older run with no verdict
    # keeps the plate's own horizon.
    def _rank(r: Dict[str, Any]) -> int:
        if "counted" in r:
            return 0 if r["counted"] else 1 if r["horizon"] in ("overdue", "past_due", "this_week") else 2
        return {"overdue": 0, "this_week": 1}.get(str(r["horizon"]), 2)
    focus = sorted(do_it, key=lambda r: (_rank(r), r["order"]))[:FOCUS_CAP]
    # cr1#1290: the top line's own ranking; "Move these today" keeps its order
    try:
        lead = _lead_rows(workspace_root, do_it, opens or [], now)
    except Exception:  # noqa: BLE001 - no ranking leaves the top line on the first thing to move
        lead = []
    # cr1#1294: the confirmed priorities with nothing open, for the top line
    try:
        goals = _goal_lines(workspace_root, _iso(now))
    except Exception:  # noqa: BLE001 - an unread priority list adds no line, never a guessed one
        goals = {"rows": [], "n": 0}
    chase = [r for r in rows if r["lane"] == "chase"][:CHASE_CAP]
    schedule = [r for r in rows if r["lane"] == "schedule"][:SCHEDULE_CAP]
    counts = dict(dv.get("plate_counts") or {})
    # cr1#1012 (train 2; closes cr1#976): the numbers the page shows come
    # from the open list, the one count the brief and chat read too, never
    # from the rows sent (the 150-row cap made "150" a cap, not a count).
    # A list that cannot be read leaves the key out and the page counts its
    # rows as before.
    try:
        from open_list import headline_counts
        counts["open_list"] = headline_counts(workspace_root, now_iso=_iso(now))
    except Exception:  # noqa: BLE001 - never a guessed number
        pass
    feed = {
        "generated_at": _iso(now),
        "header": str(dv.get("header") or ""),
        "quick_read": str(dv.get("quick_read") or ""),
        "counts": counts,
        "block_totals": dict(dv.get("block_totals") or {}),
        "focus": focus, "lead": lead, "chase": chase, "schedule": schedule,
        "goals": goals["rows"], "n_goals": goals["n"],
        "rows": rows[:PLATE_ROW_CAP], "n_rows": len(rows),
        "n_hidden": max(0, len(rows) - PLATE_ROW_CAP),
        "late_rows": [dict(x, personal_lane=str(x.get("id")) in lane_personal, **({"project_id": projects[str(x.get("id"))]} if projects.get(str(x.get("id"))) else {})) for x in late_rows],
        # review round 1 of #1700: rows name their project by id; with no fence read, Put in... is not offered
        "project_ids": True, "lanes_known": _fence is not None,
        "n_late_hidden": n_late_hidden, "overdue_ok": overdue_ok,
        "later_rows": later_rows, "n_later": n_later,
        # cr1#1017 behaviour 4: what opened and closed, with times, so Today
        # shows only what changed since the owner last looked
        "changes": _changes(workspace_root, now),
        # cr1#1017 behaviour 3: the workspace's own today, so Today can tell
        # a row due today from the backlog without the browser clock
        "today": now.astimezone(_workspace_tz(workspace_root)).date().isoformat(),
    }
    try:
        feed["done"] = _done_for_you(workspace_root, now)
    except Exception:  # noqa: BLE001 - an unread log leaves the strip off, never a guessed count
        feed["done"] = {"kinds": {}, "errors": ["done"]}
    try:
        offers = _chase_offers(workspace_root, opens or [], origins, now, hp=_hp)
    except Exception:  # noqa: BLE001 - a bad record takes the press off the row, never the plate
        offers = {}
    seat = hub_words(workspace_root)
    import plate_view as _pv
    # Every list the rows travel in, the capped ones included: a row past
    # PLATE_ROW_CAP can still ride `focus` or `chase` (cr1#1017 part 1b).
    for r in rows + list(feed.get("late_rows") or []) + list(focus) + list(lead) + list(chase):
        if isinstance(r, dict) and r.get("project") == _pv.NO_PROJECT_LABEL:
            r["project"] = "No " + _seat_word(seat, "thread")
    for r in feed["rows"] + feed.get("late_rows", []):
        offer = offers.get(str(r.get("id")))
        if offer:
            r["chase"] = None if offer.get("note") else offer
            r["chase_note"] = offer.get("note") or ""
    # cr1#1540: the Work tab's plate and piles, an optional block read from
    # the open list and the exit rules as they stand (`hub_work`). A feed
    # without it gives the page no pile; nothing above reads it.
    try:
        import hub_work
        feed["work"] = hub_work.work_block(workspace_root, _iso(now))
    except Exception:  # noqa: BLE001 - no block: the page then offers no pile
        pass
    # review of #1733, item 1: the rows themselves name nobody hidden either
    # (their who and the name part of their context), in every list they ride
    for lst in (feed["rows"], feed.get("late_rows") or [], focus, lead, chase, schedule):
        _hide_people_in_rows(lst, _hp)
    # review of #1733 round 3: who_id served the scrub only; it never ships (the
    # heavy plate came to 217,954 bytes with it, over REFRESH_DOC_LIMIT)
    for lst in (feed["rows"], feed.get("late_rows") or [], focus, lead, chase, schedule):
        for r in lst or []:
            if isinstance(r, dict):
                r.pop("who_id", None)
    # cr1#1751: last, after every pass that reads or rewrites a row, the rows
    # travel lean
    return _with_words(workspace_root, _lean_plate(feed), seat)


#: cr1#1751: the plate's rows travel lean. On a heavy book the plate sat at
#: 98.6% of `REFRESH_DOC_LIMIT`, and one more field on every row took it over,
#: so the Hub kept its morning copy all day. Every released page (5.34.0
#: onward) was read for each field below before it was left off.
#: The lists whose rows share the plate row's shape. `lead` and `later_rows`
#: are another shape and are not touched.
PLATE_LEAN_LISTS: Tuple[str, ...] = ("rows", "late_rows", "focus", "chase", "schedule")
#: No released page reads these; the same words are inside `context`.
PLATE_ROW_UNREAD: Tuple[str, ...] = ("due_phrase",)
#: A field holding exactly this value is left off: every released page reads
#: a missing one as the same value (`r.folded_ids || []`, `!r.snoozed`,
#: `r.n_counted ? ... : 1`). `counted` is NOT here: pages ask whether the
#: field is there (`"counted" in r`), so it always travels. Nor is `live`:
#: a row with no `live` is one whose thread could not be read.
PLATE_ROW_DEFAULTS: Dict[str, Any] = {"folded_ids": [], "snoozed": False, "pending": False,
                                      "personal_lane": False, "n_counted": 1}
#: The same, inside a row's `origin` (`o.line ? ... : "No source line..."`).
PLATE_ORIGIN_DEFAULTS: Dict[str, Any] = {"line": "", "paraphrase": False}


def _is_default(val: Any, default: Any) -> bool:
    """Exactly the default, by type too: 0, None or "" is never taken for
    False, nor 1 for True."""
    return type(val) is type(default) and val == default


def _lean_plate(feed: Any) -> Any:
    """READ. The plate feed with each row lean (cr1#1751), in place; a
    second pass changes nothing. A row keeps every field that says
    something: only a field no page reads, or one holding the value a page
    takes a missing field for, is left off. Anything that is not a row is
    handed back as it came."""
    if not isinstance(feed, dict):
        return feed
    for key in PLATE_LEAN_LISTS:
        rows = feed.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for name in PLATE_ROW_UNREAD:
                row.pop(name, None)
            for name, default in PLATE_ROW_DEFAULTS.items():
                if name in row and _is_default(row[name], default):
                    del row[name]
            origin = row.get("origin")
            if isinstance(origin, dict) and any(
                    k in origin and _is_default(origin[k], d) for k, d in PLATE_ORIGIN_DEFAULTS.items()):
                # a new record: the row's own source record is read by others in this call
                row["origin"] = {k: v for k, v in origin.items()
                                 if not (k in PLATE_ORIGIN_DEFAULTS and _is_default(v, PLATE_ORIGIN_DEFAULTS[k]))}
    return feed


def _card_without_hidden(c: Dict[str, Any], hp: Dict[str, Any]) -> Dict[str, Any]:
    """cr1#1738 (review of #1744): a work stream's card with the people it
    lists by id, less anyone hidden (all of them with the fence unread); its
    key contact is then the first one left, as the card picks it, so the
    panel, the contact and Ask for an update's draft never name them, and a
    namesake is never taken for them."""
    ppl = c.get("people") if isinstance(c.get("people"), dict) else None
    if not ppl:
        return c
    ids = hp.get("ids") or frozenset()
    rows = [r for r in (ppl.get("rows") or []) if isinstance(r, dict) and not hp.get("unread")
            and str(r.get("person_id") or "") not in ids]
    key = ({"person_id": rows[0].get("person_id"), "name": rows[0].get("name")} if rows else {})
    return dict(c, people=dict(ppl, rows=rows, key_contact=key))


def _hidden_people(workspace_root) -> Dict[str, Any]:
    """`hub_work.hidden_people`, failing closed (nobody is named) when it
    cannot be asked."""
    try:
        import hub_work

        return hub_work.hidden_people(workspace_root)
    except Exception:  # noqa: BLE001
        return {"unread": True, "ids": frozenset(), "names": frozenset(), "all_names": frozenset()}


def _hide_people_in_rows(rows, hp: Dict[str, Any]) -> None:
    """cr1#1721 (review of #1733): a plate row names nobody `hp` hides. Its
    `who` is judged by the row's person id where it has one, and by its words
    only for a typed name with no id; a hidden `who` is cleared, and with it
    the one part of `context` that is that who and the origin's captured
    line. A context part is dropped otherwise only when it is, whole, a hidden
    person's whole name: dates and status are never matched word by word
    (round 2, item 1). With the fence unread every who, its context part and
    every captured line go, whatever names are known (round 2, item 2). In
    place; other fields are left."""
    import hub_work

    ids = hp.get("ids") or frozenset()
    unread = bool(hp.get("unread"))
    whole = hp.get("all_full") if unread else hp.get("full_names")
    whole = whole or frozenset()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        veiled = unread
        who = str(r.get("who") or "")
        wid = str(r.get("who_id") or "")
        if unread:
            hid = bool(who)
        elif wid:
            hid = bool(who) and wid in ids
        else:
            hid = bool(who) and hub_work.name_is_hidden(who, hp)
        if hid:
            r["who"] = ""
            veiled = True
        ctx = str(r.get("context") or "")
        if ctx:
            parts = ctx.split(" · ")
            said = who.strip().lower()
            # a row judged by id and not hidden keeps its own who in context, even
            # when a hidden person shares the whole name (round 3, the twin case)
            mine = bool(wid) and not hid and not unread
            keep = [x for x in parts if not ((hid and said and x.strip().lower() == said)
                                             or (x.strip().lower() in whole and not (mine and x.strip().lower() == said)))]
            if len(keep) != len(parts):
                r["context"] = " · ".join(keep)
                veiled = True
        o = r.get("origin")
        if veiled and isinstance(o, dict) and (o.get("line") or o.get("who")):
            r["origin"] = dict(o, line="", paraphrase=False,
                               who="" if unread or hub_work.name_is_hidden(o.get("who"), hp) else o.get("who"))


class _InboxPage(HTMLParser):
    """The landed inbox page, read back for its numbered rows. The numbering
    IS the wire: a choice on row n goes to apply-choices as `n`, exactly as
    the widget's Apply sends it."""

    _CAPS = {"cr-item-name": "name", "cr-item-subject": "subject",
             "cr-item-context": "why", "cr-item-icon": "icon", "cr-eh": "eh",
             "cr-eb": "eb", "cr-orig-subject": "osub", "cr-orig-line": "oline"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: List[Dict[str, Any]] = []
        self.cur: Optional[Dict[str, Any]] = None
        self.stack: List[tuple] = []
        self.cap: Optional[str] = None
        self.buf: List[str] = []
        self.header = ""
        self._hdr: Optional[str] = None
        self.sel: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        self.stack.append((tag, cls))
        if "cr-header" in cls:
            self._hdr = "header"
            self.buf = []
        if "cr-item" in cls and a.get("data-item-n"):
            try:
                n = int(a["data-item-n"])
            except ValueError:
                n = None
            self.cur = {"n": n, "name": "", "subject": "", "why": "", "icon": "",
                        "meta": {}, "body": [], "orig_subject": "", "orig": [],
                        "actions": [], "more": []}
            self.items.append(self.cur)
        if not self.cur:
            return
        for c in cls:
            if c in self._CAPS:
                self.cap = self._CAPS[c]
                self.buf = []
                break
        if tag == "button" and a.get("data-action") and a.get("data-n"):
            self.cur["actions"].append(a["data-action"])
        if tag == "select" and "cr-action-select" in cls:
            self.sel = a.get("data-n")
        if tag == "option" and self.sel and a.get("value"):
            self.cur["more"].append(a["value"])

    def handle_endtag(self, tag):
        top = self.stack.pop() if self.stack else (None, [])
        if tag == "select":
            self.sel = None
        if self._hdr and "cr-header" in top[1]:
            self.header = " ".join("".join(self.buf).split())
            self._hdr = None
        if self.cap and self.cur and any(c in self._CAPS for c in top[1]):
            text = " ".join("".join(self.buf).split())
            key, self.cap = self.cap, None
            c = self.cur
            if key in ("name", "subject", "why", "icon"):
                c[key] = text
            elif key == "eh":
                if ":" in text:
                    k, v = text.split(":", 1)
                    c["meta"][k.strip().lower()] = v.strip()
            elif key == "eb":
                c["body"].append(text)
            elif key == "osub":
                c["orig_subject"] = text
            elif key == "oline":
                c["orig"].append(text)

    def handle_data(self, data):
        if self.cap or self._hdr:
            self.buf.append(data)


#: A bracketed blank a draft must never carry to the owner's Send (cr1#653).
_BLANK_RE = re.compile(r"\[[A-Za-z][A-Za-z ,/'-]{0,40}\]")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def feed_inbox(workspace_root=None, page_html: str = "", *, fire_stamp: str = "",
               items: Optional[List[Dict[str, Any]]] = None,
               page_rel: Optional[str] = None, now_iso=None) -> Dict[str, Any]:
    """The Inbox tab: the rows of the page the inbox leg just landed, each
    keeping that page's own number, joined to the fetched thread (`items`,
    the same list the leg handed `run_inbox_surface`) by subject when the
    join is unique, so the apply leg can re-read the thread before a send.
    Original mail text stays out of the feed: the draft and the subject are
    what the owner acts on."""
    parser = _InboxPage()
    parser.feed(str(page_html or ""))
    import inbox_helpers as _ih

    own = _ih.own_addresses(workspace_root)
    by_subject: Dict[str, List[Dict[str, Any]]] = {}
    for it in items or []:
        if isinstance(it, dict):
            by_subject.setdefault(_norm(it.get("subject")), []).append(it)
    # cr1#1792 (P2): the page numbers its rows in the selection's order, so
    # row n is the selection's thread n (its subject must agree); the unique
    # subject join is the fallback, so two threads with one subject keep theirs.
    try:
        top = list(_ih.select_priority(str(workspace_root), items)["top"]) if items else []
    except Exception:  # noqa: BLE001 - no selection: the subject join alone
        top = []
    rows = []
    for it in parser.items:
        if it.get("n") is None:
            continue
        subject = re.sub(r"^[\s—·-]+", "", it["subject"])
        kind = "calendar_invite" if ("accept" in it["actions"] or "\U0001f4c5" in it["icon"]) else "email_reply"
        thread = None
        try:
            pick = top[int(it["n"]) - 1] if int(it["n"]) >= 1 else None
        except (TypeError, ValueError, IndexError):
            pick = None
        cands = by_subject.get(_norm(subject)) or by_subject.get(_norm(it["subject"])) or []
        if isinstance(pick, dict) and _norm(pick.get("subject")) in (_norm(subject), _norm(it["subject"])):
            # review of #1822: when threads share the subject, the row's own To
            # must name the thread's sender too (no To read: no join)
            row_to = {_ih._own_key(a) for a in _ih._addresses(it["meta"].get("to", ""))}
            if len(cands) < 2 or any(_ih._own_key(a) in row_to for a in _ih._addresses(pick.get("sender_email"))):
                thread = pick
        if thread is None and len(cands) == 1:
            thread = cands[0]
        meta = it["meta"]
        body = "\n".join(it["body"]).strip()
        # cr1#653: a draft with a bracketed blank, or no text at all, is never
        # one press from a send: Send and Draft leave its actions and the page
        # names what to fill in.
        blanks = sorted(set(_BLANK_RE.findall(body)))
        actions = [a for a in it["actions"] if a in INBOX_WIRE_ACTION.values() or a in ("send", "draft")]
        if not body or blanks:
            actions = [a for a in actions if a not in ("send", "draft")]
        # cr1#936: a reply goes to everyone on the newest message, the owner
        # left out. The page's own To and Cc, and the joined thread's, together.
        rcpt = _ih.reply_all_recipients(thread or {}, own)
        to = [a for a in _ih._addresses(meta.get("to", "")) if _ih._own_key(a) not in own]
        if not own:  # fail closed, as `reply_all_recipients` does: the sender alone
            to = to[:1]
        to += [a for a in rcpt["to"] if a not in to]
        cc = [a for a in (_ih._addresses(meta.get("cc", "")) if own else []) + rcpt["cc"]
              if _ih._own_key(a) not in own and a not in to]
        cc = list(dict.fromkeys(cc))
        draft = {"to": ", ".join(to) if to else meta.get("to", ""), "cc": ", ".join(cc),
                 "subject": meta.get("subject", ""), "body": body, "blanks": blanks,
                 "voice_block_sha256": (thread.get("voice_block_sha256") if thread else None),
                 # cr1#1792, ruling 130c: the stamp of the voice rules applied
                 # ("In your voice"), and which they were
                 "voice_rules_sha256": (thread.get("voice_rules_sha256") if thread else None),
                 "voice_source": (thread.get("voice_source") if thread else None)}
        if not to and str(meta.get("to") or "").strip():
            # cr1#1250: the page's To named no one but the owner, so the To
            # above is that To unfiltered. The page never sends a Microsoft
            # 365 group reply on it.
            draft["to_unfiltered"] = True
        row = {
            "n": it["n"], "kind": kind, "name": it["name"][:120], "subject": subject[:200],
            # cr1#1635: the inbox page sets the why after a dash; the Hub's
            # read never starts with one (the subject is cleaned the same way).
            "why": re.sub(r"^[\s\u2012\u2013\u2014\u00b7-]+", "", it["why"])[:240],
            "draft": draft,
            "actions": actions,
            "thread_id": str(thread.get("thread_id") or "") if thread else None,
            # review round 1 of #1699 (B2): the thread's own sender, never the reply's first To
            "sender": (_mr_address(thread.get("sender_email") or thread.get("sender")) or None) if thread else None,
            "message_id": str(thread.get("message_id") or "") if thread else None,
            "message_ts": str(thread.get("ts") or "") if thread else None,
            "fire_stamp": fire_stamp,
        }
        # cr1#1635: the run's own verdict beside its why, when it gave one
        # (`needs`, a true or false on the thread it read): false is a row
        # that needs nothing from the owner, so the Hub lists it outside Needs
        # you. No verdict, no field: the row is one the run listed for the
        # owner, as before, and a page from before reads it that way too.
        if thread is not None and isinstance(thread.get("needs"), bool):
            row["needs"] = thread["needs"]
        rows.append(row)
    return _with_words(workspace_root, {"generated_at": _iso(_now(now_iso)), "fire_stamp": fire_stamp,
            "page_rel": page_rel, "header": parser.header, "items": rows,
            "n_items": len(rows), "n_drafted": sum(1 for r in rows if r["draft"]["body"]),
            "filtered": feed_filtered(workspace_root, items)})


#: cr1#1169: the Inbox tab's Filtered list, the threads the inbox read set
#: aside, grouped by why, in this order. `actions` are the presses a row of
#: that group takes: `keep` (show it anyway), `never` (never filter this
#: sender: an always-show rule on the address, `mail_rules` `add`), `back`
#: (a dropped thread brought back). An already-handled thread takes none: the
#: owner's reply on the sibling thread keeps it out whatever a rule says.
FILTERED_GROUPS = (
    ("rule", "Your mail rules", ("keep", "never")),
    ("dropped", "Dropped by you", ("back",)),
    ("newsletters", "Newsletters and marketing", ("keep", "never")),
    ("noise", "Noise", ("keep", "never")),
    ("preference", "You asked not to see these", ("keep", "never")),
    ("handled", "Already handled", ()),
)
FILTERED_CAP = 40
#: The plain words for each noise class (`inbox_helpers.NOISE_CLASSES`).
FILTERED_NOISE_WORDS = {"listings": "a listing", "calendar": "a calendar notice",
                        "security": "a security notice", "self_test": "a self-test message",
                        "marketing": "a newsletter or marketing mail"}
FILTERED_PREF_WORDS = {"newsletter": "you told Command Room to stop showing newsletters like this",
                       "sender": "you told Command Room to stop showing mail from this sender"}
FILTERED_HANDLED_WHY = "you already replied on another thread"
FILTERED_DROPPED_WHY = "you dropped this thread"


def feed_filtered(workspace_root, items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """READ. The Inbox tab's Filtered list (cr1#1169): every fetched thread
    the inbox read set aside, grouped by why, each row with its reason and its
    source (the thread, and the rule when a rule set it aside). Computed from
    the same `items` the inbox leg handed `run_inbox_surface`, through the
    same cut (`inbox_helpers.select_priority`), so the list is exactly what
    that read left out. The sender and the subject only: original mail text
    stays out, as on the rest of the tab. A group with nothing is left out."""
    import inbox_helpers as _ih
    import mail_rules as _mr

    items = [i for i in items or [] if isinstance(i, dict)]
    if workspace_root is None:
        return {"groups": [], "n": 0}
    # Owner ruling, 2026-11-13 (cr1#1169): the section says that mail from an
    # account marked personal is never read, so none of it is listed. Only
    # the fact travels, never the account's address.
    personal = _has_personal_account(workspace_root)
    if not items:
        return {"groups": [], "n": 0, "personal_not_read": personal}
    sel = _ih.select_priority(str(workspace_root), items)
    rules = {r.get("id"): r for r in (_mr.load(workspace_root).get("rules") or []) if isinstance(r, dict)}

    def row(item, group, why, rule=None):
        sender = _mr.address_of(item.get("sender_email") or item.get("sender") or "")
        out = {"group": group, "why": why,
               "thread_id": str(item.get("thread_id") or "") or None,
               "message_ts": str(item.get("ts") or item.get("message_ts") or "") or None,
               "name": str(item.get("sender_name") or sender or "")[:120],
               "sender": sender or None,
               "subject": str(item.get("subject") or "")[:200],
               # C3: the row points to its source: the thread (its link when the
               # fetch gave one) and, for a rule, the rule.
               "source": {"kind": "mail", "thread_id": str(item.get("thread_id") or "") or None,
                          "url": str(item.get("url") or "") or None}}
        if rule is not None:
            out["source"]["rule_id"] = rule.get("id")
            out["source"]["rule"] = _mr.plain(rule)
        return out

    buckets: Dict[str, List[Dict[str, Any]]] = {g: [] for g, _l, _a in FILTERED_GROUPS}
    for item in sel.get("hidden") or []:
        why = item.get("mail_rule")
        if why == "dropped":
            buckets["dropped"].append(row(item, "dropped", FILTERED_DROPPED_WHY))
            continue
        rule = rules.get(why)
        buckets["rule"].append(row(item, "rule", (_mr.plain(rule) if rule else str(why or "a mail rule")), rule))
    for item in sel.get("noise_items") or []:
        cls = str(item.get("noise_class") or "marketing")
        group = "newsletters" if cls == "marketing" else "noise"
        buckets[group].append(row(item, group, FILTERED_NOISE_WORDS.get(cls, "noise")))
    for item in sel.get("dropped") or []:
        buckets["preference"].append(row(item, "preference",
                                         FILTERED_PREF_WORDS.get(item.get("preference"), FILTERED_PREF_WORDS["sender"])))
    for item in sel.get("suppressed") or []:
        buckets["handled"].append(row(item, "handled", FILTERED_HANDLED_WHY))
    groups = []
    for key, label, actions in FILTERED_GROUPS:
        rows = buckets[key]
        if not rows:
            continue
        for r in rows:
            r["actions"] = [a for a in actions
                            if (a != "never" or r["sender"]) and (a == "never" or r["thread_id"])]
        groups.append({"key": key, "label": label, "n": len(rows), "rows": rows[:FILTERED_CAP]})
    return {"groups": groups, "n": sum(g["n"] for g in groups), "personal_not_read": personal}


def _has_personal_account(workspace_root) -> bool:
    """True when the seat has a mail account marked personal (cr1#1169): its
    mail is never fetched (cr1#755), so the Filtered list can hold none."""
    try:
        import connector_config as _cc
        # Case and spaces aside, as the inbox fetch plan reads the role (review).
        return any(str(a.get("role") or "").strip().lower() == _cc.ROLE_PERSONAL
                   for a in _cc.accounts(workspace_root))
    except Exception:  # noqa: BLE001 - an unread account map says nothing, never a guess
        return False


# ---------------------------------------------------------------------------
# choices: the plan (read) and the apply (write)
# ---------------------------------------------------------------------------

def _expired(choice: Dict[str, Any], now: _dt.datetime) -> bool:
    created = _parse(choice.get("created_at"))
    if created is None:
        return False
    return (now - created) > _dt.timedelta(days=CHOICE_EXPIRY_DAYS)


#: An Apply run claims each waiting choice before it acts on it (cr1#675), so
#: two runs that overlap (the hourly fire and a press on the page, or a retried
#: run) never apply one press twice: a send never goes out twice. A claim older
#: than this is a run that died midway; the next run takes the choice over.
CLAIM_STALE_MINUTES = 20
#: The most choices one claim batch carries (the database's batch limit).
CLAIM_BATCH_MAX = 50
#: What still waits after a claim batch (cr1#689): the run claims again, in
#: rounds, and names what is left when its rounds run out.
CLAIM_MORE_LINE = "{n} more wait for the next run."


def _flat_choice(raw: Any) -> Optional[Dict[str, Any]]:
    """A choice as the page's database hands it to a run: the document's own
    fields at the top, or `{doc_id | id, version, data: {...}}`. Either way the
    answer is the fields, with the version kept as `_version`."""
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("data"), dict):
        out = dict(raw["data"])
        out.setdefault("id", raw.get("doc_id") or raw.get("id"))
        if raw.get("version") is not None:
            out["_version"] = raw.get("version")
        return out
    out = dict(raw)
    if out.get("version") is not None and "_version" not in out:
        out["_version"] = out.get("version")
    return out


def _claim_live(choice: Dict[str, Any], now: _dt.datetime) -> bool:
    by = str(choice.get("claimed_by") or "").strip()
    at = _parse(choice.get("claimed_at"))
    return bool(by) and at is not None and (now - at) <= _dt.timedelta(minutes=CLAIM_STALE_MINUTES)


def plan_claim(workspace_root=None, choices: Optional[List[Any]] = None, *,
               run_id: str = "", now_iso=None) -> Dict[str, Any]:
    """READ/COMPUTE. The claim an Apply run writes before it acts (cr1#675):
    one `update` per waiting choice no other live run holds, `{claimed_by:
    run_id, claimed_at}`, pinned to the version the run read, ready to send as
    ONE ArtifactData `batch`. A pinned write fails when another run (or the
    page) changed the choice first; the run then re-reads and plans again, and
    a choice another run took is no longer offered. Answers `{ok, run_id,
    writes, claimable, taken, held, n_more, line}`: `held` is what this run
    already claimed (no write, not counted again); `n_more` counts what this batch
    could not carry, and `line` says so for the light (cr1#689): the run
    claims again while `n_more` is more than zero, in bounded rounds, and
    what is left after the last round waits for the next run."""
    rid_ = str(run_id or "").strip()
    if len(rid_) < 8:
        return {"ok": False, "run_id": rid_, "writes": [], "claimable": [], "taken": [],
                "held": [], "n_more": 0, "line": "A claim needs this run's own id (8 characters or more)."}
    now = _now(now_iso)
    writes: List[Dict[str, Any]] = []
    claimable: List[str] = []
    taken: List[str] = []
    held: List[str] = []
    seen = set()
    for raw in choices or []:
        c = _flat_choice(raw)
        if not c:
            continue
        cid = str(c.get("id") or "").strip()
        if not cid or cid in seen or c.get("applied") is True or c.get("applied_at"):
            continue
        seen.add(cid)
        if _claim_live(c, now):
            # A live claim of this run's own is already held (cr1#689): a second
            # round claims the next batch, never the same fifty again.
            (held if str(c.get("claimed_by")) == rid_ else taken).append(cid)
            continue
        claimable.append(cid)
        w: Dict[str, Any] = {"op": "update", "collection": "choices", "doc_id": cid,
                             "data": {"claimed_by": rid_, "claimed_at": _iso(now)}}
        if c.get("_version") is not None:
            w["if_version"] = c["_version"]
        writes.append(w)
    n_more = max(0, len(claimable) - CLAIM_BATCH_MAX)
    return {"ok": True, "run_id": rid_, "writes": writes[:CLAIM_BATCH_MAX],
            "claimable": claimable[:CLAIM_BATCH_MAX], "taken": taken, "held": held,
            "n_more": n_more, "line": CLAIM_MORE_LINE.format(n=n_more) if n_more else ""}


# ---------------------------------------------------------------------------
# the book record of a Send or Draft made on the page (cr1#817)
# ---------------------------------------------------------------------------

#: A Send or Draft the page made itself (cr1#710, cr1#727) lands in the mail
#: connector with no record in the book. The next Apply run writes it: the
#: `email_drafted` row, the voice snapshot and, for a send, the `email_sent`
#: row, through the writers the chat's own click uses. A press is recorded
#: once however many runs overlap: a run claims the row with a pinned write
#: before it records, and the record itself names the page's choice id, so a
#: run that died between the two never records it twice.
PAGE_RECORD_CLAIM_MINUTES = CLAIM_STALE_MINUTES
PAGE_RECORD_VERBS = ("send", "draft")
PAGE_RECORD_OUTCOMES = ("applied",)
PAGE_RECORD_SOURCE = "email-writer"
PAGE_CHOICE_KEY = "page_choice_id"
#: The page's own choice kinds that carry a mail: a reply, a chase (cr1#219),
#: and a person's list from the People tab (cr1#1017 part A).
PAGE_RECORD_KINDS = ("inbox", "chase", "list", "calnote", "followup")
#: cr1#1539: a note to the other people after a meeting moved or was
#: cancelled from the Hub is the product's own text too, drafted only on the
#: owner's press and never sent by the page: recorded like a chase, under the
#: calendar surface, with no snapshot.
PAGE_NOTE_SOURCE = "calendar-writer"
#: A meeting booked, moved or cancelled on the page (cr1#1539): the page made
#: the change on the calendar on the owner's press; the book learns of it
#: here. `remove` is a booking taken back (the Undo of Book), `move` with
#: `back` a meeting put back at its original time.
CAL_RECORD_KIND = "meeting"
CAL_RECORD_VERBS = ("book", "move", "cancel", "remove")
CAL_RECORD_LINE = "Recorded in your book."
CAL_RECORD_NO_EVENT_LINE = "No meeting on the press, so nothing was recorded."
CAL_CHANGE_WORDS = {"move": "moved", "cancel": "cancelled", "remove": "removed"}
#: A chase and a list are the product's own text, so each is recorded under
#: the commitments surface with no snapshot: an edit to it is no lesson about
#: the owner's voice.
PAGE_CHASE_SOURCE = "commitment-triage"
#: The two directions a person's list is drafted in (`chase_policy.LIST_WORDS`).
PAGE_LIST_DIRECTIONS = ("owed_to_you", "you_owe")
PAGE_RECORD_LINE = "Recorded in your book."
PAGE_RECORD_ALREADY_LINE = "Already in your book."
PAGE_RECORD_NO_RECIPIENT_LINE = "No recipient on the press, so nothing was recorded."
PAGE_LIST_SEND_LINE = "A list from the Hub is only ever a draft, so a send of one was not recorded."


def _record_claim_live(choice: Dict[str, Any], now: _dt.datetime) -> bool:
    at = _parse(choice.get("record_claimed_at"))
    return bool(choice.get("record_claimed_by")) and at is not None and (now - at) < _dt.timedelta(minutes=PAGE_RECORD_CLAIM_MINUTES)


def plan_page_records(workspace_root=None, choices: Optional[List[Any]] = None, *,
                      run_id: str = "", now_iso=None) -> Dict[str, Any]:
    """READ/COMPUTE. The page's own sends and drafts that have no book record
    yet (cr1#817): every `by_page` row whose result is `applied` and that
    carries no `book_recorded`, claimed for this run with one pinned `update`
    each (`{record_claimed_by, record_claimed_at}`), ready to send as ONE
    ArtifactData `batch`. A row another live run holds is `taken`; one this
    run already holds is `held` and goes straight to `entries`. Answers `{ok,
    run_id, writes, entries, calendar, taken, held, n_more, line}`: `entries`
    are the rows to hand `record_page_sends` once the batch landed (the ones
    in `writes`) or already held, and `calendar` (cr1#1539) the meetings
    booked, moved or cancelled on the page, to hand `record_calendar_changes`
    the same way."""
    rid_ = str(run_id or "").strip()
    if len(rid_) < 8:
        return {"ok": False, "run_id": rid_, "writes": [], "entries": [], "taken": [], "held": [],
                "calendar": [],
                "n_more": 0, "line": "A claim needs this run's own id (8 characters or more)."}
    now = _now(now_iso)
    writes: List[Dict[str, Any]] = []
    entries: List[Dict[str, Any]] = []
    calendar: List[Dict[str, Any]] = []
    taken: List[str] = []
    held: List[str] = []
    seen = set()
    for raw in choices or []:
        c = _flat_choice(raw)
        if not c:
            continue
        cid = str(c.get("id") or "").strip()
        if not cid or cid in seen or not c.get("by_page") or c.get("book_recorded"):
            continue
        # cr1#1745 (review round 1, item 3): a follow-up draft the owner took
        # back with Undo is no draft: nothing is recorded for it, and its
        # meeting is not marked followed up
        if str(c.get("kind") or "") == "followup" and c.get("undone"):
            continue
        verb = str(c.get("action") or "").strip().lower()
        res = c.get("result") if isinstance(c.get("result"), dict) else {}
        # cr1#1539: a meeting booked, moved or cancelled on the page goes to
        # `calendar`, for `record_calendar_changes`, under the same claim.
        is_cal = (str(c.get("kind") or "") == CAL_RECORD_KIND and verb in CAL_RECORD_VERBS
                  and res.get("outcome") in PAGE_RECORD_OUTCOMES)
        if not is_cal and (str(c.get("kind") or "") not in PAGE_RECORD_KINDS or verb not in PAGE_RECORD_VERBS or res.get("outcome") not in PAGE_RECORD_OUTCOMES):
            continue
        # A list is only ever a draft: the owner sends it from their own mail,
        # and so is a note about a meeting (cr1#1539).
        if str(c.get("kind") or "") in ("list", "calnote") and verb != "draft":
            continue
        seen.add(cid)
        if _record_claim_live(c, now) and str(c.get("record_claimed_by")) != rid_:
            taken.append(cid)
            continue
        if is_cal:
            cal_entry = {"id": cid, "action": verb, "event_id": str(c.get("n") or "")[:512],
                         "title": str(c.get("title") or "")[:200],
                         "start": str(c.get("start") or "")[:40], "end": str(c.get("end") or "")[:40],
                         "from_start": str(c.get("from_start") or "")[:40], "from_end": str(c.get("from_end") or "")[:40],
                         "back": c.get("back") is True, "via": str(c.get("via") or "")[:20],
                         "people": [str(x)[:120] for x in (c.get("people") if isinstance(c.get("people"), list) else [])[:MEETING_PEOPLE_CAP]],
                         "provider": str(c.get("provider") or "")[:40],
                         "applied_at": str(c.get("applied_at") or c.get("created_at") or "")}
            if _record_claim_live(c, now):
                held.append(cid)
                calendar.append(cal_entry)
                continue
            if len(writes) >= CLAIM_BATCH_MAX:
                continue
            cw: Dict[str, Any] = {"op": "update", "collection": "choices", "doc_id": cid,
                                  "data": {"record_claimed_by": rid_, "record_claimed_at": _iso(now)}}
            if c.get("_version") is not None:
                cw["if_version"] = c["_version"]
            writes.append(cw)
            calendar.append(cal_entry)
            continue
        draft = c.get("draft") if isinstance(c.get("draft"), dict) else {}
        entry = {"id": cid, "action": verb, "provider": str(c.get("provider") or ""),
                 "draft": {k: str(draft.get(k) or "")[:20000] for k in ("to", "cc", "bcc", "subject", "body")},
                 "native_id": str(c.get("native_id") or "") or None, "thread_id": c.get("thread_id") or None,
                 "applied_at": str(c.get("applied_at") or c.get("created_at") or ""),
                 "voice_block_sha256": (draft.get("voice_block_sha256") if isinstance(draft.get("voice_block_sha256"), str) else None),
                 "title": str(c.get("title") or "")[:200]}
        if str(c.get("kind") or "") == "chase":
            entry["chase_for"] = str(c.get("n") or "")
        if str(c.get("kind") or "") == "list":
            entry["list_for"] = str(c.get("n") or "")
            direction = str(c.get("direction") or "")
            entry["list_direction"] = direction if direction in PAGE_LIST_DIRECTIONS else ""
        if str(c.get("kind") or "") == "calnote":
            entry["note_for"] = str(c.get("n") or "")[:512]
        if str(c.get("kind") or "") == "followup":
            # cr1#1745: the meeting's processed ref, so the record marks it followed up
            entry["followup_for"] = str(c.get("ref") or "")[:400]
            entry["followup_meeting"] = str(c.get("n") or "")[:200]
        if _record_claim_live(c, now):
            held.append(cid)
            entries.append(entry)
            continue
        if len(writes) >= CLAIM_BATCH_MAX:
            continue
        w: Dict[str, Any] = {"op": "update", "collection": "choices", "doc_id": cid,
                             "data": {"record_claimed_by": rid_, "record_claimed_at": _iso(now)}}
        if c.get("_version") is not None:
            w["if_version"] = c["_version"]
        writes.append(w)
        entries.append(entry)
    n_more = max(0, len(seen) - len(taken) - len(entries) - len(calendar))
    return {"ok": True, "run_id": rid_, "writes": writes, "entries": entries, "taken": taken, "held": held,
            "calendar": calendar,
            "n_more": n_more, "line": CLAIM_MORE_LINE.format(n=n_more) if n_more else ""}


def _recipient_person(workspace_root, address: str) -> Optional[str]:
    """The person on file with this address, else None. A miss is fine: the
    record then carries the address alone."""
    try:
        from entities_io import entities_collection, read_entities_json
        from people_writer import get_person_emails

        ent = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
        want = address.strip().lower()
        for p in entities_collection(ent, "people"):
            if isinstance(p, dict) and any(e.strip().lower() == want for e in get_person_emails(p)):
                return str(p.get("id") or "") or None
    except Exception:  # noqa: BLE001
        return None
    return None


def _page_record_on_book(workspace_root, cid: str) -> Dict[str, Any]:
    """The seqs already on the book for this page choice: `{drafted, sent}`.
    Read with the personal-lane drop off (`drop_personal=False`, the opt-out
    the loader keeps for a reader that renders no row content): a record to a
    personal-tie contact must still be found, or the next run would write it
    twice. Nothing read here is shown or returned beyond two seqs."""
    from events_io import load_events_org_scoped

    out: Dict[str, Any] = {"drafted": None, "sent": None}
    try:
        events, _skipped = load_events_org_scoped(str(workspace_root), drop_personal=False)
    except Exception:  # noqa: BLE001 - an unreadable book is read as empty, and the writer's own append says so
        events = []
    for ev in events:
        if not isinstance(ev, dict) or not isinstance(ev.get("data"), dict):
            continue
        if str(ev["data"].get(PAGE_CHOICE_KEY) or "") != cid:
            continue
        if ev.get("type") == "email_drafted" and out["drafted"] is None:
            out["drafted"] = ev.get("seq")
        elif ev.get("type") == "email_sent" and out["sent"] is None:
            out["sent"] = ev.get("seq")
    return out


def record_page_sends(workspace_root, entries: Optional[List[Dict[str, Any]]] = None,
                      *, now_iso=None) -> Dict[str, Any]:
    """A WRITER. The book record of each Send or Draft the page made (cr1#817),
    through the writers the chat's click uses: `voice_corrections.
    record_draft_click` (the `email_drafted` row, the voice snapshot keyed to
    it, a voice line) and, for a send, one `email_sent` row carrying
    `build_email_sent_provenance` and `draft_event_seq`. Each row names the
    page's choice id, and a choice the book already names is answered from
    its first write, so a retried run never records a press twice. Answers
    `{ok, results: [{id, outcome, line, drafted_seq, sent_seq}], n_recorded,
    n_errors}`."""
    import voice_corrections as vc
    from connector_adapters.provenance import build_email_sent_provenance
    from event_gate import append_event

    rows = [e for e in (entries or []) if isinstance(e, dict)]
    now = _now(now_iso)
    events_path = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
    try:
        import connector_config
        backend = connector_config.declared_backend("email", workspace_root) or {}
    except Exception:  # noqa: BLE001
        backend = {}
    server_id = str(backend.get("server_id") or "") or None
    address = str(backend.get("address") or backend.get("account") or "") or None
    results: List[Dict[str, Any]] = []
    for e in rows:
        cid = str(e.get("id") or "").strip()
        verb = str(e.get("action") or "").strip().lower()
        r: Dict[str, Any] = {"id": cid, "action": verb, "outcome": "error", "line": ROW_ERROR_LINE,
                             "drafted_seq": None, "sent_seq": None}
        try:
            draft = e.get("draft") if isinstance(e.get("draft"), dict) else {}
            found = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", str(draft.get("to") or ""))
            recipient = found.group(0).lower() if found else ""
            if (e.get("list_for") or e.get("note_for")) and verb != "draft":
                r.update(outcome="refused", line=PAGE_LIST_SEND_LINE)
                results.append(r)
                continue
            if not cid or verb not in PAGE_RECORD_VERBS or not recipient:
                r.update(outcome="refused", line=PAGE_RECORD_NO_RECIPIENT_LINE)
                results.append(r)
                continue
            if "followup_for" in e and not _followup_kept(workspace_root, e, draft):
                # cr1#1745 (review round 1): a follow-up row that names no draft kept
                # for that meeting, or other people than the draft's own, records nothing
                r.update(outcome="refused", line=PAGE_FOLLOWUP_NO_DRAFT_LINE)
                results.append(r)
                continue
            on_book = _page_record_on_book(workspace_root, cid)
            provider = str(e.get("provider") or backend.get("provider") or "") or None
            drafted = on_book["drafted"]
            if drafted is None:
                res = vc.record_draft_click(
                    workspace_root, recipient=recipient, subject=str(draft.get("subject") or ""),
                    body=str(draft.get("body") or ""), native_draft_id=e.get("native_id") or None,
                    provider=provider, server_id=server_id, address=address,
                    topic=str(draft.get("subject") or ""), recipient_id=_recipient_person(workspace_root, recipient),
                    domain=recipient.rsplit("@", 1)[-1].lower(), voice_block_sha256=e.get("voice_block_sha256"),
                    source_skill=(PAGE_NOTE_SOURCE if e.get("note_for") else PAGE_CHASE_SOURCE
                                  if (e.get("chase_for") or e.get("list_for")) else PAGE_RECORD_SOURCE),
                    snapshot=not (e.get("chase_for") or e.get("list_for") or e.get("note_for")),
                    extra={PAGE_CHOICE_KEY: cid, "sent_from": "hub",
                           **({"note_for": e["note_for"]} if e.get("note_for") else {}),
                           **({"chase_for": e["chase_for"]} if e.get("chase_for") else {}),
                           **({"list_for": e["list_for"], "list_direction": e.get("list_direction") or ""}
                              if e.get("list_for") else {})})
                if not res.get("ok"):
                    r.update(outcome="error", line=str(res.get("line") or ROW_ERROR_LINE))
                    results.append(r)
                    continue
                drafted = res.get("event_seq")
            r["drafted_seq"] = drafted
            if e.get("followup_for"):
                _followup_mark(workspace_root, e["followup_for"], cid, drafted, verb)
            if verb == "send":
                sent = on_book["sent"]
                if sent is None:
                    subject = str(draft.get("subject") or "")
                    data: Dict[str, Any] = {"recipient": recipient, "subject": subject, "topic": subject,
                                            "draft_event_seq": drafted, PAGE_CHOICE_KEY: cid, "sent_from": "hub"}
                    data.update(build_email_sent_provenance(message_id=e.get("native_id") or None,
                                                            thread_id=e.get("thread_id") or None, provider=provider,
                                                            server_id=server_id, address=address,
                                                            workspace_root=workspace_root))
                    sent_ev: Dict[str, Any] = {"type": "email_sent", "source_skill": PAGE_RECORD_SOURCE, "data": data}
                    # The envelope keys the chat's own sent row carries, so a
                    # page send counts as a touch where sends are counted.
                    who = _recipient_person(workspace_root, recipient)
                    if who:
                        sent_ev["person_ids"] = [who]
                    written = append_event(events_path, sent_ev, holder="cockpit_helpers")
                    sent = written[0].get("seq") if written and isinstance(written[0], dict) else None
                    r["sent_seq"] = sent
                    r.update(outcome="applied", line=PAGE_RECORD_LINE)
                else:
                    r["sent_seq"] = sent
                    r.update(outcome="already", line=PAGE_RECORD_ALREADY_LINE)
            else:
                r.update(outcome="applied" if on_book["drafted"] is None else "already",
                         line=PAGE_RECORD_LINE if on_book["drafted"] is None else PAGE_RECORD_ALREADY_LINE)
        except Exception as exc:  # noqa: BLE001 - one row's failure never stops the batch
            r.update(_row_error(exc))
        results.append(r)
    return {"ok": True, "results": results, "recorded_at": _iso(now),
            "n_recorded": sum(1 for x in results if x["outcome"] == "applied"),
            "n_errors": sum(1 for x in results if x["outcome"] == "error")}


PAGE_FOLLOWUP_NO_DRAFT_LINE = "No follow-up is kept for that meeting to those people, so nothing was recorded."


def _followup_kept(workspace_root, entry: Dict[str, Any], draft: Dict[str, Any]) -> bool:
    """cr1#1745: the page's follow-up row is the kept draft's own: a
    `followup_draft` row for the ref it names, for the same meeting, still
    waiting, and every address on the press (to, cc or bcc) is one of that
    draft's people.
    False (nothing recorded) for anything else, or when the book or the
    people cannot be read."""
    try:
        import followup_draft
        import hub_meeting
        from events_io import load_events_owner_scoped

        evs = [x for x in load_events_owner_scoped(str(workspace_root))[0] if isinstance(x, dict)]
        ref = str(entry.get("followup_for") or "")
        kept = followup_draft.drafts_by_ref(evs).get(hub_meeting._ref_key(ref)) if ref else None
        if not kept or str(kept.get("meeting_id") or "") != str(entry.get("followup_meeting") or ""):
            return False
        people = followup_draft._people(Path(str(workspace_root)))
        own = {str(m).strip().lower() for pid in kept.get("to_person_ids") or []
               for m in (people.get(str(pid)) or {}).get("emails") or [] if str(m).strip()}
        found = {k: {a.lower() for a in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", str(draft.get(k) or ""))}
                 for k in ("to", "cc", "bcc")}
        return bool(found["to"]) and (found["to"] | found["cc"] | found["bcc"]) <= own
    except Exception:  # noqa: BLE001 - fail closed: nothing recorded
        return False


def _followup_mark(workspace_root, ref: str, cid: str, drafted_seq: Any, verb: str) -> None:
    """cr1#1745: a follow-up drafted or sent from the wrap-up page marks its
    meeting followed up, as the follow-up ritual's own pack does: one
    `followup_pack_drafted` row naming the meeting's ref and the page's
    choice, written once however many runs record the press."""
    from event_gate import append_event

    events_path = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
    try:
        from events_io import load_events_owner_scoped

        for ev in load_events_owner_scoped(str(workspace_root))[0]:
            d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
            if ev.get("type") == "followup_pack_drafted" and str(d.get(PAGE_CHOICE_KEY) or "") == cid:
                return
    except Exception:  # noqa: BLE001 - the book unread: nothing is marked; the next run tries again
        return
    append_event(events_path, {"type": "followup_pack_drafted", "source_skill": "follow-up-ritual",
                               "data": {"source_ref": ref, PAGE_CHOICE_KEY: cid, "draft_event_seq": drafted_seq,
                                        "how": "sent" if verb == "send" else "drafted", "sent_from": "hub"}},
                 holder="cockpit_helpers")


def _calendar_record_on_book(workspace_root, cid: str) -> Optional[Any]:
    """The seq of the row the book already holds for this page choice, else
    None. Read with the personal-lane drop off, as `_page_record_on_book`
    reads: nothing read here is shown or returned beyond one seq."""
    from events_io import load_events_org_scoped

    try:
        events, _skipped = load_events_org_scoped(str(workspace_root), drop_personal=False)
    except Exception:  # noqa: BLE001 - an unreadable book is read as empty, and the append says so
        events = []
    for ev in events:
        if not isinstance(ev, dict) or not isinstance(ev.get("data"), dict):
            continue
        if ev.get("type") in ("meeting_scheduled", "meeting_changed") and str(ev["data"].get(PAGE_CHOICE_KEY) or "") == cid:
            return ev.get("seq")
    return None


def _people_on_file(workspace_root, ids: List[str]) -> List[str]:
    """Those of `ids` that are people on file, in order."""
    try:
        from entities_io import entities_collection, read_entities_json

        ent = read_entities_json(Path(str(workspace_root)) / "_hq" / "data" / "entities.json")
        known = {str(p.get("id") or "") for p in entities_collection(ent, "people") if isinstance(p, dict)}
    except Exception:  # noqa: BLE001 - nobody is named on the row
        return []
    out: List[str] = []
    for i in ids:
        if i and i in known and i not in out:
            out.append(i)
    return out


def record_calendar_changes(workspace_root, entries: Optional[List[Dict[str, Any]]] = None,
                            *, now_iso=None) -> Dict[str, Any]:
    """A WRITER. The book record of each meeting the owner booked, moved or
    cancelled on the Hub page (cr1#1539), one row per change, through the one
    append path (`event_gate.append_event`). It records what the page already
    did on the owner's press: it never calls a calendar and never sends
    anything. A booking is `meeting_scheduled`, the type calendar-writer
    writes for the same thing; a move, a cancel and a booking taken back are
    `meeting_changed`. Each row names the page's choice id, and a choice the
    book already names is answered from its first write, so a retried run
    never records a change twice. Answers `{ok, results: [{id, action,
    outcome, line, seq}], n_recorded, n_errors}`."""
    from event_gate import append_event

    rows = [e for e in (entries or []) if isinstance(e, dict)]
    now = _now(now_iso)
    events_path = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
    results: List[Dict[str, Any]] = []
    for e in rows:
        cid = str(e.get("id") or "").strip()
        verb = str(e.get("action") or "").strip().lower()
        r: Dict[str, Any] = {"id": cid, "action": verb, "outcome": "error", "line": ROW_ERROR_LINE, "seq": None}
        try:
            event_id = str(e.get("event_id") or "").strip()
            start = _parse(e.get("start"))
            end = _parse(e.get("end"))
            timed = verb in ("book", "move")
            if (not cid or verb not in CAL_RECORD_VERBS or not event_id
                    or (timed and (start is None or end is None or end <= start))):
                r.update(outcome="refused", line=CAL_RECORD_NO_EVENT_LINE)
                results.append(r)
                continue
            on_book = _calendar_record_on_book(workspace_root, cid)
            if on_book is not None:
                r.update(outcome="already", line=PAGE_RECORD_ALREADY_LINE, seq=on_book)
                results.append(r)
                continue
            title = str(e.get("title") or "")[:200]
            if verb == "book":
                who = _people_on_file(workspace_root, [str(x) for x in e.get("people") or []])
                ev: Dict[str, Any] = {"type": "meeting_scheduled", "source_skill": PAGE_NOTE_SOURCE,
                                      "data": {"calendar_event_id": event_id, "scheduled_at_ts": _iso(start),
                                               "duration_minutes": int((end - start).total_seconds() // 60),
                                               "attendee_person_ids": who, "title": title,
                                               PAGE_CHOICE_KEY: cid, "sent_from": "hub"}}
                if who:
                    ev["person_ids"] = who
            else:
                data: Dict[str, Any] = {"change": CAL_CHANGE_WORDS[verb], "calendar_event_id": event_id,
                                        "title": title, PAGE_CHOICE_KEY: cid, "sent_from": "hub"}
                if verb == "move":
                    data.update({"to_start": _iso(start), "to_end": _iso(end), "put_back": e.get("back") is True})
                    for key in ("from_start", "from_end"):
                        was = _parse(e.get(key))
                        if was is not None:
                            data[key] = _iso(was)
                ev = {"type": "meeting_changed", "source_skill": PAGE_NOTE_SOURCE, "data": data}
            written = append_event(events_path, ev, holder="cockpit_helpers")
            r.update(outcome="applied", line=CAL_RECORD_LINE,
                     seq=written[0].get("seq") if written and isinstance(written[0], dict) else None)
        except Exception as exc:  # noqa: BLE001 - one row's failure never stops the batch
            r.update(_row_error(exc))
        results.append(r)
    return {"ok": True, "results": results, "recorded_at": _iso(now),
            "n_recorded": sum(1 for x in results if x["outcome"] == "applied"),
            "n_errors": sum(1 for x in results if x["outcome"] == "error")}


#: A row whose write failed: the owner reads this; the exception text stays in
#: the result's `error` for the run's receipt (cr1#679).
ROW_ERROR_LINE = "That one could not be written; nothing changed. Try again, or say it in chat."
#: The plate could not be read, so no close can be checked against it.
PLATE_UNREADABLE_LINE = "Your plate could not be read just now, so nothing was closed. The next Apply tries again."
NOTE_CAP = 2000


def _owner_id(workspace_root) -> str:
    """The owner's person id, the actor every Hub write is made as (cr1#678):
    the run passes none, so the writers resolve it from the workspace."""
    try:
        from primary_user import resolve_primary_user

        return str(resolve_primary_user(workspace_root) or "").strip()
    except Exception:  # noqa: BLE001 - no owner answers the writer's own no-actor line
        return ""


def _row_error(exc: BaseException) -> Dict[str, Any]:
    return {"outcome": "error", "line": ROW_ERROR_LINE, "error": f"{type(exc).__name__}: {exc}"[:240]}


#: The send tokens already used, kept on the page's database at
#: `config/sent_tokens` as `{tokens: [...]}`, newest last (cr1#684): a run
#: records a token right before it sends, so a run that dies after sending
#: and before marking the choice applied can never send it again. The list
#: is bounded; a token older than the oldest kept is also older than the
#: choice expiry, so its choice would be refused as expired first.
SENT_TOKENS_CAP = 200


def sent_tokens_update(workspace_root=None, doc: Optional[Dict[str, Any]] = None,
                       token: str = "") -> Dict[str, Any]:
    """READ/COMPUTE. The `config/sent_tokens` document with `token` recorded
    as the newest: `{tokens, already}`. `tokens` are the ones as read (a
    missing or malformed document reads as none), this one appended once,
    the oldest dropped past `SENT_TOKENS_CAP`; the run `set`s `{tokens}`,
    pinned by the version rule, before it sends. `already` is true when the
    record read already held this token: another run recorded it, so this
    run sends nothing for it (the redo after a refused pinned write is where
    that shows). `workspace_root` is what the door forwards; nothing here
    reads the folder."""
    tokens: List[str] = []
    if isinstance(doc, dict) and isinstance(doc.get("tokens"), list):
        tokens = [str(t) for t in doc["tokens"] if isinstance(t, str) and t]
    tok = str(token or "").strip()
    already = bool(tok) and tok in tokens
    if tok:
        tokens = [t for t in tokens if t != tok] + [tok]
    return {"tokens": tokens[-SENT_TOKENS_CAP:], "already": already}


def _choice_commitment_id(raw: Dict[str, Any]) -> str:
    """The commitment a page choice names: its `n`. The one mapping, read by
    `plan_apply` and by `apply_commitment_choices` for a raw page document
    (cr1#1377)."""
    return str(raw.get("n") or "").strip()


def _commitment_row(c: Dict[str, Any], now: _dt.datetime,
                    run_id: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """cr1#1377: one choice as `apply_commitment_choices` reads it, as
    `(row, refusal)`. A row that names its `commitment_id` (a `plan_apply`
    row) is taken as it is. A raw page document names its item in `n`
    instead, so it gets `plan_apply`'s mapping and the checks `plan_apply`
    would have made on it: another kind's row, an applied document and an
    expired one are refused, and a row naming no item at all is refused as
    naming none, never as gone. A refusal writes nothing to any item; the
    batch's audit row records it.

    The owner's ruling of 2026-11-19 (option b): a raw document whose claim
    is live (`_claim_live`, the guard `plan_claim` and `plan_apply` use,
    cr1#675) and held by a run other than `run_id` is refused as
    `claimed_elsewhere`, outcome `waiting`, so a stale overlapping run never
    applies a press a second time. A writer call that names no run holds no
    claim: its live-claimed press waits as `no_run_id`, which the audit
    names an error so the Hub flags the broken call. A claim gone stale
    holds nothing."""
    if str(c.get("commitment_id") or "").strip():
        return c, None
    raw = _flat_choice(c) or {}
    if str(raw.get("commitment_id") or "").strip():
        return raw, None
    verb = str(raw.get("action") or "").strip().lower()
    base = {"id": raw.get("id"), "commitment_id": "", "action": verb}
    if not str(raw.get("id") or "").strip():
        # A document the page cannot mark applied would be applied again on
        # every run; plan_apply skips it, and so does the writer.
        return None, dict(base, outcome="error", reason="no_choice_id", line=NO_CHOICE_ID_LINE)
    kind = str(raw.get("kind") or "").strip().lower()
    if kind and kind != "commitment":
        return None, dict(base, outcome="error", reason="not_commitment",
                          line=NOT_COMMITMENT_LINE.format(kind=kind[:40]))
    if raw.get("applied_at") or raw.get("applied") is True:
        return None, dict(base, outcome="already", reason="already_applied", line=ALREADY_APPLIED_LINE)
    if _expired(raw, now):
        return None, dict(base, outcome="error", reason="expired",
                          line=EXPIRED_LINE.format(days=CHOICE_EXPIRY_DAYS))
    if _claim_live(raw, now) and str(raw.get("claimed_by") or "").strip() != str(run_id or "").strip():
        if not str(run_id or "").strip():
            return None, dict(base, outcome="waiting", reason="no_run_id", line=NO_RUN_ID_LINE)
        return None, dict(base, outcome="waiting", reason="claimed_elsewhere", line=CLAIMED_ELSEWHERE_LINE)
    cid = _choice_commitment_id(raw)
    if not cid:
        return None, dict(base, outcome="error", reason="no_id", line=NO_ID_LINE)
    return dict(raw, commitment_id=cid, action=verb), None


def plan_apply(workspace_root=None, choices: Optional[List[Dict[str, Any]]] = None, *,
               inbox_fire_stamp: Optional[str] = None, now_iso=None,
               run_id: Optional[str] = None,
               used_tokens: Optional[List[str]] = None) -> Dict[str, Any]:
    """Sort the page's waiting choices into what this run applies and what
    it refuses, each refusal with its one line. READ/COMPUTE ONLY.

    `choices` are the page's rows with `applied_at` empty: `{id, kind, src,
    n, action, input, created_at, confirmed, token, title}`. Answers
    `{commitment: [...], inbox: [...], refused: [{id, line}], n_waiting}`:
    `commitment` rows go to `apply_commitment_choices`; `inbox` rows are the
    apply-choices wire tuples (`n`, `action`, `input`, `src`) plus the
    thread the feed joined, for the apply leg to dispatch through the
    inbox handlers after it re-reads the thread.

    With `run_id` (cr1#675), only the choices this run claimed are planned;
    every other one is listed in `skipped` and left alone, because another run
    holds it or no run has claimed it yet. `used_tokens` (cr1#684) are the
    send tokens a run already recorded at `config/sent_tokens`: a send whose
    token is among them is refused as reused, whatever its document says."""
    now = _now(now_iso)
    rid_ = str(run_id or "").strip()
    skipped: List[str] = []
    commitment: List[Dict[str, Any]] = []
    inbox: List[Dict[str, Any]] = []
    hub: List[Dict[str, Any]] = []
    review: List[Dict[str, Any]] = []
    week: List[Dict[str, Any]] = []
    settings: List[Dict[str, Any]] = []
    mail: List[Dict[str, Any]] = []
    people: List[Dict[str, Any]] = []
    stillyours: List[Dict[str, Any]] = []
    prepedit: List[Dict[str, Any]] = []
    focus: List[Dict[str, Any]] = []
    refused: List[Dict[str, Any]] = []
    seen = set()
    tokens_seen = set(str(t) for t in (used_tokens or []) if isinstance(t, str) and t)
    for raw in choices or []:
        raw = _flat_choice(raw)
        if not raw:
            continue
        cid = str(raw.get("id") or "").strip()
        if not cid or cid in seen or raw.get("applied_at") or raw.get("applied") is True:
            continue
        seen.add(cid)
        if rid_ and str(raw.get("claimed_by") or "").strip() != rid_:
            skipped.append(cid)
            continue
        verb = str(raw.get("action") or "").strip().lower()
        kind = str(raw.get("kind") or "").strip().lower()
        if _expired(raw, now):
            refused.append({"id": cid, "line": EXPIRED_LINE.format(days=CHOICE_EXPIRY_DAYS),
                            "reason": "expired"})
            continue
        if kind == "commitment":
            if verb not in COMMITMENT_VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            commitment.append({"id": cid, "commitment_id": _choice_commitment_id(raw),
                               "action": verb, "input": raw.get("input"),
                               "created_at": str(raw.get("created_at") or ""),
                               "title": str(raw.get("title") or "")[:200],
                               # cr1#1540: the pile the press came from, when one,
                               # and on an Undo of a pile's item the write it takes back
                               **({"pile": str(raw.get("pile"))[:80]} if raw.get("pile") else {}),
                               **({"back_of": str(raw.get("back_of"))[:160]} if raw.get("back_of") else {}),
                               **({"back_seq": raw.get("back_seq")} if isinstance(raw.get("back_seq"), int)
                                  and not isinstance(raw.get("back_seq"), bool) else {}),
                               # cr1#1541: a planned move's plan and time of day,
                               # as the page sent them; the writer's own gate reads them
                               **{k: raw[k] for k in ("plan", "at", "mins") if k in raw}})
        elif kind == "person":
            # cr1#1020 slice 3: a star or an unstar on the People tab.
            if verb not in PERSON_VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            people.append({"id": cid, "action": verb, "person_id": str(raw.get("n") or "")[:80],
                           "title": str(raw.get("title") or "")[:200]})
        elif (kind == "inbox" and verb in INBOX_RULE_VERBS) or kind == "mailrule":
            # cr1#1020: a drop, a taught rule or a rule written by hand. It
            # names its thread or its rule, never a row number, so an inbox
            # read landing since does not make it stale.
            if kind == "mailrule" and verb not in MAILRULE_VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            typed = raw.get("input") if isinstance(raw.get("input"), dict) else {}
            mail.append({"id": cid, "action": verb,
                         "thread_id": raw.get("thread_id") or typed.get("thread_id") or None,
                         "message_ts": raw.get("message_ts") or None,
                         "title": str(raw.get("title") or "")[:200],
                         "match": str(typed.get("match") or "")[:20],
                         "value": str(typed.get("value") or "")[:200],
                         "note": str(typed.get("note") or "")[:NOTE_CAP],
                         "mode": str(typed.get("mode") or "")[:10],
                         "rule_id": str(typed.get("rule_id") or raw.get("n") or "")[:40],
                         # cr1#1169: the undo of "never filter this sender".
                         "restore": typed.get("restore") is True,
                         # cr1#1230: the owner's second press, after the page
                         # said an off-limits sender's mail would be filed again.
                         "confirmed": typed.get("confirmed") is True or raw.get("confirmed") is True})
        elif kind == "inbox":
            if verb not in INBOX_VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            stamp = str(raw.get("fire_stamp") or "")
            if inbox_fire_stamp and stamp != str(inbox_fire_stamp):
                refused.append({"id": cid, "line": STALE_INBOX_LINE, "reason": "stale_inbox"})
                continue
            if verb == "send":
                token = str(raw.get("token") or "")
                if not (raw.get("confirmed") is True and len(token) >= SEND_TOKEN_MIN_LEN):
                    refused.append({"id": cid, "line": SEND_NEEDS_CONFIRM_LINE,
                                    "reason": "send_unconfirmed"})
                    continue
                if token in tokens_seen:
                    refused.append({"id": cid, "line": SEND_TOKEN_REUSED_LINE,
                                    "reason": "token_reused"})
                    continue
                tokens_seen.add(token)
            try:
                n = int(raw.get("n"))
            except (TypeError, ValueError):
                refused.append({"id": cid, "line": STALE_INBOX_LINE, "reason": "no_row"})
                continue
            tuple_ = {"n": n, "action": INBOX_WIRE_ACTION[verb], "src": INBOX_SRC}
            if isinstance(raw.get("input"), (dict, str)) and raw.get("input"):
                tuple_["input"] = raw["input"]
            # cr1#677: the draft travels with the press (to, cc, subject, the
            # body as the owner saw or edited it), so the run sends what the
            # owner pressed, never whatever row `n` holds by the time it runs.
            draft = raw.get("draft") if isinstance(raw.get("draft"), dict) else None
            inbox.append({"id": cid, "wire": tuple_, "token": raw.get("token") if verb == "send" else None,
                          "draft": ({k: str(draft.get(k) or "")[:20000] for k in ("to", "cc", "subject", "body")}
                                    if draft else None),
                          "fire_stamp": str(raw.get("fire_stamp") or "") or None,
                          "thread_id": raw.get("thread_id") or None,
                          "message_ts": raw.get("message_ts") or None,
                          "title": str(raw.get("title") or "")[:200]})
        elif kind == "review":
            # cr1#779: the Review tab's press, for `apply_review_choices`.
            if verb not in REVIEW_VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            typed = raw.get("input") if isinstance(raw.get("input"), dict) else {}
            review.append({"id": cid, "n": str(raw.get("n") or ""), "action": verb,
                           "input": str(typed.get("text") or "")[:NOTE_CAP],
                           "payload": raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
                           "review_kind": str(raw.get("review_kind") or ""), "group": str(raw.get("group") or ""),
                           "commitment_id": raw.get("commitment_id"), "seq": raw.get("seq"),
                           "thread_id": raw.get("thread_id"), "title": str(raw.get("title") or "")[:200],
                           # cr1#1540: a Looks done pile's row, and when it was pressed
                           **({"pile": str(raw.get("pile"))[:80], "created_at": str(raw.get("created_at") or "")}
                              if raw.get("pile") else {})})
        elif kind == "week":
            # cr1#780: the Done tab's press, for `apply_week_choices`.
            if verb not in WEEK_VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            week.append({"id": cid, "n": str(raw.get("n") or ""), "action": verb,
                         "payload": raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
                         "title": str(raw.get("title") or "")[:200]})
        elif kind == "tidy":
            # cr1#1584: a Tidy pile's press, or its Undo, for `apply_week_choices`
            # (the project and folder writes the Record tab's presses share).
            import hub_tidy

            if verb not in hub_tidy.VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            week.append({"id": cid, "kind": "tidy", "n": str(raw.get("n") or "")[:200], "action": verb,
                         "title": str(raw.get("title") or "")[:200],
                         **({"pile": str(raw.get("pile"))[:80]} if raw.get("pile") else {}),
                         **({"back_of": str(raw.get("back_of"))[:160]} if raw.get("back_of") else {}),
                         **({"back_seq": raw.get("back_seq")} if isinstance(raw.get("back_seq"), int)
                            and not isinstance(raw.get("back_seq"), bool) else {})})
        elif kind == "stillyours":
            # cr1#1706: a "Still yours?" press, its Undo, or the review's
            # refresh, for `still_yours.review_apply`, which checks each row
            # against the book again before it writes.
            import still_yours

            if verb not in still_yours.VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            row = {"id": cid, "action": verb, "title": str(raw.get("title") or "")[:200]}
            if raw.get("commitment_id"):
                row["commitment_id"] = str(raw.get("commitment_id"))[:80]
            if raw.get("project_id"):
                row["project_id"] = str(raw.get("project_id"))[:80]
            if verb == "undo" and isinstance(raw.get("undo"), list):
                row["undo"] = [t for t in raw["undo"][:still_yours.UNDO_TOKEN_CAP] if isinstance(t, dict)]
            stillyours.append(row)
        elif kind == "prepedit":
            # cr1#1788: the Prep page's edits (agenda, Goal, a call-guide add or
            # remove), for `hub_prep_edit`, which writes its own store
            import hub_prep_edit

            if verb not in hub_prep_edit.VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            typed = raw.get("input") if isinstance(raw.get("input"), dict) else {}

            def _p(name, cap):
                v = raw.get(name) if raw.get(name) not in (None, "") else typed.get(name)
                return str(v or "")[:cap] if isinstance(v, (str, int)) and not isinstance(v, bool) else ""

            prepedit.append({"id": cid, "action": verb, "meeting_key": _p("meeting_key", 64) or _p("n", 64),
                             "start": _p("start", 40), "moved_from": _p("moved_from", 40),
                             "text": _p("text", hub_prep_edit.TEXT_CAP), "guide_kind": _p("guide_kind", 20),
                             "item_id": _p("item_id", 40), "back_of": _p("back_of", 160),
                             "created_at": str(raw.get("created_at") or "")[:40]})
        elif kind == "focus":
            # cr1#1785: Focus, the list the owner picks: an item put in, taken
            # out, or the list's order, for `hub_focus.apply_choices`, which
            # writes its own store and never the item.
            import hub_focus

            if verb not in hub_focus.VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            typed = raw.get("input") if isinstance(raw.get("input"), dict) else {}
            row = {"id": cid, "action": verb, "title": str(raw.get("title") or "")[:200],
                   "commitment_id": str(raw.get("commitment_id") or raw.get("n") or "")[:80],
                   # when it was pressed: one Update's Focus presses apply in that order
                   "created_at": str(raw.get("created_at") or "")[:40]}
            if verb == "order":
                ids = typed.get("order") if isinstance(typed.get("order"), list) else raw.get("order")
                row["order"] = [str(x)[:80] for x in (ids if isinstance(ids, list) else [])[:hub_focus.ORDER_CAP]
                                if isinstance(x, (str, int)) and not isinstance(x, bool)]
            if verb == "undo":
                # the press this Undo takes back, by its id (review round 1 of #1797)
                row["back_of"] = str(raw.get("back_of") or typed.get("back_of") or "")[:160]
            fc = str(raw.get("from_choice") or typed.get("from_choice") or "")[:160]
            if verb == "add" and fc:
                # cr1#1785 page part: "Add to my list" puts a typed item in Focus
                # before it has an id; the pick names the to-do add's own press,
                # resolved when the same Update has written it (`apply_run`)
                row["from_choice"], row["commitment_id"] = fc, ""
            focus.append(row)
        elif kind == "settings":
            # cr1#781: the Settings tab's edit, for `apply_settings_choices`.
            if verb not in SETTINGS_VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            typed = raw.get("input") if isinstance(raw.get("input"), dict) else {}
            settings.append({"id": cid, "n": str(raw.get("n") or ""), "action": verb,
                             "input": str(typed.get("text") or "")[:NOTE_CAP],
                             "title": str(raw.get("title") or "")[:200]})
        elif kind == "meeting":
            # cr1#1632 slice 1: a meeting's live prep page, saved (or the save
            # taken back), written by apply_hub_choices through hub_meeting.
            if verb not in MEETING_VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            hub.append({"id": cid, "kind": kind, "action": verb, "n": str(raw.get("n") or "")[:512],
                        "input": raw.get("input") if isinstance(raw.get("input"), dict) else {},
                        "title": str(raw.get("title") or "")[:200]})
        elif kind in ("reminder", "todo", "waiting", "miss") or kind in EDIT_KINDS:
            # cr1#662: the Today page's own lists, written by apply_hub_choices;
            # cr1#1017: the header's "Missed something?", answered there too;
            # cr1#1383: a work stream, person or priority added, renamed or
            # taken back (`hub_edit`), written there as well; slice 4: a
            # commitment's wording edited, or that edit taken back.
            verbs = (REMINDER_VERBS if kind == "reminder" else TODO_VERBS if kind == "todo"
                     else WAITING_VERBS if kind == "waiting"
                     else MISS_VERBS if kind == "miss" else _edit_verbs(kind))
            if verb not in verbs:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            row = {"id": cid, "kind": kind, "action": verb, "n": raw.get("n"),
                   "input": raw.get("input"), "title": str(raw.get("title") or "")[:200]}
            if kind == "commitment_edit" and raw.get("created_at"):
                # Review of #1606: when the Edit was pressed, so a date press can
                # tell a Week plan this run landed first (`hub_edit._just_planned`).
                row["created_at"] = str(raw.get("created_at"))[:40]
            hub.append(row)
        else:
            refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=kind or "none"),
                            "reason": "unknown_kind"})
    return {"commitment": commitment, "inbox": inbox, "hub": hub, "review": review, "week": week,
            "settings": settings, "mail": mail, "people": people, "stillyours": stillyours,
            "prepedit": prepedit, "focus": focus, "refused": refused, "skipped": skipped,
            "n_waiting": (len(commitment) + len(inbox) + len(hub) + len(review) + len(week)
                          + len(settings) + len(mail) + len(people) + len(stillyours) + len(prepedit)
                          + len(focus)),
            "n_refused": len(refused)}


def apply_mail_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                       now_iso=None, resolved_by: str = "") -> Dict[str, Any]:
    """WRITE. The inbox's own presses on the owner's mail rules (cr1#1020):
    Drop a thread, teach a rule from one email's note, add a rule by hand,
    remove one, bring a dropped thread back. `choices` are `plan_apply`'s
    `mail` list. Each writes `_hq/data/mail-rules.json` through
    `mail_rules.apply_choices` and answers `{id, outcome, line}`; nothing is
    sent and the book is not touched. `resolved_by` is taken for the apply
    leg's one call shape and not used: the rules are the workspace's own."""
    import mail_rules
    from apply_audit import build_apply_choices_applied_event
    from event_gate import append_event

    out = mail_rules.apply_choices(workspace_root, choices, now_iso=_iso(_now(now_iso)))
    # The book keeps one audit row for the batch, like every Hub writer: what
    # was pressed and how it landed, never the rule's words.
    status = {"applied": "updated", "already": "already_resolved", "refused": "refused"}
    audit = [{"n": r.get("id"), "action": str(next((c.get("action") for c in choices or []
                                                     if isinstance(c, dict) and c.get("id") == r.get("id")), "") or ""),
              "handler_result": {"status": status.get(r.get("outcome"), "error")}, "said": r.get("outcome")}
             for r in out.get("results") or []]
    if audit:
        try:
            ev = build_apply_choices_applied_event(source=INBOX_SRC, actions=audit)
            ev["data"]["surface"] = MAIL_SURFACE
            append_event(Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl", ev, holder="cockpit_helpers")
        except Exception as exc:  # noqa: BLE001 - the rules landed; the answer says the audit did not
            out["audit_error"] = f"{type(exc).__name__}: {exc}"
    return out


def apply_people_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                         now_iso=None, resolved_by: str = "") -> Dict[str, Any]:
    """WRITE. The People tab's stars (cr1#1020 slice 3): a person starred to
    Home or unstarred, in `_hq/data/hub-stars.json` through
    `people_view.apply_choices`, then one `apply_choices_applied` audit row.
    `choices` are `plan_apply`'s `people` list. Nothing is sent; no record
    of the person changes. `resolved_by` is taken for the apply leg's one
    call shape and not used."""
    import people_view
    from apply_audit import build_apply_choices_applied_event
    from event_gate import append_event

    out = people_view.apply_choices(workspace_root, choices, now_iso=_iso(_now(now_iso)))
    status = {"applied": "updated", "already": "already_resolved", "refused": "refused"}
    by_id = {c.get("id"): c for c in choices or [] if isinstance(c, dict)}
    audit = [{"n": (by_id.get(r.get("id")) or {}).get("person_id") or r.get("id"),
              "action": str((by_id.get(r.get("id")) or {}).get("action") or ""),
              "handler_result": {"status": status.get(r.get("outcome"), "error")}, "said": r.get("outcome")}
             for r in out.get("results") or []]
    if audit:
        try:
            ev = build_apply_choices_applied_event(source=COMMITMENT_SRC, actions=audit)
            ev["data"]["surface"] = PEOPLE_SURFACE
            append_event(Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl", ev, holder="cockpit_helpers")
        except Exception as exc:  # noqa: BLE001 - the stars landed; the answer says the audit did not
            out["audit_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _apply_prepedit(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                    now_iso=None, resolved_by: str = "") -> Dict[str, Any]:
    """WRITE. The Prep page's edits (cr1#1788): the agenda, the Goal and the
    call guide's adds and removes, in `_hq/data/hub-prep-edits.json` through
    `hub_prep_edit.apply_choices`, then one `apply_choices_applied` audit row.
    No prep file and no book record is changed. `resolved_by` is taken for
    the save's one call shape and not used."""
    import hub_prep_edit
    from apply_audit import build_apply_choices_applied_event
    from event_gate import append_event

    out = hub_prep_edit.apply_choices(workspace_root, choices, now_iso=_iso(_now(now_iso)))
    status = {"applied": "updated", "already": "already_resolved", "refused": "refused"}
    by_id = {c.get("id"): c for c in choices or [] if isinstance(c, dict)}
    audit = [{"n": (by_id.get(r.get("id")) or {}).get("meeting_key") or r.get("id"),
              "action": "prep " + str((by_id.get(r.get("id")) or {}).get("action") or ""),
              "handler_result": {"status": status.get(r.get("outcome"), "error")}, "said": r.get("outcome")}
             for r in out.get("results") or []]
    if audit:
        try:
            ev = build_apply_choices_applied_event(source=COMMITMENT_SRC, actions=audit)
            ev["data"]["surface"] = PREPEDIT_SURFACE
            append_event(Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl", ev, holder="cockpit_helpers")
        except Exception as exc:  # noqa: BLE001 - the store landed; the answer says the audit did not
            out["audit_error"] = f"{type(exc).__name__}: {exc}"
    return out


def feed_prepx(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. `feed/prepx` (cr1#1788): the Prep page's edits, keyed by each
    occurrence (`<jk>|<start>`, the row's own on `feed/today`), in a window
    around today and under its own size budget, for the page to lay over the
    prep `feed/today` carries (`hub_prep_edit.feed`). Nobody hidden is named."""
    import hub_prep_edit

    return hub_prep_edit.feed(workspace_root, now_iso=_iso(_now(now_iso)))


#: The longest note the owner may leave on a Done (cr1#1785, the To do box).
CLOSE_NOTE_CAP = 500


def _close_note(c: Dict[str, Any]) -> Dict[str, Any]:
    """cr1#1785 page part: the owner's note typed in the Done box, kept on
    the closed record as the reason it closed (`close_note`). One press, so
    its Undo is the Done's own. `{}` with no note. Pure."""
    typed = c.get("input") if isinstance(c.get("input"), dict) else {}
    note = " ".join(str(typed.get("note") or "").split())[:CLOSE_NOTE_CAP]
    return {"close_note": note} if note and str(c.get("action") or "") == "done" else {}


def _apply_focus(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                 now_iso=None, resolved_by: str = "") -> Dict[str, Any]:
    """WRITE. Focus, the list the owner picks (cr1#1785): an item put in,
    taken out, or the list reordered, in `_hq/data/hub-focus.json` through
    `hub_focus.apply_choices`, then one `apply_choices_applied` audit row.
    `choices` are `plan_apply`'s `focus` list. No item's record changes (its
    due date least of all); nothing is sent. `resolved_by` is taken for the
    save's one call shape and not used."""
    import hub_focus
    from apply_audit import build_apply_choices_applied_event
    from event_gate import append_event

    out = hub_focus.apply_choices(workspace_root, choices, now_iso=_iso(_now(now_iso)))
    status = {"applied": "updated", "already": "already_resolved", "refused": "refused"}
    by_id = {c.get("id"): c for c in choices or [] if isinstance(c, dict)}
    audit = [{"n": (by_id.get(r.get("id")) or {}).get("commitment_id") or r.get("id"),
              "action": "focus " + str((by_id.get(r.get("id")) or {}).get("action") or ""),
              "handler_result": {"status": status.get(r.get("outcome"), "error")}, "said": r.get("outcome")}
             for r in out.get("results") or []]
    if audit:
        try:
            ev = build_apply_choices_applied_event(source=COMMITMENT_SRC, actions=audit)
            ev["data"]["surface"] = FOCUS_SURFACE
            append_event(Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl", ev, holder="cockpit_helpers")
        except Exception as exc:  # noqa: BLE001 - the store landed; the answer says the audit did not
            out["audit_error"] = f"{type(exc).__name__}: {exc}"
    return out


def feed_focus(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. Focus, the list the owner picks (cr1#1785): what is in it, in
    the owner's order (`hub_focus.feed`), its own document so the plate and
    the people rows carry no new field (ruling 111, #1768, #1751). Nobody
    hidden is named (`_hide_people_in_rows`, the plate's own rule)."""
    import hub_focus

    hp = _hidden_people(workspace_root)
    feed = hub_focus.feed(workspace_root, now_iso=_iso(_now(now_iso)), names=_person_names(workspace_root))
    rows = feed.get("rows") or []
    # cr1#1785 page part, ruling 128c: each row's source line, as the plate
    # gives one, so pressing a To do row can show where it came from
    origins = _origins_by_id(workspace_root, hp)
    for r in rows:
        if isinstance(r, dict):
            r["origin"] = origins.get(str(r.get("id")))
    # audit of #1823 (F4): a Focus row offers Move and Drop as the retired
    # to-do row did; Move needs the item's project and its side of the
    # personal line, read as feed_mine reads them for a to-do
    places, pnames, personal, known = _focus_projects(workspace_root, _now(now_iso))
    for r in rows:
        if isinstance(r, dict):
            pid = places.get(str(r.get("id")))
            r["personal_lane"] = str(r.get("id")) in personal
            if pid:
                r["project_id"] = pid
                r["project"] = pnames.get(pid, "")[:120]
    feed["lanes_known"] = known
    _hide_people_in_rows(rows, hp)
    for r in rows:
        if isinstance(r, dict):
            r.pop("who_id", None)
    return feed


def _focus_projects(workspace_root, now) -> Tuple[Dict[str, str], Dict[str, str], set, bool]:
    """(project id by item, project name by id, the items on the personal
    lane, whether the lanes could be read), as `feed_mine` reads them for a
    to-do's Put in... (review round 1 of #1700). Nothing read: no project and
    lanes unknown, so the page offers no Move (never a guessed one)."""
    try:
        import thread_cards as tc

        places = _projects_of(workspace_root, now)
        fence, personal = _lanes(workspace_root, places)
        cards = tc.read_cards(Path(str(workspace_root)))[0]
        names = {k: str((v or {}).get("name") or "")
                 for k, v in {**(cards.get("threads") or {}), **(cards.get("work_streams") or {})}.items()}
        return places, names, set(personal), fence is not None
    except Exception:  # noqa: BLE001
        return {}, {}, set(), False


def _origins_by_id(workspace_root, hp: Dict[str, Any]) -> Dict[str, Any]:
    """`{commitment id: its origin}` over the open items, exactly as
    `feed_plate` reads them (`_row_origin`); `{}` when the book cannot be
    read (no source line is shown then, never a wrong one)."""
    try:
        from cru_match import _commitment_id, load_open_commitments

        ep = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
        names = _person_names(workspace_root)
        owner = _owner_id(workspace_root)
        tzinfo = _workspace_tz(workspace_root)
        opens = load_open_commitments(ep) if ep.exists() else []
        ties = None if hp["unread"] else hp["ids"]
        return {str(_commitment_id(ev)): _row_origin(ev, names, owner, tzinfo, ties, hp) for ev in opens}
    except Exception:  # noqa: BLE001
        return {}


def feed_allopen(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. To do's All open (cr1#1785 page part, ruling 128l): every open
    item, lean (`hub_focus.all_open`), so the page's search finds an item
    wherever it sits, past the plate's row cap too. Its own document; the
    plate and the people rows take no new field. Nobody hidden is named,
    and an item tied to someone hidden is left out (the plate's own rule)."""
    import hub_focus

    hp = _hidden_people(workspace_root)
    now_s = _iso(_now(now_iso))
    if hp.get("unread"):
        # audit of #1823 (F3): who is off limits could not be read, so nothing
        # is listed, and the page says why instead of "Nothing open."
        return {"generated_at": now_s, "ok": False, "line": ALLOPEN_FENCE_UNREAD_LINE, "rows": [], "n": 0,
                "complete": False}
    # audit of #1823 (F2): the wall is applied to the whole book before
    # anything is counted, so `n` never counts an item the page may not list
    doc = hub_focus.all_open(workspace_root, now_iso=now_s, names=_person_names(workspace_root),
                             threads=_thread_names(workspace_root),
                             hidden_ids=set(str(i) for i in hp.get("ids") or ()))
    kept = [r for r in doc.get("rows") or [] if isinstance(r, dict)]
    _hide_people_in_rows(kept, hp)
    for r in kept:
        r.pop("who_id", None)
    doc["rows"] = kept
    return doc


#: Review round 1 of #1823 (should-fix 4): the to-do landed but its Focus pick did not.
TODO_NOT_IN_FOCUS_LINE = "It is on your list, but it could not be put in Focus; star it in All open."
#: Audit of #1823 (F3): All open with the off-limits list unread.
ALLOPEN_FENCE_UNREAD_LINE = ("Your open items could not be listed just now: who is off limits could not be read. "
                             "The next update tries again.")


def feed_people(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The People tab and the starred people on Home (cr1#1020 slice 3):
    every person on file but the owner, with company, how known, last
    contact and what is open between the owner and them
    (`people_view.people`)."""
    import people_view

    hp = _hidden_people(workspace_root)
    feed = people_view.people(workspace_root, now_iso=_iso(_now(now_iso)),
                              hidden_ids=None if hp.get("unread") else set(hp.get("ids") or ()))
    return _fit_people(_with_words(workspace_root, _hide_people_in_feed(feed, hp)))


#: cr1#1738: what the People tab says when the fence cannot be read.
PEOPLE_FENCE_UNREAD_LINE = "Your people could not be listed just now: who is off limits could not be read. The next refresh tries again."


def _hide_people_in_feed(feed: Dict[str, Any], hp: Dict[str, Any]) -> Dict[str, Any]:
    """cr1#1738 (ruling 105): someone off limits or tied to the personal side
    is not on the People tab at all (they are managed in Settings, where the
    off-limits list lives), nor starred, nor counted. With the fence unread,
    nobody is listed and the feed says why."""
    if not isinstance(feed, dict):
        return feed
    people = [r for r in feed.get("people") or [] if isinstance(r, dict)]
    if hp.get("unread"):
        return dict(feed, people=[], starred=[], n_people=0, not_shown={"unread": True},
                    errors=list(feed.get("errors") or []) + ["fence"], line=PEOPLE_FENCE_UNREAD_LINE)
    ids = hp.get("ids") or frozenset()
    # a second layer: people_view left them out before its cap already
    kept = [r for r in people if str(r.get("id") or "") not in ids]
    gone = len(people) - len(kept)
    return dict(feed, people=kept, starred=[x for x in feed.get("starred") or [] if str(x) not in ids],
                n_people=max(0, int(feed.get("n_people") or 0) - gone))


#: cr1#1715: the People feed is one page document, pulled whole; at about 400
#: people with open items it came out near 370,000 characters, over
#: `REFRESH_DOC_LIMIT`, so the tab was never published again. It is cut to
#: fit from the end of its order (the starred first, then the new, then the
#: most recently in touch), and the page says how many more there are from
#: `n_people`, as it already does past `people_view.PEOPLE_CAP`.
#: cr1#1751: 182,500 leaves 15% of `REFRESH_DOC_LIMIT` free (it was 190,000,
#: 88% of the limit, on a heavy book).
PEOPLE_FEED_BUDGET = 182_500
#: The page's first screen of people: the starred, then this many more
#: (`PEOPLE_FIRST` in the page). Rows past it travel trimmed when the feed
#: is over its budget (review of #1718, item 2).
PEOPLE_FIRST_SCREEN = 20
PEOPLE_TRIM_OPEN = 1
PEOPLE_LIST_TRIMMED_NOTE = "This list is long; ask in a chat to send it."


def _people_needs_you(row: Dict[str, Any]) -> bool:
    nxt = row.get("next_step") or {}
    return bool(nxt.get("overdue")) or int(row.get("n_owed_to_you") or 0) > 0


def _trim_person(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    if isinstance(out.get("open"), list) and len(out["open"]) > PEOPLE_TRIM_OPEN:
        out["open"] = out["open"][:PEOPLE_TRIM_OPEN]
    lists = out.get("lists")
    if isinstance(lists, dict):
        out["lists"] = {k: ({"note": PEOPLE_LIST_TRIMMED_NOTE, "n": v.get("n")} if isinstance(v, dict) and "body" in v else v)
                        for k, v in lists.items()}
    out["trimmed"] = True
    return out


def _fit_people(feed: Dict[str, Any]) -> Dict[str, Any]:
    """Under `PEOPLE_FEED_BUDGET` (cr1#1715). Nothing changes when it fits.
    Over it: the starred first, then the people with an overdue next step or
    something owed to the owner, then the rest, each in the feed's order;
    rows past the page's first screen travel with their first few open items
    and their lists as a note (their counts stay whole); only then are rows
    cut from the end, and the feed says `cut_to_fit` (the page says how many
    it holds)."""
    people = feed.get("people")
    if not isinstance(people, list):
        return feed

    def size(rows, **marks) -> int:
        # cr1#1751: the fit's own marks are counted, so the feed is never over
        # its budget by their length (it was, by up to 41 characters)
        return len(json.dumps(dict(feed, people=rows, **marks), separators=(",", ":"), default=str))

    if size(people) <= PEOPLE_FEED_BUDGET:
        return feed
    starred = [r for r in people if r.get("starred")]
    rest = [r for r in people if not r.get("starred")]
    ordered = starred + [r for r in rest if _people_needs_you(r)] + [r for r in rest if not _people_needs_you(r)]
    screen = len(starred) + PEOPLE_FIRST_SCREEN
    rows = ordered[:screen] + [_trim_person(r) for r in ordered[screen:]]
    out = dict(feed, people=rows, trimmed_to_fit=True)
    if size(rows, trimmed_to_fit=True) <= PEOPLE_FEED_BUDGET:
        return out
    lo, hi = 0, len(rows)  # the most that fit is in [lo, hi)
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if size(rows[:mid], trimmed_to_fit=True, cut_to_fit=True) <= PEOPLE_FEED_BUDGET:
            lo = mid
        else:
            hi = mid
    return dict(out, people=rows[:lo], cut_to_fit=True)


def feed_mail_rules(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The Inbox tab's rules list (cr1#1020): the owner's mail rules in
    force and the threads dropped, each with one plain line
    (`mail_rules.feed`)."""
    import mail_rules

    out = mail_rules.feed(workspace_root, now_iso=_iso(_now(now_iso)))
    # cr1#1538: what the page may list by itself (the personal wall and
    # off-limits, from the functions the tasks use). An older page ignores it.
    import hub_live

    out["live"] = hub_live.wall(workspace_root)
    return _with_words(workspace_root, out)


#: cr1#1765 (owner ruling 112): the off-limits wall as its own small
#: document. Every Update publishes it first, before any heavy part, and again
#: after a save that changed the mail rules; set-up publishes it when it seeds
#: the page. A part that runs out of time can no longer take it down. The
#: mail-rules feed keeps its `live` copy for released pages; the page reads
#: whichever of the two is newer.
WALL_DOC = "feed/wall"
WALL_KIND = "hub_wall"
WALL_NOT_SAVED_LINE = "The off-limits list could not be read from the saved answer, so it was not published."
#: Review round 1 of #1772 (MUST 2): a wall built before the one the page has
#: is never written over it.
WALL_OLDER_LINE = "The page already has a newer off-limits list, so this one was not published."
#: Ruled into #1772: a wall dated more than this far ahead of this code's
#: clock is never published (the page would read it as no wall anyway, and a
#: stored one would hold back every correct wall until the clock caught up).
WALL_AHEAD_MAX_S = 300
WALL_AHEAD_LINE = ("The off-limits list is dated more than five minutes ahead of this computer's clock, "
                   "so it was not published.")
#: Ruled into #1772: without the page's own feed read the older-wall fence
#: cannot be checked, so nothing is published.
WALL_NO_STORED_LINE = ("The page's own copy was not read first, so the off-limits list was not published "
                       "(an older list could otherwise replace a newer one). Do not run it again with --stored-empty: "
                       "that is only for a read that listed no documents.")
#: Fence fix after #1772: `--stored-empty` proves itself. The saved answer of
#: the page's `list` on `feed` must be the tool's own one line for an empty
#: collection, read from a real page (desktop session, 2026-11-25):
#: `No documents matched in collection "<collection>".` (no folder is made
#: then). A list that found documents opens with `1 document from collection
#: "<c>" saved as JSON files under "<dir>":` or `<n> documents from ...`, one
#: `- "<doc_id>"  <N> bytes  version <V>  "<path>"` line per document after
#: it (`WALL_LISTING_RE` reads that head, for cr1#1805's version reader).
WALL_EMPTY_LIST_RE = re.compile(r'^No\s+documents\s+matched\s+in\s+collection\s+"([^"]*)"\s*\.?\s*$')
WALL_LISTING_RE = re.compile(r'^(\d+)\s+documents?\s+from\s+collection\s+"([^"]*)"\s+saved\s+as\s+JSON\s+files\s+under\s+"([^"]*)"\s*:?\s*$')
WALL_EMPTY_UNPROVEN_LINE = ("The page's own read did not show an empty list, so the off-limits list was not published "
                            "(an older list could otherwise replace a newer one).")


def _listing_text(raw: str) -> str:
    """The text of a saved tool answer: as it came, or, when it was saved as
    JSON (the tool's content blocks), its string values joined in order, a
    block's `type` left out."""
    vals = _json_values(raw)
    if not vals:
        return raw
    out: List[str] = []

    def walk(v: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k != "type":
                    walk(x, depth + 1)
    for v in vals:
        walk(v)
    return "\n".join(out)


def feed_listed_none(listing_file: str) -> bool:
    """True only when `listing_file` is the saved answer of a `list` on the
    page's `feed` that found nothing: exactly one line, the tool's own
    `No documents matched in collection "feed".` (spacing, a missing full
    stop, a byte-order mark and line endings tolerated). Anything else, a
    list that found documents, another collection, any other words, or a
    file that cannot be read, is False."""
    try:
        raw = Path(listing_file).read_text(encoding="utf-8-sig")
    except (OSError, ValueError, TypeError):
        return False
    lines = [ln.strip() for ln in _listing_text(raw).lstrip("\ufeff").splitlines() if ln.strip()]
    if len(lines) != 1:
        return False
    m = WALL_EMPTY_LIST_RE.match(lines[0])
    if not m or m.group(1) != "feed":
        return False
    return True


#: Review round 1 of #1772 (MUST 1): every list a wall carries. A wall whose
#: list is missing or not a list is refused here and read as closed on the page.
WALL_LIST_FIELDS: Tuple[str, ...] = ("plan", "walled", "off_limits", "off_limits_names", "own", "own_names")


def wall_digest(doc: Dict[str, Any]) -> str:
    """The SHA-256 of a wall document in one fixed form (sorted keys, no
    spaces): `feed_wall` answers it beside the document, and `wall-batch`
    refuses an answer whose document does not match it, so a hand-copied
    answer that lost or changed one hash is never published."""
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def wall_sound(doc: Any) -> bool:
    """A wall the page may read: `ok` a boolean, every list field a list,
    `known_names` a list or null, and a readable `generated_at`."""
    if not isinstance(doc, dict) or not isinstance(doc.get("ok"), bool):
        return False
    if any(not isinstance(doc.get(k), list) for k in WALL_LIST_FIELDS):
        return False
    if doc.get("known_names") is not None and not isinstance(doc.get("known_names"), list):
        return False
    # cr1#1845: the word-form list is optional, but one that is there and not a list is no wall
    if "off_limits_words" in doc and not isinstance(doc.get("off_limits_words"), list):
        return False
    return _parse(doc.get("generated_at")) is not None


def feed_wall(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The wall document (cr1#1765): `{ok, kind: "hub_wall", doc}`,
    `doc` being `hub_live.wall`'s answer plus `generated_at`. It reads the
    people on file, the mail rules and the accounts, never the log, so it is
    quick on any book. A wall that cannot be read is published closed
    (`doc.ok` false): the page then lists nothing of its own and names no one."""
    import hub_live

    doc = dict(hub_live.wall(workspace_root))
    doc["generated_at"] = _iso(_now(now_iso))
    return {"ok": True, "kind": WALL_KIND, "doc": doc, "digest": wall_digest(doc)}


def _wall_found(obj: Any, out: List[Dict[str, Any]], depth: int = 0) -> None:
    """Every `feed_wall` answer inside a saved tool answer, however wrapped."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        if obj.get("kind") == WALL_KIND and isinstance(obj.get("doc"), dict):
            out.append(obj)
            return
        for v in obj.values():
            _wall_found(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _wall_found(v, out, depth + 1)
    elif isinstance(obj, str) and len(obj) > 20 and "{" in obj:
        for parsed in _json_values(obj):
            if not isinstance(parsed, str):
                _wall_found(parsed, out, depth + 1)


def wall_batch(answer_file: str, out_dir: str, stored_dir: str = "", *, now_iso=None,
               stored_empty: str = "") -> Dict[str, Any]:
    """Run where the page's database tool runs (cr1#1765): the saved answer
    of `feed_wall`, as the tool returned it, becomes one batch, a `set` of
    `feed/wall` from a file, so the document never passes through the run's
    typing. Reads no workspace. Answers `{ok, batches, line}`; `ok: false`
    with a line when the answer holds no wall, or one with no `ok` or no
    readable `generated_at` (nothing is published then: the page keeps the
    copy it has, and a copy older than a day lists nothing).

    Review round 1 of #1772: the document must match the answer's `digest`
    and every list field must be a list (MUST 1), or nothing is published.
    `stored_dir` is the page's `feed` read saved as files (`list` on `feed`
    with `out_dir`): a wall older than the stored one is refused (MUST 2),
    and the write is pinned to the stored version, so a newer wall another
    run wrote in between is never overwritten.

    Ruled into #1772: `stored_dir` is required, and must be the folder the
    read was saved into, holding its `feed/` folder; without it nothing is
    published (the fence holds when the argument is left out). The one
    exception is `stored_empty`, the saved answer of the run's `list` on
    `feed`: when it proves the list listed no documents (a new page, whose
    read may save no folder at all, `feed_listed_none`), there is no wall to
    protect and the first one is written with no pin. (That write has no
    create-only guard: a wall another computer wrote between the read and
    the batch would be replaced, a narrow window accepted here.) A wall dated more than five minutes
    ahead of `now_iso` (this code's clock by default) is refused, saying why."""
    try:
        raw = Path(answer_file).read_text(encoding="utf-8")
    except OSError:
        return {"ok": False, "batches": [], "line": WALL_NOT_SAVED_LINE}
    found: List[Dict[str, Any]] = []
    for val in _json_values(raw) or [raw]:
        _wall_found(val, found)
    doc = found[-1]["doc"] if found else None
    given = str(found[-1].get("digest") or "") if found else ""
    if not wall_sound(doc) or not given or wall_digest(doc) != given:
        return {"ok": False, "batches": [], "line": WALL_NOT_SAVED_LINE}
    made = _parse(doc.get("generated_at"))
    now = _parse(now_iso) if now_iso else _dt.datetime.now(_dt.timezone.utc)
    if made is None or now is None:
        return {"ok": False, "batches": [], "line": WALL_NOT_SAVED_LINE}
    if (made - now).total_seconds() > WALL_AHEAD_MAX_S:
        return {"ok": False, "batches": [], "line": WALL_AHEAD_LINE}
    if stored_empty:
        if stored_dir or not feed_listed_none(stored_empty):
            return {"ok": False, "batches": [], "line": WALL_EMPTY_UNPROVEN_LINE}
        held = (None, None)
    elif not stored_dir or not (Path(stored_dir) / "feed").is_dir():
        return {"ok": False, "batches": [], "line": WALL_NO_STORED_LINE}
    else:
        held = next(((f, v) for d, f, v in _saved_docs(stored_dir, "feed") if d == "wall"), (None, None))
    stored, version = held
    was = _parse(stored.get("generated_at")) if isinstance(stored, dict) else None
    if was is not None and was > made:
        return {"ok": False, "batches": [], "line": WALL_OLDER_LINE}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "feed__wall.json"
    target.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    write: Dict[str, Any] = {"op": "set", "collection": "feed", "doc_id": "wall", "file_path": str(target)}
    if version is not None:
        write["if_version"] = version
    return {"ok": True, "line": "", "batches": [[write]]}


#: cr1#1765: the sources the Hub page reads live, in the order set-up asks
#: for them, and the words for each. Live reads happen on the page only,
#: while it is open: no task reads a live source, because no task can be
#: asked for approval.
#: Second review of #1772: the calendar is not one of them. The page calls
#: the calendar only when the owner presses a booking button (Superhuman and
#: Microsoft 365 look up free time then, with that press's own approval) and
#: never reads it live by itself; a seat whose page lists the calendar live
#: (the live Today, cr1#1758) adds it here with that list as its test read.
#: cr1#1771: the live Today does, through `calendar_live` (Google Calendar's
#: list read, the only one verified), so the calendar is a live source on
#: exactly the seats `calendar_live` gives a plan for, and on no other.
LIVE_SOURCES: Tuple[str, ...] = ("mail", "chat", "calendar")
LIVE_SOURCE_WORDS: Dict[str, str] = {"mail": "mail", "chat": "chat", "calendar": "calendar"}
LIVE_SOURCE_STATES: Tuple[str, ...] = ("ok", "refused", "skipped", "failed")
LIVE_SOURCES_DOC = "config/live_sources"


def live_sources(workspace_root) -> Dict[str, Any]:
    """READ. Which live sources this seat's page reads (cr1#1765), from the
    same plans the page is rendered with: mail when the mail plan can list,
    chat when a chat plan is given, the calendar when `calendar_live` gives
    the page its own calendar read (cr1#1771). `{sources: [...], words: {...}}`."""
    mail = mail_direct(workspace_root) or {}
    chat = chat_direct(workspace_root) or {}
    have = {"mail": bool(mail.get("list") and mail.get("list_plan")),
            "chat": bool(chat.get("list")),
            "calendar": bool(calendar_live(workspace_root))}
    srcs = [s for s in LIVE_SOURCES if have[s]]
    return {"sources": srcs, "words": {s: LIVE_SOURCE_WORDS[s] for s in srcs}}


def _and_words(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def live_sources_check(workspace_root, doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """READ. Set-up's last check (cr1#1765): `doc` is `config/live_sources`
    as the page wrote it (`{sources: {<source>: {state, at}}}`), or None.
    Set-up is done only when every live source this seat's page reads is
    `ok` (its one test read worked) or `skipped` (the owner chose not to).
    Answers `{done, sources, ok, skipped, refused, waiting, line}`."""
    want = live_sources(workspace_root)["sources"]
    held = (doc or {}).get("sources") if isinstance(doc, dict) else None
    held = held if isinstance(held, dict) else {}
    seen: Dict[str, str] = {}
    for src in want:
        row = held.get(src)
        st = str(row.get("state") or "") if isinstance(row, dict) else ""
        seen[src] = st if st in LIVE_SOURCE_STATES else ""
    ok = [s for s in want if seen[s] == "ok"]
    skipped = [s for s in want if seen[s] == "skipped"]
    refused = [s for s in want if seen[s] in ("refused", "failed")]
    waiting = [s for s in want if not seen[s]]
    done = not refused and not waiting
    words = LIVE_SOURCE_WORDS
    if not want:
        line = "This page reads nothing live, so there is nothing to allow."
    elif done:
        line = ("The page reads " + _and_words([words[s] for s in ok]) + " live." if ok else "") \
            + (" You skipped " + _and_words([words[s] for s in skipped]) + "." if skipped else "")
        line = line.strip()
    elif refused:
        line = ("The page was not allowed to read " + _and_words([words[s] for s in refused])
                + ". On the Hub, press Allow beside it and allow it when the page asks, or press Skip, then say done.")
    else:
        line = ("Set-up is not finished: on the Hub, press Allow (or Skip) beside "
                + _and_words([words[s] for s in waiting]) + ", then say done.")
    return {"done": done, "sources": want, "ok": ok, "skipped": skipped, "refused": refused,
            "waiting": waiting, "line": line}


#: The Brief tab (cr1#1310): today's morning brief as the Morning Brief run
#: saved it (`morning-briefing/SKILL.md`, the snapshot of what it posted).
BRIEF_DIR = Path("_hq") / "briefings"
BRIEF_TEXT_CAP = 16000
BRIEF_NONE_LINE = "No morning brief has run today."
BRIEF_CUT_LINE = "(The brief continues in the Morning Brief chat.)"
_BRIEF_FILE_RE = re.compile(r"^morning-(\d{4}-\d{2}-\d{2})\.md$")
_BRIEF_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BRIEF_BOLD_HEAD_RE = re.compile(r"^(?:\*\*([^*]+)\*\*|__([^_]+)__):?\s*$")
_BRIEF_ITEM_RE = re.compile(r"^[-*•]\s+(.*\S)\s*$")
_BRIEF_NUM_RE = re.compile(r"^(\d{1,3})[.)]\s+(.*\S)\s*$")
_BRIEF_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _brief_plain(text: str) -> str:
    """One line of the brief without its markdown marks: links keep their
    words, bold and code lose their marks. Nothing else changes."""
    t = _BRIEF_LINK_RE.sub(r"\1", text)
    t = t.replace("**", "").replace("__", "").replace("`", "")
    return " ".join(t.split())


def _brief_sections(lines: List[str], cap: int) -> Tuple[Optional[str], List[Dict[str, Any]], bool]:
    """`(title, sections, truncated)`: the brief's first `# ` heading as its
    title, then one section per heading in the brief's order (the lines
    before the first heading are a section with no heading), each line a
    list item, a numbered line or text. Rules (`---`) and blank lines go.
    The saved brief opens with "Morning briefing - <date>" and no `#`
    (morning-briefing/SKILL.md), so a first line that names the brief is
    the title too. Headings and lines kept, with the cut line, stay within
    `cap` characters."""
    title: Optional[str] = None
    sections: List[Dict[str, Any]] = [{"heading": None, "lines": []}]
    room, size, cut, seen = cap - len(BRIEF_CUT_LINE), 0, False, False
    for raw in lines:
        line = raw.strip()
        if not line or (len(line) >= 3 and set(line) <= set("-*_=")):
            continue
        first, seen = not seen, True
        if first and title is None and _brief_plain(line).lower().startswith("morning brief"):
            title = _brief_plain(line.lstrip("#").strip())
            continue
        head = _BRIEF_HEAD_RE.match(line) or _BRIEF_BOLD_HEAD_RE.match(line)
        if head:
            text = _brief_plain(head.group(head.lastindex)).rstrip(":").strip()
            if not text:
                continue
            if title is None and line.startswith("# "):
                title = text
                continue
            if size + len(text) > room:
                cut = True
                break
            size += len(text)
            sections.append({"heading": text, "lines": []})
            continue
        m_item, m_num = _BRIEF_ITEM_RE.match(line), _BRIEF_NUM_RE.match(line)
        if m_item:
            row = {"kind": "item", "text": _brief_plain(m_item.group(1))}
        elif m_num:
            row = {"kind": "num", "n": int(m_num.group(1)), "text": _brief_plain(m_num.group(2))}
        else:
            row = {"kind": "text", "text": _brief_plain(line)}
        if not row["text"]:
            continue
        if size + len(row["text"]) > room:
            cut = True
            break
        size += len(row["text"])
        sections[-1]["lines"].append(row)
    if cut:
        sections.append({"heading": None, "lines": [{"kind": "text", "text": BRIEF_CUT_LINE}]})
    sections = [s for s in sections if s["lines"] or s["heading"]]
    return title, sections, cut


def _brief_made_at(root: Path, rel: str) -> Optional[_dt.datetime]:
    """When the saved brief at `rel` was made (cr1#1327): the time of the
    newest Morning Brief `pack_run` receipt whose `digest_path` names that file
    (the receipt `prep_leg.log_combined_receipt` writes, and
    `end_of_day.read_morning_digest` joins on). A rerun saves the file again,
    so the newest such receipt made the text shown; a receipt whose brief leg
    did not run saved nothing and is passed over. None when no receipt names
    it. The file's own time is never used: a sync or a copy can move it."""
    try:
        from receipts import iter_receipts
        from workspace_paths import normalize_persisted_path

        rows = iter_receipts(root, task_ids=["morning-brief"])
    except Exception:  # noqa: BLE001 - a read never breaks the feed
        return None
    best: Optional[_dt.datetime] = None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        at = r.get("dt")
        if r.get("type") != "pack_run" or not isinstance(at, _dt.datetime):
            continue
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        named = data.get("digest_path")
        if not isinstance(named, str) or not named.strip():
            continue
        legs = data.get("legs") if isinstance(data.get("legs"), dict) else {}
        if "brief" in legs and legs.get("brief") != "ran":
            continue  # a fire whose brief did not run saved no snapshot (cr1#148's rule)
        try:
            named = normalize_persisted_path(named, workspace_root=str(root)) or named
        except Exception:  # noqa: BLE001 - an odd pointer is compared as written
            pass
        if str(named).replace("\\", "/") != rel:
            continue
        if best is None or at > best:
            best = at
    return best


def feed_brief(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ (cr1#1310). The Brief tab's feed: today's morning brief as the
    Morning Brief run saved it to `_hq/briefings/morning-<date>.md`, the
    same text it posted, so the tab shows nothing the brief did not (its
    account-scope rules were applied when it was written). `brief` is
    `{for_date, path, made_at, title, sections, truncated}`, or None when no
    brief has run today; `made_at` is the time of the newest Morning Brief
    receipt that names that file (cr1#1327), or None when none does, never the
    file's own time, which a sync or a copy can move; `last_for_date` names the
    newest saved one; `today`
    and `tz` are the workspace's own date and zone, so the page tells a stale
    feed by the workspace's day, not the browser's. Reads
    that one file, the folder's file names and the Morning Brief's receipts,
    nothing else. Never raises."""
    now = _now(now_iso)
    out: Dict[str, Any] = {"generated_at": _iso(now), "today": None, "tz": None, "brief": None,
                           "last_for_date": None, "empty_line": BRIEF_NONE_LINE}
    if not workspace_root:
        return out
    root = Path(str(workspace_root))
    try:
        from day_intent import workspace_today

        today = workspace_today(root, now=now)
    except Exception:  # noqa: BLE001 - no zone set: the clock's own date
        today = now.astimezone(_workspace_tz(root)).date()
    out["today"] = today.isoformat()
    out["tz"] = _zone_name(root)
    folder = root / BRIEF_DIR
    try:
        dates = sorted(m.group(1) for p in folder.iterdir()
                       for m in [_BRIEF_FILE_RE.match(p.name)] if m and p.is_file())
    except OSError:
        dates = []
    if dates:
        out["last_for_date"] = dates[-1]
    if out["today"] not in dates:
        return _with_words(workspace_root, out)
    path = folder / f"morning-{out['today']}.md"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return _with_words(workspace_root, out)
    rel = (BRIEF_DIR / path.name).as_posix()
    made = _brief_made_at(root, rel)
    title, sections, cut = _brief_sections(text.splitlines(), BRIEF_TEXT_CAP)
    out["brief"] = {"for_date": out["today"], "path": rel,
                    "made_at": _iso(made) if made else None, "title": title, "sections": sections, "truncated": cut}
    return _with_words(workspace_root, out)


#: The Today page's own lists (cr1#662): reminders the owner adds, clears or
#: pushes, and priority to-dos the owner adds. Each is a choice the page saves
#: and the apply task writes, like every other press.
REMINDER_VERBS = frozenset({"add", "done", "push", "delete"})  # delete: cr1#1221
TODO_VERBS = frozenset({"add"})
#: cr1#1658 change 4: Waiting on them, made from a message.
WAITING_VERBS = frozenset({"add"})
WAITING_NEEDS_TEXT_LINE = "Waiting on them needs a few words."
WAITING_NO_PERSON_LINE = ("The sender is not in your People yet, so nothing was saved. "
                          "Add them there first, then press Waiting on them again.")
#: review round 1 of #1699 (B4): two people on file share the sender's address.
WAITING_SHARED_LINE = ("Two people in your People share this sender's address, so nothing was saved. "
                       "Give each their own address there, then press Waiting on them again.")
#: B1, B5: a make from a message is checked before anything is written.
MAIL_MAKE_FENCE_LINE = "Your account settings could not be read just now, so nothing was saved from this message. Try again after the next Refresh."
MAIL_MAKE_OFF_LIMITS_LINE = "That sender is off limits, so nothing was saved from this message."
MAIL_MAKE_BAD_LINE = "This message's link could not be kept, so nothing was saved. Refresh the Inbox and press it again."
MAIL_MAKE_NO_SENDER_LINE = "This message's sender was not sent with the press, so nothing was saved. Refresh the page and press it again."
#: fix check of round 1: the press names another sender than the thread's own, as the last Inbox run read it
MAIL_MAKE_SENDER_LINE = "The press names someone other than this message's sender, so nothing was saved. Refresh the Inbox and press it again."
#: review round 1 of #1717: what a To-do or Remind me from a person's card or a meeting refuses
PERSON_MAKE_OFF_LIMITS_LINE = "That person is off limits, so nothing was saved."
PERSON_MAKE_TIE_LINE = "That person is on your personal side, so nothing was put on a project; nothing was saved."
MEETING_MAKE_UNKNOWN_LINE = "That meeting has no prep or notes in your book, so nothing was saved from it."
MEETING_MAKE_SOLO_LINE = "That meeting has nobody else on it, so nothing was saved from it."
MEETING_MAKE_PRIVATE_LINE = "Someone on that meeting is off limits or on your personal side, so nothing was saved from it."
MEETING_MAKE_PERSONAL_LINE = "That meeting is on your personal calendar, so nothing was saved from it."
#: fix check of round 1 of #1717: a reminder's own link names an item on the list, or nothing is saved
REMINDER_BAD_REF_LINE = "That reminder's link is not an item on your list, so nothing was saved."
#: round-2 fix check: the last Inbox run's feed is there but cannot be read
MAIL_MAKE_FEED_LINE = "The last Inbox read could not be checked, so nothing was saved from this message. Refresh the Inbox and press it again."
#: B3: the same make pressed twice.
MAKE_ALREADY_LINE = "Already on your list from this; nothing new was added."
#: cr1#1658 change 5: the most rows the Later list sends, soonest back first.
LATER_CAP = 40
#: cr1#1383 part 2: add, rename and undo of a work stream, a person and a
#: priority from the Hub, each through its owning writer (`hub_edit`).
EDIT_KINDS = ("stream_edit", "person_edit", "priority_edit", "commitment_edit", "meeting_edit")
#: cr1#1632 slice 1: a meeting's live prep page, saved or taken back.
MEETING_VERBS = frozenset({"save", "undo", "wrap", "unwrap",   # slice 3: wrap, unwrap
                           "skipdraft", "unskipdraft"})  # cr1#1745: "Don't draft for meetings like this" and its Undo
EDIT_VERBS = frozenset({"add", "rename", "set", "undo"})  # set: cr1#1433
#: cr1#1383 slice 4: a commitment's wording is edited (`rename`) or that edit
#: taken back; a commitment is never added here. cr1#1383 Add context: a
#: note on it (`context`, through the commitment-note writer with a
#: reverser), taken back by `undo`. An older page's `commitment` `context`
#: row keeps its own path in `apply_commitment_choices`.
COMMIT_EDIT_VERBS = frozenset({"rename", "undo", "context"})
#: cr1#1383, Add context: a free note on a thread (`stream_edit`) or a person
#: (`person_edit`), taken back by `undo`; never on a work stream or a
#: priority. A commitment's note is `commitment_edit` `context`, in
#: COMMIT_EDIT_VERBS above.
CONTEXT_EDIT_KINDS = ("stream_edit", "person_edit")
CONTEXT_EDIT_VERBS = EDIT_VERBS | {"context"}
#: cr1#1540: an update the owner posts on a thread (`update`, on `stream_edit`
#: only), the order he keeps his priorities in and a thread starred into one
#: (`order` and `star`, on `priority_edit` only). `hub_edit` writes each.
STREAM_EDIT_VERBS = CONTEXT_EDIT_VERBS | {"update"}
PRIORITY_EDIT_VERBS = EDIT_VERBS | {"order", "star"}
#: cr1#1632, Attached: a link or a file on a meeting (`attach`) and its
#: Remove (`undo`), written by `hub_edit` (the press's own row is the record).
MEETING_EDIT_VERBS = frozenset({"attach", "undo"})


def _edit_verbs(kind: str) -> frozenset:
    """The verbs one edit kind takes, the one answer `plan_apply` and
    `apply_hub_choices` both read."""
    if kind == "commitment_edit":
        return COMMIT_EDIT_VERBS
    if kind == "stream_edit":
        return STREAM_EDIT_VERBS
    if kind == "priority_edit":
        return PRIORITY_EDIT_VERBS
    if kind == "meeting_edit":
        return MEETING_EDIT_VERBS
    return CONTEXT_EDIT_VERBS if kind in CONTEXT_EDIT_KINDS else EDIT_VERBS
TODO_SOURCE = "hub_todo"
TODO_REF_SCHEME = "user:hub_todo"
PRIORITY_NAMES = {1: "High", 2: "Medium", 3: "Low"}
MINE_CAP = 60
#: cr1#1632: the most attachments `feed/mine` carries, newest first, and the
#: most characters they take (review: well under `REFRESH_DOC_LIMIT`).
ATTACHED_CAP = 200
ATTACHED_BUDGET = 60_000
REMINDER_NEEDS_TEXT_LINE = "A reminder needs a few words."
#: cr1#1221: a Delete press, applied. The undo is the owner's own word.
REMINDER_DELETED_LINE = "Deleted. Say undo in a chat to bring it back."
REMINDER_NEEDS_DATE_LINE = "A reminder needs a day: a date, a day like Thursday, or a number of days from today."
TODO_NEEDS_TEXT_LINE = "A to-do needs a few words."
#: "Missed something?" (cr1#1017, train 8 behaviour 6): the header entry on
#: every tab. The owner says in a sentence what the Hub failed to show; the
#: apply run looks for it in the book, answers what it found and why it was
#: missed, files it, and logs the miss (`MISS_EVENT`) so the week's measure
#: counts it and the miss is kept as a lesson.
MISS_VERBS = frozenset({"tell"})
MISS_EVENT = "hub_miss_reported"
MISS_REF_SCHEME = "user:hub_miss"
MISS_TEXT_CAP = 600
MISS_NEEDS_TEXT_LINE = "Say in a few words what was missed."
#: Words in a "you missed..." sentence that name the miss, not the item.
MISS_FILLER = frozenset({"missed", "miss", "missing", "forgot", "forgotten", "show", "showed", "shown",
                         "hub", "didn", "didnt", "wasn", "wasnt", "isn", "isnt", "never", "should",
                         "need", "needs", "remember", "remind", "item", "list", "thing", "also", "too"})
#: Why the Hub did not show it, by what the book holds. Each answer names
#: what was found, then why, then what was filed.
MISS_WHY = {
    "listed": "It is on your list{where}, so the Hub had it; it may have sat below what you looked at.",
    "waiting": "It was waiting for your yes under To confirm in Commitments, so it was not on your list yet.",
    "snoozed": "You snoozed it until {until}, so it was out of view.",
    "closed": "It was closed on {day}, so it left your list.",
    "heard": "It was heard on {day}{source} but held back as not certain enough to track.",
    "mentioned": "It came up on {day}{source}, but no promise was taken from it.",
    "not_found": "Nothing Command Room has read mentions it, so it never reached the Hub.",
    "reminder": "You have a reminder for it from {until}, so it was on your reminders, not your list.",
}
#: The verdicts that mean the item is live already: nothing is filed, so
#: the hit must hold every word of the owner's sentence.
MISS_LIVE = frozenset({"listed", "waiting", "snoozed", "reminder"})
#: The kinds the miss files as a new item: the book held no live row for it.
MISS_FILES = frozenset({"closed", "heard", "mentioned", "not_found"})
#: cr1#1202: the apply run also searches the business mailbox and the
#: meetings connector for each waiting miss, through the personal-source
#: wall (cr1#1179), at most this many results each, over this many days.
MISS_SEARCH_LIMIT = 5
MISS_SEARCH_DAYS = 90
#: Why the Hub missed an item found only live, by where it was found. Each
#: files the item but `inbox` (the Hub had it, on the Inbox tab).
MISS_LIVE_WHY = {
    "filtered": "It is in your mail, but it was filtered{by}, so it never reached the Hub.",
    "closed_thread": "It is in your mail, but that thread was closed: {by}.",
    "never_fetched": "It is in your mail, but the inbox read never fetched it, so it never reached the Hub.",
    "inbox": "It is on your Inbox tab, so the Hub had it; it may have sat below what you looked at.",
    "meeting_unprocessed": "It came up in your meeting \"{title}\" on {day}, which Command Room has not processed yet.",
    "meeting_no_promise": "It came up in your meeting \"{title}\" on {day}, but no promise was taken from it.",
}
MISS_LIVE_FILES = frozenset({"filtered", "closed_thread", "never_fetched", "meeting_unprocessed",
                             "meeting_no_promise"})
#: Said with a "not found" answer whenever the wall names a personal source:
#: those are never searched, whatever the owner asked about.
MISS_PERSONAL_LINE = "Your personal sources are never searched, so anything only there was not looked at."
MISS_SEARCHED_WORDS = {("mail",): "your mail", ("meetings",): "your meetings",
                       ("mail", "meetings"): "your mail and meetings"}
REMINDER_GONE_LINE = "That reminder is no longer active."
REMINDER_PAST_LINE = "That day has passed; pick today or a later day."
#: cr1#1020: a reminder made from a commitment with no day asked for.
REMINDER_UNDATED_LINE = "Reminder set, with no date. It stays on your reminders until you clear it."
REMINDER_REF_CAP = 80
SUMMARY_CAP = 240


def _priority(value) -> int:
    try:
        p = int(value)
    except (TypeError, ValueError):
        return 2
    return p if p in PRIORITY_NAMES else 2


#: A reminder with no due date, pinned longer ago than this, is the "old item"
#: the owner asked to see last (cr1#726; every reminder has a pin date).
REMINDER_OLD_DAYS = 7


def _reminder_rank(r: Dict[str, Any], today: _dt.date) -> tuple:
    """Today first, then what is overdue, newest first; then what is coming,
    by date; then old pins (cr1#726). A reminder's date is the later of its
    due date and the day it pins from: a push or a repeat moves the pin and
    leaves `due` behind, and the older one is no longer true."""
    due = str(r.get("due") or "")[:10]
    days = [d for d in (due, str(r.get("remind_from") or "")[:10]) if d]
    try:
        day = _dt.date.fromisoformat(max(days))
    except ValueError:
        return (3, 0)
    if day > today:
        return (1, day.toordinal())
    if not due and (today - day).days > REMINDER_OLD_DAYS:
        return (2, -day.toordinal())
    return (0, -day.toordinal())


def feed_mine(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The Today page's two lists: the owner's active reminders
    (`reminders.load_active_reminders`, the same fold the morning brief pins
    from, personal ones included: the page is the owner's own), what is due
    first (`_reminder_rank`), and the open
    to-dos the owner added on the page (`data.source == TODO_SOURCE`), highest
    priority first, then oldest first. Never writes; a missing book answers
    two empty lists.

    cr1#1632, Attached: `attached` is what the owner attached to a meeting
    on the Hub and did not remove, `{meeting id: [attachment, ...]}`, newest
    first, at most `ATTACHED_CAP` in all and `ATTACHED_BUDGET` characters
    (`meeting_attach.by_meeting`: a
    name naming someone off limits or personal left out; when the wall
    cannot be read, none at all and `attached` named in `errors`). It rides
    this feed because Apply refreshes it right after a Hub press lands, so a
    landed attachment shows on its meeting at once, whatever calendar the
    page is showing. `attached_cut` is True when the cap or the budget left
    a row out (the page then says no meeting has nothing attached). New and
    optional: an older page reads none of it."""
    now = _now(now_iso)
    root = Path(str(workspace_root))
    today = None
    try:
        import commitment_state as cs

        today = cs._now_calendar_day(_iso(now), workspace_path=str(root))
    except Exception:  # noqa: BLE001
        today = None
    if today is None:
        today = now.date()
    reminders_out: List[Dict[str, Any]] = []
    errors: List[str] = []
    n_reminders = 0
    try:
        from reminders import load_active_reminders

        active = sorted(load_active_reminders(root, today, surface="m_facing"),
                        key=lambda r: _reminder_rank(r, today))
        n_reminders = len(active)
        for r in active[:MINE_CAP]:
            reminders_out.append({
                "id": r.get("id"), "summary": str(r.get("summary") or "")[:240],
                "remind_from": r.get("remind_from"), "due": r.get("due"),
                "status": r.get("status"), "days_pinned": r.get("days_pinned") or 0,
                "repeat": bool(r.get("repeat")), "personal": bool(r.get("personal")),
                "undated": bool(r.get("undated")), "ref": r.get("ref"),
            })
    except Exception:  # noqa: BLE001 - no reminders read leaves the list empty, never the page
        reminders_out = []
        errors.append("reminders")
    todos: List[Dict[str, Any]] = []
    todo_evs: Dict[str, Dict[str, Any]] = {}
    try:
        from cru_match import load_open_commitments

        events_path = root / "_hq" / "data" / "events.jsonl"
        opens = load_open_commitments(events_path) if events_path.exists() else []
        for ev in opens:
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if d.get("source") != TODO_SOURCE or not d.get("id"):
                continue
            todos.append({"id": d["id"], "title": str(d.get("title") or "")[:240],
                          "priority": _priority(d.get("priority")), "due": d.get("due"),
                          "added": ev.get("ts") or ""})
            todo_evs[str(d["id"])] = ev
    except Exception:  # noqa: BLE001
        todos = []
        errors.append("todos")
    todos.sort(key=lambda t: (t["priority"], str(t["added"])))
    # review round 1 of #1700: a to-do's project by id and by name, for Put in...
    todo_lanes_known = False
    try:
        import thread_cards as tc

        places = _projects_of(root, now)
        _tf, todo_personal = _lanes(root, places)
        todo_lanes_known = _tf is not None
        cards = tc.read_cards(root)[0]
        names = {k: str((v or {}).get("name") or "") for k, v in {**(cards.get("threads") or {}), **(cards.get("work_streams") or {})}.items()}
        for t_ in todos:
            pid = places.get(str(t_["id"]))
            t_["personal_lane"] = str(t_["id"]) in todo_personal
            if pid:
                t_["project_id"] = pid
                t_["project"] = names.get(pid, "")[:120]
    except Exception:  # noqa: BLE001 - no project read: the row says none, never a guessed one
        todo_lanes_known = False
    try:
        _todo_last_actions(root, todos[:MINE_CAP], todo_evs)
    except Exception:  # noqa: BLE001 - an unread last action leaves the line off, never the list
        pass
    attached: Dict[str, List[Dict[str, Any]]] = {}
    attached_cut = False
    try:
        import meeting_attach

        got = meeting_attach.by_meeting_cut(root, cap=ATTACHED_CAP, budget=ATTACHED_BUDGET)
        if got is None:
            errors.append("attached")
        else:
            attached, attached_cut = got
    except Exception:  # noqa: BLE001 - the lists never fail for this
        errors.append("attached")
    return _with_words(workspace_root, {"generated_at": _iso(now), "today": today.isoformat(),
            "reminders": reminders_out, "todos": todos[:MINE_CAP], "lanes_known": todo_lanes_known,
            "n_todos": len(todos), "n_reminders": n_reminders, "errors": errors, "attached": attached,
            "attached_cut": attached_cut})


def _todo_threads(root: Path, evs: List[Dict[str, Any]]) -> Dict[int, str]:
    """{a to-do's own seq: the thread a later correction tied it to}. A to-do
    is made with no thread; the Hub's Assign, or "which project does this
    belong to", ties it to one with a `reclassification` row naming its seq,
    read as `thread_activity.apply_reclassifications` reads it (the newest
    wins) over the org-scoped log above the confidence floor, as the thread
    cards read it. The open fold does not apply those, so they are read
    here, once, and only when there is a to-do. A correction back to no
    thread answers ""."""
    seqs = {e.get("seq") for e in evs
            if isinstance(e, dict) and isinstance(e.get("seq"), int) and not isinstance(e.get("seq"), bool)}
    if not seqs or not (root / "_hq" / "data" / "events.jsonl").exists():
        return {}
    import thread_cards as tc
    from events_io import load_events_org_scoped

    best: Dict[int, Tuple[int, str]] = {}
    for e in load_events_org_scoped(root)[0]:
        if not isinstance(e, dict) or e.get("type") != "reclassification" or not tc._above_floor(e):
            continue
        sup = e.get("supersedes_seq")
        if sup is None:
            d = e.get("data") if isinstance(e.get("data"), dict) else {}
            sup = d.get("supersedes_seq")
        if not isinstance(sup, int) or isinstance(sup, bool) or sup not in seqs:
            continue
        rseq = e.get("seq") if isinstance(e.get("seq"), int) else -1
        if sup not in best or rseq >= best[sup][0]:
            best[sup] = (rseq, str(e.get("primary_thread_id") or ""))
    return {k: v[1] for k, v in best.items()}


def _todo_thread_move(cards: Dict[str, Any], tid: str) -> Optional[Dict[str, Any]]:
    """The newest move on the thread a to-do is tied to, as a thread card's
    `last_moved` plus `thread_name`. A work stream (the Hub's Assign only
    takes one) is the newest across its own card and its threads', named
    for the thread that moved."""
    threads = cards.get("threads") or {}
    ids = [tid] + list(((cards.get("work_streams") or {}).get(tid) or {}).get("built_from") or [])
    best = None
    for i in ids:
        card = threads.get(i)
        mv = card.get("last_moved") if isinstance(card, dict) else None
        if isinstance(mv, dict) and mv.get("day") and (best is None or str(mv["day"]) > best["day"]):
            best = {"day": str(mv["day"]), "type": mv.get("type"), "source": mv.get("source"),
                    "thread_name": card.get("name")}
    return best


def _todo_last_actions(root: Path, todos: List[Dict[str, Any]], evs: Dict[str, Dict[str, Any]]) -> None:
    """cr1#1459 slice 2: each to-do on Today gets `last_action`, the same
    shape as a priority's (`_priority_last_action`): the newest of a note the
    owner added to it (`context_notes`, cr1#1463) and, when it was tied to a
    thread or work stream later, that thread's last move by the day-count
    rule (`thread_cards`), never one from before the to-do was added. On a
    tie the note wins. With neither: `{at: None, added}`, the day it was
    added. Names only, never an id. Writes nothing; a to-do whose line
    cannot be read keeps its row and has no line."""
    import open_list as ol
    import thread_cards as tc

    tz = ol._workspace_tz(root)
    cards: Optional[Dict[str, Any]] = None
    try:
        rebound = _todo_threads(root, [evs.get(str(t.get("id"))) or {} for t in todos])
    except Exception:  # noqa: BLE001 - an unread correction leaves the to-do's own thread, never the notes
        rebound = {}
    for t in todos:
        try:
            ev = evs.get(str(t.get("id"))) or {}
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            added = tc._local_day({"ts": t.get("added")}, tz) or ""
            best: Optional[Dict[str, Any]] = None
            for n in d.get("context_notes") or []:
                day = tc._local_day({"ts": n.get("ts")}, tz) if isinstance(n, dict) else ""
                if day and (best is None or day >= best["at"]):
                    best = {"what": "note added", "at": day}
            tid = (rebound[ev["seq"]] if ev.get("seq") in rebound
                   else str(ev.get("primary_thread_id") or d.get("primary_thread_id") or ""))
            if tid:
                if cards is None:
                    cards = tc.read_cards(root)[0]
                mv = _todo_thread_move(cards, tid)
                if mv and mv["day"] >= added and (best is None or mv["day"] > best["at"]):
                    best = _priority_last_action({"last_moved": mv})
            t["last_action"] = best or {"at": None, "added": added or None}
        except Exception:  # noqa: BLE001 - one unread line never takes the list down
            t.pop("last_action", None)


PRIORITY_LEVELS = {"high": 1, "medium": 2, "low": 3}
CHAT_PRIORITY_LINE = "Added to your priorities as {level}. It shows on the Hub after the next refresh."
CHAT_PRIORITY_ALREADY_LINE = "That is already on your priorities as {level}."


def _same_words(text) -> str:
    """The words of a title for a same-or-not compare, in any script: case and
    punctuation fold away, letters and digits stay. Two titles with nothing
    to compare answer "", which is never a match."""
    return " ".join("".join(c if c.isalnum() else " " for c in str(text or "").casefold()).split())


def add_chat_priority(workspace_root, *, title: str = "", priority=None,
                      resolved_by: str = "") -> Dict[str, Any]:
    """A WRITER. A priority said in chat (cr1#776): the page's own record, through
    the page's own writer, so chat and the Hub read one thing. `priority` is
    `high`, `medium`, `low` or 1 to 3; none is Medium, as the page does. The
    same words already open on the list answer from that row, so saying it
    twice makes one row; once closed, the same words are a new one. Answers
    `{ok, status: created | already, commitment_id, priority, line}`. The Hub
    shows it after its next refresh."""
    import commitment_state as cs
    from cru_match import load_open_commitments

    text = str(title or "").strip()[:240]
    if not text:
        return {"ok": False, "reason": "no_title", "line": TODO_NEEDS_TEXT_LINE}
    actor = str(resolved_by or "").strip() or _owner_id(workspace_root)
    if not actor:
        return {"ok": False, "reason": "no_actor", "line": NO_ACTOR_LINE}
    level = PRIORITY_LEVELS.get(str(priority or "").strip().lower()) or _priority(priority)
    root = Path(str(workspace_root))
    events_path = root / "_hq" / "data" / "events.jsonl"
    want = _same_words(text)
    for ev in (load_open_commitments(events_path) if events_path.exists() else []):
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if want and d.get("source") == TODO_SOURCE and _same_words(d.get("title")) == want:
            held = _priority(d.get("priority"))
            return {"ok": True, "status": "already", "commitment_id": d.get("id"), "priority": held,
                    "line": CHAT_PRIORITY_ALREADY_LINE.format(level=PRIORITY_NAMES[held].lower())}
    import uuid

    ref = f"{TODO_REF_SCHEME}:chat-{uuid.uuid4().hex}"
    out = cs.create_personal_task(str(root), title=text, owner_id=actor, source_ref=ref,
                                  source_skill=COMMITMENT_SRC, priority=level, source=TODO_SOURCE)
    cid = ((out or {}).get("commitment") or {}).get("data", {}).get("id") or _todo_id(events_path, ref)
    return {"ok": True, "status": "created", "commitment_id": cid, "priority": level,
            "line": CHAT_PRIORITY_LINE.format(level=PRIORITY_NAMES[level].lower())}


def _hub_day(value, now, root) -> Optional[str]:
    """The day a reminder is for: an ISO date as the page sends it, "today"
    or 0 for today, else `parse_later_when` (days from today, or a date)."""
    s = str(value or "").strip().lower()
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    import commitment_state as cs

    if s in ("0", "today"):
        day = cs._now_calendar_day(_iso(now), workspace_path=str(root))
        return (day or now.date()).isoformat()
    return cs.parse_later_when(s, _iso(now), workspace_path=str(root))


def _hub_day_checked(value, now, root):
    """`(day, line)`: the day as a real ISO date not before today where the
    owner is, else `(None, the line that says why)` (cr1#679: 1999 and Feb 30
    were taken as they came)."""
    try:
        day = _hub_day(value, now, root)
    except Exception:  # noqa: BLE001 - a day the parser cannot read is no day
        day = None
    if not day:
        return None, REMINDER_NEEDS_DATE_LINE
    try:
        d = _dt.date.fromisoformat(str(day)[:10])
    except ValueError:
        return None, REMINDER_NEEDS_DATE_LINE
    try:
        import commitment_state as cs

        today = cs._now_calendar_day(_iso(now), workspace_path=str(root)) or now.date()
    except Exception:  # noqa: BLE001
        today = now.date()
    if d < today:
        return None, REMINDER_PAST_LINE
    return d.isoformat(), ""


def _reminders_kept(root) -> set:
    """READ. The ids of every reminder the owner made that is not deleted now,
    cleared ones included (cr1#1636 item 6): what the Hub's Undo on a cleared
    reminder may put back. Reminders are the owner's own, so the personal
    lane is read too."""
    import reminders as rm
    from events_io import load_events_org_scoped

    events = load_events_org_scoped(str(root), drop_personal=False)[0]
    made = {str((ev.get("data") or {}).get("id") or "") for ev in events
            if isinstance(ev, dict) and ev.get("type") == "reminder" and isinstance(ev.get("data"), dict)
            and ev["data"].get("origin") == rm.REMINDER_ORIGIN}
    return {rid for rid in made if rid and not rm.is_deleted(events, rid)}


def _hub_writes_in_book(root) -> Dict[str, Dict[str, Any]]:
    """Every reminder event a Hub press already wrote, by the press's id
    (cr1#675): a press applied twice answers from the first write."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from events_io import load_events_org_scoped

        # The reminder lane is the owner's own, and the org scope drops it; the
        # Hub is owner-read only (the personal-reminder allowance, cr1#662).
        events = load_events_org_scoped(str(root), drop_personal=False)[0]
    except Exception:  # noqa: BLE001 - no log means nothing was written yet
        return out
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") in ("reminder", "reminder_updated", "reminder_cleared",
                                                        "reminder_deleted"):
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if d.get("hub_choice"):
                out[str(d["hub_choice"])] = ev
    return out


def _replayed(ev: Dict[str, Any], verb: str) -> Dict[str, Any]:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    when = str(d.get("remind_from") or "")[:10]
    if verb == "add":
        return {"outcome": "applied", "line": f"Reminder set for {when}.", "reminder_id": d.get("id"),
                "replayed": True}
    if verb == "push":
        return {"outcome": "applied", "line": f"Moved to {when}.", "replayed": True}
    if verb == "delete":
        return {"outcome": "applied", "line": REMINDER_DELETED_LINE, "replayed": True}
    return {"outcome": "applied", "line": "Cleared.", "replayed": True}


def _live_project(root, project_id, any_lane: bool = False) -> Optional[str]:
    """cr1#1658 change 6: a project a to-do added from its drawer goes on,
    when it is a live project on the thread cards; None otherwise, so the
    to-do is still added, with no project."""
    pid = str(project_id or "").strip()
    if not pid:
        return None
    try:
        import thread_cards as tc

        cards = tc.read_cards(Path(str(root)))[0]
    except Exception:  # noqa: BLE001 - no cards: no project, never a guessed one
        return None
    if pid not in {**(cards.get("threads") or {}), **(cards.get("work_streams") or {})}:
        return None
    if any_lane:
        return pid
    # review round 1 of #1700: a Hub to-do is on the work side, so a personal project is never stamped on it
    try:
        from personal_leak import business_thread_ids

        return pid if pid in business_thread_ids(root) else None
    except Exception:  # noqa: BLE001
        return None


def _meeting_pointer(meeting_id) -> Optional[str]:
    """cr1#1658 change 8: the pointer a To-do or Remind me made from a
    meeting keeps, `meeting:<id>`; None without one."""
    import commitment_state as cs

    mid = str(meeting_id or "").strip()
    return cs.about_pointer(f"meeting:{mid}") if mid else None


def _person_on_file(root, person_id) -> Optional[str]:
    """cr1#1658 change 8: `person_id` when it is a person on file and not
    the owner; None otherwise, never a guess."""
    pid = str(person_id or "").strip()
    if not pid or pid == str(_owner_id(root) or ""):
        return None
    try:
        import commitments_helpers as cmh

        return pid if pid in cmh._wo_people(str(root)) else None
    except Exception:  # noqa: BLE001 - no people on file: nobody
        return None


def _made_from(root, inp) -> Optional[str]:
    """cr1#1658: what a Hub to-do was made from, as its `about_ref`: a
    message's thread, a person on file (`person:<id>`), or a meeting."""
    import commitment_state as cs

    who = _person_on_file(root, inp.get("person"))
    return (_mail_pointer(inp.get("thread_id")) or (cs.about_pointer(f"person:{who}") if who else None)
            or _meeting_pointer(inp.get("meeting")))


def _mr_address(value) -> str:
    """A bare lower-case address (`mail_rules.address_of`), "" for none."""
    try:
        import mail_rules

        return str(mail_rules.address_of(value or "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _mail_pointer(thread_id) -> Optional[str]:
    """cr1#1658: the pointer a To-do, Remind me or Waiting on them made from
    an Inbox message keeps to its thread, `mail:thread:<id>`; None without one."""
    import commitment_state as cs

    tid = str(thread_id or "").strip()
    return cs.about_pointer(f"mail:thread:{tid}") if tid else None


def _made_gate(root, inp) -> Optional[str]:
    """Review round 1 of #1717: the line that refuses a To-do or Remind me
    made from a person's card or a meeting, or None to go on (None too for
    a press from neither). Read before anything is written, failing closed:
    a fence or a people list it cannot read refuses. A person is refused
    when off limits (`fence["off"]`, or `account_scope_gate.sender_off_limits`
    by person), and when on the personal side (a personal tie) and a project
    is given. A meeting is resolved from the book, never the page: its prep
    receipt (with its people) or its notes; refused when the book has
    neither, when nobody else is on it, or when anyone on it is off limits
    or has a personal tie."""
    person = str(inp.get("person") or "").strip()
    meeting = str(inp.get("meeting") or "").strip()
    if not person and not meeting:
        return None
    try:
        import hub_meeting
        import hub_work
        from account_scope_gate import sender_off_limits

        fence = hub_work.fence(root)
        ties = hub_meeting._tie_ids(Path(str(root)))
    except Exception:  # noqa: BLE001 - no fence read: refuse, never guess
        return MAIL_MAKE_FENCE_LINE
    if fence is None or ties is None:
        return MAIL_MAKE_FENCE_LINE
    off = set(fence.get("off") or set())
    if person:
        if person in off or sender_off_limits(root, sender_person_id=person):
            return PERSON_MAKE_OFF_LIMITS_LINE
        if person in ties and str(inp.get("project") or "").strip():
            return PERSON_MAKE_TIE_LINE
    if meeting and str(inp.get("calendar") or "").strip().lower() == "personal":
        return MEETING_MAKE_PERSONAL_LINE
    if meeting:
        people = _meeting_people(root, meeting)
        if people is None:
            return MEETING_MAKE_UNKNOWN_LINE
        people.discard(str(_owner_id(root) or ""))
        if not people:
            return MEETING_MAKE_SOLO_LINE
        if any(p in off or p in ties or sender_off_limits(root, sender_person_id=p) for p in people):
            return MEETING_MAKE_PRIVATE_LINE
    return None


def _open_item(root, cid) -> bool:
    """Fix check of round 1 of #1717: whether `cid` is an open item on the
    list (the only raw ref a reminder from a row carries)."""
    want = str(cid or "").strip()
    if not want:
        return False
    try:
        from cru_match import _commitment_id, load_open_commitments

        return any(str(_commitment_id(e)) == want for e in load_open_commitments(Path(str(root)) / "_hq" / "data" / "events.jsonl"))
    except Exception:  # noqa: BLE001 - no list read: no item, so the press is refused
        return False


def _meeting_people(root, meeting_id: str) -> Optional[set]:
    """Review round 1 of #1717: the people on a meeting by the book's own
    record, never the page's word: its prep receipts' `attendee_person_ids`,
    and the people on its notes (`meeting` rows a `meeting_processed`
    receipt for this id points at). None when the book has neither."""
    from connector_adapters.provenance import dedup_key_of
    from events_io import load_events_owner_scoped

    evs = load_events_owner_scoped(str(root))[0]
    found, people, refs = False, set(), set()
    for e in evs:
        d = e.get("data") if isinstance(e, dict) and isinstance(e.get("data"), dict) else {}
        if e.get("type") == "prep_brief" and str(d.get("meeting_id") or "") == meeting_id:
            found = True
            people |= {str(p) for p in d.get("attendee_person_ids") or [] if str(p).strip()}
        elif e.get("type") == "meeting_processed" and str(d.get("meeting_id") or "") == meeting_id:
            found = True
            # G30: stored refs are compared by their dedup key, never by raw spelling
            k = dedup_key_of(d.get("source_ref")) if d.get("source_ref") else None
            if k:
                refs.add(k)
    if refs:
        keys = refs
        for e in evs:
            d = e.get("data") if isinstance(e, dict) and isinstance(e.get("data"), dict) else {}
            if e.get("type") == "meeting" and d.get("source_ref") and dedup_key_of(d.get("source_ref")) in keys:
                people |= {str(p) for p in e.get("person_ids") or [] if str(p).strip()}
    return people if found else None


#: cr1#1701 (A): the senders of the threads this Update's waiting mail makes
#: name, from the page's own feed/inbox (`update-start` reads them in code and
#: answers them with its check; `update_claim` takes them only when the check
#: holds). Set only for the
#: Update's save, by `update_save`; None everywhere else.
_PAGE_INBOX_SENDERS: "contextvars.ContextVar[Optional[Dict[str, str]]]" = contextvars.ContextVar(
    "cockpit_page_inbox_senders", default=None)
#: A map longer than this is not one update-start writes: dropped whole.
PAGE_INBOX_SENDERS_CAP = 200


def page_inbox_senders_ok(value: Any) -> Dict[str, str]:
    """cr1#1701 (A): the map `update-start` answered, as the run passed it on,
    checked for shape: `{thread_id: bare lower-case address}`, at most
    `PAGE_INBOX_SENDERS_CAP` entries. Anything else (not a dict, too long, a
    key that is not a short text, a value that is not a bare address) is
    dropped WHOLE and answers {}: the save then checks as it did before, and
    the off-limits fence runs either way. Pure."""
    if not isinstance(value, dict) or len(value) > PAGE_INBOX_SENDERS_CAP:
        return {}
    out: Dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not k.strip() or len(k) > 300 or not isinstance(v, str):
            return {}
        addr = _mr_address(v)
        if "@" not in addr or addr != v:
            return {}
        out[k.strip()] = addr
    return out


def _page_inbox_senders(reads_dir: str) -> Dict[str, str]:
    """cr1#1701 (A), page side: the sender of each thread a waiting mail make
    names (`<reads>/waiting/choices`, the `applied == false` query saved as
    files), as the page's own `feed/inbox` (`<reads>/feed`) lists it. A thread
    not in that feed, or with no address there, is left out. Reads no
    workspace."""
    try:
        feed = next((f for d, f, _v in _saved_docs(reads_dir, "feed") if d == "inbox"), None)
        items = feed.get("items") if isinstance(feed, dict) else None
        if not isinstance(items, list):
            return {}
        tids = set()
        for _d, f, _v in _saved_docs(str(Path(reads_dir) / "waiting"), "choices"):
            inp = f.get("input") if isinstance(f.get("input"), dict) else {}
            tid = str(inp.get("thread_id") or "").strip()
            if tid and f.get("applied") is not True:
                tids.add(tid)
        out: Dict[str, str] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            tid = str(it.get("thread_id") or "").strip()
            addr = _mr_address(it.get("sender"))
            if tid in tids and tid not in out and "@" in addr:
                out[tid] = addr
            if len(out) >= PAGE_INBOX_SENDERS_CAP:
                break
        return out
    except Exception:  # noqa: BLE001 - none read: the save checks as before
        return {}


def _mail_make_gate(root, inp) -> Optional[str]:
    """Review round 1 of #1699 (B1, B5): the line that refuses a To-do,
    Remind me or Waiting on them made from a message, or None to go on. A
    press with no thread is no mail make (None). It is refused when its
    thread cannot be kept as a pointer, when it carries no sender, when the
    fence cannot be read (fail closed, `hub_work.fence`), and when the
    sender is off limits by address or by the person their address is
    (`account_scope_gate.sender_off_limits`, which reads a per-sender
    override that turns writing off too). Read only."""
    if not str(inp.get("thread_id") or "").strip():
        return None
    if _mail_pointer(inp.get("thread_id")) is None:
        return MAIL_MAKE_BAD_LINE
    # fix check of round 1: the address read once, in its bare form ("Name <a@b>" is a@b)
    addr = _mr_address(inp.get("addr"))
    if "@" not in addr:
        return MAIL_MAKE_NO_SENDER_LINE
    # ...and, when the last Inbox run's feed is at hand, it must be the thread's own sender
    known = _inbox_thread_sender(root, inp.get("thread_id"))
    if known is None:
        return MAIL_MAKE_FEED_LINE
    if known and known != addr:
        return MAIL_MAKE_SENDER_LINE
    # cr1#1701 (A): the page's own feed/inbox, as update-start read it for this
    # run. A thread there under another sender is refused; a thread not there
    # (a live-mail row, #1772) passes as before, and the fence below still runs.
    page_known = (_PAGE_INBOX_SENDERS.get() or {}).get(str(inp.get("thread_id") or "").strip())
    if page_known and page_known != addr:
        return MAIL_MAKE_SENDER_LINE
    try:
        import hub_work
        from account_scope_gate import sender_off_limits

        fence = hub_work.fence(root)
    except Exception:  # noqa: BLE001 - no fence read: refuse, never guess
        return MAIL_MAKE_FENCE_LINE
    if fence is None:
        return MAIL_MAKE_FENCE_LINE
    # fix check of round 1: every person on that address is checked, one or two
    ids = _people_for_address(root, addr)
    off = fence.get("off") or set()
    if sender_off_limits(root, sender_email=addr) or any(i in off or sender_off_limits(root, sender_person_id=i) for i in ids):
        return MAIL_MAKE_OFF_LIMITS_LINE
    return None


def _inbox_thread_sender(root, thread_id) -> Optional[str]:
    """Fix check of round 1 (a stopgap until the Inbox run keeps a thread
    record in the workspace, cr1#1701): the bare sender of `thread_id` in
    the last Inbox run's feed, when that feed is in its scratch folder.
    "" when no feed is there, or it does not hold the thread: Apply then
    checks the press's own sender only. None when something is there but
    cannot be read as a feed (round-2 fix check: refused, never read as no
    feed)."""
    tid = str(thread_id or "").strip()
    if not tid:
        return ""
    try:
        f = _refresh_file(_task_scratch(root, "inbox"), "feed/inbox")
        if not f.exists():
            return ""
        feed = json.loads(f.read_text(encoding="utf-8"))
        items = feed.get("items") if isinstance(feed, dict) else None
        if not isinstance(items, list):
            return None
        for it in items:
            if isinstance(it, dict) and str(it.get("thread_id") or "") == tid:
                # #1699's round-3 note N1: a row with a blank sender, or a name with no address, is no answer
                return _mr_address(it.get("sender")) or None
    except Exception:  # noqa: BLE001 - there but unreadable (a folder, bad JSON): no answer
        return None
    return ""


def _reminder_made(root, now, ref: Optional[str], summary: str, person: Optional[str] = None) -> bool:
    """B3: a reminder on the list already made from `ref` with these words;
    with `person` (review round 1 of #1717), one about that person with
    these words (the list does not carry its people, so its own rows do)."""
    import commitment_state as cs
    import reminders as rm

    want = " ".join(str(summary or "").split()).lower()
    today = cs._now_calendar_day(_iso(now), workspace_path=str(root)) or now.date()
    active = rm.load_active_reminders(Path(str(root)), today, surface="m_facing")
    if person:
        from events_io import load_events_owner_scoped

        ids = {str(e.get("data", {}).get("id")) for e in load_events_owner_scoped(str(root))[0]
               if isinstance(e, dict) and e.get("type") == "reminder" and person in (e.get("person_ids") or [])
               and " ".join(str((e.get("data") or {}).get("summary") or "").split()).lower() == want}
        return any(str(a.get("id")) in ids for a in active)
    for a in active:
        if str(a.get("ref") or "") == ref and " ".join(str(a.get("summary") or "").split()).lower() == want:
            return True
    return False


def _open_made(root, about: str, title: str) -> Optional[str]:
    """B3: the id of an open item already made from `about` with these words."""
    if not about:
        return None
    from cru_match import load_open_commitments

    want = " ".join(str(title or "").split()).lower()
    for ev in load_open_commitments(Path(str(root)) / "_hq" / "data" / "events.jsonl"):
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if d.get("about_ref") == about and " ".join(str(d.get("title") or "").split()).lower() == want:
            return str(d.get("id") or "")
    return None


def _people_for_address(root, addr) -> set:
    """Fix check of round 1: every person on file whose address (or one of
    their addresses) is `addr` in its bare form."""
    a = _mr_address(addr)
    if "@" not in a:
        return set()
    from entities_io import entities_collection

    try:
        data = json.loads((Path(str(root)) / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - no people on file: nobody
        return set()
    out = set()
    for p in entities_collection(data, "people"):
        if isinstance(p, dict) and p.get("id") and any(_mr_address(m) == a for m in [p.get("email")] + list(p.get("emails") or [])):
            out.add(str(p["id"]))
    return out


def _person_for_address(root, addr) -> tuple:
    """`(person id, why)`: the one person on file whose saved address (or
    one of their addresses) is `addr`; else `(None, "none")`, or
    `(None, "shared")` when two people share it (B4). Never a guess by name."""
    hits = _people_for_address(root, addr)
    if len(hits) == 1:
        return next(iter(hits)), ""
    return None, "shared" if hits else "none"


def _person_by_address(root, addr) -> Optional[str]:
    """cr1#1658: the one person on file whose saved address is `addr`, or
    None (no one, or more than one). Never a guess by name."""
    a = str(addr or "").strip().lower()
    if "@" not in a:
        return None
    from entities_io import entities_collection

    try:
        data = json.loads((Path(str(root)) / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - no people on file: nobody
        return None
    hits = []
    for p in entities_collection(data, "people"):
        if not isinstance(p, dict) or not p.get("id"):
            continue
        mails = [p.get("email")] + list(p.get("emails") or [])
        if any(str(m or "").strip().lower() == a for m in mails):
            hits.append(str(p["id"]))
    return hits[0] if len(set(hits)) == 1 else None


def _hub_waiting_add(root, inp, actor) -> Dict[str, Any]:
    """cr1#1658 change 4: Waiting on them, pressed on an Inbox message: the
    sender owes the owner what the words say. The person is the one on file
    with the sender's address, never a guess; the write is the Waiting On
    add (`commitments_helpers._wo_add_item`, then
    `commitment_state.create_owed_to_me_item`), pointing at the thread. The
    same person already owing the same thing is said and writes nothing,
    which is also what a retried run gets."""
    import commitments_helpers as cmh

    title = " ".join(str(inp.get("title") or "").split())[:240]
    if not title:
        return {"outcome": "error", "line": WAITING_NEEDS_TEXT_LINE}
    refused = _mail_make_gate(root, inp) if str(inp.get("thread_id") or "").strip() else MAIL_MAKE_BAD_LINE
    if refused:
        return {"outcome": "refused", "line": refused}
    pid, why = _person_for_address(root, inp.get("addr"))
    if not pid:
        return {"outcome": "error", "line": WAITING_SHARED_LINE if why == "shared" else WAITING_NO_PERSON_LINE}
    out = cmh._wo_add_item(str(root), actor=actor, title=title, owner_name=None, owner_id=pid,
                           n=None, commitment_id=None, about_ref=_mail_pointer(inp.get("thread_id")))
    st = out.get("status")
    line = str(out.get("ack") or WAITING_NO_PERSON_LINE)
    if st == "created":
        return {"outcome": "applied", "line": line, "commitment_id": out.get("commitment_id")}
    if st == "already_listed":
        return {"outcome": "already", "line": line, "commitment_id": out.get("commitment_id")}
    return {"outcome": "error", "line": line}


def _later_rows(workspace_root, opens, owner, now) -> tuple:
    """cr1#1658 change 5: what is hidden until a day, soonest back first,
    `(rows, n)`: each open item a live hide keeps off the lists
    (`mute_ledger.item_snoozes`: the owner's own Later, which moves the
    date and hides it until then; a Later on what someone owes the owner;
    a hold), with the day it comes back and whose it is. Read only."""
    from cru_match import _commitment_id
    from events_io import load_events_owner_scoped
    from mute_ledger import check_back_day, item_snoozes

    events = load_events_owner_scoped(str(workspace_root))[0]
    ends = item_snoozes(events, _iso(now))
    try:
        tzinfo = _workspace_tz(workspace_root)
    except Exception:  # noqa: BLE001 - no zone: the stamp's own UTC day
        tzinfo = None
    names = _person_names(workspace_root)
    rows = []
    for ev in opens:
        cid = str(_commitment_id(ev) or "")
        end = ends.get(cid)
        if not end:
            continue
        day = check_back_day(end, tzinfo)
        if day is None:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        mine = str(d.get("owner_id") or "") == str(owner or "")
        other = str(d.get("counterparty_id") or "") if mine else str(d.get("owner_id") or "")
        rows.append({"id": cid, "title": " ".join(str(d.get("title") or "").split())[:200], "back": day.isoformat(),
                     "mine": mine, "with": str(names.get(other) or "")[:120]})
    rows.sort(key=lambda r: (r["back"], r["title"]))
    return rows[:LATER_CAP], len(rows)


def _todo_id(events_path, ref: str) -> Optional[str]:
    """The id the book gave a to-do just written, found by its own pointer."""
    try:
        from cru_match import load_open_commitments
        from connector_adapters.provenance import dedup_key_of

        want = dedup_key_of(ref)
        if want is None:
            return None
        for ev in reversed(load_open_commitments(events_path)):
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if dedup_key_of(d.get("source_ref")) == want:
                return d.get("id")
    except Exception:  # noqa: BLE001
        return None
    return None


def _miss_terms(text: str) -> List[str]:
    from log_search import _tokens
    return [t for t in _tokens(text) if t not in MISS_FILLER]


def _miss_find(root: Path, text: str, now: _dt.datetime, *, skip_ref: str = "") -> Dict[str, Any]:
    """What the book holds for a missed item, and why the Hub did not show
    it: `{why, terms, found_seq, found_id, source, title, thread, day}`. Read
    only.

    The book is read on the owner's tier (the Hub is the owner's own page, so
    a personal reminder or a promise to a personal contact is found too) and
    matched by the item's own words with the log search's word rules
    (`log_search._tokens` over `_text_of`, ruling A7), each entry scored on
    ALL its words, never a snippet. A hit counts only when it holds at least
    half the sentence's words (two when there are two or more). The best is
    the one holding the most, then a promise before a heard line before any
    other entry, then the newest.

    A verdict that files nothing (`listed`, `waiting`, `snoozed`,
    `reminder`: the item is live already) needs EVERY word of the sentence
    in the entry; a weaker hit never stops the item being filed. A promise
    is judged by the open set (`cru_match.load_open_commitments`, so a
    reopened one is open) and the open list (snoozed, waiting for a yes,
    held back); a promise not open is `closed`, dated by its closing row
    (`closure_index`). A close row found by its words stands for its promise.
    `skip_ref` leaves out the to-do this same press filed (a retry)."""
    import open_list as ol
    import reminders as rm
    from closure_index import CLOSER_TYPES, closer_target_id
    from cru_match import load_open_commitments
    from events_io import load_events_owner_scoped
    from log_search import _searchable, _text_of, _tokens

    terms = _miss_terms(text)
    out: Dict[str, Any] = {"why": "not_found", "terms": terms}
    if not terms:
        return out
    need = max(1 if len(terms) == 1 else 2, (len(terms) + 1) // 2)
    try:
        events = load_events_owner_scoped(str(root))[0]
    except Exception:  # noqa: BLE001 - an unreadable book answers "not found", and says so
        return dict(out, unread=True)
    by_id: Dict[str, Dict[str, Any]] = {}
    closes: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "commitment" and d.get("id"):
            by_id.setdefault(str(d["id"]), ev)
        elif ev.get("type") in CLOSER_TYPES:
            tid = closer_target_id(ev)
            if tid:
                closes[str(tid)] = ev
    try:
        open_ids = {str((e.get("data") or {}).get("id")) for e in
                    load_open_commitments(root / "_hq" / "data" / "events.jsonl", events=events)}
    except Exception:  # noqa: BLE001 - an unread open set judges nothing live
        open_ids = set()
    try:
        tz = ol._workspace_tz(root)
        rows = {str(r.get("id")): r for r in (ol.read_list(root, now, tz)[0].get("rows") or [])}
    except Exception:  # noqa: BLE001
        rows = {}
    try:
        today = now.astimezone(ol._workspace_tz(root)).date()
        live_reminders = {str(r.get("id")) for r in rm.load_active_reminders(root, today, surface="m_facing")}
    except Exception:  # noqa: BLE001
        live_reminders = set()
    threads = _thread_names(root)
    from connector_adapters.provenance import dedup_key_of
    skip_key = dedup_key_of(skip_ref) if skip_ref else None

    def _verdict(ev: Dict[str, Any]) -> Dict[str, Any]:
        t = str(ev.get("type") or "")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if t in CLOSER_TYPES:
            ev = by_id.get(str(closer_target_id(ev) or "")) or ev
            t = str(ev.get("type") or "")
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if t == "commitment_observed":
            return {"why": "heard"}
        if t == "reminder":
            return {"why": "reminder" if str(d.get("id") or "") in live_reminders else "mentioned",
                    "until": str(d.get("remind_from") or "")[:10]}
        if t != "commitment" or not d.get("id"):
            return {"why": "mentioned"}
        cid = str(d["id"])
        if cid not in open_ids:
            closer = closes.get(cid)
            return {"why": "closed", "day": str((closer or {}).get("ts") or "")[:10] or None}
        row = rows.get(cid)
        if row is None:
            return {"why": "listed"}          # open, though not a row of its own (a step)
        if ol.is_snoozed(row, now):
            return {"why": "snoozed", "until": str(row.get("snooze_until") or "")[:10]}
        if row.get("observed"):
            return {"why": "heard"}
        if not row.get("confirmed"):
            return {"why": "waiting"}
        return {"why": "listed"}

    rank = {"commitment": 0, "commitment_observed": 1, "reminder": 1}
    scored = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        t = str(ev.get("type") or "")
        if t == MISS_EVENT or not (t in ("commitment", "commitment_observed", "reminder")
                                   or t in CLOSER_TYPES or _searchable(t)):
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if skip_key is not None and dedup_key_of(d.get("source_ref")) == skip_key:
            continue
        if t in CLOSER_TYPES:
            target = by_id.get(str(closer_target_id(ev) or ""))
            words = set(_tokens(_text_of(ev) + "\n" + (_text_of(target) if target else "")))
        else:
            words = set(_tokens(_text_of(ev)))
        n = sum(1 for term in terms if term in words)
        if n < need:
            continue
        seq = ev.get("seq") if isinstance(ev.get("seq"), int) else -1
        scored.append((-n, rank.get(t if t not in CLOSER_TYPES else "commitment", 2), -seq, n, ev))
    scored.sort(key=lambda s: s[:3])
    for _neg, _rk, _s, n, ev in scored:
        v = _verdict(ev)
        if v["why"] in MISS_LIVE and n < len(terms):
            continue          # a live verdict needs every word; a weaker hit never stops the filing
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        th = str(ev.get("primary_thread_id") or d.get("primary_thread_id") or "")
        out.update(v)
        out.update(found_seq=ev.get("seq"), found_id=str(d.get("id") or "") or None, found_n=n,
                   title=" ".join(str(d.get("title") or d.get("summary") or d.get("subject") or "").split())[:160],
                   source=str(d.get("source_ref") or ev.get("source_ref") or ""),
                   thread=threads.get(th) or None)
        if not out.get("day"):
            out["day"] = str(ev.get("ts") or "")[:10] if v["why"] != "closed" else None
        return out
    return out


def _miss_line(found: Dict[str, Any], filed: bool, *, tail: Optional[str] = None) -> str:
    why = found["why"]
    src = found.get("source") or ""
    words = {"where": (" under " + found["thread"]) if found.get("thread") else "",
             "until": found.get("until") or "later", "day": found.get("day") or "an earlier day",
             "source": (" in " + src) if src else "", "by": found.get("by") or "",
             "title": str(found.get("title") or "").strip()[:160]}
    head = ("Found it: " + str(found.get("title") or "").strip()[:160] + ". ") if why != "not_found" else ""
    body = (MISS_LIVE_WHY.get(why) or MISS_WHY[why]).format(**words)
    searched = MISS_SEARCHED_WORDS.get(tuple(found.get("searched") or ()))
    if why == "not_found" and searched:
        body = MISS_WHY["not_found"].replace(", so it", f", nor does anything in {searched}, so it")
    if why == "not_found" and found.get("personal"):
        body += " " + MISS_PERSONAL_LINE
    if tail is None:
        tail = " Added to your list." if filed else " Nothing new was added."
    return (head + body + tail).strip()


def miss_search_plan(workspace_root=None, choices: Optional[List[Dict[str, Any]]] = None,
                     *, now_iso=None) -> Dict[str, Any]:
    """READ/COMPUTE (cr1#1202). What the apply run searches for each waiting
    "Missed something?" press, before it hands the hub list to the writer:
    `{searches: [{id, query, terms}], limit, days}`. The query is the item's
    own words without the words that only name the miss ("you missed",
    "forgot"); a press with no such words is left out (the writer refuses
    it). The run searches the business mailbox and the meetings connector
    with each query, through the personal-source wall it read at the start
    (`connector_reads:chat_wall`): never a personal connector, never a
    walled mailbox. At most `limit` results each, from the last `days`."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for c in choices or []:
        if not isinstance(c, dict) or str(c.get("kind") or "").strip().lower() != "miss" \
                or str(c.get("action") or "").strip().lower() != "tell":
            continue
        cid = str(c.get("id") or "").strip()
        inp = c.get("input") if isinstance(c.get("input"), dict) else {}
        terms = _miss_terms(" ".join(str(inp.get("text") or "").split())[:MISS_TEXT_CAP])
        if not cid or cid in seen or not terms:
            continue
        seen.add(cid)
        out.append({"id": cid, "query": " ".join(terms), "terms": terms})
    return {"searches": out, "limit": MISS_SEARCH_LIMIT, "days": MISS_SEARCH_DAYS}


#: cr1#1315: the chat the Hub's Ask, Something is wrong opens (cr1#1288;
#: "Something wrong?" until cr1#1591 made it the top bar's one Ask; the
#: prefix is unchanged). The page writes
#: the owner's words after this prefix, then what it was showing after
#: `PROBLEM_SHOWING`, then its ask line (cockpit.html `problemText`).
PROBLEM_PREFIX = "Something's wrong on my Command Room Hub:"
PROBLEM_SHOWING = "The Hub was showing "
#: Words in the owner's sentence that say an item is missing, and words that
#: say something did not work. Single words, and word pairs read in order
#: (apostrophes dropped, so "didn't" is "didnt"). A strong miss word ("missed")
#: decides on its own unless a negation sits just before it ("nothing is
#: missing"); a weak one ("where", "lost", "not on") is everyday speech, so
#: any failure word, a failed light on screen, or a word naming a part of the
#: page outweighs it (review of cr1#1315).
PROBLEM_MISS_STRONG = frozenset({"missed", "miss", "misses", "missing", "forgot", "forgotten", "disappeared",
                                 "vanished"})
PROBLEM_MISS_WEAK = frozenset({"lost", "gone", "dropped", "where", "nowhere"})
PROBLEM_MISS_PAIRS = frozenset({("not", "on"), ("not", "there"), ("isnt", "there"), ("isnt", "on"),
                                ("wasnt", "there"), ("wasnt", "on"), ("didnt", "show"), ("doesnt", "show"),
                                ("dont", "see"), ("cant", "find"), ("never", "showed"), ("never", "shows"),
                                ("not", "showing"), ("isnt", "showing"), ("not", "listed")})
PROBLEM_NEGATIONS = frozenset({"nothing", "not", "no", "isnt", "wasnt", "nothings"})
PROBLEM_PAGE_WORDS = frozenset({"light", "lights", "button", "buttons", "page", "screen", "banner", "link"})
PROBLEM_BUG_WORDS = frozenset({"error", "errors", "failed", "fails", "fail", "failing", "failure", "broken",
                               "broke", "crash", "crashed", "crashes", "crashing", "stuck", "frozen", "froze",
                               "freezes", "blank", "bug", "spinning"})
PROBLEM_BUG_PAIRS = frozenset({("wont", "load"), ("didnt", "load"), ("doesnt", "load"), ("not", "load"),
                               ("didnt", "finish"), ("not", "finish"), ("not", "working"), ("isnt", "working"),
                               ("doesnt", "work"), ("didnt", "work"), ("wont", "open"), ("not", "loading")})
#: What the Hub showed when a light failed or an error banner was up
#: (cockpit.html `hubShowing`).
PROBLEM_FAILED_SHOWN = (" did not finish (", "the message \"")
PROBLEM_ASK_ROUTE_LINE = "Is something missing from the Hub, or did something not work?"
PROBLEM_ASK_WHAT_LINE = "What was missed? Say the item in a few words."
#: The one question before anything is filed from the chat.
MISS_CHAT_ASK_LINE = "Add it to your list? Nothing is added until you say yes."


def _problem_route(said: str, showing: str) -> str:
    words = re.findall(r"[a-z]+", said.lower().replace("'", "").replace("’", ""))
    pairs = set(zip(words, words[1:]))
    strong = any(w in PROBLEM_MISS_STRONG and not (set(words[max(0, i - 2):i]) & PROBLEM_NEGATIONS)
                 for i, w in enumerate(words))
    if strong:
        return "miss"
    failed = any(m in showing for m in PROBLEM_FAILED_SHOWN)
    if set(words) & PROBLEM_BUG_WORDS or pairs & PROBLEM_BUG_PAIRS or failed:
        return "bug"
    if set(words) & PROBLEM_MISS_WEAK or pairs & PROBLEM_MISS_PAIRS:
        return "ask" if set(words) & PROBLEM_PAGE_WORDS else "miss"
    return "ask"


def miss_chat_plan(workspace_root=None, text: str = "", *, route: Optional[str] = None,
                   now_iso=None) -> Dict[str, Any]:
    """READ/COMPUTE (cr1#1315). What report-bug does with the message the
    Hub's Ask, Something is wrong opens a chat with:
    `{hub, route, said, showing, line?, choice?, searches?, limit?, days?}`.

    `hub` is False for any other message (report-bug's own flow then runs).
    `said` is the owner's words (the first line, after the prefix) and
    `showing` what the Hub was showing. `route`:
      * `miss`: an item is missing. `choice` is the apply run's own miss
        shape (`{id, kind: miss, action: tell, input: {text}}`), its id the
        same for the same words on the same day, so a yes said twice files
        once; `searches`, `limit` and `days` are `miss_search_plan`'s for it.
      * `bug`: something did not work (a failed light, an error): report-bug
        files it as a bug.
      * `ask`: the words say neither, or name a miss with nothing to look
        for; `line` is the one question. The owner's answer is passed back
        as `route` (`miss` or `bug`).
    A missing item wins over a failure named in the same words: the search
    writes nothing, and the owner can still report the failure. Writes
    nothing."""
    raw = str(text or "").replace("’", "'")
    head, _, rest = raw.strip().partition(PROBLEM_PREFIX)
    if head.strip() or not _:
        return {"hub": False}
    lines = rest.split("\n")
    said = " ".join(lines[0].split())[:MISS_TEXT_CAP]
    showing = ""
    for ln in lines[1:]:
        ln = " ".join(ln.split())
        if ln.startswith(PROBLEM_SHOWING):
            showing = ln[len(PROBLEM_SHOWING):].rstrip(".")[:MISS_TEXT_CAP]
    out: Dict[str, Any] = {"hub": True, "said": said, "showing": showing}
    want = str(route or "").strip().lower()
    out["route"] = want if want in ("miss", "bug") else _problem_route(said, showing)
    if out["route"] == "ask":
        out["line"] = PROBLEM_ASK_ROUTE_LINE
        return out
    if out["route"] != "miss":
        return out
    if not said or not _miss_terms(said):
        out.update(route="ask", line=PROBLEM_ASK_WHAT_LINE)
        return out
    now = _now(now_iso)
    try:
        import open_list as ol
        day = now.astimezone(ol._workspace_tz(Path(str(workspace_root)))).date().isoformat()
    except Exception:  # noqa: BLE001 - no workspace zone: the UTC day
        day = now.date().isoformat()
    key = " ".join(said.lower().split()) + "|" + day
    choice = {"id": "chat_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12], "kind": "miss",
              "action": "tell", "input": {"text": said}}
    out["choice"] = choice
    out.update(miss_search_plan(workspace_root, [choice], now_iso=now_iso))
    out["sources"] = _miss_open_sources(workspace_root)
    return out


def _miss_open_sources(workspace_root) -> List[str]:
    """The kinds the chat may search, by the personal-source wall: `mail`
    unless the mail connector is personal (a walled mailbox on a business
    server still leaves the business mailbox open), `meetings` unless the
    meetings connector is personal. An unread wall leaves both listed; the
    skill still follows the wall it read, and every hit is held to it."""
    try:
        import connector_reads as _cr
        wall = _cr.chat_wall(Path(str(workspace_root)))
    except Exception:  # noqa: BLE001
        return ["mail", "meetings"]
    shut = {str(c.get("category") or "") for c in wall.get("connectors") or [] if isinstance(c, dict)}
    return [k for k, cat in (("mail", "email"), ("meetings", "meetings")) if cat not in shut]


def miss_chat_answer(workspace_root=None, choice: Optional[Dict[str, Any]] = None,
                     live: Optional[Dict[str, Any]] = None, inbox: Optional[Dict[str, Any]] = None,
                     *, now_iso=None) -> Dict[str, Any]:
    """READ ONLY (cr1#1315). The Hub's Ask, Something is wrong chat's answer for a
    missing item, before anything is filed: `{ok, why, line, would_file,
    ask?, already?, found_where?, title?, source?, day?, searched?}`.

    The same judgement the apply run's writer makes (`_miss_find` over the
    book, then `_miss_live` over what the chat's search of the business
    mailbox and meetings found, every hit held to the personal-source wall
    read here from the book), so the answer says where the item was found
    and why the Hub missed it. `would_file` is True when the writer would
    add it to the list; then `ask` is the one question, and only the
    owner's yes sends the same `choice`, `live` and `inbox` to
    `apply_hub_choices`. A choice already filed answers `already: True` and
    asks nothing. Writes nothing; `source` for a walled hit is never
    handed back, because a walled hit is never used."""
    c = choice if isinstance(choice, dict) else {}
    inp = c.get("input") if isinstance(c.get("input"), dict) else {}
    text = " ".join(str(inp.get("text") or "").split())[:MISS_TEXT_CAP]
    cid = str(c.get("id") or "").strip()
    if not text or not cid or str(c.get("kind") or "") != "miss":
        return {"ok": False, "why": None, "would_file": False, "line": MISS_NEEDS_TEXT_LINE}
    root = Path(str(workspace_root))
    done = _miss_written(root).get(cid)
    if done:
        return {"ok": True, "already": True, "would_file": False, "why": done.get("why"),
                "line": str(done.get("line") or "").strip()}
    now = _now(now_iso)
    ref = f"{MISS_REF_SCHEME}:{cid}"
    found = _miss_find(root, text, now, skip_ref=ref)
    entry = (live or {}).get(cid) if isinstance(live, dict) else None
    if isinstance(entry, dict):
        found = _miss_live(root, found, entry, inbox)
    # A to-do a yes filed whose record did not land is reused by the writer
    # (`_apply_miss`), which then says it was added: so this answer asks too.
    would = found["why"] in MISS_FILES or found["why"] in MISS_LIVE_FILES
    out: Dict[str, Any] = {"ok": True, "why": found["why"], "would_file": bool(would),
                           "line": _miss_line(found, False, tail="" if would else None)}
    if would:
        out["ask"] = MISS_CHAT_ASK_LINE
    for k in ("found_where", "title", "day", "searched"):
        if found.get(k) not in (None, "", []) and (k != "title" or found["why"] != "not_found"):
            out[k] = found[k]
    # a link only: a stored pointer from the book is not one to open
    if str(found.get("source") or "").startswith(("https://", "http://")):
        out["source"] = found["source"]
    return out


def _miss_walled(wall: Dict[str, Any], hit: Dict[str, Any]) -> bool:
    """A hit the personal-source wall forbids: a personal connector's server,
    or a walled mailbox (its kept senders aside). A hit the wall cannot place
    (no server on a walled kind, no mailbox on a server that carries a walled
    one) is treated as walled: it is never used on a guess. A kept sender's
    mail is walled here too: it may be shown, but an answer is written."""
    import connector_reads as _cr
    sid = str(hit.get("server_id") or "").strip()
    kind = "meetings" if hit.get("source") == "meeting" else "email"
    for c in wall.get("connectors") or []:
        if sid and sid in (c.get("server_ids") or []):
            return True
        if not sid and str(c.get("category") or "") == kind:
            return True
    if hit.get("source") == "mail":
        boxes = [m for m in wall.get("mailboxes") or [] if isinstance(m, dict)]
        box = str(hit.get("mailbox") or "").strip()
        if not box:
            # no mailbox named: walled when any mailbox is, unless the hit's
            # server carries none of them
            return any((not sid) or sid in (m.get("server_ids") or []) for m in boxes)
        # A kept sender is shown, never written (CHAT_WALL.md): a miss answer
        # is written to the book, so even a kept sender's mail is not used.
        return not _cr.chat_mail_allowed({"mailboxes": [dict(m, except_senders=[]) for m in boxes]},
                                         account=box, sender=hit.get("sender"))
    return False


def _miss_live(root: Path, found: Dict[str, Any], entry: Dict[str, Any],
               inbox: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """cr1#1202: the book's answer, bettered by what the live search found.

    `entry` is `{searched, hits}` for this press: what the run searched
    (`mail`, `meetings`) and each hit as read. Every hit is first held to the
    personal-source wall (`connector_reads.chat_wall`, read here from the
    book, never trusted from the run): a walled hit is dropped unread, and a
    "not found" answer then says personal sources are never searched. A hit
    counts by the book's word rule; it answers only when the book's answer
    would file the item anyway and the hit holds more of the words (or the
    book found nothing). A mail hit is placed by the Inbox feed: set aside by
    the read (`filtered`: a rule, a drop, noise, a preference; or a closed
    thread), on the Inbox tab (`inbox`, which needs every word and files
    nothing), else never fetched. A meeting hit says whether Command Room
    processed that meeting. Read only."""
    import connector_reads as _cr
    from log_search import _tokens

    out = dict(found)
    srch = entry.get("searched") if isinstance(entry.get("searched"), list) else []
    searched = [k for k in ("mail", "meetings") if k in srch]
    if searched:
        out["searched"] = searched
    wall = _cr.chat_wall(root)
    personal = bool(wall.get("connectors") or wall.get("mailboxes"))
    # cr1#1315 review: a kind whose connector is personal was never searched,
    # whatever the run listed, so the answer never says it was.
    shut = {str(c.get("category") or "") for c in wall.get("connectors") or [] if isinstance(c, dict)}
    if out.get("searched"):
        kept = [k for k in out["searched"] if {"mail": "email", "meetings": "meetings"}[k] not in shut]
        if kept:
            out["searched"] = kept
        else:
            out.pop("searched")
    terms = found.get("terms") or []
    if not terms or not wall.get("readable"):
        # an unread wall cannot say what is personal: no hit is used
        out["personal"] = personal
        out.pop("searched", None) if not wall.get("readable") else None
        return out
    inbox = inbox if isinstance(inbox, dict) else {}
    need = max(1 if len(terms) == 1 else 2, (len(terms) + 1) // 2)
    filtered: Dict[str, Dict[str, Any]] = {}
    fl = inbox.get("filtered") if isinstance(inbox.get("filtered"), dict) else {}
    groups = fl.get("groups") if isinstance(fl.get("groups"), list) else []
    for g in groups:
        rows_ = g.get("rows") if isinstance(g, dict) and isinstance(g.get("rows"), list) else []
        for r in rows_:
            if isinstance(r, dict) and r.get("thread_id"):
                filtered.setdefault(str(r["thread_id"]), dict(r, group=r.get("group") or g.get("key")))
    items = inbox.get("items") if isinstance(inbox.get("items"), list) else []
    on_tab = {str(i.get("thread_id")) for i in items if isinstance(i, dict) and i.get("thread_id")}
    hits = entry.get("hits") if isinstance(entry.get("hits"), list) else []
    scored = []
    for h in hits:
        if not isinstance(h, dict) or h.get("source") not in ("mail", "meeting") or _miss_walled(wall, h):
            continue
        words = set(_tokens(" ".join(str(h.get(k) or "") for k in ("subject", "title", "snippet"))))
        n = sum(1 for t in terms if t in words)
        if n >= need:
            scored.append((-n, 0 if h["source"] == "mail" else 1, n, h))
    scored.sort(key=lambda x: (x[0], x[1]))
    book_n = found.get("found_n") or 0
    for _neg, _k, n, h in scored:
        if found["why"] not in MISS_FILES or (found["why"] != "not_found" and n <= book_n):
            break
        title = " ".join(str(h.get("subject") or h.get("title") or "").split())[:160]
        v: Dict[str, Any] = {"title": title, "found_n": n, "found_seq": None, "found_id": None, "thread": None,
                             "day": str(h.get("ts") or "")[:10] or None}
        if h["source"] == "mail":
            tid = str(h.get("thread_id") or "")
            v.update(found_where="mail", source=str(h.get("url") or "") or (f"mail:{tid}" if tid else ""))
            row = filtered.get(tid) if tid else None
            if row is not None:
                group = str(row.get("group") or "")
                rsrc = row.get("source") if isinstance(row.get("source"), dict) else {}
                rule = " ".join(str(rsrc.get("rule") or "").split())[:160]
                v["filter"] = group
                if group == "handled":
                    v.update(why="closed_thread", by=str(row.get("why") or FILTERED_HANDLED_WHY))
                elif group == "rule":
                    v.update(why="filtered", rule=rule or None,
                             by=f' by your mail rule "{rule}"' if rule else " by one of your mail rules")
                elif group in ("noise", "newsletters"):
                    v.update(why="filtered", by=" as " + str(row.get("why") or "noise"))
                else:
                    v.update(why="filtered", by=": " + str(row.get("why") or FILTERED_DROPPED_WHY))
            elif tid and tid in on_tab:
                if n < len(terms) or found["why"] != "not_found":
                    continue          # the Hub had it only if every word is there, and
                    #                   it never stops the filing of what the book would file
                v.update(why="inbox")
            else:
                v.update(why="never_fetched")
        else:
            mid = str(h.get("meeting_id") or "")
            v.update(found_where="meeting", source=str(h.get("url") or "") or (f"meeting:{mid}" if mid else ""),
                     why="meeting_no_promise" if mid and _meeting_processed(root, mid) else "meeting_unprocessed")
        out.update(v)
        return out
    out["personal"] = personal
    return out


def _meeting_processed(root: Path, meeting_id: str) -> bool:
    """Has Command Room processed this meeting: a `meeting_processed` row
    whose pointer ends in its id."""
    try:
        from events_io import load_events_org_scoped
        for ev in load_events_org_scoped(str(root), drop_personal=False)[0]:
            if isinstance(ev, dict) and ev.get("type") == "meeting_processed":
                d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                # the meeting's own id is the pointer's last part (`granola:<id>`)
                tail = str(d.get("source_ref") or ev.get("source_ref") or "").rsplit(":", 1)[-1]
                if tail and tail == meeting_id:
                    return True
    except Exception:  # noqa: BLE001 - an unread log has processed nothing it can show
        return False
    return False


def _miss_written(root: Path) -> Dict[str, Dict[str, Any]]:
    """Every miss already answered, by the press's id: a press applied twice
    answers from its first write."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from events_io import load_events_org_scoped
        for ev in load_events_org_scoped(str(root), drop_personal=False)[0]:
            if isinstance(ev, dict) and ev.get("type") == MISS_EVENT:
                d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                if d.get("hub_choice"):
                    out[str(d["hub_choice"])] = d
    except Exception:  # noqa: BLE001 - no log means nothing answered yet
        return out
    return out


def _apply_miss(root: Path, c: Dict[str, Any], actor: str, now: _dt.datetime,
                answered: Dict[str, Dict[str, Any]], live: Optional[Dict[str, Any]] = None,
                inbox: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One "Missed something?" press: find, answer, file, log."""
    import commitment_state as cs
    from event_gate import append_event

    inp = c.get("input") if isinstance(c.get("input"), dict) else {}
    text = " ".join(str(inp.get("text") or "").split())[:MISS_TEXT_CAP]
    choice_id = str(c.get("id") or "").strip()
    if choice_id and choice_id in answered:
        d = answered[choice_id]
        return {"outcome": "applied", "replayed": True, "line": d.get("line") or "",
                "why": d.get("why"), "commitment_id": d.get("filed_id")}
    if not text:
        return {"outcome": "error", "line": MISS_NEEDS_TEXT_LINE}
    ref = f"{MISS_REF_SCHEME}:{choice_id}"
    events_path = root / "_hq" / "data" / "events.jsonl"
    # A retry after the to-do landed but the record did not: reuse that
    # to-do, and leave it out of the search so it is not its own answer.
    filed_id = _todo_id(events_path, ref) if choice_id else None
    found = _miss_find(root, text, now, skip_ref=ref)
    entry = (live or {}).get(choice_id) if isinstance(live, dict) and choice_id else None
    if isinstance(entry, dict):
        found = _miss_live(root, found, entry, inbox)
    if (found["why"] in MISS_FILES or found["why"] in MISS_LIVE_FILES) and not filed_id:
        out = cs.create_personal_task(str(root), title=text[:240], owner_id=actor, source_ref=ref,
                                      source_skill=SOURCE_SKILL, source=TODO_SOURCE)
        filed_id = (((out or {}).get("commitment") or {}).get("data") or {}).get("id") \
            or _todo_id(events_path, ref)
    line = _miss_line(found, bool(filed_id))
    data = {"hub_choice": choice_id, "text": text, "why": found["why"], "line": line,
            "found_seq": found.get("found_seq"), "found_id": found.get("found_id"),
            "found_source": found.get("source") or None, "filed_id": filed_id,
            "terms": found.get("terms") or [], "found_where": found.get("found_where"),
            "filter": found.get("filter"), "rule": found.get("rule"), "searched": found.get("searched")}
    append_event(root / "_hq" / "data" / "events.jsonl",
                 {"type": MISS_EVENT, "source_skill": SOURCE_SKILL, "person_ids": [actor] if actor else [],
                  "data": {k: v for k, v in data.items() if v not in (None, "", [])}},
                 holder="cockpit_helpers")
    return {"outcome": "applied", "line": line, "why": found["why"], "commitment_id": filed_id}


def apply_hub_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                      *, resolved_by: str = "", now_iso=None, live: Optional[Dict[str, Any]] = None,
                      inbox: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A WRITER. The Today page's reminder and to-do choices, each through
    the owning writer:

      * reminder `add`: `reminders.capture_reminder`, the user's own words and
        the day they picked (`parse_later_when`: a date, or days from today);
      * reminder `done`: one `reminder_cleared` (a repeating one re-arms);
      * reminder `push`: one `reminder_updated` with the new day; a push
        marked `restore` (the Hub's Undo on a cleared reminder, cr1#1636)
        may re-arm one a clear took off the list, never a deleted one;
      * reminder `delete` (cr1#1221): one `reminder_deleted`, gone from
        every surface for good (a repeating one never re-arms), carrying
        its undo handle for the owner's `undo`;
      * todo `add`: `commitment_state.create_personal_task` with the priority
        and `source = TODO_SOURCE`, owned by the owner, its pointer unique to
        the choice so the same words added twice are two items;
      * stream_edit, person_edit, priority_edit `add` / `rename` / `undo`
        (cr1#1383 part 2), commitment_edit `rename` / `undo` (slice 4,
        the wording through `edit_commitment_wording`, the date through
        `due_set`), and stream_edit, person_edit and commitment_edit
        `context` (Add context: a thread's notes through `update_thread`, a
        person's fact through `record_person_fact`, a commitment's note
        through `commitment_state.add_commitment_note`, with Take back):
        `hub_edit.apply_one`, through each record's own writer, judged
        before any write;
      * meeting_edit `attach` / `undo` (cr1#1632, Attached): a link or a
        file on a meeting, and its Remove, through `hub_edit.apply_one`,
        whose own row is the record; a name or address naming someone off
        limits or personal is refused before any write;
      * miss `tell` (cr1#1017): the header's "Missed something?". The book is
        searched for the item (`_miss_find`); the answer says what was found
        and why the Hub did not show it; an item with no live row is filed as
        a to-do; one `hub_miss_reported` row records the miss (the week's
        measure counts it). A press already answered answers again from it.
        cr1#1202: `live` carries, by the press's id, what the apply run's
        search of the business mailbox and meetings found (`{searched,
        hits}`, from `miss_search_plan`), and `inbox` the Inbox feed as read
        (`feed/inbox`, its `filtered` list and `items`); `_miss_live` judges
        them through the personal-source wall and says where it was found
        and why it was missed. Without them the book alone answers.

    A to-do is closed, snoozed or reopened as a commitment, through
    `apply_commitment_choices`. A refusal of one row never stops the rest.
    Answers `{ok, results: [{id, kind, action, outcome, line}], n_applied,
    n_errors}`."""
    actor = str(resolved_by or "").strip() or _owner_id(workspace_root)
    rows = [c for c in (choices or []) if isinstance(c, dict)]
    if not actor:
        return {"ok": False, "reason": "no_actor", "line": NO_ACTOR_LINE,
                "results": [], "n_applied": 0, "n_errors": len(rows)}
    import commitment_state as cs
    import reminders as rm
    from event_gate import append_event

    now = _now(now_iso)
    root = Path(str(workspace_root))
    events_path = root / "_hq" / "data" / "events.jsonl"
    written = _hub_writes_in_book(root)
    answered = _miss_written(root) if any(str(c.get("kind") or "").strip().lower() == "miss" for c in rows) else {}
    active = None
    kept = None   # cr1#1636 item 6: reminders a restore may put back, read once
    results: List[Dict[str, Any]] = []
    for c in rows:
        kind = str(c.get("kind") or "").strip().lower()
        verb = str(c.get("action") or "").strip().lower()
        inp = c.get("input") if isinstance(c.get("input"), dict) else {}
        r: Dict[str, Any] = {"id": c.get("id"), "kind": kind, "action": verb}
        choice_id = str(c.get("id") or "").strip()
        try:
            if kind == "reminder" and choice_id and choice_id in written:
                r.update(_replayed(written[choice_id], verb))
            elif kind == "reminder" and verb == "add":
                text = str(inp.get("summary") or "").strip()[:SUMMARY_CAP]
                # cr1#1020: a reminder made from a commitment names it (`ref`),
                # and may have no day: the owner asked for one without a date.
                ref = str(inp.get("ref") or "").strip()[:REMINDER_REF_CAP] or None
                # cr1#1658 change 4: a reminder made from a message names its thread, and
                # only its thread (round-2 fix check: the press's own ref is never kept beside
                # one); change 8: one made from a meeting names that meeting, and only it
                if str(inp.get("thread_id") or "").strip():
                    ref = _mail_pointer(inp.get("thread_id"))
                elif str(inp.get("meeting") or "").strip():
                    ref = _meeting_pointer(inp.get("meeting"))
                who = _person_on_file(root, inp.get("person"))
                gate = _mail_make_gate(root, inp) or _made_gate(root, inp)
                # a ref naming a meeting with no meeting given is the same forged shape: refused
                if not gate and str(inp.get("ref") or "").strip().lower().startswith("meeting:") and not str(inp.get("meeting") or "").strip():
                    gate = MEETING_MAKE_UNKNOWN_LINE
                # fix check of round 1: a ref naming a mail thread is a make from a message, so it
                # carries its thread (and is gated); a ref given alone is refused
                if not gate and str(inp.get("ref") or "").strip().lower().startswith("mail:") and not str(inp.get("thread_id") or "").strip():
                    gate = MAIL_MAKE_BAD_LINE
                # fix check of round 1 of #1717: any other raw ref (a person:, a made-up id) is
                # kept only when it names an open item on the list, as the page sends it
                if (not gate and str(inp.get("ref") or "").strip() and not str(inp.get("thread_id") or "").strip()
                        and not str(inp.get("meeting") or "").strip() and not _open_item(root, inp.get("ref"))):
                    gate = REMINDER_BAD_REF_LINE
                if gate:
                    r.update(outcome="refused", line=gate)
                    results.append(r)
                    continue
                if ((ref and str(ref).startswith(("mail:thread:", "meeting:")) and _reminder_made(root, now, ref, text))
                        or (who and _reminder_made(root, now, None, text, person=who))):
                    r.update(outcome="already", line=MAKE_ALREADY_LINE)
                    results.append(r)
                    continue
                undated = inp.get("undated") is True and not str(inp.get("when") or "").strip()
                when, why = (None, None) if undated else _hub_day_checked(inp.get("when"), now, root)
                if not text:
                    r.update(outcome="error", line=REMINDER_NEEDS_TEXT_LINE)
                elif undated:
                    today = cs._now_calendar_day(_iso(now), workspace_path=str(root)) or now.date()
                    ev = rm.build_reminder_event(text, remind_from=today, ref=ref, undated=True,
                                                 source_skill=SOURCE_SKILL, person_ids=[who] if who else None)
                    ev.setdefault("data", {})["hub_choice"] = choice_id
                    append_event(events_path, ev, holder="cockpit_helpers")
                    r.update(outcome="applied", line=REMINDER_UNDATED_LINE, reminder_id=ev["data"]["id"])
                elif not when:
                    r.update(outcome="error", line=why)
                else:
                    # A day asked for is the day it pins from, as on the Today
                    # page; it is not a deadline (show-my-reminders sets
                    # `due` only for a real one).
                    ev = rm.build_reminder_event(text, remind_from=when, ref=ref, source_skill=SOURCE_SKILL,
                                                 person_ids=[who] if who else None)
                    ev.setdefault("data", {})["hub_choice"] = choice_id
                    append_event(events_path, ev, holder="cockpit_helpers")
                    r.update(outcome="applied", line=f"Reminder set for {when}.",
                             reminder_id=ev["data"]["id"])
            elif kind == "reminder" and verb in ("done", "push", "delete"):
                rid_ = str(c.get("n") or "").strip()
                if active is None:
                    # The owner's day, the one `feed_mine` built the list on (cr1#688).
                    today = cs._now_calendar_day(_iso(now), workspace_path=str(root)) or now.date()
                    active = {a.get("id") for a in rm.load_active_reminders(root, today, surface="m_facing")}
                # cr1#1636 item 6: the Hub's Undo on a cleared reminder is a push
                # marked `restore`. It may put back one a clear took off the list
                # (the reader re-arms a cleared reminder on a push), never one
                # that was deleted; a plain push still needs it on the list.
                known = bool(rid_) and rid_ in active
                if not known and rid_ and verb == "push" and inp.get("restore") is True:
                    if kept is None:
                        kept = _reminders_kept(root)
                    known = rid_ in kept
                if not known:
                    r.update(outcome="already" if verb in ("done", "delete") else "error", line=REMINDER_GONE_LINE)
                elif verb == "delete":
                    ev = rm.build_reminder_deleted_event(rid_, source_skill=SOURCE_SKILL)
                    ev.setdefault("data", {})["hub_choice"] = choice_id
                    append_event(events_path, ev, holder="cockpit_helpers")
                    active.discard(rid_)  # a later press on it in this run finds it gone
                    r.update(outcome="applied", line=REMINDER_DELETED_LINE)
                elif verb == "done":
                    # The occurrence the page showed: a repeating reminder cleared
                    # twice clears that one day, never the next one too (cr1#675).
                    occ = str(inp.get("occurrence") or "").strip()[:10] or None
                    ev = rm.build_reminder_cleared_event(rid_, occurrence=occ, source_skill=SOURCE_SKILL)
                    ev.setdefault("data", {})["hub_choice"] = choice_id
                    append_event(events_path, ev, holder="cockpit_helpers")
                    r.update(outcome="applied", line="Cleared.")
                else:
                    when, why = _hub_day_checked(inp.get("when") or c.get("input"), now, root)
                    if not when:
                        r.update(outcome="error", line=why)
                    else:
                        ev = rm.build_reminder_updated_event(
                            rid_, action="push", remind_from=when, source_skill=SOURCE_SKILL)
                        ev.setdefault("data", {})["hub_choice"] = choice_id
                        append_event(events_path, ev, holder="cockpit_helpers")
                        # review of cr1#1636: a reminder put back is on the list
                        # again for the rest of this run (an Undo then a Done)
                        active.add(rid_)
                        r.update(outcome="applied", line=f"Moved to {when}.")
            elif kind == "miss" and verb == "tell":
                r.update(_apply_miss(root, c, actor, now, answered, live, inbox))
            elif kind == "meeting" and verb in MEETING_VERBS:
                import hub_meeting

                r.update(hub_meeting.apply_one(root, c, actor))
            elif kind in EDIT_KINDS and (verb in EDIT_VERBS or (verb in ("context", "attach") and verb in _edit_verbs(kind))
                                         or (verb in ("update", "order", "star") and verb in _edit_verbs(kind))):
                import hub_edit

                r.update(hub_edit.apply_one(root, c, actor))
            elif kind == "todo" and verb == "add":
                text = str(inp.get("title") or "").strip()
                ref = f"{TODO_REF_SCHEME}:{c.get('id')}"
                prior = _todo_id(events_path, ref) if choice_id else None
                # review round 1 of #1699: a make from a message is checked first (B1,
                # B5), and the same make pressed twice is added once (B3)
                gate = None if prior else (_mail_make_gate(root, inp) or _made_gate(root, inp))
                made = None if prior or gate or not text else _open_made(root, _made_from(root, inp) or "", text[:240])
                if not text:
                    r.update(outcome="error", line=TODO_NEEDS_TEXT_LINE)
                elif gate:
                    r.update(outcome="refused", line=gate)
                elif made:
                    r.update(outcome="already", line=MAKE_ALREADY_LINE, commitment_id=made)
                elif prior:
                    r.update(outcome="applied", replayed=True, commitment_id=prior,
                             line=f"Added as {PRIORITY_NAMES[_priority(inp.get('priority'))].lower()} priority.")
                else:
                    on_project = _live_project(root, inp.get("project"))
                    out = cs.create_personal_task(
                        str(root), title=text[:240], owner_id=actor,
                        source_ref=f"{TODO_REF_SCHEME}:{c.get('id')}",
                        source_skill=SOURCE_SKILL, priority=_priority(inp.get("priority")),
                        source=TODO_SOURCE, about_ref=_made_from(root, inp),
                        primary_thread_id=on_project)
                    cid = ((out or {}).get("commitment") or {}).get("data", {}).get("id")                         or _todo_id(events_path, f"{TODO_REF_SCHEME}:{c.get('id')}")
                    level = PRIORITY_NAMES[_priority(inp.get('priority'))].lower()
                    r.update(outcome="applied",
                             line=((TODO_PERSONAL_PROJECT_LINE if _live_project(root, inp.get("project"), any_lane=True) else TODO_NO_PROJECT_LINE).format(level=level)
                                   if str(inp.get("project") or "").strip() and not on_project else f"Added as {level} priority."),
                             commitment_id=cid)
            elif kind == "waiting" and verb == "add":
                r.update(_hub_waiting_add(root, inp, actor))
            else:
                r.update(outcome="error", line=UNKNOWN_VERB_LINE.format(verb=f"{kind} {verb}".strip() or "none"))
        except Exception as exc:  # noqa: BLE001 - one row's failure never stops the batch
            r.update(_row_error(exc))
        results.append(r)
    n_applied = sum(1 for r in results if r["outcome"] == "applied")
    n_errors = sum(1 for r in results if r["outcome"] == "error")
    return {"ok": True, "results": results, "n_applied": n_applied, "n_errors": n_errors}


#: What the refresh leg prunes (cr1#683): an applied choice older than the
#: expiry window, by its applied day (its press day when none). The page keeps
#: a result tag and an Undo on a close for that long; past it the row's item is
#: long gone from the lanes, and a collection that grows for good slows every
#: redraw and every query, and ends at the database's document cap.
PRUNE_LINE = "{n} old choices cleared from the page."


def plan_prune_choices(workspace_root=None, choices: Optional[List[Any]] = None, *,
                       now_iso=None) -> Dict[str, Any]:
    """READ/COMPUTE. The applied choices the refresh leg deletes from the
    page's database: `{deletes: [ids], writes: [one delete op each], n,
    line}`, ready to send as ONE ArtifactData `batch`. A waiting choice is
    never pruned (the apply leg refuses an expired one with its line and
    marks it applied, and this prunes it a week later); nor is a claimed
    one, whatever its age."""
    now = _now(now_iso)
    cutoff = now - _dt.timedelta(days=CHOICE_EXPIRY_DAYS)
    deletes: List[str] = []
    seen = set()
    for raw in choices or []:
        c = _flat_choice(raw)
        if not c:
            continue
        cid = str(c.get("id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        if not (c.get("applied") is True or c.get("applied_at")):
            continue
        if _claim_live(c, now):
            continue
        at = _parse(c.get("applied_at")) or _parse(c.get("created_at"))
        if at is None or at >= cutoff:
            continue
        deletes.append(cid)
    writes = [{"op": "delete", "collection": "choices", "doc_id": cid} for cid in deletes]
    return {"deletes": deletes, "writes": writes, "n": len(deletes),
            "line": PRUNE_LINE.format(n=len(deletes)) if deletes else ""}


def newer_message_arrived(workspace_root=None, message_ts: Optional[str] = None,
                          messages: Optional[List[Any]] = None) -> Dict[str, Any]:
    """The apply leg's send gate, decided in code: `{newer, line, n_newer}`.
    `messages` is the thread as the run re-read it, each a dict with a `ts`
    (or a bare timestamp string); `message_ts` is the newest message the
    draft answered, from the inbox feed. A thread with a message after it
    refuses the send with the one line. No feed timestamp means nothing can
    be compared, and the send is refused on that ground too: a draft the run
    cannot place in its thread is not sent on a guess."""
    base = _parse(message_ts)
    if base is None:
        return {"newer": True, "n_newer": None, "line": NEWER_MESSAGE_LINE}
    n = 0
    for m in messages or []:
        ts = _parse(m.get("ts") if isinstance(m, dict) else m)
        if ts is not None and ts > base:
            n += 1
    return {"newer": n > 0, "n_newer": n, "line": NEWER_MESSAGE_LINE if n else ""}


def reply_reaches_everyone(workspace_root=None, platform: Optional[str] = None,
                           draft: Optional[Dict[str, Any]] = None, action: str = "send") -> Dict[str, Any]:
    """The Apply run's reply-all gate (cr1#1251), decided in code:
    `{refuse, reason, line, n_recipients}`. `platform` is the mail service
    the run resolved (`mail_seams`' `declared_email.provider`; with none, the
    seat's declared email backend is read here); `draft` is the press's draft
    (`to`, `cc`). A reply to more than one person is refused unless the
    service's reply tool carries To and Cc itself (Superhuman, Gmail): on
    Microsoft 365 its reply tool answers the sender alone, and on a service
    the run cannot name nothing vouches that everyone is reached. Leaving
    the reply unsent is safer than sending it to fewer people than the owner
    saw. A press with no draft is left to the run's own no-draft refusal.
    Read only."""
    from inbox_helpers import _addresses

    if not isinstance(draft, dict):
        return {"refuse": False, "reason": None, "line": "", "n_recipients": None}
    seen: List[str] = []
    for addr in _addresses(draft.get("to")) + _addresses(draft.get("cc")):
        if addr not in seen:
            seen.append(addr)
    plat = _mail_platform(platform, workspace_root)
    if plat not in REPLY_ALL_PLATFORMS and len(seen) > 1:
        draft_act = str(action) == "draft"
        if plat:
            line = GROUP_REPLY_DRAFT_LINE if draft_act else GROUP_REPLY_LINE
        else:
            line = GROUP_REPLY_UNKNOWN_DRAFT_LINE if draft_act else GROUP_REPLY_UNKNOWN_LINE
        return {"refuse": True, "reason": "group_reply_backend", "n_recipients": len(seen),
                "platform": plat or None, "line": line}
    return {"refuse": False, "reason": None, "line": "", "n_recipients": len(seen), "platform": plat or None}


def _owner_reopens(workspace_root) -> Dict[str, _dt.datetime]:
    """cr1#1462: the time of each item's latest reopen the owner made
    (`event_types.is_customer_act`), read from the book once per batch: the
    time the owner pressed it (`data.pressed_at`, a Hub reopen, on the
    page's clock like the press it is compared with), else the time it was
    written. A reopen a scheduled job made is left out. Never raises: an unreadable
    book answers nothing, and every press is judged as before."""
    out: Dict[str, _dt.datetime] = {}
    try:
        from event_types import is_customer_act
        from events_io import load_events_org_scoped

        events, _skipped = load_events_org_scoped(workspace_root)
        for ev in events:
            if not isinstance(ev, dict) or ev.get("type") != "commitment_reopened":
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            cid = str(data.get("commitment_id") or "").strip()
            at = _parse(data.get("pressed_at")) or _parse(ev.get("ts"))
            if not cid or at is None or not is_customer_act(ev):
                continue
            if cid not in out or at > out[cid]:
                out[cid] = at
    except Exception:  # noqa: BLE001 - the check only tightens; it never blocks the batch
        return {}
    return out


def _commitment_seqs(workspace_root) -> Dict[str, int]:
    from events_io import load_events_org_scoped

    out: Dict[str, int] = {}
    events, _skipped = load_events_org_scoped(workspace_root)
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "commitment":
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            cid = data.get("id")
            if cid and ev.get("seq") is not None:
                out[str(cid)] = ev["seq"]
    return out


def _open_rows(workspace_root, now_iso) -> Optional[Dict[str, Dict[str, Any]]]:
    """The plate's open rows by id, or None when the plate refused (no owner
    resolved, an unreadable book): a refusal is never read as "all closed"."""
    from plate_view import build_plate

    # cr1#1292: snoozed rows included, so a press on a row the page still
    # shows is answered on that row's own shape.
    view = build_plate(str(workspace_root), now_iso=now_iso, include_snoozed=True)
    if not isinstance(view, dict) or view.get("ok") is False or view.get("error"):
        return None
    out: Dict[str, Dict[str, Any]] = {}
    for r in view.get("rows") or []:
        if isinstance(r, dict) and r.get("id"):
            out[str(r["id"])] = r
    return out


def _not_work_queued(workspace_root, cid: str, actor: str) -> tuple:
    """cr1#1289: "Not work" on a row still waiting for a yes, or on a line
    only heard: the queue's own drop (`needs_review_queue.drop_items`), which
    closes through `close_commitment` and promotes a heard line first.
    Answers `(handler, outcome, line)`."""
    from needs_review_queue import drop_items

    res = drop_items(str(workspace_root), [cid], resolved_by=actor,
                     evidence=NOT_WORK_EVIDENCE, source_skill=COMMITMENT_SRC)
    one = ((res or {}).get("results") or [{}])[0]
    st = one.get("status")
    if st == "closed":
        return one, "applied", NOT_WORK_LINE
    if st == "already_resolved":
        return one, "already", ALREADY_CLOSED_LINE
    if st == "confirmed_open":
        return one, "error", NOT_WORK_FAILED_LINE
    if st == "has_subitems":
        return one, "error", SUBITEMS_LINE
    return {"status": st or "error", **one}, "error", NOT_WORK_FAILED_LINE


def _assign_home(workspace_root, cid: str, target: str, ledger_open: set, now,
                 label: str, choice_id: str, extra: Optional[Dict[str, Any]] = None,
                 events: Optional[list] = None, move: bool = False) -> tuple:
    """cr1#1289: "Assign to" on a "No home yet" row. The book picks the
    target: it must be a live work stream on the thread cards, never only
    the page's word. The promise must be open and on no thread (the open
    list's own row). One `reclassification` of its capture row, marked
    `open_list.HOME_ORIGIN`; a press already written answers from it.
    `events` is the press's one read of the log (cr1#1342), read here when
    not given. Answers `(handler, outcome, line)`."""
    import open_list as ol
    import thread_cards as tc
    from event_gate import append_event

    root = Path(str(workspace_root))
    if cid not in ledger_open:
        return {"status": "already_resolved"}, "already", ALREADY_CLOSED_LINE
    cards, _from_log = tc.read_cards(root)
    streams = cards.get("work_streams") or {}
    # cr1#1658 change 6: a move goes to any live project on the cards, a
    # thread or a work stream; Assign to keeps to work streams
    places = {**(cards.get("threads") or {}), **streams} if move else streams
    if not target or target not in places:
        return ({"status": "refused", "error": "not_a_project" if move else "not_a_work_stream"}, "refused",
                REHOME_NO_TARGET_LINE if move else ASSIGN_NO_STREAM_LINE)
    events_path = root / "_hq" / "data" / "events.jsonl"
    if events is None:
        from events_io import load_events_owner_scoped

        events = load_events_owner_scoped(str(root))[0]
    for ev in events:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if (ev.get("type") == "reclassification" and d.get("origin") == ol.HOME_ORIGIN
                and choice_id and d.get("hub_choice") == choice_id):
            return {"status": "already_written", "seq": ev.get("seq")}, "already", "Already given that home."
    doc, _counted = ol.read_list(root, now)
    row = next((x for x in doc.get("rows") or [] if str(x.get("id")) == cid), None)
    if row is None:
        return {"status": "already_resolved"}, "already", ALREADY_CLOSED_LINE
    here = str(row.get("thread_id") or "").strip()
    if here and not move:
        return {"status": "has_home", "thread_id": row.get("thread_id")}, "already", ASSIGN_HAS_HOME_LINE
    if move and not here:
        # ruling 86: a row with no project keeps Assign to and its work-stream rule
        return {"status": "refused", "error": "no_home"}, "refused", REHOME_NO_HOME_LINE
    if move and here == target:
        return {"status": "same_home", "thread_id": here}, "already", REHOME_SAME_LINE
    seq = (row.get("source") or {}).get("seq")
    src = next((e for e in events if isinstance(e, dict) and e.get("seq") == seq), None) if seq is not None else None
    if src is None:
        return {"status": "error", "error": "no_capture_row"}, "error", GONE_LINE
    if move:
        stop = _move_refusal(root, cid, target, src, here)
        if stop:
            return stop
    old_related = list(src.get("related_thread_ids") or [])
    new_related = [t for t in old_related if t != target]
    # cr1#1658: a move records where the item is now (a home given earlier
    # included), so its Undo puts it back there, not on the capture's thread
    data = {"origin": ol.HOME_ORIGIN, "commitment_id": cid, "target_thread_id": target,
            "old_primary_thread_id": (here or src.get("primary_thread_id")) if move else src.get("primary_thread_id"),
            "new_primary_thread_id": target,
            "old_related_thread_ids": old_related, "new_related_thread_ids": new_related,
            "reason": REHOME_REASON if move else ASSIGN_REASON, "cockpit_batch": label}
    if choice_id:
        data["hub_choice"] = choice_id
    if extra:
        # cr1#1341: `created_stream` names the stream this press created, the
        # one stream its Undo after Apply may archive.
        data.update(extra)
    landed = append_event(events_path, {
        "type": "reclassification", "source_skill": COMMITMENT_SRC, "supersedes_seq": seq,
        "primary_thread_id": target, "related_thread_ids": new_related,
        "classification_confidence": 1.0, "data": data}, holder="cockpit_helpers")
    name = str((places.get(target) or {}).get("name") or "").strip()
    out_seq = (landed[0] if isinstance(landed, list) and landed else {}).get("seq")
    return ({"status": "assigned", "thread_id": target, "seq": out_seq}, "applied",
            f"Moved to {name}." if name else "Given a home.")


def _move_refusal(root: Path, cid: str, target: str, capture: dict, here: str = "") -> Optional[tuple]:
    """Review round 1 of #1700: a move is refused, before anything is
    written, when the fence cannot be read (fail closed), when the item is
    about someone off limits (`hub_edit._off_limits_item`, as Edit refuses),
    or when it would cross the personal line: an item on the personal lane
    (`hub_work.fence`'s `personal`) only to a personal project, an item off
    it only to a work one (`personal_leak.business_thread_ids`).
    Answers the press's `(handler, outcome, line)`, or None to go on."""
    import hub_edit
    import hub_work
    from cru_match import _commitment_id, load_open_commitments
    from personal_leak import business_thread_ids

    fence = hub_work.fence(root)
    if fence is None:
        return {"status": "refused", "error": "fence_unread"}, "refused", REHOME_FENCE_LINE
    live = next((e for e in load_open_commitments(root / "_hq" / "data" / "events.jsonl")
                 if str(_commitment_id(e)) == cid), None)
    off = hub_edit._off_limits_item(root, cid, live, capture)
    if off is None:
        return {"status": "refused", "error": "fence_unread"}, "refused", REHOME_FENCE_LINE
    if off:
        return {"status": "refused", "error": "off_limits"}, "refused", REHOME_OFF_LIMITS_LINE
    work = business_thread_ids(root)
    if not work:
        # #1700's round-2 note (a): no work project read (no threads list) is no answer; refuse
        return {"status": "refused", "error": "fence_unread"}, "refused", REHOME_FENCE_LINE
    # fix check of round 1: an item is on the personal side when the fence says
    # so (person ties, personal reminders) or when the project it is on is
    # not a work one, ties or not
    personal_item = cid in (fence.get("personal") or set()) or bool(here and here not in work)
    business_target = target in work
    if personal_item == business_target:
        return {"status": "refused", "error": "crosses_lane"}, "refused", REHOME_LANE_LINE
    return None


def _lanes(root, projects: Dict[str, str]) -> tuple:
    """Fix check of round 1 of #1700: `(fence, {ids on the personal side})`:
    the fence's personal lane, plus every item whose project is not a work
    one. The fence is None when it cannot be read; the set is then empty."""
    import hub_work
    from personal_leak import business_thread_ids

    fence = hub_work.fence(root)
    if fence is None:
        return None, set()
    work = business_thread_ids(root)
    out = set(fence.get("personal") or set())
    out |= {cid for cid, pid in projects.items() if pid and pid not in work}
    return fence, out


def _projects_of(root, now) -> Dict[str, str]:
    """Review round 1 of #1700: {item id: the project it is on}, from the
    open list (a home given on the Hub included), so a row names its project
    by id and Put in... leaves it out by id, never by name."""
    import open_list as ol

    try:
        doc, _counted = ol.read_list(Path(str(root)), now)
    except Exception:  # noqa: BLE001 - no list: no ids, the page falls back on names
        return {}
    return {str(r.get("id")): str(r.get("thread_id") or "") for r in doc.get("rows") or [] if r.get("thread_id")}


def _home_written(root: Path, choice_id: str, events: Optional[list] = None) -> Optional[Dict[str, Any]]:
    """cr1#1289 part two: the `hub_assign` home a press already wrote, read
    before anything else so Apply twice tracks, creates and writes nothing.
    `events` is the press's one read of the log (cr1#1342)."""
    import open_list as ol

    if not choice_id:
        return None
    if events is None:
        from events_io import load_events_owner_scoped

        events = load_events_owner_scoped(str(root))[0]
    for ev in events:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if (ev.get("type") == "reclassification" and d.get("origin") == ol.HOME_ORIGIN
                and d.get("hub_choice") == choice_id):
            return ev
    return None


def _no_home_state(root: Path, cid: str, ledger_open: set, now) -> Optional[tuple]:
    """None while the promise is open and on no thread; else the press's
    `(handler, outcome, line)`, the same answers `_assign_home` gives, read
    before a new work stream is created so a stale press creates nothing."""
    import open_list as ol

    if cid not in ledger_open:
        return {"status": "already_resolved"}, "already", ALREADY_CLOSED_LINE
    doc, _counted = ol.read_list(root, now)
    row = next((x for x in doc.get("rows") or [] if str(x.get("id")) == cid), None)
    if row is None:
        return {"status": "already_resolved"}, "already", ALREADY_CLOSED_LINE
    if str(row.get("thread_id") or "").strip():
        return {"status": "has_home", "thread_id": row.get("thread_id")}, "already", ASSIGN_HAS_HOME_LINE
    return None


def _stream_name(value: Any) -> str:
    """The typed name, spaces tidied, control characters out, capped."""
    text = "".join(ch_ if ch_.isprintable() else " " for ch_ in str(value or ""))
    return " ".join(text.split())[:NEW_STREAM_NAME_CAP].strip()


def _stream_for_name(root: Path, name: str, events: Optional[list] = None) -> tuple:
    """What a typed name means on this book, read only: `("existing", id)`
    for a live work stream of that name (any case), `("resume", id)` for a
    stream an earlier Apply left half made (a live thread of that name the
    Hub created, with no thread under it yet), `("new", None)` when the name
    is free, `("taken", record)` when a live thread that is not a work stream
    holds it, and `("archived", record)` / `("closed", record)` when only
    archived or closed threads do.

    cr1#1342: every thread of that name is read, not only the first one the
    book lists (`find_existing_thread`), so an archived namesake never hides a
    live one, and a name only archived threads hold is said to be archived.
    `events` is the press's one read of the log."""
    import thread_cards as tc
    import thread_writer as tw

    cards, _from_log = tc.read_cards(root)
    streams = cards.get("work_streams") or {}
    key = tw._norm(name)
    for sid, card in streams.items():
        if tw._norm(card.get("name")) == key:
            return "existing", sid
    every = [t for t in tw._threads(tw._load_entities(root)) if isinstance(t, dict)]
    namesakes = [t for t in every if tw._norm(t.get("canonical_name") or t.get("display_name")) == key]
    if not namesakes:
        return "new", None
    for hit in namesakes:
        if str(hit.get("id") or "") in streams:
            return "existing", str(hit["id"])
    if events is None:
        from events_io import load_events_owner_scoped

        events = load_events_owner_scoped(str(root))[0]
    hub_made = {str((ev.get("data") or {}).get("primary_thread_id") or "") for ev in events
                if isinstance(ev, dict) and ev.get("type") == "thread_created"
                and ev.get("source_skill") == SOURCE_SKILL}
    parents = {str(t.get("parent_thread_id") or "") for t in every}

    def status(t):
        return str(t.get("status") or "active").lower()

    for hit in namesakes:
        hid = str(hit.get("id") or "")
        live = status(hit) not in ("archived", "resolved", "dormant")
        # A thread under another (the stream's own first thread) is never a
        # half-made stream (review of part two).
        if live and hid in hub_made and hid not in parents and not hit.get("parent_thread_id"):
            return "resume", hid
    for hit in namesakes:
        if status(hit) not in tc.DONE_STATUSES:
            return "taken", hit
    archived = next((t for t in namesakes if status(t) == "archived"), None)
    return ("archived", archived) if archived is not None else ("closed", namesakes[0])


def _writer_create(root: Path, **kwargs) -> dict:
    """`thread_writer.create_thread`, finishing its own half write (cr1#1342).
    The writer saves the record, then appends its `thread_created` event; when
    that append fails the record is on the book with nothing saying who made
    it, and no later press may claim it. So a failure is read back at once,
    and the record counts as this call's only when all of these hold: the
    writer refused nothing (a `ValueError` is its dedup or validation,
    raised before any save), the book moved by exactly one save since the
    call began and that save was this skill's (`version`, `last_writer`),
    exactly one record of this name and parent is new, and no
    `thread_created` names it yet (review of cr1#1342: a concurrent press or
    another writer's record is never stamped as the Hub's). Then its missing
    event is appended through the writer's own `_log_event`, once. Every
    other failure is raised: `_HalfMadeStream` when this call's record stays
    without its event, `_StreamUnsure` when the book cannot say."""
    import thread_writer as tw
    from events_io import load_events_owner_scoped

    skill = kwargs.get("source_skill") or SOURCE_SKILL
    key = tw._norm(kwargs.get("canonical_name"))
    book = tw._load_entities(root)
    try:
        version = int(book.get("version", 0))  # as `_save_entities` counts it
    except (TypeError, ValueError):
        version = None
    before = {str(t.get("id") or "") for t in tw._threads(book) if isinstance(t, dict)}
    try:
        return tw.create_thread(root, **kwargs)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - read back; this call's own record is finished, else unsure
        first = exc
    try:
        now_book = tw._load_entities(root)
        after = [t for t in tw._threads(now_book) if isinstance(t, dict)]
        evs = load_events_owner_scoped(str(root))[0]
    except Exception:  # noqa: BLE001 - the book cannot be read back
        raise _StreamUnsure() from first
    mine = [t for t in after if str(t.get("id") or "") not in before
            and tw._norm(t.get("canonical_name") or t.get("display_name")) == key
            and str(t.get("parent_thread_id") or "") == str(kwargs.get("parent_thread_id") or "")]
    if not mine:
        raise first  # nothing of this call's landed: the writer's own error stands
    one_save = (isinstance(version, int) and now_book.get("version") == version + 1
                and now_book.get("last_writer") == skill)
    tid = str(mine[0].get("id") or "")
    logged = any(isinstance(ev, dict) and ev.get("type") == "thread_created"
                 and tid in (str((ev.get("data") or {}).get("primary_thread_id") or ""),
                             str(ev.get("primary_thread_id") or ""))
                 for ev in evs)
    if len(mine) != 1 or not one_save or logged:
        raise _StreamUnsure() from first
    try:
        tw._log_event(root, "thread_created", mine[0], skill)
    except Exception as exc:  # noqa: BLE001 - the record stays; say so
        raise _HalfMadeStream(tid) from exc
    return mine[0]


class _HalfMadeStream(Exception):
    """cr1#1342: a stream record on the book whose `thread_created` could not
    be written; it carries the record's id."""


class _StreamUnsure(Exception):
    """cr1#1342: the writer failed and the book cannot show whether part of
    the stream landed, so nothing is claimed and nothing is said to be
    unwritten."""


def _own_org(root: Path) -> str:
    """The org a new work stream belongs to: the owner's own company when the
    book holds exactly one (`relationship_type: self`), else the writer's
    unaffiliated sentinel."""
    import thread_writer as tw
    from entities_io import entities_collection

    try:
        orgs = entities_collection(tw._load_entities(root), "orgs")
    except (OSError, ValueError):
        orgs = []
    own = [o for o in orgs or [] if isinstance(o, dict) and o.get("id")
           and str(o.get("relationship_type") or "").strip().lower() == "self"]
    return str(own[0]["id"]) if len(own) == 1 else tw.UNAFFILIATED_ORG_ID


def _create_stream(root: Path, name: str, parent_id: Optional[str] = None,
                   folder_out: Optional[dict] = None) -> str:
    """A work stream through the one thread writer: the parent with the
    owner's name (unless an earlier Apply already wrote it) and one thread
    under it, because a work stream is a parent thread. Answers its id.

    cr1#1582: both Hub adds (No home yet's New work stream and the Work
    tab's bar, `hub_edit._stream_add`) come through here, so the stream's
    folder is made in the same act (`_stream_folder`); its result lands in
    `folder_out` when the caller passes one. No folder is guessed from the
    name (`guess_folder=False`): `project_folder` decides it."""
    import thread_writer as tw

    org = _own_org(root)
    if not parent_id:
        parent_id = _writer_create(root, canonical_name=name, status="active", org_id=org,
                                   source_skill=SOURCE_SKILL, guess_folder=False)["id"]
    elif any(isinstance(t, dict) and t.get("parent_thread_id") == parent_id
             for t in tw._threads(tw._load_entities(root))):
        if folder_out is not None:
            folder_out.update(_stream_folder(root, parent_id))
        return parent_id  # another Apply finished it just now
    try:
        _writer_create(root, canonical_name=NEW_STREAM_CHILD.format(name=name), status="active",
                       org_id=org, parent_thread_id=parent_id, source_skill=SOURCE_SKILL,
                       skip_dedup=True, guess_folder=False)
    except _HalfMadeStream:
        raise _HalfMadeStream(parent_id) from None
    got = _stream_folder(root, parent_id)
    if folder_out is not None:
        folder_out.update(got)
    return parent_id


def _stream_folder_refusal(root: Path, name: str) -> Optional[str]:
    """cr1#1582: the line that refuses a new work stream before anything is
    written, when its folder cannot be made as asked (`project_folder.
    folder_check`): a name no folder can hold, two folders of that name one
    level down (the owner picks in a chat), or a folder another live project
    records. None when the stream may be made."""
    import project_folder

    got = project_folder.folder_check(root, name)
    if got.get("ok"):
        return None
    reason = got.get("reason")
    if reason == "bad_name":
        return NEW_STREAM_FOLDER_NAME_LINE.format(name=name, why=project_folder.name_problem(name) or "")
    if reason == "ambiguous":
        return NEW_STREAM_FOLDER_PICK_LINE.format(name=name, where=", ".join(got.get("folders") or []))
    if reason == "taken":
        return NEW_STREAM_FOLDER_TAKEN_LINE.format(folder=got.get("folder_name"), other=got.get("holder"))
    return str(got.get("line") or NEW_STREAM_FAILED_LINE)


def _stream_folder(root: Path, sid: str) -> dict:
    """cr1#1582: the new work stream's folder, in the same press, through
    `project_folder.make_project_folder` (the one place a project folder is
    made): made at the workspace root or matched, and recorded on the stream
    and on its own first thread. Never raises: a folder that could not be
    made leaves the stream folderless, and the line says so."""
    import project_folder
    import thread_writer as tw

    kids = [str(t.get("id")) for t in tw._threads(tw._load_entities(root))
            if isinstance(t, dict) and str(t.get("parent_thread_id") or "") == sid]
    try:
        # its own first thread shares the folder, so it is not a project
        # with no folder; the parent alone renders the folder's brain
        # (`render_thread_live_state`, cr1#1582 review)
        return project_folder.make_project_folder(root, sid, source_skill=SOURCE_SKILL,
                                                  with_project=True, also_thread_ids=kids)
    except Exception as exc:  # noqa: BLE001 - the stream stands without a folder
        return {"ok": False, "outcome": "refused", "reason": type(exc).__name__}


def _track_heard(root: Path, cid: str) -> tuple:
    """A line only heard, tracked through the queue's own confirm
    (`needs_review_queue.confirm_items`, the path the owner's yes takes):
    the observed tier's promotion, then the review flags cleared. The Hub
    press on this one row is that yes, so the row is named on its own.
    Answers `(promoted id, None)` or `("", (handler, outcome, line))`."""
    from needs_review_queue import confirm_items

    # `confirm_weak_ids` names this one row: the bulk guard holds rows a
    # range or "all" reached, and a press on one row is that row named.
    res = confirm_items(str(root), [cid], source_skill=COMMITMENT_SRC, confirm_weak_ids=[cid])
    one = ((res or {}).get("results") or [{}])[0]
    pid = str(one.get("promoted_id") or "")
    if one.get("status") in ("confirmed", "not_pending") and pid:
        return pid, None
    return "", ({"status": "failed", "error": "not_tracked", "detail": str(one.get("detail") or "")[:200]},
                "error", ASSIGN_HEARD_FAILED_LINE)


def _home_press(workspace_root, cid: str, verb: str, value: Any, ledger_open: set, now,
                label: str, choice_id: str, *, heard: bool = False) -> tuple:
    """cr1#1289 part two: Assign to and New work stream, on a promise or on
    a line only heard. Everything the book can refuse is read before
    anything is written; then a heard line is tracked (`_track_heard`), a
    new stream is created (`_create_stream`), and the home is written by
    `_assign_home`, the one place a home is written. Answers
    `(handler, outcome, line)`."""
    import thread_cards as tc
    from events_io import load_events_owner_scoped

    root = Path(str(workspace_root))
    # cr1#1342: the log is read once for the press and passed down; it is
    # read again only after a heard line is tracked, whose capture row is new.
    evs = load_events_owner_scoped(str(root))[0]
    done = _home_written(root, choice_id, evs)
    if done is not None:
        return {"status": "already_written", "seq": done.get("seq")}, "already", "Already given that home."
    target, how, name = "", "", ""
    if verb == "newstream":
        name = _stream_name(value)
        if not name:
            return {"status": "refused", "error": "no_name"}, "refused", NEW_STREAM_NAME_LINE
        how, found = _stream_for_name(root, name, evs)
        if how == "new":
            refused = _stream_folder_refusal(root, name)
            if refused:
                return ({"status": "refused", "error": "folder_name"}, "refused", refused)
        if how in ("taken", "archived", "closed"):
            rec = found or {}
            if how == "taken":
                line = NEW_STREAM_TAKEN_LINE.format(name=name)
            else:
                import thread_writer as tw

                rid = str(rec.get("id") or "")
                was_stream = any(isinstance(t, dict) and str(t.get("parent_thread_id") or "") == rid
                                 for t in tw._threads(tw._load_entities(root)))
                what = "a work stream" if was_stream else "a thread"
                held = str(rec.get("canonical_name") or rec.get("display_name") or name)
                line = (NEW_STREAM_ARCHIVED_LINE if how == "archived" else NEW_STREAM_CLOSED_LINE).format(
                    name=held, what=what)
            return ({"status": "refused", "error": "name_taken" if how == "taken" else f"name_{how}",
                     "thread_id": rec.get("id")}, "refused", line)
        target = found or ""
    else:
        target = str(value or "").strip()
        streams = (tc.read_cards(root)[0].get("work_streams") or {})
        if not target or target not in streams:
            return {"status": "refused", "error": "not_a_work_stream"}, "refused", ASSIGN_NO_STREAM_LINE
    tracked = ""
    if heard:
        tracked, err = _track_heard(root, cid)
        if err:
            return err
        cid = tracked
        ledger_open.add(cid)
        evs = load_events_owner_scoped(str(root))[0]
    answered = None
    half = ""
    folder = None
    if how in ("new", "resume"):
        answered = _no_home_state(root, cid, ledger_open, now)
        if answered is None:
            try:
                folder = {}
                target = _create_stream(root, name, target if how == "resume" else None,
                                        folder_out=folder)
                # The stream's writes take a moment: the home's Apply-twice
                # check reads the log as it stands now, so a second page
                # pressing the same choice meanwhile is seen (review of cr1#1342).
                evs = load_events_owner_scoped(str(root))[0]
            except ValueError as exc:
                answered = ({"status": "error", "error": "not_created", "detail": str(exc)[:200]}, "error",
                            NEW_STREAM_FAILED_LINE)
            except _HalfMadeStream as exc:
                half = NEW_STREAM_HALF_LINE.format(name=name)
                answered = ({"status": "error", "error": "half_made", "thread_id": str(exc)}, "error", half)
            except _StreamUnsure:
                half = NEW_STREAM_UNSURE_LINE.format(name=name)
                answered = ({"status": "error", "error": "stream_unsure"}, "error", half)
    if answered is None:
        made = {"created_stream": target} if how in ("new", "resume") else None
        answered = _assign_home(root, cid, target, ledger_open, now, label, choice_id, extra=made, events=evs)
    handler, outcome, line = answered
    handler = dict(handler or {})
    if tracked:
        handler["tracked_id"] = tracked
    if folder:
        handler["folder_name"] = folder.get("folder_name")
        handler["folder_outcome"] = folder.get("outcome")
    if how in ("new", "resume") and outcome == "applied":
        handler["created_stream"] = target
        line = f"Started the work stream {name} and moved this there."
        if folder and not folder.get("ok"):
            line += NEW_STREAM_NO_FOLDER_LINE
    elif how == "existing" and outcome == "applied":
        line = line.rstrip(".") + ", the work stream you already have."
    if tracked and outcome == "applied":
        line = "Tracked. " + line
    elif tracked:
        # The tracking landed and stays (promotion is permanent), so the
        # line never claims nothing was written.
        line = HEARD_TRACKED_ONLY_LINE + (" " + half if half else "")
    return handler, outcome, line


def _home_events(evs: list) -> list:
    """The Hub's own home reclassifications (`open_list.HOME_ORIGIN`), in
    log order."""
    import open_list as ol

    out = []
    for ev in evs:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "reclassification" and d.get("origin") == ol.HOME_ORIGIN:
            out.append(ev)
    return out


def _made_stream_free(root: Path, sid: str, press_id: str, evs: list) -> tuple:
    """cr1#1341: whether the stream a press created is still only that
    press's, so its Undo may archive it. Read only. Answers `(verdict,
    child ids)`: `free` when the stream and its one thread were both made on
    the Hub and nothing else on the book names either (no other item's home,
    no capture, no note, no thread under it, no change from elsewhere);
    `archived` when it already is; `gone` when it is not on the book; else
    `kept`. Every doubt answers `kept`: a stream is never archived on a
    guess."""
    import open_list as ol
    import thread_writer as tw

    data = tw._load_entities(root)
    recs = [t for t in tw._threads(data) if isinstance(t, dict)]
    rec = next((t for t in recs if str(t.get("id") or "") == sid), None)
    if rec is None:
        return "gone", []
    if str(rec.get("status") or "").strip().lower() == "archived":
        return "archived", []
    kids = [t for t in recs if str(t.get("parent_thread_id") or "") == sid]
    ours = set()
    for ev in evs:
        if isinstance(ev, dict) and ev.get("type") == "thread_created" and ev.get("source_skill") == SOURCE_SKILL:
            ours.add(str((ev.get("data") or {}).get("primary_thread_id") or ""))
    if sid not in ours or rec.get("parent_thread_id") or len(kids) > 1 \
            or any(str(k.get("id") or "") not in ours for k in kids):
        return "kept", []
    ids = {sid} | {str(k.get("id")) for k in kids}
    quoted = [json.dumps(i) for i in ids]
    for ev in evs:
        if not isinstance(ev, dict):
            continue
        typ, src = ev.get("type"), ev.get("source_skill")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if typ == "apply_choices_applied":
            continue  # the Hub's own receipts: never work
        if typ in ("thread_created", "thread_updated", "status_change") and src == SOURCE_SKILL:
            continue  # the Hub's own create and archive of these threads
        if typ in ("project_folder_made", "project_folder_taken_back") and src == SOURCE_SKILL:
            continue  # the Hub's own folder for this stream (cr1#1582)
        if typ == "reclassification" and d.get("origin") == ol.HOME_ORIGIN and press_id \
                and press_id in (d.get("hub_choice"), d.get("undoes_choice")):
            continue  # this press and its Undo
        blob = json.dumps(ev, ensure_ascii=False, default=str)
        if any(q in blob for q in quoted):
            return "kept", []
    # The rest of the book, either shape, without the two records themselves
    # (review of cr1#1341: a flat book kept its own record in the scan).
    import copy
    from entities_io import entities_collection

    others = copy.deepcopy(data)
    coll = entities_collection(others, "threads")
    coll[:] = [t for t in coll if not (isinstance(t, dict) and str(t.get("id") or "") in ids)]
    rest = json.dumps(others, ensure_ascii=False, default=str)
    if any(q in rest for q in quoted):
        return "kept", []
    return "free", [str(k.get("id")) for k in kids]


def _undo_home(workspace_root, cid_in: str, press_id: Any, undo_id: str, ledger_open: set,
               label: str) -> tuple:
    """cr1#1341: Undo after Apply of Assign to or New work stream, an
    `unassign` choice naming the press it takes back. Through the existing
    paths only: the item goes back to no home by a later `hub_assign`
    reclassification of the same capture row (the newest home wins in
    `open_list.homes_given`); a stream the press created, and its one
    thread, are archived by `thread_archive.archive_thread`, the one archive
    path, only while nothing else is on them (`_made_stream_free`). A heard
    line the press tracked stays tracked. An Undo already written writes
    nothing again, and a press the item has moved on from is left alone.
    Answers `(handler, outcome, line)`."""
    from event_gate import append_event
    from events_io import load_events_owner_scoped

    root = Path(str(workspace_root))
    press_id = str(press_id or "").strip()
    if not press_id:
        return {"status": "refused", "error": "no_press"}, "refused", UNASSIGN_NO_PRESS_LINE
    evs = load_events_owner_scoped(str(root))[0]
    homes = _home_events(evs)
    press = next((e for e in homes if (e.get("data") or {}).get("hub_choice") == press_id
                  and not (e.get("data") or {}).get("undoes_choice")), None)
    pd = (press or {}).get("data") or {}
    cid = str(pd.get("commitment_id") or "")
    target = str((press or {}).get("primary_thread_id") or "")
    promoted_from = ""
    for ev in evs:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "commitment" and cid and str(d.get("id") or "") == cid:
            promoted_from = str(d.get("promoted_from") or "")
            break
    if press is None or not target or not cid or cid_in not in (cid, promoted_from):
        return {"status": "refused", "error": "no_press"}, "refused", UNASSIGN_NO_PRESS_LINE
    mine = next((e for e in homes if undo_id and (e.get("data") or {}).get("hub_choice") == undo_id), None)
    if mine is None and cid not in ledger_open:
        return {"status": "already_resolved"}, "already", ALREADY_CLOSED_LINE
    if mine is None:
        newest = max((e for e in homes if (e.get("data") or {}).get("commitment_id") == cid),
                     key=lambda e: e.get("seq") if isinstance(e.get("seq"), int) else -1)
        if newest is not press:
            if (newest.get("data") or {}).get("undoes_choice") != press_id:
                return {"status": "moved_since"}, "already", UNASSIGN_MOVED_LINE
            # Undone already: only a stream left standing by a failed
            # archive is tried again below (review of cr1#1341).
            mine = newest
    seq = None
    if mine is None and pd.get("reason") == REHOME_REASON:
        # ruling 86: a move's Undo puts it back only on a project still live; never out of sight
        import thread_cards as tc

        cards = tc.read_cards(root)[0]
        if str(pd.get("old_primary_thread_id") or "") not in {**(cards.get("threads") or {}), **(cards.get("work_streams") or {})}:
            return {"status": "refused", "error": "old_home_gone"}, "refused", UNMOVE_GONE_LINE
        # #1700's round-2 note (b): putting it back is a move too, judged the same way
        stop = _move_refusal(root, cid, str(pd.get("old_primary_thread_id") or ""), {}, target)
        if stop:
            return stop
    if mine is None:
        old_related = list(pd.get("old_related_thread_ids") or [])
        old_primary = str(pd.get("old_primary_thread_id") or "")
        data = {"origin": pd.get("origin"), "commitment_id": cid, "target_thread_id": "",
                "old_primary_thread_id": target, "new_primary_thread_id": old_primary or None,
                "old_related_thread_ids": list(press.get("related_thread_ids") or []),
                "new_related_thread_ids": old_related, "reason": UNASSIGN_REASON,
                "cockpit_batch": label, "hub_choice": undo_id, "undoes_choice": press_id}
        restoring = {"type": "reclassification", "source_skill": COMMITMENT_SRC,
                     "supersedes_seq": press.get("supersedes_seq"), "related_thread_ids": old_related,
                     "classification_confidence": 1.0, "data": data}
        # The capture row had no thread: the key is left out, never stamped
        # null (the binding backfill's restoring idiom).
        if old_primary:
            restoring["primary_thread_id"] = old_primary
        landed = append_event(root / "_hq" / "data" / "events.jsonl", restoring, holder="cockpit_helpers")
        seq = (landed[0] if isinstance(landed, list) and landed else {}).get("seq")
        evs = load_events_owner_scoped(str(root))[0]
    handler: Dict[str, Any] = {"status": "unassigned" if mine is None else "already_written",
                               "commitment_id": cid, "thread_id": target}
    if seq is not None:
        handler["seq"] = seq
    tracked = UNASSIGN_TRACKED_LINE if promoted_from else ""
    # cr1#1658: the Undo of a move puts it back on the project it was on
    line = (UNMOVE_LINE if pd.get("reason") == REHOME_REASON else UNASSIGN_LINE) + tracked
    name = target
    if pd.get("created_stream") == target:
        import thread_archive as ta
        import thread_writer as tw

        for t in tw._threads(tw._load_entities(root)):
            if isinstance(t, dict) and str(t.get("id") or "") == target:
                name = str(t.get("canonical_name") or t.get("display_name") or target)
        verdict, kids = _made_stream_free(root, target, press_id, evs)
        handler["stream"] = verdict
        if verdict == "free":
            # The stream first, so the line is true whichever write fails:
            # until it lands nothing is archived and the stream stays; once
            # it lands the stream is off the tab.
            try:
                ta.archive_thread(str(root), target, reason=UNASSIGN_ARCHIVE_REASON, source_skill=SOURCE_SKILL)
            except Exception as exc:  # noqa: BLE001 - the item is back already; the stream stays, and says so
                handler.update(stream="archive_failed", error=type(exc).__name__)
                # An error, so the page offers the Undo again; that retry
                # writes no second home and tries the archive once more.
                return handler, "error", UNASSIGN_ARCHIVE_FAILED_LINE.format(name=name) + tracked
            handler["stream"] = "archived_now"
            line = UNASSIGN_ARCHIVED_LINE.format(name=name) + tracked
            # cr1#1582: the folder this press made goes with it: removed when
            # empty, moved whole to the archive when anything is in it; a
            # folder that was already there stays.
            import project_folder

            try:
                back = project_folder.take_back_folder(root, target, source_skill=SOURCE_SKILL)
            except Exception as exc:  # noqa: BLE001 - the stream is archived; the folder stays
                back = {"ok": False, "outcome": "error", "reason": type(exc).__name__}
            handler["folder_outcome"] = back.get("outcome")
            if back.get("outcome") == "archived":
                line += UNASSIGN_FOLDER_ARCHIVED_LINE
            elif not back.get("ok"):
                line += UNASSIGN_FOLDER_STAYS_LINE
            for tid in kids:
                try:
                    ta.archive_thread(str(root), tid, reason=UNASSIGN_ARCHIVE_REASON, source_skill=SOURCE_SKILL,
                                      regenerate_view=False)
                except Exception as exc:  # noqa: BLE001 - say which thread stays live
                    handler.update(child_archive_failed=tid, error=type(exc).__name__)
                    line = UNASSIGN_CHILD_FAILED_LINE.format(name=name) + tracked
        elif verdict == "kept":
            line = UNASSIGN_KEPT_LINE.format(name=name) + tracked
        elif verdict == "archived":
            line = UNASSIGN_ARCHIVED_LINE.format(name=name) + tracked
    if mine is not None and handler.get("stream") != "archived_now":
        return handler, "already", UNASSIGN_ALREADY_LINE
    return handler, "applied", line


def plan_batch(plan_id: Any) -> str:
    """cr1#1541: the batch every move of one plan shares, so one Put it back
    (`brain_undo.undo_batch`, the Record tab's own) takes the whole plan back
    and nothing else."""
    return PLAN_BATCH_PREFIX + "".join(ch for ch in str(plan_id or "")[:PLAN_ID_CAP]
                                       if ch.isalnum() or ch in "_-")


def _plan_gate(c: Dict[str, Any], verb: str, workspace_root=None, now: Optional[_dt.datetime] = None,
               cid: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[str, str, Dict[str, Any]]]]:
    """cr1#1541: the fence on a planned move, asked before anything is
    written. A row that names no plan and carries no planned time answers
    `(None, None)` and is applied as it always was. A planned row must be a
    move to a day (`snooze` with a date), from no pile, naming a plan id this
    can read and a time on the quarter hour with a length the Week offers;
    then it answers `({id, at, minutes}, None)`. Anything else is refused
    and nothing is written for it.

    Review round 1, asked again here whatever the page saw (a stale page, a
    second tab): the planned day and time, on the workspace's own clock,
    must still be ahead; and the item must not carry a date of its own later
    than the planned day (a plan never moves a later deadline earlier), unless
    that date was set by this same plan (a block of it dragged again)."""
    has = any(k in c for k in ("plan", "at", "mins"))
    if not has:
        return None, None
    refused = ("refused", PLAN_UNREADABLE_LINE, {"status": "refused", "error": "plan_unreadable"})
    pid = c.get("plan")
    day = str(c.get("input") or "")
    if (verb != "snooze" or c.get("pile") or not isinstance(pid, str) or not _PLAN_ID_RE.match(pid)
            or not re.match(r"^\d{4}-\d{2}-\d{2}$", day)):
        return None, refused
    import commitment_state as cs

    try:
        f = cs.plan_fields({"at": c.get("at"), "minutes": c.get("mins")})
    except ValueError:
        return None, refused
    if workspace_root is not None and now is not None:
        try:
            hh, mm = (int(x) for x in f["plan_at"].split(":"))
            at = _dt.datetime.combine(_dt.date.fromisoformat(day), _dt.time(hh, mm), _workspace_tz(workspace_root))
        except ValueError:
            return None, refused
        if at <= now:
            return None, ("refused", PLAN_PAST_LINE, {"status": "refused", "error": "plan_past"})
        try:
            due, batch = _standing_due(workspace_root, cid)
        except Exception:  # noqa: BLE001 - a date that cannot be read is no yes (fail closed)
            return None, refused
        if due and due > day and batch != plan_batch(pid):
            title = " ".join(str(c.get("title") or "That item").split())[:80] or "That item"
            return None, ("refused", PLAN_LATER_DUE_LINE.format(title=title, due=due, day=day),
                          {"status": "refused", "error": "plan_later_due", "due": due})
    return {"id": pid, "at": f["plan_at"], "minutes": f["plan_minutes"]}, None


def _standing_due(workspace_root, cid: str) -> Tuple[str, str]:
    """cr1#1541: an item's date as the open list projects it now
    (`commitment_state.effective_due`, the one projection), and the batch of
    the newest move that set it ("" when its date came from its capture).
    `("", "")` when it has none. Raises when the book cannot be read."""
    import commitment_state as cs
    from events_io import load_events_owner_scoped

    ep = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
    due = str(cs.effective_due(ep, cid) or "")[:10]
    batch = ""
    for ev in load_events_owner_scoped(str(workspace_root))[0]:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if (ev.get("type") == "commitment_updated" and str(d.get("commitment_id") or "") == cid
                and ("new_due" in d or d.get("due_cleared"))):
            batch = str(d.get("brain_batch_id") or d.get("plan_batch_id") or "")
    return due, batch


def _pile_gate(workspace_root, c: Dict[str, Any], cid: str, verb: str, now: _dt.datetime,
               memo: Dict[str, Any], row_kind: str = "commitment") -> Tuple[str, Optional[Tuple[str, str, Dict[str, Any]]]]:
    """cr1#1540: the fence on a pile's row, asked before anything is written.
    Answers `(pile kind, refusal)`: the kind when the row may be written,
    else `("", (outcome, line, handler))`. A row from no pile answers
    `("", None)` and is applied as it always was, but for `park`, which only
    a pile presses.

    Refused, writing nothing: a row whose pile is not one the Work tab has, or
    whose kind or verb is not that pile's; a pile that cannot be checked now
    (the wall, the mail rules, the owner or the open list unread); an item
    the pile's own rule does not hold now (closed, confirmed since, parked,
    moved, dated by the owner, walled, off limits, a close question the
    review fence would hold); a move to a day already past.

    `memo` is the batch's: `sets` is the piles as the book stands, read again
    after every write this batch lands (the caller drops it), so a row is
    judged against what the rows before it did; `done` is every item this
    batch already wrote for a pile, so no item is written twice in one."""
    import hub_work

    named = c.get("pile")
    if not named and verb != "park":
        return "", None
    kind = hub_work.pile_kind(named)
    if not kind or hub_work.PILE_VERBS[kind] != verb or hub_work.PILE_ROW_KINDS[kind] != row_kind:
        return "", ("refused", PILE_VERB_LINE, {"status": "refused", "error": "pile_verb"})
    if "sets" not in memo:
        memo["sets"] = hub_work.pile_sets(workspace_root, _iso(now))
    sets = memo["sets"]
    if not sets.get("ok"):
        return "", ("refused", PILE_UNREADABLE_LINE, {"status": "refused", "error": "pile_unreadable"})
    done = memo.setdefault("done", set())
    if cid not in sets["piles"].get(kind, set()) or (kind, cid) in done:
        return "", ("refused", PILE_LEFT_LINE, {"status": "refused", "error": "pile_left"})
    if verb == "snooze":
        import commitment_state as cs

        when = cs.parse_later_when(str(c.get("input") or "")[:80], _iso(now), workspace_path=str(workspace_root))
        day = cs._now_calendar_day(_iso(now), workspace_path=str(workspace_root))
        # Review of #1663 (item 1): a pile's move goes to a day after today. A
        # move to today, from a feed read on an earlier day, would leave the
        # items late again tomorrow, so it is refused like a day gone by.
        if not when or not day or str(when)[:10] <= day.isoformat():
            return "", ("refused", PILE_DAY_LINE, {"status": "refused", "error": "pile_day"})
    done.add((kind, cid))
    return kind, None


def _standing_close(workspace_root, cid: str) -> Optional[Dict[str, Any]]:
    """cr1#1540: the close that stands on an item now, `{seq, source_skill}`:
    its newest `commitment_resolved` with no reopen after it. None when the
    item is open."""
    from events_io import load_events_owner_scoped

    last: Optional[Dict[str, Any]] = None
    for ev in load_events_owner_scoped(str(workspace_root))[0]:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if str(d.get("commitment_id") or "") != cid:
            continue
        if ev.get("type") == "commitment_resolved":
            last = {"seq": ev.get("seq"), "source_skill": str(ev.get("source_skill") or "")}
        elif ev.get("type") == "commitment_reopened":
            last = None
    return last


def _back_is_for(standing: Optional[Dict[str, Any]], c: Dict[str, Any]) -> bool:
    """cr1#1540: whether an Undo row of a pile's item (`back_of`) is for the
    write that stands now. With the write's seq on the row (`back_seq`, the
    seq the Apply run answered for the pile's row) it must be that very
    write. A row with no seq (a result written before seqs were kept) is
    taken only for a write this surface made, never for one made elsewhere."""
    if standing is None:
        return False
    if isinstance(c.get("back_seq"), int):
        return standing.get("seq") == c.get("back_seq")
    return standing.get("source_skill") == COMMITMENT_SRC


def _owner_unparks(workspace_root) -> Dict[str, _dt.datetime]:
    """cr1#1540: the time of each item's latest un-park from the Hub (the
    time it was pressed, else written), so a park pressed before it is stale
    (the #1462 guard, for `park`). Never raises."""
    out: Dict[str, _dt.datetime] = {}
    try:
        from events_io import load_events_owner_scoped

        for ev in load_events_owner_scoped(str(workspace_root))[0]:
            d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
            if ev.get("type") != "commitment_updated" or not d.get("unparked") or not d.get("cockpit_batch"):
                continue
            cid = str(d.get("commitment_id") or "")
            at = _parse(d.get("pressed_at")) or _parse(ev.get("ts"))
            if cid and at is not None and (cid not in out or at > out[cid]):
                out[cid] = at
    except Exception:  # noqa: BLE001 - the check only tightens
        return {}
    return out


def _hub_parked(workspace_root, cid: str) -> Optional[Dict[str, Any]]:
    """cr1#1540: the item's standing park, `{hub, seq}` (`hub`: a Hub pile
    wrote it, `PARK_REASON`), or None when it is not parked at all. Un-park
    from the Hub reaches only its own parks."""
    from events_io import load_events_owner_scoped

    last: Optional[Dict[str, Any]] = None
    for ev in load_events_owner_scoped(str(workspace_root))[0]:
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "commitment_updated" and str(d.get("commitment_id") or "") == cid \
                and ("status_hint" in d or d.get("unparked")):
            last = dict(d, _seq=ev.get("seq"))
    if last is None or last.get("status_hint") != "parked":
        return None
    return {"hub": str(last.get("park_reason") or "") == PARK_REASON, "seq": last.get("_seq")}


def _wait_typed(workspace_root, cid, c, actor, now, label) -> Optional[Dict[str, Any]]:
    """cr1#1291: the Snooze box's words when they set or clear a wait on an
    event rather than give a date (`commitment_wait.apply_wait_text`), under
    the batch's own undo handle (cr1#1322)."""
    from commitment_wait import apply_wait_text
    return apply_wait_text(str(workspace_root), cid, str(c.get("input") or "")[:200],
                           actor_id=actor, source_skill=COMMITMENT_SRC, now_iso=_iso(now),
                           brain_batch_id=label)


#: cr1#1608: the verbs whose plain close waits for the run's one write.
ONE_WRITE_VERBS = ("done", "confirm", "drop", "notwork")


def apply_commitment_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                             *, resolved_by: str = "", now_iso=None,
                             batch_id: Optional[str] = None,
                             run_id: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. The page's commitment choices, each through the owning
    writer, then ONE `apply_choices_applied` audit row for the batch.

    `run_id` (cr1#1377, option b) is the Apply run's own id: a raw page
    document another live run has claimed is refused as `waiting` and
    nothing is written for it (`_commitment_row`).

    The verbs:

      * `done`: `close_commitment(resolution="done", user_confirmed=True,
        resolved_by_match="id")`, the one closure path; a CONFIRM row still
        pending review goes through `needs_review_queue.done_items` (the
        DONE1 twin: confirm and close in one tap, never a bare close of a
        guess);
      * `confirm`: the same as `done` (the page's Confirm sits on CONFIRM
        rows);
      * `context`: `orphan_note.reroute_orphan_note(source_event_seq=<the
        commitment's seq>)`, the writer apply-choices uses for a note;
      * `snooze`: `parse_later_when` then `apply_later`, the writer that
        routes by ownership (the owner's item moves its date; one owed to
        them is hidden until then);
      * `reopen`: `reopen_commitment`, the page's answer to undo;
      * `assign` (cr1#1289): "Assign to" on a "No home yet" row. The input
        names a work stream; the book's own cards must hold it as a live
        work stream, and the promise must be open and on no thread. One
        `reclassification` of the promise's capture row, marked
        `open_list.HOME_ORIGIN`, which the open list honours; the capture
        row is never edited;
      * `notwork` (cr1#1289): "Not work" on a "No home yet" row. A
        confirmed promise closes as dropped through `close_commitment`; one
        still waiting for a yes, or a line only heard, through
        `needs_review_queue.drop_items`, the queue's own drop. Reopen
        undoes either;
      * `unassign` (cr1#1341): Undo after Apply of Assign to or New work
        stream, naming that press (`_undo_home`): the item goes back to no
        home by a later `hub_assign` reclassification, and a stream the press
        created is archived through the one archive path while nothing else
        is on it;
      * `park` (cr1#1540): a pile's press on an item owed to the owner that
        went quiet: `commitment_state.park_commitments`, the park the silence
        door itself uses, as the owner's own act and with no batch, so no
        surface reads it as a park the product made. The item stays open;
      * `unpark` (cr1#1540): that park's Undo, through
        `commitment_state.unpark_commitments`. It reaches only a park the Hub
        wrote.

    A row that names a `pile` (cr1#1540, the Work tab's Triage) is held to
    `_pile_gate` first: its verb must be the pile's and the pile's own rule
    must hold the item as the book stands when the row is reached (the
    piles are read again after every write of the batch). Then it is
    written exactly as the same press on the item's own row is written: a
    close through `close_commitment` with nothing added, so its Undo is the
    row's own `reopen`. Two differences: a pile's moves share that press's
    batch (`hub_work.pile_batch`) in place of the run's, so one Put it back
    (`brain_undo.undo_batch`, the Record tab's own) takes back that press's
    moves and no other; and a move covers a copy folded under the line only
    when the pile's rule holds that copy too. The row's answer carries the
    `seq` of what it wrote. An Undo row of a pile's item (`back_of`, with
    that `seq` as `back_seq`) reopens or un-parks that write and no other:
    an item closed or parked again since is left as it is.

    Context lands before Done on the same row, so a note and a close pressed
    together both land. A refusal of one row never stops the rest. Answers
    `{ok, results: [{id, commitment_id, action, outcome, line, seq?}],
    n_applied, n_errors, batch_id}`."""
    # cr1#1608: one read of the log serves every reader of this run (the
    # plate, the ids, the reopens, the closure index, the gate), refreshed
    # whenever a write grows the log (`events_io.OneLogRead`).
    from events_io import OneLogRead

    with OneLogRead():
        return _apply_commitment_choices_run(workspace_root, choices, resolved_by=resolved_by,
                                             now_iso=now_iso, batch_id=batch_id, run_id=run_id)


def _apply_commitment_choices_run(workspace_root, choices, *, resolved_by, now_iso,
                                  batch_id, run_id) -> Dict[str, Any]:
    """`apply_commitment_choices`' work, inside its one log read."""
    actor = str(resolved_by or "").strip() or _owner_id(workspace_root)
    rows = [c for c in (choices or []) if isinstance(c, dict)]
    label = batch_id or f"cockpit:{_iso(_now(now_iso)).replace(':', '')}"
    if not actor:
        return {"ok": False, "reason": "no_actor", "line": NO_ACTOR_LINE,
                "results": [], "n_applied": 0, "n_errors": len(rows), "batch_id": label}
    import commitment_state as cs

    now = _now(now_iso)
    # cr1#1377: a raw page document (its item named in `n`, as a scheduled run
    # on a smaller model hands it over without `plan_apply`) is read with
    # plan_apply's own mapping; a row that names no item, or is not a
    # commitment's, is refused here and nothing is written to any item for it.
    results: List[Dict[str, Any]] = []
    mapped: List[Dict[str, Any]] = []
    seen_raw = set()
    for c in rows:
        row, refusal = _commitment_row(c, now, run_id or "")
        if refusal is not None:
            results.append(refusal)
        elif row is not c:
            # A raw document passed twice is applied once, as plan_apply
            # drops a repeated id (a second answer would overwrite the first).
            did = str(row.get("id") or "").strip()
            if did not in seen_raw:
                seen_raw.add(did)
                mapped.append(row)
        else:
            mapped.append(row)
    rows = mapped
    seqs = _commitment_seqs(workspace_root)
    reopens = _owner_reopens(workspace_root)  # cr1#1462
    open_rows = _open_rows(workspace_root, _iso(now))
    if open_rows is None:
        return {"ok": False, "reason": "plate_unreadable", "line": PLATE_UNREADABLE_LINE,
                "results": [], "n_applied": 0, "n_errors": len(rows) + len(results), "batch_id": label}
    # Open or closed comes from the book itself (cr1#679): a row the plate
    # leaves out is not thereby closed.
    ledger_open = set()
    try:
        from cru_match import load_open_commitments

        ep = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
        for ev in (load_open_commitments(ep) if ep.exists() else []):
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if d.get("id"):
                ledger_open.add(str(d["id"]))
    except Exception:  # noqa: BLE001 - the plate alone decides then
        ledger_open = set()
    # A note and a snooze land first; a close and a reopen land in the order
    # they were pressed, against the state the batch has written so far.
    order = {"context": 0, "snooze": 1}
    counted_now: Optional[set] = None  # the overdue verdict, read once if a snooze needs it
    pile_memo: Dict[str, Any] = {}  # cr1#1540: the piles as the book stands, and what this batch wrote
    from hub_work import pile_batch as _pile_batch
    _unparked: Dict[str, Any] = {}

    def _unparks() -> Dict[str, _dt.datetime]:
        if "at" not in _unparked:
            _unparked["at"] = _owner_unparks(workspace_root)
        return _unparked["at"]
    # cr1#1377: each refusal above is on the batch's one audit row, with the
    # page's word for it (cr1#1378), so a batch of broken presses is named on
    # the Hub; an expired or already-applied document is a refusal on purpose.
    audit: List[Dict[str, Any]] = [
        {"n": r["commitment_id"], "action": r["action"],
         "handler_result": ({"status": "already_resolved"} if r["reason"] == "already_applied"
                            else {"status": "error", "error": r["reason"]}),
         "said": {"expired": "refused", "already_applied": "already",
                  "claimed_elsewhere": "waiting"}.get(r["reason"], "error"),
         "note": f"cockpit {label}"} for r in results]
    # cr1#1608: the plain closes of a run land in ONE locked write through the
    # one closure path (`commitment_state.close_commitments_one_write`), not
    # in one lock span and two whole-log reads each. A close waits here and is
    # written before anything else is written (another verb, a pile's row, the
    # review queue's own writers) and at the end, so the book gets the rows N
    # single closes write, in the same order.
    pending_closes: List[Dict[str, Any]] = []

    def _flush_closes() -> None:
        if not pending_closes:
            return
        batch = list(pending_closes)
        pending_closes.clear()
        try:
            answers = cs.close_commitments_one_write(
                str(workspace_root), [p["closure"] for p in batch], source_skill=COMMITMENT_SRC)
        except Exception:  # noqa: BLE001 - the lock or the write failed, wholly or in part
            # cr1#1608 review: never one failure reported against every row. Each
            # row goes through the single close instead, whose in-lock re-check
            # answers `already` for a row the failed write did land, and a row
            # that fails again fails alone.
            answers = []
            for p_ in batch:
                cl = dict(p_["closure"])
                try:
                    answers.append(cs.close_commitment(str(workspace_root), cl.pop("commitment_id"),
                                                       source_skill=COMMITMENT_SRC, **cl))
                except Exception as exc:  # noqa: BLE001 - this row alone
                    answers.append({"status": "error", "error": type(exc).__name__, "exception": exc})
        landed = False
        for p, ans in zip(batch, answers):
            r_, ans = p["r"], (ans or {})
            st = ans.get("status")
            exc = ans.get("exception")
            if st in ("not_open", "already_resolved"):
                r_.update(outcome="already", line=ALREADY_CLOSED_LINE)
                handler_ = {k: v for k, v in ans.items() if k != "exception"}
            elif st == "error" and isinstance(exc, cs.OpenSubitemsError):
                r_.update(outcome="error", line=SUBITEMS_LINE)
                handler_ = {"status": "error", "error": "open_subitems"}
            elif st == "error" and isinstance(exc, cs.CommitmentIdError):
                r_.update(outcome="error", line=GONE_LINE)
                handler_ = {"status": "error", "error": "unknown_id"}
            elif st == "error":
                r_.update(_row_error(exc if isinstance(exc, BaseException)
                                     else RuntimeError(str(ans.get("detail") or ""))))
                handler_ = {"status": "error", "error": str(ans.get("error") or "error")}
            else:
                r_.update(outcome="applied", line=p["line"], seq=(ans.get("event") or {}).get("seq"))
                open_rows.pop(p["cid"], None)
                ledger_open.discard(p["cid"])
                handler_ = ans
                landed = True
            p["audit"]["handler_result"] = handler_
            p["audit"]["said"] = r_.get("outcome")
        if landed:
            # the next pile row is judged against the book as these writes left it
            pile_memo.pop("sets", None)

    for c in sorted(rows, key=lambda c: (order.get(str(c.get("action") or ""), 2),
                                         str(c.get("created_at") or ""))):
        cid = str(c.get("commitment_id") or "").strip()
        verb = str(c.get("action") or "").strip().lower()
        deferred: Optional[Dict[str, Any]] = None
        if pending_closes and (verb not in ONE_WRITE_VERBS or c.get("pile")):
            _flush_closes()
        r: Dict[str, Any] = {"id": c.get("id"), "commitment_id": cid, "action": verb}
        handler: Any = None
        piled = ""
        held: Optional[Tuple[str, str, Dict[str, Any]]] = None
        try:
            if verb in COMMITMENT_VERBS:
                # cr1#1540: a pile's row is checked against the pile's own rule
                # before anything is written; any other row passes untouched.
                piled, held = _pile_gate(workspace_root, c, cid, verb, now, pile_memo)
            planned_time: Optional[Dict[str, Any]] = None
            if verb in COMMITMENT_VERBS and held is None:
                # cr1#1541: a planned move is a snooze with a time of day and
                # its plan's own batch; anything else that names a plan is refused.
                planned_time, held = _plan_gate(c, verb, workspace_root, now, cid)
            if verb not in COMMITMENT_VERBS:
                r.update(outcome="error", line=UNKNOWN_VERB_LINE.format(verb=verb or "none"))
                # cr1#1171: an empty handler result is audited ok
                handler = {"status": "error", "error": "unknown_verb", "verb": verb}
            elif held is not None:
                r.update(outcome=held[0], line=held[1], reason=held[2].get("error") or held[2].get("status"))
                handler = held[2]
            elif verb == "notwork" and cid not in seqs and cid.startswith("obs_"):
                # cr1#1289: a heard line has no capture row of its own. Its drop
                # (the queue's own writer) lands after the closes pressed before
                # it, as N single closes would (cr1#1608 review).
                _flush_closes()
                handler, outcome, line = _not_work_queued(workspace_root, cid, actor)
                r.update(outcome=outcome, line=line)
            elif verb == "unassign":
                # cr1#1341: Undo after Apply of a home press; the input names
                # that press, and a heard line's press names its obs id.
                handler, outcome, line = _undo_home(workspace_root, cid, c.get("input"), str(c.get("id") or ""),
                                                    ledger_open, label)
                r.update(outcome=outcome, line=line)
                if handler.get("seq") is not None:
                    r["seq"] = handler["seq"]
            elif verb == "rehome":
                # cr1#1658 change 6: the same home a No home yet Assign writes,
                # for an item that already has one
                handler, outcome, line = _assign_home(workspace_root, cid, str(c.get("input") or "").strip(),
                                                      ledger_open, now, label, str(c.get("id") or ""), move=True)
                r.update(outcome=outcome, line=line)
                if handler.get("seq") is not None:
                    r["seq"] = handler["seq"]
            elif verb in ("assign", "newstream") and cid not in seqs and cid.startswith("obs_"):
                # cr1#1289 part two: a heard line is tracked through the
                # queue's own confirm, then given its home.
                handler, outcome, line = _home_press(workspace_root, cid, verb, c.get("input"), ledger_open,
                                                     now, label, str(c.get("id") or ""), heard=True)
                r.update(outcome=outcome, line=line)
                if handler.get("seq") is not None:
                    r["seq"] = handler["seq"]
            elif (verb == "park" and _parse(c.get("created_at")) is not None
                  and cid in _unparks() and _parse(c.get("created_at")) < _unparks()[cid]):
                # cr1#1540: the same guard for a park pressed before the
                # owner's latest un-park of the item.
                r.update(outcome="refused", reason="pressed_before_unpark", line=PRESSED_BEFORE_UNPARK_LINE)
                handler = {"status": "refused", "error": "pressed_before_unpark"}
            elif (verb in _CLOSING_VERBS + ("park",) and cid in reopens
                  and _parse(c.get("created_at")) is not None
                  and _parse(c.get("created_at")) < reopens[cid]):
                # cr1#1462: a press made before the owner's latest reopen is
                # stale (a copy a run read before it): it never closes the
                # item again. Nothing is written to the item.
                r.update(outcome="refused", reason="pressed_before_reopen",
                         line=PRESSED_BEFORE_REOPEN_LINE)
                handler = {"status": "refused", "error": "pressed_before_reopen"}
            elif cid not in seqs:
                r.update(outcome="error", line=GONE_LINE)
                # cr1#1171: an error naming the id the book does not hold
                handler = {"status": "error", "error": "unknown_id", "commitment_id": cid}
            elif verb in ("done", "confirm"):
                row = open_rows.get(cid)
                if row is None and cid in ledger_open:
                    row = {"id": cid, "pending": verb == "confirm"}
                if row is None:
                    r.update(outcome="already", line=ALREADY_CLOSED_LINE)
                    handler = {"status": "already_resolved"}
                elif row.get("pending") and piled:
                    # cr1#1540: a pile closes confirmed items only.
                    r.update(outcome="refused", line=PILE_LEFT_LINE, reason="pile_left")
                    handler = {"status": "refused", "error": "pile_left"}
                elif row.get("pending"):
                    from needs_review_queue import done_items

                    _flush_closes()
                    res = done_items(str(workspace_root), [cid], resolved_by=actor,
                                     source_skill="apply-choices", attested_ids=[cid],
                                     now_iso=_iso(now))
                    done = (res or {}).get("n_done")
                    # cr1#1161: the audit reads this row's own answer (`done`,
                    # or `not_pending` when another page got there first). The
                    # batch dict has no status, which the audit counts as an error.
                    handler = ((res or {}).get("results") or [{"status": "not_pending"}])[0]
                    r.update(outcome="applied" if done else "already",
                             line="Confirmed and closed." if done else ALREADY_CLOSED_LINE)
                    if done:
                        open_rows.pop(cid, None)
                        ledger_open.discard(cid)
                elif not piled:
                    deferred = {"cid": cid, "line": "Closed.", "closure": {
                        "commitment_id": cid, "resolved_by": actor,
                        "evidence": CONFIRM_EVIDENCE if verb == "confirm" else DONE_EVIDENCE,
                        "resolution": "done", "user_confirmed": True, "resolved_by_match": "id",
                        "extra_data": dict({"cockpit_batch": label}, **_close_note(c))}}
                else:
                    handler = cs.close_commitment(
                        str(workspace_root), cid, resolved_by=actor,
                        evidence=CONFIRM_EVIDENCE if verb == "confirm" else DONE_EVIDENCE,
                        source_skill=COMMITMENT_SRC, resolution="done",
                        user_confirmed=True, resolved_by_match="id",
                        extra_data=dict({"cockpit_batch": label}, **_close_note(c)))
                    st = (handler or {}).get("status")
                    if st in ("not_open", "already_resolved"):
                        r.update(outcome="already", line=ALREADY_CLOSED_LINE)
                    else:
                        r.update(outcome="applied", line="Closed.",
                                 seq=((handler or {}).get("event") or {}).get("seq"))
                        open_rows.pop(cid, None)
                        ledger_open.discard(cid)
            elif verb == "drop":
                # cr1#713: the same one closure path as Done, recorded as
                # dropped, never as delivered. A row still pending review is
                # not the owner's commitment yet; Drop is not offered on it.
                row = open_rows.get(cid)
                if row is None and cid in ledger_open:
                    row = {"id": cid}
                if row is None:
                    r.update(outcome="already", line=ALREADY_CLOSED_LINE)
                    handler = {"status": "already_resolved"}
                elif row.get("pending"):
                    r.update(outcome="error", line=DROP_PENDING_LINE)
                    handler = {"status": "needs_confirm"}
                elif not piled:
                    deferred = {"cid": cid, "line": "Dropped.", "closure": {
                        "commitment_id": cid, "resolved_by": actor, "evidence": DROP_EVIDENCE,
                        "resolution": "dropped", "user_confirmed": True, "resolved_by_match": "id",
                        "extra_data": {"cockpit_batch": label}}}
                else:
                    handler = cs.close_commitment(
                        str(workspace_root), cid, resolved_by=actor,
                        evidence=DROP_EVIDENCE, source_skill=COMMITMENT_SRC,
                        resolution="dropped", user_confirmed=True,
                        resolved_by_match="id", extra_data={"cockpit_batch": label})
                    st = (handler or {}).get("status")
                    if st in ("not_open", "already_resolved"):
                        r.update(outcome="already", line=ALREADY_CLOSED_LINE)
                    else:
                        r.update(outcome="applied", line="Dropped.",
                                 seq=((handler or {}).get("event") or {}).get("seq"))
                        open_rows.pop(cid, None)
                        ledger_open.discard(cid)
            elif verb == "notwork":
                row = open_rows.get(cid)
                if row is None and cid in ledger_open:
                    row = {"id": cid}
                if row is None:
                    r.update(outcome="already", line=ALREADY_CLOSED_LINE)
                    handler = {"status": "already_resolved"}
                elif row.get("pending"):
                    _flush_closes()
                    handler, outcome, line = _not_work_queued(workspace_root, cid, actor)
                    r.update(outcome=outcome, line=line)
                elif not piled:
                    deferred = {"cid": cid, "line": NOT_WORK_LINE, "closure": {
                        "commitment_id": cid, "resolved_by": actor, "evidence": NOT_WORK_EVIDENCE,
                        "resolution": "dropped", "user_confirmed": True, "resolved_by_match": "id",
                        "extra_data": {"cockpit_batch": label}}}
                else:
                    handler = cs.close_commitment(
                        str(workspace_root), cid, resolved_by=actor,
                        evidence=NOT_WORK_EVIDENCE, source_skill=COMMITMENT_SRC,
                        resolution="dropped", user_confirmed=True,
                        resolved_by_match="id", extra_data={"cockpit_batch": label})
                    st = (handler or {}).get("status")
                    if st in ("not_open", "already_resolved"):
                        r.update(outcome="already", line=ALREADY_CLOSED_LINE)
                    else:
                        r.update(outcome="applied", line=NOT_WORK_LINE,
                                 seq=((handler or {}).get("event") or {}).get("seq"))
                if r.get("outcome") == "applied":
                    open_rows.pop(cid, None)
                    ledger_open.discard(cid)
            elif verb in ("assign", "newstream"):
                handler, outcome, line = _home_press(workspace_root, cid, verb, c.get("input"), ledger_open,
                                                     now, label, str(c.get("id") or ""))
                r.update(outcome=outcome, line=line)
                if handler.get("seq") is not None:
                    r["seq"] = handler["seq"]
            elif verb == "context":
                text = str(c.get("input") or "").strip()[:NOTE_CAP]
                if not text:
                    r.update(outcome="error", line=EMPTY_NOTE_LINE)
                    # cr1#1171 review: an empty handler result is audited ok
                    handler = {"status": "needs_input", "error": "empty_note",
                               "commitment_id": cid}
                else:
                    from orphan_note import reroute_orphan_note

                    handler = reroute_orphan_note(str(workspace_root), text,
                                                  source_event_seq=seqs[cid],
                                                  source_skill=COMMITMENT_SRC)
                    st = (handler or {}).get("status")
                    if st in ("noted", "already_noted"):
                        r.update(outcome="applied",
                                 line=f"Note saved on {handler.get('target_kind')} {handler.get('target_id')}.")
                    else:
                        r.update(outcome="error", line=(handler or {}).get("line") or NO_HOME_LINE)
            elif verb == "snooze":
                when = cs.parse_later_when(str(c.get("input") or "")[:80], _iso(now),
                                           workspace_path=str(workspace_root))
                # cr1#1291: "waiting on <event>" holds it with no date.
                waited = None if when else _wait_typed(workspace_root, cid, c, actor, now, label)
                if waited:
                    handler = waited
                    r.update(outcome="applied" if waited.get("status") in ("updated", "cleared")
                             else "already" if waited.get("status") == "unchanged" else "error",
                             line=str(waited.get("ack") or ""))
                elif not when:
                    # cr1#1292: a date typed but unreadable is asked about.
                    typed = str(c.get("input") or "").strip()
                    r.update(outcome="error",
                             line=cs.later_unreadable_ask(typed) if typed else SNOOZE_NEEDS_DATE_LINE)
                    handler = {"status": "missing_when"}
                else:
                    # Owner ruling 2 of 2026-11-12 (cr1#1012), scope (b): a
                    # snooze on a line covers the copies folded under it that
                    # the overdue number counts, through the one shared
                    # writer every surface that pushes a line calls
                    # (`commitment_state.apply_later_covering_folded`). The
                    # Hub hands it the fold it already holds and the
                    # verdict, read once per batch.
                    folded = list((open_rows.get(cid) or {}).get("folded_ids") or [])
                    if piled:
                        # cr1#1540: a pile moves only what its rule holds. A copy
                        # folded under the line moves with it only when the
                        # pile's own rule holds that copy too.
                        _in = (pile_memo.get("sets") or {}).get("piles", {}).get(piled, set())
                        _done = pile_memo.setdefault("done", set())
                        folded = [f for f in folded if str(f) in _in and (piled, str(f)) not in _done]
                        _done.update((piled, str(f)) for f in folded)
                    if folded and counted_now is None:
                        try:
                            counted_now = _counted_ids(workspace_root, now)
                        except Exception:  # noqa: BLE001 - no verdict: cover no copy
                            counted_now = set()
                    full = cs.apply_later_covering_folded(
                        str(workspace_root), cid, when_iso=when, actor_id=actor,
                        source_skill=COMMITMENT_SRC, reason=SNOOZE_REASON,
                        surface=SOURCE_SKILL, now_iso=_iso(now),
                        # cr1#1540: a pile's moves share the pile's own batch;
                        # cr1#1541: a plan's moves share the plan's own
                        brain_batch_id=(plan_batch(planned_time["id"]) if planned_time
                                        else _pile_batch(c.get("pile")) if piled else label),
                        folded_ids=folded, counted_ids=counted_now or set(),
                        plan=({"at": planned_time["at"], "minutes": planned_time["minutes"]}
                              if planned_time else None))
                    handler = {k: v for k, v in (full or {}).items() if k not in ("covered", "copies")}
                    st = (handler or {}).get("status")
                    if st in ("deferred", "snoozed"):
                        r.update(outcome="applied",
                                 line=str((handler or {}).get("ack") or f"Moved to {when}."))
                        # cr1#1657: an item the owner does not owe is hidden
                        # until that day with its date kept: the page names
                        # the day in Changes ("Hidden until Tue").
                        if st == "snoozed":
                            r["check_back"] = str(when)[:10]
                        # Each copy carries its own audit entry and its own undo.
                        for copy in full.get("copies") or []:
                            audit.append({"n": copy["id"], "action": "snooze", "handler_result": copy["result"],
                                          "note": f"cockpit {label}, folded under {cid}"})
                        covered = list(full.get("covered") or [])
                        r["covered"] = covered
                        if covered:
                            r["line"] = r["line"] + " " + cs.folded_covered_line(covered)
                    else:
                        r.update(outcome="error", line=str((handler or {}).get("ack") or NOT_OPEN_LINE))
            elif verb == "park":
                # cr1#1540: the silence door's own park, as the owner's act
                # (the gate above let it through). No batch: a park that
                # carries one reads as the product's own everywhere else.
                res = cs.park_commitments(
                    str(workspace_root),
                    [{"commitment_id": cid, "reason": PARK_REASON,
                      "extra_data": {"cockpit_batch": label, "user_confirmed": True,
                                     cs.REASON_ORIGIN_KEY: cs.REASON_ORIGIN_PRODUCT}}],
                    parked_by=actor, source_skill=COMMITMENT_SRC)
                handler = {k: v for k, v in ((res or [{}])[0]).items() if k != "event"}
                st = handler.get("status")
                if st == "parked":
                    r.update(outcome="applied", line=PARK_EVIDENCE_LINE,
                             seq=(((res or [{}])[0]).get("event") or {}).get("seq"))
                    # the audit's own words: a write that landed (the writer's word kept beside it)
                    handler = {**handler, "status": "updated", "writer_status": st}
                elif st in ("already_parked", "not_open"):
                    r.update(outcome="already", line=ALREADY_CLOSED_LINE if st == "not_open"
                             else "That one is already parked; nothing was written.")
                    handler = {**handler, "status": "already_resolved", "writer_status": st}
                else:
                    r.update(outcome="error", line=NOT_OPEN_LINE)
            elif verb == "unpark":
                # cr1#1540: the Undo of a pile's park, and of nothing else.
                mine = _hub_parked(workspace_root, cid)
                if mine is None:
                    r.update(outcome="already", line=UNPARK_ALREADY_LINE)
                    handler = {"status": "already_open"}
                elif not mine["hub"]:
                    r.update(outcome="refused", line=UNPARK_NOT_HUB_LINE, reason="not_hub_park")
                    handler = {"status": "refused", "error": "not_hub_park"}
                elif isinstance(c.get("back_seq"), int) and mine["seq"] != c.get("back_seq"):
                    # parked again since the park this Undo is for
                    r.update(outcome="already", line=BACK_MOVED_LINE, reason="back_moved")
                    handler = {"status": "already_resolved"}
                else:
                    _pressed = str(c.get("created_at") or "").strip()
                    res = cs.unpark_commitments(str(workspace_root), [cid], unparked_by=actor, source_skill=COMMITMENT_SRC,
                                                reason=UNPARK_REASON, keep_wait=True,
                                                extra_data={"cockpit_batch": label, "user_confirmed": True,
                                                            **({"pressed_at": _pressed} if _parse(_pressed) else {})})
                    handler = {k: v for k, v in ((res or [{}])[0]).items() if k != "event"}
                    st = handler.get("status")
                    if st == "unparked":
                        r.update(outcome="applied", line=UNPARK_LINE,
                                 seq=(((res or [{}])[0]).get("event") or {}).get("seq"))
                        handler = {**handler, "status": "updated", "writer_status": st}
                    elif st in ("already_open", "not_open"):
                        r.update(outcome="already", line=UNPARK_ALREADY_LINE)
                        handler = {**handler, "status": "already_resolved", "writer_status": st}
                    else:
                        r.update(outcome="error", line=str(handler.get("ack") or NOT_OPEN_LINE))
            elif verb == "reopen":
                if cid in open_rows or cid in ledger_open:
                    r.update(outcome="already", line="That one is already open.")
                    handler = {"status": "already_open"}
                elif c.get("back_of") and not _back_is_for(_standing_close(workspace_root, cid), c):
                    # cr1#1540: the Undo of a pile's close reopens that close
                    # and no other: an item closed again since stays closed.
                    r.update(outcome="already", line=BACK_MOVED_LINE, reason="back_moved")
                    handler = {"status": "already_resolved"}
                else:
                    # cr1#1462 review: the reopen keeps the time the owner
                    # pressed it, so a later press is judged against the
                    # press, on the page's own clock, never the write.
                    _pressed = str(c.get("created_at") or "").strip()
                    handler = cs.reopen_commitment(str(workspace_root), cid, reopened_by=actor,
                                                   reason=REOPEN_REASON, source_skill=COMMITMENT_SRC,
                                                   extra_data=({"pressed_at": _pressed} if _parse(_pressed) else None))
                    st = (handler or {}).get("status")
                    if st in ("reopened",):
                        r.update(outcome="applied", line="Reopened.",
                                 seq=((handler or {}).get("event") or {}).get("seq"))
                        open_rows[cid] = {"id": cid}
                        ledger_open.add(cid)
                    else:
                        r.update(outcome="error", line=NOT_OPEN_LINE)
        except cs.OpenSubitemsError:
            r.update(outcome="error", line=SUBITEMS_LINE)
            handler = {"status": "error", "error": "open_subitems"}
        except cs.CommitmentIdError:
            r.update(outcome="error", line=GONE_LINE)
            handler = {"status": "error", "error": "unknown_id"}
        except Exception as exc:  # noqa: BLE001 - one row's failure never stops the batch
            r.update(_row_error(exc))
            handler = {"status": "error", "error": type(exc).__name__}
        if r.get("outcome") == "applied":
            # cr1#1540: the next pile row is judged against the book as this
            # write left it, never against a read from before it.
            pile_memo.pop("sets", None)
            if piled and r.get("seq") is None:
                # the write this row made, so its Undo can name it
                try:
                    _mark = _hub_parked(workspace_root, cid) if verb == "park" else _standing_close(workspace_root, cid)
                    if _mark and _mark.get("seq") is not None:
                        r["seq"] = _mark["seq"]
                except Exception:  # noqa: BLE001 - the write landed; the Undo falls back to the surface check
                    pass
        results.append(r)
        # cr1#1378: the row's own answer goes with it, so the audit can never
        # say ok over a row this writer answered as an error.
        entry = {"n": cid, "action": verb, "handler_result": handler,
                 "said": r.get("outcome"), "note": f"cockpit {label}"}
        audit.append(entry)
        if deferred is not None:
            pending_closes.append({**deferred, "r": r, "audit": entry})
    _flush_closes()
    n_applied = sum(1 for r in results if r["outcome"] == "applied")
    n_errors = sum(1 for r in results if r["outcome"] == "error")
    if audit:
        try:
            from apply_audit import build_apply_choices_applied_event
            from event_gate import append_event

            ev = build_apply_choices_applied_event(source=COMMITMENT_SRC, actions=audit)
            ev["data"]["batch_id"] = label
            ev["data"]["surface"] = SOURCE_SKILL
            append_event(Path(workspace_root) / "_hq" / "data" / "events.jsonl", ev,
                         holder="cockpit_helpers")
        except Exception as exc:  # noqa: BLE001 - the closes landed; say the audit did not
            return {"ok": False, "reason": "audit_failed",
                    "line": "Your choices were written, but the record of this batch was not.",
                    "error": f"{type(exc).__name__}: {exc}"[:240],
                    "results": results, "n_applied": n_applied, "n_errors": n_errors,
                    "batch_id": label}
    return {"ok": True, "results": results, "n_applied": n_applied,
            "n_errors": n_errors, "batch_id": label}


# ---------------------------------------------------------------------------
# the run's one receipt
# ---------------------------------------------------------------------------

def log_run_receipt(workspace_root, *, fired_via: str = "scheduled",
                    legs: Optional[List[str]] = None,
                    counts: Optional[Dict[str, Any]] = None,
                    duration_ms: Optional[int] = None,
                    rerun_of: Optional[str] = None,
                    task_id: Optional[str] = None,
                    status: str = "complete", now_iso=None) -> Dict[str, Any]:
    """A WRITER. A cockpit run's one `pack_run` receipt under its task id
    (`cockpit-prep`, `cockpit-inbox`, `cockpit-apply`, `cockpit-refresh`; the
    one-task `cockpit` of #614 is still accepted): which leg ran and what it
    counted. The receipt is what the watchdog and the health check read; a
    run that writes none is a run that never happened. An unknown task id is
    refused by `log_receipt` itself."""
    from receipts import log_receipt

    tid = str(task_id or TASK_ID).strip()
    if tid != TASK_ID and tid not in COCKPIT_TASKS and tid not in RETIRED_TASKS:
        raise ValueError(f"log_run_receipt: not a cockpit task id: {tid!r}")
    extra = {"legs": [leg for leg in (legs or []) if leg in LEGS],
             "counts": dict(counts or {})}
    if rerun_of:
        extra["rerun_of"] = str(rerun_of)
    ev = log_receipt(workspace_root, tid, receipt_type="pack_run",
                     status=status, fired_via=fired_via, duration_ms=duration_ms,
                     extra_data=extra, now=now_iso)
    return {"ok": True, "receipt": ev}


# ---------------------------------------------------------------------------
# Apply in two calls (cr1#1608)
# ---------------------------------------------------------------------------
#
# A real Apply run on a long book took seven minutes for 18 closes: one locked
# write per item, about thirty tool calls built one at a time in the chat, and
# six steps the run had to improvise. Two calls now carry the whole leg:
# `apply_start` claims what waits (or, with nothing waiting, records the run and
# stops), and `apply_run` plans and writes every choice this run claimed and
# hands back the page's writes, ready to send as they are. Every id, version,
# plan and time comes from code; the run passes the answers through.

#: The light's line when nothing waits.
APPLY_NOTHING_LINE = "Nothing waiting."
#: A choice this run claimed that no writer answered for.
APPLY_NO_RESULT_LINE = "This press was not written; press it again on the page."
#: The prefix of the run id `apply_start` mints for an Apply run.
APPLY_RUN_PREFIX = "apply-"
#: One ArtifactData `batch` carries at most this many writes.
APPLY_BATCH_MAX = CLAIM_BATCH_MAX
#: The feeds a kind's landed press refreshes (cockpit-apply-detail.md says how).
APPLY_FEEDS = {"hub": ("feed/mine",), "review": ("feed/review",),
               "settings": ("feed/settings", "feed/week"), "mail": ("feed/mail_rules",),
               "people": ("feed/people",),
               # review round 2 of #1742: a Still yours? press refreshes its own list
               "stillyours": ("feed/stillyours",),
               # cr1#1788: a Prep page edit refreshes the prep edits
               "prepedit": ("feed/prepx",),
               # cr1#1785: a Focus press refreshes Focus
               "focus": ("feed/focus",)}
#: The Hub's add and edit presses that also refresh their own tab (cr1#1431).
APPLY_EDIT_FEEDS = {"stream_edit": ("feed/projects", "feed/board"),
                    "priority_edit": ("feed/projects", "feed/board"),
                    "person_edit": ("feed/people",)}

# ---- the one Update (cr1#1722) ----
#
# Apply and Refresh were two tasks: the page fired Apply, waited for its light
# to turn green behind a claim, then fired Refresh, and each was a session of
# its own that read the whole log. The Update is one run: the save (Apply's two
# calls, unchanged, through the one-write path) and then the rebuild, in that
# order, so there is nothing to wait for. A run the page fired after a press
# rebuilds only the documents that press can change; the hourly run rebuilds
# everything.

#: Every document that shows the owner's work items: a close, a reopen, a move
#: or a new date on one item moves all of these. The Maintenance record rides
#: with them (review round 1 of #1768), and Today's to-do lists (`feed/mine`)
#: too: a to-do is a work item (fix check of round 1). So does Focus
#: (cr1#1785, review round 1 of #1797): a close, a new date or new words on an
#: item in Focus change what Focus shows.
_UPDATE_WORK = ("feed/plate", "feed/projects", "feed/board", "feed/people", "feed/review", "feed/week",
                "status/maintenance", "feed/mine", "feed/pfollow",
                # cr1#1786: Urgent's waiting-on-others rows are open items others owe
                "feed/urgent", "feed/focus", "feed/allopen")
_UPDATE_TODO = ("feed/mine", "feed/plate", "feed/projects", "feed/people", "status/maintenance", "feed/pfollow",
                "feed/urgent", "feed/focus", "feed/allopen")
#: Fix check of round 1 of #1768 (coordinator's decision): the narrow rebuild
#: is safe by construction. A landed press rebuilds only the documents named
#: here for its list and verb (`update_press_key`); any list, kind or verb not
#: here rebuilds everything. Every entry has a narrow-versus-whole comparison
#: in HUBUPDATE1 [9b], which fails for an entry that has none. Nothing that
#: changes who is off limits or who is known is here, in either direction:
#: the mail list (rules, Teach, Drop), a person's name or address, and adding
#: a person from To confirm all rebuild everything; so does work the connector
#: part finishes (Send, Draft, Missed something), in `update_save`.
UPDATE_NARROW: Dict[str, Tuple[str, ...]] = {
    **{"commitment:" + v: _UPDATE_WORK for v in ("done", "drop", "context", "snooze", "reopen", "assign",
                                                 "unassign", "newstream", "notwork", "park", "unpark")},
    **{"review:" + v: _UPDATE_WORK for v in ("confirm", "not relevant", "hold")},
    # a learned rule's question: Settings lists the rule once it is answered
    **{"review:learned_rule:" + v: _UPDATE_WORK + ("feed/settings",) for v in ("confirm proposal", "dismiss proposal")},
    # the Record tab's Put it back can undo a learned rule, which Settings lists
    "week:week:undo": _UPDATE_WORK + ("feed/settings",),
    "week:tidy:archive": _UPDATE_WORK + ("feed/settings",),
    **{"stillyours:" + v: _UPDATE_WORK + ("feed/stillyours",) for v in ("refresh", "keep", "done", "drop")},
    **{"settings:" + v: ("feed/settings", "feed/week") for v in ("rule edit", "rule off", "rule delete", "budget")},
    # a star moves the person on the People tab and the top line's ranking
    **{"people:" + v: ("feed/people", "feed/plate", "feed/pfollow", "feed/urgent") for v in ("star", "unstar")},  # cr1#1786: a starred sender
    # a to-do and a "waiting on them" are open items with no home yet: they are
    # on the plate, under No home yet on the work streams, and on that person
    "hub:todo:add": _UPDATE_TODO,
    "hub:waiting:add": _UPDATE_TODO,  # cr1#1786: _UPDATE_TODO already names feed/urgent (Urgent's waiting-on-others)
    # the Record tab's recap lists the open priorities
    **{"hub:priority_edit:" + v: ("feed/projects", "feed/board", "feed/plate", "feed/week")
       for v in ("add", "rename", "star", "order", "undo")},
    # a to-do names its work stream on Today's lists
    **{"hub:stream_edit:" + v: ("feed/projects", "feed/board", "feed/plate", "feed/mine") for v in ("update", "context")},
    # a note on a person changes nothing but their card
    "hub:person_edit:context": ("feed/people",),
    **{"hub:commitment_edit:" + v: _UPDATE_WORK for v in ("rename", "context")},
    # cr1#1788: the Prep page's edits are their own store and document
    **{"prepedit:" + v: ("feed/prepx",) for v in ("agenda", "goal", "add", "remove", "undo")},
    # what is attached to a meeting is carried on the Today lists' feed
    "hub:meeting_edit:attach": ("feed/mine",),
    # cr1#1785: Focus is its own document; an item's record is never written
    **{"focus:" + v: ("feed/focus",) for v in ("add", "remove", "order", "undo")},
}


def update_press_key(kind: str, doc: Optional[Dict[str, Any]] = None) -> str:
    """READ/COMPUTE. The one key a landed press is looked up by in
    `UPDATE_NARROW`: its list (`plan_apply`'s), then for the Hub's own presses
    and the Record tab the press's kind, for To confirm a learned rule's
    question, and its verb."""
    k = str(kind or "")
    d = doc if isinstance(doc, dict) else {}
    verb = str(d.get("action") or "")
    if k == "hub":
        return "hub:" + str(d.get("kind") or "") + ":" + verb
    if k == "week":
        return "week:" + (str(d.get("kind") or "") or "week") + ":" + verb
    if k == "review" and str(d.get("review_kind") or "") == "learned_rule":
        return "review:learned_rule:" + verb
    return k + ":" + verb


def update_press_space() -> List[str]:
    """READ/COMPUTE. Every list-and-verb key a press can land under, from the
    verb sets `plan_apply` itself reads, so a suite can hold every one of
    them to the table (fix check of round 1 of #1768)."""
    import hub_prep_edit
    import hub_focus
    import hub_tidy
    import still_yours

    keys = {"commitment:" + v for v in COMMITMENT_VERBS}
    keys |= {"focus:" + v for v in hub_focus.VERBS}
    keys |= {"people:" + v for v in PERSON_VERBS}
    keys |= {"prepedit:" + v for v in hub_prep_edit.VERBS}
    keys |= {"mail:" + v for v in MAILRULE_VERBS | INBOX_RULE_VERBS}
    keys |= {"review:" + v for v in REVIEW_VERBS} | {"review:learned_rule:" + v for v in REVIEW_VERBS}
    keys |= {"week:week:" + v for v in WEEK_VERBS} | {"week:tidy:" + v for v in hub_tidy.VERBS}
    keys |= {"stillyours:" + v for v in still_yours.VERBS}
    keys |= {"settings:" + v for v in SETTINGS_VERBS}
    hub = {"reminder": REMINDER_VERBS, "todo": TODO_VERBS, "waiting": WAITING_VERBS, "miss": MISS_VERBS,
           "meeting": MEETING_VERBS, **{k: _edit_verbs(k) for k in EDIT_KINDS}}
    keys |= {"hub:" + k + ":" + v for k, verbs in hub.items() for v in verbs}
    return sorted(keys)


#: After this long with no whole rebuild, a run the page started after a press
#: rebuilds everything all the same: when the hourly run takes over a light the
#: page set a moment before, it reads as a press's run, and the hour's whole
#: rebuild must not be skipped (review round 1 of #1768).
UPDATE_FULL_EVERY_MINUTES = 55
#: cr1#1809: when the last whole rebuild finished, kept on the page's own
#: `status/update` light (`full_at`), which every run reads and pins at its
#: start. It was a file in the session's temporary folder, which a cloud
#: seat's next run (a new session) never sees, so every press rebuilt
#: everything. Every writer of that light carries the field forward: the
#: page's presses, `update-start`'s claim and the run's own light. One that
#: drops it makes the next run whole, which is always right.
UPDATE_FULL_FIELD = "full_at"
#: A send, a draft or a meeting change the page made itself, now on the book.
#: A follow-up drafted or sent from a meeting's wrap-up (#1752) is a record
#: too, and its row takes the draft off the follow-ups (fix check of #1768).
#: cr1#1789: and off Today's People follow-ups too.
UPDATE_RECORD_FEEDS: Tuple[str, ...] = ("feed/people", "feed/week", "feed/followups", "feed/pfollow",
                                        # cr1#1786: a reply the page sent answers an Urgent row
                                        "feed/urgent")
#: The light's lines.
UPDATE_NOTHING_LINE = "Nothing to save."
UPDATE_SAVE_FAILED_LINE = "Couldn't save {n} of your {total} changes: {why}"
UPDATE_BUSY_LINE = "Another update is running now; this one stopped without changing anything."
#: Early review of #1776 (must 2): the busy stop behind an update that saves
#: only the page's latest presses (or names no scope): never a whole build.
UPDATE_BUSY_NARROW_LINE = ("Another update is running now, for your latest changes only; "
                           "this one stopped without changing anything.")


def touched_feeds(kind: str, doc: Optional[Dict[str, Any]] = None) -> Optional[Tuple[str, ...]]:
    """READ/COMPUTE. cr1#1722: the documents one landed press can change, from
    the one table (`UPDATE_NARROW`, by `update_press_key`). `kind` is the list
    `plan_apply` put the press in, `doc` the press as the page saved it. None
    when the table does not know the press: the rebuild is then the whole page,
    never a guess."""
    return UPDATE_NARROW.get(update_press_key(kind, doc))


def _apply_run_id(run_id: str = "") -> str:
    rid = str(run_id or "").strip()
    return rid if len(rid) >= 8 else APPLY_RUN_PREFIX + secrets.token_hex(6)


def _apply_counts(applied: int = 0, refused: int = 0, failed: int = 0,
                  recorded: int = 0) -> Dict[str, int]:
    return {"applied": int(applied), "refused": int(refused), "failed": int(failed),
            "recorded": int(recorded)}


def _chunks(writes: List[Dict[str, Any]], size: int = APPLY_BATCH_MAX) -> List[List[Dict[str, Any]]]:
    return [writes[i:i + size] for i in range(0, len(writes), size)]


def _pinned(doc_id: str, data: Dict[str, Any], version: Any, collection: str = "choices",
            op: str = "update") -> Dict[str, Any]:
    w: Dict[str, Any] = {"op": op, "collection": collection, "doc_id": doc_id, "data": data}
    if version is not None:
        w["if_version"] = version
    return w


def _apply_light(workspace_root, status: Optional[Dict[str, Any]], run_id: str, line: str,
                 failed: bool, now_s: str) -> Dict[str, Any]:
    """The `set` of `status/apply`, pinned to the light the run read, on this
    call's clock: `error` with its line as the stop reason, else `done`."""
    st = _flat_choice(status) if isinstance(status, dict) else None
    return _pinned("apply", status_doc(workspace_root, "apply", "error" if failed else "done", line,
                                       now_iso=now_s, run_id=run_id or None,
                                       stop_reason=line if failed else None),
                   (st or {}).get("_version") if st else None, collection="status", op="set")


def apply_start(workspace_root, waiting: Optional[List[Any]] = None,
                page: Optional[List[Any]] = None, status: Optional[Dict[str, Any]] = None, *,
                run_id: str = "", fired_via: str = "scheduled", now_iso=None,
                task: str = "apply") -> Dict[str, Any]:
    """A WRITER (its one write is the run's receipt, and only when nothing
    waits). The first of Apply's two calls (cr1#1608).

    cr1#1722: `task: "update"` is the Update's save. With nothing to claim it
    writes no receipt and no light (the Update's own end writes both) and
    answers `{ok, stop: false, nothing: true, writes: []}`: the run goes on
    to its rebuild.

    `waiting` is the `choices` query with `applied == false`, `page` the one
    with `by_page == true`, both as read. Mints the run's id (`run_id` given
    back keeps it, for a run that re-reads after a refused batch) and plans
    the claim of every waiting choice and every page send or meeting change
    with no book record, as ONE batch (`plan_claim`, `plan_page_records`).

    Nothing to claim and nothing this run already holds: the run's receipt is
    written here (a run that writes none reads as a run that never happened)
    and the answer is `{ok, stop: true, line: "Nothing waiting.", light}`:
    the run sets `light` (the done light, pinned to `status`, the
    `status/apply` read beside the queries; a page press set it running),
    says that line and stops, reading and writing nothing else. Otherwise
    `{ok, stop: false, run_id, writes, n_more, line}`: `writes` go out as ONE
    `batch`, exactly as they came back."""
    rid = _apply_run_id(run_id)
    now = _now(now_iso)
    claim = plan_claim(workspace_root, waiting, run_id=rid, now_iso=_iso(now))
    rec = plan_page_records(workspace_root, page, run_id=rid, now_iso=_iso(now))
    held = list(claim.get("held") or []) + list(rec.get("held") or [])
    writes = list(claim.get("writes") or [])
    room = max(0, APPLY_BATCH_MAX - len(writes))
    rec_writes = list(rec.get("writes") or [])
    writes += rec_writes[:room]
    n_more = int(claim.get("n_more") or 0) + int(rec.get("n_more") or 0) + max(0, len(rec_writes) - room)
    if not writes and not held and str(task or "") == "update":
        return {"ok": True, "stop": False, "nothing": True, "run_id": rid, "writes": [], "n_more": 0,
                "line": UPDATE_NOTHING_LINE}
    if not writes and not held:
        receipt = log_run_receipt(workspace_root, fired_via=fired_via, legs=["apply"],
                                  counts={"apply": _apply_counts()}, task_id="cockpit-apply",
                                  now_iso=_iso(now))
        return {"ok": True, "stop": True, "run_id": rid, "writes": [], "n_more": 0,
                "line": APPLY_NOTHING_LINE, "receipt_written": bool(receipt.get("ok")),
                "light": _apply_light(workspace_root, status, rid, APPLY_NOTHING_LINE, False, _iso(now))}
    return {"ok": True, "stop": False, "run_id": rid, "writes": writes, "n_more": n_more,
            "line": CLAIM_MORE_LINE.format(n=n_more) if n_more else ""}


def _apply_line(applied: int, refused: int, failed: List[str], recorded: int, more: str) -> str:
    line = f"Applied {applied}, refused {refused}"
    if failed:
        line += f", failed {len(failed)}"
    if recorded:
        line += f", recorded {recorded} from the page"
    line += f": {failed[0]}" if failed else "."
    return (line + (" " + more if more else "")).strip()


def apply_run(workspace_root, waiting: Optional[List[Any]] = None,
              page: Optional[List[Any]] = None, status: Optional[Dict[str, Any]] = None, *,
              run_id: str = "", fired_via: str = "scheduled", n_more: int = 0,
              now_iso=None, task: str = "apply") -> Dict[str, Any]:
    """A WRITER. The second of Apply's two calls (cr1#1608): everything the
    old leg did one step at a time, in one call under one read of the log.

    cr1#1722: `task: "update"` is the Update's save. It writes no light and
    no receipt (the Update's own end writes both), leaves no feed to the
    detail steps (`next` is `detail` only for a connector part), and answers
    `touched`: the documents the landed presses can change, from the one
    table (`touched_feeds`), or `all: true` when a press is not in it.

    `waiting` and `page` are the same two `choices` queries read again after
    the claim landed, `status` the `status/apply` document read beside them
    (or null), `run_id` the id `apply_start` answered, `n_more` its count.

    Plans the claimed choices (`plan_apply`, this run's own claims only) and
    the claimed page records (`plan_page_records`), then writes each list
    through its owning writer: commitments (their closes in one locked write,
    `apply_commitment_choices`), review, week, settings, mail rules, people,
    the Today page's own presses, the page's sends and drafts and its meeting
    changes. Answers `{ok, run_id, batches, light, line, counts, next,
    connectors, feeds, results}`:

      * `batches`: the page's writes, each a list to send as ONE `batch`, in
        order, exactly as they came back: every result on its choice
        (`result`, `applied`, `applied_at`) and every page record, pinned to
        the version read. A press another run holds (`waiting`) is not
        written, so it stays for the run that holds it;
      * `light`: the `set` of `status/apply`, sent on its own after the
        batches, so a light the page changed never holds back a result;
      * `next`: `done` (the run says `line` and stops) or `detail`: a mail
        send or draft (`connectors.inbox`), a "Missed something?" search
        (`connectors.miss`) or a feed to refresh (`feeds`) is left, by its
        choice id, for `cockpit-apply-detail.md`, which writes the light and
        the receipt at its end; nothing is written for those here;
      * the receipt is written here when `next` is `done`.

    No time is passed in by the run: every stamp is this call's own clock."""
    rid = str(run_id or "").strip()
    now = _now(now_iso)
    now_s = _iso(now)
    if len(rid) < 8:
        bad = "A claim needs this run's own id (8 characters or more)."
        return {"ok": False, "run_id": rid, "batches": [], "next": "done", "line": bad,
                "light": _apply_light(workspace_root, status, rid, bad, True, now_s),
                "counts": _apply_counts(), "connectors": {"inbox": [], "miss": []}, "feeds": [],
                "results": {}}
    docs: Dict[str, Dict[str, Any]] = {}
    for raw in list(waiting or []) + list(page or []):
        c = _flat_choice(raw)
        if c and str(c.get("id") or "").strip():
            docs[str(c["id"]).strip()] = c
    plan = plan_apply(workspace_root, waiting, now_iso=now_s, run_id=rid)
    recs = plan_page_records(workspace_root, page, run_id=rid, now_iso=now_s)
    held_recs = set(recs.get("held") or [])
    results: Dict[str, Dict[str, Any]] = {}
    for r in plan.get("refused") or []:
        if isinstance(r, dict) and r.get("id"):
            results[str(r["id"])] = {"outcome": "refused", "line": r.get("line") or ""}

    def _take(kind: str, rows: List[Dict[str, Any]], answer: Dict[str, Any]) -> None:
        got = {str(x.get("id")): x for x in (answer.get("results") or [])
               if isinstance(x, dict) and x.get("id") is not None}
        for row in rows:
            did = str(row.get("id") or "")
            if not did:
                continue
            if answer.get("ok") is False and did not in got:
                results[did] = {"outcome": "error", "line": str(answer.get("line") or ROW_ERROR_LINE)}
            elif did in got:
                results[did] = dict(got[did])
            else:
                results[did] = {"outcome": "error", "line": APPLY_NO_RESULT_LINE}
            results[did]["_kind"] = kind

    writers = (("commitment", apply_commitment_choices), ("review", apply_review_choices),
               ("week", apply_week_choices), ("settings", apply_settings_choices),
               ("mail", apply_mail_choices), ("people", apply_people_choices),
               ("stillyours", _apply_still_yours), ("prepedit", _apply_prepedit))
    hub_rows = [r for r in plan.get("hub") or [] if str(r.get("kind") or "") != "miss"]
    miss_ids = [str(r.get("id")) for r in plan.get("hub") or [] if str(r.get("kind") or "") == "miss"]
    inbox_ids = [str(r.get("id")) for r in plan.get("inbox") or [] if r.get("id")]
    rec_entries = [e for e in recs.get("entries") or [] if str(e.get("id")) in held_recs]
    cal_entries = [e for e in recs.get("calendar") or [] if str(e.get("id")) in held_recs]
    rec_results: Dict[str, Dict[str, Any]] = {}
    from events_io import OneLogRead

    with OneLogRead():
        for kind, fn in writers:
            rows = list(plan.get(kind) or [])
            if not rows:
                continue
            kw: Dict[str, Any] = {"resolved_by": "", "now_iso": now_s}
            if kind == "commitment":
                kw["run_id"] = rid
            try:
                ans = fn(workspace_root, rows, **kw)
            except Exception as exc:  # noqa: BLE001 - this list failed; the others still land
                ans = {"ok": False, "line": ROW_ERROR_LINE, "error": f"{type(exc).__name__}: {exc}"[:240]}
            _take(kind, rows, ans or {})
        if hub_rows:
            try:
                ans = apply_hub_choices(workspace_root, hub_rows, resolved_by="", now_iso=now_s)
            except Exception as exc:  # noqa: BLE001
                ans = {"ok": False, "line": ROW_ERROR_LINE, "error": f"{type(exc).__name__}: {exc}"[:240]}
            _take("hub", hub_rows, ans or {})
        # cr1#1785 page part: Focus after the Hub's presses, so "Add to my list"
        # lands the typed to-do first and its pick then names the new item
        focus_rows = [dict(r) for r in plan.get("focus") or []]
        for r in focus_rows:
            fc = str(r.get("from_choice") or "")
            if fc and not r.get("commitment_id"):
                r["commitment_id"] = str((results.get(fc) or {}).get("commitment_id") or "")[:80]
            if fc and not r.get("commitment_id"):
                # audit of #1823: the to-do landed in an earlier run (its pick was
                # written after that run claimed it); the book names it by its press
                try:
                    r["commitment_id"] = str(_todo_id(Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl",
                                                      f"{TODO_REF_SCHEME}:{fc}") or "")[:80]
                except Exception:  # noqa: BLE001 - unnamed: the store refuses the pick, as before
                    pass
        if focus_rows:
            try:
                ans = _apply_focus(workspace_root, focus_rows, resolved_by="", now_iso=now_s)
            except Exception as exc:  # noqa: BLE001
                ans = {"ok": False, "line": ROW_ERROR_LINE, "error": f"{type(exc).__name__}: {exc}"[:240]}
            _take("focus", focus_rows, ans or {})
        # review round 1 of #1823 (should-fix 4): "Add to my list" (and a row
        # dropped into To do) is ONE to-do press with `input.focus`; the to-do
        # it lands is put in Focus here, in the same run, under the press's own
        # id, so a replay is applied once and a reload can never split the two
        _picks = []
        for r in hub_rows:
            inp_ = r.get("input") if isinstance(r.get("input"), dict) else {}
            got_ = results.get(str(r.get("id") or "")) or {}
            if (r.get("kind") == "todo" and r.get("action") == "add" and inp_.get("focus") is True
                    and got_.get("outcome") in ("applied", "already") and got_.get("commitment_id")):
                _picks.append({"id": str(r["id"]) + ":focus", "action": "add", "title": str(r.get("title") or "")[:200],
                               "commitment_id": str(got_["commitment_id"])[:80],
                               "created_at": str((docs.get(str(r["id"])) or {}).get("created_at") or "")[:40],
                               "_of": str(r["id"])})
        if _picks:
            try:
                ans = _apply_focus(workspace_root, [{k: v for k, v in p_.items() if k != "_of"} for p_ in _picks],
                                   resolved_by="", now_iso=now_s) or {}
            except Exception as exc:  # noqa: BLE001
                ans = {"ok": False, "line": ROW_ERROR_LINE, "error": f"{type(exc).__name__}: {exc}"[:240]}
            got_f = {str(x.get("id")): x for x in ans.get("results") or [] if isinstance(x, dict)}
            for p_ in _picks:
                f_ = got_f.get(p_["id"]) or {}
                ok_ = f_.get("outcome") in ("applied", "already")
                results[p_["_of"]]["in_focus"] = ok_
                if not ok_:
                    # the to-do landed; say plainly it is not in Focus
                    results[p_["_of"]]["line"] = (str(results[p_["_of"]].get("line") or "") + " " + TODO_NOT_IN_FOCUS_LINE).strip()
        for entries, fn in ((rec_entries, record_page_sends), (cal_entries, record_calendar_changes)):
            if not entries:
                continue
            try:
                ans = fn(workspace_root, entries, now_iso=now_s) or {}
            except Exception as exc:  # noqa: BLE001
                ans = {"ok": False, "line": ROW_ERROR_LINE}
            got = {str(x.get("id")): x for x in (ans.get("results") or []) if isinstance(x, dict)}
            for e in entries:
                did = str(e.get("id"))
                x = got.get(did) or {"outcome": "error", "line": str(ans.get("line") or APPLY_NO_RESULT_LINE)}
                rec_results[did] = dict(x, _cal=fn is record_calendar_changes)

    writes: List[Dict[str, Any]] = []
    applied = refused = recorded = 0
    failed: List[str] = []
    feeds: List[str] = []
    touched: List[str] = []
    touch_all = False
    pkeys: List[str] = []
    for did, res in results.items():
        kind = res.pop("_kind", "")
        out = res.get("outcome")
        if out == "waiting":
            continue  # cr1#1377: another run holds this press; it stays waiting
        doc = docs.get(did) or {}
        writes.append(_pinned(did, {"result": res, "applied": True, "applied_at": now_s},
                              doc.get("_version")))
        if out == "error":
            failed.append(str(res.get("line") or ROW_ERROR_LINE))
        elif out == "refused":
            refused += 1
        else:  # applied, or already as pressed
            applied += 1
            pkeys.append(update_press_key(kind, doc))
            hit = touched_feeds(kind, doc)
            if hit is None:
                touch_all = True
            for f in hit or ():
                if f not in touched:
                    touched.append(f)
            for f in APPLY_FEEDS.get(kind, ()) + APPLY_EDIT_FEEDS.get(str(doc.get("kind") or ""), ()):
                if f not in feeds:
                    feeds.append(f)
            if kind == "review" and str(doc.get("review_kind") or "") == "learned_rule" and "feed/week" not in feeds:
                feeds.append("feed/week")
    for did, x in rec_results.items():
        doc = docs.get(did) or {}
        out = x.get("outcome")
        if out in ("applied", "already", "refused"):
            rec = ({"event_seq": x.get("seq")} if x.get("_cal")
                   else {"drafted_seq": x.get("drafted_seq"), "sent_seq": x.get("sent_seq")})
            writes.append(_pinned(did, {"book_recorded": True, "book_recorded_at": now_s, "book_record": rec,
                                        "book_record_line": str(x.get("line") or "")}, doc.get("_version")))
            recorded += 1
            if out in ("applied", "already"):
                for f in UPDATE_RECORD_FEEDS:
                    if f not in touched:
                        touched.append(f)
        else:
            failed.append(str(x.get("line") or ROW_ERROR_LINE))
            writes.append(_pinned(did, {"book_record_line": str(x.get("line") or "")}, doc.get("_version")))
    more = CLAIM_MORE_LINE.format(n=int(n_more)) if int(n_more or 0) > 0 else ""
    line = _apply_line(applied, refused, failed, recorded, more)
    counts = _apply_counts(applied, refused, len(failed), recorded)
    updating = str(task or "") == "update"
    # cr1#1722: the Update's own rebuild does the feeds, so only a connector
    # part is left for the detail steps.
    left = bool(inbox_ids or miss_ids) if updating else bool(inbox_ids or miss_ids or feeds)
    light = None
    if not left and not updating:
        light = _apply_light(workspace_root, status, rid, line, bool(failed), now_s)
        log_run_receipt(workspace_root, fired_via=fired_via, legs=["apply"], counts={"apply": counts},
                        task_id="cockpit-apply", now_iso=now_s)
    answer = {"ok": True, "run_id": rid, "batches": _chunks(writes), "light": light,
              "next": "detail" if left else "done", "line": line, "counts": counts,
              "connectors": {"inbox": inbox_ids, "miss": miss_ids}, "feeds": feeds,
              "results": {k: v for k, v in results.items()}}
    if updating:
        answer["feeds"] = []
        answer["touched"] = touched
        answer["all"] = touch_all
        # the table keys of the presses that landed (HUBUPDATE1 holds each narrow one to a comparison)
        answer["keys"] = sorted(set(pkeys))
        answer["first_failed"] = failed[0] if failed else ""
    return answer


def apply_finish(workspace_root, prior: Optional[Dict[str, Any]] = None,
                 results: Optional[Dict[str, Any]] = None, status: Optional[Dict[str, Any]] = None, *,
                 run_id: str = "", error: str = "", n_more: int = 0, fired_via: str = "scheduled",
                 now_iso=None) -> Dict[str, Any]:
    """A WRITER (the run's receipt). Every way an Apply run ends that is not
    `apply_start`'s stop or `apply_run`'s `next: done` (cr1#1608 review): the
    end of `cockpit-apply-detail.md`, and any answer with `ok: false` (or a call
    that failed) from `apply_start` or `apply_run`. The light leaves "running"
    on every path, so the Hub never waits on a run that ended.

    `prior` is `apply_run`'s `counts` (or null), `results` the `{choice id:
    result}` the detail steps wrote (or null), `status` the `status/apply`
    document as read, `error` the line of an `ok: false` answer or a failed
    call. Counts in code; answers `{ok, light, line, counts}` with `light` the
    pinned `done` or `error` light on this call's clock, and writes the run's
    receipt with the counts."""
    now_s = _iso(_now(now_iso))
    c = _apply_counts(**{k: int((prior or {}).get(k) or 0)
                         for k in ("applied", "refused", "failed", "recorded")})
    failed = [ROW_ERROR_LINE] * c["failed"]
    for res in (results or {}).values():
        out = (res or {}).get("outcome") if isinstance(res, dict) else None
        if out == "error":
            failed.insert(0, str(res.get("line") or ROW_ERROR_LINE))
        elif out == "refused":
            c["refused"] += 1
        elif out in ("applied", "already"):
            c["applied"] += 1
    err = str(error or "").strip()
    if err:
        failed.insert(0, err)
    more = CLAIM_MORE_LINE.format(n=int(n_more)) if int(n_more or 0) > 0 else ""
    line = _apply_line(c["applied"], c["refused"], failed, c["recorded"], more)
    counts = _apply_counts(c["applied"], c["refused"], len(failed), c["recorded"])
    log_run_receipt(workspace_root, fired_via=fired_via, legs=["apply"], counts={"apply": counts},
                    task_id="cockpit-apply", now_iso=now_s,
                    status="partial" if err else "complete")
    return {"ok": True, "light": _apply_light(workspace_root, status, run_id, line, bool(failed), now_s),
            "line": line, "counts": counts}


def apply_repin(workspace_root=None, writes: Optional[List[Dict[str, Any]]] = None,
                docs: Optional[List[Any]] = None, status: Optional[Dict[str, Any]] = None,
                results: Optional[Dict[str, Any]] = None, now_iso=None) -> Dict[str, Any]:
    """READ/COMPUTE. A batch from `apply_run` the page refused because a
    document changed since it was read (cr1#1608): the same writes, pinned
    again to the versions in `docs` (the two `choices` queries read again)
    and `status` (`status/apply` read again), so the run never re-pins by
    hand. A result write whose choice already carries an applied result, and
    a record write whose row is already recorded, are dropped (another run
    wrote them). `results` (`{choice id: its result}`, from the parts
    `cockpit-apply-detail.md` carries out) become result writes the same way,
    `applied_at` on this call's own clock. Answers `{ok, writes, dropped}`;
    send `writes` as one batch."""
    stamp = _iso(_now(now_iso))
    writes = list(writes or []) + [
        {"op": "update", "collection": "choices", "doc_id": str(k),
         "data": {"result": v, "applied": True, "applied_at": stamp}}
        for k, v in (results or {}).items() if isinstance(v, dict)]
    by_id: Dict[str, Dict[str, Any]] = {}
    for raw in docs or []:
        c = _flat_choice(raw)
        if c and str(c.get("id") or "").strip():
            by_id[str(c["id"]).strip()] = c
    st = _flat_choice(status) if isinstance(status, dict) else None
    out: List[Dict[str, Any]] = []
    dropped: List[str] = []
    for w in writes or []:
        if not isinstance(w, dict):
            continue
        w = dict(w)
        did = str(w.get("doc_id") or "")
        if w.get("collection") == "status":
            w.pop("if_version", None)
            if st and st.get("_version") is not None:
                w["if_version"] = st["_version"]
            out.append(w)
            continue
        doc = by_id.get(did)
        data = w.get("data") if isinstance(w.get("data"), dict) else {}
        if doc is None or ("result" in data and (doc.get("applied") is True or doc.get("applied_at")))                 or ("book_recorded" in data and doc.get("book_recorded")):
            dropped.append(did)
            continue
        w.pop("if_version", None)
        if doc.get("_version") is not None:
            w["if_version"] = doc["_version"]
        out.append(w)
    return {"ok": True, "writes": out, "dropped": dropped}


# ---------------------------------------------------------------------------
# the task's registration (cockpit-setup Step 2)
# ---------------------------------------------------------------------------

MORNING_CREATED_PAUSED_LINE = ("Hub Morning was set up paused, because Hub Prep, Hub Inbox or Morning Brief "
                               "was paused. Press Go on the Hub to switch it on.")
UPDATE_CREATED_PAUSED_LINE = ("Hub Update was set up paused, because Hub Apply or Hub Refresh was paused. "
                              "Press Update now on the Hub to switch it on.")
NO_SCHEDULER_LINE = ("No scheduler is reachable from this chat, so the Hub task "
                     "was not registered; say `set up my hub` in the Claude app.")
LEGACY_SCHEDULER_LINE = ("The Hub needs the cloud scheduler, which this computer's "
                         "app does not have; it is set up from the Claude app.")


#: What a create's landing payload carries until the call has returned its id.
LANDING_ID = "<the id the call returned>"


def _call_plan(plan, task_id: str, *, old_prompt=None) -> Dict[str, Any]:
    """A planned call as a dict, with its `landing` (cr1#734): the ONE door
    payload `run_writer` lands after the call. A create's is the registration
    writer's, with `trigger_id` still `LANDING_ID`, the one value the caller
    fills in. An update has no trigger row, so its
    is the refresh writer's, with fingerprints and never a body: the old one
    is None when the listing gave no prompt."""
    import schedule_backend as sb
    import schedule_refresh as sr

    out: Dict[str, Any] = {"tool": plan.tool, "args": plan.args, "after": list(plan.after), "landing": None}
    if any(name == "schedule_backend.record_trigger_map" for name, _kw in out["after"]):
        args = dict(sb.registration_payload(out["after"], source_skill=SETUP_SKILL)["args"])
        out["landing"] = {"name": "cockpit_helpers:land_registration",
                          "args": dict(args, trigger_id=LANDING_ID)}
    elif (plan.args or {}).get("prompt"):
        out["landing"] = {"name": "schedule_refresh:log_schedule_refreshed", "args": {
            "field": "prompt", "new_value": sr.prompt_fingerprint(plan.args["prompt"]),
            "old_value": sr.prompt_fingerprint(old_prompt) if old_prompt else None,
            "source_skill": SETUP_SKILL, "task_id": task_id, "workspace_root": "<WS>"}}
    return out


def land_registration(workspace_root, *, task_id: str, row: Dict[str, Any],
                      event: Optional[Dict[str, Any]] = None, trigger_id: str = "",
                      merge: bool = False, source_skill: str = SETUP_SKILL,
                      retire: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A WRITER. A created task's trigger row and receipt, through
    `schedule_backend.record_registration` (cr1#734). The id is one argument,
    set on both here, and a missing or unfilled one is refused: the receipt
    lands in a log that is never rewritten, so a placeholder must not reach
    it.

    Early review of #1776 (should 4): Hub Morning's create carries `retire`
    (Hub Prep, Hub Inbox and the Morning Brief chat, as set-up planned them),
    and it is handed back only here, once the create's id landed: a create
    that failed or a refused landing returns none, so they keep running."""
    import schedule_backend as sb

    tid = str(trigger_id or "").strip()
    if not tid or "<" in tid or ">" in tid:
        return {"ok": False, "reason": "no_trigger_id", "line": NO_TRIGGER_ID_LINE}
    ev = dict(event, trigger_id=tid) if isinstance(event, dict) else event
    out = sb.record_registration(workspace_root, task_id, dict(row or {}, trigger_id=tid), ev,
                                 merge=merge, source_skill=source_skill)
    out = dict(out, ok=True)
    if task_id == "cockpit-morning" and isinstance(retire, dict):
        out["retire"] = {k: list(retire.get(k) or []) for k in ("after_morning_tasks", "after_morning")}
    return out


def plan_task_registration(workspace_root, *, task_id: str = "cockpit-morning",
                           tools=None, abs_path: Optional[str] = None,
                           plugin_version: str = "", tz_name: str = "",
                           registered=None, discover_block: Optional[str] = None,
                           plugin_root=None, writer_pair=None, env=None,
                           on_date=None) -> Dict[str, Any]:
    """ONE cockpit task's registration, planned and not called (READ/COMPUTE
    ONLY). `{ok, line, backend, task_id, trigger_name, body, create, update,
    current}`. `plan_cockpit_registration` below plans all four.

    A cockpit task is NOT a row of the schedules registry: it is registered
    only beside the page it fills, so this plans ONE chat the way
    `schedule_backend.plan_registration` plans each of the daily ones, with
    the same composer (`schedule_refresh.compose_stub_body`, cr1#638: the
    stub, whose full body the installed plugin composes at every fire with
    `compose_bootloader_body`, the writer pair baked, every placeholder filled) and the orchestrator named in
    `COCKPIT_TASKS` rather than in the map. `create` and `update` are the
    backend's own `CallPlan` as a dict (`tool`, `args`, `after`, `landing`);
    the skill executes the call and lands its `landing` (`_call_plan`). The
    cloud scheduler only: the older desktop app's scheduler is
    refused with its one line."""
    import os as _os

    import schedule_backend as sb
    import schedule_config as sc
    import schedule_refresh as sr

    env_map = dict(_os.environ) if env is None else dict(env)
    names = sc._tool_names(tools)
    backend, _found = sb.select_backend(names)
    tid = str(task_id or "").strip()
    out: Dict[str, Any] = {"ok": False, "line": NO_SCHEDULER_LINE, "backend": None,
                           "task_id": tid, "trigger_name": None, "body": None,
                           "create": None, "update": None, "current": False}
    if tid not in COCKPIT_TASKS:
        out["line"] = UNKNOWN_VERB_LINE.format(verb=tid or "none")
        return out
    spec = COCKPIT_TASKS[tid]
    if backend is None:
        return out
    if backend.id != "cloud":
        out.update(line=LEGACY_SCHEDULER_LINE, backend=backend.id)
        return out
    path = str(abs_path or env_map.get(sc.DEVICE_WORKSPACE_ENV, "") or workspace_root or "").strip()
    basename = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if not basename:
        out.update(line=NO_WORKSPACE_LINE)
        return out
    pair = sc.registration_pair(path, writer_pair=writer_pair, env=env)
    if pair is None:
        out.update(line=sc.no_account_line(), backend=backend.id)
        return out
    body = sr.compose_stub_body(
        tid, workspace_basename=basename, plugin_version=plugin_version,
        plugin_root=plugin_root, orchestrator_filename_override=spec["orchestrator"],
        abs_path=path, display_name=sc.task_display_name(tid),
        discover_block=discover_block, writer_id=pair[0], writer_derivation=pair[1])
    name = sb.trigger_name(tid, basename)
    out.update(ok=True, line="", backend=backend.id, trigger_name=name, body=body)
    rec = None
    for r in registered or []:
        rname = r.get("name") if isinstance(r, dict) else getattr(r, "name", None)
        if rname == name:
            rec = r
            break
    if rec is None:
        plan = backend.plan_create(tid, prompt=body, cron_local=spec["cron"],
                                   tz_name=tz_name, notify=False,
                                   workspace_basename=basename, folders=[path],
                                   on_date=on_date)
        if isinstance(plan, sb.Refusal):
            out.update(ok=False, line=plan.line)
            return out
        out["create"] = _call_plan(plan, tid)
        return out
    prompt = rec.get("prompt") if isinstance(rec, dict) else getattr(rec, "prompt", None)
    trigger_id = (rec.get("trigger_id") or rec.get("id")) if isinstance(rec, dict) else getattr(rec, "trigger_id", None)
    if prompt and sr.prompts_equivalent(body, prompt):
        out["current"] = True
        out["trigger_id"] = trigger_id
        return out
    plan = backend.plan_update(tid, trigger_id=trigger_id, prompt=body)
    if isinstance(plan, sb.Refusal):
        out.update(ok=False, line=plan.line)
        return out
    out["update"] = _call_plan(plan, tid, old_prompt=prompt)
    out["trigger_id"] = trigger_id
    return out


#: The two chats of the previous version that survive the cockpit (cr1#618,
#: cr1#619). The cockpit's set-up plans them beside its own four.
SURVIVING_CHATS = ("morning-brief", "maintenance")


def _chat_rows_placed(workspace_root, registered, basename: str):
    """`(rows, placed)`: the listing with each chat's raw row given its task
    id, and `{task_id: trigger_id}` for the rows placed here (cr1#733). The
    schedules planner matches on `taskId`, which only `normalize` sets, so a
    raw row read as no chat and a rebuild planned both as new. A row is this
    folder's chat by its exact name, the way `plan_task_registration` matches
    the four Hub tasks, or by the trigger id in the stored map, which is what
    identifies a chat the owner renamed. A name for another folder, and a row
    that already has a task id, come through as they were."""
    import schedule_backend as sb

    try:
        stored = {t: row for t, row in sb.read_trigger_map(workspace_root).items()
                  if t in SURVIVING_CHATS}
    except Exception:  # noqa: BLE001 - no map leaves the names to decide
        stored = {}
    given = list(registered or [])
    # The one placement rule, shared with the schedules planner (cr1#923).
    rows: List[Any] = sb.place_listing_rows(given, basename=basename,
                                            task_ids=list(SURVIVING_CHATS),
                                            trigger_map=stored)
    placed: Dict[str, Any] = {}
    for before, after in zip(given, rows):
        if (isinstance(after, dict) and after.get("taskId")
                and not (isinstance(before, dict) and before.get("taskId"))):
            placed[after["taskId"]] = after.get("trigger_id")
    return rows, placed


def _plan_surviving_chats(workspace_root, *, tools, abs_path, plugin_version,
                          tz_name, registered, discover_block, plugin_root,
                          writer_pair, env, on_date, chats: Tuple[str, ...] = SURVIVING_CHATS):
    """Morning Brief and Maintenance through the schedules planner, reshaped
    as cockpit plans. Returns `(plans, refused)`; never raises. `chats` is
    Maintenance alone once Hub Morning writes the brief (decision E)."""
    import schedule_backend as sb

    SURVIVING_CHATS_HERE = tuple(c for c in SURVIVING_CHATS if c in chats)

    basename = str(abs_path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    rows, placed = _chat_rows_placed(workspace_root, registered, basename)
    # cr1#1502: a listing row that gives its prompt as a `path` is read in here
    # too, so the update's `old_prompt` is the real text, not None.
    rows = sb.fill_listing_prompts(rows)
    try:
        ans = sb.plan_registration(
            tools, workspace_root=workspace_root, abs_path=abs_path,
            task_ids=list(SURVIVING_CHATS_HERE), registered=rows,
            plugin_version=plugin_version, tz_name=tz_name,
            writer_pair=writer_pair, discover_block=discover_block,
            on_date=on_date, plugin_root=plugin_root, env=env)
    except Exception as exc:  # a planner fault never stops the cockpit
        return [], [{"task_id": t, "line": "Could not plan this chat (%s)." % type(exc).__name__}
                    for t in SURVIVING_CHATS_HERE]
    if not ans.get("ok"):
        return [], [{"task_id": t, "line": ans.get("line") or ""} for t in SURVIVING_CHATS_HERE]
    by_id: Dict[str, Dict[str, Any]] = {}
    old = {r.get("taskId"): r.get("prompt") for r in rows if isinstance(r, dict) and r.get("taskId")}
    base = {"ok": True, "line": "", "backend": ans.get("backend"), "body": None,
            "create": None, "update": None, "current": False}
    for plan in ans.get("creates") or []:
        by_id[plan.task_id] = dict(base, task_id=plan.task_id,
                                   create=_call_plan(plan, plan.task_id))
    for plan in ans.get("updates") or []:
        by_id[plan.task_id] = dict(base, task_id=plan.task_id,
                                   trigger_id=(plan.args or {}).get("trigger_id"),
                                   update=_call_plan(plan, plan.task_id, old_prompt=old.get(plan.task_id)))
    for tid in ans.get("current") or []:
        by_id[tid] = dict(base, task_id=tid, current=True)
    refused = [dict(r) for r in ans.get("refused") or [] if isinstance(r, dict)]
    # A row with no prompt is unknown and never rewritten, so the schedules
    # planner skips it. The chat is there, so it is `current`, not compared,
    # and never a second task (cr1#733).
    # cr1#1502: one whose listing `path` could not be read is also marked
    # `unchecked`, so nothing downstream mistakes it for a compared one.
    gone = {str(r.get("task_id")) for r in refused} | set(ans.get("foreign") or [])
    unread = set(ans.get("unchecked") or [])
    for tid, trigger_id in placed.items():
        if tid not in by_id and tid not in gone:
            by_id[tid] = dict(base, task_id=tid, trigger_id=trigger_id, current=True, not_compared=True)
            if tid in unread:
                by_id[tid]["unchecked"] = True
    plans = [by_id[t] for t in SURVIVING_CHATS_HERE if t in by_id]
    return plans, refused


def plan_cockpit_registration(workspace_root, *, tools=None, abs_path: Optional[str] = None,
                              plugin_version: str = "", tz_name: str = "",
                              registered=None, discover_block: Optional[str] = None,
                              plugin_root=None, writer_pair=None, env=None,
                              on_date=None) -> Dict[str, Any]:
    """Every task the cockpit needs, planned and not called (READ/COMPUTE
    ONLY): `{ok, line, backend, plans: [x5], n_create, n_update, n_current,
    stale, chats_refused}`. Plans 1 to 3 are the cockpit tasks
    (`plan_task_registration` answers; four until cr1#1722 made Apply and
    Refresh the one Update); plans 4 and 5 are Morning Brief and
    Maintenance, the two chats that survive the cockpit (the owner's ruling of
    2026-11-03 on cr1#619), planned by the schedules skill's own
    `schedule_backend.plan_registration` and handed back in the SAME shape
    (`task_id`, `create` | `update` | `current`), so the set-up executes five
    plans in one loop. The first build planned the two chats in a second call
    described only in prose, and a real set-up skipped it: the owner's seat
    came out with the four cockpit tasks and neither chat (cr1#631). `ok` is
    false, with the first refusal's line, when a cockpit task cannot be
    planned (one scheduler, one account, one answer); a chat the schedules
    planner refuses is listed in `chats_refused` with its line and does not
    stop the cockpit. `stale` lists the one-task shape's
    trigger (`Cockpit - Command Room (<folder>)`, #614) when the listing still
    carries it: the set-up switches it off and says so; the bridge removes it
    (cr1#618). Since cr1#1722 `stale` also lists `Hub Apply` and `Hub Refresh`,
    the two tasks `Hub Update` replaced, so neither runs beside it.

    The folder is resolved ONCE here, for all six (cr1#732). The door refuses
    a computer path as an argument, so cockpit-setup passes none. A merged
    seat (`CR_HOST_MODE=vm`) takes the `CR_DEVICE_WORKSPACE` the door forwards
    and nothing else: the workspace path it holds is not the computer's, so
    with none forwarded nothing is planned, and a scheduler refusal still
    answers first. Any other seat's files are here: `abs_path`, else the
    forwarded value, else the workspace itself."""
    import os as _os

    import schedule_config as sc

    env_map = dict(_os.environ) if env is None else dict(env)
    forwarded = str(env_map.get(sc.DEVICE_WORKSPACE_ENV, "") or "").strip()
    if str(env_map.get("CR_HOST_MODE", "")).strip().lower() == "vm":
        given = str(abs_path or "").strip()
        same = given.replace("\\", "/").rstrip("/") == str(workspace_root or "").replace("\\", "/").rstrip("/")
        abs_path = forwarded or ("" if same else given)
    else:
        abs_path = str(abs_path or forwarded or workspace_root or "").strip()
    if not abs_path:
        first = plan_task_registration(workspace_root, task_id=COCKPIT_TASK_IDS[0], tools=tools,
                                       abs_path=str(workspace_root or ""), plugin_version=plugin_version,
                                       tz_name=tz_name, registered=[], discover_block=discover_block,
                                       plugin_root=plugin_root, writer_pair=writer_pair, env=env,
                                       on_date=on_date)
        return {"ok": False, "reason": "no_folder", "backend": first.get("backend"),
                "line": NO_FOLDER_LINE if first.get("ok") else first.get("line"),
                "plans": [], "chats_refused": [], "n_create": 0, "n_update": 0,
                "n_current": 0, "stale": []}
    plans = []
    # cr1#1722, review round 1 of #1768 (cr1#914): a Hub task that takes over
    # from others (Update from Apply and Refresh; Morning from Prep, Inbox and
    # the Morning Brief chat) is created paused when the owner had paused any
    # of them, and set-up says so; a pause is never undone by set-up.
    base_now = abs_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    brief_rows = _morning_brief_rows(workspace_root, registered, base_now, abs_path=abs_path)
    paused_from: Dict[str, bool] = {}
    # Review round 1 of #1776: a task set-up itself switched off (its delete
    # was refused) is not the owner's pause, and is never carried.
    setup_off = _setup_switched_off(workspace_root)
    for tid, olds in CARRIED_PAUSE_FROM.items():
        names = {f"{RETIRED_TASK_NAMES[o]} - Command Room ({base_now})" for o in olds if o in RETIRED_TASK_NAMES}
        hit = False
        for r in registered or []:
            rname = r.get("name") if isinstance(r, dict) else getattr(r, "name", None)
            ron = r.get("enabled") if isinstance(r, dict) else getattr(r, "enabled", None)
            rid = r.get("trigger_id") if isinstance(r, dict) else getattr(r, "trigger_id", None)
            if rname in names and ron is False and str(rid or "") not in setup_off and _row_folder_here(r, abs_path):
                hit = True
        if "morning-brief" in olds and any(row.get("enabled") is False and str(row.get("trigger_id") or "") not in setup_off
                                           for row in brief_rows):
            hit = True
        paused_from[tid] = hit
    paused_lines = {"cockpit-update": UPDATE_CREATED_PAUSED_LINE, "cockpit-morning": MORNING_CREATED_PAUSED_LINE}
    for tid in COCKPIT_TASK_IDS:
        plan = plan_task_registration(
            workspace_root, task_id=tid, tools=tools, abs_path=abs_path,
            plugin_version=plugin_version, tz_name=tz_name, registered=registered,
            discover_block=discover_block, plugin_root=plugin_root,
            writer_pair=writer_pair, env=env, on_date=on_date)
        if paused_from.get(tid) and plan.get("create"):
            # The scheduler's create takes no pause, so the plan carries the
            # call that pauses the new task, for set-up to run right after the
            # create with the id it returned.
            import schedule_backend as _sb

            plan["paused"] = True
            plan["paused_line"] = paused_lines[tid]
            plan["switch_off"] = {"tool": _sb.CLOUD_TOOL_MAP["update"],
                                  "args": {"trigger_id": LANDING_ID, "enabled": False}}
        elif paused_from.get(tid) and (plan.get("update") or plan.get("current")):
            # Fix check of round 1 of #1768: a set-up run again after one that
            # stopped half way (the Hub task made and on, a task it takes over
            # from still there and paused) pauses the one it finds on, and says so.
            import schedule_backend as _sb

            mine = next((r for r in registered or []
                         if (r.get("name") if isinstance(r, dict) else getattr(r, "name", None)) == plan.get("trigger_name")), None)
            on = mine.get("enabled") if isinstance(mine, dict) else getattr(mine, "enabled", None)
            tid_found = (mine.get("trigger_id") or mine.get("id")) if isinstance(mine, dict) else getattr(mine, "trigger_id", None)
            if on is True and tid_found:
                plan["paused"] = True
                plan["paused_line"] = paused_lines[tid]
                plan["switch_off"] = {"tool": _sb.CLOUD_TOOL_MAP["update"],
                                      "args": {"trigger_id": tid_found, "enabled": False}}
        plans.append(plan)
    bad = [p for p in plans if not p.get("ok")]
    chats_refused: List[Dict[str, Any]] = []
    # Decision E (cr1#1722 step 2): with Hub Morning planned, the Hub writes the
    # brief, so the Morning Brief chat is neither created nor refreshed here.
    morning_in = not bad and any(p.get("task_id") == "cockpit-morning" and p.get("ok") for p in plans)
    if not bad:
        chats, chats_refused = _plan_surviving_chats(
            workspace_root, tools=tools, abs_path=abs_path,
            plugin_version=plugin_version, tz_name=tz_name, registered=registered,
            discover_block=discover_block, plugin_root=plugin_root,
            writer_pair=writer_pair, env=env, on_date=on_date,
            chats=tuple(c for c in SURVIVING_CHATS if not (morning_in and c == "morning-brief")))
        plans = plans + chats
    out = {"ok": not bad, "line": bad[0]["line"] if bad else "",
           "backend": plans[0].get("backend"), "plans": plans,
           "chats_refused": chats_refused,
           "n_create": sum(1 for p in plans if p.get("create")),
           "n_update": sum(1 for p in plans if p.get("update")),
           "n_current": sum(1 for p in plans if p.get("current")), "stale": [], "after_morning": [],
           "after_morning_tasks": []}
    basename = abs_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if basename:
        import schedule_backend as sb

        # The one-task shape (#614), and the four tasks under their names before
        # the rename to Command Room Hub (cr1#670): set-up switches each off.
        olds = [sb.trigger_name(TASK_ID, basename)] + [
            f"{name} - Command Room ({basename})" for name in PRE_HUB_NAMES.values()]
        # cr1#1722: Hub Apply and Hub Refresh became Hub Update; their triggers go
        # the same way, so neither runs beside the Update that replaced them.
        retired_ids = {f"{name} - Command Room ({basename})": rid for rid, name in RETIRED_TASK_NAMES.items()}
        olds += [f"{name} - Command Room ({basename})" for name in RETIRED_TASK_NAMES.values()]
        # Review round 1 of #1776: Hub Prep and Hub Inbox are the morning's own
        # runs until Hub Morning is in, so they go only after its create landed
        # (`after_morning_tasks`, run with `after_morning`), never with `stale`.
        wait_for_morning = {name for name, rid in retired_ids.items() if rid in CARRIED_PAUSE_FROM["cockpit-morning"]}
        for r in registered or []:
            rname = r.get("name") if isinstance(r, dict) else getattr(r, "name", None)
            if rname in olds:
                tid = (r.get("trigger_id") if isinstance(r, dict) else getattr(r, "trigger_id", None))
                entry: Dict[str, Any] = {"name": rname, "trigger_id": tid, "delete": None,
                                         "switch_off": None}
                # cr1#676: a stale task is deleted, not just switched off, so no
                # Start can switch it back on; the backend that cannot delete
                # switches it off instead.
                if tid and out.get("backend") == "cloud":
                    entry["delete"] = {"tool": sb.CLOUD_TOOL_MAP["delete"], "args": {"trigger_id": tid}}
                    entry["switch_off"] = {"tool": sb.CLOUD_TOOL_MAP["update"],
                                           "args": {"trigger_id": tid, "enabled": False}}
                    if rname in retired_ids:
                        entry["record_off"] = _record_off_call(retired_ids[rname], tid)
                if rname in wait_for_morning:
                    if morning_in and _row_folder_here(r, abs_path):
                        out["after_morning_tasks"].append(entry)
                    continue
                out["stale"].append(entry)
        # Decision E: the Morning Brief chat is deleted only once Hub Morning is
        # registered on this seat (set-up runs these after its create landed),
        # and only a row that is this folder's by its exact name or by the
        # trigger id the stored map holds for it; never by a prefix, and a
        # renamed copy the map does not know is left alone.
        if morning_in:
            for row in brief_rows:
                tid = row.get("trigger_id")
                entry = {"name": row.get("name"), "trigger_id": tid, "matched_by": row.get("matched_by"),
                         "delete": None, "switch_off": None}
                if tid and out.get("backend") == "cloud":
                    entry["delete"] = {"tool": sb.CLOUD_TOOL_MAP["delete"], "args": {"trigger_id": tid}}
                    entry["switch_off"] = {"tool": sb.CLOUD_TOOL_MAP["update"],
                                           "args": {"trigger_id": tid, "enabled": False}}
                    entry["record_off"] = _record_off_call("morning-brief", tid)
                out["after_morning"].append(entry)
        # Early review of #1776 (should 4): when Hub Morning is created on this
        # run, the lists travel on its create's landing and come back from
        # `land_registration` only once its id landed.
        mp = next((x for x in plans if x.get("task_id") == "cockpit-morning"), None)
        landing = ((mp or {}).get("create") or {}).get("landing") if isinstance((mp or {}).get("create"), dict) else None
        if isinstance(landing, dict) and isinstance(landing.get("args"), dict):
            landing["args"]["retire"] = {"after_morning_tasks": out["after_morning_tasks"],
                                         "after_morning": out["after_morning"]}
            out["after_morning_tasks"], out["after_morning"] = [], []
            out["retire_after_create"] = True
    return out


#: Review round 1 of #1776: the mark set-up leaves on a task it could only
#: switch off (its delete was refused), in the stored trigger map.
SETUP_OFF_KEY = "switched_off_by_setup"
#: cr1#1782: every trigger id set-up switched off for one task, a list the
#: stored map keeps by union (`schedule_backend.MERGE_UNION_KEYS`).
SETUP_OFF_IDS_KEY = "switched_off_by_setup_ids"


def _row_folder_here(r: Any, abs_path: str) -> bool:
    """Early review of #1776 (should 5): a listing row matched by its name is
    this folder's unless the row's own folder (its `folders`, or the path its
    prompt bakes) is a different one: two folders with the same last name on
    one account never retire or pause each other's tasks."""
    try:
        import schedule_backend as sb

        there = sb.record_device_path(r)
    except Exception:  # noqa: BLE001 - an unreadable row is left to its name
        there = None
    if not there:
        return True

    def norm(x: Any) -> str:
        return str(x or "").replace("\\", "/").rstrip("/").casefold()

    return norm(there) == norm(abs_path)


def _record_off_call(task_id: str, trigger_id: Any) -> Dict[str, Any]:
    """The writer call set-up runs after a `switch_off` that stood in for a
    refused delete: the stored map remembers that set-up switched this
    trigger off, so the next set-up never reads it as the owner's pause."""
    # cr1#1782: the id goes in a list the merge keeps by union, so a second
    # copy of the same retired chat never drops the first, and the row's own
    # `trigger_id` (the stored identity of the task) is left as it was.
    return {"name": "schedule_backend:record_trigger_map",
            "args": {"merge": True, "row": {SETUP_OFF_IDS_KEY: [trigger_id]},
                     "task_id": task_id, "workspace_root": "<WS>"}}


def _setup_switched_off(workspace_root) -> set:
    """The trigger ids the stored map says set-up itself switched off."""
    try:
        import schedule_backend as sb

        rows = sb.read_trigger_map(workspace_root).values()
    except Exception:  # noqa: BLE001 - no map: every paused task is read as the owner's
        return set()
    out = {str(r.get("trigger_id")) for r in rows
           if isinstance(r, dict) and r.get(SETUP_OFF_KEY) is True and r.get("trigger_id")}
    # cr1#1782: every copy set-up switched off, kept as a list (older maps
    # carry the one flag and id above)
    for r in rows:
        ids = r.get(SETUP_OFF_IDS_KEY) if isinstance(r, dict) else None
        if isinstance(ids, list):
            out |= {str(t) for t in ids if t}
    return out


def _morning_brief_rows(workspace_root, registered, basename: str, abs_path: str = "") -> List[Dict[str, Any]]:
    """Decision E: the listing rows that are THIS folder's Morning Brief chat,
    by its exact name or by the trigger id the stored map holds for it (the
    #940/#942 rule). Every copy that matches is listed (a seat that has the
    chat twice, #942); a row of another folder, or a renamed one the map does
    not know, is not. Each `{name, trigger_id, enabled, matched_by}`."""
    if not basename:
        return []
    import schedule_backend as sb

    exact = sb.trigger_name("morning-brief", basename)
    try:
        stored = sb.read_trigger_map(workspace_root).get("morning-brief")
    except Exception:  # noqa: BLE001 - no map leaves the exact name to decide
        stored = None
    stored_id = stored.get("trigger_id") if isinstance(stored, dict) else stored
    out: List[Dict[str, Any]] = []
    for r in registered or []:
        get = (lambda k: r.get(k)) if isinstance(r, dict) else (lambda k: getattr(r, k, None))
        name, tid = get("name"), (get("trigger_id") or get("id"))
        by = "trigger_id" if stored_id and tid and str(tid) == str(stored_id) else (
            "name" if name == exact and (not abs_path or _row_folder_here(r, abs_path)) else "")
        if by:
            out.append({"name": name, "trigger_id": tid, "enabled": get("enabled"), "matched_by": by})
    return out


# ---- the refresh in one call (cr1#1530) ----
#
# A refresh used to be one program call per feed, each answer carried through
# the chat and written to the page on its own: about 35 tool calls, and on a
# seat whose folder is on the owner's computer a combined read was cut off at
# the device answer limit. `refresh_all` builds every feed in ONE process,
# with the event log read once, and keeps the answers in a scratch folder
# OUTSIDE the workspace. Its own answer is small: what was built, how long each
# took, and the parts to pull. `refresh_part` hands one part back, sized to
# stay under the limit. `unpack-refresh` (this file's command line, run where
# the page's database tool runs) turns the pulled parts into one file per
# document and the batch that writes them.

#: One pulled part stays under this many characters. The device answer is cut
#: at 262,144 (seen on a seat, cr1#1530); the rest is room for the envelope.
REFRESH_PART_BUDGET = 200_000
#: cr1#1837: how a pulled part reaches the cloud step's batch file. Nothing in
#: this code writes that file: the run's tool saves an answer to a file in the
#: session when the answer is long, and the run hands that file on unchanged.
#: An answer that comes back in the turn would have to be retyped. On the
#: owner's seat on 2026-11-25 (sizes from the live page's listing after the
#: natural runs) a 28 KB answer came back in the turn, and documents of 37.9 KB
#: and more (the Inbox feed; the plate at 168.6 KB) crossed as files: the tool's
#: cutoff sat between those, about 30,000 characters. That cutoff belongs to the
#: tool, not to this code, and may change. The part that carries the off-limits
#: list is therefore the list alone, padded so its answer is at least this long.
WALL_CARRY_DOC = "feed/wall"
WALL_CARRY_MIN_CHARS = 64_000
#: cr1#1837: what the light, the receipt and the health check say when the list
#: did not reach the page on a run.
WALL_NOT_SENT_LINE = "The off-limits list was not sent to the page on this run; the page keeps the copy it has."
WALL_IN_TURN_LINE = "Its part came back in the run's turn instead of as a file, and it is never retyped."
#: Review round 1 of #1837 (must 1): an Update always carries the list; one
#: that has no list part could not build it.
WALL_NOT_BUILT_LINE = "The off-limits list could not be built on this run."
#: Review round 1 of #1837 (must 2, the #1772 fence): without the page's own
#: read of its feeds nothing is published, so no older copy replaces a newer one.
WALL_NO_PAGE_READ_LINE = ("The page's own copy of the off-limits list could not be read on this run, "
                          "so this one was not published.")
#: One document larger than this cannot be pulled whole; it is named, not sent.
REFRESH_DOC_LIMIT = 215_000
#: One page batch stays under this many characters (the tool's limit is 1 MiB).
REFRESH_BATCH_BUDGET = 900_000
REFRESH_SCRATCH_PREFIX = "cr-hub-refresh-"
REFRESH_MANIFEST = "_manifest.json"
_REFRESH_TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")

#: The read feeds, in the order the refresh step always ran them. The plate
#: (a writer) runs before these, the Record tab and the Maintenance record after.
REFRESH_READS: Tuple[Tuple[str, str], ...] = (
    ("feed/projects", "feed_projects"),
    ("feed/board", "feed_board"),
    ("feed/mine", "feed_mine"),
    ("feed/mail_rules", "feed_mail_rules"),
    ("feed/people", "feed_people"),
    ("feed/brief", "feed_brief"),
    # cr1#1789: Today's People follow-ups and key relationships, read after the people, in
    # the same place as in the reads part (REFRESH_STAGES), so both orders agree
    ("feed/pfollow", "feed_pfollow"),
    # cr1#1785: Focus, the list the owner picks, its own small document
    ("feed/focus", "feed_focus"),
    # cr1#1785 page part: every open item, lean, for All open's search
    ("feed/allopen", "feed_allopen"),
    ("feed/review", "feed_review"),
    ("feed/settings", "feed_settings"),
    # cr1#1706: the "Still yours?" review's document, a READ of the small file
    # its writers keep (`still_yours.feed`), never built inside `feed/plate`.
    ("feed/stillyours", "feed_stillyours"),
    # cr1#1745: the follow-ups waiting on the wrap-up page, their words kept out of the plate
    ("feed/followups", "feed_followups"),
    # cr1#1786: Urgent, re-ranked from the inbox part's store at every Update
    ("feed/urgent", "feed_urgent"),
    # cr1#1788: the Prep page's edits, laid over the prep on the page
    ("feed/prepx", "feed_prepx"),
)
REFRESH_PLATE_FAILED_LINE = "The plate could not be read, so the Hub was not refreshed."
#: Every document a refresh may write, and no other (never another task's light).
REFRESH_DOCS: Tuple[str, ...] = (("feed/plate",) + tuple(d for d, _n in REFRESH_READS)
                                 + ("feed/week", "status/maintenance", "status/refresh"))
#: cr1#1638: the refresh's own light is one of its documents (sorted last, so
#: it lands after every feed); a second refresh that finds it held by a live
#: run stops instead of taking it over.
REFRESH_BUSY_LINE = "Another refresh is running now; this one stopped without changing anything."
REFRESH_CLAIM_MINUTES = RUNNING_TIMEOUT_MINUTES
REFRESH_BATCH_MAX = 50
#: cr1#1603: the scheduled Prep and Inbox runs build their documents the same
#: way, each task in its own scratch folder, handed back by the same part
#: reader. The documents each task may hand back, and no other.
#: cr1#1722: every document an Update may write: the refresh's, with its own
#: light in place of the Refresh task's.
UPDATE_DOCS: Tuple[str, ...] = tuple(d for d in REFRESH_DOCS if d != "status/refresh") + ("status/update",)
#: cr1#1837: every Update also carries the off-limits list, in its own first part.
UPDATE_PULL_DOCS: Tuple[str, ...] = UPDATE_DOCS + (WALL_CARRY_DOC,)
ONE_CALL_DOCS: Dict[str, Tuple[str, ...]] = {
    "refresh": REFRESH_DOCS, "update": UPDATE_PULL_DOCS, "prep": ("feed/today",), "inbox": ("feed/inbox",)}
#: Every document a pulled part may carry, whichever task built it.
PULL_DOCS: Tuple[str, ...] = REFRESH_DOCS + ("status/update", "feed/today", "feed/inbox", WALL_CARRY_DOC)
ONE_CALL_SCRATCH_PREFIX = "cr-hub-"
PREP_ALL_FAILED_LINE = "Meeting prep could not finish here, so the Today tab was not refreshed."
INBOX_FEED_FAILED_LINE = "The inbox page is saved, but the Inbox tab could not be built from it."
#: cr1#1614 review: the Today feed was not built or could not be kept.
PREP_FEED_FAILED_LINE = "Meeting prep ran, but the Today tab could not be built."
#: cr1#1614 review: the run's answers could not be kept in its scratch folder.
ONE_CALL_KEEP_FAILED_LINE = "The run could not keep its answers on this computer."
#: cr1#1614 review: a superseded token, said per task. The Inbox one must
#: never invite a second run of its writer chain.
ONE_CALL_GONE_LINES: Dict[str, str] = {
    "refresh": "That refresh is no longer on this computer: run it again.",
    "update": "That update is no longer on this computer: a newer one replaced it.",
    "prep": "That prep run's answers were replaced by a newer run: do not prep again; the newer run's light says how it went.",
    "inbox": ("That inbox run's answers were replaced by a newer run: do not run the inbox chain again; "
              "the newer run's light says how it went."),
}


def _one_log_read():
    """The event log read once for everything inside the block: the reader
    module's own `OneLogRead`."""
    from events_io import OneLogRead

    return OneLogRead()


def _flat_status(raw: Any) -> Optional[Dict[str, Any]]:
    """A status document as the page's database hands it to a run: its own
    fields at the top, or `{doc_id | id, version, data: {...}}` (the same two
    shapes `_flat_choice` reads). Either way the fields, with the leg taken
    from the document's id when the fields do not carry it."""
    if not isinstance(raw, dict):
        return None
    out = dict(raw["data"]) if isinstance(raw.get("data"), dict) else dict(raw)
    if not out.get("leg"):
        out["leg"] = str(raw.get("doc_id") or raw.get("id") or "")
    return out


def _refresh_base() -> Path:
    """The computer's own temporary folder, named from its environment and
    never from the working directory (which may be the workspace). The read
    half (`refresh_part`) names the same folder and creates nothing."""
    for key in ("TMPDIR", "TEMP", "TMP"):
        val = os.environ.get(key)
        if val and Path(val).is_dir():
            return Path(val)
    return Path("/tmp")


def _refresh_scratch(workspace_root) -> Path:
    """One scratch folder per workspace, named from the workspace's own path
    and reused by every refresh: a new run writes over the last one's files,
    so nothing piles up and nothing is ever deleted. Which run the files
    belong to is in the folder's manifest."""
    key = hashlib.sha256(str(Path(str(workspace_root)).resolve()).encode("utf-8")).hexdigest()[:16]
    return _refresh_base() / (REFRESH_SCRATCH_PREFIX + key)


def _task_scratch(workspace_root, task: str = "refresh") -> Path:
    """cr1#1603: the scratch folder of one task's one-call run. The refresh
    keeps its own (`_refresh_scratch`); Prep and Inbox each have their own,
    named the same way, so a run of one never writes over another's."""
    if task == "refresh":
        return _refresh_scratch(workspace_root)
    key = hashlib.sha256(str(Path(str(workspace_root)).resolve()).encode("utf-8")).hexdigest()[:16]
    return _refresh_base() / (ONE_CALL_SCRATCH_PREFIX + task + "-" + key)


def _refresh_file(scratch: Path, doc: str) -> Path:
    return scratch / (doc.replace("/", "__") + ".json")


def _refresh_manifest(scratch: Path) -> Dict[str, Any]:
    try:
        val = json.loads((scratch / REFRESH_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def _task_parts(task: str, sizes: List[Tuple[str, int]]) -> List[List[str]]:
    """cr1#1837: a task's parts. An Update's off-limits list is its own first
    part, so that part is padded alone and lands first; the rest are packed as
    `_refresh_parts` packs them."""
    if str(task or "") == "update" and any(d == WALL_CARRY_DOC for d, _n in sizes):
        return [[WALL_CARRY_DOC]] + _refresh_parts([(d, n) for d, n in sizes if d != WALL_CARRY_DOC])
    return _refresh_parts(sizes)


def _refresh_parts(sizes: List[Tuple[str, int]]) -> List[List[str]]:
    """Documents packed into parts, each under `REFRESH_PART_BUDGET`, in the
    order given; a document over the budget by itself is its own part."""
    parts: List[List[str]] = []
    room = 0
    for doc, n in sizes:
        if not parts or n > room:
            parts.append([])
            room = REFRESH_PART_BUDGET
        parts[-1].append(doc)
        room -= n
    return parts


def _one_call_build(built: Dict[str, Dict[str, Any]], report: List[Dict[str, Any]], doc: str, fn) -> None:
    """Build one document of a one-call run: its answer in `built`, its row
    (built or not, and its seconds) in `report`. A failed build is named and
    left out, never raised, except the write door's own refusal."""
    import time

    t0 = time.monotonic()
    row: Dict[str, Any] = {"doc": doc, "ok": True}
    try:
        val = fn()
        if not isinstance(val, dict):
            raise ValueError("the answer is not an object")
        built[doc] = val
    except Exception as exc:  # noqa: BLE001 - one failed read never stops the others
        if getattr(exc, "cr_refusal_reason", None) or type(exc).__name__ == "WriterIdentityRequired":
            raise  # the write door's own refusal, with its line and cause
        row = {"doc": doc, "ok": False, "error": type(exc).__name__}
    row["seconds"] = round(time.monotonic() - t0, 2)
    report.append(row)


def _keep_built(workspace_root, task: str, report: List[Dict[str, Any]],
                built: Dict[str, Dict[str, Any]]) -> Tuple[str, List[Tuple[str, int]]]:
    """Keep a one-call run's documents in the task's scratch folder OUTSIDE
    the workspace, the manifest last, and answer `(token, sizes)`. A document
    over `REFRESH_DOC_LIMIT` is marked too large on its report row and not
    kept. Raises OSError when the folder cannot be used (or would be inside
    the workspace)."""
    token = secrets.token_hex(8)
    scratch = _task_scratch(workspace_root, task)
    sizes: List[Tuple[str, int]] = []
    try:
        scratch.resolve().relative_to(Path(str(workspace_root)).resolve())
    except ValueError:
        pass  # outside the workspace, as it must be
    else:
        raise OSError("the scratch folder would be inside the workspace")
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    for row in report:
        doc = row["doc"]
        if doc not in built:
            continue
        text = json.dumps(built[doc], separators=(",", ":"), default=str)
        row["chars"] = len(text)
        if len(text) > REFRESH_DOC_LIMIT:
            row["ok"] = False
            row["error"] = "too_large"
            continue
        _refresh_file(scratch, doc).write_text(text, encoding="utf-8")
        sizes.append((doc, len(text)))
    # Written last: the manifest names this run and exactly what it built,
    # so a file an earlier run left for a document this run did not build
    # is never handed back.
    (scratch / REFRESH_MANIFEST).write_text(
        json.dumps({"token": token, "sizes": sizes}), encoding="utf-8")
    return token, sizes


#: cr1#1714: the refresh in ordered calls, each its own door call, so no one
#: call needs more than the door's 150 s. On a cloud seat the folder is read
#: over the computer's mount, and the whole refresh in one call (cr1#1530)
#: timed out there on every scheduled run. The plate is alone (the largest),
#: To confirm alone (the next), and Settings and the Record tab close the run
#: with the Maintenance record and this task's light. Each call reads the event log
#: once for itself; the documents of all of them land in the one scratch
#: folder under the first call's token, in this order, the light last.
REFRESH_STAGES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("plate", ("feed/plate",)),
    ("reads", ("feed/projects", "feed/board", "feed/mine", "feed/mail_rules",
               "feed/people", "feed/brief", "feed/pfollow", "feed/focus", "feed/allopen")),
    ("review", ("feed/review",)),
    ("record", ("feed/settings", "feed/stillyours", "feed/followups", "feed/urgent", "feed/prepx", "feed/week", "status/maintenance")),
)
REFRESH_STAGE_NAMES: Tuple[str, ...] = tuple(s for s, _d in REFRESH_STAGES)
#: What a part that did not finish is called in the light's line.
REFRESH_STAGE_WORDS: Dict[str, str] = {
    "plate": "the plate",
    "reads": "the work streams, the Week board, your lists, the mail rules, the people, the brief, the follow-ups on Today and Focus",
    "review": "To confirm",
    "record": "Settings and the Record tab",
}
REFRESH_STAGE_MISSING_LINE = "Part of the refresh did not finish ({what}), so the Hub keeps its last copy of that part."
#: cr1#1715: a document that was not built, or was too large to pull, is not
#: published; the light names it rather than saying done.
REFRESH_DOC_WORDS: Dict[str, str] = {
    "feed/projects": "the work streams", "feed/board": "the Week board", "feed/mine": "your lists",
    "feed/mail_rules": "the mail rules", "feed/people": "the people", "feed/brief": "the brief",
    "feed/review": "To confirm", "feed/settings": "Settings", "feed/week": "the Record tab",
    "feed/stillyours": "the Still yours? review", "feed/followups": "the follow-ups",
    "feed/pfollow": "the follow-ups on Today",
    "feed/urgent": "Urgent",
    "feed/prepx": "the prep edits",
    "feed/focus": "Focus", "feed/allopen": "All open",
    # Review round 1 of #1837 (must 1): a list that could not be built is named on the light
    WALL_CARRY_DOC: "the off-limits list",
    "status/maintenance": "the Maintenance record",
}
REFRESH_DOCS_FAILED_LINE = "{plate} Not updated: {what}; the Hub keeps its last copy of those."
REFRESH_STAGE_UNKNOWN_LINE = "That is not a part of the refresh; the parts are {names}."
#: cr1#1714: a part called on its own answers by this many seconds, so the
#: door (150 s) never stops it with nothing said. A part still building then
#: answers `ok: false, not_finished: true`; the run goes on, and the last
#: part's light names it. The door's cap is the limit this stays under.
REFRESH_STAGE_BUDGET_S = 120
REFRESH_STAGE_SLOW_LINE = "This part of the refresh did not finish in time; the Hub keeps its last copy of it."
REFRESH_STAGE_AGAIN_LINE = "That part of this refresh has already run, or the refresh has finished; nothing was built."
#: cr1#1722: the same two failures, in the Update's words.
UPDATE_STAGE_MISSING_LINE = "Part of the update did not finish ({what}), so the Hub keeps its last copy of that part."
UPDATE_PLATE_FAILED_LINE = "The plate could not be read, so the Hub was not updated."


def _refresh_builders(workspace_root, now_s: str, since_s: str,
                      built: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Each refresh document's builder, by document. The Maintenance record
    reads To confirm and the work streams as this run built them (`built`)."""
    out: Dict[str, Any] = {"feed/plate": lambda: feed_plate(workspace_root, now_iso=now_s)}
    for doc, name in REFRESH_READS:
        out[doc] = (lambda name=name: globals()[name](workspace_root, now_iso=now_s))

    def _week() -> Dict[str, Any]:
        # The recap's sections are a READ of that module. It is loaded by
        # name because the module also lands the recap document, and a
        # plain import here would make this module (and everything that
        # imports it) count as a document renderer in RTGATE1's derived
        # population. Nothing here renders or lands a document.
        import importlib

        week_sections = getattr(importlib.import_module("friday_" + "wrap_helpers"), "week_sections")
        sections = week_sections(str(workspace_root), since_iso=since_s, now_iso=now_s)
        return feed_week(workspace_root, sections=sections, now_iso=now_s)

    out["feed/week"] = _week
    out["status/maintenance"] = lambda: maintenance_status(workspace_root, now_iso=now_s,
                                                           review=built.get("feed/review"),
                                                           projects=built.get("feed/projects"))
    return out


def _stage_manifest_write(scratch: Path, manifest: Dict[str, Any]) -> None:
    """Atomic (review of #1718): a stop mid-write leaves the last whole
    manifest, never a torn one every later part would refuse."""
    from atomic_write import atomic_write_text

    atomic_write_text(scratch / REFRESH_MANIFEST, json.dumps(manifest, default=str))


def _stage_keep(workspace_root, report: List[Dict[str, Any]], built: Dict[str, Dict[str, Any]],
                manifest: Optional[Dict[str, Any]], task: str = "refresh") -> Dict[str, Any]:
    """cr1#1714: keep one call's documents beside the earlier calls' under the
    same token (a new token for the first call), the manifest last, and
    answer the manifest. A document over `REFRESH_DOC_LIMIT` is marked too
    large and not kept, as `_keep_built` does. Raises OSError when the folder
    cannot be used (or would be inside the workspace)."""
    scratch = _task_scratch(workspace_root, task)
    try:
        scratch.resolve().relative_to(Path(str(workspace_root)).resolve())
    except ValueError:
        pass  # outside the workspace, as it must be
    else:
        raise OSError("the scratch folder would be inside the workspace")
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    man = dict(manifest) if manifest else {"token": secrets.token_hex(8), "sizes": [],
                                           "stages": [], "report": []}
    sizes = [list(s) for s in man.get("sizes") or []]
    for row in report:
        doc = row["doc"]
        if doc not in built:
            continue
        text = json.dumps(built[doc], separators=(",", ":"), default=str)
        row["chars"] = len(text)
        if len(text) > REFRESH_DOC_LIMIT:
            row["ok"] = False
            row["error"] = "too_large"
            continue
        _refresh_file(scratch, doc).write_text(text, encoding="utf-8")
        sizes = [s for s in sizes if s[0] != doc] + [[doc, len(text)]]
    man["sizes"] = sizes
    man["report"] = list(man.get("report") or []) + report
    _stage_manifest_write(scratch, man)
    return man


def _stage_load(workspace_root, man: Dict[str, Any], doc: str, task: str = "refresh") -> Optional[Dict[str, Any]]:
    """A document an earlier call of this run kept, or None."""
    if not any(s[0] == doc for s in man.get("sizes") or []):
        return None
    try:
        val = json.loads(_refresh_file(_task_scratch(workspace_root, task), doc).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return val if isinstance(val, dict) else None


def _refresh_next(name: str, narrow: Optional[List[str]]) -> str:
    """cr1#1722: the part after `name` that has a document to build; `record`
    always runs (it writes the light). With no narrowing, the next in order."""
    later = REFRESH_STAGE_NAMES[REFRESH_STAGE_NAMES.index(name) + 1:]
    return next((s for s in later if s == "record" or narrow is None
                 or any(d in narrow for d in dict(REFRESH_STAGES)[s])), "record")


def _refresh_skip(man: Dict[str, Any], name: str, nxt: str) -> None:
    """cr1#1722: the parts between `name` and `nxt` hold nothing this run
    builds. They are noted as skipped, so the light never calls them unfinished
    and a call for one of them is refused as already run."""
    between = REFRESH_STAGE_NAMES[REFRESH_STAGE_NAMES.index(name) + 1:REFRESH_STAGE_NAMES.index(nxt)]
    if between:
        man["skipped"] = list(man.get("skipped") or []) + list(between)


def refresh_all(workspace_root, *, now_iso=None, since_iso=None,
                status_docs: Optional[List[Any]] = None,
                choices: Optional[List[Any]] = None,
                run_id: Optional[str] = None,
                stage: str = "", token: str = "",
                budget_s: Optional[float] = None,
                task: str = "refresh", only: Optional[List[str]] = None,
                saved: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A WRITER (it builds the plate, which mints display numbers). The
    refresh: the lights a dead run left, the applied choices to clear, the
    plate, every read feed, the Record tab and the Maintenance record.

    cr1#1722: `task: "update"` is the Update's rebuild (`update_build` is its
    door). It keeps its documents in the Update's own scratch folder and
    writes `status/update`, never the Refresh task's light. `only` (given
    with the first part, kept in the run's manifest) names the documents to
    build: a part with none of them builds nothing and reads no log, each
    answer's `next` is the next part that has one (`record` always runs: it
    writes the light), and a part skipped this way is not a part that did
    not finish. `saved` (given with the first part) is what the run's save
    counted, so the light says both.

    cr1#1714: a scheduled run calls it once per part, in `REFRESH_STAGES`
    order: `stage: "plate"` first (it answers the run's `token`), then
    `reads`, `review` and `record`, each with that token, each its own door
    call with the event log read once for it. A part answers `{ok, token,
    stage, next, pages, seconds, line}`; the last (`record`) answers the
    whole run's `{ok, token, pages, parts, light, counts, failed, seconds,
    line}`, and its light is `done` only when every part finished and the
    plate was built. With no `stage`, every part runs in this one process
    and the answer is the last part's (in-process callers and tests).

    The documents are kept in a scratch folder outside the workspace and are
    NOT in any answer; `refresh_part` hands each part back. A read that fails
    is named and left out, so the page keeps its old copy."""
    import time

    now = _now(now_iso)
    now_s = _iso(now)
    since_s = str(since_iso or "") or _iso(now - _dt.timedelta(days=7))
    started = time.monotonic()
    want = str(stage or "").strip()
    if want and want not in REFRESH_STAGE_NAMES:
        return {"ok": False, "stage": want,
                "line": REFRESH_STAGE_UNKNOWN_LINE.format(names=", ".join(REFRESH_STAGE_NAMES))}
    stages = [want] if want else list(REFRESH_STAGE_NAMES)
    tsk = "update" if str(task or "") == "update" else "refresh"
    light_doc = "status/" + tsk
    man: Optional[Dict[str, Any]] = None
    if stages[0] != "plate":
        man = _refresh_manifest(_task_scratch(workspace_root, tsk))
        tok = str(token or "")
        if not _REFRESH_TOKEN_RE.match(tok) or str(man.get("token") or "") != tok or "stages" not in man:
            return {"ok": False, "stage": want, "line": ONE_CALL_GONE_LINES[tsk]}
        ran = (list(man.get("stages") or []) + list(man.get("not_finished") or [])
               + list(man.get("skipped") or []))
        if want == "record" and light_doc in [str(s[0]) for s in man.get("sizes") or []]:
            # cr1#1727 item 4: a retried record answers the finished run, so
            # its parts are still pulled; nothing is built or written again
            return _refresh_record_again(workspace_root, man, tsk)
        if want in ran or "record" in ran:
            # review of #1718, item 3: a part runs once, and nothing after the light
            return {"ok": False, "stage": want, "line": REFRESH_STAGE_AGAIN_LINE}
    statuses = [s for s in (_flat_status(d) for d in (status_docs or [])) if s]
    settle: List[Dict[str, Any]] = []
    prune: Dict[str, Any] = {"writes": []}
    answer: Dict[str, Any] = {}
    # cr1#1722: the documents this run builds. Given with the first part and
    # kept in the manifest, so every later part builds from the same list.
    if stages[0] == "plate":
        narrow = None if only is None else [str(d) for d in only if str(d) in UPDATE_DOCS]
    else:
        narrow = (man or {}).get("only")
        narrow = None if narrow is None else [str(d) for d in narrow]
    for name in stages:
        built: Dict[str, Dict[str, Any]] = {}
        report: List[Dict[str, Any]] = []
        if name == "plate":
            # This run wrote its own light to running before this call; settling the
            # copy read before that would overwrite it and break the finish's pin.
            settle = [d for d in settle_statuses(workspace_root, statuses, now_iso=now_s)
                      if str(d.get("leg") or "") != tsk]
            prune = plan_prune_choices(workspace_root, list(choices or []), now_iso=now_s)
        if name == "record" and man is not None:
            for doc in ("feed/review", "feed/projects"):
                kept = _stage_load(workspace_root, man, doc, tsk)
                if kept is not None:
                    built[doc] = kept
        docs = dict(REFRESH_STAGES)[name]
        if narrow is not None:
            docs = tuple(d for d in docs if d in narrow)

        def _build(built=built, report=report, docs=docs) -> None:
            with _one_log_read():
                # cr1#1638: the plate is built inside the one log read too. Its own
                # writes (display numbers) change the file, and the read is keyed on
                # the file's size and time, so what it reads after a write is new.
                makers = _refresh_builders(workspace_root, now_s, since_s, built)
                for doc in docs:
                    _one_call_build(built, report, doc, makers[doc])

        if not docs:
            pass  # cr1#1722: nothing of this part is wanted; no log is read
        elif not want:
            _build()
        else:
            # cr1#1714: one part on its own answers inside its budget. The
            # build runs on a worker; a build past the budget is left to the
            # process's end (as the door's own stop would), and nothing it
            # built is kept.
            import threading

            box: Dict[str, Any] = {}

            def _work() -> None:
                try:
                    _build()
                except BaseException as exc:  # noqa: BLE001 - handed to the caller below
                    box["exc"] = exc

            worker = threading.Thread(target=_work, name="cr-refresh-" + name, daemon=True)
            worker.start()
            worker.join(REFRESH_STAGE_BUDGET_S if budget_s is None else float(budget_s))
            if worker.is_alive():
                return _refresh_not_finished(workspace_root, name, man, token=token,
                                             started=started, now_s=now_s, run_id=run_id,
                                             task=tsk, only=narrow, saved=saved)
            if "exc" in box:
                raise box["exc"]
        if name == "record":
            _refresh_keep_page_maint(built, report, statuses)
        if name == "plate" and tsk == "update":
            # cr1#1837: every Update, a narrow one too, carries the off-limits
            # list as built now (after the save, so a saved mail rule is in it),
            # in its own first part. It reads no log, so it costs a narrow run little.
            _one_call_build(built, report, WALL_CARRY_DOC,
                            lambda: feed_wall(workspace_root, now_iso=now_s)["doc"])
            docs = tuple(docs) + ((WALL_CARRY_DOC,) if WALL_CARRY_DOC in built else ())
        keep = {d: built[d] for d in docs if d in built}
        if name == "plate":
            plate = keep.get("feed/plate")
            # The plate counts as built when it is kept by the same rule the
            # keep applies (built, and small enough to pull), cr1#1638.
            plate_ok = plate is not None and len(json.dumps(plate, separators=(",", ":"), default=str)) <= REFRESH_DOC_LIMIT
            counts: Dict[str, Any] = {}
            line = REFRESH_PLATE_FAILED_LINE
            if narrow is not None and "feed/plate" not in narrow:
                # cr1#1722: the plate was not asked for, so it did not fail
                plate_ok, line = True, ""
            if plate_ok and plate is not None:
                day_ago = now - _dt.timedelta(hours=24)
                closed = 0
                for c in (plate.get("changes") or {}).get("closed") or []:
                    try:
                        if _now(c.get("ts")) >= day_ago:
                            closed += 1
                    except Exception:  # noqa: BLE001 - an undated close is not counted
                        pass
                counts = {"plate": int(plate.get("n_rows") or 0), "closed": closed}
                # cr1#1638: the real window, never "overnight".
                line = f"{counts['plate']} on the plate, {closed} closed in the last day."
        nxt = ""
        if name != "record":
            nxt = _refresh_next(name, narrow)
        try:
            man = _stage_keep(workspace_root, report, keep, man, tsk)
            if name == "plate":
                man.update(plate_ok=plate_ok, counts=counts, plate_line=line)
                if tsk == "update":
                    man.update(task=tsk, only=narrow, saved=dict(saved or {}))
            man["stages"] = list(man.get("stages") or []) + [name]
            if want and nxt:
                _refresh_skip(man, name, nxt)
            if name == "record":
                answer = _refresh_finish(workspace_root, man, now_s=now_s, run_id=run_id, task=tsk)
            _stage_manifest_write(_task_scratch(workspace_root, tsk), man)
        except OSError as exc:
            return {"ok": False, "stage": name, "line": "The refresh could not keep its answers on this computer.",
                    "error": type(exc).__name__, "pages": report}
        if name != "record":
            answer = {"ok": True, "token": man["token"], "stage": name, "next": nxt,
                      "pages": report, "line": ""}
    answer["seconds"] = round(time.monotonic() - started, 2)
    answer["settle"] = settle
    answer["prune"] = prune
    return answer


def _refresh_not_finished(workspace_root, name: str, man: Optional[Dict[str, Any]], *,
                          token: str, started: float, now_s: str = "",
                          run_id: Optional[str] = None, task: str = "refresh",
                          only: Optional[List[str]] = None,
                          saved: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """cr1#1714: a part that ran past its budget. The run's manifest notes it
    (the plate's call makes the run's manifest here, empty, so the later
    parts still have a token), and the answer says the part did not finish.
    The last part's light names it."""
    import time

    try:
        if not man:
            man = {"token": secrets.token_hex(8), "sizes": [], "stages": [], "report": [], "plate_ok": False}
            if task == "update":
                man.update(task=task, only=only, saved=dict(saved or {}))
        man = dict(man)
        man["not_finished"] = list(man.get("not_finished") or []) + [name]
        if name != "record":
            _refresh_skip(man, name, _refresh_next(name, man.get("only")))
        scratch = _task_scratch(workspace_root, task)
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        _stage_manifest_write(scratch, man)
    except OSError as exc:
        return {"ok": False, "stage": name, "line": "The refresh could not keep its answers on this computer.",
                "error": type(exc).__name__}
    if name == "record":
        # review of #1718, item 1: the last part past its budget still writes
        # the light (naming it) and hands back what the earlier parts built.
        try:
            fin = _refresh_finish(workspace_root, man, now_s=now_s or _iso(_now()), run_id=run_id, task=task)
            _stage_manifest_write(_task_scratch(workspace_root, task), man)
        except OSError as exc:
            return {"ok": False, "stage": name, "line": "The refresh could not keep its answers on this computer.",
                    "error": type(exc).__name__}
        fin.update(not_finished=True, seconds=round(time.monotonic() - started, 2))
        return fin
    idx = REFRESH_STAGE_NAMES.index(name)
    out: Dict[str, Any] = {"ok": False, "not_finished": True, "token": man["token"], "stage": name,
                           "line": REFRESH_STAGE_SLOW_LINE,
                           "seconds": round(time.monotonic() - started, 2)}
    if idx + 1 < len(REFRESH_STAGE_NAMES):
        out["next"] = _refresh_next(name, man.get("only"))
    return out


def _refresh_record_again(workspace_root, man: Dict[str, Any], task: str = "refresh") -> Dict[str, Any]:
    """cr1#1727 item 4: the finished run's own answer, read back from its
    manifest and its kept light; nothing is built or written."""
    try:
        light = json.loads(_refresh_file(_task_scratch(workspace_root, task), "status/" + task)
                           .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "stage": "record", "line": ONE_CALL_GONE_LINES[task]}
    sizes = [(str(s[0]), int(s[1])) for s in man.get("sizes") or []]
    report = list(man.get("report") or [])
    done = list(man.get("stages") or [])
    return {
        "ok": str(light.get("state") or "") == "done",
        "token": man["token"],
        "stage": "record",
        "again": True,
        "pages": report,
        "parts": _task_parts(task, sizes),
        "wall_part": 0 if _task_parts(task, sizes)[:1] == [[WALL_CARRY_DOC]] else None,
        "light": light,
        "counts": dict(man.get("counts") or {}),
        "missing": [s for s in REFRESH_STAGE_NAMES if s not in done and s not in (man.get("skipped") or [])],
        "failed": [r["doc"] for r in report if not r.get("ok")],
        "line": str(light.get("line") or ""),
    }


def _refresh_keep_page_maint(built: Dict[str, Dict[str, Any]], report: List[Dict[str, Any]],
                             statuses: List[Dict[str, Any]]) -> None:
    """cr1#666: an `idle` answer over a page that says `done` is a read that
    failed; the page keeps what it has."""
    page_maint = next((d for d in statuses if str(d.get("leg") or "") == "maintenance"), None)
    maint = built.get("status/maintenance")
    if maint is not None and str(maint.get("state") or "") == "idle" \
            and page_maint is not None and str(page_maint.get("state") or "") == "done":
        # cr1#1489: a kept copy that still carries the hidden chats note is
        # sent back without it; any other is left on the page as it is.
        clean = scrub_chats_note(page_maint)
        if clean is not None:
            built["status/maintenance"] = clean
        else:
            del built["status/maintenance"]
        for row in report:
            if row["doc"] == "status/maintenance":
                row["kept_page"] = True
                if clean is not None:
                    row["scrubbed"] = True


def _refresh_finish(workspace_root, man: Dict[str, Any], *, now_s: str,
                    run_id: Optional[str], task: str = "refresh") -> Dict[str, Any]:
    """The last part's answer: this task's light, kept last (cr1#1638: the
    light rides with the documents, so the finish is the same batch as the
    feeds), and the whole run's parts. The light is `done` only when every
    part of `REFRESH_STAGES` finished and the plate was built (cr1#1714)."""
    done = list(man.get("stages") or [])
    missing = [s for s in REFRESH_STAGE_NAMES if s not in done and s not in (man.get("skipped") or [])]
    plate_ok = bool(man.get("plate_ok"))
    updating = task == "update"
    if missing:
        what = "; ".join(REFRESH_STAGE_WORDS[s] for s in missing)
        line, state = (UPDATE_STAGE_MISSING_LINE if updating else REFRESH_STAGE_MISSING_LINE).format(what=what), "error"
    elif not plate_ok:
        line, state = (UPDATE_PLATE_FAILED_LINE if updating else REFRESH_PLATE_FAILED_LINE), "error"
    else:
        line, state = str(man.get("plate_line") or ""), "done"
        bad = [str(r.get("doc")) for r in man.get("report") or []
               if not r.get("ok") and str(r.get("doc")) in REFRESH_DOC_WORDS]
        if bad:
            what = ", ".join(REFRESH_DOC_WORDS[d] for d in bad)
            line, state = REFRESH_DOCS_FAILED_LINE.format(plate=line, what=what), "error"
    if updating:
        state, line = _update_light_words(man, state, line)
    light = status_doc(workspace_root, task, state, line, now_iso=now_s, run_id=run_id,
                       stop_reason=line if state == "error" else None)
    if updating:
        # cr1#1722: what the run saved and what it rebuilt, beside its line
        light["saved"] = _update_saved(man)
        light["rebuilt"] = "all" if man.get("only") is None else sorted(str(d) for d in man.get("only") or [])
        # fix check of round 1: never after a failed document, named in the
        # words table or not (fix check of #1768: feed/followups had no words)
        failed_any = any(not r.get("ok") for r in man.get("report") or [])
        if man.get("only") is None and state == "done" and not failed_any:
            # a whole rebuild that finished: the time a later run reads (cr1#1809)
            light[UPDATE_FULL_FIELD] = now_s
        else:
            held = _full_at_of(_update_state(workspace_root, str(run_id or "")).get(UPDATE_FULL_FIELD), _parse(now_s))
            if held:
                light[UPDATE_FULL_FIELD] = held
    row = {"doc": "status/" + task, "ok": True, "seconds": 0.0}
    man.update(_stage_keep(workspace_root, [row], {"status/" + task: light}, man, task))
    sizes = [(str(s[0]), int(s[1])) for s in man.get("sizes") or []]
    report = list(man.get("report") or [])
    parts = _task_parts(task, sizes)
    return {
        "ok": state == "done",
        "token": man["token"],
        "stage": "record",
        "pages": report,
        "parts": parts,
        # cr1#1837: the number of the part that is the off-limits list alone, or null
        "wall_part": 0 if parts and parts[0] == [WALL_CARRY_DOC] else None,
        "light": light,
        "counts": dict(man.get("counts") or {}),
        "missing": missing,
        "failed": [r["doc"] for r in report if not r.get("ok")],
        "line": line,
    }


def _saved_docs(reads_dir: str, coll: str) -> List[Tuple[str, Dict[str, Any], Optional[int]]]:
    """Every document a page read saved under `<reads_dir>/<coll>/` (a
    `get`, `list` or `query` with `out_dir`): `(doc_id, fields, version)`."""
    out: List[Tuple[str, Dict[str, Any], Optional[int]]] = []
    folder = Path(reads_dir) / coll
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.json")):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        fields = dict(raw["data"]) if isinstance(raw.get("data"), dict) else dict(raw)
        version = _saved_version(reads_dir, coll, f.stem)
        out.append((f.stem, fields, version))
    return out


def refresh_start(reads_dir: str, out_dir: str, *, now_iso=None, leg: str = "refresh",
                  whole: bool = False) -> Dict[str, Any]:
    """cr1#1638: the refresh's page-side start, run where the page's database
    tool runs, from the page's own reads saved as files (`list` on `status`
    and `query` on applied `choices`, each with `out_dir`), so no page
    document passes through the run's typing. Every time is this code's clock.

    Answers `{ok, stop, run_id, line, batches}`. `stop: true` when this task's
    light is held by a live refresh (claimed by another run, inside the
    timeout): nothing is to be written. Otherwise `batches[0]` is the claim of
    this task's light, alone, pinned to the version read (a page press left
    it running with the page's run id: that id is this run's; else a new
    id), and the batches after it settle the lights a dead run left and clear
    the old applied choices, every entry pinned to its version. A refused
    claim means another refresh claimed first: stop.

    cr1#1722: `leg: "update"` is the Update's start (`update-start` on this
    file's command line): the same claim, settle and clear against
    `status/update`. Its answer also carries `scope`: `touched` when the page
    fired this run after a press (the page's running light says so), else
    `all`. The choices it clears are the applied ones only, as before.

    Early review of #1776 (must 2): `whole: true` (`update-start --whole`, the
    Morning's update part) is always `all`, even on a page light that asked
    for `touched`. The Update's claim names its `scope`, and a busy stop
    behind a claim that is not `all` (or names none) answers
    `UPDATE_BUSY_NARROW_LINE`, so the Morning never counts a narrow update
    as the day's first whole one."""
    now = _now(now_iso)
    now_s = _iso(now)
    leg = "update" if str(leg or "") == "update" else "refresh"
    own_leg = leg  # the loop below names each settled light's leg
    statuses = _saved_docs(reads_dir, "status")
    choices = _saved_docs(reads_dir, "choices")
    if leg == "update":
        # the Update's page read holds waiting choices too; only applied ones are cleared
        choices = [(d, f, v) for d, f, v in choices if f.get("applied") is True]
    own = next(((f, v) for d, f, v in statuses if d == leg), (None, None))
    light, light_v = own
    run_id = ""
    scope = "all"
    if isinstance(light, dict) and str(light.get("state") or "") == "running" \
            and not running_timed_out(light, now_iso=now_s, minutes=REFRESH_CLAIM_MINUTES):
        if light.get("claimed_by"):
            busy = REFRESH_BUSY_LINE
            if leg == "update":
                busy = UPDATE_BUSY_LINE if str(light.get("scope") or "") == "all" else UPDATE_BUSY_NARROW_LINE
            return {"ok": True, "stop": True, "run_id": "", "line": busy, "batches": []}
        if light.get("by_page") and light.get("run_id"):
            run_id = str(light["run_id"])
            if leg == "update" and str(light.get("scope") or "") == "touched" and not whole:
                scope = "touched"
    run_id = run_id or leg + "-" + secrets.token_hex(6)
    # cr1#1809: the last whole rebuild, as the page's own light keeps it. With
    # none on record, or one an hour old, a press's run rebuilds everything.
    full_at = _full_at_of(light.get(UPDATE_FULL_FIELD), now) if isinstance(light, dict) and leg == "update" else ""
    if scope == "touched" and (not full_at or (now - _parse(full_at)) >= _dt.timedelta(minutes=UPDATE_FULL_EVERY_MINUTES)):
        scope = "all"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    claim = status_doc(None, leg, "running", "", now_iso=now_s, run_id=run_id)
    if full_at:
        claim[UPDATE_FULL_FIELD] = full_at
    # Review of #1664 (item 1): the claim never carries `by_page`. On the page
    # that flag means a press not yet started, and 8 minutes on (NO_START_MS)
    # the page would say the task did not start and let Go fire a second run.
    claim["claimed_by"] = run_id
    if leg == "update":
        claim["scope"] = scope  # early review of #1776 (must 2): what a busy stop behind it reads
    claim_file = out / ("status__" + leg + ".json")
    claim_file.write_text(json.dumps(claim, separators=(",", ":")), encoding="utf-8")
    first: Dict[str, Any] = {"op": "set", "collection": "status", "doc_id": leg, "file_path": str(claim_file)}
    if light_v is not None:
        first["if_version"] = light_v
    rest: List[Dict[str, Any]] = []
    versions = {d: v for d, _f, v in statuses}
    for doc in settle_statuses(None, [dict(f, leg=f.get("leg") or d) for d, f, _v in statuses if d != leg],
                               now_iso=now_s):
        leg = str(doc.get("leg") or "")
        target = out / f"status__{leg}.json"
        target.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        w: Dict[str, Any] = {"op": "set", "collection": "status", "doc_id": leg, "file_path": str(target)}
        if versions.get(leg) is not None:
            w["if_version"] = versions[leg]
        rest.append(w)
    cversions = {d: v for d, _f, v in choices}
    prune = plan_prune_choices(None, [dict(f, id=f.get("id") or d) for d, f, _v in choices], now_iso=now_s)
    for w in prune["writes"]:
        w = dict(w)
        if cversions.get(w["doc_id"]) is not None:
            w["if_version"] = cversions[w["doc_id"]]
        rest.append(w)
    batches = [[first]] + [rest[i:i + REFRESH_BATCH_MAX] for i in range(0, len(rest), REFRESH_BATCH_MAX)]
    answer = {"ok": True, "stop": False, "run_id": run_id, "line": "", "batches": batches,
              "n_settle": len(rest) - len(prune["writes"]), "n_prune": len(prune["writes"])}
    if own_leg == "update":
        answer["scope"] = scope
        answer[UPDATE_FULL_FIELD] = full_at
        # cr1#1701 (A): read in code here, from the saved page
        answer["inbox_senders"] = _page_inbox_senders(reads_dir)
        # Review of #1817: a check over what the start answered, which the
        # claim recomputes from what the run passes on
        answer["start_check"] = update_start_check(run_id, scope, full_at, answer["inbox_senders"])
    return answer


#: Review of #1817: `update-start` runs where the page's saved reads are (on a
#: device seat, the cloud container) and `update_claim` where the workspace is
#: (on that seat, the computer, through device_bash): two machines that share
#: no folder. So nothing is kept between them: the start answers a check over
#: what it answered, the run passes the values and the check on as they came,
#: and the claim recomputes it. A value the run invented, changed or dropped
#: fails the check. It is a check on the run's copying, not a secret: anyone
#: can compute it.
UPDATE_START_CHECK_SALT = "cr1-update-start-v1"
#: Review of #1821: the claim keeps this when the start's check did not hold
#: (missing, malformed or another answer), and `update_finish` counts it on
#: the receipt, so a miscopy that turned the run whole is on the record.
UPDATE_CHECK_FAILED = "check_failed"


def update_start_check(run_id: Any, scope: Any, full_at: Any, inbox_senders: Any) -> str:
    """The check `update-start` answers and `update_claim` recomputes: the
    SHA-256 of the four values in one fixed form (sorted keys, no spaces). Pure."""
    # an empty map and a map left out are the same answer: no senders
    body = json.dumps({"full_at": full_at, "inbox_senders": {} if inbox_senders is None else inbox_senders,
                       "run_id": run_id, "scope": scope},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((UPDATE_START_CHECK_SALT + "|" + body).encode("utf-8")).hexdigest()


#: cr1#1722 step 2: the Morning run's parts, in the order it runs them. The
#: brief is last: its post is the chat's answer.
MORNING_PARTS: Tuple[str, ...] = ("inbox", "prep", "update", "brief")
MORNING_PART_WORDS: Dict[str, str] = {"inbox": "the inbox", "prep": "meeting prep",
                                      "update": "the day's first update", "brief": "the morning brief"}
MORNING_MARK_STATES: Tuple[str, ...] = ("done", "error", "skipped")
#: Review round 1: a part's start is marked too. It records nothing as done;
#: it moves the light's `at` (the run is alive) and names the part under way.
MORNING_STARTED = "started"
#: Decision F (cr1#1722): the parts a page press may ask for. Hub Prep and Hub
#: Inbox are retired; a prep or inbox press fires Hub Morning with its part on
#: the running light, as the Update's `scope: touched`. A press never asks for
#: the brief or the whole build, and what it runs never counts toward the
#: morning being done.
MORNING_PRESS_PARTS: Tuple[str, ...] = ("inbox", "prep")
MORNING_MEETING_ID_MAX = 200
#: Early review of #1776 (must 1): a part counts toward the morning only when it
#: was marked on the workspace's current day, in the workspace's zone (the page
#: counts by the owner's calendar day too). A Go pressed in the evening is that
#: evening's, and the next morning's 6:45 fire runs every part again.
MORNING_BUSY_LINE = "The morning run is already going; this one stopped without changing anything."
MORNING_DONE_LINE = "The morning is already done; nothing was left to run."
MORNING_READY_LINE = "Morning ready."
MORNING_PARTIAL_LINE = "Morning ready, except {what}."
#: Ruling 129 (coordinator, reversible), item 3: the morning's update part found
#: Hub Update already running (the update's own busy stop). That run builds the
#: whole page, so the part counts as done and the 7:45 fire never reruns it. It
#: is marked `handed`, and the morning's last line says so: the morning never
#: claims a fresh page it did not build. The page's status line then follows that
#: update's own light ("Updating…", "Updated 7:34 AM", or its "Couldn't update").
MORNING_UPDATE_HANDED_LINE = "The update that was already running builds the page."
MORNING_READY_HANDED_LINE = "Morning ready; the update that was already running builds the page."
MORNING_NOT_HELD_LINE = "Another morning run holds the light now; this mark was not written."
#: Early review of #1776 (must 2), amending ruling 129a: the update part stopped
#: behind an update that saves only the latest presses. That is not the day's
#: first whole update, so the part is not done and the next morning fire runs it.
MORNING_UPDATE_NARROW_LINE = ("Another update was saving your latest changes only, so the day's first whole "
                              "update is left for the next morning run.")
#: Early review of #1776 (should 6): a fire that found a press's run holding the
#: light leaves `due` on it, and that run does the morning when its press is done.
MORNING_DUE_LINE = "A press's run is going now; it does the morning when its press is done."
#: cr1#1782: the prefix of the run id `morning_start` mints for a scheduled
#: (whole-morning) run; a page's own run carries the page's id.
MORNING_RUN_PREFIX = "morning-"
#: Review round 1: a press that names no inbox or prep part runs nothing.
MORNING_PRESS_NONE_LINE = "That press named no inbox or prep part, so nothing was run."
#: A press's own last line, when its part left none: never "Morning ready."
MORNING_PRESS_DONE_LINES: Dict[str, str] = {"inbox": "The inbox is checked.", "prep": "Meeting prep is ready."}
MORNING_PRESS_BAD_LINES: Dict[str, str] = {"inbox": "The inbox check did not finish.",
                                           "prep": "Meeting prep did not finish."}


#: Re-review of #1776: when the workspace names a zone this machine cannot
#: load, its day cannot be told, and UTC's day would count a 10:43 PM Go west
#: of UTC as the next morning. Then only marks this recent count: the 7:45 and
#: 8:45 fires still finish a cut-off morning, and an evening Go stays out.
MORNING_UNZONED_HOURS = 3
MORNING_UNZONED_NOTE = " The workspace's time zone could not be read, so only the last three hours counted."


def _morning_zone(tz: Any) -> Optional[_dt.tzinfo]:
    """The workspace's zone: a tzinfo as given, an IANA name (`load_config`'s
    `tz`) read through zoneinfo, UTC for a UTC name or none, and None for a
    named zone this machine cannot load (never UTC in its place)."""
    if isinstance(tz, _dt.tzinfo):
        return tz
    name = str(tz or "").strip()
    if not name or name.upper() == "UTC":
        return _dt.timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - a named zone not loaded: the day is unreadable
        return None


def _morning_kept(done: Any, now: _dt.datetime, zone: Any = None) -> Dict[str, Dict[str, Any]]:
    """The parts a morning light records, those of this morning only (marked
    on `now`'s day in the workspace's `zone`, must 1 of the early review of
    #1776), each `{state, at, line}`."""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(done, dict):
        return out
    z = _morning_zone(zone)
    today = now.astimezone(z).date() if z is not None else None
    for part in MORNING_PARTS:
        row = done.get(part)
        if not isinstance(row, dict) or str(row.get("state") or "") not in MORNING_MARK_STATES:
            continue
        at = _parse(row.get("at"))
        if at is None or at > now:
            continue
        if z is None:
            if now - at >= _dt.timedelta(hours=MORNING_UNZONED_HOURS):
                continue
        elif at.astimezone(z).date() != today:
            continue
        out[part] = {"state": str(row["state"]), "at": _iso(at), "line": str(row.get("line") or "")[:300]}
        if row.get("handed") is True:
            out[part]["handed"] = True  # ruling 129: another update built the page
    return out


def _morning_handed_holds(done: Dict[str, Dict[str, Any]], statuses: List[Any], now: _dt.datetime) -> Dict[str, Dict[str, Any]]:
    """Early review of #1776 (must 2): an update part marked `handed` counts
    only while the update it was handed to is live or ended `done` after the
    hand-over; once that update failed (or died), the part is not done and the
    next morning fire runs it. With no update light in the reads, as kept."""
    row = done.get("update") or {}
    if not row.get("handed"):
        return done
    upd = next((f for d, f, _v in statuses if d == "update"), None)
    if not isinstance(upd, dict):
        return done
    st = str(upd.get("state") or "")
    at, handed_at = _parse(upd.get("at")), _parse(row.get("at"))
    live = st == "running" and not running_timed_out(upd, now_iso=_iso(now), minutes=REFRESH_CLAIM_MINUTES)
    ended = st == "done" and at is not None and handed_at is not None and at >= handed_at
    if live or ended:
        return done
    return {k: v for k, v in done.items() if k != "update"}


def _morning_rest(done: Dict[str, Dict[str, Any]], now: _dt.datetime, refused: str = "") -> Dict[str, Any]:
    """The morning light with no run on it: done ("Morning ready.", at the
    time its last part ended) when every part is done this morning, else
    idle; an error carrying `refused` for a press that was refused. What
    was done this morning is kept."""
    if refused:
        doc = status_doc(None, "morning", "error", refused, now_iso=_iso(now), stop_reason=refused)
    elif all(done.get(p, {}).get("state") == "done" for p in MORNING_PARTS):
        last = max(str(done[p]["at"]) for p in MORNING_PARTS)
        doc = status_doc(None, "morning", "done", MORNING_READY_LINE, now_iso=last)
    else:
        doc = status_doc(None, "morning", "idle", "", now_iso=_iso(now))
    doc["done"] = done
    return doc


def _morning_write(out_dir: str, light: Dict[str, Any], version: Optional[int]) -> List[Dict[str, Any]]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    f = out / "status__morning.json"
    f.write_text(json.dumps(light, separators=(",", ":")), encoding="utf-8")
    w: Dict[str, Any] = {"op": "set", "collection": "status", "doc_id": "morning", "file_path": str(f)}
    if version is not None:
        w["if_version"] = version
    return [w]


def morning_start(reads_dir: str, out_dir: str, *, now_iso=None, tz: Any = None) -> Dict[str, Any]:
    """cr1#1722 step 2: the Morning run's start, run where the page's database
    tool runs, from the page's `list` on `status` saved as files (as
    `refresh_start`). Answers `{ok, stop, run_id, parts, line, batches}`.

    `parts` is what this run does, in order. The page's press names its own
    (`by_page` with `parts` on the running light: a press asks for those even
    when done); otherwise every part not done this morning, so the 7:45 fire
    finishes only what a cut-off 6:45 run left: a claimed light with no mark
    for `MORNING_STALE_MINUTES` is a dead run's, and this run takes over from
    the first part not marked done, never repeating a done one. `stop: true`:
    another live run holds the light (nothing to write), or nothing is left
    (`nothing: true`), or a press named no inbox or prep part (`refused:
    true`). On a stop, `batches` is empty unless the page's own running
    light has to be put back (a Go on a morning already done, a refused
    press): then `batches[0]` is that one write, to send before stopping.
    Otherwise `batches[0]` is the claim of `status/morning`, pinned to the
    version read; a refusal means another run claimed first: stop.

    `tz` is the workspace's zone (`load_config`'s `tz`): a part counts only
    on the day it was marked there. A busy stop behind a press's run writes
    `due` on its light (pinned), so that run does the morning after its press."""
    now = _now(now_iso)
    now_s = _iso(now)
    statuses = _saved_docs(reads_dir, "status")
    own = next(((f, v) for d, f, v in statuses if d == "morning"), (None, None))
    light, light_v = own
    light = light if isinstance(light, dict) else {}
    done = _morning_handed_holds(_morning_kept(light.get("done"), now, tz), statuses, now)
    run_id = ""
    asked: Optional[List[str]] = None
    meeting = ""
    if str(light.get("state") or "") == "running" and not running_timed_out(
            light, now_iso=now_s, minutes=RUNNING_TIMEOUT_BY_LEG["morning"]):
        if light.get("claimed_by"):
            if light.get("pressed"):
                # should 6: a fire behind a press's run; that run does the morning when its press ends
                due = [] if light.get("due") else [_morning_write(out_dir, dict(light, due=True), light_v)]
                return {"ok": True, "stop": True, "due": True, "run_id": "", "parts": [], "line": MORNING_DUE_LINE,
                        "batches": due}
            return {"ok": True, "stop": True, "run_id": "", "parts": [], "line": MORNING_BUSY_LINE, "batches": []}
        # second review of #1776 (item 2): the page gives up on its own press after
        # MORNING_PAGE_NO_START_MINUTES (its NO_START_MS) and lets Go fire again; a
        # press light older than that is unanswered and never narrows this run
        if light.get("by_page") and light.get("run_id") and not running_timed_out(
                light, now_iso=now_s, minutes=MORNING_PAGE_NO_START_MINUTES):
            run_id = str(light["run_id"])
            wanted = light.get("parts")
            if isinstance(wanted, list):
                # decision F: a press asks for prep or the inbox, never the brief or the build
                asked = [p for p in MORNING_PRESS_PARTS if p in {str(x) for x in wanted}]
                if not asked:
                    # review round 1: a press with no part of its own (`[]`, `["brief"]`) is
                    # refused, never read as the whole morning; the page's light is put back
                    return {"ok": True, "stop": True, "refused": True, "run_id": "", "parts": [],
                            "line": MORNING_PRESS_NONE_LINE,
                            "batches": [_morning_write(out_dir, _morning_rest(done, now, MORNING_PRESS_NONE_LINE), light_v)]}
                mid = light.get("meeting_id")
                if "prep" in asked and isinstance(mid, str) and 0 < len(mid.strip()) <= MORNING_MEETING_ID_MAX:
                    meeting = mid.strip()
    parts = asked if asked else [p for p in MORNING_PARTS if done.get(p, {}).get("state") != "done"]
    if not parts:
        # review round 1 (must 2): Go on a morning already done left the page's
        # own "Started." light running for good. It is put back to done, at the
        # time the morning's last part ended, so the page reads "Morning ready".
        # fix check of #1776: a dead run's own claim too (it did every part and
        # died before its finish), not only the page's light. A live claim stopped
        # as busy above, so any claim that reaches here is a dead one.
        back = [_morning_write(out_dir, _morning_rest(done, now), light_v)] \
            if str(light.get("state") or "") == "running" and (light.get("by_page") or light.get("claimed_by")) else []
        return {"ok": True, "stop": True, "nothing": True, "run_id": "", "parts": [], "line": MORNING_DONE_LINE,
                "batches": back}
    run_id = run_id or MORNING_RUN_PREFIX + secrets.token_hex(6)
    claim = status_doc(None, "morning", "running", "", now_iso=now_s, run_id=run_id)
    claim.update(claimed_by=run_id, claimed_at=now_s, parts=parts, done=done, pressed=bool(asked))
    if asked and light.get("due"):
        # cr1#1782: a scheduled run this press's light was written over left
        # it the morning; the claim keeps that, so the finish carries on
        claim["due"] = True
    if meeting:
        claim["meeting_id"] = meeting
    answer = {"ok": True, "stop": False, "run_id": run_id, "parts": parts, "pressed": bool(asked), "line": "",
              "batches": [_morning_write(out_dir, claim, light_v)]}
    if meeting:
        answer["meeting_id"] = meeting
    return answer


def _morning_held(reads_dir: str, run_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    own = next(((f, v) for d, f, v in _saved_docs(reads_dir, "status") if d == "morning"), (None, None))
    light, version = own
    if not isinstance(light, dict) or not run_id or str(light.get("claimed_by") or "") != str(run_id):
        return None, version
    return light, version


def _morning_lost(reads_dir: str, out_dir: str, run_id: str) -> Dict[str, Any]:
    """A mark by a run that no longer holds the light: `held: false`, and the
    caller stops. cr1#1782: when the run is a scheduled one (its own minted
    id) and the light now held is a press's, the page wrote the press over
    this run's claim (its read then set has no version). That press's run
    does only its own part, so this run leaves it the morning: `batches[0]`
    sets `due` on the press's light, pinned to the version read, and the
    press's run does the rest of the morning when its press is done.

    Takes effect only once the page's saved reads carry each document's
    version (#1805, cr1#1763): until then the write carries no `if_version`,
    the page's database refuses it (the light already exists), and the
    morning waits for the next fire, as it did before."""
    raw, raw_v = next(((f, v) for d, f, v in _saved_docs(reads_dir, "status") if d == "morning"), (None, None))
    press = isinstance(raw, dict) and str(raw.get("state") or "") == "running" and (
        raw.get("pressed") is True or (raw.get("by_page") and isinstance(raw.get("parts"), list) and bool(raw.get("parts"))))
    if press and str(run_id or "").startswith(MORNING_RUN_PREFIX) and not raw.get("due"):
        return {"ok": False, "held": False, "due": True, "line": MORNING_DUE_LINE,
                "batches": [_morning_write(out_dir, dict(raw, due=True), raw_v)]}
    return {"ok": False, "held": False, "batches": [], "line": MORNING_NOT_HELD_LINE}


def morning_mark(reads_dir: str, out_dir: str, *, run_id: str, part: str, state: str, line: str = "",
                 now_iso=None, tz: Any = None) -> Dict[str, Any]:
    """cr1#1722 step 2: one part of the Morning run has started or ended.
    From a fresh `get` on `status/morning` saved under `<reads_dir>/status/`.
    `state: "started"` records nothing as done: the light's `at` moves (the
    run is alive, so the 35-minute stale rule never takes a working run
    over) and `now_part` names the part under way. Any other state is the
    part's end: the light, still running and still this run's, with the part
    recorded `{state, at, line}` (`line` is the part's own last line).
    Answers `{ok, batches, line}`; `ok: false` and nothing to write when the
    light is not this run's (`held: false`: another run took it over, and
    the caller stops), or the part or state is not one of the Morning's."""
    now = _now(now_iso)
    if part not in MORNING_PARTS or state not in MORNING_MARK_STATES + (MORNING_STARTED,):
        return {"ok": False, "batches": [], "line": "That is not a part of the Morning run."}
    light, version = _morning_held(reads_dir, run_id)
    if light is None:
        return _morning_lost(reads_dir, out_dir, run_id)
    if light.get("pressed") and part not in (light.get("parts") or []):
        return {"ok": False, "batches": [], "line": "That part was not asked for by this press."}
    if state == MORNING_STARTED:
        return {"ok": True, "line": "", "batches": [_morning_write(out_dir, dict(light, at=_iso(now), now_part=part), version)]}
    light = {k: v for k, v in light.items() if k != "now_part"}
    row = {"state": state, "at": _iso(now), "line": str(line or "").strip()[:300]}
    if part == "update" and row["line"] == UPDATE_BUSY_LINE:
        # ruling 129 (item 3): another update holds the page's update light and builds the whole page
        row = {"state": "done", "at": _iso(now), "line": MORNING_UPDATE_HANDED_LINE, "handed": True}
    elif part == "update" and row["line"] == UPDATE_BUSY_NARROW_LINE:
        # early review of #1776 (must 2): a narrow update is not the day's first whole one
        row = {"state": "error", "at": _iso(now), "line": MORNING_UPDATE_NARROW_LINE}
    if light.get("pressed"):
        # decision F: a press's part is the press's, never the morning's
        ran = dict(light.get("press_done") or {}) if isinstance(light.get("press_done"), dict) else {}
        ran[part] = row
        nxt = dict(light, press_done=ran, at=_iso(now))
    else:
        done = _morning_kept(light.get("done"), now, tz)
        done[part] = row
        nxt = dict(light, done=done, at=_iso(now))
    return {"ok": True, "line": "", "batches": [_morning_write(out_dir, nxt, version)]}


def morning_finish(reads_dir: str, out_dir: str, *, run_id: str, now_iso=None, tz: Any = None) -> Dict[str, Any]:
    """cr1#1722 step 2: the Morning run's last write. The light leaves
    running: `done` with "Morning ready." when every part is done this
    morning, else `error` naming what is not ("Morning ready, except the
    inbox."), the parts' own records kept. Answers `{ok, batches, line}`;
    the run's final response is `line`.

    Should 6: a press's run whose light carries `due` (a morning fire stopped
    behind it) does not finish while morning parts are left: it answers
    `carry_on: true` with those `parts`, and `batches[0]` makes the light
    this run's morning claim; the run does them, then finishes again."""
    now = _now(now_iso)
    light, version = _morning_held(reads_dir, run_id)
    if light is None:
        return {"ok": False, "held": False, "batches": [], "line": MORNING_NOT_HELD_LINE}
    done = _morning_handed_holds(_morning_kept(light.get("done"), now, tz), _saved_docs(reads_dir, "status"), now)
    began = _parse(light.get("claimed_at"))
    elapsed = max(0, int((now - began).total_seconds())) if began is not None and began <= now else None
    left = [p for p in MORNING_PARTS if done.get(p, {}).get("state") != "done"]
    if light.get("pressed") and light.get("due") and left:
        # should 6: the morning a fire left to this run, after its press
        claim = {k: v for k, v in light.items() if k not in ("due", "press_done", "meeting_id", "now_part")}
        claim.update(pressed=False, parts=left, done=done, at=_iso(now))
        return {"ok": True, "carry_on": True, "parts": left, "line": "", "elapsed_s": None,
                "batches": [_morning_write(out_dir, claim, version)]}
    if light.get("pressed"):
        ran = light.get("press_done") if isinstance(light.get("press_done"), dict) else {}
        asked = [p for p in MORNING_PRESS_PARTS if p in (light.get("parts") or [])]
        bad = [p for p in asked if (ran.get(p) or {}).get("state") != "done"]
        # review round 1: a press ends on its own line, its part's or the press's, never "Morning ready."
        line = " ".join(str((ran.get(p) or {}).get("line") or "")
                        or (MORNING_PRESS_BAD_LINES if p in bad else MORNING_PRESS_DONE_LINES)[p] for p in asked)
        state = "error" if bad else "done"
        fin = status_doc(None, "morning", state, line, now_iso=_iso(now), run_id=str(run_id),
                         stop_reason=(line if bad else None))
        fin.update(done=done, pressed=True, parts=asked, press_done={p: ran[p] for p in asked if p in ran})
        return {"ok": True, "line": line, "elapsed_s": elapsed, "batches": [_morning_write(out_dir, fin, version)]}
    missing = [p for p in MORNING_PARTS if done.get(p, {}).get("state") != "done"]
    if missing:
        words = [MORNING_PART_WORDS[p] for p in missing]
        what = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
        line, state = MORNING_PARTIAL_LINE.format(what=what), "error"
    else:
        line, state = (MORNING_READY_HANDED_LINE if (done.get("update") or {}).get("handed") else MORNING_READY_LINE), "done"
    if _morning_zone(tz) is None:
        line += MORNING_UNZONED_NOTE
    fin = status_doc(None, "morning", state, line, now_iso=_iso(now), run_id=str(run_id),
                     stop_reason=(line if state == "error" else None))
    fin["done"] = done
    return {"ok": True, "line": line, "elapsed_s": elapsed, "batches": [_morning_write(out_dir, fin, version)]}


def _morning_cli(argv: List[str], verb: str) -> int:
    def arg(name: str) -> str:
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else ""
    reads, out, tz = arg("--reads"), arg("--out"), arg("--tz")
    if not reads or not out or not tz or (verb != "start" and not arg("--run-id")):
        print(json.dumps({"ok": False, "batches": [], "line": "usage: cockpit_helpers.py morning-" + verb
                          + " --reads <folder> --out <folder> --tz <zone>" + ("" if verb == "start" else " --run-id <id>")
                          + (" --part <part> --state <started|done|error|skipped> [--line <text>]" if verb == "mark" else "")}))
        return 2
    if verb == "start":
        res = morning_start(reads, out, tz=tz)
    elif verb == "mark":
        res = morning_mark(reads, out, run_id=arg("--run-id"), part=arg("--part"), state=arg("--state"),
                           line=arg("--line"), tz=tz)
    else:
        res = morning_finish(reads, out, run_id=arg("--run-id"), tz=tz)
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


#: Review round 1: how long the Morning ran, from its claim to its finish, is
#: on `morning-finish`'s answer (`elapsed_s`) and goes on the run's receipt.


def _refresh_start_cli(argv: List[str], leg: str = "refresh") -> int:
    reads = out = ""
    whole = False
    i = 0
    while i < len(argv):
        if argv[i] == "--whole" and leg == "update":  # early review of #1776 (must 2): the Morning's update part
            whole = True
            i += 1
        elif argv[i] == "--reads" and i + 1 < len(argv):
            reads = argv[i + 1]
            i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
            i += 2
        else:
            i += 1
    if not reads or not out:
        print(json.dumps({"ok": False, "line": f"usage: cockpit_helpers.py {leg}-start --reads <dir> --out <dir>"}))
        return 2
    try:
        ans = refresh_start(reads, out, leg=leg, whole=whole)
    except Exception as exc:  # noqa: BLE001 - review of #1664 (item 6): one JSON line, never a traceback
        print(json.dumps({"ok": False, "line": f"The {leg} could not start: {type(exc).__name__}."}))
        return 1
    print(json.dumps(ans))
    return 0


# ---- the one Update's doors (cr1#1722) ----
#
# Four writer calls carry the run, each its own door call: `update_claim` and
# `update_save` are Apply's two calls (cr1#1608) with no light and no receipt
# of their own; `update_build` is the rebuild, one call per part; and
# `update_finish` writes the run's one receipt. What the save counted and
# which documents its presses touched are kept beside the rebuild's own
# scratch files, by the run's id, so the run never types a list of feeds: the
# rebuild reads it there.

UPDATE_STATE = "_run.json"
UPDATE_SCOPES = ("all", "touched")


def _update_state(workspace_root, run_id: str) -> Dict[str, Any]:
    """What this run's earlier calls kept (`{run_id, scope, saved, touched,
    all}`), or `{}` when the file is another run's or cannot be read."""
    try:
        val = json.loads((_task_scratch(workspace_root, "update") / UPDATE_STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(val, dict) or not run_id or str(val.get("run_id") or "") != str(run_id):
        return {}
    return val


def _update_state_write(workspace_root, state: Dict[str, Any]) -> bool:
    """Keep this run's state in the Update's scratch folder, outside the
    workspace. False when the folder cannot be used: the rebuild then builds
    everything, which is always right."""
    from atomic_write import atomic_write_text

    try:
        scratch = _task_scratch(workspace_root, "update")
        try:
            scratch.resolve().relative_to(Path(str(workspace_root)).resolve())
        except ValueError:
            pass  # outside the workspace, as it must be
        else:
            return False
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write_text(scratch / UPDATE_STATE, json.dumps(state, default=str))
    except OSError:
        return False
    return True


def _update_saved(man: Dict[str, Any]) -> Dict[str, int]:
    """The save's counts as the light and the receipt carry them."""
    sv = man.get("saved") if isinstance(man.get("saved"), dict) else {}
    return {k: int(sv.get(k) or 0) for k in ("applied", "refused", "failed", "recorded", "n_more")}


def _update_light_words(man: Dict[str, Any], state: str, line: str) -> Tuple[str, str]:
    """The Update's light: what it saved, then what the rebuild said. A press
    that could not be saved makes the light an error whatever the rebuild
    did, and the line says how many of how many, and the first reason."""
    sv = _update_saved(man)
    raw = man.get("saved") if isinstance(man.get("saved"), dict) else {}
    landed = sv["applied"] + sv["recorded"]
    total = landed + sv["refused"] + sv["failed"]
    if sv["failed"]:
        why = str(raw.get("first_failed") or ROW_ERROR_LINE)
        words = UPDATE_SAVE_FAILED_LINE.format(n=sv["failed"], total=total, why=why)
        return "error", (words + (" " + line if state == "error" and line else "")).strip()
    if state == "error":
        return state, line
    head = ""
    if landed or sv["refused"]:
        head = f"Saved {landed}" + (f", refused {sv['refused']}" if sv["refused"] else "") + "."
    if sv["n_more"]:
        head = (head + " " + CLAIM_MORE_LINE.format(n=sv["n_more"])).strip()
    words = (head + " " + str(line or "")).strip()
    return "done", words or UPDATE_NOTHING_LINE


def update_claim(workspace_root, waiting: Optional[List[Any]] = None,
                 page: Optional[List[Any]] = None, *, run_id: str = "",
                 fired_via: str = "scheduled", scope: str = "all", full_at: str = "",
                 inbox_senders: Any = None, start_check: str = "", now_iso=None) -> Dict[str, Any]:
    """A WRITER (it keeps the run's state in the Update's scratch folder; the
    book is not written here). The Update's first call (cr1#1722): Apply's
    `apply_start` with no light and no receipt of its own.

    `waiting` and `page` are the two `choices` queries as read, `run_id` the
    id `update-start` answered (the light this run claimed carries it), and
    `scope` what `update-start` answered: `touched` for a run the page fired
    after a press, else `all`; `full_at` the time `update-start` answered
    (cr1#1809: the last whole rebuild, from the page's own light; anything
    that is not a time counts as none); `inbox_senders` (cr1#1701) and
    `start_check` what it answered too. Review of #1817: the four are taken
    only when `start_check` is what `update_start_check` gives for them;
    otherwise the run is `all` with no record and no map, so it rebuilds
    everything and the save checks mail as it did before. Answers `apply_start`'s own `{ok, stop: false,
    run_id, writes, n_more, line}`; with nothing to claim, `nothing: true` and
    no writes: the run skips `update_save` and goes on to `update_build`."""
    ans = apply_start(workspace_root, waiting, page, None, run_id=run_id, fired_via=fired_via,
                      now_iso=now_iso, task="update")
    copied = isinstance(start_check, str) and re.fullmatch(r"[0-9a-f]{64}", start_check) is not None \
        and start_check == update_start_check(run_id, scope, full_at, inbox_senders)
    state = {"run_id": str(ans.get("run_id") or ""),
             "scope": scope if (copied and scope in UPDATE_SCOPES) else "all",
             "saved": {}, "touched": [], "all": False,
             UPDATE_FULL_FIELD: _full_at_of(full_at, _now(now_iso)) if copied else "",
             # cr1#1701 (A): only as the start answered it, checked for shape
             "inbox_senders": page_inbox_senders_ok(inbox_senders) if copied else {}}
    if not copied:
        # Review of #1821: a check that does not hold makes the run whole and
        # drops the senders. Kept here so the receipt counts it, never silent.
        state[UPDATE_CHECK_FAILED] = 1
    ans["kept"] = _update_state_write(workspace_root, state)
    return ans


def update_save(workspace_root, waiting: Optional[List[Any]] = None,
                page: Optional[List[Any]] = None, *, run_id: str = "",
                fired_via: str = "scheduled", n_more: int = 0, now_iso=None) -> Dict[str, Any]:
    """A WRITER. The Update's save (cr1#1722): Apply's `apply_run`, every
    claimed press written through its owning writer in one call, with no
    light and no receipt of its own. Answers what `apply_run` answers
    (`batches` to send as they are, `next`, `connectors`, `counts`, `line`,
    `results`), with `light` always null, plus `touched` and `all`: what the
    rebuild will build, kept for it here by the run's id. `next: detail` is
    left only for a connector part (a confirmed send or draft, a "Missed
    something?" search)."""
    kept = _update_state(workspace_root, run_id)
    tok = _PAGE_INBOX_SENDERS.set(page_inbox_senders_ok((kept or {}).get("inbox_senders")) or None)
    try:
        ans = apply_run(workspace_root, waiting, page, None, run_id=run_id, fired_via=fired_via,
                        n_more=n_more, now_iso=now_iso, task="update")
    finally:
        _PAGE_INBOX_SENDERS.reset(tok)
    ans["light"] = None
    state = kept or {"run_id": str(run_id or ""), "scope": "all"}
    saved = dict(ans.get("counts") or {})
    saved["first_failed"] = str(ans.get("first_failed") or (ans.get("line") if ans.get("ok") is False else "") or "")
    saved["n_more"] = int(n_more or 0)
    if ans.get("ok") is False:
        saved["failed"] = max(1, int(saved.get("failed") or 0))
    # Fix check of round 1 of #1768: work the connector part finishes (an
    # Inbox Send or Draft, a "Missed something?") is not in the table, so the
    # run that does it rebuilds everything.
    conn = ans.get("connectors") or {}
    every = bool(ans.get("all")) or bool(conn.get("inbox") or conn.get("miss"))
    ans["all"] = every
    state.update(saved=saved, touched=list(ans.get("touched") or []), all=every)
    ans["kept"] = _update_state_write(workspace_root, state) if str(run_id or "") else False
    # cr1#1765: a save that changed the mail rules (or one whose reach is not
    # known) publishes the off-limits wall again before the rebuild.
    ans["wall_again"] = bool(ans.get("all")) or "feed/mail_rules" in [str(d) for d in ans.get("touched") or []]
    return ans


def _update_last_slot(workspace_root, now: _dt.datetime) -> Optional[_dt.datetime]:
    """The latest time at or before `now` the hourly Update is scheduled to
    fire, on the workspace's own clock, read from the task's cron (minute,
    hours, weekdays). None when it cannot be read."""
    try:
        import tz as _tz

        zone = _tz.load_workspace_tz(workspace_root)
        minute, hours, _dom, _mon, dows = COCKPIT_TASKS["cockpit-update"]["cron"].split()

        def spread(field: str) -> List[int]:
            out: List[int] = []
            for part in field.split(","):
                lo, _, hi = part.partition("-")
                out += list(range(int(lo), int(hi or lo) + 1))
            return out

        mins, hrs, days = spread(minute), spread(hours), spread(dows)
        local = now.astimezone(zone)
        for back in range(8):
            day = (local - _dt.timedelta(days=back)).date()
            if (day.weekday() + 1) % 7 not in days:
                continue
            for h in sorted(hrs, reverse=True):
                for m in sorted(mins, reverse=True):
                    slot = _dt.datetime(day.year, day.month, day.day, h, m, tzinfo=zone)
                    if slot <= local:
                        return slot
    except Exception:  # noqa: BLE001 - no slot read: the 55-minute rule alone
        return None
    return None


def _full_at_of(value: Any, now: Optional[_dt.datetime]) -> str:
    """cr1#1809: a `full_at` as kept: the time as written when it reads as a
    time no later than `now`, else "" (none on record)."""
    at = _parse(value) if isinstance(value, str) else None
    if at is None or now is None or at > now:
        return ""
    return str(value)


def _update_full_due(workspace_root, now: Optional[_dt.datetime], state: Optional[Dict[str, Any]] = None) -> bool:
    """True when no whole rebuild of this workspace is on record in the last
    `UPDATE_FULL_EVERY_MINUTES` (none on the page's light as this run read it,
    cr1#1809, an unreadable one, or an old one),
    or none since the hourly Update's latest scheduled time (fix check of
    round 1 of #1768: the hourly run is always a whole rebuild, even when it
    takes over a light a press set, and even within the hour of an Update now)."""
    if workspace_root is None or now is None:
        return False
    at = _parse(_full_at_of((state or {}).get(UPDATE_FULL_FIELD), now) or None)
    if at is None or (now - at) >= _dt.timedelta(minutes=UPDATE_FULL_EVERY_MINUTES) or at > now:
        return True
    slot = _update_last_slot(workspace_root, now)
    return slot is not None and at < slot


def _update_only(state: Dict[str, Any], workspace_root=None, now: Optional[_dt.datetime] = None) -> Optional[List[str]]:
    """What a run rebuilds: everything (None), unless the page fired it after
    a press and every landed press is in the one table. A whole rebuild is
    also due when none is on record for close to an hour, and a save that
    failed adds the Maintenance record, where Settings names it."""
    if not state or state.get("scope") != "touched" or state.get("all"):
        return None
    if _update_full_due(workspace_root, now, state):
        return None
    only = [str(d) for d in state.get("touched") or []]
    if int((state.get("saved") or {}).get("failed") or 0) > 0 and "status/maintenance" not in only:
        only.append("status/maintenance")
    return only


#: cr1#1808: `update_build` with `stage: "all"` runs the rebuild's parts in
#: one door call, in order, and starts another part only within this many
#: seconds of the call's start. Each part stops itself at
#: `REFRESH_STAGE_BUDGET_S` (120), so the last part it starts ends by 140 s at
#: worst, under the door's 150 s; a part it did not reach is named as `next`
#: and the run calls it as before, so a heavy book still ends with `record`.
UPDATE_ALL_START_S = 20


def update_build(workspace_root, *, run_id: str = "", stage: str = "", token: str = "",
                 results: Optional[Dict[str, Any]] = None, now_iso=None,
                 budget_s: Optional[float] = None) -> Dict[str, Any]:
    """A WRITER (it builds the plate, which mints display numbers). The
    Update's rebuild (cr1#1722): `refresh_all` for the Update, one call per
    part, each under the door's time.

    The first call is `stage: "plate"`. It reads what this run's save kept
    (by `run_id`) and decides what to build: everything on a scheduled run or
    Update now, only the documents the saved presses touched on a run the
    page fired after a press. `results` (`{choice id: its result}`, the
    connector parts `cockpit-apply-detail.md` carried out, or null) are
    counted into the save here. Its answer carries `token` and `next`; each
    later call passes that token and the `next` it was given, and the last
    (`record`) writes the run's light with the documents and answers the
    parts to pull. A part with nothing to build is skipped by `next`. With
    no `stage`, every part runs in this one process (in-process callers and
    tests).

    cr1#1808: `stage: "all"` is the plate's call that goes on: after the
    plate it runs each part the last answer named, in this one call, while
    it is inside `UPDATE_ALL_START_S`. It answers the last part's answer (the
    record's, when it got there), with `ran`, the parts it ran. When time ran
    out first, that answer's `next` names the part to call next, with the
    plate's `token`, exactly as after a single part. Nothing is built that a
    part-by-part run would not build, in the same order."""
    want = str(stage or "").strip()
    if want == "all":
        import time

        t0 = time.monotonic()
        first = update_build(workspace_root, run_id=run_id, stage="plate", results=results, now_iso=now_iso,
                             budget_s=budget_s)
        ans, ran, tok = first, ["plate"], str(first.get("token") or "")
        while (tok and ans.get("stage") != "record" and ans.get("next")
               and time.monotonic() - t0 < UPDATE_ALL_START_S):
            nxt = str(ans["next"])
            ans = update_build(workspace_root, run_id=run_id, stage=nxt, token=tok, now_iso=now_iso, budget_s=budget_s)
            ran.append(nxt)
        out = dict(ans, ran=ran)
        if tok and not out.get("token"):
            out["token"] = tok
        return out
    if want not in ("", "plate"):
        return refresh_all(workspace_root, now_iso=now_iso, run_id=run_id or None, stage=want,
                           token=token, budget_s=budget_s, task="update")
    state = _update_state(workspace_root, run_id)
    saved = dict(state.get("saved") or {})
    for res in (results or {}).values():
        out = (res or {}).get("outcome") if isinstance(res, dict) else None
        if out == "error":
            saved["failed"] = int(saved.get("failed") or 0) + 1
            saved["first_failed"] = str(saved.get("first_failed") or res.get("line") or ROW_ERROR_LINE)
        elif out == "refused":
            saved["refused"] = int(saved.get("refused") or 0) + 1
        elif out in ("applied", "already"):
            saved["applied"] = int(saved.get("applied") or 0) + 1
    state = dict(state, saved=saved) if state else state
    # cr1#1785, ruling 130a: the items in Focus on their own today are
    # recorded, so each stays after its day and a late item that never came
    # in stays out. A store that cannot be written leaves it for the next
    # run; the rebuild goes on.
    try:
        import hub_focus

        hub_focus.mark_entered(workspace_root, now_iso=_iso(_now(now_iso)))
    except Exception:  # noqa: BLE001 - never stops the rebuild
        pass
    return refresh_all(workspace_root, now_iso=now_iso, run_id=run_id or None, stage=want,
                       budget_s=budget_s, task="update", only=_update_only(state, workspace_root, _now(now_iso)),
                       saved=saved)


def update_finish(workspace_root, *, run_id: str = "", token: str = "", error: str = "",
                  fired_via: str = "scheduled", now_iso=None, wall_sent: Optional[bool] = None) -> Dict[str, Any]:
    """A WRITER (the run's ONE receipt). Every way an Update ends
    (cr1#1722): after the documents are sent, and after any call that said
    no. Counts in code from what the run kept: the save's counts (by
    `run_id`) and the rebuild's (by `token`, when the rebuild's files are
    still this run's). Answers `{ok, line, counts, light}`. `light` is null
    after a rebuild that reached its last part (the light went with the
    documents); with `error` (the line of an answer that said no, or the
    reason a call failed) it is the error light on this call's clock, for
    the run to `set` on `status/update`, so the Hub never waits on a run that
    ended."""
    now_s = _iso(_now(now_iso))
    state = _update_state(workspace_root, run_id)
    man = _refresh_manifest(_task_scratch(workspace_root, "update"))
    mine = bool(token) and str(man.get("token") or "") == str(token)
    saved = _update_saved({"saved": (man.get("saved") if mine and man.get("saved") else state.get("saved")) or {}})
    counts: Dict[str, Any] = dict(saved)
    counts["scope"] = "touched" if (mine and man.get("only") is not None) else "all"
    if mine:
        counts.update({k: v for k, v in (man.get("counts") or {}).items() if k in ("plate", "closed")})
        # cr1#1837: the off-limits list every Update carries is not one of the feeds it rebuilt
        counts["built"] = len([r for r in man.get("report") or [] if r.get("ok")
                               and str(r.get("doc")) not in ("status/update", WALL_CARRY_DOC)])
        counts["not_built"] = len([r for r in man.get("report") or [] if not r.get("ok")])
        # cr1#1764: the parts that did not finish or never ran, by name, and
        # the documents not built (a part skipped for holding nothing to build
        # is neither). A clean run has none, so its receipt is unchanged.
        # `failed` is the save's count, so the documents have their own key.
        ran = set(man.get("stages") or []) | set(man.get("skipped") or [])
        unfinished = [s for s in REFRESH_STAGE_NAMES if s not in ran]
        if unfinished:
            counts["not_finished"] = unfinished
        failed_docs = [str(r.get("doc")) for r in man.get("report") or [] if not r.get("ok")]
        if failed_docs:
            counts["failed_docs"] = failed_docs
    if state.get(UPDATE_CHECK_FAILED):
        counts[UPDATE_CHECK_FAILED] = 1
    # cr1#1837: `wall_sent` as `unpack-refresh` answered it; a list not sent is
    # on the receipt (and so in the health check's alarm), never silent.
    # Review round 1 (must 1): every Update carries the list, so "sent" only
    # when the sending step said so, and anything else (false, null, left
    # out, a run that ended before it) is "not_sent".
    sent = wall_sent is True
    # Coordinator's ruling on round 1: a run whose claim of the light was
    # refused (another run holds it and speaks for the list) never tried to
    # send it, so it is "not_attempted", and that alone makes nothing partial
    # and raises no alarm. Delta check of 5319fe083: that needs its own proof,
    # the run's error being one of the two "another update is running" lines.
    # Everything else is "not_sent" (a failed build, false, null, left out, an
    # early end after the claim, a claim call the door refused): a wrong guess
    # raises an alarm that was not needed, the safe direction.
    held = sent or str(error or "").strip() not in (UPDATE_BUSY_LINE, UPDATE_BUSY_NARROW_LINE)
    counts["wall"] = "sent" if sent else ("not_sent" if held else "not_attempted")
    sent = sent or not held  # a list this run never held is not its to report
    err = str(error or "").strip()
    kept_light: Dict[str, Any] = {}
    # cr1#1764: the light in the task's scratch folder is this run's only when
    # this run's last part wrote it (its manifest says `record` ran, finished
    # or not). Until then the file there is the last run's, and a run whose
    # rebuild never got that far is not complete.
    wrote = mine and ("record" in (man.get("stages") or []) or "record" in (man.get("not_finished") or []))
    if wrote and not err:
        try:
            kept_light = json.loads(_refresh_file(_task_scratch(workspace_root, "update"), "status/update")
                                    .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kept_light = {}
        # Review of cr1#1764: a record part cut off by its budget is in the
        # manifest before its light is kept, so the file can still be the last
        # run's. Every light names its run: one that is not this run's (or
        # names none) is not trusted.
        if not isinstance(kept_light, dict) or not run_id or str(kept_light.get("run_id") or "") != str(run_id):
            kept_light = {}
    line = err or str(kept_light.get("line") or "")
    if not line and mine and not wrote:
        line = UPDATE_STAGE_MISSING_LINE.format(what="; ".join(
            REFRESH_STAGE_WORDS[s] for s in counts.get("not_finished") or ["record"]))
    # cr1#1837: a list that was not sent makes the run partial, and says so
    # (an ended run's line is its stop reason alone; its receipt still says not sent)
    if not sent and not err and WALL_NOT_SENT_LINE not in line:
        line = (line + " " + WALL_NOT_SENT_LINE).strip()
    bad = bool(err) or not kept_light or str(kept_light.get("state") or "") != "done"
    bad = bad or not sent
    log_run_receipt(workspace_root, fired_via=fired_via, legs=["update"], counts={"update": counts},
                    task_id="cockpit-update", now_iso=now_s, status="partial" if bad else "complete")
    light = None
    if err:
        light = status_doc(workspace_root, "update", "error", err, now_iso=now_s, run_id=run_id or None,
                           stop_reason=err)
        light["saved"] = saved
        # cr1#1809: the record of the last whole rebuild this run was given
        _carry_full_at({"full_at": (state or {}).get(UPDATE_FULL_FIELD)}, light, _parse(now_s))
    return {"ok": True, "line": line or UPDATE_NOTHING_LINE, "counts": counts, "light": light}


def refresh_part(workspace_root=None, token: str = "", part: int = 0,
                 task: str = "refresh") -> Dict[str, Any]:
    """READ. One part of what `refresh_all` built, by its token:
    `{ok, token, part, parts, pages: {"feed/plate": {...}, ...}}`. It reads the
    scratch folder that call left and nothing in the workspace. A token that
    is not the last refresh's is refused: a newer run wrote over it. `task`
    (cr1#1603) names whose run to read: `prep_all` and `inbox_all` keep their
    documents the same way, each in its own folder."""
    tok = str(token or "")
    allowed = ONE_CALL_DOCS.get(str(task or ""))
    if not _REFRESH_TOKEN_RE.match(tok) or allowed is None:
        return {"ok": False, "line": "No refresh to read: run the refresh first."}
    if not workspace_root:
        return {"ok": False, "line": "No refresh to read: run the refresh first."}
    scratch = _task_scratch(workspace_root, str(task))
    manifest = _refresh_manifest(scratch)
    if str(manifest.get("token") or "") != tok:
        # another run of this task ran since, or none did
        return {"ok": False, "line": ONE_CALL_GONE_LINES[str(task)]}
    sizes: List[Tuple[str, int]] = [
        (str(row[0]), int(row[1])) for row in manifest.get("sizes") or []
        if isinstance(row, list) and len(row) == 2 and str(row[0]) in allowed
        and _refresh_file(scratch, str(row[0])).is_file()]
    parts = _task_parts(str(task), sizes)
    try:
        idx = int(part)
    except (TypeError, ValueError):
        idx = -1
    if idx < 0 or idx >= len(parts):
        return {"ok": False, "token": tok, "parts": len(parts),
                "line": f"This refresh has {len(parts)} parts, numbered from 0."}
    docs = {doc: json.loads(_refresh_file(scratch, doc).read_text(encoding="utf-8"))
            for doc in parts[idx]}
    out: Dict[str, Any] = {"ok": True, "token": tok, "part": idx, "parts": len(parts), "pages": docs}
    if WALL_CARRY_DOC in docs:
        # cr1#1837: the list's digest, made here beside it, so a list that was
        # cut, mixed or retyped on the way is never published; then padding, so
        # the answer is long enough for the run's tool to save it to a file
        out["digests"] = {WALL_CARRY_DOC: wall_digest(docs[WALL_CARRY_DOC])}
        short = WALL_CARRY_MIN_CHARS - len(json.dumps(out, separators=(",", ":")))
        if short > 0:
            out["pad"] = "." * (short + 16)
    return out


def _one_call_answer(workspace_root, task: str, leg: str, report, built, *, state: str, line: str,
                     now_s: str, run_id: Optional[str], started: float,
                     extra: Dict[str, Any], doc: str, not_kept_line: str) -> Dict[str, Any]:
    """The small answer of a one-call Prep or Inbox run: the documents kept
    (or the refusal to keep them), the parts to pull, and the light.

    cr1#1614 review: `ok` says the documents were KEPT, so the parts are
    pulled and the batch is sent whenever there are parts; the light alone
    carries an error (a degraded prep leg, a blocked inbox read). The light is
    decided after keeping, from what was kept: a `done` never stands over a
    document that was not built or could not be kept, and a run that could
    not keep its answers at all has the error light too."""
    import time

    try:
        token, sizes = _keep_built(workspace_root, task, report, built)
    except OSError as exc:
        light = status_doc(workspace_root, leg, "error", ONE_CALL_KEEP_FAILED_LINE, now_iso=now_s,
                           run_id=run_id, stop_reason=ONE_CALL_KEEP_FAILED_LINE)
        return dict(extra, ok=False, line=ONE_CALL_KEEP_FAILED_LINE, error=type(exc).__name__,
                    pages=report, light=light, seconds=round(time.monotonic() - started, 2))
    if doc not in {d for d, _n in sizes} and state != "error":
        state, line = "error", not_kept_line
    light = status_doc(workspace_root, leg, state, line, now_iso=now_s, run_id=run_id,
                       stop_reason=line if state == "error" else None)
    return dict(extra, ok=bool(sizes), token=token, task=task, pages=report,
                parts=_refresh_parts(sizes), failed=[r["doc"] for r in report if not r["ok"]],
                light=light, line=line, seconds=round(time.monotonic() - started, 2))


#: cr1#1758: "Prep this one". The page's press waits in `config/prep_one`
#: and starts Hub Prep; the run reads the document and hands it here with its
#: own calendar read. How long a request waits for a run, and how many one
#: run takes.
PREP_ONE_DOC = "config/prep_one"
PREP_ONE_MAX_AGE_MINUTES = 30
PREP_ONE_CAP = 5
PREP_ONE_LINES = {
    "expired": "That press is older than half an hour. Press Prep this one again.",
    "not_found": "That meeting is not on the calendar this run read, so nothing was prepared.",
    "personal": "That is on your personal calendar, and nothing personal is prepared here.",
    "solo": "Nobody else is on that one, so there is nothing to prepare.",
    "all_day": "That one runs all day, and an all-day event is not prepared.",
    "over": "That meeting is already over.",
    "unchecked": "Who is on that meeting could not be checked just now, so nothing was prepared.",
    "off_limits": "Everyone else on that meeting is off limits, so nothing was prepared.",
}
#: cr1#1825: a press's own run that finds no request of its own preps nothing,
#: never the usual next four; Hub Morning's prep part says the same words.
PREP_ONE_NONE_LINE = "No Prep this one press was waiting, so nothing was prepped."


def prep_one_pick(workspace_root, requests: Optional[List[Dict[str, Any]]] = None,
                  events: Optional[List[Dict[str, Any]]] = None, *, now_iso=None,
                  press_fired: bool = False, run_id: str = "") -> Dict[str, Any]:
    """READ/COMPUTE. cr1#1758: the server's judgment of the page's "Prep this
    one" presses. Only a run the page's press fired is narrowed
    (`press_fired`: the `status/prep` light the run read carried `one`): any
    other run, a scheduled one included, answers `narrowed: False` and is the
    usual run, whatever waits (ruling 120; review round 1 of cr1#1771). `requests` is `config/prep_one`'s list as the run read it
    from the page's data (`{jk, at}` each: the event's join key and when the
    press was made); `events` is the run's OWN calendar read (the fourteen
    days, each with its `event_id`). A press names its meeting by key only:
    the meeting prepared is the one the run itself read under that key, never
    anything the page typed, so a forged press can add no meeting and no
    title. Refused, each with its one plain line: a press older than
    `PREP_ONE_MAX_AGE_MINUTES`, a key the run's read does not hold, an event
    on a personal calendar, one with nobody else on it, an all-day one, one
    already over, one whose people could not be checked against off limits,
    and one where everyone else is off limits.

    Answers `{ok, narrowed, meetings, doc, n_refused}`. `narrowed` is True
    when any request was waiting: the run then preps `meetings` (each
    `{meeting_id, title, time_label, start}`, as the prep leg's plan takes
    them) in place of its next four. `doc` is `config/prep_one` as the run
    writes it back once it has finished: each request with its `state`
    (`done` or `refused`) and, for a refusal, its `line`. Writes nothing.

    cr1#1722 step 2 (ruling 120 seam with #1771): Hub Morning's prep part
    passes its `run_id`. The page stamps each request it starts a run for
    with that run's id, so only requests carrying THIS run's id are judged:
    a press another run was started for never narrows or is satisfied by
    this one, and those requests go back in `doc` exactly as read.

    cr1#1825: a run the press fired (`press_fired` with a `run_id`) that finds
    no request of its own waiting (dropped, already judged, or overwritten by
    a later press) is still narrowed, to nothing: `narrowed: True`,
    `meetings: []`, `none: True` and `line` (`PREP_ONE_NONE_LINE`). It never
    falls back to the next four the owner did not ask for."""
    now = _now(now_iso)
    out: Dict[str, Any] = {"ok": True, "narrowed": False, "meetings": [], "n_refused": 0,
                           "doc": {"at": _iso(now), "requests": []}}
    waiting: List[Dict[str, Any]] = []
    others: List[Dict[str, Any]] = []
    seen: set = set()
    mine = str(run_id or "").strip()
    for r in requests if isinstance(requests, list) else []:
        if not isinstance(r, dict) or r.get("state") in ("done", "refused"):
            continue
        if mine and str(r.get("run_id") or "") != mine:
            others.append({k: r[k] for k in ("jk", "at", "run_id") if k in r})  # another run's press, kept as read
            continue
        jk = str(r.get("jk") or "").strip().lower()
        if not re.match(r"^[0-9a-f]{%d}$" % EVENT_KEY_LEN, jk) or jk in seen:
            continue
        seen.add(jk)
        waiting.append({"jk": jk, "at": str(r.get("at") or "")[:40]})
    waiting = waiting[:PREP_ONE_CAP]
    if not waiting and press_fired is True and mine:
        # cr1#1825: the press's own run with no request of its own preps nothing
        out.update(narrowed=True, none=True, line=PREP_ONE_NONE_LINE)
        out["doc"]["requests"] = others
        return out
    if not waiting or press_fired is not True:
        out["doc"]["requests"] = others
        return out
    out["narrowed"] = True
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for m in events if isinstance(events, list) else []:
        if isinstance(m, dict):
            k = event_key(m.get("event_id"))
            if k:
                by_key.setdefault(k, []).append(m)
    wall = _meeting_wall(workspace_root)
    tzinfo = None
    for req in waiting:
        row: Dict[str, Any] = {"jk": req["jk"], "at": req["at"], "judged_at": _iso(now)}
        why = ""
        pressed = _parse(req["at"])
        hits = by_key.get(req["jk"]) or []
        m = hits[0] if len(hits) == 1 else None
        start = end = None
        if pressed is None or now - pressed > _dt.timedelta(minutes=PREP_ONE_MAX_AGE_MINUTES) \
                or pressed - now > _dt.timedelta(minutes=5):
            why = "expired"
        elif m is None:
            why = "not_found"
        elif str(m.get("calendar") or "").lower() == "personal":
            why = "personal"
        elif _DATE_ONLY_RE.match(str(m.get("start") or "").strip()):
            why = "all_day"
        else:
            start = _parse(m.get("start"))
            end = _parse(m.get("end")) or (start + _dt.timedelta(hours=1) if start else None)
            if start is None:
                why = "not_found"
            elif end <= now:
                why = "over"
            elif _no_one_else(m):
                why = "solo"
            else:
                extras = _meeting_extras(m, wall)
                if wall is None or "people" not in extras:
                    why = "unchecked"
                elif not extras["people"]:
                    why = "off_limits"
        if why:
            row.update({"state": "refused", "line": PREP_ONE_LINES[why], "why": why})
            out["n_refused"] += 1
        else:
            tzinfo = tzinfo or _workspace_tz(workspace_root)
            local = start.astimezone(tzinfo)
            label = "%d:%02d" % (local.hour % 12 or 12, local.minute)
            out["meetings"].append({"meeting_id": str(m.get("meeting_id") or m.get("event_id") or "").strip(),
                                    "title": str(m.get("title") or "").strip()[:200],
                                    "time_label": label, "start": _iso(start)})
            row["state"] = "done"
        out["doc"]["requests"].append(row)
    out["doc"]["requests"].extend(others)
    return out


def prep_all(workspace_root, *, meetings: Optional[List[Dict[str, Any]]] = None,
             generated: Optional[Dict[str, Any]] = None, started_at: str = "",
             discover_error: Optional[str] = None,
             week: Optional[List[Dict[str, Any]]] = None, now_iso=None,
             device_workspace: Optional[str] = None,
             run_id: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER (cr1#1603) only because it keeps its document in a scratch
    folder outside the workspace; it writes nothing to the book. The Prep
    run's work after call-prep, in one process with the event log read once:
    the leg's result (`morning_brief_helpers.prep_leg_result`, exactly as the
    step-by-step run calls it) and the Today feed (`feed_today` over the
    seven-day read). Answers `{ok, token, task, parts, pages, failed, leg,
    light, line, seconds}`; `refresh_part` with `task: "prep"` hands the feed
    back. The light is `done` with "Prepped N, reused M.", or `error` with
    the leg's banner when the leg itself could not run."""
    import time

    import morning_brief_helpers as mbh
    import prep_leg as pl

    started = time.monotonic()
    now_s = _iso(_now(now_iso))
    built: Dict[str, Dict[str, Any]] = {}
    report: List[Dict[str, Any]] = []
    with _one_log_read():
        leg = mbh.prep_leg_result(str(workspace_root), meetings, generated, started_at=started_at,
                                  discover_error=discover_error)
        _one_call_build(built, report, "feed/today",
                        lambda: feed_today(workspace_root, week, now_iso=now_s, device_workspace=device_workspace))
    if not isinstance(leg, dict) or leg.get("error"):
        state, line = "error", PREP_ALL_FAILED_LINE
    elif leg.get("status") == pl.STATUS_DEGRADED and leg.get("banner"):
        state, line = "error", str(leg["banner"])
    else:
        counts = leg.get("counts") or {}
        state = "done"
        line = f"Prepped {int(counts.get(pl.OUTCOME_RAN) or 0)}, reused {int(counts.get(pl.OUTCOME_REUSED) or 0)}."
    return _one_call_answer(workspace_root, "prep", "prep", report, built, state=state, line=line,
                            now_s=now_s, run_id=run_id, started=started, extra={"leg": leg},
                            doc="feed/today", not_kept_line=PREP_FEED_FAILED_LINE)


def inbox_all(workspace_root, *, items: Optional[List[Dict[str, Any]]] = None,
              fire_started_at: Optional[str] = None, fired_via: Optional[str] = None,
              n_accounts: Optional[int] = None, provider: Optional[str] = None,
              task_id: str = "cockpit-inbox", tools: Optional[List[Any]] = None,
              fetch_blocked: Optional[str] = None, now_iso=None,
              run_id: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER (cr1#1603). The Inbox run after the fetch, in one process with
    the event log read once: the chain (`inbox_helpers.run_inbox_surface`,
    the writer it always was: the page, the capture and reconcile rows, the
    read row and the receipt), then the Inbox feed (`feed_inbox`) built from
    the page that call just landed, read here and never carried through the
    chat. The feed is kept in a scratch folder outside the workspace and
    handed back by `refresh_part` with `task: "inbox"`.

    Answers `{ok, token, task, parts, pages, failed, result, light, line,
    seconds}`; `result` is the chain's own answer without the page text,
    which this run never posts. A refusal of the chain is `ok: false` with
    the chain's answer and the error light naming its first line, and no
    feed: the page keeps what it has. The light is `done` with "N threads,
    M drafted." from the feed's own counts."""
    import time

    import inbox_helpers as ih

    started = time.monotonic()
    now_s = _iso(_now(now_iso))
    built: Dict[str, Dict[str, Any]] = {}
    report: List[Dict[str, Any]] = []
    kw: Dict[str, Any] = {"items": items, "n_accounts": n_accounts, "fired_via": fired_via,
                          "task_id": task_id, "tools": tools, "fire_started_at": fire_started_at,
                          "provider": provider}
    if fetch_blocked:
        kw["fetch_blocked"] = fetch_blocked
    with _one_log_read():
        res = ih.run_inbox_surface(str(workspace_root), **kw)
        if isinstance(res, dict) and res.get("ok") and res.get("page_rel"):
            rel = str(res["page_rel"])

            def _feed() -> Dict[str, Any]:
                html = (Path(str(workspace_root)) / rel).read_text(encoding="utf-8")
                return feed_inbox(workspace_root, html, fire_stamp=str(fire_started_at or ""),
                                  items=items, page_rel=rel, now_iso=now_s)

            _one_call_build(built, report, "feed/inbox", _feed)
            # cr1#1786 (ruling 127f): what the Urgent ranking needs from this
            # read, walled, for every Update to re-rank; a failure here never
            # stops the inbox run (Urgent keeps its last base). Review round 1
            # of #1819: only the threads the Inbox feed just built lists (the
            # owner's hide and drop rules applied), and held when the
            # hidden-people read is unread; no Inbox feed, no new base.
            try:
                import hub_urgent_store as hus

                if "feed/inbox" in built:
                    blocked_now = fetch_blocked or ih.fetch_wall(str(workspace_root))
                    kept = _urgent_kept(items, built["feed/inbox"])
                    hus.save(workspace_root, hus.build([] if blocked_now else kept,
                                                       wall=_urgent_wall(workspace_root),
                                                       own=ih.own_addresses(str(workspace_root)),
                                                       tzinfo=_workspace_tz(workspace_root), now_iso=now_s))
            except Exception:  # noqa: BLE001
                pass
    # cr1#1614 review: the page text and the delivery calls are the Inbox
    # chat's to post; this run posts neither, so neither rides its answer.
    result = ({k: v for k, v in (res or {}).items() if k not in ("text", "html", "calls")}
              if isinstance(res, dict) else {})
    if not (isinstance(res, dict) and res.get("ok")):
        line = str(((res or {}).get("lines") or [""])[0] or (res or {}).get("line") or "") if isinstance(res, dict) else ""
        light = status_doc(workspace_root, "inbox", "error", line, now_iso=now_s, run_id=run_id,
                           stop_reason=line)
        return {"ok": False, "result": result, "light": light, "line": line,
                "seconds": round(time.monotonic() - started, 2)}
    feed = built.get("feed/inbox")
    # cr1#1614 review: a read that was blocked is never a clean zero. The
    # light says the blocked line: `done` only when the owner marked every
    # mailbox personal (their own setting), `error` for every other block
    # (Phase 9: no mail connector, unreadable account settings).
    try:
        blocked = str(fetch_blocked or "") or str(ih.fetch_wall(str(workspace_root)) or "")
    except Exception:  # noqa: BLE001 - an unreadable wall reads as blocked, as the chain's own
        blocked = str(fetch_blocked or "") or ih.ACCOUNTS_UNREADABLE_LINE
    if feed is None:
        state, line = "error", INBOX_FEED_FAILED_LINE
    elif blocked:
        state, line = ("done" if blocked == ih.PERSONAL_ONLY_LINE else "error"), blocked
    else:
        state = "done"
        line = f"{int(feed.get('n_items') or 0)} threads, {int(feed.get('n_drafted') or 0)} drafted."
    return _one_call_answer(workspace_root, "inbox", "inbox", report, built, state=state, line=line,
                            now_s=now_s, run_id=run_id, started=started, extra={"result": result},
                            doc="feed/inbox", not_kept_line=INBOX_FEED_FAILED_LINE)


def _json_values(text: str) -> List[Any]:
    """The JSON values in a text: the whole text when it parses, otherwise
    each object found in it, in order."""
    try:
        return [json.loads(text)]
    except ValueError:
        pass
    out: List[Any] = []
    dec = json.JSONDecoder()
    pos = 0
    tries = 0
    while tries < 200:
        i = text.find("{", pos)
        if i < 0:
            break
        tries += 1
        try:
            val, end = dec.raw_decode(text, i)
        except ValueError:
            pos = i + 1
            continue
        out.append(val)
        pos = end
    return out


def _refresh_found(obj: Any, out: List[Dict[str, Any]], depth: int = 0) -> None:
    """Every `refresh_part` answer inside a pulled tool answer, however it was
    wrapped (an envelope, a list of text blocks, JSON carried as a string)."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        if isinstance(obj.get("pages"), dict) and _REFRESH_TOKEN_RE.match(str(obj.get("token") or "")):
            out.append(obj)
            return
        for v in obj.values():
            _refresh_found(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _refresh_found(v, out, depth + 1)
    elif isinstance(obj, str) and len(obj) > 20 and "{" in obj:
        for parsed in _json_values(obj):
            if not isinstance(parsed, str):
                _refresh_found(parsed, out, depth + 1)


def _saved_version(versions_dir: str, coll: str, doc_id: str) -> Optional[int]:
    if not versions_dir:
        return None
    try:
        saved = json.loads((Path(versions_dir) / coll / (doc_id + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    for key in ("version", "_version", "__version__"):
        v = saved.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
            return v
    return None


#: Delta check of 5319fe083 (required 1): one document line of a `list`
#: answer saved with `out_dir` (`- "<doc_id>"  <N> bytes  version <V>  "<path>"`).
WALL_LISTING_DOC_RE = re.compile(r'^-\s+"([^"]+)"\s')


def feed_listing_matches(listing_file: str, saved_dir: str) -> bool:
    """Delta check of 5319fe083 (required 1): True only when `listing_file` is
    the saved answer of the page's `list` on `feed` with `out_dir`, and it
    names exactly the documents saved under `saved_dir/feed`: its head's
    count, its document lines and the saved files all agree. A listing cut
    short, of another collection, or one that cannot be read is False."""
    try:
        raw = Path(listing_file).read_text(encoding="utf-8-sig")
    except (OSError, ValueError, TypeError):
        return False
    lines = [ln.strip() for ln in _listing_text(raw).lstrip("\ufeff").splitlines() if ln.strip()]
    head = WALL_LISTING_RE.match(lines[0]) if lines else None
    if not head or head.group(2) != "feed":
        return False
    named = [m.group(1) for m in (WALL_LISTING_DOC_RE.match(ln) for ln in lines[1:]) if m]
    folder = Path(saved_dir) / "feed"
    files = sorted(f.stem for f in folder.glob("*.json")) if folder.is_dir() else []
    return int(head.group(1)) == len(named) == len(files) and sorted(named) == files


def _wall_carried(answers: List[Dict[str, Any]], versions_dir: str, now_iso=None,
                  feed_listing: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
    """cr1#1837: the off-limits list a pulled part carried, `(doc, why_not)`.
    The list must match the digest its part was made with, be sound, not be
    dated more than five minutes ahead of this computer's clock, and not be
    older than the page's own copy in `versions_dir`; otherwise it is not
    published, saying why. `(None, "")` when no part carried one."""
    have = [a for a in answers if isinstance((a.get("pages") or {}).get(WALL_CARRY_DOC), dict)]
    if not have:
        return None, ""
    ans = have[-1]
    doc = ans["pages"][WALL_CARRY_DOC]
    given = str((ans.get("digests") or {}).get(WALL_CARRY_DOC) or "")
    if not wall_sound(doc) or not given or wall_digest(doc) != given:
        return None, WALL_NOT_SAVED_LINE
    made = _parse(doc.get("generated_at"))
    now = _parse(now_iso) if now_iso else _dt.datetime.now(_dt.timezone.utc)
    if made is None or now is None:
        return None, WALL_NOT_SAVED_LINE
    if (made - now).total_seconds() > WALL_AHEAD_MAX_S:
        return None, WALL_AHEAD_LINE
    # Review round 1 of #1837 (must 2): the #1772 fence. Without the page's own
    # read of its feeds nothing is published: a read that failed or was not
    # saved could hide a newer (or a held) list. A page with no list yet still
    # has its other feeds in that read, which is the way through.
    feed = Path(versions_dir) / "feed" if versions_dir else None
    saved = _saved_docs(versions_dir, "feed") if feed is not None else []
    if feed is None or not saved:
        return None, WALL_NO_PAGE_READ_LINE
    stored, stored_v = next(((f, v) for d, f, v in saved if d == "wall"), (None, None))
    if stored is None and (feed / "wall.json").exists():
        return None, WALL_NO_PAGE_READ_LINE  # the page's copy is there and could not be read
    # Delta check of 5319fe083 (required 2): a copy whose version cannot be
    # read cannot be pinned, so the list is not written over it
    if stored is not None and stored_v is None:
        return None, WALL_NO_PAGE_READ_LINE
    # Delta check of 5319fe083 (required 1): "no list yet" only when the
    # page's own listing of its feeds names exactly the documents saved, so a
    # listing cut short never looks like a page with no list
    if stored is None and not (feed_listing and feed_listing_matches(feed_listing, versions_dir)):
        return None, WALL_NO_PAGE_READ_LINE
    was = _parse(stored.get("generated_at")) if isinstance(stored, dict) else None
    if isinstance(stored, dict) and was is None:
        return None, WALL_NO_PAGE_READ_LINE  # a copy with no date cannot be shown older
    if was is not None and was > made:
        return None, WALL_OLDER_LINE
    return doc, ""


def unpack_refresh(files: List[str], out_dir: str, versions_dir: str = "", *,
                   token: str = "", parts: Optional[int] = None,
                   pins: Optional[Dict[str, int]] = None, wall_in_turn: Optional[int] = None,
                   now_iso=None, feed_listing: str = "") -> Dict[str, Any]:
    """Run where the page's database tool runs. Reads the saved answers of
    `refresh_part`, writes one JSON file per document under `out_dir`, and
    returns the batches that set them: `{ok, batches: [[write, ...], ...],
    pages, no_version, line}`. A write carries `if_version` when
    `versions_dir` (a page read saved with `out_dir`) holds that document's
    version, or when `pins` names it (`{"status/maintenance": 4}`); a
    document listed in `no_version` has neither: it is new (no pin) or its
    version comes from the read's own result.

    With `token` only that refresh's parts count, and with `parts` every part
    from 0 to one less must be there: a part that is missing or was cut off
    is `ok: false` with `missing`, and nothing is to be sent.

    cr1#1837: an Update's off-limits list (`feed/wall`) is checked by
    `_wall_carried` and, when it passes, is the FIRST write of the first
    batch, before any document that names people. `wall_in_turn` is the
    number of the list's part when its answer came back in the run's turn
    instead of as a file: that part was not saved (it is never retyped), it
    is not missing, and the list is not published. Whenever an Update's list
    is not published, the answer says so (`wall_sent: false`, `wall_line`)
    and the run's light, `status/update`, carries it in its line and in
    `wall`, so the page and the receipt say it the same day. The padding a
    part carries is never read."""
    found: List[Dict[str, Any]] = []
    for name in files:
        try:
            text = Path(name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for val in _json_values(text):
            _refresh_found(val, found)
    if token:
        found = [a for a in found if str(a.get("token") or "") == str(token)]
    if parts is not None:
        have = {a.get("part") for a in found}
        # Review round 1 of #1837 (should 1): the list is always part 0, so
        # only part 0 is excused, and only when no saved part carries the list
        carried = any(isinstance((a.get("pages") or {}).get(WALL_CARRY_DOC), dict) for a in found)
        excused = {0} if wall_in_turn == 0 and not isinstance(wall_in_turn, bool) and not carried else set()
        missing = [n for n in range(int(parts)) if n not in have and n not in excused]
        if missing:
            return {"ok": False, "missing": missing,
                    "line": "Part(s) " + ", ".join(str(n) for n in missing)
                            + " did not arrive whole: pull them again. Nothing is to be sent."}
    docs: Dict[str, Any] = {}
    for ans in found:
        for doc, body in ans["pages"].items():
            if isinstance(body, dict) and str(doc) in PULL_DOCS and str(doc) != WALL_CARRY_DOC:
                docs[str(doc)] = body
    wall, wall_why = _wall_carried(found, versions_dir, now_iso, feed_listing=feed_listing)
    in_turn = wall_in_turn == 0 and not isinstance(wall_in_turn, bool)
    # Review round 1 of #1837 (must 1): an Update (its light is in the parts)
    # always expects the list, so one with no list part says so too
    expected = "status/update" in docs or bool(wall_why) or wall is not None or in_turn
    if wall is not None:
        docs[WALL_CARRY_DOC] = wall
    elif expected:
        wall_why = wall_why or (WALL_IN_TURN_LINE if in_turn else WALL_NOT_BUILT_LINE)
        light = docs.get("status/update")
        if isinstance(light, dict):
            light = dict(light)
            light["line"] = (str(light.get("line") or "") + " " + WALL_NOT_SENT_LINE).strip()
            light["wall"] = {"sent": False, "line": wall_why}
            docs["status/update"] = light
    # cr1#666, moved here by cr1#1638 (the page's own read is here): an
    # `idle` Maintenance answer over a page that says `done` is a read that
    # failed; the page keeps what it has.
    if versions_dir and str((docs.get("status/maintenance") or {}).get("state") or "") == "idle":
        page_m = next((f for d, f, _v in _saved_docs(versions_dir, "status") if d == "maintenance"), None)
        if isinstance(page_m, dict) and str(page_m.get("state") or "") == "done":
            # Review of #1664 (item 2, cr1#1489): the page keeps its copy, but a
            # copy from before the hide goes back scrubbed, pinned to its version.
            clean = scrub_chats_note(page_m)
            if clean:
                docs["status/maintenance"] = clean
            else:
                del docs["status/maintenance"]
    if not docs:
        return {"ok": False, "line": "No refresh answer was found in those files."}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    batches: List[List[Dict[str, Any]]] = [[]]
    room = REFRESH_BATCH_BUDGET
    no_version: List[str] = []
    # cr1#1837: the off-limits list first, before any document that names people
    for doc in sorted(docs, key=lambda d: (d != WALL_CARRY_DOC, d)):
        text = json.dumps(docs[doc], separators=(",", ":"))
        coll, doc_id = doc.split("/", 1)
        target = out / (coll + "__" + doc_id + ".json")
        target.write_text(text, encoding="utf-8")
        write: Dict[str, Any] = {"op": "set", "collection": coll, "doc_id": doc_id,
                                 "file_path": str(target)}
        version = (pins or {}).get(doc)
        if not (isinstance(version, int) and not isinstance(version, bool) and version >= 1):
            version = _saved_version(versions_dir, coll, doc_id)
        if version is None:
            no_version.append(doc)
        else:
            write["if_version"] = version
        if len(text) > room and batches[-1]:
            batches.append([])
            room = REFRESH_BATCH_BUDGET
        batches[-1].append(write)
        room -= len(text)
    ans = {"ok": True, "batches": batches, "pages": sorted(docs), "no_version": no_version,
           "line": f"{len(docs)} documents ready in {len(batches)} batch(es)."}
    if expected:
        ans["wall_sent"] = wall is not None
        if wall is None:
            ans["wall_line"] = WALL_NOT_SENT_LINE + " " + wall_why
    return ans


def _unpack_refresh_cli(argv: List[str]) -> int:
    out_dir = ""
    versions = ""
    token = ""
    parts: Optional[int] = None
    pins: Dict[str, int] = {}
    files: List[str] = []
    wall_in_turn: Optional[int] = None
    now_cli: Optional[str] = None
    feed_listing = ""
    i = 0
    while i < len(argv):
        if argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]
            i += 2
        elif argv[i] == "--versions" and i + 1 < len(argv):
            versions = argv[i + 1]
            i += 2
        elif argv[i] == "--token" and i + 1 < len(argv):
            token = argv[i + 1]
            i += 2
        elif argv[i] == "--parts" and i + 1 < len(argv) and argv[i + 1].isdigit():
            parts = int(argv[i + 1])
            i += 2
        elif argv[i] == "--wall-in-turn" and i + 1 < len(argv) and argv[i + 1].isdigit():
            wall_in_turn = int(argv[i + 1])
            i += 2
        elif argv[i] == "--feed-listing" and i + 1 < len(argv):
            feed_listing = argv[i + 1]
            i += 2
        elif argv[i] == "--now" and i + 1 < len(argv):  # suites only: a fixed clock
            now_cli = argv[i + 1]
            i += 2
        elif argv[i] == "--pin" and i + 1 < len(argv) and "=" in argv[i + 1]:
            doc, _eq, ver = argv[i + 1].rpartition("=")
            if ver.isdigit():
                pins[doc] = int(ver)
            i += 2
        else:
            files.append(argv[i])
            i += 1
    if not out_dir or not files:
        print(json.dumps({"ok": False, "line": "usage: cockpit_helpers.py unpack-refresh --out <dir> --token <token> --parts <n> [--versions <dir>] [--feed-listing <file>] [--wall-in-turn 0] [--pin <doc>=<version>]... <saved answer>..."}))
        return 2
    ans = unpack_refresh(files, out_dir, versions, token=token, parts=parts, pins=pins,
                         wall_in_turn=wall_in_turn, now_iso=now_cli, feed_listing=feed_listing)
    print(json.dumps(ans))
    return 0 if ans.get("ok") else 1


__all__ = [
    "PREPEDIT_SURFACE", "feed_prepx",
    "TASK_ID", "SOURCE_SKILL", "LEGS", "STATUS_STATES", "COMMITMENT_VERBS",
    "INBOX_VERBS", "INBOX_WIRE_ACTION", "CHOICE_EXPIRY_DAYS",
    "RUNNING_TIMEOUT_MINUTES", "CONFIG_REL", "TEMPLATE_REL",
    "CALENDAR_LIVE", "CALENDAR_LIVE_MARK", "EVENT_KEY_LEN", "calendar_live", "event_key", "prep_one_pick",
    "PREP_ONE_DOC", "PREP_ONE_LINES", "PREP_ONE_NONE_LINE", "PREP_ONE_MAX_AGE_MINUTES", "PREP_ONE_CAP",
    "load_config", "save_config", "setup_check", "capabilities", "connector_display_name", "render_page",
    "render_page_file", "template_path", "status_doc", "running_timed_out",
    "scrub_chats_note", "HIDDEN_CHAT_ACTS",
    "settle_statuses", "feed_today", "feed_plate", "feed_inbox", "feed_projects", "feed_mine", "apply_hub_choices", "plan_apply",
    "apply_commitment_choices", "log_run_receipt", "plan_task_registration",
    "plan_cockpit_registration", "PRE_HUB_NAMES", "mail_direct", "MAIL_DIRECT", "calendar_actions", "CALENDAR_ACTIONS", "record_calendar_changes", "plan_claim", "CLAIM_STALE_MINUTES", "COCKPIT_TASKS", "COCKPIT_TASK_IDS", "SURVIVING_CHATS",
    "newer_message_arrived", "prep_text", "PREP_TEXT_CAP", "sent_tokens_update", "SENT_TOKENS_CAP",
    "plan_prune_choices",
    "add_chat_priority",
    "feed_week", "apply_week_choices", "WEEK_EMPTY_LINE", "feed_urgent",
    "feed_brief", "BRIEF_NONE_LINE", "BRIEF_TEXT_CAP",
    "ORIGIN_LINE_CAP",
    "feed_review", "apply_review_choices", "REVIEW_EMPTY_LINE",
    "land_registration", "LANDING_ID",
    "PROJECT_LIVE_ORDER", "PROJECTS_CAP", "HUB_TABS", "HUB_WORK_VIEWS", "HUB_WORD_TABS", "FILTERED_GROUPS", "feed_filtered", "PRIORITIES_CAP",
    "NO_HOME_CAP",
    "feed_board", "WEEK_AWAY_TIMED_HOURS", "WEEK_DAYS", "WEEK_DAY_MEETINGS", "WEEK_DAY_CARDS", "WEEK_LANES", "WEEK_AWAY_RE",
    "WEEK_AWAY_LEAD_WORDS", "WEEK_AWAY_PERSONAL_DAYS", "WEEK_AWAY_COLLEAGUE_RE",
    "maintenance_status", "START_MAINTENANCE_BEHIND_HOURS",
    "render_from_recipe", "RECIPE_MISMATCH_LINE", "RECIPE_UNREADABLE_LINE",
    "refresh_all", "refresh_part", "unpack_refresh", "REFRESH_READS", "REFRESH_PART_BUDGET",
    "REFRESH_DOC_LIMIT", "REFRESH_BATCH_BUDGET", "REFRESH_PLATE_FAILED_LINE", "REFRESH_DOCS",
    "INVITE_TEXT_CAP", "INVITE_ROWS_CAP", "TODAY_DOC_BUDGET", "PREP_PART_FLAGS", "GUIDE_PART_KINDS",
    "TODAY_INVITE_CUT_LINE", "TODAY_OVER_BUDGET_LINE", "PREPX_UNPLACED_LINE", "PREPX_MOVED_HELD_LINE", "PREPX_GONE_LINE",
    "refresh_start", "REFRESH_BUSY_LINE", "REFRESH_BATCH_MAX",
    "feed_focus", "FOCUS_SURFACE", "feed_allopen",
    "RETIRED_TASKS", "RETIRED_TASK_NAMES", "UPDATE_NARROW", "UPDATE_RECORD_FEEDS", "UPDATE_DOCS",
    "touched_feeds", "update_press_key", "update_press_space", "update_claim", "update_save", "update_build", "update_finish",
    "UPDATE_NOTHING_LINE", "UPDATE_SAVE_FAILED_LINE", "UPDATE_BUSY_LINE", "UPDATE_BUSY_NARROW_LINE",
    "prep_all", "inbox_all", "ONE_CALL_DOCS", "PULL_DOCS", "PREP_ALL_FAILED_LINE", "INBOX_FEED_FAILED_LINE",
    "PREP_FEED_FAILED_LINE", "ONE_CALL_KEEP_FAILED_LINE", "ONE_CALL_GONE_LINES",
]


if __name__ == "__main__":  # pragma: no cover - the chat's own render, cr1#925
    import sys as _sys
    if len(_sys.argv) >= 2 and _sys.argv[1] == "render-recipe":
        _sys.exit(_render_recipe_cli(_sys.argv[2:]))
    if len(_sys.argv) >= 2 and _sys.argv[1] == "unpack-refresh":  # cr1#1530
        _sys.exit(_unpack_refresh_cli(_sys.argv[2:]))
    if len(_sys.argv) >= 2 and _sys.argv[1] == "refresh-start":  # cr1#1638
        _sys.exit(_refresh_start_cli(_sys.argv[2:]))
    if len(_sys.argv) >= 2 and _sys.argv[1] == "update-start":  # cr1#1722
        _sys.exit(_refresh_start_cli(_sys.argv[2:], "update"))
    if len(_sys.argv) >= 2 and _sys.argv[1] in ("morning-start", "morning-mark", "morning-finish"):  # cr1#1722 step 2
        _sys.exit(_morning_cli(_sys.argv[2:], _sys.argv[1].split("-", 1)[1]))
    if len(_sys.argv) >= 2 and _sys.argv[1] == "wall-batch":  # cr1#1765
        _wa = _sys.argv[2:]
        _ans = _wa[_wa.index("--answer") + 1] if "--answer" in _wa and _wa.index("--answer") + 1 < len(_wa) else ""
        _out = _wa[_wa.index("--out") + 1] if "--out" in _wa and _wa.index("--out") + 1 < len(_wa) else ""
        _sto = _wa[_wa.index("--stored") + 1] if "--stored" in _wa and _wa.index("--stored") + 1 < len(_wa) else ""
        _empty = _wa[_wa.index("--stored-empty") + 1] if "--stored-empty" in _wa and _wa.index("--stored-empty") + 1 < len(_wa) else ""
        if not _ans or not _out or ("--stored" in _wa) == ("--stored-empty" in _wa) or not (_sto or _empty) \
                or _sto.startswith("--") or _empty.startswith("--"):
            print(json.dumps({"ok": False, "batches": [],
                              "line": "usage: cockpit_helpers.py wall-batch --answer <saved answer> (--stored <the page's feed read> | --stored-empty <the saved answer of a list that listed nothing>) --out <folder>"}))
            _sys.exit(2)
        # `--now` is for suites only (a fixed clock); a run never passes it.
        _now = _wa[_wa.index("--now") + 1] if "--now" in _wa and _wa.index("--now") + 1 < len(_wa) else None
        _res = wall_batch(_ans, _out, _sto, now_iso=_now, stored_empty=_empty)
        print(json.dumps(_res))
        _sys.exit(0 if _res.get("ok") else 1)
    print(json.dumps({"ok": False, "line": "usage: cockpit_helpers.py render-recipe <answer.json> <out.html>"}))
    _sys.exit(2)
