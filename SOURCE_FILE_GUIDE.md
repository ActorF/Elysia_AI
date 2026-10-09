# Elysia AI 源码文件导览

这份文档用于帮助第一次接触 Elysia AI 的开发者理解：每个受版本控制的文件负责什么、它与哪些层连接，以及修改某项功能时应该从哪里开始。

> 当前架构边界：Python 是 Chat、Project、Memory、Attachment、Document Loading/Processing/Embedding/Retrieval、Project Source 授权、Knowledge Lifecycle 与结构化 Citation 持久化状态的事实来源；Electron Main 是本地进程、文件路径、硬件权限、外部桌宠程序目录与可选系统通知的可信边界；Preload 只暴露固定能力；React Renderer 只负责显示和临时交互状态。Project Sources 管理与显式启用的 grounded Chat 已接入同一生产桌面链路。Character State API 是 Renderer-local 的封闭语义投影：它只消费已验证的 Chat、Voice 与 Knowledge 生命周期，不成为新的 Python Canonical State，也不新增 IPC 或任意动画文件控制能力。用户限定的十值 Voice Emotion 同时控制 TTS 参考与应用内静态表情；Chat/Voice 的 `CharacterArtwork` 永远使用审核后的静态图片，不提供 Animated/Still 切换，也不从播放音频派生嘴型。默认关闭的 Desktop Pet 不再由 Elysia 渲染模型：Main 严格扫描用户选择目录内经过固定 Hash 审核的伴侣程序，启动前再次复核全部信任条件，只管理 Elysia 自己启动的精确 PID；Renderer 只看到无路径的程序摘要。Presence 与 Notifications 同样不进入 Python Protocol：用户只在 Settings 选择默认关闭的受审布尔值/频率，Electron Main 私有保存节奏锚点、判断窗口与活动状态，并使用不含会话内容的固定系统通知文案。

## 1. 先看完整连接图

```text
React Component
    │ callback / local UI state
    ▼
desktop/src/App.tsx
    ├── character-state.ts + character-emotion.ts + character-presentation.ts → CharacterArtwork.tsx / CharacterPanel.tsx / CallPreview.tsx
    ├── voice-ui-state.ts → CallPreview.tsx（主生命周期、麦克风状态、计时）
    └── SettingsView.tsx（Desired / Active Voice 行为设置）
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
    ├── initialize
        ├── start.create_brain()
        │   ├── Brain
        │   ├── ActiveConversationService
        │   ├── ChatRepository
        │   ├── ProjectChatService / ProjectRepository
        │   ├── Memory / MemoryRetriever
        │   ├── Legacy Migration
        │   └── Ollama model adapter
        ├── AttachmentService → AttachmentRepository → JsonAttachmentStore
        │   └── owner/reference reconciliation + verified file access
        └── desktop_knowledge.create_desktop_knowledge_runtime()
            ├── KnowledgeLifecycleService + KnowledgeExportService
            ├── ProjectSourceAnswerService + shared operation lease
            ├── Document processing / embedding / retrieval
            └── digest-pinned loopback Ollama grounded generator
    └── optional speech copy
        └── desktop_speech.py
            └── sentence queue → managed worker → PCM WAV
                ├── voice.speech.* metadata over authenticated NDJSON
                └── matching fd3 frame → Electron delivery → Preload Web Audio

desktop_knowledge.DesktopKnowledgeRuntime（由 desktop_backend.py 延迟构造）
    ├── DocumentLoaderService
    │   ├── AttachmentService.open_verified_file(scope, opaque file_id)
    │   └── verified immutable bytes → text / PDF / DOCX loaders
    └── path-free LoadedDocument
        → ConservativeDocumentCleaner
        → StructureAwareDocumentChunker
        → validate exact piece table, lineage, mappings, and projections
        → versioned chunks + Page/Block/Cell/Offset mappings
        → DocumentEmbeddingService → strict loopback Ollama adapter
        → versioned 1024-d unit vectors + exact chunk lineage
        → SQLiteVectorStore（exact Project/Chat scope + atomic generations）
        → DocumentRetriever（explicit generation allowlist）
          → single-transaction bounded cosine Top-K
          → exact metadata filters + dedup evidence + optional reranker
        → GroundedAnswerService（完整命中前缀 + 不可信 JSON Data）
          → structured statements + trusted filename/page/location citations

project_sources.ProjectSourceAnswerService（生产授权与回答边界）
    ├── canonical Chat → active Project → exact Project AttachmentScope
    ├── atomic FileCatalogSnapshot + explicit ProjectSourceSnapshot
    ├── complete current Generation allowlist + bounded Instructions
    └── GroundedAnswerService → post-answer authority revalidation

knowledge_lifecycle（生产 Project-only 生命周期与导出边界）
    ├── KnowledgeLifecycleService
    │   ├── durable JSON operation journal + revision CAS + recovery
    │   ├── add / reindex / rebuild → vector commit → catalog publish last
    │   ├── replace / delete → whole-catalog tombstone → cleanup → republish
    │   └── whole-Project revoke + source/operation views + preview/cache cleanup protocol
    └── KnowledgeExportService
        └── verified original export + private crash-cleanup intent（不进入 saga journal）

desktop_protocol + Electron + React
    ├── knowledge.list/add/replace/reindex/delete/rebuild/revoke/recover/export
    ├── request-correlated progress / cancel / lifecycle settled / export settled / safe error
    ├── global Knowledge lease + lifecycle/export snapshot ownership across reload
    ├── ProjectSourcesPanel（状态、恢复、归档只读）
    └── explicit Use Project Sources → grounded Chat proof + Citation UI

Presence and Notifications（Electron Main-local，不进入 Python Protocol）
    SettingsView.tsx
        → App.tsx（加载、revisioned 更新、失败后恢复 Canonical Main state）
        → preload.cts（固定 get / update / state-changed / voice-active IPC）
        → main.ts
            ├── presence-notification-contracts.ts（闭集公开合同）
            ├── presence-notification-preferences.ts（userData 私有 JSON 与 cadence anchor）
            ├── presence-notification-policy.ts（注意力、忙碌与频率纯策略）
            ├── presence-native-notification.ts（单一原生通知槽与迟到事件隔离）
            └── Electron Notification（固定静音文案；点击只显示主窗口）
    backend-process.ts 的已验证 chat-complete
        → main.ts（仅 Reply-ready 候选；chunk / cancel / error 均不触发）

Desktop Pet（Electron Main-local，不进入 Python Protocol）
    SettingsView.tsx
        → App.tsx / preload.cts（选择目录、重扫、选择程序、模式更新）
        → main.ts
            ├── desktop-pet-program-library.ts（有界扫描、固定 Hash、启动前二次复核）
            ├── desktop-pet-preferences.ts（Schema v3、默认关闭、私有目录与 opaque 选择）
            └── desktop-pet-program-manager.ts（串行切换与单一 owned PID）
                → 经复核的外部伴侣程序（原目录、无 Shell、无附加参数）
                → 原程序负责鼠标/键盘/眼球跟踪、表情、位置和动态渲染
                    └── 原程序 `config.json` 留在原处且不由 Elysia 改写

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
| Electron Main | 窗口、子进程、真实路径、原生权限、系统通知与 Main-local 偏好 | Chat/Project 持久化规则、由 Renderer 提供通知正文或原生选项 |
| Preload | 固定且最小的 Renderer 能力桥 | 暴露任意 IPC、Node 或 `fs` |
| React Renderer | 展示、表单、焦点和短暂 UI 状态 | 直接读写 Workspace/userData JSON、直接创建系统通知 |

## 3. 根目录、CI、文档与资料

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `.github/workflows/tests.yml` | GitHub Actions 入口；先检查双端源码文档与分发边界，再在 Ubuntu 运行 Python pytest/mypy 和 Desktop lint/typecheck/protocol/UI/单入口构建，在 Windows 运行原生集成、构建真实 Unpacked Package、扫描 Package Tree/ASAR 清单，验证审核静态资产各有唯一条目，并确认外部桌宠程序、模型和所有退役渲染产物均未进入发行包。 | `scripts/check_python_documentation.py`、`scripts/check_distribution_assets.py`、`desktop/package.json`、Python/Desktop 测试 |
| `.gitignore` | 排除 `.venv`、Cache、日志、构建产物、私人 `workspace`、`.env`、模型权重，以及来源为 `@书呆儿` 的本机付费桌宠程序合集。 | Git 工作树与本地运行数据边界；外部桌宠程序和模型不得进入 Git |
| `AGENTS.md` | 全仓库源码注释规范；要求文件说明、公开 API 文档、复杂算法/设计/边界原因和具体 TODO/FIXME，并禁止逐行复述普通语句。 | 所有后续源码修改、双端文档覆盖检查、Code Review |
| `mypy.ini` | 固定 Python 静态类型检查路径规则；只排除被忽略的 `models/cache/` 外部 Runtime，不能误排其他名为 cache 的源码。 | 本地 mypy、GitHub Actions、第三方 Runtime 边界 |
| `pytest.ini` | 把自动发现根固定为 `tests/`，防止被忽略的第三方 Runtime 自带测试污染项目验收。 | pytest、本地 `models/cache/` |
| `README.md` | 中文项目首页；描述功能状态、架构、CMD 启动、测试、隐私和当前限制。 | 新用户入口；链接 Desktop/Protocol/ADR 文档 |
| `README.en.md` | 与中文 README 对应的英文首页。 | 对外英文说明；应与 `README.md` 同步维护 |
| `MODEL_LICENSE.md` | 说明固定 Qwen3 Embedding Ollama Artifact、角色语料、GPT-SoVITS 权重、参考音频、SoulX-Singer 私有运行时、静态角色图，以及外部本机桌宠程序/模型合集的来源、Hash、当前运行/分发决定与权利边界；不是源码许可证。 | `documents/ollama_embedding.py`、`data/characters/`、本地桌宠程序、Ollama Storage、`models/weights/`、本机 WSL SoulX 运行时与发行边界 |
| `SOURCE_FILE_GUIDE.md` | 当前这份逐文件源码导览；记录文件职责、调用边界、测试映射和新人阅读顺序。 | 全仓库源码、配置、文档与测试 |
| `requirements.txt` | 固定基础 Python Runtime、LangChain Ollama、pypdf、OpenCC、pytest、mypy、jsonschema 等依赖版本；OpenCC 是 Chat 与 STT 共用的强制简体规范化边界，不强制安装本地 STT Native Runtime。 | `.venv`、CI、`start.py`、`desktop_backend.py`、`documents/pdf.py`、`localization/chinese.py` |
| `requirements-stt.txt` | 固定可选的 Faster-Whisper 与 NumPy 版本；只在需要本地单句转写时叠加安装，不包含或下载模型权重。 | `voice/faster_whisper.py`、本地 `.venv`、`models/weights/faster-whisper/<model>` |
| `scripts/__init__.py` | 把维护脚本标记为可导入 Package，使 Smoke CLI 能同时按模块与文件路径测试。 | `scripts/smoke_gpt_sovits.py`、`scripts/smoke_song_cover.py`、测试 |
| `scripts/benchmark_voice_pipeline.py` | Windows 三组件资源基准；以固定内容并发测量 Ollama Streaming 与受管 GPT-SoVITS，随后在模型驻留时执行 CPU Faster-Whisper；只输出脱敏数值，限制总墙钟、响应大小和 GPU 采样，并在失败时独立清理所有自有 Owner。 | `config.SETTINGS`、`desktop_speech.py`、Managed GPT-SoVITS、Faster-Whisper、Ollama、`nvidia-smi` |
| `scripts/check_distribution_assets.py` | 分发门禁；审计 Git Index 的模型/音频/Runtime/User Data/Archive 与同步歌词产物，禁止本机付费桌宠程序、SoulX/语音模型、Prompt 音频、`.lrc`/歌词清单、任何路径下被改名的 Cubism 模型/编辑器后缀、伴侣程序二进制和退役桌宠 Renderer/Runtime 构建残留进入 Git/Package；以准确视觉素材路径白名单阻止单独改名/搬移的付费纹理，并以路径、字节长度和 SHA-256 固定审核静态素材与应用图标；冻结单 Renderer 的 Electron Builder 配置，并可扫描真实 Unpacked Tree、ASAR 清单和完整抽取树；对条目缺失/重复、字节替换、Case/Unicode Alias、Link/Junction 和配置逃逸 Fail Closed。 | `MODEL_LICENSE.md`、GitHub Actions、`desktop/package.json`、每次发行产物 |
| `scripts/check_python_documentation.py` | 用标准库 AST 检查所有受维护 Python 文件的 module、public class、public function/method docstring 覆盖。 | `AGENTS.md`、GitHub Actions、Python 开发验证 |
| `scripts/gpt_sovits_protocol.py` | 定义主 Python 3.14 与隔离 GPT-SoVITS Python 3.9 共用的固定宽度二进制帧，以及两端共用且有序的 Runtime Manifest 与封闭 Import Path 清单；严格限制消息类型、Canonical JSON Metadata、Request ID 和 32 MiB 原始 Payload，错误与 repr 不暴露内容。 | 受管 Worker/Parent Pipe；不导入 `voice` 或上游 `tools`，避免运行时版本、Manifest 顺序和包名冲突 |
| `scripts/gpt_sovits_worker.py` | 在隔离 Python 3.9 进程中按父进程传入的稳定 Volume-GUID 路径重算 Voice 资产和部分 Runtime 一致性锚点，固定加载一组 GPT-SoVITS v2 权重与 Reference，拒绝 Config Fallback、热切换、全零错误音频和多 Yield，并通过私有二进制 Pipe 返回完整 PCM WAV。`READY` 只证明父子进程本次观察到同一组已声明内容，不是第三方 Runtime 的完整供应链证明；上层持续持有每个已检查文件的防写/防替换 Guard。 | `scripts/gpt_sovits_protocol.py`、`voice/managed_gpt_sovits.py`、被忽略的本地 GPT-SoVITS Runtime；不经过外部 HTTP API |
| `scripts/smoke_gpt_sovits.py` | 用固定中文句子对每个所选情绪重复两次本地合成，只输出 Readiness、格式、大小、时长和 SHA-256；不接受任意文本，也不保存音频。 | `voice/synthesis_service.py`、`.env`、被忽略的 Voice Profile Catalog |
| `scripts/smoke_song_cover.py` | 可重复且显式 Opt-in 的 Lyrics-SVS 贯通验收 CLI；先以只读方式核验 vocal/accompaniment/LRC/plain lyrics 的 Hash、真实媒体格式与时长，只有 `--run-private-runtime` 才建立全新 UUID Job 并调用生产 Worker，复核闭集阶段、WAV/MP3、WSL 清理后默认删除输出；stdout 不公开路径、歌词或底层诊断。 | `song_svs_worker.py`、本机私有 SoulX Runtime、`docs/15-SONG-COVER.md`、Fake smoke tests |
| `scripts/song_cover_worker.py` | Legacy 歌声转换短进程；接受完整歌曲或用户预先分离的人声/伴奏，按固定资源上限执行 Demucs 分离，验证 RVC/HuBERT/RMVPE/私有模型与 Index 身份，启动封闭 RVC 子进程，把 40 kHz mono 输出精确对齐为 44.1 kHz 目标样本，再做不混回原唱辅音的响度受限混音；只在成功后发布最终 WAV/MP3。 | `song-cover-manager.ts`、`song_rvc_runtime.py`、被忽略的 `models/cache/rvc-v2-40k/` 与 `models/weights/rvc/elysia-v2-40k/`、Song Cover Python tests；不负责联网歌词或 SoulX 路径 |
| `scripts/song_rvc_runtime.py` | 封闭的单次 RVC v2 Adapter；只接受显式 Source/Checkpoint/FAISS Index/HuBERT/RMVPE/Input/Output 路径与 `-2..+2` 半音，固定 speaker `0`、RMVPE、Index Rate `0.00`、Protect `0.33`、RMS Mix Rate `0.25` 和 40 kHz v2 模型家族。零 Index Rate 不混入近邻，但私有 Index 仍须按固定身份传入并验证。它拒绝 Reparse/链接边界、既存输出、非 CUDA 回退、非法 Model Metadata，以及超范围、全零或持续满幅 PCM；在 Vendor Import 前关闭会按长歌分段 Shape 累积显存的 CUDA Graph，禁用 Socket/Proxy/Hugging Face 在线访问，并只在完整验证后原子发布新 WAV。 | `song_cover_worker.py`、固定 RVC Revision `81eed5e8f68b6bed1789f682fe78cdd324495afc`、本机私有 RVC 资产；不向 Renderer 暴露上游 RVC 命令面 |
| `scripts/song_lyrics_alignment.py` | 把严格同步的普通话 LRC、目标人声音频和 SoulX 官方 G2P 组合成逐段歌词/音素/音符元数据；跨段歌词按时间重叠和剩余 onset 容量单调拆分，保证每个 Han token 只归属一次，无法无歧义分配时 Fail Closed。 | `song_svs_runtime.py`、SoulX G2P/ROSVOT、同步歌词资产、Alignment tests |
| `scripts/song_svs_runtime.py` | 私有 WSL SoulX-Singer 单次运行协调器；固定校验 Runtime、模型、Prompt 与 Job 布局，依次准备音高/音符/歌词元数据并执行本地离线推理，使用可核验的阶段 lease、总墙钟和清理规则避免取消或超时后遗留 GPU 子进程。 | `song_svs_wsl_bridge.py`、`song_lyrics_alignment.py`、本机被忽略的 SoulX Runtime/模型/Prompt |
| `scripts/song_svs_wsl_bridge.py` | Windows 到 WSL 的最小桥接边界；把固定允许输入复制到权限为 0700 的 Linux 私有 Job，启动准确的 Runtime Session，按 lease 取消/超时，并且只在成功校验后把生成 WAV 搬回 Windows Job，最后清理 Linux 临时数据。 | `song_svs_worker.py`、`song_svs_runtime.py`、WSL、Bridge tests |
| `scripts/song_svs_worker.py` | Lyrics-driven 歌声生成的 Windows 侧 Worker；验证完整歌曲或 stems、同步歌词 manifest 和 Job 所有权，调用私有 WSL SoulX bridge，再以原伴奏进行受限混音并原子发布 WAV/MP3；取消、失败与超时都收敛到同一私有清理路径。 | `song-cover-manager.ts`、`song_svs_wsl_bridge.py`、Song Cover runtime assets、Worker tests |
| `start.py` | Python Composition Root 和 Console 入口；创建 Settings、Model、Memory、Repositories、Migrator、Services、Brain 和日志。所有可写生产路径来自 `AppSettings.data_layout`，程序/模型资源仍从只读 `base_dir` 读取。 | 几乎所有 Python 生产包；`config/data_layout.py`、`ui/console.py`、`desktop_backend.py` |
| `desktop_backend.py` | Electron 启动的 Python NDJSON 进程；完成会话令牌握手、初始化、方法路由、Streaming、Cancel、错误映射和安全关闭。除 Chat/Project/Settings/Attachment/STT/Speech 外，它还组合 Project Sources runtime、在全局 Knowledge admission 下用后台 worker 执行显式 lifecycle mutation/recovery 或 verified export、异步清理身份匹配的 export temp、发布安全 operation/receipt DTO，并把显式 grounded Chat 接入同一 generation commit gate。Electron 通过进程环境注入绝对数据根，Backend 不再把安装根当作 Workspace。 | `desktop_protocol/`、`config/data_layout.py`、`desktop_knowledge.py`、`desktop_speech.py`、`start.py`、Chat/Project/Attachment/Voice 服务 |
| `desktop_knowledge.py` | 生产知识 Composition Root；共享 Project/Chat/Source repositories 与 operation lease，组合 Loader→Cleaner→Chunker→Embedding→SQLite→Retriever→Grounded Answer、Lifecycle 与 verified export，配置 app-private export cleanup-intent directory，并计算绑定全部输出规则的 index profile。构造不访问 Ollama、外部 export destination 或索引任务。 | `desktop_backend.py`、`documents/`、`project_sources/`、`knowledge_lifecycle/`、`workspace/knowledge/` |
| `desktop_speech.py` | 桌面语音 Composition Root；后台按 Active `voiceProfileId` 与闭集 `voiceEmotion` 获取 Managed GPT-SoVITS Lease，并把 Active `speechRatePercent` 作为绝对 `0.5–2.0` Speed Factor 使用。单个有界原文 Spool 在启动前后都接收快速 Brain Chunk，固定 Feeder 再按 Sentence FIFO 容量背压逐批送入，避免阻塞 Chat 或因第九个待合成分句取消整轮。普通停止/替换只逻辑丢弃过期输出，不会用 Native Abort 污染可复用 Worker；真实 Runtime/Queue/Pipe 失败仍 Fail Closed。Clip Metadata 先经 NDJSON 发送，匹配 WAV 再写入私有 fd3。 | `desktop_backend.py`、`voice/managed_gpt_sovits.py`、`voice/speech_queue.py`、`desktop_protocol/audio_channel.py` |
| `docs/decisions/0001-desktop-shell.md` | Electron 与 Tauri 选型 ADR；记录测量方法、能力差距、风险、最终选择和重访门槛。 | `desktop/benchmarks/measure-shell.ps1`、Desktop 技术决策 |
| `docs/03-VOICE-PERFORMANCE-SAFETY-RIGHTS.md` | 记录最终三组件实机测量、CPU STT 资源策略、长会话自动化清理证据、Voice Rights 决定、分发门禁和重测条件。 | Module 9 验收、`MODEL_LICENSE.md`、Benchmark/Soak/Package Audit |
| `docs/04-FILE-METADATA-STORAGE.md` | 记录版本化 File Metadata、Scope-local Content-addressed Storage、Ownership/Derived 关系、Manifest v2、迁移、验证读取、删除回滚、Renderer 隐私边界和当前非目标。 | `attachments/`、Desktop Backend、后续 Document Loaders |
| `docs/05-DOCUMENT-LOADERS.md` | 记录可信 Document Loader 的格式矩阵、路径隐私、Scope 授权、结构模型、资源预算、PDF/DOCX Parser 边界、稳定错误、测试和当前非目标。 | `documents/`、`attachments/`、下游 Cleaning/Chunking |
| `docs/06-DOCUMENT-CLEANING-CHUNKING.md` | 记录纯、版本化且保守的 Cleaning、严格重复 PDF 页眉证据、LoadedDocument Code-point Provenance、结构/Code/Table JSONL Chunking、资源预算、确定性失效和当前非目标。 | `documents/cleaning.py`、`documents/chunking.py`、后续 Embedding/Vector Store |
| `docs/07-LOCAL-EMBEDDINGS-VECTOR-STORE.md` | 记录固定 Ollama Embedding Artifact/Space、Batch/Template Policy、严格 Loopback Adapter、Chunk Lineage/Float32 Checksum、Scope-safe SQLite 事务索引、损坏/失效拒绝和当前非目标。 | `documents/embedding.py`、`documents/ollama_embedding.py`、`documents/vector_store.py`、`documents/indexing.py`、`MODEL_LICENSE.md` |
| `docs/08-RETRIEVER-RERANKING.md` | 记录 Identity-bearing Query、显式 Expected Generation Allowlist、单事务暴力 Cosine Top-K、闭集 Metadata Filter、阈值、Exact Deduplication、多来源 Evidence、可选不可信 Reranker、预算/错误和当前非目标。 | `documents/retrieval.py`、`documents/embedding.py`、`documents/vector_store.py`、Retrieval 测试 |
| `docs/09-GROUNDED-ANSWERS-CITATIONS.md` | 记录一次性 Retrieve→Generate 所有权、完整命中前缀、Prompt Data 隔离、三类结构化 Statement、可信 Citation/Location、资源预算、错误闭集和语义能力边界。 | `documents/grounding.py`、`documents/retrieval.py`、Grounding 测试 |
| `docs/10-PROJECT-SOURCES.md` | 记录 Chat-derived Project Scope 授权、原子 ownership snapshot、显式 Generation catalog、CAS、同 Project 共享、Chat Attachment 隔离/提升、安全 Instructions、操作租约和当前非目标。 | `project_sources/`、`attachments/`、`documents/grounding.py`、Project Source 测试 |
| `docs/11-KNOWLEDGE-LIFECYCLE.md` | 记录 Project-only 持久 saga journal、完整 state/phase、add/reindex/rebuild 发布顺序、whole-Project revoke、copy-on-write replace、tombstone-first delete、取消/恢复/幂等，以及不入 journal 的 verified export/cleanup intent 与 Preview/Cache cleanup 预留。 | `knowledge_lifecycle/`、`attachments/`、`documents/indexing.py`、`project_sources/` |
| `docs/12-KNOWLEDGE-UI-TESTING.md` | 记录生产 Composition Root、loopback Generator、grounded Chat 持久化、Desktop methods/events、全局 Knowledge lease、跨 reload 的 export snapshot/terminal、Project Sources/Citation UI、真实 PDF/DOCX fixtures 与 Stage 8 自动化验收边界。 | `desktop_knowledge.py`、`desktop_backend.py`、Protocol/Electron/React、真实文档回归 |
| `docs/13-PRODUCTION-DATA-LAYOUT.md` | 定义升级安全的程序资源/用户数据分离、版本化数据树、容量分类、Legacy 首次复制、两阶段目录移动、Backend Readiness 回滚和严格临时清理边界；明确移动不是备份。 | `config/data_layout.py`、Electron Data Storage、Settings、Stage 14 后续 Backup/Packaging 模块 |
| `docs/14-LIVE2D-RUNTIME.md` | 保存 Stage 13 的决策历史，并记录当前边界：应用内状态板永远静态，动态桌宠由用户本机已有的外部伴侣程序负责，Elysia 只做严格程序发现、启动前复核、owned-process 管理，且绝不复制或打包外部程序/模型。 | Static Main/Voice Artwork、Desktop Pet 外部程序边界与分发门禁 |
| `docs/15-SONG-COVER.md` | 记录实验性 Song Cover 的双路径架构、LRCLIB 查询披露、同步歌词约束、WSL SoulX 私有资产/进程/临时文件边界、Legacy fallback、导出行为、受控实机证据、可重复 Smoke 命令和当前并非安装包能力的限制。 | Song Cover Electron/React、五个生产 Python Worker/Bridge/Runtime 文件、`smoke_song_cover.py`、`MODEL_LICENSE.md`、分发门禁 |
| `data/characters/elysia_character_reference_zh.md` | 爱莉希雅背景、语录和转写参考资料；当前 Runtime 不会自动将它注入每次 Prompt。 | 人工角色研究；受 `MODEL_LICENSE.md` 的来源/授权提醒约束 |
| `data/characters/elysia_character_analysis_zh.md` | 对本地 2,965 行资料、官方发布内容与往世乐土剧情索引所作的原创十二类角色分析；记录阶段、关系、证据强度、运行时边界与资料缺口，不保存网络完整台词。 | `core/elysia_system_prompt_zh.md` 的人工研究依据；角色评审与后续语料清洗 |
| `data/characters/elysia-2dArt/README.md`、`PROMPTS.md` 与 18 张审阅 PNG | 记录最终静态 2D 审阅包、逐格修复、生成参考与 Hash；02/03/04 提供应用内静态状态、表情与回退参考。旧自制 Live2D 脸部母版和 21 层制作输入已删除。 | CharacterArtwork、静态素材分发门禁、`MODEL_LICENSE.md`；未选中的审阅总览不进入安装包 |

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
| `config/data_layout.py` | Python 的唯一 ProductionDataLayout；从 Main 注入的绝对根派生 Workspace/Settings/Chat/Project/Memory/Attachment/Knowledge、临时 Audio、Cache 与 Logs，避免服务把数据写进可替换安装目录。 | `config/settings.py`、`start.py`、Desktop/Knowledge/Speech Composition Roots |
| `config/settings.py` | 从安全默认值和根目录 `.env` 创建不可变 `AppSettings`；严格解析可选 `ELYSIA_DATA_ROOT`，把只读资源 `base_dir` 与可移动 `data_layout` 分离。除 Chat/Ollama/Memory 与 STT 闭集外，还定义自动朗读、语速、播放音量、逻辑 Voice Profile、十值闭集语音情绪、字幕、人工 Transcript Review 和安全自动续听的默认值，并限制 GPT-SoVITS 本地评估开关、请求/探测超时和确定性 Seed。 | `start.py`、Model Adapter、Memory、Recovery、Faster-Whisper 与 TTS Composition |
| `config/desktop_settings.py` | Desktop Settings Store；把旧 Schema v1/v2/v3 文档内存迁移到 v4，并对十六个公开字段做 allowlist、Revision CAS、Desired/Active Restart Diff、线程/进程锁、原子替换和损坏隔离。`speechRatePercent`、`voiceProfileId` 与 `voiceEmotion` 属于重启生效设置；自动朗读、播放音量、字幕、人工 Review 和自动续听可实时生效。 | `desktop_backend.py`、Settings UI、`workspace/settings/global.json` |
| `config/voice_profiles.example.json` | 只含原创占位内容的严格 Voice Profile Schema v2 示例；每个资产声明都要求相对 `path`、实际 `bytes` 和小写 `sha256`，示例数字与 Hash 必须替换。 | `workspace/settings/voice-profiles.json`、`voice/profiles.py` |

## 6. Python 核心协调与文本规范化：`core/`、`localization/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `core/__init__.py` | 汇出 Core 的稳定公共类型、服务和异常。 | 让入口与测试避免依赖包内实现路径 |
| `localization/__init__.py` | 汇出 Chat 与 Voice 共用的文本规范化边界。 | `core/brain.py`、`voice/transcription_jobs.py`、测试 |
| `localization/chinese.py` | 使用固定 OpenCC `t2s` 把用户可见中文统一为简体；回复路径保留代码、URL、Markdown 目标和显式路径，并保守移除段首动作舞台提示。流式规范化按安全句界保留跨 Chunk 词组上下文。 | Brain 的普通/Retry/Grounded 输出、STT Final Result、UI/Persistence/TTS 共同文本 |
| `core/chat_model.py` | 定义 `generate_reply` / `stream_reply` 最小 Model Protocol 和轻量消息类型。 | `core/brain.py`、两个 Ollama Adapter、Fake Model 测试 |
| `core/ollama_chat_model.py` | 直接使用 Ollama HTTP API；检查模型是否安装、执行非流式调用并翻译连接/HTTP/JSON 错误。 | `config/settings.py`、本地 Ollama；生产 Adapter 复用可用性检查 |
| `core/langchain_ollama_chat_model.py` | 当前生产 Model Adapter；把项目消息转成 LangChain Message，调用 invoke/stream，并拒绝空或非文本响应。 | `core/chat_model.py`、`core/brain.py`、Ollama |
| `core/elysia_system_prompt_zh.md` | 受信、版本化的中文运行时人格合同；把原作连续性、十二类人格、日常一至两句、禁止动作旁白、工具真实性、关系与安全边界写成可独立评审的 Markdown。 | `core/prompts.py` 有界加载；不直接包含原始语录库或动态用户数据 |
| `core/prompts.py` | 有界读取并缓存外部 Elysia Runtime Prompt，校验受信资源后，将 Profile、Scoped Memory、Chat/Project Context 作为明确 JSON 数据加入。 | `core/elysia_system_prompt_zh.md`、`core/brain.py`、`memory/retrieval.py`、Project Instructions |
| `core/model_memory_extractor.py` | 调用模型提取待用户确认的 Memory Candidate；严格解析 JSON、验证和去重，不自动保存。 | `core/brain.py`、`memory/extraction.py`、Long-Term Memory |
| `core/model_conversation_summarizer.py` | 调用模型生成 facts、decisions、action items、unresolved questions；支持增量摘要。 | `core/brain.py`、`memory/summarization.py`、`ChatSummary` |
| `core/active_conversation.py` | 管理 per-Chat Busy Guard、不可变快照、完整 Turn/Summary Commit 和并发修改检测；后来扩展 Chat actions、Retry 与 Attachment Commit。 | `core/brain.py`、Chat/Project Repository |
| `core/brain.py` | 应用用例总协调器；组织 Chat/Project API、上下文重建、Scoped Retrieval、Prompt、模型调用、Streaming、Cancel、Retry、Summary 和 Memory。所有生成回复先统一简体并去除段首动作提示；普通流式回复保证已发 Chunk 拼接值与最终持久化文本完全一致；grounded new-turn/retry 则在同一 generation commit gate 内调用 Project Source answer，并把规范化文本与结构化 proof 原子保存。 | Core、Chat、Project、Memory、Model、Project Sources、Localization、Desktop Backend/Console |
| `core/exceptions.py` | 定义配置、模型、Busy、Cancel、Retry、Model Mismatch、生成期间状态变化等稳定错误。 | `core/brain.py`、`desktop_backend.py`、Console、测试 |

