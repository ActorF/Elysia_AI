# Project Sources：显式授权、共享语义与安全 Instructions

本文记录 Elysia AI 在 [Grounded Answers and Citations](./09-GROUNDED-ANSWERS-CITATIONS.md) 之后新增的 Project Source Library 边界。它解决的是“当前 Chat 到底可以使用哪个 Project corpus”这一授权问题，而不是索引任务、生产模型接线或桌面 UI。

当前实现位于 `project_sources/`，并扩展了 `attachments/` 与 `documents/grounding.py`。它仍是独立 Python Library：仓库尚未提供真实 `GroundedAnswerGenerator` Adapter，也未把该服务接入 `Brain`、`start.py`、`desktop_backend.py`、Desktop Protocol 或 React。因此，本模块完成不代表当前桌面 Chat 已经能够向 Project 文件提问。

## 1. 完成范围

本边界完成以下能力：

1. **Chat-derived authorization**：回答入口只接收 `chat_id` 和 Query。Project ID、Project Scope 与准确 Generation Allowlist 必须从 canonical Chat→Project 关系、Attachment ownership snapshot 和显式发布的 Project Source catalog 派生；调用方不能自行传入。
2. **同 Project 共享**：属于同一 Project 的多个 Chat 解析到同一个 `AttachmentScope(kind="project", id=project_id)`，因此共享同一份 Project Source catalog；其他 Project、未分配 Chat、Archived Chat 或 Archived Project 均无法读取。
3. **完整 corpus，Fail Closed**：Project 有 Source 时，catalog 必须完整覆盖当前 ownership snapshot，而且每个 Generation 必须使用当前 index-profile fingerprint。缺失、部分、过期、跨 Scope 或损坏 catalog 都是 Typed Failure，不能伪装成空证据。
4. **Chat Attachment 默认隔离**：Chat Scope 不会被扫描、合并或回退为 Project Scope。即使两个 Scope 中的文件内容完全相同，也必须拥有各自独立的 ownership link。
5. **显式 promotion primitive**：用户明确选择后，只能把同时存在于 canonical Chat history、处于 committed 状态且有受信 Document Loader route 的 Chat Attachment 复制为新的 Project ownership。原 Chat ownership 保留；该操作不会伪造索引 Generation，必须等待后续生命周期服务完成索引与 catalog 发布。
6. **安全 Project Instructions**：catalog 持久化结构化 preferred link IDs 与 closed answer style；自由文本 Instructions 只作为有界、不可信的 style guidance。Preference 只重排已经通过检索、阈值与 Reranker 验证的 Hit，不能引入来源、复活低相关证据或绕过 Citation。
7. **操作租约合同与默认协调器**：完整 resolve→retrieve→generate→revalidate 操作要求持有 `ProjectSourceOperationLease`。Library 提供保守的进程内 `ProjectSourceOperationCoordinator`；生产 Composition Root 仍必须让同一实例同时包住 Chat 移动、Project 归档/Instructions 修改、Source 变更和 catalog 发布。

## 2. 权威数据流

`ProjectSourceAnswerService.answer()` 的数据流固定如下：

```text
chat_id + query
  → hold ProjectSourceOperationLease(chat_id)
  → load canonical active Chat
  → load canonical active Project from Chat.project_id
  → derive exact Project AttachmentScope
  → load one atomic FileCatalogSnapshot
  → validate every ownership as role="project_source"
  → load explicitly published ProjectSourceSnapshot
  → require exact ownership fingerprint + complete generations
  → require current index-profile fingerprint
  → resolve bounded non-authorizing answer preferences
  → GroundedAnswerService.answer(exact scope, exact generations)
  → re-read authority, files, catalog, and preferences
  → publish ProjectSourceAnswer bound to the original Chat and Project
```

这个 API 故意不接受以下字段：

