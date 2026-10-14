# 本地图片生成核心、私有运行时与持久 Gallery

最后更新：2026-10-14（America/Chicago）

## 1. 当前状态

Elysia AI 已加入第一版**受信本地图片生成核心、私有 ComfyUI 进程监督器与可恢复 Gallery Store**。当前 Python 层可以验证结构化请求与本机模型、启动每任务一个隔离子进程、执行固定工作流、验证输出，并在结果返回前终止进程和删除私有运行目录；生成结果还可独立提交到路径私有的本地 Gallery。Electron/React 产品界面、Gallery 的 Desktop 组合与导出、真实模型验收仍未接通。

当前已完成：

- 用 `ImageGenerationSpec` 表达一张新图片的结构化需求。
- 用受信 `ModelProfile` 固定模型家族、Checkpoint 声明、Prompt 方言、尺寸和采样参数。
- 将结构化需求编译成源码内固定的 SDXL `txt2img` ComfyUI API Graph。
- 提供一个只连接明确本机 Loopback ComfyUI HTTP Origin 的底层 `HttpComfyUiClient`；它明确声明 `is_managed_private_runtime=False`，不从 Package Root 导出，也不能直接注入 `ImageGenerationCore`。
- 在发起网络提交前预生成 `prompt_id`、`client_id` 与输出 Prefix，按三者精确关联提交、History 和输出，有界轮询结果并保守处理取消。
- 由 `ManagedComfyUiRuntime` 验证 Python、ComfyUI 入口和 Checkpoint 摘要，锁定文件并使用稳定卷路径启动受管子进程；随机 Loopback Port 还会绑定到准确 Child PID。
- 每项任务使用独立 Input、Output、Temp、User、Cache 与空 Frontend 目录；代理、继承环境、Custom Nodes、在线资产、Metadata 和自动打开浏览器均不会进入该子进程。
- 成功、失败、取消和异常都先终止完整 Job Object 进程树，再释放文件守卫并删除准确 UUID 目录；任一步无法证明完成都会阻止结果发布。
- 验证 PNG Chunk、CRC、像素格式、尺寸和解压上限；只移除惰性 Metadata Chunk，遇到会改变像素解释的 Ancillary Chunk 则拒绝，再重写 Canonical PNG。
- 返回图片字节、模型 Profile Digest、Workflow Digest、Seed 与 UTC 时间等可复现 Metadata。
- 用 `ImageGalleryStore` 在 `ProductionDataLayout.gallery`（`workspace/gallery`）下保存 Canonical PNG 与严格私有 Manifest；提供 Opaque ID、路径无关 Metadata、按需重新验证的 PNG Bytes、逻辑原子 Create/Delete、容量限制、可重建 Index 和启动恢复。

当前尚未完成：

- Electron/React 的 Image Generator、Settings、进度、取消和错误界面。
- Gallery 的 Desktop 组合、受限预览、原生导出、容量展示和 UI。
- 与 Ollama、GPT-SoVITS、Song Cover 共用的进程级 GPU Admission/Lease。
- 模型安装、下载、更新、Hash 实测和许可证向导。
- 使用真实 ComfyUI 版本、真实 Checkpoint 和目标 GPU 的本机 Smoke/Soak 验收。
- Reference Image、同一角色连续生成、OpenPose、ControlNet、IP-Adapter、Inpaint 或 FaceDetailer。

因此，这一层已经可以作为 Desktop Integration 的受信后端，但还不是普通用户可从界面直接使用的完整功能。普通共享 ComfyUI 的 `SaveImage` 会把原图写进 Output Directory，`/history` 还会保留 Workflow 与 Prompt；Elysia 不连接或接管这种共享实例，而是为每个任务启动并销毁自己的私有实例。

---

## 2. 设计目标

图片生成 Runtime 可以加载巨大模型、执行第三方节点、读取本机模型目录，并返回复杂输出。Elysia AI 不允许 Renderer 或语言模型直接控制这类执行面。

第一版采用以下分层：

```text
未来 Desktop Image UI
        │
        │ 结构化字段；不包含路径或 Workflow JSON
        ▼
ImageGenerationSpec
        │
        ├── Safety / Size / Text Validation
        ▼
Trusted ModelProfile Catalog
        │
        ├── Prompt Dialect Compilation
        └── Fixed SDXL txt2img Graph Compilation
        ▼
Elysia-managed Private ComfyUI Runtime
        │
        ├── Owned Child PID / Isolated Directories
        ├── Metadata / Custom Nodes / Online Assets Disabled
        ├── Closed Environment + Private Cache Directories
        └── Process-tree Stop + Whole-run Cleanup
        ▼
Loopback-only Raw ComfyUI HTTP Adapter
        │
        ├── Node / Checkpoint Preflight
        ├── Correlated Prompt Submission / Polling / Safe Cancel
        └── Opaque `/view` Fetch
        ▼
PNG Canonicalization
        │
        ▼
Verified bytes + reproducibility metadata
        │
        ▼
可选 Trusted Gallery Store（已实现，尚未接 Desktop）
```

