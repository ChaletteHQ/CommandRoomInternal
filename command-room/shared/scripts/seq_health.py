#!/usr/bin/env python3
"""seq_health — recurring duplicate-seq AND missing-seq detector.

Duplicate half: BUG-8330 item 7c. Gap half: LEDGERFENCE1 (2026-09-07).

The appender has allocated seq inside the writer lock since SPEC A1, so NEW
collisions should not occur — but ~140 historic duplicates predate it (the
pre-atomic-write window, CHANGELOG v3.13.8.x), and a dup seq is live ambiguity:
an F3 seq-alias closure (`source_event_seq`) resolves to EVERY commitment at
that seq, and the `commitment_seq_<n>` id fallback collides the same way.

This detector runs recurring (cleanup's weekly pass; system-health reads the
report on demand) instead of the one-shot report `backfill_substrate.py` was:

  - `scan(events_path)` — pure read: every seq held by 2+ events.
  - `detect_and_mark(workspace_root, apply=True)` — appends ONE additive
    `seq_repaired` marker per NEWLY-found duplicated seq. The marker is the
    detector's own memory: already-marked seqs are not re-reported, so the
    Monday note only ever surfaces NEW collisions (which, post-A1, indicate
    a real writer bug worth eyes). History is never rewritten; the events
    holding the duplicate seq stay exactly as written.

Marker shape:
  {"type": "seq_repaired", "source_skill": "<caller>",
   "data": {"duplicate_seq": N, "n_occurrences": k,
            "event_types": [...], "detected_by": "seq_health"}}

Readers: this module (dedup of its own reports), cleanup's Monday note and
system-health's report line (both render the counts).

THE GAP HALF (LEDGERFENCE1, 2026-09-07 — the attended test's third HOLD)
A duplicate seq means two events were written with the same number. A GAP is
the opposite: numbers that are not in the file at all.

On 2026-09-07 a chat produced one of those by hand: reprocessing one meeting
it decided twelve of its own rows were duplicates, backed the ledger up and
deleted the twelve lines, leaving 15555 -> 15568. `system health` reported
nothing, because it read duplicates and not holes. This half closes that: the
same detect-mark-report shape, one `seq_gap_marked` marker per newly-found
hole, so the report only ever surfaces NEW ones.

BUT NOT EVERY HOLE IS A REMOVAL, and the first cut of this half said it was
(fix round 1, review finding F-1: on the 2026-09-07 book, 814 of the 826
missing numbers it counted were not removals — most of them a counter that
had been re-based). Round 1 then went too far the other way (fix round 2,
review finding F-10): it let a hole off on the ABSENCE of evidence — rows
adjacent in the file, a repair row within three rows, the writers either side
differing — and silenced four real hand-deletions the reviewer made on a copy
of the live book, about seven in ten across every position.

So a benign verdict now needs POSITIVE evidence and there are three classes:

  - `renumbered` — the numbering VISIBLY restarts (time runs backwards across
    the boundary AND a new contiguous run of numbers begins at it).
  - `repair` — a recorded repair NAMES the numbers it set aside and this
    stretch is inside that list (or, for repairs written before the numbers
    were recorded, it ran inside this stretch's own window, set aside at
    least as many lines as are missing, and carries a number ABOVE all of
    them — a repair cannot account for entries written after it ran).
  - `removed` — everything else.

  - `scan_gaps(workspace_root)` — pure read over the FULL history, every run
    classified (`removed` / `repair` / `renumbered`).
  - `detect_and_mark_gaps(workspace_root, apply=True)` — one additive marker
    per new run OF ANY CLASS, so each class's sentence is said once and not
    every Monday after. History is never rewritten here either.
  - `gap_notice(report)` — one plain sentence per class present, most serious
    first. Round 1 spoke only about removals and rendered the rest on no
    surface at all, which made its own claim to be visible untrue.

Marker shape:
  {"type": "seq_gap_marked", "source_skill": "<caller>",
   "data": {"gap_after": N, "gap_before": M, "n_missing": k,
            "classification": "removed" | "repair" | "renumbered",
            "detected_by": "seq_health"}}

SHARDS: the gap scan reads every shard through `events_io.iter_events`, never
the active file alone. A5 rotation moves prior-year events into
`events-<year>.jsonl` siblings and leaves a `shard_rotated` anchor behind, so
an active-file-only scan on a rotated workspace would report the whole of
history as one enormous hole. (The duplicate half still reads the active file
— unchanged here; a cross-shard duplicate is a different question and this
lane does not widen a pinned set on a guess.)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

EPOCH_THRESHOLD = 10**10  # next_seq.py contract — nano-epoch artifacts excluded

# LEDGERFENCE1 fix round 3 (F-15). A quarantine sidecar holds the lines the
# parser rejected, so their numbers can only be read off the text.
#
# FIX ROUND 4 (review finding F-22). Round 3 took the LEFTMOST `"seq"` on the
# line with this pattern — but `atomic_append_jsonl` stamps the row's own
# `seq` LAST, so on every modern row the row's own number is the last one in
# the text and any NESTED one comes first. `brain_undo` writes exactly that
# shape (`data.batch_ref: {"kind": ..., "seq": <receipt seq>}`) and a
# `pack_run` row carries `data.items[].seq`, so a torn row of either kind
# made a repair claim a number it never took — and a hand deletion of THAT
# number was then silenced on the naming branch, which needs no window and no
# count bound at all. `top_level_seq_in_raw` replaces that search outright —
# round 3's leftmost pattern is gone rather than left lying next to it, so
# nothing can reach for it again. What survives is its value half, matched
# only after a `"seq"` key has been located at the row's own top level.
_RAW_SEQ_VALUE = re.compile(r'\s*:\s*(\d+)')


_ANY_RAW_SEQ = re.compile(r'"seq"\s*:\s*(\d+)')


def top_level_seq_in_raw(raw):
    """The `seq` a raw JSONL line carries AS ITS OWN, or `None`.

    For text that never parsed — the only text either caller reads this way,
    because a quarantined line is by construction one the parser rejected.

    A torn line comes in two shapes and they are not read the same way.

    A line that OPENS ITS OWN OBJECT is a row cut off at the end: its own
    `seq` sits immediately inside that object and anything nested is deeper.
    The line is walked tracking brace depth outside strings and only a `"seq"`
    key at depth 1 counts, so `data.batch_ref.seq` — the shape `brain_undo`
    writes — and `data.items[].seq` on a `pack_run` row are never mistaken
    for the row's own number. If such a line was cut off BEFORE its own `seq`
    was reached, it names nothing, which is the honest answer.

    A line that does NOT open an object is the TAIL of a row whose beginning
    went somewhere else — the shape the operator's 2026-06-23 quarantine file
    actually holds. There is no top level to measure against, so the only
    thing that can be said is uniqueness: exactly one `"seq"` in the text is
    that row's number, and two or more name nothing.

    Either way, `None` on ambiguity: a line that says two different things
    about its own number says nothing this check may act on, and the count
    beside the list still says a line went (fix round 4, F-22).

    RESIDUAL, stated rather than hidden: a tail fragment that carries exactly
    one `"seq"` and it is a nested one — its own having been cut away with the
    head — is read as the row's own. Nothing in the text can tell those apart.
    """
    if not isinstance(raw, str):
        return None
    stripped = raw.lstrip()
    if not stripped.startswith("{"):
        hits = _ANY_RAW_SEQ.findall(raw)
        return int(hits[0]) if len(hits) == 1 else None
    found = []
    depth = 0
    i = len(raw) - len(stripped)
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == '"':
            j = i + 1
            while j < n:
                if raw[j] == "\\":
                    j += 2
                    continue
                if raw[j] == '"':
                    break
                j += 1
            if j >= n:
                break  # an unterminated string ends the readable text
            if depth == 1 and raw[i + 1:j] == "seq":
                mo = _RAW_SEQ_VALUE.match(raw, j + 1)
                if mo:
                    found.append(int(mo.group(1)))
                    if len(found) > 1:
                        return None
            i = j + 1
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    return found[0] if len(found) == 1 else None


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def scan(events_path) -> Dict[int, List[dict]]:
    """seq → list of events holding it, for every seq held by 2+ events.
    Pure read; malformed lines are skipped (loader contract)."""
    from cru_match import load_events_defensively
    events, _skipped = load_events_defensively(Path(events_path))
    by_seq: Dict[int, List[dict]] = {}
    for ev in events:
        seq = ev.get("seq")
        if (isinstance(seq, int) and not isinstance(seq, bool)
                and seq < EPOCH_THRESHOLD):
            by_seq.setdefault(seq, []).append(ev)
    return {s: evs for s, evs in by_seq.items() if len(evs) > 1}


def detect_and_mark(workspace_root, *, apply: bool = False,
                    source_skill: str = "seq_health") -> dict:
    """Scan for duplicate seqs; report NEW ones (not yet covered by a
    `seq_repaired` marker). With apply=True, append one additive marker per
    new duplicate so the next run treats it as known.

    Returns {"n_duplicate_seqs", "n_new", "new": [...], "marked": bool}.

    CONCURRENCY (BUG-8330 fix round, FX-2): the mark path is a
    read-decide-append, so under `apply=True` the WHOLE of it runs inside
    `writer_lock.events_writer_lock` and the report is re-derived in there.
    Unlocked, a `seq_repaired` marker appended by a concurrent run between
    this scan and this append is invisible to the dedup check and the same
    duplicate gets marked twice. (Unlike the seq-relocation repair this is an
    APPEND, never a truncating rewrite, so the failure is a duplicate marker,
    not a destroyed event.) A read-only run (`apply=False`) takes no lock.

    PHANTOM PATHS: a missing events.jsonl is refused before the lock is taken
    — acquiring it would create `<root>/_hq/data/.writer.lock` and fabricate a
    substrate tree under a mistyped root.
    """
    events_path = _events_path(workspace_root)
    if not events_path.exists():
        return {"n_duplicate_seqs": 0, "n_new": 0, "new": [], "marked": False,
                "refused": f"no events.jsonl under {str(workspace_root)!r}"}
    if not apply:
        return _detect(workspace_root)
    from writer_lock import events_writer_lock
    with events_writer_lock(events_path, holder=source_skill):
        report = _detect(workspace_root)  # RE-DERIVED inside the lock
        if report["new"]:
            from atomic_write import atomic_append_jsonl
            atomic_append_jsonl(events_path, [{
                "type": "seq_repaired",
                "source_skill": source_skill,
                "data": dict(entry, detected_by="seq_health"),
            } for entry in report["new"]], holder=source_skill)
            report["marked"] = True
        return report


def _detect(workspace_root) -> dict:
    """Pure read half of `detect_and_mark` — the duplicate scan and the
    already-marked dedup, with nothing written. Callers on the mark path hold
    `events_writer_lock` around this."""
    from cru_match import load_events_defensively
    events_path = _events_path(workspace_root)
    dups = scan(events_path)

    already_marked = set()
    events, _skipped = load_events_defensively(events_path)
    for ev in events:
        if ev.get("type") == "seq_repaired":
            v = (ev.get("data") or {}).get("duplicate_seq")
            if isinstance(v, int):
                already_marked.add(v)

    new = []
    for seq in sorted(dups):
        if seq in already_marked:
            continue
        evs = dups[seq]
        new.append({
            "duplicate_seq": seq,
            "n_occurrences": len(evs),
            "event_types": sorted({str(e.get("type") or "?") for e in evs}),
        })

    return {
        "n_duplicate_seqs": len(dups),
        "n_new": len(new),
        "new": new,
        "marked": False,
    }


# ---------------------------------------------------------------------------
# The gap half (LEDGERFENCE1) — numbers that are not in the log.
# ---------------------------------------------------------------------------
#
# FIX ROUND 1 (review finding F-1). The first cut counted every missing number
# as a removal and told the customer so. On the 2026-09-07 book that sentence
# was wrong about 814 of the 826 numbers it counted. A detector that cries
# tampering at the product's own repair work is worse than no detector, so
# every run of missing numbers is now CLASSIFIED and only the residue — a hole
# the product cannot explain — reaches a customer surface.

# FIX ROUND 2 (review finding F-10). Round 1's classes were DEFEASIBLE: each
# one silenced a hole on the ABSENCE of evidence — the rows either side happen
# to be adjacent, a repair happens to sit within three rows, the writers either
# side happen to differ. The reviewer deleted rows from a copy of the live book
# four different ways and every one of them was explained away; simulated over
# every position, only about three hand-deletions in ten were reported.
#
# A benign class now needs POSITIVE evidence and nothing else counts:
#
#   renumbered  the numbering VISIBLY restarts — the timestamps run backwards
#               across the boundary AND a new contiguous run of numbers begins
#               there. A deletion does neither.
#   repair      a recorded repair NAMES the numbers it set aside (the event's
#               own `quarantined_seqs`, or the quarantine sidecar it points
#               at, which holds the lines themselves) and the hole is inside
#               that list. For repairs written before the writer recorded the
#               numbers, the fallback is bounded on three sides: the repair
#               must have RUN inside this hole's own window, must have set
#               aside AT LEAST as many lines as are missing, and must carry a
#               number ABOVE every missing one (fix round 3, F-17 — it
#               appended its receipt after it took them). Proximity is not
#               evidence and never clears a hole again.
#   removed     everything else.
#
# Two round-1 classes are gone. `reserved_not_used` claimed one append took
# numbers and did not use them — but the allocator has taken exactly one
# number per row it writes, inside the lock, since the racy reserve-then-write
# pattern was removed (`atomic_write` R1), so NO receipt anywhere records
# "reserved N, wrote fewer". With no such receipt the class has no evidence to
# stand on, and it folds into `removed`. `never_arrived` claimed a third
# process took the numbers because the writers either side differ; the
# reviewer showed that is equally consistent with a deletion, and a class that
# cannot tell the two apart must not be the one that decides to stay quiet.

GAP_REMOVED = "removed"
GAP_REPAIR = "repair"
GAP_RENUMBERED = "renumbered"
# Ordered the way the maintenance note renders them: what a person must act on
# first, then what merely happened.
GAP_CLASSES = (GAP_REMOVED, GAP_REPAIR, GAP_RENUMBERED)

# A restart is only a restart when a NEW contiguous run of numbers begins at
# the boundary. Three in a row separates a re-based counter from a hole that
# happens to sit next to one out-of-order row.
_RESTART_RUN = 3
_REPAIR_TYPES = ("corruption_recovery", "seq_repaired", "shard_rotated")

# FIX ROUND 3 (review finding F-18). A deletion right AT a restart boundary
# destroys the boundary's own evidence, and it does so asymmetrically: take
# out the three rows just below and the restart still reads (the three vanish
# into it); take out the three rows just above — the ones whose timestamps ran
# backwards — and the restart stops reading at all, so the whole width of the
# re-basing is reported as entries a chat took out. On the operator's book
# that is 735 entries said about a deletion of three.
#
# One thing the log still says in that corner: a stretch cannot hold more
# entries than the numbering had ever produced. At the April boundary the
# whole history below is 267 numbers and the stretch claims 735 — for that to
# be a removal the workspace would have had to write, and lose, nearly three
# times everything it had ever written, in the twenty minutes between the two
# surviving rows. That is a counter that was re-based, and it is the only
# shape this rule recognises: the FIRST break in the numbering, wider than
# twice everything below it, with a fresh unbroken run beginning above.
#
# The floor keeps it away from a young workspace, where "twice everything
# below" is a small number: under a hundred missing entries this rule never
# applies and the ordinary classes decide.
_WIDE_RESTART_FACTOR = 2
_WIDE_RESTART_FLOOR = 100

# FIX ROUND 4 (review finding F-23). The width rule above checks four things
# and NOT ONE of them is a clock, so a log whose first break happens to be
# wide reads as a re-basing however far apart in time its two surviving rows
# are: 249 entries deleted after a head of 50, timestamps running strictly
# forward throughout, came back `renumbered`. A counter that is re-based
# stops and starts again in one sitting — the operator's own April boundary
# has twenty minutes between its two surviving rows — while a stretch cut out
# of history spans whatever the deleted entries spanned, which for a stretch
# this wide is months. So the two surviving rows have to be CLOSE IN TIME,
# and a day is generous for a sitting. Unreadable on either side means the
# condition cannot be checked, and an unchecked condition is never passed.
_WIDE_RESTART_MAX_HOURS = 24.0

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _human_date(dt) -> str:
    """`7 September 2026` — the one shape a date takes on a customer surface.
    Empty string when the row carried no readable time."""
    if dt is None:
        return ""
    try:
        return f"{dt.day} {_MONTHS[dt.month - 1]} {dt.year}"
    except Exception:
        return ""


def _regression_watermarks(workspace_root) -> List[int]:
    """The file maximum recorded by every substrate-regression record beside
    the ledger — `events.jsonl.seqregression.json` and its `.archived.*`
    siblings, written by `atomic_write` when the copy of the log it could see
    was SHORTER than the high-water mark it had recorded (SPEC SYNC1 A1).

    That number is the top of the stretch that did not arrive, so a hole
    containing it is that same event, already detected and already recorded —
    not a removal. Read-only, and a missing or unreadable sidecar is simply
    no evidence."""
    out: List[int] = []
    d = _events_path(workspace_root).parent
    if not d.is_dir():
        return out
    import json as _json
    for p in sorted(d.glob("events.jsonl.seqregression.json*")):
        try:
            raw = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        v = raw.get("file_max_seq") if isinstance(raw, dict) else None
        if isinstance(v, int) and not isinstance(v, bool):
            out.append(v)
    return out


def _seqs_in_quarantine_file(workspace_root, rel) -> set:
    """The numbers a quarantine sidecar actually holds.

    `recover_corruption.py` writes the raw lines it sets aside to that file
    before it rewrites the log, so the file IS the list of what left — the
    strongest evidence there is for a repair that ran before the event carried
    the numbers itself. Read-only; a missing or unreadable file is NO
    EVIDENCE, which is not the same thing as innocence.

    FIX ROUND 3 (review finding F-15). Round 2 read the sidecar with
    `json.loads` and skipped every line that failed — but the sidecar holds
    exactly the lines the parser REJECTED, so it skipped all of them and this
    reader returned an empty set on every real sidecar there is. A torn row
    still spells its own number: the 2026-06-23 sidecar on the operator's own
    book carries `"seq": 1948` in plain text while the stretch 1947 to 1949
    was being reported as a removal. The number is read off the text when the
    parse fails, bounded by `EPOCH_THRESHOLD` the same way `scan_gaps` bounds
    the log itself — the other sidecars carry nano-epoch junk in that slot,
    and junk must never be able to clear a hole.

    FIX ROUND 4 (review finding F-22). That read took the leftmost `"seq"` on
    the line, and a sidecar line whose `data.batch_ref` names another row's
    number reads back as THAT number — evidence about an entry this repair
    never touched. `top_level_seq_in_raw` reads the row's own number only."""
    if not isinstance(rel, str) or not rel.strip():
        return set()
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        return set()
    full = Path(workspace_root) / p
    if not full.is_file():
        return set()
    import json as _json
    out = set()
    try:
        raw = full.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        seq = None
        try:
            ev = _json.loads(line)
        except Exception:
            ev = None
        if isinstance(ev, dict):
            v = ev.get("seq")
            if isinstance(v, int) and not isinstance(v, bool):
                seq = v
        else:
            # FIX ROUND 4 (F-22). The row's OWN number, at the top level of
            # its object — never a nested one, and never at all when the
            # line offers two.
            seq = top_level_seq_in_raw(line)
        if seq is not None and 0 < seq < EPOCH_THRESHOLD:
            out.add(seq)
    return out


