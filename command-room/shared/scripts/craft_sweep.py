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

KINDS OF WORK THE SWEEP DISCOVERS (cr1#1391 part B)
----------------------------------------------------
  * A candidate that carries `new_kind` names a kind of work that recurs in
    the subject's own meetings, chats and documents (a recurring board pack,
    a monthly update): a name, a parent core scope, where it applies
    (readers and triggers) and at least `MIN_RECURRENCES` evidence pointers
    the capture gather finds. It becomes ONE always-ask `craft_kind`
    proposal, never auto; nothing is added to the scope list and no file is
    written until the owner says yes. A kind already on the list is an
    ordinary rule candidate instead. Mail is never read here: it joins a
    capture only when the owner names it (owner ruling 2026-10-24).
  * `confirm_kind` is the yes: the gather runs again with the declaration,
    and the capture writer saves the first rule, which adds the kind through
    `craft_contract.add_scope` with its reader declaration (no empty kind:
    add_scope refuses one without a cited rule). It then loads on top of its
    parent in the skills it names (`craft_recall`). No held-out test runs
    here, so the file is proposed and untested, used for the owner only.
  * A declined kind is not proposed again for `KIND_DECLINE_DAYS`, whatever
    the words; a kind the owner ever took back is never proposed again.

THE CAP (cr1#1391 part B)
-------------------------
  * `offer_retirements` counts each subject's active kinds (a craft file,
    proposed or confirmed, in a kind on the list). Above `KIND_CAP` it offers
    the least used kind the owner added (fewest of its rules cited by a
    draft, the older first) for retirement, as ONE always-ask
    `craft_retire` proposal. A core kind is never offered, nor one added in
    the last `RETIRE_GRACE_DAYS`, nor one kept in the last
    `RETIRE_COOLDOWN_DAYS`; one offer is open at a time. The count is per
    subject, but the kind list is the workspace's own, so a retirement
    takes the kind off for every subject (the offer says so). After a Keep
    it, the next night may offer the next least used kind.
  * `retire_kind` is the yes: the scope writer's own take-back row
    (`craft_contract.reverse_scope_added`). Every file stays where it is,
    and capture brings the kind back with a new rule. Nothing retires
    without the owner's yes.

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
  confirm_kind(workspace_root, proposal_id, *, resolved_by, edit=None,
               now_iso=None, source_skill="cockpit") -> dict
  active_kinds(workspace_root) -> dict
  offer_retirements(workspace_root, *, now_iso=None, entities=None) -> dict
  retire_kind(workspace_root, proposal_id, *, resolved_by,
              source_skill="cockpit") -> dict
  KIND, KIND_NEW, KIND_RETIRE, DETECTOR, DETECTOR_KIND, DETECTOR_CAP,
  COOLDOWN_DAYS, MAX_ON_REVIEW, MIN_RECURRENCES, KIND_CAP

