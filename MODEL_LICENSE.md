# Model & Voice Asset Notice / 模型与语音素材说明

> Last reviewed / 最近核对：2026-09-13

## Purpose and Status / 用途与性质

This document records the known provenance, local handling rules, and current authorization gaps for model weights, reference audio, and character material used or evaluated with Elysia AI.

本文记录 Elysia AI 已使用或评估过的模型权重、参考音频与角色资料的已知来源、本地处理规则和当前授权缺口。

> [!IMPORTANT]
>
> This is an **asset and rights notice**, not an open-source license and not a grant of third-party rights. Where redistribution permission cannot be verified, the asset must remain local and must not be committed, released, or packaged.
>
> 本文件是**素材与权利告知**，不是开源许可证，也不代表本项目能够转授第三方权利。凡无法核实再分发许可的素材，都必须保留在本机，不得提交、发布或打包。

---

## Asset Inventory / 素材范围

| Location / 路径 | Repository status / 仓库状态 | Current treatment / 当前处理方式 |
| --- | --- | --- |
| `models/weights/gpt-sovits/elysia-v2/` | Ignored and currently outside the Git index / 已忽略且当前不在 Git Index 中 | Local-only GPT-SoVITS weights and reference audio; every package/release must independently verify exclusion / 本地 GPT-SoVITS 权重与参考音频；每次打包和发布都须独立确认排除 |
| `models/blobs/` and `models/manifests/` | Ignored and currently outside the Git index / 已忽略且当前不在 Git Index 中 | Ollama-managed local models; each upstream model has its own terms / Ollama 管理的本地模型，各自遵循上游条款 |
| `data/characters/elysia_character_reference_zh.md` | Tracked / 已跟踪 | Character background and quotations requiring separate source review / 需要单独审查来源的角色背景与语录 |
| `desktop/public/favicon.svg` | Tracked / 已跟踪 | Application branding asset; it does not depict the game character / 应用品牌图标，不包含游戏角色形象 |

The current runtime does **not** yet load the local GPT-SoVITS weights. They are retained only as a future local integration candidate.

当前运行时代码**尚未加载**本地 GPT-SoVITS 权重；这些文件只是未来本地接入的候选素材。

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
