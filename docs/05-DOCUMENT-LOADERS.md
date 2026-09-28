# Document Loaders：可信文件读取与有界结构提取

本文记录 Elysia AI 的文档加载边界。它把 Stage 8 已保存的原始 Attachment 转换成稳定、可测试的原始文档结构；Loader 本身不会清洗、切块、生成 Embedding、建立 Vector Store、检索或生成 Citation。其下游保守 [Cleaning/Chunking](./06-DOCUMENT-CLEANING-CHUNKING.md)、[Local Embeddings/Vector Store](./07-LOCAL-EMBEDDINGS-VECTOR-STORE.md)、[Retriever/Reranking](./08-RETRIEVER-RERANKING.md) 与 [Grounded Answer/Citation](./09-GROUNDED-ANSWERS-CITATIONS.md) Contract 已作为独立 Python Library 完成；生产 Generator 和桌面接线仍未完成。

## 1. 完成范围

当前 Loader 支持以下精确格式：

| 文档类型 | 扩展名与 MIME | 保留的结构 |
| --- | --- | --- |
| Plain Text | `.txt` + `text/plain` | 空行分隔的 Paragraph |
| Markdown | `.md` / `.markdown` + `text/markdown` | ATX/Setext Heading、Fence Code、保守的 GFM Pipe Table、Paragraph、首个一级标题 |
| CSV | `.csv` + `text/csv` | Ragged Rows 与 Cell Text；公式前缀只当作文字 |
| 常见源码文本 | `.ini`、`.css`、`.htm`、`.html`、`.js`、`.jsx`、`.json`、`.py`、`.sql`、`.toml`、`.ts`、`.tsx`、`.xml`、`.yaml`、`.yml`，且 MIME 必须匹配注册表 | 单一 Code Block，不执行、不渲染 |
| PDF | `.pdf` + `application/pdf` | Embedded Title、实际 Page Count、每个非空页的一份 Raw Text Block 与一基页码 |
| DOCX | `.docx` + OOXML Word MIME | Embedded/Styled Title、Heading Level、Paragraph、Table，以及正文中的原始顺序 |

扩展名和已保存 MIME 是一个不可拆分的双键。只匹配其中一项不会回退到“尝试解析”；这种严格路由避免把不可信二进制内容误送给宽松文本解析器。扩展名比较不区分大小写，但 MIME 必须是 Canonical Exact Value。

## 2. 数据流与信任边界

```text
Renderer / caller supplies Scope + ownership link_id
  → DocumentLoaderService
  → list_file_ownerships(scope)
      resolve link-specific file_name + media_type + opaque file_id
  → list_file_records(scope)
      resolve canonical size_bytes
  → AttachmentService.open_verified_file(scope, file_id)
      authorize exact owner and verify immutable stored bytes
  → bounded in-memory byte snapshot
      repository context closes here
  → exact suffix+MIME Loader
  → LoadedDocument
      path-free source identity + ordered raw blocks + loader version
```

Loader 从不接收本机路径，也不会用文件名重新打开文件。唯一的选择键是已经通过 Scope 授权的 Ownership；共享内容即使有相同 File ID，不同 Chat/Project 仍必须通过各自的 `link_id` 和 Scope。解析发生在验证读取 Context 关闭之后，因此第三方 Parser 不会持有 Attachment Store 的句柄。

本模块目前是独立 Python Library，尚未由 `start.py` / `desktop_backend.py` 的生产 Composition Root 构造，也未新增 Desktop Protocol 或 React API。下游 Cleaning/Chunking、Embedding/SQLite Store、Retriever/Reranker 与结构化 Grounded Answer/Citation Library 均已完成；但仍无生产 Generator、Project Sources 授权或桌面接线，因此不能向桌面层暴露“向文件提问”能力。

## 3. 稳定领域模型

`documents/domain.py` 定义以下不可变值：

- `DocumentSource`：仅包含 Scope、Ownership Link ID、Opaque File ID、文件名、MIME 与大小；没有路径字段。
- `DocumentBlock`：按 `ordinal` 保留原始顺序，闭集类型是 `title`、`heading`、`paragraph`、`code`、`table`。
- `DocumentTable`：允许 Ragged Rows；Loader 不填充不存在的 Cell，也不截断来源数据。
- `DocumentTitle`：区分 `embedded` 与 `heading` Provenance，不把文件名猜成标题。
- `LoadedDocument`：固定 Schema Version、Format、Loader ID/Version、Source、Blocks、可选 Title/Page Count 和实际使用的 Limits。
- `DocumentLoadLimits`：把本次资源策略携带到输出，Service 会验证 Loader 没有更换 Source 或放宽 Policy。

