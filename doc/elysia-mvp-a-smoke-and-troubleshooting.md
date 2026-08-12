# Elysia MVP-A 冒烟测试与故障排查

更新日期：2026-08-11

> 本文保留 M2 阶段以 Sherpa-ONNX 为默认 TTS 的历史冒烟基线。M3.3 之后的
> Fish 默认软件 RC 请按
> [`elysia-software-rc-runbook.md`](elysia-software-rc-runbook.md) 验收和排查。

状态：M2.4 人工短验收已通过。验收记录见
[`elysia-m2-4-acceptance.md`](elysia-m2-4-acceptance.md)。

## 目标

这份清单用于每次影响配置、ASR、LLM、TTS、WebSocket、Live2D 或 Electron
的变更之后，在 5–10 分钟内确认 MVP-A 主链路没有明显回退。它不替代 M0
性能验收，也不要求每次重新完成 10 轮对话。

如果任一关键门禁失败，应停止后续步骤，先按本文对应症状定位。不要在同一
次排查中同时更换模型、配置和前端版本。

## 一、快速冒烟清单

### A. 启动前门禁

在项目根目录和持有 LLM 环境变量的 PowerShell 中执行：

```powershell
[bool]$env:ELYSIA_LLM_BASE_URL
[bool]$env:ELYSIA_LLM_MODEL
[bool]$env:ELYSIA_LLM_API_KEY
Test-Path .\conf.yaml
Test-Path .\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17\model.int8.onnx
Test-Path .\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17\tokens.txt
Test-Path .\models\vits-melo-tts-zh_en\model.onnx
Test-Path .\models\vits-melo-tts-zh_en\tokens.txt
```

八项都应返回 `True`。这些命令只检查是否存在，不显示环境变量或配置内容。

如果后端尚未启动，可以检查端口是否已被其他进程占用：

```powershell
Get-NetTCPConnection -LocalPort 12393 -State Listen -ErrorAction SilentlyContinue
```

没有输出表示端口空闲。有输出时先确认是否已经存在一个项目后端，不要直接
结束未知进程。

### B. 后端启动门禁

```powershell
uv run --frozen run_server.py
```

必须看到以下阶段：

```text
Initializing Live2D: mao_pro
Initializing ASR: sherpa_onnx_asr
Initializing TTS: sherpa_onnx_tts
Initializing Agent: basic_memory_agent
Server context initialized successfully.
Starting server on localhost:12393
```

出现 `Server context initialized successfully.` 才算通过。不要只看到
`Starting server` 就认定模型全部可用。

### C. Electron 连接门禁

1. 启动 M2.1 冻结的 Electron 1.2.1。
2. 确认界面显示“已连接”。
3. 确认当前角色为“爱莉希雅 MVP”。
4. 确认 Live2D 模型已经显示，而不是空白画布。

### D. 三轮功能检查

#### 第 1 轮：文字链路

输入：

```text
你好，请用一句话回应我。
```

通过标准：出现角色字幕，能够播放语音，Live2D 嘴型随声音运动。该轮主要
隔离验证 LLM、TTS、WebSocket、音频播放和 Live2D，不依赖麦克风或 ASR。

#### 第 2 轮：语音链路

打开麦克风并说：

```text
请告诉我今天适合做什么。
```

通过标准：VAD 自动收句，出现合理中文转写，随后正常回答、播放语音并产生
嘴型。该轮验证麦克风、前端 VAD 和 ASR。

#### 第 3 轮：表情和打断

说：

```text
请开心地说一段稍微长一点的话。
```

通过标准：面部出现可见表情变化；播放期间说“等一下”，当前语音停止，旧
回复的后续分句不再继续播放，随后可以开始下一轮对话。

### E. 退出门禁

在后端 PowerShell 中按 `Ctrl+C`。后端应正常退出；下次启动时不应因为上次
残留进程出现 12393 端口占用。

## 二、冒烟结果记录模板

每次只记录结果和必要的非敏感错误类型：

```text
日期：
后端提交：
前端/Electron：1.2.1
启动门禁：通过 / 失败
Electron 连接：通过 / 失败
文字链路：通过 / 失败
语音链路：通过 / 失败
嘴型与表情：通过 / 失败
播放中打断：通过 / 失败
总结果：通过 / 失败
备注：
```

不要记录 API Key、完整 Endpoint、完整 Prompt、完整 `conf.yaml` 或私人对话。

## 三、故障排查

### 1. `uv` 无法识别或无法运行

先执行：

```powershell
Get-Command uv
uv --version
```

- 找不到命令：确认 uv 已安装，并在安装后重新打开 PowerShell。
- 只能在某个旧窗口运行：通常是 `PATH` 尚未在新进程中刷新。
- 不要改用随机的全局 Python 或临时 `pip install`；这会绕过 `uv.lock`。

### 2. 找不到 `conf.yaml` 或配置校验失败

- 确认命令在仓库根目录执行。
- 按 [`elysia-mvp-a-setup.md`](elysia-mvp-a-setup.md) 从无密钥模板创建配置。
- 已有配置时不要直接覆盖；先备份到被忽略的 `private/`。
- YAML 缩进错误、字段名错误或残留未解析占位符都可能阻止初始化。

### 3. LLM 环境变量失效

环境变量只属于设置它们的 PowerShell 会话。只检查是否存在：

```powershell
[bool]$env:ELYSIA_LLM_BASE_URL
[bool]$env:ELYSIA_LLM_MODEL
[bool]$env:ELYSIA_LLM_API_KEY
```

