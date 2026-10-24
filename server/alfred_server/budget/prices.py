"""Per-alias token prices, from the LiteLLM proxy's `GET /model/info`.

The proxy reports each deployment's `input_cost_per_token` / `output_cost_per_token` (USD)
from the same cost map it bills with, so estimates match what the spend logs will record.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from alfred_server.models.gateway import LOCAL_ALIASES, ModelAlias

PRICE_TTL_S = 3600.0


class PriceUnavailableError(RuntimeError):
    """No price is known for an alias (proxy unreachable, or the alias isn't configured)."""


@dataclass(frozen=True)
class Price:
    input_per_token: float
    output_per_token: float

    def cost(self, prompt_tokens: int, output_tokens: int) -> float:
        return prompt_tokens * self.input_per_token + output_tokens * self.output_per_token


FREE = Price(0.0, 0.0)


class GatewayPrices:
    """Fetches and caches alias prices from the proxy. Local aliases are free without a call."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        *,
        http_client: httpx.Client | None = None,
        ttl_s: float = PRICE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = base_url.rstrip("/") + "/model/info"
        self.api_key = api_key
        self.http = http_client or httpx.Client(timeout=5.0)
        self.ttl_s = ttl_s
        self.clock = clock
        self._prices: dict[str, Price] | None = None
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> dict[str, Price]:
        if not self.api_key:
            raise PriceUnavailableError("ALFRED_LITELLM_API_KEY is not set")
        try:
            response = self.http.get(self.url, headers={"Authorization": f"Bearer {self.api_key}"})
            response.raise_for_status()
            entries = response.json()["data"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise PriceUnavailableError(f"can't read prices from LiteLLM: {exc}") from exc
        prices: dict[str, Price] = {}
        for entry in entries:
            info = entry.get("model_info") or {}
            name = entry.get("model_name")
            try:
                price = Price(
                    float(info["input_cost_per_token"]), float(info["output_cost_per_token"])
                )
            except (KeyError, TypeError, ValueError):
                continue  # unpriced deployment: treated as unknown, never as free
            # Several deployments of one alias: estimate with the dearest.
            if name in prices:
                old = prices[name]
                price = Price(
                    max(old.input_per_token, price.input_per_token),
                    max(old.output_per_token, price.output_per_token),
                )
            prices[name] = price
        return prices

    def price(self, alias: ModelAlias) -> Price:
        if alias in LOCAL_ALIASES:
            return FREE
        with self._lock:
            if self._prices is None or self.clock() - self._fetched_at >= self.ttl_s:
                self._prices = self._fetch()
                self._fetched_at = self.clock()
            prices = self._prices
        if alias not in prices:
            raise PriceUnavailableError(f"no price for {alias!r}: not configured on the gateway")
        return prices[alias]
