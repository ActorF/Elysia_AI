# Model & Voice Asset Notice / 模型与语音素材说明

> Last reviewed / 最近核对：2026-10-01

## Purpose and Status / 用途与性质

This document records the known provenance, local handling rules, and current authorization gaps for model weights, reference audio, and character material used or evaluated with Elysia AI.

本文记录 Elysia AI 已使用或评估过的模型权重、参考音频与角色资料的已知来源、本地处理规则和当前授权缺口。

> [!IMPORTANT]
>
> This is an **asset and rights notice**, not an open-source license and not a grant of third-party rights. Unverified third-party assets remain local by default. Any maintainer-approved exception must record its source category, intended scope, applicable rights-holder guidance, attribution, license exclusion, and removal risk.
>
> 本文件是**素材与权利告知**，不是开源许可证，也不代表本项目能够转授第三方权利。无法核实的第三方素材默认只留本机；维护者决定公开的例外必须记录素材类别、用途范围、权利人规则、署名、许可证排除和移除风险。

---

## Asset Inventory / 素材范围

| Location / 路径 | Repository status / 仓库状态 | Current treatment / 当前处理方式 |
| --- | --- | --- |
| `models/weights/gpt-sovits/elysia-v2/` | Ignored and currently outside the Git index / 已忽略且当前不在 Git Index 中 | Local-only GPT-SoVITS weights and reference audio; every package/release must independently verify exclusion / 本地 GPT-SoVITS 权重与参考音频；每次打包和发布都须独立确认排除 |
| `models/cache/GPT-SoVITS-v2-240821/` | Ignored local external runtime / 被忽略的本地外部 Runtime | Extracted upstream Windows evaluation package and local inference YAML; not Elysia AI source and never part of Git, releases, installers, or containers / 解压后的上游 Windows 评估包和本机推理 YAML；不属于 Elysia AI 源码，也不进入 Git、Release、安装包或容器 |
| `models/blobs/` and `models/manifests/` | Ignored and currently outside the Git index / 已忽略且当前不在 Git Index 中 | Ollama-managed local models; the document-index library pins `qwen3-embedding:0.6b`, but the repository does not download or redistribute it, and each upstream model has its own terms / Ollama 管理的本地模型；文档索引库固定 `qwen3-embedding:0.6b`，但本仓库不下载或再分发它，各上游模型仍各自遵循其条款 |
| `models/cache/faster-whisper/` and `models/weights/faster-whisper/` | Ignored and currently outside the Git index / 已忽略且当前不在 Git Index 中 | Local speech-recognition models only; the adapter requires an explicit complete directory and never bundles or implicitly downloads weights / 仅存本地的语音识别模型；Adapter 要求明确、完整的目录，不打包也不隐式下载权重 |
| `data/characters/elysia_character_reference_zh.md` | Tracked / 已跟踪 | Character background and quotations requiring separate source review / 需要单独审查来源的角色背景与语录 |
| `desktop/public/elysia-icon.png` and `desktop/assets/elysia-icon.ico` | Tracked third-party branding / 已跟踪的第三方品牌素材 | Derived from an official *Honkai Impact 3rd* Elysia signet and included at the project owner's express direction for this unofficial, non-commercial fan project; © HoYoverse / miHoYo, excluded from every source-code license, no endorsement implied, and removable on rights-holder request / 由《崩坏3》爱莉希雅官方刻印制作，并按项目所有者明确决定用于本非官方、非商业粉丝项目；© HoYoverse / miHoYo，不属于任何源码许可证，不代表官方背书，权利人要求时应移除 |
| `desktop/public/character/elysia-portrait.png` | Tracked reviewed generated fan artwork / 已跟踪并完成固定审核的生成式同人立绘 | Generated with OpenAI's built-in image-generation tool from Elysia reference images supplied by the project owner; distributed only as part of this unofficial, non-commercial fan project, excluded from every source-code license, and authenticated by the distribution audit / 使用 OpenAI 内置图像生成工具并参考项目所有者提供的爱莉希雅图片生成；仅随本非官方、非商业粉丝项目分发，不属于任何源码许可证，并由分发审计验证固定内容 |
| `data/characters/elysia-2dArt/` | Tracked generated review pack (18 PNG files plus provenance/revision notes) / 已跟踪的生成式审阅包（18 个 PNG 及来源、修订记录） | Repository review material for this unofficial fan project, excluded from every source-code license; `02-activity-states.png`, `03-expression-atlas.png`, and `04-facial-rig-atlas.png` have byte-identical runtime copies selected for the installer, while the remaining review assets stay outside package input / 本非官方粉丝项目的仓库审阅资料，不属于任何源码许可证；`02-activity-states.png`、`03-expression-atlas.png` 与 `04-facial-rig-atlas.png` 以逐字节相同运行时副本获选进入安装包，其余审阅素材不属于打包输入 |
| `desktop/public/character/elysia-state-atlas.png` | Tracked reviewed generated state atlas / 已跟踪并完成固定审核的生成式状态图集 | Byte-identical runtime copy of `data/characters/elysia-2dArt/02-activity-states.png`; the first seven cells drive the closed in-app Character State contract, while the eighth success cell is unused; same fan-project, source-license exclusion, rights boundary, and distribution authentication apply / `data/characters/elysia-2dArt/02-activity-states.png` 的逐字节相同运行时副本；前七格驱动应用内封闭角色状态，第八格 success 不使用；同样受非商业粉丝项目、源码许可排除、权利边界与分发审计约束 |
| `desktop/public/character/elysia-expression-atlas.png` | Tracked reviewed generated expression atlas / 已跟踪并完成固定审核的生成式表情图集 | Byte-identical runtime copy of `data/characters/elysia-2dArt/03-expression-atlas.png`; only the user-controlled `neutral / happy / sad` closed mapping selects runtime cells, and model output cannot select arbitrary expressions / `data/characters/elysia-2dArt/03-expression-atlas.png` 的逐字节相同运行时副本；运行时仅由用户控制的 `neutral / happy / sad` 闭集映射选择格子，模型输出不能选择任意表情 |
| `desktop/public/character/elysia-speech-atlas.png` | Tracked reviewed generated speech atlas / 已跟踪并完成固定审核的生成式嘴型图集 | Byte-identical runtime copy of `data/characters/elysia-2dArt/04-facial-rig-atlas.png`; runtime uses only the first four cells of the first band as `closed / small / medium / wide` RMS-amplitude cues, not as phoneme detection or Live2D / `data/characters/elysia-2dArt/04-facial-rig-atlas.png` 的逐字节相同运行时副本；运行时只把第一带前四格作为 `closed / small / medium / wide` RMS 振幅提示，不代表音素检测或 Live2D |

