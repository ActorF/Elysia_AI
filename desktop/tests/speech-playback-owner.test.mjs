/** Verify trusted Electron speech playback ownership and private IPC settlement. */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test, { after } from 'node:test'

const SETTLED_CHANNEL = 'elysia:trusted-speech-settled:v1'
const PLAY_CHANNEL = 'elysia:trusted-speech-play:v1'
const CANCEL_CHANNEL = 'elysia:trusted-speech-cancel:v1'

const ipcMain = new EventEmitter()
const electronMock = test.mock.module('electron', {
  exports: { ipcMain },
})
const {
  PreloadSpeechPlaybackOwner,
  ReplaceableSpeechPlaybackOwner,
} = await import('../dist-electron/speech-playback-owner.js')

after(() => {
  electronMock.restore()
})

class FakeWebContents extends EventEmitter {
  constructor() {
    super()
    this.destroyed = false
    this.mainFrame = Object.freeze({ frame: 'main' })
    this.sent = []
    this.sendFailure = false
  }

  isDestroyed() {
    return this.destroyed
  }

  send(...message) {
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

function createOwner() {
  const window = new FakeWindow()
  return {
    owner: new PreloadSpeechPlaybackOwner(window),
    window,
  }
}

function speechClip(sequence = 0) {
  return {
    requestId: 'request_fixture',
    chatId: 'chat_fixture',
    sequence,
    wavBytes: Uint8Array.from([1, 2, 3, 4]),
  }
}

function playbackIdOf(window, sendIndex = 0) {
  const [channel, metadata, bytes] = window.webContents.sent[sendIndex]
  assert.equal(channel, PLAY_CHANNEL)
  assert.deepEqual(
    Object.keys(metadata).sort(),
    ['byteLength', 'playbackId', 'sequence'],
  )
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

function matchesPlaybackError(reason) {
  return (error) => (
    error instanceof Error
    && error.name === 'TrustedSpeechPlaybackError'
    && error.reason === reason
    && !error.message.includes('native')
  )
}

test('replaceable owner skips a closed-window gap and resumes playback', async () => {
  const router = new ReplaceableSpeechPlaybackOwner()
  await assert.rejects(
    router.play(speechClip()),
    matchesPlaybackError('clip-failed'),
  )

  const first = createOwner()
  router.replace(first.owner)
  const interrupted = router.play(speechClip(1))
  const interruptedId = playbackIdOf(first.window)
  router.replace(null)
  first.owner.dispose()
  await assert.rejects(interrupted, matchesPlaybackError('clip-failed'))
  assert.deepEqual(
    first.window.webContents.sent.at(-1),
    [CANCEL_CHANNEL, interruptedId],
  )

  const replacement = createOwner()
  router.replace(replacement.owner)
  const resumed = router.play(speechClip(2))
  const resumedId = playbackIdOf(replacement.window)
  try {
    emitSettlement(
      replacement.window,
      { playbackId: resumedId, status: 'played' },
    )
    await resumed

    const cancelled = router.play(speechClip(3))
    router.cancel()
    await assert.rejects(cancelled, matchesPlaybackError('clip-failed'))
  } finally {
    router.replace(null)
    replacement.owner.dispose()
    await resumed.catch(() => {})
  }
})

test('settlement accepts only the owning WebContents main frame', async () => {
  const { owner, window } = createOwner()
  const operation = owner.play(speechClip(7))
  const playbackId = playbackIdOf(window)
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
    assert.equal(
      window.webContents.sent.some(([channel]) => channel === CANCEL_CHANNEL),
      false,
    )
  } finally {
    owner.dispose()
    await operation.catch(() => {})
  }
})

test('failed and malformed settlements preserve sanitized failure classes', async () => {
  const failedFixture = createOwner()
  const failedOperation = failedFixture.owner.play(speechClip())
  const failedId = playbackIdOf(failedFixture.window)
  try {
    emitSettlement(
      failedFixture.window,
      { playbackId: failedId, status: 'failed' },
    )
    await assert.rejects(failedOperation, matchesPlaybackError('clip-failed'))
  } finally {
    failedFixture.owner.dispose()
    await failedOperation.catch(() => {})
  }

  const malformedFixture = createOwner()
  const malformedOperation = malformedFixture.owner.play(speechClip())
  const malformedId = playbackIdOf(malformedFixture.window)
  try {
    emitSettlement(malformedFixture.window, {
      playbackId: malformedId,
      status: 'played',
      path: 'D:/private/audio.wav',
    })
    await assert.rejects(
      malformedOperation,
      matchesPlaybackError('disconnected'),
    )
    assert.deepEqual(
      malformedFixture.window.webContents.sent.at(-1),
      [CANCEL_CHANNEL, malformedId],
    )
  } finally {
    malformedFixture.owner.dispose()
    await malformedOperation.catch(() => {})
  }
})

test('a retired cancellation reply cannot settle its replacement', async () => {
  const { owner, window } = createOwner()
  const first = owner.play(speechClip(0))
  const firstId = playbackIdOf(window, 0)
  owner.cancel()
  await assert.rejects(first, matchesPlaybackError('clip-failed'))

  const second = owner.play(speechClip(1))
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

test('retired reply bound skips admission without evicting a live ID', async () => {
  const { owner, window } = createOwner()
  const retiredIds = []
  for (let sequence = 0; sequence < 4; sequence += 1) {
    const operation = owner.play(speechClip(sequence))
    retiredIds.push(playbackIdOf(window, sequence * 2))
    owner.cancel()
    await assert.rejects(operation, matchesPlaybackError('clip-failed'))
  }

  const sendsBeforeBoundedSkip = window.webContents.sent.length
  await assert.rejects(
    owner.play(speechClip(4)),
    matchesPlaybackError('clip-failed'),
  )
  assert.equal(window.webContents.sent.length, sendsBeforeBoundedSkip)

  emitSettlement(
    window,
    { playbackId: retiredIds[0], status: 'failed' },
  )
  const replacement = owner.play(speechClip(5))
  const replacementId = playbackIdOf(window, sendsBeforeBoundedSkip)
  try {
    for (const retiredId of retiredIds.slice(1)) {
      emitSettlement(window, { playbackId: retiredId, status: 'failed' })
    }
    await assertStillPending(replacement)
    emitSettlement(
      window,
      { playbackId: replacementId, status: 'played' },
    )
    await replacement
  } finally {
    owner.dispose()
    await replacement.catch(() => {})
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
  test(`${name} disconnects pending playback without waiting for timeout`, async () => {
    const { owner, window } = createOwner()
    const operation = owner.play(speechClip())
    try {
      disconnect(window.webContents)
      await assert.rejects(operation, matchesPlaybackError('disconnected'))
    } finally {
      owner.dispose()
      await operation.catch(() => {})
    }
  })
}

test('only cross-document main-frame navigation disconnects playback', async () => {
  const { owner, window } = createOwner()
  const operation = owner.play(speechClip())
  playbackIdOf(window)
  try {
    window.webContents.emit(
      'did-start-navigation',
      {},
      'file:///subframe.html',
      false,
      false,
    )
    await assertStillPending(operation)

    window.webContents.emit(
      'did-start-navigation',
      {},
      'file:///index.html#main-content',
      true,
      true,
    )
    await assertStillPending(operation)
    assert.equal(
      window.webContents.sent.some(([channel]) => channel === CANCEL_CHANNEL),
      false,
    )

    window.webContents.emit(
      'did-start-navigation',
      {},
      'file:///index.html',
      false,
      true,
    )
    await assert.rejects(operation, matchesPlaybackError('disconnected'))
  } finally {
    owner.dispose()
    await operation.catch(() => {})
  }
})

test('cross-document navigation detaches idle routing until replacement', async () => {
  const window = new FakeWindow()
  const router = new ReplaceableSpeechPlaybackOwner()
  let lifecycleDisconnects = 0
  let owner
  owner = new PreloadSpeechPlaybackOwner(
    window,
    (disconnectedOwner) => {
      assert.equal(disconnectedOwner, owner)
      lifecycleDisconnects += 1
      router.replace(null)
    },
  )
  router.replace(owner)

  window.webContents.emit(
    'did-start-navigation',
    {},
    'file:///replacement.html',
    false,
    true,
  )

  assert.equal(lifecycleDisconnects, 1)
  assert.equal(ipcMain.listenerCount(SETTLED_CHANNEL), 0)
  assert.equal(window.webContents.listenerCount('destroyed'), 0)
  assert.equal(window.webContents.listenerCount('render-process-gone'), 0)
  assert.equal(window.webContents.listenerCount('did-start-navigation'), 0)
  await assert.rejects(
    router.play(speechClip()),
    matchesPlaybackError('clip-failed'),
  )
  assert.deepEqual(window.webContents.sent, [])
  owner.dispose()

  const replacement = createOwner()
  router.replace(replacement.owner)
  const resumed = router.play(speechClip(1))
  const resumedId = playbackIdOf(replacement.window)
  try {
    emitSettlement(
      replacement.window,
      { playbackId: resumedId, status: 'played' },
    )
    await resumed
  } finally {
    router.replace(null)
    replacement.owner.dispose()
    await resumed.catch(() => {})
  }
})

test('dispose removes native listeners and rejects future playback', async () => {
  const { owner, window } = createOwner()
  const operation = owner.play(speechClip())
  owner.dispose()

  await assert.rejects(operation, matchesPlaybackError('disconnected'))
  assert.equal(ipcMain.listenerCount(SETTLED_CHANNEL), 0)
  assert.equal(window.webContents.listenerCount('destroyed'), 0)
  assert.equal(window.webContents.listenerCount('render-process-gone'), 0)
  assert.equal(window.webContents.listenerCount('did-start-navigation'), 0)
  await assert.rejects(
    owner.play(speechClip(1)),
    matchesPlaybackError('disconnected'),
  )
})
