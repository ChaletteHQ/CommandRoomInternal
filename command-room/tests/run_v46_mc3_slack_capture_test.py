#!/usr/bin/env python3
"""
v4.6.0 MC3 — Slack commitment capture (COMMITMENT_SCHEMA.md's own TODO).

The schema reserved `slack:<permalink>` source_refs for years while no writer
produced them — a whole channel of promises invisible. MC3 gives
scan-for-commitments a Slack leg whose deterministic half is
`shared/scripts/slack_capture.py`. This suite regresses that half against a
fixture transcript built from REAL Slack API message shapes (threads via
`thread_ts`, edits both as in-place `edited` messages and as `message_changed`
wrappers, `bot_message`/`bot_id` noise, `channel_join` noise, empty text) —
per the realdata-fixture gotcha: unit-green code that only ever saw idealized
shapes crashes on the substrate.

Covers:
  HYGIENE      normalize_message drops noise, unwraps edits to LATEST text,
               re-screens unwrapped bot edits.
  DIRECTION    user's own messages -> user_sent (the promise source); messages
               naming the user (mention OR name token) -> names_user (the
               owed-to-you source); third-party -> other; short-token safety.
  BOUNDS       within_window default-7d math (unparseable ts is OUT; future
               ts is OUT); cap_messages keeps newest and REPORTS the drop.
  CAPTURE      build_slack_commitment_event runs the full Stage-D / S2 /
               Stage-E block + pending_review inversion — same rules as
               session_sweep._gate_commitment (v4.5.2 C1 parity) — and
               refuses direction 'other' (the W4c relevance bound).
  E2E          fixture -> builders -> event_gate.append_event into a temp
               workspace: slack: source_refs on disk, cmt_ ids minted, kinds
               kept, backdated ts, the promise/owed-to-you split lands as
               owner vs counterparty; Step-4 idempotency via already_captured;
               resolved source_refs skip.
  CONNECTOR    discover_slack_tool: absent -> tool_id None (clean no-op leg);
               Zapier-only Slack -> None; real-shaped tool list -> match with
               platform 'slack'; soft match when op hint missing.
"""
from __future__ import annotations

import datetime
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import slack_capture as sc  # noqa: E402
from event_gate import append_event  # noqa: E402
from tool_discovery import ToolDescriptor, discover_slack_tool  # noqa: E402

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


