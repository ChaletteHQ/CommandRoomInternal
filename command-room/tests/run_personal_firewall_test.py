#!/usr/bin/env python3
"""
SPEC PGUARD1 — personal firewall hardening acceptance suite (D1–D4).

Pins the whole firewall:

  1. D1 — events_io.load_events_org_scoped / iter_events_org_scoped: the
     org-scoped reader applies the account mask + personal-lane drop by
     design (a planted masked event and a personal reminder vanish from org
     reads; business events are untouched; the raw iter_events still sees
     everything — owner surfaces keep their view).
  2. D2 — personal_leak.scan_for_personal_leak flags personal fingerprints,
     and the three validators block SURFACE-GATED: org/board/client raise,
     m_facing / undeclared NEVER block (both directions tested).
  3. D3 — the reminders personal default is business-ref-driven on BOTH
     sides: "call Mom" (tracked person, no org/thread) defaults personal.
     (The full reminder-lane matrix lives in run_reminders_test.py.)
  4. D4(a) — the reminder reader's surface default stays "client_facing".
     D4(b) — grep guard: no org-facing skill/driver passes surface="m_facing"
     to the reminder reader (allowlist: show-my-reminders, morning-briefing).
     D4(c) — INVERTED structural guard: NO org/external-output skill or
     driver reads events.jsonl raw — every raw-read site must be on the
     explicit allowlist (inline maskers cru_match/dormancy/render_people_view,
     owner-facing readers, substrate infra). A planted NEW raw read site
     turns the guard red (proven on a synthetic tree).
  5. D4(e) — SPEC PGUARD2: the TYPE-SCOPED external composers. The nine
     roster composer skills read events.jsonl via the org-scoped reader
     (MIGRATED-style assertion), a WINDOWED prose fingerprint catches any
     new type-scoped read recipe on an unlisted skill (cross-line — a
     single-line regex provably missed memo-writer + follow-up-ritual),
     the commitment seam (`load_open_commitments(events=…)`) excludes
     personal-lane rows for composer callers while the no-arg owner form is
     unchanged, and the OUT4/OUT3B/BAL1 composer surfaces are pinned:
     infographic/charts modules can never learn a raw events read, the
     infographic leak gate declares the org surface (personal fingerprints
     BLOCK a forwardable page), and the Balance surface can never declare
     an org surface. Red paths planted for each new check.
"""
from __future__ import annotations

import inspect
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import personal_leak as PL  # noqa: E402
import reminders as R  # noqa: E402
from events_io import iter_events, iter_events_org_scoped, load_events_org_scoped  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def _write_events(ws: Path, events: list) -> Path:
    data = ws / "_hq" / "data"
    data.mkdir(parents=True, exist_ok=True)
    p = data / "events.jsonl"
    p.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    return p


# ---------------------------------------------------------------------------
# 1. D1 — the org-scoped reader (mask + personal-lane drop by design)
# ---------------------------------------------------------------------------
print("\n[1] D1 org-scoped reader")

# Historic (past) fixture dates only — G14.
EVS = [
    # business event on a normal account — must survive
    {"seq": 1, "ts": "2026-01-05T10:00:00Z", "type": "interaction",
     "source_skill": "inbox-triage",
     "data": {"summary": "Acme kickoff thread",
              "provenance": {"provider": "gmail", "native_id": "m1",
                             "account_id": "acct_business"}}},
    # business event on the account that gets masked below — must vanish
    {"seq": 2, "ts": "2026-01-06T10:00:00Z", "type": "interaction",
     "source_skill": "inbox-triage",
     "data": {"summary": "old mixed-account thread",
              "provenance": {"provider": "gmail", "native_id": "m2",
                             "account_id": "acct_reclassified"}}},
    # the mask event (business -> personal reclassification)
    {"seq": 3, "ts": "2026-01-07T10:00:00Z", "type": "account_scope_masked",
     "source_skill": "workspace-manager",
     "data": {"masked_account_id": "acct_reclassified",
              "address": "personal@example.com"}},
    # a personal reminder (explicit flag) — must vanish from org reads
    {"seq": 4, "ts": "2026-01-08T10:00:00Z", "type": "reminder",
     "source_skill": "show-my-reminders",
     "data": {"id": "rem_01HTESTPERSONAL01", "summary": "call Mom",
              "remind_from": "2026-01-09", "personal": True,
              "origin": "user_explicit"}},
    # a flag-less person-only reminder — D3 default => personal => vanish
    {"seq": 5, "ts": "2026-01-08T11:00:00Z", "type": "reminder",
     "source_skill": "show-my-reminders", "person_ids": ["person_042"],
     "data": {"id": "rem_01HTESTPERSONAL02", "summary": "dinner with Sam",
              "remind_from": "2026-01-10", "origin": "user_explicit"}},
    # a business commitment — must survive
    {"seq": 6, "ts": "2026-01-09T10:00:00Z", "type": "commitment",
     "source_skill": "meeting-notes", "primary_thread_id": "project_007",
     "data": {"title": "send the proposal", "status": "open",
              "origin": "user_stated"}},
    # BAL1 forward-compat personal-lane event — must vanish
    {"seq": 7, "ts": "2026-01-10T10:00:00Z", "type": "balance_nudge_suggested",
     "source_skill": "balance-guardian", "data": {"note": "family time"}},
]

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    _write_events(ws, EVS)

    org, skipped = load_events_org_scoped(ws)
    org_seqs = sorted(e.get("seq") for e in org)
    check("masked-account event dropped from org read", 2 not in org_seqs,
          f"seqs={org_seqs}")
    check("explicit personal reminder dropped from org read", 4 not in org_seqs)
    check("flag-less person-only reminder dropped (D3 default)", 5 not in org_seqs)
    check("balance_nudge_suggested dropped from org read", 7 not in org_seqs)
    check("business events + mask marker survive org read",
          {1, 6}.issubset(set(org_seqs)), f"seqs={org_seqs}")
    check("skipped channel preserved (clean fixture -> empty)", skipped == [])

    gen_seqs = sorted(e.get("seq") for e in iter_events_org_scoped(ws))
    check("iter_ generator form matches load_ form", gen_seqs == org_seqs)

    raw_seqs = sorted(e.get("seq") for e in iter_events(ws))
    check("raw iter_events (owner surfaces) still sees everything",
          raw_seqs == [1, 2, 3, 4, 5, 6, 7], f"seqs={raw_seqs}")

    # malformed-line tolerance: the defensive loader contract carries through
    p = ws / "_hq" / "data" / "events.jsonl"
    p.write_text(p.read_text(encoding="utf-8") + "not json\n",
                 encoding="utf-8")
    org2, skipped2 = load_events_org_scoped(ws)
    check("malformed line tolerated + surfaced via skipped",
          len(skipped2) == 1 and sorted(e.get("seq") for e in org2) == org_seqs)

