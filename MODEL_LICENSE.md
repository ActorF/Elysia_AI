# Model & Voice Asset Notice / 模型与语音素材说明

> Last reviewed / 最近核对：2026-09-14

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
| `models/blobs/` and `models/manifests/` | Ignored and currently outside the Git index / 已忽略且当前不在 Git Index 中 | Ollama-managed local models; each upstream model has its own terms / Ollama 管理的本地模型，各自遵循上游条款 |
| `models/cache/faster-whisper/` and `models/weights/faster-whisper/` | Ignored and currently outside the Git index / 已忽略且当前不在 Git Index 中 | Local speech-recognition models only; the adapter requires an explicit complete directory and never bundles or implicitly downloads weights / 仅存本地的语音识别模型；Adapter 要求明确、完整的目录，不打包也不隐式下载权重 |
| `data/characters/elysia_character_reference_zh.md` | Tracked / 已跟踪 | Character background and quotations requiring separate source review / 需要单独审查来源的角色背景与语录 |
| `desktop/public/elysia-icon.png` and `desktop/assets/elysia-icon.ico` | Tracked third-party branding / 已跟踪的第三方品牌素材 | Derived from an official *Honkai Impact 3rd* Elysia signet and included at the project owner's express direction for this unofficial, non-commercial fan project; © HoYoverse / miHoYo, excluded from every source-code license, no endorsement implied, and removable on rights-holder request / 由《崩坏3》爱莉希雅官方刻印制作，并按项目所有者明确决定用于本非官方、非商业粉丝项目；© HoYoverse / miHoYo，不属于任何源码许可证，不代表官方背书，权利人要求时应移除 |

The current runtime does **not** yet load the local GPT-SoVITS weights. They are retained only as a future local integration candidate.

当前运行时代码**尚未加载**本地 GPT-SoVITS 权重；这些文件只是未来本地接入的候选素材。

---

## Faster-Whisper Software and Model Boundary / Faster-Whisper 软件与模型边界

The optional adapter targets [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) and keeps its imports out of the base text-Chat runtime. The upstream Faster-Whisper source is published under the [MIT License](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE). That software license covers the upstream code, not arbitrary recordings, transcripts, converted models, or any unrelated character and voice assets.

可选 Adapter 面向 [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)，其第三方 Import 不会进入基础文字 Chat Runtime。Faster-Whisper 上游源码采用 [MIT License](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)；该软件许可证只覆盖相应上游代码，不会自动覆盖任意录音、转写文本、转换模型或无关的角色与声音素材。

