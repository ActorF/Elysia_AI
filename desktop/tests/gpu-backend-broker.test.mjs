/** Verify private fd5 correlation, bounded admission, and physical-exit fencing. */

import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import path from 'node:path'
import { Duplex } from 'node:stream'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { GpuAdmissionCoordinator } from '../dist-electron/gpu-admission.js'
import {
  GPU_BACKEND_MAX_FRAME_BYTES,
  GPU_BACKEND_MAX_PENDING,
  GPU_BACKEND_MAX_RETIRED,
  GpuBackendBroker,
} from '../dist-electron/gpu-backend-broker.js'

class PrivatePipe extends Duplex {
  constructor({ failWrite = false, blockWrite = false, highWaterMark = 16_384 } = {}) {
    super({ highWaterMark })
    this.output = []
    this.failWrite = failWrite
    this.blockWrite = blockWrite
  }

  _read() {}

  _write(chunk, _encoding, done) {
    if (this.failWrite) {
      done(new Error('private native pipe at C:\\private\\worker'))
    } else {
      this.output.push(Buffer.from(chunk))
      if (!this.blockWrite) done()
    }
  }

  send(...messages) {
    this.emit('data', Buffer.from(messages.map((message) => JSON.stringify(message)).join('\n') + '\n'))
  }

  replies() {
    return Buffer.concat(this.output).toString('utf8').split('\n').filter(Boolean).map(JSON.parse)
  }
}

function id(value) {
  return value.toString(16).padStart(32, '0')
}

function acquire(value, owner = 'ollama') {
  return { action: 'acquire', id: id(value), owner }
}

function control(action, value) {
  return { action, id: id(value) }
}

function immediate() {
  return new Promise((resolve) => setImmediate(resolve))
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((accepted, rejected) => { resolve = accepted; reject = rejected })
  return { promise, resolve, reject }
}

function harness(options = {}, pipeOptions = {}) {
  const gpu = new GpuAdmissionCoordinator()
  const pipe = new PrivatePipe(pipeOptions)
  const failures = []
  const broker = new GpuBackendBroker(pipe, gpu, (failure) => failures.push(failure), options)
  return {
    gpu, pipe, failures, broker,
    async finish() { broker.processExited(); await gpu.close(); await immediate() },
  }
}

test('one private acquisition grants its exact ID and releases after the physical callback', async () => {
  const owner = harness()
  owner.pipe.send(acquire(1, 'image-generation'))
  await immediate()
  assert.deepEqual(owner.pipe.replies(), [{ id: id(1), status: 'granted' }])
  assert.equal(owner.gpu.getSnapshot().active.owner, 'image-generation')
  owner.pipe.send(control('release', 1))
  assert.equal(owner.gpu.getSnapshot().active, null)
  assert.deepEqual(owner.failures, [])
  await owner.finish()
})

test('all backend workload types share Main FIFO ordering with each other', async () => {
  const owner = harness()
  owner.pipe.send(acquire(1, 'ollama'), acquire(2, 'gpt-sovits'), acquire(3, 'image-generation'))
  await immediate()
  assert.deepEqual(owner.pipe.replies(), [{ id: id(1), status: 'granted' }])
  assert.deepEqual(owner.gpu.getSnapshot().queued.map((entry) => entry.owner), ['gpt-sovits', 'image-generation'])
  owner.pipe.send(control('release', 1))
  await immediate()
  assert.equal(owner.gpu.getSnapshot().active.owner, 'gpt-sovits')
  owner.pipe.send(control('release', 2))
  await immediate()
  assert.equal(owner.gpu.getSnapshot().active.owner, 'image-generation')
  owner.pipe.send(control('release', 3))
  assert.deepEqual(owner.pipe.replies().map((reply) => reply.id), [id(1), id(2), id(3)])
  await owner.finish()
})

