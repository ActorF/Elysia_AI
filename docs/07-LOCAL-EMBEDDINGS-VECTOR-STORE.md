# 本地 Embedding 与向量存储

`DocumentEmbeddingService` 将 [ChunkedDocument](./06-DOCUMENT-CLEANING-CHUNKING.md) 转为带准确 lineage 的向量；`SQLiteVectorStore` 按精确 Chat/Project Scope 保存完整 generation。SQLite 中存在向量不构成读取权限，授权由 [Project Sources](./10-PROJECT-SOURCES.md) 提供。

```text
ChunkedDocument → bounded Embedding batches → EmbeddedDocument
                                             → SQLite atomic replacement
                                             → authorized Retrieval
```

`documents/indexing.py` 同步组合 Processing、Embedding 和 Store；后台执行、恢复与删除传播由 [知识生命周期](./11-KNOWLEDGE-LIFECYCLE.md) 管理，不由 Attachment 变化自动触发。

## 固定模型空间

| 项目 | 当前合同 |
| --- | --- |
| Provider / Adapter | `ollama` / `ollama-http` `1.0.0` |
| Model tag | `qwen3-embedding:0.6b` |
| Manifest SHA-256 | `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d` |
| Model layer SHA-256 | `06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439` |
| Layer size / quantization | `639,150,592` bytes / `Q8_0` |
| Dimension / normalization | `1,024` / `l2` |

运行时核对完整 manifest digest；layer digest、大小和量化方式是该 artifact 的技术 provenance，不代表 Ollama API 能独立证明整个供应链。模型许可边界见 [MODEL_LICENSE.md](../MODEL_LICENSE.md)。

仓库不捆绑或自动下载模型。需要真实索引时显式安装：

```powershell
ollama pull qwen3-embedding:0.6b
```

普通文字 Chat 不要求这个 Embedding 模型；单元测试使用 Fake/Stub，无需下载。

`embedding_space_id` 是 canonical JSON 的 SHA-256，绑定 schema、provider、adapter/version、tag、完整 digest、dimension、normalization、document/query template versions，以及实际 batch policy、input mode 和 query prefix。任何一项变化都会产生新空间，即使人类可读 version 没变；新旧向量不能混入同一数据库。

## 输入合同

| Policy | 固定值 |
| --- | ---: |
| 每 Batch 项数 | 16 |
| 单项 / 全 Batch 文字 | 2,000 / 32,000 code points |
| Truncation | `false` |
| Document template / mode | `raw-chunk-v1` / `exact-chunk-text-v1` |
| Query template | `elysia-document-retrieval-v1` |

Document 发送原始 Chunk text，不 trim、normalize 或加前缀。Query 每次只有一项，使用以下准确 prefix，`\n` 表示一个 LF：

```text
Instruct: Given a user question, retrieve relevant passages from the user's local documents that answer it.\nQuery:
```

Prefix 与 Query 合计仍受 2,000 code points 限制，因此 Query 上限为 1,886。应用预算不等同于模型 tokenizer context；模板、prefix、长度或 truncation 变化必须重建索引。

## Ollama 边界

`documents/ollama_embedding.py` 只接受明确的 loopback HTTP endpoint，拒绝远程 host、userinfo 和非预期 scheme/path/query/fragment。不读取环境代理、不跟随 redirect、不隐式 retry，不启动 Ollama 或触发 model pull。

每个 batch 前后通过 `/api/tags` 核对同一 tag 的完整 manifest digest；同名但不同内容的模型会失败。请求与响应必须符合有界有序 batch、维度和 JSON shape。

Transport 同时限制 headers 和未解压 body：

- Content-Type 只允许 `application/json` 或单一 `charset=utf-8` 参数；拒绝压缩。
- 必须有规范且与实际 body 一致的 ASCII `Content-Length`，或精确 `Transfer-Encoding: chunked`；不允许二者并存或编码链。
- Chunk-size/trailer framing：单行 8 KiB、累计 64 KiB、最多 1,024 行。
- JSON 拒绝重复 members、NaN/Infinity 和无效 UTF-8。
- Connect、send、headers、body/trailers 共用不可续期的 monotonic deadline；每次阻塞前只使用剩余时间，慢速持续返回也不能无限续期。

公开 `EmbeddingError` 不含 URL、Prompt、模型路径、原始响应或 Ollama 诊断。这些验证只证明本次 API 声明与响应符合合同，不能证明本机进程未被攻陷。

## 向量与 lineage

Service 重建 Adapter 的输入/输出快照，复核 identity、policy、purpose、项数与顺序。每个向量必须：

