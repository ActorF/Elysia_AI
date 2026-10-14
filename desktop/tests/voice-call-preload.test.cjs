/** Verify the standalone sandboxed call preload exposes only presentation and exact-call controls. */

const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

const CALL_ID = 'd95b7aaf-d40e-4208-9caa-191c80570d2c'
const STATE = {
  callId: CALL_ID, revision: 0, characterState: 'listening', emotion: 'caring',
  microphoneMuted: false, startedAtMs: 1_790_000_000_000, error: null, warning: null,
}
const source = readFileSync(path.resolve(__dirname, '..', 'dist-electron', 'voice-call-preload.cjs'), 'utf8')

/** Run the actual bundled preload in an isolated realm without filesystem or audio capabilities. */
function loadPreload() {
  const ipcRenderer = new EventEmitter()
  const invocations = []
  const exposed = new Map()
  const domListeners = new Map()
  ipcRenderer.invoke = async (channel, ...args) => {
    invocations.push([channel, ...args])
    return channel === 'voice-call:get-state' ? STATE : undefined
  }
  vm.runInNewContext(source, {
    require: (moduleName) => {
      assert.equal(moduleName, 'electron', 'sandboxed call preload has no other runtime dependency')
      return { contextBridge: { exposeInMainWorld: (name, value) => { exposed.set(name, value) } }, ipcRenderer }
    },
    addEventListener: (name, listener, options) => {
      assert.equal(options.once, true)
      domListeners.set(name, listener)
    },
  })
  return { api: exposed.get('elysiaVoiceCall'), exposed, ipcRenderer, invocations, domListeners }
}

test('exposes a frozen call bridge with no Desktop API, microphone, filesystem or audio surface', () => {
  const { api, exposed, invocations, domListeners } = loadPreload()
  assert.deepEqual([...exposed.keys()], ['elysiaVoiceCall'])
  assert.deepEqual(Object.keys(api).sort(), ['end', 'getState', 'onState', 'setMuted'])
  assert.equal(Object.isFrozen(api), true)
  assert.deepEqual(invocations, [])
  domListeners.get('DOMContentLoaded')()
  assert.deepEqual(invocations, [['voice-call:ready']])
})

test('copies bounded state, drops malformed updates and removes its listener on unsubscribe', async () => {
  const { api, ipcRenderer } = loadPreload()
  assert.deepEqual(JSON.parse(JSON.stringify(await api.getState())), STATE)
  const states = []
  const unsubscribe = api.onState((state) => { states.push(state) })
  ipcRenderer.emit('voice-call:state', {}, { ...STATE, localPath: 'D:\\private.webp' })
  ipcRenderer.emit('voice-call:state', {}, { ...STATE, revision: 2 })
  assert.equal(states.length, 1)
  assert.equal(states[0].revision, 2)
  assert.equal(Object.isFrozen(states[0]), true)
  unsubscribe()
  ipcRenderer.emit('voice-call:state', {}, { ...STATE, revision: 3 })
  assert.equal(states.length, 1)
})

test('sends only explicit mute and hang up for validated call identities', async () => {
  const { api, invocations } = loadPreload()
  await api.setMuted(CALL_ID, true)
  await api.end(CALL_ID)
  assert.deepEqual(JSON.parse(JSON.stringify(invocations)), [
    ['voice-call:set-muted', { callId: CALL_ID, kind: 'mute', muted: true }],
    ['voice-call:end', { callId: CALL_ID, kind: 'end' }],
  ])
  await assert.rejects(api.setMuted(CALL_ID, 'yes'), /mute choice is invalid/u)
  assert.throws(() => api.end('old-call'), /identity is invalid/u)
  assert.equal(invocations.length, 2)
})
