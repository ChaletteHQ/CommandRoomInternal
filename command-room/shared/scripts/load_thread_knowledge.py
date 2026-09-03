#!/usr/bin/env python3
"""READER1 — `load_thread_knowledge`, the canonical per-thread knowledge reader.

SPEC_READER1 §1–§3. One call composes everything a surface needs to know about
a thread — identity, live state, open commitments, recent activity, decisions,
and (per profile) cross-thread person-graph context — into a budgeted,
provenance-stamped payload. Skills adopt this instead of freelance substrate
scans; the callsite guard (`run_guard_thread_knowledge_callsites_test.py`)
ratchets adoption.

The §0 rulings this module encodes (do not relitigate here):

  §0.1 Named profiles only. Profiles are DATA (the module-level PROFILES
       table), never kwargs and never inline dicts — the API freezes at the
       first adoption because G41 signature-binds prose snippets fleet-wide.
       An unknown or non-string profile raises ProfileError.
  §0.2 Thread membership = `event_refs.threads_of(ev)`, pinned. NOT
       `cru_match.commitment_thread_refs()` — that returns SOURCE refs
       (Gmail hex ids), and filtering opens with it yields 0 rows on every
       thread (verified live by the prototype). This module must never call
       it for membership.
  §0.3 Coverage is PERSISTED, never computed at read time. A read-time score
       is structurally blind to misbinding (it scored a NOT-READY thread
       0.894 because a reader cannot count events bound to the WRONG
       thread). We load the gauge artifact (`_hq/data/binding_gauge.json`)
       and report its verdict; read-time we add only DEGRADATION signals —
       skipped lines, stale gauge, truncations. One sanctioned exception
       (§5c.8 read-repair): when the ARTIFACT IS MISSING and the caller
       passes `allow_recompute=True`, the gauge is recomputed inline via
       `binding_gauge.build_gauge` — the same audit-calibrated compute path,
       read-only, nothing written — and the fresh verdict is reported with
       `recomputed: true`. The default stays the honest "unmeasured".
  §5c.5 ASK-FIRST — LINE-71 GOVERNS (the ruling, recorded): until R3's
       backfill lands, a gauge-NOT-READY thread ANSWERS DIRECTLY with no
       coverage apparatus — no ask-first, no disclosure line. Ask-first and
       the coverage disclosure activate only on gauge-READY threads (plus
       the §5b.3 empty-payload degrade, which is its own loud signal). This
       supersedes the §5 threshold note's ambiguity; consuming surfaces key
       on `coverage.gauge.state == "ready"`.
  §0.4 Cross-thread person-graph expansion is NOT optional for the catch-all
       profile (the sibling-thread pricing-guardrail landmine class: a note on a
       sibling thread, invisible to any single-thread payload). Window 45d,
       cap 10 rows, every row labeled "related, from <thread>". The expansion
       also admits person-linked rows bound to NO thread at all, labeled
       "related, unbound" — deliberate (review F3): unbound landmines are the
       worst kind, and org profiles still drop personal unbound rows because
       the expansion consumes the already-privacy-scoped event list.
  §0.6 Retrieval splits library/prompt: connector-backed transcript retrieval
       stays a prompt-orchestrated stage AFTER this returns — merged into the
       ANSWER, never into this payload.
  §0.7 Profiles carry an origin filter: `exclude_swept_narrative` on
       outbound-facing profiles drops SESSION_NOTES blocks whose heading
       carries `origin: swept` until a confirming touch; owner catch-all
       keeps them LABELED. This is where the SESSSTORY1 quarantine lives.

Composed canonical modules (real signatures, verified against the live
substrate by the READER1 prototype): cru_match (open-set projection with the
PGUARD2 `events=` injection seam; pending split), events_io
(`load_events_org_scoped` for org profiles), event_refs (membership, persons,
seq/ts readers), entities_io, render_thread_live_state (live-state block +
brain-path resolution with the two DISTINCT repair codes), substance_events
(the promoted classifier).

Caching (§3): memoized per (workspace_root, thread_id, profile, now_iso,
user_person_id, allow_recompute) keyed on a stat signature across every substrate input —
events active file + all year shards + entities.json + the gauge artifact,
each as (size, mtime_ns); .md inputs (brain file, session notes) additionally
carry a CONTENT hash, because bulk regens rewrite files in place and destroy
mtime as a signal (the known R1 hazard).

THE MEMO CACHE IS PER-PROCESS. Cowork invokes scripts as fresh OS processes
per call, so across Cowork invocations every call is a cold build — the cache
helps test/battery contexts (many calls, one process) only. Callers must not
rely on cross-invocation memoization, and must not treat a cold call's cost
as a regression.

Fix round 2026-08-28:
  * owner-privacy profiles read through `events_io.load_events_owner_scoped`
    — the owner-tier seam of the allowlisted shard reader — so this module
    holds no raw events.jsonl read (run_personal_firewall_test D4c);
  * entities.json is read with torn-read retry (§5b.1 — the entities-torn-
    read bug class);
  * the decisions section is era-tolerant and closure-aware (§5b.2):
    title falls back across the title-era/summary-era writer shapes via
    render_decision_log's derivation, and a CLOSED decision is never
    rendered as active — the same latest-signal-wins status fold the
    decision-log view uses. DECSHAPES1 (2026-09-02): "closed" is both
    terminal buckets, `superseded` AND `resolved`, taken from
    `render_decision_log.CLOSED_DECISION_STATUSES` rather than spelled out
    here — this filter read `superseded` alone, and the day the resolved
    bucket landed it would have passed carried-out rulings through as live;
  * §5b.3 "degrade loudly, never answer thin": a thread that RESOLVES while
    every substantive section is empty (no events bound, no live-state
    people, no open commitments confirmed or held, hence no decisions) is
    the confident-empty payload a decoy/archived workspace produces (the
    decoy-_hq bug class, SPEC_WSPICK1). It emits the `empty_payload`
    degraded entry (naming the thread) and sets `coverage.empty_payload` —
    a consuming surface must treat that as NOT answerable-thin (ask-first
    or say the substrate holds nothing) until WSPICK1 discovery validation
    lands.

Fix round 3 2026-08-28 (§5c — novel-modality retest):
  * §5c.3: the empty-payload guard counts SUBSTANCE events only
    (substance_events), so bound lifecycle rows (thread_updated etc.) can
    no longer defeat it — the retest's 3-lifecycle-event defeat case;
  * §5c.4: payload rows that carry person_/org_ ids ALSO carry resolved
    display names from entities (owner_name, person_names, org_name,
    key_contact_name) — additive, the ids are never removed (consumers may
    key on them); an unresolvable id yields a null name, never a dropped id;
  * §5c.8: `_load_gauge` read-repair via `allow_recompute=True` (see §0.3
    above), and gauge tolerance — unknown extra keys in the artifact never
    break the reader; the §5c.2 `person_binding` extension is passed through.
  * Honest limit (recorded per §5c.8): this reader prevents substrate-STATE
    incidents and refuses loudly on substrate-ABSENCE; it cannot surface
    facts that never entered the substrate — narrative-only landmines
    remain SESSSTORY1 / brain-sweep territory, not reader territory.

stdlib + sibling shared/scripts modules only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cru_match
import event_refs
import events_io
import render_decision_log as _rdl
import render_thread_live_state as _rtls
import substance_events
from entities_io import entities_collection, unwrap_entities

__all__ = [
    "PROFILES",
    "ProfileError",
    "ThreadNotFoundError",
    "load_thread_knowledge",
    "cache_stats",
    "clear_cache",
    "GAUGE_RELPATH",
]

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProfileError(ValueError):
    """Unknown or non-string profile. Inline profile dicts are rejected by
    design (§0.1) — the profile surface is this module's table, nothing else."""


