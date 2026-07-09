#!/usr/bin/env python3
"""
Token / usage telemetry for scheduled-task fires (v2.14.0+ — Phase 1).

Per M's v2.13.2 ask: scheduled tasks consume heavily. Before optimizing, MEASURE
where the spend concentrates. This module provides a deterministic helper that
adds usage fields to every `pack_run` event written by orchestrators.

Cowork's MCP doesn't expose per-fire token counts to the agent natively, so we
proxy via:
  - prompt_chars: size of the orchestrator prompt at fire time
  - response_chars: total chars of widget HTML + Briefs section + Sources +
    apply-time response
  - connector_calls: count + breakdown by connector (gmail, calendar, granola,
    drive, zapier)
  - duration_ms: existing field, kept

Char count is a reasonable token proxy (chars / ~4 ≈ tokens for English text;
HTML/code skews higher). Connector calls are the OTHER big cost driver — each
MCP tool call has overhead.

USAGE (from an orchestrator's Phase X memory-updates step):

    from telemetry import build_pack_run_telemetry, format_telemetry_summary

    pack_run_event = {
        "type": "pack_run",
        "ts": now_iso,
        "data": {
            "kind": "inbox",
            "status": "complete",
            **build_pack_run_telemetry(
                prompt_text=ORCHESTRATOR_PROMPT_TEXT,
                response_text=widget_html + briefs_md + sources_md,
                connector_calls=[
                    {"connector": "gmail", "operation": "search_threads", "ms": 320},
                    {"connector": "gmail", "operation": "get_thread", "ms": 180},
                    ...
                ],
                duration_ms=elapsed_ms,
            ),
        }
    }
    append_to_events_jsonl(pack_run_event)

The telemetry fields are SILENT per CONTRACT.md Rule 9 — never narrated to chat.
They feed the `command room usage report` view (separate read-only tool) for
weekly trend analysis.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, Optional


CHARS_PER_TOKEN_APPROX = 4  # English text; HTML/code skews higher


def estimate_tokens(text: Optional[str]) -> int:
    """Rough token-count estimate via chars / 4 heuristic. For HTML / code,
    actual tokens may run 30-50% higher.
    """
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN_APPROX


def build_pack_run_telemetry(
    prompt_text: Optional[str] = None,
    response_text: Optional[str] = None,
    connector_calls: Optional[Iterable[dict]] = None,
    duration_ms: Optional[int] = None,
) -> dict:
    """Build the telemetry sub-dict that gets merged into a pack_run event's
    `data` field. All fields optional — orchestrators that haven't been wired
    to capture every metric can pass partial data.

    Returns: dict with keys ready for events.jsonl. Schema:
        {
            "telemetry": {
                "prompt_chars": int,
                "prompt_tokens_est": int,
                "response_chars": int,
                "response_tokens_est": int,
                "connector_call_count": int,
                "connector_calls_by_connector": {"gmail": 4, "calendar": 1, ...},
                "connector_calls_by_op": {"gmail.search_threads": 1, ...},
                "connector_total_ms": int,
                "duration_ms": int,
                "schema_version": "v2.14.0",
            }
        }

    Empty / missing inputs yield zero values; the caller can decide whether to
    emit the field.
    """
    prompt_chars = len(prompt_text) if prompt_text else 0
    response_chars = len(response_text) if response_text else 0

    by_connector: Counter = Counter()
    by_op: Counter = Counter()
    connector_total_ms = 0
    call_count = 0
    for call in connector_calls or []:
        connector = call.get("connector", "unknown")
        operation = call.get("operation", "unknown")
        by_connector[connector] += 1
        by_op[f"{connector}.{operation}"] += 1
        connector_total_ms += int(call.get("ms") or 0)
        call_count += 1

    return {
        "telemetry": {
            "prompt_chars": prompt_chars,
            "prompt_tokens_est": estimate_tokens(prompt_text),
            "response_chars": response_chars,
            "response_tokens_est": estimate_tokens(response_text),
            "connector_call_count": call_count,
            "connector_calls_by_connector": dict(by_connector),
            "connector_calls_by_op": dict(by_op),
            "connector_total_ms": connector_total_ms,
            "duration_ms": int(duration_ms) if duration_ms is not None else None,
            "schema_version": "v2.14.0",
        }
    }


def format_telemetry_summary(telemetry: dict) -> str:
    """Render a one-line plain-English summary of a telemetry block. Used by
    the `command room usage report` view and weekly-audit. NOT for chat output
    of scheduled-task fires (those stay silent per CONTRACT.md Rule 9).

    Example:
      'Inbox fire: 21k prompt + 4k response = ~6k tokens, 12 connector calls
       (gmail x10, calendar x2), 8.4s'
    """
    if not telemetry:
        return "(no telemetry)"
    parts = []
    pt = telemetry.get("prompt_tokens_est", 0)
    rt = telemetry.get("response_tokens_est", 0)
    total = pt + rt
    if total:
        if total >= 1000:
            parts.append(f"~{total / 1000:.1f}k tokens")
        else:
            parts.append(f"~{total} tokens")
    cc = telemetry.get("connector_call_count", 0)
    if cc:
        by_c = telemetry.get("connector_calls_by_connector", {})
        breakdown = ", ".join(f"{k} x{v}" for k, v in sorted(by_c.items(), key=lambda kv: -kv[1]))
        parts.append(f"{cc} connector calls ({breakdown})")
    dms = telemetry.get("duration_ms")
    if dms:
        parts.append(f"{dms / 1000:.1f}s")
    return ", ".join(parts) if parts else "(no metrics captured)"


def _duration_ms(tel: dict) -> int:
    """Read a telemetry duration in MILLISECONDS, coalescing the field-name drift
    seen across versions in real data. `duration_ms` is already ms; the `_s` /
    `_sec` / `_seconds` variants are SECONDS and are converted (×1000) so totals
    aren't silently wrong. Every read is None-coerced. First present field wins."""
    ms = tel.get("duration_ms")
    if ms is not None:
        return int(ms or 0)
    for key in ("duration_s", "duration_sec", "duration_seconds"):
        v = tel.get(key)
        if v is not None:
            return int((v or 0) * 1000)
    return 0


