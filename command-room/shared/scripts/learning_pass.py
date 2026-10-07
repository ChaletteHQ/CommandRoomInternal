#!/usr/bin/env python3
"""The `learning` job — corrections the customer has already made become the
way the product writes (SPEC_LEARN1, amended 2026-09-02).

WHAT WAS WRONG. Every correction channel has been capturing for months: a
voice-corrections log per writing skill, a graded prep brief per meeting, a
structural-corrections log per document kind. The halves that turn those into
behaviour all exist and are tested — `voice_corrections.group_op_patterns`,
`prep_grading.propose_section_weights`, `exemplars.propose_exemplar_updates`.
They had NO CALLER. The passes that were supposed to run them were written as
interactive review items inside a skill's prose, and the job that runs that
skill is a silent background leg with no chat surface, so the review item was
rendered to nobody, every week, for months. Sixty-six banked corrections and
seventy graded briefs changed nothing.

WHAT THIS IS. One code job, three automatic legs, inside the existing
`maintenance` fire. No hooks, no new task, stdlib only:

  1. voice     — repeated corrections per writing skill become a learned line
                 in that skill's voice-block override.
  2. prep      — a prep section that keeps coming up empty for a meeting type
                 gets a lower weight in call-prep's config.
  3. exemplar  — repeated structural corrections on one document kind promote
                 the corrected document to that kind's workspace exemplar.

POSTURE. Every leg goes through `brain_proposals.propose(tier="auto")` FIRST,
so the shared ledger owns the cooldown and the dedup, and the refusal path is
the safety: a class with no registered reverser cannot apply at all. Each
applied change carries one `brain_batch_id` and a `previous_*` snapshot, is
narrated once in the morning brief's CHANGED line through `change_feed`, and
reverses on one `undo`. A leg that raises records its error in the receipt and
the remaining legs still run — a job that half-failed must say so rather than
look like a quiet week.

FLOORS ARE NOT TUNING. >=3 same-pattern corrections per skill (3 phrases per
skill per run), >=6 graded meetings of a type, >=3 same-direction structural
corrections on a kind, a 60-day cooldown on anything declined, and every
applied fingerprint excluded forever. They are what make applying without
asking safe; they are not knobs for making it learn faster.
"""
from __future__ import annotations

import argparse
import json
import secrets
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional


def _effective_fired_via(explicit):
    """`receipts.effective_fired_via`, behind an import that cannot break."""
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001 - a resolver that raises is worse
        return explicit if explicit is not None else "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

TASK_ID = "learning"
# The ledger `pass` name and the proposal `detector` are the same word: the
# cooldown and the applied set are scoped to this job and to nothing else.
PASS_NAME = "learning"
DETECTOR = "learning"
SOURCE_SKILL = "learning"
BATCH_PREFIX = "lrn_"

# A4/A14 — the three automatic change classes. Each has a registered reverser
# in `brain_undo.REVERSERS` and a phrase in `_CLASS_PHRASES`; `propose` refuses
# the auto tier without both, which is the mechanical half of the safety.
CLASS_VOICE = "voice_block_update"
CLASS_PREP = "prep_section_weight"
CLASS_EXEMPLAR = "exemplar_promotion"
AUTO_CLASSES = (CLASS_VOICE, CLASS_PREP, CLASS_EXEMPLAR)

# The proposal kinds these legs emit (never a customer-visible word).
KIND_VOICE = "voice_block_update"
KIND_PREP = "prep_section_weight"
KIND_EXEMPLAR = "exemplar_promotion"

# The generic proposal verbs the shipped auto rail uses. An auto proposal is
# applied and resolved in the same run; the affordance a person actually uses
# is `undo`, and these are the registered ids the validator accepts.
_ACTION_TUPLES = [{"action": "confirm proposal"},
                  {"action": "dismiss proposal"},
                  {"action": "snooze proposal 7d"}]

# The prep leg reads a 90-day window: a section that stopped being empty three
# months ago should not still be dragging a weight down.
PREP_WINDOW_DAYS = 90


class LearningJobError(RuntimeError):
    """Raised only by the CLI for an unusable workspace path."""


