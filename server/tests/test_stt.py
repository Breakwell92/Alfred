import asyncio
import difflib
import json
import os
import re
import threading
import time
import wave
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from alfred_server.config import Settings
from alfred_server.voice.stt import FasterWhisperSTT, pcm16_to_float32, resolve_compute_type

AUDIO_DIR = Path(__file__).parent / "fixtures" / "audio"
TRANSCRIPTS: dict[str, str] = json.loads((AUDIO_DIR / "transcripts.json").read_text("utf-8"))


def read_pcm(name: str) -> bytes:
    with wave.open(str(AUDIO_DIR / name), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        return wav.readframes(wav.getnframes())


async def stream(data: bytes, chunk_size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(data), chunk_size):
        yield data[start : start + chunk_size]


def words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


class FakeModel:
    """Stands in for WhisperModel: records calls and tracks how many run at once."""

    def __init__(self, text: str = "hello there", delay: float = 0.0) -> None:
        self.text = text
        self.delay = delay
        self.calls: list[tuple[np.ndarray, dict[str, Any]]] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[Iterable[Any], Any]:
        with self._lock:
            self.calls.append((audio, kwargs))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        return [SimpleNamespace(text=f" {self.text} ")], SimpleNamespace(language="en")


def test_pcm16_to_float32_scales_to_unit_range() -> None:
    data = np.array([0, 16384, -32768, 32767], dtype="<i2").tobytes()

    audio = pcm16_to_float32(data)

    assert audio.dtype == np.float32
    np.testing.assert_allclose(audio, [0.0, 0.5, -1.0, 32767 / 32768])


def test_pcm16_to_float32_rejects_odd_byte_count() -> None:
    with pytest.raises(ValueError, match="even number of bytes"):
        pcm16_to_float32(b"\x00\x01\x02")


def test_chunks_split_mid_sample_are_reassembled() -> None:
    pcm = read_pcm("timer.wav")
    model = FakeModel()
    stt = FasterWhisperSTT(Settings(_env_file=None), model=model)

    transcript = asyncio.run(stt.transcribe(stream(pcm, chunk_size=333)))  # odd-sized chunks

    (audio, _), *_ = model.calls
    np.testing.assert_array_equal(audio, pcm16_to_float32(pcm))
    assert transcript.text == "hello there"
    assert transcript.audio_duration_s == pytest.approx(len(pcm) / 2 / 16000)


def test_decode_uses_language_and_prompt_settings() -> None:
    model = FakeModel()
    settings = Settings(
        _env_file=None, stt_language="en", stt_initial_prompt="Colour, favourite.", stt_beam_size=3
    )

    asyncio.run(FasterWhisperSTT(settings, model=model).transcribe(stream(b"\x00\x00" * 1600, 640)))

    _, kwargs = model.calls[0]
    assert kwargs["language"] == "en"
    assert kwargs["initial_prompt"] == "Colour, favourite."
    assert kwargs["beam_size"] == 3
    assert kwargs["vad_filter"] is False


def test_empty_audio_returns_empty_transcript_without_decoding() -> None:
    model = FakeModel()
    stt = FasterWhisperSTT(Settings(_env_file=None), model=model)

    transcript = asyncio.run(stt.transcribe(stream(b"", 640)))

    assert transcript.text == ""
    assert transcript.audio_duration_s == 0.0
    assert model.calls == []


@pytest.mark.parametrize("limit", [1, 2])
def test_concurrent_jobs_are_limited(limit: int) -> None:
    model = FakeModel(delay=0.05)
    stt = FasterWhisperSTT(Settings(_env_file=None, stt_max_concurrency=limit), model=model)

    async def run_four() -> None:
        await asyncio.gather(*(stt.transcribe(stream(b"\x00\x00" * 160, 320)) for _ in range(4)))

    asyncio.run(run_four())

    assert len(model.calls) == 4
    assert model.max_active == limit


def test_stt_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.stt_enabled is False
    assert settings.stt_language == "en"
    assert settings.stt_max_concurrency == 1


@pytest.mark.parametrize("value", [0, 3])
def test_stt_max_concurrency_is_bounded(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, stt_max_concurrency=value)


@pytest.mark.parametrize(
    ("setting", "device", "expected"),
    [
        ("auto", "cuda", "int8_float16"),
        ("auto", "cpu", "int8"),
        ("float16", "cuda", "float16"),
    ],
)
def test_resolve_compute_type(setting: str, device: str, expected: str) -> None:
    assert resolve_compute_type(setting, device) == expected


@pytest.mark.model
def test_transcribes_fixture_with_real_model() -> None:
    """Acceptance test: a real faster-whisper model transcribes a fixture WAV.

    Defaults to `tiny.en` on CPU so it runs in CI (downloads ~75 MB once). Point it at the
    production model with STT_TEST_MODEL / STT_TEST_DEVICE (not ALFRED_*: conftest clears those).
    """
    settings = Settings(
        _env_file=None,
        stt_model=os.environ.get("STT_TEST_MODEL", "tiny.en"),
        stt_device=os.environ.get("STT_TEST_DEVICE", "cpu"),
    )
    stt = FasterWhisperSTT(settings)

    transcript = asyncio.run(stt.transcribe(stream(read_pcm("lights.wav"), chunk_size=640)))

    expected = words(TRANSCRIPTS["lights.wav"])
    similarity = difflib.SequenceMatcher(None, words(transcript.text), expected).ratio()
    assert similarity >= 0.8, f"got {transcript.text!r}, expected {expected!r}"


def test_lifespan_loads_and_warms_stt_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from alfred_server import main
    from alfred_server.voice import stt as stt_module

    warmed: list[bool] = []
    fake = SimpleNamespace(warmup=lambda: warmed.append(True))
    monkeypatch.setattr(stt_module, "get_stt", lambda: fake)
    monkeypatch.setenv("ALFRED_STT_ENABLED", "true")

    with TestClient(main.create_app()):
        assert warmed == [True]


def test_lifespan_skips_stt_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from alfred_server import main
    from alfred_server.voice import stt as stt_module

    def fail() -> None:
        raise AssertionError("STT must not load when disabled")

    monkeypatch.setattr(stt_module, "get_stt", fail)

    with TestClient(main.create_app()) as client:
        assert client.get("/health").status_code == 200