test('a queued backend acquisition waits behind a Main-owned Song Cover and cancels independently', async () => {
  const owner = harness()
  const cover = await owner.gpu.acquire('song-cover')
  owner.pipe.send(acquire(1, 'image-generation'))
  await immediate()
  assert.deepEqual(owner.pipe.replies(), [])
  owner.pipe.send(control('cancel', 1))
  await immediate()
  assert.deepEqual(owner.pipe.replies(), [{ id: id(1), status: 'cancelled' }])
  assert.equal(owner.gpu.getSnapshot().active.owner, 'song-cover')
  assert.deepEqual(owner.gpu.getSnapshot().queued, [])
  cover.release()
  await owner.finish()
})

test('a grant winning a same-chunk cancellation race still requires an explicit release', async () => {
  const owner = harness()
  owner.pipe.send(acquire(1), control('cancel', 1))
  await immediate()
  assert.deepEqual(owner.pipe.replies(), [{ id: id(1), status: 'granted' }])
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  assert.equal(owner.gpu.getSnapshot().active.stopping, true)
  owner.pipe.send(control('release', 1))
  assert.equal(owner.gpu.getSnapshot().active, null)
  await owner.finish()
})

test('cancelling an acknowledged active turn never grants its queued replacement early', async () => {
  const owner = harness()
  owner.pipe.send(acquire(1), acquire(2, 'image-generation'))
  await immediate()
  owner.pipe.send(control('cancel', 1))
  await immediate()
  assert.deepEqual(owner.pipe.replies(), [{ id: id(1), status: 'granted' }])
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  owner.pipe.send(control('release', 1))
  await immediate()
  assert.deepEqual(owner.pipe.replies(), [
    { id: id(1), status: 'granted' }, { id: id(2), status: 'granted' },
  ])
  owner.pipe.send(control('release', 2))
  await owner.finish()
})

test('broken or disposed transport retains physical ownership until confirmed process exit', async () => {
  const owner = harness()
  owner.pipe.send(acquire(1))
  await immediate()
  const coverPromise = owner.gpu.acquire('song-cover')
  owner.broker.dispose()
  await immediate()
  assert.equal(owner.pipe.destroyed, true)
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  assert.equal(owner.gpu.getSnapshot().active.stopping, true)
  assert.equal(owner.gpu.getSnapshot().queued.length, 1)
  owner.broker.processExited()
  const cover = await coverPromise
  assert.equal(cover.owner, 'song-cover')
  cover.release()
  assert.deepEqual(owner.failures, [])
  await owner.finish()
})

test('disposal between a synchronous queue grant and its promise continuation cannot leak a lease', async () => {
  const owner = harness()
  owner.pipe.send(acquire(1))
  owner.broker.dispose()
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  owner.broker.processExited()
  await immediate()
  assert.equal(owner.gpu.getSnapshot().active, null)
  assert.deepEqual(owner.pipe.replies(), [])
  await owner.finish()
})

test('disposed queued waits cannot become active after the prior Main owner releases', async () => {
  const owner = harness()
  const cover = await owner.gpu.acquire('song-cover')
  owner.pipe.send(acquire(1), acquire(2, 'gpt-sovits'))
  owner.broker.dispose()
  owner.broker.processExited()
  cover.release()
  await immediate()
  assert.equal(owner.gpu.getSnapshot().active, null)
  assert.deepEqual(owner.gpu.getSnapshot().queued, [])
  assert.deepEqual(owner.pipe.replies(), [])
  await owner.finish()
})

