// ---- voice checks (generated from shared/scripts/voice_tell_detector.py; do not edit) ----
// The email voice check of shared/scripts/voice_tell_detector.py (scan_text with
// context "email" and a kind, and scan_subject), for the page's own drafts
// (cr1#1812, ruling 135a). Written by scripts/dev/gen_hub_voice_checks.py.
var pwVoice = (function () {
  var T = {"fail_rules":[{"rule":"opener_happy_to","re":{"source":"\\bI['\u2019]d\\s+be\\s+happy\\s+to","flags":"i"},"hint":"open with the action, not your eagerness to do it"},{"rule":"opener_love_to","re":{"source":"\\bI['\u2019]d\\s+love\\s+to","flags":"i"},"hint":"state what you'll do, not that you'd love to"},{"rule":"opener_happy_help","re":{"source":"\\bHappy\\s+to\\s+help","flags":"i"},"hint":"drop it \u2014 lead with the substance"},{"rule":"opener_great_question","re":{"source":"\\bGreat\\s+question","flags":"i"},"hint":"answer the question; don't praise it"},{"rule":"opener_great_point","re":{"source":"\\bThat['\u2019]s\\s+a\\s+great\\s+point","flags":"i"},"hint":"engage the point directly, no preamble"},{"rule":"opener_thanks_reaching","re":{"source":"\\bThanks\\s+for\\s+reaching\\s+out","flags":"i"},"hint":"open on the topic unless a thank-you is genuinely apt"},{"rule":"filler_let_me_know","re":{"source":"\\bLet\\s+me\\s+know\\s+if","flags":"i"},"hint":"make the ask concrete or cut it"},{"rule":"filler_feel_free","re":{"source":"\\bFeel\\s+free\\s+to","flags":"i"},"hint":"say plainly what they can do"},{"rule":"filler_hope_helps","re":{"source":"\\bI\\s+hope\\s+this\\s+helps","flags":"i"},"hint":"trust the content; cut the hedge"},{"rule":"filler_finds_well","re":{"source":"\\bhope\\s+this(?:\\s+\\w+){0,2}\\s+finds\\s+you\\s+well","flags":"i"},"hint":"cut it unless the CEO demonstrably uses it (allow_phrases)"},{"rule":"filler_dont_hesitate","re":{"source":"\\bPlease\\s+don['\u2019]t\\s+hesitate\\s+to","flags":"i"},"hint":"say 'email me' / 'call me' directly"},{"rule":"filler_circling_back","re":{"source":"\\bCircling\\s+back\\s+on","flags":"i"},"hint":"say 'following up on [X]'"},{"rule":"filler_wanted_circle","re":{"source":"\\bI\\s+wanted\\s+to\\s+circle\\s+back","flags":"i"},"hint":"say 'following up on [X]'"},{"rule":"filler_check_in","re":{"source":"\\bJust\\s+wanted\\s+to\\s+check\\s+in","flags":"i"},"hint":"state the actual reason for the message"},{"rule":"filler_touching_base","re":{"source":"\\bTouching\\s+base","flags":"i"},"hint":"name the specific thing you're following up on"},{"rule":"filler_per_last_email","re":{"source":"\\bAs\\s+per\\s+my\\s+last\\s+email","flags":"i"},"hint":"restate the point plainly without the jab"},{"rule":"preamble_heres_draft","re":{"source":"\\bHere['\u2019]s\\s+a\\s+draft","flags":"i"},"hint":"deliver the content; drop the framing"},{"rule":"preamble_heres_came_up","re":{"source":"\\bHere['\u2019]s\\s+what\\s+I\\s+came\\s+up\\s+with","flags":"i"},"hint":"deliver the content; drop the framing"},{"rule":"preamble_below_draft","re":{"source":"\\bBelow\\s+is\\s+a\\s+draft\\s+for\\s+your\\s+review","flags":"i"},"hint":"deliver the content; drop the framing"},{"rule":"preamble_put_together","re":{"source":"\\bI['\u2019]ve\\s+put\\s+together","flags":"i"},"hint":"deliver the content; drop the framing"},{"rule":"closer_best_regards","re":{"source":"\\bBest\\s+regards","flags":"i"},"hint":"use the CEO's calibrated sign-off (or none)"},{"rule":"closer_warm_regards","re":{"source":"\\bWarm\\s+regards","flags":"i"},"hint":"use the CEO's calibrated sign-off (or none)"},{"rule":"closer_looking_forward","re":{"source":"\\bLooking\\s+forward\\s+to\\s+hearing\\s+back","flags":"i"},"hint":"cut unless it's in the Voice Block"},{"rule":"vocab_ecosystem","re":{"source":"\\becosystem","flags":"i"},"hint":"corporate vocabulary \u2014 the calibrated voice never says this; use the plain word"},{"rule":"vocab_synergy","re":{"source":"\\bsynergy","flags":"i"},"hint":"corporate vocabulary \u2014 the calibrated voice never says this; use the plain word"},{"rule":"vocab_leverage","re":{"source":"\\bleverage","flags":"i"},"hint":"corporate vocabulary \u2014 the calibrated voice never says this; use the plain word"},{"rule":"vocab_holistic","re":{"source":"\\bholistic","flags":"i"},"hint":"corporate vocabulary \u2014 the calibrated voice never says this; use the plain word"},{"rule":"vocab_stakeholder","re":{"source":"\\bstakeholder","flags":"i"},"hint":"corporate vocabulary \u2014 the calibrated voice never says this; use the plain word"}],"time":{"source":"\\b\\d{1,2}:\\d{2}\\b","flags":""},"colon_sep":{"source":"(?<=[A-Za-z])\\s*:\\s+(?=[A-Za-z])","flags":""},"sentence_split":{"source":"[.!?]+\\s+","flags":""},"hedge":{"source":"\\b(?:i\\s+think|might|perhaps|possibly|could\\s+potentially|it\\s+may\\s+be)\\b","flags":"i"},"bullet_line":{"source":"^\\s*(?:[-*\u2022]|\\d+[.)])\\s+\\S","flags":""},"bullet_marker":{"source":"^\\s*(?:[-*\u2022]|\\d+[.)])\\s+","flags":""},"dash":{"source":"\u2014|\u2013|\\s-\\s","flags":""},"signoff":{"source":"^\\s*[\u2014\u2013]\\s*[A-Z][\\w.'-]*(?:\\s+[A-Z][\\w.'-]*){0,2}\\s*$","flags":""},"blank":{"source":"\\[(?!\\s*sic\\s*\\])[^\\[\\]\\n]*\\p{L}[^\\[\\]\\n]*\\](?!\\s*\\(\\s*(?:https?://|mailto:|www\\.|/|#)|\\s*:\\s*(?:https?://|mailto:|www\\.|/|#))|\\[\\s*(?:_{2,}|\\.{3,}|\\u2026)\\s*\\]|\\{\\{?\\s*\\p{L}[^{}\\n]*\\}\\}?|<(?!/|(?:br|p|b|i|u|em|strong|div|span|a|li|ul|ol|hr|tr|td|th|table|tbody|html|body|head)\\b)\\s*\\p{L}[\\p{L}\\p{N}_ \\-]{0,40}>","flags":"iu"},"reply_prefix":{"source":"^\\s*(?:(?:re|fwd?|aw|wg|sv|tr|antw|rif|vs)\\s*(?:\\[\\d+\\])?\\s*:\\s*)+","flags":"i"},"hints":{"blank":"fill in the bracketed blank or cut the line: a draft never carries a placeholder","dash":"no dashes as punctuation \u2014 use a comma, a colon, or rewrite (the \u201c\u2014 Name\u201d sign-off is exempt)","tri_colon":"rewrite the colon-chained clauses as prose where prose fits","hedging":"commit or cut \u2014 stacked hedges read as evasive","bullets":"the CEO would write this as prose \u2014 collapse the bullets","subject_dash":"no dashes as punctuation in subject lines \u2014 use a comma, a colon, or rewrite"},"list_kinds":["next_steps","proposal"],"list_rules":["structural_bullets_in_email","structural_tri_colon"]};
  function rx(t, extra) { return new RegExp(t.source, t.flags + (extra || "")); }
  function quoted(line) {
    var s = String(line).replace(/^\s+/, "");
    if (!s) return false;
    return s.charAt(0) === ">" || '"“”«'.indexOf(s.charAt(0)) >= 0;
  }
  function norm(p) { return String(p || "").trim().toLowerCase().replace(/\s+/g, " ").replace(/’/g, "'"); }
  function allowList(a) { var out = []; (a || []).forEach(function (p) { var n = norm(p); if (n) out.push(n); }); return out; }
  function allowed(m, list) {
    if (!list.length) return false;
    var x = norm(m);
    for (var i = 0; i < list.length; i++) if (list[i].indexOf(x) >= 0 || x.indexOf(list[i]) >= 0) return true;
    return false;
  }
  function paragraphs(text) {
    var lines = String(text).split("\n"), out = [], buf = [], start = null;
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].trim() === "") { if (buf.length) { out.push([start, buf.join("\n")]); buf = []; start = null; } }
      else { if (start === null) start = i + 1; buf.push(lines[i]); }
    }
    if (buf.length) out.push([start, buf.join("\n")]);
    return out;
  }
  function find(f, rule, severity, lineNo, line, match, hint) {
    f.push({ rule: rule, severity: severity, line_no: lineNo, line: String(line).trim(), match: match, hint: hint });
  }
  function phrases(text, list, skipQuoted) {
    var f = [], lines = String(text).split("\n");
    for (var n = 0; n < lines.length; n++) {
      if (skipQuoted && quoted(lines[n])) continue;
      for (var r = 0; r < T.fail_rules.length; r++) {
        var re = rx(T.fail_rules[r].re, "g"), m;
        while ((m = re.exec(lines[n])) !== null) {
          if (m[0] === "") { re.lastIndex++; continue; }
          if (!allowed(m[0], list)) find(f, T.fail_rules[r].rule, "fail", n + 1, lines[n], m[0], T.fail_rules[r].hint);
        }
      }
    }
    return f;
  }
  function count(re, s) { var g = rx(re, "g"), n = 0; while (g.exec(s) !== null) n++; return n; }
  function structural(text, list, skipQuoted, banDashes) {
    var f = [], ps = paragraphs(text);
    for (var p = 0; p < ps.length; p++) {
      var start = ps[p][0], para = ps[p][1], lines = para.split("\n"), first = lines[0];
      if (skipQuoted && quoted(first)) continue;
      if (banDashes) {
        for (var o = 0; o < lines.length; o++) {
          var line = lines[o];
          if (skipQuoted && quoted(line)) continue;
          if (rx(T.signoff).test(line)) continue;
          if (list.length && allowed(line, list)) continue;
          var scan = line.replace(rx(T.bullet_marker), ""), d = rx(T.dash, "g"), m;
          while ((m = d.exec(scan)) !== null) find(f, "dash_as_punctuation", "fail", start + o, line, m[0], T.hints.dash);
        }
      }
      var noTime = para.replace(rx(T.time, "g"), " ");
      if (count(T.colon_sep, noTime) >= 2)
        find(f, "structural_tri_colon", "warn", start, first, "tri-colon construction", T.hints.tri_colon);
      var sentences = para.split(rx(T.sentence_split));
      for (var s = 0; s < sentences.length; s++) {
        if (count(T.hedge, sentences[s]) >= 2) {
          f.push({ rule: "structural_hedging_stack", severity: "warn", line_no: start,
                   line: sentences[s].trim().slice(0, 120), match: "hedging stack", hint: T.hints.hedging });
          break;
        }
      }
      var bl = lines.filter(function (ln) { return rx(T.bullet_line).test(ln); });
      if (bl.length >= 2)
        find(f, "structural_bullets_in_email", "warn", start, bl[0], bl.length + " bullet lines in email body", T.hints.bullets);
    }
    return f;
  }
  function blanks(text, skipQuoted) {
    var f = [], lines = String(text || "").split("\n");
    for (var n = 0; n < lines.length; n++) {
      // review round 1 of #1813: only a line quoted with ">" (the mail replied to) is skipped
      if (skipQuoted && /^\s*>/.test(lines[n])) continue;
      var re = rx(T.blank, "g"), m;
      while ((m = re.exec(lines[n])) !== null) find(f, "bracketed_blank", "fail", n + 1, lines[n], m[0], T.hints.blank);
    }
    return f;
  }
  function verdict(f) {
    if (f.some(function (x) { return x.severity === "fail"; })) return "fail";
    if (f.some(function (x) { return x.severity === "warn"; })) return "warn";
    return "pass";
  }
  function normKind(k) { return String(k || "").trim().toLowerCase().replace(/-/g, "_").replace(/ /g, "_"); }
  function scanText(text, opts) {
    opts = opts || {};
    text = text == null ? "" : String(text);
    var skip = opts.skipQuoted !== false, list = allowList(opts.allowPhrases);
    var f = phrases(text, list, skip);
    var st = structural(text, list, skip, opts.banDashes !== false);
    if (T.list_kinds.indexOf(normKind(opts.kind)) >= 0)
      st = st.filter(function (x) { return T.list_rules.indexOf(x.rule) < 0; });
    f = f.concat(st).concat(blanks(text, skip));
    return { verdict: verdict(f), findings: f };
  }
  function scanSubject(subject, opts) {
    opts = opts || {};
    if (!subject) return { verdict: "pass", findings: [] };
    var f = phrases(subject, allowList(opts.allowPhrases), false), d = rx(T.dash, "g"), m;
    while ((m = d.exec(subject)) !== null) find(f, "subject_dash", "fail", 1, subject, m[0], T.hints.subject_dash);
    f = f.concat(blanks(drafted(subject, opts.inbound), false));
    return { verdict: verdict(f), findings: f };
  }
  // review round 1 of #1813 (fix checks): what the draft wrote of a subject; the
  // reply or forward prefix taken off, the inbound subject (its own prefixes off
  // too) taken out once, directly after the prefix, spaces folded, a subject cut
  // short matched as its start; the rest scanned (as _drafted_subject)
  function fold(v) { return String(v || "").split(/\s+/).filter(Boolean).join(" "); }
  function drafted(subject, inbound) {
    var s = fold(String(subject || "").replace(rx(T.reply_prefix), ""));
    var c = fold(String(inbound || "").replace(rx(T.reply_prefix), ""));
    if (c) {
      if (s.slice(0, c.length).toLowerCase() === c.toLowerCase()) s = s.slice(c.length);
      else if (c.slice(0, s.length).toLowerCase() === s.toLowerCase()) s = "";
    }
    return s;
  }
  return { scanText: scanText, scanSubject: scanSubject, LIST_KINDS: T.list_kinds.slice() };
})();
// ---- end voice checks
