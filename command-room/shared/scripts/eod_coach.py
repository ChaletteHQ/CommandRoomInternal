#!/usr/bin/env python3
"""SPEC EODCOACH2 — the close coaches from the record.

M's ruling (2026-08-25), after seeing the full-build sample: two prose
layers survive, on top of `eod_synthesis`'s arc read — Layer 1 (pattern
memory across closes) and Layer 2 (the intent-vs-outcome delta plus one push
line). Layer 3 (a curated action strip) is PARKED — it would have collided
with M's own one-interaction ruling (SPEC EODSYNTH1 R-2), and this build does
not build it.

THE RULINGS (§0, binding)
--------------------------
  1. PROSE ONLY, ZERO NEW INTERACTIONS. The tomorrow block stays the one
     interaction (EODSYNTH1's contract, EODARC1's fence, unchanged). Nothing
     in this module renders a verb, a checkbox, or a proposal — a sentence
     built here carries `{text, refs, tier, kind}` and nothing else, exactly
     the shape `eod_synthesis.sentence` mints.
  2. THE COACH COUNTS THE RECORD; IT NEVER JUDGES FROM TASTE. Every sentence
     below is a count over events, packs, and `day_intent` records already on
     disk. No sentiment, no scores (`eod_synthesis.assert_no_score` runs over
     this module's own composed text too), no "should".
  3. HARD CAPS. At most `CAP_PATTERNS` (2) pattern sentences, `CAP_DELTA` (1)
     delta sentence, `CAP_PUSH` (1) push line, per close. Overflow is
     silently dropped by strength: most repetitions first, then the oldest
     consequence.
  4. HONEST ABSENCE, EVERYWHERE. No STATED `day_intent` for the day being
     closed → no delta sentence, never inferred from prose. Fewer than
     `MIN_PRIOR_PACKS` (3) prior packs on disk → the pattern layer renders
     NOTHING rather than a thin pattern. A fresh workspace coaches nothing
     and says nothing about it.

LAYER 1 — PATTERN MEMORY ACROSS CLOSES
---------------------------------------
Reads the last `MAX_PRIOR_PACKS` (7) EOD pack records OFF DISK — the audit
copies `surface_drivers.build_end_of_day_pack` already writes to
`_hq/.system/briefs/end-of-day-pack-*.json` on every prior fire — and counts,
never infers:

  * **repeat-stillness** — an arc (EODARC1's own arc set, including the
    minted commitment arcs) unmoved across >= 3 CONSECUTIVE closes while
    still carrying a stated consequence. Evidence: the `unmoved`-kind
    sentences EODARC1's arc read already composed and persisted on each
    pack's own `what_it_meant` block — this module reads that, and derives
    nothing from raw rows a second time.
  * **repeat-mention-without-work** — a commitment recurring in the
    meeting-gated slip (`eod_synthesis.CONSEQUENCE_MEETING`) on >= 3 of the
    days examined, with no close in between. Evidence: `slipped_prose`'s own
    persisted sentences.
  * **survival count** — on the single OLDEST consequence-carrying overdue
    item (the longest-running date consequence among today's own open
    rows): how many consecutive closes it has now survived un-closed.

Every candidate is grounded (it carries the refs the sentence it read
already carried) and counted, not composed from scratch. THE CAP: candidates
are deduped by identity first (the same commitment or arc surfacing under
two detectors is one fact, not two sentences — the stronger detector wins),
then sorted by strength (repetitions, then the older item), then capped to
`CAP_PATTERNS`.

LAYER 2 — THE INTENT-VS-OUTCOME DELTA, AND THE ONE PUSH LINE
--------------------------------------------------------------
`compute_intent_delta` reads the day's own STATED `day_intent` (written by
the prior close's tomorrow-block tap, per EODFIX1's id linkage) and compares
its top-ranked item against today's own closures / open book — grounded
strictly through the item's `commitment_id`; an item with none renders
nothing rather than a guess from its own words. At most one sentence: the
honest positive ("...it shipped") when the item's commitment closed today,
or the honest miss ("...it didn't move") when it is still open.

`compute_push` is the one push line, and it renders ONLY when Layer 1 kept
at least one pattern — the push is never a fact this module invents on its
own, it is the strongest kept pattern's own count, restated as a fact plus
its concrete cost (evenings spent, meetings spent, days overdue — never an
imperative, never a to-do).

THE REPETITION FENCE (anti-nag)
--------------------------------
A coach that says the same push sentence nightly is a nag, and the fix is
NOT a second store to remember having said it — it is reading the ONE prior
pack's own `coach.push_state`, which is where this module wrote it last
time. `compute_push`'s state machine:

  cooldown > 0    → the NAMED identity is silent while its clock ticks down;
                    a DIFFERENT pattern's push is fresh signal and renders
                    (REVIEW_PR63 F-1 — the cooldown is per-push, not a mute
                    on the coach).
  no candidate    → silent; the active streak resets (an ended pattern ends
                    its push); a running cooldown keeps ticking.
  same identity   → the streak continues from the prior state (frozen, not
                    reset, across its own cooldown).
  new identity    → the streak restarts at 1.
  streak < 3      → renders normally.
  streak >= 3     → renders once more, NAMING the repetition at its ACTUAL
                    ordinal — "third", "fourth", … (REVIEW_PR63 F-2: the
                    module counts the record, so the naming form may never
                    say "third" about a fourth) — and starts a 3-close
                    cooldown bound to that identity.

`build_coach` persists the resulting state on the pack it returns
(`coach["push_state"]`), and `surface_drivers.build_end_of_day_pack` writes
that pack to disk exactly as it always has — no new store, the ruling
measured in code.

stdlib + `eod_synthesis` + `day_intent` only. No connector I/O; every read is
off this workspace's own disk.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional

import eod_synthesis as syn

# ---------------------------------------------------------------------------
# The pack key. NOT a member of `end_of_day.RENDER_ORDER` / `BLOCK_ORDER` /
# `COMPUTED_ONLY` / `NUMBERED_BLOCKS` / `eod_synthesis.SYNTHESIS_BLOCKS` —
# every one of those is a tuple EODARC1 and EODSYNTH1 pin by exact equality,
# and this build does not touch a single one of them (the zero-new-
# interactions pin, extended rather than duplicated). `coach` rides the pack
# the same way `catchup` already does: a key the fire renders, placed by
# instruction rather than by membership in the render-order tuple.
BLOCK_COACH = "coach"

# ---------------------------------------------------------------------------
# The caps (§0 ruling 3)
# ---------------------------------------------------------------------------
CAP_PATTERNS = 2
CAP_DELTA = 1
CAP_PUSH = 1

# Honest absence (§0 ruling 4): fewer prior packs than this and Layer 1
# renders nothing at all, rather than a pattern built on thin evidence.
MIN_PRIOR_PACKS = 3
MAX_PRIOR_PACKS = 7

# The detector thresholds. Each is the spec's own ">= 3" except survival,
# which the spec states as a plain count with no floor named; 2 is chosen so
# a commitment does not "survive" its own first evening (every open row
# trivially "survives" the close it was opened on).
STILLNESS_MIN_STREAK = 3
MENTION_MIN_COUNT = 3
SURVIVAL_MIN_STREAK = 2

# The repetition fence (§0's anti-nag rule).
NAMING_STREAK = 3
COOLDOWN_CLOSES = 3

# Deterministic tie-break when two detectors both name the same identity at
# the same strength — lower wins. Stillness is the richest read (it already
# carries the arc's own "did not move" sentence), so it is preferred.
TEMPLATE_PRIORITY = {"stillness": 0, "mention": 1, "survival": 2}

PACKS_DIRNAME = ("_hq", ".system", "briefs")
PACK_GLOB = "end-of-day-pack-*.json"

# ---------------------------------------------------------------------------
# Templates. Counted claims only — no template below carries a denominator
# against a PLAN (the shape `eod_synthesis.SCORE_PATTERNS` bans); every
# number in them is a count of REPETITIONS, which is exactly what R-1 leaves
# room for.
# ---------------------------------------------------------------------------

T_STILLNESS = "That's the {ordinal} consecutive close where {label} sat still."
T_MENTION = ("{label} has come up in meetings on {count} of the last "
             "{of_days} days without a send.")
T_SURVIVAL = "{label} has now survived {n} closes."

DELTA_NEG = "You said tomorrow was about {x}. It didn't move."
DELTA_POS = "You said tomorrow was about {x} — it shipped."

PUSH_STILLNESS = ("{label} has now sat still for {streak} consecutive "
                   "closes — {streak} evenings the plan did not move, not "
                   "one delay.")
PUSH_MENTION = ("{label} has come up in meetings on {count} of the last "
                 "{of_days} days with no send behind it — {count} "
                 "conversations about work that has not happened.")
PUSH_SURVIVAL = ("{label} has now survived {n} closes past its due date — "
                  "{n} evenings it stayed exactly where it was.")

# The naming form (the fence's third render). It says plainly that this is a
# repeat, which is the whole point of naming it, and it says what happens
# next — never an imperative about the ROW, only a statement about the
# COACH's own behavior, which is the one thing this module controls.
PUSH_NAMED_PREFIX = ("This is the {ordinal} close running I've raised this, so "
                      "I'll say it once more and then hold off: ")


def _ref_set(refs) -> frozenset:
    """A sentence's refs, normalized to a frozenset for identity comparison.

    SPEC COACHONE1 §0 ruling 1 — dedup at the render chokepoint keyed on the
    insight's REF SET, never by fuzzy text match. `push`'s candidate is
    always `patterns_kept[0]` itself (see `compute_push`), so when the push
    renders, its `refs` and that pattern's `refs` are literally the SAME
    list drawn from the SAME candidate dict — comparing the two as sets is
    exact identity, not a heuristic."""
    return frozenset(str(r) for r in (refs or []) if str(r or "").strip())


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# Reading the last N packs off disk (Layer 1's only source)
# ---------------------------------------------------------------------------

def _packs_dir(workspace_root) -> Path:
    p = Path(workspace_root)
    for part in PACKS_DIRNAME:
        p = p / part
    return p


def read_prior_packs(workspace_root, *, before_for_date: Optional[str] = None,
                      limit: int = MAX_PRIOR_PACKS) -> List[dict]:
    """The last `limit` EOD pack records on disk, NEWEST FIRST.

    Reads `_hq/.system/briefs/end-of-day-pack-*.json` — the audit copies
    `surface_drivers.build_end_of_day_pack` writes at the end of every prior
    fire. A malformed or unreadable file is SKIPPED, never fatal: the
    pattern layer degrades to fewer packs rather than crashing the close
    over one corrupted audit copy.

    `before_for_date`, when given, excludes any pack whose OWN `for_date` is
    not strictly earlier than it — defensive against a same-day re-fire
    reading itself back as its own history. This function never writes
    anything; `state on the pack record, not a new store` (§0) means every
    write this spec makes rides the SAME pack file the driver already
    writes, and this is the reader for it.
    """
    d = _packs_dir(workspace_root)
    if not d.is_dir():
        return []
    try:
        files = sorted(d.glob(PACK_GLOB), key=lambda p: p.name, reverse=True)
    except OSError:
        return []
    out: List[dict] = []
    for f in files:
        if len(out) >= limit:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a corrupt audit copy is skipped
            continue
        if not isinstance(data, dict):
            continue
        fd = data.get("for_date")
        if before_for_date and isinstance(fd, str) and fd >= before_for_date:
            continue
        out.append(data)
    return out


# ---------------------------------------------------------------------------
# Extraction off ONE pack's already-composed, already-grounded sentences.
# Nothing below re-derives from raw rows — Layer 1 reads what EODARC1 and
# EODSYNTH1 already counted and persisted.
# ---------------------------------------------------------------------------

def _unmoved_sentences_of(wim_block: Optional[dict]) -> List[dict]:
    """The unmoved-arc sentences off ONE `what_it_meant` BLOCK (today's own,
    already fenced and composed, or a prior pack's persisted copy of the
    same shape)."""
    if not isinstance(wim_block, dict):
        return []
    return [s for s in (wim_block.get("sentences") or [])
            if isinstance(s, dict) and s.get("kind") == "unmoved"]


def _unmoved_sentences(pack: dict) -> List[dict]:
    """The same, read off a WHOLE prior pack's own `what_it_meant` key."""
    return _unmoved_sentences_of(
        pack.get("what_it_meant") if isinstance(pack, dict) else None)


