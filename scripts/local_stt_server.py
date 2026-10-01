"""Local OpenAI-compatible speech-to-text server (faster-whisper).

Exposes POST /v1/audio/transcriptions (multipart: file, model, language,
response_format=json|verbose_json|text) so LiveKit's ``openai.STT`` plugin and
τ-bench can use it with ``base_url=http://localhost:8000/v1`` and no API key.

    uv run --project external/tau2-bench python scripts/local_stt_server.py --model small.en --port 8000

Models (CPU int8 on Apple silicon): tiny.en (~fastest) · base.en · small.en (default,
good accuracy/latency balance) · medium.en (slower). Weights download on first run.
"""

from __future__ import annotations

import argparse
import io
import os
import tempfile
import time

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from faster_whisper import WhisperModel

# --- Force IPv4: on this machine IPv6 connections to model CDNs hang in SYN_SENT ---
import socket as _socket

_orig_getaddrinfo = _socket.getaddrinfo


def _ipv4_first(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    v4 = [r for r in res if r[0] == _socket.AF_INET]
    return v4 or res


_socket.getaddrinfo = _ipv4_first

app = FastAPI(title="local-stt")
_model: WhisperModel | None = None
_model_name = os.environ.get("LOCAL_STT_MODEL", "small.en")


LOCAL_MODEL_ROOT = os.environ.get("LOCAL_STT_DIR", os.path.expanduser("~/.cache/local-stt"))


def get_model() -> WhisperModel:
    """Prefer a pre-downloaded CTranslate2 dir (~/.cache/local-stt/<name>/model.bin),
    else let faster-whisper fetch from the Hugging Face hub."""
    global _model
    if _model is None:
        local_dir = os.path.join(LOCAL_MODEL_ROOT, _model_name)
        src = local_dir if os.path.exists(os.path.join(local_dir, "model.bin")) else _model_name
        _model = WhisperModel(src, device="cpu", compute_type="int8")
    return _model


def _decode(data: bytes, filename: str) -> np.ndarray:
    """Decode any container soundfile/ffmpeg understands to mono float32 16 kHz."""
    try:
        audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception:
        from pydub import AudioSegment  # ffmpeg-backed fallback (mp3/webm/ogg)

        suffix = os.path.splitext(filename or "")[1] or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as f:
            f.write(data)
            f.flush()
            seg = AudioSegment.from_file(f.name)
        seg = seg.set_channels(1).set_frame_rate(16000).set_sample_width(2)
        return np.frombuffer(seg.raw_data, dtype=np.int16).astype(np.float32) / 32768.0
    audio = audio.mean(axis=1)
    if sr != 16000:
        # simple linear resample (adequate for speech)
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return audio


@app.get("/health")
def health():
    return {"ok": True, "model": _model_name}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
    response_format: str = Form("json"),
    temperature: float = Form(0.0),
):
    data = await file.read()
    t0 = time.time()
    audio = _decode(data, file.filename or "")
    segments, info = get_model().transcribe(
        audio,
        language=(language or "en").split("-")[0],
        initial_prompt=prompt,
        temperature=temperature,
        vad_filter=False,
        beam_size=1,
        condition_on_previous_text=False,
    )
    segs = list(segments)
    text = " ".join(s.text.strip() for s in segs).strip()
    dt = time.time() - t0
    if response_format == "text":
        return PlainTextResponse(text)
    body = {"text": text}
    if response_format == "verbose_json":
        body.update(
            {
                "task": "transcribe",
                "language": info.language,
                "duration": float(len(audio)) / 16000.0,
                "segments": [
                    {"id": i, "start": s.start, "end": s.end, "text": s.text, "avg_logprob": s.avg_logprob}
                    for i, s in enumerate(segs)
                ],
                "words": [
                    {"word": w.word, "start": w.start, "end": w.end}
                    for s in segs
                    for w in (s.words or [])
                ],
            }
        )
    body["_local"] = {"model": _model_name, "seconds": round(dt, 3)}
    return JSONResponse(body)


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=_model_name)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    _model_name = args.model
    get_model()  # warm up
    print(f"local-stt ready: model={_model_name} http://{args.host}:{args.port}/v1")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
