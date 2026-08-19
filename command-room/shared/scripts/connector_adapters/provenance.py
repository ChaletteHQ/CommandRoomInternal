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

TWO CONTRACTS, ONE DERIVATION (SPEC PROV2, 2026-08-16). The canonical key
above answers "is this the same artifact?" and stays lowercased forever. It is
NOT the same thing as the resolvable POINTER a close stores — that answers
"what do I hand back to the connector to open this?", and a lowercased answer
is simply wrong wherever native ids are case-sensitive. So the module carries
two spellings of one pointer and exactly one direction between them:

  - `canonical_dedup_key(...)`  → IDENTITY. Lowercased. Unchanged.
  - `canonical_source_ref(...)` → POINTER. The provider half is normalized
    (lowercase, `gcalendar`→`gcal`, Slack reduction unchanged in structure);
    the NATIVE half keeps its case VERBATIM, for EVERY provider — a uniform
    rule, not a per-provider table somebody has to remember to extend.
  - `dedup_key_of(stored_ref)`  → the derivation, one way. A pointer folds to
    its identity key; nothing ever uppercases back. A legacy lowercased row
    folds to itself, so pre- and post-PROV2 rows for one artifact land on ONE
    identity with no migration, no backfill, no history rewrite.

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


# ---------------------------------------------------------------------------
# PROV2 — the POINTER form: case-preserving in the native half
# ---------------------------------------------------------------------------
#
# MEASURED (REVIEW_PROV1 F-1, 2026-08-16): the lowercased dedup key was being
# reused as the stored, resolvable pointer. `outlook:AAMkAGI2TG93AAA=` landed
# on disk as `outlook:aamkagi2tg93aaa=` and `drive:1AbCdEf…` as
# `drive:1abcdef…`. Both providers sit in LEGACY_PREFIXES, both native-id forms
# are case-sensitive, so the stored pointer resolved to nothing — permanently,
# because provenance is saved at write time or it is gone. Nothing went red:
# gmail / granola / `session:` ids are case-insensitive in practice, so a
# Gmail-backed workspace shows no damage at all.
#
# The functions below are the POINTER twins of the identity functions above.
# They make the same SHAPE decisions (provider normalization, the Slack
# reduction, the `default_provider` prefix) and differ in exactly one thing:
# the native half is trimmed, never folded.


def _keep(s) -> str:
    """The native half of a POINTER: whitespace trimmed, case VERBATIM.

    `_norm`'s deliberate twin. The whole PROV2 split is these two spellings of
    the same trim — one that folds because it is building an identity, one that
    does not because it is building something a connector has to resolve."""
    return (s or "").strip()


def _pointer_slack(rest: str) -> str:
    """`_canon_slack`'s pointer twin: the SAME two reductions (a permalink and
    the `<team>/<channel>/<ts>` triple both collapse to `<channel>:<ts-digits>`)
    with the channel id's case preserved. The reduction is STRUCTURAL — two
    spellings of one message — and is unchanged here; only the fold moves."""
    r = (rest or "").strip()
    m = _SLACK_PERMALINK_RE.search(r)
    if m:
        return f"slack:{m.group(1)}:{m.group(2)}"
    m = _SLACK_TRIPLE_RE.match(r)
    if m:
        ts_digits = m.group(3).replace(".", "")
        return f"slack:{m.group(2)}:{ts_digits}"
    return "slack:" + _keep(r)


def _pointer_from_source_ref(source_ref: str,
                             default_provider: Optional[str]) -> Optional[str]:
    """`_canon_from_source_ref`'s pointer twin — identical shape decisions,
    native half unfolded."""
    s = (source_ref or "").strip()
    if not s:
        return None
    if ":" in s:
        provider, rest = s.split(":", 1)
        provider = provider.strip().lower()
        rest = rest.strip()
        if provider == "slack":
            return _pointer_slack(rest)
        # gcalendar → gcal normalization (both spellings seen) — a PROVIDER
        # vocabulary fact, so it survives the split untouched.
        if provider == "gcalendar":
            provider = "gcal"
        return f"{provider}:{_keep(rest)}"
    if default_provider:
        return f"{default_provider.strip().lower()}:{_keep(s)}"
    return _keep(s)