核心原则：

1. **LLM 只产生结构化意图。** 它不能产生任意 ComfyUI Graph、节点类、模型路径、输出路径或脚本。
2. **Profile 是应用配置。** 用户请求只能选择逻辑 `profile_id` 和 `size_preset_id`，不能把文件名伪装成模型选择。
3. **Workflow 由源码控制。** 第一版 Graph 的节点和连接不可由 Renderer 或 Prompt 改写。
4. **Runtime 输出不可信。** 即使来自 `127.0.0.1`，HTTP、JSON、队列记录、输出名和 PNG 仍须完整验证。
5. **共享 Runtime 不得执行。** `ImageGenerationCore` 同时要求 `is_managed_private_runtime is True` 和 Composition Root 私有签发的精确 Capability Identity；Raw Adapter 固定返回 `False` 且没有该 Capability。Capability 防止项目内部误组合，不声称隔离恶意 Python 代码。
6. **验证和清理完成前不发布。** 只有受管会话确认完整子进程树停止、私有任务目录删除、PNG Canonicalization 成功且最后一次取消检查未获胜，图片字节才可进入 Gallery Store。

---

## 3. 内容与身份边界

### 3.1 仅限虚构成年人

图片生成使用一个统一合同，同时允许普通图片和 R18 图片；没有普通/成人模式切换，也不会因为正向 Prompt 含露骨词而改走另一套流程。所有请求只允许**原创虚构且明确为成年人的角色**。第一版不要求填写具体数字年龄；数字本身也不能证明成年。系统要求的是：

- 所有请求都显式设置 `original_fictional_confirmed=true`，确认主体是原创虚构人物而非真实人物或已有角色；固定 Prompt 另外把主体限定为成年人。
- 普通描述和露骨描述使用完全相同的结构化字段、工作流、进程隔离和清理合同。
- 所有正向字段都保守拒绝明显的未成年人/年龄模糊、真实人物、多个主体和已有角色矛盾。
- 正负 Prompt 都禁止 ComfyUI `embedding:` 文本嵌入语法，防止自由文本选择 Profile 之外的本机模型资产。
- Negative Prompt 不得否定 `adult`、`fictional`、`original character` 或单主体等强制边界；它只能追加画质或内容排除条件。

这些 Unicode 规范化后的词法规则只是面向正常输入的**非对抗性 Tripwire**，用于尽早发现主体边界的明显矛盾；它们不是身份或年龄分类的证明，也不承诺抵抗刻意规避。后续 Desktop 层必须清楚表达并保留原创虚构成年人确认，但不需要内容模式开关。

### 3.2 第一版没有身份参考入口

当前 `ImageIntent` 只有 `new-character`，并且 Prompt 固定加入“original fictional adult character”。第一版不接收：

- 真实人物或真实人物相似度请求。
- 参考照片、脸部嵌入或身份 Adapter。
- 已存在角色的身份锁定。
- 图片编辑、换装、局部重绘或姿势参考。
- 多主体或批量出图。

未来若加入 Reference/Image Edit，必须增加独立的受信字段、文件授权、来源和身份政策；不得把图片路径、Base64、Node JSON 或隐藏命令塞入现有 Prompt 文本绕过合同。

### 3.3 单一内容路径

`ImageGenerationSpec` 不再包含 `content_mode` 或 `adult_confirmed`。露骨程度不是工作流分支，也不会改变模型、清理或输出验证规则。请求是否普通或 R18 都只需要同一个 `original_fictional_confirmed=true` 主体确认；未成年人/年龄模糊、真人相似、已有角色和多主体的明显矛盾始终拒绝。

---

## 4. 结构化 Image Spec

`image_generation/domain.py` 定义路径无关的 `ImageGenerationSpec`。当前字段包括：

