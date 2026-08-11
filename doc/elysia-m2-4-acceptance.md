# Elysia MVP-A M2.4 验收记录

验收日期：2026-08-11

## 结论

M2.4“启动冒烟清单与常见故障排查”正式验收通过。项目负责人按照
[`elysia-mvp-a-smoke-and-troubleshooting.md`](elysia-mvp-a-smoke-and-troubleshooting.md)
完成三轮短测试，所有关键门禁均通过。

## 人工验收结果

| 验收项 | 结果 |
|---|---|
| 后端启动 | 通过 |
| Electron 连接 | 通过 |
| 文字链路 | 通过 |
| 语音链路 | 通过 |
| Live2D 嘴型与表情 | 通过 |
| 播放中打断 | 通过 |
| 后端正常退出 | 通过 |

本次测试用于确认日常冒烟清单可以在短时间内发现主链路回退，不重复执行
M0 的 10 轮性能验收。M0 的性能结论继续作为当前基线。

## 文档覆盖范围

故障排查表已经覆盖当前阶段的主要已知症状：

- `uv`、Python 环境和锁定依赖；
- `conf.yaml`、环境变量和配置校验；
- Live2D、SenseVoice、Sherpa TTS 和 Agent 初始化；
- 12393 端口占用；
- Electron/WebSocket 连接；
- LLM 连接错误、限流和 `Thinking...`；
- 麦克风、VAD、ASR、音频、嘴型、表情和播放中打断；
- verbose 日志的使用条件和敏感信息边界。

## 下一步

进入 M2.5：运行最终自动化回归，检查配置、文档、Git 忽略和敏感信息边界，
汇总非阻塞问题并决定是否正式关闭 M2。

