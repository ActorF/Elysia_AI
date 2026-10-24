/** Compose domain-owned interface dictionaries without translating arbitrary data. */

import { coreMessages } from './core-messages.ts'
import { featureMessages } from './feature-messages.ts'
import { shellMessages } from './shell-messages.ts'
import { workMessages } from './work-messages.ts'
import type { MessageCatalog } from './types.ts'

export const messages: MessageCatalog = {
  "Elysia's thoughts": { en: "Elysia's thoughts", 'zh-CN': '爱莉希雅的想法' },
  'About these thoughts': { en: 'About these thoughts', 'zh-CN': '关于这些想法' },
  "These are short summaries of actual activity stages, not the model's word-for-word reasoning.": { en: "These are short summaries of actual activity stages, not the model's word-for-word reasoning.", 'zh-CN': '这里是实际处理阶段的简短摘要，并非模型逐字推理。' },
  "I'll take a moment to think this through.": { en: "I'll take a moment to think this through.", 'zh-CN': '让我认真想一想。' },
  'Let me check the task and available tools.': { en: 'Let me check the task and available tools.', 'zh-CN': '我先看看任务，以及能用哪些工具。' },
  'Let me prepare the next action.': { en: 'Let me prepare the next action.', 'zh-CN': '我来准备下一步操作。' },
  "Let's see what the action reported.": { en: "Let's see what the action reported.", 'zh-CN': '看看这一步返回了什么。' },
  "I'll bring the available information together.": { en: "I'll bring the available information together.", 'zh-CN': '让我把已有的信息整理给你。' },
  'Show {count} more changes': { en: 'Show {count} more changes', 'zh-CN': '展开另外 {count} 项变更' },
  'Review changes': { en: 'Review changes', 'zh-CN': '查看变更' },
  '{count} previous messages': { en: '{count} previous messages', 'zh-CN': '之前的 {count} 条消息' },
  'More information': { en: 'More information', 'zh-CN': '更多说明' },
  'Help: {label}': { en: 'Help: {label}', 'zh-CN': '{label}说明' },
  'Enter 50–200; saved changes apply after a Backend restart.': { en: 'Enter 50–200; saved changes apply after a Backend restart.', 'zh-CN': '输入 50–200；保存后重启本地服务生效。' },
  'Use a configured logical profile ID; saved changes apply after restart.': { en: 'Use a configured logical profile ID; saved changes apply after restart.', 'zh-CN': '填写已配置的语音配置 ID；保存后重启生效。' },
  'Selects the local reference voice after a Backend restart.': { en: 'Selects the local reference voice after a Backend restart.', 'zh-CN': '选择本地参考声音，重启本地服务后生效。' },
  'Device IDs stay local; labels, permission state, and live availability are never saved.': { en: 'Device IDs stay local; labels, permission state, and live availability are never saved.', 'zh-CN': '设备 ID 仅保存在本机；设备名称、权限状态和实时可用状态不会保存。' },
  ...coreMessages,
  ...featureMessages,
  ...shellMessages,
  ...workMessages,
}
