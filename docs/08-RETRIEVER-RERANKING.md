# 检索与重排

`DocumentRetriever` 在一个精确 Scope 和显式授权的 generation 集合中执行有界余弦 Top-K，再做原文去重和可选重排。它只发布带来源证据的片段；[Grounded Answers](./09-GROUNDED-ANSWERS-CITATIONS.md) 负责生成回答。

```text
Scope + Query + generation allowlist + policy/filter/limits
  → identity-bearing query embedding
  → SQLite 单事务：验证全部授权 generation，扫描并评分
  → threshold → exact deduplication → optional reranker
  → deterministic Top-K + evidence
```

[桌面组合层](./12-KNOWLEDGE-UI-TESTING.md) 已接通此能力。授权来自当前 Chat→Project 关系和发布 catalog，Retriever 不会自行发现文件或扩大语料范围。

## Query 与授权集合

`EmbeddedQuery` 绑定 schema、完整 `EmbeddingModelIdentity`、space ID、实际 batch/template policy，以及 item ID 为 `query` 的 canonical float32 单位向量。它不保存 Query 原文；裸向量或同维但不同模型/模板/策略的向量都不能搜索。

[固定 prefix](./07-LOCAL-EMBEDDINGS-VECTOR-STORE.md) 与 Query 合计最多 2,000 code points，公开 Query 上限为 **1,886**。

每个 `ExpectedDocumentGeneration` 绑定完整 `DocumentSource` 与准确 `derivation_fingerprint`。Allowlist 必须是精确 tuple、link IDs 唯一且全部属于本次 Scope；没有通配符、prefix 或全库发现。

空 Allowlist，或 File/Media filter 排空的 Allowlist，会直接返回空结果，不调用 Embedder、Store 或 Reranker。非空时，Store 必须证明每一项存在、Source metadata 完全匹配、derivation 当前有效且 embedding space 相同。任一项缺失、过期、损坏或超限都使整次搜索失败，不能忽略坏文档后发布部分结果。

## 单事务余弦搜索

`SQLiteVectorStore.search_scope()` 在一个 `BEGIN` 读事务中：

1. 按准确 `(scope_kind, scope_id)` 和 allowlisted link 读取 generation headers。
2. 复核 Source、derivation、space、lineage JSON、checksum、记录总量与预算。
3. 按 link/ordinal 流式读取全部授权 records，验证 Chunk、mappings、IDs、vector BLOB 和 generation 总计。
4. 对符合 metadata filter 的已验证 records 评分；filter 不能绕过完整性检查。
5. 用 `math.fsum(document[i] * query[i])` 计算单位向量 dot product，按浮点容差约束并量化为八位小数。
6. 以公开量化分数执行 threshold 和 candidate retention，再用 Link、ordinal、Chunk ID、Embedding ID 稳定破同分。

低于半个 `10^-8` 的差异可量化为同分；阈值同样使用公开分数，避免平台浮点噪声影响语义。

选择暴力扫描是为了小型本地语料的可预测结果与完整性，不依赖 ANN 格式或第三方向量引擎。代价是每次必须扫描完整授权集合；超出预算返回错误，不静默少扫。

前置 Store 打开验证会检查整个私有数据库的 schema/catalog、`foreign_key_check`、`quick_check(1)` 与物理预算，可能读取 Allowlist 外的数据，但不将其作为候选。随后授权扫描另启 progress handler 和 monotonic deadline；默认两阶段各最多 5 秒，整个 API 并非总共 5 秒。

## 排名、筛选与去重

| Policy | 默认值 | 硬上限 |
| --- | ---: | ---: |
| `top_k` | 5 | 20 |
| `candidate_k` | 20 | 64 |
| `minimum_cosine_similarity` | 0.35 | 1.0 |

`candidate_k >= top_k`，限制去重前的原始候选池。重复片段可能占满池，去重后不保证填满 Top-K；池外重复来源也不补入 evidence。阈值只接受有限 `0.0–1.0`，没有合格片段就返回空，不选择“最不差”的文档作证据。

`RetrievalMetadataFilter` 只接受准确 `file_ids`、`media_types`、`chunk_kinds` 和一基 `page_numbers`。同字段 OR、跨字段 AND、空 tuple 表示无限制。值必须合法、唯一且受总量限制；不支持路径、任意 SQL、substring、regex 或自定义 predicate。File/Media 在调用前缩小 Allowlist；Store 和 Retriever 发布前再次复核，防止 Adapter 违约。

