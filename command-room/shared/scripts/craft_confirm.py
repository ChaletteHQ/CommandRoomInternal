#!/usr/bin/env python3
"""Craft-capture's confirm step and held-out test as two Command Room pages (cr1#1649).

WHY THIS EXISTS
---------------
The owner asked, 2026-11-22: "for craft knowledge, when it has you confirm,
make it an html". Until now craft-capture's Step 4 said the preview card in
chat and waited for the owner to TYPE confirm, edit or dismiss per rule, and
Step 5 showed the held-out test as text. This module makes both steps pages:

  * the CONFIRM page (`widget_mode` `craft_confirm`): each proposed rule with
    its evidence and Keep / Edit (an inline box holding the rule text) /
    Dismiss; the privacy notice, word for word; an optional answer box per
    question; and ONE Save. Save sends a single message, `craft choices: `
    followed by a JSON object, carrying every decision and answer. Nothing is
    saved before the press, and a page with no rule kept cannot save.
  * the TEST page (`widget_mode` `craft_test`): the held-out artifact as it
    was and with their method, side by side, each change marked with the key
    of the rule that made it, and the test question with yes / partly / no as
    three buttons. A press sends `craft verdict: ` followed by a JSON object.

`write_craft` (craft_capture) reads those two messages itself
(`confirm_reply`, `verdict_reply`), so Step 6 writes exactly what was pressed:
the model hands the messages over as they arrived and never re-types a
decision.

THE NOTICE IS THIS MODULE'S CONSTANT, NEVER THE PREVIEW'S
---------------------------------------------------------
The preview comes back through the chat, so its `notice` is not trusted. The
page prints `craft_capture.NOTICE`, the one text, and the renderer refuses a
page that does not carry it word for word.

THE TWO MESSAGES CARRY THE CAPTURE'S ID
---------------------------------------
`capture_id(preview)` hashes where the file goes, the rules (key, text,
section) and the questions (key, text). A message from a page made for
another preview (a stale page, or one for another capture) is refused, and
nothing is written.

The pages are rendered by `chat_output_renderer.render_chat_output_widget`
(which dispatches the two modes here) and reach the screen only through
`widget_transport.render_and_persist`, like every Command Room widget. The two
door writers below land the audit page under `_hq/.system/widgets/` and hand
back the html to relay byte-exact. Its look is the shared widget CSS (the
Evening look, cr1#1634); its own few rules use only rgba() values taken from
that CSS (the brand gold and a faint white), never a hex colour, so the
stray-palette pin does not move.

PUBLIC API
  capture_id(preview) -> str
  confirm_view(preview) -> dict                 the confirm page's data view
  test_view(preview, confirm_reply, test) -> dict   the test page's data view
  render_confirm_html(view, wrapper) -> str     called by the renderer only
  render_test_html(view, wrapper) -> str        called by the renderer only
  read_confirm_reply(preview, text) -> dict     {ok, decisions, answers}
  read_verdict_reply(preview, text) -> dict     {ok, verdict}
  render_confirm_page(workspace_root, preview) -> dict    WRITER (audit page)
  render_test_page(workspace_root, preview, confirm_reply, test) -> dict

stdlib only.
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import os
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import craft_capture as _cap  # noqa: E402

MODE_CONFIRM = "craft_confirm"
MODE_TEST = "craft_test"
SOURCE_SKILL = "craft-capture"
CONFIRM_PREFIX = "craft choices: "
VERDICT_PREFIX = "craft verdict: "
VERDICTS = ("yes", "partly", "no")
#: The page's own verbs, and what each becomes in write_craft's decisions.
PAGE_VERBS = ("keep", "edit", "dismiss")
VERB_LABELS = {"keep": "Keep", "edit": "Edit", "dismiss": "Dismiss"}
VERDICT_LABELS = {"yes": "Yes", "partly": "Partly", "no": "No"}

_WHERE = {"Rules": "", "Order of work": " (a step in their order of work)",
          "What they never do": " (something they never do)"}

#: Said when a message does not belong to this preview, or is not one.
ALREADY_SAVED_LINE = ("Those choices were saved already, so nothing was saved again. "
                      "Capture it again to change the file.")
STALE_LINE = ("Those choices came from a page made for another capture, so "
              "nothing was saved. Capture it again.")
NOT_A_REPLY_LINE = ("Those choices did not come from the confirm page, so "
                    "nothing was saved.")
NOTHING_KEPT_LINE = "Nothing was kept, so nothing was saved."
EMPTY_EDIT_LINE = ("An edited rule was left empty, so nothing was saved. Edit "
                   "it again or dismiss it.")
BAD_VERDICT_LINE = ("That answer did not come from the test page, so the test "
                    "was not recorded and nothing was saved.")


def _t(text) -> str:
    """A text node: escaped, the apostrophe kept, so a sentence the page must
    print word for word (the notice) is in the html as written."""
    return _html.escape(str(text if text is not None else ""), quote=False)


def _a(text) -> str:
    """An attribute value."""
    return _html.escape(str(text if text is not None else ""), quote=True)


def capture_id(preview) -> str:
    """The id both pages and both messages carry: where the file goes, the
    rules and the questions, hashed. A message carrying another id is from a
    page made for another preview."""
    if not isinstance(preview, dict):
        return ""
    body = {
        "target": preview.get("target"),
        "action": preview.get("action"),
        # review of #1688, M1: the file the preview was made from
        "base": preview.get("base"),
        # review round 1 of #1688: everything the page shows about a rule
        "rules": [[r.get("key"), r.get("text"), r.get("section"), r.get("amends"),
                   r.get("old_text"),
                   [e.get("ref") for e in r.get("evidence") or [] if isinstance(e, dict)]]
                  for r in preview.get("rules") or [] if isinstance(r, dict)],
        "questions": [[q.get("key"), q.get("text")]
                      for q in preview.get("questions") or []
                      if isinstance(q, dict)],
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# The confirm page
# ---------------------------------------------------------------------------

def _evidence_line(rule: dict) -> str:
    ev = "; ".join(f"{e.get('label') or 'an item'}"
                   + (f", {e['date']}" if e.get("date") else "")
                   for e in rule.get("evidence") or [] if isinstance(e, dict))
    return f"From {rule.get('basis') or 'what was gathered'}. Evidence: {ev}."


def confirm_view(preview) -> dict:
    """The confirm page's data view from the preview: one row per proposed
    rule (its key, words, where it sits, its evidence, what it replaces and
    whether the check on saving would refuse it as written), the questions,
    and the notice (the module's constant). Zero lines are left out."""
    pv = preview if isinstance(preview, dict) else {}
    name = pv.get("subject_name") or "they"
    label = pv.get("scope_label") or pv.get("scope") or ""
    head = _cap._title_phrase(name, label)
    rules = []
    for r in pv.get("rules") or []:
        if not isinstance(r, dict) or not r.get("key") or not r.get("evidence"):
            continue
        blocked = sorted({f.get("kind") for f in r.get("blocked") or []
                          if isinstance(f, dict) and f.get("kind")})
        rules.append({
            "key": r["key"],
            "text": r.get("text") or "",
            "where": _WHERE.get(r.get("section"), ""),
            "evidence": _evidence_line(r),
            "amends": (f"Replaces {r['amends']}, which says: {r.get('old_text') or ''}"
                       if r.get("amends") else ""),
            "blocked": (f"Needs an edit before it can be saved: it carries a "
                        f"{', '.join(blocked)}." if blocked else ""),
        })
    notes = []
    if pv.get("already_on_file"):
        nums = ", ".join(str(a.get("number")) for a in pv["already_on_file"]
                         if isinstance(a, dict))
        notes.append(f"Already in the file, so not proposed again: {nums}.")
    if pv.get("dropped"):
        notes.append(f"Left out: {len(pv['dropped'])} rule(s) with no "
                     "evidence to point at.")
    if pv.get("open_questions"):
        notes.append(f"{len(pv['open_questions'])} more question(s) go "
                     "straight to the file's open questions.")
    new = (pv.get("gather_inputs") or {}).get("new_scope")
    new_line = ""
    if isinstance(new, dict) and new.get("label"):
        new_line = (f"New kind of work: {new['label']}, read in "
                    + ", ".join(new.get("readers") or [])
                    + (f", under {_cap.cc.scope_label(new['parent'])}"
                       if new.get("parent") else "") + ".")
    consent = ""
    if pv.get("consent"):
        consent = (f"{name} is outside your company, so the file carries "
                   f"consent {pv['consent']}. It is a note, not a block.")
    return {
        "widget_mode": "craft_confirm",
        "source_skill": SOURCE_SKILL,
        "capture": capture_id(pv),
        "header": f"{head}: "
                  + ("changes to the file" if pv.get("action") == "amend"
                     else "a first draft"),
        "new_scope_line": new_line,
        "rules": rules,
        "notes": notes,
        "questions": [{"key": q["key"], "text": q.get("text") or ""}
                      for q in pv.get("questions") or []
                      if isinstance(q, dict) and q.get("key")],
        "notice": _cap.NOTICE,
        "consent_line": consent,
    }


_CONFIRM_CSS = """
.crc-line { margin: 2px 0 0 0; font-size: 12px; }
.crc-flag { margin: 4px 0 0 0; font-size: 12px; font-style: italic; }
.crc-notice { margin: 16px 0 0 0; padding: 10px 12px; border: 1px solid rgba(184,139,74,0.45); border-radius: 6px; background: rgba(184,139,74,0.08); font-size: 12px; line-height: 1.5; }
.crc-note { margin: 8px 0 0 0; font-size: 12px; }
.crc-q { margin: 10px 0 0 0; }
.crc-q textarea, .crc-confirm .cr-action-input textarea { width: 100%; margin-top: 6px; font: inherit; color: inherit; background: rgba(255,255,255,0.04); border: 1px solid rgba(184,139,74,0.3); border-radius: 6px; padding: 6px 8px; }
.crc-confirm button.cr-action:disabled { opacity: 0.45; cursor: not-allowed !important; }
"""

#: The confirm page's script. crcWire is the ONE place the message is made,
#: and crcSave the ONE place it is sent: no other handler calls crSendPrompt.
_CONFIRM_JS = """
var crcCard=document.querySelector('.crc-confirm');
var CRC_CAP=crcCard?crcCard.getAttribute('data-capture'):'';
var crcPick={};
function crcRules(){return Array.prototype.slice.call(document.querySelectorAll('.cr-item[data-rule]'));}
function crcEditBox(k){return document.querySelector('.cr-action-input[data-input-for-n="'+k+'"] textarea');}
function crcState(){var s={decisions:{},answers:{}};
crcRules().forEach(function(r){var k=r.getAttribute('data-rule'),a=crcPick[k];
if(a==='keep'){s.decisions[k]='confirm';}
else if(a==='dismiss'){s.decisions[k]='dismiss';}
else if(a==='edit'){var b=crcEditBox(k);s.decisions[k]={edit:((b&&b.value)||'').trim()};}});
document.querySelectorAll('textarea[data-question]').forEach(function(q){var v=(q.value||'').trim();s.answers[q.getAttribute('data-question')]=v?v:null;});
return s;}
function crcKept(s){var n=0;Object.keys(s.decisions).forEach(function(k){var d=s.decisions[k];if(d==='confirm'||(d&&typeof d==='object'&&d.edit)){n++;}});return n;}
function crcWire(cap,s){var empty=false;Object.keys(s.decisions).forEach(function(k){var d=s.decisions[k];if(d&&typeof d==='object'&&!d.edit){empty=true;}});
if(empty||crcKept(s)===0){return null;}
return 'craft choices: '+JSON.stringify({capture:cap,decisions:s.decisions,answers:s.answers});}
function crcTyped(s){var p=[];Object.keys(s.decisions).forEach(function(k){var d=s.decisions[k];
if(d==='confirm'){p.push('keep '+k);}else if(d==='dismiss'){p.push('dismiss '+k);}else{p.push('edit '+k+' to: '+JSON.stringify(d.edit));}});
Object.keys(s.answers).forEach(function(k){if(s.answers[k]){p.push('answer '+k+': '+JSON.stringify(s.answers[k]));}});
return p.join('; ')+'.';}
var crcSent=false;
function crcSync(){if(crcSent){return;}var s=crcState(),c=document.getElementById('cr-count'),b=document.getElementById('cr-apply');
if(c){c.textContent=String(crcKept(s));}
if(b){b.disabled=(crcWire(CRC_CAP,s)===null)||(typeof crSHeld==='function'&&crSHeld());}}
function crcPress(b){if(b.disabled||crcSent){return;}var k=b.getAttribute('data-n'),a=b.getAttribute('data-action');
var on=crcPick[k]!==a;
document.querySelectorAll('button.cr-action[data-n="'+k+'"]').forEach(function(x){x.classList.remove('cr-selected');});
if(on){crcPick[k]=a;b.classList.add('cr-selected');}else{delete crcPick[k];}
var w=document.querySelector('.cr-action-input[data-input-for-n="'+k+'"]');
if(w){w.style.display=(crcPick[k]==='edit')?'block':'none';}
crcSync();}
function crcSave(){if(crcSent){return;}var s=crcState(),w=crcWire(CRC_CAP,s);if(w===null){crcSync();return;}
crcSent=true;
document.querySelectorAll('.crc-confirm button.cr-action, .crc-confirm textarea, #cr-apply').forEach(function(x){x.disabled=true;});
crSendPrompt(w,crcTyped(s));
var crcB=document.getElementById('cr-apply');if(crcB){crcB.disabled=true;}}
// review of #1688: when the chat could not be reached, the ladder turns Save
// back on; this page has sent once, so Save stays off and the line to paste
// is the way on
var crcFail=crSendFailed;crSendFailed=function(t,c){crcFail(t,c);var b=document.getElementById('cr-apply');if(b&&crcSent){b.disabled=true;}};
// review round 3 of #1688: the ladder's retry turns Save on before it sends;
// once this page has sent, Save stays off whatever the ladder asks
var crcAp=crSApply;crSApply=function(x){crcAp(crcSent||x);};
document.querySelectorAll('.crc-confirm button.cr-action').forEach(function(b){b.addEventListener('click',function(){crcPress(b);});});
document.querySelectorAll('.crc-confirm textarea').forEach(function(t){t.addEventListener('input',crcSync);});
var crcSaveBtn=document.getElementById('cr-apply');
if(crcSaveBtn){crcSaveBtn.addEventListener('click',crcSave);}
crcSync();
"""


def _send_block() -> str:
    """The shared send ladder (WIDGETSEND1), sliced from the renderer's own
    minified script so this page never carries a copy of its own."""
    import chat_output_renderer as cor
    src = cor._JS_CORE_MIN
    start, end = "const crSf0=", "crSendFailed(p,c);}"
    i, j = src.find(start), src.find(end)
    if i < 0 or j < i:
        raise RuntimeError("the shared send ladder was not found in the "
                           "renderer's script")
    return src[i:j + len(end)]


def _assemble(content: str, css_extra: str, js: str, wrapper: str) -> str:
    import chat_output_renderer as cor
    css = cor._compose_widget_css(content) + cor._minify_css(css_extra)
    script = _send_block() + "\n" + js.strip()
    out = []
    if wrapper == "document":
        out.append('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">')
    out.append(f"<style>{css}</style>")
    if wrapper == "document":
        out.append("</head><body>")
    out.append(content)
    out.append(f"<script>{script}</script>")
    if wrapper == "document":
        out.append("</body></html>")
    return "".join(out)


def render_confirm_html(view: dict, wrapper: str = "fragment") -> str:
    """The confirm page's html from `confirm_view`. Refuses a view whose
    notice is not the constant, word for word, and a view with no rule."""
    import chat_output_renderer as cor
    if view.get("notice") != _cap.NOTICE:
        raise ValueError("the confirm page must carry the privacy notice "
                         "word for word")
    rules = view.get("rules") or []
    if not rules:
        raise ValueError("the confirm page needs at least one rule")
    p = [f'<div class="cr-card crc-confirm" data-capture="{_a(view.get("capture"))}">',
         '<div class="cr-brand-strip">' + cor._BRAND_LOGO_SVG + '</div>',
         f'<div class="cr-header">{_t(view.get("header"))}</div>']
    if view.get("new_scope_line"):
        p.append(f'<div class="cr-sub-header">{_t(view["new_scope_line"])}</div>')
    p.append('<div class="cr-body">')
    for r in rules:
        k = r["key"]
        p.append(f'<div class="cr-item" data-rule="{_a(k)}">')
        p.append('<div class="cr-item-head">'
                 f'<span class="cr-item-num"><strong>{_t(k)}.</strong></span> '
                 f'<span class="cr-item-name">{_t(r["text"])}</span>'
                 + (f'<span class="cr-item-annotation">{_t(r["where"])}</span>'
                    if r.get("where") else "")
                 + '</div>')
        p.append(f'<div class="crc-line"><span class="cr-item-context">{_t(r["evidence"])}</span></div>')
        if r.get("amends"):
            p.append(f'<div class="crc-line"><span class="cr-item-context">{_t(r["amends"])}</span></div>')
        if r.get("blocked"):
            p.append(f'<div class="crc-flag"><span class="cr-item-annotation">{_t(r["blocked"])}</span></div>')
        buttons = []
        for verb in PAGE_VERBS:
            itype = "textarea-text" if verb == "edit" else "none"
            off = " disabled" if verb == "keep" and r.get("blocked") else ""
            buttons.append(f'<button class="cr-action" type="button" data-n="{_a(k)}" '
                           f'data-action="{verb}" data-input-type="{itype}"{off}>'
                           f'{VERB_LABELS[verb]}</button>')
        p.append(f'<div class="cr-item-actions">{"".join(buttons)}</div>')
        p.append('<div class="cr-item-inputs">'
                 f'<div class="cr-action-input" data-input-for-n="{_a(k)}" '
                 'data-input-for-action="edit" data-input-type="textarea-text" '
                 'style="display:none;">'
                 f'<textarea class="cr-input-field" rows="3">{_t(r["text"])}</textarea>'
                 '</div></div>')
        p.append('</div>')
    for note in view.get("notes") or []:
        p.append(f'<div class="crc-note"><span class="cr-item-context">{_t(note)}</span></div>')
    questions = view.get("questions") or []
    if questions:
        p.append('<div class="cr-section-title">Questions</div>')
        for q in questions:
            p.append(f'<div class="crc-q"><span class="cr-item-num"><strong>{_t(q["key"])}.</strong></span> '
                     f'<span class="cr-item-name">{_t(q["text"])}</span>'
                     f'<textarea data-question="{_a(q["key"])}" rows="2" '
                     'placeholder="Your answer, if you have one"></textarea></div>')
    p.append(f'<div class="crc-notice">{_t(view["notice"])}</div>')
    if view.get("consent_line"):
        p.append(f'<div class="crc-note"><span class="cr-item-context">{_t(view["consent_line"])}</span></div>')
    p.append('</div>')
    p.append('<div class="cr-footer">'
             f'<div class="cr-counter">Kept <strong id="cr-count">0</strong> of {len(rules)}</div>'
             '<div class="cr-footer-actions">'
             '<button class="cr-btn-apply" id="cr-apply" type="button" disabled>Save</button>'
             '</div></div>')
    p.append('</div>')
    content = "".join(p)
    html = _assemble(content, _CONFIRM_CSS, _CONFIRM_JS, wrapper)
    if _t(_cap.NOTICE) not in html:
        raise ValueError("the privacy notice did not reach the page")
    cor.scan_rendered_html(html)
    return html


# ---------------------------------------------------------------------------
# The held-out test page
# ---------------------------------------------------------------------------

def test_view(preview, confirm_reply, test) -> dict:
    """The test page's data view. `confirm_reply` is the Save message (the
    rules kept); `test` is the model's work, `{"original": "<the held-out
    text as read>", "revised": [{"text": "...", "rule": "P1" or null}]}`,
    where every marked piece names a rule the owner kept. Returns the view,
    or {"ok": False, "refused", "line"}."""
    pv = preview if isinstance(preview, dict) else {}
    held = pv.get("held_out")
    if not isinstance(held, dict):
        return {"ok": False, "refused": "nothing_held_out",
                "line": pv.get("held_out_line") or "Nothing was held out to test on."}
    got = read_confirm_reply(pv, confirm_reply)
    if not got.get("ok"):
        return got
    kept = {k for k, d in got["decisions"].items() if d != "dismiss"}
    test = test if isinstance(test, dict) else {}
    original = test.get("original")
    revised = test.get("revised")
    if not isinstance(original, str) or not original.strip() \
            or not isinstance(revised, list) or not revised:
        return {"ok": False, "refused": "no_test",
                "line": "The test has no original or no result to show."}
    parts = []
    for piece in revised:
        if not isinstance(piece, dict) or not isinstance(piece.get("text"), str):
            return {"ok": False, "refused": "bad_piece",
                    "line": "A piece of the result is not text."}
        rule = piece.get("rule")
        if rule is not None and rule not in kept:
            return {"ok": False, "refused": "unkept_rule",
                    "line": f"A change is marked with {rule}, which was not kept."}
        parts.append({"text": piece["text"], "rule": rule})
    return {
        "widget_mode": "craft_test",
        "source_skill": SOURCE_SKILL,
        "capture": capture_id(pv),
        "header": "The held-out test",
        "sub_header": held.get("label") or "",
        "original": original,
        "revised": parts,
        "question": _cap.TEST_QUESTION,
    }


_TEST_CSS = """
.crc-sides { display: flex; flex-wrap: wrap; gap: 12px; }
.crc-side { flex: 1 1 300px; min-width: 0; }
.crc-text { white-space: pre-wrap; font-size: 12px; line-height: 1.5; padding: 8px 10px; border: 1px solid rgba(184,139,74,0.25); border-radius: 6px; }
.crc-chg { background: rgba(184,139,74,0.22); color: inherit; border-radius: 3px; padding: 0 2px; }
.crc-key { font-size: 10px; font-weight: 700; margin-left: 3px; vertical-align: super; }
.crc-ask { margin: 14px 0 0 0; }
"""

_TEST_JS = """
var crcCard=document.querySelector('.crc-test');
var CRC_CAP=crcCard?crcCard.getAttribute('data-capture'):'';
function crcVerdictWire(cap,v){if(['yes','partly','no'].indexOf(v)<0){return null;}
return 'craft verdict: '+JSON.stringify({capture:cap,verdict:v});}
function crcVerdict(b){var v=b.getAttribute('data-action'),w=crcVerdictWire(CRC_CAP,v);if(w===null){return;}
document.querySelectorAll('.crc-test button.cr-action').forEach(function(x){x.classList.toggle('cr-selected',x===b);x.disabled=true;});
crSendPrompt(w,v);}
document.querySelectorAll('.crc-test button.cr-action').forEach(function(b){b.addEventListener('click',function(){crcVerdict(b);});});
"""


def render_test_html(view: dict, wrapper: str = "fragment") -> str:
    """The test page's html from `test_view`: the two sides, each change
    marked with its rule key, the question word for word, three buttons."""
    import chat_output_renderer as cor
    if view.get("question") != _cap.TEST_QUESTION:
        raise ValueError("the test page must ask the test question word for word")
    revised = []
    for piece in view.get("revised") or []:
        if piece.get("rule"):
            revised.append(f'<mark class="crc-chg">{_t(piece["text"])}'
                           f'<span class="crc-key">{_t(piece["rule"])}</span></mark>')
        else:
            revised.append(_t(piece["text"]))
    # The single-press card element is exactly the renderer's, so the
    # validator's exemption from the batch footer reads it; the page's own
    # class and its id sit on the element inside.
    p = ['<div class="cr-card cr-card-single">',
         f'<div class="crc-test" data-capture="{_a(view.get("capture"))}">',
         '<div class="cr-brand-strip">' + cor._BRAND_LOGO_SVG + '</div>',
         f'<div class="cr-header">{_t(view.get("header"))}</div>']
    if view.get("sub_header"):
        p.append(f'<div class="cr-sub-header">{_t(view["sub_header"])}</div>')
    p.append('<div class="cr-body"><div class="crc-sides">'
             '<div class="crc-side"><div class="cr-section-title">Original</div>'
             f'<div class="crc-text">{_t(view.get("original"))}</div></div>'
             '<div class="crc-side"><div class="cr-section-title">With their method</div>'
             f'<div class="crc-text">{"".join(revised)}</div></div></div>')
    p.append(f'<div class="crc-ask"><span class="cr-item-name">{_t(view["question"])}</span></div>')
    buttons = "".join(f'<button class="cr-action" type="button" data-n="verdict" '
                      f'data-action="{v}" data-input-type="none">{VERDICT_LABELS[v]}</button>'
                      for v in VERDICTS)
    p.append(f'<div class="cr-item-actions">{buttons}</div>')
    p.append('</div></div></div>')
    content = "".join(p)
    html = _assemble(content, _TEST_CSS, _TEST_JS, wrapper)
    cor.scan_rendered_html(html)
    return html


# ---------------------------------------------------------------------------
# The two messages
# ---------------------------------------------------------------------------

def _parse(text, prefix: str):
    if not isinstance(text, str):
        return None
    body = text.strip()
    if not body.startswith(prefix.strip()):
        return None
    try:
        obj = json.loads(body[len(prefix.strip()):].strip())
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def read_confirm_reply(preview, text) -> dict:
    """The Save message, read against the preview it was made for. Answers
    {"ok": True, "decisions", "answers"} in write_craft's shape (keep is
    confirm; an edit carries the owner's words), or {"ok": False, "refused",
    "line"}. Refuses a message that is not the page's, one made for another
    preview, a key the preview does not have, an empty edit, and one with no
    rule kept."""
    obj = _parse(text, CONFIRM_PREFIX)
    if obj is None:
        return {"ok": False, "refused": "not_a_reply", "line": NOT_A_REPLY_LINE}
    if obj.get("capture") != capture_id(preview):
        return {"ok": False, "refused": "stale", "line": STALE_LINE}
    rule_keys = {r.get("key") for r in (preview or {}).get("rules") or []
                 if isinstance(r, dict)}
    q_keys = {q.get("key") for q in (preview or {}).get("questions") or []
              if isinstance(q, dict)}
    raw_d = obj.get("decisions")
    raw_a = obj.get("answers") if obj.get("answers") is not None else {}
    if not isinstance(raw_d, dict) or not isinstance(raw_a, dict):
        return {"ok": False, "refused": "not_a_reply", "line": NOT_A_REPLY_LINE}
    decisions = {}
    for key, d in raw_d.items():
        if key not in rule_keys:
            return {"ok": False, "refused": "stale", "line": STALE_LINE}
        if d in ("confirm", "dismiss"):
            decisions[key] = d
        elif isinstance(d, dict) and set(d) == {"edit"} \
                and isinstance(d["edit"], str):
            words = d["edit"].strip()
            if not words:
                return {"ok": False, "refused": "empty_edit",
                        "line": EMPTY_EDIT_LINE}
            decisions[key] = {"edit": words}
        else:
            return {"ok": False, "refused": "not_a_reply",
                    "line": NOT_A_REPLY_LINE}
    if not any(d != "dismiss" for d in decisions.values()):
        return {"ok": False, "refused": "nothing_kept", "line": NOTHING_KEPT_LINE}
    answers = {}
    for key, v in raw_a.items():
        if key not in q_keys:
            return {"ok": False, "refused": "stale", "line": STALE_LINE}
        if v is not None and not isinstance(v, str):
            return {"ok": False, "refused": "not_a_reply",
                    "line": NOT_A_REPLY_LINE}
        answers[key] = (v.strip() or None) if isinstance(v, str) else None
    return {"ok": True, "decisions": decisions, "answers": answers}


def read_verdict_reply(preview, text) -> dict:
    """The test page's message: {"ok": True, "verdict"} or a refusal."""
    obj = _parse(text, VERDICT_PREFIX)
    if obj is None or obj.get("verdict") not in VERDICTS:
        return {"ok": False, "refused": "not_a_verdict", "line": BAD_VERDICT_LINE}
    if obj.get("capture") != capture_id(preview):
        return {"ok": False, "refused": "stale", "line": STALE_LINE}
    return {"ok": True, "verdict": obj["verdict"]}


# ---------------------------------------------------------------------------
# The two door writers (the audit page is their only write)
# ---------------------------------------------------------------------------

def _transport(ws: Path, view: dict, name_hint: str) -> dict:
    from widget_transport import render_and_persist, WIDGET_PAGE_BYTE_BUDGET
    transport = render_and_persist(
        data_view=view, wrapper="fragment",
        persist_dir=str(ws / "_hq" / ".system" / "widgets"), name_hint=name_hint)
    path = transport.get("path")
    try:
        page_rel = Path(path).resolve().relative_to(ws.resolve()).as_posix() \
            if path else None
    except ValueError:
        page_rel = None
    html = transport.get("html") or ""
    return {"ok": True, "html": html, "page_rel": page_rel,
            "capture": view.get("capture"),
            "over_budget": len(html) > WIDGET_PAGE_BYTE_BUDGET}


def render_confirm_page(workspace_root, preview) -> dict:
    """WRITER (the write door only; cr1#1649). Step 4's confirm page:
    `confirm_view` rendered and validated by `widget_transport.
    render_and_persist`, its audit page landed under `_hq/.system/widgets/`.
    Answers {ok, html, page_rel, capture, over_budget}: `html` goes to
    show_widget verbatim. Writes nothing else: no craft file, no event."""
    ws = Path(os.path.abspath(str(workspace_root)))
    if not isinstance(preview, dict) or not preview.get("ok"):
        return {"ok": False, "refused": "no_preview",
                "line": "There is no preview to confirm, so no page was made."}
    view = confirm_view(preview)
    if not view["rules"]:
        return {"ok": False, "refused": "no_rules",
                "line": "No rule reached the preview, so there is nothing to confirm."}
    return _transport(ws, view, "craft-confirm")


def render_test_page(workspace_root, preview, confirm_reply, test) -> dict:
    """WRITER (the write door only; cr1#1649). Step 5's held-out test page
    from the Save message and the model's result. Answers {ok, html,
    page_rel, capture, over_budget} or a refusal. Writes nothing but the
    audit page."""
    ws = Path(os.path.abspath(str(workspace_root)))
    view = test_view(preview, confirm_reply, test)
    if view.get("ok") is False:
        return view
    return _transport(ws, view, "craft-test")
