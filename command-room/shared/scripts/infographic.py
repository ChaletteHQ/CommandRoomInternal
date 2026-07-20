#!/usr/bin/env python3
"""Infographic composer — template-constrained visual one-pagers (SPEC OUT4).

WHY THIS EXISTS
---------------
Command Room already believes quality comes from CONSTRAINING STRUCTURE and
letting content vary (AntV's verified insight: a fixed template library makes
output consistent by construction, not by hope). This module gives the plugin
a visual one-pager capability built that way — a CLOSED set of 8 layout
templates (`shared/templates/infographic/`, registry in
`shared/INFOGRAPHIC_LAYOUTS.md`), each declaring a required data shape. The
model CHOOSES the layout by reading the registry table; the code VALIDATES the
shape and renders. Content that fits no layout is an honest decline, never a
force-fit.

NOT A NEW RENDERING WORLD (SPEC OUT4 §2)
----------------------------------------
This adds LAYOUTS on top of the OUT5 premium-HTML rail — it does not fork it.
`build_infographic` renders the chosen layout fragment INTO the exact OUT5
premium shell (`shared/templates/premium_brief.html`, brand-resolved via
`brand.get_brand()` through `premium_html._template_vars`). Every color/font is
a CSS variable the shell defines; no layout template carries a palette constant
(the stray-palette guard enforces). Where a layout wants a chart it uses
`charts.py` — the ONE chart owner. There is no second SVG path.

THE GATE STACK (SPEC OUT4 §3b — the same rail's gates)
------------------------------------------------------
An infographic is a client deliverable surface, so the OUT5 gates apply where
they have meaning:
  - voice-tell (`voice_tell_detector.scan_text`) over the PROSE a layout
    carries (notes, step details, comparison cells) — a fail-severity tell
    refuses the render, mirroring the brief backends' outbound posture.
  - leak scan (`docx_leak_scanner`) over the rendered HTML's visible text AND
    every href/src target — the exact OUT5 HTML channel, so a forbidden token
    hiding in a link is caught like body prose.
An infographic is NOT a brief_kind (no exec-header, no ask block, no
recommendation-ordering) — it does not run `brief_gates.run_pre_save_gates`;
it runs the two gates that a layout-first artifact can meaningfully carry.

SUBSTRATE-DERIVED ONLY (SPEC OUT4 §4)
-------------------------------------
An infographic is a VIEW of workspace truth, never decoration. This module
never invents content: it renders exactly the validated `content` the caller
built from substrate. Placeholder orgs/names only in every fixture.

Stdlib only (brand.py / components.py / premium_html.py / charts.py are all
stdlib-only too; python-docx is never imported — an HTML-only render must not
trigger the docx self-install).
"""
from __future__ import annotations

import html as _html
import json
import re as _re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from brand import get_brand  # noqa: E402
from components import (  # noqa: E402
    MAX_TILES_PER_BAND,
    build_tile_band_html,
    build_timeline_html,
    flag_key_for,
)
from output_profile import get_output_profile  # noqa: E402
# Reuse the OUT5 rail's shell + brand→template-variable resolution. `_template_vars`
# is a module-internal of premium_html; the coupling is pinned by
# tests/run_infographic_test.py so a premium_html refactor fails there with a
# name, not as a silently un-themed page (the exemplars↔docx_leak_scanner
# precedent).
from premium_html import TEMPLATE_PATH as _SHELL_PATH, _template_vars  # noqa: E402

_TEMPLATE_DIR = _HERE.parent / "templates" / "infographic"

# The eyebrow shown in the shell chrome for each layout (the KIND_LABEL slot).
_EYEBROW = "INFOGRAPHIC"


