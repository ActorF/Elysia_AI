# 本机爱莉希雅翻唱 / Local Elysia Song Cover

> Status / 状态：Composer 现在默认选择歌词驱动的 Mandarin SoulX-Singer SVS；用户明确选择的 **Legacy voice conversion** 界面/协议名称保持不变，实际转换引擎已替换为本机 RVC v2。两条路径都只面向具备私有本机 Runtime 的源码开发环境；歌曲、歌词、Stem、模型、Index、Runtime 与生成音频都不进入 Git、GitHub Release 或安装包。这是一项路线图外的实验性本机能力，不代表 Stage 12 联网总策略或 Stage 14 安装/发布已经完成。

## 1. 功能边界与默认选择

Composer 中的音符按钮位于 **Dictate** 麦克风左侧。设置窗口会先让用户明确选择 Singing method：

- **Lyrics-driven singing**：默认、推荐路径。Main 在线查找可信的同步歌词，再由本机 Windows + WSL Runtime 依照歌词和检测到的音符生成爱莉希雅主唱；
- **Legacy voice conversion**：显式回退。这是保留的 UI 与 Wire 兼容名称；本机 RVC v2 复制输入 vocal 的旋律、发音内容与时序，不访问在线歌词；详细边界见第 8 节。

两种方法都接受一首已混合歌曲，或一对从同一时间点开始的 vocal/accompaniment Stem；都只允许原调或升／降一至两个半音。完整歌曲在 Windows 侧使用固定 Demucs `htdemucs` 分离，Stem 模式跳过分离。

歌词驱动并不等于任意歌曲都可自动重唱。当前 SVS 只接受可安全规范化为简体中文汉字的 Mandarin 歌词；标点、空格和装饰符号不占音符，拉丁字母、数字及其他非汉字歌词会 Fail Closed。它还必须取得带时间戳的同步 LRC，并让该 LRC 与源 vocal 检测出的音符以严格门槛唯一对齐。

## 2. 默认 Lyrics-driven SoulX SVS 链路

默认路径已经由 Renderer、Electron Main、Windows Worker、WSL Bridge 和私有 SoulX Runtime 串接完成：

