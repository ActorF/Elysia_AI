# Model & Voice Asset Notice / 模型与语音素材说明

最近来源核对 / Last provenance review: 2026-10-06

本文记录 Elysia AI 已使用或评估过的模型权重、参考音频与角色资料的已知来源、本地处理规则和当前授权缺口。

**本文件是素材与权利告知，不是开源许可证或第三方授权。** 无法核实的素材默认只留本机；公开例外必须记录来源类别、用途、权利人规则、署名、许可排除和移除风险。

This is an asset and rights notice, not a source-code license or a grant of third-party rights. Unverified assets stay local; attribution and hashes do not establish authorization.

## Asset Inventory / 素材范围

| 路径 | 状态 | 处理方式 |
| --- | --- | --- |
| `models/weights/gpt-sovits/elysia-v2/` | 已忽略且当前不在 Git Index 中 | 本地 GPT-SoVITS 权重与参考音频；每次打包和发布都须独立确认排除 |
| `models/cache/GPT-SoVITS-v2-240821/` | 被忽略的本地外部 Runtime | 解压后的上游 Windows 评估包和本机推理 YAML；不属于 Elysia AI 源码，也不进入 Git、Release、安装包或容器 |
| `models/cache/rvc-v2-40k/` and `models/cache/singing-runtime/` | 被忽略的本机外部歌声 Runtime | 固定的 MIT RVC v2 Source、本机 HuBERT/RMVPE 资产、Demucs 4.0.1 Package 与 `htdemucs`；只为本机执行固定身份，不被跟踪、上传、发布或打包 |
| `models/weights/rvc/elysia-v2-40k/` | 被忽略的私有爱莉希雅 RVC Checkpoint 与检索 Index | 由私有语音素材本机训练；模型、Index、训练/验证音频与评估输出均只用于本机个人评估，不进入 Git、Release、安装包、Container 或诊断 |
| 当前 WSL 用户下的 `~/.local/share/elysia-ai/soulx/` | 被忽略的私有歌词驱动歌声 Runtime | 固定的 SoulX-Singer Source/Model、预处理模型、隔离环境与 59 条私有爱莉希雅 Prompt bank（48 corpus + 10 emotion + 1 anchor）；仅限本机源码开发评估。Prompt 音频、metadata、准确文本及 private manifest 内容不复制到 Git、Windows 构建输出、Release、安装包、测试、文档或诊断包 |
| `models/blobs/`, `models/manifests/`, and `models/manifests-v2/` | 已忽略且当前不在 Git Index 中 | Ollama 管理的本地模型与版本化 Manifest；文档索引库固定 `qwen3-embedding:0.6b`，但本仓库不下载或再分发它，各上游模型仍各自遵循其条款 |
| `models/cache/faster-whisper/` and `models/weights/faster-whisper/` | 已忽略且当前不在 Git Index 中 | 仅存本地的语音识别模型；Adapter 要求明确、完整的目录，不打包也不隐式下载权重 |
| `data/characters/elysia_character_reference_zh.md` | 已跟踪 | 需要单独审查来源的角色背景与语录 |
| `data/characters/elysia_character_analysis_zh.md` and `core/elysia_system_prompt_zh.md` | 已跟踪的原创总结 | 未复刻网络完整台词库的转换性分析与运行时规则；不授予底层角色、剧情或引用语料的任何权利 |
| `desktop/public/elysia-icon.png` and `desktop/assets/elysia-icon.ico` | 已跟踪的第三方品牌素材 | 由《崩坏3》爱莉希雅官方刻印制作，并按项目所有者明确决定用于本非官方、非商业粉丝项目；© HoYoverse / miHoYo，不属于任何源码许可证，不代表官方背书，权利人要求时应移除 |
| `desktop/public/character/elysia-portrait.png` | 已跟踪并完成固定审核的生成式同人立绘 | 使用 OpenAI 内置图像生成工具并参考项目所有者提供的爱莉希雅图片生成；仅随本非官方、非商业粉丝项目分发，不属于任何源码许可证，并由分发审计验证固定内容 |
| `data/characters/elysia-2dArt/` | 已跟踪的生成式静态审阅包 | 本非官方粉丝项目的仓库静态审阅来源，不属于任何源码许可证；只有选定的静态角色状态素材进入安装包 |
| `data/characters/爱莉希雅原版猫猫版总合集_34be1/` | 已忽略、由用户持有的外部 Bongo Cat Mver 程序合集 | 项目所有者购买的本机副本来源标注为 `@书呆儿`；应用只显示通过有界结构检查、固定程序/DLL/资源 Hash 与启动前复核的程序，不假定固定数量。全部付费 EXE、DLL、Live2D 模型、Runtime 与配置均原地保留，不被跟踪、上传、发布、安装或复制到构建输出。记录的免费版页面为 `https://pan.quark.cn/s/cb5d84acad8e`，但其当前内容、授权、条款、可用性以及是否等同于付费副本均未独立核验 |
| `desktop/public/character/elysia-state-atlas.png` | 已跟踪并完成固定审核的生成式状态图集 | `data/characters/elysia-2dArt/02-activity-states.png` 的逐字节相同运行时副本；前七格对应封闭角色状态，第八格 success 不使用；当前面板使用下述 UI 图集；同样受非商业粉丝项目、源码许可排除、权利边界与分发审计约束 |
| `desktop/public/character/elysia-expression-atlas.png` | 已跟踪并完成固定审核的生成式表情图集 | `data/characters/elysia-2dArt/03-expression-atlas.png` 的逐字节相同副本；保留的 `CharacterArtwork` 组件只接受十值 Voice Emotion 映射，当前主面板与通话窗口不使用此图集；模型输出不能选择任意表情 |
| `desktop/public/character/elysia-speech-atlas.png` | 已跟踪并完成固定审核的生成式嘴型参考图集 | `04-facial-rig-atlas.png` 的逐字节副本；作为审阅素材保留并接受分发完整性验证，但当前应用内角色 Renderer 不选择它，也不执行嘴型同步 |

