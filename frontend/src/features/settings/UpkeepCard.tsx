import { useState } from "react";
import { api } from "@/api/client";
import { useAsync } from "@/hooks/useAsync";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Field,
  Spinner,
  StatusMessage,
  TextInput,
} from "@/components";
import type { BackupList, ConsolidationPreview, MaintainResult } from "@/api/types";
import { bytes as formatBytes } from "@/lib/format";
import "./SettingsSection.css";

/**
 * Consolidation: what it would do, then — only if you say so — doing it.
 *
 * Consolidation is non-destructive (§2a): every capture is kept and linked to
 * the digest that now stands in for it. That makes it safe, not reversible, and
 * not necessarily what you wanted. So this card never runs on load, shows the
 * plan the engine computed (not an estimate — `consolidation_plan` is the same
 * function the run itself consolidates from), and takes a snapshot of the whole
 * graph before the first write.
 */
export function UpkeepCard({ writable, onChanged }: { writable: boolean; onChanged: () => void }) {
  const [keepDays, setKeepDays] = useState("2");
  const [preview, setPreview] = useState<ConsolidationPreview | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<{ text: string; tone: "info" | "error" | "success" } | null>(null);
  const backups = useAsync<BackupList>((signal) => api.backups(signal), []);

  const keep = Number(keepDays);
  const valid = Number.isFinite(keep) && keep >= 0 && keep <= 365;

  const runPreview = async () => {
    setBusy(true);
    setOut(null);
    try {
      setPreview(await api.consolidatePreview(keep));
    } catch (e) {
      setOut({ text: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const run = async () => {
    setBusy(true);
    try {
      const res: MaintainResult = await api.prune(keep, true);
      if (!res.ok) throw new Error(res.error || "consolidation failed");
      setOut({
        text:
          `Consolidated ${res.days_consolidated ?? 0} day(s), ` +
          `${res.newly_consolidated ?? 0} capture(s) newly folded in. ` +
          (res.backup ? `Snapshot saved to ${res.backup.path}.` : "No snapshot was taken."),
        tone: "success",
      });
      setPreview(null);
      backups.reload();
      onChanged();
    } catch (e) {
      setOut({ text: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const snapshot = async () => {
    setBusy(true);
    setOut(null);
    try {
      const res = await api.backup("manual");
      setOut({ text: `Snapshot saved to ${res.created.path}.`, tone: "success" });
      backups.reload();
    } catch (e) {
      setOut({ text: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const totals = preview?.totals;
  const nothingToDo = Boolean(totals && totals.newly_consolidated === 0);

  return (
    <Card>
      <h2 className="ml-settings__h">Upkeep</h2>
      <p className="ml-settings__hint">
        Folds each closed day&rsquo;s raw captures into one digest, so recall stops at the digest
        instead of walking every turn. Nothing is deleted — the captures stay searchable and linked
        (§2a). It never runs from this page on its own: preview first, then confirm.
      </p>

      <div className="ml-settings__grid">
        <Field
          label="Keep the last N days loose"
          hint="Days more recent than this are left alone, still being lived in."
        >
          {(id) => (
            <TextInput
              id={id}
              type="number"
              min={0}
              max={365}
              value={keepDays}
              onChange={(e) => {
                setKeepDays(e.target.value);
                setPreview(null);       // a plan for another cutoff is not this plan
              }}
            />
          )}
        </Field>
      </div>

      <div className="ml-settings__foot">
        <Button onClick={runPreview} disabled={busy || !valid}>
          {busy && !confirming ? "reading…" : "Preview"}
        </Button>
        <Button
          variant="primary"
          disabled={busy || !writable || !preview || nothingToDo}
          onClick={() => setConfirming(true)}
          title={
            !preview
              ? "Preview it first — the server refuses an unconfirmed run"
              : nothingToDo
                ? "Nothing new to consolidate"
                : undefined
          }
        >
          Consolidate
        </Button>
        <Button onClick={snapshot} disabled={busy || !writable}>
          Snapshot now
        </Button>
      </div>

      {preview ? (
        <div className="ml-settings__preview">
          <h3 className="ml-settings__h3">
            What this would change{" "}
            <Badge tone={nothingToDo ? "neutral" : "warn"}>
              {nothingToDo ? "nothing new" : `${totals?.newly_consolidated} captures`}
            </Badge>
          </h3>
          <p className="ml-settings__hint">
            Everything at or before <code>{preview.cutoff}</code>. {totals?.new_digests} digest(s)
            would be created and {totals?.updated_digests} updated, over{" "}
            {preview.days.length} day(s). {preview.still_loose} capture(s) are loose right now.
          </p>
          {preview.days.length ? (
            <div className="ml-settings__scroll">
              <table className="ml-settings__table">
                <thead>
                  <tr>
                    <th>day</th>
                    <th>action</th>
                    <th>captures</th>
                    <th>newly folded</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.days.map((d) => (
                    <tr key={d.day}>
                      <td>{d.day}</td>
                      <td>{d.action}</td>
                      <td>{d.captures}</td>
                      <td>{d.newly_consolidated}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}

      <h3 className="ml-settings__h3">Snapshots</h3>
      <p className="ml-settings__hint">
        A copy of the whole graph, taken before every consolidation you run from here and kept{" "}
        {backups.data?.keep_days ?? 5} days. Restore one by copying it back over{" "}
        <code>graph.db</code> with the server stopped.
      </p>
      {backups.loading ? <Spinner label="reading snapshots" /> : null}
      {backups.data?.backups.length ? (
        <ul className="ml-settings__list">
          {backups.data.backups.map((b) => (
            <li key={b.name}>
              <code>{b.name}</code> — {formatBytes(b.bytes)}, expires{" "}
              {b.expires.slice(0, 10)}
              {b.expired ? " (expired, removed on the next run)" : ""}
            </li>
          ))}
        </ul>
      ) : backups.data ? (
        <p className="ml-settings__hint">None yet.</p>
      ) : null}

      {out ? <StatusMessage tone={out.tone}>{out.text}</StatusMessage> : null}

      {confirming && preview ? (
        <ConfirmDialog
          title="Consolidate now?"
          danger
          busy={busy}
          confirmLabel={`Consolidate ${totals?.newly_consolidated} captures`}
          body={
            <>
              <p>
                This writes a digest for {preview.days.length} day(s) and stamps{" "}
                {totals?.newly_consolidated} capture(s) as consolidated. No capture is deleted, and
                all of them stay searchable.
              </p>
              <p>
                A snapshot of the whole graph is taken first and kept{" "}
                {preview.backup_keep_days} days, so this is undoable by copying one file back.
              </p>
            </>
          }
          onConfirm={run}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </Card>
  );
}
