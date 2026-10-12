from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.catalog import apply_meta, sell_usd, visible_products
from bot.config import Settings
from bot.db import Session, Store
from bot.pricing import format_usd, parse_usd, stars_price
from reseller_api.client import ResellerClient
from reseller_api.errors import ResellerAPIError
from reseller_api.models import Product

logger = logging.getLogger("cheap4u.bot")

BUYER_VOICE = {
    "shop": "Browse the catalog and tap a product to buy with Telegram Stars.",
    "paid": "Payment received. Preparing your order now.",
    "codes": "Your order is ready. Keep these details private.",
    "invite_wait": "Payment received. We are setting this up — you will get a message when it is done.",
    "input_rejected": "The details you sent could not be used. Please send a corrected value.",
    "failed": "We could not complete this order. You can open support from /orders.",
    "insufficient": "This item is temporarily unavailable. Please try again later.",
    "error": "Something went wrong. Try /shop or /orders.",
    "callback_error": "Something went wrong. Try again.",
    "invoice_error": "Could not open the Stars invoice. Try again from /shop.",
}

PAYSUPPORT_TEXT = (
    "Stars payment help\n\n"
    "If you paid and did not get your order, open /orders and tap Contact support. "
    "Include the short Ref from that order.\n\n"
    "Stars payments stay inside Telegram. We cannot send a refund to a bank or card from this chat."
)

DEFAULT_HELP = (
    "Pay with Telegram Stars. After payment we deliver here.\n\n"
    "/shop — catalog\n"
    "/orders — your purchases\n"
    "/paysupport — Stars payment help\n"
    "/cancel — stop the current step"
)


def _store(context: ContextTypes.DEFAULT_TYPE) -> Store:
    return context.bot_data["store"]


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.bot_data["settings"]


def _client(context: ContextTypes.DEFAULT_TYPE) -> ResellerClient:
    return context.bot_data["reseller"]


def _is_admin(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = _settings(context)
    if settings.admin_ids:
        return user_id in settings.admin_ids
    linked = context.bot_data.get("reseller_telegram_id")
    return linked is not None and user_id == linked


def _sell_usd(product: Product, context: ContextTypes.DEFAULT_TYPE) -> str:
    return sell_usd(product, _store(context))


def _stars_per_usd(context: ContextTypes.DEFAULT_TYPE) -> float:
    return _store(context).get_stars_per_usd(_settings(context).stars_per_usd)


BTN_MENU = "Menu"
BTN_BALANCE = "Balance"
BTN_ORDERS = "My Orders"
NAV_BUTTONS = frozenset({BTN_MENU, BTN_BALANCE, BTN_ORDERS})


def _reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(BTN_MENU),
                KeyboardButton(BTN_BALANCE),
                KeyboardButton(BTN_ORDERS),
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Tap Menu / Balance / Orders below",
    )


def _main_keyboard() -> ReplyKeyboardMarkup:
    return _reply_keyboard()


def _home_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Shop", callback_data="shop")],
            [
                InlineKeyboardButton("My orders", callback_data="orders:0"),
                InlineKeyboardButton("Help", callback_data="help"),
            ],
        ]
    )


def _stock_label(product: Product) -> str:
    if product.stock is None:
        return "In stock"
    if product.stock <= 0:
        return "Out of stock"
    return f"{product.stock} left"