1. **Main metadata + bounded override**：Main 使用固定本机 `ffprobe.exe` 读取第一条音频流的时长，并从 Format／Audio stream tag 中取得 `title` 与 `artist`（必要时接受 `album_artist`）。用户可在确认窗口同时填写有界的 Song title 与 Artist 来覆盖查词身份；手动值只用于在线匹配，歌曲时长仍强制来自 FFprobe。原生路径不会交给 Renderer。
2. **LRCLIB exact → fuzzy verified lookup**：Main 先请求固定 HTTPS Origin `https://lrclib.net/api/get`，带 title、artist 与四舍五入后的 duration；精确请求没有可信结果时，再请求 `/api/search`。任何被采用的精确或模糊结果都必须同时通过综合置信度 `>= 0.82`、标题相似度 `>= 0.70`、歌手相似度 `>= 0.65` 和时长差 `<= 20 s`；Search response 最多接受 20 条结果。Redirect 不被接受，Rate limit／Service unavailable 最多按受限 `Retry-After` 重试一次。
3. **Private synchronized lyrics**：只有通过上述门槛、且未触发双向文件名查询冲突的结果，才会在 Main 已创建的私有 Windows Job Root 中以排他方式写入固定 UTF-8 文件。`lyrics.lrc` 是启动 SVS 的硬性要求；`lyrics.txt` 若存在，会在 WSL 内与 LRC 的汉字序列做一致性复核；`lyrics-manifest.json` 只保存 `source=lrclib`、Record ID、置信度和同步歌词存在标记，不保存歌词正文或本机路径。
4. **Windows audio preparation**：完整歌曲先规范化为 44.1 kHz PCM，再用 CUDA Demucs 4.0.1 `htdemucs` two-stems 拆出 vocal 与 accompaniment；Stem 模式把两份输入从共同的零时间点解码并按较长者补齐。非零调性会在进入 SVS 前以固定、等长度 FFmpeg 路径同时移动 target vocal 与 accompaniment；原调只把 vocal 准备成精确长度的 mono PCM。
5. **WSL private staging**：Windows Worker 使用绝对 `%SystemRoot%\System32\wsl.exe`，不经过 Shell；`/usr/bin/wslpath` 把已验证的 Windows Job Root 转成 WSL mount path，并由固定 `/usr/bin/python3 -I` 查询当前 WSL UID 的 POSIX Home，再拼接固定私有 Runtime suffix。Bridge 只把 `target_vocal.wav`、`lyrics.lrc` 和可选 `lyrics.txt` 复制到当前 Linux 用户拥有的 mode-`0700` UUID Job，文件以 mode `0600` 创建；Manifest 不进入 Linux Runtime。
6. **FunASR hotword + strict lyric/note alignment**：固定 SoulX preprocessing 以 `language=Mandarin`、CUDA、MIDI transcription 运行。同步 LRC 先转简体汉字、去重并形成有界的 FunASR `hotword`，只用于提高 ASR 偏置，不能授权错误歌词。Preprocess 产生的 segment／note metadata 随后按 LRC 时间区间分配歌词，并以单调 Dynamic Programming 对齐 ASR onset 与权威汉字；默认要求逐段 exact-match ratio `>= 0.60`、normalized cost `<= 0.45`，相同最优路径、歌词容量不足、voiced segment 缺歌词、Plain/LRC 不一致或时间归属不唯一都会失败。音高、时长、F0 与 segment 时间保持不变，校正后的文字由 SoulX 官方 G2P 重新生成 phoneme。
7. **Fixed Elysia zero-shot SoulX**：推理固定使用本机 `SoulX-Singer/model.pt`、`soulxsinger.yaml`、phoneset、`control=score`、`auto_shift`、`pitch_shift=0`、CUDA FP16，以及固定 `elysia-v1/prompt.wav` + `prompt.json`；Renderer 不能替换模型、Prompt、语言、Device 或控制模式。生成 vocal 与 target vocal 的时长差超过 1 秒会被拒绝。
8. **Reviewed FFmpeg mix**：Bridge 先把生成 vocal 暂存回 mounted Job，删除整个私有 Linux Job 后才原子发布 `generated_vocal.wav`。Windows Worker 再使用 -18 dB 严格无声辅音层、60 Hz high-pass、0.5 dB Presence、线性 +1.3 dB vocal 补偿、2.2–5.2 kHz vocal-keyed accompaniment ducking 和 -1 dBFS limiter，输出 WAV 与 MP3。RVC 路径不会混回这份原唱辅音层；它使用固定 Protect 保留无声内容，避免再引入分离伪影或电音。完成事件只在歌词、WSL 输出和其他 Scratch 均已清理后发送。

完整默认数据流如下：

```text
React 音符按钮（默认 lyrics-svs）
  └─ Main 原生文件选择器
     ├─ 固定 FFprobe：audio duration + title/artist tags
     ├─ LRCLIB HTTPS：exact lookup → bounded fuzzy search → confidence checks
     └─ private Windows Job：lyrics.lrc + optional lyrics.txt + manifest
        └─ song_svs_worker.py
           ├─ Windows FFmpeg：规范化／可选整曲移调
           ├─ Windows Demucs：完整歌曲才分离 vocals / accompaniment
           ├─ source vocal：严格无声辅音层
           └─ wsl.exe → song_svs_wsl_bridge.py
              └─ private Linux UUID Job
                 ├─ SoulX preprocess：FunASR lyric hotword + MIDI/note metadata
                 ├─ strict LRC ↔ segment/note alignment + official G2P
                 └─ fixed Elysia prompt + SoulX score-control zero-shot inference
              └─ stage output → delete Linux Job → publish generated vocal
           └─ reviewed FFmpeg mix → WAV + 320 kbps MP3
```

## 3. 元数据、歌词与真实限制

### 3.1 哪些文件名可用于歌词匹配