def _now_iso(now_iso: Optional[str] = None) -> str:
    if now_iso:
        return str(now_iso)
    return datetime.now(timezone.utc).isoformat()


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _mint_batch_id(now_iso: str) -> str:
    stamp = str(now_iso).replace("-", "").replace(":", "")[:15]
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(4)}"


def learning_enabled(workspace_root) -> bool:
    """The `learning.auto_apply` switch, through its one reader.

    Default ON and FAIL-TO-DEFAULT (the SPEC_FLOW1 posture): what it gates is
    an act with a receipt and an undo, so a malformed config file must not
    silently stop the product learning. Only a literal stored `False` — the
    customer having said `stop learning from my edits` — turns it off."""
    try:
        from commitment_policy import LEARNING_AUTO_APPLY_KEY, flow_switch_enabled
        return flow_switch_enabled(workspace_root, LEARNING_AUTO_APPLY_KEY)
    except Exception:  # pragma: no cover — unreadable switch reads as ON
        return True


# The event a reader's DELETION of a learned line writes. The profile page
# (PROFILE1) is the surface that offers the deletion; this job is the thing
# that must never re-derive what was deleted. The event is the seam between
# them, so neither lane imports the other's module.
DELETION_EVENT = "learned_line_deleted"


def deleted_learned_fingerprints(workspace_root) -> set:
    """Fingerprints of learned lines the reader has DELETED.

    Deleting a learned line off the profile page has to mean the line does
    not come back — otherwise the next fire re-derives it from the same
    unchanged corpus and writes it straight back, and the person's deletion
    was theatre. A deletion is therefore read here as permanent exclusion,
    which is the same semantics the applied set already carries.

    Accepts either shape on the event's `data`: a `fingerprint` outright, or
    the natural key a voice line is identified by on the page (`skill` +
    `op`), from which the fingerprint is re-derived. Never raises — an
    unreadable book excludes nothing, which is the pre-deletion behaviour."""
    out: set = set()
    try:
        from events_io import iter_events
    except Exception:  # pragma: no cover
        return out
    try:
        events = iter_events(Path(workspace_root) / "_hq" / "data")
    except Exception:
        return out
    try:
        from voice_corrections import voice_proposal_fingerprint
    except Exception:  # pragma: no cover
        voice_proposal_fingerprint = None  # type: ignore
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("type") != DELETION_EVENT:
            continue
        data = ev.get("data") or {}
        if not isinstance(data, dict):
            continue
        fp = data.get("fingerprint")
        if isinstance(fp, str) and fp.strip():
            out.add(fp.strip())
            continue
        skill, op = data.get("skill"), data.get("op")
        if skill and op and voice_proposal_fingerprint is not None:
            out.add(voice_proposal_fingerprint(str(skill), str(op)))
    return out


def _excluded_fingerprints(workspace_root, now_iso: str) -> set:
    """Cooldowns UNION applied UNION deleted. A4 — `active_cooldowns` excludes
    `applied` by design, which is right for a proposer whose own store answers
    "did this already happen". An automatic leg's store cannot answer that: a
    promoted exemplar overwrote the corrections it came from, so the same
    evidence would propose the same promotion on every fire forever.

    The third term is the PROFILE1 seam, and it lives here on purpose: all
    three legs already consult this one function, so a line the reader
    deleted is excluded everywhere in one place rather than three."""
    out: set = set()
    try:
        from proposal_ledger import active_cooldowns, applied_fingerprints
        out |= set(active_cooldowns(workspace_root, PASS_NAME, now_iso=now_iso))
        out |= set(applied_fingerprints(workspace_root, PASS_NAME))
    except Exception:  # pragma: no cover — an unreadable ledger proposes nothing
        return set()
    out |= deleted_learned_fingerprints(workspace_root)
    return out


