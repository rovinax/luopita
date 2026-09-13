from __future__ import annotations

import json
import re
from typing import Any

import httpx

from utils.log import ChatbotLogger

ACTION_RE = re.compile(r"^[A-Za-z0-9_.]+$")

# NapCat 4.18.19 / OneBot 11 action catalog (https://napneko.github.io/onebot/api)
ACTION_CATALOG: dict[str, dict[str, str]] = {
    # account
    "get_login_info": {"category": "account", "summary": "获取登录信息"},
    "get_status": {"category": "account", "summary": "获取在线状态"},
    "get_version_info": {"category": "account", "summary": "获取版本信息"},
    "set_qq_profile": {"category": "account", "summary": "设置 QQ 资料"},
    "set_qq_avatar": {"category": "account", "summary": "设置 QQ 头像"},
    "set_self_longnick": {"category": "account", "summary": "设置个性签名"},
    "set_online_status": {"category": "account", "summary": "设置在线状态"},
    "set_diy_online_status": {"category": "account", "summary": "设置自定义在线状态"},
    "set_input_status": {"category": "account", "summary": "设置输入状态"},
    "clean_cache": {"category": "account", "summary": "清除缓存"},
    "bot_exit": {"category": "account", "summary": "退出机器人"},
    "set_restart": {"category": "account", "summary": "重启"},
    "get_clientkey": {"category": "account", "summary": "获取客户端密钥"},
    # friend / user
    "get_friend_list": {"category": "friend", "summary": "获取好友列表"},
    "get_friends_with_category": {"category": "friend", "summary": "获取分类好友列表"},
    "get_stranger_info": {"category": "friend", "summary": "获取陌生人信息"},
    "get_unidirectional_friend_list": {"category": "friend", "summary": "获取单向好友列表"},
    "send_private_msg": {"category": "friend", "summary": "发送私聊消息"},
    "send_like": {"category": "friend", "summary": "好友点赞"},
    "friend_poke": {"category": "friend", "summary": "好友戳一戳"},
    "set_friend_add_request": {"category": "friend", "summary": "处理好友请求"},
    "set_friend_remark": {"category": "friend", "summary": "设置好友备注"},
    "delete_friend": {"category": "friend", "summary": "删除好友"},
    "get_friend_msg_history": {"category": "friend", "summary": "获取私聊历史"},
    "mark_private_msg_as_read": {"category": "friend", "summary": "标记私聊已读"},
    "forward_friend_single_msg": {"category": "friend", "summary": "转发私聊消息"},
    "nc_get_user_status": {"category": "friend", "summary": "获取用户状态"},
    "get_profile_like": {"category": "friend", "summary": "获取资料点赞信息"},
    # group
    "get_group_list": {"category": "group", "summary": "获取群列表"},
    "get_group_info": {"category": "group", "summary": "获取群信息"},
    "get_group_info_ex": {"category": "group", "summary": "获取群扩展信息"},
    "send_group_msg": {"category": "group", "summary": "发送群消息"},
    "get_group_member_info": {"category": "group", "summary": "获取群成员信息"},
    "get_group_member_list": {"category": "group", "summary": "获取群成员列表"},
    "get_group_honor_info": {"category": "group", "summary": "获取群荣誉"},
    "get_group_msg_history": {"category": "group", "summary": "获取群消息历史"},
    "set_group_kick": {"category": "group", "summary": "踢出群成员"},
    "set_group_ban": {"category": "group", "summary": "禁言群成员"},
    "set_group_whole_ban": {"category": "group", "summary": "全员禁言"},
    "set_group_admin": {"category": "group", "summary": "设置群管理员"},
    "set_group_card": {"category": "group", "summary": "设置群名片"},
    "set_group_name": {"category": "group", "summary": "设置群名称"},
    "set_group_leave": {"category": "group", "summary": "退出群聊"},
    "set_group_special_title": {"category": "group", "summary": "设置专属头衔"},
    "set_group_add_request": {"category": "group", "summary": "处理加群请求"},
    "set_group_portrait": {"category": "group", "summary": "设置群头像"},
    "set_group_remark": {"category": "group", "summary": "设置群备注"},
    "set_group_sign": {"category": "group", "summary": "群签到"},
    "send_group_sign": {"category": "group", "summary": "发送群签到"},
    "get_essence_msg_list": {"category": "group", "summary": "获取精华消息"},
    "set_essence_msg": {"category": "group", "summary": "设置精华消息"},
    "delete_essence_msg": {"category": "group", "summary": "删除精华消息"},
    "group_poke": {"category": "group", "summary": "群内戳一戳"},
    "mark_group_msg_as_read": {"category": "group", "summary": "标记群消息已读"},
    "forward_group_single_msg": {"category": "group", "summary": "转发群消息"},
    "_send_group_notice": {"category": "group", "summary": "发送群公告"},
    "_get_group_notice": {"category": "group", "summary": "获取群公告"},
    "_del_group_notice": {"category": "group", "summary": "删除群公告"},
    "get_group_at_all_remain": {"category": "group", "summary": "获取@全体剩余次数"},
    "get_group_system_msg": {"category": "group", "summary": "获取群系统消息"},
    "get_group_shut_list": {"category": "group", "summary": "获取群禁言列表"},
    "get_group_ignore_add_request": {"category": "group", "summary": "获取加群忽略列表"},
    "get_group_ignored_notifies": {"category": "group", "summary": "获取群通知忽略列表"},
    # message
    "send_msg": {"category": "message", "summary": "发送消息"},
    "delete_msg": {"category": "message", "summary": "撤回消息"},
    "get_msg": {"category": "message", "summary": "获取消息"},
    "get_forward_msg": {"category": "message", "summary": "获取合并转发"},
    "send_forward_msg": {"category": "message", "summary": "发送合并转发"},
    "send_group_forward_msg": {"category": "message", "summary": "发送群合并转发"},
    "send_private_forward_msg": {"category": "message", "summary": "发送私聊合并转发"},
    "get_record": {"category": "message", "summary": "获取语音"},
    "get_image": {"category": "message", "summary": "获取图片"},
    "get_file": {"category": "message", "summary": "获取文件"},
    "can_send_image": {"category": "message", "summary": "是否可发图片"},
    "can_send_record": {"category": "message", "summary": "是否可发语音"},
    "ocr_image": {"category": "message", "summary": "图片 OCR"},
    ".ocr_image": {"category": "message", "summary": "图片 OCR（增强）"},
    "mark_msg_as_read": {"category": "message", "summary": "标记消息已读"},
    "_mark_all_as_read": {"category": "message", "summary": "全部标为已读"},
    "get_recent_contact": {"category": "message", "summary": "最近联系人"},
    "send_poke": {"category": "message", "summary": "戳一戳"},
    "set_msg_emoji_like": {"category": "message", "summary": "消息表情回应"},
    # file
    "upload_group_file": {"category": "file", "summary": "上传群文件"},
    "delete_group_file": {"category": "file", "summary": "删除群文件"},
    "create_group_file_folder": {"category": "file", "summary": "创建群文件夹"},
    "delete_group_folder": {"category": "file", "summary": "删除群文件夹"},
    "get_group_file_system_info": {"category": "file", "summary": "群文件系统信息"},
    "get_group_root_files": {"category": "file", "summary": "群根目录文件"},
    "get_group_files_by_folder": {"category": "file", "summary": "群子目录文件"},
    "get_group_file_url": {"category": "file", "summary": "群文件链接"},
    "move_group_file": {"category": "file", "summary": "移动群文件"},
    "trans_group_file": {"category": "file", "summary": "转发群文件"},
    "rename_group_file": {"category": "file", "summary": "重命名群文件"},
    "upload_private_file": {"category": "file", "summary": "上传私聊文件"},
    "get_private_file_url": {"category": "file", "summary": "私聊文件链接"},
    "download_file": {"category": "file", "summary": "下载文件"},
    # ai / share / other
    "get_ai_characters": {"category": "ai", "summary": "AI 角色列表"},
    "get_ai_record": {"category": "ai", "summary": "获取 AI 语音"},
    "send_group_ai_record": {"category": "ai", "summary": "发送群 AI 语音"},
    "ArkSharePeer": {"category": "share", "summary": "分享联系人"},
    "ArkShareGroup": {"category": "share", "summary": "分享群"},
    "get_mini_app_ark": {"category": "share", "summary": "小程序卡片"},
    "get_cookies": {"category": "other", "summary": "获取 Cookies"},
    "get_csrf_token": {"category": "other", "summary": "获取 CSRF Token"},
    "get_credentials": {"category": "other", "summary": "获取凭证"},
    "get_rkey": {"category": "other", "summary": "获取 Rkey"},
    "get_rkey_server": {"category": "other", "summary": "获取 Rkey Server"},
    "get_doubt_friends_add_request": {"category": "other", "summary": "可疑好友请求"},
    "set_doubt_friends_add_request": {"category": "other", "summary": "处理可疑好友请求"},
    "translate_en2zh": {"category": "other", "summary": "英译中"},
    "check_url_safely": {"category": "other", "summary": "检查 URL 安全"},
    "get_online_clients": {"category": "other", "summary": "在线客户端"},
    "get_collection_list": {"category": "other", "summary": "收藏列表"},
    "create_collection": {"category": "other", "summary": "创建收藏"},
    "fetch_custom_face": {"category": "other", "summary": "自定义表情"},
    "click_inline_keyboard_button": {"category": "other", "summary": "点击内联按钮"},
    "nc_get_packet_status": {"category": "other", "summary": "数据包状态"},
    "get_robot_uin_range": {"category": "other", "summary": "机器人 UIN 范围"},
    "get_guild_list": {"category": "other", "summary": "频道列表"},
    "get_guild_service_profile": {"category": "other", "summary": "频道资料"},
    "_get_model_show": {"category": "other", "summary": "获取模型展示"},
    "_set_model_show": {"category": "other", "summary": "设置模型展示"},
    ".get_word_slices": {"category": "other", "summary": "词语切片"},
    ".handle_quick_operation": {"category": "other", "summary": "快速操作"},
    "send_packet": {"category": "other", "summary": "发送数据包"},
}