- caller-provided Project ID；
- caller-provided `AttachmentScope`；
- caller-provided `ExpectedDocumentGeneration` allowlist；
- Chat Scope 与 Project Scope 的 union；
- 从 SQLite 反向发现的“看起来可用”Generation。

Vector row 只能证明某个索引记录存在，不能证明当前 Chat 仍被授权，也不能证明该 Generation 由当前处理策略产生。授权事实必须来自 canonical owner relationship、当前 Attachment snapshot 与显式 catalog。

## 3. Attachment 原子快照

`FileCatalogSnapshot` 从同一次 Manifest 读取中发布：

- 精确 Scope；
- canonical `OriginalFileMetadata` tuple；
- canonical `FileOwnership` tuple；
- 覆盖上述完整内容的 SHA-256 fingerprint。

它要求：

- 所有 ownership 都属于同一个 Scope；
- link ID 与 original file ID 唯一；
- 每个 ownership 都能解析到一个 immutable original；
- 不存在没有 ownership 的 original；
- 顺序变化不会改变 fingerprint。

旧的“分别 list originals，再 list ownerships”无法证明两次读取属于同一 Revision。Project Source 授权因此只消费这个原子快照。

## 4. Project Source catalog

`ProjectSourceGeneration` 保存：

- 一个准确的 `ExpectedDocumentGeneration`；
- 产生该 Generation 时的 `index_profile_fingerprint`；
- UTC `published_at`。

`ProjectSourceSnapshot` 保存：

- Project Scope；
- 正整数 CAS Revision；
- Attachment catalog fingerprint；
- 完整、唯一且按 link ID canonical 排序的 Generation tuple；
- 绑定当前 catalog 的 `ProjectSourceInstructions`，包含有序 preferred link IDs 与 closed answer style；
- 覆盖以上字段的 snapshot fingerprint。

`JsonProjectSourceRepository` 使用严格 JSON Schema、duplicate-key rejection、16 MiB descriptor-bounded read、父目录 symlink/reparse 检查、固定文件 identity 复核、同目录临时文件、`fsync` 与 `os.replace` 原子替换。共享的进程内锁与 sidecar OS file lock 覆盖完整 read→compare→write transaction，因此同一 catalog 的多个 Repository 实例或进程不能同时通过旧 Revision。新增、更新和撤销都必须提供期望 Revision：

```text
create: expected_revision=0, new revision=1
update: expected_revision=N, new revision=N+1
delete: expected_revision=N, tombstone revision=N+1
recreate: expected_revision=N+1, new revision=N+2
```

删除保留单调 tombstone，而不是把 Revision 重置为零。这会阻止持有旧 Revision 的延迟 writer 在删除并重建后通过 ABA 覆盖新的 catalog lineage。

Catalog 缺失时只有两种结果：

- 当前 Project 确实没有 Source：形成合法空 corpus，Grounding 可以返回 `insufficient_evidence`；
- 当前 Project 有 Source：返回 stale/unavailable failure，不能只回答已索引的子集。

## 5. 同 Project 共享与跨 Project 隔离

Project Sources 的唯一共享单位是 Project Scope。

```text
Project A
  ├─ Chat A1 ─┐
  └─ Chat A2 ─┴─→ scope(project, A) → catalog A

Project B
  └─ Chat B1 ───→ scope(project, B) → catalog B
```

`ChatSession.project_id` 是关系事实来源。Service 不按文件名、内容 Hash、Vector row、UI 选择或调用方参数猜测可见性。Repository 即使恶意返回另一个 Project 的 catalog，也会因为 Scope、ownership fingerprint 或 nested generation 不匹配而被拒绝。

Archived owner 不是只读 owner：Archived Chat 与 Archived Project 都不能发起 Project Source answer。

## 6. Chat Attachment 与显式提升

默认规则是：

- Chat Attachment 只属于准确 Chat Scope；
- Chat 加入 Project 不会自动迁移其旧附件；
- Project answer 不扫描 Chat history 或 Chat Manifest；
- 相同 file ID 或相同 bytes 不产生跨 Scope 权限；
- Chat Generation 不能改写 Scope 后作为 Project Generation 使用。

