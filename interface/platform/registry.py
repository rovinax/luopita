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
        adapter = self._adapters.get(message.platform)
        if adapter is None:
            self.logger.warning(f"no adapter for platform={message.platform}")
            return None
        if not adapter.enabled():
            return None
        return await adapter.send(message)
