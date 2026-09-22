import { Field, TextArea, TextInput } from "@/components";
import { TokenPicker } from "./TokenPicker";
import type { Meta, SkillsPayload } from "@/api/types";
import { DECL_HINTS, SKILL_HINTS, labelOf } from "./hints";

export interface SkillDraft {
  title: string;
  name: string;
  /** Set once the person edits the slug, so the title stops overwriting it. */
  nameTouched: boolean;
  description: string;
  realm: string;
  sections: Record<string, string>;
  declarations: Record<string, string[]>;
  rules: string[];
}

export function emptySkillDraft(): SkillDraft {
  return {
    title: "",
    name: "",
    nameTouched: false,
    description: "",
    realm: "",
    sections: {},
    declarations: {},
    rules: [],
  };
}

export function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export interface SkillFormProps {
  meta: Meta;
  catalog: SkillsPayload | null;
  draft: SkillDraft;
  onChange: (next: SkillDraft) => void;
  onNewRule: () => void;
}

/** The skill contract (§5), again shaped by what `/api/meta` reports. */
export function SkillForm({ meta, catalog, draft, onChange, onNewRule }: SkillFormProps) {
  const typed = (meta.skill_sections ?? []).filter((s) => !s.generated);

  const skillOptions = (catalog?.trees ?? []).flatMap(function flat(t): { value: string; label: string }[] {
    const walk = (s: { id: string; title: string; children: typeof s[] }): { value: string; label: string }[] => [
      { value: s.id, label: s.title },
      ...s.children.flatMap(walk),
    ];
    return t.skills.flatMap(walk);
  });

  const typeOptions = (catalog?.node_types ?? []).map((t) => ({
    value: t.type,
    label: t.count ? `${t.type} (${t.count})` : t.type,
  }));

  return (
    <>
      <Field label="Title" required>
        {(id) => (
          <TextInput
            id={id}
            value={draft.title}
            placeholder="Weekly digest"
            onChange={(e) =>
              onChange({
                ...draft,
                title: e.target.value,
                name: draft.nameTouched ? draft.name : slugify(e.target.value),
              })
            }
          />
        )}
      </Field>

      <Field
        label="Name (slug)"
        hint="The id becomes skill_<name>. Left blank it is derived from the title."
      >
        {(id) => (
          <TextInput
            id={id}
            value={draft.name}
            placeholder="weekly-digest"
            onChange={(e) => onChange({ ...draft, name: e.target.value, nameTouched: true })}
          />
        )}
      </Field>

      <Field label="Description" hint="Shown on the card and in match-skills.">
        {(id) => (
          <TextArea
            id={id}
            rows={2}
            value={draft.description}
            placeholder="One sentence: what this skill is for."
            onChange={(e) => onChange({ ...draft, description: e.target.value })}
          />
        )}
      </Field>

      <Field
        label="Section (realm)"
        hint="Blank files it under shared: a process applies in every section of life, not just the one you were in when you wrote it."
      >
        {(id) => (
          <TextInput
            id={id}
            value={draft.realm}
            placeholder="shared"
            onChange={(e) => onChange({ ...draft, realm: e.target.value })}
          />
        )}
      </Field>

      <h3 className="ml-create__head">The contract (§5.1)</h3>
      {typed.map((s) => (
        <Field key={s.field} label={labelOf(s.heading)} required={s.required} hint={SKILL_HINTS[s.field]}>
          {(id) => (
            <TextArea
              id={id}
              rows={s.required ? 5 : 3}
              value={draft.sections[s.field] ?? ""}
              onChange={(e) =>
                onChange({ ...draft, sections: { ...draft.sections, [s.field]: e.target.value } })
              }
            />
          )}
        </Field>
      ))}

      <h3 className="ml-create__head">Depends on (§5a)</h3>
      <p className="ml-create__hint">
        Left blank, a field is declared <code>none</code> — silence is not a declaration.
      </p>
      {(meta.decl_labels ?? []).map((d) => {
        const isType = d.field === "reads" || d.field === "writes";
        return (
          <Field key={d.field} label={d.label} hint={DECL_HINTS[d.field]}>
            {() => (
              <TokenPicker
                options={isType ? typeOptions : skillOptions}
                value={draft.declarations[d.field] ?? []}
                placeholder={isType ? "choose node types" : "choose skills"}
                emptyLabel="none"
                onChange={(next) =>
                  onChange({ ...draft, declarations: { ...draft.declarations, [d.field]: next } })
                }
              />
            )}
          </Field>
        );
      })}

      <h3 className="ml-create__head">Rules it answers to</h3>
      <p className="ml-create__hint">
        Each rule lands as an <code>honors</code> edge in the graph as well as a line in the
        contract.
      </p>
      <TokenPicker
        options={(catalog?.rules ?? []).map((r) => ({ value: r.id, label: r.title }))}
        value={draft.rules}
        placeholder="link rules"
        emptyLabel="no rules linked yet"
        onChange={(rules) => onChange({ ...draft, rules })}
        extraAction={{ label: "＋ New rule…", onClick: onNewRule }}
      />
    </>
  );
}

/** Refused server-side unless the six typed sections of §5.1 are filled. */
export function skillDraftError(meta: Meta, draft: SkillDraft): string | null {
  if (!draft.title.trim()) return "a skill needs a title";
  for (const s of meta.skill_sections ?? []) {
    if (s.generated || !s.required) continue;
    if (!(draft.sections[s.field] ?? "").trim())
      return `${labelOf(s.heading)} is required — declare the outcome, not the steps`;
  }
  return null;
}