# Review fix R-1 (2026-07-19): a since_ts-pruned load must take the FULL-
# HISTORY mask set. Two masked accounts straddling the shard boundary — the
# older mask lives only in a pruned shard while a second in-window mask makes
# the window computation non-empty — must both stay masked.
print("\n[1a] R-1 mask straddle under since_ts pruning")

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    data = ws / "_hq" / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "events-2025.jsonl").write_text(json.dumps(
        {"seq": 1, "ts": "2025-03-01T10:00:00Z", "type": "account_scope_masked",
         "data": {"masked_account_id": "acct_A"}}) + "\n", encoding="utf-8")
    (data / "events.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"seq": 2, "ts": "2026-01-05T10:00:00Z", "type": "interaction",
         "data": {"summary": "pruned-shard-masked acct row",
                  "provenance": {"provider": "gmail", "native_id": "x",
                                 "account_id": "acct_A"}}},
        {"seq": 3, "ts": "2026-01-06T10:00:00Z", "type": "account_scope_masked",
         "data": {"masked_account_id": "acct_B"}},
        {"seq": 4, "ts": "2026-01-07T10:00:00Z", "type": "interaction",
         "data": {"summary": "in-window-masked acct row",
                  "provenance": {"provider": "gmail", "native_id": "y",
                                 "account_id": "acct_B"}}},
    ]) + "\n", encoding="utf-8")
    pruned, _ = load_events_org_scoped(ws, since_ts="2026-01-01T00:00:00Z")
    seqs = sorted(e.get("seq") for e in pruned)
    check("straddled mask: BOTH masked accounts' rows dropped under since_ts",
          2 not in seqs and 4 not in seqs, f"seqs={seqs}")

# Review fix R-2 (2026-07-19): flag-less reminder_updated / reminder_cleared
# rows (an `edit` can carry a revised personal summary; only the reminder id
# is on the row, so they are unclassifiable) fail CLOSED — dropped from org
# reads. No org surface consumes lane-management rows.
print("\n[1c] R-2 reminder update/clear rows fail closed on org reads")

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    _write_events(ws, [
        {"seq": 1, "ts": "2026-01-07T10:00:00Z", "type": "reminder",
         "source_skill": "show-my-reminders",
         "data": {"id": "rem_01HTESTPERSONAL01", "summary": "call Mom",
                  "remind_from": "2026-01-09", "personal": True,
                  "origin": "user_explicit"}},
        {"seq": 2, "ts": "2026-01-08T10:00:00Z", "type": "reminder_updated",
         "source_skill": "show-my-reminders",
         "data": {"reminder_id": "rem_01HTESTPERSONAL01", "action": "edit",
                  "summary": "call Mom about the surgery",
                  "origin": "user_explicit"}},
        {"seq": 3, "ts": "2026-01-09T10:00:00Z", "type": "reminder_cleared",
         "source_skill": "show-my-reminders",
         "data": {"reminder_id": "rem_01HTESTPERSONAL01",
                  "note": "done, she is fine", "origin": "user_explicit"}},
        {"seq": 4, "ts": "2026-01-10T10:00:00Z", "type": "commitment",
         "source_skill": "meeting-notes", "primary_thread_id": "project_007",
         "data": {"title": "send the proposal", "status": "open",
                  "origin": "user_stated"}},
    ])
    org, _ = load_events_org_scoped(ws)
    seqs = sorted(e.get("seq") for e in org)
    check("reminder_updated / reminder_cleared rows dropped from org read",
          2 not in seqs and 3 not in seqs, f"seqs={seqs}")
    check("business row still survives alongside", 4 in seqs, f"seqs={seqs}")
    raw_seqs = sorted(e.get("seq") for e in iter_events(ws))
    check("owner raw read still sees the whole reminder lane",
          raw_seqs == [1, 2, 3, 4], f"seqs={raw_seqs}")

# Surface-level proof (acceptance #2): the CLIENT-FACING value receipt, built
# on the org-scoped reader, excludes a masked account's history from its
# numbers while identical unmasked history still counts.
print("\n[1b] D1 surface proof — value receipt")

from value_receipt import compute_value_receipt  # noqa: E402

