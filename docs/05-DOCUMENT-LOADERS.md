# 文档加载

`DocumentLoaderService` 把已保存的 Attachment 转换为版本化、无路径的 `LoadedDocument`。加载器负责严格解码和结构提取，后续处理见 [清洗与分块](./06-DOCUMENT-CLEANING-CHUNKING.md)。

## 支持格式

扩展名与已保存 MIME 必须同时精确匹配；仅匹配一项不会尝试解析。扩展名忽略大小写，MIME 使用规范值。

| 类型 | 扩展名 / MIME | 保留结构 |
| --- | --- | --- |
| 文本 | `.txt` / `text/plain` | 空行分隔的段落 |
| Markdown | `.md`、`.markdown` / `text/markdown` | ATX/Setext 标题、围栏代码、保守的管道表格、段落；首个 H1 可作标题 |
| CSV | `.csv` / `text/csv` | 不等长行与准确单元格文字 |
| 源码 | `.ini .css .htm .html .js .jsx .json .py .sql .toml .ts .tsx .xml .yaml .yml` / 注册表指定 MIME | 单一代码块 |
| PDF | `.pdf` / `application/pdf` | 内嵌标题、实际页数、非空页文字与一基页码 |
| DOCX | `.docx` / 标准 OOXML Word MIME | 标题、标题级别、段落、表格及正文顺序 |

## 读取边界

```text
Scope + ownership link_id
  → 解析 link-specific 名称/MIME 与 canonical 大小
  → open_verified_file(scope, file_id)
  → 有界 immutable bytes；关闭仓库读取 context
  → 精确 suffix + MIME adapter
  → LoadedDocument
```

文件名不会被当作路径重新打开。相同 File ID 不产生跨 Scope 权限；格式适配器只收到已验证字节，不持有 Attachment Store 句柄。

Service 复核 Adapter 输出的 Source、Limits 和类型，拒绝替换来源或放宽预算。生产 [桌面组合层](./12-KNOWLEDGE-UI-TESTING.md) 已接通完整知识链，但普通 Chat 附件不会自动入库；必须显式添加为 Project Source 并完成索引。

## 输出合同

`documents/domain.py` 定义不可变值：

- `DocumentSource`：Scope、Link/File ID、显示文件名、MIME 和大小，无路径。
- `DocumentBlock`：连续 ordinal，类型为 `title | heading | paragraph | code | table`。
- `DocumentTable`：保留不等长行，不补齐或删减单元格。
- `DocumentTitle`：区分 `embedded` 与 `heading` 来源，不从文件名猜标题。
- `LoadedDocument`：Schema、Format、Loader ID/Version、Source、Blocks、可选 Title/Page Count 和本次 Limits。

Block ordinal 只标识加载结果中的结构顺序；它不是 Chunk ID、字符偏移或原文件位置。

## 资源预算

预算在 Metadata 预检、实际短读、增量解析和最终领域值构造时分别执行，不能依赖文件声明的单一长度。

| 资源 | 默认上限 |
| --- | ---: |
| 原始文件 / 解压展开总量 | 16 MiB / 64 MiB |
| 输出文字 / Blocks | 4,000,000 code points / 50,000 |
| PDF 页数 / DOCX ZIP entries | 2,000 / 4,096 |
| Table cells / 每行列数 / 每格文字 | 1,000,000 / 256 / 32,768 code points |
| XML elements / depth | 250,000 / 128 |
| 单个 MC directive | 65,536 code points / 4,096 tokens |
| 所有选定 XML parts 的 MC tokens / namespace declarations | 16,384 / 8,192 |
| 同时生效的 namespace bindings | 256 |

DOCX 另限单成员大小、压缩比和实际读取量。XML、MC、namespace 预算跨所有实际解析的关系授权 parts 累计；未选中成员不会送入 XML parser，仍受 ZIP 预算约束。

PDF 限制请求内的解压、Page Tree、Outline、XObject 和 XMP；内容流上限为单流 1 MiB、预解析 60,000 tokens、单流 50,000 / 累计 100,000 operations、5,000 次 Form 调用。Font 语义展开累计最多 100,000 entries，同时服从调用方更小的结构预算。

## 文本、Markdown 与 CSV

文本只接受严格 UTF-8、UTF-8 BOM，或 BOM 声明的 UTF-16 LE/BE。UTF-32、损坏 Unicode、NUL、surrogate 和 Tab/LF/CR 之外的非法控制字符会被拒绝，不用替换字符掩盖错误。

