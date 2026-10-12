from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Checkout:
    id: str
    user_id: int
    chat_id: int
    product_id: int
    product_name: str
    product_type: str
    qty: int
    stars: int
    inputs: dict[str, Any] | None
    status: str
    created_at: float
    telegram_charge_id: str | None = None
    reseller_order_id: int | None = None
    ticket_id: int | None = None
    last_seen_message_id: int | None = None
    delivered_codes: tuple[str, ...] = ()


@dataclass
class Session:
    user_id: int
    step: str
    product_id: int | None = None
    qty: int = 1
    inputs: dict[str, Any] | None = None
    pending_field: str | None = None
    order_id: int | None = None
    ticket_id: int | None = None
    checkout_id: str | None = None
    note: str | None = None


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS checkouts (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    product_name TEXT NOT NULL,
                    product_type TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    stars INTEGER NOT NULL,
                    inputs_json TEXT,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    telegram_charge_id TEXT,
                    reseller_order_id INTEGER,
                    ticket_id INTEGER,
                    last_seen_message_id INTEGER,
                    delivered_codes_json TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    user_id INTEGER PRIMARY KEY,
                    step TEXT NOT NULL,
                    product_id INTEGER,
                    qty INTEGER,
                    inputs_json TEXT,
                    pending_field TEXT,
                    order_id INTEGER,
                    ticket_id INTEGER,
                    checkout_id TEXT,
                    note TEXT
                );
                CREATE TABLE IF NOT EXISTS price_overrides (
                    product_id INTEGER PRIMARY KEY,
                    usd TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS product_meta (
                    product_id INTEGER PRIMARY KEY,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    display_name TEXT,
                    display_description TEXT,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inventory_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checkout_id TEXT,
                    created_at REAL NOT NULL,
                    sold_at REAL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_checkouts_user_created
                    ON checkouts (user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_checkouts_status
                    ON checkouts (status);
                """
            )
            for ddl in (
                "ALTER TABLE checkouts ADD COLUMN last_seen_message_id INTEGER",
                "ALTER TABLE checkouts ADD COLUMN delivered_codes_json TEXT",
            ):
                try:
                    self._conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            self._conn.commit()

    def create_checkout(
        self,
        *,
        user_id: int,
        chat_id: int,
        product_id: int,
        product_name: str,
        product_type: str,
        qty: int,
        stars: int,
        inputs: dict[str, Any] | None,
    ) -> Checkout:
        checkout = Checkout(
            id=uuid.uuid4().hex,
            user_id=user_id,
            chat_id=chat_id,
            product_id=product_id,
            product_name=product_name,
            product_type=product_type,
            qty=qty,
            stars=stars,
            inputs=inputs,
            status="pending_payment",
            created_at=time.time(),
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO checkouts (
                    id, user_id, chat_id, product_id, product_name, product_type,
                    qty, stars, inputs_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkout.id,
                    checkout.user_id,
                    checkout.chat_id,
                    checkout.product_id,
                    checkout.product_name,
                    checkout.product_type,
                    checkout.qty,
                    checkout.stars,
                    json.dumps(checkout.inputs) if checkout.inputs else None,
                    checkout.status,
                    checkout.created_at,
                ),
            )
            self._conn.commit()
        return checkout

    def get_checkout(self, checkout_id: str) -> Checkout | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checkouts WHERE id = ?", (checkout_id,)
            ).fetchone()
        return _checkout_from_row(row) if row else None

    def list_all_orders(self, limit: int = 100) -> list[Checkout]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM checkouts
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_checkout_from_row(row) for row in rows]

    def order_stats(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) AS n FROM checkouts").fetchone()["n"]
            paid = self._conn.execute(
                "SELECT COUNT(*) AS n FROM checkouts WHERE status != 'pending_payment'"
            ).fetchone()["n"]
            delivered = self._conn.execute(
                "SELECT COUNT(*) AS n FROM checkouts WHERE status = 'delivered'"
            ).fetchone()["n"]
        return {"total": int(total), "paid": int(paid), "delivered": int(delivered)}

    def list_user_orders(self, user_id: int, limit: int = 20, offset: int = 0) -> list[Checkout]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM checkouts
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, limit, offset),
            ).fetchall()
        return [_checkout_from_row(row) for row in rows]

    def count_user_orders(self, user_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM checkouts WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def user_paid_stats(self, user_id: int) -> tuple[int, int]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS n, COALESCE(SUM(stars), 0) AS stars
                FROM checkouts
                WHERE user_id = ? AND status != 'pending_payment'
                """,
                (user_id,),
            ).fetchone()
        return int(row["n"] or 0), int(row["stars"] or 0)

    def get_user_checkout(self, user_id: int, checkout_id: str) -> Checkout | None:
        checkout = self.get_checkout(checkout_id)
        if checkout is None or checkout.user_id != user_id:
            return None
        return checkout

    def mark_paid(self, checkout_id: str, telegram_charge_id: str) -> Checkout | None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE checkouts
                SET status = 'paid', telegram_charge_id = ?
                WHERE id = ? AND status = 'pending_payment'
                """,
                (telegram_charge_id, checkout_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM checkouts WHERE id = ?", (checkout_id,)
            ).fetchone()
        return _checkout_from_row(row) if row else None

    def set_reseller_order(self, checkout_id: str, order_id: int, status: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE checkouts
                SET reseller_order_id = ?, status = ?
                WHERE id = ?
                """,
                (order_id, status, checkout_id),
            )
            self._conn.commit()

    def set_status(self, checkout_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE checkouts SET status = ? WHERE id = ?",
                (status, checkout_id),
            )
            self._conn.commit()

    def set_delivered_codes(self, checkout_id: str, codes: list[str] | tuple[str, ...]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE checkouts SET delivered_codes_json = ? WHERE id = ?",
                (json.dumps(list(codes)), checkout_id),
            )
            self._conn.commit()

    def set_ticket(self, checkout_id: str, ticket_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE checkouts SET ticket_id = ? WHERE id = ?",
                (ticket_id, checkout_id),
            )
            self._conn.commit()

    def clear_ticket(self, checkout_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE checkouts SET ticket_id = NULL WHERE id = ?",
                (checkout_id,),
            )
            self._conn.commit()

    def set_last_seen_message(self, checkout_id: str, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE checkouts SET last_seen_message_id = ? WHERE id = ?",
                (message_id, checkout_id),
            )
            self._conn.commit()

    def list_open_tickets(self) -> list[Checkout]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM checkouts WHERE ticket_id IS NOT NULL"
            ).fetchall()
        return [_checkout_from_row(row) for row in rows]

    def find_by_order_id(self, order_id: int) -> Checkout | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checkouts WHERE reseller_order_id = ?",
                (order_id,),
            ).fetchone()
        return _checkout_from_row(row) if row else None

    def get_session(self, user_id: int) -> Session | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()
        return _session_from_row(row) if row else None

    def set_session(self, session: Session) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (
                    user_id, step, product_id, qty, inputs_json, pending_field,
                    order_id, ticket_id, checkout_id, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    step = excluded.step,
                    product_id = excluded.product_id,
                    qty = excluded.qty,
                    inputs_json = excluded.inputs_json,
                    pending_field = excluded.pending_field,
                    order_id = excluded.order_id,
                    ticket_id = excluded.ticket_id,
                    checkout_id = excluded.checkout_id,
                    note = excluded.note
                """,
                (
                    session.user_id,
                    session.step,
                    session.product_id,
                    session.qty,
                    json.dumps(session.inputs) if session.inputs else None,
                    session.pending_field,
                    session.order_id,
                    session.ticket_id,
                    session.checkout_id,
                    session.note,
                ),
            )
            self._conn.commit()

    def clear_session(self, user_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._conn.commit()

    def get_price_override(self, product_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT usd FROM price_overrides WHERE product_id = ?",
                (product_id,),
            ).fetchone()
        return str(row["usd"]) if row else None

    def list_price_overrides(self) -> dict[int, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT product_id, usd FROM price_overrides"
            ).fetchall()
        return {int(row["product_id"]): str(row["usd"]) for row in rows}

    def set_price_override(self, product_id: int, usd: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO price_overrides (product_id, usd, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    usd = excluded.usd,
                    updated_at = excluded.updated_at
                """,
                (product_id, usd, time.time()),
            )
            self._conn.commit()

    def clear_price_override(self, product_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM price_overrides WHERE product_id = ?",
                (product_id,),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def get_product_meta(self, product_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM product_meta WHERE product_id = ?",
                (product_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "product_id": int(row["product_id"]),
            "hidden": bool(row["hidden"]),
            "display_name": row["display_name"],
            "display_description": row["display_description"],
        }

    def list_hidden_product_ids(self) -> set[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT product_id FROM product_meta WHERE hidden = 1"
            ).fetchall()
        return {int(row["product_id"]) for row in rows}

    def set_product_hidden(self, product_id: int, hidden: bool) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO product_meta (product_id, hidden, display_name, display_description, updated_at)
                VALUES (?, ?, NULL, NULL, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    hidden = excluded.hidden,
                    updated_at = excluded.updated_at
                """,
                (product_id, 1 if hidden else 0, time.time()),
            )
            self._conn.commit()

    def set_product_copy(self, product_id: int, display_name: str | None, display_description: str | None) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO product_meta (product_id, hidden, display_name, display_description, updated_at)
                VALUES (?, 0, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    display_description = excluded.display_description,
                    updated_at = excluded.updated_at
                """,
                (product_id, display_name or None, display_description or None, time.time()),
            )
            self._conn.commit()

    def add_inventory_keys(self, product_id: int, codes: list[str]) -> int:
        now = time.time()
        added = 0
        with self._lock:
            for code in codes:
                code = code.strip()
                if not code:
                    continue
                self._conn.execute(
                    """
                    INSERT INTO inventory_keys (product_id, code, status, created_at)
                    VALUES (?, ?, 'available', ?)
                    """,
                    (product_id, code, now),
                )
                added += 1
            self._conn.commit()
        return added

    def inventory_counts(self, product_id: int) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT status, COUNT(*) AS n
                FROM inventory_keys
                WHERE product_id = ?
                GROUP BY status
                """,
                (product_id,),
            ).fetchall()
        counts = {"available": 0, "sold": 0, "reserved": 0}
        for row in rows:
            counts[str(row["status"])] = int(row["n"])
        return counts

    def list_inventory(self, product_id: int, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM inventory_keys WHERE product_id = ?"
        params: list[Any] = [product_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "id": int(row["id"]),
                "product_id": int(row["product_id"]),
                "code": row["code"],
                "status": row["status"],
                "checkout_id": row["checkout_id"],
                "created_at": row["created_at"],
                "sold_at": row["sold_at"],
            }
            for row in rows
        ]

    def pop_inventory_keys(self, product_id: int, qty: int, checkout_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, code FROM inventory_keys
                WHERE product_id = ? AND status = 'available'
                ORDER BY id ASC
                LIMIT ?
                """,
                (product_id, qty),
            ).fetchall()
            if len(rows) < qty:
                return []
            now = time.time()
            codes: list[str] = []
            for row in rows:
                self._conn.execute(
                    """
                    UPDATE inventory_keys
                    SET status = 'sold', checkout_id = ?, sold_at = ?
                    WHERE id = ?
                    """,
                    (checkout_id, now, row["id"]),
                )
                codes.append(str(row["code"]))
            self._conn.commit()
        return codes

    def delete_inventory_key(self, key_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM inventory_keys WHERE id = ? AND status = 'available'",
                (key_id,),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            self._conn.commit()

    def get_stars_per_usd(self, default: float = 20) -> float:
        raw = self.get_setting("stars_per_usd")
        if raw is None:
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        if value <= 0:
            return default
        return value


def _checkout_from_row(row: sqlite3.Row) -> Checkout:
    raw_inputs = row["inputs_json"]
    return Checkout(
        id=row["id"],
        user_id=row["user_id"],
        chat_id=row["chat_id"],
        product_id=row["product_id"],
        product_name=row["product_name"],
        product_type=row["product_type"],
        qty=row["qty"],
        stars=row["stars"],
        inputs=json.loads(raw_inputs) if raw_inputs else None,
        status=row["status"],
        created_at=row["created_at"],
        telegram_charge_id=row["telegram_charge_id"],
        reseller_order_id=row["reseller_order_id"],
        ticket_id=row["ticket_id"],
        last_seen_message_id=row["last_seen_message_id"] if "last_seen_message_id" in row.keys() else None,
        delivered_codes=tuple(json.loads(row["delivered_codes_json"]))
        if "delivered_codes_json" in row.keys() and row["delivered_codes_json"]
        else (),
    )


def _session_from_row(row: sqlite3.Row) -> Session:
    raw_inputs = row["inputs_json"]
    return Session(
        user_id=row["user_id"],
        step=row["step"],
        product_id=row["product_id"],
        qty=row["qty"] or 1,
        inputs=json.loads(raw_inputs) if raw_inputs else None,
        pending_field=row["pending_field"],
        order_id=row["order_id"],
        ticket_id=row["ticket_id"],
        checkout_id=row["checkout_id"],
        note=row["note"],
    )
