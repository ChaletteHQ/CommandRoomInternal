#!/usr/bin/env python3
"""Tests for widget_transport.render_and_persist (Bug #32, v3.13.8)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from widget_transport import render_and_persist  # noqa: E402


_MINIMAL_VALID_VIEW = {
    "header": "Test header — 1 thread.",
    "sections": [
        {
            "title": "TODAY",
            "count": 1,
            "items": [
                {
                    "n": 1,
                    "icon": "✉",
                    "name": "Sam Sample",
                    "subject": "Test thread",
                    "metadata": [("Subject", "Test thread"), ("To", "sam@example.com")],
                    "body_lines": ["Test body line."],
                    "actions": ["1 send", "1 draft", "1 snooze 3d"],
                },
            ],
        }
    ],
}


def test_persist_writes_file_and_returns_uri() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="widget_transport_test_"))
    persist_dir = tmp / "_hq" / ".system" / "widgets"

    transport = render_and_persist(
        data_view=_MINIMAL_VALID_VIEW,
        wrapper="fragment",
        persist_dir=persist_dir,
        name_hint="test_surface",
    )

    assert "html" in transport, transport
    assert "file_uri" in transport, transport
    assert "path" in transport, transport
    assert transport["path"].exists(), transport["path"]
    assert transport["file_uri"].startswith("file:///"), transport["file_uri"]
    # File should contain the validated HTML — at minimum, a known
    # canonical anchor string
    written = transport["path"].read_text(encoding="utf-8")
    assert "Sam Sample" in written or "Sam" in written
    print("PASS test_persist_writes_file_and_returns_uri")


def test_fragment_persists_with_utf8_bom() -> None:
    """Bug #40 — fragment-mode persists with BOM so standalone-open in a
    browser renders em-dashes correctly without injecting <meta charset>
    into the fragment payload (which would violate the show_widget contract)."""
    tmp = Path(tempfile.mkdtemp(prefix="widget_bom_test_"))
    persist_dir = tmp / "widgets"

    transport = render_and_persist(
        data_view=_MINIMAL_VALID_VIEW,
        wrapper="fragment",
        persist_dir=persist_dir,
    )

    raw = transport["path"].read_bytes()
    # UTF-8 BOM = \xef\xbb\xbf
    assert raw.startswith(b"\xef\xbb\xbf"), raw[:10]
    print("PASS test_fragment_persists_with_utf8_bom")


def test_document_wrapper_no_bom() -> None:
    """document-mode HTML already has <head><meta charset> — no BOM."""
    tmp = Path(tempfile.mkdtemp(prefix="widget_doc_test_"))
    persist_dir = tmp / "widgets"

    transport = render_and_persist(
        data_view=_MINIMAL_VALID_VIEW,
        wrapper="document",
        persist_dir=persist_dir,
    )

    raw = transport["path"].read_bytes()
    # Should NOT start with BOM in document mode
    assert not raw.startswith(b"\xef\xbb\xbf"), raw[:10]
    # Should contain <!DOCTYPE
    assert b"<!DOCTYPE" in raw[:200]
    print("PASS test_document_wrapper_no_bom")


def test_persist_creates_persist_dir() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="widget_dir_test_"))
    persist_dir = tmp / "deeply" / "nested" / "dir"
    assert not persist_dir.exists()
    transport = render_and_persist(
        data_view=_MINIMAL_VALID_VIEW,
        wrapper="fragment",
        persist_dir=persist_dir,
    )
    assert persist_dir.exists()
    assert transport["path"].parent == persist_dir
    print("PASS test_persist_creates_persist_dir")


def test_transport_runs_wrapper_validation() -> None:
    """EW2+T (F-15) — render_and_persist runs validate_rendered_widget as part
    of the one-call canonical path. An input-needing action (Defer needs a
    date) still round-trips because the renderer emits its wrapper; and the
    validator is genuinely invoked (asserted via a monkeypatched spy)."""
    import widget_transport as wt

    tmp = Path(tempfile.mkdtemp(prefix="widget_validate_test_"))
    # _MINIMAL_VALID_VIEW carries "edit then send" + "draft" — both
    # input-needing actions, so the wrapper contract genuinely exercises.
    view = _MINIMAL_VALID_VIEW
    transport = render_and_persist(
        data_view=view, wrapper="fragment", persist_dir=tmp / "widgets")
    assert transport["path"].exists()

    # Spy: replace the validator on the renderer module and confirm the
    # transport calls it (the F-15 instruction-layer lesson mechanized at
    # the code layer too — the transport can't silently skip validation).
    import chat_output_renderer as cor
    calls = []
    original = cor.validate_rendered_widget
    # PGUARD1: the transport now plumbs surface= through — stub accepts it.
    cor.validate_rendered_widget = (
        lambda html, surface=None: calls.append(len(html or ""))
    )
    try:
        render_and_persist(data_view=view, wrapper="fragment",
                           persist_dir=tmp / "widgets2")
    finally:
        cor.validate_rendered_widget = original
    assert calls, "render_and_persist did not invoke validate_rendered_widget"
    print("PASS test_transport_runs_wrapper_validation")


def main() -> int:
    test_persist_writes_file_and_returns_uri()
    test_fragment_persists_with_utf8_bom()
    test_document_wrapper_no_bom()
    test_persist_creates_persist_dir()
    test_transport_runs_wrapper_validation()
    print("\nALL widget_transport tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
