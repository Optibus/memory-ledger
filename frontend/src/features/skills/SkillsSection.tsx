import { useMemo, useRef, useState } from "react";
import { api } from "@/api/client";
import { useAsync } from "@/hooks/useAsync";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  Menu,
  Pills,
  SearchInput,
  Select,
  Spinner,
  StatusMessage,
} from "@/components";
import type { MenuGroup } from "@/components";
import { Section } from "@/features/shell/Section";
import { SkillCard, NoSkills } from "./SkillCard";
import { RuleRow } from "./RuleRow";
import { ImportDialog } from "./ImportDialog";
import { PromptDialog } from "./PromptDialog";
import { countSkills, emptySkillFilter, keepRule, keepSkill } from "./filter";
import type { KeptSkill, SkillFilter } from "./filter";
import { useSelection } from "./useSelection";
import type { ImportChoice, SkillsPayload, UploadBundleResult } from "@/api/types";
import "./SkillsSection.css";

const TREE_SUB: Record<string, JSX.Element> = {
  plugin: (
    <>
      Shipped with the plugin. Each one declares in the <code>## Depends on</code> block of its
      SKILL.md, which the pre-commit checker gates.
    </>
  ),
  learned: (
    <>
      Taught to this brain at runtime. Each one declares in props, via{" "}
      <code>add-skill --requires/--reads/--writes</code>. A user skill may require a built-in one.
    </>
  ),
};

/**
 * The two skill trees and the rule catalogue beside them.
 *
 * Every skill is a contract: what must be true when it finishes, what it reads
 * and writes in the graph, and the rules it answers to. Nesting is the
 * `requires` edge and nothing else.
 */