def _ref_bag(sentences: Iterable[dict]) -> set:
    """Every ref named by a set of sentences, pooled. Used only as a
    MEMBERSHIP test (`identity in bag`), so pooling across sentences costs
    nothing a per-sentence walk would have bought here."""
    bag: set = set()
    for s in sentences:
        bag.update(str(r) for r in ((s or {}).get("refs") or []))
    return bag


def _unmoved_ref_bag(pack: dict) -> set:
    return _ref_bag(_unmoved_sentences(pack))


def _slipped_sentences_of(sp_block: Optional[dict],
                           kind: Optional[str] = None) -> List[dict]:
    """The slipped-prose sentences off ONE `slipped_prose` BLOCK, optionally
    filtered to one consequence `kind`."""
    if not isinstance(sp_block, dict):
        return []
    out = [s for s in (sp_block.get("sentences") or []) if isinstance(s, dict)]
    if kind is not None:
        out = [s for s in out if s.get("kind") == kind]
    return out


def _slipped_sentences(pack: dict, kind: Optional[str] = None) -> List[dict]:
    """The same, read off a WHOLE prior pack's own `slipped_prose` key."""
    return _slipped_sentences_of(
        pack.get("slipped_prose") if isinstance(pack, dict) else None, kind)


def _slipped_ref_bag(pack: dict, kind: Optional[str] = None) -> set:
    return _ref_bag(_slipped_sentences(pack, kind))


