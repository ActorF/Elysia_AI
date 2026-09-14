# Elysia AI 源码文件导览

这份文档用于帮助第一次接触 Elysia AI 的开发者理解：每个受版本控制的文件负责什么、它与哪些层连接，以及修改某项功能时应该从哪里开始。

> 当前架构边界：Python 是 Chat、Project、Memory、Attachment 和持久化状态的事实来源；Electron Main 是本地进程、文件路径和硬件权限的可信边界；Preload 只暴露固定能力；React Renderer 只负责显示和临时交互状态。

## 1. 先看完整连接图

```text
React Component
    │ callback / local UI state
    ▼
desktop/src/App.tsx
    │ window.elysiaDesktop
    ▼
desktop/electron/preload.cts
    │ fixed IPC channel
    ▼
desktop/electron/main.ts
    │ sender / type / path / permission validation
    ▼
desktop/electron/backend-process.ts
    │ authenticated NDJSON Protocol v1 over stdin/stdout
    ▼
desktop_backend.py
    │ request routing and error translation
    ├── DesktopSettingsRepository
    ├── VoiceSettingsService
    ├── TranscriptionJobRunner → FasterWhisperTranscriber
    └── initialize
        ├── start.create_brain()
        │   ├── Brain
        │   ├── ActiveConversationService
        │   ├── ChatRepository
        │   ├── ProjectChatService / ProjectRepository
        │   ├── Memory / MemoryRetriever
        │   ├── Legacy Migration
        │   └── Ollama model adapter
        └── JsonAttachmentStore + owner reconciliation

start.create_data_portability_service()
    └── 独立 Recovery API；当前没有接入 Desktop Protocol/UI
```

返回路径相反：Python 发出 response/stream frame，Electron 校验 request ID、stream sequence 和最终结果，React 再把 Canonical State 与本地 Streaming Overlay 合并显示。

一次正在显示的流式回复不等于已经保存的 Chat 消息。只有模型完整结束、请求没有被取消、Chat 没有发生并发变化时，Python 才原子提交完整的 User/Assistant Turn。

## 2. 分层词典

| 分层 | 负责什么 | 不应该负责什么 |
| --- | --- | --- |
| Domain | 数据含义、稳定 ID、业务不变量 | UI、文件路径、网络调用 |
| Serialization | Domain 与严格 JSON 的双向转换 | 跨对象业务流程 |
| Repository | 持久化位置、原子读写、索引恢复 | Prompt、React 状态 |
| Service | 生命周期、并发、跨 Repository 回滚 | 直接渲染页面 |
| Brain | 组织 Chat、Memory、Prompt、Model 等用例 | 硬编码 JSON 路径 |
| Desktop Backend | 协议路由、任务生命周期、错误转换 | 自己创造另一份业务事实 |
| Electron Main | 窗口、子进程、真实路径、原生权限 | Chat/Project 持久化规则 |
| Preload | 固定且最小的 Renderer 能力桥 | 暴露任意 IPC、Node 或 `fs` |
| React Renderer | 展示、表单、焦点和短暂 UI 状态 | 直接读写 Workspace JSON |

## 3. 根目录、CI、文档与资料

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `.github/workflows/tests.yml` | GitHub Actions 入口；先检查双端源码文档覆盖，再在 Ubuntu 运行 Python pytest/mypy 和 Desktop lint/typecheck/protocol/UI/build，在 Windows 解析 PowerShell 源码并运行原生 Attachment Bridge 测试。 | `scripts/check_python_documentation.py`、`desktop/package.json`、Python/Desktop 测试 |
| `.gitignore` | 排除 `.venv`、Cache、日志、构建产物、私人 `workspace`、`.env` 和模型权重。 | Git 工作树与本地运行数据边界 |
| `AGENTS.md` | 全仓库源码注释规范；要求文件说明、公开 API 文档、复杂算法/设计/边界原因和具体 TODO/FIXME，并禁止逐行复述普通语句。 | 所有后续源码修改、双端文档覆盖检查、Code Review |
| `mypy.ini` | 固定 Python 静态类型检查路径规则；只排除被忽略的 `models/cache/` 外部 Runtime，不能误排其他名为 cache 的源码。 | 本地 mypy、GitHub Actions、第三方 Runtime 边界 |
| `pytest.ini` | 把自动发现根固定为 `tests/`，防止被忽略的第三方 Runtime 自带测试污染项目验收。 | pytest、本地 `models/cache/` |
| `README.md` | 中文项目首页；描述功能状态、架构、CMD 启动、测试、隐私和当前限制。 | 新用户入口；链接 Desktop/Protocol/ADR 文档 |
| `README.en.md` | 与中文 README 对应的英文首页。 | 对外英文说明；应与 `README.md` 同步维护 |
| `MODEL_LICENSE.md` | 说明角色语料、GPT-SoVITS 权重、参考音频等来源与权利边界；不是源码许可证。 | `data/characters/`、本地 `models/weights/`、发行边界 |
| `SOURCE_FILE_GUIDE.md` | 当前这份逐文件源码导览；记录文件职责、调用边界、测试映射和新人阅读顺序。 | 全仓库源码、配置、文档与测试 |
| `requirements.txt` | 固定基础 Python Runtime、LangChain Ollama、pytest、mypy、jsonschema 等依赖版本；不强制安装本地 STT Native Runtime。 | `.venv`、CI、`start.py`、`desktop_backend.py` |
| `requirements-stt.txt` | 固定可选的 Faster-Whisper 与 NumPy 版本；只在需要本地单句转写时叠加安装，不包含或下载模型权重。 | `voice/faster_whisper.py`、本地 `.venv`、`models/weights/faster-whisper/<model>` |
| `scripts/__init__.py` | 把维护脚本标记为可导入 Package，使 Smoke CLI 能同时按模块与文件路径测试。 | `scripts/smoke_gpt_sovits.py`、测试 |
| `scripts/check_python_documentation.py` | 用标准库 AST 检查所有受维护 Python 文件的 module、public class、public function/method docstring 覆盖。 | `AGENTS.md`、GitHub Actions、Python 开发验证 |
| `scripts/smoke_gpt_sovits.py` | 用固定中文句子对每个所选情绪重复两次本地合成，只输出 Readiness、格式、大小、时长和 SHA-256；不接受任意文本，也不保存音频。 | `voice/synthesis_service.py`、`.env`、被忽略的 Voice Profile Catalog |
| `start.py` | Python Composition Root 和 Console 入口；创建 Settings、Model、Memory、Repositories、Migrator、Services、Brain 和日志。 | 几乎所有 Python 生产包；`ui/console.py`、`desktop_backend.py` |
| `desktop_backend.py` | Electron 启动的 Python NDJSON 进程；完成会话令牌握手、初始化、方法路由、Streaming、Cancel、错误映射和安全关闭；从 Active Settings 构造本地模型路径，惰性创建有界 STT Runner，并让 Chat/Settings 写入与物理占用中的转写互斥。 | `desktop_protocol/`、`start.py`、Chat/Project/Attachment/Voice 服务 |
| `docs/decisions/0001-desktop-shell.md` | Electron 与 Tauri 选型 ADR；记录测量方法、能力差距、风险、最终选择和重访门槛。 | `desktop/benchmarks/measure-shell.ps1`、Desktop 技术决策 |
| `data/characters/elysia_character_reference_zh.md` | 爱莉希雅背景、语录和转写参考资料；当前 Runtime 不会自动将它注入每次 Prompt。 | 人工角色研究；受 `MODEL_LICENSE.md` 的来源/授权提醒约束 |

本机还存在被 Git 忽略的 `docs/02-ROADMAP.md`。它是当前 Stage/Module 规划来源，但新的 Git Clone 不会自动得到它，因此不能作为唯一公共文档。

