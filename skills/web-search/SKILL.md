---
name: web-search
description: Search the web / Google for something and bring back a clear answer. Use for "look up...", "google...", "find docs on...", "what's the latest on...". Can save useful findings into the memory graph as resource nodes linked to whatever you're working on.
---

# web-search

Answer a question from the live web, then optionally keep what's useful.

Read `${CLAUDE_PLUGIN_ROOT}/CONVENTIONS.md` once per session first.

## Steps

1. **Search** with the `WebSearch` tool. Refine the query if the first pass is
   noisy.
2. **Read** the most promising result(s) with `WebFetch` when you need detail
   beyond the snippets.
3. **Answer in the focus contract:** the direct answer first, in 1–3 sentences.
   Then, only if useful, the source link. No wall of text.
4. **Save if it matters.** If this is reference the person will want again,
   `add-node --type resource --title "<topic>" --body "<key takeaway>"
   --props '{"url":"..."}'` and link it `about` / `relates-to` the current
   task or project so it resurfaces in context later.

## Notes
- Distinguish fact from opinion; note when sources disagree.
- If the answer is time-sensitive, say when it was true.
