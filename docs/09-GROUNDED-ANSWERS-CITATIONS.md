# Grounded Answers and Citations：有界、结构化的文档回答

本文记录 Elysia AI 在 [Retriever and Reranking](./08-RETRIEVER-RERANKING.md) 之后的独立 Grounded Answer Library 边界。当前 `documents/grounding.py` 在一次同步调用中固定执行“准确 Scope 检索 → 完整片段选择 → 单次结构化生成 → 本地引用重建”，从而避免调用方把一个问题与另一个问题的检索结果错误配对。

该模块已经定义严格的 Domain、Prompt、Generator Protocol、Response Validation、Citation Location 和稳定错误；下游 [Project Sources](./10-PROJECT-SOURCES.md) 也已建立 Chat-derived Project 授权、共享语义和安全 Instructions。但仓库目前**没有真实 `GroundedAnswerGenerator` Adapter、没有捆绑回答模型，也没有接入 `Brain`、`start.py`、`desktop_backend.py`、Desktop Protocol 或 React**。存在这些独立 Library 不等于桌面 Chat 已经可以向附件或 Project Sources 提问。

## 1. 完成范围与核心原则

当前边界完成以下能力：

1. **固定 Two-Step RAG**：`GroundedAnswerService.answer()` 对一个 Query 精确调用 Retriever 一次，并最多调用 Generator 一次；不存在自主规划、工具循环、追加检索或隐式回退。
2. **显式授权不扩权**：调用方继续提供准确 `AttachmentScope` 与 `ExpectedDocumentGeneration` Tuple。Service 不发现文件、不合并 Scope，也不把 Chat Attachment 自动升级成 Project Source。
3. **完整片段上下文**：只从已经验证的 `RetrievalResult.hits` 按安全来源 preference + 原检索排名的稳定顺序选择前缀。片段不会为了塞进 Prompt 而截断；最高优先级片段都无法完整放入预算时会返回 Limit Failure，而不是伪装成“没有资料”。
4. **闭集 Citation**：Generator 只能引用 Prompt 中给出的 opaque Citation ID；文件名、页码和位置由 Service 从经过验证的 `RetrievalEvidence` 重建，不能由模型提供或修改。
5. **结构化回答**：回答只由带类型、带 Citation 的 `GroundedStatement` 组成，不存在一段无法与 Claims 对齐的额外自由文本答案。
6. **正常拒答与故障分离**：真正的空检索或模型基于已有片段作出的保守拒答使用 `insufficient_evidence`；过期索引、损坏数据、模型不匹配、资源超限或坏 Generator 输出仍然是 Typed Failure。
7. **结构安全而非语义证明**：Library 能证明每个发布的 Citation 来自本次已授权、已选择的闭集证据，也能机械验证 `source_fact` 是连续原文；它不能证明 `model_summary` 或 `inference` 在语义上必然成立。

## 2. 数据流与当前集成边界

```text
exact AttachmentScope
  + exact query
  + ExpectedDocumentGeneration allowlist
  + bounded non-authorizing GroundedAnswerPreferences
  + retrieval filter / policy / limits
  + grounded-answer limits
  → GroundedAnswerService.answer()
      → GroundedPassageRetriever.retrieve() exactly once
          → validated RetrievalResult
      → empty hits?
          yes → deterministic insufficient_evidence
                 no generator identity lookup or generation
          no  → rebuild scope/source/hit/evidence/mapping snapshots
              → apply authorized source preference to validated hits
              → select stable prefix of whole prioritized hits
              → derive passage IDs and per-occurrence citation IDs
              → fixed trusted system message
                + canonical untrusted JSON data message
              → GroundedAnswerGenerator.generate() at most once
              → verify request and generator identity did not change
              → strict JSON / fingerprint / statement / citation validation
              → rebuild only referenced public citations locally
              → GroundedAnswerResult

  ✗ no Project Source discovery or cross-scope union
  ✗ no real generator adapter or bundled answer model
  ✗ no Brain, production composition-root, Desktop Protocol, or React wiring
  ✗ no persistence, streaming, tools, Agentic RAG, or semantic verifier
```

Service 自己拥有检索到生成的完整调用，原因是 `RetrievalResult` 有意不保存 Query 原文。若公开接受任意 `(query, retrieval_result)`，Library 无法证明该结果确实由同一个 Query 检索得到。

