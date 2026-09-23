import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "@/api/client";
import { useAsync } from "@/hooks/useAsync";
import { Badge, Button, Card, Checkbox, Field, Spinner, StatusMessage, TextInput } from "@/components";
import type { BadgeTone } from "@/components";
import type {
  TriggerChannelRow,
  TriggerChannels,
  TriggerCheck,
  TriggerConfig,
  TriggerSecret,
  TriggerService,
  TriggerStatus,
  TriggerWatcher,
} from "@/api/types";
import "./SettingsSection.css";

type Out = { text: string; tone: "info" | "error" | "success" } | null;

/**
 * Summoning the brain from Slack and Jira (docs/trigger.md).
 *
 * Everything here writes `<home>/trigger.json` through the server, which
 * restarts its watchers on the new file — so a switch flipped here is a watcher
 * started or stopped, not a preference that waits for a restart.
 *
 * Tokens are WRITE-ONLY. The server says whether one is set and where from,
 * never what it is, so a blank token field means "keep what is stored", and
 * removing one is its own explicit button. A token set in the server's
 * environment outranks the file, and the field says so instead of pretending a
 * save would change it.
 */
export function TriggerCard({ writable }: { writable: boolean }) {
  const status = useAsync<TriggerStatus>((signal) => api.trigger(signal), []);

  if (status.loading && !status.data) return <Spinner label="reading triggers" />;
  if (status.error) return <StatusMessage tone="error">{status.error}</StatusMessage>;
  if (!status.data) return null;
  const { config, runtime, watching } = status.data;
  const reload = () => status.reload();

  return (
    <Card>
      <h2 className="ml-settings__h">
        Triggers{" "}
        <Badge tone={runtime.running ? "success" : "neutral"}>
          {runtime.running ? "listening" : "off"}
        </Badge>
      </h2>
      <p className="ml-settings__hint">
        Mention the brain in a Slack thread, or in a Jira comment that mentions you with the marker,
        and a headless Claude Code session answers there as you, signed as your second brain. Only
        conversations you opened with your own mention accept anyone else&rsquo;s. Saved to{" "}
        <code>{config.path}</code>, mode 600.
      </p>

      {!watching ? (
        <StatusMessage tone="info">
          This server was started with <code>--no-trigger</code> or <code>--no-write</code>, so it
          runs no watchers. What you save here is kept for the next start without them.
        </StatusMessage>
      ) : null}
      {config.problem ? <StatusMessage tone="error">{config.problem}</StatusMessage> : null}

      <MasterSwitch config={config} writable={writable} onSaved={reload} />

      <SlackPanel
        config={config}
        channels={status.data.channels}
        watcher={runtime.slack}
        writable={writable}
        onSaved={reload}
      />
      <JiraPanel config={config} watcher={runtime.jira} writable={writable} onSaved={reload} />
      <ClaudePanel config={config} writable={writable} onSaved={reload} />
      <Recent status={status.data} />

      {!writable ? <span className="ml-settings__note">read-only server</span> : null}
    </Card>
  );
}

