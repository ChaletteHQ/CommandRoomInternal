#!/usr/bin/env python3
"""
quantify.py — derivable-only money × time tags for executive list items (EXEC1).

Per `shared/EXECUTIVE_OUTPUT_STANDARD.md` element 3, this is the ONLY sanctioned
source of inline dollar tags on list items. It composes a compact tag like
`"12d late · $40K deal"` from substrate fields that ALREADY EXIST on the
commitment / thread / org passed in:

  - time part  ← the commitment's `due` (days overdue), or a thread's
                 `last_activity` (days quiet) when there's no due date.
  - money part ← `primary_thread_id → thread → org → revenue/deal-value field`
                 (the org field is populated elsewhere, e.g. from a QBO sync
                 where that connector is wired).

NO ESTIMATION PATH EXISTS BY CONSTRUCTION. There is no branch that infers,
averages, scales, or guesses a figure. When the substrate lacks the field, that
part is simply omitted; when BOTH parts are absent the function returns `None`.
A client whose workspace never persisted a deal/revenue field (e.g. no
QuickBooks wiring) gets no dollar part — never a fabricated one. The helper makes
no MCP / connector call; it reads only fields already on the passed-in dicts.

Usage:

    from quantify import money_time_tag
    tag = money_time_tag(commitment, entities)   # "12d late · $40K deal" | None
    if tag:
        line = f"{title} — {tag}"
"""
from __future__ import annotations

import datetime
from typing import Optional


# Money fields read off the org (and, as a fallback, the item itself), in
# priority order. The label is appended after the amount when present
# ("$40K deal"); revenue-style fields carry no label ("$240K"). These are
# CONVENTIONAL field names — most workspaces won't have any of them, which is
# the no-fabrication guarantee: absent field → no money part, never an estimate.
_MONEY_FIELDS = [
    ("deal_value", "deal"),
    ("contract_value", "deal"),
    ("deal_size", "deal"),
    ("revenue", None),
    ("historical_revenue", None),
    ("annual_revenue", None),
    ("arr", None),
    ("mrr", None),
    ("account_value", None),
]


def _as_date(value) -> Optional[datetime.date]:
    """Parse an ISO date / datetime into a date. Return None on anything
    unparseable — never raise, never guess."""
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # Tolerate "YYYY-MM-DD", "YYYY-MM-DDTHH:MM:SSZ", etc.
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _get(item: dict, key: str):
    """Defensive read: top-level field, else inside `data`. Handles both the
    canonical commitment shape (`data.due`) and flat/legacy shapes (`due`)."""
    if not isinstance(item, dict):
        return None
    if item.get(key) not in (None, ""):
        return item.get(key)
    data = item.get("data")
    if isinstance(data, dict) and data.get(key) not in (None, ""):
        return data.get(key)
    return None


def _time_part(item: dict, today: datetime.date) -> Optional[str]:
    """Lateness from `due` (overdue only), else quiet-days from `last_activity`.
    None when neither field is present/parseable. Never estimates."""
    due = _as_date(_get(item, "due"))
    if due is not None:
        days_late = (today - due).days
        if days_late > 0:
            return f"{days_late}d late"
        return None  # not yet due — lateness is the only time signal we assert
    last = _as_date(_get(item, "last_activity"))
    if last is not None:
        days_quiet = (today - last).days
        if days_quiet > 0:
            return f"{days_quiet}d quiet"
    return None


def _format_money(value) -> Optional[str]:
    """Format a numeric amount as $40K / $240K / $1.2M. None on non-numeric or
    non-positive — never fabricates a figure."""
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        return None
    if isinstance(value, str):
        cleaned = value.strip().lstrip("$").replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    n = float(value)
    if n <= 0:
        return None
    if n >= 1_000_000:
        m = n / 1_000_000
        return f"${m:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"${round(n / 1000)}K"
    return f"${int(round(n))}"


def _index_by_id(seq) -> dict:
    out = {}
    if isinstance(seq, list):
        for el in seq:
            if isinstance(el, dict) and el.get("id"):
                out[el["id"]] = el
    return out


def _resolve_thread(item: dict, entities) -> Optional[dict]:
    """commitment/thread → its thread record. None if the chain can't be
    completed from the substrate (no raise)."""
    if not isinstance(entities, dict):
        return None
    threads = _index_by_id(entities.get("threads") or entities.get("projects"))

    # Find the relevant thread id: a commitment points at primary_thread_id;
    # a thread IS the item (its own id).
    thread_id = (
        _get(item, "primary_thread_id")
        or _get(item, "thread_id")
    )
    thread = threads.get(thread_id) if thread_id else None
    if thread is None and isinstance(item, dict) and item.get("id") in threads:
        thread = threads[item["id"]]
    return thread


