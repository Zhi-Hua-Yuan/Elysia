# Elysia M5.4.2.5.4 自动化与重启测试验收

更新日期：2026-08-14

当前状态：**实现与自动化验收通过**。

## 1. 验收范围

本步骤在 M5.4.2.5.1 原子配置写入器、M5.4.2.5.2 运行状态 Coordinator 和
M5.4.2.5.3 对象装配基础上，补齐真正的进程边界恢复测试。

M5.3 已经覆盖同一 Python 解释器内重新创建 Writer、Coordinator、`WebSocketServer` 和
会话对象。本步骤不重复这些断言，而是由 pytest 父进程多次启动全新的 Python 解释器，
验证前一进程退出后，后一进程只能通过临时项目目录中的 `conf.yaml` 和记忆文档恢复状态。

固定边界如下：

- 子进程使用正式配置模型、`ServiceContext`、`WebSocketServer`、WebSocket v1 管理路由、
  Coordinator、原子写入器和持久记忆服务；
- ASR、TTS、Agent、Live2D 等外部对象使用无副作用替身；
- 所有配置和记忆文件均位于 pytest `tmp_path`，不读取或修改项目正式 `conf.yaml`、
  `memory_data` 和声音资产；
- 不启动 Uvicorn 网络端口，不加载实时语音和最终 Live2D；
- 不声明跨进程并发写入、文件锁或多实例广播保证。

## 2. 新增自动化设施

### 2.1 独立解释器 Worker

新增 `tests/_support/memory_setting_restart_worker.py`，提供三个内容受限的测试动作：

- `toggle`：通过真实 WebSocket v1 路由执行带预期状态的开关切换；
- `seed`：通过真实管理路由写入一条临时测试记忆；
- `inspect`：重新装配服务、新建会话并检查状态、管理能力、列表和对话上下文。

父进程使用 `sys.executable`、参数数组、`shell=False`、标准输出捕获和 30 秒单进程超时启动
Worker。Worker 只输出精简 JSON 状态，不输出配置正文、记忆正文、文件路径或异常详情；失败
只返回异常类型。测试显式提供项目 `src` 与根目录导入路径，确保子解释器不依赖父进程的
模块缓存。

### 2.2 自动化用例

新增 `tests/test_memory_setting_restart.py`，共 6 个 pytest 用例（其中异常配置用例包含两个
参数场景）。

## 3. 验收结果

### 3.1 跨解释器开关往返

执行以下完整序列：

```text
临时配置 false
→ 进程 A 切换为 true 并退出
→ 进程 B 启动、新建会话并恢复 true
→ 进程 C 切换为 false 并退出
→ 进程 D 启动、新建会话并恢复 false
```

- [x] 两次切换均返回 `success` 和 `changed=true`；
- [x] 磁盘、Coordinator、默认上下文和新会话状态一致；
- [x] 关闭后新会话的 `create` 能力为 false；
- [x] 没有原子写入临时文件残留。

### 3.2 只读启动与遗留临时文件

- [x] 只执行启动、装配、状态查询和空列表查询时，`conf.yaml` 字节内容不变；
- [x] `conf.yaml` 的纳秒修改时间不变；
- [x] 启动不会创建空记忆文档；
- [x] 模拟崩溃遗留的 `.conf.yaml.*.tmp` 不会被当作正式配置读取；
- [x] 本步骤保持现有边界，不擅自删除不属于当前 Writer 的遗留文件。

### 3.3 记忆数据与开关语义

- [x] 开启状态下通过管理路由写入一条临时测试记忆；
- [x] 关闭并重启后，管理列表仍可看到原条目；
- [x] 关闭后 `delete` 和 `clear` 能力仍可用；
- [x] 关闭后新增请求实际返回 `memory_disabled`，且不写入数据；
- [x] 关闭后普通对话上下文为空；
- [x] 重新开启并再次重启后，原记忆重新进入普通对话上下文；
- [x] 开关切换及各次检查前后，记忆文档字节和 revision 保持不变。

### 3.4 环境变量占位符与秘密保护

- [x] 子进程环境中提供测试密钥后，`${ELYSIA_LLM_API_KEY}` 仍以占位符写回；
- [x] 展开后的测试密钥没有进入 `conf.yaml`；
- [x] 测试密钥没有进入 Worker 的 stdout 或 stderr；
- [x] 开关写入后又由全新解释器成功恢复。

### 3.5 异常配置失败关闭

- [x] `conf.yaml` 缺失时，Worker 以非零状态和 `FileNotFoundError` 类型结束；
- [x] YAML 损坏时，Worker 以非零状态和 `ParserError` 类型结束；
- [x] 损坏配置正文不出现在子进程输出；
- [x] 原损坏文件字节不变；
- [x] 失败过程不创建正式配置或临时写入文件。

原子替换前后的 `fsync`、序列化、验证、替换和清理失败已经由 M5.4.2.5.1 的故障注入
测试覆盖。本步骤没有增加依赖精确进程终止时序的强杀测试，以避免平台相关和偶发失败。

## 4. 自动化证据

新增进程边界用例：

```text
6 passed
```

持久化、Coordinator、组合根、会话生命周期、M5.3 集成验收和本次重启测试定向回归：

```text
56 passed
```

完整项目测试范围 `tests/`：

```text
524 passed, 8 subtests passed
```

所有 pytest 运行只有同一个既有警告：FFmpeg/avconv 未出现在测试进程的 `PATH`。该警告
与开关持久化、重启恢复和记忆管理无关。

质量检查：

- [x] `ruff check src tests` 通过；
- [x] 三个新增 Python 文件的 `ruff format --check` 通过；
- [x] `git diff --check` 通过；
- [x] 未修改生产代码、冻结协议、前端或正式配置；
- [x] 未生成或提交真实记忆、日志、测试音频、临时配置或凭据。

测试命令需要按仓库 `src` 布局提供导入路径：

```powershell
$env:PYTHONPATH='src;.'
.\.venv\Scripts\python.exe -m pytest tests\test_memory_setting_restart.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_memory_setting_persistence.py tests\test_memory_setting_coordinator.py tests\test_memory_setting_composition_root.py tests\test_memory_setting_session_lifecycle.py tests\test_memory_setting_integration_acceptance.py tests\test_memory_setting_restart.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\ruff.exe format --check tests\test_memory_setting_restart.py tests\_support\memory_setting_restart_worker.py tests\_support\__init__.py
git diff --check
```

## 5. 已知限制

- Coordinator 仍然是单服务进程的协调边界；本步骤只验证顺序重启，不验证两个进程同时
  修改同一个 `conf.yaml`；
- 没有启动真实网络端口和管理页面，真实 UI 重启冒烟留在 M5.4 用户侧正式验收；
- 没有模拟操作系统在 `os.replace` 的精确时刻强制终止进程；对应磁盘安全性质由原子写入器
  的确定性故障注入测试保证；
- 遗留临时文件被安全忽略但不会在启动时自动清理，避免扩大本步骤的生产行为范围。

## 6. 退出结论

M5.4.2.5.4 已完成。开关状态现在不仅在单进程运行期保持一致，也已经证明能够跨全新
Python 解释器可靠恢复。关闭记忆不会删除现有数据，管理能力和对话注入语义在重启后保持
冻结契约；配置异常、环境变量占位符和敏感输出边界均有自动化证据。

M5.4.2.5“原子写入—运行协调—对象装配—进程重启恢复”链路至此闭环，可进入后续管理
界面接入或 M5.4 正式用户侧验收。
