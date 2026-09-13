from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from interface.llm.files import FilesClient

MAX_MEDIA_ITEMS = 3
MAX_INLINE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
TEXT_PREVIEW_CHARS = 4000

MINI_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@dataclass
class FetchedMedia:
    kind: str
    name: str
    data: bytes
    mime: str = ""


def sniff_image_magic(data: bytes) -> str:
    raw = data or b""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return "image/gif"
    if raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
        return "image/webp"
    return ""


def sniff_image_mime(data: bytes, name: str = "") -> str:
    magic = sniff_image_magic(data)
    if magic:
        return magic
    lowered = (name or "").lower()
    if lowered.endswith((".png",)):
        return "image/png"
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith((".gif",)):
        return "image/gif"
    if lowered.endswith((".webp",)):
        return "image/webp"
    return ""


def is_image(item: FetchedMedia) -> bool:
    return bool(sniff_image_magic(item.data or b""))


def decode_text_file(data: bytes) -> str | None:
    if not data:
        return None
    if b"\x00" in data[:4096]:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("gb18030")
        except UnicodeDecodeError:
            return None
    stripped = text.strip()
    if not stripped:
        return None
    if len(stripped) > TEXT_PREVIEW_CHARS:
        return stripped[:TEXT_PREVIEW_CHARS] + "\n…(截断)"
    return stripped


def file_content_block(file_id: str) -> dict[str, Any]:
    return {"type": "file", "file_id": file_id}


def normalize_vision_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """DeepSeek wants top-level file_id/file_data, not OpenAI nested file.file_id."""
    typ = str(block.get("type") or "")
    if typ in {"image_url", "image", "input_image"}:
        url = ""
        image = block.get("image_url")
        if isinstance(image, dict):
            url = str(image.get("url") or "")
        elif isinstance(image, str):
            url = image
        url = url or str(block.get("url") or "")
        if url.strip():
            return {"type": "image_url", "image_url": {"url": url.strip()}}
        return None
    if typ not in {"file", "input_file"}:
        return None
    nested = block.get("file") if isinstance(block.get("file"), dict) else {}
    file_id = str(block.get("file_id") or nested.get("file_id") or "").strip()
    file_data = str(block.get("file_data") or nested.get("file_data") or "").strip()
    filename = str(block.get("filename") or nested.get("filename") or "").strip()
    if file_id:
        return {"type": "file", "file_id": file_id}
    if file_data:
        out: dict[str, Any] = {"type": "file", "file_data": file_data}
        if filename:
            out["filename"] = filename
        return out
    return None


def flatten_content_for_deepseek(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    out: list[Any] = []
    for block in content:
        if not isinstance(block, dict):
            out.append(block)
            continue
        if block.get("type") in {"file", "input_file", "image_url", "image", "input_image"}:
            normalized = normalize_vision_block(block)
            if normalized:
                out.append(normalized)
            continue
        out.append(block)
    return out


def normalize_deepseek_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if not isinstance(content, list):
        return message
    flattened = flatten_content_for_deepseek(content)
    if flattened == content:
        return message
    return {**message, "content": flattened}


def rewrite_content_blocks(content: Any, *, keep_vision: bool) -> Any:
    if not isinstance(content, list):
        return content
    texts: list[str] = []
    vision: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, str):
            if block:
                texts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        typ = str(block.get("type") or "")
        if typ == "text":
            text = str(block.get("text") or "")
            if text:
                texts.append(text)
            continue
        if typ in {"file", "input_file", "image_url", "image", "input_image"}:
            if keep_vision:
                normalized = normalize_vision_block(block)
                if normalized:
                    vision.append(normalized)
            elif "[图片]" not in "".join(texts):
                texts.append("[图片]")
    body = "\n".join(part for part in texts if part).strip()
    if not vision:
        return body
    blocks: list[dict[str, Any]] = []
    if body:
        blocks.append({"type": "text", "text": body})
    blocks.extend(vision)
    return blocks


def prepare_messages_for_llm(messages: list[Any]) -> list[Any]:
    last_human = -1
    for index, message in enumerate(messages or []):
        if getattr(message, "type", "") == "human":
            last_human = index
    out: list[Any] = []
    for index, message in enumerate(messages or []):
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            out.append(message)
            continue
        rewritten = rewrite_content_blocks(content, keep_vision=index == last_human)
        if rewritten == content:
            out.append(message)
            continue
        copier = getattr(message, "model_copy", None)
        if callable(copier):
            out.append(copier(update={"content": rewritten}))
        else:
            out.append(message)
    return out


def message_has_vision_blocks(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"image_url", "file", "image", "input_image", "input_file"}:
            return True
    return False


def messages_have_vision(messages: list[Any]) -> bool:
    for message in reversed(messages or []):
        if getattr(message, "type", "") == "human":
            return message_has_vision_blocks(getattr(message, "content", None))
    return False


async def build_user_content(
    text: str,
    fetched: list[FetchedMedia],
    files_client: FilesClient | None,
) -> tuple[str | list[dict[str, Any]], str]:
    notes: list[str] = []
    blocks: list[dict[str, Any]] = []
    body = (text or "").strip()
    if body:
        blocks.append({"type": "text", "text": body})
    images = 0
    for item in fetched[:MAX_MEDIA_ITEMS]:
        data = item.data or b""
        if len(data) > MAX_FILE_BYTES:
            notes.append(f"{item.name or '文件'}太大了，我这边下不下来。")
            continue
        if is_image(item):
            if images >= MAX_MEDIA_ITEMS:
                continue
            mime = sniff_image_mime(data, item.name) or item.mime or "image/jpeg"
            name = item.name or f"image.{mime.split('/', 1)[-1]}"
            uploaded = ""
            if files_client is not None:
                try:
                    uploaded = await files_client.upload_user_file(name, data, mime)
                except Exception:
                    uploaded = ""
            if uploaded:
                blocks.append(file_content_block(uploaded))
                images += 1
                continue
            if len(data) <= MAX_INLINE_BYTES:
                b64 = base64.b64encode(data).decode("ascii")
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    }
                )
                images += 1
            else:
                notes.append(f"{name}没传上去，发一张小一点的图吧。")
            continue
        preview = decode_text_file(data)
        if preview is not None:
            notes.append(f"文件 {item.name or '未命名'} 内容：\n{preview}")
        else:
            notes.append(f"{item.name or '这个文件'}我打不开，发图或贴文字吧。")
    if notes:
        extra = "\n".join(notes)
        blocks.append({"type": "text", "text": extra})
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return str(blocks[0].get("text") or body), "\n".join(notes)
    if not blocks:
        return body or "(empty)", "\n".join(notes)
    return blocks, "\n".join(notes)