DANGEROUS_ACTIONS = {
    "get_cookies",
    "get_csrf_token",
    "get_credentials",
    "get_clientkey",
    "get_rkey",
    "get_rkey_server",
    "bot_exit",
    "set_restart",
    "send_packet",
}

TOOL_ACTIONS = {
    "get_login_info",
    "get_status",
    "get_version_info",
    "get_stranger_info",
    "get_friend_list",
    "get_group_list",
    "get_group_info",
    "get_group_member_info",
    "get_group_member_list",
    "get_group_honor_info",
    "get_msg",
    "get_forward_msg",
    "get_group_msg_history",
    "get_friend_msg_history",
    "get_recent_contact",
    "get_essence_msg_list",
    "get_group_at_all_remain",
    "get_group_shut_list",
    "get_group_system_msg",
    "_get_group_notice",
    "ocr_image",
    "get_image",
    "get_record",
    "get_file",
    "download_file",
    "upload_group_file",
    "upload_private_file",
    "get_group_file_url",
    "get_private_file_url",
    "get_group_file_system_info",
    "get_group_root_files",
    "get_group_files_by_folder",
    "create_group_file_folder",
    "move_group_file",
    "rename_group_file",
    "trans_group_file",
    "send_private_msg",
    "send_group_msg",
    "send_msg",
    "delete_msg",
    "send_like",
    "send_poke",
    "friend_poke",
    "group_poke",
    "set_group_ban",
    "set_group_whole_ban",
    "set_group_kick",
    "set_group_card",
    "set_group_name",
    "set_group_special_title",
    "set_essence_msg",
    "delete_essence_msg",
    "set_group_add_request",
    "set_friend_add_request",
    "mark_msg_as_read",
    "set_msg_emoji_like",
    "_send_group_notice",
}