class ThreadNotFoundError(KeyError):
    """The resolved thread id is not in the entity register. Raised, not
    degraded: an unknown id is a caller bug (resolution belongs to
    entity_resolve at the call site), not a substrate condition."""


# ---------------------------------------------------------------------------
# Profile table (§2 — FROZEN at first adoption; edit only via a reviewed spec)
# ---------------------------------------------------------------------------

# Section spec keys:
#   budget_tokens  approximate token budget (JSON chars / 4); counted
#                  truncation notes are emitted when a cap bites (§3)
#   window_days    event window for the section (None = all-time)
#   keep_pinned    windowed sections keep rows whose data.pinned is truthy
#   verbatim       never truncated (Bug #86 read-back mandate); over-budget
#                  is NOTED, not cut
#   meeting_links  open rows carry their source_ref (meeting/email anchor)
#
# pending_split modes:
#   "off"             no split — every projected open row included (go)
#   "confirmed_only"  confirmed half only; held count disclosed (email-writer)
#   "held_disclosed"  confirmed half only; held count disclosed (catch-all)
#   "pointer"         confirmed half + a pointer line for the held count;
#                     F-44: undated CONFIRMED rows stay visible (call-prep)
#
# cross_thread modes (§0.4):
#   "off"    no expansion
#   "graph"  full person-graph over the payload's identity/live-state people
#   "person" key-contact-scoped expansion (recipient / attendee analogue)

