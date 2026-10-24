import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from alfred_server.config import Settings
from alfred_server.main import create_app, tts_dependency
from alfred_server.voice.tts import (
    CHUNK_BYTES,
    OUTPUT_SAMPLE_RATE,
    SEGMENT_PAUSE_S,
    Engine,
    KokoroEngine,
    LocalTTS,
    PiperEngine,
    SentenceSplitter,
    TTSUnavailableError,
    UnknownVoiceError,
    VoiceConfig,
    VoiceInfo,
    build_tts,
    resolve_model_dir,
    split_sentences,
)

TOKEN = "test-token-not-a-secret"

Splitter = Callable[..., list[str]]


def split(text: str, **kwargs: int) -> list[str]:
    splitter = SentenceSplitter(**kwargs)
    return splitter.feed(text) + splitter.flush()


def split_streamed(text: str, **kwargs: int) -> list[str]:
    """Feed one character at a time, like a token stream at its most fragmented."""
    splitter = SentenceSplitter(**kwargs)
    segments = [s for char in text for s in splitter.feed(char)]
    return segments + splitter.flush()


# --- Sentence splitter -----------------------------------------------------------------------


@pytest.mark.parametrize("splitter", [split, split_streamed])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello there. How are you?", ["Hello there.", "How are you?"]),
        ("Stop! Wait? Go.", ["Stop!", "Wait?", "Go."]),
        ("Really?! Yes.", ["Really?!", "Yes."]),
        ("Well... I suppose so.", ["Well...", "I suppose so."]),
        ("Hmm… Maybe later.", ["Hmm…", "Maybe later."]),
        ('He said "go home." Then he left.', ['He said "go home."', "Then he left."]),
        ("(That was easy.) Next one.", ["(That was easy.)", "Next one."]),
        ("No final punctuation", ["No final punctuation"]),
        ("Line one\nLine two", ["Line one", "Line two"]),
    ],
)
def test_splits_on_sentence_punctuation(splitter: Splitter, text: str, expected: list[str]) -> None:
    assert splitter(text) == expected


@pytest.mark.parametrize("splitter", [split, split_streamed])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mr. Smith is here. He waited.", ["Mr. Smith is here.", "He waited."]),
        ("Ask Dr. Jones and Mrs. Patel.", ["Ask Dr. Jones and Mrs. Patel."]),
        ("Use e.g. butter or i.e. fat. Fine.", ["Use e.g. butter or i.e. fat.", "Fine."]),
        ("It's on Baker St. now. Go.", ["It's on Baker St. now.", "Go."]),
        ("J. R. R. Tolkien wrote it. Yes.", ["J. R. R. Tolkien wrote it.", "Yes."]),
        ("Arsenal vs. Spurs tonight.", ["Arsenal vs. Spurs tonight."]),
        # A lowercase next word means the "." didn't end the sentence.
        ("Meet at 5 p.m. tomorrow. OK.", ["Meet at 5 p.m. tomorrow.", "OK."]),
        ("Apples, pears etc. are fine.", ["Apples, pears etc. are fine."]),
    ],
)
def test_keeps_abbreviations_inside_sentences(
    splitter: Splitter, text: str, expected: list[str]
) -> None:
    assert splitter(text) == expected


@pytest.mark.parametrize("splitter", [split, split_streamed])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("It costs £3.50 today. Cheap.", ["It costs £3.50 today.", "Cheap."]),
        ("Pi is 3.14159. Roughly.", ["Pi is 3.14159.", "Roughly."]),
        ("That's 1,250 people. Wow.", ["That's 1,250 people.", "Wow."]),
        ("It was built in 1999. It still works.", ["It was built in 1999.", "It still works."]),
        ("Version 2.0.1 is out.", ["Version 2.0.1 is out."]),
        ("1. Buy milk\n2. Feed the cat", ["1. Buy milk", "2. Feed the cat"]),
    ],
)
def test_handles_numbers(splitter: Splitter, text: str, expected: list[str]) -> None:
    assert splitter(text) == expected


def test_first_segment_breaks_at_a_clause_for_early_audio() -> None:
    text = "Right then, the heating is now on, and the lights are off. Anything else, sir?"

    assert split(text) == [
        "Right then, the heating is now on,",
        "and the lights are off.",
        "Anything else, sir?",
    ]


def test_short_clause_does_not_break() -> None:
    assert split("Yes, sir. Done.") == ["Yes, sir.", "Done."]


def test_clause_break_on_spaced_dash() -> None:
    assert split("The front door is locked — the back one is not.") == [
        "The front door is locked —",
        "the back one is not.",
    ]


