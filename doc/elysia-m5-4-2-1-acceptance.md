# Elysia M5.4.2.1 记忆管理协议冻结验收

更新日期：2026-08-14

当前状态：**实现与自动化验收通过；协议 v1 已冻结**。

## 1. 固定范围

本步骤只完成记忆管理 WebSocket v1 协议文档、纯 Pydantic 协议模型、公共导出和模型测试，不接入：

- `PersistentMemoryService` 管理 CRUD；
- `ServiceContext` 管理控制器；
- `WebSocketHandler` 路由；
- `conf.yaml` 开关持久化；
- 前端子模块。

M5.3 自然语言显式记忆命令、固定 TTS 反馈和现有业务语义保持不变。

## 2. 协议冻结结果

### 2.1 通用信封

- [x] 协议版本固定为严格整数 `1`；
- [x] 请求使用 UUID v4 `request_id`；
- [x] 非法请求允许错误结果使用 `request_id=null`；
- [x] 所有协议模型禁止未知字段；
- [x] 请求和响应提供带 `type` 判别字段的联合解析器；
- [x] 管理协议模型不包含持久化、路由、配置修改或对话逻辑。

### 2.2 请求类型

- [x] `memory-state-request`；
- [x] `memory-list-request`；
- [x] `memory-upsert-request`；
- [x] `memory-update-request`；
- [x] `memory-delete-request`；
- [x] `memory-clear-request`；
- [x] `memory-setting-update`。

添加、编辑、删除和清空均冻结 `expected_revision`；开关更新冻结 `expected_enabled`。revision 使用严格非负整数，布尔、浮点和字符串均不能被隐式转换。

### 2.3 响应类型

- [x] `memory-state` 冻结启用、关闭和不可用三种能力矩阵；
- [x] `memory-list` 冻结完整列表、最大 100 条和确定性排序；
- [x] `memory-management-result` 冻结无正文的成功、拒绝和失败结果；
- [x] state/list 成功必须使用专用响应；
- [x] create/update/delete 成功必须返回 `memory_id`；
- [x] set_enabled 成功必须返回最终 `enabled`；
- [x] 非成功结果必须返回稳定原因码且 `changed=false`。

### 2.4 数据与隐私

- [x] 客户端协议不能指定 `profile_id`、`conf_uid`、存储目录、来源、内部 key 或路径；
- [x] UI 写入来源由后端固定为 `manual_ui`，不进入请求模型；
- [x] 条目公开投影只包含 ID、类别、值、来源和时间；
- [x] 时间必须使用 UTC，并序列化为带 `Z` 的 ISO 8601 字符串；
- [x] 普通管理结果不能携带记忆值或请求正文；
- [x] 只有成功的 `memory-list` 响应能够包含记忆正文；
- [x] 协议值拒绝纯空白、控制字符和超过 500 字符的内容。

### 2.5 冻结语义

- [x] 关闭状态允许 list/delete/clear，拒绝 create/update；
- [x] 存储不可用时隐藏 item count 和 revision；
- [x] UI 编辑和删除按稳定 `memory_id`，不使用正文匹配；
- [x] UI 清空只接受严格 JSON 布尔值 `true`；
- [x] v1 不设计分页；
- [x] 列表按称呼、偏好、重要事实、更新时间降序和 ID 升序排序；
- [x] 管理 revision 冲突不自动重试的规则已写入正式协议；
- [x] 本机访问、代理/群聊拒绝和日志脱敏边界已在协议中冻结。

## 3. 自动化证据

新增协议模型定向测试：

```text
49 passed
```

完整项目测试范围 `tests/`：

```text
379 passed, 8 subtests passed
```

相对 M5.4.1 冻结的 `330 passed, 8 subtests passed`，新增 49 项均来自管理协议模型测试。

唯一警告仍为 FFmpeg/avconv 未出现在 `PATH`。该警告与纯 JSON/Pydantic 管理协议无关，也不影响当前 Sherpa WAV 路径。

质量门禁：

- [x] 新增和修改的 Python 文件 `ruff check` 通过；
- [x] 新增和修改的 Python 文件 `ruff format --check` 通过；
- [x] `git diff --check` 通过；
- [x] 完整项目 `tests/` 回归通过；
- [x] 没有产生 `memory_data/`、测试音频、运行日志或私有配置。

说明：直接运行无路径限定的 `pytest` 会递归收集仓库 `models/` 和 `tmp/uv-cache/` 中的第三方包测试，因此项目完整回归使用明确的 `pytest tests` 范围。这一收集行为与本次协议代码无关。

## 4. 退出结论

M5.4.2 记忆管理 WebSocket 协议 v1 已形成文档和可执行类型契约。七类请求、三类响应、能力矩阵、稳定原因码、revision、严格类型、列表投影和隐私边界均由自动化测试保护。

M5.4.2.1 正式完成。下一步可以进入 M5.4.2.2“严格管理 CRUD”，在不修改 v1 协议的前提下实现按 ID 增改删及严格 revision 业务操作。