def _consecutive_streak(identity: str, prior_packs: Iterable[dict],
                         bag_fn) -> int:
    """1 (today, the caller's own anchor) plus how many of `prior_packs`
    (NEWEST FIRST) also carry `identity` in `bag_fn(pack)`, stopping at the
    first pack that does not. A gap breaks the streak — this counts
    CONSECUTIVE closes, never a scattered total."""
    streak = 1
    for pack in prior_packs:
        if identity in bag_fn(pack):
            streak += 1
        else:
            break
    return streak


# ---------------------------------------------------------------------------
# The three detectors. Each returns candidate dicts:
#   {"template", "identity", "strength", "label", "refs", "text", "push_text"}
# ---------------------------------------------------------------------------

def detect_stillness(*, today_what_it_meant: Optional[dict],
                      arc_label_by_ref: Optional[dict],
                      prior_packs: List[dict]) -> List[dict]:
    """repeat-stillness (§ Layer 1): an arc unmoved >= `STILLNESS_MIN_STREAK`
    consecutive closes while carrying a stated consequence — read straight
    off today's OWN `what_it_meant` unmoved sentences (already fenced,
    already grounded) and the same sentence-shape on each prior pack."""
    labels = arc_label_by_ref or {}
    out: List[dict] = []
    seen: set = set()
    for s in _unmoved_sentences_of(today_what_it_meant):
        refs = [str(r) for r in (s.get("refs") or []) if str(r or "").strip()]
        if not refs:
            continue
        arc_ref = refs[0]
        if arc_ref in seen:
            continue
        seen.add(arc_ref)
        streak = _consecutive_streak(arc_ref, prior_packs, _unmoved_ref_bag)
        if streak < STILLNESS_MIN_STREAK:
            continue
        label = labels.get(arc_ref) or arc_ref.split(":", 1)[-1]
        out.append({
            "template": "stillness", "identity": arc_ref, "strength": streak,
            "label": label, "refs": refs,
            "text": T_STILLNESS.format(ordinal=_ordinal(streak), label=label),
            "push_text": PUSH_STILLNESS.format(label=label, streak=streak),
        })
    return out


