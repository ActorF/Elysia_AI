# 本机爱莉希雅翻唱

Composer 默认使用 **Voice conversion**（`source-svc`），由原唱内容和选定歌唱参考驱动 SoulX-Singer-SVC。另可明确选择歌词驱动或旧 RVC 引擎。三条路径都需要手动准备的私有 Runtime，**当前仅支持源码开发环境，安装包不含模型、参考音频和歌曲**。

## 使用

点击 **Dictate** 左侧音符，选择方法、音源和调性，再点击 **Choose audio**。

| 方法 | 引擎 | 输入依据 | 联网 |
| --- | --- | --- | --- |
| **Voice conversion**，默认 | `source-svc` | 原声内容与歌唱参考 | 不查询歌词 |
| **Lyrics-driven singing** | `lyrics-svs` | 同步普通话歌词与检测音符 | Main 查询 LRCLIB |
| **Legacy RVC** | `legacy-svc` | 源 vocal 的发音、音高与时序 | 不查询歌词 |

失败不会自动切换引擎。Voice conversion 的参考为《妖精小姐的魔法邀约》（固定 CFG 1，默认）或《Elysia》（固定 CFG 3），不会按文件名猜测。

三种方法均接受完整歌曲，或从同一时间点开始的 vocal/accompaniment 双 Stem。完整歌曲用固定 Demucs `htdemucs` 分离；Stem 跳过分离。Song key 支持原调及 `-12..+12` 整数半音，vocal 始终采用所选值。**Shift accompaniment with vocal** 默认开启；关闭后伴奏保持原调。主唱单独非八度移调可能与伴奏不协和，`±12` 保留音级关系但改变音区，较大移调仍可能增加伪影。

两种 voice conversion 每次新建任务提供 **Vocal arrangement**：

- **Entire vocal track**：默认转换整条人声，重叠歌声可能混在一起。
- **Lead only, keep other singers**：必须提供独立主唱轨和含其他歌手/伴奏、已移除主唱且时间对齐的 backing mix。不会从成品歌自动辨认歌手；backing mix 仍参与受限混音，不保证逐样本不变。