def test_sentence_is_emitted_before_the_stream_ends() -> None:
    splitter = SentenceSplitter()

    assert splitter.feed("Good evening, sir. ") == []  # waiting: is "sir." the end?
    assert splitter.feed("The") == ["Good evening, sir."]
    assert splitter.feed(" kettle is on.") == []
    assert splitter.flush() == ["The kettle is on."]


def test_question_mark_does_not_wait_for_the_next_word() -> None:
    assert SentenceSplitter().feed("Shall I? ") == ["Shall I?"]


def test_long_text_without_punctuation_is_cut() -> None:
    text = " ".join(["word"] * 100)

    segments = split(text, max_chars=50)

    assert all(len(s) <= 50 for s in segments)
    assert " ".join(segments) == text


def test_skips_segments_with_nothing_to_say() -> None:
    assert split("Hello.\n\n---\n**\nBye.") == ["Hello.", "Bye."]


def test_collapses_whitespace() -> None:
    assert split("Hello   there.\tHow  are you?") == ["Hello there.", "How are you?"]


async def tokens(*parts: str, delay: float = 0.0) -> AsyncIterator[str]:
    for part in parts:
        if delay:
            await asyncio.sleep(delay)
        yield part


def test_split_sentences_streams() -> None:
    async def run() -> list[str]:
        return [s async for s in split_sentences(tokens("One. ", "Two", ". Three"))]

    assert asyncio.run(run()) == ["One.", "Two.", "Three"]


# --- Voice config ----------------------------------------------------------------------------


@pytest.mark.parametrize(("speed", "pitch"), [(0.4, 0.0), (2.1, 0.0), (1.0, 7.0), (1.0, -7.0)])
def test_voice_config_rejects_out_of_range(speed: float, pitch: float) -> None:
    with pytest.raises(ValueError):
        VoiceConfig("kokoro", "bm_george", speed=speed, pitch=pitch)


# --- Streaming TTS with fake engines ---------------------------------------------------------


class FakeEngine:
    """Returns a 440 Hz tone, 10 ms per character of text, at the given sample rate."""

    def __init__(
        self, name: Engine, voices: list[str], sample_rate: int, fail: bool = False
    ) -> None:
        self.name: Engine = name
        self._voices = voices
        self.sample_rate = sample_rate
        self.fail = fail
        self.calls: list[tuple[str, str, float]] = []

    def voices(self) -> list[VoiceInfo]:
        return [VoiceInfo(self.name, v, "en-GB") for v in self._voices]

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        self.calls.append((text, voice, speed))
        if self.fail:
            raise RuntimeError("engine broke")
        n = int(len(text) * 0.01 * self.sample_rate / speed)
        t = np.arange(n) / self.sample_rate
        return (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), self.sample_rate


def make_tts(kokoro: FakeEngine | None = None, piper: FakeEngine | None = None) -> LocalTTS:
    if kokoro is None:
        kokoro = FakeEngine("kokoro", ["bm_george", "af_heart"], 24_000)
    if piper is None:
        piper = FakeEngine("piper", ["en_GB-alan-medium"], 22_050)
    return LocalTTS(kokoro, piper, "en_GB-alan-medium", VoiceConfig("kokoro", "bm_george"))


def speak(tts: LocalTTS, voice: VoiceConfig, *parts: str) -> list[bytes]:
    async def run() -> list[bytes]:
        return [chunk async for chunk in tts.synthesize(tokens(*parts), voice)]

    return asyncio.run(run())


def samples_for(text: str, rate: int = 24_000) -> int:
    """Expected output samples for one FakeEngine segment at speed 1."""
    return round(int(len(text) * 0.01 * rate) * OUTPUT_SAMPLE_RATE / rate) + int(
        SEGMENT_PAUSE_S * OUTPUT_SAMPLE_RATE
    )


def test_synthesizes_each_sentence_as_24khz_pcm16() -> None:
    kokoro = FakeEngine("kokoro", ["bm_george"], 24_000)
    tts = make_tts(kokoro=kokoro)

    chunks = speak(tts, VoiceConfig("kokoro", "bm_george"), "Good evening. ", "All is well.")

    assert [c[0] for c in kokoro.calls] == ["Good evening.", "All is well."]
    assert all(len(c) <= CHUNK_BYTES and len(c) % 2 == 0 for c in chunks)
    total = sum(len(c) for c in chunks) // 2
    assert total == samples_for("Good evening.") + samples_for("All is well.")
    audio = np.frombuffer(b"".join(chunks), dtype="<i2")
    assert 0 < np.abs(audio).max() <= 32767


def test_resamples_piper_to_24khz() -> None:
    piper = FakeEngine("piper", ["en_GB-alan-medium"], 22_050)
    tts = make_tts(piper=piper)

    chunks = speak(tts, VoiceConfig("piper", "en_GB-alan-medium"), "One sentence here.")

    total = sum(len(c) for c in chunks) // 2
    assert abs(total - samples_for("One sentence here.", 22_050)) <= 2