def _repair_evidence(ev, workspace_root):
    """What one recorded repair PROVES:
    `(named_seqs, n_set_aside, when, its_own_number)`.

    `named_seqs` — the exact numbers it says it set aside: the event's own
    `quarantined_seqs` (written since LEDGERFENCE1 fix round 2), else the
    numbers in the quarantine sidecar it points at. Empty when the record
    names none.
    `n_set_aside` — how many lines it says it set aside, `None` when it does
    not say. `duplicate_seqs_fixed.sample_seqs_renumbered` is a SAMPLE and is
    deliberately never read as a naming: a sample proves nothing about the
    numbers it left out.
    `its_own_number` — the repair receipt's own seq, `None` when it has none.
    A repair sets lines aside and THEN appends its receipt, so every number it
    took was allocated before its own (fix round 3, F-17).

    FIX ROUND 3 (review finding F-17). `duplicate_seqs_fixed.count` no longer
    counts toward `n_set_aside`. Renumbering a duplicate makes no hole — it
    changes a number, it does not remove a line — so counting those 22 rows on
    the operator's book let one legacy receipt vouch for up to 43 missing
    numbers it had never touched. Only `orphan_lines_removed` describes lines
    that actually left.
    """
    from event_time import event_dt
    data = ev.get("data")
    if not isinstance(data, dict):
        data = {}
    named = data.get("quarantined_seqs")
    seqs = set()
    if isinstance(named, list):
        seqs = {s for s in named
                if isinstance(s, int) and not isinstance(s, bool)}
    if not seqs:
        seqs = _seqs_in_quarantine_file(workspace_root,
                                        data.get("quarantine_file"))
    count = None
    for key in ("quarantined_count", "quarantined_line_count"):
        v = data.get(key)
        if isinstance(v, int) and not isinstance(v, bool):
            count = v
            break
    if count is None:
        blob = data.get("orphan_lines_removed")
        if isinstance(blob, dict):
            v = blob.get("count")
            if isinstance(v, int) and not isinstance(v, bool):
                count = v
    own = ev.get("seq")
    if not (isinstance(own, int) and not isinstance(own, bool)
            and 0 < own < EPOCH_THRESHOLD):
        own = None
    return (seqs, count, event_dt(ev), own)