## 7. Chat 领域与持久化：`chats/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `chats/__init__.py` | Chat Package 的稳定公共 API。 | `start.py`、Brain、Project/Recovery、测试 |
| `chats/domain.py` | 定义 ChatSession、ChatSessionMeta、ChatMessage、ChatSummary、AttachmentMetadata、稳定 ID 和全部不变量；Assistant Message 可选携带闭合的 Grounded Answer/Statement/Citation/Text-or-Table Location proof，其他 role 不可携带。 | Serialization、Repositories、Brain、Protocol 转换 |
| `chats/exceptions.py` | 定义 Not Found、Already Exists、Storage、Corruption 和 Migration 错误。 | Repository、Migrator、Recovery、入口错误映射 |
| `chats/serialization.py` | 严格转换 Chat Domain 与 JSON；处理 UTC 时间、Schema、Message、Summary、Attachment、Grounded proof 和 Index Metadata，并继续接受不含可选 proof 字段的旧 Chat 文件。 | `chats/repository.py`、Recovery、测试 |
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
| `attachments/domain.py` | 定义不包含真实路径的 AttachmentScope、AttachmentItem/State，以及版本化 OriginalFileMetadata、FileOwnership、DerivedFileRelation 和原子 FileCatalogSnapshot；强制 File ID/Hash、UTC 时间、来源闭集、Chat/Project Role 对应关系及完整 ownership fingerprint。 | Repository、Service、Store、Protocol、Project Source authorization、Renderer-safe State |
| `attachments/exceptions.py` | 定义稳定、不会泄漏本地路径的 Attachment 验证、冲突、未找到、存储和导入取消错误。 | Store、Desktop Backend |
| `attachments/repository.py` | 定义 AttachmentRepository Protocol；统一 Draft/Claim/Commit 生命周期、Owner 对账、版本化 File Metadata、Derived 关系、Scope-bound Verified Read、完整或单 link 的单次 Manifest authorization snapshot、内部显式 Chat→Project copy primitive、fingerprint-guarded Project Source removal 和单/多 Owner 删除事务。 | `attachments/service.py`、`attachments/store.py`、Project Sources、Knowledge Lifecycle、测试替身 |
| `attachments/service.py` | Application Service；把真实路径限制在受信 Import 边界，以 Scope + opaque File ID 提供验证读取，暴露完整或单 link 的原子 file snapshot，只向 canonical authority coordinator 提供私有 committed Chat Attachment copy primitive，并为生命周期层暴露只接受 Project Scope 的准确 `project_source` removal。 | Desktop Backend、AttachmentRepository、Project Sources、Knowledge Lifecycle、可信 Loader |
| `attachments/store.py` | Manifest v2 与 Scope-local Content-addressed Blob Store；按内容 Hash 在单一 Scope 内去重，保存 Original/Ownership/Derived 关系，并实现 v1 原子迁移、Descriptor-pinned Copy/Read、取消回滚、进程锁、启动恢复、Owner/Reference 对账、多 Scope 删除 Tombstone、经 Hash/Size 复核且 manifest-last commit 的显式跨 Scope copy，以及锁内 snapshot-fingerprint/role 复核且 Manifest-first commit 的单 Project Source removal。 | Electron 文件选择、AttachmentService、Desktop Backend、Chat Message Commit、Project Sources、Knowledge Lifecycle、Document Loaders |

当前 Attachment 已完成安全原始文件存储、版本化 Metadata/Ownership、Derived 关系登记、原子授权快照和可信 Backend 读取边界。Attachment Package 本身仍不解析内容；`documents.DocumentLoaderService` 通过 `open_verified_file()` 取得 Scope-bound Verified Snapshot，关闭读取 Context 后才把无路径 Bytes 交给格式 Loader 提取原始结构。生产 Document Pipeline 再保守清洗、生成版本化 Chunk，把精确 Lineage 绑定到固定本地 Embedding 空间和 Scope-safe SQLite 索引，在准确 Scope + Generation Allowlist 上有界检索，并从完整命中前缀构造有限 Prompt 与可信 Citation；`project_sources` 从 canonical Chat→Project、完整 ownership snapshot 和显式 catalog 派生该 Allowlist，`knowledge_lifecycle` 把 Project-only add/replace/reindex/rebuild/revoke/delete 组成可恢复 saga。整条链已由 `desktop_knowledge.py`、Protocol 和 React 接入显式选择的 Project Chat。

## 12. Document Loading, Processing, Embedding, Retrieval and Grounding：`documents/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `documents/__init__.py` | Document Package 的稳定公共 API；导出 Loaded/Cleaned/Chunked/Embedded/Retrieval/Grounded Answer 领域值、错误、Producer/Embedding/Reranker/Generator Contract，以及各同步 Service 与生产 Ollama Adapter。 | Loader/Cleaner/Chunker/Embedding/Indexing/Retrieval/Grounding、Desktop Knowledge、测试 |
| `documents/domain.py` | 定义不含路径的 `DocumentSource`、Title、Ragged Table、Ordered Block、`LoadedDocument` 和输入/展开/文字/结构资源预算；复核页码、Ordinal、标题来源和累计输出。 | 所有 Loader、Service、Cleaner |
| `documents/exceptions.py` | 定义稳定的 Validation、Not Found、Unsupported Format/Feature、Empty、Encrypted、Corrupt、Read、Unexpected Loader/Processing 错误，以及公开 `DocumentLimitError` 基类下的 Size/Content Limit 子类。 | Service、Loader、Cleaner/Chunker、Desktop Knowledge 稳定错误映射 |
| `documents/protocol.py` | 定义按精确 `(suffix, media_type)` 路由的 Path-private Format Loader、Scope-bound Source Loader、Cleaner 与 Chunker Protocol，以及稳定 Producer ID/Version/Policy Contract。 | Loader Service、Processing Pipeline、各 Adapter 与测试替身 |
| `documents/service.py` | 以 `scope + ownership link_id` 解析 Link-specific Metadata 和 Canonical File Record，经 `open_verified_file()` 有界读取不可变快照，关闭文件 Context 后再选择 Loader，并复核 Source、Limits 与 Producer Version 未被 Adapter 篡改。 | `attachments/service.py`、所有 Loader、`DocumentProcessingService` |
| `documents/text.py` | 严格解码 UTF-8/BOM-declared UTF-16；以常量级行游标提取 TXT Paragraph、Markdown ATX/多行 Setext/Fence/Table，以本地状态机解析严格 CSV，并把常见源码保留为 Code Block。它不执行、渲染、联网或解析外部资源。 | `DocumentLoaderService`、Domain、文本测试 |
| `documents/pdf.py` | 使用固定版本 pypdf 的 Strict Reader 和请求局部资源配置；在 Operation Graph 物化前限制 Content Token，按实际调用累计 Page/Form Bytes、Operation 与 Invocation，并以累计 Visitor/Child Guard 在重复或嵌套 Form 物化超限文字前早停。缺失或 Null `/Contents` 作为真实空页；保留 Embedded Title、Page Count 与每个非空页的一基页码 Raw Text，不猜测 Table/Layout/OCR。 | `requirements.txt`、Service、二进制 Loader 测试 |
| `documents/docx.py` | 在构造 `ZipFile` 前核对 EOCD/Zip64 并逐条扫描真实 Central Directory；在所有已解析的选定 XML Part 间累计 XML/MC Token/Namespace 资源；Main、可选 Styles 与可选 Core 必须分别经过 Relationship 和精确 Content Type 授权，未授权的固定路径诱饵会被忽略。它按 Part 使用 Namespace-level Profile，以持久化增量状态执行 `AlternateContent`、Ignorable、ProcessContent 与 MustUnderstand；DrawingML、Office Math/OMML、VML/旧 Shape 作为 Opaque Subtree 跳过，但不算 MCE understood。它还拒绝路径别名、加密、外部 Main Relationship、DTD/Entity、未知 Encoding 与 Macro Main Part，并以单次增量 Table 遍历按正文顺序提取 Title/Heading/Paragraph/Table。 | Service、Domain、二进制 Loader 测试 |
| `documents/cleaning.py` | 定义 Loaded Provenance、Text/Table Source Span、Cleaned Blocks、审计 Omission 与 Processing Limits；纯 Cleaner 除逐页完全证明的短 PDF 首行外逐 Code Point 保留 Loader 输出，并用 Canonical SHA-256 记录 Document/Cleaning Identity。 | `LoadedDocument`、Chunker、Cleaning 测试、后续派生数据生命周期 |
| `documents/chunking.py` | 定义 Chunk、Chunk-local Mapping 与 `ChunkedDocument`；按 Title/Heading/Paragraph、Page、逻辑行、固定句末和 Code-point 上限生成零重叠 Prose/Code Chunk，并把 Ragged Table 投影为版本化 JSONL；完整 Lineage 决定 Derivation Fingerprint 与 Chunk ID。 | Cleaner、Chunking 测试、后续 Embedding/Vector Store |
| `documents/pipeline.py` | 默认组合 Source Loader、保守 Cleaner 与结构 Chunker，为每个 Adapter 重建隔离快照，并把其返回值视为不可信：逐项复核请求 Scope/Ownership、Producer/Policy/Limits、Piece-table 对每个 Loaded Block 的完整分区、Canonical Fingerprint、Chunk Lineage，以及 Text/Table Mapping 对来源和 JSONL Projection 的完整重建。 | `DocumentLoaderService`、Cleaner/Chunker Protocol、`desktop_knowledge.py`、Pipeline 测试；最终返回图也与 Adapter 持有对象隔离 |
| `documents/embedding.py` | 定义版本化 Model/Embedding-space Identity、固定 Batch/Length/Template Policy、`TextEmbedder` Protocol、`EmbeddedChunk`/`EmbeddedDocument`/`EmbeddedQuery` 和 `DocumentEmbeddingService`；Space Fingerprint 直接绑定实际 Batch Policy、Document Input Mode 与精确 Query Prefix，不只依赖人工 Version Bump。它保留完整 Chunk Lineage，按 `EmbeddingModelIdentity.dimension` 验证单位向量，并生成 Canonical Float32-le Checksum。Query 同时发布完整 Space/Policy/Canonical Vector，避免 Retriever 接受身份不明的裸向量。内建 Ollama Identity 固定为 1,024 维，通用 Service 不硬编码该维度。 | `ChunkedDocument`、Ollama Adapter、Vector Store/Retriever、Embedding 测试 |
| `documents/ollama_embedding.py` | 严格的 Loopback Ollama HTTP Adapter；在每个 Batch 前后通过精确 Tag + Full Manifest Digest 拒绝 Mutable-alias Race，禁用 Proxy/Redirect/Retry/Truncation，并以精确 JSON Content Type、Duplicate-key/Non-finite 拒绝、Raw `read1` Byte Cap 和剩余 Socket Deadline 限制响应。失败脱敏为稳定 Embedding Error。Model-layer Digest/Size/Q8_0 是该 Manifest 的文档化 Provenance，不是 Adapter 单独从 API 再证明的字段。 | 本地 Ollama `/api/tags` 与 `/api/embed`、`documents/embedding.py`；不下载或启动模型 |
| `documents/ollama_grounding.py` | 生产 Structured Grounded Answer Adapter；只接受规范化 loopback HTTP Origin，在生成前后固定完整模型 digest，禁用 Proxy/Redirect/Retry/Tools/Thinking/Streaming/Truncation，固定 JSON + temperature 0，并对 body/deadline/Content-Type/UTF-8/JSON 执行有界验证和错误脱敏。 | 本地 Ollama `/api/tags` 与 `/api/chat`、`GroundedAnswerService`、`desktop_knowledge.py`；不下载或启动模型 |
| `documents/vector_store.py` | 使用标准库 SQLite 持久化一个固定 Embedding Space；Canonical JSON + SHA-256 保存完整 Lineage/Mapping，Float32-le BLOB + SHA-256 保存向量，并以精确 Chat/Project Scope 实现原子 Replace/List/Get/Delete/Rebuild、Stale/Model 拒绝与 Schema/Corruption Fail-closed。`search_scope()` 还在单个读事务中验证显式 Allowlist 的全部 Generation/Record，按单位向量 Dot Product 暴力计算有界 Cosine Candidate Pool；Schema 继续精确复核 Table DDL、PK/UNIQUE/FK 并拒绝未知 Trigger/View/显式 Index。 | `EmbeddedDocument`、`EmbeddedQuery`、Indexing/Retrieval Service、Vector Store/Retrieval 测试 |
| `documents/indexing.py` | 同步组合 Processing → Embedding → SQLite Store，并保持请求 Scope/Ownership Link 与结果 Lineage 一致；把无 Store mutation 的 `prepare_document()`、原子单 Generation `commit_document()`、整 Scope `rebuild_scope()` 与幂等 `delete_document()` 作为 Knowledge Lifecycle 的安全提交边界。 | Document Processing/Embedding/Vector Store、Knowledge Lifecycle、Desktop Knowledge、Indexing 测试 |
| `documents/retrieval.py` | 定义 Expected Generation、Filter/Policy/Limits、Hit/Evidence/Result、Reranker Identity/Request/Batch 与稳定错误；`DocumentRetriever` 只搜索准确 Scope + Allowlist，执行阈值、确定性 Top-K、准确 `(kind, text)` 去重，并把可选 Reranker 当作必须返回完整闭合评分的非可信 Adapter。 | `DocumentEmbeddingService`、`SQLiteVectorStore`、`GroundedAnswerService`、Retrieval 测试；自身不生成答案或 Citation UI |
| `documents/grounding.py` | 定义 Grounded Answer Limits、同步非流式 Generator Identity/Request/Protocol、`source_fact`/`model_summary`/`inference` Statement、可信 Citation 与 Text/Table Location，以及 fingerprint-bound GroundedAnswerPreferences；`GroundedAnswerService` 固定拥有同一次 Retrieve→Generate，先验证 exact corpus，再让 structured preference 只重排已经相关的 Hit，把问题、片段、style guidance 和 opaque Citation ID 作为不可信 Canonical JSON Data，并对模型输出的严格 JSON、Fingerprint 和引用闭包 Fail Closed。 | `DocumentRetriever`、`project_sources`、Ollama Grounding Adapter、Desktop Knowledge、Grounding 测试 |

