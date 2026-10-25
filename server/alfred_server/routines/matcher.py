"""Matching an utterance to a routine's trigger phrase, before the router and without a model.

Only the whole utterance matches: "movie time", "Alfred, movie time please", "run movie time"
or an STT slip like "movie times". "Movie time tomorrow at 8" or "make a movie time routine"
don't, so they go to the router as usual.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

from alfred_server.routines.models import Routine

# A fuzzy match needs at least this similarity, and phrases this long.
FUZZY_RATIO = 0.9
FUZZY_MIN_CHARS = 5

_WAKE = re.compile(r"^(?:(?:hey|ok|okay)\s+)?alfred\s+")
_POLITE_START = re.compile(r"^please\s+")
_POLITE_END = re.compile(r"\s+(?:please|thanks|thank you|alfred)$")
_RUN = re.compile(r"^(?:run|start|do|activate|trigger)\s+(?:the\s+|my\s+)?(.+?)(?:\s+routine)?$")


def normalise(text: str) -> str:
    """Lower case, no punctuation, no wake word or "please": the form phrases are compared in."""
    text = text.lower().replace("’", "'").replace("'", "")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for _ in range(2):  # "hey alfred, please movie time please"
        text = _WAKE.sub("", text)
        text = _POLITE_START.sub("", text)
        text = _POLITE_END.sub("", text)
    return text


def _forms(text: str) -> list[str]:
    """The utterance, and what follows "run"/"start"/"do" if it starts that way."""
    said = normalise(text)
    forms = [said]
    run = _RUN.match(said)
    if run:
        forms.append(run.group(1))
    return [f for f in forms if f]


def _similar(a: str, b: str) -> float:
    if len(b) < FUZZY_MIN_CHARS or abs(len(a.split()) - len(b.split())) > 1:
        return 0.0
    return max(
        SequenceMatcher(None, a, b).ratio(),
        # "bedtime" for "bed time"
        SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", "")).ratio(),
    )


def match(text: str, routines: Iterable[Routine]) -> Routine | None:
    """The enabled routine `text` triggers, or None.

    An exact phrase wins. Otherwise the most similar phrase at `FUZZY_RATIO` or above, unless
    two routines are equally close (then nothing runs: better to ask the router).
    """
    forms = _forms(text)
    if not forms:
        return None
    candidates = [r for r in routines if r.enabled]
    for routine in candidates:
        phrases = {normalise(p) for p in routine.phrases}
        if any(f in phrases for f in forms):
            return routine
    best: Routine | None = None
    best_ratio = 0.0
    tied = False
    for routine in candidates:
        ratio = max(
            (_similar(f, normalise(p)) for f in forms for p in routine.phrases), default=0.0
        )
        if ratio < FUZZY_RATIO:
            continue
        if ratio > best_ratio:
            best, best_ratio, tied = routine, ratio, False
        elif ratio == best_ratio:
            tied = True
    return None if tied else best