def _run_length_from(present, start: int) -> int:
    """How many consecutive numbers are present starting at `start`, counted
    no further than `_RESTART_RUN` (that is all the answer is used for)."""
    n, s = 0, start
    while s in present and n < _RESTART_RUN:
        n += 1
        s += 1
    return n


def _within_one_sitting(t_lo, t_hi) -> bool:
    """Were the two rows either side of this stretch written within a day of
    each other? `False` when either carries no readable time — an unchecked
    condition is never a passed one (fix round 4, review finding F-23)."""
    if t_lo is None or t_hi is None:
        return False
    try:
        hours = abs((t_hi - t_lo).total_seconds()) / 3600.0
    except Exception:
        return False
    return hours <= _WIDE_RESTART_MAX_HOURS


def _restarts_here(lo_row, hi_row, present, rows_above, newest_below,
                   initial_run=None) -> bool:
    """Does the numbering VISIBLY start over at this boundary?

    Three things have to be true at once, and every one of them is something
    the log says rather than something it fails to say:

      1. The first entry above the boundary is OLDER than the last entry below
         it — the new numbering begins in the past.
      2. A new unbroken run of numbers begins at the boundary, and MORE THAN
         HALF of that run is older than the entry below. One out-of-order row
         is an entry written with its own historic date (a backfill does that
         all day); a run of them is a numbering that went back to the start.
      3. The entry below the boundary carries the NEWEST time the old
         numbering ever reached. A counter that stops has stopped at its own
         latest point; a stretch cut out of the middle of history has not.

    Rule (b) — the width rule, for a boundary whose own rows are gone — adds
    a fourth of its own: the two surviving rows must be within a day of each
    other (`_WIDE_RESTART_MAX_HOURS`). A re-basing happens in one sitting; a
    deletion wide enough to trip this rule spans months (fix round 4, F-23).

    FIX ROUND 2 (F-10). Condition 1 alone — which is all round 1 asked for —
    silenced about seven per cent of every possible deletion on the live book,
    because entries written with a historic date are ordinary there. With 2
    and 3 beside it that falls to about two per cent, and the one real restart
    on that book (April 2026) is still recognised. `rows_above` unavailable
    means condition 2 cannot be checked, and an unchecked condition is never
    passed: the answer is then no.

    FIX ROUND 3 (review finding F-18). Deleting the first few rows of the NEW
    numbering takes all three conditions with it, and the boundary is then
    read as one enormous removal. `initial_run` — `(lowest number, top of the
    unbroken run that starts there)` — carries the one thing the log still
    says in that corner: how many entries the numbering had ever produced
    below this point. See `_WIDE_RESTART_FACTOR` for what that buys and where
    it stops, and `_WIDE_RESTART_MAX_HOURS` for the clock fix round 4 put
    beside it (F-23).
    """
    _i_lo, s_lo, t_lo = lo_row
    _i_hi, s_hi, t_hi = hi_row
    if _run_length_from(present, s_hi) < _RESTART_RUN:
        return False
    # (a) the boundary says so itself: three conditions, all of them.
    if (t_lo is not None and t_hi is not None and t_hi < t_lo
            and newest_below is not None and newest_below == t_lo
            and rows_above):
        older = 0
        for k in range(_RESTART_RUN):
            row = rows_above.get(s_hi + k)
            if row is not None and row[2] is not None and row[2] < t_lo:
                older += 1
        if older * 2 > _RESTART_RUN:
            return True
    # (b) the boundary's own rows are gone, but the WIDTH still says it: the
    #     first break in the numbering, wider than twice everything below it.
    if initial_run:
        base, contig_top = initial_run
        n_below = contig_top - base + 1
        n_missing = s_hi - s_lo - 1
        if (s_lo == contig_top and n_missing >= _WIDE_RESTART_FLOOR
                and n_missing > _WIDE_RESTART_FACTOR * n_below
                and _within_one_sitting(t_lo, t_hi)):
            return True
    return False


