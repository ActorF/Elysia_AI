/** Verify native call readiness, identity checks, revision ordering and one-shot hang-up cleanup. */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test, { after } from 'node:test'

const windows = []
let nextWindowSettings = {}
let activeHandlers = null
const WORK_AREA = { x: 100, y: 20, width: 1_200, height: 800 }

/** Minimal Electron WebContents double with document and IPC ownership identity. */
class FakeWebContents extends EventEmitter {
  /** Initialize one isolated native renderer identity. */
  constructor() {
    super()
    this.mainFrame = { processId: windows.length + 10, routingId: 1, parent: null, url: '' }
    this.destroyed = false
    this.messages = []
  }

  /** Return whether the owning native renderer was destroyed. */
  isDestroyed() { return this.destroyed }
  /** Return the precise top-level document currently loaded. */
  getURL() { return this.mainFrame.url }
  /** Record safe outgoing state for revision and schema assertions. */
  send(channel, value) { this.messages.push([channel, value]) }
  /** Record the handler that rejects spawned child windows. */
  setWindowOpenHandler(handler) { this.windowOpenHandler = handler }
}

/** Native window double; readiness comes from the dedicated preload handler. */
class FakeWindow extends EventEmitter {
  /** Capture constructor policy and the selected failure/readiness scenario. */
  constructor(options) {
    super()
    this.options = options
    this.webContents = new FakeWebContents()
    this.destroyed = false
    this.shown = false
    this.focused = false
    this.settings = nextWindowSettings
    nextWindowSettings = {}
    windows.push(this)
  }

  /** Record that the native menu is removed from the call surface. */
  setMenu(value) { this.menu = value }
  /** Return whether the native owner was already retired. */
  isDestroyed() { return this.destroyed }
  /** Simulate exact document loading, preload readiness or a fixed load failure. */
  async loadURL(url) {
    this.webContents.mainFrame.url = url
    if (this.settings.failLoad) {
      throw new Error('private loader diagnostic D:\\private')
    }
    this.webContents.emit('did-finish-load')
    if (!this.settings.delayReady) {
      activeHandlers.get('voice-call:ready')(eventFor(this))
    }
  }
  /** Record a successful native reveal only after ready controls exist. */
  show() { this.shown = true }
  /** Record focus after a successful load and preload handshake. */
  focus() { this.focused = true }
  /** Retire the renderer and emit the same closed event as a native user close. */
  destroy() {
    this.destroyed = true
    this.webContents.destroyed = true
    this.emit('closed')
  }
}

const electronMock = test.mock.module('electron', {
  exports: {
    BrowserWindow: FakeWindow,
    screen: { getDisplayMatching: () => ({ workArea: WORK_AREA }) },
  },
})
const { VoiceCallWindowManager } = await import('../dist-electron/voice-call-window.js')
const { VOICE_CALL_IPC } = await import('../dist-electron/voice-call-contracts.js')
after(() => { electronMock.restore() })

const CALL_ID = 'd95b7aaf-d40e-4208-9caa-191c80570d2c'
const OTHER_CALL_ID = 'b06e81ca-ae0f-45ba-9f26-a4a2d328540b'
const STATE = {
  callId: CALL_ID, revision: 0, characterState: 'listening', emotion: 'caring',
  microphoneMuted: false, startedAtMs: 1_790_000_000_000, error: null, warning: null,
}

/** Build the exact trusted top-level frame event for one call renderer. */
function eventFor(window, overrides = {}) {
  return { sender: window.webContents, senderFrame: window.webContents.mainFrame, ...overrides }
}