## 4. Python 预留 Namespace

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `agent/__init__.py` | 为未来 Work/Desktop Agent 保留稳定 Python Package 位置；当前没有已实现 Agent。 | 未来 `tools/`、权限系统与 Work Mode |
| `models/__init__.py` | 为 model-specific integration 保留 Namespace；实际 Ollama/GPT-SoVITS 权重不存放在 Python Package 中。 | `core/*chat_model.py`、被忽略的本地模型目录 |
| `tools/__init__.py` | 为未来经过权限控制的工具保留 Namespace。 | 未来 Agent、文件/桌面/网络能力 |

这些小文件不是临时垃圾；它们明确了未来模块边界。

## 5. Python 配置层：`config/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `config/__init__.py` | 配置包的稳定公开导出。 | `start.py`、`desktop_backend.py`、测试 |
| `config/settings.py` | 从安全默认值和根目录 `.env` 创建不可变 `AppSettings`；除 Chat/Ollama/Memory 与 STT 闭集外，还限制 GPT-SoVITS 本地评估开关、请求/探测超时和确定性 Seed。 | `start.py`、Model Adapter、Memory、Recovery、Faster-Whisper 与 TTS Composition |
| `config/desktop_settings.py` | Desktop Settings Store；迁移旧五字段文档并对八个公开字段做 allowlist、Revision CAS、Desired/Active Restart Diff、线程/进程锁、原子替换和损坏隔离。 | `desktop_backend.py`、Settings UI、`workspace/settings/global.json` |
| `config/voice_profiles.example.json` | 只含原创占位内容的严格 Voice Profile Schema v2 示例；每个资产声明都要求相对 `path`、实际 `bytes` 和小写 `sha256`，示例数字与 Hash 必须替换。 | `workspace/settings/voice-profiles.json`、`voice/profiles.py` |

## 6. Python 核心协调层：`core/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `core/__init__.py` | 汇出 Core 的稳定公共类型、服务和异常。 | 让入口与测试避免依赖包内实现路径 |
| `core/chat_model.py` | 定义 `generate_reply` / `stream_reply` 最小 Model Protocol 和轻量消息类型。 | `core/brain.py`、两个 Ollama Adapter、Fake Model 测试 |
| `core/ollama_chat_model.py` | 直接使用 Ollama HTTP API；检查模型是否安装、执行非流式调用并翻译连接/HTTP/JSON 错误。 | `config/settings.py`、本地 Ollama；生产 Adapter 复用可用性检查 |
| `core/langchain_ollama_chat_model.py` | 当前生产 Model Adapter；把项目消息转成 LangChain Message，调用 invoke/stream，并拒绝空或非文本响应。 | `core/chat_model.py`、`core/brain.py`、Ollama |
| `core/prompts.py` | 构建 Elysia System Prompt，将 Profile、Scoped Memory、Chat/Project Context 作为明确 JSON 数据加入。 | `core/brain.py`、`memory/retrieval.py`、Project Instructions |
| `core/model_memory_extractor.py` | 调用模型提取待用户确认的 Memory Candidate；严格解析 JSON、验证和去重，不自动保存。 | `core/brain.py`、`memory/extraction.py`、Long-Term Memory |
| `core/model_conversation_summarizer.py` | 调用模型生成 facts、decisions、action items、unresolved questions；支持增量摘要。 | `core/brain.py`、`memory/summarization.py`、`ChatSummary` |
| `core/active_conversation.py` | 管理 per-Chat Busy Guard、不可变快照、完整 Turn/Summary Commit 和并发修改检测；后来扩展 Chat actions、Retry 与 Attachment Commit。 | `core/brain.py`、Chat/Project Repository |
| `core/brain.py` | 应用用例总协调器；组织 Chat/Project API、上下文重建、Scoped Retrieval、Prompt、模型调用、Streaming、Cancel、Retry、Summary 和 Memory。 | Core、Chat、Project、Memory、Model、Desktop Backend/Console |
| `core/exceptions.py` | 定义配置、模型、Busy、Cancel、Retry、Model Mismatch、生成期间状态变化等稳定错误。 | `core/brain.py`、`desktop_backend.py`、Console、测试 |

## 7. Chat 领域与持久化：`chats/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `chats/__init__.py` | Chat Package 的稳定公共 API。 | `start.py`、Brain、Project/Recovery、测试 |
| `chats/domain.py` | 定义 ChatSession、ChatSessionMeta、ChatMessage、ChatSummary、AttachmentMetadata、稳定 ID 和全部不变量。 | Serialization、Repositories、Brain、Protocol 转换 |
| `chats/exceptions.py` | 定义 Not Found、Already Exists、Storage、Corruption 和 Migration 错误。 | Repository、Migrator、Recovery、入口错误映射 |
| `chats/serialization.py` | 严格转换 Chat Domain 与 JSON；处理 UTC 时间、Schema、Message、Summary、Attachment 和 Index Metadata。 | `chats/repository.py`、Recovery、测试 |
| `chats/storage.py` | Chat Store 的原子 UTF-8 JSON I/O：同目录临时文件、flush、fsync、`os.replace` 和失败清理。 | `chats/repository.py`、`chats/migration.py` |
| `chats/repository.py` | `ChatRepository` Protocol 与 JSON 实现；管理轻量 Index、独立 Session、CRUD、Pin/Archive、回滚和 Index 重建。 | ActiveConversation、Project Service、Migration、Recovery |
| `chats/migration.py` | 把 Stage 4 单一 Conversation 安全迁移为 `Legacy Conversation`；保存原始 Backup 和 Migration State。 | `chats/legacy.py`、Chat Repository、`start.py` |
| `chats/legacy.py` | 共享旧 Conversation 解析与语义前缀比较；允许 Legacy Chat 在迁移消息之后继续追加新消息。 | Migration、Recovery；修复早期 “no longer matches its source” 问题 |

运行时主要文件：

```text
workspace/chats/index.json
workspace/chats/sessions/chat_<id>.json
```

`index.json` 只放列表 Metadata；完整消息和摘要只放在对应 Session 文件中。

## 8. Project 领域与关系：`projects/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `projects/__init__.py` | Project Package 的稳定公共 API。 | `start.py`、Brain、Recovery、测试 |
| `projects/domain.py` | 定义 Project、ProjectSettings、WorkspaceBinding、稳定 ID 和业务不变量。 | Serialization、Repository、Prompt Context、Protocol |
| `projects/exceptions.py` | 定义 Project Storage、Not Found、Relation、Delete Policy 和 Rollback 错误。 | Repository、ProjectChatService、Desktop Backend |
| `projects/serialization.py` | Project Domain 与严格 JSON 的双向转换；拒绝未知字段。 | `projects/repository.py`、Recovery |
| `projects/storage.py` | Project Store 的原子 JSON I/O。 | `projects/repository.py` |
| `projects/repository.py` | 管理 `workspace/projects/projects.json` 的 Project CRUD、Settings、Workspace 和 Archive。 | ProjectChatService、ActiveConversation、Recovery |
| `projects/service.py` | 协调 Project 与 Chat Repository；管理 Chat attach/detach/transfer、删除策略、Busy Guard 和跨 Store 回滚。 | Brain、Desktop Backend、Chat/Project Repository |

Project 不保存另一份 Chat ID 列表。Project 与 Chat 的唯一关系来源是：

```python
ChatSession.project_id
```