def detect_mention(*, today_slipped_prose: Optional[dict],
                    prior_packs: List[dict]) -> List[dict]:
    """repeat-mention-without-work (§ Layer 1): a commitment recurring in the
    meeting-gated slip on >= `MENTION_MIN_COUNT` of the days examined
    (today plus the prior packs actually read), with no work event between —
    the same claim `eod_synthesis.compute_slipped_prose`'s own
    `CONSEQUENCE_MEETING` sentence already states for one evening; this
    counts how many evenings say it about the SAME commitment."""
    counts: dict = {}
    refs_by_id: dict = {}
    label_by_id: dict = {}
    days_examined = 0

    def _scan(sentences: List[dict]) -> None:
        nonlocal days_examined
        hit_today: set = set()
        for s in sentences:
            refs = [str(r) for r in (s.get("refs") or []) if str(r or "").strip()]
            if not refs:
                continue
            cid = refs[0]
            hit_today.add(cid)
            refs_by_id.setdefault(cid, set()).update(refs)
            text = str(s.get("text") or "")
            title = text.split(" did not go out")[0].strip()
            if title:
                label_by_id[cid] = title
        for cid in hit_today:
            counts[cid] = counts.get(cid, 0) + 1
        days_examined += 1

    _scan(_slipped_sentences_of(today_slipped_prose, syn.CONSEQUENCE_MEETING))
    for pack in prior_packs:
        _scan(_slipped_sentences(pack, syn.CONSEQUENCE_MEETING))

    out: List[dict] = []
    for cid, n in counts.items():
        if n < MENTION_MIN_COUNT:
            continue
        label = label_by_id.get(cid, cid.split(":", 1)[-1])
        refs = sorted(refs_by_id.get(cid) or {cid})
        out.append({
            "template": "mention", "identity": cid, "strength": n,
            "label": label, "refs": refs,
            "text": T_MENTION.format(label=label, count=n,
                                      of_days=days_examined),
            "push_text": PUSH_MENTION.format(label=label, count=n,
                                              of_days=days_examined),
        })
    return out


