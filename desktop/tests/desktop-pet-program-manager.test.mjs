/** Verify external Desktop Pet process ownership and bounded switching. */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'

import {
  DesktopPetProgramManager,
} from '../dist-electron/desktop-pet-program-manager.js'

class FakeChildProcess extends EventEmitter {
  constructor(pid) {
    super()
    this.pid = pid
    this.exitCode = null
    this.signalCode = null
    this.killed = false
  }

  kill() {
    this.killed = true
    return true
  }
}

const PROGRAM_A = {
  id: 'look-a',
  programDirectory: 'D:\\private\\look-a',
  executablePath: 'D:\\private\\look-a\\pet-a.exe',
}

const PROGRAM_B = {
  id: 'look-b',
  programDirectory: 'D:\\private\\look-b',
  executablePath: 'D:\\private\\look-b\\pet-b.exe',
}

function createImmediateDependencies(events, children) {
  return {
    spawnProgram(executablePath, args, options) {
      const child = new FakeChildProcess(children.length + 100)
      children.push(child)
      events.push(['spawn', executablePath, args, options])
      return child
    },
    async stopProgram(child) {
      events.push(['stop', child.pid])
      child.exitCode = 0
      child.emit('exit', 0, null)
    },
    async runWithinDeadline(operation) {
      await operation
      return true
    },
  }
}

test('launches the selected executable directly with its local working folder', async () => {
  const events = []
  const states = []
  const children = []
  const manager = new DesktopPetProgramManager(
    (runtime, warning) => { states.push([runtime, warning]) },
    createImmediateDependencies(events, children),
  )

  await manager.reconcile(PROGRAM_A, true)

  assert.equal(events.length, 1)
  assert.equal(events[0][0], 'spawn')
  assert.equal(events[0][1], PROGRAM_A.executablePath)
  assert.deepEqual(events[0][2], [])
  assert.equal(events[0][3].cwd, PROGRAM_A.programDirectory)
  assert.equal(events[0][3].shell, false)
  assert.equal(events[0][3].detached, false)
  assert.equal(events[0][3].windowsHide, false)
  assert.equal(events[0][3].stdio, 'ignore')
  assert.equal(typeof events[0][3].env, 'object')
  assert.equal(
    events[0][3].env.__COMPAT_LAYER,
    process.platform === 'win32' ? 'RunAsInvoker' : undefined,
  )
  assert.deepEqual(states, [
    ['absent', null],
    ['loading', null],
    ['visible', null],
  ])
})

test('does not forward arbitrary parent environment secrets', async () => {
  const previousSecret = process.env.ELYSIA_DESKTOP_PET_TEST_SECRET
  const previousCompatibilityLayer = process.env.__COMPAT_LAYER
  process.env.ELYSIA_DESKTOP_PET_TEST_SECRET = 'must-not-reach-the-program'
  process.env.__COMPAT_LAYER = 'RunAsAdmin'
  const events = []
  const children = []
  try {
    const manager = new DesktopPetProgramManager(
      () => {},
      createImmediateDependencies(events, children),
    )

    await manager.reconcile(PROGRAM_A, true)

    assert.equal(
      Object.hasOwn(events[0][3].env, 'ELYSIA_DESKTOP_PET_TEST_SECRET'),
      false,
    )
    assert.equal(
      events[0][3].env.__COMPAT_LAYER,
      process.platform === 'win32' ? 'RunAsInvoker' : undefined,
    )
  } finally {
    if (previousSecret === undefined) {
      delete process.env.ELYSIA_DESKTOP_PET_TEST_SECRET
    } else {
      process.env.ELYSIA_DESKTOP_PET_TEST_SECRET = previousSecret
    }
    if (previousCompatibilityLayer === undefined) {
      delete process.env.__COMPAT_LAYER
    } else {
      process.env.__COMPAT_LAYER = previousCompatibilityLayer
    }
  }
})

test('switching closes the owned child before launching its replacement', async () => {
  const events = []
  const children = []
  const manager = new DesktopPetProgramManager(
    () => {},
    createImmediateDependencies(events, children),
  )

  await manager.reconcile(PROGRAM_A, true)
  await manager.reconcile(PROGRAM_B, true)

  assert.deepEqual(
    events.map((event) => [event[0], event[1]]),
    [
      ['spawn', PROGRAM_A.executablePath],
      ['stop', 100],
      ['spawn', PROGRAM_B.executablePath],
    ],
  )
})

test('default stop targets only the exact owned PID and its process tree', async () => {
  const targets = []
  const children = []
  const manuallyStartedSameProgram = new FakeChildProcess(999)
  const dependencies = {
    spawnProgram() {
      const child = new FakeChildProcess(431)
      children.push(child)
      return child
    },
    async terminateProcessTree(pid) {
      targets.push(pid)
      const owned = children.find((child) => child.pid === pid)
      owned.exitCode = 0
      owned.emit('exit', 0, null)
    },
    async runWithinDeadline(operation) {
      await operation
      return true
    },
  }
  const manager = new DesktopPetProgramManager(() => {}, dependencies)

  await manager.reconcile(PROGRAM_A, true)
  await manager.stop()

  assert.deepEqual(targets, [431])
  assert.equal(manuallyStartedSameProgram.exitCode, null)
  assert.equal(manuallyStartedSameProgram.killed, false)
})

