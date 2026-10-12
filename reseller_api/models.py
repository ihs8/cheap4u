from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProductType = Literal["cdk", "team_invite"]
OrderStatus = Literal["pending", "paid", "partial", "delivered", "cancelled", "expired"]
FulfillmentStatus = Literal["awaiting_input", "processing", "delivered", "input_rejected", "failed"]
TicketStatus = Literal["open", "closed"]
TicketSender = Literal["buyer", "operator"]


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


@dataclass(frozen=True)
class InputField:
    name: str
    type: str = "string"
    required: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InputField:
        return cls(
            name=str(data["name"]),
            type=str(data.get("type") or "string"),
            required=bool(data.get("required", True)),
        )


@dataclass(frozen=True)
class Me:
    reseller_id: int
    name: str
    discount_percent: str
    balance: str
    telegram_id: int
    api_key_prefix: str | None = None
    telegram_username: str | None = None
    telegram_first_name: str | None = None
    linked_user_id: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Me:
        return cls(
            reseller_id=int(data["reseller_id"]),
            name=str(data["name"]),
            discount_percent=str(data["discount_percent"]),
            balance=str(data["balance"]),
            telegram_id=int(data["telegram_id"]),
            api_key_prefix=_opt_str(data.get("api_key_prefix")),
            telegram_username=_opt_str(data.get("telegram_username")),
            telegram_first_name=_opt_str(data.get("telegram_first_name")),
            linked_user_id=int(data["linked_user_id"]) if data.get("linked_user_id") is not None else None,
        )


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    slug: str
    product_type: str
    retail_price: str
    your_unit_price: str
    stock: int | None
    description: str | None = None
    inputs: tuple[InputField, ...] = ()
    delivery_instructions: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Product:
        raw_inputs = data.get("inputs") or []
        stock = data.get("stock")
        return cls(
            id=int(data["id"]),
            name=str(data["name"]),
            slug=str(data["slug"]),
            product_type=str(data["product_type"]),
            retail_price=str(data["retail_price"]),
            your_unit_price=str(data["your_unit_price"]),
            stock=int(stock) if stock is not None else None,
            description=_opt_str(data.get("description")),
            inputs=tuple(InputField.from_dict(item) for item in raw_inputs),
            delivery_instructions=_opt_str(data.get("delivery_instructions")),
        )


@dataclass(frozen=True)
class Order:
    order_id: int
    product_id: int
    product_name: str
    product_type: str
    quantity: int
    unit_price: str
    amount: str
    status: str
    delivered_count: int
    created_at: str
    fulfillment_status: str | None = None
    fulfillment_note: str | None = None
    delivered_codes: tuple[str, ...] = ()
    delivery_instructions: str | None = None
    inputs: dict[str, Any] | None = None
    paid_at: str | None = None
    delivered_at: str | None = None
    idempotency_key: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Order:
        codes = data.get("delivered_codes") or []
        return cls(
            order_id=int(data["order_id"]),
            product_id=int(data["product_id"]),
            product_name=str(data.get("product_name") or ""),
            product_type=str(data["product_type"]),
            quantity=int(data["quantity"]),
            unit_price=str(data["unit_price"]),
            amount=str(data["amount"]),
            status=str(data["status"]),
            delivered_count=int(data.get("delivered_count") or 0),
            created_at=str(data["created_at"]),
            fulfillment_status=_opt_str(data.get("fulfillment_status")),
            fulfillment_note=_opt_str(data.get("fulfillment_note")),
            delivered_codes=tuple(str(code) for code in codes),
            delivery_instructions=_opt_str(data.get("delivery_instructions")),
            inputs=dict(data["inputs"]) if isinstance(data.get("inputs"), dict) else None,
            paid_at=_opt_str(data.get("paid_at")),
            delivered_at=_opt_str(data.get("delivered_at")),
            idempotency_key=_opt_str(data.get("idempotency_key")),
        )


@dataclass(frozen=True)
class OrderPage:
    orders: tuple[Order, ...]
    next_cursor: int | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderPage:
        cursor = data.get("next_cursor")
        return cls(
            orders=tuple(Order.from_dict(item) for item in data.get("orders") or []),
            next_cursor=int(cursor) if cursor is not None else None,
        )


@dataclass(frozen=True)
class TicketMessage:
    id: int
    sender: str
    created_at: str
    text: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TicketMessage:
        return cls(
            id=int(data["id"]),
            sender=str(data["sender"]),
            created_at=str(data["created_at"]),
            text=_opt_str(data.get("text")),
        )


@dataclass(frozen=True)
class Ticket:
    ticket_id: int
    order_id: int
    status: str
    created_at: str
    closed_at: str | None = None
    messages: tuple[TicketMessage, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ticket:
        return cls(
            ticket_id=int(data["ticket_id"]),
            order_id=int(data["order_id"]),
            status=str(data["status"]),
            created_at=str(data["created_at"]),
            closed_at=_opt_str(data.get("closed_at")),
            messages=tuple(TicketMessage.from_dict(item) for item in data.get("messages") or []),
        )


@dataclass(frozen=True)
class TicketListItem:
    ticket_id: int
    order_id: int
    status: str
    message_count: int
    created_at: str
    last_sender: str | None = None
    last_at: str | None = None
    closed_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TicketListItem:
        return cls(
            ticket_id=int(data["ticket_id"]),
            order_id=int(data["order_id"]),
            status=str(data["status"]),
            message_count=int(data["message_count"]),
            created_at=str(data["created_at"]),
            last_sender=_opt_str(data.get("last_sender")),
            last_at=_opt_str(data.get("last_at")),
            closed_at=_opt_str(data.get("closed_at")),
        )


@dataclass(frozen=True)
class TicketPage:
    tickets: tuple[TicketListItem, ...]
    next_cursor: int | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TicketPage:
        cursor = data.get("next_cursor")
        return cls(
            tickets=tuple(TicketListItem.from_dict(item) for item in data.get("tickets") or []),
            next_cursor=int(cursor) if cursor is not None else None,
        )


@dataclass
class DeliveryResult:
    order: Order
    delivered_codes: tuple[str, ...] = ()
    delivery_instructions: str | None = None
    support_message: str | None = None
    fulfillment_status: str | None = None


@dataclass
class Customer:
    """Buyer-facing callback used by the glue layer.

    Implement ``ask_corrected_input`` to collect a replacement value after
    ``input_rejected``. Other hooks are optional notifications.
    """

    id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def notify(self, message: str) -> None:
        return None

    def ask_corrected_input(
        self,
        field_name: str,
        note: str | None,
        current: dict[str, Any] | None,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            f"Buyer must supply a corrected value for {field_name}"
            + (f": {note}" if note else "")
        )