def _classify_gap(lo_row, hi_row, repairs, watermarks, present=frozenset(),
                  rows_above=None, newest_below=None, initial_run=None):
    """`(classification, plain-English reason, when_datetime_or_None)` for
    one run of missing
    numbers. `lo_row` / `hi_row` are `(file_index, seq, datetime_or_None)`
    for the last row below the hole and the first row above it;
    `repairs` are `_repair_evidence` triples; `present` is every number the
    log still holds.

    FIX ROUND 2 (F-10). Every benign verdict below rests on something the log
    SAYS, never on something it fails to say. If no positive evidence covers
    the hole, the answer is `removed` — the class that gets spoken about."""
    i_lo, s_lo, t_lo = lo_row
    i_hi, s_hi, t_hi = hi_row
    missing = set(range(s_lo + 1, s_hi))

    # 1. The numbering visibly starts over (see `_restarts_here` for the three
    #    things that have to be true at once). A stretch cut out of the middle
    #    of history satisfies none of them.
    if _restarts_here(lo_row, hi_row, present, rows_above, newest_below,
                      initial_run):
        return (GAP_RENUMBERED,
                "the numbering started over here — a fresh unbroken run of "
                "numbers begins in the past, and the entry before it is the "
                "latest the old numbering ever reached",
                t_hi)

    # 2. A recorded repair NAMES these numbers. The strongest evidence, and
    #    the only one that needs no time window: every missing number is on
    #    the list the repair wrote down.
    for seqs, _count, dt, _own in repairs:
        if seqs and missing <= seqs:
            return (GAP_REPAIR,
                    "a repair wrote down the exact entries it set aside and "
                    "these are on that list",
                    dt)

    # 3. A repair that ran BEFORE the numbers were written down. Bounded on
    #    three sides so proximity alone can never clear a hole again: it must
    #    have run INSIDE this hole's own window, it must have set aside at
    #    least as many lines as are missing, and every missing number must be
    #    BELOW the repair's own number.
    #
    #    FIX ROUND 3 (review finding F-17). Without the third bound the window
    #    collapsed to plain adjacency whenever the repair receipt was itself
    #    one of the two rows beside the hole: `t_lo <= dt <= t_hi` is
    #    satisfied for free when `dt` IS one of the bounds, so deleting
    #    exactly `count` rows immediately ABOVE any historical receipt was
    #    silenced — up to 56 rows either side of one receipt on the operator's
    #    book. A repair sets lines aside and then appends its receipt, so the
    #    numbers it took were allocated before its own: a hole ABOVE the
    #    receipt is not a hole the receipt can account for. The half below it
    #    stays, because that is the real shape of a repair.
    for seqs, count, dt, own in repairs:
        if seqs or count is None or dt is None:
            continue
        if t_lo is None or t_hi is None:
            continue
        if own is None or s_hi - 1 >= own:
            continue
        if len(missing) <= count and t_lo <= dt <= t_hi:
            return (GAP_REPAIR,
                    "a repair ran between the entries either side of this "
                    "stretch, set aside at least this many lines, and was "
                    "written down after them",
                    dt)

    # 4. A shorter copy of the log was noticed and recorded, and this stretch
    #    ends EXACTLY at the maximum that copy could see (SPEC SYNC1 A1).
    for wm in watermarks:
        if wm == s_hi - 1:
            return (GAP_REPAIR,
                    "a shorter copy of the log was noticed and written down, "
                    "and this stretch ends exactly where that copy did",
                    t_hi)

    return (GAP_REMOVED,
            "nothing in the log accounts for these numbers, so they were "
            "written and then taken out",
            t_lo)


