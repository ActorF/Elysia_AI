/** Verify Electron desktop adapters use fixed native verbs, private review copies, and metadata-only window enumeration. */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { pathToFileURL } from 'node:url'
import test, { after } from 'node:test'

const calls = []
let clipboardText = 'Private clipboard.'
let selectedFiles = []
let openFailure = ''
const mocks = [
  test.mock.module('electron', { exports: {
    clipboard: { readText: () => { calls.push(['read']); return clipboardText },
      writeText: (text) => { calls.push(['write', text]); clipboardText = text } },
    dialog: { showOpenDialog: async (_window, options) => {
      calls.push(['picker', options]); return { canceled: selectedFiles.length === 0, filePaths: selectedFiles }
    } },
    shell: { openPath: async (path) => { calls.push(['open', path]); return openFailure } },
    desktopCapturer: { getSources: async (options) => { calls.push(['windows', options]); return [
      { id: 'window:private:0', name: 'Document', thumbnail: { privateImage: true }, appIcon: { privateIcon: true } },
    ] } },
  } }),
  test.mock.module('node:child_process', { exports: { spawn: (executable, args, options) => {
    calls.push(['spawn', executable, args, options])
    const child = new EventEmitter()
    child.unref = () => undefined
    queueMicrotask(() => child.emit('spawn'))
    return child
  } } }),
]
const url = process.env.ELYSIA_DESKTOP_NATIVE_TEST_MODULE
  ? pathToFileURL(process.env.ELYSIA_DESKTOP_NATIVE_TEST_MODULE)
  : new URL('../dist-electron/work-desktop-native.js', import.meta.url)
const { createWorkDesktopNativeCallbacks } = await import(url.href)
after(() => { for (const mock of mocks) mock.restore() })

function fixture(extra = {}) {
  calls.length = 0; selectedFiles = []; openFailure = ''
  const owner = { chatId: 'chat_work', projectId: null, workspacePath: 'C:\\Workspace', ready: true, mode: 'work', busy: false }
  const storage = { saveReview: async (document) => { calls.push(['copy', document]); return 'C:\\PrivateData\\reviews\\review.txt' },
    verifyReview: async (path) => { calls.push(['verify', path]) }, append: async (entry) => { calls.push(['audit', entry]) }, ...extra }
  const native = createWorkDesktopNativeCallbacks({ getOwner: () => owner, getStorage: () => storage, getWindow: () => ({ trusted: true }) })
  return { native, owner }
}

test('Native file picker provides one document path without arbitrary renderer setters or URLs', async () => {
  const { native } = fixture()
  assert.equal(await native.chooseFile(), null)
  selectedFiles = ['C:\\Workspace\\review.txt', 'C:\\Workspace\\other.txt']
  assert.equal(await native.chooseFile(), null)
  selectedFiles = ['C:\\Workspace\\review.txt']
  assert.equal(await native.chooseFile(), selectedFiles[0])
  assert.deepEqual(calls[0][1].properties, ['openFile', 'dontAddToRecent'])
  assert.deepEqual(calls[0][1].filters[0].extensions, ['txt', 'md', 'csv', 'json', 'pdf', 'docx'])
})

test('Native open receives only a saved verified private review copy and rejects stale late ownership', async () => {
  const document = { actionId: `desktop_action_${'a'.repeat(32)}`, name: 'review.txt', bytes: new Uint8Array([65]), sha256: 'b'.repeat(64) }
  const { native } = fixture()
  await native.openDocument(document)
  assert.deepEqual(calls.map((call) => call[0]), ['copy', 'verify', 'open'])
  assert.equal(calls.at(-1)[1], 'C:\\PrivateData\\reviews\\review.txt')
  openFailure = 'Native app rejected private path.'
  await assert.rejects(native.openDocument(document), /refused/u)
  const delayed = fixture({ verifyReview: async () => { delayed.owner.chatId = 'chat_other' } })
  await assert.rejects(delayed.native.openDocument(document), /changed/u)
  assert.equal(calls.some((call) => call[0] === 'open'), false)
})

test('Native clipboard adapters have no polling and windows explicitly omit thumbnails, icons and capture streams', async () => {
  const { native } = fixture()
  assert.equal(calls.length, 0)
  assert.equal(await native.readClipboard(), clipboardText)
  await native.writeClipboard('Exact approved text.')
  assert.equal(clipboardText, 'Exact approved text.')
  const metadata = await native.listWindows()
  assert.deepEqual(metadata, [{ sourceId: 'window:private:0', title: 'Document' }])
  assert.deepEqual(calls.at(-1), ['windows', { types: ['window'], thumbnailSize: { width: 0, height: 0 }, fetchWindowIcons: false }])
  assert.equal(JSON.stringify(calls).includes('getDisplayMedia'), false)
})

test('Notepad adapter refuses alternate commands/arguments and unavailable platforms without spawning', async () => {
  const { native } = fixture()
  await assert.rejects(native.launchNotepad('C:\\evil.exe', []), /unavailable/u)
  await assert.rejects(native.launchNotepad('C:\\Windows\\System32\\notepad.exe', ['secret.txt']), /unavailable/u)
  assert.equal(calls.some((call) => call[0] === 'spawn'), false)
})
