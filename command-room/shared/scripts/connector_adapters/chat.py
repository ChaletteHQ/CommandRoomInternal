#!/usr/bin/env python3
"""
The CHAT seam (SPEC CHATSCAN1 §2A) — one adapter, two providers.

WHY A SEAM AND NOT A SLACK MODULE PLUS A TEAMS MODULE
-----------------------------------------------------
The ruling that produced this build was "no chat-backend-specific integration;
native connectors only". Concretely that means no skill anywhere may contain
the sentence "if the backend is Teams, do X" — a skill asks THE DECLARED CHAT
BACKEND for a thing, and the differences between the two live here, in the
capability manifest and in this module. That is the same posture
`connector_adapters/mail.py` holds for mail, and it is what lets a client
workspace declare a backend we never tested a skill against and still get a
correct, honest degrade instead of a wrong branch.

HELPER-OR-DEGRADE, PER CAPABILITY
---------------------------------
Every question a consumer asks has three answers, never two: yes, no-and-here-
is-why, and no-backend-at-all. The third one is the important one. A workspace
that declares no chat backend must skip every chat leg SILENTLY — no error, no
mention to the user, nothing that reads as a broken feature — and still leave a
RECEIPT saying the leg was skipped and why. A silent skip with no receipt is
indistinguishable from a leg that ran and found nothing, which is the dead-rail
shape the whole receipt contract exists to prevent. `skip_receipt` is that
receipt, and it is deliberately the same shape a real run returns so callers
need no second branch.

THE TEAMS DEGRADE IS SILENT AT THE CONNECTOR, SO IT IS LOUD HERE
----------------------------------------------------------------
Probed 2026-08-06: asking the Microsoft 365 connector for a DATE-FILTERED chat
search does not fail and does not warn. It stops searching and walks the newest
50 chats reading 50 messages each, channels excluded, matching literal
substrings. A window-based sweep — which is exactly what the closure leg is —
therefore NEVER runs as a search on that backend. `plan_scan` returns that fact
up front, with the caps and a plain-language coverage note, so the caller
records a partial sweep as partial. A surface that claims a full reconcile it
did not run is worse than one that admits a gap.

ID SHAPES DIFFER AND BOTH ARE FIRST-CLASS
-----------------------------------------
Slack addresses a message as (channel, ts); Teams addresses it as (chat or
channel id, message id). A pointer schema that assumed either one would make
half the fleet's closes unauditable, so `build_chat_source_ref` carries
`provider` AND `kind` explicitly and `read_chat_source_ref` accepts both — the
event-id-shape-survey gotcha, applied at write time instead of discovered at
read time.

stdlib only. No network. No connector ids anywhere in this module: the seam
resolves a DECLARED backend by category, and tool ids live in
`tool_discovery`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from connector_adapters import capabilities as _caps  # noqa: E402

CATEGORY = "chat"

# Provider tags — the manifest's row keys. Named so callers can pin a fixture
# without spelling a literal, NEVER so a consumer can branch on them.
PROVIDER_SLACK = "slack"
PROVIDER_TEAMS = "ms365_teams"

# Scan modes a window sweep can run in.
SCAN_MODE_SEARCH = "search"              # one date-filtered query per fire
SCAN_MODE_PER_CHAT = "per_chat_scan"     # the Teams degrade: walk N chats
SCAN_MODE_NONE = "none"                  # no backend / no readable capability

# Reference kinds. Slack and Teams both use `channel`; the rest are one-sided.
KIND_CHANNEL = "channel"
KIND_THREAD = "thread"     # Slack only
KIND_DM = "dm"             # Slack only
KIND_CHAT = "chat"         # Teams only
VALID_REF_KINDS = (KIND_CHANNEL, KIND_THREAD, KIND_DM, KIND_CHAT)

# The four fields SPEC CHATSCAN1 §B requires on every chat-evidenced close,
# plus `kind` (§5 trap: the schema carries provider AND kind explicitly, or a
# reader cannot tell which id shape it is holding).
REQUIRED_REF_FIELDS = ("provider", "kind", "chat_or_channel_id", "message_id", "ts")

_TS_DOTS = re.compile(r"[.\s]")


class ChatPointerError(ValueError):
    """A chat-evidenced write had no usable pointer back to the message.

    Loud on purpose. "No pointer, no close" is the whole reason this class
    exists: an unauditable close is a row the user cannot check, and the
    unauditable-close hole must not board a third channel after mail and
    meetings.
    """


# ---------------------------------------------------------------------------
# Declaration + capabilities
# ---------------------------------------------------------------------------

def declared_chat_backend(workspace_root=None, entities: Optional[dict] = None
                          ) -> Optional[Dict[str, Any]]:
    """The workspace's declared chat backend row (`{server_id, provider,
    label}`), or None when no chat backend is declared. Never raises — an
    unreadable config is "not declared", which degrades to a silent skip
    rather than an exception inside somebody's 6:45 AM fire."""
    try:
        from connector_config import declared_backend
    except ImportError:  # pragma: no cover — path shim for odd callers
        sys.path.insert(0, str(_HERE.parent))
        from connector_config import declared_backend
    try:
        return declared_backend(CATEGORY, workspace_root, entities)
    except Exception:
        return None


def resolve_chat_provider(workspace_root=None, provider: Optional[str] = None,
                          entities: Optional[dict] = None) -> Optional[str]:
    """Which provider a chat artifact is attributed to. An explicit argument
    wins (the caller ran discovery and knows); otherwise the declared backend
    answers; otherwise None.

    None is NOT a default provider. The mail seam learned this the hard way:
    `provider or "gmail"` mislabelled every ref on a non-Gmail backend and,
    where it sat on an identity comparison, silently disabled the guard it was
    part of. There is no legacy chat anchor to fall back to and none is
    invented here — an unresolved chat provider means the leg does not run."""
    if provider and str(provider).strip():
        return str(provider).strip().lower()
    row = declared_chat_backend(workspace_root, entities) or {}
    p = row.get("provider")
    return str(p).strip().lower() if p and str(p).strip() else None


def chat_capabilities(provider: Optional[str],
                      detected: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Effective chat capabilities for `provider`, with a per-workspace
    detected row overriding the manifest defaults key-by-key.

    A provider with no row, OR a KNOWN provider whose row belongs to another
    CATEGORY, falls to the chat baseline (every key false). The category test
    is the load-bearing half: `capabilities_for`'s own category argument only
    routes UNKNOWN providers, so a known row won unconditionally and a mail
    provider answered `supports(p, "send")` TRUE — a mail row's send
    capability answering a chat question. `send` is a name both vocabularies
    already use, which is exactly the silent crossing this seam exists to
    prevent; the review measured it on `superhuman`. Latent rather than live
    (every mail row plans `SCAN_MODE_NONE`, so no leg runs), and closed here
    before a second shared key makes it live."""
    row = _caps.provider_row(provider) or {}
    if row and row.get("category") != CATEGORY:
        base = dict((_caps.load_manifest().get("baseline") or {}).get(CATEGORY) or {})
        if detected:
            base.update(detected)
        return base
    return _caps.capabilities_for(provider, detected, CATEGORY)


def supports(provider: Optional[str], capability: str,
             detected: Optional[Dict[str, Any]] = None) -> bool:
    """Fail-closed capability test. Absent key = False. Routed through
    `chat_capabilities` so the category check above applies here too — this is
    the function callers actually use, and a fence the main callsite bypasses
    is a fence that does not exist."""
    val = chat_capabilities(provider, detected).get(capability)
    if isinstance(val, bool):
        return val
    if isinstance(val, (list, tuple, dict)):
        return bool(val)
    return bool(val) and val not in ("", "false", "none")


def probed(provider: Optional[str]) -> Dict[str, Any]:
    """The provider's measured limits (`probed` block). `{}` when it declares
    none — a provider with no probed block is a provider nobody has measured,
    which the caller should treat as "assume nothing", not "assume fine"."""
    return _caps.probed_facts(provider)


def provider_label(provider: Optional[str]) -> str:
    """The manifest's own human label for a provider, for receipts and
    diagnostics. Falls back to the raw tag so an unknown provider still names
    itself. Not for customer prose — surfaces say "your chat" or nothing."""
    row = _caps.provider_row(provider) or {}
    return str(row.get("label") or (provider or "")).strip()


# ---------------------------------------------------------------------------
# Helper-or-degrade
# ---------------------------------------------------------------------------

def capability_check(provider: Optional[str], capability: str, *,
                     purpose: str,
                     detected: Optional[Dict[str, Any]] = None
                     ) -> Tuple[bool, Optional[str]]:
    """`(ok, degrade_reason)` for one capability on one provider.

    The reason is diagnostics-grade English naming the PURPOSE that could not
    be served, not the key that was false — a receipt that says
    `channel_list=false` tells a future reader nothing about what the user
    lost. Returns `(True, None)` when the capability is present."""
    if not provider:
        return False, f"no chat backend is declared, so {purpose} did not run"
    if supports(provider, capability, detected):
        return True, None
    return False, (f"the declared chat backend cannot {purpose} — that "
                   f"capability is absent on this connector")


def plan_scan(provider: Optional[str], *, date_filtered: bool = True,
              detected: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """How a WINDOWED sweep will actually run on this backend, decided before
    a single call is made.

    Returns:
      {"mode": search|per_chat_scan|none,
       "degraded": bool,               # the connector will not do what we asked
       "covers_channels": bool,
       "covers_dms": bool,
       "limits": {...},                # the probed caps that bound this mode
       "coverage_note": str|None,      # plain language, for the receipt
       "reason": str|None}             # why mode is `none`

    `date_filtered=True` is the closure leg's real question ("give me the
    messages since my cursor"). On a backend whose `date_filtered_search` is
    false this returns the per-chat-scan mode WITH its caps, because that is
    what the connector silently does — planning for a search there would have
    the leg believe it swept everything it asked for."""
    if not provider:
        return {"mode": SCAN_MODE_NONE, "degraded": False,
                "covers_channels": False, "covers_dms": False, "limits": {},
                "coverage_note": None,
                "reason": "no chat backend is declared"}

    caps = chat_capabilities(provider, detected)
    facts = probed(provider)

    can_search = bool(caps.get("channel_search")) or bool(caps.get("chat_resource_read"))
    if not can_search:
        return {"mode": SCAN_MODE_NONE, "degraded": True,
                "covers_channels": False, "covers_dms": False, "limits": {},
                "coverage_note": None,
                "reason": ("the declared chat backend exposes no readable "
                           "message surface")}

    if date_filtered and not caps.get("date_filtered_search", False):
        chat_cap = facts.get("per_chat_scan_chat_cap")
        per_chat = facts.get("per_chat_scan_messages_per_chat")
        covers_channels = bool(facts.get("per_chat_scan_covers_channels", False))
        bits = []
        if chat_cap and per_chat:
            bits.append(f"the newest {chat_cap} conversations, "
                        f"{per_chat} messages each")
        if not covers_channels:
            bits.append("channels were not covered")
        if facts.get("search_match_is_literal_substring"):
            bits.append("matching was literal text, not a smart search")
        note = ("This backend cannot filter chat by date, so the window was "
                "swept the only way it allows: "
                + ("; ".join(bits) if bits else "a partial per-conversation scan")
                + ". Anything outside that is not covered by this pass.")
        return {"mode": SCAN_MODE_PER_CHAT, "degraded": True,
                "covers_channels": covers_channels, "covers_dms": True,
                "limits": {k: facts[k] for k in
                           ("per_chat_scan_chat_cap",
                            "per_chat_scan_messages_per_chat",
                            "page_size") if k in facts},
                "coverage_note": note, "reason": None}

    return {"mode": SCAN_MODE_SEARCH, "degraded": False,
            "covers_channels": bool(caps.get("channel_search")),
            "covers_dms": True,
            "limits": {k: facts[k] for k in ("page_size",) if k in facts},
            "coverage_note": None, "reason": None}


# ---------------------------------------------------------------------------
# The pointer (`source_ref`) — both id shapes, always
# ---------------------------------------------------------------------------

def _norm(v) -> str:
    return str(v or "").strip()


def parse_iso(value) -> Optional["_dt.datetime"]:
    """Parse a connector timestamp, or None when it is not a real ISO-8601
    instant. Tolerant of the spellings connectors actually emit: a trailing
    `Z`, an explicit offset, fractional seconds, and a bare date.

    This exists because PRESENCE is not FORMAT. `build_chat_source_ref` used
    to check that `ts` was non-empty, so `"not-a-date"` built a valid-looking
    pointer and was then persisted as the chat cursor — and because the
    cursor's advance test is a raw string comparison, every later ISO
    timestamp sorted BELOW the poisoned value (`"2" < "n"`) and the cursor
    never advanced again. One malformed message from one connector wedged the
    leg permanently. A timestamp that cannot be parsed is not a timestamp."""
    import datetime as _dt
    s = _norm(value)
    if not s:
        return None
    candidate = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    try:
        return _dt.datetime.fromisoformat(candidate)
    except (TypeError, ValueError):
        return None


def is_iso(value) -> bool:
    """True iff `value` is a parseable ISO-8601 instant."""
    return parse_iso(value) is not None


def canonical_iso(value) -> Optional[str]:
    """`value` re-spelled in ONE canonical ISO-8601 form, or None.

    NORMALIZE, DON'T JUST VALIDATE (review FR-1). `is_iso` answers whether a
    string is PARSEABLE; the chat cursor's advance test is a raw string
    comparison, which needs the stronger property that every stored timestamp
    has exactly one SPELLING. Python 3.11+ `datetime.fromisoformat` accepts
    basic-format ISO — `"20260708T213523"`, `"20260708"`, week dates like
    `"2026-W28-3"` — and every one of those sorts ABOVE every extended-format
    timestamp (`"2" > "-"`), which reproduces the exact F-2 wedge one layer
    down: the cursor lands on a spelling nothing later can beat, and the leg
    never advances again. Measured end to end before this fix: a basic-format
    ts left `n_dropped_bad_ts=0`, persisted as-is, and a later real message
    returned `cursor_advanced=False`.

    Canonicalizing also settles the two lesser spellings `is_iso` admits — a
    trailing `Z` (which `datetime.isoformat` writes as `+00:00`) and a bare
    date — so the comparison is sound across every form a connector emits.

    Returns None for anything unparseable: this NEVER fabricates a time, and
    callers keep their own "no pointer, no close" refusal.
    """
    parsed = parse_iso(value)
    return parsed.isoformat() if parsed is not None else None


def normalize_message_id(provider: Optional[str], message_id) -> str:
    """The message id in the form the dedup key uses. Slack's id IS its epoch
    timestamp and the same message is spelled `1720476923.000200` in history
    and `p1720476923000200` in a permalink, so the dots come out — which is
    exactly what `provenance._canon_slack` already does for the capture leg's
    refs. Every other provider's id is opaque and is left alone."""
    mid = _norm(message_id)
    if not mid:
        return ""
    if (provider or "").strip().lower() == PROVIDER_SLACK:
        return _TS_DOTS.sub("", mid.lstrip("pP") if mid[:1] in "pP" else mid)
    return mid


def default_ref_kind(provider: Optional[str], *, has_thread: bool = False,
                     is_dm: bool = False) -> str:
    """Which reference kind a message is, when the connector did not say.

    Structure answers first — a message with a thread parent is a thread reply
    on any backend that has threads, and a direct conversation is a DM — and
    only the fallback consults the provider, because the broadest container a
    backend can express genuinely differs (Slack's is a channel, Graph's is a
    chat). That last line is the ONLY provider-keyed decision in the chat
    lane, and it lives HERE rather than in a leg on purpose: a leg that
    branches on a product name is the thing this whole seam exists to
    prevent."""
    p = (provider or "").strip().lower()
    if has_thread and supports(p, "thread_read"):
        return KIND_THREAD
    if is_dm and KIND_DM in (chat_capabilities(p).get("ref_kinds") or ()):
        return KIND_DM
    kinds = chat_capabilities(p).get("ref_kinds") or ()
    return kinds[0] if kinds else KIND_CHANNEL


def iso_from_native_id(provider: Optional[str], native_id) -> str:
    """An ISO timestamp derived from the message id, when the backend's id IS
    a time (Slack's is an epoch). `""` for every other backend — an opaque id
    is not a clock, and a GUESSED timestamp would silently defeat the
    stale-evidence fence, which refuses to close a promise with a message that
    predates it."""
    if (provider or "").strip().lower() != PROVIDER_SLACK:
        return ""
    import datetime as _dt
    try:
        return _dt.datetime.fromtimestamp(
            float(native_id), _dt.timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def native_ref_id(ref: Optional[dict]) -> Optional[str]:
    """`<chat_or_channel_id>:<normalized message id>` — the NATIVE half of the
    key, with the provider stripped off.

    This exists because the shared identity helpers take `(provider,
    native_id)` and build the prefixed key themselves
    (`provenance.primary_artifact_key`). Handing them a value that already
    carried its provider would produce `slack:slack:<…>` and match nothing —
    the same class of silent identity break the mail seam hit with
    `provider or "gmail"`. So the closure leg passes THIS to the matcher and
    `chat_ref_key` to anything that wants a whole key."""
    if not isinstance(ref, dict):
        return None
    provider = _norm(ref.get("provider")).lower()
    room = _norm(ref.get("chat_or_channel_id")).lower()
    mid = normalize_message_id(provider, ref.get("message_id")).lower()
    if not (room and mid):
        return None
    return f"{room}:{mid}"


def chat_ref_key(ref: Optional[dict]) -> Optional[str]:
    """`provider:<chat_or_channel_id>:<normalized message id>` — the canonical
    dedup key for a chat message, in the same three-segment shape
    `provenance._canon_slack` already emits for Slack captures, so a message
    reached by the capture leg and by the closure leg reduces to ONE key.
    None when the ref is incomplete."""
    if not isinstance(ref, dict):
        return None
    provider = _norm(ref.get("provider")).lower()
    room = _norm(ref.get("chat_or_channel_id")).lower()
    mid = normalize_message_id(provider, ref.get("message_id")).lower()
    if not (provider and room and mid):
        return None
    return f"{provider}:{room}:{mid}"


def build_chat_source_ref(*, provider: Optional[str], kind: str,
                          chat_or_channel_id, message_id, ts,
                          permalink: Optional[str] = None) -> Dict[str, Any]:
    """The structured pointer every chat-evidenced write carries.

    Raises ChatPointerError when any required field is missing — the refusal
    is at BUILD time so a caller cannot assemble a half-pointer and discover
    at read time that its close is unauditable. `permalink` is optional and
    additive: Slack gives one, Graph does not, and a click-through the user can
    open is worth carrying when it exists."""
    ref = {
        "provider": _norm(provider).lower(),
        "kind": _norm(kind).lower(),
        "chat_or_channel_id": _norm(chat_or_channel_id),
        "message_id": _norm(message_id),
        "ts": _norm(ts),
    }
    missing = [f for f in REQUIRED_REF_FIELDS if not ref.get(f)]
    if missing:
        raise ChatPointerError(
            "a chat-evidenced write needs a complete pointer back to the "
            f"message; missing {', '.join(missing)}"
        )
    canonical = canonical_iso(ref["ts"])
    if canonical is None:
        # PRESENCE was never enough. An unparseable `ts` is refused here, on
        # the same "no pointer, no close" rule as a missing field: it is not a
        # time, it cannot order evidence against a promise, and downstream it
        # poisons the cursor it gets written into.
        raise ChatPointerError(
            f"the chat message's timestamp {ref['ts']!r} is not a real "
            "ISO-8601 instant — a pointer that cannot be placed in time is "
            "not a pointer"
        )
    # FORMAT was not enough either (review FR-1). The pointer's `ts` is stored
    # in ONE spelling, so a basic-format connector value can never sort above
    # an extended-format one anywhere downstream.
    ref["ts"] = canonical
    if ref["kind"] not in VALID_REF_KINDS:
        raise ChatPointerError(
            f"unknown chat reference kind {ref['kind']!r} — the pointer must "
            f"say which id shape it holds (one of {', '.join(VALID_REF_KINDS)})"
        )
    if permalink and _norm(permalink):
        ref["permalink"] = _norm(permalink)
    return ref


def read_chat_source_ref(obj: Optional[dict]) -> Optional[Dict[str, Any]]:
    """Read the structured chat pointer off an event, an event's `data`, or a
    bare ref dict. Accepts BOTH id shapes and both field homes
    (`data.chat_source_ref`, and a `data.source_ref` that is itself a dict —
    a shape a future writer may legitimately produce). Returns None when there
    is no structured pointer; never raises."""
    if not isinstance(obj, dict):
        return None
    candidates = []
    data = obj.get("data") if isinstance(obj.get("data"), dict) else None
    for holder in (data, obj):
        if not isinstance(holder, dict):
            continue
        for key in ("chat_source_ref", "source_ref"):
            v = holder.get(key)
            if isinstance(v, dict):
                candidates.append(v)
    if isinstance(obj.get("provider"), str) and isinstance(
            obj.get("chat_or_channel_id"), str):
        candidates.append(obj)
    for c in candidates:
        if all(_norm(c.get(f)) for f in REQUIRED_REF_FIELDS):
            return dict(c)
    return None


def missing_pointer_reason(obj: Optional[dict]) -> Optional[str]:
    """None when `obj` carries a complete chat pointer; otherwise the reason a
    chat-evidenced close must be REFUSED. The read side of the same rule
    `build_chat_source_ref` enforces at write time — kept as a separate
    predicate so a reader (integrity check, review) can apply it to history it
    did not write."""
    if not isinstance(obj, dict):
        return "no event to check"
    ref = read_chat_source_ref(obj)
    if ref is None:
        data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
        held = data.get("chat_source_ref") or data.get("source_ref")
        if isinstance(held, dict):
            missing = [f for f in REQUIRED_REF_FIELDS if not _norm(held.get(f))]
            return ("the pointer back to the chat message is incomplete: "
                    f"missing {', '.join(missing)}")
        return "no pointer back to the chat message"
    return None


def source_ref_string(ref: Optional[dict]) -> Optional[str]:
    """The STRING form written to `data.source_ref`, so every reader that has
    always seen a string there — the dedup index, `canonical_dedup_key`, the
    capture leg's `already_captured` — keeps working unchanged. The structured
    dict rides alongside at `data.chat_source_ref`; the two are two spellings
    of one pointer and `chat_ref_key` is the bridge."""
    return chat_ref_key(ref)


def pointer_fields(ref: Optional[dict]) -> Dict[str, Any]:
    """The `data` fragment a writer merges in: the string ref every existing
    reader understands PLUS the structured ref this build introduces. One
    call, so no writer can produce one half without the other."""
    key = source_ref_string(ref)
    if not key or not isinstance(ref, dict):
        raise ChatPointerError(
            "a chat-evidenced write needs a complete pointer back to the "
            "message before it can be written")
    return {"source_ref": key, "chat_source_ref": dict(ref)}


# ---------------------------------------------------------------------------
# The silent-but-receipted skip
# ---------------------------------------------------------------------------

def skip_receipt(reason: str, *, provider: Optional[str] = None,
                 leg: str = "") -> Dict[str, Any]:
    """The receipt block for a chat leg that did not run.

    Same keys a real run reports, all zeroed, plus `status: "skipped"` and the
    reason — so a caller folds it into its own receipt with no branch, and a
    reader can tell "no chat backend" apart from "swept and found nothing".
    Those two states are the ones that must never look alike."""
    return {
        "ran": False,
        "status": "skipped",
        "leg": leg or CATEGORY,
        "skip_reason": reason,
        "provider": provider,
        "scan_mode": SCAN_MODE_NONE,
        "degraded": False,
        "coverage_note": None,
        "n_scanned": 0,
    }


__all__ = [
    "CATEGORY",
    "PROVIDER_SLACK", "PROVIDER_TEAMS",
    "SCAN_MODE_SEARCH", "SCAN_MODE_PER_CHAT", "SCAN_MODE_NONE",
    "KIND_CHANNEL", "KIND_THREAD", "KIND_DM", "KIND_CHAT", "VALID_REF_KINDS",
    "REQUIRED_REF_FIELDS",
    "ChatPointerError",
    "declared_chat_backend", "resolve_chat_provider",
    "chat_capabilities", "supports", "probed", "provider_label",
    "capability_check", "plan_scan",
    "parse_iso", "is_iso", "canonical_iso",
    "normalize_message_id", "default_ref_kind", "iso_from_native_id",
    "native_ref_id", "chat_ref_key",
    "build_chat_source_ref",
    "read_chat_source_ref", "missing_pointer_reason", "source_ref_string",
    "pointer_fields", "skip_receipt",
]