Markdown 只扫描结构，不渲染 HTML、不执行脚本，也不访问链接、图片或 Include；含糊语法保持保守解释。源码只形成代码块，不解析 AST 或执行内容。

CSV 使用局部线性状态机，避免修改 `csv.field_size_limit` 的进程全局状态。引号、转义引号、空白、空格和 CR/LF 按单元格语义保留；`= + - @` 前缀只是文字，绝不执行公式。

## PDF

当前依赖固定为 `pypdf==6.21.2`，实际 parser 版本进入 `loader_version`。

加载器先验证 signature 并使用 strict reader。所有加密 PDF 都失败，包括可用空密码打开的文件；密码生命周期不在本层。缺失或 null `/Contents`、无文字页仍计入页数，但不伪造空 Block。每个有意义页面产生一个带页码的段落，只保留可证明的页面边界和内嵌标题。

防御发生在大对象分配之前：

- Page/Form 预检累计每次实际调用的 bytes、operations 和 invocation，拒绝过深或循环引用。
- Font 预检累计 ToUnicode ranges、CID widths、Descendant Fonts、Type3 CharProcs、Encoding Differences 和压缩 font streams；紧凑 range 与重复 alias 不能绕过展开预算。
- Operand guard 在字符映射前按 `Tf`、`q/Q` 状态预留文字预算，嵌套 Form 共享剩余预算。
- Form dispatcher/extractor 将 pypdf 原本会捕获后忽略的内部异常提升到加载边界，不能把部分页面当作成功。
- Token density 在 operation graph 构造前检查，inline image samples 不当作语法 tokens。

无可提取文字的扫描件返回 Empty。该加载器不提供 OCR、视觉布局重建、图像或表格识别。限制仍在同一 Python process 内；扩大不可信文档处理规模时，应先加入有 OS 资源和 deadline 限制的 worker。

## DOCX

加载器直接读取内存中的 OPC ZIP，不解压到磁盘：

1. 构造 `ZipFile` 前验证 EOCD/Zip64 和实际 central-directory records，避免伪造 entry count 引发过量分配。
2. 拒绝危险路径、大小写别名、重复成员、symlink、加密成员和未知压缩方法；同时核对声明与实际展开量。
3. 仅接受唯一的内部 `officeDocument` relationship 和非 Macro 的标准 main content type。Styles/Core Properties 同样需要对应 relationship 与精确 content type；固定路径诱饵没有解释权限。
4. 禁止 DTD/entity，限制 XML 编码、元素、深度和 namespace。Main/Styles、Relationships、Content Types、Core Properties 各自使用 namespace profile；外国 namespace 的同名元素没有 Word/OPC 语义。这不是完整 XSD validator。
5. 实现有界 MCE `AlternateContent/Choice/Fallback`、`Ignorable`、`ProcessContent`、`MustUnderstand`。未选分支不输出文字；必要 wrapper/prefix 语法仍验证。兼容状态用不可变增量链继承，避免大规则集合被逐节点复制。
6. DrawingML、OMML、VML 和旧 Office Shape 子树整体跳过；这些词汇不能满足 MCE “understood”。外部关系、VBA、嵌入对象与图片不加载。
7. 保留正文结构顺序，忽略 tracked deletion 与不理解的扩展；表格增量限量，嵌套段落不重复计入祖先。

旧 `.doc` 和 OLE 包装的加密 Office 不属于 DOCX 路由，返回稳定的 encrypted/unsupported 语义。

## 错误与验证

加载错误统一来自 `DocumentError`：Validation、NotFound、UnsupportedFormat、UnsupportedFeature、Empty、Encrypted、Corrupt、TooLarge、ContentLimit、Read、LoadFailed。UnsupportedFormat 表示没有路由；UnsupportedFeature 表示活动内容要求未实现能力；Corrupt 表示语法或内部一致性损坏。

路径、ZIP member、hash、parser 原文只留在可信异常链，不进入公开消息。Desktop 将错误映射到闭集协议 code。Registry 重复路由等构造期编程错误仍立即抛出 `TypeError/ValueError`。

实现入口为 `documents/service.py`、`text.py`、`pdf.py`、`docx.py`。从仓库根目录验证：

```powershell
python -m pytest tests/test_document_domain.py tests/test_document_loader_service.py tests/test_document_text_loaders.py tests/test_document_binary_loaders.py -q
```

后续边界：[清洗与分块](./06-DOCUMENT-CLEANING-CHUNKING.md) · [知识生命周期](./11-KNOWLEDGE-LIFECYCLE.md)。文档检查见 [AGENTS.md](../AGENTS.md)。
