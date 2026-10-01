"""Local OpenAI-compatible text-to-speech server (Kokoro, ONNX, CPU).

Exposes POST /v1/audio/speech {model, input, voice, response_format, speed}
returning audio, so LiveKit's ``openai.TTS`` plugin can use it with
``base_url=http://localhost:8880/v1`` and no API key. Also GET /v1/voices.

    uv run --project external/tau2-bench python scripts/local_tts_server.py --port 8880

Model files (~330 MB) are downloaded once to ~/.cache/kokoro-onnx/.
Voices: af_heart, af_bella, af_sarah, am_michael, am_adam, bf_emma, bm_george, ...
"""

from __future__ import annotations

import argparse
import io
import os
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

# --- Force IPv4: on this machine IPv6 connections to model CDNs hang in SYN_SENT ---
import socket as _socket

_orig_getaddrinfo = _socket.getaddrinfo


def _ipv4_first(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    v4 = [r for r in res if r[0] == _socket.AF_INET]
    return v4 or res


_socket.getaddrinfo = _ipv4_first

CACHE = Path(os.environ.get("KOKORO_CACHE", Path.home() / ".cache" / "kokoro-onnx"))
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
SAMPLE_RATE = 24000

app = FastAPI(title="local-tts")
_kokoro = None


def ensure_files() -> tuple[Path, Path]:
    """Model + voices from Hugging Face (shared helper in tau2's local_tts_utils)."""
    try:
        from tau2.voice.utils.local_tts_utils import ensure_kokoro_files

        return ensure_kokoro_files(CACHE)
    except ImportError:  # running outside the tau2 env: GitHub release fallback
        CACHE.mkdir(parents=True, exist_ok=True)
        model, voices = CACHE / "kokoro-v1.0.onnx", CACHE / "voices-v1.0.bin"
        for url, path in ((MODEL_URL, model), (VOICES_URL, voices)):
            if not path.exists() or path.stat().st_size < 1_000_000:
                print(f"downloading {url} -> {path}")
                urllib.request.urlretrieve(url, path)
        return model, voices


def get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro

        model, voices = ensure_files()
        _kokoro = Kokoro(str(model), str(voices))
    return _kokoro


def synthesize(text: str, voice: str = "af_heart", speed: float = 1.0) -> np.ndarray:
    """Return float32 mono samples at SAMPLE_RATE."""
    samples, sr = get_kokoro().create(text, voice=voice, speed=speed, lang="en-us")
    if sr != SAMPLE_RATE:
        n = int(len(samples) * SAMPLE_RATE / sr)
        samples = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    return samples.astype(np.float32)


class SpeechRequest(BaseModel):
    model: str = "kokoro"
    input: str
    voice: str = "af_heart"
    response_format: str = "pcm"
    speed: float = 1.0


def encode(samples: np.ndarray, fmt: str) -> tuple[bytes, str]:
    if fmt == "pcm":
        return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes(), "audio/pcm"
    buf = io.BytesIO()
    if fmt == "wav":
        sf.write(buf, samples, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue(), "audio/wav"
    if fmt in ("mp3", "opus", "aac", "flac"):
        from pydub import AudioSegment

        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        seg = AudioSegment(pcm, frame_rate=SAMPLE_RATE, sample_width=2, channels=1)
        codec = {"opus": "opus", "mp3": "mp3", "aac": "adts", "flac": "flac"}[fmt]
        seg.export(buf, format=codec)
        return buf.getvalue(), f"audio/{fmt}"
    raise ValueError(f"unsupported response_format {fmt}")


@app.get("/health")
def health():
    return {"ok": True, "sample_rate": SAMPLE_RATE}


@app.get("/v1/voices")
def voices():
    return JSONResponse({"voices": sorted(get_kokoro().get_voices())})


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    samples = synthesize(req.input, voice=req.voice, speed=req.speed)
    data, ctype = encode(samples, req.response_format)
    return Response(content=data, media_type=ctype)


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8880)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    get_kokoro()
    print(f"local-tts ready: http://{args.host}:{args.port}/v1 (voices: {len(get_kokoro().get_voices())})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
