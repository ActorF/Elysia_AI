# 本机爱莉希雅翻唱 / Local Elysia Song Cover

> Status / 状态：Composer 默认使用 **Voice conversion**（`source-svc`），以原唱内容和选择的歌唱参考运行 SoulX-Singer-SVC；保留 **Lyrics-driven singing**（`lyrics-svs`）和 **Legacy RVC**（`legacy-svc`）供明确选择。三条路径都面向具备私有 Runtime 的源码开发环境，模型、参考音频和歌曲不进入安装包。短段试听通过不代表所有整曲、音域或语言均已通过音质验收。

## 1. 功能边界与默认选择

Composer 中的音符按钮位于 **Dictate** 麦克风左侧。设置窗口会先让用户明确选择 Singing method：

- **Voice conversion**：默认。原声内容引导、全程本地，无歌词查询。参考可选择《妖精小姐的魔法邀约》（固定 CFG 1）或《Elysia》（固定 CFG 3），不根据文件名自动猜测。调性仍由用户明确选择；红豆试听偏好对应《Elysia》与 `+12`，RPReplay 对应《妖精小姐的魔法邀约》与原调。
- **Lyrics-driven singing**：Main 在线查找可信的同步普通话歌词，再由本机 Windows + WSL Runtime 依照歌词和检测到的音符生成主唱；
- **Legacy RVC**：显式旧引擎，协议 ID 仍为 `legacy-svc`。失败不会自动切换引擎；详细边界见第 8 节。

三种方法都接受一首已混合歌曲，或一对从同一时间点开始的 vocal/accompaniment Stem；都只允许原调或升／降一至十二个整数半音。设置使用粉紫主题的方法卡片、音源分段选项，以及带单步加减和回到原调操作的 Song key 滑块；vocal 始终采用所选调性。**Shift accompaniment with vocal** 默认勾选并让伴奏使用同一档位，取消勾选则保持伴奏原调。该选项对三种引擎与完整歌曲／Stem 两种输入方式都生效。主唱单独进行非八度移调时通常会与原调伴奏跑调或不协和；`±12` 改变主唱音区，但保留音级关系。完整歌曲在 Windows 侧使用固定 Demucs `htdemucs` 分离，Stem 模式跳过分离。

两种 voice conversion 每次新建任务都提供 **Vocal arrangement**，取消或确认后重置：

- **Entire vocal track**：默认，转换所提供的整条 vocal。完整歌曲会先分离所有人声与伴奏；重叠歌声仍可能混在一起。
- **Lead only, keep other singers**：需要用户准备单独主唱轨，以及从同一时间点开始、含伴奏与其他歌声且已移除主唱的 backing mix。此选项强制已有的双 Stem 流程，不会自动从成品歌曲辨认或分离歌手；保留原调时不会额外移动其他歌声的音高。Backing mix 仍参与现有受限混音，因此不承诺其波形逐样本不变。

