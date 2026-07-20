#!/usr/bin/env python3
"""
Universal post-render .docx leak scanner (v3.13.8+ — Bug #57 + #59 + #54).

WHY THIS EXISTS
---------------

Bug #57: decision-memo-composer shipped a memo containing the literal
substring `project_020` in body prose — only Cowork's post-hoc audit caught
it. Bug #59: board-pack-assembler shipped substrate paths (events.jsonl,
_hq/...) in Appendix C. Bug #54: the prior extract-text validator returned
0 characters on some docx outputs, producing "false-clean" verdicts that
let leaks through.

Root cause: every .docx writer skill ran its OWN leak scan (or none at all)
with different forbidden-token lists. No single gate.

THE FIX
-------

A canonical scanner that:
  1. Unzips the .docx, reads word/document.xml.
  2. Collapses <w:r> run boundaries so tokens split across runs reassemble
     (per Bug #54's refinement — `eco</w:r><w:r>system` is still ecosystem).
  3. Strips ALL XML tags.
  4. Word-boundary-anchored regex match against the canonical forbidden-
     token list. This avoids false positives ("TTL" inside "settled") AND
     false negatives ("Phase 3" matching "Phase").
  5. Raises LeakScanError listing every match.

Called by brief_writer.make_brief() after every doc.save(). Skills writing
.docx that bypass brief_writer SHOULD migrate; until then they can call
scan_docx_for_leaks(path) themselves.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path
from typing import List

try:
    from vocabulary_policy import marketing_patterns
except ImportError:  # pragma: no cover — direct-path import fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from vocabulary_policy import marketing_patterns


class LeakScanError(RuntimeError):
    """Raised when scan_docx_for_leaks finds forbidden tokens in the rendered
    document. Lists every offending pattern + the matched text."""


# Canonical forbidden-token patterns. Word-boundary-anchored regex.
# Order matters only for the `findings` output ordering; the scanner runs
# all patterns regardless of which match first.
_FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    # Internal IDs — Bug #57 root cause
    ("internal_id", r"\b(?:project|person|org|event|thread)_\d+\b"),

    # Substrate paths — Bug #59 root cause
    ("substrate_path_events", r"\bevents\.jsonl\b"),
    ("substrate_path_entities", r"\bentities\.jsonl?\b"),
    ("substrate_path_aliases", r"\baliases\.json\b"),
    ("substrate_path_hq", r"\b_hq/(?:data|.system|skills)\b"),

    # Voice-contract forbidden — process narration (Bug #16 / #60)
    ("voice_phase", r"\bPhase \d+\b"),
    ("voice_tier", r"\bTier \d+\b"),
    ("voice_stop_contract", r"\bSTOP_CONTRACT\b"),
    ("voice_orchestrator", r"\borchestrator\b"),
    ("voice_bootloader", r"\bbootloader\b"),
    ("voice_validator_passed", r"\bvalidator passed\b"),
    ("voice_classifier", r"\bclassification_confidence\b"),

    # Marketing-speak forbidden words — sourced from the ONE shared
    # vocabulary list (v4.6.1 S3, F-53 P3a: the voice gate reads the same
    # list, so a word blocked in a docx can no longer lead an email).
    # Add/remove words in shared/scripts/vocabulary_policy.py, never here.
    *marketing_patterns(),
]


def _read_document_xml(docx_path: Path) -> str:
    """Read word/document.xml from inside the .docx zip. Returns the raw
    XML string — caller is responsible for tag stripping + run collapsing."""
    with zipfile.ZipFile(str(docx_path)) as z:
        try:
            return z.read("word/document.xml").decode("utf-8", errors="replace")
        except KeyError:
            return ""


_RUN_BOUNDARY_RE = re.compile(r"</w:t>\s*</w:r>\s*<w:r[^>]*>\s*<w:t[^>]*>")
_XML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _decode_entities(text: str) -> str:
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )


def _normalize_for_scan(xml: str) -> str:
    """Collapse <w:r> run boundaries inside text runs, strip XML tags, normalize
    whitespace. This is what makes the scanner robust to Word's habit of
    splitting words across multiple runs for styling reasons.
    """
    # Step 1: collapse run boundaries that interrupt a text fragment.
    collapsed = _RUN_BOUNDARY_RE.sub("", xml)
    # Step 2: strip all remaining XML tags.
    text = _XML_TAG_RE.sub(" ", collapsed)
    # Step 3: decode common XML entities so &quot; doesn't break word-boundary scans.
    text = _decode_entities(text)
    # Step 4: collapse whitespace.
    text = _WHITESPACE_RE.sub(" ", text)
    return text


_PARA_SPLIT_RE = re.compile(r"</w:p\s*>")

# Innermost <w:tbl>…</w:tbl> region (no nested <w:tbl> inside). Applied in a
# loop so nested tables strip inside-out. Used by _docx_paragraph_text ONLY —
# the voice-tell structural rules (incl. the FB-16 dash ban) apply to body
# PROSE, not table/matrix cells; check_sections makes the same exemption for
# section-shaped input. The LEAK scan is unaffected (it runs on
# _normalize_for_scan's full-document text, tables included).
_TBL_REGION_RE = re.compile(r"<w:tbl\b(?:(?!<w:tbl\b).)*?</w:tbl\s*>", re.DOTALL)


def _strip_tables(xml: str) -> str:
    prev = None
    while prev != xml:
        prev = xml
        xml = _TBL_REGION_RE.sub(" ", xml)
    return xml


def _docx_paragraph_text(xml: str) -> str:
    """Reconstruct the document as newline-separated paragraphs.

    `_normalize_for_scan` collapses the whole doc to ONE line — fine for the
    word-boundary leak patterns, but it destroys the paragraph structure the
    voice-tell detector's structural rules (tri-colon, em-dash pile-up, hedging
    stacks) need. This splits on `</w:p>` first so each Word paragraph becomes
    its own line, then run-collapses + tag-strips each piece. Paragraphs are
    joined with a blank line so `voice_tell_detector._iter_paragraphs` sees
    real paragraph boundaries.

    (SPEC GATE2 D2 — needed so the unified scanner catches the SAME structural
    tells voice_tell_detector finds in chat prose, in a hand-rolled .docx too.)

    Table content (<w:tbl> regions) is STRIPPED first: structural voice rules —
    including the FB-16 dash-as-punctuation FAIL — apply to body prose only,
    never table/matrix cells (an en-dash range in a stat table is data, not
    voice). Mirrors check_sections' body-only exemption. The leak scan is
    unaffected — it reads _normalize_for_scan's full text, tables included.
    """
    paras: List[str] = []
    for chunk in _PARA_SPLIT_RE.split(_strip_tables(xml)):
        collapsed = _RUN_BOUNDARY_RE.sub("", chunk)
        line = _XML_TAG_RE.sub(" ", collapsed)
        line = _decode_entities(line)
        line = _WHITESPACE_RE.sub(" ", line).strip()
        if line:
            paras.append(line)
    return "\n\n".join(paras)


def scan_text_for_leaks(text: str, *, surface: str | None = None) -> List[dict]:
    """Run the canonical forbidden-token patterns over an arbitrary text blob
    (a chat-rendered memo/email body, not a .docx). Same patterns the .docx
    scanner uses, so a `Phase 3` / `project_020` / `leverage` leak is caught in
    chat prose exactly as it is in a document. Never raises — returns findings.

    (SPEC GATE2 D4 — the chat-prose path. The memo that freelanced as chat text
    in the live test carried a `Phase N` leak; this catches it.)

    SPEC PGUARD1 D2 — `surface`: when the caller declares an
    org/board/client/external audience (personal_leak.is_org_surface), the
    personal-content patterns run too and their findings merge into the
    result (so raise-on-findings callers BLOCK on them). Absent/owner
    surfaces never get the personal scan — a brief legitimately carries
    personal rows."""
    if not text:
        return []
    findings: List[dict] = []
    for name, pattern in _FORBIDDEN_PATTERNS:
        for m in re.finditer(pattern, text):
            start, end = m.span()
            findings.append(
                {
                    "name": name,
                    "pattern": pattern,
                    "match": m.group(0),
                    "context": text[max(0, start - 20) : min(len(text), end + 20)],
                }
            )
    if surface is not None:
        try:
            from personal_leak import is_org_surface, scan_for_personal_leak
            if is_org_surface(surface):
                findings.extend(scan_for_personal_leak(text))
        except ImportError:  # partial-update tolerance; the base scan stands
            sys.stderr.write(
                "[docx_leak_scanner] WARN: personal_leak module missing — "
                "the org-surface personal-content scan did NOT run.\n"
            )
    return findings


def scan_docx_for_leaks(docx_path: str | Path, *, surface: str | None = None) -> List[dict]:
    """Scan `docx_path` for any forbidden tokens. Returns the list of
    findings (empty if clean). Raises LeakScanError if findings are
    non-empty.

    The exception path is the default — every .docx writer expects this
    function to either return [] (silent success) or raise (loud failure).

    Pass return_findings=False if you want to collect findings without
    raising — useful for audit passes that report rather than block.

    `surface` (SPEC PGUARD1 D2): an org/board/client/external tag adds the
    BLOCKING personal-content scan — a board pack or advisor export carrying
    a personal-lane fingerprint raises here. Owner-facing docs (the brief)
    pass no surface and are unaffected.
    """
    return _scan_docx(docx_path, raise_on_findings=True, surface=surface)


def collect_docx_leaks(docx_path: str | Path, *, surface: str | None = None) -> List[dict]:
    """Same as scan_docx_for_leaks but never raises — returns the findings
    list for callers that want to audit/report rather than block. Useful for
    audit tools, weekly-audit, and pre-ship gates."""
    return _scan_docx(docx_path, raise_on_findings=False, surface=surface)


def _scan_docx(docx_path: str | Path, raise_on_findings: bool,
               surface: str | None = None) -> List[dict]:
    docx_path = Path(docx_path)
    if not docx_path.exists():
        raise FileNotFoundError(f".docx not found: {docx_path}")

    xml = _read_document_xml(docx_path)
    if not xml:
        # Unusual — file exists but has no document.xml. Surface as a hard
        # failure rather than false-clean, per Bug #54.
        raise LeakScanError(
            f"Could not read word/document.xml from {docx_path.name}; "
            f"unable to verify the document is leak-free."
        )

    # The full pattern set (incl. the surface-gated personal scan) lives in
    # scan_text_for_leaks — one implementation, every file format.
    findings = scan_text_for_leaks(_normalize_for_scan(xml), surface=surface)

    if findings and raise_on_findings:
        # Build a compact summary for the exception message
        summary_lines = []
        for f in findings[:10]:
            summary_lines.append(f"  [{f['name']}] {f['match']!r} (…{f['context'][:60]}…)")
        more = f"\n  …and {len(findings) - 10} more" if len(findings) > 10 else ""
        raise LeakScanError(
            f"Forbidden tokens in {docx_path.name}:\n"
            + "\n".join(summary_lines)
            + more
        )
    return findings


# ---------------------------------------------------------------------------
# SPEC OUT5 — the premium-HTML sibling of the .docx scan. Same canonical
# forbidden-token list, same raise-on-findings posture, one scanner per file
# format. Lives in this module ON PURPOSE: the pattern list must never fork.
# ---------------------------------------------------------------------------

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_STYLE_SCRIPT_RE = re.compile(
    r"<(style|script)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Inline (phrasing-level) tags render with NO spacing of their own — the reader
# sees `project_<b>020</b>` as `project_020`, and a syntax-highlighted
# `<span>events</span><span>.jsonl</span>` as `events.jsonl`. Stripping these
# with "" (not " ") keeps the scanned text render-faithful, so a token split
# across inline markup is caught exactly like the docx path catches a token
# split across adjacent <w:r> runs. Every non-inline tag still becomes a space.
# (FU1 second-eyes FIX 2.)
_HTML_INLINE_TAG_RE = re.compile(
    r"</?(?:a|abbr|b|bdi|bdo|cite|code|data|del|dfn|em|i|ins|kbd|mark|q|s|samp"
    r"|small|span|strong|sub|sup|time|u|var|wbr)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_URL_ATTR_RE = re.compile(r"""(?:href|src)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
