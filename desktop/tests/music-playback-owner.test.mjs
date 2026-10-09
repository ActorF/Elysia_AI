/** Verify trusted Electron music playback ownership and private IPC settlement. */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test, { after } from 'node:test'

const PLAY_CHANNEL = 'elysia:trusted-music-play:v1'
const CANCEL_CHANNEL = 'elysia:trusted-music-cancel:v1'
const SETTLED_CHANNEL = 'elysia:trusted-music-settled:v1'
const PLAYBACK_TIMEOUT_MS = (13 * 60 * 1_000) + 30_000
const PLAYBACK_ID_PATTERN = (
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u
)

const ipcMain = new EventEmitter()
const electronMock = test.mock.module('electron', {
  exports: { ipcMain },
})
const { PreloadMusicPlaybackOwner } = await import(
  '../dist-electron/music-playback-owner.js'
)

after(() => {
  electronMock.restore()
})

class FakeWebContents extends EventEmitter {
  constructor() {
    super()
    this.destroyed = false
    this.mainFrame = Object.freeze({ frame: 'main' })
    this.sent = []
    this.sendAttempts = []
    this.sendFailure = false
  }

  isDestroyed() {
    return this.destroyed
  }

  send(...message) {
    this.sendAttempts.push(message)
    if (this.sendFailure) {
      throw new Error('private native send failure')
    }
    this.sent.push(message)
  }
}

class FakeWindow {
  constructor() {
    this.destroyed = false
    this.webContents = new FakeWebContents()
  }

  isDestroyed() {
    return this.destroyed
  }
}

function createOwner(onDisconnected = () => {}) {
  const window = new FakeWindow()
  return {
    owner: new PreloadMusicPlaybackOwner(window, onDisconnected),
    window,
  }
}

function playbackIdOf(window, sendIndex = 0) {
  const [channel, metadata, bytes] = window.webContents.sent[sendIndex]
  assert.equal(channel, PLAY_CHANNEL)
  assert.deepEqual(
    Object.keys(metadata).sort(),
    ['byteLength', 'outputDeviceId', 'playbackId', 'volumePercent'],
  )
  assert.match(metadata.playbackId, PLAYBACK_ID_PATTERN)
  assert.ok(bytes instanceof Uint8Array)
  return metadata.playbackId
}

function emitSettlement(window, value, overrides = {}) {
  ipcMain.emit(SETTLED_CHANNEL, {
    sender: overrides.sender ?? window.webContents,
    senderFrame: overrides.senderFrame ?? window.webContents.mainFrame,
  }, value)
}

async function assertStillPending(operation) {
  const outcome = await Promise.race([
    operation.then(
      () => 'resolved',
      () => 'rejected',
    ),
    new Promise((resolve) => setImmediate(() => resolve('pending'))),
  ])
  assert.equal(outcome, 'pending')
}

function matchesPlaybackError(message) {
  return (error) => (
    error instanceof Error
    && error.name === 'Error'
    && error.message === message
    && !error.message.includes('native')
    && !error.message.includes('D:')
  )
}

test('sends copied MP3 bytes with closed metadata and trusts only the owning main frame', async () => {
  const { owner, window } = createOwner()
  const source = Uint8Array.from([1, 2, 3, 4])
  const operation = owner.play(source, 'speaker-usb', 37)
  assert.equal(owner.hasActivePlayback(), true)
  const playbackId = playbackIdOf(window)
  const [, metadata, sentBytes] = window.webContents.sent[0]
  assert.deepEqual(metadata, {
    playbackId,
    byteLength: 4,
    outputDeviceId: 'speaker-usb',
    volumePercent: 37,
  })
  assert.notEqual(sentBytes, source)
  source.fill(9)
  assert.deepEqual([...sentBytes], [1, 2, 3, 4])

  try {
    const forgedSender = new FakeWebContents()
    emitSettlement(window, { playbackId, status: 'played' }, {
      sender: forgedSender,
      senderFrame: forgedSender.mainFrame,
    })
    await assertStillPending(operation)

    emitSettlement(window, { playbackId, status: 'played' }, {
      senderFrame: Object.freeze({ frame: 'subframe' }),
    })
    await assertStillPending(operation)

    emitSettlement(window, { playbackId, status: 'played' })
    await operation
    assert.equal(owner.hasActivePlayback(), false)
  } finally {
    owner.dispose()
    await operation.catch(() => {})
  }
})

