# Cheap4U

Telegram shop bot that resells digital products and charges **Telegram Stars**. Fulfillment goes through the Reseller API; buyers never see the upstream operator or endpoints.

## Setup

```bash
pip install --break-system-packages -r requirements.txt
cp .env.example .env
```

Fill `.env`:

```
RESELLER_API_KEY=rsk_xxx
TELEGRAM_BOT_TOKEN=123456:AA...
```

Create the bot with [@BotFather](https://t.me/BotFather). Digital goods paid in Stars do not need a payments provider token — invoices use currency `XTR`.

Catalog shows USD. Default is the API `retail_price`. Override any product from Telegram (admin only):

```
/prices
/setprice 16 1.99
/unsetprice 16
```

Checkout still charges Stars: `ceil(your_usd * qty * stars_per_usd)`. Set the rate in the dashboard Settings page (env `STARS_PER_USD` is the fallback).

Set `TELEGRAM_ADMIN_IDS` to your numeric Telegram user id (comma-separated). If empty, the Telegram account linked to the reseller key is treated as admin.

Optional:

```
STARS_PER_USD=20
STORE_NAME=Cheap4U
TELEGRAM_ADMIN_IDS=123456789
```

## Admin dashboard

Set `DASHBOARD_PASSWORD` then:

```bash
python3 -m dashboard.app
```

Sign in and use:

- Overview — wallet, local keys, recent orders
- Products — hide/show items and set per-product USD
- Keys — paste inventory; the bot delivers those first and does not buy from the supplier
- Orders — shop checkouts and delivered codes
- Tickets — open supplier tickets (replies still go to the buyer in Telegram)
- Settings — Stars per USD, welcome, and help copy used by the bot

The Telegram bot reads the same database, so catalog prices, visibility, keys, and copy update without a bot restart.

## Run the bot

```bash
python3 -m bot.app
```

Buyer flow:

1. `/start` — a persistent bar appears: Menu, Balance, My Orders
2. Menu opens the catalog; Balance shows your Stars spend; My Orders lists your purchases
3. Pick a product and quantity
4. For invite products, send the required email/username
5. Pay the Stars invoice in Telegram
6. CDK codes arrive in chat; invite orders are polled until delivered
7. `/paysupport` if a Stars payment needs help (Telegram requires this command)

Each Telegram user has a private order list. Opening another buyer’s checkout is rejected. Uncaught bot errors send a short reply instead of hanging.

## Reseller API client

```python
from reseller_api import Customer, ResellerClient, place_and_deliver

with ResellerClient() as client:
    me = client.get_me()
    products = client.list_products()
    result = place_and_deliver(Customer(id="buyer-1"), product_id=1, qty=1, client=client)
```

CLI:

```bash
reseller me
reseller products
reseller place-and-deliver 1 --qty 1
```

## Tests

```bash
pytest -q
```
