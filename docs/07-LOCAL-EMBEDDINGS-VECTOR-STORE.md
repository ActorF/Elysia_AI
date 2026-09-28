# Local Embeddings and Vector Store：本地向量与 Scope-safe 索引

本文记录 Elysia AI 在 `ChunkedDocument` 之后的独立 Python Embedding 与索引边界。当前实现使用固定的本地 Ollama 模型空间，把每个向量绑定到精确 Chunk Lineage，并由标准库 SQLite 按 Chat/Project Scope 存储。它仍未接入 `start.py`、`desktop_backend.py`、Desktop Protocol 或 React；存在本地索引 Library **不等于** 桌面 Chat 已能使用附件。

## 1. 完成范围与设计原则

本模块完成了四个可独立验证的边界：

1. **固定向量空间**：模型 Tag、完整 Manifest Digest、Adapter ID/Version、维度、归一化和 Document/Query Template Version 共同定义 `embedding_space_id`。
2. **严格本地 Adapter**：只接受 Loopback Ollama，验证已安装 Artifact 的声明身份，限制 Batch/输入/响应，且不触发模型下载。
3. **精确 Lineage**：`EmbeddedChunk` 保留 Chunk ID、Ordinal、Kind、Text、Page 和全部 Block/Cell/Offset Mapping；Embedding ID 绑定 Chunk ID、Derivation Fingerprint 与 Embedding Space。
4. **Scope-safe 持久化**：SQLite Store 对每个操作强制精确 `AttachmentScope` 和 Ownership `link_id`，支持替换、列表、单项读取、删除与整 Scope 原子重建。

所有公开 Domain 值都是 Frozen + Slots Dataclass。Embedding Service 与 Store 把注入 Adapter、输入对象图和磁盘内容都视为不可信，重建快照并重新计算身份、Checksum 与资源预算。不使用 Embedding 推测来改写 Chunk，也不把浮点向量本身当作来源事实。

## 2. 数据流与当前集成边界

```text
AttachmentScope + ownership link_id
  → DocumentProcessingService
      verified Load → Clean → Chunk → exact lineage validation
  → ChunkedDocument
  → DocumentEmbeddingService
      bounded document batches → strict loopback Ollama adapter
      → dimension / order / unit-vector / float32-le validation
  → EmbeddedDocument
  → SQLiteVectorStore
      exact Scope + link + derivation + embedding-space validation
      → atomic document generation replacement

  ✗ no Top-K or cosine retrieval
  ✗ no reranking or semantic deduplication
  ✗ no Citation, Prompt composition, or grounded answer generation
  ✗ no production composition-root, Desktop Protocol, or Renderer wiring
  ✗ no background lifecycle, retry, progress, cancellation, or cleanup jobs
```

`documents/indexing.py` 提供同步 Application Service 来组合已实现的 Processing、Embedding 和 Store 边界；它不会因为 Attachment 新增、更新或删除就自动运行。未来生产接线必须另行定义 Derived Relation、任务所有权、崩溃恢复、删除传播和用户可见状态，不能把当前同步 Library 冒充为完整生命周期。

## 3. 固定模型 Artifact 与运行时边界

| 字段 | 当前合同 |
| --- | --- |
| Provider | `ollama` |
| Adapter | `ollama-http` `1.0.0` |
| Model Tag | `qwen3-embedding:0.6b` |
| 完整 Manifest SHA-256 | `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d` |
| 模型 Layer SHA-256 | `06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439` |
| 模型 Layer 大小 | `639,150,592` bytes |
| Quantization | `Q8_0` |
| Vector Dimension | `1,024` |
| Normalization | `l2` |
| 上游能力说明 | 32K Context，100+ 自然语言与编程语言 |
| 上游模型许可说明 | Qwen Model Card 标注 Apache-2.0 |

上游的 32K Context 是模型能力说明，**不是** Elysia v1 的单项输入预算。当前 Service 只接受最多 2,000 Unicode Code Points 的单项输入，不会把 Chunk 暗中截断到模型 Context。运行时 Identity 使用完整 Manifest Digest；Model-layer Digest、Byte Size 与 Quantization 是对该固定 Manifest 的文档化技术 Provenance，不声称 Adapter 能够从当前 Ollama API 独立证明每一层供应链。模型许可与量化 Manifest 的来源边界详见 [MODEL_LICENSE.md](../MODEL_LICENSE.md)；Apache-2.0 说明指向上游 Qwen 模型，不应被扩大为对 Ollama Runtime、量化包每个组件或无关素材的概括结论。

仓库不捆绑、下载、镜像或提交该模型。需要独立运行 Embedding/索引测试时，由用户显式执行：

```bat
ollama pull qwen3-embedding:0.6b
```

