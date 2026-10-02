# Elysia 2D complete review pack v3

本目录是 2026-10-01 根据项目所有者的十条逐图复审制作并继续逐格修正的最终审阅包。反馈编号严格对应 01 至 10。内容整合完成后，项目所有者要求删除旧的 v1 与 v2 目录；本目录现为唯一保留版本。

这些文件是角色视觉和产品界面的最终静态审阅母版。应用内角色状态板固定使用审核后的静态图集，不再加载项目自制 Live2D。桌面宠物的动画模型由用户从本机外部六变体合集选择；该合集不会复制进此目录、Git 或安装包。`07-desktop-pet-key-poses.png` 仍只是动作审阅总览，不是运行时 Sprite Sheet。

## 十条反馈与 v3 处理结果

| 编号 | 文件 | v3 处理结果 |
| ---: | --- | --- |
| 1 | `01-character-turnaround.png` | 用户确认可接受；最终文件保留在 v3，原始生成记录也已迁入本目录。 |
| 2 | `02-activity-states.png` | 对第 2、4、6 格分别进行单格修复：第 2、4 格裸右手各改为一拇指加四指；第 6 格裸右手与黑手套左手完全分开，中间保留浅色背景间隙。其余五格未重画。 |
| 3 | `03-expression-atlas.png` | 用户确认可接受；最终文件保留在 v3，原始生成记录也已迁入本目录。 |
| 4 | `04-facial-rig-atlas.png` | 将拥挤的九种嘴型拆成两带，整张由三带改为四带；随后只替换第二带第 4 格的错误嘴部，使 U 嘴成为鼻下居中、左右对称的小型圆润开口。 |
| 5 | `05-chibi-stickers.png` | 25 个动作按 5 个高分辨率行条带分别生成。额外逐格修复 `05b` 第 2 格与 `05c` 第 2 格的六指手，并对全部 25 格执行只读复核。 |
| 6 | `06-ui-illustrations.png` | 重绘并审计全部 16 格。第 13 格铃铛由正确的裸右手持握，并连接黑金袖与紫色荷叶袖口；同时修正第 11 格双黑手套和第 12 格错误裸手指向。 |
| 7 | `07-desktop-pet-key-poses.png` | 逐格修复 `07a` 第 2、3 格的右手拇指勾线，`07b` 第 1 格与 `07c` 第 1 格的六指，以及 `07c` 第 4 格糊成一团的黑手套；随后复核全部 16 格。 |
| 8 | `08-layer-separation-guide.png` | 重新生成部件表，再以固定阈值将 alpha 转为严格的 0/255 二值透明；透明区 RGB 清零。白底和近黑底检查均无半透明白边。 |
| 9 | `09-color-and-detail-master.png` | 用户确认可接受；最终文件保留在 v3，哈希未发生变化。 |
| 10 | 已移除 | 该文件只是六个界面的概念视觉稿，未被桌面程序、后端或打包流程引用。项目所有者确认不需要后，从 v3 删除；包含该图的旧 v1/v2 目录也已删除。 |

## 文件清单

