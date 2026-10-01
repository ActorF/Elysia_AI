/** Verify the desktop-pet Preload exposes only its three closed native actions. */

const assert = require('node:assert/strict')
const Module = require('node:module')
const path = require('node:path')
const test = require('node:test')

const invocations = []
const exposedApis = []
const electronMock = {
  contextBridge: {
    /** Record the single isolated world capability without installing a global. */
    exposeInMainWorld(name, api) {
      exposedApis.push({ api, name })
    },
  },
  ipcRenderer: {
    /** Record closed request channels so the test never reaches native Electron. */
    invoke(channel) {
      invocations.push(channel)
      return Promise.resolve()
    },
  },
}

const originalLoad = Module._load
Module._load = function patchedModuleLoad(request, parent, isMain) {
  if (request === 'electron') return electronMock
  return originalLoad.call(this, request, parent, isMain)
}

try {
  require(path.resolve(
    __dirname,
    '..',
    'dist-electron',
    'desktop-pet-preload.cjs',
  ))
} finally {
  Module._load = originalLoad
}

test('desktop-pet Preload exposes one frozen least-privilege API', () => {
  assert.equal(exposedApis.length, 1)
  assert.equal(exposedApis[0].name, 'elysiaDesktopPet')
  assert.equal(Object.isFrozen(exposedApis[0].api), true)
  assert.deepEqual(Object.keys(exposedApis[0].api).sort(), [
    'hide',
    'openMainChat',
    'ready',
  ])
  assert.equal(Object.hasOwn(exposedApis[0].api, 'backend'), false)
  assert.equal(Object.hasOwn(exposedApis[0].api, 'filesystem'), false)
  assert.equal(Object.hasOwn(exposedApis[0].api, 'ipcRenderer'), false)
})

test('desktop-pet actions invoke only their closed Main channels', async () => {
  const api = exposedApis[0].api
  await api.ready()
  await api.hide()
  await api.openMainChat()

  assert.deepEqual(invocations, [
    'desktop-pet:ready',
    'desktop-pet:hide',
    'desktop-pet:open-main-chat',
  ])
})
