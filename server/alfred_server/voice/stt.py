"""Speech-to-text: 16 kHz mono 16-bit PCM chunks in, a `Transcript` out.

While the audio streams in, `transcribe` can also report partial transcripts (`Partial`): the
buffered audio is re-decoded every `stt_partial_interval_s`, and words two decodes in a row agree
on count as stable (docs/decisions/0021-recording-screen.md).

The voice gateway depends only on the `SpeechToText` protocol, so the engine can be swapped
(e.g. for a Parakeet-class model). The default engine is faster-whisper (CTranslate2), loaded
once per process via `get_stt()`. Model choice and benchmarks: docs/decisions/0002-stt.md.
"""

from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import logging
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from alfred_server.config import Settings, get_settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
"""Input sample rate in Hz. Audio is mono, signed 16-bit little-endian PCM."""


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    audio_duration_s: float
    processing_s: float


@dataclass(frozen=True)
class Partial:
    """An interim transcript: `stable` words won't change; the `unstable` tail still may."""

    stable: str
    unstable: str


PartialCallback = Callable[[Partial], Awaitable[None]]


class SpeechToText(Protocol):
    async def transcribe(
        self, chunks: AsyncIterator[bytes], on_partial: PartialCallback | None = None
    ) -> Transcript:
        """Transcribe one utterance, streamed as raw PCM chunks of any size.

        `on_partial`, if given, may be called with interim transcripts while the audio streams,
        always before `transcribe` returns.
        """
        ...


class WhisperLikeModel(Protocol):
    """The part of `faster_whisper.WhisperModel` used here (lets tests inject a fake)."""

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[Iterable[Any], Any]: ...


class AudioTooLongError(ValueError):
    """The streamed utterance exceeded `stt_max_audio_s`."""


def _word_key(word: str) -> str:
    """A word compared without case or punctuation ("timer," and "Timer" agree)."""
    return re.sub(r"[^\w']", "", word.lower())


def split_partial(hypothesis: str, previous: str, stable: str) -> Partial:
    """Split a fresh decode into stable and unstable words (LocalAgreement-2).

    Words count as stable once two decodes in a row (`previous`, `hypothesis`) agree on them,
    from the start. Stable words never change or shrink: once `stable` is said, it is kept as it
    was, and only the words after it can be revised.
    """
    words = hypothesis.split()
    agreed = 0
    for new, old in zip(words, previous.split(), strict=False):
        if _word_key(new) != _word_key(old):
            break
        agreed += 1
    kept = stable.split()
    stable_words = kept + words[len(kept) : agreed]
    return Partial(" ".join(stable_words), " ".join(words[len(stable_words) :]))


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """Convert signed 16-bit little-endian PCM to float32 samples in [-1, 1)."""
    if len(data) % 2:
        raise ValueError(f"PCM16 audio must have an even number of bytes, got {len(data)}")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