class InfographicDataError(ValueError):
    """The `content` shape violates the chosen layout's contract (same
    ValueError contract as components.validate_tiles / charts.ChartDataError).
    A refused layout renders NOTHING — the caller declines honestly ("this
    content doesn't fit an infographic"), never a force-fit or empty frame."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _esc(v) -> str:
    return _html.escape(str(v).strip())


def _nonempty(v) -> bool:
    return bool(str(v or "").strip())


def _require_list(content: dict, key: str, minlen: int) -> list:
    val = content.get(key)
    if not isinstance(val, list) or len(val) < minlen:
        raise InfographicDataError(
            f"{key!r} must be a list of >= {minlen} item(s); the caller drops "
            f"the infographic below that (refusal over an empty frame)"
        )
    return val


_TEMPLATE_COMMENT_RE = _re.compile(r"<!--.*?-->", _re.DOTALL)


def _load_fragment(layout: str) -> str:
    path = _TEMPLATE_DIR / f"{layout}.html"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:  # pragma: no cover — a shipped template is always present
        raise InfographicDataError(
            f"layout template missing for {layout!r} ({e}); the layout set is "
            f"closed — every registered layout ships a template file"
        )
    # Strip the template's own <!-- --> comments BEFORE slot fill (review F-3):
    # the header comment documents its slots as literal {{TOKEN}} text, so
    # filling the raw file would inject a full second copy of the content into
    # an HTML comment on every rendered page. Comments are for the template
    # reader, never the deliverable.
    return _TEMPLATE_COMMENT_RE.sub("", raw).strip()


def _fill(fragment: str, slots: Dict[str, str]) -> str:
    out = fragment
    for token, value in slots.items():
        out = out.replace("{{" + token + "}}", value)
    return out


def _clean_tiles(raw) -> List[dict]:
    """Drop-empty a tile list (a tile with no label OR no value is dropped,
    never an empty frame — the components.py posture applied at the caller)."""
    tiles: List[dict] = []
    for t in raw or []:
        if isinstance(t, dict) and _nonempty(t.get("label")) and _nonempty(t.get("value")):
            tiles.append({"label": str(t["label"]).strip(),
                          "value": str(t["value"]).strip()})
    return tiles


# ---------------------------------------------------------------------------
# Per-layout renderers. Each validates its shape (refusal over empty frames)
# and returns (fragment_html, prose_strings) — prose_strings feed the voice
# gate; the whole assembled page feeds the leak scan.
# ---------------------------------------------------------------------------

def _r_ranked_list(content: dict, brand: dict) -> Tuple[str, List[str]]:
    rows = _require_list(content, "rows", 2)
    prose: List[str] = []
    items: List[str] = []
    n = 0
    for row in rows:
        if not isinstance(row, dict) or not _nonempty(row.get("label")):
            continue  # drop-empty
        n += 1
        parts = [f'<li class="ig-ranked-row"><span class="rk">{n}</span>'
                 f'<span class="lbl">{_esc(row["label"])}</span>']
        if _nonempty(row.get("score")):
            parts.append(f'<span class="score">{_esc(row["score"])}</span>')
        if _nonempty(row.get("note")):
            prose.append(str(row["note"]))
            parts.append(f'<span class="note">{_esc(row["note"])}</span>')
        parts.append("</li>")
        items.append("".join(parts))
    if len(items) < 2:
        raise InfographicDataError(
            "ranked_list needs >= 2 rows with a non-empty 'label' after "
            "drop-empty; the caller keeps its table/tile representation instead"
        )
    tiles = _clean_tiles(content.get("tiles"))
    if len(tiles) > MAX_TILES_PER_BAND:
        raise InfographicDataError(
            f"ranked_list tile band takes at most {MAX_TILES_PER_BAND} tiles "
            f"(got {len(tiles)})"
        )
    band = build_tile_band_html(tiles, validate=True) if tiles else ""
    frag = _fill(_load_fragment("ranked_list"),
                 {"TILEBAND": band, "ROWS": "".join(items)})
    return frag, prose


def _r_sequence(content: dict, brand: dict) -> Tuple[str, List[str]]:
    steps = _require_list(content, "steps", 2)
    prose: List[str] = []
    items: List[str] = []
    for st in steps:
        if not isinstance(st, dict) or not _nonempty(st.get("title")):
            continue
        prose.append(str(st["title"]))
        parts = [f'<li class="ig-seq-step"><span class="stitle">{_esc(st["title"])}</span>']
        if _nonempty(st.get("detail")):
            prose.append(str(st["detail"]))
            parts.append(f'<span class="sdetail">{_esc(st["detail"])}</span>')
        parts.append("</li>")
        items.append("".join(parts))
    if len(items) < 2:
        raise InfographicDataError(
            "sequence needs >= 2 steps with a non-empty 'title' after drop-empty"
        )
    frag = _fill(_load_fragment("sequence"), {"STEPS": "".join(items)})
    return frag, prose


def _r_comparison_2col(content: dict, brand: dict) -> Tuple[str, List[str]]:
    a_label = content.get("a_label")
    b_label = content.get("b_label")
    if not (_nonempty(a_label) and _nonempty(b_label)):
        raise InfographicDataError(
            "comparison_2col needs non-empty 'a_label' and 'b_label'"
        )
    rows = _require_list(content, "rows", 2)
    prose: List[str] = [str(a_label), str(b_label)]
    items: List[str] = []
    for row in rows:
        if not isinstance(row, dict) or not _nonempty(row.get("label")):
            continue
        a_val = str(row.get("a") or "").strip()
        b_val = str(row.get("b") or "").strip()
        prose.extend([str(row["label"]), a_val, b_val])
        items.append(
            f'<tr><td class="rowlbl">{_esc(row["label"])}</td>'
            f'<td>{_esc(a_val)}</td><td>{_esc(b_val)}</td></tr>'
        )
    if len(items) < 2:
        raise InfographicDataError(
            "comparison_2col needs >= 2 rows with a non-empty 'label' after "
            "drop-empty"
        )
    frag = _fill(_load_fragment("comparison_2col"),
                 {"AHEAD": _esc(a_label), "BHEAD": _esc(b_label),
                  "ROWS": "".join(items)})
    return frag, prose


_MAX_HIER_DEPTH = 3  # root = level 1 (SPEC OUT4 §3a — tree <= 3 levels)


def _r_hierarchy(content: dict, brand: dict) -> Tuple[str, List[str]]:
    root = content.get("root")
    if not isinstance(root, dict) or not _nonempty(root.get("label")):
        raise InfographicDataError(
            "hierarchy needs a 'root' node with a non-empty 'label'"
        )
    prose: List[str] = []

    def node_html(node: dict, level: int) -> str:
        if level > _MAX_HIER_DEPTH:
            raise InfographicDataError(
                f"hierarchy is capped at {_MAX_HIER_DEPTH} levels (SPEC OUT4 "
                f"§3a); collapse or split a deeper tree rather than force-fit it"
            )
        if not isinstance(node, dict) or not _nonempty(node.get("label")):
            raise InfographicDataError(
                f"every hierarchy node needs a non-empty 'label': {node!r}"
            )
        prose.append(str(node["label"]))
        cls = f"lvl{min(level - 1, 2)}"
        out = (f'<li class="{cls}"><span class="node">'
               f'{_esc(node["label"])}</span>')
        children = [c for c in (node.get("children") or []) if isinstance(c, dict)]
        if children:
            out += "<ul>" + "".join(
                node_html(c, level + 1) for c in children) + "</ul>"
        out += "</li>"
        return out

    tree = node_html(root, 1)
    frag = _fill(_load_fragment("hierarchy"), {"TREE": tree})
    return frag, prose


def _r_timeline_spread(content: dict, brand: dict) -> Tuple[str, List[str]]:
    events = _require_list(content, "events", 2)
    prose: List[str] = []
    points: List[dict] = []
    for ev in events:
        if not isinstance(ev, dict) or not _nonempty(ev.get("date")) \
                or not _nonempty(ev.get("label")):
            continue
        label = str(ev["label"]).strip()
        prose.append(label)
        if _nonempty(ev.get("detail")):
            prose.append(str(ev["detail"]))
            label = f'{label} — {str(ev["detail"]).strip()}'
        pt = {"date": str(ev["date"]).strip(), "label": label}
        if ev.get("current"):
            pt["current"] = True
        points.append(pt)
    if len(points) < 2:
        raise InfographicDataError(
            "timeline_spread needs >= 2 events with 'date' and 'label' after "
            "drop-empty (the shared timeline strip refuses fewer)"
        )
    # components.build_timeline_html is the ONE timeline owner (>= 2 or refuse).
    frag = _fill(_load_fragment("timeline_spread"),
                 {"TIMELINE": build_timeline_html(points)})
    return frag, prose


def _r_stat_spotlight(content: dict, brand: dict) -> Tuple[str, List[str]]:
    hero = content.get("hero")
    if not isinstance(hero, dict) or not _nonempty(hero.get("value")) \
            or not _nonempty(hero.get("label")):
        raise InfographicDataError(
            "stat_spotlight needs a 'hero' with a non-empty 'value' and 'label'"
        )
    support = _clean_tiles(content.get("support"))
    if not (1 <= len(support) <= 4):
        raise InfographicDataError(
            f"stat_spotlight needs 1-4 support tiles after drop-empty (got "
            f"{len(support)}); a hero with no support is a stat line, not an "
            f"infographic — the caller keeps its tile band instead"
        )
    prose = [str(hero["label"])] + [t["label"] for t in support]
    frag = _fill(_load_fragment("stat_spotlight"), {
        "HERO_VALUE": _esc(hero["value"]),
        "HERO_LABEL": _esc(hero["label"]),
        "SUPPORT": build_tile_band_html(support, validate=True),
    })
    return frag, prose


def _axis_labels(axis, name: str) -> Tuple[str, str]:
    if not isinstance(axis, dict) or not _nonempty(axis.get("low")) \
            or not _nonempty(axis.get("high")):
        raise InfographicDataError(
            f"quadrant needs '{name}' with non-empty 'low' and 'high' labels"
        )
    return str(axis["low"]).strip(), str(axis["high"]).strip()


def _r_quadrant(content: dict, brand: dict) -> Tuple[str, List[str]]:
    xlow, xhigh = _axis_labels(content.get("x_axis"), "x_axis")
    ylow, yhigh = _axis_labels(content.get("y_axis"), "y_axis")
    items = _require_list(content, "items", 2)
    prose: List[str] = [xlow, xhigh, ylow, yhigh]
    spans: List[str] = []
    for it in items:
        if not isinstance(it, dict) or not _nonempty(it.get("label")):
            continue
        try:
            x = float(it.get("x"))
            y = float(it.get("y"))
        except (TypeError, ValueError):
            raise InfographicDataError(
                f"quadrant item needs numeric 'x' and 'y' in [0,1]: {it!r}"
            )
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise InfographicDataError(
                f"quadrant item 'x'/'y' must be in [0,1] (normalized "
                f"placement): {it!r}"
            )
        prose.append(str(it["label"]))
        left = round(x * 100, 2)
        top = round((1.0 - y) * 100, 2)  # y is up
        spans.append(
            f'<span class="item" style="left:{left}%;top:{top}%">'
            f'{_esc(it["label"])}</span>'
        )
    if len(spans) < 2:
        raise InfographicDataError(
            "quadrant needs >= 2 placeable items with a 'label' after drop-empty"
        )
    frag = _fill(_load_fragment("quadrant"), {
        "XLOW": _esc(xlow), "XHIGH": _esc(xhigh),
        "YLOW": _esc(ylow), "YHIGH": _esc(yhigh),
        "ITEMS": "".join(spans),
    })
    return frag, prose


# status word (via components.flag_key_for) -> (row class, pill label)
_STATUS_MAP = {
    "flag_ok": ("pass", "PASS"),
    "flag_warn": ("warn", "WARN"),
    "flag_bad": ("fail", "FAIL"),
}


def _r_checklist_scorecard(content: dict, brand: dict) -> Tuple[str, List[str]]:
    rows = _require_list(content, "rows", 2)
    prose: List[str] = []
    items: List[str] = []
    for row in rows:
        if not isinstance(row, dict) or not _nonempty(row.get("label")):
            continue
        key = flag_key_for(row.get("status"))
        if key not in _STATUS_MAP:
            raise InfographicDataError(
                f"checklist_scorecard row 'status' must read as pass / warn / "
                f"fail (ok, warn, bad and their synonyms): {row.get('status')!r}"
            )
        cls, pill = _STATUS_MAP[key]
        prose.append(str(row["label"]))
        parts = [f'<li class="ig-check-row {cls}"><span class="st">{pill}</span>'
                 f'<span class="lbl">{_esc(row["label"])}</span>']
        if _nonempty(row.get("note")):
            prose.append(str(row["note"]))
            parts.append(f'<span class="note">{_esc(row["note"])}</span>')
        parts.append("</li>")
        items.append("".join(parts))
    if len(items) < 2:
        raise InfographicDataError(
            "checklist_scorecard needs >= 2 rows with a 'label' + 'status' "
            "after drop-empty"
        )
    frag = _fill(_load_fragment("checklist_scorecard"), {"ROWS": "".join(items)})
    return frag, prose


# The CLOSED layout set (SPEC OUT4 §3a — one file each, one validator each).
LAYOUTS: Dict[str, Callable[[dict, dict], Tuple[str, List[str]]]] = {
    "ranked_list": _r_ranked_list,
    "sequence": _r_sequence,
    "comparison_2col": _r_comparison_2col,
    "hierarchy": _r_hierarchy,
    "timeline_spread": _r_timeline_spread,
    "stat_spotlight": _r_stat_spotlight,
    "quadrant": _r_quadrant,
    "checklist_scorecard": _r_checklist_scorecard,
}

SUPPORTED_LAYOUTS = tuple(LAYOUTS)


# ---------------------------------------------------------------------------
# Gates (SPEC OUT4 §3b) — the OUT5 rail's gates where they have meaning.
# ---------------------------------------------------------------------------

def _voice_gate(prose_strings: List[str], voice_gate: str) -> None:
    """Voice-tell over the layout's prose. A fail-severity tell refuses the
    render (client deliverable surface). Lazy import + ImportError tolerance,
    exactly like the brief backends: a workspace mid-update without the
    detector still renders."""
    if voice_gate == "off":
        return
    text = "\n\n".join(s for s in prose_strings if str(s).strip())
    if not text.strip():
        return
    try:
        from voice_tell_detector import scan_text, summarize_findings, VoiceTellError
    except ImportError:  # pragma: no cover — partial-install posture
        return
    result = scan_text(text, context="brief")
    fails = [f for f in result["findings"] if f["severity"] == "fail"]
    if fails and voice_gate == "default":
        raise VoiceTellError(
            f"Voice-tell gate blocked an infographic render — "
            f"{len(fails)} banned phrase(s) in the layout prose must be "
            f"rewritten:\n" + summarize_findings(result["findings"]),
            findings=result["findings"],
        )
    if result["findings"]:
        print(
            f"[voice-tell gate] {len(result['findings'])} tell(s) in "
            f"infographic prose (warn-only, render proceeds):\n"
            + summarize_findings(result["findings"]),
            file=sys.stderr,
        )


def _leak_gate(page_html: str) -> None:
    """Leak scan over the rendered page's visible text + every href/src target
    — the exact OUT5 HTML channel (docx_leak_scanner._html_visible_text). A
    forbidden token refuses the render. Lazy import + ImportError tolerance
    (the brief backends' post-save-scan posture).

    PGUARD2 (OUT4-review follow-up): the scan DECLARES the org surface. An
    infographic is a forwardable one-pager by design (the zero-names lock is
    its whole posture), so a personal-lane fingerprint in the rendered page —
    a reminder id, a `tie: personal` chip, a balance-nudge token — is a
    BLOCKING finding here, not just internal-ID tokens. There is no
    owner-facing infographic variant; if one ever exists it must plumb a
    surface parameter instead of removing this declaration."""
    try:
        from docx_leak_scanner import (
            scan_text_for_leaks, _html_visible_text, LeakScanError,
        )
    except ImportError:  # pragma: no cover — partial-install posture
        return
    findings = scan_text_for_leaks(_html_visible_text(page_html), surface="org")
    if findings:
        lines = [f"  [{f['name']}] {f['match']!r} (…{f['context'][:60]}…)"
                 for f in findings[:10]]
        more = f"\n  …and {len(findings) - 10} more" if len(findings) > 10 else ""
        raise LeakScanError(
            "Forbidden tokens in infographic (nothing returned):\n"
            + "\n".join(lines) + more
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_infographic(
    layout: str,
    content: dict,
    *,
    title: str = "",
    subtitle: str = "",
    eyebrow: Optional[str] = None,
    brand: Optional[dict] = None,
    workspace_root: Optional[str] = None,
    org_id: Optional[str] = None,
    footer_text: Optional[str] = None,
    voice_gate: str = "default",
) -> str:
    """Render a template-constrained infographic to a self-contained premium
    HTML page and RETURN the HTML string (SPEC OUT4 §3b).

    Args:
      layout: one of SUPPORTED_LAYOUTS. An unknown layout raises
        InfographicDataError (the layout set is CLOSED).
      content: the layout's required shape (see shared/INFOGRAPHIC_LAYOUTS.md).
        Substrate-derived only. Empty elements are dropped; a layout that would
        render nothing REFUSES (never an empty frame) — the caller then
        declines honestly ("this content doesn't fit an infographic").
      title / subtitle: the page heading (substrate-derived). title defaults to
        the layout's eyebrow when omitted.
      eyebrow: the chrome eyebrow label (default "INFOGRAPHIC").
      brand: a RESOLVED brand dict (get_brand(workspace_root, org_id)); None =
        workspace/org resolution, else byte-stable DEFAULT_BRAND — same
        precedence contract as premium_html / components / charts.
      voice_gate: "default" (fail-severity prose tell refuses) | "warn" | "off".

    Returns the full HTML string. Deterministic: same input + same brand → the
    same bytes.

    Raises:
      InfographicDataError (a ValueError) on an unknown layout or a shape /
        refusal violation — the caller keeps its non-infographic representation.
      VoiceTellError when a fail-severity tell appears in the layout prose.
      LeakScanError when a forbidden token appears in the rendered page.
    """
    if layout not in LAYOUTS:
        raise InfographicDataError(
            f"layout must be one of {list(SUPPORTED_LAYOUTS)}, got {layout!r} "
            f"— the layout set is closed (shared/INFOGRAPHIC_LAYOUTS.md)"
        )
    if not isinstance(content, dict):
        raise InfographicDataError(
            f"content must be a dict for layout {layout!r}, got "
            f"{type(content).__name__}"
        )

    resolved_brand = brand if brand is not None else get_brand(workspace_root, org_id)
    if footer_text is not None:
        resolved_brand = dict(resolved_brand, footer_line=footer_text)
    profile = get_output_profile(workspace_root)

    # Validate + render the layout (refusal over empty frames happens here).
    fragment_html, prose = LAYOUTS[layout](content, resolved_brand)

    # Voice gate BEFORE assembly so a blocked render returns nothing.
    _voice_gate(prose, voice_gate)

    page_title = str(title).strip() or (eyebrow or _EYEBROW).title()

    fills = _template_vars(resolved_brand, profile)
    fills.update({
        "TITLE": _esc(page_title),
        "SUBTITLE": _esc(subtitle),
        "KIND_LABEL": _esc(eyebrow or _EYEBROW),
        "BADGES": "",
        "EXEC_HEADER": "",
        "ASKS": "",
        "SOURCE_SUMMARY": "",
    })
    page = _SHELL_PATH.read_text(encoding="utf-8")
    for token, value in fills.items():
        page = page.replace("{{" + token + "}}", value)
    # CONTENT is injected LAST so any '{{...}}'-shaped substring inside user
    # content is never re-processed by the shell fill loop above.
    page = page.replace("{{CONTENT}}", fragment_html)

    # Leak scan over the ASSEMBLED page (visible text + hrefs) — refuses on
    # a finding, so nothing leaks in the returned string.
    _leak_gate(page)
    return page


def build_infographic_from_json(json_payload: str) -> str:
    """JSON wrapper for orchestrator / skill bash invocations (the format twin
    of premium_html.make_premium_brief_from_json). Keys mirror
    build_infographic() kwargs; `layout` and `content` are required. Prints and
    returns the HTML string."""
    payload = json.loads(json_payload)
    html_out = build_infographic(
        payload["layout"],
        payload["content"],
        title=payload.get("title", ""),
        subtitle=payload.get("subtitle", ""),
        eyebrow=payload.get("eyebrow"),
        brand=payload.get("brand"),
        workspace_root=payload.get("workspace_root"),
        org_id=payload.get("org_id"),
        footer_text=payload.get("footer_text"),
        voice_gate=payload.get("voice_gate", "default"),
    )
    print(html_out)
    return html_out


__all__ = [
    "SUPPORTED_LAYOUTS",
    "LAYOUTS",
    "InfographicDataError",
    "build_infographic",
    "build_infographic_from_json",
]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        build_infographic_from_json(sys.argv[1])
    else:
        build_infographic_from_json(sys.stdin.read())
