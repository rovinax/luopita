from __future__ import annotations

import hmac
import json
import re
from hashlib import sha1
from typing import Any
from urllib.parse import urlparse, urlunparse

from interface.platform.napcat_api import NapcatAPIError, NapcatClient, as_id, text_segments
from msg.schema import InboundMessage, MediaRef, OutboundMessage
from utils.config import NapcatSettings
from utils.log import ChatbotLogger

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def _parse_message_field(message: Any) -> Any:
    if not isinstance(message, str):
        return message
    raw = message.strip()
    if raw.startswith("[") or raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return message
        return parsed
    return message


def _as_list(message: Any) -> list[dict[str, Any]]:
    message = _parse_message_field(message)
    if message is None:
        return []
    if isinstance(message, list):
        return [item if isinstance(item, dict) else {"type": "text", "data": {"text": str(item)}} for item in message]
    if isinstance(message, dict):
        return [message]
    return [{"type": "text", "data": {"text": str(message)}}]


def _truthy_flag(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and int(value) == 1:
        return True
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def is_sticker_image_data(data: dict[str, Any] | None) -> bool:
    payload = data if isinstance(data, dict) else {}
    if _truthy_flag(payload.get("asface")):
        return True
    sub = payload.get("sub_type")
    if sub is None:
        sub = payload.get("subType") or payload.get("subtype")
    if str(sub).strip() == "1":
        return True
    if str(payload.get("emoji_id") or "").strip() or str(payload.get("emoji_package_id") or "").strip():
        return True
    file_name = str(payload.get("file") or payload.get("filename") or payload.get("name") or "").strip().lower()
    if file_name == "marketface" or file_name.startswith("marketface"):
        return True
    summary = str(payload.get("summary") or "")
    if "动画表情" in summary or "表情包" in summary:
        return True
    return False


def is_sticker_media_ref(item: MediaRef) -> bool:
    blob = f"{item.file_id} {item.name}".strip().lower()
    return blob == "marketface" or blob.startswith("marketface") or "marketface" in blob


_GENERIC_STICKER_LABELS = {"", "动画表情", "表情包", "表情", "image", "marketface"}


def sticker_caption(data: dict[str, Any] | None) -> str:
    payload = data if isinstance(data, dict) else {}
    raw = str(payload.get("summary") or payload.get("text") or "").strip()
    raw = raw.strip("[]【】 ").strip()
    if raw.lower() in _GENERIC_STICKER_LABELS or raw in _GENERIC_STICKER_LABELS:
        return "[表情包]"
    if len(raw) > 20:
        raw = raw[:20]
    return f"[表情包:{raw}]"


def segments_to_text(message: Any) -> str:
    if isinstance(message, str):
        return _rewrite_sticker_cq(message.strip())
    parts: list[str] = []
    for item in _as_list(message):
        kind = str(item.get("type") or "")
        data = item.get("data") or {}
        if kind == "text":
            parts.append(str(data.get("text") or item.get("text") or ""))
        elif kind == "at":
            parts.append(f"[@{data.get('qq') or ''}]")
        elif kind == "image":
            if is_sticker_image_data(data if isinstance(data, dict) else {}):
                parts.append(sticker_caption(data if isinstance(data, dict) else {}))
            else:
                label = str(data.get("file") or data.get("filename") or data.get("name") or "")
                parts.append(f"[图片:{label}]" if label else "[图片]")
        elif kind == "face":
            parts.append("[表情]")
        elif kind == "mface":
            parts.append(sticker_caption(data if isinstance(data, dict) else {}))
        elif kind == "reply":
            parts.append(f"[回复:{data.get('id') or ''}]")
        elif kind == "record":
            parts.append("[语音]")
        elif kind == "video":
            parts.append("[视频]")
        elif kind == "file":
            parts.append(f"[文件:{data.get('name') or data.get('file') or ''}]")
        elif kind == "json":
            parts.append("[卡片]")
        elif kind == "poke":
            parts.append("[戳一戳]")
        elif kind == "forward":
            parts.append("[合并转发]")
    return "".join(parts).strip()


_CQ_IMAGE_RE = re.compile(r"\[CQ:image,([^\]]+)\]", re.I)
_CQ_FILE_RE = re.compile(r"\[CQ:file,([^\]]+)\]", re.I)
_CQ_MFACE_RE = re.compile(r"\[CQ:mface,[^\]]*\]", re.I)
_CQ_FACE_RE = re.compile(r"\[CQ:face,[^\]]*\]", re.I)
_CQ_IMAGE_FULL_RE = re.compile(r"\[CQ:image,[^\]]*\]", re.I)


def _rewrite_sticker_cq(text: str) -> str:
    raw = text or ""

    def _replace_image(match: re.Match[str]) -> str:
        inner = match.group(0)
        comma = inner.find(",")
        fields = _cq_fields(inner[comma + 1 :].rstrip("]")) if comma >= 0 else {}
        return sticker_caption(fields) if is_sticker_image_data(fields) else match.group(0)

    def _replace_mface(match: re.Match[str]) -> str:
        inner = match.group(0)
        comma = inner.find(",")
        fields = _cq_fields(inner[comma + 1 :].rstrip("]")) if comma >= 0 else {}
        return sticker_caption(fields)

    raw = _CQ_IMAGE_FULL_RE.sub(_replace_image, raw)
    raw = _CQ_MFACE_RE.sub(_replace_mface, raw)
    raw = _CQ_FACE_RE.sub("[表情]", raw)
    return raw


def _unique_media(items: list[MediaRef]) -> list[MediaRef]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[MediaRef] = []
    for item in items:
        key = (item.kind, item.file_id, item.url, item.name)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _cq_fields(blob: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in str(blob or "").split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def extract_media(message: Any) -> list[MediaRef]:
    items: list[MediaRef] = []
    message = _parse_message_field(message)
    if isinstance(message, str):
        for blob in _CQ_IMAGE_RE.findall(message):
            fields = _cq_fields(blob)
            if is_sticker_image_data(fields):
                continue
            items.append(
                MediaRef(
                    kind="image",
                    file_id=fields.get("file") or fields.get("file_id") or "",
                    url=sanitize_media_url(fields.get("url") or ""),
                    name=fields.get("file") or fields.get("filename") or "",
                )
            )
        for blob in _CQ_FILE_RE.findall(message):
            fields = _cq_fields(blob)
            items.append(
                MediaRef(
                    kind="file",
                    file_id=fields.get("file_id") or fields.get("file") or "",
                    url=sanitize_media_url(fields.get("url") or ""),
                    name=fields.get("name") or fields.get("file") or "",
                )
            )
        return _unique_media(items)
    for item in _as_list(message):
        kind = str(item.get("type") or "")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if kind == "image":
            if is_sticker_image_data(data):
                continue
            name = str(data.get("filename") or data.get("name") or data.get("file") or "")
            items.append(
                MediaRef(
                    kind="image",
                    file_id=str(data.get("file") or data.get("file_id") or data.get("file_unique") or ""),
                    url=sanitize_media_url(str(data.get("url") or "")),
                    name=name,
                    mime=str(data.get("mime") or ""),
                )
            )
        elif kind in {"mface", "face"}:
            continue
        elif kind == "file":
            name = str(data.get("name") or data.get("file") or "")
            items.append(
                MediaRef(
                    kind="file",
                    file_id=str(data.get("file_id") or data.get("file") or data.get("id") or ""),
                    url=sanitize_media_url(str(data.get("url") or "")),
                    name=name,
                    mime=str(data.get("mime") or ""),
                )
            )
    return _unique_media(items)


def sanitize_media_url(url: str) -> str:
    from html import unescape

    raw = unescape((url or "").strip())
    if "&amp;" in raw:
        raw = unescape(raw)
    return raw.replace("\\u0026", "&")


def is_http_url(url: str) -> bool:
    parsed = urlparse(sanitize_media_url(url))
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def rewrite_loopback_url(url: str, bot_url: str) -> str:
    raw = sanitize_media_url(url)
    if not is_http_url(raw):
        return raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        return raw
    base = urlparse(bot_url or "")
    if not base.hostname:
        return raw
    port = parsed.port if parsed.port is not None else base.port
    hostname = base.hostname
    netloc = f"{hostname}:{port}" if port else hostname
    return urlunparse(parsed._replace(scheme=base.scheme or "http", netloc=netloc))


def is_direct_downloadable(url: str, bot_url: str) -> bool:
    target = rewrite_loopback_url(url, bot_url)
    if not is_http_url(target):
        return False
    host = (urlparse(target).hostname or "").lower()
    return host not in _LOOPBACK_HOSTS


def url_host(url: str) -> str:
    return (urlparse(sanitize_media_url(url)).hostname or "").lower()


def is_napcat_hosted_url(url: str, bot_url: str) -> bool:
    target = rewrite_loopback_url(url, bot_url)
    if not is_http_url(target):
        return False
    host = (urlparse(target).hostname or "").lower()
    bot_host = (urlparse(bot_url or "").hostname or "").lower()
    return bool(bot_host) and host == bot_host


def _decode_base64_field(raw: Any) -> bytes:
    import base64

    if raw is True or raw is False or raw is None:
        return b""
    text = str(raw).strip()
    if not text or text.lower() in {"true", "false"}:
        return b""
    if text.lower().startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text)
    except Exception:
        return b""


_CQ_AT_RE = re.compile(r"\[CQ:at,([^\]]+)\]", re.I)
_CQ_REPLY_RE = re.compile(r"\[CQ:reply,([^\]]+)\]", re.I)
_BRACKET_AT_RE = re.compile(r"\[@([^\]]+)\]")
_REPLY_MARK_RE = re.compile(r"\[回复:([^\]]*)\]")


def _uniq_ids(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in ids:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _at_ids_from_text(text: str) -> list[str]:
    ids: list[str] = []
    raw = text or ""
    for blob in _CQ_AT_RE.findall(raw):
        qq = ""
        for part in str(blob).split(","):
            if part.strip().lower().startswith("qq="):
                qq = part.split("=", 1)[1].strip()
                break
        if not qq:
            qq = str(blob).strip()
        if qq:
            ids.append(qq)
    ids.extend(_BRACKET_AT_RE.findall(raw))
    return ids


def _reply_ids_from_text(text: str) -> list[str]:
    ids: list[str] = []
    raw = text or ""
    for blob in _CQ_REPLY_RE.findall(raw):
        fields = _cq_fields(blob)
        rid = fields.get("id") or fields.get("message_id") or ""
        if rid:
            ids.append(rid)
    ids.extend(item.strip() for item in _REPLY_MARK_RE.findall(raw) if item.strip())
    return ids


def extract_reply_ids(message: Any) -> list[str]:
    ids: list[str] = []
    message = _parse_message_field(message)
    if isinstance(message, str):
        return _uniq_ids(_reply_ids_from_text(message))
    for item in _as_list(message):
        kind = str(item.get("type") or "")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if kind == "reply":
            rid = str(data.get("id") or data.get("message_id") or item.get("id") or "")
            if rid:
                ids.append(rid)
        elif kind == "text":
            ids.extend(_reply_ids_from_text(str(data.get("text") or item.get("text") or "")))
    return _uniq_ids(ids)


def strip_reply_marks(text: str) -> str:
    cleaned = _CQ_REPLY_RE.sub("", text or "")
    cleaned = _REPLY_MARK_RE.sub("", cleaned)
    return " ".join(cleaned.split()).strip()


def summarize_fetched_msg(data: dict[str, Any] | None) -> str:
    if not isinstance(data, dict):
        return ""
    sender = data.get("sender") if isinstance(data.get("sender"), dict) else {}
    name = str(sender.get("card") or sender.get("nickname") or data.get("user_id") or "某人").strip() or "某人"
    text = segments_to_text(data.get("message")) or str(data.get("raw_message") or "").strip()
    if not text:
        text = "[空消息]"
    if len(text) > 400:
        text = text[:400] + "…"
    return f"{name}: {text}"


def media_from_payload(raw: Any) -> list[MediaRef]:
    items: list[MediaRef] = []
    if not isinstance(raw, list):
        return []
    for item in raw:
        if isinstance(item, MediaRef):
            items.append(item)
            continue
        if not isinstance(item, dict):
            continue
        try:
            items.append(MediaRef.model_validate(item))
        except Exception:
            continue
    return _unique_media(items)


_IMAGE_MARK_RE = re.compile(r"\[图片:([^\]]+)\]")
_FILE_MARK_RE = re.compile(r"\[文件:([^\]]+)\]")


def media_from_event_text(content: str) -> list[MediaRef]:
    items: list[MediaRef] = []
    for label in _IMAGE_MARK_RE.findall(content or ""):
        name = str(label or "").strip()
        if name and "marketface" not in name.lower():
            items.append(MediaRef(kind="image", file_id=name, name=name))
    for label in _FILE_MARK_RE.findall(content or ""):
        name = str(label or "").strip()
        if name:
            items.append(MediaRef(kind="file", file_id=name, name=name))
    return _unique_media(items)


def dump_media_refs(items: list[MediaRef] | None) -> list[dict[str, Any]]:
    return [item.model_dump() for item in items or []]


def media_from_fetched_msg(data: dict[str, Any] | None) -> list[MediaRef]:
    if not isinstance(data, dict):
        return []
    return _unique_media(extract_media(data.get("message")) + extract_media(data.get("raw_message")))


def take_media(*groups: list[MediaRef], limit: int = 3) -> list[MediaRef]:
    cap = max(1, int(limit or 3))
    out: list[MediaRef] = []
    seen: set[tuple[str, str, str, str]] = set()
    for group in groups:
        for item in group or []:
            key = (item.kind, item.file_id, item.url, item.name)
            if key in seen:
                continue
            if is_sticker_media_ref(item):
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= cap:
                return out
    return out


def extract_at_user_ids(message: Any) -> list[str]:
    ids: list[str] = []
    message = _parse_message_field(message)
    if isinstance(message, str):
        return _uniq_ids(_at_ids_from_text(message))
    for item in _as_list(message):
        kind = str(item.get("type") or "")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if kind == "at":
            qq = str(data.get("qq") or data.get("user_id") or item.get("qq") or "")
            if qq:
                ids.append(qq)
        elif kind == "text":
            ids.extend(_at_ids_from_text(str(data.get("text") or item.get("text") or "")))
    return _uniq_ids(ids)


def parse_onebot_event(payload: dict[str, Any]) -> InboundMessage | None:
    post_type = str(payload.get("post_type") or "")
    if post_type in {"meta_event", "notice", "request"}:
        return None
    if post_type and post_type not in {"message", "message_sent"}:
        return None
    message_type = str(payload.get("message_type") or "")
    if message_type not in {"private", "group"}:
        return None
    raw_message = _parse_message_field(payload.get("message"))
    raw_fallback = payload.get("raw_message")
    text = segments_to_text(raw_message) or segments_to_text(raw_fallback)
    user_id = str(payload.get("user_id") or "")
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    if message_type == "group":
        chat_id = str(payload.get("group_id") or "")
        channel = "group"
    else:
        chat_id = user_id
        channel = "private"
    if not chat_id:
        return None
    at_ids = extract_at_user_ids(raw_message)
    at_ids.extend(extract_at_user_ids(raw_fallback))
    at_ids.extend(extract_at_user_ids(text))
    reply_ids = extract_reply_ids(raw_message)
    reply_ids.extend(extract_reply_ids(raw_fallback))
    reply_ids.extend(extract_reply_ids(text))
    media = _unique_media(extract_media(raw_message) + extract_media(raw_fallback))
    if not text and media:
        first = media[0]
        text = f"[图片:{first.name}]" if first.kind == "image" else f"[文件:{first.name or first.file_id}]"
    if not text:
        return None
    return InboundMessage(
        platform="napcat",
        channel_type=channel,
        chat_id=chat_id,
        user_id=user_id or chat_id,
        text=text,
        raw=payload,
        message_id=str(payload.get("message_id") or "") or None,
        sender_name=str(sender.get("card") or sender.get("nickname") or "") or None,
        at_user_ids=_uniq_ids(at_ids),
        reply_to_ids=_uniq_ids(reply_ids),
        self_id=str(payload.get("self_id") or ""),
        media=media,
    )


class NapcatAdapter:
    name = "napcat"

    def __init__(self, settings: NapcatSettings, logger: ChatbotLogger | None = None) -> None:
        self.settings = settings
        self.logger = logger or ChatbotLogger()
        self.api = NapcatClient(
            base_url=settings.bot_url,
            access_token=settings.access_token,
            logger=self.logger,
        )

    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    def can_fetch(self) -> bool:
        return bool((self.settings.bot_url or "").strip())

    async def fetch_message(self, message_id: str) -> dict[str, Any] | None:
        if not self.can_fetch():
            return None
        rid = str(message_id or "").strip()
        if not rid:
            return None
        try:
            data = await self.api.call("get_msg", {"message_id": as_id(rid)})
        except Exception as exc:
            self.logger.warning(f"napcat get_msg skip id={rid}: {exc}")
            return None
        if isinstance(data, dict):
            return data
        return None

    def update(self, settings: NapcatSettings) -> None:
        self.settings = settings
        self.api.base_url = settings.bot_url.rstrip("/")
        self.api.access_token = settings.access_token or ""

    def authorized(
        self,
        authorization: str | None,
        access_token: str | None,
        *,
        signature: str | None = None,
        body: bytes | None = None,
    ) -> bool:
        expected = (self.settings.access_token or "").strip()
        if not expected:
            return True
        if access_token and access_token == expected:
            return True
        if authorization:
            value = authorization.strip()
            if value == expected:
                return True
            if value.lower().startswith("bearer ") and value.split(" ", 1)[1].strip() == expected:
                return True
        # Current NapCat HTTP client signs the body instead of sending Bearer.
        if signature and body is not None:
            sig = signature.strip()
            if sig.lower().startswith("sha1="):
                sig = sig[5:]
            digest = hmac.new(expected.encode("utf-8"), body, sha1).hexdigest()
            if len(digest) == len(sig) and hmac.compare_digest(digest, sig):
                return True
        return False

    async def send(self, message: OutboundMessage) -> str | None:
        if not self.enabled():
            return None
        payload = message.segments or text_segments(message.text)
        try:
            if message.channel_type == "group":
                data = await self.api.send_msg(
                    message=payload, message_type="group", group_id=message.chat_id
                )
            else:
                data = await self.api.send_msg(
                    message=payload, message_type="private", user_id=message.chat_id
                )
        except NapcatAPIError as exc:
            self.logger.error(f"napcat send failed: {exc}")
            return None
        return _extract_outbound_message_id(data)

    def _media_keys(self, item: MediaRef) -> list[str]:
        keys: list[str] = []
        for value in (item.file_id, item.name):
            text = str(value or "").strip()
            if text and text not in keys:
                keys.append(text)
        return keys

    async def fetch_media(self, item: MediaRef, *, group_id: str = ""):
        from core.media import FetchedMedia

        if not self.can_fetch():
            self.logger.warning("media fetch skip: bot_url empty")
            return None
        name = (item.name or item.file_id or "file").strip() or "file"
        data = b""
        mime = item.mime or ""
        via = ""
        host = url_host(item.url) or "-"
        self.logger.info(
            f"media fetch start kind={item.kind} file={item.file_id or item.name or '-'} url_host={host}"
        )
        urls = [item.url] if item.url else []
        keys = self._media_keys(item)
        calls: list[tuple[str, dict[str, Any]]] = []
        if item.kind == "image":
            for key in keys:
                calls.append(("get_image", {"file": key}))
            for key in keys:
                calls.append(("get_file", {"file": key}))
                calls.append(("get_file", {"file_id": key}))
        elif keys:
            calls.append(("get_file", {"file_id": keys[0]}))
            calls.append(("get_file", {"file": keys[0]}))
            if group_id:
                calls.append(("get_group_file_url", {"group_id": group_id, "file_id": keys[0]}))
        for action, params in calls:
            blob, mime, name, extra_url = await self._from_api(action, params, name, mime)
            if extra_url:
                urls.append(extra_url)
            if blob:
                data = blob
                via = action
                break
        if not data:
            for candidate in urls:
                blob, source = await self._bytes_from_url(candidate)
                if blob:
                    data = blob
                    via = source
                    break
        if not data:
            self.logger.warning(
                f"media fetch empty kind={item.kind} file={item.file_id or item.name or '-'} url_host={host}"
            )
            return None
        self.logger.info(f"media fetch ok kind={item.kind} bytes={len(data)} via={via or '-'} name={name}")
        return FetchedMedia(kind=item.kind, name=name, data=data, mime=mime)

    async def _from_api(self, action: str, params: dict, name: str, mime: str) -> tuple[bytes, str, str, str]:
        try:
            payload = await self.api.call(action, params)
        except Exception as exc:
            self.logger.warning(f"napcat {action} skip: {exc}")
            return b"", mime, name, ""
        if not isinstance(payload, dict):
            return b"", mime, name, ""
        out_name = str(payload.get("file_name") or payload.get("filename") or name)
        file_field = str(payload.get("file") or "")
        if file_field and not is_http_url(file_field) and ("/" in file_field or "\\" in file_field):
            from pathlib import Path

            out_name = Path(file_field).name or out_name
        out_mime = str(payload.get("mime") or mime)
        url = sanitize_media_url(str(payload.get("url") or ""))
        if not is_http_url(url) and is_http_url(file_field):
            url = sanitize_media_url(file_field)
        data = _decode_base64_field(payload.get("base64") or payload.get("base64_data") or "")
        return data, out_mime, out_name, url

    async def _bytes_from_url(self, url: str) -> tuple[bytes, str]:
        raw = sanitize_media_url(url)
        if not raw:
            return b"", ""
        if is_napcat_hosted_url(raw, self.settings.bot_url):
            return await self._download_url(raw), "http"
        if is_http_url(raw) or is_http_url(rewrite_loopback_url(raw, self.settings.bot_url)):
            return await self._download_file_api(raw), "download_file"
        return b"", ""

    async def _download_file_api(self, url: str) -> bytes:
        target = sanitize_media_url(url)
        try:
            payload = await self.api.call("download_file", {"url": target})
        except Exception as exc:
            self.logger.warning(f"napcat download_file skip: {exc}")
            return b""
        if not isinstance(payload, dict):
            return b""
        data = _decode_base64_field(payload.get("base64") or payload.get("base64_data") or "")
        if data:
            return data
        local = str(payload.get("file") or "")
        if local and not is_http_url(local):
            data, _, _, _ = await self._from_api("get_file", {"file": local}, "file", "")
            if data:
                return data
            data, _, _, _ = await self._from_api("get_image", {"file": local}, "file", "")
            if data:
                return data
        nested = sanitize_media_url(str(payload.get("url") or local))
        if nested and is_napcat_hosted_url(nested, self.settings.bot_url):
            return await self._download_url(nested)
        return b""

    async def _download_url(self, url: str) -> bytes:
        target = rewrite_loopback_url(url, self.settings.bot_url)
        if not is_napcat_hosted_url(target, self.settings.bot_url):
            return b""
        try:
            return await self.api.get_bytes(target)
        except Exception as exc:
            self.logger.warning(f"napcat media download skip: {exc}")
            return b""

    async def aclose(self) -> None:
        await self.api.aclose()


def _extract_outbound_message_id(data: Any) -> str | None:
    if data is None:
        return None
    if isinstance(data, dict):
        for key in ("message_id", "messageId", "id"):
            value = data.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return None
    if isinstance(data, (int, float)):
        return str(int(data))
    text = str(data).strip()
    return text or None
