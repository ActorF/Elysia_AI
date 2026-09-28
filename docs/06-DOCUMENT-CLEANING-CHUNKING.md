# Document Cleaning and Chunking：保守清洗与可重复分块

本文记录 Elysia AI 在 `LoadedDocument` 之后、Embedding 之前的纯 Python 派生边界。当前实现把可信 Loader 的有界原始结构转换为带完整来源映射的 `CleanedDocument` 与 `ChunkedDocument`；这一纯派生层自身不会读取文件、写入索引、调用模型或生成 Embedding。下游 [Local Embeddings and Vector Store](./07-LOCAL-EMBEDDINGS-VECTOR-STORE.md) 与 [Retriever and Reranking](./08-RETRIEVER-RERANKING.md) 已作为独立 Library 完成，但整条文档链仍未接入生产 Composition Root、Desktop Protocol 或 React 文件问答入口。

## 1. 完成范围与设计原则

这一层遵守四条原则：

1. **Pure**：相同输入、版本和 Policy 总是得到相同输出；运行过程不访问文件系统、时钟、网络、Tokenizer、Embedding Model 或随机源。
2. **Versioned**：Loader、Cleaner、Chunker、Schema 与输出相关 Policy 都进入可重复的派生身份；规则变化必须产生新身份，不能静默复用旧 Chunk。
3. **Conservative**：不把排版猜测当事实。除严格证明的重复 PDF 页眉外，所有 Loader 已接受的文字与表格值逐 Unicode Code Point 保留。
4. **Provenance-preserving**：每个保留片段、审计删除和 Chunk 映射都指回 `LoadedDocument` 的 Block/Page/Cell/Offset；这些坐标不冒充原始文件内部坐标。

当前 Cleaner 不执行通用 Trim、Whitespace Collapse、Unicode Normalization、Mojibake Repair 或 Replacement Character 修复。严格文本 Loader 已在更早边界拒绝坏 UTF、NUL、Surrogate 和不允许的控制字符；PDF/DOCX Parser 输出如果语义不明确，则保留原样或由 Loader 整体失败。这样可以避免“清洗”悄悄改变代码缩进、CSV Cell、组合字符、ZWJ、双向文字、CRLF 或作者有意留下的空白。

## 2. 数据流与当前集成边界

```text
Scope + ownership link_id
  → DocumentProcessingService
  → DocumentSourceLoader / DocumentLoaderService
  → versioned, path-free LoadedDocument
  → ConservativeDocumentCleaner
      exact retained slices + audited omissions
  → CleanedDocument
  → StructureAwareDocumentChunker
      prose / code / table JSONL chunks
  → ChunkedDocument
      deterministic IDs + Page/Block/Cell/Offset mappings

  → downstream DocumentEmbeddingService + SQLiteVectorStore
      pinned local embedding space + exact Scope/Link generations

  → downstream DocumentRetriever
      explicit generation allowlist + bounded Top-K/filter/dedup/rerank

  ✗ this pure stage performs no model call or index write
  ✗ no Attachment Derived Relation or automatic lifecycle jobs
  ✗ no Citation, Prompt injection defense, or Chat wiring
  ✗ no Desktop Protocol or Renderer endpoint
```

`DocumentProcessingService` 默认组合 `ConservativeDocumentCleaner` 与 `StructureAwareDocumentChunker`，也允许注入实现 `DocumentSourceLoader`、`DocumentCleaner`、`DocumentChunker` Protocol 的测试或替代 Adapter。Loader 仍是唯一读取 Attachment Bytes 的组件；Cleaner 和 Chunker 只接收重新构造且彼此隔离的领域快照，不接收真实路径、文件句柄或 Parser 对象。

Pipeline 不信任注入 Adapter 的返回值。原始请求、Loader 权威结果、Cleaner 输入、Cleaned 权威结果、Chunker 输入与最终发布结果分别使用递归重建的快照；即使 Adapter 绕过 Frozen Dataclass 修改自己持有的嵌套 Scope、Source、Table、Policy、Limits、Span 或 Chunk，也不能反向改写已经验证的 Lineage。Pipeline 还会独立复核请求的 Scope/Ownership、Loader Result 类型、Cleaner Producer/Policy/Limits、Piece-table 的逐 Block 完整分区、Cleaned Fingerprints、Chunk Lineage/Policy、Mapping 顺序与 Bounds、每段 Text Mapping 的准确来源文字，以及全部 Table Chunk 能否完整重建 Canonical JSONL Projection。验证成功后才发布与 Adapter 对象图隔离的 `ChunkedDocument`。这一 Service 仍只返回内存结果；独立 `DocumentIndexingService` 已可继续生成 Embedding 并把完整 Lineage 写入 Scope-safe SQLite Store，`DocumentRetriever` 也可消费调用方显式授权的准确 Generation。Attachment Manifest 的 Derived Relation、删除传播、自动重新索引和任务崩溃恢复仍未实现。

