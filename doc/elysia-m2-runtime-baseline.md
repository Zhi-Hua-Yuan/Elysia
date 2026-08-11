# Elysia AI Companion M2 运行基线

更新日期：2026-08-11

## M2.1 结论

M2.1 已完成。当前 Electron 安装包与仓库冻结的 Web 前端虽然生成了不同的
JavaScript 文件名，但可以确认二者来自同一个前端源码提交：
`3d57a9a0125a0ca13e5d096f7092e4e494ed389e`。

因此，开发者工具中看到 `index-CW5qlpM1.js`，而仓库 `frontend/index.html`
引用 `main-nu7uwxNJ.js`，并不表示项目混用了未知前端版本。它们分别是
Electron renderer 构建和独立 Web 构建的产物，入口命名、打包目标和内容
大小不同，生成不同的内容哈希属于预期结果。

## 冻结基线

| 组件 | 冻结值 | 证据 |
|---|---|---|
| 后端分支 | `dev` | M2.1 核对时工作区干净 |
| 后端提交 | `ca6ca1e4390e6c5744afd42b61dd2a1027872eef` | M1 Persona v1 验收提交 |
| 后端版本 | `1.2.1` | 项目元数据及运行日志 |
| 前端子模块提交 | `06a659b114fff788cf0daaa86e484576db4975bf` | 后端 Git tree 与子模块 HEAD 一致 |
| 前端源码提交 | `3d57a9a0125a0ca13e5d096f7092e4e494ed389e` | 子模块构建提交说明明确记录 |
| Web 构建入口 | `assets/main-nu7uwxNJ.js` | `frontend/index.html` |
| Electron 产品版本 | `1.2.1` / `1.2.1.0` | Windows 文件版本元数据 |
| Electron renderer 入口 | `out/renderer/assets/index-CW5qlpM1.js` | 安装包 `app.asar` 文件表及 renderer HTML |
| Electron 发布源 | `Open-LLM-VTuber/Open-LLM-VTuber-Web` | 安装包 `app-update.yml` |

## 对应关系证据

冻结子模块提交 `06a659b1` 的提交说明为：

```text
Deploying to build from @ Open-LLM-VTuber/Open-LLM-VTuber-Web@3d57a9a0125a0ca13e5d096f7092e4e494ed389e
```

本机 Electron `app.asar.unpacked/resources/release-notes.md` 列出的最新提交为：

```text
3d57a9a fix motion out-of-bound bug
cb1b60e fix: cannot immediately stop the AI
4fe0d71 fix: resolve inconsistent audio interruption behavior
0321d1a comment F12
```

Electron 可执行文件和 `app.asar` 的时间戳为 2025-08-21 20:38；源码提交
`3d57a9a` 的时间为 2025-08-21 20:34（UTC+08:00）。提交记录、发布说明和
构建时间相互吻合。

## 本机安装包指纹

本机安装目录为 `%LOCALAPPDATA%/Programs/open-llm-vtuber`。该路径只记录
当前验收机的安装方式，不作为其他机器必须使用的固定路径。

```text
open-llm-vtuber-electron.exe
SHA256 0D892CC136CF4D073E6AA20912C2A1BF719E6A2D2991D756770A7D7534AAF365

resources/app.asar
SHA256 DACBF22DD9AE8D0486554B8507655BA748100BED76393C9AABD6BCF9E8E8849E
```

安装包内 renderer 资源指纹：

```text
index-CW5qlpM1.js
SHA256 C79D50B53D6F2B68CD53F8E88D3DAD299DE494710F76CE1D8F766924E4B5B4CC

index-DBAmt0B7.css
SHA256 0ED64AE50D817935B35BE7B83E8ADD1577A6A9E72AEF82FD89DE20439F4A321A
```

## M2 使用规则

- 后端继续以当前 `dev` 为项目开发分支；每个后续 M2 变更使用独立提交记录。
- Web 前端继续冻结在子模块 `06a659b1`，除非某个已复现问题只能通过明确的
  上游修复解决。
- Electron 继续使用上述官方 1.2.1 安装包，不因构建文件名不同而重装或
  替换资源。
- 不直接编辑 `app.asar` 或压缩后的前端构建文件。
- 设置保存问题应在同源前端逻辑中复现和定位；如果需要修复，优先在前端
  源码上做最小修改并重新构建，而不是修改安装包产物。
- 后续验收截图或日志只需记录产品版本和必要的资源名，不需要反复把不同
  资源哈希当作版本冲突。

## M2.2 后续结果

项目负责人已在进入 M2.3 前确认识别设置保存与持久化验收完成。M2.3 和
M2.4 的后续演练中，麦克风链路和播放中打断均正常。

当前仓库和冻结前端子模块没有为 M2.2 引入源码修改，因此这里只记录验收
状态，不把结果描述为仓库中的前端代码修复。如果同一症状在其他机器或新的
Electron 状态中再次出现，应按故障排查文档重新复现。