class FasterWhisperSTT:
    """faster-whisper engine. Holds one loaded model; limits concurrent jobs to the setting."""

    def __init__(self, settings: Settings, model: WhisperLikeModel | None = None) -> None:
        self._settings = settings
        self._model = model if model is not None else _load_model(settings)
        self._slots = asyncio.Semaphore(settings.stt_max_concurrency)

    async def transcribe(
        self, chunks: AsyncIterator[bytes], on_partial: PartialCallback | None = None
    ) -> Transcript:
        max_bytes = int(self._settings.stt_max_audio_s * SAMPLE_RATE) * 2
        step_bytes = int(self._settings.stt_partial_interval_s * SAMPLE_RATE) * 2
        partials = (
            _PartialDecoder(self, on_partial, step_bytes) if on_partial and step_bytes else None
        )
        buffer = bytearray()
        try:
            async for chunk in chunks:
                buffer.extend(chunk)
                if len(buffer) > max_bytes:
                    # Stop reading: a stuck VAD or misbehaving client must not grow this forever.
                    raise AudioTooLongError(
                        f"Utterance longer than {self._settings.stt_max_audio_s:g} s "
                        "(ALFRED_STT_MAX_AUDIO_S)"
                    )
                if partials:
                    partials.feed(buffer)
        except BaseException:
            if partials:
                partials.abandon()
            raise
        if partials:
            await partials.finish()
        audio = pcm16_to_float32(bytes(buffer))
        duration = len(audio) / SAMPLE_RATE
        if not len(audio):
            return Transcript("", self._settings.stt_language, 0.0, 0.0)

        async with self._slots:
            started = time.perf_counter()
            text, language = await asyncio.to_thread(self._decode, audio)
            elapsed = time.perf_counter() - started
        logger.debug("STT %.2fs audio in %.0f ms", duration, elapsed * 1000)
        return Transcript(text, language, duration, elapsed)

    def warmup(self) -> None:
        """Run one short decode so the first real request doesn't pay CUDA start-up costs."""
        self._decode(np.zeros(SAMPLE_RATE, dtype=np.float32))

    def _decode(self, audio: np.ndarray) -> tuple[str, str]:
        s = self._settings
        segments, info = self._model.transcribe(
            audio,
            language=s.stt_language,
            beam_size=s.stt_beam_size,
            initial_prompt=s.stt_initial_prompt or None,
            vad_filter=False,  # the phone's VAD already trims the utterance
            condition_on_previous_text=False,
        )
        # `segments` is lazy: decoding happens while iterating, so keep it in this thread.
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text, getattr(info, "language", s.stt_language)


_background: set[asyncio.Task[None]] = set()
"""Partial decodes abandoned mid-run, kept referenced until their thread finishes."""


class _PartialDecoder:
    """Re-decodes the audio so far, one decode at a time, to report `Partial`s.

    A partial decode starts once `step_bytes` of new audio have arrived since the last one, if
    none is running and an STT slot is free: it never queues behind another turn's decode, so
    partials can't slow anyone's final transcript by more than one short decode. The slot is
    checked again as the decode starts, since another turn may take it after `feed` schedules it.
    """

    def __init__(self, stt: FasterWhisperSTT, on_partial: PartialCallback, step_bytes: int):
        self._stt = stt
        self._on_partial = on_partial
        self._step_bytes = step_bytes
        self._decoded_bytes = 0
        self._previous = ""
        self._stable = ""
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def feed(self, buffer: bytearray) -> None:
        if self._task is not None and not self._task.done():
            return
        if len(buffer) - self._decoded_bytes < self._step_bytes or self._stt._slots.locked():
            return
        size = len(buffer) - len(buffer) % 2  # a chunk may end mid-sample
        previous, self._decoded_bytes = self._decoded_bytes, size
        self._task = asyncio.create_task(self._run(bytes(buffer[:size]), previous))

    async def finish(self) -> None:
        """End of audio: let a running partial finish (it's sent before the final transcript)."""
        if self._task is not None:
            await self._task
        self._closed = True

    def abandon(self) -> None:
        """The turn failed or was cancelled: send nothing more, but let the decode end."""
        self._closed = True
        task = self._task
        if task is not None and not task.done():
            # Its thread still holds the STT slot; keep the task alive until it lets go.
            _background.add(task)
            task.add_done_callback(_background.discard)

    async def _run(self, pcm: bytes, previous_bytes: int) -> None:
        # Another turn may have taken the last slot since `feed`. Checked right before acquiring
        # (nothing awaits in between, and a free semaphore is taken without yielding), so a
        # partial never waits in the queue; the next `feed` tries again.
        if self._closed or self._stt._slots.locked():
            self._decoded_bytes = previous_bytes
            return
        try:
            async with self._stt._slots:
                if self._closed:
                    return
                text, _ = await asyncio.to_thread(self._stt._decode, pcm16_to_float32(pcm))
            if self._closed:
                return
            partial = split_partial(text, self._previous, self._stable)
            self._previous, self._stable = text, partial.stable
            if partial.stable or partial.unstable:
                await self._on_partial(partial)
        except Exception:
            # Partials are a nicety: the final transcript doesn't depend on them.
            logger.warning("partial STT decode failed", exc_info=True)


