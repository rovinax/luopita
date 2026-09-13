from __future__ import annotations

from fastapi import HTTPException, Request


def extract_bearer_token(request: Request) -> str:
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    header = (request.headers.get("x-admin-token") or "").strip()
    if header:
        return header
    return (request.query_params.get("admin_token") or "").strip()


def require_admin(request: Request) -> None:
    """Enforce admin token when configured. Empty token keeps local/dev open."""
    runtime = request.app.state.runtime
    expected = str(getattr(runtime.config, "admin_token", "") or "").strip()
    if not expected:
        return
    provided = extract_bearer_token(request)
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="admin token required")