The first recommended local evaluation candidate is the multilingual [`Systran/faster-whisper-small`](https://huggingface.co/Systran/faster-whisper-small) model. The local CPU acceptance run on 2026-09-14 used repository revision `536b0662742c02347bc0e980a01041f333bce120`; its `model.bin` SHA-256 is `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`. The pinned snapshot's model card labels it MIT, but the downloaded snapshot contains no separate `LICENSE` file. The local copy remains Git-ignored and is not part of this repository or installer. Future changes to the upstream card must not be assumed to retroactively describe this pinned copy.

首个建议的本地评估候选是多语言 [`Systran/faster-whisper-small`](https://huggingface.co/Systran/faster-whisper-small)。2026-09-14 的本地 CPU 验收使用仓库 Revision `536b0662742c02347bc0e980a01041f333bce120`，其中 `model.bin` 的 SHA-256 为 `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`。该固定 Snapshot 的 Model Card 标注为 MIT，但下载内容没有独立 `LICENSE` 文件；本地副本继续被 Git 忽略，不属于本仓库或安装包。不得假设上游页面未来的修改会自动适用于这个固定副本。

The adapter accepts only an explicit absolute local model directory containing the runtime configuration, model binary, and tokenizer. It passes `local_files_only=True`, rejects Git LFS pointer files, and never resolves a model alias into a hidden weight download. Desktop Settings map a closed model-name allowlist to that directory and do not expose a path or download action. Model installation remains a separate user-triggered operation, and every package/release must verify that local weights and caches remain excluded.

Adapter 只接受明确的绝对本地模型目录，并要求其中存在 Runtime 配置、模型二进制与 Tokenizer；它会传入 `local_files_only=True`、拒绝 Git LFS Pointer，且不会把模型别名解析为隐藏的权重下载。Desktop Settings 只会把闭集模型名称映射到这个目录，不暴露路径或下载动作。模型安装仍是独立的用户触发操作，每次打包和发布都必须核对本地权重与缓存仍被排除。

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

The local directory contains `.ckpt` and `.pth` model weights, `.wav` reference clips, and the accompanying notice. The description on the linked Bilibili page closely matches that notice, but no recorded digest proves that the local files are the exact published version. The page also carries a no-unauthorized-repost statement.

本地目录包含 `.ckpt` 与 `.pth` 模型权重、`.wav` 参考音频和随附说明。上述 Bilibili 发布页的简介与随附说明高度吻合，但目前没有已记录的文件摘要能够证明本地文件就是该页面发布的精确版本；页面同时标有“未经作者授权，禁止转载”。

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

---

## GPT-SoVITS Software Boundary / GPT-SoVITS 软件边界

The upstream GPT-SoVITS source repository publishes its software under the [MIT License](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/LICENSE). That software license applies to the covered upstream code; it does **not** automatically license third-party checkpoints, training data, reference audio, character IP, or vocal performances.

GPT-SoVITS 上游仓库以 [MIT License](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/LICENSE) 发布其软件。该软件许可证只适用于其覆盖的上游代码，**不会自动授权**第三方 Checkpoint、训练数据、参考音频、角色 IP 或声音表演。

Elysia AI does not currently vendor the GPT-SoVITS runtime or claim that the local model pack is MIT-licensed.

Elysia AI 当前没有把 GPT-SoVITS Runtime 纳入仓库，也不声称本地模型包采用 MIT 许可证。

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

HoYoverse's current [fan-made content help article](https://support.hoyoverse.com/hc/en-us/articles/51005649400729-What-are-the-guidelines-for-creating-and-selling-fan-made-content) links creators to its current general program. The separately published [Honkai Impact 3rd material usage and fanwork guidelines](https://www.hoyolab.com/article/1463874) contain specific conditions for audio and fan content and expressly state that they do not apply to the Simplified Chinese edition released in mainland China.

HoYoverse 当前的[同人内容帮助说明](https://support.hoyoverse.com/hc/en-us/articles/51005649400729-What-are-the-guidelines-for-creating-and-selling-fan-made-content)会链接到其现行通用计划；另行发布的[《Honkai Impact 3rd》素材与同人创作指南](https://www.hoyolab.com/article/1463874)对音频和同人内容有具体条件，并明确说明不适用于中国大陆发行的简体中文版本。

Users must verify the latest rules that apply to their region, source material, and intended use. This document is not a substitute for that verification or for direct permission from the relevant rights holders.

用户必须自行核对适用于所在地区、素材来源和具体用途的最新规则。本文件不能替代该核对过程，也不能替代相关权利人的直接许可。

---

## Local Handling Rules / 本地处理规则

Contributors and maintainers must:

贡献者与维护者必须：

1. keep `models/weights/`, `models/blobs/`, and `models/manifests/` ignored and local;<br>
   保持 `models/weights/`、`models/blobs/` 与 `models/manifests/` 被忽略并仅存本机；
2. review every new model, dataset, reference clip, image, font, and character corpus before use;<br>
   使用前审查每个新增模型、数据集、参考音频、图片、字体与角色语料；
3. record the creator, original URL, version or digest, applicable terms, and permission evidence;<br>
   记录创作者、原始链接、版本或摘要、适用条款与授权证据；
4. keep third-party assets outside source-code licensing and packaging unless written permission clearly allows both;<br>
   除非书面许可明确允许，否则把第三方素材排除在源码许可和安装包之外；
5. remove an asset promptly if its provenance or permission is disputed and cannot be resolved.<br>
   若素材来源或授权发生争议且无法核实，应及时移除。

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
