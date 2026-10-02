import { useState } from "react";
import { api } from "@/api/client";
import { Badge, Card, StatusMessage } from "@/components";
import { SettingSwitch } from "./SettingSwitch";
import type { EffectiveSettings } from "@/api/types";
import "./SettingsSection.css";

/**
 * Gated per-prompt recall: the `prompt_recall` setting, which the
 * UserPromptSubmit hook reads.
 *
 * It is OFF by default because, unlike capture, it costs context tokens on
 * every prompt it fires on. On, it injects at most two short excerpts, and only
 * when the prompt clearly matches something the brain already holds.
 */
export function PromptRecallCard({
  effective,
  writable,
  onChanged,
}: {
  effective: EffectiveSettings;
  writable: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async (v: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      await api.saveSettings({ prompt_recall: v });
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <h2 className="ml-settings__h">
        Prompt recall{" "}
        <Badge tone={effective.prompt_recall ? "success" : "neutral"}>
          {effective.prompt_recall ? "on" : "off"}
        </Badge>
      </h2>
      <p className="ml-settings__hint">
        Before a prompt reaches the model, look for what the brain already knows about it and hand
        over up to two short excerpts. Decided by full-text search and a regex, with no model call.
        It fires only on a clear match, never repeats a node in a session, and never uses personal
        notes.
      </p>

      <SettingSwitch
        label="Recall related notes on each prompt"
        hint="Off by default. On, it costs about 110 tokens at most, and only on prompts that match; the rest cost nothing."
        checked={effective.prompt_recall}
        source={effective.prompt_recall_source}
        envVar="MEMORY_LEDGER_NO_PROMPT_RECALL"
        disabled={!writable || busy}
        onChange={save}
      />
      {!writable ? <span className="ml-settings__note">read-only server</span> : null}
      {err ? <StatusMessage tone="error">{err}</StatusMessage> : null}
    </Card>
  );
}
