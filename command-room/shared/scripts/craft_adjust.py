#!/usr/bin/env python3
"""Craft files that adjust over time: amend by diff, moving confidence, the stale list and the learning leg (SPEC CRAFT1 step 6, cr1#401).

WHY THIS EXISTS
---------------
A craft file says how a person works. People change, and a method note that
stays after they changed misleads every later draft. Steps 1 to 4 made the
files, read them and wrote them on the owner's word. This module keeps them
honest afterwards, without ever rewriting one behind the owner's back.

WHAT IT DOES
------------
  * AMEND BY DIFF. New material that contradicts a confirmed rule becomes an
    `craft_amend` proposal on the existing proposal rails, shown on the Hub's
    Review tab with the old and the new text side by side. The file is not
    touched until the owner answers. Yes writes the new text in place and
    keeps the old text in the file's History, marked superseded. No keeps the
    rule, which is the owner confirming it.
  * CONFIDENCE MOVES (`craft_contract`'s steps). The owner's confirmation
    raises a rule; a passed held-out test raises the file (in
    `craft_contract.file_confidence`, which capture writes with); a
    contradiction lowers the rule and carries an open question; the third
    contradiction marks it superseded pending the owner. While the owner has
    not answered, those moves are read from the ledger (`rule_state`), never
    written into the file: the file is unchanged until the owner acts.
  * THE STALE LIST. `review_list` names craft files older than the staleness
    limit whose subject was active this week, and files never tested; a file
    marked `<!-- evergreen -->` is exempt. It is a list on the Review tab,
    one line each with the phrase that refreshes it, never a question.
  * THE LEARNING LEG. `run_craft_leg`, a leg of the weekly `learning` job,
    attaches each correction the owner made to a draft that cited a craft
    rule to that rule, as evidence the owner weighs. It moves no confidence:
    a correction to a draft says the draft was wrong, not which rule was.

NOTHING IS ASKED ELSEWHERE
--------------------------
An amend proposal carries the staff meeting's surface hint, so the daily
cards never pick it up; the Review tab (and the manual capture skill) is
where it is answered.

PUBLIC API
----------
  propose_amend(workspace_root, rel, rule_id, new_text, evidence, *,
                now_iso=None, source_skill=DETECTOR, entities=None) -> dict
  answer_amend(workspace_root, proposal_id, *, accept, resolved_by,
               now_iso=None, source_skill="cockpit") -> dict
  rule_state(workspace_root, rel, rule_id, *, events=None) -> dict
  review_list(workspace_root, *, now_iso=None, entities=None) -> dict
  run_craft_leg(workspace_root, *, apply, now_iso, batch_id=None) -> dict
  CITES_KEY  the key a drafted email's row carries its cited rule ids under

stdlib only. Read paths never raise.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import craft_contract as cc  # noqa: E402

KIND_AMEND = "craft_amend"
DETECTOR = "craft-adjust"
WRITER = "craft-adjust"
EV_CONTRADICTED = "craft_rule_contradicted"
EV_AMENDED = "craft_amended"
EV_ATTACHED = "craft_evidence_attached"
#: The key an `email_drafted` row carries the craft rules its draft cited
#: under (`voice_corrections.record_draft_click(extra=...)`).
CITES_KEY = "craft_cites"
#: A craft file not updated for this many days is stale (the limit the
#: always-loaded documents' staleness check uses).
STALE_DAYS = 60
#: "Active this week".
ACTIVE_DAYS = 7
#: How long an amend proposal waits on the Review tab.
AMEND_TTL_DAYS = 30
#: At most this many lines in each part of the Review tab's craft list.
LIST_CAP = 20
FINGERPRINT_PREFIX = "craft_amend:"
_ACTIONS = [{"action": "confirm proposal"}, {"action": "dismiss proposal"},
            {"action": "snooze proposal 7d"}]
_CONF_IN_LINE = re.compile(r"\bConfidence\s+-?[0-9]+(?:\.[0-9]+)?")
_CONFIRMED_IN_LINE = re.compile(r"\bConfirmed by owner\s+\d{4}-\d{2}-\d{2}")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _plain(text) -> str:
    return " ".join(str(text or "").split())


def _norm(text) -> str:
    return _plain(text).lower().rstrip(" .")


def _today(now_iso) -> str:
    if isinstance(now_iso, str) and len(now_iso) >= 10:
        try:
            return _dt.date.fromisoformat(now_iso[:10]).isoformat()
        except ValueError:
            pass
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _date(value) -> Optional[_dt.date]:
    if isinstance(value, str) and len(value) >= 10:
        try:
            return _dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _events(root: Path) -> list:
    try:
        import brain_proposals as bp
        return bp._load_events(root)
    except Exception:  # noqa: BLE001 - a read never raises
        return []


def _events_file(root: Path) -> Path:
    try:
        from events_io import active_path
        return Path(active_path(root))
    except Exception:  # noqa: BLE001 - a stripped install still answers
        return root / cc.HQ_DIR / "data" / "events.jsonl"


def _append(root: Path, rows: list) -> list:
    from event_gate import append_event
    return append_event(_events_file(root), rows, holder="craft_adjust")


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve(root: Path, rel) -> Optional[Path]:
    """The craft file `rel` names, only when discovery finds it there."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    want = (root / rel).resolve()
    for path in cc.find_craft_files(root):
        if path.resolve() == want:
            return path
    return None


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _rule(parsed: dict, rule_id) -> Optional[dict]:
    return next((it for it in parsed.get("rules") or []
                 if it.get("id") == rule_id), None)


