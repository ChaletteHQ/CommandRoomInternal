#!/usr/bin/env python3
"""watch_gate.py — WATCHGATE: evidence strength, the bulk-accept fence,
WATCHING, and posture levels.

THE FAILURE THIS CLOSES
=======================
An accept surface presented weak guesses indistinguishably from strong ones
and let ONE gesture confirm many. On a reference substrate six commitments
went from open to "done" in two seconds, off proposals whose entire evidence
was a title match — the matcher's own conservative branch had refused to
close them a day earlier and had only PROPOSED. The batch apply undid that
refusal. After acceptance the weakness of the original evidence is invisible.

So the fix is not on the matcher. It is on the accept surface, in three
parts, and it assumes a human WILL rubber-stamp a long queue rather than
lecturing them not to:

  1. EVIDENCE STRENGTH on every row, in plain language, never a score.
  2. A BULK-ACCEPT FENCE — `confirm all`, a range, a group phrase operate on
     STRONG rows only. A weak row needs its own number, typed alone.
  3. WATCHING — a weak proposal does not sit in the queue at all. It parks,
     quietly, and either proves itself inside the window or is routed by
     STAKES at expiry. This is what keeps the queue short enough that the
     questions which DO surface get real answers.

WHAT THIS MODULE IS
===================
  weakness_reason(...)        THE weakness vocabulary — one notion, shared by
                              the intake queue and the proposal queue, so the
                              two surfaces can never drift apart
  evidence_strength(...)      that reason as STRONG / WEAK
  strength_line(...)          the row copy the user reads
  temporal_warning(...)       the apply-moment sanity check: evidence older
                              than the promise, or a meeting that has not
                              started yet
  screen_bulk_accept(...)     THE bulk-accept fence — ONE helper, and both
                              accept surfaces call it
  bulk_accept_ack(...)        the honest count line after a screened batch
  build_watch / park_in_watch / clear_watch / watch_of / is_watched
                              the WATCHING state: ADDITIVE `data.watch` on an
                              item that stays `status: "open"`. Never a new
                              status value — every existing reader keeps
                              working, and a pre-WATCHGATE reader sees a
                              watched item as the ordinary open item it is.
  stakes_for(...)             high / low, from the workspace's OWN records:
                              client org status and the pipeline tracker,
                              OR'd, plus money language and a passed due date
  expiry_fate(...)            posture levels 1/2/3 decide the fate of an
                              UNPROVEN promise at window expiry — and only
                              that. Proof always closes silently at every
                              level; questions are always one-tap.
  route_expiry(...)           the due-now split: assume / ask / carried, with
                              the ask cap honored and the overflow CARRIED,
                              never dropped
  self_confirm(...)           corroboration inside the window closes the item
                              silently, with evidence naming BOTH the original
                              title match and the corroborating signal
  confirm_review_rows(...)    the proposal-queue batch apply — screens through
                              the shared fence, closes what it may, parks the
                              rest
  build_watching_view / render_watching_text
                              `show watching`: a read-only list of parked
                              items, each individually confirmable on demand

CONNECTOR-AGNOSTIC BY CONSTRUCTION
==================================
No corroboration is FETCHED here. `self_confirm` takes a signal descriptor
whose `kind` is drawn from a fixed, vendor-free vocabulary
(`CORROBORATION_KINDS`); the caller reads the workspace's DECLARED backends
and hands the result in. A workspace with thin connectors simply never
produces one — and then the item degrades to stakes-routed expiry, which
needs no connector at all. Nothing here errors on a missing backend, and
nothing here silently stops watching.

CLI:

    python3 shared/scripts/watch_gate.py view <WORKSPACE> [--now ISO]
    python3 shared/scripts/watch_gate.py view-json <WORKSPACE> [--now ISO]

stdlib only.
"""
from __future__ import annotations
try:
    from text_clip import clip  # noqa: E402
except ImportError:  # pragma: no cover — direct-path fallback
    import sys as _sys_tc
    from pathlib import Path as _Path_tc
    _sys_tc.path.insert(0, str(_Path_tc(__file__).resolve().parent))
    from text_clip import clip  # noqa: E402

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SOURCE_SKILL = "watch-gate"

# ---------------------------------------------------------------------------
# §2.1 — evidence strength, in ONE vocabulary
# ---------------------------------------------------------------------------
#
# BULKGUARD introduced the first half of this notion on the intake queue: a
# row is too weak for a bulk answer when the capture recorded no evidence, or
# when the recorded "evidence" is the commitment's own words echoed back. The
# proposal queue needs the same idea plus two more cases it alone can see (a
# match with no completion signal; a match whose ordering is impossible). One
# function owns all four so the two surfaces can never disagree about what
# "weak" means.

STRONG = "strong"
WEAK = "weak"

# The evidence-string marker that means "the commitment's own words matched
# the source, and nothing else did".
TITLE_MATCH_MARKER = "title match"

NO_EVIDENCE_REASON = "no evidence recorded — the extractor's guess alone"
TITLE_MATCH_REASON = "a title match only, not source text"
NO_COMPLETION_REASON = ("the topic came up, but nothing in the source says it "
                        "got done")
FUTURE_MEETING_REASON = "the meeting this refers to hasn't happened yet"
STALE_EVIDENCE_REASON = "the evidence came before the promise was made"

WEAK_LEAD = "This came up in a meeting, but I can't see proof it got done"

# ---------------------------------------------------------------------------
# The PRODUCER'S OWN strength stamp (RIDERS item c)
# ---------------------------------------------------------------------------
#
# `weakness_reason` reads the evidence TEXT. Some producers know something the
# text cannot say. GRANOLA1's non-attendee lane is the live case: a close
# proposal built from a meeting the user was not in carries real completion
# language, so the text-only screen returns STRONG — and the row rendered
# identically to one from a meeting they sat through. The producer stamps
# `evidence_strength` / `strength_reason` / `auto_close_blocked` on the event;
# until now nothing rendered them, so a weak secondhand proposal and a strong
# firsthand one were the same row on screen.
#
# ONE reader, ONE sentence, both proposal surfaces. A second wording would be
# a second idea of what the row says.
STAMP_STRENGTH_FIELD = "evidence_strength"
STAMP_REASON_FIELD = "strength_reason"
STAMP_BLOCKED_FIELD = "auto_close_blocked"
STAMP_FALLBACK_REASON = "the source is secondhand"
BLOCKED_LEAD = "never auto-closed"
STAMPED_WEAK_LEAD = "weaker evidence"