def _propose_auto(workspace_root, *, kind: str, change_class: str,
                  fingerprint: str, evidence: str, render_line: str,
                  extra: Optional[dict] = None) -> Optional[dict]:
    """One auto proposal through the shared rail. Returns the propose result,
    or None when the rail declined it (cooldown / open duplicate)."""
    from brain_proposals import propose

    res = propose(
        workspace_root,
        kind=kind,
        fingerprint=fingerprint,
        evidence=evidence,
        action_tuples=list(_ACTION_TUPLES),
        tier="auto",
        change_class=change_class,
        detector=DETECTOR,
        render_line=render_line,
        extra=extra or None,
    )
    return res if res.get("status") == "proposed" else None


def _settle(workspace_root, proposal_id: str, fingerprint: str,
            summary: str) -> None:
    """Resolve the auto proposal applied and record the fingerprint as applied
    in the shared ledger — the idempotency half of A4."""
    from brain_proposals import resolve_proposal
    from proposal_ledger import append_decision

    resolve_proposal(workspace_root, proposal_id, "applied",
                     resolved_by=SOURCE_SKILL, source_skill=SOURCE_SKILL)
    append_decision(workspace_root, pass_name=PASS_NAME,
                    fingerprint=fingerprint, user_action="applied",
                    summary=summary)


# ---------------------------------------------------------------------------
# Leg 1 — voice
# ---------------------------------------------------------------------------

def _baked_voice_block(skill: str) -> str:
    """The plugin-side `## Voice Block` section of a skill's SKILL.md — what
    the learned delta merges INTO when the workspace has no override yet."""
    path = _HERE.parent.parent / "skills" / skill / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    marker = "\n## Voice Block"
    idx = text.find(marker)
    if idx < 0:
        return ""
    body = text[idx + 1:]
    nxt = body.find("\n## ", 1)
    return body[:nxt] if nxt > 0 else body


def _block_body(markdown) -> str:
    """The `## Voice Block` body of an override file, without the writer's
    header - what `apply_voice_delta` composes on and what the writer wraps."""
    text = markdown or ""
    at = text.find("## Voice Block")
    return text[at:] if at >= 0 else text


def run_voice_leg(workspace_root, *, apply: bool, now_iso: str,
                  batch_id: str) -> dict:
    """Repeated corrections per writing skill become a learned line in that
    skill's voice-block override."""
    import voice_corrections as vc

    rows = vc.load_corrections(workspace_root)
    groups = vc.group_op_patterns(rows)
    skills = sorted({skill for (skill, _op) in groups})
    existing = {}
    for skill in skills:
        override = vc.load_voice_block_override(workspace_root, skill)
        existing[skill] = (override or {}).get("markdown") or ""
    excluded = _excluded_fingerprints(workspace_root, now_iso)
    proposals = vc.propose_voice_updates(
        groups, existing_overrides=existing, cooldown_fingerprints=excluded)
    leg = {"corrections_read": len(rows), "groups": len(groups),
           "proposed": len(proposals), "applied": 0, "skipped": 0,
           "lines": [], "skills": []}
    if not apply:
        leg["lines"] = [p["plain"] for p in proposals]
        leg["skills"] = sorted({p["skill"] for p in proposals})
        return leg

    from event_gate import append_event
    events_path = _events_path(workspace_root)
    reviewed_through = {}
    for proposal in proposals:
        skill = proposal["skill"]
        had_override = bool(existing.get(skill))
        previous_markdown = existing.get(skill) or None
        # fix-round review (LC 3): the override file is header + block; the
        # writer wraps anything that does not start at the block, so basing
        # on the whole file nested a header per fire. Base on the block body.
        base = _block_body(previous_markdown) or _baked_voice_block(skill)
        merged = vc.apply_voice_delta(base, proposal, learned_on=now_iso)
        res = _propose_auto(
            workspace_root, kind=KIND_VOICE, change_class=CLASS_VOICE,
            fingerprint=proposal["fingerprint"],
            evidence=proposal["plain"], render_line=proposal["plain"],
            extra={"skill": skill, "op": proposal["op"]})
        if res is None:
            leg["skipped"] += 1
            continue
        vc.write_voice_block_override(
            workspace_root, skill, merged,
            calibration_level="calibrated",
            sample_count=proposal["evidence_rows"])
        # NIGHT 11a fix round (N-4, L-2): the next proposal for THIS skill in
        # this fire bases on the file just written, and snapshots it as its
        # `previous_markdown` - otherwise the second op silently overwrote
        # the first's bullet while both fingerprints settled as applied.
        _disk = vc.load_voice_block_override(workspace_root, skill) or {}
        existing[skill] = _disk.get("markdown") or merged
        append_event(events_path, {
            "type": "voice_block_updated",
            "source_skill": SOURCE_SKILL,
            "data": {
                "skill": skill,
                "op": proposal["op"],
                "section": proposal["section"],
                "delta": proposal["plain"],
                # A2 — the FULL prior file (or null when there was none) plus
                # the had_override flag. A path is not a snapshot; the undo
                # has to be able to put the bytes back without the file.
                "previous_markdown": previous_markdown,
                "had_override": had_override,
                "fingerprint": proposal["fingerprint"],
                "evidence_rows": proposal["evidence_rows"],
                "brain_batch_id": batch_id,
                "brain_change_class": CLASS_VOICE,
                "detector": DETECTOR,
            },
        }, holder=SOURCE_SKILL)
        _settle(workspace_root, res["proposal_id"], proposal["fingerprint"],
                proposal["plain"])
        reviewed_through[skill] = now_iso
        leg["applied"] += 1
        leg["lines"].append(proposal["plain"])
        leg["skills"].append(skill)
    if reviewed_through:
        # Staleness math unchanged: one review marker per fire, naming how far
        # each skill's corpus has been read.
        append_event(events_path, {
            "type": "voice_calibration_review",
            "source_skill": SOURCE_SKILL,
            "data": {"reviewed_through": reviewed_through,
                     "brain_batch_id": batch_id, "detector": DETECTOR},
        }, holder=SOURCE_SKILL)
    leg["skills"] = sorted(set(leg["skills"]))
    return leg


