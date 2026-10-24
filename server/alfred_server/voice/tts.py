"""Text-to-speech: a stream of text (LLM tokens) in, a stream of 24 kHz PCM chunks out.

The voice gateway depends only on the `TextToSpeech` protocol. The default implementation,
`LocalTTS`, splits the incoming text into sentences (the first one as early as possible, see
`SentenceSplitter`) and synthesizes each with Kokoro on the GPU, or Piper on the CPU as a
fallback. Engines, benchmarks and the output format: docs/decisions/0005-tts.md.

Output format, whatever the engine: mono, signed 16-bit little-endian PCM at
`OUTPUT_SAMPLE_RATE` (24 kHz), in chunks of at most `CHUNK_BYTES` (200 ms).
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np

from alfred_server.config import Settings, get_settings

logger = logging.getLogger(__name__)

OUTPUT_SAMPLE_RATE = 24_000
"""Output sample rate in Hz. Audio is mono, signed 16-bit little-endian PCM."""
CHUNK_BYTES = OUTPUT_SAMPLE_RATE // 5 * 2
"""Largest chunk `synthesize` yields: 200 ms of audio."""
SEGMENT_PAUSE_S = 0.1
"""Silence appended after each synthesized sentence or clause."""

Engine = Literal["kokoro", "piper"]

MIN_SPEED, MAX_SPEED = 0.5, 2.0
MAX_PITCH_SEMITONES = 6.0


class UnknownVoiceError(ValueError):
    """The requested voice isn't installed for that engine."""


class TTSUnavailableError(RuntimeError):
    """No TTS engine could be loaded."""


@dataclass(frozen=True)
class VoiceConfig:
    """Which voice to speak with. Personas (STU-66) will pick one of these."""

    engine: Engine
    voice: str
    speed: float = 1.0
    """Speaking rate, 0.5–2.0 (1.0 = the voice's natural rate)."""
    pitch: float = 0.0
    """Pitch shift in semitones, -6 to +6. Applied by resampling, so formants move too.

    The engine then runs at `speed / 2**(pitch / 12)`, which must also be within 0.5–2.0:
    e.g. speed 0.5 can't be combined with a higher pitch.
    """

    def __post_init__(self) -> None:
        if not MIN_SPEED <= self.speed <= MAX_SPEED:
            raise ValueError(f"speed must be {MIN_SPEED}–{MAX_SPEED}, got {self.speed}")
        if abs(self.pitch) > MAX_PITCH_SEMITONES:
            raise ValueError(f"pitch must be within ±{MAX_PITCH_SEMITONES:g}, got {self.pitch}")
        if not MIN_SPEED <= self.engine_speed <= MAX_SPEED:
            raise ValueError(
                f"speed {self.speed} with pitch {self.pitch:+g} needs an engine speed of "
                f"{self.engine_speed:.2f}, outside {MIN_SPEED}–{MAX_SPEED}"
            )

    @property
    def pitch_factor(self) -> float:
        return float(2 ** (self.pitch / 12))

    @property
    def engine_speed(self) -> float:
        """Rate to synthesize at, so that resampling for the pitch shift restores `speed`."""
        return self.speed / self.pitch_factor


@dataclass(frozen=True)
class VoiceInfo:
    engine: Engine
    id: str
    language: str
    """BCP 47-style tag, e.g. "en-GB"."""
    gender: Literal["female", "male"] | None = None


class TextToSpeech(Protocol):
    def synthesize(
        self, text_stream: AsyncIterator[str], voice: VoiceConfig
    ) -> AsyncIterator[bytes]:
        """Speak streamed text (tokens of any size) as PCM chunks (see module docstring).

        Implemented as an async generator (`async def ... yield`). Audio for the first sentence
        is yielded while later text is still arriving.
        """
        ...

    def voices(self) -> list[VoiceInfo]:
        """Every installed voice, across engines."""
        ...


# --- Sentence splitting ----------------------------------------------------------------------