def _catalog_keyboard(products: list[Product], context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for product in products:
        price = format_usd(_sell_usd(product, context))
        label = f"{product.name} · {price}"
        rows.append([InlineKeyboardButton(label, callback_data=f"p:{product.id}")])
    rows.append([InlineKeyboardButton("Home", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def _qty_keyboard(product_id: int, max_qty: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(str(n), callback_data=f"q:{product_id}:{n}")
        for n in range(1, max_qty + 1)
    ]
    rows = [buttons[i : i + 5] for i in range(0, len(buttons), 5)]
    rows.append([InlineKeyboardButton("Back to shop", callback_data="shop")])
    return InlineKeyboardMarkup(rows)


ORDERS_PAGE_SIZE = 8

STATUS_LABEL = {
    "pending_payment": "Unpaid",
    "paid": "Processing",
    "processing": "Processing",
    "delivered": "Delivered",
    "failed": "Failed",
    "fulfillment_error": "Failed",
    "input_rejected": "Needs details",
}


def _status_label(status: str) -> str:
    return STATUS_LABEL.get(status, status.replace("_", " ").title())


def _owned_checkout(context: ContextTypes.DEFAULT_TYPE, user_id: int, checkout_id: str):
    return _store(context).get_user_checkout(user_id, checkout_id)


async def _safe_answer(query: Any, text: str | None = None, show_alert: bool = False) -> None:
    try:
        if text:
            await query.answer(text, show_alert=show_alert)
        else:
            await query.answer()
    except Exception:
        logger.debug("callback answer failed", exc_info=True)


async def _notify_buyer(update: object, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    chat_id = None
    effective_chat = getattr(update, "effective_chat", None)
    if effective_chat is not None:
        chat_id = getattr(effective_chat, "id", None)
    if chat_id is None:
        message = getattr(update, "message", None) or getattr(update, "effective_message", None)
        if message is not None:
            chat = getattr(message, "chat", None)
            chat_id = getattr(chat, "id", None) if chat is not None else None
    if chat_id is None:
        query = getattr(update, "callback_query", None)
        if query is not None:
            message = getattr(query, "message", None)
            if message is not None:
                chat = getattr(message, "chat", None)
                chat_id = getattr(chat, "id", None) if chat is not None else None
    if chat_id is None:
        return
    try:
        await context.bot.send_message(chat_id, text, reply_markup=_main_keyboard())
    except Exception:
        logger.debug("buyer notify failed chat=%s", chat_id, exc_info=True)


def _order_actions(checkout_id: str, has_ticket: bool, status: str = "") -> InlineKeyboardMarkup:
    rows = []
    if has_ticket:
        rows.append([InlineKeyboardButton("Message support", callback_data=f"tmsg:{checkout_id}")])
        rows.append([InlineKeyboardButton("Close ticket", callback_data=f"tclose:{checkout_id}")])
    elif status not in {"pending_payment"}:
        rows.append([InlineKeyboardButton("Contact support", callback_data=f"ticket:{checkout_id}")])
    rows.append(
        [
            InlineKeyboardButton("My orders", callback_data="orders:0"),
            InlineKeyboardButton("Shop", callback_data="shop"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _product_card(
    product: Product,
    settings: Settings,
    qty: int = 1,
    sell_usd: str | None = None,
) -> str:
    usd = format_usd(sell_usd or product.retail_price, qty)
    kind = "Account invite" if product.product_type == "team_invite" else "Instant delivery"
    stock = _stock_label(product)
    desc = (product.description or "").strip()
    lines = [
        f"<b>{product.name}</b>",
        kind,
        f"Price: {usd}" + (f" for {qty}" if qty > 1 else ""),
        stock,
    ]
    if desc:
        lines.append("")
        lines.append(desc)
    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(context)
    if not update.message:
        return
    welcome = _store(context).get_setting("welcome_text") or (
        f"Welcome to <b>{settings.store_name}</b>.\n"
        "Digital products, paid with Telegram Stars. Delivery stays in this chat.\n"
        "Use Menu, Balance, or My Orders below."
    )
    await update.message.reply_text(
        welcome,
        parse_mode=ParseMode.HTML,
        reply_markup=_reply_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    help_text = _store(context).get_setting("help_text") or DEFAULT_HELP
    await update.message.reply_text(help_text, reply_markup=_main_keyboard())


async def cmd_paysupport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(PAYSUPPORT_TEXT, reply_markup=_main_keyboard())


async def cmd_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Unknown command. Use /shop, /orders, /help, or /paysupport.",
        reply_markup=_main_keyboard(),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    _store(context).clear_session(update.effective_user.id)
    await update.message.reply_text("Cancelled.", reply_markup=_main_keyboard())


async def show_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        raw = await asyncio.to_thread(_client(context).list_products)
        products = visible_products(raw, _store(context))
    except Exception as exc:
        if isinstance(exc, ResellerAPIError):
            logger.error("list_products failed code=%s request_id=%s", exc.code, exc.request_id)
            text = BUYER_VOICE["insufficient"]
        else:
            logger.exception("list_products crashed")
            text = BUYER_VOICE["error"]
        if update.callback_query:
            await _safe_answer(update.callback_query)
            try:
                await update.callback_query.edit_message_text(text)
            except Exception:
                logger.debug("shop error edit failed", exc_info=True)
        elif update.message:
            await update.message.reply_text(text, reply_markup=_main_keyboard())
        return
    if not products:
        text = "Nothing in the catalog right now. Check back soon."
        inline = _home_inline()
    else:
        text = BUYER_VOICE["shop"]
        inline = _catalog_keyboard(products, context)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=inline)
    elif update.message:
        await update.message.reply_text(text, reply_markup=inline)


async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    user = update.effective_user
    if not user:
        return
    try:
        await _render_orders(update, context, user.id, page)
    except Exception:
        logger.exception("show_orders failed user=%s", user.id)
        text = BUYER_VOICE["error"]
        if update.callback_query:
            await _safe_answer(update.callback_query, BUYER_VOICE["callback_error"], show_alert=True)
        elif update.message:
            await update.message.reply_text(text, reply_markup=_main_keyboard())


async def _render_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, page: int) -> None:
    store = _store(context)
    page = max(0, page)
    total = store.count_user_orders(user_id)
    orders = store.list_user_orders(user_id, limit=ORDERS_PAGE_SIZE, offset=page * ORDERS_PAGE_SIZE)
    if not total:
        text = "No purchases yet. Open Shop to buy with Telegram Stars."
        markup = _home_inline()
    else:
        start = page * ORDERS_PAGE_SIZE + 1
        end = start + len(orders) - 1
        lines = [f"<b>Your orders</b> ({start}–{end} of {total})", ""]
        rows: list[list[InlineKeyboardButton]] = []
        for checkout in orders:
            usd = format_usd(checkout.stars / _stars_per_usd(context))
            label = _status_label(checkout.status)
            lines.append(
                f"{checkout.product_name} x{checkout.qty} · {label} · {usd}"
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{label} · {checkout.product_name[:28]}",
                        callback_data=f"o:{checkout.id}",
                    )
                ]
            )
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("Newer", callback_data=f"orders:{page - 1}"))
        if end < total:
            nav.append(InlineKeyboardButton("Older", callback_data=f"orders:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("Shop", callback_data="shop")])
        text = "\n".join(lines)
        markup = InlineKeyboardMarkup(rows)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        if query:
            await _safe_answer(query)
        return
    data = query.data
    try:
        await _dispatch_callback(update, context, data)
    except Exception:
        logger.exception("callback failed data=%s", data)
        await _safe_answer(query, BUYER_VOICE["callback_error"], show_alert=True)
        await _notify_buyer(update, context, BUYER_VOICE["error"])


async def _dispatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    query = update.callback_query
    assert query
    if data == "shop":
        await show_shop(update, context)
        return
    if data == "orders" or data.startswith("orders:"):
        page = 0
        if data.startswith("orders:") and data.split(":", 1)[1].isdigit():
            page = int(data.split(":", 1)[1])
        await show_orders(update, context, page)
        return
    if data == "home":
        await query.answer()
        settings = _settings(context)
        welcome = _store(context).get_setting("welcome_text") or (
            f"Welcome to {settings.store_name}."
        )
        await query.edit_message_text(welcome, parse_mode=ParseMode.HTML, reply_markup=_home_inline())
        return
    if data == "help":
        await query.answer()
        help_text = _store(context).get_setting("help_text") or DEFAULT_HELP
        await query.edit_message_text(help_text, reply_markup=_home_inline())
        return
    if data.startswith("p:"):
        await _on_pick_product(update, context, int(data.split(":", 1)[1]))
        return
    if data.startswith("q:"):
        _, product_id, qty = data.split(":")
        await _on_pick_qty(update, context, int(product_id), int(qty))
        return
    if data.startswith("buy:"):
        await _send_invoice(update, context, data.split(":", 1)[1])
        return
    if data.startswith("o:"):
        await _show_checkout(update, context, data.split(":", 1)[1])
        return
    if data.startswith("ticket:"):
        await _start_ticket(update, context, data.split(":", 1)[1])
        return
    if data.startswith("tmsg:"):
        await _start_ticket_message(update, context, data.split(":", 1)[1])
        return
    if data.startswith("tclose:"):
        await _close_ticket(update, context, data.split(":", 1)[1])
        return
    await query.answer()


async def _on_pick_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    query = update.callback_query
    assert query
    try:
        product = await asyncio.to_thread(_client(context).get_product, product_id)
    except ResellerAPIError:
        await query.answer("Unavailable", show_alert=True)
        return
    if product.id in _store(context).list_hidden_product_ids():
        await query.answer("Unavailable", show_alert=True)
        return
    product = apply_meta(product, _store(context))
    settings = _settings(context)
    max_qty = settings.max_qty
    if product.stock is not None:
        if product.stock <= 0:
            await query.answer("Out of stock", show_alert=True)
            return
        max_qty = max(1, min(max_qty, product.stock))
    await query.answer()
    await query.edit_message_text(
        _product_card(product, settings, sell_usd=_sell_usd(product, context)) + "\n\nHow many?",
        parse_mode=ParseMode.HTML,
        reply_markup=_qty_keyboard(product.id, max_qty),
    )


async def _on_pick_qty(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, qty: int
) -> None:
    query = update.callback_query
    user = update.effective_user
    assert query and user
    try:
        product = await asyncio.to_thread(_client(context).get_product, product_id)
    except ResellerAPIError:
        await query.answer("Unavailable", show_alert=True)
        return
    store = _store(context)
    if product.id in store.list_hidden_product_ids():
        await query.answer("Unavailable", show_alert=True)
        return
    product = apply_meta(product, store)
    required = [field for field in product.inputs if field.required] or list(product.inputs)
    if required:
        first = required[0]
        store.set_session(
            Session(
                user_id=user.id,
                step="await_input",
                product_id=product.id,
                qty=qty,
                inputs={},
                pending_field=first.name,
            )
        )
        await query.answer()
        await query.edit_message_text(
            _product_card(product, _settings(context), qty, sell_usd=_sell_usd(product, context))
            + f"\n\nSend your <b>{first.name}</b> to continue.",
            parse_mode=ParseMode.HTML,
        )
        return
    await query.answer()
    await _offer_invoice(update, context, product, qty, inputs=None)


async def _offer_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    product: Product,
    qty: int,
    inputs: dict[str, Any] | None,
) -> None:
    user = update.effective_user
    chat = update.effective_chat
    assert user and chat
    settings = _settings(context)
    sell_usd = _sell_usd(product, context)
    stars = stars_price(
        sell_usd,
        qty=qty,
        stars_per_usd=_stars_per_usd(context),
    )
    checkout = _store(context).create_checkout(
        user_id=user.id,
        chat_id=chat.id,
        product_id=product.id,
        product_name=product.name,
        product_type=product.product_type,
        qty=qty,
        stars=stars,
        inputs=inputs,
    )
    text = (
        _product_card(product, settings, qty, sell_usd=sell_usd)
        + "\n\nPay with Telegram Stars. Delivery happens in this chat after payment."
    )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Pay {format_usd(sell_usd, qty)}",
                    callback_data=f"buy:{checkout.id}",
                )
            ],
            [InlineKeyboardButton("Cancel", callback_data="shop")],
        ]
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def _send_invoice(
    update: Update, context: ContextTypes.DEFAULT_TYPE, checkout_id: str
) -> None:
    query = update.callback_query
    chat = update.effective_chat
    assert query and chat
    user = update.effective_user
    checkout = _owned_checkout(context, user.id, checkout_id) if user else None
    if not checkout or checkout.status != "pending_payment":
        await query.answer("This checkout expired. Pick the product again.", show_alert=True)
        return
    await query.answer()
    title = checkout.product_name[:32]
    description = f"{checkout.product_name} x{checkout.qty}"
    try:
        await context.bot.send_invoice(
            chat_id=chat.id,
            title=title,
            description=description,
            payload=checkout.id,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=title, amount=checkout.stars)],
        )
    except Exception:
        logger.exception("send_invoice failed checkout=%s", checkout.id)
        await context.bot.send_message(chat.id, BUYER_VOICE["invoice_error"], reply_markup=_main_keyboard())


