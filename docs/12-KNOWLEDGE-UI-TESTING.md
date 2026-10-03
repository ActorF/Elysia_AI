# Knowledge UI and Testing：桌面 Project Sources 与可验证引用

本文记录 Elysia AI 如何把前八个文件/RAG 模块的独立 Python 边界接入生产 Desktop Backend、版本化协议和 React UI，并说明真实 PDF/DOCX 回归与 Stage 8 验收边界。底层数据模型与安全顺序分别见 [Grounded Answers and Citations](./09-GROUNDED-ANSWERS-CITATIONS.md)、[Project Sources](./10-PROJECT-SOURCES.md) 和 [Knowledge Lifecycle](./11-KNOWLEDGE-LIFECYCLE.md)。

## 1. 用户可见能力

Project 页面现在显示 canonical **Project Sources** 面板，并支持：

- 通过原生文件选择器添加一个或多个受支持文档；
- 查看 `ready`、`unindexed`、`stale`、`revoked` 和 `processing` 状态；
- 替换、重新索引、导出已验证的原始文件和删除单个 Source；
- 重建或撤销整个 Project corpus；
- 查看持久操作的 phase、进度、attempt 和稳定错误说明；
- 协作取消仍在安全边界之前的操作，以及恢复 durable `recovery_required` 操作；
- 导出经验证的 original；导出共用全局 Knowledge lease，但没有 journal entry 或 Renderer Stop；
- 在归档 Project 中只读查看 Sources 与操作历史。

每个 Project Chat 另有明确的 **Use Project Sources** 开关。关闭时继续走原有普通 Chat；开启时，Backend 只能从该 Chat 的 canonical Project 关系推导可读 corpus。Renderer 只在当前窗口内保存这项 opt-in，并把它绑定到准确 `chatId + projectId`；刷新/重开会重置，Chat 移到另一 Project 或 Project 被归档也会立即撤销。每次文字 Send、Retry 或 Voice Call 完整语句的自动提交都携带当次 intent，不会因 Project 中存在文件而自动开启。

成功的 grounded Assistant Message 会把陈述分为 `source_fact`、`model_summary` 和 `inference`，并显示可键盘访问的 Citation 详情。UI 只显示安全文件名、媒体类型、可选页码、可信 excerpt，以及 text block/offset 或 table cell 定位；它不会收到本地路径、内容 hash、内部 File ID、Vector、Prompt、模型推理或 traceback。证据不足是结构化 `insufficient_evidence` 结果，不会退回成无引用的普通回答。

## 2. 生产 Composition Root

`desktop_knowledge.py` 是唯一桌面知识组合边界。它从已验证的 `AppSettings` 与共享 `AttachmentService` 构造：

1. `DocumentLoaderService → ConservativeDocumentCleaner → StructureAwareDocumentChunker`；
2. 固定本地 Ollama Embedding Adapter、`DocumentEmbeddingService` 与 `SQLiteVectorStore`；
3. `DocumentIndexingService` 和 `DocumentRetriever`；
4. loopback-only `OllamaGroundedAnswerAdapter` 与 `GroundedAnswerService`；
5. 共享 Project/Chat/Source/operation repositories；
6. 同一个 `ProjectSourceOperationCoordinator` 下的 `KnowledgeLifecycleService`、`KnowledgeExportService` 与 `ProjectSourceAnswerService`。

索引 profile fingerprint 绑定 Loader route/version、资源限制、Cleaner/Chunker policy/version 与完整 Embedding space。相同处理契约在不同本机路径上产生相同 fingerprint；任一输出相关规则漂移都会把旧 generation 判为 stale，而不是继续使用含义不明的 Vector。

构造过程不会访问 Ollama、下载模型或启动索引。Backend 初始化完成 canonical owner 对账后才创建 runtime；未完成的 durable checkpoint 会显示在 Project Sources UI，由用户显式执行 **Recover**，不会阻塞 Backend 初始化、Chat 或 Settings。

## 3. Grounded Chat 与持久化