def _pointer_from_ref_dict(ref: Dict[str, Any]) -> Optional[str]:
    """`_canon_from_ref_dict`'s pointer twin.

    The chat adapter's `normalize_message_id` is ALREADY case-preserving (its
    own `_norm` only trims; `chat_ref_key` lowercases afterwards), so the
    message-id reduction is borrowed rather than re-derived here — one
    reduction, two case postures, and no second place for Slack's dotless-ts
    rule to drift."""
    provider = _norm(ref.get("provider"))
    if not provider:
        return None
    room = _keep(ref.get("chat_or_channel_id"))
    message_id = _keep(ref.get("message_id"))
    if room and message_id:
        try:
            from connector_adapters.chat import normalize_message_id
        except Exception:  # pragma: no cover — direct-path / partial install
            normalize_message_id = None
        mid = (normalize_message_id(provider, message_id)
               if normalize_message_id is not None else message_id)
        return f"{provider}:{room}:{mid}" if mid else None
    native_id = _keep(ref.get("native_id")) or message_id
    if native_id:
        return f"{provider}:{native_id}"
    return None


def dedup_key_of(stored_ref: Any = None, *,
                 default_provider: Optional[str] = None) -> Optional[str]:
    """The IDENTITY form of a pointer that is ALREADY STORED (SPEC PROV2 §2.3).

    ONE DIRECTION. The pointer is the richer form and the dedup key is always
    computable from it; nothing ever "uppercases back". A legacy lowercased row
    case-folds to itself and a post-PROV2 case-preserved row folds onto the same
    key, so one artifact keeps one identity across the boundary — which is why
    PROV2 needs no migration, no backfill and no history rewrite.

    TOTAL AND SILENT, deliberately. `canonical_source_ref` REFUSES malformed
    input because it runs at WRITE time, before anything lands, where a garbage
    ref is worse than none. This runs on rows that are already history: a
    reader that raises on a bad row cannot even measure it. Anything unusable
    returns None.

    EVERY identity comparison of stored `source_ref` strings routes through
    here (guard G30). A raw `==` between two stored refs is wrong the moment
    the substrate spans the PROV2 boundary, and wrong silently — no exception,
    no red, just a fence that quietly stops firing."""
    if stored_ref is None:
        return None
    if isinstance(stored_ref, dict):
        try:
            return _canon_from_ref_dict(stored_ref)
        except Exception:  # pragma: no cover — a stored row is never an input
            return None
    if not isinstance(stored_ref, str):
        return None
    try:
        return _canon_from_source_ref(stored_ref, default_provider)
    except Exception:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# PROV1 — the close-family source pointer
# ---------------------------------------------------------------------------

# The data key stamped on a close whose writer had no pointer to give. Absence
# of provenance must be VISIBLE: a close with neither `source_ref` nor this
# marker would be indistinguishable from a legacy row, and "we cannot tell"
# is the state PROV1 exists to end. Named here (not inlined at the writers) so
# the marker has one spelling and one home.
PROVENANCE_MISSING_KEY = "provenance_missing"

# The close-family data key that holds the canonical pointer.
SOURCE_REF_KEY = "source_ref"

# SPEC PROVMINT1 — the GRAIN of a stored pointer, and the ONE value it takes.
#
# PROV1 made the pointer optional at the caller and honest when absent. The
# 2026-08-17 walk measured what that produces in practice: on the human rails
# the marker had become the normal outcome, because the pointer was something
# PROSE asked a caller to supply and prose is the layer that flattens. So the
# writers mint a surface receipt (`session:<surface>:<instant>`) when nothing
# reaches them — and stamp this key, because a mint that is indistinguishable
# from a caller-passed artifact pointer would silently inflate
# `closure_index.pointer_coverage`, the exact metric PROV1 exists to produce.
#
# PRESENCE IS THE SIGNAL. The key is written ONLY on a minted ref; a
# caller-passed pointer carries no grain key at all, and neither does any row
# written before this spec. That asymmetry is deliberate — it means no backfill
# exists and no historic row is silently reclassified: absent = caller-passed,
# exactly as every reader already treated it.
REF_GRAIN_KEY = "ref_grain"
REF_GRAIN_SURFACE_MINTED = "surface_minted"


