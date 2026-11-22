# The chats' personal-source wall (cr1#1179)

A connector the owner calls **personal** (its `role`, cr1#1178) is never read, and a mailbox whose **surface** dial is off (a `personal` account's default, ACCOUNT_SCOPE §1) is never shown. Maintenance jobs honour that through `connector_reads`. A chat reads its connectors in the conversation, where no Python step sees the tools, so a chat asks once and obeys what it is told.

**Ask once, before the first connector read in this chat.** Render

`cd "$PLUGIN_ROOT" && python3 shared/scripts/workspace_access.py plan run_helper --json '{"name": "connector_reads:chat_wall", "args": {"workspace_root": "<WS>"}}'`

paste what it prints and run that. The envelope's `result` is `{readable, connectors, mailboxes}`.

**Then, for the rest of the chat:**

- **`connectors`:** call no tool on a server listed in any entry's `server_ids`, for any purpose: no search, no fetch, no list. If the skill's next step needs that source, skip that step as if the source were not connected, and say nothing about the source.
- **`mailboxes`:** on a mail server that also carries a business mailbox, read the business mailbox only. Show no message from a listed mailbox unless its sender is in that entry's `except_senders` (the senders the owner opted into seeing). Such a message is shown and never written.
- **Write nothing** from a listed connector or mailbox: no capture, no interaction, no commitment, no note. The append path refuses any row whose provenance names a personal connector's server (and, for a meetings, chat, documents, tasks, deals or contracts connector, a row whose provider is that connector's) in `account_scope_gate`; a row that names no source cannot be attributed, so this rule is what keeps the chat from writing one.
- **Say nothing about what was left out.** The owner chose it; the chat does not announce it. One exception: when the owner asks for a missing item through the chat the Hub's Ask opens as "Something is wrong" (report-bug's Step 0, cr1#1315; "Something wrong?" until cr1#1591), or through a "Missed something?" press saved before that button opened a chat (cr1#1202), and nothing is found, the answer says in general words that personal sources are never searched, naming none, so the owner knows where it did not look. Nothing else about them is said.
- **`readable: false`** (the book could not be read) or `ok: false`: carry on as before. The writer wall still holds.

The census guard `tests/run_guard_chat_wall_census_test.py` lists every skill file that reads a connector, so a new direct read cannot slip in without this step.
