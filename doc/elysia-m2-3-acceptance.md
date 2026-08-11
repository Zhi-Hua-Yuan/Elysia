# Elysia MVP-A M2.3 验收记录

验收日期：2026-08-11

## 结论

M2.3“固化可复现启动流程”正式验收通过。项目已从可提交的无密钥模板重新
生成本地配置，并完成后端初始化、Electron 连接和完整语音链路演练。

本次验收没有引入新的配置框架，没有修改核心会话链，也没有将 API Key、
本地模型或专有资产加入仓库。

## 自动检查结果

- 新 `conf.yaml` 与 `config_templates/conf.elysia.example.yaml` 字节级一致。
- 模板和本地配置均通过 Pydantic 校验。
- 默认 ASR 为 Sherpa-ONNX SenseVoiceSmall int8，CPU、4 线程。
- 默认 TTS 为 Sherpa-ONNX `vits-melo-tts-zh_en`，`sid: 0`、CPU、4 线程。
- Live2D 为上游 `mao_pro` 占位模型；视觉输入和 MCP 均关闭。
- TTS 模型、词典、tokens、dict 和四个 FST 文件均存在。
- `conf.yaml` 和 `private/m2.3-backup/conf.original.yaml` 均受 Git 忽略规则保护。
- 自动化回归结果为 33 项测试和 8 项子测试通过。

测试仍有 1 项 pydub 的 FFmpeg `PATH` 警告。MVP-A 的 Sherpa TTS 直接生成
WAV，M0 和本次人工播放均正常，因此该警告不阻塞 M2.3。

## 人工演练结果

项目负责人在保存着 LLM 环境变量的同一个 PowerShell 会话中完成演练：

| 验收项 | 结果 |
|---|---|
| SenseVoice ONNX 与 tokens 文件检查 | 通过 |
| 后端服务上下文初始化 | 通过 |
| Electron 连接 | 通过 |
| 麦克风和中文 ASR | 通过 |
| LLM 回复与字幕 | 通过 |
| Sherpa-ONNX 本地 TTS | 通过 |
| Live2D 嘴型 | 通过 |
| Live2D 表情 | 通过 |
| 播放中打断 | 通过 |

本次只验证从可提交模板恢复后的完整链路，没有重复执行 M0 的 10 轮性能
测试。M0 的 5 秒 p50 结论继续作为当前性能基线。

## 配置与恢复边界

- 当前运行配置仍为被 Git 忽略的 `conf.yaml`。
- 原本机配置保存在被忽略的 `private/m2.3-backup/conf.original.yaml`。
- LLM Endpoint、模型名和 API Key 通过当前 PowerShell 的环境变量提供；
  关闭窗口后需要重新设置。
- ASR、TTS 模型继续保存在被忽略的 `models/`，不进入仓库。
- 正式爱莉希雅 Live2D 和 GPT-SoVITS 资产仍留待 M3。

## 下一步

进入 M2.4：建立启动冒烟清单和常见故障排查表。M2.4 不重新实现主链路，
只把已经验证的启动、连接、音频和打断检查压缩成每次变更后都能执行的短清单。

