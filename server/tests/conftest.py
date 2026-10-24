import os
from collections.abc import Iterator

import pytest

from alfred_server.config import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stop the developer's environment leaking into tests.

    Clears ALFRED_* vars from the shell and the cached settings, and stops `Settings` reading
    `.env` (a real `server/.env` holds the developer's tokens). Tests that need a value set an
    env var or pass it to `Settings(...)`; `Settings(_env_file=path)` still reads a file.
    """
    for var in list(os.environ):
        if var.startswith("ALFRED_"):
            monkeypatch.delenv(var)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
