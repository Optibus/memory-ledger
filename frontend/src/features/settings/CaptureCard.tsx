import { useState } from "react";
import { api } from "@/api/client";
import { Badge, Button, Card, StatusMessage } from "@/components";
import type { CaptureState } from "@/api/types";
import "./SettingsSection.css";

/**
 * Is the brain recording?
 *
 * The one question `doctor` was written to answer, and until now the page could
 * not. Capture is off when `<home>/.disabled` exists OR $MEMORY_LEDGER_OFF is
 * set in the server's shell (CONVENTIONS §2d) — and those are not equal: the
 * env var outranks the marker, so with it set this card explains rather than
 * offering a button that cannot work.
 */
export function CaptureCard({
  capture,
  writable,
  onChanged,
}: {
  capture: CaptureState;
  writable: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const { enabled, env_off: envOff } = capture;

  const toggle = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.setCapture(!enabled);
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
        Capture{" "}
        <Badge tone={enabled ? "success" : "warn"}>{enabled ? "recording" : "off"}</Badge>
      </h2>
      <p className="ml-settings__hint">
        When it is on, every prompt and a summary of every answer is stored automatically, in
        Python and SQL, with no model call (§0). Turning it off stops the hooks and the bootstrap —
        it deletes nothing, and turning it back on resumes from where you left.
      </p>

      {envOff ? (
        <StatusMessage tone="info">
          <code>$MEMORY_LEDGER_OFF</code> is set in the shell that started this server, and it
          outranks the marker file. Capture stays off here until that variable is unset where it is
          exported — an rc file, a cron, or a launchd agent — and the server restarts.
        </StatusMessage>
      ) : null}

      <dl className="ml-settings__kv">
        <dt>decided by</dt>
        <dd>
          {capture.source === "env"
            ? "$MEMORY_LEDGER_OFF in the environment"
            : capture.source === "marker"
              ? "the .disabled marker file"
              : "nothing — the default is on"}
        </dd>
        <dt>marker</dt>
        <dd>
          <code>{capture.marker}</code> {capture.marker_present ? "(present)" : "(absent)"}
        </dd>
      </dl>

      <div className="ml-settings__foot">
        <Button
          variant={enabled ? undefined : "primary"}
          disabled={busy || !writable || envOff}
          onClick={toggle}
        >
          {busy ? "working…" : enabled ? "Turn capture off" : "Turn capture on"}
        </Button>
        {!writable ? <span className="ml-settings__note">read-only server</span> : null}
      </div>
      {err ? <StatusMessage tone="error">{err}</StatusMessage> : null}
    </Card>
  );
}
