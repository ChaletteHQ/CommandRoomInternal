#!/usr/bin/env python3
"""
Premium HTML brief writer (SPEC OUT5) — a second BACKEND, not a second path.

WHY THIS EXISTS
---------------
The research skill's self-contained branded HTML brief is the best-looking
output in the product. OUT5 generalizes it into a client-selectable format any
launched kind can render to (selected via the output profile's
`default_format` / `format_by_kind` behind the existing `tune output` verb —
`output_profile.resolve_format_for_kind` is the router). This module is the
HTML chokepoint: `make_premium_brief()` accepts the EXACT section / tile /
table / matrix shapes `brief_writer.make_brief()` accepts and runs the SAME
gate stack before writing.

THE PARITY INVARIANT (the spec's soul — SPEC OUT5 §3b)
------------------------------------------------------
**No gate that fires on the docx path may be absent here.** Both backends call
`brief_gates.run_pre_save_gates` (input validation → rec-ordering → contract
gate → voice gate → exec-header requirement), share the page-cap warn, and run
their format's post-save leak scan from the ONE forbidden-token list
(docx_leak_scanner). Pinned by tests/run_guard_g16_gate_parity_test.py — a
gate added to one backend and not the other fails the guard naming the side
that lags. Do NOT add a gate inline here; it belongs in brief_gates.

IT ALSO CLOSES THE FIELD-REPORT REGRESSION (acceptance #5, 2026-07-16)
----------------------------------------------------------------------
The old research render contract was "Replace {{TOKENS}} by hand" — a
prose-instructed, no-chokepoint render that got skipped at the end of long
research turns (the #104 class): live fires produced NO HTML artifact at all.
This module makes the render a MECHANICAL call whose output path the caller
asserts. There is no hand-fill path anymore; research_brief.html was
superseded by shared/templates/premium_brief.html, rendered only here.

DESIGN
------
  - Template: shared/templates/premium_brief.html — self-contained, dark,
    brand-resolved. NO external fonts / CDN / asset server (SPEC OUT5 §4
    fence; deliberately TIGHTER than the old research template, which pulled
    Google Fonts — brand font stacks fall back to system faces).
  - Components render through components.py's HTML fragment builders (tiles /
    table / matrix / timeline) — one implementation, no third markup dialect.
    The premium template's CSS styles the builders' classes (the same
    division of labor the chat widget uses with _WIDGET_CSS); brand colors
    enter at the template level via `brand.get_brand()`.
  - Output: the composer passes the routed path (same folder/naming as the
    docx twin, `.html` extension); artifact link via
    `brief_path.get_brief_artifact_url()`. This module never invents paths.
  - Output-profile knobs apply for parity: density (body leading/space),
    visual_bias (tiles/body order), page_cap (shared warn).

PREMIUM-ONLY SECTION EXTENSIONS (superset, never a divergence of core shapes)
-----------------------------------------------------------------------------
The core shapes (heading / body / bullets / table / matrix / tiles /
timeline) are byte-compatible with make_brief. Sections MAY additionally
carry, on this backend only (they came from the research brief):

  - "people":  [{name, role, note?, buyer?}]   — decision-maker cards
  - "events":  [{when, text, url?}]            — recent-signals strip
  - "sources": [{label, url}]                  — numbered source list
  - bullets items may be dicts {text, url?, low_confidence?} — cited findings;
    plain strings render exactly as on the docx side.

Widget fence (SPEC OUT5 §4): this is a DELIVERABLE surface, not a chat
widget. render_and_persist / show_widget contracts do not apply to saved
briefs, and nothing here composes widget HTML.

Stdlib only (python-docx is never imported — an HTML-only render must not
trigger the docx self-install).
"""
from __future__ import annotations

import html as _html
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from brand import get_brand  # noqa: E402
from output_profile import (  # noqa: E402
    get_output_profile,
)
from brief_gates import (  # noqa: E402
    EYEBROW_BY_KIND,
    STANDARD_KINDS,  # noqa: F401  (re-exported for callers; gate uses it internally)
    EXEC_EYEBROW_EXCLUDED_KINDS,
    ASKS_HEADING,
    EXEC_HEADER_LINES,
    run_pre_save_gates,
    warn_page_cap,
    emit_gate_ran_audit,
)
from components import (  # noqa: E402
    build_tile_band_html,
    build_table_html,
    build_matrix_html,
    build_timeline_html,
)

