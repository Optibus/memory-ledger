import { claimSession, keepSession, releaseSession } from "./session";
import { WORKLET_SOURCE } from "./worklet";

export interface DictationEvents {
  /** The open segment's provisional text; it replaces the previous partial. */
  onPartial: (text: string, segment: number) => void;
  /** A closed segment's text; it is kept. */
  onFinal: (text: string, segment: number, language: string) => void;
  onLanguage?: (language: string) => void;
  /**
   * The session is over. `reason` is empty for a normal Stop or Cancel, else why
   * it ended on its own (denied microphone, server down, socket lost): the text
   * already transcribed is kept in every case.
   */
  onEnd: (reason: string) => void;
}

export interface Dictation {
  /** End the capture and let the server finish what it has buffered. */
  stop: () => void;
  /** End the capture and throw away what was buffered. */
  cancel: () => void;
}

/** Why dictation cannot start here, or "". */
export function dictationProblem(): string {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia)
    return "This browser cannot reach the microphone (it needs https or localhost).";
  if (typeof AudioWorkletNode === "undefined") return "This browser has no AudioWorklet.";
  return "";
}

const socketUrl = () => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/voice-stt`;

/**
 * Open the microphone and a socket to `/api/voice-stt`, and stream PCM16 until stopped.
 *
 * The socket is reopened once if it drops mid-recording (the audio meanwhile is held
 * back, not lost); a second failure ends the session as an implicit Stop.
 */
export async function startDictation(language: string, ev: DictationEvents): Promise<Dictation> {
  const why = dictationProblem();
  if (why) throw new Error(why);
  if (!claimSession()) throw new Error("Another tab is already using the microphone.");
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    releaseSession();
    const denied = e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "SecurityError");
    throw new Error(denied ? "Microphone access was denied. Allow it in the browser to dictate." : "No microphone could be opened.");
  }

  let ws: WebSocket | null = null;
  let ready = false;
  let ended = false;
  let stopping = false;
  let retried = false;
  const backlog: ArrayBuffer[] = [];
  const ctx = new AudioContext();
  const heartbeat = setInterval(keepSession, 2_000);
  let stopTimer: ReturnType<typeof setTimeout> | undefined;

  const finish = (reason: string) => {
    if (ended) return;
    ended = true;
    clearInterval(heartbeat);
    clearTimeout(stopTimer);
    stream.getTracks().forEach((t) => t.stop());
    void ctx.close().catch(() => {});
    try {
      ws?.close();
    } catch {
      /* already closed */
    }
    releaseSession();
    ev.onEnd(reason);
  };

  const open = () => {
    ready = false;
    const sock = new WebSocket(socketUrl());
    ws = sock;
    sock.binaryType = "arraybuffer";
    sock.onopen = () => sock.send(JSON.stringify({ type: "start", language, sample_rate: 16000 }));
    sock.onmessage = (m) => {
      if (typeof m.data !== "string") return;
      const msg = JSON.parse(m.data) as { type: string; text?: string; segment_id?: number; language?: string; message?: string };
      if (msg.type === "ready") {
        ready = true;
        backlog.splice(0).forEach((b) => sock.send(b));
      } else if (msg.type === "partial") ev.onPartial(msg.text ?? "", msg.segment_id ?? 0);
      else if (msg.type === "final") ev.onFinal(msg.text ?? "", msg.segment_id ?? 0, msg.language ?? "");
      else if (msg.type === "language_detected") ev.onLanguage?.(msg.language ?? "");
      else if (msg.type === "stopped") finish("");
      else if (msg.type === "error") finish(msg.message || "The transcriber failed.");
    };
    sock.onclose = () => {
      if (ended || ws !== sock) return;
      if (stopping) return finish("");
      if (!retried) {
        retried = true;
        open();
      } else finish("The connection to the transcriber was lost.");
    };
    sock.onerror = () => {};
  };

  try {
    await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "text/javascript" })));
    const node = new AudioWorkletNode(ctx, "pcm16", { numberOfInputs: 1, numberOfOutputs: 0 });
    node.port.onmessage = (m: MessageEvent<ArrayBuffer>) => {
      if (stopping || ended) return;
      if (ws && ready && ws.readyState === WebSocket.OPEN) ws.send(m.data);
      else if (backlog.length < 200) backlog.push(m.data);
    };
    ctx.createMediaStreamSource(stream).connect(node);
  } catch (e) {
    finish("");
    throw new Error(`Could not start the audio capture: ${e instanceof Error ? e.message : String(e)}`);
  }
  open();
  // A browser that suspends the capture (hidden tab on some platforms) is a Stop.
  stream.getAudioTracks().forEach((t) => (t.onended = () => end(false)));

  const end = (discard: boolean) => {
    if (ended || stopping) return;
    stopping = true;
    stream.getTracks().forEach((t) => t.stop());
    if (!discard && ws && ws.readyState === WebSocket.OPEN && ready) {
      ws.send(JSON.stringify({ type: "stop" }));
      stopTimer = setTimeout(() => finish(""), 20_000);
    } else {
      try {
        if (discard && ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "cancel" }));
      } catch {
        /* closing anyway */
      }
      finish("");
    }
  };
  return { stop: () => end(false), cancel: () => end(true) };
}
