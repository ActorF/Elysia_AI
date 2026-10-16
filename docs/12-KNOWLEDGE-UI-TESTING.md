# 知识库界面与验证

桌面通过 `desktop_knowledge.py` 将独立的文件、索引、授权和回答模块组合为 Project Sources。底层合同见 [回答与引用](./09-GROUNDED-ANSWERS-CITATIONS.md)、[来源授权](./10-PROJECT-SOURCES.md) 和 [生命周期](./11-KNOWLEDGE-LIFECYCLE.md)。

## 用户流程

在 Project 的 **Project Sources** 面板中，可以添加文档，查看索引状态，替换、重新索引、导出或删除来源，也可以重建/撤销整个 Project 语料。操作历史显示阶段、进度、attempt 和稳定错误，支持安全边界内的协作取消及显式 **Recover**。Archived Project 仅允许查看。

Project Chat 使用明确的 **Use Project Sources** 开关。选择只保存在当前窗口，并绑定准确 `chatId + projectId`；刷新/重开、Chat 换 Project 或 Project 归档都会撤销。每次 Send、Retry 或 Voice Call 完整语句提交都携带当次 intent，不会因为 Project 有文件而自动开启。

Grounded Message 区分 `source_fact`、`model_summary`、`inference`，提供键盘可访问的 Citation 详情：安全文件名/MIME、可选页码、excerpt、Block/offset 或 table cell 坐标。证据不足显示 `insufficient_evidence`，不退回无引用普通回答。Citation detail 尚不提供原文件 Preview 或位置跳转。

## 生产组合层

`desktop_knowledge.py` 从已验证 AppSettings 与共享 AttachmentService 构造：

```text
Loader → Cleaner → Chunker → local Embedding → SQLite
                                           → Retriever → local Grounding
shared Project/Chat/Source/operation repositories
shared coordinator → Lifecycle + Export + ProjectSourceAnswerService
```

Index profile fingerprint 绑定 loader routes/versions、limits、Cleaner/Chunker policy/versions 和完整 embedding space；同一合同不因本机路径变化而改变，输出相关规则变化则使旧 generation stale。

构造会准备本地仓库/SQLite schema，但不访问 Ollama、不下载模型、不启动索引或 lifecycle recovery。Backend 完成 canonical owner 对账后创建 runtime；未完成操作显示在 UI，由用户显式恢复。Export temp 的独立后台清理不会等待断开的外部卷并阻塞 initialize。

## 回答与持久化

`Brain.stream_grounded_chat()` / `stream_grounded_retry()` 复用 generation admission、取消与 commit gate：

1. 验证 active Chat→Project。
2. 在共享 lease 中复核 ownership、published catalog、profile 与准确 generations。
3. 检索闭集，Generator 只能选择本次 opaque Citation IDs。
4. 取消未赢得 commit race 时，原子保存 Assistant text 与完整 proof。
5. Chat Repository 重启后恢复相同 Statement/Citation 数据。

只有 Assistant Message 可带 grounded proof。普通 Retry 替换旧回答时不会保留旧引用；序列化器兼容没有该可选字段的旧 Chat 文件。

生产 `OllamaGroundedAnswerAdapter` 仅允许 loopback HTTP origin，禁用环境 proxy、redirect、retry 和 model pull。它在生成前后核对完整 manifest digest，并固定 non-streaming、无 tools/think、无 truncation、temperature 0 和闭合 JSON schema。请求/响应 bytes、deadline、HTTP framing、UTF-8 和重复 JSON member 均有界验证。

这些约束能验证引用来自授权 evidence，不能证明 summary/inference 的语义正确；UI 保留 statement kind 供复核。

## Desktop Protocol

Protocol v1 通过 `knowledge.management` capability 暴露以下方法：

| Method | 作用 |
| --- | --- |
| `knowledge.list` | Project Sources 与持久操作历史 |
| `knowledge.source.add` / `replace` | Electron 原生选择文件后导入/替换 |
| `knowledge.source.reindex` / `delete` | 重新派生或先撤销后清理 |
| `knowledge.project.rebuild` / `revoke` | 重建完整语料并解除 revoke，或停用回答授权 |
| `knowledge.recover` | 从 durable checkpoint 向前恢复 |
| `knowledge.source.export` | 原生 Save dialog 导出 verified original |
| `request.cancel` | 仅取消仍可取消的准确 lifecycle request |

