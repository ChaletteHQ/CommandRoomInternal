#!/usr/bin/env python3
"""
Connector-agnostic config — declared backends + compound account map (Layer A/B).

The single reader (and the workspace-manager-owned setter) for the two new
`entities.json` → `workspace.*` blocks introduced by the connector-agnostic
build:

  - `workspace.connectors`  — the DECLARED backend per category, keyed by
      MCP server-id (Layer A1). Shape:
        {"email": {"server_id": "ec5e0bd5-…", "provider": "superhuman", "label": "Superhuman"},
         "calendar": {"server_id": "f9119bb5-…", "provider": "google_calendar", ...}, …}
      Also holds `workspace.connectors._zapier_server_ids: [ …server ids… ]`
      so the Zapier dispatch leg is pinned by id, not sniffed by name (R12/H-H).

  - `workspace.accounts`  — the compound account × bindings map (Layer B1).
      An array of account records; see shared/ACCOUNT_SCOPE.md §1 for the shape.

Ownership (WORKSPACE_API map): **workspace-manager owns both blocks.**
command-room-onboarding and command-room-update-bridge write ONLY through the
setter functions here (declared delegates), never a direct entities.json write.

FAIL-CLOSED / BACK-COMPAT SEMANTICS (shared/ACCOUNT_SCOPE.md §4c, R4):
  - Empty/absent account map  → everything is IN scope (today's behavior; a
    live client mid-upgrade must not regress).
  - Account map present, address/account_id NOT found  → treated as in-scope
    for the WRITE dial (back-compat; a read whose account can't be resolved
    predates the wall). Fail-closed-on-NEW is enforced at the scan/surfacing
    layer (an unclassified account is not scanned — state machine §7a), not by
    rejecting already-flowing writes here.
  - Account found + `write_to_business` off  → OUT of scope (the wall rejects).

Read-only helpers never raise (a broken/edge workspace degrades to in-scope,
= today's behavior). The setter uses the canonical locked entities.json writer.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ENTITIES_REL = Path("_hq") / "data" / "entities.json"

# tool_id shape: mcp__<server-id>__<operation>. The server-id segment can
# itself contain single underscores (rare) but never the "__" separator.
_TOOL_ID_RE = re.compile(r"^mcp__(?P<server>.+?)__(?P<op>.+)$")

# A server-id minted by the old connector runtime: eight hex characters, alone
# or heading a hyphenated UUID. Those ids ROTATE on reconnect, which is the
# whole reason a binding has to be re-pinned at all. A display-name server-id
# ("Superhuman_Mail") does not rotate, so a binding pinned to one is stable —
# and a UUID binding left behind beside it is a stale route that outbound
# addressing can still pick up (DISC1).
_UUID_SHAPED_RE = re.compile(r"^[0-9a-f]{8}(-|$)", re.IGNORECASE)

# `binding_verified` — HOW the address-to-connector binding was established.
#   user_asserted      the customer said so, or it was implied by a connector
#                      with no way to name its own account (native Gmail, H-A)
#   connector_asserted the address came back FROM the connector — Superhuman
#                      `list_accounts`, Granola `get_account_info` — so it is
#                      the connector's own answer, not a guess (DISC1, §0.31)
BINDING_USER_ASSERTED = "user_asserted"
BINDING_CONNECTOR_ASSERTED = "connector_asserted"
BINDING_VERIFIED_VALUES = (BINDING_USER_ASSERTED, BINDING_CONNECTOR_ASSERTED)


def is_uuid_shaped_server_id(server_id: Optional[str]) -> bool:
    """True when a server-id has the old rotating UUID shape."""
    return bool(_UUID_SHAPED_RE.match((server_id or "").strip()))

# Role → default two-dial posture when a classified account leaves the dials
# unset. business-* accounts file + surface; personal is walled by default;
# mixed surfaces (liberal) but files only by association (write dial off here —
# the promote-queue turns it on per-sender, ACCOUNT_SCOPE §8); functional roles
# are business-scoped. Unclassified is fail-closed on both.
_ROLE_DEFAULT_DIALS = {
    "business-primary": {"surface": True, "write_to_business": True},
    "business-secondary": {"surface": True, "write_to_business": True},
    "mixed": {"surface": True, "write_to_business": False},
    "personal": {"surface": False, "write_to_business": False},
    "shared-support": {"surface": True, "write_to_business": True},
    "billing": {"surface": True, "write_to_business": True},
    "cold-outreach": {"surface": True, "write_to_business": True},
    "unclassified": {"surface": False, "write_to_business": False},
}


# ---------------------------------------------------------------------------
# Low-level: load + locate the workspace.* block (tolerates both shapes)
# ---------------------------------------------------------------------------

def _entities_path(workspace_root) -> Path:
    return Path(workspace_root) / _ENTITIES_REL


def load_entities(workspace_root) -> Dict[str, Any]:
    """Read entities.json. Returns {} on any failure (never raises)."""
    try:
        return json.loads(_entities_path(workspace_root).read_text(encoding="utf-8"))
    except Exception:
        return {}


def workspace_block(workspace_root=None, entities: Optional[dict] = None) -> Dict[str, Any]:
    """Return the `workspace` sub-dict, honoring the nested/flat entities shape.
    Never raises; returns {} when absent."""
    ent = entities if isinstance(entities, dict) else load_entities(workspace_root)
    if not isinstance(ent, dict):
        return {}
    # TZFIX v5.9.4 (same class as tz.py's load_workspace_tz): MERGE the two
    # shapes rather than taking the first TRUTHY block. A workspace can carry
    # both, and an inner block created for one key used to shadow a top-level
    # block that still held `connectors` / `accounts` — silently emptying the
    # declared-backend map instead of falling through to the block that has it.
    # INNER wins on conflict here (the opposite of tz.py, deliberately): the
    # writer for these keys is `_workspace_container` below, which maintains the
    # inner block whenever `entities.entities` exists.
    inner = ent.get("entities") if isinstance(ent.get("entities"), dict) else None
    inner_ws = (inner or {}).get("workspace")
    top_ws = ent.get("workspace")
    return {
        **(top_ws if isinstance(top_ws, dict) else {}),
        **(inner_ws if isinstance(inner_ws, dict) else {}),
    }


# ---------------------------------------------------------------------------
# tool_id / server-id utilities (server-id-first resolution keystone)
# ---------------------------------------------------------------------------

def server_id_of(tool_id: Optional[str]) -> Optional[str]:
    """Extract the MCP server-id from a fully-qualified tool id
    (`mcp__<server>__<op>` → `<server>`). None if unparseable."""
    if not tool_id:
        return None
    m = _TOOL_ID_RE.match(tool_id.strip())
    return m.group("server") if m else None


# ---------------------------------------------------------------------------
# Declared backends (Layer A1)
# ---------------------------------------------------------------------------

def declared_backends(workspace_root=None, entities: Optional[dict] = None) -> Dict[str, Any]:
    """The full `workspace.connectors` map. {} when unset (empty-map = fallback
    to substring discovery, R4)."""
    ws = workspace_block(workspace_root, entities)
    conn = ws.get("connectors")
    return conn if isinstance(conn, dict) else {}


def declared_backend(category: str, workspace_root=None,
                     entities: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """The declared backend row for a category ({server_id, provider, label}),
    or None when undeclared (caller falls back to substring discovery).

    IDENT1 I-1 (R-RW2-1; coordinator decision D-3): when a workspace root is
    given, and the ledger has recorded at least one alias for the category,
    the row also carries `server_ids` - the declared id FIRST, then every
    alias (`declared_backend_ids`); with no alias the row is today's. The
    `server_id` key is unchanged for every reader. A
    call with no workspace root cannot read the ledger and answers the
    entities row alone, exactly as before."""
    row = declared_backends(workspace_root, entities).get(category)
    if not (isinstance(row, dict) and row.get("server_id")):
        return None
    if workspace_root is None:
        return row
    aliases = [a for a in recorded_aliases(category, workspace_root)
               if a != row["server_id"]]
    if not aliases:
        # No alias recorded: the row is today's, byte for byte, on every seat
        # (fix pass 1, M-5 - a legacy seat's declared row never moves).
        return row
    out = dict(row)
    out["server_ids"] = [row["server_id"]] + aliases
    return out


#: The two ledger rows the alias fold reads (D-3: a recorded alias is a
#: LEDGER row, never an `entities.json` rewrite from a fire).
CONNECTOR_DETECTED_EVENT = "connector_detected"
CONNECTOR_BACKEND_CHANGED_EVENT = "connector_backend_changed"


def recorded_aliases(category: str, workspace_root) -> List[str]:
    """Every server id a silent fire RECORDED for `category` since the
    category was last re-declared, oldest first.

    THE WALK (2026-09-22, HOLD driver 1). One Superhuman connector, two
    registries: the interactive seat names it `Superhuman_Mail`, the legacy
    scheduled sandbox by a UUID. The declaration could match only one, so the
    fire's mail leg went dark by naming. M ruled the HYBRID (R-RW2-1): a
    silent fire that finds EXACTLY ONE server fingerprinting to the declared
    provider uses it and records the id, so the next fire matches exactly and
    never guesses again. The record is a `connector_detected` row with
    `recorded_alias: true` (appended by the fire through `append_jsonl`);
    this fold reads it back. A later `connector_backend_changed` for the
    category (a deliberate re-declaration) drops every alias recorded before
    it. Read through `events_io`; any failure answers no aliases."""
    if workspace_root is None:
        return []
    try:
        from events_io import iter_events
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from events_io import iter_events
        except Exception:  # noqa: BLE001
            return []
    aliases: List[str] = []
    try:
        for ev in iter_events(workspace_root):
            if not isinstance(ev, dict):
                continue
            kind = ev.get("type")
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if data.get("category") != category:
                continue
            if kind == CONNECTOR_BACKEND_CHANGED_EVENT:
                aliases = []
            elif (kind == CONNECTOR_DETECTED_EVENT
                  and data.get("recorded_alias") is True):
                cand = data.get("candidate_server_id")
                if isinstance(cand, str) and cand and cand not in aliases:
                    aliases.append(cand)
    except Exception:  # noqa: BLE001 - a reader never raises
        return []
    return aliases


def declared_backend_ids(category: str, workspace_root=None,
                         entities: Optional[dict] = None) -> List[str]:
    """`[declared server_id] + recorded aliases` for a category, or `[]`
    when the category is undeclared (IDENT1 I-1)."""
    row = declared_backend(category, workspace_root, entities)
    if not row:
        return []
    return list(row.get("server_ids") or [row["server_id"]])


def zapier_server_ids(workspace_root=None, entities: Optional[dict] = None) -> List[str]:
    """Server-ids pinned as the Zapier dispatch leg (R12/H-H). Pinning by id is
    the ONLY reliable Zapier identification in a UUID-namespaced env — the
    `mcp__zapier_` prefix is absent there and the leg's tool names
    (`gmail_send_email`) otherwise mis-match as native Gmail."""
    ws = workspace_block(workspace_root, entities)
    conn = ws.get("connectors") if isinstance(ws.get("connectors"), dict) else {}
    ids = conn.get("_zapier_server_ids")
    return [s for s in ids if isinstance(s, str)] if isinstance(ids, list) else []


def is_zapier_server(server_id: Optional[str], workspace_root=None,
                    entities: Optional[dict] = None,
                    extra_ids: Optional[List[str]] = None) -> bool:
    """True if `server_id` is the declared Zapier dispatch leg. Combines the
    pinned-by-id list (authoritative) with any caller-supplied extra_ids."""
    if not server_id:
        return False
    pinned = set(zapier_server_ids(workspace_root, entities))
    if extra_ids:
        pinned.update(i for i in extra_ids if isinstance(i, str))
    return server_id in pinned


# ---------------------------------------------------------------------------
# Account map (Layer B1) + scope resolution (Layer B2)
# ---------------------------------------------------------------------------

def _norm_addr(address: Optional[str]) -> str:
    return (address or "").strip().lower()


def derive_account_id(address: Optional[str]) -> str:
    """Deterministic address-keyed account id: acct_<sha1[:12]>. Stable across
    server-id rotation (R3) — the provenance normalizer stamps this so
    historical rows resolve to the right account for scope + reply routing even
    after a reconnect changes the server UUID."""
    return "acct_" + hashlib.sha1(_norm_addr(address).encode("utf-8")).hexdigest()[:12]


def accounts(workspace_root=None, entities: Optional[dict] = None) -> List[Dict[str, Any]]:
    """The `workspace.accounts[]` compound records. [] when the map is empty."""
    ws = workspace_block(workspace_root, entities)
    acc = ws.get("accounts")
    return [a for a in acc if isinstance(a, dict)] if isinstance(acc, list) else []


def account_map_populated(workspace_root=None, entities: Optional[dict] = None) -> bool:
    """True iff the workspace has classified at least one account. When False,
    every account is in-scope (R4 — the wall is a no-op)."""
    return len(accounts(workspace_root, entities)) > 0


def account_for_address(address: Optional[str], workspace_root=None,
                       entities: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    a = _norm_addr(address)
    if not a:
        return None
    for rec in accounts(workspace_root, entities):
        if _norm_addr(rec.get("address")) == a:
            return rec
    return None


def account_for_id(account_id: Optional[str], workspace_root=None,
                  entities: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    if not account_id:
        return None
    for rec in accounts(workspace_root, entities):
        if rec.get("account_id") == account_id:
            return rec
        # A record may omit an explicit account_id; derive from its address.
        if derive_account_id(rec.get("address")) == account_id:
            return rec
    return None


def _resolve_record(address, account_id, workspace_root, entities):
    if account_id:
        rec = account_for_id(account_id, workspace_root, entities)
        if rec:
            return rec
    if address:
        return account_for_address(address, workspace_root, entities)
    return None


def _dials_for(rec: Dict[str, Any]) -> Dict[str, bool]:
    """Normalize a record's two dials to booleans, falling back to the role
    default when a dial is unset."""
    role = (rec.get("role") or "unclassified")
    defaults = _ROLE_DEFAULT_DIALS.get(role, _ROLE_DEFAULT_DIALS["unclassified"])
    scope = rec.get("scope") if isinstance(rec.get("scope"), dict) else {}

    def _b(key: str) -> bool:
        v = scope.get(key)
        if v is None:
            return defaults[key]
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("on", "true", "yes", "1")

    return {"surface": _b("surface"), "write_to_business": _b("write_to_business")}


def account_scope(address: Optional[str] = None, account_id: Optional[str] = None,
                 workspace_root=None, entities: Optional[dict] = None) -> Dict[str, bool]:
    """The effective {surface, write_to_business} dials for an account.

    Empty map  → both on (R4 back-compat). Account not found in a populated map
    → both on for BACK-COMPAT on the write dial (already-flowing provenance);
    an explicitly `unclassified` record → both off (fail-closed). A found +
    classified record → its dials (role default when a dial is unset)."""
    ent = entities if isinstance(entities, dict) else load_entities(workspace_root)
    if not account_map_populated(entities=ent):
        return {"surface": True, "write_to_business": True}
    rec = _resolve_record(address, account_id, None, ent)
    if rec is None:
        # Unknown account in a populated map — do NOT reject already-flowing
        # writes (ACCOUNT_SCOPE §4c). Surfacing/scan layer gates new accounts.
        return {"surface": True, "write_to_business": True}
    return _dials_for(rec)


def is_in_write_scope(address: Optional[str] = None, account_id: Optional[str] = None,
                     workspace_root=None, entities: Optional[dict] = None) -> bool:
    """THE writer-wall predicate. True = this account's items may be filed into
    the business substrate. Fail-closed only when an account is FOUND and
    explicitly out of write scope (ACCOUNT_SCOPE §4)."""
    return account_scope(address, account_id, workspace_root, entities)["write_to_business"]


def is_surfaceable(address: Optional[str] = None, account_id: Optional[str] = None,
                  workspace_root=None, entities: Optional[dict] = None) -> bool:
    """True = this account's items may appear in single-user ephemeral surfaces
    (brief/reminders/triage). Independent of the write dial (R9)."""
    return account_scope(address, account_id, workspace_root, entities)["surface"]


def sender_scope_override(account: Optional[Dict[str, Any]],
                          sender_address: Optional[str]) -> Optional[Dict[str, bool]]:
    """Per-sender scope override on an account record (ACCOUNT_SCOPE §1
    `overrides.senders`, populated by the promote/demote queue R8 and the
    email_exclusion_rules migration). Returns {surface, write_to_business}
    with ONLY the explicitly-set dials as booleans, or None when the sender
    has no override. Never raises."""
    try:
        if not isinstance(account, dict):
            return None
        s = _norm_addr(sender_address)
        if not s:
            return None
        ov = account.get("overrides")
        senders = ov.get("senders") if isinstance(ov, dict) else None
        if not isinstance(senders, dict):
            return None
        rec = None
        for k, v in senders.items():
            if _norm_addr(k) == s and isinstance(v, dict):
                rec = v
                break
        if rec is None:
            return None
        out: Dict[str, bool] = {}
        for key in ("surface", "write_to_business"):
            v = rec.get(key)
            if v is None:
                continue
            out[key] = v if isinstance(v, bool) else (
                str(v).strip().lower() in ("on", "true", "yes", "1"))
        return out or None
    except Exception:
        return None


def set_sender_scope_override(workspace_root, account_address: str, sender: str, *,
                              surface: Optional[bool] = None,
                              write_to_business: Optional[bool] = None,
                              reason: Optional[str] = None,
                              holder: str = "workspace-manager") -> Dict[str, Any]:
    """Write a per-sender scope override onto an account record (delegated
    setter — same single-writer discipline as set_account_classification).
    Used by: the promote-queue confirm/demote handlers (R8) and the
    email_exclusion_rules migration (structured replacement for the prose
    rules). Additive: merges dials into an existing override. Returns the
    sender's override dict."""
    ent = _load_full(workspace_root)
    ws = _workspace_container(ent)
    acc = ws.get("accounts")
    if not isinstance(acc, list):
        acc = []
        ws["accounts"] = acc
    a = _norm_addr(account_address)
    rec = None
    for r in acc:
        if isinstance(r, dict) and _norm_addr(r.get("address")) == a:
            rec = r
            break
    if rec is None:
        rec = {"address": account_address, "account_id": derive_account_id(account_address),
               "scope": {}}
        acc.append(rec)
    ov = rec.get("overrides")
    if not isinstance(ov, dict):
        ov = {}
        rec["overrides"] = ov
    senders = ov.get("senders")
    if not isinstance(senders, dict):
        senders = {}
        ov["senders"] = senders
    key = _norm_addr(sender)
    entry = senders.get(key)
    if not isinstance(entry, dict):
        entry = {}
        senders[key] = entry
    if surface is not None:
        entry["surface"] = "on" if surface else "off"
    if write_to_business is not None:
        entry["write_to_business"] = "on" if write_to_business else "off"
    if reason is not None:
        entry["reason"] = reason
    _write_entities(workspace_root, ent, holder)
    return entry