class SourceRefError(ValueError):
    """A source pointer that cannot be canonicalized.

    Raised at WRITE time, before anything lands. The rule is narrow on
    purpose: an ABSENT pointer never blocks a close (it stamps
    `provenance_missing`), but a MALFORMED one is refused loudly rather than
    stored — a garbage ref in `data.source_ref` is worse than no ref, because
    every reader downstream treats that key as resolvable evidence."""


def canonical_source_ref(source_ref: Any = None, *,
                         default_provider: Optional[str] = None) -> Optional[str]:
    """Canonicalize ANY accepted source-pointer spelling to the stored POINTER.

    SPEC PROV2: the provider half is normalized (lowercase, `gcalendar` →
    `gcal`, both Slack spellings still reducing to one shape) and the NATIVE
    half keeps its case VERBATIM, for every provider. That is what makes the
    stored ref RESOLVABLE — an Outlook immutable id and a Drive file id are
    case-sensitive, so the lowercased form this used to return pointed at
    nothing. `dedup_key_of()` derives the identity key from whatever this
    returns; identity semantics did not move.

    The API is FROZEN (PROV1) — signature, acceptances and refusals are exactly
    as they were. Only the canonicalization inside moved.

    Accepts:
      - a prefixed string (`gmail:<id>`, `granola:<meeting>`, `session:<id>`,
        either Slack spelling, `gcalendar:` → `gcal:`) → the canonical pointer;
      - a structured chat pointer dict (`{provider, chat_or_channel_id,
        message_id, …}`) or a `{provider, native_id}` dict → the same pointer
        the string form of that pointer reduces to;
      - None / blank → None (the caller stamps `provenance_missing`).

    Refuses (SourceRefError):
      - a non-string, non-dict value — an int seq or a list is not a pointer;
      - a dict that names no resolvable artifact;
      - a string with an empty provider or an empty native half (`":"`,
        `"gmail:"`, `":abc"`) — a half-pointer resolves to nothing;
      - a bare token with no provider and no `default_provider` — a pointer
        that does not say WHICH system it points into cannot be followed back.
        Callers that genuinely know the provider pass `default_provider`.
    """
    if source_ref is None:
        return None
    if isinstance(source_ref, bool) or isinstance(source_ref, (int, float)):
        raise SourceRefError(
            f"source_ref {source_ref!r} is not a pointer — a bare number names "
            "no artifact. Pass a `provider:native_id` string (or None, which "
            f"lands the close with {PROVENANCE_MISSING_KEY!r})."
        )
    if isinstance(source_ref, dict):
        key = _pointer_from_ref_dict(source_ref)
        if not key:
            raise SourceRefError(
                "source_ref dict names no resolvable artifact — a structured "
                "pointer needs provider + (chat_or_channel_id + message_id) or "
                f"provider + native_id; got keys {sorted(source_ref)!r}"
            )
        return key
    if not isinstance(source_ref, str):
        raise SourceRefError(
            f"source_ref must be a string pointer or a structured pointer dict, "
            f"got {type(source_ref).__name__}"
        )
    raw = source_ref.strip()
    if not raw:
        return None
    if ":" not in raw and not default_provider:
        raise SourceRefError(
            f"source_ref {raw!r} carries no provider prefix — a pointer that "
            "does not say which system it points into cannot be followed "
            f"back. Use one of {', '.join(LEGACY_PREFIXES)} (e.g. "
            f"'gmail:{raw}'), or pass default_provider when the caller knows "
            "the provider."
        )
    key = _pointer_from_source_ref(raw, default_provider)
    if not key:
        raise SourceRefError(f"source_ref {source_ref!r} canonicalized to nothing")
    provider_half, _, native_half = key.partition(":")
    if not provider_half.strip() or not native_half.strip():
        raise SourceRefError(
            f"source_ref {source_ref!r} is a half-pointer (canonicalizes to "
            f"{key!r}) — both the provider and the native id are required"
        )
    return key


