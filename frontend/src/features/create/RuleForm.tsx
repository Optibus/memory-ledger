import { Checkbox, Field, Select, TextArea, TextInput } from "@/components";
import type { Meta } from "@/api/types";
import { RULE_HINTS, labelOf } from "./hints";

export interface RuleDraft {
  title: string;
  scenario: string;
  source: string;
  section: string;
  realm: string;
  non_negotiable: boolean;
  sections: Record<string, string>;
}

export function emptyRuleDraft(meta: Meta | null): RuleDraft {
  return {
    title: "",
    scenario: "",
    source: meta?.rule_sources?.includes("user") ? "user" : meta?.rule_sources?.[0] ?? "user",
    section: "",
    realm: "",
    non_negotiable: false,
    sections: {},
  };
}

export interface RuleFormProps {
  meta: Meta;
  draft: RuleDraft;
  onChange: (next: RuleDraft) => void;
}

/**
 * The rule contract (§5b.1), with its shape read from the server.
 *
 * A heading typed into this file would be a second definition of §5b.1 — and
 * the one nothing checks.
 */
export function RuleForm({ meta, draft, onChange }: RuleFormProps) {
  const set = <K extends keyof RuleDraft>(key: K, value: RuleDraft[K]) =>
    onChange({ ...draft, [key]: value });

  return (
    <>
      <Field label="Title" required hint="The clause in one line, as you would say it out loud.">
        {(id) => (
          <TextInput
            id={id}
            value={draft.title}
            placeholder="Ship the link, not the scrollback"
            onChange={(e) => set("title", e.target.value)}
          />
        )}
      </Field>

      <Field
        label="Governs (the cue it fires on)"
        required
        hint="The words you would actually type when this should apply. match-rules indexes THIS, not the body, so a rule whose cue misses never fires however well the body reads (§5b.2)."
      >
        {(id) => (
          <TextArea
            id={id}
            rows={2}
            value={draft.scenario}
            placeholder="finish a write-up, share a report, post a summary"
            onChange={(e) => set("scenario", e.target.value)}
          />
        )}
      </Field>

      <div className="ml-create__grid">
        <Field label="Source">
          {(id) => (
            <Select
              id={id}
              value={draft.source}
              onChange={(e) => set("source", e.target.value)}
              options={(meta.rule_sources ?? ["user"]).map((v) => ({ value: v, label: v }))}
            />
          )}
        </Field>
        <Field label="Section label">
          {(id) => (
            <TextInput id={id} value={draft.section} placeholder="user" onChange={(e) => set("section", e.target.value)} />
          )}
        </Field>
        <Field label="Section (realm)">
          {(id) => (
            <TextInput id={id} value={draft.realm} placeholder="shared" onChange={(e) => set("realm", e.target.value)} />
          )}
        </Field>
      </div>

      <Checkbox
        checked={draft.non_negotiable}
        onChange={(v) => set("non_negotiable", v)}
        label="Non-negotiable — returned by match-rules on every cue"
      />

      <h3 className="ml-create__head">The contract (§5b.1)</h3>
      {(meta.rule_sections ?? []).map((s) => (
        <Field key={s.field} label={labelOf(s.heading)} required={s.required} hint={RULE_HINTS[s.field]}>
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
    </>
  );
}

/**
 * The engine refuses an incomplete contract at write time; say so before the
 * round trip rather than after it.
 */
export function ruleDraftError(meta: Meta, draft: RuleDraft): string | null {
  if (!draft.title.trim()) return "a rule needs a title";
  if (!draft.scenario.trim()) return "a rule with no cue never fires — fill in what it governs";
  for (const s of meta.rule_sections ?? []) {
    if (s.required && !(draft.sections[s.field] ?? "").trim())
      return `${labelOf(s.heading)} is required — a rule that cannot be checked is advice`;
  }
  return null;
}