确认窗口提供可选的 **Song title** 与 **Artist**。两项必须同时留空或同时填写；每项去除首尾空白后最多 256 个 Unicode code point，控制字符、孤立 surrogate、缺一项或额外 Contract 字段都会被 IPC Parser 拒绝。手动输入时，它优先于错误或缺失的媒体 Tag／文件名，且只送入 Main 的 LRCLIB 查询，不会进入 Worker argv、日志、返回的 `SongCoverState` 或歌词文件；用于匹配的 duration 仍只能由 FFprobe 从所选音频取得。显式选择 **Legacy voice conversion** 时这两项隐藏且不会被传递。

两项留空时，优先使用文件内的可信 title + artist tag。Stem 模式只探测第一步选中的 vocal stem，不从 accompaniment 猜歌名。只有一个 Tag 时，文件名还必须是恰好两段的 `A - B`、`A – B` 或 `A — B`，且该 Tag 能明确对应其中一段。

完全无 Tag 时，Main 只会对清晰的两段式文件名尝试 `Artist - Title` 与 `Title - Artist` 两种顺序；它也保守识别常见歌词视频导出的精确 `Artist_Title【动态歌词_Lyrics_Video】`／`Artist_Title【動態歌詞_Lyrics_Video】` 形状，但不会把普通下划线任意解释成歌手与歌名。若两种顺序都得到可信 Record，它们必须是同一个 ID，只有一边得到可信结果时也可接受。若两个顺序得到不同的可信 Record，就要求更明确的 metadata，而不是任意选一个。`song.wav`、`track.wav`、`audio.wav`、`vocals.wav`、`instrumental.wav`、`recording.wav`、`output.wav`、Screen recording、日期、UUID 等通用／生成式无标签名称不会用于猜歌。此时应在确认窗口同时填写真实歌名和歌手；这也是仅名为 `vocals.wav` 的 Stem 的正常用法。

这里没有 Audio fingerprinting、声纹识别或声学歌曲识别；Main 只使用 FFprobe metadata、受限文件名证据、时长与 LRCLIB 返回字段。改名不能保证匹配，低置信或多义结果仍会失败。

### 3.2 同步歌词是硬要求

LRCLIB 只有 Plain lyrics 而没有 `syncedLyrics` 时，Main 不会启动 SVS。LRC 必须包含有效、排序后落在音频范围内的时间戳；未定时正文、冲突 offset、同一时间戳的冲突文本、无法唯一归属到 SoulX segment 的句子，都会 Fail Closed。当前 UI 可以覆盖查词所需的歌名与歌手，但不能手动粘贴、编辑或校正歌词内容。

当前歌词校正只覆盖 Mandarin Han：繁体通过固定、已验证的 OpenCC `t2s` 资产转为简体；标点和空白可忽略，但英文、日文假名、数字、罗马音、混合语言等不能被静默丢弃。FunASR 热词只是识别提示，最终 LRC/note 对齐才是准入边界。

### 3.3 网络与离线边界

网络只用于 Main 发起的 LRCLIB 查询。用户选中 **Lyrics-driven singing** 并确认 **Choose audio** 后，Main 会向固定 `https://lrclib.net` 发送从手动覆盖、可信 Tag 或文件名解析出的歌名、歌手，以及 FFprobe 取得并四舍五入的音频时长；不会上传歌曲、人声 Stem、伴奏、模型、Prompt、本机路径或生成结果。查询使用无凭据 HTTPS GET，因此这些匹配字段会成为该请求 URL 的 Query；用户不希望发送它们时必须改选明确的离线 **Legacy voice conversion**。

没有网络、LRCLIB 不可用或未找到可信同步歌词时，默认路径不会回退到猜测，也不会自动切换 Legacy 路径。歌词准备完成后，Windows／WSL 的 Demucs、FunASR、ROSVOT、RMVPE、OpenCC 与 SoulX 模型均从本机读取；子进程强制 Hugging Face、Datasets 和 Transformers Offline，移除 Proxy，并丢弃 Vendor stdout/stderr。当前项目尚未完成 Stage 12 的全局网络总开关、代理和统一查询记录；Song Cover 只依靠该对话框中的显式方法选择与确认建立一次任务授权，不能被描述成整个应用的联网策略已经完成。

