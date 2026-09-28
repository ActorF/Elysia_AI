# Retriever and Reranking：有界、可预测的本地检索

本文记录 Elysia AI 在 Scope-safe SQLite Vector Store 之后、Grounded Answer 之前的独立 Python 检索边界。当前实现把一个有身份的 Query Embedding 与调用方显式授权的文档 Generation 放进同一精确 Chat/Project Scope，在单个 SQLite 读事务中执行有界暴力余弦 Top-K，再做严格的原文去重和可选 Reranking。它只发布带完整来源证据的候选片段，**不会生成答案，也不会在证据不足时补造来源**。

这一能力仍未接入 `start.py`、`desktop_backend.py`、Desktop Protocol 或 React。存在 `DocumentRetriever` Library **不等于** 用户现在可以在桌面 Chat 中向附件或 Project Sources 提问。

## 1. 完成范围与设计原则

本模块完成六个可独立验证的边界：

1. **Identity-bearing Query**：`EmbeddedQuery` 同时携带 Canonical Float32 单位向量、完整 `EmbeddingModelIdentity` 和实际 Batch/Template Policy；Store 不接受无法证明向量空间的裸向量。
2. **显式授权 Corpus**：每次检索都必须提供 `ExpectedDocumentGeneration` Allowlist。每项绑定完整 `DocumentSource` 与准确 `derivation_fingerprint`，不会从 Scope 中自行发现“可能可读”的文档。
3. **单快照精确 Scope 搜索**：`SQLiteVectorStore.search_scope()` 在一个读事务中验证全部预期 Generation，并流式扫描其全部 Vector Record；任何缺失、过期、损坏或超限都会让整次搜索失败，不发布看似完整的部分 Top-K。
4. **可预测的 Two-Step Retrieval**：v1 使用固定 Query Template、单位向量 Dot Product（即 Cosine Similarity）、确定性排序、阈值和 Top-K；不引入自主规划、工具循环或 Agentic RAG。
5. **证据保留去重**：只按准确 `(chunk kind, text)` 合并重复片段，不执行 Unicode Normalization、Case Folding 或语义猜测；一个 Hit 会保留进入有界原始 Candidate Pool 的每个重复来源 Evidence。
6. **结构上不可信、语义上被授权的可选 Reranker**：Reranker 只能给已经闭合的候选池逐项打分，不能新增、删除、改写或救回低于余弦阈值的片段；Library 会验证返回 Shape、完整排列、Identity 和 Score 范围，但相关性判断本身仍以调用方选定的本地模型为语义权威。配置后若失败或返回不完整结果，整次检索 Fail Closed，不静默退回另一种排序策略。

所有公开 Retrieval/Reranker Domain 值都是 Frozen + Slots Dataclass。Retriever 会重建 Scope、Source、Embedding Identity、Policy、Filter、Mapping 和 Adapter Result 快照。所有注入组件在 Shape、Mutation 与 Diagnostic 层面都按不可信输入处理；但调用方选定的 Embedding/Reranker 模型仍分别是 Vector 与 Relevance Score 的语义权威，Library 无法自行证明模型计算“正确”。Vector Store 是负责验证持久化内容、Lineage 和 Cosine 的可信语义边界，Retriever 仍会防御性复核其公开 Shape、Scope 和 Generation，并净化其异常。仅凭 Store 返回的元数据无法从密码学上重新证明 Passage 或 Score，因此未来注入其他 Store 实现时必须满足同一完整性合同。结果不包含查询文字、向量、SQLite Row、本机路径或底层原始错误。

## 2. 数据流与当前集成边界

```text
exact AttachmentScope
  + exact query text
  + ExpectedDocumentGeneration allowlist
  + exact metadata filter / retrieval policy / resource limits
  → DocumentRetriever
      → DocumentEmbeddingService.embed_query()
          fixed query prefix + pinned embedding-space identity
          → EmbeddedQuery(identity + policy + canonical unit vector)
      → SQLiteVectorStore.search_scope()
          one read transaction
          → authenticate every expected generation
          → validate and brute-force score every authorized record
          → bounded deterministic cosine candidate pool
      → threshold + exact (kind, text) deduplication
          → RetrievalHit + evidence admitted to the bounded raw pool
      → optional DocumentReranker
          closed complete score permutation only
      → deterministic Top-K RetrievalResult

  ✗ no answer generation, prompt composition, or citation rendering
  ✗ no production composition-root, Desktop Protocol, or React wiring
  ✗ no Project-to-Chat Source authorization discovery
  ✗ no automatic index lifecycle, reindex, delete propagation, or jobs
  ✗ no Agentic RAG, tool calls, ANN, remote fallback, or bundled reranker
```

