import { useCallback, useState } from "react";
import { api } from "@/api/client";
import { useMeta } from "@/api/MetaContext";
import { useAsync } from "@/hooks/useAsync";
import { useKeyboardShortcut } from "@/hooks/useKeyboardShortcut";
import { Button, Modal, StatusMessage } from "@/components";
import { RuleForm, emptyRuleDraft, ruleDraftError } from "./RuleForm";
import type { RuleDraft } from "./RuleForm";
import { SkillForm, emptySkillDraft, skillDraftError, slugify } from "./SkillForm";
import type { SkillDraft } from "./SkillForm";
import type { SkillsPayload } from "@/api/types";
import "./CreateLauncher.css";

export interface CreateLauncherProps {
  /** Re-reads the corpus, so a new contract is on the page without a reload. */
  onCreated: () => void;
}

type Stage = null | "choose" | "skill" | "rule" | "rule-on-skill";

/**
 * The floating + button and the modal stack behind it.
 *
 * Authoring here goes through the same `build_skill_contract` /
 * `build_rule_contract` the CLI uses and is REFUSED when a required section is
 * missing — so the browser is not a third way to write a contract, it is the
 * same shape, checked at write time rather than reported afterwards by
 * audit-contracts.
 */
export function CreateLauncher({ onCreated }: CreateLauncherProps) {
  const { meta, writable } = useMeta();
  const [stage, setStage] = useState<Stage>(null);
  const [ruleDraft, setRuleDraft] = useState<RuleDraft>(() => emptyRuleDraft(meta));
  const [skillDraft, setSkillDraft] = useState<SkillDraft>(emptySkillDraft);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const catalog = useAsync<SkillsPayload | null>(
    async (signal) => (stage ? await api.skills(signal) : null),
    [stage !== null],
  );

  useKeyboardShortcut(
    "n",
    useCallback(() => {
      if (writable) setStage("choose");
    }, [writable]),
  );

  if (!writable) return null;

  const close = () => {
    setStage(null);
    setError(null);
  };

  const submitRule = async () => {
    if (!meta) return;
    const problem = ruleDraftError(meta, ruleDraft);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createRule({ ...ruleDraft });
      if (stage === "rule-on-skill") {
        // The rule was created first and the skill form is still underneath —
        // drop back into it rather than closing the stack.
        setStage("skill");
        setRuleDraft(emptyRuleDraft(meta));
        catalog.reload();
      } else {
        close();
        setRuleDraft(emptyRuleDraft(meta));
        onCreated();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const submitSkill = async () => {
    if (!meta) return;
    const problem = skillDraftError(meta, skillDraft);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createSkill({
        name: skillDraft.name.trim() || slugify(skillDraft.title),
        title: skillDraft.title.trim(),
        description: skillDraft.description.trim(),
        realm: skillDraft.realm.trim(),
        sections: skillDraft.sections,
        rules: skillDraft.rules,
        new_rules: [],
        ...skillDraft.declarations,
      });
      close();
      setSkillDraft(emptySkillDraft());
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className="ml-fab"
        title="create a skill or a rule (N)"
        aria-label="create a skill or a rule"
        onClick={() => setStage("choose")}
      >
        +
      </button>

      {stage === "choose" ? (
        <Modal
          title="What are you writing?"
          subtitle="A skill is a thing the brain can DO. A rule is something it must hold to whenever it acts."
          onClose={close}
          width={520}
        >
          <div className="ml-create__choices">
            <button type="button" className="ml-create__choice" onClick={() => setStage("skill")}>
              <b>A skill</b>
              <span>
                A contract: what must be TRUE when it finishes, what it reads and writes, and what it
                may never do on the way.
              </span>
            </button>
            <button type="button" className="ml-create__choice" onClick={() => setStage("rule")}>
              <b>A rule</b>
              <span>
                A clause every matching request answers to, with the cue it fires on and the way a
                reader can tell it was followed.
              </span>
            </button>
          </div>
        </Modal>
      ) : null}

      {stage === "skill" && meta ? (
        <Modal
          title="New skill"
          subtitle="A skill is a contract: what must be TRUE when it finishes, and what it may not do on the way. Never the keystrokes (CONVENTIONS §5)."
          onClose={close}
          width={720}
          footer={
            <>
              <Button variant="primary" onClick={submitSkill} disabled={busy}>
                {busy ? "writing…" : "Create skill"}
              </Button>
              <Button onClick={close} disabled={busy}>Cancel</Button>
            </>
          }
        >
          {error ? <StatusMessage tone="error">{error}</StatusMessage> : null}
          <SkillForm
            meta={meta}
            catalog={catalog.data ?? null}
            draft={skillDraft}
            onChange={setSkillDraft}
            onNewRule={() => setStage("rule-on-skill")}
          />
        </Modal>
      ) : null}

      {(stage === "rule" || stage === "rule-on-skill") && meta ? (
        <Modal
          title="New rule"
          subtitle="A rule that cannot be checked is advice (CONVENTIONS §5b)."
          onClose={() => (stage === "rule-on-skill" ? setStage("skill") : close())}
          width={720}
          depth={stage === "rule-on-skill" ? 1 : 0}
          footer={
            <>
              <Button variant="primary" onClick={submitRule} disabled={busy}>
                {busy ? "writing…" : "Create rule"}
              </Button>
              <Button
                onClick={() => (stage === "rule-on-skill" ? setStage("skill") : close())}
                disabled={busy}
              >
                Cancel
              </Button>
            </>
          }
        >
          {error ? <StatusMessage tone="error">{error}</StatusMessage> : null}
          <RuleForm meta={meta} draft={ruleDraft} onChange={setRuleDraft} />
        </Modal>
      ) : null}
    </>
  );
}
