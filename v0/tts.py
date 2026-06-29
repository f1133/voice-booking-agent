"""Natural text-to-speech via Kokoro (kokoro-onnx — runs on onnxruntime, no torch).

Kokoro is a small, natural neural voice (PRD §11.1 names Kokoro/Piper as the
self-hosted TTS). We use the ONNX build because it installs cleanly on Python
3.14 (the torch-based `kokoro` package has no 3.14 wheels). If the package or
model files are missing, the browser falls back to its best available voice.

Setup:
    pip install kokoro-onnx soundfile
    # model files live in v0/models/ (downloaded once):
    #   kokoro-v1.0.onnx, voices-v1.0.bin
Voice: set KOKORO_VOICE (e.g. af_heart, af_bella, am_michael, bf_emma).
"""
from __future__ import annotations

import io
import os
import threading
import wave
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
MODEL_PATH = os.environ.get("KOKORO_MODEL", os.path.join(MODELS, "kokoro-v1.0.onnx"))
VOICES_PATH = os.environ.get("KOKORO_VOICES", os.path.join(MODELS, "voices-v1.0.bin"))

VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
LANG = os.environ.get("KOKORO_LANG", "en-us")

_engine = None
_backend: Optional[str] = None
_lock = threading.Lock()


def available() -> bool:
    """True only if the package AND the model files are present."""
    try:
        import kokoro_onnx  # noqa: F401
    except Exception:
        return False
    return os.path.exists(MODEL_PATH) and os.path.exists(VOICES_PATH)


def load():
    global _engine, _backend
    if _engine is not None:
        return _engine
    from kokoro_onnx import Kokoro
    if not (os.path.exists(MODEL_PATH) and os.path.exists(VOICES_PATH)):
        raise FileNotFoundError(
            f"Kokoro model files missing. Expected:\n  {MODEL_PATH}\n  {VOICES_PATH}")
    _engine = Kokoro(MODEL_PATH, VOICES_PATH)
    _backend = "kokoro-onnx"
    return _engine


def synthesize(text: str) -> bytes:
    """Return mono 16-bit PCM WAV bytes for the given text."""
    import numpy as np

    text = (text or "").strip()
    if not text:
        return b""
    with _lock:                       # onnx session reuse — serialize calls
        engine = load()
        samples, sample_rate = engine.create(text, voice=VOICE, speed=1.0, lang=LANG)

    audio = np.asarray(samples, dtype="float32")
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def backend() -> Optional[str]:
    return _backend
