#!/usr/bin/env python3
"""
Clock trust (SPEC CLOCK1, 2026-08-04) — corroborate the machine clock before
anything date-relative is computed from it.

THE DEFECT THIS EXISTS FOR
--------------------------
A scheduled fire ran on a sandbox machine whose clock had not synced at boot
and read two days behind. The machine clock is a single, uncorroborated input
to every date-relative computation in every fire, so one wrong reading produced
four user-visible harms at once:

  1. a fabricated on-time lateness receipt (the run was scored against a slot
     the stale clock believed had just passed);
  2. a two-day-old calendar surfaced as "today", so a meeting that had already
     happened was presented as upcoming;
  3. a weekday-morning surface framed itself as a weekday morning on a Sunday
     night;
  4. the permanent one — the event append gate stamped `ts` from that clock, so
     the fire wrote two-day-old timestamps into the ledger. Those events fall
     out of every "since yesterday" window from then on. Silent data loss, not
     a display bug.

The incident proved its own remedy: three sources disagreed with the machine
and agreed with each other — the newest timestamp already in the workspace
ledger, the session's stated date, and the newest connector message. This
module implements the first two. Connector reads are unavailable and expensive
inside code helpers, so the third is logged as future work and not built.

WHAT THIS IS AND IS NOT — READ BEFORE EDITING
---------------------------------------------
This is a STALENESS check. It is NOT a timezone correction.

`references/HOW_COMMAND_ROOM_WORKS.md` § "Scheduling timezone rule (R8)" stands
untouched: cron and fireAt evaluate in MACHINE-local time, lateness and
fired-recency math stay machine-local naive, the workspace timezone is
presentation-only, and conversion happens ONCE at registration time. Nothing
here changes any of that.

  **This module corrects WHICH INSTANT it is. It never corrects WHICH ZONE that
  instant is expressed in.**

`trusted_now_local_naive()` returns the corrected instant in the MACHINE's own
zone, naive — a drop-in for `datetime.now()` that moves the instant and leaves
the clock it is expressed in exactly where it was. If you find yourself routing
a fire time, a cron slot, or a lateness value through the workspace timezone,
stop: that is the bug R8 was written to prevent, and it made CI red for eight
consecutive pushes the last time someone tried it.

The one place a workspace-zone conversion is correct is `today` — the calendar
day a SURFACE frames its content against. That is presentation, it already ran
through the existing "compute today's date in local time via tz.py" contract,
and this module now supplies the instant that contract always assumed.

THE CORROBORATION RULE
----------------------
Substrate can prove exactly one thing: that the clock reads EARLIER than
something which already happened. It cannot prove a clock is fast — a
future-dated stamp is indistinguishable from a real one to a past-facing
ledger. The two directions are therefore handled asymmetrically, deliberately:

  STALE   `machine_now < newest_substrate_ts - 300s` -> provably stale. The
          corrected instant is the newest substrate timestamp: the FLOOR.
          Whatever is happening now is happening at or after the newest thing
          already known. Honest as a bound, and the same principle the
          lifecycle pass uses for an unreadable answer stamp.

  AHEAD   Detectable only from the session date, only at day granularity, and
          only where a surface can supply one. Sub-day time is NOT rewritten;
          there is no basis for a finer correction than the date itself. Where
          no session date exists — the append gate, every pure-code caller —
          ahead-ness is undetectable and accepted. A fast clock keeps writing
          future stamps until a surface fire with a session date catches the
          day-level skew. Nothing here pretends otherwise.

WHAT THE SESSION DATE IS ASSUMED TO BE
--------------------------------------
The session date is taken as **the user's local, day-granular date**. It is not
a UTC date and it carries no time. Two consequences are load-bearing:

  - It may only arbitrate DAY-level disagreements. Sub-day staleness is settled
    by the ledger alone, session date present or not.
  - Near midnight the same instant renders as two different calendar days
    depending on which zone renders it, so the AHEAD flag requires the machine
    instant's day to exceed the session day in BOTH the machine's zone AND the
    workspace's. When the two renderings disagree, this helper stays SILENT
    rather than accuse a correct clock of running a day fast.

FAIL-SAFE POSTURE, IN EVERY DIRECTION
-------------------------------------
Corroboration is best-effort and must never block a run. An unresolvable
workspace, an absent or unreadable ledger, zero parseable timestamps, a garbage
session date: all of them mean "cannot corroborate", which means trust the
machine and say nothing. A brand-new workspace behaves exactly as it did before
this module existed. Nothing in here raises.

THE CONTAMINATION FEEDBACK LOOP, AND THE GUARDS ON IT
-----------------------------------------------------
If the substrate maximum is itself a future-dated stamp, floor-stamping
perpetuates it: the corrected row carries the same maximum, so it re-poisons
the next append, self-sustaining until real time passes the seed.

THIS MODULE ONCE CALLED THAT SELF-LIMITING. It is not, and the claim is left
here as the correction it earned. "Self-limiting" is true only at minutes
scale. A seed the clock will not reach for hours or days keeps firing for
hours or days, and the field found it at both: 1,242 rows from a week-scale
seed in one workspace, 24 rows from a 2.4-hour rounded placeholder in another.
Neither the DAY-granular anomaly guard nor a lead cap could have caught the
second one.

Two guards now stand on it, and they answer different questions:

  THE SESSION-DATE ANOMALY GUARD fires on exactly one shape — the ledger
  claiming a future DAY that neither the machine nor the session date
  supports, in BOTH zone renderings. Anything narrower let a real correction
  be suppressed; anything wider let the contamination be corrected onto. It
  needs a session date, which a pure-code append never has, so it can only
  ever cover the surfaces. Documented clause by clause in `assess_clock`.

  THE `.seqhw` DISCRIMINATOR (CLOCKTS1) needs nothing but the substrate and
  covers every caller, surface or not. `.seqhw.updated` is stamped from the
  RAW machine clock on every append, so it witnesses when the last write
  really happened: a stale clock leaves `newest_ts ~= .seqhw.updated`, while a
  forward-dated payload leaves `newest_ts` leading BOTH. One comparison, no
  scale to tune. See `classify_substrate_max`, which both stamp sites call.

CACHING
-------
The verdict is computed once per (workspace, session date) and cached, because
the corroboration read parses the whole ledger and roughly twenty writer
helpers call this on their hot path. What is cached is an ABSOLUTE floor plus a
`time.monotonic()` anchor — never an offset. See `_advancing_floor` for why
that distinction is the difference between a self-healing clock and a process
that stamps the future for the rest of its life.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import time as _time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_time import parse_ts  # noqa: E402

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# How far apart two clocks may read before the difference is evidence rather
# than noise. This is the SAME 300 seconds the connector-timestamp sanity check
# already uses (`contact_capture.CLOCK_SKEW_TOLERANCE_SECONDS`, and the
# independent literal in `slack_capture`). It is deliberately re-stated here
# rather than imported: those modules sit high in the import graph and this one
# is reached from the append gate, so importing them would knot the graph for a
# number. `run_clock1_test.py` asserts the two values stay equal, so the
# duplication cannot drift into two different thresholds. Do not move either.
CLOCK_TRUST_TOLERANCE_SECONDS = 300

# A session date this far from the machine's own day is evidence the machine is
# ahead. One full calendar day: the session date is a DATE, so a finer bar
# would be reading precision into a value that does not carry it.
AHEAD_MIN_DAYS = 1

# CLOCKTS1 — the BACKSTOP cap, used ONLY where the `.seqhw` witness cannot be
# read. It is deliberately coarse, and it is not the mechanism: the witness is.
#
# WHY NOT THE 24-48h A CAP-ONLY FIX WOULD WANT. The incident THIS MODULE was
# built for was a machine that booted unsynced and read TWO DAYS behind. A cap
# at 24h or 48h refuses exactly that correction — the fix eating the feature.
# And a cap tight enough to catch the everyday hours-scale forward date would
# refuse essentially every real correction, which is why the triage rejected a
# cap as the fix rather than as the backstop.
#
# FOUR DAYS is chosen from the two real data points, not split between them:
# strictly above the 2-day unsynced-boot skew that is documented and real, and
# strictly below the 7-day and 3-week seeds observed in the field. It also
# matches how machine clocks actually fail — NTP drift is seconds, a timezone
# or DST mistake is hours, an unsynced RTC boot is days, and a dead CMOS resets
# to a manufacture epoch YEARS BEHIND, which is a different branch entirely. A
# clock running days-to-weeks AHEAD of a ledger is not a failure mode hardware
# produces; it is a row someone wrote.
TS_LEAD_CAP_DAYS = 4
TS_LEAD_CAP_SECONDS = TS_LEAD_CAP_DAYS * 86400

# The named reasons a substrate maximum is refused as clock evidence. These
# travel into the verdict's `anomaly` field, into the append gate's stderr, and
# into the health line, so they are pinned words, not incidental strings.
FLOOR_REFUSED_FORWARD_DATED = "substrate_max_leads_seqhw"
FLOOR_REFUSED_BEYOND_CAP = "substrate_max_beyond_lead_cap"

# The two disclosure lines, verbatim. PIN THE WORDS, NOT THE CONSTANT — a test
# that asserts `rendered == CLOCK_NOTICE_STALE` is green whatever the constant
# says, which is how a silent substitution ships. `run_clock1_test.py` asserts
# the load-bearing phrases and that both times actually appear.
CLOCK_NOTICE_STALE = (
    "Clock notice: this computer's clock reads {machine}, earlier than the "
    "newest activity already recorded in this workspace ({corroborated}). "
    "Dates and times in this run came from the workspace record, not the "
    "machine clock."
)
CLOCK_NOTICE_AHEAD = (
    "Clock notice: this computer's clock reads {machine_date}, ahead of the "
    "session date ({env_date}). Dates in this run came from the session date, "
    "not the machine clock."
)

# The annotation a floor-corrected event carries. Additive fields only: every
# reader takes `ts` first and ignores what it does not know, so fossil adapters
# keep parsing.
TS_SOURCE_SUBSTRATE_FLOOR = "substrate_floor"

_ENTITIES_REL = Path("_hq") / "data" / "entities.json"

# --------------------------------------------------------------------------
# Process state
# --------------------------------------------------------------------------

# {cache_key: verdict}. Cleared by reset_clock_trust_cache().
_VERDICT_CACHE: dict = {}

# {explicit-root-or-"": resolved Path or None}. The swept writer helpers call
# through here on every stamp, and resolution walks the filesystem, so it is
# memoized too. Dropped whenever the verdict cache is dropped or a new
# workspace is registered.
_ROOT_CACHE: dict = {}

# The workspace this process is working in, learned from whichever caller
# resolved one first. The writer helpers swept by CLOCK1 stamp through
# no-argument `_now_iso()` functions that cannot be given a workspace without
# rewriting fifty call sites, so the append gate and the lateness check — both
# of which DO know the root — register it here on the way past.
_REGISTERED_WORKSPACE: list = [None]


def register_workspace(workspace_root) -> None:
    """Record the workspace this process is operating on. Never raises."""
    try:
        root = _canonical(Path(workspace_root))
        if (root / _ENTITIES_REL).is_file():
            _REGISTERED_WORKSPACE[0] = root
            _ROOT_CACHE.clear()
    except Exception:
        pass


def register_workspace_from_data_path(path) -> None:
    """Same, given a path INSIDE the substrate (`.../_hq/data/events.jsonl`)."""
    try:
        parent = workspace_root_for_path(path)
        if parent is not None and _REGISTERED_WORKSPACE[0] != parent:
            _REGISTERED_WORKSPACE[0] = parent
            _ROOT_CACHE.clear()
    except Exception:
        pass


def reset_clock_trust_cache() -> None:
    """Drop the per-process verdict cache. For tests and long-lived processes
    that legitimately move between workspaces."""
    _VERDICT_CACHE.clear()
    _ROOT_CACHE.clear()
    _REGISTERED_WORKSPACE[0] = None


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def _resolve_workspace(workspace_root=None) -> Optional[Path]:
    """Explicit argument -> CR_WORKSPACE -> the registered hint -> walk up from
    the working directory. None when nothing resolves, which is a legitimate
    answer meaning "cannot corroborate"."""
    key = str(workspace_root) if workspace_root else ""
    if key in _ROOT_CACHE:
        return _ROOT_CACHE[key]
    _ROOT_CACHE[key] = _resolve_workspace_uncached(workspace_root)
    return _ROOT_CACHE[key]


def _resolve_workspace_uncached(workspace_root=None) -> Optional[Path]:
    candidates = []
    if workspace_root:
        candidates.append(Path(workspace_root))
    env = os.environ.get("CR_WORKSPACE", "").strip()
    if env:
        candidates.append(Path(env))
    if _REGISTERED_WORKSPACE[0] is not None:
        candidates.append(_REGISTERED_WORKSPACE[0])
    for candidate in candidates:
        try:
            root = candidate.expanduser()
            if (root / _ENTITIES_REL).is_file():
                return _canonical(root)
        except Exception:
            continue
    try:
        here = Path.cwd().resolve()
        for root in (here, *here.parents):
            if (root / _ENTITIES_REL).is_file():
                return _canonical(root)
    except Exception:
        pass
    return None


def _canonical(root: Path) -> Path:
    """ONE spelling of a workspace path, everywhere.

    The verdict cache is keyed on the workspace path as a STRING, and the
    append gate's suppression looks that key up after deriving its own root
    from the events path — which it resolves. If the two sides canonicalize
    differently, the key written is not the key read, the lookup misses, and
    the suppression silently does nothing. Silently is the operative word:
    every test passed, because a test that hands over an already-canonical
    path can never see it.

    A path is non-canonical more often than it looks: a Windows 8.3 short name
    (`C:\\Users\\RUNNER~1\\...`, which is what a CI runner's temp directory is),
    a symlink or junction, a mapped drive, `/var` against `/private/var` on
    macOS, or any spelling containing `..`. Resolve on BOTH sides so the two
    can never disagree. Falls back to the unresolved path if resolution fails,
    which is still better than half-resolving.
    """
    try:
        return root.resolve()
    except Exception:
        return root


def workspace_root_for_path(path) -> Optional[Path]:
    """The workspace root containing `path`, or None. Never raises.

    For callers that hold a path INSIDE the substrate (an events.jsonl, a data
    file) rather than a root — several readers take one and nothing else.
    """
    try:
        for parent in Path(path).resolve().parents:
            if (parent / _ENTITIES_REL).is_file():
                return _canonical(parent)
    except Exception:
        pass
    return None


def _events_path(workspace_root: Path) -> Path:
    try:
        import data_root

        return data_root.resolve(workspace_root) / "events.jsonl"
    except Exception:
        return workspace_root / "_hq" / "data" / "events.jsonl"


def newest_substrate_ts(workspace_root=None) -> Optional[_dt.datetime]:
    """The MAXIMUM parseable timestamp across the ledger, aware UTC.

    A max, not a tail read: events are seq-ordered, not ts-ordered, and a
    rotation or a merge can leave the newest stamp anywhere in the file. Garbage
    lines are skipped rather than fatal — the defensive-reader posture every
    other reader in the product takes. All three live timestamp spellings are
    honoured via `event_time.parse_ts` on the `ts` -> `timestamp` -> `date`
    priority order. Returns None when there is nothing to corroborate against.
    """
    root = _resolve_workspace(workspace_root)
    if root is None:
        return None
    path = _events_path(root)
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return max_ts_in_jsonl_text(text)


def max_ts_in_jsonl_text(text: str) -> Optional[_dt.datetime]:
    """The maximum parseable event timestamp in raw JSONL text, aware UTC.

    Kept as a standalone verb for readers and tests. The append gate does NOT
    call this — it calls `scan_jsonl_text`, which computes the newest timestamp
    and the maximum seq in ONE json.loads pass. Two passes over a multi-megabyte
    ledger measured +36ms on every single append, which is a real cost paid for
    nothing.
    """
    return scan_jsonl_text(text)[0]


def scan_jsonl_text(text: str, *, epoch_threshold: int = 10**10):
    """One pass, two answers: `(newest_ts_or_None, max_human_counter_seq)`.

    The seq half mirrors `next_seq.py`'s contract exactly — ignore non-dict,
    non-numeric, boolean and nano-epoch (>= 1e10) seqs — because the append
    gate's stamping arithmetic depends on that contract and a second, subtly
    different implementation of it would be a bug generator. The timestamp half
    honours all three live spellings on the `ts` -> `timestamp` -> `date`
    priority order. Garbage lines are skipped, never fatal.
    """
    import json

    newest = None
    max_seq = 0
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(entry, dict):
            continue
        seq = entry.get("seq")
        if (isinstance(seq, (int, float)) and not isinstance(seq, bool)
                and seq < epoch_threshold and seq > max_seq):
            max_seq = int(seq)
        for field in ("ts", "timestamp", "date"):
            value = entry.get(field)
            if isinstance(value, str) and value.strip():
                parsed = parse_ts(value)
                if parsed is not None and (newest is None or parsed > newest):
                    newest = parsed
                break
    return newest, max_seq


def parse_env_date(env_date) -> Optional[_dt.date]:
    """A session date, parsed tolerantly, or None.

    A model substitutes this into a prose placeholder, so it can arrive as
    ANYTHING — the unsubstituted placeholder itself, an empty string, a
    sentence, a date in a format nobody agreed on. The DOGFIX1 lesson applies
    verbatim: fail-safe means garbage NEVER moves the clock and NEVER blocks a
    run. Anything unrecognised is treated as absent, silently.
    """
    if isinstance(env_date, _dt.datetime):
        return env_date.date()
    if isinstance(env_date, _dt.date):
        return env_date
    if not isinstance(env_date, str):
        return None
    raw = env_date.strip()
    if not raw or "<" in raw or ">" in raw:
        return None  # an unsubstituted placeholder is not a date
    parsed = parse_ts(raw[:10] if len(raw) >= 10 else raw)
    if parsed is None:
        return None
    return parsed.date()


def _workspace_local_date(instant_utc: _dt.datetime,
                          workspace_root: Optional[Path]) -> _dt.date:
    """The calendar day a SURFACE frames its content against.

    This is the ONE workspace-zone conversion in this module, and it is the
    same conversion the orchestrators' existing "compute today's date in local
    time via tz.py" line already performed. Falls back to the machine zone when
    the workspace timezone cannot be resolved — the documented degrade, never
    an exception.
    """
    try:
        import tz as _tz

        localized = _tz.to_local(
            instant_utc,
            workspace_path=str(workspace_root) if workspace_root else None)
        if localized is not None:
            return localized.date()
    except Exception:
        pass
    return instant_utc.astimezone().date()


def _as_zone(machine_zone):
    """A tzinfo for an injected machine zone, or None meaning "the OS zone".

    Accepts a tzinfo or a zone NAME. Injectable so a test can pin the machine
    zone and stop depending on whichever machine happens to run the suite —
    the dependence that made this suite pass on one runner and fail on another.
    Unresolvable names degrade to None (the OS zone) rather than raising, in
    keeping with everything else here.
    """
    if machine_zone is None:
        return None
    if isinstance(machine_zone, _dt.tzinfo):
        return machine_zone
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(str(machine_zone))
    except Exception:
        return None


def _machine_zone_dt(instant_utc: _dt.datetime, machine_zone=None):
    """`instant` as the MACHINE sees it. The OS zone unless one is injected."""
    zone = _as_zone(machine_zone)
    return (instant_utc.astimezone(zone) if zone is not None
            else instant_utc.astimezone())


def _human_clock(instant_utc: _dt.datetime, machine_zone=None) -> str:
    """A machine-local rendering of an instant, for the disclosure line.

    MACHINE-local on purpose (the LATETZ rule): these are lateness-adjacent
    times, and `tz.to_local` is for upstream connector timestamps. Routing one
    of these through it re-expresses a value in a clock it was never authored
    in, which is exactly the mistake R8 forbids.
    """
    local = _machine_zone_dt(instant_utc, machine_zone).replace(tzinfo=None)
    hour = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%A %B')} {local.day} at {hour}"


def _human_day(value) -> str:
    if isinstance(value, _dt.datetime):
        value = value.astimezone().date()
    return f"{value.strftime('%A %B')} {value.day}"


# --------------------------------------------------------------------------
# CLOCKTS1 — is the substrate maximum clock evidence, or a forward-dated row?
# --------------------------------------------------------------------------

def read_seqhw_updated(events_path) -> Optional[_dt.datetime]:
    """The `updated` stamp from the `.seqhw` sidecar, aware UTC, or None.

    THE WITNESS. `atomic_write._write_seqhw` stamps this field from
    `datetime.now(timezone.utc)` — the RAW machine clock, never the corroborated
    floor — on every single events append. So it records when the last append
    really happened, and it cannot be moved by a forward-dated payload the way
    the ledger maximum can.

    Best-effort in both directions: the sidecar is written under `except: pass`
    and may legitimately be absent (a fresh workspace, or a write that failed
    the sidecar and succeeded the log). None means "no witness available", which
    routes the caller to the cap backstop, never to a refusal.
    """
    if events_path is None:
        return None
    try:
        import json

        p = Path(events_path)
        raw = json.loads(
            p.with_name(p.name + ".seqhw").read_text(encoding="utf-8"))
        value = raw.get("updated")
        if not isinstance(value, str) or not value.strip():
            return None
        parsed = parse_ts(value)
        if parsed is None:
            return None
        return parsed.astimezone(_dt.timezone.utc)
    except Exception:
        return None


def classify_substrate_max(newest_ts: Optional[_dt.datetime],
                           machine_now: _dt.datetime,
                           events_path=None) -> dict:
    """Is `newest_ts` evidence the machine clock is behind, or a poisoned row?

    Returns `{"is_clock_evidence", "reason", "seqhw_updated",
    "lead_over_seqhw", "lead_over_machine"}`.

    THE DISCRIMINATOR, and the whole of CLOCKTS1. Two situations produce a
    ledger maximum ahead of the machine clock, and until now nothing told them
    apart:

      GENUINELY STALE CLOCK. The machine reads behind. The newest row AND the
      `.seqhw` witness were both written by a healthy clock at roughly the same
      past instant, so `newest_ts ~= seqhw.updated`. The maximum IS clock
      evidence and the floor correction is exactly right — this is the feature
      CLOCK1 shipped for and it must keep working.

      FORWARD-DATED PAYLOAD. A caller supplied a `ts` ahead of now. The append
      itself really happened NOW, so `seqhw.updated ~= machine_now` while
      `newest_ts` leads BOTH. The maximum is not clock evidence, it is a wrong
      row, and adopting it as a floor re-stamps it onto every later append —
      self-sustaining until real time passes the seed.

    One comparison separates them and it is SCALE-FREE: it catches the reported
    week-scale case and the hours-scale case in the field on the same predicate,
    with no cap to tune and nothing to outgrow.

    Where the sidecar cannot be read there is no witness, so the answer falls
    back to `TS_LEAD_CAP_SECONDS`. That backstop is day-scale and therefore
    strictly weaker: an hours-scale forward date with no sidecar is still
    adopted. Stated rather than hidden — the sidecar is written on every append,
    so the only workspaces without one are those that have never appended, which
    have no maximum to be poisoned by either.

    THE KNOWN LIMIT, STATED BECAUSE IT IS REAL. The witness is the raw machine
    clock, so ONCE A STALE CLOCK HAS ITSELF APPENDED, it overwrites the witness
    with its own wrong reading. From that point the two situations above are
    genuinely identical in the data — `witness ~= machine_now` and `newest_ts`
    leads both — and no predicate over the substrate can separate them. This
    function then answers "not evidence" and the stale machine stamps its own
    reading for the rest of the session.

    That is the deliberate trade, and it is the conservative direction: the
    cost is a stale-clock session writing skew-sized wrong stamps, which is
    precisely the pre-CLOCK1 behaviour and is self-correcting the moment NTP
    lands; the alternative is adopting a maximum that may be a forward-dated
    row, which is permanent and, since the confirmed-tier age-out, drives
    wrong closures. A bounded, self-healing wrong beats an unbounded,
    self-sustaining one. The first append of a stale session — the case the
    reported incident is made of — is still corrected, because the witness is
    still the healthy clock's at that moment.
    """
    lead_over_machine = None
    if newest_ts is not None:
        lead_over_machine = (newest_ts - machine_now).total_seconds()
    out = {
        "is_clock_evidence": True,
        "reason": None,
        "seqhw_updated": None,
        "lead_over_seqhw": None,
        "lead_over_machine": lead_over_machine,
    }
    if newest_ts is None:
        return out

    # THE CAP BINDS FIRST, AND UNCONDITIONALLY (CLOCKTS1 fix round, F-3).
    # It used to sit only in the no-witness branch, so a witness that AGREED
    # with a wrong maximum waved any magnitude through. That is reachable on a
    # Drive-synced pair: a peer machine running three weeks fast writes both
    # the rows AND the witness, they corroborate each other perfectly, and a
    # healthy machine then adopts a floor three weeks in the future — 5x this
    # very constant, whose own docstring says a ledger running days-to-weeks
    # ahead is a row someone wrote, not a clock. Corroboration between two
    # values written by the SAME wrong clock is not corroboration.
    if lead_over_machine is not None and lead_over_machine > TS_LEAD_CAP_SECONDS:
        out["is_clock_evidence"] = False
        out["reason"] = FLOOR_REFUSED_BEYOND_CAP
        out["seqhw_updated"] = read_seqhw_updated(events_path)
        return out

    witness = read_seqhw_updated(events_path)
    if witness is not None:
        out["seqhw_updated"] = witness
        lead = (newest_ts - witness).total_seconds()
        out["lead_over_seqhw"] = lead
        if lead > CLOCK_TRUST_TOLERANCE_SECONDS:
            out["is_clock_evidence"] = False
            out["reason"] = FLOOR_REFUSED_FORWARD_DATED
    return out


def forward_dated_lead(row_ts, row_machine_ts, witness):
    """Was ONE row's timestamp ahead of real time when it was written?

    Returns `(is_forward_dated, lead_seconds, reference)` where `reference` is
    `"machine_ts"` or `"seqhw"` — or `(False, None, None)` when neither
    reference is available.

    WHY THIS EXISTS AS A SHARED VERB (CLOCKTS1 fix round, F-1). The health
    check and the repair tool each compared every row against the SINGLE live
    `.seqhw.updated`. That value is not a per-row record — it is overwritten
    from the raw machine clock on every append, so it ADVANCES WITH REAL TIME.
    A row that was forward-dated when written stops leading it the moment a
    later append moves it past, and both readers then report clean. Measured on
    the operator workspace's own 24-row dose, fourteen days on: 0 detected,
    "nothing to repair". Detection went silent exactly when the corruption
    became permanent, which is the opposite of what a detector is for.

    `machine_ts` is the fix, and it was already on disk: the append gate writes
    it beside `ts_source: substrate_floor`, it records what the machine really
    said for THAT row, and it never moves. Where it is present it is the
    reference, and the answer is durable forever. Where it is absent the moving
    witness is all there is — which still catches fresh contamination, and is
    exactly the site-B residue the repair tool reports rather than hides.
    """
    if row_ts is None:
        return False, None, None
    if row_machine_ts is not None:
        lead = (row_ts - row_machine_ts).total_seconds()
        if lead > CLOCK_TRUST_TOLERANCE_SECONDS:
            return True, lead, "machine_ts"
        # A row with a trail that does NOT lead its own write time is sound,
        # and the moving witness must not be allowed to overrule that: the
        # per-row reading is strictly better evidence than the file-wide one.
        return False, lead, "machine_ts"
    if witness is not None:
        lead = (row_ts - witness).total_seconds()
        if lead > CLOCK_TRUST_TOLERANCE_SECONDS:
            return True, lead, "seqhw"
        return False, lead, "seqhw"
    return False, None, None


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------

def assess_clock(workspace_root=None, *, machine_now=None,
                 env_date=None, machine_zone=None) -> dict:
    """Corroborate the machine clock. Never raises.

    Returns the verdict dict:

      trusted               False only when a direction was PROVEN
      direction             None | "stale" | "ahead"
      machine_now_utc       what the machine says, aware UTC
      corroborated_now_utc  the instant to use, aware UTC
      source                "machine" | "substrate_floor" | "env_date"
      newest_substrate_ts   the corroboration input, or None
      env_date_parsed       the session date, or None
      skew_seconds          corroborated - machine, in seconds (0 when trusted;
                            negative in the ahead direction, where only the DAY
                            moves and the instant does not)
      today                 the calendar day surfaces frame against
      notice                the disclosure line, or None
      anomaly               None | "substrate_ahead_of_session_date"
      floor_utc             the ABSOLUTE corrected instant when stale, else
                            None. Never an offset — see `_advancing_floor`.
      monotonic_at          `time.monotonic()` when this verdict was taken, the
                            anchor the floor advances from
      workspace_root        the resolved root, or None

    `machine_now` and `env_date` are injectable so the whole verdict can be
    exercised from a test without a frozen process clock.
    """
    root = _resolve_workspace(workspace_root)
    if machine_now is None:
        machine = _dt.datetime.now(_dt.timezone.utc)
    elif machine_now.tzinfo is None:
        machine = machine_now.replace(tzinfo=_dt.timezone.utc)
    else:
        machine = machine_now.astimezone(_dt.timezone.utc)

    session_day = parse_env_date(env_date)
    # Only a verdict taken from the REAL machine clock is cacheable; an
    # injected `machine_now` is a caller exploring a hypothetical and must
    # neither read nor write the cache.
    cache_key = (str(root) if root else "",
                 session_day.isoformat() if session_day else "",
                 str(machine_zone) if machine_zone is not None else "")
    if machine_now is None:
        cached = _VERDICT_CACHE.get(cache_key)
        if cached is not None:
            # The verdict is reused as taken. The moving part is the FLOOR, and
            # it advances on the monotonic anchor rather than on this clock —
            # see `_advancing_floor` for why that distinction is the whole
            # point.
            return dict(cached)

    newest = newest_substrate_ts(root) if root is not None else None

    verdict = {
        "trusted": True,
        "direction": None,
        "machine_now_utc": machine,
        "corroborated_now_utc": machine,
        "source": "machine",
        "newest_substrate_ts": newest,
        "env_date_parsed": session_day,
        "skew_seconds": 0.0,
        "today": None,
        "notice": None,
        "anomaly": None,
        "floor_utc": None,
        "monotonic_at": _time.monotonic(),
        "workspace_root": str(root) if root else None,
    }

    substrate_lead = None
    if newest is not None:
        substrate_lead = (newest - machine).total_seconds()

    # FOUR DAY RENDERINGS, because a calendar day is not a property of an
    # instant — it is a property of an instant AND a zone. The workspace zone
    # is the one the session date is stated in (the user reads "Today's date is
    # ..." in their own calendar); the machine zone is the one the scheduling
    # doctrine runs in. Any comparison that silently picks one of them is a
    # comparison that changes its answer near midnight.
    machine_day_in_workspace_zone = _workspace_local_date(machine, root)
    machine_day_in_machine_zone = _machine_zone_dt(machine, machine_zone).date()
    substrate_day_in_workspace_zone = (_workspace_local_date(newest, root)
                                       if newest is not None else None)
    substrate_day_in_machine_zone = (
        _machine_zone_dt(newest, machine_zone).date()
        if newest is not None else None)

    # --- the substrate anomaly guard, before any correction ----------------
    # WHAT THIS IS FOR, stated as the predicate: the ledger claims a future DAY
    # that NEITHER the machine NOR the session date supports. That is a
    # contaminated stamp already sitting in the ledger, and correcting onto it
    # would launder the contamination forward.
    #
    # Every clause earns its place:
    #
    #   - The session date is a DATE, so it may only arbitrate DAY-level
    #     disagreements. SUB-DAY staleness always corrects, session date or
    #     not: a machine six hours behind the newest stamp on the same day is
    #     the reported defect itself. An earlier formulation
    #     (`session_day <= machine_day`) suppressed exactly that whenever a
    #     session date happened to be present — the fence eating the fix.
    #
    #   - "Ahead of the session day" must hold in BOTH zone renderings, the
    #     same both-zones discipline the ahead branch uses. In a far-eastward
    #     workspace a perfectly ordinary ledger renders a day later than the
    #     session date, and a single-zone test read that as contamination and
    #     suppressed a genuine correction.
    #
    #   - "Ahead of the machine's day" in both zones is what makes this about
    #     the LEDGER rather than about the clock. Without it, a machine running
    #     fast past a healthy ledger would be read as ledger contamination.
    #
    # Accepted edge, stated rather than hidden: a ledger only minutes ahead of
    # the machine but across midnight in both zones satisfies this and
    # suppresses a minutes-long correction. The outcome is conservative (trust
    # the machine, change nothing) and the skew being given up is trivial.
    substrate_claims_an_unsupported_future_day = (
        session_day is not None
        and substrate_day_in_workspace_zone is not None
        and substrate_day_in_workspace_zone > session_day
        and substrate_day_in_machine_zone > session_day
        and substrate_day_in_workspace_zone > machine_day_in_workspace_zone
        and substrate_day_in_machine_zone > machine_day_in_machine_zone
    )
    if substrate_claims_an_unsupported_future_day:
        verdict["anomaly"] = "substrate_ahead_of_session_date"
        # Drop the corroboration input rather than returning here. The LEDGER
        # cannot be trusted in this shape, but the session date still can, so
        # the ahead branch below must still get its chance — otherwise a
        # doubly-wrong environment (contaminated ledger AND a machine ahead of
        # the session date) gets its day handed back to the machine clock,
        # which is the harm this helper exists to stop.
        substrate_lead = None

    # --- CLOCKTS1: is that lead clock evidence, or a forward-dated row? -----
    # SITE B, the unannotated one. This branch is reached by ~20 writer helpers
    # through `trusted_now()` / `trusted_now_local_naive()` / `clock_report()`,
    # none of which can hang a `machine_ts` trail on what they stamp — so a
    # correction taken here is invisible after the fact, and a WRONG one is
    # unrecoverable. Bounding it matters more than bounding the append gate,
    # not less.
    #
    # The `.seqhw` witness is read from the events file this verdict is ABOUT,
    # resolved from the same root the corroboration maximum came from. Where
    # there is no root there is no maximum either, so there is nothing to
    # classify.
    if substrate_lead is not None:
        ts_class = classify_substrate_max(
            newest, machine,
            events_path=_events_path(root) if root is not None else None)
        if not ts_class["is_clock_evidence"]:
            # Same posture as the anomaly guard above: drop the corroboration
            # input rather than returning, so the session-date `ahead` branch
            # still gets its chance. Record WHICH refusal fired — the reason is
            # what a health surface and a recovery run read.
            verdict["anomaly"] = ts_class["reason"]
            verdict["newest_substrate_ts"] = newest
            substrate_lead = None

    # --- STALE: the direction substrate can actually prove -----------------
    stale_by_more_than_tolerance = (
        substrate_lead is not None
        and substrate_lead > CLOCK_TRUST_TOLERANCE_SECONDS
    )
    if stale_by_more_than_tolerance:
        verdict["trusted"] = False
        verdict["direction"] = "stale"
        verdict["corroborated_now_utc"] = newest
        verdict["source"] = TS_SOURCE_SUBSTRATE_FLOOR
        verdict["skew_seconds"] = substrate_lead
        verdict["today"] = _workspace_local_date(newest, root)
        verdict["floor_utc"] = newest
        verdict["notice"] = CLOCK_NOTICE_STALE.format(
            machine=_human_clock(machine, machine_zone),
            corroborated=_human_clock(newest, machine_zone))
        return _finish(verdict, cache_key, machine_now)

    # --- AHEAD: session date only, day granularity only, both zones agree --
    if session_day is not None:
        # THE ZONE SEAM (fix round). The session date is the user's local,
        # day-granular date. Near midnight the same instant is two different
        # calendar days depending on which zone renders it, so a machine that
        # is merely in a different zone from the workspace could be accused of
        # running a day fast. Both renderings must agree before this fires; a
        # disagreement means the helper stays SILENT rather than accuse a
        # correct clock.
        days_ahead_workspace = (machine_day_in_workspace_zone
                                - session_day).days
        days_ahead_machine = (machine_day_in_machine_zone - session_day).days
        days_ahead = min(days_ahead_workspace, days_ahead_machine)
        if days_ahead >= AHEAD_MIN_DAYS:
            verdict["trusted"] = False
            verdict["direction"] = "ahead"
            verdict["source"] = "env_date"
            # The INSTANT is deliberately not rewritten: a date carries no
            # sub-day precision, so there is nothing to rewrite it with. No
            # floor either — there is nothing to advance.
            verdict["skew_seconds"] = -float(days_ahead * 86400)
            verdict["today"] = session_day
            # "this computer's clock reads ..." is a statement about the
            # COMPUTER, so it is rendered in the computer's own zone — the same
            # LATETZ rule the stale notice follows. Rendering it in the
            # workspace zone would put a day in the sentence that the machine
            # never displayed.
            verdict["notice"] = CLOCK_NOTICE_AHEAD.format(
                machine_date=_human_day(machine_day_in_machine_zone),
                env_date=_human_day(session_day))
            return _finish(verdict, cache_key, machine_now)

    verdict["today"] = machine_day_in_workspace_zone
    return _finish(verdict, cache_key, machine_now)


def _finish(verdict: dict, cache_key, machine_now) -> dict:
    if machine_now is None:
        _VERDICT_CACHE[cache_key] = dict(verdict)
    return verdict


def _advancing_floor(verdict: dict) -> Optional[_dt.datetime]:
    """The corrected floor, advanced by REAL elapsed time, or None.

    WHY MONOTONIC AND NOT AN OFFSET. The first version of this cached the skew
    and returned `datetime.now() + skew`. That is correct only while the
    machine clock stays wrong. The moment it re-syncs — an NTP correction
    landing mid-fire is precisely the situation this module exists for — the
    offset gets added to a clock that is now RIGHT, and every stamp for the
    rest of the process lands in the FUTURE. Worse, those stamps come from the
    writer helpers, which carry no annotation, so the contamination is
    invisible.

    `time.monotonic()` cannot be moved by a clock correction, so the floor
    advances at the rate of real elapsed time and nothing else. Combined with
    the `max()` in `trusted_now`, a machine that re-syncs ahead of the floor
    self-heals the instant it passes it, and a machine that is still stale
    keeps getting a floor that moves forward rather than a frozen one.
    """
    floor = verdict.get("floor_utc")
    if floor is None:
        return None
    anchor = verdict.get("monotonic_at")
    if anchor is None:
        return floor
    elapsed = _time.monotonic() - anchor
    if elapsed < 0:
        elapsed = 0.0
    return floor + _dt.timedelta(seconds=elapsed)


# --------------------------------------------------------------------------
# The public clock verbs
# --------------------------------------------------------------------------

def trusted_now(workspace_root=None, *, env_date=None,
                machine_zone=None) -> _dt.datetime:
    """The corroborated instant, aware UTC. The public verb.

    `max(machine, advancing floor)`. The machine clock always wins once it is
    at or past the floor, which is what makes a mid-fire re-sync self-healing
    instead of a source of future-dated stamps.
    """
    verdict = assess_clock(workspace_root, env_date=env_date,
                           machine_zone=machine_zone)
    machine = _dt.datetime.now(_dt.timezone.utc)
    floor = _advancing_floor(verdict)
    if floor is None or machine >= floor:
        return machine
    return floor


# The name the ~20 swept writer helpers import. Same function, spelled the way
# a stamp site reads.
trusted_now_utc = trusted_now


def trusted_now_iso(workspace_root=None, *, env_date=None,
                    machine_zone=None) -> str:
    """The corroborated instant as an ISO-8601 UTC string."""
    return trusted_now(workspace_root, env_date=env_date,
                       machine_zone=machine_zone).isoformat()


def trusted_now_local_naive(workspace_root=None, *, env_date=None,
                            machine_zone=None) -> _dt.datetime:
    """The corroborated instant expressed in the MACHINE's own zone, naive.

    The drop-in for every `_now_local()` in the scheduling helpers. The zone is
    unchanged — this is the machine clock, which is the clock cron evaluates in
    and the only clock lateness math may use. Only the INSTANT may have moved.
    """
    return _machine_zone_dt(
        trusted_now(workspace_root, env_date=env_date,
                    machine_zone=machine_zone),
        machine_zone).replace(tzinfo=None)


def clock_report(workspace_root=None, *, env_date=None,
                 machine_zone=None) -> dict:
    """The compact, JSON-safe view of the verdict that travels in
    `check_lateness`'s return dict and reaches the orchestrators.

    Read LIVE against the floor, not replayed from the cached verdict: a
    machine that re-synced after the assessment is trusted again, and a run
    that discloses a clock problem it no longer has is its own small lie.
    """
    verdict = assess_clock(workspace_root, env_date=env_date,
                           machine_zone=machine_zone)
    floor = _advancing_floor(verdict)
    if floor is not None and _dt.datetime.now(_dt.timezone.utc) >= floor:
        machine = _dt.datetime.now(_dt.timezone.utc)
        root = _resolve_workspace(workspace_root)
        return {
            "untrusted": False,
            "direction": None,
            "notice": None,
            "today": _workspace_local_date(machine, root).isoformat(),
            "machine_now": machine.isoformat(),
            "corroborated_now": machine.isoformat(),
            "source": "machine",
            "skew_seconds": 0.0,
            "anomaly": verdict["anomaly"],
        }
    return {
        "untrusted": not verdict["trusted"],
        "direction": verdict["direction"],
        "notice": verdict["notice"],
        "today": verdict["today"].isoformat() if verdict["today"] else None,
        "machine_now": verdict["machine_now_utc"].isoformat(),
        "corroborated_now": verdict["corroborated_now_utc"].isoformat(),
        "source": verdict["source"],
        "skew_seconds": verdict["skew_seconds"],
        "anomaly": verdict["anomaly"],
    }


# --------------------------------------------------------------------------
# The append-gate stamp
# --------------------------------------------------------------------------

def floor_stamp(machine_now: _dt.datetime,
                newest_ts: Optional[_dt.datetime],
                events_path=None) -> dict:
    """The `ts` an append should carry, given what the file already holds.

    Called by the event append gate with the newest timestamp it found in the
    SAME parse pass that scans for the maximum seq, so corroboration costs the
    writer nothing extra.

    Returns `{"ts": <aware UTC>, "ts_source": <str|None>, "machine_ts":
    <str|None>}`. `ts_source` and `machine_ts` are set ONLY when a correction
    actually happened; an uncorrected write is byte-identical to pre-CLOCK1.

    It writes, it does not refuse. Raising here would lose every remaining
    substrate write the fire owes — the invisible-write-loses class — and the
    ledger's lineage is healthy; only the stamp is uncertain. A real event
    belongs in the ledger, annotated.
    """
    plain = {"ts": machine_now, "ts_source": None, "machine_ts": None,
             "refused": None}
    if newest_ts is None:
        return plain
    if _correction_suppressed(events_path):
        return plain
    lead_seconds = (newest_ts - machine_now).total_seconds()
    if lead_seconds <= CLOCK_TRUST_TOLERANCE_SECONDS:
        return plain
    # CLOCKTS1, SITE A. A lead alone was the whole predicate, and a lead alone
    # cannot tell a machine that is behind from a row that is ahead. Ask the
    # `.seqhw` witness which one this is; adopt the floor only for the first.
    ts_class = classify_substrate_max(newest_ts, machine_now, events_path)
    if not ts_class["is_clock_evidence"]:
        # Stamp the MACHINE reading and say why, rather than adopting a
        # maximum that is a wrong row. `refused` is additive and travels to the
        # gate for its stderr line; the event itself is byte-identical to an
        # ordinary uncorrected append, which is the correct output here.
        return {"ts": machine_now, "ts_source": None, "machine_ts": None,
                "refused": ts_class["reason"]}
    return {
        "ts": newest_ts,
        "ts_source": TS_SOURCE_SUBSTRATE_FLOOR,
        "machine_ts": machine_now.isoformat(),
        "refused": None,
    }


def _correction_suppressed(events_path=None) -> bool:
    """True when a verdict computed earlier in this process ruled THIS
    workspace's substrate maximum anomalous.

    Scoped to the workspace being written (fix round). The first version
    scanned every cached verdict, so a process that had touched two workspaces
    let one workspace's anomaly suppress corrections in the other — a fence
    reaching outside the thing it fences. The append gate cannot see a session
    date of its own, so it honours the one a surface already arbitrated with,
    but only for the same root.
    """
    root = None
    if events_path is not None:
        resolved = workspace_root_for_path(events_path)
        root = str(resolved) if resolved is not None else None
    for key, verdict in _VERDICT_CACHE.items():
        if not verdict.get("anomaly"):
            continue
        if root is not None and key[0] != root:
            continue
        return True
    return False


__all__ = [
    "AHEAD_MIN_DAYS",
    "CLOCK_NOTICE_AHEAD",
    "CLOCK_NOTICE_STALE",
    "CLOCK_TRUST_TOLERANCE_SECONDS",
    "FLOOR_REFUSED_BEYOND_CAP",
    "FLOOR_REFUSED_FORWARD_DATED",
    "TS_LEAD_CAP_DAYS",
    "TS_LEAD_CAP_SECONDS",
    "TS_SOURCE_SUBSTRATE_FLOOR",
    "assess_clock",
    "classify_substrate_max",
    "clock_report",
    "floor_stamp",
    "forward_dated_lead",
    "read_seqhw_updated",
    "max_ts_in_jsonl_text",
    "newest_substrate_ts",
    "parse_env_date",
    "register_workspace",
    "register_workspace_from_data_path",
    "reset_clock_trust_cache",
    "scan_jsonl_text",
    "workspace_root_for_path",
    "trusted_now",
    "trusted_now_iso",
    "trusted_now_local_naive",
    "trusted_now_utc",
]
