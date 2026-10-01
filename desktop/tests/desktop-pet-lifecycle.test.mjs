/** Verify desktop-pet ready and shutdown deadlines remain bounded. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DesktopPetReadyDeadline,
  drainDesktopPetAndIndependentPersistenceWithin,
  drainDesktopPetPersistenceWithin,
  sequenceDesktopPetMutation,
  settleDesktopPetOperationWithin,
  shouldQuitAfterAllDesktopWindowsClose,
  shouldPersistDesktopPetPlacement,
} from '../dist-electron/desktop-pet-lifecycle.js'

function wait(delayMs) {
  return new Promise((resolve) => { setTimeout(resolve, delayMs) })
}

test('ready deadline expires once and then disarms itself', async () => {
  const deadline = new DesktopPetReadyDeadline()
  let expirations = 0
  deadline.arm(() => { expirations += 1 }, 5)

  await wait(25)

  assert.equal(expirations, 1)
  deadline.clear()
})

test('clearing or replacing a ready deadline invalidates the old callback', async () => {
  const deadline = new DesktopPetReadyDeadline()
  const expirations = []
  deadline.arm(() => { expirations.push('old') }, 20)
  deadline.arm(() => { expirations.push('replacement') }, 5)

  await wait(25)
  deadline.arm(() => { expirations.push('cleared') }, 5)
  deadline.clear()
  await wait(15)

  assert.deepEqual(expirations, ['replacement'])
})

test('optional shutdown work reports completion and rejection as settled', async () => {
  assert.equal(
    await settleDesktopPetOperationWithin(Promise.resolve(), 20),
    true,
  )
  assert.equal(
    await settleDesktopPetOperationWithin(Promise.reject(new Error('private')), 20),
    true,
  )
})

test('optional shutdown work stops delaying exit at its deadline', async () => {
  const never = new Promise(() => {})
  assert.equal(await settleDesktopPetOperationWithin(never, 5), false)
})

test('shutdown drains admitted reset before its final placement flush', async () => {
  let releaseReset
  const events = []
  const reset = new Promise((resolve) => {
    releaseReset = () => {
      events.push('reset')
      resolve()
    }
  })
  const drain = drainDesktopPetPersistenceWithin(
    [reset],
    async () => { events.push('flush') },
    50,
  )

  await Promise.resolve()
  assert.deepEqual(events, [])
  releaseReset()

  assert.equal(await drain, true)
  assert.deepEqual(events, ['reset', 'flush'])
})

test('independent shutdown writes cannot starve the final pet placement', async () => {
  let releasePetWrite
  const events = []
  const petWrite = new Promise((resolve) => {
    releasePetWrite = () => {
      events.push('pet-write')
      resolve()
    }
  })
  const stuckIndependentWrite = new Promise(() => {})
  const drain = drainDesktopPetAndIndependentPersistenceWithin(
    [petWrite],
    async () => { events.push('pet-flush') },
    [stuckIndependentWrite],
    30,
  )

  await Promise.resolve()
  assert.deepEqual(events, [])
  releasePetWrite()
  await wait(5)

  assert.deepEqual(events, ['pet-write', 'pet-flush'])
  assert.equal(await drain, false)
})

test('programmatic default placement is ignored until the user moves it', () => {
  const resetPlacement = { displayId: 4, x: 1200, y: 500 }
  assert.equal(
    shouldPersistDesktopPetPlacement(resetPlacement, resetPlacement),
    false,
  )
  assert.equal(
    shouldPersistDesktopPetPlacement(
      { ...resetPlacement, x: resetPlacement.x - 1 },
      resetPlacement,
    ),
    true,
  )
  assert.equal(shouldPersistDesktopPetPlacement(null, resetPlacement), false)
})

test('pet mutations remain ordered after success or rejection', async () => {
  let releaseFirst
  const events = []
  const first = new Promise((resolve) => {
    releaseFirst = resolve
  })
  const second = sequenceDesktopPetMutation(first, async () => {
    events.push('second')
    return 'completed'
  })

  await Promise.resolve()
  assert.deepEqual(events, [])
  releaseFirst()
  assert.equal(await second, 'completed')
  assert.deepEqual(events, ['second'])

  const afterFailure = sequenceDesktopPetMutation(
    Promise.reject(new Error('transient')),
    async () => 'retried',
  )
  assert.equal(await afterFailure, 'retried')
})

test('reset and later mode intent share one mutation admission order', async () => {
  let releaseReset
  let placement = 'old'
  let revision = 8
  const resetGate = new Promise((resolve) => { releaseReset = resolve })
  const reset = sequenceDesktopPetMutation(null, async () => {
    await resetGate
    placement = null
  })
  const settingsUpdate = sequenceDesktopPetMutation(reset, async () => {
    assert.equal(placement, null)
    revision += 1
  })
  const nativeHide = sequenceDesktopPetMutation(settingsUpdate, async () => ({
    expectedRevision: revision,
    placement,
  }))

  await Promise.resolve()
  assert.equal(placement, 'old')
  releaseReset()

  assert.deepEqual(await nativeHide, {
    expectedRevision: 9,
    placement: null,
  })
})

test('pet intent and pending writes govern tray residency after close', () => {
  assert.equal(
    shouldQuitAfterAllDesktopWindowsClose('win32', false, 'hidden', false),
    false,
  )
  assert.equal(
    shouldQuitAfterAllDesktopWindowsClose('win32', false, 'visible', false),
    false,
  )
  assert.equal(
    shouldQuitAfterAllDesktopWindowsClose('win32', false, 'disabled', false),
    true,
  )
  assert.equal(
    shouldQuitAfterAllDesktopWindowsClose('darwin', false, 'disabled', false),
    false,
  )
  assert.equal(
    shouldQuitAfterAllDesktopWindowsClose('win32', true, 'disabled', false),
    false,
  )
  assert.equal(
    shouldQuitAfterAllDesktopWindowsClose('win32', false, 'disabled', true),
    false,
  )
})

test('lifecycle deadlines reject non-positive or fractional durations', async () => {
  const deadline = new DesktopPetReadyDeadline()
  assert.throws(() => { deadline.arm(() => {}, 0) })
  await assert.rejects(
    settleDesktopPetOperationWithin(Promise.resolve(), 1.5),
  )
})