`DocumentRetriever` 是同步 Library Service。它不选择哪些 Project Sources 对当前 Chat 可见；下游 [Project Sources](./10-PROJECT-SOURCES.md) 已依据经过验证的 Chat/Project 关系与显式 catalog 构造准确 Scope 和 Generation Allowlist。它也不把 `RetrievalHit` 注入 Prompt；这些下游职责由独立 [Grounded Answers and Citations](./09-GROUNDED-ANSWERS-CITATIONS.md) Library 承担，但整条链仍未接入生产或桌面层。

## 3. Query Identity 与显式 Generation Allowlist

### 3.1 `EmbeddedQuery`

`DocumentEmbeddingService.embed_query()` 现在返回 `EmbeddedQuery`，而不是单独的 `EmbeddingVector`。该值绑定：

- Embedded Query Schema Version；
- 完整 `EmbeddingModelIdentity` 与 `embedding_space_id`；
- 实际 `EmbeddingBatchPolicy`；
- Item ID 固定为 `query` 的 Canonical Float32-le 单位向量。

Query 原文先使用 [Local Embeddings and Vector Store](./07-LOCAL-EMBEDDINGS-VECTOR-STORE.md) 定义的完整 Prefix，再进入同一固定本地 Embedding 空间。Prefix 与 Query 的合计上限仍是 `2,000` Unicode Code Points，因此公开 Query 上限为 `1,886` Code Points。`EmbeddedQuery` 不保存 Query 原文；Store 会在扫描前核对完整 Identity、Policy、向量维度、单位范数和 Float32 Canonical Form，拒绝同维但来自其他模型、Manifest、Template 或 Policy 的向量。

### 3.2 `ExpectedDocumentGeneration`

每个允许参与搜索的文档都必须由调用方显式提供：

```text
DocumentSource
  = exact AttachmentScope
  + ownership link_id
  + file_id / safe file_name / media_type / size

ExpectedDocumentGeneration
  = DocumentSource
  + derivation_fingerprint
```

Allowlist 必须是精确 Tuple、Link ID 不重复，而且全部 Source 必须属于本次请求的准确 Scope。Retriever 不使用前缀、通配符、“当前 Project”猜测或 SQLite 全库枚举来扩大 Corpus。空 Allowlist 或被 File/Media Filter 排空的 Allowlist 会直接返回空结果，不调用 Embedder、Store 或 Reranker。

Store 还会逐个证明：预期 Link 存在、Scope/Source Metadata 完全一致、Derivation 未过期、Embedding Space 相同。任何一个授权 Generation 缺失或不匹配都会拒绝整次搜索；不能悄悄忽略坏文档后把剩余结果称为完整检索。

## 4. SQLite 单事务暴力余弦 Top-K

`SQLiteVectorStore.search_scope()` 使用一个 `BEGIN` 读事务建立一致快照：

1. 按准确 `(scope_kind, scope_id)` 和 Allowlisted `link_id` 读取 Generation Header。
2. 复核每个 Header 的 Source、Derivation、Embedding Space、Lineage JSON、Checksum、Record Count 与累计预算。
3. 按 `link_id, ordinal` 流式读取全部授权 Record；逐项复核 Chunk Metadata、完整 Source Mapping、Embedding/Chunk ID、Vector BLOB 长度、Checksum、Finite Float 和 Generation 总计。
4. Metadata Filter 只决定一个已验证 Record 是否参与评分；它不会绕过完整性与总量证明。
5. 因 Document 与 Query Vector 都是单位向量，使用 `math.fsum(document[i] * query[i])` 得到 Cosine Similarity；分数夹在允许的浮点容差内并量化为固定八位小数。
6. Threshold、`candidate_k` 保留与 Top-K 排序都明确作用于这个八位小数的公开分数，再按 Link、Ordinal、Chunk ID 与 Embedding ID 确定性破除同分。Raw Dot Product 相差不足 `0.5 × 10^-8` 的候选在 v1 中有意视为同分；刚好跨越 Threshold 但量化到同一公开分数的值也采用相同语义，而不是暴露平台级浮点噪声。

