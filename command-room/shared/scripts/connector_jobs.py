#!/usr/bin/env python3
"""connector_jobs: the pure helpers of the connector job record (cr1#1877
slice 0). The record itself, its readers and its one writer live in
`connector_config`; nothing here reads or writes the workspace.

- `provider_of(tool_ids, category)`: the provider a connector is, from the
  capability manifest's fingerprints, matched only among the providers whose
  categories include `category`. Never typed by Claude (the #1818 risk).
- `shape_of(payload, category)`: an answer's SHAPE under a closed grammar:
  types, the field names the code reads (`SHAPE_KEYS`), and counts. Never a
  value, never another key (dropped and counted), never the text of a
  content block. The page runs the same function (`shapeOf`) and the server
  checks what it wrote with `valid_shape`.
- `valid_test_entry(entry, category)`: the server's check of one page-written
  test result before it reaches the record.
- `IDENTITY` and `identity_match`: who a connector is signed in as, for the
  check before each work write. Built and pinned here; wired to writes slice
  by slice (spec §5), never in slice 0.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: The closed shape grammar's limits (spec §4).
SHAPE_TYPES = ("obj", "arr", "str", "num", "bool", "null")
SHAPE_MAX_N = 99
SHAPE_MAX_OTHER = 999
SHAPE_MAX_DEPTH = 6
SHAPE_ITEMS = 5
ARGS_KEYS_MAX = 40
_ARG_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
#: The field shapes a job block may hold (review of slice 0): a time, a Hub
#: artifact address, an account id, a hash, a short code. Nothing free text.
class _Full:
    """A pattern matched against the WHOLE value (`fullmatch`): `$` would let
    a trailing newline through ("Sample Mail\\n")."""

    def __init__(self, pattern: str):
        self._re = re.compile(pattern)

    def match(self, value) -> bool:
        return bool(self._re.fullmatch(str(value)))


ISO_RE = _Full(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}:[0-9]{2})?")
ARTIFACT_RE = _Full(r"https://claude\.ai/(code/)?artifact/[A-Za-z0-9-]{6,80}")
ACCOUNT_ID_RE = _Full(r"acct_[0-9a-f]{12}")
HASH_RE = _Full(r"[0-9a-f]{64}")
REASON_RE = _Full(r"[a-z_]{1,40}")
_NAME_RE = _Full(r"[A-Za-z0-9 ._()-]{1,80}")
PAGE_STATES = ("granted", "prompt", "denied", "unavailable", "not_needed")
#: Text that reads like an address even without "@": "name at domain dot com",
#: a domain ending, or "name.surname.example" (review of slice 0).
_ADDRESSY = re.compile(r"(@|\(at\)|\bat\b.*\bdot\b|\.(com|org|net|io|co|edu|gov)\b|[a-z0-9]\.[a-z0-9]+\.[a-z]{2,})", re.I)

#: The field names the code reads, per category: only these survive in a
#: shape; every other key is dropped and counted. Taken from the page maps
#: (`MAIL_LIST`, `CALENDAR_LIVE`, `CHAT_DIRECT`) and the adapters; each slice
#: that wires a job confirms its own list against its readers.
SHAPE_KEYS: Dict[str, frozenset] = {
    "email": frozenset({"threads", "messages", "items", "results", "next_cursor", "nextCursor",
                        "nextPageToken", "nextOffset", "id", "thread_id", "threadId", "subject",
                        "from", "to", "cc", "date", "snippet", "labels", "unread", "account"}),
    "calendar": frozenset({"events", "items", "value", "nextPageToken", "id", "summary", "subject",
                           "start", "end", "dateTime", "date", "timeZone", "attendees", "organizer",
                           "status", "responseStatus", "recurringEventId", "seriesMasterId",
                           "isCancelled", "showAs", "email", "self"}),
    "personal_calendar": frozenset({"events", "items", "value", "nextPageToken", "id", "start", "end",
                                    "dateTime", "date", "timeZone", "status"}),
    "meetings": frozenset({"meetings", "items", "results", "id", "title", "date", "updated_at",
                           "attendees", "next_cursor"}),
    "documents": frozenset({"files", "items", "results", "id", "name", "mimeType", "modifiedTime",
                            "nextPageToken"}),
    "chat": frozenset({"results", "messages", "items", "id", "ts", "channel", "user", "text", "next_cursor"}),
}

#: The error codes a page may record (the artifact runtime's McpErrorCode set).
MCP_ERROR_CODES = frozenset({
    "needs_reauth", "server_not_connected", "selection_required", "server_not_found",
    "server_unavailable", "not_in_manifest", "blocked_by_policy", "self_only", "approval_required",
    "tool_error", "bad_request", "cancelled", "rate_limited", "upstream_error", "not_granted",
    "capability_disabled", "capability_removed", "transform_error", "consent_required", "user_changed"})
TEST_RESULTS = ("ok", "empty", "needs_approval", "failed", "account_mismatch", "not_run")

#: The read-only test tool per category and provider (spec §4), from the page
#: maps. A page test may name only one of these.
READ_TOOLS: Dict[str, Dict[str, str]] = {
    "email": {"superhuman": "list_threads", "gmail": "search_threads", "outlook": "outlook_email_search"},
    "calendar": {"google_calendar": "list_events", "outlook_calendar": "outlook_calendar_search",
                 "superhuman": "get_availability"},
    "personal_calendar": {"google_calendar": "list_events", "outlook_calendar": "outlook_calendar_search"},
    "chat": {"slack": "slack_search_public_and_private", "ms365_teams": "chat_message_search"},
}

#: Who a connector is signed in as (spec §5). `verified` False: the answer's
#: key is as described, not yet seen on a seat; such an identity answers
#: "cannot_check" until a seat's own test records it (the 151e pattern).
IDENTITY: Dict[str, Dict[str, Any]] = {
    "superhuman": {"tool": "list_accounts", "key": "accounts[].email", "verified": False},
    "outlook": {"tool": "get_me", "key": "mail", "verified": False},
    "outlook_calendar": {"tool": "get_me", "key": "mail", "verified": False},
    "google_calendar": {"tool": "list_calendars", "key": "items[primary=true].id", "verified": False},
    "gmail": {"tool": "search_threads", "key": "threads[0].messages[0].from", "verified": False},
}


def provider_of(tool_ids: Iterable[str], category: str) -> Optional[str]:
    """The manifest provider these tool ids fingerprint as, among the
    providers that serve `category` only (a mail-and-calendar connector is a
    candidate for both, never for files). None when none matches."""
    try:
        from connector_adapters import capabilities as cap
        rows = cap.providers()
        ranked = cap.match_fingerprint(list(tool_ids))
    except Exception:  # noqa: BLE001 - no manifest, no provider
        return None
    want = "calendar" if category == "personal_calendar" else category
    for name, _hits in ranked:
        row = rows.get(name) or {}
        cats = set(row.get("categories") or []) | ({row.get("category")} if row.get("category") else set())
        if want in cats:
            return name
    return None


def shape_of(payload: Any, category: str, _depth: int = 0) -> Dict[str, Any]:
    """The shape of an answer under the closed grammar (spec §4)."""
    keys = SHAPE_KEYS.get(category, frozenset())
    if _depth >= SHAPE_MAX_DEPTH:
        return {"t": "null"}
    if isinstance(payload, bool):
        return {"t": "bool"}
    if isinstance(payload, (int, float)):
        return {"t": "num"}
    if isinstance(payload, str):
        return {"t": "str"}
    if payload is None:
        return {"t": "null"}
    if isinstance(payload, list):
        item: Optional[Dict[str, Any]] = None
        for x in payload[:SHAPE_ITEMS]:
            item = _merge(item, shape_of(x, category, _depth + 1))
        out: Dict[str, Any] = {"t": "arr", "n": min(len(payload), SHAPE_MAX_N)}
        if item is not None:
            out["i"] = item
        return out
    if isinstance(payload, dict):
        kept = {}
        other = 0
        for k, v in payload.items():
            if isinstance(k, str) and k in keys:
                kept[k] = shape_of(v, category, _depth + 1)
            else:
                other += 1
        return {"t": "obj", "k": kept, "other": min(other, SHAPE_MAX_OTHER)}
    return {"t": "null"}


def _merge(a: Optional[Dict[str, Any]], b: Dict[str, Any]) -> Dict[str, Any]:
    if a is None:
        return b
    if a.get("t") != b.get("t"):
        return a
    if a["t"] == "obj":
        k = dict(a.get("k") or {})
        for key, v in (b.get("k") or {}).items():
            k[key] = _merge(k.get(key), v)
        return {"t": "obj", "k": k, "other": min(max(a.get("other", 0), b.get("other", 0)), SHAPE_MAX_OTHER)}
    if a["t"] == "arr":
        out = {"t": "arr", "n": max(a.get("n", 0), b.get("n", 0))}
        if a.get("i") or b.get("i"):
            out["i"] = _merge(a.get("i"), b.get("i") or a.get("i"))
        return out
    return a


def valid_shape(shape: Any, category: str, _depth: int = 0) -> bool:
    """True only when `shape` is inside the closed grammar for `category`:
    a known type, capped counts, only listed keys, nothing else."""
    if _depth > SHAPE_MAX_DEPTH or not isinstance(shape, dict):
        return False
    t = shape.get("t")
    if t not in SHAPE_TYPES:
        return False
    if t in ("str", "num", "bool", "null"):
        return set(shape) == {"t"}
    if t == "arr":
        if not set(shape) <= {"t", "n", "i"}:
            return False
        n = shape.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= SHAPE_MAX_N:
            return False
        return "i" not in shape or valid_shape(shape["i"], category, _depth + 1)
    if not set(shape) <= {"t", "k", "other"}:
        return False
    other = shape.get("other", 0)
    if not isinstance(other, int) or isinstance(other, bool) or not 0 <= other <= SHAPE_MAX_OTHER:
        return False
    k = shape.get("k", {})
    if not isinstance(k, dict):
        return False
    keys = SHAPE_KEYS.get(category, frozenset())
    return all(isinstance(name, str) and name in keys and valid_shape(v, category, _depth + 1)
               for name, v in k.items())


def shape_sha(shape: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(shape, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def valid_test_entry(entry: Any, category: str, provider: Optional[str]) -> bool:
    """The server's check of one page-written test result (spec §3): only
    known fields, a known result, a known error code or none, a tool from
    this category's read map, identifier-shaped argument names, a shape
    inside the grammar. A tool error's text never passes."""
    allowed = {"at", "artifact", "result", "error_code", "tool", "args_keys", "args_schema_sha",
               "manifest_sha", "shape", "shape_sha"}
    if not isinstance(entry, dict) or not set(entry) <= allowed:
        return False
    if entry.get("at") is not None and not (isinstance(entry["at"], str) and ISO_RE.match(entry["at"])):
        return False
    if entry.get("artifact") is not None and not (isinstance(entry["artifact"], str) and ARTIFACT_RE.match(entry["artifact"])):
        return False
    if entry.get("result") not in TEST_RESULTS:
        return False
    code = entry.get("error_code")
    if code is not None and code not in MCP_ERROR_CODES:
        return False
    tool = entry.get("tool")
    if tool is not None and tool != (READ_TOOLS.get(category) or {}).get(str(provider or "")):
        return False
    keys = entry.get("args_keys", [])
    if not isinstance(keys, list) or len(keys) > ARGS_KEYS_MAX \
            or not all(isinstance(x, str) and _ARG_KEY_RE.fullmatch(x) for x in keys):
        return False
    for h in ("args_schema_sha", "manifest_sha", "shape_sha"):
        v = entry.get(h)
        if v is not None and not (isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)):
            return False
    if "shape" in entry and not valid_shape(entry["shape"], category):
        return False
    return True


