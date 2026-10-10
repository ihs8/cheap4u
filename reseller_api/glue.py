from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from reseller_api.client import ResellerClient
from reseller_api.errors import ResellerAPIError
from reseller_api.models import Customer, DeliveryResult, Order, Product

logger = logging.getLogger("reseller_api")

SUPPORT_FAILED_MESSAGE = (
    "We couldn't fulfill this order. Please contact support and quote the order id."
)


def _required_input_name(product: Product) -> str | None:
    for field in product.inputs:
        if field.required:
            return field.name
    if product.inputs:
        return product.inputs[0].name
    return None


def _codes_ready(order: Order) -> bool:
    return bool(order.delivered_codes) and order.status in {"paid", "delivered", "partial"}


def place_and_deliver(
    customer: Customer,
    product_id: int,
    qty: int = 1,
    inputs: dict[str, Any] | None = None,
    *,
    client: ResellerClient | None = None,
    idempotency_key: str | None = None,
    ask_corrected_input: Callable[[str, str | None, dict[str, Any] | None], dict[str, Any]] | None = None,
) -> DeliveryResult:
    owns_client = client is None
    client = client or ResellerClient()
    try:
        product = client.get_product(product_id)
        key = idempotency_key or str(uuid.uuid4())
        logger.info(
            "placing order product_id=%s qty=%s type=%s customer=%s idempotency_key=%s",
            product_id,
            qty,
            product.product_type,
            customer.id,
            key,
        )
        try:
            order = client.place_order(
                product_id=product_id,
                quantity=qty,
                inputs=inputs,
                idempotency_key=key,
            )
        except ResellerAPIError as exc:
            if exc.code == "insufficient_balance":
                customer.notify(
                    "This order can't be completed right now. Please try again later."
                )
            raise

        if product.product_type == "cdk" or (order.product_type == "cdk"):
            if not _codes_ready(order) and order.status not in {"cancelled", "expired"}:
                order = client.poll_until_delivered(order.order_id)
            instructions = order.delivery_instructions or product.delivery_instructions
            if order.delivered_codes:
                customer.notify("Your codes are ready.")
            return DeliveryResult(
                order=order,
                delivered_codes=order.delivered_codes,
                delivery_instructions=instructions,
                fulfillment_status=order.fulfillment_status,
            )

        current_inputs = dict(inputs or order.inputs or {})
        field_name = _required_input_name(product) or next(iter(current_inputs), "email")
        ask = ask_corrected_input or customer.ask_corrected_input

        while True:
            status = order.fulfillment_status
            if status == "delivered" or order.status == "delivered":
                instructions = order.delivery_instructions or product.delivery_instructions
                customer.notify("Your order has been delivered.")
                return DeliveryResult(
                    order=order,
                    delivered_codes=order.delivered_codes,
                    delivery_instructions=instructions,
                    fulfillment_status=order.fulfillment_status or "delivered",
                )
            if status == "failed":
                note = order.fulfillment_note or SUPPORT_FAILED_MESSAGE
                customer.notify(note)
                return DeliveryResult(
                    order=order,
                    support_message=note,
                    fulfillment_status="failed",
                )
            if status == "input_rejected":
                note = order.fulfillment_note
                customer.notify(
                    note or "The details you provided couldn't be used. Please send a corrected value."
                )
                current_inputs = ask(field_name, note, current_inputs)
                order = client.resubmit_inputs(order.order_id, current_inputs)
                continue
            try:
                order = client.poll_until_delivered(order.order_id)
            except ResellerAPIError as exc:
                if exc.code == "fulfillment_failed":
                    customer.notify(exc.message or SUPPORT_FAILED_MESSAGE)
                    latest = client.get_order(order.order_id)
                    return DeliveryResult(
                        order=latest,
                        support_message=exc.message or SUPPORT_FAILED_MESSAGE,
                        fulfillment_status="failed",
                    )
                raise
    finally:
        if owns_client:
            client.close()
