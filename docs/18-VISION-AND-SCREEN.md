# 图片与屏幕理解

从输入栏 **Add Files** 添加图片，点击附件的 **Review image** 打开 Vision；右侧截图图标打开单次截图弹窗。导入不会发送消息、加载模型或执行 Work 动作。关闭弹窗只隐藏界面，原生任务实际结束前仍保留取消入口和操作占用。

## 图片输入

支持 PNG、JPEG。Backend 从当前 Chat 的已验证附件读取有界快照，经内部字节协议交给 Main；Renderer 只提交附件 ID。Main 先校验格式和画布，再解码为去除元数据的 PNG。JPEG 的 EXIF 方向由导入层应用到像素，保留 PNG 必要透明度；动画 PNG、带 EXIF 的 PNG 及不支持的像素格式拒绝。

| 项目 | 上限 |
| --- | --- |
| 源文件 | 8 MiB |
| 画布 | 每边 8192 像素，总计 16 × 1024 × 1024 像素 |
| 规范化 PNG | 单张 16 MiB；集合最多 8 张、64 MiB |
| 界面预览 | 每边 512 像素、PNG 最多 1 MiB |

读取拒绝链接、硬链接及文件身份变化。路径、EXIF、原生句柄与原文件不进入界面。导入、解码和结果发布绑定当前 Chat、Project 和模式；旧范围结果丢弃，原生回调结束前仍占用与 Chat、Work、语音和数据迁移共用的容量。

Vision 默认只保留内存副本，切换 Chat/Project、连接失效、归档或退出即撤销。**Remove image** / **Clear selected images** 不删除原文件或 Add Files 附件；附件可再次复查。绘图图库独立管理。

## OCR 与视觉模型

### 本地 OCR

选择图片后点击 **Check OCR**，再选英文、简体中文加英文或繁体中文加英文。检查和识别都需明确触发，不安装引擎或下载语言数据。

Windows 依次检查 `Program Files/Tesseract-OCR/tesseract.exe`，仅在不存在时检查 `%LOCALAPPDATA%/Programs/Tesseract-OCR/tesseract.exe`。自定义安装可在启动前设置 `ELYSIA_TESSERACT_PATH`；指定失败不回退，也不搜索 PATH。安装目录的 `tessdata` 需要 `eng.traineddata`，中文另需 `chi_sim.traineddata` 或 `chi_tra.traineddata`。

规范化 PNG 通过标准输入交给固定程序，不用 Shell、不写临时图片。结果保留文字、像素位置与置信度；TSV 最多 1 MiB、文字 32 KiB、单词框 2048 个。异常坐标、置信度或超限输出拒绝，20 秒后请求停止。文字中的命令和链接只是数据。

**Cancel analysis** 请求取消，进程实际结束后才释放容量。换图、删除或会话失效会撤销结果；OCR 不会自动加入 Chat 或 Work。

### 本地视觉模型

**Check vision models** 只检查 Ollama 已安装模型的图片能力，不下载或加载权重。只有支持图片与文本补全、允许关闭思考输出且无云端/转发元数据的模型可选。选择图片、模型和问题后，**Ask about image** 才提交。

| 输入/输出 | 限制 |
| --- | --- |
| 模型用 PNG | 最长边 2048 像素、8 MiB；Main 保留原图身份 |
| 问题 | 4 KiB |
| 回答 | 16 KiB、1024 token；预算耗尽标记不完整 |
| 请求 | 最多 120 秒 |

结果明确标为模型推测，并保留问题和模型名。连接使用 Settings 当前 Ollama Origin，只接受 HTTP 本机回环及端口，拒绝代理、重定向和远程地址，不另选替代地址。取消后等客户端连接关闭才释放容量；模型回答不能触发工具。

## 单次截图

1. **List displays and windows** 只列名称和显示器尺寸，不取缩略图、图标或像素。
2. 选择来源，点击 **Review capture scope** 查看范围。
3. **Capture this source once** 才授权一次捕获。选择/批准最多 60 秒，拒绝、取消、使用或范围变化即失效。

显示器可选完整画面或百分比区域。区域捕获会暂取所选显示器一帧，再向内取整裁剪并丢弃范围外像素；确认面板说明此过程。窗口选项只捕获所选窗口。来源消失、名称或显示器布局变化时必须重新选择，不回退到其他来源。

