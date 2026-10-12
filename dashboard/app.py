from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer

from bot.config import Settings
from bot.db import Store
from bot.pricing import format_usd, parse_stars_per_usd, parse_usd
from dashboard.html import (
    esc,
    flash_html,
    key_rows,
    layout,
    order_rows,
    product_rows,
    q,
    render,
    ticket_rows,
)
from reseller_api.client import ResellerClient
from reseller_api.errors import ResellerAPIError
from reseller_api.models import Me, Product

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
COOKIE = "cheap4u_admin"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7

logger = logging.getLogger("cheap4u.dashboard")


def _signer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="cheap4u-admin")


def _authed(request: Request, settings: Settings) -> bool:
    token = request.cookies.get(COOKIE)
    if not token or not settings.dashboard_password:
        return False
    try:
        payload = _signer(settings.dashboard_secret).loads(token, max_age=COOKIE_MAX_AGE)
    except BadSignature:
        return False
    return payload.get("ok") is True


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    client: ResellerClient | None = None,
) -> FastAPI:
    settings = settings or Settings()
    store = store or Store(settings.db_path)
    app = FastAPI(title=f"{settings.store_name} Admin", docs_url=None, redoc_url=None)
    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    app.state.settings = settings
    app.state.store = store
    app.state.client = client

    def reseller() -> ResellerClient:
        if app.state.client is not None:
            return app.state.client
        return ResellerClient()

    def load_catalog() -> tuple[Me | None, list[Product], str]:
        try:
            client_obj = reseller()
            return client_obj.get_me(), client_obj.list_products(), ""
        except ResellerAPIError as exc:
            logger.error("dashboard api error code=%s request_id=%s", exc.code, exc.request_id)
            return None, [], f'<div class="banner err">API unavailable ({exc.code}).</div>'

    def page(request: Request, active: str, body: str, title: str | None = None) -> HTMLResponse:
        if not _authed(request, settings):
            return _login_redirect()
        flash = flash_html(request.query_params.get("ok"), request.query_params.get("err"))
        return HTMLResponse(layout(settings.store_name, active, flash + body, title))

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        if _authed(request, settings):
            return RedirectResponse("/", status_code=303)
        error = '<p class="banner err" role="alert">Wrong password.</p>' if request.query_params.get("err") else ""
        return HTMLResponse(render("login.html", store_name=settings.store_name, error=error))

    @app.post("/login")
    def login(password: str = Form(...)) -> RedirectResponse:
        expected = settings.dashboard_password
        if not expected or not secrets.compare_digest(password, expected):
            return RedirectResponse("/login?err=1", status_code=303)
        token = _signer(settings.dashboard_secret).dumps({"ok": True})
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
        return response

    @app.post("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        if not _authed(request, settings):
            return _login_redirect()
        me, products, api_error = load_catalog()
        stats = store.order_stats()
        available_keys = sum(store.inventory_counts(p.id)["available"] for p in products)
        rate = store.get_stars_per_usd(settings.stars_per_usd)
        body = f"""
        {api_error}
        <section class="metrics" aria-label="Store metrics">
          <article><p class="muted">Wallet</p><p class="metric">{format_usd(me.balance) if me else "—"}</p></article>
          <article><p class="muted">Products</p><p class="metric">{len(products)}</p></article>
          <article><p class="muted">Local keys</p><p class="metric">{available_keys}</p></article>
          <article><p class="muted">Paid / delivered</p><p class="metric">{stats["paid"]} / {stats["delivered"]}</p></article>
        </section>
        <section>
          <div class="section-head"><h2>Recent orders</h2></div>
          <div class="table-wrap"><table>
            <thead><tr><th>Checkout</th><th>Product</th><th>Status</th><th>Paid</th><th>API</th><th>Keys</th><th>When</th></tr></thead>
            <tbody>{order_rows(store.list_all_orders(12), rate)}</tbody>
          </table></div>
        </section>
        """
        return page(request, "overview", body)

    @app.get("/products", response_class=HTMLResponse)
    def products_page(request: Request) -> HTMLResponse:
        if not _authed(request, settings):
            return _login_redirect()
        _, products, api_error = load_catalog()
        rate = store.get_stars_per_usd(settings.stars_per_usd)
        body = f"""
        {api_error}
        <div class="section-head">
          <h2>Products</h2>
          <p class="muted">Hide items, set USD, or send buyers to local keys instead of the supplier.</p>
        </div>
        <div class="table-wrap"><table>
          <thead><tr><th>ID</th><th>Product</th><th>Cost</th><th>Selling</th><th>Stock</th><th>Shop</th><th>USD</th><th></th><th></th></tr></thead>
          <tbody>{product_rows(products, store, rate)}</tbody>
        </table></div>
        """
        return page(request, "products", body, "Products")

    @app.post("/products/{product_id}/price")
    def set_price(request: Request, product_id: int, usd: str = Form(...)) -> RedirectResponse:
        if not _authed(request, settings):
            return _login_redirect()
        try:
            parsed = parse_usd(usd)
        except (ValueError, ArithmeticError):
            return RedirectResponse("/products?err=" + q("Enter a USD amount greater than 0"), status_code=303)
        store.set_price_override(product_id, parsed)
        return RedirectResponse("/products?ok=" + q(f"Saved #{product_id} at {parsed} USD"), status_code=303)

    @app.post("/products/{product_id}/reset")
    def reset_price(request: Request, product_id: int) -> RedirectResponse:
        if not _authed(request, settings):
            return _login_redirect()
        store.clear_price_override(product_id)
        return RedirectResponse("/products?ok=" + q(f"Reset #{product_id} to list price"), status_code=303)

    @app.post("/products/{product_id}/visibility")
    def set_visibility(request: Request, product_id: int, hidden: str = Form("1")) -> RedirectResponse:
        if not _authed(request, settings):
            return _login_redirect()
        store.set_product_hidden(product_id, hidden == "1")
        label = "hidden" if hidden == "1" else "visible"
        return RedirectResponse("/products?ok=" + q(f"#{product_id} is {label}"), status_code=303)

    @app.get("/keys", response_class=HTMLResponse)
    def keys_page(request: Request, product_id: int | None = None) -> HTMLResponse:
        if not _authed(request, settings):
            return _login_redirect()
        _, products, api_error = load_catalog()
        options = ['<option value="">Select product</option>']
        for product in products:
            counts = store.inventory_counts(product.id)
            selected = " selected" if product_id == product.id else ""
            options.append(
                f'<option value="{product.id}"{selected}>#{product.id} {esc(product.name)} ({counts["available"]} ready)</option>'
            )
        keys = store.list_inventory(product_id) if product_id else []
        counts = store.inventory_counts(product_id) if product_id else {"available": 0, "sold": 0}
        body = f"""
        {api_error}
        <div class="section-head">
          <h2>Keys</h2>
          <p class="muted">Paste codes here. If a product has local keys, the bot delivers those first and does not buy from the supplier.</p>
        </div>
        <form method="get" action="/keys" class="toolbar">
          <label class="sr" for="product_id">Product</label>
          <select id="product_id" name="product_id" onchange="this.form.submit()">{"".join(options)}</select>
          <noscript><button class="btn" type="submit">Open</button></noscript>
        </form>
        """
        if product_id:
            body += f"""
            <p class="muted">{counts["available"]} available · {counts["sold"]} sold</p>
            <form method="post" action="/keys/{product_id}/import" class="stack card">
              <label for="codes">Paste keys, one per line</label>
              <textarea id="codes" name="codes" rows="8" required placeholder="XXXX-XXXX-XXXX"></textarea>
              <button class="btn" type="submit">Add keys</button>
            </form>
            <div class="table-wrap"><table>
              <thead><tr><th>Code</th><th>Status</th><th>Checkout</th><th>Added</th><th></th></tr></thead>
              <tbody>{key_rows(keys)}</tbody>
            </table></div>
            """
        return page(request, "keys", body, "Keys")

    @app.post("/keys/{product_id}/import")
    def import_keys(request: Request, product_id: int, codes: str = Form(...)) -> RedirectResponse:
        if not _authed(request, settings):
            return _login_redirect()
        lines = [line.strip() for line in codes.splitlines() if line.strip()]
        added = store.add_inventory_keys(product_id, lines)
        return RedirectResponse(
            f"/keys?product_id={product_id}&ok=" + q(f"Added {added} keys"),
            status_code=303,
        )

    @app.post("/keys/{key_id}/delete")
    def delete_key(request: Request, key_id: int, product_id: int = Form(...)) -> RedirectResponse:
        if not _authed(request, settings):
            return _login_redirect()
        store.delete_inventory_key(key_id)
        return RedirectResponse(f"/keys?product_id={product_id}&ok=" + q("Key deleted"), status_code=303)

    @app.get("/orders", response_class=HTMLResponse)
    def orders_page(request: Request) -> HTMLResponse:
        if not _authed(request, settings):
            return _login_redirect()
        body = f"""
        <div class="section-head"><h2>Orders</h2><p class="muted">Telegram shop checkouts.</p></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Checkout</th><th>Product</th><th>Status</th><th>Paid</th><th>API</th><th>Keys</th><th>When</th></tr></thead>
          <tbody>{order_rows(store.list_all_orders(200), store.get_stars_per_usd(settings.stars_per_usd))}</tbody>
        </table></div>
        """
        return page(request, "orders", body, "Orders")

    @app.get("/orders/{checkout_id}", response_class=HTMLResponse)
    def order_detail(request: Request, checkout_id: str) -> HTMLResponse:
        if not _authed(request, settings):
            return _login_redirect()
        checkout = store.get_checkout(checkout_id)
        if not checkout:
            return RedirectResponse("/orders?err=" + q("Order not found"), status_code=303)
        codes = "<br>".join(esc(c) for c in checkout.delivered_codes) or "—"
        body = f"""
        <div class="section-head"><h2>Order {esc(checkout.id[:8])}</h2></div>
        <dl class="dl">
          <dt>Product</dt><dd>{esc(checkout.product_name)} x{checkout.qty}</dd>
          <dt>Status</dt><dd class="status {esc(checkout.status)}">{esc(checkout.status)}</dd>
          <dt>Buyer Telegram</dt><dd class="mono">{checkout.user_id}</dd>
          <dt>Stars</dt><dd>{checkout.stars}</dd>
          <dt>API order</dt><dd class="mono">{esc(checkout.reseller_order_id or "—")}</dd>
          <dt>Charge</dt><dd class="mono">{esc(checkout.telegram_charge_id or "—")}</dd>
          <dt>Delivered</dt><dd class="mono">{codes}</dd>
        </dl>
        """
        return page(request, "orders", body, "Order")

    @app.get("/tickets", response_class=HTMLResponse)
    def tickets_page(request: Request) -> HTMLResponse:
        if not _authed(request, settings):
            return _login_redirect()
        api_error = ""
        tickets: list[Any] = []
        try:
            page_data = reseller().list_tickets(status="open", limit=50)
            tickets = list(page_data.tickets)
        except ResellerAPIError as exc:
            api_error = f'<div class="banner err">Tickets unavailable ({exc.code}).</div>'
        except Exception:
            api_error = '<div class="banner err">Tickets unavailable.</div>'
        body = f"""
        {api_error}
        <div class="section-head"><h2>Tickets</h2><p class="muted">Open supplier tickets. Replies still go to the buyer in Telegram.</p></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Ticket</th><th>Order</th><th>Status</th><th>Thread</th><th>Opened</th></tr></thead>
          <tbody>{ticket_rows(tickets)}</tbody>
        </table></div>
        """
        return page(request, "tickets", body, "Tickets")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> HTMLResponse:
        if not _authed(request, settings):
            return _login_redirect()
        welcome = store.get_setting("welcome_text") or (
            f"Welcome to {settings.store_name}. Digital products, paid with Telegram Stars."
        )
        help_text = store.get_setting("help_text") or (
            "Pay with Telegram Stars. After payment we deliver here.\n/shop catalog · /orders purchases · /paysupport Stars help · /cancel stop"
        )
        rate = store.get_stars_per_usd(settings.stars_per_usd)
        body = f"""
        <div class="section-head"><h2>Settings</h2><p class="muted">Store copy and Stars rate. Bot reads this on the next message.</p></div>
        <form method="post" action="/settings" class="stack card">
          <label for="stars_per_usd">Stars per USD</label>
          <input id="stars_per_usd" name="stars_per_usd" value="{esc(rate)}" inputmode="decimal" required>
          <p class="muted">Checkout charges ceil(USD x qty x this rate). Telegram buyer packs are often ~50. Env fallback: {settings.stars_per_usd}. Max qty: {settings.max_qty}.</p>
          <label for="welcome_text">Welcome message</label>
          <textarea id="welcome_text" name="welcome_text" rows="4">{esc(welcome)}</textarea>
          <label for="help_text">Help message</label>
          <textarea id="help_text" name="help_text" rows="4">{esc(help_text)}</textarea>
          <button class="btn" type="submit">Save settings</button>
        </form>
        """
        return page(request, "settings", body, "Settings")

    @app.post("/settings")
    def save_settings(
        request: Request,
        welcome_text: str = Form(""),
        help_text: str = Form(""),
        stars_per_usd: str = Form(""),
    ) -> RedirectResponse:
        if not _authed(request, settings):
            return _login_redirect()
        try:
            rate = parse_stars_per_usd(stars_per_usd)
        except (ValueError, ArithmeticError):
            return RedirectResponse("/settings?err=" + q("Stars per USD must be greater than 0"), status_code=303)
        store.set_setting("stars_per_usd", rate)
        store.set_setting("welcome_text", welcome_text.strip())
        store.set_setting("help_text", help_text.strip())
        return RedirectResponse("/settings?ok=" + q(f"Saved {rate} Stars per USD"), status_code=303)

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True, "store": settings.store_name})

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    if not settings.dashboard_password:
        raise SystemExit("DASHBOARD_PASSWORD is not set")
    app = create_app(settings)
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