| 字段 | 用途 | 当前约束 |
| --- | --- | --- |
| `profile_id` | 选择受信模型 Profile | 小写逻辑 ID；不是路径 |
| `size_preset_id` | 选择 Profile 内尺寸 | 小写逻辑 ID；不是任意宽高 |
| `appearance` | 角色外观 | 必填、Unicode NFC、最多 1,024 Code Points |
| `outfit` | 服装 | 可选、同一文本预算 |
| `pose` | 姿势描述 | 可选；当前仅 Prompt，不是 OpenPose |
| `scene` | 场景 | 可选 |
| `style` | 画风 | 可选 |
| `camera` | 镜头与构图 | 可选 |
| `lighting` | 光线 | 可选 |
| `negative_prompt` | 用户补充的负面词 | 可选；系统负面词不会被替换 |
| `seed` | 可复现 Seed | `0` 至 JSON Safe Integer 上限 |
| `original_fictional_confirmed` | 原创虚构主体确认 | 所有请求都必须为 `true` |
| `intent` | 工作流意图 | 第一版只有 `new-character` |

控制字符、Surrogate 和超长字段会在工作流编译前拒绝。编译后的正向和负向 Prompt 也有独立的 6,000 Code Point 上限，避免多个合法字段组合成无界 Runtime 输入。

---

## 5. 受信 Model Profile

`ModelProfile` 不是用户自由编辑的启动参数，而是应用拥有并审核的配置。它绑定：

- 唯一逻辑 ID 与显示名。
- `sdxl` 模型架构。
- `sdxl-natural`、`pony-tags` 或 `illustrious-tags` Prompt 方言。
- ComfyUI Model List 中的相对 `.safetensors` Key。
- 管理员声明的 Checkpoint SHA-256、来源和许可证。
- 一个或多个固定 Size Preset。
- Sampler、Scheduler、Steps 与 CFG。

Pony、Illustrious 与普通 SDXL 即使共享相近的 Tensor 架构，也不代表 Prompt Grammar 可以互换。因此 Prompt Prefix 由 Profile 方言决定，而不是把所有模型都当成同一种自然语言模型。

尺寸必须满足：

- 单边 512–2,048 Pixels。
- 宽高都是 64 的倍数。
- 总像素不超过 4,194,304。
- 第一版 `batch_size` 固定为 1。

Profile Digest 覆盖架构、Prompt 方言、Checkpoint 声明、来源、许可证、尺寸与采样默认值。Gallery 的私有 Manifest 已保存 Profile/Workflow ID 与 Digest、Workflow Version、Job ID 和 Seed；公开 `ImageGalleryItem` 不暴露私有 Provenance 或本机路径。只保存显示名不足以重现生成条件。

### 当前 Hash 与安装信任边界

Raw Model List 只返回模型 Key，不返回 Checkpoint 摘要。因此受管 Runtime 会在启动前直接读取并验证 Python Executable、ComfyUI `main.py` 与所选 Checkpoint 的 SHA-256，再用 Windows 只读文件守卫固定三者的身份和内容。启动命令使用守卫导出的 Volume-GUID 稳定路径，避免验证后发生盘符 ABA Remap；HTTP Probe 仍会确认 ComfyUI 只暴露准确的 Checkpoint Key。

这不是完整的软件供应链证明：当前配置没有为 ComfyUI 的每个源码模块和 Python Site Package 建立逐文件 Manifest。Runtime 与 Python 环境仍必须来自用户明确选择且受信的固定本机安装；版本升级后需要更新受审 Hash 并重新运行真实 Smoke Test。后续安装器若负责下载 Runtime，应增加完整签名/Manifest 验证，而不是把只验证 `main.py` 描述成整个依赖树均已认证。

---

## 6. Prompt 编译

`image_generation/prompting.py` 负责把结构化字段编译为模型方言：

- 按 Profile 选择质量 Prefix。
- 固定加入原创虚构成年人主体描述。
- 按 UI 稳定顺序连接 Appearance、Outfit、Pose、Scene、Style、Camera 与 Lighting。
- 固定加入未成年人、差解剖、异常手指、多余肢体、畸变脸、文字、水印和 Logo 等负面词。
- 用户的 Negative Prompt 只能追加，不能删除系统负面边界。
- 自由文本不能使用 `embedding:` 选择 Textual Inversion 资产，也不能在 Negative Prompt 中反向否定成年人、原创虚构或单主体边界。

该模块不负责自动扩写、Prompt 随机池或自然语言代理。如果未来让 LLM 辅助填写字段，LLM 输出仍必须先转换并验证为 `ImageGenerationSpec`。

---

## 7. 固定 SDXL txt2img Workflow

`image_generation/workflow.py` 当前只编译 `core-sdxl-txt2img` v1，节点闭集为：

1. `CheckpointLoaderSimple`
2. 正向 `CLIPTextEncode`
3. 负向 `CLIPTextEncode`
4. `EmptyLatentImage`
5. `KSampler`
6. `VAEDecode`
7. `SaveImage`

