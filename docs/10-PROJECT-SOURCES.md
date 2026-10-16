# Project Sources

`project_sources/` 决定当前 Chat 可以读取哪一份 Project 语料。回答入口只接收 `chat_id` 和 Query，Project Scope 与 generation allowlist 必须从 canonical Chat→Project 关系、Attachment snapshot 和显式发布的 catalog 派生。

[桌面组合层](./12-KNOWLEDGE-UI-TESTING.md) 已将授权、[Grounding](./09-GROUNDED-ANSWERS-CITATIONS.md) 和 [生命周期](./11-KNOWLEDGE-LIFECYCLE.md) 接通。Project Chat 仍须显式启用 **Use Project Sources**。

## 授权流程

```text
chat_id + query
  → 持有 ProjectSourceOperationLease
  → 读取 active Chat → active Project
  → 派生准确 Project Scope
  → 原子 Attachment snapshot
  → 验证完整 published catalog + current index profile
  → 解析有界、非授权性 preferences
  → GroundedAnswerService
  → 重新读取 authority、files、catalog、preferences
  → 发布绑定原 Chat/Project 的结果
```

调用方不能提供任意 Project ID、Scope、generation 集合或 Chat/Project union。Vector row 只能证明索引存在，不能证明当前 Chat 权限或处理策略有效。

同 Project 的多个 Chat 共用一个 `scope(project, project_id)` 和完整 catalog；不同 Project、未分配 Chat、Archived Chat/Project 不可读。可见性不由文件名、内容 hash、UI 选择或向量内容推断。

## 原子文件快照

`FileCatalogSnapshot` 从同一次 Manifest 读取发布准确 Scope、canonical originals、ownerships 和顺序无关 fingerprint。

它要求所有 ownership 属于同一 Scope，Link/File IDs 各自唯一，每个 ownership 解析到一个 immutable original，且没有无 ownership 的 original。授权不能使用两次独立 list 调用拼接状态，因为无法证明它们来自同一 revision。

Project corpus 使用受支持文档路由的投影；图片等非 Document Loader route 不会污染文本 corpus fingerprint。每个入选 ownership 必须是 `project_source`。

## Catalog 与 CAS

`ProjectSourceGeneration` 保存准确 `ExpectedDocumentGeneration`、index-profile fingerprint 和 UTC published time。`ProjectSourceSnapshot` 保存 Scope、正整数 revision、Attachment fingerprint、按 link 排序的完整 generations、结构化 Instructions 和 snapshot fingerprint。

当前 schema v2 同时在 live entry 与 tombstone 保存 Instructions。v1 live entry 从 snapshot 迁移 policy，旧 tombstone 迁移为安全默认值。

`JsonProjectSourceRepository` 使用 exact-schema JSON、duplicate-key rejection、16 MiB descriptor-bounded read、父链 link/reparse 检查和文件 identity 复核。同目录 temp、`fsync`、`os.replace` 原子发布；共享进程锁和 sidecar OS lock 覆盖整个 read→compare→write，避免多个实例或进程通过同一旧 revision。

```text
create：expected 0 → revision 1
update：expected N → revision N+1
delete/revoke：expected N → tombstone N+1
recreate：expected N+1 → revision N+2
```

从未发布的 Project 也可 revoke，以 expected 0 创建 revision 1 tombstone。删除不重置 revision，防止延迟 writer 在删除/重建后通过 ABA 覆盖新 lineage。Tombstone 长期保留非授权性 policy，不依赖有界操作日志。

Catalog 必须完整覆盖当前 indexable ownership，且每项匹配当前 index profile；partial、stale、跨 Scope 或损坏 catalog 都是 typed failure。没有 catalog 时，只有确实无 Source 才是合法空 corpus；有 Source 则不可回答。显式 revoked catalog 只有 rebuild 能重新授权。

## Chat 附件与显式提升

Chat Attachment 默认只属于其准确 Chat Scope。加入 Project 不迁移旧附件；Project answer 不扫描 Chat history；相同 bytes/File ID 不产生跨 Scope 权限，也不能改写旧 generation 的 Scope。

