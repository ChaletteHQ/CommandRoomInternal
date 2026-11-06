#!/usr/bin/env python3
"""COCKPIT1 (cr1#608) - the cockpit page's engine: what the page shows, what
the page saved, and how a saved choice reaches the book.

WHAT THE COCKPIT IS
-------------------

One private artifact page the owner opens each morning. Six tabs (Today, Inbox,
Commitments, Projects, Review, Week), one Start button, one light per leg. The page
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
`feed_inbox`, `plan_apply`, `status_doc`, `maintenance_status`,
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
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

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
STATUS_WAIT_DROPPED_LINE = "Not refreshed: Apply took too long. Press Refresh day."
CHOICE_EXPIRY_DAYS = 7
PREP_HOLD_MINUTES = 60
UPCOMING_HOURS = 24
PLATE_ROW_CAP = 150
FOCUS_CAP = 8
CHASE_CAP = 6
SCHEDULE_CAP = 4

#: The page's verbs: a closed subset of the owning surface's own verbs.
COMMITMENT_VERBS = frozenset({"done", "drop", "context", "confirm", "snooze", "reopen"})
INBOX_VERBS = frozenset({"send", "draft", "snooze"})
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
CONFIRM_EVIDENCE = "Confirmed and closed on the Command Room Hub"
REOPEN_REASON = "Reopened from the Command Room Hub"
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
UNKNOWN_VERB_LINE = "The page sent a verb this surface does not take ({verb}); nothing was written."
NO_ACTOR_LINE = "No owner is on record for this workspace, so nothing was written."
GONE_LINE = "That item is no longer on the book; nothing was written."
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
    if declared and declared != pair[0]:
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
#: read live, never stored). A provider not listed here keeps Send on Apply.
MAIL_DIRECT = {
    "superhuman": {"server": "Superhuman Mail", "read": "get_thread",
                   "draft": "create_or_update_draft", "send": "send_draft", "thread": "get_thread"},
    "gmail": {"server": "Gmail", "read": "get_thread", "draft": None, "send": "reply",
              "thread": "get_thread"},
    "outlook": {"server": "Microsoft 365", "read": "outlook_email_search",
                "draft": "outlook_create_reply_draft", "send": "outlook_send_draft",
                "thread": "read_resource"},
}
_UUIDISH_RE = re.compile(r"^[0-9a-f]{8}-", re.I)


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
    for t in (plan["read"], plan["draft"], plan["send"], plan.get("thread")):
        if t and t not in tools:
            tools.append(t)
    return {"provider": provider, "server": server, "read": plan["read"],
            "draft": plan["draft"], "send": plan["send"], "thread": plan.get("thread"),
            "tools": tools}


def capabilities(server: str, mail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
                mail: Optional[Dict[str, Any]] = None) -> str:
    """Fill the template mechanically and refuse a page with a placeholder
    left over or a leak in its visible text. Nothing here is typed by hand:
    cockpit-setup calls this and publishes what it returns."""
    from chat_output_renderer import scan_rendered_html

    fills = {
        "{{TRIGGER_SERVER}}": connector_display_name(server),
        "{{WORKSPACE_BASENAME}}": str(basename or ""),
        "{{PLUGIN_VERSION}}": str(plugin_version or ""),
        "{{PUBLISHED_AT}}": str(published_at or _iso(_now())),
    }
    # The mail plan rides as JSON only (cr1#710); null keeps Send on Apply.
    mail_js = json.dumps(mail if isinstance(mail, dict) else None)
    mail_js = _script_safe(mail_js)
    template_text = template_text.replace("{{MAIL_DIRECT_JSON}}", mail_js)
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
                     plugin_root=None) -> Dict[str, Any]:
    """The door form of `render_page`: reads the shipped template beside this
    module (the staged runtime ships `shared/templates/`), fills it, and
    answers `{ok, html, bytes_len, capabilities, title}` for cockpit-setup to
    publish as it came back. Never typed by hand, never edited after."""
    if not basename:
        basename = (str(workspace_root or "").replace("\\", "/").rstrip("/")
                    .rsplit("/", 1)[-1])
    path = template_path(plugin_root)
    mail = mail_direct(workspace_root)
    html = render_page(path.read_text(encoding="utf-8"), server=server,
                       basename=basename, plugin_version=plugin_version,
                       published_at=published_at, mail=mail)
    return {"ok": True, "html": html, "bytes_len": len(html.encode("utf-8")),
            "capabilities": capabilities(server, mail), "title": "Command Room Hub",
            "mail_direct": (mail or {}).get("provider"),
            "template": str(TEMPLATE_REL).replace("\\", "/")}


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
    `completed_at` when the receipt has one, else the receipt's own time for
    each job in its `jobs_completed` (receipts from before cr1#666)."""
    import maintenance_dispatcher as md
    from receipts import iter_receipts

    newest = None
    last: Dict[str, _dt.datetime] = {}
    for r in iter_receipts(workspace_root, task_ids=[md.MAINTENANCE_TASK_ID]):
        if r.get("type") != md.RECEIPT_EVENT_TYPE or r.get("dt") is None:
            continue
        at = r["dt"]
        newest = at if newest is None or at > newest else newest
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        stamped = data.get(md.COMPLETED_AT_FIELD) if isinstance(data.get(md.COMPLETED_AT_FIELD), dict) else {}
        for job in data.get("jobs_completed") or []:
            if not isinstance(job, str) or not job:
                continue
            when = _parse(stamped.get(job)) or at
            if job not in last or when > last[job]:
                last[job] = when
    beat = None
    from events_io import load_events_org_scoped

    events, _skipped = load_events_org_scoped(workspace_root)
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == md.HEARTBEAT_EVENT_TYPE \
                and isinstance(ev.get("data"), dict):
            beat = ev["data"]
    return newest, last, beat


