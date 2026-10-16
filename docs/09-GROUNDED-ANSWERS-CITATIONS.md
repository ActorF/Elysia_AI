# 文档回答与引用

`GroundedAnswerService.answer()` 固定执行一次检索、完整片段选择、最多一次结构化生成和本地引用重建。它自行拥有整条调用链，避免把一个问题与另一个问题的检索结果配对；`RetrievalResult` 本身不保存 Query 原文。

生产 `OllamaGroundedAnswerAdapter` 与 [桌面组合层](./12-KNOWLEDGE-UI-TESTING.md) 已接通 Project 文件问答。只有用户在准确 Project Chat 中启用 **Use Project Sources** 才进入这条路径；底层 Service 不发现来源或合并 Scope。

```text
Scope + Query + exact generations + bounded preferences
  → retrieve exactly once
  → empty hits：insufficient_evidence，跳过 Generator
  → 重建并验证所有来源
  → 选择完整片段的稳定前缀
  → 固定 system policy + untrusted JSON data
  → generate at most once
  → 严格验证 response，按可信 evidence 重建 Citation
```

## 调用合同

`GroundedAnswerService(retriever, generator)` 接受 `GroundedPassageRetriever` 和 `GroundedAnswerGenerator` Protocol。`answer()` 的必需参数为准确 `AttachmentScope`、Query 和 `ExpectedDocumentGeneration` tuple；可选参数为 preferences、metadata filter、retrieval policy/limits 和 answer limits。

所有输入先递归重建快照；交给 Retriever 的对象与主请求隔离。结果重新绑定到原始 Scope、policy/filter 和授权 generation，越界 Source、derivation、mapping 或 Hit 不得进入 Prompt。

`GroundedAnswerPreferences` 绑定 Scope、有序唯一 preferred link IDs、`default | concise | balanced | detailed` style、可空 style guidance 和 fingerprint。Preferred IDs 必须在检索前证明属于 Allowlist；数量受 Retrieval documents 上限约束。Guidance 最多 8,000 code points / 32,000 UTF-8 bytes，超限直接失败，不截断或忽略。

## 完整片段选择

只有整个 Retrieval Result 通过授权、generation、filter、threshold 和 reranker 验证后，preferred links 才按每个 Hit 的最优授权 evidence 稳定重排。同 tier 保持原检索排名，Hit 内 evidence 使用相同偏好顺序；未返回或低于阈值的证据不会复活。

Service 同时检查 passage 数、文字总量、citation occurrences、locations 和完整 Prompt UTF-8 bytes。遇到第一个放不下的完整 Hit 就停止，不跳过去找更小的低排名片段；第一个 Hit 都放不下时返回 `GroundedAnswerLimitError`。

不截断片段是为了保持原文含义与来源坐标准确。长度先预检，确认完整 Hit 可放入后才计算 citation digest 和构造位置，避免合法的大 Retrieval Result 在较小 Answer Budget 下先发生对象放大。

每个去重 Hit 生成 `passage_<sha256>`；每个 evidence occurrence 分别生成 Citation，正文在 Prompt 中只出现一次。

## Generator 与请求身份

`GroundedAnswerGenerator` 公布 identity，并实现 `generate(request) -> str`。Identity 包含 provider、adapter/version、model tag/digest 和 fingerprint；tag 必须是安全公开标签，不能是原生路径。

固定 policy 为：

- `stream=false`、`tools_enabled=false`、`reasoning_enabled=false`。
- `truncate=false`、`temperature=0.0`。
- 显式 response bytes、statement 数量、单条/累计文字与 citation 数上限。

这些约束让合同可预测，不保证概率模型逐字节确定。Protocol、policy flag 或 digest 本身也不能证明 transport 在本机：生产 Adapter 额外强制 loopback IP origin，禁用环境 proxy、redirect/retry，执行 byte/deadline 限制、闭合 structured-output schema，并在生成前后验证同一 tag 的完整 manifest digest。它不下载或启动模型。

`GroundedAnswerRequest` 是 frozen/slots 且 `repr=False` 的值，含 schema、prompt template、identity、两条 message、非空唯一 citation allowlist、policy、preferences fingerprint 和 request fingerprint。

v2 fingerprint 绑定完整有序 Prompt、Allowlist、identity、preferences 与 policy。计算时仅将 envelope 内的 `request_fingerprint` 替换为固定 64 个零，消除自引用；构造器要求 envelope 最终值和派生 digest 一致，response 必须准确回显它。所有输出预算也进入该合同。

## Citation 与位置

内部 `citation_<sha256>` 绑定 passage kind/text、Scope、Link/File、derivation、Embedding/Chunk ID、ordinal/page 和完整 mappings。Generator 只能返回已给出的 ID，不能填写或改写引用元数据。

公开 `GroundedCitation` 仅发布 citation/passage ID、kind、完整 excerpt、安全文件名/MIME、可选一基页码和 locations。它不发布路径、File/Link ID、hash、derivation、vector 或检索分数。

位置合同：

| 类型 | 坐标 |
| --- | --- |
| `GroundedTextLocation` | Chunk 区间、Loaded Block ordinal、Source Block 区间；两边 code-point 长度必须相等 |
| `GroundedTableCellLocation` | 另含零基 row/column；投影标点/escape 可使两边区间不等长，Source Cell 可为空区间 |

