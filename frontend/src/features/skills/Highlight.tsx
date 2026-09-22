export interface HighlightProps {
  text: string;
  query: string;
}

/** Shows where the filter matched, rather than leaving you to find it. */
export function Highlight({ text, query }: HighlightProps) {
  const q = query.trim().toLowerCase();
  if (!q) return <>{text}</>;
  const i = text.toLowerCase().indexOf(q);
  if (i < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, i)}
      <mark className="ml-hl">{text.slice(i, i + q.length)}</mark>
      {text.slice(i + q.length)}
    </>
  );
}
