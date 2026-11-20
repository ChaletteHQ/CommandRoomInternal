#!/usr/bin/env python3
"""COCKPIT1 (cr1#608) - the cockpit page's engine: what the page shows, what
the page saved, and how a saved choice reaches the book.

WHAT THE COCKPIT IS
-------------------

One private artifact page the owner opens each morning. Nine tabs, option D's
List view (cr1#1013), in two rows since cr1#1285: Today, Brief, Week, Settings
over Inbox, Commitments, Priorities, Work streams, People (`HUB_TABS`; Brief is
the morning brief as written, cr1#1310; Overdue and Review are the Late and To
confirm filters inside Commitments since cr1#946 and cr1#1286), one Go button,
one light per leg. The page
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

import datetime as _dt
import hashlib
import json
import os
import re
import secrets
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
    "cockpit-prep": {"leg": "prep", "cron": "45 6 * * 1-5", "label": "6:45 AM weekdays",
                     "orchestrator": "orchestrator-cockpit-prep.md"},
    "cockpit-inbox": {"leg": "inbox", "cron": "15 7 * * 1-5", "label": "7:15 AM weekdays",
                      "orchestrator": "orchestrator-cockpit-inbox.md"},
    "cockpit-apply": {"leg": "apply", "cron": "0 8-15 * * 1-5", "label": "every hour, 8 AM to 3 PM weekdays",
                      "orchestrator": "orchestrator-cockpit-apply.md"},
    "cockpit-refresh": {"leg": "refresh", "cron": "30 7-15 * * 1-5", "label": "half past, 7 AM to 3 PM weekdays",
                        "orchestrator": "orchestrator-cockpit-refresh.md"},
}
COCKPIT_TASK_IDS = tuple(COCKPIT_TASKS)
COMMITMENT_SRC = "commitment-triage"
INBOX_SRC = "cr-inbox"

CONFIG_REL = Path("_hq") / "config" / "cockpit.json"
TEMPLATE_REL = Path("shared") / "templates" / "cockpit" / "cockpit.html"

LEGS = ("prep", "inbox", "apply", "refresh")
SEND_TOKEN_MIN_LEN = 24
STATUS_STATES = ("idle", "running", "done", "error", "no_answer", "waiting")
RUNNING_TIMEOUT_MINUTES = 15
#: A leg that reads and drafts a whole inbox runs longer than the rest; its
#: light is not called dead at 15 minutes while it is still working (cr1#678).
RUNNING_TIMEOUT_BY_LEG = {"inbox": 30}
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
                              "unassign"})
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
#: A heard line already tracked when a later step refused (review of part two).
HEARD_TRACKED_ONLY_LINE = "The line is now tracked, but it was not given a home; it waits under No home yet."
NOT_WORK_FAILED_LINE = "That one could not be set aside; nothing was written."
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
SNOOZE_NEEDS_DATE_LINE = "Snooze needs a date or a number of days; nothing moved."

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
    plugin_version}`. A missing or corrupt file reads as not found; losing the
    memory costs one new page address, never a failed run."""
    out: Dict[str, Any] = {"found": False, "url": None, "trigger_id": None,
                           "server": None, "basename": None,
                           "published_at": None, "plugin_version": None}
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
        "task_id": "cockpit-apply",  # the task `trigger_id` belongs to (four tasks, cr1#619)
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
MAIL_DIRECT = {
    "superhuman": {"server": "Superhuman Mail", "read": "get_thread",
                   "draft": "create_or_update_draft", "send": "send_draft", "thread": "get_thread",
                   "new_draft": "create_or_update_draft"},
    "gmail": {"server": "Gmail", "read": "get_thread", "draft": None, "send": "reply",
              "thread": "get_thread", "new_draft": "create_draft"},
    "outlook": {"server": "Microsoft 365", "read": "outlook_email_search",
                "draft": "outlook_create_reply_draft", "send": "outlook_send_draft",
                "thread": "read_resource", "new_draft": "outlook_create_draft",
                "reply_all_draft": "outlook_create_reply_all_draft", "set_recipients": "outlook_update_draft"},
}
_UUIDISH_RE = re.compile(r"^[0-9a-f]{8}-", re.I)

