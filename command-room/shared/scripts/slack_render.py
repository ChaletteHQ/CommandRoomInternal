#!/usr/bin/env python3
"""
slack_render.py — the Slack render target's Block Kit emitter (SPEC_SLACK1 C-1).

WHY THIS EXISTS
===============
SLACK1's architecture thesis: one plugin, surface declared as data, the render
target INSIDE `widget_transport.render_and_persist` — never a downstream
"format py" post-processor (a post-processor outside the transport fights the
validators, which raise rather than degrade, and forks the contract).

This module is the slack half of that: it takes the SAME `data_view` every
widget skill already builds (shape: `shared/CHAT_ACTION_WIDGET.md`) and emits

    {"blocks": [...Block Kit...], "text": "<mrkdwn fallback>"}

It is only ever called by `render_and_persist(target="slack")`, which runs the
full gate stack around it (canonical-action / data-shape / pulse-richness /
send-class gates BEFORE emission via `chat_output_renderer.validate_data_view`;
the leak scan over this module's text render + the structural block contract
AFTER). Skills never import this module directly — the transport is the one
call, same as the cowork path.

NATIVE 2026 BLOCKS (§10.4)
==========================
Slack shipped data table / data visualization / card blocks Apr–Jun 2026. The
emitter targets them first where the data is shaped for them:

  - `data_table`   — non-interactive tabular views (no actions anywhere),
                     when the slack profile's `table_mode` is "native".
                     Limits honored: ≤200 data rows + header, ≤20 columns,
                     ≤20,000 chars across cells, uniform column counts.
  - `data_visualization` — a data view carrying a `chart` spec
                     (pie/bar/line/area), when `chart_mode` is "native".
                     Limits: ≤2 per message, ≤12 series/segments, ≤20 points
                     per series, label caps per the block reference.
  - Interactive row-lists (the all-batch widget class) use header/section/
    actions/context blocks: one section per row, primary verbs as buttons
    (the FB-4 one-tap contract carried over), tail verbs in a static_select
    ("— more —", the T2.2 dropdown carried over). Wire format is UNCHANGED:
    every element's `value` carries `{"n", "action", "src"}` and the listener
    composes the identical `apply choices: [...]` string
    (references/SLACK_BUTTON_BRIDGE_SPEC.md).

PRESENTATION KNOBS (§10.3)
==========================
All slack presentation tuning comes from `surface_context.load_profile`
(`shared/config/surface_profiles.json`): block/char budgets, rows per page,
column collapse, chart/table mode, verbosity, body preview length. No knob is
read from anywhere else — guard G24 enforces the three-legal-homes rule.

MRKDWN, NOT MARKDOWN
====================
Slack mrkdwn: *bold*, _italic_, <url|label>. `_md_to_mrkdwn` converts the
data view's markdown-ish strings. `computer://` URLs never survive to Slack —
deliverable links degrade to plain bold headlines (C-4; the file itself rides
`files.upload` on the listener side).
"""
from __future__ import annotations

import json
import re

# Hard Slack platform limits (blocks.validate mirrors these server-side; we
# enforce them at render time per the validator culture — raise, never degrade).
SLACK_MAX_BLOCKS_PER_MESSAGE = 50
SLACK_MAX_TEXT_OBJECT_CHARS = 3000
SLACK_MAX_HEADER_CHARS = 150
SLACK_MAX_OPTION_LABEL_CHARS = 75
SLACK_MAX_OPTION_VALUE_CHARS = 150
SLACK_MAX_ACTIONS_ELEMENTS = 25
DATA_TABLE_MAX_DATA_ROWS = 200
DATA_TABLE_MAX_COLUMNS = 20
DATA_TABLE_MAX_TOTAL_CHARS = 20000
DATA_VIZ_MAX_PER_MESSAGE = 2
DATA_VIZ_MAX_SERIES = 12
DATA_VIZ_MAX_POINTS = 20
DATA_VIZ_LABEL_CHARS = 20
DATA_VIZ_TITLE_CHARS = 50

_KNOWN_BLOCK_TYPES = {
    "header", "section", "context", "divider", "actions",
    "data_table", "data_visualization",
}