def job_block_problem(block: Any, category: str) -> Optional[str]:
    """None when `block` is a job block `connector_config.set_connector_job`
    may store; else what is wrong. Only listed keys, each in its own shape:
    no address (an `@` anywhere in a name), no shape value, no error text."""
    import connector_config as cc
    allowed = {"state", "origin", "provider_key", "display_name", "via", "accounts", "approval",
               "test", "set_at", "set_by", "guessed", "rev"}
    if not isinstance(block, dict):
        return "not a mapping"
    extra = set(block) - allowed
    if extra:
        return "unknown keys " + ",".join(sorted(map(str, extra)))
    if block.get("state") not in cc.JOB_STATES or block.get("origin") not in cc.JOB_ORIGINS:
        return "a known state and origin"
    try:
        from connector_adapters import capabilities as cap
        known = set(cap.providers())
    except Exception:  # noqa: BLE001
        known = set()
    for key in ("provider_key", "via"):
        v = block.get(key)
        if v is not None and v not in known:
            return f"{key} is not a manifest provider"
    name = block.get("display_name")
    if name is not None and not (isinstance(name, str) and _NAME_RE.match(name) and not _ADDRESSY.search(name)):
        return "display_name is a connector's plain name only"
    for entry in block.get("accounts") or []:
        if not isinstance(entry, dict) or not set(entry) <= {"account_id", "address_h", "role", "confirmed_at"}:
            return "an account entry holds account_id, address_h, role and confirmed_at only"
        if not ACCOUNT_ID_RE.match(str(entry.get("account_id") or "")):
            return "account_id"
        if entry.get("address_h") is not None and not HASH_RE.match(str(entry["address_h"])):
            return "address_h"
        if entry.get("role") is not None and entry["role"] not in (cc.CONNECTOR_ROLES + ("personal",)):
            return "role"
        if entry.get("confirmed_at") is not None and not ISO_RE.match(str(entry["confirmed_at"])):
            return "confirmed_at"
    if not isinstance(block.get("accounts", []), list):
        return "accounts"
    appr = block.get("approval")
    if appr is not None:
        if not isinstance(appr, dict) or not set(appr) <= {"page", "seen_at", "artifact"}:
            return "approval holds page, seen_at and artifact only"
        if appr.get("page") not in PAGE_STATES:
            return "approval.page"
        if appr.get("seen_at") is not None and not ISO_RE.match(str(appr["seen_at"])):
            return "approval.seen_at"
        art = appr.get("artifact")
        if art not in (None, "") and not ARTIFACT_RE.match(str(art)):
            return "approval.artifact"
    test = block.get("test")
    if test is not None:
        if not isinstance(test, dict):
            return "test"
        passed = test.get("passed_at")
        if passed is not None and not ISO_RE.match(str(passed)):
            return "test.passed_at"
        if not valid_test_entry({k: v for k, v in test.items() if k != "passed_at"}, category,
                                block.get("provider_key")):
            return "test is outside the grammar"
    for key in ("set_at",):
        if block.get(key) is not None and not ISO_RE.match(str(block[key])):
            return key
    sb = block.get("set_by")
    if sb is not None and not (isinstance(sb, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", sb)
                               and not _ADDRESSY.search(sb)):
        return "set_by"
    if block.get("guessed") is not None and not isinstance(block["guessed"], bool):
        return "guessed"
    rev = block.get("rev")
    if rev is not None and not (isinstance(rev, int) and not isinstance(rev, bool) and rev >= 0):
        return "rev"
    return None


def identity_match(provider: str, answered_address: Optional[str], accounts_h: Iterable[str]) -> str:
    """`match`, `mismatch` or `cannot_check`: the connector's own answer about
    who it is signed in as, compared by the wall's hash of the folded bare
    address (`hub_live.digest`) with the job's confirmed accounts. Built for
    the check before each work write; not wired in slice 0."""
    ident = IDENTITY.get(str(provider or ""))
    if not ident or not ident.get("verified") or not answered_address:
        return "cannot_check"
    try:
        import hub_live
        h = hub_live.digest(answered_address)
    except Exception:  # noqa: BLE001
        return "cannot_check"
    return "match" if h in set(accounts_h or ()) else "mismatch"


__all__ = ["SHAPE_TYPES", "SHAPE_KEYS", "MCP_ERROR_CODES", "TEST_RESULTS", "READ_TOOLS", "IDENTITY",
           "ISO_RE", "ARTIFACT_RE", "ACCOUNT_ID_RE", "HASH_RE", "REASON_RE", "PAGE_STATES",
           "provider_of", "shape_of", "valid_shape", "shape_sha", "valid_test_entry", "job_block_problem",
           "identity_match"]
