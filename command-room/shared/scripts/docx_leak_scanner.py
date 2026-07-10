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
    """
    paras: List[str] = []
    for chunk in _PARA_SPLIT_RE.split(xml):
        collapsed = _RUN_BOUNDARY_RE.sub("", chunk)
        line = _XML_TAG_RE.sub(" ", collapsed)
        line = _decode_entities(line)
        line = _WHITESPACE_RE.sub(" ", line).strip()
        if line:
            paras.append(line)
    return "\n\n".join(paras)


def scan_text_for_leaks(text: str) -> List[dict]:
    """Run the canonical forbidden-token patterns over an arbitrary text blob
    (a chat-rendered memo/email body, not a .docx). Same patterns the .docx
    scanner uses, so a `Phase 3` / `project_020` / `leverage` leak is caught in
    chat prose exactly as it is in a document. Never raises — returns findings.

    (SPEC GATE2 D4 — the chat-prose path. The memo that freelanced as chat text
    in the live test carried a `Phase N` leak; this catches it.)"""
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
    return findings


def scan_docx_for_leaks(docx_path: str | Path) -> List[dict]:
    """Scan `docx_path` for any forbidden tokens. Returns the list of
    findings (empty if clean). Raises LeakScanError if findings are
    non-empty.

    The exception path is the default — every .docx writer expects this
    function to either return [] (silent success) or raise (loud failure).

    Pass return_findings=False if you want to collect findings without
    raising — useful for audit passes that report rather than block.
    """
    return _scan_docx(docx_path, raise_on_findings=True)


def collect_docx_leaks(docx_path: str | Path) -> List[dict]:
    """Same as scan_docx_for_leaks but never raises — returns the findings
    list for callers that want to audit/report rather than block. Useful for
    audit tools, weekly-audit, and pre-ship gates."""
    return _scan_docx(docx_path, raise_on_findings=False)


def _scan_docx(docx_path: str | Path, raise_on_findings: bool) -> List[dict]:
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

    text = _normalize_for_scan(xml)
    findings: list[dict] = []
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

    # 1. Forbidden-token (leak) scan over the collapsed full text.
    result["leaks"] = scan_text_for_leaks(_normalize_for_scan(xml))

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


__all__ = [
    "scan_docx_for_leaks",
    "collect_docx_leaks",
    "scan_text_for_leaks",
    "scan_docx_for_violations",
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