TEMPLATE_PATH = _HERE.parent / "templates" / "premium_brief.html"

# The premium backend renders every docx kind PLUS research (research has no
# docx eyebrow because it was born HTML — its docx export rides other kinds).
PREMIUM_EYEBROW_BY_KIND = dict(EYEBROW_BY_KIND, research="RESEARCH")
PREMIUM_SUPPORTED_KINDS = frozenset(PREMIUM_EYEBROW_BY_KIND)

# research leads with the verdict-as-bottom-line alone (EXEC1: the verdict IS
# the bottom line — no CHANGED/DECIDE/NEEDED digest eyebrow on a research
# brief), joining the FS-13 document/decision kinds.
_VERDICT_ONLY_KINDS = EXEC_EYEBROW_EXCLUDED_KINDS | {"research"}

# Source-tier and confidence chips (the research badge contract, now shared).
_SOURCE_CHIP_LABELS = {
    "enriched": "Verified &middot; Vibe Prospecting",
    "tavily": "Deep web &middot; Tavily",
    "web": "Web sources only",
}
_CONFIDENCE_CHIP_LABELS = {
    "high": "High confidence",
    "medium": "Medium confidence",
    "low": "Low confidence",
}


# ---------------------------------------------------------------------------
# Brand → template variable resolution
# ---------------------------------------------------------------------------

def _lighten(hexstr: str, factor: float) -> str:
    """Blend a 6-hex color toward white by `factor` (0..1). Returns 6-hex, no
    '#'. Used to derive the soft accent (the research template's gold role)
    from the brand accent so one palette key themes the whole page."""
    h = str(hexstr).lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (round(c + (255 - c) * factor) for c in (r, g, b))
    return f"{r:02X}{g:02X}{b:02X}"


def _rgba(hexstr: str, alpha: float) -> str:
    h = str(hexstr).lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _font_stack(name: str, fallbacks: str) -> str:
    quoted = f"'{name}'" if name and not name.startswith("'") else name
    return f"{quoted},{fallbacks}"


def _template_vars(resolved_brand: dict, profile: dict) -> Dict[str, str]:
    accent = resolved_brand["palette"]["accent"]
    fonts = resolved_brand["fonts"]
    footer_line = str(resolved_brand.get("footer_line") or "Command Room")
    monogram = next((c for c in footer_line if c.isalnum()), "C").upper()
    narrative = profile.get("density") == "narrative"
    return {
        "ACCENT": accent,
        "ACCENT_SOFT": _lighten(accent, 0.35),
        "ACCENT_GLOW": _rgba(accent, 0.10),
        "ACCENT_SELECTION": _rgba(accent, 0.28),
        "ACCENT_WASH": _rgba(accent, 0.10),
        "ACCENT_WASH_FAINT": _rgba(accent, 0.02),
        "ACCENT_WASH_LIGHT": _rgba(accent, 0.07),
        "ACCENT_BORDER": _rgba(accent, 0.5),
        "ACCENT_DOTTED": _rgba(accent, 0.5),
        "SERIF_STACK": _font_stack(
            fonts["heading"], "'Cormorant Garamond',Georgia,'Times New Roman',serif"
        ),
        "SANS_STACK": _font_stack(
            fonts["body"], "'Inter',system-ui,-apple-system,'Helvetica Neue',Arial,sans-serif"
        ),
        "MONO_STACK": _font_stack(
            fonts["mono"], "'JetBrains Mono','SF Mono',Menlo,Consolas,monospace"
        ),
        "SERIF_SVG": f"'{fonts['heading']}',Georgia,serif",
        # density (SPEC OUT2 §5 parity): tight = the template's native leading;
        # narrative = looser body leading + paragraph space, mirroring the
        # docx backend's 1.25/6pt -> 1.40/10pt shift.
        "BODY_LEADING": "1.85" if narrative else "1.65",
        "BODY_SPACE": "13px" if narrative else "8px",
        "WORDMARK": _html.escape(footer_line),
        "FOOTER_LINE": _html.escape(footer_line),
        "MONOGRAM": _html.escape(monogram),
    }