# Block-level boundaries that separate visible paragraphs in an HTML render.
# Splitting on these BEFORE stripping tags preserves the paragraph structure the
# voice-tell structural rules need — a tri-colon / em-dash pile-up spread across
# three <p> blocks must still read as three lines, not one collapsed blob.
# `</td>`/`</th>` (+ dt/dd) are boundaries too: in a .docx every table cell is
# its own <w:p> paragraph, so cells in an HTML row must not collapse into one
# line — a label/value KPI row ("Status: green | Owner: Sam | Risk: low")
# joined across cells false-fires the tri-colon rule the docx twin never sees.
# (FU1 second-eyes FIX 1.)
_HTML_BLOCK_BOUNDARY_RE = re.compile(
    r"</(?:p|h[1-6]|li|tr|td|th|dt|dd|div)\s*>|<br\s*/?>", re.IGNORECASE
)
# Innermost <table>…</table> region (no nested <table> inside), applied in a
# loop so nested tables strip inside-out. Used by _html_paragraph_text ONLY —
# the voice-tell structural rules (incl. the FB-16 dash-as-punctuation FAIL,
# landed on main @ fab31a4) apply to body PROSE, not table/matrix cells: an
# en-dash range in a stat table is data, not voice. This is the html twin of
# `_strip_tables` on the docx path — without it a swept premium scorecard
# (table-heavy by design) FAILs on cell ranges its docx twin is exempt from.
# The LEAK scan is unaffected (it runs on `_html_visible_text`, tables
# included). (FU1 second-eyes FIX 3.)
_HTML_TABLE_REGION_RE = re.compile(
    r"<table\b(?:(?!<table\b).)*?</table\s*>", re.IGNORECASE | re.DOTALL
)


