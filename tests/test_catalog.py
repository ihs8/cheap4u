from __future__ import annotations

from pathlib import Path

from bot.catalog import apply_meta, sell_usd, visible_products
from bot.db import Store
from reseller_api.models import Product
from tests.conftest import product_payload


def test_visible_products_skips_hidden(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    shown = Product.from_dict(product_payload())
    hidden = Product.from_dict(product_payload(id=2, name="Secret"))
    store.set_product_hidden(2, True)
    store.set_price_override(1, "9.99")
    store.set_product_copy(1, "Plus code", None)
    products = visible_products([shown, hidden], store)
    assert [p.id for p in products] == [1]
    assert products[0].name == "Plus code"
    assert sell_usd(shown, store) == "9.99"


def test_apply_meta_keeps_original_when_empty(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite3")
    product = Product.from_dict(product_payload())
    assert apply_meta(product, store) is product
