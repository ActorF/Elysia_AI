# 文档清洗与分块

`DocumentProcessingService` 将 [Loader](./05-DOCUMENT-LOADERS.md) 的 `LoadedDocument` 转换为 `CleanedDocument` 和 `ChunkedDocument`，保留完整来源映射。该层是纯函数式派生：不读写文件、不访问时钟或网络、不调用模型、Tokenizer 或随机源。

```text
LoadedDocument
  → ConservativeDocumentCleaner
  → CleanedDocument：保留片段 + 删除审计
  → StructureAwareDocumentChunker
  → ChunkedDocument：稳定身份 + 来源映射
  → 下游 Embedding / SQLite / Retrieval
```

相同输入、版本和 Policy 产生相同结果。规则变化必须改变派生身份；不能在旧 Chunk ID 下静默更换文字或边界。

## 来源坐标

所有 offset 都是零基、左闭右开的 Unicode code-point 区间，只相对于 Loader 发布的结构：

- `DocumentTextSpan`：Block ordinal、文字区间及可选一基 PDF 页码。
- `DocumentTableCellSpan`：另含零基行列；空 Cell 可用空区间锚定派生标点。
- `ChunkSourceMapping`：把 Chunk 内的区间映射到上述来源。

这些坐标不是原始字节、PDF glyph/bounding box、DOCX XML/run 或 CSV 磁盘行号。原文件视觉定位需要独立的格式专属合同，不能重新解释这些 offset。

## 保守清洗

默认 Cleaner 为 `conservative-piece-table` `1.0.0`，Cleaned schema 为 `1`。

Cleaner 不做通用 trim、空白折叠、Unicode normalization、乱码修复或替换字符修复。代码缩进、CRLF、组合字符、ZWJ、双向文字、作者空白与表格值都可能有意义；无法客观证明无效的内容不会被猜测修改。坏编码与非法控制字符由严格文本 Loader 提前拒绝。

`CleanedDocument` 保存 Loaded provenance、Cleaner identity/policy、处理上限、标题/页数、fingerprints、blocks 与 omissions。文字块用有序 retained spans 指向准确原文；表格保留 ragged shape、空 Cell 和原值。每次删除必须有闭集 reason 与准确范围。

### 唯一删除规则：重复 PDF 页眉

只有以下证据全部成立，才删除每页首行及紧随的 CRLF、CR 或 LF：

1. PDF 实际页数达到 policy 最少页数，默认为 `3`。
2. 每页恰有一个带页码的 paragraph block，没有空页或多 Block 页。
3. 每页首行有显式行终止符、含有效文字、逐字相同且不超过 `256` code points。
4. 删除后每页仍有正文，首行也不等于 Document Title。

任一条件不成立，整份文档零删除。成功时逐页记录 `repeated_pdf_page_header` omission。全有或全无的规则避免把章节标题、短正文、缺页或 parser 布局差异误当作页眉。

## 结构分块

Chunk 类型闭集为 `prose | code | table`。默认单 Chunk 上限 `2,000` code points，v1 overlap 固定为 `0`，避免重复范围增加引用与资源计费歧义。

### Prose

标题始终开启边界；相邻短段落可用准确的 `\n\n` 合并，但不跨 PDF 页。长 Block 的后续片段不会回填其他 Block。

超长文字按窗口内最后一个候选边界切分，优先级为：空白行、逻辑换行、句末标点、ASCII space/tab、硬 code-point 上限。Cap 至少为 2 时不拆 CRLF；Cap 为 1 时只能拆开，但连接后仍逐字还原。

插入的段落分隔符是版本化派生文字，其 Chunk-local 区间不伪装为来源映射。

### Code

Code Block 不与其他结构或另一个 Block 合并。优先在完整逻辑行切分，单行超限时才硬切；缩进、空白和行终止符保留，不解析 AST 或执行源码。

### Table

表格使用版本化 `jsonl-v1` 投影，每个 ragged row 是一个 JSON array，行间为 LF：