| 文件 | 内容 | 尺寸 | 格式 | SHA-256 |
| --- | --- | ---: | --- | --- |
| `01-character-turnaround.png` | 七视图角色设定 | 1536×1024 | RGB | `68D87AAB61382053A82FBDCCDC8FC5D0348FA90539F5FE0E633426BEE12DA14A` |
| `02-activity-states.png` | 4×2 运行状态 | 1536×1024 | RGB | `54EB2525673C2A849819BE10EB88EB2F670EB1911E86FD154E69B578CBB4C25C` |
| `03-expression-atlas.png` | 5×4 面部表情 | 1402×1122 | RGB | `FBF7A515B2651B3A881CF9B838A5605C316BEFD0B174DDE046780D8E441D7F93` |
| `04-facial-rig-atlas.png` | 四带、19 格面部绑定参考 | 1536×1024 | RGB | `21BF4496ACC4417D491FF0163C9EE1D38593E376CA25A3D452FD393C6157F9AB` |
| `05-chibi-stickers.png` | 25 个 Q 版对话表情总览 | 2172×3620 | RGB | `427B354210CBC663043E4134A1400601538813E9E82F4E8089372D7AFD00D70F` |
| `05a-row1.png` | 05 第 1 行原尺寸条带 | 2172×724 | RGB | `9FDC5AD23704A300CD00BAC6BB5DA7032D57D4F05E995461B9C6C81B91EA7594` |
| `05b-row2.png` | 05 第 2 行原尺寸条带 | 2172×724 | RGB | `85F71BA67D2044CD269BDD1AA4A81C8940F4B2D45638125E1BE0077ADCBCE274` |
| `05c-row3.png` | 05 第 3 行原尺寸条带 | 2172×724 | RGB | `15856D83EF7934F896A107F8D8C9753A76CF23627A3ADA0B7197D3D526062EDE` |
| `05d-row4.png` | 05 第 4 行原尺寸条带 | 2172×724 | RGB | `A9C6012F41DD72EDF086D205DF78D463F7A5A9C0944483FAD7F2FD65A368F2C6` |
| `05e-row5.png` | 05 第 5 行原尺寸条带 | 2172×724 | RGB | `6DBA04FC19544E209E411372C790ECC11B4CDA2F9CD90CDBD9AD5E747B902A13` |
| `06-ui-illustrations.png` | 4×4 产品状态插图 | 1254×1254 | RGB | `E375B7E6FC0B3CEA898C25A34381014A07CF334794FF7D6C0EA2D82F9707FA25` |
| `07-desktop-pet-key-poses.png` | 16 个桌宠关键姿势总览 | 2048×2813 | RGB | `49D29C4D2AA95C54D42257ADE99B95A4E03ECA9D621F333FCA06FAD8CAE3FD0D` |
| `07a-row1.png` | 07 第 1 行原尺寸条带 | 2167×725 | RGB | `9C9D0145FCDE0B3813CFCFAD67447904F9E2FCF05D6C15455C12DA2BBF4E0926` |
| `07b-row2.png` | 07 第 2 行原尺寸条带 | 2182×721 | RGB | `62D9DA8EA747F9FBFBC668B86B142EBF2655D0ABC94D1D4AD0CF111CD9D13D6F` |
| `07c-row3.png` | 07 第 3 行原尺寸条带 | 2048×768 | RGB | `5434BB923A4448652F607664E72668259779C8318DF7218BDC3A6410FF6BCC34` |
| `07d-row4.png` | 07 第 4 行原尺寸条带 | 2172×724 | RGB | `5694BFD1A581E7BD5263ED704504CC13BECE49E683FA1FC83AE42D5B0EEFD8BD` |
| `08-layer-separation-guide.png` | 二值透明部件分层视觉指南 | 1536×1024 | RGBA | `8FBD3849352E04F8BA6A77E5F7321A577694B3AE6D0585CB675699CBBC47F568` |
| `09-color-and-detail-master.png` | 正背角色、细节与色板 | 1536×1024 | RGB | `759F0CE419955B137F414E69DEFEC43CDF9D200DEE223F3A0E9F260006ED8641` |

## 全套固定规则

- 每个完整可见手必须是五指：一个拇指、四个其余手指；不得出现三指、四指、六指、重复指尖、融合指或反向拇指。
- 握笔、持书、持铃铛、托腮和比心等姿势允许自然遮挡，但手掌、拇指位置、可见指节和道具受力关系必须能够解释为正常五指结构。
- 角色解剖学右臂是黑金长袖、紫色荷叶袖口和裸右手；正面看时位于画面左侧。
- 角色解剖学左臂是白金前臂护甲和黑手套左手；正面看时位于画面右侧。
- 裸手肤色不得进入黑手套，黑色手套不得覆盖裸右手；两手接近时仍需保留清楚的轮廓和材质边界。
- 黑手套靠近粉色头发时必须有连续深色外轮廓，优先留出浅背景负空间，避免手和头发融合。
- 普通状态不脸红；只有明确的 shy 可明显脸红，少数明确情绪最多保留极轻自然暖色。
- 头发使用低饱和浅粉；后方长发由黑白蝴蝶结收束，不画成互不相连的散乱发束。
- 面部鼻子、嘴和下巴保持中轴；非眨眼等明确动作时，两眼保持同高。

## 固定行优先映射

### 02 — 运行状态

1. Idle
2. Listening
3. Thinking
4. Speaking
5. Working/focused
6. Waiting for approval
7. Error/concerned
8. Success/celebration

### 03 — 面部表情

1. Soft smile
2. Attentive
3. Focused
4. Speaking smile
5. Patient serious
6. Concerned
7. Happy
8. Gentle sad
9. Laugh
10. Wink
11. Playful
12. Shy blush
13. Surprised
14. Confused
15. Proud
16. Pout
17. Sleepy
18. Tender comfort
19. Solemn
20. Light tears

### 04 — 面部绑定参考

- 第 1 带：closed、small、medium、wide、A，共 5 格。
- 第 2 带：E、I、O、U，共 4 格。
- 第 3 带：open、half-open、closed、happy crescent、wide、worried，共 6 格。
- 第 4 带：relaxed、focused、raised、concerned，共 4 格。

