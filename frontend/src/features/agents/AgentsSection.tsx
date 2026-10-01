import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { useMeta } from "@/api/MetaContext";
import { usePolling } from "@/hooks/usePolling";
import {
  Badge,
  Button,
  Checkbox,
  EmptyState,
  Menu,
  RichText,
  SearchInput,
  Select,
  Spinner,
  StatusMessage,
  TextArea,
} from "@/components";
import { Section } from "@/features/shell/Section";
import { formatDate, truncate } from "@/lib/format";
import { plainText } from "@/lib/richText";
import type {
  AgentConnector,
  AgentConvState,
  AgentConversation,
  AgentEvent,
  AgentMessage,
} from "@/api/types";
import { exportConversation, formatCost } from "./export";
import type { ExportFormat } from "./export";
import "./AgentsSection.css";

/**
 * Conversations with an agent — Claude or an Ollama model — from the brain
 * itself (docs/agents.md).
 *
 * On the left, the conversations, each with what its last reply is doing: a
 * spinner while it runs, a ? when the agent is waiting on you, a check when it
 * answered. Hidden ones fold away under the active list. On the right, the
 * thread: yours on the right, the agent's on the left, every tool, skill and
 * subagent it used logged under its reply and folded shut.
 *
 * Every turn is one stateless call, so switching the connector or the model
 * between turns is free: the next reply is written by whichever is picked.
 */
export function AgentsSection() {
  const { writable } = useMeta();
  const [params, setParams] = useSearchParams();
  const selected = params.get("c") ?? "";
  const [q, setQ] = useState("");
  const [showHidden, setShowHidden] = useState(false);

  const status = usePolling((signal) => api.agents(signal), { intervalMs: 60_000 });
  const [anyPending, setAnyPending] = useState(false);
  const convs = usePolling((signal) => api.agentConversations(signal), {
    intervalMs: anyPending ? 2_500 : 15_000,
  });
  const all = useMemo(() => convs.data?.conversations ?? [], [convs.data]);
  useEffect(() => setAnyPending(all.some((c) => c.state === "pending")), [all]);

  const needle = q.trim().toLowerCase();
  const matches = (c: AgentConversation) =>
    !needle ||
    c.title.toLowerCase().includes(needle) ||
    plainText(c.last?.text ?? "").toLowerCase().includes(needle);
  const active = all.filter((c) => !c.hidden && matches(c));
  const hidden = all.filter((c) => c.hidden && matches(c));

  const open = (id: string) => setParams(id ? { c: id } : {});
  const current = all.find((c) => c.id === selected);

  return (
    <Section
      title="Agents"
      lead="Talk to Claude or a local Ollama model. Each turn is a fresh call that gets your message, the last few exchanges, and what the brain recalls for it."
      width={1180}
    >
      {convs.error ? <StatusMessage tone="error">{convs.error}</StatusMessage> : null}
      {status.data?.config._problem ? <StatusMessage tone="error">{status.data.config._problem}</StatusMessage> : null}
      <div className="ml-agents">
        <aside className="ml-agents__side" aria-label="agent conversations">
          <div className="ml-agents__tools">
            <SearchInput value={q} onChange={setQ} placeholder="search conversations" aria-label="search conversations" />
            <Button size="sm" variant="primary" onClick={() => open("")} title="Start a new conversation" disabled={!writable}>
              ＋ New
            </Button>
          </div>
          {!convs.data && !convs.error ? <Spinner label="reading conversations" /> : null}
          {convs.data && !active.length && !hidden.length ? (
            <EmptyState title={needle ? "Nothing matches" : "No conversations yet"} hint={needle ? undefined : "Write one on the right."} />
          ) : null}
          <div className="ml-agents__lists">
            <ConvList label="active conversations" items={active} selected={selected} onOpen={open} />
            {hidden.length ? (
              <div className="ml-agents__hidden">
                <button
                  type="button"
                  className="ml-agents__fold"
                  aria-expanded={showHidden}
                  onClick={() => setShowHidden((v) => !v)}
                >
                  <span aria-hidden="true">{showHidden ? "▾" : "▸"}</span> Hidden ({hidden.length})
                </button>
                {showHidden ? <ConvList label="hidden conversations" items={hidden} selected={selected} onOpen={open} /> : null}
              </div>
            ) : null}
          </div>
        </aside>

        <section className="ml-agents__main" aria-label="agent conversation">
          <Thread
            key={selected || "new"}
            id={selected}
            summary={current}
            connectors={status.data?.connectors ?? null}
            defaults={status.data?.config ?? null}
            writable={writable}
            onCreated={(id) => {
              open(id);
              convs.refresh();
            }}
            onChanged={() => convs.refresh()}
          />
        </section>
      </div>
    </Section>
  );
}

