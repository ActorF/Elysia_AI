# 图片与屏幕理解

Stage 11 使用独立的 Vision 面板，在 Chat 和 Work 中处理用户明确选择的图片。图片理解与本地绘图、普通附件、人格 Markdown 分开；图片导入不会自动发送 Chat 消息、加载模型或执行 Work 动作。

## 图片输入

点击 **Choose image** 打开系统文件选择器，只接受 PNG 与 JPEG。Main 校验真实格式和画布尺寸后才交给 Electron 解码，再保存去除元数据的 PNG 像素。JPEG 的 EXIF 方向会应用到像素；路径、EXIF、原生句柄与原始文件不会交给界面。透明 PNG 的必要透明度信息保留。动画 PNG、带 EXIF 的 PNG 和不支持的像素格式明确拒绝。

| 边界 | 上限 |
| --- | --- |
| 单次读取的源文件 | 8 MiB |
| 图片尺寸 | 每边 8192 像素，总计 16 × 1024 × 1024 像素 |
| 规范化后的单张 PNG | 16 MiB |
| 当前集合 | 8 张图片，规范化像素总计 64 MiB |
| 界面预览 | 每边 512 像素，PNG 最多 1 MiB |

文件读取拒绝链接、硬链接和读取期间的文件身份变化。名称仅用于显示，不能选择路径。导入、解码和发布结果都绑定规范的当前 Chat、Project 和模式；迟到的旧范围结果会丢弃。原生选择器和读取回调真正结束前，仍占用与 Chat、Work、语音及数据迁移共用的操作容量。

默认仅保留当前会话的内存副本。切换 Chat 或 Project、连接失效、归档或退出会撤销当前集合。**Remove image** 和 **Clear selected images** 删除 Vision 内存副本，不删除用户选择的原文件。普通附件和绘图图库仍沿用原有流程。

Electron 的图片 API 本身不应用 EXIF 方向，因此方向校正属于本项目导入层的职责，而不是依赖解码器隐式处理。支持范围参见 [Electron nativeImage 文档](https://www.electronjs.org/docs/latest/api/native-image)。

## 本地 OCR

选择图片后，点击 **Check OCR** 检查现有 Tesseract 与语言数据，再选择英文、简体中文加英文或繁体中文加英文进行识别。检查和识别都由用户触发，不会安装引擎或下载语言包。Windows 默认使用 `Program Files/Tesseract-OCR/tesseract.exe`；自定义安装可在启动应用前设置 `ELYSIA_TESSERACT_PATH`。中文选项需要对应的 `chi_sim` 或 `chi_tra` 与 `eng` 数据，缺少引擎和缺少语言包分别显示。

识别只把当前已审核图片的规范化 PNG 通过标准输入交给固定本地程序，不使用 Shell，也不产生临时图片。返回结果保留文字、像素位置和置信度；结果作为普通文本显示，图片内的命令和链接不能成为操作授权。TSV 输出上限为 1 MiB、文字 32 KiB、单词框 2048 个；越界坐标、异常置信度或超限输出会拒绝发布。进程超过 20 秒会请求停止。

**Cancel** 会立即请求取消，但实际进程关闭前仍占用操作容量。切换分析对象、删除图片或会话失效会撤销相关结果；迟到的旧结果不会覆盖新会话。OCR 结果仅在内存中保留，不会自动加入聊天或 Work 计划。Tesseract 的输出约定参见[官方命令行文档](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)。

## 本地视觉模型

**Check vision models** 只检查本机 Ollama 中已经安装的模型及其图片能力，不下载模型或加载权重。选择模型、图片并输入问题后，**Ask about image** 才提交图片。支持图片与文本补全、且允许关闭思考输出的模型才会列入选项；Ollama 云端模型和转发元数据会拒绝。检查约定参见 [Ollama 模型信息 API](https://docs.ollama.com/api-reference/show-model-details) 与[图片输入文档](https://docs.ollama.com/capabilities/vision)。

Main 保留原图身份，另外生成最长边不超过 2048 像素、最多 8 MiB 的 PNG 供模型使用。问题最多 4 KiB，回答最多 16 KiB，生成预算为 1024 token；达到生成预算时会标记回答不完整。结果明确标为模型推测，保留提问和模型名称，不与 OCR 文字混为一谈。

传输读取 Settings 中当前生效的 Ollama Origin，只接受 HTTP 本机回环地址及其端口；远程或不支持的 Origin 会显示不可用，不会改用另一个地址。客户端不使用系统代理、不跟随重定向，也不把图片发送到远程接口。请求最多 120 秒，取消后等待当前客户端连接关闭再释放容量。模型回答不会触发工具；图片中的命令或链接仍是待审核内容。开发验收用内存生成的红蓝方块 PNG 调用本机 `qwen3.5:9b`，正确回答了两种颜色和左右位置，没有读取用户图片或屏幕。
