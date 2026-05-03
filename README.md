# 撤回守卫插件

为 `OneBot V11` 提供三类撤回能力：

- 机器人消息定时自动撤回
- 用户撤回原消息后取消当前回复
- AI 根据当前频道权限主动撤回消息

## 主要功能

### 1. 定时自动撤回

当机器人成功发送消息后，插件会在设定延迟后自动调用 OneBot 的 `delete_msg` 撤回消息。

适用场景：

- 临时提示
- 容易刷屏的短反馈
- 不希望长期保留的中间结果

### 2. 用户撤回后取消回复

当用户撤回原消息时，插件会尝试：

- 清掉仍在防抖队列中的这条触发消息
- 取消当前正由这条消息触发的回复流程
- 可选删除这条已撤回消息在数据库中的历史
- 可选清理撤回事件生成的系统提示

### 3. AI 主动撤回消息

插件向 AI 暴露工具：

- `recall_chat_message()`

支持：

- 不传 `message_id` 时，默认撤回当前频道最近一条机器人消息
- 用 `recent_offset` 撤回更早的机器人消息
- 传 `message_id` 时，按指定消息 ID 进行撤回

## 权限规则

AI 主动撤回严格按当前频道下机器人的平台权限执行：

### 私聊频道

- 只能撤回机器人自己发送的消息
- 且只能撤回 `SELF_RECALL_TIME_LIMIT_SECONDS` 秒内发送的消息

### 群聊且机器人是普通成员

- 只能撤回机器人自己发送的消息
- 且只能撤回 `SELF_RECALL_TIME_LIMIT_SECONDS` 秒内发送的消息

### 群聊且机器人是管理员或群主

- 可以撤回当前聊天中的任意消息
- 不受时间限制

默认时限为 `120` 秒。

## AI 调用示例

撤回最近一条机器人消息：

```python
recall_chat_message()
```

撤回倒数第二条机器人消息：

```python
recall_chat_message(recent_offset=1)
```

按消息 ID 撤回：

```python
recall_chat_message(message_id="123456789")
```

## 配置项

- `ENABLE_AUTO_RECALL`
  - 是否启用机器人消息定时撤回

- `AUTO_RECALL_SECONDS`
  - 自动撤回延迟秒数

- `ENABLE_GROUP_AUTO_RECALL`
  - 群聊是否启用自动撤回

- `ENABLE_PRIVATE_AUTO_RECALL`
  - 私聊是否启用自动撤回

- `ENABLE_RECALL_CANCEL`
  - 是否启用用户撤回后取消回复

- `ENABLE_GROUP_RECALL_CANCEL`
  - 群聊是否启用撤回取消回复

- `ENABLE_PRIVATE_RECALL_CANCEL`
  - 私聊是否启用撤回取消回复

- `ENABLE_AI_RECALL_TOOL`
  - 是否启用 AI 主动撤回工具

- `GROUP_WHITELIST`
  - 群号白名单；为空则对所有群生效

- `DELETE_RECALLED_MESSAGE_FROM_HISTORY`
  - 是否删除已撤回用户消息的历史

- `DELETE_AUTO_RECALLED_BOT_MESSAGE_FROM_HISTORY`
  - 是否删除被撤回的机器人消息历史

- `HIDE_RECALL_SYSTEM_NOTICE`
  - 是否清理撤回事件生成的系统提示

- `SELF_RECALL_TIME_LIMIT_SECONDS`
  - 私聊或普通成员模式下，机器人自主撤回的时间限制

## 注意事项

- 当前仅支持 `OneBot V11`
- AI 主动撤回能力会根据当前频道中机器人的真实身份动态判断
- 如果目标平台实现不支持对应撤回能力，OneBot 侧调用会失败并返回异常
