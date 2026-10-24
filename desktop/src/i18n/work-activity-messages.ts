/** Reviewed stage labels and native receipt wrappers used only in the Work activity display. */

import type { MessageCatalog } from './types.ts'

export const workActivityMessages: MessageCatalog = {
  "CMD command approval": { "en": "CMD command approval", "zh-CN": "CMD 命令确认" },
  "Run this CMD command?": { "en": "Run this CMD command?", "zh-CN": "是否运行这条 CMD 命令？" },
  "Yes": { "en": "Yes", "zh-CN": "是" },
  "No": { "en": "No", "zh-CN": "否" },
  "Submitting your choice…": { "en": "Submitting your choice…", "zh-CN": "正在提交选择…" },
  "This command is no longer awaiting approval.": { "en": "This command is no longer awaiting approval.", "zh-CN": "这条命令已不再等待确认。" },
  "Your choice could not be confirmed. Stop the task if it is still waiting.": { "en": "Your choice could not be confirmed. Stop the task if it is still waiting.", "zh-CN": "未能确认你的选择。如果任务仍在等待，请停止任务。" },
  "Review command and working directory": { "en": "Review command and working directory", "zh-CN": "查看命令与工作目录" },
  "CMD command": { "en": "CMD command", "zh-CN": "CMD 命令" },
  "Working directory": { "en": "Working directory", "zh-CN": "工作目录" },
  "Runs as your Windows user; it can read or change files, run programs, and access the network.": {
    "en": "Runs as your Windows user; it can read or change files, run programs, and access the network.",
    "zh-CN": "以你的 Windows 用户身份运行，可读取或修改文件、启动程序及访问网络。"
  },
  "Use a nonempty single-line CMD command; join operations with & or &&. Supply a drive-qualified absolute working directory and a timeout from 1000 to 120000 milliseconds.": {
    "en": "Use a nonempty single-line CMD command; join operations with & or &&. Supply a drive-qualified absolute working directory and a timeout from 1000 to 120000 milliseconds.",
    "zh-CN": "请使用非空的单行 CMD 命令，可用 & 或 && 连接操作。工作目录须为 Windows 盘符开头的绝对路径，超时须为 1000 到 120000 毫秒。"
  },
  "运行 CMD 命令": { "en": "Run CMD command", "zh-CN": "运行 CMD 命令" },
  "Run CMD command: {command}": { "en": "Run CMD command: {command}", "zh-CN": "运行 CMD 命令：{command}" },
  "{tool}: {count} item{partial}; relative to the selected Workspace: {paths}": {
    "en": "{tool}: {count} item{partial}; relative to the selected Workspace: {paths}",
    "zh-CN": "{tool}：{count} 项{partial}；相对于所选工作区：{paths}"
  },
  "正在深度思考": {
    "en": "Thinking deeply",
    "zh-CN": "正在深度思考"
  },
  "正在重新生成回答；不会重复执行工具操作": {
    "en": "Regenerating the reply without repeating tool actions",
    "zh-CN": "正在重新生成回答；不会重复执行工具操作"
  },
  "正在检查任务与可用工具": {
    "en": "Checking the task and available tools",
    "zh-CN": "正在检查任务与可用工具"
  },
  "正在整理回答": {
    "en": "Preparing the reply",
    "zh-CN": "正在整理回答"
  },
  "无法确认下一步工具操作，已停止执行": {
    "en": "The next tool action could not be verified; execution stopped",
    "zh-CN": "无法确认下一步工具操作，已停止执行"
  },
  "已跳过重复的工具操作": {
    "en": "Skipped a repeated tool action",
    "zh-CN": "已跳过重复的工具操作"
  },
  "查找文件": {
    "en": "Find files",
    "zh-CN": "查找文件"
  },
  "查看目录": {
    "en": "List directory",
    "zh-CN": "查看目录"
  },
  "读取文件": {
    "en": "Read file",
    "zh-CN": "读取文件"
  },
  "查看文件信息": {
    "en": "Inspect file details",
    "zh-CN": "查看文件信息"
  },
  "查找可用应用": {
    "en": "Find available apps",
    "zh-CN": "查找可用应用"
  },
  "打开应用": {
    "en": "Open app",
    "zh-CN": "打开应用"
  },
  "读取系统音量": {
    "en": "Read system volume",
    "zh-CN": "读取系统音量"
  },
  "调整系统音量": {
    "en": "Adjust system volume",
    "zh-CN": "调整系统音量"
  },
  "当前权限不允许此操作。": {
    "en": "The current permission does not allow this action.",
    "zh-CN": "当前权限不允许此操作。"
  },
  "工具未能完成操作。": {
    "en": "The tool could not complete the action.",
    "zh-CN": "工具未能完成操作。"
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
  "These desktop tools require a trusted local Windows installation.": {
    "en": "These desktop tools require a trusted local Windows installation.",
    "zh-CN": "这些桌面工具需要受信任的本地 Windows 安装。"
  },
  "The requested Windows application is not installed in its trusted location.": {
    "en": "The requested Windows application is not installed in its trusted location.",
    "zh-CN": "请求的 Windows 应用未安装在受信任的位置。"
  },
  "The requested Windows application has an unsupported path or link.": {
    "en": "The requested Windows application has an unsupported path or link.",
    "zh-CN": "请求的 Windows 应用使用了不受支持的路径或链接。"
  },
  "Invalid tool arguments.": {
    "en": "Invalid tool arguments.",
    "zh-CN": "工具参数无效。"
  },
  "Use an available application ID or an integer volume from 0 to 100; extra arguments are not accepted.": {
    "en": "Use an available application ID or an integer volume from 0 to 100; extra arguments are not accepted.",
    "zh-CN": "请使用可用的应用标识或 0 至 100 的整数音量值；不接受其他参数。"
  },
  "The audio helper returned an invalid result.": {
    "en": "The audio helper returned an invalid result.",
    "zh-CN": "音频辅助程序返回了无效结果。"
  },
  "The audio output changed. Volume was not adjusted; request the operation again.": {
    "en": "The audio output changed. Volume was not adjusted; request the operation again.",
    "zh-CN": "音频输出设备已更改。音量未调整，请重新请求此操作。"
  },
  "Windows could not read or change the default audio output.": {
    "en": "Windows could not read or change the default audio output.",
    "zh-CN": "Windows 无法读取或更改默认音频输出。"
  },
  "The Windows audio helper could not start.": {
    "en": "The Windows audio helper could not start.",
    "zh-CN": "无法启动 Windows 音频辅助程序。"
  },
  "The audio operation was cancelled.": {
    "en": "The audio operation was cancelled.",
    "zh-CN": "音频操作已取消。"
  },
  "The Windows audio helper timed out.": {
    "en": "The Windows audio helper timed out.",
    "zh-CN": "Windows 音频辅助程序超时。"
  },
  "The Windows audio helper stopped accepting its request.": {
    "en": "The Windows audio helper stopped accepting its request.",
    "zh-CN": "Windows 音频辅助程序已停止接收请求。"
  },
  "The audio helper exceeded its output limit.": {
    "en": "The audio helper exceeded its output limit.",
    "zh-CN": "音频辅助程序超出了输出限制。"
  },
  "The audio helper returned an unexpected response.": {
    "en": "The audio helper returned an unexpected response.",
    "zh-CN": "音频辅助程序返回了异常响应。"
  },
  "The audio helper returned an invalid response.": {
    "en": "The audio helper returned an invalid response.",
    "zh-CN": "音频辅助程序返回了无效响应。"
  },
  "The Windows audio operation did not complete.": {
    "en": "The Windows audio operation did not complete.",
    "zh-CN": "Windows 音频操作未完成。"
  },
  "The Windows audio helper has no private data channel.": {
    "en": "The Windows audio helper has no private data channel.",
    "zh-CN": "Windows 音频辅助程序没有私有数据通道。"
  },
  "Another native Work operation is still finishing.": {
    "en": "Another native Work operation is still finishing.",
    "zh-CN": "另一个原生工作操作仍在结束中。"
  },
  "This Work request is no longer active.": {
    "en": "This Work request is no longer active.",
    "zh-CN": "此工作请求已不再处于活动状态。"
  },
  "The action was not approved.": {
    "en": "The action was not approved.",
    "zh-CN": "此操作未获批准。"
  },
  "The selected Work permission does not allow this tool.": {
    "en": "The selected Work permission does not allow this tool.",
    "zh-CN": "所选工作权限不允许使用此工具。"
  },
  "No supported applications are installed in their trusted Windows locations.": {
    "en": "No supported applications are installed in their trusted Windows locations.",
    "zh-CN": "受信任的 Windows 位置中未安装受支持的应用。"
  },
  "The audio helper returned an unexpected output device.": {
    "en": "The audio helper returned an unexpected output device.",
    "zh-CN": "音频辅助程序返回了异常输出设备。"
  },
  "The native Work operation failed.": {
    "en": "The native Work operation failed.",
    "zh-CN": "原生工作操作失败。"
  },
  " Native dispatch had started; the operation may already have taken effect.": {
    "en": " Native dispatch had started; the operation may already have taken effect.",
    "zh-CN": " 原生操作已开始执行，可能已经生效。"
  },
  "Available applications:\n{applications}": {
    "en": "Available applications:\n{applications}",
    "zh-CN": "可用应用：\n{applications}"
  },
  "Windows could not launch {application}.": {
    "en": "Windows could not launch {application}.",
    "zh-CN": "Windows 无法启动{application}。"
  },
  "Windows started the {application} launcher. Window visibility is controlled by the application.": {
    "en": "Windows started the {application} launcher. Window visibility is controlled by the application.",
    "zh-CN": "Windows 已启动{application}。窗口是否可见由该应用控制。"
  },
  "Default output volume: {level}%. {mute}": {
    "en": "Default output volume: {level}%. {mute}",
    "zh-CN": "默认输出音量：{level}%。{mute}"
  },
  "Default output volume is now {level}%. {mute}": {
    "en": "Default output volume is now {level}%. {mute}",
    "zh-CN": "默认输出音量现为 {level}%。{mute}"
  },
  "Muted.": {
    "en": "Muted.",
    "zh-CN": "已静音。"
  },
  "Not muted.": {
    "en": "Not muted.",
    "zh-CN": "未静音。"
  },
  "The output is muted.": {
    "en": "The output is muted.",
    "zh-CN": "输出已静音。"
  },
  "The output is not muted.": {
    "en": "The output is not muted.",
    "zh-CN": "输出未静音。"
  },
  "{tool}: {result}": {
    "en": "{tool}: {result}",
    "zh-CN": "{tool}：{result}"
  },
  "{tool}: {status}. {detail}": {
    "en": "{tool}: {status}. {detail}",
    "zh-CN": "{tool}：{status}。{detail}"
  },
  "Not approved": {
    "en": "Not approved",
    "zh-CN": "未获授权"
  },
  "{tool}: {count} items{partial}; relative to the selected Workspace: {paths}": {
    "en": "{tool}: {count} items{partial}; relative to the selected Workspace: {paths}",
    "zh-CN": "{tool}：{count} 项{partial}；相对于所选工作区：{paths}"
  },
  " (partial results)": {
    "en": " (partial results)",
    "zh-CN": "（结果未列全）"
  },
  "No matches": {
    "en": "No matches",
    "zh-CN": "没有匹配项"
  },
  "{tool}: {path} (within the selected Workspace, {bytes} bytes)": {
    "en": "{tool}: {path} (within the selected Workspace, {bytes} bytes)",
    "zh-CN": "{tool}：{path}（所选工作区内，{bytes} 字节）"
  },
  "{tool}: read {path}; the result exceeds the display limit": {
    "en": "{tool}: read {path}; the result exceeds the display limit",
    "zh-CN": "{tool}：已读取 {path}；结果超过显示上限"
  }
}