当前实现选择精确暴力扫描，而不是 ANN。它能让小型本地 Corpus 的结果、失效规则和完整性验证可预测，且不需要额外索引格式或第三方向量引擎。作为代价，每次查询必须在明确 Record、Payload 和 Deadline 预算内扫描全部授权 Generation；超限会返回稳定错误，而不是静默只扫一部分。

Vector Store 打开阶段先在独立 Store Timeout 内验证整个私有 Database 的 Schema/Catalog、`foreign_key_check`、`quick_check(1)` 与物理预算；该完整性阶段可能读取 Allowlist 外的数据，但不会把它们解码为 Retrieval Candidate。随后 Authorized Vector Scan 才启动自己的 SQLite Progress Handler 和 Python Monotonic Deadline；调用方的 Record/Payload 限制约束这一扫描阶段，而不是前置整库验证。事务结束前还会再次检查 Database/Sidecar 物理字节与逻辑 Page Budget。查询计划、调用方 Allowlist 顺序和等分 Row 顺序不能改变最终结果。

## 5. Policy、Filter、阈值与去重

### 5.1 默认 Retrieval Policy

| 字段 | v1 默认值 | 硬上限 |
| --- | ---: | ---: |
| 最终 `top_k` | `5` | `20` |
| 初始 `candidate_k` | `20` | `64` |
| 最小 Cosine Similarity | `0.35` | `1.0` |

`candidate_k` 不得小于 `top_k`。它限制的是去重前的原始 Candidate Pool：高分重复片段可能占用多个位置，所以去重后不保证一定填满 `top_k`，池外的重复位置也不会被发布为 Evidence。调用方若希望降低重复片段挤占唯一结果的概率，应让 `candidate_k` 大于 `top_k`，但不能超过硬上限。最低相似度只接受 `0.0–1.0` 的有限 Float；负相似片段不会通过 v1 Policy。若没有片段达到阈值，返回 `hits=()`，不会选择“最不差”的 Chunk 作为伪证据。可选 Reranker 只看到阈值之后的候选，不能改变这一资料充分性边界。

### 5.2 闭集 Metadata Filter

`RetrievalMetadataFilter` 支持四种精确 Filter：

- `file_ids`；
- `media_types`；
- `chunk_kinds`：`prose` / `code` / `table`；
- 一基 `page_numbers`。

同一字段内使用 OR，不同字段之间使用 AND；空 Tuple 表示该字段不限制。Filter Value 必须合法、唯一、总数不超过预算。v1 不支持文件路径、任意 SQL、Substring、Regex、时间偏好或调用方定义的 Predicate。

File 与 Media Filter 会先缩小 Generation Allowlist；Store 对 Source 和 Chunk Metadata 再执行相同精确判断；Retriever 发布前第三次复核候选未越过请求。重复检查用于发现 Store 合同违例并保持输出边界闭合，不能被解释为新的授权来源。

### 5.3 Exact Deduplication

Candidate 先按 Cosine 和稳定身份排序，再按准确 `(kind, text)` 分组。不同 Kind 即使文字相同也不合并；大小写、组合字符、空白、换行或代码缩进不同也保持独立。这样避免 Normalization 或 Semantic Deduplication 把含义不同的源码和原文合并。

每个 `RetrievalHit` 的分数取其 Evidence 中最高的 Cosine；`evidence` 保留有界原始 Candidate Pool 内每个重复位置的 Source、Generation、Embedding/Chunk ID、Ordinal、Page、Source Mapping 和各自 Cosine。去重减少送给下游的重复文字，但不承诺收集池外的全部重复来源；这是避免为完整 Evidence 而物化整个 Scope 的明确资源取舍。Evidence 数量继续受扫描、候选文字和 Mapping 预算约束。

## 6. 可选 Reranker 的不可信边界

当前仓库只定义 `DocumentReranker` Protocol 与版本化 Domain，**没有内建或捆绑任何 Reranker Adapter、模型或权重**，也没有因此引入新的模型许可。未来 Adapter 必须公开：

