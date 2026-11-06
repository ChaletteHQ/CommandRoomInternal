#!/usr/bin/env python3
"""One-time person-proposal backlog sweep (T2.2 — FS-17 + FS-11b-extended).

WHY THIS EXISTS
The live staff-meeting queue carried ~68 legacy person proposals — the
identity backlog M has twice called "safe to batch-skip", plus a minority
with real context worth keeping. M's FS-11b ruling extended: auto-confirm far
more people (with observed emails), and sweep the aged low-context rest —
one narrated, undoable pass instead of 68 dropdown clicks.

WHAT IT DOES (per open person-family proposal, oldest first). SPEC PID1
replaced this module's classifier: `plan_sweep` now DELEGATES to
`identity_reconcile.plan_reconcile` (one rule table, two entry shells) —
the add bar is the R1 corroboration doctrine, no longer "name +
prose-inferred role/org":

  AUTO-TIER CLUSTER (full name AND observed email / ≥2 independent source
  families with an identifier on one of them (cr1#754) AND zero same-name
  collision AND not already on file)
      → `people_writer.auto_add_person` — the same-name dedup gate runs
        BEFORE every add (a token match → needs_confirm, left open and
        reported, never auto-forked); an email is captured ONLY when an
        address literally appears in the cluster's own evidence/source_ref
        text AND that text ties it to this person (`_attributed_email`,
        cr1#769; an OBSERVED source, carried with provenance). A
        pattern-guessed or constructed address is NEVER written (F-08 extends
        to capture). Then a person_added tombstone retires EVERY member
        proposal.

  LOW-CONTEXT + AGED (name-only single mention, older than
  brain_proposals.PERSON_LOW_CONTEXT_STALE_DAYS)
      → expire: the not_relevant tombstone with an expiry note. Nothing else
        written.

  EVERYTHING ELSE (confirm clusters; on-file merge-propose clusters;
  no-name rows; update-type rows) → LEFT OPEN, reported with the tier
  named. The full link/annotation machinery is identity_reconcile's own
  entry point (`run_identity_reconcile` / `--backfill`).

UNDO — every write is reversible through the registered brain_undo reversers
(archive-never-delete): person adds reverse via person_org_creation_
structured_fact (status → archived), tombstones via person_proposal_tombstone
(person_proposal_reopened). The sweep stamps `brain_batch_id` on every write
marker so ONE `brain_undo.undo_batch({"kind": "brain_batch", "batch_id": ...})`
reverses the whole pass.

SAFETY
  - DRY-RUN IS THE DEFAULT. `--apply` performs the writes. The orchestrator
    session runs --apply against the live workspace only with M's go — this
    module never self-schedules and registers no job.
  - Narrated summary on stdout either way; a `person_backlog_swept` audit
    event records the batch (counts + batch id) on --apply.

stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import unicodedata
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# cr1#769 - reading who the text puts beside an address. Words are compared
# folded, so case, accents and script do not decide anything.
_WORD_RE = re.compile(r"[^\W\d_][\w'\u2019.\-]*")
_BRACKET_OPEN_RE = re.compile(r"[\"'\u2019\])]?[ \t]*[(<\[]\s*(?:mailto:)?$")
_BRACKET_LABEL_RE = re.compile(r"[(\[]\s*([^()<>\[\]]{1,40}?)\s*[,:]\s*$")
_BRACKET_AFTER_RE = re.compile(
    r"^[>)\]]?\s*[(<\[]\s*([^()<>\[\]]{1,60})[)>\]]")
# "Name: a@b", "Name - a@b", "Name at a@b", "Name's email is a@b". A comma is
# left out on purpose: in a list it separates two people.
_JOIN_RE = re.compile(r"(?:['\u2019]s)?\s+(?:e-?mail|address)\s*(?:is\b|:)?\s*$"
                      r"|\s+(?:is|at)\b\s*$|\s*[:\-\u2013\u2014]\s*$",
                      re.IGNORECASE)
_CLAUSE_END_RE = re.compile(r"^\s*(?:$|[.;\n])")
# A name ties only when it opens its clause. Anything else before it ("EA to",
# "on behalf of", "for", "cc", "Bo Stone /", a verb) may make the address
# somebody else's, and no list of such words would ever be complete.
_OPENS_CLAUSE_RE = re.compile(
    r"^[\s>]*(?:(?:from|to|sender|reply-to|attendee|invitee|organi[sz]er|owner"
    r"|assignee|host|guest)\s*:\s*)?[\"'\u2019\[(]?\s*"
    r"(?:(?:dr|mr|mrs|ms|mx|prof|sir)\.?\s+)?$", re.IGNORECASE)
# "On Tue, Nov 4, 2026 at 9:00 AM, Name <a@b> wrote:" Every part after the
# first holds a digit, so a name in that list does not pass.
_REPLY_HEADER_RE = re.compile(r"^[\s>]*On\s+[^,]*(?:,\s*[^,]*\d[^,]*)*,\s*$")
_CALENDAR_ATTENDEE_RE = re.compile(rf"calendar:\s*({_EMAIL_RE.pattern})")
_NEARBY = 120  # characters read either side of an address
_FOLD_TABLE = str.maketrans({"\u00f8": "o", "\u0142": "l", "\u0111": "d",
                             "\u00e6": "ae", "\u0153": "oe", "\u00f0": "d",
                             "\u00fe": "th", "\u0131": "i"})
_HONORIFICS = frozenset({"dr", "mr", "mrs", "ms", "mx", "prof", "sir"})
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "phd", "esq", "cpa"})
# Words written after a name that say what somebody does. A person's name with
# one of these beside an address may be about their office, not them.
_TITLES = frozenset({
    "ceo", "cfo", "coo", "cto", "cio", "cmo", "cro", "vp", "svp", "evp", "md",
    "gm", "president", "founder", "cofounder", "partner", "principal",
    "director", "manager", "head", "lead", "counsel", "chair", "secretary",
    "treasurer"})
# All that may sit in the bracket with an address and leave it the person's own.
_HARMLESS_IN_BRACKET = frozenset({
    "email", "mail", "mailto", "ceo", "cfo", "coo", "cto", "cio", "cmo", "cro",
    "vp", "svp", "evp", "president", "founder", "cofounder"})
# Words that label an address or carry a sentence. None of them names anybody.
_LABELS = frozenset({
    "from", "to", "cc", "bcc", "reply", "replyto", "sender", "recipient",
    "email", "mail", "mailto", "address", "contact", "via", "re", "subject",
    "fw", "fwd", "forwarded", "sent", "date", "attendee", "attendees",
    "invitee", "invitees", "organizer", "organiser", "owner", "assignee",
    "assigned", "requester", "reporter", "host", "guest", "participant", "at",
    "by", "for", "and", "or", "with", "in", "on", "of", "the", "a", "an", "as",
    "is", "was", "will", "has", "had", "wrote", "said", "asked", "emailed",
    "joined", "owns", "reach", "reachable", "her", "him", "them", "his",
    "their", "she", "he", "they", "it"})


def _clock_now(workspace_root=None):
    """CLOCK1 - the corroborated UTC instant this module stamps from.

    Swaps the CLOCK SOURCE only: every window, cutoff, threshold and output
    format around it is unchanged. A machine clock that has not synced used to
    write its own wrong reading straight into the permanent record; this reads
    the same clock, cross-checked against the newest timestamp the workspace
    already holds. Falls back to the raw machine clock if the helper is
    unavailable, so a stamp can never fail for want of corroboration.

    `workspace_root` is threaded in wherever the calling function already
    has one, because a helper that has to GUESS which workspace it is in
    guesses wrong exactly when it matters: a fire's early phases run in
    their own subprocesses, before anything has registered a root.
    """
    try:
        from trusted_now import trusted_now_utc

        return trusted_now_utc(workspace_root)
    except Exception:
        import datetime as _clock_dt

        return _clock_dt.datetime.now(_clock_dt.timezone.utc)


def _now_iso() -> str:
    return _clock_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _observed_email(p: dict) -> str | None:
    """An address the proposal's own captured text (evidence / source_ref)
    carries, for the readers that ASK of it: does this mention conflict with
    a record, does it point at one. Never derived, never guessed.

    cr1#769 - THIS IS NOT ATTRIBUTION, and nothing may write what it returns
    onto a person or treat it as licence to add one: `_attributed_email` is
    that reader. Its sole-address clause (a) names whoever's address the text
    happens to hold, which is the right thing to ask about and the wrong
    thing to believe. The link lane, the conflict gates, the annotation rail
    and `contact_capture` all ask, so they stay on this one.

    Review F-3 — quoted-thread evidence can carry the SENDER's or a third
    party's address, and "first regex hit" would attribute it to the wrong
    person. Accept an address only when:
      (a) it is the ONLY address in the captured text, or
      (b) its local part token-matches the person's name (quinn.alvarez@ →
          "Quinn Alvarez")."""
    text = f"{p.get('evidence') or ''} {p.get('source_ref') or ''}"
    hits = list(dict.fromkeys(m.group(0) for m in _EMAIL_RE.finditer(text)))
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    name_tokens = {t for t in re.split(r"[^a-z0-9]+", (p.get("name") or "").lower())
                   if len(t) >= 3}
    for h in hits:
        local_tokens = {t for t in re.split(r"[^a-z0-9]+", h.split("@", 1)[0].lower())
                        if t}
        if name_tokens & local_tokens:
            return h
    return None


def _fold(word: str) -> str:
    """A word reduced for comparing: casefolded, accents and punctuation gone,
    any script kept. A possessive goes too."""
    word = re.sub(r"['\u2019]s$", "", word.strip(".,;:'\"\u2019"))
    word = unicodedata.normalize("NFKD", word.casefold().translate(_FOLD_TABLE))
    return "".join(c for c in word if c.isalpha())


def _name_words(name) -> list:
    """The name folded, without a title of address or a suffix."""
    tokens = [t for t in str(name or "").split() if _fold(t)]
    while len(tokens) > 1 and _fold(tokens[0]) in _HONORIFICS:
        tokens.pop(0)
    while len(tokens) > 1 and _fold(tokens[-1]) in _SUFFIXES:
        tokens.pop()
    return [_fold(t) for t in tokens]


def _is_initial(word: str) -> bool:
    return len(word) == 1 and word.isascii()


def _local_part_spells(address: str, words: list) -> str | None:
    """How an address's local part spells the name: "whole" (every word of
    it, nothing more), "short" (cut down: an initial, or a middle name left
    out), "first" (the first name and nothing else) or None. A surname alone
    is not a spelling: a family shares it."""
    flat = _fold(address.split("@", 1)[0].split("+", 1)[0])
    names = [w for w in words if not _is_initial(w)] or words
    first, last = names[0], names[-1]
    if len(names) >= 2:
        if flat in {"".join(names), "".join(reversed(names))}:
            return "whole"
        if flat in {first + last, last + first, first[0] + last,
                    last + first[0], first + last[0]}:
            return "short"
    return "first" if flat == first else None


def _name_span(seen: list, words: list) -> int:
    """How many trailing words of `seen` are the proposed name, 0 for none. A
    middle initial on either side does not matter, and a header may put the
    surname first."""
    want = [w for w in words if not _is_initial(w)]
    got: list = []
    span = 0
    for word in reversed(seen):
        span += 1
        if not _is_initial(word):
            got.insert(0, word)
        if len(got) == len(want):
            break
    if got == want or (len(want) == 2 and got == want[::-1]):
        return span
    return 0


def _who_is_named(head: str, words: list, *, joined: bool = False,
                  after: bool = False) -> str | None:
    """Who the words at the end of `head` are: "own" (the proposed person),
    "part" (a bare piece of their name), "other" (somebody else) or None.

    It is "own" only when the name opens its clause and nothing after it says
    the address is somebody around them: a possessive, a role word or a
    qualifier gives None. Any word can be a name inside a bracket, in any case
    and any script, so an unknown word counts as a person there. Across a join
    word, or in the bracket after an address, prose is too likely, so the word
    must be capitalised."""
    line = head.rsplit("\n", 1)[-1]
    found = list(_WORD_RE.finditer(line))[-6:]
    seen = [_fold(m.group(0)) for m in found]
    plain = True
    while seen:
        if seen[-1] in _SUFFIXES or not seen[-1]:
            seen.pop()
        elif seen[-1] in _TITLES:
            seen.pop()
            plain = False
        elif len(seen) >= 2 and seen[-2] in ("of", "via"):
            del seen[-2:]
            plain = False
        else:
            break
    if not seen:
        return None
    span = _name_span(seen, words)
    if span:
        pre = line[:found[len(seen) - span].start()]
        possessive = found[len(seen) - 1].group(0).rstrip(".,;:").lower() \
            .endswith(("'s", "\u2019s"))
        opens = _OPENS_CLAUSE_RE.match(pre) or _REPLY_HEADER_RE.match(pre)
        return "own" if plain and opens and not possessive else None
    if seen[-1] in _LABELS:
        return None
    if joined or after:
        if not found[len(seen) - 1].group(0)[:1].isupper():
            return None
        if joined and len(seen) == 1:
            return None  # one capitalised word opening a line is a label
    if seen[-1] == words[0] or seen == words[-1:] or \
            (len(words) == 1 and words[0] in seen):
        return "part"
    return "other"


def _beside_address(text: str, start: int, end: int,
                    words: list) -> str | None:
    """Who the text puts beside the address at text[start:end]: "own" (the
    proposed person, in a bracket), "joined" (the proposed person, by a join
    word, with the address ending the clause), "part", "other" or None.
    Both sides are read, and somebody else on either side wins."""
    before = text[max(0, start - _NEARBY):start]
    after = text[end:end + _NEARBY]
    found = set()
    opening = _BRACKET_OPEN_RE.search(before)
    label = None if opening else _BRACKET_LABEL_RE.search(before)
    join = None if opening or label else _JOIN_RE.search(before)
    if opening:
        found.add(_who_is_named(before[:opening.start()], words))
    elif label:
        inner = {_fold(w) for w in _WORD_RE.findall(label.group(1))}
        found.add(_who_is_named(before[:label.start()], words)
                  if inner <= _HARMLESS_IN_BRACKET else "other")
    elif join:
        who = _who_is_named(before[:join.start()], words, joined=True)
        if who == "own":
            who = "joined" if _CLAUSE_END_RE.search(after) else None
        found.add(who)
    closing = _BRACKET_AFTER_RE.search(after)
    if closing:
        found.add(_who_is_named(closing.group(1), words, after=True))
    for kind in ("other", "own", "joined", "part"):
        if kind in found:
            return kind
    return None


def _address_sightings(p: dict, words: list) -> dict:
    """{lowercased address: {"address", "beside"}} across the proposal's
    evidence and source_ref. The two fields are read apart, so the end of one
    can never sit beside the start of the other."""
    out: dict = {}
    for field in ("evidence", "source_ref"):
        text = str(p.get(field) or "")
        for m in _EMAIL_RE.finditer(text):
            row = out.setdefault(m.group(0).lower(),
                                 {"address": m.group(0), "beside": set()})
            row["beside"].add(_beside_address(text, m.start(), m.end(), words))
    return out


def _text_words(text: str) -> list:
    """(as written, folded) for every word of the text outside its addresses."""
    return [(w, _fold(w)) for w in _WORD_RE.findall(_EMAIL_RE.sub(" ", text))]


def _name_is_shared(text: str, words: list) -> bool:
    """True when the text may show someone sharing a piece of the proposed
    name: the first name followed by any word that is not the rest of the
    name or plain prose, or the surname after any such word. A particle ("van",
    "de") or a lowercase surname counts like any other word."""
    tokens = [word for _raw, word in _text_words(text)]
    for word, following in zip(tokens, tokens[1:]):
        if word == words[0] and following not in words \
                and following not in _LABELS:
            return True
        if len(words) >= 2 and following == words[-1] and word not in words \
                and not _is_initial(word) and word not in _LABELS \
                and word not in _HONORIFICS:
            return True
    return False


def _writes_the_name(text: str, words: list, beside: set) -> bool:
    """A first-name address alone only says "someone called this". It counts
    when the text writes this person's first name with a capital, which also
    means the text is cased, so another person in it would show. A bare first
    name beside the address while the rest of the name sits apart is two
    mentions, not one."""
    tokens = _text_words(text)
    if not any(word == words[0] and raw[:1].isupper() for raw, word in tokens):
        return False
    return not ("part" in beside and
                any(word in words[1:] for _raw, word in tokens))


def _attributed_email(p: dict) -> str | None:
    """cr1#769 - the address the text TIES to this person, or None. The only
    reader whose answer may land on a person or license adding one. Being the
    only address in the text is NOT a tie.

    Two or more personal addresses in the text tie nothing. Role addresses do
    not count and never tie. A single address beside somebody else's name
    never ties. Otherwise it ties when:
      (a) the whole name opens its clause and it sits in a bracket beside that
          name ("Name (a@b)", "Name <a@b>", "Name (CEO, a@b)", "a@b (Name)",
          "[Name](mailto:a@b)"), or
      (b) a join word follows that name and the address ends the clause
          ("Name: a@b", "Name at a@b", "Name's email is a@b"), or
      (c) its local part spells every word of the name (quinn.alvarez@), or
      (d) its local part is a short spelling (qalvarez@, quinna@) and the text
          shows nobody sharing a piece of the name, or
      (e) its local part is the first name, nobody shares the name, and the
          text writes the name.

    `calendar:<address>` is the exception to (a)-(e). That ref is
    machine-written for the attendee, not prose, so it ties unless the text
    puts somebody else beside it.

    Attribution uncertainty is the no-address case: the person stays a
    question, or is added without one on another rule, and the owner's answer
    settles it. An address stored on the wrong person resolves that other
    person's mail to them forever, so every doubt lands on the question."""
    from identity_reconcile import is_role_address

    words = _name_words(p.get("name"))
    if not words:
        return None
    personal = [row for row in _address_sightings(p, words).values()
                if not is_role_address(row["address"])]
    if len(personal) != 1:
        return None
    addr, beside = personal[0]["address"], personal[0]["beside"]
    if "other" in beside:
        return None
    attendee = _CALENDAR_ATTENDEE_RE.fullmatch(
        str(p.get("source_ref") or "").strip())
    if attendee and attendee.group(1).lower() == addr.lower():
        return addr
    spells = _local_part_spells(addr, words)
    if "own" in beside or "joined" in beside or spells == "whole":
        return addr
    evidence = str(p.get("evidence") or "")
    if spells and not _name_is_shared(evidence, words) and \
            (spells == "short" or _writes_the_name(evidence, words, beside)):
        return addr
    return None