完整边界是：

```text
AttachmentScope + ownership link_id
  → DocumentProcessingService
      → DocumentLoaderService
      → AttachmentService.open_verified_file(scope, opaque file_id)
      → bounded immutable bytes; verified-file context closes
      → exact suffix + MIME adapter
      → versioned, path-free LoadedDocument
      → conservative, lossless-by-default Cleaner
      → versioned structure/code/table Chunker
      → exact reconstruction + lineage/mapping verification
  → ChunkedDocument + source mappings
      → DocumentEmbeddingService
      → strict loopback Ollama adapter + pinned embedding space
      → EmbeddedDocument + float32-le checksums
      → SQLiteVectorStore
      → exact Scope/Link filtering + atomic generation replacement
      → EmbeddedQuery + ExpectedDocumentGeneration allowlist
      → one-transaction bounded cosine candidate search
      → threshold + exact deduplication + optional fail-closed reranker
      → RetrievalResult + bounded source evidence
      → GroundedAnswerService
      → bounded whole-hit prompt context + structured generator request
      → labeled statements + trusted file/page/block/cell/offset citations
  → ProjectSourceAnswerService
      → canonical Chat→Project authority + operation lease
      → atomic ownership snapshot + explicit generation catalog
      → exact Project Scope/allowlist + bounded non-authorizing preferences
      → GroundedAnswerService + post-answer revalidation
```

Loader 输出仍是 Raw Structure；后续纯转换生成可重复 Chunk，Embedding/Store 在固定语义空间持久化 Scope-safe Vector Generation。Retriever 只接受显式授权的准确 Generation，在一致快照内执行有界搜索、Filter、阈值、去重和可选 Fail-closed Reranking。Grounding 层不接受调用方任意拼接的 `(query, result)`，而是在同一调用内检索、选择完整命中前缀、构造两消息 Prompt，再把模型只能选择的 Citation ID 解析回可信文件名、页码及位置；空 Hits 不调用 Generator。Project Source 层只从 canonical Chat→Project、原子 ownership snapshot 与显式 catalog 派生 Scope/Generation，完整操作持有租约并在发布前复核。Knowledge Lifecycle 再以 durable journal 协调 Vector 与 catalog 的安全发布/撤销/清理。`desktop_knowledge.py` 共享这些 repositories 与 lease，Brain 把回答文本和 proof 原子保存，Renderer 只显示安全 Citation。该结构能证明引用属于本次授权上下文，不能机械证明模型概括/推断的语义蕴含。完整桌面接线和验收见 `docs/12-KNOWLEDGE-UI-TESTING.md`。

## 12A. Project Source Authorization：`project_sources/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `project_sources/__init__.py` | Project Source Package 的稳定公共 API；集中导出 schema 常量、domain、错误、canonical catalog helpers、Repository/Lease/Instruction Protocol、保守 Operation Coordinator 与 Answer Service。 | Knowledge Lifecycle、Desktop Knowledge Composition Root、测试 |
| `project_sources/catalog.py` | 把 Attachment 返回值隔离为准确 `FileCatalogSnapshot`，按受信 Document route 选择可索引 `project_source` ownership，并从完整 Project catalog 派生 canonical `DocumentSource` tuple；不负责授权发布。 | Project Source Answer/Promotion、Knowledge Lifecycle |
| `project_sources/domain.py` | 定义版本化 ProjectSourceGeneration、结构化且不扩权的 ProjectSourceInstructions、CAS ProjectSourceSnapshot 与 Chat/Project-bound ProjectSourceAnswer；fingerprint 同时绑定 exact ownership catalog、完整 Generation、index profile、Instructions、Revision 和 UTC publish time。 | Repository、Answer Service、Grounding |
| `project_sources/exceptions.py` | 定义 Validation、Authorization、Conflict、Stale、Not Found、Storage 与 Data Corruption 的稳定脱敏错误闭集。 | Repository、Service、Desktop Backend Protocol mapping |
| `project_sources/repository.py` | 定义 ProjectSourceRepository，并以严格 exact-schema JSON、duplicate-key rejection、有界 descriptor read、安全目录/文件核验、跨进程锁、fsync、atomic replace、Revision CAS 与 deletion tombstone 持久化完整 Project catalog；`read_entry()` 在一次锁内返回一致的 `(revision, snapshot | None)`，不会从 Vector row 反向授权旧 Generation。 | `project_sources/service.py`、Knowledge Lifecycle、测试 |
| `project_sources/service.py` | 只接受 chat_id/query，从 canonical active Chat→Project 派生 exact Project Scope，验证原子 Attachment snapshot、完整 current-profile catalog 与 bounded preferences，在操作 lease 内调用 GroundedAnswerService 并最终复核所有 authority；另提供 canonical Chat-history + committed-only 的显式 Chat Attachment promotion，但不伪造索引 Generation。非文档路由不会污染文本 corpus。 | Chats、Projects、Attachments、Documents、`desktop_knowledge.py` |

这一层是授权组合，不是新的 Document Pipeline。它不会扫描 Chat Attachment、按 Hash 猜测权限、执行 Cross-scope Union，或让自由文本 Instructions 生成 link ID。合法空 Project 可以得到 empty corpus；存在可索引 Source 但 catalog 缺失/过期/部分时必须 Fail Closed。完整设计、写入顺序、并发合同和非目标见 `docs/10-PROJECT-SOURCES.md`。

## 12B. Knowledge Lifecycle：`knowledge_lifecycle/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `knowledge_lifecycle/__init__.py` | Lifecycle Package 的稳定公共 API；导出 durable operation domain/repository、错误、Project Source view、artifact-cleanup boundary、Service 与 verified-original Export Service。 | Desktop Knowledge Composition Root、测试 |
| `knowledge_lifecycle/domain.py` | 定义 opaque operation ID、`add/replace/reindex/rebuild/revoke/delete` kind、完整 state/phase 闭集和路径私有 `KnowledgeOperationSnapshot`；限制单调 Revision/Progress/Attempt、UTC 时间、fingerprint 与闭集错误码，不允许路径、正文、Vector 或 traceback。 | Repository、Service、Desktop Knowledge Protocol DTO |
| `knowledge_lifecycle/exceptions.py` | 定义 Validation、Not Found、Conflict、Storage、Data Corruption 与 Recovery 的稳定脱敏错误闭集；取消使用 durable operation state 表达。 | Repository、Service、Export、Desktop Backend Protocol mapping |
| `knowledge_lifecycle/repository.py` | 以最多 2,048 entries / 4 MiB 的严格 JSON journal 持久化 saga checkpoint；进程内锁 + sidecar OS lock 覆盖完整 transaction，descriptor-bounded read、identity 复核、fsync、atomic replace 与 exact next-revision CAS 防止并发丢失。 | `KnowledgeLifecycleService`、`workspace/knowledge/operations/` 生产存储目录 |
| `knowledge_lifecycle/service.py` | 只接受 active Project；在共享 mutation lease 下执行 add/reindex/rebuild 的 Vector-first/catalog-last 发布、whole-Project revoke、copy-on-write replace、tombstone-first delete、取消、最多八次 crash recovery、状态 view 和显式 Preview/Cache cleanup。Catalog 始终是唯一授权面；whole-Project revoke 后只有显式 rebuild 能恢复授权，删除仍可在保留 tombstone 的同时清理目标。 | Projects、Attachments、`documents/indexing.py`、Project Source catalog/repository |
| `knowledge_lifecycle/export.py` | 在 mutation lease 下验证 exact `project_source` ownership 与 original bytes；复制前原子持久化 temp/parent 稳定身份，逐块支持协作取消，发布前复核 ownership 与路径身份，再以 create-if-absent hard link 或 `os.replace` 原子发布。后台 crash recovery 只删除名称、父链和身份全部匹配的遗留 temp，损坏/路径替换 Fail Closed；不导出 Vector/Prompt/Journal/Internal ID。 | Projects、Attachment verified reads、Desktop Knowledge、Electron native Save dialog 与 `knowledge.source.export` |

其中 `KnowledgeLifecycleService` 是跨 Store 的 durable saga，不是假想的全局事务。新 Vector 在完整 catalog CAS publish 前没有权限；replace/delete 先 tombstone 整个 Project catalog，再幂等清理并发布剩余 corpus。`KnowledgeExportService` 共用全局 operation lease，但不进入 saga journal，只以身份固定的私有 cleanup intent 恢复未发布 temp。当前没有独立 Preview/Cache 实体，因此生产 `desktop_knowledge.py` 显式传入 `NoStoredKnowledgeArtifacts`；未来新增 artifact repository 时必须替换该 adapter。完整顺序、取消/恢复与桌面集成状态见 `docs/11-KNOWLEDGE-LIFECYCLE.md`。

## 13. Voice Python 层：`voice/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `voice/__init__.py` | Voice Package 的稳定公共 API。 | Desktop Backend、测试 |
| `voice/domain.py` | 定义输入/输出设备的 opaque ID 偏好和 Voice Settings Snapshot。 | Voice Service/Storage、Protocol |
| `voice/exceptions.py` | 定义 Voice Settings 和当前 Capture Validation 错误。 | Voice Service/Storage、Desktop Backend |
| `voice/faster_whisper.py` | 实现离线优先的 Faster-Whisper Adapter、严格本地模型完整性检查、净化后的就绪状态、设备/Compute Policy、一次性 CUDA 初始化降级、PCM float32 转换和返回错误脱敏；只接受明确的绝对本地目录，不按别名下载。 | `desktop_backend.py` 把闭集名称映射到 `models/weights/faster-whisper/<model>`；由后台 Runner 调用 |
| `voice/managed_gpt_sovits.py` | 按需启动并独占一个 Windows GPT-SoVITS Worker Lease：严格核验 Catalog 来源与 Config 快照，持有全部所选 Voice 资产和已声明 Runtime Anchor 的文件 Guard，以稳定 Volume-GUID 路径完成 Manifest/INIT/资产绑定，并只为 CPython Native Import 建立逐次核验的临时 DOS 映射。Bootstrap 封印后的目录 HANDLE 可检测既有 `FILE_ADD_FILE` 句柄并配合 DACL 阻止普通后续写入，但不能撤销封印前已打开的 `WRITE_DAC`；这里明确假设 Lease 启动时第三方 Runtime 及同一 Windows 用户下的进程可信，不声称完整依赖来源认证。Queue 层的 `binding_verified` 只表示该 Binding 由 Elysia 私有 Factory 围绕受管 Lease 签发，不表示完整第三方依赖 Provenance，因此缓存仍禁用。所有 Cleanup Lock/Condition 与复合 Owner 都在 `CreateProcessW` 前建立；最终 Cleanup Gate 检查、Launch 与无分配 Adoption 原子排序。Production Worker 从 Launch 前直到完整 Cleanup 持有全局单 Owner Token，因此并发启动、失败到清理的空档以及 Pending Cleanup 都不能放行第二个 Worker。清理先证明 Job 整树终止，以子句柄关闭唤醒阻塞 Pipe，再关闭父 Pipe、Guard、Bootstrap 文件和映射；任何模糊所有权都会整体隔离并全局 Fail-closed。 | `voice/_windows_file_guard.py`、`voice/_windows_managed_process.py`、`scripts/gpt_sovits_protocol.py`、`scripts/gpt_sovits_worker.py`、`desktop_speech.py` |
| `voice/_windows_file_guard.py` | 用 Win32 目录/文件 HANDLE 原子锁定并核验一组只读本地文件，拒绝 Reparse Point、别名、盘符映射变化和声明不符；从已持有的叶文件 HANDLE 生成稳定的 Volume-GUID 路径，供受封闭的受管运行时在盘符发生 ABA 重映射后仍只重开原卷文件。关闭失败会保留明确所有权并阻止新的 Acquisition，不会泄漏路径、Hash 或 HANDLE。 | Managed GPT-SoVITS Parent Wrapper；只在 Windows 执行，非 Windows 可安全导入并返回稳定不可用状态 |
| `voice/_windows_managed_process.py` | 用 Win32 `CreateProcessW` 的 Suspended 启动、精确 HANDLE Allowlist 与 Kill-on-close Job Object 建立受管语音子进程边界；只有完成 Job 绑定才恢复主线程，关闭/取消会终止包括 FFmpeg 在内的整棵进程树，并以 Job Accounting 的 `ActiveProcesses == 0` 作为释放 Job HANDLE 和报告成功的必要证明。并发 Teardown 共享单一有界结果，命令、环境、路径和原生 HANDLE 不进入 repr 或错误。 | 当前 `voice/managed_gpt_sovits.py` Parent Wrapper；只在 Windows 执行，非 Windows 可安全导入并返回稳定不可用状态 |
| `voice/storage.py` | 对 `audio-device.json` 执行 Revision CAS、线程/进程锁、原子替换和损坏隔离。 | Voice Service、`workspace/settings/audio-device.json` |
| `voice/service.py` | 提供硬件无关的设备偏好读取和更新；Python 不直接打开麦克风。 | Desktop Backend、Voice Repository |
| `voice/transcription.py` | 定义与具体识别引擎解耦的 Transcriber Protocol、请求、最终结果、语言范围和稳定错误。 | 复用 `VoiceCapture`；连接 Faster-Whisper Adapter、后台任务和 Desktop Backend |
| `voice/transcription_jobs.py` | 用固定 Daemon Worker、有界队列、Deadline、唯一终态和结果保留上限包装同步 Transcriber；在发布 Final Result 前对所有语言标签下的完整文本统一简体；取消/超时后保留物理容量直到 Native Call 返回，并丢弃迟到结果。 | `desktop_backend.py`、`voice/transcription.py`、`localization/chinese.py`；不把 PCM、路径或底层异常放进 Snapshot |
| `voice/synthesis.py` | 定义引擎无关的 `SpeechSynthesizer` Protocol、严格请求/结果与稳定错误；对最大 32 MiB 的 PCM WAV、Ogg Opus 和受支持 ADTS AAC 子集完整检查 Container/Transport Framing，但不虚构 Codec 可解码保证。 | GPT-SoVITS Adapter、Local Synthesis Service、Fake 单元测试；下游播放层仍须处理 Decoder Failure |
| `voice/gpt_sovits.py` | 把领域请求映射到 GPT-SoVITS `/tts`；只允许 Loopback IP（`localhost` 先规范化）、禁用环境代理/Redirect/Retry，要求声明长度的 Identity WAV/AAC 响应，并以 `/openapi.json` 做脱敏可用性探测。 | 外部本地 GPT-SoVITS Runtime；不会切换远端进程的全局权重，也不会把 `service_binding_unverified` 冒充成 `ready` |
| `voice/profiles.py` | 严格读取 Schema v2 JSON Catalog，把 Profile、情绪、准确参考文本/语言和带长度、SHA-256 的资产声明解析到固定模型根；拒绝旧版字符串路径、Windows 路径别名和矛盾身份，并实施 `verified` 与显式 Opt-in 的 `local-evaluation-only` 权利标签。读取声明本身不声称文件或进程已验证。 | `config/voice_profiles.example.json`、`models/weights/gpt-sovits/`、Synthesis Service |
| `voice/synthesis_service.py` | TTS 的惰性 Composition Root；从只读 resource root 查找 Weight，从独立可移动 data root 查找 Catalog，每次调用重载 Catalog，且构造本身不触碰磁盘或网络。省略 data root 只用于兼容源码开发布局。 | `config/settings.py`、Profile Catalog、GPT-SoVITS Adapter、Smoke CLI |
| `voice/speech_queue.py` | 把模型流式文本按自然标点或安全长度切句；只在候选含 Unicode 字母/数字时占用 Managed GPT-SoVITS 推理，跳过独立 `♪`/Emoji 等纯装饰片段但保留文字旁的符号。单一 FIFO Worker、固定有界容量和独立 Delivery/Abort Daemon 保持合成与通知顺序；通用 `feed` 仍是原子非阻塞提交，独立外部 Feeder 可用 Waiting API 在容量释放时逐批入队。每个物理合成都绑定一次性 Token；物理取消会 Abort，普通 UI 替换使用逻辑 Discard，允许最多一个已有 Native Call 静默排空并丢弃迟到音频，避免污染受管 Worker。外部绑定和当前不完整 Manifest 的受管绑定都禁止缓存。 | 当前 `desktop_speech.py` Voice 编排层、`voice/synthesis.py`、受管 GPT-SoVITS Lease；只处理 Speech 副本，不修改或替代最终持久化的 Assistant 原文 |

`voice/capture.py` 是单句 PCM 验证边界，详见本文“Voice Capture、本地 STT 与本地 TTS”部分。STT 由 `voice/transcription_jobs.py` 接入 Python Desktop Backend，Electron/React 消费最终的 PCM-free Transcript。TTS 同时保留外部 HTTP Smoke 链和桌面受管 Worker 链；后者由 `desktop_speech.py` 旁路接收 Brain Chunk，经私有 fd3 与 Preload Web Audio 播放，但不向 React 暴露原始音频。

## 14. Console UI：`ui/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `ui/__init__.py` | Console UI 的稳定公开导出。 | `start.py` |
| `ui/console.py` | 旧 Console Client；恢复/创建默认 Chat、流式输出、`/memory`、`/summarize`、候选记忆确认和退出。 | Brain、Memory；没有完整 Desktop Chat/Project 管理能力 |

## 15. Desktop 工程根目录：`desktop/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/.gitignore` | 排除 `node_modules`、`dist`、日志和常见本地编辑器文件；`dist-electron`、`out` 与 Playwright 输出由根 `.gitignore` 负责。 | npm/Vite/Electron/Playwright 生成物 |
| `desktop/README.md` | Desktop 开发指南；单次 `npm run dev` 启动、本地 STT 可选安装/模型目录、有界 Voice Session、静态半身应用内角色、严格发现并原地启动的外部桌宠程序、默认关闭的 Main-owned Presence/Notifications、架构边界、验证和打包说明。 | 根 README、Protocol README、npm scripts、`requirements-stt.txt` |
| `desktop/package.json` | npm 项目入口、React/Electron 依赖、开发/文档审计/测试/构建/打包脚本和 electron-builder 配置。 | 所有 Desktop 工具链 |
| `desktop/package-lock.json` | 固定完整 npm 依赖图和下载完整性，使 `npm ci` 与 CI 可复现；不要手工编辑。 | npm、GitHub Actions、安全审计 |
| `desktop/index.html` | 唯一 Vite Renderer HTML 入口；定义 CSP、favicon、viewport、theme-color 和 `#root` 后启动 React。主窗口只显示静态角色图，不在页面内加载或渲染外部桌宠模型。 | `desktop/src/main.tsx`、Vite、Electron 主窗口 |
| `desktop/vite.config.ts` | 配置 React Plugin、固定开发端口、生产相对资源路径，以及唯一的 `index.html` Renderer 构建入口。 | `npm run dev` / `npm run dev:renderer`、`npm run build:renderer`、单 Renderer 来源策略 |
| `desktop/vite.preload.config.ts` | 把主窗口的沙箱 Preload 及其本地 Helper 打成单一 CommonJS 文件，只把 Electron 保留为运行时 External；沙箱因此不需要也不能解析相对 `require`。 | `npm run electron:build`、`scripts/dev.mjs`、`dist-electron/preload.cjs`；避免 Preload 在 Electron 中因本地模块加载失败而丢失 `window.elysiaDesktop` |
| `desktop/eslint.config.js` | ESLint Flat Config；启用 JS、TypeScript、React Hooks 和 React Refresh 规则。 | `npm run lint` |
| `desktop/playwright.config.ts` | 配置 Electron UI 测试目录、单 Worker、Timeout 和失败产物路径。 | `npm run test:ui`、`desktop/tests/ui/` |
| `desktop/scripts/check-documentation.mjs` | 从仓库根目录检查 JS/TS/JSX 文件说明及公开 callable JSDoc、PowerShell 脚本/模块 Help，以及 CSS/HTML/HTM 文件说明；精确排除被忽略的 `models/cache/` 外部 Runtime，但不会误排其他名为 cache 的受维护源码。 | `npm run docs:check`、`AGENTS.md`、GitHub Actions |
| `desktop/scripts/clean-build-output.mjs` | 以固定 scope 安全清理受管构建输出；`renderer` 只能删除 `desktop/dist`，`electron` 只能删除 `desktop/dist-electron`，`all` 只能删除两者，拒绝未知参数且不接受任意路径。Renderer 与 Electron 构建分别先清自己的输出，防止已删除源码留下的旧 JS/Source Map 混入 ASAR。 | `npm run clean*`、`build:renderer`、`electron:build`、`package`、清理合同测试与分发门禁 |
| `desktop/scripts/dev.mjs` | Desktop 开发启动协调器；先编译 Electron Main、打包唯一的主窗口单文件沙箱 Preload，再启动固定 Vite Server 与 Electron，并在 Electron 退出或收到终止信号时收敛它拥有的子进程。 | `npm run dev`、Vite、Electron、`tsconfig.electron.json`、`vite.preload.config.ts`；`npm run dev:renderer` 只是无 Preload/Backend/麦克风/外部伴侣程序管理的浏览器预览 |
| `desktop/tsconfig.json` | TypeScript Solution Root，引用 Renderer 与 Node/Vite 配置。 | `npm run typecheck`、Renderer Build |
| `desktop/tsconfig.app.json` | React Renderer 的 DOM/ES/JSX/Strict TypeScript 配置；不直接 Emit。 | `desktop/src/`、Vite |
| `desktop/tsconfig.node.json` | `vite.config.ts` 的 NodeNext TypeScript 配置。 | Vite Config Type Check |
| `desktop/tsconfig.electron.json` | 编译 Electron `.ts/.cts` 到 `dist-electron`；Preload `.cts` 输出 CommonJS `.cjs`。 | `npm run electron:build`、Electron Runtime |
| `desktop/public/elysia-icon.png` | 由《崩坏3》爱莉希雅官方刻印制作的方形第三方品牌图；供 Browser favicon、开发/打包窗口、Tray 和 README 使用，不属于源码许可。 | `index.html`、Electron Main、README、Vite Public Assets、`MODEL_LICENSE.md` |
| `desktop/assets/elysia-icon.ico` | 同一官方刻印的多尺寸 Windows ICO 构建资源；不属于源码许可。 | `package.json`、electron-builder、Windows EXE/Installer、`MODEL_LICENSE.md` |
| `desktop/public/character/elysia-portrait.png` | 使用 OpenAI 内置图像生成工具、参考项目所有者直接提供的三张图片与本机素材集的一张立绘生成的应用内角色图；审核版本由路径、2,223,154 字节和 SHA-256 固定，不属于源码许可。主窗口和 Voice 把它作为静态最终图片回退；它不作为外部桌宠程序的占位图或资源。 | Character UI、Vite Public Assets、`scripts/check_distribution_assets.py`、`MODEL_LICENSE.md` |
| `desktop/public/character/elysia-state-atlas.png` | 与 `data/characters/elysia-2dArt/02-activity-states.png` 逐字节相同的 4×2 RGB 运行时图集；前七格映射封闭 Character State，第八格 success 不使用；2,303,963 字节与 SHA-256 由分发门禁固定，不属于源码许可。 | CharacterArtwork、Vite/ASAR、`character-presentation.ts`、`scripts/check_distribution_assets.py`、`MODEL_LICENSE.md` |
| `desktop/public/character/elysia-expression-atlas.png` | 与 `data/characters/elysia-2dArt/03-expression-atlas.png` 逐字节相同的 5×4 RGB 运行时图集；只有用户限定的十值 Voice Emotion 映射可选审核格；2,500,647 字节与 SHA-256 由分发门禁固定，不属于源码许可。 | CharacterArtwork、Vite/ASAR、`character-emotion.ts`、`character-presentation.ts`、`MODEL_LICENSE.md` |
| `desktop/public/character/elysia-speech-atlas.png` | 与 `data/characters/elysia-2dArt/04-facial-rig-atlas.png` 逐字节相同的 RGB 图集；当前仅保留为静态审阅资料，不参与 CharacterArtwork、音频嘴型或桌宠程序；2,054,767 字节与 SHA-256 由分发门禁固定。 | Vite/ASAR 静态资料、`scripts/check_distribution_assets.py`、`MODEL_LICENSE.md` |
| `desktop/benchmarks/measure-shell.ps1` | Electron/Tauri 决策时使用的 Windows 启动、内存、进程树和正常退出 Benchmark。 | Desktop ADR；不参与正常启动 |