## 9. Memory：`memory/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `memory/__init__.py` | Memory Package 的稳定公共 API。 | `start.py`、Brain、测试 |
| `memory/file_manager.py` | 基础 UTF-8 文本文件读写工具。 | 早期 Memory 存储实现 |
| `memory/json_store.py` | Stage 2–4 遗留的通用 JSON Object Store；语义比新 Chat/Project Store 简单。 | Profile、旧 Conversation/Memory 组件 |
| `memory/conversation.py` | Stage 4 旧单 Conversation 格式和操作；当前主要作为 Legacy Migration 输入。 | `memory/manager.py`、Migration、Recovery |
| `memory/conversation_summary.py` | 旧单 Conversation Summary 的 TypedDict、Schema、读取和保存。 | Migration、Recovery、旧测试 |
| `memory/profile.py` | 保存用户/助手名称、语言、项目、Launch Count 等 Profile v1，并迁移旧 Profile。 | Memory Manager、Retrieval、Prompt |
| `memory/short_term_memory.py` | Token-bounded complete-turn buffer；超限时移除最旧完整 Turn。 | Brain 为每个 Chat 重建独立近期上下文 |
| `memory/extraction.py` | 定义 `MemoryCandidate` 和 `MemoryExtractor` Protocol。 | ModelMemoryExtractor、Brain、Console 确认流程 |
| `memory/summarization.py` | 定义 `ConversationSummarizer` Protocol。 | ModelConversationSummarizer、Brain |
| `memory/long_term_memory.py` | 长期记忆 Schema、Global/Project/Chat Scope、CRUD、过滤、搜索、导出和 Stage 4 数据迁移。 | Memory Manager、Retriever、Brain |
| `memory/scope.py` | 定义 MemoryScopeRef/Context，并计算当前 Chat 可读的 Chat → Project → Global 范围。 | Long-Term Memory、Retriever、Prompt、Project/Chat Domain |
| `memory/retrieval.py` | 合并 Profile、Chat Summary 与允许范围的长期记忆；执行 Scope 过滤、同 Key 覆盖、相关度和可信度排序。 | Brain、Prompt、Memory Store |
| `memory/manager.py` | Memory Facade；集中维护 Profile、旧 Conversation、Summary 和 Long-Term Memory 路径。 | `start.py`、Brain、Console |

## 10. Recovery：`recovery/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `recovery/__init__.py` | Data Portability 的稳定公共 API。 | `start.py`、测试、未来管理 UI |
| `recovery/exceptions.py` | 定义 Export、Import、Validation、Conflict 和 Rollback 错误。 | Recovery Service、入口错误处理 |
| `recovery/service.py` | 导出/导入 Chat、Project 和 User Data Bundle；校验 Schema/Hash/路径/引用，事务恢复，失败回滚并隔离无效文件。 | Chat/Project Repository、Workspace JSON、Migration State |

## 11. Attachment：`attachments/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `attachments/__init__.py` | Attachment Package 的稳定公共 API。 | Desktop Backend、Brain、测试 |
| `attachments/domain.py` | 定义不包含真实路径的 AttachmentScope、AttachmentItem 和 AttachmentState。 | Store、Protocol、Renderer-safe State |
| `attachments/exceptions.py` | 定义稳定、不会泄漏本地路径的 Attachment 错误。 | Store、Desktop Backend |
| `attachments/store.py` | 安全 Blob/Manifest Store；Scope 隔离、扩展名/大小/数量限制、SHA-256、去重、Draft/Claim/Commit、进程锁、崩溃对账和路径防护。 | Electron 文件选择、Desktop Backend、Chat Message Commit |

当前 Attachment 只进行安全存储与 Metadata 管理，不解析文件内容，也没有 Chunking、Embedding、Vector Store 或 RAG。

## 12. Voice Python 层：`voice/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `voice/__init__.py` | Voice Package 的稳定公共 API。 | Desktop Backend、测试 |
| `voice/domain.py` | 定义输入/输出设备的 opaque ID 偏好和 Voice Settings Snapshot。 | Voice Service/Storage、Protocol |
| `voice/exceptions.py` | 定义 Voice Settings 和当前 Capture Validation 错误。 | Voice Service/Storage、Desktop Backend |
| `voice/faster_whisper.py` | 实现离线优先的 Faster-Whisper Adapter、严格本地模型完整性检查、净化后的就绪状态、设备/Compute Policy、一次性 CUDA 初始化降级、PCM float32 转换和返回错误脱敏；只接受明确的绝对本地目录，不按别名下载。 | `desktop_backend.py` 把闭集名称映射到 `models/weights/faster-whisper/<model>`；由后台 Runner 调用 |
| `voice/storage.py` | 对 `audio-device.json` 执行 Revision CAS、线程/进程锁、原子替换和损坏隔离。 | Voice Service、`workspace/settings/audio-device.json` |
| `voice/service.py` | 提供硬件无关的设备偏好读取和更新；Python 不直接打开麦克风。 | Desktop Backend、Voice Repository |
| `voice/transcription.py` | 定义与具体识别引擎解耦的 Transcriber Protocol、请求、最终结果、语言范围和稳定错误。 | 复用 `VoiceCapture`；连接 Faster-Whisper Adapter、后台任务和 Desktop Backend |
| `voice/transcription_jobs.py` | 用固定 Daemon Worker、有界队列、Deadline、唯一终态和结果保留上限包装同步 Transcriber；取消/超时后保留物理容量直到 Native Call 返回，并丢弃迟到结果。 | `desktop_backend.py`、`voice/transcription.py`；不把 PCM、路径或底层异常放进 Snapshot |
| `voice/synthesis.py` | 定义引擎无关的 `SpeechSynthesizer` Protocol、严格请求/结果与稳定错误；对最大 32 MiB 的 PCM WAV、Ogg Opus 和受支持 ADTS AAC 子集完整检查 Container/Transport Framing，但不虚构 Codec 可解码保证。 | GPT-SoVITS Adapter、Local Synthesis Service、Fake 单元测试；未来播放器仍须处理 Decoder Failure |
| `voice/gpt_sovits.py` | 把领域请求映射到 GPT-SoVITS `/tts`；只允许 Loopback IP（`localhost` 先规范化）、禁用环境代理/Redirect/Retry，要求声明长度的 Identity WAV/AAC 响应，并以 `/openapi.json` 做脱敏可用性探测。 | 外部本地 GPT-SoVITS Runtime；不会切换远端进程的全局权重，也不会把 `service_binding_unverified` 冒充成 `ready` |
| `voice/profiles.py` | 严格读取 Schema v2 JSON Catalog，把 Profile、情绪、准确参考文本/语言和带长度、SHA-256 的资产声明解析到固定模型根；拒绝旧版字符串路径、Windows 路径别名和矛盾身份，并实施 `verified` 与显式 Opt-in 的 `local-evaluation-only` 权利标签。读取声明本身不声称文件或进程已验证。 | `config/voice_profiles.example.json`、`models/weights/gpt-sovits/`、Synthesis Service |
| `voice/synthesis_service.py` | TTS 的惰性 Composition Root；每次调用重载 Catalog，按逻辑 Profile/情绪构造 Adapter 请求，且服务构造本身不触碰磁盘或网络。 | `config/settings.py`、Profile Catalog、GPT-SoVITS Adapter、Smoke CLI |

`voice/capture.py` 是单句 PCM 验证边界，详见本文“Voice Capture、本地 STT 与本地 TTS”部分。STT 由 `voice/transcription_jobs.py` 接入 Python Desktop Backend，Electron/React 已消费最终的 PCM-free Transcript。TTS 是另一条 Python-only 边界：目前只连接本地 Smoke CLI，不经过 `desktop_backend.py`、Desktop Protocol、Electron 或 React。