`chat.stream` / `chat.retry` 的 `useProjectKnowledge` 是本次请求意图；Chat detail 的 `groundedAnswer` 使用关闭字段集合。Python、TypeScript、JSON Schema 和共享 fixtures 对 request/result/event、上限与错误 shape 双端验证。

长操作使用 Python worker。Lifecycle 通过 `knowledge.operation.changed/completed` 发布安全 checkpoint；export 不写 journal，也不伪造 lifecycle event。Renderer 不接收 native paths、content hash、File ID、vectors、Prompt、隐藏推理或 traceback。

## 并发与恢复

一个 Backend 同时只允许一个公开 lifecycle/export task。Python 和 Electron 的全局 admission 还将它与 grounded answer、Project authority write 互斥；切换 Project 不释放 lease。Active lifecycle/export 存在时，冲突的 list、mutation、export、grounded request 和 authority write 在执行前被拒绝。

`BackendSnapshot.activeKnowledgeOperation` 保存 request ID、Project ID 与 cancellable。Export 进入 pending map 后以 `cancellable:false` 出现，可跨 Renderer reload 恢复所有权，目标路径不公开。

Lifecycle 成功校验后发 correlated settled event 与 authoritative state。Export 只有在 receipt 的文件名/MIME/大小与 Save dialog 前认证的 Source 一致后才发 `knowledge-export-settled`；失败走 correlated error。React 用有界 terminal tombstones 拒绝迟到旧 snapshot，防止已结束任务重新变成 active。

取消在安全点前产生 durable cancelled；迟到取消不能回滚已发布事实。Recover/export 的普通 `request.cancel` 返回 `stopped=false`，UI 不提供 Stop；export 在 shutdown 时仍可于原子发布前协作取消并清 temp。恢复最多八次，按 canonical stores 向前收敛。

新 generation 最后发布 catalog；replace/delete/revoke 先 tombstone。失败可能留下待清理数据，不能让旧向量继续获得授权。Shutdown/EOF 有界等待并关闭 runtime，迟到 worker 不能发布私有诊断。

## 自动化覆盖

[文档 fixtures](../tests/fixtures/documents/README.md) 包含小型、确定性的真实 PDF 和 DOCX：

- `atlas-release-notes.pdf`：一页、未加密、可提取文字。
- `lumen-operations-handbook.docx`：标准 OOXML，含段落和两列表格。

内容由贡献者专为测试编写，来源、SHA-256、生成属性和 CC0 waiver 记录在 fixture README。回归经过生产 Attachment、真实 Loader、Cleaning/Chunking 与 Retrieval；模型环节使用确定性 Adapter，无需网络、Ollama、GPU 或下载权重。

测试覆盖同 Project 共享、附件隔离、foreign/archived/partial/stale 拒绝、完整发布与删除恢复，以及协议、UI 状态、导出 reload/Project switch 所有权、receipt、终态竞态、proof 持久化和 Citation 键盘交互。

Electron UI 使用 mock preload。因此自动化能证明结构、授权、持久化和 UI 合同，不代表真实 Ollama/GPU、本机文件对话框或模型语义质量的人工矩阵已执行。

## 验证命令

从仓库根目录运行文档检查和知识链回归：

```powershell
python scripts/check_python_documentation.py
python -m pytest tests/test_desktop_knowledge.py tests/test_ollama_grounding.py tests/test_knowledge_lifecycle.py tests/test_knowledge_export_recovery.py tests/test_real_document_regression.py -q
cd desktop
npm run docs:check
npm run lint
npm run typecheck
npm test
npm run build
```

发布前还需按仓库开发流程执行完整 Python suite、mypy、distribution asset gate 和依赖审计；局部知识测试不能替代全项目检查。

## 能力边界

Project Sources 不自动扫描目录、不因 Workspace binding 导入文件，也不进行跨 Project 搜索或自动开启问答。当前文档加载链没有 OCR、独立 Preview Cache 或原文位置跳转；其他产品模块的联网/视觉能力不改变这里的授权合同。

运行时与安装目录见 [生产数据布局](./13-PRODUCTION-DATA-LAYOUT.md)，模型和素材分发边界见 [MODEL_LICENSE.md](../MODEL_LICENSE.md)。模型权重不随知识库模块提供。
