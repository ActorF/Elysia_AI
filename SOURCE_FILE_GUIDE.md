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
| `README.md` | 中文项目首页；描述功能状态、架构、CMD 启动、测试、隐私和当前限制。 | 新用户入口；链接 Desktop/Protocol/ADR 文档 |
| `README.en.md` | 与中文 README 对应的英文首页。 | 对外英文说明；应与 `README.md` 同步维护 |
| `MODEL_LICENSE.md` | 说明角色语料、GPT-SoVITS 权重、参考音频等来源与权利边界；不是源码许可证。 | `data/characters/`、本地 `models/weights/`、发行边界 |
| `SOURCE_FILE_GUIDE.md` | 当前这份逐文件源码导览；记录文件职责、调用边界、测试映射和新人阅读顺序。 | 全仓库源码、配置、文档与测试 |
| `requirements.txt` | 固定 Python Runtime、LangChain Ollama、pytest、mypy、jsonschema 等依赖版本。 | `.venv`、CI、`start.py`、`desktop_backend.py` |
| `scripts/check_python_documentation.py` | 用标准库 AST 检查所有受维护 Python 文件的 module、public class、public function/method docstring 覆盖。 | `AGENTS.md`、GitHub Actions、Python 开发验证 |
| `start.py` | Python Composition Root 和 Console 入口；创建 Settings、Model、Memory、Repositories、Migrator、Services、Brain 和日志。 | 几乎所有 Python 生产包；`ui/console.py`、`desktop_backend.py` |
| `desktop_backend.py` | Electron 启动的 Python NDJSON 进程；完成会话令牌握手、初始化、方法路由、Streaming、Cancel、错误映射和安全关闭。 | `desktop_protocol/`、`start.py`、Chat/Project/Attachment/Voice 服务 |
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
| `config/settings.py` | 从安全默认值和根目录 `.env` 创建不可变 `AppSettings`；校验模型、Ollama Origin、Memory 限额、Import 大小和路径。 | `start.py`、Model Adapter、Memory、Recovery |
| `config/desktop_settings.py` | Stage 6 Desktop Settings Store；对可编辑字段做 allowlist、Revision CAS、线程/进程锁、原子替换和损坏隔离。 | `desktop_backend.py`、Settings UI、`workspace/settings/global.json` |

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
| `voice/faster_whisper.py` | 实现离线优先的 Faster-Whisper Adapter、轻量能力状态、设备/Compute Policy、一次性 CUDA 初始化降级、PCM float32 转换和返回错误脱敏。 | 复用 Transcription Contract；可选 Native Runtime 与本地模型；尚未接入 Desktop Backend |
| `voice/storage.py` | 对 `audio-device.json` 执行 Revision CAS、线程/进程锁、原子替换和损坏隔离。 | Voice Service、`workspace/settings/audio-device.json` |
| `voice/service.py` | 提供硬件无关的设备偏好读取和更新；Python 不直接打开麦克风。 | Desktop Backend、Voice Repository |
| `voice/transcription.py` | 定义与具体识别引擎解耦的 Transcriber Protocol、请求、最终结果、语言范围和稳定错误。 | 复用 `VoiceCapture`；供后续 Faster-Whisper Adapter 与后台任务实现 |

`voice/capture.py` 是单句 PCM 验证边界，详见本文“Voice Capture 实现”部分。`voice/transcription.py` 保持为不导入第三方引擎的领域契约；具体 Adapter 位于 `voice/faster_whisper.py`，但尚未连接 Desktop Protocol、后台任务或 Voice UI。Adapter 只接受完整本地模型目录，不会根据模型别名隐式下载权重。

## 13. Console UI：`ui/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `ui/__init__.py` | Console UI 的稳定公开导出。 | `start.py` |
| `ui/console.py` | 旧 Console Client；恢复/创建默认 Chat、流式输出、`/memory`、`/summarize`、候选记忆确认和退出。 | Brain、Memory；没有完整 Desktop Chat/Project 管理能力 |