def _age_days(ts: str, now_iso: str) -> int | None:
    from event_time import parse_ts

    a, b = parse_ts(ts), parse_ts(now_iso)
    if a is None or b is None:
        return None
    return (b - a).days


def plan_sweep(workspace_root, *, now_iso: str | None = None) -> dict:
    """Pure planning pass (no writes): classify every open person proposal.
    Returns {add: [...], expire: [...], keep_open: [...]} where each entry
    carries the proposal row + the decision rationale.

    SPEC PID1 D7 — classification DELEGATES to `identity_reconcile.
    plan_reconcile` (one rule table, two entry shells; a forked classifier
    is the FS-19 pre-history all over again). The bar therefore changed
    from the old "name + prose-inferred role/org" to the R1 corroboration
    doctrine: only AUTO-tier clusters (full name AND observed email / ≥2
    source families AND zero collision AND not on file) plan as adds.
    Confirm clusters, on-file (merge-propose) clusters, no-name rows, and
    update-type rows all route to keep_open with the tier named — the full
    link/annotation machinery is `identity_reconcile`'s own entry point
    (`run_identity_reconcile` / `--backfill`); this shell keeps its
    original narrow add+expire shape for back-compat."""
    from identity_reconcile import plan_reconcile

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    rp = plan_reconcile(ws, now_iso=now_iso)

    def _rep_row(cluster: dict) -> dict:
        # Oldest add row, carrying the cluster's BEST name spelling — the
        # row auto_add_person / narration reads.
        rows = sorted(cluster["add_rows"],
                      key=lambda r: r.get("captured_ts") or "") \
            if cluster.get("add_rows") else list(cluster.get("rows") or [])
        rep = dict(rows[0]) if rows else {}
        rep["name"] = cluster.get("name")
        return rep

    add: list[dict] = []
    expire: list[dict] = []
    keep: list[dict] = []
    for entry in rp["auto"]:
        add.append({"proposal": _rep_row(entry["cluster"]),
                    "cluster": entry["cluster"],
                    "email": entry["email"],
                    "why": entry["why"]})
    for entry in rp["expire"]:
        expire.append({"proposal": entry["proposal"], "why": entry["why"]})
    for entry in rp["confirm"]:
        keep.append({"proposal": _rep_row(entry["cluster"]),
                     "why": entry["why"]})
    for entry in rp["merge_propose"]:
        keep.append({"proposal": _rep_row(entry["cluster"]),
                     "why": entry["why"]})
    for entry in rp["annotations"]:
        keep.append({"proposal": entry["proposal"],
                     "why": "no name captured — the identity reconciler "
                            "converts these to annotations (D5)"})
    for entry in rp["keep_open"]:
        row = entry.get("proposal") or (_rep_row(entry["cluster"])
                                        if entry.get("cluster") else {})
        keep.append({"proposal": row, "why": entry["why"]})
    for row in rp.get("updates") or []:
        keep.append({"proposal": row,
                     "why": "update to an existing record — adjudicated in "
                            "the queue, never batch-applied"})
    return {"add": add, "expire": expire, "keep_open": keep,
            "now_iso": now_iso}


