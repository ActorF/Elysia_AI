/** Verify Desktop Pet placement and the bounded native startup handshake. */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { PassThrough, Writable } from 'node:stream'
import test, { after, before } from 'node:test'

import {
  createDesktopPetWindowPreparation,
  getDesktopPetInitialBounds,
} from '../dist-electron/desktop-pet-window-controller.js'

const PLACEMENT = {
  appBounds: { x: 100, y: 100, width: 1000, height: 800 },
  workArea: { x: 0, y: 0, width: 1920, height: 1040 },
  scaleFactor: 1,
}
const PROGRAM = {
  id: 'owned-test-look',
  programDirectory: 'D:\\private\\爱莉希雅女仆',
  executablePath: 'D:\\private\\爱莉希雅女仆\\A爱莉希雅女仆.exe',
}
const previousSystemRoot = process.env.SystemRoot
const previousSecret = process.env.ELYSIA_WINDOW_CONTROLLER_TEST_SECRET

before(() => {
  process.env.SystemRoot = 'C:\\Windows'
  process.env.ELYSIA_WINDOW_CONTROLLER_TEST_SECRET = 'must-stay-in-main'
})

after(() => {
  if (previousSystemRoot === undefined) delete process.env.SystemRoot
  else process.env.SystemRoot = previousSystemRoot
  if (previousSecret === undefined) delete process.env.ELYSIA_WINDOW_CONTROLLER_TEST_SECRET
  else process.env.ELYSIA_WINDOW_CONTROLLER_TEST_SECRET = previousSecret
})

class FakeProcess extends EventEmitter {
  constructor(pid) {
    super()
    this.pid = pid
    this.exitCode = null
    this.signalCode = null
    this.killed = false
    this.writes = []
    this.stdout = new PassThrough()
    this.stdin = new Writable({
      write: (chunk, _encoding, callback) => {
        this.writes.push(chunk.toString('utf8'))
        callback()
      },
    })
  }

  kill() {
    this.killed = true
    return true
  }
}

function createHarness(placement = PLACEMENT, platform = 'win32') {
  const helpers = []
  const spawns = []
  const child = new FakeProcess(731)
  const unrelated = new FakeProcess(990)
  const abort = new AbortController()
  const prepare = createDesktopPetWindowPreparation(() => placement, {
    platform,
    spawnController(executable, args, options) {
      const helper = new FakeProcess(9000 + helpers.length)
      helpers.push(helper)
      spawns.push({ executable, args, options })
      return helper
    },
  })
  return {
    helpers, spawns, child, unrelated, abort, prepare,
    setPlacement(nextPlacement) { placement = nextPlacement },
  }
}

async function arm(harness) {
  const warming = harness.prepare.warmup(harness.abort.signal)
  await Promise.resolve()
  const helper = harness.helpers.at(-1)
  assert.ok(helper, 'warmup starts the controller before any pet process is assigned')
  helper.stdout.write('ARMED\n')
  await warming
  return helper
}

async function begin(harness) {
  const helper = await arm(harness)
  const operation = harness.prepare(harness.child, PROGRAM, harness.abort.signal)
  // Attach a rejection observer before injecting synchronous malformed output.
  // The assertion still awaits the original promise and verifies its outcome.
  operation.catch(() => {})
  await Promise.resolve()
  return { helper, operation }
}

test('fits the existing aspect ratio beside the app without using the 1500px default', () => {
  assert.deepEqual(getDesktopPetInitialBounds(PLACEMENT, 1500, 1500), {
    x: 1038, y: 642, width: 250, height: 250,
  })
  assert.deepEqual(getDesktopPetInitialBounds(PLACEMENT, 1500, 2000), {
    x: 1040, y: 572, width: 240, height: 320,
  })
})

test('clamps to a negative-coordinate monitor and applies physical DPI sizing', () => {
  const negativeMonitor = {
    appBounds: { x: -1850, y: 50, width: 1600, height: 900 },
    workArea: { x: -1920, y: 0, width: 1920, height: 1040 },
    scaleFactor: 1,
  }
  assert.deepEqual(getDesktopPetInitialBounds(negativeMonitor, 1500, 1500), {
    x: -372, y: 582, width: 360, height: 360,
  })
  const highDpi = {
    appBounds: { x: 200, y: 200, width: 2000, height: 1600 },
    workArea: { x: 0, y: 0, width: 3840, height: 2080 },
    scaleFactor: 2,
  }
  assert.deepEqual(getDesktopPetInitialBounds(highDpi, 1500, 1500), {
    x: 2075, y: 1284, width: 500, height: 500,
  })
})

