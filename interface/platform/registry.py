from __future__ import annotations

from interface.platform.base import PlatformAdapter
from msg.schema import OutboundMessage
from utils.log import ChatbotLogger


class AdapterRegistry:
    def __init__(self, adapters: list[PlatformAdapter], logger: ChatbotLogger | None = None) -> None:
        self._adapters = {adapter.name: adapter for adapter in adapters}
        self.logger = logger or ChatbotLogger()

    def get(self, name: str) -> PlatformAdapter | None:
        return self._adapters.get(name)

    def snapshot(self) -> list[dict]:
        items = []
        for name, adapter in self._adapters.items():
            items.append({"name": name, "enabled": adapter.enabled()})
        return items

    async def send(self, message: OutboundMessage) -> str | None:
        from core.outbound_sanitize import sanitize_outbound_text

        adapter = self._adapters.get(message.platform)
        if adapter is None:
            self.logger.warning(f"no adapter for platform={message.platform}")
            return None
        if not adapter.enabled():
            return None
        raw = message.text or ""
        cleaned = sanitize_outbound_text(raw)
        if not cleaned.strip():
            self.logger.warning(
                f"blocked empty/markup outbound platform={message.platform} chat={message.chat_id}"
            )
            return None
        if cleaned != raw:
            message = message.model_copy(update={"text": cleaned})
            self.logger.info(
                f"sanitized outbound markup platform={message.platform} chat={message.chat_id}"
            )
        return await adapter.send(message)
