/** Verify bounded GPU admission, physical ownership, cancellation, and shutdown. */

import assert from 'node:assert/strict'
import { getEventListeners } from 'node:events'
import test from 'node:test'

import {
  GPU_ADMISSION_MAX_QUEUE,
  GPU_ADMISSION_MAX_WAIT_MS,
  GpuAdmissionCoordinator,
  GpuAdmissionError,
} from '../dist-electron/gpu-admission.js'

function hasReason(reason) {
  return (error) => error instanceof GpuAdmissionError && error.reason === reason
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((accepted, rejected) => { resolve = accepted; reject = rejected })
  return { promise, resolve, reject }
}

function immediate() {
  return new Promise((resolve) => setImmediate(resolve))
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

test('Ollama, speech, cover, and image workloads receive exclusive FIFO turns', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const active = await gpu.acquire('ollama')
  const admitted = []
  const queued = ['gpt-sovits', 'song-cover', 'image-generation'].map((owner) => (
    gpu.acquire(owner).then((lease) => { admitted.push(owner); return lease })
  ))
  await immediate()
  assert.deepEqual(admitted, [])
  assert.deepEqual(gpu.getSnapshot().queued.map((entry) => entry.owner), ['gpt-sovits', 'song-cover', 'image-generation'])
  assert.equal(active.release(), true)
  const speech = await queued[0]
  assert.deepEqual(admitted, ['gpt-sovits'])
  speech.release()
  const cover = await queued[1]
  assert.deepEqual(admitted, ['gpt-sovits', 'song-cover'])
  cover.release()
  const image = await queued[2]
  assert.deepEqual(admitted, ['gpt-sovits', 'song-cover', 'image-generation'])
  image.release()
  assert.equal(gpu.getSnapshot().active, null)
  await gpu.close()
})

test('queue capacity is bounded at eight independently of the current lease', async () => {
  assert.equal(GPU_ADMISSION_MAX_QUEUE, 8)
  assert.equal(GPU_ADMISSION_MAX_WAIT_MS, 120_000)
  const gpu = new GpuAdmissionCoordinator()
  const active = await gpu.acquire('ollama')
  const queued = Array.from({ length: GPU_ADMISSION_MAX_QUEUE }, () => gpu.acquire('image-generation'))
  await assert.rejects(gpu.acquire('song-cover'), hasReason('queue-full'))
  assert.equal(gpu.getSnapshot().queued.length, 8)
  active.release()
  for (const waiting of queued) {
    const next = await waiting
    next.release()
  }
  await gpu.close()
})

test('a zero-capacity coordinator admits idle work and rejects all waiting work', async () => {
  const gpu = new GpuAdmissionCoordinator({ maxQueue: 0 })
  const active = await gpu.acquire('ollama')
  await assert.rejects(gpu.acquire('gpt-sovits'), hasReason('queue-full'))
  active.release()
  const next = await gpu.acquire('gpt-sovits')
  next.release()
  await gpu.close()
})

for (const options of [
  { maxQueue: 9 }, { maxQueue: -1 }, { maxQueue: 0.5 },
  { defaultWaitMs: 0 }, { defaultWaitMs: 120_001 }, { defaultWaitMs: Number.NaN },
  { closeTimeoutMs: -1 }, { closeTimeoutMs: 120_001 }, { closeTimeoutMs: Number.POSITIVE_INFINITY },
]) {
  test(`invalid coordinator policy is rejected (${JSON.stringify(options)})`, () => {
    assert.throws(() => new GpuAdmissionCoordinator(options), hasReason('invalid-request'))
  })
}

test('invalid workload and queue deadlines cannot consume admission capacity', async () => {
  const gpu = new GpuAdmissionCoordinator()
  await assert.rejects(gpu.acquire('renderer-selected-path'), hasReason('invalid-request'))
  for (const timeoutMs of [0, -1, 0.5, 120_001, Number.POSITIVE_INFINITY]) {
    await assert.rejects(gpu.acquire('ollama', { timeoutMs }), hasReason('invalid-request'))
  }
  assert.deepEqual(gpu.getSnapshot(), { closed: false, active: null, queued: [] })
  await gpu.close()
})

test('already-cancelled work never enters the queue or registers a listener', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const source = new AbortController()
  source.abort(new Error('private worker path'))
  await assert.rejects(gpu.acquire('ollama', { signal: source.signal }), hasReason('cancelled'))
  assert.equal(getEventListeners(source.signal, 'abort').length, 0)
  assert.equal(gpu.getSnapshot().active, null)
  await gpu.close()
})

