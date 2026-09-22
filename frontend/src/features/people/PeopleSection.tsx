import { useMemo, useState } from "react";
import { api } from "@/api/client";
import { useAsync } from "@/hooks/useAsync";
import { usePref } from "@/hooks/usePrefs";
import { Badge, Card, EmptyState, SearchInput, Spinner, StatusMessage } from "@/components";
import { Section } from "@/features/shell/Section";
import { PersonCard } from "./PersonCard";
import { DuplicateGroup } from "./DuplicateGroup";
import type { Person, PersonDuplicates } from "@/api/types";
import "./PeopleSection.css";

/**
 * Everyone the brain knows, and where each of them shows up.
 *
 * People were the one node type with a full engine surface and no UI at all:
 * `person_duplicates`, `api_merge_person` and `api_add_alias` have been there
 * the whole time, reachable only from the CLI. That is the wrong place for
 * them, because people are the node type whose quality decays on its own —
 * every capture that says "Isa" instead of "Isadora" is a new node until
 * somebody says otherwise.
 *
 * NO MODEL CALL anywhere on this page. Duplicate detection is name matching
 * over every alias each node answers to, and merging moves edges and writes an
 * alias — plain SQL, exactly like the rest of the write surface (§2g).
 */
export function PeopleSection() {
  const people = useAsync<{ ok: boolean; people: Person[] }>((signal) => api.people(signal), []);
  const dupes = useAsync<PersonDuplicates>((signal) => api.personDuplicates(signal), []);
  const [q, setQ] = useState("");
  // Which card was open survives a reload, like every other view preference.
  const [expanded, setExpanded] = usePref<string | null>("people.expanded", null);

  const reload = () => {
    people.reload();
    dupes.reload();
  };

  const shown = useMemo(() => {
    const all = people.data?.people ?? [];
    const needle = q.trim().toLowerCase();
    if (!needle) return all;
    // Aliases are part of the name, not metadata: searching "Isa" has to find
    // the node titled "Isadora" that answers to it.
    return all.filter(
      (p) =>
        p.title.toLowerCase().includes(needle) ||
        p.aliases.some((a) => a.toLowerCase().includes(needle)),
    );
  }, [people.data, q]);

  const groups = dupes.data?.duplicates ?? [];

  return (
    <Section
      title="People"
      lead="Every person node, what it connects to, and the ones that look like the same human twice."
      actions={
        <SearchInput
          value={q}
          onChange={setQ}
          placeholder="name or alias"
          aria-label="filter people"
        />
      }
    >
      {people.loading ? <Spinner label="reading people" /> : null}
      {people.error ? <StatusMessage tone="error">{people.error}</StatusMessage> : null}

      {groups.length ? (
        <Card>
          <h2 className="ml-people__h">
            Possible duplicates <Badge tone="warn">{groups.length}</Badge>
          </h2>
          <p className="ml-people__hint">
            Matched by name across every alias each node answers to — no model call. Merging moves
            the edges onto the canonical node, pins the other spelling as an alias, and leaves the
            duplicate in place pointing at its replacement, so an old capture that said
            &ldquo;Isa&rdquo; still says Isa and still resolves to the right human.
          </p>
          {groups.map((g) => (
            <DuplicateGroup key={g.canonical.id} group={g} onMerged={reload} />
          ))}
        </Card>
      ) : dupes.data ? (
        <Card>
          <h2 className="ml-people__h">
            Possible duplicates <Badge tone="success">none</Badge>
          </h2>
          <p className="ml-people__hint">No two person nodes currently look like the same human.</p>
        </Card>
      ) : null}

      {people.data && !shown.length ? (
        <EmptyState
          title={q ? "No one matches that" : "No people yet"}
          hint={
            q
              ? "Try a shorter fragment — aliases are searched too."
              : "People appear as the brain captures them, or when you create one."
          }
        />
      ) : null}

      <div className="ml-people__grid" role="list" aria-label="people">
        {shown.map((p) => (
          <PersonCard
            key={p.id}
            person={p}
            open={expanded === p.id}
            onToggle={() => setExpanded(expanded === p.id ? null : p.id)}
            onChanged={reload}
          />
        ))}
      </div>
    </Section>
  );
}
