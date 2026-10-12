# 本地图片生成核心

最后更新：2026-10-12（America/Chicago）

## 1. 当前状态

Elysia AI 已加入第一版**受信本地图片生成编译与验证核心**。这一版建立的是 Python 领域合同、模型配置、固定工作流编译、底层 Loopback ComfyUI 协议适配和输出验证，并不等同于已经完成可执行的桌面产品功能。

当前已完成：

- 用 `ImageGenerationSpec` 表达一张新图片的结构化需求。
- 用受信 `ModelProfile` 固定模型家族、Checkpoint 声明、Prompt 方言、尺寸和采样参数。
- 将结构化需求编译成源码内固定的 SDXL `txt2img` ComfyUI API Graph。
- 提供一个只连接明确本机 Loopback ComfyUI HTTP Origin 的底层 `HttpComfyUiClient`；它明确声明 `is_managed_private_runtime=False`，不从 Package Root 导出，也不能直接注入 `ImageGenerationCore`。
- 在发起网络提交前预生成 `prompt_id`、`client_id` 与输出 Prefix，按三者精确关联提交、History 和输出，有界轮询结果并保守处理取消。
- 验证 PNG Chunk、CRC、像素格式、尺寸和解压上限；只移除惰性 Metadata Chunk，遇到会改变像素解释的 Ancillary Chunk 则拒绝，再重写 Canonical PNG。
- 返回图片字节、模型 Profile Digest、Workflow Digest、Seed 与 UTC 时间等可复现 Metadata。

当前尚未完成：

- Elysia 管理的私有 ComfyUI Runtime：尚无组件拥有子进程 PID、隔离目录、可靠终止等待和残留清理，因此实际生成，包括成人内容生成，当前一律 Fail Closed。
- Electron/React 的 Image Generator、Settings、进度、取消和错误界面。
- Generated Image Gallery、持久化、导出、删除、容量统计和恢复。
- 与 Ollama、GPT-SoVITS、Song Cover 共用的进程级 GPU Admission/Lease。
- 模型安装、下载、更新、Hash 实测和许可证向导。
- Reference Image、同一角色连续生成、OpenPose、ControlNet、IP-Adapter、Inpaint 或 FaceDetailer。

