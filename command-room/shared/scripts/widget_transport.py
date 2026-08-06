#!/usr/bin/env python3
"""
Render-validate-persist transport for chat widgets (v3.13.8+ — Bug #32;
delivery reworked T2 — Bug #67).

WHY THIS EXISTS
---------------

Pre-v3.13.8, every widget surface went through a raw byte-relay of the whole
validated HTML through `show_widget`'s parameter. Agents observed the cost of
large parameter payloads and rationally chose freelance render paths for many
surfaces, bypassing the validators at the final transport step. Root cause was
structural, not contractual: as long as canonical = expensive relay,
freelance = cheap direct, agents keep picking freelance.

`render_and_persist()` is the fix: it renders via the canonical path (so all
validators fire), runs `validate_rendered_widget` internally, and persists the
validated HTML to a workspace-local file. ONE call = render + full validator
chain + persist + audit trail.

DELIVERY (reworked T2 — the F2 gate)
------------------------------------

The integration-2026-07 + full-stack dogfood cycles CONFIRMED that Cowork's
`mcp__visualize__show_widget` has NO `file_uri` parameter (CHANGELOG Bug #67;
schema = loading_messages / title / widget_code only). The former contract —
"hand transport['file_uri'] to show_widget" — was impossible on the live
runtime, and the runtime silently improvised a freelance render every time
(FS-08). The delivery contract is now:

  1. PAGINATE BY DESIGN. Unbounded views (the full commitment set, the Staff
     Meeting queue) render ONE page of ~10 rows at a time — `page=N`. A page is
     the unit the runtime relays. `show more` re-fires with `page=N+1`.
  2. Each page is render+validate+persisted here (validators fire, audit file
     written), and DELIVERED by relaying `transport["html"]` — the persisted
     page's validated bytes, VERBATIM — as `show_widget`'s `widget_code`.
     The scaffold is diet-minified (renderer, T2) so a page fits one Cowork
     Read (25K-token cap).

The persist step is the validation gate + audit trail, not the delivery
carrier. `transport["html"]` is the deliverable; `transport["file_uri"]` /
`transport["path"]` remain for the audit file and any standalone open, but they
are NOT handed to show_widget (it cannot read them).

USAGE
=====

    from widget_transport import render_and_persist
    transport = render_and_persist(
        data_view=data,
        wrapper="fragment",
        persist_dir=workspace_root / "_hq" / ".system" / "widgets",
        page=1,                   # omit page for bounded/small surfaces
    )                             # page_size defaults to DEFAULT_PAGE_SIZE
    # transport["html"]        — the validated PAGE HTML → show_widget widget_code
    # transport["pagination"]  — {page, total_pages, has_more, total_items}
    # transport["path"]        — disk audit path (Path object)
    # transport["file_uri"]    — file:// URI (audit / standalone open only)
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Optional


_BOM = "﻿"


def _atomic_write_widget_html(path: Path, html: str, wrapper: str) -> None:
    """Atomic write of validated widget HTML.

    Handles two cases:
      - wrapper="fragment": persist the fragment as-is. When the file is
        opened standalone in a browser the user will get a working render
        because the fragment includes <style> + content + <script>; for
        proper UTF-8 handling we prepend a BOM so the browser doesn't have
        to guess (Bug #40 — mojibake on standalone open).
      - wrapper="document": already has <!DOCTYPE> + <head><meta charset>;
        write as-is.

    Per §3.4 of the v3.13.8 plan, do NOT inject <meta charset> into a
    fragment that may later be sent to show_widget — that violates the
    contract. The BOM is invisible to show_widget's parser but tells
    standalone browsers to use UTF-8.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if wrapper == "fragment":
        content = _BOM + html
    else:
        content = html
    # Atomic write via temp + rename
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8", newline="")
    os.replace(str(tmp), str(path))


_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_filename(stem: str) -> str:
    s = _SAFE_NAME_RE.sub("-", stem).strip("-")
    return s or "widget"


# T2.1 (RV-1 calibration): the hard cap on a single page's rendered bytes.
# T2 sized "10 rows/page" on synthetic ~300-char rows (~884B rendered); real
# commitment rows carry six verb buttons + meta and weigh ~3KB rendered, so a
# 10-row page hit 50KB and the runtime refused the byte-exact relay. Observed
# evidence: ~45KB relayed comfortably (prior probe); 50-51KB refused twice.
# 40KB sits deliberately UNDER that ceiling — the margin absorbs later pages
# rendering somewhat heavier than the page-1 sizing anchor (accepted trade:
# the delivered page itself is never re-checked against the budget).
WIDGET_PAGE_BYTE_BUDGET = 40_000
_MIN_PAGE_SIZE = 3


def _fit_page_size(data_view: dict, wrapper: str, requested: int) -> int:
    """Return the LARGEST page_size in requested.._MIN_PAGE_SIZE whose PAGE-1
    render fits the byte budget (T2.2 — replaces the T2.1 halving sequence,
    which could only pick from {requested, requested//2, ...} and left real
    density on the table: a 12-row-capable view got 5 because 6-9 were never
    probed).

    Binary search over the size range — page-1 rendered bytes are monotone
    non-decreasing in page_size (a bigger page is the same page plus more
    rows), so the search is sound and needs ~log2(requested) probe renders.
    (Review F-8 note: the returned size is always VERIFIED-fitting; at edges
    where page-count chrome shifts the byte total non-monotonically — e.g. a
    position line growing a digit — it can be one row shy of the true
    maximum. Fitting is the guarantee; maximality is best-effort.)

    Page 1 stays the deterministic sizing anchor: every call over the same
    data view converges on the same effective size, so page boundaries stay
    stable across a fire's show-more sequence. Later pages can in principle
    render slightly heavier than page 1; the budget carries margin for that
    (accepted trade, unchanged from T2.1 — the delivered page itself is never
    re-checked against the budget; the over_budget flag downstream covers the
    floor-size case).

    PAGESNAP correction: "every call over the same data view" was doing more
    work in that sentence than it could carry. The claim is true of THIS
    function and always was — but `surface_drivers.run_surface` re-read the
    substrate on every page, so page 2 was never the same data view, and the
    effective size could therefore shift BETWEEN pages of one fire (a second,
    quieter way the page boundary moved under the user). The page-set snapshot
    is what actually makes the sentence true: pages 2+ now fit against the
    same frozen view page 1 was fitted against.
    """
    from chat_output_renderer import render_chat_output_widget, paginate_data_view

    def _fits(size: int) -> bool:
        probe = paginate_data_view(data_view, page=1, page_size=size)
        return len(render_chat_output_widget(probe, wrapper=wrapper)) <= WIDGET_PAGE_BYTE_BUDGET

    hi = max(_MIN_PAGE_SIZE, int(requested))
    lo = _MIN_PAGE_SIZE
    if _fits(hi):
        return hi
    if not _fits(lo):
        return lo  # floor-size page still over budget → over_budget flags it
    # Invariant: _fits(lo) is True, _fits(hi) is False. Find the boundary.
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _fits(mid):
            lo = mid
        else:
            hi = mid
    return lo


def render_and_persist(
    *,
    data_view: dict,
    wrapper: str = "fragment",
    persist_dir: str | Path,
    name_hint: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    suppress_ids: Optional[set] = None,
    target: str = "cowork_html",
) -> dict:
    """Canonical render-validate-persist transport (delivery = widget_code
    relay of transport["html"], T2 — see module docstring).

    SPEC_SLACK1 C-1 — `target` selects the render surface INSIDE the one
    canonical call (never a downstream post-processor):

      - "cowork_html" (DEFAULT): today's behavior, byte-identical — the code
        path below is untouched when target is defaulted.
      - "slack": emits Block Kit JSON + a mrkdwn fallback from the SAME
        data_view through the SAME gate stack (canonical-action / data-shape /
        pulse-richness / send-class gates, the leak scan extended over the
        Block Kit text render, the structural block contract), paginated to
        the slack budget (~50 blocks / ~3k chars — surface_profiles.json),
        and persists the audit file identically. Returns
        {"blocks", "text", "pagination", "path", "file_uri"} — the listener
        posts `blocks` (shared/CHAT_ACTION_WIDGET.md § Transport; the button
        round-trip contract is references/SLACK_BUTTON_BRIDGE_SPEC.md).

    Mute/dismissal filters and cross-surface dedup run UPSTREAM in the data
    view build (projectors/orchestrators) exactly as for cowork — both targets
    consume the identical, already-filtered view; nothing about the slack
    target bypasses them.

    Renders the widget via `chat_output_renderer.render_chat_output_widget`
    (all validators fire), runs `validate_rendered_widget`, writes the
    validated HTML to an audit file inside `persist_dir`, and returns a
    transport dict. When `page` is given, the data view is first sliced to that
    page (paginate-by-design for unbounded views).

    Args:
      data_view: same data shape that render_chat_output_widget expects.
      wrapper: "fragment" (default, for show_widget) or "document"
        (standalone HTML file).
      persist_dir: directory in which to write the validated HTML audit file.
        Typically `<workspace>/_hq/.system/widgets/`.
      name_hint: optional prefix for the on-disk filename (otherwise derived
        from the data_view's surface tag).
      page: 1-indexed page to render. None (default) renders the full view —
        correct for bounded surfaces (the daily ≤5 card, small fires). Any
        unbounded surface (commitments full set, Staff Meeting queue) MUST pass
        an explicit page and relay one page at a time — and MUST hand the same
        frozen view for every page of one fire (see `page_snapshot`).
      page_size: max top-level rows per page (ceiling). None uses
        DEFAULT_PAGE_SIZE — one value for the whole stack (PAGESNAP).
      suppress_ids: wire ids (`n`) to drop AFTER slicing — the rows the user
        already applied in this page-set. Applied post-slice on purpose: the
        slice indexes the frozen list so nothing shifts, and dropping a row
        here shortens this one page rather than pulling the next row across
        the page boundary. `pagination` is left as sliced, so the reported
        total stays the page-set's total and the header holds steady across
        the fire.

    Returns:
      {
        "html":       the validated PAGE HTML → relay as show_widget widget_code,
        "file_uri":   "file:///abs/path" (audit / standalone open ONLY),
        "path":       Path object for the persisted audit file,
        "pagination": {page, total_pages, page_size, has_more, total_items}
                      (present whenever `page` was passed),
      }

    Raises:
      Whatever `render_chat_output_widget` raises (CanonicalActionError,
      DataShapeError, LeakDetectedError), plus WrapperContractError /
      WidgetFeedbackContractError from the built-in `validate_rendered_widget`
      pass — the transport runs it so callers can't skip it.
    """
    if target == "slack":
        return _render_and_persist_slack(
            data_view=data_view,
            persist_dir=persist_dir,
            name_hint=name_hint,
            page=page,
            page_size=page_size,
            suppress_ids=suppress_ids,
        )
    if target != "cowork_html":
        raise ValueError(
            f"unknown render target {target!r} — 'cowork_html' or 'slack'")

    # Import here to avoid a circular import at module load time
    from chat_output_renderer import (
        DEFAULT_PAGE_SIZE,
        render_chat_output_widget,
        validate_rendered_widget,
        paginate_data_view,
    )

    pagination = None
    view = data_view
    if page is not None:
        # T2.1: fit rows-per-page to the relay byte budget (RV-1: real rows
        # weigh ~3x the synthetic calibration; requested size is a ceiling).
        eff_size = _fit_page_size(
            data_view, wrapper,
            DEFAULT_PAGE_SIZE if page_size is None else page_size)
        view = paginate_data_view(data_view, page=page, page_size=eff_size)
        pagination = view.get("pagination")
        if suppress_ids:
            from page_snapshot import suppress_applied
            view, n_suppressed = suppress_applied(view, set(suppress_ids))
            if n_suppressed:
                pagination = dict(pagination or {})
                pagination["suppressed"] = n_suppressed
                view["pagination"] = pagination

    html = render_chat_output_widget(view, wrapper=wrapper)
    # EW2+T (F-15): the transport IS the one-call canonical path — the wrapper
    # contract check runs here so no caller can ship a widget whose input
    # buttons lost their wrappers. Passes trivially on button-less HTML.
    # PGUARD1 D2: the data view's `surface` tag is plumbed through so a widget
    # DECLARED for an org/board/client audience gets the blocking
    # personal-content scan; owner surfaces (commitments, staff-meeting, the
    # brief) are untouched — an absent/unknown tag never escalates to org.
    validate_rendered_widget(html, surface=data_view.get("surface"))

    # T2.1 (review F-5): a floor-size page can still exceed the budget on
    # monster rows. Flag it so skill text can pre-warn (deliver substance as
    # text) instead of eating a refused relay downstream.
    if pagination is not None and len(html) > WIDGET_PAGE_BYTE_BUDGET:
        pagination["over_budget"] = True

    persist_dir = Path(persist_dir)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    surface = name_hint or data_view.get("surface") or "widget"
    page_tag = f"_p{pagination['page']}of{pagination['total_pages']}" if pagination else ""
    filename = f"{_safe_filename(surface)}{page_tag}_{ts}.html"
    out_path = persist_dir / filename

    _atomic_write_widget_html(out_path, html, wrapper=wrapper)

    # file:// URI — absolute path, forward slashes, three slashes after file:
    file_uri = "file:///" + str(out_path.resolve()).replace(os.sep, "/").lstrip("/")

    result = {
        "html": html,
        "file_uri": file_uri,
        "path": out_path,
    }
    if pagination is not None:
        result["pagination"] = pagination
    return result


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomic write for the slack audit payload (same temp+rename discipline
    as the HTML writer; no BOM — the payload is JSON, not a browser file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8", newline="")
    os.replace(str(tmp), str(path))


def _fit_page_size_slack(data_view: dict, requested: int, profile: dict) -> int:
    """Slack analog of `_fit_page_size`: the LARGEST page_size whose PAGE-1
    emission fits the slack budgets (block count from the profile; a fixed
    reserve covers header/footer/context chrome). Same binary search, same
    page-1-as-anchor determinism, same floor-size over-budget flagging
    downstream."""
    import json as _json

    from chat_output_renderer import paginate_data_view
    from slack_render import emit_slack_payload

    block_budget = int(profile.get("block_budget", 50))
    char_budget = int(profile.get("char_budget", 3000))

    def _fits(size: int) -> bool:
        probe = paginate_data_view(data_view, page=1, page_size=size)
        payload = emit_slack_payload(probe, profile)
        if len(payload["blocks"]) > block_budget:
            return False
        # char weight: the largest single text object must fit, and the
        # fallback digest must fit the ~3k message-text budget.
        return len(payload.get("text", "")) <= char_budget

    hi = max(_MIN_PAGE_SIZE, int(requested))
    lo = _MIN_PAGE_SIZE
    if _fits(hi):
        return hi
    if not _fits(lo):
        return lo
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _fits(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _render_and_persist_slack(
    *,
    data_view: dict,
    persist_dir: str | Path,
    name_hint: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    suppress_ids: Optional[set] = None,
) -> dict:
    """The slack target (SPEC_SLACK1 C-1). Same shape as the cowork path:
    gates → paginate → emit → leak scan + structural contract → persist →
    return. Raises exactly like the cowork path (CanonicalActionError,
    DataShapeError, LeakDetectedError, SlackBlockContractError) — a payload
    that fails a gate is reported, never degraded (FS-08)."""
    import json as _json

    from chat_output_renderer import (
        DEFAULT_PAGE_SIZE,
        paginate_data_view,
        validate_chat_output,
        validate_data_view,
    )
    from slack_render import (
        blocks_text_render,
        emit_slack_payload,
        validate_slack_payload,
    )
    from surface_context import load_profile

    profile = load_profile("slack", skill=data_view.get("source_skill"))

    # Gate 1 family — the SAME pre-render validators the cowork path runs
    # inside render_chat_output_widget (canonical actions, data shape, pulse
    # richness, send-class emails, voice-tell backstop).
    validate_data_view(data_view)

    pagination = None
    view = data_view
    if page is not None:
        eff_size = _fit_page_size_slack(
            data_view,
            int(profile.get("rows_per_page", DEFAULT_PAGE_SIZE)) if page_size is None else int(page_size),
            profile,
        )
        view = paginate_data_view(data_view, page=page, page_size=eff_size)
        pagination = view.get("pagination")
        if suppress_ids:
            from page_snapshot import suppress_applied
            view, n_suppressed = suppress_applied(view, set(suppress_ids))
            if n_suppressed:
                pagination = dict(pagination or {})
                pagination["suppressed"] = n_suppressed
                view["pagination"] = pagination

    payload = emit_slack_payload(view, profile)

    # Gate 2 — the leak scan, extended over the Block Kit text render (C-1).
    # Same scanner, same blocking semantics as the cowork HTML path; the
    # PGUARD1 org-surface personal scan rides the same `surface` tag.
    validate_chat_output(
        blocks_text_render(payload),
        surface=data_view.get("surface"),
    )

    # Gate 3 — the structural block contract (the validate_rendered_widget
    # analog): budgets, block shapes, platform limits.
    validate_slack_payload(
        payload,
        block_budget=int(profile.get("block_budget", 50)),
        char_budget=int(profile.get("char_budget", 3000)),
    )

    if pagination is not None and len(payload["blocks"]) > int(profile.get("block_budget", 50)):
        pagination["over_budget"] = True

    persist_dir = Path(persist_dir)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    surface = name_hint or data_view.get("surface") or "widget"
    page_tag = f"_p{pagination['page']}of{pagination['total_pages']}" if pagination else ""
    filename = f"{_safe_filename(surface)}{page_tag}_{ts}.slack.json"
    out_path = persist_dir / filename

    audit = {"blocks": payload["blocks"], "text": payload["text"]}
    if pagination is not None:
        audit["pagination"] = pagination
    _atomic_write_text(out_path, _json.dumps(audit, ensure_ascii=False, indent=1))

    file_uri = "file:///" + str(out_path.resolve()).replace(os.sep, "/").lstrip("/")

    result = {
        "blocks": payload["blocks"],
        "text": payload["text"],
        "path": out_path,
        "file_uri": file_uri,
    }
    if pagination is not None:
        result["pagination"] = pagination
    return result


__all__ = ["render_and_persist"]