`Brain.stream_grounded_chat()` 和 `Brain.stream_grounded_retry()` 复用原有 generation admission、取消与 commit gate。一次调用按以下顺序完成：

1. 验证 active Chat，并从其 canonical `project_id` 读取 Project；
2. 在共享 operation lease 内复核 Project、Attachment ownership、published catalog、index profile 和 exact generation allowlist；
3. 只检索这个闭集，并让 Generator 只能引用本次请求的 opaque Citation ID；
4. 在取消仍未赢得 commit race 时，把 Assistant 文本与完整结构化 proof 一起原子保存；
5. 由 Chat Repository 在重启后恢复相同 Answer/Statement/Citation 数据。

普通 Chat/Retry 默认不携带 grounded proof；用普通 Retry 替换旧 Assistant Message 时也不会错误保留旧引用。只有 Assistant Message 可以持有 grounded answer。序列化器继续接受不含该可选字段的旧 Chat 文件，因此没有另建第二套 Chat History 或进行破坏性迁移。

## 4. 本地 Generator 边界

`documents/ollama_grounding.py` 只接受规范化的 loopback HTTP origin。Adapter：

- 不信任 proxy 环境、不跟随 redirect、不开 retry、不调用 model pull；
- 在生成前后解析同一 Ollama tag 的完整 manifest digest，tag 漂移时丢弃结果；
- 固定 `stream=false`、`tools=[]`、`think=false`、`truncate=false`、JSON format 和 temperature 0；
- 对 request/response bytes、wall-clock deadline、Content-Type、Content-Length、UTF-8、duplicate JSON member 与闭合响应 shape 执行有界验证；
- 只返回脱敏的稳定错误，不把 URL、路径或 transport/native 诊断跨出该边界。

结构闭包能证明每个显示 Citation 来自本次授权 Evidence，不能机械证明模型的 summary 或 inference 在语义上必然正确。UI 因此保留 statement kind，并继续提示用户复核重要内容。

## 5. Desktop Protocol v1

协议新增 `knowledge.management` capability，以及以下 exact methods：

| Method | 作用 |
| --- | --- |
| `knowledge.list` | 返回一个 Project 的 Sources 与持久操作历史。 |
| `knowledge.source.add` | 从 Electron 选择的绝对 native paths 启动 add saga。 |
| `knowledge.source.replace` | 用一个新选择文件替换准确 Source。 |
| `knowledge.source.reindex` | 重新派生并发布一个 Source。 |
| `knowledge.source.delete` | 先撤销授权，再清理派生数据与原始 ownership。 |
| `knowledge.project.rebuild` | 重建当前全部 owned Sources，并显式解除 revoke。 |
| `knowledge.project.revoke` | 保留 originals，但让整个 corpus 不再可检索。 |
| `knowledge.recover` | 从 durable checkpoint 向前恢复。 |
| `knowledge.source.export` | 通过原生 save dialog 导出一个已验证 original；只返回路径私有 receipt，不写 lifecycle journal。 |
| `request.cancel` | 支持准确、仍可取消的 Knowledge lifecycle request；recover/export 不会伪报停止成功，React 也不会为它们启用 Stop。 |

长生命周期操作在 Python worker 中执行，因此 Main/Renderer 仍可发送准确取消。`knowledge.operation.changed` 只传递路径私有 journal snapshot；`knowledge.operation.completed` 是 terminal checkpoint。Verified export 也在后台 worker 中执行，但不伪造 journal event。Electron 与 Python 都把 lifecycle mutation、export 和显式 grounded answer 视为同一个保守全局 Knowledge lease；active lifecycle/export owner 存在时，新的 list、mutation、export、grounded request 和 Project authority write 会在进入冲突工作前 Fail Closed。