### Reviewed generated in-app character artwork / 已审核的生成式应用内角色图

The portrait at `desktop/public/character/elysia-portrait.png` was generated on 2026-09-30 with OpenAI's built-in image-generation tool. The generation used three Elysia reference images supplied directly by the project owner plus one pre-existing Elysia portrait from the owner's local asset collection. Those four reference files are not part of this repository or its packages, and this notice does not claim or grant rights in them. The reviewed output is exactly **2,223,154 bytes**, with SHA-256 **`359c2620ac5286cc6c77d533e5c53d1b63fd0fe08fdf42f5952136b7c5bcafb2`**. CI rejects a missing file, a different byte length, or any content mutation at the reviewed repository path. After packaging, it also requires exactly one `dist/character/elysia-portrait.png` ASAR entry and authenticates bytes extracted from that entry against the same size and digest, so a source or package replacement cannot silently inherit this review.

`desktop/public/character/elysia-portrait.png` 中的立绘于 2026-09-30 使用 OpenAI 内置图像生成工具生成，生成时参考了项目所有者本次直接提供的三张爱莉希雅图片，以及所有者本机素材集内原有的一张爱莉希雅立绘。这四张参考文件不属于本仓库或安装包，本说明也不主张或授予对参考文件的权利。审核后的输出固定为 **2,223,154 字节**，SHA-256 为 **`359c2620ac5286cc6c77d533e5c53d1b63fd0fe08fdf42f5952136b7c5bcafb2`**。CI 会拒绝仓库审核路径上的文件缺失、长度变化或任意内容变化；打包后还要求 ASAR 中恰好出现一次 `dist/character/elysia-portrait.png`，并把该条目抽取出的实际字节与同一长度和摘要比对，避免源码或安装包内的替换文件自动继承本次审核结论。

The reviewed state atlas was generated on 2026-10-01 with OpenAI's built-in image-generation tool from Elysia angle, face, costume, and style references supplied by the project owner. Its full generation and correction record is stored beside the source review image under `data/characters/elysia-2dArt/`. The runtime file is a byte-identical copy of `02-activity-states.png`: exactly **2,303,963 bytes**, with SHA-256 **`54eb2525673c2a849819be10eb88eb2f670eb1911e86fd154e69b578cbb4c25c`**. The first seven row-major cells represent `idle`, `listening`, `thinking`, `speaking`, `working`, `waiting_approval`, and `error`; the eighth success/celebration cell is intentionally outside the closed runtime state set. CI pins the repository file, requires exactly one `dist/character/elysia-state-atlas.png` ASAR entry, and verifies bytes extracted from that entry.

审核状态图集于 2026-10-01 使用 OpenAI 内置图像生成工具制作，参考了项目所有者提供的爱莉希雅角度、面部、服装和风格图片；完整生成与定点修复记录保存在 `data/characters/elysia-2dArt/` 的源审阅图旁。运行时文件与 `02-activity-states.png` 逐字节相同，固定为 **2,303,963 字节**，SHA-256 为 **`54eb2525673c2a849819be10eb88eb2f670eb1911e86fd154e69b578cbb4c25c`**。按行读取的前七格分别表示 `idle`、`listening`、`thinking`、`speaking`、`working`、`waiting_approval` 和 `error`；第八格成功/庆祝明确不属于运行时封闭状态。CI 会固定仓库文件，要求 ASAR 中恰好出现一次 `dist/character/elysia-state-atlas.png`，并验证从该条目抽取的实际字节。

The reviewed expression atlas shares the same 2026-10-01 generation references and the correction record stored beside `data/characters/elysia-2dArt/03-expression-atlas.png`. Its byte-identical runtime copy at `desktop/public/character/elysia-expression-atlas.png` is exactly **2,500,647 bytes**, with SHA-256 **`fbf7a515b2651b3a881cf9b838a5605c316befd0b174dde046780d8e441d7f93`**. Runtime selection is restricted to the user setting `neutral`, `happy`, or `sad`; the same active setting selects the local TTS reference and the reviewed static expression after a Backend restart. No model response can name an emotion, file, atlas cell, or animation. CI pins the repository file, requires exactly one `dist/character/elysia-expression-atlas.png` ASAR entry, and verifies its extracted bytes.

