import { Badge, Card, StatusMessage } from "@/components";
import { usePrefsSync } from "@/hooks/usePrefs";
import { SettingSwitch } from "./SettingSwitch";
import type { EffectiveSettings } from "@/api/types";
import "./SettingsSection.css";

/**
 * This browser's own view state, and whether the brain keeps a copy.
 *
 * The split is deliberate. A setting (timezone, realm, owner) changes what the
 * ENGINE computes, so it has to be server-side or the CLI and the cron disagree
 * with the page. Which dashboard view you had open changes nothing, so it lives
 * in localStorage — instant, and never a disk write on the brain for a value no
 * other reader cares about.
 *
 * Syncing covers the one case localStorage cannot: the same person on a second
 * machine. Turned on, the brain's copy is what a cold browser starts from; with
 * no copy stored yet, the defaults still apply, so enabling it never blanks a
 * view.
 */
export function BrowserCard({
  effective,
  writable,
  onChange,
}: {
  effective: EffectiveSettings;
  writable: boolean;
  onChange: (key: string, value: boolean) => void;
}) {
  const { syncing } = usePrefsSync();

  return (
    <Card>
      <h2 className="ml-settings__h">
        This browser <Badge tone={syncing ? "success" : "neutral"}>{syncing ? "synced" : "local only"}</Badge>
      </h2>
      <p className="ml-settings__hint">
        Which dashboard view you had open, how far back <em>closed</em> looks, what the map has
        filtered out and how fast it spins. These are remembered per browser, in{" "}
        <code>localStorage</code>, and they are restored when you come back to a section.
      </p>

      <SettingSwitch
        label="Keep these preferences in the brain"
        hint="Stores them in <home>/ui_prefs.json as well, so a second machine — or another browser on this one — starts from what you last had instead of the defaults. Your local copy stays authoritative while you work; the brain's is what a cold start reads."
        checked={effective.ui_sync}
        source={effective.ui_sync_source}
        envVar="MEMORY_LEDGER_NO_UI_SYNC"
        disabled={!writable}
        onChange={(v) => onChange("ui_sync", v)}
      />

      {!writable ? (
        <StatusMessage>
          This server was started with <code>--no-write</code>, so preferences stay in this browser
          only.
        </StatusMessage>
      ) : null}
    </Card>
  );
}