- `RerankerIdentity`：Provider、Adapter ID/Version、Model Tag、Model Digest、固定 `relevance-0-to-1` Score Semantics 和 Canonical Identity Fingerprint；
- `RerankerPolicy`：最多 `64` 个输入、Query 最多 `1,886` Code Points、单片段最多 `2,000`、Batch 最多 `128,000`，且 `truncate=false`；
- `rerank(RerankerRequest) -> RerankerBatch`。

Retriever 给每个去重后的准确 `(kind, text)` 派生 opaque `candidate_<sha256>` ID。Request Fingerprint 绑定 Query、完整有序 ID/Text 与 Policy。返回 Batch 必须保持同一 Identity、Policy 和 Request Fingerprint，并按请求顺序为每个 Candidate **恰好返回一次** `0.0–1.0` 有限 Relevance Score。

Reranker 不能返回新 ID、漏项、重复项、改写文字、改变 Evidence 或返回部分排序。Retriever 会在调用前后复核 Adapter Identity/Policy 和 Request Object 未被篡改；最终按 Reranker Score、Cosine 与稳定身份排序。若配置了 Reranker，而 Adapter 抛错、变更身份、篡改请求或返回坏结果，则抛出脱敏的 `RerankerFailedError`。不自动回退到 Cosine，是因为那会在调用方不知情时发布与所选 Policy 不同的排名。

## 7. Result、Evidence 与“资料不足返回空”

`RetrievalResult` 发布：

- Schema Version、准确 Scope、Retrieval Policy 与 Metadata Filter；
- 实际 Embedding Identity；若 Eligible Corpus 在 Embedding 前已证明为空，则为 `None`；
- 可选的 Reranker Identity；
- 去重后的 `candidate_count` 与最多 `top_k` 个 `RetrievalHit`。

`RetrievalHit` 只包含准确 Chunk Kind/Text、最佳 Cosine、可选 Reranker Score 和一个或多个 `RetrievalEvidence`。Evidence 保留安全文件名、媒体类型、大小、Page 与精确 Block/Cell/Offset Mapping，但不包含 Vector、本机路径或 SQLite Storage Detail。

两种正常情况返回真实空结果：

1. Allowlist 为空，或 File/Media Filter 证明没有 Eligible Generation；此时不会触发任何外部依赖。
2. Corpus 有效，但没有 Candidate 达到 `minimum_cosine_similarity`；此时结果携带 Embedding Identity，但 `candidate_count=0`、`hits=()`。

缺失、过期、损坏、超预算或模型不匹配不是“资料不足”，而是 typed failure。下游 Grounding Library 已把真正的空结果处理为“不依据文档回答”的确定性信号，并且不会在空命中时调用 Generator；但 Retriever 本身不生成用户可见答复。

## 8. 资源预算

`RetrievalLimits` 的系统硬上限为：

| 资源 | 上限 |
| --- | ---: |
| Query Text | `1,886` Unicode Code Points |
| Expected Document Generations | `128` |
| 全部 Metadata Filter Values | `256` |
| Scanned Vector Records | `10,000` |
| Scanned `lineage_json` / `chunk_json` / `vector_blob` Payload | `64 MiB` |
| Candidate Text | `128,000` Code Points |
| Source Mappings | `100,000` |

调用方可以缩小除 Query 固定预算外的限制，不能放大经过审计的系统上限。Store 自身还有独立的 Search Ceiling：`128` Documents、`10,000` Records、`64` Results 与 `128 MiB` Payload；实际执行使用 Retriever 与 Store 中更小的限制。默认配置下，Connection/Open 完整性验证与 Authorized Search 各有独立的最长 `5` 秒 Deadline，因此一次 API 调用不是总计 `5` 秒；此外仍受 Database/Sidecar `2 GiB` 和 `524,288` SQLite Page 限制。

这些预算限制约束 Authorized Scan 的验证工作、传入 Python 的可变长 JSON/Vector Payload、候选内存和 Reranker 输入。固定宽度 Identifier/Checksum 不计入 Payload 总数，但会在 SQL 投影表达式内按准确 Storage Type 与 UTF-8 Byte Length 验证，损坏值不会作为任意长度字符串进入 Python。上述预算不替代前置整库完整性检查、模型 Token Window、整机磁盘 Quota 或后台并发调度器。

## 9. 稳定错误与恢复语义

Retriever 的公开错误闭集包括：