# The FB-4 primary-verb set, carried over from the cowork widget: these render
# as one-tap buttons; everything else rides the "— more —" select.
_PRIMARY_VERBS = ("send", "draft", "resolved", "mark done", "mark received",
                  "confirm", "snooze 3d")


class SlackBlockContractError(ValueError):
    """A produced Block Kit payload violates the structural contract
    (budgets, block shapes, platform limits). Fix the data view or the
    profile — never post an over-limit payload and let Slack truncate."""


# ---------------------------------------------------------------------------
# mrkdwn conversion
# ---------------------------------------------------------------------------

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")


def _md_link_to_mrkdwn(m: re.Match) -> str:
    label, url = m.group(1), m.group(2)
    # computer:// and file:// URLs are dead on Slack (C-4) — degrade to the
    # bare label; the deliverable itself is files.upload'ed by the listener.
    if url.startswith(("computer://", "file://")):
        return label
    return f"<{url}|{label}>"


def _md_to_mrkdwn(text: str) -> str:
    """Markdown-ish data-view text → Slack mrkdwn."""
    text = _MD_LINK_RE.sub(_md_link_to_mrkdwn, text)
    text = _MD_BOLD_RE.sub(r"*\1*", text)      # **b** → *b* (before single-*)
    text = _MD_ITALIC_RE.sub(r"_\1_", text)    # *i* → _i_
    return text


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# wire tuples — the C-3 contract
# ---------------------------------------------------------------------------

def _wire_value(n, action: str, src: str | None) -> str:
    """The element `value`: the SAME tuple shape the cowork widget's Apply
    batch carries — the listener composes `apply choices: [...]` from these
    verbatim (statelessness is why apply-choices ports with zero changes)."""
    tup = {"n": n, "action": action}
    if src:
        tup["src"] = src
    value = json.dumps(tup, separators=(",", ":"), ensure_ascii=False)
    if len(value) > SLACK_MAX_OPTION_VALUE_CHARS:
        raise SlackBlockContractError(
            f"wire tuple for item {n!r} exceeds Slack's {SLACK_MAX_OPTION_VALUE_CHARS}-char "
            f"option value cap ({len(value)} chars): {value[:80]}…"
        )
    return value


def _display_label(action: str, n) -> str:
    """Display label via the shared verb taxonomy (one vocabulary on every
    surface — F-59). Falls back to the stripped wire id."""
    try:
        from chat_output_renderer import _action_display_label, _strip_action_n_prefix
        return _truncate(_action_display_label(_strip_action_n_prefix(action, n)),
                         SLACK_MAX_OPTION_LABEL_CHARS)
    except Exception:
        return _truncate(str(action), SLACK_MAX_OPTION_LABEL_CHARS)


def _bare_action(action: str, n) -> str:
    try:
        from chat_output_renderer import _strip_action_n_prefix
        return _strip_action_n_prefix(action, n)
    except Exception:
        return action


def _is_primary(action: str) -> bool:
    low = action.lower()
    return any(low == p or low.startswith(p + " ") for p in _PRIMARY_VERBS)


# ---------------------------------------------------------------------------
# item / section emission (interactive row-list mode)
# ---------------------------------------------------------------------------

def _item_header_mrkdwn(item: dict) -> str:
    parts = [f"*{item.get('n')}.*"]
    if item.get("icon"):
        parts.append(item["icon"])
    if item.get("name"):
        parts.append(f"*{_md_to_mrkdwn(str(item['name']))}*")
    if item.get("subject"):
        parts.append(f'· "{_md_to_mrkdwn(str(item["subject"]))}"')
    if item.get("context_tag"):
        parts.append(f"— {_md_to_mrkdwn(str(item['context_tag']))}")
    return " ".join(parts)


