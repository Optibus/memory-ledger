import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { useAsync } from "@/hooks/useAsync";
import { Badge, Button, Card, Checkbox, EmptyState, Field, Spinner, StatusMessage, TextArea, TextInput } from "@/components";
import type { BadgeTone } from "@/components";
import type { CustomTriggerDetail, CustomTriggerDryRun, CustomTriggerList, CustomTriggerVerdict } from "@/api/types";
import "./SettingsSection.css";

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

const TONE: Record<string, BadgeTone> = {
  ok: "success",
  skipped: "neutral",
  running: "accent",
  error: "danger",
  invalid: "warn",
};

function StatusBadge({ status }: { status?: string }) {
  if (!status) return <span className="ml-settings__note">never ran</span>;
  return <Badge tone={TONE[status] ?? "neutral"}>{status}</Badge>;
}

/**
 * Custom triggers: polls written as blocks, one YAML file each in `<home>/triggers`
 * (docs/trigger.md#custom-triggers). The editor is the file itself, the same format the
 * CLI and the `custom-trigger-builder` skill write: validate checks it without saving,
 * save refuses what does not validate, and the dry run calls what the saved blocks call
 * and creates nothing.
 */
export function CustomTriggersCard({ writable }: { writable: boolean }) {
  const list = useAsync<CustomTriggerList>((signal) => api.customTriggers(signal), []);
  const [open, setOpen] = useState<string | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [newId, setNewId] = useState("");
  const [busy, setBusy] = useState("");
  const [out, setOut] = useState<{ text: string; tone: "success" | "error" } | null>(null);

  if (list.loading && !list.data) return <Spinner label="reading custom triggers" />;
  if (list.error) return <StatusMessage tone="error">{list.error}</StatusMessage>;
  if (!list.data) return null;
  const data = list.data;

  const toggle = async (id: string, enabled: boolean) => {
    setBusy(id);
    setOut(null);
    try {
      await api.enableCustomTrigger(id, enabled);
      setOut({ text: `${id} is ${enabled ? "on" : "off"}.`, tone: "success" });
      list.reload();
    } catch (err) {
      setOut({ text: err instanceof Error ? err.message : String(err), tone: "error" });
    } finally {
      setBusy("");
    }
  };

  const startNew = () => {
    if (!ID_RE.test(newId)) {
      setOut({ text: "An id is letters, digits, - and _.", tone: "error" });
      return;
    }
    if (data.triggers.some((t) => t.id === newId)) {
      setOut({ text: `${newId} exists already.`, tone: "error" });
      return;
    }
    setOut(null);
    setIsNew(true);
    setOpen(newId);
  };

  return (
    <Card>
      <h2 className="ml-settings__h">Custom triggers</h2>
      <p className="ml-settings__hint">
        Polls you write as blocks: every event one finds becomes a task. One file each in <code>{data.dir}</code>.{" "}
        {data.active ? (
          <Badge tone="success">scheduled here</Badge>
        ) : (
          <Badge tone="warn">not scheduled: switch on triggers and custom.enabled in trigger.json</Badge>
        )}
      </p>
      <p className="ml-settings__note">
        Secrets in trigger.json: {data.secrets.length ? data.secrets.join(", ") : "none"}. A trigger names them; their
        values never show here.
      </p>

      {data.triggers.length === 0 ? (
        <EmptyState title="No custom triggers yet" hint="Start one below, or ask the brain to build one with you." />
      ) : (
        <table className="ml-settings__table ml-custom__table">
          <thead>
            <tr>
              <th>On</th>
              <th>Trigger</th>
              <th>Every</th>
              <th>Last run</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.triggers.map((t) => (
              <tr key={t.id}>
                <td>
                  <Checkbox
                    checked={t.enabled === true}
                    onChange={(v) => void toggle(t.id, v)}
                    label={<span className="sr-only">{`${t.id} on`}</span>}
                    disabled={!writable || !t.ok || busy === t.id}
                  />
                </td>
                <td>
                  <code>{t.id}</code>
                  {t.name && t.name !== t.id ? ` · ${t.name}` : ""}
                  {t.ok ? null : <div className="ml-settings__note">{t.error}</div>}
                </td>
                <td>{t.interval ?? ""}</td>
                <td>
                  <StatusBadge status={t.state.last_status} />{" "}
                  <span className="ml-settings__note">{t.state.last_run_at ?? ""}</span>
                  {t.state.last_error ? <div className="ml-settings__note">{t.state.last_error}</div> : null}
                </td>
                <td>
                  <Button
                    size="sm"
                    onClick={() => {
                      setIsNew(false);
                      setOpen(open === t.id ? null : t.id);
                    }}
                  >
                    {open === t.id ? "Close" : "Edit"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {writable ? (
        <div className="ml-settings__foot">
          <Field label="New trigger id">
            {(id) => <TextInput id={id} value={newId} placeholder="site-health" onChange={(e) => setNewId(e.target.value.trim())} />}
          </Field>
          <Button onClick={startNew}>New trigger</Button>
        </div>
      ) : null}
      {out ? <StatusMessage tone={out.tone}>{out.text}</StatusMessage> : null}

      {open ? (
        <CustomTriggerEditor
          key={open}
          id={open}
          isNew={isNew}
          template={data.template.split("my-trigger").join(open)}
          writable={writable}
          onSaved={() => {
            setIsNew(false);
            list.reload();
          }}
        />
      ) : null}
    </Card>
  );
}

function CustomTriggerEditor({
  id,
  isNew,
  template,
  writable,
  onSaved,
}: {
  id: string;
  isNew: boolean;
  template: string;
  writable: boolean;
  onSaved: () => void;
}) {
  const detail = useAsync<CustomTriggerDetail | null>(
    (signal) => (isNew ? Promise.resolve(null) : api.customTrigger(id, signal)),
    [id, isNew],
  );
  const [text, setText] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<CustomTriggerVerdict | null>(null);
  const [dry, setDry] = useState<CustomTriggerDryRun | null>(null);
  const [busy, setBusy] = useState("");
  const [out, setOut] = useState<{ text: string; tone: "success" | "error" } | null>(null);

  useEffect(() => {
    if (isNew) setText(template);
    else if (detail.data) {
      setText(detail.data.text);
      setVerdict({ ok: true, valid: detail.data.errors.length === 0, errors: detail.data.errors, warnings: detail.data.warnings });
    }
  }, [detail.data, isNew, template]);

  if (detail.loading && !detail.data && !isNew) return <Spinner label={`reading ${id}`} />;
  if (detail.error) return <StatusMessage tone="error">{detail.error}</StatusMessage>;
  if (text === null) return null;
  const saved = isNew ? null : detail.data;
  const dirty = saved ? text !== saved.text : true;

  const run = async (what: "validate" | "save" | "dry") => {
    setBusy(what);
    setOut(null);
    try {
      if (what === "validate") {
        setVerdict(await api.saveCustomTrigger(id, text, true));
      } else if (what === "save") {
        const res = await api.saveCustomTrigger(id, text);
        setVerdict({ ...res, valid: true });
        setOut({ text: res.created ? `Created ${id}.` : `Saved ${id}.`, tone: "success" });
        onSaved();
        detail.reload();
      } else {
        setDry(await api.dryRunCustomTrigger(id));
      }
    } catch (err) {
      setOut({ text: err instanceof Error ? err.message : String(err), tone: "error" });
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="ml-settings__preview">
      <h3 className="ml-settings__h3">
        <code>{id}.yaml</code>
        {isNew ? " (new, not saved)" : dirty ? " (changed, not saved)" : ""}
      </h3>
      <Field label="Trigger file" hint="YAML, as the CLI and the custom-trigger-builder skill write it.">
        {(fid) => (
          <TextArea
            id={fid}
            className="ml-custom__editor"
            rows={22}
            spellCheck={false}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setVerdict(null);
            }}
            disabled={!writable}
          />
        )}
      </Field>
      <div className="ml-settings__foot">
        <Button onClick={() => void run("validate")} disabled={!!busy}>
          {busy === "validate" ? "Validating…" : "Validate"}
        </Button>
        <Button variant="primary" onClick={() => void run("save")} disabled={!writable || !!busy || !dirty}>
          {busy === "save" ? "Saving…" : "Save"}
        </Button>
        <Button onClick={() => void run("dry")} disabled={!writable || !!busy || isNew || dirty}>
          {busy === "dry" ? "Running…" : "Dry run"}
        </Button>
        {dirty && !isNew ? <span className="ml-settings__note">The dry run runs the saved file.</span> : null}
      </div>
      {out ? <StatusMessage tone={out.tone}>{out.text}</StatusMessage> : null}

      {verdict ? (
        verdict.valid === false || verdict.errors.length ? (
          <StatusMessage tone="error">
            Not valid:
            <ul className="ml-settings__list">
              {verdict.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </StatusMessage>
        ) : (
          <StatusMessage tone="success">Valid.</StatusMessage>
        )
      ) : null}
      {verdict?.warnings.length ? (
        <ul className="ml-settings__list" aria-label="warnings">
          {verdict.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}

      {dry ? (
        <div className="ml-custom__dry" aria-label="dry run">
          <p className="ml-settings__hint">
            Dry run: <StatusBadge status={dry.status} />{" "}
            {dry.error ? dry.error : `${dry.events.length} event(s) it would create; nothing was created.`}
            {dry.checkpoint.would_be !== null ? ` Checkpoint would be ${dry.checkpoint.would_be}.` : ""}
          </p>
          {dry.events.length ? (
            <ul className="ml-settings__list">
              {dry.events.map((e) => (
                <li key={e.event_id}>
                  <code>{e.event_id}</code> {e.text}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {saved ? (
        <>
          <p className="ml-settings__note">Checkpoint: {saved.checkpoint ? <code>{saved.checkpoint}</code> : "empty"}</p>
          {saved.runs.length ? (
            <div className="ml-settings__scroll">
              <table className="ml-settings__table" aria-label="runs">
                <thead>
                  <tr>
                    <th>Started</th>
                    <th>Status</th>
                    <th>Events</th>
                    <th>Tasks</th>
                    <th>Tries</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {saved.runs.map((r) => (
                    <tr key={r.run_id}>
                      <td>{r.started_at}</td>
                      <td>
                        <StatusBadge status={r.status} />
                      </td>
                      <td>{r.events}</td>
                      <td>{r.created}</td>
                      <td>{r.attempts}</td>
                      <td>{r.error}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="ml-settings__note">No runs yet.</p>
          )}
        </>
      ) : null}
    </div>
  );
}