def detect_survival(*, today_open_rows: Optional[List[dict]],
                     prior_packs: List[dict]) -> List[dict]:
    """survival count (§ Layer 1): on each of today's open rows that states a
    DATE consequence (`eod_synthesis.CONSEQUENCE_DATE`), how many
    consecutive closes it has survived un-closed — evidenced by its
    commitment ref appearing in either the unmoved-arc bag or the
    meeting-slip bag on each prior pack (both are "still open and stated" on
    that evening's own record). Only the item at or above
    `SURVIVAL_MIN_STREAK` becomes a candidate; the caller (via the cap and
    the strength sort) picks the OLDEST one when more than one qualifies."""
    out: List[dict] = []
    for row in (today_open_rows or []):
        if not isinstance(row, dict):
            continue
        cons = syn.arc_stated_consequence(row)
        if not cons or cons.get("kind") != syn.CONSEQUENCE_DATE:
            continue
        cid = str(row.get("commitment_id") or "").strip()
        if not cid:
            continue
        identity = syn.ref_commitment(cid)

        def _bag(pack: dict) -> set:
            return _unmoved_ref_bag(pack) | _slipped_ref_bag(pack)

        streak = _consecutive_streak(identity, prior_packs, _bag)
        if streak < SURVIVAL_MIN_STREAK:
            continue
        label = str(row.get("title") or "").strip() or cid
        out.append({
            "template": "survival", "identity": identity, "strength": streak,
            "label": label, "refs": [identity],
            "text": T_SURVIVAL.format(label=label, n=streak),
            "push_text": PUSH_SURVIVAL.format(label=label, n=streak),
        })
    return out


def compute_patterns(*, today_what_it_meant: Optional[dict],
                      today_slipped_prose: Optional[dict],
                      today_open_rows: Optional[List[dict]],
                      arc_label_by_ref: Optional[dict],
                      prior_packs: List[dict],
                      cap: int = CAP_PATTERNS) -> dict:
    """LAYER 1, ASSEMBLED. At most `cap` pattern sentences.

    HONEST ABSENCE (§0 ruling 4): fewer than `MIN_PRIOR_PACKS` packs on disk
    and this renders NOTHING — a thin history is not evidence, and a coach
    that talks anyway over two nights of data is exactly the lecture the
    hard caps exist to prevent.

    THE CAP (§0 ruling 3): candidates are DEDUPED BY IDENTITY first — the
    same commitment or arc surfacing under two detectors is one fact, not
    two sentences, and the stronger detector (by strength, then
    `TEMPLATE_PRIORITY`) wins the identity. What survives is sorted by
    strength (most repetitions first, then the older item by identity for a
    deterministic tie-break) and capped.
    """
    if len(prior_packs or []) < MIN_PRIOR_PACKS:
        return {"kept": [], "dropped": [], "n_dropped": 0, "n_candidates": 0}

    candidates = (
        detect_stillness(today_what_it_meant=today_what_it_meant,
                          arc_label_by_ref=arc_label_by_ref,
                          prior_packs=prior_packs)
        + detect_mention(today_slipped_prose=today_slipped_prose,
                          prior_packs=prior_packs)
        + detect_survival(today_open_rows=today_open_rows,
                           prior_packs=prior_packs)
    )

    best: dict = {}
    for c in candidates:
        key = c["identity"]
        cur = best.get(key)
        if cur is None:
            best[key] = c
            continue
        c_rank = (c["strength"], -TEMPLATE_PRIORITY.get(c["template"], 99))
        cur_rank = (cur["strength"], -TEMPLATE_PRIORITY.get(cur["template"], 99))
        if c_rank > cur_rank:
            best[key] = c

    ordered = sorted(
        best.values(),
        key=lambda c: (-c["strength"], TEMPLATE_PRIORITY.get(c["template"], 99),
                        c["identity"]))
    kept = ordered[:cap] if cap else list(ordered)
    dropped = ordered[cap:] if cap else []
    return {"kept": kept, "dropped": dropped, "n_dropped": len(dropped),
            "n_candidates": len(ordered)}


