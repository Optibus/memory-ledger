"""Voice for the Agents chat: the settings, the speech-to-text socket, the speech route.

None of these writes to the brain or calls a model, so unlike `writes.py` they
are open on a `--no-write` server too: listening and reading aloud change
nothing (the settings and the translator, which do, are in `writes.py`).
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

import voice

from . import models as m
from .writes import body, clip

router = APIRouter(prefix="/api", tags=["voice"])


@router.get("/voice", summary="Voice: the settings, and what can listen and speak here",
            description="The saved settings plus whether faster-whisper (STT) and a TTS engine "
                        "are usable on this machine, and the voices the engine offers.")
def voice_status():
    return voice.status()


@router.post("/voice-speak", summary="Read a block aloud",
             description="Cleans the text (markdown, URLs, long paths, emoji), picks the language "
                         "and the voice, and answers the audio as a WAV. 204 when nothing is left "
                         "to say after the cleaning.")
async def voice_speak(data: m.VoiceSpeak = body(m.VoiceSpeak)):
    try:
        audio, mime, lang = await asyncio.to_thread(
            voice.synthesize, clip(data.text, 4000), clip(data.language, 8), clip(data.voice, 80))
    except ValueError:
        return Response(status_code=204)
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return Response(audio, media_type=mime, headers={"X-Voice-Language": lang})


async def _send(ws: WebSocket, **msg) -> bool:
    try:
        await ws.send_text(json.dumps(msg, ensure_ascii=False))
        return True
    except (RuntimeError, WebSocketDisconnect):
        return False


@router.websocket("/voice-stt")
async def voice_stt(ws: WebSocket):
    """One session of dictation. Client: `{"type":"start","language":"pt"|"auto"}`, binary
    PCM16 16 kHz mono frames, then `{"type":"stop"}` (finish what is buffered) or
    `{"type":"cancel"}`. Server: `ready`, `partial`, `final`, `language_detected`, `error`.

    The server cuts segments on pauses (`voice.Segmenter`); a partial is the open segment
    transcribed again, a final is the closed one. A partial that finds the transcriber
    busy is dropped, a final waits its turn."""
    await ws.accept()
    seg = voice.Segmenter()
    jobs: asyncio.Queue = asyncio.Queue()
    language: str | None = None
    seg_id = 0
    detected = ""
    cfg = voice.load_config()

    async def work():
        nonlocal seg_id, detected
        while True:
            kind, pcm = await jobs.get()
            if kind == "end":
                return
            try:
                text, lang = await asyncio.to_thread(voice.STT.transcribe, pcm, language)
            except voice.SttError as e:
                await _send(ws, type="error", code="stt_failed", message=str(e))
                continue
            if lang and language is None and lang != detected:
                detected = lang
                await _send(ws, type="language_detected", language=lang)
            if kind == "final":
                if text:
                    await _send(ws, type="final", text=text, segment_id=seg_id, language=lang)
                seg_id += 1
            elif text:
                await _send(ws, type="partial", text=text, segment_id=seg_id)

    def enqueue(batch):
        for kind, pcm in batch:
            if kind == "final":
                jobs.put_nowait((kind, pcm))
            elif kind == "partial" and jobs.empty() and not busy.locked():
                jobs.put_nowait((kind, pcm))

    busy = voice.STT.lock
    task = None
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if task:
                    enqueue(seg.feed(msg["bytes"]))
                continue
            try:
                ctl = json.loads(msg.get("text") or "{}")
            except ValueError:
                continue
            t = ctl.get("type")
            if t == "start" and task is None:
                want = ctl.get("language") or cfg["stt_language"]
                language = None if want == "auto" else (want if want in voice.LANGS else "pt")
                st = voice.stt_status()
                if not st["available"]:
                    await _send(ws, type="error", code="stt_unavailable", message=st["problem"])
                    break
                task = asyncio.create_task(work())
                await _send(ws, type="ready")
            elif t == "stop":
                if task:
                    enqueue(seg.flush())
                    jobs.put_nowait(("end", b""))
                    await task
                    task = None
                await _send(ws, type="stopped")
                break
            elif t == "cancel":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if task and not task.done():
            task.cancel()
        try:
            await ws.close()
        except RuntimeError:
            pass
