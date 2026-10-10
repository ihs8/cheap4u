# Reseller API client

Typed Python client for the Reseller API at `https://cgpt-active.pro/telegram/api`.

## Setup

```bash
# Install runtime + test dependencies
pip install --break-system-packages -r requirements.txt

# Copy the example env file and paste the key from the /become_reseller approval DM
cp .env.example .env
```

Required environment variables:

```
RESELLER_API_KEY=rsk_xxx
RESELLER_API_BASE_URL=https://cgpt-active.pro/telegram/api
```

## Usage

```python
from reseller_api import Customer, ResellerClient, place_and_deliver

with ResellerClient() as client:
    me = client.get_me()
    products = client.list_products()
    order = client.place_order(product_id=1, quantity=1, idempotency_key="...")
    result = place_and_deliver(Customer(id="buyer-1"), product_id=1, qty=1, client=client)
    print(result.delivered_codes)
```

CLI (after `pip install --break-system-packages -e .`):

```bash
# Show profile and wallet
reseller me

# List products
reseller products

# Place and wait for delivery
reseller place-and-deliver 1 --qty 1
```

`place_and_deliver` returns CDK codes immediately. For `team_invite` products it polls `fulfillment_status` with exponential backoff (2s -> 60s, 10 min ceiling), asks the buyer for a corrected input on `input_rejected`, and surfaces a support message on `failed`.

## Tests

```bash
# Run the unit suite (mocked HTTP, no live key required)
pytest -q
```