### Reviewed generated in-app character artwork / 已审核的生成式应用内角色图

`desktop/public/character/elysia-portrait.png` 中的立绘于 2026-09-30 使用 OpenAI 内置图像生成工具生成，生成时参考了项目所有者本次直接提供的三张爱莉希雅图片，以及所有者本机素材集内原有的一张爱莉希雅立绘。这四张参考文件不属于本仓库或安装包，本说明也不主张或授予对参考文件的权利。审核后的输出固定为 **2,223,154 字节**，SHA-256 为 **`359c2620ac5286cc6c77d533e5c53d1b63fd0fe08fdf42f5952136b7c5bcafb2`**。CI 会拒绝仓库审核路径上的文件缺失、长度变化或任意内容变化；打包后还要求 ASAR 中恰好出现一次 `dist/character/elysia-portrait.png`，并把该条目抽取出的实际字节与同一长度和摘要比对，避免源码或安装包内的替换文件自动继承本次审核结论。

应用内角色状态板始终为静态。可选动态桌宠默认关闭，只有 Electron Main 验证选中的本机 Bongo Cat Mver 程序后才会启动一项；动态渲染与交互由外部原程序负责，而不是应用内角色表现。任何付费 EXE、DLL、Live2D 模型、纹理、动作、表情、物理文件、Runtime、配置或源压缩包都不会复制进 Git、Release、构建输出或安装包。`data/characters/elysia-2dArt/07-desktop-pet-key-poses.png` 仍只是安装包输入之外的审阅资料。

审核状态图集于 2026-10-01 使用 OpenAI 内置图像生成工具制作，参考了项目所有者提供的爱莉希雅角度、面部、服装和风格图片；完整生成与定点修复记录保存在 `data/characters/elysia-2dArt/` 的源审阅图旁。运行时文件与 `02-activity-states.png` 逐字节相同，固定为 **2,303,963 字节**，SHA-256 为 **`54eb2525673c2a849819be10eb88eb2f670eb1911e86fd154e69b578cbb4c25c`**。按行读取的前七格分别表示 `idle`、`listening`、`thinking`、`speaking`、`working`、`waiting_approval` 和 `error`；第八格成功/庆祝明确不属于运行时封闭状态。CI 会固定仓库文件，要求 ASAR 中恰好出现一次 `dist/character/elysia-state-atlas.png`，并验证从该条目抽取的实际字节。

审核表情图集沿用 2026-10-01 的同一组生成参考，完整修订记录保存在 `data/characters/elysia-2dArt/03-expression-atlas.png` 旁。`desktop/public/character/elysia-expression-atlas.png` 是其逐字节相同副本，固定为 **2,500,647 字节**，SHA-256 为 **`fbf7a515b2651b3a881cf9b838a5605c316befd0b174dde046780d8e441d7f93`**。保留的 `CharacterArtwork` 组件仅接受 `neutral`、`happy`、`sad`、`caring`、`moved`、`playful`、`affectionate`、`teasing`、`serious` 或 `surprised` 的审核映射；Backend 重启后的 Active Emotion 选择本地 TTS 参考，当前主面板和通话窗口不据此切换图片。模型回复不能命名情绪、文件、图集格或动画。CI 会固定仓库文件，要求 ASAR 中恰好出现一次 `dist/character/elysia-expression-atlas.png`，并验证抽取字节。

审核面部绑定图集同样沿用 2026-10-01 的参考，生成与定点嘴部修复记录保存在 `data/characters/elysia-2dArt/04-facial-rig-atlas.png` 旁。`desktop/public/character/elysia-speech-atlas.png` 是其逐字节相同的审核副本，固定为 **2,054,767 字节**，SHA-256 为 **`21bf4496acc4417d491ff0163c9ee1d38593e376ca25a3d452fd393c6157f9ab`**。当前应用内角色表现只有静态图片，不从该图集选择格子，不为画面采样 Web Audio RMS，也不声称音素同步。CI 仍固定这份保留的审阅文件，要求 ASAR 中恰好出现一次 `dist/character/elysia-speech-atlas.png` 并验证抽取字节；该完整性检查不代表它属于当前角色渲染路径。

角色面板改用项目所有者指定的已审核 4×4 `06-ui-illustrations.png` 图集。运行时副本 `desktop/public/character/elysia-ui-illustrations.png` 与母版逐字节一致，固定为 **2,415,759 字节**，SHA-256 为 **`e375b7e6fc0b3cea898c25a34381014a07cf334794ff7d6c0ea2d82f9707fa25`**；沿用上述来源与权利限制，不受源码许可证覆盖。分发检查固定这份副本及其唯一 ASAR 路径。面板按语义状态选取静态格，Emotion 只保留为元数据；独立通话窗口使用固定背景，保留的共享表情组件不代表这两个界面的当前渲染路径。

### External Bongo Cat Mver desktop-pet programs / 外部 Bongo Cat Mver 桌宠程序

以前生成的动态模型素材、脸部母版、源层与制作脚本均已移除。Elysia AI 不随软件分发动态角色模型或 Live2D Runtime，应用内角色状态板永久保持静态。可选动态桌宠改为从用户选择、来源标注为 `@书呆儿` 的本机合集启动一个原 Bongo Cat Mver 程序；键盘/鼠标反馈、眼部追踪、表情快捷键、窗口行为和 Live2D 动态均由原程序负责。

选择文件夹不等于允许执行其中任意代码。Electron Main 会限制遍历深度与数量、拒绝链接逃逸、要求完整的 standard/keyboard/gamepad 配置，并按固定字节长度和 SHA-256 验证已审查的 Launcher、UI 程序、顶层 DLL 集及必要资源。每次启动前，Main 都会对选中的 Descriptor 重新执行全部结构与 Hash 检查；任何被修改、缺失或新出现的可执行组件都会 Fail Closed，绝不启动。