# Words that end in "." without ending a sentence. Kept short on purpose: a missed break only
# delays audio, a wrong break only adds a short pause.
_ABBREVIATIONS = frozenset(
    {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "e.g", "i.e", "cf", "approx"}
)
_CLOSERS = "\"'”’)]"
_BOUNDARY = re.compile(
    rf"\n"  # line break: always ends a segment
    rf"|[.!?…]+[{re.escape(_CLOSERS)}]*(?=\s)"  # sentence end, once whitespace follows
    rf"|[,;:][{re.escape(_CLOSERS)}]*(?=\s)"  # clause end
    rf"|\s[—–-](?=\s)"  # spaced dash
)
_NEXT_WORD = re.compile(r"\s+(\S)")
_SPEAKABLE = re.compile(r"\w")


class SentenceSplitter:
    """Buffer streamed text and cut it into sentences that can be synthesized on their own.

    The first segment is also cut at a clause (",", ";", ":", spaced dash) once it has
    `min_clause_words` words, so speech can start before the first sentence is complete.
    Segments longer than `max_chars` are cut at their last clause or space.
    """

    def __init__(self, min_clause_words: int = 4, max_chars: int = 250) -> None:
        self._buffer = ""
        self._emitted_any = False
        self._min_clause_words = min_clause_words
        self._max_chars = max_chars

    def feed(self, text: str) -> list[str]:
        """Add text; return the segments that are now complete."""
        self._buffer += text
        segments: list[str] = []
        while True:
            end = self._find_boundary()
            if end is None and len(self._buffer) > self._max_chars:
                end = self._forced_boundary()
            if end is None:
                return segments
            self._emit(self._buffer[:end], segments)
            self._buffer = self._buffer[end:]

    def flush(self) -> list[str]:
        """End of stream: return whatever is left as a final segment."""
        segments: list[str] = []
        self._emit(self._buffer, segments)
        self._buffer = ""
        return segments

    def _emit(self, text: str, segments: list[str]) -> None:
        segment = " ".join(text.split())
        if _SPEAKABLE.search(segment):
            segments.append(segment)
            self._emitted_any = True

    def _find_boundary(self) -> int | None:
        buf = self._buffer
        for match in _BOUNDARY.finditer(buf):
            token = match.group()
            end = match.end()
            if token == "\n":
                return end
            if token[0] in ",;:" or token.strip() in "—–-":
                if not self._emitted_any and len(buf[:end].split()) >= self._min_clause_words:
                    return end
                continue
            if token[0] in "!?":
                return end
            # "." or an ellipsis: decide from the words either side.
            words = buf[: match.start()].split()
            if token.rstrip(_CLOSERS) == "." and words and _is_non_terminal(words):
                continue
            following = _NEXT_WORD.match(buf, end)
            if following is None:
                return None  # the next word decides; wait for it
            if following.group(1).islower():
                continue  # "5 p.m. today", "etc. and": a sentence doesn't start lowercase
            return end
        return None

    def _forced_boundary(self) -> int:
        head = self._buffer[: self._max_chars]
        clauses = [m.end() for m in _BOUNDARY.finditer(head)]
        if clauses:
            return clauses[-1]
        space = head.rfind(" ")
        return space if space > 0 else self._max_chars


def _is_non_terminal(words: list[str]) -> bool:
    """Whether a "." right after `words[-1]` is part of the word, not a sentence end."""
    word = words[-1].lstrip("\"'“‘([").lower()
    if word in _ABBREVIATIONS:
        return True
    if len(word) == 1 and word.isalpha() and words[-1][-1].isupper():
        return True  # an initial, as in "J. R. R. Tolkien"
    # "1." on its own: a list marker at the start of a segment
    return len(words) == 1 and word.isdigit()


async def split_sentences(
    text_stream: AsyncIterator[str], splitter: SentenceSplitter | None = None
) -> AsyncIterator[str]:
    """Yield speakable segments from a token stream as soon as each is complete."""
    splitter = splitter or SentenceSplitter()
    async for text in text_stream:
        for segment in splitter.feed(text):
            yield segment
    for segment in splitter.flush():
        yield segment


# --- Engines ---------------------------------------------------------------------------------


class SynthesisEngine(Protocol):
    """One TTS engine. Synthesis is blocking; `LocalTTS` runs it in a worker thread."""

    name: Engine

    def voices(self) -> list[VoiceInfo]: ...

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        """Return mono float32 samples in [-1, 1] and their sample rate."""
        ...


