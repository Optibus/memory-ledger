import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { useAsync } from "@/hooks/useAsync";
import { Badge, Button, Card, Checkbox, Field, Select, Spinner, StatusMessage, TextInput } from "@/components";
import type { VoiceConfig, VoiceLang, VoiceStatus, VoiceVerbosity } from "@/api/types";
import "./SettingsSection.css";

const LANGS: { value: VoiceLang; label: string }[] = [
  { value: "pt", label: "Português" },
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
  { value: "it", label: "Italiano" },
];

const VERBOSITY: { value: VoiceVerbosity; label: string }[] = [
  { value: "default", label: "Default (as it is now)" },
  { value: "verbose", label: "Verbose (one short sentence per step)" },
  { value: "verbose_tts", label: "Verbose with voice (only when speech or conversation mode is on)" },
];

/**
 * Voice for the Agents chat: dictation, reading aloud, language, narration. Saved to
 * `voice.json` in the brain home. Speech to text needs faster-whisper on this machine
 * (the model comes from `$BRAIN_STT_MODEL`); the voice is whichever TTS engine is installed.
 */
export function VoiceCard({ writable }: { writable: boolean }) {
  const status = useAsync<VoiceStatus>((signal) => api.voice(signal), []);
  const [cfg, setCfg] = useState<VoiceConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<{ text: string; tone: "success" | "error" } | null>(null);

  useEffect(() => {
    if (status.data) setCfg(status.data.config);
  }, [status.data]);

  if (status.loading && !status.data) return <Spinner label="reading voice settings" />;
  if (status.error) return <StatusMessage tone="error">{status.error}</StatusMessage>;
  if (!status.data || !cfg) return null;
  const { stt, tts } = status.data;
  const set = (patch: Partial<VoiceConfig>) => setCfg((c) => (c ? { ...c, ...patch } : c));
  const voicesFor = (lang: VoiceLang) => [
    { value: "", label: "automatic" },
    ...tts.voices.filter((v) => v.language === lang).map((v) => ({ value: v.id, label: v.name })),
  ];

  const save = async () => {
    setBusy(true);
    setOut(null);
    try {
      await api.saveVoice(cfg);
      setOut({ text: "Saved.", tone: "success" });
      status.reload();
    } catch (err) {
      setOut({ text: err instanceof Error ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <h2 className="ml-settings__h">Voice</h2>
      <p className="ml-settings__hint">
        Talk to the agents in <code>Agents</code> and hear them back. Saved to <code>voice.json</code> in the brain home.
      </p>
      <p className="ml-settings__hint">
        Listening:{" "}
        {stt.available ? <Badge tone="success">faster-whisper · {stt.model}</Badge> : <Badge tone="warn">{stt.problem}</Badge>}
        {"  "}Speaking:{" "}
        {tts.available ? <Badge tone="success">{tts.engine}</Badge> : <Badge tone="warn">{tts.problem}</Badge>}
      </p>

      <div className="ml-settings__grid">
        <Field label="Listening language" hint="The language you speak.">
          {(id) => (
            <Select id={id} value={cfg.stt_language} options={LANGS} onChange={(e) => set({ stt_language: e.target.value as VoiceLang })} disabled={!writable} />
          )}
        </Field>
        <Field label="Speaking language" hint="The voice, when a block's language cannot be told or detection is off.">
          {(id) => (
            <Select id={id} value={cfg.tts_language} options={LANGS} onChange={(e) => set({ tts_language: e.target.value as VoiceLang })} disabled={!writable} />
          )}
        </Field>
        <Field label="Silence before sending" hint="Seconds, 2 to 15. Conversation mode sends after this long without new words.">
          {(id) => (
            <TextInput id={id} type="number" min={2} max={15} value={String(cfg.silence_s)} onChange={(e) => set({ silence_s: Number(e.target.value) })} disabled={!writable} />
          )}
        </Field>
        <Field label="Narration" hint="Global; each chat can override it.">
          {(id) => (
            <Select id={id} value={cfg.verbosity} options={VERBOSITY} onChange={(e) => set({ verbosity: e.target.value as VoiceVerbosity })} disabled={!writable} />
          )}
        </Field>
        {LANGS.map((l) => (
          <Field key={l.value} label={`Voice: ${l.label}`}>
            {(id) => (
              <Select
                id={id}
                value={cfg.tts_voices[l.value] ?? ""}
                options={voicesFor(l.value)}
                onChange={(e) => set({ tts_voices: { ...cfg.tts_voices, [l.value]: e.target.value } })}
                disabled={!writable || !tts.available}
              />
            )}
          </Field>
        ))}
      </div>

      <div className="ml-settings__switch">
        <Checkbox checked={cfg.stt_auto} onChange={(v) => set({ stt_auto: v })} label="Detect the language I speak" disabled={!writable} />
      </div>
      <div className="ml-settings__switch">
        <Checkbox checked={cfg.tts_auto} onChange={(v) => set({ tts_auto: v })} label="Detect the language of each block to pick the voice" disabled={!writable} />
      </div>
      <div className="ml-settings__switch">
        <Checkbox checked={cfg.tts_enabled} onChange={(v) => set({ tts_enabled: v })} label="Read the agent's messages aloud (each chat can override it)" disabled={!writable} />
      </div>
      <div className="ml-settings__switch">
        <Checkbox checked={cfg.tts_background} onChange={(v) => set({ tts_background: v })} label="Also read chats that are not on screen (announced by chat name)" disabled={!writable} />
      </div>
      <div className="ml-settings__switch">
        <Checkbox checked={cfg.translate} onChange={(v) => set({ translate: v })} label="Translate every block of output (one model call each)" disabled={!writable} />
      </div>
      {cfg.translate ? (
        <Field label="Translate into">
          {(id) => (
            <Select id={id} value={cfg.translate_target} options={LANGS} onChange={(e) => set({ translate_target: e.target.value as VoiceLang })} disabled={!writable} />
          )}
        </Field>
      ) : null}

      <div className="ml-settings__foot">
        <Button variant="primary" onClick={() => void save()} disabled={!writable || busy}>
          {busy ? "Saving…" : "Save voice"}
        </Button>
        {out ? <StatusMessage tone={out.tone}>{out.text}</StatusMessage> : null}
      </div>
    </Card>
  );
}
