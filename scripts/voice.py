#!/usr/bin/env python3
"""Voice for the Agents chat: speech to text, text to speech, language, translation.

Stdlib only, like the rest of the engine. The two heavy parts stay out of the
server's own environment:

  * STT runs in `stt_worker.py`, a child process under a Python that has
    faster-whisper (`$BRAIN_VOICE_PYTHON`, else the first `python3` that can
    import it). The model loads once, on the first session, and stays loaded.
  * TTS is an engine behind `TTSEngine`. The one shipped is macOS `say`
    (+ ffmpeg to WAV); a Piper or Kokoro engine is one more subclass.

The settings live in `<home>/voice.json`. The agent's verbosity is one of them
and is the only one the model ever sees (`verbosity_instruction`).

    python3 scripts/voice.py status
"""

from __future__ import annotations

import array
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph  # noqa: E402

LANGS = ("pt", "en", "es", "it")
VERBOSITIES = ("default", "verbose", "verbose_tts")

DEFAULTS: dict[str, Any] = {
    "stt_language": "pt",
    "stt_auto": False,
    "tts_language": "pt",
    "tts_auto": True,
    "translate": False,
    "translate_target": "en",
    "tts_enabled": False,
    "tts_background": False,
    "silence_s": 5,
    "tts_voices": {},          # language -> voice id; absent means automatic
    "verbosity": "default",
}

VERBOSE_PROMPT = (
    "Before each relevant step, write one short sentence saying what you are about to do. "
    "When you finish, sum up the result in one or two sentences. Avoid long blocks of text; "
    "prefer several short messages."
)


def config_path() -> Path:
    return graph.home_dir() / "voice.json"


def load_config() -> dict[str, Any]:
    cfg = {**DEFAULTS, "tts_voices": {}}
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if isinstance(raw, dict):
        cfg.update(_clean(raw))
    return cfg


def _lang(v: Any, default: str) -> str:
    return v if v in LANGS else default


def _clean(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ("stt_auto", "tts_auto", "translate", "tts_enabled", "tts_background"):
        if k in patch:
            out[k] = patch[k] is True
    for k in ("stt_language", "tts_language", "translate_target"):
        if k in patch:
            out[k] = _lang(patch[k], DEFAULTS[k])
    if "silence_s" in patch:
        try:
            out["silence_s"] = max(2, min(15, int(patch["silence_s"])))
        except (TypeError, ValueError):
            out["silence_s"] = DEFAULTS["silence_s"]
    if "verbosity" in patch:
        out["verbosity"] = patch["verbosity"] if patch["verbosity"] in VERBOSITIES else "default"
    if "tts_voices" in patch and isinstance(patch["tts_voices"], dict):
        out["tts_voices"] = {k: str(v)[:80] for k, v in patch["tts_voices"].items()
                             if k in LANGS and isinstance(v, str) and v}
    return out


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    cfg = {**load_config(), **_clean(patch)}
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return cfg


def verbosity_instruction(chat: str, cfg: dict[str, Any] | None = None,
                          tts_on: bool = False) -> str:
    """What to add to the agent's system prompt: the chat's own choice, else the
    global one. `verbose_tts` is verbose only while TTS is on for that chat."""
    cfg = cfg or load_config()
    mode = chat if chat in VERBOSITIES else cfg.get("verbosity", "default")
    if mode == "verbose" or (mode == "verbose_tts" and tts_on):
        return VERBOSE_PROMPT
    return ""


# ---------------------------------------------------------------- language
# A stopword vote, not a model: blocks are short and the four languages the
# owner speaks share few common words. Below the margin it answers "" and the
# caller uses the configured default.
_STOP = {
    "pt": "o a os as um uma de do da dos das em no na nos nas que não para por com se é são foi "
          "vou você voc eu ele ela isso este esta mas como mais já também ao aos tem foi está "
          "arquivo vamos agora preciso posso antes depois então".split(),
    "en": "the a an of to in is are was were be been and or not for with on at this that it "
          "you i he she we they will have has had from but as by can do does let me now file "
          "then before after".split(),
    "es": "el la los las un una de del en que no para por con se es son fue voy usted yo él "
          "ella eso este esta pero como más ya también al tiene está archivo vamos ahora "
          "necesito puedo antes después entonces".split(),
    "it": "il lo la i gli le un una di del della in che non per con si è sono era vado tu io "
          "lui lei questo questa ma come più già anche al ha sta file andiamo ora devo posso "
          "prima dopo allora".split(),
}
_ACCENT = {"pt": "ãõçê", "es": "ñ¿¡", "it": "ìù"}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_language(text: str) -> tuple[str, float]:
    """(language, confidence 0..1), or ("", 0.0) when it cannot tell."""
    words = [w.lower() for w in _WORD.findall(text or "")]
    if len(words) < 2:
        return "", 0.0
    score = {k: sum(1 for w in words if w in set(v)) for k, v in _STOP.items()}
    for k, chars in _ACCENT.items():
        score[k] += sum(1 for ch in (text or "").lower() if ch in chars) * 0.5
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < 1 or top - second < 1:
        return "", 0.0
    return best, round(min(1.0, (top - second) / max(2.0, len(words) / 2)), 2)


# ---------------------------------------------------------------- text
_CODE = re.compile(r"```.*?```", re.S)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"(?:~|\.{0,2})(?:/[\w.@+-]+){3,}/?")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D]")
_MARK = re.compile(r"^\s*(?:#+|>+|[-*+]|\d+[.)])\s+|[*~`|]+", re.M)


