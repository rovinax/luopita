from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from core.identity import default_session_key


PlatformName = Literal["napcat", "tui", "admin"]
ChannelType = Literal["private", "group", "console"]


class MediaRef(BaseModel):
    kind: Literal["image", "file"] = "image"
    file_id: str = ""
    url: str = ""
    name: str = ""
    mime: str = ""


class InboundMessage(BaseModel):
    platform: PlatformName
    channel_type: ChannelType
    chat_id: str
    user_id: str
    text: str
    session_key: str = ""
    raw: dict[str, Any] | None = None
    message_id: str | None = None
    sender_name: str | None = None
    at_user_ids: list[str] = Field(default_factory=list)
    reply_to_ids: list[str] = Field(default_factory=list)
    self_id: str = ""
    media: list[MediaRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def fill_session_key(self) -> "InboundMessage":
        if not self.session_key:
            self.session_key = default_session_key(
                self.platform, self.channel_type, self.chat_id, self.user_id
            )
        return self


class OutboundMessage(BaseModel):
    platform: PlatformName
    channel_type: ChannelType
    chat_id: str
    text: str = ""
    session_key: str = ""
    user_id: str | None = None
    segments: list[dict[str, Any]] | None = None


class ChatRequest(BaseModel):
    text: str
    user_id: str | None = None
    session_id: str | None = None


class ChatResponse(BaseModel):
    ok: bool = True
    session_id: str = ""
    reply: str = ""
    ignored: bool = False
    role: str = "user"


class ConfigUpdate(BaseModel):
    llm: dict[str, Any] | None = None
    napcat: dict[str, Any] | None = None
    tui: dict[str, Any] | None = None
    agent: dict[str, Any] | None = None
    persona: dict[str, Any] | None = None
    host: str | None = None
    port: int | None = None
    log_level: str | None = None


class PersonaUpdate(BaseModel):
    name: str | None = None
    owner_address: str | None = None
    voice: str | None = None
    taboos: str | None = None
    relationship: str | None = None
    system_prompt: str | None = None


class IdentityUpdate(BaseModel):
    owners: list[dict[str, Any]] | None = None
    group_require_at: bool | None = None
    wake_keywords: list[str] | None = None
    group_tech_chance: float | None = None
    group_chatty_chance: float | None = None
    group_chime_cooldown_sec: int | None = None
    group_engage_sec: int | None = None
    group_engage_replies: int | None = None
