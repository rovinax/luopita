from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from interface.platform.napcat import NapcatAdapter
from interface.platform.napcat_api import (
    TOOL_ACTIONS,
    NapcatAPIError,
    dump_result,
    is_dangerous,
    normalize_action,
)
from core.identity import OWNER_REFUSAL, is_owner_role


def build_napcat_tools(adapter: NapcatAdapter | None) -> list[BaseTool]:
    async def _call(action: str, params: dict[str, Any] | None = None) -> str:
        if not is_owner_role():
            return OWNER_REFUSAL
        if adapter is None or not adapter.enabled():
            return "Error: NapCat is disabled. Enable the napcat adapter first."
        try:
            return dump_result(await adapter.api.call(action, params))
        except (NapcatAPIError, ValueError) as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: {exc}"

    @tool
    async def qq_get_login_info() -> str:
        """Get the logged-in QQ bot account info (nickname, user_id)."""
        return await _call("get_login_info")

    @tool
    async def qq_get_status() -> str:
        """Get NapCat / QQ online status."""
        return await _call("get_status")

    @tool
    async def qq_get_stranger_info(user_id: str) -> str:
        """Look up a QQ user profile by user_id."""
        return await _call("get_stranger_info", {"user_id": user_id})

    @tool
    async def qq_get_friend_list() -> str:
        """List QQ friends of the logged-in bot."""
        return await _call("get_friend_list")

    @tool
    async def qq_get_group_list() -> str:
        """List QQ groups the bot has joined."""
        return await _call("get_group_list")

    @tool
    async def qq_get_group_info(group_id: str) -> str:
        """Get info for a QQ group."""
        return await _call("get_group_info", {"group_id": group_id})

    @tool
    async def qq_get_group_member_info(group_id: str, user_id: str) -> str:
        """Get one member's info in a QQ group."""
        return await _call("get_group_member_info", {"group_id": group_id, "user_id": user_id})

    @tool
    async def qq_get_group_member_list(group_id: str) -> str:
        """List members of a QQ group."""
        return await _call("get_group_member_list", {"group_id": group_id})

    @tool
    async def qq_get_msg(message_id: str) -> str:
        """Fetch a QQ message by message_id."""
        return await _call("get_msg", {"message_id": message_id})

    @tool
    async def qq_get_group_msg_history(group_id: str, count: int = 20) -> str:
        """Fetch recent group message history."""
        return await _call("get_group_msg_history", {"group_id": group_id, "count": count})

    @tool
    async def qq_send_private_msg(user_id: str, message: str) -> str:
        """Send a private QQ text message to user_id."""
        from core.outbound_sanitize import sanitize_outbound_text

        cleaned = sanitize_outbound_text(message)
        if not cleaned:
            return "Error: message looked like tool markup and was blocked."
        return await _call("send_private_msg", {"user_id": user_id, "message": cleaned})

    @tool
    async def qq_send_group_msg(group_id: str, message: str) -> str:
        """Send a group QQ text message to group_id."""
        from core.outbound_sanitize import sanitize_outbound_text

        cleaned = sanitize_outbound_text(message)
        if not cleaned:
            return "Error: message looked like tool markup and was blocked."
        return await _call("send_group_msg", {"group_id": group_id, "message": cleaned})

    @tool
    async def qq_delete_msg(message_id: str) -> str:
        """Recall / delete a QQ message by message_id."""
        return await _call("delete_msg", {"message_id": message_id})

    @tool
    async def qq_send_like(user_id: str, times: int = 1) -> str:
        """Send profile likes to a QQ friend. Keep times small."""
        return await _call("send_like", {"user_id": user_id, "times": times})

    @tool
    async def qq_send_poke(user_id: str, group_id: str = "") -> str:
        """Poke a user. Pass group_id for a group poke, otherwise private poke."""
        if group_id:
            return await _call("group_poke", {"user_id": user_id, "group_id": group_id})
        return await _call("friend_poke", {"user_id": user_id})

    @tool
    async def qq_set_group_ban(group_id: str, user_id: str, duration: int = 60) -> str:
        """Mute a group member. duration is seconds; 0 unmutes."""
        return await _call("set_group_ban", {"group_id": group_id, "user_id": user_id, "duration": duration})

    @tool
    async def qq_set_group_whole_ban(group_id: str, enable: bool = True) -> str:
        """Enable or disable whole-group mute."""
        return await _call("set_group_whole_ban", {"group_id": group_id, "enable": enable})

    @tool
    async def qq_set_group_kick(group_id: str, user_id: str, reject_add_request: bool = False) -> str:
        """Kick a member from a group."""
        return await _call(
            "set_group_kick",
            {"group_id": group_id, "user_id": user_id, "reject_add_request": reject_add_request},
        )

    @tool
    async def qq_set_group_card(group_id: str, user_id: str, card: str) -> str:
        """Set a member's group card / nickname."""
        return await _call("set_group_card", {"group_id": group_id, "user_id": user_id, "card": card})

    @tool
    async def qq_set_friend_add_request(flag: str, approve: bool = True, remark: str = "") -> str:
        """Approve or deny a friend request. flag comes from the request event."""
        params: dict[str, Any] = {"flag": flag, "approve": approve}
        if remark:
            params["remark"] = remark
        return await _call("set_friend_add_request", params)

    @tool
    async def qq_set_group_add_request(flag: str, approve: bool = True, reason: str = "") -> str:
        """Approve or deny a group join request/invite. flag comes from the request event."""
        params: dict[str, Any] = {"flag": flag, "approve": approve}
        if reason:
            params["reason"] = reason
        return await _call("set_group_add_request", params)

    @tool
    async def qq_ocr_image(image: str) -> str:
        """OCR a QQ image. image is a file id, path, or URL from a message."""
        return await _call("ocr_image", {"image": image})

    @tool
    async def qq_api(action: str, params_json: str = "{}") -> str:
        """Call a NapCat OneBot 11 action by name. params_json is a JSON object.
        File upload/download (upload_group_file, upload_private_file, download_file,
        get_file, get_image, get_group_file_url, …) are allowed.
        Cookies, credentials, bot_exit and similar actions are blocked.
        """
        import json

        try:
            name = normalize_action(action)
        except ValueError as exc:
            return f"Error: {exc}"
        base = name.replace("_async", "").replace("_rate_limited", "")
        if is_dangerous(base) or base not in TOOL_ACTIONS:
            return f"Error: action not allowed for the agent: {name}"
        try:
            params = json.loads(params_json or "{}")
        except json.JSONDecodeError as exc:
            return f"Error: params_json is not valid JSON: {exc}"
        if not isinstance(params, dict):
            return "Error: params_json must be a JSON object"
        return await _call(name, params)

    return [
        qq_get_login_info,
        qq_get_status,
        qq_get_stranger_info,
        qq_get_friend_list,
        qq_get_group_list,
        qq_get_group_info,
        qq_get_group_member_info,
        qq_get_group_member_list,
        qq_get_msg,
        qq_get_group_msg_history,
        qq_send_private_msg,
        qq_send_group_msg,
        qq_delete_msg,
        qq_send_like,
        qq_send_poke,
        qq_set_group_ban,
        qq_set_group_whole_ban,
        qq_set_group_kick,
        qq_set_group_card,
        qq_set_friend_add_request,
        qq_set_group_add_request,
        qq_ocr_image,
        qq_api,
    ]