def run_sweep(workspace_root, *, apply: bool = False,
              now_iso: str | None = None) -> dict:
    """Plan and (with apply=True) execute. Returns the plan plus per-item
    results and the undo batch id."""
    from event_gate import append_event

    ws = Path(workspace_root)
    plan = plan_sweep(ws, now_iso=now_iso)
    batch_id = "pbs_" + _clock_now(workspace_root).strftime("%Y%m%dT%H%M%SZ")
    results = {"added": [], "needs_confirm": [], "expired": [], "errors": []}
    if not apply:
        plan["batch_id"] = batch_id
        plan["applied"] = False
        return plan

    from confirm_flow import build_person_proposal_resolved_event
    from identity_reconcile import cluster_source_identifiers
    from people_writer import auto_add_person

    events_path = _events_path(ws)
    for entry in plan["add"]:
        p = entry["proposal"]
        try:
            res = auto_add_person(
                ws,
                canonical_name=p["name"].strip(),
                email=entry["email"],
                email_provenance=({"via": "person_proposal",
                                   "proposal_seq": p.get("seq"),
                                   "source_ref": p.get("source_ref")}
                                  if entry["email"] else None),
                source_skill="person-backlog-sweep",
                role=p.get("inferred_role") or None,
                source_identifiers=cluster_source_identifiers(
                    entry.get("cluster") or {"name": p.get("name"),
                                             "add_rows": [p]}, ws),
            )
        except Exception as exc:  # loud per-item, contained per-batch
            results["errors"].append({"seq": p.get("seq"),
                                      "error": f"{type(exc).__name__}: {exc}"})
            continue
        if res.get("status") == "needs_confirm":
            # Same-name collision — NEVER auto-forked. Left open for the
            # widget/disambiguation path.
            results["needs_confirm"].append({"seq": p.get("seq"),
                                             "name": p.get("name")})
            continue
        record = res["record"]
        # Tombstone EVERY member proposal in the identity cluster (PID1 D3:
        # the duplicate mentions are the corroboration evidence — the
        # cluster consumes them, they all close on the one add) + stamp the
        # undo batch markers. Seq-less members tombstone via the D8
        # fingerprint.
        members = (entry.get("cluster") or {}).get("rows") or [p]
        tombs = []
        for m in members:
            seq = m.get("seq")
            kwargs = {}
            if not isinstance(seq, int) or isinstance(seq, bool):
                seq = None
                kwargs["proposal_fingerprint"] = m.get("fingerprint")
            tomb = build_person_proposal_resolved_event(
                seq, resolution="person_added",
                source_skill="person-backlog-sweep",
                person_id=record["id"],
                note=f"backlog sweep {batch_id}", **kwargs)
            tomb.setdefault("data", {})
            tomb["data"]["brain_batch_id"] = batch_id
            tomb["data"]["brain_change_class"] = "person_org_creation_structured_fact"
            tomb["data"]["person_id"] = record["id"]
            tombs.append(tomb)
        append_event(events_path, tombs, holder="person-backlog-sweep")
        results["added"].append({"seq": p.get("seq"), "name": p.get("name"),
                                 "person_id": record["id"],
                                 "email": entry["email"],
                                 "n_proposals": len(members),
                                 "email_dropped_no_provenance":
                                     res.get("email_dropped_no_provenance")})
    for entry in plan["expire"]:
        p = entry["proposal"]
        try:
            seq = p.get("seq")
            kwargs = {}
            if not isinstance(seq, int) or isinstance(seq, bool):
                seq = None  # D8 — seq-less rows tombstone by fingerprint
                kwargs["proposal_fingerprint"] = p.get("fingerprint")
            tomb = build_person_proposal_resolved_event(
                seq, resolution="not_relevant",
                source_skill="person-backlog-sweep",
                note=f"expired by backlog sweep {batch_id} — {entry['why']}",
                **kwargs)
            tomb.setdefault("data", {})
            tomb["data"]["brain_batch_id"] = batch_id
            tomb["data"]["brain_change_class"] = "person_proposal_tombstone"
            append_event(events_path, [tomb], holder="person-backlog-sweep")
            results["expired"].append({"seq": p.get("seq"),
                                       "name": p.get("name")})
        except Exception as exc:
            results["errors"].append({"seq": p.get("seq"),
                                      "error": f"{type(exc).__name__}: {exc}"})
    # ONE audit event for the whole pass.
    append_event(events_path, [{
        "type": "person_backlog_swept",
        "source_skill": "person-backlog-sweep",
        "data": {
            "batch_id": batch_id,
            "n_added": len(results["added"]),
            "n_expired": len(results["expired"]),
            "n_needs_confirm": len(results["needs_confirm"]),
            "n_kept_open": len(plan["keep_open"]),
            "n_errors": len(results["errors"]),
        },
    }], holder="person-backlog-sweep")
    plan["batch_id"] = batch_id
    plan["applied"] = True
    plan["results"] = results
    return plan


