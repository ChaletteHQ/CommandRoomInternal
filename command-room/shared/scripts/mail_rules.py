#!/usr/bin/env python3
"""The owner's own mail rules: what the inbox hides, what it always shows,
and the threads the owner dropped (cr1#1020, slice 1).

One store beside the other learned files, `_hq/data/mail-rules.json`:

  rules    one row per rule, `{id, mode, match, value, note, source,
           added_at, choice_id}`, plus `off_limits: true` on a sender hide
           that onboarding's off-limits answer wrote (cr1#1181: that sender's
           mail is also kept out of filing, capture and the record). `mode` is `hide` or `show` (owner ruling
           2026-11-11 on cr1#1020: a taught filter HIDES that kind of mail;
           it does not only lower it). `match` is what "that kind of mail"
           means: `sender` (one address), `domain` (an address's domain and
           its subdomains), `subject` (every word of the value appears in the
           subject) or `kind` (the inbox's own noise class, such as
           `marketing`). A removed rule keeps its row with `removed_at`, so the
           history stays additive; it no longer matches.
  dropped  one row per thread the owner dropped from the inbox, `{thread_id,
           message_ts, title, dropped_at, choice_id}`. A dropped thread stays
           gone until a message newer than the one it was dropped at arrives.
           Bringing it back stamps `back_at`.
  kept     one row per thread the owner asked to see although a filter set it
           aside (cr1#1169, "show it anyway" on the Hub's Filtered list),
           `{thread_id, title, kept_at, choice_id}`. A kept thread is shown,
           whatever rule or noise class would set it aside, until the owner
           takes it back (`unkeep`, which stamps `unkept_at`). Written only
           by a press; a store without the key reads as nothing kept.

The rules are the owner's words, applied by code: nothing here guesses a rule
from behaviour (that is insight-generator's Pass 14, with its own store).
Every inbox read applies them (`inbox_helpers.select_priority`), and the Hub
lists them where they can be removed. The actions (`apply_choices`) and the
reads (`feed`, `verdict`, `sort_items`) are surface-free, so a later Hub
(cr1#1013) reuses them and only redraws the screen.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import wall_text  # cr1#1779: the one wall fold

try:
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock  # type: ignore

STORE_REL = Path("_hq") / "data" / "mail-rules.json"
#: The last off-limits count Maintenance saw (decision 141x 2): a number and a
#: time only, never a name, kept in the workspace beside the store.
#: Not `mail-rules-<word>.json`: that is the shape the sync-conflict rule
#: reads as a copy of the list (`sync_conflict_copies.copy_tag`).
SEEN_REL = Path("_hq") / "data" / "off-limits-count.json"
MODES = ("hide", "show")
MATCHES = ("sender", "domain", "subject", "kind")
#: The inbox's own noise classes (`inbox_helpers.NOISE_CLASSES`), the only
#: values a `kind` rule takes.
KINDS = ("listings", "marketing", "calendar", "security", "self_test")
#: A domain rule on a public mail domain would hide or pin everyone who uses
#: it, so it is refused; a sender rule is the narrower answer there.
PUBLIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com",
    "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com",
})
NOTE_CAP = 300
VALUE_CAP = 200
TITLE_CAP = 200
RULES_CAP = 500
#: Words a note uses to mean "always show these". Anything else is a hide:
#: the ruling's default for a taught filter.
_SHOW_RE = re.compile(r"\b(always show|always keep|show (me )?(these|this|them|all)|keep showing|"
                      r"never hide|important|i care|we care|matters?)\b", re.I)
_NEGATED_RE = re.compile(r"\b(don'?t|doesn'?t|do not|does not|isn'?t|aren'?t|is not|are not|won'?t|never|"
                         r"no longer|not|stop|nothing|none of (this|these|them|it))\s+"
                         r"(that\s+|really\s+|very\s+|much\s+)?(care|matters?|important|show)", re.I)
_PREFIX_RE = re.compile(r"^\s*((re|fw|fwd|aw|sv)\s*:\s*)+", re.I)
_WORD_RE = re.compile(r"[a-z0-9']+")

ADDED_LINE = "Rule saved: {plain}. It applies from the next inbox read."
ALREADY_LINE = "That rule is already in force; nothing was written."
REPLACED_LINE = "Rule saved: {plain}. It replaces the opposite rule on the same mail, and applies from the next inbox read."
REMOVED_LINE = "Rule removed: {plain}. It stops applying from the next inbox read."
RESTORED_LINE = "Rule removed: {plain}. The rule it replaced is back: {back}. Both apply from the next inbox read."
REMOVED_ALREADY_LINE = "That rule was already removed; nothing was written."
RULE_GONE_LINE = "No rule with that id is on file; nothing was written."
DROPPED_LINE = "Dropped. It stays off your inbox until a newer message arrives in that thread."
DROPPED_ALREADY_LINE = "That thread is already dropped; nothing was written."
BACK_LINE = "Brought back. It shows again from the next inbox read."
BACK_ALREADY_LINE = "That thread is not dropped; nothing was written."
KEPT_LINE = "Shown anyway. It comes back to your inbox from the next inbox read."
KEPT_ALREADY_LINE = "That thread is already shown anyway; nothing was written."
UNKEPT_LINE = "Filtered again. It is set aside from the next inbox read."
UNKEPT_ALREADY_LINE = "That thread is not shown anyway; nothing was written."
#: The `why` a kept thread's verdict carries.
KEPT_WHY = "you asked to see it"
NO_THREAD_LINE = "That row names no thread, so it cannot be dropped from here; say it in chat."
BAD_MODE_LINE = "A rule either hides mail or always shows it; nothing was written."
BAD_MATCH_LINE = "A rule matches a sender, a domain, a subject or a kind of mail; nothing was written."
EMPTY_VALUE_LINE = "The rule names nothing to match; nothing was written."
BAD_SENDER_LINE = "That is not an email address; nothing was written."
BAD_DOMAIN_LINE = "That is not a mail domain; nothing was written."
PUBLIC_DOMAIN_LINE = "{domain} is a public mail service, so a rule on it would cover everyone who uses it. Make it a sender rule instead; nothing was written."
BAD_KIND_LINE = "That kind of mail is not one the inbox sorts by ({kinds}); nothing was written."
SHORT_SUBJECT_LINE = "A subject rule needs at least one word of two letters or more; nothing was written."
UNKNOWN_VERB_LINE = "This surface does not take that press ({verb}); nothing was written."
STORE_UNREADABLE_LINE = "Your mail rules could not be read, so nothing was written. The next run tries again."
FULL_LINE = "You have {n} mail rules in force, the most this inbox keeps; remove one first."


# ---------------------------------------------------------------- time

def _now(now_iso: Optional[str] = None) -> _dt.datetime:
    if now_iso:
        dt = _parse(now_iso)
        if dt is not None:
            return dt
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value) -> Optional[_dt.datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            secs = float(value) / (1000.0 if float(value) > 1e11 else 1.0)
            return _dt.datetime.fromtimestamp(secs, _dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return _parse(int(text))
    try:
        dt = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


# ---------------------------------------------------------------- the store

def store_path(workspace_root) -> Path:
    return Path(workspace_root) / STORE_REL


def _empty() -> Dict[str, Any]:
    return {"version": 1, "rules": [], "dropped": []}


def load(workspace_root) -> Dict[str, Any]:
    """The store, or an empty one when it does not exist. Never raises: an
    unreadable store reads as empty here, so an inbox read never stops on it
    (the writers refuse instead, see `_load_for_write`)."""
    try:
        return _load_for_write(workspace_root)
    except ValueError:
        return _empty()


def _load_for_write(workspace_root) -> Dict[str, Any]:
    path = store_path(workspace_root)
    if not path.exists():
        # cr1#1831 (decision 141u b): a list that vanished while a kept copy
        # still puts someone off limits is held, not "nobody" (the likelier
        # accident on a synced folder). No copy, or a copy with nobody on
        # it, is a new workspace: empty, as before.
        if _missing_is_held(workspace_root):
            held = _empty()
            held[HELD_KEY] = {"missing": True}
            return held
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"mail rules unreadable: {type(exc).__name__}") from exc
    return _checked(data)


def _checked(data) -> Dict[str, Any]:
    """A parsed store, made whole, or ValueError when it is no answer. The
    live store and every kept copy pass the same check (cr1#1831)."""
    if not isinstance(data, dict):
        raise ValueError("mail rules unreadable: not an object")
    # cr1#1830: rules that are there but not a list are no answer. Read as an
    # empty list they put nobody off limits, and the next write replaced them
    # with only the new rule, losing every off-limits answer for good.
    if "rules" in data and not isinstance(data.get("rules"), list):
        raise ValueError("mail rules unreadable: rules not a list")
    data.setdefault("version", 1)
    for key in ("rules", "dropped"):
        if not isinstance(data.get(key), list):
            data[key] = []
    return data


# ---------------------------------------------------------------- matching

def _clean(text: Any, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def address_of(text: Any) -> str:
    """The bare address in `Name <name@example.com>` or a bare address, lower-cased; empty when
    there is none. cr1#1779: read through the one wall fold's `strip` first,
    so a mark or a character that prints nothing inside an address does not
    make it a different address."""
    s = wall_text.strip(text)
    m = re.search(r"<([^<>@\s]+@[^<>\s]+)>", s)
    addr = m.group(1) if m else s.strip()
    addr = addr.strip().strip("<>").lower()
    return addr if re.fullmatch(r"[^@\s,;]+@[a-z0-9.-]+\.[a-z0-9-]+", addr) else ""


def domain_of(text: Any) -> str:
    addr = address_of(text)
    if addr:
        return addr.rsplit("@", 1)[1]
    d = wall_text.strip(text).strip().lower().lstrip("@").rstrip(".")
    return d if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", d) else ""


def subject_words(text: Any) -> List[str]:
    """The words a subject rule compares: the subject without its Re/Fwd
    prefixes, lower-cased, words of two letters or more, and no word holding a
    digit (an invoice number or a date makes every similar mail different)."""
    s = _PREFIX_RE.sub("", str(text or "")).lower()
    return [w for w in _WORD_RE.findall(s) if len(w) >= 2 and not any(ch.isdigit() for ch in w)]


def _norm_value(match: str, value: Any) -> tuple:
    """`(value, error line)` for a rule's value, normalised for its match."""
    raw = _clean(value, VALUE_CAP)
    if not raw:
        return "", EMPTY_VALUE_LINE
    if match == "sender":
        addr = address_of(raw)
        return (addr, None) if addr else ("", BAD_SENDER_LINE)
    if match == "domain":
        dom = domain_of(raw)
        if not dom:
            return "", BAD_DOMAIN_LINE
        if dom in PUBLIC_DOMAINS:
            return "", PUBLIC_DOMAIN_LINE.format(domain=dom)
        return dom, None
    if match == "subject":
        words = subject_words(raw)
        return (" ".join(words), None) if words else ("", SHORT_SUBJECT_LINE)
    kind = raw.lower().replace("-", "_").replace(" ", "_")
    return (kind, None) if kind in KINDS else ("", BAD_KIND_LINE.format(kinds=", ".join(KINDS)))


def stored_value(rule: Dict[str, Any]) -> str:
    """A rule's value as a match reads it now (review of cr1#1779, MUST): a
    sender or a domain stored before the one wall fold holds its raw value
    ("zo\u00eb@example.com"), which `address_of` now reads folded; read through
    the same function, a rule stored either way matches the same mail."""
    match, value = rule.get("match"), str(rule.get("value") or "")
    if match == "sender":
        return address_of(value) or value.strip().lower()
    if match == "domain":
        return domain_of(value) or value.strip().lower()
    return value


def _same_rule(rule: Dict[str, Any], mode: str, match: str, value: str) -> bool:
    """Whether `rule` is the rule (mode, match, value): by id, or, for a rule
    stored before the one wall fold, by its value read now."""
    if rule.get("id") == rule_id(mode, match, value):
        return True
    return rule.get("mode") == mode and rule.get("match") == match and stored_value(rule) == value


def rule_id(mode: str, match: str, value: str) -> str:
    digest = hashlib.sha1(f"{mode}|{match}|{value}".encode("utf-8")).hexdigest()[:12]
    return f"mr-{digest}"


def plain(rule: Dict[str, Any]) -> str:
    """One plain phrase for a rule, for its row and its receipt line."""
    verb = "always show" if rule.get("mode") == "show" else "hide"
    match, value = rule.get("match"), rule.get("value") or ""
    if rule.get("off_limits") and rule.get("mode") == "hide" and match == "sender":
        # cr1#1181: onboarding's off-limits answer, kept out of the record too.
        return f"keep mail from {value} out of your inbox and your records"
    if match == "sender":
        what = f"mail from {value}"
    elif match == "domain":
        what = f"mail from anyone at {value}"
    elif match == "subject":
        what = f"mail whose subject has the words \"{value}\""
    else:
        what = f"{str(value).replace('_', '-')} mail"
    return f"{verb} {what}"


def mode_from_note(note: Any) -> str:
    """`show` when the owner's note says to always show this kind of mail,
    otherwise `hide` (the ruling's default for a taught filter)."""
    # A phone types a curly apostrophe; read it as the plain one.
    text = str(note or "").replace("\u2019", "'").replace("\u2018", "'")
    if _SHOW_RE.search(text) and not _NEGATED_RE.search(text):
        return "show"
    return "hide"


def item_facts(item: Dict[str, Any]) -> Dict[str, Any]:
    """What a rule can match on one inbox thread, from the fields the inbox
    fetch carries. Nothing else of the thread is read."""
    sender = address_of(item.get("sender_email") or item.get("sender") or item.get("from") or "")
    domain = wall_text.fold(item.get("domain")) or (sender.rsplit("@", 1)[1] if sender else "")
    return {"sender": sender, "domain": domain,
            "subject": set(subject_words(item.get("subject") or "")),
            "kind": str(item.get("noise_class") or "").strip().lower().replace("-", "_"),
            "thread_id": str(item.get("thread_id") or "").strip(),
            "ts": _parse(item.get("ts") or item.get("message_ts"))}


def _matches(rule: Dict[str, Any], facts: Dict[str, Any]) -> bool:
    match, value = rule.get("match"), stored_value(rule)
    if not value:
        return False
    if match == "sender":
        return facts["sender"] == value
    if match == "domain":
        d = facts["domain"]
        return bool(d) and (d == value or d.endswith("." + value))
    if match == "subject":
        words = value.split()
        return bool(words) and all(w in facts["subject"] for w in words)
    if match == "kind":
        return facts["kind"] == value
    return False


def active_rules(store: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in (store.get("rules") or [])
            if isinstance(r, dict) and not r.get("removed_at") and r.get("mode") in MODES]


def _active_drop(store: Dict[str, Any], thread_id: str) -> Optional[Dict[str, Any]]:
    if not thread_id:
        return None
    for d in store.get("dropped") or []:
        if isinstance(d, dict) and str(d.get("thread_id") or "") == thread_id and not d.get("back_at"):
            return d
    return None


def _active_keep(store: Dict[str, Any], thread_id: str) -> Optional[Dict[str, Any]]:
    if not thread_id:
        return None
    for k in store.get("kept") or []:
        if isinstance(k, dict) and str(k.get("thread_id") or "") == thread_id and not k.get("unkept_at"):
            return k
    return None


def verdict(store: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    """`{action, rule_id, why}` for one thread: `show` (a show rule or a
    "show it anyway" names it; it is never hidden), `hide` (a hide rule or a
    drop names it) or `None`. A show rule wins over a hide rule, so "always
    show these" is never undone by a broader hide; a drop wins over all,
    because it names this thread; a kept thread (cr1#1169) wins over every
    rule for the same reason."""
    facts = item_facts(item)
    drop = _active_drop(store, facts["thread_id"])
    if drop is not None:
        at = _parse(drop.get("message_ts")) or _parse(drop.get("dropped_at"))
        if facts["ts"] is None or at is None or facts["ts"] <= at:
            return {"action": "hide", "rule_id": None, "why": "dropped"}
    if _active_keep(store, facts["thread_id"]) is not None:
        return {"action": "show", "rule_id": None, "why": KEPT_WHY}
    hit_hide = None
    for rule in active_rules(store):
        if not _matches(rule, facts):
            continue
        if rule["mode"] == "show":
            return {"action": "show", "rule_id": rule.get("id"), "why": plain(rule)}
        hit_hide = hit_hide or rule
    if hit_hide is not None:
        return {"action": "hide", "rule_id": hit_hide.get("id"), "why": plain(hit_hide)}
    return {"action": None, "rule_id": None, "why": None}


def sort_items(store: Dict[str, Any], items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """`{shown, hidden, pinned}` over the fetched threads, in their order.
    `pinned` are the threads a show rule names (also in `shown`); `hidden`
    carries each thread with the `mail_rule` that hid it."""
    shown, hidden, pinned = [], [], []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        v = verdict(store, item)
        if v["action"] == "hide":
            row = dict(item)
            row["mail_rule"] = v["rule_id"] or v["why"]
            hidden.append(row)
            continue
        shown.append(item)
        if v["action"] == "show":
            pinned.append(item)
    return {"shown": shown, "hidden": hidden, "pinned": pinned}


# ---------------------------------------------------------------- writes

def _write(workspace_root, mutate) -> Dict[str, Any]:
    """Read, change and write the store under its lock. `mutate(store)`
    returns the result dict and whether anything changed."""
    path = store_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_write_lock(path, holder="mail-rules")
    try:
        try:
            store = _load_for_write(workspace_root)
        except ValueError:
            return {"outcome": "error", "line": STORE_UNREADABLE_LINE}
        # cr1#1831 (decision 141aa): while the list is held and a readable
        # kept copy exists, no write lands. The copy ends the hold only by the
        # owner's restore (ruling 142a), so one re-entered person never
        # becomes the list while the copies from before the hold age out.
        refusal = held_refusal(workspace_root, store)
        if refusal is not None:
            return refusal
        was_held = is_held(store)
        result, changed = mutate(store)
        if changed:
            atomic_write_json(path, store)
            if was_held:
                # review round 1, should-fix 5: the write that ends a hold
                # (a re-entered, maybe shorter, list) never replaces the last
                # copy from before the hold.
                return result
            # cr1#1831 (decision 141t): the day's copy is taken AFTER the
            # write, from the bytes just written, once they read back clean
            # and are not a held store, so the newest off-limits row is never
            # the one a restore loses and a damaged list never reaches a backup.
            _keep_written_copy(workspace_root, path)
        return result
    finally:
        release_write_lock(lock)


# ---------------------------------------------------------------- the last readable copy (cr1#1831)
#
# Owner rulings 142a and 142b on cr1#1831 (option A). A store that cannot be
# read is no answer, so everything that names people holds (cr1#1830). The
# repair has three parts, all here:
#   * KEPT COPY. On every write, after the new store is written and read back
#     clean (decision 141t), the store just written is copied into the one
#     backups folder, `_hq/data/_backups/mail-rules.json.<YYYYMMDD>.backup`
#     (one slot a day: a later clean write that day replaces it with a newer
#     good copy). The folder tidy keeps the three newest
#     (`cleanup_actions._TIDY_BACKUP_GROUPS`). A copy is `{taken_at, store}`.
#   * SET ASIDE. Maintenance, on every fire (`set_aside_if_unreadable`), copies
#     a damaged store's bytes into a freshly claimed dated `_archive/cleanup-*`
#     folder with a MANIFEST.md, then replaces the live file, atomically, with
#     a HELD store: no rules, and a `held` record. The live file is never
#     absent, because a missing store reads as nobody off limits. A held store
#     reads as not readable (`account_scope_gate.off_limits_readable`), so every
#     hold stays on, and `held_line` gives the brief and the health check one
#     line with the last readable copy's date and count, naming nobody.
#     The same fire seeds a copy only when no readable copy exists at all
#     (decision 141x 1), and notes the off-limits count it saw, a number only,
#     in `_hq/data/off-limits-count.json` (141x 2): a missing store whose copies
#     are gone too reads as held when that count was not zero.
#   * WRITES WAIT while the list is held and a readable copy exists
#     (`held_refusal`, decision 141aa): every write of the store answers the
#     copy's date and count and the words to restore it, so a hold with a
#     copy ends only by the restore. With no copy there is nothing to lose,
#     and re-entering the list in Settings ends the hold.
#   * RESTORE, on the owner's word only (`restore_off_limits`, confirm after
#     the date and count are shown, with the shown copy's time handed back and
#     compared as an instant, 141u c and 141x 3); or, with no readable copy, the owner re-enters the
#     list in Settings (`add_rule(off_limits=True)`), which ends the hold. Nothing
#     restores by itself.

BACKUP_DIR_REL = Path("_hq") / "data" / "_backups"
BACKUP_GROUP = STORE_REL.name
BACKUP_SUFFIX = ".backup"
HELD_KEY = "held"
_ARCHIVE_ROOT = "_archive"
_ARCHIVE_PREFIX = "cleanup-"
#: HQRESOLVE1: an archive never holds a folder named `_hq`.
_HQ_MIRROR = "hq-system-locks"

HELD_LINE = ("\u26a0 Your off-limits list could not be read, so nobody is named on your "
             "meetings and no new mail is read until it is fixed. The last readable copy "
             "is from {when}, with {count} on it: say \"restore my off-limits list\" to put "
             "it back.")
HELD_NO_COPY_LINE = ("\u26a0 Your off-limits list could not be read, so nobody is named on "
                     "your meetings and no new mail is read until it is fixed. There is no "
                     "earlier readable copy, so re-enter the list in Settings.")
RESTORE_OFFER_LINE = ("The last readable copy of your off-limits list is from {when}, with "
                      "{count} on it. Anyone you put off limits after that is not on it. "
                      "Say yes to put it back.")
RESTORED_OFF_LIMITS_LINE = ("Your off-limits list is back as it was on {when}, with {count} "
                            "on it. It applies from the next run.")
RESTORE_NO_COPY_LINE = ("There is no earlier readable copy of your off-limits list, so nothing "
                        "was restored. Re-enter the list in Settings.")
RESTORE_NOT_NEEDED_LINE = "Your off-limits list reads normally, so there is nothing to restore."
HELD_NO_COPY_COUNT_LINE = ("⚠ Your off-limits list could not be read, so nobody is named "
                           "on your meetings and no new mail is read until it is fixed. There is "
                           "no earlier readable copy. It last had {count} on it: re-enter them in "
                           "Settings.")
HELD_WRITE_REFUSED_LINE = ("Nothing was changed: your off-limits list is on hold. The last "
                           "readable copy is from {when}, with {count} on it. Say \"restore my "
                           "off-limits list\" to put it back, then make the change.")
RESTORE_ASK_FIRST_LINE = ("Nothing was restored: I need to show you the copy first. {offer}")
RESTORE_CHANGED_LINE = ("Nothing was restored: the last readable copy is not the one you were "
                        "shown. It is now from {when}, with {count} on it. Say yes again to "
                        "put that one back.")
ASIDE_MANIFEST = ("# Off-limits list set aside\n\n"
                  "The off-limits list (`_hq/data/mail-rules.json`) could not be read, so "
                  "Command Room moved it here on {when} and left a held list in its place. "
                  "Nobody is named until the owner restores the last readable copy or "
                  "re-enters the list. Nothing here was deleted.\n\n"
                  "- `{rel}`: the file as it was found\n")


def is_held(store: Any) -> bool:
    """A held store: the damaged one was set aside and nothing has replaced
    it yet (cr1#1831)."""
    return isinstance(store, dict) and isinstance(store.get(HELD_KEY), dict)


def off_limits_count(store: Dict[str, Any]) -> int:
    """How many senders a store puts off limits. A count, never a name."""
    return sum(1 for r in active_rules(store) if r.get("off_limits"))


def _backups_dir(workspace_root) -> Path:
    return Path(workspace_root) / BACKUP_DIR_REL


def _missing_is_held(workspace_root) -> bool:
    """A missing store reads as held when the newest readable kept copy puts
    anyone off limits (decision 141u b), or, with no copy left, when the last
    count Maintenance saw was not zero (decision 141x 2: the list and its
    copies both gone). A workspace that never had anyone off limits has no
    count, or a zero one, and stays new. Never raises."""
    try:
        got = _copies(workspace_root)
        if got:
            return off_limits_count(got[0]["store"]) > 0
        seen = _seen_count(workspace_root)
        return seen is None or seen > 0
    except Exception:  # noqa: BLE001
        return False


def _seen_count(workspace_root) -> Optional[int]:
    """The last off-limits count Maintenance saw: 0 when it never saw one (no
    trace), None when the trace is there and cannot be read (read as held:
    the workspace had a list, and nobody can say it was empty)."""
    path = Path(workspace_root) / SEEN_REL
    if not path.exists():
        return 0
    try:
        n = json.loads(path.read_text(encoding="utf-8")).get("off_limits_count")
        return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None
    except Exception:  # noqa: BLE001
        return None


def _note_seen(workspace_root, n: int, now_iso: Optional[str] = None) -> None:
    """Maintenance keeps the count it saw (decision 141x 2), written only when
    it changes. Never raises."""
    try:
        if Path(workspace_root).joinpath(SEEN_REL).exists() and _seen_count(workspace_root) == n:
            return
        atomic_write_json(Path(workspace_root) / SEEN_REL,
                          {"version": 1, "off_limits_count": int(n), "seen_at": _iso(_now(now_iso))})
    except Exception:  # noqa: BLE001
        pass


def _today_slot(workspace_root, now_iso: Optional[str] = None) -> Path:
    return _backups_dir(workspace_root) / f"{BACKUP_GROUP}.{_now(now_iso):%Y%m%d}{BACKUP_SUFFIX}"


def _keep_written_copy(workspace_root, path: Path) -> None:
    """The store as just written, read back from disk, into today's slot;
    nothing when it does not read back clean or is held. Never raises."""
    try:
        written = _checked(json.loads(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 - not verified clean: no copy
        return
    if not is_held(written):
        _keep_copy(workspace_root, written)


def _keep_copy(workspace_root, store: Dict[str, Any], now_iso: Optional[str] = None) -> None:
    """A verified store into today's backup slot. Best-effort: a copy that
    cannot be written never stops the owner's own write."""
    now = _now(now_iso)
    dest = _today_slot(workspace_root, now_iso)
    try:
        atomic_write_json(dest, {"version": 1, "taken_at": _iso(now), "store": store})
    except Exception:  # noqa: BLE001
        pass


def _copies(workspace_root) -> List[Dict[str, Any]]:
    """The readable kept copies, newest first, each `{path, taken_at, store}`.
    A copy that does not read is skipped, never offered."""
    out = []
    d = _backups_dir(workspace_root)
    if not d.is_dir():
        return out
    for p in d.iterdir():
        name = p.name
        if not (name.startswith(BACKUP_GROUP + ".") and name.endswith(BACKUP_SUFFIX)) or not p.is_file():
            continue
        try:
            wrap = json.loads(p.read_text(encoding="utf-8"))
            store = _checked(wrap.get("store"))
            taken = _parse(wrap.get("taken_at"))
        except Exception:  # noqa: BLE001
            continue
        if taken is None or is_held(store):
            continue
        out.append({"path": p, "taken_at": taken, "store": store})
    out.sort(key=lambda c: (c["taken_at"], c["path"].name), reverse=True)
    return out


def last_readable_copy(workspace_root) -> Optional[Dict[str, Any]]:
    """The newest readable kept copy as `{taken_at, n}`, or None. Read only."""
    got = _copies(workspace_root)
    if not got:
        return None
    return {"taken_at": _iso(got[0]["taken_at"]), "n": off_limits_count(got[0]["store"])}


def _count_words(n: int) -> str:
    if n == 0:
        return "nobody"
    return "1 person" if n == 1 else f"{n} people"


def _when(workspace_root, value) -> str:
    """A copy's time in the owner's timezone, in plain words."""
    try:
        from tz import to_local
        d = to_local(value, workspace_path=workspace_root)
    except Exception:  # noqa: BLE001
        d = _parse(value)
    if d is None:
        return "an earlier day"
    hour = d.hour % 12 or 12
    return f"{d:%A}, {d:%B} {d.day} at {hour}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def held_refusal(workspace_root, store: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The refusal every write of the store answers while the list is held and
    a readable kept copy exists (decision 141aa): the copy's date and count and
    the words to restore it, naming nobody. None when the list is not held, or
    held with no copy (then re-entering it ends the hold: nothing to lose)."""
    if store is None:
        try:
            store = _load_for_write(workspace_root)
        except ValueError:
            return None
    if not is_held(store):
        return None
    copy = last_readable_copy(workspace_root)
    if copy is None:
        return None
    return {"outcome": "refused", "reason": "held_with_copy",
            "line": HELD_WRITE_REFUSED_LINE.format(when=_when(workspace_root, copy["taken_at"]),
                                                   count=_count_words(copy["n"])),
            "taken_at": copy["taken_at"], "n": copy["n"]}


def held_line(workspace_root) -> str:
    """The one line the brief and the health check say while the list cannot
    be read: date and count of the last readable copy, never a name."""
    copy = last_readable_copy(workspace_root)
    if copy is None:
        # the count trace knows how many were on the list (a number only)
        seen = _seen_count(workspace_root)
        if seen:
            return HELD_NO_COPY_COUNT_LINE.format(count=_count_words(seen))
        return HELD_NO_COPY_LINE
    return HELD_LINE.format(when=_when(workspace_root, copy["taken_at"]),
                            count=_count_words(copy["n"]))


def _claim_archive(root: Path, today: str) -> str:
    """A fresh dated archive folder, never one that exists (the folder tidy's
    own rule: an existing dated folder is never written into)."""
    arch = root / _ARCHIVE_ROOT
    arch.mkdir(parents=True, exist_ok=True)
    base = f"{_ARCHIVE_PREFIX}{today}"
    name, n = base, 2
    while True:
        try:
            (arch / name).mkdir()
            return name
        except FileExistsError:
            name = f"{base}-{n}"
            n += 1


def _local_day(root: Path, now: _dt.datetime) -> str:
    """The workspace's own day for an archive folder's name (review round 1,
    should-fix 7); UTC only when the workspace's zone cannot be read."""
    try:
        from tz import to_local
        return f"{to_local(now, workspace_path=root):%Y-%m-%d}"
    except Exception:  # noqa: BLE001
        return f"{now:%Y-%m-%d}"


def _set_aside_locked(root: Path, path: Path, now: _dt.datetime) -> Dict[str, Any]:
    """Under the store's lock, a damaged store: its bytes into a dated archive
    folder, then the held store over it in one atomic replace."""
    raw = path.read_bytes()
    bucket = _claim_archive(root, _local_day(root, now))
    rel = Path(*(_HQ_MIRROR if part == "_hq" else part for part in STORE_REL.parts))
    dest = root / _ARCHIVE_ROOT / bucket / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    archived = (Path(_ARCHIVE_ROOT) / bucket / rel).as_posix()
    from atomic_write import atomic_write_text
    atomic_write_text(root / _ARCHIVE_ROOT / bucket / "MANIFEST.md",
                      ASIDE_MANIFEST.format(when=_iso(now), rel=rel.as_posix()))
    held = _empty()
    held[HELD_KEY] = {"set_aside_at": _iso(now), "archived_to": archived}
    atomic_write_json(path, held)
    return {"outcome": "set_aside", "archived_to": archived}


def set_aside_if_unreadable(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Maintenance, every fire: a store that cannot be read is set aside and a
    held store takes its place. A WRITER. Outcomes: `absent`, `missing_held`
    (no file, but a kept copy puts someone off limits, or with no copy the
    last count seen was not zero: held, decisions 141u b and 141x 2; nothing
    is written), `readable`, `held` (already set aside), `set_aside`,
    `error`. A readable list with no readable copy at all gets one (decisions
    141u a and 141x 1: only when there is none, so a wrong but readable list
    never pushes the older good copies out), and its count is noted. Never
    raises."""
    root = Path(workspace_root)
    path = store_path(root)
    if not path.exists():
        return {"outcome": "missing_held" if _missing_is_held(root) else "absent"}
    try:
        lock = acquire_write_lock(path, holder="mail-rules")
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "error", "reason": type(exc).__name__}
    try:
        try:
            store = _load_for_write(root)
        except ValueError:
            return _set_aside_locked(root, path, _now(now_iso))
        if is_held(store):
            return {"outcome": "held"}
        seeded = False
        if not _copies(root):
            _keep_copy(root, store, now_iso)
            seeded = True
        _note_seen(root, off_limits_count(store), now_iso)
        return {"outcome": "readable", "seeded": seeded}
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "error", "reason": type(exc).__name__}
    finally:
        release_write_lock(lock)


def restore_off_limits(workspace_root, *, confirm: bool = False,
                       taken_at: Optional[str] = None,
                       now_iso: Optional[str] = None) -> Dict[str, Any]:
    """The owner's restore (ruling 142a, option A). Without `confirm`: what the
    last readable copy is, as a date and a count, and nothing is written. With
    `confirm` (the owner said yes after seeing them): the newest readable copy
    is put back, keeping any rule, dropped thread or kept thread the owner
    added during the hold. A damaged store not yet set aside is set aside
    first, so the archive keeps it. Names nobody. Outcomes: `offer`,
    `restored`, `changed`, `ask_first`, `no_copy`, `not_needed`, `error`.

    The yes is tied to the copy shown (decision 141u c): the offer returns
    `taken_at`, and `confirm` must hand the same `taken_at` back. Any other
    value, or none, restores nothing and answers `changed` with the newest
    copy's date and count, to be confirmed again. The two are compared as
    instants, so the same moment written another way is the same copy
    (decision 141x 3); a yes with no time at all answers `ask_first` with the
    offer, and writes nothing."""
    root = Path(workspace_root)
    try:
        current = _load_for_write(root)
        damaged = False
    except ValueError:
        current, damaged = None, True
    if not damaged and not is_held(current):
        return {"outcome": "not_needed", "line": RESTORE_NOT_NEEDED_LINE}
    copies = _copies(root)
    if not copies:
        return {"outcome": "no_copy", "line": RESTORE_NO_COPY_LINE}
    top = copies[0]
    when, count = _when(root, top["taken_at"]), _count_words(off_limits_count(top["store"]))
    if not confirm:
        return {"outcome": "offer", "line": RESTORE_OFFER_LINE.format(when=when, count=count),
                "taken_at": _iso(top["taken_at"]), "n": off_limits_count(top["store"])}
    if taken_at in (None, ""):
        offer = RESTORE_OFFER_LINE.format(when=when, count=count)
        return {"outcome": "ask_first", "line": RESTORE_ASK_FIRST_LINE.format(offer=offer),
                "taken_at": _iso(top["taken_at"]), "n": off_limits_count(top["store"])}
    shown = _parse(taken_at)
    # the same instant, at the whole second a copy is stored with
    if shown is None or _iso(shown) != _iso(top["taken_at"]):
        return {"outcome": "changed", "line": RESTORE_CHANGED_LINE.format(when=when, count=count),
                "taken_at": _iso(top["taken_at"]), "n": off_limits_count(top["store"])}
    path = store_path(root)
    lock = acquire_write_lock(path, holder="mail-rules")
    try:
        try:
            current = _load_for_write(root)
        except ValueError:
            _set_aside_locked(root, path, _now(now_iso))
            current = _load_for_write(root)
        if not is_held(current):
            return {"outcome": "not_needed", "line": RESTORE_NOT_NEEDED_LINE}
        back = json.loads(json.dumps(top["store"]))
        for key, ident in (("rules", "id"), ("dropped", "thread_id"), ("kept", "thread_id")):
            rows = back.get(key) if isinstance(back.get(key), list) else []
            have = {r.get(ident) for r in rows if isinstance(r, dict)}
            for r in current.get(key) or []:
                if isinstance(r, dict) and r.get(ident) not in have:
                    rows.append(r)
            if rows or key != "kept":
                back[key] = rows
        back["restored"] = {"at": _iso(_now(now_iso)), "copy_taken_at": _iso(top["taken_at"]),
                            "set_aside_at": (current.get(HELD_KEY) or {}).get("set_aside_at")}
        back.pop(HELD_KEY, None)
        atomic_write_json(path, back)
    finally:
        release_write_lock(lock)
    return {"outcome": "restored", "line": RESTORED_OFF_LIMITS_LINE.format(when=when, count=count),
            "taken_at": _iso(top["taken_at"]), "n": off_limits_count(top["store"])}


def _replay(store: Dict[str, Any], choice_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The row a press already wrote, so a retried run answers from it."""
    if not choice_id:
        return None
    for key in ("rules", "dropped", "kept"):
        for row in store.get(key) or []:
            if isinstance(row, dict) and choice_id in (row.get("choice_id"), row.get("removed_by"),
                                                       row.get("back_by"), row.get("unkept_by")):
                return row
    return None


def add_rule(workspace_root, *, mode: str, match: str, value: Any, note: Any = "",
             source: str = "manual", from_thread: Optional[str] = None,
             choice_id: Optional[str] = None, now_iso: Optional[str] = None,
             off_limits: bool = False) -> Dict[str, Any]:
    """Add one rule. A rule already in force answers `already`; the opposite
    rule on the same mail is removed by the new one (`replaced`).

    `off_limits` (cr1#1181): onboarding's off-limits answer, a `hide` rule
    on one sender that also keeps that sender's mail out of filing, capture
    and the record (`account_scope_gate`). An ordinary hide rule only hides.
    Asking it of a hide rule already in force flags that rule."""
    off_limits = bool(off_limits) and str(mode or "").strip().lower() == "hide"         and str(match or "").strip().lower() == "sender"
    mode = str(mode or "").strip().lower()
    match = str(match or "").strip().lower()
    if mode not in MODES:
        return {"outcome": "refused", "line": BAD_MODE_LINE}
    if match not in MATCHES:
        return {"outcome": "refused", "line": BAD_MATCH_LINE}
    value, err = _norm_value(match, value)
    if err:
        return {"outcome": "refused", "line": err}
    now = _iso(_now(now_iso))
    rid = rule_id(mode, match, value)

    def mutate(store):
        prior = _replay(store, choice_id)
        if prior is not None and prior.get("id") == rid:
            return {"outcome": "already", "line": ALREADY_LINE, "rule": prior, "replayed": True}, False
        live = active_rules(store)
        # review of cr1#1779: a rule stored with its raw value before the one
        # wall fold is the same rule, never a second one
        if any(_same_rule(r, mode, match, value) for r in live):
            held = next(r for r in live if _same_rule(r, mode, match, value))
            if off_limits and not held.get("off_limits"):
                held["off_limits"] = True
                return {"outcome": "applied", "line": ADDED_LINE.format(plain=plain(held)),
                        "rule": held, "flagged": True}, True
            return {"outcome": "already", "line": ALREADY_LINE, "rule": held}, False
        if len(live) >= RULES_CAP:
            return {"outcome": "refused", "line": FULL_LINE.format(n=len(live))}, False
        other_mode = "show" if mode == "hide" else "hide"
        replaced = None
        for r in live:
            if _same_rule(r, other_mode, match, value):
                r["removed_at"] = now
                r["removed_by"] = choice_id or ""
                r["replaced_by"] = rid
                replaced = r.get("id")
        rule = {"id": rid, "mode": mode, "match": match, "value": value,
                "note": _clean(note, NOTE_CAP), "source": "taught" if source == "taught" else "manual",
                "added_at": now, "choice_id": choice_id or ""}
        if from_thread:
            rule["from_thread"] = _clean(from_thread, 200)
        if off_limits:
            rule["off_limits"] = True
        # A removed rule with the same id comes back as a new row: the old
        # row stays as history.
        store["rules"].append(rule)
        line = (REPLACED_LINE if replaced else ADDED_LINE).format(plain=plain(rule))
        out = {"outcome": "applied", "line": line, "rule": rule}
        if replaced:
            out["replaced"] = replaced
        return out, True

    def mutate_or_end_hold(store):
        # cr1#1831: the owner re-entering the list (Settings writes each
        # answer here with `off_limits`) ends a hold; an ordinary rule does not.
        result, changed = mutate(store)
        if off_limits and is_held(store) and result.get("outcome") in ("applied", "already"):
            store["re_entered"] = {"at": now, "set_aside_at": store[HELD_KEY].get("set_aside_at")}
            store.pop(HELD_KEY, None)
            changed = True
        return result, changed

    return _write(workspace_root, mutate_or_end_hold)


def remove_rule(workspace_root, *, rule_id_: str, choice_id: Optional[str] = None,
                now_iso: Optional[str] = None, restore: bool = False) -> Dict[str, Any]:
    """Remove one rule. With `restore` (cr1#1169: the undo of "never filter
    this sender"), the rule it replaced, if any, is in force again, so the
    undo leaves the mail as it was before the press."""
    rid = str(rule_id_ or "").strip()
    now = _iso(_now(now_iso))

    def mutate(store):
        rows = [r for r in store.get("rules") or [] if isinstance(r, dict) and r.get("id") == rid]
        if not rows:
            return {"outcome": "refused", "line": RULE_GONE_LINE}, False
        live = [r for r in rows if not r.get("removed_at")]
        if not live:
            return {"outcome": "already", "line": REMOVED_ALREADY_LINE, "rule": rows[-1]}, False
        for r in live:
            r["removed_at"] = now
            r["removed_by"] = choice_id or ""
        out = {"outcome": "applied", "line": REMOVED_LINE.format(plain=plain(live[-1])), "rule": live[-1]}
        if restore:
            live_ids = {r.get("id") for r in active_rules(store)}
            for old in reversed(store.get("rules") or []):
                if (isinstance(old, dict) and old.get("replaced_by") == rid and old.get("removed_at")
                        and old.get("id") not in live_ids):
                    for key in ("removed_at", "removed_by", "replaced_by"):
                        old.pop(key, None)
                    old["restored_at"] = now
                    out["restored"] = old.get("id")
                    out["line"] = RESTORED_LINE.format(plain=plain(live[-1]), back=plain(old))
                    break
        return out, True

    return _write(workspace_root, mutate)


def drop_thread(workspace_root, *, thread_id: Any, message_ts: Any = None, title: Any = "",
                choice_id: Optional[str] = None, now_iso: Optional[str] = None) -> Dict[str, Any]:
    tid = _clean(thread_id, 200)
    if not tid:
        return {"outcome": "refused", "line": NO_THREAD_LINE}
    now = _iso(_now(now_iso))
    ts = _parse(message_ts)

    def mutate(store):
        if _active_drop(store, tid) is not None:
            return {"outcome": "already", "line": DROPPED_ALREADY_LINE}, False
        store["dropped"].append({"thread_id": tid, "message_ts": _iso(ts) if ts else None,
                                 "title": _clean(title, TITLE_CAP), "dropped_at": now,
                                 "choice_id": choice_id or ""})
        return {"outcome": "applied", "line": DROPPED_LINE}, True

    return _write(workspace_root, mutate)


def bring_back(workspace_root, *, thread_id: Any, choice_id: Optional[str] = None,
               now_iso: Optional[str] = None) -> Dict[str, Any]:
    tid = _clean(thread_id, 200)
    now = _iso(_now(now_iso))

    def mutate(store):
        drop = _active_drop(store, tid)
        if drop is None:
            return {"outcome": "already", "line": BACK_ALREADY_LINE}, False
        drop["back_at"] = now
        drop["back_by"] = choice_id or ""
        return {"outcome": "applied", "line": BACK_LINE}, True

    return _write(workspace_root, mutate)


def keep_thread(workspace_root, *, thread_id: Any, title: Any = "",
                choice_id: Optional[str] = None, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Show it anyway (cr1#1169): the thread is shown from the next inbox
    read, whatever rule or noise class set it aside."""
    tid = _clean(thread_id, 200)
    if not tid:
        return {"outcome": "refused", "line": NO_THREAD_LINE}
    now = _iso(_now(now_iso))

    def mutate(store):
        if _active_keep(store, tid) is not None:
            return {"outcome": "already", "line": KEPT_ALREADY_LINE}, False
        store.setdefault("kept", []).append({"thread_id": tid, "title": _clean(title, TITLE_CAP),
                                             "kept_at": now, "choice_id": choice_id or ""})
        return {"outcome": "applied", "line": KEPT_LINE}, True

    return _write(workspace_root, mutate)


def unkeep_thread(workspace_root, *, thread_id: Any, choice_id: Optional[str] = None,
                  now_iso: Optional[str] = None) -> Dict[str, Any]:
    """The undo of show it anyway: the filters apply to the thread again."""
    tid = _clean(thread_id, 200)
    now = _iso(_now(now_iso))

    def mutate(store):
        keep = _active_keep(store, tid)
        if keep is None:
            return {"outcome": "already", "line": UNKEPT_ALREADY_LINE}, False
        keep["unkept_at"] = now
        keep["unkept_by"] = choice_id or ""
        return {"outcome": "applied", "line": UNKEPT_LINE}, True

    return _write(workspace_root, mutate)


# ---------------------------------------------------------------- the Hub's door

#: The presses a surface sends, as `{id, action, ...}`:
#:   drop    `thread_id`, `message_ts`, `title`
#:   teach   `thread_id`, `match`, `value`, `note`, `mode` (optional: read from the note)
#:   add     `match`, `value`, `mode`, `note`  (a rule written by hand)
#:   remove  `rule_id`, `restore` (true: the rule it replaced comes back, cr1#1169)
#:   back    `thread_id` (a dropped thread brought back)
#:   keep    `thread_id`, `title` (cr1#1169: show it anyway)
#:   unkeep  `thread_id` (its undo: filtered again)
VERBS = ("drop", "teach", "add", "remove", "back", "keep", "unkeep")


#: cr1#1230: what a press says, once, before it lets an off-limits sender's
#: mail back into the records. Nothing is written until the owner says yes.
#: cr1#1892 (decision 155e): the line names nobody. It keeps its ending, which the page's
#: `serverAsked` matches to count the next press as the second.
REOPEN_OFF_LIMITS_LINE = ("This sender is off limits, so their mail is kept out of your records. "
                          "This would file their mail into your records again. "
                          "Press it again to go ahead.")
#: cr1#1892 (decision 155e, the wording approved on 2026-11-26): an off-limits rule's row on the page.
OFF_LIMITS_RULE_LINE = "Mail from someone you put off limits"


def reopens_off_limits(workspace_root, choice: Dict[str, Any]) -> Optional[str]:
    """cr1#1230: the off-limits sender this press would let back into the
    records, or None. Two presses do: removing an off-limits rule, and a
    "show" rule on that sender (which replaces the hide, `add_rule`). The undo
    of "never filter this sender" (`remove` with `restore`, on the show rule)
    puts a hide back, so it reopens nothing; `restore` on the off-limits rule
    itself still removes it, so it is asked like any removal. Never raises."""
    try:
        verb = str(choice.get("action") or "").strip().lower()
        live = active_rules(load(workspace_root))
        if verb == "remove":
            rid = str(choice.get("rule_id") or "").strip()
            hit = next((r for r in live if r.get("id") == rid and r.get("off_limits")), None)
            return str(hit.get("value")) if hit else None
        if verb in ("teach", "add"):
            note = choice.get("note") or ""
            mode = str(choice.get("mode") or "").strip().lower() or mode_from_note(note)
            if mode != "show" or str(choice.get("match") or "").strip().lower() != "sender":
                return None
            addr = address_of(choice.get("value"))
            hit = next((r for r in live if r.get("off_limits") and r.get("mode") == "hide"
                        and r.get("match") == "sender" and stored_value(r) == addr), None)
            return addr if hit else None
    except Exception:  # noqa: BLE001
        return None
    return None


def apply_one(workspace_root, choice: Dict[str, Any], *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    verb = str(choice.get("action") or "").strip().lower()
    cid = str(choice.get("id") or "").strip() or None
    if choice.get("confirmed") is not True:
        reopened = reopens_off_limits(workspace_root, choice)
        if reopened:
            return {"outcome": "refused", "reason": "reopen_unconfirmed", "needs_confirm": True,
                    "line": REOPEN_OFF_LIMITS_LINE}
    if verb == "drop":
        return drop_thread(workspace_root, thread_id=choice.get("thread_id"),
                           message_ts=choice.get("message_ts"), title=choice.get("title"),
                           choice_id=cid, now_iso=now_iso)
    if verb in ("teach", "add"):
        note = choice.get("note") or ""
        mode = str(choice.get("mode") or "").strip().lower() or mode_from_note(note)
        return add_rule(workspace_root, mode=mode, match=choice.get("match"), value=choice.get("value"),
                        note=note, source="taught" if verb == "teach" else "manual",
                        from_thread=choice.get("thread_id") if verb == "teach" else None,
                        choice_id=cid, now_iso=now_iso)
    if verb == "remove":
        return remove_rule(workspace_root, rule_id_=choice.get("rule_id"), choice_id=cid, now_iso=now_iso,
                           restore=choice.get("restore") is True)
    if verb == "back":
        return bring_back(workspace_root, thread_id=choice.get("thread_id"), choice_id=cid, now_iso=now_iso)
    if verb == "keep":
        return keep_thread(workspace_root, thread_id=choice.get("thread_id"), title=choice.get("title"),
                           choice_id=cid, now_iso=now_iso)
    if verb == "unkeep":
        return unkeep_thread(workspace_root, thread_id=choice.get("thread_id"), choice_id=cid, now_iso=now_iso)
    return {"outcome": "refused", "line": UNKNOWN_VERB_LINE.format(verb=verb or "none")}


def apply_choices(workspace_root, choices: Optional[List[Dict[str, Any]]] = None, *,
                  now_iso: Optional[str] = None) -> Dict[str, Any]:
    """`{ok, results: [{id, outcome, line}]}`, one result per press, in order."""
    results = []
    for c in choices or []:
        if not isinstance(c, dict):
            continue
        try:
            res = apply_one(workspace_root, c, now_iso=now_iso)
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the rest
            res = {"outcome": "error", "line": STORE_UNREADABLE_LINE, "error": f"{type(exc).__name__}: {exc}"}
        res = dict(res)
        res["id"] = c.get("id")
        results.append(res)
    return {"ok": True, "results": results}


def feed(workspace_root, *, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """READ. The rules in force and the threads dropped, each with one plain
    line, for a surface to list with a Remove or Bring back on each row."""
    store = load(workspace_root)
    rules = []
    for r in active_rules(store):
        if r.get("off_limits"):
            # cr1#1892 (decision 155e): an off-limits rule names nobody on the page. No `value` and no
            # `note` (the owner's own words can name them); its line is the approved one. Who is off
            # limits stays visible as names on People's "Not shown here" line.
            # Decision 155k (S3): `value` is sent empty, so a released v6.12.5 page that formats
            # it into its Remove banner reads an empty name, never "undefined".
            rules.append({"id": r.get("id"), "mode": r.get("mode"), "match": r.get("match"), "value": "",
                          "off_limits": True, "plain": OFF_LIMITS_RULE_LINE,
                          "source": r.get("source") or "manual", "added_at": r.get("added_at")})
            continue
        rules.append({"id": r.get("id"), "mode": r.get("mode"), "match": r.get("match"),
                      "value": r.get("value"), "note": r.get("note") or "", "plain": plain(r),
                      "off_limits": bool(r.get("off_limits")),
                      "source": r.get("source") or "manual", "added_at": r.get("added_at")})
    rules.sort(key=lambda r: str(r.get("added_at") or ""), reverse=True)
    dropped = [{"thread_id": d.get("thread_id"), "title": d.get("title") or "",
                "dropped_at": d.get("dropped_at"), "message_ts": d.get("message_ts")}
               for d in store.get("dropped") or [] if isinstance(d, dict) and not d.get("back_at")]
    dropped.sort(key=lambda d: str(d.get("dropped_at") or ""), reverse=True)
    kept = [{"thread_id": k.get("thread_id"), "title": k.get("title") or "", "kept_at": k.get("kept_at")}
            for k in store.get("kept") or [] if isinstance(k, dict) and not k.get("unkept_at")]
    kept.sort(key=lambda k: str(k.get("kept_at") or ""), reverse=True)
    held = None
    if is_held(store):
        # review round 1, should-fix 6: Settings shows why the list is empty.
        copy = last_readable_copy(workspace_root)
        held = {"line": held_line(workspace_root),
                "copy_taken_at": (copy or {}).get("taken_at"), "copy_n": (copy or {}).get("n")}
    return {"generated_at": _iso(_now(now_iso)), "held": held, "rules": rules, "dropped": dropped[:100],
            "kept": kept[:100], "n_kept": len(kept),
            "n_rules": len(rules), "n_dropped": len(dropped),
            "matches": list(MATCHES), "kinds": list(KINDS), "modes": list(MODES)}


__all__ = [
    "STORE_REL", "MODES", "MATCHES", "KINDS", "VERBS", "load", "store_path",
    "address_of", "domain_of", "subject_words", "mode_from_note", "plain", "rule_id", "stored_value",
    "item_facts", "verdict", "sort_items", "active_rules",
    "add_rule", "remove_rule", "drop_thread", "bring_back", "keep_thread", "unkeep_thread",
    "apply_one", "apply_choices", "feed",
    "REOPEN_OFF_LIMITS_LINE", "OFF_LIMITS_RULE_LINE", "reopens_off_limits",
    "is_held", "off_limits_count", "last_readable_copy", "held_line",
    "set_aside_if_unreadable", "restore_off_limits",
]
