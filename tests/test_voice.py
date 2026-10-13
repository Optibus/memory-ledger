"""Voice for the Agents chat (docs/agents.md#voice), against a THROWAWAY brain.

    python -m pytest tests/test_voice.py

The pure parts (language, cleaning, the segmenter, the settings, the verbosity
prompt, turn end) run in process. The dictation socket runs against a real
serve.py with a fake transcriber (`$BRAIN_STT_WORKER`), so no model is loaded;
a minimal RFC 6455 client drives it.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import socket
import struct
import sys

import pytest

import agents
import voice
from conftest import LOCALHOST, UTF8

PCM_RATE_KHZ = 16
LOUD = 6000
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
STT_PATH = "/api/voice-stt"
CHUNK_MS = 100
VERBOSE_MARK = "Avoid long blocks"
TTS_ENGINE_VAR = "BRAIN_TTS_ENGINE"
WS_TIMEOUT_S = 20
HTTP_TIMEOUT_S = 30


def pcm(ms: int, amp: int) -> bytes:
    """A square wave at `amp` (0 is silence), 16 kHz mono PCM16."""
    n = PCM_RATE_KHZ * ms
    return b"".join(struct.pack("<h", amp if (i // 20) % 2 else -amp) for i in range(n))


def feed(segmenter, chunks: int, amp: int) -> list:
    out: list = []
    for _ in range(chunks):
        out += segmenter.feed(pcm(CHUNK_MS, amp))
    return out


# ================================================================ language
LANGUAGE_CASES = {
    "pt": "Vou ler o arquivo antes de continuar, voc\u00ea pode esperar um instante",
    "en": "I will read the file and then run the tests for you",
    "es": "Voy a leer el archivo antes de continuar, usted puede esperar",
    "it": "Vado a leggere il file prima di continuare, puoi aspettare",
}


@pytest.mark.parametrize("want, text", LANGUAGE_CASES.items())
def test_language_is_told_from_the_text(want, text):
    lang, conf = voice.detect_language(text)
    assert lang == want
    assert conf > 0.2


def test_a_block_too_short_to_tell_answers_nothing():
    assert voice.detect_language("ok") == ("", 0.0)


class TestSpeakLanguage:
    CFG = {**voice.DEFAULTS, "tts_auto": True}

    def test_speech_follows_the_detected_language(self):
        assert voice.speak_language(LANGUAGE_CASES["en"], self.CFG) == "en"

    def test_and_the_default_when_it_cannot_tell(self):
        assert voice.speak_language("ok", self.CFG) == "pt"

    def test_auto_off_always_uses_the_default(self):
        assert voice.speak_language(LANGUAGE_CASES["en"], {**self.CFG, "tts_auto": False}) == "pt"

    def test_translation_on_speaks_the_target(self):
        cfg = {**self.CFG, "translate": True, "translate_target": "it"}
        assert voice.speak_language(LANGUAGE_CASES["pt"], cfg) == "it"


# ================================================================ cleaning
CLEAN_INPUT = ("## Title\n- **bold** item with [a link](https://x.io/a) and https://very.long/url?x=1\n"
               "```py\nprint(1)\n```\nRead /Users/me/dev/project/src/file.py now \U0001F680")


@pytest.mark.parametrize("gone", ["#", "**", "http", "print", "\U0001F680", "```"])
def test_markdown_code_urls_and_emoji_are_gone(gone):
    assert gone not in voice.clean_for_speech(CLEAN_INPUT)


def test_a_link_keeps_its_text_and_a_long_path_its_file_name():
    text = voice.clean_for_speech(CLEAN_INPUT)
    assert "a link" in text
    assert "file.py" in text
    assert "/Users" not in text


def test_nothing_left_means_nothing_to_say():
    assert voice.clean_for_speech("```\ncode\n```  \U0001F680") == ""


# ================================================================ segmenter
class TestSegmenter:
    """The segmenter cuts on pauses and ignores silence."""

    def test_silence_makes_no_job(self):
        assert feed(voice.Segmenter(), 20, 0) == []

    def test_speech_makes_partials_while_it_goes(self):
        kinds = [k for k, _ in feed(voice.Segmenter(), 25, LOUD)]     # 2.5 s of speech
        assert kinds.count("partial") >= 2
        assert "final" not in kinds

    def test_a_pause_closes_it_as_one_final_holding_the_speech(self):
        s = voice.Segmenter()
        feed(s, 25, LOUD)
        out = feed(s, 9, 0)                                            # 0.9 s of pause
        assert [k for k, _ in out] == ["final"]
        assert len(out[0][1]) > 2 * PCM_RATE_KHZ * 2000

    def test_stop_flushes_what_is_open_as_a_final(self):
        s = voice.Segmenter()
        feed(s, 8, LOUD)
        assert [k for k, _ in s.flush()] == ["final"]

    def test_a_blip_shorter_than_the_minimum_is_dropped_on_stop(self):
        s = voice.Segmenter()
        feed(s, 1, LOUD)
        assert s.flush() == []

    def test_flushing_nothing_gives_nothing(self):
        assert voice.Segmenter().flush() == []

    def test_a_very_long_monologue_is_cut_at_the_cap(self):
        assert any(k == "final" for k, _ in feed(voice.Segmenter(), 270, LOUD))


# ================================================================ settings
class TestConfigAndPrompt:
    """Settings, verbosity, turn end."""

    @pytest.fixture
    def saved(self, brain_home):
        return voice.save_config({"silence_s": 99, "stt_language": "xx", "tts_enabled": True,
                                  "verbosity": "verbose_tts",
                                  "tts_voices": {"pt": "Luciana", "zz": "x"}})

    def test_defaults(self, brain_home):
        cfg = voice.load_config()
        assert cfg["silence_s"] == 5
        assert cfg["tts_enabled"] is False

    def test_silence_is_held_to_2_to_15(self, saved):
        assert saved["silence_s"] == 15
        assert voice.save_config({"silence_s": 0})["silence_s"] == 2

    def test_an_unknown_language_keeps_the_default(self, saved):
        assert saved["stt_language"] == "pt"

    def test_voices_are_kept_for_known_languages_only(self, saved):
        assert saved["tts_voices"] == {"pt": "Luciana"}

    def test_and_persisted(self, saved):
        assert voice.load_config()["tts_enabled"] is True

    def test_verbose_tts_is_silent_with_tts_off_in_the_chat(self, saved):
        assert voice.verbosity_instruction("", saved, False) == ""

    def test_and_narrates_with_tts_on(self, saved):
        assert VERBOSE_MARK in voice.verbosity_instruction("", saved, True)

    def test_a_chats_own_choice_beats_the_global_one(self, saved):
        assert voice.verbosity_instruction("default", saved, True) == ""
        assert VERBOSE_MARK in voice.verbosity_instruction(
            "verbose", {**saved, "verbosity": "default"}, False)

    def test_the_system_prompt_carries_the_verbosity_and_the_chats_choices(self, saved):
        con = agents.db()
        conv = agents.save_conversation(con, "", connector="claude",
                                        model="claude-sonnet-5-5")["conversation"]
        assert VERBOSE_MARK in agents.system_prompt(conv, 0)       # TTS on from the global setting
        agents.save_conversation(con, conv["id"], tts="off")
        conv = agents.get_conv(con, conv["id"])
        assert VERBOSE_MARK not in agents.system_prompt(conv, 0)   # TTS off in the chat
        agents.save_conversation(con, conv["id"], verbosity="verbose")
        assert VERBOSE_MARK in agents.system_prompt(agents.get_conv(con, conv["id"]), 0)

    def test_the_thread_carries_the_turn_end(self, saved):
        con = agents.db()
        conv = agents.save_conversation(con, "", connector="claude",
                                        model="claude-sonnet-5-5")["conversation"]
        assert "turn_end" in agents.thread(con, conv["id"])["conversation"]


def agent_message(status: str, err: str = "") -> dict:
    return {"id": "a", "role": "agent", "status": status, "error": err}


class TestTurnEnd:
    def test_a_running_turn_has_no_end_yet(self):
        assert agents.turn_end([agent_message("running")]) is None

    @pytest.mark.parametrize("status, err, expected", [
        ("done", "", "ok"),
        ("question", "", "ok"),
        ("error", "boom", "error"),
        ("error", agents.CANCELLED, "cancelled"),
    ], ids=["done-is-ok", "a-question-is-a-turn-end", "an-error-ends-it", "a-cancel-is-told-apart"])
    def test_how_a_turn_ends(self, status, err, expected):
        assert agents.turn_end([agent_message(status, err)])["status"] == expected


# ================================================================ engines
class TestEngines:
    """TTS engines: one per platform, with a fallback chain."""

    def test_each_platform_starts_with_its_own_engine(self):
        assert voice.engine_chain("darwin")[0] == "say"
        assert voice.engine_chain("win32")[0] == "sapi"
        assert voice.engine_chain("linux")[0] == "espeak-ng"

    @pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
    def test_piper_is_in_every_chain(self, platform):
        assert "piper" in voice.engine_chain(platform)

    def test_an_unknown_platform_gets_the_linux_chain(self):
        assert voice.engine_chain("freebsd14") == voice.engine_chain("linux")

    def test_every_engine_in_a_chain_is_registered(self):
        assert [n for p in voice.CHAINS for n in voice.CHAINS[p] if n not in voice.ENGINES] == []

    def test_the_env_var_picks_the_engine(self, monkeypatch):
        monkeypatch.setenv(TTS_ENGINE_VAR, "piper")
        assert voice.engine().name == "piper"

    def test_an_unknown_env_var_value_falls_back_to_the_chain(self, monkeypatch):
        monkeypatch.setenv(TTS_ENGINE_VAR, "nope")
        assert voice.engine().name in voice.engine_chain()

    @pytest.mark.skipif(os.name == "nt", reason="SAPI is the engine on Windows")
    def test_sapi_says_why_it_cannot_speak_off_windows(self):
        assert "Windows" in voice.SapiEngine().problem()

    def test_the_python_search_starts_with_this_interpreter(self):
        assert voice._python_candidates()[0] == (sys.executable,)


# ================================================================ socket
def ws_connect(port: int, path: str) -> socket.socket:
    s = socket.create_connection((LOCALHOST, port), timeout=WS_TIMEOUT_S)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {path} HTTP/1.1\r\nHost: {LOCALHOST}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
               ).encode())
    head = b""
    while b"\r\n\r\n" not in head:
        head += s.recv(1)
    assert b" 101 " in head.split(b"\r\n")[0], head
    assert base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()) in head
    return s


def ws_send(s: socket.socket, data: bytes | str) -> None:
    op, payload = (2, data) if isinstance(data, bytes) else (1, data.encode())
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        hdr = bytes([0x80 | op, 0x80 | n])
    elif n < 65536:
        hdr = bytes([0x80 | op, 0x80 | 126]) + struct.pack(">H", n)
    else:
        hdr = bytes([0x80 | op, 0x80 | 127]) + struct.pack(">Q", n)
    s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def ws_recv(s: socket.socket) -> dict | None:
    def need(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                return b""
            buf += chunk
        return buf

    h = need(2)
    if not h:
        return None
    op, n = h[0] & 0x0F, h[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", need(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", need(8))[0]
    body = need(n)
    if op == 8:
        return None
    return json.loads(body) if op == 1 else {}


FAKE_WORKER = '''
import json, sys
rd, wr = sys.stdin.buffer, sys.stdout.buffer
while True:
    line = rd.readline()
    if not line:
        break
    head = json.loads(line)
    pcm = rd.read(head["n"])
    wr.write((json.dumps({"text": "fala %d" % (len(pcm) // 3200), "language": head["language"] or "en"}) + "\\n").encode())
    wr.flush()
'''


@pytest.fixture
def server(brain_home, tmp_path, serve_process):
    """A real serve.py over a brain whose silence is held at 15 s, and whose
    transcriber is a fake."""
    voice.save_config({"silence_s": 99})
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER, encoding=UTF8)
    return serve_process(brain_home, "--no-build", env={"BRAIN_STT_WORKER": str(worker)})


@pytest.fixture
def ws(server):
    sock = ws_connect(server.port, STT_PATH)
    yield sock
    sock.close()


def start(sock: socket.socket, **fields) -> None:
    ws_send(sock, json.dumps({"type": "start", "language": "pt", "sample_rate": 16000, **fields}))


class TestDictationSocket:
    """The dictation socket, with a fake transcriber."""

    def test_the_server_says_ready(self, ws):
        start(ws)
        assert (ws_recv(ws) or {}).get("type") == "ready"

    def test_a_pause_yields_a_final_with_its_text_segment_id_and_language(self, ws):
        start(ws)
        ws_recv(ws)
        for _ in range(8):
            ws_send(ws, pcm(CHUNK_MS, LOUD))
        for _ in range(9):
            ws_send(ws, pcm(CHUNK_MS, 0))
        got: list = []
        while True:
            m = ws_recv(ws)
            got.append(m)
            if (m and m.get("type") == "final") or m is None or len(got) > 8:
                break
        types = [m["type"] for m in got if m]
        assert types
        assert types[-1] == "final", types
        fin = next(m for m in got if m and m["type"] == "final")
        assert fin["text"].startswith("fala")
        assert fin["segment_id"] == 0
        assert fin["language"] == "pt"

    def test_stop_finalises_what_is_buffered_then_says_stopped(self, ws):
        start(ws)
        ws_recv(ws)
        for _ in range(5):
            ws_send(ws, pcm(CHUNK_MS, LOUD))
        ws_send(ws, json.dumps({"type": "stop"}))
        tail: list[str] = []
        while True:
            m = ws_recv(ws)
            if m is None:
                break
            tail.append(m["type"])
            if m["type"] == "stopped":
                break
        assert "final" in tail
        assert tail[-1] == "stopped"

    def test_cancel_closes_the_socket_and_says_nothing_more(self, ws):
        start(ws, language="auto")
        ws_recv(ws)
        for _ in range(8):
            ws_send(ws, pcm(CHUNK_MS, LOUD))
        ws_send(ws, json.dumps({"type": "cancel"}))
        assert ws_recv(ws) is None


class TestVoiceHttp:
    def test_get_voice_reports_the_settings_and_the_engines(self, server):
        c = http.client.HTTPConnection(LOCALHOST, server.port, timeout=HTTP_TIMEOUT_S)
        c.request("GET", "/api/voice")
        r = json.loads(c.getresponse().read())
        assert r.get("ok"), list(r)
        assert "stt" in r
        assert "tts" in r
        assert r["config"]["silence_s"] == 15

    def test_speaking_nothing_is_a_204(self, server):
        c = http.client.HTTPConnection(LOCALHOST, server.port, timeout=HTTP_TIMEOUT_S)
        c.request("POST", "/api/voice-speak", body=json.dumps({"text": "```x```"}),
                  headers={"Content-Type": "application/json"})
        assert c.getresponse().status == 204