// ---------------------------------------------------------------- saving
function useSave(onSaved: () => void) {
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<Out>(null);
  const save = async (patch: Record<string, unknown>, done = "Saved.") => {
    setBusy(true);
    setOut(null);
    try {
      await api.saveTrigger(patch);
      setOut({ text: done, tone: "success" });
      onSaved();
      return true;
    } catch (err) {
      setOut({ text: err instanceof Error ? err.message : String(err), tone: "error" });
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, out, save, setOut };
}

function MasterSwitch({
  config,
  writable,
  onSaved,
}: {
  config: TriggerConfig;
  writable: boolean;
  onSaved: () => void;
}) {
  const { busy, out, save } = useSave(onSaved);
  const [signature, setSignature] = useState(config.signature_template);
  const [notEnabled, setNotEnabled] = useState(config.refusals.not_enabled);
  const [policy, setPolicy] = useState(config.refusals.policy);
  useEffect(() => {
    setSignature(config.signature_template);
    setNotEnabled(config.refusals.not_enabled);
    setPolicy(config.refusals.policy);
  }, [config.signature_template, config.refusals.not_enabled, config.refusals.policy]);

  return (
    <>
      <div className="ml-settings__switch">
        <Checkbox
          label="Listen for mentions"
          checked={config.stored_enabled}
          disabled={!writable || busy}
          onChange={(v) => save({ enabled: v }, v ? "Triggers on." : "Triggers off.")}
        />
        <p className="ml-settings__hint ml-settings__hint--switch">
          The master switch. Off, no service listens, whatever its own switch says.
        </p>
      </div>

      <h3 className="ml-settings__h3">Replies</h3>
      <Field
        label="Signature"
        hint={
          <>
            The last line of every reply, never translated. <code>{"{owner}"}</code> is your
            name; now it reads <em>{config.signature.replace(/^_|_$/g, "")}</em>. It is also how
            the brain knows its own words, so keep it distinctive.
          </>
        }
      >
        {(id) => <TextInput id={id} value={signature} onChange={(e) => setSignature(e.target.value)} />}
      </Field>
      <div className="ml-settings__grid">
        <Field
          label="Refusal: not enabled here"
          hint="For a mention in a conversation you have not opened. Sent by the brain itself, once per conversation."
        >
          {(id) => <TextInput id={id} value={notEnabled} onChange={(e) => setNotEnabled(e.target.value)} />}
        </Field>
        <Field
          label="Refusal: against a rule"
          hint={
            <>
              For a request that breaks one of your rules or guardrails. The session sends it when
              it declines. <code>{"{reason}"}</code> is the one-line why.
            </>
          }
        >
          {(id) => <TextInput id={id} value={policy} onChange={(e) => setPolicy(e.target.value)} />}
        </Field>
      </div>
      <div className="ml-settings__switch">
        <Checkbox
          label="Reply in the sender's language"
          checked={config.translate_notices}
          disabled={!writable || busy}
          onChange={(v) => save({ translate_notices: v }, v ? "Notices will be translated." : "Notices go out as written.")}
        />
        <p className="ml-settings__hint ml-settings__hint--switch">
          The brain&rsquo;s answers always follow the language of the message. This is for the two
          notices above: on, each is rewritten in the sender&rsquo;s language (Hebrew for someone who
          wrote in Hebrew, English for the same person writing in English), which is a small model
          call. Off, they
          go out exactly as written, with none.
        </p>
      </div>
      <div className="ml-settings__foot">
        <Button
          disabled={!writable || busy}
          onClick={() => save({ signature, refusals: { not_enabled: notEnabled, policy } })}
        >
          Save replies
        </Button>
      </div>
      {out ? <StatusMessage tone={out.tone}>{out.text}</StatusMessage> : null}
    </>
  );
}

// ---------------------------------------------------------------- panels
const WATCH_TONE: Record<string, BadgeTone> = {
  connected: "success",
  polling: "success",
  connecting: "accent",
  reconnecting: "warn",
  misconfigured: "danger",
  erroring: "danger",
  unavailable: "danger",
  off: "neutral",
};

function Panel({
  title,
  service,
  watcher,
  switchLabel,
  switchHint,
  enabled,
  writable,
  busy,
  onToggle,
  onSave,
  out,
  children,
  after,
}: {
  title: string;
  service: TriggerService;
  watcher?: TriggerWatcher;
  switchLabel?: string;
  switchHint?: string;
  enabled?: boolean;
  writable: boolean;
  busy: boolean;
  onToggle?: (v: boolean) => void;
  onSave: () => void;
  out: Out;
  children: ReactNode;
  /** Below the fields: something with its own saves, like the channel list. */
  after?: ReactNode;
}) {
  const [check, setCheck] = useState<TriggerCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkErr, setCheckErr] = useState<string | null>(null);

  const runCheck = async () => {
    setChecking(true);
    setCheck(null);
    setCheckErr(null);
    try {
      setCheck(await api.checkTrigger(service));
    } catch (err) {
      setCheckErr(err instanceof Error ? err.message : String(err));
    } finally {
      setChecking(false);
    }
  };

  return (
    <section className="ml-settings__preview" aria-label={title}>
      <h3 className="ml-settings__h3">
        {title}
        {watcher ? (
          <Badge tone={WATCH_TONE[watcher.state] ?? "neutral"} title={watcher.at}>
            {watcher.state}
          </Badge>
        ) : null}
      </h3>
      {watcher?.last_error ? <StatusMessage tone="error">{watcher.last_error}</StatusMessage> : null}

      {switchLabel && onToggle ? (
        <div className="ml-settings__switch">
          <Checkbox
            label={switchLabel}
            checked={!!enabled}
            disabled={!writable || busy}
            onChange={onToggle}
          />
          {switchHint ? (
            <p className="ml-settings__hint ml-settings__hint--switch">{switchHint}</p>
          ) : null}
        </div>
      ) : null}

      <div className="ml-settings__grid">{children}</div>
      {after}

      <div className="ml-settings__foot">
        <Button variant="primary" disabled={!writable || busy} onClick={onSave}>
          {busy ? "saving…" : `Save ${title}`}
        </Button>
        <Button disabled={!writable || checking} onClick={runCheck}>
          {checking ? "checking…" : `Check ${title}`}
        </Button>
      </div>
      {out ? <StatusMessage tone={out.tone}>{out.text}</StatusMessage> : null}
      {checkErr ? <StatusMessage tone="error">{checkErr}</StatusMessage> : null}
      {check ? <CheckResult check={check} title={title} /> : null}
    </section>
  );
}