### 05 — Q 版对话表情

1. Hello wave
2. Welcome back
3. Good morning
4. Good night
5. Goodbye
6. Smile
7. Laugh
8. Wink
9. Heart
10. Shy
11. Praise
12. Celebrate
13. Thank you
14. Cheer up
15. Thinking
16. Confused/question
17. Surprised
18. Concerned
19. Comforting hug gesture
20. Sad tear
21. Pout
22. Sorry
23. Busy working
24. Please review
25. Warning/error

### 06 — 产品 UI 插图

1. Assistant avatar
2. Chat welcome
3. Backend waiting
4. Disconnected
5. No chats
6. No projects
7. No memory
8. No sources
9. Indexing/reading
10. No search results
11. Microphone unavailable
12. Fatal error/reload
13. Reminder
14. Approval required
15. Task success
16. Setup complete

### 07 — 桌宠关键姿势

1. Spawn wave
2. Idle breathing
3. Blink
4. Cursor look
5. Walk/float
6. Sit
7. Sleep
8. Click reaction
9. Dragged
10. Drag release/landing
11. Open chat invitation
12. Notification attention
13. Listening
14. Thinking
15. Speaking
16. Working

## 05 与 07 为什么保留行条带

直接把 25 或 16 个小人物一次生成到方形画布，会显著压缩手部像素并增加手指错误。v3 将每一行单独生成和审阅，保留原尺寸条带作为后续选片、修手、切图和替换的可靠母版。总览文件只是把这些最终条带纵向缩放并拼接，方便一次查看整个动作集合。

## 08 的透明边缘处理

ImageGen 输出仍包含 255 个 alpha 等级，透明区附近存在视觉上发灰的半透明像素，因此最终稿使用确定性后处理：

1. 对生成稿应用 `alpha >= 192 -> 255`，其余 alpha 设为 `0`。
2. 对 alpha 为 0 的像素将 RGB 同时清零，避免隐藏的白色或粉色污染后续合成。
3. 分别合成到纯白和近黑背景，检查角色、头发、服装和小配件边缘。

最终文件的 alpha 只有 0 和 255 两种值；保留的不透明面积约为 44.97%。它只是身体、服装和头发的部件边界审阅参考，不是可绑定 PSD，也不再用于生成项目内置 Live2D 模型。

## 应用内运行时选片

应用内角色状态板始终静态，不再提供 Animated/Still 切换。以下三张审核母版仍以逐字节副本进入 Desktop Public Assets；运行时不重新编码、不执行 AI 重绘，也不把整套审阅包复制进安装包。桌宠动画与这些状态图分离，由外部本机 Live2D 模型负责。

| 审阅母版 | 运行时副本 | 字节数 | SHA-256 | 运行时用途 |
| --- | --- | ---: | --- | --- |
| `02-activity-states.png` | `desktop/public/character/elysia-state-atlas.png` | 2,303,963 | `54EB2525673C2A849819BE10EB88EB2F670EB1911E86FD154E69B578CBB4C25C` | 七个封闭 Character State |
| `03-expression-atlas.png` | `desktop/public/character/elysia-expression-atlas.png` | 2,500,647 | `FBF7A515B2651B3A881CF9B838A5605C316BEFD0B174DDE046780D8E441D7F93` | 用户限定的三种静态情绪 |
| `04-facial-rig-atlas.png` | `desktop/public/character/elysia-speech-atlas.png` | 2,054,767 | `21BF4496ACC4417D491FF0163C9EE1D38593E376CA25A3D452FD393C6157F9AB` | 静态嘴型审阅与安全回退 |

分发门禁同时固定每个运行时副本的仓库路径、长度、SHA-256、ASAR 精确路径、唯一条目数与抽取字节，避免未审核替换继承本次结论。

### 02 — Character State 运行时映射

运行时按固定 4×2 网格裁切，单格逻辑尺寸为 384×512，映射如下：

| Character State | 列 | 行 | 视觉含义 |
| --- | ---: | ---: | --- |
| `idle` | 0 | 0 | Soft smile / resting |
| `listening` | 1 | 0 | Attentive / listening |
| `thinking` | 2 | 0 | Focused / thinking |
| `speaking` | 3 | 0 | Speaking smile / speaking |
| `working` | 0 | 1 | Focused / working |
| `waiting_approval` | 1 | 1 | Patient / waiting |
| `error` | 2 | 1 | Concerned / alert |