def _confirmed(rule: dict) -> bool:
    return "Confirmed by owner" in (rule.get("evidence_text") or "")


def _refs(evidence) -> list:
    """Evidence as [{"ref", "date"}]: strings or dicts with a ref."""
    out = []
    for e in evidence if isinstance(evidence, list) else [evidence]:
        if isinstance(e, str) and _plain(e):
            out.append({"ref": _plain(e), "date": None})
        elif isinstance(e, dict) and _plain(e.get("ref")):
            out.append({"ref": _plain(e["ref"]),
                        "date": e.get("date") if _date(e.get("date")) else None})
    return out


def _ptr(refs: list) -> str:
    return "; ".join(r["ref"] + (f", {r['date']}" if r.get("date") else "")
                     for r in refs)


def _title(ents: dict, root: Path, header: dict) -> str:
    owner = cc.owner_person_id(ents)
    scope = cc.scope_label(header.get("scope"), root)
    if owner and header.get("subject") == owner:
        return f"How you {scope}"
    return f"How {cc.entity_label(ents, header.get('subject'))} does {scope}"


def _say(ents: dict, root: Path, header: dict) -> str:
    owner = cc.owner_person_id(ents)
    scope = cc.scope_label(header.get("scope"), root)
    if owner and header.get("subject") == owner:
        return f"capture how I {scope}"
    return (f"capture how {cc.entity_label(ents, header.get('subject'))} "
            f"does {scope}")


def _question(rule_id: str, new_text: str) -> str:
    return f"Does {rule_id} still hold, or is it now this: {_plain(new_text)}"


# ---------------------------------------------------------------------------
# Confidence read from the ledger
# ---------------------------------------------------------------------------

def rule_state(workspace_root, rel, rule_id, *, events=None) -> dict:
    """Where a rule stands between the owner's answers: the contradictions
    since the owner last answered for it, the confidence they bring it to,
    whether it is superseded pending the owner, and the open question the
    latest one raised. Read only; the file is not touched."""
    root = Path(workspace_root)
    events = events if isinstance(events, list) else _events(root)
    reset = -1
    for ev in events:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == EV_AMENDED and d.get("craft_path") == rel \
                and d.get("rule") == rule_id and isinstance(ev.get("seq"), int):
            reset = max(reset, ev["seq"])
    hits = [ev for ev in events
            if ev.get("type") == EV_CONTRADICTED
            and isinstance(ev.get("data"), dict)
            and ev["data"].get("craft_path") == rel
            and ev["data"].get("rule") == rule_id
            and isinstance(ev.get("seq"), int) and ev["seq"] > reset]
    stored = None
    path = _resolve(root, rel)
    text = _read(path) if path else None
    if text is not None:
        rule = _rule(cc.parse_craft(text), rule_id)
        stored = rule.get("confidence") if rule else None
    n = len(hits)
    conf = cc.move_confidence(stored, -cc.CONTRADICTION_STEP * n) \
        if stored is not None else None
    if n == 0:
        conf = stored
    latest = hits[-1]["data"] if hits else {}
    return {"contradictions": n, "stored": stored, "confidence": conf,
            "pending_supersede": n >= cc.SUPERSEDE_AFTER,
            "open_question": latest.get("question"),
            "latest_seq": hits[-1]["seq"] if hits else None}