捕获使用独立临时 Session 的固定本地页面，仅有此次视频许可，没有音频、摄像头、网络或主界面 API。取得一帧后停止轨道、销毁辅助窗口，再把 PNG 加入集合；OCR/问答仍需另行触发。取消和超时撤销许可，资源关闭前保留锁。应用不定时截图，Work 建议也不能触发截图。

界面重载或 Backend 断开后，可用仅含忙碌布尔值的查询观察/取消现有任务，不因此重新枚举窗口或读屏。

## 审核后用于 Chat 或 Work

填写 **Your description** 后点击 **Review context**，分别查看来源、OCR、模型推测和用户说明。审核只用 Main 已持有的内容，不触发新分析；缺失部分明确显示。

审核记录绑定 Chat、Project、图片 ID 和像素摘要，内存最多八份、五分钟有效。用户说明最多 4 KiB；OCR 和模型正文各最多 8 KiB，按完整 Unicode 字符缩短并保留截断标记。Renderer 不能注入审核正文或批准字段。重审、移除图片、范围变化、断连或退出撤销旧记录；修改说明清除旧界面审核。

| 操作 | 效果 |
| --- | --- |
| **Add to Chat draft** | 将分开的来源、OCR、模型推测和说明追加到现有草稿；不附像素、路径、ID 或摘要，不自动发送 |
| **Use in Work plan** | 为下一计划选择审核 ID；Main 重验后传递有界文本，实际步骤、Start 和写入批准仍由用户决定 |

进入草稿后的文字沿用 Chat 保存规则。Work 视觉证据只留在当前进程；修改未完成步骤可继续使用同一份证据，但检查点、审计和 Chat 历史不保存它，重启不恢复图片或旧批准。图片中的权限声明始终不能成为操作授权。

## 可选保存与清除

**Allow saving selected images** 默认关闭，重载、切换 Chat/Project 或断连后重新关闭。开启只启用 **Save selected image**；每次明确保存才把规范化 PNG 和安全来源标签写入 `workspace/vision/history`，不保存路径、OCR、回答或 Chat 文本。

图片未加密，保存前应检查像素中的敏感内容。历史最多 32 份、128 MiB，包含记录头与中断临时副本；超限拒绝新保存，不自动淘汰。关闭开关不删除已有副本。

**Load saved images** 按当前 Chat/Project 列表；**Restore to selected images** 重验文件、摘要和 PNG 并分配新会话 ID，不恢复审核、分析、截图批准或发送消息。

单份删除、**Clear this Chat’s saved images** 和 **Clear all saved Vision images** 各有独立确认。全局清除含其他或已删除 Chat 的副本；损坏记录仍可按已验证文件名清除。链接、未知文件和目录拒绝操作，不递归删除外部内容。删除历史不删除原文件、内存图片、Chat 消息或绘图图库。

历史随应用数据目录迁移，容量计入 Other，不属于临时音频/缓存/日志清理。删除只作用于当前根；Settings 列出的迁移恢复副本需另行检查。Chat JSON 导出不包含 Vision 图片，要保留它们应备份应用数据目录。

## 验证

自动化覆盖格式/EXIF、坐标、模型连接、一次性截图、过期审核、旧会话结果、Work 批准、历史 CRUD 和迁移。真实 Windows 截图只捕获测试创建的合成窗口；Linux 替身测试不算真实捕获验收。

本机 OCR 验收使用 [Tesseract 5.5.3](https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3)、[tessdata_best 英文/简繁数据](https://github.com/tesseract-ocr/tessdata_best/tree/e12c65a915945e4c28e237a9b52bc4a8f39a0cec)，以及 Windows Arial、Microsoft YaHei、Microsoft JhengHei 字体。语言数据经过来源/摘要核对但不提交 Git；实际精度仍取决于清晰度、字体和排版。

在 `desktop` 执行：

```powershell
npm run electron:build
$env:ELYSIA_OCR_NATIVE_TEST = '1'
node --test tests/vision-ocr-native.test.mjs
```

显式启用后，缺引擎、语言、字体或结果不符会失败，不静默跳过。普通协议测试无需 OCR。包内验收先 `npm run package`，再设置 `$env:ELYSIA_OCR_PACKAGE_ASAR = (Resolve-Path 'out/win-unpacked/resources/app.asar').Path` 并重跑同一测试。

接口参考：[Electron nativeImage](https://www.electronjs.org/docs/latest/api/native-image)、[Tesseract CLI](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)、[Ollama 模型信息](https://docs.ollama.com/api-reference/show-model-details)与[图片输入](https://docs.ollama.com/capabilities/vision)。