## 13. Console UI：`ui/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `ui/__init__.py` | Console UI 的稳定公开导出。 | `start.py` |
| `ui/console.py` | 旧 Console Client；恢复/创建默认 Chat、流式输出、`/memory`、`/summarize`、候选记忆确认和退出。 | Brain、Memory；没有完整 Desktop Chat/Project 管理能力 |

## 14. Desktop 工程根目录：`desktop/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/.gitignore` | 排除 `node_modules`、`dist`、日志和常见本地编辑器文件；`dist-electron`、`out` 与 Playwright 输出由根 `.gitignore` 负责。 | npm/Vite/Electron/Playwright 生成物 |
| `desktop/README.md` | Desktop 开发指南；双 CMD 启动、本地 STT 可选安装/模型目录、Final Transcript 手工测试、架构边界、验证和打包说明。 | 根 README、Protocol README、npm scripts、`requirements-stt.txt` |
| `desktop/package.json` | npm 项目入口、React/Electron 依赖、开发/文档审计/测试/构建/打包脚本和 electron-builder 配置。 | 所有 Desktop 工具链 |
| `desktop/package-lock.json` | 固定完整 npm 依赖图和下载完整性，使 `npm ci` 与 CI 可复现；不要手工编辑。 | npm、GitHub Actions、安全审计 |
| `desktop/index.html` | Vite Renderer HTML 入口；定义 CSP、favicon、viewport、theme-color 和 `#root`。 | `desktop/src/main.tsx`、Vite、Electron Window |
| `desktop/vite.config.ts` | 配置 React Plugin、固定开发端口和生产相对资源路径。 | `npm run dev`、`npm run build:renderer` |
| `desktop/eslint.config.js` | ESLint Flat Config；启用 JS、TypeScript、React Hooks 和 React Refresh 规则。 | `npm run lint` |
| `desktop/playwright.config.ts` | 配置 Electron UI 测试目录、单 Worker、Timeout 和失败产物路径。 | `npm run test:ui`、`desktop/tests/ui/` |
| `desktop/scripts/check-documentation.mjs` | 从仓库根目录检查 JS/TS/JSX 文件说明及公开 callable JSDoc、PowerShell 脚本/模块 Help，以及 CSS/HTML/HTM 文件说明；精确排除被忽略的 `models/cache/` 外部 Runtime，但不会误排其他名为 cache 的受维护源码。 | `npm run docs:check`、`AGENTS.md`、GitHub Actions |
| `desktop/tsconfig.json` | TypeScript Solution Root，引用 Renderer 与 Node/Vite 配置。 | `npm run typecheck`、Renderer Build |
| `desktop/tsconfig.app.json` | React Renderer 的 DOM/ES/JSX/Strict TypeScript 配置；不直接 Emit。 | `desktop/src/`、Vite |
| `desktop/tsconfig.node.json` | `vite.config.ts` 的 NodeNext TypeScript 配置。 | Vite Config Type Check |
| `desktop/tsconfig.electron.json` | 编译 Electron `.ts/.cts` 到 `dist-electron`；Preload `.cts` 输出 CommonJS `.cjs`。 | `npm run electron:build`、Electron Runtime |
| `desktop/public/elysia-icon.png` | 由《崩坏3》爱莉希雅官方刻印制作的方形第三方品牌图；供 Browser favicon、开发/打包窗口、Tray 和 README 使用，不属于源码许可。 | `index.html`、Electron Main、README、Vite Public Assets、`MODEL_LICENSE.md` |
| `desktop/assets/elysia-icon.ico` | 同一官方刻印的多尺寸 Windows ICO 构建资源；不属于源码许可。 | `package.json`、electron-builder、Windows EXE/Installer、`MODEL_LICENSE.md` |
| `desktop/benchmarks/measure-shell.ps1` | Electron/Tauri 决策时使用的 Windows 启动、内存、进程树和正常退出 Benchmark。 | Desktop ADR；不参与正常启动 |

## 15. Electron 可信边界：`desktop/electron/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/electron/contracts.ts` | 定义 Renderer 可见的最小 Desktop API、Backend Snapshot/Event、Chat/Project/Settings/Attachment/Voice 类型；Voice 只暴露开始/取消、相关 Final/Error Event 与净化后的转写状态，不是 Python 原始 Wire Schema。 | Preload、Main、React、Mock Preload |
| `desktop/electron/preload.cts` | 用 `contextBridge` 暴露固定 `window.elysiaDesktop`；把一次性 STT 开始/取消映射到固定 IPC，并把净化 Event 转交 Renderer，不暴露 `ipcRenderer`、Node、`fs` 或进程句柄。 | React、Electron Main |
| `desktop/electron/main.ts` | Electron 主进程；创建带品牌图标的窗口/托盘，验证 Sender、Settings/STT 参数、请求 ID、路径和权限，注册固定 IPC，控制导航与应用关闭。 | Preload、BackendProcess、原生 Dialog/Clipboard/Audio、`public/elysia-icon.png` |
| `desktop/electron/backend-process.ts` | Python 子进程 Owner 和 Protocol State Machine；除 Handshake/Stream/Cancel 外，关联 STT Request/Session/Chat、拒绝并发生成与配置写入、净化 Progress/Error、丢弃 PCM Metadata，并只向 Renderer 发 Final/Error；Python stderr 不原样暴露。 | Main、`desktop_backend.py`、`protocol.ts` |
| `desktop/electron/protocol.ts` | TypeScript 端 Protocol v1 类型、Builder、Parser 和严格 Runtime Validation；覆盖 STT 配置枚举、exact Readiness Status、一次性 Voice Transcription 输入与 PCM-free Final Result，不把静态类型当安全边界。 | BackendProcess、共享 Schema/Fixtures、Contract Tests |
| `desktop/electron/protocol-text.ts` | 定义跨 Python/TypeScript 一致的 Unicode Code Point 长度、Blank Set 和 Trim 规则。 | `protocol.ts`、Python Contracts |
| `desktop/electron/renderer-source.ts` | 只允许准确的 Vite Root 或打包 `dist/index.html` 作为可信 Renderer 来源。 | Main、Permission Policy、测试 |
| `desktop/electron/audio-permission.ts` | 只为可信主窗口和主 Frame 放行 audio-only microphone 或 speaker selection。 | Main 的 Chromium Permission Handler |
| `desktop/electron/external-url.ts` | 只接受无 Credentials 的 HTTP/HTTPS URL，拒绝危险 Scheme、空白、NUL 和无 Host URL。 | Main、Markdown Link、系统浏览器 |

## 16. React Renderer 总入口：`desktop/src/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/main.tsx` | 初始化 React Root、StrictMode、ThemeProvider 和 ErrorBoundary；初始 Paint 后通知 Electron。 | `index.html`、`App.tsx`、Preload API |
| `desktop/src/AppErrorBoundary.tsx` | 捕获 React Render Error，显示可恢复错误并把焦点移动到错误区域。 | `main.tsx` |
| `desktop/src/App.tsx` | Renderer 总协调器；除 Canonical State、Draft、Retry、Attachments 与 Settings 外，还关联单句 Capture/STT Event，处理取消/迟到竞态，并把用户编辑后的 Final Transcript 显式写入当前 Chat 草稿。 | 所有 React Feature、`window.elysiaDesktop` |
| `desktop/src/App.css` | App Shell、Chat、Dialog、Settings、Voice、Responsive、High Zoom 和 Forced Colors 样式。 | `App.tsx`、Design Tokens |
| `desktop/src/desktop-api.d.ts` | 扩展 Browser `Window` 类型，声明可选 `elysiaDesktop`；不会实际创建 API。 | TypeScript、Preload Contracts |

`App.tsx` 的 LocalStorage 只保存 UI 恢复数据，例如 Chat Draft、Pending Send 和 Retry Draft。Python 返回的 Chat/Project 仍然是 Canonical State。

