/** Verify conversational Windows tools enforce permissions, fixed launch targets, endpoint identity, and physical cancellation. */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { PassThrough, Writable } from 'node:stream'
import { pathToFileURL } from 'node:url'
import test, { after } from 'node:test'

const electron = test.mock.module('electron', { exports: { dialog: { showMessageBox: () => { throw new Error('Use the injected test dialog.') } } } })
const moduleUrl = process.env.ELYSIA_WORK_AGENT_NATIVE_TEST_MODULE
  ? pathToFileURL(process.env.ELYSIA_WORK_AGENT_NATIVE_TEST_MODULE)
  : new URL('../dist-electron/work-agent-native.js', import.meta.url)
const { createWorkAgentNativeTools } = await import(moduleUrl.href)
after(() => electron.restore())

const AUDIO = { endpointId: '{test-default-endpoint}', level: 45, muted: false }

function deferred() {
  let resolve
  const promise = new Promise((accept) => { resolve = accept })
  return { promise, resolve }
}

function childProcess() {
  const child = new EventEmitter()
  child.stdout = new PassThrough()
  child.writes = []
  child.stdin = new Writable({ write: (chunk, _encoding, callback) => { child.writes.push(chunk.toString('utf8')); callback() } })
  child.kill = () => { child.killed = true; return true }
  child.unref = () => { child.unreferenced = true }
  return child
}

function harness(overrides = {}) {
  const calls = []
  const children = []
  let current = true
  let nextCallId = 0
  const dependencies = {
    platform: 'win32', systemRoot: 'C:\\Windows',
    inspectPath: async (target) => {
      calls.push(['inspect', target])
      const file = target.endsWith('.exe')
      return { file, directory: !file, link: false, realPath: target }
    },
    confirm: async (_window, options) => { calls.push(['confirm', options]); return { response: 1 } },
    spawnProcess: (executable, args, options) => {
      const child = childProcess()
      children.push(child)
      calls.push(['spawn', executable, args, options])
      if (args.length === 0) queueMicrotask(() => child.emit('spawn'))
      return child
    },
    ...overrides,
  }
  const execute = createWorkAgentNativeTools({ isCurrent: () => current, getWindow: () => ({ trusted: true }) }, dependencies)
  return {
    calls, children, execute,
    invalidate: () => { current = false },
    request: (tool, args = {}, permission = 'auto') => ({
      requestId: 'turn_test', chatId: 'chat_work', callId: `call_${++nextCallId}`, tool, arguments: args,
      options: { thinking: 'off', permission },
    }),
  }
}

async function nextChild(h, index = 0) {
  for (let attempt = 0; attempt < 30 && h.children.length <= index; attempt += 1) await Promise.resolve()
  assert.ok(h.children[index], 'the expected native helper was created')
  return h.children[index]
}

function completeAudio(child, state = AUDIO) {
  child.stdout.write('READY\n')
  child.stdout.write(`${JSON.stringify(state)}\n`)
  child.emit('close', 0)
}

test('none denies all tools and read denies side effects before disk, dialogs, or native processes', async () => {
  const h = harness()
  for (const [tool, args] of [['list_apps', {}], ['get_volume', {}], ['open_app', { application: 'notepad' }], ['set_volume', { level: 30 }]]) {
    assert.equal((await h.execute(h.request(tool, args, 'none'), new AbortController().signal)).status, 'denied')
  }
  for (const [tool, args] of [['open_app', { application: 'notepad' }], ['set_volume', { level: 30 }]]) {
    assert.equal((await h.execute(h.request(tool, args, 'read'), new AbortController().signal)).error, 'permission_denied')
  }
  assert.deepEqual(h.calls, [])
})

test('tool inputs reject command paths, arbitrary arguments, unknown tools and noninteger volume', async () => {
  const h = harness()
  for (const [tool, args] of [
    ['open_app', { application: 'C:\\evil.exe' }], ['open_app', { application: '__proto__' }],
    ['open_app', { application: 'notepad', arguments: ['private.txt'] }], ['list_apps', { path: '.' }],
    ['get_volume', { level: 30 }], ['set_volume', { level: -1 }], ['set_volume', { level: 101 }],
    ['set_volume', { level: 30.5 }], ['set_volume', { level: '30' }], ['run_shell', {}],
  ]) {
    assert.equal((await h.execute(h.request(tool, args), new AbortController().signal)).error, 'invalid_request')
  }
  assert.deepEqual(h.calls, [])
})

