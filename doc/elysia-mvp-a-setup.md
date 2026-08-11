# Elysia MVP-A 可复现启动指南

更新日期：2026-08-11

## 文档状态

- M2.3.1 配置基线对齐：已完成。
- M2.3.2 Windows/uv 环境准备：已完成。
- M2.3.3 本地配置和凭据准备：已完成。
- M2.3.4 干净配置启动演练：已完成。
- M2.3：正式验收通过。

本文档用于从项目冻结版本重新建立 MVP-A 运行环境。它不负责安装正式
爱莉希雅 Live2D 或 GPT-SoVITS 资产，也不引入新的配置框架。

## 冻结运行栈

| 模块 | MVP-A 基线 |
|---|---|
| 操作系统 | Windows 10/11 |
| Python | `>=3.10,<3.13` |
| 依赖管理 | `uv` + 仓库中的 `uv.lock` |
| 后端 | Open-LLM-VTuber 1.2.1 项目分支 |
| Electron | M2.1 确认的官方 1.2.1 安装包 |
| Live2D | 上游 `mao_pro` 占位模型 |
| ASR | Sherpa-ONNX SenseVoiceSmall int8，CPU、4 线程 |
| LLM | 用户提供的 OpenAI-compatible API |
| 默认 TTS | Sherpa-ONNX `vits-melo-tts-zh_en`，CPU、4 线程 |
| 回退 TTS | Edge TTS 中文女声，需要网络连接 |
| 后端 VAD | 关闭；使用前端浏览器 VAD |

MVP-A 不要求 NVIDIA GPU。LLM 仍需要访问用户配置的外部 API；Edge TTS
仅在切换到在线回退时需要网络。

## M2.3.1 配置基线

可提交的完整配置模板为：

```text
config_templates/conf.elysia.example.yaml
```

该模板已经与 M0 正式验收配置对齐：默认使用 Sherpa-ONNX 本地 TTS，
`sid: 0`、CPU、4 线程、速度 1.0；Edge TTS 只保留为在线回退配置。

用户实际运行仍使用仓库根目录下被 Git 忽略的 `conf.yaml`。模板中只保存
环境变量占位符，不包含 Endpoint、模型名或 API Key 的真实值。

## M2.3.2 环境准备

### 1. 在正确目录打开 PowerShell

所有命令均应在仓库根目录执行，例如：

```powershell
Set-Location D:\ajavacode\Elysia\Open-LLM-VTuber
```

下面的命令默认当前目录中能看到 `pyproject.toml`、`uv.lock` 和
`run_server.py`。

### 2. 检查 uv

```powershell
uv --version
```

`uv` 用于根据项目声明创建 Python 虚拟环境、安装锁定依赖和运行后端。
如果该命令输出版本号，说明当前 PowerShell 可以找到 `uv`。

### 3. 同步锁定依赖

```powershell
uv sync --frozen
```

- `sync` 会创建或同步项目根目录下的 `.venv`。
- `--frozen` 强制使用现有 `uv.lock`，不在准备环境时重新计算依赖版本。
- 不需要另外执行 `pip install -r requirements.txt`。
- 不需要手动运行 `.venv\Scripts\Activate.ps1`。

本阶段不要运行 `uv run upgrade.py`。升级脚本会把项目带离已经验收的冻结
基线，后续只有在单独评估上游版本时才使用。

### 4. 检查项目 Python

```powershell
uv run --frozen python --version
```

输出应位于 Python 3.10–3.12 范围。`uv run` 会自动使用项目 `.venv`，
因此后续启动命令不依赖 PowerShell 是否显示虚拟环境名称。

### 5. 检查前端子模块

```powershell
git submodule status
```

`frontend` 应对应 M2.1 冻结的子模块提交
`06a659b114fff788cf0daaa86e484576db4975bf`。如果是新的仓库副本且子模块
目录尚未检出，才执行：

```powershell
git submodule update --init --recursive
```

这条命令会下载并检出仓库记录的前端版本，需要网络。当前已经完成 M2.1
核对的工作区不需要重复执行。

### 6. 准备本地模型目录

模型文件位于 Git 忽略的 `models/`，不会随仓库提交自动出现。

ASR 必需文件：

```text
models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/
  model.int8.onnx
  tokens.txt
```

TTS 必需文件：

```text
models/vits-melo-tts-zh_en/
  model.onnx
  lexicon.txt
  tokens.txt
  dict/
  number.fst
  phone.fst
  date.fst
  new_heteronym.fst
```

模型目录还应保留模型包自带的许可证和说明文件。不要把下载得到的模型、
缓存或第三方声音资产加入 Git。

可以使用以下只读命令逐项检查：

