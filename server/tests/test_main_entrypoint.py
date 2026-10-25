from typing import Any

import pytest

from alfred_server import __main__ as entrypoint


@pytest.mark.parametrize(
    ("platform", "loop"), [("win32", "asyncio:SelectorEventLoop"), ("linux", "auto")]
)
def test_main_runs_uvicorn_with_settings(
    monkeypatch: pytest.MonkeyPatch, platform: str, loop: str
) -> None:
    monkeypatch.setattr(entrypoint.sys, "platform", platform)
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setenv("ALFRED_HOST", "0.0.0.0")
    monkeypatch.setenv("ALFRED_PORT", "9001")
    monkeypatch.setenv("ALFRED_LOG_LEVEL", "WARNING")

    entrypoint.main()

    assert calls == [
        (
            ("alfred_server.main:app",),
            {
                "host": "0.0.0.0",
                "port": 9001,
                "log_level": "warning",
                "log_config": entrypoint.log_config("WARNING"),
                "loop": loop,
            },
        )
    ]


def test_log_config_adds_the_apps_loggers() -> None:
    config = entrypoint.log_config("INFO")

    assert config["loggers"]["alfred_server"]["level"] == "INFO"
    assert config["loggers"]["alfred_server"]["handlers"] == ["alfred"]
    assert config["loggers"]["uvicorn"]["handlers"] == ["default"]  # uvicorn's own kept
    assert config["handlers"]["alfred"]["formatter"] == "alfred"