def test_pitch_keeps_duration_and_changes_engine_speed() -> None:
    kokoro = FakeEngine("kokoro", ["bm_george"], 24_000)
    tts = make_tts(kokoro=kokoro)

    plain = speak(tts, VoiceConfig("kokoro", "bm_george"), "Test sentence.")
    higher = speak(tts, VoiceConfig("kokoro", "bm_george", pitch=3.0), "Test sentence.")

    assert kokoro.calls[1][2] == pytest.approx(1 / 2 ** (3 / 12))
    plain_len = sum(map(len, plain))
    assert abs(sum(map(len, higher)) - plain_len) <= 0.01 * plain_len


def test_speed_is_passed_to_the_engine() -> None:
    kokoro = FakeEngine("kokoro", ["bm_george"], 24_000)

    speak(make_tts(kokoro=kokoro), VoiceConfig("kokoro", "bm_george", speed=1.25), "Quick.")

    assert kokoro.calls[0][2] == pytest.approx(1.25)


def test_first_audio_arrives_before_the_text_stream_ends() -> None:
    tts = make_tts()

    async def run() -> tuple[float, float]:
        started = time.perf_counter()
        first = None
        stream = tokens("Hello there. ", "And", " then", " more", " text.", delay=0.2)
        async for _ in tts.synthesize(stream, VoiceConfig("kokoro", "bm_george")):
            if first is None:
                first = time.perf_counter() - started
        return first or 0.0, time.perf_counter() - started

    first, total = asyncio.run(run())

    assert first < 0.6  # after the second token, not after all five
    assert total >= 1.0


def test_unknown_voice_is_rejected() -> None:
    with pytest.raises(UnknownVoiceError):
        speak(make_tts(), VoiceConfig("kokoro", "xx_nobody"), "Hello.")


def test_falls_back_to_piper_when_kokoro_is_not_loaded(caplog: pytest.LogCaptureFixture) -> None:
    piper = FakeEngine("piper", ["en_GB-alan-medium"], 22_050)
    tts = LocalTTS(None, piper, "en_GB-alan-medium", VoiceConfig("kokoro", "bm_george"))

    with caplog.at_level(logging.WARNING):
        chunks = speak(tts, VoiceConfig("kokoro", "bm_george", speed=1.1), "Hello.")

    assert chunks
    assert piper.calls == [("Hello.", "en_GB-alan-medium", pytest.approx(1.1))]
    assert "Kokoro is not loaded" in caplog.text
    assert [v.engine for v in tts.voices()] == ["piper"]


def test_falls_back_to_piper_when_kokoro_fails_on_a_sentence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    kokoro = FakeEngine("kokoro", ["bm_george"], 24_000, fail=True)
    piper = FakeEngine("piper", ["en_GB-alan-medium"], 22_050)

    with caplog.at_level(logging.WARNING):
        chunks = speak(make_tts(kokoro, piper), VoiceConfig("kokoro", "bm_george"), "Hi there.")

    assert chunks
    assert [c[0] for c in piper.calls] == ["Hi there."]
    assert "Kokoro failed" in caplog.text


def test_piper_errors_propagate() -> None:
    piper = FakeEngine("piper", ["en_GB-alan-medium"], 22_050, fail=True)

    with pytest.raises(RuntimeError, match="engine broke"):
        speak(make_tts(piper=piper), VoiceConfig("piper", "en_GB-alan-medium"), "Hi.")


def test_empty_text_gives_no_audio() -> None:
    assert speak(make_tts(), VoiceConfig("kokoro", "bm_george"), "", "  ") == []


# --- Engine wrappers and loading -------------------------------------------------------------


class FakeKokoro:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_voices(self) -> list[str]:
        return ["af_heart", "bm_george", "xx_unknown"]

    def create(
        self, text: str, voice: str, speed: float = 1.0, lang: str = "en-us"
    ) -> tuple[np.ndarray, int]:
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        return np.zeros(240, dtype=np.float32), 24_000


def test_kokoro_engine_lists_voices_and_picks_language() -> None:
    model = FakeKokoro()
    engine = KokoroEngine(model)

    assert engine.voices() == [
        VoiceInfo("kokoro", "af_heart", "en-US", "female"),
        VoiceInfo("kokoro", "bm_george", "en-GB", "male"),
    ]
    engine.synthesize("Hello.", "bm_george", 1.0)
    assert model.calls[-1]["lang"] == "en-gb"


def write_piper_voice(model_dir: Path, name: str) -> None:
    (model_dir / f"{name}.onnx").write_bytes(b"")
    (model_dir / f"{name}.onnx.json").write_text("{}")