节点 ID、连接、`batch_size=1`、`denoise=1.0` 和输出节点都由源码固定。可替换值只来自已验证 Spec/Profile。编译结果以 Canonical JSON 保存并计算 Workflow Digest；每次 `request_graph()` 都返回新对象，避免一个调用者的 Mutation 污染下一次提交。

`SaveImage` 的 Prefix 只包含 Elysia 固定前缀与 Core 创建的 UUID。后续代码不依赖 ComfyUI 的本机输出路径，也不会把 Runtime 返回的文件名当作 Windows 路径打开。

`SaveImage` 仍会先把源 PNG 持久化到 ComfyUI 的 Output Directory，ComfyUI `/history` 也会在内存中保存完整 Graph，通常包括正负 Prompt。Canonicalizer 只清理取回副本中的 Metadata，因此 Raw Adapter 不是安全的生产会话。受管 Runtime 把 Input/Output/Temp/User/Cache 全部放进单次任务根，禁用文件 Metadata，在结果发布前终止整个私有实例并删除任务根；进程退出也同时销毁该实例的内存 History。

---

## 8. ComfyUI Loopback Adapter

### 8.1 网络边界

`HttpComfyUiClient` 是供 Contract Test 和未来私有 Runtime Supervisor 使用的底层 Raw Adapter，只接受 Canonical Loopback HTTP Origin：

- 允许 `127.0.0.1`、IPv6 Loopback 或规范化后的 `localhost`。
- 拒绝 HTTPS、远程 Host、Credentials、Path、Query 与 Fragment。
- `requests.Session.trust_env=False`，不继承代理。
- 禁止重定向与重试。
- 请求 `Accept-Encoding: identity` 并拒绝意外压缩编码。
- JSON、Control Response 与 PNG 各有固定 Byte Budget；每个 HTTP Body 使用一个不可续期的绝对单调时钟 Deadline，Job Polling 也使用独立总 Deadline，慢速滴流不能靠每次收到少量字节无限延长请求。

本机服务不自动等于可信服务。禁止 Proxy/Redirect 的目的，是防止“本机 ComfyUI 地址”被环境变量、DNS、Hosts 修改或恶意响应转发到远程位置。

Raw Adapter 的 `is_managed_private_runtime` 固定为 `False`，不从 `image_generation` Package Root 导出，也无法通过 `ImageGenerationCore` 的组合边界。Loopback 只限制网络目的地，并不证明 Elysia 拥有该进程、目录、History 或正在运行的其他 Job。

### 8.2 Preflight 与提交

私有子进程启动后，Supervisor 先用 `/system_stats` 确认 API 和节点初始化完成；响应含有 Child Command Line，因此只验证必要字段，不返回或记录完整文档。随后 Adapter 使用 `/object_info/{node}` 检查六类必需 Built-in Node，并用 `/models/checkpoints` 确认 Profile 声明的准确 Checkpoint Key 存在。它不会自动安装缺失节点、下载模型或选择名字相近的替代品。API 已就绪后的缺节点/缺模型属于稳定配置错误，不会无意义地重试完整启动超时。

Adapter 先在任何 Submission I/O 发生前预生成 Canonical `prompt_id`、`client_id` 与 Workflow Output Prefix，供未来 Durable Caller 先记录；随后 `/prompt` Payload 只含这组身份与固定 Graph。Response 必须回显完全相同的 `prompt_id`。`/history/{prompt_id}` 还必须精确匹配 Prompt ID、Client ID、预期 Output Node 和文件 Prefix；任一不一致、冲突或外来记录都会 Fail Closed。Node Error 或异常响应会转换成稳定、脱敏的项目错误。

### 8.3 等待与取消

Adapter 有界轮询 `/history/{prompt_id}`，只接受准确关联到本次 Prompt 的唯一记录、明确的 `completed=true`/成功状态、预期输出节点和恰好一张图片。失败状态映射为稳定生成失败；存在 Status 却既未明确成功也未明确失败时继续等待或在不一致时 Fail Closed，绝不因图片字段恰好存在而提前接受。

取消规则：

- 如果 Prompt 仍在 Pending Queue，只删除准确的 `prompt_id`。
- 同一个 Prompt ID 在 Running/Pending 中重复，或同时出现在两者中，属于不可信的所有权歧义；Adapter 拒绝取消，不猜测应该修改哪一项。
- `/interrupt` 是 ComfyUI Instance-global 操作；共享实例中不会为了取消 Elysia 的任务而中断他人的 Active Prompt。
- 只有配置为 Exclusive Instance，而且 Queue 证明当前唯一 Running Prompt 正是本任务时，才允许发送带目标 `prompt_id` 的 `/interrupt`。
- 无法安全物理取消时采用逻辑取消：Native 任务可能继续运行，但其迟到输出永不发布。