test('poison prevents release and denies later work until the backend physically exits', async () => {
  const owner = harness()
  owner.pipe.send(acquire(1, 'gpt-sovits'), acquire(2, 'image-generation'))
  await immediate()
  owner.pipe.send(control('poison', 1))
  await immediate()
  assert.deepEqual(owner.failures, ['runtime-poisoned'])
  assert.deepEqual(owner.pipe.replies(), [
    { id: id(1), status: 'granted' }, { id: id(2), status: 'denied' },
  ])
  owner.pipe.send(control('release', 1), acquire(3, 'ollama'))
  await immediate()
  assert.equal(owner.gpu.getSnapshot().active.owner, 'gpt-sovits')
  assert.equal(owner.gpu.getSnapshot().active.stopping, true)
  assert.deepEqual(owner.pipe.replies().at(-1), { id: id(3), status: 'denied' })
  await owner.finish()
  assert.equal(owner.gpu.getSnapshot().active, null)
})

for (const malformed of [
  {}, [], null,
  { ...acquire(1), owner: 'song-cover' },
  { ...acquire(1), owner: 'C:\\private\\model' },
  { ...acquire(1), id: 'a'.repeat(31) },
  { ...acquire(1), id: 'A'.repeat(32) },
  { ...acquire(1), path: 'private' },
  { ...control('release', 1), owner: 'ollama' },
  { ...control('release', 1), action: 'reset' },
]) {
  test(`strict private request grammar rejects ${JSON.stringify(malformed)}`, async () => {
    const owner = harness()
    owner.pipe.send(malformed)
    await immediate()
    assert.deepEqual(owner.failures, ['invalid-request'])
    assert.equal(owner.pipe.destroyed, true)
    assert.equal(owner.gpu.getSnapshot().active, null)
    assert.deepEqual(owner.pipe.replies(), [])
    await owner.finish()
  })
}

test('duplicate JSON keys are rejected rather than interpreted by last-key precedence', async () => {
  const owner = harness()
  owner.pipe.emit('data', Buffer.from(`{"action":"acquire","id":"${id(1)}","owner":"ollama","owner":"gpt-sovits"}\n`))
  await immediate()
  assert.deepEqual(owner.failures, ['invalid-request'])
  assert.equal(owner.gpu.getSnapshot().active, null)
  await owner.finish()
})

test('ordinary JSON spacing and property order are accepted without loosening fields', async () => {
  const owner = harness()
  const line = `  { "owner": "ollama", "id": "${id(1)}", "action": "acquire" } \r\n`
  for (const byte of Buffer.from(line)) owner.pipe.emit('data', Buffer.from([byte]))
  await immediate()
  assert.deepEqual(owner.pipe.replies(), [{ id: id(1), status: 'granted' }])
  await owner.finish()
})

for (const [name, emit] of [
  ['oversized line', (pipe) => pipe.emit('data', Buffer.alloc(GPU_BACKEND_MAX_FRAME_BYTES + 1, 32))],
  ['invalid UTF-8', (pipe) => pipe.emit('data', Buffer.from([0xc0, 0xaf, 10]))],
  ['truncated EOF', (pipe) => { pipe.emit('data', Buffer.from('{')); pipe.emit('end') }],
  ['string data', (pipe) => pipe.emit('data', '{}\n')],
  ['clean EOF before process exit', (pipe) => pipe.emit('end')],
  ['native error', (pipe) => pipe.emit('error', new Error('private native diagnostic'))],
]) {
  test(`${name} closes the broker with a sanitized transport failure`, async () => {
    const owner = harness()
    emit(owner.pipe)
    await immediate()
    assert.deepEqual(owner.failures, ['stream-failed'])
    assert.deepEqual(owner.pipe.replies(), [])
    assert.equal(owner.pipe.destroyed, true)
    await owner.finish()
  })
}

test('unknown release cannot unlock another process owner', async () => {
  const owner = harness()
  const cover = await owner.gpu.acquire('song-cover')
  owner.pipe.send(control('release', 99))
  assert.deepEqual(owner.failures, ['invalid-request'])
  assert.equal(owner.gpu.getSnapshot().active.generation, cover.generation)
  cover.release()
  await owner.finish()
})