基础文字 Chat 只使用其配置的 Chat Model，不需要这个 Embedding Model。单元测试也使用 Fake/Stub，不运行 `ollama pull`。

## 4. Embedding Space、Template 与资源 Policy

`EmbeddingModelIdentity` 通过 Canonical UTF-8 JSON 和 SHA-256 派生 `embedding_space_id`。Preimage 固定包含 Schema、Provider、Adapter ID/Version、Model Tag、完整 Manifest Digest、Dimension、Normalization、Document/Query Template Version，以及**实际生效的** Batch Policy、Document Input Mode 和精确 Query Prefix。因此即使维护者忘记更新人类可读的 Version，实际长度、Truncation 或 Prefix 变化也会得到新向量空间。任一 Preimage 字段变化都不得在同一 SQLite Store 中与旧向量混用。

| Policy | v1 固定值 |
| --- | ---: |
| 每 Batch 最多项数 | `16` |
| 单项最多输入 | `2,000` Unicode Code Points |
| 每 Batch 最多输入 | `32,000` Unicode Code Points |
| Truncation | `false` |
| Document Template | `raw-chunk-v1` |
| Document Input Mode | `exact-chunk-text-v1` |
| Query Template | `elysia-document-retrieval-v1` |

Document Embedding 把 Chunk Text 原样发送，不 Trim、Normalize 或加前缀。Query Embedding 则使用下列精确 Prefix（`\n` 表示一个 LF），且每次只允许一个 Query：

```text
Instruct: Given a user question, retrieve relevant passages from the user's local documents that answer it.\nQuery:
```

Batch Policy、`exact-chunk-text-v1` 与上述完整 Prefix 都直接进入 Space Fingerprint，也作为持久化合同保存。即使模型 Digest 不变，更改 Template、实际 Prefix、长度或 Truncation 也必须重建受影响的索引。

Policy 按 Python Unicode Code Points 计数，不把 UTF-8 Bytes、UTF-16 Code Units 或模型 Token 数混入 Chunk Lineage。它是应用层资源上限，不声称可以精确换算 Tokenizer Context。

## 5. Ollama Adapter 的信任边界

`documents/ollama_embedding.py` 只连接明确的 Loopback HTTP Endpoint，并把 Ollama 进程及其 JSON 响应视为不可信输入。边界包括：

- 拒绝远程 Host、用户信息、非预期 Scheme/Path/Query/Fragment；不使用环境代理，不跟随 Redirect，不隐式 Retry。
- 每个 Batch 调用前后都通过 `/api/tags` 核对固定 Model Tag 的完整 Manifest Digest；不接受只有同名 Tag 但内容已改变的本地模型。Model-layer Digest、Byte Size 与 `Q8_0` 是该固定 Manifest 的文档化 Provenance，当前 Adapter 不使用另一 API 对它们独立再证明。
- 只发送经验证的有界 Ordered Batch，显式要求不截断；响应数量、顺序、维度与 JSON Shape 必须符合合同。Response `Content-Type` 只允许无参数 `application/json` 或只带单一 `charset=utf-8` 参数的无歧义形式，并且不允许压缩。响应必须使用一个规范、与实际 Body 一致且有界的 ASCII 十进制 `Content-Length`，或只使用精确的 `Transfer-Encoding: chunked`；拒绝二者并存、编码链和其他 Transfer Coding。Chunk-size/Trailer Framing 同时限制为单行 `8 KiB`、累计 `64 KiB` 和最多 `1,024` 行。JSON 解码还拒绝重复 Member、NaN/Infinity 和无效 UTF-8。
- Header 和未解压 Body 都通过有界 Reader 读取；从 Connect、Request Send、Header 到 Body/Trailer 共用一个不可续期的 Monotonic Deadline，每次可能阻塞的 Socket 操作前只写入剩余时间。即使对端持续缓慢返回小 Header、Chunk 或 Trailer，完整调用也不能通过“每次仍有进展”无限续期。
- 网络、HTTP、JSON、Model Identity 和推理错误转换为稳定 `EmbeddingError`，不把 URL、Prompt、模型路径、Ollama 原始错误或响应体放入公开错误。

Adapter 只验证它本次从本地 Ollama API 观察到的声明身份和响应。这不是 Ollama 进程、其依赖或本机账户的完整供应链证明，也不把同一用户下的恶意本机进程排除在信任边界之外。

## 6. Vector 验证、Checksum 与精确 Lineage

`DocumentEmbeddingService` 把 Adapter 返回值作为不可信：

