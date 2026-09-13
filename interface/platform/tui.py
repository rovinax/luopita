from __future__ import annotations

import json
from collections import defaultdict

from fastapi import WebSocket

from msg.schema import OutboundMessage
from utils.config import TuiSettings


class TuiAdapter:
    name = "tui"

    def __init__(self, settings: TuiSettings) -> None:
        self.settings = settings
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)

    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    def update(self, settings: TuiSettings) -> None:
        self.settings = settings

    def register(self, chat_id: str, ws: WebSocket) -> None:
        self._clients[chat_id].add(ws)
        self._clients["*"].add(ws)

    def unregister(self, chat_id: str, ws: WebSocket) -> None:
        self._clients[chat_id].discard(ws)
        self._clients["*"].discard(ws)

    async def send(self, message: OutboundMessage) -> str | None:
        if not self.enabled():
            return None
        targets = set(self._clients.get(message.chat_id) or set())
        if not targets:
            targets = set(self._clients.get("*") or set())
        stale: list[WebSocket] = []
        payload = json.dumps(
            {
                "type": "reply",
                "text": message.text,
                "session_id": message.session_key,
                "chat_id": message.chat_id,
            },
            ensure_ascii=False,
        )
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.unregister(message.chat_id, ws)
        return None


class AdminAdapter:
    name = "admin"

    def enabled(self) -> bool:
        return True

    async def send(self, message: OutboundMessage) -> str | None:
        return None
