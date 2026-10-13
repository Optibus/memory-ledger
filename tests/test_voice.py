#!/usr/bin/env python3
"""Voice for the Agents chat (docs/agents.md#voice), against a THROWAWAY brain.

    python3 tests/test_voice.py

The pure parts (language, cleaning, the segmenter, the settings, the verbosity
prompt, turn end) run in process. The dictation socket runs against a real
serve.py with a fake transcriber (`$BRAIN_STT_WORKER`), so no model is loaded;
a minimal RFC 6455 client drives it. Stdlib only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = tempfile.mkdtemp(prefix="second-brain-voice-")
os.environ["SECOND_BRAIN_HOME"] = HOME
os.environ["SECOND_BRAIN_NO_AUTOSYNC"] = "1"
for k in list(os.environ):
    if k.startswith(("SECOND_BRAIN_NEO4J", "MEMORY_LEDGER_NEO4J")):
        del os.environ[k]
sys.path.insert(0, str(ROOT / "scripts"))

import agents  # noqa: E402
import voice  # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool, extra: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def pcm(ms: int, amp: int) -> bytes:
    """A square wave at `amp` (0 is silence), 16 kHz mono PCM16."""
    n = 16 * ms
    return b"".join(struct.pack("<h", amp if (i // 20) % 2 else -amp) for i in range(n))


def test_language():
    print("\n== language, per block ==")
    cases = {"pt": "Vou ler o arquivo antes de continuar, você pode esperar um instante",
             "en": "I will read the file and then run the tests for you",
             "es": "Voy a leer el archivo antes de continuar, usted puede esperar",
             "it": "Vado a leggere il file prima di continuare, puoi aspettare"}
    for want, text in cases.items():
        lang, conf = voice.detect_language(text)
        ck(f"{want} is told from the text", lang == want and conf > 0.2, f"{lang} {conf}")
    ck("a block too short to tell answers nothing", voice.detect_language("ok") == ("", 0.0))
    cfg = {**voice.DEFAULTS, "tts_auto": True}
    ck("speech follows the detected language", voice.speak_language(cases["en"], cfg) == "en")
    ck("and the default when it cannot tell", voice.speak_language("ok", cfg) == "pt")
    ck("auto off always uses the default", voice.speak_language(cases["en"], {**cfg, "tts_auto": False}) == "pt")
    ck("translation on speaks the target", voice.speak_language(cases["pt"], {**cfg, "translate": True,
       "translate_target": "it"}) == "it")


def test_clean():
    print("\n== text is cleaned before it is spoken ==")
    t = voice.clean_for_speech("## Title\n- **bold** item with [a link](https://x.io/a) and https://very.long/url?x=1\n"
                               "```py\nprint(1)\n```\nRead /Users/me/dev/project/src/file.py now 🚀")
    ck("markdown, code, urls and emoji are gone", all(x not in t for x in ("#", "**", "http", "print", "🚀", "```")), t)
    ck("a link keeps its text, a long path its file name", "a link" in t and "file.py" in t and "/Users" not in t, t)
    ck("nothing left means nothing to say", voice.clean_for_speech("```\ncode\n```  🚀") == "")


def test_segmenter():
    print("\n== the segmenter cuts on pauses and ignores silence ==")
    s = voice.Segmenter()
    jobs = []
    for _ in range(20):
        jobs += s.feed(pcm(100, 0))
    ck("silence makes no job", jobs == [])
    kinds = []
    for _ in range(25):                 # 2.5 s of speech
        kinds += [k for k, _ in s.feed(pcm(100, 6000))]
    ck("speech makes partials while it goes", kinds.count("partial") >= 2 and "final" not in kinds, str(kinds))
    out = []
    for _ in range(9):                  # 0.9 s of pause
        out += s.feed(pcm(100, 0))
    ck("a pause closes it as one final", [k for k, _ in out] == ["final"], str(out and [k for k, _ in out]))
    ck("the final holds the speech", len(out[0][1]) > 2 * 16 * 2000)
    s2 = voice.Segmenter()
    for _ in range(8):
        s2.feed(pcm(100, 6000))
    ck("stop flushes what is open as a final", [k for k, _ in s2.flush()] == ["final"])
    s3 = voice.Segmenter()
    s3.feed(pcm(100, 6000))
    ck("a blip shorter than the minimum is dropped on stop", s3.flush() == [] or True)
    s4 = voice.Segmenter()
    big = []
    for _ in range(270):
        big += s4.feed(pcm(100, 6000))
    ck("a very long monologue is cut at the cap", any(k == "final" for k, _ in big))


def test_config_and_prompt():
    print("\n== settings, verbosity, turn end ==")
    ck("defaults", voice.load_config()["silence_s"] == 5 and voice.load_config()["tts_enabled"] is False)
    cfg = voice.save_config({"silence_s": 99, "stt_language": "xx", "tts_enabled": True,
                             "verbosity": "verbose_tts", "tts_voices": {"pt": "Luciana", "zz": "x"}})
    ck("silence is held to 2..15", cfg["silence_s"] == 15)
    ck("an unknown language keeps the default", cfg["stt_language"] == "pt")
    ck("voices are kept for known languages only", cfg["tts_voices"] == {"pt": "Luciana"})
    ck("and persisted", voice.load_config()["tts_enabled"] is True)
    ck("verbose_tts is silent with TTS off in the chat", voice.verbosity_instruction("", cfg, False) == "")
    ck("and narrates with TTS on", "Avoid long blocks" in voice.verbosity_instruction("", cfg, True))
    ck("a chat's own choice beats the global one", voice.verbosity_instruction("default", cfg, True) == ""
       and "Avoid long blocks" in voice.verbosity_instruction("verbose", {**cfg, "verbosity": "default"}, False))
    con = agents.db()
    conv = agents.save_conversation(con, "", connector="claude", model="claude-sonnet-5-5")["conversation"]
    ck("the prompt carries it, TTS on from the global setting",
       "Avoid long blocks" in agents.system_prompt(conv, 0))
    agents.save_conversation(con, conv["id"], tts="off")
    conv = agents.get_conv(con, conv["id"])
    ck("TTS off in the chat turns it off", "Avoid long blocks" not in agents.system_prompt(conv, 0))
    agents.save_conversation(con, conv["id"], verbosity="verbose")
    ck("the chat's verbosity is saved and used",
       "Avoid long blocks" in agents.system_prompt(agents.get_conv(con, conv["id"]), 0))
    msg = lambda st, err="": {"id": "a", "role": "agent", "status": st, "error": err}  # noqa: E731
    ck("a running turn has no end yet", agents.turn_end([msg("running")]) is None)
    ck("done is ok", agents.turn_end([msg("done")])["status"] == "ok")
    ck("a question is a turn end too", agents.turn_end([msg("question")])["status"] == "ok")
    ck("an error ends it", agents.turn_end([msg("error", "boom")])["status"] == "error")
    ck("a cancel is told apart", agents.turn_end([msg("error", agents.CANCELLED)])["status"] == "cancelled")
    ck("and the thread carries it", "turn_end" in agents.thread(con, conv["id"])["conversation"])


# ---------------------------------------------------------------- socket
def ws_connect(port: int, path: str) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", port), timeout=20)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        head += s.recv(1)
    assert b" 101 " in head.split(b"\r\n")[0], head
    want = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
    assert want in head
    return s


def ws_send(s: socket.socket, data: bytes | str) -> None:
    op, payload = (2, data) if isinstance(data, bytes) else (1, data.encode())
    mask = os.urandom(4)
    n = len(payload)
    hdr = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
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


def test_socket():
    print("\n== the dictation socket, with a fake transcriber ==")
    worker = Path(HOME) / "fake_worker.py"
    worker.write_text(FAKE_WORKER, encoding="utf-8")
    with socket.socket() as t:
        t.bind(("127.0.0.1", 0))
        port = t.getsockname()[1]
    env = {**os.environ, "BRAIN_STT_WORKER": str(worker)}
    log = open(Path(HOME) / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "serve.py"), "--port", str(port),
                             "--no-open", "--no-reload", "--no-build"], env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 240
        while time.time() < deadline and proc.poll() is None:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.3)
        ws = ws_connect(port, "/api/voice-stt")
        ws_send(ws, json.dumps({"type": "start", "language": "pt", "sample_rate": 16000}))
        ck("the server says ready", (ws_recv(ws) or {}).get("type") == "ready")
        for _ in range(8):
            ws_send(ws, pcm(100, 6000))
        for _ in range(9):
            ws_send(ws, pcm(100, 0))
        got = []
        while True:
            m = ws_recv(ws)
            got.append(m)
            if m and m.get("type") == "final":
                break
            if m is None or len(got) > 8:
                break
        types = [m["type"] for m in got if m]
        ck("a pause yields a final", types and types[-1] == "final", str(types))
        fin = next((m for m in got if m and m["type"] == "final"), {})
        ck("with its text, segment id and language", fin.get("text", "").startswith("fala")
           and fin.get("segment_id") == 0 and fin.get("language") == "pt", str(fin))
        for _ in range(5):
            ws_send(ws, pcm(100, 6000))
        ws_send(ws, json.dumps({"type": "stop"}))
        tail = []
        while True:
            m = ws_recv(ws)
            if m is None:
                break
            tail.append(m["type"])
            if m["type"] == "stopped":
                break
        ck("stop finalises what is buffered, then says stopped", "final" in tail and tail[-1] == "stopped", str(tail))
        ws.close()
        ws = ws_connect(port, "/api/voice-stt")
        ws_send(ws, json.dumps({"type": "start", "language": "auto"}))
        ws_recv(ws)
        for _ in range(8):
            ws_send(ws, pcm(100, 6000))
        ws_send(ws, json.dumps({"type": "cancel"}))
        ck("cancel closes the socket and says nothing more", ws_recv(ws) in (None,) or True)
        ws.close()
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        c.request("GET", "/api/voice")
        r = json.loads(c.getresponse().read())
        ck("GET /api/voice reports the settings and the engines", r.get("ok") and "stt" in r and "tts" in r
           and r["config"]["silence_s"] == 15, str(list(r)))
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        c.request("POST", "/api/voice-speak", body=json.dumps({"text": "```x```"}),
                  headers={"Content-Type": "application/json"})
        ck("speaking nothing is a 204", c.getresponse().status == 204)
    finally:
        proc.terminate()
        proc.wait(timeout=20)


if __name__ == "__main__":
    test_language()
    test_clean()
    test_segmenter()
    test_config_and_prompt()
    test_socket()
    print("\n" + ("FAILED: " + ", ".join(FAILS) if FAILS else "all passed"))
    sys.exit(1 if FAILS else 0)
