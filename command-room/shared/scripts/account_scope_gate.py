#!/usr/bin/env python3
"""
The writer-side account-scope wall (Layer B2, R2/R3) — the load-bearing privacy
guarantee.

Runs inside `atomic_append_jsonl`'s events.jsonl branch (the SAME single
chokepoint as the event gate and the dedup hook), so every append is walled
caller-agnostically. Enforces `shared/ACCOUNT_SCOPE.md` §4:

  R2 — closes the fail-OPEN inversion. The naive rule "reject if provenance
       present AND out-of-scope" lets an LLM drop `source_ref` to bypass the
       wall silently. Fix: enumerate provenance-REQUIRED families and reject
       those when provenance is ABSENT, in addition to rejecting ANY event
       whose provenance resolves to an out-of-scope account.
  R3 — scope is resolved through the stable, address-keyed `account_id`
       (connector_adapters.provenance.resolve_account_id) so a server-id
       rotation (Rule 22) never mis-attributes a historical row.

FAIL-CLOSED, BUT ONLY ONCE A WORKSPACE OPTS IN (R4 / ACCOUNT_SCOPE §4c):
  - Empty/absent account map  → NO-OP. Every existing workspace + test + live
    client mid-upgrade behaves exactly as today. The wall only bites once the
    user has classified at least one account.
  - Populated map, event's account resolves to a classified OUT-OF-SCOPE
    account → REJECT (AccountScopeError).
  - Populated map, provenance-REQUIRED family with NO provenance → REJECT
    (the R2 fail-open fix).
  - Account unresolvable (legacy row, no address) → IN scope (back-compat;
    those rows predate the wall — ACCOUNT_SCOPE §4b).

Only ever raises the deliberate `AccountScopeError`. Any INTERNAL error degrades
to "allow the write" (a broken account map must never brick the substrate) — the
wall is a privacy filter, not a new corruption vector.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional


class AccountScopeError(ValueError):
    """An event was rejected because its account is out of business-write scope,
    or a provenance-required family carried no provenance (R2). Fail loud."""


# Families that ALWAYS carry connector provenance — reject on absent OR
# out-of-scope (R2 strict). `meeting` is NOT here: a meeting is strict only
# when connector-sourced (data.origin == "connector") — workspace-manager's
# end-session review and manual "log the meeting" write provenance-less
# meeting events that must keep working on classified workspaces (review
# fix 6). See _classify.
_STRICT_REQUIRED = frozenset({"interaction"})

# Origin discriminator (review fix 5) — mirrors the reminders lane's
# data.origin precedent (event_gate.REMINDER_ORIGIN). Producers stamp
# commitment/meeting events at write time: connector capture paths
# (inbox-triage scan, sent_capture, slack_capture, meeting_capture,
# capture_gate promote) stamp "connector"; chat-stated items stamp
# "user_stated". ABSENT origin = legacy staging: treated as today
# (scope_only via provider sniff) for producer back-compat, with a stderr
# warning — NOT hard-rejected yet (live producers lag; flip to strict once
# the fleet stamps origin).
ORIGIN_CONNECTOR = "connector"
ORIGIN_USER_STATED = "user_stated"

# Person-enrichment families — provenance-required ONLY when they came FROM a
# connector read (they carry provenance). A manually-added person has none and
# must pass. So: reject on out-of-scope when provenance IS present; never
# reject-on-absent (that would block manual CRM adds).
_PERSON_ENRICHMENT = frozenset({
    "person_created", "person_updated", "person_enriched",
    "contact_email_captured", "person_proposal", "person_update_proposal",
})

# Connector provider tags that mark provenance as connector-derived (vs a
# user-stated commitment typed in chat).
_CONNECTOR_PROVIDERS = frozenset({
    "gmail", "gcal", "gcalendar", "slack", "granola", "superhuman",
    "outlook", "drive",
    # CHATSCAN1 — the second chat provider. `slack` has been here since the
    # capture leg landed; without its sibling, a Teams-sourced row would sniff
    # as NOT connector-derived and skip the scope wall entirely, which is the
    # opposite of what a per-tenant client backend needs.
    "ms365_teams",
})


def _data(ev: dict) -> dict:
    d = ev.get("data")
    return d if isinstance(d, dict) else {}


def _has_provenance(ev: dict) -> bool:
    d = _data(ev)
    sref = d.get("source_ref") or ev.get("source_ref")
    if isinstance(sref, str) and sref.strip():
        return True
    prov = d.get("provenance")
    return isinstance(prov, dict) and bool(prov.get("native_id"))


def _provider_of(ev: dict) -> Optional[str]:
    d = _data(ev)
    prov = d.get("provenance")
    if isinstance(prov, dict) and prov.get("provider"):
        return str(prov["provider"]).lower()
    sref = d.get("source_ref") or ev.get("source_ref")
    if isinstance(sref, str) and ":" in sref:
        return sref.split(":", 1)[0].strip().lower()
    return None


def _is_connector_derived_commitment(ev: dict) -> bool:
    if ev.get("type") != "commitment":
        return False
    prov = _provider_of(ev)
    return prov in _CONNECTOR_PROVIDERS


def _warn_absent_origin(ev: dict) -> None:
    try:
        sys.stderr.write(
            f"[account_scope_gate] WARN: connector-derived {ev.get('type')!r} "
            "event carries no data.origin — stamping lags (producers should "
            f"write data.origin='{ORIGIN_CONNECTOR}' for connector captures, "
            f"'{ORIGIN_USER_STATED}' for chat-stated items). Falling back to "
            "provider-sniff scope check (legacy staging).\n"
        )
    except Exception:
        pass


def _classify(ev: dict) -> str:
    """'strict' (reject on absent + out-of-scope), 'scope_only' (reject on
    out-of-scope only), or 'exempt'."""
    t = ev.get("type")
    if t in _STRICT_REQUIRED:
        return "strict"
    origin = _data(ev).get("origin")
    if t == "meeting":
        # Strict only for connector-sourced meetings (ACCOUNT_SCOPE §4a scopes
        # strict to meetings "sourced from a connector read"). A provenance-
        # less manual / end-session meeting log passes (review fix 6).
        if origin == ORIGIN_CONNECTOR:
            return "strict"
        if _has_provenance(ev):
            return "scope_only"
        return "exempt"
    if t == "commitment":
        # R2 via the origin discriminator (review fix 5, reminders-lane
        # precedent): connector-origin is STRICT (an LLM dropping source_ref
        # can no longer bypass the wall for stamped producers); user-stated is
        # exempt; absent origin = legacy staging (today's provider sniff +
        # stderr warn — no hard reject while live producers lag).
        if origin == ORIGIN_CONNECTOR:
            return "strict"
        if origin == ORIGIN_USER_STATED:
            return "exempt"
        if _is_connector_derived_commitment(ev):
            _warn_absent_origin(ev)
            return "scope_only"
        return "exempt"
    if t in _PERSON_ENRICHMENT and _has_provenance(ev):
        # The promote-queue lane (R8, ACCOUNT_SCOPE §8): a proposal that IS
        # the propose-then-confirm review surface must be writable even when
        # its account is out of write scope — that's the whole point (the
        # wall bites at PROMOTION: the confirmed create_person + the
        # subsequent interactions, not at the metadata-only proposal).
        # Narrowed 2026-07-12 closeout: person_update_proposal removed from
        # the exemption — no producer stamps promote_queue on it today.
        # Re-add if/when a real update-proposal lane joins the promote-queue.
        if t == "person_proposal" and _data(ev).get("promote_queue") is True:
            return "exempt"
        return "scope_only"
    return "exempt"


def _account_map_populated(workspace_root) -> bool:
    try:
        from connector_config import account_map_populated
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from connector_config import account_map_populated
    return account_map_populated(workspace_root)


def _sender_of(ev: dict) -> Optional[str]:
    """Best-effort counterparty/sender address on an event (for the per-sender
    override lookup). None when the event carries no sender address."""
    d = _data(ev)
    for key in ("from", "sender", "sender_email", "counterparty_email"):
        v = d.get(key)
        if isinstance(v, str) and "@" in v:
            return v.strip().lower()
    return None


def _has_person_refs(ev: dict) -> bool:
    """True when the event references a resolved entity — the cheap, already-
    computed business-by-association signal (person records are the entity
    graph; capture paths resolve person_ids/counterparty_id at write time).

    BUG-8244: folds RESOLVED-id variants only (persons_of covers
    data.attendee_person_ids etc.). Attendee EMAILS deliberately do not
    count — an unresolved address is not a resolved entity, and treating it
    as one would file unknown-sender events to business by association."""
    d = _data(ev)
    pids = ev.get("person_ids") or d.get("person_ids")
    if isinstance(pids, list) and any(p for p in pids):
        return True
    try:
        from event_refs import persons_of
        if persons_of(ev):
            return True
    except Exception:
        pass
    return bool(d.get("counterparty_id"))


def _out_of_scope(ev: dict, workspace_root) -> bool:
    """True iff the event resolves to a classified OUT-OF-SCOPE account.
    Unresolvable account → in scope (back-compat).

    Two carve-outs (ACCOUNT_SCOPE §1/§8):
      - a per-sender override (`overrides.senders[addr].write_to_business: on`)
        puts that sender in scope on ANY account role — the promote-queue's
        confirm surface and the email_exclusion_rules migration write these;
      - a `mixed` account files BY ASSOCIATION: an event referencing a
        resolved entity (person_ids / counterparty_id) is in scope; an
        unknown-sender event stays walled and routes to the promote-queue."""
    try:
        from connector_adapters.provenance import resolve_account_id
        from connector_config import (account_for_id, is_in_write_scope,
                                      sender_scope_override)
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from connector_adapters.provenance import resolve_account_id
        from connector_config import (account_for_id, is_in_write_scope,
                                      sender_scope_override)
    aid = resolve_account_id(event=ev, workspace_root=workspace_root)
    if not aid:
        return False  # unresolvable → in scope (ACCOUNT_SCOPE §4b)
    if is_in_write_scope(account_id=aid, workspace_root=workspace_root):
        return False
    rec = account_for_id(aid, workspace_root)
    if isinstance(rec, dict):
        ov = sender_scope_override(rec, _sender_of(ev))
        if ov and ov.get("write_to_business") is True:
            return False
        if (rec.get("role") or "").strip().lower() == "mixed" and _has_person_refs(ev):
            return False  # business-by-association (ACCOUNT_SCOPE §1/§8)
    return True


def _workspace_root_from_path(path) -> Optional[Path]:
    if not path:
        return None
    p = Path(path)
    if p.name == "events.jsonl" and p.parent.name == "data" and p.parent.parent.name == "_hq":
        return p.parent.parent.parent
    return None


def enforce_scope(events: List[dict], *, path=None, workspace_root=None,
                  holder: str = "account_scope_gate") -> List[dict]:
    """Wall a batch of events. Returns them unchanged when in scope; raises
    AccountScopeError on the first violation. No-op unless the account map is
    populated (R4). Defensive: any internal error → allow the write."""
    try:
        if workspace_root is None:
            workspace_root = _workspace_root_from_path(path)
        if workspace_root is None:
            return events
        if not _account_map_populated(workspace_root):
            return events
    except Exception:
        return events

    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            continue
        try:
            mode = _classify(ev)
        except Exception:
            continue
        if mode == "exempt":
            continue
        try:
            if mode == "strict" and not _has_provenance(ev):
                raise AccountScopeError(
                    f"event {i} type={ev.get('type')!r} is a provenance-REQUIRED "
                    f"family but carries no provenance (holder={holder}) — R2: a "
                    "connector-derived interaction/meeting MUST carry a "
                    "source_ref / provenance so the account-scope wall can "
                    "resolve which mailbox it came from. An absent provenance is "
                    "a silent bypass of the personal-mail wall; write it with "
                    "connector_adapters.provenance.normalize_provenance."
                )
            if _out_of_scope(ev, workspace_root):
                raise AccountScopeError(
                    f"event {i} type={ev.get('type')!r} resolves to an "
                    f"OUT-OF-SCOPE account (write_to_business: off; "
                    f"holder={holder}) — personal / non-business mail never "
                    "enters the business substrate (ACCOUNT_SCOPE §4). Surface "
                    "it in the brief if its surface dial is on, but do not file "
                    "it. If this account IS business, classify it via "
                    "'[address] is my business account'."
                )
        except AccountScopeError:
            raise
        except Exception:
            # Any resolution error → allow (never brick a write over a broken map)
            continue
    return events


# ---------------------------------------------------------------------------
# Reader-honor for scope masks (R5, ACCOUNT_SCOPE §6) — the SHARED helper so
# readers (people-view, the CRU/commitment projectors, dormancy,
# relationship-moves) don't each reimplement mask resolution.
#
# A business→personal reclassification appends `account_scope_masked`
# {address, masked_account_id, reason}; a personal→business restore appends
# `account_scope_restored`. A mask is LIVE iff its latest mask event comes
# after its latest restore (append order). Readers drop rows whose resolved
# account identity matches a live mask. Rows are NEVER physically moved —
# this is read-side only.
#
# Honest limit (stated in ACCOUNT_SCOPE §6): a historical row can only be
# masked if it CARRIES account identity (provenance.account_id, or a
# data.account_address / data.from address that derives to the masked
# account). Rows that predate account stamping have no attribution and stay
# visible — prospectively, every new connector write carries account_id via
# normalize_provenance.
# ---------------------------------------------------------------------------

def _mask_ids_for(data: dict):
    """The identity set one mask/restore event refers to: the explicit
    masked_account_id plus the id derived from the address (both spellings
    must un/mask together)."""
    ids = set()
    aid = data.get("masked_account_id") or data.get("account_id")
    if isinstance(aid, str) and aid.strip():
        ids.add(aid.strip())
    addr = data.get("address")
    if isinstance(addr, str) and addr.strip():
        try:
            from connector_config import derive_account_id
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from connector_config import derive_account_id
        ids.add(derive_account_id(addr))
    return ids


def live_masks_from_events(events) -> frozenset:
    """Compute the LIVE mask set (account_ids) from an already-loaded event
    list, in append order. Pure; never raises (junk rows are skipped)."""
    masked: set = set()
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t not in ("account_scope_masked", "account_scope_restored"):
            continue
        try:
            ids = _mask_ids_for(_data(ev))
        except Exception:
            continue
        if t == "account_scope_masked":
            masked |= ids
        else:
            masked -= ids
    return frozenset(masked)


def live_masks(workspace_root) -> frozenset:
    """The live mask set for a workspace, read from events.jsonl (shard-
    transparent via events_io). Empty set on any failure — a broken log must
    never brick a reader."""
    try:
        try:
            from events_io import iter_events
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from events_io import iter_events
        return live_masks_from_events(iter_events(workspace_root))
    except Exception:
        return frozenset()


def _event_account_ids(ev: dict):
    """Identity candidates a ROW can be matched against a mask with:
    provenance.account_id (authoritative), else ids derived from
    data.account_address / data.from."""
    ids = set()
    d = _data(ev)
    prov = d.get("provenance")
    if isinstance(prov, dict) and isinstance(prov.get("account_id"), str):
        ids.add(prov["account_id"])
    try:
        from connector_config import derive_account_id
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from connector_config import derive_account_id
    for key in ("account_address", "from"):
        v = d.get(key)
        if isinstance(v, str) and "@" in v:
            ids.add(derive_account_id(v))
    return ids


def filter_masked_events(events, *, masks=None, workspace_root=None):
    """Drop rows whose account identity matches a live scope mask (R5).

    `masks` may be precomputed (frozenset of account_ids); when None it is
    computed from `events` itself when that is a list (mask events live in
    the same log), else from `workspace_root`. Returns the input unchanged
    when there are no masks. Never raises — any internal error returns the
    events unfiltered (a broken mask must never blank a surface)."""
    try:
        evs = list(events or [])
        if masks is None:
            masks = live_masks_from_events(evs) if evs else frozenset()
            if not masks and workspace_root is not None:
                masks = live_masks(workspace_root)
        if not masks:
            return evs
        out = []
        for ev in evs:
            try:
                if isinstance(ev, dict) and (_event_account_ids(ev) & masks):
                    continue
            except Exception:
                pass
            out.append(ev)
        return out
    except Exception:
        return list(events or [])


def enforce_record_scope(workspace_root, *, provenance: Optional[dict] = None,
                         source_ref: Optional[str] = None,
                         account_address: Optional[str] = None,
                         holder: str = "people_writer") -> None:
    """The CRM record wall (review fix 7): walls entities.json / _people record
    writes (people_writer.create_person / update_person, org_writer.create_org)
    the same way enforce_scope walls events.jsonl — a record whose payload
    carries connector provenance resolving to a classified OUT-OF-SCOPE account
    raises AccountScopeError BEFORE the entities.json write.

    A payload with NO connector provenance passes (manual "add Dustin" stays
    frictionless). An unresolvable account passes (back-compat, §4b). Same
    never-brick posture as enforce_scope: empty map → no-op; any INTERNAL error
    (corrupt entities.json, broken map) → allow the write."""
    try:
        if workspace_root is None:
            return
        if not _account_map_populated(workspace_root):
            return
        try:
            from connector_adapters.provenance import resolve_account_id
            from connector_config import is_in_write_scope
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from connector_adapters.provenance import resolve_account_id
            from connector_config import is_in_write_scope
        aid = None
        if isinstance(provenance, dict) and provenance.get("account_id"):
            aid = provenance["account_id"]
        if aid is None and account_address:
            aid = resolve_account_id(address=account_address,
                                     workspace_root=workspace_root)
        if aid is None:
            # A bare source_ref (gmail:<id>) names the ARTIFACT, not the
            # mailbox — without an account_id / address it cannot resolve to
            # an account; pass (§4b back-compat + never-brick).
            return
        in_scope = is_in_write_scope(account_id=aid, workspace_root=workspace_root)
    except Exception:
        return
    if not in_scope:
        raise AccountScopeError(
            f"record write carries connector provenance resolving to an "
            f"OUT-OF-SCOPE account (account_id={aid!r}, "
            f"write_to_business: off; holder={holder}) — a personal-account "
            "contact never enters entities.json/_people (ACCOUNT_SCOPE §2). "
            "Manual adds (no connector provenance) are unaffected. If this "
            "account IS business, classify it via "
            "'[address] is my business account'."
        )


__all__ = ["AccountScopeError", "enforce_scope", "enforce_record_scope",
           "live_masks", "live_masks_from_events", "filter_masked_events",
           "ORIGIN_CONNECTOR", "ORIGIN_USER_STATED"]