# ---------------------------------------------------------------------------
# Amend by diff
# ---------------------------------------------------------------------------

def _refuse(reason: str, line: str) -> dict:
    return {"ok": False, "refused": reason, "line": line}


def propose_amend(workspace_root, rel, rule_id, new_text, evidence, *,
                  now_iso=None, source_skill=DETECTOR, entities=None) -> dict:
    """New material contradicts confirmed rule `rule_id` of the craft file at
    `rel`: record the contradiction and put ONE amend proposal on the Review
    tab with the old and the new text. The file is not written.

    Cite or drop: no evidence pointer, nothing recorded. The new text passes
    the craft privacy refusal (figures, third-party names, tracked org and
    project names, owner ruling on cr1#396) or nothing is recorded. A second
    contradiction of the same rule replaces the open proposal with one that
    carries the newest text and the count, so the tab shows one row per rule.

    Returns {"ok", "proposal_id", "contradictions", "confidence",
    "pending_supersede", "line"} or {"ok": False, "refused", "line"}."""
    import brain_proposals as bp
    import craft_capture

    root = Path(workspace_root)
    ents = entities if isinstance(entities, dict) else cc.load_entities(root)
    path = _resolve(root, rel)
    if path is None:
        return _refuse("not_a_craft_file",
                       "That is not a craft file in this workspace, so "
                       "nothing was recorded.")
    rel = _rel(root, path)
    text = _read(path)
    check = cc.validate_craft(text or "", workspace_root=root, entities=ents,
                              path=path)
    if text is None or check["errors"]:
        return _refuse("unreadable", "That craft file does not read as one, "
                       "so nothing was recorded.")
    header = check["header"]
    if header.get("status") == "superseded":
        return _refuse("superseded", "That file is superseded, so it is not "
                       "amended.")
    rule = _rule(cc.parse_craft(text), rule_id)
    if rule is None:
        return _refuse("no_rule", f"The file has no rule {rule_id}.")
    if not _confirmed(rule):
        return _refuse("not_confirmed",
                       f"{rule_id} is not confirmed yet, so new material is "
                       "weighed when it is captured, not here.")
    new = _plain(new_text)
    if not new:
        return _refuse("no_text", "There is no new text, so nothing was "
                       "recorded.")
    if _norm(new) == _norm(rule.get("body")):
        return _refuse("same_text", f"That is what {rule_id} already says.")
    refs = _refs(evidence)
    if not refs:
        return _refuse("no_evidence", "New material needs a pointer to where "
                       "it came from, so nothing was recorded.")
    found = craft_capture._findings(new, rule["section"], ents)
    if found:
        kinds = ", ".join(sorted({f["kind"] for f in found}))
        return _refuse("privacy", f"The new text carries a {kinds}, which a "
                       "craft file never holds, so nothing was recorded.")

    today = _today(now_iso)
    # cr1#400 review: the same material on the same rule counts once, so a
    # sweep that reads the same window again never lowers the rule twice.
    seen = {r["ref"] for r in refs}
    for ev in _events(root):
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == EV_CONTRADICTED and d.get("craft_path") == rel \
                and d.get("rule") == rule_id and _norm(d.get("new_text")) == _norm(new) \
                and seen == {e.get("ref") for e in d.get("evidence") or [] if isinstance(e, dict)}:
            return _refuse("already_recorded", "That material is already on "
                           "record against this rule; nothing was added.")
    question = _question(rule_id, new)
    written = _append(root, [{
        "type": EV_CONTRADICTED, "source_skill": source_skill,
        "data": {"craft_path": rel, "rule": rule_id,
                 "subject_id": header.get("subject"),
                 "scope": header.get("scope"),
                 "old_text": _plain(rule.get("body")), "new_text": new,
                 "evidence": refs, "date": today, "question": question},
    }])
    seq = written[0].get("seq") if written else None
    if seq is None:
        return _refuse("not_recorded", "The contradiction could not be "
                       "recorded, so nothing was added on the Hub, in Work, under Commitments, To confirm.")
    state = rule_state(root, rel, rule_id)
    n = state["contradictions"]

    stem = f"{FINGERPRINT_PREFIX}{rel}#{rule_id}:"
    for item in bp._open_brain_proposals(_events(root), now=None):
        if str(item.get("fingerprint") or "").startswith(stem):
            bp.resolve_proposal(str(root), item["id"], "superseded",
                                resolved_by=source_skill,
                                source_skill=source_skill,
                                note="newer material on the same rule")
    title = _title(ents, root, header) + f": {rule_id}"
    pending = (" This is the third time or more, so the rule is superseded "
               "pending your answer." if state["pending_supersede"] else "")
    line = (f"New material says otherwise ({n} time"
            f"{'' if n == 1 else 's'}). Yes puts the new text in; No keeps "
            f"{rule_id} as it is." + pending)
    res = bp.propose(
        str(root), kind=KIND_AMEND, fingerprint=f"{stem}{seq}",
        evidence="From " + _ptr(refs), action_tuples=_ACTIONS,
        tier="confirm", ttl_days=AMEND_TTL_DAYS, detector=DETECTOR,
        render_line=line,
        extra={"title": title, "surface_hint": "staff-meeting",
               "craft_path": rel, "craft_rule": rule_id,
               "old_text": _plain(rule.get("body")), "new_text": new,
               "craft_question": question, "craft_contradictions": n,
               "craft_confidence": state["confidence"],
               "craft_pending": bool(state["pending_supersede"]),
               "craft_evidence": refs, "contradiction_seq": seq})
    if res.get("status") != "proposed":
        return {"ok": False, "refused": res.get("status"),
                "line": "The contradiction is recorded; a question for it "
                        "is already on the Hub, in Work, under Commitments, To confirm.",
                "contradictions": n}
    return {"ok": True, "proposal_id": res["proposal_id"],
            "contradictions": n, "confidence": state["confidence"],
            "pending_supersede": state["pending_supersede"],
            "line": f"{title} is on the Hub, in Work, under Commitments, To confirm, with the old and the new "
                    "text side by side. The file is unchanged until you "
                    "answer."}


