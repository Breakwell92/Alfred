import os
from collections.abc import Iterator

import pytest

from alfred_server.config import get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stop ALFRED_* vars from the developer's shell, and cached settings, leaking into tests."""
    for var in list(os.environ):
        if var.startswith("ALFRED_"):
            monkeypatch.delenv(var)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