# ---------------------------------------------------------------------------
# Layer 2, first half — the intent-vs-outcome delta
# ---------------------------------------------------------------------------

def compute_intent_delta(*, workspace_root, for_date: str,
                          closures: Optional[Iterable[dict]],
                          open_rows: Optional[Iterable[dict]]) -> dict:
    """LAYER 2 (§ Layer 2): the day's own STATED `day_intent` against what
    today's record shows. At most ONE sentence.

    HONEST ABSENCE (§0 ruling 4): no STATED record for `for_date` -> renders
    nothing. GROUNDED ONLY: the top-ranked item's own `commitment_id`
    (EODFIX1's linkage) is the sole join this reads — an item with none, or
    whose commitment can no longer be found open OR closed, renders nothing
    rather than a guess reconstructed from the item's own words.
    """
    import day_intent as di

    record = di.load_day_intent(workspace_root, for_date)
    if not record or not record.get("items"):
        return {"text": "", "sentences": [], "refs": [], "renders": False}

    items = sorted(
        [i for i in record["items"] if isinstance(i, dict)],
        key=lambda i: i.get("rank") if isinstance(i.get("rank"), int) else 999)
    if not items:
        return {"text": "", "sentences": [], "refs": [], "renders": False}
    item = items[0]
    cid = str(item.get("commitment_id") or "").strip()
    text = str(item.get("text") or "").strip()
    if not cid or not text:
        return {"text": "", "sentences": [], "refs": [], "renders": False}

    closed_row = next(
        (c for c in (closures or [])
         if isinstance(c, dict) and str(c.get("commitment_id") or "") == cid),
        None)
    if closed_row is not None:
        refs = [r for r in (syn.ref_commitment(cid),
                             syn.ref_event(closed_row.get("ts"))) if r]
        s = syn.sentence(DELTA_POS.format(x=text), refs, tier=syn.TIER_MEANT,
                          kind="delta_positive")
        result = syn.compose([s])
        result["renders"] = bool(result["text"])
        return result

    still_open = any(
        isinstance(r, dict) and str(r.get("commitment_id") or "") == cid
        for r in (open_rows or []))
    if still_open:
        s = syn.sentence(DELTA_NEG.format(x=text), [syn.ref_commitment(cid)],
                          tier=syn.TIER_MEANT, kind="delta_negative")
        result = syn.compose([s])
        result["renders"] = bool(result["text"])
        return result

    # Neither closed nor open: the item's own commitment cannot be found on
    # today's record (dropped, or unresolvable). Nothing to ground a claim
    # on, so this stays silent rather than infer one from the intent's text.
    return {"text": "", "sentences": [], "refs": [], "renders": False}


# ---------------------------------------------------------------------------
# Layer 2, second half — the push line and the repetition fence
# ---------------------------------------------------------------------------

def _read_push_state(prior_packs: List[dict]) -> dict:
    """The FSM state the most recent PRIOR pack left behind, or the zero
    state when there is none.

    THE STATE LIVES ON THE PACK RECORD (§0's ruling on the fence, verbatim:
    "state on the pack record, not a new store"). This is the ONLY reader,
    and it looks at exactly ONE prior pack — the fence is a chain each close
    extends, never a history this module re-derives."""
    if not prior_packs:
        return {"identity": None, "streak": 0, "cooldown": 0}
    coach = prior_packs[0].get("coach") if isinstance(prior_packs[0], dict) else None
    if not isinstance(coach, dict):
        return {"identity": None, "streak": 0, "cooldown": 0}
    state = coach.get("push_state")
    if not isinstance(state, dict):
        return {"identity": None, "streak": 0, "cooldown": 0}
    return {
        "identity": state.get("identity"),
        "streak": int(state.get("streak") or 0),
        "cooldown": int(state.get("cooldown") or 0),
        # REVIEW_PR63 F-1 — the cooldown binds to the identity that was
        # named, never to the coach as a whole. Older packs without the key
        # degrade to the named identity itself.
        "cooldown_identity": state.get("cooldown_identity",
                                       state.get("identity")),
    }


