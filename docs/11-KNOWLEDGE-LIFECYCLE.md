# 知识生命周期

`knowledge_lifecycle/` 将 Attachment、Processing/Embedding、SQLite 和 [Project Source catalog](./10-PROJECT-SOURCES.md) 组合成可恢复变更。Catalog 是唯一授权发布面：新数据最后授权，删除先撤销授权。

该后端 Library 已接入 [桌面 UI](./12-KNOWLEDGE-UI-TESTING.md)，但不依赖 Renderer/Electron。变更只接受 active canonical Project、准确 Project Scope 和 `project_source` ownership。Chat 附件需要先显式 promotion，Project Workspace binding 不产生自动读取权限。

## 操作与事务模型

`KnowledgeLifecycleService` 提供 add、replace、reindex、rebuild、revoke、delete、`recover_pending()`，另有状态/操作查询和取消。Verified original export 使用独立 Service。

Attachment Manifest、SQLite 与 JSON catalog 没有共同事务，因此使用持久 saga：

1. 第一个副作用前写操作日志。
2. 每个阶段以单调 CAS revision 写 checkpoint。
3. 新数据先提交，完整 catalog 最后发布。
4. 破坏性操作先 tombstone 整个 catalog，再清理数据。
5. 恢复重新读取各 canonical store，幂等推进到安全终态。

允许短暂留下未授权向量或已撤销但未清理的数据；不允许缺失、过期或删除中的 generation 继续回答。

## 持久日志

`JsonKnowledgeOperationRepository` 保存版本化 `knowledge_operations.json`，最多 **2,048 项 / 4 MiB**。采用 exact-schema JSON、重复键拒绝、安全父链、descriptor-bounded read、identity 复核、进程内/OS sidecar 锁和同目录 temp→fsync→replace；支持时同步父目录。

`journal_revision` 必须 N→N+1；progress、attempt、catalog revision 单调前进。create/load/save 都验证 state/phase/progress、kind/target/staged identity 与 terminal/error 一致性，载入时拒绝一个 Project 存在多个可恢复任务。

日志仅含恢复需要的 opaque operation/link IDs、Project Scope、kind/state/phase/progress、revisions、profile/catalog fingerprints、有界 Instructions、attempt、UTC 时间和闭集错误码。它不保存 native import path、正文、vector、原文件 digest、Prompt、模型输出或 traceback。

不可变 origin marker 记录操作是否从显式 revoked catalog 开始，区分既有 tombstone 与 replace 从 revision 0 自行创建的首个 tombstone，防止恢复错误重启授权。

JSON array 顺序是锁内分配的 durable creation sequence，不依赖 wall clock 或随机 ID。超出 2,048 项时只淘汰最旧 terminal entry；非终态不让位，全部活跃或仍超 4 MiB 则失败。日志不是无限审计记录，也不是长期 Instructions authority；后者由 catalog/tombstone 保存。

## 状态与阶段

| State | 含义 |
| --- | --- |
| `running` | 正常推进 |
| `cancel_requested` | 取消已持久化，等待安全边界 |
| `recovery_required` | 副作用可能提交，必须重新核对 stores |
| `succeeded` | 要求的授权结果已持久完成 |
| `cancelled` | 安全提交点前完成取消，无已授权半成品 |
| `failed` | 确定不能继续或恢复次数耗尽 |

后三者是终态，且当且仅当 phase 为 `completed`。其他 phases 是 `preparing`、`importing`（仅 add/replace）、`indexing`、`revoking`、`cleaning`、`publishing`。`recovery_required` 保留可继续的最近阶段。

Progress 仅是单调显示值，不是恢复游标或授权事实。错误仅保存 `add_failed`、`recovery_failed`、`recovery_exhausted` 等脱敏 code。

## 新增、重新索引与重建

所有发布必须覆盖当前完整、current-profile corpus。

```text
Add
  journal → import → prepare processing/embedding → atomic vector commit
  → 补齐当前 ownership 的 generations → CAS catalog last → succeeded

Reindex
  journal → verify target → prepare → atomic generation replacement
  → CAS complete catalog last → succeeded

Rebuild
  journal → snapshot all current indexable ownership
  → process/embed all → atomic whole-Scope vector rebuild
  → CAS complete catalog last → succeeded
```

Prepare 不修改 Vector Store。Add 在导入后、vector commit 前可安全取消并清理 staged ownership；一旦 commit 结果模糊就进入恢复，不能宣称干净取消。

