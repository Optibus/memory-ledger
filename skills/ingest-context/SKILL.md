---
name: ingest-context
description: Ingest a piece of context into the memory graph — a Google Meet transcript, a Slack thread/message, an email, or pasted plain text — extracting people, decisions, action items, meetings, and facts, then deduping and linking them. Use when the person shares or points you at any of these, or AUTOMATICALLY when an email carrying a meeting transcription/notes arrives (e.g. Gemini "Notes by Gemini"). The team lives in Google Meet, Slack, and email — this is how any of it becomes durable memory.
---

# ingest-context

Turn a raw blob of context (a call, a thread, an email, or pasted text) into
connected memory the rest of work-brain can use.

Read `${CLAUDE_PLUGIN_ROOT}/CONVENTIONS.md` once per session first (graph API,
vocabulary, response contract).

## 1. Identify the source and fetch the content

- **Google Meet** — transcripts/notes usually arrive as a Google Doc ("Notes by
  Gemini", or "Anotações do Gemini") linked on the calendar event or emailed.
  Read it with the Google Drive tools (`read_file_content` on the doc id) or via
  the email body.
- **Email** — fetch with the Gmail tools (`search_threads` / `get_thread`).
  **Meeting-transcription emails** are the trigger for auto-ingest: sender like
  `gemini-notes@google.com`, subject starting `Notes:` / "Notes by Gemini", or a
  body that is clearly a meeting transcript/summary. Treat these as a meeting.
- **Jira notification emails** — sender `jira@optibus.atlassian.net`, subjects
  like `[JIRA] (OS-xxxx) ...`, `... mentioned you on OS-xxxx`, or update digests.
  Ingest the **transitions and changes** they carry: status/workflow transitions
  (e.g. In Progress → Code Review → Deployed), field changes (assignee, story
  points, priority, sprint, labels, resolution, attachments), new comments, and
  @mentions. Apply them to the graph: **update the matching `jira-issue` node**
  (`update-node` its `status`/props), add a `note` for a substantive comment
  linked `about` the issue, create a `task` if a mention is a direct ask to
  Gabriel, and link the actor `person`. If you need the authoritative current
  state (email can lag), confirm via the Jira MCP `getJiraIssue`. Don't create
  duplicate issue nodes — match on the `OS-xxxx` key.
- **Slack** — read with `slack_read_thread` / `slack_read_channel` /
  `slack_search_public_and_private`.
- **Plain text** — the person pastes it directly; use it as-is. No fetch needed.

## 2. Classify what it is
Meeting/call · discussion · decision · request/ask · status update · reference.
This decides which node types you create.

## 3. Extract the entities
Pull out the durable things (ignore chit-chat and noise):
- **People** — attendees + anyone mentioned. Map to existing `person` nodes.
- **Decisions** — anything settled ("we'll do X", "agreed to Y").
- **Action items** — who owns what, by when.
- **Facts / context** — durable notes worth keeping.
- **References** — tickets (OS-xxxx), PRs, projects, channels.

## 4. Dedupe first
For each entity, `search` the graph before creating. If it exists (a person, a
project, a ticket), **update/link it** instead of making a duplicate.

## 5. Write nodes + edges
- **Meeting** → `add-node --type meeting --title "<name> — <date>"` with
  `--props '{"source":"gmeet|email|slack","url":"...","date":"ISO"}'`.
- **Attendees** → `add-edge` meeting `relates-to` each person (rel prop
  "attendee"). Create missing people as `person` nodes.
- **Decisions** → `add-node --type decision ...`, linked `relates-to` the
  meeting and `about` whom/what it concerns. If it's Gabriel's own way of
  working, also link it `part-of hub_process` (see the Process category).
- **Action items** → `add-node --type task ... --status open` (+ `--due` if a
  date was given), linked `assigned-to` the owner and `relates-to` the meeting.
  Items owned by Gabriel land on his plate; consider `add-task` for micro-steps.
- **Facts** → `add-node --type note ...` linked `about`/`relates-to` the topic.
- **References** → link `mentions`/`relates-to` the ticket/PR/project/channel
  nodes.

## 6. Meeting-transcript specifics (the common case)
Create the meeting node, attach attendees, then extract **decisions** and
**action items with owners + due dates**. Keep a short summary in the meeting
body. Surface which action items are Gabriel's.

## 7. Reply (focus contract)
One or two calm lines: what you ingested (e.g. "Saved the CORD Planning call —
4 attendees, 2 decisions, 3 action items") and **the single thing that needs
Gabriel** (usually his own action item). Don't dump the whole extraction.

## Email hygiene — ASK before marking read
Reading an email to ingest it must NOT change its state. **Never mark an email
as read, archive it, or apply/remove labels without asking Gabriel first** —
confirm before any such change. Fetching/reading content is fine; mutating the
mailbox is gated. This applies to Jira, meeting, and any other emails.

## Notes
- Always store **provenance** in props (source, url, date) so context is
  traceable.
- Everything stays in the local brain (`~/.work_brain`); it's private.
- This skill is what `read-email`, `slack-inbox`, and `/brain` hand off to when
  they detect ingestible context. When a meeting-transcript email arrives,
  ingest it automatically, then report.