Demucs two-stems 只分人声与其余声音，不分主唱、和声或第二位歌手（[官方说明](https://github.com/facebookresearch/demucs/blob/main/README.md)）。当前无多声部自动分离/批处理；分别转换歌手需先取得各自独立声部。

### 输入、输出与播放

| 项目 | 限制 |
| --- | --- |
| 格式 | aac、flac、m4a、mp3、ogg、opus、wav |
| 大小 / 时长 | 1 B–1 GiB；1–720 秒 |
| 并发 / 总期限 | 全局一个任务；默认两小时 |
| 导出 | 44.1 kHz stereo PCM16 WAV |
| 预览 | 44.1 kHz stereo 320 kbps MP3；最多 64 MiB、12 分钟 |

卡片显示方法、调性和真实阶段，运行中可 Cancel，完成后可 Play/Stop/Save WAV。进度保留实际报告值，不按时间虚增；source-svc 按生成步骤/片段更新，旧引擎无上报时保留阶段值。减少动态效果设置会关闭填充动画。

Seek 支持播放前/播放中调整，Stop 保留位置，自然结束回到起点。叉号只收起卡片；音符变为 **Show Song Cover**，可重新打开，不取消任务、停止播放或删除输出。

任务完成且麦克风未被 Dictate/Voice Call 使用时自动播放。Voice 准入会使尚在读取文件/扬声器设置的歌曲播放失效，避免竞态。指定输出设备不可用时失败，不暗换扬声器；偏好读取最多等待五秒。

## 默认原声转换

`source-svc` 复用 Windows 验证/Demucs、WSL 私有作业、进程 lease、取消恢复和混音。Main 只写固定 `singing-request.json`：版本、`fairy`/`elysia` 参考 ID、整数半音；请求不能带路径、CFG、歌词或局部修正时间点，也不能混入另一引擎的请求文件。

Windows 不先移动内容波形。SVC 按完整源时长准备 24 kHz mono，RMVPE 取原调 F0，再按用户选择移调；参考 F0 不变。Whisper 只提供内容特征，不以 ASR 重写歌词。参考、权重与离线 Whisper 均按固定摘要验证，不从评估缓存发现、不在线下载或自动更新。

通用音高修正只处理短暂、非八度识别错误，需要周期性、多阶谐波、独立 pYIN 和两侧未修改锚点共同支持。弱声、噪声、长无音高区、竞争声部、正常大跳和八度歧义保守保留；修正帧不能递归成为新锚点。算法不引用歌名/时间点，不声称分离重叠歌手。

约每 18 秒寻找低能量切点并保留两侧上下文，每个源样本属于唯一连续核心。零 F0 帧仍推理，保留气声、辅音和静音。Vocoder 必须满足原始帧长度，禁止尾补零，只允许小于一帧的真实生成尾裁剪。输出精确为 `round(源时长 × 24000)` 个 mono PCM24 样本，Windows 混音前再次核对。

相邻上下文在原切点 ±50 ms 内按波形值、斜率与近零幅度选切点，只改变同一绝对样本的归属，不移位、交叉混合或逐段归一化。整首仅做一次必要的峰值衰减；模型仍可能改变音色、强弱，需试听。

## 可选歌词驱动

此路径只接受可规范为简体汉字的 Mandarin 歌词，必须从 LRCLIB 取得可信同步 LRC，再与检测音符唯一对齐。英文、假名、数字、罗马音或混合语言不会被静默丢弃。当前 UI 不接受本机 LRC、粘贴/编辑歌词或音频指纹识别。

### 歌名与歌词匹配

可同时填写 **Song title** 与 **Artist**，每项去空白后最多 256 Unicode code points；必须都填或都空，控制字符、surrogate 和额外字段拒绝。手动值优先于媒体标签，只用于 Main 查询，不进 worker argv、日志、公开状态或歌词文件；时长始终来自固定 FFprobe。双 Stem 只探测 vocal。

留空时先用 title/artist（必要时 album_artist）标签。只有一项标签时，文件名须是明确两段 `A - B` / `A – B` / `A — B` 且能与标签对应。无标签时可尝试两种顺序，也识别精确的 `Artist_Title【动态歌词_Lyrics_Video】` 及繁体形状，不任意拆普通下划线。双向查询若得到不同可信记录即拒绝。`vocals.wav`、`song.wav`、ScreenRecording、日期、UUID 等通用名不猜歌，应同时填写两项。

Main 先请求固定 `https://lrclib.net/api/get`，缺可信同步结果才用 `/api/search`（最多 20 条）。所有采用结果都须满足：

| 条件 | 门槛 |
| --- | --- |
| 综合置信度 | ≥ 0.82 |
| 标题 / 歌手相似度 | ≥ 0.70 / ≥ 0.65 |
| 时长差 | ≤ 20 秒 |

可信同步候选优于 plain-only；只有纯文本时准确报告缺同步歌词，不启动 SVS。拒绝重定向；限流/服务不可用最多按有界 Retry-After 重试一次。LRC 时间戳必须有效并在音频范围内；冲突 offset/同时间文本、未定时正文和多义对齐均拒绝。

联网需开启 [Internet](19-INTERNET-AND-SERVICES.md)，`lrclib.net` 已是内置服务域名。只发送歌名、歌手和四舍五入的时长，且它们会出现在 HTTPS GET query；不上传音频、路径、模型、参考或结果。没有网络或可信同步歌词就失败，不猜词、不切换引擎。后续推理均离线，子进程关闭代理及 Hugging Face/Datasets/Transformers 在线访问。

### 音频准备与对齐

```text
Main：FFprobe → LRCLIB → 私有歌词文件
  → Windows：44.1 kHz 规范化 / 完整歌曲 Demucs / 移调
  → WSL：私有 UUID job → SoulX preprocessing → 严格 LRC/note 对齐
  → 认证 Prompt bank → 有界候选 → 全局选择 → 24 kHz vocal
  → 删除 Linux job → 发布 vocal → Windows 混音 → WAV / MP3
```

`lyrics.lrc` 必需，可选 `lyrics.txt` 在 WSL 与 LRC 汉字序列复核。`lyrics-manifest.json` 只存 LRCLIB record ID、来源、置信度与同步标记，不含正文或路径。Windows 规范为 44.1 kHz PCM；Demucs 4.0.1 使用 CUDA `htdemucs`、two-stems vocals、`segment=7`、`shifts=1`、`overlap=0.5`、`jobs=1`。Stem 从共同零点解码并按较长者补齐，vocal/伴奏按所选策略等长移调。

固定 Mandarin preprocessing 使用 CUDA/MIDI transcription；OpenCC `t2s` 规范歌词并生成有界 FunASR hotword。热词只影响识别偏置，不授权错误歌词。

对齐用跨 segment 的全局单调动态规划：连续非空 LRC 行组成区块，明确空行时间戳结束区块，避免 segment 边界或间奏误拆短句。ASR 字符成本优先；完全同分才使用谱面 onset 与 LRC 真实行首，仍同分只允许最早的精确重复字符匹配，其他 canonical/group 位置必须一致，否则拒绝多解。

Type-2 onset 硬时间窗允许行前最多 100 ms、行首最多晚 600 ms，行内新字不能超过下一时间戳 100 ms；type-3 延音不受此窗限制。不从下一行或间奏平均猜逐字时间，明确空白区的 ad-lib 保留原 ASR metadata。每个区块及合成段都需 exact-match ratio ≥ 0.60、normalized onset cost ≤ 0.45；容量不足、Plain/LRC 不一致或有词区间缺 metadata 均失败。

音高、时长、F0 和 segment 时间保持不变，渲染前重验谱面身份。跨段延音只在新 slice 起点重标 onset，仍用已验证音节；校正字由官方 G2P 生成 phoneme。

### Prompt 与质量选择

固定 SoulX 模型、配置和 phoneset，以 `control=score`、`auto_shift=False`、`pitch_shift=0`、CUDA FP16 和 strict deterministic Torch 推理。私有 bank 共 59 条（48 corpus、10 emotion、1 anchor），先认证 canonical manifest 与全部 WAV/metadata。

每段按音域、语速与 phoneme 覆盖选最多两份不同内容参考。歌词/谱面 emotion proxy 只在 acoustic cost 同分时决胜，不是训练出的情绪识别器。先生成 C0；结构失败或 soft loss 高才申请同参考新 seed 的 C1、第二参考初始 seed 的 C2。结构失败优先占救援预算；每段最多三次、最多 12 个救援段，额外生成通常 ≤ 有声时长 25% 且绝对 ≤ 90 秒，单段歌曲也仅有界救援。Seed 来自内容摘要和候选序号，不依赖 UUID/路径。

模型帧长度与绝对 score 时间分别验证。只有固定两位小数 note duration 可使用每 note 5 ms 加两个 hop、且受 `max(3 hops, 绝对区间 1%)` 限制的量化容差；高精度 duration 和未量化 F0 仍须严格小于两 hop。逐项检查 note type/pitch、精确有限整数音高和 mel2note 覆盖，不能靠截断/集合比较漏掉错位。认证量化差才可在 5 ms 收零后确定性截断/补零。

结构硬门禁拒绝非有限、长度违约、持续同号近 rail 平台、真正 full-scale rail、不可恢复 headroom、DC 偏移和有效段近空音频；原始 float 超过 ±1 本身不等于 PCM 削波。只有明确 `<SP>` 是预期静音。F0/dropout/误有声/频谱代理只作 soft score，不能充当通用听感判官。

全局 DP 综合时长加权 loss、边界连续性和参考切换，并保留 Pareto 前缀，避免未来 headroom 条件推翻过早取舍。候选按绝对样本放置：空隙数字静音，段边缘在各自区间内 5 ms 收零，不交叉淡化、重叠或移动时间。长间隙免除不连续波形比较，但仍计参考切换。整首只允许一次最多 -6 dB 衰减至设计峰值 0.994、终检 ≤ 0.995，不提升或逐段归一化，不在该阶段使用 limiter。

每任务只加载一次模型/processor；GPU Prompt LRU 最多两条，第三条先逐出。目标 tensor 按当前段构建，候选用 float32，避免整曲缓存和候选爆炸。Renderer 不能改模型、语言、设备、seed、预算或门禁。

## 显式 Legacy RVC

Legacy 由源 vocal 决定发音和时序，不理解歌词，纯伴奏不会凭空生成演唱。固定 speaker 0、RVC v2 40 kHz、RMVPE、HuBERT、Index Rate 0.00、Protect 0.33、RMS Mix Rate 0.25。Index 虽不混入 FAISS 邻居，仍是需认证的固定 profile 资产，不能换任意文件。

Adapter 独立子进程、禁 Socket/在线模型访问，CUDA 不可用或资产/PCM 异常就失败。关闭 `RVC_CUDA_GRAPH`，因为各静音分段 shape 不同，一次任务缓存 graph 无 replay 收益却占显存。构模前固定 seed 114514、关闭 cuDNN benchmark、固定 deterministic cuDNN 并设 `CUBLAS_WORKSPACE_CONFIG=:4096:8`；不启 strict deterministic algorithms，因为固定 Torch 的 CUDA cumsum 不支持。

自有拼接按每段真实拥有的源样本区间取生成核心，保留原切点、输入和整曲 F0，修复 10 ms 切点与 20 ms HuBERT 网格的累计丢样。40 kHz 样本数必须先精确匹配源时长映射，再重采样；最终 apad/atrim 只处理换采样率尾取整，不能掩盖缺帧。适配仅绑定当前实例，不改 vendor 文件。

生产直接发布 segmented RVC PCM，已撤销事后 transition replacement；旧修复以安静高通源音替换主体造成短时能量塌陷和侧链 pumping，不能恢复上线当作电音解决方案。后续候选必须对 raw segmented 盲听，证明问题段/非问题段均不退化、无新增 >1 dB 短时 envelope notch，且不改变 sidechain key。整曲 one-shot 在目标 12 GiB GPU 曾 OOM，不能替代可执行分段方案。

当前模型只由 2182 份说话 slice 训练，没有干净目标歌唱数据；低/高音、停顿和摩擦音仍可能发哑、电音、掉字或偏音。Protect 在 Index Rate 0.00 时不是无声恢复保证。较大移调可能触及 50–1100 Hz 粗 F0 裁剪边缘，允许选择不等于听感保证。

## 混音、进程与恢复

共享混音为 60 Hz high-pass、0.5 dB presence、线性 +1.3 dB vocal 补偿、2.2–5.2 kHz 人声驱动伴奏 ducking 和 -1 dBFS limiter，无 vocal compressor/makeup。只有歌词 SVS 加 -18 dB 严格无声辅音层；SVC/RVC 不回填原唱辅音，避免原唱泄漏和分离伪影。

Worker 事件使用严格闭集字段、固定前缀和顺序：

```text
lyrics-svs: validating → separating → transcribing → aligning → synthesizing → mixing → complete
source-svc / legacy-svc: validating → separating → converting → mixing → complete
```

Stem 也报告 separating，但不调用 Demucs。stdout 单行最多 64 KiB、任务最多 32 MiB；WSL bridge 单行 2 KiB、总计 256 KiB、128 事件，vendor 输出不透传。React 只看到安全文件名、UUID、方法、阶段、进度、播放时间和固定错误，不得到路径、歌词正文、PID、stderr 或音频字节。

Main 固定最终 WAV/MP3 身份，播放/导出前重验格式和身份。结束、失败或取消撤销 Blob URL。SVS 启动目录只允许歌词及 manifest；完成后必须恰好剩 `elysia-cover.wav`、`elysia-cover.mp3`，晚到文件注入也会阻止发布。Linux 先暂存输出，删除私有 job 后才原子发布 vocal。

### 取消与清理所有权

取消匹配准确 UUID。Windows 使用固定 System32 `taskkill.exe /pid <PID> /t /f` 并等待退出；确认树结束后，WSL 两条路径另起 cleanup-only worker，只把同 UUID 交给固定 bridge。它验证 owner、mode、类型和无链接边界后，只删除 `jobs/<UUID>`，不扫描整个 jobs 根。

WSL preprocessing/inference 各在独立 session 运行，原子 lease 记录 PID、PGID、SID、kernel start ticks 与随机 stage ID。接触私有资产前，child 必须匹配父 lease 的 job token、stage、capability 和原生身份，并是 session leader。取消先写不可逆标记，再以 flock 串行化启动竞态，仅对同 UID 的准确 lease 成员 TERM→KILL 并确认退出。Runtime 共用 90 分钟预算（预处理最多 35、推理最多 55），内层清理 30 秒小于 Main 外层 60 秒。

cleanup-only 有固定成功帧、输出预算与超时；超时也必须确认其进程树停止。无法确认停止或删除时保留准确 UUID 的所有权，阻止下一任务、数据迁移和退出，不删除可能仍在使用的目录；重复退出不能绕过门禁。

临时数据位于创建时的数据根 `audio/song-covers/<job-id>/`。新建下一首、临时音频清理或退出会删除未导出结果；原生保存对话框导出的 WAV 不在此范围。需要保留结果应先导出再移动数据根。

启动恢复用残留 `lyrics-manifest.json` / `singing-request.json` 识别 WSL 事务，先对同 UUID cleanup-only，再删除 Windows job。失败保留现场/清理 lease，不能用“已取消”掩盖未释放资源。旧 RVC Runtime 的未知 `raw/`、`results/` 不再读取，也不由应用扫描删除。

## 本机 Runtime

应用不安装、下载或修复 Runtime。需要兼容 NVIDIA CUDA GPU、驱动、显存与 WSL GPU 集成；不自动切 CPU/DirectML。默认 WSL distribution 必须有 `/usr/bin/python3`，worker 不搜索其他 distribution。

```text
仓库内（Git 忽略）
models/cache/GPT-SoVITS-v2-240821/  runtime/python.exe、ffmpeg.exe、ffprobe.exe
models/cache/singing-runtime/     Demucs 与 htdemucs checkpoint
models/cache/rvc-v2-40k/           固定 RVC 源码、HuBERT、RMVPE
models/weights/rvc/elysia-v2-40k/  私有 model.pth / model.index

当前 Linux 用户的 Home
~/.local/share/elysia-ai/soulx/
├── python/cpython-3.10.22-linux-x86_64-gnu/
├── prep-env/、infer-env/、source/
├── models/SoulX-Singer/、SoulX-Singer-Preprocess/、SoulX-SVC/
├── prompts/elysia-bank-v1/        私有 manifest + 59 组 audio/metadata
├── prompts/singing-v1/           SVC 歌唱参考
└── jobs/                         临时 UUID 作业
```

Windows 不信任 `HOME` 或硬编码 Linux 用户名，固定 `wsl.exe`/`wslpath`/`python3 -I` 从当前 UID 的 POSIX home 推导 suffix。Windows child 使用 allowlist 环境，并把 USERPROFILE/APPDATA/LOCALAPPDATA 指向已验证的私有临时 profile，满足 Torch/librosa 的 Home/缓存需求而不继承真实用户目录；不提前占用 worker 排他创建的 UUID job。

### 资产认证

长度、SHA-256 与树摘要以 [Windows worker](../scripts/song_cover_worker.py)、[SoulX runtime](../scripts/song_svs_runtime.py) 和 [SVC 资产定义](../scripts/song_svc_assets.py) 为准。资产缺失/变化、危险链接或不干净 checkout 均失败，不接受近似替代。

Windows 校验解释器、FFmpeg/FFprobe、Demucs、RVC 源/数据、HuBERT、RMVPE、生产 e50 checkpoint 和 index；树摘要按排序相对路径、长度和内容计算。导入前清理生成 pycache 并拒绝覆盖受保护 import 的 sibling provider、未审 compiled code 与异常 scratch。

生产 HuBERT 为一次性兼容转换：将 original0/original1 键映射到固定 Transformers 4.36.2 识别的 weight_g/weight_v，tensor 不变，避免原格式加载静音。运行时不动态迁移未知 checkpoint。RVC 源固定于 `81eed5e8f68b6bed1789f682fe78cdd324495afc`，生产 checkpoint/index 只接受固定路径和字节身份。

WSL 认证解释器、FFmpeg、SoulX/RMVPE/ROSVOT/FunASR/OpenCC、参考 manifest 及引用资产；源码必须是固定干净 revision，`pretrained_models` 只指向私有 model 根。静态树验证 owner/权限，内部链接只能解析回受信树。

Prompt 目录精确 mode 0700、文件 0600、当前 UID 所有。用 O_NOFOLLOW 从同一 descriptor 验证普通文件、单链接、长度和 SHA-256，同时复制到不可写/扩缩/重新密封的 sealed memfd；关闭源后，DataProcessor 只读 `/proc/self/fd` 副本，防 rename/swap 和同 inode 后续改写。私有逐条文本、路径、摘要和音频不进入 Git/诊断。

这仍不是完整 Linux 供应链证明：未逐项认证的 Python 包和 Torch/CUDA 扩展依赖同一系统用户准备并信任的环境。更新核心摘要不能代替整个安装认证。

### 安装包状态

Windows 安装包已包含基础 Python 后端，但不包含翻唱专用 worker、上述 Windows 模型或 WSL runtime。翻唱仍为源码环境功能，安装包固定报告翻唱 Runtime 未包含；源码 readiness 只 lstat 必需 worker/core 文件，不联网、不运行模型。不可用时禁新建，但仍可播放/导出已完成结果；Main 在文件选择器前后重验，Renderer 状态不能授权启动。

## 验证与已知质量

自动化使用合成音频/fake model 验证协议、谱面对齐、预算、资产身份、进程清理和确定性；不能代替真实模型与听感验收。已有本机证据保留以下结论：

| 路径 | 已验证 | 仍有限制 |
| --- | --- | --- |
| 默认 SVC | 三首整曲结构验收；Main → Windows → WSL → 混音/导出及真实取消清理；用户接受当前可用版本 | 个别能量缺口、动态压缩及音色变化仍需逐段试听，不保证任意语言/重叠人声无伪影 |
| 歌词 SVS | 59/59 参考经真实 DataProcessor 加载；整曲候选/全局选择/发布；同环境独立短任务及多段确定性重放 | QA 自洽 LRC 不是产品查词回退，覆盖不等于所有歌曲、语言和音域通过 |
| Legacy RVC | 整曲精确样本数、输出/混音/清理；同环境 PCM 可复现，修复分段累计丢样 | 原有 false-voicing 电音与高频缺失仍在；失败的 UV 训练、事后替换及 one-shot 实验未进入生产 |

本机试听与机器证据位于被忽略的 `models/cache/rvc-eval/evaluation/quality-20261013/`（含 `full-listen.html`）及 `recheck-20261013/`，不进入分发。同环境一致不保证跨 Torch/CUDA/驱动/硬件一致；峰值、时长和数字静音检查不能代替对电音、口吃、压嗓、掉字和角色音色的听审。

### 自动化与私有 smoke

在仓库根运行相关测试与文档检查：

```text
python -m pytest tests -k "song_cover or song_rvc or song_lyrics or song_svs or song_svc"
python scripts/check_python_documentation.py
cd desktop
npm run docs:check
npm run typecheck
npm run test:contract
npx playwright test tests/ui/app-shell.spec.ts -g "Song Cover|Dictate"
```

私有 Runtime 验收先做只读预检。以下为 CMD 示例，不联网、不加载歌声模型：

```bat
.venv\Scripts\python.exe scripts\smoke_song_cover.py ^
  --vocals "D:\private\vocals.wav" ^
  --accompaniment "D:\private\accompaniment.wav" ^
  --lyrics-lrc "D:\private\lyrics.lrc" ^
  --plain-lyrics "D:\private\lyrics.txt" ^
  --key-shift-semitones 0
```

默认伴奏跟随，测试原调伴奏加 `--no-shift-accompaniment`。检查 JSON 后，显式 `--run-private-runtime` 才创建 UUID 并运行生产 SVS；验证编码、大小、时长、摘要和 cleanup-only。默认删除输出；人工试听可加 `--keep-output`，只留在 `%TEMP%\elysia-song-cover-smoke\<stdout job_id>`。stdout 不包含歌词、路径或底层诊断，私有素材不放入仓库/fixtures。

UI 验收应覆盖默认 source-svc/参考/原调，歌词模式成对 metadata 输入、网络和对齐失败，伴奏跟随、真实阶段、播放前后 Seek，以及收起后重新打开。两种 voice conversion 不应访问 LRCLIB。

## 权利边界

用户需有权处理歌曲、原唱录音、歌词、伴奏、参考与结果。在线取得歌词和本机推理不会自动授予复制、改编、发布、角色或配音权利。

SoulX、preprocessing、FunASR、ROSVOT、RMVPE、OpenCC、RVC、HuBERT、FAISS、Demucs、预训练权重、私有训练数据与参考均按各自来源/条款处理。RVC 上游 MIT 只覆盖适用源码，不为模型/数据授权。当前私有歌声资产不进入仓库或安装包；完整记录见 [MODEL_LICENSE.md](../MODEL_LICENSE.md)。
