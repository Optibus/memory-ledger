---
name: web-search
description: Search the web / Google for something and bring back a clear answer. Use for "look up...", "google...", "find docs on...", "what's the latest on...". Can save useful findings into the memory graph as resource nodes linked to whatever you're working on.
---

# web-search

Answer a question from the live web, then keep what is worth keeping.

## When to use

"Look up", "google", "find docs on", "what is the latest on".

## Given

- **The question**, and the `WebSearch` tool to answer it. A noisy first pass gets
  a refined query, not a lowered bar.
- **`WebFetch`**, for when the snippets are not enough and the detail is on the page.
- **What the person is currently working on**, so a finding worth keeping can be
  linked to it and resurface in context later.

## Rules honored

Ask the genome for the rules that apply before searching (§5.2):

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" match-rules "search the web and save the finding"
```

Accountable for: `rule_s1_how-to-respond-the-focus-contract-non-ne`.

Rule classes this skill touches, resolved from the person's own genome:

- **cost** (§5.2), via `match-rules "read without spending a model call"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `remember`, when the finding belongs on an existing
  node rather than in a new one.
- **Reads:** the live web, via `WebSearch` and `WebFetch`, plus `task` and
  `project` for what the finding should link to.
- **Writes:** a `resource` node with `props {url}`, linked `about` or `relates-to`
  the current task or project.

## Deliverables

- **The direct answer comes first**, in one to three sentences, then the source
  link only if it is useful.
- **Fact is separated from opinion**, and disagreement between sources is said out
  loud rather than averaged into a confident sentence.
- **Time-sensitive answers carry their date.** "True as of" is part of the answer
  when the answer can expire.
- **Reference worth reusing is kept.** A `resource` node holds the topic, the key
  takeaway and the url, linked to the work it serves.

## Judged against

- **The first sentence answers the question.** If the person has to read to the
  end, the ordering clause failed.
- **A saved resource is linked to something.** An unlinked resource never
  resurfaces, which was the only reason to save it.
- **Any claim that could expire says when it was true.**

## Guardrails — must not

- **Must not present a single source's claim as settled** when sources disagree.
- **Must not answer from memory when the question is about current state.** That is
  what the live web is for.
- **Must not return a wall of text.** The answer is the answer, not the research.

## Reply (focus contract)

The answer in one to three sentences, then the link if it earns its place.