def _proposal(root: Path, proposal_id: str) -> Optional[dict]:
    """The open amend proposal's own event data, read from the book."""
    import brain_proposals as bp
    events = _events(root)
    open_ids = {it["id"] for it in bp._open_brain_proposals(events, now=None)}
    if proposal_id not in open_ids:
        return None
    for ev in events:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "brain_proposal" and \
                d.get("proposal_id") == proposal_id and \
                d.get("kind") == KIND_AMEND:
            return d
    return None


def _header_lines(header: dict, confidence, today) -> tuple:
    one = (f"<!-- craft: subject={header.get('subject')} "
           f"scope={header.get('scope')} status={header.get('status')} "
           f"confidence={_fmt(confidence)}"
           + (f" consent={header['consent']}" if header.get("consent")
              else "") + " -->")
    tested = header.get("tested")
    two = f"<!-- last-updated: {today}  last-writer: {WRITER}  tested: "
    two += (f"{tested} verdict={header.get('verdict')}" if tested
            else "never") + " -->"
    return one, two


def _fmt(value) -> str:
    return ("%.2f" % float(value)).rstrip("0").rstrip(".") or "0"


def _rewrite(text: str, rule_id: str, header: dict, today: str, *,
             replace=None, touch=None, history: str) -> Optional[str]:
    """The file with rule `rule_id`'s item lines replaced (`replace`, a list
    of lines) or edited (`touch`, a function over its lines), one History
    entry added and the header refreshed. None when the rule is not found."""
    import craft_capture as cap

    pre, sections = cap._split(text)
    hit = False
    for sec in sections:
        if sec[0] not in ("Rules", "What they never do"):
            continue
        lead, items = cap._items_of(sec[2])
        for i, lines in enumerate(items):
            m = cap._ITEM_ID_RE.match(lines[0])
            if m and f"R{m.group(1)}" == rule_id:
                items[i] = replace if replace is not None else touch(lines)
                hit = True
        body = list(lead)
        for lines in items:
            lines = list(lines)
            while len(lines) > 1 and not lines[-1].strip():
                lines.pop()
            body += lines
        sec[2] = body
    if not hit:
        return None
    for sec in sections:
        if sec[0] == "History":
            body = list(sec[2])
            while body and not body[-1].strip():
                body.pop()
            sec[2] = body + [f"- {history}"]
    out = cap._join(pre, sections)
    one, two = _header_lines(header, cc.file_confidence(
        out, header.get("verdict")), today)
    return cap._set_header(out, one, two)