如果任一项为 `False`，在同一窗口重新设置，然后从该窗口启动后端。不要用
`Write-Output` 或截图展示真实值。

### 4. 后端初始化失败

首先查看 `Failed to initialize server context` 前最后一个初始化阶段：

| 最后阶段 | 优先检查 |
|---|---|
| `Initializing Live2D` | `model_dict.json`、模型名、Live2D 文件 |
| `Initializing ASR` | SenseVoice ONNX、tokens、目录权限 |
| `Initializing TTS` | VITS model、lexicon、tokens、dict、FST、`sid` |
| `Initializing Agent` | LLM provider 配置、模型名和环境变量 |

SenseVoice 默认相对路径缺失时，上游会尝试从网络下载。MVP-A 稳定运行不应
依赖每次启动自动下载；应先确认本地文件完整。

### 5. `WinError 10048` 或端口 12393 被占用

这表示已有进程监听同一端口，并不代表 ASR、LLM 或 TTS 初始化失败。

```powershell
Get-NetTCPConnection -LocalPort 12393 -State Listen | Select-Object OwningProcess
Get-Process -Id <上一步的进程号>
```

如果是之前启动的 Open-LLM-VTuber，在原 PowerShell 中按 `Ctrl+C` 结束。
如果属于其他程序，先确认用途；不要直接结束未知进程。

### 6. Electron 显示未连接

- 确认后端仍在运行，并已经输出 `Starting server on localhost:12393`。
- 在浏览器访问 `http://localhost:12393`，区分后端不可达和 Electron 问题。
- 确认 Electron 使用默认本机地址和端口，而不是旧的远程地址。
- 关闭重复 Electron 窗口后重新连接；不要同时启动第二个后端。

### 7. 文字输入也没有回复或一直停在 `Thinking...`

- 先查看界面或日志是否出现 `Error calling the chat endpoint`。
- `Connection error`：检查 Endpoint 可达性和当前网络。
- `Rate limit exceeded`：等待服务端限流恢复，不要修改本地 ASR/TTS。
- 其他 API 错误：核对模型名、API Key 权限和服务的 OpenAI 兼容程度。
- 先用文字输入定位 LLM；文字链路失败时，继续调整麦克风没有意义。

标准日志已经避免输出 Endpoint、Prompt 和 Key。只有必要时才开启 `--verbose`，
分享 DEBUG 日志前仍需人工复查敏感内容。

### 8. 麦克风有输入但没有中文转写

- 先确认 Electron 获得 Windows 麦克风权限并选中了正确设备。
- 观察前端是否进入监听状态以及 VAD 是否结束本轮录音。
- 文字输入正常而语音输入失败，问题通常位于麦克风、VAD 或 ASR。
- 查看后端是否使用 `sherpa_onnx_asr`，以及日志是否显示 CPU 推理。
- 不要在一次排查中同时切换 SenseVoice 和 Whisper。

### 9. 有字幕但没有声音

- 确认 Windows 输出设备和应用音量没有静音。
- 查看后端是否出现 `sherpa-onnx unable to generate audio` 或空音频错误。
- 检查 `vits-melo-tts-zh_en` 文件和 `sid: 0`。
- 文字字幕正常说明 LLM 已工作，应优先检查 TTS、音频 payload 和前端播放。
- pydub 的 FFmpeg `PATH` 警告不阻塞当前 Sherpa WAV 基线。

### 10. 有声音但嘴型不动

- 先确认当前模型是已验收的 `mao_pro`。
- 重新连接 Electron，排除前端音频状态未重置。
- 打开开发者工具，确认收到 `audio` payload 且 `volumes` 不是空数组。
- 有声音而 `volumes` 正常时，问题位于前端 Live2D 嘴型驱动，不要修改 TTS。

### 11. 表情不变化或标签出现在字幕中

- 使用映射到不同索引的标签测试：`neutral`、`sadness`、`anger`、`joy`。
- `joy`、`smirk`、`surprise` 在 `mao_pro` 中共享索引，不能用它们证明三种
  不同表情。
- 开发者工具中的 `actions.expressions` 应包含表情索引。
- 已识别标签不应出现在字幕或 TTS；如果重新出现，运行表情标签回归测试。

### 12. 播放中无法打断

- 确认麦克风在 AI 播放期间仍处于监听状态。
- 确认 M2.2 已验收的识别设置仍然保存，没有被旧前端状态覆盖。
- 说话时应触发前端 `interrupt-signal`，后端随后取消剩余 LLM/TTS 任务。
- 如果当前声音停止但稍后又播放旧句子，重点检查音频队列和任务取消。
- 如果完全没有识别到打断语音，先按“麦克风有输入但没有中文转写”排查。

### 13. 何时使用 verbose 日志

只有以下情况才使用：

```powershell
uv run --frozen run_server.py --verbose
```

- 标准日志无法确定失败阶段；
- 需要核对 WebSocket 消息顺序；
- 需要核对首段 ASR、LLM、TTS 性能日志。

复现完成后恢复普通模式。不要长期保存或分享未经检查的 DEBUG 日志。

## 四、M2.4 通过条件

- 文档中的启动信号和错误文本与冻结源码一致。
- 项目负责人能够在 5–10 分钟内完成一次三轮冒烟测试。
- 文字链路、语音链路、嘴型、表情和打断全部通过。
- 至少能够使用本文定位一个已知问题，而不需要修改核心架构。
- 自动化回归继续通过。

## 五、M2.4 验收结果

项目负责人已经完成三轮短验收，后端启动、Electron 连接、文字链路、语音
链路、嘴型、表情、播放中打断和后端退出全部通过。M2.4 正式关闭。