`promote_chat_attachment(chat_id, attachment_id)` 是受信后端 primitive：

1. 在 lease 内重解 Chat→Project；Attachment 必须在 canonical messages 恰好出现一次，名称/MIME/大小一致。
2. 从单次 Manifest 读取取得该 link 的 ownership/original snapshot，核对角色；单 link 投影不会因长期 Chat 历史超过完整 catalog 上限而失败。
3. 底层复制要求 `committed` 状态及准确受支持 suffix/MIME route。
4. 通过 verified stream 复制并复核 size/SHA-256，在 Project 建立新 ownership，保留原 Chat ownership。
5. 同 bytes/metadata 重复调用复用既有 Project ownership；同 bytes 但不同 metadata 返回 conflict。
6. 不复制旧 derived relations，也不伪造新 generation。

Promotion 后 ownership 与 catalog 暂时不匹配会明确返回 stale，直到生命周期完成索引与完整 catalog 发布。该后端能力不等同于 UI 已提供独立 promotion 按钮。

## Instructions 与来源偏好

| 来源 | 可影响内容 |
| --- | --- |
| Catalog `preferred_source_link_ids` | 同一已授权 snapshot 内有界、唯一、有序的来源子集 |
| Catalog `answer_style` | `default`, `concise`, `balanced`, `detailed` |
| `ProjectSettings.custom_instructions` | 作为 untrusted JSON `style_guidance`，仅影响表达 |

Preferences 绑定准确 Scope、上述完整值和 canonical fingerprint。自由文本不解析为权限、Scope、link ID、tool call 或 Citation 指令。

偏好只在授权、metadata filter、cosine 排名、阈值、exact dedup 与可选 reranking **全部完成后**重排已有 Hit。它不能降低阈值、增加来源或恢复未返回片段；Prompt/request fingerprint 同时绑定偏好。

Style guidance 上限为 8,000 code points / 32,000 UTF-8 bytes。Project 通用设置允许保存更长 Instructions，但 Grounded Answer 会在检索前返回 typed limit failure，不静默截断；这不是 Project 持久化上限。

## 并发与最终复核

一次 busy 检查无法防止“Chat 属于 A → 生成期间移到 B → 发布 A 的引用”。因此 lease 必须覆盖完整 resolve→retrieve→generate→revalidate。

默认 `ProjectSourceOperationCoordinator` 使用保守的进程内全局 `RLock`。生产 Answer、Lifecycle、verified export 共享同一实例；Chat move、Chat/Project archive、Instructions/preferences、Source mutation 和 catalog publish/revoke 也必须进入同一权威协调边界。

生成后再次比较 Chat/Project identity 与更新时间、default model、Instructions、Attachment/catalog fingerprints 和完整 preferences。任何变化都丢弃答案并返回 conflict。这能检测错误 Adapter 或集成缺陷，不能替代共享 lease。

## 发布与删除

跨 Store 安全顺序由 [Knowledge Lifecycle](./11-KNOWLEDGE-LIFECYCLE.md) 实现：

```text
新增/重建：verified ownership → process/embed → atomic vector commit → CAS catalog last
撤销/删除：CAS catalog tombstone first → vectors/artifacts/ownership cleanup
```

发布前的向量是未授权数据；撤销后的清理失败只留下不可访问数据。完整 corpus 不变量不允许静默回答已索引的子集。

## 错误与验证

错误闭集为 Validation、Authorization、Conflict、Stale、NotFound、Storage、DataCorruption，均来自 `project_sources.exceptions`。公开消息不含 Query、Instructions、正文、路径、File ID、hash 或 generation fingerprint。合法零 Source/零 Hit 与权限、失效、损坏和资源错误保持区分。

以下测试覆盖跨实例/进程 CAS、tombstone ABA、防越权、共享、完整 corpus、promotion、偏好和生成期间 authority 变化：

```powershell
python -m pytest tests/test_project_sources.py tests/test_document_grounding.py -q
```

从仓库根目录运行；文档检查见 [AGENTS.md](../AGENTS.md)。文件 Preview、原文位置跳转、目录监听、自动导入与跨 Project 搜索不属于当前 Project Sources 能力。
