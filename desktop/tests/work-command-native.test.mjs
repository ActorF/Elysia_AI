/** Verify approved CMD dispatch, private helper inputs, bounded receipts, and cancellation leases. */

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { existsSync } from 'node:fs'
import { PassThrough } from 'node:stream'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { createWorkCommandNativeRunner } from '../dist-electron/work-command-native.js'

const cwd = 'C:\\Users\\test'
const command = 'echo "test"'

function request(arguments_ = { command }, permission = 'full') {
  return { requestId: 'request_test', chatId: 'chat_test', callId: 'work_call_' + 'a'.repeat(32),
    tool: 'run_command', arguments: arguments_, options: { thinking: 'off', permission } }
}

function receipt(overrides = {}) {
  return { status: 'succeeded', text: 'CMD exited with code 0.', error: null,
    command: { command, cwd, stdout: 'test\r\n', stderr: '', exitCode: 0, termination: 'exit', truncated: false, ...overrides } }
}

function harness(overrides = {}) {
  const child = new EventEmitter()
  child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough()
  child.kill = () => { child.kills = (child.kills ?? 0) + 1; return true }
  const calls = []
  let current = true
  const execute = createWorkCommandNativeRunner({
    isCurrent: () => current, projectRoot: 'D:\\Elysia', pythonExecutable: 'D:\\Elysia\\python.exe', defaultCwd: cwd,
  }, {
    platform: 'win32', environment: { SystemRoot: 'C:\\Windows', PATH: '.;relative;C:\\Tools;C:\\Tools',
      COMPUTERNAME: 'test-machine', USERNAME: 'test-user', USERPROFILE: cwd,
      PYTHONPATH: 'malicious-import', ELYSIA_DESKTOP_SESSION_TOKEN: 'private-token', API_KEY: 'secret' },
    cancellationGraceMs: 20,
    spawnProcess: (...args) => { calls.push(args); return child }, ...overrides,
  })
  return { execute, child, calls, invalidate: () => { current = false } }
}

function complete(child, result = receipt()) {
  child.stdout.write(JSON.stringify(result) + '\n')
  child.emit('close', 0)
}

test('only ask and full can dispatch a command, preserving the old auto ceiling', async () => {
  const h = harness()
  for (const permission of ['none', 'read', 'auto']) {
    assert.equal((await h.execute(request({ command }, permission), new AbortController().signal)).error, 'permission_denied')
  }
  assert.equal(h.calls.length, 0)
})

test('invalid fields and stale owners cannot launch the helper', async () => {
  const h = harness()
  for (const args of [{ command: '' }, { command: 'x\0' }, { command: '\uD800' },
    { command: 'echo alpha\necho beta' }, { command: 'echo alpha\r\necho beta' }, { command, cwd: null },
    { command, cwd: 'C:relative' }, { command, cwd: '\\\\localhost\\D$\\Elysia_AI' },
    { command, timeoutMs: null }, { command, timeoutMs: 999 }, { command, extra: true }]) {
    assert.equal((await h.execute(request(args), new AbortController().signal)).error, 'invalid_request')
  }
  h.invalidate()
  assert.equal((await h.execute(request(), new AbortController().signal)).error, 'cancelled')
  assert.equal(h.calls.length, 0)
})

test('the fixed helper receives NDJSON without inherited credentials or shell interpolation', async () => {
  const h = harness()
  const pending = h.execute(request(), new AbortController().signal)
  assert.equal(h.calls.length, 1)
  const [executable, args, options] = h.calls[0]
  assert.equal(executable, 'D:\\Elysia\\python.exe')
  assert.deepEqual(args, ['-B', '-u', '-s', '-m', 'work_tools.command_runner'])
  assert.equal(options.shell, false)
  assert.equal(options.windowsHide, true)
  assert.equal(options.cwd, 'D:\\Elysia')
  assert.equal(options.env.PATH, 'C:\\Windows\\System32;C:\\Tools')
  assert.equal(options.env.NoDefaultCurrentDirectoryInExePath, '1')
  assert.equal(options.env.COMPUTERNAME, 'test-machine')
  for (const key of ['PYTHONPATH', 'API_KEY', 'ELYSIA_DESKTOP_SESSION_TOKEN']) assert.equal(options.env[key], undefined)
  assert.deepEqual(JSON.parse(h.child.stdin.read().toString()), { command, cwd, timeoutMs: 30000 })
  complete(h.child)
  assert.deepEqual(await pending, receipt())
})

