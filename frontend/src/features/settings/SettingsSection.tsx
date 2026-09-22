import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { useAsync } from "@/hooks/useAsync";
import {
  Badge,
  Button,
  Card,
  Field,
  KeyValue,
  Spinner,
  StatusMessage,
  TextInput,
} from "@/components";
import { Section } from "@/features/shell/Section";
import type { Settings, Status, Timezones } from "@/api/types";
import "./SettingsSection.css";

/**
 * Preferences, and the health of the store behind them.
 *
 * Everything here is written SERVER-side, to `<home>/settings.json` — a
 * timezone set in the browser has to hold for the CLI, the hooks and any cron
 * reading the same brain, and `localStorage` cannot do that.
 */
export function SettingsSection() {
  const settings = useAsync<Settings>((signal) => api.settings(signal), []);
  const status = useAsync<Status>((signal) => api.status(signal), []);
  const zones = useAsync<Timezones>((signal) => api.timezones(signal), []);

  return (
    <Section
      title="Settings"
      lead="Saved to the brain's home directory, so every path into it — CLI, hooks, cron, this page — reads the same answer."
    >
      {settings.loading ? <Spinner label="reading settings" /> : null}
      {settings.error ? <StatusMessage tone="error">{settings.error}</StatusMessage> : null}

      {settings.data ? (
        <PreferencesCard
          settings={settings.data}
          zones={zones.data?.zones ?? []}
          onSaved={() => settings.reload()}
        />
      ) : null}

      {status.data ? <StoreCard status={status.data} onChanged={() => status.reload()} /> : null}
      {status.error ? <StatusMessage tone="error">{status.error}</StatusMessage> : null}
    </Section>
  );
}