test('a duplicate old release cannot release the next acquired identity', async () => {
  const owner = harness()
  owner.pipe.send(acquire(1), acquire(2))
  await immediate()
  owner.pipe.send(control('release', 1))
  await immediate()
  const generation = owner.gpu.getSnapshot().active.generation
  owner.pipe.send(control('release', 1))
  assert.deepEqual(owner.failures, ['invalid-request'])
  assert.equal(owner.gpu.getSnapshot().active.generation, generation)
  await owner.finish()
})

test('pending requests and completed identity history have explicit hard ceilings', async () => {
  assert.equal(GPU_BACKEND_MAX_FRAME_BYTES, 512)
  assert.equal(GPU_BACKEND_MAX_PENDING, 8)
  assert.equal(GPU_BACKEND_MAX_RETIRED, 4096)
  const owner = harness()
  const cover = await owner.gpu.acquire('song-cover')
  owner.pipe.send(...Array.from({ length: 9 }, (_, index) => acquire(index + 1)))
  await immediate()
  assert.equal(owner.gpu.getSnapshot().queued.length, 8)
  assert.deepEqual(owner.pipe.replies(), [{ id: id(9), status: 'denied' }])
  owner.broker.processExited()
  cover.release()
  await owner.finish()
})

test('replay history exhaustion fails closed rather than dropping an old identity', async () => {
  const owner = harness({ maxRetired: 2 })
  for (const value of [1, 2]) {
    owner.pipe.send(acquire(value))
    await immediate()
    owner.pipe.send(control('release', value))
  }
  owner.pipe.send(acquire(3))
  assert.deepEqual(owner.failures, ['capacity-exceeded'])
  assert.equal(owner.pipe.destroyed, true)
  assert.equal(owner.gpu.getSnapshot().active, null)
  await owner.finish()
})

test('an acquisition ID cannot be replayed after a cancelled queued wait', async () => {
  const owner = harness()
  const cover = await owner.gpu.acquire('song-cover')
  owner.pipe.send(acquire(1), control('cancel', 1))
  await immediate()
  owner.pipe.send(acquire(1))
  assert.deepEqual(owner.failures, ['invalid-request'])
  cover.release()
  await owner.finish()
})

test('late cancellation after a queue deadline is idempotent without unlocking Main', async () => {
  const owner = harness({ waitTimeoutMs: 10 })
  const cover = await owner.gpu.acquire('song-cover')
  owner.pipe.send(acquire(1))
  await delay(25)
  assert.deepEqual(owner.pipe.replies(), [{ id: id(1), status: 'denied' }])
  owner.pipe.send(control('cancel', 1))
  assert.deepEqual(owner.failures, [])
  assert.equal(owner.gpu.getSnapshot().active.generation, cover.generation)
  cover.release()
  await owner.finish()
})

test('reply backpressure cannot accumulate an unbounded outgoing queue or free active work', async () => {
  const owner = harness({}, { blockWrite: true, highWaterMark: 1 })
  owner.pipe.send(acquire(1))
  await immediate()
  assert.deepEqual(owner.failures, ['write-blocked'])
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  assert.equal(owner.pipe.destroyed, true)
  await owner.finish()
})

test('write-callback failure consumes its later native error and retains the lease until exit', async () => {
  const owner = harness({}, { failWrite: true })
  owner.pipe.send(acquire(1))
  await immediate()
  await immediate()
  assert.deepEqual(owner.failures, ['stream-failed'])
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  assert.deepEqual(owner.pipe.replies(), [])
  await owner.finish()
})

test('physical exit cleanup is idempotent and removes every owned stream listener', async () => {
  const owner = harness()
  owner.pipe.send(acquire(1))
  await immediate()
  owner.broker.processExited()
  owner.broker.processExited()
  owner.broker.dispose()
  await immediate()
  for (const event of ['data', 'end', 'error', 'close']) {
    assert.equal(owner.pipe.listenerCount(event), 0, `${event} listener leaked`)
  }
  assert.equal(owner.gpu.getSnapshot().active, null)
  await owner.finish()
})

