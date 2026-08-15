# Elysia M5.4.2 记忆管理 WebSocket 协议 v1

- 更新日期：2026-08-14
- 协议版本：`1`
- 文档状态：M5.4.2.1 冻结
- 依赖基线：`e984492`

## 1. 目的与范围

本文冻结 M5.4.2 后端记忆管理平面的 WebSocket JSON 协议。后续后端接口和前端管理界面必须遵循本文，不能在实现阶段隐式改变消息名称、字段、状态、revision 或隐私语义。

本协议只覆盖本机用户主动管理持久记忆，不覆盖：

- M5.3 自然语言显式记忆命令；
- TTS、字幕、表情和聊天历史反馈；
- 群聊、代理模式和远程管理；
- 隐式记忆提取；
- `profile_id`、`conf_uid`、容量和存储路径编辑。

管理平面不调用 Agent、不产生语音、不写入聊天历史，也不进入短期会话上下文。

## 2. 传输与通用信封

所有管理消息使用现有 WebSocket 连接上的 UTF-8 JSON 文本帧。

合法请求必须包含：

```json
{
  "type": "memory-state-request",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a"
}
```

通用规则：

- `protocol_version` 为严格整数，v1 固定为 `1`；布尔值和字符串不等价于整数。
- `request_id` 使用标准 UUID v4 字符串，由客户端生成。
- 后端在响应中关联同一 `request_id`。
- 请求模型禁止未知字段。
- 每个合法管理请求只产生一个终态响应。
- `request_id` 不作为服务端幂等缓存键；修改操作依靠 revision 或开关期望值防重。
- 非法或缺少 `request_id` 时，错误结果的 `request_id` 为 `null`。
- 不支持的协议版本返回 `unsupported_protocol_version`。

## 3. 消息类型总览

### 3.1 请求

| `type` | 额外必需字段 | 作用 |
| --- | --- | --- |
| `memory-state-request` | 无 | 获取管理状态和能力 |
| `memory-list-request` | 无 | 获取当前作用域全部条目 |
| `memory-upsert-request` | `category`、`value`、`expected_revision` | 添加或幂等命中条目 |
| `memory-update-request` | `memory_id`、`category`、`value`、`expected_revision` | 按 ID 编辑条目 |
| `memory-delete-request` | `memory_id`、`expected_revision` | 按 ID 删除条目 |
| `memory-clear-request` | `expected_revision`、`confirm` | 清空全部条目 |
| `memory-setting-update` | `enabled`、`expected_enabled` | 更新总开关 |

### 3.2 响应

| `type` | 作用 |
| --- | --- |
| `memory-state` | 成功返回状态和能力 |
| `memory-list` | 成功返回全部可管理条目 |
| `memory-management-result` | 返回修改结果，以及状态/列表失败 |

## 4. 禁止由客户端指定的字段

任何请求都不得包含：

- `profile_id`
- `conf_uid` 或 `character_conf_uid`
- `storage_dir`
- `source`
- 内部 `key`
- 主文件、备份或临时文件路径
- 备份模式
- Prompt 或渲染上下文

后端必须从当前连接的 `ServiceContext` 推导作用域，并固定 UI 写入来源为 `manual_ui`。

## 5. 请求定义

### 5.1 状态请求

```json
{
  "type": "memory-state-request",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a"
}
```

### 5.2 列表请求

```json
{
  "type": "memory-list-request",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a"
}
```

M5 最大容量为 100 条，v1 不提供分页。

### 5.3 添加请求

```json
{
  "type": "memory-upsert-request",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a",
  "category": "preference",
  "value": "饮料不要太甜",
  "expected_revision": 5
}
```

规则：

- `category` 只允许 `preferred_address`、`preference`、`important_fact`。
- `value` 必须包含非空白字符，不得包含 Unicode 控制字符，协议绝对上限为 500 个字符。
- 具体配置限制和敏感内容校验由业务层执行。
- 相同规范化内容为幂等成功：`changed=false`，revision 不增加，并返回现有 `memory_id`。
- 请求不得携带 `memory_id`。

### 5.4 编辑请求

```json
{
  "type": "memory-update-request",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a",
  "memory_id": "9b88f7a60a3c49c0a4fe87a044264d22",
  "category": "preference",
  "value": "饮料只要微甜",
  "expected_revision": 5
}
```

规则：

- `memory_id` 为现有持久化契约使用的 32 位小写十六进制 UUID。
- 成功编辑保留原 `id` 和 `created_at`，只更新内容、类别及 `updated_at`。
- 相同类别和规范化内容为幂等成功。
- 与其他条目重复时返回 `duplicate_item`。
- ID 不存在时返回 `not_found`。

### 5.5 删除请求

```json
{
  "type": "memory-delete-request",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a",
  "memory_id": "9b88f7a60a3c49c0a4fe87a044264d22",
  "expected_revision": 5
}
```

UI 删除只按稳定 ID，不执行正文、包含、模糊或语义匹配，因此管理协议不产生 `ambiguous_match`。

### 5.6 清空请求

```json
{
  "type": "memory-clear-request",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a",
  "expected_revision": 5,
  "confirm": true
}
```

`confirm` 只接受 JSON 布尔值 `true`。`false`、字符串、数字或缺失均属于 `invalid_request`。UI 的确认弹窗不复用 M5.3 语音命令的 60 秒连接级确认状态。

### 5.7 开关更新请求

```json
{
  "type": "memory-setting-update",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a",
  "enabled": false,
  "expected_enabled": true
}
```

开关不使用记忆文档 revision，而使用 `expected_enabled` 防止陈旧界面覆盖更新后的配置状态。

## 6. 状态响应

