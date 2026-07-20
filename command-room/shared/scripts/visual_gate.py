#!/usr/bin/env python3
"""
Render-then-critique visual gate (SPEC OUT2 §3) — best-effort docx → PNG previews.

WHY THIS EXISTS
---------------
PlotGen (WWW 2025) ablation: visual critique of the RENDERED page catches the
error class code review structurally cannot — empty tiles, broken tables,
orphaned headings, cramped spacing. `make_brief` validates STRUCTURE before the
file exists; nothing before this module ever looked at the PAGE after it did.

THE CONTRACT (read before wiring a skill to this)
-------------------------------------------------
- `render_preview(docx_path) -> list[png_path] | None` is a best-effort ladder:
  Word COM (Windows, pywin32 present) → `soffice --headless` (if on
  PATH) → `None`. The produced PDF is rasterized via PyMuPDF (`fitz`),
  `pdftoppm`, or — on Windows, with zero installs — the OS-native
  `Windows.Data.Pdf` API driven through `powershell.exe`.
- **NEVER raises into the calling skill.** Any failure at any rung returns
  `None`. `None` means: renderer unavailable, gate skipped — the skill notes it
  in the `visual_gate` audit event and proceeds. Behavior with no renderer is
  byte-identical to pre-OUT2 (Cowork sandboxes may lack BOTH renderers — the
  gate upgrades machines that CAN render; it never degrades ones that can't).
- Pages 1–2 only, dpi clamped to 100–150, output to a fresh SESSION TEMP dir
  (`tempfile.mkdtemp`) — NEVER the workspace. Previews are ephemeral critique
  input, not deliverables; nothing here writes under the Drive root.
- WARN-ONLY FOREVER at the code layer. The gate is judgment (the skill LOOKS at
  the images against CHECKLIST), not schema — there is no blocking mode and
  none is planned. The `visual_gate` audit event is what usage-report /
  insight-generator mine to prove the gate fires.
- Kill switch: `CR_VISUAL_GATE=off` (or `0` / `skip`) forces the skipped path —
  CI and tests use this for determinism.

Prose contract: `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass".
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Sequence

# Hard caps per SPEC OUT2 §3 — pages 1–2 only, 100–150 dpi.
MAX_PAGES = 2
DEFAULT_DPI = 120
_DPI_MIN, _DPI_MAX = 100, 150

_SUBPROCESS_TIMEOUT_S = 120

# The fixed 7-item checklist the calling skill walks against the rendered
# pages (EXECUTIVE_OUTPUT_STANDARD § "The visual pass"). Kept here as the one
# machine-readable copy so prose and tests reference the same list. Extend,
# don't reorder (the pins are positional history): item 7 landed with SPEC
# OUT3 (charts).
CHECKLIST = (
    "orphaned heading at a page break",
    "empty or placeholder tile",
    "table overflow / wrap damage",
    "cramped spacing",
    "header/footer intact",
    "brand palette applied",
    "chart unreadable / overplotted",
)


# ---------------------------------------------------------------------------
# FS-12 — zero-table / data-heavy structural flag. memo-writer + one-pager
# rendered data-bearing content as bullet walls in premium typography while
# decision-memo-composer (same session family) structured its data into tables
# + a star matrix. This is a WARN-ONLY structural pre-check the composers run
# on their `sections` payload BEFORE the visual pass: if a document-shaped kind
# carries data-bearing sections but ZERO table/matrix/tile structure, surface a
# one-line nudge to restructure. Never blocks a save (warn-only, like the whole
# gate).
# ---------------------------------------------------------------------------

# Document/decision kinds where tabular/comparative data should be structured,
# not bulleted. (decision_memo already hard-validates its matrix; included for
# completeness so the flag is consistent across the family.)
_DATA_STRUCTURE_KINDS = frozenset({
    "memo", "one_pager", "decision_memo", "board_pack",
})

_NUM_TOKEN_RE = re.compile(r"(?<![\w])(?:\$\s?\d|\d+\s?%|\d[\d,]*\.?\d*\s?[kKmMbB]?\b)")


def _section_has_structure(sec: dict) -> bool:
    if not isinstance(sec, dict):
        return False
    if isinstance(sec.get("table"), dict) and (sec["table"].get("rows")):
        return True
    if isinstance(sec.get("matrix"), dict) and (sec["matrix"].get("rows")):
        return True
    if sec.get("tiles"):
        return True
    if sec.get("timeline"):
        return True
    return False


def _section_is_data_heavy(sec: dict) -> bool:
    """Heuristic: a section carries comparative/tabular data when it has ≥3
    bullets that each contain a number, OR its body text carries ≥3 numeric /
    currency / percentage tokens. Bullet walls of metrics are the FS-12 shape."""
    if not isinstance(sec, dict):
        return False
    bullets = sec.get("bullets") or []
    numbered_bullets = sum(
        1 for b in bullets
        if isinstance(b, str) and _NUM_TOKEN_RE.search(b)
    )
    if numbered_bullets >= 3:
        return True
    body = sec.get("body") or ""
    if isinstance(body, list):
        body = " ".join(str(x) for x in body)
    if isinstance(body, str) and len(_NUM_TOKEN_RE.findall(body)) >= 3:
        return True
    return False


def flag_zero_table_data_heavy(sections: Sequence[dict], brief_kind: str) -> str:
    """Return a one-line WARN string when a document-shaped kind has
    data-bearing sections but no table/matrix/tile structure anywhere; else "".
    """
    if brief_kind not in _DATA_STRUCTURE_KINDS:
        return ""
    secs = list(sections or [])
    if any(_section_has_structure(s) for s in secs):
        return ""
    heavy = [s for s in secs if _section_is_data_heavy(s)]
    if not heavy:
        return ""
    names = ", ".join(
        (s.get("heading") or "(untitled)") for s in heavy[:3]
    )
    return (
        f"visual-grammar: {len(heavy)} data-bearing section(s) [{names}] are "
        f"rendered as bullets with zero table/tile structure. Restructure the "
        f"comparative data into a `table`/`matrix`/`tiles` primitive (see "
        f"decision-memo-composer) before shipping (FS-12)."
    )


# ---------------------------------------------------------------------------
# Rung 1 — Word COM (Windows dev machines). Best-effort: absent pywin32 or
# absent Word → None, fall through. Never raises.
# ---------------------------------------------------------------------------

def _docx_to_pdf_word_com(docx_path: str, out_dir: str) -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import win32com.client  # type: ignore  # pywin32 — absent on most sandboxes
    except Exception:
        return None
    word = None
    try:
        pdf_path = str(Path(out_dir) / (Path(docx_path).stem + ".pdf"))
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(
            str(Path(docx_path).resolve()), ReadOnly=True, AddToRecentFiles=False
        )
        try:
            # 17 = wdExportFormatPDF
            doc.ExportAsFixedFormat(OutputFileName=pdf_path, ExportFormat=17)
        finally:
            doc.Close(False)
        return pdf_path if os.path.isfile(pdf_path) else None
    except Exception:
        return None
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Rung 2 — LibreOffice headless, if on PATH.
# ---------------------------------------------------------------------------

def _docx_to_pdf_soffice(docx_path: str, out_dir: str) -> Optional[str]:
    soffice = shutil.which("soffice") or shutil.which("soffice.exe")
    if not soffice:
        return None
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf",
             "--outdir", str(out_dir), str(docx_path)],
            check=True, capture_output=True, timeout=_SUBPROCESS_TIMEOUT_S,
        )
        pdf = Path(out_dir) / (Path(docx_path).stem + ".pdf")
        return str(pdf) if pdf.is_file() else None
    except Exception:
        return None


# Module-level so tests can monkeypatch the ladder empty (the CI-shaped
# "neither renderer exists" case) without depending on the host machine.
_DOCX_TO_PDF_LADDER: Sequence[Callable[[str, str], Optional[str]]] = (
    _docx_to_pdf_word_com,
    _docx_to_pdf_soffice,
)


# ---------------------------------------------------------------------------
# SPEC OUT6 — the pptx→PDF rung for the board-deck companion. Same posture as
# the docx ladder: best-effort, never raises, None = skipped (the skill logs a
# skipped_reason audit note and proceeds). PowerPoint COM where present →
# soffice (the soffice rung is format-agnostic — same CLI converts .pptx).
# ---------------------------------------------------------------------------

def _pptx_to_pdf_powerpoint_com(pptx_path: str, out_dir: str) -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import win32com.client  # type: ignore  # pywin32 — absent on most sandboxes
    except Exception:
        return None
    app = None
    try:
        pdf_path = str(Path(out_dir) / (Path(pptx_path).stem + ".pdf"))
        app = win32com.client.DispatchEx("PowerPoint.Application")
        # NB: PowerPoint refuses Visible=False; WithWindow=False on Open is
        # the supported headless-ish path.
        pres = app.Presentations.Open(
            str(Path(pptx_path).resolve()), ReadOnly=True, Untitled=False,
            WithWindow=False,
        )
        try:
            pres.SaveAs(pdf_path, 32)  # 32 = ppSaveAsPDF
        finally:
            pres.Close()
        return pdf_path if os.path.isfile(pdf_path) else None
    except Exception:
        return None
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass


_PPTX_TO_PDF_LADDER: Sequence[Callable[[str, str], Optional[str]]] = (
    _pptx_to_pdf_powerpoint_com,
    _docx_to_pdf_soffice,  # format-agnostic: soffice converts .pptx identically
)


# ---------------------------------------------------------------------------
# SPEC OUT5 §3d — the .html rung for the premium brief. Same posture as every
# other rung: best-effort, never raises, None = skipped (the skill logs a
# skipped_reason audit note and proceeds). A headless Chromium-family browser
# screenshots the file directly — no PDF intermediate. Edge headless ships on
# stock Windows (the common client box), so most deployments get this with
# zero installs; Chrome / Chromium cover the rest.
# ---------------------------------------------------------------------------

# Known install locations checked after PATH (Edge/Chrome don't register on
# PATH by default on Windows).
_BROWSER_CANDIDATES = (
    "msedge", "msedge.exe", "chrome", "chrome.exe",
    "google-chrome", "chromium", "chromium-browser",
)
_BROWSER_KNOWN_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def _find_headless_browser() -> Optional[str]:
    for name in _BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    for p in _BROWSER_KNOWN_PATHS:
        if os.path.isfile(p):
            return p
    return None


def _html_to_pngs_headless_browser(
    html_path: str, out_dir: str, dpi: int, max_pages: int
) -> Optional[List[str]]:
    """Screenshot a saved .html deliverable via a headless browser. One tall
    capture (an HTML brief has no page breaks — the top viewport is the
    critique surface, mirroring the docx pages-1–2 cap). Never raises."""
    browser = _find_headless_browser()
    if not browser:
        return None
    profile_dir = None
    try:
        import time
        out_png = Path(out_dir) / "page1.png"
        # Fresh throwaway profile: without it a running desktop Edge/Chrome
        # instance can swallow the headless launch into the existing process.
        profile_dir = tempfile.mkdtemp(prefix="cr_visual_gate_profile_")
        # 940px matches the premium template's page max-width; the height cap
        # approximates the docx ladder's two-page critique window.
        subprocess.run(
            [
                browser, "--headless", "--disable-gpu", "--hide-scrollbars",
                "--no-first-run", f"--user-data-dir={profile_dir}",
                f"--screenshot={out_png}", "--window-size=940,2200",
                Path(html_path).resolve().as_uri(),
            ],
            check=True, capture_output=True, timeout=_SUBPROCESS_TIMEOUT_S,
        )
        # The Windows msedge.exe launcher exits before the real browser
        # process flushes the capture — poll briefly rather than trusting the
        # returncode's timing (observed live: rc 0, file lands ~1s later).
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if out_png.is_file() and out_png.stat().st_size > 0:
                return [str(out_png)]
            time.sleep(0.25)
        return None
    except Exception:
        return None
    finally:
        # Best-effort: the throwaway profile is pure waste once the capture
        # lands (or fails) — without this every visual-gate fire leaks a
        # Chromium profile dir in temp (the G15 review's Windows temp-leak
        # class). ignore_errors: the browser can hold handles briefly on
        # Windows; a straggler dir is acceptable, unbounded accumulation is not.
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# PDF → PNG rasterization ladder:
#   PyMuPDF (fitz) → pdftoppm (poppler) → Windows.Data.Pdf via PowerShell → None.
# The WinRT rung is the load-bearing one on real deployments: stock Windows has
# neither fitz nor poppler, but Windows.Data.Pdf ships with the OS — so a
# machine with Word (the common client box) rasterizes with zero installs.
# ---------------------------------------------------------------------------

# WinRT rasterizer, executed by powershell.exe (always present on Windows).
# {pdf}/{out_dir} are injected as PS single-quoted strings (quotes doubled).
_WINRT_PDF_PS = """
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Data.Pdf.PdfDocument,Windows.Data.Pdf,ContentType=WindowsRuntime]
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
function Await($t,$rt){{ $m=([System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object{{$_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'}})[0].MakeGenericMethod($rt); $x=$m.Invoke($null,@($t)); $x.Wait(); $x.Result }}
function AwaitAction($t){{ $m=([System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object{{$_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'}})[0]; $x=$m.Invoke($null,@($t)); $x.Wait() }}
$f = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{pdf}')) ([Windows.Storage.StorageFile])
$doc = Await ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($f)) ([Windows.Data.Pdf.PdfDocument])
for ($i=0; $i -lt [Math]::Min({max_pages}, $doc.PageCount); $i++) {{
  $pg=$doc.GetPage($i)
  $st=New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
  $op=New-Object Windows.Data.Pdf.PdfPageRenderOptions
  $op.DestinationWidth={width}
  AwaitAction ($pg.RenderToStreamAsync($st,$op))
  $ns=[System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($st.GetInputStreamAt(0))
  $fs=[System.IO.File]::Create((Join-Path '{out_dir}' ('page'+($i+1)+'.png')))
  $ns.CopyTo($fs); $fs.Close(); $st.Dispose(); $pg.Dispose()
}}
"""


def _pdf_to_pngs_winrt_ps(pdf_path: str, out_dir: str, dpi: int, max_pages: int) -> Optional[List[str]]:
    if os.name != "nt":
        return None
    try:
        def _ps_quote(s: str) -> str:
            return str(s).replace("'", "''")
        script = _WINRT_PDF_PS.format(
            pdf=_ps_quote(pdf_path), out_dir=_ps_quote(out_dir),
            max_pages=int(max_pages), width=int(8.5 * dpi),
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True, capture_output=True, timeout=_SUBPROCESS_TIMEOUT_S,
        )
        pngs = sorted(str(p) for p in Path(out_dir).glob("page*.png"))
        return pngs or None
    except Exception:
        return None


def _pdf_to_pngs(pdf_path: str, out_dir: str, dpi: int, max_pages: int) -> Optional[List[str]]:
    try:
        import fitz  # type: ignore  # PyMuPDF — optional
        pngs: List[str] = []
        with fitz.open(pdf_path) as pdf:
            for i, page in enumerate(pdf):
                if i >= max_pages:
                    break
                pix = page.get_pixmap(dpi=dpi)
                p = str(Path(out_dir) / f"page{i + 1}.png")
                pix.save(p)
                pngs.append(p)
        if pngs:
            return pngs
    except Exception:
        pass
    tool = shutil.which("pdftoppm")
    if tool:
        try:
            prefix = str(Path(out_dir) / "page")
            subprocess.run(
                [tool, "-png", "-r", str(dpi), "-f", "1", "-l", str(max_pages),
                 str(pdf_path), prefix],
                check=True, capture_output=True, timeout=_SUBPROCESS_TIMEOUT_S,
            )
            pngs = sorted(str(p) for p in Path(out_dir).glob("page*.png"))
            if pngs:
                return pngs
        except Exception:
            pass
    return _pdf_to_pngs_winrt_ps(pdf_path, out_dir, dpi, max_pages)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_preview(
    docx_path: str,
    max_pages: int = MAX_PAGES,
    dpi: int = DEFAULT_DPI,
) -> Optional[List[str]]:
    """Render pages 1–2 of `docx_path` to PNGs in a fresh session temp dir.

    Returns the list of PNG paths, or `None` when no renderer is available /
    anything at all goes wrong. NEVER raises into the caller — `None` is the
    universal "gate skipped" answer and MUST leave the calling skill's
    behavior identical to not having called this at all.

    SPEC OUT6: a `.pptx` path takes the pptx→PDF rung (PowerPoint COM →
    soffice) instead of the docx ladder; everything downstream (rasterize,
    caps, temp-dir posture) is identical. Slides preview at the same page
    caps — the deck's first two slides are the verdict + KPI slides, the
    highest-value critique surface.

    SPEC OUT5 §3d: a `.html` path (the premium brief) takes the headless-
    browser rung — Edge headless ships on stock Windows; Chrome/Chromium
    cover the rest — screenshotting the top of the document directly. Absent
    browser = `None` = gate skipped with the standard audit note, exactly
    like an absent docx renderer. Never raises, never blocks.
    """
    try:
        if os.environ.get("CR_VISUAL_GATE", "").strip().lower() in ("off", "0", "skip"):
            return None
        try:
            max_pages = min(int(max_pages), MAX_PAGES)
            dpi = max(_DPI_MIN, min(_DPI_MAX, int(dpi)))
        except Exception:
            max_pages, dpi = MAX_PAGES, DEFAULT_DPI
        if max_pages < 1:
            return None
        if not docx_path or not os.path.isfile(str(docx_path)):
            return None
        # Session temp dir ONLY — never the workspace (previews are ephemeral).
        out_dir = tempfile.mkdtemp(prefix="cr_visual_gate_")
        # SPEC OUT5 §3d: a .html deliverable takes the headless-browser rung —
        # a direct screenshot, no PDF intermediate. Same contract: None =
        # skipped, the skill logs skipped_reason and proceeds (never blocks).
        if str(docx_path).lower().endswith((".html", ".htm")):
            return _html_to_pngs_headless_browser(
                str(docx_path), out_dir, dpi, max_pages
            )
        pdf = None
        is_pptx = str(docx_path).lower().endswith(".pptx")
        ladder = _PPTX_TO_PDF_LADDER if is_pptx else _DOCX_TO_PDF_LADDER
        for rung in ladder:
            pdf = rung(str(docx_path), out_dir)
            if pdf:
                break
        if not pdf:
            return None
        return _pdf_to_pngs(pdf, out_dir, dpi, max_pages)
    except Exception:
        return None


def log_visual_gate(
    workspace_root: str,
    doc: str,
    rendered: bool,
    findings: Optional[Sequence[str]] = None,
    fixed: bool = False,
    skipped_reason: Optional[str] = None,
    source_skill: str = "visual_gate",
) -> bool:
    """Append the `visual_gate` audit event {doc, rendered, findings, fixed}.

    Best-effort, NEVER raises (mirrors brief_writer._emit_brief_meta_audit —
    an audit write must never block a deliverable). When the ladder returned
    `None`, pass rendered=False + a short `skipped_reason` ("no renderer on
    this machine") so the skipped path is detectable, not invisible.
    Returns True when the event landed, False otherwise.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from next_seq import next_seq
        from atomic_write import atomic_append_jsonl
        from cru_match import _now_iso
        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        data = {
            "doc": str(doc),
            "rendered": bool(rendered),
            "findings": [str(f) for f in (findings or [])],
            "fixed": bool(fixed),
        }
        if skipped_reason:
            data["skipped_reason"] = str(skipped_reason)
        atomic_append_jsonl(events_path, [{
            "seq": next_seq(str(events_path)),
            "ts": _now_iso(),
            "type": "visual_gate",
            "source_skill": source_skill,
            "data": data,
        }])
        return True
    except Exception:
        return False


__all__ = ["render_preview", "log_visual_gate", "CHECKLIST", "MAX_PAGES", "DEFAULT_DPI"]
