# Elysia M5.4.2.5.2 运行状态 Coordinator 验收

更新日期：2026-08-14

当前状态：**实现与自动化验收通过**。

## 1. 固定范围

本步骤在 M5.4.2.5.1 原子配置写入器之上，实现记忆总开关的进程内运行状态编排：

- 由唯一 Coordinator 串行化写盘、运行时同步和失败补偿；
- 将 `AtomicMemoryConfigWriter` 适配为 `MemorySettingUpdatePort`；
- 同步当前已注册的 `ServiceContext` 开关状态；
- 清除 M5.3 待确认状态，但不取消正在执行的对话或记忆命令；
- 在磁盘提交成功、运行时同步失败时执行反向 compare-and-set 补偿；
- 在冲突或幂等写入后，以磁盘返回状态修复运行时漂移。

本步骤不创建全局 Coordinator 实例，不向 `MemoryManagementController` 注入端口，不修改
WebSocket 新建连接、断开连接或服务关闭流程。上述正式生命周期接入属于
M5.4.2.5.3。

## 2. 实现结果

### 2.1 Coordinator 与端口契约

- [x] 新增 `MemorySettingRuntimeCoordinator`；
- [x] Coordinator 结构化实现既有 `MemorySettingUpdatePort`；
- [x] 新增窄化的 `MemorySettingPersistencePort`，只允许执行开关 CAS 写入；
- [x] 新增窄化的 `MemorySettingRuntimeTarget`，只允许应用开关并清理临时状态；
- [x] 构造参数和更新参数只接受严格布尔值；
- [x] Coordinator 使用单个 `asyncio.Lock` 覆盖写盘、同步、注册、注销和补偿；
- [x] Coordinator 自身保存最新权威运行状态，供后续新连接注册时立即收敛。

### 2.2 运行时目标管理

- [x] 目标按注册顺序同步，为后续“默认上下文优先”提供确定性；
- [x] 相同对象按身份去重，不因重复注册而重复保留；
- [x] 注册和更新使用同一把锁，不存在“先克隆旧状态、后错过切换”的竞态窗口；
- [x] 新目标注册时立即应用 Coordinator 当前状态并清理过期待确认；
- [x] 注销后不再接收后续同步；
- [x] 注册表只保存弱引用，断开的会话不会被 Coordinator 长期持有；
- [x] 同步某个目标失败不会阻止其余目标执行同步和临时状态清理。

### 2.3 `ServiceContext` 窄运行时接口

- [x] `apply_memory_enabled_state()` 只修改
  `config.memory_config.enabled`；
- [x] `clear_memory_setting_transients()` 只清除 M5.3 待确认状态；
- [x] 不清除 `active_memory_command_turn_id`，不主动取消在途对话；
- [x] 不调用 `load_from_config()` 或 `_init_memory_service()`；
- [x] 不重建或替换 memory service、Agent、TTS、ASR、VAD、翻译、MCP 或 Live2D；
- [x] 缺失记忆配置时失败关闭，不伪造运行状态。

### 2.4 状态转换语义

| 磁盘结果 | 运行时动作 | 对外结果 |
| --- | --- | --- |
| 成功且有变化 | 同步所有目标到磁盘状态 | `success`，`changed=true` |
| 幂等成功 | 仍同步所有目标，修复漂移 | `success`，`changed=false` |
| `setting_conflict` | 同步所有目标到磁盘实际状态 | `rejected`，`changed=false` |
| 写盘失败 | 不应用请求目标状态 | `failed`，保持或报告已知状态 |
| 磁盘状态未知 | 使用 Coordinator 当前状态响应 | `failed`，不猜测磁盘状态 |
| 运行时同步失败 | 执行补偿流程 | `failed/internal_error`，`changed=false` |

### 2.5 运行时失败补偿

当目标配置已经提交到磁盘，但任一运行时目标同步失败时：

1. 以切换前状态对所有目标执行一次尽力回退；
2. 调用原子写入器执行从已提交状态到切换前状态的反向 CAS；
3. 根据反向写入返回的实际磁盘状态重新确定权威状态；
4. 再次使全部存活目标向该权威状态收敛；
5. 无论补偿是否完整成功，本次原请求均返回
   `failed/internal_error` 和 `changed=false`；
6. 如果反向写入失败或被外部改写冲突，不会虚报“已恢复”，响应中的 `enabled`
   使用当前能够确认的最终磁盘状态。

所有失败日志只记录阶段、异常类型和布尔型补偿结论，不记录配置正文、路径、记忆内容或
异常正文。

## 3. 自动化证据

新增 Coordinator 专项测试：

```text
17 passed
```

覆盖范围包括：双向切换、多目标同步、注册顺序、对象去重、注销、弱引用回收、严格布尔
入参、并发 CAS 串行化、幂等漂移修复、冲突自愈、写盘失败、未知磁盘状态、运行时失败
补偿、补偿失败后的最终状态报告、日志脱敏、在途命令标记保留、服务与引擎身份保持，
以及真实原子写入器与 `ServiceContext` 的组合验证。

Coordinator、原子写入、管理控制器和服务上下文定向回归：

```text
56 passed
```

完整项目测试范围 `tests/`：

```text
494 passed, 8 subtests passed
```

相对 M5.4.2.5.1 的 `477 passed, 8 subtests passed`，新增 17 项。唯一警告仍为
FFmpeg/avconv 未出现在测试进程的 `PATH`，与本次运行状态同步无关。

质量门禁：

- [x] 修改的 Python 文件 `ruff check` 通过；
- [x] 修改的 Python 文件 `ruff format --check` 通过；
- [x] `git diff --check` 通过；
- [x] 完整项目 `tests/` 回归通过；
- [x] 未改变冻结的 WebSocket v1 管理协议；
- [x] 未接入 WebSocket 生命周期或前端代码；
- [x] 未触发 Agent、语音、历史记录或记忆正文副作用。

## 4. 已知限制

- Coordinator 目前只是可复用组件，尚未由应用启动流程创建和持有；
- 默认上下文和会话上下文尚未在连接生命周期中正式注册、注销；
- 管理控制器尚未注入 Coordinator，因此正式 WebSocket 开关请求仍未启用真实持久化；
- 在途对话不会被强制取消，切换前已开始的操作允许自然结束；后续请求和下一轮上下文
  才保证观察到新状态；
- 若某个运行时目标持续抛出异常，Coordinator 会返回 `internal_error` 并保留可确认的
  权威状态，但无法保证该损坏目标已经收敛；
- 跨进程文件锁仍不在 M5 范围内。

## 5. 退出结论

M5.4.2.5.2 已完成运行状态 Coordinator。磁盘 CAS、进程内状态同步、目标注册表和失败
补偿现在由单一串行化边界统一编排；幂等和冲突可修复运行时漂移，写盘失败不会误应用
请求状态，磁盘提交后的运行时失败也具备反向 CAS 和最终状态收敛语义。

下一步可进入 M5.4.2.5.3：在应用级 WebSocket 生命周期中创建唯一 Coordinator，先注册
默认 `ServiceContext`，再为会话上下文执行注册与注销，并将同一 Coordinator 注入所有
`MemoryManagementController`。该接入步骤应继续保持本次已冻结的窄接口和补偿语义。