PROFILES: dict[str, dict] = {
    "email-writer": {
        "privacy": "org",              # PGUARD2 injection — see _load_events
        "pending_split": "confirmed_only",
        "exclude_swept_narrative": True,
        "cross_thread": "person",
        "sections": {
            "identity":         {"budget_tokens": 300},
            "open_commitments": {"budget_tokens": 800},
            "recent_activity":  {"budget_tokens": 600, "window_days": 90},
            "decisions":        {"budget_tokens": 400, "window_days": 180,
                                 "keep_pinned": True},
        },
        "section_order": ["identity", "open_commitments", "recent_activity",
                          "decisions", "cross_thread"],
    },
    "go": {
        "privacy": "owner",
        "pending_split": "off",
        "exclude_swept_narrative": False,
        "cross_thread": "off",
        "sections": {
            "live_state":       {"budget_tokens": 500, "verbatim": True},
            "identity":         {"budget_tokens": 300},
            "open_commitments": {"budget_tokens": 800},
            "recent_activity":  {"budget_tokens": 800, "window_days": 14},
            "decisions":        {"budget_tokens": 400},
        },
        "section_order": ["live_state", "identity", "open_commitments",
                          "recent_activity", "decisions"],
    },
    "go-render": {
        # GORENDER1 (memory program R1 second adoption) — the anchor-render
        # surface BEHIND coverage-gated `go`, not the chat-facing go load:
        # the frozen "go" row above stays byte-identical for the prose
        # adoption, while the render needs sibling-thread landmine rows
        # (cross_thread "graph" — the landmines-judgment anchor's
        # person-sensitive class) and durable-file windows. Swept narrative
        # is EXCLUDED: a render etches content into a durable brain file,
        # and unconfirmed swept blocks must not be laundered into it
        # (§0.7 applied to a write-facing surface).
        "privacy": "owner",
        "pending_split": "off",
        "exclude_swept_narrative": True,
        "cross_thread": "graph",
        "sections": {
            "identity":         {"budget_tokens": 300},
            "open_commitments": {"budget_tokens": 800},
            "recent_activity":  {"budget_tokens": 800, "window_days": 30},
            "decisions":        {"budget_tokens": 500, "window_days": 365,
                                 "keep_pinned": True},
        },
        "section_order": ["identity", "open_commitments", "recent_activity",
                          "decisions", "cross_thread"],
    },
    "catch-all": {
        "privacy": "owner",
        "pending_split": "held_disclosed",
        "exclude_swept_narrative": False,   # kept, LABELED (§0.7)
        "cross_thread": "graph",            # §0.4 — mandatory
        "sections": {
            "identity":         {"budget_tokens": 300},
            "live_state":       {"budget_tokens": 400, "verbatim": True},
            "open_commitments": {"budget_tokens": 600},
            "recent_activity":  {"budget_tokens": 600, "window_days": 30},
            "decisions":        {"budget_tokens": 400, "window_days": 180,
                                 "keep_pinned": True},
        },
        "section_order": ["identity", "live_state", "open_commitments",
                          "recent_activity", "decisions", "cross_thread"],
    },
    "call-prep": {
        "privacy": "owner",
        "pending_split": "pointer",
        "exclude_swept_narrative": True,    # ON for the brief body
        "cross_thread": "person",           # person-graph for attendees
        "sections": {
            "identity":         {"budget_tokens": 300},
            "open_commitments": {"budget_tokens": 1000, "meeting_links": True},
            "recent_activity":  {"budget_tokens": 800, "window_days": 30},
            "decisions":        {"budget_tokens": 500},
        },
        "section_order": ["identity", "open_commitments", "recent_activity",
                          "decisions", "cross_thread"],
    },
    "brief-line": {
        # ADOPT4 (READER1 R1 follow-on, 2026-09-02) — the morning brief's
        # PER-PROJECT detail line, and nothing else on that surface. The
        # brief is a SCHEDULED, MANY-THREAD surface: one call per rendered
        # project line, so this row carries the tightest budgets in the
        # table and the smallest section set — identity (what the thread
        # is, its status, its key contact), open commitments (the CONFIRMED
        # half only — an unconfirmed extraction must never narrate itself
        # from a scheduled fire; the held count rides `coverage`), and
        # decisions on the record (90d + pinned, supersession folded).
        # NO recent_activity: the brief never reads session notes (its own
        # Step 1 rule), and a scheduled surface must never narrate
        # unconfirmed machine notes — swept narrative is excluded twice
        # over (no narrative section exists, AND the §0.7 flag is ON as
        # defense in depth should a section ever be added). NO live_state
        # (a verbatim, unbudgetable block has no place on an N-thread
        # line). NO cross-thread expansion (sibling landmines belong to the
        # go / catch-all lanes, not to a one-line status). Recency ("Last
        # touched" / quiet N days) is deliberately NOT this payload's job:
        # the brief already holds the canonical `thread_activity` map from
        # its Step 3d derivation, one fold for every thread in the fire.
        "privacy": "owner",
        "pending_split": "held_disclosed",
        "exclude_swept_narrative": True,
        "cross_thread": "off",
        "sections": {
            "identity":         {"budget_tokens": 200},
            "open_commitments": {"budget_tokens": 300},
            "decisions":        {"budget_tokens": 200, "window_days": 90,
                                 "keep_pinned": True},
        },
        "section_order": ["identity", "open_commitments", "decisions"],
    },
}

CROSS_THREAD_WINDOW_DAYS = 45   # §0.4
CROSS_THREAD_CAP = 10           # §0.4
SESSION_NOTES_MAX_BLOCKS = 5
SESSION_NOTES_CLIP_CHARS = 400
GAUGE_RELPATH = ("_hq", "data", "binding_gauge.json")

EPOCH_THRESHOLD = 10 ** 10      # legacy nano-epoch seqs are not freshness

_SWEPT_ORIGIN_RE = re.compile(r"origin:\s*swept", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Cache (§3)
# ---------------------------------------------------------------------------

_CACHE: dict[tuple, tuple] = {}
_CACHE_STATS = {"hits": 0, "builds": 0}


def cache_stats() -> dict:
    """Copy of the memo counters — {'hits': n, 'builds': n}."""
    return dict(_CACHE_STATS)


def clear_cache() -> None:
    _CACHE.clear()
    _CACHE_STATS["hits"] = 0
    _CACHE_STATS["builds"] = 0


def _stat_sig(p: Path, *, content_hash: bool = False):
    """(path, size, mtime_ns[, sha256]) — or (path, None) when absent.

    `content_hash=True` for .md inputs: bulk regenerations rewrite brains and
    session notes in place, and a same-size rewrite plus a preserved/cloned
    mtime is exactly the signal-destruction the R1 hazard names. The hash is
    the witness that survives it.
    """
    try:
        st = p.stat()
    except OSError:
        return (str(p), None)
    sig = [str(p), st.st_size, st.st_mtime_ns]
    if content_hash:
        try:
            sig.append(hashlib.sha256(p.read_bytes()).hexdigest())
        except OSError:
            sig.append(None)
    return tuple(sig)


def _signature(root: Path, brain_path: Path | None,
               notes_paths: list[Path]) -> tuple:
    parts = [
        _stat_sig(root / "_hq" / "data" / "entities.json"),
        _stat_sig(events_io.active_path(root)),
        _stat_sig(root.joinpath(*GAUGE_RELPATH)),
    ]
    for shard in sorted(events_io.shard_paths(root)):
        parts.append(_stat_sig(shard))
    if brain_path is not None:
        parts.append(_stat_sig(brain_path, content_hash=True))
    for np_ in notes_paths:
        parts.append(_stat_sig(np_, content_hash=True))
    return tuple(parts)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now(now_iso: str | None) -> datetime:
    if now_iso:
        dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _ts(ev: dict) -> str:
    return event_refs.event_ts(ev) or ""


def _event_text(ev: dict) -> str:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    parts = [str(d.get(k) or "") for k in
             ("title", "summary", "text", "note", "subject", "description",
              "decision", "body", "topic")]
    parts.append(str(ev.get("title") or ""))
    return " ".join(p for p in parts if p).strip()


def _human_seq(ev: dict):
    s = event_refs.event_seq(ev)
    if s is None or s >= EPOCH_THRESHOLD:
        return None
    return int(s)


def _max_human_seq(evs) -> int | None:
    latest = None
    for ev in evs:
        s = _human_seq(ev)
        if s is not None and (latest is None or s > latest):
            latest = s
    return latest


def _est_tokens(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str)) // 4