## 3. 公共 API

### 3.1 `GroundedAnswerService`

```python
GroundedAnswerService(
    retriever: GroundedPassageRetriever,
    generator: GroundedAnswerGenerator,
)

answer(
    scope: AttachmentScope,
    query: str,
    expected_documents: tuple[ExpectedDocumentGeneration, ...],
    *,
    preferences: GroundedAnswerPreferences | None = None,
    metadata_filter: RetrievalMetadataFilter = RetrievalMetadataFilter(),
    retrieval_policy: RetrievalPolicy = RetrievalPolicy(),
    retrieval_limits: RetrievalLimits = RetrievalLimits(),
    answer_limits: GroundedAnswerLimits = GroundedAnswerLimits(),
) -> GroundedAnswerResult
```

`GroundedPassageRetriever` 与 Module 5 Retriever 保持同一调用合同。Service 会先重建 Scope、Allowlist、Preferences、Filter、Policy 与 Limits 的独立快照，并先证明每个 preferred link ID 都属于准确 Allowlist，再把另一组脱离主请求对象的快照交给注入的 Retriever。返回结果还会重新绑定到原始 Scope、Policy、Filter 和授权 Generation；越过授权边界的 Source、Derivation、Mapping 或 Hit 不会进入 Prompt。

### 3.2 `GroundedAnswerGenerator`

Generator 是一个 Protocol，不是当前仓库中的真实 Adapter：

```python
class GroundedAnswerGenerator(Protocol):
    @property
    def identity(self) -> GroundedAnswerGeneratorIdentity: ...

    def generate(self, request: GroundedAnswerRequest) -> str: ...
```

Identity 固定 `provider`、`adapter_id`、`adapter_version`、`model_tag`、`model_digest` 与派生的 `identity_fingerprint`。Generator Policy 固定：

- `stream = False`；
- `tools_enabled = False`；
- `reasoning_enabled = False`，不能把隐藏思考文字混入严格 JSON；
- `truncate = False`，不能把截断响应当成完整结构；
- `temperature = 0.0`；
- 显式 `max_response_utf8_bytes`；
- 显式 `max_statements`、单条/累计 Statement Code Points 与单条 Citation 数量上限。

这些设置与完整 Prompt、Allowlist、Preferences Fingerprint 和 Identity 一起进入 v2 Request Fingerprint；同一 Prompt 只要任一输出上限或 Preference 改变，就不再是同一个生成合同。它们使执行合同可预测，但不能把概率模型的语义输出提升为逐字节确定性保证。

`GroundedAnswerGenerator` 只是依赖注入 Protocol。`provider`、Policy Flag、Fingerprint 或 `model_digest` **都不能证明 Transport 位于本机，也不能阻止一个恶意实现把 Query/Passage 外发**。当前仓库没有生产 Adapter，因此当前 Library 不执行网络请求；后续 Adapter 必须单独强制可信本地 Transport/Host、禁用 Proxy/Redirect/Retry、设置完整 Body 前的 Byte Cap 与 Deadline，把上述 Policy 落实为真实动态 Structured-output Schema，并验证实际模型 Digest。`model_tag` 只允许不含路径分隔符的公开显示标签，原生模型路径不得进入 Identity 或 Result。

### 3.3 `GroundedAnswerRequest`

Request 是 `frozen=True, slots=True, repr=False` 的值对象，包含：

- Grounded Answer Schema Version；
- Prompt Template Version；
- Generator Identity；
- 恰好两条 Prompt Message：一条 `system`、一条 `user`；
- 非空、无重复的 Citation ID Allowlist；
- 固定 Generator Policy；
- `preferences_fingerprint`；
- `request_fingerprint`。

v2 Fingerprint Domain 绑定 Generator Identity、Prompt Template、完整有序 Prompt、Citation Allowlist、Preferences Fingerprint 与 Policy。为解决 Digest 自身出现在 JSON Envelope 中的循环依赖，计算时只把 Envelope 的 `request_fingerprint` 字段替换为固定 64 个零；Question、Preference、Passage、Citation ID 和其他所有字节仍被绑定。Request 构造器随后要求 Envelope 内字段与派生出的最终 Fingerprint 完全相等，Generator Response 也必须原样回显它。

