from __future__ import annotations

from typing import Protocol, runtime_checkable

from msg.schema import OutboundMessage


@runtime_checkable
class PlatformAdapter(Protocol):
    name: str

    def enabled(self) -> bool: ...

    async def send(self, message: OutboundMessage) -> str | None: ...