# ---------------------------------------------------------------------------
# Leg 2 — prep section weights
# ---------------------------------------------------------------------------

def run_prep_leg(workspace_root, *, apply: bool, now_iso: str,
                 batch_id: str) -> dict:
    """A prep section that keeps coming up empty for a meeting type gets a
    lower weight in call-prep's config."""
    import prep_grading as pg
    from skill_config_writer import load_skill_config

    since = None
    try:
        base = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
        since = (base - timedelta(days=PREP_WINDOW_DAYS)).isoformat()
    except Exception:  # pragma: no cover — an unparseable now reads all history
        since = None
    rows = pg.load_prep_feedback(workspace_root, since_iso=since)
    stats = pg.aggregate_section_stats_normalized(rows)
    stored = load_skill_config(workspace_root, "call-prep") or {}
    cfg = stored.get("config") if isinstance(stored, dict) else None
    existing = (cfg or {}).get("section_weights") or {}
    excluded = _excluded_fingerprints(workspace_root, now_iso)
    # ONE act on this rail: the shipped drop at the shipped floor. The
    # "render it shorter at half the evidence" tier was built and taken back
    # out before merge — see the note in `prep_grading`. On a book with no
    # section over the floor this leg correctly applies nothing, and the
    # brief says nothing, which is the honest outcome rather than a smaller
    # bar invented to produce one.
    proposals = pg.propose_section_weights(
        stats, existing_weights=existing, cooldown_fingerprints=excluded)
    leg = {"feedback_read": len(rows), "sections": len(stats),
           "proposed": len(proposals), "applied": 0, "skipped": 0,
           "lines": [], "meeting_types": []}
    if not apply:
        # Even the dry run's sentence goes through the one renderer, over the
        # change the write WOULD make. There is no second composer.
        leg["lines"] = [
            line for line in (
                pg.weight_change_line(
                    {"meeting_type": p["meeting_type"], "section": p["section"],
                     "from": pg.section_weight(cfg or {}, p["meeting_type"],
                                               p["section"]),
                     "to": p["weight"]},
                    rendered=p.get("rendered"), empty=p.get("empty"))
                for p in proposals) if line]
        leg["meeting_types"] = sorted({p["meeting_type"] for p in proposals})
        return leg

    from event_gate import append_event
    events_path = _events_path(workspace_root)
    for proposal in proposals:
        res = _propose_auto(
            workspace_root, kind=KIND_PREP, change_class=CLASS_PREP,
            fingerprint=proposal["fingerprint"],
            evidence=proposal["plain"], render_line=proposal["plain"],
            extra={"meeting_type": proposal["meeting_type"],
                   "section": proposal["section"]})
        if res is None:
            leg["skipped"] += 1
            continue
        written = pg.set_section_weights(workspace_root, [proposal])
        if not written["changes"]:
            leg["skipped"] += 1
            continue
        # THE SENTENCE IS DERIVED FROM THE WRITE, NOT FROM THE PROPOSAL. If
        # the write moved nothing the renderer returns "" and there is no
        # line — a surface must never announce an effect it did not produce.
        lines = [ln for ln in
                 (pg.weight_change_line(change,
                                        rendered=proposal.get("rendered"),
                                        empty=proposal.get("empty"))
                  for change in written["changes"]) if ln]
        delta = " ".join(lines)
        append_event(events_path, {
            "type": "prep_weights_updated",
            "source_skill": SOURCE_SKILL,
            "data": {
                # `changes` carries the from-value per row: the reverser puts
                # the config back from this and never re-derives it.
                "changes": written["changes"],
                # The job's own sentence rides the event, so `change_feed`
                # renders what the job decided rather than composing a second
                # sentence about the same act that can drift from it — and it
                # is the sentence derived from the WRITE, in the past tense,
                # not the proposer's question.
                "delta": delta,
                "fingerprint": proposal["fingerprint"],
                "brain_batch_id": batch_id,
                "brain_change_class": CLASS_PREP,
                "detector": DETECTOR,
            },
        }, holder=SOURCE_SKILL)
        _settle(workspace_root, res["proposal_id"], proposal["fingerprint"],
                delta or proposal["plain"])
        leg["applied"] += 1
        if delta:
            leg["lines"].append(delta)
        leg["meeting_types"].append(proposal["meeting_type"])
    leg["meeting_types"] = sorted(set(leg["meeting_types"]))
    return leg