def compute_push(*, patterns_kept: List[dict], prior_packs: List[dict]) -> dict:
    """LAYER 2's push line and THE REPETITION FENCE (§0's anti-nag rule).

    At most one push, and ONLY when Layer 1 kept a pattern — `patterns_kept`
    is the CAPPED list `compute_patterns` returned, so the push is always
    the strongest kept candidate's own count, never a fact this function
    invents. See the module docstring for the state machine; the short
    version: silent during a cooldown, silent with no candidate, otherwise
    the streak continues or restarts by identity, and the third consecutive
    render names the repetition and starts the cooldown.
    """
    prior_state = _read_push_state(prior_packs)
    candidate = patterns_kept[0] if patterns_kept else None

    # REVIEW_PR63 F-1 — the cooldown is PER-PUSH, not global. It mutes only
    # the identity that was named; a different pattern's push arising during
    # the cooldown is fresh signal and renders. The cooling identity's clock
    # ticks down regardless of what else happens.
    cooling = prior_state["cooldown"] > 0
    cd_id = prior_state["cooldown_identity"] if cooling else None
    cd_left = prior_state["cooldown"] - 1 if cooling else 0

    if candidate is None:
        new_state = {"identity": None, "streak": 0,
                     "cooldown": cd_left,
                     "cooldown_identity": cd_id if cd_left > 0 else None}
        return {"text": "", "sentences": [], "refs": [], "renders": False,
                "push_state": new_state, "named": False}

    identity = candidate["identity"]

    if cooling and identity == cd_id:
        # The muted one is still the strongest — stay quiet, keep its streak
        # frozen so the post-cooldown resume counts the record truthfully.
        new_state = {"identity": prior_state["identity"],
                     "streak": prior_state["streak"],
                     "cooldown": cd_left,
                     "cooldown_identity": cd_id if cd_left > 0 else None}
        return {"text": "", "sentences": [], "refs": [], "renders": False,
                "push_state": new_state, "named": False}

    streak = (prior_state["streak"] + 1
              if prior_state["identity"] == identity else 1)

    if streak >= NAMING_STREAK:
        # REVIEW_PR63 F-2 — the naming form states the ACTUAL streak. A
        # persisting item resuming after a cooldown is on its fourth, fifth,
        # … close, and a module whose ruling is "counts the record" may not
        # say "third" about it.
        text = (PUSH_NAMED_PREFIX.format(ordinal=_ordinal(streak))
                + candidate["push_text"])
        new_state = {"identity": identity, "streak": streak,
                     "cooldown": COOLDOWN_CLOSES,
                     "cooldown_identity": identity}
        named = True
    else:
        text = candidate["push_text"]
        new_state = {"identity": identity, "streak": streak,
                     "cooldown": cd_left,
                     "cooldown_identity": cd_id if cd_left > 0 else None}
        named = False

    refs = list(candidate.get("refs") or [identity])
    s = syn.sentence(text, refs, tier=syn.TIER_MEANT, kind="push")
    result = syn.compose([s])
    result["renders"] = bool(result["text"])
    result["push_state"] = new_state
    result["named"] = named
    return result


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------

