"""Local voice embeddings for clinician matching — no third-party PHI network calls.

Default fingerprint is offline (numpy spectral). Optional ATTUNE_VOICE_ID=resemblyzer
uses a local model if installed. Samples are never retained after embed().
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence


def _voice_id_mode() -> str:
    return (os.environ.get("ATTUNE_VOICE_ID") or "local").strip().lower()


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return max(-1.0, min(1.0, dot / math.sqrt(na * nb)))


def match_threshold() -> float:
    raw = os.environ.get("ATTUNE_VOICE_MATCH_THRESHOLD", "0.72").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.72


@dataclass(frozen=True)
class VoiceMatch:
    clinician_id: str
    display_name: str
    score: float


class VoiceIdProvider(ABC):
    @abstractmethod
    def embed_file(self, audio_path: str) -> list[float]:
        """Compute embedding; caller deletes audio afterward."""

    @abstractmethod
    def embed_pcm(self, samples: list[float], sample_rate: int) -> list[float]:
        ...


def load_mono_pcm(audio_path: str, target_sr: int = 16000) -> tuple[list[float], int]:
    """Load mono float PCM in [-1, 1]. Prefer wav; try PyAV for webm/mp3."""
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(audio_path)
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return _load_wav(path, target_sr)
    try:
        return _load_with_av(path, target_sr)
    except Exception:
        # Last resort: deterministic bytes fingerprint path (embed_file handles).
        raise RuntimeError(
            f"unsupported audio format for voice-id decode: {suffix or 'unknown'}"
        )


def _load_wav(path: Path, target_sr: int) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    if width == 1:
        fmt = f"{len(raw)}B"
        ints = struct.unpack(fmt, raw)
        samples = [(v - 128) / 128.0 for v in ints]
    elif width == 2:
        count = len(raw) // 2
        ints = struct.unpack(f"<{count}h", raw)
        samples = [v / 32768.0 for v in ints]
    else:
        raise RuntimeError("only 8- or 16-bit wav supported for voice-id")
    if channels > 1:
        mono: list[float] = []
        for i in range(0, len(samples), channels):
            chunk = samples[i : i + channels]
            mono.append(sum(chunk) / len(chunk))
        samples = mono
    if rate != target_sr and samples:
        samples = _resample(samples, rate, target_sr)
        rate = target_sr
    return samples, rate


def _load_with_av(path: Path, target_sr: int) -> tuple[list[float], int]:
    import av
    import numpy as np

    container = av.open(str(path))
    try:
        stream = container.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(
            format="flt", layout="mono", rate=target_sr
        )
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                arr = out.to_ndarray()
                if arr.ndim > 1:
                    arr = arr.reshape(-1)
                chunks.append(arr.astype("float32", copy=False))
        if not chunks:
            raise RuntimeError("no audio frames")
        pcm = np.concatenate(chunks)
        return pcm.tolist(), target_sr
    finally:
        container.close()


def _resample(samples: list[float], src_rate: int, dst_rate: int) -> list[float]:
    if src_rate == dst_rate or not samples:
        return samples
    ratio = dst_rate / src_rate
    out_len = max(1, int(len(samples) * ratio))
    out: list[float] = []
    for i in range(out_len):
        src_idx = i / ratio
        left = int(src_idx)
        right = min(left + 1, len(samples) - 1)
        frac = src_idx - left
        out.append(samples[left] * (1 - frac) + samples[right] * frac)
    return out


def _spectral_fingerprint(samples: list[float], sample_rate: int, dims: int = 64) -> list[float]:
    """Lightweight offline embedding: log band energies over the clip."""
    if len(samples) < 32:
        # Tiny clip — fall back to hash of quantized samples.
        return _hash_embedding(b"".join(struct.pack("<f", s) for s in samples[:256]), dims)
    # Frame and accumulate magnitude spectrum via numpy if available.
    try:
        import numpy as np
    except ImportError:
        raw = b"".join(struct.pack("<f", s) for s in samples[:8000])
        return _hash_embedding(raw, dims)

    x = np.asarray(samples, dtype=np.float32)
    # Pre-emphasis + window
    x = x - np.mean(x)
    n = min(len(x), sample_rate * 8)
    x = x[:n]
    win = 512
    hop = 256
    bands = np.zeros(dims, dtype=np.float64)
    if len(x) < win:
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        idx = np.linspace(0, len(spec) - 1, dims).astype(int)
        bands = np.log1p(spec[idx])
    else:
        for start in range(0, len(x) - win, hop):
            frame = x[start : start + win] * np.hanning(win)
            spec = np.abs(np.fft.rfft(frame))
            # Mel-ish log spacing into dims bins
            edges = np.geomspace(1, len(spec), dims + 1).astype(int)
            for i in range(dims):
                a, b = edges[i], max(edges[i + 1], edges[i] + 1)
                bands[i] += float(np.mean(spec[a:b]))
        bands = np.log1p(bands)
    norm = np.linalg.norm(bands)
    if norm <= 0:
        return [0.0] * dims
    return (bands / norm).tolist()


def _hash_embedding(data: bytes, dims: int = 64) -> list[float]:
    digest = hashlib.sha256(data).digest()
    # Expand with counter hashing for dims floats in [-1, 1]
    out: list[float] = []
    block = digest
    while len(out) < dims:
        for i in range(0, len(block), 4):
            if len(out) >= dims:
                break
            chunk = block[i : i + 4]
            if len(chunk) < 4:
                break
            val = struct.unpack(">I", chunk)[0]
            out.append((val / 0xFFFFFFFF) * 2.0 - 1.0)
        block = hashlib.sha256(block + digest).digest()
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in out)) or 1.0
    return [v / norm for v in out]


class LocalSpectralVoiceId(VoiceIdProvider):
    """Offline spectral fingerprint — default for Phase 2 (no network)."""

    def embed_file(self, audio_path: str) -> list[float]:
        try:
            samples, rate = load_mono_pcm(audio_path)
            return self.embed_pcm(samples, rate)
        except Exception:
            data = Path(audio_path).read_bytes()
            return _hash_embedding(data)

    def embed_pcm(self, samples: list[float], sample_rate: int) -> list[float]:
        return _spectral_fingerprint(samples, sample_rate)


class HashVoiceId(VoiceIdProvider):
    """Deterministic content-hash embedding (tests / undecodable formats)."""

    def embed_file(self, audio_path: str) -> list[float]:
        return _hash_embedding(Path(audio_path).read_bytes())

    def embed_pcm(self, samples: list[float], sample_rate: int) -> list[float]:
        raw = b"".join(struct.pack("<f", s) for s in samples[:4000])
        raw += struct.pack("<I", sample_rate)
        return _hash_embedding(raw)


def get_voice_id_provider() -> VoiceIdProvider:
    mode = _voice_id_mode()
    if mode in ("hash", "mock"):
        return HashVoiceId()
    if mode == "resemblyzer":
        try:
            return _ResemblyzerVoiceId()
        except Exception:
            return LocalSpectralVoiceId()
    return LocalSpectralVoiceId()


class _ResemblyzerVoiceId(VoiceIdProvider):
    """Optional local GE2E model — only if resemblyzer is installed."""

    def __init__(self) -> None:
        from resemblyzer import VoiceEncoder  # type: ignore

        self._encoder = VoiceEncoder()

    def embed_file(self, audio_path: str) -> list[float]:
        from resemblyzer import preprocess_wav  # type: ignore
        import numpy as np

        wav = preprocess_wav(Path(audio_path))
        emb = self._encoder.embed_utterance(wav)
        return np.asarray(emb, dtype=float).tolist()

    def embed_pcm(self, samples: list[float], sample_rate: int) -> list[float]:
        from resemblyzer import preprocess_wav  # type: ignore
        import numpy as np

        wav = preprocess_wav(np.asarray(samples, dtype=np.float32), source_sr=sample_rate)
        emb = self._encoder.embed_utterance(wav)
        return np.asarray(emb, dtype=float).tolist()


def best_matches(
    probe: list[float],
    gallery: list[tuple[str, str, list[float]]],
    *,
    threshold: Optional[float] = None,
) -> list[VoiceMatch]:
    """
    gallery items: (clinician_id, display_name, embedding)
    Returns matches sorted by score desc, filtered by threshold.
    """
    thr = match_threshold() if threshold is None else threshold
    scored = [
        VoiceMatch(clinician_id=cid, display_name=name, score=cosine_similarity(probe, emb))
        for cid, name, emb in gallery
        if emb
    ]
    scored.sort(key=lambda m: m.score, reverse=True)
    return [m for m in scored if m.score >= thr]
