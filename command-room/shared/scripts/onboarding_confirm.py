#!/usr/bin/env python3
"""Onboarding step 5: confirm what the pull worked out (cr1#1163 part 2).

After the pull (Phase 1) and the Mirror (Phase 2), the owner sees what the
system worked out about them, each answer with its evidence, and confirms or
edits it in one go. Only what the data cannot tell is asked: words or habits
never to use, how cautious closing should be, and how drafted email should
reach the drafts folder (part 2b, moved here from the setup widget).
Where the data is too thin, an item is a plain question instead.

Who is off limits is NEVER asked (owner ruling 2026-11-21, cr1#1587): it is
not an item, so no card, no question and no what's-new setup offers it. An
off-limits answer the owner gives on his own is still saved, through the same
writer (`VOLUNTEERED`), and the wall it sets is unchanged.

EVERY ANSWER SETS A REAL SETTING, through the writer that owns it, and a
reader uses it (owner ruling 2026-11-12: nothing is stored that no reader
uses). The map is `ITEMS` below:

  name             the owner's person record, `people_writer.rename_person`
  sign_off         email-writer's `sign_off` (`save_skill_config`)
  business         the first line under "Who You Are" in BUSINESS_CONTEXT.md,
                   which `context_loader` reads into every skill
  never_wait       inbox "show" rules (`mail_rules.add_rule`), read by
                   `inbox_helpers.select_priority`
  thread_word,
  work_stream_word the word list (`words.set_words`, through the sheet)
  priorities       open objectives (`objective_state.create_objective`, B2:
                   suggested, then confirmed), read by the Hub's Priorities
                   and the morning brief
  morning          the morning brief's time (`schedule_config.set_morning_time`)
  avoid            the chat persona's `never_line`, rendered into CLAUDE.md
  close_caution    `close_mode` on every declared connector, read by every
                   closing job (`ask` -> `propose`, `when_sure` -> `close`)
  draft_posture    email-writer's `draft_posture` (`save_skill_config`), read
                   by email-writer before every draft; once saved, its own
                   first-draft question is not asked again

and, never asked, only taken when the owner says it himself (`VOLUNTEERED`):

  off_limits       inbox "hide" rules per sender (`mail_rules.add_rule`,
                   `off_limits=True`), read by the inbox, and by the scope gate,
                   which keeps their mail out of the record (cr1#1181)

The advisor's sheet (`advisor_sheet.write_sheet`, cr1#1162) keeps every
answer in the owner's words as the record (ruling (a) of 2026-11-12); its
eight questions are filled here rather than asked as a lane of their own.

`plan_confirm` reads what is on file now and the evidence code can compute
(the suggested priorities, the current values); the skill adds what the pull
found and shows the cards. `apply_confirm` takes the confirmed or edited
answers and writes them: every answer is checked before anything is written,
everything a writer could refuse is checked too, and the sheet is written
last with only what landed; a write that still fails is named in the answer. An edit is stored
exactly as typed (spaces tidied only).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SOURCE_SKILL = "command-room-onboarding"
BUSINESS_REL = "_hq/BUSINESS_CONTEXT.md"
#: the line this step writes under "## Who You Are" (replaced on a re-run)
BUSINESS_PREFIX = "In one line, in your words: "
SIGN_OFFS = ("dash_first", "thanks_first", "first_only", "custom")
CLOSE_CAUTION = {"ask": "propose", "when_sure": "close"}
#: cr1#1230: a never-wait answer naming someone off limits is not saved.
NEVER_WAIT_OFF_LIMITS_LINE = ("{value} is off limits, so their mail is kept out of your inbox and "
                              "your records; it was not added to the people you never wait on. "
                              "Take them off the off-limits list on the Hub first.")
#: email-writer's two postures, and the card's own words for them
DRAFT_POSTURES = ("show_first", "auto_queue")
_POSTURE_WORDS = {"show_me_first": "show_first", "show_first": "show_first",
                  "auto_queue": "auto_queue", "auto": "auto_queue"}
MAX_LIST = 12
#: an objective's tracking binding (thread_writer.OBJECTIVE_BINDING_TYPES),
#: not an event type
BIND_ACTIVITY, BIND_SELF = "activity", "self"

#: id -> (proposed from the data | asked, what a confirm changes, in words)
ITEMS: Dict[str, Dict[str, str]] = {
    "name": {"how": "proposed",
             "sets": "This sets the name I use for you everywhere."},
    "sign_off": {"how": "proposed",
                 "sets": "This sets how the emails I draft for you are signed."},
    "business": {"how": "proposed",
                 "sets": "This sets the one line every part of Command Room reads about your business."},
    "never_wait": {"how": "proposed",
                   "sets": "This puts these people's messages at the top of your inbox, always."},
    "thread_word": {"how": "proposed",
                    "sets": "This sets the word I use for one piece of work."},
    "work_stream_word": {"how": "proposed",
                         "sets": "This sets the word I use for a group of them."},
    "priorities": {"how": "proposed",
                   "sets": "This makes these your priorities, on your Priorities list and in your morning brief."},
    "morning": {"how": "proposed",
                "sets": "This sets the time your morning brief arrives."},
    "avoid": {"how": "asked",
              "sets": "This sets a word or habit I never use with you."},
    "close_caution": {"how": "asked",
                      "sets": "This sets whether I close a finished item myself or always ask you first."},
    "draft_posture": {"how": "asked",
                      "sets": "This sets whether a drafted email waits for you to "
                              "look first, or goes into your drafts with one tap."},
}

#: cr1#1587: answers taken only when the owner gives them on his own, never
#: an item: no card, no question, no `set` flag, never offered by the
#: what's-new setup. The writer below saves them as it always has.
VOLUNTEERED: Dict[str, Dict[str, str]] = {
    "off_limits": {"how": "volunteered",
                   "sets": "This hides these people's mail from your inbox and keeps it out of your records."},
}
_SETS: Dict[str, str] = {k: v["sets"] for k, v in list(ITEMS.items()) + list(VOLUNTEERED.items())}

#: the sheet question each answer is recorded under (advisor_sheet.QUESTIONS)
_SHEET_ID = {"name": "name", "business": "business", "thread_word": "thread_word",
             "work_stream_word": "work_stream_word", "never_wait": "people",
             "priorities": "priorities", "morning": "morning", "avoid": "avoid"}


def _ws_block(root: Path) -> dict:
    try:
        data = json.loads((root / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    inner = data.get("entities") if isinstance(data.get("entities"), dict) else data
    ws = inner.get("workspace") if isinstance(inner, dict) else None
    return ws if isinstance(ws, dict) else {}


def _owner(root: Path) -> Optional[str]:
    """The owner's person id, through the one resolver (G34)."""
    from primary_user import resolve_primary_user
    pid = resolve_primary_user(root)
    return str(pid) if pid else None


