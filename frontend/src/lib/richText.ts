/**
 * A small, safe rich-text reader for messages: what people actually type in a
 * chat, and nothing that could run.
 *
 *   **bold**   *italic* or _italic_   __underline__   ~~strike~~
 *   `code`     ```a block of code```
 *   [text](https://…)   <https://…|text> (Slack's)   and any bare https:// or www. link
 *
 * It returns a tree of plain values, never HTML: the renderer turns each node
 * into a React element, so a message can never inject markup, and a link is
 * only ever http(s) or mailto. A marker that does not close is left as typed,
 * and one inside a word (snake_case, 2*3*4) is not a marker.
 */

export type RichNode =
  | { t: "text"; v: string }
  | { t: "code"; v: string }
  | { t: "pre"; v: string }
  | { t: "link"; href: string; c: RichNode[] }
  | { t: "b" | "i" | "u" | "s"; c: RichNode[] };

// Earliest match wins; at the same place, the first alternative listed.
const INLINE = new RegExp(
  [
    "`([^`\\n]+)`", // 1 code
    "\\[([^\\]\\n]+)\\]\\(((?:https?:\\/\\/|mailto:)[^\\s)]+)\\)", // 2 text, 3 href
    "<((?:https?:\\/\\/|mailto:)[^\\s|>]+)(?:\\|([^>\\n]+))?>", // 4 href, 5 text
    "((?:https?:\\/\\/|www\\.)[^\\s<>]*[^\\s<>.,;:!?)\\]'\"*_~])", // 6 bare link
    "\\*\\*(?=\\S)([\\s\\S]*?\\S)\\*\\*", // 7 bold
    "(?<!\\w)__(?=\\S)([\\s\\S]*?\\S)__(?!\\w)", // 8 underline
    "~~(?=\\S)([\\s\\S]*?\\S)~~", // 9 strike
    "(?<![\\w*])\\*(?=[^\\s*])([^*\\n]*?[^\\s*])?\\*(?![\\w*])", // 10 italic
    "(?<![\\w_])_(?=[^\\s_])([^_\\n]*?[^\\s_])?_(?![\\w_])", // 11 italic
  ].join("|"),
  "g",
);

const FENCE = /```(?:[\w+-]*\n)?([\s\S]*?)```/g;

/** Only these schemes become links; everything else stays text. */
export function safeHref(raw: string): string | null {
  const href = /^www\./i.test(raw) ? `https://${raw}` : raw;
  return /^(https?:\/\/|mailto:)/i.test(href) ? href : null;
}

function inline(text: string, depth = 0): RichNode[] {
  const out: RichNode[] = [];
  let last = 0;
  const push = (v: string) => {
    if (!v) return;
    const prev = out[out.length - 1];
    if (prev?.t === "text") prev.v += v;
    else out.push({ t: "text", v });
  };
  // Nesting deeper than a person would type is read as text, so a crafted
  // message cannot recurse the reader into the ground.
  if (depth > 6) {
    push(text);
    return out;
  }
  const re = new RegExp(INLINE.source, "g");
  for (let m = re.exec(text); m; m = re.exec(text)) {
    push(text.slice(last, m.index));
    last = m.index + m[0].length;
    const [, code, mdText, mdHref, slHref, slText, bare, b, u, s, i1, i2] = m;
    if (code !== undefined) out.push({ t: "code", v: code });
    else if (mdHref !== undefined) out.push({ t: "link", href: mdHref, c: inline(mdText ?? "", depth + 1) });
    else if (slHref !== undefined) out.push({ t: "link", href: slHref, c: inline(slText ?? slHref, depth + 1) });
    else if (bare !== undefined) {
      const href = safeHref(bare);
      if (href) out.push({ t: "link", href, c: [{ t: "text", v: bare }] });
      else push(bare);
    } else if (b !== undefined) out.push({ t: "b", c: inline(b, depth + 1) });
    else if (u !== undefined) out.push({ t: "u", c: inline(u, depth + 1) });
    else if (s !== undefined) out.push({ t: "s", c: inline(s, depth + 1) });
    else {
      // A one-letter *x* leaves its group empty: the letter is the lookahead.
      const body = m[0].slice(1, -1);
      out.push({ t: "i", c: inline(i1 ?? i2 ?? body, depth + 1) });
    }
  }
  push(text.slice(last));
  return out;
}

/** A message as rich nodes: code fences first, then everything inline. */
export function parseRich(text: string): RichNode[] {
  const out: RichNode[] = [];
  let last = 0;
  const src = text ?? "";
  for (const m of src.matchAll(FENCE)) {
    out.push(...inline(src.slice(last, m.index)));
    out.push({ t: "pre", v: (m[1] ?? "").replace(/\n$/, "") });
    last = (m.index ?? 0) + m[0].length;
  }
  out.push(...inline(src.slice(last)));
  return out;
}

/** The words with the markers taken out: a preview line, a search. */
export function plainText(text: string): string {
  const flat = (nodes: RichNode[]): string =>
    nodes
      .map((n) => (n.t === "text" || n.t === "code" || n.t === "pre" ? n.v : flat(n.c)))
      .join("");
  return flat(parseRich(text));
}

export type Mark = "b" | "i" | "u" | "s" | "code" | "link";

const WRAP: Record<Exclude<Mark, "link">, string> = { b: "**", i: "*", u: "__", s: "~~", code: "`" };

/**
 * What the composer's buttons do: wrap the selection in a mark, or with
 * nothing selected put an empty pair at the caret. Returns the new text and
 * the selection to restore, so whatever is selected afterwards is what the
 * person would type over next.
 */
export function applyMark(
  text: string,
  start: number,
  end: number,
  mark: Mark,
): { text: string; start: number; end: number } {
  const sel = text.slice(start, end);
  if (mark === "link") {
    if (safeHref(sel.trim()) && !/\s/.test(sel.trim())) {
      // A URL selected: it is the target, and the label is what to type.
      const label = "link";
      const next = `${text.slice(0, start)}[${label}](${sel.trim()})${text.slice(end)}`;
      return { text: next, start: start + 1, end: start + 1 + label.length };
    }
    const label = sel || "link";
    const url = "https://";
    const next = `${text.slice(0, start)}[${label}](${url})${text.slice(end)}`;
    const at = start + label.length + 3;
    return { text: next, start: at, end: at + url.length };
  }
  const w = WRAP[mark];
  // Already wrapped: take the mark off again, as a toggle.
  if (sel && text.slice(start - w.length, start) === w && text.slice(end, end + w.length) === w) {
    const next = text.slice(0, start - w.length) + sel + text.slice(end + w.length);
    return { text: next, start: start - w.length, end: end - w.length };
  }
  const next = `${text.slice(0, start)}${w}${sel}${w}${text.slice(end)}`;
  return { text: next, start: start + w.length, end: end + w.length };
}
