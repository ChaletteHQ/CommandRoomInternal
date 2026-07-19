#!/usr/bin/env python3
"""connector_config — declared backends + compound account map + scope wall.

Real-shaped fixtures per the realdata-fixture gotcha: account records mirror the
ACCOUNT_SCOPE.md §1 compound shape; the workspace block is tested under BOTH the
nested (`entities.workspace`) and flat (`workspace`) entities.json shapes.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import connector_config as cc  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


# Real-shaped account map (M-like: Superhuman + Gmail bind one business mailbox;
# a personal address surfaces but never files; a mixed address).
_ACCOUNTS = [
    {
        "address": "matthew@chaletteholdings.com",
        "account_id": "acct_biz1",
        "role": "business-primary",
        "scope": {"surface": "on", "write_to_business": "on"},
        "bindings": [
            {"server_id": "ec5e0bd5", "provider": "superhuman", "binding_verified": "user_asserted"},
            {"server_id": "f12657a1", "provider": "gmail", "binding_verified": "user_asserted"},
        ],
    },
    {
        "address": "matt@personal.example.com",
        "account_id": "acct_pers",
        "role": "personal",
        "scope": {"surface": "on", "write_to_business": "off"},
    },
    {
        "address": "matt.side@mixed.example.com",
        "account_id": "acct_mixed",
        "role": "mixed",  # dials default: surface on, write off (by-association)
    },
]

_CONNECTORS = {
    "email": {"server_id": "ec5e0bd5", "provider": "superhuman", "label": "Superhuman"},
    "calendar": {"server_id": "f9119bb5", "provider": "google_calendar", "label": "Google Calendar"},
    "_zapier_server_ids": ["4658de3a"],
}


def _nested_ent():
    return {"entities": {"workspace": {"accounts": _ACCOUNTS, "connectors": _CONNECTORS}}}


def _flat_ent():
    return {"workspace": {"accounts": _ACCOUNTS, "connectors": _CONNECTORS}}


def test_shapes():
    for label, ent in (("nested", _nested_ent()), ("flat", _flat_ent())):
        ws = cc.workspace_block(entities=ent)
        check(f"{label}: workspace block resolves", bool(ws.get("accounts")))
        check(f"{label}: declared email backend", cc.declared_backend("email", entities=ent)["provider"] == "superhuman")
        check(f"{label}: accounts len 3", len(cc.accounts(entities=ent)) == 3)


def test_scope_wall():
    ent = _nested_ent()
    check("business-primary writes", cc.is_in_write_scope(address="matthew@chaletteholdings.com", entities=ent))
    check("personal write BLOCKED", not cc.is_in_write_scope(address="matt@personal.example.com", entities=ent))
    check("personal surfaceable", cc.is_surfaceable(address="matt@personal.example.com", entities=ent))
    check("mixed surfaces by default", cc.is_surfaceable(address="matt.side@mixed.example.com", entities=ent))
    check("mixed does NOT write by default", not cc.is_in_write_scope(address="matt.side@mixed.example.com", entities=ent))
    # by account_id
    check("scope resolvable by account_id", not cc.is_in_write_scope(account_id="acct_pers", entities=ent))


def test_connectors_absent_accounts_present():
    # Review should-fix: a workspace can classify ACCOUNTS before declaring any
    # BACKEND (`workspace.connectors` absent, `workspace.accounts` present) —
    # scope reads must work and backend reads must degrade to None (substring
    # fallback), never raise.
    ent = {"entities": {"workspace": {"accounts": _ACCOUNTS}}}
    check("connectors absent: declared_backend -> None (fallback)",
          cc.declared_backend("email", entities=ent) is None)
    check("connectors absent: declared_backends -> {}",
          cc.declared_backends(entities=ent) == {})
    check("connectors absent: zapier ids -> []",
          cc.zapier_server_ids(entities=ent) == [])
    check("connectors absent: account map still populated",
          cc.account_map_populated(entities=ent))
    check("connectors absent: personal write still BLOCKED",
          not cc.is_in_write_scope(address="matt@personal.example.com", entities=ent))
    check("connectors absent: business still writes",
          cc.is_in_write_scope(address="matthew@chaletteholdings.com", entities=ent))


def test_role_default_dials_via_setter():
    # Review fix 1: classifying `personal` WITHOUT explicit dials must land on
    # the role default — BOTH dials OFF (ACCOUNT_SCOPE §1) — because the verbs
    # no longer pass dials for role defaults.
    ws = Path(tempfile.mkdtemp(prefix="cc_dials_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "entities.json").write_text(
        json.dumps({"version": 1, "entities": {"people": [], "workspace": {}}}),
        encoding="utf-8")
    cc.set_account_classification(ws, "just.role@personal.example.com", role="personal")
    check("fix1: personal role default -> surface OFF",
          not cc.is_surfaceable(address="just.role@personal.example.com", workspace_root=ws))
    check("fix1: personal role default -> write OFF",
          not cc.is_in_write_scope(address="just.role@personal.example.com", workspace_root=ws))
    # Explicit non-default override still honored (user opted senders/surface in).
    cc.set_account_classification(ws, "opted@personal.example.com", role="personal", surface=True)
    check("fix1: explicit surface=True override honored",
          cc.is_surfaceable(address="opted@personal.example.com", workspace_root=ws))
    check("fix1: override leaves write OFF",
          not cc.is_in_write_scope(address="opted@personal.example.com", workspace_root=ws))


def test_empty_map_is_in_scope():
    # R4 — empty map = today's behavior: everything in scope, wall is a no-op.
    check("empty map: write in scope", cc.is_in_write_scope(address="whoever@x.com", entities={}))
    check("empty map: surfaceable", cc.is_surfaceable(address="whoever@x.com", entities={}))
    check("empty map: account_map_populated False", not cc.account_map_populated(entities={}))


def test_unknown_in_populated_map_backcompat():
    # ACCOUNT_SCOPE §4c — an unknown account in a populated map is NOT rejected
    # (already-flowing provenance predates the wall). Fail-closed-on-new is at
    # the scan/surfacing layer, not here.
    ent = _nested_ent()
    check("unknown account write allowed (back-compat)", cc.is_in_write_scope(address="stranger@new.example.com", entities=ent))


def test_zapier_pinning():
    ent = _nested_ent()
    check("zapier server pinned by id", cc.is_zapier_server("4658de3a", entities=ent))
    check("non-zapier server not flagged", not cc.is_zapier_server("f12657a1", entities=ent))
    check("zapier_server_ids list", cc.zapier_server_ids(entities=ent) == ["4658de3a"])


def test_server_id_of():
    check("server_id parse", cc.server_id_of("mcp__ec5e0bd5__create_or_update_draft") == "ec5e0bd5")
    check("server_id parse op with __", cc.server_id_of("mcp__abc__get_read_status_feed") == "abc")
    check("server_id parse junk -> None", cc.server_id_of("not-a-tool-id") is None)


def test_derive_account_id_stable():
    a = cc.derive_account_id("Matthew@Chaletteholdings.com ")
    b = cc.derive_account_id("matthew@chaletteholdings.com")
    check("account_id derivation case/space-stable (R3)", a == b and a.startswith("acct_"))


def test_binding_routing():
    ent = _nested_ent()
    b = cc.binding_for_address("matthew@chaletteholdings.com", entities=ent)
    check("binding resolves to a server (routing keys on binding)", b and b.get("server_id") == "ec5e0bd5")


def test_setter_roundtrip():
    ws = Path(tempfile.mkdtemp(prefix="cc_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "entities.json").write_text(
        json.dumps({"version": 1, "entities": {"people": [], "workspace": {"user_first_name": "Matthew"}}}),
        encoding="utf-8")
    # declare a backend
    cc.set_declared_backend(ws, "email", "ec5e0bd5", provider="superhuman", label="Superhuman")
    cc.set_declared_backend(ws, "email", "4658de3a", is_zapier=True)  # pin zapier
    check("setter wrote declared backend", cc.declared_backend("email", ws)["server_id"] == "ec5e0bd5")
    check("setter pinned zapier by id", cc.is_zapier_server("4658de3a", ws))
    # classify an account
    cc.set_account_classification(ws, "matt@personal.example.com", role="personal", surface=True, write_to_business=False)
    check("setter classified personal (write blocked)", not cc.is_in_write_scope(address="matt@personal.example.com", workspace_root=ws))
    check("setter classified personal (surfaceable)", cc.is_surfaceable(address="matt@personal.example.com", workspace_root=ws))
    # reclassify in place (personal -> business restore)
    cc.set_account_classification(ws, "matt@personal.example.com", role="business-secondary", write_to_business=True)
    check("reclassify updates in place (now writes)", cc.is_in_write_scope(address="matt@personal.example.com", workspace_root=ws))
    check("no duplicate account record on reclassify",
          sum(1 for a in cc.accounts(workspace_root=ws) if a["address"] == "matt@personal.example.com") == 1)
    # version bumped (concurrency guard)
    ent = json.loads((ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))
    check("version bumped by setter", int(ent.get("version", 0)) >= 4)
    # entities.json still valid JSON + people preserved
    check("people collection preserved through setter writes",
          isinstance(ent["entities"]["people"], list))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_shapes()
    test_scope_wall()
    test_connectors_absent_accounts_present()
    test_role_default_dials_via_setter()
    test_empty_map_is_in_scope()
    test_unknown_in_populated_map_backcompat()
    test_zapier_pinning()
    test_server_id_of()
    test_derive_account_id_stable()
    test_binding_routing()
    test_setter_roundtrip()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL connector_config tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
