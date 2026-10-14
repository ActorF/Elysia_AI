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

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function createPreparedHarness({ warmup, prepare, stop, deadline, spawn } = {}) {
  const events = []
  const states = []
  const children = []
  const signals = []
  const dependencies = createImmediateDependencies(events, children)
  const prepareWindow = async (child, descriptor, signal) => {
    events.push(['prepare', child.pid])
    await prepare?.(child, descriptor, signal)
  }
  prepareWindow.warmup = async (signal) => {
    signals.push(signal)
    events.push(['warmup'])
    await warmup?.(signal)
  }
  dependencies.prepareWindow = prepareWindow
  if (stop !== undefined) dependencies.stopProgram = (child) => stop(child, events)
  if (deadline !== undefined) dependencies.runWithinDeadline = deadline
  if (spawn !== undefined) dependencies.spawnProgram = spawn
  const manager = new DesktopPetProgramManager(
    (runtime, warning) => { states.push([runtime, warning]) }, dependencies,
  )
  return { manager, events, states, children, signals }
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

test('prewarms before hidden spawn and publishes visible only after native preparation', async () => {
  const warmupEntered = deferred()
  const warmupGate = deferred()
  const prepareEntered = deferred()
  const prepareGate = deferred()
  const harness = createPreparedHarness({
    async warmup(signal) {
      warmupEntered.resolve(signal)
      await warmupGate.promise
    },
    async prepare(child, descriptor, signal) {
      prepareEntered.resolve({ child, descriptor, signal })
      await prepareGate.promise
    },
  })
  const starting = harness.manager.reconcile(PROGRAM_A, true)
  const warmupSignal = await warmupEntered.promise
  assert.equal(harness.children.length, 0)
  assert.deepEqual(harness.states.at(-1), ['loading', null])

  warmupGate.resolve()
  const prepared = await prepareEntered.promise
  assert.equal(prepared.child, harness.children[0])
  assert.deepEqual(prepared.descriptor, PROGRAM_A)
  assert.equal(prepared.signal, warmupSignal)
  assert.deepEqual(harness.events.map(([kind]) => kind), ['warmup', 'spawn', 'prepare'])
  assert.equal(harness.events.find(([kind]) => kind === 'spawn')[3].windowsHide, true)
  assert.equal(harness.states.some(([runtime]) => runtime === 'visible'), false)

  prepareGate.resolve()
  await starting
  assert.deepEqual(harness.states.at(-1), ['visible', null])
  assert.equal(warmupSignal.aborted, false)
})

test('failed native prewarm never launches a child and exposes a sanitized failure', async () => {
  const harness = createPreparedHarness({
    async warmup() { throw new Error('C:\\private\\helper failed to compile') },
  })
  await harness.manager.reconcile(PROGRAM_A, true)
  assert.equal(harness.children.length, 0)
  assert.equal(harness.signals[0].aborted, true)
  assert.deepEqual(harness.states.at(-1), ['failed', 'Desktop Pet program could not be started.'])
  assert.equal(JSON.stringify(harness.states).includes('private'), false)
})

test('a prewarm deadline aborts its reservation without spawning the selected program', async () => {
  const harness = createPreparedHarness({
    async warmup() { await new Promise(() => {}) },
    async deadline() { return false },
  })
  await harness.manager.reconcile(PROGRAM_A, true)
  assert.equal(harness.children.length, 0)
  assert.equal(harness.signals[0].aborted, true)
  assert.deepEqual(harness.states.at(-1), ['failed', 'Desktop Pet program could not be started.'])
})

test('a spawn failure releases the already warmed native controller', async () => {
  const harness = createPreparedHarness({
    spawn() { throw new Error('native process refused to start') },
  })
  await harness.manager.reconcile(PROGRAM_A, true)
  assert.equal(harness.signals[0].aborted, true)
  assert.deepEqual(harness.events, [['warmup']])
  assert.deepEqual(harness.states.at(-1), ['failed', 'Desktop Pet program could not be started.'])
})

test('failed preparation stops the exact child before retrying another selection', async () => {
  const harness = createPreparedHarness({
    async prepare(_child, descriptor) {
      if (descriptor.id === PROGRAM_A.id) throw new Error('window was not ready')
    },
  })
  await harness.manager.reconcile(PROGRAM_A, true)
  assert.equal(harness.signals[0].aborted, true)
  assert.equal(harness.children[0].exitCode, 0)
  assert.deepEqual(harness.states.at(-1), ['failed', 'Desktop Pet program could not be started.'])
  assert.equal(harness.states.some(([runtime]) => runtime === 'visible'), false)

  await harness.manager.reconcile(PROGRAM_B, true)
  assert.deepEqual(harness.events.filter(([kind]) => kind === 'spawn' || kind === 'stop')
    .map((event) => event.slice(0, 2)), [
    ['spawn', PROGRAM_A.executablePath], ['stop', 100], ['spawn', PROGRAM_B.executablePath],
  ])
  assert.deepEqual(harness.states.at(-1), ['visible', null])
})

test('failed preparation with failed cleanup cannot be mistaken for an already visible same model', async () => {
  const harness = createPreparedHarness({
    async prepare() { throw new Error('native window never became ready') },
    async stop(child, events) {
      events.push(['stop', child.pid])
      throw new Error('owned process is still alive')
    },
  })
  await harness.manager.reconcile(PROGRAM_A, true)
  assert.equal(harness.children[0].exitCode, null)
  assert.deepEqual(harness.states.at(-1), ['failed', 'Desktop Pet program could not be closed safely.'])

  await assert.rejects(harness.manager.reconcile(PROGRAM_A, true), /could not be closed safely/u)
  assert.equal(harness.children.length, 1, 'failed cleanup retains ownership instead of creating another pet')
  assert.deepEqual(harness.events.filter(([kind]) => kind === 'stop'), [['stop', 100], ['stop', 100]])
  assert.equal(harness.states.some(([runtime]) => runtime === 'visible'), false)
})

test('a preparation deadline stops the hidden child and rejects a late success', async () => {
  const preparing = deferred()
  let calls = 0
  const harness = createPreparedHarness({
    async prepare() { await preparing.promise },
    async deadline(operation) {
      calls += 1
      // Warmup and OS spawn finish; the native-window wait alone expires.
      if (calls === 3) return false
      await operation
      return true
    },
  })
  await harness.manager.reconcile(PROGRAM_A, true)
  assert.equal(harness.children[0].exitCode, 0)
  assert.equal(harness.signals[0].aborted, true)
  assert.equal(harness.states.some(([runtime]) => runtime === 'visible'), false)
  const stateCount = harness.states.length
  preparing.resolve()
  await Promise.resolve()
  assert.equal(harness.states.length, stateCount)
})

test('same-turn on then off skips prewarm and does not briefly launch the stale selection', async () => {
  const harness = createPreparedHarness()
  const starting = harness.manager.reconcile(PROGRAM_A, true)
  const hiding = harness.manager.reconcile(PROGRAM_A, false)
  await Promise.all([starting, hiding])
  assert.deepEqual(harness.events, [])
  assert.deepEqual(harness.states, [['absent', null]])
})

test('hide, stop and shutdown cancel prewarm before a child can be spawned', async (t) => {
  for (const action of ['hide', 'stop', 'shutdown']) {
    await t.test(action, async () => {
      const entered = deferred()
      const gate = deferred()
      const harness = createPreparedHarness({
        async warmup(signal) {
          entered.resolve(signal)
          await gate.promise
        },
      })
      const starting = harness.manager.reconcile(PROGRAM_A, true)
      const signal = await entered.promise
      const cancelling = action === 'hide'
        ? harness.manager.reconcile(PROGRAM_A, false) : harness.manager[action]()
      assert.equal(signal.aborted, true)
      gate.reject(new Error('controller observed abort'))
      await Promise.all([starting, cancelling])
      assert.equal(harness.children.length, 0)
      assert.deepEqual(harness.states.at(-1), ['absent', null])
      assert.equal(harness.states.some(([runtime]) => runtime === 'failed'), false)
    })
  }
})

test('switching during prewarm abandons the old reservation and starts only the latest model', async () => {
  const entered = deferred()
  const gate = deferred()
  let warmups = 0
  const harness = createPreparedHarness({
    async warmup(signal) {
      warmups += 1
      if (warmups === 1) {
        entered.resolve(signal)
        await gate.promise
      }
    },
  })
  const oldStart = harness.manager.reconcile(PROGRAM_A, true)
  const oldSignal = await entered.promise
  const newStart = harness.manager.reconcile(PROGRAM_B, true)
  assert.equal(oldSignal.aborted, true)
  gate.reject(new Error('controller observed replacement'))
  await Promise.all([oldStart, newStart])
  assert.deepEqual(harness.events.filter(([kind]) => kind === 'spawn').map((event) => event[1]),
    [PROGRAM_B.executablePath])
  assert.deepEqual(harness.states.at(-1), ['visible', null])
  assert.equal(harness.signals[1].aborted, false)
})

test('hide during native preparation closes the child without publishing visible', async () => {
  const entered = deferred()
  const gate = deferred()
  const harness = createPreparedHarness({
    async prepare(_child, _descriptor, signal) {
      entered.resolve(signal)
      await gate.promise
    },
  })
  const starting = harness.manager.reconcile(PROGRAM_A, true)
  const signal = await entered.promise
  const hiding = harness.manager.reconcile(PROGRAM_A, false)
  assert.equal(signal.aborted, true)
  gate.reject(new Error('controller observed abort'))
  await Promise.all([starting, hiding])
  assert.equal(harness.children[0].exitCode, 0)
  assert.equal(harness.states.some(([runtime]) => runtime === 'visible'), false)
  assert.deepEqual(harness.states.at(-1), ['absent', null])
})

test('switching during preparation stops the old PID before starting its replacement', async () => {
  const entered = deferred()
  const gate = deferred()
  const harness = createPreparedHarness({
    async prepare(_child, descriptor, signal) {
      if (descriptor.id === PROGRAM_A.id) {
        entered.resolve(signal)
        await gate.promise
      }
    },
  })
  const oldStart = harness.manager.reconcile(PROGRAM_A, true)
  const signal = await entered.promise
  const newStart = harness.manager.reconcile(PROGRAM_B, true)
  assert.equal(signal.aborted, true)
  gate.reject(new Error('controller observed replacement'))
  await Promise.all([oldStart, newStart])
  assert.deepEqual(harness.events.filter(([kind]) => kind === 'spawn' || kind === 'stop')
    .map((event) => event.slice(0, 2)), [
    ['spawn', PROGRAM_A.executablePath], ['stop', 100], ['spawn', PROGRAM_B.executablePath],
  ])
  assert.equal(harness.states.filter(([runtime]) => runtime === 'visible').length, 1)
  assert.deepEqual(harness.states.at(-1), ['visible', null])
})

test('shutdown during native preparation closes the owned PID and blocks future launches', async () => {
  const entered = deferred()
  const gate = deferred()
  const harness = createPreparedHarness({
    async prepare(_child, _descriptor, signal) {
      entered.resolve(signal)
      await gate.promise
    },
  })
  const starting = harness.manager.reconcile(PROGRAM_A, true)
  const signal = await entered.promise
  const closing = harness.manager.shutdown()
  assert.equal(signal.aborted, true)
  gate.reject(new Error('controller observed shutdown'))
  await Promise.all([starting, closing])
  await harness.manager.reconcile(PROGRAM_B, true)
  assert.equal(harness.children.length, 1)
  assert.equal(harness.children[0].exitCode, 0)
  assert.equal(harness.states.some(([runtime]) => runtime === 'visible'), false)
  assert.deepEqual(harness.states.at(-1), ['absent', null])
})

test('a newer hide while the old model stops prevents the queued replacement from spawning', async () => {
  const stopping = deferred()
  const stopGate = deferred()
  const harness = createPreparedHarness({
    async stop(child, events) {
      events.push(['stop', child.pid])
      stopping.resolve()
      await stopGate.promise
      child.exitCode = 0
      child.emit('exit', 0, null)
    },
  })
  await harness.manager.reconcile(PROGRAM_A, true)
  const switching = harness.manager.reconcile(PROGRAM_B, true)
  await stopping.promise
  const hiding = harness.manager.reconcile(PROGRAM_B, false)
  stopGate.resolve()
  await Promise.all([switching, hiding])
  assert.deepEqual(harness.events.filter(([kind]) => kind === 'spawn').map((event) => event[1]),
    [PROGRAM_A.executablePath])
  assert.equal(harness.signals.length, 1, 'the stale replacement must not even reserve a controller')
  assert.deepEqual(harness.states.at(-1), ['absent', null])
})