function PreferencesCard({
  settings,
  zones,
  onSaved,
}: {
  settings: Settings;
  zones: string[];
  onSaved: () => void;
}) {
  const eff = settings.effective;
  const [tz, setTz] = useState(settings.settings.tz ?? "");
  const [realm, setRealm] = useState(settings.settings.realm ?? "");
  const [mapPoll, setMapPoll] = useState(String(eff.map_poll_seconds));
  const [dashRefresh, setDashRefresh] = useState(String(eff.dash_refresh_seconds));
  const [out, setOut] = useState<{ text: string; tone: "info" | "error" | "success" } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setTz(settings.settings.tz ?? "");
    setRealm(settings.settings.realm ?? "");
    setMapPoll(String(eff.map_poll_seconds));
    setDashRefresh(String(eff.dash_refresh_seconds));
  }, [settings, eff.map_poll_seconds, eff.dash_refresh_seconds]);

  const save = async () => {
    setBusy(true);
    setOut(null);
    try {
      // "" REMOVES a key, falling back to the env var or the system default —
      // which is why the fields send empty strings rather than skipping them.
      await api.saveSettings({
        tz,
        realm,
        map_poll_seconds: Number(mapPoll) || 0,
        dash_refresh_seconds: Number(dashRefresh) || 0,
      });
      setOut({ text: `Saved to ${settings.path}.`, tone: "success" });
      onSaved();
    } catch (err) {
      setOut({ text: err instanceof Error ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const overridden = Object.keys(settings.env);

  return (
    <Card>
      <h2 className="ml-settings__h">Preferences</h2>
      <p className="ml-settings__hint">
        Precedence is the same everywhere in the engine: explicit flag &gt; environment variable &gt;
        this file &gt; built-in default.
      </p>

      {overridden.length ? (
        <StatusMessage tone="info">
          The shell has taken over {overridden.join(", ")} — saving here will not change what the
          engine actually uses until those variables are unset.
        </StatusMessage>
      ) : null}

      <Field
        label="Timezone"
        hint={`Decides which calendar day a due date falls on. In effect: ${
          eff.tz || eff.system_tz
        } (from ${eff.tz_source}); the system says ${eff.system_tz}.`}
      >
        {(id) => (
          <>
            <TextInput
              id={id}
              list="ml-zones"
              value={tz}
              placeholder={eff.system_tz}
              onChange={(e) => setTz(e.target.value)}
            />
            <datalist id="ml-zones">
              {zones.map((z) => (
                <option key={z} value={z} />
              ))}
            </datalist>
          </>
        )}
      </Field>

      <Field label="Default realm" hint={`New nodes land here when nothing else says. In effect: ${eff.realm}.`}>
        {(id) => (
          <TextInput id={id} value={realm} placeholder={eff.realm} onChange={(e) => setRealm(e.target.value)} />
        )}
      </Field>

      <div className="ml-settings__grid">
        <Field label="Map poll (seconds)" hint="How often the map re-reads the graph.">
          {(id) => (
            <TextInput id={id} type="number" min={1} value={mapPoll} onChange={(e) => setMapPoll(e.target.value)} />
          )}
        </Field>
        <Field label="Dashboard refresh (seconds)" hint="How often a date view re-runs.">
          {(id) => (
            <TextInput
              id={id}
              type="number"
              min={5}
              value={dashRefresh}
              onChange={(e) => setDashRefresh(e.target.value)}
            />
          )}
        </Field>
      </div>

      <div className="ml-settings__foot">
        <Button variant="primary" onClick={save} disabled={busy || !settings.writable}>
          {busy ? "saving…" : "Save"}
        </Button>
        {!settings.writable ? <span className="ml-settings__note">read-only server</span> : null}
      </div>
      {out ? <StatusMessage tone={out.tone}>{out.text}</StatusMessage> : null}
    </Card>
  );
}

function StoreCard({ status, onChanged }: { status: Status; onChanged: () => void }) {
  const [url, setUrl] = useState(status.neo4j.url);
  const [db, setDb] = useState(status.neo4j.db);
  const [user, setUser] = useState(status.neo4j.user);
  const [password, setPassword] = useState("");
  const [out, setOut] = useState<{ text: string; tone: "info" | "error" | "success" } | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setOut({ text: `${label}…`, tone: "info" });
    try {
      const res = (await fn()) as Record<string, unknown>;
      setOut({ text: describe(label, res), tone: "success" });
      onChanged();
    } catch (err) {
      setOut({ text: `${label} failed: ${err instanceof Error ? err.message : String(err)}`, tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <h2 className="ml-settings__h">Where the data lives</h2>
      <KeyValue
        pairs={[
          ["database", status.db],
          ["home", status.home],
          ["nodes", status.nodes],
          ["edges", status.edges],
          ["queued writes", status.pending_ops],
          ["loose captures", status.loose_captures],
          ["reading from", status.neo4j.reading_from],
        ]}
      />

      <h3 className="ml-settings__h3">
        The neo4j master{" "}
        <Badge tone={status.neo4j.up ? "success" : status.neo4j.configured ? "danger" : "neutral"}>
          {status.neo4j.up ? "reachable" : status.neo4j.configured ? "unreachable" : "not configured"}
        </Badge>
      </h3>
      <p className="ml-settings__hint">
        Optional. The brain is one local SQLite file and works offline; a master is a mirror, not a
        dependency. The password is written mode 600 and never sent back to the browser — leave it
        blank to keep the stored one, and clear the url to go sqlite-only.
      </p>
      {status.neo4j.env.length ? (
        <StatusMessage>
          These are set in the environment and win over the file: {status.neo4j.env.join(", ")}.
        </StatusMessage>
      ) : null}

      <div className="ml-settings__grid">
        <Field label="URL">
          {(id) => (
            <TextInput id={id} value={url} placeholder="neo4j+s://…" onChange={(e) => setUrl(e.target.value)} />
          )}
        </Field>
        <Field label="Database">
          {(id) => <TextInput id={id} value={db} placeholder="neo4j" onChange={(e) => setDb(e.target.value)} />}
        </Field>
        <Field label="User">
          {(id) => <TextInput id={id} value={user} placeholder="neo4j" onChange={(e) => setUser(e.target.value)} />}
        </Field>
        <Field
          label="Password"
          hint={status.neo4j.password_set ? "One is stored. Blank keeps it." : "None stored yet."}
        >
          {(id) => (
            <TextInput
              id={id}
              type="password"
              value={password}
              autoComplete="new-password"
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>
      </div>

      <div className="ml-settings__foot">
        <Button
          disabled={busy || !status.writable}
          onClick={() => run("Saving", () => api.saveNeo4j({ url, db, user, password }))}
        >
          Save connection
        </Button>
        <Button disabled={busy || !status.writable} onClick={() => run("Testing", () => api.testNeo4j())}>
          Test
        </Button>
        <Button
          disabled={busy || !status.writable}
          title={`${status.pending_ops} write${status.pending_ops === 1 ? "" : "s"} queued`}
          onClick={() => run("Flushing", () => api.flush())}
        >
          Flush queue ({status.pending_ops})
        </Button>
      </div>

      <h3 className="ml-settings__h3">Upkeep</h3>
      <p className="ml-settings__hint">
        Prune runs <code>maintain</code>: it consolidates old captures and deletes nothing. Finishing
        work is archiving, never deleting (CONVENTIONS §2a).
      </p>
      <div className="ml-settings__foot">
        <Button disabled={busy || !status.writable} onClick={() => run("Pruning", () => api.prune())}>
          Prune
        </Button>
      </div>

      {out ? <StatusMessage tone={out.tone}>{out.text}</StatusMessage> : null}
    </Card>
  );
}

/** Turns a write result into a sentence, rather than showing raw JSON. */
function describe(label: string, res: Record<string, unknown>): string {
  const parts = Object.entries(res)
    .filter(([k]) => k !== "ok")
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
  return parts.length ? `${label} done — ${parts.join(", ")}` : `${label} done.`;
}

export { PreferencesCard, StoreCard };
