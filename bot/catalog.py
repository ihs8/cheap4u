from __future__ import annotations

from dataclasses import replace

from bot.db import Store
from reseller_api.models import Product


def sell_usd(product: Product, store: Store) -> str:
    override = store.get_price_override(product.id)
    return override if override is not None else product.retail_price


def visible_products(products: list[Product], store: Store) -> list[Product]:
    hidden = store.list_hidden_product_ids()
    visible: list[Product] = []
    for product in products:
        if product.id in hidden:
            continue
        visible.append(apply_meta(product, store))
    return visible


def apply_meta(product: Product, store: Store) -> Product:
    meta = store.get_product_meta(product.id)
    if not meta:
        return product
    name = meta.get("display_name") or product.name
    description = meta.get("display_description")
    if description is None:
        description = product.description
    return replace(product, name=name, description=description)