## 3. 坐标与来源语义

所有 `start_code_point` / `end_code_point` 都是 **零基、左闭右开** 的 Unicode Code Point 区间，并且只针对 Loader 已发布的 `LoadedDocument` 表示：

- `DocumentTextSpan` 指向一个非 Table `DocumentBlock.text`，包含 `block_ordinal`、Code-point Range 与可选一基 PDF `page_number`。
- `DocumentTableCellSpan` 指向一个 Ragged Table Cell，额外包含零基 `row_index` / `column_index`；空 Cell 可以用相等的 Start/End 作为投影标点锚点。
- `ChunkSourceMapping` 再把 Chunk 内的 Code-point Range 映射到上述来源 Span。

这些 Offset **不是**：

- 原始文件 Byte Offset；
- PDF Content Stream、Text Operation、Glyph 或视觉 Bounding Box；
- DOCX ZIP Member、XML Node 或 Run Offset；
- CSV 源 Byte、Quoted Field Token 或磁盘行号。

Loader 可能执行严格解码或 Parser 提取，因此只有 `LoadedDocument` 是本层可准确复现的来源坐标系。未来 Citation UI 若需要原文件视觉定位，必须由新的格式专属 Provenance Contract 明确提供，不能重新解释这里的 Code-point Offset。

## 4. `CleanedDocument` Contract

`documents/cleaning.py` 定义不可变的清洗结果：

- `LoadedDocumentProvenance` 冻结 Loaded Schema、Source、Format、Loader ID/Version 与实际 Load Limits。
- `CleanedTextBlock` 保存准确文字以及组成它的有序 `retained_spans`。
- `CleanedTableBlock` 原样复用 `DocumentTable`，不补齐 Ragged Row、不删空 Cell、不改 Cell Text。
- `CleaningOmission` 记录闭集 Reason 与被删除的准确来源范围。
- `CleanedDocument` 携带 Cleaner ID/Version、Policy、Processing Limits、标题、页数、两个 Fingerprint、Blocks 与 Omissions。

默认 Producer 为：

| 字段 | 当前值 |
| --- | --- |
| Cleaned Schema | `1` |
| Cleaner ID | `conservative-piece-table` |
| Cleaner Version | `1.0.0` |
| PDF 页眉最少页数 | `3` |
| PDF 页眉最大长度 | `256` Code Points |

### 4.1 唯一删除规则：严格重复 PDF 页眉

只有同时满足以下全部条件，Cleaner 才删除每页最前方的一行及其紧随的 CRLF、CR 或 LF：

1. 文档格式准确为 PDF，并且实际 `page_count` 达到 Policy 的最少页数。
2. 每个 PDF Page 都恰好有一个 Loaded Block；空页或多 Block Page 均视为证据不足。
3. 每个 Block 都是带一基 `page_number` 的 Paragraph。
4. 每页首行都存在显式 CRLF、CR 或 LF 结束符。
5. 首行包含有意义文字、长度不超过上限，并在每页逐 Code Point 完全一致。
6. 删除后每一页仍包含有意义正文。
7. 该首行不等于 Loader 提供的 Document Title。

任何一项不成立，就对整份文档执行零删除，而不是“尽量删掉几页”。每次成功删除都会生成一条 `repeated_pdf_page_header` Omission；保留正文从准确的 Line Terminator 之后开始。此 All-or-nothing 规则避免把章节标题、短文正文、扫描缺页或 Parser Layout 差异误判为 Boilerplate。

### 4.2 明确保留的内容

Cleaner 原样保留：

- CRLF、CR、LF、Unicode Line/Paragraph Separator；
- Tab、代码缩进、段落内与段落尾空白；
- Combining Marks、ZWJ、Emoji、CJK、双向控制已被 Loader 合法保留的内容；
- Replacement Character（如果它是 Parser 的实际输出，而非 Loader 对坏 Byte 的静默替换）；
- Table 的 Ragged Shape、空 Cell、空白 Cell、Quote、Backslash 与换行。

因此“无效空白和坏字符”的处理边界是：能由 Loader 客观判定为无效的输入在加载时失败；无法客观判定的内容不由 Cleaner 猜测修改。

## 5. Structure-aware Chunking

`documents/chunking.py` 的 `StructureAwareDocumentChunker` 把 `CleanedDocument` 纯粹派生为三种闭集 Chunk：`prose`、`code`、`table`。默认最大 Chunk 长度为 2,000 Unicode Code Points；v1 的 Overlap 固定为 `0`，避免重复来源范围让 Citation 与资源计费产生歧义。