async def on_precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if not query:
        return
    try:
        checkout = _store(context).get_checkout(query.invoice_payload)
        if not checkout or checkout.status != "pending_payment":
            await query.answer(ok=False, error_message="This checkout expired. Start again from /shop.")
            return
        payer = getattr(query, "from_user", None)
        payer_id = getattr(payer, "id", None) if payer is not None else None
        if payer_id is not None and payer_id != checkout.user_id:
            await query.answer(ok=False, error_message="This invoice belongs to another buyer.")
            return
        if query.currency != "XTR" or query.total_amount != checkout.stars:
            await query.answer(ok=False, error_message="Price mismatch. Start again from /shop.")
            return
        await query.answer(ok=True)
    except Exception:
        logger.exception("precheckout failed payload=%s", getattr(query, "invoice_payload", None))
        try:
            await query.answer(ok=False, error_message=BUYER_VOICE["error"])
        except Exception:
            logger.debug("precheckout reject failed", exc_info=True)


async def on_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.successful_payment:
        return
    payment = message.successful_payment
    store = _store(context)
    existing = store.get_checkout(payment.invoice_payload)
    payer = getattr(update, "effective_user", None)
    payer_id = getattr(payer, "id", None) if payer is not None else None
    if existing and payer_id is not None and existing.user_id != payer_id:
        await message.reply_text("This payment does not match your account.")
        return
    checkout = store.mark_paid(payment.invoice_payload, payment.telegram_payment_charge_id)
    if not checkout:
        await message.reply_text("Payment received, but this checkout was already processed.")
        return
    await message.reply_text(BUYER_VOICE["paid"])
    try:
        await _fulfill(update, context, checkout)
    except Exception:
        logger.exception("fulfill crashed checkout=%s", checkout.id)
        store.set_status(checkout.id, "fulfillment_error")
        await message.reply_text(BUYER_VOICE["failed"])


