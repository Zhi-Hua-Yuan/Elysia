# Elysia M3.3 候选角色音色与软件运行基线验收

更新日期：2026-08-12

当前状态：**正式验收通过**。

## 目标与范围

M3.3 将以下组合固化为当前软件开发基线：

- Persona：Elysia Persona v1。
- Live2D：上游 `mao_pro` 占位模型。
- 默认 TTS：Fish Audio 在线候选角色音色。
- 本地回退：Sherpa-ONNX TTS，发生网络、额度或服务故障时手动切换。
- 已有功能：文字/麦克风输入、ASR、LLM、表情标签、语音、音量嘴型、打断和
  桌宠模式。

本里程碑不接入正式爱莉希雅 Live2D，不假定最终模型的参数 ID、表情索引、
Motion 分组或目录结构，也不新增 Character 框架和公开通信协议。

## 配置基线

`config_templates/conf.elysia.example.yaml` 默认选择：

```yaml
tts_config:
  tts_model: 'fish_api_tts'
  fish_api_tts:
    api_key: '${ELYSIA_FISH_API_KEY}'
    reference_id: 'aca922eff5f0446fbfd395fe03e48f35'
    latency: 'balanced'
    base_url: 'https://api.fish.audio'
    model: 's2.1-pro-free'
```

启动前检查环境变量是否进入当前终端：

```powershell
[bool]$env:ELYSIA_FISH_API_KEY
```

命令应返回 `True`。`conf.yaml` 必须保留 `${ELYSIA_FISH_API_KEY}` 占位符，
不得写入明文 Key。启动日志应出现：

```text
Initializing TTS: fish_api_tts
```

Fish 不可用时，在受 Git 忽略的 `conf.yaml` 中手动切换：

```yaml
tts_model: 'sherpa_onnx_tts'
```

切换后重启后端；M3.3 不实现运行时自动重试或自动更换音色。

## 错误处理

Fish 适配器已覆盖：

- 缺失或未展开的 API Key 在服务初始化时产生明确错误。
- 400、401、402、429 和其他 HTTP 错误返回脱敏摘要。
- 超时与网络传输错误只记录异常类型，不记录供应商响应内容。
- HTTP 200 但不是有效 WAV 时拒绝进入播放链路。
- 音频先写入 `.part` 临时文件，完整写入后原子替换；失败时清理临时文件。
- API Key、用户 TTS 原文、供应商响应正文和 Endpoint 不进入异常日志。
- TTS 返回失败时，由现有 TTS Manager 发送无音频 payload；后续轮次仍可继续。

同时移除了配置校验失败时输出完整配置对象的行为，只保留字段位置和错误类型，
避免环境变量展开后的凭据被写入日志。

## 自动化验收

2026-08-12 执行结果：

- Fish 与 Elysia 配置定向测试：21 项通过，8 项子测试通过。
- 完整测试集：48 项通过，8 项子测试通过。
- `ruff check src tests`：通过。
- `git diff --check`：通过。
- 已跟踪文件 Fish Key 模式扫描：无匹配。
- `conf.yaml`、`conf.yaml.backup` 和 `private/`：继续由 `.gitignore` 排除。

## 人工验收清单

以下项目需要项目负责人在自己的 Windows/Electron 运行环境完成：

- [x] `conf.yaml` 使用环境变量占位符，不含明文 Fish Key。
- [x] `uv run run_server.py` 启动，日志显示 `Initializing TTS: fish_api_tts`。
- [x] 输入一个普通中文短句，Fish 音频、字幕、嘴型和表情正常。
- [x] 连续完成三轮语音对话，无旧回复串音或无声轮次。
- [x] 让 LLM 返回多句内容，播放顺序与文本顺序一致。
- [x] 播放过程中说话，当前音频停止，旧回复不再继续播放。
- [x] 临时切换为 `sherpa_onnx_tts` 并重启，本地回退链路正常。
- [x] 切回 `fish_api_tts` 后再次正常对话。
- [x] 检查本次日志，没有 API Key、用户完整输入或供应商响应正文。

项目负责人于 2026-08-12 确认上述项目全部通过。

## 当前边界

- Fish 是默认技术运行方案，但声音模型授权状态仍为 `pending`；不得由“默认”
  推导为“允许公开发布”。
- Fish 是在线服务，受到网络、额度、限流和供应商状态影响。
- 首次技术验收的输入结束到首段 payload p50 为 5131.0 ms，结论为有条件通过，
  不覆盖 M0 已通过的 Sherpa 本地性能基线。
- 正式爱莉希雅 Live2D 尚未接入，当前继续使用 `mao_pro`。
- 最终模型特有映射和验收统一保留到 `M3-Live2D Final`。

## 退出条件

自动化检查和全部人工验收项均已通过，M3.3 正式完成。该结论仅代表软件运行
基线稳定，不代表正式 Live2D 或声音资产授权已经完成。
