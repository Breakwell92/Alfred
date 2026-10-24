from typing import Any

import httpx
import pytest

from alfred_server.budget.prices import GatewayPrices, Price, PriceUnavailableError

MODEL_INFO: dict[str, Any] = {
    "data": [
        {
            "model_name": "cloud-claude",
            "litellm_params": {"model": "anthropic/claude-sonnet-5-5"},
            "model_info": {"input_cost_per_token": 2e-6, "output_cost_per_token": 1e-5},
        },
        {
            "model_name": "local-main",
            "litellm_params": {"model": "ollama_chat/gpt-oss:20b"},
            "model_info": {"input_cost_per_token": 0, "output_cost_per_token": 0},
        },
        # A second deployment of the same alias, dearer: estimates use the dearest.
        {
            "model_name": "cloud-gpt",
            "model_info": {"input_cost_per_token": 1e-6, "output_cost_per_token": 4e-6},
        },
        {
            "model_name": "cloud-gpt",
            "model_info": {"input_cost_per_token": 2e-6, "output_cost_per_token": 3e-6},
        },
        # Unpriced: never treated as free.
        {"model_name": "cloud-gemini", "model_info": {"input_cost_per_token": None}},
    ]
}


class Proxy:
    def __init__(self, status: int = 200, body: Any = MODEL_INFO) -> None:
        self.status = status
        self.body = body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def prices(proxy: Proxy, clock: Clock | None = None, key: str | None = "sk-test") -> GatewayPrices:
    return GatewayPrices(
        "http://litellm.test:4000/",
        key,
        http_client=httpx.Client(transport=httpx.MockTransport(proxy)),
        clock=clock or Clock(),
    )


def test_reads_prices_from_model_info_with_key() -> None:
    proxy = Proxy()

    price = prices(proxy).price("cloud-claude")

    assert price == Price(2e-6, 1e-5)
    assert price.cost(10_000, 1_000) == pytest.approx(0.03)
    (request,) = proxy.requests
    assert str(request.url) == "http://litellm.test:4000/model/info"
    assert request.headers["Authorization"] == "Bearer sk-test"


def test_dearest_deployment_wins() -> None:
    assert prices(Proxy()).price("cloud-gpt") == Price(2e-6, 4e-6)


def test_unpriced_alias_is_unavailable() -> None:
    with pytest.raises(PriceUnavailableError):
        prices(Proxy()).price("cloud-gemini")


def test_local_alias_is_free_without_a_request() -> None:
    proxy = Proxy()

    assert prices(proxy).price("local-router").cost(10**6, 10**6) == 0
    assert proxy.requests == []


def test_cached_for_an_hour() -> None:
    proxy, clock = Proxy(), Clock()
    source = prices(proxy, clock)

    source.price("cloud-claude")
    clock.t += 3599
    source.price("cloud-gpt")
    assert len(proxy.requests) == 1

    clock.t += 1
    source.price("cloud-claude")
    assert len(proxy.requests) == 2


@pytest.mark.parametrize(
    "proxy", [Proxy(status=401, body={"error": "bad key"}), Proxy(body={"oops": []})]
)
def test_proxy_errors_are_unavailable(proxy: Proxy) -> None:
    with pytest.raises(PriceUnavailableError):
        prices(proxy).price("cloud-claude")


def test_unreachable_proxy_is_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    source = GatewayPrices(
        "http://litellm.test:4000",
        "sk",
        http_client=httpx.Client(transport=httpx.MockTransport(refuse)),
    )
    with pytest.raises(PriceUnavailableError):
        source.price("cloud-claude")


def test_no_key_is_unavailable() -> None:
    with pytest.raises(PriceUnavailableError):
        prices(Proxy(), key=None).price("cloud-claude")
