#!/usr/bin/env python3
"""The nightly sweep proposes craft rules from method signal; the Hub's Review tab asks (SPEC CRAFT1 step 5, cr1#400).

WHY THIS EXISTS
---------------
Craft files were made only when the owner asked (`capture how [name] does
[scope]`, cr1#398). The owner asked for the product to notice method on its
own: a person explaining why they do something, correcting a draft, walking
through their own review, refusing something on principle. The nightly
session sweep already reads every chat and processed meeting of the day; the
model picks out that signal there. This module is the exact half: it turns
what the model found into proposals on the existing rails, and turns the
owner's yes into a craft file through the capture writer.

WHAT IT DOES
------------
  * `propose_craft_rules` takes the sweep's candidates. Each names a subject,
    a scope, the rule's words and the evidence it rests on.
      - A candidate that contradicts a confirmed rule goes to
        `craft_adjust.propose_amend` (owner ruling of 2026-11-12, option a):
        an amend proposal on the Review tab, the file unchanged.
      - Any other candidate becomes ONE `craft_rule` proposal, tier confirm
        and never auto (owner ruling 2026-10-24), with the rule text, its
        evidence and the file it would go in.
    Cite or drop: evidence the capture gather cannot find drops the
    candidate, so the writer that later saves it can check every pointer.
    The privacy refusal of the craft contract runs here too. A rule already
    in its file, one the owner dismissed in the last ninety days, and one
    already proposed (open or ever accepted) are not proposed again.
  * `confirm_rule` is the Review tab's yes: it gathers again, builds the
    capture preview for that one rule and saves it through
    `craft_capture.write_craft` (cr1#398), which appends `craft_captured`.
    No held-out test runs here, so a new file is saved as proposed and never
    tested, exactly as the capture writer keeps one.

ASKED ONLY ON THE REVIEW TAB
----------------------------
Each proposal carries the staff meeting's surface hint, so the daily cards
never pick it up. The Review tab shows at most `MAX_ON_REVIEW` craft
questions at a time (`cockpit_helpers.feed_review`); each can be skipped.

PUBLIC API
----------
  propose_craft_rules(workspace_root, candidates, *, now_iso=None,
                      entities=None) -> dict
  confirm_rule(workspace_root, proposal_id, *, resolved_by, edit=None,
               now_iso=None, source_skill="cockpit") -> dict
  KIND, DETECTOR, COOLDOWN_DAYS, MAX_ON_REVIEW

stdlib only. `propose_craft_rules` never raises on a bad candidate; each is
skipped with its reason.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import craft_contract as cc  # noqa: E402

KIND = "craft_rule"
DETECTOR = "craft-sweep"
#: A dismissed rule is not proposed again for this long (cr1#400).
COOLDOWN_DAYS = 90
#: At most this many craft questions on the Review tab at a time (owner
#: ruling 2026-10-24: five per card).
MAX_ON_REVIEW = 5
#: How long a craft proposal waits on the Review tab.
TTL_DAYS = 30
FINGERPRINT_PREFIX = "craft_rule:"
_ACTIONS = [{"action": "confirm proposal"}, {"action": "dismiss proposal"},
            {"action": "snooze proposal 7d"}]
_CITE_RE = re.compile(r"^(.+)#(R\d+)$")


def _plain(text) -> str:
    return " ".join(str(text or "").split())


def _norm(text) -> str:
    return _plain(text).lower().rstrip(" .")


def _now_iso(now_iso) -> str:
    if isinstance(now_iso, str) and now_iso:
        return now_iso
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fingerprint(target: str, text: str) -> str:
    digest = hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()[:16]
    return f"{FINGERPRINT_PREFIX}{target}:{digest}"


def _ledger(root: Path, now_iso: str) -> tuple:
    """(fingerprints on cooldown, fingerprints ever accepted)."""
    try:
        from proposal_ledger import active_cooldowns, load_rows
        cooling = active_cooldowns(root, DETECTOR, now_iso=now_iso,
                                   cooldown_days=COOLDOWN_DAYS)
        accepted = {r.get("fingerprint") for r in load_rows(root, DETECTOR)
                    if r.get("pass") == DETECTOR
                    and r.get("user_action") in ("applied", "edited")}
        return cooling, accepted
    except Exception:  # noqa: BLE001 - propose() still applies its own floor
        return set(), set()


def _hits(evidence: list) -> list:
    """Chat sessions (`session:<id>`) the gather cannot find on its own, as
    transcript hits it lists beside the meetings."""
    out = []
    for e in evidence:
        if e["ref"].startswith("session:"):
            out.append({"ref": e["ref"], "date": e.get("date"),
                        "title": "a chat"})
    return out


def _evidence(raw) -> list:
    out = []
    for e in raw if isinstance(raw, list) else [raw]:
        if isinstance(e, str) and _plain(e):
            out.append({"ref": _plain(e), "date": None})
        elif isinstance(e, dict) and _plain(e.get("ref")):
            out.append({"ref": _plain(e["ref"]), "date": e.get("date")})
    return out


def _gathered_refs(gathered: dict, evidence: list) -> list:
    """Each evidence pointer as the gather names it: the item's own ref, or
    the item whose source is that pointer. Pointers it does not hold drop."""
    items = [it for it in gathered.get("items") or [] if isinstance(it, dict)]
    by_ref = {it["ref"]: it for it in items if it.get("ref")}
    by_source = {}
    for it in items:
        read = it.get("read") if isinstance(it.get("read"), dict) else {}
        if read.get("source_ref"):
            by_source.setdefault(str(read["source_ref"]), it["ref"])
    out = []
    for e in evidence:
        ref = e["ref"] if e["ref"] in by_ref else by_source.get(e["ref"])
        if ref and ref not in out:
            out.append(ref)
    return out


def _known_evidence(root: Path, refs: list, known_sessions: set) -> list:
    """The amend path's cite-or-drop: a pointer stays only when it names
    something on record: a chat this sweep read, a meeting in the ledger, or
    a file in the workspace."""
    events = None
    keep = []
    for e in refs:
        ref = e["ref"]
        kind, _, ident = ref.partition(":")
        ok = False
        if kind == "session":
            ok = ident in known_sessions
        elif kind == "meeting":
            if events is None:
                import brain_proposals as bp
                events = bp._load_events(root)
            ok = any(str(ev.get("seq")) == ident and ev.get("type") == "meeting"
                     for ev in events)
        elif kind in ("document", "note") and ident:
            target = (root / ident).resolve()
            ok = target.is_file() and root.resolve() in target.parents
        if ok:
            keep.append(e)
    return keep


def _kept_recently(root: Path, rel: str, rule_id: str, text: str, now_iso: str) -> bool:
    """True when the owner kept this rule against these same words in the
    last COOLDOWN_DAYS (the amend side of the ninety days)."""
    import datetime as _dt
    import brain_proposals as bp
    from craft_adjust import EV_AMENDED
    try:
        now = _dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    for ev in bp._load_events(root):
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") != EV_AMENDED or d.get("outcome") != "kept" \
                or d.get("craft_path") != rel or d.get("rule") != rule_id \
                or _norm(d.get("new_text")) != _norm(text):
            continue
        try:
            at = _dt.datetime.fromisoformat(str(ev.get("ts")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=_dt.timezone.utc)
        if (now - at).days < COOLDOWN_DAYS:
            return True
    return False


def _skip(skipped: list, cand_no: int, reason: str) -> None:
    skipped.append({"candidate": cand_no, "reason": reason})


def propose_craft_rules(workspace_root, candidates, *, now_iso=None,
                        entities=None, known_sessions=None) -> dict:
    """The sweep's craft candidates onto the proposal rails.

    A candidate is {"subject_id", "scope", "text", "section" (Rules, Order of
    work or What they never do; default Rules), "evidence": [ref or {"ref",
    "date"}], "contradicts" (optional, "<craft file path>#R<n>")}. Evidence
    refs are the capture gather's pointers (`meeting:<seq>`,
    `document:<path>`, `note:<path>`, `fact:<seq>`) or a chat's
    `session:<id>`, which counts only when it is one of `known_sessions`
    (the chats this sweep read; none given, none count). Returns {"proposed", "amends", "skipped": [{"candidate",
    "reason"}], "proposal_ids"}. Writes `brain_proposal` rows only; a file is
    never written here."""
    import brain_proposals as bp
    import craft_adjust
    import craft_capture as cap

    root = Path(workspace_root)
    now_iso = _now_iso(now_iso)
    ents = entities if isinstance(entities, dict) else cc.load_entities(root)
    cooling, accepted = _ledger(root, now_iso)
    sessions = {str(x) for x in known_sessions or [] if x}
    out = {"proposed": 0, "amends": 0, "skipped": [], "proposal_ids": []}
    for n, raw in enumerate(candidates or [], start=1):
        try:
            _one(root, n, raw, out, ents=ents, now_iso=now_iso, cooling=cooling,
                 accepted=accepted, sessions=sessions)
        except Exception as exc:  # noqa: BLE001 - one candidate never stops the rest
            _skip(out["skipped"], n, "error: " + type(exc).__name__)
    return out


def _one(root: Path, n: int, raw, out: dict, *, ents, now_iso, cooling,
         accepted, sessions) -> None:
    """One candidate onto the rails, or skipped with its reason."""
    import brain_proposals as bp
    import craft_adjust
    import craft_capture as cap

    if not isinstance(raw, dict):
        return _skip(out["skipped"], n, "not a candidate")
    text = _plain(raw.get("text"))
    evidence = _evidence(raw.get("evidence"))
    # A chat counts only when this sweep read it (cite or drop).
    evidence = [e for e in evidence if not e["ref"].startswith("session:")
                or e["ref"].partition(":")[2] in sessions]
    if not text:
        return _skip(out["skipped"], n, "no rule text")
    if not evidence:
        return _skip(out["skipped"], n, "no evidence pointer")
    cite = _CITE_RE.match(_plain(raw.get("contradicts")))
    if cite:
        rel, rule_id = cite.group(1), cite.group(2)
        evidence = _known_evidence(root, evidence, sessions)
        if not evidence:
            return _skip(out["skipped"], n, "evidence not found")
        if _kept_recently(root, rel, rule_id, text, now_iso):
            return _skip(out["skipped"], n, "kept by the owner in the last ninety days")
        res = craft_adjust.propose_amend(root, rel, rule_id, text, evidence,
                                         now_iso=now_iso, source_skill=DETECTOR,
                                         entities=ents)
        if res.get("ok"):
            out["amends"] += 1
            out["proposal_ids"].append(res.get("proposal_id"))
            return None
        return _skip(out["skipped"], n, "amend: " + str(res.get("refused")))
    subject, scope = raw.get("subject_id"), raw.get("scope")
    hits = _hits(evidence)
    gathered = cap.gather_sources(root, subject, scope, transcript_hits=hits,
                                  entities=ents)
    if not gathered.get("ok"):
        return _skip(out["skipped"], n, str(gathered.get("reason") or "no file"))
    refs = _gathered_refs(gathered, evidence)
    if not refs:
        return _skip(out["skipped"], n, "evidence not found")
    section = raw.get("section") if raw.get("section") in         cc.EVIDENCE_SECTIONS else "Rules"
    if cap._findings(text, section, ents):
        return _skip(out["skipped"], n, "privacy")
    existing = gathered.get("existing") or {}
    if any(_norm(r.get("text")) == _norm(text)
           for r in existing.get("rules") or [] if isinstance(r, dict)):
        return _skip(out["skipped"], n, "already in the file")
    target = gathered["target"]
    fp = _fingerprint(target, text)
    if fp in accepted:
        return _skip(out["skipped"], n, "already accepted once")
    if fp in cooling:
        return _skip(out["skipped"], n, "dismissed in the last ninety days")
    name, label = gathered.get("subject_name"), gathered.get("scope_label")
    title = f"How {name} does {label}: a new rule"
    res = bp.propose(
        str(root), kind=KIND, fingerprint=fp,
        evidence="From " + "; ".join(refs), action_tuples=_ACTIONS,
        tier="confirm", ttl_days=TTL_DAYS, detector=DETECTOR,
        render_line=("Seen in the day's work. Yes adds it to the file "
                     f"{target}; Skip asks again in a week."),
        extra={"title": title, "surface_hint": "staff-meeting",
               "craft_path": target, "craft_subject": subject,
               "craft_scope": scope, "craft_section": section,
               "new_text": text, "craft_evidence": refs,
               "craft_hits": hits})
    if res.get("status") == "proposed":
        out["proposed"] += 1
        out["proposal_ids"].append(res["proposal_id"])
        return None
    return _skip(out["skipped"], n, str(res.get("status")))


def _proposal(root: Path, proposal_id: str) -> Optional[dict]:
    import brain_proposals as bp
    events = bp._load_events(root)
    if proposal_id not in {it["id"] for it in
                           bp._open_brain_proposals(events, now=None)}:
        return None
    for ev in events:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "brain_proposal" and \
                d.get("proposal_id") == proposal_id and d.get("kind") == KIND:
            return d
    return None


def confirm_rule(workspace_root, proposal_id, *, resolved_by, edit=None,
                 now_iso=None, source_skill="cockpit") -> dict:
    """The owner's yes to one craft proposal, read from the book (never from
    a page): the rule is saved through `craft_capture.write_craft`, the one
    writer of craft files, and the proposal is resolved. `edit` is the
    owner's own words for the rule, saved in place of the proposed ones.
    Returns {"ok", "status", "line", "rel"}; a refusal leaves the proposal
    open and says why in one line."""
    import brain_proposals as bp
    import craft_capture as cap

    root = Path(workspace_root)
    data = _proposal(root, str(proposal_id))
    if data is None:
        return {"ok": False, "status": "already_resolved",
                "line": "That one was already answered; nothing was written."}
    hits = [h for h in data.get("craft_hits") or [] if isinstance(h, dict)]
    gathered = cap.gather_sources(root, data.get("craft_subject"),
                                  data.get("craft_scope"),
                                  transcript_hits=hits)
    if not gathered.get("ok"):
        return {"ok": False, "status": "refused",
                "line": "Nothing was saved: " + str(gathered.get("line") or
                                                    "the sources are gone.")}
    preview = cap.build_preview(root, gathered, [{
        "text": data.get("new_text"), "section": data.get("craft_section"),
        "evidence": list(data.get("craft_evidence") or [])}])
    rules = preview.get("rules") or [] if preview.get("ok") else []
    if not rules:
        line = "Nothing was saved: the rule's evidence is no longer found."
        if preview.get("already_on_file"):
            line = "That rule is already in the file; nothing was written."
            bp.resolve_proposal(str(root), str(proposal_id), "superseded",
                                resolved_by=resolved_by,
                                source_skill=source_skill,
                                note="already in the file")
        return {"ok": False, "status": "refused", "line": line}
    words = _plain(edit)
    decision = {"edit": words} if words else "confirm"
    res = cap.write_craft(root, preview, {rules[0]["key"]: decision},
                          now_iso=now_iso, source_skill=source_skill)
    if not res.get("ok"):
        return {"ok": False, "status": "refused",
                "line": str(res.get("line") or "Nothing was saved.")}
    bp.resolve_proposal(str(root), str(proposal_id),
                        "edited" if words else "applied",
                        resolved_by=resolved_by, source_skill=source_skill)
    return {"ok": True, "status": "applied", "rel": res.get("rel"),
            "line": str(res.get("line") or "Saved.")}


__all__ = ["KIND", "DETECTOR", "COOLDOWN_DAYS", "MAX_ON_REVIEW",
           "propose_craft_rules", "confirm_rule"]