## 16. Electron 可信边界：`desktop/electron/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/electron/contracts.ts` | 定义唯一主 Renderer 可见的最小 Desktop API、Backend Snapshot/Event、Chat/Project/Settings/Attachment/Knowledge/Voice、Song Cover、Data & Storage、桌宠与 Presence 公开状态类型；Snapshot 还携带 Electron 跨 Renderer reload 持有的准确 Chat generation 与 lifecycle/export Knowledge request ownership。Song Cover API 只接受闭集方法/来源/移调和可选歌曲身份，返回无路径状态，不能取得音频字节、歌词、模型或进程。Data API 只能读取容量、查看 Main 已认证的 active/recovery root、打开当前目录、请求原生选目录/迁移及用 revision+scan token 清理固定临时类别；桌宠 API 只能请求原生程序目录选择、重扫和以 opaque ID 更新选择，不接受 Renderer 路径。为保持既有 IPC 兼容，部分字段仍名为 `models / modelId / selectedModelId`，其当前语义都是“检测到的伴侣程序”。它不是 Python 原始 Wire Schema。 | 主 Preload、Main、React、Mock Preload；状态不含 WAV、歌词、Token、Hash、正文、Renderer 自选路径、move capability、桌宠可执行路径/PID、export destination、私有 reminder anchor 或诊断；旧根只在被持久化标记为 Recovery Copy 时有意披露精确本机路径 |
| `desktop/electron/preload.cts` | 用 `contextBridge` 暴露唯一固定的 `window.elysiaDesktop`；把 Knowledge list/mutation/export/cancel、Song Cover 选择/播放/取消/导出、桌宠程序目录选择/重扫/opaque 程序选择、Presence Settings 控制、Presence Voice-active 信号、一次性 STT 开始/取消和按 Request ID 停止播放映射到固定 IPC，并把净化 Event/Presence state 转交 Renderer。Presence Bridge 不暴露 `Notification` 构造器、通知正文、节奏锚点或任意 Channel。私有、未导出的 Web Audio Owner 只接受 Main 发来的 Canonical 32 kHz mono PCM16 WAV；私有 Music Owner 则只接受 Main 验证后的一个有界 MP3，并以媒体元素完成长音频播放/取消。连续 Speech Clip 复用一个可信 `AudioContext + GainNode` 输出图，但每段仍重新读取并在 Decode/Start 前应用 Active `speechVolumePercent` 与已保存 Output Sink。失败或初始化期取消会先隔离并关闭旧图，防止迟到 `setSinkId` 修改替换 Clip。应用内角色永远静态，因此 Preload 不向 React 发布 RMS、嘴型或连续包络。无效音量或指定设备路由失败会 Fail Closed，绝不回退到默认扬声器。播放结束、失败或取消只回送一次性 opaque Settlement；React API 不接触 WAV/MP3、歌词、原始样本、Token、Hash、`ipcRenderer`、Node、`fs`、桌宠程序路径、PID 或外部配置。 | React、Electron Main、`speech-playback-owner.ts`、`music-playback-owner.ts`、Global Settings、Project Sources、Desktop Pet 与 Presence Settings |
| `desktop/electron/main.ts` | Electron 主进程；除主窗口/托盘、原生通知、权限和固定 IPC 外，它初始化稳定数据指针，把唯一绝对数据根注入 Python，组合单一 Song Cover Manager，并串行执行容量扫描、原生空目录选择、Backend 离线、两阶段移动/Readiness/commit/rollback，以及只含 Audio/Cache/Logs 的确认清理。它还私有保存桌宠程序目录：用严格 Scanner 建立无路径摘要，在每次启动前重新执行 lstat/realpath/边界/结构与完整受信文件 Hash 校验，再交给单一 program manager 原地启动或按 owned PID 有序停止/切换。它不创建桌宠 BrowserWindow，也不读写外部程序的 `config.json`。启动遇到 pending move 会先保守回滚；无法启动时显示本地恢复提示，退出由 Shutdown Gate 阻止重复 quit，直到 Song Cover/长音频、存储事务和桌宠进程按有界规则收敛。Cleanup 返回是 commit point，之后 Backend/容量失败只形成 warning，不把已删除内容误报为失败。Renderer 只能看到当前根、Song Cover 安全摘要、容量、pending fact、持久化 Recovery Copy 路径和无路径桌宠程序摘要，不能获得内部事务、可执行路径、歌曲/歌词路径或 PID。 | `application-shutdown-gate.ts`、`song-cover-manager.ts`、`data-storage.ts`、主 Preload、`desktop-pet-program-library.ts`、`desktop-pet-program-manager.ts`、Desktop Pet/Presence、BackendProcess、Electron Dialog/Shell/Notification/Clipboard/Audio/Screen/Tray |
| `desktop/electron/application-shutdown-gate.ts` | 把 Electron 可能重复触发的 `before-quit` 折叠成 `start / wait / allow` 三态；所有强制私有清理完成前持续阻止退出，只允许 Main 最终的一次程序化 quit，清理失败时可安全恢复为可重试状态。 | `main.ts`、Song Cover/桌宠/数据事务退出清理、Gate tests |
| `desktop/electron/data-storage-contracts.ts` | 定义 layout/bootstrap v1、十个容量类别、三类可清理 allowlist、内部 move transaction、Renderer-safe public state、持久化 retained-root 视图、一次性 cleanup request 与 commit/rollback 结果；严格拒绝额外字段、任意清理路径和 durable cleanup 类别。 | `data-storage.ts`、Main、Contracts/Preload、Settings、Node/UI tests |
| `desktop/electron/data-storage.ts` | Electron Main 私有的生产数据根管理器；在稳定 `userData` 保存原子 bootstrap，以 `layout.json`/rootId 认证根目录，首次只复制并 SHA-256 验证 legacy workspace；有界扫描不跟随 link/junction，并按十类报告容量。移动只接受 Main 选择的空绝对目录，拒绝资源根/重叠路径，经 sibling stage 与完整 Hash 后发布 pending pointer；Backend ready 后先 finalise pointer，再用 prepare fingerprint、第二次隔离扫描和 exact-file 删除保守退休旧根。删除前先持久登记原路径与隔离候选，重启会重新核对，无法安全删除的 Recovery Copy 因而不会成为隐藏孤儿。清理只能消费 revision-bound 一次性 token 并触达 Audio/Cache/Logs。 | Main、`data-storage-contracts.ts`、`backend-process.ts`、`data-storage.test.mjs`；不接触 Renderer IPC 或 Python 生命周期 |
| `desktop/electron/presence-notification-contracts.ts` | 定义 `off / daily / weekly` Reminder 闭集、`available / unsupported / failed` 运行状态、Renderer-safe state 与 revisioned replacement request；严格要求三个精确更新字段，拒绝任意文案、URL、声音、紧急度、动作和自定义 schedule。 | `contracts.ts`、Preload、Main、App、Settings、Mock 与 Node 合同测试；不属于 Python Protocol |
| `desktop/electron/presence-notification-policy.ts` | 以纯函数计算 daily/weekly 下一到期时间，并依据 opt-in、native runtime、退出状态、窗口存在/可见/最小化/聚焦、Backend readiness 与 Chat/Voice/受管朗读/Knowledge busy 决定 Reply-ready 或 Reminder 是否可投递。Reminder 只有窗口 absent/hidden/minimized 才可出现；仅 Alt-Tab 离开焦点不够。时钟回退等待一个普通周期；纯策略只返回投递决定，Main 会在调用前持久化消费到期周期，避免恢复空闲后补发或爆发。 | Main、`presence-notification-contracts.ts`、Node 合同测试；不读取消息、Prompt、Project 或模型输出 |
| `desktop/electron/presence-native-notification.ts` | 管理唯一可替换的原生通知槽；Completion 可替换 Reminder，Reminder 不覆盖仍显示的 Completion。Windows `timedOut` 后继续保留可撤回 Handle，固定 ID/Group 的下一条、Settings 全关或退出会移除 Action Center 旧项；替换时先解绑 Listener，以对象身份拒绝旧实例迟到的 click/failed，原生异常也不会逃逸到 Chat。 | Main、`presence-native-notification.test.mjs`；只接收 `completion / reminder` 闭集，不接触通知正文或 Electron IPC |
| `desktop/electron/presence-notification-preferences.ts` | Electron Main 私有的 Presence JSON Repository；默认 Reply/Reminder 均 Off，限制 16 KiB 严格 Schema，以 revision CAS、同路径锁和同目录临时文件同步后原子替换公开选择。私有 `lastReminderHandledAt` 锚点不进入 Renderer state；切换 cadence 会重新锚定，坏/超限文件 Fail Closed，持久化失败以封闭错误交由 Main 隔离。 | Main、Node 合同测试；文件位于 Electron `userData/presence-notifications.json`，不属于 Python Workspace |
| `desktop/electron/desktop-pet-contracts.ts` | 定义 `disabled / hidden / visible` 偏好、`absent / loading / visible / failed` 运行状态、程序库状态、无路径程序摘要和严格 revision update。公开字段沿用 `models / modelId / selectedModelId` 名称以保持既有主 Renderer 合同兼容，但 ID 只代表 Main 严格扫描出的伴侣程序，不能编码路径、参数或 PID。 | Main、主 Preload、App、Settings 与测试；不含原生路径、进程句柄、Backend 或任意 IPC |
| `desktop/electron/desktop-pet-directory-picker.ts` | 为原生桌宠程序目录选择器计算 Main-private 默认位置并验证选择结果；已有保存目录优先，开发环境只在真实存在时定位到通用 `data/characters` 父目录，其中本地付费合集子目录由 Git 忽略，并避免将专有合集名编入公开代码；打包版本绝不探测该源码目录。取消返回空选择，非单一绝对路径 Fail Closed。 | Main、Electron Native Dialog、目录选择合同测试；绝对路径不进入 Renderer |
| `desktop/electron/desktop-pet-program-library.ts` | 在 Main 中有界发现 Bongo Cat Mver 伴侣程序：限制遍历深度、条目和候选数，拒绝 Node 可识别的 Symlink/Junction 及 realpath 发生重定向的路径；候选必须具有精确 `A<目录名>.exe`、受信 UI EXE/DLL、全部审核的顶层 Code DLL、`Resources/cat.ttf` / `l2dlogo.png`、有界且含固定顶层结构的 `config.json`，以及完整 `img/{standard,keyboard,gamepad}/cat_model/cat.model3.json` 引用闭包。可执行代码和固定资源按字节长度与 SHA-256 固定，模型数据可不同但每条必需路径仍须留在所选根内；额外顶层 EXE/DLL、已识别链接与路径逃逸均拒绝。程序 ID 由相对目录与固定 Launcher Hash 派生，所以用户修改 `config.json` 后选择仍稳定。`revalidateDesktopPetProgramForLaunch` 在每次启动前重做 lstat/realpath/边界/结构与全部受信文件 Hash 校验，以拒绝复核时已经陈旧的选择并缩短 check-to-use 窗口；最终启动仍按路径执行，因此不宣称消除复核后到 CreateProcess 前的 TOCTOU。 | Main、`desktop-pet-program-manager.ts`、`desktop-pet-preferences.ts`、Program Library 合同测试；只向 Renderer 提供 opaque ID、display name 与 folder name，不公开原生路径 |
| `desktop/electron/desktop-pet-program-manager.ts` | 串行协调一个可选外部桌宠子进程；用复核后的精确 EXE、空参数、`shell: false`、最小 OS/Profile 环境变量白名单和程序原目录启动，因此原程序继续拥有其位置、输入模式与 `config.json`，同时不会继承启动 Elysia 的 API Key 或开发凭据。固定 Hash 的受审 Launcher 虽声明 `requireAdministrator`，但 Main 只给该子进程写死 `RunAsInvoker`，让它留在当前用户 Token，不授予未签名外部程序管理员权限，并保留精确 PID tree 所有权；对其他管理员程序的输入仍服从 Windows 完整性隔离。切换必须先按 Elysia 记录的精确 owned PID 停止旧进程并等待退出，绝不按镜像名枚举或关闭用户手工启动的同名副本；停止失败/超时会保留 ownership 并阻止第二个进程。Hidden、Disabled 与 shutdown 都走同一有界 stop 路径。 | Main、Program Manager 合同测试、`desktop-pet-program-library.ts`；不复制、解析或改写外部程序配置 |
| `desktop/electron/desktop-pet-preferences.ts` | Electron Main 私有的 Schema v3 桌宠 JSON Repository；偏好文件缺失时默认 `disabled`，v1/v2 迁移丢弃已退役内嵌窗口的 placement，旧内置模型不会自动开启外部程序，坏/超限/不合法文件同样 Fail Closed。Repository 限制 16 KiB 严格 Schema，以 revision CAS、同路径串行锁和临时文件同步后 rename 原子写入模式、私有程序目录与 opaque selection；公开状态只含 folder name、扫描状态和无路径程序摘要。 | Main、Scanner、Node 合同测试；文件位于 Electron `userData`，程序绝对路径/PID 不进入 Renderer 或 Python Workspace，外部 `config.json` 留在原目录 |
| `desktop/electron/desktop-pet-lifecycle.ts` | 提供可独立测试的桌宠协调边界：Settings/Tray/扫描/选择/模式变化共享可恢复的串行 mutation queue；`visible + absent/failed` 的显式动作会重试而非只看持久意图短路；程序库失效必须先停止准确 owned PID 才允许清除持久选择。Hidden opt-in 与 Disabled 的 last-window 退出规则保持明确；退出时先有界排空已接纳的桌宠偏好写入，再与 Presence 等独立持久化并行受同一 2 秒总上限约束，以便 Main 随后停止准确的 owned 程序。 | Main、Lifecycle Contract Tests；不创建 BrowserWindow、不管理外部程序位置或配置 |
| `desktop/electron/bounded-ndjson.ts` | 用固定上限 Buffer 增量切分 Python stdout；按原始字节限制 Frame，接受 CRLF，严格拒绝坏 UTF-8、未换行截断和超限无换行数据，并在终态移除全部 Stream Listener。 | `desktop/electron/backend-process.ts`、Protocol Contract Tests |
| `desktop/electron/speech-audio-channel.ts` | 增量解析独立 Pipe 上的固定 84-byte `audio.binary.v1` Frame；在 Payload 分配前限制 8 MiB，流式校验 SHA-256，只接受精确 32 kHz mono PCM16 WAV 与 120 秒上限，并以单 Frame ACK/Discard、Pause 和 `unshift` 保持顺序、背压及有界内存。任何坏 Header、Token、Hash、WAV、截断或 ACK 都会终止 Reader；无待处理 Frame 的干净 EOF 会单独通知 Owner。 | `speech-delivery.ts`、`backend-process.ts` fd3 Owner；Reader 不自行销毁 Owner Stream，原始 WAV 不进入 NDJSON 或 React |
| `desktop/electron/speech-delivery.ts` | 在 Electron Main 内关联可以任意先后抵达的 NDJSON Clip Metadata 与 fd3 Binary Frame，逐项核验 Request/Chat/Sequence/Token/长度/格式/Hash，并且每次只允许一个未确认 Frame。失败句子按序跳过；Terminal、取消、迟到结果、播放器失败和 Pipe EOF 都以有界状态收敛。Main 只读取一个完整 turn 是否仍在合成空档、排队、播放或 terminal drain 的布尔值，用于抑制可选 Presence 通知。 | `backend-process.ts`、`speech-audio-channel.ts`、`speech-playback-owner.ts`；对 React 只可生成无 Token/Hash/音频的安全状态 |
| `desktop/electron/speech-playback-owner.ts` | Main 到可信 Preload 的单 Clip 播放 Owner；生成一次性 UUID、验证 Settlement 只能来自所属窗口 Main Frame，以 130 秒上限处理播放、取消、窗口销毁和跨文档断连。有效但过期的 Settlement 不能结算当前 Clip；超时、坏 Settlement 或有界 Retired-ID 达上限时，Main 在同一存活可信文档上换用新的相关性 Owner，不再永久断开后续分句；导航、崩溃或窗口关闭则必须等待新 Renderer。页面内锚点跳转不会误中断播放；Main 读取 `hasActivePlayback()` 作为当前 Clip 的防御性 busy 信号，完整朗读 turn 则由 Delivery Coordinator 持有。 | `main.ts`、`preload.cts`、`speech-delivery.ts`；固定私有 IPC Channel 不进入 `DesktopApi`，Presence 不接收 WAV 或播放 ID |
| `desktop/electron/music-playback-owner.ts` | Main 到可信 Preload 的单首长音频 Owner；限制 MP3 大小与总播放时长，以一次性 UUID 绑定准确窗口/Main Frame，并在替换、取消、导航、Renderer 崩溃或超时后 Exactly-once 收敛。歌曲字节和播放 ID 从不进入公开 React API。 | `main.ts`、`preload.cts`、`song-cover-manager.ts`、Music playback tests |
| `desktop/electron/song-cover-contracts.ts` | 定义 Lyrics-driven/Legacy 两种引擎、完整歌曲/stems 两种来源、有限移调、歌曲身份、封闭阶段/错误码、无路径 Renderer 状态，以及只含 `available/unavailable` 与两个固定原因的 Runtime Readiness；严格解析所有请求、Readiness 与 Worker progress，拒绝额外字段和任意路径/命令。 | `contracts.ts`、Preload/Main、`song-cover-manager.ts`、React Song Cover UI、Contract tests |
| `desktop/electron/song-lyrics-provider.ts` | Main 私有的 LRCLIB HTTPS 客户端；Origin/Host/GET 固定，不带凭据、不跟随重定向，限制超时/重试/响应/结果/文本大小，并按标题、歌手、时长和同步程度确定性匹配，低置信度结果 Fail Closed。 | `song-cover-lyrics-assets.ts`、LRCLIB、Provider tests；只发送用户确认或音频标签产生的元数据，不发送音频或本机路径 |
| `desktop/electron/song-cover-lyrics-assets.ts` | 在 Main 私有 Job 内用受限 FFprobe 读取标题/歌手/时长，经明确 AbortSignal 调用固定 LRCLIB Provider，并以 no-follow/独占写入保存 `lyrics.lrc`、纯歌词与 manifest；超长歌曲、缺少同步歌词或取消都会在启动 SoulX 前停止并清理。 | `song-lyrics-provider.ts`、`song-cover-manager.ts`、Lyrics asset tests |
| `desktop/electron/song-cover-manager.ts` | 串行拥有一个本地 Song Cover Job 的原生输入/输出路径、Worker、临时目录、状态、长音频播放、导出与取消；Lyrics-driven 路径先准备同步歌词再启动 WSL SoulX Worker，Legacy 兼容名路径则只向 Worker 传递固定 `models/cache/rvc-v2-40k/` 与 `models/weights/rvc/elysia-v2-40k/{model.pth,model.index}`。启动前只以 `lstat` 核验源码环境核心文件为普通非链接文件，安装包因未包含 Runtime 固定 Fail Closed；该 Readiness 不联网、不启动模型，Main 仍会在 Picker 前后复核。只发布封闭状态和安全文件名，关闭/失败/超时执行私有 cleanup-only 协议后再删除 Job。 | `main.ts`、`song-cover-contracts.ts`、`song-cover-lyrics-assets.ts`、`music-playback-owner.ts`、Python Song Cover workers、Manager tests |
| `desktop/electron/backend-process.ts` | Python 子进程 Owner 和 Protocol State Machine；通过有界 NDJSON Reader 限制 stdout，关联 Chat/STT/Knowledge Request 与封闭 Lifecycle Event，并为子进程建立独立 fd3 Speech Pipe。Main 构造时提供的数据根会覆盖并删除 ambient `ELYSIA_DATA_ROOT` authority；tentative move 失败恢复旧根，已持久化 rollback 的 authoritative restart 即使失败也保持稳定根。它另提供 Main-only maintenance busy fact，防止移动打断请求或朗读。其余 Chat/Knowledge/Speech correlation、export receipt 与错误净化边界不变。 | Main、`data-storage.ts`、`desktop_backend.py`、`protocol.ts`、`bounded-ndjson.ts`、`speech-delivery.ts` |
| `desktop/electron/protocol.ts` | TypeScript 端 Protocol v1 类型、Builder、Parser 和严格 Runtime Validation；除 Voice/Settings 外还封闭验证 Knowledge method/state/operation/export 与 grounded proof，复核 Project/Citation ID 闭包、answered context 和逐字 `source_fact` Evidence，不把静态类型当安全边界。 | BackendProcess、共享 Schema/Fixtures、Contract Tests、私有二进制音频 Reader |
| `desktop/electron/protocol-text.ts` | 定义跨 Python/TypeScript 一致的 Unicode Code Point 长度、Blank Set 和 Trim 规则。 | `protocol.ts`、Python Contracts |
| `desktop/electron/renderer-source.ts` | 用 exact-entry policy 只验证固定 Vite Root 或打包后的 `dist/index.html`；已没有第二 Renderer 入口可借用主 Preload 能力。 | Main、Permission Policy、Protocol Contract Tests |
| `desktop/electron/audio-permission.ts` | 只为可信主窗口和主 Frame 放行 audio-only microphone 或 speaker selection。 | Main 的 Chromium Permission Handler |
| `desktop/electron/external-url.ts` | 只接受无 Credentials 的 HTTP/HTTPS URL，拒绝危险 Scheme、空白、NUL 和无 Host URL。 | Main、Markdown Link、系统浏览器 |

