#!/usr/bin/env python3
"""connector_setup: the server side of the Hub's Connectors panel (cr1#1877
slice 1; spec §3 to §5, owner rulings 150 and 151).

The page does choices, consent and test calls only. What it writes
(`config/connector_tests`) is judged here before anything reaches the record:

- `setup_doc(ws, servers, hub_address)`: what set-up puts in
  `config/connector_setup` for the panel: per job, the candidate connectors
  (display name, server id, provider key, the read-only test tool and the
  identity tool) and the work accounts as HASHES only (`address_h`), so the
  page can tell which work account a connector is signed in as without being
  handed an address.
- `apply_setup(ws, tests_doc, setup)`: the page's choices and test results,
  re-checked (the closed grammar, the read map, the candidates, the Hub page
  it was made on, the account's role) and written through
  `connector_config.set_connector_job`.
- `panel_feed(ws, hub_address)`: one plain row per job for the panel.
- `calendar_live_allowed(ws, provider)`: ruling 151e, a seat's own successful
  calendar test made on THIS Hub page switches an unverified live plan on.
- `identity_gate(ws, category, provider, addresses)`: the check before each
  work write. The code decides; any prose that calls it only obeys its answer.

Nothing here calls a connector or stores what one answered beyond its shape.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import connector_config as cc  # noqa: E402
import connector_jobs as cj  # noqa: E402

#: The jobs whose rows the panel switches on; each slice adds its own.
ENABLED_JOBS = ("calendar",)
#: Every job the panel lists (the frame is built for all six in slice 1).
PANEL_JOBS = ("calendar", "email", "chat", "meetings", "documents", "personal_calendar")
#: The identity tool per provider (connector_jobs.IDENTITY), named for the page's manifest.
SETUP_DOC = "config/connector_setup"
TESTS_DOC = "config/connector_tests"
ARTIFACT_LINE = ("You are allowing these connectors for this Hub page ({page}). "
                 "Another page, or a new Hub, asks again.")
NOT_ON_PAGE_LINE = cc.NOT_APPROVED_ON_PAGE_LINE
_SID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
#: Why a job was not saved, in the owner's words (review of 1e3899d05, M1).
WHY_WORDS = {
    "test_failed": "the test did not work; press Test again on the Hub",
    "test_needs_approval": "the page was not allowed to read it yet; press Test again and allow it",
    "test_empty": "the test read nothing it could check; press Test again",
    "test_account_mismatch": "it is signed in as an account that is not a work account",
    "identity_cannot_check": "which account it is signed in as could not be checked; press Test again",
    "identity_mismatch": "it is signed in as an account that is not a work account",
    "no_work_account": "no work account matched",
    "stale_test": "that test is older than the one already saved",
    "other_page": "the test was made on another Hub page",
    "not_a_candidate": "that connector was not offered for it",
    "test_outside_grammar": "the test could not be read",
    "other": "nothing was changed",
}
#: The manifest's account argument per provider: a write through such a
#: connector names the account it acts as (review of 1e3899d05, M3).
def acting_key(provider: str) -> Optional[str]:
    try:
        from connector_adapters import capabilities as cap
        row = cap.provider_row(str(provider or "")) or {}
        return row.get("account_param") or None
    except Exception:  # noqa: BLE001
        return None
GATE_LINES = {
    "match": "",
    "mismatch": "{job} is signed in as an account that is not the one you confirmed, so nothing was changed.",
    "cannot_check": "Command Room could not check which account {job} is signed in as, so nothing was changed.",
    "no_job": "{job} is not set up on the Hub, so nothing was changed.",
}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def hub_address(workspace_root) -> str:
    """The Hub page set-up published to, as recorded in the folder; '' when none."""
    try:
        import cockpit_helpers as ch
        cfg = ch.load_config(workspace_root)
        return str(cfg.get("url") or "").strip() if cfg.get("found") else ""
    except Exception:  # noqa: BLE001
        return ""


def _work_accounts(workspace_root, category: str) -> List[Dict[str, Any]]:
    import hub_live
    allowed = cc.PERSONAL_JOB_ROLES if category == "personal_calendar" else cc.WORK_ROLES
    out = []
    for a in cc.accounts(workspace_root):
        if not isinstance(a, dict) or str(a.get("role") or "") not in allowed or not a.get("address"):
            continue
        out.append({"account_id": str(a.get("account_id") or cc.derive_account_id(a.get("address"))),
                    "address_h": hub_live.digest(cc._norm_addr(a.get("address"))),
                    "role": str(a.get("role"))})
    return out


def setup_doc(workspace_root, servers: Iterable[Dict[str, Any]], hub: Optional[str] = None) -> Dict[str, Any]:
    """`config/connector_setup` for the panel. `servers` is what the set-up chat
    sees: `[{server_id, display_name, tools: [tool names]}]`. Each job lists
    the servers whose tools fingerprint as one of its providers
    (`connector_jobs.provider_of`), with that provider's read-only test tool
    and identity tool. Accounts go as hashes only."""
    jobs: Dict[str, Any] = {}
    srv = [s for s in servers or [] if isinstance(s, dict)]
    for cat in PANEL_JOBS:
        cands = []
        for s in srv:
            sid, name = str(s.get("server_id") or ""), str(s.get("display_name") or "")
            if not _SID_RE.fullmatch(sid) or not cj._NAME_RE.match(name):
                continue
            prov = cj.provider_of(list(s.get("tools") or []), cat)
            test_tool = (cj.READ_TOOLS.get(cat) or {}).get(str(prov or ""))
            if not prov or not test_tool:
                continue
            ident = (cj.IDENTITY.get(prov) or {}).get("tool")
            cands.append({"server_id": sid, "display_name": name, "provider_key": prov,
                          "test_tool": test_tool, "identity_tool": ident})
        jobs[cat] = {"enabled": cat in ENABLED_JOBS, "word": cc.JOB_WORDS.get(cat, cat),
                     "candidates": cands, "accounts": _work_accounts(workspace_root, cat)}
    page = str(hub if hub is not None else hub_address(workspace_root))
    return {"version": 1, "hub": page, "line": ARTIFACT_LINE.format(page=page or "this page"), "jobs": jobs}


def apply_setup(workspace_root, tests_doc: Any, setup: Any) -> Dict[str, Any]:
    """The page's choices and tests, judged and written (spec §3). `tests_doc`
    is `config/connector_tests` as the page wrote it, `setup` the
    `config/connector_setup` the page was shown. Only enabled jobs; a choice
    must be one of that job's candidates; a test must pass the closed grammar
    and have been made on the Hub page set-up recorded; the account is the
    work account the page's identity look matched. Answers `{applied,
    skipped, refused: [{category, why}], line}`."""
    applied: List[str] = []
    skipped: List[str] = []
    kept: List[str] = []
    refused: List[Dict[str, str]] = []
    jobs = (tests_doc or {}).get("jobs") if isinstance(tests_doc, dict) else None
    sjobs = (setup or {}).get("jobs") if isinstance(setup, dict) else None
    if not isinstance(jobs, dict) or not isinstance(sjobs, dict):
        return {"applied": [], "skipped": [], "refused": [{"category": "*", "why": "unreadable"}],
                "line": "The Hub's connector choices could not be read, so nothing changed."}
    here = hub_address(workspace_root)
    for cat in ENABLED_JOBS:
        entry = jobs.get(cat)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            refused.append({"category": cat, "why": "unreadable"})
            continue
        if entry.get("skip") is True:
            if cc._declared_backend_raw(cat, workspace_root):
                cc.set_connector_job(workspace_root, cat, {"state": "skipped", "origin": "panel",
                                                          "set_at": _now(), "set_by": "hub_panel"},
                                     reason="panel_skip", event=cc.JOB_CLEARED_EVENT)
            skipped.append(cat)
            continue
        choice = entry.get("choice") if isinstance(entry.get("choice"), dict) else {}
        cands = (sjobs.get(cat) or {}).get("candidates") or []
        cand = next((c for c in cands if isinstance(c, dict) and c.get("server_id") == choice.get("server_id")
                     and c.get("provider_key") == choice.get("provider_key")), None)
        if cand is None:
            refused.append({"category": cat, "why": "not_a_candidate"})
            continue
        test = entry.get("test") if isinstance(entry.get("test"), dict) else None
        if test is None or not cj.valid_test_entry(test, cat, cand["provider_key"]):
            refused.append({"category": cat, "why": "test_outside_grammar"})
            continue
        if not here or test.get("artifact") != here:
            refused.append({"category": cat, "why": "other_page"})
            continue
        # NIT: the page-writable set-up document is re-checked before anything reaches the book
        if not (_SID_RE.fullmatch(str(cand.get("server_id") or "")) and cj._NAME_RE.match(str(cand.get("display_name") or ""))
                and cand.get("provider_key") in (cj.READ_TOOLS.get(cat) or {})):
            refused.append({"category": cat, "why": "not_a_candidate"})
            continue
        # review of 1e3899d05, SHOULD 4: a test no newer than the one on record is stale
        raw = cc._declared_backend_raw(cat, workspace_root) or {}
        prior = raw.get(cc.JOB_KEY) if isinstance(raw.get(cc.JOB_KEY), dict) else {}
        prior_at = str(((prior.get("test") or {}) if isinstance(prior.get("test"), dict) else {}).get("at") or "")
        if prior_at and str(test.get("at") or "") == prior_at:
            kept.append(cat)   # fix check S5: a second "done" on the same test: already saved
            continue
        if prior_at and str(test.get("at") or "") < prior_at:
            refused.append({"category": cat, "why": "stale_test"})
            continue
        # review of 1e3899d05, M1 (spec §5 M5, 151a): only a test that worked, whose
        # identity look matched a work account, confirms a job. Anything else changes
        # nothing on the record: the seat's calendar keeps working as it did.
        if test.get("result") != "ok":
            refused.append({"category": cat, "why": "test_" + str(test.get("result") or "unknown")})
            continue
        if test.get("identity") != "match":
            refused.append({"category": cat, "why": "identity_" + str(test.get("identity") or "unknown")})
            continue
        acct = next((a for a in _work_accounts(workspace_root, cat)
                     if a["account_id"] == str(entry.get("account_id") or "")), None)
        if acct is None:
            refused.append({"category": cat, "why": "no_work_account"})
            continue
        approval = str(entry.get("approval") or "")
        block: Dict[str, Any] = {"state": "set", "origin": "panel", "provider_key": cand["provider_key"],
                                 "display_name": cand["display_name"],
                                 "accounts": [{"account_id": acct["account_id"], "address_h": acct["address_h"],
                                               "role": acct["role"], "confirmed_at": _now()}],
                                 "approval": {"page": approval if approval in cj.PAGE_STATES else "prompt",
                                              "seen_at": _now(), "artifact": here},
                                 "test": dict(test), "set_at": _now(), "set_by": "hub_panel"}
        block["test"]["passed_at"] = str(test.get("at"))   # SHOULD 4: when it passed, not when applied
        if not raw or raw.get("server_id") != cand["server_id"]:
            cc.set_declared_backend(workspace_root, cat, cand["server_id"], provider=cand["provider_key"],
                                    label=cand["display_name"])
        try:
            written = cc.set_connector_job(workspace_root, cat, block, reason="panel_test", event=cc.JOB_TESTED_EVENT)
        except ValueError:
            refused.append({"category": cat, "why": "not_a_job_block"})
            continue
        cc._job_event(workspace_root, cc.JOB_APPROVAL_EVENT, cat, written, "page_" + block["approval"]["page"])
        applied.append(cat)
    words = [cc.JOB_WORDS.get(c, c) for c in applied]
    line = ("Saved: " + ", ".join(words) + "." if words else "") + \
        (" Already saved: " + ", ".join(cc.JOB_WORDS.get(c, c) for c in kept) + "." if kept else "") + \
        (" Not saved: " + "; ".join(cc.JOB_WORDS.get(r["category"], r["category"]) + ", "
                                    + WHY_WORDS.get(r["why"], WHY_WORDS["other"]) for r in refused) + "."
         if refused else "")
    return {"applied": applied, "skipped": skipped, "kept": kept, "refused": refused, "line": line.strip()}


def panel_feed(workspace_root, hub: Optional[str] = None) -> Dict[str, Any]:
    """One plain row per job for the panel: its state, its connector's name,
    whether it is approved on THIS page, and its last test's result."""
    page = str(hub if hub is not None else hub_address(workspace_root))
    rows = []
    for cat in PANEL_JOBS:
        raw = cc._declared_backend_raw(cat, workspace_root) or {}
        block = raw.get(cc.JOB_KEY) if isinstance(raw.get(cc.JOB_KEY), dict) else {}
        read = cc.connector_job(cat, workspace_root)
        approval = cc.page_approval(cat, page, workspace_root) if raw else "not_needed"
        test = block.get("test") if isinstance(block.get("test"), dict) else {}
        confirmed = block.get("origin") in ("panel", "chat") and block.get("state") == "set"
        rows.append({"category": cat, "word": cc.JOB_WORDS.get(cat, cat), "enabled": cat in ENABLED_JOBS,
                     "provider_key": block.get("provider_key") or raw.get("provider") or "",
                     "accounts_h": ([str(a.get("address_h")) for a in block.get("accounts") or []
                                     if isinstance(a, dict) and a.get("address_h")] if confirmed else []),
                     "state": ("never_set" if read is None else "refused" if cc.is_refused(read)
                               else (block.get("state") or "legacy")),
                     "line": read.get("line") if cc.is_refused(read) else "",
                     "display_name": block.get("display_name") or raw.get("label") or "",
                     "approval_here": approval,
                     "approval_line": NOT_ON_PAGE_LINE if approval == cc.PAGE_NEEDS_APPROVAL and raw else "",
                     "test": test.get("result") or ""})
    import cockpit_helpers as ch
    return {"hub": page, "line": ARTIFACT_LINE.format(page=page or "this page"), "rows": rows,
            "unconfirmed_line": ch.unconfirmed_line(workspace_root, jobs=ENABLED_JOBS)}


def page_config(workspace_root, servers: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The Connectors panel's config for the page (`CFG.connectors`): the
    panel rows, the Hub page, the identity tool per provider and how the page
    reads its answer, and `tools`: the read-only test and identity tools of
    each enabled job's candidates, which the page's `mcp` manifest declares.
    No address and no account: the candidates and the hashed work accounts
    ride in `config/connector_setup` (owner only)."""
    feed = panel_feed(workspace_root)
    tools: Dict[str, List[str]] = {}
    ident_tools: Dict[str, List[str]] = {}
    if servers:
        doc = setup_doc(workspace_root, servers, feed["hub"])
        for cat, job in doc["jobs"].items():
            if not job.get("enabled"):
                continue
            for c in job.get("candidates") or []:
                t = tools.setdefault(c["display_name"], [])
                for name in (c.get("test_tool"), c.get("identity_tool")):
                    if name and name not in t:
                        t.append(name)
    # a confirmed job's own connector carries its identity tool, so the page
    # can check who it is signed in as before each write (spec §5), declared
    # under the SAME name the page's calendar plans call (review SHOULD 7)
    for row in feed["rows"]:
        prov = row.get("provider_key")
        ident = (cj.IDENTITY.get(str(prov or "")) or {}).get("tool")
        if row.get("accounts_h") and ident:
            row["server"] = _page_server(workspace_root, row["category"]) or row.get("display_name") or ""
            if row["server"]:
                for bag in (tools, ident_tools):
                    t = bag.setdefault(row["server"], [])
                    if ident not in t:
                        t.append(ident)
    # Only what the page uses (the recipe answer must fit one reply, HUBPUB1):
    # each row's non-empty fields, and the identity tools and test arguments
    # of the providers this seat actually has.
    provs = {r.get("provider_key") for r in feed["rows"] if r.get("provider_key")}
    if servers:
        provs |= {c["provider_key"] for job in setup_doc(workspace_root, servers, feed["hub"])["jobs"].values()
                  for c in job.get("candidates") or []}
    # review M4: the static wording (the artifact line, the gate's lines, the
    # not-approved line) and the shape keys are the page's own constants, pinned
    # equal to these; only what varies per seat rides here. A row the panel
    # does not switch on yet shows its word and state only.
    # Only the switched-on jobs' rows; the others, the identity tools, the
    # account keys and the test arguments are the page's own constants
    # (`page_constants`, pinned equal by CONNSETUP1).
    keep = ("category", "state", "display_name", "provider_key", "accounts_h", "server", "line", "test")
    rows = [dict({k: r[k] for k in keep if r.get(k)},
                 **({"here": 1} if r.get("approval_here") == "granted" else {}))
            for r in feed["rows"] if r.get("enabled")]
    out = {"hub": feed["hub"], "rows": rows,
           "tools": [{"server": k, "tools": v} for k, v in sorted(tools.items())],
           "identity_tools": [{"server": k, "tools": v} for k, v in sorted(ident_tools.items())]}
    if feed.get("unconfirmed_line"):
        out["unconfirmed_line"] = feed["unconfirmed_line"]
    return out


def page_constants() -> Dict[str, Any]:
    """What the page carries as its own constants for the panel (review M4),
    the same for every seat: CONNSETUP1 pins the page's copy equal to this."""
    return {"artifact_line": ARTIFACT_LINE, "not_on_page_line": NOT_ON_PAGE_LINE,
            "gate_lines": {k: v for k, v in GATE_LINES.items() if v},
            "shape_keys": {c: sorted(cj.SHAPE_KEYS.get(c, ())) for c in ENABLED_JOBS},
            "identity": {p: v.get("tool") for p, v in sorted(cj.IDENTITY.items())},
            "acting": {p: acting_key(p) for p in sorted(cj.IDENTITY) if acting_key(p)},
            "test_args": {c: v for c, v in _test_args().items() if c in ENABLED_JOBS},
            "words": {c: cc.JOB_WORDS.get(c, c) for c in PANEL_JOBS}, "enabled": list(ENABLED_JOBS)}


def _page_server(workspace_root, category: str) -> str:
    """The connector name the page's own plans call for `category`, so a tool
    declared for the panel is declared where the page will call it."""
    try:
        import cockpit_helpers as ch
        if category == "calendar":
            plan = ch.calendar_direct(workspace_root) or ch.calendar_actions(workspace_root, include_unverified=True) or {}
            return str(plan.get("server") or "")
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _test_args() -> Dict[str, Dict[str, Any]]:
    """How the page asks each calendar provider's test tool for the next
    seven days, one event (spec §4): the argument names from the plans the
    page already reads with (`CALENDAR_LIVE`, `CALENDAR_ACTIONS`)."""
    import cockpit_helpers as ch
    cal: Dict[str, Dict[str, Any]] = {}
    for prov, plan in ch.CALENDAR_LIVE.items():
        cal[prov] = {"from": plan.get("from_key"), "to": plan.get("to_key"), "tz": plan.get("tz_key"),
                     "size": plan.get("size_key"), "time": plan.get("time"),
                     "args": {k: v for k, v in (plan.get("args") or {}).items() if k != plan.get("size_key")}}
    free = ((ch.CALENDAR_ACTIONS.get("superhuman") or {}).get("free") or {})
    if free:
        cal["superhuman"] = {"from": free.get("from_key"), "to": free.get("to_key"), "tz": free.get("tz_key"),
                             "time": "rfc3339",
                             "args": dict(free.get("args") or {}, **{free.get("minutes_key") or "duration_minutes": 30})}
    return {"calendar": cal}


def calendar_live_allowed(workspace_root, provider: str) -> bool:
    """Ruling 151e: True only when this seat's own calendar test, made on the
    Hub page set-up recorded, came back `ok`, named the plan's list tool,
    sent every argument the plan sends, and answered with a list of events.
    No recorded test, a test from another page, or any mismatch: False."""
    try:
        import cockpit_helpers as ch
        plan = ch.CALENDAR_LIVE.get(str(provider or ""))
    except Exception:  # noqa: BLE001
        return False
    if not plan:
        return False
    job = cc.connector_job("calendar", workspace_root)
    if not isinstance(job, dict) or cc.is_refused(job) or not isinstance(job.get("job"), dict):
        return False
    block = job["job"]
    if block.get("origin") not in ("panel",) or block.get("provider_key") != provider:
        return False
    test = block.get("test") if isinstance(block.get("test"), dict) else {}
    here = hub_address(workspace_root)
    if test.get("result") != "ok" or not here or test.get("artifact") != here or test.get("tool") != plan["list"]:
        return False
    # review of 1e3899d05, M2: a first-page read sends no page argument (the page's
    # cnWindow never does), so the plan's `page_in` is not asked of the test
    need = {plan.get(k) for k in ("from_key", "to_key", "size_key") if plan.get(k)} | set(plan.get("args") or {})
    if not need <= set(test.get("args_keys") or []):
        return False
    shape = test.get("shape") if isinstance(test.get("shape"), dict) else {}
    if not cj.valid_shape(shape, "calendar"):
        return False
    lists = [v for k, v in (shape.get("k") or {}).items() if k in ("value", "items", "events") and v.get("t") == "arr"]
    return bool(lists) or shape.get("t") == "arr"


def identity_plan(workspace_root, category: str) -> Dict[str, Any]:
    """Which identity tool to call before a work write on `category`'s job,
    so no prose names a product: `{provider, tool}`, `tool` None when the
    job's provider has no identity entry (then `identity_gate` answers
    cannot_check for a confirmed job, and a migrated one writes as before)."""
    raw = cc._declared_backend_raw(category, workspace_root) or {}
    block = raw.get(cc.JOB_KEY) if isinstance(raw.get(cc.JOB_KEY), dict) else {}
    prov = str(block.get("provider_key") or raw.get("provider") or "")
    return {"provider": prov, "tool": (cj.IDENTITY.get(prov) or {}).get("tool"), "acting_key": acting_key(prov)}


def identity_gate(workspace_root, category: str, provider: str, addresses: Optional[Iterable[str]] = None,
                  answer: Any = None) -> Dict[str, Any]:
    """The check before a work write (spec §5): `{ok, verdict, line}`. The
    connector's own answer about who it is signed in as (`addresses`, read
    by `connector_jobs.identities_from_answer`) is hashed and compared with
    the job's confirmed accounts. A confirmed job writes only on `match`; a
    migrated or legacy draft writes as before and only reports (151a); a
    refused or unset job writes nothing."""
    word = cc.JOB_WORDS.get(category, category)
    job = cc.connector_job(category, workspace_root, purpose="write")
    if job is None:
        # fix check N1 (151a): a seat with no row for this job writes as it does
        # today; the gate only reports, as for a migrated draft
        return {"ok": True, "verdict": "no_job", "line": "", "reported": True, "acting_key": None, "acting": None}
    if cc.is_refused(job):
        # 155s (151a): a foreign scheduled run on a row with no confirmed account
        # (a legacy row or a migrated draft) writes as today, as a seat with no row
        # does; the gate only reports. A confirmed job stays refused on such a run.
        raw = cc._declared_backend_raw(category, workspace_root) or {}
        rblock = raw.get(cc.JOB_KEY) if isinstance(raw.get(cc.JOB_KEY), dict) else None
        if job.get("reason") == "foreign_run" and (rblock is None or rblock.get("origin") in cc.JOB_EXEMPT_ORIGINS):
            return {"ok": True, "verdict": "unconfirmed", "line": "", "reported": True, "acting_key": None, "acting": None}
        return {"ok": False, "verdict": "refused", "line": job.get("line") or ""}
    block = job.get("job") if isinstance(job.get("job"), dict) else None
    confirmed = block is not None and block.get("origin") not in cc.JOB_EXEMPT_ORIGINS
    if confirmed:
        # fix check S2: the provider is the record's, never the caller's; a caller
        # naming another one is refused, and only the code picks the addresses
        rec_prov = str(block.get("provider_key") or "")
        if provider and str(provider) != rec_prov:
            return {"ok": False, "verdict": "cannot_check", "line": GATE_LINES["cannot_check"].format(job=word),
                    "acting_key": None, "acting": None}
        provider = rec_prov
        addresses = None
    # SHOULD 6: the code reads the identity tool's answer, not the model
    if answer is not None:
        addresses = cj.identities_from_answer(provider, answer)
    addrs = sorted({cc._norm_addr(a) for a in (addresses or []) if isinstance(a, str) and "@" in a})
    try:
        import hub_live
        by_h = {hub_live.digest(a): a for a in addrs}
    except Exception:  # noqa: BLE001
        by_h = {}
    held = {str(a.get("address_h") or "") for a in ((block or {}).get("accounts") or []) if isinstance(a, dict)}
    matched = sorted(by_h[h] for h in by_h if h in held)
    key = acting_key(provider)
    acting = None
    if not by_h:
        verdict = "cannot_check"
    elif key:
        # M3: a connector with several accounts acts as the one the write names;
        # the gate picks the confirmed one, and the write must name it
        verdict = "match" if matched else "mismatch"
        acting = matched[0] if matched else None
    elif len(by_h) > 1:
        verdict = "cannot_check"   # several accounts and no account argument: cannot tell which one acts
    else:
        verdict = "match" if matched else "mismatch"
    exempt = block is None or block.get("origin") in cc.JOB_EXEMPT_ORIGINS
    if exempt:
        return {"ok": True, "verdict": verdict, "line": "", "reported": True, "acting_key": key, "acting": None}
    test = block.get("test") if isinstance(block.get("test"), dict) else {}
    seat_checked = test.get("identity") == "match" or (cj.IDENTITY.get(str(provider or "")) or {}).get("verified")
    if verdict == "match" and not seat_checked:
        verdict, acting = "cannot_check", None
    return {"ok": verdict == "match", "verdict": verdict, "line": GATE_LINES[verdict].format(job=word),
            "acting_key": key, "acting": acting}


__all__ = ["ENABLED_JOBS", "PANEL_JOBS", "SETUP_DOC", "TESTS_DOC", "ARTIFACT_LINE", "NOT_ON_PAGE_LINE",
           "GATE_LINES", "hub_address", "setup_doc", "apply_setup", "panel_feed", "page_config",
           "calendar_live_allowed", "identity_plan",
           "identity_gate"]
