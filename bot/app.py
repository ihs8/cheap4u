from __future__ import annotations

import logging
import sys

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from bot.config import Settings, require_bot_token
from bot.db import Store
from bot.handlers import (
    cmd_cancel,
    cmd_help,
    cmd_orders,
    cmd_paysupport,
    cmd_prices,
    cmd_setprice,
    cmd_shop,
    cmd_start,
    cmd_unknown,
    cmd_unsetprice,
    error_handler,
    on_callback,
    on_precheckout,
    on_successful_payment,
    on_text,
    poll_open_tickets,
)
from reseller_api.client import ResellerClient

logger = logging.getLogger("cheap4u.bot")


def build_application(settings: Settings | None = None) -> Application:
    settings = settings or Settings()
    token = require_bot_token(settings)
    store = Store(settings.db_path)
    reseller = ResellerClient()
    application = Application.builder().token(token).build()
    application.bot_data["settings"] = settings
    application.bot_data["store"] = store
    application.bot_data["reseller"] = reseller
    try:
        application.bot_data["reseller_telegram_id"] = reseller.get_me().telegram_id
    except Exception:
        application.bot_data["reseller_telegram_id"] = None
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("paysupport", cmd_paysupport))
    application.add_handler(CommandHandler("shop", cmd_shop))
    application.add_handler(CommandHandler("orders", cmd_orders))
    application.add_handler(CommandHandler("cancel", cmd_cancel))
    application.add_handler(CommandHandler("prices", cmd_prices))
    application.add_handler(CommandHandler("setprice", cmd_setprice))
    application.add_handler(CommandHandler("unsetprice", cmd_unsetprice))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(PreCheckoutQueryHandler(on_precheckout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))
    application.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(error_handler)
    if application.job_queue:
        application.job_queue.run_repeating(poll_open_tickets, interval=20, first=15)
    return application


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        application = build_application()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    logger.info("starting %s polling", application.bot_data["settings"].store_name)
    application.run_polling(allowed_updates=["message", "callback_query", "pre_checkout_query"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
