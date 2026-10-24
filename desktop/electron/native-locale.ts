/** Keep native dialogs and the tray in the renderer-selected language without changing native capabilities. */

export type NativeLocale = 'en' | 'zh-CN'

type Values = Readonly<Record<string, string | number>>
const messages: Readonly<Record<string, Readonly<Record<NativeLocale, string>>>> = {
  "Run this CMD command?": {
    "en": "Run this CMD command?", "zh-CN": "运行这条 CMD 命令？"
  },
  "Runs as your Windows user and can read or change files, run programs, and access the network.\n\nWorking directory: {cwd}\n\nCMD:\n{command}": {
    "en": "Runs as your Windows user and can read or change files, run programs, and access the network.\n\nWorking directory: {cwd}\n\nCMD:\n{command}",
    "zh-CN": "命令按当前 Windows 用户权限运行，可读写文件、运行程序和联网。\n\n工作目录：{cwd}\n\nCMD：\n{command}"
  },
  "Your local reply is ready.": {
    "en": "Your local reply is ready.",
    "zh-CN": "本地回复已就绪。"
  },
  "Open Elysia whenever you are ready.": {
    "en": "Open Elysia whenever you are ready.",
    "zh-CN": "准备好时，随时打开 Elysia。"
  },
  "Display {number}": {
    "en": "Display {number}",
    "zh-CN": "显示器 {number}"
  },
  "Window {number}": {
    "en": "Window {number}",
    "zh-CN": "窗口 {number}"
  },
  "Choose a local Desktop Pet program folder": {
    "en": "Choose a local Desktop Pet program folder",
    "zh-CN": "选择本地桌宠程序文件夹"
  },
  "Use this folder": {
    "en": "Use this folder",
    "zh-CN": "使用此文件夹"
  },
  "Select an image for this Chat": {
    "en": "Select an image for this Chat",
    "zh-CN": "为此聊天选择图像"
  },
  "Still PNG and JPEG images": {
    "en": "Still PNG and JPEG images",
    "zh-CN": "PNG 和 JPEG 静态图像"
  },
  "Choose an empty folder for Elysia data": {
    "en": "Choose an empty folder for Elysia data",
    "zh-CN": "为 Elysia 数据选择空文件夹"
  },
  "Move Elysia data?": {
    "en": "Move Elysia data?",
    "zh-CN": "移动 Elysia 数据？"
  },
  "Elysia will pause while it verifies and moves private data.": {
    "en": "Elysia will pause while it verifies and moves private data.",
    "zh-CN": "Elysia 将在验证和移动私密数据期间暂停。"
  },
  "Choose Move only if the selected folder is empty and remains available. The current copy is kept until the Backend is ready.": {
    "en": "Choose Move only if the selected folder is empty and remains available. The current copy is kept until the Backend is ready.",
    "zh-CN": "仅当所选文件夹为空且保持可用时，才选择移动。当前副本将保留至本地服务就绪。"
  },
  "Move": {
    "en": "Move",
    "zh-CN": "移动"
  },
  "Cancel": {
    "en": "Cancel",
    "zh-CN": "取消"
  },
  "Clear temporary Elysia data?": {
    "en": "Clear temporary Elysia data?",
    "zh-CN": "清除 Elysia 临时数据？"
  },
  "Delete temporary audio, application cache, and local logs?": {
    "en": "Delete temporary audio, application cache, and local logs?",
    "zh-CN": "删除临时音频、应用缓存和本地日志？"
  },
  "Chats, Projects, Memory, attachments, Sources, indexes, models, and external files are not part of this cleanup.": {
    "en": "Chats, Projects, Memory, attachments, Sources, indexes, models, and external files are not part of this cleanup.",
    "zh-CN": "此次清理不涉及聊天、项目、记忆、附件、资料来源、索引、模型或外部文件。"
  },
  "Clear temporary data": {
    "en": "Clear temporary data",
    "zh-CN": "清除临时数据"
  },
  "PNG image": {
    "en": "PNG image",
    "zh-CN": "PNG 图像"
  },
  "Choose a song for Elysia to sing": {
    "en": "Choose a song for Elysia to sing",
    "zh-CN": "选择由 Elysia 演唱的歌曲"
  },
  "Choose the isolated vocal stem": {
    "en": "Choose the isolated vocal stem",
    "zh-CN": "选择分离后的人声音轨"
  },
  "Create Song Cover": {
    "en": "Create Song Cover",
    "zh-CN": "创建歌曲翻唱"
  },
  "Choose vocals": {
    "en": "Choose vocals",
    "zh-CN": "选择人声"
  },
  "Audio": {
    "en": "Audio",
    "zh-CN": "音频"
  },
  "Choose the matching instrumental accompaniment": {
    "en": "Choose the matching instrumental accompaniment",
    "zh-CN": "选择匹配的伴奏"
  },
  "Choose accompaniment": {
    "en": "Choose accompaniment",
    "zh-CN": "选择伴奏"
  },
  "Export Elysia Song Cover": {
    "en": "Export Elysia Song Cover",
    "zh-CN": "导出 Elysia 歌曲翻唱"
  },
  "Export WAV": {
    "en": "Export WAV",
    "zh-CN": "导出 WAV"
  },
  "Lossless WAV audio": {
    "en": "Lossless WAV audio",
    "zh-CN": "无损 WAV 音频"
  },
  "Open external website": {
    "en": "Open external website",
    "zh-CN": "打开外部网站"
  },
  "Open this website in your browser?": {
    "en": "Open this website in your browser?",
    "zh-CN": "在浏览器中打开此网站？"
  },
  "Your browser controls its own cookies, login and network traffic. Turning Internet off in Elysia will not close this tab.": {
    "en": "Your browser controls its own cookies, login and network traffic. Turning Internet off in Elysia will not close this tab.",
    "zh-CN": "浏览器自行管理其 Cookie、登录状态和网络流量。在 Elysia 中关闭联网不会关闭此标签页。"
  },
  "Open browser": {
    "en": "Open browser",
    "zh-CN": "打开浏览器"
  },
  "Add attachments to Elysia": {
    "en": "Add attachments to Elysia",
    "zh-CN": "向 Elysia 添加附件"
  },
  "Supported files": {
    "en": "Supported files",
    "zh-CN": "支持的文件"
  },
  "Add sources to this Project": {
    "en": "Add sources to this Project",
    "zh-CN": "向此项目添加资料"
  },
  "Supported documents": {
    "en": "Supported documents",
    "zh-CN": "支持的文档"
  },
  "Choose a replacement source": {
    "en": "Choose a replacement source",
    "zh-CN": "选择替换资料"
  },
  "Export original Project source": {
    "en": "Export original Project source",
    "zh-CN": "导出项目原始资料"
  },
  "Choose a workspace for this Project": {
    "en": "Choose a workspace for this Project",
    "zh-CN": "为此项目选择工作区"
  },
  "Choose a Workspace for this Work Chat": {
    "en": "Choose a Workspace for this Work Chat",
    "zh-CN": "为此工作聊天选择工作区"
  },
  "Select a Workspace document to review": {
    "en": "Select a Workspace document to review",
    "zh-CN": "选择要检查的工作区文档"
  },
  "Review documents": {
    "en": "Review documents",
    "zh-CN": "可检查的文档"
  },
  "Configure Desktop Pet in Settings": {
    "en": "Configure Desktop Pet in Settings",
    "zh-CN": "在设置中配置桌宠"
  },
  "Stop Desktop Pet": {
    "en": "Stop Desktop Pet",
    "zh-CN": "停止桌宠"
  },
  "Retry Desktop Pet": {
    "en": "Retry Desktop Pet",
    "zh-CN": "重试桌宠"
  },
  "Launch Desktop Pet": {
    "en": "Launch Desktop Pet",
    "zh-CN": "启动桌宠"
  },
  "Show Elysia": {
    "en": "Show Elysia",
    "zh-CN": "显示 Elysia"
  },
  "Disable Desktop Pet": {
    "en": "Disable Desktop Pet",
    "zh-CN": "禁用桌宠"
  },
  "Quit": {
    "en": "Quit",
    "zh-CN": "退出"
  },
  "Allow Work action?": {
    "en": "Allow Work action?",
    "zh-CN": "允许工作操作？"
  },
  "Allow": {
    "en": "Allow",
    "zh-CN": "允许"
  },
  "Open {application}?": {
    "en": "Open {application}?",
    "zh-CN": "打开{application}？"
  },
  "Work will launch this application without arguments:\n{executable}": {
    "en": "Work will launch this application without arguments:\n{executable}",
    "zh-CN": "工作模式将不带参数启动此应用：\n{executable}"
  },
  "Set volume to {level}%?": {
    "en": "Set volume to {level}%?",
    "zh-CN": "将音量设为 {level}%？"
  },
  "Default multimedia output: {current}% → {level}%. {mute}": {
    "en": "Default multimedia output: {current}% → {level}%. {mute}",
    "zh-CN": "默认多媒体输出：{current}% → {level}%。{mute}"
  },
  "The output will stay muted.": {
    "en": "The output will stay muted.",
    "zh-CN": "输出将保持静音。"
  },
  "The mute setting will stay unchanged.": {
    "en": "The mute setting will stay unchanged.",
    "zh-CN": "静音设置保持不变。"
  },
  "Notepad": {
    "en": "Notepad",
    "zh-CN": "记事本"
  },
  "Calculator": {
    "en": "Calculator",
    "zh-CN": "计算器"
  },
  "Paint": {
    "en": "Paint",
    "zh-CN": "画图"
  },
  "File Explorer": {
    "en": "File Explorer",
    "zh-CN": "文件资源管理器"
  },
  "Publish GitHub issue": {
    "en": "Publish GitHub issue",
    "zh-CN": "发布 GitHub 议题"
  },
  "Create an issue in {repository}?": {
    "en": "Create an issue in {repository}?",
    "zh-CN": "在 {repository} 中创建议题？"
  },
  "Destination: {url}\n\n{payload}\n\n{notice}": {
    "en": "Destination: {url}\n\n{payload}\n\n{notice}",
    "zh-CN": "目标地址：{url}\n\n{payload}\n\n{notice}"
  },
  "This sends the displayed title and body using your configured GitHub token and may notify repository subscribers. Closing Internet after transmission cannot undo a published issue. This approval applies once to this exact issue.": {
    "en": "This sends the displayed title and body using your configured GitHub token and may notify repository subscribers. Closing Internet after transmission cannot undo a published issue. This approval applies once to this exact issue.",
    "zh-CN": "此操作会使用已配置的 GitHub 令牌发送所显示的标题和正文，并可能通知仓库订阅者。发送后关闭联网无法撤销已发布的议题。此次批准仅适用于这一条确切的议题。"
  },
  "Create this issue": {
    "en": "Create this issue",
    "zh-CN": "创建此议题"
  },
  "选择受信 ComfyUI 安装目录（包含 main.py）": {
    "en": "Choose a trusted ComfyUI installation folder (containing main.py)",
    "zh-CN": "选择受信 ComfyUI 安装目录（包含 main.py）"
  },
  "选择该 ComfyUI 安装使用的 Python 解释器": {
    "en": "Choose the Python interpreter used by this ComfyUI installation",
    "zh-CN": "选择该 ComfyUI 安装使用的 Python 解释器"
  },
  "选择 SDXL safetensors 主模型文件": {
    "en": "Choose an SDXL safetensors checkpoint",
    "zh-CN": "选择 SDXL safetensors 主模型文件"
  },
  "选择要导入的 SDXL LoRA safetensors 文件": {
    "en": "Choose an SDXL LoRA safetensors file to import",
    "zh-CN": "选择要导入的 SDXL LoRA safetensors 文件"
  },
  "导入受信主模型": {
    "en": "Import trusted checkpoint",
    "zh-CN": "导入受信主模型"
  },
  "导入受信 LoRA": {
    "en": "Import trusted LoRA",
    "zh-CN": "导入受信 LoRA"
  },
  "确认并导入": {
    "en": "Confirm and import",
    "zh-CN": "确认并导入"
  },
  "取消": {
    "en": "Cancel",
    "zh-CN": "取消"
  },
  "请确认文件来源与许可。文件将复制到本机私有模型目录，原文件会保留。": {
    "en": "Review the file source and license. The file will be copied to the private local model folder; the original is kept.",
    "zh-CN": "请确认文件来源与许可。文件将复制到本机私有模型目录，原文件会保留。"
  },
  "来源与许可：": {
    "en": "Source and license:",
    "zh-CN": "来源与许可："
  },
  "确认受信图片安装": {
    "en": "Confirm trusted image installation",
    "zh-CN": "确认受信图片安装"
  },
  "确认并保存": {
    "en": "Confirm and save",
    "zh-CN": "确认并保存"
  },
  "请确认安装来源与模型许可，并固定本次文件摘要。修改这些文件后需要重新审核。损坏的旧配置会先备份再替换，画廊会保留。": {
    "en": "Review the installation source and model license, and pin the current file hashes. Changes to these files require another review. A damaged previous configuration will be backed up before replacement; the gallery is kept.",
    "zh-CN": "请确认安装来源与模型许可，并固定本次文件摘要。修改这些文件后需要重新审核。损坏的旧配置会先备份再替换，画廊会保留。"
  },
  "模型 SHA-256:": {
    "en": "Model SHA-256:",
    "zh-CN": "模型 SHA-256:"
  },
  "来源:": {
    "en": "Source:",
    "zh-CN": "来源:"
  },
  "许可:": {
    "en": "License:",
    "zh-CN": "许可:"
  },
  "恢复未完成的图片任务": {
    "en": "Recover unfinished image tasks",
    "zh-CN": "恢复未完成的图片任务"
  },
  "隔离保留并恢复": {
    "en": "Quarantine and recover",
    "zh-CN": "隔离保留并恢复"
  },
  "未完成任务的文件将移入私有隔离目录，已提交画廊继续保留。": {
    "en": "Unfinished task files will move to a private quarantine folder. Committed gallery items are kept.",
    "zh-CN": "未完成任务的文件将移入私有隔离目录，已提交画廊继续保留。"
  },
  "{runs} 个任务，{files} 个文件，{bytes} 字节。\n\n保留位置：{directory}\n\n恢复期间将关闭并重新启动 Backend。": {
    "en": "{runs} tasks, {files} files, {bytes} bytes.\n\nKept at: {directory}\n\nThe Backend will stop and restart during recovery.",
    "zh-CN": "{runs} 个任务，{files} 个文件，{bytes} 字节。\n\n保留位置：{directory}\n\n恢复期间将关闭并重新启动本地服务。"
  },
  "导出生成的图片": {
    "en": "Export generated image",
    "zh-CN": "导出生成的图片"
  }
}
let nativeLocale: NativeLocale = 'en'

