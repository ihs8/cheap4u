from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from reseller_api.client import ResellerClient
from reseller_api.errors import ResellerAPIError
from reseller_api.glue import Customer, place_and_deliver


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _dump(obj: Any) -> str:
    if hasattr(obj, "__dict__"):
        data = {}
        for key, value in obj.__dict__.items():
            if hasattr(value, "__dict__") and not isinstance(value, type):
                data[key] = value
            elif isinstance(value, tuple):
                data[key] = [
                    item.__dict__ if hasattr(item, "__dict__") else item for item in value
                ]
            else:
                data[key] = value
        return json.dumps(data, default=str, indent=2)
    return json.dumps(obj, default=str, indent=2)


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="reseller", description="Reseller API CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("me", help="Show reseller profile and wallet balance")
    sub.add_parser("products", help="List active products")

    p_get = sub.add_parser("product", help="Show one product")
    p_get.add_argument("product_id", type=int)

    p_order = sub.add_parser("order", help="Place an order")
    p_order.add_argument("product_id", type=int)
    p_order.add_argument("--qty", type=int, default=1)
    p_order.add_argument("--email")
    p_order.add_argument("--username")
    p_order.add_argument("--idempotency-key")

    p_get_order = sub.add_parser("get-order", help="Fetch one order")
    p_get_order.add_argument("order_id", type=int)

    p_orders = sub.add_parser("orders", help="List orders")
    p_orders.add_argument("--cursor", type=int)
    p_orders.add_argument("--limit", type=int, default=20)

    p_deliver = sub.add_parser("place-and-deliver", help="Place an order and wait for delivery")
    p_deliver.add_argument("product_id", type=int)
    p_deliver.add_argument("--qty", type=int, default=1)
    p_deliver.add_argument("--email")
    p_deliver.add_argument("--username")
    p_deliver.add_argument("--customer-id", default="cli")

    p_ticket_open = sub.add_parser("open-ticket")
    p_ticket_open.add_argument("order_id", type=int)
    p_ticket_open.add_argument("message")

    p_ticket_msg = sub.add_parser("ticket-message")
    p_ticket_msg.add_argument("ticket_id", type=int)
    p_ticket_msg.add_argument("message")

    p_ticket = sub.add_parser("ticket")
    p_ticket.add_argument("ticket_id", type=int)

    p_tickets = sub.add_parser("tickets")
    p_tickets.add_argument("--status")
    p_tickets.add_argument("--order-id", type=int)

    p_close = sub.add_parser("close-ticket")
    p_close.add_argument("ticket_id", type=int)

    args = parser.parse_args(argv)

    try:
        with ResellerClient() as client:
            if args.cmd == "me":
                print(_dump(client.get_me()))
                return 0
            if args.cmd == "products":
                for product in client.list_products():
                    print(_dump(product))
                return 0
            if args.cmd == "product":
                print(_dump(client.get_product(args.product_id)))
                return 0
            if args.cmd == "order":
                inputs = {}
                if args.email:
                    inputs["email"] = args.email
                if args.username:
                    inputs["username"] = args.username
                order = client.place_order(
                    args.product_id,
                    quantity=args.qty,
                    inputs=inputs or None,
                    idempotency_key=args.idempotency_key,
                )
                print(_dump(order))
                return 0
            if args.cmd == "get-order":
                print(_dump(client.get_order(args.order_id)))
                return 0
            if args.cmd == "orders":
                print(_dump(client.list_orders(cursor=args.cursor, limit=args.limit)))
                return 0
            if args.cmd == "place-and-deliver":
                inputs = {}
                if args.email:
                    inputs["email"] = args.email
                if args.username:
                    inputs["username"] = args.username
                result = place_and_deliver(
                    Customer(id=args.customer_id),
                    args.product_id,
                    qty=args.qty,
                    inputs=inputs or None,
                    client=client,
                )
                print(_dump(result))
                return 0
            if args.cmd == "open-ticket":
                print(_dump(client.open_ticket(args.order_id, args.message)))
                return 0
            if args.cmd == "ticket-message":
                print(_dump(client.post_ticket_message(args.ticket_id, args.message)))
                return 0
            if args.cmd == "ticket":
                print(_dump(client.get_ticket(args.ticket_id)))
                return 0
            if args.cmd == "tickets":
                print(_dump(client.list_tickets(status=args.status, order_id=args.order_id)))
                return 0
            if args.cmd == "close-ticket":
                print(_dump(client.close_ticket(args.ticket_id)))
                return 0
    except ResellerAPIError as exc:
        logging.getLogger("reseller_api").error(
            "api error code=%s request_id=%s message=%s",
            exc.code,
            exc.request_id,
            exc.message,
        )
        print(json.dumps({"code": exc.code, "message": exc.message, "request_id": exc.request_id}))
        return 1
    except NotImplementedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