# ---------------------------------------------------------------------------
# Fragment renderers (research-heritage regions; core shapes ride components.py)
# ---------------------------------------------------------------------------

class _CiteCounter:
    """Running [n] citation numbering across cited bullets and events. The
    composer keeps its sources section in the same order it cites — the same
    discipline the research skill already holds."""

    def __init__(self) -> None:
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def _cite_anchor(url: str, counter: _CiteCounter) -> str:
    return (
        f'<a class="cite" href="{_html.escape(str(url), quote=True)}">'
        f'[{counter.next()}]</a>'
    )


def _bullets_html(bullets: List, counter: _CiteCounter) -> str:
    items: List[str] = []
    for b in bullets:
        if isinstance(b, dict):
            text = _html.escape(str(b.get("text") or "").strip())
            if b.get("low_confidence"):
                text = f'<span class="flag-low">{text}</span>'
            cite = _cite_anchor(b["url"], counter) if b.get("url") else ""
            items.append(f"<li>{text}{cite}</li>")
        else:
            line = str(b).strip()
            if not line:
                continue
            if line.startswith(("- ", "* ", "• ")):
                line = line[2:].lstrip()
            items.append(f"<li>{_html.escape(line)}</li>")
    return "<ul>" + "".join(items) + "</ul>" if items else ""


def _people_html(people: List[dict]) -> str:
    cards: List[str] = []
    for p in people:
        if not isinstance(p, dict) or not str(p.get("name") or "").strip():
            raise ValueError(f"each person needs a non-empty 'name': {p!r}")
        buyer = bool(p.get("buyer"))
        cls = "person buyer" if buyer else "person"
        parts = [
            f'<div class="{cls}">',
            f'<span class="name">{_html.escape(str(p["name"]).strip())}</span>',
        ]
        role = str(p.get("role") or "").strip()
        if role:
            parts.append(f'<span class="role">{_html.escape(role)}</span>')
        if buyer:
            parts.append('<span class="tag">Likely buyer</span>')
        note = str(p.get("note") or "").strip()
        if note:
            parts.append(f'<p class="note">{_html.escape(note)}</p>')
        parts.append("</div>")
        cards.append("".join(parts))
    return '<div class="people">' + "".join(cards) + "</div>" if cards else ""


def _events_html(events: List[dict], counter: _CiteCounter) -> str:
    items: List[str] = []
    for ev in events:
        if not isinstance(ev, dict) or not str(ev.get("text") or "").strip():
            raise ValueError(f"each event needs a non-empty 'text': {ev!r}")
        when = _html.escape(str(ev.get("when") or "").strip())
        text = _html.escape(str(ev["text"]).strip())
        cite = _cite_anchor(ev["url"], counter) if ev.get("url") else ""
        items.append(
            f'<li><span class="when">{when}</span><span>{text}{cite}</span></li>'
        )
    return '<ul class="events">' + "".join(items) + "</ul>" if items else ""


def _sources_html(sources: List[dict]) -> str:
    items: List[str] = []
    for s in sources:
        if not isinstance(s, dict) or not str(s.get("label") or "").strip():
            raise ValueError(f"each source needs a non-empty 'label': {s!r}")
        label = _html.escape(str(s["label"]).strip())
        url = str(s.get("url") or "").strip()
        if url:
            items.append(
                f'<li><a class="cite" href="{_html.escape(url, quote=True)}" '
                f'style="margin-left:0">{label}</a></li>'
            )
        else:
            items.append(f"<li>{label}</li>")
    return "<ol>" + "".join(items) + "</ol>" if items else ""


def _badges_html(badges: Optional[dict]) -> str:
    if not badges:
        return ""
    if not isinstance(badges, dict):
        raise ValueError(f"badges must be a dict or None, got {type(badges).__name__}")
    chips: List[str] = []
    source = badges.get("source")
    if source is not None:
        if source not in _SOURCE_CHIP_LABELS:
            raise ValueError(
                f"badges.source must be one of {sorted(_SOURCE_CHIP_LABELS)}, "
                f"got {source!r} — never claim a tier whose tools were absent"
            )
        chips.append(f'<span class="chip {source}">{_SOURCE_CHIP_LABELS[source]}</span>')
    confidence = badges.get("confidence")
    if confidence is not None:
        if confidence not in _CONFIDENCE_CHIP_LABELS:
            raise ValueError(
                f"badges.confidence must be one of {sorted(_CONFIDENCE_CHIP_LABELS)}, "
                f"got {confidence!r}"
            )
        chips.append(
            f'<span class="chip {confidence}">{_CONFIDENCE_CHIP_LABELS[confidence]}</span>'
        )
    if not chips:
        return ""
    return '<div class="badges">' + "".join(chips) + '</div>\n<hr class="rule">'


