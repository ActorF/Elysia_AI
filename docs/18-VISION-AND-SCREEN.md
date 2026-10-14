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