def _item_text(item: dict, profile: dict) -> str:
    collapse = set(profile.get("column_collapse") or [])
    lines = [_item_header_mrkdwn(item)]
    if "annotations" not in collapse:
        for ann in item.get("annotations", []) or []:
            lines[0] += f" {_md_to_mrkdwn(str(ann))}"
    for key, val in item.get("metadata", []) or []:
        lines.append(f"*{key}:* {_md_to_mrkdwn(str(val))}")
    preview = int(profile.get("body_preview_lines", 3))
    body = [ln for ln in (item.get("body_lines") or []) if str(ln).strip()]
    for ln in body[:preview]:
        lines.append(f"_{_md_to_mrkdwn(str(ln))}_")
    if len(body) > preview:
        lines.append(f"_… {len(body) - preview} more line(s) in the full draft_")
    if "sources" not in collapse:
        srcs = item.get("sources") or []
        links = [f"<{s['url']}|{_truncate(str(s.get('label', 'source')), 40)}>"
                 for s in srcs if isinstance(s, dict) and s.get("url")
                 and not str(s["url"]).startswith(("computer://", "file://"))]
        if links:
            lines.append("↗ " + " · ".join(links))
    for sub in item.get("sub_items", []) or []:
        sub_line = f"    *{sub.get('n')}.* {_md_to_mrkdwn(str(sub.get('summary', '')))}"
        lines.append(sub_line)
    return _truncate("\n".join(lines), SLACK_MAX_TEXT_OBJECT_CHARS)


def _select_element(owner_n, actions: list, src: str | None, *, action_id: str,
                    placeholder: str) -> dict:
    options = []
    for a in actions:
        bare = _bare_action(str(a), owner_n)
        options.append({
            "text": {"type": "plain_text", "text": _display_label(str(a), owner_n)},
            "value": _wire_value(owner_n, bare, src),
        })
    return {
        "type": "static_select",
        "action_id": action_id,
        "placeholder": {"type": "plain_text", "text": placeholder},
        "options": options[:100],
    }


def _item_action_elements(item: dict, src: str | None, *, block_index: int) -> list[dict]:
    """Primary verbs → buttons; tail verbs → one '— more —' select; each
    sub_item with actions → its own select. Mirrors the cowork FB-4/T2.2
    row chrome on Slack's element vocabulary."""
    n = item.get("n")
    actions = [str(a) for a in (item.get("actions") or [])]
    primaries = [a for a in actions if _is_primary(_bare_action(a, n))]
    tail = [a for a in actions if a not in primaries]
    elements: list[dict] = []
    for i, a in enumerate(primaries[:3]):
        bare = _bare_action(a, n)
        elements.append({
            "type": "button",
            "action_id": f"cr_verb_{block_index}_{i}",
            "text": {"type": "plain_text", "text": _display_label(a, n)},
            "value": _wire_value(n, bare, src),
        })
    if tail:
        elements.append(_select_element(
            n, tail, src,
            action_id=f"cr_more_{block_index}",
            placeholder="— more —" if primaries else "— choose —",
        ))
    for j, sub in enumerate(item.get("sub_items", []) or []):
        sub_actions = [str(a) for a in (sub.get("actions") or [])]
        if not sub_actions:
            continue
        elements.append(_select_element(
            sub.get("n"), sub_actions, src,
            action_id=f"cr_sub_{block_index}_{j}",
            placeholder=f"{sub.get('n')} — choose",
        ))
    return elements[:SLACK_MAX_ACTIONS_ELEMENTS]


# ---------------------------------------------------------------------------
# native data_table / data_visualization mapping (§10.4)
# ---------------------------------------------------------------------------

def _view_is_tabular(data: dict) -> bool:
    """A view maps to the native data_table only when NOTHING is interactive
    (no actions on any item or sub_item) and every item carries metadata
    tuples — the read-only report class (usage report, roster views)."""
    sections = data.get("sections") or []
    items = [it for s in sections for it in (s.get("items") or [])]
    if not items:
        return False
    for it in items:
        if it.get("actions") or any(su.get("actions") for su in it.get("sub_items", []) or []):
            return False
        if not it.get("metadata"):
            return False
    return True


def _raw_text_cell(text: str) -> dict:
    return {"type": "raw_text", "text": text}


