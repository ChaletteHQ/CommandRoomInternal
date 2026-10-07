# Corrections in passing — the shared contract (SPEC_SURFACES2 §1)

Every skill listed in `correction_turn.CONTRACT_SKILLS` carries the paragraph
below, verbatim, inside its own SKILL.md. The paragraph is the instruction
layer; `shared/scripts/correction_turn.py` is the code behind it. Neither is
sufficient alone: a model that is not told to call the module will not call
it, and a paragraph with no module behind it is a promise nothing keeps.

## Why this exists

People correct constantly while asking for something else — "too long", "not
the inbox", "why is this here", "no, I meant the other one", "that should have
been the memo writer". Before this contract those turns landed nowhere. The
only door for changing behaviour was a `customize` conversation nobody starts,
so the same sentence got said every week and nothing moved.

A correction is already an instruction. Acting on it and showing a receipt is
one touch; asking about it is two, and the second one is the friction the
correction was complaining about in the first place.

## The rules

1. **Never ask.** No confirm card, no "should I?", no "which one?". Act at the
   smallest scope that fits and show what changed.
2. **Always receipt.** One plain sentence saying what changed, with `undo` on
   it where a store moved.
3. **Record it as asked.** The correction goes into the same store the passive
   rail writes, stamped `origin: asked`. Something the person SAID outranks
   anything the product works out on its own, permanently and without a floor.
4. **Routing complaints get a `router_miss` row.** "Wrong skill" is data about
   the router, not about the answer.
5. **"Make it like this" banks the shape.** When someone holds up a document
   and says that is the format they want, the document goes to
   `exemplars.append_structural_correction` — capture only, never a write of
   the exemplar itself. This is the exemplar rail's ONLY code capture site;
   until the composers' other twelve prose sites get one, this is the only
   way a structural correction is ever banked.
6. **Then answer the real request.** The correction is a side effect of the
   turn, never a replacement for it.
7. **Say the receipt the module returns, as it returns it.** One sentence,
   printed, nothing composed beside it. On 2026-09-16 a lane made up its own
   wording for a "too long" said about a draft ("Cut, and logged so internal
   notes stay this short"), wrote nothing to the ledger, and left `undo` with
   nothing to find. The sentence and the act have to come from the same call
   or they drift apart exactly like that.
8. **Every correction leaves ONE batch, and `undo` is never a menu.**
   `handle_correction_turn` mints one batch id for the turn and hands it to
   every write it makes (`voice_corrections.log_in_passing` for the lesson,
   the persona step for the ladder), so one `undo` reverses the whole
   gesture through `brain_undo` like any other batch. Taking the lesson back
   is an APPEND — `voice_corrections.retract_correction` writes a retraction
   row and `load_corrections` stops serving the retracted one. A corrections
   log is never rewritten, backed up and cut, or deleted from, by any path
   (M's ruling R-20; `tests/run_guard_corrections_append_only_test.py` reds
   on any other write).

## The paragraph (verbatim — this is what each SKILL.md carries)

> **Correction in passing (shared contract — `shared/CORRECTION_IN_PASSING.md`).**
> Before answering any turn, pass the user's message to
> `correction_turn.handle_correction_turn(workspace_root, text, skill="<this skill>")`.
> When it returns a result, say its `receipt` in one clause inside your real
> answer and then answer the request — never instead of it, never as a
> question, never as a card. When it returns `None`, proceed exactly as you
> would have. The module applies the change, writes the correction to the store
> with `origin: asked`, and logs a `router_miss` when the complaint was about
> routing; you neither re-apply nor re-record any of that. When the turn is
> someone holding up a document and saying to make it like that, pass the
> document body as `document=<the text>` in the same call — that is the only
> path that banks a structural correction, and without it the shape they just
> showed you is lost.
