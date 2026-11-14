"""A Word draft as it would print with every tracked change accepted (cr1#1004).

python-docx's `paragraph.text` reads only a paragraph's direct runs. A tracked
insertion wraps its runs in `<w:ins>` (and a move's destination in
`<w:moveTo>`), so that text was skipped: a counterparty's inserted sections did
not exist to a review, which then reported them as missing clauses. Deleted
text is stored as `w:delText` inside `<w:del>` / `<w:moveFrom>` and is left
out here, as it would be once accepted.

On a draft with no tracked changes the text reads as `paragraph.text` reads
it: a run's tab and positional tab are a tab, a line break or carriage return
a newline, a page or column break nothing, a non-breaking hyphen "-". A text
box is not part of its host paragraph's text (python-docx never read it, and
Word writes each box twice, once per `mc:AlternateContent` branch).

Tracked changes are counted by element tag alone. A revision Word anonymized
("remove personal information": author "Author", no date) is a revision like
any other; a check that matched only dated revisions counted none of twelve.
A marker on a paragraph mark (`w:pPr/w:rPr/w:ins`) is part of the revision
whose runs it ends, not a second one, so it is not counted.

Read only. This is the one .docx text path the skills name; `doc_shelf` reads
paragraphs through `paragraph_text` too.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

#: The revision tags counted, by their local name.
TRACKED_TAGS = ("ins", "del", "moveFrom", "moveTo")


def _w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


#: Wrappers whose text is gone once the draft's changes are accepted.
_REMOVED = {_w("del"), _w("moveFrom")}
#: Subtrees that are not the host paragraph's own text.
_NOT_TEXT = {_w("txbxContent"), f"{{{MC_NS}}}AlternateContent", _w("pPr"),
             _w("rPr")}
_P, _TBL, _TR, _TC, _SDT, _SDT_CONTENT = (_w("p"), _w("tbl"), _w("tr"),
                                          _w("tc"), _w("sdt"), _w("sdtContent"))
_MARK_PARENTS = {_w("rPr")}


def _run_char(el) -> str:
    """One run-content element as `paragraph.text` reads it, "" otherwise."""
    tag = el.tag
    if tag == _w("t"):
        return el.text or ""
    if tag in (_w("tab"), _w("ptab")):
        return "\t"
    if tag == _w("cr"):
        return "\n"
    if tag == _w("br"):
        kind = el.get(_w("type"))
        return "\n" if kind in (None, "textWrapping") else ""
    if tag == _w("noBreakHyphen"):
        return "-"
    return ""


def _walk_text(node) -> Iterator[str]:
    """The accepted text under `node`, in document order, skipping deleted
    and moved-out content and anything that is not the paragraph's own text."""
    for child in node.iterchildren():
        tag = child.tag
        if not isinstance(tag, str):  # comments and processing instructions
            continue
        if tag in _REMOVED or tag in _NOT_TEXT:
            continue
        piece = _run_char(child)
        if piece:
            yield piece
        elif tag != _w("t"):
            yield from _walk_text(child)


def paragraph_text(p_element) -> str:
    """One `w:p` element's text with its tracked changes accepted: inserted
    and moved-in runs in, deleted and moved-out runs out."""
    return "".join(_walk_text(p_element))


def _cell_paragraphs(tc) -> Iterator[Any]:
    """A cell's own paragraphs, through content controls, never a nested
    table's (python-docx's `cell.text` reads the cell's own paragraphs)."""
    for child in tc.iterchildren():
        if child.tag == _P:
            yield child
        elif child.tag == _SDT:
            for content in child.iterchildren(_SDT_CONTENT):
                yield from _cell_paragraphs(content)


def table_rows(tbl_element) -> List[List[str]]:
    """A `w:tbl` element's own rows, each a list of its cells' accepted text.
    A nested table is not flattened into the outer one."""
    rows = []
    for tr in tbl_element.iterchildren(_TR):
        cells = []
        for tc in tr.iterchildren(_TC):
            cells.append(" ".join(paragraph_text(p) for p in _cell_paragraphs(tc)).strip())
        rows.append(cells)
    return rows


def body_blocks(body) -> Iterator[Any]:
    """The body's paragraphs and tables in order, through body-level content
    controls (`w:sdt`), which python-docx's `document.paragraphs` skips."""
    for child in body.iterchildren():
        if child.tag in (_P, _TBL):
            yield child
        elif child.tag == _SDT:
            for content in child.iterchildren(_SDT_CONTENT):
                yield from body_blocks(content)


def tracked_counts(element) -> Dict[str, int]:
    """`{ins, del, moveFrom, moveTo}`: the revision elements under `element`,
    counted by tag, whatever their author or date. A paragraph-mark marker
    (one whose parent is `w:rPr`) is part of the revision it ends, not one
    more."""
    out = {}
    for tag in TRACKED_TAGS:
        out[tag] = sum(1 for el in element.iter(_w(tag))
                       if el.getparent() is None or el.getparent().tag not in _MARK_PARENTS)
    return out


def read_docx(path) -> Dict[str, Any]:
    """`{paragraphs, text, tracked_changes, tracked_total, has_tracked_changes}`.

    `paragraphs` is the body in order: each paragraph's accepted text, and a
    table as one entry per row with its cells joined by " | ". `text` is them
    joined by newlines. A paragraph left empty only because its content was
    deleted is dropped, so a deleted paragraph leaves no blank line.
    `tracked_changes` counts the body's revisions by tag."""
    import docx  # python-docx, pinned in command-room/requirements.txt

    body = docx.Document(str(Path(path))).element.body
    paragraphs: List[str] = []
    for block in body_blocks(body):
        if block.tag == _P:
            text = paragraph_text(block)
            gone = tracked_counts(block)
            if not text.strip() and gone["del"] + gone["moveFrom"]:
                continue
            paragraphs.append(text)
        else:
            for row in table_rows(block):
                paragraphs.append(" | ".join(row))
    counts = tracked_counts(body)
    total = sum(counts.values())
    return {"paragraphs": paragraphs, "text": "\n".join(paragraphs),
            "tracked_changes": counts, "tracked_total": total,
            "has_tracked_changes": total > 0}