def stamped_strength(data) -> dict:
    """The producer's own strength claim off an event payload / row, or {}.

    {} when there is no stamp — which is what keeps an unstamped row's render
    byte-identical to what it was before this existed.

    A stamp may only make a row WEAKER. A producer claiming STRONG never
    promotes a row the text screen called weak: the bulk fence screens on the
    text, and a badge that disagrees with the fence is worse than no badge.
    """
    d = data if isinstance(data, dict) else {}
    strength = str(d.get(STAMP_STRENGTH_FIELD) or "").strip().lower()
    if strength not in (STRONG, WEAK):
        return {}
    return {
        "strength": strength,
        "reason": str(d.get(STAMP_REASON_FIELD) or "").strip(),
        "blocked": bool(d.get(STAMP_BLOCKED_FIELD)),
    }


def stamped_strength_fields(data) -> dict:
    """The stamp, re-emitted under its OWN field names for a render row — or
    {} when there is none, so an unstamped row's dict is key-for-key what it
    was before this existed (the byte-identity property the riders pin)."""
    stamp = stamped_strength(data)
    if not stamp:
        return {}
    out = {STAMP_STRENGTH_FIELD: stamp["strength"],
           STAMP_BLOCKED_FIELD: stamp["blocked"]}
    if stamp["reason"]:
        out[STAMP_REASON_FIELD] = stamp["reason"]
    return out


def stamped_strength_note(data) -> str:
    """The one clause a stamped-WEAK row adds to its render, or "".

    "" for an unstamped row and for a stamp that says STRONG — a badge that
    fires on everything says nothing. RENDER ONLY: this changes what the row
    SAYS, never what any fence decides.
    """
    stamp = stamped_strength(data)
    if not stamp or stamp["strength"] != WEAK:
        return ""
    reason = stamp["reason"] or STAMP_FALLBACK_REASON
    lead = BLOCKED_LEAD if stamp["blocked"] else STAMPED_WEAK_LEAD
    return f"{lead} — {reason}"


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


def weakness_reason(evidence, *, completion_signal=None,
                    temporal=None) -> str:
    """Why a row is too weak for a bulk answer — or "" when it is not.

    WEAK means the user has nothing real to weigh:

      * no evidence text at all — a bare extractor guess;
      * evidence that is a TITLE MATCH, i.e. the commitment's own words echoed
        back rather than source text (the exact evidence string behind the
        six-wrong-closes incident);
      * a `temporal` warning from the apply-moment ordering check — a row
        whose evidence could not physically prove the thing it claims is the
        weakest row there is, whatever its text says;
      * `completion_signal=False` — the caller looked for fulfillment language
        and found none. `None` means "not assessed" and never weakens a row,
        so callers that cannot judge (the intake queue reads captures, not
        matches) behave exactly as they did before this module existed.

    Order is by severity, and it decides only WHICH sentence the user reads —
    any one of them holds the row.
    """
    if temporal:
        return str(temporal)
    evd = evidence.strip() if isinstance(evidence, str) else ""
    if not evd:
        return NO_EVIDENCE_REASON
    if TITLE_MATCH_MARKER in evd.lower():
        return TITLE_MATCH_REASON
    if completion_signal is False:
        return NO_COMPLETION_REASON
    return ""


def commitment_weak_reason(ev: dict) -> str:
    """`weakness_reason` over a projected commitment event — the intake
    queue's shape. Reads `data.evidence` and nothing else: a capture carries
    no match, so there is no completion signal to assess and no ordering to
    check."""
    val = (ev.get("data") or {}).get("evidence") if isinstance(ev, dict) else None
    return weakness_reason(val)


def evidence_strength(evidence, *, completion_signal=None,
                      temporal=None) -> str:
    """STRONG or WEAK for the same inputs `weakness_reason` reads."""
    return WEAK if weakness_reason(
        evidence, completion_signal=completion_signal,
        temporal=temporal) else STRONG


def strength_line(reason: str, *, evidence: str = "", limit: int = 110) -> str:
    """The row's one plain-language evidence line.

    A weak row NAMES WHAT IS MISSING; a strong row quotes what it rests on.
    Neither ever shows a number — a score is not something a person can weigh,
    and printing one invites arguing with the arithmetic instead of reading
    the sentence.
    """
    if reason:
        return f"{WEAK_LEAD} — {reason}."
    evd = (evidence or "").strip()
    if len(evd) > limit:
        evd = evd[:limit - 3] + "..."
    return f'The record says: "{evd}"' if evd else "There's source text behind this one."


# ---------------------------------------------------------------------------
# §2.5 — temporal sanity, checked at the APPLY moment
# ---------------------------------------------------------------------------


