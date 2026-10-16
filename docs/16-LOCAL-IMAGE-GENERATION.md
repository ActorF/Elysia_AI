# 本地图片生成

已实现受信生成核心、私有 ComfyUI 监督器、持久 Gallery、应用服务、Python Backend 路由、Electron 私有预览/原生导出和 React 图片生成器。**受信安装配置尚未接通**；真实模型/GPU 验收和跨模型 GPU 调度也未完成。

## 架构与责任

```text
结构化 ImageGenerationSpec + 受信 ModelProfile
  → 固定 SDXL txt2img Graph
  → 每任务独立的受管 ComfyUI
  → 关联结果、验证 PNG、停止进程、清理任务目录
  → 应用服务裁决取消或提交
  → 私有 Gallery
  → Python Desktop Backend / 二进制预览 Writer
  → Electron fd4 / 一次性预览 URL
  → React 图片生成器 / Gallery
```

| 层 | 责任 |
| --- | --- |
| `ImageGenerationSpec` | 验证结构化意图，不接受路径、脚本或任意 Graph |
| `ModelProfile` | 固定模型、提示词方言、尺寸和采样参数 |
| `ManagedComfyUiRuntime` | 验证资产、拥有子进程与私有目录、强制终止和清理 |
| `HttpComfyUiClient` | 回环 HTTP、严格提交/History 关联和有界轮询 |
| `ImageGenerationCore` | 编译、验证输出、完成清理后返回图片字节 |
| `ImageGenerationApplicationService` | 单任务准入、取消/提交裁决和 Gallery 发布 |
| `ImageGalleryStore` | 私有 PNG/Manifest、容量、恢复与按需重验 |

Core 只接收 `is_managed_private_runtime is True` 且持有组合根私有 capability 的会话。公开组合入口是 `create_managed_image_generation_core()`；Raw HTTP Adapter 固定为 `False`，不从包根导出。此边界防止项目内误组合，不声称隔离恶意 Python 代码。

## 请求合同

`image_generation/domain.py` 定义路径无关的 `ImageGenerationSpec`：

| 字段 | 约束 |
| --- | --- |
| `profile_id`、`size_preset_id` | 小写逻辑 ID，不能用文件名或任意宽高替代 |
| `appearance` | 必填，Unicode NFC，最多 1024 code points |
| `outfit`、`pose`、`scene`、`style`、`camera`、`lighting` | 可选，与外观相同的文本预算；姿势仅为文本 |
| `negative_prompt` | 可选，只能追加系统负面词 |
| `seed` | 0 至 JSON safe integer 上限 |
| `original_fictional_confirmed` | 必须为 `true` |
| `intent` | 仅 `new-character` |

控制字符、surrogate 和超长文本在编译前拒绝；编译后的正负提示词各最多 6000 code points。LLM 若参与填写，输出仍必须先通过同一合同，不能直接控制节点、模型或路径。

当前请求仅限原创虚构、明确成年的单个人物。普通和成人描述共用结构化字段与工作流，没有 `content_mode`、`adult_confirmed` 或成人模式开关。正向字段拒绝明显的未成年/年龄模糊、真人、已有角色和多主体矛盾；正负文本均禁止 `embedding:`，负面词不得否定成年人、原创虚构或单主体边界。

这些 Unicode 规范化词法检查只是正常输入的早期提示，不是年龄/身份分类证明，也不保证抵抗刻意规避。第一版没有照片、身份 Adapter、已有角色锁定、编辑/换装、姿势参考或批量入口。后续能力需要独立文件授权与合同，不能把路径、Base64 或 Node JSON 塞入提示词。

## 模型与固定工作流

`ModelProfile` 由应用审核，绑定逻辑 ID、显示名、`sdxl` 架构、相对 `.safetensors` Key、声明的 SHA-256/来源/许可、尺寸与 Sampler/Scheduler/Steps/CFG。支持 `sdxl-natural`、`pony-tags`、`illustrious-tags` 方言；相近架构不代表提示词语法通用。

尺寸单边 512–2048、为 64 的倍数，总计最多 4,194,304 像素，`batch_size=1`。Profile Digest 覆盖上述配置；只保存模型显示名不足以重现条件。

`image_generation/prompting.py` 按方言添加固定质量前缀和原创虚构成年人描述，再按稳定顺序连接字段。系统负面词不可删除；该模块不做自动扩写或随机提示词池。

`image_generation/workflow.py` 仅编译 `core-sdxl-txt2img` v1：

