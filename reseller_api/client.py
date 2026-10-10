from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Callable

import httpx

from reseller_api.errors import ResellerAPIError
from reseller_api.models import Me, Order, OrderPage, Product, Ticket, TicketPage

DEFAULT_BASE_URL = "https://cgpt-active.pro/telegram/api"
DEFAULT_TIMEOUT = 30.0
POLL_INITIAL_DELAY = 2.0
POLL_MAX_DELAY = 60.0
POLL_CEILING = 600.0

logger = logging.getLogger("reseller_api")


def _load_api_key(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    key = os.getenv("RESELLER_API_KEY")
    if not key:
        raise ResellerAPIError(
            "unauthorized",
            "RESELLER_API_KEY is not set",
        )
    return key


def _load_base_url(explicit: str | None = None) -> str:
    if explicit:
        return explicit.rstrip("/")
    return os.getenv("RESELLER_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


class ResellerClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.base_url = _load_base_url(base_url)
        self.api_key = _load_api_key(api_key)
        self.timeout = timeout
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)
        self._sleep = sleeper or time.sleep

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> ResellerClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self._http.request(
                method,
                url,
                headers=self._headers(extra_headers),
                json=json,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise ResellerAPIError("network_error", str(exc)) from exc

        if response.status_code in expected:
            if response.status_code == 204 or not response.content:
                return None
            return response.json()

        payload: dict[str, Any]
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if "code" in payload:
            code = str(payload.get("code") or "unknown")
            message = str(payload.get("message") or response.text or "request failed")
            request_id = payload.get("request_id")
            logger.error(
                "reseller api error code=%s request_id=%s status=%s message=%s",
                code,
                request_id,
                response.status_code,
                message,
            )
            raise ResellerAPIError(
                code,
                message,
                request_id=str(request_id) if request_id is not None else None,
                status_code=response.status_code,
            )

        if "detail" in payload:
            detail = payload["detail"]
            message = str(detail) if not isinstance(detail, list) else "; ".join(str(item) for item in detail)
            raise ResellerAPIError(
                "validation_error",
                message,
                status_code=response.status_code,
            )

        raise ResellerAPIError(
            "http_error",
            response.text or f"HTTP {response.status_code}",
            status_code=response.status_code,
        )

    def get_me(self) -> Me:
        return Me.from_dict(self._request("GET", "/v1/me"))

    def list_products(self) -> list[Product]:
        data = self._request("GET", "/v1/products")
        return [Product.from_dict(item) for item in data.get("products") or []]

    def get_product(self, product_id: int) -> Product:
        return Product.from_dict(self._request("GET", f"/v1/products/{product_id}"))

    def place_order(
        self,
        product_id: int,
        quantity: int = 1,
        inputs: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Order:
        key = idempotency_key or str(uuid.uuid4())
        body: dict[str, Any] = {"product_id": product_id, "quantity": quantity}
        if inputs is not None:
            body["inputs"] = inputs
        data = self._request(
            "POST",
            "/v1/orders",
            json=body,
            extra_headers={"Idempotency-Key": key, "Content-Type": "application/json"},
            expected=(200, 201),
        )
        return Order.from_dict(data)

    def list_orders(self, cursor: int | None = None, limit: int = 20) -> OrderPage:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return OrderPage.from_dict(self._request("GET", "/v1/orders", params=params))

    def get_order(self, order_id: int) -> Order:
        return Order.from_dict(self._request("GET", f"/v1/orders/{order_id}"))

    def resubmit_inputs(self, order_id: int, inputs: dict[str, Any]) -> Order:
        return Order.from_dict(
            self._request(
                "POST",
                f"/v1/orders/{order_id}/inputs",
                json={"inputs": inputs},
                extra_headers={"Content-Type": "application/json"},
            )
        )

    def poll_until_delivered(
        self,
        order_id: int,
        *,
        initial_delay: float = POLL_INITIAL_DELAY,
        max_delay: float = POLL_MAX_DELAY,
        ceiling: float = POLL_CEILING,
        on_status: Callable[[Order], None] | None = None,
    ) -> Order:
        delay = initial_delay
        elapsed = 0.0
        while True:
            order = self.get_order(order_id)
            if on_status:
                on_status(order)
            if order.product_type == "cdk":
                if order.status in {"paid", "delivered"} and order.delivered_codes:
                    return order
                if order.status in {"cancelled", "expired"}:
                    raise ResellerAPIError(
                        "order_failed",
                        f"Order {order_id} ended with status={order.status}",
                    )
            else:
                status = order.fulfillment_status
                if status == "delivered" or (order.status == "delivered" and status is None):
                    return order
                if status == "input_rejected":
                    return order
                if status == "failed":
                    raise ResellerAPIError(
                        "fulfillment_failed",
                        order.fulfillment_note or f"Order {order_id} fulfillment failed",
                    )
                if order.status in {"cancelled", "expired"}:
                    raise ResellerAPIError(
                        "order_failed",
                        f"Order {order_id} ended with status={order.status}",
                    )
            if elapsed >= ceiling:
                raise ResellerAPIError(
                    "poll_timeout",
                    f"Timed out waiting for order {order_id} after {ceiling}s",
                )
            self._sleep(delay)
            elapsed += delay
            delay = min(delay * 2, max_delay)

    def open_ticket(self, order_id: int, message: str) -> Ticket:
        return Ticket.from_dict(
            self._request(
                "POST",
                f"/v1/orders/{order_id}/tickets",
                json={"message": message},
                extra_headers={"Content-Type": "application/json"},
                expected=(200, 201),
            )
        )

    def post_ticket_message(self, ticket_id: int, message: str) -> Ticket:
        return Ticket.from_dict(
            self._request(
                "POST",
                f"/v1/tickets/{ticket_id}/messages",
                json={"message": message},
                extra_headers={"Content-Type": "application/json"},
            )
        )

    def get_ticket(self, ticket_id: int) -> Ticket:
        return Ticket.from_dict(self._request("GET", f"/v1/tickets/{ticket_id}"))

    def list_tickets(
        self,
        status: str | None = None,
        order_id: int | None = None,
        cursor: int | None = None,
        limit: int = 20,
    ) -> TicketPage:
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if order_id is not None:
            params["order_id"] = order_id
        if cursor is not None:
            params["cursor"] = cursor
        return TicketPage.from_dict(self._request("GET", "/v1/tickets", params=params))

    def close_ticket(self, ticket_id: int) -> Ticket:
        return Ticket.from_dict(
            self._request("POST", f"/v1/tickets/{ticket_id}/close")
        )

    def poll_ticket(
        self,
        ticket_id: int,
        *,
        seen_message_ids: set[int] | None = None,
        initial_delay: float = POLL_INITIAL_DELAY,
        max_delay: float = POLL_MAX_DELAY,
        ceiling: float = POLL_CEILING,
        on_operator_message: Callable[[Ticket], None] | None = None,
    ) -> Ticket:
        delay = initial_delay
        elapsed = 0.0
        seen = set(seen_message_ids or ())
        while True:
            ticket = self.get_ticket(ticket_id)
            new_operator = [
                msg for msg in ticket.messages if msg.sender == "operator" and msg.id not in seen
            ]
            if new_operator and on_operator_message:
                on_operator_message(ticket)
            if ticket.status == "closed" or new_operator:
                return ticket
            if elapsed >= ceiling:
                raise ResellerAPIError(
                    "poll_timeout",
                    f"Timed out waiting for ticket {ticket_id} after {ceiling}s",
                )
            self._sleep(delay)
            elapsed += delay
            delay = min(delay * 2, max_delay)
