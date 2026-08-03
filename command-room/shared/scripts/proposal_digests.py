#!/usr/bin/env python3
"""Evidence-class digests + the staff-meeting page bound (SPEC STAFFCUT).

WHY THIS EXISTS
The staff-meeting confirm queue was UNCAPPED. One fire measured 105 rows over
7 screens, and three kinds accounted for 93 of the 101 queue rows: 54
sent-match review rows, 33 identity rows, 6 org rows. The review rows were
worse than merely numerous — 34 of them rested on ONE IDENTICAL evidence line.
Fifty-four separate questions were being asked about sixteen pieces of
evidence, which is not fifty-four decisions; it is sixteen decisions rendered
fifty-four times, and answering them one by one is what turns a queue into a
rubber stamp.

WHAT THIS MODULE IS
  group_into_digests   ONE row per EVIDENCE CLASS, carrying the honest member
                       count, the shared evidence line, and every member's own
                       dispatch payload.
  expand_review_rows   the inverse — a digest row back to its members, so
                       dispatch is still PER ID.
  confirm_review_digest  the fence's new CALLER: expand, then hand the flat
                       batch to `watch_gate.confirm_review_rows`. There is no
                       second fence and no second policy here.
  bound_page           the render bound (§3.1) — the fold's shipped volume
                       guard applied to the queue lane.

TWO INVARIANTS, BOTH LOAD-BEARING
  1. **Grouping changes PRESENTATION, never ADJUDICATION.** Every member keeps
     its own id, its own verbs and its own resolution path; the digest row
     embeds them all (the shipped precedent is PID1's cluster rows — one click
     adjudicates the whole cluster because the row carries `cluster_seqs`).
     Nothing is resolved that a user did not gesture at, nothing auto-closes,
     and no threshold moves.
  2. **A grouped confirm IS a bulk gesture.** It routes through THE shared
     bulk-accept fence (`watch_gate.screen_bulk_accept`, reached via
     `confirm_review_rows`), so a digest whose members rest on nothing but a
     title match is HELD and PARKED exactly as `confirm all` would be. A
     digest id typed on its own does NOT name its members individually — the
     weak-evidence override belongs to a human reading THAT row and naming
     THAT number, and a digest is by construction not that.

WHAT DOES NOT DIGEST (deliberate)
  deal_creation / deal_update (37.5% decline — real judgment, worth its slot),
  person_merge (confirm forever, no reverser), person_link (the designed
  re-auto fence), the person rows that carry a possible match (those are the
  auto-widen lane's input, not filler), and the honest quarantine placeholders
  (a withheld row has one read-only verb and nothing to group with).

stdlib only.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# A digest row's wire id. Chosen so apply-choices can branch on it without a
# lookup, and so no proposal id can ever collide with one (`bp_`, `cru:`,
# `person:`, `org:` and `dont_forget:` are the shipped shapes).
DIGEST_ID_PREFIX = "digest:"

# Two members is the floor. A "digest" of one is just the row with its own
# verbs replaced by a narrower set — strictly worse than leaving it alone.
DIGEST_MIN_MEMBERS = 2

# How many member titles a digest row NAMES before it counts the remainder.
# The row has to be adjudicable from what it shows; it does not have to be the
# whole list (that is what expanding to the on-demand surfaces is for).
DIGEST_TITLE_SAMPLE = 4

# §3.1 — the staff-meeting page bound. The QUEUE lane's budget is this minus
# whatever the appended sections (the meeting fold, this week's moves) already
# occupy, so the number below is the whole page, not one section of it: ~21
# rows is two pages at `chat_output_renderer.DEFAULT_PAGE_SIZE` (15), which is
# the "about two screens" the audit's cut list was sized against.
#
# Same posture as the fold's own caps (`needs_review_queue.STAFF_GROUP_CAP` /
# `STAFF_ROW_CAP`): a module constant plus a function parameter. A cap bounds
# what ONE FIRE RENDERS; the projector still holds everything, the ranked front
# is what shows, and answering the front is what advances the rotation — so
# nothing can be suppressed forever. It is emphatically NOT a per-skill rail or
# a status row (the AUTOAPPLY dead-rail switch is workspace-GLOBAL and stays
# that way).
STAFF_PAGE_ROW_CAP = 21

_ADDR_SPACE_RE = re.compile(r"\s+")


def _norm(text) -> str:
    return _ADDR_SPACE_RE.sub(" ", str(text or "").strip()).lower()


def _action_ids(item: dict) -> list:
    out = []
    for t in item.get("action_tuples") or []:
        act = t.get("action") if isinstance(t, dict) else None
        if act:
            out.append(act)
    return out


# ---------------------------------------------------------------------------
# Which items digest, and by what key
# ---------------------------------------------------------------------------

CLASS_REVIEW = "commitment_review"
CLASS_PERSON_NAME_ONLY = "person_name_only"
CLASS_ENTITY_FACT = "entity_fact"

DIGEST_CLASSES = (CLASS_REVIEW, CLASS_PERSON_NAME_ONLY, CLASS_ENTITY_FACT)


def _is_name_only_person(item: dict) -> bool:
    """A person row with NOTHING on it but a name (§3.3).

    Four exclusions, each for its own reason:
      * `person_id` — an UPDATE row about someone already on file; existence is
        its premise and it is a different question;
      * `inferred_role` / `inferred_org` — there is context to decide on;
      * `match_name` / `match_person_id` — the "same as [existing]" shape, which
        is the auto-widen lane's input (§3.4), never filler;
      * a row whose verbs do not include `add person` — the honest quarantine
        placeholders carry one read-only verb and have nothing to group with.
    """
    if item.get("kind") != "person":
        return False
    if item.get("person_id") or item.get("inferred_role") \
            or item.get("inferred_org"):
        return False
    if item.get("match_name") or item.get("match_person_id"):
        return False
    return "add person" in _action_ids(item)


def digest_class(item: dict):
    """The digest class this queue item belongs to, or None when it renders as
    its own row (the explicit keeps)."""
    kind = item.get("kind")
    if kind == "commitment_review":
        return CLASS_REVIEW
    if kind == "entity_fact":
        return CLASS_ENTITY_FACT
    if _is_name_only_person(item):
        return CLASS_PERSON_NAME_ONLY
    return None


def digest_key(item: dict):
    """The grouping key inside a class, or None when the item does not digest.

    For review rows the key IS THE EVIDENCE. Rows whose capture recorded no
    evidence group together under one honest key: "no evidence recorded" is an
    evidence class too, and it is the one the shared fence holds in bulk.
    """
    cls = digest_class(item)
    if cls is None:
        return None
    if cls == CLASS_REVIEW:
        return f"{cls}|{_norm(item.get('evidence')) or '(no evidence recorded)'}"
    # The identity and fact lanes group by CLASS, not by text: 18 unrelated
    # names are one "who are these people" question, and 3 unadjudicated facts
    # are one "worth saving?" question. Splitting either by text would just
    # rebuild the queue it is replacing.
    return f"{cls}|all"


def _digest_id(key: str) -> str:
    return (DIGEST_ID_PREFIX
            + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12])


def is_digest_id(value) -> bool:
    return isinstance(value, str) and value.startswith(DIGEST_ID_PREFIX)


# ---------------------------------------------------------------------------
# Member payloads — the per-id dispatch contract
# ---------------------------------------------------------------------------

# The keys `watch_gate.confirm_review_rows` reads off a review row. Copied
# VERBATIM off the queue item — every one of them is already there, and
# deriving any of them here would be a second opinion about evidence.
_REVIEW_MEMBER_KEYS = ("commitment_id", "evidence", "match_score",
                       "has_completion_signal", "evidence_ts", "promise_ts",
                       "strength", "weak_reason")
# PID1 fan-out keys — one click adjudicates every underlying proposal.
_PERSON_MEMBER_KEYS = ("cluster_seqs", "cluster_fingerprints", "name")


def _member(item: dict, cls: str) -> dict:
    out = {"id": item["id"], "kind": item.get("kind"),
           "title": (item.get("title") or "").strip(),
           "actions": _action_ids(item)}
    keys = ()
    if cls == CLASS_REVIEW:
        keys = _REVIEW_MEMBER_KEYS
    elif cls == CLASS_PERSON_NAME_ONLY:
        keys = _PERSON_MEMBER_KEYS
    for k in keys:
        if item.get(k) is not None:
            out[k] = item[k]
    return out


# ---------------------------------------------------------------------------
# Row copy
# ---------------------------------------------------------------------------

def _compact_titles(titles, *, sample: int = DIGEST_TITLE_SAMPLE) -> str:
    """A few member titles, then an honest count of the rest. Never a
    fabricated "etc." — the remainder is a number the user can trust."""
    named = [t for t in titles if t][:sample]
    rest = max(0, len([t for t in titles if t]) - len(named))
    if not named:
        return ""
    text = ", ".join(named)
    return f"{text} +{rest} more" if rest else text


def _review_digest(key: str, members: list, items: list) -> dict:
    # The SAME line builder the single rows use — imported, never copied, so a
    # grouped row and a lone row can never describe the same evidence two ways.
    from brain_proposals import _cru_render_line

    n = len(members)
    evidence = ""
    weak = ""
    for it in items:
        evidence = evidence or (it.get("evidence") or "").strip()
        weak = weak or (it.get("weak_reason") or "").strip()
    render_evidence = evidence or "matched an outbound send"
    line = _cru_render_line("", render_evidence, weak)
    titles = _compact_titles([it.get("title") for it in items])
    if titles:
        line = f"{line} · {titles}"
    noun = "promise" if n == 1 else "promises"
    return {
        "title": f"{n} {noun} matched to the same message",
        "render_line": line,
        "shape": "hygiene",
        "action_tuples": [{"action": "confirm"}, {"action": "not relevant"},
                          {"action": "hold"}],
        "evidence": evidence,
        "weak_reason": weak,
        "strength": ("weak" if weak else "strong"),
    }


def _person_digest(key: str, members: list, items: list) -> dict:
    n = len(members)
    names = _compact_titles([it.get("title") for it in items])
    noun = "name" if n == 1 else "names"
    line = ("mentioned by name only · "
            f"{names} · no contact record yet — name one to add it")
    return {
        "title": f"{n} new {noun} from your calls",
        "render_line": line,
        "shape": "identity",
        # NOT `add person`. Adding N contacts from one click would be exactly
        # the silent-creation creep this build exists to prevent — 18 records
        # off one gesture is not a decision anyone made. The row's copy says
        # how to add one: name it, and its own row dispatches.
        "action_tuples": [{"action": "proposal not relevant"},
                          {"action": "snooze proposal 7d"}],
        "evidence": "",
    }


def _fact_digest(key: str, members: list, items: list) -> dict:
    """The fact digest KEEPS its confirm verb, where the person digest refuses
    one. The asymmetry is deliberate and it rests on reversibility, not on
    convenience.

    Confirming a fact appends an additive `*_fact_observed` event, and the
    registered `entity_fact_structured` reverser retracts it by appending
    `entity_fact_retracted` — the renderers do the forgetting. Nothing outside
    the history view changes, and a wrong one costs a retraction. So fanning one
    click across three facts is a decision a person can actually make and
    actually take back.

    Creating a person is not that. `add person` mints a RECORD that every other
    surface then resolves against, and reversing it is an archive flip that
    leaves the record and its provenance on file forever. Eighteen records off
    one gesture is not a decision anyone made — it is the silent-creation creep
    this build exists to prevent — so `_person_digest` ships without the verb
    and tells the user how to add one.

    Same rule stated once: a digest may fan out a verb whose mistake is cheap to
    undo. It may never fan out one whose mistake is a new record."""
    n = len(members)
    facts = _compact_titles([
        (str(it.get("fact") or "").strip() or (it.get("title") or "").strip())
        for it in items])
    noun = "fact" if n == 1 else "facts"
    return {
        "title": f"{n} {noun} from connected sources",
        "render_line": f"{facts} · confirm to save them to history",
        "shape": "hygiene",
        "action_tuples": [{"action": "confirm proposal"},
                          {"action": "dismiss proposal"},
                          {"action": "snooze proposal 7d"}],
        "evidence": "",
    }


_BUILDERS = {CLASS_REVIEW: _review_digest,
             CLASS_PERSON_NAME_ONLY: _person_digest,
             CLASS_ENTITY_FACT: _fact_digest}


# ---------------------------------------------------------------------------
# The grouping pass
# ---------------------------------------------------------------------------

def group_into_digests(items, *, min_members: int = DIGEST_MIN_MEMBERS):
    """Fold a ranked queue into evidence-class digests.

    Returns `(rows, stats)`:
      rows   the queue with each qualifying group replaced by ONE digest item.
             A digest item is projector-shaped (it flows through
             `rank_proposals` and `build_card_view` unchanged) and additionally
             carries `digest_class`, `digest_key`, `digest_count` and
             `digest_members` — the per-id dispatch payloads.
      stats  {"rows": int, "items_represented": int, "by_class": {...},
              "grouped_items": int, "digest_rows": int} — the honest arithmetic
             the D2 receipt records so a later audit can measure both numbers.

    Order: a digest lands where its OLDEST member ranked (its `opened_at` is
    that member's), so the ranked-front rotation is unchanged. Groups below
    `min_members` pass through untouched, verbs and all.
    """
    items = list(items or [])
    buckets: dict = {}
    order: list = []
    for it in items:
        key = digest_key(it)
        if key is None:
            order.append(("row", it))
            continue
        if key not in buckets:
            buckets[key] = []
            order.append(("group", key))
        buckets[key].append(it)

    out: list = []
    by_class: dict = {}
    grouped_items = 0
    digest_rows = 0
    for kind, payload in order:
        if kind == "row":
            out.append(payload)
            continue
        group = buckets[payload]
        if len(group) < max(1, int(min_members)):
            out.extend(group)
            continue
        cls = digest_class(group[0])
        members = [_member(it, cls) for it in group]
        base = _BUILDERS[cls](payload, members, group)
        opened = sorted((it.get("opened_at") or "") for it in group
                        if it.get("opened_at"))
        row = {
            "id": _digest_id(payload),
            "source_family": group[0].get("source_family") or cls,
            "kind": group[0].get("kind"),
            "tier": "confirm",
            "fingerprint": _digest_id(payload),
            "opened_at": opened[0] if opened else "",
            "expires_at": "",
            "detector": group[0].get("detector") or "unknown",
            "seq": None,
            "digest_class": cls,
            "digest_key": payload,
            "digest_count": len(members),
            "digest_members": members,
        }
        row.update(base)
        out.append(row)
        by_class[cls] = by_class.get(cls, 0) + 1
        grouped_items += len(members)
        digest_rows += 1

    stats = {"rows": len(out), "items_represented": len(items),
             "by_class": by_class, "grouped_items": grouped_items,
             "digest_rows": digest_rows}
    return out, stats


# THE TWO SHAPES A DISPATCH BATCH ARRIVES IN, and why both have to be read.
#
# apply-choices dispatches from a PERSISTED WIDGET (stateless `src` dispatch),
# so a row can reach these functions as a RENDERED CARD ROW — wire id at `n`,
# target ids under `data` — or as a PROJECTOR/FENCE ROW the dispatcher built by
# copying keys off `load_open_proposals` (wire id at `id`). A real batch mixes
# them: the dispatcher builds fence rows for ordinary review rows and reads
# `data.digest_members` off the widget row for a digest.
#
# THE SUPPORT WAS BUILT IN TWO GO-ROUNDS, and the first one was half of it.
#
# Round one taught `digest_members` to look under `data` and left the id read
# alone, so a mixed batch of one digest of three plus one lone widget row
# expanded to THREE: the lone commitment never closed, and the ack under-
# reported with nothing to explain it. A helper that silently drops an answer
# the user gave is worse than one that refuses it out loud.
#
# Round one's FIX was itself half of a fix, and the missing half wrote to the
# substrate. Coalescing the id for the EXPANSION was not enough, because
# `watch_gate` reads `id` / `commitment_id` off the top level only: the lone row
# reached the park leg with neither, and parking wrote `commitment_updated` with
# `commitment_id: ""` into an append-only log, permanently, with only a
# validator warning. The ack then said "answer one by its own number" for a
# number that did not exist, and the real commitment came back next fire.
#
# So the shapes are made EQUIVALENT rather than merely both-readable:
#   * `brain_proposals._row_target_ids` embeds a review row's fence inputs in
#     the widget payload (the digest members always carried them);
#   * `_normalize_candidate` lifts them to the top level, additively, only when
#     absent, so a fence row passes through as the same object;
#   * `park_in_watch` refuses an empty id, so this class cannot reach the log
#     again by some other road.

def row_wire_id(row: dict) -> str:
    """The row's wire id, whichever shape it arrived in: `id` (projector /
    fence row), `data.id` or `n` (rendered card row), or `commitment_id` as the
    last resort. "" when the row carries no identity at all — the ONE case a
    caller may drop, because there is nothing to dispatch against."""
    if not isinstance(row, dict):
        return ""
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    for candidate in (row.get("id"), data.get("id"), row.get("n"),
                      row.get("commitment_id"), data.get("commitment_id")):
        if candidate not in (None, ""):
            return str(candidate)
    return ""


def digest_members(row: dict) -> list:
    """The member payloads on a digest row, or [] for an ordinary row. Reads
    both the projector item (`digest_members`) and the rendered card row
    (`data.digest_members`) — see the shape note above: the widget is one of
    the two shapes a real dispatch batch arrives in."""
    if not isinstance(row, dict):
        return []
    members = row.get("digest_members")
    if members is None:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        members = data.get("digest_members")
    return list(members or [])


def _normalize_candidate(cand: dict) -> dict:
    """A dispatch row with `id` and `commitment_id` present at the TOP LEVEL,
    where every downstream reader looks for them.

    ADDITIVE AND ONLY WHEN ABSENT. A row that already carries both is returned
    as the SAME OBJECT, so a fence row the dispatcher built keeps every key it
    copied off the projector, byte for byte.

    This is the other half of reading both shapes, and skipping it was not a
    cosmetic gap. `watch_gate` reads `id` / `commitment_id` off the top level
    only, so a rendered card row reached the park leg with NEITHER — and parking
    wrote `commitment_updated` with `commitment_id: ""` into an append-only log,
    permanently. The payload validator warned and did not block. Downstream: the
    ack told the user to "answer one by its own number" for a number that did
    not exist, and the real commitment, never parked, came back on the next
    fire. Nothing was wrongly CLOSED only because `close_commitment` validates
    its id (see NF-3 — `park_in_watch` now does too).

    The data was already on the row: `_row_target_ids` embeds `commitment_id`
    verbatim in the widget payload. This just puts it where readers look.
    """
    data = cand.get("data") if isinstance(cand.get("data"), dict) else {}
    rid = row_wire_id(cand)
    missing = [k for k in _REVIEW_MEMBER_KEYS
               if cand.get(k) is None and data.get(k) is not None]
    if (cand.get("id") or "") == rid and not missing:
        return cand
    out = dict(cand)
    if not out.get("id") and rid:
        out["id"] = rid
    for k in missing:
        out[k] = data[k]
    return out


# The status a memberless digest row is REPORTED under (NF-2). Never written,
# never treated as a commitment.
DIGEST_MEMBERS_MISSING = "digest_members_missing"


def expand_review_batch(rows) -> dict:
    """Expand a dispatch batch, and REPORT what could not be expanded.

    Returns `{"rows": [...], "refused": [{"id", "status", "detail"}, ...]}`.

    Two things a caller has to know about, and one of them used to be silent:

      * every digest row becomes its MEMBERS, normalized so `id` and
        `commitment_id` sit at the top level whichever shape the row arrived in
        (`_normalize_candidate`);
      * a digest row that arrives WITHOUT its members is REFUSED and NAMED —
        never expanded to itself. A `digest:` id is not a commitment id, and a
        memberless digest row used to become one: it expanded to itself, reached
        the fence with no ids at all, and parked a watch under an empty
        commitment id. `strip_digest_ids` guarded the weak-evidence override and
        nothing guarded the ROWS. This is reachable without any bug of ours — a
        stale frozen page-set, or the plugin-update partial-write truncation
        class, is enough to hand back a row whose payload has been cut.

    Ordinary rows pass through; duplicates by wire id collapse to ONE entry,
    keeping the first position but preferring the RICHER candidate (the one
    carrying `evidence`). Position-stable and content-decided, so a batch that
    happens to carry both shapes of one row gets the same answer whichever
    order they arrive in — first-seen-wins made `[widget, fence]` hold a row
    that `[fence, widget]` closed.
    """
    out: list = []
    refused: list = []
    index: dict = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        members = digest_members(row)
        if not members and is_digest_id(row_wire_id(row)):
            refused.append({
                "id": row_wire_id(row),
                "status": DIGEST_MEMBERS_MISSING,
                "detail": "this grouped row arrived without its member ids — "
                          "nothing was answered for it; re-run the surface to "
                          "get a fresh page",
            })
            continue
        for cand in (members if members else [row]):
            if not isinstance(cand, dict):
                continue
            cand = _normalize_candidate(cand)
            rid = row_wire_id(cand)
            if not rid:
                continue
            if rid in index:
                prev = out[index[rid]]
                if not str(prev.get("evidence") or "").strip() \
                        and str(cand.get("evidence") or "").strip():
                    out[index[rid]] = cand
                continue
            index[rid] = len(out)
            out.append(cand)
    return {"rows": out, "refused": refused}


def expand_review_rows(rows) -> list:
    """The rows half of `expand_review_batch` — every digest replaced by its
    members, de-duplicated, normalized. Memberless digest rows are absent from
    this list; use `expand_review_batch` when the caller has to report them."""
    return expand_review_batch(rows)["rows"]


def strip_digest_ids(individually_named) -> list:
    """`individually_named` with every DIGEST id removed.

    A digest id typed on its own names a CLASS, not a row. The weak-evidence
    override exists for a human who read one row and named that row's number;
    letting a digest id populate it would hand `confirm all` semantics to a
    single keystroke, which is the incident class the fence was built for.
    """
    return [x for x in (individually_named or ()) if not is_digest_id(str(x))]


def confirm_review_digest(workspace_root, rows, *, resolved_by: str,
                          individually_named=(), now_iso=None,
                          source_skill: str = "apply-choices",
                          watch_window_days=None) -> dict:
    """Confirm a review batch that MAY contain digest rows.

    This is a CALLER of the shared bulk-accept fence, not a second fence: it
    expands the digests to their members, drops digest ids from the
    weak-evidence override, and hands the flat batch to
    `watch_gate.confirm_review_rows` — the same function the ungrouped batch
    dispatch has always called, with the same screening, the same parking and
    the same ack. Grouping a row can therefore never change what happens when
    it is confirmed.

    A row the expansion REFUSED (a digest that arrived without its members —
    see `expand_review_batch`) rides back on `refused` / `n_refused` and as an
    entry in `results`, so the ack can name it. It is never written, and never
    treated as a commitment.
    """
    from watch_gate import confirm_review_rows

    batch = expand_review_batch(rows)
    out = confirm_review_rows(
        workspace_root, batch["rows"],
        resolved_by=resolved_by,
        individually_named=strip_digest_ids(individually_named),
        now_iso=now_iso, source_skill=source_skill,
        watch_window_days=watch_window_days)
    if batch["refused"]:
        out = dict(out)
        out["results"] = list(out.get("results") or []) + list(batch["refused"])
        out["refused"] = list(batch["refused"])
        out["n_refused"] = len(batch["refused"])
    return out


# ---------------------------------------------------------------------------
# §3.1 — the page bound
# ---------------------------------------------------------------------------

# Rank order of the shapes, for the bound's allocation and its notes. Mirrors
# `brain_proposals._SHAPE_RANK` — money first, objectives last.
_SHAPE_ORDER = ("money", "identity", "hygiene", "objective")


def bound_page(items, *, n_extra_rows: int = 0,
               page_cap: int = STAFF_PAGE_ROW_CAP):
    """Bound what ONE FIRE RENDERS from the queue lane.

    `n_extra_rows` is what the appended sections already occupy (the meeting
    fold, this week's moves). The bound is the PAGE, so the queue's budget is
    whatever is left of it — at least one row, always, so a page full of extras
    can never silence the queue entirely.

    ALLOCATION IS PER SHAPE, and that is not a refinement — it is the fix for
    how the first version of this bound behaved. `items` arrives ranked (money >
    identity > hygiene, then oldest first), so a plain `items[:budget]` truncates
    the ranked TAIL, and on the audit-day mix that meant a page of 1 money row
    and 16 identity rows with the ENTIRE hygiene lane — every one of the review
    digests this build exists to create — cut. "Nothing is suppressed forever"
    was still technically true, and the page was still useless.
    So the budget is filled a slot at a time, always giving the next slot to the
    shape with the fewest so far (ties broken by rank), which converges on an
    even split and lets an underfull lane hand its unused slots to the others.
    A shape that has rows always gets at least one.

    Within a shape the ranked order is untouched, so the front of each lane is
    what shows and answering it is what advances that lane's rotation — the
    meeting fold's own argument, applied per lane instead of once globally.

    Returns `(kept, stats)`. `stats`:
      {"cap", "budget", "shown", "total", "dropped", "extra_rows",
       "by_shape": {shape: {"shown", "total", "dropped"}}}
    `dropped` counts are the honest remainder the caller renders in the section
    notes; the projector still holds every one of them.
    """
    items = list(items or [])
    cap = max(1, int(page_cap))
    budget = max(1, cap - max(0, int(n_extra_rows)))

    lanes: dict = {}
    for it in items:
        lanes.setdefault(it.get("shape") or "hygiene", []).append(it)
    present = [s for s in _SHAPE_ORDER if s in lanes]
    present += [s for s in lanes if s not in _SHAPE_ORDER]  # unknown shapes last

    quota = {s: 0 for s in present}
    remaining = budget
    while remaining > 0:
        # The hungriest lane that still has rows; rank order breaks ties.
        candidates = [s for s in present if quota[s] < len(lanes[s])]
        if not candidates:
            break
        pick = min(candidates, key=lambda s: (quota[s], present.index(s)))
        quota[pick] += 1
        remaining -= 1

    # Walk the ranked list ONCE and take each lane's first `quota` rows. (Not
    # `it in lane` / `lane.index(it)`: these are dicts, so `==` would match two
    # equal-but-distinct rows and mis-place both.)
    taken = {s: 0 for s in present}
    kept: list = []
    for it in items:
        shape = it.get("shape") or "hygiene"
        if taken.get(shape, 0) < quota.get(shape, 0):
            taken[shape] = taken.get(shape, 0) + 1
            kept.append(it)

    by_shape = {s: {"shown": min(quota[s], len(lanes[s])),
                    "total": len(lanes[s]),
                    "dropped": max(0, len(lanes[s]) - quota[s])}
                for s in present}
    stats = {"cap": cap, "budget": budget, "shown": len(kept),
             "total": len(items), "dropped": max(0, len(items) - len(kept)),
             "extra_rows": max(0, int(n_extra_rows)), "by_shape": by_shape}
    return kept, stats


BOUND_NOTE = ("showing the front {shown} of {total} — the rest stay queued "
              "and lead the next one")
DIGEST_NOTE = "{items} items grouped into {rows} rows"
# A lane the bound allocated ZERO slots to renders no section at all, so it has
# no title of its own to be honest in. Its count rides the LAST rendered
# section instead — because the alternative is that it vanishes, and a queue
# that silently loses a whole shape is the defect the honest-totals rule exists
# to prevent (the fold's rule, all the way up).
VANISHED_NOTE = "also queued: {lanes} — they lead the next one"
_SHAPE_LANE_LABEL = {"money": "money", "identity": "identity",
                     "hygiene": "housekeeping", "objective": "objectives"}


def section_notes(items, bound) -> dict:
    """Per-shape title suffixes for `build_card_view(section_notes=...)`.

    Three honesties, in the fold's own idiom:
      * how many ITEMS the rows of this shape represent (the digest arithmetic);
      * how many rows of this shape the bound left off (per shape, so the
        pointer sits under the rows it is about — a bound that trimmed identity
        must not report itself under hygiene);
      * and, on the LAST rendered shape, any lane the bound cut ENTIRELY.

    That third clause is not a nicety. `bound["by_shape"]` holds the truth for
    every lane, but a lane with zero rows renders no section — so a page whose
    budget was eaten by the appended sections (measured: 18 extra rows leaves a
    budget of 3) dropped an entire shape with no count anywhere and no pointer.
    The rows were still queued and the page said nothing about them, which is
    indistinguishable from having lost them.

    The full numbers live here rather than in the header because the header
    count must keep equalling the rows the widget SHOWS (RV-4).
    """
    per_shape_items: dict = {}
    per_shape_rows: dict = {}
    for it in items:
        shape = it.get("shape") or "hygiene"
        per_shape_rows[shape] = per_shape_rows.get(shape, 0) + 1
        per_shape_items[shape] = per_shape_items.get(shape, 0) + max(
            1, int(it.get("digest_count") or 1))
    by_shape = (bound or {}).get("by_shape") or {}
    notes: dict = {}
    for shape, n_rows in per_shape_rows.items():
        bits = []
        if per_shape_items.get(shape, n_rows) > n_rows:
            bits.append(DIGEST_NOTE.format(items=per_shape_items[shape],
                                           rows=n_rows))
        lane = by_shape.get(shape) or {}
        if lane.get("dropped"):
            bits.append(BOUND_NOTE.format(shown=lane["shown"],
                                          total=lane["total"]))
        notes[shape] = " · ".join(b for b in bits if b)

    # Lanes the bound zeroed out, in rank order, reported on the last shape that
    # DID render (the bound guarantees a budget of at least 1, so whenever the
    # queue is non-empty at least one shape renders and there is always
    # somewhere honest to put this).
    vanished = [
        f"{lane['total']} {_SHAPE_LANE_LABEL.get(shape, shape)}"
        for shape in _SHAPE_ORDER
        for lane in [by_shape.get(shape) or {}]
        if lane.get("total") and not lane.get("shown")
        and shape not in per_shape_rows
    ]
    if vanished:
        rendered = [s for s in _SHAPE_ORDER if s in per_shape_rows]
        if rendered:
            host = rendered[-1]
            note = VANISHED_NOTE.format(lanes=", ".join(vanished))
            notes[host] = f"{notes[host]} · {note}" if notes[host] else note
    return {k: v for k, v in notes.items() if v}


__all__ = [
    "DIGEST_ID_PREFIX",
    "DIGEST_MIN_MEMBERS",
    "DIGEST_CLASSES",
    "CLASS_REVIEW",
    "CLASS_PERSON_NAME_ONLY",
    "CLASS_ENTITY_FACT",
    "STAFF_PAGE_ROW_CAP",
    "BOUND_NOTE",
    "DIGEST_NOTE",
    "VANISHED_NOTE",
    "digest_class",
    "digest_key",
    "is_digest_id",
    "row_wire_id",
    "group_into_digests",
    "digest_members",
    "expand_review_rows",
    "expand_review_batch",
    "DIGEST_MEMBERS_MISSING",
    "strip_digest_ids",
    "confirm_review_digest",
    "bound_page",
    "section_notes",
]
