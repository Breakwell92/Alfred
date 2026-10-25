from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alfred_server.chat import routine_service_dependency
from alfred_server.main import create_app
from alfred_server.routines.service import RoutineService
from tests.routine_helpers import FakeLoader, Policies, make_service, movie_time

TOKEN = "test-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Api:
    def __init__(self) -> None:
        self.service, self.loader, self.policies, _ = make_service()
        app = create_app()
        app.dependency_overrides[routine_service_dependency] = lambda: self.service
        self.client = TestClient(app)

    def body(self, **fields: Any) -> dict[str, Any]:
        return movie_time(**fields).model_dump(mode="json")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[Api]:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    yield Api()


def test_routes_need_the_token(api: Api) -> None:
    assert api.client.get("/routines").status_code == 401
    assert api.client.post("/routines", json=api.body()).status_code == 401
    assert api.client.post("/routines/x/run").status_code == 401


def test_crud(api: Api) -> None:
    created = api.client.post("/routines", json=api.body(), headers=AUTH)
    assert created.status_code == 201
    routine = created.json()
    assert routine["name"] == "movie time" and routine["id"]

    listed = api.client.get("/routines", headers=AUTH).json()
    assert [r["id"] for r in listed] == [routine["id"]]
    assert api.client.get(f"/routines/{routine['id']}", headers=AUTH).json() == routine

    changed = api.client.put(
        f"/routines/{routine['id']}", json=api.body(reply="Lights down."), headers=AUTH
    )
    assert changed.status_code == 200 and changed.json()["reply"] == "Lights down."

    assert api.client.delete(f"/routines/{routine['id']}", headers=AUTH).status_code == 204
    assert api.client.get(f"/routines/{routine['id']}", headers=AUTH).status_code == 404
    assert api.client.delete(f"/routines/{routine['id']}", headers=AUTH).status_code == 404


def test_invalid_routines_are_422(api: Api) -> None:
    unlock = {"connector": "home_assistant", "tool": "ha_unlock", "args": {"name": "front door"}}
    body = api.body() | {"steps": [unlock], "approved_tools": ["ha_unlock"]}
    response = api.client.post("/routines", json=body, headers=AUTH)
    assert response.status_code == 422
    assert "approval in the Alfred app" in response.json()["detail"]
    # Schema errors too (no steps).
    no_steps = api.body() | {"steps": []}
    assert api.client.post("/routines", json=no_steps, headers=AUTH).status_code == 422


def test_run(api: Api) -> None:
    routine = api.client.post("/routines", json=api.body(), headers=AUTH).json()
    response = api.client.post(f"/routines/{routine['id']}/run", headers=AUTH)
    assert response.status_code == 200
    ran = response.json()
    assert ran["reply"] == "Movie time."
    assert ran["trigger"] == "api"
    assert [s["ok"] for s in ran["steps"]] == [True, True]
    assert len(api.loader.ha.calls) == 2
    assert api.client.post("/routines/nope/run", headers=AUTH).status_code == 404


def test_run_is_503_without_the_policy(api: Api) -> None:
    routine = api.client.post("/routines", json=api.body(), headers=AUTH).json()
    api.policies.down = True
    assert api.client.post(f"/routines/{routine['id']}/run", headers=AUTH).status_code == 503
    assert api.loader.ha.calls == []


def test_storage_down_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    service = RoutineService(_BrokenStore(), Policies(), FakeLoader())  # type: ignore[arg-type]
    app = create_app()
    app.dependency_overrides[routine_service_dependency] = lambda: service
    client = TestClient(app)
    assert client.get("/routines", headers=AUTH).status_code == 503
    body = movie_time().model_dump(mode="json")
    assert client.post("/routines", json=body, headers=AUTH).status_code == 503


class _BrokenStore:
    def __getattr__(self, name: str) -> Any:
        def broken(*_args: Any) -> Any:
            raise RuntimeError("database down")

        return broken