def emit_data_table(data: dict, profile: dict) -> dict:
    """Non-interactive tabular view → ONE native data_table block."""
    collapse = set(profile.get("column_collapse") or [])
    sections = data.get("sections") or []
    items = [it for s in sections for it in (s.get("items") or [])]
    columns: list[str] = ["Item"]
    for it in items:
        for key, _ in it.get("metadata", []) or []:
            if key not in columns and key.lower() not in collapse:
                columns.append(key)
    columns = columns[:DATA_TABLE_MAX_COLUMNS]
    rows = [[_raw_text_cell(c) for c in columns]]
    for it in items[:DATA_TABLE_MAX_DATA_ROWS]:
        meta = {k: str(v) for k, v in (it.get("metadata") or [])}
        first = " ".join(x for x in [str(it.get("n", "")) + ".",
                                     str(it.get("name", "") or it.get("subject", ""))] if x)
        cells = [_raw_text_cell(first)]
        for c in columns[1:]:
            cells.append(_raw_text_cell(meta.get(c, "")))
        rows.append(cells)
    total_chars = sum(len(c["text"]) for row in rows for c in row)
    if total_chars > DATA_TABLE_MAX_TOTAL_CHARS:
        raise SlackBlockContractError(
            f"data_table would carry {total_chars} chars (cap "
            f"{DATA_TABLE_MAX_TOTAL_CHARS}) — paginate the view (pass page=N)."
        )
    return {
        "type": "data_table",
        "caption": _truncate(str(data.get("header") or "Command Room"), 150),
        "rows": rows,
        "page_size": min(int(profile.get("rows_per_page", 8)), 100),
    }


def chart_to_block(chart: dict) -> dict:
    """A data view's `chart` spec → native data_visualization block.

    Expected spec shape (built by the chart-carrying skills):
      {"type": "pie"|"bar"|"line"|"area", "title": str,
       "segments": [{"label","value"}]                 # pie
       "series": [{"name","data":[{"label","value"}]}],# bar/line/area
       "categories": [...], "x_label": str, "y_label": str}
    """
    ctype = chart.get("type")
    title = _truncate(str(chart.get("title") or "Chart"), DATA_VIZ_TITLE_CHARS)
    if ctype == "pie":
        segments = [
            {"label": _truncate(str(s["label"]), DATA_VIZ_LABEL_CHARS),
             "value": s["value"]}
            for s in (chart.get("segments") or [])[:DATA_VIZ_MAX_SERIES]
        ]
        if not segments:
            raise SlackBlockContractError("pie chart spec carries no segments")
        return {"type": "data_visualization", "title": title,
                "chart": {"type": "pie", "segments": segments}}
    if ctype in ("bar", "line", "area"):
        categories = [_truncate(str(c), DATA_VIZ_LABEL_CHARS)
                      for c in (chart.get("categories") or [])[:DATA_VIZ_MAX_POINTS]]
        series = []
        for s in (chart.get("series") or [])[:DATA_VIZ_MAX_SERIES]:
            series.append({
                "name": _truncate(str(s["name"]), DATA_VIZ_LABEL_CHARS),
                "data": [
                    {"label": _truncate(str(p["label"]), DATA_VIZ_LABEL_CHARS),
                     "value": p["value"]}
                    for p in (s.get("data") or [])[:DATA_VIZ_MAX_POINTS]
                ],
            })
        if not (categories and series):
            raise SlackBlockContractError(f"{ctype} chart spec needs categories + series")
        axis = {"categories": categories}
        if chart.get("x_label"):
            axis["x_label"] = _truncate(str(chart["x_label"]), DATA_VIZ_TITLE_CHARS)
        if chart.get("y_label"):
            axis["y_label"] = _truncate(str(chart["y_label"]), DATA_VIZ_TITLE_CHARS)
        return {"type": "data_visualization", "title": title,
                "chart": {"type": ctype, "series": series, "axis_config": axis}}
    raise SlackBlockContractError(f"unknown chart type {ctype!r}")


# ---------------------------------------------------------------------------
# the emitter
# ---------------------------------------------------------------------------

