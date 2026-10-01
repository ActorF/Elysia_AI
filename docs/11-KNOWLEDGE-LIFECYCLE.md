# Knowledge Lifecycle：Project Source 可恢复变更与安全清理

本文记录 Elysia AI 在 [Project Sources](./10-PROJECT-SOURCES.md) 授权层之上新增的独立 Python Knowledge Lifecycle 边界。它把 Attachment Manifest、Document Processing/Embedding、SQLite Vector Generation 和 Project Source catalog 组合成持久、可恢复的 saga，并把 catalog 作为唯一授权发布面。

当前实现位于 `knowledge_lifecycle/`，并复用 `attachments/`、`documents/indexing.py` 与 `project_sources/`。它仍是 **Project-only 的后端 Library**，但已由 `desktop_knowledge.py` 与 `desktop_backend.py` 接入生产桌面路径：Desktop Protocol/Electron/React 可以管理 Source 与持久操作，显式开启的 Project Chat 可以通过 loopback-only Ollama Generator 得到带 Citation 的回答。该 Library 自身仍不依赖 Renderer 或 Electron。

## 1. 范围与硬边界

Knowledge Lifecycle 只接受 active canonical Project，并且只处理 `AttachmentScope(kind="project", id=project_id)` 中 `role="project_source"` 的 ownership。

- Chat Attachment 仍属于准确 Chat Scope；Chat 加入 Project 不会隐式提升旧附件。
- Chat Attachment 必须先经过 `project_sources` 的显式 promotion authority，产生新的 Project Source ownership，才可进入本生命周期。
- Archived 或不存在的 Project 被拒绝。
- 调用方不能传入任意 Scope，也不能用文件名、内容 Hash、Vector row 或 UI 状态推断 Project authority。
- 同一 Project 的多个 Chat 最终共享同一个完整 Project catalog；不同 Project 的 Attachment、Vector、catalog 和操作日志不能交叉使用。

`KnowledgeLifecycleService` 提供以下 Project-only 操作：

- `add_project_source()`：导入、索引并发布一个新 Source；
- `replace_project_source()`：以 copy-on-write 替换一个 Source；
- `reindex_project_source()`：重新计算并发布一个 Source 的当前 Generation；
- `rebuild_project_sources()`：原子重建当前 Project 的完整 Vector corpus；
- `revoke_project_sources()`：撤销整个 Project corpus 的回答授权，但保留 Project-owned originals；
- `delete_project_source()`：先撤销授权，再传播删除一个 Source；
- `recover_pending()`：从持久日志与当前 canonical stores 恢复未完成操作。

读取侧另外提供状态视图、操作查询、取消请求与原始文件导出；这些 API 不会把真实源路径暴露给调用方。

## 2. 为什么使用持久 saga

一次知识变更跨越至少三个不同持久化系统：

```text
Attachment Manifest / content-addressed originals
  ↕
SQLite chunks, vectors, and lineage
  ↕
Project Source authorization catalog
```

它们不存在一个共同数据库事务。实现因此不假装提供跨 Store ACID，而是采用 durable saga：

1. 在第一个副作用前写入操作日志；
2. 每个阶段通过单调 CAS Revision 持久化 checkpoint；
3. 新数据先准备并提交，授权 catalog 最后发布；
4. 破坏性操作先 tombstone 整个 catalog，再清理数据；
5. 崩溃后重新读取 Attachment、Vector 与 catalog 的 canonical state，幂等地向安全终态推进。

这一设计允许短暂留下“已经写入但尚未授权”的 Vector 或“已经撤销但尚未清理”的数据，但不会让缺失、过期或正在删除的 Generation 继续进入回答。

## 3. 持久操作日志

`JsonKnowledgeOperationRepository` 把有界操作历史保存在一个版本化 `knowledge_operations.json` 中：

