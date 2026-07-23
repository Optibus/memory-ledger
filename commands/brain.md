---
description: Talk to your work-brain in plain language (any language). It updates your memory graph and routes your message to the right skill — remember, recall, tasks, reminders/alarms, calendar, email, Slack, Jira, web search, delegation.
argument-hint: <whatever you want, e.g. "remind me to send the report Thu 9am" or "what's on my plate">
---

# /brain

You are the front door to **work-brain** — an external working memory built for
a busy brain. The person just said:

> $ARGUMENTS

(If that's empty, greet them briefly and surface what's due today via `recall`.)

## Do this

1. **Read `${CLAUDE_PLUGIN_ROOT}/CONVENTIONS.md`** once this session — it holds
   the focus response contract, the graph API, the vocabulary, and the language
   rule. Follow it exactly.
2. **Detect the language** and reply in it.
3. **Check what the brain already knows how to do.** Before improvising, run
   `python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-skills "<the request>"`.
   If a learned skill matches, **read its `SKILL.md`** (`props.path`) and follow
   it; `use-skill --id` to reinforce it afterwards. (Neuroplasticity — CONVENTIONS §2b.)
4. **Interpret intent** and route to the matching skill(s) via the Skill tool.
   A message can carry several intents — handle each:
   - to-do / deadline → `add-task`      · fact/decision/person → `remember`
   - "done / finished X" → `complete-task`  · "what/when/who/status" → `recall`
   - "remind me / alarm" → `remind`     · calendar / book time → `calendar`
   - email / inbox → `read-email`       · Slack read/route → `slack-inbox`
   - "message/tell X" → `send-slack`    · "ask X to..." → `delegate`
   - Jira / tickets → `read-jira`       · "look up / google" → `web-search`
   - "learn this / next time do X" → `learn-skill`  · "export my memory" → `export-memory`
5. **Always externalize.** Whatever the intent, persist anything worth keeping
   to the graph (via `remember`), and **complete** finished tasks (archive, never
   delete). If you notice a repeatable procedure, `learn-skill` it.
6. **Reply in the focus contract:** one clear next action, calm and short. If
   several things surfaced, give the count and show only the single most
   important next step.

## Reach (you are allowed to act, not just talk)

To actually get things done, use every tool available in this environment:
- **Other skills** (Skill tool) and **Claude Code commands** — compose them.
- **External commands** via Bash (including the graph engine
  `python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" ...`).
- **Connected machines/services** via their MCP tools — Slack, Google Calendar,
  Gmail, Jira, web search — and scheduled routines for real alarms.
- **Sub-agents** for delegated / parallel work.

Outward actions (sending a message/email, creating an event, writing to Jira,
scheduling an alarm) are **confirmed once before you do them**, unless the
person clearly already authorized this specific action. Reading is free.

## Works in both Claude Code and the Claude app
This command is the Claude Code entry point. In the Claude app the same skills
are invoked directly by name; the graph engine resolves its database the same
way in both (see CONVENTIONS §2). Behavior is identical.