选择 **Legacy voice conversion** 才会进入无在线歌词的显式回退路径。

## 4. 输入、输出、安全与生命周期

| 项目 | Contract |
| --- | --- |
| 输入格式 | `.aac`, `.flac`, `.m4a`, `.mp3`, `.ogg`, `.opus`, `.wav` |
| 输入大小 | `1 B`–`1 GiB` |
| 输入时长 | `1`–`720` 秒 |
| 输入方式 | 一首完整歌曲；或一对从同一时间点开始、彼此对齐的人声与伴奏 Stem |
| 默认 Engine | UI 默认 `lyrics-svs`；用户可显式选择 `legacy-svc` |
| 手动查词身份 | `lyrics-svs` 可同时填写 Song title + Artist；仅覆盖 LRCLIB query，duration 始终来自 FFprobe |
| SVS 歌词 | 通过匹配门槛且无双向文件名冲突的 LRCLIB 同步 LRC；目前 Mandarin Han-only |
| 整曲调性 | `-2`、`-1`、`0`、`+1`、`+2` 半音；SVS 在 Preprocess 前等时移动 vocal + accompaniment，Legacy RVC 直接移动检测到的 vocal F0 并等时移动 accompaniment |
| 分离模型 | Demucs 4.0.1 `htdemucs`, CUDA, two-stems vocals, `segment=7`, `shifts=1`, `overlap=0.5`, `jobs=1`；Stem 模式跳过 |
| SVS 推理 | Mandarin SoulX-Singer，固定 Elysia zero-shot Prompt、score control、auto-shift、CUDA FP16 |
| 清晰度处理 | 60 Hz high-pass、0.5 dB Presence、线性 +1.3 dB 人声补偿、2.2–5.2 kHz vocal-keyed accompaniment ducking、-1 dBFS limiter；无 Vocal Compressor/Makeup。-18 dB 无声辅音层仅用于 Lyrics-driven SVS；Legacy RVC 使用固定 Protect，不混回原唱辅音 |
| 导出 | 44.1 kHz stereo 16-bit PCM WAV |
| 预览 | 44.1 kHz stereo 320 kbps MP3 |
| 并发 | 全局一次一个任务 |

React 只能看到安全 Base Name、UUID、Engine、固定阶段、0–100 进度、是否存在输出和净化错误。源路径、输出路径、歌词正文、模型路径、PID、stderr 和音频字节不进入公开 `DesktopApi`。歌词正文也不进入 Native argv、Worker stdout 或日志。

SVS Worker 启动时要求 Windows Job Root 是完全扁平的 allowlist：必须只有 `lyrics.lrc`、`lyrics-manifest.json` 与可选 `lyrics.txt`，任何额外文件、目录或链接都会失败。成功清理后再次检查该目录必须恰好只剩 `elysia-cover.wav` 与 `elysia-cover.mp3`；这也会把处理期间的晚到注入变成失败，而不是发布部分清理的结果。

Main 只允许一个启动中、转换中或收尾中的任务。取消必须匹配准确 UUID。Windows 取消使用绝对 `%SystemRoot%\System32\taskkill.exe /pid <PID> /t /f`，检查退出码并等待 Worker 关闭；确认原进程树结束后，歌词驱动任务还会启动独立的 cleanup-only Worker。该 Worker 只把同一个规范 UUID 交给固定 WSL Bridge；Bridge 只重建私有 `jobs/<UUID>` 这一直接子目录并验证 owner、mode、类型和无链接边界，然后删除这一个目录。cleanup-only 也有固定成功帧、输出上限和超时；自身超时时必须确认整个 cleanup 进程树停止，才允许以后重试。无法确认任一进程树结束时，不会删除仍可能被 FFmpeg／WSL 使用的目录，也不会释放任务所有权。此时退出请求也会被 Main Shutdown Gate 拦住；清理仍在进行时重复点击退出不能绕过该 Gate。