test('failed settlement is recoverable while malformed settlement disconnects safely', async () => {
  const disconnects = []
  let owner
  const fixture = createOwner((disconnectedOwner) => {
    assert.equal(disconnectedOwner, owner)
    disconnects.push(disconnectedOwner)
  })
  owner = fixture.owner
  const failed = owner.play(Uint8Array.from([1]), null, 100)
  const failedId = playbackIdOf(fixture.window)
  emitSettlement(fixture.window, { playbackId: failedId, status: 'failed' })
  await assert.rejects(failed, matchesPlaybackError('Song playback failed.'))
  assert.equal(owner.hasActivePlayback(), false)
  assert.deepEqual(disconnects, [])

  const malformed = owner.play(Uint8Array.from([2]), null, 100)
  const malformedId = playbackIdOf(fixture.window, 1)
  try {
    emitSettlement(fixture.window, {
      playbackId: malformedId,
      status: 'played',
      path: 'D:/private/cover.mp3',
    })
    await assert.rejects(
      malformed,
      matchesPlaybackError('Song playback is not available.'),
    )
    assert.deepEqual(disconnects, [owner])
    assert.equal(owner.hasActivePlayback(), false)
    await assert.rejects(
      owner.play(Uint8Array.from([3]), null, 100),
      matchesPlaybackError('Song playback is not available.'),
    )
  } finally {
    owner.dispose()
    await malformed.catch(() => {})
  }
})

test('cancel rejects locally and a stale settlement cannot settle its replacement', async () => {
  const { owner, window } = createOwner()
  const first = owner.play(Uint8Array.from([1]), null, 100)
  const firstId = playbackIdOf(window)
  owner.cancel()
  await assert.rejects(
    first,
    matchesPlaybackError('Song playback was cancelled.'),
  )
  assert.deepEqual(window.webContents.sent[1], [CANCEL_CHANNEL, firstId])
  assert.equal(owner.hasActivePlayback(), false)

  const second = owner.play(Uint8Array.from([2]), 'speaker-next', 0)
  const secondId = playbackIdOf(window, 2)
  try {
    emitSettlement(window, { playbackId: firstId, status: 'failed' })
    await assertStillPending(second)

    emitSettlement(window, { playbackId: secondId, status: 'played' })
    await second
    assert.notEqual(firstId, secondId)
  } finally {
    owner.dispose()
    await second.catch(() => {})
  }
})

test('cancel send failure disconnects the owner and makes playback unavailable', async () => {
  const disconnects = []
  let owner
  const fixture = createOwner((disconnectedOwner) => {
    assert.equal(disconnectedOwner, owner)
    disconnects.push(disconnectedOwner)
  })
  owner = fixture.owner
  const operation = owner.play(Uint8Array.from([1]), null, 100)
  playbackIdOf(fixture.window)
  fixture.window.webContents.sendFailure = true

  try {
    owner.cancel()
    await assert.rejects(
      operation,
      matchesPlaybackError('Song playback is not available.'),
    )
    assert.deepEqual(disconnects, [owner])
    assert.equal(owner.hasActivePlayback(), false)

    await assert.rejects(
      owner.play(Uint8Array.from([2]), null, 100),
      matchesPlaybackError('Song playback is not available.'),
    )
    assert.deepEqual(
      fixture.window.webContents.sendAttempts.map(([channel]) => channel),
      [PLAY_CHANNEL, CANCEL_CHANNEL],
    )
    assert.equal(disconnects.length, 1)
  } finally {
    owner.dispose()
    await operation.catch(() => {})
  }
})

test('rejects invalid requests and concurrent playback before private IPC', async () => {
  const { owner, window } = createOwner()
  const invalidRequests = [
    [new Uint8Array(), null, 100],
    [Uint8Array.from([1]), '', 100],
    [Uint8Array.from([1]), 'speaker\u0000private', 100],
    [Uint8Array.from([1]), 's'.repeat(2_049), 100],
    [Uint8Array.from([1]), null, -1],
    [Uint8Array.from([1]), null, 101],
    [Uint8Array.from([1]), null, 1.5],
  ]
  for (const [bytes, outputDeviceId, volumePercent] of invalidRequests) {
    await assert.rejects(
      owner.play(bytes, outputDeviceId, volumePercent),
      matchesPlaybackError('Song playback request is invalid.'),
    )
  }
  assert.deepEqual(window.webContents.sent, [])

  const active = owner.play(Uint8Array.from([4]), null, 50)
  try {
    await assert.rejects(
      owner.play(Uint8Array.from([5]), null, 50),
      matchesPlaybackError('Song playback is not available.'),
    )
    assert.equal(window.webContents.sent.length, 1)
    owner.cancel()
    await assert.rejects(
      active,
      matchesPlaybackError('Song playback was cancelled.'),
    )
  } finally {
    owner.dispose()
    await active.catch(() => {})
  }
})