test('listing returns only known installed system IDs and skips absent or redirected binaries', async () => {
  const h = harness({ inspectPath: async (target) => {
    if (target.endsWith('calc.exe')) throw new Error('Not installed')
    return { file: target.endsWith('.exe'), directory: !target.endsWith('.exe'), link: target.endsWith('mspaint.exe'), realPath: target }
  } })
  const output = await h.execute(h.request('list_apps', {}, 'read'), new AbortController().signal)
  assert.equal(output.status, 'succeeded')
  assert.match(output.text, /notepad: Notepad/u)
  assert.match(output.text, /explorer: File Explorer/u)
  assert.doesNotMatch(output.text, /calculator|paint:|C:\\/u)
  assert.deepEqual(h.calls, [])
})

test('ask confirms the exact fixed application and launches with no shell, user arguments, or workspace cwd', async () => {
  const h = harness()
  const output = await h.execute(h.request('open_app', { application: 'notepad' }, 'ask'), new AbortController().signal)
  assert.equal(output.status, 'succeeded')
  const confirmation = h.calls.find(([type]) => type === 'confirm')[1]
  assert.deepEqual(confirmation.buttons, ['Cancel', 'Allow'])
  assert.equal(confirmation.defaultId, 0)
  assert.match(confirmation.detail, /C:\\Windows\\System32\\notepad\.exe/u)
  const spawn = h.calls.find(([type]) => type === 'spawn')
  assert.equal(spawn[1], 'C:\\Windows\\System32\\notepad.exe')
  assert.deepEqual(spawn[2], [])
  assert.equal(spawn[3].shell, false)
  assert.equal(spawn[3].cwd, 'C:\\Windows')
  assert.equal(h.children[0].unreferenced, true)
  assert.match(output.text, /launcher/u)
})

test('cancelled or stale native approval cannot launch an application and the reviewed path is revalidated', async () => {
  const approval = deferred()
  const h = harness({ confirm: () => approval.promise })
  const pending = h.execute(h.request('open_app', { application: 'calculator' }, 'ask'), new AbortController().signal)
  for (let index = 0; index < 20; index += 1) await Promise.resolve()
  h.invalidate()
  approval.resolve({ response: 1 })
  assert.equal((await pending).status, 'cancelled')
  assert.equal(h.children.length, 0)
  let reviewed = false
  const replaced = harness({
    confirm: async () => { reviewed = true; return { response: 1 } },
    inspectPath: async (target) => ({ file: target.endsWith('.exe'), directory: !target.endsWith('.exe'), link: reviewed, realPath: target }),
  })
  assert.equal((await replaced.execute(replaced.request('open_app', { application: 'paint' }, 'ask'), new AbortController().signal)).error, 'unavailable')
  assert.equal(replaced.children.length, 0)
  const denied = harness({ confirm: async () => ({ response: 0 }) })
  assert.equal((await denied.execute(denied.request('open_app', { application: 'explorer' }, 'ask'), new AbortController().signal)).status, 'denied')
  assert.equal(denied.children.length, 0)
})

test('auto launches directly while unsupported platforms fail without invoking a native process', async () => {
  const h = harness()
  assert.equal((await h.execute(h.request('open_app', { application: 'explorer' }), new AbortController().signal)).status, 'succeeded')
  assert.equal(h.calls.some(([type]) => type === 'confirm'), false)
  const unsupported = harness({ platform: 'linux' })
  assert.equal((await unsupported.execute(unsupported.request('list_apps'), new AbortController().signal)).error, 'unavailable')
  assert.deepEqual(unsupported.calls, [])
})