逻辑取消适合保护调用者不接收迟到结果，却不能证明 GPU 工作、`SaveImage` 文件或 `/history` 已消失。生产会话还必须等待 Native Job 终态并完成清理；API 失联时，只能终止自己拥有的子 PID，不能杀死用户单独启动的共享 ComfyUI。

### 8.4 Raw API 兼容限制

当前实现直接使用 ComfyUI Raw HTTP API，而不是把第三方服务当成永久稳定的产品协议。ComfyUI Raw API 不提供跨版本兼容保证；因此：

- Adapter 有自己的 `COMFYUI_ADAPTER_ID` 与 `COMFYUI_ADAPTER_VERSION`。
- ComfyUI 升级后必须重新运行 Fake-server Contract Test 和真实本机 Smoke Test。
- 只有必需节点、Model List、Prompt/History/Queue/View 响应仍符合严格合同，才可把新版本标记为兼容。
- 未来若迁移到官方 Versioned SDK/API，应新增 Adapter 版本，不在原实现中静默放宽解析。

---

## 9. PNG 输出验证

ComfyUI 的 `/view` 返回值先由 `image_generation/png.py` 处理，不能直接交给 Electron、浏览器 Decoder 或磁盘图库。

第一版只接受：

- PNG Signature。
- 非 Interlaced。
- 8-bit RGB 或 RGBA。
- 合法且按顺序出现的 `IHDR`、连续 `IDAT`、`IEND`。
- 每个 Chunk CRC 正确。
- 与 Workflow 预期完全一致的宽高。
- 编码数据不超过 32 MiB。
- 解压后的 Scanline 长度精确等于像素画布预算。
- 合法的 PNG Filter Byte。

只有确定不影响像素解释的惰性 Metadata Chunk（`eXIf`、`iTXt`、`tEXt`、`tIME`、`zTXt`）会在通过结构/CRC 检查后丢弃。`tRNS`、`gAMA`、`sRGB`、`iCCP` 等会改变透明度、色彩或渲染语义的 Ancillary Chunk 不会被静默剥除，而是直接拒绝；否则“Canonicalization”可能改变用户看到的像素。最终重新编码成只含 `IHDR`、`IDAT` 和 `IEND` 的 Canonical PNG，并计算新的 SHA-256，同时拒绝 Trailing Bytes、损坏 Chunk 与压缩炸弹。

---

## 10. Core Service 生命周期

`ImageGenerationCore.generate()` 定义同步、单次、无 Gallery 持久化的可信用例。它的构造函数只接受同时声明 `is_managed_private_runtime is True` 并持有 Composition Root 私有 Capability Identity 的会话；底层 `HttpComfyUiClient` 固定为 `False` 且没有该 Capability。`create_managed_image_generation_core()` 是当前唯一公开的具体组合入口，它创建 `ManagedComfyUiRuntime` 后再注入 Core。

进度闭集为：

```text
validating → probing → submitting → sampling → verifying → completed
```

进度回调是提示性观察者；回调异常不能控制推理或绕过验证。取消会在编译后、Probe 后、等待期间、下载后和发布前检查。Core 不信任 Wrapper 声称已经 Canonicalize 的对象，而会在 Composition Root 对候选 Bytes 再解析一次，并要求 Bytes、尺寸、Media Type、Digest 与无 Metadata 结论完全一致。最后一次检查确保用户取消已经获胜时，即使图片通过验证也不会返回为可发布 Artifact。

一旦 `prepare()` 返回结构有效的 `ComfyPromptHandle`，`finalize(handle)` 必定在 `finally` 中执行，包括随后发现 Output Prefix 不匹配的情况。成功结果也不能在 Finalizer 确认 Native Terminal State、精确 History 与源/临时文件已清理前返回；清理抛错或返回非空协议值都会阻止发布。这样成功、失败、取消和验证拒绝都收敛到同一强制清理边界，而不只依赖 Core 的逻辑取消。若 `prepare()` 连结构有效的 Handle 都没有返回，Core 无法安全猜测清理 Identity，因此 Supervisor 必须保证在抛错或返回无效值前自行回滚内部 Allocation。

生成结果包括：

- Core 创建的 `job_id`。
- `CanonicalPng` Bytes、宽高、Media Type 与 SHA-256。
- Profile ID 与 Profile Digest。
- Workflow ID、Version 与 Digest。
- Seed。
- UTC `created_at`。

当前 Core 不负责：

- 多 Job Queue。
- Durable Job Record。
- Gallery Storage。
- 导出路径或文件选择。
- Electron Event。
- Process-level GPU Lease。