async def _fulfill(update: Update, context: ContextTypes.DEFAULT_TYPE, checkout) -> None:
    client = _client(context)
    store = _store(context)
    chat = update.effective_chat
    assert chat
    try:
        await _fulfill_order(update, context, checkout, client, store, chat.id)
    except ResellerAPIError:
        raise
    except Exception:
        logger.exception("fulfill crashed checkout=%s", checkout.id)
        store.set_status(checkout.id, "fulfillment_error")
        await context.bot.send_message(chat.id, BUYER_VOICE["failed"])


async def _fulfill_order(update: Update, context: ContextTypes.DEFAULT_TYPE, checkout, client, store, chat_id: int) -> None:
    local_codes = store.pop_inventory_keys(checkout.product_id, checkout.qty, checkout.id)
    if local_codes:
        store.set_reseller_order(checkout.id, 0, "delivered")
        store.set_delivered_codes(checkout.id, local_codes)
        await _send_local_codes(context, chat_id, local_codes)
        return
    try:
        order = await asyncio.to_thread(
            client.place_order,
            checkout.product_id,
            checkout.qty,
            checkout.inputs,
            checkout.id,
        )
    except ResellerAPIError as exc:
        logger.error(
            "place_order failed checkout=%s code=%s request_id=%s",
            checkout.id,
            exc.code,
            exc.request_id,
        )
        store.set_status(checkout.id, "fulfillment_error")
        text = BUYER_VOICE["insufficient"] if exc.code == "insufficient_balance" else BUYER_VOICE["failed"]
        await context.bot.send_message(chat_id, text)
        return

    store.set_reseller_order(checkout.id, order.order_id, "processing")
    if order.product_type != "team_invite":
        if not order.delivered_codes:
            try:
                order = await asyncio.to_thread(client.poll_until_delivered, order.order_id)
            except ResellerAPIError as exc:
                logger.error("cdk poll failed order=%s code=%s", order.order_id, exc.code)
                store.set_status(checkout.id, "fulfillment_error")
                await context.bot.send_message(chat_id, BUYER_VOICE["failed"])
                return
        store.set_status(checkout.id, "delivered")
        store.set_delivered_codes(checkout.id, order.delivered_codes)
        await _send_codes(context, chat_id, order)
        return

    await _drive_invite(context, chat_id, checkout.id, order)


