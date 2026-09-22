import { useState } from "react";
import { api } from "@/api/client";
import { Badge, Button, ConfirmDialog, StatusMessage } from "@/components";
import type { DuplicateCandidate, PersonDuplicate } from "@/api/types";
import "./PeopleSection.css";

/**
 * One group of person nodes that look like the same human, and the merge.
 *
 * The canonical is the engine's pick — the most connected node, because merging
 * into it moves the fewest edges — and it is a default, not a verdict: the
 * button on any duplicate re-points the merge, since only a human can say which
 * spelling is the real person.
 *
 * Merging is not deleting (§2a). Edges move, the other spelling becomes an
 * alias, and the duplicate node stays as a resolver pointing at its
 * replacement, so every capture already written keeps resolving.
 */
export function DuplicateGroup({
  group,
  onMerged,
}: {
  group: PersonDuplicate;
  onMerged: () => void;
}) {
  const [canonical, setCanonical] = useState(group.canonical.id);
  const [pending, setPending] = useState<DuplicateCandidate | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const everyone = [group.canonical, ...group.duplicates];
  const keep = everyone.find((p) => p.id === canonical) ?? group.canonical;

  const merge = async (dup: DuplicateCandidate) => {
    setBusy(true);
    setErr(null);
    try {
      const res = (await api.mergePerson(canonical, dup.id)) as {
        ok: boolean;
        error?: string;
      };
      if (!res.ok) throw new Error(res.error || "merge failed");
      onMerged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  return (
    <div className="ml-dupe">
      <ul className="ml-dupe__list">
        {everyone.map((p) => {
          const isKeep = p.id === canonical;
          return (
            <li key={p.id} className={`ml-dupe__row${isKeep ? " is-keep" : ""}`}>
              <label className="ml-dupe__pick">
                <input
                  type="radio"
                  name={`canonical-${group.canonical.id}`}
                  checked={isKeep}
                  onChange={() => setCanonical(p.id)}
                />
                <span className="ml-dupe__name">{p.title}</span>
              </label>
              <span className="ml-dupe__edges">
                {p.edges} connection{p.edges === 1 ? "" : "s"}
              </span>
              {p.confidence ? <Badge tone="neutral">{p.confidence}</Badge> : null}
              {isKeep ? (
                <Badge tone="success">keep</Badge>
              ) : (
                <Button size="sm" disabled={busy} onClick={() => setPending(p)}>
                  Merge into {keep.title}
                </Button>
              )}
            </li>
          );
        })}
      </ul>

      {err ? <StatusMessage tone="error">{err}</StatusMessage> : null}

      {pending ? (
        <ConfirmDialog
          title="Merge these two?"
          busy={busy}
          confirmLabel={`Merge ${pending.title} into ${keep.title}`}
          body={
            <>
              <p>
                <strong>{pending.title}</strong>&rsquo;s {pending.edges} connection
                {pending.edges === 1 ? "" : "s"} move onto <strong>{keep.title}</strong>, and
                &ldquo;{pending.title}&rdquo; is pinned there as an alias.
              </p>
              <p>
                Nothing is deleted. <strong>{pending.title}</strong> stays in the graph pointing at{" "}
                <strong>{keep.title}</strong>, so anything already written that used that spelling
                still resolves to the right person.
              </p>
            </>
          }
          onConfirm={() => merge(pending)}
          onCancel={() => setPending(null)}
        />
      ) : null}
    </div>
  );
}