_RCPT_EVS = [
    {"seq": 1, "ts": "2026-01-05T10:00:00Z", "type": "commitment",
     "source_skill": "meeting-notes",
     "data": {"id": "cmt_TESTCLEAN000000001", "title": "send the deck",
              "status": "open", "origin": "user_stated"}},
    {"seq": 2, "ts": "2026-01-06T10:00:00Z", "type": "commitment",
     "source_skill": "meeting-notes",
     "data": {"id": "cmt_TESTMASKED00000001", "title": "old personal-acct item",
              "status": "open", "origin": "user_stated",
              "provenance": {"provider": "gmail", "native_id": "m9",
                             "account_id": "acct_reclassified"}}},
    {"seq": 3, "ts": "2026-01-07T10:00:00Z", "type": "account_scope_masked",
     "source_skill": "workspace-manager",
     "data": {"masked_account_id": "acct_reclassified",
              "address": "personal@example.com"}},
]

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    _write_events(ws, _RCPT_EVS)
    receipt = compute_value_receipt(ws, "2026-01-01T00:00:00Z",
                               "2026-02-01T00:00:00Z")
    n = receipt["metrics"].get("commitments_captured")
    check("value receipt counts ONLY the unmasked commitment", n == 1,
          f"commitments_captured={n}")

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    _write_events(ws, [e for e in _RCPT_EVS if e["seq"] != 3])  # no mask
    receipt = compute_value_receipt(ws, "2026-01-01T00:00:00Z",
                               "2026-02-01T00:00:00Z")
    n = receipt["metrics"].get("commitments_captured")
    check("without the mask both commitments count (business unaffected)",
          n == 2, f"commitments_captured={n}")

# ---------------------------------------------------------------------------
# 2. D2 — scanner + surface-gated blocking (both directions)
# ---------------------------------------------------------------------------
print("\n[2] D2 personal-content scan, surface-gated")

ORG_TEXT = ("Board pack appendix — open items: rem_01HTESTPERSONAL01 "
            "(personal: true) call Mom")
CLEAN_TEXT = "Board pack appendix — Acme renewal moved to contract review."

findings = PL.scan_for_personal_leak(ORG_TEXT)
check("scan flags a planted personal row", len(findings) >= 2,
      f"findings={[f['name'] for f in findings]}")
check("scan is clean on business text",
      PL.scan_for_personal_leak(CLEAN_TEXT) == [])

check("is_org_surface: board/client/org true, m_facing/None/unknown false",
      PL.is_org_surface("board") and PL.is_org_surface("client")
      and PL.is_org_surface("org") and PL.is_org_surface("advisor-export")
      and not PL.is_org_surface("m_facing") and not PL.is_org_surface(None)
      and not PL.is_org_surface("staff-meeting"))

from chat_output_renderer import (LeakDetectedError, validate_chat_output,  # noqa: E402
                                  validate_rendered_widget)

blocked = False
try:
    validate_chat_output(ORG_TEXT, surface="board")
except LeakDetectedError:
    blocked = True
check("validate_chat_output BLOCKS personal content on surface='board'", blocked)

for surf in (None, "m_facing"):
    ok = True
    try:
        validate_chat_output(ORG_TEXT, surface=surf)
    except LeakDetectedError:
        ok = False
    check(f"validate_chat_output never blocks personal on surface={surf!r}", ok)

widget_html = f"<div class='cr-card'><div class='cr-body'>{ORG_TEXT}</div></div>"
blocked = False
try:
    validate_rendered_widget(widget_html, surface="client")
except LeakDetectedError:
    blocked = True
check("validate_rendered_widget BLOCKS personal on surface='client'", blocked)

ok = True
try:
    validate_rendered_widget(widget_html)          # undeclared surface
    validate_rendered_widget(widget_html, surface="m_facing")
except LeakDetectedError:
    ok = False
check("validate_rendered_widget never blocks m_facing / undeclared", ok)

from docx_leak_scanner import scan_text_for_leaks  # noqa: E402

org_findings = scan_text_for_leaks(ORG_TEXT, surface="board")
check("docx-scanner text scan includes personal findings on org surface",
      any(f["name"].startswith("personal_") for f in org_findings))
owner_findings = scan_text_for_leaks(ORG_TEXT)
check("docx-scanner text scan has NO personal findings without a surface",
      not any(f["name"].startswith("personal_") for f in owner_findings))

check("personal_leak reminder-family list mirrors reminders.REMINDER_TYPES",
      tuple(PL._REMINDER_TYPES) == tuple(R.REMINDER_TYPES))

# ---------------------------------------------------------------------------
# 3. D3 — "call Mom" defaults personal on both sides (acceptance #4)
# ---------------------------------------------------------------------------
print("\n[3] D3 personal default hardening")

mom = R.build_reminder_event("call Mom", remind_from="2026-01-09",
                             person_ids=["person_042"])
check("write side: person-only reminder defaults personal=true",
      mom["data"]["personal"] is True)
biz = R.build_reminder_event("chase Acme invoice", remind_from="2026-01-09",
                             primary_thread_id="project_007")
check("write side: thread-ref reminder defaults personal=false",
      biz["data"]["personal"] is False)

legacy = dict(mom, ts="2026-01-08T10:00:00Z")
legacy["data"] = {k: v for k, v in mom["data"].items() if k != "personal"}
rows_cf = R.active_reminders([legacy], "2026-01-09")
rows_m = R.active_reminders([legacy], "2026-01-09", surface="m_facing")
check("read side: flag-less person-only row is personal "
      "(client_facing empty, m_facing sees it)",
      rows_cf == [] and len(rows_m) == 1)

# ---------------------------------------------------------------------------
# 4. D4(a) — reminder reader surface default stays client_facing
# ---------------------------------------------------------------------------
print("\n[4] D4a reminder-reader default surface")