## 14. Desktop 工程根目录：`desktop/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/.gitignore` | 排除 `node_modules`、`dist`、日志和常见本地编辑器文件；`dist-electron`、`out` 与 Playwright 输出由根 `.gitignore` 负责。 | npm/Vite/Electron/Playwright 生成物 |
| `desktop/README.md` | Desktop 开发指南；双 CMD 启动、架构边界、手工测试、验证和打包说明。 | 根 README、Protocol README、npm scripts |
| `desktop/package.json` | npm 项目入口、React/Electron 依赖、开发/文档审计/测试/构建/打包脚本和 electron-builder 配置。 | 所有 Desktop 工具链 |
| `desktop/package-lock.json` | 固定完整 npm 依赖图和下载完整性，使 `npm ci` 与 CI 可复现；不要手工编辑。 | npm、GitHub Actions、安全审计 |
| `desktop/index.html` | Vite Renderer HTML 入口；定义 CSP、favicon、viewport、theme-color 和 `#root`。 | `desktop/src/main.tsx`、Vite、Electron Window |
| `desktop/vite.config.ts` | 配置 React Plugin、固定开发端口和生产相对资源路径。 | `npm run dev`、`npm run build:renderer` |
| `desktop/eslint.config.js` | ESLint Flat Config；启用 JS、TypeScript、React Hooks 和 React Refresh 规则。 | `npm run lint` |
| `desktop/playwright.config.ts` | 配置 Electron UI 测试目录、单 Worker、Timeout 和失败产物路径。 | `npm run test:ui`、`desktop/tests/ui/` |
| `desktop/scripts/check-documentation.mjs` | 从仓库根目录检查 JS/TS/JSX 文件说明及公开 callable JSDoc、PowerShell 脚本/模块 Help，以及 CSS/HTML/HTM 文件说明；排除依赖、构建产物与缓存。 | `npm run docs:check`、`AGENTS.md`、GitHub Actions |
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
| `desktop/electron/contracts.ts` | 定义 Renderer 可见的最小 Desktop API、Backend Snapshot/Event、Chat/Project/Settings/Attachment/Voice 类型；不是 Python 原始 Wire Schema。 | Preload、Main、React、Mock Preload |
| `desktop/electron/preload.cts` | 用 `contextBridge` 暴露固定 `window.elysiaDesktop`；每个方法映射固定 IPC，不暴露 `ipcRenderer`、Node、`fs` 或进程句柄。 | React、Electron Main |
| `desktop/electron/main.ts` | Electron 主进程；创建带品牌图标的窗口/托盘，验证 Sender、参数、路径和权限，注册 IPC，控制导航与应用关闭。 | Preload、BackendProcess、原生 Dialog/Clipboard/Audio、`public/elysia-icon.png` |
| `desktop/electron/backend-process.ts` | Python 子进程 Owner 和 Protocol State Machine；管理 Session Token、Handshake、Initialize、Request Map、Timeout、Stream、Cancel、Snapshot 与 Shutdown。 | Main、`desktop_backend.py`、`protocol.ts` |
| `desktop/electron/protocol.ts` | TypeScript 端 Protocol v1 类型、Builder、Parser 和严格 Runtime Validation；不把静态类型当安全边界。 | BackendProcess、共享 Schema/Fixtures、Contract Tests |
| `desktop/electron/protocol-text.ts` | 定义跨 Python/TypeScript 一致的 Unicode Code Point 长度、Blank Set 和 Trim 规则。 | `protocol.ts`、Python Contracts |
| `desktop/electron/renderer-source.ts` | 只允许准确的 Vite Root 或打包 `dist/index.html` 作为可信 Renderer 来源。 | Main、Permission Policy、测试 |
| `desktop/electron/audio-permission.ts` | 只为可信主窗口和主 Frame 放行 audio-only microphone 或 speaker selection。 | Main 的 Chromium Permission Handler |
| `desktop/electron/external-url.ts` | 只接受无 Credentials 的 HTTP/HTTPS URL，拒绝危险 Scheme、空白、NUL 和无 Host URL。 | Main、Markdown Link、系统浏览器 |

