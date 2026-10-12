from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bot.config import Settings
from bot.db import Store
from bot.handlers import (
    BTN_BALANCE,
    BTN_MENU,
    BTN_ORDERS,
    BUYER_VOICE,
    PAYSUPPORT_TEXT,
    _fulfill,
    _reply_keyboard,
    _show_checkout,
    cmd_paysupport,
    cmd_start,
    cmd_unknown,
    error_handler,
    on_callback,
    on_precheckout,
    on_successful_payment,
    on_text,
    show_balance,
    show_orders,
)
from reseller_api.errors import ResellerAPIError
from tests.conftest import invite_order_payload, order_payload


class FakeMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.markups: list[Any] = []
        self.successful_payment = None
        self.text = ""

    async def reply_text(self, text: str, **kwargs: Any) -> None:
        self.texts.append(text)
        self.markups.append(kwargs.get("reply_markup"))


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.invoices: list[dict[str, Any]] = []
        self.invoice_error: Exception | None = None

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.sent.append((chat_id, text))

    async def send_invoice(self, **kwargs: Any) -> None:
        if self.invoice_error:
            raise self.invoice_error
        self.invoices.append(kwargs)


class FakeReseller:
    def __init__(self, orders: list[Any] | None = None, error: Exception | None = None) -> None:
        self.orders = list(orders or [])
        self.error = error
        self.place_calls: list[dict[str, Any]] = []

    def place_order(self, product_id, quantity=1, inputs=None, idempotency_key=None):
        self.place_calls.append(
            {
                "product_id": product_id,
                "quantity": quantity,
                "inputs": inputs,
                "idempotency_key": idempotency_key,
            }
        )
        if self.error:
            raise self.error
        from reseller_api.models import Order

        return Order.from_dict(self.orders.pop(0))

    def poll_until_delivered(self, order_id):
        from reseller_api.models import Order

        return Order.from_dict(self.orders.pop(0))

    def list_products(self):
        return []


class FakeContext:
    def __init__(self, store: Store, reseller: FakeReseller) -> None:
        self.bot_data = {
            "store": store,
            "settings": Settings(),
            "reseller": reseller,
        }
        self.bot = FakeBot()


@pytest.mark.asyncio
async def test_fulfill_cdk_sends_codes(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    checkout = store.create_checkout(
        user_id=1,
        chat_id=99,
        product_id=1,
        product_name="CDK",
        product_type="cdk",
        qty=2,
        stars=100,
        inputs=None,
    )
    store.mark_paid(checkout.id, "tg-charge")
    reseller = FakeReseller(orders=[order_payload()])
    context = FakeContext(store, reseller)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=99))
    await _fulfill(update, context, store.get_checkout(checkout.id))
    assert reseller.place_calls[0]["idempotency_key"] == checkout.id
    assert store.get_checkout(checkout.id).status == "delivered"
    assert any("CODE-A" in text for _, text in context.bot.sent)
    assert any(BUYER_VOICE["codes"] in text for _, text in context.bot.sent)


@pytest.mark.asyncio
async def test_fulfill_uses_local_inventory_first(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    store.add_inventory_keys(1, ["LOCAL-A", "LOCAL-B"])
    checkout = store.create_checkout(
        user_id=1,
        chat_id=99,
        product_id=1,
        product_name="CDK",
        product_type="cdk",
        qty=2,
        stars=100,
        inputs=None,
    )
    store.mark_paid(checkout.id, "tg-charge")
    reseller = FakeReseller(orders=[order_payload()])
    context = FakeContext(store, reseller)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=99))
    await _fulfill(update, context, store.get_checkout(checkout.id))
    assert reseller.place_calls == []
    delivered = store.get_checkout(checkout.id)
    assert delivered is not None
    assert delivered.status == "delivered"
    assert delivered.delivered_codes == ("LOCAL-A", "LOCAL-B")
    assert any("LOCAL-A" in text for _, text in context.bot.sent)