def _narrate(plan: dict) -> str:
    lines = []
    mode = "APPLIED" if plan.get("applied") else "DRY RUN — nothing written"
    lines.append(f"Person backlog sweep ({mode})")
    lines.append(f"  add as contacts: {len(plan['add'])}")
    for e in plan["add"]:
        p = e["proposal"]
        lines.append(f"    + {p.get('name')!r} — {e['why']}")
    lines.append(f"  expire (aged, name-only): {len(plan['expire'])}")
    for e in plan["expire"]:
        lines.append(f"    - {e['proposal'].get('name') or '(no name)'!r} — {e['why']}")
    lines.append(f"  left open: {len(plan['keep_open'])}")
    for e in plan["keep_open"]:
        lines.append(f"    = {e['proposal'].get('name') or '(no name)'!r} — {e['why']}")
    if plan.get("applied"):
        res = plan["results"]
        lines.append(
            f"  applied: {len(res['added'])} added, {len(res['expired'])} "
            f"expired, {len(res['needs_confirm'])} held for a same-name "
            f"confirm, {len(res['errors'])} errors")
        lines.append(f"  undo: the whole pass reverses with batch id "
                     f"{plan['batch_id']} (adds archive, expiries reopen)")
    return "\n".join(lines)


def main() -> int:
    # Review F-5: Windows pipes default to cp1252 — the output carries
    # non-ASCII (middots, warning glyphs) and would crash the CLI.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="perform the writes (default: dry-run plan only)")
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    ap.add_argument("--json", action="store_true",
                    help="emit the machine-readable plan as well")
    args = ap.parse_args()
    plan = run_sweep(args.workspace, apply=args.apply, now_iso=args.now)
    print(_narrate(plan))
    if args.json:
        print(json.dumps(plan, default=str))
    return 0


__all__ = ["plan_sweep", "run_sweep"]


if __name__ == "__main__":
    raise SystemExit(main())