def binding_for_address(address: Optional[str], workspace_root=None,
                       entities: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """The first connector binding on an account (server_id/provider/verified).
    Outbound routing keys on the binding, not the account (B1)."""
    rec = account_for_address(address, workspace_root, entities)
    if not rec:
        return None
    binds = rec.get("bindings")
    if isinstance(binds, list):
        for b in binds:
            if isinstance(b, dict) and b.get("server_id"):
                return b
    return None


# ---------------------------------------------------------------------------
# Setter (workspace-manager-owned; onboarding/update-bridge call as delegates)
# ---------------------------------------------------------------------------

def _load_full(workspace_root) -> Dict[str, Any]:
    try:
        return json.loads(_entities_path(workspace_root).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _workspace_container(ent: Dict[str, Any]) -> Dict[str, Any]:
    """Return the dict that DOES / should hold `workspace`, honoring nested vs
    flat. Creates the `workspace` sub-dict in place, returns it."""
    inner = ent.get("entities") if isinstance(ent.get("entities"), dict) else None
    container = inner if inner is not None else ent
    ws = container.get("workspace")
    if not isinstance(ws, dict):
        ws = {}
        container["workspace"] = ws
    return ws


def _write_entities(workspace_root, ent: Dict[str, Any], holder: str) -> None:
    # Bump the top-level version (concurrency guard — WORKSPACE_API write protocol).
    try:
        ent["version"] = int(ent.get("version", 0)) + 1
    except Exception:
        ent["version"] = 1
    try:
        from atomic_write import atomic_write_json_locked
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from atomic_write import atomic_write_json_locked
    atomic_write_json_locked(_entities_path(workspace_root), ent, holder=holder)


def retire_uuid_bindings(ws: Dict[str, Any], new_server_id: str,
                         provider: Optional[str] = None,
                         *, prior_server_id: Optional[str] = None,
                         binding_verified: Optional[str] = None) -> List[str]:
    """Move every account binding that still points at a ROTATING server-id
    onto `new_server_id`, in place. Returns the retired ids.

    Called from the re-pin path below, and only when the new id is NOT
    UUID-shaped. `set_account_classification` merges bindings BY server-id, so
    without this a re-pin leaves the old UUID binding sitting beside the new
    one — and `binding_for_address` hands out the FIRST binding it finds, so
    outbound mail could still be addressed through a connection that no longer
    exists. A binding is only moved when it is for the same provider (or was
    the exact id being re-pinned): one account can be bound to two different
    connectors on purpose.

    Mutates `ws` in place; the caller writes."""
    retired: List[str] = []
    accts = ws.get("accounts")
    if not isinstance(accts, list):
        return retired
    want = (provider or "").strip().lower() or None
    for rec in accts:
        if not isinstance(rec, dict):
            continue
        binds = rec.get("bindings")
        if not isinstance(binds, list):
            continue
        kept: List[Any] = []
        for b in binds:
            if not isinstance(b, dict):
                kept.append(b)
                continue
            sid = b.get("server_id")
            if not isinstance(sid, str) or sid == new_server_id:
                kept.append(b)
                continue
            if not is_uuid_shaped_server_id(sid):
                kept.append(b)
                continue
            same_provider = want is not None and (
                str(b.get("provider") or "").strip().lower() == want
                or str(b.get("capabilities_ref") or "").strip().lower() == want)
            if not (same_provider or (prior_server_id and sid == prior_server_id)):
                kept.append(b)
                continue
            moved = dict(b)
            moved["server_id"] = new_server_id
            moved["retired_server_id"] = sid
            if binding_verified:
                moved["binding_verified"] = binding_verified
            retired.append(sid)
            existing = next((k for k in kept
                             if isinstance(k, dict) and k.get("server_id") == new_server_id),
                            None)
            if existing is not None:
                # Already re-pinned by an earlier pass — merge rather than
                # leave two rows for one connector.
                existing.update({k: v for k, v in moved.items() if v is not None})
            else:
                kept.append(moved)
        rec["bindings"] = kept
    return retired


def set_declared_backend(workspace_root, category: str, server_id: str,
                        provider: Optional[str] = None, label: Optional[str] = None,
                        *, is_zapier: bool = False,
                        binding_verified: Optional[str] = None,
                        holder: str = "workspace-manager") -> Dict[str, Any]:
    """Declare (or re-declare) the backend for a category. Workspace-manager
    owned; the runtime verb `set my email backend to [connector]` and
    update-bridge's migration both route here. Returns the new connectors block.

    `is_zapier=True` pins the server-id into `_zapier_server_ids` (R12) instead
    of a category row — the Zapier dispatch leg is never a category backend.

    RE-PIN (DISC1): when the new server-id is not UUID-shaped — a display-name
    id, which does not rotate — every account binding still pointing at a
    rotating id for that provider is moved onto it and the old id recorded as
    `retired_server_id`. `binding_verified` (`connector_asserted` when the
    address came back from the connector itself) is stamped on the moved
    bindings when given. An unknown `binding_verified` value is refused rather
    than written: a routing field nobody can read is worse than an absent
    one."""
    if binding_verified is not None and binding_verified not in BINDING_VERIFIED_VALUES:
        raise ValueError(
            f"binding_verified must be one of {BINDING_VERIFIED_VALUES}, "
            f"got {binding_verified!r}")
    ent = _load_full(workspace_root)
    ws = _workspace_container(ent)
    conn = ws.get("connectors")
    if not isinstance(conn, dict):
        conn = {}
        ws["connectors"] = conn
    if is_zapier:
        ids = conn.get("_zapier_server_ids")
        if not isinstance(ids, list):
            ids = []
            conn["_zapier_server_ids"] = ids
        if server_id not in ids:
            ids.append(server_id)
    else:
        prior = conn.get(category)
        prior_sid = prior.get("server_id") if isinstance(prior, dict) else None
        conn[category] = {"server_id": server_id, "provider": provider, "label": label}
        if not is_uuid_shaped_server_id(server_id):
            retire_uuid_bindings(ws, server_id, provider,
                                 prior_server_id=prior_sid,
                                 binding_verified=binding_verified)
    _write_entities(workspace_root, ent, holder)
    return conn


def set_account_classification(workspace_root, address: str, *, role: Optional[str] = None,
                              surface: Optional[bool] = None,
                              write_to_business: Optional[bool] = None,
                              what_lives_here: Optional[str] = None,
                              binding: Optional[Dict[str, Any]] = None,
                              holder: str = "workspace-manager") -> Dict[str, Any]:
    """Classify (or reclassify) an account. Workspace-manager owned; the
    onboarding account-enumeration gate and the runtime verbs
    (`[address] is my personal account`, `mark [account] out of scope`) route
    here. Additive: an existing record is updated in place; a new one is
    appended with a derived stable account_id (R3). Returns the record.

    Dial changes are recorded but the account-lifecycle EVENT
    (account_classified / account_role_changed / account_scope_masked) is
    emitted by the CALLING skill through event_gate.append_event — this setter
    owns only the entities.json block (single-writer discipline; the skill owns
    its own event stream + any tombstone emission on a business→personal flip)."""
    ent = _load_full(workspace_root)
    ws = _workspace_container(ent)
    acc = ws.get("accounts")
    if not isinstance(acc, list):
        acc = []
        ws["accounts"] = acc

    a = _norm_addr(address)
    rec = None
    for r in acc:
        if isinstance(r, dict) and _norm_addr(r.get("address")) == a:
            rec = r
            break
    if rec is None:
        rec = {"address": address, "account_id": derive_account_id(address),
               "scope": {}}
        acc.append(rec)
    rec.setdefault("account_id", derive_account_id(address))
    if role is not None:
        rec["role"] = role
    if what_lives_here is not None:
        rec["what_lives_here"] = what_lives_here
    scope = rec.get("scope") if isinstance(rec.get("scope"), dict) else {}
    if surface is not None:
        scope["surface"] = "on" if surface else "off"
    if write_to_business is not None:
        scope["write_to_business"] = "on" if write_to_business else "off"
    rec["scope"] = scope
    if binding is not None:
        binds = rec.get("bindings")
        if not isinstance(binds, list):
            binds = []
            rec["bindings"] = binds
        sid = binding.get("server_id")
        existing = next((b for b in binds if isinstance(b, dict) and b.get("server_id") == sid), None)
        if existing:
            existing.update(binding)
        else:
            binds.append(binding)

    _write_entities(workspace_root, ent, holder)
    return rec


# ---------------------------------------------------------------------------
# The accounts SURFACE (FIX3 F3-3)
# ---------------------------------------------------------------------------
#: The two clauses the reply has always written, moved out of the skill's prose
#: and into the composer that renders them, so one sentence has one home.
SURFACE_CLAUSE_ON = "I'll show it in your brief"
SURFACE_CLAUSE_OFF = "I'll keep it out of your brief"
WRITE_CLAUSE_ON = "I'll file it into your records"
WRITE_CLAUSE_OFF = "I'll never file it"

#: Plain-English names for the roles. A role name is this product's word for a
#: shape, not the reader's; the answer says what it MEANS.
_ROLE_IN_ENGLISH = {
    "business-primary": "your main work address",
    "business-secondary": "a second work address",
    "mixed": "work and personal mixed together",
    "personal": "personal",
    "shared-support": "a shared support address",
    "billing": "a billing address",
    "cold-outreach": "an outreach address",
    "unclassified": "not classified yet",
}


#: An address-shaped token on a composed line. Deliberately loose on the
#: local part and strict on the shape, because this is a REFUSAL gate: a
#: false positive drops one line of the product's own answer, a false
#: negative puts somebody else's address in front of the reader.
#:
#: FIX PASS 2 (review N-2). The local part is RFC 5322 atext, not a hand-
#: picked five. The narrow class extracted `sample@example.com` out of
#: `o'sample@example.com`, which is not in the reader's own accept set, so
#: the fence dropped the reader's OWN classified line — the one thing this
#: answer exists to show. The trade named above is still the right one; it
#: just has to be paid on somebody else's address, not on theirs.
_ADDRESS_RE = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _only_known_addresses(lines: List[str], known: set) -> List[str]:
    """Drop any line carrying an address the reader never classified.

    `known` is built from `workspace.accounts` alone. The lines are not: the
    mail and calendar lines quote a connector LABEL, which nothing in this
    module wrote. That asymmetry is the whole point — a filter whose accept
    set comes from the same place as the thing it filters accepts everything.
    """
    kept: List[str] = []
    for line in lines:
        found = _ADDRESS_RE.findall(line or "")
        if any(_norm_addr(tok) not in known for tok in found):
            continue
        kept.append(line)
    return kept


def accounts_surface(workspace_root=None,
                     entities: Optional[dict] = None) -> Dict[str, Any]:
    """`{"lines": [...], "n_accounts": int}` — the whole accounts answer.

    The reader asked which of their mail addresses this product treats as work
    and which it leaves alone. So: which connector carries mail, which carries
    calendar, and then their own addresses with what each one means. Nothing
    else. Not a server id, not a key name, not the word substrate, and — the
    defect this composer exists for (re-walk 2026-09-21) — not the harness's
    inventory of their computer, its operating system or its shared folders.

    Every line goes through `validate_chat_output` before it is returned, so a
    line this composer could not gate never reaches the caller. An address that
    is not in `workspace.accounts` is never emitted — and that is a fence over
    EVERY line, not only the per-account ones: the backend lines carry a
    connector LABEL, which is operator-supplied text this composer does not
    own, and a label with an address in it would otherwise put an unclassified
    address into the answer. `_ADDRESS_RE` finds address-shaped tokens on a
    composed line and `_only_known_addresses` drops any line carrying one the
    reader never classified. (FIX PASS 1, review M-2: the first form of this
    fence built its `known` set from the same records it filtered, so every
    address was in it by construction and removing the filter changed
    nothing.)
    """
    ent = entities if isinstance(entities, dict) else load_entities(workspace_root)
    lines: List[str] = []

    for category, noun in (("email", "Mail"), ("calendar", "Calendar")):
        row = declared_backend(category, workspace_root, ent)
        label = str((row or {}).get("label") or "").strip() if row else ""
        if label:
            lines.append(f"{noun} runs through {label}.")
        else:
            lines.append(f"No {noun.lower()} backend is declared yet.")

    records = accounts(workspace_root, ent)
    known = {_norm_addr(rec.get("address")) for rec in records if
             str(rec.get("address") or "").strip()}
    for rec in records:
        address = str(rec.get("address") or "").strip()
        if not address:
            continue
        role = str(rec.get("role") or "unclassified")
        meaning = _ROLE_IN_ENGLISH.get(role, _ROLE_IN_ENGLISH["unclassified"])
        dials = _dials_for(rec)
        surface = SURFACE_CLAUSE_ON if dials["surface"] else SURFACE_CLAUSE_OFF
        write = WRITE_CLAUSE_ON if dials["write_to_business"] else WRITE_CLAUSE_OFF
        lines.append(f"{address} — {meaning}. {surface}, and {write}.")

    if not records:
        lines.append("You have not told me about any of your addresses yet.")

    # THE ADDRESS FENCE. `known` is the set of addresses the reader
    # classified; the lines above it were composed from two sources, and only
    # one of them is that set. The backend lines are built from a connector
    # `label` — operator text — so this is the door through which an address
    # the reader never classified could reach the answer. A line carrying one
    # is DROPPED; the reader is told about their own addresses or not at all.
    lines = _only_known_addresses(lines, known)

    # Every line leaves through `surface_composers.say`, which is what makes
    # this a composer rather than prose with extra steps: it runs the leak
    # gate AND stamps the line, so `post(..., relayed=...)` can vouch for it
    # later. This name is on `FOREIGN_COMPOSERS` for that reason. A line the
    # gate refuses is this product's own words carrying something internal, so
    # it is dropped rather than posted — and the drop is visible, because
    # `n_accounts` still counts what was found.
    gated: List[str] = []
    try:
        from surface_composers import say
    except Exception:  # noqa: BLE001 — a missing gate narrows, never breaks
        return {"lines": lines, "n_accounts": len(records)}
    for line in lines:
        try:
            gated.append(say(line, workspace=workspace_root,
                             surface="accounts"))
        except Exception:  # noqa: BLE001 — a refused line is not posted
            continue
    return {"lines": gated, "n_accounts": len(records)}


__all__ = [
    # readers
    "accounts_surface",
    "SURFACE_CLAUSE_ON", "SURFACE_CLAUSE_OFF",
    "WRITE_CLAUSE_ON", "WRITE_CLAUSE_OFF",
    "load_entities", "workspace_block", "server_id_of",
    "declared_backends", "declared_backend",
    "zapier_server_ids", "is_zapier_server",
    "derive_account_id", "accounts", "account_map_populated",
    "account_for_address", "account_for_id",
    "account_scope", "is_in_write_scope", "is_surfaceable", "binding_for_address",
    "sender_scope_override",
    # setter (workspace-manager owned)
    "set_declared_backend", "set_account_classification",
    "retire_uuid_bindings", "is_uuid_shaped_server_id",
    "BINDING_USER_ASSERTED", "BINDING_CONNECTOR_ASSERTED",
    "BINDING_VERIFIED_VALUES",
    "set_sender_scope_override",
]