/** Accept only the same two interface languages offered by the renderer. */
export function parseNativeLocale(value: unknown): NativeLocale {
  if (value !== 'en' && value !== 'zh-CN') throw new TypeError('Interface locale is invalid.')
  return value
}

/** Seed native copy from the OS before the saved renderer preference arrives. */
export function initializeNativeLocale(systemLocale: string): void {
  nativeLocale = /^zh(?:-|$)/i.test(systemLocale) ? 'zh-CN' : 'en'
}

/** Apply a validated interface preference without changing any permission or application setting. */
export function setNativeLocale(value: unknown): void {
  nativeLocale = parseNativeLocale(value)
}

/** Translate only reviewed authored copy; substitution values remain literal and complete. */
export function nativeText(source: string, values?: Values): string {
  const message = messages[source]?.[nativeLocale] ?? source
  if (values === undefined) return message
  return message.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (placeholder, key: string) => (
    Object.hasOwn(values, key) ? String(values[key]) : placeholder
  ))
}

interface DialogCopy {
  title?: string
  message?: string
  detail?: string
  buttonLabel?: string
  buttons?: string[]
  filters?: Array<{ name: string; extensions: string[] }>
}

/**
 * Copy only authored display fields into the chosen language. Button order,
 * default/cancel IDs, signals, paths, file extensions and permission choices
 * remain untouched; native OS chrome continues to use the operating-system language.
 */
export function localizeNativeDialog<T extends DialogCopy>(options: T): T {
  return {
    ...options,
    ...(options.title === undefined ? {} : { title: nativeText(options.title) }),
    ...(options.message === undefined ? {} : { message: nativeText(options.message) }),
    ...(options.detail === undefined ? {} : { detail: nativeText(options.detail) }),
    ...(options.buttonLabel === undefined ? {} : { buttonLabel: nativeText(options.buttonLabel) }),
    ...(options.buttons === undefined ? {} : { buttons: options.buttons.map(label => nativeText(label)) }),
    ...(options.filters === undefined ? {} : { filters: options.filters.map(filter => ({ ...filter, name: nativeText(filter.name) })) }),
  }
}