test('cancelling a middle waiter frees capacity without reordering surviving work', async () => {
  const gpu = new GpuAdmissionCoordinator({ maxQueue: 3 })
  const active = await gpu.acquire('ollama')
  const first = gpu.acquire('gpt-sovits')
  const source = new AbortController()
  const removed = assert.rejects(gpu.acquire('song-cover', { signal: source.signal }), hasReason('cancelled'))
  const last = gpu.acquire('image-generation')
  source.abort()
  await removed
  assert.equal(getEventListeners(source.signal, 'abort').length, 0)
  const replacement = gpu.acquire('song-cover')
  assert.deepEqual(gpu.getSnapshot().queued.map((entry) => entry.owner), ['gpt-sovits', 'image-generation', 'song-cover'])
  active.release()
  ;(await first).release()
  ;(await last).release()
  ;(await replacement).release()
  await gpu.close()
})

test('a queue deadline removes only that waiter and leaves physical work active', async () => {
  const gpu = new GpuAdmissionCoordinator({ defaultWaitMs: 10 })
  const active = await gpu.acquire('ollama')
  const source = new AbortController()
  const timedOut = assert.rejects(gpu.acquire('gpt-sovits', { signal: source.signal }), hasReason('wait-timeout'))
  await delay(25)
  await timedOut
  assert.equal(gpu.getSnapshot().active.owner, 'ollama')
  assert.equal(active.signal.aborted, false)
  assert.deepEqual(gpu.getSnapshot().queued, [])
  assert.equal(getEventListeners(source.signal, 'abort').length, 0)
  active.release()
  await gpu.close()
})

test('a granted lease no longer has its queue deadline', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const active = await gpu.acquire('ollama')
  const waiting = gpu.acquire('song-cover', { timeoutMs: 10 })
  active.release()
  const lease = await waiting
  await delay(25)
  assert.equal(gpu.getSnapshot().active.owner, 'song-cover')
  assert.equal(lease.signal.aborted, false)
  lease.release()
  await gpu.close()
})

test('active cancellation requests teardown while retaining its physical lease', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const source = new AbortController()
  const active = await gpu.acquire('ollama', { signal: source.signal })
  let admitted = false
  const waiting = gpu.acquire('image-generation').then((lease) => { admitted = true; return lease })
  source.abort(new Error('private native diagnostic'))
  await immediate()
  assert.equal(active.signal.aborted, true)
  assert.equal(active.signal.reason.reason, 'cancelled')
  assert.equal(String(active.signal.reason).includes('private'), false)
  assert.equal(gpu.getSnapshot().active.stopping, true)
  assert.equal(admitted, false)
  active.release()
  const next = await waiting
  assert.equal(admitted, true)
  next.release()
  assert.equal(getEventListeners(source.signal, 'abort').length, 0)
  await gpu.close()
})

test('duplicate and stale releases cannot unlock a later generation of the same owner', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const old = await gpu.acquire('image-generation')
  old.release()
  const current = await gpu.acquire('image-generation')
  assert.ok(current.generation > old.generation)
  assert.equal(old.release(), false)
  assert.equal(old.release(), false)
  assert.equal(gpu.getSnapshot().active.generation, current.generation)
  assert.equal(current.release(), true)
  assert.equal(current.release(), false)
  await gpu.close()
})

test('normal release detaches the source signal before its later cancellation', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const source = new AbortController()
  const old = await gpu.acquire('ollama', { signal: source.signal })
  assert.equal(getEventListeners(source.signal, 'abort').length, 1)
  old.release()
  assert.equal(getEventListeners(source.signal, 'abort').length, 0)
  const current = await gpu.acquire('ollama')
  source.abort()
  assert.equal(current.signal.aborted, false)
  current.release()
  await gpu.close()
})

test('run preserves ownership until an aborted worker physically finishes', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const source = new AbortController()
  const physical = deferred()
  let workerSignal
  const running = gpu.run('gpt-sovits', source.signal, (signal) => { workerSignal = signal; return physical.promise })
  const rejected = assert.rejects(running, hasReason('cancelled'))
  await immediate()
  const waiting = gpu.acquire('image-generation')
  source.abort()
  await immediate()
  assert.equal(workerSignal.aborted, true)
  assert.equal(gpu.getSnapshot().active.owner, 'gpt-sovits')
  assert.equal(gpu.getSnapshot().queued.length, 1)
  physical.resolve('late result')
  await rejected
  const next = await waiting
  assert.equal(next.owner, 'image-generation')
  next.release()
  await gpu.close()
})

