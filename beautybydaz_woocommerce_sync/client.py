from __future__ import annotations

import time
from typing import Any

import requests

from .core import RETRYABLE_STATUS_CODES, product_endpoint, retry_delays, safe_summary


class WooError(RuntimeError):
    pass


class WooReadbackError(WooError):
    pass


class WooClient:
    def __init__(self, channel, session: requests.Session | None = None, sleeper=time.sleep):
        self.channel = channel
        self.base = channel.base_url.rstrip("/") + "/wp-json/wc/v3/"
        self.timeout = int(channel.timeout_seconds or 45)
        self.max_retries = int(channel.max_retries or 3)
        self.session = session or requests.Session()
        self.session.auth = (channel.get_password("consumer_key"), channel.get_password("consumer_secret"))
        self.session.headers.update({"User-Agent": "Convergenix-BBD-WooSync/1.0", "Accept": "application/json"})
        self.sleeper = sleeper

    def request(self, method: str, path: str, *, params=None, payload=None) -> Any:
        url = self.base + path.lstrip("/")
        attempts = self.max_retries + 1
        delays = retry_delays(self.max_retries)
        last_error = None
        for attempt in range(attempts):
            try:
                response = self.session.request(method, url, params=params, json=payload, timeout=self.timeout)
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                    self.sleeper(delays[attempt])
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise WooError(f"Woo HTTP {response.status_code} for {method} {path}: {response.text[:500]}")
                if not response.content:
                    return None
                return response.json()
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self.sleeper(delays[attempt])
        raise WooError(f"Woo request failed after {attempts} attempts for {method} {path}: {last_error}")

    def get_order(self, order_id: int | str) -> dict[str, Any]:
        return self.request("GET", f"orders/{int(order_id)}")

    def list_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.request("GET", "orders", params={"per_page": min(100, int(limit)), "orderby": "id", "order": "desc", "status": "any"}) or []

    def list_refunds(self, order_id: int | str) -> list[dict[str, Any]]:
        return self.request("GET", f"orders/{int(order_id)}/refunds", params={"per_page": 100}) or []

    def get_product(self, product_id: int | str, variation_id: int | str = 0) -> dict[str, Any]:
        return self.request("GET", product_endpoint(product_id, variation_id))

    def update_product(self, product_id: int | str, variation_id: int | str, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = product_endpoint(product_id, variation_id)
        self.request("PUT", endpoint, payload=payload)
        return self.request("GET", endpoint)

    def create_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        created = self.request("POST", "products", payload=payload)
        if not created or not created.get("id"):
            raise WooReadbackError(f"Woo create returned no product ID: {safe_summary(created)}")
        return self.get_product(created["id"], 0)