@pytest.mark.asyncio
async def test_fulfill_insufficient_balance(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    checkout = store.create_checkout(
        user_id=1,
        chat_id=99,
        product_id=1,
        product_name="CDK",
        product_type="cdk",
        qty=1,
        stars=50,
        inputs=None,
    )
    reseller = FakeReseller(
        error=ResellerAPIError("insufficient_balance", "Balance $5.00 < required $22.50", "rid")
    )
    context = FakeContext(store, reseller)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=99))
    await _fulfill(update, context, checkout)
    assert store.get_checkout(checkout.id).status == "fulfillment_error"
    assert context.bot.sent[0][1] == BUYER_VOICE["insufficient"]


@pytest.mark.asyncio
async def test_fulfill_team_invite_wait(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    checkout = store.create_checkout(
        user_id=1,
        chat_id=99,
        product_id=2,
        product_name="Invite",
        product_type="team_invite",
        qty=1,
        stars=80,
        inputs={"email": "buyer@example.com"},
    )
    reseller = FakeReseller(
        orders=[
            invite_order_payload(fulfillment_status="processing"),
            invite_order_payload(fulfillment_status="delivered", status="delivered"),
        ]
    )
    context = FakeContext(store, reseller)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=99))
    await _fulfill(update, context, checkout)
    assert store.get_checkout(checkout.id).status == "delivered"
    assert any("delivered" in text.lower() for _, text in context.bot.sent)


@pytest.mark.asyncio
async def test_precheckout_ok(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    checkout = store.create_checkout(
        user_id=1,
        chat_id=1,
        product_id=1,
        product_name="CDK",
        product_type="cdk",
        qty=1,
        stars=42,
        inputs=None,
    )
    answers: list[dict[str, Any]] = []

    class Query:
        invoice_payload = checkout.id
        currency = "XTR"
        total_amount = 42

        async def answer(self, **kwargs: Any) -> None:
            answers.append(kwargs)

    context = FakeContext(store, FakeReseller())
    await on_precheckout(SimpleNamespace(pre_checkout_query=Query()), context)
    assert answers == [{"ok": True}]


@pytest.mark.asyncio
async def test_precheckout_rejects_stale(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    answers: list[dict[str, Any]] = []

    class Query:
        invoice_payload = "missing"
        currency = "XTR"
        total_amount = 1

        async def answer(self, **kwargs: Any) -> None:
            answers.append(kwargs)

    context = FakeContext(store, FakeReseller())
    await on_precheckout(SimpleNamespace(pre_checkout_query=Query()), context)
    assert answers[0]["ok"] is False


@pytest.mark.asyncio
async def test_successful_payment_delivers(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    checkout = store.create_checkout(
        user_id=1,
        chat_id=7,
        product_id=1,
        product_name="CDK",
        product_type="cdk",
        qty=1,
        stars=10,
        inputs=None,
    )
    message = FakeMessage()
    message.successful_payment = SimpleNamespace(
        invoice_payload=checkout.id,
        telegram_payment_charge_id="chg",
    )
    reseller = FakeReseller(orders=[order_payload(quantity=1, delivered_codes=["CODE-A"])])
    context = FakeContext(store, reseller)
    update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=7))
    await on_successful_payment(update, context)
    assert BUYER_VOICE["paid"] in message.texts
    assert store.get_checkout(checkout.id).status == "delivered"


class FakeQuery:
    def __init__(self, data: str | None = None) -> None:
        self.data = data
        self.alerts: list[str] = []
        self.edits: list[str] = []
        self.message = None

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs: Any) -> None:
        if show_alert and text:
            self.alerts.append(text)

    async def edit_message_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append(text)