test('preparation holds global ownership before granting the backend its turn', async () => {
  const preparation = deferred()
  let preparedOwner
  const owner = harness({ prepare: (kind) => { preparedOwner = kind; return preparation.promise } })
  owner.pipe.send(acquire(1, 'image-generation'))
  await immediate()
  assert.equal(preparedOwner, 'image-generation')
  assert.equal(owner.gpu.getSnapshot().active.owner, 'image-generation')
  assert.deepEqual(owner.pipe.replies(), [])
  preparation.resolve()
  await immediate()
  assert.deepEqual(owner.pipe.replies(), [{ id: id(1), status: 'granted' }])
  owner.pipe.send(control('release', 1))
  await owner.finish()
})

test('failed preparation denies Python but quarantines capacity until external cleanup succeeds', async () => {
  const confirmation = deferred()
  const owner = harness({
    prepare: async () => { throw new Error('private daemon path') },
    quiesceExternal: () => confirmation.promise,
  })
  owner.pipe.send(acquire(1, 'image-generation'))
  await immediate()
  assert.deepEqual(owner.pipe.replies(), [{ id: id(1), status: 'denied' }])
  assert.deepEqual(owner.failures, ['prepare-failed'])
  owner.pipe.send(control('release', 1))
  assert.equal(owner.gpu.getSnapshot().active.owner, 'image-generation')
  const exited = owner.broker.processExited()
  await immediate()
  assert.equal(owner.gpu.getSnapshot().active.owner, 'image-generation')
  confirmation.resolve()
  await exited
  assert.equal(owner.gpu.getSnapshot().active, null)
  await owner.finish()
})

test('a premature release during preparation cannot unlock its physical lease', async () => {
  const preparation = deferred()
  const owner = harness({ prepare: () => preparation.promise })
  owner.pipe.send(acquire(1, 'image-generation'))
  await immediate()
  owner.pipe.send(control('release', 1))
  assert.deepEqual(owner.failures, ['invalid-request'])
  assert.equal(owner.gpu.getSnapshot().active.owner, 'image-generation')
  preparation.resolve()
  await owner.finish()
})

test('external daemon confirmation fences a dead backend lease from the next Main owner', async () => {
  const confirmation = deferred()
  const owner = harness({ quiesceExternal: () => confirmation.promise })
  owner.pipe.send(acquire(1, 'ollama'))
  await immediate()
  const coverPromise = owner.gpu.acquire('song-cover')
  const exited = owner.broker.processExited()
  await immediate()
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  assert.equal(owner.gpu.getSnapshot().queued.length, 1)
  confirmation.resolve()
  await exited
  const cover = await coverPromise
  cover.release()
  await owner.finish()
})

test('failed external confirmation retains capacity and permits an explicit cleanup retry', async () => {
  let attempts = 0
  const owner = harness({ quiesceExternal: async () => {
    attempts += 1
    if (attempts === 1) throw new Error('private external model diagnostics')
  } })
  owner.pipe.send(acquire(1))
  await immediate()
  await assert.rejects(owner.broker.processExited(), (error) => {
    assert.equal(error.message.includes('private'), false)
    return true
  })
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  assert.deepEqual(owner.failures, ['external-residency'])
  await owner.broker.processExited()
  assert.equal(owner.gpu.getSnapshot().active, null)
  assert.equal(attempts, 2)
  await owner.finish()
})

test('physical exit joins a still-running preparation before external cleanup and release', async () => {
  const preparation = deferred()
  let preparationSignal
  let confirmed = false
  const owner = harness({
    prepare: (_kind, signal) => { preparationSignal = signal; return preparation.promise },
    quiesceExternal: async () => { confirmed = true },
  })
  owner.pipe.send(acquire(1))
  await immediate()
  const exited = owner.broker.processExited()
  await immediate()
  assert.equal(preparationSignal.aborted, true)
  assert.equal(confirmed, false)
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  preparation.resolve()
  await exited
  assert.equal(confirmed, true)
  assert.deepEqual(owner.pipe.replies(), [])
  assert.equal(owner.gpu.getSnapshot().active, null)
  await owner.finish()
})