stdlib only. `propose_craft_rules` never raises on a bad candidate; each is
skipped with its reason.
"""
from __future__ import annotations

import hashlib
import re
import string
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

#: cr1#1391 part B: a kind of work the sweep found recurring, asked first.
KIND_NEW = "craft_kind"
DETECTOR_KIND = "craft-kind"
KIND_FINGERPRINT_PREFIX = "craft_kind:"
#: How many distinct pointers the gather finds make a kind recur (decided
#: reversibly under the owner's word of 2026-11-16).
MIN_RECURRENCES = 3
#: A declined kind is not proposed again for this long, whatever its words.
KIND_DECLINE_DAYS = 365
#: The cap on active kinds per subject (about a dozen, cr1#1391), and the
#: always-ask offer to retire the least used added kind above it.
KIND_CAP = 12
KIND_RETIRE = "craft_retire"
DETECTOR_CAP = "craft-cap"
RETIRE_FINGERPRINT_PREFIX = "craft_retire:"
#: A kind added this recently is never offered for retirement.
RETIRE_GRACE_DAYS = 30
#: A kind the owner kept is not offered again for this long.
RETIRE_COOLDOWN_DAYS = 90
_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}
#: The privacy findings that name someone or something on file.
_NAME_FINDINGS = ("tracked org or project name", "third-party name")
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
    out = {"proposed": 0, "amends": 0, "kinds": 0, "skipped": [],
           "proposal_ids": []}
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
    if "new_kind" in raw:
        nk = raw.get("new_kind") if isinstance(raw.get("new_kind"), dict) else {}
        try:
            slug = cc.canonical_scope(cc.scope_slug(nk.get("label")))
        except ValueError:
            slug = None
        if slug is None or slug not in {s["slug"] for s in cc.known_scopes(root)}:
            return _one_kind(root, n, raw, out, ents=ents, now_iso=now_iso,
                             sessions=sessions)
        # A kind already on the list: an ordinary rule in it.
        raw = dict({k: v for k, v in raw.items() if k != "new_kind"},
                   scope=slug)
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
    scope = cc.canonical_scope(scope)  # sell is win business (cr1#1391)
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


def _kind_fingerprint(slug: str) -> str:
    return f"{KIND_FINGERPRINT_PREFIX}{slug}"


def _ever_taken_back(root: Path, slug: str, events=None) -> bool:
    """True when the ledger holds a take-back of this kind: the owner's undo
    of an added kind, or a retirement said yes to."""
    import brain_proposals as bp
    for ev in events if events is not None else bp._load_events(root):
        if isinstance(ev, dict) and ev.get("type") == cc.SCOPE_REMOVED_EVENT \
                and (ev.get("data") or {}).get("slug") == slug:
            return True
    return False


def _kind_declared(nk: dict) -> dict:
    """The kind's declaration, checked by the contract: a parent from the
    core is required (LookupError when it is missing), and the readers
    default to where the parent applies. ValueError on a bad declaration."""
    parent = nk.get("parent")
    if parent not in cc.CORE_SLUGS:
        raise LookupError("no parent")
    readers = nk.get("readers") or list(cc.CORE_DECLARATIONS[parent]["readers"])
    decl = cc.check_declaration(nk.get("label"), readers,
                                nk.get("triggers") or None, parent)
    return {"slug": decl["slug"], "label": decl["label"],
            "readers": list(decl["readers"]),
            "triggers": list(decl["triggers"]), "parent": decl["parent"]}


def _one_kind(root: Path, n: int, raw: dict, out: dict, *, ents, now_iso,
              sessions) -> None:
    """One new kind of work onto the rails, or skipped with its reason
    (cr1#1391 part B). Writes one `brain_proposal` row at most; the scope
    list and the files are untouched until the owner's yes."""
    import brain_proposals as bp
    import craft_capture as cap
    from proposal_ledger import active_cooldowns

    nk = raw.get("new_kind") if isinstance(raw.get("new_kind"), dict) else {}
    try:
        slug = cc.scope_slug(nk.get("label"))
    except ValueError as exc:
        return _skip(out["skipped"], n, f"declaration: {exc}")
    if slug in cc.RENAMED_SCOPES or slug in cc.CORE_SLUGS:
        return _skip(out["skipped"], n,
                     "already a kind of work, or an old name for one")
    try:
        decl = _kind_declared(nk)
    except LookupError:
        return _skip(out["skipped"], n, "no parent in the core eight")
    except ValueError as exc:
        return _skip(out["skipped"], n, f"declaration: {exc}")
    text = _plain(raw.get("text"))
    if not text:
        return _skip(out["skipped"], n, "no rule text")
    if _ever_taken_back(root, slug):
        return _skip(out["skipped"], n, "taken back before")
    fp = _kind_fingerprint(slug)
    if fp in active_cooldowns(root, DETECTOR_KIND, now_iso=now_iso,
                              cooldown_days=KIND_DECLINE_DAYS):
        return _skip(out["skipped"], n, "declined")
    # A chat counts only when this sweep read it (cite or drop).
    evidence = [e for e in _evidence(raw.get("evidence"))
                if not e["ref"].startswith("session:")
                or e["ref"].partition(":")[2] in sessions]
    subject = raw.get("subject_id")
    hits = _hits(evidence)
    new_scope = {k: decl[k] for k in ("label", "readers", "triggers", "parent")}
    gathered = cap.gather_sources(root, subject, slug, transcript_hits=hits,
                                  entities=ents, new_scope=new_scope)
    if not gathered.get("ok"):
        return _skip(out["skipped"], n,
                     str(gathered.get("reason") or "no file"))
    refs = _gathered_refs(gathered, evidence)
    if len(refs) < MIN_RECURRENCES:
        return _skip(out["skipped"], n, "seen fewer than "
                     f"{_WORDS.get(MIN_RECURRENCES, MIN_RECURRENCES)} times")
    section = raw.get("section") if raw.get("section") in \
        cc.EVIDENCE_SECTIONS else "Rules"
    # The kind's own name and triggers become a file name and a ledger row
    # on the yes, so they pass the same refusal as the rule. They are
    # lower case by contract, so each is also checked with capitals, for
    # names on file only (a capitalised plain phrase is not a stray name).
    words = [decl["label"], *decl["triggers"]]
    if cap._findings(text, section, ents) or any(
            cap._findings(w, "Rules", ents)
            or any(f.get("kind") in _NAME_FINDINGS for f in
                   cap._findings(string.capwords(w), "Rules", ents))
            for w in words):
        return _skip(out["skipped"], n, "privacy")
    target = gathered["target"]
    parent = cc.scope_label(decl["parent"])
    res = bp.propose(
        str(root), kind=KIND_NEW, fingerprint=fp,
        evidence="Seen in " + "; ".join(refs), action_tuples=_ACTIONS,
        tier="confirm", ttl_days=TTL_DAYS, detector=DETECTOR_KIND,
        render_line=(f"Seen {len(refs)} times in their work. It sits under "
                     f"{parent} and loads on top of it in "
                     f"{', '.join(decl['readers'])}. Yes adds it with this "
                     f"first rule to {target}; Skip asks again in a week."),
        extra={"title": f"A new kind of work for "
                        f"{gathered.get('subject_name')}: {decl['label']}",
               "surface_hint": "staff-meeting",
               "craft_path": target, "craft_subject": subject,
               "craft_scope": slug, "craft_section": section,
               "new_text": text, "craft_evidence": refs,
               "craft_hits": hits, "craft_new_kind": new_scope})
    if res.get("status") == "proposed":
        out["kinds"] += 1
        out["proposal_ids"].append(res["proposal_id"])
        return None
    return _skip(out["skipped"], n, str(res.get("status")))


def _proposal(root: Path, proposal_id: str, kind: str = KIND) -> Optional[dict]:
    import brain_proposals as bp
    events = bp._load_events(root)
    if proposal_id not in {it["id"] for it in
                           bp._open_brain_proposals(events, now=None)}:
        return None
    for ev in events:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if ev.get("type") == "brain_proposal" and \
                d.get("proposal_id") == proposal_id and d.get("kind") == kind:
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
    return _confirm(Path(workspace_root), str(proposal_id), KIND,
                    resolved_by=resolved_by, edit=edit, now_iso=now_iso,
                    source_skill=source_skill)


def confirm_kind(workspace_root, proposal_id, *, resolved_by, edit=None,
                 now_iso=None, source_skill="cockpit") -> dict:
    """The owner's yes to a new kind of work the sweep found (cr1#1391 part
    B), read from the book. The gather runs again with the kind's
    declaration and the capture writer saves its first rule, which adds the
    kind through `craft_contract.add_scope` with its readers, triggers and
    parent. A kind the owner added meanwhile takes the rule as it is. `edit`
    is the owner's own words for the rule. Returns {"ok", "status", "line",
    "rel"}; a refusal leaves the proposal open and adds nothing."""
    return _confirm(Path(workspace_root), str(proposal_id), KIND_NEW,
                    resolved_by=resolved_by, edit=edit, now_iso=now_iso,
                    source_skill=source_skill)


def _confirm(root: Path, proposal_id: str, kind: str, *, resolved_by, edit,
             now_iso, source_skill) -> dict:
    """The shared yes of a new rule and a new kind: gather again, preview
    the one rule, save it through the capture writer, resolve the row."""
    import brain_proposals as bp
    import craft_capture as cap

    data = _proposal(root, proposal_id, kind)
    if data is None:
        return {"ok": False, "status": "already_resolved",
                "line": "That one was already answered; nothing was written."}
    hits = [h for h in data.get("craft_hits") or [] if isinstance(h, dict)]
    new_scope = None
    if kind == KIND_NEW and data.get("craft_scope") not in \
            {s["slug"] for s in cc.known_scopes(root)}:
        nk = data.get("craft_new_kind") if isinstance(
            data.get("craft_new_kind"), dict) else {}
        new_scope = {k: nk.get(k) for k in ("label", "readers", "triggers",
                                            "parent")}
    gathered = cap.gather_sources(root, data.get("craft_subject"),
                                  data.get("craft_scope"),
                                  transcript_hits=hits, new_scope=new_scope)
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


# ---------------------------------------------------------------------------
# The cap: the least used kind offered for retirement, asked first
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def active_kinds(workspace_root) -> dict:
    """{subject id: {kind slug: [craft file paths]}}: the kinds of work each
    subject has a proposed or confirmed craft file in, counting only kinds on
    the list (a file filed under a renamed kind counts as its new name). An
    evergreen file counts too; it is still a kind in use. READ, never
    raises."""
    root = Path(workspace_root)
    live = {s["slug"] for s in cc.known_scopes(root)}
    out: dict = {}
    try:
        files = cc.find_craft_files(root)
    except Exception:  # noqa: BLE001 - a read never raises
        return out
    for path in files:
        text = _read_text(path)
        if text is None:
            continue
        head = cc.parse_craft(text)["header"]
        scope = cc.canonical_scope(head.get("scope"))
        subject = head.get("subject")
        if head.get("status") not in ("proposed", "confirmed") \
                or scope not in live or not isinstance(subject, str):
            continue
        rel = path.relative_to(root).as_posix()
        out.setdefault(subject, {}).setdefault(scope, []).append(rel)
    return out


def _parse_iso(value):
    import datetime as _dt
    try:
        at = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=_dt.timezone.utc)