# ---------------------------------------------------------------------------
# Leg 3 — exemplar promotion
# ---------------------------------------------------------------------------

def run_exemplar_leg(workspace_root, *, apply: bool, now_iso: str,
                     batch_id: str) -> dict:
    """Repeated structural corrections on one document kind promote the
    corrected document to that kind's workspace exemplar.

    A5 — the AUTOMATIC leg passes no `confirmed_residuals`, so any name-shaped
    token the workspace entity list cannot vouch for raises
    `ExemplarScrubError` and the promotion is REFUSED and counted. A refusal
    is a correct outcome here, not an error: a real name inside a gold
    standard is worse than a missed promotion, every time."""
    import exemplars as ex

    rows = ex.load_structural_corrections(workspace_root)
    excluded = _excluded_fingerprints(workspace_root, now_iso)
    proposals = ex.propose_exemplar_updates(
        rows, cooldown_fingerprints=excluded)
    leg = {"corrections_read": len(rows), "proposed": len(proposals),
           "applied": 0, "skipped": 0, "refused_scrub": 0,
           "no_candidate_text": 0, "lines": [], "kinds": []}
    by_key = ex.group_correction_patterns(rows)
    if not apply:
        # Even the dry run's sentence goes through the one renderer, over the
        # change the write WOULD make. `p["plain"]` is the proposer's
        # QUESTION and it must not reach a surface from here either.
        leg["lines"] = [
            line for line in (
                ex.promotion_change_line(
                    {"kind": p["kind"], "direction": p["direction"],
                     "section": p.get("section"), "count": p.get("count"),
                     "rotated": bool(
                         (ex.get_exemplar(p["kind"], workspace_root) or {})
                         .get("source") == "workspace")})
                for p in proposals) if line]
        leg["kinds"] = sorted({p["kind"] for p in proposals})
        return leg

    from event_gate import append_event
    events_path = _events_path(workspace_root)
    for proposal in proposals:
        key = (proposal["kind"], proposal["direction"],
               ex._norm(proposal.get("section") or ""))
        group = by_key.get(key) or []
        candidate = ""
        for row in reversed(group):
            text = (row.get("doc") or "").strip()
            if text:
                candidate = text
                break
        if not candidate:
            leg["no_candidate_text"] += 1
            continue
        current = ex.get_exemplar(proposal["kind"], workspace_root) or {}
        previous_text = (current.get("text")
                         if current.get("source") == "workspace" else None)
        res = _propose_auto(
            workspace_root, kind=KIND_EXEMPLAR, change_class=CLASS_EXEMPLAR,
            fingerprint=proposal["fingerprint"],
            evidence=proposal["plain"], render_line=proposal["plain"],
            extra={"exemplar_kind": proposal["kind"],
                   "direction": proposal["direction"]})
        if res is None:
            leg["skipped"] += 1
            continue
        try:
            written = ex.promote_workspace_exemplar(
                workspace_root, proposal["kind"], candidate,
                confirmed_residuals=None)
        except ex.ExemplarScrubError:
            leg["refused_scrub"] += 1
            from brain_proposals import resolve_proposal
            resolve_proposal(workspace_root, res["proposal_id"], "declined",
                             resolved_by=SOURCE_SKILL,
                             source_skill=SOURCE_SKILL,
                             note="refused by the exemplar scrub gate")
            continue
        # THE SENTENCE IS DERIVED FROM THE WRITE, NOT FROM THE PROPOSAL —
        # same rule as the prep rail. `proposal["plain"]` asks "make that the
        # standard layout?", which is the retired review widget's question;
        # putting it on the morning brief asks the customer to approve
        # something the job already did, and then offers an undo in the same
        # breath. `rotated` is the write's own fact: whether this replaced a
        # standard the workspace already had.
        delta = ex.promotion_change_line(
            {"kind": proposal["kind"], "direction": proposal["direction"],
             "section": proposal.get("section"),
             "count": proposal.get("count"),
             "rotated": bool((written or {}).get("rotated"))})
        append_event(events_path, {
            "type": "exemplar_promoted",
            "source_skill": SOURCE_SKILL,
            "data": {
                "kind": proposal["kind"],
                "direction": proposal["direction"],
                "previous_text": previous_text,
                "delta": delta,
                "fingerprint": proposal["fingerprint"],
                "brain_batch_id": batch_id,
                "brain_change_class": CLASS_EXEMPLAR,
                "detector": DETECTOR,
            },
        }, holder=SOURCE_SKILL)
        _settle(workspace_root, res["proposal_id"], proposal["fingerprint"],
                delta or proposal["plain"])
        leg["applied"] += 1
        if delta:
            leg["lines"].append(delta)
        leg["kinds"].append(proposal["kind"])
    leg["kinds"] = sorted(set(leg["kinds"]))
    return leg


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------

