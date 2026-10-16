# 源码导览

按功能找到入口，再沿调用链阅读。每个模块的详细合同由源码注释和[专题文档](./docs/README.md)维护。

## 整体结构

```text
React UI
  → 受限 Preload API
  → Electron Main
  → 认证 NDJSON Protocol v1
  → Python 服务与 Repository
  → 本地模型 / 本地数据
```

| 层 | 负责什么 | 主要入口 |
| --- | --- | --- |
| React | 页面、草稿、流式显示与交互状态 | [App.tsx](./desktop/src/App.tsx) |
| Preload | 向 Renderer 暴露固定能力 | [preload.cts](./desktop/electron/preload.cts) |
| Electron Main | 窗口、原生权限、路径与子进程 | [main.ts](./desktop/electron/main.ts) |
| Backend | 请求路由、生命周期与错误转换 | [desktop_backend.py](./desktop_backend.py) |
| Core | 组装模型、会话与记忆，完成一次对话 | [start.py](./start.py)、[brain.py](./core/brain.py) |
| Persistence | Domain 校验、Repository 与原子存储 | [chats](./chats/)、[projects](./projects/)、[memory](./memory/) |

Python 保存 Chat、Project、Memory、知识库和音频设备选择；Electron 拥有桌面能力、硬件枚举与权限，以及桌宠和通知的本机偏好。React 不直接读取用户文件或改写存储。流式显示不等于已保存消息。

Domain 定义合法数据，Repository 隐藏存储方式，Service 协调跨对象操作。修改业务时优先沿这条边界推进。

## 按功能找代码

### 对话、记忆与设置

| 功能 | 从这里开始 |
| --- | --- |
| Chat 生命周期、重试与提交 | [core/active_conversation.py](./core/active_conversation.py)、[chats/](./chats/) |
| 模型调用与本地连接策略 | [core/langchain_ollama_chat_model.py](./core/langchain_ollama_chat_model.py)、[core/local_ollama.py](./core/local_ollama.py) |
| Prompt 组合 | [core/prompts.py](./core/prompts.py)；角色设定独立维护 |
| 简繁转换与回复规范化 | [localization/chinese.py](./localization/chinese.py) |
| Project 与 Chat 归属 | [projects/service.py](./projects/service.py)、[projects/repository.py](./projects/repository.py) |
| 记忆范围、检索与摘要 | [memory/scope.py](./memory/scope.py)、[memory/retrieval.py](./memory/retrieval.py)、[memory/summarization.py](./memory/summarization.py) |
| 默认配置与桌面设置 | [config/settings.py](./config/settings.py)、[config/desktop_settings.py](./config/desktop_settings.py) |
| 数据根与恢复 | [config/data_layout.py](./config/data_layout.py)、[recovery/service.py](./recovery/service.py)、[data-storage.ts](./desktop/electron/data-storage.ts) |

`ChatSession.project_id` 是 Chat 归属的唯一事实来源。记忆使用前必须过滤 Scope；模型推测与用户确认事实不能混为一类。设置保留 Saved/Active 与重启生效边界。

### 文件与知识库

```text
附件验证读取 → 文档加载 → 清洗/分块 → 本地 Embedding
  → SQLite 索引 → Project 授权检索 → 结构化回答与引用
```

| 模块 | 职责 |
| --- | --- |
| [attachments/](./attachments/) | Scope、文件所有权、私有 Blob 与校验读取 |
| [documents/](./documents/) | 格式加载、来源映射、分块、向量、检索与 Grounding |
| [project_sources/](./project_sources/) | 从规范 Chat→Project 关系派生允许使用的来源 |
| [knowledge_lifecycle/](./knowledge_lifecycle/) | 添加、替换、索引、撤销、删除、导出与崩溃恢复 |
| [desktop_knowledge.py](./desktop_knowledge.py) | 生产 Adapter、共享操作租约和服务组合 |

这条桌面链路已接通。新 Generation 在处理完成后才授权，破坏性操作先撤销授权再清理；回答和引用一起保存。证据闭包保证引用可定位，不证明模型概括在语义上必然正确。

具体合同从[文件存储](./docs/04-FILE-METADATA-STORAGE.md)读到[知识库 UI](./docs/12-KNOWLEDGE-UI-TESTING.md)。

### 语音与翻唱

| 功能 | 从这里开始 |
| --- | --- |
| 麦克风、重采样与 VAD | [audio-capture.ts](./desktop/src/voice/audio-capture.ts)、[voice-activity-detector.ts](./desktop/src/voice/voice-activity-detector.ts) |
| Dictation 与 Voice Call | [dictation-session-controller.ts](./desktop/src/voice/dictation-session-controller.ts)、[voice-session-controller.ts](./desktop/src/voice/voice-session-controller.ts) |
| 转写、Voice Profile 与受管 TTS | [voice/](./voice/)、[desktop_speech.py](./desktop_speech.py) |
| 音频传输 | [audio_channel.py](./desktop_protocol/audio_channel.py)、[speech-audio-channel.ts](./desktop/electron/speech-audio-channel.ts) |
| 独立通话窗口 | [voice-call-window.ts](./desktop/electron/voice-call-window.ts)、[voice-call-contracts.ts](./desktop/electron/voice-call-contracts.ts) |
| 翻唱与播放 | [song-cover-manager.ts](./desktop/electron/song-cover-manager.ts)、[music-playback-owner.ts](./desktop/electron/music-playback-owner.ts) |

