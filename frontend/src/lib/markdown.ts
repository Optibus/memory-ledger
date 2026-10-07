/**
 * The block level of Markdown for the agent chat: headings, lists, quotes,
 * rules, tables and `doc:` links. Everything inside a block is the inline reader
 * of `lib/richText`, which stays the only thing that decides what a link or a
 * mark is. Like it, this returns plain values and never HTML.
 *
 *   # heading (1-6)     - item / * item     1. item     > quote     ---
 *   | a | b |  (header row, then |---|---| , then rows)
 *   [name](doc:documents/report.html)  a document card, on a line of its own
 *
 * A line that is none of these joins the paragraph above it. Code fences are
 * left inside the text, because `parseRich` already reads those.
 */

export type Block =
  | { t: "p"; v: string }
  | { t: "h"; level: number; v: string }
  | { t: "list"; ordered: boolean; items: string[] }
  | { t: "quote"; v: string }
  | { t: "hr" }
  | { t: "table"; head: string[]; rows: string[][] }
  | { t: "doc"; name: string; path: string };

const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const DOC = /^\s*\[([^\]\n]+)\]\(doc:([^)\s]+)\)\s*$/;
const FENCE_LINE = /^\s*```/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

const cells = (line: string): string[] =>
  line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

/** Only a relative path under exports; the server checks it again. */
export function safeDocPath(path: string): boolean {
  return !path.startsWith("/") && !path.split("/").includes("..") && !/[\0\\]/.test(path);
}

export function parseBlocks(text: string): Block[] {
  const lines = (text ?? "").split("\n");
  const out: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) out.push({ t: "p", v: para.join("\n") });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (FENCE_LINE.test(line)) {
      // A fence is one paragraph to the end of it, so nothing inside is read as a block.
      let j = i + 1;
      while (j < lines.length && !FENCE_LINE.test(lines[j])) j++;
      para.push(...lines.slice(i, Math.min(j, lines.length - 1) + 1));
      i = j;
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    const doc = DOC.exec(line);
    if (doc && safeDocPath(doc[2])) {
      flush();
      out.push({ t: "doc", name: doc[1], path: doc[2] });
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      flush();
      out.push({ t: "h", level: h[1].length, v: h[2] });
      continue;
    }
    if (RULE.test(line)) {
      flush();
      out.push({ t: "hr" });
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      flush();
      const head = cells(line);
      const rows: string[][] = [];
      let j = i + 2;
      while (j < lines.length && lines[j].includes("|") && lines[j].trim()) rows.push(cells(lines[j++]));
      out.push({ t: "table", head, rows });
      i = j - 1;
      continue;
    }
    const ul = BULLET.exec(line);
    const ol = NUMBERED.exec(line);
    if (ul || ol) {
      flush();
      const ordered = !ul;
      const items: string[] = [];
      let j = i;
      for (; j < lines.length; j++) {
        const m = (ordered ? NUMBERED : BULLET).exec(lines[j]);
        if (m) items.push(m[1]);
        else if (/^\s{2,}\S/.test(lines[j]) && items.length) items[items.length - 1] += `\n${lines[j].trim()}`;
        else break;
      }
      out.push({ t: "list", ordered, items });
      i = j - 1;
      continue;
    }
    if (/^\s*>/.test(line)) {
      flush();
      const quoted: string[] = [];
      let j = i;
      for (; j < lines.length && /^\s*>/.test(lines[j]); j++) quoted.push(lines[j].replace(/^\s*>\s?/, ""));
      out.push({ t: "quote", v: quoted.join("\n") });
      i = j - 1;
      continue;
    }
    para.push(line);
  }
  flush();
  return out;
}