```json
{
  "type": "memory-state",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a",
  "supported": true,
  "enabled": false,
  "available": true,
  "item_count": 3,
  "max_items": 24,
  "revision": 5,
  "plaintext_storage": true,
  "capabilities": {
    "list": true,
    "create": false,
    "update": false,
    "delete": true,
    "clear": true,
    "set_enabled": true
  },
  "reason_code": null
}
```

冻结的能力矩阵：

| 状态 | list | create/update | delete/clear | set_enabled |
| --- | ---: | ---: | ---: | ---: |
| 已启用且存储可用 | 是 | 是 | 是 | 是 |
| 已关闭且存储可用 | 是 | 否 | 是 | 是 |
| 存储不可用 | 否 | 否 | 否 | 仅允许关闭 |

约束：

- 成功的 `memory-state` 中 `supported` 和 `plaintext_storage` 固定为 `true`。
- 存储可用时必须返回 `item_count` 和 `revision`，且 `reason_code=null`。
- 存储不可用时 `item_count` 和 `revision` 必须为 `null`，`reason_code=storage_failure`。
- 非本机、代理、群聊、不支持 Agent 或非法作用域使用失败结果，不返回状态正文、条目数量、revision 或开关状态。

“关闭后允许查看和删除”只属于用户主动管理；对话链路关闭后仍不得读取、写入或注入记忆。

## 7. 列表响应

```json
{
  "type": "memory-list",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a",
  "revision": 5,
  "item_count": 2,
  "max_items": 24,
  "items": [
    {
      "id": "9b88f7a60a3c49c0a4fe87a044264d22",
      "category": "preferred_address",
      "value": "阿源",
      "source": "explicit_user",
      "created_at": "2026-08-14T08:00:00Z",
      "updated_at": "2026-08-14T08:00:00Z"
    }
  ]
}
```

列表规则：

1. `preferred_address`；
2. `preference`；
3. `important_fact`；
4. 同类按 `updated_at` 降序；
5. 时间相同按 `id` 升序。

响应只返回管理界面需要的公开字段。时间必须是 UTC，并序列化为带 `Z` 的 ISO 8601 字符串。只有成功的 `memory-list` 可以返回记忆正文。

## 8. 统一操作结果

```json
{
  "type": "memory-management-result",
  "protocol_version": 1,
  "request_id": "8ec55466-eec1-4bef-8133-cc883d03965a",
  "operation": "update",
  "status": "success",
  "reason_code": null,
  "changed": true,
  "memory_id": "9b88f7a60a3c49c0a4fe87a044264d22",
  "revision": 6,
  "item_count": 3,
  "enabled": true
}
```

`operation`：

```text
state | list | create | update | delete | clear | set_enabled
```

`status`：

```text
success | rejected | failed
```

约束：

- `changed` 始终存在。
- `memory_id`、`revision`、`item_count`、`enabled` 可以为 `null`。
- 成功结果不得携带 `reason_code`。
- 非成功结果必须携带 `reason_code`。
- `changed=true` 只允许出现在成功结果中。
- 成功的 create、update、delete 必须返回 `memory_id`。
- state 和 list 成功使用专用响应；`memory-management-result` 只表示它们的失败。
- 结果不得携带记忆正文、请求原文、敏感命中内容、文件路径或异常正文。

## 9. 原因码

### 9.1 协议与访问

- `invalid_request`
- `unsupported_protocol_version`
- `local_access_required`
- `proxy_not_supported`
- `group_not_supported`
- `unsupported_agent`
- `invalid_scope`

### 9.2 业务状态

- `memory_disabled`
- `storage_failure`
- `invalid_value`
- `sensitive_content`
- `capacity_reached`
- `not_found`
- `duplicate_item`
- `revision_conflict`
- `setting_conflict`

### 9.3 配置与内部故障

- `config_persist_failure`
- `internal_error`

状态映射：

| 情况 | `status` |
| --- | --- |
| 协议拒绝、权限限制、关闭、敏感内容、容量、未找到、重复或冲突 | `rejected` |
| 存储、配置持久化或不可预期内部故障 | `failed` |
| 实际修改或幂等成功 | `success` |

## 10. revision 与重复请求

- 添加、编辑、删除和清空必须携带读取列表时获得的 `expected_revision`。
- 管理请求遇到 revision 冲突后不得自动重试到新版本。
- 冲突结果可以返回当前 revision 和 item count，但不得返回正文。
- 客户端收到冲突后重新请求列表并让用户重新确认。
- 实际写入 revision 只增加一次。
- 幂等成功 revision 不变。
- 同一修改请求被重复发送时，CAS 保证后续请求不会重复修改。
- 本规则不改变 M5.3 自然语言命令现有的单次重载重算策略。

## 11. 访问与隐私边界

- 管理接口只允许回环地址连接。
- 代理模式、群聊、非 `basic_memory_agent` 和非法作用域必须拒绝。
- 不满足资格的状态请求不能泄漏开关、数量或 revision。
- 后端不得记录完整管理请求、列表响应或条目正文。
- 前端不得把 `memory-list`、添加或编辑负载输出到控制台。
- Toast 不显示条目正文。
- 条目值必须作为纯文本渲染，不能作为 HTML 或 Markdown。
- 删除和清空继续使用 replacement 备份语义。

## 12. v1 兼容性规则

- v1 已冻结的字段不能改变类型、含义或必需性。
- 可以新增新的协议版本，但不能让 v1 客户端静默采用新语义。
- v1 响应不能新增未经客户端容错验证的必需字段。
- 后续实现不得让客户端决定作用域、来源、路径或安全策略。
- WebSocket 接入前必须由纯协议模型测试验证本文全部示例和非法负载。
