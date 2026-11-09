"""Does a reply say that the file on it IS the thing owed? (cr1#764)

One job: read one inbound message against one item title and say whether it
hands that item over, so the inbound close never takes "a file arrived on the
thread" for "the deliverable arrived". Pure, no ledger, never raises into a
job.

THE RULE (owner ruling, 2026-11-07): a delivery closes only at high
confidence. Everything else is a proposal, which the owner confirms on the
Review tab; some genuine deliveries propose, and that is accepted. So this
module lists what a closing message may say, never what stops one. A word or
a sentence it does not know is a proposal, which is why "isn't", "won't",
"declining", "next week" and "more tomorrow" need no entry anywhere.

`delivery_strength` closes only when ALL of these hold:

  * the connector named the files, and at least one is a document. A file
    with no name, a calendar invite or a signature image is never the item;
  * every clause of the sender's own words (`own_words`: no quoted history,
    no legal footer, no signature block) is one of three things: courtesy
    (`COURTESY_PHRASES`, a greeting, a party's name), a hand-over
    (`HANDOVER_PHRASES`) of the item or of no named thing, or the item named
    beside a hand-over or a citation of the promise (`CITED_PHRASES`). No
    sentence is a question;
  * every word of a hand-over is the item's own, a state of a document
    (`QUALIFIER_WORDS`), a word for a document (`GENERIC_DOCUMENT_WORDS`), a
    party's name or a word that says nothing (`PLAIN_WORDS`);
  * the item is named, by those words or by a file's name read as strictly.
    "Here you go" names nothing, so it takes a file whose name is the item;
  * no document on the message says it is something else. A file name that
    tells nothing (`scan_0001.pdf`) leaves the words to speak.

`asks_for_delivery` reads the title the same way: its ask must open with a
delivery word (`DELIVERY_LEADS`) whose object is the document. "To pay the
invoice", "feedback on the proposal" and "send payment on the invoice" are
not delivered by a file, and "to send the headshots" names no document noun
yet is.

The grade is how many of the item's words the message names, so two items on
one thread can be compared (`attachment_is_the_item` closes only a strict
winner); 0 is a proposal.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

NO_LINK = 0

#: Attachment names that are never the thing owed: a calendar invite, a
#: signature image, a contact card, a signature certificate.
NON_DELIVERABLE_SUFFIXES = (".ics", ".vcf", ".vcs", ".png", ".jpg", ".jpeg",
                            ".gif", ".bmp", ".svg", ".webp", ".p7s", ".asc",
                            ".sig")

#: Document shapes the matcher's list lacks. Read here only, so they name a
#: document in a message without widening what the matcher treats as a
#: document ask.
MORE_DOCUMENT_NOUNS = ("form", "paperwork", "sheet", "workbook", "checklist",
                       "nda", "msa", "questionnaire", "template", "certificate")

#: The words of waiting for the thing. "Needs to sign" is not one: followed
#: by "to", the word opens another act.
_WAITING_LEADS = frozenset({"owe", "owed", "waiting", "awaiting", "expecting",
                            "need"})

#: The words that open a delivery ask in a title.
DELIVERY_LEADS = frozenset({"send", "resend", "share", "forward", "deliver",
                            "provide", "submit", "return", "email",
                            "upload"}) | _WAITING_LEADS

#: What ends the object of the ask: "send payment ON the invoice" is a
#: payment, "send the contract TO Sam" is the contract.
_PREPOSITIONS = frozenset({
    "on", "for", "of", "about", "regarding", "re", "to", "from", "by", "with",
    "in", "at", "due", "before", "after", "until"})

#: The sender handing something over, now (not acknowledging receipt: "got
#: it" and "received it" are the OTHER direction and stay out). Longest
#: first, because each is cut out of the clause to see what is left.
HANDOVER_PHRASES = (
    "please find attached", "please find enclosed", "please see attached",
    "here they are", "here you go", "here it is", "sending over",
    "sending you", "find attached", "see attached", "please find",
    "sent over", "attaching", "sending", "attached", "enclosed", "here are",
    "here is", "here's",
)

#: Words that cite the promise without handing anything over: they count
#: only beside a hand-over or the item's name ("the vendor contract, as you
#: asked"), so "as promised, let's talk Monday" is nothing.
CITED_PHRASES = ("as you requested", "as you asked", "as requested",
                 "as promised", "as discussed", "per your request",
                 "we discussed", "you asked for", "you requested",
                 "for your records", "for your files", "for your convenience")

#: Whole clauses that only open or close a message. The questions sign-off is
#: one; any other "let me know if..." asks something of the owner and proposes.
COURTESY_PHRASES = frozenset({
    "thanks", "thank you", "thanks so much", "thank you so much",
    "many thanks", "thanks again", "thx", "ty", "thanks for waiting",
    "thank you for waiting", "thanks for your patience",
    "thank you for your patience", "best", "best regards", "regards",
    "kind regards", "warm regards", "cheers", "sincerely", "all the best",
    "sorry for the delay", "apologies for the delay", "sorry for the wait",
    "hope you are well", "hope you're well", "hope all is well",
    "hope this helps", "let me know if you have questions",
    "let me know if you have any questions",
    "let me know if you need anything",
    "let me know if you need anything else", "any questions let me know",
    "happy to answer any questions"})

#: Who a greeting may name besides a person's capitalised name.
_GREETED_GROUPS = frozenset({"all", "team", "there", "everyone", "folks"})
_GREETING_RE = re.compile(
    r"^\s*(?:hi|hello|hey|dear|good (?:morning|afternoon|evening))\b(.*)$",
    re.IGNORECASE)

#: A line that opens the signature block.
_SIGN_OFF_LINE_RE = re.compile(
    r"^(?:--+|(?:thanks again|many thanks|thank you|thanks|thx|best regards"
    r"|kind regards|warm regards|regards|all the best|best|cheers|sincerely)"
    r"[,.]?)$")

#: Lowercase words a signature line may hold ("VP of Sales").
_SIGNATURE_CONNECTORS = frozenset({"of", "and", "the", "at", "for"})

_SENT_FROM_RE = re.compile(r"^\s*sent from my\b", re.IGNORECASE)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_CLAUSE_RE = re.compile(r"\s*[,;:]\s*|\s+[-–]\s+")
_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")

#: Where quoted thread history or a legal footer starts: what follows is not
#: this sender's word.
_QUOTED_RE = re.compile(
    r"\bon\b[^\n]{5,160}\bwrote:|-{2,}\s*original message|^\s*from:\s.+$"
    r"|\bif you are not the intended recipient\b|\bconfidentiality notice\b",
    re.IGNORECASE | re.MULTILINE)

#: Words that say what a document looks like, never which document it is.
GENERIC_DOCUMENT_WORDS = frozenset({
    "file", "doc", "document", "pdf", "attachment", "deliverable", "paperwork",
    "scan", "copy", "version", "image", "img", "untitled", "download",
    "noname"})

#: What state a document is in, never which document it is. "Signed" on its
#: own never tells a signed contract from a signed form.
QUALIFIER_WORDS = frozenset({
    "signed", "countersigned", "executed", "final", "finalized", "updated",
    "revised", "latest", "new", "current", "completed", "clean", "full",
    "fully"})

#: The verb of the ask, in the item's title.
ASK_WORDS = frozenset({
    "send", "sending", "sent", "resend", "share", "provide", "forward",
    "deliver", "get", "give", "submit", "return", "confirm", "review",
    "prepare", "finish", "complete", "finalize", "owe", "need", "waiting"})

#: Words in a hand-over that say nothing about what is handed over.
PLAIN_WORDS = frozenset({
    "the", "a", "an", "our", "my", "your", "this", "that", "these", "those",
    "is", "are", "it", "over", "from"})


def _fold_set(words: Iterable) -> set:
    from cru_match import fold_plural
    return {fold_plural(w) for w in words}


def _tokens(text) -> set:
    from cru_match import _content_tokens
    return _fold_set(_content_tokens(str(text or "")))


def _document_nouns() -> set:
    from cru_match import DELIVERABLE_ARTIFACT_NOUNS, _phrases
    nouns = _fold_set(_phrases("deliverable_artifact_nouns",
                               DELIVERABLE_ARTIFACT_NOUNS))
    return (nouns | _fold_set(MORE_DOCUMENT_NOUNS)) - _fold_set(GENERIC_DOCUMENT_WORDS)


def _raw_words(text) -> list:
    return _WORD_RE.findall(str(text or "").lower().replace("’", "'"))


def _name_tokens(names) -> set:
    out: set = set()
    for name in names or ():
        out |= _tokens(name)
    return out


def _lead_index(words: list):
    """Where the delivery ask opens, None when the title has none."""
    from cru_match import fold_plural
    for i, word in enumerate(words):
        folded = fold_plural(word)
        if folded not in DELIVERY_LEADS:
            continue
        if folded in _WAITING_LEADS and words[i + 1:i + 2] == ["to"]:
            continue
        return i
    return None


def asks_for_delivery(title, party_names=()) -> bool:
    """Can a file do what the title asks? Only when its ask opens with a
    delivery word and the object of that ask is the document: the words after
    the lead, up to the first preposition. A title with no document at all
    ("to send the headshots") passes on the lead alone, and the message must
    then name it. Anything else is an act of its own."""
    parties = _name_tokens(party_names)
    words = [w for w in _raw_words(title) if not _fold_set([w]) & parties]
    lead = _lead_index(words)
    if lead is None:
        return False
    nouns = _document_nouns()
    if not _fold_set(words) & nouns:
        return True
    rest = words[lead + 1:]
    while rest and rest[0] in ("on", "for"):      # "waiting on", "waiting for"
        rest = rest[1:]
    while rest:
        cut = next((i for i, w in enumerate(rest) if w in _PREPOSITIONS), len(rest))
        obj = _fold_set(rest[:cut])
        if obj & nouns:
            return True
        # "a copy of the contract": a word for a document leads to the next.
        if not obj & _fold_set(GENERIC_DOCUMENT_WORDS):
            return False
        rest = rest[cut + 1:]
    return False


def is_document_name(name) -> bool:
    low = str(name or "").strip().lower()
    return bool(low) and not low.endswith(NON_DELIVERABLE_SUFFIXES)


def own_words(body) -> str:
    """The sender's own text: quoted lines and quoted history removed."""
    text = str(body or "").replace("’", "'")
    hit = _QUOTED_RE.search(text)
    if hit:
        text = text[:hit.start()]
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith(">"))