- exact-schema JSON、duplicate-key rejection 与 canonical serialization；
- 最多 2,048 个操作、最多 4 MiB；
- 安全父目录检查，拒绝 symlink / Windows reparse redirect；
- descriptor-bounded read 与读取前后文件 identity 复核；
- 共享进程内锁和 sidecar OS file lock 覆盖完整 read→compare→write；
- 同目录临时文件、文件 `fsync`、`os.replace`，并在支持的平台同步父目录；
- `journal_revision` 必须严格 `N → N+1`，避免丢失并发 checkpoint；
- 操作进度、恢复尝试次数和 catalog revision 只能单调前进；
- create/load/save 都复核 state/phase/progress、kind/target/staged identity 与 terminal/error coherence，载入时还拒绝同一 Project 的多个 recoverable jobs。
- JSON array 的物理顺序就是跨进程锁内分配的 durable creation sequence；相同或回退的 wall clock 与随机 operation ID 都不能改变淘汰顺序。

日志只保存完成恢复所需的闭集元数据：opaque operation/link IDs、Project Scope、操作类型、状态、阶段、进度、catalog revision、操作是否起始于显式 revoked catalog 的 immutable marker、index-profile/catalog fingerprints、有界 Instructions、尝试次数、UTC 时间和闭集错误码。这个 origin marker 用来区分“操作从既有 tombstone 开始”与“从 revision 0 开始的 replace 自己先创建了首个 tombstone”，避免恢复错误撤销或错误重启授权。日志明确不保存本机路径、源正文、Vector、原始文件 content digest、Prompt、模型输出或 traceback。`add` / `replace` 接受的绝对源路径只存在于当前受信调用栈，不进入 journal。

Journal 是恢复状态，不是 Instructions 的长期 authority，也不是无限审计日志。创建第 2,049 个操作时，Repository 只淘汰 durable creation sequence 中最旧的 terminal entry；任何 `running`、`cancel_requested` 或 `recovery_required` 操作都不会为新命令让位。如果 2,048 项全部非终态，或 canonical JSON 仍超过 4 MiB，写入会以稳定资源错误 Fail Closed。撤销后的非授权性 Instructions 由 Project Source tombstone 自身持久保留，不会因 journal 淘汰而丢失。

## 4. Operation state 与 phase

`KnowledgeOperationSnapshot` 的 state 是操作结果/恢复语义，phase 是最近持久化的工作位置；两者不能互相替代。

### 4.1 State 闭集

| State | 含义 |
| --- | --- |
| `running` | 操作正在正常推进，尚未到达终态。 |
| `cancel_requested` | 已持久收到协作式取消请求；Service 会在下一个安全边界取消，或在越过提交点后继续向前恢复。 |
| `recovery_required` | 某个副作用可能已经提交，不能安全宣称失败或回滚；必须由恢复流程重新核对 canonical stores。 |
| `succeeded` | 所要求的授权结果已经持久完成；terminal。 |
| `cancelled` | 取消在安全提交点前完成，未留下已授权的半成品；terminal。 |
| `failed` | 操作确定无法继续且不需要/不能再自动恢复；terminal。 |

`succeeded`、`cancelled` 与 `failed` 是终态。公开错误只保存如 `add_failed`、`recovery_failed`、`recovery_exhausted` 这类闭集、脱敏 code，不保存 adapter diagnostics。

### 4.2 Phase 闭集

| Phase | 含义 |
| --- | --- |
| `preparing` | journal 已先行创建，尚未开始第一个外部副作用。 |
| `importing` | 正在把用户明确选择的文件导入 Project-owned Attachment storage；只用于 `add` / `replace`。 |
| `indexing` | 正在 Processing、Embedding 或提交/重建 SQLite Vector Generation。 |
| `revoking` | 正在 CAS tombstone 完整 Project Source catalog，使整个 corpus 先失去回答授权。 |
| `cleaning` | catalog 已撤销，正在幂等删除目标 Vector/Lineage、可选 artifact 与 ownership。 |
| `publishing` | 正在补齐所有 owned Sources 的当前 Generation，并把完整 catalog 作为最后一步 CAS 发布。 |
| `completed` | 操作到达 `succeeded`、`cancelled` 或 `failed` 终态。Repository 强制 terminal state 当且仅当 phase 为 `completed`；`recovery_required` 会保留最后一个可继续的工作 phase。 |

