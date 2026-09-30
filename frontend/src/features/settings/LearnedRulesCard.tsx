import { useState } from "react";
import { api } from "@/api/client";
import { useAsync } from "@/hooks/useAsync";
import { Badge, Button, Card, StatusMessage } from "@/components";
import { SettingSwitch } from "./SettingSwitch";
import type { EffectiveSettings, RuleProposals } from "@/api/types";
import "./SettingsSection.css";

/**
 * The learned-rules detector: a switch, and what it found.
 *
 * It is OFF until the person turns it on, and even then it only PROPOSES. A
 * proposal is not a rule — match-rules cannot return it — until Accept turns it
 * into one (source learned, precedence 50, below anything the person wrote).
 * Reviewing what is already there works with the switch off.
 */
export function LearnedRulesCard({
  effective,
  writable,
  onChanged,
}: {
  effective: EffectiveSettings;
  writable: boolean;
  onChanged: () => void;
}) {
  const proposals = useAsync<RuleProposals>((signal) => api.ruleProposals(signal), []);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" | "success" } | null>(
    null,
  );

  const run = async (fn: () => Promise<unknown>, done: (res: Record<string, unknown>) => string) => {
    setBusy(true);
    setNote(null);
    try {
      const res = (await fn()) as Record<string, unknown>;
      setNote({ text: done(res), tone: "success" });
      proposals.reload();
      onChanged();
    } catch (e) {
      setNote({ text: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const list = proposals.data?.proposals ?? [];

  return (
    <Card>
      <h2 className="ml-settings__h">
        Learned rules{" "}
        <Badge tone={effective.learned_rules ? "success" : "neutral"}>
          {effective.learned_rules ? "detecting" : "off"}
        </Badge>
      </h2>
      <p className="ml-settings__hint">
        Spots corrections you repeat across sessions (&ldquo;no, never…&rdquo;, &ldquo;não,
        nunca…&rdquo;) and proposes a rule. It reads prompts already captured, with no model call,
        and it only proposes: nothing is applied until you accept it, and a rejected one is kept and
        not proposed again.
      </p>

      <SettingSwitch
        label="Look for repeated corrections"
        hint="Off by default. On, it runs at most once a day at session start, silently. Turn it off and nothing runs; proposals you already have stay reviewable."
        checked={effective.learned_rules}
        source={effective.learned_rules_source}
        envVar="MEMORY_LEDGER_NO_LEARNED_RULES"
        disabled={!writable || busy}
        onChange={(v) =>
          run(
            () => api.saveSettings({ learned_rules: v }),
            () => (v ? "Learned rules are on." : "Learned rules are off."),
          )
        }
      />

      <div className="ml-settings__foot">
        <Button
          disabled={busy || !writable || !effective.learned_rules}
          onClick={() =>
            run(
              () => api.proposeRules(),
              (r) => `Found ${(r.proposed as unknown[] | undefined)?.length ?? 0} new proposal(s).`,
            )
          }
        >
          Look now
        </Button>
        {!writable ? <span className="ml-settings__note">read-only server</span> : null}
      </div>

      {proposals.error ? <StatusMessage tone="error">{proposals.error}</StatusMessage> : null}
      {proposals.data && list.length === 0 ? (
        <p className="ml-settings__hint">No proposals waiting.</p>
      ) : null}
      {list.map((p) => (
        <div key={p.id} className="ml-settings__switch">
          <strong>{p.text}</strong>{" "}
          <Badge tone="neutral">
            {p.count}× in {p.sessions} sessions
          </Badge>
          <ul className="ml-settings__hint">
            {p.evidence.map((e) => (
              <li key={e.id}>&ldquo;{e.snippet}&rdquo;</li>
            ))}
          </ul>
          <div className="ml-settings__foot">
            <Button
              variant="primary"
              disabled={busy || !writable}
              onClick={() =>
                run(
                  () => api.acceptRule(p.id),
                  () => "Accepted: it is now an active learned rule.",
                )
              }
            >
              Accept
            </Button>
            <Button
              disabled={busy || !writable}
              onClick={() =>
                run(
                  () => api.rejectRule(p.id),
                  () => "Rejected. It is kept and will not be proposed again.",
                )
              }
            >
              Reject
            </Button>
          </div>
        </div>
      ))}
      {note ? <StatusMessage tone={note.tone}>{note.text}</StatusMessage> : null}
    </Card>
  );
}