### 5.1 Prose

- `title` 与 `heading` 总是开启新的结构边界。
- 短的相邻 Paragraph 可以用准确的 `\n\n` 合并，但不会跨 PDF Page。
- 同一长 Block 的后续片段不会回填到其他 Block；每个片段保持连续来源映射。
- 达到长度上限时，按固定优先级选择窗口内最后一个边界：空白行、逻辑换行、句末标点（`. ! ? 。！ ？`）、ASCII Space/Tab，最后才在硬 Code-point 上限截断。
- 当 Chunk Cap 至少能容纳两个 Code Points 时，CRLF 作为一个完整换行边界，不在 `\r` 与 `\n` 中间切开；只有显式配置为 `1` 的极端 Cap 才会被迫拆开，但连接 Chunk 后仍逐字还原原文。

插入的 `\n\n` 是版本化的派生分隔符，不冒充来源文字，因此 Source Mapping 可以在这两个 Chunk-local Code Points 上留下明确空隙。

### 5.2 Code

Code Block 与其他结构完全隔离，不与 Prose 或另一个 Block 合并。Chunker 优先在完整逻辑行边界切分；单行本身超过上限时才按硬 Code-point 上限拆分。缩进、空白与所有 Line Ending 原样保留，不解析 AST，也不执行源码。

### 5.3 Table JSONL Projection

Table 使用版本化的 `jsonl-v1` Canonical Projection：每个 Ragged Row 是一个 JSON Array，行间只插入 LF。例如：

```text
["name","value"]
["爱莉希雅","a\tb"]
[""]
```

Projection 规则固定为：

- 保留非 ASCII 字符，不执行 Unicode Escape 或 Normalization；
- 只对 `"`、`\\`、Tab、LF、CR 使用稳定 JSON Escape；
- 保留 Ragged Row 与空 Cell，不自动补列；
- 优先保持完整 Row/Cell；单个投影 Cell 超限时以有界 Piece 流式拆分；
- JSON Quote、Comma、Bracket、Escape 与行间 LF 是派生语法，通过相关 Cell Span 锚定，不冒充原始 Cell 字符。

`table_projection_version` 参与派生 Fingerprint；任何投影语法变化都必须同时更新 Chunker Version 或 Policy，并重新生成所有受影响 Chunk。

## 6. 确定性身份与失效规则

实现使用 Canonical UTF-8 JSON（固定字段形状、排序键、无 NaN、无 Locale 或 Dataclass `repr`）和 SHA-256 建立三层身份：

1. `document_fingerprint`：绑定 Loaded Schema、Scope/Link/File Identity、Format、Loader ID/Version、Title/Page 与完整 Raw Blocks。
2. `cleaning_fingerprint`：绑定 Document Fingerprint、Source、Cleaner ID/Version、Cleaning Policy、Cleaned Blocks 与全部 Omissions。
3. `derivation_fingerprint`：绑定上述 Lineage、Load/Processing Limits、Chunker ID/Version、Chunking Policy、Title 与 Page Count。

每个 `chunk_id` 再绑定 Derivation Fingerprint、Chunk Ordinal/Kind/Text/Page 与完整 Source Mappings，格式为 `chunk_<64 lowercase hex>`。同一输入与配置会逐字得到相同 Chunk 和 ID；Source Scope 或 Ownership Link 不同，即使 Bytes 相同，也不会共享身份。

任何持久化消费者都必须把以下任一变化视为缓存失效并从可信 Original 重新 Load/Clean/Chunk，而不是沿用旧 ID：

- Loaded Schema、Loader ID/Version、Parser 行为或 Load Policy 改变；
- Cleaner ID/Version、Cleaning Policy 或 Cleaner 输出改变；
- Chunked Schema、Chunker ID/Version、Chunking Policy、Table Projection 或 Processing Limits 改变；
- Source Scope/Link/File Identity、Metadata、Title/Page 或文档内容改变。

当前 Cleaning/Chunking 模块只定义可验证的派生身份，不写 Manifest Derived Relation，也不执行自动 Reindex/Delete Propagation。已实现的 `SQLiteVectorStore` 保存完整 Lineage，并在读取时以 `derivation_fingerprint` 不匹配作为稳定 Stale Error；它不会自动决定何时重建。

## 7. 资源预算

`DocumentProcessingLimits` 同时约束 Cleaner 输出和 Chunk Object Graph；默认值为：

| 资源 | 默认上限 |
| --- | ---: |
| Cleaned Code Points | 4,000,000 |
| Cleaned Blocks | 50,000 |
| Cleaning Omissions | 2,000 |
| Chunks | 10,000 |
| 全部 Chunk Code Points | 16,000,000 |
| 单 Chunk Source Mappings | 4,096 |
| 全部 Source Mappings | 1,000,000 |