固定 Demucs 的 `--two-stems vocals` 只区分人声与其余声音，不区分主唱、和声和第二位歌手（[官方说明](https://github.com/facebookresearch/demucs/blob/main/README.md)）。重叠人声分离是独立任务，[MedleyVox](https://github.com/jeonchangbin49/MedleyVox) 专门区分 duet、unison 和 main-versus-rest；当前本机没有部署此类分离模型。若需把每位歌手分别转换为 Elysia，同时保留独立旋律，需要先取得各自独立声部，再分别转换和对齐混合，当前界面没有多声部自动批处理能力。

歌词驱动并不等于任意歌曲都可自动重唱。当前 SVS 只接受可安全规范化为简体中文汉字的 Mandarin 歌词；标点、空格和装饰符号不占音符，拉丁字母、数字及其他非汉字歌词会 Fail Closed。它还必须取得带时间戳的同步 LRC，并让该 LRC 与源 vocal 检测出的音符以严格门槛唯一对齐。

## 2. Lyrics-driven SoulX SVS 链路

可选歌词路径由 Renderer、Electron Main、Windows Worker、WSL Bridge 和私有 SoulX Runtime 串接：

1. **Main metadata + bounded override**：Main 使用固定本机 `ffprobe.exe` 读取第一条音频流的时长，并从 Format／Audio stream tag 中取得 `title` 与 `artist`（必要时接受 `album_artist`）。用户可在确认窗口同时填写有界的 Song title 与 Artist 来覆盖查词身份；手动值只用于在线匹配，歌曲时长仍强制来自 FFprobe。原生路径不会交给 Renderer。
2. **LRCLIB exact → synchronized fuzzy verified lookup**：Main 先请求固定 HTTPS Origin `https://lrclib.net/api/get`，带 title、artist 与四舍五入后的 duration。精确请求没有可信结果，或可信记录只有 Plain lyrics 而没有同步 LRC 时，再请求 `/api/search`；后者用于寻找由其他贡献者上传的可信同步版本。Search 中只要存在通过全部门槛的同步候选，就优先于 Plain-only 候选；若没有可信同步候选，则保留可信的精确 Plain-only 记录，让上层继续给出准确的“没有同步歌词”结果。任何被采用的精确或模糊结果都必须同时通过综合置信度 `>= 0.82`、标题相似度 `>= 0.70`、歌手相似度 `>= 0.65` 和时长差 `<= 20 s`；Search response 最多接受 20 条结果。Redirect 不被接受，Rate limit／Service unavailable 最多按受限 `Retry-After` 重试一次。
3. **Private synchronized lyrics**：只有通过上述门槛、且未触发双向文件名查询冲突的结果，才会在 Main 已创建的私有 Windows Job Root 中以排他方式写入固定 UTF-8 文件。`lyrics.lrc` 是启动 SVS 的硬性要求；`lyrics.txt` 若存在，会在 WSL 内与 LRC 的汉字序列做一致性复核；`lyrics-manifest.json` 只保存 `source=lrclib`、Record ID、置信度和同步歌词存在标记，不保存歌词正文或本机路径。
4. **Windows audio preparation**：完整歌曲先规范化为 44.1 kHz PCM，再用 CUDA Demucs 4.0.1 `htdemucs` two-stems 拆出 vocal 与 accompaniment；Stem 模式把两份输入从共同的零时间点解码并按较长者补齐。非零调性会在进入 SVS 前以固定、等长度 FFmpeg 路径移动 target vocal；默认勾选伴奏跟随时，accompaniment 使用同一移调，否则保留规范化／分离后的原调。原调只把 vocal 准备成精确长度的 mono PCM。
5. **WSL private staging**：Windows Worker 使用绝对 `%SystemRoot%\System32\wsl.exe`，不经过 Shell；`/usr/bin/wslpath` 把已验证的 Windows Job Root 转成 WSL mount path，并由固定 `/usr/bin/python3 -I` 查询当前 WSL UID 的 POSIX Home，再拼接固定私有 Runtime suffix。Bridge 只把 `target_vocal.wav`、`lyrics.lrc` 和可选 `lyrics.txt` 复制到当前 Linux 用户拥有的 mode-`0700` UUID Job，文件以 mode `0600` 创建；Manifest 不进入 Linux Runtime。
6. **FunASR hotword + strict lyric/note alignment**：固定 SoulX preprocessing 以 `language=Mandarin`、CUDA、MIDI transcription 运行。同步 LRC 先转简体汉字、去重并形成有界的 FunASR `hotword`，只用于提高 ASR 偏置，不能授权错误歌词。相邻的非空 LRC 行会组成连续歌词区块，明确的空行时间戳负责结束区块；每个区块跨 SoulX segment 在全局 note onset 时间线上做单调 Dynamic Programming，避免短句被 segment 边界或下一句之前的停顿错误拆分。ASR 字符成本始终是首要依据；只有字符成本完全相同时，才以 note duration 推导的 onset 与 LRC 明确提供的逐行起点作为第二依据；若识别器在同一行起点后重复了完全相同的歌词字，最终只允许“最早的精确匹配”打破平局，不匹配 onset 的 canonical/group 位置必须在候选路径间完全相同，否则仍视为多解。每个 type-2 onset 还必须落在所属 LRC 行的硬时间窗内：允许最多 `100 ms` 提前，行首允许最多 `600 ms` 延后，行内其他新字不得越过下一时间戳 `100 ms`；不合法的 onset transition 根本不进入 DP，type-3 延音不受该窗口限制。普通 LRC 没有提供的逐字时间不会用下一行时间或间奏长度平均推测；明确空白区间中的 ad-lib 或口播保留原 ASR metadata，不会被远处歌词覆盖。默认同时要求每个连续歌词区块与每个独立合成 segment 的 canonical onset exact-match ratio `>= 0.60`、normalized onset cost `<= 0.45`；剩余多解、歌词容量不足、Plain/LRC 不一致或有歌词的时间区间完全没有 metadata 都会失败。音高、时长、F0 与 segment 时间保持不变；渲染前会重新核对原文、note type、duration 和 segment time，防止旧计划套用到已变更的时间线。跨 segment 的延音只在新 slice 起点重新标成可独立推理的 onset，文字仍保持已验证的前一音节，所有校正文字最终由 SoulX 官方 G2P 重新生成 phoneme。
7. **Authenticated Elysia Prompt bank + deterministic quality search**：推理固定使用本机 `SoulX-Singer/model.pt`、`soulxsinger.yaml`、phoneset、`control=score`、`auto_shift=False`、`pitch_shift=0` 与 CUDA FP16，并以 strict deterministic Torch mode Fail Closed。私有 Prompt bank 共 59 条：48 条 corpus、10 条闭集 emotion、1 条 anchor；Canonical manifest 与其引用的 WAV/metadata 都先按大小、SHA-256、普通文件及无路径逃逸规则验证。Prompt WAV 不会直接交给 Vendor：认证读取先用 `O_NOFOLLOW` 取得来源 descriptor，在流式 SHA-256 验证期间复制到不可写、不可扩缩、不可重新密封的 sealed memfd，关闭来源后才只把该密封副本的 `/proc/self/fd` 引用交给 DataProcessor。每个目标段先按音域统计、语速和 phoneme 覆盖选出最多两个 Prompt；以实际音频/metadata 身份去重，避免不同标识重复占用候选预算。由歌词与谱面推断的 emotion/style 会规整空格和延音重复，只作量化 acoustic cost 完全同分时的稳定决胜项，不会覆盖音域或 phoneme 证据，也不是学习式情绪分类器。每段先生成确定性的 C0；只有 C0 结构失败或 acoustic loss 较高时，才在全局有界预算内依次尝试同 Prompt 新 seed 的 C1，以及第二 Prompt 初始 seed 的 C2，并且结构硬失败的 Rescue 优先于仅 soft loss 偏高的请求。Seed 只由规范化的音频/metadata/Prompt 摘要与候选序号产生，不依赖 UUID 或本机路径。DataProcessor 的 `mel2note_frames × hop_size` 是模型原始生成长度合同，score `[time]` 是绝对放置合同；固定 SoulX 会在 preprocessing 输出中把每个 note duration 独立量化到 0.01 秒，因此只有确实采用固定两位小数格式的 serialized duration sum，才可使用按每个 note 最多 5 ms 累积、再加两个 hop、且受 `max(3 hops, absolute interval 的 1%)` 上限约束的容差；更高精度 duration 继续使用严格的 `< 2 hops` 绝对校验。DataProcessor 模型帧仍使用同一有界 vendor 量化容差，未量化的原始 F0 时间线须独立满足 `< 2 hops`，所以合法长歌不会因数十毫秒累计舍入而失败，损坏或未量化的绝对时序也不能借 note 数量绕过验证。完整 `note_type`／`note_pitch` 序列及 `mel2note` 覆盖须逐项对应权威谱面；音高必须是精确有限整数，不能通过截断或集合比较掩盖缺失、重复或错位 note。受认证的量化差在 5 ms 收零 Ramp 后确定性截断/补零。结构硬门禁先拒绝非有限样本、模型时长违约、FP16 量化后持续近 rail 的同号平台、或真正触及 full-scale rail 的样本、超过一次 `-6 dB` 全局衰减仍无法恢复的 Headroom、DC 偏移及有效段近空音频；原始 float 超过 `±1` 本身不是 PCM 削波。只有 score 中明确的 `<SP>` 才构成预期静音，其他无音高帧不能被误当作静音。音高 loss 以 p90、gross deviation 与 octave evidence 单调加重，掉音、误有声与频谱证据仅形成 acoustic soft score。最后由全局 Dynamic Programming 同时考虑逐段分数、边界连续性和 Prompt 切换，并保留 Pareto 前缀状态，以免未来的 Headroom/Pareto 项使过早的局部 tie-break 错失全局较优路径；再按绝对 sample offset 放入精确 24 kHz mono 时间线。段间空隙保持数字静音；连续独立段的两侧或与空隙相接的边缘都在各自区间内使用 5 ms 收零 Ramp，不做 Crossfade、重叠或时间移动，长空隙只免除不连续波形的跳变/loudness 比较，不免除 Prompt switch cost。整首不提升音量、不逐段归一化、不使用 Limiter，只允许一次最多 `-6 dB` 的全局衰减到设计峰值 `0.994`，并以 `0.995` 最终硬上限复检。Renderer 不能替换模型、Prompt、语言、Device、Seed、预算、门禁或控制模式。
8. **Reviewed FFmpeg mix**：Bridge 先把生成 vocal 暂存回 mounted Job，删除整个私有 Linux Job 后才原子发布 `generated_vocal.wav`。Windows Worker 再使用 -18 dB 严格无声辅音层、60 Hz high-pass、0.5 dB Presence、线性 +1.3 dB vocal 补偿、2.2–5.2 kHz vocal-keyed accompaniment ducking 和 -1 dBFS limiter，输出 WAV 与 MP3。RVC 路径不会混回这份原唱辅音层，以避免原唱泄漏及重新引入分离伪影；固定 Protect 参数本身不构成无声内容保证。完成事件只在歌词、WSL 输出和其他 Scratch 均已清理后发送。

歌词驱动数据流如下：

```text
React 音符按钮（选择 lyrics-svs）
  └─ Main 原生文件选择器
     ├─ 固定 FFprobe：audio duration + title/artist tags
     ├─ LRCLIB HTTPS：exact lookup → bounded fuzzy search → confidence checks
     └─ private Windows Job：lyrics.lrc + optional lyrics.txt + manifest
        └─ song_svs_worker.py
           ├─ Windows FFmpeg：规范化／主唱移调／可选伴奏跟随
           ├─ Windows Demucs：完整歌曲才分离 vocals / accompaniment
           ├─ source vocal：严格无声辅音层
           └─ wsl.exe → song_svs_wsl_bridge.py
              └─ private Linux UUID Job
                 ├─ SoulX preprocess：FunASR lyric hotword + MIDI/note metadata
                 ├─ strict LRC ↔ segment/note alignment + official G2P
                 └─ authenticated 59-prompt bank + bounded C0/C1/C2 quality search
                    └─ global DP → exact absolute-offset 24 kHz timeline
              └─ stage output → delete Linux Job → publish generated vocal
           └─ reviewed FFmpeg mix → WAV + 320 kbps MP3
```

## 3. 元数据、歌词与真实限制

### 3.1 哪些文件名可用于歌词匹配

确认窗口提供可选的 **Song title** 与 **Artist**。两项必须同时留空或同时填写；每项去除首尾空白后最多 256 个 Unicode code point，控制字符、孤立 surrogate、缺一项或额外 Contract 字段都会被 IPC Parser 拒绝。手动输入时，它优先于错误或缺失的媒体 Tag／文件名，且只送入 Main 的 LRCLIB 查询，不会进入 Worker argv、日志、返回的 `SongCoverState` 或歌词文件；用于匹配的 duration 仍只能由 FFprobe 从所选音频取得。选择任一 voice conversion 时这两项隐藏且不会被传递。

两项留空时，优先使用文件内的可信 title + artist tag。Stem 模式只探测第一步选中的 vocal stem，不从 accompaniment 猜歌名。只有一个 Tag 时，文件名还必须是恰好两段的 `A - B`、`A – B` 或 `A — B`，且该 Tag 能明确对应其中一段。

完全无 Tag 时，Main 只会对清晰的两段式文件名尝试 `Artist - Title` 与 `Title - Artist` 两种顺序；它也保守识别常见歌词视频导出的精确 `Artist_Title【动态歌词_Lyrics_Video】`／`Artist_Title【動態歌詞_Lyrics_Video】` 形状，但不会把普通下划线任意解释成歌手与歌名。若两种顺序都得到可信 Record，它们必须是同一个 ID，只有一边得到可信结果时也可接受。若两个顺序得到不同的可信 Record，就要求更明确的 metadata，而不是任意选一个。`song.wav`、`track.wav`、`audio.wav`、`vocals.wav`、`instrumental.wav`、`recording.wav`、`output.wav`、Screen recording、日期、UUID 等通用／生成式无标签名称不会用于猜歌。此时应在确认窗口同时填写真实歌名和歌手；这也是仅名为 `vocals.wav` 的 Stem 的正常用法。

这里没有 Audio fingerprinting、声纹识别或声学歌曲识别；Main 只使用 FFprobe metadata、受限文件名证据、时长与 LRCLIB 返回字段。改名不能保证匹配，低置信或多义结果仍会失败。

### 3.2 同步歌词是硬要求

LRCLIB 只有 Plain lyrics 而没有 `syncedLyrics` 时，Main 不会启动 SVS。LRC 必须包含有效、排序后落在音频范围内的时间戳；未定时正文、冲突 offset、同一时间戳的冲突文本、无法唯一归属到 SoulX segment 的句子，都会 Fail Closed。当前 UI 可以覆盖查词所需的歌名与歌手，但不能手动粘贴、编辑或校正歌词内容。

当前歌词校正只覆盖 Mandarin Han：繁体通过固定、已验证的 OpenCC `t2s` 资产转为简体；标点和空白可忽略，但英文、日文假名、数字、罗马音、混合语言等不能被静默丢弃。FunASR 热词只是识别提示，最终 LRC/note 对齐才是准入边界。

### 3.3 网络与离线边界

网络只用于 Main 发起的 LRCLIB 查询。用户选中 **Lyrics-driven singing** 并确认 **Choose audio** 后，Main 会向固定 `https://lrclib.net` 发送从手动覆盖、可信 Tag 或文件名解析出的歌名、歌手，以及 FFprobe 取得并四舍五入的音频时长；不会上传歌曲、人声 Stem、伴奏、模型、Prompt、本机路径或生成结果。查询使用无凭据 HTTPS GET，因此这些匹配字段会成为该请求 URL 的 Query；两种 voice conversion 均不进行在线歌词查询。

没有网络、LRCLIB 不可用或未找到可信同步歌词时，歌词路径不会回退到猜测，也不会自动切换 Legacy 路径。歌词准备完成后，Windows／WSL 的 Demucs、FunASR、ROSVOT、RMVPE、OpenCC 与 SoulX 模型均从本机读取；子进程强制 Hugging Face、Datasets 和 Transformers Offline，移除 Proxy，并丢弃 Vendor stdout/stderr。歌词查询还必须通过 Stage 12 的统一网络策略：当前会话开启 Internet，并允许精确域名 `lrclib.net`；代理、超时和关闭后的取消都由 Main 管理。Choose audio 仍需要明确的方法选择与任务确认。联网设置不会自动授权上传音频或其他外部写入；查询记录功能见 [联网说明](19-INTERNET-AND-SERVICES.md)。

默认 Voice conversion 与显式 Legacy RVC 都从源音频转换，不访问 LRCLIB。

## 4. 输入、输出、安全与生命周期

| 项目 | Contract |
| --- | --- |
| 输入格式 | `.aac`, `.flac`, `.m4a`, `.mp3`, `.ogg`, `.opus`, `.wav` |
| 输入大小 | `1 B`–`1 GiB` |
| 输入时长 | `1`–`720` 秒 |
| 输入方式 | 一首完整歌曲；或一对从同一时间点开始、彼此对齐的人声与伴奏 Stem |
| 默认 Engine | UI 默认 `source-svc`；用户可显式选择 `lyrics-svs` 或 `legacy-svc` |
| 手动查词身份 | `lyrics-svs` 可同时填写 Song title + Artist；仅覆盖 LRCLIB query，duration 始终来自 FFprobe |
| SVS 歌词 | 通过匹配门槛且无双向文件名冲突的 LRCLIB 同步 LRC；目前 Mandarin Han-only |
| 主唱与伴奏调性 | `-12` 至 `+12` 的整数半音；一个下拉框提供 Original、Lower 1–12 与 Raise 1–12，vocal 始终采用所选值；默认勾选 **Shift accompaniment with vocal** 时伴奏等时使用同一移调，取消后保持原调。该选择同时适用于 Lyrics-driven／Legacy 和完整歌曲／Stem |
| 分离模型 | Demucs 4.0.1 `htdemucs`, CUDA, two-stems vocals, `segment=7`, `shifts=1`, `overlap=0.5`, `jobs=1`；Stem 模式跳过 |
| SVS 推理 | Mandarin SoulX-Singer；私有 59 条 Prompt bank（48 corpus + 10 emotion + 1 anchor）；音域/phoneme 优先自动选择，歌词/谱面 emotion/style 仅同分决胜；`control=score`、`auto_shift=False`、`pitch_shift=0`、CUDA FP16；确定性且全局有界的 C0/C1/C2；结构硬门禁 + acoustic soft score + global DP + 精确绝对时间线拼接 |
| 混音处理 | 60 Hz high-pass、0.5 dB Presence、线性 +1.3 dB 人声补偿、2.2–5.2 kHz vocal-keyed accompaniment ducking、-1 dBFS limiter；无 Vocal Compressor/Makeup。-18 dB 无声辅音层仅用于 Lyrics-driven SVS；Legacy RVC 不混回原唱辅音，固定 Protect 参数不代表无声恢复保证 |
| 导出 | 44.1 kHz stereo 16-bit PCM WAV |
| 预览 | 44.1 kHz stereo 320 kbps MP3 |
| 并发 | 全局一次一个任务 |

React 只能看到安全 Base Name、UUID、Engine、固定阶段、0–100 进度、播放位置和总时长（秒）、是否存在输出和净化错误。源路径、输出路径、歌词正文、模型路径、PID、stderr 和音频字节不进入公开 `DesktopApi`。歌词正文也不进入 Native argv、Worker stdout 或日志。

SVS Worker 启动时要求 Windows Job Root 是完全扁平的 allowlist：必须只有 `lyrics.lrc`、`lyrics-manifest.json` 与可选 `lyrics.txt`，任何额外文件、目录或链接都会失败。成功清理后再次检查该目录必须恰好只剩 `elysia-cover.wav` 与 `elysia-cover.mp3`；这也会把处理期间的晚到注入变成失败，而不是发布部分清理的结果。

Main 只允许一个启动中、转换中或收尾中的任务。取消必须匹配准确 UUID。Windows 取消使用绝对 `%SystemRoot%\System32\taskkill.exe /pid <PID> /t /f`，检查退出码并等待 Worker 关闭；确认原进程树结束后，两类 WSL 任务还会启动独立的 cleanup-only Worker。该 Worker 只把同一个规范 UUID 交给固定 WSL Bridge；Bridge 只重建私有 `jobs/<UUID>` 这一直接子目录并验证 owner、mode、类型和无链接边界，然后删除这一个目录。cleanup-only 也有固定成功帧、输出上限和超时；自身超时时必须确认整个 cleanup 进程树停止，才允许以后重试。无法确认任一进程树结束时，不会删除仍可能被 FFmpeg／WSL 使用的目录，也不会释放任务所有权。此时退出请求也会被 Main Shutdown Gate 拦住；清理仍在进行时重复点击退出不能绕过该 Gate。

三条 Worker lifecycle 都是严格、不可跳阶段的固定序列：

```text
lyrics-svs: validating → separating → transcribing → aligning
            → synthesizing → mixing → complete

legacy-svc: validating → separating → converting → mixing → complete

source-svc: validating → separating → converting → mixing → complete
```

Stem 模式仍发送 `separating`，但不会调用 Demucs。Worker 事件只接受固定 Prefix、字段、进度值和对应 Engine 的固定阶段顺序；单行最多 64 KiB、整个 Native 任务最多 32 MiB stdout，默认两小时总时限。WSL Bridge 还有独立的 2 KiB line、256 KiB total、128 event 协议界限；Vendor 文本不会被透传。

Main 会检查最终 WAV 是 44.1 kHz stereo 16-bit PCM、MP3 有合法帧头，并固定两个输出的文件身份；播放和导出前再次复核，路径被本机其他进程替换时会 Fail Closed。播放只接受最多 64 MiB、带 MP3 Header、解码时长不超过 12 分钟的内容；指定输出设备不可用时失败，不暗中改用其他扬声器。Worker 关闭后的扬声器偏好读取最多等待 5 秒；结束、失败或取消都会撤销 Blob URL。

临时结果位于创建任务时受管数据根的 `audio/song-covers/<job-id>/`。创建下一首会先删除上一首；**Settings → Data & storage** 清理临时音频或应用退出也会删除未导出结果。用户通过原生保存对话框导出的 WAV 位于用户选择的位置，不再属于临时结果。

启动时的崩溃遗留清理是受管 Lease：Main 通过未完成 Windows Job 中仍存在的 `lyrics-manifest.json` 或 `singing-request.json` 识别 WSL 事务，先按同一个 UUID 尝试 WSL cleanup-only，再删除 Windows Job Root；它不会扫描或递归删除整个 WSL jobs 根目录。Lease 仍在运行或失败待重试时会阻止受管数据根移动。取消、Worker／Bridge 失败、输出身份验证失败或超时后，如果私有 Linux Job、歌词、人声、Stem 或 Windows Job 目录无法删除，Main 会保留该精确 UUID 和目录的清理所有权并显示固定错误；重试成功前不允许创建下一首或移动数据根。完成但尚未清理的结果固定在创建它的数据根，移动前应先导出需要保留的 WAV。

任务完成且 Dictate／Voice Call 未占用麦克风时会自动播放。Main 会原子保留 Voice 会话并使已开始但仍在读取文件或扬声器设置的 Song Cover 播放失效，避免麦克风与歌曲在竞态中同时启动。Composer 的独立 `SongCoverPanel` 以紧凑唱片卡显示文件名、方法、调性与真实阶段，提供运行中 Progress／Cancel，完成后 Play／Stop／Save WAV，以及不包含本机路径或 Native 诊断的固定错误。

转换进度保留后台最近报告的实际百分比；填充上的轻微动态只表示任务仍在执行，不按时间虚增百分比。`source-svc` 在准备后持续报告实际生成步骤和片段完成情况，随后进入混音；没有段内上报的旧引擎保留最近的阶段百分比。开启减少动态效果时关闭填充动画。

完成后显示可拖动、可用键盘调整的 Seek 时间条，以及当前位置／总时长。播放中跳转复用 Preload 已拥有的同一个音频元素；尚未播放时先记住所选位置，下一次 Play 从该处开始。Stop 保留位置，自然播放结束回到起点。进度数据与跳转命令均绑定当前作业和受信播放身份，Renderer 无需读取文件或创建另一个播放器。

右上角叉号只收起当前卡片，不取消转换、不停止播放、不删除输出。收起后音符按钮变为 **Show Song Cover**，转换期间也能重新打开并查看最新状态；新任务自动显示。此隐藏状态只属于界面，不改变临时结果原有的保留与清理周期。

Windows Worker 的封闭环境将 `USERPROFILE` 指向已存在的私有临时任务根。固定版本 Torch 即使未开启 crash dump，也会在导入时调用 `Path.home()`；完全缺少 Windows Home 变量会在 Demucs 启动前报错。这里既不继承真实用户 Profile，也不提前创建由 Legacy Worker 排他创建的 UUID Job 目录。Smoke 工具使用相同规则。

## 5. 本机 Runtime 与固定身份

### 5.1 Windows 共享与 Legacy RVC 资产

这些仓库内目录由根级 `.gitignore` 排除，并受 Distribution audit 约束：

```text
models/cache/GPT-SoVITS-v2-240821/
├── runtime/python.exe
├── ffmpeg.exe
└── ffprobe.exe

models/cache/singing-runtime/
├── site-packages/demucs/
└── torch/hub/checkpoints/955717e8-8726e21a.th

models/cache/rvc-v2-40k/                    # Legacy fallback only
├── assets/hubert_base/
│   ├── config.json
│   ├── preprocessor_config.json
│   └── pytorch_model.bin
├── assets/rmvpe/rmvpe.pt
├── configs/
├── infer/
└── ... pinned RVC v2 Python source

models/weights/rvc/elysia-v2-40k/           # private Legacy fallback only
├── model.pth
└── model.index
```

| Local asset | Bytes / count | SHA-256 |
| --- | ---: | --- |
| Windows Runtime `python.exe` | 103,416 | `07c96337729e3c4c986e8f5660a97cb475c4c04842606be2654b37892af57de9` |
| Windows `ffmpeg.exe` | 52,925,440 | `b6a4d917a444790f4c06ada640c1c0c95aecde2f8953ed8d0dfb19352500bfcd` |
| Windows `ffprobe.exe` | 122,135,040 | `2da5b980a9a14a808f423d181c4ed51c2b8af11b1366699f3f7eab0609926f8f` |
| Demucs-specific singing runtime aggregate | 119 files / 1,107,739 bytes | `3146d43372b416a46c17c2d06227f98b92c1e26c62eea208e6277d1428b30712` |
| `htdemucs` checkpoint | 84,141,911 | `8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4` |
| RVC v2 Python tree | 130 files / 1,382,718 bytes | `a391270fe0b38307c3c966c5cb394d947d177990fae64307cb38313ae762b6c5` |
| Windows-compatible HuBERT directory aggregate | 3 files / 189,207,510 bytes | `c4843b2163be0aebac54b770579ad8c2b107f42c2fbc658d7b8ce7d244a54560` |
| RVC RMVPE checkpoint `assets/rmvpe/rmvpe.pt` | 181,184,272 | `6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193` |
| Selected Elysia RVC v2 e50 checkpoint `model.pth` (training export `elysia_rvc_50e_e50_s19850.pth`) | 55,232,507 | `cb3fec4d975eafd7c6b73bd096a6e6cdd6b9c3ca1d6793320d48fb17bea2b9c8` |
| Elysia retrieval index `model.index` | 31,588,619 | `86a2da597f7a09d8cb27bd2dad3f1bcfa6fd6a561268622f4daf94ab1de8737e` |

生产 `model.pth` 是所选 e50 导出的规范副本；生产 `model.index` 对应本机训练输出 `source/logs/elysia_rvc_50e/added_IVF256_Flat_nprobe_1_elysia_rvc_50e_v2.index`。训练文件名与训练目录仅用于来源追踪；运行时只接受上方固定生产路径与字节身份。

RVC Source 来自 `https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git` 的固定 Revision `81eed5e8f68b6bed1789f682fe78cdd324495afc`；该 Checkout 的 `LICENSE` 为 MIT。这只说明被该 License 覆盖的上游源码，不会为 HuBERT、RMVPE、私有训练数据、爱莉希雅 Checkpoint 或生成翻唱自动授权。

原始评估 HuBERT 使用 PyTorch Parametrizations 的 `original0`／`original1` 权重键；Bundled Transformers `4.36.2` 在 Windows RVC 路径加载时忽略这些键，导致 Content Feature 静音。生产 `pytorch_model.bin` 是本机一次性、逐键的兼容转换：只把相应键名映射成该运行时识别的 `weight_g`／`weight_v`，不改变任何 Tensor 值。转换后的文件为 189,205,793 字节；它与另外两份未改变的 HuBERT 配置共同构成上表固定 Aggregate。应用运行时不会动态迁移、猜测或修复未知 Checkpoint，而只接受这组已审核生产字节。

Python Tree Digest 对排序后的相对 `.py` 路径、文件长度和文件内容做 SHA-256；HuBERT Aggregate 对固定三个文件使用相同的相对路径／长度／内容规则。Demucs Aggregate 包括固定 Package、Extension、`demucs/remote/files.txt` 与 `htdemucs.yaml`，Checkpoint 自身另按长度和 Hash 验证。Worker 在验证／Import 前删除生成式 `__pycache__`，并拒绝可覆盖受保护 Import 的 Sibling provider、链接、未审核 Compiled Code 与异常 Scratch Directory。私有 `model.pth` 与 `model.index` 也由 Worker 在启动推理前以固定长度和 SHA-256 复核。

### 5.2 私有 WSL SoulX Runtime

SVS Runtime 位于 Linux 用户私有目录，不在 Repository 中：

```text
~/.local/share/elysia-ai/soulx/
├── python/cpython-3.10.22-linux-x86_64-gnu/
├── prep-env/
├── infer-env/
├── source/                         # fixed clean SoulX checkout
├── models/
│   ├── SoulX-Singer/model.pt
│   └── SoulX-Singer-Preprocess/    # RMVPE, ROSVOT, FunASR Mandarin
├── prompts/elysia-bank-v1/
│   ├── manifest.json               # private canonical index; not in Git
│   ├── audio/                      # 59 private prompt WAV files
│   └── metadata/                   # 59 private score/phoneme records
└── jobs/                           # mode-0700 transient UUID jobs
```

Windows Worker 不信任 Windows `HOME`，也不硬编码 Linux username。它以固定 `/usr/bin/python3 -I` 和固定 Script 从 POSIX `pwd` 解析当前 WSL UID 的 Home，只允许在严格验证后的绝对路径后追加 `.local/share/elysia-ai/soulx/prep-env/bin/python`。Runtime 继续按同一 Linux UID 验证 `$HOME/.local/share/elysia-ai/soulx`、Job owner、mode 与无链接路径。

高信任资产按固定长度与 SHA-256 验证，包括 Linux CPython、固定 FFmpeg、SoulX model、RMVPE／ROSVOT、Mandarin FunASR model/config/tokens/seg dictionary、Prompt bank canonical manifest、manifest 引用的 59 组私有 Prompt WAV/metadata，以及 OpenCC Python/native/config/dictionaries。Prompt bank 由 48 条 corpus、10 条 emotion 与 1 条 anchor 组成；准确文本、逐条路径、逐条 Hash 和音频内容只存在于本机 manifest，不进入 Git、文档、构建产物或诊断。`prompts/`、bank、audio 与 metadata 目录必须由当前 WSL UID 拥有且精确为 mode `0700`，manifest、WAV 和 metadata 文件必须精确为 mode `0600`；推理会以 `O_NOFOLLOW` 打开来源文件，从同一 descriptor 复核类型、owner、mode、单链接、长度与 SHA-256，再在验证时流式复制到 sealed memfd。来源 descriptor 随即关闭，SoulX DataProcessor 只能经 `/proc/self/fd` 读取不可变密封副本，避免先验路径校验后被 rename/swap，也避免已认证 inode 随后被同 inode 写入篡改。整个静态 Runtime tree 还逐项验证 owner、目录/普通文件权限和路径边界；内部 symlink 仅可解析到同一受信 Runtime 内，并会递归复核其目标。SoulX Source 还必须是无修改、无 Untracked file 的固定审核 revision，并且 `pretrained_models` symlink 必须只指向固定私有 Model 目录。

该验证不是完整 Linux 环境供应链证明。当前 `prep-env`／`infer-env` 中未单独列入高信任清单的 Python 包、Torch/CUDA Native Extension 仍以“同一操作系统用户准备并信任这个私有环境”为前提。固定解释器、模型、关键配置和 Source Revision 可以发现已知核心资产替换，但不能证明每个可导入依赖都来自可重建、签名或完整树摘要认证的环境。

应用不会自动下载、安装、复制或更新 Windows／WSL Runtime 和模型。资产缺失、大小或 Hash 变化、SoulX checkout 不干净、链接目标变化或固定目录不安全都会 Fail Closed。

## 6. 运行与打包状态

- 完整歌曲的 Demucs 与 Legacy RVC 在 Windows CUDA Runtime 中执行；SoulX preprocessing／inference 在 WSL CUDA Runtime 中执行。没有兼容 NVIDIA GPU、WSL GPU integration、驱动、足够显存或上述当前私有布局时会失败，不会把长曲静默切到 CPU 或 DirectML。
- Worker 没有指定 `wsl.exe --distribution`，因此 Windows 当前默认 WSL Distribution 必须提供 `/usr/bin/python3`，且当前 UID 的 Home 下必须存在固定 SoulX Runtime suffix；它不会搜索其他 Distribution 或任意模型路径。
- Main 给 Windows Worker 的环境采用 Allowlist；WSL child 使用独立的最小、Offline、Proxy-free 环境。LRCLIB 是唯一预期的在线步骤，模型推理不会隐式下载缺失资产。
- WSL preprocessing 与 inference 各自在新的 Linux Session 中运行，并在 UUID Job 下原子记录 PID、PGID、Session ID、Kernel start ticks 与随机 Stage ID。Child Stage 在接触 Vendor/私有资产前必须等待并验证父进程发布的准确 Lease：Job token、Stage ID、capability、PID/PGID/SID 与 kernel start ticks 全部匹配，且 Child 本身必须是该 Stage 的 session leader；缺失、超时或不一致均 Fail Closed。取消/超时/cleanup-only 会先写不可逆取消标记，再以 `flock` 串行化启动竞态，只对该准确 Lease 的同 UID 成员执行 TERM→KILL 并确认全部退出；不会按进程名枚举或影响其他 Job。私有 Runtime 共用 90 分钟总预算（preprocess 最多 35 分钟、inference 最多 55 分钟），为 Electron 的 2 小时 Job 上限保留原生分离、混音与清理时间；WSL cleanup 内层 30 秒仍小于 Main 的 60 秒外层清理期限。
- 当前 Electron Builder 只打包 `dist/`, `dist-electron/` 与 `package.json`；Python Workers、Windows `models/cache/rvc-v2-40k/`、私有 `models/weights/rvc/elysia-v2-40k/` 与 WSL Runtime 均不在安装包中。因此三种引擎目前都只面向已手动准备 Runtime 的源码开发环境。
- Electron Main 提供闭集 Readiness：安装包固定报告 Runtime 未包含；源码环境仅以 `lstat` 检查必要 Worker/Core 文件是普通非链接文件，不联网、不运行 Python/WSL/模型。Composer 会禁用“新建翻唱”并显示固定恢复提示，但不会阻止播放或导出已经完成的结果；Main 在文件选择器前后仍会权威复核，Renderer Readiness 不能授权启动。缺少本机 RVC 资产时不会自动下载、切回旧引擎或启用未验证的 CPU 替代路径。
- Repository 内的 `models/cache/` 与 `models/weights/` 被 `.gitignore` 排除；WSL Runtime 位于 Repository 外。Ignore rule 本身不是发布授权，实际 Release 仍必须检查打包文件清单。
- 歌词 SVS 尚不支持用户提供本机 LRC、手动修改歌词正文、Audio fingerprinting、非 Mandarin 或混合语言歌词、无同步时间戳的歌词，以及自动从失败的 SVS 降级到 Legacy RVC。歌名与歌手则可在确认窗口成对覆盖。
- 当前私有 Prompt bank 固定为 59 条：48 条通过 corpus 筛选、10 条覆盖闭集 emotion、1 条 anchor。每条都带已审核的单段 score/phoneme metadata 与预计算音域/语速/phoneme Profile；生产 Runtime 会完整验证 canonical manifest 和全部引用资产，既不会把 `slicer` 中未入选的日常语音盲目当作 Prompt，也不会在运行时自行扩充 Bank。多 Prompt 搜索已经实现，但它只是确定性、有界的局部质量选择，不能据此推断所有歌曲、音域或情绪都已经通过人耳验收。
- 选择顺序保持保守：目标段的 note median/P10/P90/span、syllables-per-second 与 phoneme 缺失量先决定 acoustic ranking；从简体歌词词汇/标点和谱面密度/动态得到的闭集 emotion/style proxy 只在 acoustic cost 完全相同时决胜，不确定时保持 `None`/`neutral`。它不是训练出的情绪识别器，也不会为了“更像情绪”选择明显不合音域或缺 phoneme 的 Prompt。
- 每段 C0 使用首选 Prompt 与 canonical seed 0。只有 C0 结构失败或 soft loss 较高时才申请 Rescue：C1 是同 Prompt 的 canonical seed 1，C2 是第二 Prompt 的 canonical seed 0；没有第二 Prompt 时不会虚耗 C2。结构硬失败的候选会先占用 Rescue 预算，soft loss 较高的候选只能使用剩余预算，避免较早但可用的 soft 请求挤掉必须恢复的段。默认每段最多三次、最多 12 个 Rescue segment、额外生成时长通常不超过 voiced 总时长的 25% 且绝对不超过 90 秒；单段歌曲仍可在 90 秒绝对上限内获得一次有界救援。该固定搜索面避免无限重试与指数候选爆炸。
- Structural hard gate 只处理可以安全判定的波形缺陷；未校准为通用歌唱判官的 F0/note、dropout、false voicing 和 spectral 指标仅进入 acoustic soft score。原始 SoulX float 的绝对值超过 `1.0` 不等于已经削波：FP16 量化后持续贴近同号 rail 的平台，或真正到达 full-scale rail 的样本，才是硬证据；前者不会把孤立冲击误判为削波，后者也不能被局部异常掩盖。可用不超过 `-6 dB` 修复的 Headroom 不触发 Rescue 或 local loss，而是在 acoustic cost 完全相同时作为稳定路径决胜项；超过恢复容量则是可触发有界 Rescue 的硬失败。若整条路径能用一次受限衰减收敛到 `0.994`，就保留原始段间动态，否则 Fail Closed。音高 loss 对 p90、gross deviation 与 octave evidence 单调加重，避免更严重的偏移反而取得较低分。通过门禁的候选由全局 DP 按时长加权 local loss、边界跳变与 Prompt switch cost 选出一致路径，并保留必要的 Pareto 前缀，避免未来 Headroom 项推翻过早的局部 tie-break。拼接不拉伸、不重叠、不对连续段做 Crossfade；候选先收敛为各自 absolute interval，再对实际放置波形评分。数字静音空隙边缘使用 5 ms Ramp；两个独立候选恰好连续时，也分别在前段尾部和后段头部的现有区间内收零，绝不重叠或移动时间。长空隙仅免除不存在连续波形时的跳变/loudness 比较，不免除 Prompt switch cost。最后整首只执行一次必要的衰减，不提升音量、不逐段归一化、不使用 Limiter，并以 `0.995` 最终峰值上限和完整结构合同复检。
- SoulX model 与 DataProcessor 每项任务各加载一次；Prompt preprocessing 有界缓存，GPU Prompt LRU 最多保留两条，第三条进入前先逐出最旧 GPU payload，因而不会出现三条同时驻留的瞬时峰值。Target CUDA tensor 不做整曲缓存，只在 C0 和获准 Rescue 的当前段构建；候选 PCM 使用 float32，帧分析采用 strided view。这样候选预算同时约束推理次数、显存驻留与分析开销，而不是把多 Prompt 变成无界穷举。
- 2026-10-09 已在当前私有 Runtime 上验证 canonical manifest 和全部 59 条 Prompt，并让 59/59 都通过真实 SoulX DataProcessor 加载；生产 `_verify_runtime_assets` 也完成整组只读身份验证。该证据只证明 Bank 结构、资产身份与 preprocessing 兼容，不代表 59 条都完成了整曲推理或人耳质量验收。质量/推理 CI 使用 Fake Model/Processor 与合成波形，不读取这些私有音频或 manifest 内容。
- 2026-10-08 在当前 Windows + 默认 `Ubuntu-24.04` WSL + CUDA 私有 Runtime 上完成了一次 6.71 秒受控 Lyrics-SVS 贯通 Smoke：生成结果是 44.1 kHz、Stereo、16-bit PCM WAV，并同时生成 44.1 kHz、Stereo、320 kbps MP3；成功 Job 只保留这两个输出。该证据早于 59 条 Prompt bank 与多候选质量选择接线，只验证当时 Windows Worker → WSL Bridge → SoulX → Windows Mix 的机器互操作，不能作为当前多 Prompt 路径、完整歌曲、多音区、快速咬字、取消/超时或人耳音质矩阵已经通过的证据。
- 2026-10-10 在同一私有 Runtime 上完成当前实现的两级验收。第一层以 QA-only、与目标 vocal 自洽的同步 LRC（明确不是产品查词回退）运行 194.808 秒真实整曲 SoulX：四个目标段的 C0 都通过结构门禁，最后一段按有界预算额外生成 C1/C2 并由全局 DP 选择较优候选；发布前 24 kHz mono PCM16 峰值为 32571，满幅样本率为零。第二层从干净 UUID Job 运行正式 Windows Worker，经 WSL、严格混音及私有清理后只留下 194.808 秒的 44.1 kHz Stereo PCM16 WAV 与 MP3 两个非空普通文件。另将 6.71 秒受控目标在全 59 条 Prompt bank 下连续运行两次；每次只生成 C0/C1 两个有界候选，最终 WAV 的 SHA-256 完全相同。该证据覆盖当前多 Prompt、候选评分、连续段收零、一次全局 Headroom、整曲发布与确定性重放，但仍不替代对不同歌曲、音域、语言、取消/超时和主观音质的人类矩阵验收。
- 2026-10-10 在 sealed-memfd、严格 duration/frame/F0 与逐 source-note coverage 加固后，先以真实 SoulX/CUDA 完成一条短目标，再把来自 anchor、emotion 与 corpus 的四条不同真实 score record 拼成连续四段目标并独立运行两次；三次都经全 59 条 Prompt 的生产选择与密封读取成功完成，四段任务的两次候选选择完全相同且最终 WAV SHA-256 一致，所有临时输出随后删除。该检查证明当前 Torchaudio/DataProcessor 能读取 sealed-memfd WAV，快速短音符的正常 BOW/content/EOW 覆写不会被误拒，并且当前多段路径保持确定性；同 inode 认证后改写隔离则由使用合成私有 bytes 的安全回归覆盖。它不扩大上一条对主观音质与歌曲覆盖面的结论。
- 2026-10-09 在当前 Windows CUDA 私有 Runtime 上以一对 248.294 秒 Stem 完成 Legacy RVC Worker 贯通验收：RVC 子进程生成完整 40 kHz mono PCM16 人声，最终只保留 44.1 kHz Stereo PCM16 WAV 与 320 kbps MP3；WAV 峰值约 `-3.44 dBFS`，没有时长截断或满幅削波。关闭上游按 Shape 缓存的 CUDA Graph 后，实测显存从失败路径接近 12 GiB 降到约 2 GiB。该验收证明当前机器的完整 Worker/混音/清理边界可运行，不代表所有歌曲的人耳音色、分离质量、咬字或音域都已通过。
- 在相同已审核 Runtime、Device 与输入下，两次独立短任务产生了字节一致的 PCM（SHA-256 相同）。该结果验证本机任务重启的可复现性，不承诺跨 PyTorch、CUDA、cuDNN、驱动或硬件版本复现。
- 固定随机源后又以同一首 248.294 秒 MP3 完成 `Demucs → RVC → mix` 整曲重跑；输出仍为 44.1 kHz Stereo PCM16 WAV 与 320 kbps MP3，WAV 峰值约 `-3.84 dBFS`，没有满幅样本或时长截断。额外 A/B 显示二次 UVR 去混响、单侧／自适应声道、BS-Roformer 替换，以及先移调再移回都会在问题片段增加掉音、音高错误、相位或高频伪影，因此它们没有进入默认链路。
- 同日还执行了隔离的 UV-aware 训练 Pilot。原训练标签把全部 899,182 帧插值成有声音高；重新以固定 RMVPE 阈值提取后，364,270 帧（40.511%）保持为连续 F0 `0`，对应的 RVC coarse UV bin 为 `1`。只改变推理 UV 语义的 e50 控制组没有达到预设的 10% 问题片段改善门槛；在这批纯说话语料上低学习率适配 5 轮的新候选也没有改善已知电音段，并令三份保留对白的掉音率增加 3.03–6.76 个百分点，高音片段 Spectral-flatness P95 上升约 17.1 倍。三组每组 11 个输入的两次独立 PCM 均完全一致，但新候选触发预设灾难性退化门禁，因此没有继续到 e10，也没有接入 UV 推理改动、替换生产 Checkpoint 或改动 Index。
- 对剩余电音的逐段定位排除了分块接缝、削波和宽带噪声：四个实际输出接缝位于约 `57.82`、`129.04`、`176.36`、`244.96` 秒，边界跳变只有 0–2 个 PCM LSB，而已知问题区位于 `186–197` 秒。异常集中在源音辅音／无声帧被 RMVPE + RVC 生成稳定音高的 false voicing。早期预注册实验均未达到完整质量门槛：频谱局部衰减没有减少该区 386 个误有声帧；把 RMVPE 阈值提高到 `0.05`／`0.08` 只减少 10／13 帧，却令漏音代理分别增加 `0.007389`／`0.012315`；最强的全局 pYIN + RMVPE 候选虽把 386 帧降至 365 帧（`5.4404%`），仍使低音样本 dropout 增加 `0.014899`、身份样本最坏频谱指标升至基线 `2.92207` 倍，并在其他样本新增 `0.009709` 的误有声率，因此该全局方案没有进入生产。
- 2026-10-12 曾隔离评估范围更窄的事后 transition guard。它以 11.025 kHz YIN、RMS、稀疏归一化自相关和区域 pYIN 寻找 source-unvoiced／output-voiced 转场；在当前 248.294 秒粤语样本上，宽松预筛形成 43 个窗口、共 95.939 秒，严格层最终选中 305 帧、39 个短区间、合计 1.770525 秒，其中已知 `186–197` 秒问题区占 9 个区间、0.371475 秒。该结果只说明检测器可以复现候选位置，不证明替换波形具有可接受的人耳质量。
- 真实应用重新生成的完整 WAV 随后否决了这个修复。旧逻辑在入选核心执行 `converted * (1-envelope) + highpass(source) * 0.10 * envelope`，即完全移除 RVC 主体，只留下 `3.2 kHz` 以上且衰减 20 dB 的源辅音；控制 A/B 的人声核心因此下降约 27–53 dB。新文件时长仍精确为 248.293878 秒，排除了删帧与时间漂移，但 `186–197` 秒九个已知修复窗的中心声道凹陷中位为 `-2.69 dB`、最坏为 `-9.3 dB`，未修复基线中位约 `-0.04 dB`；全曲最明显的 `191.06–191.18` 秒短时能量骤降约 `13.3 dB`。5 ms Fade 不能掩盖这种频谱／包络塌陷，混音器又以转换人声驱动 5 ms attack／120 ms release 的侧链，使短暂静音进一步表现成 pumping、迟钝与卡顿。因此该候选在人耳反馈与客观回归上都失败，不得作为已解决电音的证据。
- 生产 Adapter 已撤销所有事后 transition replacement，直接发布上游 segmented RVC PCM；正常任务不会再运行检测器、pYIN 或源辅音重采样。这项回退消除的是修复器新增的卡顿风险，并不声称已经消除原有 false-voicing 电音。实验分析仍保留 12 秒下限、歌曲时长 15% 与 30 秒上限的 Fail Open 预算，任何候选范围过宽时必须原样返回 RVC PCM。未来方案进入生产前还必须以 raw segmented 为对照通过盲听，并证明没有新增超过 1 dB 的短时 envelope notch、没有改变 sidechain key，同时在问题段与非问题段都不退化；不再允许以安静高通源音做时域全替换。
- 离线分析代码保留两项经逐样本验证的性能修正，便于继续研究而不会复活失败修复：pYIN 的 scoped banded Viterbi 与 librosa 0.9.2 dense decoder 在真实 222-bin／444-state lattice 上 path／final score 一致；denominator-aligned 十秒 polyphase 分块在 44.1→40 kHz 与 40→44.1 kHz 都与 monolithic 结果逐样本相同。UI 修复则与音频算法独立：由于上游 RVC 没有可信的连续完成比例，`converting` 阶段显示持续活动的不确定进度和 `Working…`，不再把阶段入口 `55%` 冒充实时百分比。
- 同一份干净 AI 翻唱源、同一次 Demucs Stem 和相同 seed／模型／参数还用于隔离分段 A/B；两边都绕过事后 guard。正常分段 raw RVC 用时 15.62 秒。临时 one-shot proxy 只在模型加载后把 upstream `t_max` 设为不可达值，不改生产或 Vendor 文件，但整首声码器在 RTX 4070 SUPER 12 GB 上 OOM：PyTorch 已分配 13.84 GiB 时仍尝试申请 4.82 GiB，没有发布半成品。已知 `186–197` 秒问题区本来就在约 `176.36–244.96` 秒的同一次 `vc()` 调用内，距切点约 9.6 秒，并不跨接缝；因此不能把该电音归因于拼接，也不能把不可执行的整首 one-shot 接入生产。证据指向源音辅音／无声转场被当前 RMVPE + RVC Profile 生成为稳定音高，而不是这份源歌曲本身有电音。

## 7. CMD 测试

以下命令覆盖 Legacy RVC Adapter/Worker，以及歌词对齐、SoulX Prompt 选择/质量评分/推理编排、WSL Runtime、Bridge 和 Windows SVS Worker pytest 文件：

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe -m pytest tests\test_song_rvc_runtime.py tests\test_smoke_song_cover.py tests\test_song_cover_worker.py tests\test_song_lyrics_alignment.py tests\test_song_svs_quality.py tests\test_song_svs_inference.py tests\test_song_svs_runtime.py tests\test_song_svs_wsl_bridge.py tests\test_song_svs_worker.py -q
.venv\Scripts\python.exe scripts\check_python_documentation.py

cd /d D:\Elysia_AI\desktop
npm run docs:check
npm run typecheck
npm run test:contract
npx playwright test tests\ui\app-shell.spec.ts -g "Song Cover|Dictate"
npm run dev
```

`npm run test:contract` 包含 `song-lyrics-provider.test.mjs`、`song-cover-lyrics-assets.test.mjs` 与 `song-cover-manager.test.mjs`。

需要重复本机私有 Runtime 验收时，先用显式 stems 与歌词运行只读预检。默认命令只验证 SHA-256、实际音频容器／编码／时长和 LRC 结构，不加载 SoulX、Demucs 或其他歌声模型，也不会联网：

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe scripts\smoke_song_cover.py ^
  --vocals "D:\private\vocals.wav" ^
  --accompaniment "D:\private\accompaniment.wav" ^
  --lyrics-lrc "D:\private\lyrics.lrc" ^
  --plain-lyrics "D:\private\lyrics.txt" ^
  --key-shift-semitones 0
```

Smoke 默认让伴奏跟随所选调性；测试保持原调的伴奏时增加 `--no-shift-accompaniment`。检查预检 JSON 后，只有显式增加 `--run-private-runtime` 才会创建新的临时 UUID Job 并调用生产 `song_svs_worker.py`。Smoke 使用固定四字段本机 manifest，解析完整封闭阶段协议，验证 44.1 kHz Stereo PCM16 WAV 与 MP3 的 Hash、编码、大小和时长，并再次执行 Worker 的 cleanup-only 协议确认 WSL 私有 Job 已清除。默认在验证后删除本机 Job；确需人工试听时可再增加 `--keep-output`，结果只保留在 `%TEMP%\elysia-song-cover-smoke\<stdout job_id>`。stdout 不包含歌词、输入路径、Runtime 路径或底层诊断。私人音频和歌词不得复制到 Repository、测试 Fixture 或文档目录。

手工验证时，在打开的 Elysia 中点击 Dictate 左侧音符。Dialog 应默认选中 **Voice conversion**，参考为《妖精小姐的魔法邀约》、原调；测试本节歌词路径时先选择 **Lyrics-driven singing**，并默认勾选 **Shift accompaniment with vocal**；当前歌词驱动模式只支持普通话／中文同步歌词。可信 Tag 或清晰两段式文件名可让 Song title + Artist 同时留空；ScreenRecording、`vocals.wav` 等通用名称应同时填写两项。只填一项时 Dialog 必须停留并提示，不得打开原生文件选择器。选择完整歌曲或 Stem、选择调性，并决定伴奏是否跟随后，再点击 **Choose audio**。取消伴奏跟随后，状态摘要应明确显示 Vocal-only shift，伴奏应保持原调。歌词 SVS 应依次显示 `validating → separating → transcribing → aligning → synthesizing → mixing → ready/playing`。若无网络、无可接受的 LRCLIB match、无同步 LRC、含非 Han 内容或无法严格对齐，应安全失败，而不是生成猜测歌词。

若选择 **Voice conversion** 或 **Legacy RVC**，应依次显示 `validating → separating → converting → mixing → ready/playing`，且不访问 LRCLIB。`converting` 不再切换成 `Working…`；Voice conversion 应随真实生成工作推进百分比，旧引擎没有上报时保留阶段值。验证完成后的 Seek 可在播放前和播放中跳转，收起／重新打开卡片不取消任务或丢失结果。

## 8. Legacy RVC v2 显式回退

**Legacy RVC** 是显式保留的 RVC v2 引擎，协议 ID 为 `legacy-svc`，默认 Voice conversion 则使用 `source-svc`。它适用于无法取得同步歌词、非 Mandarin 歌曲，或用户明确希望以源 vocal 的发音和时序引导转换的情况；用户必须在 Singing method 中主动选择它。

该路径不会读取或理解歌词。旋律、节拍、发音内容与演唱时序都来自输入 vocal；只选纯伴奏不会凭空生成歌词和演唱。处理步骤如下：

1. FFmpeg 把输入规范化为 44.1 kHz PCM；完整歌曲模式使用 Demucs `htdemucs` 分离 vocal 与 accompaniment，Stem 模式直接使用从同一时间点开始的已对齐输入；
2. 短命 RVC Adapter 在独立子进程中固定 speaker `0`、RVC v2 40 kHz model family、RMVPE F0、HuBERT、Index Rate `0.00`、Protect `0.33` 与 RMS Mix Rate `0.25`。它在 Vendor Import 前固定 `RVC_CUDA_GRAPH=0`：上游会按静音点形成不同长度片段，而一次性整曲任务缓存每个 Shape 的 CUDA Graph 没有 Replay 收益，只会持续占用显存。每项任务还会在构建模型前重置 upstream seed `114514`，关闭 cuDNN benchmark、固定 deterministic cuDNN selection，并设置 `CUBLAS_WORKSPACE_CONFIG=:4096:8`；没有启用 PyTorch strict deterministic algorithms，因为当前固定 Torch 的 RVC CUDA `cumsum` 没有该模式的实现。当前选定的 e50 声学 Profile 不把 FAISS 邻居混入输出；`model.index` 仍作为该私有 Profile 的固定、身份验证资产传入并校验，不能被替换成任意 Index。Renderer 只能提交 `-12..+12` 的整数半音及闭集的伴奏跟随布尔选择，不能改变上游 RVC 命令面、模型、Index、F0 方法或混合参数；
3. Adapter 禁用 Socket 与 Hugging Face 在线访问，只从显式 `models/cache/rvc-v2-40k/` 载入已验证的 Source/HuBERT/RMVPE，并从 `models/weights/rvc/elysia-v2-40k/` 载入私有 Checkpoint/Index。非 CUDA Device、路径跳转、不匹配的 Model Metadata、超范围／全零／长时间持续满幅 PCM 或既存输出都会 Fail Closed；
4. RVC 产生 40 kHz mono 人声。自有拼接适配层保留原切点、推理输入和整曲 F0，按各段实际拥有的源样本区间从生成结果取声，末尾长度依据预备 WAV 计算；这修复了 10 ms 切点与 20 ms HuBERT 特征网格造成的累积丢样，不补静音、不拉伸。Worker 必须先确认 40 kHz 样本数精确等于源时长映射，再重采样到 44.1 kHz；后面的 `apad + atrim` 只收敛采样率换算后的尾部取整，不能掩盖缺帧或少转一段。适配层仅绑定当前转换实例，结束时恢复，不修改固定 Vendor 文件；
5. Worker 不会把原唱的辅音高频层混回转换结果，以避免原唱泄漏及重新引入分离伪影。固定 Index Rate `0.00` 时上游 Protect 的 Feature Blend 是恒等操作，不能把 `0.33` 参数解释成无声内容保证。FFmpeg 对转换人声做 60 Hz High-pass、0.5 dB Presence 与线性 +1.3 dB 补偿，不使用 Vocal Compressor/Makeup；随后用人声驱动的窄频伴奏 Ducking 腾出空间，最后以 -1 dBFS 限幅；
6. 保存 44.1 kHz stereo 16-bit PCM WAV 用于导出，并生成 320 kbps MP3 用于应用内播放。

**Original key (`0`)** 最贴近原唱音高。任何非零值都会在 RVC 内有意移动检测到的 vocal F0；默认勾选伴奏跟随时，还会以 `asetrate → aresample → atempo` 等时移动伴奏，取消勾选则直接使用原调伴奏。这个选择同时适用于完整歌曲与 Stem。它是音域取舍，不是更精确的原唱复刻。主唱单独进行非八度移调时通常会与原调伴奏跑调或不协和；`±12` 是一个完整八度，会改变主唱音区但保留音级关系。应使用刚好适合目标声线的最小移调量：小幅移调通常比大幅移调保真，越接近 `±12` 越容易软化瞬态、放大人声伪影，或把 RVC 音高推到 `50–1100 Hz` 粗范围的裁剪边缘。伴奏跟随开启时，Raise 12 正好抵达当前单段 `atempo` 的 `0.5` 下限，Lower 12 使用 `2.0`，产品以一个八度作为对称上限。允许选择并不构成音质保证，`±13` 及以上继续 Fail Closed。若已有干净 Stem，优先使用 Stem 模式以避免 Demucs 分离伪影。

当前私有 RVC 声学模型只由 2,182 份说话对白 Slice 训练，没有干净的目标歌唱录音。完整作业验收只能证明处理链和输出边界可运行，不能证明所有歌唱音域与音素的音质；持续低音、高音、停顿及摩擦音仍可能出现发哑、电音、掉字或音高偏差。

### 8.1 2026-10-13 三首实际歌曲复核

对用户提供的三首歌曲以原调依次运行完整生产 Legacy 链：Demucs → 固定 RVC → 受限混音。三次最终任务均成功，分别用时约 25、26、30 秒；44.1 kHz Stereo PCM16 输出时长分别为 187.536、204.288、248.294 秒，均与目标样本数精确一致，无满幅削波样本，也未检测到源人声活跃时的数字静音。Windows 启动环境的 `USERPROFILE` 修复已包含在该复核环境内。随后补充了首次启动的私有 Profile 准备：Main 与 Smoke 在启动 Worker 前逐级创建并验证非链接的 `AppData/Local` 和 `AppData/Roaming`，显式设置 `APPDATA`／`LOCALAPPDATA`，避免 Torch／librosa／appdirs 在全新目录下解析 Windows 缓存路径失败；清理不会递归删除共享 Profile 或其他活动 Job。

这不构成听感验收。以源活跃门槛 −35 dBFS、20 ms 窗及全局中位增益对齐比较干声，第一首仍有累计 3.94 秒相对衰减至少 20 dB，最长连续 0.36 秒；后两首均累计 0.04 秒。第一首的 154.82–155.18 秒问题区发生在 RVC 干声，源 mono RMS 约 −29.76 dBFS，转换后约 −55.83 dBFS；源高频占比很高，符合无声／高频成分弱化的候选，不能只凭这些指标判断具体歌词掉字。该区域未见明显反相 downmix 消音，修改最终 limiter 或伴奏 ducking 不能补回转换阶段丢失的内容。

轻声到强声需同时检查响度包络和声线自然度。三个选定 16 秒片段的源／转换响度上升分别约为 8.62／8.36 dB、5.76／6.57 dB、3.38／2.13 dB，说明起伏并未统一被压平，也并非完全复刻。当前 RMS mix `0.25` 沿用固定上游实现；全局提高动态匹配不能由这组证据推出，也不能修复真假声、摩擦音或超出目标训练音域的问题，因此本次没有更改该参数或启用先前被否决的 transition guard。

完整 WAV／MP3、原唱与转换后的等响度片段、动态曲线、固定资源 Hash 与阶段记录仅保留在本机被忽略的 `models/cache/rvc-eval/evaluation/recheck-20261013/`，入口为 `listen.html`，机器可读证据为 `report.json`、`analysis.json` 与 `listening-review.json`。它们不进入版本控制或安装包；尚未获得人工听感通过结论，也不能推广为任意语言、复调和声或双人重叠演唱的无伪影保证。

随后确认并修复了一个独立的分段时序缺陷：旧拼接对部分 10 ms 奇数帧区间采用固定左右裁剪，累计丢失已生成的核心样本，最后再以补齐掩盖总长差。三首整曲的同一次神经生成数据分别重放旧、新拼接：旧输出逐样本复现之前生产结果，新拼接保留同一全局 F0、原切点及每段模型输入，并与独立的绝对时间裁剪重建逐样本一致。RPReplay、TruE、红豆分别取回 1,355、1,440、1,120 个 40 kHz 样本，全部分段累计偏移为零且尾长精确；这是从已有生成上下文取回音频，不是静音填充。完整 RPReplay Stem Worker 再次通过严格样本数检查及混音，44.1 kHz Stereo 输出为 10,949,760 个样本。用户指出的 RPReplay 电音区发生在原分段偏移之前，因此该时序修复没有消除那处电音，TruE 的高频缺失也仍存在；证据保留在 `quality-20261013/owned-core-validation/`。

后续质量实验保留在独立的 `models/cache/rvc-eval/evaluation/quality-20261013/`。降低 RVC 随机噪声、改为全精度、统一缩短分段均没有得到三首一致改善，未改变生产参数。独立的 SoulX-Singer-SVC 音频转换实验与现有歌词驱动 SVS 是不同路径：它不重新生成歌词，也不自动移调；模型及 Whisper 依赖只放在隔离的本机评估目录，当时尚未接入应用。说话参考下，TruE 的 8 秒问题片段中相对能量缺口从 0.54 秒降至 0.02–0.04 秒，但用户实际试听指出红豆声线偏离、RPReplay 片段约第 6 秒的多个候选仍有电音，因此该批候选明确未通过验收。F0 检测器之间的八度歧义、能量和频谱代理均不能推翻听感反馈。此后用户选定两组歌唱参考并确认局部电音修正，后续接入与证据见第 9 节。

旧引擎曾在共享 Runtime 下创建 `raw/` 与 `results/` Scratch；当前 RVC 流程不再读写这些目录。转换中间 WAV 只存在于 Main 拥有的 UUID Job Root，发布前必须是新文件，完成/取消/失败/退出都沿现有受管 Job 清理路径收敛。应用不会扫描或删除 RVC Runtime 内的未知用户文件。

## 9. 原声内容引导的歌唱转换

`source-svc` 复用 Windows 音源验证、Demucs、WSL 私有作业、受验证进程 Lease、取消、恢复及混音。Main 只写入固定 `singing-request.json`：版本、`fairy` / `elysia` 参考 ID 和 `-12..12` 整数半音。请求不能携带路径、CFG、歌词或局部修正时间点；混合两种引擎的请求文件会失败。未完成作业保留该 marker，供启动恢复和异常清理识别 WSL 所有权。

Windows 不移调 SVC 的内容波形。推理按完整源时长准备 24 kHz mono，以 RMVPE 提取原调 F0，再在 F0 上执行用户选定的移调；参考 F0 不变。歌唱参考、SVC 权重和离线 Whisper 编码器分别按固定摘要验证，位置为私有 WSL Runtime 的 `prompts/singing-v1/` 与 `models/SoulX-SVC/`。这些资产不从评估缓存动态发现、不在线下载、不自动更新。Whisper 仅提供源音频内容特征，不用 ASR 重写歌词。

通用音高修正规则只接受短暂、非八度的识别错误，需要周期性、多阶谐波、独立 pYIN 和两侧未修改的可靠锚点共同支持。弱声、噪声、较长无音高区、竞争声部、正常大跳与八度歧义保守保留。低置信桥接受连续性和强声学证据约束，不能递归把修正帧当作新的锚点。算法不引用任何歌曲名或时间点，不声称能拆开重叠歌手。

分段在约 18 秒附近寻找低能量切点，并保留两侧上下文；所有源样本各属于一个连续核心区间。零 F0 帧也进入推理，不丢弃气声、辅音或静音段。原始 vocoder 长度必须满足帧合同，禁止短段尾补零；只允许不到一帧的真实生成尾部裁剪。输出为精确 `round(源时长 × 24000)` 的 mono PCM24，Windows 在混音前再次检查；没有跨段挪动、逐段归一化或原唱辅音回填。生成后的整首只做一次必要的峰值衰减，避免再逐段拉平响度；模型自身仍可能改变音色和强弱，需试听。相邻已生成上下文在原切点 ±50 ms 内按波形值、斜率和近零幅度选择最终切点，只改变相同绝对样本的归属，不移位、不混合相消。

2026-10-13 用户确认 RPReplay 11 秒焦点片段第 5–6 秒的局部 F0 修正版本电音消失，并偏好红豆《Elysia》参考的另一组参数。通用规则随后在九段 4450 帧中只修改 RP 的六帧，在完整 RP 的 12415 帧中仍只修改对应六帧；其余帧逐字节保持原样。12 个声学反例未修改，四个合成正例中通过三个，110 Hz 谐波歧义正例被保守拒绝。248.293875 秒 RP 的原始与自动修正整曲均已完成 14 段推理，源样本完整覆盖，没有尾补零。这些是定位和结构证据，尚不能替代整曲人耳对电音、口吃、压嗓和角色音色的验收。

正式生产模块随后完成红豆（《Elysia》、CFG 3、主唱 +12）204.288 秒与 TruE（《妖精小姐的魔法邀约》、CFG 1、原调）187.536 秒全曲，两首都保持原生 F0，分别采用 12 / 11 个上下文核心。一次受限全局衰减后，最大接缝样本跳变分别为约 `4.32e-5` / `6.26e-5`，源时长对应样本精确，没有尾补零。试听混音保留伴奏原调；这对红豆是明确的主唱升八度组合。

真实 Main → Windows → WSL → 混音 → 导出验证完成 RPReplay 整曲：24 kHz mono PCM24 干声 5,959,053 帧、44.1 kHz stereo PCM16 混音 10,949,760 帧，有限且无满幅样本；完成后 Linux 私有 UUID 已清除，Main shutdown 后 Windows Job 也清除。第二次真实任务在取得 inference stage 的 PID/session/start-tick Lease 后取消，Windows 进程树与私有 WSL 作业都已结束，结果不可播放。Stem 预备的 mono→stereo→mono 会产生约 -3.01 dB 固定增益差，所以离线整曲的六帧 F0 结论不能未经实际输入复核直接套到 Main 输出。

用户收到三首整曲与重点片段的试听页后反馈“还行吧，听得过去”，因此保留此版作为当前可用版本；这不是零缺陷或所有语言的音质保证。20 ms 源活跃窗口、单次中位电平对齐后的 >=20 dB 相对缺口，红豆为 0.12 秒（最长 20 ms），TruE 为 1.66 秒（最长 100 ms，129.30–129.40 秒）；原先焦点的红豆 40–50 秒与 TruE 151–159 秒均为零。源定义的 1 秒轻声/强声分位组对比，红豆从 8.87 dB 变为 3.43 dB，TruE 从 12.75 dB 变为 10.73 dB。这些能量/动态信号保留供后续具体片段复查，不能直接等同掉字、口吃或压嗓，也不能被时长、峰值和无数字静音检查掩盖。对照保存在本机 `quality-20261013/full-listen.html`，程序没有据此自动加补偿或声称所有语言无伪影。

## 10. 权利边界

用户必须有权处理所选歌曲、原唱录音、歌词、伴奏、Elysia Prompt 和生成结果。在线取得歌词不代表取得复制、改编或发布授权；本机推理也不会自动授予歌曲、角色、配音、歌声模型或训练数据权利。

SoulX-Singer、SoulX preprocessing models、FunASR、ROSVOT、RMVPE、OpenCC、RVC v2 Source、HuBERT、FAISS Index、Demucs Source、预训练模型、Elysia Prompt／Checkpoint、私有训练数据、示例歌曲及示例翻唱都必须按各自条款与来源记录处理。当前私有 Elysia 歌声资产不进入 Repository 或安装包；详见根目录 [MODEL_LICENSE.md](../MODEL_LICENSE.md)。
