from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bot.config import Settings
from bot.db import Store
from dashboard.app import create_app
from reseller_api.models import Me, Product, TicketPage
from tests.conftest import me_payload, product_payload


class FakeReseller:
    def get_me(self) -> Me:
        return Me.from_dict(me_payload())

    def list_products(self) -> list[Product]:
        return [Product.from_dict(product_payload())]

    def list_tickets(self, status=None, order_id=None, cursor=None, limit=20) -> TicketPage:
        return TicketPage.from_dict({"tickets": [], "next_cursor": None})


def _client(tmp_path: Path, password: str = "secret") -> tuple[TestClient, Store]:
    settings = Settings()
    settings.dashboard_password = password
    settings.dashboard_secret = "test-secret"
    settings.stars_per_usd = 20
    settings.store_name = "Cheap4U"
    settings.db_path = tmp_path / "bot.sqlite3"
    store = Store(settings.db_path)
    app = create_app(settings=settings, store=store, client=FakeReseller())
    return TestClient(app), store


def test_login_required(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_admin_pages_and_price(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    client.post("/login", data={"password": "secret"})
    home = client.get("/")
    assert home.status_code == 200
    assert "Wallet" in home.text
    products = client.get("/products")
    assert products.status_code == 200
    assert "ChatGPT Plus CDK" in products.text
    saved = client.post("/products/1/price", data={"usd": "19.99"}, follow_redirects=True)
    assert "19.99" in saved.text
    hidden = client.post("/products/1/visibility", data={"hidden": "1"}, follow_redirects=True)
    assert hidden.status_code == 200
    assert "hidden" in hidden.text.lower()


def test_import_and_delete_keys(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    client.post("/login", data={"password": "secret"})
    added = client.post("/keys/1/import", data={"codes": "AAA-1\nBBB-2\n"}, follow_redirects=True)
    assert added.status_code == 200
    assert "AAA-1" in added.text
    assert store.inventory_counts(1)["available"] == 2
    key_id = store.list_inventory(1)[0]["id"]
    client.post(f"/keys/{key_id}/delete", data={"product_id": 1})
    assert store.inventory_counts(1)["available"] == 1


def test_settings_save(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    client.post("/login", data={"password": "secret"})
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Stars per USD" in page.text
    client.post(
        "/settings",
        data={"welcome_text": "Hello shop", "help_text": "Help here", "stars_per_usd": "50"},
    )
    assert store.get_setting("welcome_text") == "Hello shop"
    assert store.get_stars_per_usd(20) == 50.0
    products = client.get("/products")
    assert "1250 Stars" in products.text


def test_settings_rejects_bad_rate(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    client.post("/login", data={"password": "secret"})
    bad = client.post(
        "/settings",
        data={"welcome_text": "x", "help_text": "y", "stars_per_usd": "0"},
        follow_redirects=False,
    )
    assert bad.status_code == 303
    assert "err=" in bad.headers["location"]
    assert store.get_setting("stars_per_usd") is None


def test_tickets_and_orders_pages(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    client.post("/login", data={"password": "secret"})
    tickets = client.get("/tickets")
    assert tickets.status_code == 200
    checkout = store.create_checkout(
        user_id=9,
        chat_id=9,
        product_id=1,
        product_name="ChatGPT Plus CDK",
        product_type="cdk",
        qty=1,
        stars=40,
        inputs=None,
    )
    orders = client.get("/orders")
    assert checkout.id[:8] in orders.text
    detail = client.get(f"/orders/{checkout.id}")
    assert detail.status_code == 200
    assert "ChatGPT Plus CDK" in detail.text


def test_health(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
