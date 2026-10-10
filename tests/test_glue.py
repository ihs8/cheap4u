from __future__ import annotations

from typing import Any

import httpx
import pytest

from reseller_api.errors import ResellerAPIError
from reseller_api.glue import Customer, place_and_deliver
from tests.conftest import (
    error_payload,
    invite_order_payload,
    invite_product_payload,
    order_payload,
    product_payload,
)


class RecordingCustomer(Customer):
    def __init__(self, corrections: list[dict[str, Any]] | None = None) -> None:
        super().__init__(id="buyer-1")
        self.notes: list[str] = []
        self.corrections = list(corrections or [])

    def notify(self, message: str) -> None:
        self.notes.append(message)

    def ask_corrected_input(
        self,
        field_name: str,
        note: str | None,
        current: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not self.corrections:
            raise AssertionError("no correction queued")
        return self.corrections.pop(0)


def test_place_and_deliver_cdk_sync(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/products/1"):
            return httpx.Response(200, json=product_payload())
        if request.url.path.endswith("/v1/orders"):
            return httpx.Response(201, json=order_payload())
        raise AssertionError(str(request.url))

    client, _ = make_client(handler)
    customer = RecordingCustomer()
    result = place_and_deliver(customer, 1, qty=2, client=client)
    assert result.delivered_codes == ("CODE-A", "CODE-B")
    assert result.delivery_instructions == "Redeem at chatgpt.com"
    assert customer.notes == ["Your codes are ready."]


def test_place_and_deliver_team_invite_polls(make_client) -> None:
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/products/2"):
            return httpx.Response(200, json=invite_product_payload())
        if request.method == "POST" and request.url.path.endswith("/v1/orders"):
            return httpx.Response(
                201,
                json=invite_order_payload(fulfillment_status="processing"),
            )
        if request.url.path.endswith("/v1/orders/99"):
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(
                    200,
                    json=invite_order_payload(fulfillment_status="processing"),
                )
            return httpx.Response(
                200,
                json=invite_order_payload(
                    fulfillment_status="delivered",
                    status="delivered",
                    delivered_count=1,
                ),
            )
        raise AssertionError(str(request.url))

    client, _ = make_client(handler)
    customer = RecordingCustomer()
    result = place_and_deliver(
        customer,
        2,
        qty=1,
        inputs={"email": "buyer@example.com"},
        client=client,
    )
    assert result.fulfillment_status == "delivered"
    assert result.delivery_instructions == "Accept the invite in your inbox"
    assert "delivered" in customer.notes[-1].lower()


def test_place_and_deliver_input_rejected_then_resubmit(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/products/2"):
            return httpx.Response(200, json=invite_product_payload())
        if request.method == "POST" and request.url.path.endswith("/v1/orders"):
            return httpx.Response(
                201,
                json=invite_order_payload(
                    fulfillment_status="input_rejected",
                    fulfillment_note="unknown email",
                ),
            )
        if request.method == "POST" and request.url.path.endswith("/v1/orders/99/inputs"):
            return httpx.Response(
                200,
                json=invite_order_payload(
                    fulfillment_status="processing",
                    inputs={"email": "fixed@example.com"},
                ),
            )
        if request.url.path.endswith("/v1/orders/99"):
            return httpx.Response(
                200,
                json=invite_order_payload(
                    fulfillment_status="delivered",
                    status="delivered",
                    inputs={"email": "fixed@example.com"},
                ),
            )
        raise AssertionError(str(request.url))

    client, _ = make_client(handler)
    customer = RecordingCustomer(corrections=[{"email": "fixed@example.com"}])
    result = place_and_deliver(
        customer,
        2,
        inputs={"email": "bad@example.com"},
        client=client,
    )
    assert result.fulfillment_status == "delivered"
    assert any("couldn't be used" in note or "unknown email" in note for note in customer.notes)


def test_place_and_deliver_failed_surfaces_support(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/products/2"):
            return httpx.Response(200, json=invite_product_payload())
        if request.method == "POST" and request.url.path.endswith("/v1/orders"):
            return httpx.Response(
                201,
                json=invite_order_payload(fulfillment_status="failed", fulfillment_note="out of invites"),
            )
        raise AssertionError(str(request.url))

    client, _ = make_client(handler)
    customer = RecordingCustomer()
    result = place_and_deliver(customer, 2, inputs={"email": "buyer@example.com"}, client=client)
    assert result.fulfillment_status == "failed"
    assert result.support_message == "out of invites"
    assert "out of invites" in customer.notes


def test_place_and_deliver_insufficient_balance(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/products/1"):
            return httpx.Response(200, json=product_payload())
        if request.url.path.endswith("/v1/orders"):
            return httpx.Response(
                402,
                json=error_payload(
                    "insufficient_balance",
                    "Balance $5.00 < required $22.50",
                ),
            )
        raise AssertionError(str(request.url))

    client, _ = make_client(handler)
    customer = RecordingCustomer()
    with pytest.raises(ResellerAPIError) as exc:
        place_and_deliver(customer, 1, qty=1, client=client)
    assert exc.value.code == "insufficient_balance"
    assert exc.value.request_id == "f05f...3db1"
    assert customer.notes