子进程和私有目录由注入的 Managed Runtime 负责，而不是 Core 自身；持久化由独立 `ImageGalleryStore` 承担，Desktop Event、导出、Queue 和 GPU Lease 仍必须由后续 Integration Layer 添加，不能绕过 `is_managed_private_runtime` Gate。

### 10.1 已实现的受管 Runtime 合同

`ManagedComfyUiRuntime` 当前实现：

- 启动并持有唯一 Child PID，使用随机 Loopback Port；绝不接管或终止用户自行启动的共享 ComfyUI。
- 为每个受管实例设置 Elysia 拥有的隔离 Input、Output、Temp、User、Cache 与空 Frontend Directory。
- Private State Root 必须位于固定本机卷，且不能与 Runtime、Interpreter 或 Models 路径重叠；发现任何已有 Entry 都拒绝创建新任务。
- 使用 `--disable-metadata`、`--disable-all-custom-nodes`、`--offline`、`--disable-assets`、`--disable-partner-nodes` 与 `--disable-auto-launch` 收窄执行面。
- 用最小闭合环境启动；Temp、User、HF/Torch/CUDA/Triton/Numba/Matplotlib/Pip 等常见缓存全部指向单次运行根。
- API 失联、取消、推理失败或验证失败时只终止所拥有的 Job Object 进程树，确认停止后再释放 Guard 和删除准确 UUID Root。
- 删除前完整盘点且不跟随 Symlink/Junction，只允许常规文件/目录，并限制 Entry 数量与目录深度，防止异常 Runtime 把清理变成无界遍历。
- 只由受审 Composition Root 注入私有 Capability；不能仅用一个可自行声明的 Boolean 冒充 Managed Session。
- 实现幂等 `finalize(handle)`，使提交前取消、提交失败、推理失败、验证失败和成功都能证明终态与清理完成。
- 用 Terminal Lock 串行化 Startup/Finalize/Close，避免 Close 返回后仍有子进程启动，或两个终止调用重复删除同一目录。

如果宿主进程硬崩溃或机器断电，Windows Job Object 会在宿主句柄关闭时终止子进程，但磁盘 UUID Root 仍可能存在。下一次 Preflight 发现任何残留或并发拥有者都会 Fail Closed，并保留现场，不会猜测归属后自动删除。未来恢复 UI 应先展示并显式处理这种残留。

### 10.2 已实现的持久 Gallery 合同

`ImageGalleryStore` 是独立于 Core 的单写者持久化边界。它使用 `ProductionDataLayout.gallery`，即 `<data-root>/workspace/gallery`；Renderer 和未来 Desktop DTO 只接触 `gallery_<uuid>`、路径无关元数据与按需重新验证后的 PNG Bytes。Store 只接受 Backend 内部已经完成 Core 生命周期的 `GeneratedImageResult`；它验证存储结构和图片一致性，但不是生成来源、Prompt 与 Workflow 真实性的加密证明。

每个 Artifact 的权威记录是同级 `image_<uuid>/` 目录中的 `image.png` 与严格 `manifest.json`。Create 先在准确 `.staging_<uuid>` 中写入并刷新两个文件，再以目录重命名跨过程序运行期间的逻辑提交点；Delete 先把权威目录重命名为 `.deleting_<uuid>` 使其不可见，再把 Tombstone 当作可延迟的安全清理。Windows 没有通过这一实现提供可移植的目录 `fsync`，因此这里的“提交”不宣称能够抵抗所有文件系统或断电故障。

新建 Gallery 会写入固定所有权标记。启动恢复必须先完整验证所有权标记、Root Grammar 和每个候选节点，确认没有未知条目后，第二阶段才会清理准确 `.staging_*`、`.deleting_*` 和 Index Temp；误选到已有宽目录时不会先删除碰巧同名的文件。Symlink、Junction/Reparse Point、Special File、额外 Hard Link 与读取期间身份变化均拒绝。Windows 生产根还必须位于固定本机的受支持文件系统，不能依赖网络盘或可移动盘的不同锁与重命名语义。

`index.json` 只是无 Prompt、无路径的可重建缓存，不决定 Artifact 是否存在；缺失、陈旧、损坏或无法写回时，不会阻止读取已经验证的权威 Artifact。打开 Store 时会验证严格 Manifest、目录结构、文件身份和声明大小，并建立独占 Lease 下的内存快照；`open_verified()` 才读取目标图片，重新验证 SHA-256 与 Canonical PNG。这样 List/Get/Create 不会随 Gallery 总图片字节数重复解码整个图库，目标图片的字节篡改仍会在发布前被拒绝。