def test_piper_engine_finds_voices(tmp_path: Path) -> None:
    write_piper_voice(tmp_path, "en_GB-alan-medium")
    write_piper_voice(tmp_path, "en_US-lessac-medium")
    (tmp_path / "en_GB-orphan-low.onnx.json").write_text("{}")  # no model file
    (tmp_path / "kokoro-v1.0.onnx").write_bytes(b"")  # no config: not a Piper voice

    assert PiperEngine(tmp_path).voices() == [
        VoiceInfo("piper", "en_GB-alan-medium", "en-GB"),
        VoiceInfo("piper", "en_US-lessac-medium", "en-US"),
    ]
    with pytest.raises(UnknownVoiceError):
        PiperEngine(tmp_path).load("en_GB-missing-medium")


def test_model_dir_defaults_to_user_cache(tmp_path: Path) -> None:
    assert resolve_model_dir(Settings(_env_file=None)) == Path.home() / ".cache/alfred/tts"
    settings = Settings(_env_file=None, tts_model_dir=str(tmp_path))
    assert resolve_model_dir(settings) == tmp_path


def test_build_tts_falls_back_to_piper_when_kokoro_fails_to_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    write_piper_voice(tmp_path, "en_GB-alan-medium")
    monkeypatch.setattr(PiperEngine, "load", lambda self, voice: object())
    settings = Settings(_env_file=None, tts_model_dir=str(tmp_path))

    with caplog.at_level(logging.WARNING):
        tts = build_tts(settings)  # no Kokoro files in tmp_path

    assert "Kokoro failed to load" in caplog.text
    assert "falling back to Piper voice en_GB-alan-medium" in caplog.text
    assert [v.id for v in tts.voices()] == ["en_GB-alan-medium"]
    assert tts.default_voice == VoiceConfig("piper", "en_GB-alan-medium")


def test_build_tts_fails_when_no_engine_loads(tmp_path: Path) -> None:
    with pytest.raises(TTSUnavailableError):
        build_tts(Settings(_env_file=None, tts_model_dir=str(tmp_path)))


def test_build_tts_uses_kokoro_when_it_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_piper_voice(tmp_path, "en_GB-alan-medium")
    monkeypatch.setattr(PiperEngine, "load", lambda self, voice: object())
    monkeypatch.setattr(KokoroEngine, "load", classmethod(lambda cls, *args: cls(FakeKokoro())))

    tts = build_tts(Settings(_env_file=None, tts_model_dir=str(tmp_path), tts_engine="kokoro"))

    assert {v.engine for v in tts.voices()} == {"kokoro", "piper"}
    assert tts.default_voice == VoiceConfig("kokoro", "bm_george")


# --- GET /voices -----------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    app = create_app()
    app.dependency_overrides[tts_dependency] = lambda: make_tts()
    return TestClient(app)


def test_voices_lists_kokoro_and_piper(client: TestClient) -> None:
    response = client.get("/voices", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    body = response.json()
    assert body["sample_rate"] == 24_000
    assert body["default"] == {"engine": "kokoro", "voice": "bm_george", "speed": 1.0, "pitch": 0.0}
    assert {(v["engine"], v["id"]) for v in body["voices"]} == {
        ("kokoro", "bm_george"),
        ("kokoro", "af_heart"),
        ("piper", "en_GB-alan-medium"),
    }


def test_voices_requires_token(client: TestClient) -> None:
    assert client.get("/voices").status_code == 401


def test_voices_unavailable_when_tts_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    client = TestClient(create_app())

    response = client.get("/voices", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 503


# --- Real engines (need the model files: scripts/download_tts_models.py) --------------------


def installed_settings() -> Settings:
    settings = Settings(_env_file=None)
    model_dir = resolve_model_dir(settings)
    if not (model_dir / settings.tts_kokoro_model).is_file():
        pytest.skip(f"TTS models not downloaded to {model_dir}")
    return settings


@pytest.mark.model
def test_real_engines_speak() -> None:
    settings = installed_settings()
    tts = build_tts(settings)

    for voice in (
        VoiceConfig("kokoro", settings.tts_kokoro_voice),
        VoiceConfig("piper", settings.tts_piper_voice),
    ):
        audio = b"".join(speak(tts, voice, "Good evening, sir. ", "The kettle is on."))
        seconds = len(audio) / 2 / OUTPUT_SAMPLE_RATE
        assert 1.0 < seconds < 6.0, voice
        assert np.abs(np.frombuffer(audio, dtype="<i2")).max() > 1000, voice


@pytest.mark.model
def test_real_kokoro_load_failure_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    settings = installed_settings().model_copy(update={"tts_kokoro_model": "missing.onnx"})

    with caplog.at_level(logging.WARNING):
        tts = build_tts(settings)
        audio = speak(tts, VoiceConfig("kokoro", "bm_george"), "Still talking.")

    assert audio
    assert "falling back to Piper" in caplog.text