class NapcatAPIError(RuntimeError):
    def __init__(self, action: str, status: str, retcode: int, message: str, data: Any = None) -> None:
        self.action = action
        self.status = status
        self.retcode = retcode
        self.payload = data
        super().__init__(f"{action} failed status={status} retcode={retcode} {message}".strip())


def normalize_action(action: str) -> str:
    name = (action or "").strip().lstrip("/")
    if not name:
        raise ValueError("action is required")
    check = name[1:] if name.startswith(".") else name
    if not ACTION_RE.match(check):
        raise ValueError(f"invalid action name: {action}")
    return name


def is_dangerous(action: str) -> bool:
    base = action.replace("_async", "").replace("_rate_limited", "")
    return base in DANGEROUS_ACTIONS


def dump_result(data: Any, limit: int = 4000) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > limit:
        return text[:limit] + "...(truncated)"
    return text


def as_id(value: Any) -> int | str:
    if value is None or value == "":
        raise ValueError("id is required")
    text = str(value)
    return int(text) if text.isdigit() else text


def text_segments(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "data": {"text": text}}]


class NapcatClient:
    """OneBot 11 HTTP client compatible with NapCat 4.18.19."""

    def __init__(
        self,
        base_url: str,
        access_token: str = "",
        timeout: float = 20.0,
        logger: ChatbotLogger | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.access_token = access_token or ""
        self.logger = logger or ChatbotLogger()
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def call(self, action: str, params: dict[str, Any] | None = None) -> Any:
        name = normalize_action(action)
        if not self.base_url:
            raise NapcatAPIError(name, "failed", -1, "bot_url is empty")
        url = f"{self.base_url}/{name}"
        try:
            response = await self._client.post(url, json=params or {}, headers=self._headers())
        except Exception as exc:
            raise NapcatAPIError(name, "failed", -1, str(exc)) from exc
        try:
            body = response.json()
        except Exception as exc:
            raise NapcatAPIError(name, "failed", response.status_code, response.text[:300]) from exc
        if not isinstance(body, dict):
            raise NapcatAPIError(name, "failed", response.status_code, "invalid response")
        status = str(body.get("status") or ("ok" if response.is_success else "failed"))
        retcode = int(body.get("retcode") or 0)
        message = str(body.get("message") or body.get("wording") or "")
        if status != "ok" or retcode != 0 or not response.is_success:
            raise NapcatAPIError(name, status, retcode or response.status_code, message, body.get("data"))
        return body.get("data")

    async def send_msg(
        self,
        *,
        message: str | list[dict[str, Any]],
        message_type: str,
        user_id: Any = None,
        group_id: Any = None,
    ) -> Any:
        params: dict[str, Any] = {
            "message_type": message_type,
            "message": message if not isinstance(message, str) else text_segments(message),
        }
        if message_type == "group":
            params["group_id"] = as_id(group_id)
        else:
            params["user_id"] = as_id(user_id)
        return await self.call("send_msg", params)

    async def get_bytes(self, url: str) -> bytes:
        from urllib.parse import urlparse

        headers = {}
        target_host = (urlparse(url).hostname or "").lower()
        bot_host = (urlparse(self.base_url).hostname or "").lower()
        if self.access_token and target_host and target_host == bot_host:
            headers["Authorization"] = f"Bearer {self.access_token}"
        response = await self._client.get(url, headers=headers, follow_redirects=True)
        response.raise_for_status()
        return response.content

    async def aclose(self) -> None:
        await self._client.aclose()
