---
name: complete-task
description: Mark a task (or subtask/reminder) as done and move it to the done archive — kept forever, never deleted. Use when the person says "done", "finished X", "that's handled", "close this out", or when another skill finishes work it tracked. Completed work stays in memory so the brain can learn from it.
---

# complete-task

Finishing work is a first-class action here. We **archive, never delete**, because
a finished task is the evidence the brain learns from.

## When to use

The person says "done", "finished that", "handled", "close this out". Also when
another skill completes work it was tracking.

## Given

- **What they said was finished**, which is usually a fragment of the title rather
  than an id. Resolving it to one node is part of the job.
- **The task's own graph position**: its subtasks, and anything it was blocking.

## Rules honored

Ask the genome for the rules that apply before closing anything (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "mark a task done and archive it"
```

Accountable for: `rule_s2a_finishing-work-archive-never-delete`,
`rule_s2b_learned-skills-neuroplasticity` (a finish that taught something becomes
a skill).

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `learn-skill`, when finishing this revealed a
  repeatable procedure worth keeping.
- **Reads:** `task`, `subtask`, via `search`, `list` and `get-node` for the
  blocking edges.
- **Writes:** `task`, `subtask`, moved to status `done` with `completed_at`
  stamped by `complete`. Nothing is deleted.

## Deliverables

- **The right task is closed.** Exactly one node moved to done, and when several
  matched the title, the person chose which.
- **History survives.** The node stays searchable, keeps every edge, and appears
  in `done`. Nothing was deleted.
- **How it was done is recorded.** The completion carries a note, because the
  note is what makes the archive worth keeping.
- **The cascade is resolved.** Open subtasks of a completed parent are closed or
  explicitly offered, and anything that was `blocked-by` this is flagged as now
  unblocked.

## Judged against

- **`done` contains the task, and `list --type task --status open` no longer does.**
- **No `del-node` ran.** Deleting a finished task destroys the history the brain
  learns from, and it cannot be recovered.
- **Nothing silently stayed blocked.** A task waiting on this one and still marked
  blocked means the cascade clause was skipped.

## Guardrails — must not

- **Must not delete a finished task.** History is the whole point of archiving.
- **Must not close a task on an ambiguous match.** Closing the wrong one hides
  live work and the person will not notice until it is late.
- **Must not close the parent's subtasks silently.** They may still be real work.

## Reply (focus contract)

One calm line of acknowledgement, then the natural next action.

> Done, archived "Reply to Ravid". You have 9 active tasks left. Next up:
> **fix the failing CI on PR #7357.** Want it?

To un-complete: `update-node --id <id> --status open`.
