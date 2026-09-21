---
name: recall
description: Retrieve and resurface things from the memory-ledger memory graph — answer "what do I know about X", "what's on my plate", "what's due", "what was decided about Y", "how is this connected". Use whenever the person asks about past context, or before acting so you don't lose what's already known.
---

# recall

Get context back out of the graph, and surface it without overwhelming.

## When to use

The person asks about past context, and also before you act on anything, so you
build on what is already known instead of starting cold.

## Given

- **The question**, which determines the lookup:
  - a topic, or "what do I know about X", is `search` then `neighbors --depth 1`
    on the best hit
  - open work is `list --type task --status open` and `--status doing`
  - "what is due" or "am I behind" is `due --within-days 7`
  - "how is X connected to Y" is `neighbors --id X --depth 2`
  - a person or project is `search` then `neighbors` on the node
- **The date views**, which cost no model call. Prefer `brain_query.py` when the
  question is a date question.

## Rules honored

Ask the genome for the rules that apply before answering (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "look something up in the memory graph and present it"
```

Accountable for: `rule_s1_how-to-respond-the-focus-contract-non-ne`,
`rule_s2h_memory-dynamics-how-recall-behaves-like-`.

Rule classes this skill touches, resolved from the person's own genome:

- **cost** (§5.2), via `match-rules "read without spending a model call"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Reads:** any node type, most often `task`, `subtask`, `person`, `project`,
  `decision`, `note`, through `search`, `get-node`, `neighbors`, `list`, `due`
  and the `brain_query.py` views.
- **Writes:** none. Recall is read-only.

## Deliverables

- **The lookup matched the question.** A topic question used search and
  neighbors, a date question used the token-free date view.
- **One thing leads.** The single most relevant result is bolded first, and
  anything overdue or at risk comes before anything that is not.
- **The rest is counted, not dumped.** If more exists, the reply says how many and
  offers them.
- **Ids became titles.** Nothing raw reaches the person.

## Judged against

- **A reader can act on the first line alone.** If they have to read the whole
  reply to find what matters, the ordering clause failed.
- **No JSON appears in the reply.**
- **Nothing in the graph changed**, since this skill only reads.

## Guardrails — must not

- **Must not dump every hit.** A list of 28 is the same as no answer.
- **Must not paste raw node output.** Ids mean nothing to the person.
- **Must not spend a model call on a date question** that `brain_query.py`
  answers for free.
- **Must not answer from your own memory of the conversation** when the graph holds
  the record. The graph is what stayed true.

## Reply (focus contract)

The single most relevant thing, bolded, then the count of what else exists.

> **PR #7357 is still waiting on your review**, 4 days old. Two other loops are
> open. Want them?