function CheckResult({ check, title }: { check: TriggerCheck; title: string }) {
  return (
    <StatusMessage tone={check.ready ? "success" : "error"}>
      <strong>{check.ready ? `${title} is ready.` : `${title} is not ready.`}</strong>
      <ul className="ml-settings__list">
        {check.problems.map((p) => (
          <li key={`p-${p}`}>{p}</li>
        ))}
        {check.notes.map((n) => (
          <li key={`n-${n}`}>{n}</li>
        ))}
      </ul>
    </StatusMessage>
  );
}

/**
 * A token field. Blank keeps the stored one; typing replaces it; "Remove"
 * deletes it. Set in the environment, it cannot be changed from here at all,
 * and the field is disabled rather than silently ineffective.
 */
function SecretField({
  label,
  secret,
  value,
  onChange,
  onRemove,
  writable,
  placeholder,
  hint,
}: {
  label: string;
  secret?: TriggerSecret;
  value: string;
  onChange: (v: string) => void;
  onRemove: () => void;
  writable: boolean;
  placeholder: string;
  hint: string;
}) {
  const fromEnv = secret?.source === "env";
  const state = fromEnv
    ? `Set by $${secret?.env_var} in the server's environment, which outranks this page.`
    : secret?.stored
      ? "One is stored. Leave blank to keep it."
      : "None stored.";
  return (
    <Field
      label={
        <>
          {label}{" "}
          <Badge tone={secret?.set ? "success" : "neutral"}>
            {fromEnv ? "env" : secret?.stored ? "stored" : "not set"}
          </Badge>
        </>
      }
      hint={`${hint} ${state}`}
    >
      {(id) => (
        <div className="ml-settings__foot">
          <TextInput
            id={id}
            type="password"
            autoComplete="new-password"
            placeholder={fromEnv ? "from the environment" : placeholder}
            value={value}
            disabled={fromEnv || !writable}
            onChange={(e) => onChange(e.target.value)}
          />
          {secret?.stored && !fromEnv ? (
            <Button size="sm" disabled={!writable} onClick={onRemove}>
              Remove
            </Button>
          ) : null}
        </div>
      )}
    </Field>
  );
}

function txt(label: string, value: string, set: (v: string) => void, hint: string, placeholder = "") {
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <TextInput id={id} value={value} placeholder={placeholder} onChange={(e) => set(e.target.value)} />
      )}
    </Field>
  );
}