Block Ordinal 不是 Chunk ID，也不是字符 Offset。清洗和切块会改变文字边界；已完成的版本化 Chunker 使用 `LoadedDocument` Code-point Span 和完整 Producer Lineage 定义可重复 Chunk Identity，不能把 Block Ordinal 直接当成 Chunk 或原文件坐标。

## 4. 资源上限

默认策略为：

| 资源 | 默认上限 |
| --- | ---: |
| 原始文件 | 16 MiB |
| 解压/展开总量 | 64 MiB |
| 输出文字 | 4,000,000 Unicode Code Points |
| Blocks | 50,000 |
| PDF Pages | 2,000 |
| DOCX ZIP Entries | 4,096 |
| Table Cells | 1,000,000 |
| Columns per Row | 256 |
| Cell Text | 32,768 Code Points |
| XML Elements | 250,000 |
| XML Depth | 128 |
| 单个 DOCX MC Directive | 65,536 Code Points / 4,096 Tokens |
| DOCX MC Tokens（所有已解析的选定 XML Part 累计） | 16,384 |
| DOCX 同时在 Scope 的 Namespace Bindings | 256 |
| DOCX Namespace Declarations（所有已解析的选定 XML Part 累计） | 8,192 |

输入大小会在打开文件前先用 Metadata 拒绝，再在实际短读循环中独立检查。最终 `LoadedDocument` 会再次验证全部输出预算。DOCX 还限制单成员大小和压缩比，并在构造 `ZipFile` 前核对 EOCD/Zip64 与实际 Central Directory Records。XML Element、MC Token 与 Namespace Declaration 由一个 Budget 在所有实际解析的选定 Part 之间累计：`[Content_Types].xml`、Package Relationships、Relationship 选出的 Main Document，以及存在时的 Main Relationships、授权 Styles 和授权 Core Properties；ZIP 中未被关系选中的其他 XML Member 不会进入 XML Parser，但仍受 Entry、Expanded Byte、Member Size 与 Compression Ratio 预算约束。PDF 使用请求局部的 pypdf Configuration 限制 Stream 解压、Page Tree、Outline、XObject 调用与 XMP 输入；Page/Form Content 另有 1 MiB 单 Stream、60,000 个预解析 Token、50,000 个单 Stream Operation、100,000 个累计 Operation 和 5,000 次 Form Invocation 上限，Font Mapping/Width 等语义对象另有 100,000 个累计 Entry 硬上限并同时服从调用方的 Structure Budget。

这些限制是 Admission 与 Parser 两层防线，不依赖攻击者提供的单一长度字段。

## 5. Text、Markdown、CSV 与源码

文本只接受严格 UTF-8、UTF-8 BOM，或由 BOM 明确声明的 UTF-16 LE/BE。UTF-32、坏 Unicode、NUL、Surrogate 与除 Tab/Newline/CR 之外的控制字符会被拒绝；Loader 不使用 Replacement Character 静默修复损坏内容。

Markdown Loader 是结构扫描器，不是 HTML Renderer：

- 保留 ATX/多行 Setext 标题、段落、Fence Code 和保守 Pipe Table；
- 不执行 HTML、JavaScript、Link、Image 或 Include；
- 不把无法确定的语法猜成更强的语义；
- 第一个 H1 可成为 `heading` 来源标题，同时保留原始 Heading Block。

CSV 使用本地线性状态机，而不是修改 Python `csv.field_size_limit` 的进程全局状态。Quoted Field、Escaped Quote、Whitespace/Empty Cell 与 CR/LF 被严格保留；Quoted 与 Unquoted 不会改变相同 Cell 的可加载性。以 `=`, `+`, `-`, `@` 开头的值只是文字，不会送给电子表格执行。

源码格式只产生 Code Block。Loader 不解析 AST、不执行代码、不解析外部实体，也不发起网络请求。

## 6. PDF

PDF Loader 固定使用 `pypdf==6.19.0`，并把 Parser 版本写入 `loader_version`，使未来 Parser 升级导致的输出变化可以被识别。

