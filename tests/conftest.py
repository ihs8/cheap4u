from __future__ import annotations

from typing import Any

import httpx
import pytest

from reseller_api.client import ResellerClient

BASE_URL = "https://cgpt-active.pro/telegram/api"


def me_payload() -> dict[str, Any]:
    return {
        "reseller_id": 7,
        "name": "Acme Resale",
        "discount_percent": "10.00",
        "balance": "120.500000",
        "api_key_prefix": "rsk_abcd",
        "telegram_id": 111,
        "telegram_username": "acme",
        "telegram_first_name": "Acme",
        "linked_user_id": 99,
    }


def product_payload(**overrides: Any) -> dict[str, Any]:
    data = {
        "id": 1,
        "name": "ChatGPT Plus CDK",
        "slug": "chatgpt-plus-cdk",
        "description": "A redeemable code",
        "product_type": "cdk",
        "retail_price": "25.000000",
        "your_unit_price": "22.500000",
        "stock": 10,
        "inputs": [],
        "delivery_instructions": "Redeem at chatgpt.com",
    }
    data.update(overrides)
    return data


def invite_product_payload(**overrides: Any) -> dict[str, Any]:
    data = product_payload(
        id=2,
        name="Team Invite",
        slug="team-invite",
        product_type="team_invite",
        stock=None,
        inputs=[{"name": "email", "type": "string", "required": True}],
        delivery_instructions="Accept the invite in your inbox",
    )
    data.update(overrides)
    return data


def order_payload(**overrides: Any) -> dict[str, Any]:
    data = {
        "order_id": 42,
        "product_id": 1,
        "product_name": "ChatGPT Plus CDK",
        "product_type": "cdk",
        "quantity": 2,
        "unit_price": "22.500000",
        "amount": "45.000000",
        "status": "paid",
        "fulfillment_status": None,
        "fulfillment_note": None,
        "delivered_count": 2,
        "delivered_codes": ["CODE-A", "CODE-B"],
        "delivery_instructions": "Redeem at chatgpt.com",
        "inputs": None,
        "idempotency_key": "idem-1",
        "created_at": "2026-01-01T00:00:00Z",
        "paid_at": "2026-01-01T00:00:01Z",
        "delivered_at": "2026-01-01T00:00:01Z",
    }
    data.update(overrides)
    return data


def invite_order_payload(**overrides: Any) -> dict[str, Any]:
    data = order_payload(
        order_id=99,
        product_id=2,
        product_name="Team Invite",
        product_type="team_invite",
        quantity=1,
        unit_price="22.500000",
        amount="22.500000",
        status="paid",
        fulfillment_status="processing",
        delivered_count=0,
        delivered_codes=[],
        delivery_instructions="Accept the invite in your inbox",
        inputs={"email": "buyer@example.com"},
        delivered_at=None,
    )
    data.update(overrides)
    return data


def ticket_payload(**overrides: Any) -> dict[str, Any]:
    data = {
        "ticket_id": 5,
        "order_id": 99,
        "status": "open",
        "created_at": "2026-01-01T00:00:00Z",
        "closed_at": None,
        "messages": [
            {
                "id": 1,
                "sender": "buyer",
                "text": "Need help",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
    }
    data.update(overrides)
    return data


def ticket_list_item_payload(**overrides: Any) -> dict[str, Any]:
    data = {
        "ticket_id": 5,
        "order_id": 99,
        "status": "open",
        "message_count": 1,
        "last_sender": "buyer",
        "last_at": "2026-01-01T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
        "closed_at": None,
    }
    data.update(overrides)
    return data


def error_payload(code: str, message: str, request_id: str = "f05f...3db1") -> dict[str, Any]:
    return {"code": code, "message": message, "request_id": request_id}


class MockTransport:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self.handler(request)


@pytest.fixture
def make_client():
    def _make(handler) -> tuple[ResellerClient, MockTransport]:
        transport = MockTransport(handler)

        def _transport(request: httpx.Request) -> httpx.Response:
            return transport.handle(request)

        http = httpx.Client(transport=httpx.MockTransport(_transport))
        client = ResellerClient(
            api_key="rsk_test",
            base_url=BASE_URL,
            http_client=http,
            sleeper=lambda _delay: None,
        )
        return client, transport

    return _make