#: Calendar answers from the page (cr1#723, owner ruling 2026-11-04): Accept,
#: Decline and Tentative on a meeting row, through the seat's declared
#: calendar connector, one map like `MAIL_DIRECT`. Per provider: the
#: connector's usual display name, the respond tool, the names of its event-id
#: and answer arguments, and the words the tool takes for each answer.
#: `verified` says whether the tool's contract was read from the connector
#: itself: Microsoft 365's was (`outlook_respond_to_event`: eventId, response
#: accept|decline|tentative, sendResponse); Google Calendar's is the seam's
#: expected spelling and still needs a seat to confirm it. Superhuman's
#: calendar has no respond tool, so its rows show no calendar answers.
#: `admin_line` is the one plain line a seat sees when the client's admin has
#: not switched writing on, in place of a failure.
CALENDAR_DIRECT = {
    "google_calendar": {"server": "Google Calendar", "respond": "respond_to_event",
                        "event_key": "eventId", "answer_key": "response",
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
              plan.get("reply_all_draft"), plan.get("set_recipients")):
        if t and t not in tools:
            tools.append(t)
    out = {"provider": provider, "server": server, "read": plan["read"],
           "draft": plan["draft"], "send": plan["send"], "thread": plan.get("thread"),
           "new_draft": plan.get("new_draft"), "tools": tools, "admin_line": MAIL_ADMIN_LINE.get(provider)}
    if plan.get("reply_all_draft") and plan.get("set_recipients"):
        out["reply_all_draft"] = plan["reply_all_draft"]
        out["set_recipients"] = plan["set_recipients"]
    return out


def capabilities(server: str, mail: Optional[Dict[str, Any]] = None,
                 calendar: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The runtime capabilities the published page declares. The database
    rule: everything in it, feeds, status, config and choices, is readable
    and writable by the artifact's owner alone (plan item 2: the page shows
    the owner's own people and work, and nobody the page is shared with
    reads its data). The connector is named by its display name whatever
    spelling the caller passed (`connector_display_name`). `mail`, from
    `mail_direct`, adds the seat's mail connector with the tools the page
    sends through (cr1#710)."""
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
    return {
        "db": {
            "rules": [
                {"path": "", "read": "owner", "write": "owner"},
            ]
        },
        "user": {},
        "mcp": {"servers": servers},
    }


def _script_safe(js: str) -> str:
    """JSON made safe inside a <script>: no value can close the tag."""
    return (js.replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def render_page(template_text: str, *, server: str, basename: str,
                plugin_version: str, published_at: Optional[str] = None,
                mail: Optional[Dict[str, Any]] = None,
                calendar: Optional[Dict[str, Any]] = None,
                runtime_version: Optional[str] = None) -> str:
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
    # cr1#925: the stamp is fixed here, so the chat's own render of the
    # recipe is byte-for-byte this page.
    published_at = published_at or _iso(_now())
    html = render_page(path.read_text(encoding="utf-8"), server=server,
                       basename=basename, plugin_version=plugin_version,
                       published_at=published_at, mail=mail, calendar=calendar,
                       runtime_version=runtime_version)
    out = {"ok": True, "html": html, "bytes_len": len(html.encode("utf-8")),
            "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "capabilities": capabilities(server, mail, calendar), "title": "Command Room Hub",
            "mail_direct": (mail or {}).get("provider"), "plugin_version": plugin_version,
            "runtime_version": runtime_version,
            "calendar_direct": (calendar or {}).get("provider"),
            "calendar_verified": (calendar or {}).get("verified"),
            "template": str(TEMPLATE_REL).replace("\\", "/")}
    if recipe:
        out.pop("html")
        out["recipe"] = {"server": server, "basename": basename,
                         "plugin_version": plugin_version,
                         "published_at": published_at, "mail": mail,
                         "calendar": calendar, "runtime_version": runtime_version}
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
                       runtime_version=recipe.get("runtime_version"))
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
        minutes = RUNNING_TIMEOUT_BY_LEG.get(str(doc.get("leg") or ""), RUNNING_TIMEOUT_MINUTES)
        if running_timed_out(doc, now_iso=now_iso, minutes=minutes):
            out.append(status_doc(workspace_root, doc.get("leg"), "no_answer",
                                  STATUS_NO_ANSWER_LINE.format(minutes=minutes),
                                  now_iso=now_iso, run_id=doc.get("run_id"),
                                  stop_reason=doc.get("stop_reason")))
        elif doc.get("state") == "waiting":
            # cr1#679: a Refresh light left waiting on an Apply that never
            # finished stops saying it waits.
            at = _parse(doc.get("at"))
            if at is None or (now - at) >= _dt.timedelta(minutes=WAITING_TIMEOUT_MINUTES):
                out.append(status_doc(workspace_root, doc.get("leg"), "error",
                                      STATUS_WAIT_DROPPED_LINE, now_iso=now_iso))
    return out


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




def _maintenance_trouble(workspace_root) -> Dict[str, Any]:
    """What the fires themselves say went wrong (cr1#822): each job failing and
    each connector not confirmed read, off the receipts, so a scheduled fire is
    read like a typed one. `fires` tells a failing job from one that only left
    a connector unread. A connector is one entry however many jobs read it, by
    the label `run maintenance` says. The receipt keeps no line between "not
    read" and "not confirmed as read", so the words claim only the second.
    `left_unread` (cr1#979) is the due connector jobs the newest run left
    unread; `silent` (cr1#1007) the sources that usually bring items in and
    have read nothing on their last runs. Answers `{failing, unread,
    left_unread, silent, line}`; `line` is empty when there is nothing to
    say, and an unreadable read says nothing."""
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
    if failing:
        bits.append("Failing: " + ", ".join(f"{f['name']} ({_fires(f['fires'])})" for f in failing) + ".")
    if unread:
        bits.append("Not confirmed read: " + ", ".join(f"{u['label']} ({_fires(u['fires'])})" for u in unread) + ".")
    if left:
        bits.append("Due and not run on the last run: " + ", ".join(task_display_name(j) for j in left) + ".")
    try:
        # cr1#1007: a source that usually brings items in and has read
        # nothing on its last runs, read live off the book.
        silent = md.maintenance_silent_sources(workspace_root)
    except Exception:  # noqa: BLE001
        silent = []
    if silent:
        bits.append(md.silent_sources_line(silent))
    return {"failing": failing, "unread": unread, "left_unread": left,
            "silent": silent, "line": " ".join(b for b in bits if b)}


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


def inbox_stop(workspace_root, *, now_iso=None) -> Optional[Dict[str, Any]]:
    """READ. cr1#1464: `{line, at}` when the Hub inbox run's newest stop
    record (`fire_stopped`, task `cockpit-inbox`) is from the last
    `APPLY_TROUBLE_HOURS` and no inbox receipt (`pack_run`) is newer, else
    None. The Hub's note and the Morning Brief both say `line`. Never
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
    return {"line": INBOX_STOP_LINE, "at": _iso(at)}


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
    return {"job_id": "cockpit-apply", "name": task_display_name("cockpit-apply"), "fires": len(streak),
            "since": _iso(streak[-1][0]), "at": _iso(streak[0][0]), "n_failed": _apply_failed_rows(newest),
            "n_choices": len([a for a in newest.get("actions") or [] if isinstance(a, dict)])}


def schedule_holder_status(workspace_root, env: Optional[Dict[str, str]] = None
                           ) -> Optional[Dict[str, Any]]:
    """cr1#1138: which computer and account hold this folder's schedules, for
    the Hub. `{line, computer, account, pending}`: `line` is the health
    check's own sentence (`task_watchdog.scheduled_writer_lines`, one
    producer), `computer` the holder's plain name or None, `account` `this` /
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
    return {"line": lines[0], "computer": held.get("device"),
            "account": account, "pending": bool(held.get("pending"))}


def maintenance_status(workspace_root=None, *, now_iso=None) -> Dict[str, Any]:
    """The Hub's `status/maintenance` document (cr1#666), composed from the
    book, READ/COMPUTE ONLY: a `status_doc` for the `maintenance` leg plus
    `finished_at` (the newest heartbeat's finish time; the newest receipt's
    time on a book from before the heartbeat), `last_completed` (`{job_id:
    UTC ISO}`, each job's newest completion), `hours_behind`
    (`maintenance_dispatcher.maintenance_staleness`, from the newest receipt,
    as of `now_iso`), `behind_hours`, the hours past which it counts as
    behind, and `failing` and `unread` (cr1#822), `left_unread` (cr1#979) and
    `silent` (cr1#1007), which the line names too. A Hub Apply that failed
    (cr1#1378) is a `failing` entry too, but never in the line: it is
    `apply_line`, its own sentence, and `apply_at`, its own run's time
    (cr1#1385), empty when none failed. It
    is data only: Start runs Maintenance on every press
    (cr1#682). A workspace with no Maintenance
    on record answers `idle` with nothing measured; an unreadable book
    answers the same, never raises."""
    now = _now(now_iso)
    doc = status_doc(workspace_root, "maintenance", "idle", MAINTENANCE_NONE_LINE, now_iso=now)
    doc.update({"finished_at": None, "last_completed": {}, "hours_behind": None,
                "behind_hours": START_MAINTENANCE_BEHIND_HOURS, "failing": [], "unread": [],
                "left_unread": [], "silent": [], "apply_line": "", "apply_at": None,
                "inbox_line": "", "inbox_at": None})
    if not workspace_root or not (Path(str(workspace_root)) / "_hq" / "data").is_dir():
        return doc
    holder = schedule_holder_status(workspace_root)
    if holder:
        doc["holder"] = holder
    # cr1#1464: an inbox run that built no page is its own sentence and time,
    # like a failing Apply, whether or not Maintenance has run yet.
    stopped = inbox_stop(workspace_root, now_iso=now)
    if stopped:
        doc.update({"inbox_line": stopped["line"], "inbox_at": stopped["at"]})
    try:
        import maintenance_dispatcher as md

        newest, last, beat, newest_via = _maintenance_history(workspace_root)
        hours = md.maintenance_staleness(workspace_root, now=now).get("hours_behind")
    except Exception:  # noqa: BLE001 - the page keeps its old document
        return doc
    if newest is None:
        return doc
    finished = _parse((beat or {}).get("finished_at"))
    trouble = _maintenance_trouble(workspace_root)
    try:
        # cr1#1378: a Hub Apply whose rows failed is named with the jobs.
        apply_bad = _apply_trouble(workspace_root, now)
    except Exception:  # noqa: BLE001 - the note keeps what it knows
        apply_bad = None
    apply_line, apply_at = "", None
    if apply_bad:
        # cr1#1385: the Apply is its own sentence with its own run's time, never
        # in Maintenance's line, so the page's "After <run>" clause (which names
        # Maintenance's run) never reads as the cause of an Apply failure. The
        # entry stays in `failing`, so the page still shows the note.
        trouble = dict(trouble, failing=list(trouble["failing"]) + [apply_bad])
        apply_line = (f"{apply_bad['name']} failing: {apply_bad['n_failed']} of "
                      f"{apply_bad['n_choices']} choices failed on its last run.")
        apply_at = apply_bad.get("at")
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
        "fired_via": fired_via, "fired_at": fired_at,
        "apply_line": apply_line, "apply_at": apply_at,
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


_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _workspace_tz(workspace_root):
    try:
        from tz import load_workspace_tz

        return load_workspace_tz(str(workspace_root)) or _dt.timezone.utc
    except Exception:  # noqa: BLE001
        return _dt.timezone.utc


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


def _week_dates(today: _dt.date) -> List[_dt.date]:
    return [today + _dt.timedelta(days=i) for i in range(WEEK_DAYS)]


def _day_name(d: _dt.date, today: _dt.date) -> str:
    return "Today" if d == today else d.strftime("%A")


def _week_days(workspace_root, meetings, now: _dt.datetime) -> Dict[str, Any]:
    """The seven days' meetings for the Week view, from the same calendar
    read as the Today list (the run reads seven days for it). Each day: its
    date, its name ("Today" for the first), `away` (the title of an all-day
    event that says time away, else None), its meetings in start order, each
    marked `personal` and, on an away day, `clash` for a business meeting
    with a time. Days are the owner's own, in the workspace's time zone.
    cr1#1299: a day's meetings are counted by the Today feed's rule
    (cr1#1293, `_no_one_else`): a timed event with nobody else on it is
    marked `solo`, stays on the day, is counted in `n_blocks` and not in
    `n_meetings`, and is never a clash."""
    tzinfo = _workspace_tz(workspace_root)
    today = now.astimezone(tzinfo).date()
    dates = _week_dates(today)
    first, last = dates[0], dates[-1]
    by_day: Dict[_dt.date, List[Dict[str, Any]]] = {d: [] for d in dates}
    away: Dict[_dt.date, str] = {}
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
            if WEEK_AWAY_RE.search(title):
                for d in span:
                    away.setdefault(d, title or "time away")
                continue
            for d in span:
                by_day[d].append({"meeting_id": str(m.get("meeting_id") or "").strip(), "title": title,
                                  "start": None, "end": None, "all_day": True,
                                  "personal": personal, "clash": False, "solo": False})
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
        if WEEK_AWAY_RE.search(title) and end - start >= _dt.timedelta(hours=WEEK_AWAY_TIMED_HOURS):
            for d in span:
                away.setdefault(d, title)
            continue
        for d in span:
            by_day[d].append({"meeting_id": str(m.get("meeting_id") or "").strip(), "title": title,
                              "start": _iso(start), "end": _iso(end), "all_day": False,
                              "personal": personal, "clash": False, "solo": _no_one_else(m)})
    days = []
    for d in dates:
        ms = by_day[d]
        if d in away:
            for x in ms:
                x["clash"] = bool(not x["all_day"] and not x["personal"] and not x["solo"])
        ms.sort(key=lambda x: (not x["all_day"], x["start"] or ""))
        days.append({"date": d.isoformat(), "name": _day_name(d, today), "away": away.get(d),
                     "meetings": ms[:WEEK_DAY_MEETINGS],
                     "n_meetings": sum(1 for x in ms if not x["solo"]),
                     "n_blocks": sum(1 for x in ms if x["solo"]),
                     "n_clashes": sum(1 for x in ms if x["clash"])})
    return {"today": today.isoformat(), "days": days}


def feed_board(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The Week view's board (cr1#1017, option D): the next seven days,
    today first, each with its due items as cards, and under the board one
    lane per confirmed priority (ruling B2: suggested ones are not on it)
    with its threads' cards on the same days. Cards come from the open
    list's confirmed rows both ways, never a second count of the log; a card
    past its date sits on today, marked `late`. The meetings come from the
    Today feed's `week` (the calendar read is the prep run's); the page joins
    them by date. An item on no thread names the seat's word ("No thread").
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
    try:
        from mute_ledger import workspace_moved_ids
        moved = workspace_moved_ids(root, _iso(now))
    except Exception:  # noqa: BLE001 - no read: the board shows what the lists show
        moved = set()
    try:
        opens, today, _n, _from_log = tc._open_by_thread(root, _iso(now), keep_ids=moved)
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
    for tid, v in opens.items():
        for direction in ("you_owe", "owed_to_you"):
            for r in v.get(direction) or []:
                cid = str(r.get("id") or "")
                if cid and tid:
                    on_threads.setdefault(cid, set()).add(str(tid))
                due = str(r.get("due") or "")[:10]
                if not cid or cid in seen or not due:
                    continue
                try:
                    d = _dt.date.fromisoformat(due)
                except ValueError:
                    continue
                if d > last:
                    continue
                seen.add(cid)
                late = d < today
                on_day[(today if late else d).isoformat()].append({
                    "id": cid, "title": str(r.get("title") or "")[:240], "due": due,
                    "direction": direction, "late": late, "thread_id": str(tid or ""),
                    "thread": (names.get(str(tid)) or no_home)[:160] if tid else no_home,
                })
    # The items on no thread are not in the per-thread open list: they come
    # from the loose-ends list's unfiled kind, the same read as the Work
    # streams tab's "No home yet", confirmed promises only, like the rest.
    try:
        import loose_ends as le
        view = le.loose_ends_view(root, _iso(now), kind=le.KIND_UNFILED, keep_ids=moved)
    except Exception:  # noqa: BLE001 - the homeless items stay off, and it says so
        out["errors"].append("no_home")
        view = {}
    for r in view.get("rows") or []:
        cid = str(r.get("id") or "")
        direction = str(r.get("direction") or "")
        due = str(r.get("due") or "")[:10]
        if (not cid or cid in seen or not due or direction not in ("you_owe", "owed_to_you")
                or "unconfirmed" in (r.get("kinds") or [])):
            continue
        try:
            d = _dt.date.fromisoformat(due)
        except ValueError:
            continue
        if d > last:
            continue
        seen.add(cid)
        late = d < today
        on_day[(today if late else d).isoformat()].append({
            "id": cid, "title": str(r.get("title") or "")[:240], "due": due, "direction": direction,
            "late": late, "thread_id": "", "thread": no_home})
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


def feed_today(workspace_root, meetings: Optional[List[Dict[str, Any]]] = None,
               *, now_iso=None, device_workspace: Optional[str] = None) -> Dict[str, Any]:
    """The Today feed: the next 24 hours of meetings as the run read them
    from the calendars (`{meeting_id, title, start, end, calendar, others}`
    each, `calendar` one of business or personal, `others` how many people
    besides the owner are on it). A timed event with `others` 0 is a block of
    the owner's own time (`solo`, cr1#1293): it stays on the list and is left
    out of `n_meetings`. Each row comes with its prep when a
    `prep_brief` receipt names one, shown until an hour after the meeting
    ends. The calendar read is the run's; this only joins it to the book."""
    now = _now(now_iso)
    horizon = now + _dt.timedelta(hours=UPCOMING_HOURS)
    hold = _dt.timedelta(minutes=PREP_HOLD_MINUTES)
    tzinfo = None
    rows = []
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
            if end + hold < now or start > horizon:
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
    ids = [r["meeting_id"] for r in rows if r["meeting_id"]]
    if ids:
        try:
            from receipts import prep_receipts

            found = prep_receipts(workspace_root, meeting_ids=ids)
        except Exception:  # noqa: BLE001 - a bad book leaves the rows without prep
            found = []
        newest: Dict[str, Dict[str, Any]] = {}
        for rec in found:
            newest[rec["meeting_id"]] = rec  # oldest first, so the last wins
        for r in rows:
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
                # cr1#655: the brief itself, so the page opens it in place
                r["prep_text"] = prep_text(workspace_root, rel)
                if device_workspace:
                    r["prep_on_pc"] = str(device_workspace).rstrip("/\\") + "/" + rel
            if rec.get("dt"):
                r["prep_at"] = _iso(rec["dt"])
    rows.sort(key=lambda r: r["start"])
    try:
        week = _week_days(workspace_root, meetings, now)
    except Exception:  # noqa: BLE001 - the list never fails for the week's sake
        week = {"today": None, "days": [], "error": True}
    return _with_words(workspace_root, {"generated_at": _iso(now), "meetings": rows,
            "n_meetings": sum(1 for r in rows if not r["solo"]), "n_prepped": sum(1 for r in rows if r["prep"]),
            "week": week, "about": _today_about(workspace_root, now)})


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


def _row_origin(ev: Dict[str, Any], names: Dict[str, str], owner: str, tzinfo) -> Dict[str, Any]:
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
    held = str(d.get("owner_id") or "")
    outside = str(d.get("owner_external") or "").strip()
    if outside or (held and held != owner):
        who = names.get(held) or outside
        who = "from " + who if who else ""
    else:
        cid = str(d.get("counterparty_id") or "")
        cp = ("" if cid == owner else names.get(cid)) or d.get("counterparty_name") \
            or next(iter(d.get("counterparty_names") or []), "") or ""
        who = "to " + str(cp) if cp else ""
    day = str(d.get("meeting_date") or "")[:10]
    captured = not day
    if captured:
        at = _parse(ev.get("ts"))
        day = at.astimezone(tzinfo).date().isoformat() if at else ""
    return {"source": source, "date": day, "captured": captured, "who": who[:120],
            "line": str(d.get("evidence") or "").strip()[:ORIGIN_LINE_CAP],
            "paraphrase": str(d.get("evidence_kind") or "") == "paraphrase"}


_CLOSER_WORDS = {
    "exit-silence": "aged out after going quiet",
    "calendar-close": "closed by a meeting that happened",
    "reconcile-sent": "closed by mail you sent",
    "inbox": "closed by replies in your inbox",
    "commitment-triage": "closed by you",
    "meeting-notes": "closed from meeting notes",
}


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
                closes[cid] = {"at": ts, "how": _CLOSER_WORDS.get(key, "closed by " + key.replace("-", " "))}
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
            rows["meetings"].append({"ts": _iso(at), "source": _src(ev, d),
                                     "title": str(d.get("title") or d.get("meeting_title") or "A meeting")[:240]})
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
HUB_TABS = (("today", "Today"), ("brief", "Brief"), ("week", "Done"), ("settings", "Settings"),
            ("inbox", "Inbox"), ("plate", "Commitments"), ("priorities", "Priorities"),
            ("projects", "Work streams"), ("people", "People"))
#: The tabs whose label is a seat's word (cr1#1017 part 1b), by the key in
#: `words.DEFAULTS`; every other tab keeps its label from `HUB_TABS`.
HUB_TAB_WORDS = {"projects": "work_stream"}


def hub_words(workspace_root) -> Dict[str, Any]:
    """READ (cr1#1017 part 1b, ruling A6). The seat's words as every Hub feed
    carries them under `words`: the word for one piece of work and for a group
    of them, each `{one, many}`, whether the seat set them (`source`, `set`),
    and the tabs in `HUB_TABS` order with the seat's label on each. The page
    reads the labels from here (train 3); a seat with no list reads the ruled
    defaults, which are `HUB_TABS` as written. Never raises: a list that will
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
            for key, text in HUB_TABS]
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
    Never writes."""
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
    out: Dict[str, int] = {}
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
        out[pid] = len(ids)
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


def _feed_priorities(tc, root: Path, now_iso: str) -> Dict[str, Any]:
    """The Priorities tab: the confirmed priorities and the suggested ones,
    apart (ruling B2), from the priority cards. No money. cr1#1294: each
    confirmed priority says how many live items stand behind it (`n_open`),
    left off when the open list cannot be read."""
    pv = tc.priorities_view(root, now_iso)
    counts = _priority_open(root, now_iso, pv.get("confirmed") or [])
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
    return {"confirmed": conf[:PRIORITIES_CAP], "n_confirmed": len(conf), "suggested": sugg}


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
    for tid, c in (cards.get("threads") or {}).items():
        v = opens.get(tid) or {}
        mine, theirs = list(v.get("you_owe") or []), list(v.get("owed_to_you") or [])
        nxt = tc.pick_next(mine, theirs, today)
        company = c.get("company") or {}
        key = (c.get("people") or {}).get("key_contact") or {}
        rows.append({
            "id": tid,
            "name": str(c.get("name") or tid)[:160],
            "org": str(company.get("name") or "")[:120],
            "company_id": str(c.get("company_id") or ""),
            "status": str((c.get("status") or {}).get("value") or "active"),
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
REVIEW_VERBS = frozenset(REVIEW_CLOSE_VERBS + REVIEW_ROW_VERBS + REVIEW_ACCEPT_VERBS + (REVIEW_SAME_AS_VERB,))
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
    return _with_words(workspace_root, {"generated_at": iso_now, "rows": rows, "n_open": len(weighed) + n_craft_waiting,
            "n_hidden": max(0, len(weighed) - REVIEW_CAP) + n_craft_waiting,
            "line": "" if rows else REVIEW_EMPTY_LINE, "accept_rates": _review_rates(events),
            "craft": craft})


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
        shared fence; a weak row is parked and closes nothing;
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
                ev = build_commitment_review_dismissed_event(
                    commitment_id=cid, primary_thread_id=str(c.get("thread_id") or ""),
                    source_skill=COMMITMENT_SRC, next_seq=None,
                    proposal_seq=int(c["seq"]) if c.get("seq") is not None else None)
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


#: The Done tab (cr1#780).
WEEK_DAYS = 7
WEEK_EMPTY_LINE = "Nothing was settled for you this week."
WEEK_RULES_LINE = "Nothing else was settled for you this week."
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
    every batch kind shares: one `brain_change_undone` per change undone."""
    out: Dict[str, int] = {}
    for e in events:
        if isinstance(e, dict) and e.get("type") == "brain_change_undone":
            ref = str((e.get("data") or {}).get("batch_ref") or "")
            if ref:
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
    ("applied", "choice applied", "choices applied"),
)
#: The closes that count as done this month: a fact, the owner's word, or
#: the owner's own tap (`flow_measure.route_of`). A silence or lapse close is
#: a letting go, not a thing done.
MONTH_CLOSE_ROUTES = ("fact", "own_word", "tap")
#: How many items travel on the page, newest first. Counts are never capped.
MONTH_ITEMS_CAP = 150
#: A prep slug's last part is the meeting's 8-hex hash (`prep_pipeline.prep_slug`).
_PREP_SLUG_HASH = re.compile(r"(?:^|-)[0-9a-f]{8}$")


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
                              title=str(d.get("title") or d.get("meeting_title") or slug or "A meeting prep")[:240]))
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
                          "title": (f"{len(ok)} choice applied" if len(ok) == 1 else f"{len(ok)} choices applied")
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


def apply_week_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                       *, resolved_by: str = "", now_iso=None) -> Dict[str, Any]:
    """A WRITER. The Done tab's presses (cr1#780), each through its own rail,
    then ONE `apply_choices_applied` audit row:

      * `undo`: `brain_undo.undo_batch`, a reversal the owner pressed; a batch
        already reversed is left alone, and a partial one says so;
      * `bring back`: dormant or retired to active as the owner's act, the
        questions standing on it answered (`_bring_back`);
      * `archive`: `thread_archive.archive_thread`, the one archive path.

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
    for c in rows:
        verb = str(c.get("action") or "").strip().lower()
        n = str(c.get("n") or "").strip()
        r: Dict[str, Any] = {"id": c.get("id"), "action": verb, "n": n}
        handler: Any = None
        try:
            if verb not in WEEK_VERBS or not n:
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
        audit.append({"n": n, "action": verb, "handler_result": handler, "said": r.get("outcome")})  # cr1#1378
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
                "confirmed_at": str(r.get("applied_at") or "")[:10] or None,
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
            voice.append({"skill": skill, "last_refreshed": str(ov.get("last_refreshed") or "")[:10] or None,
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
            "installed_version": plugin_version_here()})


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
                  now: _dt.datetime) -> Dict[str, Dict[str, Any]]:
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
        who = names.get(other[0], "") if other else \
            primary_counterparty_name(ev, workspace_root=str(workspace_root)) or ""
        return {"late": is_overdue(raw, today), "bucket": bucket, "due": due.isoformat() if due else "",
                "due_phrase": render_due_phrase(due.isoformat(), today) if due else "",
                "who": str(who)[:120], "snoozed": str(_commitment_id(ev)) in muted,
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
    dv = plate_data_view(str(workspace_root), now_iso=_iso(now),
                         source_skill=COMMITMENT_SRC)
    rows = _plate_rows(dv)
    # cr1#725: each row's own record, read once, from the same open set the
    # plate draws (personal rows included: the page is the owner's own).
    # Nothing here is fetched live.
    opens: Optional[List[Dict[str, Any]]] = None
    try:
        from cru_match import _commitment_id, load_open_commitments

        ep = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
        names = _person_names(workspace_root)
        owner = _owner_id(workspace_root)
        tzinfo = _workspace_tz(workspace_root)
        opens = load_open_commitments(ep) if ep.exists() else []
        origins = {str(_commitment_id(ev)): _row_origin(ev, names, owner, tzinfo) for ev in opens}
    except Exception:  # noqa: BLE001 - a bad record leaves the note off, never the plate
        origins = {}
    for r in rows:
        r["origin"] = origins.get(str(r.get("id")))
    late_rows: List[Dict[str, Any]] = []
    n_late_hidden = 0
    overdue_ok = False
    try:
        if opens is not None and _owner_id(workspace_root):
            late_rows, n_late_hidden = _overdue_facts(workspace_root, rows, opens, origins, now)
            overdue_ok = True
    except Exception:  # noqa: BLE001 - the Overdue tab says it has no answer; the plate still draws
        pass
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
        "late_rows": late_rows, "n_late_hidden": n_late_hidden, "overdue_ok": overdue_ok,
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
        offers = _chase_offers(workspace_root, opens or [], origins, now)
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
    return _with_words(workspace_root, feed, seat)


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
    rows = []
    for it in parser.items:
        if it.get("n") is None:
            continue
        subject = re.sub(r"^[\s—·-]+", "", it["subject"])
        kind = "calendar_invite" if ("accept" in it["actions"] or "\U0001f4c5" in it["icon"]) else "email_reply"
        thread = None
        cands = by_subject.get(_norm(subject)) or by_subject.get(_norm(it["subject"])) or []
        if len(cands) == 1:
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
                 "voice_block_sha256": (thread.get("voice_block_sha256") if thread else None)}
        if not to and str(meta.get("to") or "").strip():
            # cr1#1250: the page's To named no one but the owner, so the To
            # above is that To unfiltered. The page never sends a Microsoft
            # 365 group reply on it.
            draft["to_unfiltered"] = True
        rows.append({
            "n": it["n"], "kind": kind, "name": it["name"][:120], "subject": subject[:200],
            "why": it["why"][:240],
            "draft": draft,
            "actions": actions,
            "thread_id": str(thread.get("thread_id") or "") if thread else None,
            "message_id": str(thread.get("message_id") or "") if thread else None,
            "message_ts": str(thread.get("ts") or "") if thread else None,
            "fire_stamp": fire_stamp,
        })
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
PAGE_RECORD_KINDS = ("inbox", "chase", "list")
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
    run_id, writes, entries, taken, held, n_more, line}`: `entries` are the rows
    to hand `record_page_sends` once the batch landed (the ones in `writes`)
    or already held."""
    rid_ = str(run_id or "").strip()
    if len(rid_) < 8:
        return {"ok": False, "run_id": rid_, "writes": [], "entries": [], "taken": [], "held": [],
                "n_more": 0, "line": "A claim needs this run's own id (8 characters or more)."}
    now = _now(now_iso)
    writes: List[Dict[str, Any]] = []
    entries: List[Dict[str, Any]] = []
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
        verb = str(c.get("action") or "").strip().lower()
        res = c.get("result") if isinstance(c.get("result"), dict) else {}
        if str(c.get("kind") or "") not in PAGE_RECORD_KINDS or verb not in PAGE_RECORD_VERBS or res.get("outcome") not in PAGE_RECORD_OUTCOMES:
            continue
        # A list is only ever a draft: the owner sends it from their own mail.
        if str(c.get("kind") or "") == "list" and verb != "draft":
            continue
        seen.add(cid)
        if _record_claim_live(c, now) and str(c.get("record_claimed_by")) != rid_:
            taken.append(cid)
            continue
        draft = c.get("draft") if isinstance(c.get("draft"), dict) else {}
        entry = {"id": cid, "action": verb, "provider": str(c.get("provider") or ""),
                 "draft": {k: str(draft.get(k) or "")[:20000] for k in ("to", "cc", "subject", "body")},
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
    n_more = max(0, len(seen) - len(taken) - len(entries))
    return {"ok": True, "run_id": rid_, "writes": writes, "entries": entries, "taken": taken, "held": held,
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
            if e.get("list_for") and verb != "draft":
                r.update(outcome="refused", line=PAGE_LIST_SEND_LINE)
                results.append(r)
                continue
            if not cid or verb not in PAGE_RECORD_VERBS or not recipient:
                r.update(outcome="refused", line=PAGE_RECORD_NO_RECIPIENT_LINE)
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
                    source_skill=PAGE_CHASE_SOURCE if (e.get("chase_for") or e.get("list_for")) else PAGE_RECORD_SOURCE,
                    snapshot=not (e.get("chase_for") or e.get("list_for")),
                    extra={PAGE_CHOICE_KEY: cid, "sent_from": "hub",
                           **({"chase_for": e["chase_for"]} if e.get("chase_for") else {}),
                           **({"list_for": e["list_for"], "list_direction": e.get("list_direction") or ""}
                              if e.get("list_for") else {})})
                if not res.get("ok"):
                    r.update(outcome="error", line=str(res.get("line") or ROW_ERROR_LINE))
                    results.append(r)
                    continue
                drafted = res.get("event_seq")
            r["drafted_seq"] = drafted
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
                               "title": str(raw.get("title") or "")[:200]})
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
                           "thread_id": raw.get("thread_id"), "title": str(raw.get("title") or "")[:200]})
        elif kind == "week":
            # cr1#780: the Done tab's press, for `apply_week_choices`.
            if verb not in WEEK_VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            week.append({"id": cid, "n": str(raw.get("n") or ""), "action": verb,
                         "payload": raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
                         "title": str(raw.get("title") or "")[:200]})
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
        elif kind in ("reminder", "todo", "miss") or kind in EDIT_KINDS:
            # cr1#662: the Today page's own lists, written by apply_hub_choices;
            # cr1#1017: the header's "Missed something?", answered there too;
            # cr1#1383: a work stream, person or priority added, renamed or
            # taken back (`hub_edit`), written there as well; slice 4: a
            # commitment's wording edited, or that edit taken back.
            verbs = (REMINDER_VERBS if kind == "reminder" else TODO_VERBS if kind == "todo"
                     else MISS_VERBS if kind == "miss" else COMMIT_EDIT_VERBS
                     if kind == "commitment_edit" else CONTEXT_EDIT_VERBS
                     if kind in CONTEXT_EDIT_KINDS else EDIT_VERBS)
            if verb not in verbs:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            hub.append({"id": cid, "kind": kind, "action": verb, "n": raw.get("n"),
                        "input": raw.get("input"), "title": str(raw.get("title") or "")[:200]})
        else:
            refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=kind or "none"),
                            "reason": "unknown_kind"})
    return {"commitment": commitment, "inbox": inbox, "hub": hub, "review": review, "week": week,
            "settings": settings, "mail": mail, "people": people, "refused": refused, "skipped": skipped,
            "n_waiting": (len(commitment) + len(inbox) + len(hub) + len(review) + len(week)
                          + len(settings) + len(mail) + len(people)),
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


def feed_people(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The People tab and the starred people on Home (cr1#1020 slice 3):
    every person on file but the owner, with company, how known, last
    contact and what is open between the owner and them
    (`people_view.people`)."""
    import people_view

    return _with_words(workspace_root, people_view.people(workspace_root, now_iso=_iso(_now(now_iso))))


def feed_mail_rules(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The Inbox tab's rules list (cr1#1020): the owner's mail rules in
    force and the threads dropped, each with one plain line
    (`mail_rules.feed`)."""
    import mail_rules

    return _with_words(workspace_root, mail_rules.feed(workspace_root, now_iso=_iso(_now(now_iso))))


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
#: cr1#1383 part 2: add, rename and undo of a work stream, a person and a
#: priority from the Hub, each through its owning writer (`hub_edit`).
EDIT_KINDS = ("stream_edit", "person_edit", "priority_edit", "commitment_edit")
EDIT_VERBS = frozenset({"add", "rename", "set", "undo"})  # set: cr1#1433
#: cr1#1383 slice 4: a commitment's wording is edited (`rename`) or that edit
#: taken back; a commitment is never added here.
COMMIT_EDIT_VERBS = frozenset({"rename", "undo"})
#: cr1#1383, Add context: a free note on a thread (`stream_edit`) or a person
#: (`person_edit`), taken back by `undo`; never on a work stream, a priority
#: or a commitment edit (named on cr1#1383).
CONTEXT_EDIT_KINDS = ("stream_edit", "person_edit")
CONTEXT_EDIT_VERBS = EDIT_VERBS | {"context"}
TODO_SOURCE = "hub_todo"
TODO_REF_SCHEME = "user:hub_todo"
PRIORITY_NAMES = {1: "High", 2: "Medium", 3: "Low"}
MINE_CAP = 60
REMINDER_NEEDS_TEXT_LINE = "A reminder needs a few words."
#: cr1#1221: a Delete press, applied. The undo is the owner's own word.
REMINDER_DELETED_LINE = "Deleted. Say undo in a chat to bring it back."
REMINDER_NEEDS_DATE_LINE = "A reminder needs a day: a date, or a number of days from today."
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
    two empty lists."""
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
    try:
        _todo_last_actions(root, todos[:MINE_CAP], todo_evs)
    except Exception:  # noqa: BLE001 - an unread last action leaves the line off, never the list
        pass
    return _with_words(workspace_root, {"generated_at": _iso(now), "today": today.isoformat(),
            "reminders": reminders_out, "todos": todos[:MINE_CAP],
            "n_todos": len(todos), "n_reminders": n_reminders, "errors": errors})


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


#: cr1#1315: the chat "Something wrong?" opens (cr1#1288). The page writes
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
    Hub's "Something wrong?" opens a chat with:
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
    """READ ONLY (cr1#1315). The "Something wrong?" chat's answer for a
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
      * reminder `push`: one `reminder_updated` with the new day;
      * reminder `delete` (cr1#1221): one `reminder_deleted`, gone from
        every surface for good (a repeating one never re-arms), carrying
        its undo handle for the owner's `undo`;
      * todo `add`: `commitment_state.create_personal_task` with the priority
        and `source = TODO_SOURCE`, owned by the owner, its pointer unique to
        the choice so the same words added twice are two items;
      * stream_edit, person_edit, priority_edit `add` / `rename` / `undo`
        (cr1#1383 part 2), commitment_edit `rename` / `undo` (slice 4,
        the wording through `edit_commitment_wording`), and stream_edit and
        person_edit `context` (Add context: a thread's notes through
        `update_thread`, a person's fact through `record_person_fact`):
        `hub_edit.apply_one`, through each record's own writer, judged
        before any write;
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
                undated = inp.get("undated") is True and not str(inp.get("when") or "").strip()
                when, why = (None, None) if undated else _hub_day_checked(inp.get("when"), now, root)
                if not text:
                    r.update(outcome="error", line=REMINDER_NEEDS_TEXT_LINE)
                elif undated:
                    today = cs._now_calendar_day(_iso(now), workspace_path=str(root)) or now.date()
                    ev = rm.build_reminder_event(text, remind_from=today, ref=ref, undated=True,
                                                 source_skill=SOURCE_SKILL)
                    ev.setdefault("data", {})["hub_choice"] = choice_id
                    append_event(events_path, ev, holder="cockpit_helpers")
                    r.update(outcome="applied", line=REMINDER_UNDATED_LINE, reminder_id=ev["data"]["id"])
                elif not when:
                    r.update(outcome="error", line=why)
                else:
                    # A day asked for is the day it pins from, as on the Today
                    # page; it is not a deadline (show-my-reminders sets
                    # `due` only for a real one).
                    ev = rm.build_reminder_event(text, remind_from=when, ref=ref, source_skill=SOURCE_SKILL)
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
                if not rid_ or rid_ not in active:
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
                        r.update(outcome="applied", line=f"Moved to {when}.")
            elif kind == "miss" and verb == "tell":
                r.update(_apply_miss(root, c, actor, now, answered, live, inbox))
            elif kind in EDIT_KINDS and (verb in EDIT_VERBS or (verb == "context" and kind in CONTEXT_EDIT_KINDS)):
                import hub_edit

                r.update(hub_edit.apply_one(root, c, actor))
            elif kind == "todo" and verb == "add":
                text = str(inp.get("title") or "").strip()
                ref = f"{TODO_REF_SCHEME}:{c.get('id')}"
                prior = _todo_id(events_path, ref) if choice_id else None
                if not text:
                    r.update(outcome="error", line=TODO_NEEDS_TEXT_LINE)
                elif prior:
                    r.update(outcome="applied", replayed=True, commitment_id=prior,
                             line=f"Added as {PRIORITY_NAMES[_priority(inp.get('priority'))].lower()} priority.")
                else:
                    out = cs.create_personal_task(
                        str(root), title=text[:240], owner_id=actor,
                        source_ref=f"{TODO_REF_SCHEME}:{c.get('id')}",
                        source_skill=SOURCE_SKILL, priority=_priority(inp.get("priority")),
                        source=TODO_SOURCE)
                    cid = ((out or {}).get("commitment") or {}).get("data", {}).get("id")                         or _todo_id(events_path, f"{TODO_REF_SCHEME}:{c.get('id')}")
                    r.update(outcome="applied",
                             line=f"Added as {PRIORITY_NAMES[_priority(inp.get('priority'))].lower()} priority.",
                             commitment_id=cid)
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
                 events: Optional[list] = None) -> tuple:
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
    if not target or target not in streams:
        return {"status": "refused", "error": "not_a_work_stream"}, "refused", ASSIGN_NO_STREAM_LINE
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
    if str(row.get("thread_id") or "").strip():
        return {"status": "has_home", "thread_id": row.get("thread_id")}, "already", ASSIGN_HAS_HOME_LINE
    seq = (row.get("source") or {}).get("seq")
    src = next((e for e in events if isinstance(e, dict) and e.get("seq") == seq), None) if seq is not None else None
    if src is None:
        return {"status": "error", "error": "no_capture_row"}, "error", GONE_LINE
    old_related = list(src.get("related_thread_ids") or [])
    new_related = [t for t in old_related if t != target]
    data = {"origin": ol.HOME_ORIGIN, "commitment_id": cid, "target_thread_id": target,
            "old_primary_thread_id": src.get("primary_thread_id"), "new_primary_thread_id": target,
            "old_related_thread_ids": old_related, "new_related_thread_ids": new_related,
            "reason": ASSIGN_REASON, "cockpit_batch": label}
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
    name = str((streams.get(target) or {}).get("name") or "").strip()
    out_seq = (landed[0] if isinstance(landed, list) and landed else {}).get("seq")
    return ({"status": "assigned", "thread_id": target, "seq": out_seq}, "applied",
            f"Moved to {name}." if name else "Given a home.")


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


def _create_stream(root: Path, name: str, parent_id: Optional[str] = None) -> str:
    """A work stream through the one thread writer: the parent with the
    owner's name (unless an earlier Apply already wrote it) and one thread
    under it, because a work stream is a parent thread. Answers its id."""
    import thread_writer as tw

    org = _own_org(root)
    if not parent_id:
        parent_id = _writer_create(root, canonical_name=name, status="active", org_id=org,
                                   source_skill=SOURCE_SKILL)["id"]
    elif any(isinstance(t, dict) and t.get("parent_thread_id") == parent_id
             for t in tw._threads(tw._load_entities(root))):
        return parent_id  # another Apply finished it just now
    try:
        _writer_create(root, canonical_name=NEW_STREAM_CHILD.format(name=name), status="active",
                       org_id=org, parent_thread_id=parent_id, source_skill=SOURCE_SKILL,
                       skip_dedup=True)
    except _HalfMadeStream:
        raise _HalfMadeStream(parent_id) from None
    return parent_id


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
    if how in ("new", "resume"):
        answered = _no_home_state(root, cid, ledger_open, now)
        if answered is None:
            try:
                target = _create_stream(root, name, target if how == "resume" else None)
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
    if how in ("new", "resume") and outcome == "applied":
        handler["created_stream"] = target
        line = f"Started the work stream {name} and moved this there."
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
    line = UNASSIGN_LINE + tracked
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


def _wait_typed(workspace_root, cid, c, actor, now, label) -> Optional[Dict[str, Any]]:
    """cr1#1291: the Snooze box's words when they set or clear a wait on an
    event rather than give a date (`commitment_wait.apply_wait_text`), under
    the batch's own undo handle (cr1#1322)."""
    from commitment_wait import apply_wait_text
    return apply_wait_text(str(workspace_root), cid, str(c.get("input") or "")[:200],
                           actor_id=actor, source_skill=COMMITMENT_SRC, now_iso=_iso(now),
                           brain_batch_id=label)


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
        is on it.

    Context lands before Done on the same row, so a note and a close pressed
    together both land. A refusal of one row never stops the rest. Answers
    `{ok, results: [{id, commitment_id, action, outcome, line, seq?}],
    n_applied, n_errors, batch_id}`."""
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
    for c in sorted(rows, key=lambda c: (order.get(str(c.get("action") or ""), 2),
                                         str(c.get("created_at") or ""))):
        cid = str(c.get("commitment_id") or "").strip()
        verb = str(c.get("action") or "").strip().lower()
        r: Dict[str, Any] = {"id": c.get("id"), "commitment_id": cid, "action": verb}
        handler: Any = None
        try:
            if verb not in COMMITMENT_VERBS:
                r.update(outcome="error", line=UNKNOWN_VERB_LINE.format(verb=verb or "none"))
                # cr1#1171: an empty handler result is audited ok
                handler = {"status": "error", "error": "unknown_verb", "verb": verb}
            elif verb == "notwork" and cid not in seqs and cid.startswith("obs_"):
                # cr1#1289: a heard line has no capture row of its own.
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
            elif verb in ("assign", "newstream") and cid not in seqs and cid.startswith("obs_"):
                # cr1#1289 part two: a heard line is tracked through the
                # queue's own confirm, then given its home.
                handler, outcome, line = _home_press(workspace_root, cid, verb, c.get("input"), ledger_open,
                                                     now, label, str(c.get("id") or ""), heard=True)
                r.update(outcome=outcome, line=line)
                if handler.get("seq") is not None:
                    r["seq"] = handler["seq"]
            elif (verb in _CLOSING_VERBS and cid in reopens
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
                elif row.get("pending"):
                    from needs_review_queue import done_items

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
                else:
                    handler = cs.close_commitment(
                        str(workspace_root), cid, resolved_by=actor,
                        evidence=CONFIRM_EVIDENCE if verb == "confirm" else DONE_EVIDENCE,
                        source_skill=COMMITMENT_SRC, resolution="done",
                        user_confirmed=True, resolved_by_match="id",
                        extra_data={"cockpit_batch": label})
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
                    handler, outcome, line = _not_work_queued(workspace_root, cid, actor)
                    r.update(outcome=outcome, line=line)
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
                    if folded and counted_now is None:
                        try:
                            counted_now = _counted_ids(workspace_root, now)
                        except Exception:  # noqa: BLE001 - no verdict: cover no copy
                            counted_now = set()
                    full = cs.apply_later_covering_folded(
                        str(workspace_root), cid, when_iso=when, actor_id=actor,
                        source_skill=COMMITMENT_SRC, reason=SNOOZE_REASON,
                        surface=SOURCE_SKILL, brain_batch_id=label, now_iso=_iso(now),
                        folded_ids=folded, counted_ids=counted_now or set())
                    handler = {k: v for k, v in (full or {}).items() if k not in ("covered", "copies")}
                    st = (handler or {}).get("status")
                    if st in ("deferred", "snoozed"):
                        r.update(outcome="applied",
                                 line=str((handler or {}).get("ack") or f"Moved to {when}."))
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
            elif verb == "reopen":
                if cid in open_rows or cid in ledger_open:
                    r.update(outcome="already", line="That one is already open.")
                    handler = {"status": "already_open"}
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
        results.append(r)
        # cr1#1378: the row's own answer goes with it, so the audit can never
        # say ok over a row this writer answered as an error.
        audit.append({"n": cid, "action": verb, "handler_result": handler,
                      "said": r.get("outcome"), "note": f"cockpit {label}"})
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
    if tid != TASK_ID and tid not in COCKPIT_TASKS:
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
# the task's registration (cockpit-setup Step 2)
# ---------------------------------------------------------------------------

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
                      merge: bool = False, source_skill: str = SETUP_SKILL) -> Dict[str, Any]:
    """A WRITER. A created task's trigger row and receipt, through
    `schedule_backend.record_registration` (cr1#734). The id is one argument,
    set on both here, and a missing or unfilled one is refused: the receipt
    lands in a log that is never rewritten, so a placeholder must not reach
    it."""
    import schedule_backend as sb

    tid = str(trigger_id or "").strip()
    if not tid or "<" in tid or ">" in tid:
        return {"ok": False, "reason": "no_trigger_id", "line": NO_TRIGGER_ID_LINE}
    ev = dict(event, trigger_id=tid) if isinstance(event, dict) else event
    out = sb.record_registration(workspace_root, task_id, dict(row or {}, trigger_id=tid), ev,
                                 merge=merge, source_skill=source_skill)
    return dict(out, ok=True)


def plan_task_registration(workspace_root, *, task_id: str = "cockpit-prep",
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
                          writer_pair, env, on_date):
    """Morning Brief and Maintenance through the schedules planner, reshaped
    as cockpit plans. Returns `(plans, refused)`; never raises."""
    import schedule_backend as sb

    basename = str(abs_path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    rows, placed = _chat_rows_placed(workspace_root, registered, basename)
    try:
        ans = sb.plan_registration(
            tools, workspace_root=workspace_root, abs_path=abs_path,
            task_ids=list(SURVIVING_CHATS), registered=rows,
            plugin_version=plugin_version, tz_name=tz_name,
            writer_pair=writer_pair, discover_block=discover_block,
            on_date=on_date, plugin_root=plugin_root, env=env)
    except Exception as exc:  # a planner fault never stops the cockpit
        return [], [{"task_id": t, "line": "Could not plan this chat (%s)." % type(exc).__name__}
                    for t in SURVIVING_CHATS]
    if not ans.get("ok"):
        return [], [{"task_id": t, "line": ans.get("line") or ""} for t in SURVIVING_CHATS]
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
    gone = {str(r.get("task_id")) for r in refused} | set(ans.get("foreign") or [])
    for tid, trigger_id in placed.items():
        if tid not in by_id and tid not in gone:
            by_id[tid] = dict(base, task_id=tid, trigger_id=trigger_id, current=True, not_compared=True)
    plans = [by_id[t] for t in SURVIVING_CHATS if t in by_id]
    return plans, refused


def plan_cockpit_registration(workspace_root, *, tools=None, abs_path: Optional[str] = None,
                              plugin_version: str = "", tz_name: str = "",
                              registered=None, discover_block: Optional[str] = None,
                              plugin_root=None, writer_pair=None, env=None,
                              on_date=None) -> Dict[str, Any]:
    """Every task the cockpit needs, planned and not called (READ/COMPUTE
    ONLY): `{ok, line, backend, plans: [x6], n_create, n_update, n_current,
    stale, chats_refused}`. Plans 1 to 4 are the cockpit tasks
    (`plan_task_registration` answers); plans 5 and 6 are Morning Brief and
    Maintenance, the two chats that survive the cockpit (the owner's ruling of
    2026-11-03 on cr1#619), planned by the schedules skill's own
    `schedule_backend.plan_registration` and handed back in the SAME shape
    (`task_id`, `create` | `update` | `current`), so the set-up executes six
    plans in one loop. The first build planned the two chats in a second call
    described only in prose, and a real set-up skipped it: the owner's seat
    came out with the four cockpit tasks and neither chat (cr1#631). `ok` is
    false, with the first refusal's line, when a cockpit task cannot be
    planned (one scheduler, one account, one answer); a chat the schedules
    planner refuses is listed in `chats_refused` with its line and does not
    stop the cockpit. `stale` lists the one-task shape's
    trigger (`Cockpit - Command Room (<folder>)`, #614) when the listing still
    carries it: the set-up switches it off and says so; the bridge removes it
    (cr1#618).

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
    for tid in COCKPIT_TASK_IDS:
        plans.append(plan_task_registration(
            workspace_root, task_id=tid, tools=tools, abs_path=abs_path,
            plugin_version=plugin_version, tz_name=tz_name, registered=registered,
            discover_block=discover_block, plugin_root=plugin_root,
            writer_pair=writer_pair, env=env, on_date=on_date))
    bad = [p for p in plans if not p.get("ok")]
    chats_refused: List[Dict[str, Any]] = []
    if not bad:
        chats, chats_refused = _plan_surviving_chats(
            workspace_root, tools=tools, abs_path=abs_path,
            plugin_version=plugin_version, tz_name=tz_name, registered=registered,
            discover_block=discover_block, plugin_root=plugin_root,
            writer_pair=writer_pair, env=env, on_date=on_date)
        plans = plans + chats
    out = {"ok": not bad, "line": bad[0]["line"] if bad else "",
           "backend": plans[0].get("backend"), "plans": plans,
           "chats_refused": chats_refused,
           "n_create": sum(1 for p in plans if p.get("create")),
           "n_update": sum(1 for p in plans if p.get("update")),
           "n_current": sum(1 for p in plans if p.get("current")), "stale": []}
    basename = abs_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if basename:
        import schedule_backend as sb

        # The one-task shape (#614), and the four tasks under their names before
        # the rename to Command Room Hub (cr1#670): set-up switches each off.
        olds = [sb.trigger_name(TASK_ID, basename)] + [
            f"{name} - Command Room ({basename})" for name in PRE_HUB_NAMES.values()]
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
                out["stale"].append(entry)
    return out


__all__ = [
    "TASK_ID", "SOURCE_SKILL", "LEGS", "STATUS_STATES", "COMMITMENT_VERBS",
    "INBOX_VERBS", "INBOX_WIRE_ACTION", "CHOICE_EXPIRY_DAYS",
    "RUNNING_TIMEOUT_MINUTES", "CONFIG_REL", "TEMPLATE_REL",
    "load_config", "save_config", "setup_check", "capabilities", "connector_display_name", "render_page",
    "render_page_file", "template_path", "status_doc", "running_timed_out",
    "settle_statuses", "feed_today", "feed_plate", "feed_inbox", "feed_projects", "feed_mine", "apply_hub_choices", "plan_apply",
    "apply_commitment_choices", "log_run_receipt", "plan_task_registration",
    "plan_cockpit_registration", "PRE_HUB_NAMES", "mail_direct", "MAIL_DIRECT", "plan_claim", "CLAIM_STALE_MINUTES", "COCKPIT_TASKS", "COCKPIT_TASK_IDS", "SURVIVING_CHATS",
    "newer_message_arrived", "prep_text", "PREP_TEXT_CAP", "sent_tokens_update", "SENT_TOKENS_CAP",
    "plan_prune_choices",
    "add_chat_priority",
    "feed_week", "apply_week_choices", "WEEK_EMPTY_LINE",
    "feed_brief", "BRIEF_NONE_LINE", "BRIEF_TEXT_CAP",
    "ORIGIN_LINE_CAP",
    "feed_review", "apply_review_choices", "REVIEW_EMPTY_LINE",
    "land_registration", "LANDING_ID",
    "PROJECT_LIVE_ORDER", "PROJECTS_CAP", "HUB_TABS", "FILTERED_GROUPS", "feed_filtered", "PRIORITIES_CAP",
    "NO_HOME_CAP",
    "feed_board", "WEEK_AWAY_TIMED_HOURS", "WEEK_DAYS", "WEEK_DAY_MEETINGS", "WEEK_DAY_CARDS", "WEEK_LANES", "WEEK_AWAY_RE",
    "maintenance_status", "START_MAINTENANCE_BEHIND_HOURS",
    "render_from_recipe", "RECIPE_MISMATCH_LINE", "RECIPE_UNREADABLE_LINE",
]


if __name__ == "__main__":  # pragma: no cover - the chat's own render, cr1#925
    import sys as _sys
    if len(_sys.argv) >= 2 and _sys.argv[1] == "render-recipe":
        _sys.exit(_render_recipe_cli(_sys.argv[2:]))
    print(json.dumps({"ok": False, "line": "usage: cockpit_helpers.py render-recipe <answer.json> <out.html>"}))
    _sys.exit(2)
