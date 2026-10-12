from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "bot.sqlite3"


def load_env_file(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _int_list(name: str) -> tuple[int, ...]:
    raw = os.getenv(name, "")
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            values.append(int(part))
    return tuple(values)


class Settings:
    def __init__(self) -> None:
        load_env_file()
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.admin_ids = _int_list("TELEGRAM_ADMIN_IDS")
        self.stars_per_usd = float(os.getenv("STARS_PER_USD", "20"))
        self.db_path = Path(os.getenv("BOT_DB_PATH", str(DEFAULT_DB_PATH)))
        self.store_name = os.getenv("STORE_NAME", "Cheap4U")
        self.max_qty = int(os.getenv("MAX_QTY", "5"))
        self.dashboard_password = os.getenv("DASHBOARD_PASSWORD", "").strip()
        self.dashboard_secret = os.getenv("DASHBOARD_SECRET", "cheap4u-dashboard-secret").strip()


def require_bot_token(settings: Settings) -> str:
    if not settings.bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return settings.bot_token