## 17. React Shell：`desktop/src/shell/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/shell/AppShell.tsx` | 排列 Sidebar、Workspace 和 Character Panel；在窄屏把侧栏变成 Modal，并管理 Focus Trap、Inert、Escape 和焦点恢复。 | `App.tsx`、Sidebar、Feature Views |
| `desktop/src/shell/Sidebar.tsx` | 显示新的 Elysia 品牌图标、主导航和 Chat History；支持搜索、Create/Open、Rename、Pin、Archive/Restore、Delete 和批量操作。 | App callbacks、Backend Canonical Chat List、`public/elysia-icon.png` |
| `desktop/src/shell/ChatActionDialog.tsx` | Rename、Archive、Delete 等 Chat 操作的可复用 `<dialog>` 和表单/焦点逻辑。 | Sidebar、App mutation callbacks |

## 18. React Chat：`desktop/src/chat/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/chat/ChatView.tsx` | 组合 Chat Header、Connection Status、Message Timeline、Feedback 和 Composer，并管理滚动。 | App、MessageView、Composer |
| `desktop/src/chat/Composer.tsx` | 受控 Textarea、附件、模型、麦克风测试/Voice 入口和 Send/Stop；保护中文 IME，Enter 发送、Shift+Enter 换行。 | ChatView、App callbacks；不直接访问 Electron API |
| `desktop/src/chat/MessageView.tsx` | User 消息以纯文本显示；Assistant 使用安全 GFM；禁止 Raw HTML/外部图片，支持复制、Regenerate、Edit and retry 和 Attachment Chips。 | App callbacks、Electron External URL/Clipboard |
| `desktop/src/chat/types.ts` | 定义只供 Renderer 展示的 Message/Streaming/Retry/Notice 类型；不是持久化 Schema。 | App、ChatView、MessageView |

## 19. React Project 与 Attachment：`desktop/src/projects/`、`desktop/src/attachments/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/projects/ProjectView.tsx` | 完整 Project UI；创建/打开/编辑/归档、Instructions、Workspace、Chat 归属、Sources 和 Settings。 | App、Desktop API、AttachmentSurface |
| `desktop/src/projects/ProjectView.css` | Project Split View、List/Detail、Tabs、Cards、Workspace、Dialog 和响应式布局。 | ProjectView、Design Tokens |
| `desktop/src/attachments/AttachmentSurface.tsx` | Chat/Project 共用的 Scope-bound Attachment UI；处理 Picker、Drag/Drop、Metadata、Remove、Pending/Error 和焦点恢复。 | App/Desktop API、Composer、ProjectView |

Project Memory 页面目前仍是明确 Placeholder。Project Source 只安全保存文件，还不会理解文件内容。

## 20. React Settings 与 Voice：`desktop/src/settings/`、`desktop/src/voice/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/settings/SettingsView.tsx` | 编辑 Chat/Ollama/Memory/Import 设置，以及 STT 模型、`auto|cuda|cpu` 设备和 `auto|zh|en` 默认语言；显示 Desired/Active、净化后的就绪状态、重启提示、主题和隐私边界。 | App、Desktop Settings/Voice Backend、ThemeProvider、`transcription-readiness.ts` |
| `desktop/src/settings/VoiceSettingsSection.tsx` | 设备偏好 UI；枚举麦克风/扬声器、保存 opaque ID、显示权限、刷新设备、运行短暂输入电平和输出音调测试。 | `audio-devices.ts`、Voice Desktop API |
| `desktop/src/voice/audio-devices.ts` | Stage 7 设备 Controller；构造时不请求权限，管理 enumerate、8 秒麦克风 Level Test、800 ms Speaker Tone、Race 和 Cleanup。 | VoiceSettingsSection、Browser MediaDevices/AudioContext |
| `desktop/src/voice/CallPreview.tsx` | 全窗口单句 Voice 页面；显示采集/转写/取消状态和可编辑 Final Transcript，要求用户显式放入或追加到 Composer；不显示 Partial Transcript，也不自动发送。 | App、Capture Controller、Voice Backend API |
| `desktop/src/voice/transcription-readiness.ts` | 把 Python/Electron 的闭合集合 STT Status/Reason 转成 Settings 与 Voice 共用的安全、可操作提示；绝不渲染模型路径或 Native Error。 | `App.tsx`、`SettingsView.tsx`、`electron/protocol.ts` |