预算在增量构造期间和不可变 Domain 构造时重复检查。Chunk Cap 不能大于 Total Chunk Output Limit；Mapping 的单 Chunk Limit 不能大于总 Limit。长度统一按 Python Unicode Code Points 计算，不混用 UTF-8 Byte、UTF-16 Code Unit 或 Token Count。

这些是资源和可验证性上限，不是模型 Context Window。Token-aware Rechunking 若以后加入，必须是新的、显式版本化的 Producer，而不能在当前 ID 下偷偷改变边界。

## 8. 稳定错误

本层继续使用 `DocumentError` 层级：

- `DocumentValidationError`：Domain 值、Policy、Producer Identity、Fingerprint、Page/Ordinal 或 Mapping 不一致；
- `DocumentContentLimitError`：清洗文字、Block/Omission、Chunk 或 Source Mapping 超过显式预算；
- `DocumentProcessingFailedError`：Cleaner、Chunker 或 Processing Pipeline 内出现无法安全分类的意外失败。

`DocumentProcessingService` 保留已经稳定分类的 `DocumentError`；`MemoryError` / `RecursionError` 映射为稳定的 Content Limit，其他未知 Adapter Exception 映射为 `DocumentProcessingFailedError`。底层异常通过 Exception Chaining 留给可信日志，不把本机路径、Parser 对象或任意错误原文放入公共结果。构造期缺少 Loader 等编程错误仍可以立即使用 `TypeError`。

## 9. 文件职责与测试

| 文件 | 职责 |
| --- | --- |
| `documents/cleaning.py` | Cleaning Domain、Loaded Provenance、Piece-table Span、严格 PDF 页眉删除、处理预算与 Canonical Fingerprint |
| `documents/chunking.py` | Chunk Domain、结构/长度边界、Code 分块、Table JSONL Projection、Source Mapping、Derivation Fingerprint 与 Chunk ID |
| `documents/protocol.py` | Source Loader、Cleaner 与 Chunker 的最小 Adapter Protocol |
| `documents/pipeline.py` | 默认三步组合；复核 Scope/Ownership、Piece-table 完整重建、Fingerprint/Lineage 与 Chunk Mapping/Projection 后才发布结果 |
| `documents/exceptions.py` | Loader 与 Processing 共用的稳定、路径无关错误层级 |
| `documents/__init__.py` | Document Processing 的稳定公共 API |
| `tests/test_document_cleaning.py` | Lossless Unicode/Whitespace/Table、严格页眉证据、Span/Omission、Fingerprint 与预算回归 |
| `tests/test_document_chunking.py` | Prose/Code/Table Boundary、JSONL Escape、Page/Offset Mapping、确定性/失效与资源预算回归 |
| `tests/test_document_processing_pipeline.py` | 默认端到端组合与恶意 Loader/Cleaner/Chunker 的 Scope、Lineage、Piece-table、Mapping、Projection 和错误映射拒绝 |

验证命令：

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe -m pytest tests\test_document_cleaning.py tests\test_document_chunking.py tests\test_document_processing_pipeline.py -q
.venv\Scripts\python.exe scripts\check_python_documentation.py
cd desktop
npm run docs:check
```

## 10. 明确非目标与下游边界

当前模块不提供：

- 模糊 Boilerplate Detection、语言改写、拼写修复、OCR 或视觉 Layout Reconstruction；
- Tokenizer-aware、Embedding-aware、Overlap 或 Query-specific Chunking；
- Derived Chunk 的磁盘持久化、Attachment Manifest 登记、Job Progress、Cancel 或 Crash Recovery；
- Embedding、Vector Store、Retrieval 与 Grounded Answer/Citation 不是本纯派生模块的职责；这些能力已在下游独立 Library 中实现，但生产接线仍未完成；
- Prompt Injection Detection/Isolation；
- `start.py` / `desktop_backend.py` 生产接线、Desktop Protocol、React Preview 或“向文件提问”UI。

Local Embeddings/Vector Store 与 Retriever/Reranker 已分别完成，详见 [Local Embeddings and Vector Store](./07-LOCAL-EMBEDDINGS-VECTOR-STORE.md) 和 [Retriever and Reranking](./08-RETRIEVER-RERANKING.md)；有限 Prompt、三类结构化陈述和可信 Citation 也已在 [Grounded Answers and Citations](./09-GROUNDED-ANSWERS-CITATIONS.md) 中作为独立 Library 完成，Chat-derived 授权与共享语义见 [Project Sources](./10-PROJECT-SOURCES.md)。生产 Generator、知识生命周期和桌面接线完成前，仍不能声称 Project Sources 已可被桌面 Chat 检索、引用或用于回答。
