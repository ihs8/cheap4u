from __future__ import annotations

from pathlib import Path

from bot.db import Session, Store


def test_checkout_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    checkout = store.create_checkout(
        user_id=11,
        chat_id=22,
        product_id=1,
        product_name="Plus CDK",
        product_type="cdk",
        qty=2,
        stars=100,
        inputs=None,
    )
    loaded = store.get_checkout(checkout.id)
    assert loaded is not None
    assert loaded.product_name == "Plus CDK"
    assert loaded.status == "pending_payment"
    paid = store.mark_paid(checkout.id, "charge-1")
    assert paid is not None
    assert paid.status == "paid"
    assert paid.telegram_charge_id == "charge-1"
    store.set_reseller_order(checkout.id, 42, "delivered")
    again = store.get_checkout(checkout.id)
    assert again is not None
    assert again.reseller_order_id == 42
    orders = store.list_user_orders(11)
    assert len(orders) == 1
    store.close()


def test_orders_are_isolated_per_user(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    first = store.create_checkout(
        user_id=11,
        chat_id=11,
        product_id=1,
        product_name="Plus CDK",
        product_type="cdk",
        qty=1,
        stars=20,
        inputs=None,
    )
    store.mark_paid(first.id, "charge-11")
    second = store.create_checkout(
        user_id=22,
        chat_id=22,
        product_id=2,
        product_name="Invite",
        product_type="team_invite",
        qty=1,
        stars=40,
        inputs=None,
    )
    store.mark_paid(second.id, "charge-22")
    mine = store.list_user_orders(11)
    theirs = store.list_user_orders(22)
    assert [item.id for item in mine] == [first.id]
    assert [item.id for item in theirs] == [second.id]
    assert store.count_user_orders(11) == 1
    assert store.get_user_checkout(11, first.id) is not None
    assert store.get_user_checkout(11, second.id) is None
    paid_n, paid_stars = store.user_paid_stats(11)
    assert paid_n == 1
    assert paid_stars == 20
    unpaid = store.create_checkout(
        user_id=11,
        chat_id=11,
        product_id=3,
        product_name="Pending",
        product_type="cdk",
        qty=1,
        stars=99,
        inputs=None,
    )
    assert unpaid.status == "pending_payment"
    assert store.user_paid_stats(11) == (1, 20)
    store.close()


def test_mark_paid_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    checkout = store.create_checkout(
        user_id=1,
        chat_id=1,
        product_id=1,
        product_name="X",
        product_type="cdk",
        qty=1,
        stars=10,
        inputs=None,
    )
    first = store.mark_paid(checkout.id, "a")
    second = store.mark_paid(checkout.id, "b")
    assert first is not None and first.telegram_charge_id == "a"
    assert second is not None and second.telegram_charge_id == "a"
    store.close()


def test_session_and_ticket(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    checkout = store.create_checkout(
        user_id=9,
        chat_id=9,
        product_id=2,
        product_name="Invite",
        product_type="team_invite",
        qty=1,
        stars=50,
        inputs={"email": "a@b.c"},
    )
    store.set_session(
        Session(user_id=9, step="await_input", product_id=2, pending_field="email")
    )
    session = store.get_session(9)
    assert session is not None
    assert session.pending_field == "email"
    store.clear_session(9)
    assert store.get_session(9) is None
    store.set_ticket(checkout.id, 5)
    store.set_last_seen_message(checkout.id, 3)
    open_tickets = store.list_open_tickets()
    assert len(open_tickets) == 1
    assert open_tickets[0].ticket_id == 5
    assert open_tickets[0].last_seen_message_id == 3
    store.clear_ticket(checkout.id)
    assert store.list_open_tickets() == []
    store.set_price_override(16, "1.99")
    assert store.get_price_override(16) == "1.99"
    assert store.list_price_overrides() == {16: "1.99"}
    assert store.clear_price_override(16) is True
    assert store.get_price_override(16) is None
    store.close()


def test_hidden_products_and_inventory(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    store.set_product_hidden(1, True)
    store.set_product_copy(1, "Plus code", "Keep private")
    meta = store.get_product_meta(1)
    assert meta is not None
    assert meta["hidden"] is True
    assert meta["display_name"] == "Plus code"
    assert store.list_hidden_product_ids() == {1}
    store.set_product_hidden(1, False)
    assert store.list_hidden_product_ids() == set()
    added = store.add_inventory_keys(1, ["AAA-1", "BBB-2", "  ", "CCC-3"])
    assert added == 3
    assert store.inventory_counts(1)["available"] == 3
    codes = store.pop_inventory_keys(1, 2, "chk-1")
    assert codes == ["AAA-1", "BBB-2"]
    assert store.inventory_counts(1) == {"available": 1, "sold": 2, "reserved": 0}
    assert store.pop_inventory_keys(1, 2, "chk-2") == []
    leftover = store.list_inventory(1, status="available")
    assert leftover[0]["code"] == "CCC-3"
    assert store.delete_inventory_key(leftover[0]["id"]) is True
    assert store.inventory_counts(1)["available"] == 0
    store.set_setting("welcome_text", "Hello shop")
    assert store.get_setting("welcome_text") == "Hello shop"
    assert store.get_stars_per_usd(20) == 20.0
    store.set_setting("stars_per_usd", "50")
    assert store.get_stars_per_usd(20) == 50.0
    store.set_setting("stars_per_usd", "0")
    assert store.get_stars_per_usd(20) == 20.0
    store.close()
