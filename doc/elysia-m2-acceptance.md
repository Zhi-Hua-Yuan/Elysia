# Elysia MVP-A M2 总验收记录

验收日期：2026-08-11

## 结论

M2“MVP-A 稳定化与可复现交付”正式验收通过。MVP-A 已从“当前机器能够
运行”整理为具有冻结运行基线、无密钥配置模板、Windows/uv 启动说明、
日常冒烟清单、故障排查和分阶段验收记录的可复现交付。

M2 没有引入长期记忆、MCP、Agent、Computer Use、正式角色资产或新的
Character 架构，也没有修改核心会话链。

## 冻结基线

| 组件 | 冻结值 |
|---|---|
| 后端 M2 起点 | `ca6ca1e4390e6c5744afd42b61dd2a1027872eef` |
| 后端版本 | Open-LLM-VTuber 1.2.1 |
| 前端子模块 | `06a659b114fff788cf0daaa86e484576db4975bf` |
| 前端源码 | `3d57a9a0125a0ca13e5d096f7092e4e494ed389e` |
| Electron | 官方 1.2.1 安装包 |
| Live2D | 上游 `mao_pro` 占位模型 |
| ASR | Sherpa-ONNX SenseVoiceSmall int8，CPU、4 线程 |
| 默认 TTS | Sherpa-ONNX `vits-melo-tts-zh_en`，`sid: 0`、CPU、4 线程 |
| 在线回退 | Edge TTS 中文女声 |

Electron renderer 与 Web 构建资源名不同，但 M2.1 已证明它们来自同一前端
源码提交，不属于版本混用。

## 分项结果

### M2.1 运行版本关系 — 通过

- 核对后端、前端子模块、前端源码和 Electron 安装包来源。
- 记录 Electron 可执行文件、`app.asar` 和 renderer 资源指纹。
- 决定继续使用冻结子模块和官方 Electron 1.2.1，不编辑压缩构建产物。

证据见 [`elysia-m2-runtime-baseline.md`](elysia-m2-runtime-baseline.md)。

### M2.2 设置保存与持久化 — 通过

- 项目负责人已在进入 M2.3 前确认 M2.2 完成。
- 后续 M2.3 干净配置演练和 M2.4 三轮冒烟中，麦克风链路与播放中打断正常。
- 当前仓库和前端子模块没有 M2.2 源码改动，因此本记录只确认验收结果，
  不把它描述为仓库中的前端代码修复。

如果保存症状在其他机器或新 Electron 状态中重新出现，应按冒烟故障排查
重新复现，并将其作为新的前端问题处理。

### M2.3 可复现启动 — 通过

- 配置模板从 Edge 默认切换到 M0 已验收的 Sherpa-ONNX 本地 TTS。
- 从无密钥模板重新生成本地配置并通过 Pydantic 校验。
- Windows、uv、Python、模型目录、环境变量和启动顺序已经固化。
- 后端、Electron、ASR、LLM、TTS、嘴型、表情和打断人工演练全部通过。

证据见 [`elysia-m2-3-acceptance.md`](elysia-m2-3-acceptance.md)。

### M2.4 冒烟与故障排查 — 通过

- 建立 5–10 分钟三轮冒烟清单。
- 故障排查覆盖环境、配置、模型、端口、WebSocket、LLM、ASR、TTS、
  Live2D 和打断。
- 项目负责人完成文字、语音、表情/打断三轮短验收及正常退出，全部通过。

证据见 [`elysia-m2-4-acceptance.md`](elysia-m2-4-acceptance.md)。

### M2.5 最终稳定性检查 — 通过

- 完整回归：33 项测试和 8 项子测试通过。
- Ruff：本次修改的 Python 测试文件通过。
- 配置：Pydantic 校验通过；凭据占位符、ASR/TTS 选型和功能开关正确。
- Git：后端和前端冻结提交一致，没有未说明的核心代码或前端产物修改。
- 安全：待交付文件未命中常见 API Key/Token 格式。
- 忽略：`conf.yaml`、`private/`、`models/` 继续受 `.gitignore` 保护。
- 文档：相对链接检查无缺失目标，补丁格式检查通过。
- 许可证：后端、Live2D 示例和本地 Sherpa TTS 模型许可证文件均存在。

## M2 退出条件

| 条件 | 结果 |
|---|---|
| 可从无密钥模板建立本地配置 | 通过 |
| 可按文档独立启动后端与 Electron | 通过 |
| 完整语音链路和播放中打断可复现 | 通过 |
| 有日常冒烟与主要故障排查路径 | 通过 |
| 仓库不包含凭据或专有角色资产 | 通过 |
| 自动化回归通过 | 通过 |
| 已知非阻塞问题有记录 | 通过 |

## 已知非阻塞事项

1. 测试进程仍提示 pydub 找不到 FFmpeg。当前 Sherpa TTS 直接生成 WAV，
   M0、M2.3 和 M2.4 人工播放均正常；切换压缩音频格式时重新验证。
2. LLM 环境变量只在设置它们的 PowerShell 会话中有效，关闭窗口后需要
   重新设置。这是当前无密钥配置流程的操作约束，不是配置框架缺陷。
3. M0 原计划的“独立连续打断 10 次至少成功 9 次”没有量化记录；M0 已由
   项目负责人正式关闭，M2.3 和 M2.4 又分别验证了播放中打断，不阻塞 M2。
4. 正式爱莉希雅 Live2D、GPT-SoVITS 权重、参考音频和角色音色尚未接入，
   属于 M3 范围。

## 下一步

M2 正式关闭。只有在用户提供有权本地使用的 Live2D 和声音资产后才进入
M3“MVP-B 正式角色资产接入”。资产到位前不提前修改 `model_dict.json`、
表情索引或 GPT-SoVITS 参考音频配置。

