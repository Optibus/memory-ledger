import { useEffect, useState } from "react";
import { api, documentFileUrl } from "@/api/client";
import type { AgentDocument } from "@/api/types";
import { Button } from "./Button";
import { Modal } from "./Modal";
import { Spinner, StatusMessage } from "./Feedback";
import { Markdown } from "./Markdown";
import "./DocCard.css";

/**
 * A document an agent wrote: collapsed it is only its name and an Expand
 * button; expanded it overlays the interface, closing with the cross at the
 * top right, Escape, or a click outside it. The file is read only on expand.
 *
 * HTML and PDF show in a sandboxed frame (its script cannot reach this app's
 * API); Markdown is rendered; anything else is shown as text.
 */
export function DocCard({ name, path }: { name: string; path: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="ml-doc">
        <span className="ml-doc__icon" aria-hidden="true">▤</span>
        <span className="ml-doc__name" title={path}>{name}</span>
        <Button size="sm" onClick={() => setOpen(true)} aria-label={`Expand ${name}`}>
          Expand
        </Button>
      </div>
      {open ? (
        <Modal title={name} subtitle={path} width="min(1100px, 96vw)" onClose={() => setOpen(false)}>
          <DocBody path={path} />
        </Modal>
      ) : null}
    </>
  );
}

function DocBody({ path }: { path: string }) {
  const [doc, setDoc] = useState<AgentDocument | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const ctl = new AbortController();
    api.document(path, ctl.signal).then(setDoc, (e) => {
      if (!ctl.signal.aborted) setError(e instanceof Error ? e.message : String(e));
    });
    return () => ctl.abort();
  }, [path]);

  if (error) return <StatusMessage tone="error">{error}</StatusMessage>;
  if (!doc) return <Spinner label="reading the document" />;
  if (doc.kind === "html" || doc.kind === "pdf")
    return (
      <iframe
        className="ml-doc__frame"
        title={doc.name}
        src={documentFileUrl(path)}
        sandbox={doc.kind === "html" ? "allow-scripts" : undefined}
      />
    );
  if (doc.kind === "md") return <Markdown text={doc.text ?? ""} />;
  return <pre className="ml-doc__text">{doc.text}</pre>;
}