async def _send_local_codes(context: ContextTypes.DEFAULT_TYPE, chat_id: int, codes: list[str]) -> None:
    joined = "\n".join(codes)
    await context.bot.send_message(
        chat_id,
        f"{BUYER_VOICE['codes']}\n\n<code>{joined}</code>",
        parse_mode=ParseMode.HTML,
    )


async def _send_codes(context: ContextTypes.DEFAULT_TYPE, chat_id: int, order) -> None:
    codes = "\n".join(order.delivered_codes) if order.delivered_codes else "(none yet)"
    parts = [BUYER_VOICE["codes"], f"<code>{codes}</code>"]
    if order.delivery_instructions:
        parts.append(order.delivery_instructions)
    await context.bot.send_message(chat_id, "\n\n".join(parts), parse_mode=ParseMode.HTML)


async def _drive_invite(context: ContextTypes.DEFAULT_TYPE, chat_id: int, checkout_id: str, order) -> None:
    client = _client(context)
    store = _store(context)
    user_id = store.get_checkout(checkout_id).user_id if store.get_checkout(checkout_id) else 0
    status = order.fulfillment_status
    if status == "delivered" or order.status == "delivered":
        store.set_status(checkout_id, "delivered")
        text = "Your order has been delivered."
        if order.delivery_instructions:
            text += "\n\n" + order.delivery_instructions
        await context.bot.send_message(chat_id, text)
        return
    if status == "failed":
        store.set_status(checkout_id, "failed")
        await context.bot.send_message(chat_id, order.fulfillment_note or BUYER_VOICE["failed"])
        return
    if status == "input_rejected":
        store.set_status(checkout_id, "input_rejected")
        field_name = "email"
        if order.inputs:
            field_name = next(iter(order.inputs))
        store.set_session(
            Session(
                user_id=user_id,
                step="await_correction",
                product_id=order.product_id,
                qty=order.quantity,
                inputs=dict(order.inputs or {}),
                pending_field=field_name,
                order_id=order.order_id,
                checkout_id=checkout_id,
                note=order.fulfillment_note,
            )
        )
        note = order.fulfillment_note or BUYER_VOICE["input_rejected"]
        await context.bot.send_message(chat_id, f"{note}\n\nSend a corrected {field_name}.")
        return
    await context.bot.send_message(chat_id, BUYER_VOICE["invite_wait"])
    try:
        order = await asyncio.to_thread(client.poll_until_delivered, order.order_id)
    except ResellerAPIError as exc:
        logger.error("invite poll failed order=%s code=%s request_id=%s", order.order_id, exc.code, exc.request_id)
        store.set_status(checkout_id, "failed")
        await context.bot.send_message(chat_id, BUYER_VOICE["failed"])
        return
    await _drive_invite(context, chat_id, checkout_id, order)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user or not message.text:
        return
    try:
        await _handle_text(update, context, message, user)
    except Exception:
        logger.exception("on_text failed user=%s", user.id)
        await message.reply_text(BUYER_VOICE["error"], reply_markup=_main_keyboard())


