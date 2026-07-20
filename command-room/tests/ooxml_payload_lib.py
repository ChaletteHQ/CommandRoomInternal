#!/usr/bin/env python3
"""Shared OOXML payload-equality helper for the test battery.

`zip_payload_identical` was mirrored verbatim in run_charts_test.py (OUT3) and
run_deck_writer_test.py (OUT6) while the out7-kpi-scorecard branch was still in
flight — each suite carried its own copy with a note to consolidate once OUT7
merged (it merged 2026-07-19 @ c122137). This is that consolidation: one copy,
both suites import it. Kept out of the `run_*`/`test_*` discovery namespace
(same as output_exercise_lib.py) so run_all.py never tries to execute it.

See the OOXML zip-timestamp gotcha: never `read_bytes() ==` two docx/pptx in a
test — zip entry headers carry 2s-resolution DOS timestamps, so two renders that
straddle a 2s boundary differ in container metadata while every XML part is
identical. The acceptance guarantee is about the PAYLOAD, never the mtimes.
"""
from __future__ import annotations

import zipfile


def zip_payload_identical(a, b) -> bool:
    """Entry-content equality for two OOXML archives (docx/pptx ARE zips).

    A raw `read_bytes()` compare intermittently fails: zip entry headers carry
    DOS timestamps with 2-SECOND resolution, so two renders that straddle a
    2s boundary differ in archive metadata while every XML part is identical
    (the intermittent battery failure this replaces — reproducible with a
    time.sleep(2.1) between the two renders). The acceptance guarantee is
    about the PAYLOAD — same parts, same bytes per part — never the zip
    container's mtimes."""
    with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
        na, nb = za.namelist(), zb.namelist()
        if na != nb:
            return False
        return all(za.read(n) == zb.read(n) for n in na)