def build_coach(*, workspace_root, for_date: str,
                 today_what_it_meant: Optional[dict],
                 today_slipped_prose: Optional[dict],
                 today_closures: Optional[Iterable[dict]],
                 today_open_rows: Optional[Iterable[dict]],
                 arc_label_by_ref: Optional[dict] = None) -> dict:
    """Every EODCOACH2 block, computed once. Prose only, ZERO new
    interactions (§0). Reads the last `MAX_PRIOR_PACKS` EOD packs off disk as
    this evening's ONLY history; nothing here fetches a connector or writes
    anything — the caller (`surface_drivers.build_end_of_day_pack`) persists
    the returned dict onto the pack exactly as it already persists every
    other block, and THAT write is the fence's only store.

    `today_what_it_meant` / `today_slipped_prose` are the ALREADY-FENCED,
    already-composed blocks `eod_synthesis.build_synthesis` returned for
    today — Layer 1 reads their sentences rather than re-deriving from raw
    rows. `today_closures` / `today_open_rows` must be fenced BY THE CALLER
    through `eod_synthesis.visible_rows` before they reach here — this is a
    THIRD door onto the same rows the arc read and the tomorrow rollover
    already had to fence (EODARC1 §3.6), and it inherits the same discipline
    rather than re-implementing it.
    """
    open_rows = [r for r in (today_open_rows or []) if isinstance(r, dict)]
    closures = [c for c in (today_closures or []) if isinstance(c, dict)]

    prior_packs = read_prior_packs(workspace_root, before_for_date=for_date,
                                    limit=MAX_PRIOR_PACKS)

    patterns = compute_patterns(
        today_what_it_meant=today_what_it_meant,
        today_slipped_prose=today_slipped_prose,
        today_open_rows=open_rows,
        arc_label_by_ref=arc_label_by_ref,
        prior_packs=prior_packs)

    delta = compute_intent_delta(workspace_root=workspace_root,
                                  for_date=for_date, closures=closures,
                                  open_rows=open_rows)

    push = compute_push(patterns_kept=patterns["kept"],
                         prior_packs=prior_packs)

    pattern_sentences = [
        syn.sentence(c["text"], c["refs"], tier=syn.TIER_MEANT, kind="pattern")
        for c in patterns["kept"]
    ]
    # `pattern_block` is the FULL Layer 1 computed content — EODCOACH2's own
    # refs contract, untouched — and is what this function returns under
    # "patterns" below (the pack's audit/receipt copy, per COACHONE1 §0
    # ruling 2: dedup is presentation-only, the computed record stays whole).
    pattern_block = syn.compose(pattern_sentences)

    # -----------------------------------------------------------------
    # COACHONE1 §0 ruling 1 — THE RENDER CHOKEPOINT. One insight renders
    # once per close. `compute_push`'s candidate is always
    # `patterns_kept[0]` (the strongest kept pattern) restated as a fact
    # plus its concrete cost — so whenever the push actually renders, it
    # names the SAME insight as one of the sentences Layer 1 already kept,
    # under a different template. Presentation drops that pattern
    # sentence in favor of the push's fuller (count + cost, and on the
    # naming close, the repetition itself) restatement — identity by
    # exact ref-SET equality (never fuzzy text match), so a genuinely
    # distinct pattern that merely shares no refs with the push is never
    # touched. Ruling 3: cadence, push_state, and pattern selection
    # (`patterns["kept"]` itself) do not move — only which ALREADY-KEPT
    # sentences join the presentation text is affected here.
    # -----------------------------------------------------------------
    push_renders = bool(push.get("renders"))
    push_ref_set = _ref_set(push.get("refs")) if push_renders else frozenset()
    render_sentences = [
        s for s in pattern_sentences
        if not (push_renders and push_ref_set
                and _ref_set(s.get("refs")) == push_ref_set)
    ]
    pattern_render_block = syn.compose(render_sentences)

    parts = [t for t in (pattern_render_block["text"], delta.get("text"),
                          push.get("text")) if t]
    text = "\n".join(parts)
    refs: List[str] = []
    for block in (pattern_render_block, delta, push):
        for r in (block.get("refs") or []):
            if r not in refs:
                refs.append(r)

    syn.assert_no_score(text, where="coach")

    return {
        "text": text,
        "refs": refs,
        "renders": bool(text),
        "patterns": pattern_block,
        "delta": delta,
        "push": push,
        "push_state": push.get("push_state"),
        "n_prior_packs": len(prior_packs),
        "n_pattern_candidates": patterns.get("n_candidates", 0),
        "n_pattern_dropped": patterns.get("n_dropped", 0),
    }


__all__ = [
    "BLOCK_COACH",
    "CAP_PATTERNS", "CAP_DELTA", "CAP_PUSH",
    "MIN_PRIOR_PACKS", "MAX_PRIOR_PACKS",
    "STILLNESS_MIN_STREAK", "MENTION_MIN_COUNT", "SURVIVAL_MIN_STREAK",
    "NAMING_STREAK", "COOLDOWN_CLOSES",
    "T_STILLNESS", "T_MENTION", "T_SURVIVAL",
    "DELTA_NEG", "DELTA_POS",
    "PUSH_STILLNESS", "PUSH_MENTION", "PUSH_SURVIVAL", "PUSH_NAMED_PREFIX",
    "read_prior_packs",
    "_ref_set",
    "detect_stillness", "detect_mention", "detect_survival",
    "compute_patterns",
    "compute_intent_delta",
    "compute_push",
    "build_coach",
]