Renderer-facing `BackendSnapshot.activeKnowledgeOperation` 保存进行中 lifecycle/export 的 request ID、Project ID 与可取消性。Export 在请求进入 Backend pending map 后以 `cancellable: false` 出现在 snapshot，因此 Renderer reload 会恢复所有权，切换到另一个 Project 也只会改变可见面板，不会释放 lease 或重新启用冲突动作。Lifecycle 成功响应验证完毕后，Electron 发携带 authoritative `KnowledgeState` 的 correlated settled event；export 则只在安全 receipt 与 Save dialog 前认证的 Source 文件名、media type、字节数完全匹配后发 `knowledge-export-settled`。目标路径、temp path 和 cleanup intent 永远不会进入 snapshot/event/React。失败使用 correlated `knowledge-operation-error`。React 以有界 terminal tombstone 拒绝迟到的旧 snapshot，避免 terminal-before-snapshot 或 renderer-reload 竞态重新激活已完成 request。

`chat.stream` 与 `chat.retry` 的可选 `useProjectKnowledge` 只表达本次请求的显式意图。Chat detail 的可选 `groundedAnswer` 是关闭字段集合；Python、TypeScript、JSON Schema 和共享 fixtures 对方法、result、event、字段上限及错误 shape 做双端验证。

## 6. 并发、取消、失败与恢复

- 一个 Backend 同时只允许一个公开 lifecycle/export task；共享的全局 `ProjectSourceOperationCoordinator` 还把 grounded answer 与这类写/导出工作互斥。它不是 per-Project lock，所以切到另一个 Project 不会扩大并发权限。
- Cancel 是协作式的：在可撤销点前会得到 durable `cancelled`；若不可逆 commit 已完成，操作可以安全成功，不能为了迎合迟到取消而回滚已发布事实。Export 没有 Renderer Stop 控件，但 Backend shutdown 会在原子发布前请求协作取消并清理未发布临时文件；发布完成后仍按成功处理。Recovery 是向前收敛且不接受协作取消；协议层对 recover/export 的普通 `request.cancel` 返回 `stopped=false`，公开 UI 不会发送这类 Stop。
- 新 generation 始终最后发布 catalog；replace/delete/revoke 在清理前先写入 tombstone，所以失败或 crash 不会让旧 Vector 继续被授权。
- `recovery_required` 显示稳定 error code，不显示底层异常。恢复从 Attachment、Vector、catalog 与 journal 的 canonical 状态向前收敛，最多尝试八次。
- Backend shutdown/EOF 请求取消、做有界等待并关闭 runtime；迟到 worker 不能把路径或私有诊断写入 Renderer 状态。
- Export 在复制原文字节前持久化 identity-pinned cleanup intent；强杀遗留 temp 由 initialization 之后的独立后台恢复清理，断开的外部目录不能阻塞 initialize，损坏或 identity mismatch 只保留记录并 Fail Closed。
- Archived Project 允许只读 source/operation view，但所有 mutation 和 grounded answer authority 都继续拒绝。

## 7. 真实文档与验收测试

`tests/fixtures/documents/` 提交两个很小、确定性的真实 container：

- `atlas-release-notes.pdf`：一页、未加密、可抽取文字的 PDF；
- `lumen-operations-handbook.docx`：包含 prose 与两列表格的标准 OOXML DOCX。

两份内容均由项目贡献者专为测试编写，不含私人课程资料、第三方文字、品牌、图片、字体或数据集，并在 fixture README 中记录 SHA-256、生成属性、来源和 CC0 1.0 waiver。回归使用生产 `AttachmentService`、真实 PDF/DOCX Loader、Cleaning/Chunking 与下游边界读取这些 bytes；模型相关部分使用确定性 test adapter，因此 CI 不需要网络、Ollama、GPU 或下载权重。

Stage 8 的自动化证据组合覆盖：

- 同 Project 多 Chat 派生相同 exact generation allowlist；
- Chat Attachment 不会隐式升级为 Project Source；
- foreign/archived Project、错误 scope、partial/stale catalog 全部 fail closed；
- replace/reindex/rebuild 只在新 generation 完整后发布；
- delete 先撤销，失败后仍不可检索，并由 recovery 完成清理；
- 真实 PDF/DOCX 的生产 loader route 与结构保留；
- Desktop method/result/event、原生 file dialog、进度、取消、恢复、归档只读，以及 export 在 Renderer reload/Project 切换期间的 snapshot ownership、可信 receipt、终态/失败和全局 busy 竞态；
- grounded proof 的持久化、重启恢复、statement 分类、Citation 展开与键盘焦点。

