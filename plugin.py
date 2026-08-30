"""撤回守卫插件

提供三类撤回相关能力：
1. 机器人在 OneBot V11 下发送的文本消息支持定时自动撤回。
2. 用户撤回原消息时，若该消息仍在防抖队列或正在触发回复，则取消本次回复流程。
3. 提供给 AI 调用的撤回接口，让 AI 在满足权限规则时主动撤回消息。

实现约束：
- 仅通过插件文件自身完成接入，不修改现有核心逻辑。
- 当前仅对 OneBot V11 生效。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from nonebot import on_notice
from nonebot.adapters.onebot.v11 import NoticeEvent
from pydantic import Field, field_validator

from nekro_agent.api import i18n
from nekro_agent.api.plugin import ConfigBase, ExtraField, NekroPlugin, SandboxMethodType
from nekro_agent.api.schemas import AgentCtx
from nekro_agent.models.db_chat_message import DBChatMessage
from nekro_agent.schemas.chat_message import ChatMessage
from nekro_agent.services.message_service import message_service

plugin = NekroPlugin(
    name="撤回守卫插件",
    module_name="recall_guard",
    description="为 OneBot V11 提供机器人定时撤回、撤回取消回复与 AI 主动撤回能力",
    version="1.0.0",
    author="XGGM",
    url="https://github.com/XG2020/recall_guard",
    support_adapter=["onebot_v11"],
    i18n_name=i18n.i18n_text(
        zh_CN="撤回守卫插件",
        en_US="Recall Guard Plugin",
    ),
    i18n_description=i18n.i18n_text(
        zh_CN="为 OneBot V11 提供机器人定时撤回、撤回取消回复与 AI 主动撤回能力",
        en_US="Provides timed recall, recall-cancel and AI-initiated recall for OneBot V11",
    ),
    allow_sleep=False,
    sleep_brief="处理 OneBot 消息撤回：支持机器人定时撤回、用户撤回后取消当前回复，以及 AI 主动撤回消息。",
)


@plugin.mount_config()
class RecallGuardConfig(ConfigBase):
    """撤回守卫配置"""

    ENABLE_AUTO_RECALL: bool = Field(
        default=True,
        title="启用机器人消息定时撤回",
        description="启用后，机器人在 OneBot V11 中发出的文本消息会在指定秒数后自动撤回",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="启用机器人消息定时撤回",
                en_US="Enable Timed Bot Recall",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="启用后，机器人在 OneBot V11 中发出的文本消息会在指定秒数后自动撤回",
                en_US="When enabled, bot messages sent on OneBot V11 will be recalled after a delay",
            ),
        ).model_dump(),
    )
    AUTO_RECALL_SECONDS: int = Field(
        default=30,
        title="自动撤回延迟秒数",
        description="机器人消息发送成功后，等待多少秒后执行撤回",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="自动撤回延迟秒数",
                en_US="Auto Recall Delay",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="机器人消息发送成功后，等待多少秒后执行撤回",
                en_US="Delay in seconds before recalling a sent bot message",
            ),
        ).model_dump(),
    )
    ENABLE_GROUP_AUTO_RECALL: bool = Field(
        default=True,
        title="群聊启用自动撤回",
        description="是否在群聊中启用机器人消息自动撤回",
    )
    ENABLE_PRIVATE_AUTO_RECALL: bool = Field(
        default=False,
        title="私聊启用自动撤回",
        description="是否在私聊中启用机器人消息自动撤回",
    )
    ENABLE_RECALL_CANCEL: bool = Field(
        default=True,
        title="启用用户撤回取消回复",
        description="启用后，用户撤回原消息时会取消该消息对应的待处理或进行中回复",
    )
    ENABLE_GROUP_RECALL_CANCEL: bool = Field(
        default=True,
        title="群聊启用撤回取消回复",
        description="是否在群聊中启用“用户撤回后取消回复”",
    )
    ENABLE_PRIVATE_RECALL_CANCEL: bool = Field(
        default=True,
        title="私聊启用撤回取消回复",
        description="是否在私聊中启用“用户撤回后取消回复”",
    )
    ENABLE_AI_RECALL_TOOL: bool = Field(
        default=True,
        title="启用 AI 主动撤回工具",
        description="启用后，AI 可以根据当前频道权限规则主动撤回消息",
    )
    GROUP_WHITELIST: List[str] = Field(
        default=[],
        title="群号白名单",
        description="为空时表示所有群都生效；填写后仅对白名单群生效，只需填写纯群号",
    )
    DELETE_RECALLED_MESSAGE_FROM_HISTORY: bool = Field(
        default=True,
        title="从历史中移除已撤回用户消息",
        description="启用后，用户撤回的原消息会从数据库历史中删除，避免继续进入后续上下文",
    )
    DELETE_AUTO_RECALLED_BOT_MESSAGE_FROM_HISTORY: bool = Field(
        default=True,
        title="从历史中移除已撤回机器人消息",
        description="启用后，被自动撤回或手动撤回的机器人消息会从数据库历史中删除",
    )
    HIDE_RECALL_SYSTEM_NOTICE: bool = Field(
        default=True,
        title="隐藏撤回系统提示",
        description="启用后，OneBot 撤回事件生成的系统提示会从历史中清理，避免污染上下文",
    )
    SELF_RECALL_TIME_LIMIT_SECONDS: int = Field(
        default=120,
        title="普通成员/私聊自主撤回时限",
        description="当机器人在群里只是普通成员，或当前为私聊频道时，只允许撤回自己在该时间内发送的消息",
    )

    @field_validator("AUTO_RECALL_SECONDS", "SELF_RECALL_TIME_LIMIT_SECONDS")
    @classmethod
    def validate_positive_seconds(cls, value: int) -> int:
        if value < 1:
            raise ValueError("秒数不能小于 1")
        return value

    @field_validator("GROUP_WHITELIST", mode="before")
    @classmethod
    def validate_group_whitelist(cls, value: Any) -> List[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            raw_items = value.replace("\n", ",").split(",")
            return [item.strip() for item in raw_items if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("群号白名单格式错误")


config: RecallGuardConfig = plugin.get_config(RecallGuardConfig)

_original_push_bot_message: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None
_original_run_chat_agent_task: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None
_runtime_patched = False

_auto_recall_tasks: set[asyncio.Task[Any]] = set()
_running_source_message_ids: Dict[str, str] = {}
_recent_local_recalled: Dict[Tuple[str, str], float] = {}

RECALL_NOTICE_TYPES = {"group_recall", "friend_recall"}
SYSTEM_NOTICE_CLEANUP_WINDOW_SECONDS = 8
LOCAL_RECALL_CACHE_EXPIRE_SECONDS = 60


def _remove_task(task: asyncio.Task[Any]) -> None:
    _auto_recall_tasks.discard(task)


def _cleanup_recent_local_recalled_cache() -> None:
    now = time.time()
    expired_keys = [
        key
        for key, ts in _recent_local_recalled.items()
        if now - ts > LOCAL_RECALL_CACHE_EXPIRE_SECONDS
    ]
    for key in expired_keys:
        _recent_local_recalled.pop(key, None)


def _parse_chat_key(chat_key: str) -> tuple[str, str]:
    try:
        _, channel_id = chat_key.split("-", 1)
    except ValueError as exc:
        raise ValueError(f"无效的 chat_key: {chat_key}") from exc

    if channel_id.startswith("group_"):
        return "group", channel_id.removeprefix("group_")
    if channel_id.startswith("private_"):
        return "private", channel_id.removeprefix("private_")
    raise ValueError(f"不支持的频道类型: {chat_key}")


def _parse_channel_id(channel_id: str) -> tuple[str, str]:
    if channel_id.startswith("group_"):
        return "group", channel_id.removeprefix("group_")
    if channel_id.startswith("private_"):
        return "private", channel_id.removeprefix("private_")
    raise ValueError(f"不支持的频道 ID: {channel_id}")


def _is_group_allowed(group_id: str) -> bool:
    whitelist = {group_id_.strip() for group_id_ in config.GROUP_WHITELIST if group_id_.strip()}
    return not whitelist or group_id in whitelist


def _is_feature_enabled_for_chat(chat_key: str, *, for_auto_recall: bool) -> bool:
    try:
        chat_type, group_or_user_id = _parse_chat_key(chat_key)
    except ValueError:
        return False

    if chat_type == "group":
        enabled = config.ENABLE_GROUP_AUTO_RECALL if for_auto_recall else config.ENABLE_GROUP_RECALL_CANCEL
        return enabled and _is_group_allowed(group_or_user_id)

    if chat_type == "private":
        return config.ENABLE_PRIVATE_AUTO_RECALL if for_auto_recall else config.ENABLE_PRIVATE_RECALL_CANCEL

    return False


def _build_chat_key_from_notice(event_dict: Dict[str, Any]) -> str:
    notice_type = str(event_dict.get("notice_type", ""))
    if notice_type == "group_recall":
        return f"onebot_v11-group_{event_dict['group_id']}"
    if notice_type == "friend_recall":
        return f"onebot_v11-private_{event_dict['user_id']}"
    raise ValueError(f"不支持的撤回事件类型: {notice_type}")


async def _delete_onebot_message(message_id: str) -> bool:
    from nekro_agent.adapters.onebot_v11.core.bot import get_bot

    bot = get_bot()
    await bot.call_api("delete_msg", message_id=int(message_id))
    return True


async def _delete_db_messages(chat_key: str, message_id: str) -> int:
    query = DBChatMessage.filter(chat_key=chat_key, message_id=message_id)
    count = await query.count()
    if count:
        await query.delete()
    return count


async def _cleanup_recent_recall_system_notice(chat_key: str) -> int:
    if not config.HIDE_RECALL_SYSTEM_NOTICE:
        return 0

    cutoff = int(time.time()) - SYSTEM_NOTICE_CLEANUP_WINDOW_SECONDS
    notices = await DBChatMessage.filter(
        chat_key=chat_key,
        sender_name="SYSTEM",
        content_text__contains="撤回",
        send_timestamp__gte=cutoff,
    ).all()
    if not notices:
        return 0

    for notice in notices:
        await notice.delete()
    return len(notices)


async def _mark_message_recalled(chat_key: str, message_id: str, *, prefer_delete: bool) -> int:
    if prefer_delete:
        return await _delete_db_messages(chat_key, message_id)

    await DBChatMessage.filter(chat_key=chat_key, message_id=message_id).update(is_recalled=True)
    return 0


async def _get_bot_role_in_ctx(_ctx: AgentCtx) -> str:
    if _ctx.adapter_key != "onebot_v11":
        raise ValueError("当前仅支持 OneBot V11")

    channel_type, channel_real_id = _parse_channel_id(_ctx.channel_id or "")
    if channel_type == "private":
        return "private"

    bot = await _ctx.get_onebot_v11_bot()
    try:
        member_info = await bot.get_group_member_info(
            group_id=int(channel_real_id),
            user_id=int(bot.self_id),
            no_cache=False,
        )
    except Exception as exc:
        plugin.logger.debug(f"[RecallGuard] 查询机器人群权限失败，回退为普通成员: {exc}")
        return "member"

    return str(member_info.get("role", "member") or "member")


def _is_bot_message(db_message: DBChatMessage) -> bool:
    return str(db_message.sender_id) == "-1"


def _is_within_self_recall_limit(db_message: DBChatMessage) -> bool:
    return int(time.time()) - int(db_message.send_timestamp) <= config.SELF_RECALL_TIME_LIMIT_SECONDS


async def _get_target_db_message(chat_key: str, message_id: str, recent_offset: int) -> DBChatMessage:
    if message_id.strip():
        target = await DBChatMessage.filter(
            chat_key=chat_key,
            message_id=message_id.strip(),
        ).order_by("-send_timestamp").first()
        if not target:
            raise ValueError(f"未找到消息 ID 为 {message_id} 的消息")
        return target

    if recent_offset < 0:
        raise ValueError("recent_offset 不能小于 0")

    target = await (
        DBChatMessage.filter(chat_key=chat_key, sender_id="-1")
        .exclude(message_id="")
        .order_by("-send_timestamp")
        .offset(recent_offset)
        .first()
    )
    if not target:
        raise ValueError("当前频道没有可供撤回的机器人消息")
    return target


def _ensure_manual_recall_allowed(role: str, db_message: DBChatMessage) -> None:
    if role in {"admin", "owner"}:
        return

    if not _is_bot_message(db_message):
        raise ValueError("当前权限仅允许撤回机器人自己发送的消息")

    if not _is_within_self_recall_limit(db_message):
        raise ValueError(
            f"当前权限仅允许撤回机器人在 {config.SELF_RECALL_TIME_LIMIT_SECONDS} 秒内发送的消息"
        )


def _build_recall_capability_text(role: str) -> str:
    if role in {"admin", "owner"}:
        return (
            "Recall capability: available. In this group the bot is admin/owner, "
            "so it can recall any message in the current chat at any time."
        )
    return (
        "Recall capability: limited. In private chat or when the bot is only a group member, "
        f"it may only recall its own messages sent within {config.SELF_RECALL_TIME_LIMIT_SECONDS} seconds. "
        "Call `recall_chat_message()` without `message_id` to recall the latest bot message."
    )


async def _auto_recall_after_delay(chat_key: str, message_id: str) -> None:
    await asyncio.sleep(config.AUTO_RECALL_SECONDS)

    if not plugin.is_enabled or not config.ENABLE_AUTO_RECALL:
        return
    if not _is_feature_enabled_for_chat(chat_key, for_auto_recall=True):
        return

    try:
        await _delete_onebot_message(message_id)
        _recent_local_recalled[(chat_key, message_id)] = time.time()

        if config.DELETE_AUTO_RECALLED_BOT_MESSAGE_FROM_HISTORY:
            await _delete_db_messages(chat_key, message_id)

        plugin.logger.info(f"[RecallGuard] 已自动撤回机器人消息: chat={chat_key}, msg={message_id}")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        plugin.logger.warning(
            f"[RecallGuard] 自动撤回机器人消息失败: chat={chat_key}, msg={message_id}, error={exc}"
        )


async def _patched_push_bot_message(
    chat_key: str,
    agent_messages: Any,
    plt_response: Any = None,
    db_chat_channel: Any = None,
    ref_msg_id: Optional[str] = None,
    normalize_at_markup: bool = True,
):
    if _original_push_bot_message is None:
        raise RuntimeError("RecallGuard 未正确挂载 push_bot_message")

    result = await _original_push_bot_message(
        chat_key=chat_key,
        agent_messages=agent_messages,
        plt_response=plt_response,
        db_chat_channel=db_chat_channel,
        ref_msg_id=ref_msg_id,
        normalize_at_markup=normalize_at_markup,
    )

    if not plugin.is_enabled or not config.ENABLE_AUTO_RECALL:
        return result
    if not _is_feature_enabled_for_chat(chat_key, for_auto_recall=True):
        return result

    message_id = str(getattr(plt_response, "message_id", "") or "").strip()
    if not message_id:
        return result

    task = asyncio.create_task(_auto_recall_after_delay(chat_key, message_id))
    task.add_done_callback(_remove_task)
    _auto_recall_tasks.add(task)
    return result


async def _patched_run_chat_agent_task(
    chat_key: str,
    message: Optional[ChatMessage] = None,
    ctx: Any = None,
):
    if _original_run_chat_agent_task is None:
        raise RuntimeError("RecallGuard 未正确挂载 _run_chat_agent_task")

    source_message_id = (message.message_id or "").strip() if message else ""
    if source_message_id:
        _running_source_message_ids[chat_key] = source_message_id
    else:
        _running_source_message_ids.pop(chat_key, None)

    try:
        return await _original_run_chat_agent_task(
            chat_key=chat_key,
            message=message,
            ctx=ctx,
        )
    finally:
        if _running_source_message_ids.get(chat_key) == source_message_id:
            _running_source_message_ids.pop(chat_key, None)


@plugin.mount_prompt_inject_method("recall_guard_capability")
async def recall_guard_prompt(_ctx: AgentCtx) -> str:
    """向 AI 注入当前频道下的撤回能力说明"""
    if not config.ENABLE_AI_RECALL_TOOL or _ctx.adapter_key != "onebot_v11":
        return ""
    try:
        role = await _get_bot_role_in_ctx(_ctx)
    except Exception:
        return ""
    return _build_recall_capability_text(role)


@plugin.mount_sandbox_method(
    SandboxMethodType.TOOL,
    name="撤回当前聊天中的消息",
    description="在当前 OneBot 聊天中撤回消息；不传 message_id 时默认撤回最近一条机器人消息",
)
async def recall_chat_message(
    _ctx: AgentCtx,
    message_id: str = "",
    recent_offset: int = 0,
) -> str:
    """撤回当前聊天中的消息

    用途：
    - 当你发现自己刚发出的消息明显错误、重复、跑题或不适合继续保留时，可以主动调用。
    - 不传 `message_id` 时，会默认撤回当前聊天最近一条机器人消息。
    - 传入 `recent_offset=1` 可撤回倒数第二条机器人消息，以此类推。
    - 如果当前群中机器人身份是群主或管理员，则可撤回当前聊天中的任意消息。
    - 如果当前是私聊，或机器人在群里只是普通成员，则只能撤回机器人自己在限定时间内发送的消息。

    Args:
        message_id (str): 要撤回的目标消息 ID。留空则自动选择最近一条机器人消息。
        recent_offset (int): 当 `message_id` 为空时，选择倒数第几条机器人消息。0 表示最近一条。

    Returns:
        str: 被成功撤回的消息 ID

    Example:
        recall_chat_message()
        recall_chat_message(recent_offset=1)
        recall_chat_message(message_id="123456789")
    """
    if _ctx.adapter_key != "onebot_v11":
        raise ValueError("该工具当前仅支持 OneBot V11")
    if not config.ENABLE_AI_RECALL_TOOL:
        raise ValueError("AI 主动撤回工具当前未启用")

    role = await _get_bot_role_in_ctx(_ctx)
    db_message = await _get_target_db_message(_ctx.chat_key, message_id, recent_offset)
    _ensure_manual_recall_allowed(role, db_message)

    try:
        await _delete_onebot_message(db_message.message_id)
    except Exception as exc:
        raise ValueError(f"撤回失败: {exc}") from exc

    _recent_local_recalled[(_ctx.chat_key, db_message.message_id)] = time.time()
    prefer_delete = config.DELETE_AUTO_RECALLED_BOT_MESSAGE_FROM_HISTORY if _is_bot_message(db_message) else True
    await _mark_message_recalled(_ctx.chat_key, db_message.message_id, prefer_delete=prefer_delete)
    await _cleanup_recent_recall_system_notice(_ctx.chat_key)

    plugin.logger.info(
        f"[RecallGuard] AI 主动撤回成功: chat={_ctx.chat_key}, msg={db_message.message_id}, role={role}"
    )
    return db_message.message_id


@plugin.mount_collect_methods()
async def collect_available_methods(_ctx: AgentCtx) -> List[Callable]:
    """按适配器和配置收集可用方法"""
    methods: List[Callable] = []
    if _ctx.adapter_key == "onebot_v11" and config.ENABLE_AI_RECALL_TOOL:
        methods.append(recall_chat_message)
    return methods


@plugin.mount_init_method()
async def init_plugin() -> None:
    """初始化插件并挂载运行时钩子"""
    global _original_push_bot_message, _original_run_chat_agent_task, _runtime_patched

    if _runtime_patched:
        return

    _original_push_bot_message = message_service.push_bot_message
    _original_run_chat_agent_task = message_service._run_chat_agent_task  # noqa: SLF001
    message_service.push_bot_message = _patched_push_bot_message  # type: ignore[method-assign]
    message_service._run_chat_agent_task = _patched_run_chat_agent_task  # type: ignore[attr-defined,method-assign]  # noqa: SLF001
    _runtime_patched = True
    plugin.logger.info("[RecallGuard] 运行时钩子挂载完成")


@plugin.mount_cleanup_method()
async def clean_up() -> None:
    """卸载插件时恢复运行时钩子并清理任务"""
    global _runtime_patched

    if _original_push_bot_message is not None:
        message_service.push_bot_message = _original_push_bot_message  # type: ignore[method-assign]
    if _original_run_chat_agent_task is not None:
        message_service._run_chat_agent_task = _original_run_chat_agent_task  # type: ignore[attr-defined,method-assign]  # noqa: SLF001

    for task in list(_auto_recall_tasks):
        if not task.done():
            task.cancel()
    if _auto_recall_tasks:
        await asyncio.gather(*_auto_recall_tasks, return_exceptions=True)

    _auto_recall_tasks.clear()
    _running_source_message_ids.clear()
    _recent_local_recalled.clear()
    _runtime_patched = False


recall_notice_matcher = on_notice(priority=100000, block=False)


@recall_notice_matcher.handle()
async def _handle_onebot_recall_notice(event: NoticeEvent) -> None:
    """监听 OneBot 撤回事件，取消对应待回复流程"""
    if not plugin.is_enabled or not config.ENABLE_RECALL_CANCEL:
        return

    event_dict = dict(event)
    notice_type = str(event_dict.get("notice_type", ""))
    if notice_type not in RECALL_NOTICE_TYPES:
        return

    recalled_message_id = str(event_dict.get("message_id", "") or "").strip()
    if not recalled_message_id:
        return

    try:
        chat_key = _build_chat_key_from_notice(event_dict)
    except Exception as exc:
        plugin.logger.warning(f"[RecallGuard] 解析撤回事件失败: {exc}")
        return

    if not _is_feature_enabled_for_chat(chat_key, for_auto_recall=False):
        return

    _cleanup_recent_local_recalled_cache()

    pending_message = message_service.pending_messages.get(chat_key)
    pending_cleared = False
    if pending_message and pending_message.message_id == recalled_message_id:
        message_service.pending_messages.pop(chat_key, None)
        message_service.debounce_timers.pop(chat_key, None)
        pending_cleared = True

    task_cancelled = False
    if _running_source_message_ids.get(chat_key) == recalled_message_id:
        task_cancelled = await message_service.cancel_agent_task(chat_key)

    db_message = await DBChatMessage.filter(
        chat_key=chat_key,
        message_id=recalled_message_id,
    ).order_by("-id").first()

    removed_history = 0
    if db_message is not None:
        prefer_delete = (
            config.DELETE_AUTO_RECALLED_BOT_MESSAGE_FROM_HISTORY
            if _is_bot_message(db_message)
            else config.DELETE_RECALLED_MESSAGE_FROM_HISTORY
        )
        removed_history = await _mark_message_recalled(
            chat_key,
            recalled_message_id,
            prefer_delete=prefer_delete,
        )

    cleaned_notice_count = await _cleanup_recent_recall_system_notice(chat_key)

    if pending_cleared or task_cancelled or removed_history or cleaned_notice_count:
        plugin.logger.info(
            "[RecallGuard] 处理撤回事件成功 | "
            f"chat={chat_key} msg={recalled_message_id} "
            f"pending_cleared={pending_cleared} running_cancelled={task_cancelled} "
            f"history_removed={removed_history} notice_removed={cleaned_notice_count}"
        )