function num(label: string, value: string, set: (v: string) => void, hint: string, min: number, max: number) {
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <TextInput
          id={id}
          type="number"
          min={min}
          max={max}
          value={value}
          onChange={(e) => set(e.target.value)}
        />
      )}
    </Field>
  );
}

function SlackPanel({
  config,
  channels,
  watcher,
  writable,
  onSaved,
}: {
  config: TriggerConfig;
  channels: TriggerChannels;
  watcher: TriggerWatcher;
  writable: boolean;
  onSaved: () => void;
}) {
  const s = config.slack;
  const { busy, out, save } = useSave(onSaved);
  const [owner, setOwner] = useState(s.owner_user_id ?? "");
  const [lookback, setLookback] = useState(String(s.thread_lookback_days ?? 7));
  const [appToken, setAppToken] = useState("");
  const [botToken, setBotToken] = useState("");
  const [userToken, setUserToken] = useState("");
  useEffect(() => {
    setOwner(s.owner_user_id ?? "");
    setLookback(String(s.thread_lookback_days ?? 7));
  }, [s.owner_user_id, s.thread_lookback_days]);

  const submit = async () => {
    const slack: Record<string, unknown> = {
      owner_user_id: owner,
      thread_lookback_days: Number(lookback),
    };
    // Only a typed token travels. Blank means keep, never "set to empty".
    if (appToken) slack.app_token = appToken;
    if (botToken) slack.bot_token = botToken;
    if (userToken) slack.user_token = userToken;
    if (await save({ slack }, "Slack saved.")) {
      setAppToken("");
      setBotToken("");
      setUserToken("");
    }
  };
  const remove = (name: string) => save({ clear: [name] }, `Removed ${name}.`);

  return (
    <Panel
      title="Slack"
      service="slack"
      watcher={watcher}
      switchLabel="Listen on Slack"
      switchHint="A Socket Mode connection: outbound only, no public endpoint. The app needs no chat:write; replies go out as you."
      enabled={s.enabled}
      writable={writable}
      busy={busy}
      onToggle={(v) => save({ slack: { enabled: v } }, v ? "Slack on." : "Slack off.")}
      onSave={submit}
      out={out}
      after={<ChannelsCard channels={channels} writable={writable} onChanged={onSaved} />}
    >
      {txt("Your Slack user id", owner, setOwner, "Your own U… id. Your mention opens a thread; nobody else's can.", "U0123ABCD")}
      {num("Thread look-back (days)", lookback, setLookback, "How far back catch-up looks for threads with new replies.", 0, 90)}
      <SecretField
        label="App token"
        secret={config.secrets["slack.app_token"]}
        value={appToken}
        onChange={setAppToken}
        onRemove={() => remove("slack.app_token")}
        writable={writable}
        placeholder="xapp-…"
        hint="App-level token with connections:write. Opens the socket."
      />
      <SecretField
        label="Bot token"
        secret={config.secrets["slack.bot_token"]}
        value={botToken}
        onChange={setBotToken}
        onRemove={() => remove("slack.bot_token")}
        writable={writable}
        placeholder="xoxb-…"
        hint="Reads mentions and history. Never writes."
      />
      <SecretField
        label="User token"
        secret={config.secrets["slack.user_token"]}
        value={userToken}
        onChange={setUserToken}
        onRemove={() => remove("slack.user_token")}
        writable={writable}
        placeholder="xoxp-…"
        hint="Optional. Posts refusal notices as you, directly. Without it they go out through your Slack MCP instead, as you all the same, in a short headless session."
      />
    </Panel>
  );
}