## 8. 验证命令

在 CMD 中运行：

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe scripts\check_python_documentation.py
.venv\Scripts\python.exe scripts\check_distribution_assets.py
.venv\Scripts\python.exe -m mypy --platform win32 agent attachments chats config core desktop_protocol documents knowledge_lifecycle memory models project_sources projects recovery scripts tools ui voice desktop_backend.py desktop_knowledge.py desktop_speech.py start.py
.venv\Scripts\python.exe -m pytest -q

cd /d D:\Elysia_AI\desktop
npm run docs:check
npm run lint
npm run typecheck
npm test
npm run build
npm audit
```

## 9. 明确非目标

本模块没有加入后台目录扫描、Internet Source、OCR、Preview Cache、自动文件导入、Agentic RAG、跨 Project 搜索或自动开启文件问答。Project Workspace binding 仍不授予文件读取权限；只有用户显式添加的 Project Source 才能进入 lifecycle。安装包仍不内置 Python、Ollama、Embedding/Chat 模型或可选语音 runtime，生产签名与独立更新继续属于后续阶段。

自动化中的真实 PDF/DOCX 是真实 container bytes，但生成/向量环节使用确定性 test adapter，Electron UI 回归使用专用 mock preload。因此这些验收能证明 Loader、结构保留、授权、持久化和 UI 合同，但不代表真实 Ollama/GPU、本机文件对话框或模型语义质量的人工矩阵已执行。

## 10. 关键文件

| 文件 | 责任 |
| --- | --- |
| `desktop_knowledge.py` | 生产知识 Composition Root、共享 lease/store/repository 与 profile fingerprint。 |
| `documents/ollama_grounding.py` | 有界、固定 digest、loopback-only 的 Ollama structured answer adapter。 |
| `core/brain.py` | Grounded new-turn/retry 编排与 proof/text 原子 commit。 |
| `chats/domain.py` / `serialization.py` | 结构化 Answer/Statement/Citation domain 与向后兼容持久化。 |
| `knowledge_lifecycle/export.py` | 原始文件导出、逐块取消、发布前身份复核与 crash cleanup intent/recovery。 |
| `desktop_backend.py` | 生命周期 worker、显式 recovery、非阻塞 export cleanup、取消、导出、grounded routing 与安全 DTO。 |
| `desktop_protocol/contracts.py` | Python request/result/event 和 grounded history exact validation。 |
| `desktop/electron/protocol.ts` | TypeScript 对称 parser、limits 与 operation invariants。 |
| `desktop/electron/backend-process.ts` | 全局 Knowledge lease admission、lifecycle/export pending snapshot、可信 receipt、terminal event/error correlation。 |
| `desktop/electron/main.ts` / `preload.cts` | Native open/save dialog、Source metadata 纵深复核与不含路径的窄 renderer API。 |
| `desktop/src/App.tsx` / `knowledge/ProjectSourcesPanel.tsx` | 跨 reload/Project 的 owner 恢复、全局 busy、terminal tombstone、Source/operation 管理、错误、取消、恢复与只读状态。 |
| `desktop/src/chat/MessageView.tsx` | Grounded statement/Citation 可访问显示。 |
| `tests/fixtures/documents/` | 可公开、确定性的真实 PDF/DOCX regression fixtures。 |
| `tests/test_desktop_knowledge.py` | Composition Root、共享 authority/store/lease、path-independent profile 和构造阶段零 HTTP 回归。 |
| `tests/test_ollama_grounding.py` | loopback origin、digest pinning、闭合生成 policy、响应预算与 transport 脱敏回归。 |
| `tests/test_knowledge_export_recovery.py` | export 强杀遗留、身份匹配、坏 intent 隔离、取消和 publish terminal consistency 回归。 |
| `tests/test_real_document_regression.py` | 真实 PDF/DOCX bytes 经生产 Loader/Processing 进入同 Project Retrieval 的确定性回归。 |