- 使用 Strict Reader，并先验证 `%PDF-` Signature。
- 所有加密 PDF 都返回稳定的 Encrypted Error；即使空密码可打开也不例外，因为密码与 Secret 生命周期不属于本模块。
- 缺失 `/Contents`、显式 Null `/Contents` 与无文字页都计入真实 `page_count`，但不会伪造空 Block。
- 每个有意义的页面文本形成一个 Paragraph Block，并携带一基 `page_number`。
- Page 与 Form 在文字提取前先走有界 Content Preflight；重复 Form 每次按实际调用累计 Bytes、Operation 与 Invocation，嵌套过深和循环引用会整体失败，不会返回部分页面文字。
- Font Preflight 会在 pypdf 建立字典前累计 ToUnicode Range、CID Width、Descendant Font、Type3 CharProc、Encoding Difference 与压缩 Font Stream；相同 Font Alias 或重复 Form 的每次实际解析都会重新计费，紧凑 Range 不能绕过语义展开上限。
- Operand Visitor 在 `Tj`、`TJ`、`'`、`"` 映射前按当前 `Tf` 与 `q`/`Q` Font State 预留最大输出；递归 Form 使用自身 Resources 与共享剩余文字预算的 Child Guard，因此单条 CMap 放大、连续操作、重复或嵌套 Form 都不能先物化超限字符串再被拒绝。
- Page 实例同时包裹 Form Dispatcher 与 Form Text Extractor；pypdf 原本会记录后忽略的嵌套 Parser/Limit/Dependency 异常会跨过其宽泛 Catch，并在 Loader Boundary 映射为稳定错误，绝不会把 Form 前已经提取的页面文字当成部分成功返回。
- Token Density 在 pypdf 物化 Operation Graph 前检查；Inline Image 的有界 Binary Samples 不会被误当作语法 Token。
- 只保留 PDF 能可靠提供的页面边界和 Embedded Title；不猜测 Paragraph、Heading 或 Table。
- 扫描件若没有可提取文字会返回 Empty，而不是伪称 OCR 已完成。

OCR、Layout Reconstruction、图像提取和 PDF Table Detection 均不是当前能力。当前限制属于同一 Python Process 内的防御性上限；若未来要处理更大或来源更复杂的 PDF，应把 Parser 移到受 OS 资源与 Deadline 约束的 Worker，而不是放宽这些上限。

## 7. DOCX

DOCX Loader 直接读取内存中的 OPC ZIP，不把成员解压到磁盘：

- 构造 Python `ZipFile` 前先读取固定大小 EOCD/Zip64 Metadata，并逐条扫描实际 Central Directory Records；伪造偏小的 Entry Count 不能推迟到标准库分配后才被发现；
- 拒绝绝对路径、空段、`.` / `..`、反斜杠、NUL、大小写别名、重复名、Symbolic Link、加密成员与未知压缩方法；
- 同时检查声明展开大小、累计展开大小、单成员大小、压缩比与实际读取长度；
- 仅接受 Package Relationships 中内部且唯一的 `officeDocument` Relationship，以及非 Macro 的标准 Word Main Content Type；可选 Core Properties 必须由 Package Relationship 选择，可选 Styles 必须由 Main Document 的 Relationship 选择，两者还必须具有各自精确的 Content Type；
- 不信任传统固定路径：没有对应 Relationship 授权的 `word/document.xml`、`word/styles.xml`、`docProps/core.xml` 等诱饵 Member 即使存在也不会被解释；
- 禁止 DTD/Entity，限制 XML Byte、Element、Depth、MC Directive/Token 和 Namespace Binding/Declaration；未知或与字节不一致的 XML Encoding 稳定映射为 Corrupt；
- Main/Styles、Package Relationships、Content Types 与 Core Properties 分别使用独立的 Namespace-level Profile；除下一项明确列出的 Opaque 词汇外，不受支持且非 Ignorable 的 Element/Attribute Namespace 会失败，Foreign Namespace 中相同的 Local Name 不会获得 Word/OPC 语义。该 Profile 不是完整 OOXML XSD Validator；结构提取器只解释明确允许的 Word Local Name 与上下文；
- DrawingML、Office Math/OMML、VML 与旧 Office Shape Namespace 在 Word Part 中作为已知 Opaque Subtree 整体跳过，不提取其中内容；它们不算 MCE “understood”，因此不能满足 `mc:Choice Requires` 或 `mc:MustUnderstand`；
- 实现 OOXML Markup Compatibility 的 `AlternateContent` / `Choice` / `Fallback`、`Ignorable`、`ProcessContent` 与 `MustUnderstand`：选择首个受支持 Choice，否则使用可选 Fallback；未选分支不暴露文字，只验证必须保持全局可信的 Wrapper/Prefix 语法；
- MC 状态以不可变增量链继承，空或重复声明复用父状态，避免“大规则集合 × 大量子元素”的复制放大；
- 只解析关系授权的 Main Document、可选 Styles 和可选 Core Properties；不加载 VBA、Embedded Object、Image，也不跟随外部 Relationship；
- 保留正文中 Paragraph/Heading/Title/Table 的顺序，忽略 Tracked Deletion 与不理解的 Extension Content；Table 按单次遍历增量限制 Column、Cell 与 Text，嵌套 Paragraph 不会重复计入祖先内容。

旧 `.doc` 和使用 OLE Wrapper 的加密 Office 文件不属于 DOCX 路由，并返回稳定的 Encrypted/Unsupported 语义。

## 8. 稳定错误

所有加载期公开失败都来自 `DocumentError` 层级；构造 Loader Registry 时发现重复路由等编程错误，仍使用 `TypeError` / `ValueError` 立即拒绝：