def _canon_from_ref_dict(ref: Dict[str, Any]) -> Optional[str]:
    """The canonical key for a structured pointer dict. Chat pointers reduce to
    the same three-segment key `chat.chat_ref_key` builds, so a message reached
    by the capture leg and by the closure leg is ONE key."""
    provider = _norm(ref.get("provider"))
    if not provider:
        return None
    room = _norm(ref.get("chat_or_channel_id"))
    message_id = _norm(ref.get("message_id"))
    if room and message_id:
        # Delegate to the chat adapter so the closure leg and the capture leg
        # produce ONE key for one message (its `normalize_message_id` strips
        # Slack's ts dots exactly as `_canon_slack` does). The local spelling
        # below is the fallback for a caller that reaches this module without
        # the chat adapter importable.
        try:
            from connector_adapters.chat import chat_ref_key as _chat_ref_key
        except Exception:  # pragma: no cover — direct-path / partial install
            _chat_ref_key = None
        if _chat_ref_key is not None:
            key = _chat_ref_key(ref)
            if key:
                return key
        return f"{provider}:{room}:{message_id}"
    native_id = _norm(ref.get("native_id")) or message_id
    if native_id:
        return f"{provider}:{native_id}"
    return None


def close_provenance_fields(source_ref: Any = None, *,
                            default_provider: Optional[str] = None) -> Dict[str, Any]:
    """The `data` fragment EVERY close-family writer merges in (PROV1).

    Exactly one of three shapes, never two and never none (PROVMINT1 added the
    middle one; this function still returns only the outer two, because minting
    is a WRITER decision and this is the shape layer):
      - `{"source_ref": "<canonical pointer>"}` — a caller-passed pointer,
        canonicalized here so no reader downstream has to re-normalize a raw
        spelling. PROV2: the native half's case is preserved, so readers hand
        this to a resolver BYTE-IDENTICAL and identity comes from
        `dedup_key_of`, never from a raw string comparison;
      - `{"source_ref": "session:<surface>:<instant>", "ref_grain":
        "surface_minted"}` — the module-side FLOOR (PROVMINT1): nothing reached
        the writer, so it minted a receipt for the act itself and said so. Built
        by `commitment_state._minted_pointer_fields`, which composes this
        function's first shape with `REF_GRAIN_KEY`;
      - `{"provenance_missing": True}` — an honest "this close cites nothing".
        Still reachable from writers OUTSIDE PROVMINT1's three (the merge and
        thread-resolve writers), and from any legacy caller of this function.

    THE CONTRACT IS UNCHANGED for callers: signature, acceptances and refusals
    are exactly as PROV1 froze them. Never blocks a close — a human/chat close
    with no message id passes its `session:` receipt, and a machine close with
    nothing in hand still lands. Only a MALFORMED pointer raises
    (SourceRefError)."""
    key = canonical_source_ref(source_ref, default_provider=default_provider)
    if key is None:
        return {PROVENANCE_MISSING_KEY: True}
    return {SOURCE_REF_KEY: key}


def has_source_pointer(event_or_data: Optional[dict]) -> bool:
    """True when a close-family event (or its `data`) carries a usable pointer.
    The read side of `close_provenance_fields`, used by the coverage metric and
    by any reader that must tell a pointered close from a marked one. Legacy
    rows carry neither key and read False — absent, never an error."""
    if not isinstance(event_or_data, dict):
        return False
    data = event_or_data.get("data")
    data = data if isinstance(data, dict) else event_or_data
    ref = data.get(SOURCE_REF_KEY)
    if isinstance(ref, str) and ref.strip():
        return True
    if isinstance(ref, dict) and _canon_from_ref_dict(ref):
        return True
    return False