两条 Worker lifecycle 都是严格、不可跳阶段的固定序列：

```text
lyrics-svs: validating → separating → transcribing → aligning
            → synthesizing → mixing → complete

legacy-svc: validating → separating → converting → mixing → complete
```

Stem 模式仍发送 `separating`，但不会调用 Demucs。Worker 事件只接受固定 Prefix、字段、进度值和对应 Engine 的固定阶段顺序；单行最多 64 KiB、整个 Native 任务最多 32 MiB stdout，默认两小时总时限。WSL Bridge 还有独立的 2 KiB line、256 KiB total、128 event 协议界限；Vendor 文本不会被透传。

Main 会检查最终 WAV 是 44.1 kHz stereo 16-bit PCM、MP3 有合法帧头，并固定两个输出的文件身份；播放和导出前再次复核，路径被本机其他进程替换时会 Fail Closed。播放只接受最多 64 MiB、带 MP3 Header、解码时长不超过 12 分钟的内容；指定输出设备不可用时失败，不暗中改用其他扬声器。Worker 关闭后的扬声器偏好读取最多等待 5 秒；结束、失败或取消都会撤销 Blob URL。

临时结果位于创建任务时受管数据根的 `audio/song-covers/<job-id>/`。创建下一首会先删除上一首；**Settings → Data & storage** 清理临时音频或应用退出也会删除未导出结果。用户通过原生保存对话框导出的 WAV 位于用户选择的位置，不再属于临时结果。

启动时的崩溃遗留清理是受管 Lease：Main 通过未完成 Windows Job 中仍存在的 `lyrics-manifest.json` 识别歌词驱动事务，先按同一个 UUID 尝试 WSL cleanup-only，再删除 Windows Job Root；它不会扫描或递归删除整个 WSL jobs 根目录。Lease 仍在运行或失败待重试时会阻止受管数据根移动。取消、Worker／Bridge 失败、输出身份验证失败或超时后，如果私有 Linux Job、歌词、人声、Stem 或 Windows Job 目录无法删除，Main 会保留该精确 UUID 和目录的清理所有权并显示固定错误；重试成功前不允许创建下一首或移动数据根。完成但尚未清理的结果固定在创建它的数据根，移动前应先导出需要保留的 WAV。