- `DocumentValidationError`
- `DocumentNotFoundError`
- `DocumentUnsupportedFormatError`
- `DocumentUnsupportedFeatureError`
- `DocumentEmptyError`
- `DocumentEncryptedError`
- `DocumentCorruptError`
- `DocumentLimitError`（资源预算错误的公共基类）
  - `DocumentTooLargeError`
  - `DocumentContentLimitError`
- `DocumentReadError`
- `DocumentLoadFailedError`

`DocumentUnsupportedFormatError` 表示扩展名/MIME 路由未注册；`DocumentUnsupportedFeatureError` 表示格式已经受支持，但活动内容强制要求当前 Consumer 尚未实现的能力；`DocumentCorruptError` 只表示语法、结构或内部一致性损坏。

底层路径、OS Error、ZIP Member、Hash、Native Object 或第三方 Parser 原文只保留在 Exception Chaining 中，不进入稳定消息。未来若把错误映射到 Desktop Protocol，应继续转成闭集 Code，而不是显示 `str(cause)`。

## 9. 文件职责与测试

| 文件 | 职责 |
| --- | --- |
| `documents/domain.py` | 不可变 Source/Title/Table/Block/LoadedDocument 与全部跨格式不变量 |
| `documents/exceptions.py` | 稳定、路径无关的错误分类 |
| `documents/protocol.py` | Loader ID、Version、Route 与 `load()` Adapter Contract |
| `documents/service.py` | Scope 授权、Ownership/Original Metadata 解析、Verified Snapshot、闭集路由、错误映射和 Adapter 输出复核 |
| `documents/text.py` | TXT、Markdown、CSV 与源码文本的有界结构提取 |
| `documents/pdf.py` | Strict PDF Reader、Null/缺失 Content 处理、Page/Text/Title 保留、解析前 Token Ceiling、Font Semantic Budget、Operand/Child Guard 文字早停、嵌套 Form 异常提升、重复 Form 累计与 pypdf 资源限制 |
| `documents/docx.py` | ZipFile 前 Central Directory/Zip64 预检、Relationship + Content Type Part 授权、分部 OPC/XML Namespace Profile、Opaque Drawing/Math 跳过、有界 MCE 选择、持久化兼容状态、增量 Table 预算与正文结构保留 |
| `documents/__init__.py` | 稳定公共 API |
| `tests/test_document_domain.py` | Domain Union、Ordinal、Page、Title 与累计预算 |
| `tests/test_document_loader_service.py` | 真实 Attachment Store 集成、Scope 隔离、Verified Read、Route 冲突与恶意 Adapter |
| `tests/test_document_text_loaders.py` | Encoding、Unicode 行边界、多行 Setext、CSV/Markdown 结构、Malformed Input、Route、预算与峰值内存回归 |
| `tests/test_document_binary_loaders.py` | PDF/DOCX 正常结构、Null Content、Form/Inline Image、CMap/CID/Type3 语义展开、映射前文字早停、嵌套 Form 错误提升、MCE/分部 Namespace Profile、Relationship 授权与固定路径诱饵、Opaque Drawing/Math、加密/损坏/不支持能力、EOCD/Zip64、ZIP/XML/PDF 资源边界、路径与外部关系边界 |

## 10. 明确非目标与下一步

本模块不做：

- Normalization、Boilerplate Removal 或语言清洗；
- Stable Chunk ID、Overlap 或 Token-aware Chunking；
- Derived File 持久化；
- Embedding、Vector Store、Retriever、Citation 或 Prompt Injection Defense；其中 Embedding/Vector Store 与 Retriever/Reranking 已由下游独立 Library 实现，仍不是 Loader 的职责；
- OCR、复杂 PDF Layout、Spreadsheet、Presentation 或旧 Office Binary Format；
- Renderer Preview 或“向 Chat 提问此文件”的 UI。

版本化 Cleaning/Chunking Contract、可重现 Chunk Identity 与 Page/Block/Cell/Offset Source Location 已在 [Document Cleaning and Chunking](./06-DOCUMENT-CLEANING-CHUNKING.md) 中完成；固定 Model/Space Identity、本地 Ollama Adapter 和 Scope-safe SQLite 索引见 [Local Embeddings and Vector Store](./07-LOCAL-EMBEDDINGS-VECTOR-STORE.md)；显式 Generation Allowlist、有界检索与可选 Reranker 见 [Retriever and Reranking](./08-RETRIEVER-RERANKING.md)；有限 Prompt、结构化陈述和可信位置见 [Grounded Answers and Citations](./09-GROUNDED-ANSWERS-CITATIONS.md)。生产持久化生命周期仍需登记 Attachment Derived Relation，并实现版本变化、替换和删除时的任务、重建与传播规则。