def is_surface_minted(event_or_data: Optional[dict]) -> bool:
    """True when a close-family event's pointer was MINTED by its writer rather
    than passed by its caller (SPEC PROVMINT1).

    The read side of `REF_GRAIN_KEY`, and the reason the coverage metric can
    split "points at an email, meeting or message" from "points at the moment
    someone closed it" instead of reporting one flattering number. Absence of
    the key means caller-passed — which is what every pre-PROVMINT1 row is, and
    why no backfill exists."""
    if not isinstance(event_or_data, dict):
        return False
    data = event_or_data.get("data")
    data = data if isinstance(data, dict) else event_or_data
    return data.get(REF_GRAIN_KEY) == REF_GRAIN_SURFACE_MINTED


# The provenance prefix every pre-connector-agnostic mail row on disk carries.
# It is a BACK-COMPAT ANCHOR, not a default: a workspace whose mail backend is
# declared always attributes its own provider. Named rather than inlined so
# `provider or "gmail"` can never read as a considered choice again — that
# spelling was a silent mislabel on every non-Gmail backend, and where it sat
# on an IDENTITY comparison it silently disabled the guard it was part of
# (MAILSEAM items 4/5).
LEGACY_MAIL_PROVIDER = "gmail"


def resolve_mail_provider(workspace_root=None, provider: Optional[str] = None) -> Optional[str]:
    """Which provider a mail artifact should be attributed to.

    Order: (1) the provider the caller resolved through discovery — the
    declared backend's provider tag / `DiscoveryResult.platform`; (2) the
    workspace's DECLARED email backend, read here so a caller that forgot to
    pass one still attributes honestly instead of falling to a literal;
    (3) None — genuinely unknown, and the caller decides whether that is a
    back-compat write (`LEGACY_MAIL_PROVIDER`) or a refusal. Never raises."""
    if provider and str(provider).strip():
        return str(provider).strip().lower()
    if workspace_root is None:
        return None
    try:
        try:
            from connector_config import declared_backend
        except ImportError:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from connector_config import declared_backend
        row = declared_backend("email", workspace_root)
    except Exception:
        return None
    p = (row or {}).get("provider")
    return str(p).strip().lower() if p and str(p).strip() else None


def is_same_artifact(candidate_key: Optional[str],
                     provider: Optional[str] = None,
                     native_id: Optional[str] = None) -> bool:
    """Does `candidate_key` (a canonical dedup key read off an event) name the
    same mail artifact as `provider` + `native_id`?

    Comparing ONE constructed key is only correct while a workspace has always
    written under one provider label, and that is not the world. Rows written
    before the backend was declared carry the legacy `gmail:` anchor; rows
    written after carry the real provider — the same message, two keys. And a
    PURE matcher (`reconcile_sent`) often has no provider at all, because the
    caller that could resolve one is a layer up.

    So:
      - provider RESOLVED → exact match against that provider's key OR the
        legacy anchor's, covering both sides of a backend switch.
      - provider UNRESOLVED → the provider half is not evidence, so only the
        native ids are compared. This is deliberately the OVER-matching
        direction, and `provider or "gmail"` picked the under-matching side:
        on a Superhuman backend it built `gmail:<id>` against rows stored as
        `superhuman:<id>`, matched nothing at all, and turned the guard off
        without failing anything.

    WHICH WAY OVER-MATCHING FAILS, PER CONSUMER (review F-1 — the first cut of
    this docstring claimed over-matching "can only ever decline to close",
    which is true of one consumer and false of the other; both are named here
    so the tie-break is a judgment rather than an oversight):

      - `reconcile_sent_commitments.reconcile_sent` / the REPLYCLOSE inbound
        twin — over-match EXCLUDES a commitment from this message's close
        candidates. Fail-SAFE: at worst a real completion waits for the next
        fire, and matching is idempotent, so nothing is lost.
      - `sent_capture.already_captured` — over-match returns True and
        SUPPRESSES a capture. Fail-UNSAFE in the opposite direction: a promise
        that is never OPENED, which is the BUG-3719 class the sent-capture
        rail exists to prevent. Sharpest sub-case: for `commitment_resolved` /
        `thread_resolved` events there is no title gate, so identity alone
        returns True immediately.

    The unresolved branch is still the right default, because the *known*
    failure it replaced was total (a guard matching nothing on any non-Gmail
    backend) while this one needs a native-id collision across providers to
    bite. But it is a trade, not a free lunch, and a caller that cannot
    tolerate a suppressed capture should resolve a provider before asking.

    False when there is no native id: the caller leaves the guard off rather
    than guess an identity."""
    nid = _norm(native_id)
    key = _norm(candidate_key)
    if not nid or not key:
        return False
    if provider and str(provider).strip():
        return key in (f"{_norm(str(provider))}:{nid}",
                       f"{_norm(LEGACY_MAIL_PROVIDER)}:{nid}")
    # Unresolved: compare the native half. A key with no prefix compares whole;
    # a multi-segment key (`slack:<chan>:<ts>`) keeps its remaining segments, so
    # it never collapses onto a bare id.
    return key == nid or key.split(":", 1)[-1] == nid