for fn in (R.active_reminders, R.load_active_reminders):
    default = inspect.signature(fn).parameters["surface"].default
    check(f"{fn.__name__} surface default is 'client_facing'",
          default == "client_facing", f"got {default!r}")

# ---------------------------------------------------------------------------
# 5. D4(b) — grep guard: who passes surface="m_facing" to the reminder reader
# ---------------------------------------------------------------------------
print("\n[5] D4b m_facing reminder-reader callers")

# A file that references the reminder reader AND passes m_facing must be on
# this allowlist (owner-facing surfaces + the reader's own module).
M_FACING_ALLOW = {
    "skills/show-my-reminders/SKILL.md",
    "skills/morning-briefing/SKILL.md",
    "shared/scripts/reminders.py",          # docstrings define the semantics
    "shared/scripts/balance.py",            # BAL1 — the owner-only Balance surface reads personal reminders by design
    "shared/EVENT_TYPES.md",                # lane documentation, not a caller
}
_READER_RE = re.compile(r"\b(?:active_reminders|load_active_reminders)\b")
_MFACING_RE = re.compile(r"""surface\s*=\s*["']m_facing["']""")

violations = []
for base in (ROOT / "skills", ROOT / "shared"):
    for f in base.rglob("*"):
        if f.suffix not in (".py", ".md") or not f.is_file():
            continue
        rel = f.relative_to(ROOT).as_posix()
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _READER_RE.search(text) and _MFACING_RE.search(text):
            if rel not in M_FACING_ALLOW:
                violations.append(rel)
check("no org-facing file passes surface='m_facing' to the reminder reader",
      violations == [], f"violations={violations}")

# ---------------------------------------------------------------------------
# 6. D4(c) — INVERTED structural guard: no unlisted raw events.jsonl reads
# ---------------------------------------------------------------------------
print("\n[6] D4c inverted raw-read structural guard")

# Raw-read markers. `iter_events_org_scoped(` does NOT match the raw
# `iter_events(` marker (different token). File-granularity guard: a file on
# the allowlist may contain raw reads for its stated reason; any file NOT
# listed that matches a marker is a NEW raw read site -> RED.
_RAW_MARKERS = [
    re.compile(r"\bload_events_defensively\b"),
    re.compile(r"\bevent_refs\.load_events\b|from\s+event_refs\s+import\s+load_events\b"),
    re.compile(r"\biter_events\("),
    re.compile(r"\bload_all\("),
]


def _raw_read_files(root: Path) -> list[str]:
    """Every skills/ + shared/ file matching a raw events-read marker, plus
    files with an open()/read_text() within 2 lines of an events.jsonl
    mention (the inline-parse shape)."""
    hits = set()
    for base in (root / "skills", root / "shared"):
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if f.suffix not in (".py", ".md") or not f.is_file():
                continue
            rel = f.relative_to(root).as_posix()
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(m.search(text) for m in _RAW_MARKERS):
                hits.add(rel)
                continue
            lines = text.splitlines()
            for i, ln in enumerate(lines):
                if "events.jsonl" not in ln:
                    continue
                window = lines[max(0, i - 2): i + 3]
                if any(_reads_in_line(w) for w in window):
                    hits.add(rel)
                    break
    return sorted(hits)


_WRITE_OPEN_RE = re.compile(r"""\bopen\([^)]*["'][wax]b?["']""")


def _reads_in_line(w: str) -> bool:
    """A READ-shaped file access: read_text(), or open() in a non-write mode.
    Write/append opens are the atomic-write FORBIDDEN examples quoted in
    skill prose ("never open(path,'a')") — not read sites."""
    if "read_text(" in w:
        return True
    for m in re.finditer(r"\bopen\(", w):
        tail = w[m.start():]
        if not _WRITE_OPEN_RE.match(tail):
            return True
    return False


