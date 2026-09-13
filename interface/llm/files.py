from __future__ import annotations

from typing import Protocol

import httpx


class FilesClient(Protocol):
    async def upload_user_file(self, filename: str, data: bytes, mime: str = "") -> str: ...


class MockFilesClient:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, int, str]] = []

    async def upload_user_file(self, filename: str, data: bytes, mime: str = "") -> str:
        name = (filename or "image.bin").strip() or "image.bin"
        self.uploads.append((name, len(data or b""), mime or ""))
        return f"file-api-mock-{len(self.uploads)}"


class HttpFilesClient:
    """OpenAI-compatible Files API (DeepSeek: purpose=user_data, images only)."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0) -> None:
        self.base_url = (base_url or "https://api.deepseek.com").rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout

    async def upload_user_file(self, filename: str, data: bytes, mime: str = "") -> str:
        if not data:
            raise ValueError("empty file")
        name = (filename or "image.bin").strip() or "image.bin"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        files = {"file": (name, data, mime or "application/octet-stream")}
        form = {
            "purpose": "user_data",
            "expires_after[anchor]": "created_at",
            "expires_after[seconds]": "86400",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/files", headers=headers, files=files, data=form)
        response.raise_for_status()
        body = response.json()
        file_id = str((body or {}).get("id") or "")
        if not file_id:
            raise ValueError("files api returned no id")
        return file_id
