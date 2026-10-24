/** Verify localization boundaries, catalog consistency, and preservation of native diagnostics and user values. */

import assert from 'node:assert/strict'
import { readdir } from 'node:fs/promises'
import test from 'node:test'
import { formatUiError } from '../src/i18n/ui-error.ts'
import { audioDeviceLabel, formatStorageWarning } from '../src/i18n/settings-copy.ts'
import { formatWorkActivity } from '../src/i18n/work-activity-copy.ts'

test('localizes an exact reviewed native error in either interface language', () => {
  const message = 'The requested file does not exist.'
  const envelope = `Error invoking remote method 'work:run-tool': Error: ${message}`
  assert.equal(formatUiError(envelope, 'en'), message)
  assert.equal(formatUiError(envelope, 'zh-CN'), '请求的文件不存在。')
  assert.equal(formatUiError(message, 'zh-CN'), '请求的文件不存在。')
  assert.equal(formatUiError("Error invoking remote method 'work:run-tool': TypeError: 请求的文件不存在。", 'en'), message)
})

test('keeps unknown and partly matching diagnostics intact without parsing user values', () => {
  const diagnostics = [
    "Error invoking remote method 'work:run-tool': Error: Private diagnostic C:\\用户\\Error.txt $& {name}",
    "Error invoking remote method 'work:run-tool': Error: The requested file does not exist.\n    at user-script.js:12",
    "Error invoking remote method 'work:run-tool': Error: The requested file does not exist. Additional information",
    "Error invoking remote method 'work:run-tool': Error: Error invoking remote method 'other:call': Error: The requested file does not exist.",
    "Error invoking remote method 'unexpected channel': Error: The requested file does not exist.",
    "Prefix Error invoking remote method 'work:run-tool': Error: The requested file does not exist.",
    'Model-provided diagnostic 中文 $& {name}',
  ]
  for (const diagnostic of diagnostics) {
    assert.equal(formatUiError(diagnostic, 'zh-CN'), diagnostic)
    assert.equal(formatUiError(diagnostic, 'en'), diagnostic)
  }
})

test('renders combined native recovery warnings completely while preserving unknown diagnostics', () => {
  const first = 'The data move was rolled back.'
  const second = 'A recovery-copy path could not be inspected and remains recorded.'
  assert.equal(formatStorageWarning(`${first} ${second}`, 'zh-CN'), '数据迁移已回滚。无法检查某个恢复副本路径，相关记录已保留。')
  assert.equal(formatStorageWarning(`${first} ${second}`, 'en'), `${first} ${second}`)
  const unknown = `${first} C:\\Private\\user-data could not be opened.`
  assert.equal(formatStorageWarning(unknown, 'zh-CN'), unknown)
  assert.equal(formatUiError("Error invoking remote method 'desktop-pet:update': Error: Choose a detected Desktop Pet program first.", 'zh-CN'), '请先选择已检测到的桌宠程序。')
})

test('generated device labels follow language changes without translating real device names', () => {
  const generated = { deviceId: 'input-1', kind: 'audioinput', label: 'Microphone 1', generatedLabelNumber: 1 }
  assert.equal(audioDeviceLabel(generated, 0, 'zh-CN'), '麦克风 1')
  assert.equal(audioDeviceLabel(generated, 0, 'en'), 'Microphone 1')
  const hardware = { deviceId: 'input-2', kind: 'audioinput', label: 'Microphone 1' }
  assert.equal(audioDeviceLabel(hardware, 1, 'zh-CN'), 'Microphone 1')
  assert.equal(hardware.label, 'Microphone 1')
})

test('authored Work activity changes language without changing paths, receipts, or unknown text', () => {
  const stage = { sequence: 1, phase: 'planning', text: '正在检查任务与可用工具' }
  assert.equal(formatWorkActivity(stage, 'en'), 'Checking the task and available tools')
  const path = 'folder/取消 ${name} $&（文件）.txt'
  const receipt = { sequence: 2, phase: 'result', text: `读取文件：${path}（所选 Workspace 内，128 字节）` }
  assert.equal(formatWorkActivity(receipt, 'en'), `Read file: ${path} (within the selected Workspace, 128 bytes)`)
  assert.equal(formatWorkActivity(receipt, 'zh-CN'), `读取文件：${path}（所选工作区内，128 字节）`)
  assert.equal(receipt.text, `读取文件：${path}（所选 Workspace 内，128 字节）`)
  const search = { sequence: 3, phase: 'result', text: `查找文件：1 项（结果未列全）；相对于所选 Workspace：${path}` }
  assert.equal(formatWorkActivity(search, 'en'), `Find files: 1 item (partial results); relative to the selected Workspace: ${path}`)
  assert.equal(formatWorkActivity({ sequence: 4, phase: 'result', text: '调整系统音量：Default output volume is now 37%. The output is muted.' }, 'zh-CN'), '调整系统音量：默认输出音量现为 37%。输出已静音。')
  assert.equal(formatWorkActivity({ sequence: 5, phase: 'result', text: '打开应用：Windows started the Notepad launcher. Window visibility is controlled by the application.' }, 'zh-CN'), '打开应用：Windows 已启动记事本。窗口是否可见由该应用控制。')
  for (const text of ['User-authored prose 取消 $&', '读取文件：opaque diagnostic', `读取文件：${path}（所选 Workspace 内，`]) {
    assert.equal(formatWorkActivity({ sequence: 6, phase: 'result', text }, 'en'), text)
  }
})

test('domain catalogs never silently override the same source with conflicting meanings', async () => {
  const directory = new URL('../src/i18n/', import.meta.url)
  const seen = new Map()
  for (const file of (await readdir(directory)).filter(name => name.endsWith('-messages.ts')).sort()) {
    const module = await import(new URL(file, directory).href)
    for (const [name, catalog] of Object.entries(module)) {
      if (!name.endsWith('Messages')) continue
      for (const [source, translated] of Object.entries(catalog)) {
        const previous = seen.get(source)
        if (previous !== undefined) {
          assert.deepEqual(translated, previous.translated, `Conflicting source ${JSON.stringify(source)} in ${previous.file} and ${file}; use a separate context key.`)
        }
        seen.set(source, { file, translated })
      }
    }
  }
})
