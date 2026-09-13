"""NapCat speaks OneBot 11; keep this module as a stable import path."""

from interface.platform.napcat import NapcatAdapter, parse_onebot_event
from interface.platform.napcat_api import NapcatClient

__all__ = ["NapcatAdapter", "parse_onebot_event", "NapcatClient"]
