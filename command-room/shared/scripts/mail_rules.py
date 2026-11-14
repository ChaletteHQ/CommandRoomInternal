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

try:
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from atomic_write import acquire_write_lock, atomic_write_json, release_write_lock  # type: ignore

STORE_REL = Path("_hq") / "data" / "mail-rules.json"
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
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"mail rules unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise ValueError("mail rules unreadable: not an object")
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
    there is none."""
    s = str(text or "")
    m = re.search(r"<([^<>@\s]+@[^<>\s]+)>", s)
    addr = m.group(1) if m else s.strip()
    addr = addr.strip().strip("<>").lower()
    return addr if re.fullmatch(r"[^@\s,;]+@[a-z0-9.-]+\.[a-z0-9-]+", addr) else ""


def domain_of(text: Any) -> str:
    addr = address_of(text)
    if addr:
        return addr.rsplit("@", 1)[1]
    d = str(text or "").strip().lower().lstrip("@").rstrip(".")
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
    domain = str(item.get("domain") or "").strip().lower() or (sender.rsplit("@", 1)[1] if sender else "")
    return {"sender": sender, "domain": domain,
            "subject": set(subject_words(item.get("subject") or "")),
            "kind": str(item.get("noise_class") or "").strip().lower().replace("-", "_"),
            "thread_id": str(item.get("thread_id") or "").strip(),
            "ts": _parse(item.get("ts") or item.get("message_ts"))}


def _matches(rule: Dict[str, Any], facts: Dict[str, Any]) -> bool:
    match, value = rule.get("match"), str(rule.get("value") or "")
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
        result, changed = mutate(store)
        if changed:
            atomic_write_json(path, store)
        return result
    finally:
        release_write_lock(lock)


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
        if any(r.get("id") == rid for r in live):
            held = next(r for r in live if r.get("id") == rid)
            if off_limits and not held.get("off_limits"):
                held["off_limits"] = True
                return {"outcome": "applied", "line": ADDED_LINE.format(plain=plain(held)),
                        "rule": held, "flagged": True}, True
            return {"outcome": "already", "line": ALREADY_LINE, "rule": held}, False
        if len(live) >= RULES_CAP:
            return {"outcome": "refused", "line": FULL_LINE.format(n=len(live))}, False
        other = rule_id("show" if mode == "hide" else "hide", match, value)
        replaced = None
        for r in live:
            if r.get("id") == other:
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

    return _write(workspace_root, mutate)


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
REOPEN_OFF_LIMITS_LINE = ("{value} is off limits, so their mail is kept out of your records. "
                          "This would file their mail into your records again. "
                          "Press it again to go ahead.")


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
                        and r.get("match") == "sender" and r.get("value") == addr), None)
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
                    "line": REOPEN_OFF_LIMITS_LINE.format(value=reopened)}
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
    return {"generated_at": _iso(_now(now_iso)), "rules": rules, "dropped": dropped[:100],
            "kept": kept[:100], "n_kept": len(kept),
            "n_rules": len(rules), "n_dropped": len(dropped),
            "matches": list(MATCHES), "kinds": list(KINDS), "modes": list(MODES)}


__all__ = [
    "STORE_REL", "MODES", "MATCHES", "KINDS", "VERBS", "load", "store_path",
    "address_of", "domain_of", "subject_words", "mode_from_note", "plain", "rule_id",
    "item_facts", "verdict", "sort_items", "active_rules",
    "add_rule", "remove_rule", "drop_thread", "bring_back", "keep_thread", "unkeep_thread",
    "apply_one", "apply_choices", "feed",
    "REOPEN_OFF_LIMITS_LINE", "reopens_off_limits",
]
