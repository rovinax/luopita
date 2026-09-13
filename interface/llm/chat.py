from __future__ import annotations

from typing import Any


class Client:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    def chat(self, messages: list[dict[str, Any]]) -> str:
        raise NotImplementedError("Subclasses should implement this method.")

    async def upload_user_file(self, filename: str, data: bytes, mime: str = "") -> str:
        raise NotImplementedError("this provider does not support Files API")
