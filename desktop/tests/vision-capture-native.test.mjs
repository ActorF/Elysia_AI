/** Test isolated Electron capture grants, IPC/frame correlation, deny-all session reuse, and actual destruction leases using inert native objects. */
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import path from 'node:path'
import { mock } from 'node:test'
import test from 'node:test'
import { pathToFileURL } from 'node:url'

const handlers = new Map(), ipc = new EventEmitter(), windows = [], partitions = [], sessions = new Map()
ipc.handle = (name, callback) => handlers.set(name, callback)
ipc.removeHandler = name => handlers.delete(name)
let holdDestruction = false, sources = [{ id: 'window:8:0', name: 'Synthetic source', display_id: '' }], enumerations = []
class FakeContents extends EventEmitter {
  constructor() { super(); this.mainFrame = { url: '' }; this.destroyed = false }
  isDestroyed() { return this.destroyed }
  setWindowOpenHandler(callback) { this.windowOpen = callback }
  executeJavaScript() { return Promise.resolve(true) }
}
class FakeWindow extends EventEmitter {
  constructor(options) { super(); this.options = options; this.webContents = new FakeContents(); this.destroyed = false; windows.push(this) }
  isDestroyed() { return this.destroyed }
  loadFile(file) { this.webContents.mainFrame.url = pathToFileURL(file).href; return Promise.resolve() }
  destroy() { this.destroyRequested = true; this.destroyed = true; this.emit('closed'); if (!holdDestruction) this.finishDestruction() }
  finishDestruction() { this.webContents.destroyed = true; this.webContents.emit('destroyed') }
}
function privateSession(name) {
  partitions.push(name)
  if (!sessions.has(name)) {
    const result = new EventEmitter()
    result.webRequest = { onBeforeRequest: (_filter, callback) => { result.network = callback } }
    result.setDisplayMediaRequestHandler = callback => { result.media = callback }
    result.setPermissionCheckHandler = callback => { result.check = callback }
    result.setPermissionRequestHandler = callback => { result.permission = callback }
    sessions.set(name, result)
  }
  return sessions.get(name)
}
mock.module('electron', { namedExports: {
  BrowserWindow: FakeWindow, ipcMain: ipc, session: { fromPartition: privateSession },
  desktopCapturer: { getSources: async options => { enumerations.push(options); return sources.map(item => ({ ...item })) } },
  screen: { getAllDisplays: () => [{ id: 1, size: { width: 100, height: 100 }, scaleFactor: 1,
    bounds: { x: 0, y: 0, width: 100, height: 100 }, rotation: 0 }] },
} })
const moduleUrl = process.env.ELYSIA_VISION_CAPTURE_NATIVE_TEST_MODULE
  ? pathToFileURL(process.env.ELYSIA_VISION_CAPTURE_NATIVE_TEST_MODULE).href : new URL('../dist-electron/vision-capture-native.js', import.meta.url).href
const { createVisionCaptureNative } = await import(moduleUrl)
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6fS4AAAAASUVORK5CYII=', 'base64')
const tick = () => new Promise(resolve => setImmediate(resolve))
const isCode = code => error => error.code === code
function options(timeoutMs = 20000) { return { helperDocumentPath: path.resolve('public/vision-capture.html'),
  preloadPath: path.resolve('dist-electron/vision-capture-preload.cjs'), timeoutMs } }
async function begin(timeoutMs = 20000) {
  const native = createVisionCaptureNative(options(timeoutMs)), source = (await native.listSources())[0]
  const aborter = new AbortController()
  const pending = native.captureFrame(source, null, aborter.signal, () => true)
  await tick()
  const window = windows.at(-1), contents = window.webContents, session = window.options.webPreferences.session
  const job = window.options.webPreferences.additionalArguments[0].slice('--elysia-vision-capture-job='.length)
  const prefix = `vision-capture-private:${job}:`
  const event = { sender: contents, senderFrame: contents.mainFrame, returnValue: undefined }
  const configuration = () => { ipc.emit(`${prefix}configuration`, event); return event.returnValue }
  const permission = (mediaTypes, changed = {}) => {
    let granted
    session.permission(contents, 'media', value => { granted = value },
      { isMainFrame: true, requestingUrl: contents.mainFrame.url, mediaTypes, ...changed })
    return granted
  }
  const display = async (changed = {}) => {
    let result
    session.media({ frame: contents.mainFrame, securityOrigin: 'file:///', userGesture: true,
      videoRequested: true, audioRequested: false, ...changed }, value => { result = value })
    await tick(); return result
  }
  return { native, source, pending, aborter, window, contents, session, prefix, event, configuration, permission, display }
}
async function grant(f) { assert.notEqual(f.configuration(), null); assert.equal(f.permission([]), true); assert.equal((await f.display()).video.id, f.source.nativeId) }
async function publish(f, value = { bytes: png, width: 1, height: 1 }) { handlers.get(`${f.prefix}finish`)(f.event, value); return f.pending }

test('Enumeration skips every thumbnail/icon and removes native paths from source display labels without starting a helper', async () => {
  const count = windows.length
  sources = [{ id: 'window:8:0', name: 'C:\\private\\secret.txt - Editor', display_id: '' }]
  const result = await createVisionCaptureNative(options()).listSources()
  assert.equal(result[0].label, 'Window 1')
  assert.equal(windows.length, count)
  assert.deepEqual(enumerations.at(-1), { types: ['screen', 'window'], thumbnailSize: { width: 0, height: 0 }, fetchWindowIcons: false })
  sources = [{ id: 'window:8:0', name: 'Synthetic source', display_id: '' }]
})

