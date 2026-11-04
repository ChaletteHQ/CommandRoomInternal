# Command Room release notes

What changed in each update, newest first. This file is generated from the release manifests by the maintainer's tooling and is not edited by hand.

## v5.35.0 (2026-11-04)

Command Room Hub: one private page with a Start button for your morning.

- This update adds Command Room Hub, one private page with a Start button that preps your next meetings, drafts replies for your inbox and shows your plate, with Done, Snooze and Undo on each item. Say "set up my hub" to make yours. Your scheduled chats come down to Morning Brief and Maintenance; the work of the others moves onto the Hub. Files Command Room makes for you now come back as a file card.

## v5.34.0 (2026-11-04)

Three weeks of fixes to Waiting On, the meeting recap, drafts and the brief you ask for in chat.

- This update carries three weeks of fixes. Waiting On now understands "[name] owes me [thing]", and you can drop an item or say whose it is. The meeting recap says plainly which steps it could not run. Drafts carry your saved voice through to the moment you send. The brief you ask for in chat now preps every meeting on today's calendar. Nothing for you to do.

## v5.33.2 (2026-10-12)

The Inbox runs again for owners whose book holds a name that sounds like "Person".

- One fix. On some books the Inbox stopped with an error before it made a page, every time, scheduled or run by hand. It now finds your own names the same way the rest of Command Room does, and runs.

## v5.33.1 (2026-10-06)

Cards show up again on the new app, and Apply all sends your choices. On some seats the card tool has to be switched on before it can be used; Command Room now switches it on itself instead of posting a plain list.

- Two fixes. On the new app, and on seats whose chats already run in the cloud, your cards were arriving as plain text with no buttons: the card tool was there but switched off, and Command Room read that as missing. It now switches the tool on first and shows the card. And Apply all on a card sends your choices again instead of dropping them without a word. Nothing changes on the older desktop app.

## v5.33.0 (2026-09-23)

Your scheduled chats now run beside your own files on your own computer, under your own account, and nothing internal reaches you. Everything the product writes goes through one door, a late chat serves the slot it missed, documents land in your folder by their path on your computer, and every time is shown in your workspace's zone.

- What changed. (1) Each scheduled chat now finds your folder on its own, checks the copy of the product installed beside your files, reads how you like to be spoken to, and only then does its work; a chat whose work is not yet moved over says so in one sentence and writes nothing. (2) Everything written to your workspace is logged under your own account, never under a session. (3) A chat that fires late reads which slot it is serving and never serves the same slot twice. (4) Briefs, pages and packs land in your folder and are named by their path on your computer. (5) Every time you see is in your workspace's zone. (6) The old sidebar dashboards are no longer installed; the update declares the computer it runs on as the one that runs your scheduled chats.
- Command Room can now set up your scheduled chats for this computer itself. Say "set up command room schedules" once: it registers each chat for this computer and brings any already set up current, keeping the times you chose.

## v5.32.1 (2026-09-19)

When you tap Apply all on a card and the click cannot reach the chat, the card now tells you and shows you the exact line to paste, instead of looking like it worked. Everything else is unchanged.

- One fix. On some machines a card's Apply all built its message and dropped it, so nothing happened and nothing said so. Now every card tries every way to reach the chat, and if none works it says 'This card could not reach the chat. Copy the line below and paste it.' with the exact line ready to paste, and Apply stays available for a retry. Typing your instruction into the chat works as before.

## v5.32.0 (2026-09-18)

Nothing internal reaches you any more: everything the chat says after it composes an answer goes through the same door as the answer. Your morning brief, End of Day and board all say one open number and it is the plate's. Taking a lesson back adds a line to the record instead of cutting one out, and the profile page tells the truth about what the product learned. The Friday wrap rests nothing overdue, names what was decided for you, and the Staff Meeting says which week it read. 'Show scheduling' shows its rows. A workspace with a book in it is never treated as a fresh install.

- What changed. (1) No raw id, file path, function name, event name or internal label reaches you on any surface, including the sentences after an answer: the whole reply goes through one door, and if the door refuses, you get one honest sentence and the phrase to try again. (2) One open number everywhere, and it is your plate's; the brief credits a background batch to the door that did it ('The review door closed 45'), and the two plumbing lines that used to sit on the brief now live on the maintenance report. (3) The Friday wrap never rests an overdue item under Parked, shows one Parked count, names up to five things decided for you plus a count of the rest, and the Staff Meeting states the week it read; its tiles match its body. (4) 'Show scheduling' renders every row with its number; a dictation that matches an item already on file never opens a second row.
- Also new. When you take a lesson back ('undo' after 'too long'), the product adds a line saying so and stops acting on it; nothing is ever cut out of the record. Saying the same thing twice writes nothing and says so ('Already on file, nothing changed'). 'My profile' now reads what the learning job actually applied, dated to when it ran; 'reset how you think' either clears its own keys or tells you which page owns them, and never touches the ledger. 'I texted / called / spoke to / talked to' close an item the way 'I sent' does; an engagement letter or a fee quote is a money item and waits for you. And on setup: a workspace that already has a book in it is never treated as new, so nothing can be seeded over it; 'restart onboarding' is refused on a live book.

## v5.31.0 (2026-09-14)

Your morning brief, End of Day and Friday wrap now tell the truth and nothing else: one open number everywhere, a row number that belongs to the item, no plumbing lines, the machine's acts credited to the machine by door with an undo, and nothing already reversed reported as standing. A mail that only asks a question closes nothing; one send closes one item; a meeting processed twice writes nothing new; mark won plus undo reverses everything. The 30-day cleanup is retired. And the product now learns from your edits, keeps a profile page you can read and correct, and lets you shape your brief in a sentence.

- Retired the 30-day cleanup — the 45/60-day silence door is the only one now.
- What changed on the surfaces you read. (1) The morning brief leads with one open number and every other count is a breakdown of it; it never asks you a question and carries no connector or health lines (say 'health check' for those). (2) End of Day credits what the product did on its own to the door that did it, one line per batch with an undo, never as 'you let go'; a close you already undid is never listed; and if it cannot render it says so in one sentence and you say 'end of day' to retry. (3) The Friday wrap's Parked list shows at most ten items with a real reason each, one in-and-out pair, and 'Decided for you' names each act. (4) A row's number now belongs to the item, so 'later 83 to friday' lands on the same row tomorrow; 'show waiting' and 'show scheduling' answer on their own; and the two switch phrases 'turn off closing when I say it is done' and 'turn off parking items nobody touches' now work as announced.
- Also new. A sent email that only asks a question closes nothing, one send closes at most one item, and a title match alone never closes without a completion signal or an attachment. Processing a meeting a second time writes nothing new; it only corrects what is already there. 'Mark won' followed by 'undo' puts the deal, the company and the win back exactly as they were. An invoice or a retainer never lapses through the two-day door. Say 'undo the three items you let go' and the product finds that batch. No raw id, file path or internal name reaches you on any surface. And from night 11a: say 'my profile' for one page of what the product knows about you, every line with where it came from; say 'group my brief by workstream', 'lead with my calendar' or 'keep it short enough for my phone' to shape the brief in a sentence, and 'reset my brief' to put it back; your in-passing corrections now change how the product writes, each with an undo, and 'stop learning from my edits' turns that off.

## v5.30.0 (2026-09-08)

Your plate is now one board, grouped by project, one line per item. What comes in is held or filed with a date; what goes out leaves by a named door — your own word, a fact on your calendar or in your sent mail, or sixty quiet days — each with a receipt and an undo that holds. The machine's own acts are no longer credited to you. Two morning chats fold into the brief, every question has a lifetime, and the health lines leave your brief for the health check.

- Three things you will notice. (1) 'What's on my plate' now opens as a board: grouped by project, one line per item, one tap or none, the most important first; say 'work my plate' for the page with every verb, and type a verb by number against the board ('later 3'). (2) Items leave your plate by a named door, each with a receipt and an undo that holds against the next morning: say what you finished in your own words and the matching item closes; a real email you sent or a meeting that happened closes the item it was about; an item nobody has touched in forty-five days rests under Parked, and at sixty it is let go — the Friday wrap lists what was let go and one 'undo' puts them back to rest. Say 'turn off closing when I say it is done' or 'turn off parking items nobody touches' to stop either. (3) New items are held until something confirms them or filed with a date from where they came from, so fewer 'whose is this?' questions reach you; 'turn off holding new items until something confirms them' stops it.
- Also new. Every question the Staff Meeting asks now has a lifetime and a sensible default; one it cannot wait on any longer is answered for you and the Friday wrap says so under 'Decided for you' ('turn off answering old questions for me' stops it). Duplicate people are merged and new people added without asking, one batch a week with an undo ('turn off merging duplicate people', 'turn off adding people automatically'). The Waiting On and My Plate morning chats no longer fire on their own — their one-line headlines sit on the brief, and 'show waiting' and 'what's on my plate' still answer any time. The brief carries no health or plumbing lines; say 'health check' when you want them. A note you mail to yourself never closes anything, and nothing the product does on its own is ever written up as your doing.

## v5.29.3

Cards show up again on the new app. On some seats the card tool has to be switched on before it can be used; Command Room now switches it on itself instead of posting a plain list.

- One fix. On the new app, and on seats whose chats already run in the cloud, your cards were arriving as plain text with no buttons: the card tool was there but switched off, and Command Room read that as missing. It now switches the tool on first and shows the card. Nothing changes on the older desktop app.

## v5.29.2

Apply all on a card sends your choices again. The button had been building your choices and dropping them without a word; that is fixed, and if a card ever cannot reach the chat it now says so and shows you the line to paste.

- One fix. Apply all on a card was doing nothing: it collected your choices and dropped them without saying so. It sends them now, once. If a card ever cannot reach the chat, it tells you and shows the exact line to paste. Typing your instruction into the chat works as before.

## v5.29.0 (2026-09-06)

Closing on evidence is switched off until you turn it on. Every client seat now starts light: at most five questions a week, the plate shows forty at a time with overdue and this-week items always up. The morning brief leads with one number, the day-close shows what opened, closed and slipped, and the Friday wrap lists everything parked. Won clients are never nagged as live deals, and a deal undone is undone everywhere.