`progress_percent` 只用于展示单调进度，不是授权事实，也不是恢复游标。恢复始终以 state、phase 和当前 stores 的真实状态为准。

## 5. 安全发布顺序

所有发布都要求完整 current-profile corpus；不会只发布本次变更的单个 Generation。

### 5.1 Add

```text
persist journal
→ import verified original as a new Project Source ownership
→ prepare Processing + Embedding without mutating the Vector Store
→ atomically commit the new Vector Generation
→ re-read complete current Project ownership
→ prepare/commit any Generation missing during recovery
→ CAS publish the complete Project Source catalog last
→ mark operation succeeded
```

Catalog 发布前，新 Vector 是未授权数据。若导入后、Vector 提交前安全取消，Service 删除 staged ownership；若 Vector 可能已经提交，则操作进入恢复路径，不能把模糊结果报告成干净取消。

### 5.2 Reindex

```text
persist journal
→ verify exact current Project Source ownership
→ prepare new Generation without Store mutation
→ atomically replace that Source's Vector Generation
→ CAS publish a complete catalog with the new Generation last
→ mark operation succeeded
```

Vector replace 与 catalog publish 之间，旧 catalog 可能暂时指向已经不存在的旧 Generation。Retriever 的 exact-generation validation 会在这个窗口 Fail Closed；它不会回退到“Store 中最新的”记录。

### 5.3 Rebuild

```text
persist journal
→ snapshot every current indexable Project Source
→ process and embed every Source
→ atomically rebuild the complete Project Vector scope
→ CAS publish the matching complete catalog last
→ mark operation succeeded
```

`rebuild_scope()` 的 SQLite transaction 是整 Scope 的 Vector 提交边界。Catalog 仍然最后发布，所以崩溃不会授权 partial rebuild。

## 6. Whole-Project revoke

`revoke_project_sources()` 是明确的 **整个 Project 紧急停用**，不是单 Source toggle：

```text
persist journal
→ CAS tombstone the complete Project Source catalog
  (即使此前从未发布，也创建 revision 1 tombstone；保留 Instructions)
→ mark operation succeeded
```

它保留 Project-owned originals，也不承诺立即删除既有 Vector；没有 catalog 时，回答入口无法构造准确 Generation allowlist。由于 Project Source 授权要求 catalog 完整覆盖当前 ownership，系统不能一边保留某个 `project_source` ownership，一边把它从 catalog 中静默隐藏，否则“完整 corpus”不变量会失效。

需要重新启用时，必须显式调用 `rebuild_project_sources()`，对所有仍 owned 的 Sources 重新建立 current-profile Generations 并发布完整 catalog。显式 tombstone 存在时，普通 add/replace/reindex 会返回 conflict，不能顺带恢复授权；`delete_project_source()` 仍可永久移除一个 Source，但会用 CAS 更新 tombstone 中已裁剪的 preferences，使其余 Sources 与 answer style 继续处于持久 revoked 状态。`JsonProjectSourceRepository` 的 catalog schema v2 为 live entry 和 tombstone 都保存 Instructions，并能把旧 schema v1 tombstone 安全迁移为默认 policy。

## 7. Copy-on-write replace

Replace 不在原 link 上原地覆盖：

```text
persist journal with old target link
→ import replacement under a new staged Project Source link
→ prepare + commit its Vector Generation while still unauthorized
→ cancellation boundary
→ replace old preferred-link reference with the staged link
→ CAS tombstone the whole Project catalog       # point of no return
  (tombstone already retains the post-replace policy)
→ checkpoint cleaning
→ delete old Vector/Lineage and optional artifacts
→ remove old project_source ownership last
→ CAS publish the complete remaining/new corpus
→ mark operation succeeded
```