export function SkillsSection() {
  const [filter, setFilter] = useState<SkillFilter>(emptySkillFilter);
  const [out, setOut] = useState<string>("");
  const [outTone, setOutTone] = useState<"info" | "error" | "success">("info");
  const [staged, setStaged] = useState<{ file: string; data: UploadBundleResult } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [forceBuiltins, setForceBuiltins] = useState(false);
  const [prompt, setPrompt] = useState<null | "export-tree" | "ingest-tree">(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const sel = useSelection();

  const skills = useAsync<SkillsPayload>((signal) => api.skills(signal), []);
  const data = skills.data;

  const say = (text: string, tone: "info" | "error" | "success" = "info") => {
    setOut(text);
    setOutTone(tone);
  };

  const trees = useMemo(() => {
    if (!data) return [];
    return data.trees.map((t) => ({
      ...t,
      kept: t.skills
        .map((s) => keepSkill(s, t.kind, filter))
        .filter((x): x is KeptSkill => x !== null),
    }));
  }, [data, filter]);

  const keptRules = useMemo(
    () => (data?.rules ?? []).filter((r) => keepRule(r, filter)),
    [data, filter],
  );

  const domains = useMemo(() => {
    const seen = new Set<string>();
    for (const r of data?.rules ?? []) for (const d of r.domains || []) seen.add(d);
    const walk = (s: { domains: string[]; children: typeof s[] }) => {
      for (const d of s.domains) seen.add(d);
      s.children.forEach(walk);
    };
    for (const t of data?.trees ?? []) t.skills.forEach((s) => walk(s as never));
    return [...seen].sort();
  }, [data]);

  /** Every id currently on screen, for "select all shown". */
  const shownIds = useMemo(() => {
    const ids: string[] = [];
    const walk = (list: KeptSkill[]) => {
      for (const s of list) {
        ids.push(s.id);
        walk(s.children);
      }
    };
    if (filter.show !== "rules") for (const t of trees) walk(t.kept);
    if (filter.show !== "skills") for (const r of keptRules) ids.push(r.id);
    return ids;
  }, [trees, keptRules, filter.show]);

  const nSkills = trees.reduce((n, t) => n + countSkills(t.kept), 0);
  const counts = [
    filter.show !== "rules" ? `${nSkills} skill${nSkills === 1 ? "" : "s"}` : null,
    filter.show !== "skills" ? `${keptRules.length} rule${keptRules.length === 1 ? "" : "s"}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  // ---- the actions behind the Options menu --------------------------------
  const exportSelected = async () => {
    const { skills: s, rules: r } = sel.split();
    say("Building the bundle…");
    try {
      const d = await api.exportBundle(s, r);
      say(
        `Bundled ${d.skills} skill${d.skills === 1 ? "" : "s"} and ${d.rules} rule${
          d.rules === 1 ? "" : "s"
        } into\n${d.path}\n\nThe zip holds one directory per kind and an import-plan.json naming the order they can be read back in — rules first, then each skill after the skills it requires.` +
          (d.missing_skills?.length
            ? `\n\nThese are REQUIRED by something you exported and did not travel:\n${d.missing_skills
                .map((x) => `  ${x}`)
                .join("\n")}\nThe other brain needs them, or those links will not form.`
            : "") +
          (d.cycles?.length
            ? `\n\nA dependency cycle was found and ordered arbitrarily:\n${d.cycles
                .map((c) => `  ${c.join(" → ")}`)
                .join("\n")}`
            : ""),
        "success",
      );
      // A real same-origin route, so the browser downloads it AND the file stays
      // on disk at the path above — two machines wants the path, "send it to
      // someone" wants the download.
      const a = document.createElement("a");
      a.href = d.download;
      a.download = d.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (err) {
      say(`Export failed: ${err instanceof Error ? err.message : String(err)}`, "error");
    }
  };

  const deleteSelected = async () => {
    setBusy(true);
    try {
      const d = await api.deleteContracts([...sel.picked], forceBuiltins);
      say(
        `Deleted ${d.count} item${d.count === 1 ? "" : "s"}.\n${d.deleted
          .map((x) => `  ${x.type.padEnd(6)} ${x.id}`)
          .join("\n")}` +
          (d.refused.length
            ? `\n\nRefused:\n${d.refused.map((x) => `  ${x.id} — ${x.why}`).join("\n")}`
            : "") +
          (d.not_found.length
            ? `\n\nNot in this brain:\n${d.not_found.map((x) => `  ${x}`).join("\n")}`
            : ""),
        "success",
      );
      sel.setPicking(false);
      skills.reload();
    } catch (err) {
      say(`Delete failed: ${err instanceof Error ? err.message : String(err)}`, "error");
    } finally {
      setBusy(false);
      setConfirmDelete(false);
      setForceBuiltins(false);
    }
  };

  const onFile = async (file: File) => {
    say(`Reading ${file.name}…`);
    try {
      const b64 = await toBase64(file);
      const got = await api.uploadBundle(file.name, b64);
      say(
        `${file.name}: ${got.counts.skills || 0} skill(s), ${got.counts.rules || 0} rule(s). Choose what to bring in.`,
      );
      setStaged({ file: file.name, data: got });
    } catch (err) {
      say(`That bundle could not be read: ${err instanceof Error ? err.message : String(err)}`, "error");
    }
  };

  const runImport = async (select: Record<string, ImportChoice>) => {
    if (!staged) return;
    const name = staged.file;
    const token = staged.data.token;
    setStaged(null);
    say("Importing…");
    try {
      const d = await api.importBundle(token, select);
      const words: Record<string, string> = {
        created: "created",
        updated: "updated",
        replaced: "replaced",
        ignored: "left alone",
        kept: "left alone",
      };
      say(
        `Imported from ${name}.\n${d.applied
          .map(
            (a) =>
              `  ${(words[a.action] || a.action).padEnd(10)} ${a.id}` +
              (a.rules_not_found?.length
                ? `  (rules not in this brain: ${a.rules_not_found.join(", ")})`
                : ""),
          )
          .join("\n")}` +
          (d.tree ? `\n\nTree: +${d.tree.added} edges, -${d.tree.pruned}.` : "") +
          (d.missing_skills?.length
            ? `\n\nThe bundle declares skills it did not carry: ${d.missing_skills.join(", ")}. Those links cannot form until they are in this brain.`
            : ""),
        "success",
      );
      skills.reload();
    } catch (err) {
      say(`Import failed: ${err instanceof Error ? err.message : String(err)}`, "error");
    }
  };

  const runSync = async (kind: "export-tree" | "ingest-tree", dir: string) => {
    setPrompt(null);
    const label = kind === "export-tree" ? "Exporting" : "Ingesting";
    say(`${label}…`);
    try {
      const d =
        kind === "export-tree"
          ? await api.exportSkills(dir ? { dir } : {})
          : await api.ingestSkills(dir ? { dir } : {});
      if (kind === "export-tree") {
        const ruleCount = typeof d.rules === "number" ? d.rules : (d.rules?.length ?? 0);
        say(
          `Exported ${d.skills} skill${d.skills === 1 ? "" : "s"} and ${ruleCount} rule file${
            ruleCount === 1 ? "" : "s"
          } to\n${d.dir}\n\nCopy that directory to the other machine and ingest it there.`,
          "success",
        );
      } else {
        const acts = (d.ingested ?? []).map(
          (r) =>
            `  ${r.action.padEnd(8)} ${r.id}` +
            (r.rules_not_found?.length
              ? `  (rules not in this brain: ${r.rules_not_found.join(", ")})`
              : ""),
        );
        const rules = Array.isArray(d.rules) ? d.rules.map((r) => `  ${r.action.padEnd(8)} ${r.id}`) : [];
        say(
          `Ingested ${d.count} skill${d.count === 1 ? "" : "s"}.\n${acts.join("\n")}` +
            (rules.length ? `\n\nRules that travelled with them:\n${rules.join("\n")}` : "") +
            (d.tree ? `\n\nTree: +${d.tree.added} edges, -${d.tree.pruned}.` : ""),
          "success",
        );
      }
      skills.reload();
    } catch (err) {
      say(`${label} failed: ${err instanceof Error ? err.message : String(err)}`, "error");
    }
  };

  const menu: MenuGroup[] = [
    {
      heading: "This brain",
      items: [
        {
          id: "select",
          label: "Select skills and rules",
          description: "Checkboxes on every card, then export or delete what you ticked.",
          onSelect: () => sel.setPicking(!sel.picking),
        },
      ],
    },
    {
      heading: "Bundles — a selection, as one zip",
      items: [
        {
          id: "import",
          label: "Import from a bundle…",
          description: "Choose a .zip, pick what to bring in, and say what to do about anything this brain already holds.",
          onSelect: () => fileRef.current?.click(),
        },
      ],
    },
    {
      heading: "Whole-tree markdown sync",
      items: [
        {
          id: "export-tree",
          label: "Export the learned tree to .md",
          description: "Every learned skill, as folders on disk.",
          onSelect: () => setPrompt("export-tree"),
        },
        {
          id: "ingest-tree",
          label: "Ingest a .md folder",
          description: "Read a directory of SKILL.md files back in.",
          onSelect: () => setPrompt("ingest-tree"),
        },
      ],
    },
  ];

  return (
    <Section
      title="Skills"
      lead={
        <>
          Every skill is a contract: what must be true when it finishes, what it reads and writes in
          the graph, and the rules it answers to. Nesting is the <code>requires</code> edge. Click a
          skill to open it, then a rule to read it.
        </>
      }
      actions={<Menu label="Options" groups={menu} />}
    >
      {out ? <StatusMessage tone={outTone}>{out}</StatusMessage> : null}

      <input
        ref={fileRef}
        type="file"
        accept=".zip,application/zip"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) void onFile(f);
        }}
      />

      {sel.picking ? (
        <div className="ml-skills__selbar">
          <span className="ml-skills__seln">
            {sel.picked.size ? `${sel.picked.size} selected` : "Nothing selected"}
          </span>
          <Button size="sm" onClick={() => sel.selectAll(shownIds)}>Select all shown</Button>
          <Button size="sm" onClick={sel.clear}>Clear</Button>
          <span style={{ flex: 1 }} />
          <Button size="sm" disabled={!sel.picked.size} onClick={exportSelected}>
            Export selected…
          </Button>
          <Button
            size="sm"
            variant="danger"
            disabled={!sel.picked.size}
            onClick={() => setConfirmDelete(true)}
          >
            Delete selected…
          </Button>
          <Button size="sm" onClick={() => sel.setPicking(false)}>Done</Button>
        </div>
      ) : null}

      <div className="ml-skills__filter">
        <SearchInput
          value={filter.q}
          onChange={(q) => setFilter((f) => ({ ...f, q }))}
          placeholder="filter by name, trigger, rule text, node type…"
        />
        <Pills
          aria-label="show"
          value={filter.show}
          onChange={(show) => setFilter((f) => ({ ...f, show }))}
          options={[
            { value: "all", label: "Both" },
            { value: "skills", label: "Skills" },
            { value: "rules", label: "Rules" },
          ]}
        />
        <Pills
          aria-label="source"
          value={filter.src}
          onChange={(src) => setFilter((f) => ({ ...f, src }))}
          options={[
            { value: "all", label: "Any source" },
            { value: "plugin", label: "Built-in" },
            { value: "user", label: "User" },
            { value: "learned", label: "Learned" },
          ]}
        />
        <Select
          aria-label="domain"
          value={filter.dom}
          onChange={(e) => setFilter((f) => ({ ...f, dom: e.target.value }))}
          options={[
            { value: "all", label: "Any domain" },
            ...domains.map((d) => ({ value: d, label: data?.areas?.[d] || d })),
          ]}
        />
        <span className="ml-skills__count">{counts}</span>
      </div>

      {skills.loading ? <Spinner label="loading the skill trees" /> : null}
      {skills.error ? (
        <StatusMessage tone="error">
          {skills.error}. If the trees were never built, run{" "}
          <code>python3 scripts/graph.py sync-skill-tree</code>.
        </StatusMessage>
      ) : null}

      {filter.show !== "rules"
        ? trees.map((t) => {
            const n = countSkills(t.kept);
            if (!t.kept.length && (filter.q || filter.src !== "all" || filter.dom !== "all"))
              return null;
            return (
              <section className="ml-skills__tree" key={t.hub}>
                <h2 className="ml-skills__treeh">
                  {t.title}
                  <span className="ml-skills__ct">
                    {n}
                    {n !== t.count ? ` of ${t.count}` : ""}
                  </span>
                </h2>
                <p className="ml-skills__sub">{TREE_SUB[t.kind]}</p>
                {t.kept.length ? (
                  t.kept.map((s) => (
                    <SkillBranch
                      key={s.id}
                      skill={s}
                      depth={0}
                      query={filter.q}
                      picking={sel.picking}
                      picked={sel.picked}
                      onPick={sel.toggle}
                    />
                  ))
                ) : (
                  <NoSkills />
                )}
              </section>
            );
          })
        : null}

      {filter.show !== "skills" ? (
        <section className="ml-skills__tree">
          <h2 className="ml-skills__treeh">
            Rules
            <span className="ml-skills__ct">
              {keptRules.length}
              {keptRules.length !== (data?.rules?.length ?? 0) ? ` of ${data?.rules?.length}` : ""}
            </span>
          </h2>
          <p className="ml-skills__sub">
            The genome every skill asks before acting. <code>source</code> says who authored it:
            built-in rules come from CONVENTIONS.md, user and learned ones from this brain.
          </p>
          {keptRules.length ? (
            keptRules.map((r) => (
              <RuleRow
                key={r.id}
                id={r.id}
                title={r.title}
                source={r.source}
                nonNegotiable={r.non_negotiable}
                query={filter.q}
                selectable={sel.picking}
                selected={sel.picked.has(r.id)}
                onSelect={(on) => sel.toggle(r.id, on)}
              />
            ))
          ) : (
            <EmptyState title="No rule matches this filter." />
          )}
        </section>
      ) : null}

      {staged ? (
        <ImportDialog
          filename={staged.file}
          staged={staged.data}
          onCancel={() => {
            setStaged(null);
            say("Import cancelled. Nothing was written.");
          }}
          onConfirm={runImport}
        />
      ) : null}

      {confirmDelete ? (
        <ConfirmDialog
          title={`Delete ${sel.picked.size} item${sel.picked.size === 1 ? "" : "s"}?`}
          danger
          busy={busy}
          confirmLabel="Delete"
          onCancel={() => {
            setConfirmDelete(false);
            setForceBuiltins(false);
          }}
          onConfirm={deleteSelected}
          body={
            <>
              <p className="ml-skills__warn">
                A contract is not history: a skill nobody should fire again has to stop firing, and a
                rule left in the corpus keeps binding every request. This deletes the nodes and every
                edge on them, and it cannot be undone from here.
              </p>
              <div className="ml-skills__dellist">
                {[...sel.picked].slice(0, 200).map((id) => (
                  <div key={id}>{id}</div>
                ))}
                {sel.picked.size > 200 ? <p>…and {sel.picked.size - 200} more.</p> : null}
              </div>
              <Button
                size="sm"
                active={forceBuiltins}
                onClick={() => setForceBuiltins((v) => !v)}
              >
                {forceBuiltins ? "✓ built-ins included" : "Delete built-ins too"}
              </Button>
              <p className="ml-skills__warn">
                A built-in ships as a file: deleting one empties its row and the next ingest brings it
                straight back, so it is refused unless you say to do it anyway.
              </p>
            </>
          }
        />
      ) : null}

      {prompt ? (
        <PromptDialog
          kind={prompt}
          onCancel={() => setPrompt(null)}
          onSubmit={(dir) => void runSync(prompt, dir)}
        />
      ) : null}
    </Section>
  );
}

/** Indentation carries the `requires` edge and nothing else. */
function SkillBranch({
  skill,
  depth,
  query,
  picking,
  picked,
  onPick,
}: {
  skill: KeptSkill;
  depth: number;
  query: string;
  picking: boolean;
  picked: Set<string>;
  onPick: (id: string, on: boolean) => void;
}) {
  return (
    <div className={depth ? "ml-skills__kid" : undefined}>
      <SkillCard
        skill={skill}
        query={query}
        selectable={picking}
        selected={picked}
        onSelect={onPick}
      />
      {skill.children.map((c) => (
        <SkillBranch
          key={c.id}
          skill={c}
          depth={depth + 1}
          query={query}
          picking={picking}
          picked={picked}
          onPick={onPick}
        />
      ))}
    </div>
  );
}

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(",").pop() ?? "");
    fr.onerror = () => reject(new Error("could not read that file"));
    fr.readAsDataURL(file);
  });
}
