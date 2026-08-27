const { contextBridge } = require('electron')

const initialSnapshot = {
  revision: 0,
  status: 'starting',
  capabilities: [],
  models: [],
}

let snapshot = clone(initialSnapshot)
let selectedFiles = []
let nextRequestNumber = 1
let nextCallSequence = 1
let calls = []
let delayCharacterPanelChanges = false
let pendingCharacterPanelChanges = []
const backendListeners = new Set()

function clone(value) {
  if (value === undefined) {
    return undefined
  }
  return JSON.parse(JSON.stringify(value))
}

function record(method, args = []) {
  calls.push({
    sequence: nextCallSequence,
    method,
    args: clone(args),
  })
  nextCallSequence += 1
}

function releaseNextCharacterPanelChange() {
  const release = pendingCharacterPanelChanges.shift()
  release?.()
  return release !== undefined
}

function releaseAllCharacterPanelChanges() {
  while (releaseNextCharacterPanelChange()) {
    // Drain every test-controlled IPC completion before resetting the mock.
  }
}

const desktopApi = {
  rendererReady: async () => {
    record('rendererReady')
  },

  setThemePreference: async (theme) => {
    record('setThemePreference', [theme])
  },

  getSnapshot: async () => {
    record('getSnapshot')
    return clone(snapshot)
  },

  restartBackend: async () => {
    record('restartBackend')
    return clone(snapshot)
  },

  sendMessage: async (request) => {
    record('sendMessage', [request])
    const requestId = `test-request-${nextRequestNumber}`
    nextRequestNumber += 1
    return { requestId }
  },

  selectModel: async (modelName) => {
    record('selectModel', [modelName])
    snapshot = {
      ...snapshot,
      revision: snapshot.revision + 1,
      modelName,
    }
    return clone(snapshot)
  },

  chooseFiles: async () => {
    record('chooseFiles')
    return clone(selectedFiles)
  },

  setCharacterPanelOpen: async (open) => {
    record('setCharacterPanelOpen', [open])
    if (delayCharacterPanelChanges) {
      await new Promise((resolve) => {
        pendingCharacterPanelChanges.push(resolve)
      })
    }
  },

  onBackendEvent: (listener) => {
    record('onBackendEvent.subscribe')
    backendListeners.add(listener)
    return () => {
      backendListeners.delete(listener)
      record('onBackendEvent.unsubscribe')
    }
  },
}

const testControl = {
  reset: () => {
    releaseAllCharacterPanelChanges()
    snapshot = clone(initialSnapshot)
    selectedFiles = []
    nextRequestNumber = 1
    nextCallSequence = 1
    calls = []
    delayCharacterPanelChanges = false
  },

  setSnapshot: (nextSnapshot) => {
    snapshot = clone(nextSnapshot)
  },

  emitBackendEvent: (event) => {
    const nextEvent = clone(event)
    if (nextEvent.type === 'snapshot') {
      snapshot = clone(nextEvent.snapshot)
    }
    for (const listener of backendListeners) {
      listener(clone(nextEvent))
    }
  },

  setSelectedFiles: (files) => {
    selectedFiles = clone(files)
  },

  setCharacterPanelChangeDelay: (delayed) => {
    delayCharacterPanelChanges = delayed
    if (!delayed) {
      releaseAllCharacterPanelChanges()
    }
  },

  getPendingCharacterPanelChangeCount: () => (
    pendingCharacterPanelChanges.length
  ),

  releaseNextCharacterPanelChange,

  getCalls: () => clone(calls),

  clearCalls: () => {
    nextCallSequence = 1
    calls = []
  },
}

contextBridge.exposeInMainWorld('elysiaDesktop', desktopApi)
contextBridge.exposeInMainWorld('elysiaDesktopTest', testControl)
