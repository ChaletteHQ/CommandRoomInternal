#!/usr/bin/env python3
"""GORENDER1 — coverage-gated render of the five memory anchors in a
project's PROJECT_BRAIN.md (memory program R1, first render).

MIGRATE1 seeded five question-section anchors into every safe brain file
(`where-things-stand`, `what-we-decided`, `whats-owed`, `landmines-judgment`,
`how-we-work-this`) with a placeholder promising "rendered on first
coverage-gated go". This module IS that render: on a `go` where the thread's
persisted binding gauge says READY, each anchor's interior is drawn from the
canonical reader payload (`load_thread_knowledge`, profile "go-render") via
`render_brain_block`'s existing marker machinery. Durable hand-owned content
outside the markers is never touched.

THE GATE (SPEC_READER1 §5c.5, line-71 ruling). Content is written ONLY when
`coverage.gauge.state == "ready"`. A `not_ready` or `unmeasured` thread keeps
its seeded-empty anchors byte-identical — that is the contract the seed
placeholder text promises, not a failure mode. `coverage.empty_payload` also
refuses (a confident-empty payload must never be etched into a durable file —
SPEC_READER1 §5b.3). A stale-but-READY gauge does NOT block the render: the
gauge verdict is about binding quality, staleness of the measurement is
reported by the reader, and the dirty-check keeps the rendered content itself
current.

HAND-EDIT POSTURE (recorded default — the renderer half of MIGRATE1 spec
fix 3, deliberately deferred there): REFUSE-WITH-DISCLOSURE. Before writing,
each anchor is classified with `migrate_seed_anchors.anchor_state` (the same
fence-aware classifier the migration used). An anchor whose start marker has
no machine provenance ("hand"), or whose seeded interior grew non-comment
content ("hand-content"), is NOT rendered — the human's content survives
byte-identical and the anchor is reported `refused_hand_content` /
`refused_hand_anchor` for a one-sentence plain-language disclosure at the
surface. Malformed marker pairs refuse likewise. A displaced-content snapshot
was considered and NOT built on this branch: refusing preserves everything a
snapshot would, without inventing a new artifact format — flip to snapshot in
a later train if drain pressure appears.

FENCE-AWARENESS (the renderer half of MIGRATE1 spec fix 4, closed here for
THIS surface without editing render_brain_block): classification runs over
`mask_code_spans`-masked text, so a marker pair that exists only inside a
code fence classifies "absent" → `no_anchor`, and `render_block` (which is
not fence-aware at HEAD) is never invoked against it — the in-fence write the
MIGRATE1 deviations list warned about cannot happen through this module.
`render_brain_block.py` itself is deliberately untouched: adding refuse/fence
semantics there would change the LIVE-STATE:people contract ("the renderer
owns that block" — hand edits there are clobbered BY DESIGN).

CANDOR (operator ruling 2026-08-31, binding): the landmines-judgment section
renders FULL CANDOR by default — M: people don't share these files. The knob
is per-workspace skill_config, file `_hq/data/skill_config/brain_render.json`,
key `brain_candor`, values "full" (default, the live behavior) |
"process-only" (for client fleets set at onboarding). Mechanical rule for the
person-sensitive class: a landmine line is PERSON-SCOPED when its source row
came from the reader's person-graph expansion (`cross_thread` rows — every
one carries person linkage by construction; the Brad-pricing-guardrail class
the ruling was about). In-thread judgment notes are topic/process class: they
are this project's own record, already visible in this project's own file.
On "process-only" the person-scoped class is withheld and replaced by ONE
counted plain-language line. A PRESENT-but-invalid knob value fails CLOSED to
"process-only" (someone tried to restrict; a typo must not silently restore
full candor) and is flagged `candor_invalid` in the result — the absent-key
default stays "full".

REGISTER (RENDER2, logic v2 — operator feedback on the first live render,
2026-09-02): the five composers write chief-of-staff SYNTHESIS, not payload
dumps. where-things-stand is narrative state (cadence, current push, latest
note, decision arc, open-load tension) that references the other sections by
COUNT and never restates an open item or a ruling — `_drop_reserved` is the
dedup seam that enforces it. whats-owed is a direction-split ledger (you owe /
owed to you / no owner on record) with owner, due, and age per line, split on
the workspace's resolved primary user (`primary_user` seam; unresolvable →
one undivided owner-named list). what-we-decided is standing rulings only,
newest-first, one line each. landmines-judgment renders each caution with its
provenance (source thread + date). how-we-work-this carries durable process
facts only — the v1 `Status:` line was transient state and is gone. Every
section caps its lines per `_SECTION_CAPS` and discloses overflow with ONE
counted "…plus N more" line — silent truncation never ships.

NAMES, NEVER IDS (SPEC_READER1 §5c.4 carried into a durable file): rendered
bodies use the payload's resolved display names; any raw person_/org_/
project_/thread_ token that survives into composed text (e.g. inside a
free-text event title) is substituted from the payload's own id→name pairs or
elided. Rendered content also carries no internal vocabulary — no event-type
tokens, no coverage jargon.

DIRTY-CHECK / IDEMPOTENCE: `render_brain_block.needs_render` gates every
write on (stamp seq, logic_v). The stamp seq is the max freshness seq across
the payload's sections (cross-thread rows can outrun the thread's own seqs).
An unchanged payload re-`go` rewrites nothing — byte-identical file. Honest
limit: a NEW sibling-thread landmine that enters the payload without raising
any section freshness seq above the stamp would wait for the next in-thread
event or a logic_v bump; the dirty-check is an optimization, not the truth.

stdlib + sibling shared/scripts modules only.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import render_brain_block as rbb
import render_thread_live_state as _rtls
from load_thread_knowledge import PROFILES as _READER_PROFILES
from load_thread_knowledge import load_thread_knowledge
from release_actions.migrate_seed_anchors import (
    QUESTION_BLOCK_IDS,
    anchor_state,
    mask_code_spans,
)

__all__ = [
    "ANCHOR_IDS",
    "RENDER_LOGIC_VERSION",
    "RENDER_PROFILE",
    "DEFAULT_CANDOR",
    "render_thread_anchors",
    "compose_anchor_bodies",
]

# The five ids are MIGRATE1's — imported, not re-declared, so the seed and
# the render can never drift apart.
ANCHOR_IDS: list[str] = list(QUESTION_BLOCK_IDS)

# Bump when body-composition logic changes: needs_render() re-renders quiet
# threads once per bump (Bug #97 pattern).
# v2 — RENDER2: composers rewritten from list-dumps to chief-of-staff
# synthesis (the operator's live feedback on the first render, 2026-09-02).
# The bump is load-bearing: every v1-stamped anchor in the fleet re-renders
# under the new composers on its next coverage-gated go, payload unchanged.
RENDER_LOGIC_VERSION = 2

# The reader profile this render consumes. "go-render" (not "go"): the
# chat-facing go payload stays byte-identical to its frozen R1 shape, while
# the render needs sibling-thread landmine rows (cross_thread "graph") and a
# durable-file window. See PROFILES["go-render"] in load_thread_knowledge.
RENDER_PROFILE = "go-render"

DEFAULT_CANDOR = "full"                  # operator ruling 2026-08-31
CANDOR_VALUES = ("full", "process-only")
CONFIG_SKILL = "brain_render"            # _hq/data/skill_config/brain_render.json
CONFIG_KEY = "brain_candor"

_HEADINGS = {
    "where-things-stand": "Where things stand",
    "what-we-decided": "What we decided",
    "whats-owed": "What's owed",
    "landmines-judgment": "Landmines & judgment",
    "how-we-work-this": "How we work this",
}

# Per-section line budgets (RENDER2 recorded default). Each composer caps its
# own output and, where rows exist beyond the cap, emits ONE honest counted
# overflow line — silent truncation is the v1 defect class, never repeated.
# whats-owed's budget is PER DIRECTION (you owe / owed to you are separate
# ledgers with separate caps).
_SECTION_CAPS = {
    "where-things-stand": 6,   # narrative sentences, 3-6 by design
    "what-we-decided": 8,      # standing rulings, newest-first
    "whats-owed": 5,           # per direction group
    "landmines-judgment": 8,
    "how-we-work-this": 6,
}

_RAW_ID_RE = re.compile(r"\b(?:person|org|project|thread)_[A-Za-z0-9]+\b")
_EMPTY_LINE = "Nothing on the record yet."

# The reader window the narrative section describes — welded to the profile
# table so the prose can never claim a window the reader did not apply.
_ACTIVITY_WINDOW_DAYS = (_READER_PROFILES[RENDER_PROFILE]
                         ["sections"]["recent_activity"]["window_days"])


# ---------------------------------------------------------------------------
# Candor knob
# ---------------------------------------------------------------------------


def resolve_candor(workspace_root: str | Path) -> tuple[str, str | None]:
    """(candor, invalid_raw). Absent key → DEFAULT_CANDOR ("full").
    Present-but-invalid → "process-only" (fail closed) + the raw value."""
    try:
        from skill_config_writer import get_config
        cfg = get_config(workspace_root, CONFIG_SKILL,
                         {CONFIG_KEY: DEFAULT_CANDOR})
        raw = cfg.get(CONFIG_KEY, DEFAULT_CANDOR)
    except Exception:  # noqa: BLE001 — a broken config file must not stop a go
        return DEFAULT_CANDOR, None
    if raw in CANDOR_VALUES:
        return raw, None
    return "process-only", str(raw)


# ---------------------------------------------------------------------------
# Body composition (pure — payload in, five markdown bodies out)
# ---------------------------------------------------------------------------


def _date(ts: str | None) -> str:
    return (ts or "")[:10]


def _id_replacements(payload: dict) -> dict[str, str]:
    """id → display-name pairs the payload itself carries (§5c.4 additive
    resolution, reused here as the scrub map)."""
    rep: dict[str, str] = {}
    secs = payload.get("sections", {})
    ident = (secs.get("identity") or {}).get("content") or {}
    for id_key, name_key in (("org_id", "org_name"),
                             ("affiliation_id", "affiliation_name"),
                             ("key_contact_id", "key_contact_name")):
        if ident.get(id_key) and ident.get(name_key):
            rep[ident[id_key]] = ident[name_key]
    for row in (secs.get("open_commitments") or {}).get("content") or []:
        if row.get("owner_person_id") and row.get("owner_name"):
            rep[row["owner_person_id"]] = row["owner_name"]
    for row in (secs.get("cross_thread") or {}).get("content") or []:
        for pid, nm in zip(row.get("person_ids") or [],
                           row.get("person_names") or []):
            if pid and nm:
                rep.setdefault(pid, nm)
    return rep


def _scrub_ids(text: str, rep: dict[str, str]) -> str:
    """Names, never ids: substitute known ids, elide unknown ones."""
    def sub(m: re.Match) -> str:
        return rep.get(m.group(0), "")
    out = _RAW_ID_RE.sub(sub, text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r" +([,.;:)])", r"\1", out).strip()


def _norm_text(text: str) -> str:
    """Dedup normal form: lowercase, punctuation and runs of whitespace
    collapsed to single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _age_days(ts: str | None, now_date: str) -> int:
    try:
        d0 = datetime.strptime((ts or "")[:10], "%Y-%m-%d").date()
        d1 = datetime.strptime(now_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0
    return max(0, (d1 - d0).days)


def _reserved_titles(secs: dict) -> set[str]:
    """Normalized titles already owned by whats-owed (open commitments) and
    what-we-decided (rulings) — the cross-section dedup census. The live v1
    defect was exactly these re-appearing as undated where-things-stand
    clones; any narrative candidate containing one is dropped."""
    reserved: set[str] = set()
    for row in (secs.get("open_commitments") or {}).get("content") or []:
        n = _norm_text(row.get("title") or "")
        if n:
            reserved.add(n)
    for row in (secs.get("decisions") or {}).get("content") or []:
        n = _norm_text(row.get("title") or "")
        if n:
            reserved.add(n)
    return reserved


def _drop_reserved(lines: list[str], reserved: set[str]) -> list[str]:
    """Drop any narrative candidate whose normalized text contains a title
    that already lives in whats-owed or what-we-decided. THE dedup seam —
    gutting this returns the live v1 clone defect (mutation-pinned in
    run_render2_test.py)."""
    out = []
    for ln in lines:
        n = _norm_text(ln)
        if any(t and t in n for t in reserved):
            continue
        out.append(ln)
    return out


def _where_things_stand(secs: dict, now_date: str) -> list[str]:
    """NARRATIVE state — what changed lately, the current push, the key
    tension — synthesized from recent_activity + the decisions arc + the open
    load. Never a restatement of open items or rulings: titled clones are
    dropped by `_drop_reserved`, and the owed/decided sections are referenced
    by count only."""
    ra = (secs.get("recent_activity") or {}).get("content") or {}
    events = ra.get("events") or []
    reserved = _reserved_titles(secs)
    lines: list[str] = []

    # Cadence — how much moved, how recently.
    if events:
        latest = max((r.get("ts") or "") for r in events)[:10]
        n = len(events)
        lines.append(f"{n} recorded touch{'es' if n != 1 else ''} in the "
                     f"last {_ACTIVITY_WINDOW_DAYS} days, the latest on "
                     f"{latest}.")

    # The current push — the newest activity-stream row that is not itself
    # an owed item or a ruling (those live in their own sections).
    stream = [r for r in events
              if (r.get("title") or "").strip()
              and r.get("family") not in ("decision", "commitment",
                                          "commitment_resolved")]
    for row in sorted(stream, key=lambda r: (r.get("ts") or ""),
                      reverse=True):
        title = row["title"].strip()
        if row.get("family") == "meeting":
            cand = (f'Most recent working session: "{title}" '
                    f"({_date(row.get('ts'))}).")
        else:
            cand = f"Most recent development: {title} ({_date(row.get('ts'))})."
        kept = _drop_reserved([cand], reserved)
        if kept:
            lines.append(kept[0])
            break

    # Where the last session's narrative left things.
    notes = ra.get("session_notes") or []
    if notes:
        top = notes[0]
        clip = re.sub(r"\s*[-*]\s+", " ", top.get("text") or "")
        clip = " ".join(clip.split())[:180]
        if clip:
            heading = (top.get("heading") or "").strip()
            cand = (f"Where the last note left it ({heading}): {clip}"
                    if heading else f"Where the last note left it: {clip}")
            lines.extend(_drop_reserved([cand], reserved))

    # Decision arc — recency and weight, never restated titles.
    dec = [r for r in (secs.get("decisions") or {}).get("content") or []
           if r.get("status") == "active"]
    if dec:
        newest = max((r.get("ts") or "") for r in dec)[:10]
        n = len(dec)
        verb = "shapes" if n == 1 else "shape"
        lines.append(f"{n} standing ruling{'s' if n != 1 else ''} {verb} the "
                     f"direction here, the newest set {newest} — listed under "
                     "What we decided.")

    # The key tension — the open load, counted, never titled.
    opens = (secs.get("open_commitments") or {}).get("content") or []
    if opens:
        overdue = sum(1 for r in opens
                      if r.get("due") and str(r["due"]) < now_date)
        oldest = max((_age_days(r.get("ts"), now_date) for r in opens),
                     default=0)
        bits = [f"{len(opens)} open item{'s' if len(opens) != 1 else ''} "
                "in play"]
        if overdue:
            bits.append(f"{overdue} past due")
        line = ", ".join(bits)
        if oldest >= 14:
            line += f"; the oldest has sat {oldest} days"
        lines.append(line + " — the ledger is under What's owed.")

    return [f"- {ln}" for ln in lines[:_SECTION_CAPS["where-things-stand"]]]


def _what_we_decided(secs: dict) -> list[str]:
    """The standing rulings, one line each, newest-first. Superseded rulings
    are already folded out by the reader (§5b.2); the status filter here is
    the belt — a row that arrives marked anything but active never renders."""
    rows = [r for r in (secs.get("decisions") or {}).get("content") or []
            if r.get("status") == "active" and (r.get("title") or "").strip()]
    rows.sort(key=lambda r: ((r.get("ts") or ""),
                             r.get("seq") if isinstance(r.get("seq"), int)
                             else 0),
              reverse=True)
    cap = _SECTION_CAPS["what-we-decided"]
    out = [f"- {_date(r.get('ts'))} — {r['title'].strip()}"
           for r in rows[:cap]]
    extra = len(rows) - cap
    if extra > 0:
        out.append(f"- …plus {extra} more standing ruling"
                   f"{'s' if extra != 1 else ''} on file, not shown here.")
    return out


def _owed_line(row: dict, now_date: str, *, with_owner: bool) -> str:
    bits = []
    if with_owner:
        bits.append(row.get("owner_name") or "owner unrecorded")
    bits.append(f"due {row['due']}" if row.get("due") else "no date set")
    age = _age_days(row.get("ts"), now_date)
    bits.append(f"open {age} days" if age >= 1 else "opened today")
    return f"- {row['title'].strip()} — {', '.join(bits)}"


def _whats_owed(secs: dict, primary_user_id: str | None,
                now_date: str) -> list[str]:
    """Direction-split ledger: what you owe others / what others owe you,
    each line carrying owner (where it is not you), due, and age. Rows with
    no recorded owner get their own honest group rather than a guessed
    direction. When the workspace's primary user cannot be resolved at all,
    the split would be fiction — one undivided list with owner names renders
    instead (recorded degrade, not an error)."""
    rows = [r for r in (secs.get("open_commitments") or {}).get("content")
            or [] if (r.get("title") or "").strip()]
    cap = _SECTION_CAPS["whats-owed"]
    out: list[str] = []

    def emit(label: str, lines: list[str]) -> None:
        if not lines:
            return
        if out:
            out.append("")
        out.append(f"**{label}:**")
        out.extend(lines[:cap])
        extra = len(lines) - cap
        if extra > 0:
            out.append(f"- …plus {extra} more, not shown here.")

    if primary_user_id:
        you = [_owed_line(r, now_date, with_owner=False) for r in rows
               if r.get("owner_person_id") == primary_user_id]
        them = [_owed_line(r, now_date, with_owner=True) for r in rows
                if r.get("owner_person_id")
                and r.get("owner_person_id") != primary_user_id]
        unowned = [_owed_line(r, now_date, with_owner=False) for r in rows
                   if not r.get("owner_person_id")]
        emit("You owe", you)
        emit("Owed to you", them)
        emit("No owner on record", unowned)
    else:
        emit("Open items", [_owed_line(r, now_date, with_owner=True)
                            for r in rows])
    return out


def _landmines(secs: dict, candor: str) -> tuple[list[str], int]:
    """(lines, n_person_rows_withheld). In-thread judgment notes are the
    topic/process class; cross_thread person-graph rows are the
    person-sensitive class (see module docstring for the mechanical rule).
    Each caution carries its provenance: where it was seen and when."""
    ra = (secs.get("recent_activity") or {}).get("content") or {}
    lines: list[str] = []
    for row in reversed(ra.get("events") or []):     # newest first
        if row.get("type") not in ("note", "intel_logged"):
            continue
        title = (row.get("title") or "").strip()
        if title:
            lines.append(f"- {title} (noted {_date(row.get('ts'))})")
    cross = (secs.get("cross_thread") or {}).get("content") or []
    withheld = 0
    for row in cross:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        if candor == "process-only":
            withheld += 1
            continue
        label = row.get("label") or ""
        if label.startswith("related, from "):
            prov = f"seen in {label[len('related, from '):]}, " \
                   f"{_date(row.get('ts'))}"
        else:
            prov = f"logged outside any project, {_date(row.get('ts'))}"
        names = ", ".join(n for n in (row.get("person_names") or []) if n)
        mid = f" — involves {names}" if names else ""
        lines.append(f"- {title}{mid} ({prov})")
    cap = _SECTION_CAPS["landmines-judgment"]
    shown, extra = lines[:cap], len(lines) - cap
    if extra > 0:
        shown.append(f"- …plus {extra} more caution"
                     f"{'s' if extra != 1 else ''} on file, not shown here.")
    if withheld:
        shown.append(f"- {withheld} judgment note(s) involving people "
                     "withheld by this workspace's privacy setting.")
    return shown, withheld


def _how_we_work(secs: dict) -> list[str]:
    """Durable process facts only — affiliation, roles, standing notes.
    Transient state (thread status and the like) never renders here: that is
    where-things-stand's job (RENDER2 recorded default; the v1 body carried a
    `Status:` line)."""
    ident = (secs.get("identity") or {}).get("content") or {}
    out: list[str] = []
    org = ident.get("org_name") or ident.get("affiliation_name")
    if org:
        out.append(f"- Part of {org}.")
    if ident.get("key_contact_name"):
        out.append(f"- Key contact: {ident['key_contact_name']}.")
    notes = ident.get("notes")
    if isinstance(notes, str) and notes.strip():
        out.append("- " + " ".join(notes.split())[:200])
    elif isinstance(notes, list):
        out.extend("- " + " ".join(str(n).split())[:200] for n in notes if n)
    return out[:_SECTION_CAPS["how-we-work-this"]]


def compose_anchor_bodies(payload: dict, candor: str, *,
                          primary_user_id: str | None = None,
                          now_iso: str | None = None) -> tuple[dict, int]:
    """{anchor_id: markdown body} from a "go-render" payload, plus the count
    of person-sensitive rows withheld under "process-only". Pure.

    `primary_user_id` powers the whats-owed direction split (you owe / owed
    to you); None renders one undivided owner-named list. `now_iso` anchors
    age and overdue arithmetic; None uses the current UTC date."""
    secs = payload.get("sections", {})
    rep = _id_replacements(payload)
    now_date = (now_iso or datetime.now(timezone.utc).isoformat())[:10]
    land, withheld = _landmines(secs, candor)
    raw = {
        "where-things-stand": _where_things_stand(secs, now_date),
        "what-we-decided": _what_we_decided(secs),
        "whats-owed": _whats_owed(secs, primary_user_id, now_date),
        "landmines-judgment": land,
        "how-we-work-this": _how_we_work(secs),
    }
    bodies = {}
    for bid, lines in raw.items():
        lines = [_scrub_ids(ln, rep) if ln else "" for ln in lines]
        if not any(ln.strip() for ln in lines):
            lines = [f"- {_EMPTY_LINE}"]
        bodies[bid] = f"### {_HEADINGS[bid]}\n\n" + "\n".join(lines)
    return bodies, withheld


# ---------------------------------------------------------------------------
# The render
# ---------------------------------------------------------------------------


def _stamp_seq(payload: dict) -> int:
    """Max freshness seq across sections + the bound max — the dirty-check
    watermark. 0 when the substrate carries no human seqs at all (a stable
    stamp; None would re-render every go)."""
    best = payload.get("provenance", {}).get("max_bound_seq")
    best = best if isinstance(best, int) else 0
    for sec in payload.get("sections", {}).values():
        fs = (sec.get("provenance") or {}).get("freshness_seq")
        if isinstance(fs, int) and fs > best:
            best = fs
    return best


def render_thread_anchors(
    workspace_root: str | Path,
    thread_id: str,
    *,
    now_iso: str | None = None,
    payload: dict | None = None,
) -> dict:
    """Coverage-gated render of the five memory anchors for one thread.

    Returns a result dict — never raises for substrate conditions:
      {"thread_id", "gate": {"state", "rendered", "reason"},
       "candor", "candor_invalid"?, "withheld_person_rows",
       "brain_path"?, "anchors": {id: {"status": ...}}, "stamp_seq"?}

    Anchor statuses: "written" | "skipped_clean" (dirty-check) | "unchanged"
    | "no_anchor" (absent or fence-quoted only — seeding is MIGRATE1's job,
    never done here) | "refused_hand_anchor" | "refused_hand_content" |
    "refused_malformed" (all three: the human's bytes survive untouched).

    `payload` is a test seam (inject a reader payload); production callers
    omit it and the canonical reader is called with the "go-render" profile.
    """
    root = Path(workspace_root)
    if payload is None:
        payload = load_thread_knowledge(root, thread_id, RENDER_PROFILE,
                                        now_iso=now_iso)
    candor, invalid = resolve_candor(root)
    result: dict = {"thread_id": thread_id,
                    "candor": candor,
                    "withheld_person_rows": 0,
                    "anchors": {}}
    if invalid is not None:
        result["candor_invalid"] = invalid

    gauge = (payload.get("coverage") or {}).get("gauge") or {}
    state = gauge.get("state", "unmeasured")
    if state != "ready":
        # THE GATE (§5c.5): seeded-empty anchors stay byte-identical.
        result["gate"] = {"state": state, "rendered": False,
                          "reason": "gauge not READY — anchors left as "
                                    "seeded (the coverage-gate contract)"}
        return result
    if (payload.get("coverage") or {}).get("empty_payload"):
        result["gate"] = {"state": state, "rendered": False,
                          "reason": "empty payload — a confident-empty "
                                    "payload is never etched into a durable "
                                    "file (SPEC_READER1 §5b.3)"}
        return result

    brain_path, reason = _rtls.resolve_brain_path(root, thread_id)
    if reason != "ok":
        result["gate"] = {"state": state, "rendered": False, "reason": reason}
        return result
    result["brain_path"] = str(brain_path)

    now = (now_iso or datetime.now(timezone.utc)
           .isoformat(timespec="seconds")).replace("+00:00", "Z")
    try:
        from primary_user import resolve_primary_user
        primary = resolve_primary_user(root)
    except Exception:  # noqa: BLE001 — no resolvable user must not stop a go
        primary = None   # whats-owed degrades to one undivided owner-named list
    bodies, withheld = compose_anchor_bodies(payload, candor,
                                             primary_user_id=primary,
                                             now_iso=now)
    result["withheld_person_rows"] = withheld
    seq = _stamp_seq(payload)
    result["stamp_seq"] = seq

    text = brain_path.read_text(encoding="utf-8")
    masked, _unterminated = mask_code_spans(text)
    rendered_any = False
    for bid in ANCHOR_IDS:
        st = anchor_state(text, masked, bid)["state"]
        if st == "absent":
            result["anchors"][bid] = {"status": "no_anchor"}
            continue
        if st == "malformed":
            result["anchors"][bid] = {"status": "refused_malformed"}
            continue
        if st == "hand":
            result["anchors"][bid] = {"status": "refused_hand_anchor"}
            continue
        if st == "hand-content":
            result["anchors"][bid] = {"status": "refused_hand_content"}
            continue
        # stamped (seed placeholder) or rendered (a prior render) — ours.
        if not rbb.needs_render(brain_path, bid, seq,
                                logic_version=RENDER_LOGIC_VERSION):
            result["anchors"][bid] = {"status": "skipped_clean"}
            continue
        r = rbb.render_block(brain_path, bid, bodies[bid],
                             generated_at=now, source_seq=seq,
                             logic_version=RENDER_LOGIC_VERSION,
                             create_parents=False)
        result["anchors"][bid] = {"status": r["status"]}
        if r["status"] == "written":
            rendered_any = True
            # re-read: render_block rewrote the file; later anchors must
            # classify against the current bytes.
            text = brain_path.read_text(encoding="utf-8")
            masked, _unterminated = mask_code_spans(text)

    result["gate"] = {"state": state, "rendered": rendered_any,
                      "reason": "ok"}
    return result


if __name__ == "__main__":
    import sys
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — non-console stream
            pass
    if len(sys.argv) < 3:
        print("usage: render_brain_anchors.py <workspace_root> <thread_id>")
        raise SystemExit(2)
    out = render_thread_anchors(sys.argv[1], sys.argv[2])
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
