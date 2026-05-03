"""
# 撤回守卫插件 (Recall Guard)

为 OneBot V11 提供三类撤回能力：
- 机器人消息定时自动撤回
- 用户撤回原消息后取消当前回复
- AI 根据当前频道权限主动撤回消息
"""

from .plugin import plugin

__all__ = ["plugin"]