在 tombstone 前，取消可删除 staged Vector 与 ownership，旧 Source 继续保持原授权。越过 tombstone 后，恢复必须向前完成 cleanup 与 republish；此时回滚到旧 catalog 会重新授权一个可能已经被删除的 Generation，因此不安全。

## 8. Delete：先 tombstone，再传播清理

单 Source 删除仍先撤销 **整个** Project catalog：

```text
persist journal
  (journal already contains the post-delete pruned preferences)
→ CAS tombstone complete catalog first          # point of no return
  (tombstone retains the pruned preferences and answer style)
→ checkpoint cleaning
→ delete target chunks, vectors, and lineage
→ invoke explicit preview/cache cleanup boundary
→ remove exact project_source ownership last
→ if the operation began from a live catalog:
     CAS publish the complete remaining corpus
  else preserve the existing no-catalog/tombstone state
→ mark operation succeeded
```

Attachment removal 只允许 Project Scope 和精确 `project_source` role，并在 Manifest lock 内比较调用方刚读取的完整 snapshot fingerprint。Manifest-first removal 保证 ownership 的删除/关联级联作为一个原子 Manifest commit 发生；Blob 清理失败可以恢复，但不会伪造 ownership 已部分存在。

若 cleanup 中断，catalog 已是 tombstone，所以 Project corpus 保持不可回答。操作从 live catalog 开始时，恢复完成所有删除后才会重新发布剩余 Sources；操作从未发布或已 revoked 的 catalog 开始时，删除只清理目标并继续保持无授权 catalog。

## 9. 取消、恢复与幂等

`request_cancel()` 通过 journal CAS 把非终态操作持久转为 `cancel_requested`。取消是 cooperative，不是任意位置强杀：

- `preparing` / `importing`，或尚未进入模糊 Vector commit 的安全点，可以清理 staged 数据并终止为 `cancelled`；
- Vector 可能已提交、catalog 已 tombstone，或操作已经进入后续阶段时，取消请求不会留下半完成授权状态，恢复会继续 roll forward；
- terminal 操作不会被重新取消。

`recover_pending(project_id=None)` 枚举 `running`、`cancel_requested` 和 `recovery_required` 操作。每次恢复都重新读取 active Project、Attachment ownership 与 catalog，并通过幂等 prepare/commit/rebuild/delete 让 Vector Store 收敛；它不会假设最后一个 journal write 恰好紧邻最后一个副作用。它会：

- 按 operation kind 验证已经发布的准确目标 catalog（例如 delete 必须确认 target 已消失，replace 必须确认 staged 已发布且旧 target 已消失），并要求所有 Generation 使用当前 profile；
- 只使用 journal 已持久记录的 exact staged link；若崩溃发生在 import 成功但 staged-link checkpoint 之前，恢复以 `source_selection_lost` Fail Closed，不按 ownership 差集猜测目标；
- 重做 prepare/commit、tombstone、cleanup 或完整 catalog publish；
- replay 已到 `publishing` 时不会把 durable phase 倒退到 `cleaning`；cleanup 仍幂等执行，但 checkpoint 保持单调；
- 在 ambiguous SQLite commit 后保守向前；
- Catalog 发布/撤销只接受 journal 已确认的 revision；意外并发 revision 不能被吸收或覆盖；
- add/reindex/rebuild 若跨重启发现 immutable index profile 已变化，不会在新 profile 下冒充原操作继续执行；未授权 staged add 会先回滚，再以 `index_profile_changed` 终止；
- delete 的 cleanup 与 profile 无关，因此即使升级也必须先完成目标删除并保持 tombstone；replace 若已越过 tombstone，也会完成旧目标 cleanup、保留 post-replace policy 并维持 revoked，随后要求显式 rebuild，绝不因 profile 漂移复活待删来源；
- destructive cleanup 从 immutable Attachment Manifest 重建目标 identity，不再经过当前 loader-route 过滤；移除某种文档格式的支持不能把已经 revoked 的旧 ownership 永久卡在恢复队列；
- import/prepare 失败且第一次 staged rollback 也失败时，恢复只重试幂等 cleanup，不会重新执行已知失败的索引或意外发布该 Source；
- 每次递增 `attempt`，最多 8 次，耗尽后进入 `failed` / `recovery_exhausted`。