## 17. React Renderer 总入口：`desktop/src/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/main.tsx` | 初始化 React Root、StrictMode、ThemeProvider 和 ErrorBoundary；初始 Paint 后通知 Electron。主 Renderer 没有角色动画 Provider。 | `index.html`、`App.tsx`、Preload API |
| `desktop/src/AppErrorBoundary.tsx` | 捕获 React Render Error，显示可恢复错误并把焦点移动到错误区域。 | `main.tsx` |
| `desktop/src/App.tsx` | 主 Renderer 总协调器；除 Canonical State、Draft、Retry、Attachments、Settings、Voice、Knowledge、桌宠与 Presence 外，还订阅 Main-owned Song Cover/Data Storage state。Song Cover 只发送闭集 setup request、job ID 和播放/导出/取消意图，把封闭错误码转换为可操作提示，并在 Voice/普通朗读/聊天等竞争音频操作之间执行明确停播规则；不持有歌曲、歌词、输出、模型或进程路径。Data Storage 以本地 operation ID/busy guard 调用刷新、原生移动、固定临时清理和打开目录；桌宠流程只提交 revision、闭集模式和 Main 已发布的 opaque 程序 ID。 | 所有主 React Feature、`window.elysiaDesktop`；Canonical state 仍由 Python/Main 返回，Renderer 只保存暂态 |
| `desktop/src/App.css` | App Shell、Chat（含输入卡内的紧凑附件预览、Song Cover 状态/进度/播放控件与模型选择器左侧加号）、Song Cover/Dialog、Settings、Data & Storage 容量卡/类别/操作、Desktop Pet/Presence、Voice、静态状态/表情、Responsive、High Zoom 和 Forced Colors 样式。 | `App.tsx`、`SongCoverSetupDialog.tsx`、`CharacterArtwork.tsx`、`SettingsView.tsx`、`CallPreview.tsx`、Design Tokens |
| `desktop/src/desktop-api.d.ts` | 扩展 Browser `Window` 类型，声明可选 `elysiaDesktop`；不会实际创建 API。 | TypeScript、Preload Contracts |

`App.tsx` 的 LocalStorage 只保存 UI 恢复数据，例如 Chat Draft、Pending Send 和 Retry Draft。Python 返回的 Chat/Project 仍然是 Canonical State。

## 18. React Shell：`desktop/src/shell/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/shell/AppShell.tsx` | 排列 Sidebar、Workspace 和 Character Panel；在窄屏把侧栏变成 Modal，并管理 Focus Trap、Inert、Escape 和焦点恢复。 | `App.tsx`、Sidebar、Feature Views |
| `desktop/src/shell/Sidebar.tsx` | 显示新的 Elysia 品牌图标、主导航和 Chat History；支持搜索、Create/Open、Rename、Pin、Archive/Restore、Delete 和批量操作。 | App callbacks、Backend Canonical Chat List、`public/elysia-icon.png` |
| `desktop/src/shell/ChatActionDialog.tsx` | Chat 操作和 Song Cover Setup 共用的 `<dialog>` 容器；初次打开才定位指定起始控件，后续受控输入重渲染不夺回焦点，同时用 Tab trap、Escape、关闭后焦点恢复和可选长内容滚动保持键盘可用性。 | Sidebar、`SongCoverSetupDialog.tsx`、App mutation callbacks、UI focus regression |

## 19. React Chat：`desktop/src/chat/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/chat/ChatView.tsx` | 组合 Chat Header、Connection Status、Message Timeline、Feedback 和 Composer，并管理滚动。 | App、MessageView、Composer |
| `desktop/src/chat/Composer.tsx` | 受控 Textarea、模型选择器左侧紧凑附件加号/预览、音符 Song Cover 入口与安全状态/进度/播放/导出/取消控件、Dictate/Voice 入口和 Send/Stop；整张输入卡接受文件拖放，并保护中文 IME，Enter 发送、Shift+Enter 换行。 | ChatView、App callbacks、`song-cover-stage.ts`；不直接访问 Electron API、歌词或本机路径 |
| `desktop/src/chat/MessageView.tsx` | User 消息以纯文本显示；Assistant 使用安全 GFM；禁止 Raw HTML/外部图片，支持复制、Regenerate、Edit and retry、Attachment Chips，以及可键盘聚焦的 grounded statement kind 与 Citation detail。 | App callbacks、Electron External URL/Clipboard、Knowledge proof DTO |
| `desktop/src/chat/types.ts` | 定义只供 Renderer 展示的 Message/Streaming/Retry/Notice 与 grounded proof 类型；不是持久化 Schema。 | App、ChatView、MessageView |
| `desktop/src/song-cover/song-cover-stage.ts` | 以纯闭集集合判断哪些 Song Cover 阶段仍拥有生成管线；Renderer 用同一结果禁用重复选择并显示 Cancel，不自行推断 Worker 进程状态。 | `App.tsx`、`Composer.tsx`、Song Cover contract |
| `desktop/src/song-cover/SongCoverSetupDialog.tsx` | 收集 Lyrics-driven/Legacy、完整歌曲/stems、有限移调与成对 title/artist；默认 Lyrics-driven，并在用户确认前披露只把歌曲身份与时长发送给 LRCLIB，输入字段受控但不会因每次键入被 Dialog 抢回焦点。 | `ChatActionDialog.tsx`、`App.tsx`、Song Cover contracts、UI tests |

## 20. React Project 与 Attachment：`desktop/src/projects/`、`desktop/src/attachments/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/projects/ProjectView.tsx` | 完整 Project UI；创建/打开/编辑/归档、Instructions、Workspace、Chat 归属，以及 canonical Project Sources lifecycle；全局 Knowledge owner 存在时禁用会改变 authority 的 Project 动作。 | App、Desktop API、ProjectSourcesPanel |
| `desktop/src/projects/ProjectView.css` | Project Split View、List/Detail、Tabs、Cards、Workspace、Knowledge surface、Dialog 和响应式布局。 | ProjectView、Knowledge.css、Design Tokens |
| `desktop/src/attachments/AttachmentSurface.tsx` | Scope-bound Attachment UI；支持完整面板和 Chat 输入卡紧凑模式，处理 Picker、整卡 Drag/Drop、Metadata、Remove、Pending/Error、切换 Scope 时的拖放状态清理和焦点恢复。 | App/Desktop API、Composer、未来 Project Attachment UI |
| `desktop/src/knowledge/ProjectSourcesPanel.tsx` | 显示 Source health、持久 operation history/progress/error，并委托 add/replace/reindex/delete/export/rebuild/revoke/recover/stop；组件本地 pending 只覆盖 picker/dialog，Backend request ownership来自 App 恢复的 Electron snapshot。全局 Knowledge busy、归档或 capability 缺失时只读，export 不启用 Stop，也不接收本地路径、内部 File ID、Hash、Prompt 或 Vector。 | App、ProjectView、Knowledge Desktop API |
| `desktop/src/knowledge/Knowledge.css` | Project Sources、进度、错误、grounded statement/Citation 与显式 per-Chat knowledge toggle 的响应式/Forced-colors 样式。 | ProjectSourcesPanel、MessageView、App |

Project Memory 页面目前仍是明确 Placeholder。Project Sources 已使用独立 lifecycle route 接入 Production Knowledge Runtime；普通 Attachment API 对 Project scope fail closed，避免绕开 index/catalog saga。文件问答仍必须在准确 Project Chat 中显式开启，不会因添加 Source 自动发生。

## 21. React Settings 与 Voice：`desktop/src/settings/`、`desktop/src/voice/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/settings/SettingsView.tsx` | 除全局/Voice/外观/桌宠/Presence 设置外，提供 Backend 离线时仍可见的 Data & Storage 区域：显示 active root、跨重启 Recovery Copy 路径、总量/可用/可回收、测量时间、十个固定类别、blocked/truncated/operation warning，并把 Refresh/Move/Open/Clear 作为互斥 Main action。外观区明确 Chat/Voice 状态板永远静态；桌宠区说明来源 `@书呆儿`、免费版下载链接、默认关闭，提供原生程序目录选择、重扫、实际检测程序选择和 Visible/Hidden/Off。界面只显示 folder name、display name、扫描/运行状态与安全 warning，绝不显示绝对路径、可执行文件、PID 或 `config.json` 内容。只有 Audio/Cache/Logs 标为 Temporary；Durable 与 Recovery Copy 没有通用删除入口。 | App、Desktop Settings/Voice Backend、Data Storage public state、Desktop Pet/Presence Main State、ThemeProvider |
| `desktop/src/settings/VoiceSettingsSection.tsx` | 设备偏好 UI；枚举麦克风/扬声器、保存 opaque ID、显示权限、刷新设备、运行短暂输入电平和输出音调测试。 | `audio-devices.ts`、Voice Desktop API |
| `desktop/src/voice/audio-devices.ts` | Stage 7 设备 Controller；构造时不请求权限，管理 enumerate、8 秒麦克风 Level Test、800 ms Speaker Tone、Race 和 Cleanup。 | VoiceSettingsSection、Browser MediaDevices/AudioContext |
| `desktop/src/voice/voice-session-controller.ts` | Renderer-local 的封闭五状态 Voice Session Controller；只保存有界 ID、Final Transcript、安全终态和单调 `completionId`，不拥有 PCM、播放器或持久化。一次性 `startContinuationListening` 只消费同一成功 Turn 的干净完成记录，并拒绝重复、过期、取消或 Speech 未排空的续听。 | 以 epoch、Chat、Project、Capture/STT、Chat Operation/Request 和 Speech Sequence 拒绝迟到、跨会话及乱序事件；把已确认 Transcript 交给现有 Chat 路径 |
| `desktop/src/voice/voice-ui-state.ts` | 纯函数推导 Voice 页面展示状态：把主会话生命周期与麦克风 Active/Monitoring/Muted/Unavailable 分离，并格式化有界 Session 计时。Reply-time Monitoring 不会覆盖 Thinking/Speaking，Confirmed Barge-in 才进入 Interrupting；其主生命周期是 Character State 的输入之一，但仍不拥有角色状态。 | `CallPreview.tsx`、`audio-capture.ts`、`voice-session-controller.ts`、`voice-ui-state.test.mjs` |
| `desktop/src/voice/CallPreview.tsx` | 全窗口有界 Voice 页面；分别显示主生命周期与麦克风状态、Session 计时、Assistant Captions、可编辑 Final Transcript，以及 Captions、Mute、Audio Settings、Capture、Auto-continue、Close Voice 控件，并复用永远静态的 CharacterArtwork 消费同一 Character State 与 Active Voice Emotion。 | App、`voice-ui-state.ts`、`character-state.ts`、`character-emotion.ts`、Capture Controller、Voice Session Controller；不显示实时 Partial、不会自动提交 Transcript，也不渲染音频嘴型；自动续听必须由设置/控件显式启用并由 App 的安全条件放行 |
| `desktop/src/voice/transcription-readiness.ts` | 把 Python/Electron 的闭合集合 STT Status/Reason 转成 Settings 与 Voice 共用的安全、可操作提示；绝不渲染模型路径或 Native Error。 | `App.tsx`、`SettingsView.tsx`、`electron/protocol.ts` |