def _exec_header_html(
    exec_header: Optional[Dict[str, str]], brief_kind: str
) -> str:
    """EXEC1 element 1 — the 30-second contract, mirroring the docx renderer:
    verdict lead always; CHANGED/DECIDE/NEEDED eyebrow lines for brief-family
    kinds only (FS-13 excluded kinds + research render the verdict alone)."""
    if not exec_header:
        return ""
    verdict = (exec_header.get("verdict") or "").strip()
    if not verdict:
        return ""
    parts = [f'<p class="bottomline">{_html.escape(verdict)}</p>']
    if brief_kind not in _VERDICT_ONLY_KINDS:
        lines: List[str] = []
        for key, label in EXEC_HEADER_LINES:
            text = (exec_header.get(key) or "").strip()
            if not text:
                continue
            lines.append(
                f'<div class="exec-line"><span class="exec-label">{label}</span>'
                f'<span>{_html.escape(text)}</span></div>'
            )
        if lines:
            parts.append('<div class="exec-lines">' + "".join(lines) + "</div>")
    parts.append('<hr class="rule">')
    return "\n".join(parts)


def _asks_html(asks: Optional[List[Dict[str, str]]]) -> str:
    """EXEC1 element 4 — the ASK block, last content block, canonical heading.
    Same deadline form as the docx renderer (' — by <when>')."""
    if not asks:
        return ""
    items: List[str] = []
    for ask in asks:
        text = (ask.get("text") or "").strip()
        if not text:
            continue
        deadline = (ask.get("deadline") or "").strip()
        line = _html.escape(text)
        if deadline:
            line += f'<span class="ask-deadline"> — by {_html.escape(deadline)}</span>'
        items.append(f"<li>{line}</li>")
    if not items:
        return ""
    return (
        '<section class="section"><h2>'
        + _html.escape(ASKS_HEADING)
        + '</h2><div class="asks"><ul>'
        + "".join(items)
        + "</ul></div></section>"
    )


def _body_html(body: str) -> str:
    """Blank-line-separated blocks become paragraphs — same split rule as the
    docx backend's _add_body_paragraphs."""
    paras = []
    for block in str(body).split("\n\n"):
        block = block.strip()
        if block:
            paras.append(f"<p>{_html.escape(block)}</p>")
    return "".join(paras)