def primary_artifact_key(provider: Optional[str] = None,
                         native_id: Optional[str] = None) -> Optional[str]:
    """The ONE key to hand a downstream API that compares a single ref.

    The resolved provider's key when there is one; the legacy anchor when
    there is not — which is exactly what the writer side produces in that same
    state, so an unresolved provider degrades the comparison instead of
    killing it. `artifact_keys` is the richer SET form for callers that can
    compare against more than one."""
    nid = _norm(native_id)
    if not nid:
        return None
    return f"{_norm(provider) if provider else _norm(LEGACY_MAIL_PROVIDER)}:{nid}"


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
    """The `email_drafted` payload fragment (EW2+T, F-12; FB-plumbing item 5).

    Returns data fields for the draft-created event:
      - `native_draft_id` — the declared backend's native draft id, written
        for EVERY provider (a Superhuman draft id lands here exactly as a Gmail
        one does). This replaces the old, misleadingly Gmail-flavored
        `gmail_draft_id` field NAME — the value was never Gmail-specific, so the
        name lied on every non-Gmail backend. WRITERS EMIT THE NEW NAME;
        READERS ACCEPT BOTH via `native_draft_id_from_data` (legacy events on
        disk keep their `gmail_draft_id` and read forever — back-compat lives
        read-side, never a history rewrite).
      - `provenance` — the structured `{connector, provider, native_id,
        account_id}` shape (R3), so new readers resolve identity + account
        scope without the id-name channel at all.

    Skills call THIS instead of hand-writing the field (grep-gate 1): the id
    spelling lives here, in the adapter layer. Mirrors
    `build_email_sent_provenance` so the drafted/sent pair carries one identity
    model."""
    out: Dict[str, Any] = {}
    did = (draft_id or "").strip()
    if did:
        out["native_draft_id"] = did
    p = normalize_provenance(server_id=server_id, provider=provider,
                             native_id=did or None, address=address,
                             account_id=account_id,
                             workspace_root=workspace_root)
    if p:
        out["provenance"] = p
    return out


def native_draft_id_from_data(data: Optional[dict]) -> Optional[str]:
    """Read the native draft id from an `email_drafted` event's `data`, accepting
    BOTH the new `native_draft_id` and the legacy `gmail_draft_id` spelling
    (FB-plumbing item 5 — reader back-compat forever; append-only history never
    gets rewritten). New name wins when both are somehow present. Returns None
    when neither is set."""
    if not isinstance(data, dict):
        return None
    v = data.get("native_draft_id")
    if isinstance(v, str) and v.strip():
        return v
    legacy = data.get("gmail_draft_id")
    if isinstance(legacy, str) and legacy.strip():
        return legacy
    return None


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
    "LEGACY_MAIL_PROVIDER",
    "PROVENANCE_MISSING_KEY",
    "REF_GRAIN_KEY",
    "REF_GRAIN_SURFACE_MINTED",
    "SOURCE_REF_KEY",
    "SourceRefError",
    "is_same_artifact",
    "is_surface_minted",
    "build_email_drafted_provenance",
    "build_email_sent_provenance",
    "canonical_dedup_key",
    "canonical_source_ref",
    "close_provenance_fields",
    "dedup_key_of",
    "has_source_pointer",
    "native_draft_id_from_data",
    "normalize_provenance",
    "primary_artifact_key",
    "resolve_account_id",
    "resolve_mail_provider",
]