第 8 格（列 3、行 1）的 Success/celebration 不属于当前封闭 Character State，运行时明确不使用。图集由 `character-presentation.ts` 的穷尽映射选择；Chat、Voice、Knowledge 与未来 Work/Approval producer 只能提供语义状态，不能提供路径、格号或任意动画名称。

### 03 — 用户限定静态表情

Settings 的 Voice Emotion 只接受 `neutral`、`happy` 与 `sad`。Backend 成功重启后，同一个 Active 值既选择本地 GPT-SoVITS 参考，也选择审核静态表情；模型回复没有情绪、文件路径、图集格号或动画名称字段，不能覆盖用户选择。运行时映射为：

| Voice Emotion | 列 | 行 | 审核表情 |
| --- | ---: | ---: | --- |
| `neutral` | 0 | 0 | Soft smile |
| `happy` | 1 | 1 | Happy |
| `sad` | 2 | 1 | Gentle sad |

### 04 — 静态嘴型审阅参考

04 保留已审核的嘴、眼和眉形状，供静态状态与错误回退复核。应用内角色状态板不会根据音频振幅驱动它，也不会把 A/E/I/O/U 描述成实时音素绑定。桌宠的嘴部动画属于用户所选外部 Live2D 模型自身的参数和动作能力，与本图集互不替换。

### 可选 Live2D Desktop Pet

独立桌宠**没有**把 `07-desktop-pet-key-poses.png` 或其四条 row strip 当作运行时 Sprite Sheet。它直接读取用户从六套本机外部 Live2D 中选择的一套，并保留 `desktop/public/character/elysia-portrait.png` 作为不可用与错误回退；外部文件不会被复制、缓存或重新打包。

外部合集来源标注为 `@书呆儿`，记录的公开下载页为 <https://pan.quark.cn/s/cb5d84acad8e>。链接内容及条款尚未独立核验，运行时只按当前本机扫描结果显示实际可用的模型数量，不能假定链接或六套文件永远存在。

### 回退与功能隔离

应用内角色状态板始终按 `expression → state → portrait → accessible text` 使用静态回退。桌宠单独异步加载所选外部 Live2D；目录缺失、扫描失败、模型不兼容或 WebGL 失败时只让桌宠进入静态回退，不会阻止 Chat、Voice、Knowledge 或声音播放。

## 审计结论与仍需注意的地方

- 所有完整展开的可见手都能辨认一个拇指和四个手指；未发现明确的三指、四指、六指、融合掌或左右装备互换。
- 道具握持、托腮、比心和睡姿中会存在自然遮挡，不能要求五个指尖同时形成互不重叠的剪影；结构按五指关系检查。
- 04 的 worried 眼型格出现轻微下弯嘴，而非与前五格完全相同的中性嘴；部分眉型因刘海遮挡，视觉差异较细。
- 04 第一带头饰顶部安全距离偏小，但没有被裁切或碰到边界。
- 02 第 6 格两手已完全分开，中间保留浅色背景间隙。
- 08 原图只保留为视觉切层指南；旧的自制模型、脸部母版、统一画布源层和生成脚本已删除。
- 分发门禁只固定兼容 Core 与静态回退的路径、长度和 SHA-256，并明确禁止任何项目内置 Live2D 模型或命名的外部付费素材目录进入 Git/ASAR。

## 参考来源

角色角度与服装结构主要参考项目所有者提供的七张游戏截图：

- `D:\Honkai Impact 3rd game\ScreenShot\2026-09-30-23-05-30_0.png`
- `D:\Honkai Impact 3rd game\ScreenShot\2026-09-30-23-05-19_0.png`
- `D:\Honkai Impact 3rd game\ScreenShot\2026-09-30-23-05-13_0.png`
- `D:\Honkai Impact 3rd game\ScreenShot\2026-09-30-23-05-07_0.png`
- `D:\Honkai Impact 3rd game\ScreenShot\2026-09-30-23-05-00_0.png`
- `D:\Honkai Impact 3rd game\ScreenShot\2026-09-30-23-04-50_0.png`
- `D:\Honkai Impact 3rd game\ScreenShot\2026-09-30-23-04-39_0.png`

脸部、二维渲染和 Q 版风格参考：

- `D:\IMG_0022..WEBP`
- `D:\IMG_2363..JPG`
- `D:\IMG_2444(20260920-110857)..PNG`
- `D:\BaiduNetdiskDownload\爱莉希雅\RogueGod_Elysia.png`
- `D:\Elysia_AI\desktop\public\character\elysia-portrait.png`

参考图没有复制到本目录。生成和修复规范完整记录在 `PROMPTS.md`，包括 01、03、09 从旧版本迁入的记录；删除 v1/v2 后不再存在外部文档依赖。