## 21. Character、Design System 与 Theme

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/character/CharacterPanel.tsx` | 显示当前 Chat、模型、连接状态和 CSS Artwork Placeholder；不读写 Chat/Memory，也不是 Live2D。 | AppShell、Backend Snapshot |
| `desktop/src/design-system/tokens.css` | 集中定义颜色、字体、Spacing、Radius、Shadow 和 Motion Semantic Tokens。 | 全部 UI CSS、Light/Dark/Forced Colors |
| `desktop/src/design-system/global.css` | 导入 Tokens，并提供 Reset、字体、Root、表单、Focus、Selection、Scrollbar 和 Accessibility Defaults。 | `main.tsx`、整个 Renderer |
| `desktop/src/design-system/Icon.tsx` | 项目统一 SVG Icon Set；默认 Decorative，避免重复 Screen Reader Label。 | Sidebar、Composer、Views、Feedback |
| `desktop/src/design-system/Feedback.tsx` | 可复用 LoadingState、EmptyState、InlineAlert 和状态 Action。 | Chat/Project/Settings/Voice 页面 |
| `desktop/src/theme/ThemeProvider.tsx` | 管理 System/Light/Dark Theme、`elysia.theme` LocalStorage、Media Query 和 Electron Native Background 同步。 | `main.tsx`、SettingsView、Main IPC |

## 22. Desktop Protocol：`desktop_protocol/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop_protocol/README.md` | 人类可读 Protocol v1 文档；说明 Handshake、Capabilities、Streaming、Cancel、Settings、Attachment、Voice Capture/Transcription 和安全不变量。 | Python/TypeScript 实现和测试 |
| `desktop_protocol/schema/v1.schema.json` | Draft 2020-12 JSON Schema；描述所有 Client/Server Frame，并约束 STT 设置枚举、Readiness exact object、PCM/Final Result 和跨字段状态不变量。 | Shared Fixtures、Python/Node Contract Tests |
| `desktop_protocol/fixtures/v1.samples.json` | Python 与 TypeScript 同时读取的 Valid/Invalid Conformance Samples；包含 STT Settings Desired/Active、可用/不可用状态和敏感额外字段拒绝样本。 | `contracts.py`、`protocol.ts`、两端测试 |
| `desktop_protocol/contracts.py` | Python 端 TypedDict、严格 Parser、Runtime Validator 和 Response/Error/Stream/Event Builder；序列化安全 STT Status，拒绝路径、Native Message 与扩展字段。 | `desktop_backend.py`、Schema/Fixtures、Python Tests |
| `desktop_protocol/__init__.py` | 汇出协议常量、类型、Parser 和 Builder。 | Desktop Backend、测试 |

协议的 Python 与 TypeScript Parser 都是手写的，Schema 不是代码生成器。因此修改协议时必须同步维护两端和共享 Fixtures。

## 23. Desktop 测试：`desktop/tests/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/tests/check-documentation.test.mjs` | 验证源码发现会排除精确的 `models/cache/`，同时继续扫描 `core/cache/` 等受维护目录。 | Documentation Checker、`npm run test:contract` |
| `desktop/tests/protocol.contract.test.mjs` | 在 Node 中测试编译后的 Protocol Helpers 和 BackendProcess；覆盖双端 Fixture、STT exact Status/敏感字段拒绝、Request 关联、互斥、Cancel/Draining Race、PCM 不保留、Stream、URL 与 Permission Policy。 | `dist-electron`、Schema/Fixtures；使用 Fake Child，不启动真实 Python |
| `desktop/tests/ui/electron-main.cjs` | Playwright 专用 Electron Main；加载生产 Renderer Build，保持 Sandbox/Context Isolation，但不启动生产 Backend。 | UI Test、Mock Preload、`dist/index.html` |
| `desktop/tests/ui/mock-preload.cjs` | UI 测试专用 `elysiaDesktop` Fake；除 Canonical 状态外模拟 STT 开始/终态/取消、Readiness、延迟、失败、Reload 和 Race。 | App Shell UI Tests；不会进入生产包 |
| `desktop/tests/ui/app-shell.spec.ts` | Playwright 启动真实 Electron Renderer，覆盖 Chat/Project/Settings/Voice；STT 回归包括编辑、显式放入/追加草稿、取消迟到结果、Close、Fresh Retry 与安全 Readiness。 | Production React Build + Mock Backend |

## 24. Python 测试：`tests/`

| 文件 | 实际用途 |
| --- | --- |
| `tests/__init__.py` | 将测试目录标记为 Python Package。 |
| `tests/test_active_conversation_integration.py` | Brain 的多 Chat 上下文、Scoped Memory 和失败隔离。 |
| `tests/test_active_conversation_service.py` | Busy Token、快照、Turn/Summary Commit、Retry 和 Chat actions。 |
| `tests/test_attachment_service.py` | Attachment Store 生命周期、Manifest、锁、恢复和文件系统安全。 |
| `tests/test_brain.py` | Brain 的 Chat、Streaming、Memory、Summary、Retry、Cancel 和 Attachment 协调。 |
| `tests/test_chat_domain.py` | Chat、Message、Summary、Attachment Metadata、ID 和不变量。 |
| `tests/test_chat_repository.py` | Index/Detail、CRUD、原子失败、重启和 Index Recovery。 |
| `tests/test_console.py` | Console Commands、Streaming 和 Session 行为。 |
| `tests/test_conversation_summarization.py` | Model Summarizer、严格结构和增量摘要。 |
| `tests/test_conversation_summary.py` | Stage 4 旧 Summary Schema 与存储。 |
| `tests/test_data_portability.py` | Bundle Export/Import、Hash、路径、Conflict、Quarantine 和 Rollback。 |
| `tests/test_desktop_backend.py` | Python Bridge 的 Handshake、Routing、Streaming、Cancel、Chat/Project/Settings/Attachment，以及 STT Readiness、Admission、配置写互斥、终态与 Shutdown Race。 |
| `tests/test_desktop_protocol.py` | Python Protocol Parser/Builder 与共享 Fixture Contract；覆盖 STT 设置/状态的 exact shape 与脱敏边界。 |
| `tests/test_desktop_settings.py` | Desktop Settings 八字段 Validation、旧 Schema Migration、Desired/Active Restart Diff、Revision CAS、锁和 Quarantine。 |
| `tests/test_faster_whisper.py` | 不安装 Native Runtime 或模型也能验证离线 Adapter、设备降级、PCM、惰性结果、错误脱敏和边界。 |
| `tests/test_file_manager.py` | 基础文本文件操作。 |
| `tests/test_gpt_sovits.py` | Loopback URL 规范化、HTTP 请求映射、代理/Redirect/Retry/Transfer-Encoding 禁止、Content-Length、慢速 Body Deadline、Readiness、响应上限、格式与错误脱敏。 |
| `tests/test_json_store.py` | 早期通用 JSON Store。 |
| `tests/test_langchain_ollama_chat_model.py` | 生产 LangChain Ollama Adapter。 |
| `tests/test_legacy_conversation_migration.py` | Legacy Backup、幂等性、语义前缀和失败回滚。 |
| `tests/test_long_term_memory.py` | Long-Term Memory Schema、Scope 和旧格式迁移。 |
| `tests/test_memory.py` | Memory Facade、Profile 和旧 Conversation。 |
| `tests/test_memory_extraction.py` | Memory Candidate 提取、解析和人工确认。 |
| `tests/test_memory_management.py` | Memory Search/Edit/Delete/Export 和 Scope。 |
| `tests/test_memory_retrieval.py` | 相关度、来源、可信度、Scope 和不可信数据处理。 |
| `tests/test_memory_scope.py` | Scope/ID 验证和可读范围顺序。 |
| `tests/test_ollama_chat_model.py` | 直接 Ollama HTTP Adapter。 |
| `tests/test_profile.py` | Profile Validation 和 Migration。 |
| `tests/test_project_domain.py` | Project、Settings、WorkspaceBinding 不变量。 |
| `tests/test_project_repository.py` | Project Persistence、排序、Archive 和 Corruption。 |
| `tests/test_project_service.py` | Project–Chat 关系、删除策略、Rollback 和 Busy Guard。 |
| `tests/test_prompts.py` | Elysia 人格规则和 JSON 数据边界。 |
| `tests/test_python_documentation_check.py` | 文档扫描只排除精确的 `models/cache/`，不会把其他同名源码目录误排。 |
| `tests/test_scoped_memory_integration.py` | 跨 Chat/Project Memory 隔离和同 Key 覆盖。 |
| `tests/test_settings.py` | `.env`、STT 闭集配置/安全回退、GPT-SoVITS 本地评估 Opt-in、请求/探测 Timeout、Seed、默认值和基础 AppSettings。 |
| `tests/test_short_term_memory.py` | Token Budget 和完整 Turn 淘汰。 |
| `tests/test_smoke_gpt_sovits.py` | Smoke CLI 的固定文本、重复/多情绪、缓冲成功输出、格式摘要和闭集错误码。 |
| `tests/test_stage5_acceptance.py` | Stage 5 端到端验收：多 Project/Chat、Memory 隔离、重启和完整 Export/Import。 |
| `tests/test_start.py` | Composition Root、Migration 和配置限制。 |
| `tests/test_synthesis_service.py` | 惰性构造、Catalog 重载、Profile/情绪映射、权利 Opt-in 与离线错误。 |
| `tests/test_voice_capture.py` | Python 单句 PCM Capture Contract、Canonical Base64、Markers、边界和收据。 |
| `tests/test_voice_settings.py` | Audio Device Preferences、CAS、锁和损坏恢复。 |
| `tests/test_voice_profiles.py` | Voice Profile Schema、路径固定、准确 Prompt/语言、情绪选择与权利状态。 |
| `tests/test_voice_synthesis.py` | 引擎无关 TTS 请求/结果、不可变性、不可发声 Unicode、音频上限、PCM WAV/Ogg Opus/ADTS AAC Framing 与错误层级。 |
| `tests/test_voice_transcription.py` | 引擎无关的转写请求、最终结果、语言、置信度、不可变性和错误层级。 |
| `tests/test_voice_transcription_jobs.py` | 有界后台转写的 Admission、Worker/Queue Capacity、Cancel/Timeout Race、Native Draining、迟到结果丢弃、Retention 和 Shutdown。 |

## 25. Voice Capture、本地 STT 与本地 TTS

当前桌面 Voice 是“显式单句采集 → 本地最终转写 → 人工确认进入草稿”的有界流程。Python 另有独立的单次 TTS 基础，但尚未接入桌面。它们都不是持续会话；理解这一层时要分别看 Capture/STT/Renderer Handoff 与 Python TTS 两条数据流。

### Capture 核心文件

| 文件 | 当前职责 | 完成边界 |
| --- | --- | --- |
| `desktop/src/voice/audio-capture.ts` | 获取显式授权的单次麦克风输入；Downmix、流式重采样到 16 kHz、PCM16 Frame、生命周期和清理。 | 自身不识别文字，不持久化录音 |
| `desktop/src/voice/voice-activity-detector.ts` | 对 20 ms PCM Frame 做有界 VAD、Pre-roll、No-speech/Maximum Timeout 和尾静音结束判断。 | 只是保守 VAD，不产生 Partial Transcript |
| `desktop/tests/voice-capture.test.mjs` | 测试 Capture/VAD 时序、静音、固定音调、噪音、Speech-like Signal 和 Cleanup。 | Renderer Voice 单元回归 |
| `voice/capture.py` | 在可信 Python 边界验证 canonical Base64、16 kHz mono s16le、Frame Alignment、时长、Markers 和 SHA-256。 | 不打开麦克风，不调用 Brain |
| `tests/test_voice_capture.py` | 测试 Python PCM Capture Contract。 | Python Voice 单元回归 |

`voice.capture.complete` 仍是独立的安全 Receipt Primitive：它只返回格式、时长和 Digest，不执行 STT。当前 Voice 页面在 VAD 完成后改走下面的 `voice.transcription.start` 流程。

### 本地 STT 配置与 Python 核心

| 文件 | 当前职责 | 关键边界 |
| --- | --- | --- |
| `requirements-stt.txt` | 可选安装 Faster-Whisper 与 NumPy。 | 不属于基础依赖，不包含模型 |
| `config/settings.py` | 定义 STT 模型、设备、语言的闭集默认值和 `.env` Bootstrap。 | 非法别名回退，不能触发下载 |
| `config/desktop_settings.py` | 持久化 STT Desired/Active 值并计算 Restart Diff。 | 正常已初始化会话需重启；Brain 尚未建立时的修复会立即采用并重建空闲 STT Runtime |
| `voice/transcription.py` | 定义不依赖具体引擎的请求、Final Result、语言和稳定错误。 | 只允许有界最终文本 |
| `voice/faster_whisper.py` | 从明确本地目录加载、选择设备/Compute、执行转写并生成净化 Status/Error。 | 不下载模型，不返回路径或 Native Error |
| `voice/transcription_jobs.py` | 固定 Worker/Queue、Deadline、Cancel、唯一终态、Native Draining 和迟到结果丢弃。 | 逻辑取消后仍保留物理容量直到 Native Call 返回 |
| `desktop_backend.py` | 从 Active Settings 组装 Adapter/Runner，执行 Admission、Chat/Settings 互斥和 Protocol 终态。 | Python 是 Native Draining 的最终 Busy Gate |

可选模型名称是 `tiny`、`base`、`small`、`medium`、`large-v3`、`turbo`；每个名称只映射到 `models/weights/faster-whisper/<model>`。设备是 `auto`、`cuda`、`cpu`，默认语言是 `auto`、`zh`、`en`。缺模型、缺依赖、Runtime Probe、CUDA 或初始化失败只形成闭集 Reason，不允许把模型路径、异常消息或 Native 对象穿过协议。

### Electron 与 Renderer Final Transcript Handoff

| 文件 | 当前职责 | 关键边界 |
| --- | --- | --- |
| `desktop/electron/protocol.ts` | 严格解析 STT Settings、Readiness、一次性 PCM 请求和 PCM-free Final Result。 | 拒绝 Extra/Path/Message/Native 字段和不一致状态 |
| `desktop/electron/backend-process.ts` | 关联 Request/Session/Chat，处理 Progress、Cancel Race 和终态，并在 STT 活跃时阻止冲突写入。 | Pending Metadata 不保留 PCM |
| `desktop/electron/main.ts` | 校验 Renderer STT 参数与 Cancel Request ID，映射固定 IPC。 | 不接受任意 Channel 或任意设备/模型值 |
| `desktop/electron/preload.cts` | 暴露 `beginVoiceTranscription` / `stopVoiceTranscription` 与安全 Event Subscription。 | 不暴露原始 `ipcRenderer` |
| `desktop/electron/contracts.ts` | 声明 Renderer 可见的开始确认、Final/Error Event 和净化 Status。 | Final Event 不包含 PCM 或 Native Diagnostic |
| `desktop/src/App.tsx` | 管理 Capture/STT Operation Token、Request 关联、取消/关闭/导航竞态和 Final Transcript 到草稿的原子 Handoff。 | 迟到结果不能污染其他 Chat |
| `desktop/src/voice/CallPreview.tsx` | 显示并允许编辑 Final Transcript；按钮根据现有草稿选择 Use 或 Append。 | 不自动发送，不显示实时 Partial |
| `desktop/src/voice/transcription-readiness.ts` | 把闭集 Status/Reason 转成一致的恢复步骤。 | UI 不渲染底层路径或错误原文 |
| `desktop/src/settings/SettingsView.tsx` | 编辑 STT 模型/设备/语言并显示 Active 值、Readiness 和 Restart 提示。 | 保存值与当前生效值明确分离 |

完整连接关系：

```text
explicit Start microphone
  → getUserMedia
  → audio-capture.ts
  → voice-activity-detector.ts
  → beginVoiceTranscription (Preload / Electron)
  → voice.transcription.start
  → desktop_backend.py admission / mutual exclusion
  → TranscriptionJobRunner
  → FasterWhisperTranscriber
  → correlated, bounded PCM-free final result
  → editable CallPreview transcript
  → explicit Use / Append
  → current Chat's durable Composer draft
  → user sends separately