### 3.4 `GroundedAnswerPreferences`

Preferences 绑定准确 Scope、有序且唯一的 preferred source link IDs、closed `default|concise|balanced|detailed` answer style、可空 style guidance 与 canonical fingerprint。Preferred IDs 数量不能超过 Retrieval document 上限，并必须在任何检索调用前被证明属于 `expected_documents`；style guidance 最多 8,000 code points 与 32,000 UTF-8 bytes。超限、Scope 错配、未知 link 或 fingerprint 错配都是 Typed Failure，不会截断、降级或静默忽略。

## 4. 有界上下文选择

`RetrievalResult.hits` 已由 Retriever 排序、阈值过滤并按准确 `(kind, text)` 去重。Grounding 层不重新搜索，也不从隐藏的 `candidate_count` 补取片段。只有在完整 Retrieval Result 通过 Scope、Generation、Filter、阈值与 Reranker 合同复核后，结构化 `preferred_source_link_ids` 才按每个 Hit 最优授权 Evidence 的 preference tier 做稳定重排；同 tier 保留原 Retrieval rank，未返回或被阈值拒绝的证据永远不会复活。去重 Hit 内的 Evidence/Citation 也按同一授权 preference 稳定排序。

Service 按上述已验证顺序构造一个**完整片段的稳定前缀**，同时检查：

- Passage 数；
- Passage Text Code Points 总数；
- Citation Occurrence 数；
- Citation Location 数；
- 固定 System Prompt 与 Canonical JSON Envelope 的 UTF-8 Bytes。

一旦下一个完整 Hit 不能同时满足所有预算，选择立即结束，不跳过它去挑选更低排名的小片段。若第一个 Hit 就无法容纳，则抛出 `GroundedAnswerLimitError`。这种规则保持排名语义和来源映射完整，避免截断文字改变含义或让 Citation Offset 失真。

Text、Evidence 和 Source-mapping Tuple 数量先用长度预算预检；只有确认当前完整 Hit 能落入 Answer Budget 后，才计算绑定完整 Lineage 的 Citation Digest 并重建所有公开 Location。这样 Retriever 的较大合法预算不会在更小的 Answer Budget 判定失败前造成无意义的对象放大。

每个去重 Hit 生成一个由准确 `kind + text` 派生的 `passage_<sha256>`。同一 Hit 可能来自多个文件、页或位置；其中每个 `RetrievalEvidence` Occurrence 分别生成一个 Citation，而 Passage Text 在 Prompt 中只出现一次。

## 5. Citation 与 Location

### 5.1 Citation 身份

每个 `citation_<sha256>` 在内部绑定：

- Passage Kind 与完整 Text；
- Scope Kind / ID；
- Ownership Link ID 与 File ID；
- Derivation Fingerprint；
- Embedding ID 与 Chunk ID；
- Chunk Ordinal 与 Page；
- 完整 Source Mapping。

这些私有 Lineage 字段只参与 opaque ID 派生，不作为公开 Citation Metadata 发布。Generator 也不能提交 Citation Metadata；它只返回 Allowlist 中的 ID。

### 5.2 公开 `GroundedCitation`

最终结果中的 Citation 只发布：

- `citation_id` 与 `passage_id`；
- `kind` 与完整 `excerpt`；
- 安全显示用 `file_name`、`media_type`；
- 可选的一基 `page_number`；
- 一个或多个结构化 `locations`。

它不发布本机路径、File ID、Link ID、Hash、Derivation Fingerprint、Embedding ID、Vector、Cosine Score 或 Reranker Score。

### 5.3 Location 坐标

`GroundedTextLocation` 描述非表格来源：

- Chunk 内零基半开区间 `[chunk_start_code_point, chunk_end_code_point)`；
- Loaded Source 的 `block_ordinal`；
- Source Block 内零基半开区间 `[source_start_code_point, source_end_code_point)`。

Text Mapping 必须保持两边长度完全相等。

`GroundedTableCellLocation` 额外发布零基 `row_index` 与 `column_index`。Table Projection 包含 JSON 标点和 Escape，因此 Chunk 区间不要求与 Source Cell 区间等长，Source Cell 也允许空区间。Page 统一放在 Citation 顶层；同一 Evidence 的 Mapping 已由既有 `DocumentChunk` Contract 复核 Page、Kind、顺序、覆盖与 Prose Gap 规则。

