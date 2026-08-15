# Elysia M5.4.2.6 管理契约和后端接口阶段验收

- 更新日期：2026-08-15
- 当前状态：**验收通过；M5.4.2 已关闭**
- 冻结基线：`e984492`（M5.3 显式记忆命令与阶段验收）
- 质量修复提交：`6508e3b`

## 1. 验收目的

本记录汇总 M5.4.2.1—M5.4.2.5.4 的交付结果，并对 M5.4.2“管理契约和后端接口”执行最终阶段验收。

本阶段只建立供后续管理界面使用的后端管理平面，不实现 React 管理界面，不扩展自然语言记忆命令，不启用隐式记忆抽取，也不改变实时语音、TTS、Live2D 或角色资产路线。

## 2. 验收范围

本次验收覆盖：

- 记忆管理 WebSocket 协议 v1；
- 严格请求和响应模型；
- 基于 `memory_id` 与 `expected_revision` 的管理 CRUD；
- 本机回环连接、代理模式、群聊和 Agent 类型访问边界；
- 管理请求的 WebSocket 路由与脱敏错误响应；
- `memory_config.enabled` 原子持久化；
- 单进程运行状态 Coordinator；
- 默认上下文和活动会话的注册、同步与注销；
- 关闭、重启、重新开启后的状态和记忆恢复；
- 自动化回归、格式、静态质量、隐私和产物门禁。

以下内容不属于 M5.4.2：

- 前端记忆管理抽屉或设置页面；
- 前端 TypeScript 类型、状态管理和交互；
- 真实 UI 与后端的端到端人工验收；
- 远程管理、代理模式管理和群聊管理；
- 多进程同时写入同一配置或记忆目录；
- 云同步、认证、多用户账号和加密存储。

## 3. 分阶段交付结果

### 3.1 M5.4.2.1：管理协议 v1

- [x] 冻结状态、列表、添加、编辑、删除、清空和开关七类请求；
- [x] 所有请求携带 UUID v4 `request_id` 和固定 `protocol_version`；
- [x] 请求模型禁止未知字段、错误类型和客户端指定作用域；
- [x] 状态、列表和操作结果使用独立响应模型；
- [x] 操作结果不携带记忆正文、路径、配置正文或异常详情；
- [x] 关闭状态允许列表、删除和清空，但禁止添加和编辑；
- [x] 协议文档已完成阶段范围 `diff --check` 清理。

详细契约见 [`elysia-m5-4-2-management-protocol.md`](elysia-m5-4-2-management-protocol.md)。

### 3.2 M5.4.2.2：严格管理 CRUD

- [x] 管理添加使用 `manual_ui` 来源并重新执行内容策略；
- [x] 编辑按 `memory_id` 定位，保留原 ID 和创建时间；
- [x] 删除按 `memory_id` 定位，不复用自然语言正文匹配；
- [x] 所有管理修改严格校验 `expected_revision`，冲突时不自动重试；
- [x] 编辑后的重复、敏感内容、非法值和容量边界均安全拒绝；
- [x] 删除与清空继续使用 replacement 备份，已移除正文不能从备份恢复；
- [x] 幂等操作不增加 revision，不产生多余文件写入。

### 3.3 M5.4.2.3：管理控制器

- [x] 管理平面与显式语音命令控制器分离；
- [x] 作用域只从服务端 `profile_id + character conf_uid` 推导；
- [x] 非回环连接、代理模式、活动群聊和非 BasicMemoryAgent 请求均拒绝；
- [x] 状态响应区分 enabled、available、容量、revision 和能力集合；
- [x] 存储不可用时失败关闭，不返回条目数量、revision 或正文；
- [x] 管理修改会清除当前连接中旧的语音清空确认；
- [x] 管理操作不进入 Agent、聊天历史、字幕、TTS 或表情链路。

### 3.4 M5.4.2.4：WebSocket 接入

- [x] 七类请求已注册到现有 WebSocket 路由；
- [x] 回环地址使用数值 IPv4/IPv6 地址判断，不使用字符串前缀猜测；
- [x] 非法负载只返回一个终态、无正文的错误响应；
- [x] 响应序列化前再次经过冻结协议模型校验；
- [x] 日志只记录消息类型、原因码和异常类型；
- [x] 请求正文、记忆值和异常详情不进入日志；
- [x] 请求处理不创建对话任务，不改变 `history_uid`。

### 3.5 M5.4.2.5：开关持久化与运行协调

- [x] 原子配置写入器只修改 `memory_config.enabled` 的语义值；
- [x] 写入前后执行配置解析和验证；
- [x] 临时文件位于同目录，关闭句柄后执行原子替换；
- [x] 失败时保留原配置和原运行状态并清理当前临时文件；
- [x] 环境变量占位符不会被展开后的密钥替换写回配置；
- [x] Coordinator 串行处理开关转换并校验预期旧状态；
- [x] 默认上下文和活动会话共享同一运行状态；
- [x] 新会话注册、断开注销和失败连接清理均有覆盖；
- [x] 开关状态可以跨全新 Python 解释器恢复；
- [x] 关闭不会删除记忆，重新开启并重启后原记忆恢复注入。