async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, message, user) -> None:
    text = message.text.strip()
    if text in NAV_BUTTONS:
        await _on_nav_button(update, context, text)
        return
    store = _store(context)
    session = store.get_session(user.id)
    if not session:
        await message.reply_text("Use Menu below or /shop to browse.", reply_markup=_reply_keyboard())
        return
    if session.step == "await_input":
        await _collect_input(update, context, session, text)
        return
    if session.step == "await_correction":
        await _collect_correction(update, context, session, text)
        return
    if session.step == "await_ticket":
        await _open_ticket_message(update, context, session, text)
        return
    if session.step == "await_ticket_msg":
        await _append_ticket_message(update, context, session, text)
        return
    store.clear_session(user.id)
    await message.reply_text("Use Menu below or /shop to browse.", reply_markup=_reply_keyboard())


async def _on_nav_button(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    user = update.effective_user
    if text == BTN_MENU:
        if user:
            _store(context).clear_session(user.id)
        await show_shop(update, context)
        return
    if text == BTN_BALANCE:
        await show_balance(update, context)
        return
    if text == BTN_ORDERS:
        await show_orders(update, context)


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if not user or not message:
        return
    paid, stars = _store(context).user_paid_stats(user.id)
    usd = format_usd(stars / _stars_per_usd(context)) if stars else "$0.00"
    text = (
        f"<b>Your spend</b>\n"
        f"{stars} Stars ({usd})\n"
        f"{paid} paid order" + ("" if paid == 1 else "s") + "\n\n"
        "Checkout uses Telegram Stars. There is no shop wallet to top up."
    )
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=_reply_keyboard())


async def _collect_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session, value: str
) -> None:
    assert update.message and session.product_id and session.pending_field
    inputs = dict(session.inputs or {})
    inputs[session.pending_field] = value
    try:
        product = await asyncio.to_thread(_client(context).get_product, session.product_id)
    except ResellerAPIError:
        await update.message.reply_text(BUYER_VOICE["insufficient"])
        return
    remaining = [
        field.name
        for field in product.inputs
        if field.required and field.name not in inputs
    ]
    if remaining:
        session.inputs = inputs
        session.pending_field = remaining[0]
        _store(context).set_session(session)
        await update.message.reply_text(f"Send your {remaining[0]}.")
        return
    _store(context).clear_session(session.user_id)
    await _offer_invoice(update, context, product, session.qty, inputs)