def scan_gaps(workspace_root) -> List[dict]:
    """Every run of MISSING human-counter seq numbers across the full
    history, oldest first, each one CLASSIFIED:

        [{"gap_after", "gap_before", "n_missing", "classification",
          "because", "when", "when_sort"}, ...]

    FIX ROUND 4 (review finding F-25). Round 2 also put `writer_below` and
    `writer_above` on every run, "recorded for whoever is diagnosing a hole".
    Nothing ever read them — not the marker, not a surface, not a test — and
    on the operator's own book the writers differ across the one real
    re-basing, so they could not have been promoted into evidence either
    without turning 732 explained numbers into an accusation. A field nothing
    reads and nothing pins is the shape G29 exists to catch, so they are gone.

    Pure read, shard-transparent (`events_io.iter_events`), defensive on
    malformed lines. Nano-epoch artifacts (>= EPOCH_THRESHOLD) are excluded
    per the `next_seq` contract, and so is everything below the ledger's own
    lowest number — a workspace whose history starts at 900 has no hole at 1.

    THE THREE CLASSES (fix round 2, F-10 — every benign one needs POSITIVE
    evidence; absence of evidence is never a reason to stay quiet):

      renumbered  The numbering VISIBLY restarts: the timestamps run
                  backwards across the boundary AND a new contiguous run of
                  numbers begins at it. Where the boundary's own rows have
                  been taken out, a first break wider than twice the whole
                  history below it counts too — but only when the two rows
                  that survive it were written within a day of each other,
                  which a re-basing is and a mass deletion across months is
                  not (fix round 4, F-23).
      repair      A recorded repair NAMES the numbers it set aside and this
                  stretch is inside that list — the event's own
                  `quarantined_seqs`, or the quarantine sidecar it points at,
                  which holds the lines themselves. For repairs written
                  before the numbers were recorded, the fallback is bounded
                  on three sides: the repair RAN inside this stretch's own
                  time window, set aside at least as many lines as are
                  missing, AND carries a number above every missing one
                  (fix round 3, F-17). Also: a substrate-regression record
                  (`.seqregression.json`, SPEC SYNC1 A1) whose file maximum
                  is EXACTLY the last missing number, so the stretch ends
                  where the shorter copy did.
      removed     Everything else. Written, then taken out.

    Every run is reported, whatever its class — `gap_notice` says one plain
    sentence per class and the marker makes each one say it once. Round 1
    classified four fifths of the live book benign and rendered none of it
    anywhere, so the only way to see it was to run this script by hand.

    SHARDS: reads every shard through `events_io.iter_events`, never the
    active file alone. A5 rotation moves prior-year events into
    `events-<year>.jsonl` siblings, so an active-file-only scan on a rotated
    workspace would report the whole of history as one enormous hole.
    """
    from events_io import iter_events
    from event_time import event_dt

    first: Dict[int, tuple] = {}
    last: Dict[int, tuple] = {}
    repairs: List[tuple] = []
    idx = 0
    for ev in iter_events(workspace_root):
        if not isinstance(ev, dict):
            continue
        idx += 1
        if ev.get("type") in _REPAIR_TYPES:
            repairs.append(_repair_evidence(ev, workspace_root))
        seq = ev.get("seq")
        if not (isinstance(seq, int) and not isinstance(seq, bool)
                and 0 < seq < EPOCH_THRESHOLD):
            continue
        row = (idx, seq, event_dt(ev))
        first.setdefault(seq, row)
        last[seq] = row
    if len(last) < 2:
        return []
    watermarks = _regression_watermarks(workspace_root)
    present = set(last)
    ordered = sorted(last)
    # The newest time the numbering had reached at each number — one of the
    # three things a real restart has to show (`_restarts_here`).
    newest_at: Dict[int, object] = {}
    running = None
    for s in ordered:
        t = last[s][2]
        if t is not None and (running is None or t > running):
            running = t
        newest_at[s] = running
    # The unbroken run the numbering starts with — how many entries it had
    # ever produced before its first break. F-18's one remaining handle on a
    # boundary whose own rows are gone.
    contig_top = ordered[0]
    for s in ordered[1:]:
        if s != contig_top + 1:
            break
        contig_top = s
    initial_run = (ordered[0], contig_top)
    runs: List[dict] = []
    for lo, hi in zip(ordered, ordered[1:]):
        if hi - lo <= 1:
            continue
        klass, because, when_dt = _classify_gap(
            last[lo], first[hi], repairs, watermarks, present,
            rows_above=first, newest_below=newest_at.get(lo),
            initial_run=initial_run)
        runs.append({"gap_after": lo, "gap_before": hi,
                     "n_missing": hi - lo - 1,
                     "classification": klass, "because": because,
                     # `when` is what a person reads; `when_sort` is how the
                     # notice finds the earliest of several stretches.
                     "when": _human_date(when_dt),
                     "when_sort": (when_dt.isoformat()
                                   if when_dt is not None else "")})
    return runs