Reindex 的 vector replace 与 catalog publish 之间，旧 catalog 可能指向已不存在的旧 generation。Retriever 会准确拒绝，不回退为“最新记录”。Rebuild 的 SQLite 事务只覆盖整个 vector Scope，catalog 仍最后发布，避免授权部分重建。

## 整个 Project 撤销

`revoke_project_sources()` 记录日志后 CAS tombstone **完整** catalog，再标记成功。即使此前未发布也创建 revision 1 tombstone，保留 Instructions。Originals 保留，既有向量不保证立即删除，但已无法构造授权 allowlist。

恢复授权必须显式 `rebuild_project_sources()`。Revoked 状态下 add/replace/reindex 返回 conflict，不能顺带启用来源。Delete 仍可清理单个 Source，并把裁剪后的 preferences 写回 tombstone，使其余来源和 answer style 持续保持 revoked。

这是整个 Project 停用，不是单 Source toggle；保留 ownership 却静默从 catalog 隐藏它会破坏完整 corpus 合同。

## 替换与删除

Replace 使用 copy-on-write，不覆盖原 link：

```text
journal → import new staged link → prepare/commit staged vector
  → cancellation boundary
  → preferred old link 改为 staged link
  → CAS whole-catalog tombstone，保留替换后的 policy
  → cleaning checkpoint → delete old vectors/artifacts → remove old ownership
  → CAS complete remaining/new catalog → succeeded
```

Tombstone 是不可返回点。在此前取消可清理 staged 数据、保留旧授权；之后只能向前完成，因为回滚旧 catalog 可能重新授权已被删除的 generation。

Delete 同样先撤销整个 catalog：

```text
journal（已含裁剪后的 preferences）
  → CAS tombstone（保留 policy）
  → delete target vectors/lineage → artifact cleanup → remove ownership last
  → 原来为 live catalog：发布完整剩余 corpus
    原来未发布/已 revoked：继续保持无授权
  → succeeded
```

Attachment removal 在 Manifest lock 内比较刚读取的完整 snapshot fingerprint，并要求准确 Project Scope/role。先提交 ownership/derived-relation 删除，再清理 blob；清理失败可恢复，但不能产生部分存活 ownership。

## 取消与恢复

`request_cancel()` 以 journal CAS 持久化 `cancel_requested`。取消是协作式：在导入/prepare 等安全点清理 staged 数据后终止；vector commit 可能发生或 catalog 已 tombstone 时继续向前恢复。终态不能重新取消。

`recover_pending(project_id=None)` 处理 running、cancel_requested 和 recovery_required，重新读取 active Project、ownership、catalog 与 vectors，不假设最后 checkpoint 紧邻最后副作用：

- 按 kind 证明目标终态；delete 必须确认 target 消失，replace 必须确认 staged 发布且旧 target 消失，generation 必须匹配 profile。
- 只使用 journal 持久的 exact staged link。若 import 成功但 link checkpoint 未写入，返回 `source_selection_lost`，不按 ownership 差集猜目标。
- Prepare/commit/rebuild/delete 可重复；到了 publishing 后可重做 cleanup，但 durable phase 不倒退。
- 模糊 SQLite commit 保守向前；catalog 只接受日志确认的 revision，不吸收意外并发写入。
- Add/reindex/rebuild 跨重启发现 immutable profile 改变时，以 `index_profile_changed` 终止；未授权 staged add 先回滚。
- Delete 无论 profile 是否升级都必须完成 cleanup 并保留 tombstone。Replace 越过 tombstone 后同样清理旧目标、保留新 policy、保持 revoked，要求显式 rebuild，不能复活待删来源。
- Destructive cleanup 从 immutable Manifest 重建目标，不受当前 loader-route 过滤；移除某格式支持不能永久阻塞旧数据删除。
- Import/prepare 已失败且首次 staged rollback 也失败时，只重试 cleanup，不重新索引或发布。
- 每次增加 attempt，最多 **8 次**，耗尽后 failed / `recovery_exhausted`。

幂等性来自 atomic vector replacement/rebuild、可重复 delete、catalog revision CAS 和 snapshot-guarded attachment removal。它只保证**同一个 durable operation** 的恢复；公开 add/replace 没有 caller idempotency key，重发全新命令仍可能创建新操作。

## 全局协调与状态视图

生产 Answer、Lifecycle、export 共用一个全局 `ProjectSourceOperationCoordinator`。Backend 同时只允许一个公开 lifecycle/export task，并在 grounded generation 和 authority write 的 admission 上对称检查。切换 Project 不释放 lease，不能启动第二个冲突操作、Chat move、Project archive、Instructions/workspace mutation。

