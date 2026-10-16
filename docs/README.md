# 文档

从使用开始，需要时再深入实现。

[返回主页](../README.md) · [English overview](../README.en.md)

## 开始与开发

| 文档 | 内容 |
| --- | --- |
| [桌面开发指南](../desktop/README.md) | 安装、启动、可选语音配置、验证与打包 |
| [源码导览](../SOURCE_FILE_GUIDE.md) | 架构、模块入口与修改路径 |
| [本地协议](../desktop_protocol/README.md) | Python 与 Electron 的通信合同 |
| [源码文档规范](../AGENTS.md) | 文件说明、公开接口注释与必需检查 |
| [桌面架构决策](./decisions/0001-desktop-shell.md) | 选择 Electron 的依据与重审条件 |

## 功能指南

| 文档 | 内容 |
| --- | --- |
| [文件问答](./12-KNOWLEDGE-UI-TESTING.md) | Sources、引用、恢复与当前限制 |
| [数据管理](./13-PRODUCTION-DATA-LAYOUT.md) | 本地目录、迁移、容量与清理 |
| [桌宠](./14-LIVE2D-RUNTIME.md) | 静态应用内角色与可选外部桌宠 |
| [本机翻唱](./15-SONG-COVER.md) | 运行时准备、生成、试听与导出 |
| [本地图片生成](./16-LOCAL-IMAGE-GENERATION.md) | 桌面生成器、Gallery、原生导出、受信配置与 GPU 准入 |
| [图片运行环境审计](./17-LOCAL-IMAGE-RUNTIME-AUDIT.md) | 固定来源、许可、依赖摘要与审计范围 |
| [真实图片验收](./18-LOCAL-IMAGE-REAL-ACCEPTANCE.md) | 本机生成、取消、关闭、恢复与连续运行记录 |
| [Work 工作流](./17-WORKFLOW-AND-DESKTOP-AGENT.md) | 计划、工具、审批、恢复与审计 |
| [图片与屏幕](./18-VISION-AND-SCREEN.md) | 图片输入、OCR、截图与隐私 |
| [联网与服务](./19-INTERNET-AND-SERVICES.md) | 网络开关、查询、来源与外部写入 |

## 文件问答的实现

按数据处理顺序阅读即可。

1. [文件与元数据存储](./04-FILE-METADATA-STORAGE.md)
2. [文档加载](./05-DOCUMENT-LOADERS.md)
3. [清洗与分块](./06-DOCUMENT-CLEANING-CHUNKING.md)
4. [本地向量与索引](./07-LOCAL-EMBEDDINGS-VECTOR-STORE.md)
5. [检索与重排](./08-RETRIEVER-RERANKING.md)
6. [有依据的回答与引用](./09-GROUNDED-ANSWERS-CITATIONS.md)
7. [Project Sources 授权](./10-PROJECT-SOURCES.md)
8. [知识生命周期](./11-KNOWLEDGE-LIFECYCLE.md)

## 验证与素材

- [语音性能与验收记录](./03-VOICE-PERFORMANCE-SAFETY-RIGHTS.md)
- [素材来源与权利说明](../MODEL_LICENSE.md)
- [文档测试样本](../tests/fixtures/documents/README.md)