```text
CheckpointLoaderSimple
  → 正/负 CLIPTextEncode + EmptyLatentImage
  → KSampler → VAEDecode → SaveImage
```

节点、连接、`batch_size=1`、`denoise=1.0` 和输出节点由源码固定；可变值只能来自已验证 Spec/Profile。Canonical JSON 形成 Workflow Digest，`request_graph()` 每次返回新对象，防止调用者修改污染后续提交。输出前缀只含固定标识与 Core UUID，返回文件名从不当作本机路径打开。

## 私有 Runtime 与信任边界

普通 ComfyUI 的 `SaveImage` 会落盘，History 会保留 Graph/提示词；只清除返回 PNG 元数据不足以清理它们。因此生产为每个任务启动独立实例，发布前终止完整进程树并删除 Input、Output、Temp、User、Cache 和空 Frontend 目录。

启动前按 SHA-256 验证 Python、ComfyUI `main.py` 和 Checkpoint，并以 Windows 只读文件守卫固定身份/内容。命令使用 Volume-GUID 稳定路径，避免盘符重映射；随机回环端口必须属于准确 Child PID。HTTP 仍核对模型 Key。

私有根必须在固定本机卷，不能与 Runtime、解释器或模型路径重叠；已有任何条目都拒绝新任务。启动参数禁用 metadata、custom nodes、在线 assets、partner nodes 和浏览器自动打开；闭合环境将 HF/Torch/CUDA/Triton 等缓存全部指向当前任务根，不继承代理。

终止通过所拥有的 Windows Job Object，绝不接管共享 ComfyUI。确认完整进程树退出后才释放守卫，并在有界、不跟随链接的目录盘点后删除准确 UUID 根。Startup/Finalize/Close 共用终态锁，避免关闭后又启动或重复删除。清理无法证明完成就阻止结果发布。

硬崩溃/断电后，Job Object 在宿主句柄关闭时终止子进程，但磁盘目录可能残留。下次 preflight 保留现场并拒绝启动；当前没有自动残留恢复 UI，不猜测归属后删除。

**认证范围有限：** 尚未给 ComfyUI 所有模块和 Python 包建立完整清单。固定解释器、入口与模型摘要不等于整个依赖树可信；当前仍依赖用户受信的本机安装，升级需要重审摘要和真实验收。

## HTTP、结果与取消

Raw Adapter 只接受规范回环 HTTP Origin：`127.0.0.1`、IPv6 回环或规范化 `localhost`。拒绝 HTTPS、远程主机、凭据、路径、query、fragment；`trust_env=False`，禁用重定向/重试，要求 identity 编码。JSON、控制响应和 PNG 各有预算，响应体与轮询分别用不可续期的单调时钟总期限，防止慢速滴流。

1. `/system_stats` 确认 API/节点就绪；其中的命令行只验证，不记录或返回。
2. `/object_info/{node}` 检查六类内置节点，`/models/checkpoints` 检查准确 Key。缺配置不自动安装或重复等待完整启动超时。
3. 任何提交 I/O 前创建 `prompt_id`、`client_id` 和输出前缀；`/prompt` 必须回显同一 ID。
4. `/history/{prompt_id}` 必须匹配 Prompt、Client、输出节点和前缀，明确成功且恰好一图才接受。外来/冲突记录拒绝；节点错误转为固定脱敏错误。

Pending 只删除准确 Prompt ID。队列内重复或跨 Running/Pending 的身份歧义拒绝取消；只有独占实例且唯一运行 Prompt 属于本任务时，才可发送定向 `/interrupt`。不能安全物理取消时先做逻辑取消，迟到结果不发布；生产仍需终止所拥有的进程树并清理，不能把逻辑取消当作 GPU 和磁盘已释放。

ComfyUI Raw API 没有本项目的跨版本保证。Adapter 自带 ID/版本；升级后必须重跑 fake-server 合同与真实 smoke。协议变化应新增适配器版本，不静默放宽校验。

## PNG 与 Core 生命周期

`image_generation/png.py` 在图片进入解码器或图库前验证：PNG signature、非交错 8-bit RGB/RGBA、Chunk 顺序/CRC、连续 IDAT、预期尺寸、合法 filter、精确解压长度和最多 32 MiB 编码数据。拒绝尾随字节、损坏块和压缩炸弹。