## 16. React Renderer 总入口：`desktop/src/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/main.tsx` | 初始化 React Root、StrictMode、ThemeProvider 和 ErrorBoundary；初始 Paint 后通知 Electron。 | `index.html`、`App.tsx`、Preload API |
| `desktop/src/AppErrorBoundary.tsx` | 捕获 React Render Error，显示可恢复错误并把焦点移动到错误区域。 | `main.tsx` |
| `desktop/src/App.tsx` | Renderer 总协调器；管理 Backend Snapshot、Canonical Chat/Project、Streaming Overlay、Draft、Retry、Attachments、Settings、Voice 和页面状态。 | 所有 React Feature、`window.elysiaDesktop` |
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
| `desktop/src/settings/SettingsView.tsx` | 编辑模型、Ollama Origin、Short-Term Budget、Retrieval Limit、Import 大小；显示配置所有权、重启提示、主题和隐私边界。 | App、Desktop Settings Backend、ThemeProvider |
| `desktop/src/settings/VoiceSettingsSection.tsx` | 设备偏好 UI；枚举麦克风/扬声器、保存 opaque ID、显示权限、刷新设备、运行短暂输入电平和输出音调测试。 | `audio-devices.ts`、Voice Desktop API |
| `desktop/src/voice/audio-devices.ts` | Stage 7 设备 Controller；构造时不请求权限，管理 enumerate、8 秒麦克风 Level Test、800 ms Speaker Tone、Race 和 Cleanup。 | VoiceSettingsSection、Browser MediaDevices/AudioContext |
| `desktop/src/voice/CallPreview.tsx` | 全窗口 Voice 页面；当前正在扩展一次性录音状态，但不应宣称 STT/TTS 或连续通话已完成。 | App、当前 Capture Controller、Voice Backend API |

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
| `desktop_protocol/README.md` | 人类可读 Protocol v1 文档；说明 Handshake、Capabilities、Streaming、Cancel、Settings、Attachment、Voice 和安全不变量。 | Python/TypeScript 实现和测试 |
| `desktop_protocol/schema/v1.schema.json` | Draft 2020-12 JSON Schema；描述所有 Client/Server Frame 和可机器表达的限制。 | Shared Fixtures、Python/Node Contract Tests |
| `desktop_protocol/fixtures/v1.samples.json` | Python 与 TypeScript 同时读取的 Valid/Invalid Conformance Samples。 | `contracts.py`、`protocol.ts`、两端测试 |
| `desktop_protocol/contracts.py` | Python 端 TypedDict、严格 Parser、Runtime Validator 和 Response/Error/Stream/Event Builder。 | `desktop_backend.py`、Schema/Fixtures、Python Tests |
| `desktop_protocol/__init__.py` | 汇出协议常量、类型、Parser 和 Builder。 | Desktop Backend、测试 |

协议的 Python 与 TypeScript Parser 都是手写的，Schema 不是代码生成器。因此修改协议时必须同步维护两端和共享 Fixtures。

## 23. Desktop 测试：`desktop/tests/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/tests/protocol.contract.test.mjs` | 在 Node 中测试编译后的 Protocol Helpers 和 BackendProcess；覆盖双端 Fixture、Stream Sequence、Cancel Race、Snapshot、URL 与 Permission Policy。 | `dist-electron`、Schema/Fixtures；使用 Fake Child，不启动真实 Python |
| `desktop/tests/ui/electron-main.cjs` | Playwright 专用 Electron Main；加载生产 Renderer Build，保持 Sandbox/Context Isolation，但不启动生产 Backend。 | UI Test、Mock Preload、`dist/index.html` |
| `desktop/tests/ui/mock-preload.cjs` | UI 测试专用 `elysiaDesktop` Fake；维护 Canonical Mock Chat/Project/Settings/Attachment 状态，并可模拟延迟、失败、Reload 和 Race。 | App Shell UI Tests；不会进入生产包 |
| `desktop/tests/ui/app-shell.spec.ts` | Playwright 启动真实 Electron Renderer，覆盖 Chat、Project、Settings、Voice、Attachments、Markdown、Stop/Retry、Reload、IME、Focus 和响应式行为。 | Production React Build + Mock Backend |

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
| `tests/test_desktop_backend.py` | Python Bridge 的 Handshake、Routing、Streaming、Cancel、Chat/Project/Settings/Attachment/Voice。 |
| `tests/test_desktop_protocol.py` | Python Protocol Parser/Builder 与共享 Fixture Contract。 |
| `tests/test_desktop_settings.py` | Desktop Settings Validation、Revision CAS、锁和 Quarantine。 |
| `tests/test_faster_whisper.py` | 不安装 Native Runtime 或模型也能验证离线 Adapter、设备降级、PCM、惰性结果、错误脱敏和边界。 |
| `tests/test_file_manager.py` | 基础文本文件操作。 |
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
| `tests/test_scoped_memory_integration.py` | 跨 Chat/Project Memory 隔离和同 Key 覆盖。 |
| `tests/test_settings.py` | `.env`、默认值和基础 AppSettings。 |
| `tests/test_short_term_memory.py` | Token Budget 和完整 Turn 淘汰。 |
| `tests/test_stage5_acceptance.py` | Stage 5 端到端验收：多 Project/Chat、Memory 隔离、重启和完整 Export/Import。 |
| `tests/test_start.py` | Composition Root、Migration 和配置限制。 |
| `tests/test_voice_capture.py` | Python 单句 PCM Capture Contract、Canonical Base64、Markers、边界和收据。 |
| `tests/test_voice_settings.py` | Audio Device Preferences、CAS、锁和损坏恢复。 |
| `tests/test_voice_transcription.py` | 引擎无关的转写请求、最终结果、语言、置信度、不可变性和错误层级。 |

