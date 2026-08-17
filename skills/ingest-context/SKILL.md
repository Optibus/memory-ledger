---
name: ingest-context
description: Ingest a piece of context into the memory graph — a Google Meet transcript, a Slack thread/message, an email, or pasted plain text — extracting people, decisions, action items, meetings, and facts, then deduping and linking them. Use when the person shares or points you at any of these, or AUTOMATICALLY when an email carrying a meeting transcription/notes arrives (e.g. Gemini "Notes by Gemini"). The team lives in Google Meet, Slack, and email — this is how any of it becomes durable memory.
---

# ingest-context

Turn a raw blob of context, a call, a thread, an email or pasted text, into
connected memory the rest of memory-ledger can use.

## When to use

The person shares or points at any of it. Also automatically when an email
carrying a meeting transcription or notes arrives, such as Gemini "Notes by
Gemini" or "Anotações do Gemini".

## Given

The source, and where its content actually lives:

- **Google Meet.** Transcripts and notes arrive as a Google Doc linked on the
  calendar event or emailed. Read it with the Drive tools (`read_file_content` on
  the doc id) or from the email body.
- **Email.** Gmail tools (`search_threads`, `get_thread`). A sender like
  `gemini-notes@google.com`, a subject starting `Notes:`, or a body that is
  plainly a transcript, means treat it as a meeting.
- **Jira notification email.** Sender `jira@optibus.atlassian.net`, subjects like
  `[JIRA] (OS-xxxx) ...` or a mention. What matters in it is the transitions and
  field changes it carries: status moves, assignee, story points, priority,
  sprint, labels, resolution, attachments, new comments, mentions. Email lags, so
  confirm authoritative state with `getJiraIssue` when it matters.
- **Slack.** `slack_read_thread`, `slack_read_channel`,
  `slack_search_public_and_private`.
- **Plain text.** Pasted directly, used as is.

## Rules honored

Ask the genome for the rules that apply before ingesting (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "ingest a meeting transcript, extract decisions and action items"
```

Accountable for: `rule_s2e_areas-one-hub-per-domain`.

Rule classes this skill touches, resolved from the person's own genome:

- **durable-storage** (§5.2), via `match-rules "store a durable fact"`.
- **mailbox-state** (§5.2), via `match-rules "change the state of an email"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `add-task` to break Gabriel's own action items into
  micro-steps.
- **Reads:** Gmail, Drive, Slack, Jira, and every node type through `search`
  for deduping.
- **Writes:** `meeting`, `person`, `decision`, `task`, `note`, `jira-issue`, plus
  the edges `relates-to`, `about`, `assigned-to`, `mentions`, `part-of`.

## Deliverables

- **The source is classified.** Meeting, discussion, decision, request, status
  update or reference. That choice determines which node types exist afterward.
- **Nothing durable was missed.** People, decisions, action items with owners and
  dates, facts worth keeping, and references to tickets, PRs, projects and
  channels are all extracted. Chit-chat is not.
- **Nothing was duplicated.** Every entity was searched before it was created, and
  an existing person, project or ticket was updated and linked instead of forked.
  Jira issues match on their `OS-xxxx` key.
- **The meeting is reconstructable.** A `meeting` node carries a short summary and
  `props` with source, url and date, with attendees linked, so the context is
  traceable back to where it came from.
- **Decisions landed in their domain.** A decision reflecting Gabriel's own way of
  working is also filed under the process area, not left loose.
- **Action items are owned.** Each is a `task` with an owner edge, a due date when
  one was given, and a link back to the meeting.
- **Jira changes were applied, not just read.** The matching `jira-issue` node was
  updated, a substantive comment became a `note` linked `about` it, a direct
  mention that asks Gabriel for something became a `task`, and the actor was
  linked as a person.

## Judged against

- **Provenance exists on everything.** Source, url and date in props, so any
  ingested fact traces back to the thing it came from. A fact without provenance
  cannot be trusted six weeks later.
- **A second ingest of the same source creates nothing new.** That is the dedupe
  clause, checked.
- **Gabriel's own action items are visible as his.** If his items are buried among
  everyone else's, the reply clause failed.
- **The mailbox is in the same state as before**, unless he was asked and said yes.

## Guardrails — must not

- **Must not mark read, archive, move or relabel any email without asking.**
  Gabriel uses unread state as his own queue, so silently clearing it destroys the
  signal he works from. Reading is fine, mutating is gated.
- **Must not create a duplicate person, project or ticket node.** Two nodes for
  one thing split its history and neither half stays true.
- **Must not store a fact without its source.** Unsourced memory is rumour.
- **Must not translate what was said when saving it.** Store it in the language it
  arrived in.
- **Must not dump the whole extraction into the reply.**

## Reply (focus contract)

One or two calm lines: what was ingested, then the single thing that needs Gabriel.

> Saved the CORD Planning call: 4 attendees, 2 decisions, 3 action items. **One is
> yours: send Ravid the export sample by Thursday.**

This skill is what `read-email`, `slack-inbox` and `/ledger` hand to when they
detect ingestible context. A meeting-transcript email is ingested automatically,
then reported. Everything stays in the local brain at `~/.second_brain`, which is
private.