```

每份 PCM 只跨协议一次，不进入 Chat、Memory 或长期文件。用户取消、关闭 Voice 或切换上下文后，迟到结果不能回填草稿。Cancel 或 Timeout 只结束用户可见任务；Python 无法安全终止正在 Native Library 内运行的线程，因此 Runner 会继续占用物理容量直到调用返回并丢弃迟到结果。在排空期间，新 STT、Chat 与冲突配置写入会收到 Busy。

当前桌面协议只传递最终文字；实时 Partial Transcript、TTS 音频传输/播放、自动回复与连续 Voice Conversation 明确留给后续工作。Fake Runtime 自动化覆盖设备选择、降级和竞态，另有一次真实 CPU STT Runtime/模型 Smoke 验证；这里不声称 STT CUDA 已通过真实 GPU 验证。

### Python-only GPT-SoVITS 单次合成

| 文件 | 当前职责 | 关键边界 |
| --- | --- | --- |
| `config/settings.py` | 默认关闭本地评估，并限制 HTTP 请求/探测超时与 Seed。 | 环境值不提供任意 URL 或路径 |
| `workspace/settings/voice-profiles.json` | 本机 Schema v2 Catalog：声明 Base URL、权重、参考片段、准确文本/语言、速度、格式、权利状态，以及每项资产的实际长度与 SHA-256。 | 被 Git 忽略；Windows 路径也使用 `/` 分隔的相对路径；旧版字符串路径不会自动降级接受 |
| `voice/profiles.py` | 将逻辑 Profile/情绪解析为固定根下的不可变资产声明与外部 Adapter 配置。 | 拒绝逃逸路径、Windows 别名、矛盾身份、远端 URL、未知字段与未获 Opt-in 的本地评估素材；解析 Catalog 不等同于验证文件 |
| `voice/synthesis_service.py` | 惰性加载 Catalog 并创建一次 Adapter 调用。 | 构造服务不访问磁盘或网络；文字 Chat 不依赖 TTS 在线 |
| `voice/gpt_sovits.py` | 探测 `/openapi.json` 并 POST `/tts`，按剩余 Body Deadline 收取有界结果。 | 仅 Loopback IP；不信任代理，不自动重试/重定向或切换全局权重；拒绝无 Content-Length、压缩或 Transfer-Encoding；非流式配置仅 WAV/AAC |
| `voice/synthesis.py` | 验证请求与最大 32 MiB 编码结果的完整 Transport Framing。 | PCM WAV、Ogg Opus、受支持 ADTS AAC 子集通过结构验证；不声称已做 Codec Decode |
| `scripts/smoke_gpt_sovits.py` | 每个情绪对固定句子合成两次并输出安全摘要。 | 不写音频、不回显 Prompt/路径/异常原文 |

完整连接关系：

```text
config/settings.py
  + workspace/settings/voice-profiles.json
  + models/weights/gpt-sovits/
  → JsonVoiceProfileCatalog
  → LocalSpeechSynthesisService
  → GptSovitsSynthesizer
  → GET loopback /openapi.json
  → POST loopback /tts
  → transport-framed SynthesisResult
  → scripts/smoke_gpt_sovits.py
  → safe metadata only