```text
["name","value"]
["爱莉希雅","a\tb"]
[""]
```

非 ASCII 文字不转义或 normalization；quote、backslash、Tab、LF、CR 使用稳定 JSON escape。优先保持整行/整格，单 Cell 超限时以有界片段拆分。

引号、逗号、括号、escape 和行间 LF 属于派生语法，通过相关 Cell span 锚定。Projection version 进入 fingerprint；语法改变必须升级 Chunker 或 Policy，并重新派生。

## 确定性身份

Canonical UTF-8 JSON 使用固定形状、排序键、无 NaN，避免 locale 或对象 `repr` 影响 SHA-256：

| 身份 | 绑定内容 |
| --- | --- |
| `document_fingerprint` | Loaded schema、Source、Format、Loader/version、标题/页数与完整 raw blocks |
| `cleaning_fingerprint` | Document fingerprint、Source、Cleaner/version/policy、cleaned blocks 与 omissions |
| `derivation_fingerprint` | 上述 lineage、加载/处理上限、Chunker/version/policy、标题/页数 |
| `chunk_<sha256>` | Derivation、ordinal、kind、text、page 与完整 mappings |

Scope 或 ownership link 不同，即使字节相同也不会共享派生身份。Loader/parser、Cleaner、Chunker、schema、policy、limits、source metadata 或内容变化都必须使旧结果失效，并从可信 original 重新处理。

[SQLite Store](./07-LOCAL-EMBEDDINGS-VECTOR-STORE.md) 保存完整 lineage，读取时拒绝不匹配的 derivation；何时重建由 [知识生命周期](./11-KNOWLEDGE-LIFECYCLE.md) 决定。

## Adapter 隔离与复核

`DocumentSourceLoader`、`DocumentCleaner`、`DocumentChunker` 可注入，但返回值不被直接信任。Pipeline 对原始请求、各阶段权威结果、交给下一 Adapter 的输入和最终输出分别递归重建快照，防止绕过 frozen dataclass 的嵌套变异反写 lineage。

发布前独立复核：

- Scope/ownership、Loader result、Cleaner producer/policy/limits。
- Piece table 对每个 Block 的完整分区、文字来源和 cleaned fingerprints。
- Chunk lineage/policy、mapping 顺序与 bounds，以及 text mapping 的准确原文。
- 全部 table chunks 能否完整重建 canonical JSONL projection。

纯 Processing Service 只发布内存结果；Embedding、索引、删除传播与恢复由上层组合。Attachment manifest 不保存本层的独立 Chunk 派生记录。

## 预算与错误

| 资源 | 默认上限 |
| --- | ---: |
| Cleaned code points / blocks | 4,000,000 / 50,000 |
| Cleaning omissions / chunks | 2,000 / 10,000 |
| 全部 Chunk code points | 16,000,000 |
| 单 Chunk / 全部 source mappings | 4,096 / 1,000,000 |

增量构造和不可变结果构造都检查预算；单项上限不能大于对应总上限。所有长度按 Python Unicode code points 计数，不等同于 bytes、UTF-16 units 或 tokens。未来 token-aware、overlap 或 query-specific chunking 必须使用新的版本化 producer。

`DocumentValidationError` 表示领域值、身份或映射不一致；`DocumentContentLimitError` 表示预算不足；未知处理异常变为 `DocumentProcessingFailedError`。`MemoryError/RecursionError` 映射为 ContentLimit，底层诊断只保留在可信异常链。

## 验证

实现入口为 `documents/cleaning.py`、`chunking.py` 和 `pipeline.py`。测试覆盖无损文字/表格、页眉证据、边界、身份失效、预算与恶意 Adapter：

```powershell
python -m pytest tests/test_document_cleaning.py tests/test_document_chunking.py tests/test_document_processing_pipeline.py -q
```

运行位置为仓库根目录；文档检查见 [AGENTS.md](../AGENTS.md)。桌面接线见 [Knowledge UI](./12-KNOWLEDGE-UI-TESTING.md)。