def _parse(value) -> Optional[_dt.datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    try:
        from event_time import parse_ts
        return parse_ts(str(value))
    except Exception:
        return None


def temporal_warning(*, evidence_ts=None, promise_ts=None,
                     meeting_start=None, apply_ts=None) -> Optional[str]:
    """The ordering check, run WHEN THE ROW IS ACCEPTED — not when it was
    proposed.

    That placement is the whole point. A proposal written on Monday can be
    perfectly well ordered and still be accepted on Wednesday for a meeting
    that starts Wednesday afternoon; the violation exists only at the moment
    the acceptance would write a closure. Two shapes:

      * `apply_ts` earlier than `meeting_start` — accepting this would close a
        promise on the strength of a meeting that has not happened yet.
      * `evidence_ts` earlier than `promise_ts` — the source predates the
        promise, so it cannot be evidence the promise was kept.

    Returns the warning sentence, or None. Any unparseable input makes that
    comparison inert (never a false alarm, never a crash) — an ordering we
    cannot establish is not an ordering violation.
    """
    a_ts, m_ts = _parse(apply_ts), _parse(meeting_start)
    if a_ts is not None and m_ts is not None and a_ts < m_ts:
        return FUTURE_MEETING_REASON
    e_ts, p_ts = _parse(evidence_ts), _parse(promise_ts)
    if e_ts is not None and p_ts is not None and e_ts < p_ts:
        return STALE_EVIDENCE_REASON
    return None


# ---------------------------------------------------------------------------
# §2.2 — THE bulk-accept fence
# ---------------------------------------------------------------------------


def screen_bulk_accept(rows, *, individually_named=()) -> dict:
    """Split an accept batch into what may be written and what is HELD.

    THE fence. Both accept surfaces call this one function — the intake
    queue's `confirm all [group]` / ranges (`needs_review_queue.confirm_items`)
    and the proposal queue's batch apply (`confirm_review_rows`). It is
    deliberately pure and shapeless: a row is `{"id", "weak_reason"}` and
    nothing more, so neither surface can grow its own private idea of what a
    bulk gesture may sweep.

    `individually_named` is the ONLY override, and it means exactly one thing:
    the user typed THAT row's number as a standalone token. `all`, a range and
    a group phrase name nothing individually, so they can never populate it.
    Sweeping a bare guess into the book takes a human reading THAT row and
    naming THAT number.

    Returns {"accept": [id, ...], "held": [{"id", "reason"}, ...],
             "n_accept": int, "n_held": int} — order preserved on both sides.
    """
    named = {str(x) for x in (individually_named or ())}
    accept: list[str] = []
    held: list[dict] = []
    for row in rows or []:
        rid = str(row.get("id"))
        reason = (row.get("weak_reason") or "").strip()
        if reason and rid not in named:
            held.append({"id": rid, "reason": reason})
        else:
            accept.append(rid)
    return {"accept": accept, "held": held,
            "n_accept": len(accept), "n_held": len(held)}


def bulk_accept_ack(n_accept: int, n_held: int, *, parked: int = 0) -> str:
    """The count line after a screened batch — what went through, what did
    not, and how to act on one. Never scolds, never explains the fence."""
    lead = f"Confirmed {n_accept}." if n_accept else "Confirmed nothing."
    if not n_held:
        return lead
    noun = "item" if n_held == 1 else "items"
    where = "on watch" if parked else "waiting"
    return (f"{lead} {n_held} {noun} I can't prove — {where}. "
            f"Answer one by its own number to keep it now.")


# ---------------------------------------------------------------------------
# §2.3 — WATCHING: additive data, never a status value
# ---------------------------------------------------------------------------
#
# BACK-COMPAT IS THE POINT. A watched item stays `status: "open"` and gains
# `data.watch`. `load_open_commitments` keeps returning it, every count keeps
# counting it, and a reader built before this module existed sees an ordinary
# open commitment — which is what it is. (The KIND1 lesson: a plain new-field
# CHECK breaks live rows; only additive data is safe.)

WATCH_STATE_UNCONFIRMED = "unconfirmed"

CORROBORATION_KINDS = (
    "sent_mail",             # the user's own outbound message fulfilled it
    "calendar_event",        # a matching event that actually occurred
    "transcript_completion", # a later conversation with real completion language
    "delivery_evidence",     # a delivery signal on the item itself
)

ASSUMED_EVIDENCE = "assumed done — discussed in meeting, unconfirmed"
DEFAULT_ORIGINAL_EVIDENCE = "discussed in a meeting, title match only"


def _now_iso() -> str:
    return _clock_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _events_path(ws) -> Path:
    return Path(ws) / "_hq" / "data" / "events.jsonl"


def watch_until_iso(now_iso: str, days: int) -> str:
    """The window edge. An unparseable `now` yields "" — the caller then parks
    with no edge, which `route_expiry` reads as never-due rather than
    always-due (a window we cannot compute must not expire everything)."""
    now = _parse(now_iso)
    if now is None:
        return ""
    return (now + _dt.timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_watch(*, watch_until: str, stakes, matched_ref=None,
                match_score=None, state: str = WATCH_STATE_UNCONFIRMED,
                evidence: str = "", reason: str = "") -> dict:
    """The `data.watch` payload.

    `match_score` rides along because the calibration pass reads it later —
    but NO render surface prints it (§2.1). `evidence` is the ORIGINAL
    evidence string, kept so a later self-confirm can name both halves.
    """
    watch: dict = {
        "state": state,
        "watch_until": watch_until or "",
        "stakes": stakes if isinstance(stakes, str) else (
            (stakes or {}).get("level") or STAKES_LOW),
    }
    if isinstance(stakes, dict) and stakes.get("reasons"):
        watch["stakes_reasons"] = list(stakes["reasons"])
    if matched_ref:
        watch["matched_ref"] = str(matched_ref)
    if match_score is not None:
        try:
            watch["match_score"] = float(match_score)
        except (TypeError, ValueError):
            pass
    if evidence:
        watch["evidence"] = clip(evidence)
    if reason:
        watch["reason"] = str(reason)[:200]
    return watch


def watch_of(ev) -> Optional[dict]:
    """The projected item's watch payload, or None. Defensive: a malformed
    value reads as unwatched rather than raising."""
    if not isinstance(ev, dict):
        return None
    w = (ev.get("data") or {}).get("watch")
    return w if isinstance(w, dict) and w else None


def is_watched(ev) -> bool:
    return watch_of(ev) is not None


def watched_commitment_ids(workspace_root, *, events_path=None) -> set:
    """The ids of every currently-watched open commitment. The cheap read —
    no stakes derivation, no row building — because its callers are FILTERS
    (the proposal queue asking "is this one already parked?"), and a filter
    that costs a full stakes pass per row would be paid on every fire.

    Defensive: any failure yields an empty set, which fails toward SHOWING a
    row rather than hiding one. A queue that renders something already parked
    is a redundancy; a queue that silently hides a row it should offer is a
    disappearance, and those are not the same mistake.
    """
    try:
        from cru_match import (_commitment_id, load_open_commitments,
                               split_pending_review)
        path = events_path or str(_events_path(workspace_root))
        confirmed, _nr = split_pending_review(load_open_commitments(
            path, workspace_root=str(workspace_root)))
        return {_commitment_id(ev) for ev in confirmed if is_watched(ev)}
    except Exception:
        return set()


def park_in_watch(workspace_root, commitment_id, *, watch: dict,
                  source_skill: str, note: str = "", force: bool = False,
                  known_watched=None) -> dict:
    """Park an item in WATCHING — one `commitment_updated` carrying
    `data.watch_set: true` plus the payload. The capture is never rewritten;
    the projector folds the latest watch read-side.

    IDEMPOTENT. An item already carrying a watch is a NO-OP
    (`{"status": "already_watching"}`) and appends nothing. Without this, a
    second bulk answer over the same row wrote a second `watch_set` event —
    harmless to the projection (latest wins) but three separate kinds of
    wrong: it doubles the history, it re-stamps the item as freshly touched
    for anything reading update events, and it makes "how many times has this
    been parked" unanswerable. `force=True` re-parks deliberately (a changed
    window or a re-park after an expiry pass); `known_watched` lets a batch
    caller pass the set it already computed instead of re-projecting per row.

    Returns {"status": "watching"|"already_watching", "commitment_id": ...,
             "event": {...}} — `event` only on an actual write.

    REFUSES AN EMPTY ID, like every other id-bearing writer. This writer took
    whatever it was handed and stringified it, so a caller that arrived with no
    id wrote `commitment_updated` with `commitment_id: ""` into an append-only
    log — permanently, with only a validator warning. It happened for real:
    a rendered card row reached the batch park leg with its ids only under
    `data`, and the watch landed on nothing while the actual commitment came
    back on the next fire. `close_commitment` has always validated its id,
    which is the only reason the ACCEPT leg never wrote the same shape.

    Raising is the right answer rather than returning a status: every batch
    caller already wraps this in `except Exception` and reports the row as
    `not_parked` with the message, so the failure is visible per row and the
    batch survives.
    """
    from event_gate import append_event

    cid = str(commitment_id or "").strip()
    if not cid:
        from commitment_state import CommitmentIdError

        raise CommitmentIdError(
            "park_in_watch got an empty commitment id — a watch has to be "
            "about something. The caller's row arrived with no id at the top "
            "level (a rendered card row keeps its ids under `data` — see "
            "proposal_digests._normalize_candidate).")
    if not force:
        already = (known_watched if known_watched is not None
                   else watched_commitment_ids(workspace_root))
        if cid in already:
            return {"status": "already_watching", "commitment_id": cid}
    data = {"commitment_id": cid, "watch_set": True,
            "watch": dict(watch or {})}
    if note:
        data["note"] = note[:200]
    ev = {"type": "commitment_updated", "source_skill": source_skill,
          "data": data}
    append_event(_events_path(workspace_root), [ev], holder=source_skill)
    return {"status": "watching", "commitment_id": cid, "event": ev}


def clear_watch(workspace_root, commitment_id, *, source_skill: str,
                note: str = "") -> dict:
    """Stop watching an item WITHOUT resolving it — the mirror of
    `park_in_watch`. One `commitment_updated` carrying
    `data.watch_cleared: true`; the item stays exactly as open as it was."""
    from event_gate import append_event

    cid = str(commitment_id)
    data = {"commitment_id": cid, "watch_cleared": True}
    if note:
        data["note"] = note[:200]
    ev = {"type": "commitment_updated", "source_skill": source_skill,
          "data": data}
    append_event(_events_path(workspace_root), [ev], holder=source_skill)
    return {"status": "cleared", "commitment_id": cid, "event": ev}


# ---------------------------------------------------------------------------
# Stakes — read from the workspace's OWN records, identically in every repo
# ---------------------------------------------------------------------------

STAKES_HIGH = "high"
STAKES_LOW = "low"

# Money language, in the words a person actually writes. Deliberately narrow:
# a false HIGH costs one question, but a false LOW lets a paid deliverable be
# assumed done in silence.
_MONEY_RE = re.compile(
    r"(\$\s?\d|\b\d+\s?(k|m)\b|\busd\b|\beur\b|\bgbp\b|retainer|invoice|"
    r"invoiced|deposit|payment|\bpaid\b|\bfee\b|\bfees\b|quote|proposal price|"
    r"contract value|statement of work|\bsow\b|purchase order)",
    re.IGNORECASE)

# Reason → weight, for ordering the questions when the cap bites. A named
# paying relationship outranks a generic external counterparty.
_STAKES_WEIGHTS = {"client_org": 4, "pipeline": 3, "money": 3,
                   "overdue": 2, "counterparty": 1}

# The reasons that make an item money-shaped, for posture level 1.
_MONEY_SHAPED = frozenset({"client_org", "pipeline", "money"})


def stakes_context(workspace_root) -> dict:
    """The workspace-side inputs stakes detection needs, read ONCE.

    {"client_org_ids": set, "deal_org_ids": set, "person_org": {person_id: org_id}}

    Every read is defensive: a missing entities.json, an unreadable pipeline,
    a thin workspace with no orgs at all — each yields an empty set and the
    detection degrades to the signals it can still see (money language, a
    passed due date, a counterparty). It never errors and never blocks.
    """
    ctx = {"client_org_ids": set(), "deal_org_ids": set(), "person_org": {}}
    if workspace_root is None:
        return ctx
    ws = Path(workspace_root)
    try:
        raw = (ws / "_hq" / "data" / "entities.json").read_text("utf-8")
        data = json.loads(raw) or {}
    except Exception:
        data = {}
    for org in (data.get("orgs") or []):
        if not isinstance(org, dict):
            continue
        if (org.get("relationship_type") or "").strip().lower() == "client" \
                and org.get("id"):
            ctx["client_org_ids"].add(org["id"])
    for p in (data.get("people") or []):
        if not isinstance(p, dict) or not p.get("id"):
            continue
        oid = p.get("primary_org_id") or p.get("affiliation_id") or p.get("org_id")
        if oid:
            ctx["person_org"][p["id"]] = oid
    # The pipeline tracker's own reader — never a second projection of deals.
    try:
        from deal_state import list_open_deals
        for row in list_open_deals(workspace_root) or []:
            if isinstance(row, dict) and row.get("org_id"):
                ctx["deal_org_ids"].add(row["org_id"])
    except Exception:
        pass
    return ctx


def stakes_for(ev, *, workspace_root=None, now_iso=None,
               context: Optional[dict] = None) -> dict:
    """How much it costs to be wrong about this one item.

    HIGH when ANY of these holds — the OR is the decision, per spec §4.3:

      counterparty  someone outside the workspace is waiting on it
      client_org    that counterparty belongs to an org the workspace records
                    as a CLIENT (org client status)
      pipeline      that counterparty's org has an OPEN deal (pipeline tracker)
      money         the item's own words are money-shaped
      overdue       the due date has already passed

    LOW otherwise — internal, no counterparty, no money, no date. Returns
    {"level", "reasons": [...], "rank": int}. `rank` orders the questions when
    the ask cap bites.
    """
    ctx = context if isinstance(context, dict) else stakes_context(workspace_root)
    d = (ev.get("data") or {}) if isinstance(ev, dict) else {}
    reasons: list[str] = []

    try:
        from commitment_parties import counterparty_ids, counterparty_names
        cp_ids = counterparty_ids(ev)
        # F-28: threaded, because the phantom it collapses would change this
        # answer. One person written once as a resolved id and once as that
        # person's free-text name is ONE counterparty; unthreaded, the same
        # item reads as having a counterparty in one place and two in
        # another, and stakes would disagree with itself across surfaces.
        cp_names = counterparty_names(ev, workspace_root=workspace_root)
    except Exception:
        cp_ids, cp_names = [], []
    if cp_ids or cp_names:
        reasons.append("counterparty")
    for cid in cp_ids:
        org = ctx.get("person_org", {}).get(cid)
        if org and org in ctx.get("client_org_ids", set()):
            if "client_org" not in reasons:
                reasons.append("client_org")
        if org and org in ctx.get("deal_org_ids", set()):
            if "pipeline" not in reasons:
                reasons.append("pipeline")

    text = " ".join(str(d.get(k) or "") for k in ("title", "summary", "note"))
    if _MONEY_RE.search(text):
        reasons.append("money")

    due = d.get("due") or d.get("due_date")
    if due:
        try:
            from commitment_state import is_overdue
            if is_overdue(due, now_iso or _now_iso()):
                reasons.append("overdue")
        except Exception:
            pass

    rank = sum(_STAKES_WEIGHTS.get(r, 0) for r in reasons)
    return {"level": STAKES_HIGH if reasons else STAKES_LOW,
            "reasons": reasons, "rank": rank}


# ---------------------------------------------------------------------------
# §2.4 — posture levels
# ---------------------------------------------------------------------------
#
# Proof closes silently at EVERY level. Questions are one-tap at EVERY level.
# The level changes exactly one thing: the fate of an UNPROVEN promise when
# its window runs out.

POSTURE_HANDLE_IT = 1
POSTURE_BALANCED = 2
POSTURE_CHECK_WITH_ME = 3
POSTURE_LEVELS = (POSTURE_HANDLE_IT, POSTURE_BALANCED, POSTURE_CHECK_WITH_ME)
POSTURE_NAMES = {
    POSTURE_HANDLE_IT: "Handle it",
    POSTURE_BALANCED: "Balanced",
    POSTURE_CHECK_WITH_ME: "Check with me",
}

FATE_ASSUME = "assume"
FATE_ASK = "ask"


def expiry_fate(stakes, posture_level: int) -> str:
    """FATE_ASSUME (let it go quietly, recorded as assumed — never as
    verified) or FATE_ASK (one question).

      1 Handle it      ask only when it is money-shaped AND the date has
                       passed; everything else is let go
      2 Balanced       ask on any high-stakes signal (shipped default)
      3 Check with me  nothing is let go on a guess — every unproven item
                       becomes a question (the cap still applies, and the
                       overflow carries)
    """
    reasons = set((stakes or {}).get("reasons") or []) if isinstance(stakes, dict) else set()
    level = (stakes or {}).get("level") if isinstance(stakes, dict) else stakes
    if posture_level == POSTURE_CHECK_WITH_ME:
        return FATE_ASK
    if posture_level == POSTURE_HANDLE_IT:
        return FATE_ASK if (reasons & _MONEY_SHAPED) and "overdue" in reasons \
            else FATE_ASSUME
    return FATE_ASK if level == STAKES_HIGH else FATE_ASSUME


def _day_ordinal(now_iso) -> int:
    """The fire's UTC DATE as an integer, or 0 when unreadable. The rotation
    below turns on the UTC day and nothing finer, so two fires on the same
    UTC day agree (a pair straddling UTC midnight may not — harmless for a
    weekly fixed-time fire)."""
    ts = _parse(now_iso)
    return ts.date().toordinal() if ts is not None else 0


def rotate_ask(ask, *, cap: int, now_iso: str) -> tuple:
    """Bound the ask list at `cap` WITHOUT letting the same rows own it forever.

    THE ROTATION RULE (WATCHGATE N-4). Stakes ranking alone made `carried` a
    promise the code did not keep: this module's own contract says the overflow
    is what "the next fire asks", but the ordering is stable, so the next fire
    asked the same five and a lower-stakes watched item could sit past its
    window indefinitely — never answered, never let go, and never shown. That
    is §0's "never silently stops watching" arriving by a different road.

    So the LAST slot rotates, round-robin by the DATE of the fire, over the
    rows from the cap boundary down — `ask[cap - 1:]`. That pool, and not the
    overflow alone, is the load-bearing detail: rotating only over `ask[cap:]`
    would permanently displace whatever sits at `cap - 1` and simply move the
    starvation one row up (measured on the shipped fixture: with a single
    overflow row, the row at the boundary never surfaced again). Including the
    boundary row makes the pool a genuine cycle, so every row in it takes the
    slot once per `len(pool)` days.

    Deterministic — the same day yields the same choice, so a re-fire is not a
    reshuffle. The top `cap - 1` are still the highest-stakes rows, so a
    money-shaped question is never the one that steps aside.

    Same doctrine as `commitment_state.cap_needs_attention`: no new state, a
    pure function of the day and the current list, which is what keeps it honest
    across machines — a per-machine "last shown" ledger would rotate differently
    on each of them. `carried` keeps the pool's own order minus the chosen row,
    so the row nearest the cap is still first in line.

    Returns `(shown, carried)`. Pure — no I/O.
    """
    rows = list(ask or [])
    cap = max(0, int(cap))
    if cap == 0 or len(rows) <= cap:
        return rows[:cap], rows[cap:]
    pool = rows[cap - 1:]
    pick = pool[_day_ordinal(now_iso) % len(pool)]
    shown = rows[:cap - 1] + [pick]
    carried = [r for r in pool if r is not pick]
    return shown, carried


def route_expiry(rows, *, now_iso: str, posture_level: int,
                 ask_cap: int) -> dict:
    """Split watched rows into what happens to them now.

    `rows`: [{"id", "title", "stakes": {...}, "watch_until": iso}, ...]

    Returns {"assume": [...], "ask": [...], "carried": [...],
             "not_due": [...]}. Rows whose window has not run out are
    `not_due` and untouched. Of the due ones, `expiry_fate` decides; the ask
    list is ordered by stakes and bounded at `ask_cap` through `rotate_ask`, and
    everything past the cap lands in `carried` — which the next fire really does
    ask, because the last slot rotates over the rows from the cap boundary down,
    by the DATE of the fire. Overflow
    is never dropped: an unanswered question that quietly disappears is the
    defect this whole module exists to prevent, arriving by a different road.
    """
    now = _parse(now_iso)
    assume: list[dict] = []
    ask: list[dict] = []
    not_due: list[dict] = []
    for row in rows or []:
        edge = _parse((row or {}).get("watch_until"))
        if now is None or edge is None or edge > now:
            not_due.append(row)
            continue
        if expiry_fate(row.get("stakes"), posture_level) == FATE_ASSUME:
            assume.append(row)
        else:
            ask.append(row)
    ask.sort(key=lambda r: (-int(((r.get("stakes") or {}).get("rank") or 0)),
                            str(r.get("watch_until") or ""), str(r.get("id"))))
    shown, carried = rotate_ask(ask, cap=ask_cap, now_iso=now_iso)
    return {"assume": assume, "ask": shown, "carried": carried,
            "not_due": not_due}


# ---------------------------------------------------------------------------
# Self-confirm and expiry — the two ways a watch ends
# ---------------------------------------------------------------------------


def dual_evidence(original: str, signal: dict) -> str:
    """The evidence string a self-confirm writes: BOTH halves, always. The
    original guess is named so the record never claims more certainty than it
    had, and the corroborating signal is named so the close can be audited."""
    orig = (original or "").strip() or DEFAULT_ORIGINAL_EVIDENCE
    detail = (signal or {}).get("detail") or (signal or {}).get("kind") or ""
    return clip(f"{orig}; confirmed by {detail}".strip())


def self_confirm(workspace_root, commitment_id, *, signal: dict,
                 resolved_by: str, source_skill: str,
                 watch: Optional[dict] = None) -> dict:
    """Corroboration inside the window closes the item — silently.

    `signal` is `{"kind": <one of CORROBORATION_KINDS>, "detail": str,
    "observed_at": iso}`, built by the CALLER from the workspace's declared
    backends. An unrecognized kind is a loud ValueError rather than a
    permissive pass: this vocabulary is the connector-agnostic contract, and a
    vendor-shaped kind sneaking through would be exactly the coupling §0
    forbids.

    Closes through `commitment_state.close_commitment` — THE closure path —
    with `user_confirmed=False`, because this is a programmatic close and
    saying otherwise would launder it. An item that is ALSO an unconfirmed
    extraction therefore refuses to close and is reported, not written: a
    guess never self-closes off another guess.
    """
    from commitment_state import (CommitmentIdError, OpenSubitemsError,
                                  PendingReviewError, close_commitment)

    kind = (signal or {}).get("kind")
    if kind not in CORROBORATION_KINDS:
        raise ValueError(
            f"unknown corroboration kind {kind!r} — allowed: "
            f"{CORROBORATION_KINDS}. Signals are named by WHAT they are, "
            "never by which product produced them.")
    original = (watch or {}).get("evidence") or DEFAULT_ORIGINAL_EVIDENCE
    try:
        res = close_commitment(
            workspace_root, str(commitment_id), resolved_by=resolved_by,
            evidence=dual_evidence(original, signal),
            source_skill=source_skill, resolution="done",
            extra_data={"watch_resolution": "self_confirmed",
                        "corroboration_kind": kind},
        )
    except PendingReviewError as exc:
        return {"status": "held_pending_review",
                "commitment_id": str(commitment_id), "detail": str(exc)}
    except (CommitmentIdError, OpenSubitemsError) as exc:
        return {"status": "not_closed", "commitment_id": str(commitment_id),
                "detail": str(exc)}
    return res


def close_as_assumed(workspace_root, commitment_id, *, resolved_by: str,
                     source_skill: str) -> dict:
    """The low-stakes expiry close. Recorded as ASSUMED, never as verified —
    the evidence string says so in the words a person would use, and
    `data.assumed_done` lets any surface badge it honestly."""
    from commitment_state import (CommitmentIdError, OpenSubitemsError,
                                  PendingReviewError, close_commitment)
    try:
        return close_commitment(
            workspace_root, str(commitment_id), resolved_by=resolved_by,
            evidence=ASSUMED_EVIDENCE, source_skill=source_skill,
            resolution="done",
            extra_data={"assumed_done": True, "watch_resolution": "assumed"},
        )
    except PendingReviewError as exc:
        return {"status": "held_pending_review",
                "commitment_id": str(commitment_id), "detail": str(exc)}
    except (CommitmentIdError, OpenSubitemsError) as exc:
        return {"status": "not_closed", "commitment_id": str(commitment_id),
                "detail": str(exc)}


# ---------------------------------------------------------------------------
# Reading the watched set
# ---------------------------------------------------------------------------


def load_watched(workspace_root, *, now_iso=None) -> list[dict]:
    """Every WATCHED open commitment, as row dicts.

    Reads the CONFIRMED half of the projection through
    `cru_match.split_pending_review` — the seam. An unconfirmed extraction is
    a queue member, not a promise, and it belongs to the intake queue; parking
    one here would put the same item in two places.
    """
    from cru_match import (_commitment_field, _commitment_id,
                           load_open_commitments, split_pending_review)

    ws = Path(workspace_root)
    now = now_iso or _now_iso()
    confirmed, _needs_review = split_pending_review(load_open_commitments(
        str(_events_path(ws)), workspace_root=str(ws)))
    ctx = stakes_context(ws)
    out: list[dict] = []
    for ev in confirmed:
        w = watch_of(ev)
        if not w:
            continue
        out.append({
            "id": _commitment_id(ev),
            "commitment_id": _commitment_id(ev),
            "title": _commitment_field(ev, "title") or "(untitled)",
            "due": _commitment_field(ev, "due") or None,
            "watch": w,
            "watch_until": w.get("watch_until") or "",
            "evidence": w.get("evidence") or "",
            "stakes": stakes_for(ev, workspace_root=ws, now_iso=now,
                                 context=ctx),
        })
    out.sort(key=lambda r: (str(r["watch_until"] or "~"), r["id"]))
    return out


def days_left(watch_until: str, now_iso: str) -> Optional[int]:
    edge, now = _parse(watch_until), _parse(now_iso)
    if edge is None or now is None:
        return None
    return int((edge - now).total_seconds() // 86400)


# ---------------------------------------------------------------------------
# The proposal-queue batch apply — the second callsite of the shared fence
# ---------------------------------------------------------------------------


def confirm_review_rows(workspace_root, rows, *, resolved_by: str,
                        individually_named=(), now_iso=None,
                        source_skill: str = "apply-choices",
                        watch_window_days: Optional[int] = None) -> dict:
    """Apply a batch of proposal-queue confirms, screened by the shared fence.

    THIS IS THE CALLSITE THE INCIDENT WENT THROUGH. Six rows became "done" in
    two seconds because the batch dispatch had no idea what any of them rested
    on. Now the batch is screened first: every row carries its own weakness
    reason (computed here, including the APPLY-MOMENT ordering check), the
    fence decides what may be written, and everything held is PARKED rather
    than left to be re-swept by the next bulk gesture.

    `rows`: [{"id" (wire id, verbatim), "commitment_id", "evidence",
              "match_score", "has_completion_signal" (optional),
              "meeting_start" / "promise_ts" / "evidence_ts" (optional),
              "matched_ref" (optional)}, ...]

    Returns {"results", "n_confirmed", "n_held", "n_parked", "n_failed",
             "held", "ack"}.
    """
    from commitment_state import (CommitmentIdError, OpenSubitemsError,
                                  PendingReviewError, close_commitment)
    try:
        from confidence import watch_window_days as _window
    except Exception:  # pragma: no cover — shipped constant fallback
        def _window(_ws=None):
            return 14

    ws = Path(workspace_root)
    now = now_iso or _now_iso()
    window = int(watch_window_days if watch_window_days is not None
                 else _window(ws))

    screened_rows: list[dict] = []
    by_id: dict[str, dict] = {}
    for row in rows or []:
        rid = str((row or {}).get("id") or (row or {}).get("commitment_id") or "")
        warn = temporal_warning(
            evidence_ts=row.get("evidence_ts"),
            promise_ts=row.get("promise_ts"),
            meeting_start=row.get("meeting_start"),
            apply_ts=now,
        )
        reason = weakness_reason(
            row.get("evidence"),
            completion_signal=row.get("has_completion_signal"),
            temporal=warn,
        )
        enriched = dict(row)
        enriched["id"] = rid
        enriched["weak_reason"] = reason
        enriched["temporal_warning"] = warn
        by_id[rid] = enriched
        screened_rows.append({"id": rid, "weak_reason": reason})

    screen = screen_bulk_accept(screened_rows,
                                individually_named=individually_named)

    ctx = stakes_context(ws)
    opens_by_id: dict = {}
    already_watched: set = set()
    try:
        from cru_match import (_commitment_id, load_open_commitments,
                               split_pending_review)
        confirmed, _nr = split_pending_review(load_open_commitments(
            str(_events_path(ws)), workspace_root=str(ws)))
        opens_by_id = {_commitment_id(e): e for e in confirmed}
        already_watched = {cid for cid, e in opens_by_id.items()
                           if is_watched(e)}
    except Exception:
        opens_by_id = {}

    results: list[dict] = []
    n_confirmed = n_parked = n_failed = 0
    for rid in screen["accept"]:
        row = by_id.get(rid, {})
        cid = str(row.get("commitment_id") or rid)
        try:
            res = close_commitment(
                ws, cid, resolved_by=resolved_by,
                evidence=clip(row.get("evidence")) or
                DEFAULT_ORIGINAL_EVIDENCE,
                source_skill=source_skill, resolution="done",
                user_confirmed=True,
            )
        except (CommitmentIdError, OpenSubitemsError, PendingReviewError) as exc:
            results.append({"id": rid, "commitment_id": cid,
                            "status": "not_closed", "detail": str(exc)})
            n_failed += 1
            continue
        status = res.get("status")
        results.append({"id": rid, "commitment_id": cid, "status": status})
        if status == "closed":
            n_confirmed += 1

    for entry in screen["held"]:
        rid = entry["id"]
        row = by_id.get(rid, {})
        cid = str(row.get("commitment_id") or rid)
        target = opens_by_id.get(cid)
        stakes = (stakes_for(target, workspace_root=ws, now_iso=now,
                             context=ctx) if target is not None
                  else {"level": STAKES_LOW, "reasons": [], "rank": 0})
        watch = build_watch(
            watch_until=watch_until_iso(now, window), stakes=stakes,
            matched_ref=row.get("matched_ref"),
            match_score=row.get("match_score"),
            evidence=row.get("evidence") or "",
            reason=entry["reason"],
        )
        try:
            parked = park_in_watch(
                ws, cid, watch=watch, source_skill=source_skill,
                note="held from a bulk answer — watching instead",
                known_watched=already_watched)
        except Exception as exc:
            results.append({"id": rid, "commitment_id": cid,
                            "status": "not_parked", "detail": str(exc),
                            "reason": entry["reason"]})
            n_failed += 1
            continue
        status = parked.get("status")
        results.append({"id": rid, "commitment_id": cid, "status": status,
                        "reason": entry["reason"],
                        "temporal_warning": row.get("temporal_warning")})
        if status == "watching":
            already_watched.add(cid)
            n_parked += 1

    return {
        "results": results,
        "n_confirmed": n_confirmed,
        "n_held": screen["n_held"],
        "n_parked": n_parked,
        "n_failed": n_failed,
        "held": screen["held"],
        "ack": bulk_accept_ack(n_confirmed, screen["n_held"], parked=n_parked),
    }


def run_watch_expiry(workspace_root, *, resolved_by: str,
                     source_skill: str = SOURCE_SKILL, now_iso=None,
                     posture_level: Optional[int] = None,
                     ask_cap: Optional[int] = None) -> dict:
    """The expiry pass: read the watched set, route it, and write ONLY the
    assumed-done closes.

    The ask list is RETURNED, not written — a question is something the user
    answers on a surface, not an event this pass appends. And `carried` needs
    no bookkeeping at all: a carried row is simply still watched and still
    past its edge, so the next fire routes it again (and `rotate_ask` makes
    sure it is actually asked). That is what "never dropped" means here — the
    overflow is not stored anywhere it could be lost.

    FAILURE IS CONTAINED PER ITEM (WATCHGATE N-5). One close that raises used
    to abort the whole pass, which is the worst possible shape here: the items
    ahead of it are already CLOSED, and the ask list — the questions those
    closes were supposed to arrive beside — never reaches the caller at all.
    Writes without their questions is precisely the silence §0 forbids. So each
    close is attempted on its own; a failing item is counted, named by its id
    and its exception CLASS (never a message — an exception's text carries
    whatever the raiser put in it), and the pass continues.

    Returns {"assumed": [...], "ask": [...], "carried": [...], "results": [...],
             "n_assumed", "n_ask", "n_carried", "n_failed", "failures"}.
    `n_failed` is additive and 0 on every healthy pass, so a caller that does
    not read it behaves exactly as it did.
    """
    try:
        from confidence import posture_level as _posture, staffmeeting_ask_cap as _cap
    except Exception:  # pragma: no cover — shipped constants fallback
        def _posture(_ws=None):
            return POSTURE_BALANCED

        def _cap(_ws=None):
            return 5

    ws = Path(workspace_root)
    now = now_iso or _now_iso()
    level = int(posture_level if posture_level is not None else _posture(ws))
    cap = int(ask_cap if ask_cap is not None else _cap(ws))

    routed = route_expiry(load_watched(ws, now_iso=now), now_iso=now,
                          posture_level=level, ask_cap=cap)
    results: list[dict] = []
    failures: list[dict] = []
    for row in routed["assume"]:
        rid = str((row or {}).get("id") or "")
        try:
            results.append(close_as_assumed(
                ws, rid, resolved_by=resolved_by, source_skill=source_skill))
        except Exception as exc:  # noqa: BLE001 — see the docstring
            # The CLASS, never the message. A raiser's text may name the item,
            # and this value travels onto a persisted receipt (the
            # skipped_reasons lesson from CAPTUREFLOW's re-verify).
            failures.append({"id": rid, "error": type(exc).__name__})
            results.append({"status": "failed", "commitment_id": rid,
                            "error": type(exc).__name__})
    return {
        "assumed": routed["assume"], "ask": routed["ask"],
        "carried": routed["carried"], "not_due": routed["not_due"],
        "results": results,
        # n_assumed counts items ATTEMPTED on the assume route; a contained
        # per-item failure (N-5) is counted in n_failed, not subtracted here.
        "n_assumed": len(routed["assume"]), "n_ask": len(routed["ask"]),
        "n_carried": len(routed["carried"]),
        "n_failed": len(failures), "failures": failures,
        "posture_level": level, "ask_cap": cap,
    }


# ---------------------------------------------------------------------------
# `show watching` — the read-only view
# ---------------------------------------------------------------------------

WATCHING_EMPTY_TEXT = ("Nothing on watch — every guess has either proved "
                       "itself or been answered.")

_WATCHING_ACTIONS = ["confirm", "drop", "hold"]


def build_watching_view(workspace_root, *, now_iso: str | None = None) -> dict:
    """The `show watching` data view — PURE READ, shaped for
    `widget_transport.render_and_persist` (never a hand-built row).

    Each row says what it rests on and how long it has left, and carries the
    per-item verbs, because the whole promise of parking something is that the
    user can still reach in and answer it whenever they want.
    """
    now = now_iso or _now_iso()
    rows = load_watched(workspace_root, now_iso=now)
    items: list[dict] = []
    for i, r in enumerate(rows, start=1):
        left = days_left(r["watch_until"], now)
        bits = []
        if left is not None:
            bits.append("checking back today" if left <= 0
                        else ("1 day left" if left == 1 else f"{left} days left"))
        if (r["stakes"] or {}).get("level") == STAKES_HIGH:
            bits.append("worth asking about")
        bits.append(strength_line(r["watch"].get("reason") or "",
                                  evidence=r.get("evidence") or ""))
        items.append({
            "n": r["id"],
            "display_n": i,
            "name": r["title"],
            "context_tag": " · ".join(b for b in bits if b),
            "data": {"id": r["id"]},
            "actions": list(_WATCHING_ACTIONS),
        })
    total = len(items)
    noun = "item" if total == 1 else "items"
    view = {
        "source_skill": "needs-your-call",
        "header": f"Watching — {total} {noun} I'm still trying to prove",
        "sections": ([{"title": "ON WATCH", "count": total, "items": items}]
                     if items else []),
    }
    if not items:
        view["quick_read"] = WATCHING_EMPTY_TEXT
        view["pointer"] = WATCHING_EMPTY_TEXT
    return view


def render_watching_text(view: dict) -> str:
    """The view as the scannable text a caller can relay when no widget is in
    play (the transport is still the default — this is the fallback)."""
    sections = view.get("sections") or []
    if not sections:
        return WATCHING_EMPTY_TEXT
    lines = [view.get("header") or "Watching", ""]
    for section in sections:
        for row in section.get("items") or []:
            lines.append(f"  {row['display_n']}. {row['name']}")
            if row.get("context_tag"):
                lines.append(f"       {row['context_tag']}")
    lines.append("")
    lines.append("Say `confirm 2` to close one now, or `drop 2` to let it go. "
                 "Left alone, I keep looking.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["view", "view-json"])
    ap.add_argument("workspace")
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    args = ap.parse_args(argv)

    view = build_watching_view(args.workspace, now_iso=args.now)
    if args.command == "view-json":
        print(json.dumps(view, ensure_ascii=False))
    else:
        print(render_watching_text(view))
    return 0


__all__ = [
    "STRONG", "WEAK", "TITLE_MATCH_MARKER",
    "NO_EVIDENCE_REASON", "TITLE_MATCH_REASON", "NO_COMPLETION_REASON",
    "FUTURE_MEETING_REASON", "STALE_EVIDENCE_REASON",
    "STAMP_STRENGTH_FIELD", "STAMP_REASON_FIELD", "STAMP_BLOCKED_FIELD",
    "STAMP_FALLBACK_REASON", "BLOCKED_LEAD", "STAMPED_WEAK_LEAD",
    "stamped_strength", "stamped_strength_fields", "stamped_strength_note",
    "weakness_reason", "commitment_weak_reason", "evidence_strength",
    "strength_line", "temporal_warning",
    "screen_bulk_accept", "bulk_accept_ack",
    "WATCH_STATE_UNCONFIRMED", "CORROBORATION_KINDS", "ASSUMED_EVIDENCE",
    "build_watch", "watch_of", "is_watched", "park_in_watch", "clear_watch",
    "watched_commitment_ids", "watch_until_iso", "days_left",
    "STAKES_HIGH", "STAKES_LOW", "stakes_context", "stakes_for",
    "POSTURE_HANDLE_IT", "POSTURE_BALANCED", "POSTURE_CHECK_WITH_ME",
    "POSTURE_LEVELS", "POSTURE_NAMES", "FATE_ASK", "FATE_ASSUME",
    "expiry_fate", "route_expiry", "rotate_ask",
    "dual_evidence", "self_confirm", "close_as_assumed", "run_watch_expiry",
    "load_watched", "confirm_review_rows",
    "build_watching_view", "render_watching_text", "WATCHING_EMPTY_TEXT",
]


if __name__ == "__main__":
    raise SystemExit(main())