## 22. Character、Design System 与 Theme

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/src/character/character-state.ts` | 定义 `idle/listening/thinking/speaking/working/waiting_approval/error` 闭集、确定性优先级与纯推导函数；只输出语义状态和可访问文案，不引用图片、Live2D 参数或动画文件。 | `App.tsx`、CharacterArtwork、CharacterPanel、CallPreview、纯状态测试 |
| `desktop/src/character/character-emotion.ts` | 定义唯一允许的十值 Voice Emotion 闭集，并把未知 Settings/Protocol 值安全回退为 `neutral`；不接受模型生成的任意情绪或视觉选择器。 | App、Settings、`character-presentation.ts`、CharacterArtwork、纯 presentation 测试 |
| `desktop/src/character/character-presentation.ts` | 以穷尽 Record 把七态映射到状态图集格与闭集语义动作，并把十种用户情绪映射到审核静态表情格；不接受 URL、模型文本、任意格号或 CSS class，第八格 success 明确不进入状态合同。 | CharacterArtwork、`elysia-state-atlas.png`、`elysia-expression-atlas.png`、纯 presentation 测试 |
| `desktop/src/character/CharacterArtwork.tsx` | 永远以普通图片呈现应用内 Chat/Voice 角色：按状态/情绪在审核图集中选格，失败时按 expression → state → 原审核立绘 → 可访问文本回退。它不创建 Canvas、Live2D/WebGL、动画循环或音频嘴型，也不拥有 Chat、Voice 或 Work 生命周期。 | CharacterPanel、CallPreview、三张运行时静态图片、`character-presentation.ts` |
| `desktop/src/character/CharacterPanel.tsx` | 显示当前 Chat、模型、独立 Backend 连接状态、状态驱动静态角色图和可访问 Character State live status；保持只读、可关闭，不写 Chat/Memory。 | AppShell、Backend Snapshot、CharacterArtwork、`character-state.ts` |
| `desktop/src/design-system/tokens.css` | 集中定义颜色、字体、Spacing、Radius、Shadow 和 Motion Semantic Tokens。 | 全部 UI CSS、Light/Dark/Forced Colors |
| `desktop/src/design-system/global.css` | 导入 Tokens，并提供 Reset、字体、Root、表单、Focus、Selection、Scrollbar 和 Accessibility Defaults。 | `main.tsx`、整个 Renderer |
| `desktop/src/design-system/Icon.tsx` | 项目统一 SVG Icon Set；默认 Decorative，避免重复 Screen Reader Label。 | Sidebar、Composer、Views、Feedback |
| `desktop/src/design-system/Feedback.tsx` | 可复用 LoadingState、EmptyState、InlineAlert 和状态 Action。 | Chat/Project/Settings/Voice 页面 |
| `desktop/src/theme/ThemeProvider.tsx` | 管理 System/Light/Dark Theme、`elysia.theme` LocalStorage、Media Query 和 Electron Native Background 同步。 | `main.tsx`、SettingsView、Main IPC |

Character State API 目前是 Renderer 内部合同，不属于 `desktop_protocol`；`working` 可由当前 Chat 所属 Project 的 Knowledge busy 或 Work-mode generation 产生，同一 Project 的 Knowledge error 会映射为 `error`，其他 Project 的后台状态不会污染当前角色。`waiting_approval` 尚无 producer；闭集中保留它不代表 Work Agent 已实现。

## 23. Desktop Protocol：`desktop_protocol/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop_protocol/README.md` | 人类可读 Protocol v1 文档；说明 Handshake、Capabilities、Streaming、Cancel、Settings、Attachment、Knowledge Lifecycle/Grounded Answer、Voice Capture/Transcription，以及 fd3 Speech Audio Frame 与封闭控制事件的不变量。 | Python/TypeScript 实现和测试 |
| `desktop_protocol/schema/v1.schema.json` | Draft 2020-12 JSON Schema；描述全部 Client/Server Frame，并约束 Knowledge methods/state/operation events、grounded Chat history、STT/PCM/Final Result 和 Speech exact payload。 | Shared Fixtures、Python/Node Contract Tests |
| `desktop_protocol/fixtures/v1.samples.json` | Python 与 TypeScript 同时读取的 Valid/Invalid Conformance Samples；覆盖 Knowledge/RAG intent/grounded history、STT 和 Speech 的合法样本，以及 extra field、路径/错误详情泄漏、引用闭包或状态不一致拒绝样本。 | `contracts.py`、`protocol.ts`、两端测试 |
| `desktop_protocol/audio_channel.py` | 用固定 84-byte Header 和独立 fd3 匿名 Pipe 传送最多 8 MiB、120 秒的 canonical 32 kHz mono PCM16 WAV；生成不重复 Correlation Token、SHA-256 和安全 Metadata，验证 OS Pipe 类型与去继承，强制单一待发送 Frame、Partial-write Poison，并让 Close 不等待阻塞 Writer。 | 当前 `desktop_speech.py` Queue Callback、Electron `speech-audio-channel.ts` Reader 与 `speech-delivery.ts` Parser；Electron 停止时须先 drain/关闭读端，原始音频不进入 NDJSON 或 React |
| `desktop_protocol/contracts.py` | Python 端 TypedDict、严格 Parser、Runtime Validator 和 Builder；除 Chat/STT/Speech 外，对 Knowledge Source/Operation/Export、changed/completed event、grounded statement/citation/location 和 `useProjectKnowledge` 做 exact validation，并拒绝路径、Hash、Vector、Prompt、Native Message 与扩展字段。 | `desktop_backend.py`、Schema/Fixtures、Python Tests |
| `desktop_protocol/__init__.py` | 汇出 NDJSON 协议、封闭 Speech Event 边界与私有音频通道的常量、类型、Parser、Builder 和 Writer。 | Desktop Backend、测试 |

协议的 Python 与 TypeScript Parser 都是手写的，Schema 不是代码生成器。因此修改协议时必须同步维护两端和共享 Fixtures。

## 24. Desktop 测试：`desktop/tests/`

| 文件 | 实际用途 | 主要连接 |
| --- | --- | --- |
| `desktop/tests/check-documentation.test.mjs` | 验证源码发现会排除精确的 `models/cache/`，同时继续扫描 `core/cache/` 等受维护目录。 | Documentation Checker、`npm run test:contract` |
| `desktop/tests/clean-build-output.test.mjs` | 在隔离临时 Desktop 根验证三个固定清理 scope 只删除各自 `dist` / `dist-electron`，保留相邻源码和另一类输出，并拒绝未知 scope；不会触碰真实构建目录。 | `scripts/clean-build-output.mjs`、npm Build Scripts、分发门禁 |
| `desktop/tests/backend-data-root.test.mjs` | 用一次性本地 Node 子进程验证显式 `ELYSIA_DATA_ROOT` 注入、ambient authority 删除，以及 tentative/authoritative restart 失败后的后续根选择。 | 编译后的 `backend-process.ts`；不启动 Python、Electron UI 或模型 |
| `desktop/tests/data-storage.test.mjs` | 覆盖 bootstrap/layout、legacy 只复制 Workspace、十类容量、link/junction/资源根/重叠拒绝、SHA staged move、pending 跨 Main 恢复与目的盘消失回滚、late known write 保留、先 finalise pointer、Recovery Copy 跨重启可发现、commit/rollback 和一次性三类 cleanup。 | 编译后的 Data Storage 模块；使用临时目录，不启动 Python 或 Renderer |
| `desktop/tests/application-shutdown-gate.test.mjs` | 覆盖首次/重复/最终 quit admission、清理失败后重试与非法状态转换，证明重复 Electron 退出事件不会越过仍在执行的私有清理。 | `application-shutdown-gate.ts` 编译产物；不启动 Electron |
| `desktop/tests/music-playback-owner.test.mjs` | 覆盖长音频播放的大小上限、所属窗口/Main Frame、一次性 Settlement、取消、替换、超时、导航/崩溃/关闭和 Listener 清理。 | `music-playback-owner.ts` 编译产物与 Electron Module Mock；不播放真实音频 |
| `desktop/tests/preload-music-playback.test.cjs` | 在隔离 Node 进程加载生产 Preload，验证私有 Music IPC、Blob/Media Element 播放、指定输出设备、取消与 Exactly-once Settlement；确认歌曲字节和 ID 不进入公开 Desktop API。 | `preload.cts` 单文件 Bundle、Fake Electron/DOM Media；不播放真实歌曲 |
| `desktop/tests/song-lyrics-provider.test.mjs` | 覆盖固定 LRCLIB Origin/GET/无凭据、查询编码、Abort/Timeout/Retry-After、重定向和响应预算拒绝、严格 JSON/字段，以及 title/artist/duration/sync 的确定性匹配和低置信度 Fail Closed。 | `song-lyrics-provider.ts` 编译产物；使用注入 HTTPS transport，不访问网络 |
| `desktop/tests/song-cover-lyrics-assets.test.mjs` | 覆盖受限 FFprobe、720 秒上限、成对 override、Provider Abort、同步歌词/manifest 的 no-follow 独占写入、部分失败清理和稳定错误映射。 | `song-cover-lyrics-assets.ts` 编译产物；使用临时目录和 Fake Provider/Probe |
| `desktop/tests/song-cover-manager.test.mjs` | 覆盖单 Job admission、两种引擎/来源、Lyrics asset handoff、Worker 进度/输出验证、取消/超时、私有 cleanup-only、播放/导出、关闭收敛和 Renderer-safe 错误/状态。 | `song-cover-manager.ts` 编译产物；使用 Fake Child/Playback/Picker，不加载真实模型 |
| `desktop/tests/protocol.contract.test.mjs` | 在 Node 中测试编译后的 Protocol Helpers 和 BackendProcess；覆盖双端 Fixture、有界 NDJSON、Knowledge method/result/event、grounded history、全局 lifecycle/export admission、export snapshot/receipt/terminal/error race、STT/Speech correlation、Chat response 早于最终 Speech terminal 的 busy 边界与 capability/fd3 生命周期，并验证开发/打包环境只接受精确的单一主 Renderer 入口、Browser Notification permission 仍不向 Renderer 开放。 | `dist-electron`、Schema/Fixtures；使用 Fake Child 与一次性本地 Node Child，不启动真实 Python |
| `desktop/tests/desktop-pet-lifecycle.test.mjs` | 覆盖可选退出工作的成功/失败/超时、独立慢写入不会饿死最终程序停止、扫描/Settings/Tray 意图在成功或拒绝后的统一 admission 顺序、程序停止先于失效选择持久化、`visible + absent/failed` 显式重试、Hidden/Visible 托盘驻留、Disabled 退出及待处理模式写入延迟退出。 | `desktop-pet-lifecycle.ts` 编译产物；不启动 Electron、外部程序或真实文件写入 |
| `desktop/tests/desktop-pet-directory-picker.test.mjs` | 覆盖已保存目录优先、开发机通用本地角色数据父目录、打包版本禁止探测源码目录、断盘/探测失败回退、取消，以及中文/空格绝对路径与非单一/相对选择拒绝。 | `desktop-pet-directory-picker.ts` 编译产物；不打开真实系统对话框 |
| `desktop/tests/desktop-pet-preferences.test.mjs` | 覆盖桌宠 update exact schema、缺失文件默认 Off、Schema v1/v2→v3 迁移时丢弃退役 placement、私有程序目录、无路径公开状态、坏/超限文件 Fail Closed、目录与 opaque 程序选择的 revision CAS、并发和原子替换。 | `desktop-pet-contracts.ts`、`desktop-pet-preferences.ts` 编译产物；不启动 Electron、React、Python 或外部程序 |
| `desktop/tests/desktop-pet-program-library.test.mjs` | 以合成临时目录覆盖空库、精确 `A<目录名>.exe`、受信 Hash 拒绝、`config.json` 结构/大小、三种输入 Profile 引用闭包、额外顶层可执行代码、候选数、相对/不可用根和 Symlink；同时确认公开摘要/lookup 不泄露路径。受环境变量显式启用的本机集成样本只验证六套已购程序均可识别且不执行；启动前复核测试会复制一套受信程序、在复核前替换 `BongoCatUI.exe` 并确认拒绝，覆盖陈旧 Scan 拒绝但不声称封闭最终按路径启动的 TOCTOU。 | `desktop-pet-program-library.ts` 编译产物；默认不读取项目所有者的付费目录，任何测试都不执行伴侣程序 |
| `desktop/tests/desktop-pet-program-manager.test.mjs` | 用 Fake Child 验证精确 EXE/工作目录/空参数和最小环境启动、旧 owned child 先停再切换、仅按精确 PID/进程树停止、停止失败或超时阻止替换、迟到/过早退出事件隔离、启动失败净化、Disabled/空选择不启动，以及 shutdown 后永久拒绝新启动。 | `desktop-pet-program-manager.ts` 编译产物；不启动真实桌宠程序、不读写其 `config.json` |
| `desktop/tests/presence-native-notification.test.mjs` | 覆盖 Completion 优先的单一原生槽、Windows timeout 后仍可撤回、替换/关闭、迟到 click/failed 隔离、show failure 净化和用户取消释放。 | `presence-native-notification.ts` 编译产物；使用纯 Fake Handle，不创建真实系统通知 |
| `desktop/tests/presence-notification-preferences.test.mjs` | 覆盖 Presence 更新 exact schema、默认全 Off、坏/超限文件 Fail Closed、公开 state 不含私有 reminder anchor、revision CAS/并发、原子替换失败、cadence 重锚、旧 anchor CAS 与私有 handled marker；纯策略同时覆盖 daily/weekly 计时、时钟回退、Reply opt-in/注意力和 Reminder hidden/idle/busy/unsupported/shutdown 条件。 | Contracts/Policy/Preferences 三个编译产物；不启动 Electron Notification、React、Python 或后台服务 |
| `desktop/tests/voice-session-controller.test.mjs` | 覆盖五状态、显式确认、Chat/播放终态任意顺序、无 Speech Capability、Terminal-before-ACK、取消/Hang-up、跨 Chat/Project、迟到与乱序事件、一次性安全续听，以及 200 轮 Speech/Text 交替后零异步 Owner 的 Soak。 | 纯 Controller 测试，不启动 Electron、Python、模型或真实音频 |
| `desktop/tests/voice-ui-state.test.mjs` | 覆盖主回复生命周期与被动麦克风监控的优先级、监控失败、Confirmed Barge-in、取消/转写错误、静音、人工 Review 状态和有界 Session 时钟格式。 | `voice-ui-state.ts` 的纯状态测试，不启动 React、Electron、麦克风或模型 |
| `desktop/tests/character-state.test.mjs` | 覆盖全部闭集状态、确定性优先级、Backend/Chat/Knowledge facts、保留的 work-mode/approval 输入与 Voice 映射。 | `character-state.ts` 纯状态测试；不启动 React、Electron 或模型 |
| `desktop/tests/character-presentation.test.mjs` | 覆盖七态到前七个唯一状态格、十值闭集情绪到审核表情格、未知情绪回退、success 排除，以及应用内 CharacterArtwork 永远标记静态且不公开嘴型动画。 | `character-emotion.ts`、`character-presentation.ts`、`CharacterArtwork.tsx`；不启动 React、Electron 或模型 |
| `desktop/tests/speech-audio-channel.test.mjs` | 直接测试 Electron 二进制音频 Reader 与 Delivery Coordinator；覆盖每个分片边界、Coalesced Frame、ACK/Discard 背压、EOF 截断/干净关闭、长度先验、Header/Token/Hash、Canonical WAV、Metadata 任意到达顺序、FIFO、失败跳过、取消/迟到 Settlement、Terminal 计数、terminal 已到但最终播放未排空时仍 busy、播放器断连、错误脱敏和 Listener 清理。 | `speech-audio-channel.ts` 与 `speech-delivery.ts` 编译产物；使用内存 Pipe 和 Fake Playback，不启动 Python、Electron UI 或真实模型 |
| `desktop/tests/preload-speech-playback.test.cjs` | 在隔离 Node 进程中加载生产 Preload，先验证沙箱产物只保留 `electron` External 且不含相对运行时 `require`，再验证连续 Clip 复用单一输出图、每句重新应用 Sink/音量、失败后等待关闭并重建、取消 Exactly-once，以及迟到 `setSinkId` 不能修改替换 Clip。指定设备路由失败不回退，无效音量 Fail Closed，播放链不创建或采样嘴型 Analyser。 | `preload.cts` 单文件 Bundle、Fake Electron IPC 与 Fake Web Audio |
| `desktop/tests/speech-playback-owner.test.mjs` | 直接验证 Main 所有的私有 Playback Owner；覆盖一次性 Settlement、所属 Main Frame、取消迟到回复、窗口替换、Renderer 崩溃、跨文档导航、空闲 Owner 退役、Listener 清理，以及超时/坏回执/Retired-ID 上限后换用新 Owner 并隔离迟到旧回执。 | `speech-playback-owner.ts` 编译产物与 Electron Module Mock |
| `desktop/tests/ui/electron-main.cjs` | Playwright 专用 Electron Main；加载生产 Renderer Build，保持 Sandbox/Context Isolation，但不启动生产 Backend。 | UI Test、Mock Preload、`dist/index.html` |
| `desktop/tests/ui/mock-preload.cjs` | UI 测试专用 `elysiaDesktop` Fake；除 Chat/Project/Voice/Knowledge/Presence 外，还模拟 revisioned Data Storage 容量、移动、一次性临时清理，以及无路径的桌宠程序库状态、目录选择、重扫和 opaque 程序选择。 | App Shell UI Tests；不会进入生产包，也不会创建真实系统通知、移动文件、读取本机桌宠目录或启动外部程序 |
| `desktop/tests/ui/app-shell.spec.ts` | Playwright 启动真实 Electron 主 Renderer，覆盖 Chat/Project/Settings/Voice/Knowledge、Song Cover、桌宠、Presence 与 Data & Storage；Song Cover 用例验证音符入口、默认 Lyrics-driven、LRCLIB 披露、Legacy/stems/移调、受控 title 输入不会跳焦、状态/进度/播放/导出/取消和互斥音频行为。其余用例验证主/Voice 状态板恒定静态、桌宠选择/Visible/Hidden/Off，以及十类容量、IPC 参数、warning、Backend offline 与 busy 禁用。 | Production React Build + Mock Backend；不替代真实 LRCLIB、SoulX/Legacy 推理、原生文件对话框、桌宠程序或设备矩阵 |

## 25. Python 测试：`tests/`

| 文件 | 实际用途 |
| --- | --- |
| `tests/__init__.py` | 将测试目录标记为 Python Package。 |
| `tests/test_active_conversation_integration.py` | Brain 的多 Chat 上下文、Scoped Memory 和失败隔离。 |
| `tests/test_active_conversation_service.py` | Busy Token、快照、Turn/Summary Commit、Retry 和 Chat actions。 |
| `tests/test_attachment_service.py` | Attachment Store 生命周期、Manifest、锁、恢复和文件系统安全。 |
| `tests/test_attachment_file_domain.py` | 版本化 Original/Ownership/Derived Domain 值、内容稳定 File ID、UTC 时间、安全名称、Scope/Role 对应和路径字段禁入。 |
| `tests/test_attachment_application_service.py` | AttachmentService 的 Repository Delegation、Scope-bound Verified Read、Derived 登记以及 Chat/Project 多 Owner 删除回滚边界。 |
| `tests/test_document_domain.py` | Document Source、Title、Block/Table Union、PDF Page、连续 Ordinal，以及 Source/Expansion/Text/Structure 全部累计预算。 |
| `tests/test_document_loader_service.py` | Document Service 的真实 Attachment Store 集成、Scope 隔离、Link-specific Metadata、Verified Read、Route 冲突、Size Preflight 与恶意 Adapter 输出拒绝。 |
| `tests/test_document_text_loaders.py` | TXT/Markdown/CSV/源码的严格 Encoding、Unicode 行边界、多行 Setext、结构保留、Route、Malformed Input、预算和峰值内存边界。 |
| `tests/test_document_binary_loaders.py` | PDF/DOCX 的标题/页码/正文/表格、Null Content、重复/嵌套 Form、Form 错误提升、CMap/CID/Type3 字体语义预算、映射前文字早停、Inline Image、EOCD/Zip64、MCE、分部 Namespace Profile、Relationship + Content Type Part 授权、固定路径诱饵与 Opaque Drawing/Math，以及加密、损坏、不支持能力、外部关系、路径别名和资源预算边界。 |
| `tests/test_document_cleaning.py` | Cleaner 对 CRLF/Unicode/代码空白/Ragged Table 的逐值保留、严格 PDF 页眉 All-or-nothing 证据、准确 Span/Omission、Scope/Loader/Policy Fingerprint 失效与 Processing Budget。 |
| `tests/test_document_chunking.py` | Prose/Heading/Page/Code 边界、Table JSONL Escape、Chunk-local 到 Block/Cell Offset 映射、确定性 ID/Version 失效、Mapping/Chunk/总输出预算与稳定错误。 |
| `tests/test_document_processing_pipeline.py` | 默认 Load → Clean → Chunk 组合，以及恶意 Source Loader/Cleaner/Chunker 对 Ownership、Piece-table、Fingerprint、Lineage、Mapping/Projection 的篡改拒绝、资源错误保留和未知异常脱敏。 |
| `tests/test_document_embedding.py` | Embedding Model/Space/Template/Batch 身份、Document/Query Input、恶意 Adapter 返回、Chunk Lineage、Identity-defined 维度与单位向量、Float32-le Checksum、确定性 ID 和预算。 |
| `tests/test_ollama_embedding.py` | Ollama Loopback URL、代理/Redirect/Retry 禁止、Batch 前后固定 Full Manifest Digest 核对、精确 Content Type、Duplicate JSON Member/UTF-8/Non-finite 拒绝、Raw Body Byte/Wall-clock Deadline、向量数量/维度和脱敏错误。 |
| `tests/test_document_vector_store.py` | SQLite Add/Update/List/Get/Delete/Rebuild、精确 Chat/Project Scope 隔离、原子替换/回滚、Stale/Space 拒绝、精确 DDL/Constraint/Trigger/View/Index 与 JSON/BLOB/Checksum 损坏、资源预算。 |
| `tests/test_document_indexing.py` | Processing → Embedding → Store 同步组合的 Scope/Link/Lineage 传递、替换/重建语义、失败保留与错误边界。 |
| `tests/test_document_retrieval.py` | 真实 SQLite 单事务暴力 Cosine Top-K、Threshold、精确 Scope/Generation Allowlist、闭集 Metadata Filter、Missing/Stale 全搜索失败、准确 `(kind, text)` 去重与多来源 Evidence、稳定 Tie、空 Corpus 短路、可选 Reranker Fail-closed、Embedding Space Mismatch 和 Path/Vector 隐私。 |
| `tests/test_document_grounding.py` | Retrieve→Generate 单次所有权、空证据零模型调用、完整排名前缀、三类 Statement、PDF/Text/Table Location、多 Evidence、Prompt Injection Data 隔离、严格 JSON/Fingerprint/Citation 闭包、变异 Adapter、预算和异常脱敏；并验证 structured source preference 只重排已相关 Hit、style guidance 保持为不可信 Data。 |
| `tests/test_ollama_grounding.py` | 生产 Grounded Generator 的 loopback Origin、生成前后 Manifest Digest 固定、闭合 deterministic policy、截断/隐藏思考拒绝、Raw Body 预算、Duplicate Tag 与 transport 脱敏。 |
| `tests/test_real_document_regression.py` | 从提交的真实 PDF/DOCX container bytes 经生产 Attachment/Loader/Cleaner/Chunker 进入同 Project Retrieval；复核结构、路径私有性与固定 SHA-256，模型环节使用确定性 test adapter。 |
| `tests/fixtures/documents/README.md` 与 PDF/DOCX fixtures | 记录项目贡献者自制的 CC0 回归内容、生成属性和 SHA-256；二进制 fixtures 是测试输入，不是用户资料或生产模型资产。 |
| `tests/test_file_metadata_store.py` | Manifest v2、v1 Migration、Scope-local 去重、路径隐私、Derived 级联、取消清理、跨 Scope 删除、Verified Read 完整性和未知 Schema Fail-closed。 |
| `tests/test_brain.py` | Brain 的 Chat、Canonical Streaming、跨 Chunk 空白、Memory、Summary、Retry、Cancel 和 Attachment 协调；另验证 grounded answer 经 generation commit gate 把文本与结构化 proof 一起保存。 |
| `tests/test_chat_domain.py` | Chat、Message、Summary、Attachment Metadata、ID 和不变量。 |
| `tests/test_chat_repository.py` | Index/Detail、CRUD、原子失败、重启和 Index Recovery；包含 Statement/Citation/Location 的 grounded proof 能原样跨 Repository 重启恢复。 |
| `tests/test_console.py` | Console Commands、Streaming 和 Session 行为。 |
| `tests/test_conversation_summarization.py` | Model Summarizer、严格结构和增量摘要。 |
| `tests/test_conversation_summary.py` | Stage 4 旧 Summary Schema 与存储。 |
| `tests/test_data_portability.py` | Bundle Export/Import、Hash、路径、Conflict、Quarantine 和 Rollback。 |
| `tests/test_distribution_assets.py` | 覆盖 Git Force-add、Case/NFKC Alias、模型/音频/Archive、同步歌词 `.lrc`/manifest/plain-text 产物、改名后独立付费纹理、准确视觉路径白名单、完整 Builder Shape、继承/Hook/App Root/Platform Files 逃逸、图标及立绘/状态/表情/嘴型图集的仓库/ASAR 字节固定、退役桌宠 HTML/专用 Preload/Renderer/Runtime 构建残留的零存在拒绝，以及 Unpacked/ASAR 与当前真实仓库。 |
| `tests/test_desktop_backend.py` | Python Bridge 的 Handshake、Routing、Streaming、Cancel、Chat/Project/Settings/Attachment、Knowledge、STT 与可选 Speech 集成；覆盖 grounded routing/persistence、Knowledge worker/admission、Project mutation race、durable cancel ACK、explicit recovery、非阻塞 export cleanup、typed error mapping 与 shutdown/runtime ownership，并继续证明 Speech 失败不会改变文字终态或持久化回复。 |
| `tests/test_desktop_knowledge.py` | 生产知识 Factory 共享 Chat/Project/Source authority、operation lease 和持久 Store；复核构造阶段零 HTTP/零索引、profile 与路径无关但绑定 route/chunking 合同，并拒绝远程 Ollama Origin。 |
| `tests/test_desktop_audio_channel.py` | 验证 fd3 固定所有权、OS Pipe 类型与去继承、84-byte Header、Token/Digest、无歧义桌面 PCM WAV、8 MiB/120 秒上限、Partial Write、单待发 Frame、反射篡改、Poison、非阻塞 Close 和错误脱敏。 |
| `tests/test_desktop_protocol.py` | Python Protocol Parser/Builder 与共享 Fixture Contract；覆盖 Knowledge method/result/event、grounded history/use intent、Schema v4 Voice 行为设置（含情绪闭集）、STT 设置/状态的 exact shape 与脱敏边界。 |
| `tests/test_desktop_speech_protocol.py` | 验证 Speech Clip/Failure/Terminal Event Builder、二进制 Metadata 上下界、固定失败码、终态计数关系、Request 关联、未知 Event/私有字段拒绝，以及 Schema 与 Runtime 常量一致。 |
| `tests/test_desktop_speech.py` | 用真实 Sentence Queue 与 AudioChannelWriter、Fake Managed Runtime 验证 Active `voiceProfileId`/`voiceEmotion`/`speechRatePercent` 的配置、解析、闭集情绪选择和绝对 Rate 映射；还覆盖启动前超过旧 4096 字缓冲的完整回复、超过 Sentence FIFO 总容量的快速长回复、Spool/Feeder 背压、自然分句/FIFO、Metadata 先于 Binary、普通取消/替换不 Abort 且后续 Turn 仍可播放、自发 Worker Poison 才关闭语音、Bootstrap 失败隔离、Terminal Exactly-once 和退出清理。 |
| `tests/test_desktop_settings.py` | Desktop Settings 十六字段 Validation、Schema v1/v2/v3 到 v4 Migration、Voice 行为字段的 Live/Restart 分类、Desired/Active Restart Diff、Revision CAS、锁和 Quarantine。 |
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
| `tests/test_production_data_layout.py` | 精确数据树、环境根校验，以及 Chat/Project/Memory/Settings/Attachment/Knowledge/Speech 各 Composition Root 的资源/数据分离。 |
| `tests/test_project_sources.py` | Project Source catalog 跨线程实例/真实 spawn process CAS、tombstone、Instructions fingerprint 与持久化；同 Project 多 Chat 共享、跨 Project/Archived Owner 拒绝、Chat Attachment 隔离与 canonical-history committed-only promotion、相同 bytes/不同 metadata 冲突、文档路由过滤、完整 current-profile corpus、Instructions 子集约束和生成后 authority revalidation。 |
| `tests/test_knowledge_lifecycle.py` | Knowledge journal strict CAS、同 Project 单 recoverable operation、terminal retention、phase/state 单调性、add/reindex/rebuild 发布顺序、copy-on-write replace、tombstone-first delete、whole-Project revoke、故障恢复、跨 Project 隔离、source views 与 verified original export。 |
| `tests/test_knowledge_export_recovery.py` | Export durable cleanup-intent recovery：正常遗留/missing temp 收敛、损坏与 temp/parent identity mismatch Fail Closed、多 intent 隔离清理、发布前最终 identity guard，以及 publish 后 private intent 延迟清理的一致终态。 |
| `tests/test_chinese_localization.py` | OpenCC 上下文词组、回复动作提示过滤、代码/URL/路径保护、跨 Chunk 规范化与 Stream 生命周期。 |
| `tests/test_prompts.py` | 外部 Elysia Prompt 的缺失/空白/编码/大小/保留标记 Fail-Closed、进程缓存、人格规则和 JSON 数据边界。 |
| `tests/test_python_documentation_check.py` | 文档扫描只排除精确的 `models/cache/`，不会把其他同名源码目录误排。 |
| `tests/test_scoped_memory_integration.py` | 跨 Chat/Project Memory 隔离和同 Key 覆盖。 |
| `tests/test_settings.py` | `.env`、STT 与语音情绪闭集配置/安全回退、GPT-SoVITS 本地评估 Opt-in、请求/探测 Timeout、Seed、默认值和基础 AppSettings。 |
| `tests/test_short_term_memory.py` | Token Budget 和完整 Turn 淘汰。 |
| `tests/test_song_cover_worker.py` | Legacy RVC Worker 的完整歌曲/stems、音频预检、Demucs 分离、RVC 资产身份/子进程调用、40→44.1 kHz 精确样本对齐、不混回原唱辅音的混音、进度、取消、原子发布、资源预算与错误脱敏；全部以 Fake Tool/合成短音频运行，不加载私人模型。 |
| `tests/test_song_rvc_runtime.py` | 封闭 RVC Adapter 的命令面、路径/资产边界、CUDA 要求、CUDA Graph 禁用与环境恢复、网络封锁、Vendor Import 来源、固定推理参数、int16 PCM 规范化、浮点范围及全零/持续满幅拒绝、临时文件清理和原子发布；使用 Fake Vendor API，不加载 CUDA 模型。基础 CI 不安装私有 RVC Runtime 的 NumPy，因此只跳过依赖真实 Array Dtype 的 PCM 个案，其余边界仍完整运行；具备本机 RVC 依赖时会执行全部 PCM 个案。 |
| `tests/test_smoke_song_cover.py` | Song Cover Smoke CLI 的参数边界、四输入预检、Hash/格式/时长复核、固定四字段 Manifest、闭集 Worker 帧、WAV/MP3 与 WSL 清理验证、默认删除、可选保留和诊断脱敏；全程使用 Fake Worker/合成短音频，不加载私人模型。 |
| `tests/test_song_lyrics_alignment.py` | LRC 解析、严格普通话 Han 约束、官方 G2P seam、音符/音节容量、等时证据歧义、跨 Segment 单调拆分、每 Token 恰好一次，以及无重叠歌词不得静默丢失。 |
| `tests/test_song_svs_runtime.py` | WSL SoulX Runtime 的固定目录/资产、阶段参数、总墙钟、精确 session lease、取消标记、TERM→KILL 回收、输出验证和清理；使用 Fake Stage Process，不加载 CUDA 模型。 |
| `tests/test_song_svs_wsl_bridge.py` | Windows↔WSL 路径转换、0700 私有 Job、固定输入复制、桥接参数、取消/超时/cleanup-only lease 收敛、输出回传与 Linux Job 删除；使用 Fake WSL Command，不访问真实发行资产。 |
| `tests/test_song_svs_worker.py` | Lyrics-driven Windows Worker 的 sources/manifest、桥接生命周期、进度、混音/输出、取消/失败清理、预算与错误码；使用 Fake Bridge/Tool，不加载 SoulX。 |
| `tests/test_smoke_gpt_sovits.py` | Smoke CLI 的固定文本、重复/多情绪、缓冲成功输出、格式摘要、闭集错误码和独立 data root 传递。 |
| `tests/test_gpt_sovits_protocol.py` | 受管 TTS 私有 Pipe 的二进制帧、Canonical Metadata、长度先验、Partial I/O、截断/坏帧脱敏、不可变性与 Python 3.9 语法兼容。 |
| `tests/test_gpt_sovits_worker.py` | 用 Fake Engine 验证受管 Worker 的 INIT/READY/SYNTHESIZE/STOP 状态机、Challenge/单调 ID、稳定 Worker/Protocol/Runtime/资产路径与 Manifest 重算、上游 Config Fallback、Reference 复用、静音 Text Stream 的 UTF-8 加固、全零 Sentinel、不恢复热重载、单 Yield PCM WAV、坏 Pipe Poison、错误脱敏和 Python 3.9 兼容；不加载真实模型。 |
| `tests/test_managed_gpt_sovits.py` | 用 Fake Transport/Process/Guard 验证 Parent 的严格 Config/Catalog 来源、Volume-GUID 路径布局、双重 Manifest/Binding、READY/AUDIO/STOP、64 位 Token、提前取消 Tombstone、Active Abort、并发 Close、超时/协议失败 Poison、Process-first 阻塞 Pipe 唤醒、Owner/Thread 构造失败、Process+Transport+Guard 精确隔离、Bootstrap 部分创建回滚及封印前目录写句柄检测；不启动真实模型。 |
| `tests/test_windows_file_guard.py` | 在 Windows 验证声明核验、目录/叶文件共享锁、Reparse/Hard-link/Case/8.3/SUBST/UNC 拒绝、盘符映射 ABA 检测、Volume-GUID 稳定重开、严格私有 Accessor、并发关闭，以及 Snapshot/类型/Hash/构造/关闭异常下的 HANDLE 回滚与延迟所有权；不加载真实模型。 |
| `tests/test_windows_managed_process.py` | 在 Windows 真正启动隔离 Python 子进程，验证 Argument Quoting、封闭环境、HANDLE Allowlist、Suspended→Job→Resume、`terminate()` 返回前 Job 已无活动根/孙进程、外部 Process Object 的有界回收、Job Accounting/等待/关闭故障的所有权重试、UTF-16 上限、幂等生命周期和秘密脱敏；不加载真实语音模型。 |
| `tests/test_speech_queue.py` | 验证流式自然分句、纯装饰符过滤、文字/数字旁符号保留、FIFO、有界 Work/Delivery/Cache 记账、超容量 Waiting Feed 逐批排空、Cancel/Shutdown 唤醒等待者、逻辑 Discard 不 Abort 受管 Worker 且替换 Turn 可继续、失败跳过、取消 Callback Boundary、每句唯一 Operation Token、提前取消 Tombstone 契约、固定 Abort Dispatcher、Shutdown Deadline、迟到音频丢弃、秘密脱敏，以及 256 轮完成/取消后零 Queue/Delivery Owner；使用 Fake Runtime，不加载真实模型。 |
| `tests/test_stage5_acceptance.py` | Stage 5 端到端验收：多 Project/Chat、Memory 隔离、重启和完整 Export/Import。 |
| `tests/test_start.py` | Composition Root、Migration 和配置限制。 |
| `tests/test_synthesis_service.py` | 惰性构造、可替换 Weight 资源根与可移动 Catalog 数据根分离、Catalog 重载、Profile/情绪映射、权利 Opt-in 与离线错误。 |
| `tests/test_voice_capture.py` | Python 单句 PCM Capture Contract、Canonical Base64、Markers、边界和收据。 |
| `tests/test_voice_settings.py` | Audio Device Preferences、CAS、锁和损坏恢复。 |
| `tests/test_voice_profiles.py` | Voice Profile Schema、路径固定、准确 Prompt/语言、情绪选择与权利状态。 |
| `tests/test_voice_synthesis.py` | 引擎无关 TTS 请求/结果、不可变性、不可发声 Unicode、音频上限、PCM WAV/Ogg Opus/ADTS AAC Framing 与错误层级。 |
| `tests/test_voice_transcription.py` | 引擎无关的转写请求、最终结果、语言、置信度、不可变性和错误层级。 |
| `tests/test_voice_pipeline_benchmark.py` | 以 Fake Monitor/Pipeline/HTTP 验证三组件并发顺序、脱敏 JSON、WAV→CPU Capture、失败清理、Loopback、硬 Deadline、固定分块 NDJSON、未终止帧和 `/api/ps` 大小边界；不加载真实模型。 |
| `tests/test_voice_transcription_jobs.py` | 有界后台转写的 Admission、Worker/Queue Capacity、Cancel/Timeout Race、Native Draining、迟到结果丢弃、Final Transcript 强制简体、Retention、Shutdown，以及 256 轮成功/失败后零 Job/Worker Owner。 |

## 26. Voice Session、Capture、本地 STT 与本地 TTS

当前桌面已把显式单句采集、Final STT、人工确认、Canonical Chat、受管 TTS、可信播放状态、安全 Barge-in 和可选自动续听组合为一个有界 Voice Session。它不是第二套对话系统：Transcript 只有在用户点击 **Send transcript** 后才进入现有 Chat，之后的 Brain、Persistence、Summary、Memory 和回复播放都沿用正常路径。尚未实现的是 Partial Transcript 与自动提交；Python 256 轮与 Renderer 200 轮 Soak 已验证程序内 Owner 清理，但真实设备、房间回声和多小时人类通话仍未声称完成。

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
| `config/settings.py` | 定义 STT 模型、设备、语言和语音情绪的闭集默认值与 `.env` Bootstrap。 | 非法别名回退，不能触发下载或任意情绪指令 |
| `config/desktop_settings.py` | 持久化 STT 与八项 Voice 行为的 Desired/Active 值并计算 Restart Diff。 | Profile/Rate/Emotion 与 STT Runtime 设置需重启；自动朗读、音量、字幕、人工 Review 和自动续听实时生效；初始化前修复会被首次 Runtime 组合采用 |
| `voice/transcription.py` | 定义不依赖具体引擎的请求、Final Result、语言和稳定错误。 | 只允许有界最终文本 |
| `voice/faster_whisper.py` | 从明确本地目录加载、选择设备/Compute、执行转写并生成净化 Status/Error。 | 不下载模型，不返回路径或 Native Error |
| `voice/transcription_jobs.py` | 固定 Worker/Queue、Deadline、Cancel、唯一终态、Native Draining 和迟到结果丢弃。 | 逻辑取消后仍保留物理容量直到 Native Call 返回 |
| `desktop_backend.py` | 从 Active Settings 组装 Adapter/Runner，执行 Admission、Chat/Settings 互斥和 Protocol 终态。 | Python 是 Native Draining 的最终 Busy Gate |

可选模型名称是 `tiny`、`base`、`small`、`medium`、`large-v3`、`turbo`；每个名称只映射到 `models/weights/faster-whisper/<model>`。设备是 `auto`、`cuda`、`cpu`，默认设备为给 Ollama/TTS 保留 GPU 容量的 `cpu`；默认语言是 `auto`，也可选 `zh`、`en`。缺模型、缺依赖、Runtime Probe、CUDA 或初始化失败只形成闭集 Reason，不允许把模型路径、异常消息或 Native 对象穿过协议。

### Electron、Renderer 与 Voice Session Handoff

| 文件 | 当前职责 | 关键边界 |
| --- | --- | --- |
| `desktop/electron/protocol.ts` | 严格解析 Schema v4 Voice 行为设置（含十值 Voice Emotion 闭集）、STT Readiness、一次性 PCM 请求和 PCM-free Final Result。 | 拒绝 Extra/Path/Message/Native 字段、不一致状态、越界行为值和任意动画选择器 |
| `desktop/electron/backend-process.ts` | 关联 Request/Session/Chat，处理 Progress、Cancel Race 和终态，并把 Delivery Coordinator 的闭集播放状态转成 Renderer-safe Event。 | Pending Metadata 不保留 PCM；语音失效时移除 `voice.speech`，文字 Chat 继续 |
| `desktop/electron/main.ts` | 校验 Renderer 的 Voice 行为/STT 参数、Cancel Request ID 与播放停止 Request ID，映射固定 IPC。 | 不接受任意 Channel 或任意设备、模型、Profile/Rate/Volume 值 |
| `desktop/electron/preload.cts` | 暴露 `beginVoiceTranscription` / `stopVoiceTranscription` / `stopSpeechPlayback` 与安全 Event Subscription；私有播放 Owner 在连续 Clip 间复用 AudioContext/GainNode，并按每个 Clip 重新应用 Active Volume 和 Sink。应用内角色固定静态，播放链不创建或采样用于嘴型的 Analyser。 | 私有 WAV IPC、波形样本与原始 `ipcRenderer` 不暴露给 React；无效音量或指定 Sink 失败时不播放，初始化期取消会隔离旧图 |
| `desktop/electron/contracts.ts` | 声明 Renderer 可见的 Voice 行为设置、开始确认、Final/Error，以及只含 Request/Chat、闭集 Kind、Sequence 或 Terminal State 的安全播放状态。 | 不包含 PCM、Token、Hash、文本、路径或 Native Diagnostic |
| `desktop/src/voice/voice-session-controller.ts` | 管理 `IDLE → LISTENING → TRANSCRIBING → THINKING → SPEAKING → IDLE`，以 exact owner 接受异步事件，并用单调完成 ID 保护一次性续听。 | 只持有有界 ID、Final Transcript 与安全终态，不拥有 PCM、Chat 持久化或播放器 |
| `desktop/src/voice/voice-ui-state.ts` | 分离推导主回复生命周期与麦克风/中断监控状态，并格式化 Session 计时。 | 纯展示状态，不拥有捕获、转写、Chat 或播放资源；主生命周期作为 Character State 输入 |
| `desktop/src/App.tsx` | 把 Capture/STT、Controller、Canonical Chat Send、播放状态、字幕、静音、Reply-time Interruption、安全自动续听与当前 Chat/Project-scoped Character State 关联；Final Transcript 可显式 Send 或进入草稿。 | Voice 打开时固定 Chat/Project；迟到、跨上下文或乱序事件不能污染当前 Session；自动续听不自动发送 Transcript |
| `desktop/src/voice/CallPreview.tsx` | 显示主/麦克风双状态、计时、Assistant Captions 和可编辑 Final Transcript；提供 Send、Use/Append、Mute、Audio Settings、Auto-continue 与 Close Voice，并把主生命周期、Backend failure 与 Active Voice Emotion 交给共享的静态 Character Artwork。 | 复用状态/情绪驱动 CharacterArtwork；不显示实时 Partial、不自动提交、不渲染音频嘴型；自动续听必须显式启用并由 App/Controller 放行 |
| `desktop/src/voice/transcription-readiness.ts` | 把闭集 Status/Reason 转成一致的恢复步骤。 | UI 不渲染底层路径或错误原文 |
| `desktop/src/settings/SettingsView.tsx` | 编辑 STT 与八项 Voice 行为设置，包括十值闭集 Voice Emotion，并显示 Active 值、Readiness 和 Restart 提示。 | 保存值与当前生效值明确分离；Rate、Profile、Emotion 重启生效，设备 ID 仍属于独立 Voice Settings Store |

完整连接关系：

```text
open Voice
  → bind exact chat_id + optional project_id + epoch
  → IDLE