1. 匹配 identity 声明的维度，所有分量有限且非全零。
2. L2 norm 在 `1e-4` 容差内为 `1.0`；错误响应不会被悄悄重新归一化。
3. Round-trip 为 little-endian IEEE-754 float32，再以准确 bytes 计算 `vector_checksum`。

通用 Service 不硬编码 1,024 维，测试 Adapter 可声明其他有界维度。

`embedding_<sha256>` 绑定 `chunk_id + derivation_fingerprint + embedding_space_id`；vector bytes 由独立 checksum 保护。这样身份描述“哪个 Chunk 属于哪个空间”，checksum 检测实际浮点数据损坏。`EmbeddedDocument` 保留完整 `ChunkedDocument`，包括 Scope/Link/File、producer versions、policies 与 source mappings。

## SQLite 合同

数据库只使用标准库 `sqlite3`，永久绑定一个完整 model identity、space ID、dimension 和 `float32-le-v1` encoding。数据库必须是 UTF-8；不同模型/模板/维度或现有 UTF-16 数据库均失败，不自动迁移或混用。

首次初始化在一个 `BEGIN IMMEDIATE` 事务内写入 allowlisted DDL、identity metadata、application ID 和 user version。只有真正空 catalog 才能初始化。

| 操作 | 原子性与选择键 |
| --- | --- |
| `replace_document` | 精确 Scope + Link 的完整 generation；失败保留旧值 |
| `list_document` / `get_record` | 要求 Scope、Link、期望 derivation 与 model identity；列表按 ordinal 有界分页 |
| `delete_document` / `delete_scope` | 仅删除指定 Link 或准确 Scope |
| `rebuild` | 一个事务替换一个 Scope 的完整文档集合 |

Scope 使用准确 `(scope_kind, scope_id)`，没有 prefix、wildcard 或跨 Project 合并；同名 Chat 与 Project 仍是不同分区。`model_identity` 和 `limits` 只发布隔离的只读快照。

向量 BLOB 与 canonical lineage/chunk JSON 分别有 checksum。Lineage checksum 还绑定记录数、文字总量和 mapping 总量。读取时有界重建领域对象并验证 ID、ordinal、vector、checksum、预算及完整 lineage，不能把 SQLite row 直接当作可信对象。

可信组合层提供绝对 DB 路径；Store 拒绝 link/reparse/hard-link target、未知 table/index/view/trigger，并验证准确 DDL、keys、schema versions 与完整性。主 DB 和 journal/WAL/SHM 同时受物理预算约束，另检查逻辑 pages。验证有独立 timeout，默认 5 秒。

## 失效与恢复

- `VectorStoreStaleError`：期望 derivation 与已存 generation 不同。
- `VectorStoreModelMismatchError`：model/space 不同。
- `DocumentCorruptError` / `VectorStorePersistenceError`：schema、JSON、checksum、BLOB、总量或领域重建不能证明完整。

这些不是“没有结果”；不会部分返回坏文档或让旧向量对应新 Chunk。恢复应从已验证 original 重新 Load/Clean/Chunk/Embed，并原子 replace/rebuild，不能手改 digest 或复制旧 rows。

## 存储预算

| 资源 | 默认上限 |
| --- | ---: |
| 每文档 records / 单次 list | 10,000 / 1,000 |
| Scope rebuild documents / records | 1,000 / 100,000 |
| Indexing rebuild chunks | 100,000 |
| Search documents / records / results | 128 / 10,000 / 64 |
| Search lineage/chunk/vector payload | 128 MiB |
| 每文档 lineage JSON / 每 Chunk JSON | 256 KiB / 2 MiB |
| 每文档 metadata / vector bytes | 256 MiB / 64 MiB |
| Canonical JSON nesting | 128 层 |
| DB + journal/WAL/SHM | 2 GiB |
| SQLite page count | 524,288 |

Embedding 在调用 Adapter 前限量；跨文档 Chunk 总量在下一模型 batch 前检查；Store 在事务前增量预处理。因此超限不会先做无界推理或先删旧 generation。这不是整机磁盘 quota 或全局任务调度器。

## 验证与后续边界

从仓库根目录运行，无需真实 Ollama：

```powershell
python -m pytest tests/test_document_embedding.py tests/test_ollama_embedding.py tests/test_document_vector_store.py tests/test_document_indexing.py -q
```

检索、去重与可选 reranker 见 [Retriever](./08-RETRIEVER-RERANKING.md)；桌面接线见 [Knowledge UI](./12-KNOWLEDGE-UI-TESTING.md)，文档检查见 [AGENTS.md](../AGENTS.md)。当前没有 ANN、Hybrid/BM25、自动增量索引、远程 Embedding fallback 或自动模型管理。
