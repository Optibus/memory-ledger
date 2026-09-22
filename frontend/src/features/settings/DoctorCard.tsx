import { useState } from "react";
import { api } from "@/api/client";
import { Badge, Button, Card, StatusMessage } from "@/components";
import type { DoctorReport } from "@/api/types";
import "./SettingsSection.css";

/**
 * The health check, in the browser.
 *
 * `doctor` exists because zero-touch install removed the one moment that proved
 * the ledger was wired up: a broken hook, a stray $MEMORY_LEDGER_OFF or a
 * forgotten `.disabled` marker can sit invisibly for weeks. Reaching that answer
 * used to mean knowing the CLI existed, which is precisely the wrong
 * prerequisite for "is this thing on?".
 *
 * Run on demand, not on load — it stats the hooks file and probes the mirror,
 * and a page that does that every time you open Settings is a page that makes
 * its own diagnosis slower.
 */
export function DoctorCard() {
  const [report, setReport] = useState<DoctorReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setReport(await api.doctor());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <h2 className="ml-settings__h">
        Health{" "}
        {report ? (
          <Badge tone={report.healthy ? "success" : "danger"}>
            {report.healthy ? "healthy" : `${report.problems.length} problem(s)`}
          </Badge>
        ) : null}
      </h2>
      <p className="ml-settings__hint">
        The same check <code>doctor</code> runs from the terminal: where the brain is, whether the
        hooks point at scripts that exist, what the config resolves to and whether the mirror
        answers. Being turned off is a choice, not a fault, and never counts as a problem.
      </p>

      <div className="ml-settings__foot">
        <Button onClick={run} disabled={busy}>
          {busy ? "checking…" : report ? "Run again" : "Run the health check"}
        </Button>
      </div>

      {err ? <StatusMessage tone="error">{err}</StatusMessage> : null}

      {report ? (
        <>
          {report.problems.length ? (
            <StatusMessage tone="error">
              <ul className="ml-settings__list">
                {report.problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </StatusMessage>
          ) : (
            <StatusMessage tone="success">Nothing actionable is wrong.</StatusMessage>
          )}
          <div className="ml-settings__scroll">
            {(["status", "paths", "config", "content", "hooks", "mirror"] as const).map((name) => (
              <section key={name} className="ml-settings__doctor">
                <h3 className="ml-settings__h3">{name}</h3>
                <dl className="ml-settings__kv">
                  {Object.entries(report[name] ?? {}).map(([k, v]) => (
                    <FieldRow key={k} name={k} value={v} />
                  ))}
                </dl>
              </section>
            ))}
          </div>
        </>
      ) : null}
    </Card>
  );
}

/** One line of a doctor section. The shapes are the engine's, not this page's,
 *  so anything structured is shown as JSON rather than guessed at. */
function FieldRow({ name, value }: { name: string; value: unknown }) {
  const text =
    value === null || value === undefined
      ? "—"
      : typeof value === "boolean"
        ? value ? "yes" : "no"
        : typeof value === "object"
          ? JSON.stringify(value)
          : String(value);
  return (
    <>
      <dt>{name.replace(/_/g, " ")}</dt>
      <dd>{text}</dd>
    </>
  );
}