幂等性建立在底层操作上：Vector replace/rebuild 是原子 replacement，Vector delete 可重复，catalog save/delete 使用 Revision CAS，catalog 已达到目标时可直接识别，cleanup 在 ownership 已消失时仍可重复删除 Vector，Attachment removal 使用 snapshot fingerprint 防止删错新的 ownership。这里保证的是 **同一个 durable operation 的恢复幂等**；公开 add/replace API 当前没有 caller-provided idempotency key，用户重复发起一个全新命令仍可能创建新的操作。

当前生产 Runtime 使用保守的**全局** Knowledge lease，而不是按 Project 分片：`desktop_knowledge.py` 让 Lifecycle、Project Source answer 与 verified export 共用同一个 `ProjectSourceOperationCoordinator`；Desktop Backend 同时只允许一个公开 lifecycle/export task，并在 grounded generation 与 Project authority write 的 admission 边界执行对称检查。这样即使用户在任务期间切换到另一个 Project，也不能启动第二个 Source mutation、export、grounded answer、Chat→Project move、Project archive 或 Instructions/workspace mutation。Renderer 忙状态只是这项事实的可见投影，不能替代 Python 的最终权威检查。未来若改为 keyed coordinator，必须先证明跨 Project Chat move 与 authority-change 竞态仍被关闭。

## 10. Source view 与操作视图

`list_project_sources()` 从同一租约下的 current ownership、原子 catalog entry 和 recoverable operations 派生 `KnowledgeSourceView`：

| State | 含义 |
| --- | --- |
| `ready` | ownership、完整 catalog、Generation 与 current index profile 精确一致。 |
| `unindexed` | Source 已 owned，但该 Project 从未发布 catalog。 |
| `stale` | catalog 存在但 ownership fingerprint、Source、Generation 或 profile 不再准确。 |
| `revoked` | catalog 已有单调 tombstone revision 且当前没有授权 snapshot。 |
| `processing` | 该 link 是 recoverable operation 的 staged 或 target Source。 |

View 只包含 `DocumentSource` 的 path-private metadata、闭集状态、可选 derivation/profile fingerprint、发布时间与 opaque operation ID。`get_operation()` / `list_operations()` 返回 durable checkpoint；`desktop_backend.py` 已把它们映射为更窄的 Knowledge Protocol DTO，并用 `knowledge.operation.changed` / `knowledge.operation.completed` 发布相关请求的进度与终态。Renderer 不会收到原始 journal 文件或内部路径。

## 11. 原始文件导出

`KnowledgeExportService.export_original()` 与 mutating saga 分离。它只导出一个 active Project 当前 owned 的原始文件，不导出 Vector、Chunk、Prompt、journal、内部路径、catalog ID 或其他 Project 数据。

导出流程：

1. 要求 native caller 提供绝对目标路径，且父目录已经存在；
2. 拒绝父目录链中的 symlink / Windows reparse redirect；
3. 在共享 mutation lease 下读取单 link 原子 snapshot，并要求准确 `project_source` role；
4. 外部 temp 创建后、任何 original byte 写入前，把 temp/parent 绝对路径与稳定文件身份原子写入 app-private cleanup intent，并 flush + `fsync`；
5. 通过 `open_verified_file()` 流式复制，经 declared-size 上下界验证后 flush + `fsync`；
6. 发布前重新读取相同 ownership fingerprint，并再次验证 temp、parent 及完整父链身份，拒绝并发 replace/delete 或路径替换；
7. `overwrite=False` 使用同目录 hard-link create-if-absent，`overwrite=True` 使用 `os.replace`；
8. 正常 publish/cancel cleanup 后原子清除 private intent，再返回安全文件名、media type 与写入字节数，不返回内部 ID 或路径。