def _is_signature_line(line) -> bool:
    """A name, a title, a company or a contact line. A line holding a sentence
    ("Signed copy next week") is not one, so it is still read."""
    text = str(line or "").strip()
    if not text or "@" in text or "://" in text or _SENT_FROM_RE.match(text):
        return True
    if text.endswith((".", "!", "?")) or text.lower().startswith(("p.s", "ps ")):
        return False
    return all(word[:1].isupper() or not word[:1].isalpha()
               or word.lower() in _SIGNATURE_CONNECTORS
               for word in text.split())


def _without_signature(text) -> str:
    """`text` with the signature set aside: from a sign-off line on, each line
    that is a signature's. Any other line after it is still read."""
    lines = str(text or "").splitlines()
    for i, line in enumerate(lines):
        if _SIGN_OFF_LINE_RE.match(line.strip().lower()):
            return "\n".join(lines[:i] + [ln for ln in lines[i + 1:]
                                          if not _is_signature_line(ln)])
    return str(text or "")


def _item_terms(title, party_names) -> tuple:
    """`(words, nouns)`: what identifies this item among documents. The
    parties' names, the verb of the ask and the state of the document are not
    in it."""
    words = (_tokens(title) - _fold_set(GENERIC_DOCUMENT_WORDS)
             - _fold_set(QUALIFIER_WORDS) - _fold_set(ASK_WORDS)
             - _name_tokens(party_names))
    return words, words & _document_nouns()