class KokoroLike(Protocol):
    """The part of `kokoro_onnx.Kokoro` used here (lets tests inject a fake)."""

    def get_voices(self) -> list[str]: ...

    def create(
        self, text: str, voice: str, speed: float = ..., lang: str = ...
    ) -> tuple[np.ndarray, int]: ...


# Kokoro voice ids start with a language letter and a gender letter, e.g. "bm_george".
_KOKORO_LANGUAGES = {
    "a": ("en-US", "en-us"),
    "b": ("en-GB", "en-gb"),
    "e": ("es", "es"),
    "f": ("fr-FR", "fr-fr"),
    "h": ("hi", "hi"),
    "i": ("it", "it"),
    "j": ("ja", "ja"),
    "p": ("pt-BR", "pt-br"),
    "z": ("zh", "cmn"),
}
_GENDERS: dict[str, Literal["female", "male"]] = {"f": "female", "m": "male"}


class KokoroEngine:
    name: Engine = "kokoro"

    def __init__(self, model: KokoroLike) -> None:
        self._model = model
        self._voices = [v for v in model.get_voices() if v[:1] in _KOKORO_LANGUAGES]

    @classmethod
    def load(cls, model_path: Path, voices_path: Path, device: str) -> KokoroEngine:
        import onnxruntime as rt
        from kokoro_onnx import Kokoro

        for path in (model_path, voices_path):
            if not path.is_file():
                raise FileNotFoundError(f"{path} not found (run scripts/download_tts_models.py)")
        started = time.perf_counter()
        providers = ["CPUExecutionProvider"]
        if device != "cpu" and "CUDAExecutionProvider" in rt.get_available_providers():
            _preload_cuda_libraries(rt)
            providers.insert(0, "CUDAExecutionProvider")
        # Errors only: onnxruntime's CUDA kernel warnings and the phonemizer's word-count
        # warnings are noise for this model.
        rt.set_default_logger_severity(3)
        logging.getLogger("phonemizer").setLevel(logging.ERROR)
        options = rt.SessionOptions()
        options.log_severity_level = 3
        session = rt.InferenceSession(str(model_path), options, providers=providers)
        active = session.get_providers()[0]
        if device == "cuda" and active != "CUDAExecutionProvider":
            raise RuntimeError(f"Kokoro could not use CUDA (running on {active})")
        engine = cls(Kokoro.from_session(session, str(voices_path)))
        engine.warmup()
        logger.info(
            "Loaded Kokoro %s (%s) in %.1fs",
            model_path.name,
            active,
            time.perf_counter() - started,
        )
        return engine

    def voices(self) -> list[VoiceInfo]:
        return [
            VoiceInfo("kokoro", v, _KOKORO_LANGUAGES[v[0]][0], _GENDERS.get(v[1:2]))
            for v in self._voices
        ]

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        lang = _KOKORO_LANGUAGES[voice[0]][1]
        samples, sample_rate = self._model.create(text, voice=voice, speed=speed, lang=lang)
        return np.asarray(samples, dtype=np.float32), sample_rate

    def warmup(self) -> None:
        """One short synthesis so the first request doesn't pay CUDA start-up costs."""
        if self._voices:
            self.synthesize("Ready.", self._voices[0], 1.0)


class PiperEngine:
    """Piper voices found in a directory (`<name>.onnx` + `<name>.onnx.json`), loaded lazily.

    Always on the CPU: medium-quality voices synthesize a short sentence in ~30 ms there, faster
    than on the GPU (docs/decisions/0005-tts.md), and use no VRAM.
    """

    name: Engine = "piper"

    def __init__(self, model_dir: Path) -> None:
        self._model_dir = model_dir
        self._loaded: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._voices = sorted(
            p.name.removesuffix(".onnx.json")
            for p in model_dir.glob("*.onnx.json")
            if p.with_suffix("").is_file()
        )

    def voices(self) -> list[VoiceInfo]:
        return [VoiceInfo("piper", v, _piper_language(v)) for v in self._voices]

    def load(self, voice: str) -> Any:
        with self._lock:
            if voice not in self._loaded:
                if voice not in self._voices:
                    raise UnknownVoiceError(f"Piper voice {voice!r} not found in {self._model_dir}")
                from piper import PiperVoice

                self._loaded[voice] = PiperVoice.load(self._model_dir / f"{voice}.onnx")
                logger.info("Loaded Piper voice %s", voice)
            return self._loaded[voice]

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        from piper import SynthesisConfig

        model = self.load(voice)
        chunks = list(model.synthesize(text, SynthesisConfig(length_scale=1.0 / speed)))
        sample_rate = chunks[0].sample_rate if chunks else model.config.sample_rate
        samples = [c.audio_float_array for c in chunks]
        audio = np.concatenate(samples) if samples else np.zeros(0, dtype=np.float32)
        return audio.astype(np.float32, copy=False), sample_rate