`promote_chat_attachment(chat_id, attachment_id)` 是一个明确的受信操作：

1. 在操作租约中重新解析 Chat→Project；
2. 要求 Attachment ID 在 canonical Chat messages 中恰好出现一次，并与消息中的文件名、media type 与 size metadata 一致；
3. 从同一次 Manifest 读取取得该 link 的原子 ownership/original snapshot，要求 ownership 存在且角色正确；这个单 link 投影不会因长期 Chat 已保留超过完整 catalog 上限的历史 Attachment 而失败；
4. 底层复制 primitive 再要求 Manifest 状态为 `status="committed"`；
5. 要求扩展名与 media type 精确匹配当前受信 Document Loader route；
6. 通过已验证的 source stream 复制 bytes，并重新核对 size 与 SHA-256；
7. 在 Project Scope 建立新的 `role="project_source"` ownership；
8. 保留原 Chat ownership；
9. 对相同内容与 metadata 重复调用时返回既有 Project ownership；若相同 bytes 已用不同 metadata 存在，则返回 conflict 而不是伪装成功；
10. 不复制旧 Derived relation，也不发布虚假 Generation。

Promotion 后 catalog 与 ownership 暂时不匹配是安全状态：回答会明确失败为 stale，直到 Module 8 完成 Processing、Embedding、Vector replacement 与 catalog CAS publish。

## 7. Project Instructions 与来源优先级

Project Instructions 分为三个安全层次：

- catalog 中的 `preferred_source_link_ids`：随 `ProjectSourceInstructions` 持久化，必须是同一 snapshot 内完整授权 Source link IDs 的有界、唯一子集；
- catalog 中的 `answer_style`：只允许 `default`、`concise`、`balanced` 或 `detailed`；
- 现有 `ProjectSettings.custom_instructions`：只映射为 `style_guidance`，作为 Prompt 中的 untrusted JSON data。它不能被解析为 Scope、link ID、permission、tool call 或 Citation directive。

`GroundedAnswerPreferences` 绑定：

- exact Scope；
- ordered preferred link IDs；
- closed answer style：`default`、`concise`、`balanced` 或 `detailed`；
- 有界 style guidance；
- canonical preferences fingerprint。

Preference 应用发生在 Retriever 已完成以下检查之后：

1. exact Scope 与 Generation allowlist；
2. Metadata Filter；
3. cosine candidate ranking；
4. minimum-similarity threshold；
5. exact deduplication；
6. 可选 fail-closed Reranking。

因此来源优先级只重排已经合法、已经相关的 Hit。它不能降低阈值、加入被过滤来源、恢复未返回片段或改变 Citation 闭集。Prompt template v2 把 style 和 preferences fingerprint 放入 canonical untrusted envelope；Request fingerprint 同时绑定完整 Prompt 与 preference fingerprint。

Style guidance 的硬上限为 8,000 code points 和 32,000 UTF-8 bytes。现有 Project 设置允许保存更长的 Instructions，因此 Project Source answer 会在检索和生成前返回清晰的 Typed Limit Failure，不进行静默截断；未来 UI 应在编辑时显示这个回答边界。

## 8. 并发与最终复核

单纯的 `is_chat_busy()` 检查无法关闭下列竞态：

```text
check Chat belongs to Project A
→ another operation moves Chat to Project B
→ old answer publishes citations from A
```

因此 Service 要求 context-manager lease 覆盖整个操作。`ProjectSourceOperationCoordinator` 提供可直接共享的保守全局进程内 `RLock`，但本模块尚未提供生产 Composition Root；后续接线必须让以下 mutation 使用同一个协调器实例：

- Chat 移入或移出 Project；
- Chat/Project archive；
- Project Instructions 或结构化 preference 修改；
- Project Source add/remove/promote；
- catalog publish/revoke；
- Project Source answer。