def _tidy(text) -> Optional[str]:
    if not isinstance(text, str):
        return None
    t = " ".join(text.split())
    return t or None


def _as_list(value) -> Optional[List[str]]:
    if isinstance(value, str):
        value = [v for v in value.replace(";", ",").split(",")]
    if not isinstance(value, list):
        return None
    out = [_tidy(v) for v in value]
    out = [v for v in out if v]
    return out if 0 < len(out) <= MAX_LIST else None


# ---------------------------------------------------------------------------
# the read
# ---------------------------------------------------------------------------

def plan_confirm(workspace_root, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """READ. Each step-5 item with what a confirm changes, its current value
    and the evidence code can compute. Writes nothing; never raises."""
    root = Path(str(workspace_root))
    current: Dict[str, Any] = {k: None for k in ITEMS}
    evidence: Dict[str, Any] = {}
    try:
        pid = _owner(root)
        if pid:
            data = json.loads((root / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
            inner = data.get("entities") if isinstance(data.get("entities"), dict) else data
            for p in inner.get("people") or []:
                if isinstance(p, dict) and p.get("id") == pid:
                    current["name"] = p.get("canonical_name") or p.get("name")
    except Exception:  # noqa: BLE001
        pass
    try:
        from skill_config_writer import load_skill_config
        cfg = load_skill_config(root, "email-writer") or {}
        cfg = cfg.get("config") if isinstance(cfg.get("config"), dict) else cfg
        current["sign_off"] = cfg.get("sign_off")
        current["draft_posture"] = cfg.get("draft_posture")
    except Exception:  # noqa: BLE001
        pass
    try:
        import words
        doc = words.load_words(root)
        current["thread_word"] = words.label(doc, "thread")
        current["work_stream_word"] = words.label(doc, "work_stream")
    except Exception:  # noqa: BLE001
        pass
    try:
        import mail_rules
        rules = mail_rules.active_rules(mail_rules.load(root))
        current["never_wait"] = [r.get("value") for r in rules or []
                                 if isinstance(r, dict) and r.get("mode") == "show"] or None
    except Exception:  # noqa: BLE001
        pass
    try:
        import thread_cards
        sugg = thread_cards.suggested_priorities(root, now_iso)
        evidence["priorities"] = [{"thread_id": (s.get("built_from") or [None])[0],
                                   "statement": s.get("statement"), "why": s.get("why")}
                                  for s in sugg if isinstance(s, dict)]
    except Exception:  # noqa: BLE001
        evidence["priorities"] = []
    try:
        import schedule_config as sc
        view = sc.load_schedule_config(root / "_hq" / "data" / "entities.json")
        current["morning"] = (view.get(sc.MORNING_TASK_ID) or {}).get("label")
        evidence["timezone"] = _ws_block(root).get("user_timezone")
    except Exception:  # noqa: BLE001
        pass
    try:
        import chat_persona
        current["avoid"] = chat_persona.get_chat_persona(root).get("never_line") or None
    except Exception:  # noqa: BLE001
        pass
    try:
        import connector_config as cc
        modes = sorted({(r.get("dials") or {}).get("close_mode")
                        for r in cc.declared_connector_settings(root)} - {None})
        words_for = {v: k for k, v in CLOSE_CAUTION.items()}
        current["close_caution"] = [words_for.get(m, m) for m in modes] or None
    except Exception:  # noqa: BLE001
        pass
    flags = settings_present(root, current)
    items = [{"id": k, "how": v["how"], "sets": v["sets"],
              "current": current.get(k), "evidence": evidence.get(k),
              "set": bool(flags.get(k))}
             for k, v in ITEMS.items()]
    return {"items": items, "timezone": evidence.get("timezone")}


def _confirmed(root: Path, skill: str, key: str) -> bool:
    """Did the owner confirm `key` of `skill`'s config here (its save logged
    it under `keys_set`)? Never raises."""
    try:
        # the org-scoped reader, the one events door (personal firewall)
        from events_io import load_events_org_scoped
        events, _skipped = load_events_org_scoped(root)
        for ev in events:
            data = ev.get("data") if isinstance(ev, dict) else None
            if isinstance(data, dict) and data.get("skill_name") == skill                     and key in (data.get("keys_set") or []):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def settings_present(root: Path, current: Dict[str, Any]) -> Dict[str, bool]:
    """cr1#1235: per item, whether its SETTING exists on this seat, so a
    later pass (the update bridge's `set_up_whats_new_v1`) never asks it
    again. A ruled default is not a setting: the word list's defaults, the
    morning brief's default time and a connector's kind defaults read as
    unset. Never raises; an unreadable store reads as unset."""
    out: Dict[str, bool] = {k: False for k in ITEMS}
    out["name"] = bool(current.get("name"))
    try:
        from skill_config_writer import load_skill_config
        cfg = load_skill_config(root, "email-writer") or {}
        cfg = cfg.get("config") if isinstance(cfg.get("config"), dict) else cfg
        # email-writer's first draft saves its defaults whole, so its
        # default sign-off is not the owner's choice (cr1#1235 review)
        out["sign_off"] = bool(cfg.get("sign_off")) and (
            cfg.get("sign_off") != "dash_first" or _confirmed(root, "email-writer", "sign_off"))
        out["draft_posture"] = bool(cfg.get("draft_posture"))
    except Exception:  # noqa: BLE001
        pass
    try:
        text = (root / BUSINESS_REL).read_text(encoding="utf-8")
        out["business"] = any(ln.startswith(BUSINESS_PREFIX) for ln in text.splitlines())
    except Exception:  # noqa: BLE001
        pass
    out["never_wait"] = bool(current.get("never_wait"))
    try:
        import words
        seat = set(words.load_words(root).get("set") or [])
        out["thread_word"] = "thread" in seat
        out["work_stream_word"] = "work_stream" in seat
    except Exception:  # noqa: BLE001
        pass
    try:
        import objective_state
        out["priorities"] = bool(objective_state.list_open_objectives(root))
    except Exception:  # noqa: BLE001
        pass
    try:
        import schedule_config as sc
        override = sc._fold_override(root, sc.MORNING_TASK_ID)
        registered = False
        try:
            import schedule_backend as _sb
            registered = sc.MORNING_TASK_ID in (_sb.read_trigger_map(root) or {})
        except Exception:  # noqa: BLE001
            registered = False
        out["morning"] = bool(override.get("cron")) or registered
    except Exception:  # noqa: BLE001
        pass
    out["avoid"] = bool(current.get("avoid"))
    try:
        import connector_config as cc
        # `dials` holds all five once anything is set on a connector, so the
        # owner's own answer is read from `dials_set` (cr1#1235 review)
        out["close_caution"] = any(
            isinstance(row, dict) and "close_mode" in (row.get("dials_set") or [])
            for row in cc.declared_backends(root).values())
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------------------
# the write
# ---------------------------------------------------------------------------

def _refuse(item: str, line: str) -> Dict[str, Any]:
    return {"ok": False, "reason": "refused", "item": item,
            "line": line + " Nothing was saved."}


def _entities(root: Path) -> dict:
    try:
        data = json.loads((root / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    inner = data.get("entities") if isinstance(data.get("entities"), dict) else data
    return inner if isinstance(inner, dict) else {}


def _thread_names(inner: dict) -> set:
    return {str(t.get("canonical_name") or t.get("name") or "").strip().casefold()
            for t in inner.get("threads") or [] if isinstance(t, dict)}


def _open_statements(root: Path) -> set:
    try:
        from objective_state import list_open_objectives
        return {str((r.get("objective") or {}).get("statement") or "").strip().casefold()
                for r in list_open_objectives(root)}
    except Exception:  # noqa: BLE001
        return set()


def objective_name(statement: str) -> str:
    """The objective thread's own name. Never the bare statement: a suggested
    priority's statement is its piece of work's name, and thread names are
    unique, so the bare statement would collide with the work it is about."""
    return f"Priority: {statement}"


def _check(root: Path, answers: Dict[str, Any]) -> tuple:
    """`(clean, refusal)`: every answer checked, and everything a writer could
    refuse checked here too, so a confirm that passes lands whole. Nothing is
    written."""
    clean: Dict[str, Any] = {}
    inner = _entities(root)
    for key, raw in answers.items():
        if key not in ITEMS and key not in VOLUNTEERED:
            return None, _refuse(key, f"There is no step-5 item called {key!r}.")
        if key in ("name", "business", "thread_word", "work_stream_word"):
            t = _tidy(raw)
            if not t:
                return None, _refuse(key, f"The answer for {key.replace('_', ' ')} had no words in it.")
            if key == "name" and len(t.split()) < 2:
                return None, _refuse(key, "A name needs a first and a last name.")
            clean[key] = t
        elif key == "sign_off":
            v = str(raw or "").strip().lower()
            if v not in SIGN_OFFS:
                return None, _refuse(key, "A sign-off is one of: " + ", ".join(SIGN_OFFS) + ".")
            clean[key] = v
        elif key in ("never_wait", "off_limits"):
            vals = _as_list(raw)
            if vals is None:
                return None, _refuse(key, f"Give one to {MAX_LIST} email addresses"
                                     + (" or company domains." if key == "never_wait" else "."))
            import mail_rules
            parsed = []
            for v in vals:
                match = "sender" if "@" in v else "domain"
                norm, err = mail_rules._norm_value(match, v)
                if err or (key == "off_limits" and match != "sender"):
                    return None, _refuse(key, f"\"{v}\" is not an email address"
                                         + ("" if key == "off_limits" else " or a company domain") + ".")
                parsed.append((match, norm, v))
            clean[key] = parsed
        elif key == "priorities":
            vals = raw if isinstance(raw, list) else [raw]
            rows = []
            for v in vals:
                if isinstance(v, str):
                    v = {"statement": v}
                if not isinstance(v, dict) or not _tidy(v.get("statement")):
                    return None, _refuse(key, "Each priority needs its words.")
                rows.append({"statement": _tidy(v["statement"]),
                             "thread_id": _tidy(v.get("thread_id")) if v.get("thread_id") else None})
            if not rows or len(rows) > 5:
                return None, _refuse(key, "Give one to five priorities.")
            clean[key] = rows
        elif key == "morning":
            import schedule_config as sc
            if sc._parse_time_of_day(raw) is None:
                return None, _refuse(key, f"\"{raw}\" is not a time of day, like 7:30 or 8am.")
            clean[key] = str(raw).strip()
        elif key == "avoid":
            import chat_persona
            t = _tidy(raw)
            if not t or not t.lower().startswith("never ") \
                    or not chat_persona._valid_value("never_line", t):
                return None, _refuse(key, "That has to be one short sentence starting with "
                                     "\"Never\", under 90 characters, in the owner's words.")
            clean[key] = t
        elif key == "close_caution":
            v = str(raw or "").strip().lower().replace(" ", "_")
            if v not in CLOSE_CAUTION:
                return None, _refuse(key, "Closing caution is `ask` (always ask first) or `when_sure` (close when sure).")
            clean[key] = v
        elif key == "draft_posture":
            v = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
            v = _POSTURE_WORDS.get(v, v)
            if v not in DRAFT_POSTURES:
                return None, _refuse(key, "Drafts are `show_first` (shown to you first) or "
                                     "`auto_queue` (one tap into your drafts).")
            clean[key] = v
    for key in clean:
        text = clean[key] if isinstance(clean[key], str) else None
        if key in _SHEET_ID and text is not None and len(text) > 1000:
            return None, _refuse(key, "That answer is over 1000 characters; a sentence or two is enough.")
    wc = {w: clean[k] for k, w in (("thread_word", "thread"), ("work_stream_word", "work_stream"))
          if k in clean}
    if wc:
        import words
        trial = words.plan_words(root, {k: (None if words.means_no_word(v) else v)
                                        for k, v in wc.items()})
        if not trial.get("ok"):
            key = "thread_word" if "thread" in (trial.get("keys") or []) else "work_stream_word"
            return None, {"ok": False, "reason": "refused", "item": key,
                          "line": trial.get("line") or "That word cannot be used. Nothing was saved."}
    pid = _owner(root)
    people = {str(p.get("id")): p for p in inner.get("people") or [] if isinstance(p, dict)}
    if "name" in clean:
        if not pid or pid not in people:
            return None, _refuse("name", "There is no owner on file yet to name.")
        # cr1#1447: the writer's own matcher, so a name that is only another
        # person's spelling (in their `aliases`) is caught here too, with a
        # plain line, rather than reaching `rename_person` as its error.
        # Only when the name changes: an unchanged name never reaches the
        # writer (see `_name` below), so it is not refused here either. The
        # whole people list, as the writer walks it.
        import people_writer
        unchanged = str(people[pid].get("canonical_name") or "") == clean["name"]
        other, how, on_file = (None, "", "") if unchanged else \
            people_writer._other_record_holding_name(
                [p for p in inner.get("people") or [] if isinstance(p, dict)],
                pid, clean["name"])
        if how == "name":
            return None, _refuse("name", f"\"{clean['name']}\" is already someone else on file.")
        if how == "alias":
            who = str(other.get("canonical_name") or "").strip() or "someone else"
            return None, _refuse("name", f"\"{on_file}\" is already how {who} is spelled on file. "
                                 "Pick another name.")
    if "business" in clean and not (root / BUSINESS_REL).is_file():
        return None, _refuse("business", "Your business file is not built yet (it comes from the scan).")
    if "never_wait" in clean or "off_limits" in clean:
        import mail_rules
        n_new = len(clean.get("never_wait") or []) + len(clean.get("off_limits") or [])
        if len(mail_rules.active_rules(mail_rules.load(root))) + n_new > mail_rules.RULES_CAP:
            return None, _refuse("never_wait" if "never_wait" in clean else "off_limits",
                                 "Your inbox rules are full.")
    if "priorities" in clean:
        import objective_state as os_
        data = os_._load_entities(root)
        names = _thread_names(inner)
        open_now = _open_statements(root)
        for row in clean["priorities"]:
            if row["thread_id"] and os_._find_thread(data, row["thread_id"]) is None:
                return None, _refuse("priorities", f"There is no piece of work {row['thread_id']!r} on file.")
            if (row["statement"].casefold() not in open_now
                    and objective_name(row["statement"]).casefold() in names):
                return None, _refuse("priorities", f"\"{row['statement']}\" is already on file under "
                                     "another name.")
    if "close_caution" in clean:
        import connector_config as cc
        if not cc.declared_connector_settings(root):
            return None, _refuse("close_caution", "No connector is set up yet to apply that to.")
    return clean, None


def _set_business_line(root: Path, sentence: str) -> None:
    """The confirmed sentence as the first line under "## Who You Are" in
    BUSINESS_CONTEXT.md, which `context_loader` reads into every skill. A line
    this step wrote before is replaced; the rest of the file is kept."""
    from atomic_write import atomic_write_text
    path = root / BUSINESS_REL
    lines = path.read_text(encoding="utf-8").splitlines()
    mine = BUSINESS_PREFIX + sentence
    lines = [ln for ln in lines if not ln.startswith(BUSINESS_PREFIX)]
    at = next((i for i, ln in enumerate(lines) if ln.strip().lower() == "## who you are"), None)
    if at is None:
        lines += ["", "## Who You Are", mine]
    else:
        lines.insert(at + 1, mine)
    atomic_write_text(path, "\n".join(lines) + "\n")


def _repair_line(exc: Exception) -> Optional[str]:
    """The writer's plain refusal when the owner's record needs a repair
    before a rename (cr1#1442, the line cr1#1441 added and the Hub shows),
    else None and the caller keeps its own line."""
    import people_writer
    if isinstance(exc, people_writer.PersonRecordNeedsRepairError):
        return exc.line
    return None


def apply_confirm(workspace_root, answers: Dict[str, Any], *,
                  by: str = "owner", now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. The owner's confirmed or edited step-5 answers, each written
    through the writer that owns it (see `ITEMS`). Every answer, and what its
    writer could refuse, is checked first: one that would be refused stops
    the whole call with its line and nothing is written. The advisor sheet is
    written LAST, with the answers that landed. Answers
    `{ok, applied: [{id, line}], already: [...], failed: [...]}`."""
    root = Path(str(workspace_root))
    if not isinstance(answers, dict) or not answers:
        return {"ok": False, "reason": "nothing_to_save",
                "line": "No answer was given, so nothing changed."}
    clean, refusal = _check(root, answers)
    if refusal:
        return refusal
    applied: List[Dict[str, str]] = []
    already: List[Dict[str, str]] = []
    failed: List[Dict[str, str]] = []

    def run(key, fn):
        try:
            line = fn()
            applied.append({"id": key, "line": line or _SETS[key]})
        except Exception as exc:  # noqa: BLE001 - name it, keep going
            failed.append({"id": key, "line": _repair_line(exc) or
                           f"{key.replace('_', ' ')} was not saved: "
                           f"{str(exc)[:160] or type(exc).__name__}"})

    if "name" in clean:
        def _name():
            import people_writer
            pid = _owner(root)
            cur = next((p for p in _entities(root).get("people") or []
                        if isinstance(p, dict) and p.get("id") == pid), {})
            if str(cur.get("canonical_name") or "") != clean["name"]:
                people_writer.rename_person(root, pid, canonical_name=clean["name"],
                                            reason="onboarding confirm",
                                            source_skill=SOURCE_SKILL, now_iso=now_iso)
        run("name", _name)
    for key in ("sign_off", "draft_posture"):
        if key in clean:
            def _email(key=key):
                from skill_config_writer import load_skill_config, save_skill_config
                cur = load_skill_config(root, "email-writer") or {}
                cur = dict(cur.get("config") if isinstance(cur.get("config"), dict) else cur)
                cur[key] = clean[key]
                # `keys_set` names what the owner confirmed, so a confirmed
                # default still reads as set (cr1#1235 review)
                save_skill_config(root, "email-writer", cur, origin="asked",
                                  event_extra={"keys_set": [key]})
            run(key, _email)
    if "business" in clean:
        run("business", lambda: _set_business_line(root, clean["business"]))
    for key, mode in (("never_wait", "show"), ("off_limits", "hide")):
        if key in clean:
            def _rules(key=key, mode=mode):
                import mail_rules
                for match, norm, _raw in clean[key]:
                    if key == "never_wait" and mail_rules.reopens_off_limits(
                            root, {"action": "add", "mode": "show", "match": match, "value": norm}):
                        # cr1#1230: an always-show here would let an off-limits
                        # sender's mail back into the records without asking.
                        raise ValueError(NEVER_WAIT_OFF_LIMITS_LINE.format(value=norm))
                    got = mail_rules.add_rule(root, mode=mode, match=match, value=norm,
                                              source="onboarding", now_iso=now_iso,
                                              off_limits=(key == "off_limits"))
                    if got.get("outcome") == "refused":
                        raise ValueError(got.get("line"))
            run(key, _rules)
    if "priorities" in clean:
        def _prio():
            import objective_state
            open_now = _open_statements(root)
            made = 0
            for row in clean["priorities"]:
                if row["statement"].casefold() in open_now:
                    already.append({"id": "priorities",
                                    "line": f"\"{row['statement']}\" is already a priority."})
                    continue
                tid = row["thread_id"]
                binding = ({"type": BIND_ACTIVITY, "entity_ids": [tid]} if tid
                           else {"type": BIND_SELF})
                objective_state.create_objective(root, statement=row["statement"], binding=binding,
                                                 name=objective_name(row["statement"]),
                                                 anchor_thread_id=tid, owner_person_id=_owner(root),
                                                 source_skill=SOURCE_SKILL)
                made += 1
            if not made:
                raise _Already()
        try:
            _prio()
            applied.append({"id": "priorities", "line": ITEMS["priorities"]["sets"]})
        except _Already:
            pass
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": "priorities", "line": f"priorities were not all saved: "
                                                      f"{str(exc)[:160] or type(exc).__name__}"})
    if "morning" in clean:
        try:
            import schedule_config as sc
            got = sc.set_morning_time(root, clean["morning"], source_skill=SOURCE_SKILL)
            if got.get("ok"):
                applied.append({"id": "morning", "line": got["line"]})
            else:
                failed.append({"id": "morning", "line": got.get("line") or "The time was not saved."})
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": "morning", "line": f"morning was not saved: "
                                                   f"{str(exc)[:160] or type(exc).__name__}"})
    if "avoid" in clean:
        def _avoid():
            import chat_persona
            from event_gate import append_event
            from skill_config_writer import save_skill_config
            before = chat_persona.get_chat_persona(root).get("never_line") or ""
            saved = dict(chat_persona._load_saved(root) or {})
            saved["never_line"] = clean["avoid"]
            save_skill_config(root, "chat_persona", saved, origin="asked")
            append_event(root / "_hq" / "data" / "events.jsonl", [chat_persona.build_style_changed_event(
                layer="chat_persona", origin="asked",
                changes=[{"knob": "never_line", "from": before, "to": clean["avoid"]}],
                source_skill=SOURCE_SKILL, evidence="onboarding confirm")], holder=SOURCE_SKILL)
        run("avoid", _avoid)
    if "close_caution" in clean:
        def _close():
            import connector_config as cc
            mode = CLOSE_CAUTION[clean["close_caution"]]
            for rec in cc.declared_connector_settings(root):
                cc.set_connector_settings(root, rec["category"], dials={"close_mode": mode},
                                          holder=SOURCE_SKILL)
        run("close_caution", _close)

    # The sheet last, with only what landed (or was already so): it is the
    # record, and it must never say more than the settings do.
    landed = {a["id"] for a in applied} | {a["id"] for a in already}
    for key in ("thread_word", "work_stream_word"):
        if key in clean:
            landed.add(key)
    sheet = {}
    for key, qid in _SHEET_ID.items():
        if key not in landed or key not in answers:
            continue
        raw = answers[key]
        if isinstance(raw, list):
            raw = "; ".join(r.get("statement", "") if isinstance(r, dict) else str(r) for r in raw)
        sheet[qid] = str(raw)
    if sheet:
        import advisor_sheet
        got = advisor_sheet.write_sheet(root, sheet, by=by, now_iso=now_iso)
        if got.get("ok"):
            for key in ("thread_word", "work_stream_word"):
                if key in clean:
                    applied.append({"id": key, "line": ITEMS[key]["sets"]})
        else:
            for key in ("thread_word", "work_stream_word"):
                if key in clean:
                    failed.append({"id": key, "line": got.get("line") or "The word was not saved."})
            failed.append({"id": "sheet", "line": got.get("line") or "The sheet was not saved."})
    return {"ok": not failed, "applied": applied, "already": already, "failed": failed}


class _Already(Exception):
    """Every priority given was already one: nothing to say as applied."""


__all__ = ["settings_present", "ITEMS", "VOLUNTEERED", "SIGN_OFFS", "CLOSE_CAUTION", "DRAFT_POSTURES", "plan_confirm", "apply_confirm",
           "objective_name"]