function JiraPanel({
  config,
  watcher,
  writable,
  onSaved,
}: {
  config: TriggerConfig;
  watcher: TriggerWatcher;
  writable: boolean;
  onSaved: () => void;
}) {
  const j = config.jira;
  const { busy, out, save } = useSave(onSaved);
  const [baseUrl, setBaseUrl] = useState(j.base_url ?? "");
  const [email, setEmail] = useState(j.email ?? "");
  const [account, setAccount] = useState(j.owner_account_id ?? "");
  const [marker, setMarker] = useState(j.marker ?? "");
  const [poll, setPoll] = useState(String(j.poll_seconds ?? 60));
  const [jql, setJql] = useState(j.jql ?? "");
  const [catchUp, setCatchUp] = useState(String(j.max_catchup_hours ?? 72));
  const [token, setToken] = useState("");
  useEffect(() => {
    setBaseUrl(j.base_url ?? "");
    setEmail(j.email ?? "");
    setAccount(j.owner_account_id ?? "");
    setMarker(j.marker ?? "");
    setPoll(String(j.poll_seconds ?? 60));
    setJql(j.jql ?? "");
    setCatchUp(String(j.max_catchup_hours ?? 72));
  }, [j.base_url, j.email, j.owner_account_id, j.marker, j.poll_seconds, j.jql, j.max_catchup_hours]);

  const submit = async () => {
    const jira: Record<string, unknown> = {
      base_url: baseUrl,
      email,
      owner_account_id: account,
      marker,
      poll_seconds: Number(poll),
      jql,
      max_catchup_hours: Number(catchUp),
    };
    if (token) jira.token = token;
    if (await save({ jira }, "Jira saved.")) setToken("");
  };

  return (
    <Panel
      title="Jira"
      service="jira"
      watcher={watcher}
      switchLabel="Poll Jira"
      switchHint="Reads new comments on a timer with your API token. A comment counts when it mentions you AND carries the marker."
      enabled={j.enabled}
      writable={writable}
      busy={busy}
      onToggle={(v) => save({ jira: { enabled: v } }, v ? "Jira polling on." : "Jira polling off.")}
      onSave={submit}
      out={out}
    >
      {txt("Site URL", baseUrl, setBaseUrl, "Your Atlassian site.", "https://your-site.atlassian.net")}
      {txt("Email", email, setEmail, "The account the API token belongs to.", "you@example.com")}
      {txt("Your account id", account, setAccount, "Left empty, it is looked up from the token.", "optional")}
      {txt("Marker", marker, setMarker, "The text that says a comment is for the brain. Case and accents do not matter.", "@brain")}
      {num("Poll every (seconds)", poll, setPoll, "15 to 3600.", 15, 3600)}
      {num("Catch-up limit (hours)", catchUp, setCatchUp, "After a long gap, look back no further than this.", 1, 720)}
      {txt("Extra JQL", jql, setJql, "ANDed onto the search, to narrow which issues are read.", "project in (PROJ, OPS)")}
      <SecretField
        label="API token"
        secret={config.secrets["jira.token"]}
        value={token}
        onChange={setToken}
        onRemove={() => save({ clear: ["jira.token"] }, "Removed jira.token.")}
        writable={writable}
        placeholder="Atlassian API token"
        hint="From id.atlassian.com, for the email above."
      />
    </Panel>
  );
}

