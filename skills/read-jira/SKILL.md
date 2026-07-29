---
name: read-jira
description: Read Jira — fetch an issue by key, search issues with JQL ("my open bugs", "what's in this sprint"), read comments and status. Use for "what's the status of PROJ-123", "show my Jira tickets", "what did they say on that ticket". Can cache issues into the memory graph so they link to your tasks and projects.
---

# read-jira

Pull work items from Jira using the Atlassian MCP tools, and connect them to
memory.

Load only the conventions that apply to what you are about to do — not the
whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "<task in a few words>"`
(no model call; ~8% of the full text). If it returns no rules the base layer is
not loaded yet — run `ingest-conventions` once, then re-query. See CONVENTIONS
§2i for the genome.

## Steps

1. **Find the cloud/site** once with
   `mcp__atlassian__getAccessibleAtlassianResources` (needed for the cloudId).
2. **Fetch:**
   - Specific issue → `mcp__atlassian__getJiraIssue` (key like `PROJ-123`).
   - Search → `mcp__atlassian__searchJiraIssuesUsingJql` with a JQL string
     (e.g. `assignee = currentUser() AND statusCategory != Done ORDER BY due`).
   - Comments → `mcp__atlassian__getJiraIssue` includes them, or read the
     issue's fields.
3. **Cache into the graph (optional but encouraged).** For issues that matter,
   `add-node --type jira-issue --title "PROJ-123 <summary>"
   --props '{"jira_key":"PROJ-123","status":"...","url":"..."}'` and link it
   `relates-to` / `blocks` the relevant task or project. Update status on
   re-read instead of duplicating.

## Reply (focus contract)

Summarize, don't paste the raw issue. Lead with the **status + the one thing
that needs the person's attention** (a blocker, a mention, an approaching due
date). Offer to turn it into a task (`add-task`) if there's action for them.

## Notes
- This skill is read-oriented. Creating/editing Jira issues is a separate,
  gated action — confirm explicitly before any write.
