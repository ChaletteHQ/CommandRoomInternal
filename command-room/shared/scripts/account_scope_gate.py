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
    # cr1#1139: the id names the MAILBOX only when the row says which mailbox
    # it was read from; the resolver's last fallback is the sender (`from`).
    _d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    _p = _d.get("provenance") if isinstance(_d.get("provenance"), dict) else {}
    by_mailbox = bool(_p.get("account_id") or _d.get("account_address"))
    if is_in_write_scope(account_id=aid, workspace_root=workspace_root, mailbox=by_mailbox):
        return False
    rec = account_for_id(aid, workspace_root)
    if isinstance(rec, dict):
        ov = sender_scope_override(rec, _sender_of(ev))
        if ov and ov.get("write_to_business") is True:
            return False
        if (rec.get("role") or "").strip().lower() == "mixed" and _has_person_refs(ev):
            return False  # business-by-association (ACCOUNT_SCOPE §1/§8)
    return True


#: cr1#1179: connector kinds whose rows may be matched by PROVIDER alone when
#: they carry no server id. Mail and calendar are left out: their
#: `source_ref` prefixes are legacy-mapped (a Superhuman row reads `gmail:`),
#: one provider often fronts a business AND a personal mailbox, and the
#: account wall already scopes them by mailbox. Matching them by provider
#: would refuse a business capture from an undeclared second connector.
_PROVIDER_MATCH_KINDS = frozenset({"meetings", "chat", "documents", "tasks",
                                   "deals", "contracts"})


def _personal_connectors(workspace_root) -> list:
    """cr1#1179: `[{server_ids, provider}]` for every declared connector whose
    role is personal (#1178). `provider` is set only for a kind in
    `_PROVIDER_MATCH_KINDS` whose provider no other declared connector
    shares. [] on any failure (the wall then works as before, by account)."""
    try:
        try:
            import connector_config as cc
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import connector_config as cc
        recs = cc.declared_connector_settings(workspace_root)
        out = []
        for rec in recs:
            if rec.get("role") != cc.ROLE_PERSONAL:
                continue
            ids = set()
            try:
                ids.update(cc.declared_backend_ids(rec["category"], workspace_root))
            except Exception:  # noqa: BLE001
                pass
            if rec.get("server_id"):
                ids.add(rec["server_id"])
            prov = str(rec.get("provider") or "").strip().lower() or None
            shared = any(str(r.get("provider") or "").strip().lower() == prov
                         and r.get("role") != cc.ROLE_PERSONAL for r in recs)
            by_provider = (prov if (prov and not shared
                                    and rec.get("kind") in _PROVIDER_MATCH_KINDS)
                           else None)
            out.append({"server_ids": ids, "provider": by_provider})
        return out
    except Exception:  # noqa: BLE001
        return []


def _from_personal_connector(ev: dict, personal: list) -> bool:
    """cr1#1179: True iff the event was captured from a connector the owner
    called personal: its provenance names that connector's server
    (`connector`, the key `normalize_provenance` writes, or `server_id`), or,
    with no server on the row, its provider is a personal connector's
    (only for the kinds in `_PROVIDER_MATCH_KINDS`). A chat-stated item
    (`origin: user_stated`) is the owner's own words and never matches.
    Separate from `_out_of_scope`, which is the ACCOUNT wall and runs only
    once the account map is populated; this clause runs on every book and
    for every event family."""
    if not personal:
        return False
    d = _data(ev)
    if d.get("origin") == ORIGIN_USER_STATED:
        return False
    prov = d.get("provenance")
    sid = None
    if isinstance(prov, dict):
        sid = prov.get("connector") or prov.get("server_id")
    if sid:
        return any(sid in p["server_ids"] for p in personal)
    provider = _provider_of(ev)
    return bool(provider) and any(p["provider"] == provider for p in personal)


#: cr1#1181: the off-limits set per workspace, kept until either store changes.
_OFF_LIMITS_CACHE: dict = {}


