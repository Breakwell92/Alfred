from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from alfred_server.budget.service import BudgetService, get_budget_service
from alfred_server.main import create_app
from tests.budget_helpers import NOW, FakeSpend, make_service

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def service() -> BudgetService:
    spend = FakeSpend()
    spend.add(2.5, NOW - timedelta(hours=1), agent="router", task_id="t1")
    return make_service(spend=spend)


@pytest.fixture
def client(service: BudgetService, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    app = create_app()
    app.dependency_overrides[get_budget_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/settings/budget"), ("PUT", "/settings/budget"), ("GET", "/spend/summary")],
)
def test_routes_require_token(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 401
    assert client.request(method, path, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_routes_fail_closed_without_configured_token(service: BudgetService) -> None:
    app = create_app()
    app.dependency_overrides[get_budget_service] = lambda: service

    assert TestClient(app).get("/settings/budget", headers=AUTH).status_code == 503


def test_get_returns_seed_defaults(client: TestClient) -> None:
    body = client.get("/settings/budget", headers=AUTH).json()

    assert body["cloud_enabled"] is False
    assert body["monthly_cap_gbp"] == 20
    assert body["thresholds"] == {"restricted": 0.7, "exhausted": 1.0}
    assert body["providers"] == {"claude": True, "gpt": True, "gemini": True}
    assert body["provider_preferences"]["summary"] == "cheapest"


def test_put_then_get_round_trips(client: TestClient) -> None:
    new = client.get("/settings/budget", headers=AUTH).json()
    new.update(cloud_enabled=True, monthly_cap_gbp=30)
    new["providers"]["gpt"] = False

    put = client.put("/settings/budget", json=new, headers=AUTH)

    assert put.status_code == 200
    assert put.json() == new
    assert client.get("/settings/budget", headers=AUTH).json() == new


@pytest.mark.parametrize(
    "bad",
    [
        {"monthly_cap_gbp": -1},
        {"thresholds": {"restricted": 0.9, "exhausted": 0.5}},
        {"providers": {"llama": True}},
        {"provider_preferences": {"summary": ["mistral"]}},
    ],
)
def test_put_rejects_invalid_settings(client: TestClient, bad: dict[str, object]) -> None:
    response = client.put("/settings/budget", json=bad, headers=AUTH)

    assert response.status_code == 422
    assert client.get("/settings/budget", headers=AUTH).json()["monthly_cap_gbp"] == 20


def test_spend_summary(client: TestClient) -> None:
    response = client.get("/spend/summary", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["month"] == "2026-10"
    assert body["month_to_date_gbp"] == 2.5
    assert body["today_gbp"] == 2.5
    assert body["remaining_gbp"] == 17.5
    assert body["by_provider"] == {"claude": 2.5}
    assert body["by_agent"] == {"router": 2.5}
    assert body["by_task_id"] == {"t1": 2.5}


def test_spend_summary_unavailable(client: TestClient, service: BudgetService) -> None:
    service.spend.fail = True  # type: ignore[attr-defined]

    assert client.get("/spend/summary", headers=AUTH).status_code == 503