explicit Start microphone
  → LISTENING
  → getUserMedia
  → audio-capture.ts
  → voice-activity-detector.ts
  → TRANSCRIBING
  → beginVoiceTranscription (Preload / Electron)
  → voice.transcription.start
  → desktop_backend.py admission / mutual exclusion
  → TranscriptionJobRunner
  → FasterWhisperTranscriber
  → correlated, bounded PCM-free final result
  → editable CallPreview transcript
  ├─ explicit Use / Append
  │    → current Chat's durable Composer draft only
  └─ explicit Send transcript
       → existing durable Chat send path
       → THINKING
       → Brain / persistence / Summary / scoped Memory
       → streamed Chat reply
       ├─ text-only or unavailable speech
       │    → IDLE after Chat terminal
       └─ trusted playing status
            → SPEAKING
            → wait for both Chat terminal and speech terminal
            → IDLE
```

每份 PCM 只跨协议一次，不进入 Chat、Memory 或长期文件。Final Transcript 停留在 `TRANSCRIBING` 等待人工检查；Capture/STT 本身不会创建 Chat Turn。直接 Send 与文字 Composer 复用同一个 Durable Pending Send、Backend Request、Brain 和持久化路径，但不会消费已有 Composer Draft，也不会把暂存 Attachment 附加到这次 Voice Turn。

Controller 以 epoch、Chat ID、可选 Project ID、Capture/STT ID、Chat Operation/Request ID 和 Speech Sequence 做 fail-closed 关联。用户取消、关闭 Voice、导航或切换 Chat/Project 后，迟到结果不会回填草稿或改变新 Session。Chat 终态与 Speech 终态可以任意先后到达；只有两侧都排空才回到 `IDLE`。若 `voice.speech` 未协商或运行中失效，Controller 把可选播放视为已排空，文字回复仍正常结束。

Cancel 或 Timeout 只结束用户可见的 STT 任务；Python 无法安全终止正在 Native Library 内运行的线程，因此 Runner 会继续占用物理容量直到调用返回并丢弃迟到结果。在排空期间，新 STT、Chat 与冲突配置写入会收到 Busy。当前 STT 桌面协议仍只传递 Final Transcript，不提供实时 Partial Transcript，也不会自动提交。首次 Capture 必须由用户显式开始；回复期间只有经过验证的 Echo Cancellation 才允许 Barge-in Monitoring，正常回复后也只有显式开启自动续听且所有安全终态成功时才开始下一次有界 Capture。Fake Runtime 自动化覆盖设备选择、降级和竞态，另有一次真实 CPU STT Runtime/模型 Smoke 验证；这里不声称 STT CUDA 或完整音频设备矩阵已通过实机验证。

### GPT-SoVITS 单次合成与受管桌面播放

| 文件 | 当前职责 | 关键边界 |
| --- | --- | --- |
| `config/settings.py` | 默认关闭本地评估，并限制 HTTP 请求/探测超时与 Seed。 | 环境值不提供任意 URL 或路径 |
| `workspace/settings/voice-profiles.json` | 本机 Schema v2 Catalog：声明 Base URL、权重、参考片段、准确文本/语言、速度、格式、权利状态，以及每项资产的实际长度与 SHA-256。 | 被 Git 忽略；Windows 路径也使用 `/` 分隔的相对路径；旧版字符串路径不会自动降级接受 |
| `voice/profiles.py` | 将逻辑 Profile/情绪解析为固定根下的不可变资产声明与外部 Adapter 配置。 | 拒绝逃逸路径、Windows 别名、矛盾身份、远端 URL、未知字段与未获 Opt-in 的本地评估素材；解析 Catalog 不等同于验证文件 |
| `voice/synthesis_service.py` | 惰性加载 Catalog 并创建一次 Adapter 调用。 | 构造服务不访问磁盘或网络；文字 Chat 不依赖 TTS 在线 |
| `voice/gpt_sovits.py` | 探测 `/openapi.json` 并 POST `/tts`，按剩余 Body Deadline 收取有界结果。 | 仅 Loopback IP；不信任代理，不自动重试/重定向或切换全局权重；拒绝无 Content-Length、压缩或 Transfer-Encoding；非流式配置仅 WAV/AAC |
| `voice/synthesis.py` | 验证请求与最大 32 MiB 编码结果的完整 Transport Framing。 | PCM WAV、Ogg Opus、受支持 ADTS AAC 子集通过结构验证；不声称已做 Codec Decode |
| `scripts/smoke_gpt_sovits.py` | 每个情绪对固定句子合成两次并输出安全摘要。 | 不写音频、不回显 Prompt/路径/异常原文 |
| `voice/managed_gpt_sovits.py` | 独占启动固定 Python 3.9 Worker、核验所选声明并提供可取消的 Lease。 | 第三方 Runtime 与同一 Windows 用户进程在 Lease 开始时属于信任范围；部分 Manifest 不是完整供应链证明，缓存保持禁用 |
| `voice/speech_queue.py` | 从模型 Chunk 自然分句，跳过无文字/数字锚点的纯装饰片段，并以有界 FIFO 合成、交付、跳过失败和取消迟到结果；独立 Feeder 可在容量释放时继续递交。 | 队列只持有 Speech 副本，不修改 Brain 的最终 Assistant 文本；普通替换逻辑丢弃而不 Poison Worker |
| `desktop_speech.py` | 用有界原文 Spool 和背压 Feeder 把 Managed Lease、Sentence Queue、NDJSON Metadata 与 fd3 WAV 组合起来。 | 快速长回复不阻塞 Chat 且不因 FIFO 瞬时满载被截断；真实可选 Speech 故障只禁用语音 |
| `desktop/electron/speech-delivery.ts` | 在 Main 内严格配对 Metadata/Frame，等待可信播放结束后才 ACK 下一帧，并产生最小的 `playing|played|skipped|terminal` 状态。 | Renderer 状态只含 Request/Chat/Sequence 或闭集终态；WAV、Token 与 Hash 不进入 React API |
| `desktop/electron/speech-playback-owner.ts` + `preload.cts` | 以私有 IPC 把单个 WAV 交给 Preload Web Audio，复用输出图并处理 Decode、Gain、Output Sink、结束、取消、超时、跨文档导航和窗口替换；可恢复错误会换用新 Owner 世代。公开桥只提供按 Chat Request ID 停止播放，不从音频生成角色视觉。 | Settlement 只接受所属窗口 Main Frame；迟到旧回执不能影响新 Clip，Renderer 业务代码看不到音频/样本，Chat/Voice 静态角色不依赖播放链 |

外部 HTTP Smoke 连接关系：

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

真实本机 Smoke 基线已对同一中文文本的 `neutral`、`happy`、`sad` 各运行两次，六次都得到有效 WAV；十值扩展又通过受管 CUDA Runtime 对全部十种情绪各运行一次，十次都得到有效的 32 kHz mono 16-bit PCM WAV。重复要求是“每次都有效”，并不承诺编码字节完全相同。Runtime 停止后返回稳定的 `service_unreachable`，文字 Chat 测试仍通过。外部 HTTP 探测仍只报告 `available / service_binding_unverified`，因为 `/openapi.json` 不能证明服务实际加载了 Catalog 声明的权重；它与桌面受管 Worker 是两条不同边界。

桌面受管播放连接关系：

```text
Brain.stream_chat() canonical chunks and commit
  → desktop_backend.py optional speech copy
  → desktop_speech.py bounded raw-text spool / fixed feeder
  → voice/speech_queue.py natural segmentation / backpressured bounded FIFO
  → voice/managed_gpt_sovits.py fixed worker lease
  → scripts/gpt_sovits_worker.py
  → PCM WAV
  ├─ voice.speech.* metadata over authenticated NDJSON
  └─ matching binary frame over inherited fd3
       → Electron SpeechDeliveryCoordinator
       ├─ private Main-to-Preload playback IPC
       │    → Web Audio decode / playback / settlement
       │    → ACK or discard fd3 frame
       └─ sanitized status
            → VoiceSessionController THINKING / SPEAKING / IDLE