因此，这个核心目前应被理解为下一层私有 Runtime 与 Desktop Integration 的受信基础，而不是普通用户已经可以从界面直接使用的完整能力。普通共享 ComfyUI 的 `SaveImage` 会把原图写进它的 Output Directory，`/history` 还会保留 Workflow 与 Prompt；仅清理下载后的 PNG 不能消除这些副本。为了不把私密 Prompt 或成人图片遗留在未知 Runtime 中，生产执行必须等到受管私有 Runtime 完成后才可启用。

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
Elysia-managed Private ComfyUI Runtime（尚未实现）
        │
        ├── Owned Child PID / Isolated Directories
        ├── Metadata / Custom Nodes / API Nodes Disabled
        └── Terminal Wait + History/File Cleanup
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
未来 Trusted Gallery Store
```

核心原则：

1. **LLM 只产生结构化意图。** 它不能产生任意 ComfyUI Graph、节点类、模型路径、输出路径或脚本。
2. **Profile 是应用配置。** 用户请求只能选择逻辑 `profile_id` 和 `size_preset_id`，不能把文件名伪装成模型选择。
3. **Workflow 由源码控制。** 第一版 Graph 的节点和连接不可由 Renderer 或 Prompt 改写。
4. **Runtime 输出不可信。** 即使来自 `127.0.0.1`，HTTP、JSON、队列记录、输出名和 PNG 仍须完整验证。
5. **共享 Runtime 不得执行。** `ImageGenerationCore` 同时要求 `is_managed_private_runtime is True` 和 Composition Root 私有签发的精确 Capability Identity；Raw Adapter 固定返回 `False` 且没有该 Capability，所以当前生产执行 Fail Closed。Capability 只防止项目内部误组合，不声称能隔离恶意 Python 代码。
6. **验证和清理完成前不发布。** 只有未来受管会话确认 Native Job 已进入终态、History/源文件清理完成、PNG Canonicalization 成功且最后一次取消检查未获胜，图片字节才可进入后续 Gallery Store。

---

## 3. 内容与身份边界

### 3.1 仅限虚构成年人

所有内容模式都只允许**原创虚构且明确为成年人的角色**。第一版不要求用户为虚构角色填写一个具体数字年龄；数字本身也不能证明成年。系统要求的是：

- 所有请求都显式设置 `original_fictional_confirmed=true`，确认主体是原创虚构人物而非真实人物或已有角色；固定 Prompt 另外把主体限定为成年人。
- 成人内容另外显式选择 `adult` 内容模式并设置 `adult_confirmed=true`。
- 普通模式的正向字段若出现明显露骨词，会被拒绝并要求走成人 Opt-in；Negative Prompt 中用于排除某内容的词不会因此误触发。
- 所有模式的正向字段都保守拒绝明显的未成年人/年龄模糊、真实人物、多个主体和已有角色矛盾。
- 正负 Prompt 都禁止 ComfyUI `embedding:` 文本嵌入语法，防止自由文本选择 Profile 之外的本机模型资产。
- Negative Prompt 不得否定 `adult`、`fictional`、`original character` 或单主体等强制边界；它只能追加画质或内容排除条件。

这些 Unicode 规范化后的词法规则只是面向正常输入的**非对抗性 Tripwire**，用于尽早发现明显矛盾；它们不是身份、年龄或内容分类的证明，也不承诺抵抗刻意规避。后续 Desktop 层仍必须保留明确 Opt-in、清楚的原创虚构成年人确认和适当的错误说明，不能因为 Python Domain 已校验就把 UI 变成无提示开关。

### 3.2 第一版没有身份参考入口

当前 `ImageIntent` 只有 `new-character`，并且 Prompt 固定加入“original fictional adult character”。第一版不接收：

- 真实人物或真实人物相似度请求。
- 参考照片、脸部嵌入或身份 Adapter。
- 已存在角色的身份锁定。
- 图片编辑、换装、局部重绘或姿势参考。
- 多主体或批量出图。

未来若加入 Reference/Image Edit，必须增加独立的受信字段、文件授权、来源和身份政策；不得把图片路径、Base64、Node JSON 或隐藏命令塞入现有 Prompt 文本绕过合同。

### 3.3 普通与成人模式

`general` 与 `adult` 是两个显式闭集值。两者都要求 `original_fictional_confirmed=true`；`adult_confirmed` 只允许和 `adult` 同时出现，普通模式不能携带多余的成人确认状态。普通模式若含明显露骨的正向内容会被拒绝并指向成人 Opt-in；成人模式则额外要求 `adult_confirmed=true`。未成年人/年龄模糊、真实人物、多个主体与已有角色的明显矛盾在两个模式中都会拒绝。

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
| `content_mode` | 普通或成人 | `general` / `adult` 闭集 |
| `original_fictional_confirmed` | 原创虚构主体确认 | 所有模式都必须为 `true` |
| `adult_confirmed` | 虚构成年人确认 | 仅成人模式可为 `true` |
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

Profile Digest 覆盖架构、Prompt 方言、Checkpoint 声明、来源、许可证、尺寸与采样默认值。后续 Gallery 应把该 Digest 与图片一同保存；只保存显示名不足以重现生成条件。

### 当前 Hash 限制

ComfyUI 的 Raw Model List 只返回模型 Key，不返回本机 Checkpoint 实际文件摘要。当前 Adapter 会确认准确 Key 已安装，但**不能**通过 HTTP 证明 Runtime 实际加载文件等于 Profile 中声明的 SHA-256。

所以第一版的 `checkpoint_sha256` 是管理员审查与 Artifact Provenance 声明，不是运行时文件证明。若以后由 Elysia 管理 ComfyUI 进程，必须在启动前从可信文件边界验证实际模型，再把经过验证的 Runtime 交给 Adapter。

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

但 `SaveImage` 仍会先把源 PNG 持久化到 ComfyUI 的 Output Directory，ComfyUI `/history` 也会保存完整 Graph，通常包括正负 Prompt。Canonicalizer 只清理取回的副本，不能清理这些 Runtime 侧 Artifact。正因为这一点，Raw Adapter 不是安全的生产会话；未来受管 Runtime 必须使用隔离 Output/Temp/User/Input Directory、禁用 Metadata，并在允许结果发布前清理精确 History 与文件。

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

提交前，Adapter 使用 `/object_info/{node}` 检查六类必需 Built-in Node，并用 `/models/checkpoints` 确认 Profile 声明的准确 Checkpoint Key 存在。它不会自动安装缺失节点、下载模型或选择名字相近的替代品。

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

`ImageGenerationCore.generate()` 定义同步、单次、无 Gallery 持久化的可信用例。它的构造函数只接受同时声明 `is_managed_private_runtime is True` 并持有 Composition Root 私有 Capability Identity 的会话；底层 `HttpComfyUiClient` 固定为 `False` 且没有该 Capability，因此当前仓库没有可用于生产执行的具体组合，调用会在提交前 Fail Closed。测试中的 Managed Fake 只验证合同，不代表已经实现真实 Runtime Supervisor。

未来受管会话接通后，进度闭集为：

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
- 启动、拥有和回收 ComfyUI 子进程及其私有目录。

这些职责必须由后续可信 Integration Layer 添加，不能偷偷塞进同步 Core；在它们完成之前不得绕过 `is_managed_private_runtime` Gate 来启用实际生成。

### 10.1 下一层受管 Runtime 的最低合同

首个后续实现必须：

- 启动并持有唯一 Child PID，使用随机 Loopback Port；绝不接管或终止用户自行启动的共享 ComfyUI。
- 为每个受管实例设置 Elysia 拥有的隔离 Input、Output、Temp 与 User Directory。
- 使用 `--disable-metadata`、`--disable-all-custom-nodes`、`--disable-api-nodes` 与 `--disable-auto-launch` 收窄执行面。
- 在提交前持久记录预生成的 Prompt/Client Identity；取消和超时后继续收敛到 Native Terminal State。
- 精确清理本 Job 的 History 与源/临时文件，并在清理无法确认时禁止发布结果。
- API 失联时只终止所拥有的 Child PID，等待退出后再清理私有目录。
- 只由受审 Composition Root 注入私有 Capability；不能仅用一个可自行声明的 Boolean 冒充 Managed Session。
- 实现幂等 `finalize(handle)`，使提交前取消、提交失败、推理失败、验证失败和成功都能证明终态与清理完成。

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
- 成人模式状态、进度、成功或失败提示必须保持无声，或只使用与该模型完全无关且许可证允许的通用系统提示。
- Image Core 不导入 `voice/`，也不携带 Voice Profile/Emotion 字段。
- 未来 Desktop 接线仍须保持这条隔离，不能因为普通 Voice Call 已启用就自动朗读成人图片 Prompt 或结果。

这是一条工程和许可证隔离边界，不是对任何图片模型内容条款的替代。图片模型仍须按各自许可单独审核。

---

## 13. 自动化验证

### `tests/test_image_generation_core.py`

覆盖：

- Spec ID、Prompt 长度、控制字符、Seed 和 Intent 边界。
- 所有模式的原创虚构主体确认、成人额外 Opt-in、无数字年龄要求、普通模式露骨内容分流，以及明显未成年人/年龄模糊、真实人物、多主体和已有角色矛盾拒绝。
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
- HTTP Body 绝对 Deadline、Job 总 Deadline、慢速滴流与大小上限。
- Redirect 拒绝和外来 Prompt Record 拒绝。
- Pending 精确删除。
- Shared Instance 不调用 Global Interrupt。
- 重复/跨队列 Prompt Ownership 拒绝；Exclusive Instance 只向自己的唯一 Running Prompt 发送 Targeted Interrupt。
- 输出文件名、子目录和扩展名的 Traversal/Alias 拒绝。

这些测试不安装 ComfyUI、不加载真实 Checkpoint，也不使用 GPU。真实 Runtime、具体模型画质、VRAM 峰值和长时间取消仍需要单独的显式本机 Smoke/Soak 验收。

---

## 14. 下一步

### 第一优先：Elysia-managed Private ComfyUI Runtime

- 实现上一节列出的 Child PID、随机 Loopback Port、隔离 Input/Output/Temp/User Directory 与禁用开关。
- 把底层 Raw Adapter 包在拥有完整 Cleanup/Terminal-Wait 合同的 Managed Session 中，并只在受审 Composition Root 为该 Session 注入私有 Capability；Boolean 本身不构成准入。
- 对成功、失败、取消、超时、丢失 POST Response、Backend Crash 和 API 失联分别验证 History/File Cleanup；清理不确定即不发布。
- 在真实 Runtime 上确认 `SaveImage`、`/history`、退出和重启后的残留边界。

### 第二优先：Desktop Protocol、Settings 与 Gallery

- 定义路径无关的 Image Request/Progress/Result/Error DTO。
- 在 Backend 组合受信 Profile Catalog 与 Managed Runtime Session，不直接组合 Raw `HttpComfyUiClient`。
- 加入 Desktop Settings：Runtime Address、已审核 Profile、默认尺寸、成人模式默认关闭。
- 实现持久化 Gallery Manifest、Canonical PNG 原子写入、导出、删除、容量和恢复。
- Renderer 只获得 Opaque Artifact ID、Metadata 和受限预览能力，不获得本机模型/输出路径。

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