def _is_pinned(ev: dict) -> bool:
    d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return bool(d.get("pinned") or ev.get("pinned"))


_ENTITIES_READ_RETRIES = 3
_ENTITIES_READ_BACKOFF_S = 0.05


def _read_entities_defensive(ent_path) -> dict:
    """entities.json with torn-read retry (SPEC_READER1 §5b.1).

    A mid-sync truncation or a torn read serves unparseable bytes for a
    moment while the file on disk is clean (the entities-torn-read bug class;
    same transient shape substrate_health.preflight_freshness retries). Retry
    ×3 with a short backoff before giving up; the final failure re-raises for
    the caller to map (and best-effort drops a `.readalarm.json` sidecar, the
    FS-15 evidence pattern, so the window leaves a trace).
    """
    last_exc: Exception | None = None
    for attempt in range(_ENTITIES_READ_RETRIES + 1):
        if attempt and _ENTITIES_READ_BACKOFF_S > 0:
            time.sleep(_ENTITIES_READ_BACKOFF_S)
        try:
            return json.loads(ent_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            last_exc = e
    try:  # best-effort evidence, never a second failure (events_io FS-15
        # convention: an absent file is not an alarm, only a bad READ is)
        if Path(str(ent_path)).exists():
            from read_alarm import record_read_alarm
            record_read_alarm(ent_path, last_exc,
                              reader="load_thread_knowledge")
    except Exception:  # noqa: BLE001
        pass
    raise last_exc


def _thread_record(ent: dict, thread_id: str) -> dict | None:
    for t in entities_collection(ent, "threads"):
        if t.get("id") == thread_id:
            return t
    return None


def _thread_display_name(ent: dict, thread_id: str) -> str:
    t = _thread_record(ent, thread_id)
    if t:
        return t.get("canonical_name") or t.get("folder_name") or thread_id
    return thread_id


def _entity_names(ent: dict) -> dict[str, str]:
    """{entity_id: display name} across people/orgs/threads — the §5c.4
    id-resolution map (live fire printed raw person ids to the user).
    ADDITIVE only at every use site: rows gain a resolved *_name next to the
    id, the id itself is never removed."""
    names: dict[str, str] = {}
    for coll in ("people", "orgs", "threads"):
        for rec in entities_collection(ent, coll):
            if not isinstance(rec, dict):
                continue
            rid = rec.get("id")
            nm = rec.get("canonical_name") or rec.get("folder_name")
            if isinstance(rid, str) and rid and isinstance(nm, str) and nm:
                names.setdefault(rid, nm)
    return names


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _identity_content(thread: dict, names: dict[str, str]) -> dict:
    keep = ("id", "canonical_name", "folder_name", "kind", "status", "org_id",
            "affiliation_id", "key_contact_id", "aliases", "notes")
    content = {k: thread.get(k) for k in keep if thread.get(k)}
    # §5c.4 — resolve ids to display names, additive (ids stay).
    for id_key, name_key in (("org_id", "org_name"),
                             ("affiliation_id", "affiliation_name"),
                             ("key_contact_id", "key_contact_name")):
        rid = content.get(id_key)
        if rid:
            content[name_key] = names.get(rid)
    return content


def _open_rows(opens: list[dict], names: dict[str, str], *,
               meeting_links: bool) -> list[dict]:
    import closure_index
    rows = []
    for ev in opens:
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        owner_id = d.get("owner_person_id") or d.get("owner_id")
        row = {
            "id": closure_index.commitment_key(ev),
            "ts": _ts(ev)[:10],
            "title": d.get("title") or ev.get("title"),
            "due": cru_match._commitment_field(ev, "due"),
            "owner_person_id": owner_id,
            "seq": ev.get("seq"),
        }
        if owner_id:
            # §5c.4 — additive resolution; the id stays, an unresolvable id
            # yields a null name rather than a dropped row or id.
            row["owner_name"] = names.get(owner_id)
        if meeting_links:
            row["source_ref"] = d.get("source_ref")
        rows.append(row)
    rows.sort(key=lambda r: (r["ts"], r["seq"] if isinstance(r["seq"], int) else 0))
    return rows


def _recent_event_rows(bound: list[dict], floor_iso: str) -> list[dict]:
    recent = [ev for ev in bound if _ts(ev) >= floor_iso]
    recent.sort(key=_ts)
    return [{
        "ts": _ts(ev)[:16],
        "type": substance_events.event_type_of(ev),
        "family": substance_events.family_of(substance_events.event_type_of(ev)),
        "title": (_event_text(ev)[:160] or None),
        "seq": ev.get("seq"),
    } for ev in recent]


def _decision_rows(bound: list[dict], floor_iso: str | None,
                   keep_pinned: bool,
                   overlays: dict | None = None) -> tuple[list[dict], int]:
    """Era-tolerant, supersession-aware decision rows (SPEC_READER1 §5b.2).

    Title falls back across the title-era/summary-era writer shapes via
    `render_decision_log._decision_title` (title → summary → decision →
    evidence clip) — the 415-of-648-"(untitled)" era-join bug class. Status
    comes from `render_decision_log._decision_status` (latest-signal-wins
    between supersede and reaffirm, both id- and seq-keyed overlays joined),
    so a superseded decision is NEVER rendered as active: it is dropped from
    the section and COUNTED. `overlays` is the `_categorize_decisions` fold
    over the FULL event list (supersede events are not always thread-bound).

    Returns (rows, n_closed_excluded).
    """
    rows = []
    n_closed = 0
    for ev in bound:
        etype = substance_events.event_type_of(ev)
        fam = substance_events.family_of(etype)
        if fam != "decision":
            continue
        pinned = _is_pinned(ev)
        if floor_iso is not None and _ts(ev) < floor_iso and not (keep_pinned and pinned):
            continue
        if etype == "decision":
            status = "active"
            if overlays is not None:
                status, _overlay = _rdl._decision_status(ev, overlays)
            # DECSHAPES1 (2026-09-02) — the status fold gained a fifth bucket,
            # `resolved` (a ruling the ledger says was CARRIED OUT). This
            # exclusion read `superseded` alone, so the day the bucket landed a
            # closed-out ruling would have flowed into the payload beside the
            # live ones, presented as current, with the coverage note
            # under-reporting the fold. Both CLOSED buckets are excluded; the
            # open ones (`active` / `reaffirmed` / `snoozed`) are what this
            # section is for.
            if status in _rdl.CLOSED_DECISION_STATUSES:
                n_closed += 1
                continue
            title = _rdl._decision_title(
                ev.get("data") if isinstance(ev.get("data"), dict) else {})
        else:
            # lifecycle markers in the family (decision_pending / reaffirmed /
            # revisit_scheduled) keep the composite text — their data carries
            # none of the title-era fields
            status = None
            title = _event_text(ev)[:200]
        row = {
            "ts": _ts(ev)[:10],
            "title": title[:200],
            "seq": ev.get("seq"),
            "pinned": pinned,
        }
        if status is not None:
            row["status"] = status
        rows.append(row)
    rows.sort(key=lambda r: r["ts"])
    return rows, n_closed


def _find_session_notes(root: Path, thread: dict) -> list[Path]:
    folder = thread.get("folder_name")
    if not folder or not (root / folder).is_dir():
        return []
    return sorted((root / folder).glob("SESSION_NOTES*.md"))


def _session_notes_blocks(paths: list[Path]) -> list[dict]:
    """Dated `## …` blocks, file order (newest-first by house convention)."""
    blocks: list[dict] = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        current = None
        for line in text.splitlines():
            if line.startswith("## "):
                if current:
                    blocks.append(current)
                heading = line[3:].strip()
                current = {
                    "heading": heading,
                    "origin": "swept" if _SWEPT_ORIGIN_RE.search(heading) else None,
                    "text": "",
                    "source": p.name,
                }
            elif current is not None:
                body = (current["text"] + "\n" + line).strip()
                current["text"] = body[:SESSION_NOTES_CLIP_CHARS]
        if current:
            blocks.append(current)
    return blocks[:SESSION_NOTES_MAX_BLOCKS]


def _cross_thread_rows(events: list[dict], ent: dict, names: dict[str, str],
                       thread_id: str, persons: set[str],
                       now: datetime) -> tuple[list[dict], int]:
    """§0.4 person-graph expansion: recent note/decision-family events that
    carry one of `persons` and are bound to OTHER threads (or unbound).
    Returns (rows, n_over_cap)."""
    if not persons:
        return [], 0
    floor_iso = (now - timedelta(days=CROSS_THREAD_WINDOW_DAYS)).isoformat()
    hits = []
    for ev in events:
        refs = event_refs.threads_of(ev)
        if thread_id in refs:
            continue
        etype = substance_events.event_type_of(ev)
        fam = substance_events.family_of(etype)
        if not (fam == "decision" or etype == "note" or etype == "intel_logged"):
            continue
        if _ts(ev) < floor_iso:
            continue
        if not (event_refs.persons_of(ev) & persons):
            continue
        src_tid = next(iter(sorted(refs)), None)
        label = (f"related, from {_thread_display_name(ent, src_tid)}"
                 if src_tid else "related, unbound")
        pids = sorted(event_refs.persons_of(ev) & persons)
        hits.append({
            "ts": _ts(ev)[:16],
            "type": etype,
            "title": _event_text(ev)[:200],
            "seq": ev.get("seq"),
            "thread_id": src_tid,
            "label": label,
            "person_ids": pids,
            # §5c.4 — additive resolution, aligned with person_ids.
            "person_names": [names.get(p) for p in pids],
        })
    hits.sort(key=lambda r: r["ts"], reverse=True)
    over = max(0, len(hits) - CROSS_THREAD_CAP)
    return hits[:CROSS_THREAD_CAP], over


def _payload_persons(thread: dict, bound: list[dict], mode: str) -> set[str]:
    """People 'in the payload's identity/live_state' (§0.4). graph = every
    person the thread's events reference plus the key contact; person = the
    key contact (recipient/attendee analogue) with a graph fallback when the
    record names none."""
    key_contact = thread.get("key_contact_id")
    graph: set[str] = set()
    for ev in bound:
        graph |= event_refs.persons_of(ev)
    if key_contact:
        graph.add(key_contact)
    if mode == "person":
        return {key_contact} if key_contact else graph
    return graph


def _live_state_people(root: Path, thread_id: str) -> int:
    """Confirmed live-state roster size — the §5b.3 confident-empty probe.

    Counts the `high`/`pinned` roster the live-state block would render as
    members. Runs only on the rare all-else-empty path (the caller
    short-circuits), so the extra roster derivation costs nothing on healthy
    threads. Any failure counts as 0 people: this probe feeds a DEGRADE
    signal, and a probe crash must never take the payload down with it.
    """
    try:
        roster = _rtls.derive_roster(root, thread_id)
    except Exception:  # noqa: BLE001 — degrade-signal probe, never fatal
        return 0
    return sum(1 for r in roster
               if r.get("confidence") in ("high", "pinned"))


# ---------------------------------------------------------------------------
# Gauge (§0.3)
# ---------------------------------------------------------------------------


def _gauge_from_record(rec: dict, doc: dict, *,
                       recomputed: bool = False) -> dict:
    """Map one artifact thread record to the payload's gauge dict. TOLERANT
    by design (§5c.8): only the known keys are read, so a schema-compatible
    extension (e.g. the §5c.2 `person_binding`) never breaks the reader —
    known extensions are passed through, unknown extras ignored."""
    gauge = {
        "state": "ready" if rec.get("ready") else "not_ready",
        "ratio": rec.get("ratio"),
        "substance_bound": rec.get("substance_bound", rec.get("sub")),
        "measured_at": doc.get("generated"),
    }
    if "person_binding" in rec:
        gauge["person_binding"] = rec.get("person_binding")
    if recomputed:
        gauge["recomputed"] = True
    return gauge


def _recompute_gauge(root: Path, thread_id: str) -> dict | None:
    """§5c.8 read-repair: recompute the gauge verdict inline when the
    artifact is MISSING and the caller opted in via `allow_recompute=True`.
    Read-only — `binding_gauge.build_gauge` is the audit-calibrated compute
    path and nothing is written. Returns None (caller falls back to the
    honest "unmeasured") when the recompute fails or the thread is absent —
    a repair probe must never take the payload down with it."""
    try:
        import binding_gauge
        doc = binding_gauge.build_gauge(root)
    except Exception:  # noqa: BLE001 — repair path, never fatal
        return None
    rec = (doc.get("threads") or {}).get(thread_id)
    if not isinstance(rec, dict):
        return None
    return _gauge_from_record(rec, doc, recomputed=True)


def _load_gauge(root: Path, thread_id: str, current_max_seq, *,
                allow_recompute: bool = False) -> tuple[dict, bool]:
    """(coverage.gauge, stale_substrate). The verdict is PERSISTED — this
    reader only reports it, plus the read-time staleness of the artifact
    itself (gauge measured at an events high-water mark below the current
    one = the coverage claim describes an older substrate). When the
    artifact is missing AND `allow_recompute` is set, the §5c.8 read-repair
    computes a fresh verdict inline (read-only); the default stays the
    honest "unmeasured"."""
    gpath = root.joinpath(*GAUGE_RELPATH)
    if not gpath.is_file():
        if allow_recompute:
            gauge = _recompute_gauge(root, thread_id)
            if gauge is not None:
                # measured against the substrate as read — never stale
                return gauge, False
        return {"state": "unmeasured"}, False
    try:
        doc = json.loads(gpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "unmeasured", "note": "gauge artifact unreadable"}, False
    rec = (doc.get("threads") or {}).get(thread_id)
    if not isinstance(rec, dict):
        return {"state": "unmeasured"}, False
    gauge = _gauge_from_record(rec, doc)
    stale = False
    gmax = doc.get("events_max_seq")
    if (isinstance(gmax, int) and isinstance(current_max_seq, int)
            and current_max_seq > gmax):
        stale = True
        gauge["stale"] = True
    return gauge, stale


# ---------------------------------------------------------------------------
# Budgets (§3)
# ---------------------------------------------------------------------------


def _apply_budget(name: str, rows: list[dict], budget: int,
                  truncations: list[dict]) -> tuple[list[dict], str | None]:
    """Drop OLDEST rows (list is ts-ascending) until within budget. Emits a
    COUNTED truncation note when the cap bites."""
    total = len(rows)
    kept = list(rows)
    while kept and _est_tokens(kept) > budget:
        kept.pop(0)
    dropped = total - len(kept)
    if dropped:
        note = (f"truncated: {dropped} of {total} rows dropped "
                f"(oldest first) to fit the {budget}-token budget")
        truncations.append({"section": name, "dropped": dropped,
                            "kept": len(kept), "total": total,
                            "budget_tokens": budget})
        return kept, note
    return kept, None


def _note_verbatim_budget(name: str, content, budget: int,
                          truncations: list[dict]) -> str | None:
    est = _est_tokens(content)
    if est > budget:
        truncations.append({"section": name, "dropped": 0, "kept": None,
                            "total": None, "budget_tokens": budget,
                            "over_budget": True, "estimated_tokens": est})
        return (f"over the {budget}-token budget at ~{est} tokens; kept "
                f"verbatim (read-back mandate — never truncated)")
    return None


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def load_thread_knowledge(
    workspace_root: str | Path,
    thread_id: str,
    profile: str,
    *,
    now_iso: str | None = None,
    user_person_id: str | None = None,
    allow_recompute: bool = False,
) -> dict:
    """Compose the canonical knowledge payload for one resolved thread.

    `thread_id` is already resolved (project_*/person_*/org_*) — resolution
    stays with entity_resolve at call sites. `profile` is a NAME from the
    PROFILES table (§0.1). `allow_recompute` opts in to the §5c.8 gauge
    read-repair (inline recompute when the artifact is MISSING; read-only);
    the default keeps the honest "unmeasured". Raises ProfileError /
    ThreadNotFoundError; every substrate shortfall short of those degrades
    in `payload["degraded"]`.

    The returned payload may be a cached object — treat it as READ-ONLY
    (the cru_match R1 convention).
    """
    if not isinstance(profile, str):
        raise ProfileError(
            f"profile must be a name from PROFILES, got {type(profile).__name__} "
            "(inline profile dicts are rejected by design — SPEC_READER1 §0.1)")
    prof = PROFILES.get(profile)
    if prof is None:
        raise ProfileError(
            f"unknown profile {profile!r}; known: {sorted(PROFILES)}")

    root = Path(workspace_root)
    ent_path = root / "_hq" / "data" / "entities.json"
    try:
        # §5b.1 — torn-read retry: the canonical DEFENSIVE entities load.
        ent = unwrap_entities(_read_entities_defensive(ent_path))
    except (OSError, json.JSONDecodeError) as e:
        raise ThreadNotFoundError(
            f"{thread_id}: entities register unreadable ({e})") from e
    thread = _thread_record(ent, thread_id)
    if thread is None:
        raise ThreadNotFoundError(thread_id)

    # ---- cache probe -------------------------------------------------------
    brain_path, brain_reason = _rtls.resolve_brain_path(root, thread_id)
    notes_paths = _find_session_notes(root, thread)
    sig = _signature(root, brain_path, notes_paths)
    key = (str(root), thread_id, profile, now_iso, user_person_id,
           allow_recompute)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == sig:
        _CACHE_STATS["hits"] += 1
        return hit[1]

    now = _now(now_iso)
    degraded: list[str] = []
    truncations: list[dict] = []
    if brain_reason != "ok":
        # DISTINCT repair codes (spec §3): an empty folder_name wants the
        # folder recorded; a folder_name pointing nowhere wants the folder
        # located or the record corrected. Never collapse them.
        degraded.append(brain_reason)

    # ---- events load (privacy layer — PGUARD2) -----------------------------
    events_path = events_io.active_path(root)
    personal_withheld = None  # None = NOT MEASURED (absent-vs-zero, honored)
    if prof["privacy"] == "org":
        # stats= makes this module the THIRD documented carrier of the
        # withheld counter (census in events_io's docstring, pinned by
        # run_personal_tie_join_test C-4): the payload's coverage block is a
        # real disclosure surface, and without stats= an org-profile payload
        # could not distinguish "nothing withheld" from "not measured".
        stats: dict = {}
        events, skipped = events_io.load_events_org_scoped(root, stats=stats)
        # absent key = the drop layer did not run; keep None, never 0
        personal_withheld = stats.get("personal_withheld", None)
        opens_all = cru_match.load_open_commitments(
            events_path, events=events, workspace_root=root)
    else:
        # Owner tier reads through the allowlisted shard reader's owner seam
        # (fix round 2026-08-28) — full view, defensive, skipped channel
        # preserved; no raw events.jsonl read lives in this module.
        events, skipped = events_io.load_events_owner_scoped(root)
        opens_all = cru_match.load_open_commitments(
            events_path, workspace_root=root)

    # ---- membership (§0.2 — threads_of, PINNED) ----------------------------
    bound = [ev for ev in events if thread_id in event_refs.threads_of(ev)]
    max_seq = _max_human_seq(bound)

    # ---- open commitments + pending split ----------------------------------
    opens_thread = [ev for ev in opens_all
                    if thread_id in event_refs.threads_of(ev)]
    split_mode = prof["pending_split"]
    needs_review_held = None
    pointer_note = None
    if split_mode == "off":
        opens_final = opens_thread
    else:
        confirmed, pending = cru_match.split_pending_review(opens_thread)
        opens_final = confirmed
        needs_review_held = len(pending)
        if split_mode == "pointer" and pending:
            pointer_note = (f"{len(pending)} unconfirmed extraction(s) held — "
                            "see the needs-your-call queue")

    # ---- sections ----------------------------------------------------------
    # §5c.4 id-resolution map — built once, used by every row builder.
    names = _entity_names(ent)
    sections: dict[str, dict] = {}
    spec_sections = prof["sections"]

    if "identity" in spec_sections:
        content = _identity_content(thread, names)
        note = None
        b = spec_sections["identity"]["budget_tokens"]
        if _est_tokens(content) > b:
            for k in ("notes", "aliases"):
                if _est_tokens(content) > b:
                    content.pop(k, None)
            note = f"trimmed to fit the {b}-token budget"
            truncations.append({"section": "identity", "dropped": 0,
                                "kept": None, "total": None,
                                "budget_tokens": b, "trimmed_fields": True})
        sections["identity"] = {
            "content": content,
            "provenance": {"source": "entities.json",
                           "freshness_seq": max_seq,
                           "coverage_note": note},
        }

    if "live_state" in spec_sections:
        try:
            body, live_seq = _rtls.format_live_state(root, thread_id)
        except Exception as e:  # noqa: BLE001 — degrade, never crash the payload
            body, live_seq = None, None
            degraded.append(f"live_state_failed:{type(e).__name__}")
        b = spec_sections["live_state"]["budget_tokens"]
        note = _note_verbatim_budget("live_state", body, b, truncations) if body else None
        sections["live_state"] = {
            "content": body,   # VERBATIM — Bug #86 read-back mandate
            "provenance": {"source": "render_thread_live_state.format_live_state",
                           "freshness_seq": live_seq,
                           "coverage_note": note},
        }

    if "open_commitments" in spec_sections:
        sec = spec_sections["open_commitments"]
        rows = _open_rows(opens_final, names,
                          meeting_links=bool(sec.get("meeting_links")))
        rows, note = _apply_budget("open_commitments", rows,
                                   sec["budget_tokens"], truncations)
        notes = [n for n in (note, pointer_note) if n]
        sections["open_commitments"] = {
            "content": rows,
            "provenance": {"source": "cru_match open-set projection "
                                     "(closure-folded), threads_of membership",
                           "freshness_seq": _max_human_seq(opens_final),
                           "coverage_note": "; ".join(notes) or None},
        }

    if "recent_activity" in spec_sections:
        sec = spec_sections["recent_activity"]
        floor_iso = (now - timedelta(days=sec["window_days"])).isoformat()
        rows = _recent_event_rows(bound, floor_iso)
        rows, note = _apply_budget("recent_activity", rows,
                                   sec["budget_tokens"], truncations)
        notes = [f"window {sec['window_days']}d"]
        if note:
            notes.append(note)

        # session-notes narrative + the §0.7 swept-origin filter
        blocks = _session_notes_blocks(notes_paths)
        swept = [bl for bl in blocks if bl["origin"] == "swept"]
        if prof["exclude_swept_narrative"]:
            blocks = [bl for bl in blocks if bl["origin"] != "swept"]
            if swept:
                notes.append(f"{len(swept)} swept-origin block(s) excluded "
                             "pending a confirming touch")
        else:
            for bl in swept:
                bl["label"] = "origin: swept — unconfirmed narrative"
        sections["recent_activity"] = {
            "content": {"events": rows, "session_notes": blocks},
            "provenance": {"source": "events.jsonl (threads_of) + SESSION_NOTES",
                           "freshness_seq": _max_human_seq(bound),
                           "coverage_note": "; ".join(notes)},
        }

    if "decisions" in spec_sections:
        sec = spec_sections["decisions"]
        wd = sec.get("window_days")
        floor_iso = (now - timedelta(days=wd)).isoformat() if wd else None
        # §5b.2 — the supersession overlay folds over the FULL event list:
        # a supersede/reaffirm marker is not always thread-bound.
        overlays = _rdl._categorize_decisions(events)
        rows, n_closed = _decision_rows(
            bound, floor_iso, bool(sec.get("keep_pinned")), overlays)
        rows, note = _apply_budget("decisions", rows, sec["budget_tokens"],
                                   truncations)
        notes = []
        if wd:
            notes.append(f"window {wd}d"
                         + (" + pinned" if sec.get("keep_pinned") else ""))
        if n_closed:
            # DECSHAPES1 — "superseded" was accurate when that was the only
            # closed bucket; it would now silently mislabel a carried-out
            # ruling as a replaced one in the coverage note a reader uses to
            # judge what the payload left out.
            notes.append(f"{n_closed} closed decision(s) excluded "
                         f"(superseded or carried out)")
        if note:
            notes.append(note)
        sections["decisions"] = {
            "content": rows,
            "provenance": {"source": "events.jsonl decision family "
                                     "(supersession folded)",
                           "freshness_seq": _max_human_seq(bound),
                           "coverage_note": "; ".join(notes) or None},
        }

    if prof["cross_thread"] != "off":
        persons = _payload_persons(thread, bound, prof["cross_thread"])
        if user_person_id:
            persons.discard(user_person_id)  # the CEO is on every thread
        rows, over = _cross_thread_rows(events, ent, names, thread_id,
                                        persons, now)
        note = (f"window {CROSS_THREAD_WINDOW_DAYS}d, cap {CROSS_THREAD_CAP}"
                + (f"; {over} additional match(es) beyond the cap" if over else ""))
        sections["cross_thread"] = {
            "content": rows,
            "provenance": {"source": "person-graph expansion over sibling "
                                     "threads (§0.4)",
                           "freshness_seq": _max_human_seq(rows and [
                               ev for ev in events
                               if ev.get("seq") in {r["seq"] for r in rows}
                           ] or []),
                           "coverage_note": note},
        }

    # ---- coverage (§0.3 — persisted gauge + read-time degradation) ---------
    gauge, stale = _load_gauge(root, thread_id, _max_human_seq(events),
                               allow_recompute=allow_recompute)
    coverage = {
        "gauge": gauge,
        "skipped_lines": len(skipped),
        "needs_review_held": needs_review_held,
        "personal_withheld": personal_withheld,   # None = not measured
        "stale_substrate": stale,
        "truncations": truncations,
    }
    if skipped:
        degraded.append("skipped_lines")

    # ---- 5b.3 empty-payload degrade (BEGIN — the degraded suite's mutation
    # fence cuts exactly this sentinel block; keep both marker comments) -----
    # SPEC_READER1 §5b.3 "degrade loudly, never answer thin": the thread
    # RESOLVED (ThreadNotFoundError already ruled out) yet every substantive
    # section is empty — no SUBSTANCE events bound (§5c.3: lifecycle rows
    # like thread_updated are system bookkeeping and must not defeat the
    # guard — the retest's 3-lifecycle-event defeat case; recent activity
    # and decisions render nothing substantive without substance), no open
    # commitments confirmed OR held, and no live-state people. That is the
    # CONFIDENT-EMPTY payload a decoy or archived workspace produces (the
    # decoy-_hq bug class, SPEC_WSPICK1): nothing here is wrong-looking, it
    # is just silently hollow. Until WSPICK1 discovery validation lands,
    # degrade LOUDLY: the entry names the thread, and coverage.empty_payload
    # tells the consuming surface it must not answer thin (ask-first, or say
    # the substrate holds nothing).
    bound_substance = sum(
        1 for ev in bound if substance_events.is_substance_event(ev))
    if (bound_substance == 0 and not opens_final
            and not (needs_review_held or 0)
            and _live_state_people(root, thread_id) == 0):
        coverage["empty_payload"] = True
        degraded.append(
            f"empty_payload: thread {thread_id} "
            f"({_thread_display_name(ent, thread_id)}) resolves but the "
            "substrate holds nothing reachable for it — no substance "
            "events bound, no live-state people, no open commitments, no "
            "decisions; a consuming surface must not answer thin "
            "(SPEC_READER1 §5b.3)")
    # ---- 5b.3 empty-payload degrade (END) ----------------------------------

    payload = {
        "thread_id": thread_id,
        "profile": profile,
        "sections": sections,
        "section_order": [s for s in prof["section_order"] if s in sections],
        "coverage": coverage,
        "degraded": degraded,
        "provenance": {
            "generated": now.isoformat(),
            "events_total": len(events),
            "bound_events": len(bound),
            "max_bound_seq": max_seq,
            "profile_privacy": prof["privacy"],
        },
    }
    _CACHE[key] = (sig, payload)
    _CACHE_STATS["builds"] += 1
    return payload


if __name__ == "__main__":
    import sys
    # Self-configure UTF-8 — Cowork invokes this from a cp1252 Windows
    # console; never rely on the caller setting PYTHONUTF8 (the
    # verify_fleet/tag_release unicode-crash class). Same pattern as
    # tests/run_all.py.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — non-console stream
            pass
    _args = [a for a in sys.argv[1:] if a != "--allow-recompute"]
    _recompute = "--allow-recompute" in sys.argv[1:]   # §5c.8 opt-in
    ws, tid, prof_name = _args[0], _args[1], (
        _args[2] if len(_args) > 2 else "catch-all")
    out = load_thread_knowledge(ws, tid, prof_name,
                                allow_recompute=_recompute)
    print(json.dumps(copy.deepcopy(out), indent=1, ensure_ascii=False,
                     default=str))