# The D4(c) allowlist — every entry carries its category. ADDING A FILE HERE
# NEEDS A REASON: an org/external-output surface NEVER qualifies — it must
# read via events_io.load_events_org_scoped / iter_events_org_scoped instead.
RAW_READ_ALLOW = {
    # -- the reader itself + canonical loaders ------------------------------
    "shared/scripts/events_io.py",           # THE shard reader (org + raw forms)
    "shared/scripts/cru_match.py",           # canonical loader; inline masker (D4c)
    "shared/scripts/event_refs.py",          # shared defensive loader (library)
    # -- inline maskers (the D4c allowlist proper) --------------------------
    "shared/scripts/dormancy.py",            # inline filter_masked_events
    "shared/scripts/render_people_view.py",  # inline filter_masked_events
    # -- owner-facing (m_facing) surfaces & their drivers -------------------
    "skills/morning-briefing/SKILL.md",
    "skills/show-my-list/SKILL.md",
    "shared/scripts/reminders.py",           # own surface gate (default-deny)
    "shared/scripts/surface_drivers.py",     # brief_state ts probe (brief path)
    "shared/scripts/render_org_history.py",
    "shared/scripts/render_person_history.py",
    "shared/scripts/render_master_tracker.py",  # LB2 — owner workspace view (M-facing tracker); shard-transparent via events_io.load_all
    "skills/list-active/render_tree.py",        # LB2 — owner list-active tree; shard-transparent via events_io.load_all
    "shared/scripts/render_thread_live_state.py",
    "shared/scripts/relationship_moves.py",  # owner-facing outreach queue
    "shared/scripts/balance.py",             # BAL1 — owner-only (m_facing) personal surface; its output is personal-lane and dropped by the org reader
    "shared/scripts/voice_corrections.py",   # owner voice learning loop
    "shared/scripts/email_outcomes.py",      # owner voice learning loop
    "shared/scripts/change_feed.py",         # owner CHANGED window
    "shared/scripts/session_sweep.py",       # owner session hygiene
    "shared/scripts/thread_roster.py",
    "shared/scripts/thread_activity.py",
    # -- substrate infra: writers, projectors, repair, capture, telemetry ---
    "shared/scripts/account_scope_gate.py",  # mask computation reads the log
    "shared/scripts/brain_undo.py",
    "shared/scripts/capture_gate.py",
    "shared/scripts/chase_policy.py",
    "shared/scripts/commitment_activity.py",
    "shared/scripts/commitment_noise.py",
    "shared/scripts/commitment_state.py",
    "shared/scripts/confidence_calibration.py",
    "shared/scripts/config_drift_detector.py",  # LB2 — work-lane detector (reads config-override signals, proposes only; same category as deal_signal_detector)
    "shared/scripts/confirm_flow.py",
    "shared/scripts/deal_signal_detector.py",
    "shared/scripts/deal_state.py",
    "shared/scripts/extraction_hints.py",
    "shared/scripts/late_fire.py",
    "shared/scripts/identity_reconcile.py",  # PID1 reconciler: person-family proposals/annotations/receipts (work lane per D3) — same category as person_backlog_sweep
    "shared/scripts/meeting_capture.py",
    "shared/scripts/mute_ledger.py",
    "shared/scripts/person_backlog_sweep.py",
    "shared/scripts/prep_grading.py",
    "shared/scripts/prospect_conversion_detector.py",
    "shared/scripts/receipts.py",
    "shared/scripts/reconcile_sent_commitments.py",
    "shared/scripts/recover_corruption.py",
    "shared/scripts/repair_commitment_closures.py",
    "shared/scripts/schedule_proposals.py",
    "shared/scripts/sent_capture.py",
    "shared/scripts/slack_capture.py",
    "shared/scripts/source_event_seq_backfill.py",
    "shared/scripts/surface_preferences.py",
    "shared/scripts/task_watchdog.py",
    "shared/scripts/triage_feedback.py",
    # release detectors (fleet repair infra) — allow the whole family
    "shared/scripts/release_detectors/",
    # -- protocol / API documentation (instruction text, not read sites) ----
    "shared/ENTITY_RESOLVE_PROTOCOL.md",
    "shared/SUBAGENT_VERIFICATION.md",
    "shared/WORKSPACE_API.md",
}


def _unlisted(files: list[str]) -> list[str]:
    out = []
    for rel in files:
        if rel in RAW_READ_ALLOW:
            continue
        if any(rel.startswith(d) for d in RAW_READ_ALLOW if d.endswith("/")):
            continue
        out.append(rel)
    return out


offending = _unlisted(_raw_read_files(ROOT))
check("no unlisted raw events.jsonl read site in skills/ or shared/",
      offending == [], f"offending={offending}")

# The seven PGUARD1-migrated org surfaces must never regress to a raw read.
MIGRATED = [
    "skills/operator-report/SKILL.md",
    "skills/weekly-recap/SKILL.md",
    "skills/board-pack-assembler/SKILL.md",
    "skills/boardroom/SKILL.md",
    "skills/advisor-export/SKILL.md",
    "shared/scripts/value_receipt.py",
    "shared/scripts/brain_proposals.py",
]
raw_now = set(_raw_read_files(ROOT))
regressed = [f for f in MIGRATED if f in raw_now]
check("the seven migrated org surfaces contain no raw-read marker",
      regressed == [], f"regressed={regressed}")
for f in MIGRATED:
    text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
    check(f"{f} reads via the org-scoped reader",
          "load_events_org_scoped" in text or "iter_events_org_scoped" in text)

# RED path: a planted new raw read site on a synthetic tree must be caught.
with tempfile.TemporaryDirectory() as td:
    fake = Path(td)
    site = fake / "skills" / "fake-rollup" / "SKILL.md"
    site.parent.mkdir(parents=True)
    site.write_text(
        "Reads events via load_events_defensively(events_path) for the "
        "client rollup.\n", encoding="utf-8",
    )
    planted = _unlisted(_raw_read_files(fake))
    check("guard goes RED on a planted new raw read site",
          planted == ["skills/fake-rollup/SKILL.md"], f"got {planted}")

# ---------------------------------------------------------------------------
# 7. D4(e) — SPEC PGUARD2: external-composer read coverage
# ---------------------------------------------------------------------------
print("\n[7] D4e composer read coverage (PGUARD2)")

# The nine type-scoped external composers (SPEC PGUARD2 §2, re-grounded on
# 9b80b96). Every file here must read events.jsonl through the org-scoped
# reader and carry no raw marker — same contract as MIGRATED, plus these are
# prose recipes so the fingerprint sweep below exempts them by membership.
PGUARD2_ROSTER = [
    "skills/email-writer/SKILL.md",
    "skills/intro-broker/SKILL.md",
    "skills/contract-review/SKILL.md",
    "skills/memo-writer/SKILL.md",
    "skills/decision-memo-composer/SKILL.md",
    "skills/one-pager-composer/SKILL.md",
    "skills/follow-up-ritual/SKILL.md",
    "skills/thread-resurrection/SKILL.md",
    "skills/calendar-writer/SKILL.md",       # D-B: agenda lands in invite bodies
]