def answer_amend(workspace_root, proposal_id, *, accept: bool, resolved_by,
                 now_iso=None, source_skill="cockpit") -> dict:
    """The owner's answer to one amend proposal, read from the book (never
    from a page). Yes: the new text replaces the rule in place, confirmed by
    the owner today, and the old text is kept in History as superseded. No:
    the rule stays, and the owner's word raises its confidence. Either way
    the file is gated and lands through the same door capture writes with,
    ONE `craft_amended` event resets the rule's count, and the proposal is
    resolved. A rule whose text changed since the proposal is not written
    over; the proposal is let go with that said."""
    import brain_proposals as bp
    import craft_capture as cap

    root = Path(workspace_root)
    data = _proposal(root, str(proposal_id))
    if data is None:
        return {"ok": False, "status": "already_resolved",
                "line": "That one was already answered; nothing was written."}
    rel, rule_id = data.get("craft_path"), data.get("craft_rule")
    # A retry after the file landed but before the proposal was resolved
    # finishes the resolve and never writes the file a second time.
    for ev in _events(root):
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == EV_AMENDED and                 d.get("proposal_id") == str(proposal_id):
            bp.resolve_proposal(str(root), str(proposal_id),
                                "applied" if d.get("outcome") == "applied"
                                else "declined", resolved_by=resolved_by,
                                source_skill=source_skill)
            return {"ok": True, "status": d.get("outcome"), "rel": rel,
                    "rule": rule_id, "confidence": d.get("confidence"),
                    "line": "That answer was already written to the file."}
    path = _resolve(root, rel)
    text = _read(path) if path else None
    ents = cc.load_entities(root)
    check = cc.validate_craft(text or "", workspace_root=root, entities=ents,
                              path=path)
    rule = _rule(cc.parse_craft(text or ""), rule_id)
    if text is None or check["errors"] or rule is None or \
            _norm(rule.get("body")) != _norm(data.get("old_text")):
        bp.resolve_proposal(str(root), str(proposal_id), "superseded",
                            resolved_by=resolved_by, source_skill=source_skill,
                            note="the rule changed since the proposal")
        return {"ok": False, "status": "refused",
                "line": f"{rule_id} changed since this was proposed, so "
                        "nothing was written over it."}
    header = check["header"]
    today = _today(now_iso)
    state = rule_state(root, rel, rule_id)
    n = state["contradictions"]
    refs = _refs(data.get("craft_evidence") or [])
    if accept:
        conf = cc.move_confidence(cap.START_CONFIDENCE_SAID, cc.CONFIRM_STEP)
        new_rule = {"text": _plain(data.get("new_text")),
                    "section": rule["section"], "evidence": refs,
                    "confidence": conf}
        lines = cap._item_lines(new_rule, rule_id, today)
        history = (f"{today} {rule_id} amended on the Hub, in Work, under Commitments, To confirm, after "
                   f"{n} contradiction(s), from {_ptr(refs)}; its superseded "
                   f"text: {_plain(rule.get('body'))}")
        out = _rewrite(text, rule_id, header, today, replace=lines,
                       history=history)
        outcome, action = "applied", "applied"
    else:
        conf = cc.move_confidence(rule.get("confidence"), cc.CONFIRM_STEP)

        def touch(lines):
            done = []
            for ln in lines:
                ln = _CONF_IN_LINE.sub(f"Confidence {_fmt(conf)}", ln)
                ln = _CONFIRMED_IN_LINE.sub(f"Confirmed by owner {today}", ln)
                done.append(ln)
            return done
        history = (f"{today} {rule_id} kept by the owner on the Hub, in Work, under Commitments, To confirm, "
                   f"against new material ({n} contradiction(s)); "
                   f"confidence now {_fmt(conf)}")
        out = _rewrite(text, rule_id, header, today, touch=touch,
                       history=history)
        outcome, action = "kept", "declined"
    if out is None:
        return {"ok": False, "status": "refused",
                "line": f"{rule_id} could not be found in the file, so "
                        "nothing was written."}
    try:
        cc.gate_craft_text(out, workspace_root=root, entities=ents, path=path)
    except cc.CraftRefused:
        return {"ok": False, "status": "refused",
                "line": "Nothing was written: the file would not pass the "
                        "craft check."}
    landed = cap._land(root, rel, out, path.stat().st_mtime_ns)
    if not landed.get("ok"):
        return {"ok": False, "status": "error",
                "line": landed.get("line") or "The file could not be saved, "
                        "so nothing changed."}
    _append(root, [{
        "type": EV_AMENDED, "source_skill": source_skill,
        "data": {"craft_path": rel, "rule": rule_id, "outcome": outcome,
                 "proposal_id": str(proposal_id), "contradictions": n,
                 "old_text": _plain(rule.get("body")),
                 "new_text": _plain(data.get("new_text")),
                 "confidence": conf, "date": today,
                 "resolved_by": resolved_by},
    }])
    bp.resolve_proposal(str(root), str(proposal_id), action,
                        resolved_by=resolved_by, source_skill=source_skill)
    try:
        import render_craft_view
        render_craft_view.regenerate(root)
    except Exception:  # noqa: BLE001 - the view is rebuilt weekly too
        pass
    line = (f"{rule_id} now reads the new way; the old text is kept in the "
            "file's History." if accept else
            f"{rule_id} stays as it was, and your answer raised its "
            "confidence.")
    return {"ok": True, "status": outcome, "rel": rel, "rule": rule_id,
            "confidence": conf, "line": line}


