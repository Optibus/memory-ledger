---
name: document-html
description: Write a single self-contained HTML page (inline CSS, no external requests) and hand it to the owner as an expandable card in the Agents tab. Use for "make an HTML report", "build a page for this", "write it as HTML", whenever the owner wants a document rather than a chat reply. Headless runs cannot write artifacts; this is the way to deliver one. The owner can customize how it looks and reads, and the choice is remembered.
---

# document-html

Produce a single self-contained HTML page (inline CSS, no external requests), save it where the Agents tab can show it, and reply with the
line that becomes a card: the document's name and an Expand button, which opens
the file over the interface.

## When to use

"make an html report", "build a page for this", "write it as html"; any time the answer is a document the owner will read, keep or
share, not a short reply. Not for a few lines: answer in chat.

## Given

- **What the document is for**, and its content or the source to build it from.
- **The owner's style for this format**, if they set one: the `note` node titled
  `Document style - html`. Look it up first:

  ```
  python3 /Users/gabriel.nori/dev/second-brain/scripts/graph.py search "Document style - html"
  ```

- **Customization asked for in this request** (e.g. theme (dark by default), accent colour, font, density, sections to include, language, length). It wins over the
  stored style for this document only.

## Rules honored

```
python3 /Users/gabriel.nori/dev/second-brain/scripts/graph.py match-rules "write a html document for the owner"
```

Rule classes this skill touches, resolved from the person's own genome:

- **artifact-style** (dark mode by default), via `match-rules "build an artifact"`.
- **report-structure** (recommendation first, then the evidence), via `match-rules "write a report"`.
- **outward-text** (no emoji, the person's voice), via `match-rules "draft a document"`.

## Depends on

See [`docs/skill-dependencies.md`](../../docs/skill-dependencies.md) (CONVENTIONS §5a).

- **Requires:** none.
- **Optional (enriching):** `recall` for the source material, `writing-style` when the
  document speaks for the owner.
- **Reads:** the `Document style - html` note.
- **Writes:** one file under `<home>/exports/documents/` via
  `scripts/make_document.py`, and optionally that `note` when the owner asks to keep a
  customization.

## Deliverables

- **The content.** Write one complete HTML document: `<!doctype html>`, inline `<style>`, semantic headings, tables for tabular data, inline SVG for charts. Dark mode first: bare `:root` dark palette, `color-scheme: dark`, explicit dark body background. Script is allowed but runs in a sandbox with no access to the brain's API, so keep the page readable without it.
- **The file**, saved by piping the full HTML into the script, which prints the card line:

  ```
  <content> | python3 /Users/gabriel.nori/dev/second-brain/scripts/make_document.py --format html --title "<title>"
  ```

- **Customization.** If the owner says how they want these to look ("always
  ...", "from now on ..."), store it as a `note` titled `Document style - html`
  (`graph.py add-node --type note ...`), updating the existing one rather than adding
  another. One-off asks are applied and not stored. If the owner has no style yet and
  the request leaves it open, use the defaults and offer once to change them.
- **The reply** carries the printed line unchanged, on a line of its own.

## Judged against

- **The file exists** under `exports/documents/` and the printed line points to it.
- **It opens in the card**: the Agents tab shows the name collapsed, and the file in
  the overlay once expanded.
- **The stored style and the request were both honored**, the request first.

## Guardrails — must not

- **Must not write outside `<home>/exports/documents/`**, and must not hand a
  filesystem path to the owner instead of the card line.
- **Must not edit the printed `doc:` link** or wrap it in a sentence: it has to be
  alone on its line to become a card.
- **Must not store a one-off request as the owner's style.**
- **Must not put secrets or tokens in a document**; it can be shared.

## Reply (focus contract)

One short line saying what the document is, then the card line on its own.

> The Q3 report, with the open risks first.
>
> [Q3 report](doc:documents/20261007-1030-q3-report.html)