@pytest.mark.asyncio
async def test_orders_list_is_per_user(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    mine = store.create_checkout(
        user_id=11,
        chat_id=11,
        product_id=1,
        product_name="My CDK",
        product_type="cdk",
        qty=1,
        stars=20,
        inputs=None,
    )
    store.mark_paid(mine.id, "a")
    store.set_delivered_codes(mine.id, ["MINE-1"])
    other = store.create_checkout(
        user_id=22,
        chat_id=22,
        product_id=1,
        product_name="Their CDK",
        product_type="cdk",
        qty=1,
        stars=20,
        inputs=None,
    )
    store.mark_paid(other.id, "b")
    message = FakeMessage()
    context = FakeContext(store, FakeReseller())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=11),
        message=message,
        callback_query=None,
    )
    await show_orders(update, context)
    assert any("My CDK" in text for text in message.texts)
    assert all("Their CDK" not in text for text in message.texts)


@pytest.mark.asyncio
async def test_checkout_detail_rejects_other_user(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    checkout = store.create_checkout(
        user_id=11,
        chat_id=11,
        product_id=1,
        product_name="My CDK",
        product_type="cdk",
        qty=1,
        stars=20,
        inputs=None,
    )
    store.mark_paid(checkout.id, "a")
    store.set_delivered_codes(checkout.id, ["MINE-1"])
    query = FakeQuery()
    context = FakeContext(store, FakeReseller())
    stranger = SimpleNamespace(effective_user=SimpleNamespace(id=22), callback_query=query)
    await _show_checkout(stranger, context, checkout.id)
    assert query.alerts == ["That order is not in your account."]
    owner_query = FakeQuery()
    owner = SimpleNamespace(effective_user=SimpleNamespace(id=11), callback_query=owner_query)
    await _show_checkout(owner, context, checkout.id)
    assert any("MINE-1" in text for text in owner_query.edits)


@pytest.mark.asyncio
async def test_paysupport_replies(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    message = FakeMessage()
    context = FakeContext(store, FakeReseller())
    await cmd_paysupport(SimpleNamespace(message=message), context)
    assert PAYSUPPORT_TEXT in message.texts


@pytest.mark.asyncio
async def test_unknown_command_replies(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    message = FakeMessage()
    context = FakeContext(store, FakeReseller())
    await cmd_unknown(SimpleNamespace(message=message), context)
    assert any("/paysupport" in text for text in message.texts)


@pytest.mark.asyncio
async def test_callback_crash_answers_query(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    query = FakeQuery()
    context = FakeContext(store, FakeReseller())
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=1),
    )
    query.data = "p:not-an-int"
    await on_callback(update, context)
    assert BUYER_VOICE["callback_error"] in query.alerts


@pytest.mark.asyncio
async def test_error_handler_notifies_buyer(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    context = FakeContext(store, FakeReseller())
    context.error = RuntimeError("boom")
    message = FakeMessage()
    update = SimpleNamespace(
        message=message,
        effective_chat=SimpleNamespace(id=9),
        callback_query=None,
        pre_checkout_query=None,
    )
    await error_handler(update, context)
    assert context.bot.sent[0] == (9, BUYER_VOICE["error"])


@pytest.mark.asyncio
async def test_error_handler_answers_callback(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    context = FakeContext(store, FakeReseller())
    context.error = RuntimeError("boom")
    query = FakeQuery()
    update = SimpleNamespace(callback_query=query, pre_checkout_query=None, message=None)
    await error_handler(update, context)
    assert query.alerts == [BUYER_VOICE["callback_error"]]


@pytest.mark.asyncio
async def test_precheckout_crash_rejects(tmp_path) -> None:
    answers: list[dict[str, Any]] = []

    class BrokenStore:
        def get_checkout(self, _payload: str) -> None:
            raise RuntimeError("db down")

    class Query:
        invoice_payload = "x"
        currency = "XTR"
        total_amount = 1

        async def answer(self, **kwargs: Any) -> None:
            answers.append(kwargs)

    context = FakeContext(Store(tmp_path / "bot.sqlite3"), FakeReseller())
    context.bot_data["store"] = BrokenStore()
    await on_precheckout(SimpleNamespace(pre_checkout_query=Query()), context)
    assert answers[0]["ok"] is False
    assert answers[0]["error_message"] == BUYER_VOICE["error"]


@pytest.mark.asyncio
async def test_successful_payment_fulfill_crash_notifies(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    checkout = store.create_checkout(
        user_id=1,
        chat_id=7,
        product_id=1,
        product_name="CDK",
        product_type="cdk",
        qty=1,
        stars=10,
        inputs=None,
    )
    message = FakeMessage()
    message.successful_payment = SimpleNamespace(
        invoice_payload=checkout.id,
        telegram_payment_charge_id="chg",
    )

    class BoomReseller(FakeReseller):
        def place_order(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("supplier down")

    context = FakeContext(store, BoomReseller())
    update = SimpleNamespace(
        message=message,
        effective_chat=SimpleNamespace(id=7),
        effective_user=SimpleNamespace(id=1),
    )
    await on_successful_payment(update, context)
    assert any(BUYER_VOICE["failed"] in text for _, text in context.bot.sent) or BUYER_VOICE["failed"] in message.texts
    assert store.get_checkout(checkout.id).status == "fulfillment_error"


def test_reply_keyboard_has_three_nav_buttons() -> None:
    markup = _reply_keyboard()
    labels = [button.text for row in markup.keyboard for button in row]
    assert labels == [BTN_MENU, BTN_BALANCE, BTN_ORDERS]
    assert markup.is_persistent is True
    assert markup.resize_keyboard is True


@pytest.mark.asyncio
async def test_start_attaches_reply_keyboard(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    message = FakeMessage()
    context = FakeContext(store, FakeReseller())
    await cmd_start(SimpleNamespace(message=message), context)
    assert message.markups
    labels = [button.text for row in message.markups[0].keyboard for button in row]
    assert labels == [BTN_MENU, BTN_BALANCE, BTN_ORDERS]


@pytest.mark.asyncio
async def test_balance_button_shows_buyer_spend(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    paid = store.create_checkout(
        user_id=11,
        chat_id=11,
        product_id=1,
        product_name="CDK",
        product_type="cdk",
        qty=1,
        stars=40,
        inputs=None,
    )
    store.mark_paid(paid.id, "chg")
    store.create_checkout(
        user_id=11,
        chat_id=11,
        product_id=1,
        product_name="Unpaid",
        product_type="cdk",
        qty=1,
        stars=99,
        inputs=None,
    )
    message = FakeMessage()
    context = FakeContext(store, FakeReseller())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=11), message=message)
    await show_balance(update, context)
    assert any("40 Stars" in text for text in message.texts)
    assert any("1 paid order" in text for text in message.texts)
    assert all("120.50" not in text for text in message.texts)


@pytest.mark.asyncio
async def test_nav_buttons_from_text(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    message = FakeMessage()
    message.text = BTN_BALANCE
    context = FakeContext(store, FakeReseller())
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=11),
        callback_query=None,
    )
    await on_text(update, context)
    assert any("Your spend" in text for text in message.texts)


@pytest.mark.asyncio
async def test_menu_button_opens_shop(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    message = FakeMessage()
    message.text = BTN_MENU
    context = FakeContext(store, FakeReseller())
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=11),
        callback_query=None,
    )
    await on_text(update, context)
    assert any("catalog" in text.lower() or "Nothing in the catalog" in text for text in message.texts)


@pytest.mark.asyncio
async def test_orders_button_opens_orders(tmp_path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    mine = store.create_checkout(
        user_id=11,
        chat_id=11,
        product_id=1,
        product_name="My CDK",
        product_type="cdk",
        qty=1,
        stars=20,
        inputs=None,
    )
    store.mark_paid(mine.id, "a")
    message = FakeMessage()
    message.text = BTN_ORDERS
    context = FakeContext(store, FakeReseller())
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=11),
        callback_query=None,
    )
    await on_text(update, context)
    assert any("My CDK" in text for text in message.texts)