def clean_for_speech(text: str) -> str:
    """Markdown, URLs, long paths and emoji out, so the reading sounds natural."""
    t = _CODE.sub(" ", text or "")
    t = _LINK.sub(r"\1", t)
    t = _URL.sub(" ", t)
    t = _PATH.sub(lambda m: m.group(0).rstrip("/").rsplit("/", 1)[-1], t)
    t = _EMOJI.sub("", t)
    t = _MARK.sub(lambda m: " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- STT
RATE = 16000
BYTES_PER_MS = RATE * 2 // 1000


class Segmenter:
    """Cuts a 16 kHz PCM16 stream into speech segments on pauses.

    `feed` returns jobs: ("partial", pcm) while a segment is open and has grown
    enough since the last one, ("final", pcm) when a pause (or the length cap)
    closes it. Audio with no speech in it produces no job, so Whisper is never
    handed silence to hallucinate on. The energy threshold follows the room's
    noise floor."""

    PAUSE_MS = 700
    PARTIAL_MS = 1000
    MAX_MS = 25_000
    PREROLL_MS = 300
    MIN_SPEECH_MS = 200

    def __init__(self) -> None:
        self.buf = bytearray()
        self.preroll = bytearray()
        self.in_speech = False
        self.silence_ms = 0.0
        self.speech_ms = 0.0
        self.since_partial = 0.0
        self.floor = 150.0

    @staticmethod
    def rms(chunk: bytes) -> float:
        n = len(chunk) // 2
        if not n:
            return 0.0
        samples = array.array("h")
        samples.frombytes(chunk[: n * 2])
        if sys.byteorder == "big":
            samples.byteswap()
        step = max(1, n // 400)
        picked = samples[::step]
        return (sum(s * s for s in picked) / len(picked)) ** 0.5

    def feed(self, chunk: bytes) -> list[tuple[str, bytes]]:
        ms = len(chunk) / BYTES_PER_MS
        level = self.rms(chunk)
        loud = level > max(350.0, self.floor * 3)
        if not loud and not self.in_speech:
            self.floor = 0.95 * self.floor + 0.05 * level
        jobs: list[tuple[str, bytes]] = []
        if loud:
            if not self.in_speech:
                self.in_speech = True
                self.buf = bytearray(self.preroll)
                self.speech_ms = 0.0
                self.since_partial = 0.0
            self.silence_ms = 0.0
            self.speech_ms += ms
            self.buf += chunk
            self.since_partial += ms
            if len(self.buf) / BYTES_PER_MS >= self.MAX_MS:
                jobs.append(self._close())
            elif self.since_partial >= self.PARTIAL_MS and self.speech_ms >= self.MIN_SPEECH_MS:
                self.since_partial = 0.0
                jobs.append(("partial", bytes(self.buf)))
        elif self.in_speech:
            self.buf += chunk
            self.silence_ms += ms
            if self.silence_ms >= self.PAUSE_MS:
                jobs.append(self._close())
        else:
            self.preroll += chunk
            del self.preroll[: max(0, len(self.preroll) - self.PREROLL_MS * BYTES_PER_MS)]
        return jobs

    def _close(self) -> tuple[str, bytes]:
        pcm = bytes(self.buf)
        keep = self.speech_ms >= self.MIN_SPEECH_MS
        self.buf = bytearray()
        self.in_speech = False
        self.silence_ms = 0.0
        self.speech_ms = 0.0
        self.preroll = bytearray()
        return ("final", pcm) if keep else ("drop", b"")

    def flush(self) -> list[tuple[str, bytes]]:
        """Stop: whatever is buffered becomes a final."""
        if self.in_speech and self.speech_ms >= self.MIN_SPEECH_MS:
            return [self._close()]
        self.buf = bytearray()
        self.in_speech = False
        return []


class SttError(Exception):
    pass


def _worker_script() -> Path:
    """`$BRAIN_STT_WORKER` swaps the transcriber (the tests do); it brings its own deps."""
    return Path(os.environ.get("BRAIN_STT_WORKER") or Path(__file__).with_name("stt_worker.py"))


def _voice_python() -> str:
    """A Python that can import faster-whisper, or "" when none can."""
    if os.environ.get("BRAIN_STT_WORKER"):
        return sys.executable
    want = os.environ.get("BRAIN_VOICE_PYTHON", "").strip()
    cands = [want] if want else [sys.executable, shutil.which("python3") or "", "/usr/bin/python3",
                                 "/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
    for py in dict.fromkeys(c for c in cands if c):
        try:
            if subprocess.run([py, "-c", "import faster_whisper, numpy"], capture_output=True,
                              timeout=60).returncode == 0:
                return py
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


class SttWorker:
    """One long-lived `stt_worker.py`; requests are serialised."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.problem = ""

    def _start(self) -> subprocess.Popen:
        if self.proc and self.proc.poll() is None:
            return self.proc
        py = _voice_python()
        if not py:
            raise SttError("no Python with faster-whisper found: pip install faster-whisper, "
                           "or set $BRAIN_VOICE_PYTHON")
        self.proc = subprocess.Popen([py, str(_worker_script())],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        return self.proc

    def transcribe(self, pcm: bytes, language: str | None) -> tuple[str, str]:
        """(text, language) for one segment. Blocking; call it off the event loop."""
        with self.lock:
            p = self._start()
            try:
                head = json.dumps({"language": language or "", "n": len(pcm)}) + "\n"
                assert p.stdin and p.stdout
                p.stdin.write(head.encode() + pcm)
                p.stdin.flush()
                line = p.stdout.readline()
            except (OSError, AssertionError) as e:
                self.proc = None
                raise SttError(f"the transcriber stopped: {e}") from e
            if not line:
                self.proc = None
                raise SttError("the transcriber stopped")
            res = json.loads(line)
            if res.get("error"):
                raise SttError(res["error"])
            return res.get("text", "").strip(), res.get("language", "")

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None


STT = SttWorker()


def stt_status() -> dict[str, Any]:
    py = _voice_python()
    return {"available": bool(py), "model": os.environ.get("BRAIN_STT_MODEL", "medium"),
            "problem": "" if py else "no Python with faster-whisper found: pip install "
                                     "faster-whisper, or set $BRAIN_VOICE_PYTHON"}


# ---------------------------------------------------------------- TTS
class TTSEngine(ABC):
    """A local speech engine. Swap it by subclassing and registering in ENGINES."""
    name = ""

    @abstractmethod
    def problem(self) -> str:
        """Why this engine cannot speak here, or ""."""

    @abstractmethod
    def voices(self) -> list[dict[str, str]]:
        """[{id, name, language}], language one of LANGS."""

    @abstractmethod
    def synth(self, text: str, language: str, voice: str) -> tuple[bytes, str]:
        """(audio bytes, mime type)."""


class SayEngine(TTSEngine):
    """macOS `say`, converted to WAV with ffmpeg so every browser plays it."""
    name = "say"
    PREFER = {"pt": ("Luciana", "Eddy (Portuguese (Brazil))"), "en": ("Samantha", "Reed (English (US))"),
              "es": ("Paulina", "Monica", "Reed (Spanish (Mexico))"), "it": ("Alice", "Reed (Italian (Italy))")}
    LOCALES = {"pt": "pt_BR", "en": "en_US", "es": ("es_MX", "es_ES"), "it": "it_IT"}

    def problem(self) -> str:
        if not shutil.which("say"):
            return "the `say` command exists only on macOS; install a TTS engine"
        if not shutil.which("ffmpeg"):
            return "ffmpeg is needed to convert the speech to WAV"
        return ""

    def voices(self) -> list[dict[str, str]]:
        if self.problem():
            return []
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        found = []
        for line in out.splitlines():
            m = re.match(r"^(.*?)\s{2,}([a-z]{2}_[A-Z]{2})\b", line)
            if not m:
                continue
            for lang, loc in self.LOCALES.items():
                if m.group(2) in ((loc,) if isinstance(loc, str) else loc):
                    found.append({"id": m.group(1).strip(), "name": m.group(1).strip(), "language": lang})
        return found

    def synth(self, text: str, language: str, voice: str) -> tuple[bytes, str]:
        if not voice:
            have = {v["id"] for v in self.voices() if v["language"] == language}
            voice = next((v for v in self.PREFER.get(language, ()) if v in have), "")
        with tempfile.TemporaryDirectory(prefix="brain-tts-") as d:
            aiff, wav = os.path.join(d, "s.aiff"), os.path.join(d, "s.wav")
            cmd = ["say", *(["-v", voice] if voice else []), "-o", aiff, "--", text[:4000]]
            r = subprocess.run(cmd, capture_output=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode(errors="replace").strip() or "say failed")
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", aiff, "-ac", "1",
                                "-ar", "22050", wav], capture_output=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode(errors="replace").strip() or "ffmpeg failed")
            return Path(wav).read_bytes(), "audio/wav"


ENGINES: dict[str, type[TTSEngine]] = {"say": SayEngine}


def engine() -> TTSEngine:
    return ENGINES.get(os.environ.get("BRAIN_TTS_ENGINE", "say"), SayEngine)()


def speak_language(text: str, cfg: dict[str, Any]) -> str:
    """The language a block is read in: the detected one, else the default."""
    if cfg.get("translate"):
        return cfg["translate_target"]
    if cfg.get("tts_auto"):
        lang, conf = detect_language(text)
        if lang and conf >= 0.2:
            return lang
    return cfg["tts_language"]


def synthesize(text: str, language: str = "", voice: str = "") -> tuple[bytes, str, str]:
    """(audio, mime, language) for a block, cleaned first. ValueError when nothing is left to say."""
    cfg = load_config()
    clean = clean_for_speech(text)
    if not clean:
        raise ValueError("nothing to say")
    lang = language if language in LANGS else speak_language(clean, cfg)
    eng = engine()
    why = eng.problem()
    if why:
        raise RuntimeError(why)
    audio, mime = eng.synth(clean, lang, voice or cfg["tts_voices"].get(lang, ""))
    return audio, mime, lang


def tts_status() -> dict[str, Any]:
    eng = engine()
    why = eng.problem()
    return {"available": not why, "engine": eng.name, "problem": why,
            "voices": [] if why else eng.voices()}


# ---------------------------------------------------------------- translation
TRANSLATE_MODEL = "claude-haiku-4-5-20251001"
TRANSLATE_PROMPT = (
    "Translate the text between <text> tags into {lang}. Keep file names, paths, commands, "
    "identifiers, ticket ids, code and technical terms exactly as they are. Keep the markdown. "
    "Answer with the translation only, no tags, no comments.")
LANG_NAME = {"pt": "Brazilian Portuguese", "en": "English", "es": "Spanish", "it": "Italian"}


def translate(text: str, target: str, claude_bin: str = "claude") -> tuple[str, bool]:
    """(text, translated). Skipped, and returned as is, when the block is already in `target`."""
    lang, conf = detect_language(text)
    if lang == target and conf >= 0.2:
        return text, False
    if not clean_for_speech(text):
        return text, False
    r = subprocess.run([claude_bin, "-p", f"<text>\n{text}\n</text>", "--model", TRANSLATE_MODEL,
                        "--tools", "", "--append-system-prompt",
                        TRANSLATE_PROMPT.format(lang=LANG_NAME.get(target, target))],
                       capture_output=True, text=True, timeout=120)
    out = r.stdout.strip()
    if r.returncode != 0 or not out:
        raise RuntimeError(r.stderr.strip()[:300] or "the translator returned nothing")
    return out, True


def status() -> dict[str, Any]:
    return {"ok": True, "config": load_config(), "stt": stt_status(), "tts": tts_status()}


if __name__ == "__main__":
    graph.out(status()) if len(sys.argv) > 1 and sys.argv[1] == "status" else print(__doc__)