区间均零基半开；Page 放在 Citation 顶层。这些位置描述 Loader 结构，不是 PDF bounding box、像素或原生 Office selection。原文 Preview 与 page/block/cell 跳转仍未实现。

## 三类陈述

回答只由有序 `GroundedStatement` 组成，不另附一段无法对齐引用的自由文本答案。

| Kind | 合同 |
| --- | --- |
| `source_fact` | Text 必须是**每个**所引 passage 的准确连续子串 |
| `model_summary` | 对来源的改写、压缩或综合；结构可验证，语义忠实度由 Generator 负责 |
| `inference` | 基于所引前提的推断；类型/引用可验证，推断正确性由 Generator 负责 |

每条必须含有效 Unicode text、至少一个 Allowlist Citation、无重复引用，并满足预算。Statement IDs 按响应顺序生成 `statement_001` 等连续值。

最终 citations 恰好是实际引用 ID 的 canonical union，保持所选 passage/evidence 顺序；未引用证据不发布。系统能证明引用来自本次授权闭集，不能机械证明 summary 或 inference 成立。

## Prompt 与严格响应

Prompt 始终只有固定 trusted system message 和 canonical JSON user message。不可信值不插入 system policy。

User JSON 只含 schema/fingerprint、Question、bounded answer preferences，以及有序 passage ID/kind/text 和 opaque citation IDs。文件名、MIME、页码与 mapping 留在可信本地状态，降低元数据披露和恶意文件名注入面。

System policy 要求只依据文档回答。Question 只能选择内容，preferences 只能影响语言、长度、组织、语气和合法来源顺序；文档中的 markup、code、URL、命令样文字都是数据。工具、外部知识、文件、命令和网络均不属于生成合同。偏好不能隐藏相矛盾的相关证据。

响应只能是一个准确 JSON object：

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

Parser 拒绝 Markdown fences、额外文字、重复/未知/缺失字段、NaN/Infinity、错误类型或 Unicode、schema/fingerprint 不匹配、空/未知/重复/超量引用，以及不满足原文子串的 source_fact。`answered` 必须有 statements；`insufficient_evidence` 必须为空。Response 在 JSON 解析前先做 code-point 预检，再以准确 UTF-8 bytes 限量。

调用 Generator 前后重新比较 request 和 identity；Retriever 返回的 nested values 也通过准确类型和构造器重建。默认 repr 与公开错误不带 Query、passage、raw response、路径或底层诊断。Prompt 防护降低风险，但不是语义证明器。

## 空证据与失败

两个正常 `insufficient_evidence` 状态：

- 检索成功且 `hits=()`：读取 Generator identity 之前返回，context count 为 0，identity 为 `None`，不调用模型。
- 有 context 但 Generator 保守拒答：保留实际 context count 和 identity，statements/citations 为空。

两者都不会退回无引用普通回答。Desktop DTO 保留区分所需字段。

公开错误为 `GroundedAnswerValidationError`、`GroundedAnswerLimitError`、`GroundedAnswerUnavailableError`、`GroundedAnswerFailedError`。既有 RetrievalError 分类被保留但用固定消息重建、清除原始 cause；Generator limit/unavailable 同样脱敏，其他异常统一 failed。过期、损坏、缺失 generation 或部分搜索失败不能被包装成“资料不足”。

## 预算

| 资源 | 默认值 | 硬上限 |
| --- | ---: | ---: |
| Context passages | 8 | 20 |
| Context text | 16,000 code points | 40,000 code points |
| Citation occurrences | 64 | 64 |
| Citation locations | 4,096 | 100,000 |
| Prompt / response | 256 KiB / 128 KiB UTF-8 | 同默认值 |
| Statements | 32 | 32 |
| 单条 / 累计 statement text | 4,000 / 16,000 code points | 同默认值 |
| 每条 statement citations | 64 | 64 |

调用方只可缩小上限；单条文字/引用不能大于对应总预算。Query 继续最多 1,886 code points，其他检索预算见 [Retriever](./08-RETRIEVER-RERANKING.md)。Prompt 在选择、request 构造和调用前复核；statement 限制复制到 generator policy 并绑定 fingerprint。

结果保留 schema、Scope、status、可选公开 generator identity、context count、statements/citations；不保留 Query、Prompt、request fingerprint、raw response、私有 lineage 或分数。Excerpt 与文件名仍是用户数据，只应在授权上下文中使用。

## 集成与验证

[Project Sources](./10-PROJECT-SOURCES.md) 在共享 lease 内派生授权并于生成后复核；[Knowledge Lifecycle](./11-KNOWLEDGE-LIFECYCLE.md) 负责变更/恢复；Brain 原子保存 Assistant text 与 proof，Desktop 以严格字段白名单发布。

从仓库根目录运行：

```powershell
python -m pytest tests/test_document_grounding.py tests/test_ollama_grounding.py -q
```

文档检查见 [AGENTS.md](../AGENTS.md)。当前合同不提供 Agentic RAG、tool calls、语义 verifier、远程 Generator fallback 或自动模型下载。