# ---------------------------------------------------------------------------
# The Review tab's craft list
# ---------------------------------------------------------------------------

def _active_subjects(events: list, ents: dict, since: _dt.date) -> set:
    """Every record an event since `since` names, with the org and the key
    contact of each project named."""
    threads = {t.get("id"): t for t in ents.get("entities", {}).get(
        "threads", []) if isinstance(t, dict)}
    out: set = set()
    for ev in events:
        day = _date(ev.get("ts"))
        if day is None or day < since:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ids = [ev.get("primary_thread_id"), ev.get("org_id"),
               ev.get("person_id"), d.get("person_id"), d.get("org_id"),
               d.get("thread_id")]
        ids += list(ev.get("related_thread_ids") or [])
        ids += list(ev.get("person_ids") or [])
        for i in ids:
            if not isinstance(i, str) or not i:
                continue
            out.add(i)
            t = threads.get(i)
            if t:
                out.update(x for x in (t.get("org_id"),
                                       t.get("key_contact_id")) if x)
    return out


def review_list(workspace_root, *, now_iso=None, entities=None) -> dict:
    """READ. The craft part of the Review tab: stale files on subjects active
    this week, files never tested, and corrections the learning run attached
    to a rule. One line each, with the phrase that acts on it; nothing here
    is a question. A superseded or evergreen file is never listed.
    Returns {"stale": [...], "untested": [...], "evidence": [...]}."""
    root = Path(workspace_root)
    out = {"stale": [], "untested": [], "evidence": []}
    try:
        ents = entities if isinstance(entities, dict) else \
            cc.load_entities(root)
        today = _date(_today(now_iso))
        events = _events(root)
        active = _active_subjects(events, ents,
                                  today - _dt.timedelta(days=ACTIVE_DAYS))
        always = ({cc.owner_person_id(ents)} | cc.own_company_org_ids(ents)) \
            - {None}
        files = {}
        for path in cc.find_craft_files(root):
            text = _read(path)
            if text is None or cc.is_evergreen(text):
                continue
            parsed = cc.parse_craft(text)
            header = parsed["header"]
            if header.get("status") not in ("proposed", "confirmed"):
                continue
            rel = _rel(root, path)
            files[rel] = (header, parsed)
            title = _title(ents, root, header)
            say = _say(ents, root, header)
            updated = _date(header.get("last_updated"))
            subject = header.get("subject")
            if updated and (today - updated).days > STALE_DAYS and \
                    (subject in active or subject in always):
                out["stale"].append({
                    "path": rel, "title": title, "say": say,
                    "last_updated": updated.isoformat(),
                    "line": f"{title}: last updated "
                            f"{(today - updated).days} days ago, and they "
                            f"were active this week. Say `{say}` to "
                            "refresh it."})
            if not header.get("tested"):
                out["untested"].append({
                    "path": rel, "title": title, "say": say,
                    "line": f"{title}: never tested on a held-out piece of "
                            f"work. Say `{say}` to test it."})
        seen = set()
        for ev in reversed(events):
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if ev.get("type") != EV_ATTACHED or d.get("craft_path") not in files:
                continue
            key = (d.get("craft_path"), d.get("rule"))
            if key in seen:
                continue
            seen.add(key)
            header, parsed = files[d["craft_path"]]
            if _rule(parsed, d.get("rule")) is None:
                continue
            n = sum(1 for e in events if e.get("type") == EV_ATTACHED
                    and (e.get("data") or {}).get("craft_path") == d["craft_path"]
                    and (e.get("data") or {}).get("rule") == d.get("rule"))
            title = _title(ents, root, header)
            out["evidence"].append({
                "path": d["craft_path"], "rule": d.get("rule"), "title": title,
                "count": n,
                "line": f"{title}, {d.get('rule')}: you corrected {n} "
                        f"draft{'' if n == 1 else 's'} that used it. The "
                        "corrections are attached to the rule as evidence."})
        for k in out:
            out[k] = out[k][:LIST_CAP]
    except Exception:  # noqa: BLE001 - a read never raises; the tab says less
        return {"stale": [], "untested": [], "evidence": [],
                "errors": ["craft"]}
    return out