def _resolve_org(item: dict, entities) -> Optional[dict]:
    """commitment/thread → its thread → that thread's org. None if the chain
    can't be completed from the substrate (no raise)."""
    thread = _resolve_thread(item, entities)
    if thread is None or not isinstance(entities, dict):
        return None
    orgs = _index_by_id(entities.get("orgs"))
    org_id = thread.get("affiliation_id") or thread.get("org_id")
    if not org_id or org_id == "personal":
        return None
    return orgs.get(org_id)


def _nested_deal_value(candidate) -> Optional[str]:
    """A PIPE1 deal thread's `deal.value` — the stated per-deal figure. Reads
    the nested object only; still no estimation (absent/invalid → None)."""
    if not isinstance(candidate, dict):
        return None
    deal = candidate.get("deal")
    if not isinstance(deal, dict):
        return None
    amount = _format_money(deal.get("value"))
    if amount is None:
        return None
    return f"{amount} deal"


def _money_part(item: dict, entities) -> Optional[str]:
    """Trace to the money figure, in priority order (SPEC PIPE1 extended the
    trace): (1) the item's own nested `deal.value` (the item IS a deal
    thread), (2) the resolved thread's `deal.value` (a commitment on a deal
    thread), (3) the org's revenue/deal-value fields — flat top-level keys
    AND the grouped `org.money` object (SPEC HIST1 D4/B1: set_org_money
    writes the grouped shape; its inner keys mirror _MONEY_FIELDS, so the
    sub-dict resolves as one more candidate), (4) a value annotated
    directly on the item. A stated per-deal figure beats the org-level
    convention fields — an org can have three deals. Returns None when no
    such field exists — the no-fabrication guarantee."""
    thread = _resolve_thread(item, entities)
    for deal_carrier in (item, thread):
        tag = _nested_deal_value(deal_carrier)
        if tag is not None:
            return tag

    candidates = []
    org = _resolve_org(item, entities)
    if isinstance(org, dict):
        candidates.append(org)
        if isinstance(org.get("money"), dict):
            candidates.append(org["money"])  # grouped org money (SPEC HIST1 D4/B1) — inner keys mirror _MONEY_FIELDS
    if isinstance(item, dict):
        candidates.append(item)  # value annotated straight on the item
        data = item.get("data")
        if isinstance(data, dict):
            candidates.append(data)

    for source in candidates:
        for field, label in _MONEY_FIELDS:
            if source.get(field) in (None, ""):
                continue
            amount = _format_money(source.get(field))
            if amount is None:
                continue
            return f"{amount} {label}" if label else amount
    return None


def money_time_tag(commitment_or_thread, entities, *, now=None) -> Optional[str]:
    """Compose a `"12d late · $40K deal"`-style tag from substrate fields only.

    Args:
      commitment_or_thread: a commitment event dict (canonical or flat/legacy
        shape) or a thread/project dict.
      entities: the entities registry dict ({people, projects/threads, orgs}).
        Used to trace the org behind the item's thread. May be None/partial —
        the money part is simply omitted when the chain can't be completed.
      now: optional date / ISO string for deterministic time math (tests).
        Defaults to today.

    Returns:
      The composed tag, or None when NEITHER a time nor a money part can be
      derived from the substrate. NEVER estimates a missing figure.
    """
    if not isinstance(commitment_or_thread, dict):
        return None

    today = _as_date(now) or datetime.date.today()

    parts = []
    time_part = _time_part(commitment_or_thread, today)
    if time_part:
        parts.append(time_part)
    money_part = _money_part(commitment_or_thread, entities)
    if money_part:
        parts.append(money_part)

    if not parts:
        return None
    return " · ".join(parts)


__all__ = ["money_time_tag"]


if __name__ == "__main__":
    # Smoke test — no estimation anywhere.
    ents = {
        "threads": [{"id": "project_017", "affiliation_id": "org_acme"}],
        "orgs": [{"id": "org_acme", "canonical_name": "Acme Co", "deal_value": 40000}],
    }
    c = {"type": "commitment", "primary_thread_id": "project_017",
         "data": {"due": "2026-06-02"}}
    print("with field:", money_time_tag(c, ents, now="2026-06-14"))
    no_field = {"type": "commitment", "primary_thread_id": "project_017",
                "data": {"due": "2026-06-02"}}
    print("no money field:", money_time_tag(
        no_field, {"threads": [{"id": "project_017", "affiliation_id": "org_x"}],
                   "orgs": [{"id": "org_x", "canonical_name": "X"}]},
        now="2026-06-14"))
    print("nothing derivable:", money_time_tag({"type": "commitment"}, {}, now="2026-06-14"))