_LEGS = (("voice", run_voice_leg),
         ("prep", run_prep_leg),
         ("exemplar", run_exemplar_leg))


def run_learning_job(workspace_root, *, apply: bool = True,
                     now_iso: Optional[str] = None,
                     fired_via=None) -> dict:
    """The `learning` MAINTENANCE_JOBS entry point. Runs the three automatic
    legs in order, each wrapped: one leg raising never aborts the job, and its
    error text lands in the receipt rather than in nobody's hands.

    `apply=False` is a dry run — it proposes nothing, writes nothing, and
    deliberately writes NO receipt, so the job stays due (the
    `identity_reconcile --apply` posture the maintenance prompt depends on).

    Returns {ran, switch_on, batch_id, legs, errors, receipt}."""
    # FIX3 F3-6: a literal default IS an explicit value by the time the
    # resolver sees it (the FIX2 M-3 lesson), so this signature says
    # nothing and the seat answers. A legacy or local seat still reads
    # `scheduled`, byte for byte; a merged seat with nothing forwarded
    # reads `manual`, which is what a typed brief actually is.
    fired_via = _effective_fired_via(fired_via)
    now_iso = _now_iso(now_iso)
    batch_id = _mint_batch_id(now_iso)
    switch_on = learning_enabled(workspace_root)
    result = {"ran": True, "switch_on": switch_on, "batch_id": batch_id,
              "apply": bool(apply), "legs": {}, "errors": [],
              "lines": [], "receipt": None}
    if not switch_on:
        # The customer said stop. Nothing is proposed and nothing is written,
        # but the job still FIRED — a skip that leaves no trace reads exactly
        # like a job that never ran.
        result["ran"] = False
        if apply:
            result["receipt"] = _log(workspace_root, fired_via, result)
        return result

    for name, fn in _LEGS:
        try:
            result["legs"][name] = fn(workspace_root, apply=apply,
                                      now_iso=now_iso, batch_id=batch_id)
            result["lines"].extend(result["legs"][name].get("lines") or [])
        except Exception as exc:  # loud per-leg, contained per-job
            result["legs"][name] = {"error": f"{type(exc).__name__}: {exc}"}
            result["errors"].append({"leg": name,
                                     "error": f"{type(exc).__name__}: {exc}"})
    if apply:
        result["receipt"] = _log(workspace_root, fired_via, result)
    return result