## 4. 独立自动化验收

验收环境按仓库 `src` 布局设置：

```powershell
$env:PYTHONPATH='src;.'
```

### 4.1 管理协议专项

执行范围：

```text
tests/test_memory_management_types.py
tests/test_memory_management_service.py
tests/test_memory_management_controller.py
tests/test_memory_management_transport.py
tests/test_service_context_memory_management.py
tests/test_websocket_memory_management.py
```

结果：

```text
138 passed
```

### 4.2 持久化与重启组合

执行范围：

```text
tests/test_memory_setting_persistence.py
tests/test_memory_setting_coordinator.py
tests/test_memory_setting_composition_root.py
tests/test_memory_setting_session_lifecycle.py
tests/test_memory_setting_integration_acceptance.py
tests/test_memory_setting_restart.py
```

结果：

```text
56 passed
```

### 4.3 项目完整回归

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
524 passed, 8 subtests passed
```

全部 pytest 运行只有同一个既有警告：FFmpeg/avconv 未出现在测试进程的 `PATH`。该警告不影响当前 Sherpa WAV 路径，也与记忆管理协议、JSON 存储、配置持久化和重启恢复无关。

## 5. 静态质量与变更门禁

### 5.1 Ruff lint

```powershell
.\.venv\Scripts\ruff.exe check src tests
```

结果：

```text
All checks passed!
```

### 5.2 阶段 Python 格式

以 M5.3 冻结提交 `e984492` 为基线，检查本阶段改动的全部 Python 文件：

```powershell
$changed = git diff --name-only e984492..HEAD -- '*.py'
.\.venv\Scripts\ruff.exe format --check $changed
```

结果：

```text
29 files already formatted
```

全仓另有 11 个未被 M5.4.2 修改的历史 Python 文件不符合 Ruff format。为避免把无关机械改写混入本阶段，本次不格式化这些文件；它们不影响 Ruff lint、pytest 或 M5.4.2 阶段范围格式门禁。

### 5.3 基线范围空白检查

```powershell
git diff --check e984492..HEAD
```

结果：通过，无行尾空白或补丁格式错误。

质量修复提交 `6508e3b` 仅调整协议文档元数据列表和一个测试函数的 Ruff 换行，没有修改生产行为。

### 5.4 CI 工具版本

提交 `577d49b` 将 GitHub Actions 中的 Ruff 固定为 `0.9.1`，与当前 `uv.lock` 和本地验收版本一致，避免 CI 自动升级到不同 Ruff 版本后产生不稳定门禁结果。

## 6. 安全、隐私与产物检查

- [x] 前端协议不能提交或覆盖 `profile_id`、`conf_uid` 和文件路径；
- [x] 非本机连接无法读取状态、数量、revision 或记忆正文；
- [x] 管理列表只返回 UI 所需字段，不返回内部 key 和存储路径；
- [x] 错误响应不包含请求正文、记忆值、配置正文或异常消息；
- [x] 日志脱敏覆盖解析失败、控制器异常、存储失败和配置失败；
- [x] 配置写回不会把展开后的 API Key 写入 `conf.yaml`；
- [x] 删除和清空后的备份不保留已删除正文；
- [x] 测试使用 pytest 临时目录，不读取或修改正式记忆数据；
- [x] 验收后工作区未出现 `memory_data/`、测试音频、运行日志、临时配置或凭据。

## 7. 已知限制

- M5.4.2 只交付后端管理协议，真实管理界面将在 M5.4.3 实现；
- 管理接口只允许本机回环连接，不支持远程浏览器管理；
- 代理模式、活动群聊和非 BasicMemoryAgent 保持关闭；
- Coordinator 只协调单个服务进程，不提供多进程同时修改同一配置的锁；
- 原子写入故障通过确定性故障注入验证，没有依赖平台时序执行进程强杀测试；
- 启动时会安全忽略遗留临时文件，但不会主动删除不属于当前 Writer 的文件；
- 本阶段没有修改第一阶段的 Live2D、TTS、ASR、Persona 和声音资产边界。

## 8. 退出结论

M5.4.2 管理契约和后端接口已经完成协议、严格 CRUD、权限控制、WebSocket 接入、原子配置写入、运行协调、会话生命周期和跨进程重启恢复的闭环。

独立验收确认：管理专项、持久化组合和项目完整回归全部通过；阶段 Python 格式、Ruff lint 和基线范围空白检查通过；未发现记忆正文、配置秘密、路径或测试产物泄漏。

因此正式结论为：

> **M5.4.2 验收通过并关闭，可以进入 M5.4.3 前端源代码实现。**
