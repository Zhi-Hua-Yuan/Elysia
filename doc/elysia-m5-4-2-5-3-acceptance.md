# Elysia M5.4.2.5.3 对象装配与集成验收

更新日期：2026-08-14

当前状态：**实现与自动化验收通过**。

## 1. 固定范围

本步骤完成 M5.4.2.5.1 原子配置写入器和 M5.4.2.5.2 运行状态 Coordinator
在应用生命周期中的正式装配，并对完整后端链路执行阶段级集成验收：

```text
WebSocket v1 请求
→ WebSocketHandler
→ ServiceContext
→ MemoryManagementController
→ MemorySettingRuntimeCoordinator
→ AtomicMemoryConfigWriter
→ conf.yaml
→ 默认上下文与在线会话同步
→ WebSocket v1 响应
```

固定边界如下：

- 一个服务进程只持有一个 Writer 和一个 Coordinator；
- 默认 `ServiceContext` 与所有在线会话共用同一个 Coordinator；
- 新会话在进入 Handler 映射前完成端口绑定和目标注册；
- 正常断开、连接失败和取消路径执行显式注销与资源关闭；
- 开关切换只修改 `memory_config.enabled` 并清除相关瞬态状态；
- 不重建 Memory Service、Agent、ASR、TTS、VAD、翻译或 Live2D；
- 不增加状态广播，不修改冻结的 WebSocket 管理协议 v1；
- 管理界面和真实语音链路不属于本步骤。

## 2. 对象装配结果

### 2.1 ServiceContext 注入入口

- [x] `ServiceContext` 只依赖既有 `MemorySettingUpdatePort`；
- [x] 首次绑定时创建带有效端口的 `MemoryManagementController`；
- [x] 同一端口重复绑定保持身份幂等；
- [x] 拒绝 `None` 和不同端口的二次绑定；
- [x] 未装配端口时继续以 `config_persist_failure` 失败关闭。

### 2.2 服务级组合根

- [x] `WebSocketServer` 显式持有并向路由注入唯一 `WebSocketHandler`；
- [x] 在 Uvicorn 事件循环的 startup 阶段创建异步锁、Writer 和 Coordinator；
- [x] 初始开关值来自已验证并加载的默认配置；
- [x] 默认上下文先绑定、注册，再向 Handler 发布 Coordinator；
- [x] 初始化按对象身份幂等，不重复创建、注册或写盘；
- [x] 默认上下文未加载或开关值不是严格布尔值时拒绝装配。

### 2.3 会话注册与注销

- [x] 会话完成缓存加载后绑定进程级 Coordinator；
- [x] 会话注册成功后才写入客户端映射并发送初始消息；
- [x] 注册立即使新会话收敛到 Coordinator 当前权威状态；
- [x] 注册失败或任务取消时注销并关闭部分构造的上下文；
- [x] 正常断开和连接初始化失败复用统一释放流程；
- [x] 显式注销后，会话不再接收后续开关同步；
- [x] 会话关闭不关闭共享 Agent，拥有资源的默认上下文仍只关闭一次。

## 3. 阶段级集成验收

新增 `tests/test_memory_setting_integration_acceptance.py`，核心链路使用真实：

- `WebSocketServer`；
- `WebSocketHandler`；
- `ServiceContext`；
- `MemoryManagementController`；
- `MemorySettingRuntimeCoordinator`；
- `AtomicMemoryConfigWriter`；
- 配置模型、持久记忆服务和临时 `conf.yaml`。

只有 WebSocket 和 ASR/TTS/Agent/Live2D 等外部对象使用无副作用测试替身。所有配置写入
均发生在 pytest 的 `tmp_path` 中，不读取或修改项目正式 `conf.yaml`。

### 3.1 双会话完整开关闭环

- [x] 客户端 A 通过真实 WebSocket 路由执行 `false → true`；
- [x] 客户端 B 通过相同路由执行 `true → false`；
- [x] v1 响应的操作、状态、`changed` 和 `enabled` 正确；
- [x] 磁盘、Coordinator、默认上下文和两个会话保持一致；
- [x] 对端客户端随后查询可见最新状态；
- [x] 没有引入额外广播或临时配置文件残留；
- [x] 切换前后 Memory Service、Controller 和所有引擎身份保持不变。