def _detect_gaps(workspace_root) -> dict:
    """Pure read half of `detect_and_mark_gaps` — the classified gap scan and
    the already-marked dedup, with nothing written. Callers on the mark path
    hold `events_writer_lock` around this (same read-decide-append hazard the
    duplicate half documents in FX-2).

    FIX ROUND 2 (F-10d). `new` carries EVERY unmarked run, whatever its
    class, and `gap_notice` says one sentence per class. Round 1 put the
    benign classes in `by_class` and called that "in the report for whoever
    set the workspace up" — but nothing rendered `by_class` on any surface, so
    four fifths of the live book's stretches were visible only to somebody who
    ran this script by hand. Everything that happened to the log is now said
    once, in the words that fit what it was."""
    from events_io import iter_events

    runs = scan_gaps(workspace_root)
    removed = [r for r in runs if r["classification"] == GAP_REMOVED]

    from event_time import event_dt

    already_marked = set()
    # FIX ROUND 3 (review finding F-16). When a removal was FIRST noticed —
    # the date on the earliest marker for a stretch nothing accounts for. The
    # standing line on the on-demand health check needs it, because after the
    # one Monday note the news is spent and the fact is not.
    first_noticed_dt = None
    for ev in iter_events(workspace_root):
        if not isinstance(ev, dict) or ev.get("type") != "seq_gap_marked":
            continue
        d = ev.get("data") or {}
        a, b = d.get("gap_after"), d.get("gap_before")
        if isinstance(a, int) and isinstance(b, int):
            already_marked.add((a, b))
        if d.get("classification") == GAP_REMOVED:
            dt = event_dt(ev)
            if dt is not None and (first_noticed_dt is None
                                   or dt < first_noticed_dt):
                first_noticed_dt = dt

    new = [r for r in runs
           if (r["gap_after"], r["gap_before"]) not in already_marked]
    new_removed = [r for r in new if r["classification"] == GAP_REMOVED]
    by_class = {k: {"n_runs": 0, "n_missing": 0} for k in GAP_CLASSES}
    for r in runs:
        slot = by_class.setdefault(r["classification"],
                                   {"n_runs": 0, "n_missing": 0})
        slot["n_runs"] += 1
        slot["n_missing"] += r["n_missing"]
    return {
        "n_gaps": len(runs),
        "n_missing": sum(r["n_missing"] for r in runs),
        "n_removed": len(removed),
        "n_removed_missing": sum(r["n_missing"] for r in removed),
        "by_class": by_class,
        "runs": runs,
        "n_new": len(new),
        "n_new_missing": sum(r["n_missing"] for r in new),
        "n_new_removed": len(new_removed),
        "n_new_removed_missing": sum(r["n_missing"] for r in new_removed),
        "new": new,
        "first_noticed": _human_date(first_noticed_dt),
        "marked": False,
    }