去重只按准确 `(kind, text)`。大小写、空白、换行、组合字符或代码缩进不同均不合并，避免改变原文语义。Hit 分数取组内最高 cosine，保留有界候选池内每个重复位置的 Source、generation、Chunk/Embedding ID、ordinal、page、mappings 与原分数。

## 可选 Reranker

仓库定义 `DocumentReranker` Protocol 和版本化领域值，**没有内建 Adapter、模型或权重**。

Adapter 必须公布 provider、adapter/version、model tag/digest、`relevance-0-to-1` score semantics 与 identity fingerprint。Policy 上限为 64 个候选、1,886 code-point Query、每片段 2,000、总输入 128,000，且 `truncate=false`。

Retriever 为每个去重片段生成 `candidate_<sha256>`；request fingerprint 绑定 Query、完整有序 ID/text 与 policy。返回必须：

- 保持 identity、policy 和 request fingerprint。
- 按请求顺序为每个候选恰好返回一次有限 `0.0–1.0` score。
- 不增加、删除或重复 ID，不改写文字、来源或 evidence。

调用前后复核 Adapter identity/policy 和 request mutation；最终按 reranker score、cosine、稳定身份排序。配置后的任何异常或坏结果均返回 `RerankerFailedError`，不会静默回退另一排名策略。低于 cosine 阈值的片段不会送入 Reranker，也不能被它恢复。

结构与 mutation 可验证，模型相关性判断本身仍由调用方选择的 Adapter 负责；Library 无法证明其语义评分正确。同样，Vector Store 是验证持久化、lineage 和 cosine 的可信实现，替代 Store 必须履行同一合同。

## 结果与空证据

`RetrievalResult` 包含 schema、Scope、policy/filter、实际 embedding identity、可选 reranker identity、去重后的 `candidate_count` 与最多 Top-K 个 hits。

Hit 只含 kind/text、最高 cosine、可选 rerank score 和 evidence。Evidence 不含向量、查询原文、本机路径或 SQLite 细节；它是后端内部来源合同，最终 Renderer 只接收更窄的 Citation DTO。

正常空结果有两类：

- Eligible corpus 在 Embedding 前已为空：identity 为 `None`，不调用外部依赖。
- Corpus 有效但无片段达到阈值：保留 embedding identity，`candidate_count=0`、`hits=()`。

缺失、过期、损坏、模型不匹配和超限都是 typed failure。Grounding 对真实空结果不调用 Generator；Retriever 自身不生成用户答复。

## 预算与错误

| Retrieval 资源 | 系统硬上限 |
| --- | ---: |
| Query code points | 1,886 |
| Expected generations / filter values | 128 / 256 |
| Scanned vector records | 10,000 |
| 扫描 lineage/chunk/vector payload | 64 MiB |
| Candidate text / source mappings | 128,000 code points / 100,000 |

除固定 Query 预算外，调用方可缩小限制。Store 另有 128 documents、10,000 records、64 results、128 MiB payload ceiling，实际取更小值。DB/sidecars 还受 2 GiB 与 524,288 pages 限制。

固定宽度 ID/checksum 不计入 payload，但在 SQL 投影内先验证类型和 UTF-8 长度；损坏的超长标识符不会进入 Python。预算约束授权扫描与候选内存，不替代前置全库验证、模型 context 或全局并发控制。

错误闭集为 `RetrievalValidationError`、`RetrievalLimitError`、`RetrievalUnavailableError`、`RetrievalFailedError`、`RerankerFailedError`。底层 Store 的 incomplete/stale/model mismatch/corruption 在跨边界时脱敏，不泄漏 Query、passage、URL、SQL 或原始诊断。

恢复应重新验证并索引 original，或修复明确配置的依赖；不能跳过坏文档或伪造 fingerprint。

## 验证

`documents/retrieval.py` 定义检索合同，`vector_store.py` 实现扫描。以下测试使用 Fake Embedder/Reranker 与临时 SQLite：

```powershell
python -m pytest tests/test_document_embedding.py tests/test_document_vector_store.py tests/test_document_retrieval.py -q
```

从仓库根目录运行；文档检查见 [AGENTS.md](../AGENTS.md)。当前没有 ANN、Hybrid/BM25、语义去重、recency/diversity boost、跨 Scope union、Agentic RAG 或远程重排 fallback。