```

受管路径固定了 Profile/情绪、Worker、权重声明和进程/管道生命周期，并持续 Guard 已声明的资产与 Runtime Anchor；它没有逐一认证第三方 Runtime 的六万多个依赖，也无法撤销同一用户在封印前已经取得的 `WRITE_DAC`。因此当前威胁模型明确信任 Lease 开始时的本地第三方 Runtime 与同一 Windows 用户进程。Queue 层 `binding_verified=True` 仅证明 Elysia 私有 Factory 签发了围绕该受管 Lease 的 Binding，不是完整依赖来源认证，也不会使语音缓存获得资格。若未来需要抵御恶意同用户进程，应改用由 Installer/SYSTEM 所有的只读 Runtime，或独立受限身份/AppContainer 与经过批准的完整签名 Manifest。

## 27. 哪些文件不应被当成源码垃圾

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

## 28. 修改功能时从哪里开始

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

### 修改 Document Loader 或增加格式

```text
documents/domain.py（结构或预算改变时）
→ documents/protocol.py
→ 对应 format adapter
→ documents/service.py（路由或授权改变时）
→ tests/test_document_domain.py
→ 对应 loader tests + service integration tests
→ docs/05-DOCUMENT-LOADERS.md
```

新格式必须先定义精确 Extension + MIME Route、内容 Signature、资源预算与稳定错误；Loader 只能消费 `open_verified_file()` 产生的 Bytes，不能接受或重开本机路径。桌面现在只暴露最小 Knowledge DTO；新格式必须同步 route/profile/错误映射与真实 fixture 回归，不能把 Parser 对象或底层异常直接序列化给 Renderer。

### 修改 Document Cleaning 或 Chunking

```text
documents/cleaning.py（Loaded Provenance、Span、Omission、Cleaner Policy/Version）
→ documents/chunking.py（Boundary、Projection、Mapping、Chunker Policy/Version）
→ documents/protocol.py 与 documents/pipeline.py（Adapter/端到端复核改变时）
→ tests/test_document_cleaning.py
→ tests/test_document_chunking.py
→ tests/test_document_processing_pipeline.py
→ docs/06-DOCUMENT-CLEANING-CHUNKING.md
```

任何会改变保留文字、Table Projection、Chunk Boundary、Source Mapping 或 ID Preimage 的修改，都必须显式更新相应 Producer Version/Policy，并验证旧 Derivation 不会被误当成新结果。Offset 始终相对 `LoadedDocument` Unicode Code Points；不能把它改写成原始 Byte、PDF Glyph 或 DOCX XML Offset。新增持久化或桌面接线时，还必须单独定义 Scope-filtered Derived Relation、重建/删除传播、最小 Protocol DTO 与 Renderer 隐私边界。

### 修改 Local Embedding 或 Vector Store

```text
documents/embedding.py（Model/Space/Template/Batch Identity 与 Lineage）
→ documents/ollama_embedding.py（Artifact 声明与 Loopback HTTP 边界）
→ documents/vector_store.py（Schema、Scope、Transaction、Canonical Encoding）
→ documents/indexing.py（组合顺序或失败语义改变时）
→ tests/test_document_embedding.py
→ tests/test_ollama_embedding.py
→ tests/test_document_vector_store.py
→ tests/test_document_indexing.py
→ docs/07-LOCAL-EMBEDDINGS-VECTOR-STORE.md
→ MODEL_LICENSE.md（Model Artifact、Digest、Quantization 或条款改变时）
```

Model Tag 不能单独代表向量空间；Manifest Digest、Adapter/Template Version、Dimension 与 Normalization 都必须参与身份。任何会改变 Model Input 或 Vector Meaning 的修改都必须创建新 `embedding_space_id` 并拒绝旧 Store；不得通过默认 Truncation、隐式 Normalization 或手工改 Digest 来兼容。每个读写操作仍必须提供精确 Chat/Project `AttachmentScope` 和 Ownership Link；上层 Retriever 不能用 SQL Wildcard 取代经验证的 Scope Context。

### 修改 Retriever 或 Reranking Contract

```text
documents/embedding.py（EmbeddedQuery Identity/Policy 改变时）
→ documents/retrieval.py（Allowlist、Filter、Policy、Evidence、Reranker Contract）
→ documents/vector_store.py（事务快照、扫描/评分/预算改变时）
→ documents/__init__.py（公共 API）
→ tests/test_document_embedding.py
→ tests/test_document_vector_store.py
→ tests/test_document_retrieval.py
→ docs/08-RETRIEVER-RERANKING.md
```

Retriever 只能消费由上层验证后显式提供的准确 Scope 与 `ExpectedDocumentGeneration` Allowlist；不能自行枚举 Scope、推断 Project-to-Chat 权限或把缺失/过期 Generation 当作空结果。任何更改 Query Template、阈值、Tie-break、Dedup Key、Reranker Score Semantics、Input Shape 或 Truncation Policy 的行为都必须作为版本化 Contract 处理。配置了 Reranker 后必须完整成功或 Fail Closed，不能静默回退成另一种排序。下游 Grounded Answer/Citation、Project Source authorization 与 Project-only Knowledge Lifecycle 现已由 `documents/ollama_grounding.py`、`desktop_knowledge.py`、Protocol 和 React 接入生产桌面路径；更改 Retriever 合同必须同步这些消费者。

### 修改 Grounded Answer 或 Citation Contract

```text
documents/retrieval.py（Hit/Evidence/Mapping 改变时）
→ documents/grounding.py（Context、Prompt、Generator、Statement、Citation）
→ documents/ollama_grounding.py（生产 Generator transport/schema）
→ documents/__init__.py（稳定公共 API）
→ chats/domain.py、serialization.py（持久 proof shape）
→ core/brain.py 与 desktop_knowledge.py（commit/composition）
→ 双端 Desktop Protocol、Electron contracts、React MessageView
→ tests/test_document_grounding.py
→ tests/test_ollama_grounding.py、test_brain.py、test_chat_repository.py
→ tests/test_desktop_protocol.py、desktop/tests/protocol.contract.test.mjs
→ desktop/tests/ui/app-shell.spec.ts
→ docs/09-GROUNDED-ANSWERS-CITATIONS.md
→ docs/12-KNOWLEDGE-UI-TESTING.md
```

`GroundedAnswerService` 必须继续拥有同一次检索与至多一次非流式生成，不能公开接受任意 Query + `RetrievalResult` 配对。Preference 只能在完整 Retrieval validation 后重排已相关 Hit；片段只能选择完整前缀，文件名、页码和位置只能从检索 Evidence 构造，不能信任模型返回。新增 Statement 类型、Prompt/Preference 字段、Citation ID 域、预算、截断或输出 Schema 都属于版本化 Contract 变化。Project Source 授权由 `project_sources` 组合，Project-only 索引生命周期由 `knowledge_lifecycle` 组合，`OllamaGroundedAnswerAdapter` 与 Brain/Protocol/UI 已是当前生产消费者；合同变化必须更新全链路 fixtures 与持久化验证。

### 修改 Project Source Authorization 或 Catalog

```text
attachments/domain.py、repository.py、service.py、store.py
→ documents/service.py（受支持文档路由改变时）
→ documents/grounding.py（Instructions/Preference 合同改变时）
→ project_sources/catalog.py
→ project_sources/domain.py
→ project_sources/repository.py
→ project_sources/service.py
→ project_sources/__init__.py
→ desktop_knowledge.py 与 desktop_backend.py
→ 双端 Desktop Protocol / Electron / React Knowledge UI
→ tests/test_file_metadata_store.py
→ tests/test_document_grounding.py
→ tests/test_project_sources.py
→ tests/test_desktop_knowledge.py 与 Desktop Protocol/UI tests
→ docs/10-PROJECT-SOURCES.md
→ docs/12-KNOWLEDGE-UI-TESTING.md
```

回答入口不能接受 caller-provided Project ID、Scope 或 Generation Allowlist；它们必须从 canonical Chat→Project、原子 ownership snapshot 与显式 catalog 派生。Catalog CAS 必须覆盖完整跨进程 read/compare/write，删除必须保留单调、policy-bearing tombstone 防止 ABA；即使从未发布 live catalog，显式 revoke 也必须从 revision 0 原子创建 tombstone。结构化 preferred link IDs 必须是当前 catalog 的子集，自由文本 Instructions 只能作为有界、不可信 style data。Chat Attachment promotion 必须同时证明 canonical Chat history ownership 与 committed Manifest 状态，且只能通过持有 authority lease 的 Project Source coordinator 调用。

### 修改 Knowledge Lifecycle

```text
attachments/repository.py、service.py、store.py（ownership/removal 合同改变时）
→ documents/indexing.py（prepare/commit/rebuild/delete 边界）
→ project_sources/catalog.py、repository.py（完整 corpus 与 CAS/tombstone）
→ knowledge_lifecycle/domain.py
→ knowledge_lifecycle/repository.py
→ knowledge_lifecycle/service.py
→ knowledge_lifecycle/export.py
→ knowledge_lifecycle/__init__.py
→ desktop_knowledge.py 与 desktop_backend.py
→ 双端 Desktop Protocol / Electron / React Knowledge UI
→ 对应 Attachment/Indexing/Project Source/Lifecycle 测试
→ tests/test_desktop_knowledge.py 与 Desktop Protocol/UI tests
→ docs/11-KNOWLEDGE-LIFECYCLE.md
→ docs/12-KNOWLEDGE-UI-TESTING.md
```

Knowledge Lifecycle 必须保持 Project-only；新 Generation 必须最后发布完整 catalog，replace/delete 必须先写入保留 post-operation Instructions 的 tombstone 再清理。Journal checkpoint 必须先于第一个副作用并使用 exact next-revision CAS，JSON array insertion order 是 durable creation sequence；Journal 不是长期 policy authority，撤销后的 Instructions 必须留在 catalog tombstone。恢复要从 canonical stores 向前收敛，profile 或 supported-route 漂移也不能阻止已 tombstone 的 delete/replace 完成安全清理；route 只控制准入/索引，cleanup identity 必须直接来自 exact immutable Manifest。Preview/Cache 目前没有实体，但显式 `KnowledgeArtifactCleanup` 不能被删除：新增独立 artifact store 时必须替换 no-op adapter 并纳入 ownership 删除前的幂等 cleanup。Verified export 共用全局 Knowledge lease，但必须与 lifecycle journal 分离；其 destination 只留在 Main/Python，跨 reload ownership 由 Electron snapshot 表达，且只有与预认证 Source metadata 匹配的路径私有 receipt 才能产生 `knowledge-export-settled`。当前 Desktop route 已经存在；任何 lifecycle/export ownership、字段、state/phase、receipt 或错误语义变化，都必须同步 Python/TypeScript/Schema/fixtures、Composition Root、BackendProcess、Main、App 与 UI tests。

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

若改动 Speech 控制事件或 fd3 二进制帧，还必须同步检查：

```text
desktop_protocol/audio_channel.py
desktop_speech.py
desktop/electron/speech-audio-channel.ts
desktop/electron/speech-delivery.ts
desktop/electron/speech-playback-owner.ts
desktop/electron/preload.cts
tests/test_desktop_speech_protocol.py
tests/test_desktop_audio_channel.py
desktop/tests/speech-audio-channel.test.mjs
desktop/tests/speech-playback-owner.test.mjs
desktop/tests/preload-speech-playback.test.cjs
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

### 修改可选 Desktop Pet

```text
desktop/electron/desktop-pet-contracts.ts
→ desktop/electron/desktop-pet-directory-picker.ts（原生程序目录选择变化时）
→ desktop/electron/desktop-pet-program-library.ts（扫描、信任 Hash、模型引用或启动前复核边界变化时）
→ desktop/electron/desktop-pet-preferences.ts（Schema v3、私有目录、opaque 选择或模式变化时）
→ desktop/electron/desktop-pet-lifecycle.ts（mutation 顺序、退出或总 deadline 变化时）
→ desktop/electron/desktop-pet-program-manager.ts（启动、owned PID、切换或停止边界变化时）
→ desktop/electron/main.ts + renderer-source.ts（单入口 Sender 验证与原生流程组合）
→ 主 Renderer contracts/preload/App/Settings（目录、重扫、程序摘要或模式表面变化时）
→ desktop/tests/desktop-pet-program-library.test.mjs
  + desktop-pet-program-manager.test.mjs
  + desktop-pet-lifecycle.test.mjs + desktop-pet-preferences.test.mjs
  + desktop-pet-directory-picker.test.mjs
  + protocol.contract.test.mjs + ui/app-shell.spec.ts
→ Distribution/Git/ASAR 检查
```

桌宠偏好缺失、v1/v2 迁移或配置损坏时都必须安全收敛；首次使用保持
`disabled`，只有 Main 严格扫描到所选受信程序后，Settings 才能请求
`visible`。Main 在真正启动前还必须对 descriptor/root 重做 lstat、realpath、
边界、必需结构、字节长度与全部固定 Hash 校验，不能把先前扫描结果当成可
执行授权。程序绝对目录、EXE、PID 与配置内容只能留在 Main；公开状态只含
folder name、扫描状态、opaque program ID 与 display name。Manager 必须在
切换前等待旧 owned PID 退出，停止失败时保留 ownership 并阻止第二实例；
不能按进程名关闭用户手工启动的同名副本。外部程序必须在原目录直接启动，
它自己的 `config.json`、窗口位置、输入模式和角色设置均原地保留，Elysia
不得改写或复制。来源为 `@书呆儿` 的付费合集以及用户从免费下载链接取得的
程序/模型都属于外部本机内容：绝不复制进 Project、Git、ASAR 或安装包。
应用内 Chat/Voice 状态板始终使用审核静态图；`07-desktop-pet-key-poses.png`
也不是运行时 Sprite Sheet。

### 修改 Presence 与 Notifications

```text
desktop/electron/presence-notification-contracts.ts
→ desktop/electron/presence-notification-policy.ts
→ desktop/electron/presence-notification-preferences.ts
→ desktop/electron/presence-native-notification.ts
→ desktop/electron/main.ts
→ desktop/electron/contracts.ts + preload.cts
→ desktop/src/App.tsx
→ desktop/src/settings/SettingsView.tsx + App.css
→ desktop/tests/presence-native-notification.test.mjs
→ desktop/tests/presence-notification-preferences.test.mjs
→ desktop/tests/ui/mock-preload.cjs + app-shell.spec.ts
→ desktop/tests/protocol.contract.test.mjs（Renderer permission 边界变化时）
→ README / desktop/README / Roadmap / 本文件
```

这条能力必须保持 Electron Main-local，不应为偏好、节奏或系统通知新增
Python Protocol。Reply-ready 的唯一生产触发是 BackendProcess 已验证并由
Main 收到的 `chat-complete`；stream chunk、取消、错误、Knowledge settled
或 Speech terminal 都不是完成通知。Renderer 只能替换审核过的 boolean /
frequency 并报告 Voice 页面 active fact，不能提供通知标题、正文、URL、
声音、动作、紧急度、schedule 或任意 IPC。Browser Notification permission
继续拒绝。

任何改动都要同时复核以下不变量：两个选择默认 Off，**Turn all off** 能
立即关闭并移除现存通知；系统文案固定、静音且不含 Chat/Prompt/Project/
文件/模型内容；点击只显示主窗口；Reminder 只在应用本来就在运行、周期
到期、Backend idle 且窗口 absent/hidden/minimized 时投递，仅 Alt-Tab 不能
触发；Chat、Voice、受管朗读或 Knowledge busy 期间的到期周期被记录为已
处理，不得在空闲后补发。Timer 必须 `unref`，固定 ID/Group 的单一原生槽
必须让 timeout 后的 Action Center 条目仍可被下一条、全关或退出撤回，旧实例
的迟到 click/failed 必须无效。退出必须清 Timer/Notification/Voice flag，并
在有界期限内排空已接纳的 Preference/handled-anchor 写入；unsupported、
持久化失败或 native delivery 失败只能关闭这项可选能力，不能中断 Chat、
Voice、Work 或延长应用生命周期。公开 state 不能包含私有
`lastReminderHandledAt`；若 cadence、注意力或 busy 定义变化，先扩充纯策略
和 Repository 合同测试，再修改 Main orchestration 与 Renderer 文案。

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
STT:
config/settings.py + config/desktop_settings.py
→ voice/transcription.py
→ voice/faster_whisper.py + voice/transcription_jobs.py
→ desktop_backend.py
→ 双端 Protocol / Fixtures
→ Electron contracts / BackendProcess / Preload / Main
→ voice-session-controller.ts
→ App.tsx / CallPreview.tsx / voice-ui-state.ts / character-state.ts / transcription-readiness.ts
→ Python Contract/Runner Tests + voice-session-controller.test.mjs + voice-ui-state.test.mjs + character-state.test.mjs
→ Desktop Protocol/UI Tests

TTS:
config/settings.py + config/voice_profiles.example.json
→ voice/synthesis.py + voice/profiles.py
→ voice/gpt_sovits.py + voice/synthesis_service.py + scripts/smoke_gpt_sovits.py
→ voice/managed_gpt_sovits.py + scripts/gpt_sovits_worker.py
→ voice/speech_queue.py + desktop_speech.py + desktop_backend.py
→ 双端 Speech Protocol / fd3 Audio Channel
→ Electron BackendProcess / SpeechDelivery / PlaybackOwner / Preload
→ Python 与 Desktop Contract Tests
```

如果只是增加模型权重或可选 Runtime，不要把它提交进源码：依赖版本进入 `requirements-stt.txt`，完整模型只放在被忽略的 `models/weights/faster-whisper/<model>`。任何新的 Runtime Error 必须先映射为稳定枚举，不能把路径、底层异常或 Native 对象直接送给 Renderer。

只修改外部 HTTP 单次合成时，仍可从较短链开始：

```text
config/settings.py + config/voice_profiles.example.json
→ voice/synthesis.py + voice/profiles.py
→ voice/gpt_sovits.py + voice/synthesis_service.py
→ scripts/smoke_gpt_sovits.py
→ 对应 Python tests
```

改动桌面播放必须继续检查 Managed Wrapper、Sentence Queue、Speech Protocol、fd3 Reader、Delivery Coordinator、Playback Owner、Preload、安全 Renderer Status、Voice Session Controller 和按 Request ID 的停止路径；React 只需要最小状态，不能接触音频。外部 GPT-SoVITS Runtime 放在被忽略的 `models/cache/`，权重/参考音频放在 `models/weights/gpt-sovits/`；不得把本机 Catalog、准确 Prompt、资产或 Runtime 混进源码提交。

## 29. 推荐的新成员阅读顺序

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
12. `core/elysia_system_prompt_zh.md`
13. `core/prompts.py`
14. `core/brain.py`
15. `chats/migration.py`
16. `recovery/service.py`
17. `attachments/domain.py`
18. `attachments/service.py` 与 `attachments/store.py`
19. `documents/domain.py` 与 `documents/protocol.py`
20. `documents/service.py`
21. `documents/text.py`、`documents/pdf.py` 与 `documents/docx.py`
22. `documents/cleaning.py`、`documents/chunking.py` 与 `documents/pipeline.py`
23. `documents/embedding.py` 与 `documents/ollama_embedding.py`
24. `documents/vector_store.py` 与 `documents/indexing.py`
25. `documents/retrieval.py`
26. `documents/grounding.py` 与 `documents/ollama_grounding.py`
27. `project_sources/catalog.py`、`project_sources/domain.py`、`project_sources/repository.py` 与 `project_sources/service.py`
28. `knowledge_lifecycle/domain.py`、`knowledge_lifecycle/repository.py`、`knowledge_lifecycle/service.py` 与 `knowledge_lifecycle/export.py`
29. `desktop_knowledge.py`
30. `docs/05-DOCUMENT-LOADERS.md`、`docs/06-DOCUMENT-CLEANING-CHUNKING.md`、`docs/07-LOCAL-EMBEDDINGS-VECTOR-STORE.md`、`docs/08-RETRIEVER-RERANKING.md`、`docs/09-GROUNDED-ANSWERS-CITATIONS.md`、`docs/10-PROJECT-SOURCES.md`、`docs/11-KNOWLEDGE-LIFECYCLE.md` 与 `docs/12-KNOWLEDGE-UI-TESTING.md`
31. `desktop_protocol/README.md`
32. `desktop/electron/contracts.ts`
33. `desktop/electron/preload.cts`
34. `desktop/electron/main.ts`
35. `desktop/electron/desktop-pet-contracts.ts`、`desktop-pet-program-library.ts`、`desktop-pet-program-manager.ts`、`desktop-pet-preferences.ts` 与 `desktop-pet-lifecycle.ts`
36. `desktop/electron/presence-notification-contracts.ts`、`presence-notification-policy.ts`、`presence-notification-preferences.ts` 与 `presence-native-notification.ts`
37. `desktop/electron/backend-process.ts`
38. `desktop_backend.py`
39. `desktop/src/knowledge/ProjectSourcesPanel.tsx` 与 `desktop/src/chat/MessageView.tsx`
40. `desktop_speech.py`
41. `voice/speech_queue.py`
42. `voice/managed_gpt_sovits.py` 与 `scripts/gpt_sovits_worker.py`
43. `desktop_protocol/audio_channel.py`
44. `desktop/electron/speech-delivery.ts`
45. `desktop/electron/speech-playback-owner.ts`
46. `desktop/electron/song-cover-contracts.ts`、`song-lyrics-provider.ts`、`song-cover-lyrics-assets.ts`、`song-cover-manager.ts`、`music-playback-owner.ts` 与 `application-shutdown-gate.ts`
47. `scripts/song_lyrics_alignment.py`、`song_svs_runtime.py`、`song_svs_wsl_bridge.py`、`song_svs_worker.py`、`song_rvc_runtime.py` 与 `song_cover_worker.py`
48. `docs/15-SONG-COVER.md`、`desktop/src/song-cover/SongCoverSetupDialog.tsx`、`song-cover-stage.ts` 与 `desktop/src/chat/Composer.tsx`
49. `desktop/src/voice/audio-capture.ts` 与 `desktop/src/voice/voice-activity-detector.ts`
50. `desktop/src/voice/voice-session-controller.ts`
51. `desktop/src/voice/voice-ui-state.ts`
52. `desktop/src/character/character-state.ts`、`character-emotion.ts`、`character-presentation.ts` 与 `CharacterArtwork.tsx`
53. `docs/14-LIVE2D-RUNTIME.md` 与 `desktop/src/settings/SettingsView.tsx`（了解静态应用内状态板和外部桌宠程序边界）
54. `desktop/src/App.tsx`
55. `desktop/src/voice/CallPreview.tsx` 与其他具体 Feature Component
56. 对应测试，尤其是 Document Loading/Processing/Embedding/Indexing/Retrieval/Grounding、Project Sources、Knowledge Lifecycle、Song Cover、`tests/test_desktop_knowledge.py`、`tests/test_real_document_regression.py`、`tests/test_song_lyrics_alignment.py`、`tests/test_song_svs_runtime.py`、`tests/test_song_svs_wsl_bridge.py`、`tests/test_song_svs_worker.py`、`desktop/tests/protocol.contract.test.mjs`、`desktop/tests/song-cover-manager.test.mjs`、`desktop/tests/song-lyrics-provider.test.mjs`、`desktop/tests/ui/app-shell.spec.ts`、`desktop/tests/voice-session-controller.test.mjs`、`desktop/tests/voice-ui-state.test.mjs` 与 `desktop/tests/character-state.test.mjs`

读完后应形成以下心智模型：

- Domain 管“什么数据是合法的”。
- Repository 管“保存在哪里、如何安全保存”。
- Service 管“多个对象怎样一起变化”。
- Brain 管“一次 AI 用例怎样完成”。
- Python 是持久化事实来源。
- Electron 管可信本机能力。
- React 只管理显示和短暂状态。
- 主窗口与 Voice 的 Character State 始终只选择审核静态图片；它们没有 Animated/Still 偏好或音频嘴型。动态桌宠由默认关闭的外部伴侣程序自己呈现：Main 严格扫描并在每次启动前复核受信程序，Renderer 只选择无路径摘要，program manager 只拥有 Elysia 启动的精确 PID；程序与模型绝不进入 Git 或安装包，原程序 `config.json` 保持原地且不由 Elysia 修改。
- Presence/Notifications 是默认关闭的 Electron Main-local 可选能力：Renderer 只表达受审偏好，Python 不参与 cadence；只有已验证 `chat-complete` 可产生固定 Reply-ready 候选，主动 Reminder 只在应用仍运行且窗口隐藏/最小化时出现。
- 单句 STT 只返回 Final Transcript；进入 Composer 与显式 **Send transcript** 是两个不同选择，任何 Chat Turn 都必须经过用户确认。
- 有界 Voice Session 已连接 Canonical Chat、受管播放、安全 Barge-in 与显式启用的自动续听；React 只看到安全状态，不接触 WAV。Partial Transcript 与自动提交仍未实现，真实设备/回声/长通话验收仍待记录。
- Streaming Overlay 不等于已保存消息。
- `ChatSession.project_id` 是 Project–Chat 关系的唯一真相。
- 所有 Memory 使用前都必须经过 Scope 过滤。
- `DocumentLoaderService` 只能通过 `open_verified_file()` 读取 Scope-authorized Verified Bytes；格式 Loader 只收到关闭读取 Context 后的无路径快照。`DocumentProcessingService` 组合纯 Cleaner/Chunker 并复核 Piece-table、Fingerprint/Lineage 与 LoadedDocument Source Mapping；`DocumentEmbeddingService` 把精确 Chunk Lineage 绑定到固定本地 Ollama 向量空间，`SQLiteVectorStore` 再以精确 Project Scope 和原子 Generation 持久化它。`KnowledgeLifecycleService` 以 Project-only durable saga 协调 add/replace/reindex/rebuild/revoke/delete，新 Generation 最后发布 catalog，破坏性操作先 tombstone；`KnowledgeExportService` 在同一全局 lease 下用私有 cleanup intent 导出 verified original，但不进入 journal；`ProjectSourceAnswerService` 再从 canonical Chat→Project、原子 ownership snapshot 和显式 current-profile catalog 派生唯一允许的 Project Generation。`DocumentRetriever` 只搜索该 Allowlist，在单事务中验证并计算有界 Cosine Top-K，保留去重 Evidence；`GroundedAnswerService` 再从完整命中前缀生成结构化陈述并只发布解析到可信 Evidence 的 Citation。`desktop_knowledge.py` 组合生产 Ollama Adapter 与共享 lease/store，Brain 原子保存文本和 proof，Protocol/Electron/React 再只发布路径私有的 Source/Operation/Citation/Export Receipt DTO；Electron 另外持有跨 reload 的 lifecycle/export request ownership。这条生产链已接通，但仍不包含文件 Preview、原文定位跳转、自动目录导入或语义正确性证明。
