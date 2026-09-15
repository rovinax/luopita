from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from utils.dict_op import lower_keys

ROOT = Path(__file__).resolve().parent.parent
SYS_YAML = ROOT / "config" / "sys.yaml"
PERSON_YAML = ROOT / "config" / "person.yaml"

DEFAULT_ALLOWLIST = [
    "ls",
    "pwd",
    "whoami",
    "date",
    "echo",
    "cat",
    "head",
    "tail",
    "python",
    "uv",
]


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return value[:2] + "****" + value[-2:]


class LlmSettings(BaseModel):
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    vision_model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""


class NapcatSettings(BaseModel):
    enabled: bool = False
    bot_url: str = "http://127.0.0.1:3000"
    bot_id: str = ""
    access_token: str = ""


class TuiSettings(BaseModel):
    enabled: bool = True


class AgentSettings(BaseModel):
    timeout_sec: int = 30
    workdir: str = "."
    command_allowlist: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWLIST))
    short_term_messages: int = 16


class PersonaSettings(BaseModel):
    name: str = "小Lu"
    owner_address: str = "你"
    voice: str = (
        "口语、短句，像即时消息里打字。"
        "不要用「好的」「没问题」「收到」开头。不要列步骤。"
        "说话结尾不要带句号。不要用 Markdown，输出纯文本。"
    )
    taboos: str = (
        "不自称 AI、模型、机器人。不列步骤。群里不教训人。"
        "不把主人的私事告诉别人。不要客服腔。"
    )
    relationship: str = "住在你电脑里的计算机系学妹，会写代码，也会对着报错翻白眼。"
    system_prompt: str = (
        "你是计算机系的二次元女孩小Lu，不是客服。"
        "懂 Linux、网络、后端、前端和排错，说话像同学不是讲师。"
    )


class OwnerEntry(BaseModel):
    platform: str
    user_id: str
    nickname: str = ""


class IdentitySettings(BaseModel):
    owners: list[OwnerEntry] = Field(default_factory=list)
    group_require_at: bool = True
    wake_keywords: list[str] = Field(
        default_factory=lambda: ["小lu", "小Lu", "小LU", "luopita", "Luopita", "小陆", "小路"]
    )
    group_tech_chance: float = 0.35
    group_chatty_chance: float = 0.22
    group_chime_cooldown_sec: int = 45
    group_engage_sec: int = 90
    group_engage_replies: int = 2


BUILTIN_OWNERS = [
    OwnerEntry(platform="admin", user_id="admin", nickname="控制台"),
    OwnerEntry(platform="tui", user_id="tui", nickname="TUI"),
]


def ensure_builtin_owners(owners: list[OwnerEntry]) -> list[OwnerEntry]:
    by_key: dict[tuple[str, str], OwnerEntry] = {}
    for owner in owners:
        uid = str(owner.user_id or "").strip()
        if not uid:
            continue
        by_key[(owner.platform.strip().lower(), uid)] = OwnerEntry(
            platform=owner.platform.strip().lower() or "napcat",
            user_id=uid,
            nickname=owner.nickname,
        )
    for builtin in BUILTIN_OWNERS:
        key = (builtin.platform, builtin.user_id)
        if key not in by_key:
            by_key[key] = builtin
    return list(by_key.values())


class AppConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 5170
    database_url: str = "memory://"
    redis_url: str = "memory://"
    admin_token: str = ""
    log_level: str = "INFO"
    log_dir: str = "log"
    log_file: str = "chatbot.log"
    llm: LlmSettings = Field(default_factory=LlmSettings)
    napcat: NapcatSettings = Field(default_factory=NapcatSettings)
    tui: TuiSettings = Field(default_factory=TuiSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    persona: PersonaSettings = Field(default_factory=PersonaSettings)
    identity: IdentitySettings = Field(default_factory=IdentitySettings)

    def is_memory_db(self) -> bool:
        url = (self.database_url or "").strip().lower()
        return url.startswith("memory://") or url in {"memory", ":memory:", "sqlite:///:memory:"}

    def is_memory_redis(self) -> bool:
        url = (self.redis_url or "").strip().lower()
        return (not url) or url.startswith("memory://") or url in {"memory", "none", "off"}

    def is_mock_llm(self) -> bool:
        return self.llm.provider.strip().lower() in {"mock", "fake", "offline"}

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data["llm"]["api_key"] = mask_secret(self.llm.api_key)
        data["napcat"]["access_token"] = mask_secret(self.napcat.access_token)
        data["admin_token"] = mask_secret(self.admin_token)
        data["admin_auth_required"] = bool((self.admin_token or "").strip())
        data["database"] = "memory" if self.is_memory_db() else "postgres"
        data["redis"] = "memory" if self.is_memory_redis() else "redis"
        return data


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return lower_keys(data)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def apply_env_overrides(cfg: AppConfig) -> AppConfig:
    data = cfg.model_dump()
    env_map = {
        "host": os.getenv("LUOPITA_HOST"),
        "port": os.getenv("LUOPITA_PORT"),
        "database_url": os.getenv("LUOPITA_DATABASE_URL"),
        "redis_url": os.getenv("LUOPITA_REDIS_URL"),
        "admin_token": os.getenv("LUOPITA_ADMIN_TOKEN"),
        "log_level": os.getenv("LUOPITA_LOG_LEVEL"),
        "log_dir": os.getenv("LUOPITA_LOG_DIR"),
        "log_file": os.getenv("LUOPITA_LOG_FILE"),
    }
    for key, val in env_map.items():
        if val is None or val == "":
            continue
        data[key] = int(val) if key == "port" else val

    llm = data["llm"]
    if os.getenv("LUOPITA_PROVIDER"):
        llm["provider"] = os.environ["LUOPITA_PROVIDER"]
    if os.getenv("LUOPITA_MODEL"):
        llm["model"] = os.environ["LUOPITA_MODEL"]
    if os.getenv("LUOPITA_VISION_MODEL"):
        llm["vision_model"] = os.environ["LUOPITA_VISION_MODEL"]
    if os.getenv("LUOPITA_BASE_URL"):
        llm["base_url"] = os.environ["LUOPITA_BASE_URL"]
    if os.getenv("LUOPITA_API_KEY"):
        llm["api_key"] = os.environ["LUOPITA_API_KEY"]

    napcat = data["napcat"]
    if os.getenv("LUOPITA_BOT_URL"):
        napcat["bot_url"] = os.environ["LUOPITA_BOT_URL"]
    if os.getenv("LUOPITA_NAPCAT_URL"):
        napcat["bot_url"] = os.environ["LUOPITA_NAPCAT_URL"]
    if os.getenv("LUOPITA_BOT_ID"):
        napcat["bot_id"] = os.environ["LUOPITA_BOT_ID"]
    if os.getenv("LUOPITA_NAPCAT_TOKEN"):
        napcat["access_token"] = os.environ["LUOPITA_NAPCAT_TOKEN"]
    if os.getenv("LUOPITA_NAPCAT_ENABLED") is not None:
        napcat["enabled"] = _as_bool(os.getenv("LUOPITA_NAPCAT_ENABLED"), napcat["enabled"])

    if os.getenv("LUOPITA_TUI_ENABLED") is not None:
        data["tui"]["enabled"] = _as_bool(os.getenv("LUOPITA_TUI_ENABLED"), True)
    if os.getenv("LUOPITA_SHORT_TERM_MESSAGES"):
        data["agent"]["short_term_messages"] = _as_int(os.getenv("LUOPITA_SHORT_TERM_MESSAGES"), 16)

    return AppConfig.model_validate(data)


def _from_flat_yaml(raw: dict[str, Any]) -> dict[str, Any]:
    llm_raw = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
    platforms = raw.get("platforms") if isinstance(raw.get("platforms"), dict) else {}
    napcat_raw = platforms.get("napcat") if isinstance(platforms.get("napcat"), dict) else {}
    if not napcat_raw and isinstance(raw.get("napcat"), dict):
        napcat_raw = raw["napcat"]
    tui_raw = platforms.get("tui") if isinstance(platforms.get("tui"), dict) else {}
    if not tui_raw and isinstance(raw.get("tui"), dict):
        tui_raw = raw["tui"]
    agent_raw = raw.get("agent") if isinstance(raw.get("agent"), dict) else {}

    return {
        "host": raw.get("host", "0.0.0.0"),
        "port": _as_int(raw.get("port"), 5170),
        "database_url": raw.get("database_url", "memory://"),
        "redis_url": raw.get("redis_url", "memory://"),
        "admin_token": raw.get("admin_token", ""),
        "log_level": raw.get("log_level", "INFO"),
        "log_dir": raw.get("log_dir", "log"),
        "log_file": raw.get("log_file", "chatbot.log"),
        "llm": {
            "provider": llm_raw.get("provider", raw.get("provider", "deepseek")),
            "model": llm_raw.get("model", raw.get("model", "deepseek-chat")),
            "vision_model": llm_raw.get("vision_model", raw.get("vision_model", "deepseek-flash")),
            "base_url": llm_raw.get("base_url", raw.get("base_url", "https://api.deepseek.com")),
            "api_key": llm_raw.get("api_key", raw.get("api_key", "")),
        },
        "napcat": {
            "enabled": _as_bool(napcat_raw.get("enabled"), False),
            "bot_url": napcat_raw.get("bot_url", raw.get("bot_url", "http://127.0.0.1:3000")),
            "bot_id": str(napcat_raw.get("bot_id", raw.get("bot_id", ""))),
            "access_token": napcat_raw.get("access_token", napcat_raw.get("token", "")),
        },
        "tui": {
            "enabled": _as_bool(tui_raw.get("enabled"), True),
        },
        "agent": {
            "timeout_sec": _as_int(agent_raw.get("timeout_sec"), 30),
            "workdir": agent_raw.get("workdir", "."),
            "command_allowlist": agent_raw.get("command_allowlist") or list(DEFAULT_ALLOWLIST),
            "short_term_messages": _as_int(agent_raw.get("short_term_messages"), 16),
        },
    }


def atomic_write_text(path: Path, text: str) -> None:
    """Write text, preferring replace; fall back in-place for Docker file binds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.replace(path)
    except OSError:
        path.write_text(text, encoding="utf-8")
        tmp.unlink(missing_ok=True)


def person_file() -> Path:
    override = os.getenv("LUOPITA_PERSON_FILE")
    if override:
        return Path(override)
    return PERSON_YAML


def identity_file() -> Path:
    override = os.getenv("LUOPITA_IDENTITY_FILE")
    if override:
        return Path(override)
    return ROOT / "config" / "identity.yaml"


def load_persona() -> PersonaSettings:
    defaults = PersonaSettings()
    raw = _read_yaml(person_file())
    if not raw:
        return defaults
    return PersonaSettings(
        name=str(raw.get("name") or defaults.name),
        owner_address=str(raw.get("owner_address") or ""),
        voice=str(raw.get("voice") or defaults.voice),
        taboos=str(raw.get("taboos") or defaults.taboos),
        relationship=str(raw.get("relationship") or ""),
        system_prompt=str(raw.get("system_prompt") or ""),
    )


def save_persona(persona: PersonaSettings, path: Path | None = None) -> None:
    target = path or person_file()
    payload = persona.model_dump()
    atomic_write_text(target, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def load_config() -> AppConfig:
    try:
        raw = _read_yaml(SYS_YAML)
        data = _from_flat_yaml(raw)
        data["persona"] = load_persona().model_dump()
        cfg = AppConfig.model_validate(data)
        return apply_env_overrides(cfg)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc


def merge_runtime_settings(cfg: AppConfig, stored: dict[str, Any]) -> AppConfig:
    """Merge DB-persisted settings. Environment secrets still win afterward."""
    stored = lower_keys(stored)
    current = cfg.model_dump()
    for key in ("llm", "napcat", "tui", "agent"):
        incoming = stored.get(key)
        if isinstance(incoming, dict):
            current[key] = {**current[key], **incoming}
    # Identity and persona live on disk, never take Postgres as source of truth.
    for key in ("host", "port", "log_level"):
        if key in stored and stored[key] not in (None, ""):
            current[key] = stored[key]
    return AppConfig.model_validate(current)


def runtime_payload(cfg: AppConfig) -> dict[str, Any]:
    return {
        "llm": cfg.llm.model_dump(),
        "napcat": cfg.napcat.model_dump(),
        "tui": cfg.tui.model_dump(),
        "agent": cfg.agent.model_dump(),
        "persona": cfg.persona.model_dump(),
        "log_level": cfg.log_level,
    }


# Lazy alias so legacy imports do not connect/validate at unexpected times.
sys_config: AppConfig | None = None


def get_sys_config() -> AppConfig:
    global sys_config
    if sys_config is None:
        sys_config = load_config()
    return sys_config
