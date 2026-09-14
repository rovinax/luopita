from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

from core.media import normalize_deepseek_message
from interface.llm.chat import Client
from interface.llm.deepseek import DeepSeekClient
from interface.llm.files import HttpFilesClient, MockFilesClient
from interface.llm.openai import OpenAIClient


class MockClient(Client):
    def chat(self, messages):
        last = messages[-1]["content"] if messages else ""
        return f"(mock) {last}"

    async def upload_user_file(self, filename: str, data: bytes, mime: str = "") -> str:
        return await MockFilesClient().upload_user_file(filename, data, mime)


class MockChatModel(BaseChatModel):
    """Deterministic chat model for tests and offline mode."""

    @property
    def _llm_type(self) -> str:
        return "mock"

    def _extract_last_human(self, messages: list[BaseMessage]) -> str:
        for message in reversed(messages):
            if getattr(message, "type", "") == "human":
                return _plain_content(message.content)
        if messages:
            return _plain_content(messages[-1].content)
        return ""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        last = self._extract_last_human(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=f"(mock) {last}"))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "MockChatModel":
        return self


def _plain_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content or "")


def get_llm_client(provider: str, base_url: str, api_key: str, model: str) -> Client:
    p = (provider or "").strip().lower()
    if p in ("mock", "fake", "offline"):
        return MockClient(base_url=base_url, api_key=api_key, model=model)
    if p in ("deepseek", "deepseek-chat", "deepseek_api"):
        return DeepSeekClient(base_url=base_url, api_key=api_key, model=model)
    if p in ("openai", "oai"):
        return OpenAIClient(base_url=base_url, api_key=api_key, model=model)
    raise ValueError(f"Unknown provider: {provider}")


class DeepSeekChatModel(ChatOpenAI):
    """ChatOpenAI nests file_id under file; DeepSeek wants it at the top level."""

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = payload.get("messages")
        if isinstance(messages, list):
            payload["messages"] = [normalize_deepseek_message(item) for item in messages]
        return payload


def get_chat_model(
    provider: str, base_url: str, api_key: str, model: str, temperature: float | None = None
) -> BaseChatModel:
    p = (provider or "").strip().lower()
    if p in ("mock", "fake", "offline"):
        return MockChatModel()
    if not api_key:
        return MockChatModel()
    kwargs = {
        "model": model or "deepseek-chat",
        "api_key": api_key,
        "base_url": base_url or None,
        "temperature": 0.4 if temperature is None else temperature,
    }
    if p in ("deepseek", "deepseek-chat", "deepseek_api"):
        return DeepSeekChatModel(**kwargs)
    return ChatOpenAI(**kwargs)


def get_files_client(provider: str, base_url: str, api_key: str):
    p = (provider or "").strip().lower()
    if p in ("mock", "fake", "offline") or not api_key:
        return MockFilesClient()
    return HttpFilesClient(base_url=base_url, api_key=api_key)


def vision_model_name(provider: str, model: str, vision_model: str = "") -> str:
    if (vision_model or "").strip():
        return vision_model.strip()
    p = (provider or "").strip().lower()
    if p in ("deepseek", "deepseek-chat", "deepseek_api"):
        return "deepseek-flash"
    return (model or "").strip() or "deepseek-chat"