test('normal Ollama release retains physical ownership through independent unload confirmation', async () => {
  const confirmation = deferred()
  let cleanupSignal
  const owner = harness({ quiesceExternal: (signal) => { cleanupSignal = signal; return confirmation.promise } })
  owner.pipe.send(acquire(1), acquire(2, 'image-generation'))
  await immediate()
  owner.pipe.send(control('cancel', 1), control('release', 1))
  await immediate()
  assert.equal(cleanupSignal.aborted, false)
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  assert.deepEqual(owner.pipe.replies(), [{ id: id(1), status: 'granted' }])
  confirmation.resolve()
  await immediate()
  assert.equal(owner.gpu.getSnapshot().active.owner, 'image-generation')
  owner.pipe.send(control('release', 2))
  assert.deepEqual(owner.failures, [])
  await owner.finish()
})

test('failed normal Ollama unload quarantines its lease until a confirmed exit cleanup retry', async () => {
  let attempts = 0
  const confirmation = deferred()
  const owner = harness({ quiesceExternal: () => {
    attempts += 1
    if (attempts === 1) throw new Error('private unload failure')
    return confirmation.promise
  } })
  owner.pipe.send(acquire(1), acquire(2, 'image-generation'))
  await immediate()
  owner.pipe.send(control('release', 1))
  await immediate()
  assert.deepEqual(owner.failures, ['external-residency'])
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  assert.equal(owner.gpu.getSnapshot().active.stopping, true)
  assert.deepEqual(owner.pipe.replies().at(-1), { id: id(2), status: 'denied' })
  owner.pipe.send(control('release', 1), acquire(3, 'gpt-sovits'))
  await immediate()
  assert.deepEqual(owner.pipe.replies().at(-1), { id: id(3), status: 'denied' })
  const exited = owner.broker.processExited()
  await immediate()
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  confirmation.resolve()
  await exited
  assert.equal(attempts, 2)
  assert.equal(owner.gpu.getSnapshot().active, null)
  await owner.finish()
})

test('disposal during normal Ollama unload still requires confirmed physical exit', async () => {
  const firstConfirmation = deferred()
  const exitConfirmation = deferred()
  let attempts = 0
  const owner = harness({ quiesceExternal: () => {
    attempts += 1
    return attempts === 1 ? firstConfirmation.promise : exitConfirmation.promise
  } })
  owner.pipe.send(acquire(1))
  await immediate()
  owner.pipe.send(control('release', 1))
  await immediate()
  owner.broker.dispose()
  firstConfirmation.resolve()
  await immediate()
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  const exited = owner.broker.processExited()
  await immediate()
  assert.equal(attempts, 2)
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  exitConfirmation.resolve()
  await exited
  assert.equal(owner.gpu.getSnapshot().active, null)
  await owner.finish()
})

test('a duplicate release cannot bypass an Ollama unload already in progress', async () => {
  const confirmation = deferred()
  const owner = harness({ quiesceExternal: () => confirmation.promise })
  owner.pipe.send(acquire(1))
  await immediate()
  owner.pipe.send(control('release', 1), control('release', 1))
  assert.deepEqual(owner.failures, ['invalid-request'])
  assert.equal(owner.gpu.getSnapshot().active.owner, 'ollama')
  confirmation.resolve()
  await owner.finish()
})