test('audio read uses a hidden fixed helper, waits for READY, sends only JSON, and reports actual mute/level', async () => {
  const h = harness()
  const pending = h.execute(h.request('get_volume', {}, 'read'), new AbortController().signal)
  const child = await nextChild(h)
  assert.deepEqual(child.writes, [])
  const [, executable, args, options] = h.calls.find(([type]) => type === 'spawn')
  assert.equal(executable, 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe')
  assert.deepEqual(args.slice(0, 4), ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand'])
  assert.match(Buffer.from(args[4], 'base64').toString('utf16le'), /SetMasterVolumeLevelScalar/u)
  assert.equal(options.windowsHide, true)
  assert.equal(options.shell, false)
  assert.deepEqual(Object.keys(options.env).sort(), ['PATH', 'SystemRoot', 'TEMP', 'TMP', 'WINDIR'])
  completeAudio(child, { ...AUDIO, muted: true })
  assert.deepEqual(child.writes, ['{}\n'])
  const output = await pending
  assert.equal(output.status, 'succeeded')
  assert.match(output.text, /45%.*Muted\./u)
})

test('set volume confirms exact percent, pins the reviewed device and returns native readback rather than requested value', async () => {
  const h = harness()
  const pending = h.execute(h.request('set_volume', { level: 30 }, 'ask'), new AbortController().signal)
  completeAudio(await nextChild(h))
  const child = await nextChild(h, 1)
  const confirmation = h.calls.find(([type]) => type === 'confirm')[1]
  assert.match(confirmation.detail, /45% → 30%/u)
  assert.match(confirmation.detail, /mute setting will stay unchanged/u)
  completeAudio(child, { ...AUDIO, level: 31 })
  assert.deepEqual(JSON.parse(child.writes[0]), { level: 30, endpointId: AUDIO.endpointId })
  const output = await pending
  assert.equal(output.status, 'succeeded')
  assert.match(output.text, /31%/u)
})

test('volume denial or default output change never claims the requested change succeeded', async () => {
  const denied = harness({ confirm: async () => ({ response: 0 }) })
  const first = denied.execute(denied.request('set_volume', { level: 0 }, 'ask'), new AbortController().signal)
  completeAudio(await nextChild(denied))
  assert.equal((await first).status, 'denied')
  assert.equal(denied.children.length, 1)
  const changed = harness()
  const second = changed.execute(changed.request('set_volume', { level: 100 }), new AbortController().signal)
  completeAudio(await nextChild(changed))
  const helper = await nextChild(changed, 1)
  completeAudio(helper, { error: 'endpoint_changed' })
  const output = await second
  assert.equal(output.error, 'unavailable')
  assert.match(output.text, /Volume was not adjusted/u)
})

test('aborting a dispatched volume helper keeps the executor occupied until physical close and reports uncertainty', async () => {
  const h = harness()
  const abort = new AbortController()
  let settled = false
  const pending = h.execute(h.request('set_volume', { level: 20 }), abort.signal).then((output) => { settled = true; return output })
  completeAudio(await nextChild(h))
  const helper = await nextChild(h, 1)
  helper.stdout.write('READY\n')
  abort.abort()
  await Promise.resolve()
  assert.equal(helper.killed, true)
  assert.equal(settled, false)
  assert.equal((await h.execute(h.request('list_apps'), new AbortController().signal)).status, 'denied')
  helper.emit('close', null)
  const output = await pending
  assert.equal(output.status, 'cancelled')
  assert.match(output.text, /may already have taken effect/u)
  assert.equal((await h.execute(h.request('list_apps'), new AbortController().signal)).status, 'succeeded')
})

test('stale ownership at READY prevents command delivery and waits for the retired helper to close', async () => {
  const h = harness()
  const pending = h.execute(h.request('get_volume'), new AbortController().signal)
  const child = await nextChild(h)
  h.invalidate()
  child.stdout.write('READY\n')
  assert.deepEqual(child.writes, [])
  assert.equal(child.killed, true)
  child.emit('close', null)
  assert.equal((await pending).status, 'cancelled')
})

test('malformed helper output is bounded and cannot turn an incomplete native operation into success', async () => {
  for (const invalid of ['x'.repeat(8193), 'READY\n{"level":200,"muted":false,"endpointId":"x"}\n', 'READY\nREADY\n']) {
    const h = harness()
    const pending = h.execute(h.request('get_volume'), new AbortController().signal)
    const child = await nextChild(h)
    child.stdout.write(invalid)
    assert.equal(child.killed, true)
    child.emit('close', 0)
    assert.equal((await pending).status, 'failed')
  }
})

test('native startup failure and helper timeout settle only after the process closes', async (t) => {
  const h = harness()
  const pending = h.execute(h.request('get_volume'), new AbortController().signal)
  const child = await nextChild(h)
  child.emit('error', new Error('private native diagnostics'))
  assert.equal((await h.execute(h.request('list_apps'), new AbortController().signal)).status, 'denied')
  child.emit('close', -1)
  const failed = await pending
  assert.equal(failed.error, 'unavailable')
  assert.doesNotMatch(failed.text, /private native diagnostics/u)

  t.mock.timers.enable({ apis: ['setTimeout'] })
  const timeoutHarness = harness()
  let settled = false
  const timed = timeoutHarness.execute(timeoutHarness.request('get_volume'), new AbortController().signal)
    .then((value) => { settled = true; return value })
  const helper = await nextChild(timeoutHarness)
  t.mock.timers.tick(20_000)
  assert.equal(helper.killed, true)
  assert.equal(settled, false)
  assert.equal((await timeoutHarness.execute(timeoutHarness.request('list_apps'), new AbortController().signal)).status, 'denied')
  helper.emit('close', null)
  assert.equal((await timed).error, 'timeout')
})

test('volume approval rechecks cancellation before starting the writing helper', async () => {
  const approval = deferred()
  const h = harness({ confirm: () => approval.promise })
  const abort = new AbortController()
  const pending = h.execute(h.request('set_volume', { level: 30 }, 'ask'), abort.signal)
  completeAudio(await nextChild(h))
  await Promise.resolve()
  abort.abort()
  approval.resolve({ response: 1 })
  assert.equal((await pending).status, 'cancelled')
  assert.equal(h.children.length, 1)
})