def _empty_gap_report(refused=None) -> dict:
    out = {"n_gaps": 0, "n_missing": 0, "n_removed": 0,
           "n_removed_missing": 0,
           "by_class": {k: {"n_runs": 0, "n_missing": 0} for k in GAP_CLASSES},
           "runs": [], "n_new": 0, "n_new_missing": 0,
           "n_new_removed": 0, "n_new_removed_missing": 0,
           "new": [], "first_noticed": "", "marked": False}
    if refused:
        out["refused"] = refused
    return out


def detect_and_mark_gaps(workspace_root, *, apply: bool = False,
                         source_skill: str = "seq_health") -> dict:
    """Scan for runs of missing seq numbers; report every NEW one — of any
    class — not yet covered by a `seq_gap_marked` marker. With apply=True,
    append one additive marker per new run so the next run treats it as known
    and its class line is said once, not every Monday.

    Returns {"n_gaps", "n_missing", "n_removed", "n_removed_missing",
    "by_class", "runs", "n_new", "n_new_missing", "n_new_removed",
    "n_new_removed_missing", "new", "marked"}. Same lock
    and phantom-path discipline as `detect_and_mark`: the whole
    read-decide-append runs inside `writer_lock.events_writer_lock` and the
    report is re-derived in there; a missing events.jsonl is refused BEFORE
    the lock is taken so a mistyped root never grows a substrate tree.
    """
    events_path = _events_path(workspace_root)
    if not events_path.exists():
        return _empty_gap_report(
            refused=f"no events.jsonl under {str(workspace_root)!r}")
    if not apply:
        return _detect_gaps(workspace_root)
    from writer_lock import events_writer_lock
    with events_writer_lock(events_path, holder=source_skill):
        report = _detect_gaps(workspace_root)  # RE-DERIVED inside the lock
        if report["new"]:
            from atomic_write import atomic_append_jsonl
            atomic_append_jsonl(events_path, [{
                "type": "seq_gap_marked",
                "source_skill": source_skill,
                "data": {"gap_after": entry["gap_after"],
                         "gap_before": entry["gap_before"],
                         "n_missing": entry["n_missing"],
                         "classification": entry["classification"],
                         "detected_by": "seq_health"},
            } for entry in report["new"]], holder=source_skill)
            report["marked"] = True
        return report