def _log(workspace_root, fired_via: str, result: dict) -> dict:
    from receipts import log_receipt

    legs = result.get("legs") or {}
    applied = sum(int((legs.get(n) or {}).get("applied") or 0)
                  for n in ("voice", "prep", "exemplar"))
    return log_receipt(
        workspace_root, TASK_ID,
        receipt_type="pack_run",
        fired_via=fired_via,
        surfaced=applied,
        extra_data={
            "switch_on": result.get("switch_on"),
            "brain_batch_id": result.get("batch_id"),
            "n_applied": applied,
            "legs": {n: {k: v for k, v in (legs.get(n) or {}).items()
                         if k not in ("lines",)}
                     for n in ("voice", "prep", "exemplar")},
            "errors": result.get("errors") or [],
        },
    )


def validate_learning_ran(workspace_root) -> dict:
    """Enforcement binds to the receipt artifact, never to narration (the
    reconcile-sent doctrine)."""
    from receipts import iter_receipts

    latest = None
    for r in iter_receipts(workspace_root, task_ids=[TASK_ID]):
        latest = r
    if latest is None:
        return {"ok": False, "ran": False,
                "reason": "no learning receipt on file"}
    return {"ok": True, "ran": True, "receipt": latest}


def _narrate(result: dict) -> str:
    if not result.get("switch_on"):
        return "Learning from your edits is off for this workspace — nothing changed."
    lines = result.get("lines") or []
    head = ("Dry run — nothing written." if not result.get("apply")
            else f"Applied {len(lines)} change(s).")
    body = "\n".join("  - " + line for line in lines) or "  (nothing at the floors)"
    errs = result.get("errors") or []
    tail = ("\n" + "\n".join(f"  ! {e['leg']}: {e['error']}" for e in errs)) if errs else ""
    return f"{head}\n{body}{tail}"


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="perform the writes (default: dry-run, no receipt)")
    ap.add_argument("--triggered-by", default=None,
                    help="the surface that asked for this run")
    ap.add_argument("--fired-via", default=None,
                    choices=("scheduled", "manual", "catchup"))
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    # FIX3 F3-6: export what this run was asked by, so every composer
    # below reads it from one place instead of being threaded through
    # a dozen signatures.
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)
    ws = Path(args.workspace)
    if not ws.is_dir():
        raise LearningJobError(f"workspace not found: {args.workspace}")
    result = run_learning_job(ws, apply=args.apply, now_iso=args.now,
                              fired_via=args.fired_via)
    print(_narrate(result))
    if args.json:
        print(json.dumps(result, default=str))
    return 0


__all__ = [
    "TASK_ID", "PASS_NAME", "DETECTOR", "BATCH_PREFIX",
    "CLASS_VOICE", "CLASS_PREP", "CLASS_EXEMPLAR", "AUTO_CLASSES",
    "PREP_WINDOW_DAYS", "DELETION_EVENT", "learning_enabled",
    "deleted_learned_fingerprints", "run_voice_leg", "run_prep_leg",
    "run_exemplar_leg", "run_learning_job", "validate_learning_ran",
]


if __name__ == "__main__":
    raise SystemExit(main())