### 3.2 新会话与断开会话

- [x] 切换成功后创建的新会话首次可见状态即为权威状态；
- [x] 新会话注册不触发第二次配置写入；
- [x] 新会话继续共享默认上下文的 Memory Service；
- [x] 断开的会话从 Handler 映射移除并显式注销；
- [x] 后续切换只同步默认上下文和仍在线会话；
- [x] 已断开会话不再被 Coordinator 修改。

### 3.3 并发 CAS

两个客户端同时以 `expected_enabled=false` 请求启用记忆：

- [x] 只有一个请求返回 `success` 和 `changed=true`；
- [x] 另一个请求返回 `setting_conflict`；
- [x] 两个请求不会同时声称完成了状态变化；
- [x] 最终磁盘和全部运行目标一致为 `true`；
- [x] 配置文件保持完整且没有临时文件残留。

### 3.4 外部冲突自愈

- [x] 测试模拟运行状态为 `true`、磁盘被外部改为 `false`；
- [x] 携带旧预期的请求返回冻结的 `setting_conflict`；
- [x] Coordinator 不覆盖外部磁盘状态；
- [x] 默认上下文和全部在线会话收敛到磁盘实际值；
- [x] 响应中的 `enabled` 与最终权威状态一致。

### 3.5 重启恢复

- [x] 第一服务实例将开关持久化为 `true`；
- [x] 第二服务实例使用全新的 Writer、Coordinator 和默认上下文；
- [x] 第二实例从同一测试配置恢复 `true`；
- [x] 重启后的新会话首次查询即返回 `enabled=true`；
- [x] 初始化读取不产生无意义重写。

## 4. 自动化证据

M5.4.2.5.3.4 新增集成验收：

```text
5 passed
```

开关持久化、Coordinator、ServiceContext、组合根、会话生命周期、WebSocket 和新增集成
验收定向回归：

```text
68 passed
```

M5 相关测试：

```text
470 passed, 48 deselected
```

完整项目测试范围 `tests/`：

```text
518 passed, 8 subtests passed
```

所有 pytest 运行只有同一个既有警告：FFmpeg/avconv 未出现在测试进程的 `PATH`。该警告
与本次后端对象装配和开关持久化无关。

质量检查：

- [x] 全部 `src` 和 `tests` 的 `ruff check` 通过；
- [x] 本次新增测试文件的 `ruff format --check` 通过；
- [x] `git diff --check` 通过；
- [x] 未修改生产代码、冻结协议、前端或正式配置；
- [x] 未生成或提交记忆正文、日志、测试音频、临时配置或凭据。

全树 `ruff format --check src tests` 仍报告 14 个既有文件需要使用当前 Ruff 版本重新格式
化，其中包括早先提交的 `server.py`、`routes.py` 和组合根测试。本次没有批量重写这些
既有文件，以避免在集成验收提交中混入无关格式差异；新增文件自身已经格式化通过。

## 5. 未执行项与已知限制

- 本步骤没有执行真实进程双客户端人工冒烟；自动化测试已经覆盖同一后端协议闭环，管理
  页面完成后可在 M5.4 正式验收中统一执行真实 UI 冒烟；
- Coordinator 仍是单进程协调边界，不提供跨进程锁或跨实例广播；
- 其他客户端不会被主动广播开关变化，需要在下一次状态查询时刷新；
- 在途对话不会因开关变化被强制取消，下一次记忆访问保证观察到新状态；
- 全树既有 Ruff 格式债务不由本步骤扩散处理。

## 6. 退出结论

M5.4.2.5.3 已完成对象装配和阶段级集成验收。真实 WebSocket v1 开关请求现在可以经过
ServiceContext、管理 Controller、进程级 Coordinator 和原子写入器完成持久化，并使默认
上下文及全部在线会话向同一权威状态收敛。新会话、断开会话、并发 CAS、外部冲突和服务
重启均已由自动化测试覆盖，且没有触发 Memory Service 或实时语音相关对象重建。

下一步可以进入 M5.4 后续管理界面接入与正式用户侧验收。
