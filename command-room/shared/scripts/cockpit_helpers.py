#!/usr/bin/env python3
"""COCKPIT1 (cr1#608) - the cockpit page's engine: what the page shows, what
the page saved, and how a saved choice reaches the book.

WHAT THE COCKPIT IS
-------------------

One private artifact page the owner opens each morning. Three tabs (Today,
Inbox, Commitments), one Start button, one light per leg. The page keeps its
own small database on claude.ai: the feeds a run writes for it, the status
line per leg, and the choices the owner presses. Nothing on the page writes
the book. A run of the `cockpit` scheduled task reads the choices back and
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
`feed_inbox`, `plan_apply`, `status_doc`,
`load_config`, `setup_check`, `render_page`, `capabilities`.

Writers (on `RUN_WRITER_ALLOWLIST`): `feed_plate` (building the plate mints
display numbers under the ledger lock, exactly as `morning_brief_helpers:
plate_lines` is a writer for that reason), `apply_commitment_choices` (the
closes, notes, snoozes and reopens, plus the one audit row),
`save_config` (the page address under `_hq/config/`) and `log_run_receipt`
(the run's one `pack_run` receipt under task id `cockpit`).

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
COMMITMENT_VERBS = frozenset({"done", "context", "confirm", "snooze", "reopen"})
INBOX_VERBS = frozenset({"send", "draft", "snooze"})
#: What each page verb becomes on the apply-choices wire (inbox only; the
#: commitment verbs are applied here, in this module).
INBOX_WIRE_ACTION = {"send": "send", "draft": "draft", "snooze": "snooze 3d"}

FIRE_PAYLOAD_PREFIX = "Command Room Hub"
#: The four tasks' names before the page was renamed Command Room Hub
#: (cr1#670). A seat set up before the rename still carries them until
#: set-up runs again, which switches them off (`stale`).
PRE_HUB_NAMES = {"cockpit-prep": "Cockpit Prep", "cockpit-inbox": "Cockpit Inbox",
                 "cockpit-apply": "Cockpit Apply", "cockpit-refresh": "Cockpit Refresh"}

DONE_EVIDENCE = "Marked done on the Command Room Hub"
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


def capabilities(server: str) -> Dict[str, Any]:
    """The runtime capabilities the published page declares. The database
    rule: everything in it, feeds, status, config and choices, is readable
    and writable by the artifact's owner alone (plan item 2: the page shows
    the owner's own people and work, and nobody the page is shared with
    reads its data). The connector is named by its display name whatever
    spelling the caller passed (`connector_display_name`)."""
    server = connector_display_name(server)
    return {
        "db": {
            "rules": [
                {"path": "", "read": "owner", "write": "owner"},
            ]
        },
        "user": {},
        "mcp": {
            "servers": [
                {"server": str(server),
                 "tools": ["list_triggers", "update_trigger", "fire_trigger"]}
            ]
        },
    }


_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def render_page(template_text: str, *, server: str, basename: str,
                plugin_version: str, published_at: Optional[str] = None,
                trigger_id: Optional[str] = None) -> str:
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
                     trigger_id: Optional[str] = None,
                     plugin_root=None) -> Dict[str, Any]:
    """The door form of `render_page`: reads the shipped template beside this
    module (the staged runtime ships `shared/templates/`), fills it, and
    answers `{ok, html, bytes_len, capabilities, title}` for cockpit-setup to
    publish as it came back. Never typed by hand, never edited after."""
    if not basename:
        basename = (str(workspace_root or "").replace("\\", "/").rstrip("/")
                    .rsplit("/", 1)[-1])
    path = template_path(plugin_root)
    html = render_page(path.read_text(encoding="utf-8"), server=server,
                       basename=basename, plugin_version=plugin_version,
                       published_at=published_at, trigger_id=trigger_id)
    return {"ok": True, "html": html, "bytes_len": len(html.encode("utf-8")),
            "capabilities": capabilities(server), "title": "Command Room Hub",
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
    if leg_id not in LEGS and leg_id not in ("start", "maintenance", "morning-brief"):
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


_CLOSER_WORDS = {
    "exit-silence": "aged out after going quiet",
    "calendar-close": "closed by a meeting that happened",
    "reconcile-sent": "closed by mail you sent",
    "inbox": "closed by replies in your inbox",
    "commitment-triage": "closed by you",
    "meeting-notes": "closed from meeting notes",
    "cockpit": "closed on the Command Room Hub",
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
    do_it = [r for r in rows if r["lane"] in ("do_it", "confirm")]
    order = {"overdue": 0, "today": 1, "this_week": 2, "week": 2}
    focus = sorted(do_it, key=lambda r: (order.get(str(r["horizon"]), 3), r["order"]))[:FOCUS_CAP]
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
    return {"commitment": commitment, "inbox": inbox, "hub": hub, "refused": refused,
            "skipped": skipped,
            "n_waiting": len(commitment) + len(inbox) + len(hub), "n_refused": len(refused)}


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


def feed_mine(workspace_root, *, now_iso=None) -> Dict[str, Any]:
    """READ. The Today page's two lists: the owner's active reminders
    (`reminders.load_active_reminders`, the same fold the morning brief pins
    from, personal ones included: the page is the owner's own) and the open
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

        active = load_active_reminders(root, today, surface="m_facing")
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
                    active = {a.get("id") for a in rm.load_active_reminders(root, now.date(), surface="m_facing")}
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

TASK_CRON = COCKPIT_TASKS["cockpit-prep"]["cron"]
TASK_CRON_LABEL = COCKPIT_TASKS["cockpit-prep"]["label"]
ORCHESTRATOR_FILENAME = COCKPIT_TASKS["cockpit-prep"]["orchestrator"]
NO_SCHEDULER_LINE = ("No scheduler is reachable from this chat, so the Hub task "
                     "was not registered; say `set up my hub` in the Claude app.")
LEGACY_SCHEDULER_LINE = ("The Hub needs the cloud scheduler, which this computer's "
                         "app does not have; it is set up from the Claude app.")


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
    backend's own `CallPlan` as a dict (`tool`, `args`, `after`); the skill
    executes the call and lands its `after` through
    `schedule_backend.registration_payload` exactly as the schedules skill
    does. The cloud scheduler only: the older desktop app's scheduler is
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
        out["create"] = {"tool": plan.tool, "args": plan.args, "after": list(plan.after)}
        return out
    prompt = rec.get("prompt") if isinstance(rec, dict) else getattr(rec, "prompt", None)
    trigger_id = rec.get("trigger_id") if isinstance(rec, dict) else getattr(rec, "trigger_id", None)
    if prompt and sr.prompts_equivalent(body, prompt):
        out["current"] = True
        out["trigger_id"] = trigger_id
        return out
    plan = backend.plan_update(tid, trigger_id=trigger_id, prompt=body)
    if isinstance(plan, sb.Refusal):
        out.update(ok=False, line=plan.line)
        return out
    out["update"] = {"tool": plan.tool, "args": plan.args, "after": list(plan.after)}
    out["trigger_id"] = trigger_id
    return out


#: The two chats of the previous version that survive the cockpit (cr1#618,
#: cr1#619). The cockpit's set-up plans them beside its own four.
SURVIVING_CHATS = ("morning-brief", "maintenance")


def _plan_surviving_chats(workspace_root, *, tools, abs_path, plugin_version,
                          tz_name, registered, discover_block, plugin_root,
                          writer_pair, env, on_date):
    """Morning Brief and Maintenance through the schedules planner, reshaped
    as cockpit plans. Returns `(plans, refused)`; never raises."""
    import schedule_backend as sb

    try:
        ans = sb.plan_registration(
            tools, workspace_root=workspace_root, abs_path=abs_path,
            task_ids=list(SURVIVING_CHATS), registered=registered,
            plugin_version=plugin_version, tz_name=tz_name,
            writer_pair=writer_pair, discover_block=discover_block,
            on_date=on_date, plugin_root=plugin_root, env=env)
    except Exception as exc:  # a planner fault never stops the cockpit
        return [], [{"task_id": t, "line": "Could not plan this chat (%s)." % type(exc).__name__}
                    for t in SURVIVING_CHATS]
    if not ans.get("ok"):
        return [], [{"task_id": t, "line": ans.get("line") or ""} for t in SURVIVING_CHATS]
    by_id: Dict[str, Dict[str, Any]] = {}
    base = {"ok": True, "line": "", "backend": ans.get("backend"), "body": None,
            "create": None, "update": None, "current": False}
    for plan in ans.get("creates") or []:
        by_id[plan.task_id] = dict(base, task_id=plan.task_id,
                                   create={"tool": plan.tool, "args": plan.args, "after": list(plan.after)})
    for plan in ans.get("updates") or []:
        by_id[plan.task_id] = dict(base, task_id=plan.task_id,
                                   trigger_id=(plan.args or {}).get("trigger_id"),
                                   update={"tool": plan.tool, "args": plan.args, "after": list(plan.after)})
    for tid in ans.get("current") or []:
        by_id[tid] = dict(base, task_id=tid, current=True)
    plans = [by_id[t] for t in SURVIVING_CHATS if t in by_id]
    refused = [dict(r) for r in ans.get("refused") or [] if isinstance(r, dict)]
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
    (cr1#618)."""
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
    path = str(abs_path or workspace_root or "").strip()
    basename = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
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
    "plan_cockpit_registration", "PRE_HUB_NAMES", "plan_claim", "CLAIM_STALE_MINUTES", "COCKPIT_TASKS", "COCKPIT_TASK_IDS", "SURVIVING_CHATS",
    "newer_message_arrived", "prep_text", "PREP_TEXT_CAP", "sent_tokens_update", "SENT_TOKENS_CAP",
    "plan_prune_choices",
    "TASK_CRON", "TASK_CRON_LABEL",
    "PROJECT_LIVE_ORDER", "PROJECTS_CAP",
]