def _piper_language(voice: str) -> str:
    """Language tag of a Piper voice name: en_GB-alan-medium -> en-GB."""
    return voice.split("-", 1)[0].replace("_", "-")


# --- Streaming TTS ---------------------------------------------------------------------------


class LocalTTS:
    """`TextToSpeech` over Kokoro (preferred) and Piper (fallback).

    If Kokoro isn't loaded, or fails on a sentence, that audio comes from the Piper fallback
    voice instead, with a warning in the log. One sentence is synthesized at a time.
    """

    def __init__(
        self,
        kokoro: SynthesisEngine | None,
        piper: SynthesisEngine,
        fallback_voice: str,
        default_voice: VoiceConfig,
    ) -> None:
        self._kokoro = kokoro
        self._piper = piper
        self._fallback_voice = fallback_voice
        self.default_voice = default_voice
        # One synthesis at a time across all streams (GPU memory, predictable latency).
        self._render_lock = threading.Lock()
        self._warned_unavailable = False

    def voices(self) -> list[VoiceInfo]:
        engines = [e for e in (self._kokoro, self._piper) if e is not None]
        return [v for engine in engines for v in engine.voices()]

    async def synthesize(
        self, text_stream: AsyncIterator[str], voice: VoiceConfig
    ) -> AsyncIterator[bytes]:
        voice = self._resolve(voice)
        queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue(maxsize=4)

        async def produce() -> None:
            try:
                async for segment in split_sentences(text_stream):
                    pcm = await asyncio.to_thread(self._render, segment, voice)
                    await queue.put(pcm)
            except Exception as exc:
                await queue.put(exc)
            else:
                await queue.put(None)

        # Synthesize ahead in a task, so later sentences render while earlier audio is sent.
        producer = asyncio.create_task(produce())
        try:
            while (item := await queue.get()) is not None:
                if isinstance(item, BaseException):
                    raise item
                for start in range(0, len(item), CHUNK_BYTES):
                    yield item[start : start + CHUNK_BYTES]
        finally:
            producer.cancel()

    def _resolve(self, voice: VoiceConfig) -> VoiceConfig:
        if voice.engine == "kokoro" and self._kokoro is None:
            if not self._warned_unavailable:
                logger.warning(
                    "Kokoro is not loaded; speaking with Piper voice %s instead",
                    self._fallback_voice,
                )
                self._warned_unavailable = True
            return VoiceConfig("piper", self._fallback_voice, voice.speed, voice.pitch)
        engine = self._engine(voice.engine)
        if voice.voice not in {v.id for v in engine.voices()}:
            raise UnknownVoiceError(f"No {voice.engine} voice {voice.voice!r}")
        return voice

    def _engine(self, name: Engine) -> SynthesisEngine:
        engine = self._kokoro if name == "kokoro" else self._piper
        if engine is None:
            raise TTSUnavailableError(f"{name} is not loaded")
        return engine

    def _render(self, text: str, voice: VoiceConfig) -> bytes:
        """Synthesize one segment and convert it to output PCM. Runs in a worker thread."""
        # Pitch: synthesize slower by the pitch factor, then resample as if it had been
        # recorded at a higher rate. Duration ends up as requested; pitch moves by the factor.
        factor = voice.pitch_factor
        speed = voice.engine_speed
        with self._render_lock:
            started = time.perf_counter()
            try:
                audio, rate = self._engine(voice.engine).synthesize(text, voice.voice, speed)
            except Exception:
                if voice.engine != "kokoro":
                    raise
                # Length only: replies carry personal content and warnings are persisted.
                logger.warning(
                    "Kokoro failed on a %d-char segment; using Piper voice %s",
                    len(text),
                    self._fallback_voice,
                    exc_info=True,
                )
                audio, rate = self._piper.synthesize(text, self._fallback_voice, speed)
        logger.debug("TTS %d chars in %.0f ms", len(text), (time.perf_counter() - started) * 1000)
        return to_pcm16(audio, rate * factor)