导出不是 lifecycle operation，也不进入 saga journal。正常异常、协作取消和 shutdown 会按已记录身份删除未发布 temp；进程被强制终止时，下一次 runtime 初始化在独立后台 worker 调用 `recover_pending_exports()`。构造函数只验证 app-private intent storage，不读取或等待断开的 USB/UNC/外部目标，因此 Backend initialize 不被恢复阻塞。恢复只会删除名称、父链、temp identity 与 parent identity 全部匹配的普通文件；missing temp 代表 publish/cleanup 已跨越边界，可安全清 intent；损坏、路径替换、identity mismatch 或不可达父目录均保留 intent/path 并 Fail Closed，同时不会阻止其他合法 intent 的清理。发布完成后若仅 private intent 清除失败，协议仍报告已发生的成功发布，遗留 intent 会在下次恢复时收敛。

Electron 通过原生 Save dialog 获得用户选择的目标，再由 `knowledge.source.export` 把经验证的绝对路径只传给 Python 可信边界。Save dialog 结束且请求进入 Backend pending map 后，Electron 才成为跨 Renderer reload 与 Project switch 的可观察 owner；snapshot 只公开 request ID、Project ID 和 `cancellable: false`，不公开目标路径。Main 在对话框前固定当前 Source 的安全文件名、media type 与字节数，BackendProcess 在移除 pending ownership 和发布 `knowledge-export-settled` 前要求路径私有 receipt 与这三项完全一致，Main 再做相同检查作为纵深防御。失败使用同一 request/Project 相关的安全 Knowledge error event。React 没有 Export Stop 控件，也永远不会收到 destination、temp path、cleanup intent 或本机诊断。

## 12. Preview / Cache cleanup 预留

当前仓库没有独立持久化的 Knowledge Preview 或 Knowledge Cache 实体。Chunk、Vector 和 Lineage 属于 Vector Store；Attachment derived relations 随最终 ownership Manifest commit 清理。

Lifecycle 仍要求显式注入 `KnowledgeArtifactCleanup`：

```text
purge_document(exact Project Scope, exact DocumentSource) → idempotent cleanup
```

生产 `desktop_knowledge.py` 明确传入 `NoStoredKnowledgeArtifacts`，表示“本部署确实没有独立 Preview/Cache repository”，而不是默默跳过未来数据。这是有意的删除传播协议：以后若引入 Preview、OCR cache、rendered page 或其他独立 artifact，Composition Root 必须替换该 adapter，并在 ownership 删除前完成其幂等清理；否则不能声称 delete 已覆盖全部派生数据。

## 13. 资源、安全与隐私

- 所有 Source 都必须匹配当前受支持的 `(suffix, media_type)` Document route。
- Processing、Embedding、SQLite 和 Retrieval 继续执行各自文档化的内容、结构、批次、维度、事务与时间预算；Lifecycle 不放宽下层限制。
- Operation journal 有 2,048 entries / 4 MiB 硬上限；恢复 attempt 上限为 8。
- Progress、错误和 view 均不包含正文、路径、Vector、Prompt 或底层异常。
- Catalog 是唯一授权面；存在 Vector 不等于有回答权限。
- 任何 ownership/catalog/profile mismatch 都是 stale/conflict/storage failure，不能降级为 partial corpus 或空命中。
- Library 不后台扫描 Workspace、不联网、不自动导入文件，也不因 Project Workspace binding 产生副作用。
- 公开异常分为 Validation、Not Found、Conflict、Storage、Data Corruption 与 Recovery；取消通过 durable `cancel_requested` / `cancelled` 状态表达，依赖异常会被转换为稳定脱敏语义。

