/** Verify desktop-pet mutation ordering and shutdown remain bounded. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  drainDesktopPetAndIndependentPersistenceWithin,
  sequenceDesktopPetMutation,
  settleDesktopPetOperationWithin,
  shouldApplyDesktopPetModeRequest,
  shouldQuitAfterAllDesktopWindowsClose,
  stopDesktopPetBeforePersistence,
} from '../dist-electron/desktop-pet-lifecycle.js'

function wait(delayMs) {
  return new Promise((resolve) => { setTimeout(resolve, delayMs) })
}

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

test('independent shutdown writes cannot starve the final program stop', async () => {
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
    async () => { events.push('program-stop') },
    [stuckIndependentWrite],
    30,
  )

  await Promise.resolve()
  assert.deepEqual(events, [])
  releasePetWrite()
  await wait(5)

  assert.deepEqual(events, ['pet-write', 'program-stop'])
  assert.equal(await drain, false)
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

test('scan and later mode intent share one mutation admission order', async () => {
  let releaseScan
  let selectedProgram = 'old'
  let revision = 8
  const scanGate = new Promise((resolve) => { releaseScan = resolve })
  const scan = sequenceDesktopPetMutation(null, async () => {
    await scanGate
    selectedProgram = 'new'
  })
  const settingsUpdate = sequenceDesktopPetMutation(scan, async () => {
    assert.equal(selectedProgram, 'new')
    revision += 1
  })
  const trayHide = sequenceDesktopPetMutation(settingsUpdate, async () => ({
    expectedRevision: revision,
    selectedProgram,
  }))

  await Promise.resolve()
  assert.equal(selectedProgram, 'old')
  releaseScan()

  assert.deepEqual(await trayHide, {
    expectedRevision: 9,
    selectedProgram: 'new',
  })
})

test('unavailable-library persistence happens only after an exact stop', async () => {
  const events = []
  const result = await stopDesktopPetBeforePersistence(
    async () => { events.push('stop') },
    async () => {
      events.push('persist')
      return 'saved'
    },
  )

  assert.equal(result, 'saved')
  assert.deepEqual(events, ['stop', 'persist'])

  const blocked = []
  await assert.rejects(
    stopDesktopPetBeforePersistence(
      async () => {
        blocked.push('stop')
        throw new Error('owned process is still alive')
      },
      async () => { blocked.push('persist') },
    ),
    /still alive/u,
  )
  assert.deepEqual(blocked, ['stop'])
})

test('explicit Visible retries recover absent and failed native runtimes', () => {
  assert.equal(shouldApplyDesktopPetModeRequest('visible', 'absent', 'visible'), true)
  assert.equal(shouldApplyDesktopPetModeRequest('visible', 'failed', 'visible'), true)
  assert.equal(shouldApplyDesktopPetModeRequest('visible', 'loading', 'visible'), false)
  assert.equal(shouldApplyDesktopPetModeRequest('visible', 'visible', 'visible'), false)
  assert.equal(shouldApplyDesktopPetModeRequest('hidden', 'absent', 'hidden'), false)
  assert.equal(shouldApplyDesktopPetModeRequest('disabled', 'absent', 'visible'), true)
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
  await assert.rejects(
    settleDesktopPetOperationWithin(Promise.resolve(), 1.5),
  )
})