这些坐标支持文件名、PDF 页码、文本 Block/Code-point 或表格 Cell 的准确说明，但不承诺 PDF 视觉 Bounding Box、像素坐标或原生 Office Selection Range。

## 6. 三类 Statement

`GroundedAnswerResult` 不包含独立的自由文本 Answer。唯一可发布的回答内容是按模型顺序排列的 `GroundedStatement`：

| Kind | 合同 | 可机械验证的部分 |
| --- | --- | --- |
| `source_fact` | 来源中的直接文字事实 | Statement Text 必须是每一个所引 Citation 对应 Passage 的准确连续子串，不能混入不支持该引文的来源 |
| `model_summary` | 对一个或多个 Passage 的忠实改写、压缩或综合，不应增加新事实 | 类型、Text Budget 与 Citation 闭集可验证；语义忠实度由所选 Generator 负责 |
| `inference` | 从所引 Passage 前提得出的推断，而不是来源原句 | 类型和 Citation 闭集可验证；推断是否成立由所选 Generator 负责 |

每条 Statement 必须：

- 使用闭集 Kind；
- 包含有意义且安全的 Unicode Text；
- 至少引用一个本次 Prompt Allowlist 中的 Citation ID；
- 不包含重复 Citation ID；
- 服从单条与累计文字预算；
- 获得按响应顺序派生的 `statement_001`、`statement_002` 等连续 ID。

最终 `citations` Tuple 恰好等于所有 Statement 实际引用 ID 的 Canonical Union，并保持所选 Passage / Evidence 的顺序；未引用的 Prompt Citation 不会发布。

## 7. Prompt Injection 与严格响应合同

Prompt 永远只有两条消息：

1. 固定的 Trusted System Policy；
2. Canonical JSON User Message。

System Policy 要求回答 JSON 的 Question Field，但 Question 只能选择所需内容，不能覆盖 Grounding Policy；Passage 中的 Markup、Code、URL 和看似命令的文字都只是数据，不能执行或改变策略。`answer_preferences` 同样是不可信数据，只能影响语言、长度、组织、语气，以及已经授权且相关的 Passage 顺序。System Policy 明确说明 Passage 已按 validated preference + retrieval rank 排序：证据同等支持时优先较早 Passage，但不得隐藏或矛盾后续相关证据。Generator 还被禁止使用外部知识、工具、文件、命令或网络。任何不可信值都不会插值进 System Message。

User JSON 只包含：

- Schema Version 与 Request Fingerprint；
- Question；
- `answer_preferences`：closed `answer_style`、可空且有界的 `style_guidance`，以及精确 `preferences_fingerprint`；
- 有序 Passage ID、Kind 和完整 Text；
- 每个 Passage 对应的一个或多个 opaque Citation ID。

File Name、Media Type、Page 和完整 Source Mapping 都不进入 Prompt；Generator 只需选择 opaque ID，这些展示字段留在可信本地状态中并从 Evidence 重建。这样既缩小私人元数据披露面，也避免恶意文件名扩大 Prompt Injection 面。

Generator 必须返回且只返回一个 JSON Object：

```json
{
  "schema_version": 1,
  "request_fingerprint": "<exact request fingerprint>",
  "status": "answered",
  "statements": [
    {
      "kind": "model_summary",
      "text": "A supported statement.",
      "citation_ids": ["citation_<sha256>"]
    }
  ]
}
```

Parser 拒绝：

- Markdown Fence、前后解释文字或非 JSON Response；
- 重复 JSON Key、未知字段、缺失字段；
- `NaN`、Infinity、非准确类型或非安全 Unicode；
- Schema / Fingerprint 不匹配；
- 未知、重复、空或超量 Citation ID；
- `answered` 但没有 Statement；
- `insufficient_evidence` 却携带 Statement；
- 不是准确连续引文的 `source_fact`；
- 超过 Response、Statement 或累计文字预算的输出。

Prompt Injection 防护降低文档文字改变模型行为的风险，但不能把语言模型变成形式证明器。特别是 `model_summary` 与 `inference` 的语义质量仍由调用方选择并信任的 Generator 决定。

