from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.auth import require_admin
from app.runtime import Runtime
from msg.schema import ChatRequest, ConfigUpdate, IdentityUpdate, InboundMessage, PersonaUpdate
from interface.platform.napcat import parse_onebot_event
from interface.platform.napcat_api import (
    ACTION_CATALOG,
    DANGEROUS_ACTIONS,
    NapcatAPIError,
    is_dangerous,
    normalize_action,
)

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = await Runtime.start()
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.close()


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


class ToggleBody(BaseModel):
    enabled: bool | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Luopita", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health(request: Request):
        runtime = get_runtime(request)
        db_ok = False
        redis_ok = False
        try:
            db_ok = await runtime.db.ping()
        except Exception:
            db_ok = False
        try:
            redis_ok = await runtime.redis.ping()
        except Exception:
            redis_ok = False
        return {
            "ok": True,
            "service": "luopita",
            "provider": runtime.config.llm.provider,
            "database": "memory" if runtime.config.is_memory_db() else "postgres",
            "db_ok": db_ok,
            "redis": "memory" if runtime.config.is_memory_redis() else "redis",
            "redis_ok": redis_ok,
        }

    @app.get("/api/config")
    async def get_config(request: Request):
        require_admin(request)
        runtime = get_runtime(request)
        return {
            "ok": True,
            "config": runtime.config.public_dict(),
            "platforms": runtime.adapters.snapshot(),
        }

    @app.put("/api/config")
    async def put_config(payload: ConfigUpdate, request: Request):
        require_admin(request)
        runtime = get_runtime(request)
        cfg = await runtime.apply_update(payload)
        return {"ok": True, "config": cfg.public_dict()}

    @app.get("/api/persona")
    async def get_persona(request: Request):
        require_admin(request)
        runtime = get_runtime(request)
        return {"ok": True, "persona": runtime.config.persona.model_dump()}

    @app.put("/api/persona")
    async def put_persona(payload: PersonaUpdate, request: Request):
        require_admin(request)
        runtime = get_runtime(request)
        cfg = await runtime.apply_update(ConfigUpdate(persona=payload.model_dump(exclude_none=True)))
        return {"ok": True, "persona": cfg.persona.model_dump()}

    @app.get("/api/identity")
    async def get_identity(request: Request):
        require_admin(request)
        runtime = get_runtime(request)
        return {"ok": True, "identity": runtime.identity.settings.model_dump()}

    @app.put("/api/identity")
    async def put_identity(payload: IdentityUpdate, request: Request):
        require_admin(request)
        runtime = get_runtime(request)
        saved = runtime.apply_identity(payload)
        return {"ok": True, "identity": saved.model_dump()}

    @app.get("/api/platforms")
    async def get_platforms(request: Request):
        require_admin(request)
        runtime = get_runtime(request)
        return {"ok": True, "platforms": runtime.adapters.snapshot()}

    @app.post("/api/platforms/{name}/toggle")
    async def toggle_platform(name: str, request: Request, body: ToggleBody | None = None):
        require_admin(request)
        runtime = get_runtime(request)
        try:
            enabled = body.enabled if body else None
            await runtime.toggle_platform(name, enabled)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "platforms": runtime.adapters.snapshot()}

    async def _chat(req: ChatRequest, request: Request):
        require_admin(request)
        text = (req.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        runtime = get_runtime(request)
        inbound = InboundMessage(
            platform="admin",
            channel_type="console",
            chat_id=req.session_id or req.user_id or "admin",
            user_id=req.user_id or "admin",
            text=text,
            session_key=req.session_id or "",
        )
        try:
            resp = await runtime.orchestrator.handle(inbound, deliver=False)
            return {
                "ok": True,
                "session_id": resp.session_id,
                "reply": resp.reply,
                "role": resp.role,
                "ignored": resp.ignored,
            }
        except Exception as exc:
            runtime.logger.error(f"chat failed: {exc}")
            raise HTTPException(status_code=502, detail="llm_call_failed") from exc

    @app.post("/api/chat")
    async def api_chat(req: ChatRequest, request: Request):
        return await _chat(req, request)

    @app.post("/chat")
    async def chat_alias(req: ChatRequest, request: Request):
        return await _chat(req, request)

    @app.get("/api/sessions")
    async def list_sessions(request: Request, limit: int = Query(default=50, ge=1, le=200)):
        require_admin(request)
        runtime = get_runtime(request)
        rows = await runtime.db.list_sessions(limit=limit)
        sessions = []
        for row in rows:
            item = dict(row)
            item["role"] = runtime.identity.resolve_role(str(item.get("platform") or ""), str(item.get("user_id") or ""))
            sessions.append(item)
        return {"ok": True, "sessions": sessions}

    @app.get("/api/sessions/{session_id}/messages")
    async def session_messages(session_id: str, request: Request, limit: int = Query(default=100, ge=1, le=500)):
        require_admin(request)
        runtime = get_runtime(request)
        rows = await runtime.db.load_messages(session_id, limit=limit)
        for row in rows:
            if hasattr(row.get("created_at"), "isoformat"):
                row["created_at"] = row["created_at"].isoformat()
        return {"ok": True, "session_id": session_id, "messages": rows}

    @app.get("/api/napcat/actions")
    async def napcat_actions(request: Request):
        require_admin(request)
        items = [
            {"action": name, **meta, "dangerous": name in DANGEROUS_ACTIONS}
            for name, meta in ACTION_CATALOG.items()
        ]
        return {"ok": True, "count": len(items), "actions": items}

    @app.post("/api/napcat/{action}")
    async def napcat_call(action: str, request: Request):
        require_admin(request)
        runtime = get_runtime(request)
        try:
            name = normalize_action(action)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if is_dangerous(name):
            raise HTTPException(status_code=403, detail=f"action blocked: {name}")
        if not runtime.napcat.enabled():
            raise HTTPException(status_code=409, detail="napcat adapter is disabled")
        try:
            raw = await request.json()
        except Exception:
            raw = {}
        if raw is None:
            params: dict = {}
        elif isinstance(raw, dict):
            nested = raw.get("params")
            params = nested if isinstance(nested, dict) and list(raw.keys()) == ["params"] else raw
        else:
            raise HTTPException(status_code=400, detail="JSON object required")
        try:
            data = await runtime.napcat.api.call(name, params)
        except NapcatAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "action": name, "data": data}

    @app.post("/webhooks/napcat")
    async def napcat_webhook(request: Request, access_token: str | None = None):
        runtime = get_runtime(request)
        raw = await request.body()
        if not runtime.napcat.authorized(
            request.headers.get("authorization"),
            access_token,
            signature=request.headers.get("x-signature"),
            body=raw,
        ):
            raise HTTPException(status_code=401, detail="unauthorized")
        try:
            payload = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="invalid json") from exc
        inbound = parse_onebot_event(payload if isinstance(payload, dict) else {})
        if inbound is None:
            return {"ok": True, "ignored": True}
        try:
            resp = await runtime.orchestrator.handle(inbound, deliver=True)
            return {
                "ok": True,
                "ignored": resp.ignored,
                "session_id": resp.session_id,
                "role": resp.role,
            }
        except Exception as exc:
            runtime.logger.error(f"napcat webhook failed: {exc}")
            raise HTTPException(status_code=502, detail="llm_call_failed") from exc

    @app.websocket("/ws/tui")
    async def tui_socket(
        ws: WebSocket,
        chat_id: str = "local",
        user_id: str = "tui",
        admin_token: str = "",
    ):
        runtime: Runtime = ws.app.state.runtime
        expected = str(runtime.config.admin_token or "").strip()
        if expected and (admin_token or "").strip() != expected:
            await ws.close(code=1008)
            return
        if not runtime.config.tui.enabled:
            await ws.close(code=1008)
            return
        await ws.accept()
        runtime.tui.register(chat_id, ws)
        try:
            while True:
                data = await ws.receive_json()
                text = str(data.get("text") or "").strip()
                if not text:
                    continue
                inbound = InboundMessage(
                    platform="tui",
                    channel_type="console",
                    chat_id=str(data.get("chat_id") or chat_id),
                    user_id=str(data.get("user_id") or user_id),
                    text=text,
                    session_key=str(data.get("session_id") or ""),
                )
                try:
                    await runtime.orchestrator.handle(inbound, deliver=True)
                except Exception as exc:
                    runtime.logger.error(f"tui chat failed: {exc}")
                    await ws.send_json({"type": "error", "error": "llm_call_failed"})
        except WebSocketDisconnect:
            runtime.tui.unregister(chat_id, ws)
        except Exception:
            runtime.tui.unregister(chat_id, ws)

    if DIST.is_dir():
        assets = DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        async def spa_index():
            return FileResponse(DIST / "index.html")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            blocked = ("api/", "webhooks/", "ws/", "health", "docs", "openapi.json", "redoc", "chat")
            if full_path.startswith(blocked) or full_path in {"health", "chat", "docs", "openapi.json", "redoc"}:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            index = DIST / "index.html"
            candidate = DIST / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)
    else:

        @app.get("/")
        async def root_ok():
            return {"ok": True, "service": "luopita", "ui": False}

    return app


app = create_app()
