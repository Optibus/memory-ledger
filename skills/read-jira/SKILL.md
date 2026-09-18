---
name: read-jira
description: Read Jira — fetch an issue by key, search issues with JQL ("my open bugs", "what's in this sprint"), read comments and status. Use for "what's the status of PROJ-123", "show my Jira tickets", "what did they say on that ticket". Can cache issues into the memory graph so they link to your tasks and projects.
---

# read-jira

Pull work items from Jira, and connect them to memory so a ticket stops being a
tab and becomes part of the graph.

## When to use

"What is the status of OS-1234", "show my tickets", "what did they say on that
ticket", "what is in this sprint".

## Given

- **The Atlassian MCP tools.** `getAccessibleAtlassianResources` once for the
  cloudId, then `getJiraIssue` for a key or `searchJiraIssuesUsingJql` for a
  query, for example
  `assignee = currentUser() AND statusCategory != Done ORDER BY due`.
- **The existing `jira-issue` nodes**, matched on their key, so a re-read updates
  rather than duplicates.

## Rules honored

Ask the genome for the rules that apply before reading (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "read a jira issue and cache it in the graph"
```

Accountable for: `rule_s1_how-to-respond-the-focus-contract-non-ne`.

Rule classes this skill touches, resolved from the person's own genome:

- **outward-text** (§5.2), via `match-rules "draft a message to <recipient>"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `add-task` when the ticket carries action for the
  person, `ingest-context` when a comment thread carries durable context.
- **Reads:** the Atlassian MCP tools, and `jira-issue` nodes already cached.
- **Writes:** `jira-issue` nodes with `{jira_key, status, url}`, linked
  `relates-to` or `blocks` the task or project they concern.

## Deliverables

- **The answer is a summary, not the raw issue.** Status plus the one thing that
  needs the person: a blocker, a mention, an approaching date.
- **A ticket that matters is in the graph**, keyed on `jira_key`, linked to the
  work it concerns, and updated in place on re-read.
- **Action was offered as a task.** If there is something for the person to do,
  `add-task` is offered rather than left implied.

## Judged against

- **One node per ticket key.** A second node for `OS-1234` means the match-on-key
  clause was skipped.
- **The cached status matches Jira** at the time of reading.
- **The reply names what needs him**, not the full field list.

## Guardrails — must not

- **Must not write to Jira without an explicit, separate confirmation.** This is a
  read skill, and a Jira write is visible to the whole team.
- **Must not paste the raw issue.** The fields are noise, the status and the ask
  are the answer.
- **Must not create a second node for a ticket already cached.**

## Reply (focus contract)

Status plus the one thing that needs him.

> **OS-82316 is In Review, waiting on Alessandro's approval** since Monday.
> Nothing needed from you until he answers. Want a reminder for Thursday?
