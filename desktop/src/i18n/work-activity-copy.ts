/** Localize the closed Work progress wrappers at display time while preserving receipts, paths, and unknown text. */

import type { WorkProgressEntry } from '../../electron/work-agent-protocol.ts'
import { getLocale, translate, type Locale } from './locale.ts'
import { workActivityMessages } from './work-activity-messages.ts'

const toolLabels = ['查找文件', '查看目录', '读取文件', '查看文件信息', '查找可用应用', '打开应用', '读取系统音量', '调整系统音量', '运行 CMD 命令']
const applications: Readonly<Record<string, string>> = {
  notepad: 'Notepad', calculator: 'Calculator', paint: 'Paint', explorer: 'File Explorer',
}
const dispatchNotice = ' Native dispatch had started; the operation may already have taken effect.'

function fixedCopy(source: string, locale: Locale): string {
  return workActivityMessages[source]?.[locale] ?? source
}

function nativeReceipt(source: string, locale: Locale): string {
  if (source.endsWith(dispatchNotice)) {
    const message = source.slice(0, -dispatchNotice.length)
    return nativeReceipt(message, locale) + fixedCopy(dispatchNotice, locale)
  }
  const volume = /^(Default output volume:|Default output volume is now) (\d{1,3})%\. (Muted\.|Not muted\.|The output is muted\.|The output is not muted\.)$/.exec(source)
  if (volume !== null && Number(volume[2]) <= 100) {
    return translate(volume[1].endsWith(':') ? 'Default output volume: {level}%. {mute}' : 'Default output volume is now {level}%. {mute}', locale, {
      level: volume[2], mute: fixedCopy(volume[3], locale),
    })
  }
  for (const name of Object.values(applications)) {
    if (source === `Windows could not launch ${name}.`) {
      return translate('Windows could not launch {application}.', locale, { application: fixedCopy(name, locale) })
    }
    if (source === `Windows started the ${name} launcher. Window visibility is controlled by the application.`) {
      return translate('Windows started the {application} launcher. Window visibility is controlled by the application.', locale, { application: fixedCopy(name, locale) })
    }
  }
  if (source.startsWith('Available applications:\n')) {
    const lines = source.slice('Available applications:\n'.length).split('\n')
    const translated = lines.map(line => {
      const app = Object.entries(applications).find(([id, name]) => line === `${id}: ${name}`)
      return app === undefined ? null : `${app[0]}: ${fixedCopy(app[1], locale)}`
    })
    if (translated.every(line => line !== null)) {
      return translate('Available applications:\n{applications}', locale, { applications: translated.join('\n') })
    }
  }
  return fixedCopy(source, locale)
}

/**
 * Render only the source-controlled grammar emitted by core/work_assistant.py.
 * No translated value is written back to activity state or model evidence. File
 * values are inserted literally; an unknown or truncated wrapper is kept intact.
 */
export function formatWorkActivity(entry: WorkProgressEntry, locale: Locale = getLocale()): string {
  const source = entry.text
  if (entry.phase === 'tool' && source.startsWith('运行 CMD 命令：')) {
    return translate('Run CMD command: {command}', locale, { command: source.slice('运行 CMD 命令：'.length) })
  }
  if (entry.phase !== 'result') return fixedCopy(source, locale)
  const label = toolLabels.find(tool => source.startsWith(`${tool}：`))
  if (label === undefined) return fixedCopy(source, locale)
  const tool = fixedCopy(label, locale)
  const body = source.slice(label.length + 1)
  const failure = /^(未获授权|未完成|已取消)。([\s\S]*)$/.exec(body)
  if (failure !== null) {
    const status = failure[1] === '未获授权' ? 'Not approved' : failure[1] === '未完成' ? 'Incomplete' : 'Cancelled'
    return translate('{tool}: {status}. {detail}', locale, { tool, status: translate(status, locale), detail: nativeReceipt(failure[2], locale) })
  }
  if (toolLabels.indexOf(label) >= 4) {
    return translate('{tool}: {result}', locale, { tool, result: nativeReceipt(body, locale) })
  }
  const listing = /^(\d+) 项(（结果未列全）)?；相对于所选 Workspace：([\s\S]*)$/.exec(body)
  if (listing !== null) {
    return translate(listing[1] === '1'
      ? '{tool}: {count} item{partial}; relative to the selected Workspace: {paths}'
      : '{tool}: {count} items{partial}; relative to the selected Workspace: {paths}', locale, {
      tool, count: listing[1], partial: listing[2] ? fixedCopy(' (partial results)', locale) : '',
      paths: listing[1] === '0' && listing[3] === '没有匹配项' ? fixedCopy('No matches', locale) : listing[3],
    })
  }
  const file = /^([\s\S]+)（所选 Workspace 内，(\d+) 字节）$/.exec(body)
  if (file !== null) {
    return translate('{tool}: {path} (within the selected Workspace, {bytes} bytes)', locale, { tool, path: file[1], bytes: file[2] })
  }
  const overflow = /^已读取 ([\s\S]+)；结果超过显示上限$/.exec(body)
  if (overflow !== null) {
    return translate('{tool}: read {path}; the result exceeds the display limit', locale, { tool, path: overflow[1] })
  }
  return source
}