- Result 必须使用相同 Identity、Batch Policy、Purpose、Item Count 和顺序。
- 每个 Vector 必须恰好匹配 `EmbeddingModelIdentity.dimension`、只含有限 Float、非全零，且 L2 Norm 在 `1e-4` 绝对容差内为 `1.0`。内建固定 Ollama Identity 的 Dimension 是 1,024；通用 `DocumentEmbeddingService` 不硬编码这个值，因此测试 Adapter 可在自己的 Identity 下使用其他有界维度。Service 只验证模型声明的单位向量，不情况不明地重归一化一个错误响应。
- 验证后每个分量立即 Round-trip 到 Little-endian IEEE-754 Float32；`vector_checksum` 是这个 Canonical Byte String 的 SHA-256，不依赖 Python 原生 Float Object Layout。

`embedding_id` 格式为 `embedding_<64 lowercase hex>`，它的 Canonical Preimage 只绑定：

1. `chunk_id`；
2. `derivation_fingerprint`；
3. `embedding_space_id`。

Vector Bytes 不进入 Embedding ID，但受独立 Checksum 保护。这使“哪个派生 Chunk 在哪个语义空间中应有一条向量记录”保持确定，同时可以通过 Checksum 发现本地 Float Blob 损坏。`EmbeddedDocument` 还嵌入完整 `ChunkedDocument`，所以不会丢失 Scope/Link/File、Loader/Cleaner/Chunker Version、结构 Policy 或 Source Mapping。

## 7. SQLite Store 与 Scope 安全

`SQLiteVectorStore` 只使用 Python 标准库 `sqlite3`。每个 Database 在创建时永久绑定一个 `embedding_space_id`、完整 `EmbeddingModelIdentity`、Dimension 和 `float32-le-v1` Encoding；用另一模型、Manifest、Template 或维度打开会 Fail Closed，不会自动迁移或混用向量。

初次建库把全部 Allowlisted DDL、Model Identity Metadata、Application ID 和 User Version 放在同一 `BEGIN IMMEDIATE` 事务中；中途失败不会留下部分初始化 Schema。

| 操作 | 合同 |
| --- | --- |
| `replace_document` | 统一实现新增与更新；在单个 `BEGIN IMMEDIATE` 事务中替换精确 Scope + Link 的完整 Generation，失败保留旧 Generation |
| `list_document` | 按 Ordinal 有界分页；必须提供精确 Scope、Link、期望 Derivation 与 Model Identity |
| `get_record` | 在通过同样的 Generation/Space 复核后读取精确 Chunk ID |
| `delete_document` | 只删除精确 Scope + Link |
| `delete_scope` | 只删除精确 Chat 或 Project Scope 下的文档 |
| `rebuild` | 在单个事务中原子替换一个精确 Scope 的全部文档集合 |
| `model_identity` / `limits` | 以只读快照公布该 Database 绑定的完整模型空间和当前资源上限 |

Scope 过滤使用精确 `(scope_kind, scope_id)`，不执行 Prefix、Wildcard、跨 Project 共享或“当前 Chat 猜测”。Chat 与 Project 是两个不可互换的 Scope Kind；即使 ID Text 相同，也不是同一索引分区。当前 Store 不实现“Project Chat 可读 Project Sources”之类上层授权规则；未来 Retriever 必须由已验证的 Chat/Project Context 显式构造允许的精确 Scope 查询。

Store 保存两类完整性证据：向量以固定长度 Little-endian Float32 BLOB 及 SHA-256 存储；Lineage 和 Chunk Metadata 使用固定 Shape 的 Canonical JSON 及独立 SHA-256。Lineage Checksum 还覆盖总记录数、总 Chunk Code Points 和总 Mapping 数。读取时会在有界查询、页级累计预算和验证时限内重新解析 Domain 对象，并核对这些总量、Ordinal、ID、Checksum、Vector Length/Finite Values 与完整 Lineage，而不是直接把 SQLite Row 当作安全对象。

Database Path 必须是由可信 Composition Root 提供的绝对路径。Store 拒绝不安全的 Link/Reparse/Hard-link Target，以及未知 Table/Index/View/Trigger；只有真正空的 Catalog 才允许初始化。每次验证同时检查主 Database 与 Journal/WAL/SHM Sidecar 的物理总字节、SQLite 逻辑 Page Count/Size，并以 Store Timeout（默认 `5` 秒）限制完整性与 Schema 检查。公开的 Identity/Limits 都是隔离快照；路径、SQLite 原始错误和清理阶段错误不会越过稳定错误边界。

## 8. Stale、模型不匹配与损坏的 Fail-closed 语义

三类情况不会被当作“没有结果”静默跳过：

- `VectorStoreStaleError`：请求的 `derivation_fingerprint` 与持久化 Generation 不同，表示 Loader/Cleaner/Chunker/Policy/Source 中至少一项已变化。
- `VectorStoreModelMismatchError`：当前 Model/Embedding Space 与 Database 或 Document Generation 不同。
- `DocumentCorruptError` / `VectorStorePersistenceError`：SQLite Application ID/User Version、精确 Table DDL/Column/PK/UNIQUE/FK、不允许的 Trigger/View/显式 Index、`quick_check`、Canonical JSON、Checksum、BLOB 长度、Row Count 或 Domain Reconstruction 无法证明完整。