def aggregate_pack_run_telemetry(events: Iterable[dict]) -> dict:
    """Aggregate telemetry across multiple pack_run events. Used by weekly-audit
    to summarize the week's spend per orchestrator.

    Returns:
        {
            "by_kind": {"inbox": {"fires": 5, "avg_tokens": 5200, "avg_ms": 8400, ...}, ...},
            "totals": {"fires": 22, "tokens": 110000, "ms": 184000, ...},
        }
    """
    by_kind = {}
    totals = {"fires": 0, "tokens": 0, "ms": 0, "connector_calls": 0}
    for ev in events:
        if ev.get("type") != "pack_run":
            continue
        data = ev.get("data") or {}
        # v4.5.2 R1 — bucket by the CANONICAL task id so legacy spellings
        # (`cr-commitments`, `past_meetings`, `dont_forget`) and kind-less
        # task_id-only receipts (F-47 P2a's two-shapes-one-day) aggregate
        # into one row instead of fragmenting the table (F-49).
        kind = data.get("kind") or data.get("task_id") or data.get("taskId") or "unknown"
        try:
            from receipts import normalize_task_id
            kind = normalize_task_id(kind) or "unknown"
        except ImportError:
            pass
        tel = data.get("telemetry") or {}
        if not tel:
            continue
        bucket = by_kind.setdefault(kind, {
            "fires": 0, "tokens": 0, "ms": 0, "connector_calls": 0,
        })
        bucket["fires"] += 1
        # Real pack_run events carry numeric keys PRESENT-but-None, so dict.get's
        # default never applies — coerce every numeric read with `or 0`.
        tokens = (tel.get("prompt_tokens_est") or 0) + (tel.get("response_tokens_est") or 0)
        ms = _duration_ms(tel)
        ccount = tel.get("connector_call_count") or 0
        bucket["tokens"] += tokens
        bucket["ms"] += ms
        bucket["connector_calls"] += ccount
        totals["fires"] += 1
        totals["tokens"] += tokens
        totals["ms"] += ms
        totals["connector_calls"] += ccount

    # Average metrics per kind
    for kind, bucket in by_kind.items():
        n = bucket["fires"] or 1
        bucket["avg_tokens"] = bucket["tokens"] // n
        bucket["avg_ms"] = bucket["ms"] // n
        bucket["avg_connector_calls"] = bucket["connector_calls"] // n

    return {"by_kind": by_kind, "totals": totals}