# (i) MIGRATED-style assertion over the roster.
roster_raw = [f for f in PGUARD2_ROSTER if f in raw_now]
check("PGUARD2 roster: no raw-read marker in any roster composer",
      roster_raw == [], f"raw={roster_raw}")
for f in PGUARD2_ROSTER:
    text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
    check(f"{f} reads via the org-scoped reader",
          "load_events_org_scoped" in text)

# (ii) WINDOWED prose fingerprint — the D4(e) sweep. A type-scoped read
# recipe is `events.jsonl` within ±3 lines of a `type ==` filter, OR a
# same-line "read/scan events.jsonl for …" recipe. Window, don't
# single-line: memo-writer and follow-up-ritual put the type filter on a
# DIFFERENT line than the path, and a single-line regex missed both
# (SPEC PGUARD2 §2 method gotcha). Windows already carrying the org-reader
# token are clean; append/write call sites are not reads.
_ORG_TOKEN_RE = re.compile(r"load_events_org_scoped|iter_events_org_scoped")
_TYPE_FILTER_RE = re.compile(r"`?\btype`?\s*==")
_READ_FOR_RE = re.compile(
    r"\b(read|scan)\b[^\n]{0,40}?`?_hq/data/events\.jsonl`?[^\n]{0,20}?\bfor\b",
    re.I)
_APPEND_CALL_RE = re.compile(r"\b(append_event|atomic_append_jsonl)\s*\(")


def _composer_read_hits(root: Path) -> dict:
    """skills/**/*.md files with a type-scoped / read-recipe events.jsonl
    window that is NOT org-scoped. Returns {rel_path: [line_numbers]}."""
    hits: dict = {}
    base = root / "skills"
    if not base.exists():
        return hits
    for f in base.rglob("*.md"):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines):
            if "events.jsonl" not in ln:
                continue
            window = lines[max(0, i - 3): i + 4]
            if any(_ORG_TOKEN_RE.search(w) for w in window):
                continue          # already reads through the org reader
            if _APPEND_CALL_RE.search(ln):
                continue          # a gated write call site, not a read
            if any(_TYPE_FILTER_RE.search(w) for w in window) \
                    or _READ_FOR_RE.search(ln):
                hits.setdefault(rel, []).append(i + 1)
    return hits


# Owner-facing prose allowlist (D-C ratified + grounded hits). Every entry
# carries its rationale — an org/external-output surface NEVER qualifies
# (same contract as RAW_READ_ALLOW).
OWNER_READ_ALLOW_PROSE = {
    "skills/call-prep/SKILL.md",             # D-C: m_facing prep brief; chained drafts route through migrated email-writer
    "skills/decision-revisit/SKILL.md",      # D-C: owner decision hygiene, no external artifact
    "skills/dormant-customer-scan/SKILL.md", # D-C: owner list; already masked via cru_match.event_references_person; drafts route through migrated composers
    "skills/morning-briefing/SKILL.md",      # owner brief (m_facing) — legitimately sees everything
    "skills/show-my-list/SKILL.md",          # owner commitments surface
    "skills/show-my-reminders/SKILL.md",     # owner reminder lane (own surface gate)
    "skills/workspace-manager/SKILL.md",     # owner tracker overlay / navigation
    "skills/team-intelligence/SKILL.md",     # owner "who owns X" lookup, renders to the CEO only
    "skills/log-resolution/SKILL.md",        # idempotency read before an owner WRITE (closure flow)
    "skills/level-up-command-room/SKILL.md", # owner install-state menu (artifact_installed reads)
    "skills/balance/SKILL.md",               # BAL1: owner-only personal surface — reads personal by design, renders m_facing only (pinned below)
}


def _unlisted_composer_hits(root: Path) -> dict:
    exempt = set(PGUARD2_ROSTER) | set(MIGRATED) | OWNER_READ_ALLOW_PROSE
    return {rel: lns for rel, lns in _composer_read_hits(root).items()
            if rel not in exempt}


offending = _unlisted_composer_hits(ROOT)
check("no unlisted type-scoped composer read recipe in skills/ prose",
      offending == {}, f"offending={offending}")

# (iii) RED path — a planted CROSS-LINE type-scoped read on a synthetic tree
# must be caught (the exact shape the single-line regex missed).
with tempfile.TemporaryDirectory() as td:
    fake = Path(td)
    site = fake / "skills" / "fake-pitch-writer" / "SKILL.md"
    site.parent.mkdir(parents=True)
    site.write_text(
        "Step 3 — history pull.\n"
        "Keep rows from `_hq/data/events.jsonl` where\n"
        "`type == \"interaction\"` names the recipient.\n",
        encoding="utf-8",
    )
    planted = _unlisted_composer_hits(fake)
    check("D4e guard goes RED on a planted cross-line type-scoped read",
          list(planted) == ["skills/fake-pitch-writer/SKILL.md"],
          f"got {planted}")

# ... and the false-positive controls hold on the same sweep: a gated WRITE
# near a type token, and an org-scoped read window, must both stay clean.
with tempfile.TemporaryDirectory() as td:
    fake = Path(td)
    site = fake / "skills" / "fake-writer" / "SKILL.md"
    site.parent.mkdir(parents=True)
    site.write_text(
        "Write the receipt:\n"
        "append_event(\"<ws>/_hq/data/events.jsonl\", {\"type\": \"x\"},\n"
        "             holder=\"fake\")\n",
        encoding="utf-8",
    )
    site2 = fake / "skills" / "fake-clean-reader" / "SKILL.md"
    site2.parent.mkdir(parents=True)
    site2.write_text(
        "Read via load_events_org_scoped (never a raw load) from\n"
        "`_hq/data/events.jsonl`, then filter\n"
        "`type == \"decision\"` on the topic.\n",
        encoding="utf-8",
    )
    check("D4e fingerprint ignores gated writes + org-scoped read windows",
          _unlisted_composer_hits(fake) == {},
          f"got {_unlisted_composer_hits(fake)}")

