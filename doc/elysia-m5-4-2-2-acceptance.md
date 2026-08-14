# Elysia M5.4.2.2 严格管理 CRUD 验收

更新日期：2026-08-14

当前状态：**实现与自动化验收通过**。

## 1. 固定范围

本步骤基于 M5.4.2.1 已冻结的 WebSocket v1 协议，只实现
`PersistentMemoryService` 的管理专用写操作和业务层测试：

- 带严格 `expected_revision` 的 Upsert；
- 按稳定 `memory_id` 编辑；
- 按稳定 `memory_id` 删除；
- 带严格 `expected_revision` 的清空；
- 管理写入固定来源 `manual_ui`；
- 重复、容量、敏感内容、存储失败和 revision 冲突结果；
- replacement 备份净化与同作用域并发保护。

本步骤不接入 `ServiceContext` 管理控制器、WebSocket 路由、配置开关持久化
或前端界面。M5.3 自然语言记忆命令的写入重试、正文匹配删除和清空冲突
返回语义保持不变。

依赖基线提交：`525976a`。

## 2. 实现结果

### 2.1 严格 revision

- [x] 四类管理写操作都要求严格非负整数 `expected_revision`；
- [x] 布尔值不能作为 revision 使用；
- [x] 每次操作在同作用域互斥锁内强制重载持久化文档；
- [x] 预期 revision 不一致时不写入、不重试；
- [x] 存储 CAS 冲突后只重载当前 revision 和 item count，不重试写入；
- [x] 实际修改只增加一次 revision，幂等成功不增加 revision；
- [x] 相同旧 revision 的并发请求最多只有一个成功修改。

### 2.2 Upsert 与编辑

- [x] 管理 Upsert 的新增和称呼替换都固定来源为 `manual_ui`；
- [x] 相同规范化内容返回已有 ID，且不改写来源、时间或 revision；
- [x] 编辑只按 32 位小写十六进制 `memory_id` 查找；
- [x] 编辑保留原 `id` 和 `created_at`，重建内部 key；
- [x] 实际编辑更新 `updated_at` 并将来源固定为 `manual_ui`；
- [x] 相同类别和规范化内容为幂等成功；
- [x] 与其他普通条目重复或产生第二条首选称呼时返回 `duplicate_item`；
- [x] ID 不存在时返回 `not_found`，不退化为正文或模糊匹配；
- [x] 更新称呼不受“当前已满”误阻塞，新建仍受 `max_items` 限制。

### 2.3 删除、清空与备份

- [x] 管理删除只按精确 ID，不产生 `ambiguous_match`；
- [x] 删除和清空均使用 replacement 备份；
- [x] 编辑也使用 replacement 备份，旧敏感正文不会残留在 `.bak`；
- [x] 新建继续使用 previous 备份模式；
- [x] 空文档清空为幂等成功，不增加 revision；
- [x] 写入失败不会用未持久化文档污染服务缓存。

### 2.4 校验与兼容性

- [x] 内容策略在访问存储前执行；
- [x] 敏感内容、非法值和容量限制沿用现有 M5 业务规则；
- [x] 新增稳定原因码 `duplicate_item` 及对应固定反馈；
- [x] 管理 CRUD 不记录正文，也不暴露路径或内部 key；
- [x] M5.3 Upsert 仍在 CAS 冲突后最多重载重算一次；
- [x] M5.3 原清空接口仍保留冻结的冲突返回，不附加管理接口元数据。

## 3. 自动化证据

新增管理业务层测试：

```text
19 passed
```

管理 CRUD 与既有记忆业务、反馈、类型定向回归：

```text
62 passed
```

完整项目测试范围 `tests/`：

```text
398 passed, 8 subtests passed
```

相对 M5.4.2.1 的 `379 passed, 8 subtests passed`，新增 19 项均来自严格
管理 CRUD 测试。唯一警告仍为 FFmpeg/avconv 未出现在当前测试进程的
`PATH`；它与本次纯记忆存储业务逻辑无关。

质量门禁：

- [x] 修改的 Python 文件 `ruff check` 通过；
- [x] 修改的 Python 文件 `ruff format --check` 通过；
- [x] `git diff --check` 通过；
- [x] 完整项目 `tests/` 回归通过；
- [x] 没有新增运行日志、测试音频、私有配置或持久记忆运行产物。

## 4. 退出结论

M5.4.2.2 已在业务层形成可供后续管理控制器调用的严格 CRUD：管理写入
使用强制重载、精确 revision 和存储 CAS 双重保护，编辑与删除使用稳定 ID，
幂等、重复、容量、敏感内容、并发冲突、备份净化和失败缓存均有自动化测试
保护，同时未改变冻结的 M5.3 自然语言链路。

下一步可以进入 M5.4.2.3，将冻结的协议模型映射到这些业务方法，并接入
管理控制器；该步骤仍应与 WebSocket 路由和前端实现分层验收。
