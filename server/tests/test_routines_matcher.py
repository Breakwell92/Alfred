from datetime import UTC, datetime

import pytest

from alfred_server.routines.matcher import match, normalise
from alfred_server.routines.models import Routine
from tests.routine_helpers import movie_time

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def routine(name: str, *phrases: str, enabled: bool = True) -> Routine:
    spec = movie_time(name=name, phrases=list(phrases) or [name], enabled=enabled)
    return Routine(**spec.model_dump(), id=name, created_at=NOW, updated_at=NOW)


ROUTINES = [
    routine("movie time", "movie time", "cinema mode"),
    routine("bedtime", "bed time"),
    routine("lights on"),
    routine("lights off"),
]


def matched(text: str) -> str | None:
    found = match(text, ROUTINES)
    return found.name if found else None


def test_normalise() -> None:
    assert normalise("Hey Alfred, movie-time please!") == "movie time"
    assert normalise("Alfred… it’s BED TIME") == "its bed time"
    assert normalise("  ") == ""


@pytest.mark.parametrize(
    ("said", "name"),
    [
        ("movie time", "movie time"),
        ("Movie time.", "movie time"),
        ("Alfred, movie time please", "movie time"),
        ("hey alfred movie time", "movie time"),
        ("cinema mode", "movie time"),
        ("run movie time", "movie time"),
        ("start the movie time routine", "movie time"),
        ("bedtime", "bedtime"),
        ("movie times", "movie time"),  # an STT slip
        ("lights on", "lights on"),
        ("lights off", "lights off"),
    ],
)
def test_matches(said: str, name: str) -> None:
    assert matched(said) == name


@pytest.mark.parametrize(
    "said",
    [
        "movie time tomorrow at 8",
        "make a movie time routine",
        "when I say movie time dim the lights",
        "what's the time",
        "lights",
        "yes",
        "",
    ],
)
def test_near_misses_go_to_the_router(said: str) -> None:
    assert matched(said) is None


def test_disabled_routines_never_match() -> None:
    assert match("movie time", [routine("movie time", enabled=False)]) is None


def test_two_equally_close_routines_match_neither() -> None:
    routines = [routine("movie night"), routine("movie light")]
    assert match("movie nights", routines) is not None  # closest wins
    assert match("movie bight", routines) is None  # equally close: ask the router


@pytest.mark.parametrize(
    ("said", "saved"),
    [
        ("turn the heating on", "turn the heating off"),
        ("turn the heating off", "turn the heating on"),
        ("lounge lights on", "lounge lights off"),
        ("living room lights on", "living room lights off"),
        ("lights up", "lights down"),
        ("unlock everything", "lock everything"),
        ("movie time now", "movie time"),
    ],
)
def test_a_fuzzy_match_never_flips_or_changes_a_word(said: str, saved: str) -> None:
    assert match(said, [routine(saved)]) is None


@pytest.mark.parametrize(
    ("said", "saved"),
    [
        ("movie times", "movie time"),
        ("the movie time", "movie time"),
        ("bedtime", "bed time"),
        ("good mornin", "good morning"),
    ],
)
def test_fuzzy_matches_absorb_stt_slips(said: str, saved: str) -> None:
    found = match(said, [routine(saved)])
    assert found is not None and found.name == saved