# (iv) OUT4/OUT3B composer pins — the chart/infographic composers must never
# learn a raw events read. infographic.py and charts.py take computed data
# only (their producers are org-scoped value_receipt / entity-state
# pipeline_math); chart-on-demand's only events.jsonl block is the gated
# chart_render write.
for mod in ("shared/scripts/infographic.py", "shared/scripts/charts.py"):
    text = (ROOT / mod).read_text(encoding="utf-8", errors="replace")
    check(f"{mod} never mentions events.jsonl (computed-data composer)",
          "events.jsonl" not in text)
    check(f"{mod} carries no raw-read marker", mod not in raw_now)
check("chart-on-demand SKILL carries no raw marker / read fingerprint",
      "skills/chart-on-demand/SKILL.md" not in raw_now
      and "skills/chart-on-demand/SKILL.md" not in _composer_read_hits(ROOT))

# The infographic leak gate declares the org surface (OUT4-review follow-up):
# structurally pinned so a refactor can't quietly drop the declaration.
_infog_text = (ROOT / "shared/scripts/infographic.py").read_text(
    encoding="utf-8", errors="replace")
check("infographic _leak_gate scans with surface=\"org\"",
      re.search(r"scan_text_for_leaks\(.*surface=\"org\"", _infog_text)
      is not None)

from infographic import build_infographic  # noqa: E402
from docx_leak_scanner import LeakScanError  # noqa: E402

_INFOG_CONTENT = {"hero": {"value": "38", "label": "hours returned"},
                  "support": [{"value": "12", "label": "commitments closed"}]}
try:
    html = build_infographic("stat_spotlight", _INFOG_CONTENT,
                             title="Quarterly receipt")
    check("infographic renders clean business content", "38" in html)
except Exception as e:  # noqa: BLE001
    check("infographic renders clean business content", False, repr(e))

blocked = False
try:
    build_infographic(
        "stat_spotlight",
        {"hero": {"value": "38", "label": "hours returned"},
         "support": [{"value": "rem_01HTESTPERSONAL01",
                      "label": "call Mom (personal: true)"}]},
        title="Quarterly receipt",
    )
except LeakScanError:
    blocked = True
except Exception:  # noqa: BLE001 — a shape refusal is not the leak block
    blocked = False
check("infographic BLOCKS a personal fingerprint (org surface declared)",
      blocked)

# (v) BAL1 pin — the Balance surface is owner-only BY DESIGN (it reads the
# personal lane; migrating it to the org reader would delete the feature).
# The read-gate treatment for this row is directional: Balance may never
# DECLARE an org/external surface, so its personal content can never be
# rendered through a blocking-exempt org path.
_ORG_SURFACE_DECL_RE = re.compile(
    r"""surface\s*=\s*["'](?:org|board|client|client-facing|external|"""
    r"""board-pack|board-pack-assembler|advisor-export|value-receipt)["']""")


def _balance_org_decls(root: Path) -> list[str]:
    out = []
    for rel in ("shared/scripts/balance.py", "skills/balance/SKILL.md"):
        p = root / rel
        if not p.exists():
            continue
        if _ORG_SURFACE_DECL_RE.search(
                p.read_text(encoding="utf-8", errors="replace")):
            out.append(rel)
    return out


check("balance surface never declares an org/external surface",
      _balance_org_decls(ROOT) == [], f"decls={_balance_org_decls(ROOT)}")
for rel in ("shared/scripts/balance.py", "skills/balance/SKILL.md"):
    check(f"{rel} pins the m_facing contract",
          "m_facing" in (ROOT / rel).read_text(encoding="utf-8",
                                               errors="replace"))

# RED path: a synthetic balance module that declares an org surface is caught.
with tempfile.TemporaryDirectory() as td:
    fake = Path(td)
    bp = fake / "shared" / "scripts" / "balance.py"
    bp.parent.mkdir(parents=True)
    bp.write_text('render(surface="client")  # m_facing\n', encoding="utf-8")
    check("balance pin goes RED on a planted org-surface declaration",
          _balance_org_decls(fake) == ["shared/scripts/balance.py"],
          f"got {_balance_org_decls(fake)}")

# ---------------------------------------------------------------------------
# 8. PGUARD2 fixture proofs — the D2 seam + composer-context exclusion
# ---------------------------------------------------------------------------
print("\n[8] PGUARD2 seam + composer-context fixture proofs")

from cru_match import load_open_commitments  # noqa: E402

# The seam is keyword-only with a None default — the no-arg owner form is
# byte-identical to pre-PGUARD2 behavior.
_p = inspect.signature(load_open_commitments).parameters["events"]
check("load_open_commitments events param is kw-only, default None",
      _p.default is None and _p.kind is inspect.Parameter.KEYWORD_ONLY)