def _section_html(sec: dict, visual_bias: str, counter: _CiteCounter) -> str:
    heading = sec.get("heading")
    if not heading:
        raise ValueError(f"section missing 'heading': {sec!r}")
    body = sec.get("body")
    bullets = sec.get("bullets")
    table = sec.get("table")
    matrix = sec.get("matrix")
    tiles = sec.get("tiles")
    timeline = sec.get("timeline")
    people = sec.get("people")
    events = sec.get("events")
    sources = sec.get("sources")
    if tiles and not isinstance(tiles, list):
        raise ValueError(f"section 'tiles' must be a list: {sec!r}")
    if body and not isinstance(body, str):
        raise ValueError(f"section 'body' must be a string: {sec!r}")

    parts: List[str] = [
        f'<section class="section"><h2>{_html.escape(str(heading))}</h2>'
    ]

    # SPEC OUT2 §5 — visual_bias sets the tiles/body order within a section,
    # mirroring the docx backend exactly ("tiles_first" is the default order).
    tiles_html = build_tile_band_html(tiles, validate=True) if tiles else ""
    body_html = _body_html(body) if body else ""
    if visual_bias == "prose_first":
        parts.append(body_html + tiles_html)
    else:
        parts.append(tiles_html + body_html)

    if bullets:
        if not isinstance(bullets, list):
            raise ValueError(f"section 'bullets' must be a list: {sec!r}")
        parts.append(_bullets_html(bullets, counter))
    if table:
        if not isinstance(table, dict):
            raise ValueError(f"section 'table' must be a dict: {sec!r}")
        parts.append(build_table_html(
            table["rows"],
            headers=table.get("headers"),
            highlight_row_idx=table.get("highlight_row_idx"),
        ))
    if matrix:
        if not isinstance(matrix, dict):
            raise ValueError(f"section 'matrix' must be a dict: {sec!r}")
        parts.append(build_matrix_html(
            matrix["cells"],
            headers_row=matrix.get("headers_row"),
            headers_col=matrix.get("headers_col"),
            star_col_idx=matrix.get("star_col_idx"),
            flag_col_idx=matrix.get("flag_col_idx"),
        ))
    if timeline:
        if not isinstance(timeline, list):
            raise ValueError(f"section 'timeline' must be a list: {sec!r}")
        parts.append(build_timeline_html(timeline))
    if people:
        if not isinstance(people, list):
            raise ValueError(f"section 'people' must be a list: {sec!r}")
        parts.append(_people_html(people))
    if events:
        if not isinstance(events, list):
            raise ValueError(f"section 'events' must be a list: {sec!r}")
        parts.append(_events_html(events, counter))
    if sources:
        if not isinstance(sources, list):
            raise ValueError(f"section 'sources' must be a list: {sec!r}")
        parts.append(_sources_html(sources))

    if not any((body, bullets, table, matrix, tiles, timeline,
                people, events, sources)):
        raise ValueError(
            f"section needs 'body', 'bullets', 'table', 'matrix', 'tiles', "
            f"'timeline', 'people', 'events', or 'sources': {sec!r}"
        )

    parts.append("</section>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def make_premium_brief(
    output_path: str,
    *,
    brief_kind: str,
    title: str,
    subtitle: str,
    sections: List[Dict[str, Union[str, List[str]]]],
    footer_text: Optional[str] = None,
    voice_gate: str = "default",
    contract: str = "enforce",
    contract_profile: Optional[str] = None,
    exec_header: Optional[Dict[str, str]] = None,
    asks: Optional[List[Dict[str, str]]] = None,
    workspace_root: Optional[str] = None,
    brand: Optional[dict] = None,
    org_id: Optional[str] = None,
    badges: Optional[dict] = None,
    source_summary: Optional[str] = None,
) -> str:
    """Write a premium self-contained HTML brief to `output_path` and return
    the path — the format twin of `brief_writer.make_brief`, same kwargs plus
    two research-heritage extras (`badges`, `source_summary`).

    The caller resolves `output_path` exactly as for the docx twin (same
    folder, same naming, `.html` extension) and links it via
    `brief_path.get_brief_artifact_url()`. After this returns, ASSERT the file
    exists — the return is the proof the render actually happened (the
    field-report class this module exists to close).

    Gate order (identical to make_brief, parity-pinned): input validation →
    contract gate → voice gate → exec-header requirement → render → post-save
    leak scan (`docx_leak_scanner.scan_html_for_leaks`, same forbidden-token
    list as the docx scan). Raises ValueError / OutputContractError /
    VoiceTellError / LeakScanError exactly as the docx twin does; a pre-render
    raise writes no file.

    Extra args over make_brief:
      badges: optional {"source": "enriched"|"tavily"|"web",
        "confidence": "high"|"medium"|"low"} — the research chip contract
        (emit ONE source tier, the strongest actually used, and ONE confidence
        chip; never claim a tier whose tools were absent). Any kind may pass
        them; research always does.
      source_summary: optional one-line footer source note ("5 sources via
        Tavily + Vibe Prospecting enrichment"). Omitted = quiet footer.
      footer_text: overrides the brand `footer_line` in the page chrome
        (wordmark + page foot), same precedence as the docx footer.
    """
    # SPEC OUT5 §3b — the SHARED pre-save gate stack (brief_gates). Identical
    # call to the docx backend's; do not add gates inline here (G16).
    gates_ran: List[str] = run_pre_save_gates(
        brief_kind=brief_kind,
        title=title,
        subtitle=subtitle,
        sections=sections,
        supported_kinds=PREMIUM_SUPPORTED_KINDS,
        contract=contract,
        contract_profile=contract_profile,
        voice_gate=voice_gate,
        exec_header=exec_header,
        asks=asks,
        workspace_root=workspace_root,
    )

    # Theme + profile, resolved per render exactly like the docx backend
    # (explicit brand= > workspace/org resolution > byte-stable defaults).
    resolved_brand = brand if brand is not None else get_brand(workspace_root, org_id)
    if footer_text is not None:
        resolved_brand = dict(resolved_brand, footer_line=footer_text)
    resolved_profile = get_output_profile(workspace_root)
    warn_page_cap(resolved_profile, brief_kind, title, subtitle, sections)

    tmpl = TEMPLATE_PATH.read_text(encoding="utf-8")

    counter = _CiteCounter()
    visual_bias = (
        resolved_profile.get("visual_bias")
        if resolved_profile.get("visual_bias") in ("tiles_first", "prose_first")
        else "tiles_first"
    )
    content = "\n".join(
        _section_html(sec, visual_bias, counter) for sec in sections
    )

    source_line = (source_summary or "").strip()
    if source_line:
        source_html = _html.escape(source_line)
    else:
        # Quiet footer: drop the separator + source spans via CSS-free empty
        # string (the spans render empty, invisible at 0 content).
        source_html = ""

    fills = _template_vars(resolved_brand, resolved_profile)
    fills.update({
        "TITLE": _html.escape(str(title)),
        "SUBTITLE": _html.escape(str(subtitle)),
        "KIND_LABEL": _html.escape(PREMIUM_EYEBROW_BY_KIND[brief_kind]),
        "BADGES": _badges_html(badges),
        "EXEC_HEADER": _exec_header_html(exec_header, brief_kind),
        "CONTENT": content,
        "ASKS": _asks_html(asks),
        "SOURCE_SUMMARY": source_html,
    })
    page = tmpl
    for token, value in fills.items():
        page = page.replace("{{" + token + "}}", value)

    out = Path(output_path)
    out.write_text(page, encoding="utf-8")

    # Post-save leak scan — the format twin of make_brief's docx scan, same
    # canonical forbidden-token list, same lazy-import tolerance (a workspace
    # mid-update that lacks the scanner still saves; the scanner applies on
    # the next plugin update).
    try:
        from docx_leak_scanner import scan_html_for_leaks
        scan_html_for_leaks(str(out))
        gates_ran.append("leak")
    except ImportError:
        pass

    # SPEC GATE1 — the detectable-bypass audit, surface="premium_html" so the
    # verify loop's *_drafted ⋈ gate_ran join works per backend.
    emit_gate_ran_audit(
        brief_kind, gates_ran, str(out), workspace_root, surface="premium_html"
    )

    return str(output_path)