/** Create an isolated manager and capture its IPC handlers and audio-owner actions. */
function makeManager(options = {}) {
  const handlers = new Map()
  activeHandlers = handlers
  const actions = []
  const mainWindow = {
    isDestroyed: () => false,
    getBounds: () => ({ x: 0, y: 0, width: 1_000, height: 700 }),
    webContents: {},
  }
  const manager = new VoiceCallWindowManager({
    policy: { appPath: 'D:\\Elysia_AI\\desktop', isPackaged: false, platform: 'win32', developmentUrl: 'http://localhost:5173' },
    preloadPath: 'D:\\Elysia_AI\\desktop\\dist-electron\\voice-call-preload.cjs',
    canOpen: options.canOpen,
    currentMainWindow: () => mainWindow,
    onAction: (action) => {
      actions.push(action)
      if (options.throwAction) throw new Error('private Main delivery failure')
    },
  })
  manager.registerIpcHandlers({ handle: (channel, handler) => { handlers.set(channel, handler) } }, (event) => {
    if (event.sender !== mainWindow.webContents) throw new Error('untrusted main')
  })
  return { manager, actions, handlers, mainWindow }
}

test('creates an independent portrait frameless window with native resizing and its dedicated sandbox preload', async () => {
  const { manager } = makeManager()
  await manager.open(STATE)
  const window = windows.at(-1)
  assert.equal(window.options.parent, undefined)
  assert.equal(window.options.frame, false)
  assert.equal(window.options.titleBarStyle, undefined)
  assert.equal(window.options.titleBarOverlay, undefined)
  assert.notEqual(window.options.thickFrame, false)
  assert.equal(window.options.movable, true)
  assert.equal(window.options.resizable, true)
  assert.equal(window.options.minimizable, true)
  assert.equal(window.options.maximizable, true)
  assert.deepEqual([window.options.width, window.options.height], [360, 610])
  assert.deepEqual([window.options.minWidth, window.options.minHeight], [320, 360])
  assert.equal(window.options.webPreferences.sandbox, true)
  assert.equal(window.options.webPreferences.contextIsolation, true)
  assert.equal(window.options.webPreferences.nodeIntegration, false)
  assert.match(window.options.webPreferences.preload, /voice-call-preload\.cjs$/u)
  assert.equal(window.webContents.getURL(), 'http://localhost:5173/voice-call.html')
  assert.equal(window.shown, true)
  assert.equal(window.menu, null)
  assert.deepEqual(window.webContents.windowOpenHandler(), { action: 'deny' })
  manager.dispose()
})

test('bounds portrait dimensions and resize minima to the active display work area', async () => {
  const previousWorkArea = { ...WORK_AREA }
  const { manager } = makeManager()
  try {
    Object.assign(WORK_AREA, { x: 50, y: 40, width: 280, height: 330 })
    await manager.open(STATE)
    const window = windows.at(-1)
    assert.deepEqual([window.options.width, window.options.height], [280, 330])
    assert.deepEqual([window.options.minWidth, window.options.minHeight], [280, 330])
    assert.deepEqual([window.options.x, window.options.y], [50, 40])
    assert.equal(window.options.frame, false)
    assert.equal(window.options.resizable, true)
  } finally {
    manager.dispose()
    Object.assign(WORK_AREA, previousWorkArea)
  }
})

test('waits for the dedicated preload before allowing microphone capture', async () => {
  const { manager, handlers } = makeManager()
  nextWindowSettings = { delayReady: true }
  let opened = false
  const opening = manager.open(STATE).then(() => { opened = true })
  await new Promise((resolve) => setImmediate(resolve))
  const window = windows.at(-1)
  assert.equal(opened, false)
  assert.equal(window.shown, false)
  handlers.get(VOICE_CALL_IPC.ready)(eventFor(window))
  await opening
  assert.equal(window.shown, true)
  manager.dispose()
})