def _stamp(path: Path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def off_limits_senders(workspace_root) -> set:
    """cr1#1181: the senders the owner put off limits, as bare lower-case
    addresses (`mail_rules.address_of`).

    Two places say it, and either is enough:
      * a per-sender override whose `write_to_business` is explicitly off, on
        any account record (`overrides.senders`, ACCOUNT_SCOPE §1): the
        "write on" side of the same override is honoured in `_out_of_scope`;
      * a `hide` rule on one sender that onboarding's off-limits answer wrote
        (`mail_rules`, `off_limits: true`). An ordinary hide rule only hides
        mail from the inbox, and is not read here.
    Read on every append, so it is cached until entities.json or the rules
    file changes. Never raises: a store that cannot be read puts nobody off
    limits."""
    try:
        import mail_rules
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import mail_rules
    root = Path(str(workspace_root))
    key = (str(root), _stamp(root / "_hq" / "data" / "entities.json"),
           _stamp(mail_rules.store_path(root)))
    hit = _OFF_LIMITS_CACHE.get(key[0])
    if hit is not None and hit[0] == key:
        return set(hit[1])
    out: set = set()
    try:
        from connector_config import accounts, sender_scope_override
        for rec in accounts(workspace_root):
            ov = rec.get("overrides")
            senders = ov.get("senders") if isinstance(ov, dict) else None
            for addr in (senders or {}) if isinstance(senders, dict) else ():
                got = sender_scope_override(rec, addr)
                if got and got.get("write_to_business") is False:
                    bare = mail_rules.address_of(addr)
                    if bare:
                        out.add(bare)
    except Exception:  # noqa: BLE001
        pass
    try:
        for r in mail_rules.active_rules(mail_rules.load(workspace_root)):
            if r.get("off_limits") and r.get("mode") == "hide" and r.get("match") == "sender":
                bare = mail_rules.address_of(r.get("value"))
                if bare:
                    out.add(bare)
    except Exception:  # noqa: BLE001
        pass
    _OFF_LIMITS_CACHE[key[0]] = (key, frozenset(out))
    return out


def off_limits_person_ids(workspace_root, off_limits: Optional[set] = None) -> set:
    """cr1#1181: the people on file whose address is off limits, so a row that
    names its sender only by person id is caught too. Never raises."""
    off = off_limits_senders(workspace_root) if off_limits is None else off_limits
    if not off:
        return set()
    # cr1#1230: read on every append once anyone is off limits, so cached
    # until entities.json changes (or the off-limits set does).
    key = (str(workspace_root), _stamp(Path(str(workspace_root)) / "_hq" / "data" / "entities.json"),
           frozenset(off))
    hit = _OFF_PIDS_CACHE.get(key[0])
    if hit is not None and hit[0] == key:
        return set(hit[1])
    got = _off_limits_person_ids(workspace_root, off)
    _OFF_PIDS_CACHE[key[0]] = (key, frozenset(got))
    return got


_OFF_PIDS_CACHE: dict = {}


def _mail_sources(workspace_root) -> set:
    """cr1#1230: the declared mail connector's server ids and provider, the
    marks a mail-derived row's provenance carries. Never raises."""
    try:
        from connector_config import workspace_block
        conns = workspace_block(workspace_root).get("connectors") or {}
        out = set()
        for key, rec in (conns.items() if isinstance(conns, dict) else ()):
            if not isinstance(rec, dict) or not (key == "email" or rec.get("category") == "email"):
                continue
            for v in [rec.get("server_id"), rec.get("provider")] + list(rec.get("server_ids") or []):
                if v:
                    out.add(str(v))
        return out
    except Exception:  # noqa: BLE001
        return set()


def _off_limits_person_ids(workspace_root, off: set) -> set:
    try:
        import mail_rules
        from connector_config import load_entities
        ent = load_entities(workspace_root)
        inner = ent.get("entities") if isinstance(ent.get("entities"), dict) else ent
        out = set()
        for person in inner.get("people") or []:
            if not isinstance(person, dict) or not person.get("id"):
                continue
            addrs = [person.get("email")] + list(person.get("emails") or [])
            if any(mail_rules.address_of(a) in off for a in addrs if a):
                out.add(str(person["id"]))
        return out
    except Exception:  # noqa: BLE001
        return set()


def sender_off_limits(workspace_root, *, sender_email=None, sender_person_id=None,
                      off_limits: Optional[set] = None, person_ids: Optional[set] = None) -> bool:
    """cr1#1181: one inbound message or capture candidate, by its sender's
    address or the person it resolved to. The upstream check the mail legs
    run before anything is captured, closed or moved. Never raises."""
    try:
        import mail_rules
        off = off_limits_senders(workspace_root) if off_limits is None else off_limits
        if not off:
            return False
        if sender_email and mail_rules.address_of(sender_email) in off:
            return True
        if sender_person_id:
            pids = off_limits_person_ids(workspace_root, off) if person_ids is None else person_ids
            return str(sender_person_id) in pids
    except Exception:  # noqa: BLE001
        return False
    return False


def _from_off_limits_sender(ev: dict, off_limits: set, person_ids: Optional[set] = None,
                            mail_sources: Optional[set] = None) -> bool:
    """cr1#1181: True iff the event's sender (`_sender_of`, a display form
    such as `Name <a@b>` included) is one the owner put off limits. Only for
    connector-derived rows: a chat-stated item (`origin: user_stated`) is the
    owner's own words, and an exempt family (an owner's press, a receipt) is
    not their mail, so neither ever matches. Runs on every book."""
    if not off_limits:
        return False
    if _data(ev).get("origin") == ORIGIN_USER_STATED:
        return False
    try:
        if _classify(ev) == "exempt":
            return False
        import mail_rules
        if mail_rules.address_of(_sender_of(ev) or "") in off_limits:
            return True
        # cr1#1230: a MAIL row that names the person only by id (no address
        # at all) is theirs too, when that person's address is off limits. A
        # row with an address was judged above (the owner's own sent mail
        # carries the owner's), and a row from a meeting or any other source
        # is not their mail: a promise owed to them there is kept.
        if person_ids and mail_sources and _sender_of(ev) is None:
            d = _data(ev)
            prov = d.get("provenance") if isinstance(d.get("provenance"), dict) else {}
            src = {str(v) for v in (prov.get("connector"), prov.get("server_id"), prov.get("provider")) if v}
            if not (src & mail_sources):
                return False
            cp = d.get("counterparty_id") or ev.get("counterparty_id")
            return bool(cp) and str(cp) in person_ids
    except Exception:  # noqa: BLE001
        return False
    return False


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
    populated (R4), a declared connector is personal (cr1#1179, which walls
    that connector's captures on every book), or a sender is off limits
    (cr1#1181, walled on every book too). Defensive: any internal error →
    allow the write."""
    try:
        if workspace_root is None:
            workspace_root = _workspace_root_from_path(path)
        if workspace_root is None:
            return events
        populated = _account_map_populated(workspace_root)
        personal = _personal_connectors(workspace_root)
        off_limits = off_limits_senders(workspace_root)
        off_pids = off_limits_person_ids(workspace_root, off_limits) if off_limits else set()
        mail_sources = _mail_sources(workspace_root) if off_pids else set()
        if not populated and not personal and not off_limits:
            return events
    except Exception:
        return events

    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            continue
        try:
            if _from_personal_connector(ev, personal):
                raise AccountScopeError(
                    f"event {i} type={ev.get('type')!r} was captured from a "
                    f"connector the owner called personal (holder={holder}) — "
                    "a personal source is never read for the business book "
                    "and nothing from it is filed (cr1#1178, cr1#1179). If it "
                    "is a work source, change its role with workspace-manager."
                )
            if _from_off_limits_sender(ev, off_limits, off_pids, mail_sources):
                raise AccountScopeError(
                    f"event {i} type={ev.get('type')!r} is mail from a sender "
                    f"the owner put off limits (holder={holder}) — their mail "
                    "is kept out of filing, capture and the record, not only "
                    "out of the inbox (cr1#1181). To let it in, take the "
                    "sender off the off-limits list."
                )
            if not populated:
                continue
            mode = _classify(ev)
            if mode == "exempt":
                continue
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
        # cr1#1139: `aid` came from the provenance's account id or the
        # mailbox address handed in, so it names the mailbox.
        in_scope = is_in_write_scope(account_id=aid, workspace_root=workspace_root,
                                     mailbox=True)
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
           "off_limits_senders", "off_limits_person_ids", "sender_off_limits",
           "live_masks", "live_masks_from_events", "filter_masked_events",
           "ORIGIN_CONNECTOR", "ORIGIN_USER_STATED"]