## 8. 防变异与不可信依赖

所有公开 Domain 值使用 Frozen + Slots Dataclass。Service 仍把注入的 Retriever 与 Generator 当作不可信边界：

- 传给 Retriever 的 Scope、Generation、Filter、Policy 和 Limits 与主请求对象分离；
- 返回的 Result、Hit、Evidence、Source、Text/Table Span 与 Mapping 通过准确类型和公开构造器重建；
- Evidence 必须重新匹配本次授权的 Source 与 Derivation；
- Request 在调用 Generator 前完整 Snapshot；调用后再次重建并与原 Request 比较；
- Generator Identity 在调用前后都重建并比较；
- Fingerprint 把 Prompt、Allowlist、Identity 与 Policy 绑定到同一 Response；
- Adapter 返回的 File Name、Page、Location 或任意 Citation Metadata 根本不在 Response Schema 中，因此无法覆盖可信 Provenance。

`GroundedAnswerRequest` 禁止默认 `repr`，避免 Query 与 Passage 因普通对象日志意外出现。公开错误也不会带出 Adapter 原始 Message、Raw Response、Query、Passage、文件路径或底层 Traceback Cause。

## 9. 空证据、模型拒答与错误

### 9.1 两种正常 `insufficient_evidence`

1. **Retriever 成功但 `hits=()`**：Service 在读取 Generator Identity 之前直接返回；`context_passage_count=0`、`generator_identity=None`、`statements=()`、`citations=()`，Generator 不会被调用。
2. **已有 Context，但 Generator 保守拒答**：严格响应为 `status=insufficient_evidence` 且 `statements=[]`；结果保留实际 `context_passage_count>0` 与 Generator Identity，但仍不发布 Statement 或 Citation。

这两种状态都不会生成无 Citation 的普通回答。它们在 Backend Domain 中可由 Context Count 与 Generator Identity 区分；未来 UI 如何措辞属于 Module 9。

### 9.2 Stable Error Set

- `GroundedAnswerValidationError`：调用参数、公开 Domain、Scope/Allowlist 关系或 Shape 非法；
- `GroundedAnswerLimitError`：Context、Citation、Location、Prompt、Response、Statement 或解析资源超限；
- `GroundedAnswerUnavailableError`：配置的 Generator 明确不可用；
- `GroundedAnswerFailedError`：非 Typed Retriever 失败、不安全的 Retrieval Result、Generator Identity/Request 变化、坏 JSON 或坏响应合同；
- 既有 `RetrievalError` 分类：Validation、Limit、Unavailable、Store/Embedding/Reranker Failure 会保留对应类型，但 Service 会用固定无内容文字重建异常并清除原始 Cause，避免注入 Retriever 的 Message 泄漏 Query、路径或内部诊断。

缺失 Generation、过期索引、损坏 Store、Embedding Space 不匹配或部分搜索失败不是“资料不足”。这些问题必须修复或重新索引，不能通过空回答、忽略坏文档或无引用模型回答来掩盖。

Generator 抛出的 `GroundedAnswerUnavailableError` 与 `GroundedAnswerLimitError` 会被重新映射成无内容的稳定消息；其他异常统一成为 `GroundedAnswerFailedError`。Dependency 原始诊断不会跨越边界。

## 10. 资源预算

`GroundedAnswerLimits` 每个字段都可以由调用方缩小，但不能超过系统硬上限：

| 资源 | 默认值 | 硬上限 |
| --- | ---: | ---: |
| Context Passages | `8` | `20` |
| Context Text | `16,000` Code Points | `40,000` Code Points |
| Citation Occurrences | `64` | `64` |
| Citation Locations | `4,096` | `100,000` |
| Prompt | `256 KiB` UTF-8 | `256 KiB` UTF-8 |
| Generator Response | `128 KiB` UTF-8 | `128 KiB` UTF-8 |
| Statements | `32` | `32` |
| 单条 Statement | `4,000` Code Points | `4,000` Code Points |
| 全部 Statement Text | `16,000` Code Points | `16,000` Code Points |
| 单条 Statement Citations | `64` | `64` |

此外：

