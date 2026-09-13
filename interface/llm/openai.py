from typing import Any

import openai

from interface.llm.chat import Client
from interface.llm.files import HttpFilesClient


class OpenAIClient(Client):
    def __init__(self, base_url: str, api_key: str, model: str):
        super().__init__(base_url, api_key, model)
        self.client = openai.OpenAI(base_url=base_url, api_key=api_key)
        self._files = HttpFilesClient(base_url=base_url, api_key=api_key)

    def chat(self, messages: list[dict[str, Any]]) -> str:
        resp = self.client.chat.completions.create(model=self.model, messages=messages, stream=False)
        return (resp.choices[0].message.content or "").strip()

    async def upload_user_file(self, filename: str, data: bytes, mime: str = "") -> str:
        return await self._files.upload_user_file(filename, data, mime)