只有惰性元数据 `eXIf`、`iTXt`、`tEXt`、`tIME`、`zTXt` 可在验证后移除。`tRNS`、`gAMA`、`sRGB`、`iCCP` 等影响透明度/颜色解释的块直接拒绝，避免悄悄改变像素。重新编码只含 IHDR/IDAT/IEND 的 canonical PNG，并计算新摘要。

Core 进度为 `validating → probing → submitting → sampling → verifying → completed`。回调异常不控制推理；编译、probe、等待、下载和发布前均检查取消。组合根再次解析 PNG，核对字节、尺寸、媒体类型、摘要和无元数据结论。

取得结构有效的 `ComfyPromptHandle` 后，`finalize(handle)` 必须在 `finally` 中执行，即使随后发现前缀错误。终态、History 和文件清理未确认不能返回成功；清理错误或非空协议返回值都阻止发布。若 prepare 没有返回有效 handle，Supervisor 必须自行回滚分配，Core 不猜测身份。

返回值含 job ID、canonical PNG、Profile/Workflow ID 与摘要、Workflow 版本、seed 和 UTC 时间。Core 不拥有 Gallery、导出、Desktop 事件、持久队列或全局 GPU lease。

## Gallery 与发布

### 私有存储

`ImageGalleryStore` 位于 `<data-root>/workspace/gallery`。权威记录是 `image_<uuid>/image.png` 与严格 `manifest.json`，公开对象只含 `gallery_<uuid>` 和路径无关元数据。可选提示词只入私有 Manifest；索引不含提示词或路径。

Create 在准确 `.staging_<uuid>` 写入/刷新文件后，以目录重命名逻辑提交。Delete 先改名为 `.deleting_<uuid>` 隐藏记录，再安全清理。此实现没有可移植 Windows 目录 fsync，不承诺抵抗所有断电/文件系统故障。

启动先完整验证所有权标记、根目录语法和候选节点，确认无未知条目后才清理 staging、tombstone 和索引临时文件。链接、junction/reparse、特殊文件、多硬链接及读取期间身份变化拒绝；Windows 根限固定本机受支持文件系统。

`index.json` 只是可重建缓存，损坏/缺失/不可写不阻止权威记录读取。打开 Store 验证 Manifest、结构、身份和声明大小；`open_verified()` 才读取目标 PNG 并重验摘要/内容，避免每次列表或创建都解码整个图库。Store 检查一致性，不是生成来源或提示词真实性的加密证明。

| 容量 | 上限 |
| --- | --- |
| 图库 | 1024 个 artifact、4 GiB 图片 |
| 单图 | 32 MiB，仍受像素预算约束 |
| Store 单页 | 256 条 |
| Desktop 单页 | 255 条，预留 `limit + 1` 判断 `hasMore` |

进程内 RLock 与非阻塞跨进程 lease 串行化单写者。同一账户主动绕过 lease 并发替换目录不在当前威胁模型内，静态重定向和异常结构仍拒绝。

### 取消或提交

应用服务为每次尝试使用 `imageop_<uuid>`，结束后将 ID 退休至有界进程内集合；迟到取消不能影响后续任务。只允许一个生成任务，Gallery 操作由 Store 自行串行化。

```text
RUNNING → CANCEL_REQUESTED → 丢弃结果，不创建 Gallery
        ↘ COMMITTING      → 返回 Gallery 提交结果
```

两条分支在同一 Condition 锁内裁决。取消先获锁，即使 Core 已返回图片也丢弃；提交先获锁，取消返回 `too-late`，以真实保存结果为终态。Core 的 `completed=100` 被拦截，权威目录提交成功后才发布一次完成。

私有 Manifest 仅保存规范化用户正向组件，不把它冒充完整系统/方言/负面提示词。公开异常不含路径、提示词或 Runtime 响应。

关闭先永久停止准入：运行任务协作取消，提交中的任务完成短提交，已接纳 Gallery 调用排空后释放资源。并发关闭共享结果；失败时后续 close 只重试未释放资源，不重新开放准入。

## Python Desktop Backend 与预览合同

Protocol v1 已定义并接通 `image.generate` 和 `image.gallery.list/get/delete/open`，但不代表已有产品入口。请求禁止路径、任意 Graph、图片字节或内容模式字段；Backend 固定 `new-character`。图片请求只要求已完成认证握手，不依赖 Brain、Ollama 或 Chat 初始化，也不会将提示词交给语音模块。