## 14. 桌面集成状态与剩余非目标

Module 9 已完成生产 `OllamaGroundedAnswerAdapter`、`desktop_knowledge.py` Composition Root、显式 lifecycle recovery、全局 Knowledge worker/admission、operation/view/export/cancel DTO 与 route、Electron open/save dialog、跨 Renderer reload 的 lifecycle/export ownership、React Project Sources 管理和 grounded Chat/Citation 持久化。Archived Project 只能查看 Source/operation，mutation、export 与 grounded answer 继续 Fail Closed。

当前仍不存在独立 Preview/Cache repository，也没有文件 Preview、page/block/cell 原文跳转、后台目录扫描、自动导入、跨 Project 搜索或长期任务调度器。Lifecycle 操作只由用户明确选择 **Recover** 后向前恢复；Backend 初始化仅在独立 daemon 中清理身份完全匹配的 export temp，且不会等待不可达外部卷。真实 PDF/DOCX fixtures 已通过生产 Loader/Processing/Retrieval 自动化回归；模型部分使用确定性测试 Adapter，因此不声称真实 Ollama/GPU 桌面矩阵已全部验收。

## 15. 相关文件

| 文件 | 责任 |
| --- | --- |
| `knowledge_lifecycle/domain.py` | 操作 ID、kind/state/phase、路径私有 durable snapshot 与不变量。 |
| `knowledge_lifecycle/repository.py` | 有界、跨进程锁定、原子替换、Revision CAS 的 JSON saga journal。 |
| `knowledge_lifecycle/service.py` | Project-only add/replace/reindex/rebuild/revoke/delete、view、cancel 与 crash recovery 协调。 |
| `knowledge_lifecycle/export.py` | 在共享全局 operation lease 下验证并原子导出一个 Project Source original；它使用独立 cleanup intent，不写 lifecycle saga journal。 |
| `knowledge_lifecycle/exceptions.py` | 稳定、脱敏的生命周期错误闭集。 |
| `attachments/repository.py` / `service.py` / `store.py` | 原子 ownership snapshot 与 fingerprint-guarded Project Source removal。 |
| `documents/indexing.py` | 无 Store mutation 的 prepare、原子 Generation commit/rebuild 与幂等 delete。 |
| `project_sources/catalog.py` | canonical Attachment snapshot、文档路由过滤与 `DocumentSource` 派生。 |
| `project_sources/repository.py` | 原子 catalog entry read、CAS publish、schema-v1 migration 与保留 Instructions 的单调 tombstone。 |
| `tests/test_knowledge_lifecycle.py` | Journal CAS/retention、取消、发布顺序、replace/delete/revoke/recovery、Scope 隔离、view 与 export 回归。 |
| `desktop_knowledge.py` | 共享 repository/lease/store 的生产知识 Composition Root 与 index-profile fingerprint。 |
| `documents/ollama_grounding.py` | digest-pinned、loopback-only 的生产结构化 Generator Adapter。 |
| `desktop_backend.py` / `desktop_protocol/` | 显式 lifecycle recovery、非阻塞 export-temp cleanup、全局 lease/admission、窄 DTO、进度、取消、路径私有导出 receipt 与 grounded Chat route。 |
| `desktop/src/knowledge/ProjectSourcesPanel.tsx` | Source/operation 管理、恢复、导出、归档只读与稳定错误显示。 |
| `tests/test_desktop_knowledge.py` / `tests/test_real_document_regression.py` | Composition Root 不发起 HTTP 的构造边界，以及真实 PDF/DOCX container 的生产 Loader/Processing/Retrieval 回归。 |
| `docs/10-PROJECT-SOURCES.md` | 回答授权、同 Project 共享、Generation allowlist 与 catalog 完整性。 |
