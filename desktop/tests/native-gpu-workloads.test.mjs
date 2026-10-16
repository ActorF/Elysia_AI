/** Verify native GPU ownership includes Ollama unload and failed cleanup retry. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { GpuAdmissionCoordinator } from '../dist-electron/gpu-admission.js'
import { NativeGpuWorkloads } from '../dist-electron/native-gpu-workloads.js'

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

test('native worker starts only after prior model residency is cleared', async () => {
  const admission = new GpuAdmissionCoordinator()
  const prepared = deferred()
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => 'http://127.0.0.1:11434',
    quiesce: async () => prepared.promise,
  })
  const pending = native.acquire('song-cover')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(admission.getSnapshot().active.owner, 'song-cover')
  let granted = false
  void pending.then(() => { granted = true })
  assert.equal(granted, false)
  prepared.resolve()
  const lease = await pending
  assert.equal(granted, true)
  lease.release()
  await admission.close()
})

test('failed prepare retains exact capacity until a later unload retry succeeds', async () => {
  const admission = new GpuAdmissionCoordinator()
  let unavailable = true
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => 'http://127.0.0.1:11434',
    quiesce: async () => { if (unavailable) throw new Error('private origin') },
  })
  await assert.rejects(native.acquire('song-cover'), /could not be prepared/u)
  assert.equal(admission.getSnapshot().active.owner, 'song-cover')
  unavailable = false
  await native.recover()
  assert.equal(admission.getSnapshot().active, null)
  const lease = await native.acquire('image-generation')
  lease.release()
  await admission.close()
})

test('Ollama inference retains lease until its final unload confirmation settles', async () => {
  const admission = new GpuAdmissionCoordinator()
  const cleanup = deferred()
  let calls = 0
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => 'http://127.0.0.1:11434',
    quiesce: async () => { if (++calls === 2) await cleanup.promise },
  })
  const operation = native.runOllama(new AbortController().signal, async () => 'pixels')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(admission.getSnapshot().active.owner, 'ollama')
  assert.equal(calls, 2)
  cleanup.resolve()
  assert.equal(await operation, 'pixels')
  assert.equal(admission.getSnapshot().active, null)
  await admission.close()
})

test('inference failure and cancellation still use an independent cleanup deadline', async () => {
  const admission = new GpuAdmissionCoordinator()
  const abort = new AbortController()
  const signals = []
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => 'http://127.0.0.1:11434',
    quiesce: async (_origin, signal) => { signals.push(signal) },
  })
  await assert.rejects(native.runOllama(abort.signal, async () => {
    abort.abort()
    throw new Error('inference failed')
  }), /inference failed/u)
  assert.equal(signals[0].aborted, true)
  assert.equal(signals[1], undefined)
  assert.equal(admission.getSnapshot().active, null)
  await admission.close()
})

test('failed final unload quarantines a finished inference instead of freeing CUDA', async () => {
  const admission = new GpuAdmissionCoordinator()
  let calls = 0
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => 'http://127.0.0.1:11434',
    quiesce: async () => { if (++calls === 2) throw new Error('private socket') },
  })
  await assert.rejects(native.runOllama(new AbortController().signal, async () => 'answer'), /awaiting model cleanup/u)
  assert.equal(admission.getSnapshot().active.owner, 'ollama')
  await native.recover()
  assert.equal(admission.getSnapshot().active, null)
  await admission.close()
})

test('aborted queued native work never reaches preparation or the worker', async () => {
  const admission = new GpuAdmissionCoordinator()
  const occupying = await admission.acquire('gpt-sovits')
  const abort = new AbortController()
  let prepared = false
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => 'http://127.0.0.1:11434',
    quiesce: async () => { prepared = true },
  })
  const pending = native.acquire('song-cover', abort.signal)
  await new Promise(resolve => setImmediate(resolve))
  abort.abort()
  await assert.rejects(pending)
  assert.equal(prepared, false)
  occupying.release()
  await admission.close()
})

test('a fresh refusal cannot erase GPU uncertainty retained after failed preparation', async () => {
  const admission = new GpuAdmissionCoordinator()
  const policies = []
  let first = true
  let reachable = false
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => 'http://127.0.0.1:11434',
    quiesce: async (_origin, _signal, options) => {
      policies.push(options.allowInitialRefusal)
      if (first) {
        first = false
        throw new Error('GPU model observed before the daemon stopped responding')
      }
      if (!reachable && !options.allowInitialRefusal) throw new Error('connection refused')
    },
  })
  await assert.rejects(native.acquire('song-cover'), /could not be prepared/u)
  assert.deepEqual(policies, [true, false])
  assert.equal(admission.getSnapshot().active.owner, 'song-cover')
  await assert.rejects(native.recover(), /awaiting model cleanup/u)
  assert.equal(admission.getSnapshot().active.owner, 'song-cover')
  assert.equal(policies.at(-1), false)
  reachable = true
  await native.recover()
  assert.equal(admission.getSnapshot().active, null)
  await admission.close()
})

test('post-inference refusal keeps the external runner quarantined across retries', async () => {
  const admission = new GpuAdmissionCoordinator()
  const policies = []
  let reachable = true
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => 'http://127.0.0.1:11434',
    quiesce: async (_origin, _signal, options) => {
      policies.push(options.allowInitialRefusal)
      if (!reachable && !options.allowInitialRefusal) throw new Error('connection refused')
    },
  })
  await assert.rejects(native.runOllama(new AbortController().signal, async () => {
    reachable = false
    return 'answer from a now-unreachable daemon'
  }), /awaiting model cleanup/u)
  assert.deepEqual(policies, [true, false])
  await assert.rejects(native.recover(), /awaiting model cleanup/u)
  assert.equal(admission.getSnapshot().active.owner, 'ollama')
  reachable = true
  await native.recover()
  assert.equal(admission.getSnapshot().active, null)
  await admission.close()
})

test('Python and native preparations share knowledge of a prior Ollama grant', async () => {
  const admission = new GpuAdmissionCoordinator()
  const origin = 'http://127.0.0.1:11434'
  const policies = []
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => origin,
    quiesce: async (_origin, _signal, options) => { policies.push(options.allowInitialRefusal) },
  })
  const python = await admission.acquire('ollama')
  await native.prepare('ollama', origin, python.signal)
  python.release()
  const cover = await native.acquire('song-cover')
  assert.deepEqual(policies, [true, false])
  cover.release()
  const image = await native.acquire('image-generation')
  assert.deepEqual(policies, [true, false, true])
  image.release()
  await admission.close()
})

test('failed Python preparation remains strict even when no native quarantine entry exists', async () => {
  const admission = new GpuAdmissionCoordinator()
  const origin = 'http://127.0.0.1:11434'
  const policies = []
  let first = true
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => origin,
    quiesce: async (_origin, _signal, options) => {
      policies.push(options.allowInitialRefusal)
      if (first) { first = false; throw new Error('GPU observation became uncertain') }
    },
  })
  const python = await admission.acquire('image-generation')
  await assert.rejects(native.prepare('image-generation', origin, python.signal))
  await native.prepare('image-generation', origin, python.signal)
  assert.deepEqual(policies, [true, false])
  python.release()
  await admission.close()
})

test('explicit external process-exit cleanup always requires a positive live response', async () => {
  const admission = new GpuAdmissionCoordinator()
  const policies = []
  const native = new NativeGpuWorkloads(admission, {
    getOllamaOrigin: async () => 'http://127.0.0.1:11434',
    quiesce: async (_origin, _signal, options) => { policies.push(options.allowInitialRefusal) },
  })
  await native.quiesceExternal('http://127.0.0.1:11434')
  assert.deepEqual(policies, [false])
  await admission.close()
})