# ---------------------------------------------------------------------------
# The learning leg
# ---------------------------------------------------------------------------

def _cited(root: Path, events: list) -> dict:
    """{draft key: [cite ids]} from drafted emails that carried their cited
    craft rules, keyed the way a correction names its draft."""
    try:
        import voice_corrections as vc
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for ev in events:
        if ev.get("type") != "email_drafted":
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        cites = [c for c in d.get(CITES_KEY) or [] if isinstance(c, str)]
        if not cites or ev.get("seq") is None:
            continue
        try:
            key = vc._first_of_draft(root, ev["seq"])[0]
        except Exception:  # noqa: BLE001
            key = ev["seq"]
        out.setdefault(key, [])
        out[key] += [c for c in cites if c not in out[key]]
    return out


def run_craft_leg(workspace_root, *, apply: bool, now_iso: str,
                  batch_id=None) -> dict:
    """The `learning` job's craft leg: each correction logged against a draft
    that cited a craft rule is attached to that rule, as ONE
    `craft_evidence_attached` event per correction and rule, never twice. A
    rule no longer in its file, or a file no longer on disk, gets nothing.
    Confidence does not move here. `apply=False` counts and writes nothing.
    Returns {"attached", "applied": 0, "lines": []}."""
    import voice_corrections as vc

    root = Path(workspace_root)
    events = _events(root)
    cited = _cited(root, events)
    if not cited:
        return {"attached": 0, "applied": 0, "lines": []}
    done = {((ev.get("data") or {}).get("craft_path"),
             (ev.get("data") or {}).get("rule"),
             (ev.get("data") or {}).get("correction"))
            for ev in events if ev.get("type") == EV_ATTACHED}
    rules: dict = {}
    rows = []
    for row in vc.load_corrections(root):
        key = row.get("draft_ref")
        if key is None or key not in cited:
            continue
        corr = vc._fingerprint(row.get("skill") or "",
                               row.get("original_draft", ""),
                               row.get("corrected_by_user", ""))
        corr = f"{corr}:{key}"
        for cid in cited[key]:
            rel, _sep, rule_id = cid.rpartition("#")
            if (rel, rule_id, corr) in done:
                continue
            if rel not in rules:
                path = _resolve(root, rel)
                text = _read(path) if path else None
                rules[rel] = {r["id"] for r in
                              cc.parse_craft(text)["rules"]} if text else set()
            if rule_id not in rules[rel]:
                continue
            done.add((rel, rule_id, corr))
            rows.append({
                "type": EV_ATTACHED, "source_skill": "learning",
                "data": {"craft_path": rel, "rule": rule_id, "correction": corr,
                         "draft_ref": key, "skill": row.get("skill"),
                         "correction_type": row.get("correction_type"),
                         "corrected_at": row.get("timestamp"),
                         "reading": "unsettled", "batch_id": batch_id,
                         "date": _today(now_iso)}})
    if apply and rows:
        _append(root, rows)
    return {"attached": len(rows), "applied": 0, "lines": []}


__all__ = [
    "KIND_AMEND", "DETECTOR", "EV_CONTRADICTED", "EV_AMENDED", "EV_ATTACHED",
    "CITES_KEY", "STALE_DAYS", "ACTIVE_DAYS",
    "propose_amend", "answer_amend", "rule_state", "review_list",
    "run_craft_leg",
]