test('a parsed receipt does not release ownership before physical helper close', async () => {
  const h = harness()
  let resolved = false
  const pending = h.execute(request(), new AbortController().signal).then(result => { resolved = true; return result })
  h.child.stdout.write(JSON.stringify(receipt()) + '\n')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(resolved, false)
  assert.equal((await h.execute(request(), new AbortController().signal)).error, 'permission_denied')
  h.child.emit('close', 0)
  assert.equal((await pending).status, 'succeeded')
})

test('abort closes private input, escalates to kill, and still waits for close', async () => {
  const h = harness()
  const abort = new AbortController()
  let resolved = false
  const pending = h.execute(request(), abort.signal).then(result => { resolved = true; return result })
  abort.abort()
  assert.equal(h.child.stdin.writableEnded, true)
  await new Promise(resolve => setTimeout(resolve, 35))
  assert.equal(h.child.kills, 1)
  assert.equal(resolved, false)
  h.child.emit('close', 1)
  assert.equal((await pending).command.termination, 'cancelled')
})

test('navigation revokes a running command even without an explicit abort signal', async () => {
  const h = harness()
  const pending = h.execute(request(), new AbortController().signal)
  h.invalidate()
  await new Promise(resolve => setTimeout(resolve, 130))
  assert.equal(h.child.stdin.writableEnded, true)
  h.child.emit('close', 1)
  assert.equal((await pending).status, 'cancelled')
})

test('wrong command identity, malformed JSON, and oversized output fail closed', async () => {
  for (const output of [JSON.stringify(receipt({ command: 'other' })) + '\n', '{}\n', 'x'.repeat(262145)]) {
    const h = harness()
    const pending = h.execute(request(), new AbortController().signal)
    h.child.stdout.write(output)
    h.child.emit('close', 0)
    assert.equal((await pending).status, 'failed')
  }
})

test('nonzero helper exit cannot promote a reported success', async () => {
  const h = harness()
  const pending = h.execute(request(), new AbortController().signal)
  h.child.stdout.write(JSON.stringify(receipt()) + '\n')
  h.child.emit('close', 1)
  assert.equal((await pending).status, 'failed')
})

test('real Windows helper returns Unicode and machine identity through the native adapter', { skip: process.platform !== 'win32' }, async () => {
  const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
  // CI installs Python on PATH without creating a checkout virtual environment.
  // Resolve that executable before invoking the production host, whose trusted
  // interpreter must remain absolute even when the test uses a PATH override.
  const venvPython = path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  const pythonCommand = process.env.ELYSIA_TEST_PYTHON ?? (existsSync(venvPython) ? venvPython : 'python')
  const interpreter = spawnSync(pythonCommand, ['-c', 'import sys; print(sys.executable)'],
    { encoding: 'utf8', timeout: 5_000, maxBuffer: 4096, windowsHide: true, shell: false })
  assert.equal(interpreter.status, 0, 'The native CMD contract requires an available Python interpreter.')
  const pythonExecutable = interpreter.stdout.trim()
  assert.ok(path.isAbsolute(pythonExecutable), 'Python must report an absolute executable path.')
  const execute = createWorkCommandNativeRunner({ isCurrent: () => true, projectRoot,
    pythonExecutable, defaultCwd: projectRoot })
  const result = await execute(request({ command: 'echo 中文回执 & echo %COMPUTERNAME% & echo %USERNAME%' }), new AbortController().signal)
  assert.equal(result.status, 'succeeded')
  assert.match(result.command.stdout, /中文回执/u)
  assert.ok(result.command.stdout.includes(process.env.COMPUTERNAME))
  assert.ok(result.command.stdout.includes(process.env.USERNAME))
})