Dictation 持续分段转写到草稿，不自动发送。Voice Call 将完整语句提交到同一个 Chat，回复与预期朗读结束后继续监听。主 Renderer 拥有音频；独立通话窗口只接收展示状态并请求静音或挂断。

TTS 通过私有二进制通道交付经过校验的 WAV；PCM、模型路径和参考文本不进入 React 或普通协议日志。停止后过期的本机合成可能需要排空，不能冒充已立即终止。实时 Partial Transcript 尚未实现。

配置与验证见[桌面指南](./desktop/README.md)、[语音验收](./docs/03-VOICE-PERFORMANCE-SAFETY-RIGHTS.md)和[翻唱指南](./docs/15-SONG-COVER.md)。

### Work、视觉与联网

| 功能 | 从这里开始 |
| --- | --- |
| Work 请求与计划 | [desktop_work.py](./desktop_work.py)、[desktop_work_plans.py](./desktop_work_plans.py) |
| 工作流、检查点与任务 | [agent/](./agent/) |
| Workspace、工具、写入与审计 | [work_tools/](./work_tools/) |
| 视觉上下文 | [agent/vision_context.py](./agent/vision_context.py)、[desktop_work_context.py](./desktop_work_context.py) |
| 图片、截图与 OCR | [desktop/electron/](./desktop/electron/) 下的 `vision-*` 模块 |
| 网络策略与原生请求 | [internet-policy.ts](./desktop/electron/internet-policy.ts)、[internet-transport.ts](./desktop/electron/internet-transport.ts) |
| 联网聊天组合 | [core/internet_assistant.py](./core/internet_assistant.py)、[internet-chat-search.ts](./desktop/electron/internet-chat-search.ts) |

模型输出不能增加工具调用或批准操作。恢复任务需要重新验证权限和外部状态；网络读取与外部写入分别授权。操作路径见 [Work](./docs/17-WORKFLOW-AND-DESKTOP-AGENT.md)、[Vision](./docs/18-VISION-AND-SCREEN.md) 与 [Internet](./docs/19-INTERNET-AND-SERVICES.md)。

### 角色、桌宠与通知

- [desktop/src/character/](./desktop/src/character/)：主面板按封闭语义状态选择审核静态图片，Emotion 保留为元数据；独立通话窗口使用固定背景。角色提示词与美术资料单独维护。
- [desktop-pet-program-library.ts](./desktop/electron/desktop-pet-program-library.ts)：扫描用户选择的程序目录，验证结构和固定 Hash。
- [desktop-pet-program-manager.ts](./desktop/electron/desktop-pet-program-manager.ts)：启动前复核，只管理 Elysia 启动的准确 PID 树。
- [presence-notification-policy.ts](./desktop/electron/presence-notification-policy.ts)：默认关闭的固定文案通知与 Daily / Weekly 提醒。

应用内角色不运行 Live2D；动态桌宠由用户持有的外部原程序呈现。切换前必须停止旧进程，原程序配置留在原目录。通知由 Main 管理，只在应用已运行时调度。边界见[桌宠指南](./docs/14-LIVE2D-RUNTIME.md)与[素材说明](./MODEL_LICENSE.md)。

### 本地图片生成

[image_generation/](./image_generation/) 包含固定单图 Spec、审核 Profile、受管 ComfyUI、PNG 校验、持久 Gallery 与原子发布服务。[desktop_protocol/image_channel.py](./desktop_protocol/image_channel.py) 定义私有图片传输合同。

核心与部分协议已实现，桌面生成入口、预览、导出和全局 GPU 协调仍待接入。不能把协议类型存在当成用户功能已可用；当前限制与后续工作见[图片生成指南](./docs/16-LOCAL-IMAGE-GENERATION.md)。

## 修改与验证

1. 先读相应 Domain、Service 和本模块测试；只改 UI 时从功能组件入手。
2. 跨进程字段变化须同步 Python/TypeScript 合同、共享 Schema、Fixtures 与 Preload API。
3. 持久化变化须考虑旧版本、原子提交、取消、损坏隔离和恢复。
4. 原生能力变化须验证权限、真实路径、进程归属及迟到事件。
5. 按 [AGENTS.md](./AGENTS.md) 补充说明并执行文档检查，再运行与变化相关的测试。

| 验证范围 | 入口 |
| --- | --- |
| Python | [tests/](./tests/)、`python -m pytest` |
| 协议与原生控制器 | [desktop/tests/](./desktop/tests/)、`npm run test:contract` |
| Electron UI | [desktop/tests/ui/](./desktop/tests/ui/)、`npm run test:ui` |
| 分发内容 | [check_distribution_assets.py](./scripts/check_distribution_assets.py) |
| CI 完整流程 | [tests.yml](./.github/workflows/tests.yml) |

完整开发命令见[桌面指南](./desktop/README.md)。

## 文件清理边界

`package-lock.json`、Schema、Fixtures、迁移代码和小型 `__init__.py` 都有用途。构建目录与缓存可以重建，用户数据、模型、语音素材和本机配置不能当作源码垃圾删除。

Desktop 默认使用 Electron `userData/data`；Console 与旧开发模式可能仍使用仓库 `workspace/`。移动目录应走 **Settings → Data & storage**，它不等于备份。旧 Recovery Bundle 只覆盖部分 Domain JSON，不能代替完整数据根备份。详见[数据布局](./docs/13-PRODUCTION-DATA-LAYOUT.md)。