任务完成且 Dictate／Voice Call 未占用麦克风时会自动播放。Main 会原子保留 Voice 会话并使已开始但仍在读取文件或扬声器设置的 Song Cover 播放失效，避免麦克风与歌曲在竞态中同时启动。Composer 面板提供运行中 Progress／Cancel，完成后 Play／Stop／Export WAV，以及不包含本机路径或 Native 诊断的固定错误。

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
├── prompts/elysia-v1/
│   ├── prompt.wav
│   └── prompt.json
└── jobs/                           # mode-0700 transient UUID jobs
```

Windows Worker 不信任 Windows `HOME`，也不硬编码 Linux username。它以固定 `/usr/bin/python3 -I` 和固定 Script 从 POSIX `pwd` 解析当前 WSL UID 的 Home，只允许在严格验证后的绝对路径后追加 `.local/share/elysia-ai/soulx/prep-env/bin/python`。Runtime 继续按同一 Linux UID 验证 `$HOME/.local/share/elysia-ai/soulx`、Job owner、mode 与无链接路径。

高信任资产按固定长度与 SHA-256 验证，包括 Linux CPython、固定 FFmpeg、SoulX model、RMVPE／ROSVOT、Mandarin FunASR model/config/tokens/seg dictionary、Elysia Prompt 以及 OpenCC Python/native/config/dictionaries。SoulX Source 还必须是无修改、无 Untracked file 的 revision `81aeb3ae772c70093c3de74dc23c92d983801ae4`，并且 `pretrained_models` symlink 必须只指向固定私有 Model 目录。

该验证不是完整 Linux 环境供应链证明。当前 `prep-env`／`infer-env` 中未单独列入高信任清单的 Python 包、Torch/CUDA Native Extension 仍以“同一操作系统用户准备并信任这个私有环境”为前提。固定解释器、模型、关键配置和 Source Revision 可以发现已知核心资产替换，但不能证明每个可导入依赖都来自可重建、签名或完整树摘要认证的环境。

应用不会自动下载、安装、复制或更新 Windows／WSL Runtime 和模型。资产缺失、大小或 Hash 变化、SoulX checkout 不干净、链接目标变化或固定目录不安全都会 Fail Closed。

## 6. 运行与打包状态

- 完整歌曲的 Demucs 与 Legacy RVC 在 Windows CUDA Runtime 中执行；SoulX preprocessing／inference 在 WSL CUDA Runtime 中执行。没有兼容 NVIDIA GPU、WSL GPU integration、驱动、足够显存或上述当前私有布局时会失败，不会把长曲静默切到 CPU 或 DirectML。
- Worker 没有指定 `wsl.exe --distribution`，因此 Windows 当前默认 WSL Distribution 必须提供 `/usr/bin/python3`，且当前 UID 的 Home 下必须存在固定 SoulX Runtime suffix；它不会搜索其他 Distribution 或任意模型路径。
- Main 给 Windows Worker 的环境采用 Allowlist；WSL child 使用独立的最小、Offline、Proxy-free 环境。LRCLIB 是唯一预期的在线步骤，模型推理不会隐式下载缺失资产。
- WSL preprocessing 与 inference 各自在新的 Linux Session 中运行，并在 UUID Job 下原子记录 PID、PGID、Session ID、Kernel start ticks 与随机 Stage ID。取消/超时/cleanup-only 会先写不可逆取消标记，再以 `flock` 串行化启动竞态，只对该准确 Lease 的同 UID 成员执行 TERM→KILL 并确认全部退出；不会按进程名枚举或影响其他 Job。私有 Runtime 共用 90 分钟总预算（preprocess 最多 35 分钟、inference 最多 55 分钟），为 Electron 的 2 小时 Job 上限保留原生分离、混音与清理时间；WSL cleanup 内层 30 秒仍小于 Main 的 60 秒外层清理期限。
- 当前 Electron Builder 只打包 `dist/`, `dist-electron/` 与 `package.json`；Python Workers、Windows `models/cache/rvc-v2-40k/`、私有 `models/weights/rvc/elysia-v2-40k/` 与 WSL Runtime 均不在安装包中。因此默认 SVS 与 Legacy fallback 目前都只面向已手动准备 Runtime 的源码开发环境。
- Electron Main 提供闭集 Readiness：安装包固定报告 Runtime 未包含；源码环境仅以 `lstat` 检查必要 Worker/Core 文件是普通非链接文件，不联网、不运行 Python/WSL/模型。Composer 会禁用“新建翻唱”并显示固定恢复提示，但不会阻止播放或导出已经完成的结果；Main 在文件选择器前后仍会权威复核，Renderer Readiness 不能授权启动。缺少本机 RVC 资产时不会自动下载、切回旧引擎或启用未验证的 CPU 替代路径。
- Repository 内的 `models/cache/` 与 `models/weights/` 被 `.gitignore` 排除；WSL Runtime 位于 Repository 外。Ignore rule 本身不是发布授权，实际 Release 仍必须检查打包文件清单。
- 默认 SVS 尚不支持用户提供本机 LRC、手动修改歌词正文、Audio fingerprinting、非 Mandarin 或混合语言歌词、无同步时间戳的歌词，以及自动从失败的 SVS 降级到 Legacy RVC。歌名与歌手则可在确认窗口成对覆盖。
- 当前只使用一份经过人工复核、含准确文本和音符元数据的歌唱 Prompt；不会把 `slicer` 中缺少逐条转写、音高和时序标注的全部日常语音盲目当作 SoulX Prompt。多 Prompt 选择、覆盖更多音域与情绪，或训练专用歌声模型仍属于后续音质工作，不能从当前单 Prompt Smoke 推断已经完成。
- 2026-10-08 在当前 Windows + 默认 `Ubuntu-24.04` WSL + CUDA 私有 Runtime 上完成了一次 6.71 秒受控 Lyrics-SVS 贯通 Smoke：生成结果是 44.1 kHz、Stereo、16-bit PCM WAV，并同时生成 44.1 kHz、Stereo、320 kbps MP3；成功 Job 只保留这两个输出。该证据验证了 Windows Worker → WSL Bridge → SoulX → Windows Mix 的当前机器互操作，不代表完整歌曲、多音区、快速咬字、取消/超时或人耳音质矩阵已经通过。
- 2026-10-09 在当前 Windows CUDA 私有 Runtime 上以一对 248.294 秒 Stem 完成 Legacy RVC Worker 贯通验收：RVC 子进程生成完整 40 kHz mono PCM16 人声，最终只保留 44.1 kHz Stereo PCM16 WAV 与 320 kbps MP3；WAV 峰值约 `-3.44 dBFS`，没有时长截断或满幅削波。关闭上游按 Shape 缓存的 CUDA Graph 后，实测显存从失败路径接近 12 GiB 降到约 2 GiB。该验收证明当前机器的完整 Worker/混音/清理边界可运行，不代表所有歌曲的人耳音色、分离质量、咬字或音域都已通过。

## 7. CMD 测试

以下命令覆盖 Legacy RVC Adapter/Worker，以及歌词对齐、WSL Runtime、Bridge 和 Windows SVS Worker pytest 文件：

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe -m pytest tests\test_song_rvc_runtime.py tests\test_smoke_song_cover.py tests\test_song_cover_worker.py tests\test_song_lyrics_alignment.py tests\test_song_svs_runtime.py tests\test_song_svs_wsl_bridge.py tests\test_song_svs_worker.py -q
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

检查预检 JSON 后，只有显式增加 `--run-private-runtime` 才会创建新的临时 UUID Job 并调用生产 `song_svs_worker.py`。Smoke 使用固定四字段本机 manifest，解析完整封闭阶段协议，验证 44.1 kHz Stereo PCM16 WAV 与 MP3 的 Hash、编码、大小和时长，并再次执行 Worker 的 cleanup-only 协议确认 WSL 私有 Job 已清除。默认在验证后删除本机 Job；确需人工试听时可再增加 `--keep-output`，结果只保留在 `%TEMP%\elysia-song-cover-smoke\<stdout job_id>`。stdout 不包含歌词、输入路径、Runtime 路径或底层诊断。私人音频和歌词不得复制到 Repository、测试 Fixture 或文档目录。

手工验证时，在打开的 Elysia 中点击 Dictate 左侧音符。Dialog 应默认选中 **Lyrics-driven singing**；当前高质量模式只支持普通话／中文同步歌词。可信 Tag 或清晰两段式文件名可让 Song title + Artist 同时留空；ScreenRecording、`vocals.wav` 等通用名称应同时填写两项。只填一项时 Dialog 必须停留并提示，不得打开原生文件选择器。选择完整歌曲或 Stem、选择调性，再点击 **Choose audio**。默认 SVS 应依次显示 `validating → separating → transcribing → aligning → synthesizing → mixing → ready/playing`。若无网络、无可接受的 LRCLIB match、无同步 LRC、含非 Han 内容或无法严格对齐，应安全失败，而不是生成猜测歌词。

若显式选择 **Legacy voice conversion**，应依次显示 `validating → separating → converting → mixing → ready/playing`，且不访问 LRCLIB。

## 8. Legacy RVC v2 显式回退

`Legacy voice conversion` 不再是界面默认值。该名称只为保持 Renderer、IPC 和已有状态的兼容；实际推理引擎是 RVC v2，已移除的旧转换引擎不再属于生产路径。它适用于无法取得同步歌词、非 Mandarin 歌曲，或用户明确希望保留源 vocal 发音内容的情况；用户必须在 Singing method 中主动选择它。

该路径不会读取或理解歌词。旋律、节拍、发音内容与演唱时序都来自输入 vocal；只选纯伴奏不会凭空生成歌词和演唱。处理步骤如下：

1. FFmpeg 把输入规范化为 44.1 kHz PCM；完整歌曲模式使用 Demucs `htdemucs` 分离 vocal 与 accompaniment，Stem 模式直接使用从同一时间点开始的已对齐输入；
2. 短命 RVC Adapter 在独立子进程中固定 speaker `0`、RVC v2 40 kHz model family、RMVPE F0、HuBERT、Index Rate `0.00`、Protect `0.33` 与 RMS Mix Rate `0.25`。它在 Vendor Import 前固定 `RVC_CUDA_GRAPH=0`：上游会按静音点形成不同长度片段，而一次性整曲任务缓存每个 Shape 的 CUDA Graph 没有 Replay 收益，只会持续占用显存。当前选定的 e50 声学 Profile 不把 FAISS 邻居混入输出；`model.index` 仍作为该私有 Profile 的固定、身份验证资产传入并校验，不能被替换成任意 Index。Renderer 只能提交 `-2..+2` 半音，不能改变上游 RVC 命令面、模型、Index、F0 方法或混合参数；
3. Adapter 禁用 Socket 与 Hugging Face 在线访问，只从显式 `models/cache/rvc-v2-40k/` 载入已验证的 Source/HuBERT/RMVPE，并从 `models/weights/rvc/elysia-v2-40k/` 载入私有 Checkpoint/Index。非 CUDA Device、路径跳转、不匹配的 Model Metadata、超范围／全零／长时间持续满幅 PCM 或既存输出都会 Fail Closed；
4. RVC 产生 40 kHz mono 人声。Worker 将它重采样到 44.1 kHz，再以 `apad + atrim` 精确对齐原目标 Sample Count；这里不使用时长拉伸，避免为了追平长度而制造新的节拍或音高伪影；
5. RVC Protect 已负责无声内容保留，所以 Worker 不会再把原唱的辅音高频层混回转换结果。FFmpeg 对转换人声做 60 Hz High-pass、0.5 dB Presence 与线性 +1.3 dB 补偿，不使用 Vocal Compressor/Makeup；随后用人声驱动的窄频伴奏 Ducking 腾出空间，最后以 -1 dBFS 限幅；
6. 保存 44.1 kHz stereo 16-bit PCM WAV 用于导出，并生成 320 kbps MP3 用于应用内播放。

**Original key (`0`)** 最贴近原唱音高。任何非零值都会在 RVC 内有意移动检测到的 vocal F0，并以 `asetrate → aresample → atempo` 等时移动伴奏；它是音域取舍，不是更精确的原唱复刻。若已有干净 Stem，优先使用 Stem 模式以避免 Demucs 分离伪影。

旧引擎曾在共享 Runtime 下创建 `raw/` 与 `results/` Scratch；当前 RVC 流程不再读写这些目录。转换中间 WAV 只存在于 Main 拥有的 UUID Job Root，发布前必须是新文件，完成/取消/失败/退出都沿现有受管 Job 清理路径收敛。应用不会扫描或删除 RVC Runtime 内的未知用户文件。

## 9. 权利边界

用户必须有权处理所选歌曲、原唱录音、歌词、伴奏、Elysia Prompt 和生成结果。在线取得歌词不代表取得复制、改编或发布授权；本机推理也不会自动授予歌曲、角色、配音、歌声模型或训练数据权利。

SoulX-Singer、SoulX preprocessing models、FunASR、ROSVOT、RMVPE、OpenCC、RVC v2 Source、HuBERT、FAISS Index、Demucs Source、预训练模型、Elysia Prompt／Checkpoint、私有训练数据、示例歌曲及示例翻唱都必须按各自条款与来源记录处理。当前私有 Elysia 歌声资产不进入 Repository 或安装包；详见根目录 [MODEL_LICENSE.md](../MODEL_LICENSE.md)。