```powershell
Test-Path .\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17\model.int8.onnx
Test-Path .\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17\tokens.txt
Test-Path .\models\vits-melo-tts-zh_en\model.onnx
Test-Path .\models\vits-melo-tts-zh_en\lexicon.txt
Test-Path .\models\vits-melo-tts-zh_en\tokens.txt
Test-Path .\models\vits-melo-tts-zh_en\dict
```

每条命令都应返回 `True`。FST 文件将在配置校验和 TTS 初始化时继续检查。

### 7. FFmpeg 边界

当前 Sherpa-ONNX TTS 直接生成 WAV，M0 已确认该链路不依赖 FFmpeg。
测试进程如果只出现 pydub 的 FFmpeg `PATH` 警告，不阻塞 MVP-A。以后切换
到 MP3、Opus 等压缩格式时再把 FFmpeg 作为强制前置条件。

## M2.3.3 本地配置准备结果

- 原配置已备份至 `private/m2.3-backup/conf.original.yaml`。
- 新 `conf.yaml` 与 `config_templates/conf.elysia.example.yaml` 字节级一致。
- 三个 LLM 环境变量已由项目负责人在当前 PowerShell 会话中设置并确认存在。
- 配置通过 Pydantic 校验；ASR、TTS、Live2D 和功能开关与 MVP-A 基线一致。
- `conf.yaml` 和 `private/` 均已确认受 `.gitignore` 保护。
- 校验过程没有输出 Endpoint、模型名或 API Key 的真实值。

环境变量只属于设置它们的 PowerShell 进程。后端必须从同一个窗口启动；
如果关闭该窗口，需要重新设置三个变量。

## M2.3.4 干净配置启动演练

### 1. 在用户 PowerShell 中确认 ASR 文件

Codex 沙箱账户受 Windows ACL 限制，无法枚举 SenseVoice 目录。请在保存着
三个 LLM 环境变量的 PowerShell 中执行：

```powershell
Test-Path .\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17\model.int8.onnx
Test-Path .\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17\tokens.txt
```

两项都应返回 `True`。TTS 模型、词典、tokens、dict 和四个 FST 文件已由
自动检查确认存在。

### 2. 启动后端

仍在同一个 PowerShell 中执行：

```powershell
uv run --frozen run_server.py
```

正常启动应依次看到 Live2D、ASR、TTS 和 Agent 初始化，并最终出现：

```text
Server context initialized successfully.
Starting server on localhost:12393
```

第一次加载本地 ASR/TTS 可能需要几秒。如果出现端口 12393 已占用，不要
启动第二个后端；先关闭旧进程，再重新执行启动命令。

只有启动失败且标准日志无法定位时，才改用：

```powershell
uv run --frozen run_server.py --verbose
```

分享日志前必须检查其中没有 Endpoint、对话内容或凭据。

### 3. 连接 Electron 并完成一轮对话

1. 启动 M2.1 冻结的 Electron 1.2.1。
2. 确认界面显示“已连接”。
3. 确认当前角色为“爱莉希雅 MVP”。
4. 打开麦克风，说：`你好，请用一句话告诉我你今天的心情。`
5. 检查中文转写、角色字幕、本地语音、嘴型和表情均正常。
6. 再发起一个稍长回答，并在播放期间说“等一下”，确认语音能够停止。

这不是重新执行 M0 的 10 轮性能验收，只验证从可提交模板生成的干净配置
仍然能够运行完整链路。

### 4. 结束演练

在后端 PowerShell 中按 `Ctrl+C` 停止服务。保留新的 `conf.yaml`，直至
M2.3 验收记录完成。原配置继续保存在 Git 忽略的 `private/m2.3-backup/`。

## M2.3.4 回报内容

项目负责人只需回报：

- 两个 ASR 文件检查是否均为 `True`；
- 后端是否出现 `Server context initialized successfully`；
- Electron 是否连接；
- 一轮语音对话的 ASR、TTS、嘴型、表情是否正常；
- 播放中打断是否正常。

不要发送 API Key、完整 `conf.yaml` 或包含敏感值的 DEBUG 日志。

## M2.3 验收结果

项目负责人已确认两个 ASR 文件、后端初始化、Electron 连接、ASR、TTS、
嘴型、表情和播放中打断全部正常。完整记录见
[`elysia-m2-3-acceptance.md`](elysia-m2-3-acceptance.md)。

后续日常变更不需要重复本指南的完整恢复流程，使用
[`elysia-mvp-a-smoke-and-troubleshooting.md`](elysia-mvp-a-smoke-and-troubleshooting.md)
中的 5–10 分钟冒烟清单即可。