Renderer busy 只是后端事实的投影。未来改为按 Project 分片时，必须重新证明跨 Project move 和 authority-change 竞态已关闭。

`list_project_sources()` 在同一 lease 下组合 ownership、catalog 和可恢复操作：

| Source state | 含义 |
| --- | --- |
| `ready` | ownership、完整 catalog、generation、当前 profile 精确一致 |
| `unindexed` | 已 owned，Project 从未发布 catalog |
| `stale` | fingerprint、Source、generation 或 profile 不匹配 |
| `revoked` | 有单调 tombstone，无授权 snapshot |
| `processing` | 可恢复操作的 staged 或 target link |

内部 view 包含无路径 Source metadata、状态、可选 fingerprints/发布时间和 operation ID。Desktop 映射为更窄 DTO，并通过 `knowledge.operation.changed/completed` 发布 checkpoint。Archived Project 可只读查看 Sources/operations，不能变更、导出或 grounded answer。

## 原始文件导出

`KnowledgeExportService.export_original()` 只导出 active Project 当前 owned original，不导出 vectors、chunks、Prompt、journal 或内部 catalog。它使用共享 lease，但不写 lifecycle saga journal。

1. Native caller 提供绝对目标路径，父目录必须存在且父链无 symlink/reparse。
2. 读取单 link 原子 snapshot，核对 `project_source` role。
3. 外部 temp 创建后、写 original bytes 前，将 temp/parent 绝对路径与稳定 identity 写入 app-private cleanup intent，并 flush/fsync。
4. 经 `open_verified_file()` 流式复制，核对 declared size 上下界后 flush/fsync。
5. 发布前复核 ownership fingerprint、temp/parent 及完整父链 identity，拒绝 replace/delete 或路径替换。
6. `overwrite=False` 用同目录 hard-link create-if-absent，`overwrite=True` 用 `os.replace`。
7. 发布/取消清理后清除 intent，仅返回安全名称、MIME、字节数。

正常异常、协作取消和 shutdown 按已记录 identity 清理未发布 temp。强杀后，由初始化之后独立 worker 执行 `recover_pending_exports()`；构造函数只验证 app-private intent storage，不等待断开的 USB/UNC/外部目录。

恢复只删除名称、普通文件类型、父链、temp/parent identities 全匹配的文件。Missing temp 可安全清 intent；坏记录、不可达目录或 identity mismatch 保留原状并 fail closed，不阻止其他合法记录清理。已成功发布但清 intent 失败仍报告成功，遗留记录下次收敛。

Electron Save dialog 提供目标路径，只传入可信 Python 边界。请求进入 Backend pending map 后，snapshot 仅显示 request ID、Project ID、`cancellable:false`，可跨 Renderer reload/Project switch 恢复所有权。Main 在对话框前固定 Source 名称/MIME/大小；BackendProcess 与 Main 都复核 receipt 后才发布 settled event。目标/temp/intent 路径不进入 React，UI 没有 Export Stop。

## 派生数据清理边界

当前没有独立 Knowledge Preview/Cache repository；Chunk、vector、lineage 在 SQLite，Attachment derived relations 随最终 ownership 删除级联。

生命周期仍显式依赖 `KnowledgeArtifactCleanup.purge_document(scope, source)`。生产注入 `NoStoredKnowledgeArtifacts`，表明当前没有额外仓库。未来若引入 preview、OCR cache 或 rendered pages，组合层必须替换 Adapter，并在删除 ownership 前完成幂等清理。

下层内容/结构/推理/时间预算不被生命周期放宽。公开错误为 Validation、NotFound、Conflict、Storage、DataCorruption、Recovery；进度和错误不带正文、路径、vector、Prompt 或底层异常。

## 验证与运行边界

```powershell
python -m pytest tests/test_knowledge_lifecycle.py tests/test_knowledge_export_recovery.py tests/test_desktop_knowledge.py -q
```

从仓库根目录运行；文档检查见 [AGENTS.md](../AGENTS.md)。

Lifecycle 恢复由用户显式选择 **Recover**；初始化仅在后台清理符合 identity 的 export temp。当前没有目录监听、自动导入、跨 Project 搜索、长期任务调度器、文件 Preview 或原文位置跳转。真实文档和 UI 验证范围见 [Knowledge UI](./12-KNOWLEDGE-UI-TESTING.md)。