def _strip_html_tables(html_text: str) -> str:
    prev = None
    while prev != html_text:
        prev = html_text
        html_text = _HTML_TABLE_REGION_RE.sub(" ", html_text)
    return html_text


def _html_visible_text(html_text: str) -> str:
    """Reader-visible text of an HTML document, plus every href/src value.

    Comments and <style>/<script> blocks are dropped (CSS/JS is not reader
    text and its property names would only produce noise), remaining tags are
    stripped, entities decoded, whitespace collapsed. Link/image TARGETS are
    appended to the scanned text so a substrate path or internal ID hiding in
    an href (invisible on the page, live on click) is caught exactly like body
    prose."""
    import html as _html_mod

    urls = " ".join(_HTML_URL_ATTR_RE.findall(html_text))
    text = _HTML_COMMENT_RE.sub(" ", html_text)
    text = _HTML_STYLE_SCRIPT_RE.sub(" ", text)
    # Inline tags join (render-faithful — they add no spacing on the page);
    # everything else separates. Order matters: inline first, then the rest.
    text = _HTML_INLINE_TAG_RE.sub("", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _html_mod.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text + " " + _html_mod.unescape(urls))
    return text


def _html_paragraph_text(html_text: str) -> str:
    """Reconstruct an HTML document as blank-line-separated visible paragraphs.

    `_html_visible_text` collapses the whole page to ONE line — right for the
    word-boundary leak patterns, wrong for the voice-tell structural rules
    (tri-colon, em-dash pile-up, hedging stacks), which read paragraph by
    paragraph. This drops comments + <style>/<script> first (same as the
    visible-text path), splits on block-level boundaries (</p>, </h1..6>,
    </li>, </tr>, </td>, </th>, </dt>, </dd>, </div>, <br>), then strips tags
    (inline tags join, render-faithful; the rest separate) / decodes entities /
    collapses whitespace per piece and joins with a blank line so
    `voice_tell_detector._iter_paragraphs` sees real boundaries. Link/image
    TARGETS are intentionally NOT appended here — an href is not prose; it
    belongs to the leak scan's visible-text path, not the voice structural scan.

    (SPEC FU1 D2 — the html twin of `_docx_paragraph_text`. Minified or
    <br>-only layouts degrade gracefully to fewer paragraphs: fewer structural
    detections, leak scan unaffected — SPEC FU1 R3.)
    """
    import html as _html_mod

    stripped = _HTML_COMMENT_RE.sub(" ", html_text)
    stripped = _HTML_STYLE_SCRIPT_RE.sub(" ", stripped)
    # Table regions are DATA, not voice prose — stripped from the voice path
    # only, mirroring the docx `_strip_tables` exemption (FB-16). The leak scan
    # still reads them via `_html_visible_text`.
    stripped = _strip_html_tables(stripped)
    paras: List[str] = []
    for chunk in _HTML_BLOCK_BOUNDARY_RE.split(stripped):
        line = _HTML_INLINE_TAG_RE.sub("", chunk)
        line = _HTML_TAG_RE.sub(" ", line)
        line = _html_mod.unescape(line)
        line = _WHITESPACE_RE.sub(" ", line).strip()
        if line:
            paras.append(line)
    return "\n\n".join(paras)


