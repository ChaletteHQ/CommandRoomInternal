#!/usr/bin/env python3
"""connector_adapters (provenance / capabilities / mail / calendar) + the
server-id-first resolution and H-H Zapier-lookalike fixture in tool_discovery.

Fixtures mirror REAL connector tool-id shapes captured in Step-0 (the actual
UUID servers + operation names), per the realdata-fixture gotcha — NOT idealized
`mcp__gmail__…` names.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from connector_adapters import provenance as prov, capabilities as cap, mail, calendar  # noqa: E402
import tool_discovery as td  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


# ---- provenance: the canonical dedup key (R16) --------------------------------

def test_canonical_key_gmail_invariant():
    # The BUG-3719 self-closure invariant: a legacy gmail:<id> row and every new
    # form of the SAME message reduce to ONE key.
    keys = {
        prov.canonical_dedup_key("gmail:MSG123"),
        prov.canonical_dedup_key(provider="Gmail", native_id="msg123"),
        prov.canonical_dedup_key(event={"data": {"provenance": {"provider": "gmail", "native_id": "msg123"}}}),
        prov.canonical_dedup_key(event={"type": "email_sent", "data": {"gmail_message_id": "msg123"}}),
        prov.canonical_dedup_key(event={"data": {"source_ref": "gmail:msg123"}}),
    }
    check("R16: all gmail spellings reduce to one key", keys == {"gmail:msg123"})


def test_canonical_key_slack_two_spellings():
    perma = prov.canonical_dedup_key("slack:https://acme.slack.com/archives/C0ABC/p1699900000123456")
    triple = prov.canonical_dedup_key("slack:T123/C0ABC/1699900000.123456")
    check("R16: both Slack spellings unify", perma == triple == "slack:c0abc:1699900000123456")


def test_canonical_key_granola_bare():
    # meeting_capture historical bare ids absorb via default_provider / meeting type.
    bare = prov.canonical_dedup_key("abc999", default_provider="granola")
    pref = prov.canonical_dedup_key("granola:abc999")
    ev = prov.canonical_dedup_key(event={"type": "meeting", "data": {"source_ref": "abc999"}})
    check("R16: bare granola id == prefixed == meeting-typed", bare == pref == ev == "granola:abc999")


def test_canonical_key_none_paths():
    check("no provenance -> None", prov.canonical_dedup_key(event={"data": {}}) is None)
    check("decision (no source_ref) -> None", prov.canonical_dedup_key(event={"type": "decision", "data": {"x": 1}}) is None)


def test_normalize_provenance_shape():
    p = prov.normalize_provenance(server_id="srv0test1", provider="superhuman", native_id="t1", account_id="acct_biz1")
    check("provenance carries connector(server_id)", p["connector"] == "srv0test1")
    check("provenance carries stable account_id (R3)", p["account_id"] == "acct_biz1")
    check("provenance drops None fields", "reason" not in p)


def test_email_drafted_provenance_builder():
    # EW2+T (F-12): the drafted-side dual-write builder — legacy field name,
    # backend-native value, structured provenance block. Superhuman and Gmail
    # draft ids land identically.
    d = prov.build_email_drafted_provenance(
        draft_id="sh_draft_123", provider="superhuman",
        server_id="srv0test1", account_id="acct_biz1")
    check("F-12: legacy gmail_draft_id carries the Superhuman native id",
          d["gmail_draft_id"] == "sh_draft_123")
    check("F-12: structured provenance carries provider",
          d["provenance"]["provider"] == "superhuman")
    check("F-12: structured provenance carries native_id",
          d["provenance"]["native_id"] == "sh_draft_123")
    check("F-12: structured provenance carries account_id",
          d["provenance"]["account_id"] == "acct_biz1")
    g = prov.build_email_drafted_provenance(draft_id="r-9988", provider="gmail")
    check("F-12: gmail draft id lands in the same shape",
          g["gmail_draft_id"] == "r-9988" and g["provenance"]["provider"] == "gmail")
    empty = prov.build_email_drafted_provenance(draft_id=None, provider="superhuman", server_id="srv0test1")
    check("F-12: no draft id -> legacy field omitted",
          "gmail_draft_id" not in empty)
    check("F-12: no draft id -> provenance still identifies the connector",
          empty["provenance"]["connector"] == "srv0test1")


def test_resolve_account_id_backcompat():
    # Legacy row with no account_id and no address -> None (readers treat as in-scope).
    check("legacy row -> account_id None", prov.resolve_account_id(event={"data": {"source_ref": "gmail:x"}}) is None)
    # Derivable from address deterministically.
    aid = prov.resolve_account_id(address="matthew@chaletteholdings.com")
    check("address -> derived stable account_id", aid and aid.startswith("acct_"))


# ---- capabilities: detect-once + fingerprint re-pair --------------------------

def test_capabilities_defaults():
    check("gmail: no native send (H-A)", cap.supports("gmail", "send") is False)
    check("gmail: draft yes", cap.supports("gmail", "draft") is True)
    check("superhuman: send + read_receipts + splits", all(cap.supports("superhuman", c) for c in ("send", "read_receipts", "splits")))
    check("outlook: read-only (no draft/send)", not cap.supports("outlook", "draft") and not cap.supports("outlook", "send"))
    check("unknown provider -> baseline fail-closed", cap.supports("zoho_mail", "send") is False)


def test_detect_once_override():
    # A detected row overrides the manifest default (e.g. a write-capable Outlook).
    caps = cap.capabilities_for("outlook", detected={"draft": True, "send": True})
    check("detected row overrides manifest default", caps["draft"] is True and caps["send"] is True)


def test_fingerprint_repair():
    # Reconnect changed the UUID; the tool SET still identifies the provider (A1b).
    sh = ["mcp__NEWID__list_splits", "mcp__NEWID__get_read_status_feed", "mcp__NEWID__undo_send", "mcp__NEWID__create_or_update_draft"]
    gm = ["mcp__OTHER__apply_sensitive_message_label", "mcp__OTHER__search_threads", "mcp__OTHER__create_draft"]
    check("fingerprint -> superhuman", cap.best_fingerprint_match(sh) == "superhuman")
    check("fingerprint -> gmail", cap.best_fingerprint_match(gm) == "gmail")
    check("no confident match -> None", cap.best_fingerprint_match(["mcp__X__do_thing"]) is None)


def test_zapier_provider_flag():
    check("zapier_gmail is_zapier", cap.is_zapier_provider("zapier_gmail") is True)
    check("gmail not is_zapier", cap.is_zapier_provider("gmail") is False)


# ---- mail / calendar adapters -------------------------------------------------

def test_mail_query_compile():
    q = mail.compile_search({"unread": True, "in_sent": True, "from": "a@sender.example.com", "not_draft": True}, "gmail")
    check("gmail operators compiled", "is:unread" in q and "in:sent" in q and "from:a@sender.example.com" in q and "-in:draft" in q)
    check("superhuman -> structured passthrough", isinstance(mail.compile_search({"unread": True}, "superhuman"), dict))
    check("unknown provider -> passthrough dict", isinstance(mail.compile_search({"unread": True}, "zoho"), dict))


def test_mail_query_disjunction():
    # Review fix 3: the two OR-bearing queries the Phase-2 rewrites regressed
    # must compile back to their ORIGINAL Gmail literals through the seam.
    # 1. morning-briefing self-reply broaden path.
    q = mail.compile_search(
        {"any_of": [{"in_inbox": True}, {"in_sent": True}], "not_draft": True},
        "gmail")
    check("fix3: inbox-or-sent compiles to the original Gmail literal",
          q == "(in:inbox OR in:sent) -in:draft")
    # 2. orchestrator-dont-forget live-contact lookup.
    q2 = mail.compile_search(
        {"any_of": [{"from_me": True, "to": "pat@client.example.com"},
                    {"from": "pat@client.example.com"}]},
        "gmail")
    check("fix3: to/from-address disjunction compiles to the original OR-group",
          q2 == "(from:me to:pat@client.example.com OR from:pat@client.example.com)")
    # Disjunction composes with a trailing window operator.
    q3 = mail.compile_search(
        {"any_of": [{"from_me": True, "to": "pat@client.example.com"},
                    {"from": "pat@client.example.com"}], "newer_than": "7d"},
        "gmail")
    check("fix3: disjunction + window operator",
          q3 == "(from:me to:pat@client.example.com OR from:pat@client.example.com) newer_than:7d")
    # Outlook compiles an equivalent parenthesized OR-group.
    qo = mail.compile_search(
        {"any_of": [{"in_inbox": True}, {"in_sent": True}], "not_draft": True},
        "outlook")
    check("fix3: outlook OR-group equivalent",
          qo == "(parentFolder:inbox OR parentFolder:sentitems) parentFolder:ne:drafts")
    # Single-branch any_of degrades to the bare branch (no stray parens).
    q1 = mail.compile_search({"any_of": [{"in_sent": True}]}, "gmail")
    check("fix3: single-branch any_of -> bare compile", q1 == "in:sent")
    # Pass-through providers keep the any_of structure verbatim.
    sh = mail.compile_search({"any_of": [{"in_inbox": True}, {"in_sent": True}]}, "superhuman")
    check("fix3: passthrough provider keeps any_of structure",
          isinstance(sh, dict) and isinstance(sh.get("any_of"), list))


def test_mail_threading_and_deeplink():
    check("gmail threading field", mail.threading_field("gmail") == "threadId")
    check("outlook conversationId", mail.threading_field("outlook") == "conversationId")
    check("prefer returned URL always", mail.deep_link("gmail", "id1", returned_url="https://x/y") == "https://x/y")
    check("gmail host fallback", mail.deep_link("gmail", "id1") == "https://mail.google.com/mail/u/0/#all/id1")
    check("superhuman no host -> None (degrade, N8)", mail.deep_link("superhuman", "id1") is None)


def test_calendar_adapter():
    w = calendar.compile_window("2026-07-11T00:00:00Z", "2026-07-12T00:00:00Z", "google_calendar")
    check("google window fields", "timeMin" in w and "timeMax" in w)
    check("outlook rsvp accepted", calendar.is_accepted({"responseStatus": "accepted"}, "outlook_calendar"))
    check("google calendarId addressing (N4)", calendar.calendar_addressing_field("google_calendar") == "calendarId")
    check("calendar deep-link prefers returned url", calendar.deep_link("google_calendar", "e1", returned_url="https://cal") == "https://cal")


# ---- tool_discovery: server-id-first + the H-H Zapier-lookalike fixture -------

def _real_env_tools():
    """The REAL M-environment shape (Step-0): Superhuman + native Gmail (no send
    tool) + the Zapier Gmail leg (UUID-namespaced, tool names contain 'gmail')
    + Google Calendar."""
    return [
        td.ToolDescriptor("mcp__srv0test1__create_or_update_draft"),
        td.ToolDescriptor("mcp__srv0test1__send_draft"),
        td.ToolDescriptor("mcp__srv0test1__list_splits"),
        td.ToolDescriptor("mcp__f12657a1__search_threads"),
        td.ToolDescriptor("mcp__f12657a1__create_draft"),
        # The H-H trap: a UUID Zapier server whose tool names contain 'gmail'.
        td.ToolDescriptor("mcp__4658de3a__gmail_send_email"),
        td.ToolDescriptor("mcp__4658de3a__gmail_reply_to_email"),
        td.ToolDescriptor("mcp__4658de3a__get_configuration_url"),
        td.ToolDescriptor("mcp__f9119bb5__create_event"),
        td.ToolDescriptor("mcp__f9119bb5__list_calendars"),
    ]


def test_HH_zapier_lookalike():
    tools = _real_env_tools()
    zap = td.zapier_servers(tools)
    check("H-H: Zapier server detected by signature (get_configuration_url)", "4658de3a" in zap)
    # The Zapier gmail_send_email must NEVER be returned as a native mail send.
    # Assert the DEGRADE outcome (tool_id None + the plain-English reason), not
    # a trivially-true `!=` (review should-fix: `None != id` passes for the
    # wrong reasons).
    send = td.discover_mail_send_tool(tools)
    check("H-H: native mail-send degrades (None + no-native-tool reason), never the Zapier leg",
          send.tool_id is None and "no native mail tool" in (send.reason or "").lower())
    # discover_mail_reply_tool likewise must not grab the Zapier reply.
    reply = td.discover_mail_reply_tool(tools)
    check("H-H: native mail-reply degrades (None + no-native-tool reason), never the Zapier leg",
          reply.tool_id is None and "no native mail tool" in (reply.reason or "").lower())
    # The Zapier send tool IS reachable through its own dedicated helper — the
    # UUID leg is recognized by the get_configuration_url signature (heuristic)
    # OR by an explicitly pinned server-id list.
    zsend = td.discover_zapier_send_tool([
        td.ToolDescriptor("mcp__4658de3a__gmail_send_email", "Gmail Send", "send email"),
        td.ToolDescriptor("mcp__4658de3a__get_configuration_url", "Configure", "configure zap"),
    ])
    check("H-H: Zapier send reachable via signature heuristic", zsend.tool_id == "mcp__4658de3a__gmail_send_email")
    zsend2 = td.discover_zapier_send_tool(
        [td.ToolDescriptor("mcp__4658de3a__gmail_send_email", "Gmail Send", "send email")],
        zapier_ids=["4658de3a"])
    check("H-H: Zapier send reachable via pinned server-id", zsend2.tool_id == "mcp__4658de3a__gmail_send_email")


def test_server_id_first_resolution():
    tools = _real_env_tools()
    # Declared email backend = Superhuman (srv0test1). Draft resolves ON that server.
    declared = {"server_id": "srv0test1", "provider": "superhuman"}
    r = td.discover_for_category("email", "create_or_update_draft", tools, declared=declared)
    check("server-id-first: resolves draft on declared server", r.tool_id == "mcp__srv0test1__create_or_update_draft")
    check("server-id-first: platform tagged from declared", r.platform == "superhuman")
    # Declared calendar backend = Google Calendar.
    rc = td.discover_for_category("calendar", "create_event", tools, declared={"server_id": "f9119bb5", "provider": "google_calendar"})
    check("server-id-first: resolves calendar create_event on declared server", rc.tool_id == "mcp__f9119bb5__create_event")
    # No declared backend -> None + reason (caller falls back to substring, R4).
    rn = td.discover_for_category("email", "create_draft", tools, declared=None)
    check("no declared backend -> None (fallback to today's behavior)", rn.tool_id is None and "no declared" in rn.reason)
    # Declared backend that lacks the op -> capability-absent reason (degrade).
    rmiss = td.discover_for_category("email", "undo_send", tools, declared={"server_id": "f12657a1", "provider": "gmail"})
    check("declared backend missing op -> capability-absent reason", rmiss.tool_id is None and "no" in rmiss.reason.lower())


def test_repair_backend_helper():
    check("repair_backend re-pairs a reconnected superhuman",
          td.repair_backend(["mcp__NEW__list_splits", "mcp__NEW__get_read_status_feed", "mcp__NEW__undo_send"]) == "superhuman")


def test_detect_backend_drift():
    # R13: declared server present -> no drift.
    tools = _real_env_tools()
    declared = {"server_id": "srv0test1", "provider": "superhuman"}
    check("R13: declared server present -> None (no drift)",
          td.detect_backend_drift(tools, declared) is None)
    # Reconnect rotated the UUID: declared id gone, same provider under a new id.
    rotated = [td.ToolDescriptor(i) for i in (
        "mcp__NEWUUID__create_or_update_draft", "mcp__NEWUUID__send_draft",
        "mcp__NEWUUID__list_splits", "mcp__NEWUUID__get_read_status_feed",
        "mcp__NEWUUID__undo_send",
        "mcp__f12657a1__search_threads", "mcp__f12657a1__create_draft")]
    drift = td.detect_backend_drift(rotated, declared)
    check("R13: rotated UUID detected with a same-provider candidate",
          drift is not None and drift["candidate_server_id"] == "NEWUUID"
          and drift["candidate_provider"] == "superhuman")
    # Backend genuinely gone (no fingerprint match) -> drift with no candidate.
    gone = [td.ToolDescriptor("mcp__f9119bb5__list_calendars"),
            td.ToolDescriptor("mcp__f9119bb5__create_event")]
    drift2 = td.detect_backend_drift(gone, declared)
    check("R13: backend gone -> drift with candidate None",
          drift2 is not None and drift2["candidate_server_id"] is None)
    # The seam's declared-path reason distinguishes drift from capability-absent.
    r = td.discover_for_category("email", "create_or_update_draft", gone, declared=declared)
    check("R13: discover_for_category reports NOT PRESENT (drift), not capability-absent",
          r.tool_id is None and "not present" in r.reason.lower())
    gmail_tools = [td.ToolDescriptor("mcp__f12657a1__search_threads"),
                   td.ToolDescriptor("mcp__f12657a1__create_draft")]
    r2 = td.discover_for_category("email", "undo_send", gmail_tools,
                                  declared={"server_id": "f12657a1", "provider": "gmail"})
    check("R13: capability-absent reason unchanged when the server IS present",
          r2.tool_id is None and "exposes no" in r2.reason.lower())


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for fn in [
        test_canonical_key_gmail_invariant, test_canonical_key_slack_two_spellings,
        test_canonical_key_granola_bare, test_canonical_key_none_paths,
        test_normalize_provenance_shape, test_email_drafted_provenance_builder,
        test_resolve_account_id_backcompat,
        test_capabilities_defaults, test_detect_once_override, test_fingerprint_repair,
        test_zapier_provider_flag, test_mail_query_compile, test_mail_query_disjunction,
        test_mail_threading_and_deeplink,
        test_calendar_adapter, test_HH_zapier_lookalike, test_server_id_first_resolution,
        test_repair_backend_helper, test_detect_backend_drift,
    ]:
        fn()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL connector_adapters tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
