from types import SimpleNamespace

import pytest
import requests

from beautybydaz_woocommerce_sync.client import WooClient, WooError


class Response:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.content = b"x" if payload is not None else b""

    def json(self):
        return self._payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}
        self.auth = None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def channel():
    return SimpleNamespace(
        base_url="https://example.invalid",
        timeout_seconds=5,
        max_retries=2,
        get_password=lambda field: "hidden",
    )


def test_retry_then_success():
    waits = []
    session = Session([Response(503, text="busy"), Response(200, {"id": 1})])
    client = WooClient(channel(), session=session, sleeper=waits.append)
    assert client.get_product(1) == {"id": 1}
    assert len(session.calls) == 2
    assert waits == [1.0]


def test_network_retry_exhaustion():
    session = Session([requests.Timeout("x"), requests.Timeout("x"), requests.Timeout("x")])
    client = WooClient(channel(), session=session, sleeper=lambda _: None)
    with pytest.raises(WooError, match="after 3 attempts"):
        client.get_product(1)


def test_update_is_followed_by_get_readback():
    session = Session([Response(200, {"id": 1}), Response(200, {"id": 1, "regular_price": "50"})])
    client = WooClient(channel(), session=session, sleeper=lambda _: None)
    result = client.update_product(1, 0, {"regular_price": "50"})
    assert result["regular_price"] == "50"
    assert [call[0] for call in session.calls] == ["PUT", "GET"]
    assert session.calls[0][2]["json"] == {"regular_price": "50"}


def test_create_product_is_read_back_and_never_trusts_post_only():
    session = Session([Response(201, {"id": 99}), Response(200, {"id": 99, "status": "draft"})])
    client = WooClient(channel(), session=session, sleeper=lambda _: None)
    result = client.create_product({"name": "Test", "status": "draft"})
    assert result == {"id": 99, "status": "draft"}
    assert [call[0] for call in session.calls] == ["POST", "GET"]
