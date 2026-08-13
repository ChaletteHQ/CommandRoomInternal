#!/usr/bin/env python3
"""connector_id_patterns — connector-minted opaque ids, ONE leak family
(BUG-8330 item 11).

Every id pattern in both leak scanners anchored on PLUGIN-MINTED prefixes
(person_123, cmt_<ulid>, bp_<hex>…). Connector-minted opaque ids had zero
coverage: the reported live case was `p1786032197391009` — a normalized
Slack permalink id — rendered in a user-facing surface. The withheld
direction was already covered (placeholder rows); the printed direction was
structural: nothing KNEW a connector id when it saw one.

This module is the one source for the family, mirrored into BOTH scanners
the way vocabulary_policy.marketing_patterns() already is (a pattern added
here is always-scanned; never re-declare it in a scanner):

  - docx_leak_scanner._FORBIDDEN_PATTERNS   (deliverable/doc surfaces)
  - chat_output_renderer._LEAK_PATTERNS     (chat + widget surfaces)

Two shapes:
  - the bare Slack permalink id: p + 16 digits;
  - the provider:opaque source_ref shape (`slack:p17…`, `gmail:18c4…`,
    `granola:<uuid>` …) — the internal normalized ref
    (connector_adapters' primary-artifact key), which is provenance, never
    prose. Real URLs (https://…) are untouched.

stdlib only.
"""
from __future__ import annotations

import re

# Providers whose normalized refs exist in the substrate (the
# primary-artifact-key vocabulary + calendar/file sources). The opaque tail
# floor (10+ ref chars) keeps prose like "slack: yes" out of scope.
_PROVIDERS = (
    "slack", "gmail", "superhuman", "outlook", "teams",
    "granola", "gcal", "calendar", "drive", "onedrive", "sharepoint",
)

CONNECTOR_ID_PATTERNS: list[tuple[str, str]] = [
    # The normalized Slack permalink id, bare (the reported leak).
    ("connector_opaque_id", r"\bp\d{16}\b"),
    # provider:opaque — the internal source_ref shape. Never a URL.
    ("connector_source_ref",
     r"\b(?:" + "|".join(_PROVIDERS) + r"):[A-Za-z0-9][A-Za-z0-9_\-./=+]{9,}"),
]


def connector_id_patterns() -> list[tuple[str, str]]:
    """(name, regex-source) rows — docx_leak_scanner's registration shape."""
    return list(CONNECTOR_ID_PATTERNS)


def connector_id_leak_patterns():
    """(compiled, label) rows — chat_output_renderer's registration shape."""
    return [(re.compile(src, re.IGNORECASE), "connector-id leak")
            for _name, src in CONNECTOR_ID_PATTERNS]


__all__ = [
    "CONNECTOR_ID_PATTERNS",
    "connector_id_patterns",
    "connector_id_leak_patterns",
]