审核表情图集沿用 2026-10-01 的同一组生成参考，完整修订记录保存在 `data/characters/elysia-2dArt/03-expression-atlas.png` 旁。`desktop/public/character/elysia-expression-atlas.png` 是其逐字节相同运行时副本，固定为 **2,500,647 字节**，SHA-256 为 **`fbf7a515b2651b3a881cf9b838a5605c316befd0b174dde046780d8e441d7f93`**。运行时选择严格限于用户设置的 `neutral`、`happy` 或 `sad`；Backend 成功重启后，同一 Active 设置同时选择本地 TTS 参考与审核静态表情。模型回复不能命名情绪、文件、图集格或动画。CI 会固定仓库文件，要求 ASAR 中恰好出现一次 `dist/character/elysia-expression-atlas.png`，并验证抽取字节。

The reviewed facial-rig atlas also shares the 2026-10-01 references and has its generation and targeted mouth-correction record beside `data/characters/elysia-2dArt/04-facial-rig-atlas.png`. Its byte-identical runtime copy at `desktop/public/character/elysia-speech-atlas.png` is exactly **2,054,767 bytes**, with SHA-256 **`21bf4496acc4417d491ff0163c9ee1d38593e376ca25a3d452fd393c6157f9ab`**. Runtime uses only the first four row-major cells of the first band as `closed`, `small`, `medium`, and `wide` amplitude levels. Trusted Preload derives them from real Web Audio RMS at no more than 20 Hz; it does not infer phonemes and is not a Live2D rig. Still and OS Reduced Motion prohibit the animated mouth path. CI pins the repository file, requires exactly one `dist/character/elysia-speech-atlas.png` ASAR entry, and verifies its extracted bytes.

审核面部绑定图集同样沿用 2026-10-01 的参考，生成与定点嘴部修复记录保存在 `data/characters/elysia-2dArt/04-facial-rig-atlas.png` 旁。`desktop/public/character/elysia-speech-atlas.png` 是其逐字节相同运行时副本，固定为 **2,054,767 字节**，SHA-256 为 **`21bf4496acc4417d491ff0163c9ee1d38593e376ca25a3d452fd393c6157f9ab`**。运行时只按行使用第一带前四格作为 `closed`、`small`、`medium` 与 `wide` 振幅档位；可信 Preload 以不高于 20 Hz 的真实 Web Audio RMS 推导这些档位，不推断音素，也不是 Live2D 绑定。Still 与系统 Reduced Motion 禁止嘴型动画路径。CI 会固定仓库文件，要求 ASAR 中恰好出现一次 `dist/character/elysia-speech-atlas.png`，并验证抽取字节。

The generated outputs are still recognizably based on Elysia from *Honkai Impact 3rd*. AI generation does not transfer or erase the underlying character, design, name, or trademark rights, which remain with HoYoverse / miHoYo and other applicable rights holders. Elysia AI claims no affiliation or endorsement, does not sell or separately license these images, excludes them from any present or future source-code license, and will remove them upon a valid rights-holder request. Any replacement or derivative must receive a new provenance and distribution review and update the pinned digest and length deliberately.

这些生成结果仍明确以《崩坏3》角色爱莉希雅为基础。AI 生成不会转移或消除底层角色、设计、名称或商标权利；相关权利仍归 HoYoverse / 米哈游及其他适用权利人所有。Elysia AI 不声称与官方有关联或获得背书，不售卖或单独授权这些图片，并将其排除在当前及未来的任何源码许可证之外；若收到有效的权利人要求，将移除相关素材。任何替换或衍生版本都必须重新完成来源与分发审核，并明确更新固定摘要与字节长度。

The independently started loopback smoke path remains an opt-in, Python-only adapter and reports `service_binding_unverified`, because the upstream API cannot attest which checkpoints its external process actually loaded. Separately, Desktop sentence speech now uses an Elysia-owned managed worker plus a private Electron transport and playback chain. A real local evaluation loaded the selected Elysia GPT/SoVITS checkpoints and returned valid audio through that managed path. This proves technical interoperability on that machine only: it does not establish production readiness, grant distribution rights, or change any asset-authorization gap described below.

单独启动的 Loopback Smoke 路径仍是默认关闭、仅限 Python 的 Adapter；由于上游 API 无法证明其外部进程实际加载了哪些 Checkpoint，该路径只报告 `service_binding_unverified`。与它分开，Desktop 分句语音现已使用由 Elysia 持有的受管 Worker，以及私有 Electron 传输与播放链。本机真实评估通过该受管路径加载所选 Elysia GPT/SoVITS Checkpoint 并返回有效音频。这只证明该机器上的技术互操作，不代表生产就绪，不授予分发权，也不会消除下述任何素材授权缺口。

---

## Faster-Whisper Software and Model Boundary / Faster-Whisper 软件与模型边界

The optional adapter targets [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) and keeps its imports out of the base text-Chat runtime. The upstream Faster-Whisper source is published under the [MIT License](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE). That software license covers the upstream code, not arbitrary recordings, transcripts, converted models, or any unrelated character and voice assets.

可选 Adapter 面向 [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)，其第三方 Import 不会进入基础文字 Chat Runtime。Faster-Whisper 上游源码采用 [MIT License](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)；该软件许可证只覆盖相应上游代码，不会自动覆盖任意录音、转写文本、转换模型或无关的角色与声音素材。