def _uses(events: list) -> dict:
    """{craft file path: the number of times a draft cited one of its rules}
    (the `craft_cites` a drafted row carries, `craft_adjust.CITES_KEY`)."""
    from craft_adjust import CITES_KEY
    out: dict = {}
    for ev in events:
        d = ev.get("data") if isinstance(ev, dict) and \
            isinstance(ev.get("data"), dict) else {}
        for cite in d.get(CITES_KEY) or []:
            if isinstance(cite, str) and "#" in cite:
                rel = cite.rpartition("#")[0]
                out[rel] = out.get(rel, 0) + 1
    return out


def offer_retirements(workspace_root, *, now_iso=None, entities=None) -> dict:
    """The nightly cap (cr1#1391 part B). For each subject with more than
    `KIND_CAP` active kinds, the least used kind the owner added among them
    (fewest cited rules across every file in that kind, the older first) is
    offered for retirement as ONE always-ask `craft_retire` proposal on the
    Review tab. Never a core kind, never one added in the last
    `RETIRE_GRACE_DAYS`, never one the owner kept in the last
    `RETIRE_COOLDOWN_DAYS`; while one offer is open, none is added.
    Writes `brain_proposal` rows only; nothing is retired here. Returns
    {"offered", "over": [{"subject_id", "kinds"}], "proposal_ids"}. Never
    raises."""
    import datetime as _dt
    import brain_proposals as bp
    from event_seq import coerce_seq
    from proposal_ledger import active_cooldowns

    root = Path(workspace_root)
    now_iso = _now_iso(now_iso)
    out = {"offered": 0, "over": [], "proposal_ids": []}
    try:
        kinds = active_kinds(root)
        over = {sid: k for sid, k in kinds.items() if len(k) > KIND_CAP}
        out["over"] = [{"subject_id": sid, "kinds": len(k)}
                       for sid, k in sorted(over.items())]
        if not over:
            return out
        events = bp._load_events(root)
        now = _parse_iso(now_iso)
        # an offer past its time is off the tab already, so it never blocks
        if any(p.get("kind") == KIND_RETIRE
               for p in bp._open_brain_proposals(events, now=now)):
            return out
        cooling = active_cooldowns(root, DETECTOR_CAP, now_iso=now_iso,
                                   cooldown_days=RETIRE_COOLDOWN_DAYS)
        added = {r["slug"]: r for r in cc._read_owner_scopes(root)}
        by_seq = {ev.get("seq"): ev for ev in events if isinstance(ev, dict)}
        uses = _uses(events)
        ents = entities if isinstance(entities, dict) else \
            cc.load_entities(root)
        every = {}
        for subj in kinds.values():
            for slug, rels in subj.items():
                every.setdefault(slug, []).extend(rels)
        for sid, subj in sorted(over.items()):
            pool = []
            for slug in subj:
                row = added.get(slug)
                if row is None or slug in cc.CORE_SLUGS:
                    continue
                at = _parse_iso((by_seq.get(row.get("seq")) or {}).get("ts"))
                if at is None or now is None or \
                        (now - at) < _dt.timedelta(days=RETIRE_GRACE_DAYS):
                    continue
                if f"{RETIRE_FINGERPRINT_PREFIX}{slug}" in cooling:
                    continue
                used = sum(uses.get(rel, 0) for rel in every.get(slug, []))
                seq = coerce_seq(row.get("seq"), context="craft_sweep")
                pool.append((used, seq if seq is not None else -1, slug, row))
            if not pool:
                continue
            used, _seq, slug, row = min(pool)
            name = cc.entity_label(ents, sid)
            readers = ", ".join(row.get("readers") or ()) or "no skill"
            n_files = len(every.get(slug, []))
            res = bp.propose(
                str(root), kind=KIND_RETIRE,
                fingerprint=f"{RETIRE_FINGERPRINT_PREFIX}{slug}",
                evidence=(f"{name} has {len(subj)} kinds of work, over the "
                          f"cap of {KIND_CAP}. {row['label']} was cited "
                          f"{used} time{'' if used == 1 else 's'}, the "
                          "fewest."),
                action_tuples=_ACTIONS, tier="confirm", ttl_days=TTL_DAYS,
                detector=DETECTOR_CAP,
                render_line=(f"Yes retires {row['label']}: it stops loading "
                             f"in {readers}, for everyone. Every file in it "
                             f"stays where it is ({n_files} now), and "
                             "capturing a new rule in it brings it back. "
                             "Keep it leaves it as it is."),
                extra={"title": f"Retire a kind of work: {row['label']}",
                       "surface_hint": "staff-meeting",
                       "craft_subject": sid, "craft_scope": slug,
                       "craft_scope_seq": row.get("seq")})
            if res.get("status") == "proposed":
                out["offered"] += 1
                out["proposal_ids"].append(res["proposal_id"])
                break  # one offer open at a time
    except Exception as exc:  # noqa: BLE001 - the nightly sweep never fails here
        out["error"] = type(exc).__name__
    return out