async def _collect_correction(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session, value: str
) -> None:
    assert update.message and session.order_id and session.pending_field and session.checkout_id
    inputs = dict(session.inputs or {})
    inputs[session.pending_field] = value
    try:
        order = await asyncio.to_thread(
            _client(context).resubmit_inputs, session.order_id, inputs
        )
    except ResellerAPIError as exc:
        logger.error("resubmit failed order=%s code=%s", session.order_id, exc.code)
        await update.message.reply_text(BUYER_VOICE["failed"])
        return
    _store(context).clear_session(session.user_id)
    await update.message.reply_text("Got it. We will continue from here.")
    await _drive_invite(context, update.message.chat_id, session.checkout_id, order)


async def _show_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE, checkout_id: str) -> None:
    query = update.callback_query
    user = update.effective_user
    assert query and user
    checkout = _owned_checkout(context, user.id, checkout_id)
    if not checkout:
        await query.answer("That order is not in your account.", show_alert=True)
        return
    await query.answer()
    usd = format_usd(checkout.stars / _stars_per_usd(context))
    lines = [
        f"<b>{checkout.product_name}</b> x{checkout.qty}",
        f"Status: {_status_label(checkout.status)}",
        f"Paid: {usd} ({checkout.stars} Stars)",
        f"Ref: <code>{checkout.id[:8]}</code>",
    ]
    if checkout.delivered_codes:
        lines.append("")
        lines.append(BUYER_VOICE["codes"])
        lines.append("<code>" + "\n".join(checkout.delivered_codes) + "</code>")
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_order_actions(checkout.id, bool(checkout.ticket_id), checkout.status),
    )


async def _start_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE, checkout_id: str) -> None:
    query = update.callback_query
    user = update.effective_user
    assert query and user
    checkout = _owned_checkout(context, user.id, checkout_id)
    if not checkout or checkout.status == "pending_payment":
        await query.answer("Support is not available for this order yet.", show_alert=True)
        return
    if not checkout.reseller_order_id:
        await query.answer("Message us from this chat. This order has no supplier ticket.", show_alert=True)
        return
    _store(context).set_session(
        Session(
            user_id=user.id,
            step="await_ticket",
            order_id=checkout.reseller_order_id,
            checkout_id=checkout.id,
            ticket_id=checkout.ticket_id,
        )
    )
    await query.answer()
    await query.edit_message_text("Send your message. We will reply in this chat.")


async def _start_ticket_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, checkout_id: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    assert query and user
    checkout = _owned_checkout(context, user.id, checkout_id)
    if not checkout or not checkout.ticket_id:
        await query.answer("No open ticket.", show_alert=True)
        return
    _store(context).set_session(
        Session(
            user_id=user.id,
            step="await_ticket_msg",
            order_id=checkout.reseller_order_id,
            checkout_id=checkout.id,
            ticket_id=checkout.ticket_id,
        )
    )
    await query.answer()
    await query.edit_message_text("Send your next message.")


async def _open_ticket_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session, text: str
) -> None:
    assert update.message and session.order_id and session.checkout_id
    try:
        ticket = await asyncio.to_thread(_client(context).open_ticket, session.order_id, text)
    except ResellerAPIError as exc:
        logger.error("open_ticket failed order=%s code=%s", session.order_id, exc.code)
        await update.message.reply_text("Support is not available for this order yet.")
        return
    _store(context).set_ticket(session.checkout_id, ticket.ticket_id)
    _store(context).clear_session(session.user_id)
    await update.message.reply_text(
        "Message sent. We will reply here.",
        reply_markup=_order_actions(session.checkout_id, True),
    )


async def _append_ticket_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session, text: str
) -> None:
    assert update.message and session.ticket_id and session.checkout_id
    try:
        await asyncio.to_thread(_client(context).post_ticket_message, session.ticket_id, text)
    except ResellerAPIError as exc:
        logger.error("ticket message failed ticket=%s code=%s", session.ticket_id, exc.code)
        await update.message.reply_text("Could not send that message. Try again later.")
        return
    _store(context).clear_session(session.user_id)
    await update.message.reply_text("Message sent.", reply_markup=_order_actions(session.checkout_id, True))