test('native Python fd5 exchanges acquisition and release over the inherited duplex pipe', async () => {
  const projectRoot = fileURLToPath(new URL('../../', import.meta.url))
  const candidates = process.env.ELYSIA_TEST_PYTHON
    ? [process.env.ELYSIA_TEST_PYTHON]
    : [path.join(projectRoot, '.venv', 'Scripts', 'python.exe'), path.join(projectRoot, '.venv', 'bin', 'python'), 'python3', 'python']
  const python = candidates.find((candidate) => {
    const result = spawnSync(candidate, ['--version'], { encoding: 'utf8', timeout: 5_000, windowsHide: true })
    return result.status === 0 && /^Python /u.test(`${result.stdout}${result.stderr}`.trim())
  })
  assert.ok(python, 'A Python interpreter is required for the native GPU broker contract.')
  const script = [
    '"""Exchange a bounded GPU lease on the actual inherited private duplex descriptor."""',
    'import json, os, sys',
    'os.set_inheritable(5, False)',
    `request_id = "${id(1)}"`,
    'os.write(5, (json.dumps({"action": "acquire", "id": request_id, "owner": "ollama"}) + "\\n").encode("ascii"))',
    'reply = bytearray()',
    'while b"\\n" not in reply:',
    '    piece = os.read(5, 512 - len(reply))',
    '    if not piece or len(reply) + len(piece) > 512:',
    '        raise RuntimeError("Private GPU reply is invalid.")',
    '    reply.extend(piece)',
    'value = json.loads(reply)',
    'assert value == {"id": request_id, "status": "granted"}',
    'os.write(5, (json.dumps({"action": "release", "id": request_id}) + "\\n").encode("ascii"))',
    'print(json.dumps({"status": value["status"], "inheritable": os.get_inheritable(5)}), flush=True)',
    'sys.stdin.buffer.read(1)',
  ].join('\n')
  const child = spawn(python, ['-X', 'utf8', '-c', script], {
    cwd: projectRoot,
    stdio: ['pipe', 'pipe', 'pipe', 'pipe', 'pipe', 'pipe'],
    windowsHide: true,
  })
  const gpu = new GpuAdmissionCoordinator()
  const failures = []
  const broker = new GpuBackendBroker(child.stdio[5], gpu, (failure) => failures.push(failure))
  const stdout = []
  const stderr = []
  const media = []
  child.stdout.on('data', (bytes) => stdout.push(bytes))
  child.stderr.on('data', (bytes) => stderr.push(bytes))
  child.stdio[3].on('data', (bytes) => media.push(bytes))
  child.stdio[4].on('data', (bytes) => media.push(bytes))
  let deadline
  const closed = new Promise((resolve, reject) => {
    deadline = setTimeout(() => { child.kill(); reject(new Error('Native GPU pipe exchange timed out.')) }, 10_000)
    child.once('error', reject)
    child.once('close', resolve)
  })
  void closed.catch(() => {})
  try {
    await new Promise((resolve, reject) => {
      child.stdout.once('data', resolve)
      child.once('error', reject)
      child.once('close', (code) => reject(new Error(`Native GPU client exited before exchange (${String(code)}): ${Buffer.concat(stderr).toString('utf8')}`)))
    })
    // stdout and fd5 have independent scheduling. Wait until Main consumes the
    // explicit release before closing the pipe and allowing the child to exit.
    for (let attempt = 0; attempt < 100 && gpu.getSnapshot().active !== null; attempt += 1) {
      await immediate()
    }
    assert.equal(gpu.getSnapshot().active, null)
    broker.dispose()
    child.stdin.write('q')
    assert.equal(await closed, 0, Buffer.concat(stderr).toString('utf8'))
    broker.processExited()
    assert.deepEqual(JSON.parse(Buffer.concat(stdout).toString('utf8')), { status: 'granted', inheritable: false })
    assert.equal(Buffer.concat(stderr).length, 0)
    assert.equal(Buffer.concat(media).length, 0)
    assert.deepEqual(failures, [])
  } finally {
    clearTimeout(deadline)
    child.kill()
    if (child.exitCode === null && child.signalCode === null) {
      await new Promise((resolve) => child.once('close', resolve))
    }
    broker.processExited()
    await gpu.close()
  }
})
