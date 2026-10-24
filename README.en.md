<p align="center">
  <img src="./desktop/public/elysia-icon.png" alt="Elysia AI" width="88">
</p>

<h1 align="center">Elysia AI</h1>

<p align="center">A conversation. On your own computer.</p>
<p align="center">A local-first Windows AI companion inspired by Elysia.</p>

<p align="center">
  <a href="./README.md">中文</a> · <strong>English</strong> · <a href="https://github.com/ActorF/Elysia_AI/releases/latest">Download</a> · <a href="#get-started">Get started</a> · <a href="./docs/README.md">Documentation</a>
</p>

![Elysia in light mode](./docs/assets/desktop-light.png)

[View dark mode](./docs/assets/desktop-dark.png)

**Delivery snapshot: 0.1.0 · 2026-10-24.** This is a preview release. These instructions describe this version; model quality and optional features depend on your local setup.

## What it does

- **Local conversations:** streaming replies, separate chats, scoped memory, and a saved name preference.
- **Projects and sources:** organize chats and files, retrieve information, and inspect answer sources.
- **Work tasks:** use tools and CMD under your selected permissions, with command output, exit codes, and file-operation records.
- **Optional features:** web search and weather, image and screen understanding, voice, local image generation, song covers, and an external desktop pet.

Chats and data are stored locally by default. In-app internet access starts off, screenshots are user-triggered, and external writes require separate confirmation. CMD has independent permissions and can read or change files, launch programs, and access the network as your Windows user.

## Get started