def build_data_view_snapshot(data_view: Optional[dict]) -> Optional[dict]:
    """Capture a sanitized snapshot of the data view passed to the renderer.

    Used for remote diagnostic capability (v2.14.1+) — when a user reports
    "Edit then send didn't open" on their machine, we read events.jsonl
    pack_run.data.last_data_view and inspect what the orchestrator actually
    built without needing console access.

    Sanitization:
      - body_lines truncated to 200 chars total per item (we only need shape)
      - metadata kept verbatim (To/Cc/Subject already in chat anyway)
      - sub_item summaries truncated to 100 chars
      - original_thread.body truncated to 200 chars
      - actions array kept verbatim (this is what we need for diagnosis)

    Returns: dict ready for events.jsonl serialization. Returns None if input
    is None / falsy.

    The snapshot lives ONLY in events.jsonl (silent per Rule 9) — never
    rendered to chat. Inspecting it requires reading the file directly OR using
    the `usage report` skill's diagnostic mode.
    """
    if not data_view:
        return None
    snap = {
        "header": (data_view.get("header") or "")[:200],
        "sub_header": (data_view.get("sub_header") or "")[:200],
        "sections": [],
    }
    for section in data_view.get("sections", []):
        snap_section = {
            "title": section.get("title"),
            "count": section.get("count"),
            "items": [],
        }
        for item in section.get("items", []):
            snap_item = {
                "n": item.get("n"),
                "icon": item.get("icon"),
                "name": item.get("name"),
                "subject": (item.get("subject") or "")[:120],
                "context_tag": (item.get("context_tag") or "")[:200],
                "metadata_keys": [k for k, v in (item.get("metadata") or []) if v],
                "metadata_count": len([1 for k, v in (item.get("metadata") or []) if v]),
                "body_lines_count": len(item.get("body_lines") or []),
                "body_chars_total": sum(len(l) for l in (item.get("body_lines") or [])),
                "actions": list(item.get("actions") or []),
                "has_original_thread": bool(item.get("original_thread")),
                "has_artifact_link": bool(item.get("artifact_link")),
                "sub_items": [
                    {
                        "id": sub.get("id"),
                        "summary": (sub.get("summary") or "")[:100],
                        "actions": list(sub.get("actions") or []),
                    }
                    for sub in (item.get("sub_items") or [])
                ],
            }
            snap_section["items"].append(snap_item)
        snap["sections"].append(snap_section)
    return snap


__all__ = [
    "estimate_tokens",
    "build_pack_run_telemetry",
    "build_data_view_snapshot",
    "format_telemetry_summary",
    "aggregate_pack_run_telemetry",
    "CHARS_PER_TOKEN_APPROX",
]


if __name__ == "__main__":
    # Smoke test
    t = build_pack_run_telemetry(
        prompt_text="x" * 21000,
        response_text="y" * 4000,
        connector_calls=[
            {"connector": "gmail", "operation": "search_threads", "ms": 320},
            {"connector": "gmail", "operation": "get_thread", "ms": 180},
            {"connector": "calendar", "operation": "list_events", "ms": 120},
        ],
        duration_ms=8400,
    )
    print("Telemetry:", t)
    print("Summary:", format_telemetry_summary(t["telemetry"]))