test('rejects nonfinite geometry and impossible native source dimensions', () => {
  for (const invalid of [NaN, Infinity, 0, -1, 32769, 1.5]) {
    assert.throws(() => getDesktopPetInitialBounds(PLACEMENT, invalid, 1500))
  }
  for (const scaleFactor of [NaN, Infinity, 0, 9]) {
    assert.throws(() => getDesktopPetInitialBounds({ ...PLACEMENT, scaleFactor }, 1500, 1500))
  }
  assert.throws(() => getDesktopPetInitialBounds({
    ...PLACEMENT, workArea: { ...PLACEMENT.workArea, x: NaN },
  }, 1500, 1500))
})

test('prewarms a fixed hidden helper and waits for ARMED before sending a child identity', async () => {
  const harness = createHarness()
  let warmed = false
  const warming = harness.prepare.warmup(harness.abort.signal).then(() => { warmed = true })
  await Promise.resolve()
  const helper = harness.helpers[0]
  assert.equal(warmed, false)
  assert.deepEqual(helper.writes, [])
  const { executable, args, options } = harness.spawns[0]
  assert.equal(executable, 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe')
  assert.deepEqual(args.slice(0, 4), ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand'])
  assert.equal(options.windowsHide, true)
  assert.equal(options.shell, false)
  assert.deepEqual(options.stdio, ['pipe', 'pipe', 'ignore'])
  assert.equal(Object.hasOwn(options.env, 'ELYSIA_WINDOW_CONTROLLER_TEST_SECRET'), false)
  helper.stdout.write('AR')
  await Promise.resolve()
  assert.equal(warmed, false)
  helper.stdout.write('MED\r\n')
  await warming
  assert.equal(warmed, true)
  assert.deepEqual(helper.writes, [])
  harness.abort.abort()
  assert.equal(helper.killed, true)
  assert.equal(harness.child.killed, false)
})

test('sends only the owned identity and resolves after placement, READY and clean exit', async () => {
  const harness = createHarness()
  const { helper, operation } = await begin(harness)
  assert.deepEqual(JSON.parse(helper.writes[0]), {
    pid: harness.child.pid, executable: PROGRAM.executablePath,
  })
  helper.stdout.write('WIND')
  helper.stdout.write('OW 1500 2000\r\n')
  assert.equal(helper.writes[1], '1040 572 240 320\n')
  let settled = false
  operation.then(() => { settled = true })
  helper.stdout.write('READY\n')
  await Promise.resolve()
  assert.equal(settled, false, 'READY alone cannot conceal a failing native helper exit')
  helper.emit('close', 0)
  await operation
  assert.equal(settled, true)
  assert.equal(helper.killed, false)
  assert.equal(harness.child.killed, false)
  assert.equal(harness.unrelated.killed, false)
})

test('uses the latest app and display bounds when the loaded window requests placement', async () => {
  const harness = createHarness()
  const { helper, operation } = await begin(harness)
  // The app can cross monitors while native models are loading. The warmed
  // helper must not restore coordinates captured before that user movement.
  harness.setPlacement({
    appBounds: { x: -1850, y: 50, width: 1600, height: 900 },
    workArea: { x: -1920, y: 0, width: 1920, height: 1040 },
    scaleFactor: 1,
  })
  helper.stdout.write('WINDOW 1500 1500\n')
  assert.equal(helper.writes[1], '-372 582 360 360\n')
  helper.stdout.write('READY\n')
  helper.emit('close', 0)
  await operation
  assert.equal(helper.killed, false)
})

test('abort during preparation kills only the helper and cannot accept a late READY', async () => {
  const harness = createHarness()
  const { helper, operation } = await begin(harness)
  helper.stdout.write('WINDOW 1500 1500\n')
  harness.abort.abort()
  await assert.rejects(operation, /could not be prepared/u)
  helper.stdout.write('READY\n')
  helper.emit('close', 0)
  assert.equal(helper.killed, true)
  assert.equal(harness.child.killed, false)
  assert.equal(harness.unrelated.killed, false)
})

test('rejects a successful helper exit that never confirmed a rendered pet', async () => {
  const harness = createHarness()
  const { helper, operation } = await begin(harness)
  helper.stdout.write('WINDOW 1500 1500\n')
  helper.emit('close', 0)
  await assert.rejects(operation, /could not be prepared/u)
  assert.equal(harness.child.killed, false)
})

test('rejects a native failure even after a syntactically complete handshake', async () => {
  const harness = createHarness()
  const { helper, operation } = await begin(harness)
  helper.stdout.write('WINDOW 1500 1500\nREADY\n')
  helper.emit('close', 1)
  await assert.rejects(operation, /could not be prepared/u)
})

test('rejects malformed, duplicated, oversized or out-of-order native messages', async (t) => {
  for (const output of [
    'READY\n',
    'WINDOW 0 1500\n',
    'WINDOW 1500 32769\n',
    'WINDOW 1500 1500\nWINDOW 1500 1500\n',
    'WINDOW 1500 1500\nREADY\nREADY\n',
    'WINDOW 1500 1500\nUNEXPECTED\n',
    'x'.repeat(257),
  ]) {
    await t.test(output.slice(0, 60), async () => {
      const harness = createHarness()
      const { helper, operation } = await begin(harness)
      helper.stdout.write(output)
      await assert.rejects(operation, /could not be prepared/u)
      assert.equal(helper.killed, true)
      assert.equal(harness.child.killed, false)
      assert.equal(harness.unrelated.killed, false)
    })
  }
})

test('does not accept an unterminated trailing protocol fragment', async () => {
  const harness = createHarness()
  const { helper, operation } = await begin(harness)
  helper.stdout.write('WINDOW 1500 1500\nREADY\nextra')
  helper.emit('close', 0)
  await assert.rejects(operation, /could not be prepared/u)
})

test('rejects unavailable placement and unsupported platforms before a native helper', async () => {
  for (const harness of [createHarness(null), createHarness(PLACEMENT, 'linux')]) {
    await assert.rejects(harness.prepare.warmup(harness.abort.signal), /could not be prepared/u)
    assert.equal(harness.helpers.length, 0)
  }
})

test('an already aborted startup never spawns a helper', async () => {
  const harness = createHarness()
  harness.abort.abort()
  await assert.rejects(harness.prepare.warmup(harness.abort.signal), /could not be prepared/u)
  assert.equal(harness.helpers.length, 0)
})

test('requires a prewarmed session bound to the same startup signal', async () => {
  const harness = createHarness()
  await assert.rejects(harness.prepare(harness.child, PROGRAM, harness.abort.signal))
  assert.equal(harness.helpers.length, 0)
  const helper = await arm(harness)
  const unrelatedAbort = new AbortController()
  await assert.rejects(harness.prepare(harness.child, PROGRAM, unrelatedAbort.signal))
  assert.deepEqual(helper.writes, [])
  harness.abort.abort()
  assert.equal(helper.killed, true)
})

test('aborting prewarm stops its helper before any process identity is released', async () => {
  const harness = createHarness()
  const warming = harness.prepare.warmup(harness.abort.signal)
  const rejected = assert.rejects(warming, /could not be prepared/u)
  const helper = harness.helpers[0]
  harness.abort.abort()
  await rejected
  helper.stdout.write('ARMED\n')
  assert.deepEqual(helper.writes, [])
  assert.equal(helper.killed, true)
  assert.equal(harness.child.killed, false)
})

test('rejects an exited child and relative image path without sending them to the helper', async (t) => {
  for (const invalid of ['exited', 'relative', 'pid']) {
    await t.test(invalid, async () => {
      const harness = createHarness()
      const helper = await arm(harness)
      const descriptor = { ...PROGRAM }
      if (invalid === 'exited') harness.child.exitCode = 0
      else if (invalid === 'relative') descriptor.executablePath = 'pet.exe'
      else harness.child.pid = 0
      await assert.rejects(harness.prepare(harness.child, descriptor, harness.abort.signal))
      assert.deepEqual(helper.writes, [])
      assert.equal(helper.killed, true)
      assert.equal(harness.child.killed, false)
    })
  }
})

test('bounds a stalled prewarm and a pet that never reports READY', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const cold = createHarness()
  const warming = cold.prepare.warmup(cold.abort.signal)
  const warmingRejected = assert.rejects(warming, /could not be prepared/u)
  t.mock.timers.tick(20_000)
  await warmingRejected
  assert.equal(cold.helpers[0].killed, true)
  assert.deepEqual(cold.helpers[0].writes, [])

  const harness = createHarness()
  const { helper, operation } = await begin(harness)
  const rejected = assert.rejects(operation, /could not be prepared/u)
  helper.stdout.write('WINDOW 1500 1500\n')
  t.mock.timers.tick(20_000)
  await rejected
  assert.equal(helper.killed, true)
  assert.equal(harness.child.killed, false)
  assert.equal(harness.unrelated.killed, false)
})