def scan_html_for_leaks(html_path: str | Path, *, surface: str | None = None) -> List[dict]:
    """Scan a saved premium-HTML deliverable for forbidden tokens (SPEC OUT5).

    The exact contract of `scan_docx_for_leaks`, per file format: returns []
    when clean, raises LeakScanError listing every match otherwise. An
    unreadable or empty file is a LOUD failure (the Bug #54 posture — never a
    false-clean). Called by `premium_html.make_premium_brief` after every
    save, mirroring make_brief's post-render scan. `surface` (PGUARD1 D2):
    org/board/client tags add the blocking personal-content scan."""
    html_path = Path(html_path)
    if not html_path.exists():
        raise FileNotFoundError(f".html not found: {html_path}")
    try:
        raw = html_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise LeakScanError(
            f"Could not read {html_path.name} ({type(e).__name__}); "
            f"unable to verify the document is leak-free."
        )
    if not raw.strip():
        raise LeakScanError(
            f"{html_path.name} is empty; unable to verify the document is leak-free."
        )
    findings = scan_text_for_leaks(_html_visible_text(raw), surface=surface)
    if findings:
        summary_lines = []
        for f in findings[:10]:
            summary_lines.append(f"  [{f['name']}] {f['match']!r} (…{f['context'][:60]}…)")
        more = f"\n  …and {len(findings) - 10} more" if len(findings) > 10 else ""
        raise LeakScanError(
            f"Forbidden tokens in {html_path.name}:\n"
            + "\n".join(summary_lines)
            + more
        )
    return findings


