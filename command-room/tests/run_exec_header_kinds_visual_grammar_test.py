#!/usr/bin/env python3
"""FS-13 (per-kind exec-header eyebrow) + FS-12 (zero-table data-heavy flag).

FS-13: the CHANGED / DECIDE / NEEDED eyebrow is a brief-family scaffold. Memo /
one-pager / decision-memo / board-pack / analysis kinds render the VERDICT lead
only — never the three eyebrow lines (they bled onto 4/4 live docs and cost the
one-pager its page).

FS-12: memo-writer + one-pager rendered data-bearing content as bullet walls.
`visual_gate.flag_zero_table_data_heavy` flags a document kind that carries
data-bearing sections but zero table/matrix/tile structure.

House convention: non-zero exit = fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import brief_writer as bw  # noqa: E402
from visual_gate import flag_zero_table_data_heavy  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


class _FakeRun:
    def __init__(self):
        self.text = ""


class _FakePara:
    def __init__(self):
        self.runs = []
        from types import SimpleNamespace
        self.paragraph_format = SimpleNamespace(
            space_before=None, space_after=None, line_spacing=None)

    def add_run(self, text=""):
        r = _FakeRun()
        r.text = text
        self.runs.append(r)
        return r


class _FakeDoc:
    """Captures paragraph text so we can assert what _add_exec_header emitted,
    without a real python-docx dependency."""
    def __init__(self):
        self.paras: list[_FakePara] = []

    def add_paragraph(self, *a, **k):
        p = _FakePara()
        self.paras.append(p)
        return p

    def text_blob(self) -> str:
        return "\n".join(r.text for p in self.paras for r in p.runs)


def _exec_header_text(kind: str) -> str:
    """Render an exec header for `kind` via the real _add_exec_header, using a
    fake doc + monkeypatched rule/run helpers so no python-docx is needed."""
    doc = _FakeDoc()
    orig_rule = bw._add_header_rule
    orig_set = bw._set_run
    bw._add_header_rule = lambda d: None
    bw._set_run = lambda *a, **k: None
    try:
        bw._add_exec_header(doc, {
            "verdict": "Ship the playbook first.",
            "changed": "Acme moved to paperwork.",
            "decide": "Whether to gate the hire.",
            "needs": "Approve the redline.",
        }, brief_kind=kind)
    finally:
        bw._add_header_rule = orig_rule
        bw._set_run = orig_set
    return doc.text_blob()


def main() -> int:
    # ---- FS-13: eyebrow present for brief-family, absent for doc kinds -----
    brief_txt = _exec_header_text("weekly_recap")
    check("brief-family renders the verdict", "Ship the playbook first." in brief_txt)
    check("brief-family renders the CHANGED eyebrow", "CHANGED" in brief_txt)
    check("brief-family renders DECIDE + NEEDED",
          "DECIDE" in brief_txt and "NEEDED" in brief_txt)

    for kind in ("memo", "one_pager", "decision_memo", "board_pack",
                 "contract_review", "automation_scan", "stress_test"):
        txt = _exec_header_text(kind)
        check(f"{kind}: verdict lead still renders",
              "Ship the playbook first." in txt)
        check(f"{kind}: NO CHANGED/DECIDE/NEEDED eyebrow (FS-13)",
              "CHANGED" not in txt and "DECIDE" not in txt and "NEEDED" not in txt,
              txt.replace("\n", " ")[:80])

    check("EXEC_EYEBROW_EXCLUDED_KINDS covers memo + one_pager",
          {"memo", "one_pager"} <= bw.EXEC_EYEBROW_EXCLUDED_KINDS)
    check("brief-family kinds NOT excluded",
          "weekly_recap" not in bw.EXEC_EYEBROW_EXCLUDED_KINDS)

    # ---- FS-12: zero-table data-heavy flag --------------------------------
    heavy = [{"heading": "Pricing",
              "bullets": ["Plan A $5k/mo", "Plan B $8k/mo", "Plan C $12k/mo"]}]
    check("data-heavy one-pager with no table is FLAGGED",
          flag_zero_table_data_heavy(heavy, "one_pager") != "")
    check("flag names the section", "Pricing" in flag_zero_table_data_heavy(heavy, "memo"))
    structured = [{"heading": "Pricing",
                   "table": {"headers": ["Plan", "Price"],
                             "rows": [["A", "$5k"], ["B", "$8k"]]}}]
    check("structured data → no flag",
          flag_zero_table_data_heavy(structured, "one_pager") == "")
    check("prose section (no data) → no flag",
          flag_zero_table_data_heavy(
              [{"heading": "Intro", "body": "A short narrative with no numbers."}],
              "memo") == "")
    check("non-document kind → no flag",
          flag_zero_table_data_heavy(heavy, "call_prep") == "")
    tiled = [{"heading": "Metrics", "tiles": [{"label": "MRR", "value": "$5k"}]}]
    check("tiles count as structure → no flag",
          flag_zero_table_data_heavy(tiled + heavy, "one_pager") == "")

    if failures:
        print(f"\nexec-header kinds + visual grammar FAIL — "
              f"{len(failures)} of {checks} failed")
        return 1
    print(f"exec-header per-kind + visual grammar (FS-12/FS-13): {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