# Historic (past) fixture dates only — G14.
_SEAM_EVS = [
    # business open commitment — must survive everywhere
    {"seq": 1, "ts": "2026-01-05T10:00:00Z", "type": "commitment",
     "source_skill": "meeting-notes", "primary_thread_id": "project_007",
     "data": {"id": "cmt_TESTBUSINESS000001", "title": "send the proposal",
              "status": "open", "origin": "user_stated"}},
    # personal-tie open commitment (BAL1 lane) — owner sees it, composers don't
    {"seq": 2, "ts": "2026-01-06T10:00:00Z", "type": "commitment",
     "source_skill": "meeting-notes", "tie": "personal",
     "data": {"id": "cmt_TESTPERSONAL00001", "title": "plan the date night",
              "status": "open", "origin": "user_stated"}},
    # masked-account open commitment + its mask — R5 drops it EVERYWHERE
    {"seq": 3, "ts": "2026-01-07T10:00:00Z", "type": "commitment",
     "source_skill": "meeting-notes",
     "data": {"id": "cmt_TESTMASKED0000001", "title": "old personal-acct item",
              "status": "open", "origin": "user_stated",
              "provenance": {"provider": "gmail", "native_id": "m9",
                             "account_id": "acct_reclassified"}}},
    {"seq": 4, "ts": "2026-01-08T10:00:00Z", "type": "account_scope_masked",
     "source_skill": "workspace-manager",
     "data": {"masked_account_id": "acct_reclassified",
              "address": "personal@example.com"}},
]

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    ep = _write_events(ws, _SEAM_EVS)

    owner = load_open_commitments(ep)
    owner_ids = sorted(c["data"]["id"] for c in owner)
    check("no-arg (owner) form still projects the personal-tie commitment",
          "cmt_TESTPERSONAL00001" in owner_ids, f"ids={owner_ids}")
    check("no-arg form already drops the masked-account commitment (R5 — "
          "pre-existing, both forms)",
          "cmt_TESTMASKED0000001" not in owner_ids, f"ids={owner_ids}")

    org_events, _sk = load_events_org_scoped(ws)
    composer = load_open_commitments(ep, events=org_events)
    composer_ids = sorted(c["data"]["id"] for c in composer)
    check("seam (events=org-scoped) excludes the personal-tie commitment",
          "cmt_TESTPERSONAL00001" not in composer_ids, f"ids={composer_ids}")
    check("seam excludes the masked-account commitment",
          "cmt_TESTMASKED0000001" not in composer_ids, f"ids={composer_ids}")
    check("seam still projects the business commitment",
          composer_ids == ["cmt_TESTBUSINESS000001"], f"ids={composer_ids}")

    # Composer-context proof #1 (email-writer-shaped): the D1 recipe — one
    # org-scoped load, type-filtered at the call site — never sees the
    # personal/masked rows an owner read sees.
    drafts_ctx = [e for e in org_events if e.get("type") == "commitment"]
    titles = " | ".join((e.get("data") or {}).get("title", "")
                        for e in drafts_ctx)
    check("email-writer-shaped context: planted personal + masked titles "
          "absent, business title present",
          "date night" not in titles and "personal-acct" not in titles
          and "send the proposal" in titles, f"titles={titles!r}")

# Composer-context proof #2 (doc-composer-shaped): decisions + interactions.
_DOC_EVS = [
    {"seq": 1, "ts": "2026-01-05T10:00:00Z", "type": "decision",
     "source_skill": "decision-log", "primary_thread_id": "project_007",
     "data": {"summary": "we will not accept uncapped indemnification",
              "origin": "user_stated"}},
    {"seq": 2, "ts": "2026-01-06T10:00:00Z", "type": "interaction",
     "source_skill": "inbox-triage",
     "data": {"summary": "masked-acct pricing thread",
              "provenance": {"provider": "gmail", "native_id": "x1",
                             "account_id": "acct_reclassified"}}},
    {"seq": 3, "ts": "2026-01-07T10:00:00Z", "type": "account_scope_masked",
     "source_skill": "workspace-manager",
     "data": {"masked_account_id": "acct_reclassified",
              "address": "personal@example.com"}},
    {"seq": 4, "ts": "2026-01-08T10:00:00Z", "type": "interaction",
     "source_skill": "inbox-triage",
     "data": {"summary": "clean-acct pricing thread",
              "provenance": {"provider": "gmail", "native_id": "x2",
                             "account_id": "acct_business"}}},
]

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    _write_events(ws, _DOC_EVS)
    org_events, _sk = load_events_org_scoped(ws)
    ctx = " | ".join((e.get("data") or {}).get("summary", "")
                     for e in org_events
                     if e.get("type") in ("decision", "interaction"))
    check("doc-composer-shaped context: masked interaction absent, decision "
          "+ clean interaction present",
          "masked-acct" not in ctx and "uncapped indemnification" in ctx
          and "clean-acct" in ctx, f"ctx={ctx!r}")
    # Both directions: without the mask event the same rows all flow.
    _write_events(ws, [e for e in _DOC_EVS if e["seq"] != 3])
    org2, _sk = load_events_org_scoped(ws)
    ctx2 = " | ".join((e.get("data") or {}).get("summary", "")
                      for e in org2)
    check("without the mask the same account's rows flow to composers "
          "(business unaffected)", "masked-acct" in ctx2, f"ctx={ctx2!r}")

# Balance fingerprints on a widget: blocked on org surfaces, never m_facing.
_BAL_WIDGET = ("<div class='cr-card'><div class='cr-body'>Sunday balance — "
               "tie: personal (Sam Sample) rem_01HTESTPERSONAL01"
               "</div></div>")
blocked = False
try:
    validate_rendered_widget(_BAL_WIDGET, surface="client")
except LeakDetectedError:
    blocked = True
check("balance-shaped widget content BLOCKS on an org surface", blocked)
ok = True
try:
    validate_rendered_widget(_BAL_WIDGET, surface="m_facing")
except LeakDetectedError:
    ok = False
check("balance-shaped widget content renders m_facing (owner surface)", ok)

# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}\nPASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
