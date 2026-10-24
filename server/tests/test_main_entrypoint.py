from typing import Any

import pytest

from alfred_server import __main__ as entrypoint


def test_main_runs_uvicorn_with_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setenv("ALFRED_HOST", "0.0.0.0")
    monkeypatch.setenv("ALFRED_PORT", "9001")
    monkeypatch.setenv("ALFRED_LOG_LEVEL", "WARNING")

    entrypoint.main()

    assert calls == [
        (
            ("alfred_server.main:app",),
            {"host": "0.0.0.0", "port": 9001, "log_level": "warning"},
        )
    ]