Main 只启动当前选中的程序，并且最多持有一个此类子进程。切换时先关闭由 Elysia 启动并记录的准确根 PID 及其子进程树；它绝不按进程名搜索，因此用户手动启动的同名程序不在 Elysia 权限范围内。关闭失败或超时会阻止启动替换项。每套程序原有 `config.json` 均留在原地并继续由原程序拥有；Elysia 只读取验证受支持输入配置所需的有界结构，不修改、不替换、不复制、不上传、不打包配置，因此每套程序的设置保留在其原目录。

全部付费 EXE、DLL、Live2D 模型、纹理、动作、表情、物理文件、Runtime、配置与源压缩包必须留在 Git、GitHub、Release、安装包、ASAR、构建输出和共享诊断之外。记录的免费版页面为 <https://pan.quark.cn/s/cb5d84acad8e>，但其当前内容、授权、条款、可用性以及是否等同于项目所有者购买的本机副本均未独立核验。该链接只提供来源背景，不构成再分发许可或安全背书。

这些生成结果仍明确以《崩坏3》角色爱莉希雅为基础。AI 生成不会转移或消除底层角色、设计、名称或商标权利；相关权利仍归 HoYoverse / 米哈游及其他适用权利人所有。Elysia AI 不声称与官方有关联或获得背书，不售卖或单独授权这些图片，并将其排除在当前及未来的任何源码许可证之外；若收到有效的权利人要求，将移除相关素材。任何替换或衍生版本都必须重新完成来源与分发审核，并明确更新固定摘要与字节长度。

单独启动的 Loopback Smoke 路径仍是默认关闭、仅限 Python 的 Adapter；由于上游 API 无法证明其外部进程实际加载了哪些 Checkpoint，该路径只报告 `service_binding_unverified`。与它分开，Desktop 分句语音现已使用由 Elysia 持有的受管 Worker，以及私有 Electron 传输与播放链。本机真实评估通过该受管路径加载所选 Elysia GPT/SoVITS Checkpoint 并返回有效音频。这只证明该机器上的技术互操作，不代表生产就绪，不授予分发权，也不会消除下述任何素材授权缺口。

## Faster-Whisper Software and Model Boundary / Faster-Whisper 软件与模型边界

可选 Adapter 面向 [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)，其第三方 Import 不会进入基础文字 Chat Runtime。Faster-Whisper 上游源码采用 [MIT License](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)；该软件许可证只覆盖相应上游代码，不会自动覆盖任意录音、转写文本、转换模型或无关的角色与声音素材。

