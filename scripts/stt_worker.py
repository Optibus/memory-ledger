#!/usr/bin/env python3
"""The STT child process (scripts/voice.py): faster-whisper, loaded once.

Protocol on stdin/stdout, one request at a time:
    in : a JSON line {"language": "pt" | "", "n": <bytes>} then <n> bytes of PCM16 mono 16 kHz
    out: a JSON line {"text": ..., "language": ...} or {"error": ...}

The model is `$BRAIN_STT_MODEL` (default `medium`) on CPU, int8, unless
`$BRAIN_STT_DEVICE` / `$BRAIN_STT_COMPUTE` say otherwise.
"""

import json
import os
import sys

import numpy as np


def main() -> None:
    rd, wr = sys.stdin.buffer, sys.stdout.buffer
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(os.environ.get("BRAIN_STT_MODEL", "medium"),
                             device=os.environ.get("BRAIN_STT_DEVICE", "cpu"),
                             compute_type=os.environ.get("BRAIN_STT_COMPUTE", "int8"))
    except Exception as e:
        # Answer every request with why, instead of dying and leaving the server guessing.
        why = f"could not load the model {os.environ.get('BRAIN_STT_MODEL', 'medium')!r}: {type(e).__name__}: {str(e)[-300:]}"
        for line in rd:
            rd.read(int(json.loads(line)["n"]))
            wr.write((json.dumps({"error": why}) + "\n").encode())
            wr.flush()
        return
    while True:
        line = rd.readline()
        if not line:
            return
        try:
            head = json.loads(line)
            pcm = rd.read(int(head["n"]))
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            segs, info = model.transcribe(audio, language=head.get("language") or None,
                                          beam_size=1, vad_filter=False,
                                          condition_on_previous_text=False)
            out = {"text": " ".join(s.text.strip() for s in segs), "language": info.language}
        except Exception as e:  # reported to the server, which keeps the session alive
            out = {"error": f"{type(e).__name__}: {e}"}
        wr.write((json.dumps(out) + "\n").encode())
        wr.flush()


if __name__ == "__main__":
    main()
