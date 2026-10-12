from __future__ import annotations

import json

import httpx
import pytest

from reseller_api.client import ResellerClient
from reseller_api.errors import ResellerAPIError
from tests.conftest import (
    BASE_URL,
    error_payload,
    invite_order_payload,
    invite_product_payload,
    me_payload,
    order_payload,
    product_payload,
    ticket_list_item_payload,
    ticket_payload,
)


def _json(request: httpx.Request) -> dict:
    return json.loads(request.content.decode()) if request.content else {}


def test_get_me(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == f"{BASE_URL}/v1/me"
        assert request.headers["Authorization"] == "Bearer rsk_test"
        return httpx.Response(200, json=me_payload())

    client, _ = make_client(handler)
    me = client.get_me()
    assert me.reseller_id == 7
    assert me.balance == "120.500000"
    assert me.telegram_id == 111
    assert me.telegram_username == "acme"


def test_list_products(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/products")
        return httpx.Response(
            200,
            json={"products": [product_payload(), invite_product_payload()]},
        )

    client, _ = make_client(handler)
    products = client.list_products()
    assert len(products) == 2
    assert products[0].your_unit_price == "22.500000"
    assert products[0].stock == 10
    assert products[1].stock is None
    assert products[1].inputs[0].name == "email"
    assert products[0].delivery_instructions == "Redeem at chatgpt.com"


def test_get_product(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/products/1")
        return httpx.Response(200, json=product_payload())

    client, _ = make_client(handler)
    product = client.get_product(1)
    assert product.id == 1
    assert product.product_type == "cdk"


def test_place_order(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/v1/orders")
        assert request.headers["Idempotency-Key"] == "idem-1"
        body = _json(request)
        assert body == {"product_id": 1, "quantity": 2, "inputs": {"email": "buyer@example.com"}}
        return httpx.Response(201, json=order_payload())

    client, _ = make_client(handler)
    order = client.place_order(
        1,
        quantity=2,
        inputs={"email": "buyer@example.com"},
        idempotency_key="idem-1",
    )
    assert order.order_id == 42
    assert order.delivered_codes == ("CODE-A", "CODE-B")
    assert order.status == "paid"
    assert order.idempotency_key == "idem-1"


def test_place_order_idempotency_replay(make_client) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["Idempotency-Key"] == "same-key"
        return httpx.Response(201, json=order_payload(idempotency_key="same-key"))

    client, _ = make_client(handler)
    first = client.place_order(1, quantity=2, idempotency_key="same-key")
    second = client.place_order(1, quantity=2, idempotency_key="same-key")
    assert first.order_id == second.order_id == 42
    assert calls["n"] == 2


def test_get_order(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/orders/42")
        return httpx.Response(200, json=order_payload())

    client, _ = make_client(handler)
    order = client.get_order(42)
    assert order.order_id == 42
    assert order.fulfillment_status is None


def test_list_orders(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["cursor"] == "41"
        assert request.url.params["limit"] == "10"
        return httpx.Response(
            200,
            json={"orders": [order_payload()], "next_cursor": 20},
        )

    client, _ = make_client(handler)
    page = client.list_orders(cursor=41, limit=10)
    assert len(page.orders) == 1
    assert page.next_cursor == 20


def test_resubmit_inputs(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/v1/orders/99/inputs")
        assert _json(request) == {"inputs": {"email": "fixed@example.com"}}
        return httpx.Response(
            200,
            json=invite_order_payload(
                fulfillment_status="processing",
                inputs={"email": "fixed@example.com"},
            ),
        )

    client, _ = make_client(handler)
    order = client.resubmit_inputs(99, {"email": "fixed@example.com"})
    assert order.fulfillment_status == "processing"
    assert order.inputs == {"email": "fixed@example.com"}


def test_poll_until_delivered_cdk(make_client) -> None:
    states = iter(
        [
            order_payload(status="pending", delivered_codes=[], delivered_count=0),
            order_payload(status="paid", delivered_codes=["CODE-A", "CODE-B"]),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(states))

    client, _ = make_client(handler)
    order = client.poll_until_delivered(42)
    assert order.delivered_codes == ("CODE-A", "CODE-B")


def test_poll_until_delivered_team_invite(make_client) -> None:
    states = iter(
        [
            invite_order_payload(fulfillment_status="processing"),
            invite_order_payload(
                fulfillment_status="delivered",
                status="delivered",
                delivered_count=1,
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(states))

    client, _ = make_client(handler)
    order = client.poll_until_delivered(99)
    assert order.fulfillment_status == "delivered"


def test_poll_until_delivered_input_rejected_returns(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=invite_order_payload(
                fulfillment_status="input_rejected",
                fulfillment_note="bad email",
            ),
        )

    client, _ = make_client(handler)
    order = client.poll_until_delivered(99)
    assert order.fulfillment_status == "input_rejected"


def test_poll_until_delivered_failed_raises(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=invite_order_payload(
                fulfillment_status="failed",
                fulfillment_note="operator could not fulfill",
            ),
        )

    client, _ = make_client(handler)
    with pytest.raises(ResellerAPIError) as exc:
        client.poll_until_delivered(99)
    assert exc.value.code == "fulfillment_failed"
    assert "operator could not fulfill" in exc.value.message


def test_open_ticket(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/v1/orders/99/tickets")
        assert _json(request) == {"message": "Need help"}
        return httpx.Response(201, json=ticket_payload())

    client, _ = make_client(handler)
    ticket = client.open_ticket(99, "Need help")
    assert ticket.ticket_id == 5
    assert ticket.messages[0].sender == "buyer"


def test_post_ticket_message(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/tickets/5/messages")
        assert _json(request) == {"message": "Follow up"}
        return httpx.Response(
            200,
            json=ticket_payload(
                messages=[
                    {
                        "id": 1,
                        "sender": "buyer",
                        "text": "Need help",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "id": 2,
                        "sender": "buyer",
                        "text": "Follow up",
                        "created_at": "2026-01-01T00:01:00Z",
                    },
                ]
            ),
        )

    client, _ = make_client(handler)
    ticket = client.post_ticket_message(5, "Follow up")
    assert len(ticket.messages) == 2


def test_get_ticket(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/tickets/5")
        return httpx.Response(
            200,
            json=ticket_payload(
                messages=[
                    {
                        "id": 1,
                        "sender": "buyer",
                        "text": "Need help",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "id": 3,
                        "sender": "operator",
                        "text": "Working on it",
                        "created_at": "2026-01-01T00:02:00Z",
                    },
                ]
            ),
        )

    client, _ = make_client(handler)
    ticket = client.get_ticket(5)
    assert ticket.messages[-1].sender == "operator"
    assert ticket.messages[-1].text == "Working on it"


def test_list_tickets(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["status"] == "open"
        assert request.url.params["order_id"] == "99"
        return httpx.Response(
            200,
            json={"tickets": [ticket_list_item_payload()], "next_cursor": None},
        )

    client, _ = make_client(handler)
    page = client.list_tickets(status="open", order_id=99)
    assert page.tickets[0].ticket_id == 5
    assert page.next_cursor is None


def test_close_ticket(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/v1/tickets/5/close")
        return httpx.Response(200, json=ticket_payload(status="closed", closed_at="2026-01-01T01:00:00Z"))

    client, _ = make_client(handler)
    ticket = client.close_ticket(5)
    assert ticket.status == "closed"


def test_typed_error_insufficient_balance(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json=error_payload(
                "insufficient_balance",
                "Balance $5.00 < required $22.50",
            ),
        )

    client, _ = make_client(handler)
    with pytest.raises(ResellerAPIError) as exc:
        client.place_order(1, quantity=1, idempotency_key="k")
    err = exc.value
    assert err.code == "insufficient_balance"
    assert err.message == "Balance $5.00 < required $22.50"
    assert err.request_id == "f05f...3db1"
    assert err.status_code == 402


def test_typed_error_missing_idempotency_key(make_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json=error_payload("missing_idempotency_key", "Idempotency-Key header required"),
        )

    client, _ = make_client(handler)
    with pytest.raises(ResellerAPIError) as exc:
        client.place_order(1)
    assert exc.value.code == "missing_idempotency_key"


def test_missing_api_key(monkeypatch) -> None:
    monkeypatch.delenv("RESELLER_API_KEY", raising=False)
    with pytest.raises(ResellerAPIError) as exc:
        ResellerClient()
    assert exc.value.code == "unauthorized"
