"""
connector_adapters — data-model adapters behind intent (Layer A4/A5).

One module per category: `mail`, `calendar`. `provenance` owns the
`gmail:`/`gcal:`/`slack:`/`granola:` prefix logic + the canonical dedup key in
ONE place (read back-compat, no history rewrite). `capabilities` loads the
capability manifest + does fingerprint re-pair.

Per YAGNI (handoff §8), only mail + calendar are wired now; chat/storage/e-sign/
accounting get their own module when their first consumer skill exists. The
registry SHAPE is category-generic so they slot in as modules, not surgery.
"""
from __future__ import annotations

__all__ = ["mail", "calendar", "provenance", "capabilities"]
