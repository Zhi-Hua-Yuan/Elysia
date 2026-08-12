# Elysia 软件 RC 可复现运行手册

更新日期：2026-08-12

## 文档定位

本文是 M3.4 软件路线的当前运行手册，取代 M2 阶段以 Sherpa-ONNX TTS 为
默认方案的日常启动说明。M2 文档继续作为历史验收证据保留。

当前软件 RC 使用：

- Elysia Persona v1；
- 上游 `mao_pro` 占位 Live2D；
- Sherpa-ONNX SenseVoice 本地 ASR；
- OpenAI-compatible LLM；
- Fish Audio 在线候选角色音色；
- Sherpa-ONNX TTS 手动本地回退；
- 冻结的 Electron 1.2.1 前端。

正式爱莉希雅 Live2D 尚未接入，最终模型的参数、表情和动作映射统一留到
`M3-Live2D Final`。Fish 的技术链路已经通过，但声音模型授权仍为 `pending`，
因此本基线是私人开发软件 RC，不是正式可分发角色资产版本。

## 一、冻结边界

M3.4 不实现新的对话功能、自动 TTS 回退、GPT-SoVITS、角色专属 Live2D 参数、
Character 框架、Memory、MCP、Agent 或 Computer Use。

软件基线：

| 模块 | RC 基线 |
|---|---|
| 操作系统 | Windows 10/11 |
| Python | `>=3.10,<3.13` |
| 依赖 | `uv` + 仓库内 `uv.lock` |
| 后端 | Open-LLM-VTuber 1.2.1 项目分支 |
| 前端 | 冻结 Electron 1.2.1 / 子模块 `06a659b1` |
| Live2D | `mao_pro` 占位模型 |
| ASR | SenseVoiceSmall int8、CPU、4 线程 |
| LLM | 用户提供的 OpenAI-compatible API |
| 默认 TTS | Fish Audio `s2.1-pro-free`，需要网络和 API Key |
| 本地回退 | Sherpa-ONNX `vits-melo-tts-zh_en` |
| VAD | 前端浏览器 VAD；后端 VAD 关闭 |

## 二、准备环境

所有命令都在仓库根目录的同一个 PowerShell 窗口执行：

```powershell
Set-Location D:\ajavacode\Elysia\Open-LLM-VTuber
```

### 1. 同步锁定依赖

```powershell
uv --version
uv sync --frozen
uv run --frozen python --version
```

- `uv sync --frozen` 按现有 `uv.lock` 创建或同步 `.venv`，不会重新解析依赖；
- `uv run --frozen` 使用项目虚拟环境运行命令，不要求手动激活 `.venv`；
- 不运行 `upgrade.py`，避免离开冻结基线。

### 2. 检查本地模型

默认 Fish 不需要本地 TTS 模型，但 ASR 和 Sherpa 手动回退需要下列文件：

```powershell
Test-Path .\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17\model.int8.onnx
Test-Path .\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17\tokens.txt
Test-Path .\models\vits-melo-tts-zh_en\model.onnx
Test-Path .\models\vits-melo-tts-zh_en\lexicon.txt
Test-Path .\models\vits-melo-tts-zh_en\tokens.txt
Test-Path .\models\vits-melo-tts-zh_en\dict
Test-Path .\models\vits-melo-tts-zh_en\number.fst
Test-Path .\models\vits-melo-tts-zh_en\phone.fst
Test-Path .\models\vits-melo-tts-zh_en\date.fst
Test-Path .\models\vits-melo-tts-zh_en\new_heteronym.fst
```

各项应返回 `True`。模型、缓存和第三方资产必须留在 Git 忽略目录。

## 三、从无密钥模板创建本地配置

不要直接覆盖已有配置。先把它备份到 Git 忽略目录：

```powershell
New-Item -ItemType Directory -Force .\private\m3.4-backup | Out-Null
Copy-Item .\conf.yaml .\private\m3.4-backup\conf.before-rc.yaml
Copy-Item .\config_templates\conf.elysia.example.yaml .\conf.yaml
```

如果本来没有 `conf.yaml`，跳过第一条 `Copy-Item`。模板中的凭据保持为环境变量
占位符，不要把真实值写入可提交文件。

在准备启动的同一个 PowerShell 中，只检查变量是否存在：

```powershell
[bool]$env:ELYSIA_LLM_BASE_URL
[bool]$env:ELYSIA_LLM_MODEL
[bool]$env:ELYSIA_LLM_API_KEY
[bool]$env:ELYSIA_FISH_API_KEY
```