test('rejects unrelated windows, subframes, wrong frame identity and altered entry URLs', async () => {
  const { manager, handlers } = makeManager()
  await manager.open(STATE)
  const window = windows.at(-1)
  const handler = handlers.get(VOICE_CALL_IPC.getState)
  assert.deepEqual(handler(eventFor(window)), STATE)
  for (const overrides of [
    { sender: { mainFrame: window.webContents.mainFrame } },
    { senderFrame: null },
    { senderFrame: { ...window.webContents.mainFrame, parent: {} } },
    { senderFrame: { ...window.webContents.mainFrame, processId: 555 } },
    { senderFrame: { ...window.webContents.mainFrame, routingId: 555 } },
    { senderFrame: { ...window.webContents.mainFrame, url: 'http://localhost:5173/' } },
    { senderFrame: { ...window.webContents.mainFrame, url: 'http://localhost:5173/voice-call.html?borrow=desktop' } },
  ]) {
    assert.throws(() => handler(eventFor(window, overrides)), /untrusted/u)
  }
  manager.dispose()
})

test('ignores stale revisions and other call identities while preserving the timer origin', async () => {
  const { manager } = makeManager()
  await manager.open(STATE)
  const window = windows.at(-1)
  manager.update({ ...STATE, revision: 2, microphoneMuted: true })
  const count = window.webContents.messages.length
  manager.update({ ...STATE, revision: 1 })
  manager.update({ ...STATE, revision: 2 })
  manager.update({ ...STATE, callId: OTHER_CALL_ID, revision: 3 })
  assert.equal(window.webContents.messages.length, count)
  assert.equal(window.webContents.messages.at(-1)[1].microphoneMuted, true)
  assert.throws(() => manager.update({ ...STATE, revision: 3, startedAtMs: STATE.startedAtMs + 1 }), /cannot change/u)
  manager.dispose()
})

test('forwards explicit mute only for the live exact call and ends once on native close', async () => {
  const { manager, handlers, actions } = makeManager()
  await manager.open(STATE)
  const window = windows.at(-1)
  const mute = handlers.get(VOICE_CALL_IPC.setMuted)
  mute(eventFor(window), { callId: CALL_ID, kind: 'mute', muted: true })
  assert.deepEqual(actions, [{ callId: CALL_ID, kind: 'mute', muted: true }])
  assert.throws(() => mute(eventFor(window), { callId: OTHER_CALL_ID, kind: 'mute', muted: true }), /does not match/u)
  window.destroy()
  window.webContents.emit('render-process-gone')
  window.webContents.emit('did-fail-load', {}, -1, 'private', '', true)
  assert.deepEqual(actions, [{ callId: CALL_ID, kind: 'mute', muted: true }, { callId: CALL_ID, kind: 'end' }])
})

test('crashes, load failures and explicit hang up all close promptly with one exact-call end', async () => {
  for (const scenario of ['crash', 'failed-load', 'hang-up']) {
    const { manager, handlers, actions } = makeManager()
    await manager.open(STATE)
    const window = windows.at(-1)
    if (scenario === 'crash') window.webContents.emit('render-process-gone')
    if (scenario === 'failed-load') window.webContents.emit('did-fail-load', {}, -1, 'private', '', true)
    if (scenario === 'hang-up') handlers.get(VOICE_CALL_IPC.end)(eventFor(window), { callId: CALL_ID, kind: 'end' })
    assert.equal(window.destroyed, true)
    assert.deepEqual(actions, [{ callId: CALL_ID, kind: 'end' }])
  }
})

test('quiet main cleanup and delayed old controls cannot end a reopened call', async () => {
  const { manager, handlers, actions } = makeManager()
  await manager.open(STATE)
  const oldWindow = windows.at(-1)
  await manager.open({ ...STATE, callId: OTHER_CALL_ID })
  const newWindow = windows.at(-1)
  assert.equal(oldWindow.destroyed, true)
  assert.deepEqual(actions, [])
  manager.close(CALL_ID)
  assert.equal(newWindow.destroyed, false)
  assert.throws(() => handlers.get(VOICE_CALL_IPC.end)(eventFor(oldWindow), { callId: CALL_ID, kind: 'end' }), /untrusted/u)
  oldWindow.emit('closed')
  assert.deepEqual(actions, [])
  manager.close(OTHER_CALL_ID)
  assert.equal(newWindow.destroyed, true)
  assert.deepEqual(actions, [])
})

