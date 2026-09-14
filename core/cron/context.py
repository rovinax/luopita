from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class CronDelivery:
    platform: str
    channel_type: str
    chat_id: str
    user_id: str


_current_delivery: ContextVar[CronDelivery | None] = ContextVar(
    "luopita_cron_delivery", default=None
)


def current_cron_delivery() -> CronDelivery | None:
    return _current_delivery.get()


def set_cron_delivery(delivery: CronDelivery | None):
    return _current_delivery.set(delivery)


def reset_cron_delivery(token) -> None:
    _current_delivery.reset(token)
