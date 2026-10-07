#!/usr/bin/env python3
"""surface_leak_patterns — LEAK2's family: the shapes that reached NARRATION,
GROUP HEADERS and CARDS while every widget row scanned clean.

WHY THIS EXISTS. The v5.29.0 attended test (2026-09-07) found raw ids on
customer surfaces six times across five surfaces, and not one of them was a
shape either scanner knew:

  - B1.3  a bare hex mail-message id stood as a plate GROUP HEADER
          (sixteen hex characters, no `superhuman:` prefix, so
          `connector_id_patterns` never saw it);
  - B3.5  a Granola id (a bare UUID), two folder paths, a spec code and
          test-battery numbers in a ONE-LINE capture reply;
  - D4     file paths, spec ids and script names in the router-miss answer.

Every id pattern in both scanners anchored on a PREFIX — `person_`, `cmt_`,
`bp_`, `slack:`. An opaque token with no prefix had zero coverage, and the
plumbing vocabulary (a `.py` file, `SPEC_FLOW1`, `tests/run_all.py`, "the
battery") had none either.

THE FAMILY, IN TWO HALVES. Mirrored into both scanners the way
`connector_id_patterns` and `vocabulary_policy.marketing_patterns` already
are — a pattern added HERE is scanned everywhere; never re-declare one in a
scanner.

  1. `surface_id_patterns()` — OPAQUE IDS. Dangerous no matter who typed
     them: a bare hex message id or a bare UUID identifies nothing to a
     reader and is machine plumbing wherever it appears. ALWAYS-SCANNED in
     both `chat_output_renderer._LEAK_PATTERNS` and
     `docx_leak_scanner._FORBIDDEN_PATTERNS`, exactly like the entity ids.

  2. `plumbing_vocab_leak_patterns()` — THE BUILD'S OWN WORDS: script names,
     tree paths, spec/lane codes, guard ids, battery talk. Registered in the
     CHAT scanner only, and listed in `USER_TEXT_BLANKED_LABELS` there — an
     operator who writes their own commitment titles about this product's
     internals is quoting their own work (the same reasoning that already
     exempts `schema-field leak`). Renderer- and model-authored occurrences
     still refuse, which is the case D4 and B3.5 are. Deliberately NOT in
     the docx scanner: a research brief may legitimately name a file.

URLS ARE NOT LEAKS. A `Sources:` line carries real links, and a Gmail or
Granola URL has an opaque id INSIDE it. Both id patterns therefore refuse to
match a token that sits inside a path or a query (`(?<![/=#?&.-])`), so the
label a reader sees is scanned and the href is left alone. This is the same
split `_scan_for_path_leaks` already makes with `paths_text`.

stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path


# A module-level name this product could narrate: a `def`, a `class`, or a
# plain module-level assignment, read off the source text rather than by
# importing anything (importing 262 modules at import time is not an option,
# and half of them have side effects).
_MODULE_ATTR_RE = re.compile(
    rb"^(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"
    rb"|^([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=\n]+)?=[^=]",
    re.MULTILINE)
_SNAKE_ATTR_RE = re.compile(rb"[a-z_][a-z0-9_]*\Z")

# REVIEW_LEAK3 F-2 — the right-hand side of a dot is never a file extension.
# Belt-and-braces beside the attribute check below: if some module ever
# grows an attribute literally called `csv` or `html`, an ordinary sentence
# naming an attachment must still post.
DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({
    "pdf", "zip", "rar", "csv", "tsv", "xlsx", "xls", "xlsm", "docx", "doc",
    "pptx", "ppt", "jpg", "jpeg", "png", "gif", "webp", "heic", "svg",
    "html", "htm", "md", "txt", "rtf", "mp3", "mp4", "mov", "wav", "m4a",
    "json", "jsonl", "yaml", "yml", "xml", "ics", "eml", "msg", "key",
    "pages", "numbers", "com", "net", "org", "io", "co", "ai", "app",
})


def _discover_shared_script_modules() -> dict:
    """Every module filename (minus `.py`) under `shared/scripts/` mapped to
    the module-level names it actually defines, built from the tree AT
    IMPORT — LEAK3 6.1: "module is any file under shared/scripts/ (build the
    list at import)". `surface_leak_patterns.py` lives in that same
    directory, so its own parent is the directory to list; a file this
    module cannot see (a bad install, a read error) degrades to an empty
    result rather than raising — the dotted-internal pattern is additive
    coverage, never load-bearing enough to brick every render on a missing
    directory.

    REVIEW_LEAK3 F-2 — ONE tree scan now yields BOTH halves. The first cut
    took only the filenames, and a dozen of them are ordinary English nouns
    (`brand`, `receipts`, `claims`, `charts`, `profile`, `balance`,
    `quiet`…), so `<stem>.<lowercase word>` — the shape of an everyday
    attachment — hard-refused eleven of twelve ordinary customer sentences
    ("I attached brand.pdf", "she sent receipts.zip"). The dot only means
    something when the name on the RIGHT is a name that module really
    carries, so the attribute set is derived in the same pass."""
    here = Path(__file__).resolve().parent
    found: dict = {}
    try:
        paths = sorted(here.glob("*.py"))
    except OSError:  # pragma: no cover — defensive only
        return {}
    for p in paths:
        if not p.stem or p.stem == "__init__":
            continue
        try:
            src = p.read_bytes()
        except OSError:  # pragma: no cover — defensive only
            continue
        names = set()
        for m in _MODULE_ATTR_RE.finditer(src):
            raw = m.group(1) or m.group(2)
            if raw and _SNAKE_ATTR_RE.match(raw):
                name = raw.decode("ascii", "replace")
                if name not in DOCUMENT_EXTENSIONS:
                    names.add(name)
        found[p.stem] = frozenset(names)
    return found


def _maintenance_job_ids() -> frozenset[str]:
    """Job ids from `maintenance_dispatcher.MAINTENANCE_JOBS`, per LEAK3
    6.1's roster. Imported lazily (a function, not a module-level import)
    because `maintenance_dispatcher` is a much heavier module with its own
    dependency chain; failing to import it must not break every chat
    render. Falls back to a snapshot taken at this build's kickoff
    (`525364f2`) if the import fails — stale is better than empty, and
    either way this function is the ONE place the fallback list lives."""
    try:
        from maintenance_dispatcher import MAINTENANCE_JOBS
        return frozenset(MAINTENANCE_JOBS)
    except ImportError:  # pragma: no cover — direct-path fallback
        return frozenset({
            "reconcile-sent", "reconcile-chat", "meeting-capture",
            "session-sweep", "cleanup", "weekly-insights", "learning",
            "deal-signals", "identity-reconcile", "lifecycle",
            "review-expiry", "question-expiry", "age-out", "calendar-close",
            "exit-doors", "binding-gauge", "daily-measure", "monthly-report",
        })


# The module list and its attribute sets — computed once at import from ONE
# tree scan, never hand-typed.
DOTTED_INTERNAL_ATTRS: dict = _discover_shared_script_modules()
DOTTED_INTERNAL_MODULES: frozenset[str] = frozenset(DOTTED_INTERNAL_ATTRS)


def _dotted_internal_source() -> str:
    """`module.attribute` for every module under `shared/scripts/`, each
    module paired ONLY with the names it actually defines (REVIEW_LEAK3
    F-2). A module with no module-level names contributes nothing, and a
    tree this module cannot read contributes nothing at all — the caller
    substitutes a never-matching pattern in that case."""
    parts = []
    for mod in sorted(DOTTED_INTERNAL_ATTRS, key=len, reverse=True):
        attrs = DOTTED_INTERNAL_ATTRS[mod]
        if not attrs:
            continue
        parts.append(
            re.escape(mod) + r"\.(?:"
            + "|".join(re.escape(a) for a in sorted(attrs, key=len, reverse=True))
            + r")")
    if not parts:
        return r"(?!)"
    return r"\b(?:" + "|".join(parts) + r")\b"

# LEAK3 6.1 — "bare snake_case identifiers from a shipped roster
# (n_deduped, source_ref, root_repair, dormancy_signal,
# load_open_commitments, past-meetings, job ids from MAINTENANCE_JOBS) — the
# roster is generated from the tree (a guard keeps it current), never
# hand-typed." The six named literals are hand-typed here (they are not
# derivable from any tree scan — they are function/field names chosen by
# people, not filenames) and NAMED so a guard can assert each one is still a
# real identifier somewhere in the tree (see
# `tests/run_leak3_corpus_test.py`, which is that guard's first cut — full
# automatic derivation of this half is NOT built; see this lane's STOP
# record, "not built + why").
#
# THE MAINTENANCE_JOBS HALF IS DELIBERATELY NOT FOLDED IN HERE.
# `_maintenance_job_ids()` above IS the generated half the spec asks for,
# and it is real and tested — but three of its members (`cleanup`,
# `learning`, `lifecycle`) are ALSO ordinary English words with zero
# distinguishing shape, and folding the raw set into a bare word-boundary
# match refused real customer sentences on the over-block fixture this lane
# is required to run ("I did a big cleanup of my inbox this week.", "I am
# learning to delegate more.", "The product lifecycle is entering
# maturity." — all three refused before this line was written). A fourth,
# `age-out`, is a real compound a client in HR/legal/benefits prose can
# write ("we need an age-out provision"). The `battery` pattern earlier in
# this file already established the fix for this exact shape — narrow to
# the BUILD's phrasing of a word, never the bare word — and that narrowing
# is not built for the maintenance-job vocabulary yet (see STOP record).
# `_maintenance_job_ids()` stays exported and tested so the next pass can
# do that narrowing without re-deriving the job list.
# `pattern_break_detected` joins its own pair (REVIEW_LEAK4 F-5). Both halves
# of recorded instance 2's trailing block name an event type, and only one of
# them was caught by name: `dormancy_signal` is here, and
# `pattern_break_detected` was reachable ONLY through the event-type row in
# `chat_output_renderer`, which needs `event(s)` / `written` / `logged` /
# `appended` to follow it. "The pattern_break_detected rows are stale." was
# clean. It is not a payload key anywhere in the tree - it is an event TYPE,
# and the type rows are matched as values, not as keys - so the same reasoning
# that makes `dormancy_signal` safe here makes this one safe.
_VOCAB_HAND_ROSTER: frozenset = frozenset({
    "n_deduped", "source_ref", "root_repair", "dormancy_signal",
    "pattern_break_detected",
    "load_open_commitments", "past-meetings",
})

# THE GENERATED HALF (SPEC FIXTRAIN v5.31.0 6.5) — measured, then NARROWED.
#
# The spec for this lane states that "every one of the 13 strings raises when
# fed to the gate as machine text", which was true of the v5.30.0 record and
# is NOT true of the v5.31.0 one. Reconstructing the thirteen recorded
# instances at this lane's base, TEN raised and three did not:
#
#   instance 3  — the brief's raw prep paths (closed by 6.3, above);
#   instance 7  — the let-go undo's explanation, which named an event type in
#                 code font and the door's own config key
#                 (`exit.silence_age_out`);
#   instance 11 — the brief-grouping refusal, which named two brief setting
#                 keys and a parser status.
#
# THE FIRST CUT OF THIS WAS TOO WIDE AND THE MEASUREMENT CAUGHT IT. It read
# every quoted `snake_case` literal in the three modules that declare this
# product's machine vocabulary — 245 tokens — and two of them
# (`owed_to_you`, `source_skill`) are DATA-VIEW KEYS that the product's own
# spec examples are required to carry. `run_leak2_test` and
# `run_spec_example_render_test` both went red on them, in different ways,
# and both were right: a payload key is not a sentence, and a roster that
# refuses one refuses the shapes this repository is built out of. Two
# collisions inside the 152 suites this lane ran is a promise of more inside
# the 450 it could not, so the wide half is gone rather than patched.
#
# WHAT SURVIVES IS THE HALF THAT CANNOT COLLIDE: the DOTTED config keys, read
# from the module that declares them. `exit.silence_age_out`,
# `identity.auto_merge`, `questions.expire_to_default` — a namespace, a dot,
# and a snake_case name. That shape is never a payload key (JSON keys here
# are undotted), never ordinary prose, and never a module reference (the
# dotted-internal pattern below requires a real `shared/scripts` module on
# the left). It closes instance 7, which is what it is for.
#
# Instance 11 is closed by `_SETTING_KEY_NAMED_SRC` below — a SHAPE, not a
# roster — and the reasoning for preferring a shape there is with it.

_VOCAB_SOURCE_MODULES = ("commitment_policy.py",)

# A namespace, a dot, a snake_case name. The floor keeps a bare `a.b` out.
_VOCAB_TOKEN_RE = re.compile(
    r"[\"\']([a-z][a-z0-9]*\.[a-z][a-z0-9]*(?:_[a-z0-9]+)+)[\"\']")

_VOCAB_MIN_LEN = 10


def _tree_vocab_tokens() -> frozenset:
    """Every dotted config key the policy module declares.

    Read as BYTES with a line regex — nothing is imported, so this stays free
    of the import cycle a real import would create and costs one file read at
    module import.
    """
    here = Path(__file__).resolve().parent
    out: set = set()
    for name in _VOCAB_SOURCE_MODULES:
        try:
            text = (here / name).read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - a partial install
            continue
        for tok in _VOCAB_TOKEN_RE.findall(text):
            if len(tok) >= _VOCAB_MIN_LEN:
                out.add(tok)
    return frozenset(out)


TREE_VOCAB_ROSTER: frozenset = _tree_vocab_tokens()

# AND THE BRIEF'S OWN AXES, BY NAME — derived from the module that declares
# them, never typed here.
#
# The recorded sentence named "two brief setting keys and a parser status".
# The shapes above catch a key INTRODUCED with a noun or an `=`; they do not
# catch "leads_with is now your calendar", which is the plainest way this
# product would say it and is one of the phrasings the reviewer found clean.
# Those particular names are safe to carry by name for the same reason
# `dormancy_signal` is: the UNDERSCORE. A customer writes "going quiet", not
# `going_quiet`, and the earlier over-wide cut of this roster failed on
# `owed_to_you` / `source_skill` because those are PAYLOAD keys the product's
# own spec examples must carry — an axis name is not a payload key.
#
# Read as BYTES off `brief_settings.AXES`' own literal (no import — the same
# cycle reasoning as `_tree_vocab_tokens`), filtered to underscore-joined
# tokens of at least `_VOCAB_MIN_LEN`. That filter is what keeps `depth`,
# `tone`, `shape`, `voice`, `when` and `filters` — all ordinary English, all
# axis names — off the roster. An axis added tomorrow is covered with no edit.
_AXIS_SOURCE_MODULE = "brief_settings.py"
_AXIS_TOKEN_RE = re.compile(
    r"^ {4}[\"']([a-z][a-z0-9]*(?:_[a-z0-9]+)+)[\"']: \{", re.M)


def _brief_axis_tokens() -> frozenset:
    """The brief/day-close setting keys, off the module that declares them."""
    here = Path(__file__).resolve().parent
    try:
        text = (here / _AXIS_SOURCE_MODULE).read_text(encoding="utf-8",
                                                      errors="replace")
    except OSError:  # pragma: no cover - a partial install
        return frozenset()
    return frozenset(tok for tok in _AXIS_TOKEN_RE.findall(text)
                     if len(tok) >= _VOCAB_MIN_LEN)


BRIEF_AXIS_ROSTER: frozenset = _brief_axis_tokens()

# ONE roster, ONE alternation, ONE finding per string. The hand half and the
# generated half are merged here rather than registered as two rows, because
# two rows sharing a label double-count a string that matches both (F-1).
GENERATED_VOCAB_ROSTER: frozenset = frozenset(
    _VOCAB_HAND_ROSTER | TREE_VOCAB_ROSTER | BRIEF_AXIS_ROSTER)

# INSTANCE 11, AS A SHAPE RATHER THAN A ROSTER.
#
# The recorded sentence named two of the brief's own setting keys and a
# parser status. Every roster that catches `leads_with` and `section_depth`
# by NAME also catches them where they legitimately belong — in a payload, in
# a spec's example dict, in a writer's own field list — because they are the
# same strings. What is different about the leak is the CONTEXT: the product
# was telling the reader about "the leads_with and section_depth keys", which
# is a sentence, and a sentence is where a key name has no business being.
#
# So: a snake_case internal identifier immediately followed by the word that
# marks it as a mechanic — key, field, flag, setting, parameter. A payload
# carries the identifier alone; only prose puts a noun after it. Customer
# prose does not produce this shape: it needs an underscore-joined identifier
# AND the mechanic word AND nothing between them.
#
# WIDENED (RE-VERIFY_LEAK4 N-5, 2026-09-18). The reviewer rebuilt instance 11
# independently and five of their seven natural phrasings were CLEAN at the
# first tip, because this shape needs the mechanic word flush against the
# identifier. Two things sit between them in real prose and neither changes
# what the sentence is: CODE FONT or quotes round the identifier ("the
# `leads_with` key"), and a mechanic ADJECTIVE before the noun ("the
# leads_with SETTING key", "its config flag"). Both are admitted now.
_SETTING_KEY_NAMED_SRC = (
    r"[`\"']?\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b[`\"']?\s+"
    r"(?:(?:config|configuration|internal|setting|settings|default)\s+)?"
    r"(?:key|keys|field|fields|flag|flags|setting|settings|parameter|"
    r"parameters)\b")

# THE SAME IDENTIFIER, ASSIGNED — the parser-status half of instance 11.
#
# `parse_status=OK` carries no mechanic noun for the shape above to hang on,
# and it is exactly how a machine reports a machine's state. An
# underscore-joined lower-case identifier, an `=`, and a value pressed
# against it with no spaces is a machine assignment wherever it is written;
# a customer writing about a price or a date writes `$500` and `May 3`, never
# `unit_cost=500`. The underscore is what keeps `x=1` and `rate=4` out.
_SETTING_KEY_ASSIGNED_SRC = (
    r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+=[A-Za-z0-9_.:/-]+")

# A token inside a URL path, query or fragment is provenance, not prose.
#
# REVIEW_LEAK2 F-5 / F-7: this character class is the DELIVERABLE scanner's
# carve-out, and it is a blunt one — excluding `.` and `-` also lets a
# prose-adjacent id through (`ref-18f3a9c7d2e14b60`, `thread.18f3a9…`), and
# it protects only the two id patterns, so a code-citing link tripped the
# VOCABULARY half (`.../tests/run_all.py`). The chat scanner therefore
# carves URLs BY SPAN instead (`chat_output_renderer._URL_SPAN_RE`), which
# covers both halves and needs no lookbehind — see `surface_id_leak_patterns`
# below. The docx scanner has no span machinery and keeps the class.
_NOT_IN_URL = r"(?<![/=#?&.\-\w])"

# The id shapes themselves, WITHOUT any URL carve-out. Each registration
# function adds the carve-out its scanner can afford.
SURFACE_ID_PATTERNS: list[tuple[str, str]] = [
    # A bare opaque hex id — a mail message id, a thread key, a digest. The
    # lookahead requires at least one a–f so a long DECIMAL number (an
    # invoice, a phone run, a dollar figure) is not swept in; sixteen is the
    # floor because that is the shortest shape observed (B1.3) and shorter
    # hex runs collide with ordinary words.
    #
    # REVIEW_LEAK2 F-6: an IBAN is a hex-only run with an `a-f` letter in it
    # (`DE89370400440532013000` — German, Belgian and UAE account numbers
    # all start with hex letters), and a payment memo or a wire instruction
    # legitimately carries one. A country prefix — two UPPER-CASE letters
    # then two check digits, the ISO 13616 shape — is exempt. The case is
    # load-bearing and the exemption says so inline (`(?-i:…)`), so a
    # lower-case `ab12…` id is still refused under IGNORECASE.
    ("bare_opaque_id",
     r"\b(?!(?-i:[A-Z]{2}\d{2}))(?=[0-9a-f]{16,}\b)(?=[0-9a-f]*[a-f])"
     r"[0-9a-f]{16,}\b"),
    # A bare UUID — the Granola id shape (B3.5). `granola:<uuid>` was already
    # covered by connector_source_ref; the naked one was not.
    ("bare_uuid",
     r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
     r"[0-9a-f]{12}\b"),
    # TRUTH1 / SPEC_MERGEFIX1 F16 — the sandbox SESSION id, which the
    # receipt contract stores in `data.machine` and which the health check
    # printed to a customer verbatim: "the most recent one ran Saturday 12:49
    # PM (on claude-f352)". `machine_identity` mints `<node>-<4 hex>`, and
    # inside the container `platform.node()` is the same word on every
    # computer (that module's own opening finding), so the token identifies a
    # SESSION, names no machine, and is an opaque id on a customer surface —
    # this family's whole subject. The renderer that leaked it is fixed at
    # source (`task_watchdog.detect_registry_vantage` no longer composes it
    # into any state); this row is the fence under the fix, and the F16 token
    # is `run_truth1_test`'s decoy.
    #
    # Scoped HARD on purpose. The hex run must be hex AND carry a digit, so
    # the ordinary product words a sentence really does say — `claude-code`
    # (`o` is not hex), `claude-face`, `claude-beta` (no digit) — are not
    # swept in, while every minted id is (the four characters are a hash
    # slice: the chance of four hex characters with no digit is under 7%, and
    # such a token is indistinguishable from a word by any rule).
    ("sandbox_session_id",
     r"\bclaude-(?=[0-9a-f]{4,}\b)(?=[0-9a-f]*[0-9])[0-9a-f]{4,}\b"),
]

# ---------------------------------------------------------------------------
# THE SUBSTRATE-PATH FAMILY — one source, two consumers (REVIEW_LEAK3 F-3).
#
# These three shapes are this product's own storage: a ledger file, a named
# data file, a path inside `_hq/`. LEAK3 6.1 widened them in the CHAT
# scanner and left `docx_leak_scanner` carrying its own older, narrower
# enumeration (`events\.jsonl`, `entities\.jsonl?`, `_hq/(data|.system|
# skills)`), so the widening never reached the `.docx` surface — and the
# weekly wrap, where the recorded `_hq/…` citation was actually seen, IS a
# `.docx`. Both scanners now read these rows; neither re-declares one.
#
# Each consumer applies its own flags, exactly as it did before: the chat
# renderer compiles IGNORECASE, the document scanner runs case-sensitive.
SUBSTRATE_PATH_PATTERNS: list[tuple[str, str, str]] = [
    # The named data files, `.json` or `.txt`.
    #
    # REVIEW_LEAK3 F-1 — this row said `\.(?:jsonl?|txt)` before LEAK3 and
    # `\.txt` after, so a bare `entities.json` / `events.json` /
    # `aliases.json` / `staging_emissions.json` RAISED at base and PASSED at
    # tip. That is recorded leak instance 11, and eleven real strings on the
    # book copy lost coverage. `.json` is back. The trailing `\b` cannot
    # match inside `.jsonl` (the `l` is a word character), so this row and
    # the `*.jsonl` row below stay disjoint and one `events.jsonl` is still
    # exactly one hit — the double-count the first cut correctly avoided.
    ("substrate_data_file_named",
     r"\b(events|entities|aliases|staging_emissions|known-newsletters)"
     r"\.(?:json|txt)\b",
     "internal data file"),
    # ANY `*.jsonl` filename, not an enumeration of five. `.jsonl` alone
    # (unlike bare `.json`, which a customer's own export can carry) is this
    # product's append-only-ledger extension.
    ("substrate_data_file_jsonl",
     r"\b[A-Za-z0-9_][A-Za-z0-9_.\-]*\.jsonl\b",
     "internal data file"),
    # Every `_hq/…` path except the one deliberate carve-out,
    # `_hq/meetings/`, where briefs save and the reader opens them.
    ("substrate_path_hq",
     r"\b_hq/(?!meetings/)[A-Za-z0-9_][A-Za-z0-9_.\-]*"
     r"(?:/[A-Za-z0-9_.\-]+)*",
     "internal _hq path"),
    # THE CARVE-OUT, NARROWED TO WHAT IT WAS CARVED OUT FOR (SPEC FIXTRAIN
    # v5.31.0 6.3, M's default ruling 5 of 2026-09-17).
    #
    # `_hq/meetings/` was allowed ANYWHERE, because that is where briefs save
    # and where the artifact link points. LEAK3 measured the cost of that and
    # recorded it as a stated finding rather than a bug: a bare
    # `Saved to _hq/meetings/Weekly_Wrap_<date>.docx` "passes the gate today
    # and will keep passing", because the scanner cannot tell a link from a
    # sentence. The v5.31.0 test then printed exactly that shape — the 09-15
    # 17:13 on-demand brief put two raw `_hq/meetings/Call_Prep_…docx` paths
    # in its "Today's preps" line (recorded leak instance 3), while the
    # scheduled brief the next morning printed none.
    #
    # So the allowance becomes a LINK TARGET rather than a string. This row
    # matches every `_hq/meetings/…` path and carries the SAME label as the
    # row above (one label, one finding name for the reader); the chat
    # scanner reads it against the link-blanked copy of the text, so a path
    # inside `](…)` or an `href` is exempt and the same path in visible text
    # refuses. That is the only form the product intends: the brief's own
    # template says the chips are links, and `absolutize_doc_links` is the
    # one chokepoint that converts them for the posted copy.
    #
    # The document scanner has no link spans to blank and registers this row
    # plainly, which is the right reading there: a `.docx` body carries
    # visible text, and the wrap footer's path is a relationship, not a
    # sentence.
    ("substrate_path_hq_meetings",
     r"\b_hq/meetings/[A-Za-z0-9_][A-Za-z0-9_.\-]*"
     r"(?:/[A-Za-z0-9_.\-]+)*",
     "internal _hq path"),
]

# The rows a CHAT surface reads against the link-blanked copy of the text —
# allowed inside a link target, refused in visible text. Named rather than
# labelled, because this row shares its label with the row above it on
# purpose: the reader of a refusal should see one finding name for one kind
# of path, not two. `chat_output_renderer` is the consumer; the document
# scanner deliberately ignores this set (it has no link spans to blank).
LINK_TARGET_EXEMPT_NAMES = frozenset({"substrate_path_hq_meetings"})

# name -> regex source, for a consumer that keeps the row in place in its
# own ordered list rather than appending the family at the end.
SUBSTRATE_PATH_SOURCES: dict = {
    name: src for name, src, _label in SUBSTRATE_PATH_PATTERNS
}


def substrate_path_patterns() -> list[tuple[str, str]]:
    """(name, regex-source) rows — `docx_leak_scanner`'s registration
    shape."""
    return [(name, src) for name, src, _label in SUBSTRATE_PATH_PATTERNS]


def substrate_path_leak_patterns():
    """(compiled, label) rows — `chat_output_renderer`'s registration
    shape."""
    return [(re.compile(src, re.IGNORECASE), label)
            for _name, src, label in SUBSTRATE_PATH_PATTERNS]


PLUMBING_VOCAB_PATTERNS: list[tuple[str, str]] = [
    # A script or module file name (D4: "script names").
    (r"\b[A-Za-z_][A-Za-z0-9_]*\.(?:py|sh)\b", "plugin script name"),
    (r"\bSKILL\.md\b", "plugin skill file"),
    # A path inside the plugin tree or the build's own folders (D4 / B3.5:
    # "file paths", "two folder paths"). A THIRD segment or a file extension
    # is required: the bare two-segment root (`shared/scripts`) is what every
    # code example in the shipped specs writes on its `sys.path` line, and
    # those literals never reach a reader — a path a customer could actually
    # be shown always goes deeper than the root.
    (r"\b(?:shared|skills|tests|hooks|references|build-specs|handoffs|"
     r"audit-reports)/[A-Za-z0-9_.\-]+"
     r"(?:/[A-Za-z0-9_.\-]+|\.[A-Za-z0-9]{1,5})",
     "plugin tree path"),
    # A spec / build / review document code (B3.5: "a spec code").
    (r"\b(?:SPEC|BUILD|REVIEW|HANDOFF|ATTENDED_TEST)_[A-Z0-9][A-Z0-9_]{2,}\b",
     "internal spec code"),
    # A lane code — LEAK2, ATTRIB1B, ONEPLATE1, HYGIENE9. Four or more caps
    # then a SINGLE ordinal digit, optionally a phase letter.
    #
    # REVIEW_LEAK2 F-2: the digit run was `\d{1,2}`, which ate `ROUTE66` and
    # `COVID19` — an all-caps brand and a year-suffixed acronym, both of
    # which a renderer may name in a sentence about a real client. The
    # build's own shape carries a single ordinal: every lane code in this
    # tree (`grep -ohE '\b[A-Z]{4,12}[0-9]{1,2}[A-Z]?\b' tests shared skills`,
    # 2026-09-07 — POLICY1, LIFECYCLE1, EODSYNTH1, ATTRIB1B, HYGIENE9,
    # PROV2, FLOOR3 …) is `<NAME><1-9>[A-Z]?`, and NOT ONE carries a
    # two-digit tail. Narrowing to that shape costs nothing real and hands
    # back both brand shapes. (The builder's record cited `ISO27001` as the
    # risk; that was wrong — three caps never matched. The shapes that fire
    # are 4+-cap brands and year-suffixed acronyms.)
    (r"\b[A-Z]{4,12}\d(?![\d])[A-Z]?\b", "internal lane code"),
    # A ship-gate guard id, only in its own phrase — bare `G4` is a seat
    # number in half the world's prose.
    (r"\b(?:guard\s+G\d{1,3}|G\d{1,3}\s+guard)\b", "internal guard id"),
    # The test battery, by name or by score (B3.5: "battery numbers").
    # REVIEW_LEAK2 F-1: the `test ` prefix was OPTIONAL, so the BARE NOUN was
    # a leak — "order a new battery for the van" was refused on every chat
    # surface. "Battery" is ordinary English and a fleet, parts or energy
    # client writes it constantly; what is plumbing is the BUILD's phrasing
    # of it — a TEST battery, a battery OF SUITES, a battery that RAN GREEN
    # (or ran a score). Those three shapes are anchored here and the bare
    # noun is not one of them. All 20 occurrences on the 2026-09-07 book
    # snapshot are the CI battery and every one still matches.
    (r"\b(?:test|regression|guard|CI|nightly|ship[- ]gate)\s+batter(?:y|ies)\b"
     r"|\bbatter(?:y|ies)\s+of\s+(?:tests?|suites?|checks?|guards?)\b"
     r"|\bbatter(?:y|ies)\s+(?:ran|runs|is|was|went|came\s+back)\s+"
     r"(?:\d|green\b|red\b|clean\b)",
     "internal test vocabulary"),
    (r"\b\d+\s*/\s*\d+\s+(?:tests?|checks?|suites?|guards?|green|passing)\b",
     "internal test vocabulary"),
    (r"\brun_all\b", "internal test vocabulary"),
    # The battery's own command-line FLAG. REVIEW_LEAK2 round-2 R-1
    # asked for "tier guard / tier unit" to refuse. The BARE words
    # cannot — "tier one support is included" is a real MSP sentence
    # and "the guard tier came back green" is ordinary English — but
    # the flag can: no customer prose contains `--tier`. Narrow on
    # purpose, the flag and not the word.
    #
    # LEAK3 6.1 — widened from `--tier` alone to ANY CLI flag. The record's
    # leak instance 3 (Maintenance Run Now: raw seq, `--apply`, `root_repair`)
    # leaked a DIFFERENT flag than the one this pattern covered, and the
    # build carries many (`--apply`, `--force`, `--dry-run`, `--tier`…).
    # OVER-BLOCK GUARD: an informal double-hyphen dash glued to the previous
    # word ("wait--immediately") is real customer prose and must not refuse,
    # so the "--" must be preceded by whitespace or start-of-string — the
    # shape an actual argv token has (a space before it on the command
    # line). Case-sensitive on purpose (no IGNORECASE on this compile): a
    # renderer never emits `--Apply`.
    (r"(?:^|(?<=\s))--[a-z][a-z0-9-]{1,30}\b", "internal CLI flag"),
    # LEAK3 6.1 — dotted internals: `module.function` where `module` is any
    # file under `shared/scripts/` (`DOTTED_INTERNAL_MODULES`, built at
    # import — see `_discover_shared_script_modules` above). Leak instance
    # 10 (End of Day traceback) named `brain_undo.undo_after_reprocess`;
    # instance 11 named `brain_undo.undo_batch` and `load_open_commitments`
    # (the bare-identifier half, below). NO space between module, dot and
    # function — that shape does not occur in ordinary prose (a sentence
    # ending "...see the profile." followed by a new sentence always has a
    # space before the next capitalized word; this pattern requires a
    # lowercase identifier with NO space immediately after the dot).
    #
    # REVIEW_LEAK3 F-2 — the right-hand side used to be ANY lowercase
    # identifier, and a dozen module stems are ordinary English nouns, so
    # "I attached brand.pdf" / "she sent receipts.zip" were REFUSED
    # outright (eleven of twelve ordinary attachment sentences; the surface
    # goes down, it is not softened). The right-hand side must now be a name
    # the named module really defines — `brain_undo.undo_batch` and
    # `plate_view.plate_numbers` are real and still refuse; `brand.pdf` is
    # not a name `brand.py` carries and posts clean. File extensions are
    # excluded from every attribute set on top of that
    # (`DOCUMENT_EXTENSIONS`), so the fix does not depend on no module ever
    # defining something called `csv`.
    (_dotted_internal_source(), "internal module reference"),
    # LEAK3 6.1 — the bare-identifier half of the same roster item: snake_case
    # names and hyphenated job/doc ids that are this product's own vocabulary
    # even with no dot (leak instances 2, 6, 10, 11: `n_deduped`,
    # `dormancy_signal`, `past-meetings`, `load_open_commitments`). Word-
    # bounded so `n_deduped` cannot match inside a longer identifier a
    # customer might type, and `past-meetings` needs the whole hyphenated
    # token including the boundary the hyphen itself creates.
    (r"\b(?:" + "|".join(re.escape(w) for w in
                          sorted(GENERATED_VOCAB_ROSTER, key=len, reverse=True))
     + r")\b", "internal vocabulary roster")
    if GENERATED_VOCAB_ROSTER else (r"(?!)", "internal vocabulary roster"),
    # FIXTRAIN v5.31.0 6.5 — "the <identifier> key", the shape recorded leak
    # instance 11 took. The reasoning is beside `_SETTING_KEY_NAMED_SRC`.
    (_SETTING_KEY_NAMED_SRC, "internal vocabulary roster"),
    # ...and the same identifier ASSIGNED (`parse_status=OK`), which is that
    # instance's parser-status half and carries no mechanic noun at all.
    (_SETTING_KEY_ASSIGNED_SRC, "internal vocabulary roster"),
]

# ---------------------------------------------------------------------------
# THE SERVING MODEL ID — TZ1, night M2 fix round 1 (review M-4).
#
# TZ1 made receipts carry `data.model` when the environment names the serving
# model (`receipts.model_fields`, M's ruling §0.6). It is ledger vocabulary:
# "which model served this fire?" is a question your own records should
# answer and a sentence should never say. The lane's only fence was a SOURCE
# SCAN over 33 of 269 modules, and the reviewer walked straight round it —
# the identical model read planted into `task_alarm` and into
# `commitment_backlog_sweep`, both customer-sentence emitters, left the suite
# green both times. These rows are the RUNTIME fence: `validate_chat_output`
# raises wherever the sentence is composed, so the population is every
# surface there is instead of a list someone remembered to widen.
#
# WHY THEY SIT IN THEIR OWN BLOCK DOWN HERE rather than inside the two lists
# above. This file belongs to another lane's row and that lane is closed, so
# TZ1 lands the rows with the reason written — and a second writer's rows in
# the middle of a list a third writer is also appending to is a merge
# conflict by construction. Appended, folded in by the readers below, so any
# order of merges is a union. Fold them into any new reader you add here.
#
# DISJOINT FROM `sandbox_session_id` on purpose. That row is `claude-` plus a
# hex run carrying a digit, scoped hard so `claude-code` and `claude-beta`
# stay out. A model id is a different shape and none of it is hex: a FAMILY
# word then an ordinal, with or without the vendor prefix. Three alternatives
# inside ONE group, so the `_NOT_IN_URL` carve-out the docx scanner prepends
# binds to all three and not only to the first:
#   1. `claude-opus-5-20260101`, `claude-fable-5-1`, `claude-sonnet-4-5`, and
#      the bare `opus-5` a sentence writes when it drops the vendor.
#   2. any future `claude-<family>-<ordinal>` — the families are not a closed
#      set, and a row that must be widened per release is a row that is out
#      of date at the next one.
#   3. the harness's own agent id, `claude-code_2-1-275_agent` (`AI_AGENT`,
#      read off a live shell 2026-09-20), which the hex rule cannot see and
#      which names a build of this product, not a model.
# A DIGIT IS REQUIRED in every branch, so the ordinary product words
# (`claude-code`, `Claude Code`) are untouched, and a bare `Opus 5` with a
# space is not a model id and is not matched.
MODEL_ID_SURFACE_PATTERNS: list[tuple[str, str]] = [
    ("serving_model_id",
     r"(?:\b(?:claude-)?(?:opus|sonnet|haiku|fable)-\d+(?:[.\-]\d+)*\b"
     r"|\bclaude-[a-z]{3,12}-\d+(?:[.\-]\d+)*\b"
     r"|\bclaude-code[_\-][0-9][0-9._\-]*[_\-]agent\b)"),
]

# The other half of the same leak: a sentence that does not print the value
# but names the plumbing holding it ("the model field on the receipt"). That
# is the build's own words, so it is chat-only and user-text-blanked like
# every other vocabulary row — an operator writing their own note about this
# product is quoting their own work, a renderer doing it is leaking.
MODEL_ID_VOCAB_PATTERNS: list[tuple[str, str]] = [
    (r"\bdata\.model\b"
     r"|\bmodel\s+field\s+(?:on|of)\s+(?:the|a)\s+receipt\b"
     r"|\breceipt(?:'s)?\s+model\s+field\b",
     "internal receipt field"),
]


# The labels that stop reading user-authored spans, for
# `chat_output_renderer.USER_TEXT_BLANKED_LABELS` to fold in. Every one is
# this product's own vocabulary; the opaque-id half is deliberately absent.
PLUMBING_VOCAB_LABELS = frozenset(
    label for _src, label in PLUMBING_VOCAB_PATTERNS + MODEL_ID_VOCAB_PATTERNS)


def surface_id_patterns() -> list[tuple[str, str]]:
    """(name, regex-source) rows — `docx_leak_scanner`'s registration shape.
    The always-scanned half only, each carrying the character-class URL
    carve-out (that scanner reads a document, not a rendered page, and has
    no span machinery)."""
    return [(name, _NOT_IN_URL + src)
            for name, src in SURFACE_ID_PATTERNS + MODEL_ID_SURFACE_PATTERNS]


def surface_id_leak_patterns():
    """(compiled, label) rows — `chat_output_renderer`'s registration shape.
    The always-scanned half only, WITHOUT the character-class carve-out: the
    chat scanner blanks whole URL spans before these patterns read the text
    (REVIEW_LEAK2 F-7), which is both wider (a link's vocabulary is exempt
    too) and tighter (`ref-18f3a9c7d2e14b60` no longer escapes)."""
    return [(re.compile(src, re.IGNORECASE), "opaque-id leak")
            for _name, src in SURFACE_ID_PATTERNS + MODEL_ID_SURFACE_PATTERNS]


def plumbing_vocab_leak_patterns():
    """(compiled, label) rows — `chat_output_renderer`'s registration shape
    for the build's own words. Chat/widget surfaces only, and behind the same
    URL-span carve-out (REVIEW_LEAK2 F-5: a code-citing `Sources:` link named
    a `.py` file and the whole line was refused)."""
    return [(re.compile(src), label)
            for src, label in PLUMBING_VOCAB_PATTERNS + MODEL_ID_VOCAB_PATTERNS]



# ---------------------------------------------------------------------------
# The DIAGNOSIS class (LEAK4), from the 2026-09-20 M1+M2 gate walk
# ---------------------------------------------------------------------------
#
# Three trailers on three customer surfaces, all the same shape: a clean
# surface renders, and then the chat EXPLAINS ITSELF underneath it. "The
# `show-my-list` skill's preamble runs awk over the folder list and the app
# rewrote the argument token…"; "your events.jsonl under _hq/data/ is
# drive-synced, so…"; "the workspace resolves through a session-scoped mount,
# so a computer:// link would fail". Two of the three came after a surface that
# was already right, which is what makes this a class rather than three
# mistakes: the turn backstop scanned the widget and never read the paragraph
# under it.
#
# CONTRACT Rule 4 and CHAT_ACTION_WIDGET already FORBID commentary after the
# widget, and the validator already refuses some of these tokens. What it did
# not do was name them as one thing, so a finding could not say "the chat is
# diagnosing the product at a customer". The family below is that name.
#
# EVERY PATTERN IS NARROW ON PURPOSE. `find` is an English verb, `mount` is
# something you put a screen on, and a skill directory name is a hyphenated
# phrase a customer could write. So `find` counts only with a flag or a path
# behind it, `mount` only within a clause of the word workspace, and a skill
# name only IN BACKTICKS, which is the shape of a chat naming an internal thing
# rather than of a customer's own words.


def _instruction_layer_names():
    """`(skill directory names, shipped script basenames)` from the GENERATED
    constant beside this module, or two empty sets.

    IT IS A CONSTANT BECAUSE THE STAGED RUNTIME HAS NO `skills/` (re-verifier
    H-1). This used to list `<plugin>/skills/` at import, and
    `workspace_access.runtime_file_set` says of the staged runtime, in terms,
    "NOT `skills/` — ever". So on a merged seat the helper child ran with an
    EMPTY set, the skill-name pattern degraded to one that can never match,
    and the 2026-09-20 walk's own first trailer passed clean on exactly the
    seat it happened on. `shared/scripts/` IS staged, so a constant here
    travels; `scripts/dev/gen_instruction_layer_names.py` writes it and guard
    G71 reds when it disagrees with the tree.
    """
    try:
        from instruction_layer_names import (SHIPPED_SCRIPT_NAMES,
                                             SKILL_DIRECTORY_NAMES)
        return SKILL_DIRECTORY_NAMES, SHIPPED_SCRIPT_NAMES
    except Exception:  # pragma: no cover — defensive only
        return frozenset(), frozenset()


SKILL_DIRECTORY_NAMES, SHIPPED_SCRIPT_NAMES = _instruction_layer_names()


#: A word that turns a name into a DIAGNOSIS. A skill's directory name is
#: also a trigger phrase this product offers ("Try `end-of-day` when you are
#: wrapping up"), so the name alone cannot tell an offer from an explanation -
#: `end-of-day` is a skill directory AND a thing anybody says (re-verifier,
#: LOW). What the walk's trailer had, and an offer never does, is the
#: MECHANISM beside it: "the `show-my-list` skill's preamble runs awk". So the
#: name counts when one of these sits within a clause of it, either side.
_MECHANISM_WORDS = (
    "skill", "preamble", "module", "handler", "resolver", "renderer",
    "validator", "scanner", "composer", "orchestrator", "helper", "script",
    "pattern", "prompt", "template", "codebase", "repo", "source",
)


def _skill_name_pattern() -> str:
    """A HYPHENATED skill directory name inside backticks, BESIDE a mechanism
    word.

    TWO NARROWINGS, BOTH FROM REAL SENTENCES (re-verifier M-5). A skill's
    directory name is also a trigger phrase the product OFFERS as a next step,
    and offering it in backticks is the ordinary shape — "Say `cleanup` when
    you want me to tidy the workspace", "`boardroom` gives you the full pack".
    Half these names are ordinary English words (`cleanup`, `balance`,
    `research`, `boardroom`, `objectives`), so the hyphen is the
    discriminator: `show-my-list` and `commitment-triage` are the product's
    own spelling of an internal thing and nothing a customer writes by
    accident, which is what the walk's trailer used. A one-word trigger in
    backticks is an offer, not a diagnosis.

    An empty set yields a pattern that can never match, never an empty
    alternation — which would match at every position and refuse everything.
    """
    names = sorted((n for n in SKILL_DIRECTORY_NAMES if "-" in n),
                   key=len, reverse=True)
    if not names:
        return r"(?!x)x"
    quoted = r"`(?:" + "|".join(re.escape(n) for n in names) + r")`"
    mech = r"\b(?:" + "|".join(_MECHANISM_WORDS) + r")s?\b"
    return (quoted + r"[^.!?\n]{0,40}" + mech
            + r"|" + mech + r"[^.!?\n]{0,40}" + quoted)


def _shipped_script_pattern() -> str:
    """A `.py` or `.sh` basename THIS PRODUCT SHIPS.

    A customer quoting their own file — "he sent over `reconcile.py` to look
    at" — is their business and not a diagnosis of ours (re-verifier M-5).
    The set is the generated constant, so it is the same list on the staged
    runtime as on the tree.
    """
    names = sorted(SHIPPED_SCRIPT_NAMES, key=len, reverse=True)
    if not names:
        return r"(?!x)x"
    return r"(?<![A-Za-z0-9_])(?:" + "|".join(
        re.escape(n) for n in names) + r")(?![A-Za-z0-9_])"


# ---------------------------------------------------------------------------
# The DEVICE MARKER family (FIX3 F3-3, from the re-walk of 2026-09-21)
# ---------------------------------------------------------------------------
# `what accounts do I have` asked which of the reader's mail addresses this
# product treats as work. The answer opened with the reader's own computer by
# its short device name, its operating system, and how many folders were shared
# to the task - the harness's inventory of itself, on a surface that was asked
# about mail. It is not an id and not a path, so no family on this list saw it.
#
# The discriminator is the MARKER, never the word: a customer's own sentence
# about their computer ("your computer is asleep", "I left it on my laptop") is
# ordinary English and stays green. What refuses is the harness's own shape -
# a `Computer:` line label, a device named in quotes, an operating system
# inside a clause about a device or computer, the grant sentence, and the
# grant's own field name.
DEVICE_MARKER_PATTERNS: list = [
    (r"^\s*(?:[-*>]\s*)?\*{0,2}Computer\*{0,2}\s*:",
     "device marker: a Computer line label"),
    (r"\bdevice\s+[\"'“‘]", "device marker: the device by name"),
    (r"\b(?:device|computer)\b[^.!?\n]{0,60}"
     r"\((?:Windows|macOS|Mac ?OS|Linux)\)"
     r"|\((?:Windows|macOS|Mac ?OS|Linux)\)[^.!?\n]{0,60}"
     r"\b(?:device|computer)\b",
     "device marker: the operating system beside a device"),
    (r"\bshared to this task\b|\bthis task can see\b",
     "device marker: what this task can see"),
    (r"\bconnectedFolders\b", "device marker: the folder-grant field"),
]

DEVICE_MARKER_LABELS: frozenset = frozenset(
    label for _src, label in DEVICE_MARKER_PATTERNS)


def device_marker_leak_patterns():
    """(compiled, label) rows — the DEVICE MARKER class, for every registrar."""
    return [(re.compile(src, re.IGNORECASE), label)
            for src, label in DEVICE_MARKER_PATTERNS]


# ---------------------------------------------------------------------------
# The DEAD POINTER family (FIX3 F3-4, from the re-walk of 2026-09-21)
# ---------------------------------------------------------------------------
# The prep landed correctly, on the right day, and the chat named it
# `→ Call Prep — Sam Sample`: an arrow, a document title, and no target. Not
# a session link and not a breadcrumb, so every existing family read it clean,
# and the reader had nothing to click and nothing to look for.
#
# Three shapes refuse. A line that POINTS at a document and names no place to
# find it; a `›` used as a path separator (a breadcrumb that reads like a path
# and is not one); and a paraphrase of the one sanctioned "Saved under …"
# sentence, which exists precisely so nobody writes their own.
#
# What stays green is pinned beside them: call-prep's own multi-attendee
# talking points legitimately begin `→ <FirstName>:`, and a markdown link whose
# target is the opener's own scheme is the correct surface, not a leak.
_DOC_TITLE = (r"(?:Call Prep|Prep —|Prep -|Board Pack|One-Pager"
              r"|[\w .()-]+\.(?:docx|pptx|xlsx|html|htm|pdf))")

DEAD_POINTER_PATTERNS: list = [
    # An arrow line pointing at a document, with no href and no absolute path
    # anywhere on it. The `](` lookahead is what keeps a real link green; the
    # drive-letter and leading-slash lookaheads keep a spelled-out path green.
    (r"^(?!.*\]\()(?!.*[A-Za-z]:[\\/])(?!.*(?:^|\s)/)"
     r"\s*(?:→|->|›|»)\s*(?!.{0,20}:)"
     r".*" + _DOC_TITLE,
     "dead pointer: an arrow at a document with no target"),
    # A breadcrumb built out of `›`: two or more of them on one line, or one
    # with a filename behind it.
    (r"›[^›\n]{1,60}›"
     r"|›\s*[\w .()-]+\.(?:docx|pptx|xlsx|html|htm|pdf)\b",
     "dead pointer: a breadcrumb standing in for a path"),
    # A paraphrase of the one sanctioned sentence. The constant reads
    # "Saved under _hq/meetings/<file> in your Command Room folder."; anything
    # else that opens "Saved under" is somebody's own version of it.
    (r"\bSaved under\b(?!\s+_hq/)",
     "dead pointer: a paraphrase of the saved-location sentence"),
    # IDENT1 I-3 (R-RW2-4). A `Saved to` sentence whose drive-letter path is
    # NOT inside a code span. That bare form is what the app's markdown eats
    # (a backslash before an underscore renders as nothing), so the path the
    # customer sees opens nothing; the one sanctioned form puts the path in
    # backticks.
    (r"\bSaved to\s+(?!`)[A-Za-z]:[\\/]",
     "dead pointer: a saved-to path outside a code span"),
]

DEAD_POINTER_LABELS: frozenset = frozenset(
    label for _src, label in DEAD_POINTER_PATTERNS)


def dead_pointer_leak_patterns():
    """(compiled, label) rows — the DEAD POINTER class, for every registrar."""
    return [(re.compile(src, re.IGNORECASE), label)
            for src, label in DEAD_POINTER_PATTERNS]


#: IDENT1 I-4 (ruling R-RW2-6; the 2026-09-22 re-walk, HOLD driver 3). A
#: scheduled fire's own working notes, said to the customer. The fire printed
#: five of these ABOVE the widget and two more after it - the run mode, the
#: orchestrator it was reading, the step it was on, the receipt writes it was
#: making. Every one is plain English with no token in it, which is why no
#: other family caught them: the class is NARRATION, and it has its own
#: family so a finding names it. Line-shaped on purpose (an opener, or a
#: phrase only a fire's self-talk contains) - the ordinary sentence "Your mail
#: wasn't read this time." stays legal.
FIRE_NARRATION_PATTERNS: list = [
    (r"^\s*Run mode\s*:", "fire narration: the run mode"),
    (r"\bReading (?:the )?orchestrator\b", "fire narration: reading the orchestrator"),
    (r"\bI must execute\b", "fire narration: the instructions, restated"),
    (r"^\s*Now (?:render|the|persist)\b", "fire narration: the step it is on"),
    (r"\bSilent receipt writes?\b", "fire narration: the receipt writes"),
    (r"\bPersist the page\b", "fire narration: the step it is on"),
    (r"\bThat flag is logged\b", "fire narration: the flag it wrote"),
]

FIRE_NARRATION_LABELS: frozenset = frozenset(
    label for _src, label in FIRE_NARRATION_PATTERNS)


#: IDENT1 I-12 (vii). A connector's or the harness's tool id on a customer
#: line. Probe B's stop message named the widget tool by its id and was pushed
#: to M's phone; a tool id is plumbing on every surface, the push included.
TOOL_ID_PATTERNS: list = [
    (r"mcp__[A-Za-z0-9_-]+__", "tool id: a connector or harness tool named"),
]

TOOL_ID_LABELS: frozenset = frozenset(label for _src, label in TOOL_ID_PATTERNS)


def tool_id_leak_patterns():
    """(compiled, label) rows - the TOOL ID class (IDENT1 I-12)."""
    return [(re.compile(src), label) for src, label in TOOL_ID_PATTERNS]


#: IDENT1 I-16 (M 2026-09-23; the app's own template rule). A scheduled fire
#: runs with nobody there: it never asks a question, never proposes
#: connecting, reconnecting or re-pairing anything, never offers a choice. What
#: it cannot do it records as a flag for the next interactive chat. Applied by
#: `validate_chat_output` ONLY when the caller says the turn is a fire's
#: (`fired_via` scheduled / catchup) - an interactive chat asks all it likes.
UNATTENDED_ASK_PATTERNS: list = [
    (r"\?\s*$", "unattended ask: a question"),
    (r"\bwould you like\b", "unattended ask: an offer"),
    (r"\bdo you want\b", "unattended ask: an offer"),
    (r"\bshall I\b", "unattended ask: an offer"),
    (r"\blet me know\b", "unattended ask: a request for an answer"),
    (r"\breply with\b", "unattended ask: a request for an answer"),
    (r"^\s*reply\b", "unattended ask: a request for an answer"),
    (r"\bsay yes\b", "unattended ask: a request for an answer"),
    (r"\bconnect your\b", "unattended ask: a connector suggestion"),
    (r"\breconnect", "unattended ask: a connector suggestion"),
    (r"\bre-?pair", "unattended ask: a connector suggestion"),
    (r"\bre-?pin", "unattended ask: a connector suggestion"),
    (r"\bauthori[sz]e\b", "unattended ask: a connector suggestion"),
    (r"\bgrant access\b", "unattended ask: a connector suggestion"),
]

UNATTENDED_ASK_LABELS: frozenset = frozenset(
    label for _src, label in UNATTENDED_ASK_PATTERNS)

#: The run modes the family applies to.
UNATTENDED_FIRED_VIA = ("scheduled", "catchup")


#: DOCS1 D-3 (2026-09-24; the unattended rule extended to documents). A fire
#: never produces a document anywhere but the folder - no Claude Doc, no page,
#: no slide deck. A customer line on a fire that offers or names one is a
#: leak of the same class as an ask: it points the customer at hosted storage
#: the workspace cannot see. Applied by `validate_chat_output` on a fire's
#: turn only, beside the ask family. The ONE legal page link on a fire is the
#: republished Artifact of a page ALREADY landed in the folder (R-RW2-9),
#: rendered from `fire_delivery.ARTIFACT_LINE` - exempt by IDENTITY (the line
#: is that constant with a URL), never by wording.
UNATTENDED_DOC_PATTERNS: list = [
    (r"\bI (?:have )?(?:created|made|wrote|published|opened) (?:a|the|your) (?:Claude )?(?:doc|document|page|slide deck|deck)\b",
     "unattended doc: a document made outside the folder"),
    (r"\bopen the (?:Claude )?doc\b", "unattended doc: a doc offered"),
    (r"\bClaude Docs?\b", "unattended doc: Claude Docs named"),
    (r"docs\.claude", "unattended doc: a docs link"),
    (r"/artifact/", "unattended doc: a page link outside the folder"),
    (r"\bin (?:a|your) (?:Claude )?(?:doc|slide deck)\b", "unattended doc: a doc named"),
]

UNATTENDED_DOC_LABELS: frozenset = frozenset(
    label for _src, label in UNATTENDED_DOC_PATTERNS)


def unattended_doc_leak_patterns():
    """(compiled, label) rows - the UNATTENDED DOC class (DOCS1 D-3)."""
    return [(re.compile(src, re.IGNORECASE), label)
            for src, label in UNATTENDED_DOC_PATTERNS]


def artifact_line_exempt(line: str) -> bool:
    """True when `line` IS the fire-delivery page line rendered with a URL -
    `fire_delivery.ARTIFACT_LINE` by identity: the constant's prefix and one
    URL and nothing else. A reworded line that also carries a page link is
    read like any other."""
    text = str(line or "").strip()
    if text.startswith("> "):
        text = text[2:].strip()
    try:
        import fire_delivery as _fd  # noqa: WPS433 - lazy; the constant lives there

        template = _fd.ARTIFACT_LINE
    except Exception:  # noqa: BLE001
        return False
    prefix = template.split("{url}", 1)[0]
    if not prefix or not text.startswith(prefix):
        return False
    rest = text[len(prefix):].strip()
    return bool(rest) and " " not in rest and rest.startswith("https://")


def unattended_ask_leak_patterns():
    """(compiled, label) rows - the UNATTENDED ASK class (IDENT1 I-16)."""
    return [(re.compile(src, re.IGNORECASE), label)
            for src, label in UNATTENDED_ASK_PATTERNS]


#: MF-23 (Night M3 merged-tree review F-8; I-16 (2)): the scheduled-task
#: bootloader's six quoted abort messages, byte for byte as the template
#: carries them with `<TASK_ID>` unrendered (a pin holds the template to these
#: constants). A fire that stops posts one verbatim; each tells the customer
#: what to say in an interactive chat and waits on nothing, so the
#: unattended-ask family exempts them by IDENTITY - rendered for every task id
#: the registry knows, with and without the blockquote marker. A reworded copy
#: is read like any other line.
BOOTLOADER_ABORT_MESSAGES: tuple = (
    "⚠️ Command Room's scheduled chat `<TASK_ID>` could not find the Command Room plugin in this run, so it did nothing. Open a new chat and say `set up command room schedules`; this chat will work next time once the plugin is reachable.",
    "⚠️ Command Room's scheduled chat `<TASK_ID>` found the plugin but not its instructions for this chat — usually an update still settling. Open a new chat, say `what's new in command room`, then `set up command room schedules`.",
    "⚠️ Command Room's scheduled chat `<TASK_ID>` could not tell which environment it is running in, so it did nothing. Open a new chat and say `health check`.",
    "⚠️ Command Room's scheduled chat `<TASK_ID>` could not reach your workspace folder from this run, so it did nothing. Open a new chat with your Command Room folder attached and say `set up command room schedules` to re-bind.",
    '⚠️ Command Room scheduled task `<TASK_ID>` can\'t confirm your workspace registration. Your workspace folder doesn\'t match what\'s on record — it may have moved or been renamed, and I found more than one folder (or none) that could be it, so I won\'t guess. Please open Command Room and say "set up command room schedules" to reconnect it. This task will work again once the registration is confirmed.',
    "⚠️ The orchestrator file for `<TASK_ID>` exists but doesn't contain the canonical OUTPUT CONTRACT marker. The plugin may be partially installed or corrupted. Please reinstall Command Room and type `set up command room schedules`.",
)


def bootloader_abort_lines() -> frozenset:
    """Every rendering of `BOOTLOADER_ABORT_MESSAGES` a fire can post: each
    message with `<TASK_ID>` replaced by each task id the schedule registry
    names (live, silent, retired, display rows), bare and blockquoted."""
    try:
        import schedule_config as sc  # noqa: WPS433 - lazy, like inbox_helpers below

        ids = (set(sc.DEFAULT_SCHEDULES) | set(sc.SILENT_TASKS)
               | set(sc.RETIRED_TASKS) | set(sc.DISPLAY_NAMES))
    except Exception:  # noqa: BLE001
        return frozenset()
    out = set()
    for message in BOOTLOADER_ABORT_MESSAGES:
        for task_id in ids:
            line = message.replace("<TASK_ID>", task_id)
            out.add(line)
            out.add("> " + line)
    return frozenset(out)


def unattended_ask_exempt_lines() -> frozenset:
    """The fixed lines the family never reads - exempt by IDENTITY, never by
    wording: the text surface's one draft affordance, and the bootloader's
    abort messages (MF-23). A fire that composes its own version of either is
    refused like any other ask."""
    lines = set(bootloader_abort_lines())
    try:
        import inbox_helpers  # noqa: WPS433 - lazy: that module imports this one's family

        lines.add(inbox_helpers.DRAFT_HINT_LINE)
    except Exception:  # noqa: BLE001
        pass
    return frozenset(lines)


def fire_narration_leak_patterns():
    """(compiled, label) rows - the FIRE NARRATION class (IDENT1 I-4)."""
    return [(re.compile(src, re.IGNORECASE), label)
            for src, label in FIRE_NARRATION_PATTERNS]


DIAGNOSIS_PATTERNS: list = [
    # A script THIS PRODUCT ships, by name. The walk's trailer named the
    # propagation script by filename, in a sentence to a customer.
    (_shipped_script_pattern(), "diagnosis: a script name"),
    # A folder inside the plugin, or one of its shared protocol documents.
    (r"\b(?:scripts/dev|shared/scripts|skills|tests|hooks|references)/",
     "diagnosis: a plugin folder"),
    (r"\bshared/[A-Z][A-Z0-9_]+\.md\b", "diagnosis: a shared protocol file"),
    (r"\bSKILL\.md\b", "diagnosis: the skill file"),
    # A shell command word. `awk` and `sed` are not English words; `find` is,
    # so it counts only with something behind it that makes it a command.
    (r"\b(?:awk|sed)\b", "diagnosis: a shell command"),
    (r"\bfind\s+(?:-[a-z]|/|\"/|\$|\"\$)", "diagnosis: a shell command"),
    # The substrate, named as a MECHANISM rather than as a place a document
    # went. `_hq/meetings/` stays legal (Rule 4 allows it; R-GW-1 rules a
    # plain folder name in an honest sentence cosmetic); the ledger and the
    # anchor files are the walk's second trailer, which explained drive sync
    # and write contention to a customer who asked about mail accounts.
    (r"\b(?:events\.jsonl|entities\.json|aliases\.json)\b",
     "diagnosis: a substrate file"),
    (r"_hq/(?:data|staging|views|tmp)/", "diagnosis: a substrate folder"),
    # The substitution token this round exists to remove, inside a code span.
    # A BARE digit only: `$0` and `$1` are awk's, while `$45K` and `$1,250`
    # are money and R-FIX-1 says a currency shape is counted, never a finding
    # — a rule that has to hold in this validator as well as in G70
    # (re-verifier M-5).
    # `$0` in a code span is awk's whole record and the token the walk
    # proved the app rewrites. `$1`-`$9` count only inside BRACES - the
    # awk-block shape `{$1=""}` - because a lone `` `$1` `` or
    # `` `$1,250` `` in a code span is money, and R-FIX-1 says money is
    # counted and never a finding (re-verifier, LOW).
    (r"`[^`\n]*\$0\b[^`\n]*`|\{[^}\n]*\$0\b[^}\n]*\}"
     r"|\{[^}\n]*\$[1-9]\b[^}\n]*\}",
     "diagnosis: an argument token"),
    (r"\bsession-scoped\b", "diagnosis: the session mount"),
    # The mount as a MECHANISM the customer is being told about - "the
    # workspace resolves through a session-scoped mount" - and not the
    # ordinary passive "your workspace folder is mounted on this machine",
    # which is an honest sentence and was refused by the first cut
    # (re-verifier M-5). The discriminator is the NOUN: a determiner in
    # front of it, or "mount point" / "mount path". "We mounted the new
    # screen" has no workspace in the clause and never reached it.
    (r"\bworkspace\b[^.!?\n]{0,60}"
     r"\b(?:a|an|the|this|that|its|one)\s+(?:[a-z-]+\s+){0,2}mounts?\b"
     r"|\b(?:a|an|the|this|that|its|one)\s+(?:[a-z-]+\s+){0,2}mounts?\b"
     r"[^.!?\n]{0,60}\bworkspace\b"
     r"|\bmount\s+(?:point|path)\b",
     "diagnosis: the session mount"),
    # IDENT1 I-12 / I-14: the harness's own machinery, explained to the
    # customer - Probe B's push ("the widget-rendering tool it needs isn't
    # available in this session") reached M's phone in exactly this shape.
    (r"\bwidget[- ]rendering tool\b", "diagnosis: the harness's own machinery"),
    (r"\btools?\b[^.\n]{0,60}\b(?:isn't|is not|not) available in this session\b",
     "diagnosis: the harness's own machinery"),
    (r"\bsession[- ]configuration\b", "diagnosis: the harness's own machinery"),
    (r"computer://", "diagnosis: an internal link scheme"),
    (r"\brenderer-side\b", "diagnosis: a rendering internal"),
    (r"\bvisual_gate_preview\b", "diagnosis: a plugin working directory"),
    (_skill_name_pattern(), "diagnosis: a skill directory name"),
]


def diagnosis_leak_patterns():
    """(compiled, label) rows — the DIAGNOSIS class, for every registrar.

    THE HONEST LIMIT, written where it belongs: this catches what passes
    THROUGH the validator. What stops the model writing the paragraph at all is
    rule 6 of the Access preamble, and what removed the reason it was writing
    them is the argument-token fix in the same round.
    """
    return [(re.compile(src, re.IGNORECASE), label)
            for src, label in DIAGNOSIS_PATTERNS]


DIAGNOSIS_LABELS: frozenset = frozenset(
    label for _src, label in DIAGNOSIS_PATTERNS)


LEAK2_LABELS = frozenset(PLUMBING_VOCAB_LABELS | {"opaque-id leak"})

_ALL_ID_RE = re.compile(
    "|".join(_NOT_IN_URL + src for _name, src
             in SURFACE_ID_PATTERNS + MODEL_ID_SURFACE_PATTERNS),
    re.IGNORECASE)

# The plugin-minted entity-id shape, kept here too so ONE call answers "is
# this string an id rather than a name" for every composer that has a
# display name to fall back on (`narration_names.safe_name`).
_ENTITY_ID_RE = re.compile(
    r"\b(person|project|org|thread|event|matter|engagement)_\d{3,}\b",
    re.IGNORECASE)

# REVIEW_LEAK3 F-6 — the SHORT numeric tail. The floor unification closed
# the 6-9 character band (`cmt_016123` used to read clean and now refuses),
# but `cmt_016` and `seq_86` sat under every floor and passed the gate, both
# id predicates and `narration_names.safe_name()`. A shipped id prefix
# followed by an underscore and digits is a wire id at ANY length — there is
# no shorter, innocent reading of `cmt_016`.
#
# OVER-BLOCK GUARD: the shape is `<prefix>_<digits>` and nothing else.
# Ordinary words that carry digits have no underscore before them, so "Q3",
# "v5.31.0", "2pm" and "A-Z" are untouched, and the leading `\b` cannot
# match inside `commitment_seq_86` (an underscore is a word character), so
# the longer `commitment_seq_N` shape is still exactly one hit.
NUMERIC_TAILED_ID_PREFIXES = ("cmt", "seq", "person", "project", "org", "deal")
NUMERIC_TAILED_ID_SRC = (
    r"\b(?:" + "|".join(NUMERIC_TAILED_ID_PREFIXES) + r")_\d+\b")
_NUMERIC_TAILED_ID_RE = re.compile(NUMERIC_TAILED_ID_SRC, re.IGNORECASE)

# LEAK3 6.1 — the `cmt_` minimum-length floor. Two callers carried two
# different numbers (this module's `_WIRE_ID_RE` used 6, the chat renderer's
# `_LEAK_PATTERNS` entry used 10) for the identical id shape; a `cmt_` ULID
# between 6 and 9 characters read clean through the renderer while this
# module's own `carries_surface_id()` already called it an id. ONE constant,
# both callers import it — never a hand-typed digit in either place again.
CMT_ID_MIN_LEN = 6

_WIRE_ID_RE = re.compile(
    r"\b(?:cmt_[0-9A-Za-z]{" + str(CMT_ID_MIN_LEN) + r",}|bp_[0-9a-f]{6,}"
    r"|commitment_seq_\d+|pcand:\S+|[a-z][a-z0-9_]*:[0-9a-f]{6,})\b",
    re.IGNORECASE)


def carries_surface_id(text) -> bool:
    """True when `text` carries an id shape no customer surface may render —
    a plugin-minted entity or wire id, or one of this module's opaque ids.

    This is the post-condition a COMPOSER checks on a display name it is
    about to print (a plate group header, a card option). The blocking
    scanner is still `validate_chat_output`; this is the earlier, cheaper
    door that keeps the id out of the sentence in the first place.
    """
    s = str(text or "")
    if not s:
        return False
    return bool(_ENTITY_ID_RE.search(s) or _WIRE_ID_RE.search(s)
                or _NUMERIC_TAILED_ID_RE.search(s)
                or _ALL_ID_RE.search(s))


__all__ = [
    "CMT_ID_MIN_LEN",
    "DEAD_POINTER_LABELS",
    "DEAD_POINTER_PATTERNS",
    "DEVICE_MARKER_LABELS",
    "DEVICE_MARKER_PATTERNS",
    "DOCUMENT_EXTENSIONS",
    "DOTTED_INTERNAL_ATTRS",
    "DOTTED_INTERNAL_MODULES",
    "LEAK2_LABELS",
    "LINK_TARGET_EXEMPT_NAMES",
    "NUMERIC_TAILED_ID_PREFIXES",
    "NUMERIC_TAILED_ID_SRC",
    "PLUMBING_VOCAB_LABELS",
    "GENERATED_VOCAB_ROSTER",
    "TREE_VOCAB_ROSTER",
    "PLUMBING_VOCAB_PATTERNS",
    "SUBSTRATE_PATH_PATTERNS",
    "SUBSTRATE_PATH_SOURCES",
    "SURFACE_ID_PATTERNS",
    "carries_surface_id",
    "dead_pointer_leak_patterns",
    "device_marker_leak_patterns",
    "FIRE_NARRATION_LABELS",
    "FIRE_NARRATION_PATTERNS",
    "fire_narration_leak_patterns",
    "UNATTENDED_ASK_LABELS",
    "UNATTENDED_ASK_PATTERNS",
    "UNATTENDED_FIRED_VIA",
    "unattended_ask_exempt_lines",
    "unattended_doc_leak_patterns",
    "artifact_line_exempt",
    "UNATTENDED_DOC_PATTERNS",
    "UNATTENDED_DOC_LABELS",
    "BOOTLOADER_ABORT_MESSAGES",
    "bootloader_abort_lines",
    "unattended_ask_leak_patterns",
    "TOOL_ID_LABELS",
    "TOOL_ID_PATTERNS",
    "tool_id_leak_patterns",
    "plumbing_vocab_leak_patterns",
    "substrate_path_leak_patterns",
    "substrate_path_patterns",
    "surface_id_leak_patterns",
    "surface_id_patterns",
]