test('private send failure disconnects the owner without leaking diagnostics', async () => {
  const disconnects = []
  let owner
  const fixture = createOwner((disconnectedOwner) => {
    assert.equal(disconnectedOwner, owner)
    disconnects.push(disconnectedOwner)
  })
  owner = fixture.owner
  fixture.window.webContents.sendFailure = true
  const operation = owner.play(Uint8Array.from([1]), null, 100)

  try {
    await assert.rejects(
      operation,
      matchesPlaybackError('Song playback is not available.'),
    )
    assert.deepEqual(disconnects, [owner])
    assert.equal(owner.hasActivePlayback(), false)
  } finally {
    owner.dispose()
    await operation.catch(() => {})
  }
})

for (const [name, disconnect] of [
  [
    'renderer crash',
    (webContents) => webContents.emit('render-process-gone'),
  ],
  [
    'WebContents destruction',
    (webContents) => webContents.emit('destroyed'),
  ],
]) {
  test(`${name} rejects pending playback and detaches private listeners`, async () => {
    const disconnects = []
    let owner
    const fixture = createOwner((disconnectedOwner) => {
      assert.equal(disconnectedOwner, owner)
      disconnects.push(disconnectedOwner)
    })
    owner = fixture.owner
    const operation = owner.play(Uint8Array.from([1]), null, 100)
    try {
      disconnect(fixture.window.webContents)
      await assert.rejects(
        operation,
        matchesPlaybackError('Song playback is not available.'),
      )
      assert.deepEqual(disconnects, [owner])
      assert.equal(fixture.window.webContents.listenerCount('destroyed'), 0)
      assert.equal(
        fixture.window.webContents.listenerCount('render-process-gone'),
        0,
      )
      assert.equal(
        fixture.window.webContents.listenerCount('did-start-navigation'),
        0,
      )
    } finally {
      owner.dispose()
      await operation.catch(() => {})
    }
  })
}

test('only cross-document main-frame navigation disconnects playback', async () => {
  const disconnects = []
  let owner
  const fixture = createOwner((disconnectedOwner) => {
    assert.equal(disconnectedOwner, owner)
    disconnects.push(disconnectedOwner)
  })
  owner = fixture.owner
  const operation = owner.play(Uint8Array.from([1]), null, 100)
  playbackIdOf(fixture.window)
  try {
    fixture.window.webContents.emit(
      'did-start-navigation',
      {},
      'file:///subframe.html',
      false,
      false,
    )
    await assertStillPending(operation)

    fixture.window.webContents.emit(
      'did-start-navigation',
      {},
      'file:///index.html#main-content',
      true,
      true,
    )
    await assertStillPending(operation)

    fixture.window.webContents.emit(
      'did-start-navigation',
      {},
      'file:///replacement.html',
      false,
      true,
    )
    await assert.rejects(
      operation,
      matchesPlaybackError('Song playback is not available.'),
    )
    assert.deepEqual(disconnects, [owner])
  } finally {
    owner.dispose()
    await operation.catch(() => {})
  }
})

test('playback timeout disconnects the owner at the long-form hard limit', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const disconnects = []
  let owner
  const fixture = createOwner((disconnectedOwner) => {
    assert.equal(disconnectedOwner, owner)
    disconnects.push(disconnectedOwner)
  })
  owner = fixture.owner
  const operation = owner.play(Uint8Array.from([1]), null, 100)
  const playbackId = playbackIdOf(fixture.window)
  try {
    t.mock.timers.tick(PLAYBACK_TIMEOUT_MS - 1)
    await assertStillPending(operation)
    t.mock.timers.tick(1)
    await assert.rejects(
      operation,
      matchesPlaybackError('Song playback is not available.'),
    )
    assert.deepEqual(
      fixture.window.webContents.sent.at(-1),
      [CANCEL_CHANNEL, playbackId],
    )
    assert.deepEqual(disconnects, [owner])
  } finally {
    owner.dispose()
    await operation.catch(() => {})
  }
})

test('dispose is idempotent, removes listeners, and rejects future playback', async () => {
  const { owner, window } = createOwner()
  const operation = owner.play(Uint8Array.from([1]), null, 100)
  owner.dispose()
  owner.dispose()

  await assert.rejects(
    operation,
    matchesPlaybackError('Song playback is not available.'),
  )
  assert.equal(ipcMain.listenerCount(SETTLED_CHANNEL), 0)
  assert.equal(window.webContents.listenerCount('destroyed'), 0)
  assert.equal(window.webContents.listenerCount('render-process-gone'), 0)
  assert.equal(window.webContents.listenerCount('did-start-navigation'), 0)
  await assert.rejects(
    owner.play(Uint8Array.from([2]), null, 100),
    matchesPlaybackError('Song playback is not available.'),
  )
})