1. On Windows 10 / 11 x64, download and run [Elysia-Setup-0.1.0.exe](https://github.com/ActorF/Elysia_AI/releases/download/v0.1.0/Elysia-Setup-0.1.0.exe). Python and core backend dependencies are included; Node.js, Git, and a development environment are unnecessary. [Release notes and updates](https://github.com/ActorF/Elysia_AI/releases/latest) · [SHA256 checksums](https://github.com/ActorF/Elysia_AI/releases/download/v0.1.0/SHA256SUMS.txt)
2. Separately install and start [Ollama](https://ollama.com/download/windows), then open **Command Prompt** and download the default model:

   ```bat
   ollama pull qwen3.5:9b
   ollama list
   ```

3. Open Elysia and check the model under **Settings → General**. The default address is `http://localhost:11434`; another installed local model can be selected. If startup reports an unavailable model, finish the download or start Ollama, then retry.

Basic text chat needs no cloud API key or `.env`. Ollama and large models are not inside the EXE. Model downloads require internet access and additional storage; speed and memory requirements depend on your model and hardware. Only local Ollama is supported, not remote or cloud models.

The installer is unsigned, so Windows may display an unknown-publisher notice. Check the download source and published SHA256. GitHub source ZIPs are not installers.

## Everyday controls

| Action | Where or how |
| --- | --- |
| Chats and search | Use the left rail for Chat / Work and expand project conversations. `Ctrl+K` searches; `Ctrl+B` toggles navigation. |
| Send and stop | Enter sends, Shift+Enter adds a line, and Stop cancels generation. Work retries do not rerun commands. |
| Name, language, theme | `Ctrl+,` opens Settings. Save Your name under General for future replies; the interface supports 中文, English, and light/dark themes. |
| Help and records | Hover, focus, or click an information icon for help; Escape dismisses the current layer. Expand commands, file changes, and sources to inspect them. |
| Window | Drag the empty top strip to move the window; double-click to maximize/restore. Native Windows caption buttons remain at the top right. |

Light mode uses pearl-white content, a rose-gray sidebar, pink actions, and lilac selections. Dark mode uses plum surfaces. Both share the same layout, faint champagne frame, and matching native caption colors.

## Optional setup

| Capability | Requirements and limits |
| --- | --- |
| Time and online answers | Time comes from the computer clock. Enable Internet in Settings to let the model choose search or city-based weather; explicit offline requests skip research. Weather includes current conditions and today's/tomorrow's forecast; missing cities prompt clarification. [Guide](./docs/19-INTERNET-AND-SERVICES.md) |
| CMD | Work defaults to Ask before acting: confirm each command with Yes / No in the composer. Allow CMD commands executes directly as your Windows user. Inspect actual output; Stop ends managed command processes. CMD changes do not have automatic Undo. [Guide](./docs/17-WORKFLOW-AND-DESKTOP-AGENT.md) |
| File-based answers | Download `qwen3-embedding:0.6b`, add Project sources, wait for Ready, and enable their use. Chat attachments are not automatically indexed as Project sources. [Guide](./docs/12-KNOWLEDGE-UI-TESTING.md) |
| Images and screen | Review an attached PNG/JPEG or explicitly select a single capture. OCR needs Tesseract and language data; image questions need an installed local vision model. [Guide](./docs/18-VISION-AND-SCREEN.md) |
| Voice | Prepare Faster-Whisper models plus GPT-SoVITS runtime, models, and authorized audio. Dictate fills the draft; Voice Call sends complete utterances. [Setup](./desktop/README.md#optional-voice-setup) |
| Image generation | Select trusted ComfyUI, a full Python interpreter, and a compatible SDXL model in Settings. [Guide](./docs/16-LOCAL-IMAGE-GENERATION.md) |
| Song covers | Source-only, with dedicated CUDA/WSL runtimes and authorized assets. Not available in this installer. [Guide](./docs/15-SONG-COVER.md) |
| Desktop pet | Off by default; select a supported external program. [Guide](./docs/14-LIVE2D-RUNTIME.md) |

Unconfigured extensions do not block basic text chat. Work's Deep thinking needs model support; Elysia's thoughts summarizes activity stages, not word-for-word model reasoning. Memory can contribute to context, but there is no separate memory browser or editor. Voice quality, GPU performance, and external runtime compatibility require checks on the actual device.

## Data and troubleshooting

The installed app defaults to `%APPDATA%\Elysia\data`, separate from program files. **Settings → Data & storage** shows the active location and offers migration and temporary-file cleanup. After a move, use the location shown there.

Chats, sources, settings, and the gallery are under `workspace`; diagnostics are under `logs`; configured speech assets are under `models`. Ollama manages its models separately. Close the app before backing up, and preserve both the active data folder and configuration under `%APPDATA%\Elysia`. Moving data is not a backup. Do not delete chats, the gallery, or `models/cache` as temporary cache. [Directory guide](./docs/13-PRODUCTION-DATA-LAYOUT.md)

If chat fails, run `ollama list` in Command Prompt, check the service and model, then verify the selected model and address in Settings. Live weather requires Internet to be enabled. For other failures, retain the error and logs and open an [issue](https://github.com/ActorF/Elysia_AI/issues) with the app version, Windows version, and model name; remove private content before sharing.

## Run from source

Requires Windows x64, Python 3.14, Node.js 24, Git, and Ollama. In **Command Prompt**:

```bat
git clone https://github.com/ActorF/Elysia_AI.git
cd /d Elysia_AI
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3.5:9b
cd desktop
npm.cmd ci
npm.cmd run dev
```

For later launches, run `npm.cmd run dev` from the checkout's `desktop` folder. Repeat `npm.cmd ci` when dependencies change. See the [desktop development guide](./desktop/README.md) for checks and EXE packaging.

[Documentation](./docs/README.md) · [Source guide](./SOURCE_FILE_GUIDE.md) · [Local protocol](./desktop_protocol/README.md) · [Contribution rules](./AGENTS.md)

## License and assets

Source code uses the [MIT License](./LICENSE). This unofficial fan project is not affiliated with or endorsed by HoYoverse / miHoYo. Characters, artwork, and third-party resources retain their own rights; MIT does not replace their usage conditions. Large models, private audio, and paid desktop-pet resources are not distributed with the project. See the [asset and rights notice](./MODEL_LICENSE.md).
