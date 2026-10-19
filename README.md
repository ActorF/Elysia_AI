<p align="center">
  <img src="./desktop/public/elysia-icon.png" alt="Elysia AI" width="88">
</p>

<h1 align="center">Elysia AI</h1>

<p align="center">在你的电脑上，与你对话。</p>
<p align="center">以爱莉希雅为角色的本地优先 Windows AI 伴侣。</p>

<p align="center">
  <strong>中文</strong> · <a href="./README.en.md">English</a> · <a href="#开始使用">开始使用</a> · <a href="./docs/README.md">文档</a>
</p>

<br>

## 陪伴，也能协助

- **自在对话** — 本地模型、流式回复、独立会话与分范围记忆。
- **整理知识** — 用 Project 组织聊天与文件，让回答附上可查看的来源。
- **开口交流** — 语音输入、回复朗读，以及与当前聊天相连的语音通话。
- **一起完成工作** — 查看计划，在选定目录内执行操作，确认写入并保留审计记录。
- **按需扩展** — 本地图片生成与画廊、图片与屏幕理解、联网查询、本机翻唱和可选桌宠。

聊天与数据默认留在本机。联网默认关闭，截图由用户触发，外部写入需要独立确认。

当前为**开发预览**，需从源码运行；Python、Ollama 和模型尚未随安装包提供。语音等可选能力需要另行配置。本地图片生成可从侧栏进入，在 Settings 的“图片生成”中确认受信 ComfyUI、完整 Python 与 SDXL 安装；详见[图片生成指南](docs/16-LOCAL-IMAGE-GENERATION.md)。

## 开始使用

准备 Windows 10 / 11、Python 3.14、Node.js 24、Git 和 [Ollama](https://ollama.com/)。以下命令在 **CMD** 中执行。

**1. 安装项目**

```bat
git clone https://github.com/ActorF/Elysia_AI.git
cd /d Elysia_AI
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**2. 准备本地模型**

```bat
ollama pull qwen3.5:9b
```

保持 Ollama 运行，默认地址为 `http://localhost:11434`。

**3. 打开桌面应用**

以下以项目位于 `D:\Elysia_AI` 为例；其他位置请替换路径。在新开的 **CMD** 窗口中执行：

```bat
cd /d "D:\Elysia_AI\desktop"
npm.cmd ci
npm.cmd run dev
```

`npm.cmd ci` 只需在首次安装或更新依赖后执行；以后启动只需切换目录并运行 `npm.cmd run dev`。

使用自动打开的 Electron 窗口。模型与偏好可在 **Settings** 中调整；基础文字聊天不需要云端 API Key 或 `.env`。

## 按需开启

**文件问答** · 在 Project 中 **Add sources**，待 **Ready** 后开启 **Use Project Sources**；另需 `qwen3-embedding:0.6b`。[查看指南](./docs/12-KNOWLEDGE-UI-TESTING.md)

**语音交流** · 配置本地 Faster-Whisper / GPT-SoVITS。Dictate 写入草稿；Voice Call 按完整语句发送。[桌面配置](./desktop/README.md)

**翻唱与桌宠** · 使用自己有权使用的本机素材。[翻唱](./docs/15-SONG-COVER.md) · [桌宠](./docs/14-LIVE2D-RUNTIME.md)

**数据管理** · 在 **Settings → Data & storage** 查看或移动数据。[了解详情](./docs/13-PRODUCTION-DATA-LAYOUT.md)

## 深入了解

[文档索引](./docs/README.md) · [开发指南](./desktop/README.md) · [源码导览](./SOURCE_FILE_GUIDE.md) · [本地协议](./desktop_protocol/README.md)

欢迎提交 Issue 或 Pull Request。修改源码前请阅读 [贡献与注释规则](./AGENTS.md)，并运行其中的检查。

---

本项目为非官方、非商业粉丝项目，与 HoYoverse / miHoYo 无隶属或背书关系。角色及相关素材权利归各自权利人；模型、参考音频与付费桌宠资源不随项目分发。