`create_managed_image_application_service(profiles, config, data_root)` 为受信启动组合入口：对所有 Profile 完成解释器、入口和 Checkpoint 文件/Hash 预检，再打开固定的 `<data-root>/workspace/gallery`，并组合现有 Core 与应用服务。预检不启动 ComfyUI，不代表真实 GPU/API 验收；每次生成仍重新验证资产并检查私有 ComfyUI API。私有运行根与 Gallery 不得重叠，构造失败分别关闭已分配的 Runtime 和 Gallery，公开错误不保留私有异常链。

启动宿主将返回的服务和独立 `ImageChannelWriter` 分别注入 `DesktopBackend(image_service=..., image_writer=...)`，Backend 接管两者的关闭责任。只有两者都存在时，握手才广告五项图片 Capability；缺少任一依赖时，所有合法图片请求明确返回 `image.unavailable`。当前 `main()` 已接收固定 fd4 图片 Pipe，但尚无受信安装配置，因此默认不创建图片服务，也不广告图片能力。请求解析成功但尚未实现的其他方法同样有明确的兜底拒绝。

`desktop_images.py` 将同步生成和 Gallery I/O 放在独立工作线程，保持请求读取和取消可响应。生成只准入一个任务，以请求 ID 关联内部单次 `imageop_<uuid>`；预留与应用服务准入之间的取消意图会保留，并在首次进度回调后重送，避免立即取消丢失。最终取消或提交仍由应用服务裁决，过期请求 ID 不能取消后续任务。

协议进度固定为 `validating=5 → probing=15 → submitting=25 → sampling=35 → verifying=90 → completed=100`，`total=100`。只映射已审查的阶段，不转发 Runtime 诊断；100 仍只在 Gallery 提交成功后出现。Gallery 分页读取 `limit + 1` 条判断 `hasMore`，每个 Backend 最多一个 Gallery 工作线程，限制阻塞预览期间的内存占用。

Open 的 JSON 只含 artifact ID、一次性 64-hex token、尺寸、长度、摘要与 `image/png`。实际像素通过 `ELYSIMG1` v1：88-byte little-endian header + payload。读取前限制 32 MiB、单边 2048 和总像素；收到后核对 SHA-256、signature 与 IHDR。Writer 最多一帧，部分写入/篡改/关闭失败使通道拒绝后续操作；Reader 每次返回一帧及精确消费量，便于保留后缀和背压。

Gallery Open 在读取时重新验证 PNG，由 Writer 创建一次性 Token；先发送 JSON Metadata，再在工作线程写入准确的一帧，避免 Main 等待 Token 而 Writer 被 Pipe 背压阻塞。已发送 Metadata 后的写入失败不会再次发送矛盾的 JSON 终态；通道失败后停止图片准入并撤销后续握手中的图片 Capability。PNG 不进入 NDJSON，也不转为 Base64。

关闭、stdin EOF 和读取异常都先关闭图片输出 Gate，并通过应用服务 `request_shutdown()` 原子停止准入。已经获胜的 Gallery Commit 必须排空后才确认关闭；其余推理和预览关闭使用有界等待，后台所有者继续持有资源直到排空。Runtime 经由应用服务/Core 关闭，不能提前直接关闭在用 Runtime。Writer 在独立关闭线程等待已有写入结束，不阻塞 Runtime 清理；Electron 在退出/失败时先销毁私有 Pipe 读端，释放可能阻塞的 Writer。关闭失败保留所有者供后续调用重试；有界等待结束不表示资源已证明释放。

Electron Main 创建并拥有 fd4，Python 接管前验证它是 Pipe 并关闭可继承标志。`image-preview-channel.ts` 在有界分片缓冲中验证 Header、保留字节、Token 序列、32 MiB 上限、SHA-256 和 canonical PNG envelope；只有已消费的一帧被释放后才继续读取。截断、超时、错配或流错误销毁图片通道，不能回退到 stdout 或 fd3。

`ImagePreviewOwner` 将 Open Metadata 与私有帧的 Token、长度、摘要、尺寸、媒体类型绑定，最多容纳一帧短暂先于 Metadata 到达的乱序数据。源 Token 和 Renderer URL 都只能使用一次；Renderer URL 使用独立随机值、最多 60 秒、内存上限两个图片，采用 `elysia-image://preview/<token>`，不包含磁盘路径。Main 的受信 IPC 只接受 opaque artifact ID，协议处理器使用 `no-store`/`nosniff` 返回 PNG。关闭或重启撤销全部 URL 和等待者；已取消的控制请求保留有界退休 ID，跨管道迟到响应不会破坏关闭流程。