def _noise() -> set:
    return _fold_set(QUALIFIER_WORDS) | _fold_set(GENERIC_DOCUMENT_WORDS)


def _names_item(tokens: set, words: set, nouns: set) -> int:
    """How many of the item's words `tokens` names, 0 when it does not name
    the item. It takes the item's document noun, or two of its words, or its
    one word when the item has no noun. `tokens` may hold nothing else: no
    other document, no other word."""
    if (tokens & _document_nouns()) - nouns:
        return 0
    shared = tokens & words
    if not shared or not ((shared & nouns) or len(shared) >= 2 or not nouns):
        return 0
    return 0 if tokens - shared - _noise() - _fold_set(ASK_WORDS) else len(shared)


def _phrase_re(phrases) -> "re.Pattern":
    return re.compile(r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r")\b")


_HANDOVER_RE = _phrase_re(HANDOVER_PHRASES)
_CITED_RE = _phrase_re(CITED_PHRASES)


def _is_courtesy(clause, party_tokens: set) -> bool:
    """A clause that only opens or closes the message."""
    words = _raw_words(clause)
    if not words or " ".join(words) in COURTESY_PHRASES or _SENT_FROM_RE.match(clause):
        return True
    greeting = _GREETING_RE.match(clause)
    if greeting:
        rest = greeting.group(1).split()
        return len(rest) <= 2 and all(w[:1].isupper() or w.lower() in _GREETED_GROUPS
                                      for w in rest)
    return _fold_set(words) <= party_tokens