- Closing on evidence is off until you turn it on. Command Room no longer closes a promise because a meeting transcript sounded like it was kept. The pass that did that was tightened (a close now needs the meeting's own time, a promise older than the meeting, and one turn that both says 'done' and names the item) and then switched off, pending a replay on a real book. While it is off, a promise a meeting shows was kept becomes a suggestion under 'needs your call', the morning brief says how many look kept, stale suggestions withdraw themselves instead of closing, and booked-meeting rows are offered rather than closed. Say 'turn on closing on evidence' to let those close on their own (each close carries the words that closed it and 'undo' reverses a fire); say 'turn off closing on evidence' to stop. Nothing on your plate moved.
- Three things you will notice. (1) Your morning brief now opens with one number — how many items are on your plate — with the top moves and one pointer; the warnings and the other counts sit below it. Your day-close shows what opened, closed and slipped today and asks you nothing; the Friday wrap lists everything parked, with the reason. Your scheduled chats pick the new shape up on their own at their next fire. (2) The Staff Meeting no longer asks whether a company is a live deal; that signal is kept quietly and a company becomes a client only when something is paid, signed or won — one line in your brief, 'undo' puts it back. A company you already won is never nagged about it again. 'Mark X won' with no deal on file now says so and does it openly. (3) Later… on your plate holds until you give it a date, an undone Later goes back to no date, rows you undo come back on the page, and the plate shows nine rows a page so every row keeps its buttons.

## v5.28.0 (2026-09-04)

Promises your meetings show were kept now close themselves, with the words that closed them. The plate reaches your morning brief, your day-close and your Friday wrap. A won deal quiets its own nag and promotes its own company. Meetings now say who owes what to whom, and stop turning requests and dictation into to-dos.

- The biggest change: when a later meeting shows a promise was kept, that item now closes itself as done, and the row carries the exact words that closed it. You are not asked first. Say 'undo' in any chat to reopen the whole batch. Where the evidence is only middling, one small note rides the row for four days and then either closes it or quietly goes away on its own; nothing waits on you to answer it. Anything left unanswered now takes its default after four days rather than seven.
- Three things you will notice. (1) Your morning brief now leads with one number, how many items are on your plate today, with the top few and one pointer, instead of six competing counts. Your day-close shows what opened, closed and slipped today in the same shape, and the Friday wrap carries the week's version plus everything parked. (2) After a call, the meeting card asks at most three quick questions, each with the likely answer already picked; leave one and that answer applies after two days, or if there is no likely answer the item is let go instead, and 'undo' brings it back either way. Meetings no longer turn a request nobody accepted, or something you dictated to a tool, into a to-do. (3) The moment a deal is marked won, or a payment or signed agreement lands, the 'looks like a live deal' reminder stops on its own and the company is promoted to client automatically, with one line in your brief's changed feed and 'undo' to put it back.

## v5.27.0 (2026-09-03)

Your plate now groups by what each item wants next, captures from meetings carry their evidence, and five small skills fold into the ones they belonged to. One silent fix: workspaces without an owner pointer get one written.

- I recorded who this workspace belongs to, so your plate and counts read from the right side (you owe / owed to you) from now on. Nothing for you to do.
- Two things you will notice. (1) Say 'what's on my plate': instead of one long list by age you get six groups in this order — Confirm, Do it, Chase, Wait, Schedule, Parked — each by project, then by when. Four actions on every row: done, not mine, later, drop. Parked items stay visible with the reason. (2) Commitments captured from meetings now record why they were believed and why any of them was held back for review, and a session where only you were talking is kept as notes rather than turned into to-dos. Nothing to do on your side.
- Fewer front doors, same phrases. Five small skills folded into the ones they belonged to: the sidebar dashboards now live in one place (say "level up command room" to see Workspace Map, Quick Commands and the new My Open Commitments — "install my commitments" adds it), "warm threads to revive" and "what conversations went quiet" are the threads lens of the dormant-customer scan, "what decisions should I revisit" and "decision audit" are the revisit mode of your decision log, and "show me what you'd hide" is a read-only view inside needs-your-call. Every phrase you already use still works; nothing pinned to your sidebar changed.

## v5.26.1 (2026-09-02)

Internal — no user-visible changes. Nothing to do.

## v5.26.0 (2026-09-02)

Internal — the activity log now appends in place instead of being rewritten (faster, safer under two machines), a routing miss is recorded instead of guessed, and the licence gate ships switched off. Nothing to do.

## v5.25.0 (2026-09-02)

Your morning brief now reads the same trust-gated project memory as everything else, past records can be filed to their project with one tap, and decisions that were carried out finally leave the active list.

- Three things you will notice. (1) The morning brief's per-project lines draw on the same canonical, trust-gated read of each project's history that preps, project loads and drafts already use — steadier one-liners, nothing to do. (2) Say 'review project bindings' when you have a minute: it shows past notes and captures that name a project they were never filed under, one tap each — Bind or Not this project — with a single undo for the batch. Nothing is filed automatically. (3) Decisions your record shows were carried out now sit in a Resolved bucket instead of lingering as active; nothing is deleted and the history stays. Also: the cards in your people directory no longer show an internal id next to each name.

## v5.24.0 (2026-09-02)

Email drafts and general project questions now draw on the same trust-gated project memory as your preps and project loads — steadier context everywhere, nothing to do on your side.

- The memory layer now reaches everything. Email drafts and loose project questions ("pull up Northstar", "where are we with the pilot") draw on the same canonical, trust-gated read of each project's history that meeting preps and project loads adopted in the last update. Projects with a well-bound record get steadier, richer context; thin-history projects answer plainly instead of guessing. Nothing to do — it applies from your next draft or question.

## v5.23.0 (2026-09-01)

Project loads and meeting preps now draw on a canonical, trust-gated read of each project's memory — more consistent context with no action needed on your side.

- Opening a project and prepping for meetings just got more consistent. Behind the scenes, those surfaces now read each project's context through one canonical, trust-gated memory layer instead of assembling it fresh every time — projects with a well-bound history get richer, steadier context, and projects with thin history simply answer plainly rather than guessing. Nothing to do; it applies from your next project load or prep.

## v5.22.0 (2026-08-31)

Stale unconfirmed captures now clean themselves up after two quiet days, your saved tone preferences govern scheduled briefs too, and overnight-drafted session notes wait for your confirmation before entering the record.

- Unconfirmed captures now clean themselves up. When a possible commitment is extracted from a meeting or email and you don't claim or drop it within about two days, it closes on its own and disappears from your queues — reversibly. Heads up on the first morning after this update: the whole standing backlog of stale unconfirmed items will close in one batch, and your brief will say so in one line. That is the new rule working, not data loss — nothing is deleted, saying "undo" right after puts every one of them back, and anything you have actually confirmed as a real commitment is never touched.
- Your saved tone preferences now govern scheduled surfaces too. If you have told your assistant how to talk to you — for example "never open with pleasantries" — the morning brief, evening close, and weekly recap now honor it, not just regular chats. Nothing to do: the preference you already saved simply applies everywhere from the next scheduled run.
- Overnight-drafted session notes now wait for your confirmation. When you walk away from a work session without saying "end session", the nightly sweep still drafts notes for it — but they are held in a side file your client-facing documents cannot see, instead of landing straight in the project's session notes. The next time you say "end session" on that project, the held notes fold in as confirmed. If any sit waiting too long, your weekly note will mention them.

## v5.21.0 (2026-08-29)

Your scheduled tasks get self-checking fire-time guards and a pre-close capture slot, the evening close proposes tomorrow and coaches without repeating itself, and the morning brief narrates what was captured while you were away.

- This update improves your scheduled tasks: every one now checks at fire time that it is running against the right workspace, the background maintenance task gains an afternoon capture slot so your evening close opens already caught up, and twin fire slots guard against double runs. To apply the new schedule, say "set up command room schedules" once — it refreshes your registered tasks in place, preserves any times you have customized, and tells you exactly what changed.
- Your daily bookends learned some manners. The evening close now offers up to three ranked candidates for what tomorrow is about — reply with a number to pick one — names anything it deferred, and its coach makes each point once. The morning brief opens with what was captured since your last close, and status lines appear only when there is something to disclose. Ad-hoc work sessions you simply walk away from are composed into your session notes overnight — saying "end session" is still welcome, just no longer required.

## v5.20.0 (2026-08-26)

Telling Command Room how to talk to you now works from any chat, and the improved promise-reading can be applied to your existing review pile.

- You can now shape how Command Room talks to you from any fresh chat — say "how do you talk to me", "never open with pleasantries", "be more direct", or correct the tone in passing. Your preference is saved once, in your workspace's style settings, and your chats honor it from then on. (Scheduled briefings pick the preference up in an upcoming update.)
- A recent update taught Command Room to read the full exchange in a meeting — the offer AND the reply — before deciding whether a promise was accepted. Items captured before that fix can now be re-checked the same way, in a supervised pass that proposes each change for your confirmation and never rewrites anything on its own. Chalette will run this with you when your workspace is ready for it.

## v5.19.0 (2026-08-26)

The evening close gets fast without getting thin, tomorrow stops proposing the past, and every count means items.

- One thing to do once, on each computer you use Command Room from: say "set up command room schedules". This update teaches the background maintenance pass to capture your meetings during the day, so the 5 PM close verifies instead of fetching and lands in minutes — but that instruction lives inside your registered task, which only refreshes when you say the phrase on this machine. Until you do, nothing breaks: the close simply keeps working the old, slower way. Takes seconds, changes nothing else, safe to say more than once.
- Tomorrow's plan can no longer contain the past: an item anchored to the day being closed — "tonight", a same-day time, a due date already behind you — is never proposed for the next day. And every due date you see now reads against today: "due Aug 6 — 19 days ago", never a stale "before Thursday".
- Your morning brief and evening close now count the way the queues do: items, not fragments. Where several captures are one real piece of work, the headline says so — "9 items, 41 rows" — so the number you see is the number of decisions in front of you.

## v5.18.0 (2026-08-26)

Command Room learns how you like to be spoken to — and proposes it from your own record instead of asking a questionnaire.

- Command Room now carries a style layer: how brief, how formal, how much explanation, and one concrete "never do this" rule of your choosing. You can set any of it by just saying so ("be more direct with me", "skip the pleasantries") — and over time it may PROPOSE a setting it can read from your own record, like your sent mail running short. A proposal is always shown with its evidence and applied only when you confirm; anything you have ruled on explicitly is never second-guessed. Say "how do you talk to me" to see the current settings.

## v5.17.0 (2026-08-25)

The evening close starts coaching from the record: patterns across your closes, and whether the day did what you said it would be about.

- Your evening close now remembers its own history. When something has sat still across several closes, it says so with the count. When you set what tomorrow was about and the day went elsewhere, it names that plainly — and when the day did what you intended, it says that too. At most a sentence or two, always drawn from your own record, never a score or a lecture: if it has raised the same thing three closes running it says so once more and then lets it rest for a few evenings. Nothing new to tap; the one question about tomorrow stays the only question.

## v5.16.0 (2026-08-25)

The list gets shorter and truer: one line per real thing, an evening that reads your day instead of listing it, and captures that hear the answer, not just the offer.

- Your queues now show one line per real-world item. When the same piece of work was captured several times across calls, you see it once, with a small "+N folded" naming how many captures sit behind it — tap to read them. One tap on "keep as one" makes the fold permanent, and one undo puts everything back. Nothing merges on its own except word-for-word duplicates, the same as before. The counts you see now count items, not fragments.
- Your evening close now reads the day back against the bigger picture: which of the things you are running moved today, which ones that carry consequences did not, and where the day's weight actually went — in plain sentences, each grounded in something that happened. It adds no new buttons and asks the same single question about tomorrow.
- Items the system was quietly watching now appear at the tail of "needs your call" as a Set Aside section, each answerable with confirm or drop. Before this they were in a lane no surface showed, which meant they could never be cleared. If your queue looks slightly longer this week, that is previously invisible work becoming answerable — not new inflow.
- Two quiet corrections underneath everything: pressing Skip or dismissing a question now does exactly nothing — declining to answer never closes, drops, or changes an item. And when a capture from a meeting is judged, the system now reads the reply that followed — "That's perfect." after an offer counts as the acceptance it was — so fewer real promises get labeled as never agreed to.

## v5.15.0 (2026-08-24)

The evening brief tells you about your day instead of scoring it, a clock that runs ahead can no longer poison the ledger, and the weakest captures get a reading chair before anything is ever hidden.

- Your end-of-day changes shape. It used to hand you a scored list of what moved; now it reads the day back to you in plain sentences, each one grounded in something that actually happened, and ends by asking a single question about tomorrow. Nothing is scored and nothing is graded. If the evening pass has felt like a report card rather than a close-out, this is the change.
- There is a new thing you can ask to see: say "show me what you'd hide" and you get every capture that was judged too weak to be worth asking you about, grouped by the call it came from, each row carrying the plain reason it was refused. Looking at the list changes nothing at all — no row is closed, dropped, or re-dated by reading it. It exists so that the weakest captures can be looked at before anyone decides whether they should stop being asked about. That decision has not been made and nothing is being hidden from you today.
- A record stamped with a time in the future is now refused when it arrives, instead of being written and quietly dragging your workspace's sense of "now" forward with it. If a machine with a wrong clock ever did write one, your health check names the contamination directly rather than reporting everything as fine — say "health check" to see it, and it will tell you what can be repaired from the record itself.

## v5.14.0 (2026-08-23)

The list starts cleaning itself: overdue work asks once and then rests, agreed work that goes silent ages out weekly, weak guesses stop at the door, and closing one item no longer hides the rest.

- One thing to do once, on each computer you use Command Room from: say "set up command room schedules". This update adds a weekly pass that lets go of agreed work which has gone quiet, and that pass lives inside your registered chats rather than in the code. Your scheduled chats are stored on the computer that runs them, so the new leg only reaches this one when you say it here. It takes a few seconds, it changes nothing else about your chats, and it is safe to say more than once.
- Run Now changes, and the note in the last update about it no longer holds. That note said a scheduled chat which had already delivered today's edition would decline a second press and leave it there. Run Now now renders a fresh edition on every scheduled chat, and every render carries a line saying when that chat last ran, so a re-run is never mistaken for the scheduled one. The only press it still declines is a duplicate within two hours of a delivery, which is the case the refusal was written for. Outside that window, press it whenever you want the current picture.
- Old unanswered guesses can now be let go in one sitting. Say "review amnesty" and you get the whole batch of guesses nobody ever answered, sorted into yours and other people's, with the count shown before anything happens. One "undo" puts the entire batch back exactly as it was. Then answer tonight's questions on what is left: for each item still open you get done, a new date, or drop. That is the one sitting. From there the weekly pass keeps the list at that size on its own, so this is not something you do again.
- An item that has gone past its date now asks you once and then rests. The question carries the three ways out you already have: done, a new date, or drop. Until you answer one of them that item stays quiet, so a single slipped promise no longer repeats at you every evening for a week. Answering it puts it straight back into the normal flow, and a new date reschedules it rather than closing it.
- Agreed work that goes silent now leaves on its own. An item nobody has touched is shown to you three times first, so nothing goes without having asked. After the third showing it is let go in a weekly pass that says how many it released, and one "undo" puts that whole batch back. Work you have answered, dated, or acted on is never in scope, and neither is anything you agreed to this week. This is the leg that only reaches a computer once you have done the one-time step in this update.
- Closing or confirming one item no longer hides the other open items on the same conversation. Answering one promise on a thread used to quiet every other promise on that thread for a week, which is why a thread you had only partly dealt with could look finished. Each item now stands on its own. You may see a handful of items reappear that were being held back this way. They are not new work and they are not new questions: they fall under the asks you already have, and they answer the same three ways.
- Low quality guesses are now stopped where they are written instead of landing in the pile you confirm. Every guess carries a confidence rating, and the quality floor that rating feeds was only being applied at some of the places that create work. It is applied at all of them now, so a guess below the bar never becomes a row you have to answer. Two odd cases are handled rather than waved through: a rating that cannot be read as a number goes to review instead of past the floor, and a rating far outside the sensible range is brought back to that range's edge. What you should notice is a shorter confirm pile, not a different one.
- When two of your items match what you said closely enough that it is genuinely unclear which one you meant, you now get a single either or question instead of a guess. It names both sides and it deliberately carries no confirm button, because a tap cannot say which one you meant. You answer in words and the right item closes. Nothing sweeps an ambiguous row shut on your behalf, and no bulk action reaches one either. This closes the last way the evening routine could have closed the wrong promise.
- Meeting prep now reads the mail account you told Command Room to use. Prep had been looking for one mail product whatever you actually had connected, so anyone working out of Outlook or Superhuman got a brief with the calendar and the meeting history in it and no mail context at all. It resolves whichever mail you declared and speaks that product's own search vocabulary, so the thread history that belongs in a prep is in it. Nothing changes for anyone already on the mail it used to assume.

## v5.13.2 (2026-08-22)

Three things worth knowing on your first days after this update.

- One change in how Run Now behaves. A scheduled chat that has already delivered today's edition now declines to send it a second time when you press Run Now — it tells you when it last ran and leaves it there. This exists so a catch-up and a scheduled run can never land the same brief twice. When you want a fresh one on demand, say its phrase instead — "brief me" for the morning brief, "end of day" for the day-close — and it renders right away.
- If you ask for a health check within a day of updating, it may tell you that several of your scheduled chats "ran but didn't record their work." That is the update itself: refreshing your chats looks like a run to the check, and the record rightly has nothing for it. It clears on its own after the next morning's chats fire. Nothing is wrong with the chats and nothing needs re-authorizing.
- The weekly pass that lets go of unanswered guesses starts with this update, and your first one may be large — it reaches everything that has sat unanswered for two weeks or more, which on a workspace that has been running since the summer can be dozens of rows in one go. It says how many it let go of, it never touches work you have agreed to or anything you have answered, and one "undo" puts the whole batch back. If you would rather see it before it happens, say "review amnesty" any time and it shows you the list and asks first.

## v5.13.1 (2026-08-21)

The held pile drains itself, the day-close says what it read, and a name already in your invite stops becoming a question.

- The largest number in your workspace was a pile that could only grow, and two things now reach it. First: when a capture names somebody your workspace cannot place, it is held for your word — and until now nobody was ever asked to add that person, so the same few names failed meeting after meeting and everything waiting behind them was stuck for good. A name that has failed across two or more different calls is now offered to you as one question, with the count of captures waiting behind it and every spelling your transcripts used. Answering it adds the person, saves those spellings so the name resolves from then on, and clears the whole backlog behind it in the same breath. The offer appears at the top of "needs your call", in your staff meeting and in your End of Day — at most two names at a time, and not at all when there is nothing to ask. Second: guesses nobody has answered now expire on a weekly pass instead of sitting forever. You see what it is about to let go of before anything happens, it never touches something you have answered, and one "undo" puts it all back. Nothing is closed as done and nothing stops being captured — if it comes up again in a later conversation, it is captured fresh.
- Your End of Day stops reporting numbers it cannot stand behind. It now opens by saying what it actually read — how far your mail and your chat have been read to, whether your calendar was reachable, how far back the meeting pass reached — and when one of those has fallen behind it names the gap in plain words instead of presenting partial counts as the whole day. On a day when no morning brief fired there was no starting point to measure from, and the block naming what moved was reading your entire recorded history and reporting it as today's; it now counts from your own workspace's midnight, in your own timezone, and says that is the window it used. Nothing was ever recorded wrongly by the old behaviour — these were reads only, so there is nothing to repair on your side and nothing to re-register. Your evening close picks all of this up on its next fire.
- Two quieter fixes, neither needing anything from you. The same promise stops taking up two lines: when one person's name had been written two different ways, the duplicate check gave up before it ever compared the promises, so genuine duplicates stayed on your book as separate rows — and an item imported twice could appear several times over. Both fold to one row now, and a folded row says that it folded rather than hiding it. And when a capture names somebody your workspace cannot place, it now checks that same meeting's attendee list first: if the list carries their full name and their email address, the contact is created and the capture files itself clean, with one line saying who was added, what it was taken from, and "undo" to reverse the lot. The bar is deliberately narrow — a name with no email address attached still becomes a question, because an attendee list without an address proves nothing the transcript did not already say, and a name you have set aside as "not a person" is never created whatever the list says. Worth knowing: this only helps on meetings whose records carry everyone's email address. If your meeting notes record only your own address, turning on participant sync at the source is what makes it work.

## v5.13.0 (2026-08-19)

Your morning brief stops hedging on days when nothing is behind, the Apply receipt stops calling finished work an error, and the pile of unconfirmed captures finally has a way out.

- Two things you may have noticed are fixed, and neither needs anything from you. First, your morning brief had been telling you every single day that it had not been able to check your sent mail, and softening every item you owe with "you may have already handled this" — even on days when your sent mail was being read cleanly several times over. It was never being told how far that reading had got, so it assumed the worst every morning. It now reads the real state, so the warning appears only when something is actually behind, and the brief also stops listing items on conversations that moved in the last week. That second change is the one you will notice: your morning list may be much shorter than it was — on one workspace it went from over a hundred rows to under forty overnight. That is the fix working, not work disappearing. Nothing was removed and nothing was closed; the full lists are still there — say "my plate" for everything you owe and "waiting on" for everything owed to you. Second, when you answered a row and it landed correctly, the record of it could still come back labelled an error, and rows that looked failed were offered to you again. Both now say what actually happened. If your workspace stored who you are under an older internal name, your owed-by-you and owed-to-you counts were also being computed against nobody — that is fixed too, so those two numbers may look quite different from yesterday. They are right now; nothing about your recorded work changed.
- New in this release: a way to clear the pile of unconfirmed captures. When something in a meeting or an email sounds like a promise but is not clear enough to act on, it is held as a guess and waits for your word — it is never chased and never closed on its own. Nothing ever retired one, so on a busy few months that pile becomes the largest number in your workspace, and a list nobody can finish is a list nobody opens. Two answers now reach it. Say "review amnesty" to let go of every guess nobody has answered in the last two weeks — you see the count and the split between yours and other people's before anything happens, and one "undo" puts them all back. Or, when one bad import filled it with junk, name that meeting and say to drop everything that ingest captured. Neither one says the work was not real and neither stops anything being captured again: if it comes up in a later conversation, it is captured fresh. Worth knowing: this clears what has piled up, it does not change what arrives — that is separate work.

## v5.12.0 (2026-08-14)

Fewer already-done items reach your commitment list, and your decision log renders its titles and its superseded decisions again.

- Two things changed in this release, and neither needs anything from you. First, your commitment list should get quieter. When you do something live on a call — send a file while screen-sharing, fire off an invite in the moment — that was still landing on your list as work you owed. The check meant to catch it was reading the sentence someone said, when the giveaway is actually in what gets said a few seconds later. It now reads the following turns, and duplicate captures of the same promise fold into one row. Second, your decision log: most rows had been rendering with no title at all, and decisions you had superseded were still showing as current. Both are fixed. The commitment change applies to new captures at the next scheduled run; the decision log corrects itself the next time it regenerates, or immediately if you say "show my decision log".

## v5.11.1 (2026-08-13)

Meeting-prep links now open on OneDrive and SharePoint workspaces, and a workspace with two cloud drives connected always searches the right one.

- A fix from the field: if your workspace folder syncs through OneDrive or SharePoint, the document links in your briefs could answer a click with nothing at all. The documents were always fine — the link only knew how to find files on Google Drive. Links now resolve against whichever cloud drive actually hosts your workspace, and if you have more than one drive connected, the right one is chosen from where your workspace lives rather than whichever tool answered first. Nothing for you to do — the next brief that fires carries working links.

## v5.11.0 (2026-08-11)

The Aug-10 train: meetings know who was in them, wrongly-closed decisions come back, your surfaces stop disagreeing with themselves — and the morning brief preps your day itself.

- Two chats became one. Your Morning Brief now writes today's meeting prep before it writes the brief, so the prep documents are ready and linked by the time you read it. Same prep, same folder, same quality — one less chat to watch, and if the prep ever fails the brief still arrives and tells you which meeting to ask about. Anything booked after the brief runs is one ask away: say 'prep me for my 2pm'.
- One thing to do, once, on each computer you use Command Room from: say 'set up command room schedules'. Your scheduled chats live on the computer that runs them, so the change only reaches this one when you say it here. If your Upcoming Meetings chat is still sitting in your Scheduled list afterwards, say 'pause upcoming meetings' and I'll switch it off — its work now happens inside your Morning Brief.
- A fix from the field: meeting prep documents attached to your brief could answer a click with "This file can't be found on your computer." The documents were always fine and always in your workspace folder — the link recorded where the file sat on the computer that made it, which is not where it sits on any other. Links are now recorded relative to your workspace and resolved at the moment they are posted, against the machine posting them — which is the machine you are reading on, because your scheduled chats run where you are. Older links are repaired as they are read; nothing in your history is rewritten. If a document has not finished syncing yet, the brief says so plainly instead of handing you a dead link.
- One new thing you may see, so it isn't a surprise: when you close an item from your Morning Brief by its number, that number has to match the list the brief recorded when it posted. If a brief ever posts without recording its list, closing by number could land on the wrong item — so instead of guessing, I'll tell you plainly that the numbers don't line up and offer two ways through: run the brief once so they match, or just name the item and I'll close it directly. You should rarely see it. It exists because closing the wrong thing quietly is worse than asking.
- Meetings finally know who was in them. Every new meeting records its attendees canonically, so relationship cadence, dormancy alerts, call-prep timelines and 'when did I last talk to X' stop under-counting people you actually meet. Historical meetings can be repaired too — your operator runs that backfill for you, so there's nothing you need to do.
- Meetings no longer close your decisions on a word-match. When a call looks like it reversed an old decision, the decision log now shows a 'supersede proposed' note on that decision's own line — it stays active until YOU say otherwise. And a decision that was closed by mistake can be brought back: say 'still valid' on it and the newest signal wins, with the history kept.
- Your daily surfaces got honest: My Plate renders every promise (no more silently missing rows), unowned items drain through their own triage lane instead of sitting pinned forever, review questions ask per item instead of closing in bulk, duplicate rows collapse to one card that names what it absorbed, and every unconfirmed number now says which slice of what it is.

## v5.10.0 (2026-08-05)

The workspace stops drifting from the truth — self-updating registers in your always-on file, no more vanishing projects, an honest Monday note, and reply drafts that keep their thread.

- Two things you will actually notice after this update, both intentional. First: your workspace's always-on file gains two generated sections — a register of your organizations and one of your workstreams, drawn from your own records and kept current automatically from now on. They are clearly marked as generated; your own writing in that file is never touched. Don't edit inside the marked blocks — the next refresh redraws them. Second: your next Monday self-maintenance note may list more findings than usual. That is the report becoming honest — it now surfaces problems it previously discarded silently, including some that have existed for a while. It is reporting old breakage, not new breakage. Also in this release: projects can no longer silently vanish from the master tracker view, and editing a reply draft can no longer detach it from its email thread.

## v5.9.4 (2026-08-05)

Internal — the workspace timezone is found wherever it lives; no user action needed

## v5.9.3 (2026-08-05)

Internal — audit-trail correctness for "already done"; no user-visible changes

## v5.9.2 (2026-08-05)

Brief links open on Google Drive workspaces — a saved recap no longer answers a click with "Failed to load local file."

- A fix from the field: on a workspace that lives in Google Drive, meeting briefs and weekly recaps saved fine, but clicking their links answered "Failed to load local file." — the link pointed at a place that only exists on this side, never on your machine. Links for Drive-hosted workspaces now open the document in Google Drive itself, where the file actually lives. Workspaces in a regular local folder are untouched — those links keep opening exactly as before. Nothing to do on your side; the change arrives with your next scheduled brief or recap.

## v5.9.1 (2026-08-05)

Actions that need something typed always show the box to type it in — Reassign asked for a name and gave you nowhere to write one; that whole family is fixed.

- A fix from a field report: choosing Reassign on a My Plate item held Apply with "Reassign needs a name" — correctly — but the box to type the name into never appeared, so the action couldn't be completed there at all. That box now appears, and so does the one for every sibling that had the same gap: Theirs, Mark received from, Same as, Move stage, Report status, Split, and Add sub-items. Each typing box now also says what it wants ("Type the name") instead of showing date examples. Nothing to do on your side — the fix arrives the next time a list renders.

## v5.9.0 (2026-08-03)

Contact records create themselves for people you actually correspond with — two-way mail only, structured names only, narrated with undo. Shared inboxes are recognised as mailboxes rather than people across the whole product.

- Contacts now add themselves for people you demonstrably write to. The bar is strict on purpose: you must have sent that address a direct To/CC message from your declared account (inbound-only never counts — newsletters and vendors never file themselves in), and the name must come from a structured place (their own mail's display name, or a calendared attendee record — never a transcript's guess, never a name reconstructed from the address). New contacts appear in the change feed with an undo, never as another question; a 'who is this?' row already in your queue retires when the new record answers it. Nothing is backfilled — it looks forward from today only. If you'd rather not keep one, say 'undo' — archived, never deleted, and it stays gone.

## v5.8.0 (2026-08-03)

"Already done" is an answer on the review queue (with an undo that really un-confirms), session notes file themselves with a safety copy and an honest refusal for custom files, and the weekday morning digest chat retires with its jobs folded into the weekly rhythm.

- Three changes worth knowing. First, the review queue has a new answer: 'already done' — the promise was real and you already handled it off-mail. One tap books it and closes it as completed, recorded honestly as your word at review time; it works one item at a time on purpose, and undo returns the item to the queue instead of leaving a closed record. Second, long session-notes files now roll themselves into dated archives during weekly maintenance — always with a full safety copy first, and a file too custom to split safely is left untouched and offered as 'roll over my session notes' instead. Third, the weekday morning digest chat is retiring: quiet-project detection now runs weekly (a project only goes dormant after a question you were asked lapsed unanswered — any answer stops it), the questions wait at 'stalled projects', and intro follow-ups answer to 'check my intros'. If the old chat is registered here you'll be offered its retirement — nothing switches off silently.

## v5.7.0 (2026-08-02)

The staff meeting fits on two screens. Questions of one kind arrive as one grouped ask, every row can actually be answered, obvious name matches with real context apply themselves (with undo), and quiet-project questions wait until you ask.

- Your staff meeting just got much shorter — typically two screens instead of seven. Nothing was dropped: repeated questions of one kind now arrive as one grouped row with an honest count (answer the group in one tap, or name any single item by its number), section titles always show the full totals, and anything past the page bound rotates up on the next fire. Two behavior changes worth knowing: a name mention that exactly matches someone on file AND shares real context (same mail domain, same organization, or the same meeting) now links itself — it shows up in the change feed with an undo, and a name alone still asks. And quiet-project questions left the weekly meeting; ask 'stalled projects' when you want them.

## v5.6.4 (2026-08-02)

Your open list only holds what's real now. Unsure meeting extractions wait in a needs-your-call queue grouped by call, every review row shows its evidence, bulk-confirm can't sweep weak guesses, meeting capture rejects clutter at the source, and archiving a project finally sticks.

- Your open-commitment numbers may look smaller today. That's not lost data: items the extractor wasn't sure about no longer count as open. They wait for you, grouped by the call they came from. Say 'needs your call' to see them and clear them in batches — confirm, drop, or 'not mine', one tap each. Your morning brief also now shows the 5 most pressing items instead of everything, and your staff meeting picks up a 'From your meetings' section for the same queue.

## v5.6.3 (2026-07-31)

When you clean up your commitments, the list now comes back with the account of the pass printed on it — how far back it looked, how much of your list it could actually reach, what it set aside and why, and whether it stopped early. That account had only been appearing on runs that found nothing at all.

## v5.6.2 (2026-07-30)

One command now looks backwards through months of your mail for promises you already kept, closes the ones the evidence settles, and asks about the rest in a single list.

- Say 'clean up my commitments' once, when you have a minute. The daily passes only ever look at new mail, so anything you delivered before Command Room started watching has been sitting on your open list ever since. This new pass looks backwards through the last six months of your mail instead. Where it finds hard evidence that you already delivered something — you sent the attachment, or the person you were waiting on replied on that same thread with the thing you asked for — it closes the item straight away and shows you exactly what closed and why, with one word to reverse the whole run. Everything less certain than that is a question, not a decision: items that look handled, items that are the same thing written twice, and items nobody has touched in months all come back as a list you answer. If you would rather see the whole thing before anything changes, say 'clean up my commitments, show me first' and it will change nothing at all.

## v5.6.1 (2026-07-30)

An email can no longer close a promise that did not exist when the email was sent. Every automatic closure now checks that the evidence came after the commitment it closes, on both the sent and reply rails, and the run receipt counts what the check refused.

## v5.6.0 (2026-07-29)

The email that is the deliverable now closes the commitment it fulfills, a counterparty's reply now closes what you were waiting on — with the evidence recorded and every closure undoable — and sent-mail reading works the same on every connected email backend.

- Sending the deliverable now closes the promise. Until now the sent-mail matcher measured how much of a commitment's title reappeared in the email — so 'Here you go' with the deck attached never closed anything, and the emails that simply delivered the thing were exactly the ones that fell through. The matcher now recognizes evidence of delivery: an attachment to the right person carrying completion language closes the item outright, and a message on the commitment's own email thread is matched on that thread alone. Nothing that closed before stops closing — the title-based path is unchanged — and the run receipt now says whether the delivery checks actually ran, so a quiet day and a broken rail no longer look alike.
- A reply now closes what you were waiting on. When someone you are waiting on replies on the same email thread with completion language or the document you asked for, the item closes on its own, with the evidence recorded on the closure. A reply that arrives outside the thread only raises a confirm question, never a silent close — and your own message can never close your own item. Every automatic closure lands in a batch that 'undo' can list and reverse, so a wrong close is one word away from undone.

## v5.5.1 (2026-07-29)

Spanish/bilingual is now an opt-in workspace setting chosen at onboarding. Existing workspaces are unchanged — an English install behaves exactly as it did before this update, byte for byte.

## v5.5.0 (2026-07-29)

A late fire now catches up on everything it missed instead of processing only its usual window, pages hold still while you read them, lateness notices name the right time in every timezone, and undo can no longer be taken down by one bad row.

- Closing the laptop no longer loses work. Until now, a scheduled surface that fired late processed only its usual window — a morning brief looked at the last day, a weekly recap at the last seven — so anything older than that silently fell through the gap. Every catch-up-capable surface now reaches back to its last successful run: Monday picks up the whole weekend, a missed Friday recap is recovered by the next one, and when a large backlog has to be worked in batches, the fire records where it stopped so the next one resumes there instead of abandoning the tail.
- Three reliability fixes you may notice. Pages hold still: 'show more' now serves the same frozen list page after page, so a row can no longer appear twice or vanish between pages, and if the data genuinely moved on, the refresh says so. Times are told in your clock: lateness notices used to name the wrong wall time wherever the machine and workspace timezones disagreed. And undo is sturdier: one malformed row in the history could previously take down the whole undo listing — it now reads through bad rows harmlessly and refuses new ones at the door. Separately, dashes as punctuation are now rewritten out of every document kind automatically, with the signature sign-off untouched.

## v5.4.0 (2026-07-27)

The system stops asking you about what it already knows: a meeting is no longer checked against the commitments it just created, records that clearly agree merge themselves, and an email address matching exactly one person links itself. Every automatic change is narrated the same day and can be reversed by saying undo.

- You should have noticeably fewer things waiting on your confirmation. The biggest cause was processing a meeting: every transcript was checked against the commitments that same meeting had just produced, so one clean call could hand you a page of questions that all had the same answer. That comparison is gone. On top of that, two records of the same commitment that clearly agree now merge on their own, an email address that matches exactly one person and conflicts with nothing links itself, and a receipt closes once everyone on it is accounted for, showing you what it based that on. All of it is told to you the same day it happens, and all of it can be reversed. Say undo in any chat and you will get a list of the recent automatic changes to pick from, so you no longer have to be back in the conversation where it happened.
- A piece of work will never again point at a folder that does not exist. Folder names are now checked against what is actually there, and when nothing matches, the name is simply left blank instead of being filled in with a guess. That guess is what used to put a confident but wrong folder name on a record and send later work looking for a place that was never created. Three things follow from it: the weekly tidy-up no longer creates folders just to satisfy a name it invented, work you have already closed stops reappearing, and anything that drops off a review without someone actually closing it gets flagged to you rather than quietly disappearing.

## v5.3.0 (2026-07-27)

Ask for anything on demand and it runs now — no more "skipped, it was scheduled for Friday." Applying a batch of choices no longer duplicates notes, and your inbox triage brief now arrives as a properly formatted document.

- One thing worth knowing: when you ask for something on demand, it runs — full stop. Asking for the full My Plate or the Friday wrap outside its usual slot used to get you "skipped, it was scheduled for 8:45 AM Friday" instead of the thing you asked for. That's fixed. Anything you ask for now is treated as a request for right now, whatever the clock says. Applying a batch of choices also no longer leaves you with each note written twice.
- Your inbox triage brief now arrives as a properly formatted document. It was always meant to be one, but it was being assembled by hand each run, which meant it skipped the quality checks every other brief goes through — formatting depth, your voice, and the scan that keeps internal identifiers and file paths out of anything you read. It now goes through the same writer as your call prep and memos. What's in the brief hasn't changed — the same sections, in the same order.

## v5.2.2 (2026-07-27)

Fixes — balance reminder confirmations now always attach to the exact reminder they answer, even when several arrive close together. No action needed; nothing about how you use the workspace changed.

## v5.2.1 (2026-07-26)

Internal — your install now carries only the files it actually runs; the development-only test tree and a contributor document no longer ship. Also: a meeting brief is never delivered as a Google Doc, which had been landing copies at your Drive root instead of the meeting folder. Nothing about how the workspace behaves changed.

## v5.2.0 (2026-07-22)

Every card leads with its most likely action as a real button, your corrections now count in every view, and obvious name-matches link themselves — with undo.

- When you correct where something belongs — a meeting filed to the wrong project, a misattributed note — every view now honors it: stalled projects, the pipeline, your tracker, the workspace map, and name lookups all move the activity together. One thing to expect in the first week: a few honest "is this still active?" questions may appear. That's your past corrections finally being counted everywhere — each one is a single confirm, capped at a few per day.
- Your action cards got clearer: every row now leads with its most likely action as a visible button (Nudge on delegated items, Done on commitments, Close it on cleanup questions), small option sets show all their buttons instead of hiding in a dropdown, and a single-item card acts the moment you click. Identity questions now show the actual quote they came from plus what's on file, so you can decide in one glance — and when a mentioned name exactly matches one contact with nothing conflicting, it links itself and tells you, with `undo` always one word away.

## v5.1.0 (2026-07-21)

Standing objectives arrive — a small set of big priorities the workspace tracks for you — plus clearer everyday language and the retirement of the old my-list.

- New: standing objectives. Keep a small set of big, weeks-long priorities and let the workspace track each one the way you choose — reviewed in a recurring meeting, self-reported in one weekly touch, or read from the linked work itself. Say `objectives` to see them, `new objective` to add one — first run proposes candidates from your own workspace. Objective updates you confirm or dismiss now flow through your Staff Meeting like everything else.
- The old 'Add to my list' button is retired — items route to real homes (notes, tasks, meeting follow-ups) instead of a parking lot; anything already on your list stays viewable until it drains. Also: plainer language across cards ('Merge records (permanent)' says what it does, deal stages read in plain English), and when two people share a name you now get an honest 'which one?' instead of a silent guess.

## v5.0.0 (2026-07-20)

Your commitments split into two clear lanes — what's on your plate and what you're waiting on others for — plus people identity cleanup, entity history, and new chart, infographic, and scorecard formats.

- Your commitments are splitting into two lanes: My Plate (things you own) and Waiting On (things you're waiting on other people for). This takes a one-time migration per workspace — say `set up command room schedules` once to run it. Until you do, items you own stay quiet, so run it soon after updating.
- Two under-the-hood upgrades landed: people identity now reconciles and de-duplicates your contacts automatically, and a privacy pass closes person-side leaks across every surface that reads or composes about people. Nothing to do — your records just get cleaner and safer.
- New this release: entity history (every person, deal, and project now keeps a timeline you can review, with auto-captured facts and undo), plus fresh output formats — charts on demand, infographics, and scorecards — and a Balance Guardian that runs a Sunday check-in you can opt into. Deals can now originate straight from your pipeline.

## v4.9.0 (2026-07-18)

A calmer morning brief, a real decision meeting three times a week, denser action cards, and a polished HTML format for your documents.

- Your morning brief is now a clean read-only digest — it tells you what's going on and stays out of your way. Anything that needs a decision from you moves to the Staff Meeting, which now runs Monday, Wednesday, and Friday and picks up a 'hold' option for items you want parked without dismissing. To switch to the new schedule, say `set up command room schedules` once — it takes about a minute.
- Board packs, one-pagers, value receipts, and research briefs can now come out as a polished web-style HTML page instead of a Word document — same content, same quality checks, easier to read on screen. Research briefs use the new format by default; for everything else say `tune output` to choose per document type. You can always ask for 'as a doc' or 'as HTML' on any single request.
- Your action cards got faster and denser — roughly two to three times more items fit on a page, with a compact dropdown for each row's actions. Also in this update: people already saved as contacts stop being re-suggested as new people, and a batch of reliability fixes across the daily loop landed after a full live re-verification pass.

## v4.8.1 (2026-07-17)

Internal fix — people already in your contacts stop re-appearing as "add person" prompts.

## v4.8.0 (2026-07-13)

Command Room now tracks your deals — and its background upkeep got simpler and more reliable.

- Command Room now tracks deals. Say 'new deal [name] with [company], [amount]' to open one, 'show my pipeline' for the full picture (totals, what's closing this month, what's gone quiet), 'move [deal] to negotiating' as it progresses, and 'mark [deal] won' or 'mark [deal] lost — [reason]' to close it. If a prospect signs, just say '[Company] signed' — the deal closes as won and they become a client in the same breath. Your morning brief will also quietly flag a deal that's been silent too long.
- The five background upkeep tasks (sent-mail reconcile, chat sweep, weekly cleanup, weekly insights, monthly report) now run as one consolidated maintenance task that keeps its own ledger of what ran and what's owed — if your machine is closed when something was scheduled, it catches up at the next opportunity instead of silently skipping. To switch over, say `set up command room schedules` once — it takes about a minute and retires the five old tasks automatically.
- A few quiet improvements: when you send one email to some (but not all) people waiting on the same thing, that partial delivery is now recorded per person and the item stays open until everyone has it; casual observations that never got confirmed stop counting after 30 days (still searchable, just not cluttering your lists); and if a background task ever fails outright, your health check now says so in plain terms instead of staying silent.

## v4.7.1 (2026-07-13)

Fix: the new account and connector commands now respond when you say them.

- A quick fix on top of the account controls that shipped in the last update: saying 'what accounts do I have', '[address] is my personal account', or 'set my email backend to [connector]' now reliably reaches the right handler. If you tried one of these and got a generic answer instead of the real thing, it works now.

## v4.7.0 (2026-07-13)

Command Room now works with whatever mail and calendar you connect — and you can tell it which email accounts are business and which are personal.

- Command Room no longer assumes Gmail. It now resolves whatever mail and calendar connectors you actually have — Outlook, Superhuman, Google, or something newer — and picks the right tools automatically. Nothing to configure; if your setup worked yesterday, it works the same today.
- You can now tell Command Room what each connected email account is for. Say '[address] is my personal account' and that account's mail stays out of your business records permanently — important personal items can still show in your brief without ever being filed. Say 'what accounts do I have' to see the map. Until you classify an account, nothing changes.

## v4.6.3 (2026-07-10)

Your documents got a serious upgrade — a sharper default look out of the box, stat tiles on more deliverables, and a visual quality check before anything reaches you.

- Every Word deliverable now ships with a refined, executive-grade design with zero setup — cleaner headings, a sharper palette, and warm stat tiles. Nothing to configure; your next document simply looks better.
- The visual layer now reaches more deliverables: board packs, value receipts, and dormant-customer scans open with at-a-glance stat tiles, contract reviews get a flag matrix, and team briefs use the same engine as your meeting prep.
- Documents are now rendered and visually reviewed before they reach you — layout problems get caught and fixed instead of delivered.
- You can now set standing preferences for how your documents come out — say 'tune' or 'customize' plus the document type (for example 'customize memo writer') to adjust length, density, and how visual the output is. Preferences persist until you change them.

## v4.6.2 (2026-07-09)

The marathon finale — uncertain captures now come to you, noisy captures stay quiet, promises to groups track per person, and promises you make by email get captured too.

- Your daily Commitments chat now opens with a short 'Needs a quick confirm' section: anything captured with an unclear owner or a suspected duplicate comes to you with one-tap answers — Mine, Theirs (naming the person), or Drop. Answering 'Same as [person]' when a name was misheard teaches the workspace permanently. Ten seconds a day, and nothing uncertain ever silently becomes fact.
- Capture got quieter: things OTHER people promise each other in your meetings no longer land on your list — they're kept as background context and only surface if they come up again. Your open list stays proportional to what you actually owe and are owed. Items with a date or money attached always surface, and you can tune the strictness ('customize how commitments are captured') any time.
- Promises to a group now track per person: 'send the deck to the board' chases each board member individually, you can mark each one received as replies come in, and the item proposes closing itself only when everyone has delivered — you always make the final call.
- Promises you make in emails you SEND are now captured too — 'I'll get you the numbers Friday' in a reply becomes a tracked item automatically, and if it restates something already tracked, the two merge instead of doubling up.
- Plus a polish pass everywhere: people are always named by their correct name from your records (not how a transcript misheard them), email subject lines follow your voice rules, snoozed items are reviewable with 'show muted', and the morning brief carries a one-line health note when a background task needs attention.

## v4.6.1 (2026-07-09)

Install hotfix — I fixed a packaging setting that was quietly blocking new installs and updates for everyone. Nothing about how the workspace behaves changed.

## v4.6.0 (2026-07-09)

The dogfood release — every surface tells the truth, briefs got visual, and you can now say 'remind me'.

- New: reminders. Say `remind me about the proposal on Friday` and it appears in your morning brief that day — every day, until you clear it or push it. Ignored reminders get louder, personal ones stay private, and `show my reminders` lists everything. Repeating reminders work too (`remind me every Monday...`).
- Your meeting-prep briefs got a full rebuild: the automatic morning briefs now use the same deep engine as `prep me` — a walk-out-with objective, what changed since you last met (including overnight email), open decisions, and a two-column table of what's owed both ways, with stat tiles and a relationship timeline up top. One brief per meeting, refreshed in place.
- Reliability got stricter about the truth: `health check` now reports only what receipts on disk prove — never invented run history, never a guessed cause. Late runs are reported as late with real times, and every count in every surface (brief, commitments chat, triage) now comes from one shared ledger, so the numbers always agree.
- One vocabulary everywhere: buttons and text now use the same words on every surface (Done, Defer, Drop, Not mine, Make task), every snooze states its duration on the button, and buttons visibly light up when selected with a live 'N selected' counter. New abilities: `fix the wording` on a mis-captured item, `that's actually [name]'s` to reassign, `split that into...` to break one item into parts, `merge those two` for duplicates, and `show muted` to see and reverse anything you've snoozed.
- If Slack is connected, say `scan slack for commitments` — promises you made (and things people owe you) in Slack messages now get captured with the same care as meetings and email. Suspected duplicates across sources get flagged for your call, never silently doubled.

## v4.5.1 (2026-07-06)

Internal — routing metadata compliance; no user-visible changes.

## v4.5.0 (2026-07-04)

The consolidated trust wave — commitments that actually close, schedules that can't fail silently, and a workspace that learns your preferences.

- Your commitments list got a deep upgrade: checking something off now closes it everywhere, for good, and to-dos are tracked separately from promises so they don't get chased. Say `clean up my commitments` for a one-pass review of anything stale — every action has an undo.
- New: say `health check` any time for a status view of your scheduled chats — including anything that quietly stopped running. Late or missed runs are now detected automatically and noted in your Monday summary.
- You can now set standing preferences in plain words — say things like `customize morning-briefing: keep it under one page`. I'll remember it every time. `show morning-briefing customizations` lists what I've saved; removing one is one tap.
- This update added new background upkeep (including a nightly step that catches anything from your chats that should have been remembered). To switch it on, say `set up command room schedules` once — it takes about a minute and keeps all your existing times.

## v4.4.0 (2026-06-30)

Your writing voice is measured, not guessed, and shown back to you at the end of setup.

- Setup now measures how you actually write: sentence length, greetings and sign-offs, the phrases you reach for, how you say no. At the end it shows you two drafts of the same email, one generic and one in your voice, so you can see the difference.

## v4.3.0 (2026-06-30)

Setup asks one question fewer.

- The setup card no longer asks which email senders to leave out. You can still exclude one at any time by saying `add [domain] to my exclusion list`.

## v4.2.0 (2026-06-28)

Your Master Tracker and weekly insight views are self-maintaining again — both had silently stopped updating.

- Two of your generated views had quietly stopped refreshing — your Master Tracker and your weekly insight views (timeline, relationships, commitment-aging). Both are fixed and now self-maintain: the tracker rebuilds when you end a session and on your Sunday cleanup, and a new silent Weekly Insights task (Sundays 7 PM) keeps the analytical views current. Nothing for you to do — they catch up on their own. Want them refreshed right now? Say `rebuild views` for the tracker and `run insights` for the analytical views.

## v4.1.0 (2026-06-28)

Weekly cleanup now archives instead of deleting — nothing in your workspace is ever removed.

- Small change to how I keep your workspace tidy: when I clear out old briefings, prep notes, and reports, I now move them into an archive folder instead of deleting them. Nothing is ever removed — if you ever want an old file back, it's still there. You don't need to do anything.

## v4.0.1 (2026-06-22)

Patch — fixed three crashes found in dogfooding (the active-list showing names not IDs, the usage report, and the weekly relationship-moves surface). Nothing to do; the fixes apply on the next run of each.

## v4.0.0 (2026-06-21)

Major release — the gate/reliability stack lands plus a wave of new capability: voice-calibration loop, on-demand deliverable quality scan, retention hooks, outcome tracking, weekly relationship moves, and memory-layer hardening (dedup index, stricter record schemas, event-log sharding, output regression harness). Nothing to do — new skills are discoverable by phrase.

## v3.20.0 (2026-06-14)

Under-the-hood quality hardening — writing standards are now enforced at save time and concurrent writes are race-safe. Nothing to do.

## v3.19.0 (2026-06-14)

Two new skills: `convene the board` for a multi-perspective review, and `forge my advisor profile` to share your archetype.

- New: I can convene a board on any decision. Say `convene the board on [subject]` and a CFO, COO, customer voice, a skeptic, and a strategy seat each weigh in from your real numbers and history — then I hand you back where they disagree, because that's where the risk hides. Say `configure my board` to change the seats.
- New: say `forge my advisor profile` and I'll distill how you decide into a shareable pack a colleague can load into their own Command Room — so you can sit on their board. Or `model [name] as an advisor` to build your own read of how a colleague thinks (that one stays local to you).

## v3.18.17 (2026-06-04)

research now actively uses your connected Tavily / Vibe Prospecting tools instead of defaulting to built-in web. No user action needed.

## v3.18.16 (2026-06-04)

Research brief restyled — dark Command Room branding. No user action needed.

## v3.18.15 (2026-06-04)

New skill: research — verified briefs on any company, person, or market, folded back into your workspace.

- New skill: research. When you need to learn something that isn't already in your workspace or your meetings, just say 'research [company]', 'look into [company] before my call', 'background on [person]', or 'pull together research on [topic]'. I'll frame it against what you already have on file, run verified web research with citations, and hand you a branded brief — then offer to save the findings into your workspace so they compound into future briefs. It works out of the box on built-in web search (no setup). If you've connected Tavily it searches the web deeper; if you've connected Vibe Prospecting it also verifies company size/revenue, surfaces funding and hiring signals, and identifies the real decision-makers. The brief always shows which sources it used.

## v3.18.14 (2026-06-01)

Reconciliation now actually closes the follow-ups you've sent: it matches an email to a commitment by the recipient's name in the commitment, not just a linked contact record — so 'Send Sam the deck' closes when you email Sam, even if Sam wasn't linked or had no address on file. It also now reliably knows which person is you, so your 'you owe' list and the matching are attributed correctly.

## v3.18.13 (2026-06-01)

The first run of the new sent-mail reconcile now looks back 30 days, so follow-ups you sent before it started get caught instead of stranded. If yours already ran once and missed the backlog, just say 'catch up my sent mail' and it'll sweep the last 30 days once.

## v3.18.12 (2026-06-01)

Closing the follow-ups you've already sent now runs on its own each weekday morning, as its own quiet background task — so they stop showing up as still-owed. Your morning brief just reads the result. If that task ever runs late, the brief now says 'you may have already handled this' instead of telling you to redo it. Nothing for you to set up; the catch-up of anything already sent happens automatically on the first run.

## v3.18.11 (2026-06-01)

Command Room now reconciles your sent mail automatically as part of your morning email pass — so follow-ups you completed by emailing someone directly stop showing up as still-owed. And if that reconciliation is ever behind, your brief now says 'you may have already handled this' instead of telling you to redo it. Both happen on their own; nothing for you to do.

## v3.18.10 (2026-06-01)

Your update now reaches your existing projects: the recent people-list fix — dropping stray contacts who showed up once but aren't really on a project — gets applied to your projects the moment you update, instead of waiting for the weekly cleanup. Plus a behind-the-scenes fix so updates from much older versions never skip a step.

## v3.18.9 (2026-06-01)

Reworks a batch of fixes the real-data test loop caught as broken or incomplete: your morning brief now actually reconciles the follow-ups you sent straight from Gmail (instead of just claiming it did), stops telling you to redo work you've already done, and shows every prospect that looks like a client — and your project people-lists drop stray vendor/demo contacts. All of it surfaces automatically on your next brief and weekly cleanup; nothing for you to do.

## v3.18.8 (2026-05-31)

The 'this prospect looks like a client now' nudge now also shows up in your daily brief (not just the coach and weekly cleanup) — still a suggestion you confirm, never an automatic change.

## v3.18.7 (2026-05-31)

Command Room now notices when a prospect looks like it's become a client (an active engagement, an active project, or signing language in a recent call) and nudges you to convert them — it never reclassifies on its own. Pairs with the new "[Name] is now a client" command.

## v3.18.6 (2026-05-31)

You can now close the loop on a prospect: say "[Name] is now a client" and Command Room converts them — flips the relationship, links the engagement, and offers to set up the project. Previously this was offered but never actually did anything.

- New command: when a prospect becomes a paying client, just say "[Name] is now a client" (or "promote [Name] to client"). I'll flip them from prospect to client, link the engagement to your company, and offer to set up their project folder — reusing the existing record, no duplicate. This used to be offered but never worked; now it does. If any of your current prospects have already signed, you can convert them now.

## v3.18.5 (2026-05-31)

Reliability fixes from live testing — the weekly cleanup now actually updates your project brains, your commitment counts line up across the brief and the coach, sent follow-ups close themselves, and the workspace map renderer is no longer broken. Nothing for you to do.

## v3.18.4 (2026-05-31)

Two small polish fixes — opening a project no longer suggests people who aren't on it, and the updater stops claiming a dashboard is installed when it isn't. Nothing for you to do.

## v3.18.3 (2026-05-31)

Your commitment counts now tell the truth — the coach and the morning brief agree, and follow-ups you already sent from Gmail close themselves (with an undo) instead of nagging you to redo them.

## v3.18.2 (2026-05-31)

Four quiet reliability fixes — the weekly cleanup now registers and runs its full self-heal, new prospects get tracked properly, and opening a project shows you who's on it. Nothing for you to do.

## v3.18.1 (2026-05-31)

Small fix so the automatic activity-log repair actually runs on update (it now heals any current glitches immediately, instead of quietly skipping).

## v3.18.0 (2026-05-31)

Your workspace now repairs any small glitches in your activity log the moment you update — and an old issue that could leave those glitches behind is fixed at the source.

## v3.17.2 (2026-05-30)

Saying new prospect records the prospect properly.

- Saying `new prospect` now creates the organisation and its deal record the same careful way every other record is created, and the deal's stage is written on that record.

## v3.17.1 (2026-05-30)

Your people directory is rebuilt from your records every week.

- The people directory had drifted from your actual contacts. It is now regenerated from your records during the Sunday tidy-up, so the count and the details stay right.

## v3.17.0 (2026-05-30)

A Sunday evening tidy-up, with a Monday note on what was handled for you.

- Command Room now tidies your workspace every Sunday at 6 PM. It refreshes every project page and leaves a short note for Monday: what it tidied, what it handled for you during the week, and what is waiting on you.

## v3.16.0 (2026-05-30)

Project pages refresh themselves, and new people and organisations stop going missing from lists.

- Each project's page now redraws its people and its status from your records every time you open it, instead of freezing at the moment it was written. Behind the scenes, your records are now stored the same way every time, so a newly added person or organisation shows up where it should.

## v3.15.0 (2026-05-29)

Your workspace now tidies itself and quietly repairs small glitches on its own — you'll only hear about it when something needs your eye.

## v3.14.8 (2026-05-29)

Your commitment counts and 'reply to X' list are now computed the same way every time — internal reliability work, nothing to do.

## v3.14.7 (2026-05-29)

Scheduling commitments now close on the calendar, not just the inbox — fixed silently on your next briefing.

## v3.14.6 (2026-05-28)

Reading a transcript or an email on demand now remembers the people in it.

- Pulling a meeting or an email into the chat used to show you what you asked for and forget the new people it mentioned. They are now added to your contacts the same way the scheduled chats add them.

## v3.14.5 (2026-05-28)

Something you are waiting on closes itself when the other person's email delivers it.

- When a contact emails you the thing they promised, with words like here is the deck or as promised, the item leaves your waiting-on list on its own. A message that only says they are still working on it leaves the item open.

## v3.14.4 (2026-05-26)

The system does its own plumbing now — no more migration prompts you don't understand.

- Behavior change worth knowing: when an update introduces a small repair or backfill for your workspace, I now do it on your behalf and tell you what I did — instead of asking you to type a phrase to make it happen. Three things that used to prompt you (memory hygiene, linking older to-discuss items, registering missing default chats) now just happen silently when applicable, with a one-line notice after. The only things I'll still ask about are genuine choices — your assistant's name, what kind of operator you are — that only you can answer.
- Same change applied to your org classifications: when I first learn the vendor / prospect / dormant taxonomy on your workspace, I auto-tier from your email patterns instead of asking you to confirm each one. If you ever want to review the inferred tiers, say `recheck my org classifications`.

## v3.14.3 (2026-05-26)

Multi-draft emails render in one widget; Friday Wrap fills in for older installs.

- When you ask me to draft multiple emails at once — a stage 1 and a stage 2, a with-intro and a without-intro variant, two recipients in parallel — I now render all of them in a single widget. Each draft gets its own send / edit then send / draft / skip controls so you can pick what to ship and what to drop visually. No more typing `send stage 2` to choose.
- One thing to know if you hit it: after a plugin update, your scheduled chats may briefly point at the old plugin until Cowork's underlying cache refreshes. If tomorrow's morning brief (or any scheduled chat) shows a message about not being able to load its instructions, the fix is to fully quit and reopen Cowork — end every Cowork process in Task Manager / Activity Monitor, not just close the window. When you reopen, type `set up command room schedules` to re-bind your tasks against the new install. Your tasks themselves aren't broken — only the mount needs a refresh.

## v3.14.2 (2026-05-26)

Your AI has a name now, and it routes when you call it.

- Two things landed in this update: 1. **Address me by name.** Whatever you named me (default `Penelope`, change with `name my AI [Name]`), say it at the start of any request and I'll know you're talking to me. `Penelope, prep me for my 2pm` routes to call-prep. `Penelope, draft an email to Sam` routes to email-writer. `Penelope, what's going on` runs the full briefing. The name strips off and the rest of your message routes normally — I'm not adding a step, I'm catching the natural way you'd address a chief of staff. 2. **`new project` works on its own.** Before this, saying `new project` and then pasting context in a follow-up message often didn't fire project creation — you had to say `new project in command room` to force it. Now `new project` (plus any natural variant like `start a new project` / `create a new project` / `I want to add a new project`) locks the routing as soon as you say it. If you don't include the name inline, my first follow-up is just `What's the project name?` — no open-ended re-prompts. Also: my name now shows up across briefing intros, call-prep covers, meeting-notes acknowledgments, board pack covers, decision memo bylines, the weekly audit summary, and the project roster footer. Wherever the back-and-forth happens, it should feel like a real conversation with a named operator from here on.

## v3.14.1.3 (2026-05-26)

stalled-projects UX flip — show, then tune (no questions on first fire)

- Better UX for stalled-projects. The first version blocked you behind 3 setup questions before showing anything useful. Now: first time you fire 'show me stalled projects', it just shows you your stalled threads using the recommended thresholds, then asks if you want to tune. You can also give freeform feedback ('flag paused threads sooner', 'I don't care about exploring threads') and it'll parse that into a config change without walking the full questionnaire. Same canonical pattern — 'show, then tune' — will roll out to every other customizable skill from here.

## v3.14.1.2 (2026-05-26)

Stalled-projects fix — now reads every workspace shape cleanly + handles every project status.

- Quick bugfix on the stalled-projects detector. The first version only worked on workspaces with an older data shape — most workspaces use the newer one and got zero results. Now I read both, use each project's own last-activity timestamp as the primary signal, and handle every project status (active, exploring, paused, blocked, dormant, archived — the prior version only knew three). If you already configured stalled-projects, your settings carry forward. Say `change my stall settings` to add thresholds for the new statuses, or `show me stalled projects` to run the fixed detector.

## v3.14.1.1 (2026-05-26)

Settings UX — reconfigure, show, reset paths for config-aware skills

- Small UX upgrade — for skills with first-run setup questions (currently just stalled-projects, more coming), you can now change your answers anytime. Say 'change my stall settings' or 'reconfigure stalled-projects' to walk the questions again with your current answers pre-filled. Say 'show my stall settings' to just see what's set. Say 'reset stall settings' to start over from defaults. Future skills with setup questions will all support this same pattern.

## v3.14.1 (2026-05-26)

New skill — stalled-projects: surface projects that have gone quiet

- New skill — stalled-projects. Surfaces every project that has gone quiet (no meetings, commitments, decisions, or real conversations within a threshold you set: 14 days for active, 30 for exploring, 60 for dormant — all configurable on first fire). Fire it with 'show me stalled projects' or 'what's stalled'. Projects you created but never touched also surface, with a different recommended action. In an upcoming release this will also surface automatically in Pulse + Friday Wrap.

## v3.14.0 (2026-05-26)

v3.14 foundation — first-run questionnaire infrastructure + new event types

- Quiet foundation update preparing for new skills coming in upcoming releases. Adds the infrastructure for skills to remember your first-time setup answers between sessions, so when a new skill asks you 3-5 setup questions, it only asks once — not every time you fire it. Nothing for you to do; the next wave of skills will use this automatically.

## v3.13.8.3 (2026-05-25)

Patch — activity-log writer hygiene (sequence + timestamp auto-stamped) + repeat-safe update reminders

- Quiet reliability fix: tightened the activity-log writer so every entry now gets a sequence number and timestamp automatically — even if a skill's template forgot to include them. This closes a class of bugs where ~36% of historic entries were missing sequence numbers (breaking the 'discuss → resolve → close underlying' chain in a few corner cases) and the occasional empty-timestamp entry that's been quietly polluting the tail. Nothing for you to do; new entries from now on will be clean.
- Behavior change worth knowing: when I have something specific for you to do (like naming your AI or picking your workspace shape), I'll keep mentioning it on each update until you take the action or tell me to skip it — previously these quietly disappeared after the first surface even if nothing was done. Informational notes still fire once. If a re-nudge is bugging you, say `skip [item]` and it stops permanently.

## v3.13.8.2 (2026-05-24)

A small follow-up — naming, and a quiet housekeeping pass on your activity log.

- Housekeeping — I tightened up your activity log so a few new kinds of notes I write are recognized cleanly. You won't see a difference; your weekly audit will just run quieter going forward.

## v3.13.8.1 (2026-05-24)

A small reliability pass — the activity log is sturdier, and a few stale items in your to-discuss list can be linked up.

- I closed a subtle writer bug that was occasionally re-introducing malformed lines into your activity log even after a recovery ran. Recovery is now a one-time fix, not a recurring chore.
- When I run a repair on your activity log, you'll see a friendly summary now instead of raw output.
- Recovery now carries a few small details that make your weekly audit's recap of any repair cleaner and more accurate.
- Honest note: I tightened how I prepare interactive cards for you, but the very last step still goes through the older path because of a limitation outside my control. Your experience is unchanged either way — and the moment that limitation lifts, I'll switch automatically with no work on your end.
- Internal tidying — nothing changes for you.

## v3.13.8 (2026-05-24)

A bigger update — sturdier under the hood, sharper documents, fewer ways for things to go wrong.

- I tightened up how your interactive cards render — same look, but every one now passes through my safety checks before it reaches you. Wider coverage means fewer odd buttons or missing details slip through.
- Every document I produce for you — memos, one-pagers, board packs, stress tests, decision docs — now runs through one polished render pipeline. Real headings, consistent typography, and a final scan that catches internal jargon before it reaches you. Tables in decision memos and ranked safeguards in stress tests look cleaner too.
- Your activity history reader is sturdier now. If a stray malformed line ever sneaks in, I notice it and tell you instead of crashing or silently swallowing it. The repair tool can also clean it up when you ask.
- I'll never show you a Send button next to a contact whose email I don't actually have. If the address is missing, I'll show you `add email then send` instead — type it once and I'll remember it for next time.
- I now resolve names the same way everywhere — when you mention 'Bo' or 'Northstar' in any conversation, it lands on the same person or project no matter which part of me is listening. Your daily brief also reads from the same open-commitments source as the rest of me, so the count matches what you see in your discuss list.
- When I'm updating several of your files in one pass, I now treat the whole batch as one safe transaction — either all of it lands cleanly or none of it does. No more half-applied changes if something gets interrupted mid-flow.
- When I log a new entry in your activity history, the numbering helper is more careful now — it skips over any junk lines or oddly-numbered residue from old bugs so your sequence stays clean going forward.
- Interactive cards now follow consistent rules whether there's one item, many items, or just something for you to read. Same look across every surface I produce.
- Housekeeping — your activity log now properly recognizes a few new kinds of notes I'd been writing. You won't see a difference; your weekly audit will just run quieter going forward.
- Your scheduled chats now report their work more cleanly behind the scenes. Your usage report will give you a more accurate picture of where your time goes.

## v3.13.7 (2026-05-23)

Email drafts wait for your click, and a quiet contact is checked against live mail before anyone says so.

- Drafting an email now shows you the card first and writes the draft only when you choose. Before anyone is flagged as going quiet, I check recent mail and calendar activity. The commitments card stays small enough to arrive every time, a name that could be two people asks you which one, and the morning brief drops an item once you have replied.

## v3.13.6 (2026-05-21)

Slow-pass update — 7 ship-blockers and 8 high-severity bugs caught by actually running code against realistic data, not just static checks.

- I fixed a crash in my name-resolution helper that's been silently broken for a couple of updates. The same helper underlies the `go [name]` flow and transcript-name search — both have been quietly returning empty results. Now fixed and tested against realistic data.
- I fixed two bugs in your Friday Wrap weekly recap that would have crashed every Friday afternoon. The recap now produces the actual weekly document instead of crashing.
- I fixed a half-merged template in your monthly recap and a three-sided contradiction about output format. The report now produces what was specified, with the synthesis lead I added earlier.
- The workspace doc cleanup I offered a couple of updates back was non-idempotent — the replacement text contained the same marker the detector watched for, so the prompt kept firing forever even after you'd updated. Fixed; clicking through now resolves it permanently.
- The org-record repair I offered a couple updates back had three bugs (raw error on validation failure, partial-write on mid-loop crash, dry-run that didn't validate so the preview lied). Rewrote as transactional — either every org repairs successfully or none are written. Dry-run now actually validates.
- The person and org writers were producing malformed entries in your activity log on every record write. Every workspace had polluted entries that failed validation. Fixed the shape; added the missing note types to my recognized list.
- The intro-followup flow had a silent shape mismatch between the writer and the reader — every resolution wrote entries with empty people lists. Now both sides agree, with back-compat for in-flight entries from before.
- Internal tidying — my insight generator had a few half-merge spots between an older shape and the newer one. All reconciled. Nothing changes for you.
- The first-install onboarding flow had three stale references (a removed step, a non-existent playbook file, a retired skill). New users following the install path no longer hit broken pointers. Corrections now flow through natural-language commands like `move X under Y` and `merge those orgs`.
- Housekeeping — I now properly recognize a few fields I'd been writing without formal recognition. You won't see a difference.

## v3.13.5 (2026-05-20)

Cleanup pass — every remaining drift item from the four-round verification round closed (no new functionality, pure hygiene).

- I fixed three internal cross-references that pointed at files that don't exist. An agent following those references for context would have hit a dead end. Now they resolve.
- I swept some stale action-button references from documentation prose that escaped my structural guard (the guard scans code blocks, not prose tables). Examples used button names that had been consolidated to newer canonical names. All updated.
- Internal tidying — my email draft protocol now has consistent references on both sides of its scope contract. Nothing changes for you.

## v3.13.4 (2026-05-20)

Third-round verification fixes (two more would-crash bugs caught) + a new automated guard so this class of bug can't ship silently again.

- Third-round verification caught a sister bug in an upcoming-meetings example — body text containing an internal phrase that trips my jargon scanner. Any orchestrator copying the example would have crashed. Rewrote the example in plain language and added a new automated check that scans every example body text across every spec, so this class can't silently ship again.
- Calendar invites would have crashed on every fire because my email-shape detector saw `To` + `Subject` in the invite's data and demanded a `draft` button — but calendar invites don't use a draft button. The detector now recognizes calendar-shaped items (time / duration / location / date fields present) and uses send / edit then send / skip, by design.
- Internal tidying — two of my style rules were contradicting each other. Reconciled so internal authors aren't misdirected. Nothing changes for you.
- I reworded three user-facing strings that would have been blocked by my privacy scanner before reaching you — they were guidance shown when an email-send setup needs attention. Reworded to describe the issue without leaking internal details.
- I added a new automated guard that scans every literal string in every example card across every spec — at commit time, not just at render time. Two updates in a row had the same bug class slip through; this stops the pattern.

## v3.13.3 (2026-05-20)

Caught one more crash in the intro-followup surface + tied up paths and apply-time handlers from the prior round.

- Second-round verification caught one more crash in the intro-followup card. The action buttons were fixed, but the description text still contained a phrase that trips my internal jargon scanner — so the card would crash with a different error. Fixed.
- I added the apply-time handler documentation for the three intro-followup action buttons from the prior update — they were recognized by the renderer but the dispatch logic for what happens on click wasn't documented. Every button now has a documented path so the intro-followup state persists correctly across pulses.
- Internal tidying — nothing changes for you.

## v3.13.2 (2026-05-20)

Caught and fixed two ship-blockers in the intro-followup and revival flows + sound-alike name matching across multi-token names.

- I caught and fixed a class of bug that had been quietly shipping for several updates: three action buttons in the intro-followup card (the surface that re-checks 'did this intro land?') weren't recognized — the card would crash before showing you anything. Now `landed`, `didn't land`, and `snooze 14 days` are properly wired, and a new automated check makes sure no future orphan button slips through. If you've ever wondered why intro-followups never appeared, that's why. Going forward they will.
- The skill that finds quiet conversations worth reviving now uses the same editable card pattern as every other email-emitting skill. Before, it was the outlier — surfacing drafts as a plain markdown list with broken buttons. Now you get `draft / resolved / add to my list / skip`. For conversations where someone owes you something, clicking `draft` routes through the commitment-chase flow automatically.
- I swept some older action-button names from my documentation — they got consolidated into a cleaner verb a while back, but a few specs still referenced the old names, which would have caused crashes if anything followed them literally. All cleaned up.
- When you misspell someone's first name (e.g., `Arya` for `Aria Sample`), I now correctly find them. Before, I only matched single-token names — multi-token canonical names would silently miss against single-word queries. Sound-alike matching now works token-by-token, so `Arya` matches the first part of `Aria Sample` and `Northstarr` matches `Northstar Sample Co`.

## v3.13.1 (2026-05-20)

Voice cleanup across the rest of my skills + one-question-with-options for vague asks + editable inbox reply cards.

- I finished the voice cleanup across the rest of my skills. Every surface you read is now reframed — morning brief, weekly recap, inbox triage, follow-ups, intro drafts, thread resurrection, meeting prep, decision revisits, dormant scan, automation scanner, onboarding, the update prompts, and more. No more FAIL framing, no scores in front of you, no internal file paths, no internal phase or pass labels in your text.
- When you ask something genuinely vague (`help`, `catch me up`, `what now`), I now respond with ONE focused question and two to four concrete options — not a wall of open-ended questions. If you mention a name in your message, I check your workspace first so I don't ask about something already on file.
- When you triage your inbox, your Reply Now drafts now show up as editable email cards — the same kind your scheduled tasks already use. Edit, send, save to drafts, or skip, all inline. Decision-needed, deep-read, and FYI items stay as list entries since they don't carry a draft.

## v3.13.0 (2026-05-20)

A big update — better people / org memory, sharper name matching, sturdier document rendering, friendlier voice across every surface you read.

- I noticed your workspace docs (your master `CLAUDE.md` and infrastructure notes) still reference an older folder layout I retired a while back. Nothing breaks, but every fresh session reading them gets the wrong location for things. Let me show you the replacement text to paste in.
- I improved how I store people — you can now have multiple emails, phones, and nicknames per person, all properly tracked. Your existing records may have older shape drift; I can run a one-time cleanup with a dry-run preview before applying anything. Backup taken automatically.
- When several parts of me write to your memory at the same time, we no longer step on each other's changes. If a write somehow produces a broken file, I auto-restore from the most recent backup and surface the failure rather than letting silent issues accumulate.
- I now have a canonical way to write organization records — validated, deduped, and consistent. Your older org records may have shape drift; I can run a one-time repair with a dry-run preview. No data lost.
- When a new person enters your workspace from a meeting or calendar, I now auto-attach them to their org when the signal is strong (matching work-domain or explicit hint). Free-mail addresses (gmail, yahoo, icloud, etc.) never auto-trigger. For your existing unattributed people, I can run a one-time backfill with a dry-run preview — any new orgs I'd create show up before they're saved.
- When you type into a card's `+ Add context` field but don't click an action, your typed text is no longer dropped — it's saved to your discuss list so you can revisit it. The Apply button enables as you type, even without selecting an action. Affects every action card across your scheduled chats and meeting notes.
- Internal plumbing — cards now render through their canonical pathway with full validation. Same look for you; sturdier guarantees behind the scenes.
- When I draft emails for you — direct drafts, intro drafts, follow-up drafts — they now show up as editable cards instead of plain text previews. You can edit, send, save to drafts, or skip, all inline. Inbox reply paths and quiet-conversation revival drafts get the same treatment in a later update.
- Deliverable links in my replies now open reliably on Windows when your workspace folder contains spaces (which is the default). Previously the link would silently fail to open. Now it works.
- When I produce a document for you, the link to it in my reply is now a bold heading at the bottom of the turn — easy to find, never buried mid-response. Multi-document surfaces use smaller headings to stay scannable.
- Reveal-in-folder cards no longer dual-surface alongside the clickable link. The link is what you click; the card was redundant.
- Name resolution is sharper now. When you mention `Arya` or `Northstar` anywhere, I match through exact alias, fuzzy similarity, and sound-alike — so a misspelled name or a brand never mentioned in transcripts still finds the right person or org instead of producing four open-ended clarifying questions.
- Your decision log view now actually updates. Previously it claimed to be auto-generated but no code was producing it — yours was significantly stale. It now rebuilds whenever any decision-related skill writes, and the first run during this update brings it current.
- Inbox triage now reads the actual latest message of every thread before classifying it. Before, it sometimes relied on a truncated slice — so threads where you'd already replied could be misclassified as 'stalled on you.' That's fixed; whose ball it is now keys off the latest message direction.
- I never silently create a calendar event anymore. Every invite shows up as an editable card with `send` / `edit then send` / `skip` — and the actual write to your calendar only fires when you click send.
- Your morning brief got a substantial rebuild. The Top 3 moves you need to make today now appear near the top (after the one-line theme and your commitments at a glance), not buried at the bottom. New: deltas instead of raw counts (+3 since Monday), going-quiet relationships promoted to a top-tier section, clickable deliverable links, internal vs external work visually split, and a since-yesterday momentum line. Vocabulary kept portable so the brief renders cleanly when shared. Verify by firing the next morning brief.
- When a scheduled chat finds nothing to surface (no urgent items, no one going quiet), the output now explains why — what window was scanned, what was filtered — and links you back to the last non-empty run. So an empty day is never a dead end, and you can tell whether the system found nothing or hiccuped.
- Pulse no longer flags people you've recently contacted as dormant. Before, if you emailed someone today and the inbox skill hadn't logged it yet, Pulse still saw silence. Now a live Gmail and Calendar check runs before any dormancy flag — recent contact catches you up.
- Your Friday Wrap now splits 'You Owe' into External (client commitments) and Internal (your own backlog) when both exist. Cleaner separation; the Internal section only renders when you're the operator of the system itself.
- Your monthly recap now opens with a one-paragraph synthesis of the month — the anchor moment, the theme it crystallized, what came of it — before the metrics. Numbers stay where they were; theme leads.
- I tightened my voice rules. No more alarming framing (FAIL / CRITICAL / ABORT), no more scores or grades shown to you, no more internal mechanism names in your replies, no more self-narration (scanning, regenerating, validating). Calm, friendly assistant tone; lead with the action; numbers only when factual. Applied immediately to weekly audits and insights; the cross-skill sweep finishes in the next update.
- Your weekly audit looks different now. One-line friendly summary (Healthy / Drifting / Needs work in human voice), top 3 prioritized actions for the week, then smaller follow-ups by category, then aging commitments framed as 'getting older' instead of 'overdue.' No scores, no color-coded grades, no FAIL framing. The deeper Word document follows the same friendly voice.
- Insights I generate are now strictly about people, customers, relationships, and your business — never about your workspace plumbing or system health (those belong in the weekly audit). The output is a Word document with a clickable link at the bottom of the turn. Classification review and project proposals surface as separate interactive cards.

## v3.12.2 (2026-05-20)

Stronger privacy protection for the plugin source — write-time guard catches name leaks before they ship

## v3.12.1 (2026-05-20)

Polish pass — release-note language refresh and example sanitization

## v3.12.0 (2026-05-20)

Performance and reliability improvements across the plugin

## v3.11.5 (2026-05-20)

Better team intelligence — 1:1 prep and team status views show the latest from your calendar and email

## v3.11.4 (2026-05-20)

Workspace consistency improvements — your daily flows stay in sync with the underlying data

## v3.11.3 (2026-05-20)

Sharper Morning Brief — cleaner inbox summaries and more accurate project status

## v3.11.2 (2026-05-19)

Audit cleanup — small fixes from a sweep across the last several releases.

- A small cleanup pass — no new features. Tied up about 17 paper-cuts and consistency items that surfaced after the recent release arc: automation scaffolding now actually uses its helper, monthly intro check-ins surface in your daily pulse, stale references to retired skills swept across documentation, decision-revisit routing fixed, Friday Wrap counts reconciled, board-pack QuickBooks failure modes documented, and a new automated guard to block this drift class going forward. Two new note types added to my recognized list. No action needed.

## v3.11.1 (2026-05-19)

Quick Commands artifact expanded to 45-card test surface (10 categories, every user-facing skill)

## v3.11.0 (2026-05-19)

Friday Wrap weekly recap arrives — and the Commitment Cockpit retires.

- I retired the Commitment Cockpit dashboard. The kanban surface (you owe / they owe / both stuck) is now folded into your daily Commitments scheduled chat, which carries inline action surfaces — strictly better than a snapshot-only dashboard. If you have the Cockpit pinned in your sidebar, you can leave it (it just won't refresh) or unpin it. No data lost.
- I added Friday Wrap — your first weekly-rhythm scheduled chat. It fires 4 PM Fridays your local time and produces a 7-day cross-source recap saved as a document. To register it in your workspace, type `set up command room schedules`. Your existing scheduled tasks stay; Friday Wrap is added alongside. On first fire, Cowork will surface a one-time permission prompt — click Run Now to authorize.

## v3.10.0 (2026-05-19)

Helper plumbing for the new skills — every document I produce now uses the canonical layout pipeline.

- Plumbing for the new skills I added — every document they produce (memos, board packs, decision memos, automation scaffolds) now runs through the same polished layout pipeline as the rest of my output. The user-facing behavior of every skill is unchanged; their documents just look better. No action needed.

## v3.9.1 (2026-05-19)

How-it-works guide updated for the new skills.

## v3.9.0 (2026-05-19)

Six internal diagnostic skills retired — your trigger router is cleaner.

- I retired six skills that were internal diagnostics — they only fired on phrases customers never used, and they cluttered routing. A few may interest you: speech-prep is being phased out (use `memo on` with a board-update or position-paper angle instead — produces stronger talking-point output). The other five moved to a separate internal plugin. Customer-visible change: 'speech prep', 'prep me to speak', 'help me prepare for the keynote' no longer route here — use memo writing for talking points.

## v3.8.0 (2026-05-19)

Eight new skills — calendar scheduling, contract review, intro drafting, decision memos, board packs, and more.

- I gained eight new skills that all build on your accumulated memory. (1) Calendar scheduling — I can write to your calendar now, with context-aware agendas pulling open commitments. (2) Contract review — parse contracts, compare against your standards, flag deviations, suggest redlines, history-aware. (3) Automation scaffolding — pair with the automation scanner to get a real working Zapier zap, Python script, or n8n flow for the opportunities I surface. (4) Decision revisiting — find decisions worth re-examining based on time elapsed and contradictory new signal. (5) Thread resurrection — find specific quiet conversations worth reviving (different from the dormant-customer scan, which finds quiet people). (6) Intro brokering — draft both-sided intros voice-calibrated to your past ones, with a follow-up check a month out. (7) Decision memos — structured tradeoff analysis with weighted criteria and a recommended call. (8) Board pack assembly — a multi-page board pack that writes itself from your activity history, decision log, people / project memory, and QuickBooks. Try them by name: `build the board pack for next Tuesday`, `review this contract`, `intro Bo to Rio`, `what decisions should I revisit`, and more.

## v3.7.1 (2026-05-19)

Six skills now contribute to my memory — past one-pagers, automation scans, sent emails, transcript searches, decisions, and follow-ups are all remembered now.

- I upgraded six skills to log what they do, so the rest of me can reference it later. One-pagers from 3 months ago are now visible to me. Automation scans dedup against prior ones. Email drafts now naturally address what's already open between you and the recipient. Repeated transcript searches become an insight signal — search 'pricing' 7 times in 60 days and the next pulse will surface a pricing insight. Memo writing auto-logs the decision when the memo IS a decision. Follow-up packs link back to the source meeting. No action needed — next time any of these fires, the memory lands.

## v3.7.0 (2026-05-19)

Polished reports save as Word documents now — no more loose markdown files for things you read.

- Every polished report I produce — automation scans, dormancy reports, audits, monthly recaps, follow-up packs, summaries — now saves as a Word document so it renders correctly in Word and Pages. Email drafts no longer save as files at all; they go straight to your Gmail Drafts. Files I use as working memory (briefings, insights, notes, project context) stay in their original format since they're for me to read, not you. No action needed — next time any of these fires, you get the document.

## v3.6.5 (2026-05-19)

Two sidebar dashboards redesigned — cleaner navigation tree + sharper Quick Commands typography.

- I redesigned two of your sidebar dashboards. The Workspace Map is now a pure navigation tree — org → projects, with optional stuck-people inline. The engagement chart, count pills, key-people block, and Run Weekly Audit button are gone (use the `weekly audit` chat trigger instead). All sections are collapsible. The Quick Commands cheat sheet got a typography pass for legibility — same command set, easier to read. Both auto-rebuilt during this update.

## v3.6.4 (2026-05-19)

Your meeting briefs got noticeably richer.

- I fixed a drift where some content I extract from meetings wasn't making it into your briefs. From now on, your morning Upcoming Meetings brief includes a since-your-last-brief delta, decisions already on the record, and per-attendee tagging on talking points when 2+ people are in the room. Your evening Past Meetings brief preserves scope changes and financial details in the document, and the notes I append keep the deeper analysis — risks, opportunities, timeline, relationships — that was getting dropped. Your next scheduled fire picks up the new content automatically.

## v3.6.3 (2026-05-19)

Privacy sweep deepened — domain allowlist + dependency pin.

## v3.6.2 (2026-05-18)

Privacy sweep finished + new structural guard against future drift.

## v3.6.1 (2026-05-18)

Honest documentation pass + dependency pin.

## v3.6.0 (2026-05-18)

Documents are now scanned for internal jargon before they reach you.

## v3.5.3 (2026-05-18)

A saved-file message names the folder where the file actually landed.

- When I tell you where I saved a brief, the location is now the folder on your own computer where the file landed. Some installs were shown a folder from another machine; that is gone.

## v3.5.2 (2026-05-18)

Sample names replace real ones throughout the product's own notes and examples.

- Nothing changes in how Command Room works for you. The product's own notes and examples now use sample names only, so nothing about any real person travels with an update.

## v3.5.1 (2026-05-18)

Two small polish items before broader rollout.

- Final polish — two routing-edge cases tightened, plus housekeeping on a few note types I write. No action needed.

## v3.5.0 (2026-05-17)

Eleven small reliability fixes — sturdier matching, captured intel actually shows up, snooze respected, plus a fresh how-it-works guide.

- A small reliability pass — eleven things I tightened up. Dormancy detection now catches people whose involvement was logged in older note shapes. Intel you capture now shows up in your timeline. Snoozing something for 3 days actually waits 3 days. Common confidence thresholds are now shared (no more drift). Silent failures in scheduled-task passes get logged so you can see them in your usage report. Status changes propagate cleanly across your views. Plus a few naming and routing cleanups, and a new top-level how-it-works guide. Most changes apply automatically on the next scheduled fire — you may notice your dormancy list shift slightly tomorrow because the older inconsistency was masking some real silence.

## v3.4.5 (2026-05-17)

Update flow learns to play per-version fixes — and decisions can now close.

- When you say `update command room` now, I'll also check for any per-update fixes that apply to your workspace — surface anything you need to know about, announce new skills, run small repairs in the background. Every future update plays these automatically, and I only surface what actually applies to you.
- Decisions you logged before now close themselves. When a new meeting transcript shows that something got signed / kicked off / 'went with', I write a decision-resolved note automatically. If you change direction (changed our mind / switching to / decided against), I mark it superseded. Conservative threshold — borderline calls don't act.

## v3.4.4 (2026-05-17)

Commitments — older items will surface in your daily fire.

## v3.4.3 (2026-05-17)

Onboarding widget update — first-time setup is smoother.

## v3.4.2 (2026-05-17)

A new way to triage inbound bug reports — say `process bug report` when one lands.

- I added a skill for when someone forwards you a bug report. Say `process bug report` and I'll triage it — verify the diagnosis, check for related broken spots, log it, and draft a voice-calibrated reply saved to your Gmail Drafts (never auto-sent).
