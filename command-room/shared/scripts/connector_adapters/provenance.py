#!/usr/bin/env python3
"""
Provenance normalizer (Layer A4) — ONE place that owns the
`gmail:` / `gcal:` / `slack:` / `granola:` prefix logic, the new
`{connector, provider, native_id, account_id}` shape, and the CANONICAL DEDUP
KEY stable across old + new formats.

WHY THIS IS PHASE 1, NOT PHASE 2 (MF-5 / R16): dedup + reconcile + the
self-closure guard key on the LITERAL provenance string today —
`(gmail:<id>, title)` in inbox-triage, `gmail:<message_id>` in the BUG-3719
self-closure guard, `slack:<permalink>` in slack_capture. A post-migration
re-observation of the same message MUST reduce to the same key or it
double-tracks commitments and the self-closure guard silently breaks. So the
canonical key ships with the mechanism, before any skill is de-hardcoded.

CANONICAL KEY = `provider:native_id`, lowercased, absorbing the documented
drift spellings already on disk (R16):
  - legacy `gmail:<id>`  ≡  new `{provider:"gmail", native_id:"<id>"}`  → `gmail:<id>`
  - `email_sent` channel `data.gmail_message_id` / `gmail_thread_id`        → `gmail:<id>`
  - both Slack spellings  `slack:<permalink>` and `slack:<team>/<chan>/<ts>` → `slack:<chan>:<ts>`
  - bare meeting ids (meeting_capture historical) via default_provider="granola"

READ BACK-COMPAT (never a history rewrite): legacy rows carry no `account_id`;
readers treat a missing `account_id` as IN scope (ACCOUNT_SCOPE §4b). The
normalizer resolves `account_id` for NEW writes from the account map (R3).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# The legacy single-token provenance prefixes this module absorbs. New writes
# emit the structured provenance dict; readers still see these forever.
LEGACY_PREFIXES = ("gmail", "gcal", "slack", "granola", "drive", "outlook",
                   "gcalendar", "session")

# Slack permalink: …/archives/<C-channel>/p<digits>  (ts is the digits, dotless)
_SLACK_PERMALINK_RE = re.compile(r"/archives/([A-Za-z0-9]+)/p(\d+)")
# Slack triple form: slack:<team>/<channel>/<ts-with-dot>
_SLACK_TRIPLE_RE = re.compile(r"^([^/]+)/([^/]+)/([\d.]+)$")


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _canon_slack(rest: str) -> str:
    """Reduce either Slack provenance spelling to `slack:<channel>:<ts-digits>`.
    Falls back to `slack:<normalized rest>` when neither shape parses."""
    r = (rest or "").strip()
    m = _SLACK_PERMALINK_RE.search(r)
    if m:
        return f"slack:{m.group(1).lower()}:{m.group(2)}"
    m = _SLACK_TRIPLE_RE.match(r)
    if m:
        ts_digits = m.group(3).replace(".", "")
        return f"slack:{m.group(2).lower()}:{ts_digits}"
    return "slack:" + _norm(r)


def _canon_from_source_ref(source_ref: str, default_provider: Optional[str]) -> Optional[str]:
    s = (source_ref or "").strip()
    if not s:
        return None
    if ":" in s:
        provider, rest = s.split(":", 1)
        provider = provider.strip().lower()
        rest = rest.strip()
        if provider == "slack":
            return _canon_slack(rest)
        # gcalendar → gcal normalization (both spellings seen)
        if provider == "gcalendar":
            provider = "gcal"
        return f"{provider}:{_norm(rest)}"
    # No prefix — a bare native id. Only a caller that KNOWS the provider
    # (e.g. the meeting reader, historical granola drift) may unify it.
    if default_provider:
        return f"{default_provider.strip().lower()}:{_norm(s)}"
    return _norm(s)


def canonical_dedup_key(source_ref: Optional[str] = None, *,
                        provider: Optional[str] = None,
                        native_id: Optional[str] = None,
                        event: Optional[dict] = None,
                        default_provider: Optional[str] = None) -> Optional[str]:
    """The stable dedup key. Priority: explicit provider+native_id → event dict
    → source_ref string. None when nothing resolves.

    The invariant every caller relies on: two spellings of the SAME artifact
    (legacy string, structured provenance, email_sent id-field channel) return
    the SAME key. This is what preserves the BUG-3719 self-closure guard across
    old/new formats (R16) and stops post-migration double-capture."""
    if provider and native_id:
        return f"{_norm(provider)}:{_norm(native_id)}"

    if isinstance(event, dict):
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        # 1. structured provenance object
        prov = data.get("provenance") if isinstance(data.get("provenance"), dict) else None
        if prov and prov.get("provider") and prov.get("native_id"):
            return f"{_norm(prov['provider'])}:{_norm(prov['native_id'])}"
        # 2. explicit source_ref (data or top-level)
        sref = data.get("source_ref") or event.get("source_ref")
        if isinstance(sref, str) and sref.strip():
            dp = default_provider
            if dp is None and event.get("type") in ("meeting", "meeting_processed", "meeting_scheduled"):
                dp = "granola"
            return _canon_from_source_ref(sref, dp)
        # 3. email_sent channel — gmail id fields (R16)
        mid = data.get("gmail_message_id")
        if isinstance(mid, str) and mid.strip():
            return f"gmail:{_norm(mid)}"
        tid = data.get("gmail_thread_id")
        if isinstance(tid, str) and tid.strip():
            return f"gmail:{_norm(tid)}"
        return None

    if source_ref:
        return _canon_from_source_ref(source_ref, default_provider)
    return None


def normalize_provenance(*, server_id: Optional[str] = None, provider: Optional[str] = None,
                         native_id: Optional[str] = None, address: Optional[str] = None,
                         account_id: Optional[str] = None,
                         workspace_root=None) -> Dict[str, Any]:
    """Build the NEW structured provenance dict
    `{connector, provider, native_id, account_id}` (A4). `connector` is the
    server-id (rotates on reconnect — Rule 22); `account_id` is the stable
    address-keyed id (R3) that survives rotation for scope + reply routing.
    Resolves account_id from the account map when only an address is known."""
    if account_id is None and (address or workspace_root):
        account_id = resolve_account_id(address=address, workspace_root=workspace_root)
    prov: Dict[str, Any] = {
        "connector": server_id,
        "provider": (provider or "").lower() or None,
        "native_id": native_id,
        "account_id": account_id,
    }
    return {k: v for k, v in prov.items() if v is not None}


def build_email_sent_provenance(*, message_id: Optional[str] = None,
                                thread_id: Optional[str] = None,
                                provider: Optional[str] = None,
                                server_id: Optional[str] = None,
                                address: Optional[str] = None,
                                account_id: Optional[str] = None,
                                workspace_root=None) -> Dict[str, Any]:
    """The `email_sent` payload fragment — DUAL-WRITE, additive (Phase-2
    call-site wiring, gate 5).

    Returns data fields for the send-confirmation event:
      - `gmail_message_id` / `gmail_thread_id` — the LEGACY id channel, kept
        for reader back-compat FOREVER (email_outcomes, reconcile-sent thread
        fetch, voice-corrections matching all read these today). Written for
        every provider — the field NAME is legacy, the value is the declared
        backend's native id.
      - `provenance` — the structured `{connector, provider, native_id,
        account_id}` shape (R3), plus `thread_native_id` when known, so new
        readers resolve identity + account scope without the legacy names.

    Skills call THIS instead of naming the legacy fields (grep-gate 1): the
    provider-token spelling lives here, in the adapter layer, on the
    allow-list."""
    out: Dict[str, Any] = {}
    mid = (message_id or "").strip()
    tid = (thread_id or "").strip()
    if mid:
        out["gmail_message_id"] = mid
    if tid:
        out["gmail_thread_id"] = tid
    p = normalize_provenance(server_id=server_id, provider=provider,
                             native_id=mid or None, address=address,
                             account_id=account_id,
                             workspace_root=workspace_root)
    if tid:
        p["thread_native_id"] = tid
    if p:
        out["provenance"] = p
    return out


def build_email_drafted_provenance(*, draft_id: Optional[str] = None,
                                   provider: Optional[str] = None,
                                   server_id: Optional[str] = None,
                                   address: Optional[str] = None,
                                   account_id: Optional[str] = None,
                                   workspace_root=None) -> Dict[str, Any]:
    """The `email_drafted` payload fragment — DUAL-WRITE, additive (EW2+T,
    F-12: the Superhuman cutover of the click-time substrate writes).

    Returns data fields for the draft-created event:
      - `gmail_draft_id` — the LEGACY id channel, kept for reader back-compat
        FOREVER (voice_corrections' snapshot store and drafted-but-not-sent
        detection read it today). Written for every provider — the field
        NAME is legacy, the VALUE is the declared backend's native draft id
        (a Superhuman draft id lands here exactly as a Gmail one does).
      - `provenance` — the structured `{connector, provider, native_id,
        account_id}` shape (R3), so new readers resolve identity + account
        scope without the legacy name.

    Skills call THIS instead of hand-writing `gmail_draft_id` (grep-gate 1):
    the provider-token spelling lives here, in the adapter layer, on the
    allow-list. Mirrors `build_email_sent_provenance` so the drafted/sent
    pair carries one identity model."""
    out: Dict[str, Any] = {}
    did = (draft_id or "").strip()
    if did:
        out["gmail_draft_id"] = did
    p = normalize_provenance(server_id=server_id, provider=provider,
                             native_id=did or None, address=address,
                             account_id=account_id,
                             workspace_root=workspace_root)
    if p:
        out["provenance"] = p
    return out


def resolve_account_id(*, event: Optional[dict] = None, address: Optional[str] = None,
                       provenance: Optional[dict] = None, workspace_root=None) -> Optional[str]:
    """Resolve the stable account_id for a read. Order: an explicit account_id
    on the provenance → the account map keyed by address → a derived id from the
    address (R3, deterministic). None when no address is known (legacy rows —
    readers treat that as in-scope, ACCOUNT_SCOPE §4b)."""
    if provenance and provenance.get("account_id"):
        return provenance["account_id"]
    if event and isinstance(event.get("data"), dict):
        p = event["data"].get("provenance")
        if isinstance(p, dict) and p.get("account_id"):
            return p["account_id"]
        address = address or event["data"].get("account_address") or event["data"].get("from")
    if not address:
        return None
    try:
        from connector_config import account_for_address, derive_account_id
    except ImportError:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from connector_config import account_for_address, derive_account_id
    rec = account_for_address(address, workspace_root) if workspace_root else None
    if rec and rec.get("account_id"):
        return rec["account_id"]
    return derive_account_id(address)


__all__ = [
    "LEGACY_PREFIXES",
    "build_email_drafted_provenance",
    "build_email_sent_provenance",
    "canonical_dedup_key",
    "normalize_provenance",
    "resolve_account_id",
]