```

真实本机 Smoke 已对同一中文文本的 `neutral`、`happy`、`sad` 各运行两次，六次都得到有效 WAV；重复要求是“每次都有效”，并不承诺编码字节完全相同。Runtime 停止后返回稳定的 `service_unreachable`，文字 Chat 测试仍通过。正常探测只报告 `available / service_binding_unverified`，因为 `/openapi.json` 能证明兼容服务在线，却不能证明外部进程实际加载了 Catalog 声明的权重。Catalog 中的 GPT/SoVITS 路径因此是独立 Runtime 的预期部署配置，不是身份 Attestation；只有未来由 Elysia 控制、固定权重且能证明同一服务实例的 Wrapper 才可以把状态提升为 `ready`。这条链目前不经过 `desktop_backend.py`、Electron、Preload 或 React。

## 26. 哪些文件不应被当成源码垃圾

### 工具生成，但应被 Git 跟踪

- `desktop/package-lock.json`：确保 npm 安装和 CI 可复现。

### 可以重建、通常被忽略

```text
desktop/node_modules/
desktop/dist/
desktop/dist-electron/
desktop/out/
desktop/test-results/
desktop/playwright-report/
*.tsbuildinfo
__pycache__/
.pytest_cache/
.mypy_cache/
```

### 本地数据或环境，不能因为“清垃圾”随意删除

```text
workspace/             Chat、Project、Memory、Settings、Attachments、Migration
.env                   本机配置或未来 Secret
logs/                  排错日志
.venv/                 Python 环境；可重建，但删除后程序不能启动
models/blobs/          本地模型数据
models/manifests/      本地模型清单
models/weights/        本地 Faster-Whisper、GPT-SoVITS 等权重/参考音频
models/cache/          解压的可选外部 Runtime 与下载/推理缓存；可重建但体积大
docs/02-ROADMAP.md     被 Git 忽略的本地项目路线图
```

## 27. 修改功能时从哪里开始

### 修改 Chat 或 Project 业务规则

```text
Domain
→ Service / Brain
→ Repository（需要改变持久化时）
→ Python tests
→ Desktop Backend / Protocol（需要暴露给桌面时）
→ React UI
→ Contract / UI tests
```

### 修改 Memory

```text
memory/scope.py or long_term_memory.py
→ memory/retrieval.py
→ core/prompts.py / core/brain.py
→ scoped memory tests
```

任何读取 Project/Chat Memory 的新功能，都不能绕过 Scope Context。

### 修改 Protocol

至少同步检查：

```text
desktop_protocol/schema/v1.schema.json
desktop_protocol/fixtures/v1.samples.json
desktop_protocol/contracts.py
desktop/electron/protocol.ts
desktop/electron/backend-process.ts
desktop_backend.py
tests/test_desktop_protocol.py
tests/test_desktop_backend.py
desktop/tests/protocol.contract.test.mjs
```

Renderer API 也变化时，再同步：

```text
desktop/electron/contracts.ts
desktop/electron/preload.cts
desktop/electron/main.ts
desktop/src/App.tsx
desktop/tests/ui/mock-preload.cjs
desktop/tests/ui/app-shell.spec.ts
```

### 修改纯 UI

```text
具体 Component
→ App callback/state（必要时）
→ CSS / Design Tokens
→ app-shell.spec.ts
```

纯 UI 修改不应绕过 Desktop API 去直接读取文件或 Workspace。

### 修改本地文件或硬件能力

```text
Renderer intent
→ preload 固定 capability
→ Electron Main trusted boundary
→ Python service（需要业务持久化时）
```

不要向 Renderer 暴露任意 IPC、原生路径、Node API 或 Python 进程句柄。

### 修改本地 Voice / STT / TTS

```text
config/settings.py + config/desktop_settings.py
→ voice/transcription.py
→ voice/faster_whisper.py + voice/transcription_jobs.py
→ desktop_backend.py
→ 双端 Protocol / Fixtures
→ Electron contracts / BackendProcess / Preload / Main
→ App.tsx / CallPreview.tsx / transcription-readiness.ts
→ Python Contract/Runner Tests + Desktop Contract/UI Tests
```

如果只是增加模型权重或可选 Runtime，不要把它提交进源码：依赖版本进入 `requirements-stt.txt`，完整模型只放在被忽略的 `models/weights/faster-whisper/<model>`。任何新的 Runtime Error 必须先映射为稳定枚举，不能把路径、底层异常或 Native 对象直接送给 Renderer。

Python TTS 改动从另一条尚未接桌面的链开始：

```text
config/settings.py + config/voice_profiles.example.json
→ voice/synthesis.py + voice/profiles.py
→ voice/gpt_sovits.py + voice/synthesis_service.py
→ scripts/smoke_gpt_sovits.py
→ 对应 Python tests
```

只有开始实现桌面播放时，才继续修改 Protocol、Electron、Preload 与 React。外部 GPT-SoVITS Runtime 放在被忽略的 `models/cache/`，权重/参考音频放在 `models/weights/gpt-sovits/`；不得把本机 Catalog、准确 Prompt、资产或 Runtime 混进源码提交。

## 28. 推荐的新成员阅读顺序

准备修改源码的人应在项目首页之后先阅读 `AGENTS.md`，再按下面的架构顺序进入实现。

1. `README.md`
2. `config/settings.py`
3. `start.py`
4. `core/chat_model.py`
5. `chats/domain.py`
6. `chats/repository.py`
7. `core/active_conversation.py`
8. `projects/domain.py`
9. `projects/service.py`
10. `memory/scope.py`
11. `memory/retrieval.py`
12. `core/prompts.py`
13. `core/brain.py`
14. `chats/migration.py`
15. `recovery/service.py`
16. `desktop_protocol/README.md`
17. `desktop/electron/contracts.ts`
18. `desktop/electron/preload.cts`
19. `desktop/electron/main.ts`
20. `desktop/electron/backend-process.ts`
21. `desktop_backend.py`
22. `desktop/src/App.tsx`
23. 具体 Feature Component
24. 对应测试

读完后应形成以下心智模型：

- Domain 管“什么数据是合法的”。
- Repository 管“保存在哪里、如何安全保存”。
- Service 管“多个对象怎样一起变化”。
- Brain 管“一次 AI 用例怎样完成”。
- Python 是持久化事实来源。
- Electron 管可信本机能力。
- React 只管理显示和短暂状态。
- 单句 STT 只返回 Final Transcript；进入 Composer 和发送消息是两个独立、显式动作。
- Python 单次 TTS 已能验证本地合成，但 Desktop Protocol、句子队列和播放器尚未连接。
- Streaming Overlay 不等于已保存消息。
- `ChatSession.project_id` 是 Project–Chat 关系的唯一真相。
- 所有 Memory 使用前都必须经过 Scope 过滤。