同一 Store 由进程内 `RLock` 与非阻塞跨进程文件 Lease 串行化。默认上限为 1,024 个 Artifact、4 GiB 图片总量，单页最多 256 条；单图仍受 Canonical PNG 的 32 MiB 和像素预算约束。可选 Private Prompt 只进入私有 Manifest，不进入 `ImageGalleryItem` 或 Index。当前 Store 提供 Create/List/Get/Open Verified/Delete；Native Export、Desktop Preview、稳定游标分页与 Job Commit Coordination 尚未接入。同一用户账户中主动绕过 Lease 并并发替换目录节点不属于当前威胁模型；静态重定向和非协作文件结构仍会 Fail Closed。

---

## 11. GPU 与其他模型的协调边界

项目当前本机 GPU 还会被 Ollama、GPT-SoVITS、Song Cover/RVC/SoulX 使用。第一版图片 Core 明确**没有**实现全局 GPU Owner，因此在 Desktop 接线前不得声称可以安全地与这些任务并发。

后续 Desktop Module 必须建立进程级 Heavy-GPU Admission：

- 同一时间只允许经过政策批准的重型推理组合。
- 图片任务不能在 Song Cover 或其他独占 GPU 任务期间启动。
- 等待必须有用户可见状态、取消和上限。
- Lease Owner 必须绑定具体 Job，失败、取消、Backend 重启和窗口关闭都要收敛释放。
- 不能用“显存通常够用”代替正式并发合同。

---

## 12. 模型、版权与语音隔离

本仓库当前不分发：

- ComfyUI Runtime。
- SDXL/Pony/Illustrious Checkpoint。
- LoRA、ControlNet、IP-Adapter 或 VAE。
- 第三方 Custom Node。
- 用户生成图片或本机 Gallery。

`ModelProfile` 要求记录来源与许可证，但记录信息本身不会创造使用或再分发权。加入任何真实模型 Profile 前，必须独立确认模型许可、内容限制、商业/非商业条件和再分发边界，并决定它只能本机检测、允许下载指引，还是可进入安装包。

爱莉希雅 GPT-SoVITS 随附说明明确禁止 R18 使用。因此：

- 成人图片生成路径不得调用爱莉希雅 GPT-SoVITS、它的参考音频或任何衍生语音资产。
- R18 图片的状态、进度、成功或失败提示必须保持无声，或只使用与该模型完全无关且许可证允许的通用系统提示。
- Image Core 不导入 `voice/`，也不携带 Voice Profile/Emotion 字段。
- 未来 Desktop 接线仍须保持这条隔离，不能因为普通 Voice Call 已启用就自动朗读成人图片 Prompt 或结果。

这是一条工程和许可证隔离边界，不是对任何图片模型内容条款的替代。图片模型仍须按各自许可单独审核。

---

## 13. 自动化验证

### `tests/test_image_generation_core.py`

覆盖：

- Spec ID、Prompt 长度、控制字符、Seed 和 Intent 边界。
- 单一内容路径的原创虚构主体确认、普通/R18 描述共同接收、无数字年龄要求，以及明显未成年人/年龄模糊、真实人物、多主体和已有角色矛盾拒绝。
- Unicode NFKC/Casefold 安全 Shadow、`embedding:` 模型引用拒绝、Negative Prompt 排除语义及安全 Anchor 反向否定拒绝；词法检查仅作为非对抗性 Tripwire。
- Profile Checkpoint Key、Digest、Size、Sampler 与 Catalog 不变量。
- 不同 Prompt Dialect 的固定 Prefix。
- 固定单图 SDXL Graph、编译 Capability Seal、Canonical JSON、字段/节点/连接篡改拒绝与 Graph Copy Isolation。
- PNG CRC、Chunk、Filter、Trailing Bytes、尺寸绑定、惰性 Metadata 清除和渲染语义 Ancillary Chunk 拒绝。
- Managed Runtime Boolean + Capability Identity Gate、Prepared Handle/Output 类型关联、Composition Root 二次 PNG Canonicalization、强制 Finalizer、Core 进度单调、取消 Commit Boundary 与 Callback Failure 隔离。

### `tests/test_comfyui_client.py`

使用离线 Fake Loopback HTTP Server 覆盖：

- Origin 规范化与远程/凭证/Path 拒绝。
- Node/Checkpoint Probe、预生成 Prompt/Client Identity、固定 Graph 提交、Prompt/Client/Output Prefix 的精确 History 关联、明确成功终态与 PNG Fetch。
- `/system_stats` Readiness 合同、敏感 `argv` 丢弃和畸形响应拒绝。
- HTTP Body 绝对 Deadline、Job 总 Deadline、慢速滴流与大小上限。
- Redirect 拒绝和外来 Prompt Record 拒绝。
- Pending 精确删除。
- Shared Instance 不调用 Global Interrupt。
- 重复/跨队列 Prompt Ownership 拒绝；Exclusive Instance 只向自己的唯一 Running Prompt 发送 Targeted Interrupt。
- 输出文件名、子目录和扩展名的 Traversal/Alias 拒绝。