The first recommended local evaluation candidate is the multilingual [`Systran/faster-whisper-small`](https://huggingface.co/Systran/faster-whisper-small) model. The local CPU acceptance run on 2026-09-14 used repository revision `536b0662742c02347bc0e980a01041f333bce120`; its `model.bin` SHA-256 is `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`. The pinned snapshot's model card labels it MIT, but the downloaded snapshot contains no separate `LICENSE` file. The local copy remains Git-ignored and is not part of this repository or installer. Future changes to the upstream card must not be assumed to retroactively describe this pinned copy.

首个建议的本地评估候选是多语言 [`Systran/faster-whisper-small`](https://huggingface.co/Systran/faster-whisper-small)。2026-09-14 的本地 CPU 验收使用仓库 Revision `536b0662742c02347bc0e980a01041f333bce120`，其中 `model.bin` 的 SHA-256 为 `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`。该固定 Snapshot 的 Model Card 标注为 MIT，但下载内容没有独立 `LICENSE` 文件；本地副本继续被 Git 忽略，不属于本仓库或安装包。不得假设上游页面未来的修改会自动适用于这个固定副本。

The adapter accepts only an explicit absolute local model directory containing the runtime configuration, model binary, and tokenizer. It passes `local_files_only=True`, rejects Git LFS pointer files, and never resolves a model alias into a hidden weight download. Desktop Settings map a closed model-name allowlist to that directory and do not expose a path or download action. Model installation remains a separate user-triggered operation, and every package/release must verify that local weights and caches remain excluded.

Adapter 只接受明确的绝对本地模型目录，并要求其中存在 Runtime 配置、模型二进制与 Tokenizer；它会传入 `local_files_only=True`、拒绝 Git LFS Pointer，且不会把模型别名解析为隐藏的权重下载。Desktop Settings 只会把闭集模型名称映射到这个目录，不暴露路径或下载动作。模型安装仍是独立的用户触发操作，每次打包和发布都必须核对本地权重与缓存仍被排除。

---

## Qwen3 Embedding via Ollama / 通过 Ollama 使用 Qwen3 Embedding

The standalone document-index library is fixed to the Ollama tag `qwen3-embedding:0.6b`. The [official Ollama library page](https://ollama.com/library/qwen3-embedding) is the public display source for the model family, available tags, and high-level size/context information. The full manifest digest below records the exact registry response reviewed for this implementation on 2026-09-25; it is an identity anchor for that response, not a promise that a mutable registry tag will remain unchanged. The following values identify the exact artifact contract accepted by the current implementation; they do not place the artifact in this repository:

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

The [upstream Qwen3-Embedding-0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) labels the upstream Qwen model Apache-2.0 and states the 32K, multilingual, and code capabilities summarized above. That Apache-2.0 statement applies to the upstream model as described by its model card. It must not be read as a blanket license for the Ollama runtime, every layer or metadata object in the quantized Ollama manifest, unrelated dependencies, or any other Elysia AI asset.

[Qwen3-Embedding-0.6B 上游 Model Card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) 把上游 Qwen 模型标注为 Apache-2.0，并声明上表概述的 32K、多语言和代码能力。该 Apache-2.0 说明只适用于 Model Card 所描述的上游模型；不得把它解释为对 Ollama Runtime、量化 Ollama Manifest 中每个 Layer 或 Metadata Object、无关依赖，或 Elysia AI 其他素材的概括授权。

The manifest and model-layer digests, byte size, and `Q8_0` label are technical provenance anchors for the exact Ollama quantized artifact expected by this version. Unless an authoritative publisher or quantizer record is separately retained, these observations do not by themselves prove who produced every manifest component, establish a complete supply chain, or grant redistribution rights. At runtime the adapter verifies the exact tag and full manifest digest before and after each batch; the model-layer digest, size, and quantization are documented provenance beneath that manifest, not fields independently re-attested through another Ollama API. The implementation does not download, mirror, bundle, commit, or redistribute the artifact.

Manifest 与模型 Layer 摘要、Byte 大小和 `Q8_0` 标记，只是当前版本期待的精确 Ollama 量化 Artifact 的技术来源锚点。除非另行留存权威发布者或量化者记录，这些观察本身不能证明每个 Manifest 组件由谁制作，不能建立完整供应链，也不授予再分发权。运行时 Adapter 会在每个 Batch 前后验证精确 Tag 与完整 Manifest Digest；Model-layer Digest、Size 与 Quantization 是该 Manifest 之下的文档化 Provenance，不是通过另一 Ollama API 独立重新证明的字段。实现不下载、镜像、打包、提交或再分发该 Artifact。

Installation is an explicit, optional local action:

安装必须是用户显式执行的可选本地操作：

```bat
ollama pull qwen3-embedding:0.6b
```

This model is not required for basic text Chat. Neither the application nor its tests trigger `ollama pull`; model blobs and manifests remain in Ollama-managed, ignored local storage and must stay out of Git, releases, installers, containers, and diagnostic archives.

基础文字 Chat 不需要该模型。应用与测试都不会触发 `ollama pull`；模型 Blob 与 Manifest 继续保留在 Ollama 管理、被 Git 忽略的本地存储中，不得进入 Git、Release、安装包、Container 或诊断归档。

The document-retrieval library defines an optional reranker protocol, but this repository currently bundles, downloads, or selects no reranker model. Enabling a future implementation requires a separate identity, license, provenance, local-storage, and distribution review; it must not reuse this Qwen embedding entry as authorization for another artifact.

文档检索库定义了可选的 Reranker Protocol，但本仓库目前没有打包、下载或选定任何 Reranker 模型。未来启用具体实现前，必须另行审查其身份、许可证、来源、本地存储与分发边界；不得把本节 Qwen Embedding 的记录视为其他 Artifact 的授权。

---

## Elysia GPT-SoVITS v2 Provenance / Elysia GPT-SoVITS v2 来源

The `README.txt` supplied with the local pack identifies the following parties and material:

本地模型包随附的 `README.txt` 标注了以下信息：

- **Character / 角色**: Elysia from *Honkai Impact 3rd* / 《崩坏3》爱莉希雅
- **Voice performer named by the pack / 模型包标注的角色配音**: Yan Ning / 宴宁
- **Model publisher / 模型发布者**: `TinyLight微光小明`
- **Integration-pack provider / 整合包提供者**: `花儿不哭`
- **Matching publication page / 内容吻合的发布页**: [BV1Yz421a7HW](https://www.bilibili.com/video/BV1Yz421a7HW/)
- **Integration tutorial / 整合包教程**: `BV12g4y1m7Uw`
- **Upstream software / 上游软件**: [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)

The local directory contains `.ckpt` and `.pth` model weights, `.wav` reference clips, and the accompanying notice. The description on the linked Bilibili page closely matches that notice, and the following digests identify exactly what the 2026-09-14 technical evaluation used. The publisher did not provide matching authoritative digests, however, so these hashes do **not** prove that the local files are the exact published version, establish permission, or establish the publisher's authority. The page also carries a no-unauthorized-repost statement.

本地目录包含 `.ckpt` 与 `.pth` 模型权重、`.wav` 参考音频和随附说明。上述 Bilibili 发布页的简介与随附说明高度吻合；下列摘要只固定 2026-09-14 技术评估实际使用的本地文件。发布者没有提供可对应核验的权威摘要，因此这些 Hash **不能**证明本地文件就是发布页中的精确版本，也不能证明授权或发布者拥有相应权利。页面同时标有“未经作者授权，禁止转载”。

| Local evaluation selection / 本地评估选择 | Bytes | SHA-256 | Technical metadata / 技术元数据 |
| --- | ---: | --- | --- |
| Selected GPT v2 checkpoint / 所选 GPT v2 Checkpoint | 155,312,566 | `C73957C7815EA36A345678DF6DDEDA9FFD2B03498D17802BF54502414C9D887B` | `【GPT2.0】Elysia-e20.ckpt`; technical candidate only / 仅为技术候选 |
| Selected SoVITS v2 checkpoint / 所选 SoVITS v2 Checkpoint | 85,007,488 | `D095458023374D2BB7B657FF622A010504F8825B7188AB0582B91EF6412CD9CE` | `【GPT2.0】Elysia_e24_s13080.pth`; technical candidate only / 仅为技术候选 |
| `neutral` reference / 中性参考 | 476,676 | `4FF61FE9F385450154B2C460EB0DEF5EE15013480CC17FDD235DBEB20A4E34B8` | WAV PCM, 44.1 kHz, mono, 16-bit, 238,316 frames, 5.403991 s |
| `happy` reference / 开心参考 | 411,672 | `C42EEE79E91B847FF5E54E4B5F7C46751CD0CDB3E18223373F385A67A42D277E` | WAV PCM, 44.1 kHz, mono, 16-bit, 205,814 frames, 4.666984 s |
| `sad` reference / 悲伤参考 | 481,438 | `999730EB7DAA2F41A87DF7AA1D56748A8408DE7A8B45180785BE6C7EC98A8A01` | WAV PCM, 44.1 kHz, mono, 16-bit, 240,697 frames, 5.457982 s |

Choosing a higher epoch/step file for this acceptance run is not a claim that it has the best quality, verified provenance, or authorization. Exact reference filenames and prompt text remain only in the ignored local catalog because they may themselves disclose copyrighted dialogue and local paths.

本次验收选择较高 Epoch/Step 文件，不代表其音质最佳、来源已经核实或已经获得授权。参考音频的完整文件名和准确 Prompt 文本只保留在被忽略的本机 Catalog 中，因为它们本身可能暴露受版权保护的台词和本地路径。

The `models/weights/` ignore rule keeps these files outside the current Git index, but `.gitignore` alone cannot prevent forced adds, historical inclusion, or accidental copying by packaging scripts. Every release must verify its actual file list.

`models/weights/` 忽略规则使这些文件当前不在 Git Index 中，但 `.gitignore` 本身不能阻止强制添加、历史提交或打包脚本意外复制。每次发布都必须核对实际文件清单。

### Terms stated by the supplied notice / 随附说明写明的条件

The supplied notice asks users to credit the software and voice-model source in derivative works and prohibits resale, commercial use, R18 use, and unlawful use.

随附说明希望二创作品注明软件与语音模型来源，并明确禁止盗卖、商业用途、R18 用途及违法用途。

Elysia AI applies the following internal isolation policy while authorization remains unverified. This policy reduces risk; it does **not** mean that local non-commercial use has been licensed:

在授权仍未核实的情况下，Elysia AI 采用以下项目内部隔离策略。该策略只用于降低风险，**不代表本地非商业使用已经获得许可**：

- local, personal, non-commercial evaluation only / 仅限本地、个人、非商业评估；
- no resale or paid distribution / 不得盗卖或付费分发；
- no R18 or unlawful use / 不得用于 R18 或违法内容；
- retain the model and software attribution / 保留模型与软件来源署名；
- do not imply endorsement by the publisher, performer, miHoYo, or HoYoverse / 不得暗示模型发布者、配音演员、米哈游或 HoYoverse 对本项目提供背书。

### Authorization gap / 授权缺口

The supplied local notice does not itself contain an original publication URL or an explicit grant allowing this repository to redistribute the model weights or reference audio. A matching Bilibili page has now been identified, but the available material still does not demonstrate that the publisher holds authority to license the relevant character, recording, or performance rights.

本地随附说明本身没有提供模型原始发布链接，也没有明确授予本仓库再分发模型权重或参考音频的权利。现在虽已找到内容吻合的 Bilibili 发布页，但现有材料仍未证明发布者有权授予相关角色、录音或表演权。

Therefore, the local weights and audio must not be:

因此，本地权重与音频不得：

- committed to this repository / 提交到本仓库；
- attached to a GitHub Release or shared archive / 上传到 GitHub Release 或共享压缩包；
- bundled into an installer or container image / 打进安装包或容器镜像；
- mirrored, sold, sublicensed, or presented as project-owned assets / 镜像、售卖、转授权或宣称为本项目自有素材。

Before any future distribution, the maintainer must obtain and retain permission that clearly covers the exact asset version, redistribution, modification, attribution, commercial scope, and any voice-performance rights that apply.

未来如需分发，维护者必须取得并保留能够明确覆盖具体素材版本、再分发、修改、署名、商业范围以及相关声音表演权的许可记录。

### Current authorization decision / 当前授权决定

The `default` Elysia Voice Profile remains classified as `local-evaluation-only`. The source note, matching publication page, exact local digests, and named contributors recorded above are provenance evidence, but they are not a redistribution grant. The local opt-in flag enables a technical evaluation only; it cannot change that rights status. Until adequate permission is retained, the checkpoints and reference recordings must remain outside Git, releases, installers, containers, mirrors, and shared diagnostic archives.

`default` 爱莉希雅 Voice Profile 继续归类为 `local-evaluation-only`。上文记录的随附说明、内容吻合的发布页、本地文件精确摘要和署名主体属于来源证据，但不构成再分发授权。本机 Opt-in 开关只允许技术评估，不能改变素材的权利状态。在留存充分许可之前，Checkpoint 与参考录音必须继续排除在 Git、Release、安装包、Container、镜像和共享诊断压缩包之外。

This repository enforces that decision with `scripts/check_distribution_assets.py`. CI audits the Git index, freezes the complete reviewed Electron Builder configuration, builds the Windows package, and scans both the unpacked tree and its `asar list` capture. Release preparation must repeat the artifact scan for the exact candidate being published. The checker is a preventive engineering control, not a legal conclusion.

本仓库通过 `scripts/check_distribution_assets.py` 执行上述决定。CI 会检查 Git Index、冻结完整且已审查的 Electron Builder 配置、构建 Windows Package，并同时扫描 Unpacked Tree 与 `asar list` 清单；准备发行时仍必须对实际候选产物重复该扫描。该检查器是预防性工程控制，不是法律结论。

---

## GPT-SoVITS Software Boundary / GPT-SoVITS 软件边界

The upstream GPT-SoVITS source repository publishes its software under the [MIT License](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/LICENSE). That software license applies to the covered upstream code; it does **not** automatically license third-party checkpoints, training data, reference audio, character IP, or vocal performances.

GPT-SoVITS 上游仓库以 [MIT License](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/LICENSE) 发布其软件。该软件许可证只适用于其覆盖的上游代码，**不会自动授权**第三方 Checkpoint、训练数据、参考音频、角色 IP 或声音表演。

For the 2026-09-14 Windows acceptance run, the maintainer explicitly downloaded the upstream-linked [`GPT-SoVITS-v2-240821.7z`](https://huggingface.co/lj1995/GPT-SoVITS-windows-package/blob/42b55dd0c41f0d23218f8f7c1e9e0636a0e386e8/GPT-SoVITS-v2-240821.7z) at repository revision `42b55dd0c41f0d23218f8f7c1e9e0636a0e386e8`. The remote file size is 5,744,891,255 bytes and its verified SHA-256 is `9D9BA79DE6ACA0CF28A3635CCB1DBBB08B6AEF362C4352E32FAD99BB49E3000A`. It was extracted under ignored `models/cache/GPT-SoVITS-v2-240821/`; the downloaded archive was deleted after hash verification and extraction. The local Runtime loaded v2 on CUDA in half precision for the smoke run.

2026-09-14 的 Windows 验收由维护者显式下载上游链接的 [`GPT-SoVITS-v2-240821.7z`](https://huggingface.co/lj1995/GPT-SoVITS-windows-package/blob/42b55dd0c41f0d23218f8f7c1e9e0636a0e386e8/GPT-SoVITS-v2-240821.7z)，固定仓库 Revision 为 `42b55dd0c41f0d23218f8f7c1e9e0636a0e386e8`。远端文件大小为 5,744,891,255 bytes，已验证 SHA-256 为 `9D9BA79DE6ACA0CF28A3635CCB1DBBB08B6AEF362C4352E32FAD99BB49E3000A`。它解压到被忽略的 `models/cache/GPT-SoVITS-v2-240821/`；下载压缩包在 Hash 与解压验证后已经删除。本机 Runtime 在 Smoke 中以 CUDA Half Precision 加载 v2。

The extracted package includes an MIT `LICENSE` naming RVC-Boss, but that license covers only software to which it validly applies. It must not be extended to every bundled dependency or pretrained model, nor to the Elysia checkpoints, training data, reference audio, character IP, recordings, or performances. Elysia AI does not vendor this Runtime and does not claim that the package as a whole—or the local Elysia voice pack—is MIT-licensed. The extracted Runtime, local inference YAML, model weights, and reference audio stay ignored and outside Git, releases, installers, and containers.

解压包内包含署名 RVC-Boss 的 MIT `LICENSE`，但该许可证只覆盖其能够合法适用的软件，不能扩大到包内每个依赖或预训练模型，也不能覆盖 Elysia Checkpoint、训练数据、参考音频、角色 IP、录音或表演。Elysia AI 不把该 Runtime 纳入仓库，也不声称整份整合包或本地 Elysia 声音包采用 MIT 许可证。解压 Runtime、本机推理 YAML、模型权重与参考音频继续被忽略，不进入 Git、Release、安装包或容器。

---

## Character Reference Corpus / 角色参考语料

`data/characters/elysia_character_reference_zh.md` contains character background material and a large quotation/transcription collection.

`data/characters/elysia_character_reference_zh.md` 包含角色背景资料以及大量语录和语音转写文本。

The background section credits the [Elysia article on Moegirlpedia](https://zh.moegirl.org.cn/%E7%88%B1%E8%8E%89%E5%B8%8C%E9%9B%85) and states that the cited page text uses [CC BY-NC-SA 3.0 CN](https://creativecommons.org/licenses/by-nc-sa/3.0/cn/) by default. The compilation says it preserves 629 lines of background material, combines 171 selected quotations with 2,169 voice transcriptions, and performs exact-text deduplication to produce 2,318 quotation entries. It does not record the imported page revision, contributor history, or item-level provenance. The page-level license statement must not be assumed to cover every quotation, official game line, transcription, or other excepted material in the compiled file.

背景部分标注来源为[萌娘百科“爱莉希雅”条目](https://zh.moegirl.org.cn/%E7%88%B1%E8%8E%89%E5%B8%8C%E9%9B%85)，并注明所引页面文字默认采用 [CC BY-NC-SA 3.0 中国大陆协议](https://creativecommons.org/licenses/by-nc-sa/3.0/cn/)。汇总文件自述保留 629 行背景资料，把 171 条精选语录与 2,169 条语音转写合并，并在精确文本去重后得到 2,318 条语录；但它没有记录导入页面的版本、贡献者历史或逐条来源。页面级许可证说明不能被自动扩大到汇总文件中的每条语录、官方游戏台词、语音转写或其他例外素材。

This file is already tracked, so every clone or repository share distributes a copy. Its unresolved provenance is therefore a current distribution risk, not only a future-release concern. The maintainer should promptly conduct an item-level review and then remove unsupported material, replace it with original summaries, or obtain adequate permission. Removing it from a future tree would not by itself erase copies from Git history.

该文件已经被 Git 跟踪，因此每次 Clone 或共享仓库都会分发副本。来源未解决是**当前分发风险**，而不只是未来正式发布时的问题。维护者应尽快逐项审查，并删除无法证明可复用的内容、改写为原创摘要，或取得充分许可。将来从工作树移除该文件，也不会自动清除 Git 历史中的既有副本。

---

## Underlying Character and Voice Rights / 底层角色与声音权利

Elysia, *Honkai Impact 3rd*, and related names, character designs, story, artwork, audio, and trademarks belong to their respective rights holders, including HoYoverse / miHoYo where applicable. Voice and performance rights may also belong to the performer and other parties.

爱莉希雅、《崩坏3》以及相关名称、角色设计、剧情、美术、音频与商标归各自权利人所有；其中适用的权利包括 HoYoverse / 米哈游的权利，声音与表演还可能涉及配音演员及其他主体的权利。

Elysia AI is an unofficial fan-development project. It is not affiliated with, endorsed by, sponsored by, or partnered with HoYoverse / miHoYo or the named model and voice contributors.

Elysia AI 是非官方粉丝开发项目，与 HoYoverse / 米哈游以及上述模型、声音贡献者没有隶属、合作、赞助或背书关系。

On 2026-09-23, the current [HoYoverse fan-made content help article](https://support.hoyoverse.com/hc/en-us/articles/51005649400729-What-are-the-guidelines-for-creating-and-selling-fan-made-content), shown by HoYoverse as updated on 2025-10-13, was rechecked. Its linked program is a product-specific fan-creation guide, not permission to redistribute this *Honkai Impact 3rd* voice pack. The previously recorded [Honkai Impact 3rd material usage and fanwork guidelines](https://www.hoyolab.com/article/1463874) remain a boundary reference rather than a model, recording, or performance license. Neither source supplies the missing permission identified above.

2026-09-23 已重新核对 HoYoverse 当前的[同人内容帮助说明](https://support.hoyoverse.com/hc/en-us/articles/51005649400729-What-are-the-guidelines-for-creating-and-selling-fan-made-content)；HoYoverse 页面显示其更新于 2025-10-13。该页链接的是特定产品的同人创作指南，并不是再分发本《Honkai Impact 3rd》声音包的许可。此前记录的[《Honkai Impact 3rd》素材与同人创作指南](https://www.hoyolab.com/article/1463874)仍只作为边界参考，不是模型、录音或表演权许可；两者都没有补足上文所述授权缺口。

Users must verify the latest rules that apply to their region, source material, and intended use. This document is not a substitute for that verification or for direct permission from the relevant rights holders.

用户必须自行核对适用于所在地区、素材来源和具体用途的最新规则。本文件不能替代该核对过程，也不能替代相关权利人的直接许可。

---

## Local Handling Rules / 本地处理规则

Contributors and maintainers must:

贡献者与维护者必须：

1. keep `models/cache/`, `models/weights/`, `models/blobs/`, and `models/manifests/` ignored and local, and inspect every actual release file list because ignore rules do not prevent forced adds or packaging copies;<br>
   保持 `models/cache/`、`models/weights/`、`models/blobs/` 与 `models/manifests/` 被忽略并仅存本机；由于 Ignore Rule 不能阻止强制添加或打包复制，每次都须检查实际发行文件清单；
2. review every new model, dataset, reference clip, image, font, and character corpus before use;<br>
   使用前审查每个新增模型、数据集、参考音频、图片、字体与角色语料；
3. record the creator, original URL, version or digest, applicable terms, and permission evidence;<br>
   记录创作者、原始链接、版本或摘要、适用条款与授权证据；
4. keep third-party assets outside source-code licensing and packaging unless written permission clearly allows both;<br>
   除非书面许可明确允许，否则把第三方素材排除在源码许可和安装包之外；
5. remove an asset promptly if its provenance or permission is disputed and cannot be resolved.<br>
   若素材来源或授权发生争议且无法核实，应及时移除。

Before publishing any artifact, run both the repository policy check and an artifact scan, replacing the example path with the actual output directory:

发布任何产物前，必须同时运行仓库策略检查与产物扫描，并把示例路径替换为真实输出目录：

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe scripts\check_distribution_assets.py
cd desktop
npm run package
npx --no-install asar list out\win-unpacked\resources\app.asar > "%TEMP%\elysia-asar-listing.txt"
if exist "%TEMP%\elysia-portrait.png" del /f /q "%TEMP%\elysia-portrait.png"
if exist "%TEMP%\elysia-state-atlas.png" del /f /q "%TEMP%\elysia-state-atlas.png"
if exist "%TEMP%\elysia-expression-atlas.png" del /f /q "%TEMP%\elysia-expression-atlas.png"
if exist "%TEMP%\elysia-speech-atlas.png" del /f /q "%TEMP%\elysia-speech-atlas.png"
pushd "%TEMP%"
call "D:\Elysia_AI\desktop\node_modules\.bin\asar.cmd" extract-file "D:\Elysia_AI\desktop\out\win-unpacked\resources\app.asar" "dist\character\elysia-portrait.png"
call "D:\Elysia_AI\desktop\node_modules\.bin\asar.cmd" extract-file "D:\Elysia_AI\desktop\out\win-unpacked\resources\app.asar" "dist\character\elysia-state-atlas.png"
call "D:\Elysia_AI\desktop\node_modules\.bin\asar.cmd" extract-file "D:\Elysia_AI\desktop\out\win-unpacked\resources\app.asar" "dist\character\elysia-expression-atlas.png"
call "D:\Elysia_AI\desktop\node_modules\.bin\asar.cmd" extract-file "D:\Elysia_AI\desktop\out\win-unpacked\resources\app.asar" "dist\character\elysia-speech-atlas.png"
popd
cd ..
.venv\Scripts\python.exe scripts\check_distribution_assets.py --unpacked-tree desktop\out\win-unpacked --asar-listing "%TEMP%\elysia-asar-listing.txt" --extracted-asar-portrait "%TEMP%\elysia-portrait.png" --extracted-asar-character-atlas "%TEMP%\elysia-state-atlas.png" --extracted-asar-expression-atlas "%TEMP%\elysia-expression-atlas.png" --extracted-asar-speech-atlas "%TEMP%\elysia-speech-atlas.png"
del /f /q "%TEMP%\elysia-asar-listing.txt" "%TEMP%\elysia-portrait.png" "%TEMP%\elysia-state-atlas.png" "%TEMP%\elysia-expression-atlas.png" "%TEMP%\elysia-speech-atlas.png"
```

---

## Source-Code License Boundary / 源代码许可证边界

This repository currently has no root `LICENSE` file. This notice does not grant a license to Elysia AI source code. Likewise, any future source-code license must not be interpreted as covering character IP, the character corpus, model weights, reference audio, Ollama models, or other third-party assets unless it explicitly says so and the project has authority to make that grant.

本仓库目前没有根级 `LICENSE` 文件。本说明不授予 Elysia AI 源代码许可。将来即使添加源码许可证，也不得把它解释为涵盖角色 IP、角色语料、模型权重、参考音频、Ollama 模型或其他第三方素材，除非许可证明确写明且本项目确实有权作出该授权。

---

## Corrections / 更正

If you are a rights holder or can provide authoritative provenance or permission information, please contact the repository maintainer through GitHub so this notice can be corrected. A credit entry does not waive any right, and a correction request will be handled without requiring public disclosure of private evidence.

如果你是相关权利人，或能提供权威的来源与授权信息，请通过 GitHub 联系仓库维护者以更正本说明。署名不代表权利被放弃；提出更正时，也不要求公开披露私密授权证据。

This document is informational and is not legal advice.

本文件仅用于信息说明，不构成法律意见。