def _fires(n: int) -> str:
    return f"{n} fire" if n == 1 else f"{n} fires"


def _maintenance_trouble(workspace_root) -> Dict[str, Any]:
    """What the fires themselves say went wrong (cr1#822): each job failing and
    each connector not confirmed read, off the receipts, so a scheduled fire is
    read like a typed one. `fires` tells a failing job from one that only left
    a connector unread. A connector is one entry however many jobs read it, by
    the label `run maintenance` says. The receipt keeps no line between "not
    read" and "not confirmed as read", so the words claim only the second.
    Answers `{failing, unread, line}`; `line` is empty when there is nothing to
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
    bits = []
    if failing:
        bits.append("Failing: " + ", ".join(f"{f['name']} ({_fires(f['fires'])})" for f in failing) + ".")
    if unread:
        bits.append("Not confirmed read: " + ", ".join(f"{u['label']} ({_fires(u['fires'])})" for u in unread) + ".")
    return {"failing": failing, "unread": unread, "line": " ".join(bits)}


def maintenance_status(workspace_root=None, *, now_iso=None) -> Dict[str, Any]:
    """The Hub's `status/maintenance` document (cr1#666), composed from the
    book, READ/COMPUTE ONLY: a `status_doc` for the `maintenance` leg plus
    `finished_at` (the newest heartbeat's finish time; the newest receipt's
    time on a book from before the heartbeat), `last_completed` (`{job_id:
    UTC ISO}`, each job's newest completion), `hours_behind`
    (`maintenance_dispatcher.maintenance_staleness`, from the newest receipt,
    as of `now_iso`), `behind_hours`, the hours past which it counts as
    behind, and `failing` and `unread` (cr1#822), which the line names too. It
    is data only: Start runs Maintenance on every press
    (cr1#682). A workspace with no Maintenance
    on record answers `idle` with nothing measured; an unreadable book
    answers the same, never raises."""
    now = _now(now_iso)
    doc = status_doc(workspace_root, "maintenance", "idle", MAINTENANCE_NONE_LINE, now_iso=now)
    doc.update({"finished_at": None, "last_completed": {}, "hours_behind": None,
                "behind_hours": START_MAINTENANCE_BEHIND_HOURS, "failing": [], "unread": []})
    if not workspace_root or not (Path(str(workspace_root)) / "_hq" / "data").is_dir():
        return doc
    try:
        import maintenance_dispatcher as md

        newest, last, beat = _maintenance_history(workspace_root)
        hours = md.maintenance_staleness(workspace_root, now=now).get("hours_behind")
    except Exception:  # noqa: BLE001 - the page keeps its old document
        return doc
    if newest is None:
        return doc
    finished = _parse((beat or {}).get("finished_at"))
    trouble = _maintenance_trouble(workspace_root)
    doc.update({
        "state": "done",
        "line": " ".join(x for x in (MAINTENANCE_DONE_LINE, trouble["line"]) if x),
        "failing": trouble["failing"], "unread": trouble["unread"],
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


def _local_midnight(value, tzinfo) -> Optional[_dt.datetime]:
    """A date-only calendar value (an all-day event) as midnight where the
    owner is, never UTC midnight (cr1#679)."""
    try:
        d = _dt.date.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return _dt.datetime.combine(d, _dt.time(0), tzinfo)


def feed_today(workspace_root, meetings: Optional[List[Dict[str, Any]]] = None,
               *, now_iso=None, device_workspace: Optional[str] = None) -> Dict[str, Any]:
    """The Today feed: the next 24 hours of meetings as the run read them
    from the calendars (`{meeting_id, title, start, end, calendar}` each,
    `calendar` one of business or personal), each with its prep when a
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
    return {"generated_at": _iso(now), "meetings": rows,
            "n_meetings": len(rows), "n_prepped": sum(1 for r in rows if r["prep"])}


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
                "order": order,
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


def _overnight(workspace_root, now: _dt.datetime, hours: int = 24) -> Dict[str, Any]:
    from events_io import load_events_org_scoped

    since = now - _dt.timedelta(hours=hours)
    closed: Dict[str, int] = {}
    opened = 0
    try:
        events, _skipped = load_events_org_scoped(workspace_root)
    except Exception:  # noqa: BLE001 - no ledger reads as nothing overnight
        events = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ts = _parse(ev.get("ts"))
        if ts is None or ts < since:
            continue
        kind = ev.get("type")
        if kind == "commitment_resolved":
            key = str(ev.get("source_skill") or "other").split(":")[0]
            closed[key] = closed.get(key, 0) + 1
        elif kind == "commitment":
            opened += 1
    lines = [{"n": n, "how": _CLOSER_WORDS.get(k, "closed by " + k.replace("-", " "))}
             for k, n in sorted(closed.items(), key=lambda kv: -kv[1])]
    return {"hours": hours, "closed": sum(closed.values()), "opened": opened, "by": lines}


#: The Projects tab (cr1#660): what counts as a live project, the order the
#: groups show in, and how many rows travel.
PROJECT_LIVE_ORDER = ("active", "scoping", "paused", "dormant")
PROJECT_DONE = ("archived", "completed", "resolved", "retired", "closed")
PROJECTS_CAP = 200


def feed_projects(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The Projects tab: every project that is not archived, completed
    or resolved, with its organization, its status, the last thing that
    happened on it (`thread_activity.derive_from_events`, the C3 rule every
    surface uses) and its open and overdue commitments
    (`cru_match.load_open_commitments`, the counting API). Sorted by group,
    then most recent activity first. Never writes; a missing book answers an
    empty feed."""
    from entities_io import entities_collection, read_entities_json, unwrap_entities

    now = _now(now_iso)
    data_dir = Path(str(workspace_root)) / "_hq" / "data"
    try:
        ent = read_entities_json(data_dir / "entities.json")
    except (FileNotFoundError, ValueError, OSError):
        ent = {}
    if not isinstance(ent, dict):
        ent = {}
    container = unwrap_entities(ent) if ent else {}
    orgs = {}
    for o in (container.get("orgs") or []):
        if isinstance(o, dict) and o.get("id"):
            orgs[o["id"]] = o.get("display_name") or o.get("canonical_name") or o.get("name") or ""
    projects = [p for p in (entities_collection(ent, "projects") if ent else [])
                if isinstance(p, dict) and p.get("id")
                and str(p.get("status") or "active").lower() not in PROJECT_DONE]

    events_path = data_dir / "events.jsonl"
    activity: Dict[str, str] = {}
    events: List[Dict[str, Any]] = []
    try:
        from events_io import load_events_org_scoped
        from thread_activity import ALL_TYPES, derive_from_events

        # The org-scoped reader, never a raw read of the log (the personal
        # firewall's D4c guard): the same rows every other surface counts.
        events = load_events_org_scoped(str(workspace_root))[0] if events_path.exists() else []
        activity = {tid: act.ts.isoformat() for tid, act in derive_from_events(
            events, activity_types=ALL_TYPES, honor_reclassifications=True).items()}
    except Exception:  # noqa: BLE001 - a bad log row leaves the dates blank, never the tab
        pass
    opens: List[Dict[str, Any]] = []
    try:
        from cru_match import _commitment_field, load_open_commitments

        opens = load_open_commitments(events_path, events=events) if events else []
    except Exception:  # noqa: BLE001
        opens = []
    open_n: Dict[str, int] = {}
    over_n: Dict[str, int] = {}
    next_of: Dict[str, Dict[str, str]] = {}
    who_of: Dict[str, Dict[str, int]] = {}
    people = _person_names(workspace_root)
    decided: Dict[str, int] = {}
    week_ago = now - _dt.timedelta(days=7)
    try:
        from decision_match import load_open_decisions
        from event_refs import threads_of as _threads

        # The open set, not raw rows: a decision since superseded or restamped
        # is not one this project decided (cr1#731).
        scoped = {e.get("seq") for e in events if isinstance(e, dict)}
        for ev in (load_open_decisions(events_path) if events_path.exists() else []):
            if ev.get("seq") not in scoped:  # the org-scoped rows only: the personal firewall
                continue
            at = _parse(ev.get("ts"))
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if at is not None and at >= week_ago and not d.get("pending_review"):
                for tid in _threads(ev):
                    decided[tid] = decided.get(tid, 0) + 1
    except Exception:  # noqa: BLE001 - a bad row leaves the count off, never the tab
        decided = {}
    if opens:
        from commitment_state import _now_calendar_day, is_overdue
        from event_refs import threads_of

        try:
            day = _now_calendar_day(_iso(now), workspace_path=str(workspace_root))
            today_iso = day.isoformat() if day else _iso(now)
        except Exception:  # noqa: BLE001
            today_iso = _iso(now)

        for ev in opens:
            try:
                late = is_overdue(_commitment_field(ev, "due"), today_iso)
            except Exception:  # noqa: BLE001
                late = False
            for tid in threads_of(ev):
                open_n[tid] = open_n.get(tid, 0) + 1
                if late:
                    over_n[tid] = over_n.get(tid, 0) + 1
                # cr1#731: the card's next step and key contact.
                due = _commitment_field(ev, "due")
                d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                held = next_of.get(tid)
                if due and (held is None or str(due) < str(held["due"])):
                    next_of[tid] = {"title": str(_commitment_field(ev, "title") or "")[:160], "due": str(due)[:10]}
                who = (people.get(str(d.get("counterparty_id") or "")) or d.get("counterparty_name")
                       or next(iter(d.get("counterparty_names") or []), ""))
                if who:
                    seen = who_of.setdefault(tid, {})
                    seen[str(who)] = seen.get(str(who), 0) + 1

    rows = []
    for p in projects:
        pid = p["id"]
        status = str(p.get("status") or "active").lower()
        rows.append({
            "id": pid,
            "name": str(p.get("display_name") or p.get("canonical_name") or p.get("name") or pid)[:160],
            "org": str(orgs.get(p.get("org_id") or p.get("affiliation_id")) or "")[:120],
            "status": status,
            "last_activity": activity.get(pid) or p.get("last_activity") or p.get("first_seen") or None,
            "open": open_n.get(pid, 0),
            "overdue": over_n.get(pid, 0),
            "next": next_of.get(pid),
            "contact": (max(who_of[pid].items(), key=lambda kv: kv[1])[0][:120] if who_of.get(pid) else None),
            "decided_week": decided.get(pid, 0),
        })
    rank = {s: i for i, s in enumerate(PROJECT_LIVE_ORDER)}
    rows.sort(key=lambda r: str(r["last_activity"] or ""), reverse=True)
    rows.sort(key=lambda r: rank.get(r["status"], len(rank)))
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {
        "generated_at": _iso(now),
        "rows": rows[:PROJECTS_CAP], "n_rows": len(rows),
        "n_hidden": max(0, len(rows) - PROJECTS_CAP),
        "by_status": by_status,
    }


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
REVIEW_VERBS = frozenset(REVIEW_CLOSE_VERBS + REVIEW_ROW_VERBS + REVIEW_ACCEPT_VERBS)
#: Why an accept is not on the row. The rest of the row still answers here.
REVIEW_CHAT_WHY = {
    "project": "To accept this one, say `staff meeting` in a chat: a new project needs its folder and a first read of your connectors.",
    "person_merge": "To accept this one, say `staff meeting` in a chat: merging two records cannot be undone.",
    "thread_split": "To accept this one, say `staff meeting` in a chat: a split moves past records between threads.",
    "person_link": "To accept this one, say `staff meeting` in a chat: it names nobody on file to link to.",
}
REVIEW_PERSON_ON_FILE_LINE = "To accept this one, say `staff meeting` in a chat: it changes a record already on file."
REVIEW_PERSON_LIKE_LINE = "To accept this one, say `staff meeting` in a chat: someone with a name like this is on file, so it is add or same as."
#: The keys the writers read, off the proposal event, onto the row.
REVIEW_ROW_KEYS = ("proposed_money", "fact", "category", "proposed_role", "to_org_id",
                   "objective_id", "source_event_seq")
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


def _review_verbs(item: Dict[str, Any]) -> List[str]:
    """The verbs this row can be answered with on the Hub, in the row's order."""
    import brain_proposals as bp

    kind = str(item.get("kind") or "")
    own = bp._row_actions(item)
    if kind == "commitment_review":
        return [v for v in own if v in REVIEW_CLOSE_VERBS]
    keep = REVIEW_ROW_VERBS + (REVIEW_ACCEPT_VERBS if _review_accepts(item) else ())
    if str(item.get("id") or "").split(":", 1)[0] in _REVIEW_LEGACY_DECLINES:
        keep += ("not relevant",)
    return [v for v in own if v in keep]


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
    if verb not in REVIEW_ACCEPT_VERBS or verb == "add person":
        return {}
    if kind == "org_money":
        return {"ask": "A different amount, if this one is wrong"}
    if kind == "entity_fact":
        return {"ask": "Different wording, if this is not right", "required": not item.get("fact")}
    if kind == "org":
        return {"ask": "A different name, if this one is wrong"}
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
        return {"generated_at": iso_now, "rows": [], "n_open": 0, "n_hidden": 0, "accept_rates": {},
                "line": "", "errors": ["review"]}
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
    weighed.sort(key=lambda w: w[0])
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
        verbs = [{"action": v, "label": "Yes" if kind == "learned_rule" and v == "confirm proposal" else (display_label(v) or v),
                  **{k: w for k, w in _review_ask(full, v).items() if w}} for v in answers]
        rows.append({
            "id": it.get("id"), "kind": kind, "shape": it.get("shape") or "hygiene",
            "group": "close" if kind == "commitment_review" else ("rule" if "rule" in kind else "question"),
            "title": str(bp._row_name(it))[:240], "line": str(it.get("render_line") or "")[:300],
            "evidence": str(it.get("evidence") or "")[:REVIEW_EVIDENCE_CAP],
            "reason": reason, "commitment_id": it.get("commitment_id"), "seq": it.get("seq"),
            "thread_id": it.get("thread_id"), "verbs": verbs,
            "chat_line": _review_chat_line(full, answers),
            "expires_on": ends.date().isoformat() if ends else None, "default_line": default_line,
            "today": n < REVIEW_TODAY,
            "data": {**bp._row_target_ids(it), **{k: full[k] for k in REVIEW_ROW_KEYS if k in full}},
        })
    return {"generated_at": iso_now, "rows": rows, "n_open": len(weighed),
            "n_hidden": max(0, len(weighed) - REVIEW_CAP),
            "line": "" if rows else REVIEW_EMPTY_LINE, "accept_rates": _review_rates(events)}


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


def _review_tombstones(events_path, row: Dict[str, Any], resolution: str, **kw) -> int:
    """One `person_proposal_resolved` per sighting behind a person row: the
    cluster's seqs and seq-less fingerprints, else the row's own seq."""
    from confirm_flow import build_person_proposal_resolved_event as build
    from event_gate import append_event

    seqs = list(row.get("cluster_seqs") or [])
    prints = list(row.get("cluster_fingerprints") or [])
    if not seqs and not prints and isinstance(row.get("seq"), int) and not str(row.get("id")).startswith("bp_"):
        seqs = [row["seq"]]
    for seq in seqs:
        append_event(events_path, build(seq, resolution=resolution, source_skill=COMMITMENT_SRC, **kw), holder="cockpit_helpers")
    for fp in prints:
        append_event(events_path, build(None, resolution=resolution, source_skill=COMMITMENT_SRC,
                                        proposal_fingerprint=fp, **kw), holder="cockpit_helpers")
    return len(seqs) + len(prints)


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


def _accept_learned_rule(ws, row, text, actor, events_path, now):
    """The one yes `learning_pass` keeps for a voice question: it writes the
    rule and answers the row, so nothing else resolves it. The rule's Undo is on
    the Week tab."""
    from learning_pass import confirm_asked_rule

    res = confirm_asked_rule(ws, str(row.get("id")), resolved_by=actor, source_skill=COMMITMENT_SRC)
    status = res.get("status")
    handler = {"status": status, "answered": True}
    if status == "applied":
        return handler, "applied", str(res.get("line") or "")
    if status in ("already_resolved", "no_question"):
        return handler, "already", REVIEW_GONE_LINE
    return handler, "refused" if status == "refused" else "error", str(res.get("line") or ROW_ERROR_LINE)


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


def _accept_objective_link(ws, row, text, actor, events_path, now):
    _review_objective_pair(ws, row, events_path, now, confirmed=True)
    return {"status": "linked"}, "applied", "Linked."


#: The writer of each kind's yes, the same one the chat's apply-choices names.
_REVIEW_ACCEPTS = {
    "person": _accept_person, "person_link": _accept_person_link, "person_rename": _accept_person_rename,
    "person_update": _accept_person_update, "entity_fact": _accept_entity_fact, "org_money": _accept_org_money,
    "org": _accept_org, "config_drift": _accept_config_drift, "objective_link": _accept_objective_link,
    "learned_rule": _accept_learned_rule,
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
    if verb not in bp._row_actions(row):
        return {"status": "not_this_row"}, "refused", UNKNOWN_VERB_LINE.format(verb=verb)
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
            return {"status": "declined"}, "applied", "Let go."
        handler = hold_item(str(ws), n, source_skill=COMMITMENT_SRC, surface=REVIEW_SURFACE,
                            ttl_days=REVIEW_DECLINE_DAYS, now_iso=_iso(now))
        return handler, "applied", "Let go."
    # A decline: `dismiss proposal` or `proposal not relevant`.
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
        queue writes nothing.

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
            elif (verb in REVIEW_ACCEPT_VERBS or verb == "proposal not relevant"
                  or (verb == "not relevant" and n.split(":", 1)[0] in _REVIEW_LEGACY_DECLINES)
                  or (verb == "dismiss proposal" and kind in ("objective_link", "person_link"))):
                # Read per row: an earlier row in this batch may have answered it.
                by_seq = {e.get("seq"): e for e in _review_events(workspace_root) if isinstance(e, dict)}
                queue = {str(i.get("id")): i for i in bp.load_open_proposals(str(workspace_root), "staff-meeting", now_iso=_iso(now))}
                live = queue.get(n)
                if live is None:
                    handler = {"status": "not_open"}
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
        entry = {"n": n, "action": verb, "handler_result": handler}
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


#: The Week tab (cr1#780).
WEEK_DAYS = 7
WEEK_EMPTY_LINE = "Nothing was settled for you this week."
WEEK_RULES_LINE = "Nothing else was settled for you this week."
WEEK_PARTIAL_LINE = "Part of this could not be put back; say `undo` in a chat."
WEEK_ALREADY_LINE = "That was already put back; nothing was written."
WEEK_VERBS = frozenset({"undo", "bring back", "archive"})
WEEK_SURFACE = "cockpit-week"
WEEK_ITEMS_CAP = 12
WEEK_DECIDED_CAP = 40
_WEEK_DORMANT_VERBS = [{"action": "bring back", "label": "Bring back"}, {"action": "archive", "label": "Archive"}]


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


def feed_week(workspace_root, *, sections: Optional[Dict[str, Any]] = None, now_iso=None) -> Dict[str, Any]:
    """READ. The Week tab (cr1#780): every batch the system settled this week
    (`brain_undo.recent_auto_batches`), each named in its own words, with a way
    back where one is registered; each piece of work that went dormant and
    still is, with the owner's two steps; what the lifecycle pass archived on
    its own, shown and not hidden; and the recap. `sections` is what
    `friday_wrap_helpers:week_sections` answered: the run hands it in, because
    that module renders documents and this one must not import it. Its
    `decided_for_you` narrates what no batch can carry. Never writes. `rules`
    is every learned rule still in force with its own Undo (cr1#821), whatever
    its age; a batch shown there is not shown again under `decided`."""
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
            for ev in events:
                d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
                if bid and d.get("brain_batch_id") == bid:
                    if d.get("brain_change_class"):
                        classes.add(d["brain_change_class"])
                    title = (d.get("title") or d.get("name") or asked.get(str(d.get("proposal_id")))
                             or titled.get(str(d.get("commitment_id") or d.get("id"))) or "")
                    if title and title not in items:
                        items.append(str(title)[:160])
            decided.append({"id": key, "label": str(b.get("label") or "")[:200], "n": n,
                            "at": str(b.get("ts") or "")[:10], "items": items[:WEEK_ITEMS_CAP],
                            "n_more": max(0, len(items) - WEEK_ITEMS_CAP),
                            "reversible": all(c in bu.REVERSERS for c in classes), "batch_ref": ref})
        n_decided = len(decided)
        decided = decided[:WEEK_DECIDED_CAP]
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
                archived.append(row)
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
    empty = not (decided or dormant or archived or recap["changes"] or recap["decided_lines"])
    return {"generated_at": iso_now, "decided": decided, "n_decided": n_decided, "dormant": dormant, "archived": archived,
            "recap": recap, "rules": rules[:WEEK_DECIDED_CAP], "n_rules": len(rules),
            "line": (WEEK_RULES_LINE if rules else WEEK_EMPTY_LINE) if empty and not errors else "", "errors": errors}


def _bring_back(workspace_root, tid: str, actor: str) -> None:
    """A press: dormant to active, as the owner's act. The dormancy question is
    answered too, or the next pass would read the old silence as still standing
    and flip the project straight back. The clock itself is untouched."""
    import brain_proposals as bp
    import lifecycle_pass as lp
    import thread_writer
    from event_gate import append_event
    from thread_archive import build_thread_status_event

    root = str(workspace_root)
    thread_writer.update_thread(root, tid, source_skill=COMMITMENT_SRC, status="active")
    events_path = Path(root) / "_hq" / "data" / "events.jsonl"
    append_event(events_path, [build_thread_status_event(tid, from_status="dormant", to_status="active",
                                                         reason="Brought back on the Command Room Hub",
                                                         source_skill=COMMITMENT_SRC)], holder="cockpit_helpers")
    want = lp.FINGERPRINT_PREFIX + tid
    for ev in _review_events(root):
        d = ev.get("data") if isinstance(ev, dict) and isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "brain_proposal" and str(d.get("fingerprint") or "") == want and d.get("proposal_id"):
            append_event(events_path, [{"type": "brain_proposal_resolved", "source_skill": COMMITMENT_SRC,
                                        "data": {"proposal_id": d["proposal_id"], "user_action": "declined",
                                                 "resolved_by": actor, "note": "Brought back on the Command Room Hub"}}],
                         holder="cockpit_helpers")


def apply_week_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                       *, resolved_by: str = "", now_iso=None) -> Dict[str, Any]:
    """A WRITER. The Week tab's presses (cr1#780), each through its own rail,
    then ONE `apply_choices_applied` audit row:

      * `undo`: `brain_undo.undo_batch`, a reversal the owner pressed; a batch
        already reversed is left alone, and a partial one says so;
      * `bring back`: dormant to active as the owner's act, the dormancy
        question answered (`_bring_back`);
      * `archive`: `thread_archive.archive_thread`, the one archive path.

    A project no longer dormant is left alone. A refusal of one row never
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
                    else:
                        r.update(outcome="error", line=WEEK_PARTIAL_LINE)
                        handler = {"status": "error"}
            elif status_of(n) != "dormant":
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
        audit.append({"n": n, "action": verb, "handler_result": handler})
    if audit:
        ev = build_apply_choices_applied_event(source=COMMITMENT_SRC, actions=audit)
        ev["data"]["surface"] = WEEK_SURFACE
        append_event(events_path, ev, holder="cockpit_helpers")
    return {"ok": True, "results": results, "n_applied": sum(1 for r in results if r["outcome"] == "applied"),
            "n_errors": sum(1 for r in results if r["outcome"] == "error")}


def feed_plate(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """A WRITER (building the plate mints display numbers). The Commitments
    tab and the Today tab's Move these today: the plate's own data view,
    serialized, never re-derived (`plate_view.plate_data_view` is the one
    grouping). Rows beyond `PLATE_ROW_CAP` are counted, not sent."""
    from plate_view import plate_data_view

    now = _now(now_iso)
    dv = plate_data_view(str(workspace_root), now_iso=_iso(now),
                         source_skill=COMMITMENT_SRC)
    rows = _plate_rows(dv)
    # cr1#725: each row's own record, read once, from the same open set the
    # plate draws (personal rows included: the page is the owner's own).
    # Nothing here is fetched live.
    try:
        from cru_match import _commitment_id, load_open_commitments

        ep = Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"
        names = _person_names(workspace_root)
        owner = _owner_id(workspace_root)
        tzinfo = _workspace_tz(workspace_root)
        origins = {str(_commitment_id(ev)): _row_origin(ev, names, owner, tzinfo)
                   for ev in (load_open_commitments(ep) if ep.exists() else [])}
    except Exception:  # noqa: BLE001 - a bad record leaves the note off, never the plate
        origins = {}
    for r in rows:
        r["origin"] = origins.get(str(r.get("id")))
    do_it = [r for r in rows if r["lane"] in ("do_it", "confirm")]
    order = {"overdue": 0, "this_week": 1}
    focus = sorted(do_it, key=lambda r: (order.get(str(r["horizon"]), 2), r["order"]))[:FOCUS_CAP]
    chase = [r for r in rows if r["lane"] == "chase"][:CHASE_CAP]
    schedule = [r for r in rows if r["lane"] == "schedule"][:SCHEDULE_CAP]
    counts = dict(dv.get("plate_counts") or {})
    return {
        "generated_at": _iso(now),
        "header": str(dv.get("header") or ""),
        "quick_read": str(dv.get("quick_read") or ""),
        "counts": counts,
        "block_totals": dict(dv.get("block_totals") or {}),
        "focus": focus, "chase": chase, "schedule": schedule,
        "rows": rows[:PLATE_ROW_CAP], "n_rows": len(rows),
        "n_hidden": max(0, len(rows) - PLATE_ROW_CAP),
        "overnight": _overnight(workspace_root, now),
    }


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
        rows.append({
            "n": it["n"], "kind": kind, "name": it["name"][:120], "subject": subject[:200],
            "why": it["why"][:240],
            "draft": {"to": meta.get("to", ""), "cc": meta.get("cc", ""),
                      "subject": meta.get("subject", ""), "body": body, "blanks": blanks,
                      "voice_block_sha256": (thread.get("voice_block_sha256") if thread else None)},
            "actions": actions,
            "thread_id": str(thread.get("thread_id") or "") if thread else None,
            "message_id": str(thread.get("message_id") or "") if thread else None,
            "message_ts": str(thread.get("ts") or "") if thread else None,
            "fire_stamp": fire_stamp,
        })
    return {"generated_at": _iso(_now(now_iso)), "fire_stamp": fire_stamp,
            "page_rel": page_rel, "header": parser.header, "items": rows,
            "n_items": len(rows), "n_drafted": sum(1 for r in rows if r["draft"]["body"])}


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
            commitment.append({"id": cid, "commitment_id": str(raw.get("n") or "").strip(),
                               "action": verb, "input": raw.get("input"),
                               "created_at": str(raw.get("created_at") or ""),
                               "title": str(raw.get("title") or "")[:200]})
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
            # cr1#780: the Week tab's press, for `apply_week_choices`.
            if verb not in WEEK_VERBS:
                refused.append({"id": cid, "line": UNKNOWN_VERB_LINE.format(verb=verb or "none"),
                                "reason": "unknown_verb"})
                continue
            week.append({"id": cid, "n": str(raw.get("n") or ""), "action": verb,
                         "payload": raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
                         "title": str(raw.get("title") or "")[:200]})
        elif kind in ("reminder", "todo"):
            # cr1#662: the Today page's own lists, written by apply_hub_choices.
            verbs = REMINDER_VERBS if kind == "reminder" else TODO_VERBS
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
            "refused": refused, "skipped": skipped,
            "n_waiting": len(commitment) + len(inbox) + len(hub) + len(review) + len(week), "n_refused": len(refused)}


#: The Today page's own lists (cr1#662): reminders the owner adds, clears or
#: pushes, and priority to-dos the owner adds. Each is a choice the page saves
#: and the apply task writes, like every other press.
REMINDER_VERBS = frozenset({"add", "done", "push"})
TODO_VERBS = frozenset({"add"})
TODO_SOURCE = "hub_todo"
TODO_REF_SCHEME = "user:hub_todo"
PRIORITY_NAMES = {1: "High", 2: "Medium", 3: "Low"}
MINE_CAP = 60
REMINDER_NEEDS_TEXT_LINE = "A reminder needs a few words."
REMINDER_NEEDS_DATE_LINE = "A reminder needs a day: a date, or a number of days from today."
TODO_NEEDS_TEXT_LINE = "A to-do needs a few words."
REMINDER_GONE_LINE = "That reminder is no longer active."
REMINDER_PAST_LINE = "That day has passed; pick today or a later day."
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
            })
    except Exception:  # noqa: BLE001 - no reminders read leaves the list empty, never the page
        reminders_out = []
        errors.append("reminders")
    todos: List[Dict[str, Any]] = []
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
    except Exception:  # noqa: BLE001
        todos = []
        errors.append("todos")
    todos.sort(key=lambda t: (t["priority"], str(t["added"])))
    return {"generated_at": _iso(now), "today": today.isoformat(),
            "reminders": reminders_out, "todos": todos[:MINE_CAP],
            "n_todos": len(todos), "n_reminders": n_reminders, "errors": errors}


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
        if isinstance(ev, dict) and ev.get("type") in ("reminder", "reminder_updated", "reminder_cleared"):
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


def apply_hub_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                      *, resolved_by: str = "", now_iso=None) -> Dict[str, Any]:
    """A WRITER. The Today page's reminder and to-do choices, each through
    the owning writer:

      * reminder `add`: `reminders.capture_reminder`, the user's own words and
        the day they picked (`parse_later_when`: a date, or days from today);
      * reminder `done`: one `reminder_cleared` (a repeating one re-arms);
      * reminder `push`: one `reminder_updated` with the new day;
      * todo `add`: `commitment_state.create_personal_task` with the priority
        and `source = TODO_SOURCE`, owned by the owner, its pointer unique to
        the choice so the same words added twice are two items.

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
                when, why = _hub_day_checked(inp.get("when"), now, root)
                if not text:
                    r.update(outcome="error", line=REMINDER_NEEDS_TEXT_LINE)
                elif not when:
                    r.update(outcome="error", line=why)
                else:
                    ev = rm.build_reminder_event(text, remind_from=when, source_skill=SOURCE_SKILL)
                    ev.setdefault("data", {})["hub_choice"] = choice_id
                    append_event(events_path, ev, holder="cockpit_helpers")
                    r.update(outcome="applied", line=f"Reminder set for {when}.",
                             reminder_id=ev["data"]["id"])
            elif kind == "reminder" and verb in ("done", "push"):
                rid_ = str(c.get("n") or "").strip()
                if active is None:
                    # The owner's day, the one `feed_mine` built the list on (cr1#688).
                    today = cs._now_calendar_day(_iso(now), workspace_path=str(root)) or now.date()
                    active = {a.get("id") for a in rm.load_active_reminders(root, today, surface="m_facing")}
                if not rid_ or rid_ not in active:
                    r.update(outcome="already" if verb == "done" else "error", line=REMINDER_GONE_LINE)
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

    view = build_plate(str(workspace_root), now_iso=now_iso)
    if not isinstance(view, dict) or view.get("ok") is False or view.get("error"):
        return None
    out: Dict[str, Dict[str, Any]] = {}
    for r in view.get("rows") or []:
        if isinstance(r, dict) and r.get("id"):
            out[str(r["id"])] = r
    return out


def apply_commitment_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None,
                             *, resolved_by: str = "", now_iso=None,
                             batch_id: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. The page's commitment choices, each through the owning
    writer, then ONE `apply_choices_applied` audit row for the batch:

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
      * `reopen`: `reopen_commitment`, the page's answer to undo.

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
    seqs = _commitment_seqs(workspace_root)
    open_rows = _open_rows(workspace_root, _iso(now))
    if open_rows is None:
        return {"ok": False, "reason": "plate_unreadable", "line": PLATE_UNREADABLE_LINE,
                "results": [], "n_applied": 0, "n_errors": len(rows), "batch_id": label}
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
    results: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    for c in sorted(rows, key=lambda c: (order.get(str(c.get("action") or ""), 2),
                                         str(c.get("created_at") or ""))):
        cid = str(c.get("commitment_id") or "").strip()
        verb = str(c.get("action") or "").strip().lower()
        r: Dict[str, Any] = {"id": c.get("id"), "commitment_id": cid, "action": verb}
        handler: Any = None
        try:
            if verb not in COMMITMENT_VERBS:
                r.update(outcome="error", line=UNKNOWN_VERB_LINE.format(verb=verb or "none"))
            elif cid not in seqs:
                r.update(outcome="error", line=GONE_LINE)
            elif verb in ("done", "confirm"):
                row = open_rows.get(cid)
                if row is None and cid in ledger_open:
                    row = {"id": cid, "pending": verb == "confirm"}
                if row is None:
                    r.update(outcome="already", line=ALREADY_CLOSED_LINE)
                    handler = {"status": "already_resolved"}
                elif row.get("pending"):
                    from needs_review_queue import done_items

                    handler = done_items(str(workspace_root), [cid], resolved_by=actor,
                                         source_skill="apply-choices", attested_ids=[cid],
                                         now_iso=_iso(now))
                    done = (handler or {}).get("n_done")
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
            elif verb == "context":
                text = str(c.get("input") or "").strip()[:NOTE_CAP]
                if not text:
                    r.update(outcome="error", line=EMPTY_NOTE_LINE)
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
                if not when:
                    r.update(outcome="error", line=SNOOZE_NEEDS_DATE_LINE)
                    handler = {"status": "missing_when"}
                else:
                    handler = cs.apply_later(str(workspace_root), cid, when_iso=when,
                                             actor_id=actor, source_skill=COMMITMENT_SRC,
                                             reason=SNOOZE_REASON, surface=SOURCE_SKILL,
                                             brain_batch_id=label, now_iso=_iso(now))
                    st = (handler or {}).get("status")
                    if st in ("deferred", "snoozed"):
                        r.update(outcome="applied",
                                 line=str((handler or {}).get("ack") or f"Moved to {when}."))
                    else:
                        r.update(outcome="error", line=str((handler or {}).get("ack") or NOT_OPEN_LINE))
            elif verb == "reopen":
                if cid in open_rows or cid in ledger_open:
                    r.update(outcome="already", line="That one is already open.")
                    handler = {"status": "already_open"}
                else:
                    handler = cs.reopen_commitment(str(workspace_root), cid, reopened_by=actor,
                                                   reason=REOPEN_REASON, source_skill=COMMITMENT_SRC)
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
        audit.append({"n": cid, "action": verb, "handler_result": handler,
                      "note": f"cockpit {label}"})
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

    names = {sb.trigger_name(t, basename): t for t in SURVIVING_CHATS}
    stored: Dict[str, str] = {}
    try:
        for tid, row in sb.read_trigger_map(workspace_root).items():
            if tid in SURVIVING_CHATS and isinstance(row, dict) and row.get("trigger_id"):
                stored[str(row["trigger_id"])] = tid
    except Exception:  # noqa: BLE001 - no map leaves the names to decide
        stored = {}
    rows: List[Any] = []
    placed: Dict[str, Any] = {}
    for r in registered or []:
        if isinstance(r, dict) and not r.get("taskId"):
            trig = r.get("trigger_id") or r.get("id")
            tid = names.get(r.get("name")) or stored.get(str(trig))
            if tid:
                r = dict(r, taskId=tid, trigger_id=trig)
                placed[tid] = trig
        rows.append(r)
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
    "ORIGIN_LINE_CAP",
    "feed_review", "apply_review_choices", "REVIEW_EMPTY_LINE",
    "land_registration", "LANDING_ID",
    "PROJECT_LIVE_ORDER", "PROJECTS_CAP",
    "maintenance_status", "START_MAINTENANCE_BEHIND_HOURS",
]