test('process-tree stop failure blocks the replacement program', async () => {
  const targets = []
  const launched = []
  const dependencies = {
    spawnProgram(executablePath) {
      launched.push(executablePath)
      return new FakeChildProcess(500 + launched.length)
    },
    async terminateProcessTree(pid) {
      targets.push(pid)
      throw new Error('taskkill rejected the exact owned PID')
    },
    async runWithinDeadline(operation) {
      await operation
      return true
    },
  }
  const states = []
  const manager = new DesktopPetProgramManager(
    (runtime, warning) => { states.push([runtime, warning]) },
    dependencies,
  )

  await manager.reconcile(PROGRAM_A, true)
  await assert.rejects(
    manager.reconcile(PROGRAM_B, true),
    /could not be closed safely/u,
  )

  assert.deepEqual(targets, [501])
  assert.deepEqual(launched, [PROGRAM_A.executablePath])
  assert.deepEqual(states.at(-1), [
    'failed',
    'Desktop Pet program could not be closed safely.',
  ])
})

test('a timed-out stop blocks replacement and preserves process ownership', async () => {
  const events = []
  const states = []
  const children = []
  let deadlineCall = 0
  const dependencies = createImmediateDependencies(events, children)
  dependencies.stopProgram = async (child) => {
    events.push(['stop', child.pid])
    await new Promise(() => {})
  }
  dependencies.runWithinDeadline = async (operation) => {
    deadlineCall += 1
    if (deadlineCall === 2) {
      void operation.catch(() => {})
      return false
    }
    await operation
    return true
  }
  const manager = new DesktopPetProgramManager(
    (runtime, warning) => { states.push([runtime, warning]) },
    dependencies,
  )

  await manager.reconcile(PROGRAM_A, true)
  await assert.rejects(
    manager.reconcile(PROGRAM_B, true),
    /could not be closed safely/u,
  )

  assert.equal(events.filter(([kind]) => kind === 'spawn').length, 1)
  assert.deepEqual(states.at(-1), [
    'failed',
    'Desktop Pet program did not close in time.',
  ])
})

test('late events from a replaced child cannot overwrite replacement state', async () => {
  const events = []
  const states = []
  const children = []
  const dependencies = createImmediateDependencies(events, children)
  dependencies.stopProgram = async (child) => {
    events.push(['stop', child.pid])
  }
  const manager = new DesktopPetProgramManager(
    (runtime, warning) => { states.push([runtime, warning]) },
    dependencies,
  )

  await manager.reconcile(PROGRAM_A, true)
  await manager.reconcile(PROGRAM_B, true)
  const visibleReplacementState = states.length
  children[0].emit('exit', 0, null)
  children[0].emit('error', new Error('stale'))

  assert.equal(states.length, visibleReplacementState)
  assert.deepEqual(states.at(-1), ['visible', null])
})

test('an error from a still-running child retains ownership until exact stop', async () => {
  const events = []
  const states = []
  const children = []
  const manager = new DesktopPetProgramManager(
    (runtime, warning) => { states.push([runtime, warning]) },
    createImmediateDependencies(events, children),
  )

  await manager.reconcile(PROGRAM_A, true)
  children[0].emit('error', new Error('operation failed but process survived'))
  await manager.reconcile(PROGRAM_B, true)

  assert.deepEqual(
    events.map((event) => [event[0], event[1]]),
    [
      ['spawn', PROGRAM_A.executablePath],
      ['stop', 100],
      ['spawn', PROGRAM_B.executablePath],
    ],
  )
  assert.deepEqual(states.at(-1), ['visible', null])
})

test('spawn failures are sanitized and do not leave an owned child', async () => {
  const states = []
  let attempts = 0
  const manager = new DesktopPetProgramManager(
    (runtime, warning) => { states.push([runtime, warning]) },
    {
      spawnProgram() {
        attempts += 1
        throw new Error('D:\\private\\secret.exe failed')
      },
    },
  )

  await manager.reconcile(PROGRAM_A, true)
  await manager.stop()

  assert.equal(attempts, 1)
  assert.deepEqual(states, [
    ['absent', null],
    ['loading', null],
    ['failed', 'Desktop Pet program could not be started.'],
    ['absent', null],
  ])
  assert.equal(JSON.stringify(states).includes('private'), false)
})

test('a program that exits before listener installation is never reported visible', async () => {
  const states = []
  const manager = new DesktopPetProgramManager(
    (runtime, warning) => { states.push([runtime, warning]) },
    {
      spawnProgram() {
        const child = new FakeChildProcess(733)
        child.exitCode = 1
        return child
      },
      async runWithinDeadline(operation) {
        await operation
        return true
      },
    },
  )

  await manager.reconcile(PROGRAM_A, true)

  assert.deepEqual(states, [
    ['absent', null],
    ['loading', null],
    ['failed', 'Desktop Pet program closed unexpectedly.'],
  ])
})

test('disabled and missing selections never launch an external program', async () => {
  const events = []
  const states = []
  const children = []
  const manager = new DesktopPetProgramManager(
    (runtime, warning) => { states.push([runtime, warning]) },
    createImmediateDependencies(events, children),
  )

  await manager.reconcile(PROGRAM_A, false)
  await manager.reconcile(null, true)

  assert.equal(events.length, 0)
  assert.deepEqual(states, [
    ['absent', null],
    ['failed', 'Choose a detected Desktop Pet program first.'],
  ])
})

test('shutdown cancels queued work and prevents every later launch', async () => {
  const events = []
  const children = []
  const dependencies = createImmediateDependencies(events, children)
  const manager = new DesktopPetProgramManager(() => {}, dependencies)

  const starting = manager.reconcile(PROGRAM_A, true)
  const shutdown = manager.shutdown()
  await Promise.all([starting, shutdown])
  await manager.reconcile(PROGRAM_B, true)

  assert.deepEqual(events, [])
})