### `tests/test_managed_comfyui.py`

使用封闭依赖注入而不启动 GPU 进程，覆盖：

- 无副作用文件 Preflight、固定本机卷、Hash、稳定 Guard Path 和额外模型根拒绝。
- 完整安全启动命令、闭合环境、私有目录和准确 Loopback PID 所有权。
- 启动失败、早退、错误 PID、Readiness 超时、稳定配置失败和无效 Handle 的逐层回滚。
- 成功生命周期、提交/Handle/Workflow/Output 关联、任意 Cancel 异常后的权威进程终止。
- Finalize/Close 竞态串行化、幂等关闭、清理失败重试、旧 PID 重用和崩溃残留 Fail-Closed。

### 私有目录与端口测试

`tests/test_private_run_directory.py` 验证准确 UUID Root、Sibling Preservation、Redirect/Reparse/Special Node、Entry/Depth Budget；`tests/test_windows_loopback_owner.py` 验证 Windows IPv4 Loopback Listener 必须同时匹配地址、端口和 Child PID。

### `tests/test_image_gallery_store.py`

覆盖 Opaque/path-free CRUD、Newest-first 分页、所有权标记、未拥有宽目录零删除、缺失/陈旧/损坏/不可写 Index 的读取恢复、部分 Staging/Tombstone 崩溃恢复、未知或畸形状态 Fail Closed、目标 PNG/Manifest 篡改、Symlink/Reparse/Hard-link/Special File、数量/字节/并发容量、Private Prompt 边界、跨进程 Lease、关闭语义、畸形 Result Metadata 拒绝、提交后同步/清理失败语义、Rollback Sibling Preservation 和完整异常链脱敏。测试还固定 List/Get/Create 不会重新读取或 Canonicalize 已有图片；完整 PNG 校验只发生在目标 `open_verified()`。

这些测试不安装 ComfyUI、不加载真实 Checkpoint，也不使用 GPU。真实 Runtime、具体模型画质、VRAM 峰值和长时间取消仍需要单独的显式本机 Smoke/Soak 验收。

---

## 14. 下一步

### 第一优先：Desktop Protocol、Settings 与 Gallery Integration/Export

- 定义路径无关的 Image Request/Progress/Result/Error DTO。
- 在 Backend 组合受信 Profile Catalog 与 Managed Runtime Session，不直接组合 Raw `HttpComfyUiClient`。
- 加入 Desktop Settings：Runtime/Models/Private State Folder、已审核 Profile 与默认尺寸；不提供内容模式开关。
- 在 Desktop Application Service 组合 Core 与 `ImageGalleryStore`，定义生成/提交 Commit Gate，并实现路径私有 Preview、Native Export 与容量展示。
- Renderer 只获得 Opaque Artifact ID、Metadata 和受限预览能力，不获得本机模型/输出路径。

### 第二优先：真实 Runtime 与模型验收

- 在明确版本的标准 ComfyUI Source Checkout 上验证安全启动参数、`/system_stats`、Built-in Nodes、`SaveImage`、退出与重启行为。
- 对实际 Checkpoint 记录来源、许可证、Hash、Prompt Dialect、画质、VRAM 峰值和单图时间。
- 为受管 ComfyUI/Python 依赖树增加完整 Manifest 或签名验证；当前只固定 Python、`main.py` 与 Checkpoint，不能夸大为整个安装均已认证。
- 加入崩溃残留的用户可见恢复流程；所有自动删除仍必须有可证明的精确归属。

### 第三优先：GPU Admission 与恢复

- 和 Ollama、Voice、Song Cover 共用一个明确的 Heavy-GPU Lease Policy。
- 加入 Job Queue、Owner、Timeout、Cancel、Late-output Discard 和 Backend Restart Recovery。
- 在 RTX 4070 SUPER 12 GiB 等真实目标硬件上记录单图峰值与互斥组合。

### 后续受信工作流

在单图核心、Gallery 和 GPU Lifecycle 稳定后，再分别设计：

- OpenPose + Matching Architecture ControlNet。
- Reference/Identity Adapter。
- Inpaint/Mask 与换装编辑。
- Optional FaceDetailer。
- 同一原创角色 Profile 与可复现变体。

每一类都需要独立的结构化合同、模型兼容矩阵、文件授权、安全验证、输出 Provenance 和回归测试，不能通过开放任意 ComfyUI Workflow JSON 一次性绕过。