def _class_line(klass: str, n_runs: int, n_entries: int, when: str) -> str:
    """One class, one plain sentence. No number, path, spec code or plumbing
    word from the log itself — a count, a date, and what it means."""
    entries = "entry" if n_entries == 1 else "entries"
    on = f" on {when}" if when else ""
    since = f" since {when}" if when else ""
    if klass == GAP_REMOVED:
        where = ("one stretch of your activity log" if n_runs == 1
                 else f"{n_runs} stretches of your activity log")
        first = "" if n_runs == 1 else ", the earliest"
        # FIX ROUND 3 (review finding F-21, M's ruling 1). Round 2 said "a
        # chat removed them" — a cause the log cannot decide. It is true for
        # the twelve entries of 2026-09-07; it is almost certainly false for
        # ten numbers three rows 23 microseconds apart burned inside one
        # append, undecidable for nineteen across a change of writer, and
        # contradicted by the product's own quarantine file for one more. The
        # CLASS is honest — nothing in the log accounts for these — so the
        # sentence says that, and says the two things it can still stand
        # behind: the log is only ever added to, and nothing can put them
        # back. No hedge-word, same length.
        return (
            f"⚠ {n_entries} {entries} are missing from {where}"
            f"{first}{since}, and nothing in the log accounts for them. "
            f"Command Room only ever adds to that log — something outside it "
            f"took them out, or they were never written — and nothing can "
            f"put them back. Mention it to whoever set up your Command Room."
        )
    if klass == GAP_REPAIR:
        if n_runs == 1:
            return (f"{n_entries} {entries} were set aside by a repair{on}. "
                    f"They are saved, not lost, and nothing is wrong.")
        return (f"{n_entries} {entries} were set aside by {n_runs} repairs, "
                f"the first{on}. They are saved, not lost, and nothing is "
                f"wrong.")
    if klass == GAP_RENUMBERED:
        # FIX ROUND 3 (review finding F-18). "Nothing is missing" was an
        # overclaim at the one place it matters: an entry taken out right at
        # the boundary leaves the same trace the re-basing does, so the
        # restart absorbs it either way. The line says the numbers in between
        # were never used — which is what a restart means — and then says
        # plainly what it cannot see.
        tail = ("The numbers in between were never used. An entry taken out "
                "right at that point would look the same, so that one spot "
                "is the only place this check cannot see.")
        if n_runs == 1:
            return f"The numbering in your activity log started over{on}. {tail}"
        return (f"The numbering in your activity log started over {n_runs} "
                f"times, the first{on}. {tail}")
    return ""


def gap_notice(report: dict) -> str:
    """What cleanup's Monday note and system-health's self-report say about
    stretches of missing numbers — ONE plain sentence per class, most serious
    first, composed here rather than in prose so both surfaces say the same
    words and neither can soften them.

    Empty string when there is nothing new. A marked stretch is known, not
    news: `detect_and_mark_gaps(apply=True)` marks every class, so each
    sentence is said once and never again on the same stretches.

    FIX ROUND 2 (F-10). Round 1 spoke only about `removed` and rendered the
    rest nowhere at all, which made the report's own claim to be visible
    untrue. Every class now reaches the surface in the words that fit it: a
    restart says nothing is missing, a repair says the entries are saved, and
    a removal says what it is and that nothing can undo it."""
    new = [r for r in ((report or {}).get("new") or []) if isinstance(r, dict)]
    if not new:
        return ""
    lines = []
    for klass in GAP_CLASSES:
        runs = [r for r in new if r.get("classification") == klass]
        if not runs:
            continue
        n_entries = sum(int(r.get("n_missing") or 0) for r in runs)
        earliest = sorted(
            (r for r in runs if r.get("when")),
            key=lambda r: str(r.get("when_sort") or "9999"))
        line = _class_line(klass, len(runs), n_entries,
                           str(earliest[0]["when"]) if earliest else "")
        if line:
            lines.append(line)
    return "\n".join(lines)


def gap_standing_line(report: dict) -> str:
    """The ONE line the on-demand health check keeps saying about entries
    nothing accounts for — after the news is spent.

    FIX ROUND 3 (review finding F-16). `gap_notice` says a stretch once and
    the marker makes sure it is never said again, which is right for a
    restart and right for a repair: they happened, they are explained, and
    repeating them every Monday is noise. It is the wrong shape for a removal.
    HEALTH1's reviewer found exactly this for duplicate numbers — marked once,
    reported nowhere ever after — and HEALTH1's answer was a standing line.
    An entry that cannot be put back is the stronger case for one: a customer
    who was away on the one Monday it was said would never learn of it.

    Where it goes (M's ruling 2, default as built): the ON-DEMAND health check
    only. The Monday note stays once-only, so no scheduled surface grows and
    no touch is added; the morning brief never sees any of it. That is kept by
    code: the `--gaps` CLI — which is the command the Monday pass runs — does
    not put this line in its payload at all, so the note cannot render it
    however its prose is later edited (fix round 4, review finding F-27).

    Empty when nothing has been removed, and empty while `gap_notice` is
    still saying it — the two lines are composed here together so neither
    surface can print both."""
    rep = report or {}
    n = int(rep.get("n_removed_missing") or 0)
    k = int(rep.get("n_removed") or 0)
    if n <= 0 or k <= 0:
        return ""
    if int(rep.get("n_new_removed") or 0) > 0:
        return ""  # the news line is saying it right now
    entries = "entry" if n == 1 else "entries"
    where = ("one stretch" if k == 1 else f"{k} stretches")
    when = str(rep.get("first_noticed") or "")
    since = f", first noticed {when}" if when else ""
    return (f"Your activity log still has {n} {entries} missing from {where} "
            f"that nothing in it accounts for{since}; nothing can put them "
            f"back.")


def main(argv) -> int:
    if len(argv) < 2:
        print("usage: seq_health.py <workspace_root> [--mark] [--gaps]",
              file=sys.stderr)
        return 2
    import json
    flags = argv[2:]
    apply = "--mark" in flags
    if "--gaps" in flags:
        report = detect_and_mark_gaps(argv[1], apply=apply)
        report["notice"] = gap_notice(report)
        # FIX ROUND 4 (review finding F-27). The standing line is NOT in this
        # payload. `--gaps --mark` is the weekly maintenance pass's own
        # command, and its note renders what this JSON carries: on a Monday
        # whose only new stretch is a repair or a restart, `notice` speaks and
        # `gap_standing_line` returns its sentence — so the scheduled surface
        # was one prose instruction away from growing by a line, which is the
        # one thing ruling 2's default promises it will not do. The standing
        # line belongs to the on-demand health check, and that surface imports
        # `gap_standing_line` directly. A rule kept by code, not by prose.
    else:
        report = detect_and_mark(argv[1], apply=apply)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