- Query 继续服从 Retriever 的固定 `1,886` Code-point 上限；
- Expected Documents、Filter、Vector Scan、Payload 和 Source Mapping 仍服从 `RetrievalLimits`；
- `max_statement_code_points` 不得大于累计 Statement 预算；
- 单条 Statement Citation 上限不得大于整个 Context Citation 上限；
- Grounding 的 `64` 个 Citation 上限与 Retriever `candidate_k` 的硬上限一致；
- Prompt Budget 同时在 Context 选择、Request Domain 与 Generator 调用前检查；
- Statement 数量、单条/累计文字和单条 Citation 上限被复制进 Generator Policy 并绑定到 Fingerprint，Response Parser 直接按该 Policy 验证；
- Response 先以 Code-point 数作零分配超限预检，再安全编码并按准确 UTF-8 Bytes 限制，之后才执行 JSON 与 Statement 验证。

这些预算约束单次 Library 调用的内存对象与注入模型输入输出，不是全局并发限制、模型 Token Window、磁盘 Quota 或后台任务调度器。

## 11. 结果隐私边界

`GroundedAnswerResult` 发布 Schema、Scope、Status、可选 Generator Identity、实际 Context Passage Count、Statements 与被引用 Citations。它有意不保留：

- Query；
- Prompt Message 或 Request Fingerprint；
- Raw Generator Response；
- Local Path、File ID、Link ID 或 Content Hash；Generator Identity 中只保留经过格式限制的公开 Model Tag 与 Digest，不能放入原生模型路径；
- Derivation / Embedding / Chunk 私有身份；
- Vector、Cosine 或 Reranker Score。

Citation Excerpt 与安全文件名本身仍是用户文档数据，只应在当前授权 Scope 内使用。当前结果是 Python Backend Domain，并非 Desktop Wire DTO；Renderer 的最终字段 Allowlist、HTML/Markdown Escaping 与引用跳转仍属于 Module 9。

## 12. 明确非目标与 Module 7–9

### Module 7：Project Sources

Grounding 层本身仍不发现 Project Sources；调用它的底层 API 继续要求准确 Scope 与 Generation Allowlist。下游 [Project Sources](./10-PROJECT-SOURCES.md) 已新增独立授权组合层：

- 只从 canonical Chat→Project 关系派生 Scope；
- 让同一 Project 的多个 Chat 共享一个完整 Generation catalog；
- 保持 Chat Attachment 默认隔离，并只允许显式 committed-file promotion；
- 把 catalog 中持久化的 structured preferred sources 与 closed answer style 绑定到 snapshot fingerprint，并把 Project 自由文本 Instructions 作为 fingerprint-bound untrusted style guidance；
- 让结构化 preferred source 只重排已经通过 Retrieval 的授权 Hit；
- 在持有操作 lease 的前提下，于生成后复核 authority、ownership、catalog 与 preferences。

它不修改 Grounding 的单 Scope 原则，也不执行 Cross-scope Union。

### Module 8：Knowledge Lifecycle

当前模块不负责：

- 查看、替换、重新索引、导出或删除文档；
- 删除时传播清理 Chunk、Vector、Metadata、Preview 或 Cache；
- Index Job 的进度、取消、重试与 Crash Recovery；
- Grounded Answer 或 Citation 的持久化生命周期。

Stale 或 Missing Generation 继续由 Retriever Fail Closed。

### Module 9：Knowledge UI and Testing

当前模块不负责：

- `Brain`、`start.py` 或 Desktop Backend Composition Root；
- Electron IPC / Desktop Protocol / React 数据合同；
- 用户可见的 Statement 类型标签、Citation 格式化或本地化；
- Citation 点击、文件 Preview、Page / Block / Cell 跳转；
- Chat Message 持久化、流式显示、停止生成与切换 Chat 的事件隔离；
- 真实 PDF/DOCX 的端到端桌面验收。

此外，本模块不提供 Agentic RAG、Tool Call、Hybrid/BM25/ANN Search、第二模型语义 Verifier、远程 Generator Fallback、模型下载、真实 Generator Adapter 或新的模型素材许可。

下一步由 Module 8 完成知识生命周期，再由 Module 9 建立生产接线、严格 Desktop DTO、可访问 UI 与真实文档回归。在这些边界完成前，不能声称桌面 Chat 已能安全地回答或引用用户文件。
