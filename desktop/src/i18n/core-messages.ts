/** Reviewed bilingual copy for application startup and shared settings. */

import type { MessageCatalog } from './types.ts'
import { settingsMessages } from './settings-messages.ts'

export const coreMessages: MessageCatalog = {
  ...settingsMessages,
  'Could not connect to local Ollama. Install or start Ollama, check its address in Settings, then retry.': {
    en: 'Could not connect to local Ollama. Install or start Ollama, check its address in Settings, then retry.',
    'zh-CN': '无法连接本机 Ollama。请安装或启动 Ollama，在设置中检查地址后重试。',
  },
  'The selected local Ollama model is not installed. Download it in Ollama or select an installed model in Settings.': {
    en: 'The selected local Ollama model is not installed. Download it in Ollama or select an installed model in Settings.',
    'zh-CN': '尚未安装所选的本地 Ollama 模型。请先下载该模型，或在设置中选择已安装的模型。',
  },
  'Local Ollama could not prepare the selected model. Check Ollama and the model in Settings, then retry.': {
    en: 'Local Ollama could not prepare the selected model. Check Ollama and the model in Settings, then retry.',
    'zh-CN': '本机 Ollama 无法准备所选模型。请检查 Ollama 及模型设置后重试。',
  },
  'Startup configuration is invalid. Check the local Ollama settings; reinstall Elysia if the problem persists.': {
    en: 'Startup configuration is invalid. Check the local Ollama settings; reinstall Elysia if the problem persists.',
    'zh-CN': '启动配置无效，请检查本地 Ollama 设置。如果问题持续存在，请重新安装 Elysia。',
  },
  "Model '{model}' is not installed in Ollama.": {
    en: "Model '{model}' is not installed in Ollama.", 'zh-CN': 'Ollama 中尚未安装模型“{model}”。',
  },
  'Could not connect to Ollama at {origin}.': {
    en: 'Could not connect to Ollama at {origin}.', 'zh-CN': '无法连接到 {origin} 的 Ollama 服务。',
  },
  'Ollama must use local HTTP: choose localhost, 127.0.0.1, or [::1] with the local service port. Remote Ollama, credentials, and URL paths are not supported.': {
    en: 'Ollama must use local HTTP: choose localhost, 127.0.0.1, or [::1] with the local service port. Remote Ollama, credentials, and URL paths are not supported.',
    'zh-CN': 'Ollama 必须使用本机 HTTP 地址：localhost、127.0.0.1 或 [::1]，以及本地服务端口。不支持远程地址、认证信息或 URL 路径。',
  },
  'Choose an installed local Ollama model; cloud model names are not supported.': {
    en: 'Choose an installed local Ollama model; cloud model names are not supported.', 'zh-CN': '请选择已安装的本地 Ollama 模型，不支持云端模型。',
  },
  'Use local HTTP with localhost, 127.0.0.1, or [::1]. Credentials, paths, query strings, and fragments are rejected.': {
    en: 'Use local HTTP with localhost, 127.0.0.1, or [::1]. Credentials, paths, query strings, and fragments are rejected.',
    'zh-CN': '请使用 localhost、127.0.0.1 或 [::1] 的本机 HTTP 地址。不允许认证信息、路径、查询参数或片段。',
  },
  'The local service is offline. Retry to reconnect.': { en: 'The local service is offline. Retry to reconnect.', 'zh-CN': '本地服务已离线，请重试以重新连接。' },
  'Set up local chat': { en: 'Set up local chat', 'zh-CN': '准备本地聊天' },
  'Elysia needs Ollama and a downloaded local model to chat. These are installed separately.': {
    en: 'Elysia needs Ollama and a downloaded local model to chat. These are installed separately.',
    'zh-CN': '聊天需要另外安装 Ollama 并下载本地模型。',
  },
  'Install and open Ollama on this computer.': { en: 'Install and open Ollama on this computer.', 'zh-CN': '在这台电脑上安装并打开 Ollama。' },
  'In PowerShell, use ollama list to find an installed model.': {
    en: 'In PowerShell, use ollama list to find an installed model.', 'zh-CN': '在 PowerShell 中运行 ollama list，查看已安装的模型。',
  },
  'In PowerShell, download the configured model. The first download can take several minutes.': {
    en: 'In PowerShell, download the configured model. The first download can take several minutes.',
    'zh-CN': '在 PowerShell 中下载当前配置的模型，首次下载可能需要较长时间。',
  },
  'Open model settings to choose an installed model, save any changes, then retry the connection.': {
    en: 'Open model settings to choose an installed model, save any changes, then retry the connection.',
    'zh-CN': '打开模型设置，选择已安装的模型并保存更改，然后重试连接。',
  },
  'Download Ollama': { en: 'Download Ollama', 'zh-CN': '下载 Ollama' },
  'Open model settings': { en: 'Open model settings', 'zh-CN': '打开模型设置' },
  'Interface language': { en: 'Interface language', 'zh-CN': '界面语言' },
  'Language': { en: 'Language', 'zh-CN': '语言' },
  'Simplified Chinese': { en: 'Simplified Chinese', 'zh-CN': '简体中文' },
  'English': { en: 'English', 'zh-CN': '英语' },
  'Applies immediately on this device. Your conversations and files stay as written.': {
    en: 'Applies immediately on this device. Your conversations and files stay as written.',
    'zh-CN': '立即应用于此设备。对话和文件中的原始内容保持不变。',
  },
  'Elysia could not initialize.': { en: 'Elysia could not initialize.', 'zh-CN': 'Elysia 无法初始化。' },
  'Reload the interface, or close and reopen Elysia if the problem continues.': {
    en: 'Reload the interface, or close and reopen Elysia if the problem continues.',
    'zh-CN': '请重新加载界面；如果问题仍然存在，请关闭并重新打开 Elysia。',
  },
  'Reload interface': { en: 'Reload interface', 'zh-CN': '重新加载界面' },
}
