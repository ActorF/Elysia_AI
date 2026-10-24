/** Verify native language synchronization preserves consent defaults, exact details, and trusted IPC admission. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test, { afterEach } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import {
  initializeNativeLocale,
  localizeNativeDialog,
  nativeText,
  parseNativeLocale,
  setNativeLocale,
} from '../dist-electron/native-locale.js'
import { confirmGithubIssueNative } from '../dist-electron/internet-write-native.js'

afterEach(() => { setNativeLocale('en') })

test('only supported renderer locale values replace the native preference', () => {
  initializeNativeLocale('zh-TW')
  assert.equal(nativeText('Cancel'), '取消')
  setNativeLocale('en')
  assert.equal(nativeText('Cancel'), 'Cancel')
  for (const value of [null, undefined, {}, ['en'], '', 'zh', 'fr', 'EN', 'en-US']) {
    assert.throws(() => parseNativeLocale(value), /invalid/u)
    assert.throws(() => setNativeLocale(value), /invalid/u)
    assert.equal(nativeText('Cancel'), 'Cancel')
  }
})

test('localized native dialogs retain action ordering, safe default, file scope and literal details', () => {
  setNativeLocale('zh-CN')
  const signal = new AbortController().signal
  const options = {
    type: 'question', title: 'Allow Work action?', message: 'A user supplied exact review',
    detail: 'C:\\Private\\{name}\\资料.txt\n$& ${literal}\nDo not alter this data.',
    buttons: ['Cancel', 'Allow'], defaultId: 0, cancelId: 0, noLink: true, signal,
    defaultPath: 'C:\\Private\\资料.txt',
    filters: [{ name: 'Supported files', extensions: ['txt', 'md'] }],
  }
  const translated = localizeNativeDialog(options)
  assert.equal(translated.title, '允许工作操作？')
  assert.deepEqual(translated.buttons, ['取消', '允许'])
  assert.equal(translated.defaultId, 0)
  assert.equal(translated.cancelId, 0)
  assert.equal(translated.signal, signal)
  assert.equal(translated.detail, options.detail)
  assert.equal(translated.message, options.message)
  assert.equal(translated.defaultPath, options.defaultPath)
  assert.deepEqual(translated.filters[0].extensions, options.filters[0].extensions)
  assert.deepEqual(options.buttons, ['Cancel', 'Allow'])
  assert.equal(nativeText('Open {application}?', { application: '$& {application}' }), '打开$& {application}？')
})

test('Chinese GitHub consent preserves exact target and escaped payload and still requires the affirmative index', async () => {
  setNativeLocale('zh-CN')
  const preview = {
    id: `internet_approval_${'2'.repeat(32)}`, scope: { chatId: 'chat_locale', projectId: null },
    owner: 'octocat', repo: 'Hello-World', title: 'Exact title $& {repo}',
    body: 'First\n第二行\u202e', expiresAt: new Date(Date.now() + 300000).toISOString(),
  }
  let options
  assert.equal(await confirmGithubIssueNative(preview, async value => {
    options = value
    return { response: 0 }
  }), false)
  assert.equal(options.title, '发布 GitHub 议题')
  assert.deepEqual(options.buttons, ['取消', '创建此议题'])
  assert.equal(options.defaultId, 0)
  assert.equal(options.cancelId, 0)
  assert.ok(options.detail.includes('https://api.github.com/repos/octocat/Hello-World/issues'))
  assert.ok(options.detail.includes('Exact title $& {repo}'))
  assert.ok(options.detail.includes('First\\n第二行\\u202e'))
  assert.ok(options.detail.includes('无法撤销'))
  assert.ok(options.detail.includes('仅适用于这一条确切的议题'))
  assert.equal(await confirmGithubIssueNative(preview, async () => ({ response: 1 })), true)
})

test('the production locale IPC authenticates the sender before validating or changing native copy', async () => {
  const text = await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8')
  const source = ts.createSourceFile('main.ts', text, ts.ScriptTarget.Latest, true)
  const register = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'registerIpcHandlers')
  const handler = register.body.statements.find(node => ts.isExpressionStatement(node)
    && ts.isCallExpression(node.expression) && node.expression.arguments[0]?.text === 'window:set-interface-locale')
  assert.ok(handler)
  let callback, trayRefreshes = 0
  const host = {
    ipcMain: { handle: (_channel, value) => { callback = value } },
    assertTrustedSender: event => { if (event.trusted !== true) throw new Error('Untrusted sender.') },
    setNativeLocale,
    refreshTrayMenu: () => { trayRefreshes += 1 },
  }
  vm.runInNewContext(ts.transpileModule(handler.getText(source), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, host)
  assert.throws(() => callback({ trusted: false }, 'zh-CN'), /Untrusted/u)
  assert.equal(nativeText('Cancel'), 'Cancel')
  assert.throws(() => callback({ trusted: true }, 'fr'), /invalid/u)
  assert.equal(trayRefreshes, 0)
  callback({ trusted: true }, 'zh-CN')
  assert.equal(nativeText('Cancel'), '取消')
  assert.equal(trayRefreshes, 1)
})

test('fixed native notification copy follows the interface language without changing delivery options', async () => {
  const text = await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8')
  const source = ts.createSourceFile('main.ts', text, ts.ScriptTarget.Latest, true)
  const factory = source.statements.find(node => ts.isFunctionDeclaration(node)
    && node.name?.text === 'createFixedPresenceNotificationHandle')
  assert.ok(factory)
  const delivered = []
  const host = {
    Notification: function (options) { delivered.push(options); return { on() {}, off() {}, show() {}, close() {} } },
    PRESENCE_NOTIFICATION_ID: 'fixed-id', PRESENCE_NOTIFICATION_GROUP_ID: 'fixed-group',
    resolveApplicationIconPath: () => 'fixed-icon', nativeText,
  }
  vm.runInNewContext(ts.transpileModule(`${factory.getText(source)}\nglobalThis.makeNotification = createFixedPresenceNotificationHandle;`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, host)
  setNativeLocale('zh-CN')
  host.makeNotification('completion', {})
  host.makeNotification('reminder', {})
  assert.equal(delivered[0].body, '本地回复已就绪。')
  assert.equal(delivered[1].body, '准备好时，随时打开 Elysia。')
  setNativeLocale('en')
  host.makeNotification('completion', {})
  assert.equal(delivered[2].body, 'Your local reply is ready.')
  for (const options of delivered) {
    assert.equal(options.title, 'Elysia')
    assert.equal(options.id, 'fixed-id')
    assert.equal(options.groupId, 'fixed-group')
    assert.equal(options.silent, true)
    assert.equal(options.timeoutType, 'default')
    assert.equal(options.urgency, 'low')
  }
})
