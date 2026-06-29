"""Speech-to-text via faster-whisper (local, GPU with CPU fallback).

Design-aligned local ASR (PRD §11.1). On the RTX 5060 (Blackwell) it tries CUDA
first; if the installed CTranslate2 build can't target sm_120 it falls back to CPU
so the demo still works. The model is loaded lazily and reused across turns.
"""
from __future__ import annotations

import os
from io import BytesIO
from typing import Optional

_model = None
_backend: Optional[str] = None

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")        # base | small | medium
DEVICE = os.environ.get("WHISPER_DEVICE", "auto").lower()    # auto | cpu | cuda


def _build(device: str, compute_type: str):
    from faster_whisper import WhisperModel
    return WhisperModel(MODEL_SIZE, device=device, compute_type=compute_type)


def load():
    """Load the Whisper model once. Raises a friendly error if faster-whisper
    isn't installed. Honors WHISPER_DEVICE; defaults to trying CUDA then CPU.

    Note: CTranslate2 may not touch the CUDA DLLs until the first transcription,
    so a missing cuBLAS/cuDNN often only surfaces in transcribe() — handled there."""
    global _model, _backend
    if _model is not None:
        return _model
    try:
        import faster_whisper  # noqa: F401
    except ImportError as e:
        raise RuntimeError("faster-whisper isn't installed. Run:  pip install faster-whisper") from e

    if DEVICE in ("cuda", "auto"):
        try:
            candidate = _build("cuda", "int8_float16")
            _probe(candidate)        # force the CUDA libs to load NOW, not mid-call
            _model, _backend = candidate, "cuda"
            return _model
        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            print(f"[stt: CUDA unavailable ({msg}); using CPU]", flush=True)

    _model = _build("cpu", "int8")
    _backend = "cpu"
    return _model


def _probe(model) -> None:
    """Run a tiny inference so a missing cuBLAS/cuDNN surfaces at load time
    (it otherwise only fails on the first real transcription)."""
    import numpy as np
    segments, _info = model.transcribe(np.zeros(16000, dtype="float32"), beam_size=1)
    list(segments)  # the generator is lazy — force it to actually run the model


def _run(model, audio_bytes: bytes) -> str:
    segments, _info = model.transcribe(
        BytesIO(audio_bytes),
        language="en",
        vad_filter=True,          # trim silence / reduce hallucinated text
        beam_size=1,              # fast; correctness here is the read-back's job
    )
    return " ".join(seg.text for seg in segments).strip()


def transcribe(audio_bytes: bytes) -> str:
    """Transcribe an audio blob (e.g. browser webm/opus) to text.

    If a CUDA model was loaded but inference fails (e.g. cublas64_12.dll /
    cuDNN missing — common on Windows / Blackwell), rebuild on CPU and retry
    so the demo keeps working instead of erroring out."""
    global _model, _backend
    model = load()
    try:
        return _run(model, audio_bytes)
    except Exception as e:
        if _backend == "cuda":
            print(f"[stt: CUDA inference failed ({e}); falling back to CPU]", flush=True)
            _model = _build("cpu", "int8")
            _backend = "cpu"
            return _run(_model, audio_bytes)
        raise


def backend() -> Optional[str]:
    return _backend


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False