- `RetrievalValidationError`：请求、Policy、Filter、Scope、Generation 或 Domain Shape 非法；
- `RetrievalLimitError`：Query、Corpus、Filter、Scan、Payload、Candidate Text、Mapping 或 Reranker Batch 超限；
- `RetrievalUnavailableError`：所需本地 Query Embedding 依赖不可用；
- `RetrievalFailedError`：Query Embedding 或 Vector Store 无法发布完整、安全的结果；
- `RerankerFailedError`：已配置 Reranker 失败或返回不完整/不可信结果。

底层仍使用 `VectorStoreIncompleteError`、`VectorStoreStaleError`、`VectorStoreModelMismatchError`、`DocumentCorruptError` 与 `VectorStorePersistenceError` 精确区分 Store 内原因；跨 Retriever 边界后会净化为不含路径、Query、Passage、URL、SQL 或 Adapter 原始消息的稳定类别。正确恢复方式是从可信 Attachment 重新 Load/Clean/Chunk/Embed，原子发布准确 Generation，或修复明确配置的本地依赖；不是忽略坏文档、降低完整性检查或手工伪造 Fingerprint。

## 10. 文件职责与测试

| 文件 | 职责 |
| --- | --- |
| `documents/embedding.py` | 新增 Identity-bearing `EmbeddedQuery`；Query Service 发布完整 Space/Policy/Canonical Vector，而不是裸向量 |
| `documents/retrieval.py` | Retrieval/Reranker Domain、Expected Generation、Filter/Policy/Limits、确定性阈值/去重/排序、可选不可信 Reranker 与稳定错误 |
| `documents/vector_store.py` | 在既有 Scope-safe Store 上新增单事务、全 Generation 验证的暴力 Cosine 搜索与 Search Budget |
| `documents/__init__.py` | 导出稳定 Retrieval/Reranker 公共 API |
| `tests/test_document_embedding.py` | `EmbeddedQuery` Identity、Policy、Canonical Float32 与恶意 Adapter 回归 |
| `tests/test_document_retrieval.py` | 真实 SQLite Top-K/阈值、Scope/Allowlist、Filter、Missing/Stale、Exact Dedup、稳定 Tie、空 Corpus、可选 Reranker Fail-closed、Space Mismatch 和 Path/Vector 隐私 |

单元测试使用 Fake Embedder/Reranker 与临时 SQLite，不需要真实 Ollama、Embedding Model 或 Reranker Model：

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe -m pytest tests\test_document_embedding.py tests\test_document_vector_store.py tests\test_document_retrieval.py -q
.venv\Scripts\python.exe scripts\check_python_documentation.py
cd desktop
npm run docs:check
```

## 11. 明确非目标与下一步

当前模块不提供：

- Grounded Answer Generation、Prompt Composition、Citation Selection/Rendering、来源事实/概括/推断标签或 Prompt-injection Isolation；这些已由下游独立 Grounding Library 实现，仍不属于 Retriever；
- Project Sources 到 Project Chat 的生产接线；授权 Library 已在 Module 7 完成；
- Attachment Derived Relation、自动索引、替换/删除传播、Progress、Cancel、Retry 与 Crash Recovery；这些属于 Module 8；
- Sources/索引状态/引用跳转 UI、Desktop Protocol 或 React 文件问答入口；这些属于 Module 9；
- ANN、Hybrid/BM25 Search、Semantic Deduplication、Diversity/Recency Boost、Cross-scope Union、Agentic RAG 或 Tool Planning；
- 内建 Reranker Adapter、Reranker 模型/权重、远程 Reranking Fallback、模型下载或新的素材/模型许可。

下游 [Grounded Answers and Citations](./09-GROUNDED-ANSWERS-CITATIONS.md) 已能只从有界 `RetrievalHit` 完整前缀构造有限上下文，在 Retriever 空命中时不调用 Generator，并把 Generator 选择的 opaque Citation 解析为可信文件名、页码和准确位置；[Project Sources](./10-PROJECT-SOURCES.md) 已在其上建立 Chat-derived 授权、共享语义和安全 Instructions。接下来仍需完成知识生命周期、生产 Generator/Composition Root、Protocol 与 UI；在这些边界完成前，不能声称桌面 Chat 已能检索、回答或引用用户文件。