function ClaudePanel({
  config,
  writable,
  onSaved,
}: {
  config: TriggerConfig;
  writable: boolean;
  onSaved: () => void;
}) {
  const c = config.claude;
  const { busy, out, save } = useSave(onSaved);
  const [bin, setBin] = useState(c.bin ?? "claude");
  const [noticeModel, setNoticeModel] = useState(c.notice_model ?? "haiku");
  const [mcp, setMcp] = useState(c.mcp_config ?? "");
  const [strict, setStrict] = useState(!!c.strict_mcp_config);
  const [timeout, setTimeoutS] = useState(String(c.timeout_s ?? 900));
  const [concurrent, setConcurrent] = useState(String(c.max_concurrent ?? 3));
  useEffect(() => {
    setBin(c.bin ?? "claude");
    setNoticeModel(c.notice_model ?? "haiku");
    setMcp(c.mcp_config ?? "");
    setStrict(!!c.strict_mcp_config);
    setTimeoutS(String(c.timeout_s ?? 900));
    setConcurrent(String(c.max_concurrent ?? 3));
  }, [c.bin, c.notice_model, c.mcp_config, c.strict_mcp_config, c.timeout_s, c.max_concurrent]);

  return (
    <Panel
      title="Claude"
      service="claude"
      writable={writable}
      busy={busy}
      onSave={() =>
        save(
          {
            claude: {
              bin,
              notice_model: noticeModel,
              mcp_config: mcp,
              strict_mcp_config: strict,
              timeout_s: Number(timeout),
              max_concurrent: Number(concurrent),
            },
          },
          "Claude saved.",
        )
      }
      out={out}
    >
      {txt("Command", bin, setBin, "The claude binary each mention runs as `claude -p`.", "claude")}
      {txt("MCP config", mcp, setMcp, "The MCP servers the session may use: your Slack, Jira, Gmail, Drive. Empty uses what Claude Code loads anyway.", "~/.second_brain/trigger-mcp.json")}
      {txt("Notice model", noticeModel, setNoticeModel, "The small model that translates a refusal notice. Only used with a Slack user token or on Jira; without a token the notice session does it.", "haiku")}
      {num("Timeout (seconds)", timeout, setTimeoutS, "A session running longer is killed and marked timeout.", 30, 7200)}
      {num("At once", concurrent, setConcurrent, "How many mentions may run at the same time.", 1, 16)}
      <div className="ml-settings__switch">
        <Checkbox
          label="Only these MCP servers"
          checked={strict}
          disabled={!writable || !mcp}
          onChange={setStrict}
        />
        <p className="ml-settings__hint ml-settings__hint--switch">
          --strict-mcp-config: ignore every other MCP server Claude Code would load.
        </p>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- channels
/**
 * The channels open to everyone's mentions, kept in the brain's database.
 *
 * Collapsed by default: it can grow long, and most visits to Settings are not
 * about it. A closed channel is not deleted. It moves to "Closed", with who
 * closed it and when, and can be reopened from there.
 */
export function ChannelsCard({
  channels,
  writable,
  onChanged,
}: {
  channels: TriggerChannels;
  writable: boolean;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [showClosed, setShowClosed] = useState(false);
  const [channel, setChannel] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<Out>(null);

  const change = async (id: string, enabled: boolean, name = "") => {
    setBusy(true);
    setOut(null);
    try {
      await api.setTriggerChannel(id, enabled, name);
      const shown = name ? `#${name}` : id;
      setOut({ text: enabled ? `Opened ${shown}.` : `Closed ${shown}.`, tone: "success" });
      if (enabled) {
        setChannel("");
        setName("");
      }
      onChanged();
    } catch (err) {
      setOut({ text: err instanceof Error ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const count = channels.active.length;
  return (
    <div className="ml-settings__preview ml-trigger__channels">
      <button
        type="button"
        className="ml-trigger__toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true">{open ? "▾" : "▸"}</span> Open channels{" "}
        <Badge tone={count ? "accent" : "neutral"}>{count}</Badge>
      </button>
      <p className="ml-settings__hint">
        Everyone&rsquo;s mentions run in these channels, in every thread. Elsewhere only a thread
        you opened with your own mention accepts them.
      </p>

      {open ? (
        <>
          {count ? (
            <ul className="ml-trigger__rows" aria-label="open channels">
              {channels.active.map((c) => (
                <ChannelRow key={c.channel_id} row={c}>
                  <Button
                    size="sm"
                    disabled={!writable || busy}
                    onClick={() => change(c.channel_id, false, c.name)}
                    aria-label={`Close ${label(c)}`}
                  >
                    Close
                  </Button>
                </ChannelRow>
              ))}
            </ul>
          ) : (
            <p className="ml-settings__hint">No channel is open to everyone yet.</p>
          )}

          <div className="ml-settings__grid">
            <Field label="Channel" hint="Its id (C0123ABCD), or paste a link to it or to any message in it.">
              {(id) => (
                <TextInput
                  id={id}
                  value={channel}
                  placeholder="C0123ABCD or https://…/archives/C0123ABCD"
                  onChange={(e) => setChannel(e.target.value)}
                />
              )}
            </Field>
            <Field label="Name" hint="Optional. Looked up from Slack when left empty.">
              {(id) => (
                <TextInput id={id} value={name} placeholder="#general" onChange={(e) => setName(e.target.value)} />
              )}
            </Field>
          </div>
          <div className="ml-settings__foot">
            <Button
              variant="primary"
              disabled={!writable || busy || !channel.trim()}
              onClick={() => change(channel.trim(), true, name.trim())}
            >
              Open channel
            </Button>
          </div>

          {channels.removed.length ? (
            <div className="ml-trigger__closed">
              <button
                type="button"
                className="ml-trigger__toggle ml-trigger__toggle--minor"
                aria-expanded={showClosed}
                onClick={() => setShowClosed((v) => !v)}
              >
                <span aria-hidden="true">{showClosed ? "▾" : "▸"}</span> Closed{" "}
                <Badge>{channels.removed.length}</Badge>
              </button>
              {showClosed ? (
                <ul className="ml-trigger__rows" aria-label="closed channels">
                  {channels.removed.map((c) => (
                    <ChannelRow key={c.channel_id} row={c} closed>
                      <Button
                        size="sm"
                        disabled={!writable || busy}
                        onClick={() => change(c.channel_id, true, c.name)}
                        aria-label={`Reopen ${label(c)}`}
                      >
                        Reopen
                      </Button>
                    </ChannelRow>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
      {out ? <StatusMessage tone={out.tone}>{out.text}</StatusMessage> : null}
    </div>
  );
}

/** How a channel reads to a person: `#name`, or its id when it has none. */
const label = (c: TriggerChannelRow) => (c.name ? `#${c.name}` : c.channel_id);

function ChannelRow({
  row,
  closed,
  children,
}: {
  row: TriggerChannelRow;
  closed?: boolean;
  children: ReactNode;
}) {
  const when = (closed ? row.removed_at : row.added_at).slice(0, 10);
  const who = closed ? row.removed_by : row.added_by;
  return (
    <li className="ml-trigger__row">
      <span>
        <strong>{label(row)}</strong>{" "}
        {row.name ? <code>{row.channel_id}</code> : null}
      </span>
      <span className="ml-settings__note">
        {closed ? "closed" : "opened"} {when}
        {who ? ` by ${who}` : ""}
      </span>
      {children}
    </li>
  );
}

// ---------------------------------------------------------------- recent
const REQUEST_TONE: Record<string, BadgeTone> = {
  done: "success",
  declined: "warn",
  running: "accent",
  queued: "accent",
  refused: "warn",
  failed: "danger",
  timeout: "danger",
  interrupted: "warn",
};

function Recent({ status }: { status: TriggerStatus }) {
  const total = Object.values(status.counts).reduce((a, b) => a + b, 0);
  return (
    <>
      <h3 className="ml-settings__h3">
        Requests <Badge>{total}</Badge>
      </h3>
      {status.recent.length ? (
        <ul className="ml-settings__list" aria-label="recent requests">
          {status.recent.map((r) => (
            <li key={r.id} title={r.id}>
              <Badge tone={REQUEST_TONE[r.status] ?? "neutral"}>{r.status}</Badge> {r.source} ·{" "}
              {r.requester_name || r.requester_id}: {r.text}{" "}
              {r.permalink ? (
                <a href={r.permalink} target="_blank" rel="noreferrer">
                  open
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="ml-settings__hint">No mention has reached the brain yet.</p>
      )}
      <p className="ml-settings__hint">
        {status.enabled_scopes.length} open conversation
        {status.enabled_scopes.length === 1 ? "" : "s"}. Every request and each action taken for it:{" "}
        <code>trigger.py show --id &lt;id&gt;</code>.
      </p>
    </>
  );
}
