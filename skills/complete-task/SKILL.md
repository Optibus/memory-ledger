---
name: complete-task
description: Mark a task (or subtask/reminder) as done and move it to the done archive — kept forever, never deleted. Use when the person says "done", "finished X", "that's handled", "close this out", or when another skill finishes work it tracked. Completed work stays in memory so the brain can learn from it.
---

# complete-task

Finishing work is a first-class action here: we **archive, never delete**.

Load only the conventions that apply to what you are about to do — not the
whole file:
`python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "<task in a few words>"`
(no model call; ~8% of the full text). If it returns no rules the base layer is
not loaded yet — run `ingest-conventions` once, then re-query. See CONVENTIONS
§2i for the genome.

## Steps

1. **Find the task.** `search "<words>"` or `list --type task` to get its id.
   If several match, confirm which one.
2. **Complete it:**
   ```
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" complete --id <id> --note "<how it was done>"
   ```
   This sets status `done`, stamps `completed_at`, and moves it out of active
   views — but it stays searchable, still linked to its people/tickets, and
   shows up in `done`.
3. **Cascade sensibly.** If completing a parent task, offer to complete its open
   subtasks too. If it was `blocked-by` something now resolved, note that.
4. **Learn from it (optional but encouraged).** If finishing this revealed a
   repeatable procedure, hand off to the `learn-skill` skill so the brain keeps
   it.

## Reply (focus contract)

One calm line of acknowledgement + the natural next action. e.g.
`Done — archived "Reply to Ravid". You have 9 active tasks left; next up:
**fix the failing CI on PR #7357.** Want it?`

## Notes
- Never `del-node` a finished task. Deletion loses history the brain uses.
- To un-complete, `update-node --id <id> --status open`.