async def _close_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE, checkout_id: str) -> None:
    query = update.callback_query
    user = update.effective_user
    assert query and user
    checkout = _owned_checkout(context, user.id, checkout_id)
    if not checkout or not checkout.ticket_id:
        await query.answer("No open ticket.", show_alert=True)
        return
    try:
        await asyncio.to_thread(_client(context).close_ticket, checkout.ticket_id)
    except ResellerAPIError as exc:
        logger.error("close_ticket failed ticket=%s code=%s", checkout.ticket_id, exc.code)
        await query.answer("Could not close it.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text("Ticket closed.", reply_markup=_home_inline())


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_shop(update, context)


async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_orders(update, context)


def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    return bool(user and _is_admin(user.id, context))


async def cmd_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not _require_admin(update, context):
        await update.message.reply_text("Admin only.")
        return
    try:
        products = await asyncio.to_thread(_client(context).list_products)
    except ResellerAPIError:
        await update.message.reply_text("Could not load products.")
        return
    overrides = _store(context).list_price_overrides()
    lines = ["Custom USD prices (override / default):", ""]
    for product in products:
        current = overrides.get(product.id, product.retail_price)
        mark = "custom" if product.id in overrides else "default"
        lines.append(
            f"#{product.id} {product.name}: {format_usd(current)} ({mark}, list {format_usd(product.retail_price)})"
        )
    lines.append("")
    lines.append("Set: /setprice 16 1.99")
    lines.append("Reset: /unsetprice 16")
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3900] + "\n…"
    await update.message.reply_text(text)


async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not _require_admin(update, context):
        await update.message.reply_text("Admin only.")
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /setprice <product_id> <usd>\nExample: /setprice 16 1.99")
        return
    try:
        product_id = int(args[0])
        usd = parse_usd(args[1])
    except (ValueError, ArithmeticError):
        await update.message.reply_text("Need a product id and a USD amount, e.g. /setprice 16 1.99")
        return
    try:
        product = await asyncio.to_thread(_client(context).get_product, product_id)
    except ResellerAPIError:
        await update.message.reply_text("Unknown product id. Use /prices.")
        return
    _store(context).set_price_override(product_id, usd)
    await update.message.reply_text(
        f"{product.name} is now {format_usd(usd)} (was {format_usd(product.retail_price)} list)."
    )


async def cmd_unsetprice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not _require_admin(update, context):
        await update.message.reply_text("Admin only.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /unsetprice <product_id>")
        return
    try:
        product_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Usage: /unsetprice <product_id>")
        return
    if _store(context).clear_price_override(product_id):
        await update.message.reply_text(f"#{product_id} back to list price.")
        return
    await update.message.reply_text("No custom price on that product.")


async def poll_open_tickets(context: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(context)
    client = _client(context)
    try:
        open_tickets = store.list_open_tickets()
    except Exception:
        logger.exception("ticket poll list failed")
        return
    for checkout in open_tickets:
        if not checkout.ticket_id:
            continue
        try:
            ticket = await asyncio.to_thread(client.get_ticket, checkout.ticket_id)
            seen = checkout.last_seen_message_id or 0
            new_msgs = [
                msg for msg in ticket.messages if msg.sender == "operator" and msg.id > seen
            ]
            for msg in new_msgs:
                text = msg.text or "We have an update on your order."
                await context.bot.send_message(checkout.chat_id, text)
                store.set_last_seen_message(checkout.id, msg.id)
            if ticket.status == "closed":
                store.clear_ticket(checkout.id)
        except ResellerAPIError as exc:
            logger.error(
                "ticket poll failed ticket=%s code=%s request_id=%s",
                checkout.ticket_id,
                exc.code,
                exc.request_id,
            )
        except Exception:
            logger.exception("ticket poll crashed ticket=%s", checkout.ticket_id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("handler error: %s", context.error)
    if update is None:
        return
    query = getattr(update, "callback_query", None)
    if query is not None:
        await _safe_answer(query, BUYER_VOICE["callback_error"], show_alert=True)
        return
    pre_checkout = getattr(update, "pre_checkout_query", None)
    if pre_checkout is not None:
        try:
            await pre_checkout.answer(ok=False, error_message=BUYER_VOICE["error"])
        except Exception:
            logger.debug("precheckout error answer failed", exc_info=True)
        return
    await _notify_buyer(update, context, BUYER_VOICE["error"])