def to_pcm16(audio: np.ndarray, sample_rate: float) -> bytes:
    """Resample float samples to `OUTPUT_SAMPLE_RATE`, add the segment pause, encode as PCM16."""
    if round(sample_rate) != OUTPUT_SAMPLE_RATE and len(audio):
        import soxr

        audio = soxr.resample(audio, sample_rate, OUTPUT_SAMPLE_RATE, quality="HQ")
    pause = np.zeros(int(SEGMENT_PAUSE_S * OUTPUT_SAMPLE_RATE), dtype=np.float32)
    audio = np.concatenate([np.asarray(audio, dtype=np.float32), pause])
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def resolve_model_dir(settings: Settings) -> Path:
    """Where TTS model files live: ALFRED_TTS_MODEL_DIR, default ~/.cache/alfred/tts."""
    if settings.tts_model_dir:
        return Path(settings.tts_model_dir).expanduser()
    return Path.home() / ".cache" / "alfred" / "tts"


def build_tts(settings: Settings) -> LocalTTS:
    """Load the engines. Kokoro failing to load is a warning (Piper takes over), not an error."""
    model_dir = resolve_model_dir(settings)
    piper = PiperEngine(model_dir)
    piper_ok = True
    try:
        piper.load(settings.tts_piper_voice)
    except Exception:
        piper_ok = False
        logger.warning(
            "Piper fallback voice %s failed to load", settings.tts_piper_voice, exc_info=True
        )

    kokoro: KokoroEngine | None = None
    try:
        kokoro = KokoroEngine.load(
            model_dir / settings.tts_kokoro_model,
            model_dir / settings.tts_kokoro_voices,
            settings.tts_kokoro_device,
        )
    except Exception as exc:
        logger.warning(
            "Kokoro failed to load (%s); falling back to Piper voice %s",
            exc,
            settings.tts_piper_voice,
        )
    if kokoro is None and not piper_ok:
        raise TTSUnavailableError(
            f"Neither Kokoro nor Piper could be loaded from {model_dir} "
            "(run scripts/download_tts_models.py)"
        )

    if settings.tts_engine == "kokoro" or not piper_ok:
        if settings.tts_engine == "piper":
            logger.warning(
                "Default Piper voice %s is unavailable; using Kokoro voice %s instead",
                settings.tts_piper_voice,
                settings.tts_kokoro_voice,
            )
        default = VoiceConfig("kokoro", settings.tts_kokoro_voice)
    else:
        default = VoiceConfig("piper", settings.tts_piper_voice)
    return LocalTTS(kokoro, piper, settings.tts_piper_voice, default)


@lru_cache
def get_tts() -> LocalTTS:
    """Return the process-wide TTS, loading the engines on first use."""
    return build_tts(get_settings())


def voices_response(tts: LocalTTS) -> dict[str, Any]:
    """Body of `GET /voices`."""
    return {
        "sample_rate": OUTPUT_SAMPLE_RATE,
        "default": asdict(tts.default_voice),
        "voices": [asdict(v) for v in tts.voices()],
    }


def _preload_cuda_libraries(rt: Any) -> None:
    """Load CUDA/cuDNN from the `cuda` extra's wheels, if installed (no-op otherwise)."""
    try:
        rt.preload_dlls()
    except Exception as exc:  # older onnxruntime, or no NVIDIA wheels: rely on system CUDA
        logger.debug("onnxruntime.preload_dlls() failed: %s", exc)


def write_wav(path: Path, pcm: bytes | Iterable[bytes]) -> None:
    """Write output PCM to a WAV file (used by the demo script and tests)."""
    import wave

    data = pcm if isinstance(pcm, bytes) else b"".join(pcm)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(OUTPUT_SAMPLE_RATE)
        wav.writeframes(data)