Library 仍会在生成后再次加载并比较：

- Chat ID、Project ID 与更新时间；
- Project 更新时间、default model 与 Instructions；
- Attachment snapshot fingerprint；
- catalog snapshot fingerprint；
- 完整 preferences value。

二次复核用于检测错误 adapter 或未来不完整接线；它不能替代生产共享租约。若任何权威值改变，答案被丢弃并返回 conflict。

## 9. 安全写入顺序

Module 8 实现生命周期时必须遵循以下顺序。

新增或重新索引：

```text
verified Project ownership
→ process and embed
→ atomically replace vector generation
→ CAS publish catalog record last
```

如果崩溃发生在最后一步前，Vector 只是未授权垃圾，不会进入回答。

撤销或删除：

```text
CAS revoke catalog record first
→ delete vector / derived data / original ownership
```

后续清理失败只会留下不可访问的数据，不会让已删除文档继续被检索。

## 10. 稳定错误语义

`project_sources.exceptions` 提供闭集错误：

- `ProjectSourceValidationError`：调用值、领域值或 adapter 返回结构非法；
- `ProjectSourceAuthorizationError`：Chat 没有可用的 active Project authority；
- `ProjectSourceConflictError`：lease 或操作期间的 authority/catalog 发生冲突；
- `ProjectSourceStaleError`：ownership、generation 或 index profile 不再完整匹配；
- `ProjectSourceNotFoundError`：显式 catalog 不存在；
- `ProjectSourceStorageError`：持久化或依赖边界失败；
- `ProjectSourceDataCorruptionError`：存储 JSON 不满足严格 Schema。

公开错误不包含 Query、Instructions、文档正文、本机路径、File ID、Hash 或 Generation fingerprint。正常的零 Source/零 Hit 与授权、过期、损坏和资源故障保持分离。

## 11. 测试覆盖

`tests/test_project_sources.py` 覆盖：

- catalog round-trip、Instructions fingerprint 与 CAS conflict；
- 跨 Repository 实例及真实 spawn process 的并发更新不丢失、删除 tombstone 防止 ABA；
- 同 Project 多 Chat 共享准确 Scope/Generation/Preferences；
- Chat Attachment 不会隐式进入 Project corpus；
- 图片等非 Document Loader route 不会污染文本 corpus fingerprint；
- 未授权 structured preference 在调用 Grounding 前失败；
- 旧 profile、ownership mismatch 与 partial corpus fail closed；
- foreign Project catalog 被拒绝；
- Archived Chat/Project 被拒绝；
- canonical Chat history + committed Manifest + supported route、保留 Chat ownership、幂等 promotion，以及相同 bytes/不同 metadata collision；
- promotion 后未索引状态明确为 stale；
- Grounding 期间 authority 改变时丢弃答案。

`tests/test_document_grounding.py` 另外覆盖 preference 只能重排已验证 Hit、恶意 style guidance 保持为不可信 JSON、指纹随配置改变，以及未知 preferred link 在检索前失败。

## 12. 当前非目标与下一步

本模块没有实现：

- Background index job、进度、取消、Retry 或 Crash Recovery；
- add/replace/reindex/revoke/delete 的完整传播清理；
- 生产 `GroundedAnswerGenerator`；
- `Brain` / `start.py` / Desktop Backend Composition Root；
- Desktop Protocol DTO、React Project Sources 状态与 Citation UI；
- Answer/Statement/Citation 的 Chat Message 持久化；
- Preview、page/block/cell 跳转；
- 真实 PDF/DOCX Desktop end-to-end 回归。

下一步是 **Knowledge Lifecycle**：把 Attachment、Processing、Embedding、SQLite Generation 与 Project Source catalog 按安全顺序组成可恢复任务。完成该步骤后，仍需 Knowledge UI and Testing 才能声称桌面 Chat 已经端到端支持文件问答。
