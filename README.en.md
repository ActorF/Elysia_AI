<p align="center">
  <img src="./desktop/public/elysia-icon.png" alt="Elysia AI" width="88">
</p>

<h1 align="center">Elysia AI</h1>

<p align="center">A conversation. On your own computer.</p>
<p align="center">A local-first Windows AI companion inspired by Elysia.</p>

<p align="center">
  <a href="./README.md">中文</a> · <strong>English</strong> · <a href="#get-started">Get started</a> · <a href="./docs/README.md">Documentation</a>
</p>

<br>

## Company, and a helping hand

- **Talk naturally** — Local models, streaming replies, separate chats, and scoped memory.
- **Explore your knowledge** — Organize chats and files in Projects, with sources you can inspect.
- **Use your voice** — Dictation, read-aloud replies, and voice calls linked to your current chat.
- **Work together** — Review plans, operate within a chosen workspace, approve writes, and retain an audit trail.
- **Add what you need** — Image and screen understanding, internet queries, local song covers, and an optional desktop pet.

Chat and data stay local by default. Internet access starts off, screenshots are user-triggered, and external writes require separate confirmation.

This is a **development preview** that runs from source. Packages do not yet include Python, Ollama, or models. Optional features such as voice need separate setup; local image generation is still being integrated into the desktop app.

## Get started

Prepare Windows 10 / 11, Python 3.14, Node.js 24, Git, and [Ollama](https://ollama.com/). Run the following commands in **Command Prompt**.

**1. Set up the project**

```bat
git clone https://github.com/ActorF/Elysia_AI.git
cd /d Elysia_AI
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**2. Prepare the local model**

```bat
ollama pull qwen3.5:9b
```

Keep Ollama running at its default address, `http://localhost:11434`.

**3. Open the desktop app**

```bat
cd desktop
npm ci
npm run dev
```

Use the Electron window that opens automatically. Adjust models and preferences in **Settings**. Basic text chat needs no cloud API key or `.env` file.

## Make it yours

**File-based answers** · **Add sources** to a Project, wait for **Ready**, then enable **Use Project Sources**. Also install `qwen3-embedding:0.6b`. [Knowledge guide](./docs/12-KNOWLEDGE-UI-TESTING.md)

**Voice** · Configure local Faster-Whisper / GPT-SoVITS. Dictate fills the draft; Voice Call sends complete utterances. [Desktop setup](./desktop/README.md)

**Song covers and a desktop pet** · Supply local assets you have permission to use. [Song covers](./docs/15-SONG-COVER.md) · [Desktop pet](./docs/14-LIVE2D-RUNTIME.md)

**Data storage** · View or move your data in **Settings → Data & storage**. [Data management](./docs/13-PRODUCTION-DATA-LAYOUT.md)

## Explore further

[Documentation](./docs/README.md) · [Development guide](./desktop/README.md) · [Source guide](./SOURCE_FILE_GUIDE.md) · [Local protocol](./desktop_protocol/README.md)

Issues and pull requests are welcome. Read the [contribution and documentation rules](./AGENTS.md) before changing source code, and run the required checks.

---

This is an unofficial, non-commercial fan project with no affiliation with or endorsement from HoYoverse / miHoYo. Character and asset rights remain with their owners. Models, reference audio, and paid desktop-pet resources are not distributed with the project.

The repository currently has no root source-code `LICENSE`. See the [asset and rights notice](./MODEL_LICENSE.md) for provenance, attribution, restrictions, and unresolved permissions.