def emit_slack_payload(data: dict, profile: dict) -> dict:
    """The SAME data view the cowork widget renders → {"blocks", "text"}.

    Called ONLY by `widget_transport.render_and_persist(target="slack")`
    (gates run there). The `text` value is the mrkdwn notification fallback —
    a numbered digest, never the whole payload."""
    src = data.get("source_skill")
    blocks: list[dict] = []
    fallback_lines: list[str] = []

    header = str(data.get("header") or "")
    if header:
        blocks.append({"type": "header",
                       "text": {"type": "plain_text",
                                "text": _truncate(_MD_BOLD_RE.sub(r"\1", header),
                                                  SLACK_MAX_HEADER_CHARS)}})
        fallback_lines.append(_truncate(header, 200))
    if data.get("sub_header"):
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn",
                                     "text": _truncate(_md_to_mrkdwn(str(data["sub_header"])),
                                                       SLACK_MAX_TEXT_OBJECT_CHARS)}]})

    widget_mode = data.get("widget_mode", "all_batch_widget")
    if widget_mode == "all_clear_summary":
        summary = str(data.get("summary") or "All clear — nothing needs your eyes right now.")
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": _md_to_mrkdwn(summary)}})
        fallback_lines.append(summary)
        return {"blocks": blocks, "text": "\n".join(fallback_lines)}

    chart = data.get("chart")
    if chart and profile.get("chart_mode", "native") == "native":
        blocks.append(chart_to_block(chart))

    if profile.get("table_mode", "native") == "native" and _view_is_tabular(data):
        blocks.append(emit_data_table(data, profile))
        for s in data.get("sections") or []:
            for it in s.get("items") or []:
                fallback_lines.append(
                    f"{it.get('n')}. {it.get('name', '') or it.get('subject', '')}")
        return {"blocks": blocks, "text": "\n".join(fallback_lines)}

    any_actions = False
    for section in data.get("sections") or []:
        title = section.get("title")
        if title:
            count = section.get("count")
            t = str(title).upper() + (f" ({count})" if count is not None else "")
            blocks.append({"type": "section",
                           "text": {"type": "mrkdwn", "text": f"*{_truncate(t, 200)}*"}})
        for item in section.get("items") or []:
            if item.get("n") is None:
                raise ValueError(
                    "Item missing required 'n' (item number) field — Rule 3 "
                    "violation in source data")
            blocks.append({"type": "section",
                           "text": {"type": "mrkdwn", "text": _item_text(item, profile)}})
            elements = _item_action_elements(item, src, block_index=len(blocks))
            if elements:
                any_actions = True
                blocks.append({"type": "actions",
                               "block_id": f"cr_row_{item.get('n')}",
                               "elements": elements})
            fb = f"{item.get('n')}. {item.get('name', '') or item.get('subject', '')}"
            if item.get("context_tag"):
                fb += f" — {item['context_tag']}"
            fallback_lines.append(fb)

    if any_actions:
        # The batch footer, ported: the LISTENER accumulates selections per
        # thread and composes ONE `apply choices: [...]` on Apply (C-3).
        blocks.append({"type": "actions", "block_id": "cr_footer",
                       "elements": [
                           {"type": "button", "action_id": "cr_apply_all",
                            "style": "primary",
                            "text": {"type": "plain_text", "text": "Apply selected"},
                            "value": json.dumps({"op": "apply_all", "src": src})},
                           {"type": "button", "action_id": "cr_snooze_rest",
                            "text": {"type": "plain_text", "text": "Snooze rest (1 day)"},
                            "value": json.dumps({"op": "snooze_rest", "src": src})},
                       ]})

    pagination = data.get("pagination")
    if pagination:
        pos = (f"Page {pagination.get('page')} of {pagination.get('total_pages')} "
               f"({pagination.get('total_items')} total) — say `show more` for the next page.")
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn", "text": pos}]})

    quick_read = data.get("quick_read")
    if quick_read:
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn",
                                     "text": _truncate(_md_to_mrkdwn(str(quick_read)),
                                                       SLACK_MAX_TEXT_OBJECT_CHARS)}]})

    return {"blocks": blocks, "text": _truncate("\n".join(fallback_lines), 2800)}


# ---------------------------------------------------------------------------
# structural contract + leak-scan text render
# ---------------------------------------------------------------------------