def collect_html_leaks(html_path: str | Path, *, surface: str | None = None) -> List[dict]:
    """Same as scan_html_for_leaks but never raises — findings for audit/report
    callers (deliverable sweeps, weekly-audit). Unreadable file returns a
    single synthetic finding so a sweep can FLAG it rather than pass it."""
    try:
        return scan_html_for_leaks(html_path, surface=surface)
    except FileNotFoundError:
        raise
    except LeakScanError as e:
        try:
            raw = Path(html_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        if not raw.strip():
            return [{"name": "unreadable", "pattern": "", "match": "",
                     "context": str(e)}]
        return scan_text_for_leaks(_html_visible_text(raw), surface=surface)


# Self-contained premium HTML (inline CSS, data-URI images) runs bigger than a
# markdown memo; a 2 MB cap would error-flag legit renders. 5 MB flags none of
# the live deliverable-shaped files today and still guards against a runaway
# file. Oversize error-flags LOUD (Bug #54 posture — never a silent
# false-clean). (SPEC FU1 D4.)
_MAX_HTML_BYTES = 5_000_000


def sweep_leak_scan(text: str) -> List[dict]:
    """FLAG-ONLY sweep leak scan: canonical forbidden tokens PLUS personal-lane
    substrate fingerprints, run surface-less.

    The SAVE-TIME gates decide blocking via `surface`; the deliverable sweep
    never knows a produced file's audience, so per PGUARD1's risk rule it stays
    surface-less and NEVER blocks. But personal fingerprints — `rem_…` reminder
    ids, `data-personal="true"` wire attrs, `[personal]` chips, `tie: personal`,
    the personal-lane event token — are substrate MACHINERY, not personal
    content, and must never appear in ANY produced deliverable, so the sweep
    FLAGS them exactly like an internal id or a substrate path. The personal
    findings share the `scan_text_for_leaks` shape ({name, pattern, match,
    context}), so they merge into the sweep's `leaks` list unchanged.

    (SPEC FU1 M-2 — folds PGUARD1's open Step-7 follow-up. It lands ONCE here
    and covers docx + md + html + chat, because every sweep scanner routes its
    leak step through this helper. Import-tolerant: if `personal_leak` is absent
    on a partial update, the forbidden-token scan still stands.)"""
    findings = scan_text_for_leaks(text)  # surface-less → forbidden tokens only
    try:
        from personal_leak import scan_for_personal_leak

        findings = findings + scan_for_personal_leak(text)
    except Exception:
        pass
    return findings


def scan_docx_for_violations(docx_path: str | Path) -> dict:
    """SPEC GATE2 D2 — the unified content scanner. Given a .docx PATH, return
    EVERY voice tell + privacy/substrate leak in it, in one call, regardless of
    how the file was produced.

    This is the load-bearing detector. It opens the file (unzip → document.xml)
    and reads the rendered text, so it catches a doc the LLM hand-rolled via
    `python-docx` / the generic docx skill EXACTLY as well as one that went
    through `brief_writer.make_brief`. Prevention is leaky (the LLM can always
    pip-install docx); reading the produced file is not.

    Returns:
      {
        "path": <str>,
        "leaks":   [ {name, pattern, match, context}, ... ],   # forbidden tokens
        "voice":   {"verdict": "fail"|"warn"|"pass", "findings": [...]},
        "has_violation": bool,   # any leak OR any fail-severity voice finding
        "has_voice_warn": bool,  # any warn-severity structural voice tell
        "error": <str or absent>,  # set instead of the above on a read failure
      }

    NEVER raises — a sweep over a live client workspace must not crash on one
    unreadable file. Read failures come back as {"error": ...} so the caller
    can FLAG ("couldn't verify this doc") rather than silently pass it.
    """
    docx_path = Path(docx_path)
    result: dict = {
        "path": str(docx_path),
        "leaks": [],
        "voice": {"verdict": "pass", "findings": []},
        "has_violation": False,
        "has_voice_warn": False,
    }
    try:
        if not docx_path.exists():
            result["error"] = f"file not found: {docx_path}"
            return result
        xml = _read_document_xml(docx_path)
        if not xml:
            result["error"] = (
                f"could not read word/document.xml from {docx_path.name} — "
                f"unable to verify it is clean"
            )
            return result
    except Exception as e:  # zip corruption, permission error, etc.
        result["error"] = f"unreadable .docx ({type(e).__name__}): {docx_path.name}"
        return result

    # 1. Forbidden-token + personal-fingerprint (flag-only) leak scan over the
    #    collapsed full text (SPEC FU1 M-2 — surface-less at the sweep funnel).
    result["leaks"] = sweep_leak_scan(_normalize_for_scan(xml))

    # 2. Voice-tell scan over paragraph-structured text. context="brief" leaves
    #    the bullets-in-email structural rule off (documents legitimately use
    #    lists), but tri-colon / em-dash / hedging-stack + every exact banned
    #    phrase still run. Lazy import + tolerance: if the detector isn't
    #    installed (partial update), the leak scan still stands.
    try:
        from voice_tell_detector import scan_text  # type: ignore

        result["voice"] = scan_text(_docx_paragraph_text(xml), context="brief")
    except Exception:
        result["voice"] = {"verdict": "pass", "findings": []}

    voice_findings = result["voice"].get("findings", [])
    has_voice_fail = any(f.get("severity") == "fail" for f in voice_findings)
    result["has_voice_warn"] = any(f.get("severity") == "warn" for f in voice_findings)
    result["has_violation"] = bool(result["leaks"]) or has_voice_fail
    return result


def scan_html_for_violations(html_path: str | Path) -> dict:
    """SPEC FU1 D2 — the html sibling of `scan_docx_for_violations`. Given an
    `.html`/`.htm` PATH, return every voice tell + privacy/substrate/personal
    leak in it, in one call, regardless of how the file was produced.

    The premium-HTML save-time gate (`premium_html.make_premium_brief`) already
    runs the full stack, but GATE2's whole premise is that the chokepoint is
    bypassable — the LLM can hand-roll or later-edit an `.html` deliverable in
    any format. This reads the produced file, so a hand-rolled page is caught
    exactly like a gated one.

    Returns the SAME result dict as `scan_docx_for_violations` (path / leaks /
    voice / has_violation / has_voice_warn / optional error). NEVER raises: an
    empty, unreadable, or oversize (> `_MAX_HTML_BYTES`) file comes back
    `error`-flagged so the sweep FLAGS it ("couldn't verify this file") rather
    than passing it — the Bug #54 loud-not-false-clean posture.

    Leak scan runs over `_html_visible_text` (reader text + every href/src
    target); voice scan over `_html_paragraph_text` (block-structured) with
    context="brief". No `surface` param — the sweep never knows the audience,
    and PGUARD1's rule is never to default an unknown surface to org.
    """
    html_path = Path(html_path)
    result: dict = {
        "path": str(html_path),
        "leaks": [],
        "voice": {"verdict": "pass", "findings": []},
        "has_violation": False,
        "has_voice_warn": False,
    }
    try:
        if not html_path.exists():
            result["error"] = f"file not found: {html_path}"
            return result
        if html_path.stat().st_size > _MAX_HTML_BYTES:
            result["error"] = f"file too large to scan: {html_path.name}"
            return result
        raw = html_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # permission error, decode failure, etc.
        result["error"] = f"unreadable .html ({type(e).__name__}): {html_path.name}"
        return result
    if not raw.strip():
        result["error"] = f"{html_path.name} is empty — unable to verify it is clean"
        return result

    # 1. Forbidden-token + personal-fingerprint (flag-only) leak scan over the
    #    reader-visible text + every link/image target (SPEC FU1 M-2).
    result["leaks"] = sweep_leak_scan(_html_visible_text(raw))

    # 2. Voice-tell scan over block-structured paragraphs. context="brief" leaves
    #    the bullets-in-email rule off (documents legitimately use lists); the
    #    structural tri-colon / em-dash / hedging tells + banned phrases still
    #    run. Lazy import + tolerance: if the detector isn't installed (partial
    #    update), the leak scan still stands.
    try:
        from voice_tell_detector import scan_text  # type: ignore

        result["voice"] = scan_text(_html_paragraph_text(raw), context="brief")
    except Exception:
        result["voice"] = {"verdict": "pass", "findings": []}

    voice_findings = result["voice"].get("findings", [])
    has_voice_fail = any(f.get("severity") == "fail" for f in voice_findings)
    result["has_voice_warn"] = any(f.get("severity") == "warn" for f in voice_findings)
    result["has_violation"] = bool(result["leaks"]) or has_voice_fail
    return result


__all__ = [
    "scan_docx_for_leaks",
    "collect_docx_leaks",
    "scan_text_for_leaks",
    "scan_html_for_leaks",
    "collect_html_leaks",
    "sweep_leak_scan",
    "scan_docx_for_violations",
    "scan_html_for_violations",
    "LeakScanError",
]


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: docx_leak_scanner.py <path-to-docx>", file=sys.stderr)
        raise SystemExit(2)
    findings = collect_docx_leaks(sys.argv[1])
    if not findings:
        print("OK: no forbidden tokens detected")
        raise SystemExit(0)
    print(f"FAIL: {len(findings)} forbidden tokens found")
    for f in findings:
        print(f"  [{f['name']}] {f['match']!r}")
    raise SystemExit(1)