首个建议的本地评估候选是多语言 [`Systran/faster-whisper-small`](https://huggingface.co/Systran/faster-whisper-small)。2026-09-14 的本地 CPU 验收使用仓库 Revision `536b0662742c02347bc0e980a01041f333bce120`，其中 `model.bin` 的 SHA-256 为 `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`。该固定 Snapshot 的 Model Card 标注为 MIT，但下载内容没有独立 `LICENSE` 文件；本地副本继续被 Git 忽略，不属于本仓库或安装包。不得假设上游页面未来的修改会自动适用于这个固定副本。

Adapter 只接受明确的绝对本地模型目录，并要求其中存在 Runtime 配置、模型二进制与 Tokenizer；它会传入 `local_files_only=True`、拒绝 Git LFS Pointer，且不会把模型别名解析为隐藏的权重下载。Desktop Settings 只会把闭集模型名称映射到这个目录，不暴露路径或下载动作。模型安装仍是独立的用户触发操作，每次打包和发布都必须核对本地权重与缓存仍被排除。

## Qwen3 Embedding via Ollama / 通过 Ollama 使用 Qwen3 Embedding

独立文档索引库固定使用 Ollama Tag `qwen3-embedding:0.6b`。[Ollama 官方 Library 页](https://ollama.com/library/qwen3-embedding)是模型系列、可用 Tag 和高层 Size/Context 信息的公开展示来源。下文完整 Manifest Digest 记录了本实现于 2026-09-25 审查的精确 Registry Response；它是该次响应的身份锚点，不承诺可变 Registry Tag 今后永远不变。下列值标识当前实现所接受的精确 Artifact Contract，不代表该 Artifact 已进入本仓库：

| Field / 字段 | Pinned value / 固定值 |
| --- | --- |
| Ollama tag / Ollama Tag | `qwen3-embedding:0.6b` |
| Full manifest SHA-256 / 完整 Manifest SHA-256 | `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d` |
| Model-layer SHA-256 / 模型 Layer SHA-256 | `06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439` |
| Model-layer size / 模型 Layer 大小 | `639,150,592` bytes |
| Quantization / 量化 | `Q8_0` |
| Embedding dimensions / Embedding 维度 | `1,024` |
| Upstream capability summary / 上游能力概述 | 32K context; 100+ natural and programming languages / 32K Context；100+ 自然语言与编程语言 |

[Qwen3-Embedding-0.6B 上游 Model Card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) 把上游 Qwen 模型标注为 Apache-2.0，并声明上表概述的 32K、多语言和代码能力。该 Apache-2.0 说明只适用于 Model Card 所描述的上游模型；不得把它解释为对 Ollama Runtime、量化 Ollama Manifest 中每个 Layer 或 Metadata Object、无关依赖，或 Elysia AI 其他素材的概括授权。

Manifest 与模型 Layer 摘要、Byte 大小和 `Q8_0` 标记，只是当前版本期待的精确 Ollama 量化 Artifact 的技术来源锚点。除非另行留存权威发布者或量化者记录，这些观察本身不能证明每个 Manifest 组件由谁制作，不能建立完整供应链，也不授予再分发权。运行时 Adapter 会在每个 Batch 前后验证精确 Tag 与完整 Manifest Digest；Model-layer Digest、Size 与 Quantization 是该 Manifest 之下的文档化 Provenance，不是通过另一 Ollama API 独立重新证明的字段。实现不下载、镜像、打包、提交或再分发该 Artifact。

安装必须是用户显式执行的可选本地操作：

```bat
ollama pull qwen3-embedding:0.6b
```

基础文字 Chat 不需要该模型。应用与测试都不会触发 `ollama pull`；模型 Blob 与 Manifest 继续保留在 Ollama 管理、被 Git 忽略的本地存储中，不得进入 Git、Release、安装包、Container 或诊断归档。

文档检索库定义了可选的 Reranker Protocol，但本仓库目前没有打包、下载或选定任何 Reranker 模型。未来启用具体实现前，必须另行审查其身份、许可证、来源、本地存储与分发边界；不得把本节 Qwen Embedding 的记录视为其他 Artifact 的授权。

## Elysia Song-Cover Runtime and Model Boundary / 爱莉希雅翻唱 Runtime 与模型边界

默认 **Voice conversion** 使用私有 SoulX-Singer-SVC 权重、离线 Whisper 内容特征及用户选定的两种歌唱参考之一；源波形与完整时间轴引导转换，调性由用户明确选择。可选 **Lyrics-driven singing** 只向 LRCLIB 发送歌名、歌手与时长以取得普通话同步歌词；**Legacy RVC** 以 `legacy-svc` 保留旧离线转换。失败不会静默切换引擎。三种方式接受完整歌曲或对齐 Stem、-12 至 +12 整数半音与明确的伴奏跟随选择。音频、歌词、参考、中间结果及未导出音频均保持本机受管。算法检查和有限试听不会授予歌曲、录音、表演、歌词、角色、Prompt、模型或生成翻唱的权利。

已检查的本机示例目录包含三份时长一致的 44.1 kHz 文件：Stereo 最终混音、Stereo 伴奏和 Mono 爱莉希雅转换人声。这里只用它们确认预期流水线结构，即“转换人声 + 伴奏”；任何示例音频都没有被复制进仓库、测试、构建输出、安装包或文档。它们的存在不能证明项目有权再分发源歌曲或衍生录音。

Source-guided SVC additionally pins SoulX-Singer `model-svc.pt` from model revision `40493ad90286056c7a9095035164434a79daa8c9` (2,793,965,154 bytes; SHA-256 `38e72d9e15be6eef3cc937daaf81421a7b5b546816786530bb9aa346cee5d43c`) and the `openai/whisper-base` content encoder from revision `e37978b90ca9030d5170a5c07aadb050351a65bb` (290,403,936-byte safetensors; SHA-256 `07cadb9f25677c8d50df603e66a98fbd842cce45047139baeb16e6219a1e807b`). They remain in the private WSL runtime, outside Git and packages. The two singing excerpts and their F0 arrays under `prompts/singing-v1/` were prepared locally from the user's supplied recordings; their selection is listening feedback, not redistribution permission. Exact asset validation is in `scripts/song_svc_assets.py`.

私有 SoulX Source Checkout 记录的 Origin 为 `https://github.com/Soul-AILab/SoulX-Singer.git`，固定 Revision 为 `81aeb3ae772c70093c3de74dc23c92d983801ae4`。其中随库 `LICENSE` 是 Apache-2.0，上游 README 也声明 SoulX-Singer Code 与 Model Weight 使用 Apache-2.0。该声明不会自动为 RMVPE、ROSVOT、FunASR、OpenCC、它们的 Checkpoint/Dictionary、CUDA/Torch 依赖、所选歌词、爱莉希雅 Prompt、底层录音或生成翻唱重新授权。这些独立资产继续只保留在本机；任何分发前都必须分别复核其条款及表演者/数据集来源。

| Private WSL evaluation selection / 私有 WSL 评估选择 | Bytes | SHA-256 |
| --- | ---: | --- |
| SoulX-Singer `model.pt` | 2,818,092,278 | `447EAF41F91A6B6659D55E9EC3C9B809221724FB8592AEBAEC35A23751A5B500` |
| Preprocess RMVPE `rmvpe.pt` | 181,184,272 | `6D62215F4306E3CA278246188607209F09AF3DC77ED4232EFDD069798C4EC193` |
| ROSVOT RMVPE `model.pt` | 368,492,925 | `19DC1809CF4CDB0A18DB93441816BC327E14E5644B72EEAAE5220560C6736FE2` |
| ROSVOT singing-note `model.pt` | 144,674,420 | `7501FB5F913D971C2F51BCB3063B930027B03206581820A4D2BFDC394C9C3FCB` |
| ROSVOT word-boundary `model.pt` | 119,897,457 | `0BC2D42A6D4B7A05436DEB937E2DEDA1C12DE49E5687CFDA0BDF6A430120DCD2` |
| Mandarin FunASR `model.pt` | 989,763,045 | `3D491689244EC5DFBF9170EF3827C358AA10F1F20E42A7C59E15E688647946D1` |
| Private Elysia prompt-bank canonical manifest | 62,399 | `8A5A4ABD41054DCE40E91996260600E3906DF0F6D1E88A8AAF0E2B70EFEE825E` |
| 59 private Prompt WAV + 59 private score/phoneme metadata records | Per-entry bounded and authenticated / 每条均有界并验证 | Per-entry size and SHA-256 remain only in the private manifest / 每条大小与 SHA-256 只留在 private manifest |

59 条爱莉希雅 Prompt bank 没有随附可留存的逐条创作者身份、原始发布 URL、数据集记录、表演者授权或再分发许可；canonical manifest 与逐条摘要只认证本机已审核字节。Prompt 音频、metadata、准确文本与 manifest 内容不得进入 Git、公共镜像、Release、安装包、Container、测试 Fixture、文档、共享诊断或可下载 Runtime Bundle。包含配置、字典、解释器、FFmpeg、OpenCC、canonical manifest 与 manifest 引用的全部 Prompt 资产在内的完整 Runtime Allowlist 由 `scripts/song_svs_runtime.py` 执行；上表只记录 manifest 身份与 Bank 构成，不复刻私有条目，也不把全部依赖伪装成使用同一许可证。

只有用户选择歌词驱动方法并确认任务后，应用才把 LRCLIB 用作固定的 Metadata 与同步歌词查询服务。从该服务取得歌词不代表获得著作权、改编、公开表演或再分发授权。Runtime 歌词文件受仓库分发门禁禁止，并随私有 Job 删除；用户仍须自行负责所选歌曲、歌词及适用服务条款和法律要求。

固定的 RVC Source 来自 [`RVC-Project/Retrieval-based-Voice-Conversion-WebUI`](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) Revision `81eed5e8f68b6bed1789f682fe78cdd324495afc`，其随库 `LICENSE` 为 MIT。[Demucs 仓库](https://github.com/facebookresearch/demucs) 也把其覆盖的软件标为 MIT License。这些源码许可不会自动授权 HuBERT/RMVPE 权重、`htdemucs`、第三方依赖、私有训练录音、爱莉希雅 Checkpoint/Index、角色、原配音演员、选中歌曲或生成翻唱。

选定的爱莉希雅 RVC v2 40 kHz 模型由 2,182 份私有说话对白 WAV Slice（约 3.542 小时）在本机训练，没有干净的目标歌唱录音：2,073 份用于训练，109 份保留用于评估。这种纯说话覆盖不能证明持续低音、高音、停顿或摩擦音的质量。本仓库没有留存足以授权再分发原始素材、衍生 Checkpoint、衍生 FAISS Index 或评估音频的许可；它们均只是本机个人评估资产。Digest 和本机训练都不会自动授予发布底层声音表演、角色素材、模型、Index 或生成录音的权利。

使用同一批纯说话素材进行的隔离五轮 UV-aware 适配因掉音与频谱退化被客观门禁拒绝，从未成为选定的生产模型。其确定性本机评估不会为临时 Checkpoint、评估音频或报告产生再分发授权。

| Local RVC evaluation selection / 本地 RVC 评估选择 | Bytes / files | SHA-256 |
| --- | ---: | --- |
| Reviewed RVC Python tree | 130 files / 1,382,718 | `A391270FE0B38307C3C966C5CB394D947D177990FAE64307CB38313AE762B6C5` |
| Windows-compatible HuBERT directory aggregate | 3 files / 189,207,510 | `C4843B2163BE0AEBAC54B770579AD8C2B107F42C2FBC658D7B8CE7D244A54560` |
| RVC RMVPE checkpoint `assets/rmvpe/rmvpe.pt` | 181,184,272 | `6D62215F4306E3CA278246188607209F09AF3DC77ED4232EFDD069798C4EC193` |
| Selected Elysia RVC v2 e50 checkpoint `model.pth` (training export `elysia_rvc_50e_e50_s19850.pth`) | 55,232,507 | `CB3FEC4D975EAFD7C6B73BD096A6E6CDD6B9C3CA1D6793320D48FB17BEA2B9C8` |
| Elysia retrieval index `model.index` | 31,588,619 | `86A2DA597F7A09D8CB27BD2DAD3F1BCFA6FD6A561268622F4DAF94AB1DE8737E` |
| Demucs `htdemucs` checkpoint | 84,141,911 | `8726E21A993978C7BA086D3872E7608D7D5BFCA646CA4ACA459FFDA844FAA8B4` |
| Reviewed Demucs-specific singing runtime aggregate | 119 files / 1,107,739 | `3146D43372B416A46C17C2D06227F98B92C1E26C62EEA208E6277D1428B30712` |

Demucs Aggregate 包含 `demucs/remote/htdemucs.yaml` 和 `demucs/remote/files.txt`：前者固定选择模型 ID `955717e8`，后者把该 ID 绑定到 `hybrid_transformer/955717e8-8726e21a.th`；Checkpoint 字节仍另行接受长度与 SHA-256 验证。Runtime 验证还会拒绝任何可以覆盖受保护 Demucs Import 的同名 Sibling Module 或 Extension Provider，而不是只信任预期 Package Directory。

原始评估 HuBERT 把 Weight Normalization 状态保存在 PyTorch Parametrizations 的 `original0`／`original1` 键下；Bundled Transformers `4.36.2` 在 Windows RVC 路径中忽略这些键，因而产生静音的 Content Feature。生产 `pytorch_model.bin` 是本机精确的键 Schema 转换：只把适用键重命名为 `weight_g`／`weight_v`，没有改变任何 Tensor 值。转换后的文件为 189,205,793 字节，与另外两份未改变的 HuBERT 配置共同形成上表记录的目录身份。该兼容序列化不会新增再分发授权，也不会改变底层模型来源。

生产布局使用被忽略的 `models/cache/rvc-v2-40k/` 保存已审核 Source、HuBERT 与 RMVPE 资产，以及被忽略的 `models/weights/rvc/elysia-v2-40k/model.pth` 和 `model.index` 保存私有角色模型。`model.pth` 是已选训练导出 `elysia_rvc_50e_e50_s19850.pth` 的规范生产副本；对应 Index 在复制到规范私有路径前生成于 `source/logs/elysia_rvc_50e/added_IVF256_Flat_nprobe_1_elysia_rvc_50e_v2.index`。封闭 Adapter 禁用网络、要求 CUDA，并只暴露受限的半音移调。不可变推理 Profile 固定为 RVC v2 40 kHz、speaker `0`、RMVPE、Index Rate `0.00`、Protect `0.33`、RMS Mix Rate `0.25`、upstream seed `114514`、deterministic cuDNN selection 与 cuBLAS workspace `:4096:8`；固定 RVC CUDA cumulative-sum 路径没有 PyTorch strict deterministic algorithms 模式实现，所以该严格模式保持关闭。固定的零 Index Rate 同时禁用近邻混合，并令 Protect 的 Feature Blend 没有实际差异，因此不能把 Protect 描述为无声内容保证。即使不混入近邻，Index 仍须通过固定身份验证。Parent 在启动前验证 Runtime Tree 与私有资产。当前安装包不包含任何上述文件，会 Fail Closed，不会下载模型或切换到其他转换引擎。已记录的 HuBERT/RMVPE 身份只认证本机字节；其再分发条款与底层训练数据来源仍需独立审查。

RVC 转换 Scratch 现在只存在于 Main 拥有的精确 UUID Job Directory。旧外部 Runtime 的 `raw/` 与 `results/` Scratch 约定不再被读取、扫描或删除。这会把清理范围收紧到既有的受管 Song Cover Job 边界，避免把未知 Runtime 文件误当作应用所有数据。

完整 Runtime Executable/Tree 身份、临时数据行为与测试记录见 [`docs/15-SONG-COVER.md`](./docs/15-SONG-COVER.md)。

## Elysia GPT-SoVITS v2 Provenance / Elysia GPT-SoVITS v2 来源

本地模型包随附的 `README.txt` 标注了以下信息：

- **Character / 角色**: Elysia from *Honkai Impact 3rd* / 《崩坏3》爱莉希雅
- **Voice performer named by the pack / 模型包标注的角色配音**: Yan Ning / 宴宁
- **Model publisher / 模型发布者**: `TinyLight微光小明`
- **Integration-pack provider / 整合包提供者**: `花儿不哭`
- **Matching publication page / 内容吻合的发布页**: [BV1Yz421a7HW](https://www.bilibili.com/video/BV1Yz421a7HW/)
- **Integration tutorial / 整合包教程**: `BV12g4y1m7Uw`
- **Upstream software / 上游软件**: [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)

本地目录包含 `.ckpt` 与 `.pth` 模型权重、`.wav` 参考音频和随附说明。上述 Bilibili 发布页的简介与随附说明高度吻合；下列摘要只固定 2026-09-14 技术评估实际使用的本地文件。发布者没有提供可对应核验的权威摘要，因此这些 Hash **不能**证明本地文件就是发布页中的精确版本，也不能证明授权或发布者拥有相应权利。页面同时标有“未经作者授权，禁止转载”。

| Local evaluation selection / 本地评估选择 | Bytes | SHA-256 | Technical metadata / 技术元数据 |
| --- | ---: | --- | --- |
| Selected GPT v2 checkpoint / 所选 GPT v2 Checkpoint | 155,312,566 | `C73957C7815EA36A345678DF6DDEDA9FFD2B03498D17802BF54502414C9D887B` | `【GPT2.0】Elysia-e20.ckpt`; technical candidate only / 仅为技术候选 |
| Selected SoVITS v2 checkpoint / 所选 SoVITS v2 Checkpoint | 85,007,488 | `D095458023374D2BB7B657FF622A010504F8825B7188AB0582B91EF6412CD9CE` | `【GPT2.0】Elysia_e24_s13080.pth`; technical candidate only / 仅为技术候选 |
| `neutral` reference / 中性参考 | 511,604 | `D5E62E6FF2158A729DFEDB2DD8648D3919B9FA962C02C49CADAED944FA8388A7` | WAV PCM, 44.1 kHz, mono, 16-bit, 255,780 frames, 5.800000 s |
| `happy` reference / 开心参考 | 411,672 | `C42EEE79E91B847FF5E54E4B5F7C46751CD0CDB3E18223373F385A67A42D277E` | WAV PCM, 44.1 kHz, mono, 16-bit, 205,814 frames, 4.666984 s |
| `sad` reference / 悲伤参考 | 707,408 | `31AB2CC595208B7D86852945D729C4C89C990BAA10BB50C51DDED911EE49DCB0` | WAV PCM, 44.1 kHz, mono, 16-bit, 353,682 frames, 8.020000 s |
| `caring` reference / 关怀参考 | 711,200 | `548E252FAC86229FD68BBF1A702689540E1446329C82E5185C6184D6EF8CE981` | WAV PCM, 44.1 kHz, mono, 16-bit, 355,578 frames, 8.062993 s |
| `moved` reference / 感动参考 | 766,766 | `723AF5FD855B9DDA3404635B4127730C42E0776FA878C6FB18FA9E22DBD4ED6A` | WAV PCM, 44.1 kHz, mono, 16-bit, 383,361 frames, 8.692993 s |
| `playful` reference / 调皮参考 | 605,536 | `96914A59AE5F43E67E290D4BF8C88601C5B2ECD0C337899284C8C032870FB09E` | WAV PCM, 44.1 kHz, mono, 16-bit, 302,746 frames, 6.864989 s |
| `affectionate` reference / 撒娇参考 | 337,320 | `5F84EC94CB6DFFECD919BE3BFA3422A37F48FB32649C759E76E251311236A64C` | WAV PCM, 44.1 kHz, mono, 16-bit, 168,638 frames, 3.823991 s |
| `teasing` reference / 撩拨参考 | 373,306 | `346713B34CC019E4048BE515345F54E5AADB01EE35EB034F00A2A57F1D9A37E7` | WAV PCM, 44.1 kHz, mono, 16-bit, 186,631 frames, 4.231995 s |
| `serious` reference / 严肃参考 | 661,102 | `1214D3C63FA1290CBED6DB754606EC57956719612094268E94C9155A51888EF1` | WAV PCM, 44.1 kHz, mono, 16-bit, 330,529 frames, 7.494989 s |
| `surprised` reference / 惊喜参考 | 359,810 | `D5C2E061CBE6A87D1830E0B30F9AEAB71E1DED976620EED222637DFFE7313A8F` | WAV PCM, 44.1 kHz, mono, 16-bit, 179,883 frames, 4.078980 s |

本次验收选择较高 Epoch/Step 文件，不代表其音质最佳、来源已经核实或已经获得授权。参考音频的完整文件名和准确 Prompt 文本只保留在被忽略的本机 Catalog 中，因为它们本身可能暴露受版权保护的台词和本地路径。

`models/weights/` 忽略规则使这些文件当前不在 Git Index 中，但 `.gitignore` 本身不能阻止强制添加、历史提交或打包脚本意外复制。每次发布都必须核对实际文件清单。

### Terms stated by the supplied notice / 随附说明写明的条件

随附说明希望二创作品注明软件与语音模型来源，并明确禁止盗卖、商业用途、R18 用途及违法用途。

在授权仍未核实的情况下，Elysia AI 采用以下项目内部隔离策略。该策略只用于降低风险，**不代表本地非商业使用已经获得许可**：

- local, personal, non-commercial evaluation only / 仅限本地、个人、非商业评估；
- no resale or paid distribution / 不得盗卖或付费分发；
- no R18 or unlawful use / 不得用于 R18 或违法内容；
- retain the model and software attribution / 保留模型与软件来源署名；
- do not imply endorsement by the publisher, performer, miHoYo, or HoYoverse / 不得暗示模型发布者、配音演员、米哈游或 HoYoverse 对本项目提供背书。

### Authorization gap / 授权缺口

本地随附说明本身没有提供模型原始发布链接，也没有明确授予本仓库再分发模型权重或参考音频的权利。现在虽已找到内容吻合的 Bilibili 发布页，但现有材料仍未证明发布者有权授予相关角色、录音或表演权。

因此，本地权重与音频不得：

- committed to this repository / 提交到本仓库；
- attached to a GitHub Release or shared archive / 上传到 GitHub Release 或共享压缩包；
- bundled into an installer or container image / 打进安装包或容器镜像；
- mirrored, sold, sublicensed, or presented as project-owned assets / 镜像、售卖、转授权或宣称为本项目自有素材。

未来如需分发，维护者必须取得并保留能够明确覆盖具体素材版本、再分发、修改、署名、商业范围以及相关声音表演权的许可记录。

### Current authorization decision / 当前授权决定

`default` 爱莉希雅 Voice Profile 继续归类为 `local-evaluation-only`。上文记录的随附说明、内容吻合的发布页、本地文件精确摘要和署名主体属于来源证据，但不构成再分发授权。本机 Opt-in 开关只允许技术评估，不能改变素材的权利状态。在留存充分许可之前，Checkpoint 与参考录音必须继续排除在 Git、Release、安装包、Container、镜像和共享诊断压缩包之外。

本仓库通过 `scripts/check_distribution_assets.py` 执行上述决定。CI 会检查 Git Index、冻结完整且已审查的 Electron Builder 配置、构建 Windows Package，并同时扫描 Unpacked Tree 与 `asar list` 清单；准备发行时仍必须对实际候选产物重复该扫描。该检查器是预防性工程控制，不是法律结论。

## GPT-SoVITS Software Boundary / GPT-SoVITS 软件边界

GPT-SoVITS 上游仓库以 [MIT License](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/LICENSE) 发布其软件。该软件许可证只适用于其覆盖的上游代码，**不会自动授权**第三方 Checkpoint、训练数据、参考音频、角色 IP 或声音表演。

2026-09-14 的 Windows 验收由维护者显式下载上游链接的 [`GPT-SoVITS-v2-240821.7z`](https://huggingface.co/lj1995/GPT-SoVITS-windows-package/blob/42b55dd0c41f0d23218f8f7c1e9e0636a0e386e8/GPT-SoVITS-v2-240821.7z)，固定仓库 Revision 为 `42b55dd0c41f0d23218f8f7c1e9e0636a0e386e8`。远端文件大小为 5,744,891,255 bytes，已验证 SHA-256 为 `9D9BA79DE6ACA0CF28A3635CCB1DBBB08B6AEF362C4352E32FAD99BB49E3000A`。它解压到被忽略的 `models/cache/GPT-SoVITS-v2-240821/`；下载压缩包在 Hash 与解压验证后已经删除。本机 Runtime 在 Smoke 中以 CUDA Half Precision 加载 v2。

解压包内包含署名 RVC-Boss 的 MIT `LICENSE`，但该许可证只覆盖其能够合法适用的软件，不能扩大到包内每个依赖或预训练模型，也不能覆盖 Elysia Checkpoint、训练数据、参考音频、角色 IP、录音或表演。Elysia AI 不把该 Runtime 纳入仓库，也不声称整份整合包或本地 Elysia 声音包采用 MIT 许可证。解压 Runtime、本机推理 YAML、模型权重与参考音频继续被忽略，不进入 Git、Release、安装包或容器。

## Character Reference Corpus / 角色参考语料

`data/characters/elysia_character_reference_zh.md` 包含角色背景资料以及大量语录和语音转写文本。

背景部分标注来源为[萌娘百科“爱莉希雅”条目](https://zh.moegirl.org.cn/%E7%88%B1%E8%8E%89%E5%B8%8C%E9%9B%85)，并注明所引页面文字默认采用 [CC BY-NC-SA 3.0 中国大陆协议](https://creativecommons.org/licenses/by-nc-sa/3.0/cn/)。汇总文件自述保留 629 行背景资料，把 171 条精选语录与 2,169 条语音转写合并，并在精确文本去重后得到 2,318 条语录；但它没有记录导入页面的版本、贡献者历史或逐条来源。页面级许可证说明不能被自动扩大到汇总文件中的每条语录、官方游戏台词、语音转写或其他例外素材。

该文件已经被 Git 跟踪，因此每次 Clone 或共享仓库都会分发副本。来源未解决是**当前分发风险**，而不只是未来正式发布时的问题。维护者应尽快逐项审查，并删除无法证明可复用的内容、改写为原创摘要，或取得充分许可。将来从工作树移除该文件，也不会自动清除 Git 历史中的既有副本。

`data/characters/elysia_character_analysis_zh.md` 是按身份、人格、价值观、动机、思维、情绪、语言、交流、关系、偏好、情境反应与还原边界整理的原创研究总结；`core/elysia_system_prompt_zh.md` 则是应用实际加载的独立人格合同。两者不会把本地 2,318 条语录或网络剧情全文复制进每次模型请求。其原创表达仍不改变底层角色、剧情、名称与其他第三方权利的归属。

## Underlying Character and Voice Rights / 底层角色与声音权利

爱莉希雅、《崩坏3》以及相关名称、角色设计、剧情、美术、音频与商标归各自权利人所有；其中适用的权利包括 HoYoverse / 米哈游的权利，声音与表演还可能涉及配音演员及其他主体的权利。

Elysia AI 是非官方粉丝开发项目，与 HoYoverse / 米哈游以及上述模型、声音贡献者没有隶属、合作、赞助或背书关系。

2026-09-23 已重新核对 HoYoverse 当前的[同人内容帮助说明](https://support.hoyoverse.com/hc/en-us/articles/51005649400729-What-are-the-guidelines-for-creating-and-selling-fan-made-content)；HoYoverse 页面显示其更新于 2025-10-13。该页链接的是特定产品的同人创作指南，并不是再分发本《Honkai Impact 3rd》声音包的许可。此前记录的[《Honkai Impact 3rd》素材与同人创作指南](https://www.hoyolab.com/article/1463874)仍只作为边界参考，不是模型、录音或表演权许可；两者都没有补足上文所述授权缺口。

用户必须自行核对适用于所在地区、素材来源和具体用途的最新规则。本文件不能替代该核对过程，也不能替代相关权利人的直接许可。

## Local Handling Rules / 本地处理规则

贡献者与维护者必须：

1. 保持 `models/cache/`、`models/weights/`、`models/blobs/`、`models/manifests/` 与 `models/manifests-v2/` 被忽略并仅存本机；每次检查实际发行清单，不能只依赖忽略规则。
2. 使用前审查每个新增模型、数据集、参考音频、图片、字体和角色语料。
3. 记录创作者、原始链接、版本或摘要、适用条款和授权证据。
4. 除非书面许可明确允许，否则将第三方素材排除在源码许可和安装包之外。
5. 来源或授权争议无法解决时，及时移除素材。

发布前必须执行仓库策略检查，并对实际候选包进行目录、ASAR 清单及解包字节审计。完整命令见 [桌面开发指南](desktop/README.md#package-and-audit)；不能只检查 Git 或依赖 `.gitignore`。

## Bundled Runtime Components / 随安装包分发的运行组件

2026-10-22 的 Windows 候选包包含以下运行组件；大型生成模型、私人声音模型、参考录音及工作区数据仍被排除。构建配置与固定下载摘要见 [backend-runtime.json](desktop/backend-runtime.json)，每个成品文件另有 `resources/backend/distribution-manifest.json` 摘要清单。

- **CPython 3.14.9 x64**：来自 [Python 官方发行页](https://www.python.org/downloads/release/python-3149/) 的 embeddable ZIP，SHA-256 为 `3c01d7cfc666f746a6f064f60fb2156ab0ec464d3d738ab796371a46a9bb24a2`。原始 `LICENSE.txt` 随解释器保留。
- **Microsoft Visual C++ Runtime 14.51.36247.0 x64**：从 Microsoft 官方 Redistributable 提取 `msvcp140.dll` 与 `msvcp140_1.dll`，与 CPython 自带同版本 `vcruntime140.dll` / `vcruntime140_1.dll` 一起放在解释器目录。下载包 SHA-256 为 `843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c`；本次 Windows Authenticode 检查为 `Valid`，签名人为 Microsoft Corporation。构建不执行该安装程序。随包的 `MICROSOFT-VC-RUNTIME-NOTICE.txt` 记录固定来源，`MICROSOFT-VC-RUNTIME-LICENSE.txt` 保留[官方许可](https://visualstudio.microsoft.com/license-terms/vs2026-ga-visualcpp-v14-redist-runtime/)文档的文字。
- **faster-whisper 1.2.1 / Silero VAD**：该 wheel 的轻量语音活动检测文件 `python/Lib/site-packages/faster_whisper/assets/silero_vad_v6.onnx` 随运行库分发，SHA-256 为 `4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2`。这不是 Whisper 转写模型或声音克隆权重。库的许可与其他 Python wheel 的许可保留在 `*.dist-info/licenses/`；该单一 VAD 路径和摘要由分发检查器明确允许，其他模型扩展名仍默认拒绝。

这些组件有各自许可证，不改变下面的项目源代码许可边界。

## Source-Code License Boundary / 源代码许可证边界

本仓库目前没有根级 `LICENSE` 文件。本说明不授予 Elysia AI 源代码许可。将来即使添加源码许可证，也不得把它解释为涵盖角色 IP、角色语料、模型权重、参考音频、Ollama 模型或其他第三方素材，除非许可证明确写明且本项目确实有权作出该授权。

## Corrections / 更正

如果你是相关权利人，或能提供权威的来源与授权信息，请通过 GitHub 联系仓库维护者以更正本说明。署名不代表权利被放弃；提出更正时，也不要求公开披露私密授权证据。

本文件仅用于信息说明，不构成法律意见。