def blocks_text_render(payload: dict) -> str:
    """Every human-visible string in the payload, one per line — THE input the
    leak scanner runs over (C-1: 'leak scan extended to scan the Block Kit
    text render'). Walks the payload structurally so a new block type's text
    still surfaces; element `value`s (wire tuples) are included on purpose —
    a leaked internal id in a wire tuple is still a leak."""
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, val in node.items():
                if key in ("text", "caption", "title", "label", "placeholder",
                           "value", "x_label", "y_label", "name") and isinstance(val, str):
                    out.append(val)
                else:
                    walk(val)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload.get("blocks", []))
    if payload.get("text"):
        out.append(payload["text"])
    return "\n".join(out)


def validate_slack_payload(payload: dict, *, block_budget: int = SLACK_MAX_BLOCKS_PER_MESSAGE,
                           char_budget: int = SLACK_MAX_TEXT_OBJECT_CHARS) -> None:
    """The slack analog of `validate_rendered_widget` — structural contract
    checks the transport runs on every emission. Raises
    SlackBlockContractError; never trims, never degrades (the FS-08 rule:
    a payload that can't ship is reported, not quietly reshaped)."""
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise SlackBlockContractError("payload carries no blocks")
    if len(blocks) > min(block_budget, SLACK_MAX_BLOCKS_PER_MESSAGE):
        raise SlackBlockContractError(
            f"{len(blocks)} blocks exceeds the budget "
            f"({min(block_budget, SLACK_MAX_BLOCKS_PER_MESSAGE)}) — lower rows_per_page "
            "in surface_profiles.json or paginate (page=N).")
    n_viz = 0
    for i, b in enumerate(blocks):
        btype = b.get("type")
        if btype not in _KNOWN_BLOCK_TYPES:
            raise SlackBlockContractError(f"block[{i}] has unknown type {btype!r}")
        if btype == "data_visualization":
            n_viz += 1
        if btype == "section":
            t = (b.get("text") or {}).get("text", "")
            if len(t) > SLACK_MAX_TEXT_OBJECT_CHARS:
                raise SlackBlockContractError(
                    f"block[{i}] section text is {len(t)} chars (cap "
                    f"{SLACK_MAX_TEXT_OBJECT_CHARS})")
            if not t:
                raise SlackBlockContractError(f"block[{i}] section has empty text")
        if btype == "actions":
            els = b.get("elements") or []
            if not els or len(els) > SLACK_MAX_ACTIONS_ELEMENTS:
                raise SlackBlockContractError(
                    f"block[{i}] actions carries {len(els)} elements "
                    f"(1..{SLACK_MAX_ACTIONS_ELEMENTS})")
        if btype == "data_table":
            rows = b.get("rows") or []
            if not (2 <= len(rows) <= DATA_TABLE_MAX_DATA_ROWS + 1):
                raise SlackBlockContractError(
                    f"block[{i}] data_table has {len(rows)} rows "
                    f"(2..{DATA_TABLE_MAX_DATA_ROWS + 1})")
            widths = {len(r) for r in rows}
            if len(widths) != 1:
                raise SlackBlockContractError(
                    f"block[{i}] data_table rows have unequal column counts {sorted(widths)}")
            if next(iter(widths)) > DATA_TABLE_MAX_COLUMNS:
                raise SlackBlockContractError(
                    f"block[{i}] data_table exceeds {DATA_TABLE_MAX_COLUMNS} columns")
    if n_viz > DATA_VIZ_MAX_PER_MESSAGE:
        raise SlackBlockContractError(
            f"{n_viz} data_visualization blocks (max {DATA_VIZ_MAX_PER_MESSAGE} per message)")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise SlackBlockContractError(
            "payload carries no mrkdwn fallback text (notifications render it)")
    approx = sum(len(s) for s in blocks_text_render({"blocks": blocks}).splitlines())
    if approx > max(char_budget, SLACK_MAX_TEXT_OBJECT_CHARS) * 4:
        raise SlackBlockContractError(
            f"payload text weight ~{approx} chars far exceeds the char budget "
            f"({char_budget}) — paginate or lower rows_per_page.")


__all__ = [
    "SlackBlockContractError",
    "emit_slack_payload",
    "emit_data_table",
    "chart_to_block",
    "blocks_text_render",
    "validate_slack_payload",
    "SLACK_MAX_BLOCKS_PER_MESSAGE",
]