def retire_kind(workspace_root, proposal_id, *, resolved_by,
                source_skill="cockpit") -> dict:
    """The owner's yes to a retirement offer, read from the book: the kind
    comes off the list through the scope writer's own take-back row
    (`craft_contract.reverse_scope_added`); every craft file stays where it
    is. A kind already taken back resolves the row and writes nothing else.
    Returns {"ok", "status", "line"}."""
    import brain_proposals as bp

    root = Path(workspace_root)
    data = _proposal(root, str(proposal_id), KIND_RETIRE)
    if data is None:
        return {"ok": False, "status": "already_resolved",
                "line": "That one was already answered; nothing was written."}
    slug = data.get("craft_scope")
    live = {r["slug"]: r for r in cc._read_owner_scopes(root)}
    row = live.get(slug)
    if row is None:
        bp.resolve_proposal(str(root), str(proposal_id), "superseded",
                            resolved_by=resolved_by, source_skill=source_skill,
                            note="already off the list")
        return {"ok": True, "status": "applied",
                "line": "That kind is already off the list; nothing else "
                        "was written."}
    if data.get("craft_scope_seq") is not None and             row.get("seq") != data.get("craft_scope_seq"):
        # added again since the offer: that is not the one the owner saw
        bp.resolve_proposal(str(root), str(proposal_id), "superseded",
                            resolved_by=resolved_by, source_skill=source_skill,
                            note="added again since the offer")
        return {"ok": True, "status": "applied",
                "line": "That kind was added again since the offer, so "
                        "nothing was retired."}
    try:
        res = cc.reverse_scope_added(root, row["seq"], undone_by=resolved_by,
                                     source_skill=source_skill)
    except ValueError:
        return {"ok": False, "status": "refused",
                "line": "Nothing was retired."}
    if res.get("status") not in ("removed", "already_removed"):
        return {"ok": False, "status": "refused",
                "line": "Nothing was retired."}
    bp.resolve_proposal(str(root), str(proposal_id), "applied",
                        resolved_by=resolved_by, source_skill=source_skill)
    return {"ok": True, "status": "applied",
            "line": f"Retired {row['label']}. Its files stay where they are; "
                    "capture a new rule in it to bring it back."}


__all__ = ["KIND", "KIND_NEW", "KIND_RETIRE", "DETECTOR", "DETECTOR_KIND",
           "DETECTOR_CAP", "COOLDOWN_DAYS", "MAX_ON_REVIEW",
           "MIN_RECURRENCES", "KIND_CAP", "KIND_DECLINE_DAYS",
           "propose_craft_rules", "confirm_rule", "confirm_kind",
           "active_kinds", "offer_retirements", "retire_kind"]