test('Private permission grants require configured exact frame/document, desktop empty types, and one use; camera/mic and unknown remain denied', async () => {
  const f = await begin()
  assert.equal(f.permission([]), false)
  assert.deepEqual(await f.display(), {})
  assert.notEqual(f.configuration(), null)
  for (const types of [['video'], ['audio'], ['audio', 'video'], ['unknown'], undefined]) assert.equal(f.permission(types), false)
  assert.equal(f.permission([], { isMainFrame: false }), false)
  assert.equal(f.permission([], { requestingUrl: 'https://other.example/' }), false)
  assert.equal(f.permission([]), true); assert.equal(f.permission([]), false)
  assert.equal(f.session.check(f.contents, 'media', 'file:///', { isMainFrame: true }), false)
  for (const changed of [{ userGesture: false }, { audioRequested: true }, { videoRequested: false },
    { frame: { url: f.contents.mainFrame.url } }, { securityOrigin: 'https://other.example' }]) assert.deepEqual(await f.display(changed), {})
  assert.equal((await f.display()).video.id, f.source.nativeId)
  assert.deepEqual(await f.display(), {})
  const result = await publish(f)
  assert.deepEqual(result.pngBytes, png)
})

test('Private IPC refuses another renderer and stops granting once helper destruction begins', async () => {
  const f = await begin()
  const foreign = { sender: new FakeContents(), senderFrame: f.contents.mainFrame }
  ipc.emit(`${f.prefix}configuration`, foreign)
  assert.equal(foreign.returnValue, null)
  await grant(f)
  assert.throws(() => handlers.get(`${f.prefix}finish`)(foreign, { bytes: png, width: 1, height: 1 }), isCode('permission_denied'))
  assert.equal(f.window.destroyRequested, undefined)
  await publish(f)
  assert.equal(handlers.has(`${f.prefix}finish`), false)
  assert.equal(ipc.listenerCount(`${f.prefix}configuration`), 0)
  assert.equal(f.session.listenerCount('will-download'), 0)
  let network
  f.session.network({ url: f.contents.mainFrame.url }, value => { network = value })
  assert.equal(network.cancel, true)
  let granted
  f.session.permission(f.contents, 'media', value => { granted = value }, { isMainFrame: true, mediaTypes: [] })
  assert.equal(granted, false)
  assert.deepEqual(await f.display(), {})
})

test('Owned WebContents destruction determines physical lease release, including cancellation and replacement adapters', async () => {
  holdDestruction = true
  const f = await begin()
  const refused = assert.rejects(f.pending, isCode('cancelled'))
  f.aborter.abort()
  assert.equal(f.window.destroyRequested, true)
  const next = createVisionCaptureNative(options())
  await assert.rejects(next.captureFrame(f.source, null, new AbortController().signal, () => true), isCode('busy'))
  assert.equal(f.contents.isDestroyed(), false)
  f.window.finishDestruction(); await refused
  holdDestruction = false
  const g = await begin()
  await grant(g); await publish(g)
  assert.equal(f.session, g.session)
  assert.equal(partitions.every(name => name === 'elysia-vision-capture'), true)
  assert.equal(g.session.listenerCount('will-download'), 0)
})

test('A changed source during native admission, malformed frame and timeout destroy the helper and never publish pixels', async () => {
  const f = await begin()
  const refused = assert.rejects(f.pending, isCode('owner_changed'))
  f.configuration(); f.permission([])
  sources = [{ ...sources[0], name: 'Changed window' }]
  assert.deepEqual(await f.display(), {})
  await refused
  sources = [{ id: 'window:8:0', name: 'Synthetic source', display_id: '' }]
  const g = await begin()
  await grant(g)
  const invalid = assert.rejects(g.pending, isCode('invalid_image'))
  handlers.get(`${g.prefix}finish`)(g.event, { bytes: png, width: 8192, height: 8192 })
  await invalid
  holdDestruction = true
  const h = await begin(80)
  const timedOut = assert.rejects(h.pending, isCode('timeout'))
  await new Promise(resolve => setTimeout(resolve, 100))
  assert.equal(h.window.destroyRequested, true)
  assert.equal(h.contents.isDestroyed(), false)
  h.window.finishDestruction(); await timedOut
  holdDestruction = false
})

test('The helper blocks every URL other than its two exact local artifacts and refuses external navigation, popup and webview requests', async () => {
  const f = await begin()
  assert.deepEqual(f.contents.windowOpen(), { action: 'deny' })
  for (const url of ['https://example.com/', 'http://127.0.0.1:11434/api/chat', 'file:///private.txt', 'file:///C:/private.png']) {
    let result
    f.session.network({ url }, value => { result = value })
    assert.equal(result.cancel, true)
  }
  let result
  f.session.network({ url: f.contents.mainFrame.url }, value => { result = value })
  assert.equal(result.cancel, false)
  let prevented = false
  f.contents.emit('will-attach-webview', { preventDefault: () => { prevented = true } })
  assert.equal(prevented, true)
  const refused = assert.rejects(f.pending, isCode('unavailable'))
  f.contents.emit('will-navigate', { preventDefault: () => {} }, 'https://other.example/')
  await refused
})
