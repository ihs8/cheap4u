from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from bot.db import Checkout, Store
from bot.pricing import format_usd, stars_price
from reseller_api.models import Product

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"


def esc(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def html(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def render(name: str, **values: Any) -> str:
    content = html(name)
    for key, value in values.items():
        content = content.replace("{{" + key + "}}", str(value))
    return content


def fmt_time(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def flash_html(ok: str | None = None, err: str | None = None) -> str:
    if ok:
        return f'<div class="banner ok" role="status">{esc(ok)}</div>'
    if err:
        return f'<div class="banner err" role="alert">{esc(err)}</div>'
    return ""


def nav(active: str, store_name: str) -> str:
    items = [
        ("/", "Overview", "overview"),
        ("/products", "Products", "products"),
        ("/keys", "Keys", "keys"),
        ("/orders", "Orders", "orders"),
        ("/tickets", "Tickets", "tickets"),
        ("/settings", "Settings", "settings"),
    ]
    links = []
    for href, label, key in items:
        cls = ' class="active"' if key == active else ""
        links.append(f'<a href="{href}"{cls}>{label}</a>')
    return f"""
    <header class="top">
      <div>
        <p class="eyebrow">Admin</p>
        <h1>{esc(store_name)}</h1>
      </div>
      <nav class="nav">{"".join(links)}</nav>
      <form method="post" action="/logout">
        <button class="btn ghost" type="submit">Sign out</button>
      </form>
    </header>
    """


def layout(store_name: str, active: str, body: str, title: str | None = None) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title or store_name)} · Admin</title>
  <link rel="stylesheet" href="/static/dashboard.css">
</head>
<body>
  {nav(active, store_name)}
  <main>
    {body}
  </main>
</body>
</html>
"""


def product_rows(products: list[Product], store: Store, stars_per_usd: float) -> str:
    if not products:
        return '<tr><td colspan="9" class="empty">No products from the API.</td></tr>'
    hidden = store.list_hidden_product_ids()
    rows: list[str] = []
    for product in products:
        override = store.get_price_override(product.id)
        sell = override if override is not None else product.retail_price
        custom = "custom" if override is not None else "list"
        stock = "Unlimited" if product.stock is None else str(product.stock)
        stars = stars_price(sell, qty=1, stars_per_usd=stars_per_usd)
        counts = store.inventory_counts(product.id)
        vis = "hidden" if product.id in hidden else "live"
        toggle_label = "Show" if product.id in hidden else "Hide"
        reset = ""
        if override is not None:
            reset = (
                f'<form method="post" action="/products/{product.id}/reset" class="inline">'
                '<button class="btn ghost" type="submit">Reset price</button></form>'
            )
        rows.append(
            f"""
            <tr class="{vis}">
              <td class="mono">#{product.id}</td>
              <td>
                <div class="name">{esc(product.name)}</div>
                <div class="muted">{esc(product.product_type)}</div>
              </td>
              <td>{format_usd(product.your_unit_price)}</td>
              <td><span class="pill {custom}">{format_usd(sell)}</span><div class="muted">{stars} Stars</div></td>
              <td>{stock}<div class="muted">local {counts["available"]}</div></td>
              <td><span class="pill {vis}">{vis}</span></td>
              <td>
                <form method="post" action="/products/{product.id}/price" class="price-form">
                  <label class="sr" for="usd-{product.id}">USD</label>
                  <input id="usd-{product.id}" name="usd" value="{esc(sell)}" required>
                  <button class="btn" type="submit">Save</button>
                </form>
              </td>
              <td>
                <form method="post" action="/products/{product.id}/visibility" class="inline">
                  <input type="hidden" name="hidden" value="{"0" if product.id in hidden else "1"}">
                  <button class="btn ghost" type="submit">{toggle_label}</button>
                </form>
              </td>
              <td>
                <a class="btn ghost" href="/keys?product_id={product.id}">Keys</a>
                {reset}
              </td>
            </tr>
            """
        )
    return "\n".join(rows)


def order_rows(orders: list[Checkout], stars_per_usd: float) -> str:
    if not orders:
        return '<tr><td colspan="7" class="empty">No shop orders yet.</td></tr>'
    rows: list[str] = []
    for order in orders:
        usd = format_usd(order.stars / stars_per_usd) if stars_per_usd else "$0"
        codes = "<br>".join(esc(code) for code in order.delivered_codes[:6]) or "—"
        rows.append(
            f"""
            <tr>
              <td class="mono"><a href="/orders/{esc(order.id)}">{esc(order.id[:8])}</a></td>
              <td>{esc(order.product_name)} <span class="muted">x{order.qty}</span></td>
              <td class="status {esc(order.status)}">{esc(order.status)}</td>
              <td>{usd}<div class="muted">{order.stars} Stars</div></td>
              <td class="mono">{order.reseller_order_id or "—"}</td>
              <td class="mono">{codes}</td>
              <td class="muted">{fmt_time(order.created_at)}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def key_rows(keys: list[dict[str, Any]]) -> str:
    if not keys:
        return '<tr><td colspan="5" class="empty">No keys for this product.</td></tr>'
    rows: list[str] = []
    for item in keys:
        delete = ""
        if item["status"] == "available":
            delete = (
                f'<form method="post" action="/keys/{item["id"]}/delete" class="inline">'
                f'<input type="hidden" name="product_id" value="{item["product_id"]}">'
                '<button class="btn ghost" type="submit">Delete</button></form>'
            )
        rows.append(
            f"""
            <tr>
              <td class="mono">{esc(item["code"])}</td>
              <td class="status {esc(item["status"])}">{esc(item["status"])}</td>
              <td class="mono">{esc(item["checkout_id"] or "—")}</td>
              <td class="muted">{fmt_time(item["created_at"])}</td>
              <td>{delete}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def ticket_rows(tickets: list[Any]) -> str:
    if not tickets:
        return '<tr><td colspan="5" class="empty">No open tickets.</td></tr>'
    rows: list[str] = []
    for ticket in tickets:
        last = ticket.last_sender or "—"
        rows.append(
            f"""
            <tr>
              <td class="mono">#{ticket.ticket_id}</td>
              <td class="mono">#{ticket.order_id}</td>
              <td class="status {esc(ticket.status)}">{esc(ticket.status)}</td>
              <td>{ticket.message_count} · last {esc(last)}</td>
              <td class="muted">{esc(ticket.created_at)}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def q(text: str) -> str:
    return quote_plus(text)
