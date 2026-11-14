#!/usr/bin/env python3
"""What's new, set up once on an existing seat (cr1#1235, `set_up_whats_new_v1`).

An existing workspace never re-runs onboarding (`restart onboarding` is
refused on a seat with data), so two onboarding steps never reached it:

  step 1  what each connector and mail account is for, kept as its role
          (cr1#1163 part 1: a connector or account marked personal is never
          read);
  step 5  the confirm cards (cr1#1163 part 2): name, sign-off, the business
          line, the people never to keep waiting, the two words, the
          priorities, the morning time, the never line, closing caution,
          off-limits senders, draft posture.

The update bridge offers both once; "set up what's new", "walk my
connectors" or "confirm my settings" runs them on demand. Nothing already
set is asked again, and onboarding is not redone: the cards are built from
the workspace's existing record, not a fresh pull.

  plan_whats_new  READ. The connectors and accounts with no role, the cards
                  whose setting does not exist yet (`onboarding_confirm.
                  plan_confirm` with its `set` flags), and whether there is
                  anything to ask at all.
  apply_walk      A WRITER. The walk's answers: each connector's role through
                  `connector_config.set_connector_settings`, each account's
                  through `connector_config.set_account_classification` plus
                  its `account_classified` row. Every answer is checked first;
                  a refused one writes nothing anywhere.

The cards are saved by `onboarding_confirm:apply_confirm`, the same writer
step 5 uses. Nothing here reads a connector: the walk asks the owner, and a
source they mark personal is never read afterwards (`connector_reads`).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

MIGRATION_ID = "set_up_whats_new_v1"
SOURCE_SKILL = "command-room-update-bridge"
#: said when there is nothing to ask, on the offer and on the phrase
NOTHING_LINE = ("Everything this setup asks about is already set here, so "
                "there is nothing to ask.")
#: account roles that mean "nobody said yet"
_UNSET_ROLES = ("", "unclassified")


def _role_unset(role) -> bool:
    return str(role or "").strip().lower() in _UNSET_ROLES


def plan_whats_new(workspace_root, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """READ. `{connectors: [{category, label, kind}], accounts: [{address}],
    cards: [plan_confirm items whose setting is unset], n_set, pending,
    line}`. Writes nothing; never raises."""
    root = Path(str(workspace_root))
    connectors: List[Dict[str, Any]] = []
    accounts: List[Dict[str, Any]] = []
    try:
        import connector_config as cc
        for cat, row in cc.declared_backends(root).items():
            if cat == cc._ZAPIER_PIN_KEY or not isinstance(row, dict) \
                    or not row.get("server_id"):
                continue
            # a connector whose kind resolves to nothing cannot take a role
            # (its writer refuses), so it is not asked about (review R4)
            if cc.kind_for_category(str(cat), row) is None:
                continue
            if _role_unset(row.get("role")):
                rec = cc.connector_settings(str(cat), root) or {}
                connectors.append({"category": str(cat),
                                   "label": row.get("label") or str(cat),
                                   "kind": rec.get("kind")})
        for rec in cc.accounts(root):
            addr = str(rec.get("address") or "").strip()
            if addr and _role_unset(rec.get("role")):
                accounts.append({"address": addr})
    except Exception:  # noqa: BLE001
        pass
    cards: List[Dict[str, Any]] = []
    n_set = 0
    timezone = None
    try:
        import onboarding_confirm as oc
        plan = oc.plan_confirm(root, now_iso)
        timezone = plan.get("timezone")
        for item in plan.get("items") or []:
            if item.get("set"):
                n_set += 1
            else:
                cards.append(item)
    except Exception:  # noqa: BLE001
        pass
    pending = bool(connectors or accounts or cards)
    return {"connectors": connectors, "accounts": accounts, "cards": cards,
            "n_set": n_set, "timezone": timezone, "pending": pending,
            "line": None if pending else NOTHING_LINE}


def _plain(cc, role: str) -> str:
    """The role in the product's plain words (`connector_config`'s table)."""
    return getattr(cc, "_ROLE_IN_ENGLISH", {}).get(role) or role


def _refuse(line: str) -> Dict[str, Any]:
    return {"ok": False, "reason": "refused", "line": line + " Nothing was saved."}


def apply_walk(workspace_root, connectors: Optional[Dict[str, str]] = None,
               accounts: Optional[Dict[str, str]] = None, *,
               now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER. The walk's answers: `connectors` is `{category: role}`,
    `accounts` is `{address: role}`, in the account role words. Every answer
    is checked before anything is written; one that would be refused stops
    the call with its line and nothing is written. Answers
    `{ok, applied: [{id, line}], failed: [...]}`."""
    import connector_config as cc
    root = Path(str(workspace_root))
    connectors = {str(k).strip(): str(v or "").strip().lower()
                  for k, v in (connectors or {}).items()}
    accounts = {str(k).strip().lower(): str(v or "").strip().lower()
                for k, v in (accounts or {}).items()}
    if not connectors and not accounts:
        return {"ok": False, "reason": "nothing_to_save",
                "line": "No answer was given, so nothing changed."}
    roles = ", ".join(cc.CONNECTOR_ROLES)
    rows_by_cat = {str(k): row for k, row in cc.declared_backends(root).items()
                   if isinstance(row, dict) and row.get("server_id")}
    declared = set(rows_by_cat)
    label_of = {k: row.get("label") for k, row in rows_by_cat.items()}
    for cat, role in connectors.items():
        if cat not in declared or cat == cc._ZAPIER_PIN_KEY:
            return _refuse(f"There is no connector set up for {cat}.")
        if cc.kind_for_category(cat, rows_by_cat[cat]) is None:
            return _refuse(f"{label_of.get(cat) or cat} is not a kind of connector "
                           "this can set a role on.")
        if role not in cc.CONNECTOR_ROLES:
            return _refuse(f"A connector's role is one of: {roles}.")
    known = {str(r.get("address") or "").strip().lower(): r for r in cc.accounts(root)}
    for addr, role in accounts.items():
        if addr not in known:
            return _refuse("That address is not one of the mail accounts on file.")
        if not _role_unset(known[addr].get("role")):
            # a change of role is workspace-manager's, with its own rows
            # (review R5); this setup only fills what nobody has said
            return _refuse(f"{known[addr].get('address') or addr} already has a role; "
                           "change it by saying so in one sentence.")
        if role not in cc.CONNECTOR_ROLES:
            return _refuse(f"An account's role is one of: {roles}.")

    applied: List[Dict[str, str]] = []
    failed: List[Dict[str, str]] = []
    for cat, role in connectors.items():
        try:
            out = cc.set_connector_settings(root, cat, role=role, holder=SOURCE_SKILL)
            if isinstance(out, dict) and out.get("ok") is False:
                raise ValueError(out.get("line") or "refused")
            applied.append({"id": f"connector:{cat}",
                            "line": f"{label_of.get(cat) or cat}: {_plain(cc, role)}."})
        except Exception as exc:  # noqa: BLE001 - name it, keep going
            failed.append({"id": f"connector:{cat}",
                           "line": f"{cat} was not saved: {str(exc)[:160] or type(exc).__name__}"})
    rows = []
    for addr, role in accounts.items():
        try:
            rec = cc.set_account_classification(root, known[addr].get("address") or addr,
                                                role=role, holder=SOURCE_SKILL)
            shown = known[addr].get("address") or addr
            try:
                scope = cc.account_scope(shown, None, root) or {}
            except Exception:  # noqa: BLE001
                scope = {}
            rows.append({"type": "account_classified", "source_skill": SOURCE_SKILL,
                         "data": {"address": shown, "role": role,
                                  "account_id": (rec or {}).get("account_id")
                                  or known[addr].get("account_id"),
                                  "surface": scope.get("surface"),
                                  "write_to_business": scope.get("write_to_business")}})
            applied.append({"id": f"account:{addr}",
                            "line": f"{known[addr].get('address') or addr}: {_plain(cc, role)}."})
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": f"account:{addr}",
                           "line": f"{addr} was not saved: {str(exc)[:160] or type(exc).__name__}"})
    if rows:
        try:
            from event_gate import append_event
            append_event(root / "_hq" / "data" / "events.jsonl", rows, holder=SOURCE_SKILL)
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": "account_events",
                           "line": f"the account record was saved but not logged: {str(exc)[:160]}"})
    return {"ok": not failed, "applied": applied, "failed": failed}


__all__ = ["MIGRATION_ID", "NOTHING_LINE", "plan_whats_new", "apply_walk"]