test('run holds capacity through failure cleanup and propagates the operation error', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const physical = deferred()
  const workerFailure = new Error('worker failed after authoritative termination')
  const running = gpu.run('song-cover', undefined, () => physical.promise)
  const rejected = assert.rejects(running, (error) => error === workerFailure)
  await immediate()
  const waiting = gpu.acquire('image-generation')
  assert.equal(gpu.getSnapshot().active.owner, 'song-cover')
  physical.reject(workerFailure)
  await rejected
  const next = await waiting
  next.release()
  await gpu.close()
})

test('run releases on synchronous failure and returns successful worker values', async () => {
  const gpu = new GpuAdmissionCoordinator()
  await assert.rejects(gpu.run('ollama', undefined, () => { throw new Error('failed') }), /failed/u)
  assert.equal(gpu.getSnapshot().active, null)
  assert.equal(await gpu.run('ollama', undefined, () => 42), 42)
  assert.equal(gpu.getSnapshot().active, null)
  await gpu.close()
})

test('cancellation after grant but before run starts does not launch its worker', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const source = new AbortController()
  let started = false
  const running = gpu.run('ollama', source.signal, () => { started = true })
  source.abort()
  await assert.rejects(running, hasReason('cancelled'))
  assert.equal(started, false)
  assert.equal(gpu.getSnapshot().active, null)
  await gpu.close()
})

test('owner cancellation aborts only that subsystem and preserves other FIFO work', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const active = await gpu.acquire('ollama')
  const rejected = assert.rejects(gpu.acquire('ollama'), hasReason('cancelled'))
  const waiting = gpu.acquire('song-cover')
  gpu.cancelOwner('ollama')
  await rejected
  assert.equal(active.signal.aborted, true)
  assert.equal(gpu.getSnapshot().active.owner, 'ollama')
  assert.deepEqual(gpu.getSnapshot().queued.map((entry) => entry.owner), ['song-cover'])
  active.release()
  const cover = await waiting
  assert.equal(cover.signal.aborted, false)
  cover.release()
  await gpu.close()
})

test('close rejects all waiters and joins the active physical release', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const active = await gpu.acquire('song-cover')
  const source = new AbortController()
  const rejected = assert.rejects(gpu.acquire('image-generation', { signal: source.signal }), hasReason('closed'))
  const closing = gpu.close()
  assert.equal(gpu.close(), closing)
  let drained = false
  void closing.then(() => { drained = true })
  await rejected
  await immediate()
  assert.equal(active.signal.aborted, true)
  assert.equal(active.signal.reason.reason, 'closed')
  assert.equal(drained, false)
  assert.equal(gpu.getSnapshot().closed, true)
  assert.equal(gpu.getSnapshot().active.owner, 'song-cover')
  assert.equal(getEventListeners(source.signal, 'abort').length, 0)
  await assert.rejects(gpu.acquire('ollama'), hasReason('closed'))
  active.release()
  await closing
  assert.equal(drained, true)
  assert.equal(gpu.getSnapshot().active, null)
})

test('close can join a synchronous abort handler that confirms physical termination', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const active = await gpu.acquire('song-cover')
  active.signal.addEventListener('abort', () => active.release(), { once: true })
  await gpu.close()
  assert.equal(gpu.getSnapshot().active, null)
})

test('drain timeout retains honest ownership and never reopens capacity', async () => {
  const gpu = new GpuAdmissionCoordinator({ closeTimeoutMs: 10 })
  const active = await gpu.acquire('gpt-sovits')
  const rejected = assert.rejects(gpu.close(), hasReason('drain-timeout'))
  await delay(25)
  await rejected
  assert.deepEqual(gpu.getSnapshot().active, { owner: 'gpt-sovits', generation: active.generation, stopping: true })
  assert.equal(gpu.getSnapshot().closed, true)
  await assert.rejects(gpu.acquire('image-generation'), hasReason('closed'))
  active.release()
  await gpu.close()
  assert.equal(gpu.getSnapshot().active, null)
})

test('snapshot and lease objects are immutable and expose no paths or request data', async () => {
  const gpu = new GpuAdmissionCoordinator()
  const active = await gpu.acquire('ollama')
  const waiting = gpu.acquire('image-generation')
  const snapshot = gpu.getSnapshot()
  assert.equal(Object.isFrozen(active), true)
  assert.equal(Object.isFrozen(snapshot), true)
  assert.equal(Object.isFrozen(snapshot.active), true)
  assert.equal(Object.isFrozen(snapshot.queued), true)
  assert.equal(Object.isFrozen(snapshot.queued[0]), true)
  assert.deepEqual(Object.keys(snapshot), ['closed', 'active', 'queued'])
  assert.deepEqual(Object.keys(snapshot.active), ['owner', 'generation', 'stopping'])
  assert.deepEqual(Object.keys(snapshot.queued[0]), ['owner', 'generation'])
  active.release()
  ;(await waiting).release()
  await gpu.close()
})