_COURTESY, _HANDED, _BARE, _CITED, _NAMED_ONLY, _OTHER = range(6)


def _read_clause(clause, words: set, nouns: set, party_tokens: set) -> tuple:
    """`(kind, n)`: what this clause is, and how many of the item's words it
    names. `_OTHER` for anything but the shapes in the module docstring."""
    if _is_courtesy(clause, party_tokens):
        return _COURTESY, 0
    low = clause.lower()
    handover = bool(_HANDOVER_RE.search(low))
    cited = bool(_CITED_RE.search(low))
    rest = _CITED_RE.sub(" ", _HANDOVER_RE.sub(" ", low))
    pieces = _fold_set(p for w in words for p in _WORD_RE.findall(w))
    allowed = words | pieces | party_tokens | _noise() | _fold_set(PLAIN_WORDS)
    if not _fold_set(_raw_words(rest)) <= allowed:
        return _OTHER, 0
    tokens = _tokens(rest) - party_tokens
    n = _names_item(tokens, words, nouns)
    if n:
        return (_HANDED if handover or cited else _NAMED_ONLY), n
    if tokens - _noise():
        return _OTHER, 0
    if handover:
        return _BARE, 0
    return (_CITED, 0) if cited else (_OTHER, 0)


def _read_words(body, words: set, nouns: set, party_tokens: set):
    """How many of the item's words the sender's own text names, 0 for a
    hand-over of no named thing; None when it is not a plain hand-over."""
    handed, named = False, 0
    for sentence in _SENTENCE_RE.split(_without_signature(own_words(body))):
        if not sentence.strip():
            continue
        if sentence.rstrip().endswith("?"):
            return None
        if " ".join(_raw_words(sentence)) in COURTESY_PHRASES:
            continue
        read = [_read_clause(c, words, nouns, party_tokens)
                for c in _CLAUSE_RE.split(sentence) if c.strip()]
        kinds = {kind for kind, _n in read}
        if _OTHER in kinds:
            return None
        if _NAMED_ONLY in kinds and not kinds & {_HANDED, _BARE, _CITED}:
            return None
        if _CITED in kinds and not kinds & {_HANDED, _BARE, _NAMED_ONLY}:
            return None
        handed = handed or bool(kinds - {_COURTESY})
        named = max([named] + [n for _kind, n in read])
    return named if handed else None


def _file_tokens(name) -> set:
    """A file name's words; a version or scan number is not a word, a year
    is."""
    stem = str(name).rsplit(".", 1)[0]
    return {t for t in _tokens(re.sub(r"[_\-.+]+", " ", stem))
            if not any(c.isdigit() for c in t) or re.fullmatch(r"(?:19|20)\d\d", t)}


def delivery_strength(body, attachment_names, title, party_names=()) -> int:
    """How many of the item's words the message hands over, 0 for a proposal
    (the rule is the module docstring). `attachment_names` is [] when the
    connector sent none: a file with no name is a proposal."""
    documents = [n for n in (attachment_names or ())
                 if str(n or "").strip() and is_document_name(n)]
    if not documents:
        return NO_LINK
    words, nouns = _item_terms(title, party_names)
    party_tokens = _name_tokens(party_names)
    read = _read_words(body, words, nouns, party_tokens)
    if read is None:
        return NO_LINK
    files = [_file_tokens(n) - party_tokens for n in documents]
    file_grade = max(_names_item(t, words, nouns) for t in files)
    if file_grade:
        return max(read, file_grade)
    return read if all(not t - _noise() for t in files) else NO_LINK


__all__ = ["NO_LINK", "NON_DELIVERABLE_SUFFIXES", "HANDOVER_PHRASES",
           "CITED_PHRASES", "COURTESY_PHRASES", "DELIVERY_LEADS",
           "is_document_name", "own_words", "asks_for_delivery",
           "delivery_strength"]