test('fails closed with safe guidance when native loading rejects before capture', async () => {
  const { manager, actions } = makeManager()
  nextWindowSettings = { failLoad: true }
  await assert.rejects(manager.open(STATE), /^Error: Voice Call window could not open\. Please try again\.$/u)
  assert.equal(windows.at(-1).destroyed, true)
  assert.deepEqual(actions, [{ callId: CALL_ID, kind: 'end' }])
})

test('main handlers reject child attempts to acquire Desktop window capabilities', async () => {
  const { manager, handlers, mainWindow } = makeManager()
  await manager.open(STATE)
  const childEvent = eventFor(windows.at(-1))
  await assert.rejects(handlers.get(VOICE_CALL_IPC.open)(childEvent, STATE), /untrusted main/u)
  assert.throws(() => handlers.get(VOICE_CALL_IPC.update)(childEvent, STATE), /untrusted main/u)
  assert.throws(() => handlers.get(VOICE_CALL_IPC.close)(childEvent, CALL_ID), /untrusted main/u)
  handlers.get(VOICE_CALL_IPC.close)({ sender: mainWindow.webContents }, CALL_ID)
  assert.equal(windows.at(-1).destroyed, true)
})

test('delivery failures cannot turn native hang up into an uncaught Main exception', async () => {
  const { manager, handlers, actions } = makeManager({ throwAction: true })
  await manager.open(STATE)
  const window = windows.at(-1)
  assert.throws(() => handlers.get(VOICE_CALL_IPC.setMuted)(eventFor(window), {
    callId: CALL_ID, kind: 'mute', muted: true,
  }), /^Error: Voice Call control could not reach the main window\.$/u)
  assert.doesNotThrow(() => window.webContents.emit('render-process-gone'))
  assert.equal(window.destroyed, true)
  assert.equal(actions.at(-1).kind, 'end')
})

test('application shutdown notifies a live audio owner once while document teardown stays quiet', async () => {
  const { manager, actions } = makeManager()
  await manager.open(STATE)
  const firstWindow = windows.at(-1)
  manager.endCurrent()
  manager.endCurrent()
  firstWindow.webContents.emit('render-process-gone')
  assert.equal(firstWindow.destroyed, true)
  assert.deepEqual(actions, [{ callId: CALL_ID, kind: 'end' }])
  await manager.open({ ...STATE, callId: OTHER_CALL_ID })
  const secondWindow = windows.at(-1)
  manager.dispose()
  manager.endCurrent()
  assert.equal(secondWindow.destroyed, true)
  assert.deepEqual(actions, [{ callId: CALL_ID, kind: 'end' }])
})

test('shutdown admission creates no new call window and retries work after shutdown failure', async () => {
  let shuttingDown = false
  const { manager, actions, handlers, mainWindow } = makeManager({ canOpen: () => !shuttingDown })
  await manager.open(STATE)
  const firstWindow = windows.at(-1)
  shuttingDown = true
  manager.endCurrent()
  const count = windows.length
  const openingState = { ...STATE, callId: OTHER_CALL_ID }
  await assert.rejects(handlers.get(VOICE_CALL_IPC.open)({ sender: mainWindow.webContents }, openingState), /shutting down/u)
  assert.equal(windows.length, count)
  assert.equal(firstWindow.destroyed, true)
  assert.deepEqual(actions, [{ callId: CALL_ID, kind: 'end' }])
  // Cleanup remains admitted while quit drains, even after its exact window
  // already closed. A failed shutdown restores opening through the same policy.
  handlers.get(VOICE_CALL_IPC.update)({ sender: mainWindow.webContents }, { ...STATE, revision: 1 })
  handlers.get(VOICE_CALL_IPC.close)({ sender: mainWindow.webContents }, CALL_ID)
  shuttingDown = false
  await manager.open(openingState)
  assert.equal(windows.length, count + 1)
  assert.equal(windows.at(-1).shown, true)
  manager.dispose()
})