def expect_raises(label: str, fn, *args, needle: str = "", **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except sc.SlackItemError as e:
        ok = needle.lower() in str(e).lower() if needle else True
        check(label, ok, f"raised but message lacked {needle!r}: {e}")
    except Exception as e:  # noqa: BLE001
        check(label, False, f"wrong exception {type(e).__name__}: {e}")
    else:
        check(label, False, "did not raise SlackItemError")


# ---------------------------------------------------------------------------
# Fixture — one channel's history in REAL Slack API shapes. The workspace
# owner is U_OWNER ("Morgan"); counterpart humans are U_BOWIE, U_STACY,
# U_RICK; B_GRANOLA is a bot.
# Scan reference time: 2026-07-09 18:00 UTC.
# ---------------------------------------------------------------------------
NOW = datetime.datetime(2026, 7, 9, 18, 0, tzinfo=datetime.timezone.utc)

# build_slack_commitment_event stamps status (open vs overdue) against the
# REAL clock — no injectable now — so due-date fixtures must be computed
# relative to today, never hardcoded (a hardcoded "future" date silently
# becomes past and flips the expected status).
_TODAY = datetime.datetime.now(datetime.timezone.utc).date()
FUTURE_DUE = (_TODAY + datetime.timedelta(days=7)).isoformat()
PAST_DUE = (_TODAY - datetime.timedelta(days=7)).isoformat()


def _ts(days_ago: float, tail: str = "000100") -> str:
    return f"{(NOW - datetime.timedelta(days=days_ago)).timestamp():.0f}.{tail}"


U_OWNER, U_BOWIE, U_STACY, U_RICK = "U0OWNER", "U0BOWIE", "U0STACY", "U0RICK"

MSG_USER_PROMISE = {  # the user's own promise, explicit relative due
    "type": "message", "user": U_OWNER, "ts": _ts(1.0),
    "text": f"<@{U_BOWIE}> I'll send you the revised onboarding deck by Friday.",
    "permalink": "https://acme.slack.com/archives/C0GEN/p1001",
}
MSG_OWED_TO_USER = {  # someone else promising TO the user (mention form)
    "type": "message", "user": U_BOWIE, "ts": _ts(2.0),
    "text": f"<@{U_OWNER}> I owe you the Q3 vendor shortlist — you'll have it Thursday.",
    "permalink": "https://acme.slack.com/archives/C0GEN/p1002",
}
MSG_NAMES_USER_PLAIN = {  # names the user in plain text, no mention markup
    "type": "message", "user": U_STACY, "ts": _ts(3.0),
    "text": "Morgan, I'll get you the contract redlines before the offsite.",
    "permalink": "https://acme.slack.com/archives/C0GEN/p1003",
}
MSG_THREAD_REPLY = {  # a threaded reply (thread_ts != ts) from the user
    "type": "message", "user": U_OWNER, "ts": _ts(0.5),
    "thread_ts": MSG_OWED_TO_USER["ts"],
    "text": "and I'll intro you to the Acme buyer once it lands, no date on that yet",
    "permalink": "https://acme.slack.com/archives/C0GEN/p1004",
}
MSG_EDITED_INPLACE = {  # history shape for an edited message: text is current
    "type": "message", "user": U_OWNER, "ts": _ts(4.0),
    "text": "I'll finalize Dana's paperwork by July 10 (edited: was July 8)",
    "edited": {"user": U_OWNER, "ts": _ts(3.9)},
    "permalink": "https://acme.slack.com/archives/C0GEN/p1005",
}
MSG_EDIT_WRAPPER = {  # RTM/event shape: message_changed wrapper, nested latest
    "type": "message", "subtype": "message_changed", "channel": "C0GEN",
    "ts": _ts(0.2),
    "message": {
        "type": "message", "user": U_BOWIE, "ts": _ts(2.5),
        "text": f"<@{U_OWNER}> correction — the shortlist lands Wednesday, not Thursday",
        "edited": {"user": U_BOWIE, "ts": _ts(0.2)},
        "permalink": "https://acme.slack.com/archives/C0GEN/p1006",
    },
}
MSG_THIRD_PARTY = {  # Stacy owes Rick — never the user's open item
    "type": "message", "user": U_STACY, "ts": _ts(1.5),
    "text": f"<@{U_RICK}> I'll have the audit report to you Monday.",
    "permalink": "https://acme.slack.com/archives/C0GEN/p1007",
}
MSG_BOT = {
    "type": "message", "subtype": "bot_message", "bot_id": "B0GRAN",
    "ts": _ts(1.1), "text": "Granola: your meeting notes are ready!",
}
MSG_BOT_NO_SUBTYPE = {  # bots don't always carry the subtype — bot_id is the tell
    "type": "message", "bot_id": "B0ZAP", "user": "U0ZAPBOT",
    "ts": _ts(1.2), "text": "Zap ran successfully.",
}
MSG_JOIN = {
    "type": "message", "subtype": "channel_join", "user": U_RICK,
    "ts": _ts(2.2), "text": f"<@{U_RICK}> has joined the channel",
}
MSG_EMPTY = {"type": "message", "user": U_STACY, "ts": _ts(2.3), "text": "   "}
MSG_DELETED_WRAPPER = {
    "type": "message", "subtype": "message_deleted", "ts": _ts(2.4),
    "deleted_ts": _ts(2.6),
}
MSG_STALE = {  # outside the 7-day window
    "type": "message", "user": U_OWNER, "ts": _ts(12.0),
    "text": "I'll send the May recap tomorrow.",
    "permalink": "https://acme.slack.com/archives/C0GEN/p0900",
}

FIXTURE = [
    MSG_USER_PROMISE, MSG_OWED_TO_USER, MSG_NAMES_USER_PLAIN, MSG_THREAD_REPLY,
    MSG_EDITED_INPLACE, MSG_EDIT_WRAPPER, MSG_THIRD_PARTY, MSG_BOT,
    MSG_BOT_NO_SUBTYPE, MSG_JOIN, MSG_EMPTY, MSG_DELETED_WRAPPER, MSG_STALE,
]


def _ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="mc3_slack_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    return ws


print("== hygiene: normalize_message ==")
kept = [m for m in (sc.normalize_message(m) for m in FIXTURE) if m]
check("bot_message dropped", sc.normalize_message(MSG_BOT) is None)
check("bot_id without subtype dropped", sc.normalize_message(MSG_BOT_NO_SUBTYPE) is None)
check("channel_join dropped", sc.normalize_message(MSG_JOIN) is None)
check("empty text dropped", sc.normalize_message(MSG_EMPTY) is None)
check("message_deleted dropped", sc.normalize_message(MSG_DELETED_WRAPPER) is None)
check("plain human message kept", sc.normalize_message(MSG_USER_PROMISE) is not None)
check("threaded reply kept", sc.normalize_message(MSG_THREAD_REPLY) is not None)
inplace = sc.normalize_message(MSG_EDITED_INPLACE)
check("in-place edited message kept at current text",
      inplace is not None and "July 10" in inplace["text"])
unwrapped = sc.normalize_message(MSG_EDIT_WRAPPER)
check("message_changed unwraps to LATEST text",
      unwrapped is not None and "Wednesday" in (unwrapped or {}).get("text", ""))
check("message_changed keeps inner author/ts",
      (unwrapped or {}).get("user") == U_BOWIE
      and (unwrapped or {}).get("ts") == MSG_EDIT_WRAPPER["message"]["ts"])
bot_edit = sc.normalize_message(
    {"type": "message", "subtype": "message_changed",
     "message": {"type": "message", "bot_id": "B1", "user": "U9",
                 "ts": _ts(1.0), "text": "edited bot text"}})
check("unwrapped bot edit re-screened and dropped", bot_edit is None)
# 8 humans survive hygiene (the stale one included — the WINDOW filters it,
# hygiene doesn't); window then drops the 12-day-old, leaving 7 to extract.
check(f"fixture survivors = 8 human messages (got {len(kept)})", len(kept) == 8)
in_window = [m for m in kept if sc.within_window(m["ts"], now=NOW)]
check(f"window pass leaves 7 (got {len(in_window)})", len(in_window) == 7
      and all("May recap" not in m["text"] for m in in_window))

print("== direction split ==")
d = lambda m: sc.classify_direction(m, user_slack_ids=[U_OWNER], user_names=["Morgan", "Morgan Sample"])
check("own message -> user_sent", d(MSG_USER_PROMISE) == sc.DIRECTION_USER_SENT)
check("own threaded reply -> user_sent", d(MSG_THREAD_REPLY) == sc.DIRECTION_USER_SENT)
check("mention of user -> names_user", d(MSG_OWED_TO_USER) == sc.DIRECTION_NAMES_USER)
check("plain-text name -> names_user", d(MSG_NAMES_USER_PLAIN) == sc.DIRECTION_NAMES_USER)
check("third-party <-> third-party -> other", d(MSG_THIRD_PARTY) == sc.DIRECTION_OTHER)
check("unwrapped edit naming user -> names_user",
      d(unwrapped) == sc.DIRECTION_NAMES_USER)
check("short name tokens never match",
      sc.classify_direction(
          {"user": U_STACY, "text": "an aside about m and md files"},
          user_slack_ids=[U_OWNER], user_names=["M", "md"]) == sc.DIRECTION_OTHER)

print("== bounds: window + cap ==")
check("1-day-old inside default window", sc.within_window(MSG_USER_PROMISE["ts"], now=NOW))
check("12-day-old outside default window", not sc.within_window(MSG_STALE["ts"], now=NOW))
check("12-day-old inside a 30-day window",
      sc.within_window(MSG_STALE["ts"], days=30, now=NOW))
check("unparseable ts is OUT", not sc.within_window("not-a-ts", now=NOW))
check("missing ts is OUT", not sc.within_window(None, now=NOW))
check("future ts is OUT", not sc.within_window(_ts(-2.0), now=NOW))
many = [{"ts": _ts(0.01 * i), "text": f"m{i}", "user": U_OWNER} for i in range(10)]
kept_cap, dropped = sc.cap_messages(many, cap=4)
check("cap keeps newest N", len(kept_cap) == 4
      and all(m["text"] in ("m0", "m1", "m2", "m3") for m in kept_cap))
check("cap REPORTS the drop (no silent caps)", dropped == 6)
check("under-cap passes through undropped",
      sc.cap_messages(many, cap=100) == (many, 0))

print("== capture block: Stage-D / S2 / Stage-E / inversion ==")
PERMA = MSG_USER_PROMISE["permalink"]
BASE = dict(permalink=PERMA, kind="promise", direction=sc.DIRECTION_USER_SENT,
            owner_id="person_001", counterparty_id="person_042",
            due=FUTURE_DUE, message_ts=MSG_USER_PROMISE["ts"])

ev = sc.build_slack_commitment_event("send Bowie the revised onboarding deck", **BASE)
check("source_ref is slack:<permalink>", ev["data"]["source_ref"] == f"slack:{PERMA}")
check("ts backdated to the message time",
      ev.get("ts", "").startswith(
          datetime.datetime.fromtimestamp(
              float(MSG_USER_PROMISE["ts"]), datetime.timezone.utc
          ).isoformat()[:19]))
check("counterparty joins person_ids", "person_042" in ev["person_ids"])
check("owner joins person_ids", "person_001" in ev["person_ids"])
check("confident capture carries NO pending_review",
      "pending_review" not in ev["data"])
check("future due -> status open", ev["data"]["status"] == "open")
past = sc.build_slack_commitment_event("t", **{**BASE, "due": PAST_DUE})
check("past due -> status overdue at write", past["data"]["status"] == "overdue")

expect_raises("Stage D: missing kind rejects",
              sc.build_slack_commitment_event, "t",
              **{**BASE, "kind": None}, needle="data.kind")
expect_raises("Stage D: invalid kind rejects",
              sc.build_slack_commitment_event, "t",
              **{**BASE, "kind": "todo"}, needle="data.kind")
expect_raises("S2: no due and no no_due rejects",
              sc.build_slack_commitment_event, "t",
              **{**BASE, "due": None}, needle="due date")
expect_raises("S2: due AND no_due contradiction rejects",
              sc.build_slack_commitment_event, "t",
              **{**BASE, "no_due": True}, needle="pick one")
expect_raises("promise-vs-task: task with counterparty rejects",
              sc.build_slack_commitment_event, "t",
              **{**BASE, "kind": "task"}, needle="promise, not a task")
expect_raises("relevance bound: direction 'other' refuses to mint",
              sc.build_slack_commitment_event, "t",
              **{**BASE, "direction": sc.DIRECTION_OTHER}, needle="third-party")
expect_raises("empty title rejects",
              sc.build_slack_commitment_event, "   ", **BASE, needle="title")
expect_raises("empty permalink rejects",
              sc.build_slack_commitment_event, "t",
              **{**BASE, "permalink": ""}, needle="permalink")

no_due_task = sc.build_slack_commitment_event(
    "intro the Acme buyer once the deck lands",
    permalink=MSG_THREAD_REPLY["permalink"], kind="promise",
    direction=sc.DIRECTION_USER_SENT, owner_id="person_001",
    counterparty_name="Bowie", no_due=True,
    message_ts=MSG_THREAD_REPLY["ts"])
check("explicit no_due accepted", no_due_task["data"].get("no_due") is True
      and no_due_task["data"]["due"] == "")
check("inversion: unresolved counterparty name stamps pending_review",
      no_due_task["data"].get("pending_review") is True
      and "no person record" in no_due_task["data"].get("review_reason", ""))

bare = sc.build_slack_commitment_event(
    "chase the thing", permalink=PERMA + "x", kind="promise",
    direction=sc.DIRECTION_NAMES_USER, no_due=True)
check("inversion: promise w/o counterparty AND w/o owner stamps pending_review",
      bare["data"].get("pending_review") is True
      and "no counterparty" in bare["data"]["review_reason"]
      and "no resolved owner" in bare["data"]["review_reason"])

lowconf = sc.build_slack_commitment_event(
    "t", **BASE, classification_confidence=0.4)
check("inversion: sub-threshold confidence stamps pending_review",
      lowconf["data"].get("pending_review") is True
      and "below threshold" in lowconf["data"]["review_reason"])
preset = sc.build_slack_commitment_event(
    "t", **BASE, pending_review=True, review_reason="extractor said so")
check("extractor-set pending_review never unset",
      preset["data"].get("pending_review") is True
      and preset["data"]["review_reason"] == "extractor said so")

print("== e2e: fixture -> builders -> gated append -> disk ==")
ws = _ws()
events_path = ws / "_hq" / "data" / "events.jsonl"
batch = [
    # what I promised (user_sent lane): owner = the user
    sc.build_slack_commitment_event(
        "send Bowie the revised onboarding deck",
        permalink=MSG_USER_PROMISE["permalink"], kind="promise",
        direction=sc.DIRECTION_USER_SENT, owner_id="person_001",
        counterparty_id="person_042", due=FUTURE_DUE,
        evidence=MSG_USER_PROMISE["text"], channel="#general",
        message_ts=MSG_USER_PROMISE["ts"], classification_confidence=0.9),
    # owed to you (names_user lane): owner = the counterpart, user is counterparty
    sc.build_slack_commitment_event(
        "Bowie to send the Q3 vendor shortlist",
        permalink=MSG_OWED_TO_USER["permalink"], kind="promise",
        direction=sc.DIRECTION_NAMES_USER, owner_id="person_042",
        counterparty_id="person_001", due=PAST_DUE,
        evidence=MSG_OWED_TO_USER["text"], channel="#general",
        message_ts=MSG_OWED_TO_USER["ts"], classification_confidence=0.85),
]
append_event(events_path, batch, holder="scan-for-commitments")
on_disk = [json.loads(l) for l in events_path.read_text(encoding="utf-8").splitlines() if l.strip()]
cmts = [e for e in on_disk if e.get("type") == "commitment"]
check(f"2 commitments on disk (got {len(cmts)})", len(cmts) == 2)
check("all carry slack: source_refs",
      all(e["data"]["source_ref"].startswith("slack:https://") for e in cmts))
check("gate minted cmt_ ids",
      all(str(e["data"].get("id", "")).startswith("cmt_") for e in cmts))
check("kinds survive the gate", all(e["data"]["kind"] == "promise" for e in cmts))
check("direction split lands as owner vs counterparty",
      any(e["data"]["owner_id"] == "person_001" and e["data"]["counterparty_id"] == "person_042" for e in cmts)
      and any(e["data"]["owner_id"] == "person_042" and e["data"]["counterparty_id"] == "person_001" for e in cmts))
check("source_skill is scan-for-commitments",
      all(e["source_skill"] == "scan-for-commitments" for e in cmts))

check("Step-4 idempotency: same (permalink, title) reads captured",
      sc.already_captured(ws, MSG_USER_PROMISE["permalink"],
                          "send Bowie the revised onboarding deck"))
check("case/truncation tolerant (first-60 ci rule)",
      sc.already_captured(ws, MSG_USER_PROMISE["permalink"],
                          "SEND BOWIE THE REVISED ONBOARDING DECK and whatever trails past sixty characters"))
check("same permalink, different title NOT captured",
      not sc.already_captured(ws, MSG_USER_PROMISE["permalink"],
                              "an entirely different promise"))
check("unseen permalink NOT captured",
      not sc.already_captured(ws, "https://acme.slack.com/archives/C0GEN/p9999", "anything"))
append_event(events_path, {
    "type": "commitment_resolved", "source_skill": "log-resolution",
    "data": {"commitment_id": cmts[0]["data"]["id"],
             "source_ref": cmts[0]["data"]["source_ref"],
             "evidence": "test closure"}}, holder="test")
check("resolved source_ref reads captured (skip re-creating done work)",
      sc.already_captured(ws, MSG_USER_PROMISE["permalink"],
                          "a brand new spelling of the same promise"))

print("== connector discovery ==")
REAL_SHAPED = [
    ToolDescriptor("mcp__e235c676-b584__slack_read_channel", "Slack read channel", ""),
    ToolDescriptor("mcp__e235c676-b584__slack_read_thread", "Slack read thread", ""),
    ToolDescriptor("mcp__e235c676-b584__slack_search_channels", "Slack search channels", ""),
    ToolDescriptor("mcp__e235c676-b584__slack_search_users", "Slack search users", ""),
    ToolDescriptor("mcp__abc456__gmail_send_message", "Gmail Send", ""),
]
r = discover_slack_tool(REAL_SHAPED, "read_channel")
check("matches real-shaped read_channel",
      r.tool_id == "mcp__e235c676-b584__slack_read_channel" and r.platform == "slack")
check("matches search_users",
      discover_slack_tool(REAL_SHAPED, "search_users").tool_id
      == "mcp__e235c676-b584__slack_search_users")
soft = discover_slack_tool(REAL_SHAPED, "read_user_profile")
check("soft-matches when op hint absent (tool still usable)",
      soft.tool_id is not None and "not in tool ID" in soft.reason)
absent = discover_slack_tool(
    [ToolDescriptor("mcp__abc456__gmail_send_message", "Gmail", "")], "read_channel")
check("Slack absent -> tool_id None (leg silently doesn't exist)",
      absent.tool_id is None)
check("Zapier-only Slack does NOT count as connected",
      discover_slack_tool(
          [ToolDescriptor("mcp__zapier_slack__send_channel_message", "Zap", "")],
          "read_channel").tool_id is None)
check("empty registry -> clean None", discover_slack_tool([], "read_channel").tool_id is None)

print()
print(f"MC3 slack capture: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
