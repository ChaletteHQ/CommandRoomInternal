"""wall_text: the one way text is read before it is matched against the wall
(cr1#1779, ruling 147x).

Every place that asks "does this text name someone off limits or hidden"
reads both sides through here, so a spelling one check holds is held by all
of them, and by the Hub page, whose `normOf` is this same fold.

- `strip(v)`: NFKD, then every mark (Unicode category M: Mn, Mc and Me) and
  every format character that prints nothing (Cf: a zero-width space or
  joiner, a soft hyphen, a direction mark) taken out. Case and spacing are
  kept. `keep_format=True` keeps the Cf characters (import_wall's older
  read keeps them, so two words either side of one are not joined).
- `fold(v)`: `strip`, every run of whitespace one space, trimmed, lower
  case. Exactly the page's `normOf` and what the owner's page hashes are
  made of (`hub_live.digest`).
- `words(v)`: `fold`, with every character that is not a letter, a digit or
  "_" read as a space: a name as words.
- `address(v)`: the bare address in `Name <a@b>` or a bare address, read
  through `strip`, lower case (`mail_rules.address_of`).

`unicodedata.combining()` is NOT the test for a mark: it answers 0 for an
enclosing mark (Me, U+20DD) and for a spacing mark (Mc, U+0903), which the
page drops. The category is the test.

Stated limits (ruling 147x): case is `lower()`, not `casefold()`, so the page
can match it byte for byte: "ß" and "ss" are different here (the follow-up
screen casefolds on top of this, for its own Python-only match). Look-alike
letters and dashes are read only by import_wall, on top of `strip`.

Stated limit (review of cr1#1779): the two folds read Unicode at different
versions. Python 3.12's `unicodedata` is Unicode 15.0 and the page's
`String.prototype.normalize` and its mark class (`[\\p{M}]`) follow the browser's (node: 17.0),
so the 259 code points that are new or recategorised between those versions
can fold differently on the two sides. A name written with one of them is
checked as each side reads it; ordinary letters are unaffected.

Pure: no I/O, imports only the standard library (and `mail_rules`, lazily,
for `address`).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

__all__ = ["strip", "fold", "words", "address", "is_mark"]

_NOT_WORD = re.compile(r"[^\w]+")


def is_mark(ch: str) -> bool:
    """Whether `ch` is a mark (category Mn, Mc or Me)."""
    return unicodedata.category(ch)[0] == "M"


def strip(value: Any, *, keep_format: bool = False) -> str:
    """NFKD, marks out, and (unless `keep_format`) format characters out.
    Case and spacing as they were."""
    t = unicodedata.normalize("NFKD", str(value or ""))
    if keep_format:
        return "".join(c for c in t if not is_mark(c))
    return "".join(c for c in t if not is_mark(c) and unicodedata.category(c) != "Cf")


def fold(value: Any) -> str:
    """`strip`, whitespace runs one space, trimmed, lower case: the page's
    `normOf`. Python's `split()` also reads U+001C to U+001F and U+0085 as
    spaces, as `normOf` does by hand."""
    return " ".join(strip(value).split()).lower()


def words(value: Any) -> str:
    """`fold`, every non-word character a space: a name as its words."""
    return " ".join(_NOT_WORD.sub(" ", fold(value)).split())


def address(value: Any) -> str:
    """The bare address, read through `strip`, lower case; empty when there
    is none (`mail_rules.address_of`, which strips first itself)."""
    import mail_rules

    return mail_rules.address_of(value)
