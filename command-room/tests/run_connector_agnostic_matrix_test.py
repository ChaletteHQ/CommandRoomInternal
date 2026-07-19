#!/usr/bin/env python3
"""
Connector-agnostic per-provider verification matrix (§6 gates 1–7).

Runs on every future release. Encodes the handoff §6 gate checklist as
assertions against real-shaped fixtures (the actual Step-0 UUID servers + op
names). Gates 8 (battery green) and 9 (honest-caveat list) live in run_all.py +
the build report respectively.

Gate 1 (grep-clean) is a HARD gate as of the 2026-07-12 fix pass: the
PENDING_DEHARDCODE set is empty and ANY skill .md naming a provider
tool/operator/URL fails the build. Gate 5 is asserted at the REAL call sites
(the reconcile self-closure guard, sent_capture.already_captured, and the
email_sent dual-write builder), not just the normalizer.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import tool_discovery as td  # noqa: E402
from connector_adapters import capabilities as cap, mail  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


# ---------------------------------------------------------------------------
# Provider fixtures — REAL Step-0 tool-id shapes (realdata-fixture gotcha).
# ---------------------------------------------------------------------------
def T(*ids):
    return [td.ToolDescriptor(i) for i in ids]


FIXTURES = {
    "gmail": T("mcp__f12657a1__search_threads", "mcp__f12657a1__create_draft",
               "mcp__f12657a1__get_thread", "mcp__f12657a1__apply_sensitive_message_label"),
    "superhuman": T("mcp__ec5e0bd5__create_or_update_draft", "mcp__ec5e0bd5__send_draft",
                    "mcp__ec5e0bd5__list_splits", "mcp__ec5e0bd5__get_read_status_feed",
                    "mcp__ec5e0bd5__undo_send", "mcp__ec5e0bd5__query_email_and_calendar"),
    "outlook": T("mcp__c700769d__outlook_email_search", "mcp__c700769d__get_me",
                 "mcp__c700769d__sharepoint_search"),  # read-only — no draft/send
    "google_calendar": T("mcp__f9119bb5__list_calendars", "mcp__f9119bb5__create_event",
                         "mcp__f9119bb5__respond_to_event", "mcp__f9119bb5__suggest_time"),
    "zapier_lookalike": T("mcp__4658de3a__gmail_send_email", "mcp__4658de3a__gmail_reply_to_email",
                          "mcp__4658de3a__get_configuration_url"),
    "empty": [],
}


# ---- Gate 2: per-provider fixture matrix green -------------------------------

def test_gate2_matrix():
    # Gmail declared -> draft resolves on the Gmail server.
    r = td.discover_for_category("email", "create_draft", FIXTURES["gmail"],
                                 declared={"server_id": "f12657a1", "provider": "gmail"})
    check("gate2 gmail: draft resolves on declared server", r.tool_id == "mcp__f12657a1__create_draft")

    # Superhuman declared -> send resolves natively (no Zapier).
    r = td.discover_for_category("email", "send_draft", FIXTURES["superhuman"],
                                 declared={"server_id": "ec5e0bd5", "provider": "superhuman"})
    check("gate2 superhuman: native send resolves", r.tool_id == "mcp__ec5e0bd5__send_draft")

    # Outlook read-only -> declared but no draft tool -> capability-absent (degrade).
    r = td.discover_for_category("email", "create_draft", FIXTURES["outlook"],
                                 declared={"server_id": "c700769d", "provider": "outlook"})
    check("gate2 outlook(read-only): no draft tool -> degrade path", r.tool_id is None and "no" in r.reason.lower())

    # Google Calendar declared -> event create resolves.
    r = td.discover_for_category("calendar", "create_event", FIXTURES["google_calendar"],
                                 declared={"server_id": "f9119bb5", "provider": "google_calendar"})
    check("gate2 gcal: create_event resolves", r.tool_id == "mcp__f9119bb5__create_event")

    # Empty map -> no declared backend -> None + fallback reason (today's behavior).
    r = td.discover_for_category("email", "create_draft", FIXTURES["empty"], declared=None)
    check("gate2 empty-map: falls back to today's behavior", r.tool_id is None and "no declared" in r.reason)

    # Zapier-lookalike -> the UUID gmail_* leg resolves as Zapier, NEVER native.
    zap = td.zapier_servers(FIXTURES["zapier_lookalike"])
    send = td.discover_mail_send_tool(FIXTURES["zapier_lookalike"])
    check("gate2 zapier-lookalike: server detected as Zapier", "4658de3a" in zap)
    # Assert the DEGRADE outcome (None + reason), not a trivially-true `!=`.
    check("gate2 zapier-lookalike: native send degrades with no-native-tool reason",
          send.tool_id is None and "no native mail tool" in (send.reason or "").lower())
    # …while the Zapier leg stays findable through its own dedicated helper.
    zsend = td.discover_zapier_send_tool(FIXTURES["zapier_lookalike"], zapier_ids=["4658de3a"])
    check("gate2 zapier-lookalike: Zapier send still findable via its own helper",
          zsend.tool_id == "mcp__4658de3a__gmail_send_email")


# ---- Gate 3: draft lands in the DECLARED connector's Drafts -------------------

def test_gate3_declared_drafts():
    # A mixed env (Superhuman + Gmail + Zapier) with Superhuman declared: the
    # draft op resolves on Superhuman, not Gmail, not Zapier.
    mixed = FIXTURES["superhuman"] + FIXTURES["gmail"] + FIXTURES["zapier_lookalike"]
    r = td.discover_for_category("email", "create_or_update_draft", mixed,
                                 declared={"server_id": "ec5e0bd5", "provider": "superhuman"})
    check("gate3: draft lands on the DECLARED connector (superhuman) in a mixed env",
          r.tool_id == "mcp__ec5e0bd5__create_or_update_draft")


# ---- Gate 4: writer-side wall (delegated to the dedicated suite) --------------

def test_gate4_writer_wall_present():
    import account_scope_gate as asg
    check("gate4: writer-side wall module present + raises AccountScopeError",
          hasattr(asg, "enforce_scope") and issubclass(asg.AccountScopeError, Exception))
    # Full end-to-end proof (personal cannot enter events.jsonl) is in
    # run_account_scope_gate_test.py; this asserts the wall is wired.


# ---- Gate 5: dedup identity across old/new formats ---------------------------

def test_gate5_dedup_identity():
    from connector_adapters.provenance import canonical_dedup_key
    # Keys normalize (lowercase, consistent with source_ref_index._norm_source_ref).
    legacy = canonical_dedup_key("gmail:msgABC")
    structured = canonical_dedup_key(event={"data": {"provenance": {"provider": "gmail", "native_id": "msgABC"}}})
    sent_channel = canonical_dedup_key(event={"type": "email_sent", "data": {"gmail_message_id": "msgABC"}})
    check("gate5: legacy gmail:<id> and structured re-observation reduce to one key",
          legacy == structured == sent_channel == "gmail:msgabc")
    # THE REAL CALL SITES (2026-07-12 fix pass — gate 5 was previously scored
    # off the normalizer alone, the wrong layer):
    # (a) the reconcile-sent self-closure guard excludes across formats — a
    #     commitment opened with a case-drifted legacy ref is never closed by
    #     its own message re-observed.
    import reconcile_sent_commitments as rsc
    c = {"seq": 1, "type": "commitment", "person_ids": ["person_bob"],
         "data": {"id": "c1", "owner_id": "person_u", "status": "open",
                  "title": "Send Bob the Q2 financial review deck",
                  "source_ref": "gmail:MsgCASE"}}
    r = rsc.reconcile_sent([c], [{
        "message_id": "msgcase", "ts": "2026-06-01T09:00:00",
        "recipient_person_ids": ["person_bob"],
        "subject": "Q2 financial review deck",
        "body": "Bob, sending the Q2 financial review deck I owed you.",
    }], user_person_id="person_u")
    check("gate5 call-site: self-closure guard holds across formats (reconcile_sent)",
          r["auto_close"] == [] and r["pending"] == [])
    # (b) sent_capture.already_captured keys on the canonical identity — a
    #     structured-provenance row on disk dedups a legacy-form re-scan.
    import json as _json, tempfile as _tf
    from pathlib import Path as _P
    ws = _P(_tf.mkdtemp(prefix="gate5_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text(_json.dumps({
        "seq": 1, "type": "commitment",
        "data": {"id": "c9", "status": "open", "title": "send the deck",
                 "provenance": {"provider": "gmail", "native_id": "MsgCASE"}},
    }) + "\n", encoding="utf-8")
    import sent_capture as sc
    check("gate5 call-site: already_captured dedups legacy vs structured forms",
          sc.already_captured(str(ws), "msgcase", "send the deck"))
    check("gate5 call-site: a different message is NOT falsely deduped",
          not sc.already_captured(str(ws), "otherid", "send the deck"))
    # (c) the email_sent dual-write builder emits BOTH channels with one identity.
    from connector_adapters.provenance import build_email_sent_provenance
    frag = build_email_sent_provenance(message_id="MsgCASE", thread_id="Th1",
                                       provider="gmail", server_id="f12657a1")
    check("gate5: dual-write keeps the legacy id fields (reader back-compat)",
          frag.get("gmail_message_id") == "MsgCASE" and frag.get("gmail_thread_id") == "Th1")
    check("gate5: dual-write adds structured provenance reducing to the same key",
          canonical_dedup_key(event={"data": frag}) == canonical_dedup_key("gmail:MsgCASE"))


# ---- Gate 6: read-only connector degrades to paste ---------------------------

def test_gate6_read_only_degrades():
    check("gate6: outlook read-only cannot draft (manifest)", cap.supports("outlook", "draft") is False)
    check("gate6: outlook read-only cannot send (manifest)", cap.supports("outlook", "send") is False)
    # The dispatch contract (EMAIL_DRAFT_PROTOCOL §0.5 pt2) turns "cannot write"
    # into paste-text, never a hard fail. We assert the capability signal the
    # skills branch on.
    check("gate6: superhuman CAN send (so it does not degrade)", cap.supports("superhuman", "send") is True)


# ---- Gate 7: empty account map = zero behavior change ------------------------

def test_gate7_empty_map_zero_change():
    import connector_config as cc
    check("gate7: empty map -> in write scope (no wall)", cc.is_in_write_scope(address="a@example.com", entities={}))
    check("gate7: empty map -> account map not populated", not cc.account_map_populated(entities={}))
    # (End-to-end zero-change through the real append path is asserted in
    # run_account_scope_gate_test.test_e2e_empty_map_zero_change.)


# ---- Gate 1: grep-clean (NON-REGRESSION; honestly reports NOT-YET-MET) --------

# Files whose BODIES still name provider tools/operators/URLs. EMPTY as of the
# 2026-07-12 fix pass — the final four (email-writer, orchestrator-commitments,
# orchestrator-inbox, reconcile-sent) were de-hardcoded with the canonical-key
# call-site wiring: the email_sent identity fields now come from
# connector_adapters.provenance.build_email_sent_provenance (dual-write:
# legacy ids for reader back-compat + structured provenance), the reconcile
# self-closure guard + sent_capture.already_captured compare CANONICAL keys,
# and the message-id-header lookup is the `message_id_lookup` intent. Gate 1
# is now a hard grep-clean assertion: ANY skill .md naming a provider token
# fails the build.
PENDING_DEHARDCODE = set()

_PROVIDER_TOKENS = re.compile(
    r"\bcreate_draft\b|\bsend_draft\b|\bsearch_threads\b|\bcreate_label\b|gcal_"
    r"|gmail_thread_id|gmail_message_id|mail\.google\.com|outlook\.office\.com"
    r"|calendar\.google\.com|\bis:unread\b|\bin:inbox\b|\bin:sent\b|\bfrom:me\b"
    r"|newer_than:|rfc822msgid|responseStatus|htmlLink|\btimeMin\b|\btimeMax\b"
    # 2026-07-12 closeout — extended set: calendar + Zapier namespaces, the
    # remaining Gmail operators (colon-value form so English prose "not
    # after: ..." doesn't false-positive), and provider field shapes.
    # Operation NAMES (get_thread / list_events / find_events /
    # respond_to_event) are deliberately NOT banned — they are the seam's own
    # operation-hint vocabulary (discover_* op arguments), not provider ids.
    r"|google_calendar|mcp__zapier|\bafter:[0-9a-zA-Z<\"']|\bsubject:[0-9a-zA-Z<\"']"
    r"|\blabelIds\b|\bthreadId\b|messageFormat|FULL_CONTENT"
)

# A line carrying this marker is rationale prose (a documented provider quirk,
# not tool routing) and is allow-listed from the gate-1 scan. Use sparingly;
# every use is a logged deviation in the build report.
_PROVIDER_NOTE_OK = "provider-note-ok"


def test_gate1_grep_clean_nonregression():
    skills = ROOT / "skills"
    offenders = set()
    for p in skills.rglob("*.md"):
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if _PROVIDER_NOTE_OK in line:
                    continue
                if _PROVIDER_TOKENS.search(line):
                    offenders.add(str(p.relative_to(ROOT)).replace("\\", "/"))
                    break
        except Exception:
            continue
    new = offenders - PENDING_DEHARDCODE
    resolved = PENDING_DEHARDCODE - offenders
    if resolved:
        print(f"     gate1: newly clean (remove from PENDING): {sorted(resolved)}")
    check("gate1 grep-clean: NO skill file names a provider tool/operator/URL"
          + ("" if PENDING_DEHARDCODE else " (PENDING set empty — hard gate)"),
          not new)
    if new:
        print(f"     gate1 OFFENDERS: {sorted(new)}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for fn in [test_gate2_matrix, test_gate3_declared_drafts, test_gate4_writer_wall_present,
               test_gate5_dedup_identity, test_gate6_read_only_degrades,
               test_gate7_empty_map_zero_change, test_gate1_grep_clean_nonregression]:
        fn()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL connector-agnostic matrix gates PASSED (gate1 hard grep-clean; gate5 at the real call sites)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