四项均应返回 `True`。不要使用会显示变量真实值的命令，也不要发送完整
`conf.yaml` 或未经检查的 verbose 日志。

模板配置可通过定向测试验证：

```powershell
uv run --frozen pytest tests/test_elysia_persona_template.py -q
```

## 四、启动软件 RC

先检查端口是否空闲：

```powershell
Get-NetTCPConnection -LocalPort 12393 -State Listen -ErrorAction SilentlyContinue
```

没有输出时启动后端：

```powershell
uv run --frozen run_server.py
```

正常启动必须包括：

```text
Initializing Live2D: mao_pro
Initializing ASR: sherpa_onnx_asr
Initializing TTS: fish_api_tts
Initializing Agent: basic_memory_agent
Server context initialized successfully.
Starting server on localhost:12393
```

只有普通日志无法定位问题时才使用：

```powershell
uv run --frozen run_server.py --verbose
```

随后启动冻结的 Electron 1.2.1，确认“已连接”、角色为“爱莉希雅 MVP”，并
显示 `mao_pro` 占位模型。

## 五、五项 RC 冒烟验收

### 1. 文字链路

输入：`你好，请用一句话回应我。`

应有字幕和 Fish 语音；嘴型运动、表情变化；表情标签不进入字幕或朗读。

### 2. 麦克风链路

说：`请告诉我今天适合做什么。`

VAD 应自动收句，ASR 转写合理，随后正常回答和播放。

### 3. 多句顺序

要求角色用三句话回答。三句的字幕与音频顺序应一致，没有漏句、重复或串音。

### 4. 播放中打断

播放期间说“等一下”。当前声音应停止，旧回复后续分句不再播放，并可继续
下一轮对话。

### 5. Sherpa 手动回退

停止后端，把受 Git 忽略的 `conf.yaml` 中 TTS 选择改为：

```yaml
tts_model: 'sherpa_onnx_tts'
```

重启后日志应显示 `Initializing TTS: sherpa_onnx_tts`。完成一轮文字或语音
对话后，再切回 `fish_api_tts` 并验证启动。

M3.4 不实现运行时自动切换；Fish 失败时无音频 payload 和后续轮次仍可继续，
属于当前已记录行为。

## 六、Fish 故障排查

| 日志摘要 | 优先检查 |
|---|---|
| API Key required / unresolved | `ELYSIA_FISH_API_KEY` 是否在启动窗口中存在 |
| 400 | 声音模型 ID、模型名或请求参数 |
| 401 | API Key 缺失、失效或权限不足 |
| 402 | 账户额度 |
| 429 | 服务并发或频率限制 |
| timed out | 当前网络或在线服务长尾 |
| transport error | DNS、代理、网络或供应商可达性 |
| invalid WAV | 服务响应不是预期 WAV，音频会被拒绝 |

错误日志不应包含 API Key、用户 TTS 原文、Endpoint 或供应商响应正文。Fish
属于在线服务，网络、额度、限流和服务状态都会影响它。已有 11 轮测试的首段
payload p50 为 5131.0 ms，且存在明显长尾；这不会取代 M0 已通过的 Sherpa
本地 5 秒性能基线。

## 七、安全、资产与许可证边界

- `conf.yaml`、`conf.yaml.backup`、`private/`、`models/` 和日志必须受 Git
  忽略；
- 不提交 Fish API Key、LLM 凭据、私人 Endpoint、模型或声音资产；
- 后端代码、Web/Electron 前端、Live2D SDK/模型和声音资产适用不同许可；
- Fish 技术可用不代表候选声音允许公开展示、再分发或商业使用；
- 正式爱莉希雅 Live2D 尚未接入，当前截图和验收均使用上游占位模型。

完整资产边界见 [`elysia-m3-asset-contract.md`](elysia-m3-asset-contract.md)。

## 八、退出与回报

在后端窗口按 `Ctrl+C` 正常退出。不要直接结束未知的端口占用进程。

M3.4.3 人工验证只需回报：

```text
四个环境变量检查：通过 / 失败
本地模型检查：通过 / 失败
Fish 默认启动：通过 / 失败
Electron 连接：通过 / 失败
文字链路：通过 / 失败
麦克风链路：通过 / 失败
多句顺序：通过 / 失败
播放中打断：通过 / 失败
Sherpa 回退及切回 Fish：通过 / 失败
日志敏感信息检查：通过 / 失败
```

不要回报任何 Key、完整配置或私人对话内容。