## 25. Voice Capture 实现

这个有界功能由 5 个核心文件和 24 个跨层集成文件组成。它们共同实现显式单句录音、16 kHz PCM、VAD、可信协议校验与安全 Receipt，不应被误解成彼此独立的功能。

### 5 个核心文件

| 文件 | 当前职责 | 完成边界 |
| --- | --- | --- |
| `desktop/src/voice/audio-capture.ts` | 获取显式授权的单次麦克风输入；Downmix、流式重采样到 16 kHz、PCM16 Frame、生命周期和清理。 | 不执行 STT，不持久化录音 |
| `desktop/src/voice/voice-activity-detector.ts` | 对 20 ms PCM Frame 做有界 VAD、Pre-roll、No-speech/Maximum Timeout 和尾静音结束判断。 | 只是保守 VAD，不识别文字 |
| `desktop/tests/voice-capture.test.mjs` | 测试 Capture/VAD 时序、静音、固定音调、噪音、Speech-like Signal 和 Cleanup。 | Renderer Voice 单元回归 |
| `voice/capture.py` | 在可信 Python 边界验证 canonical Base64、16 kHz mono s16le、Frame Alignment、时长、Markers 和 SHA-256。 | 不打开麦克风，不调用 Brain |
| `tests/test_voice_capture.py` | 测试 Python PCM Capture Contract。 | Python Voice 单元回归 |

### 24 个跨层集成文件

```text
desktop/README.md
desktop/electron/backend-process.ts
desktop/electron/contracts.ts
desktop/electron/main.ts
desktop/electron/preload.cts
desktop/electron/protocol.ts
desktop/package.json
desktop/src/App.css
desktop/src/App.tsx
desktop/src/chat/Composer.tsx
desktop/src/voice/CallPreview.tsx
desktop/tests/protocol.contract.test.mjs
desktop/tests/ui/app-shell.spec.ts
desktop/tests/ui/mock-preload.cjs
desktop_backend.py
desktop_protocol/README.md
desktop_protocol/__init__.py
desktop_protocol/contracts.py
desktop_protocol/fixtures/v1.samples.json
desktop_protocol/schema/v1.schema.json
tests/test_desktop_backend.py
tests/test_desktop_protocol.py
voice/__init__.py
voice/exceptions.py
```

这些修改负责把 Capture 从 React 连接到 Preload、Electron Main、BackendProcess、双端 Protocol、Python Backend 和测试；它们不是 24 个彼此独立的新功能。

连接关系：

```text
getUserMedia
  → audio-capture.ts
  → voice-activity-detector.ts
  → Preload / Electron / Protocol
  → desktop_backend.py
  → voice/capture.py
  → 安全 Receipt
```

这条链目前不会：

- 创建 Chat Turn；
- 调用 Brain；
- 执行 STT；
- 执行 TTS；
- 开始连续 Voice Conversation；
- 将 PCM 保存为长期文件。

下一层的 `voice/transcription.py` 已定义如何把验证后的 `VoiceCapture` 表达成转写请求，以及如何返回有界、非空的最终文本；`voice/faster_whisper.py` 已能在纯 Fake Runtime 下验证本地模型、设备选择、初始化降级和最终文本。两者仍未接入现有 Capture 链；只有后台任务、Protocol 与 UI 完成后，录音才会真正产生可编辑 Transcript。

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
models/weights/        本地 GPT-SoVITS 等权重/参考音频
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
- Streaming Overlay 不等于已保存消息。
- `ChatSession.project_id` 是 Project–Chat 关系的唯一真相。
- 所有 Memory 使用前都必须经过 Scope 过滤。
