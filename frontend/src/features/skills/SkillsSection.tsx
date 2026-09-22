import { useMemo, useRef, useState } from "react";
import { api } from "@/api/client";
import { useMeta } from "@/api/MetaContext";
import { useAsync } from "@/hooks/useAsync";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  FacetDropdown,
  Menu,
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
import { ProcessRow } from "./ProcessRow";
import { ProcessDialog } from "./ProcessDialog";
import type { ProcessDraft } from "./ProcessDialog";
import {
  ITEM_TYPES,
  SOURCES,
  allSources,
  countSkills,
  effectiveRealms,
  emptySkillFilter,
  keepProcess,
  keepRule,
  keepSkill,
} from "./filter";
import type { ItemType, KeptSkill, SkillFilter, Source } from "./filter";
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

const TYPE_LABEL: Record<ItemType, string> = {
  process: "Processes",
  skill: "Skills",
  rule: "Rules",
};

const SOURCE_LABEL: Record<Source, [string, string]> = {
  plugin: ["Built-in", "shipped with the plugin"],
  user: ["User", "written in this brain by a person; every process is one"],
  learned: ["Learned", "taught to this brain at runtime"],
};

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * The processes, the two skill trees, and the rule catalogue beside them.
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
  const [newProcess, setNewProcess] = useState(false);
  const [processError, setProcessError] = useState<string | null>(null);
  const { writable } = useMeta();
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

  const show = (t: ItemType) => filter.types.has(t);
  const currentRealm = data?.realm ?? "";
  const shownRealms = effectiveRealms(filter, currentRealm);
  const allProcesses = data?.processes ?? [];
  const keptProcesses = useMemo(() => {
    const realms = effectiveRealms(filter, currentRealm);
    return allProcesses.filter((p) => keepProcess(p, filter, realms));
  }, [allProcesses, filter, currentRealm]);

  /** Every realm that holds a process, and the current one even when it holds none. */
  const realmOptions = useMemo(() => {
    const counts = new Map<string, number>();
    if (currentRealm) counts.set(currentRealm, 0);
    for (const p of allProcesses) counts.set(p.realm, (counts.get(p.realm) ?? 0) + 1);
    return [...counts.entries()]
      .sort(([a], [b]) => (a === currentRealm ? -1 : b === currentRealm ? 1 : a.localeCompare(b)))
      .map(([value, count]) => ({
        value,
        label: value === currentRealm ? `${value} (current)` : value,
        count,
      }));
  }, [allProcesses, currentRealm]);
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
    if (filter.types.has("process")) for (const p of keptProcesses) ids.push(p.id);
    if (filter.types.has("skill")) for (const t of trees) walk(t.kept);
    if (filter.types.has("rule")) for (const r of keptRules) ids.push(r.id);
    return ids;
  }, [trees, keptRules, keptProcesses, filter.types]);

  const nSkills = trees.reduce((n, t) => n + countSkills(t.kept), 0);
  const counts =
    [
      show("process") ? plural(keptProcesses.length, "process", "processes") : null,
      show("skill") ? plural(nSkills, "skill") : null,
      show("rule") ? plural(keptRules.length, "rule") : null,
    ]
      .filter(Boolean)
      .join(" · ") || "nothing shown";
  const narrowed = Boolean(filter.q) || !allSources(filter) || filter.dom !== "all";

  // ---- the actions behind the Options menu --------------------------------
  const exportSelected = async () => {
    const { skills: s, rules: r, processes: p } = sel.split(
      new Set(allProcesses.map((x) => x.id)),
    );
    say("Building the bundle…");
    try {
      const d = await api.exportBundle(s, r, p);
      const parts = [
        d.processes ? plural(d.processes, "process", "processes") : null,
        plural(d.skills, "skill"),
        plural(d.rules, "rule"),
      ].filter(Boolean);
      say(
        `Bundled ${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]} into\n${d.path}\n\nThe zip holds one directory per kind and an import-plan.json naming the order they can be read back in — rules first, then processes, then each skill after the skills it requires.` +
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
        `${file.name}: ${got.counts.processes || 0} process(es), ${got.counts.skills || 0} skill(s), ${got.counts.rules || 0} rule(s). Choose what to bring in.`,
      );
      setStaged({ file: file.name, data: got });
    } catch (err) {
      say(`That bundle could not be read: ${err instanceof Error ? err.message : String(err)}`, "error");
    }
  };

  const runImport = async (select: Record<string, ImportChoice>, processRealm?: string) => {
    if (!staged) return;
    const name = staged.file;
    const token = staged.data.token;
    setStaged(null);
    say("Importing…");
    try {
      const d = await api.importBundle(token, select, processRealm);
      const words: Record<string, string> = {
        created: "created",
        updated: "updated",
        replaced: "replaced",
        ignored: "left alone",
        kept: "left alone",
      };
      say(
        `Imported from ${name}.` +
          (processRealm ? ` Processes landed in the ${processRealm} realm.` : "") +
          `\n${d.applied
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

  const saveProcess = async (draft: ProcessDraft) => {
    setBusy(true);
    setProcessError(null);
    try {
      const d = await api.createProcess(draft);
      setNewProcess(false);
      say(`Saved "${draft.title}" in the ${d.node.realm} realm as ${d.node.id}.`, "success");
      // A process saved outside the realms on screen would otherwise vanish on save.
      if (!shownRealms.has(d.node.realm))
        setFilter((f) => ({ ...f, realms: new Set([...shownRealms, d.node.realm]) }));
      skills.reload();
    } catch (err) {
      setProcessError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const menu: MenuGroup[] = [
    {
      heading: "This brain",
      items: [
        ...(writable
          ? [
              {
                id: "new-process",
                label: "New process…",
                description: `Record a way of working, in the ${currentRealm || "current"} realm unless you pick another.`,
                onSelect: () => setNewProcess(true),
              },
            ]
          : []),
        {
          id: "select",
          label: "Select processes, skills and rules",
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
          Processes are the ways of working this brain was told, kept per realm. Every skill is a
          contract: what must be true when it finishes, what it reads and writes in the graph, and the
          rules it answers to. Nesting is the <code>requires</code> edge. Click anything to open it.
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
        <FacetDropdown
          name="Types"
          options={ITEM_TYPES.map((t) => ({
            value: t,
            label: TYPE_LABEL[t],
            count:
              t === "process"
                ? allProcesses.filter((p) => shownRealms.has(p.realm)).length
                : t === "skill"
                  ? (data?.trees ?? []).reduce((n, tr) => n + tr.count, 0)
                  : (data?.rules?.length ?? 0),
          }))}
          selected={filter.types as Set<string>}
          onChange={(next) => setFilter((f) => ({ ...f, types: next as Set<ItemType> }))}
        />
        {show("process") ? (
          <FacetDropdown
            name="Realms"
            options={realmOptions}
            selected={shownRealms}
            onChange={(next) => setFilter((f) => ({ ...f, realms: next }))}
            note={
              <>
                Processes only: skills and rules are shared contracts. New and imported processes land
                in <code>{currentRealm}</code>.
              </>
            }
          />
        ) : null}
        <FacetDropdown
          name="Sources"
          options={SOURCES.map((v) => ({
            value: v,
            label: SOURCE_LABEL[v][0],
            title: SOURCE_LABEL[v][1],
          }))}
          selected={filter.src as Set<string>}
          onChange={(next) => setFilter((f) => ({ ...f, src: next as Set<Source> }))}
          note={<>A skill in the learned tree counts as both user and learned.</>}
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

      {show("process") && data ? (
        <section className="ml-skills__tree">
          <h2 className="ml-skills__treeh">
            Processes
            <span className="ml-skills__ct">
              {keptProcesses.length}
              {keptProcesses.length !== allProcesses.length ? ` of ${allProcesses.length}` : ""}
            </span>
          </h2>
          <p className="ml-skills__sub">
            Ways of working: <code>process</code> nodes part-of <code>hub_process</code>, each in one
            realm. Showing {[...shownRealms].join(", ") || "no realm"}; use the Realms filter to read
            another realm&rsquo;s.
          </p>
          {keptProcesses.length ? (
            keptProcesses.map((p) => (
              <ProcessRow
                key={p.id}
                process={p}
                currentRealm={currentRealm}
                query={filter.q}
                selectable={sel.picking}
                selected={sel.picked.has(p.id)}
                onSelect={(on) => sel.toggle(p.id, on)}
              />
            ))
          ) : (
            <EmptyState
              title={
                allProcesses.length
                  ? "No process matches this filter."
                  : "No processes recorded yet."
              }
            />
          )}
          {writable ? (
            <Button size="sm" onClick={() => setNewProcess(true)}>
              New process…
            </Button>
          ) : null}
        </section>
      ) : null}

      {show("skill")
        ? trees.map((t) => {
            const n = countSkills(t.kept);
            if (!t.kept.length && narrowed) return null;
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

      {show("rule") ? (
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
          realm={currentRealm}
          realms={realmOptions.map((r) => r.value)}
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

      {newProcess ? (
        <ProcessDialog
          realm={currentRealm}
          busy={busy}
          error={processError}
          onCancel={() => {
            setNewProcess(false);
            setProcessError(null);
          }}
          onSubmit={(d) => void saveProcess(d)}
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
