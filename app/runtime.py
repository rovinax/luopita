from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.chat import ChatOrchestrator
from core.database import Database, create_database
from core.graph import build_graph, make_memory_checkpointer
from core.group_context.pipeline import GroupContextPipeline
from core.group_context.redis_client import InMemoryRedis, RedisClient, create_redis
from core.group_context.store import HotStore
from core.identity import IdentityStore
from core.memory import MemoryService
from interface.platform.napcat import NapcatAdapter
from interface.platform.registry import AdapterRegistry
from interface.platform.tui import AdminAdapter, TuiAdapter
from msg.schema import ConfigUpdate, IdentityUpdate
from utils.config import (
    AppConfig,
    IdentitySettings,
    OwnerEntry,
    load_config,
    load_persona,
    merge_runtime_settings,
    runtime_payload,
    save_persona,
)
from utils.log import ChatbotLogger


def _merge_section(current: dict[str, Any], patch: dict[str, Any], secret_keys: set[str]) -> dict[str, Any]:
    out = {**current}
    for key, value in patch.items():
        if key in secret_keys and (value is None or value == "" or str(value).startswith("**") or "****" in str(value)):
            continue
        out[key] = value
    return out


@dataclass
class Runtime:
    config: AppConfig
    logger: ChatbotLogger
    db: Database
    redis: RedisClient
    hot: HotStore
    group_pipeline: GroupContextPipeline
    memory: MemoryService
    checkpointer: Any
    graph: Any
    owner_graph: Any
    user_graph: Any
    adapters: AdapterRegistry
    orchestrator: ChatOrchestrator
    napcat: NapcatAdapter
    tui: TuiAdapter
    identity: IdentityStore
    _checkpointer_pool: Any = field(default=None)

    @classmethod
    async def start(cls) -> "Runtime":
        config = load_config()
        logger = ChatbotLogger(log_dir=config.log_dir, log_file=config.log_file, log_level=config.log_level)
        db = create_database(config.database_url)
        try:
            await db.init()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to connect to database ({config.database_url}). "
                "Start Postgres via docker compose, or set LUOPITA_DATABASE_URL=memory:// for local/dev."
            ) from exc
        redis_url = config.redis_url
        if config.is_memory_db() and config.is_memory_redis():
            redis_url = "memory://"
        redis = create_redis(redis_url)
        try:
            await redis.init()
        except Exception as exc:
            logger.warning(f"redis unavailable ({redis_url}), falling back to memory: {exc}")
            redis = create_redis("memory://")
            await redis.init()
        hot = HotStore(redis)
        group_pipeline = GroupContextPipeline(hot, db)
        stored = await db.get_settings()
        if stored:
            config = merge_runtime_settings(config, stored)
            from utils.config import apply_env_overrides

            config = apply_env_overrides(config)

        identity = IdentityStore()
        identity_settings = identity.load()
        config = config.model_copy(update={"identity": identity_settings, "persona": load_persona()})

        checkpointer, pool = await cls._make_checkpointer(config)
        napcat = NapcatAdapter(config.napcat, logger=logger)
        tui = TuiAdapter(config.tui)
        adapters = AdapterRegistry([napcat, tui, AdminAdapter()], logger=logger)
        runtime = cls(
            config=config,
            logger=logger,
            db=db,
            redis=redis,
            hot=hot,
            group_pipeline=group_pipeline,
            memory=MemoryService(db),
            checkpointer=checkpointer,
            graph=None,
            owner_graph=None,
            user_graph=None,
            adapters=adapters,
            orchestrator=None,  # type: ignore[arg-type]
            napcat=napcat,
            tui=tui,
            identity=identity,
            _checkpointer_pool=pool,
        )
        runtime.owner_graph = build_graph(
            config, logger, checkpointer, runtime.get_config, napcat=napcat, role="owner", memory=runtime.memory
        )
        runtime.user_graph = build_graph(
            config, logger, checkpointer, runtime.get_config, napcat=napcat, role="user", memory=runtime.memory
        )
        runtime.graph = runtime.owner_graph
        runtime.orchestrator = ChatOrchestrator(
            config=config,
            logger=logger,
            memory=runtime.memory,
            owner_graph=runtime.owner_graph,
            user_graph=runtime.user_graph,
            adapters=adapters,
            get_config=runtime.get_config,
            identity=identity,
            group_pipeline=group_pipeline,
        )
        await runtime._sync_bot_id()
        logger.info(
            f"runtime started provider={config.llm.provider} "
            f"db={'memory' if config.is_memory_db() else 'postgres'} "
            f"redis={'memory' if isinstance(redis, InMemoryRedis) else 'redis'}"
        )
        return runtime

    def get_config(self) -> AppConfig:
        return self.config

    async def _sync_bot_id(self) -> None:
        if not self.config.napcat.enabled:
            return
        try:
            info = await self.napcat.api.call("get_login_info")
        except Exception as exc:
            self.logger.warning(f"napcat bot_id sync skip: {exc}")
            return
        uid = str((info or {}).get("user_id") or "").strip()
        if uid:
            self.config.napcat.bot_id = uid
            self.logger.info(f"napcat bot_id={uid}")

    @staticmethod
    async def _make_checkpointer(config: AppConfig) -> tuple[Any, Any]:
        if config.is_memory_db():
            return make_memory_checkpointer(), None
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            conninfo=config.database_url,
            min_size=1,
            max_size=4,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            open=False,
        )
        await pool.open()
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        return saver, pool

    def rebuild(self) -> None:
        self.napcat.update(self.config.napcat)
        self.tui.update(self.config.tui)
        self.owner_graph = build_graph(
            self.config, self.logger, self.checkpointer, self.get_config, napcat=self.napcat, role="owner", memory=self.memory
        )
        self.user_graph = build_graph(
            self.config, self.logger, self.checkpointer, self.get_config, napcat=self.napcat, role="user", memory=self.memory
        )
        self.graph = self.owner_graph
        self.orchestrator.attach(
            self.owner_graph, self.user_graph, self.adapters, self.config, identity=self.identity,
            group_pipeline=self.group_pipeline,
        )

    async def apply_update(self, patch: ConfigUpdate) -> AppConfig:
        data = self.config.model_dump()
        if patch.llm:
            data["llm"] = _merge_section(data["llm"], patch.llm, {"api_key"})
        if patch.napcat:
            data["napcat"] = _merge_section(data["napcat"], patch.napcat, {"access_token"})
        if patch.tui:
            data["tui"] = {**data["tui"], **patch.tui}
        if patch.agent:
            data["agent"] = {**data["agent"], **patch.agent}
        if patch.persona:
            data["persona"] = {**data["persona"], **patch.persona}
        if patch.host:
            data["host"] = patch.host
        if patch.port is not None:
            data["port"] = patch.port
        if patch.log_level:
            data["log_level"] = patch.log_level
        self.config = AppConfig.model_validate(data)
        if patch.persona:
            save_persona(self.config.persona)
        await self.db.put_settings(runtime_payload(self.config))
        if patch.agent:
            self.logger.info(
                "agent allowlist updated cmds="
                + ",".join(self.config.agent.command_allowlist)
            )
        self.rebuild()
        return self.config

    def apply_identity(self, patch: IdentityUpdate | IdentitySettings) -> IdentitySettings:
        if isinstance(patch, IdentitySettings):
            saved = self.identity.save(patch)
        else:
            data = self.identity.settings.model_dump()
            updates = patch.model_dump(exclude_none=True)
            if "owners" in updates:
                data["owners"] = [OwnerEntry.model_validate(item) for item in updates["owners"]]
                del updates["owners"]
            data.update(updates)
            saved = self.identity.save(IdentitySettings.model_validate(data))
        self.config = self.config.model_copy(update={"identity": saved})
        return saved

    async def toggle_platform(self, name: str, enabled: bool | None = None) -> AppConfig:
        data = self.config.model_dump()
        if name == "napcat":
            data["napcat"]["enabled"] = (not data["napcat"]["enabled"]) if enabled is None else enabled
        elif name == "tui":
            data["tui"]["enabled"] = (not data["tui"]["enabled"]) if enabled is None else enabled
        else:
            raise ValueError(f"Unknown platform: {name}")
        self.config = AppConfig.model_validate(data)
        await self.db.put_settings(runtime_payload(self.config))
        self.rebuild()
        return self.config

    async def close(self) -> None:
        await self.napcat.aclose()
        await self.db.close()
        await self.redis.close()
        if self._checkpointer_pool is not None:
            await self._checkpointer_pool.close()