@lru_cache
def get_stt() -> FasterWhisperSTT:
    """Return the process-wide STT engine, loading the model on first use."""
    return FasterWhisperSTT(get_settings())


def _load_model(settings: Settings) -> WhisperLikeModel:
    _register_cuda_libraries()
    from faster_whisper import WhisperModel

    started = time.perf_counter()
    device = resolve_device(settings.stt_device)
    compute_type = resolve_compute_type(settings.stt_compute_type, device)
    model = WhisperModel(
        settings.stt_model,
        device=device,
        compute_type=compute_type,
        download_root=settings.stt_download_root or None,
        # One CTranslate2 worker per allowed concurrent job; with a single worker a second
        # job just queues inside CTranslate2. Each extra worker costs some memory (ADR 0002).
        num_workers=settings.stt_max_concurrency,
    )
    logger.info(
        "Loaded STT model %s (%s, %s) in %.1fs",
        settings.stt_model,
        device,
        compute_type,
        time.perf_counter() - started,
    )
    return model


def resolve_device(device: str) -> str:
    """Turn "auto" into "cuda" when a CUDA device is visible, else "cpu"."""
    if device != "auto":
        return device
    import ctranslate2

    return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"


def resolve_compute_type(compute_type: str, device: str) -> str:
    """Turn "auto" into the benchmarked choice for the device (docs/decisions/0002-stt.md)."""
    if compute_type != "auto":
        return compute_type
    return "int8_float16" if device == "cuda" else "int8"


def _register_cuda_libraries() -> None:
    """Make cuBLAS/cuDNN from the optional `cuda` extra's pip wheels loadable by CTranslate2.

    The wheels put their libraries under `site-packages/nvidia/*/{bin,lib}`, which is on
    neither the Windows DLL path nor the Linux loader path. No-op when they aren't installed
    (e.g. a CUDA base image that already ships them, or CPU-only installs).
    """
    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return
    roots = [Path(location) for location in spec.submodule_search_locations]
    if sys.platform == "win32":
        _add_windows_dll_dirs(sorted(d for root in roots for d in root.glob("*/bin")))
    else:
        _preload_shared_libraries(
            sorted(lib for root in roots for lib in root.glob("*/lib/lib*.so.*"))
        )


_registered_dll_dirs: set[str] = set()
_preloaded_libraries: set[str] = set()


def _add_windows_dll_dirs(bin_dirs: list[Path]) -> None:
    """Add each directory to the DLL search path (and PATH) once per process."""
    if sys.platform != "win32":  # also tells mypy add_dll_directory exists below
        return
    for bin_dir in map(str, bin_dirs):
        if bin_dir in _registered_dll_dirs:
            continue
        os.add_dll_directory(bin_dir)
        path = os.environ.get("PATH", "")
        if bin_dir not in path.split(os.pathsep):
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{path}"
        _registered_dll_dirs.add(bin_dir)


def _preload_shared_libraries(libs: list[Path]) -> None:
    """`dlopen` every library globally, retrying failures until no more can be loaded.

    Libraries depend on each other (e.g. libcublas needs libcublasLt, which sorts after it), so
    a single pass in name order isn't enough. Whatever still fails is logged as a warning:
    CTranslate2 will then fail to load with a less helpful error.
    """
    pending = [lib for lib in libs if str(lib) not in _preloaded_libraries]
    errors: dict[Path, OSError] = {}
    while pending:
        errors = {}
        for lib in pending:
            try:
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
            except OSError as exc:
                errors[lib] = exc
            else:
                _preloaded_libraries.add(str(lib))
        if len(errors) == len(pending):
            break  # no progress this pass
        pending = list(errors)
    for lib, error in errors.items():
        logger.warning("Could not preload CUDA library %s: %s", lib, error)