def make_premium_brief_from_json(json_payload: str) -> str:
    """JSON wrapper for orchestrator / skill bash invocations — the format
    twin of `brief_writer.make_brief_from_json`. Pipe a JSON object on stdin
    OR pass as the first CLI arg; keys mirror `make_premium_brief()` kwargs.
    Prints and returns the output path for shell capture (the caller asserts
    the file exists — acceptance #5's checked-not-assumed render)."""
    payload = json.loads(json_payload)
    path = make_premium_brief(
        payload["output_path"],
        brief_kind=payload["brief_kind"],
        title=payload["title"],
        subtitle=payload["subtitle"],
        sections=payload["sections"],
        footer_text=payload.get("footer_text"),
        voice_gate=payload.get("voice_gate", "default"),
        contract=payload.get("contract", "enforce"),
        contract_profile=payload.get("contract_profile"),
        exec_header=payload.get("exec_header"),
        asks=payload.get("asks"),
        workspace_root=payload.get("workspace_root"),
        brand=payload.get("brand"),
        org_id=payload.get("org_id"),
        badges=payload.get("badges"),
        source_summary=payload.get("source_summary"),
    )
    print(path)
    return path


__all__ = [
    "PREMIUM_EYEBROW_BY_KIND",
    "PREMIUM_SUPPORTED_KINDS",
    "TEMPLATE_PATH",
    "make_premium_brief",
    "make_premium_brief_from_json",
]


if __name__ == "__main__":
    # CLI: `python3 premium_html.py '<json>'` OR pipe JSON on stdin.
    if len(sys.argv) > 1:
        make_premium_brief_from_json(sys.argv[1])
    else:
        make_premium_brief_from_json(sys.stdin.read())
