# Elysia M3.4 软件路线可复现交付验收

更新日期：2026-08-12

当前状态：**正式验收通过**。

## 目标

把 M3.3 已通过的 Fish 默认软件组合整理为可从无密钥模板重建、可回退、可
审计的软件 RC。该 RC 使用 `mao_pro` 占位 Live2D，不等待正式角色美术资产。

## 固定范围

- Persona：Elysia Persona v1；
- Live2D：上游 `mao_pro` 占位模型；
- 默认 TTS：Fish Audio 在线候选角色音色；
- 手动回退：Sherpa-ONNX 本地 TTS；
- 已有功能：文字/麦克风、ASR、LLM、字幕、表情、嘴型、打断和桌宠；
- 最终 Live2D 接入：延后到 `M3-Live2D Final`。

Fish 声音模型授权仍为 `pending`。本里程碑只建立私人开发软件 RC，不授予或
推定公开展示、再分发和商业使用权。

## M3.4.1 范围与基线

- [x] M3.3 验收提交为 `70ae6d8`。
- [x] M3.4 不新增角色功能或通信协议。
- [x] 不编写最终 Live2D 模型专属代码。
- [x] Fish 保持默认，Sherpa 保持手动本地回退。
- [x] M3-Live2D Final 与软件路线继续解耦。

## M3.4.2 文档交付

- [x] 新增当前软件 RC 可复现运行手册。
- [x] 记录四个环境变量、模型文件和端口检查。
- [x] 记录 Fish 默认启动、五项冒烟和 Sherpa 回退流程。
- [x] 记录 Fish 在线限制、错误摘要、日志脱敏和资产授权边界。
- [x] 保留 M2 指南为历史记录，并指向当前 RC 手册。

当前运行手册：[`elysia-software-rc-runbook.md`](elysia-software-rc-runbook.md)。

## M3.4.3 干净配置与人工验收

- [x] 现有 `conf.yaml` 已备份到 Git 忽略目录。
- [x] 从 `config_templates/conf.elysia.example.yaml` 重新生成 `conf.yaml`。
- [x] 四个环境变量检查均为 `True`，且未显示真实值。
- [x] ASR 和 Sherpa 回退模型文件检查通过。
- [x] 后端默认初始化 `fish_api_tts`。
- [x] Electron 连接并显示 `mao_pro`。
- [x] 文字链路通过。
- [x] 麦克风链路通过。
- [x] 三句话按顺序播放。
- [x] 播放中打断通过。
- [x] Sherpa 回退及切回 Fish 通过。
- [x] 本轮日志没有凭据、私人 Endpoint、完整用户输入或供应商响应正文。

这部分由项目负责人在真实 Windows/Electron 环境操作。不要在验收记录中填写
任何凭据或私人对话内容。

项目负责人于 2026-08-12 确认以上项目全部通过。

## M3.4.4 自动化、仓库与安全门禁

- [x] Elysia 模板与 Fish 定向测试通过：20 项测试、8 项子测试。
- [x] 完整 Pytest 回归通过：48 项测试、8 项子测试。
- [x] `ruff check src tests` 通过。
- [x] `git diff --check` 通过。
- [x] 配置模板使用环境变量占位符，不包含真实凭据。
- [x] 已跟踪文本文件的凭据模式扫描无匹配。
- [x] `conf.yaml`、备份、`private/`、模型和日志继续受 Git 忽略。
- [x] 冻结前端提交仍为 `06a659b114fff788cf0daaa86e484576db4975bf`。
- [x] 当前工作区只有 M3.4 文档变更，没有专有 Live2D、声音权重或参考音频
  变更。

初检说明：沙箱账户无法执行用户安装的全局 `uv.exe`，因此定向测试使用项目
`.venv` 中的 Python 并显式加入 `src` 路径运行；测试内容与 `uv run` 路径一致。
项目负责人最终仍按运行手册使用 `uv run --frozen` 完成人工启动。

SenseVoice 模型目录继续受到 Windows ACL 限制，沙箱账户无法读取其中的模型和
tokens；这不是文件缺失结论。两项存在性检查保留给项目负责人的 PowerShell。
Sherpa TTS 的 model、lexicon、tokens 和 dict 已由自动检查确认存在。

## M3.4.5 RC 基线

- [x] 更新项目进度文档，M3.4 标记为完成。
- [x] 项目负责人确认软件 RC 验收结果。
- [x] 建立独立、可回退的 M3.4 提交。
- [x] 建立本地 `elysia-software-rc1` 标签。

不自动推送远程，不把软件 RC 称为正式角色资产版。

## 退出条件

人工验收、完整自动化回归、敏感信息和 Git 忽略检查已经全部通过，M3.4 正式
关闭。正式爱莉希雅 Live2D 与最终角色资产验收仍属于 `M3-Live2D Final`。

当前 RC 只证明软件可复现运行，不改变 Fish 声音模型授权为 `pending` 的结论，
也不把 `mao_pro` 占位模型表述为正式角色模型。