未知 Schema 不会自动降级读取，损坏记录不会部分返回，旧 Embedding 不会被错配到新 Chunk。正确恢复方式是从经验证的原始 Attachment 重新 Load/Clean/Chunk/Embed，再用 `replace_document` 或 `rebuild` 原子发布；不是修改 Digest 或手工复制旧 Row。

## 9. 持久化资源预算

`VectorStoreLimits` 默认值为：

| 资源 | 默认上限 |
| --- | ---: |
| 每文档 Vector Records | `10,000` |
| 单次 List Records | `1,000` |
| 单次 Scope Rebuild Documents | `1,000` |
| 单次 Scope Rebuild Records | `100,000` |
| Indexing Service 单次 Scope Rebuild Chunks | `100,000` |
| 每文档 Canonical Lineage JSON | `256 KiB` |
| 每个 Chunk Canonical JSON | `2 MiB` |
| 每文档全部 Metadata | `256 MiB` |
| 每文档全部 Vector Bytes | `64 MiB` |
| Database + Journal/WAL/SHM 物理总字节 | `2 GiB` |
| SQLite 逻辑 Page Count | `524,288` |
| Schema/Integrity Validation | Store Timeout，默认 `5` 秒 |

Embedding 的 16/2,000/32,000 预算在调用 Adapter 前检查；Indexing Service 在调用下一个模型 Batch 前执行跨文档 Chunk 总量检查；Store 预算在完整事务开始前增量预处理。因此超限文档不会先完成无界推理，或先删除旧 Generation 再发现无法写入。这些上限约束 Object Graph、Inference、Disk Write 与验证工作量；物理字节检查是单 Store 文件族上限，但仍不是整台机器的磁盘 Quota 或后台任务调度器。

## 10. 文件职责与测试

| 文件 | 职责 |
| --- | --- |
| `documents/embedding.py` | Model/Space Identity、Batch/Template Policy、Embedding Protocol、Document/Query Service、Embedded Lineage、Float32 Checksum |
| `documents/ollama_embedding.py` | 严格 Loopback Ollama HTTP Adapter、本地 Artifact 身份核对、有界 JSON 请求/响应与错误脱敏 |
| `documents/vector_store.py` | Scope-safe SQLite Schema、Canonical Lineage/Chunk Serialization、Float32 BLOB、原子替换/重建、稳定失效与损坏拒绝 |
| `documents/indexing.py` | 同步组合 Processing → Embedding → Store；不负责后台生命周期或桌面接线 |
| `tests/test_document_embedding.py` | Identity/Template/Batch、恶意 Adapter、Lineage、单位向量、Float32 Checksum 与预算 |
| `tests/test_ollama_embedding.py` | Loopback URL、Batch 前后 Full Manifest 身份、HTTP/JSON 边界、数量/维度和脱敏错误 |
| `tests/test_document_vector_store.py` | Add/Update/List/Filter/Delete/Rebuild、Scope 隔离、事务回滚、Stale/Space 拒绝、Schema/JSON/BLOB/Checksum 损坏 |
| `tests/test_document_indexing.py` | 组合顺序、精确 Scope/Link/Lineage 传递、替换/重建语义与稳定错误 |

单元测试不需要真实 Ollama 或模型：

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe -m pytest tests\test_document_embedding.py tests\test_ollama_embedding.py tests\test_document_vector_store.py tests\test_document_indexing.py -q
.venv\Scripts\python.exe scripts\check_python_documentation.py
cd desktop
npm run docs:check
```

## 11. 明确非目标与下一步

当前模块不提供：

- Top-K、Cosine/Dot-product/ANN Retrieval 或 Query-time Scope Union；
- Reranking、Semantic Deduplication、Diversity/Recency Scoring 或相似度阈值；
- Citation Selection、Prompt Composition、Prompt-injection Isolation 或 Grounded Answer Generation；
- `start.py` / `desktop_backend.py` Composition-root Wiring、Desktop Protocol、React 文档预览/问答 UI；
- Attachment Derived Relation 持久化、自动增量索引、删除传播、Job Queue、Progress、Cancel、Retry 或 Crash Recovery；
- 模型下载、Ollama 进程启动、自动选模型或 Remote Embedding Fallback。

下一模块是 Retrieval：在已验证的 Query Embedding 与精确 Chat/Project Scope 上实现有界 Top-K/余弦搜索，再对 Reranking、Deduplication 和 Citation Candidate 建立独立版本合同。在 Retrieval、Composition Root 和桌面协议完成前，本地 SQLite 里存在 Vector 仍不意味着 Project Sources 可以被 Chat 查询或引用。