## 桌面产品与受信配置

侧栏“图片生成”进入独立 Image Studio，提供 Profile、尺寸、外观、服装、姿势、场景、风格、镜头、光线、负面词与安全整数 Seed。每次生成必须确认明确成年、原创虚构、单人物且不涉及真人。所有描述使用同一 DTO，没有内容模式开关。

进度与取消来自真实 Backend 操作；取消确认不释放生成按钮，直到生成任务终态到达。页面保持挂载，切换 Chat/Settings 不丢失当前任务或取消入口。Gallery 支持分页、按需私有预览、确认删除和 Main 原生导出；过期预览明确提示重新打开。缺失模型、Hash 不一致、运行错误与空画廊均有独立状态。

原生导出已由 Main 接管：Renderer 仅提交 Gallery artifact ID，Main 显示 Save Dialog 后，重新通过 Backend/Gallery 和 fd4 获取验证后的 PNG。`ImageExportOwner` 在所选目录创建排他的临时文件、刷新后原子发布；已有目标保留回滚副本，失败时恢复原文件。路径和原生错误不进入 Python 或 Renderer。取消对话框不会读取图片；应用关闭中止等待并排空临时文件清理。路径身份检查拒绝链接、非普通文件和替换，但纯 Node 在最终身份检查与 rename 系统调用之间仍无 handle-relative 比较交换保证。

Settings 的“图片生成”页通过 Main 原生选择 ComfyUI 目录、解释器和 `models/checkpoints` 下的 SDXL 模型。Renderer 只提交逻辑 Profile、默认尺寸、来源和许可；Main 流式计算稳定文件 SHA-256，原生确认后通过私有 Worker 写入固定配置。配置期间保留维护准入，取消不重启 Backend；成功写入新 Revision 后重启以重新验证并广告 Capability。

配置保存到私有数据根的 `workspace/settings/images.json`，使用 Revision 比较交换和原子发布。Backend 启动只从该受信配置组合 Runtime/Profile/Gallery；缺失模型、摘要变化和不可用安装明确呈现。损坏配置只能在原生确认后修复，原字节备份到固定私有文件，Gallery 保留。启动期间读取多 GiB 模型摘要，因此握手期限为 180 秒。

下一步需要：

- 建立与 Ollama、Voice、Song Cover 共用的重型 GPU 准入，提供有上限的可取消等待，并按 job 在失败/重启/关闭时释放 lease。
- 完成真实 ComfyUI/模型/GPU smoke 与 soak、依赖树认证和可见的崩溃残留恢复。

当前没有全局 GPU 所有者，不能声称与其他重型任务安全并行。参考图、OpenPose/ControlNet、Inpaint、FaceDetailer 和连续角色变体也需各自合同及验证，不能开放任意 Workflow JSON 代替设计。

## 模型许可与语音隔离

仓库不分发 ComfyUI、Checkpoint、LoRA/ControlNet/IP-Adapter/VAE、Custom Nodes、生成图片或本机 Gallery。Profile 记录来源和许可不等于取得权利；真实模型加入前需核对内容、商业使用和再分发条件。

爱莉希雅 GPT-SoVITS 随附说明禁止 R18 使用。成人图片流程不得调用该模型、参考音频或衍生语音；状态提示保持无声，或仅用独立且许可允许的通用系统提示。Image Core 不导入 `voice/`、不携带 Voice Profile/Emotion；后续接线不能自动朗读成人图片提示词或结果。此隔离不替代图片模型本身的许可审核。详见 [MODEL_LICENSE.md](../MODEL_LICENSE.md)。

## 验证

离线测试覆盖结构化请求、固定 Graph、PNG、HTTP 所有权/期限、受管进程与清理、Gallery 恢复/容量、取消提交竞争，以及 Python/TypeScript 共享协议和二进制分片。测试不安装 ComfyUI、不加载真实模型或使用 GPU。

```text
python -m pytest tests/test_image_generation_core.py tests/test_comfyui_client.py tests/test_managed_comfyui.py tests/test_private_run_directory.py tests/test_windows_loopback_owner.py tests/test_image_gallery_store.py tests/test_image_application_service.py tests/test_desktop_image_composition.py tests/test_desktop_backend_images.py tests/test_desktop_backend.py tests/test_desktop_image_channel.py tests/test_desktop_protocol.py
cd desktop
npm run test:contract
npm run test:image
```

真实模型画质、显存峰值、长时间取消与目标 GPU 兼容性需要单独本机验收。