function ConvList({
  label,
  items,
  selected,
  onOpen,
}: {
  label: string;
  items: AgentConversation[];
  selected: string;
  onOpen: (id: string) => void;
}) {
  return (
    <ul className="ml-agents__list" aria-label={label}>
      {items.map((c) => (
        <li key={c.id}>
          <button
            type="button"
            className={`ml-agents__conv${c.id === selected ? " is-active" : ""}`}
            aria-current={c.id === selected ? "true" : undefined}
            onClick={() => onOpen(c.id)}
          >
            <span className="ml-agents__conv-top">
              <StateIcon state={c.state ?? "empty"} />
              <span className="ml-agents__name">{c.title || "Untitled"}</span>
              <time className="ml-agents__when" dateTime={c.updated_at} title={c.updated_at}>
                {formatDate(c.updated_at)}
              </time>
            </span>
            <span className="ml-agents__preview">
              {c.last ? `${c.last.role === "user" ? "You: " : ""}${truncate(plainText(c.last.text), 70)}` : c.model}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

const STATE_LABEL: Record<AgentConvState, string> = {
  pending: "waiting for the agent",
  question: "the agent asked you something",
  done: "answered",
  error: "the reply failed",
  empty: "no messages yet",
};

/** ◌ spinning, ? waiting on you, ✓ answered, ✕ failed. */
export function StateIcon({ state }: { state: AgentConvState }) {
  const label = STATE_LABEL[state];
  if (state === "pending")
    return (
      <span className="ml-agents__state ml-agents__state--pending" role="img" aria-label={label} title={label}>
        <span className="ml-spinner__dot" aria-hidden="true" />
      </span>
    );
  const glyph = state === "question" ? "?" : state === "done" ? "✓" : state === "error" ? "✕" : "·";
  return (
    <span className={`ml-agents__state ml-agents__state--${state}`} role="img" aria-label={label} title={label}>
      {glyph}
    </span>
  );
}

interface ThreadProps {
  id: string;
  summary: AgentConversation | undefined;
  connectors: AgentConnector[] | null;
  defaults: { default_connector: string; context_turns: number } | null;
  writable: boolean;
  onCreated: (id: string) => void;
  onChanged: () => void;
}

interface Pick {
  connector: string;
  model: string;
  full_tools: boolean;
  context_turns: number;
}

function Thread({ id, summary, connectors, defaults, writable, onCreated, onChanged }: ThreadProps) {
  const [fast, setFast] = useState(false);
  const thread = usePolling((signal) => api.agentThread(id, signal), {
    // Fast while a reply runs, so its log and its cost move as it works.
    intervalMs: fast || summary?.state === "pending" ? 1_500 : 10_000,
    enabled: !!id,
    key: id,
  });
  const conv = thread.data?.conversation ?? summary;
  const messages = useMemo(() => (thread.data ? thread.data.messages : null), [thread.data]);
  const live = !!messages?.some((m) => m.status === "queued" || m.status === "running");
  // The list polls slower than the thread; tell it when a reply lands.
  const wasLive = useRef(live);
  useEffect(() => setFast(live), [live]);
  useEffect(() => {
    if (wasLive.current && !live) onChanged();
    wasLive.current = live;
  }, [live, onChanged]);

  const [draft, setDraft] = useState<Pick | null>(null);
  const pick: Pick | null = conv
    ? { connector: conv.connector, model: conv.model, full_tools: conv.full_tools, context_turns: conv.context_turns }
    : draft ?? (connectors && defaults ? initialPick(connectors, defaults) : null);
  const [error, setError] = useState<string | null>(null);
  const [find, setFind] = useState("");
  const [finding, setFinding] = useState(false);

  const change = async (next: Partial<Pick>) => {
    if (!pick) return;
    const merged = { ...pick, ...next };
    if (next.connector && next.connector !== pick.connector) {
      merged.model = connectorOf(connectors, next.connector)?.default_model ?? "";
    }
    if (!conv) {
      setDraft(merged);
      return;
    }
    setError(null);
    try {
      await api.saveAgentConversation({ id: conv.id, ...next, ...(next.connector ? { model: merged.model } : {}) });
      thread.refresh();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const setHidden = async (hidden: boolean) => {
    if (!conv) return;
    try {
      await api.saveAgentConversation({ id: conv.id, hidden });
      thread.refresh();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const needle = find.trim().toLowerCase();
  const shown = useMemo(() => {
    if (!messages || !needle) return messages;
    return messages.filter((m) =>
      [m.text, m.error, ...m.events.map((e) => `${e.name} ${e.input ?? ""} ${e.output ?? ""}`)]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [messages, needle]);

  const bottom = useRef<HTMLLIElement>(null);
  const count = messages?.length ?? 0;
  useEffect(() => {
    if (!needle) bottom.current?.scrollIntoView?.({ block: "end" });
  }, [count, needle]);

  const local = connectorOf(connectors, conv?.connector ?? "")?.kind === "ollama";
  const doExport = (f: ExportFormat) => {
    if (conv && messages) exportConversation(f, conv, messages);
  };

  return (
    <>
      <header className="ml-agents__head">
        <span className="ml-agents__title">{conv ? conv.title || "Untitled" : "New conversation"}</span>
        {conv?.hidden ? <Badge tone="neutral">hidden</Badge> : null}
        {conv ? (
          <div className="ml-agents__actions" role="toolbar" aria-label="conversation tools">
            {finding ? (
              <SearchInput value={find} onChange={setFind} placeholder="search in this chat" aria-label="search in this chat" />
            ) : null}
            <Button
              size="sm"
              active={finding}
              onClick={() => {
                setFinding((v) => !v);
                setFind("");
              }}
              title="Search in this chat"
              aria-label="search in chat"
            >
              🔍
            </Button>
            <span
              className="ml-agents__cost"
              aria-label="conversation cost"
              title={`${conv.tokens_in ?? 0} tokens in · ${conv.tokens_out ?? 0} out${conv.cost_estimated ? " · estimated from list prices while a reply runs" : ""}`}
            >
              {formatCost(conv.cost_usd ?? 0, !!conv.cost_estimated, local)}
            </span>
            <Menu
              label="Export"
              groups={[
                {
                  items: [
                    { id: "pdf", label: "PDF", description: "Print view, save as PDF", onSelect: () => doExport("pdf") },
                    { id: "txt", label: "Text", description: "The messages, plain", onSelect: () => doExport("txt") },
                    { id: "json", label: "JSON", description: "Everything, tool logs included", onSelect: () => doExport("json") },
                  ],
                },
              ]}
            />
            <Button size="sm" disabled={!writable} onClick={() => void setHidden(!conv.hidden)}>
              {conv.hidden ? "Unhide" : "Hide"}
            </Button>
          </div>
        ) : null}
      </header>

      <ol className="ml-agents__thread" aria-label="agent messages">
        {thread.error ? <StatusMessage tone="error">{thread.error}</StatusMessage> : null}
        {id && !messages && !thread.error ? <Spinner label="reading messages" /> : null}
        {!id ? <EmptyState title="Start a conversation" hint="Pick an agent and a model below, then write." /> : null}
        {needle && shown ? (
          <li className="ml-agents__found" aria-live="polite">
            {shown.length} of {messages?.length ?? 0} messages match
          </li>
        ) : null}
        {shown?.map((m) => (
          <MessageItem key={m.id} m={m} writable={writable} onRetry={() => void api.retryAgent(m.id).then(() => { thread.refresh(); onChanged(); }, (e: Error) => setError(e.message))} />
        ))}
        <li ref={bottom} aria-hidden="true" className="ml-agents__end" />
      </ol>

      <Composer
        conv={conv}
        pick={pick}
        connectors={connectors}
        writable={writable}
        busy={live}
        error={error}
        onError={setError}
        onPick={(p) => void change(p)}
        onSent={(res) => {
          if (!conv) onCreated(res);
          else {
            thread.refresh();
            onChanged();
          }
        }}
      />
    </>
  );
}

function connectorOf(connectors: AgentConnector[] | null, id: string) {
  return connectors?.find((c) => c.id === id);
}

function initialPick(connectors: AgentConnector[], defaults: { default_connector: string; context_turns: number }): Pick {
  const c = connectorOf(connectors, defaults.default_connector) ?? connectors.find((x) => x.enabled) ?? connectors[0];
  return { connector: c?.id ?? "", model: c?.default_model ?? "", full_tools: false, context_turns: defaults.context_turns };
}

function MessageItem({ m, writable, onRetry }: { m: AgentMessage; writable: boolean; onRetry: () => void }) {
  const side = m.role === "user" ? "out" : "in";
  const running = m.status === "queued" || m.status === "running";
  return (
    <li className={`ml-agents__item ml-agents__item--${side}`} aria-label={m.role === "user" ? "your message" : "agent reply"}>
      {m.role === "agent" && m.events.length ? <EventLog events={m.events} running={running} /> : null}
      {m.role === "agent" && running ? (
        <div className="ml-agents__bubble ml-agents__bubble--wait">
          <Spinner label="the agent is working" /> {m.status === "queued" ? "queued…" : "working…"}
        </div>
      ) : m.status === "error" ? (
        <div className="ml-agents__bubble ml-agents__bubble--error">
          <StatusMessage tone="error">{m.error || "the reply failed"}</StatusMessage>
          <Button size="sm" disabled={!writable} onClick={onRetry}>
            Retry
          </Button>
        </div>
      ) : (
        <div className="ml-agents__bubble">
          <RichText text={m.text} />
        </div>
      )}
      <div className="ml-agents__meta">
        {m.role === "agent" ? (
          <>
            <StateIcon state={running ? "pending" : m.status === "question" ? "question" : m.status === "error" ? "error" : "done"} />
            <span title={m.connector}>{m.model}</span>
            {m.full_tools ? <Badge tone="warn">tools on</Badge> : null}
            {m.cost_usd !== null ? (
              <span title={`${m.tokens_in} in · ${m.tokens_out} out`}>{formatCost(m.cost_usd, m.cost_estimated, m.cost_usd === 0)}</span>
            ) : null}
            {m.context.length ? <span title={m.context.join("\n")}>{m.context.length} recalled</span> : null}
          </>
        ) : null}
        <time dateTime={m.at} title={m.at}>
          {formatDate(m.at)}
        </time>
      </div>
    </li>
  );
}

const KIND_ICON: Record<string, string> = { tool: "🔧", skill: "🧩", agent: "🤖", mcp: "🔌", thinking: "💭", text: "💬", note: "ℹ" };

/** Every tool, skill and subagent the reply used, folded shut by default. */
function EventLog({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const counts = new Map<string, number>();
  for (const e of events) counts.set(e.kind, (counts.get(e.kind) ?? 0) + 1);
  const summary = [...counts.entries()].map(([k, n]) => `${KIND_ICON[k] ?? "·"} ${n} ${k}${n > 1 ? "s" : ""}`).join(" · ");
  return (
    <details className="ml-agents__log">
      <summary>
        {summary}
        {running ? " …" : ""}
      </summary>
      <ul aria-label="agent activity">
        {events.map((e, i) => (
          <li key={e.id || i} className={e.parent ? "is-nested" : ""}>
            <details>
              <summary>
                <span aria-hidden="true">{KIND_ICON[e.kind] ?? "·"}</span> <b>{e.name}</b>
                {e.tool && e.tool !== e.name ? <span className="ml-agents__muted"> {e.tool}</span> : null}
                {e.error ? <Badge tone="danger">failed</Badge> : null}
              </summary>
              {e.input ? <pre className="ml-agents__pre">{e.input}</pre> : null}
              {e.output ? <pre className="ml-agents__pre">{e.output}</pre> : null}
            </details>
          </li>
        ))}
      </ul>
    </details>
  );
}

interface ComposerProps {
  conv: AgentConversation | undefined;
  pick: Pick | null;
  connectors: AgentConnector[] | null;
  writable: boolean;
  busy: boolean;
  error: string | null;
  onError: (e: string | null) => void;
  onPick: (p: Partial<Pick>) => void;
  onSent: (conversationId: string) => void;
}

const TURN_OPTIONS = Array.from({ length: 11 }, (_, n) => ({
  value: String(n),
  label: n === 0 ? "no history" : `last ${n} exchange${n > 1 ? "s" : ""}`,
}));

function Composer({ conv, pick, connectors, writable, busy, error, onError, onPick, onSent }: ComposerProps) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const c = pick ? connectorOf(connectors, pick.connector) : undefined;
  const models = c ? (c.models.includes(pick!.model) || !pick!.model ? c.models : [pick!.model, ...c.models]) : [];
  const usable = !!c && c.enabled && !!pick?.model;
  const canSend = writable && usable && !sending && !!text.trim();

  const send = async () => {
    if (!canSend || !pick) return;
    setSending(true);
    onError(null);
    try {
      const res = await api.sendAgent({
        text: text.trim(),
        conversation: conv?.id,
        connector: pick.connector,
        model: pick.model,
        full_tools: pick.full_tools,
        context_turns: pick.context_turns,
      });
      setText("");
      onSent(res.conversation.id);
      box.current?.focus();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };

  const why = !writable
    ? "This server is read-only (--no-write)."
    : connectors && !connectors.length
      ? "No connectors. Add one in Settings → Agents."
      : c && !c.enabled
        ? `The ${c.name} connector is turned off.`
        : c?.problem || (c && !pick?.model ? `Pick a model for ${c.name}.` : "");

  return (
    <div className="ml-agents__compose">
      {why ? (
        <p className="ml-agents__hint">
          {why} {writable ? <Link to="/settings">Settings</Link> : null}
        </p>
      ) : null}
      {error ? <StatusMessage tone="error">{error}</StatusMessage> : null}
      {pick && connectors ? (
        <div className="ml-agents__pickers">
          <Select
            aria-label="agent"
            value={pick.connector}
            options={connectors.map((x) => ({ value: x.id, label: `${x.name}${x.enabled ? "" : " (off)"}` }))}
            onChange={(e) => onPick({ connector: e.target.value })}
            disabled={!writable}
          />
          <Select
            aria-label="model"
            value={pick.model}
            options={models.length ? models.map((m) => ({ value: m, label: m })) : [{ value: "", label: "no models" }]}
            onChange={(e) => onPick({ model: e.target.value })}
            disabled={!writable || !models.length}
          />
          <Select
            aria-label="context"
            title="Earlier exchanges each turn carries, besides what the brain recalls"
            value={String(pick.context_turns)}
            options={TURN_OPTIONS}
            onChange={(e) => onPick({ context_turns: Number(e.target.value) })}
            disabled={!writable}
          />
          <span title="Off: the agent can read and look things up, and changes nothing. On: it may act (edit, run commands, write to the brain).">
            {/* The box is the label: ticked means read-only. It once ticked for
                "tools on" while saying "Read-only" unticked, which read as the opposite. */}
            <Checkbox
              checked={!pick.full_tools}
              onChange={(readOnly) => onPick({ full_tools: !readOnly })}
              label="Read-only"
              disabled={!writable}
            />
          </span>
        </div>
      ) : (
        <Spinner label="reading connectors" />
      )}
      <div className="ml-agents__compose-row">
        <TextArea
          ref={box}
          rows={2}
          value={text}
          disabled={!writable}
          placeholder={busy ? "The agent is still answering. You can queue another message." : "Write to the agent. Enter sends, Shift+Enter for a new line."}
          aria-label="message to the agent"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        <Button variant="primary" disabled={!canSend} onClick={() => void send()}>
          {sending ? "sending…" : "Send"}
        </Button>
      </div>
    </div>
  );
}
