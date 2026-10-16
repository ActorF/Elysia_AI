/** Verify real inherited fd5 duplex I/O, desktop startup, and absent-asset readiness. */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const PROJECT = path.resolve(fileURLToPath(new URL('../../', import.meta.url)))
const VENV_PYTHON = path.join(PROJECT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const PYTHON = process.env.ELYSIA_TEST_PYTHON ?? process.env.ELYSIA_PYTHON
  ?? (existsSync(VENV_PYTHON) ? VENV_PYTHON : process.platform === 'win32' ? 'python' : 'python3')

async function runPython(arguments_, { stdin = '', broker = true, flag = true, grantMode = 'whole' } = {}) {
  const dataRoot = await mkdtemp(path.join(os.tmpdir(), 'elysia-fd5-integration-'))
  const child = spawn(PYTHON, arguments_, {
    cwd: PROJECT,
    windowsHide: true,
    shell: false,
    env: { ...process.env, ELYSIA_GPU_BROKER: flag ? '1' : '0',
      ELYSIA_DATA_ROOT: dataRoot, ELYSIA_DESKTOP_SESSION_TOKEN: 'a'.repeat(64) },
    stdio: ['pipe', 'pipe', 'pipe', 'pipe', 'pipe', broker ? 'pipe' : 'ignore'],
  })
  const frames = []
  const output = []
  const errors = []
  let brokerBuffer = ''
  child.stdout.on('data', bytes => output.push(bytes))
  child.stderr.on('data', bytes => errors.push(bytes))
  child.stdio[3].resume()
  child.stdio[4].resume()
  if (broker) {
    child.stdio[5].on('data', bytes => {
      brokerBuffer += bytes.toString('ascii')
      for (;;) {
        const end = brokerBuffer.indexOf('\n')
        if (end < 0) break
        const frame = JSON.parse(brokerBuffer.slice(0, end))
        brokerBuffer = brokerBuffer.slice(end + 1)
        frames.push(frame)
        if (frame.action === 'acquire') {
          const reply = `${JSON.stringify({ id: frame.id, status: 'granted' })}\n`
          if (grantMode === 'partial') child.stdio[5].write(reply.slice(0, 17))
          else if (grantMode === 'oversized') child.stdio[5].write('x'.repeat(513))
          else if (grantMode === 'fragmented') {
            child.stdio[5].write(reply.slice(0, 17))
            setTimeout(() => child.stdio[5].write(reply.slice(17)), 25)
          } else child.stdio[5].write(reply)
        }
      }
    })
    child.stdio[5].on('error', () => {})
  }
  child.stdin.on('error', () => {})
  const timeout = setTimeout(() => child.kill('SIGKILL'), 8_000)
  try {
    const closed = new Promise((resolve, reject) => {
      child.once('error', reject)
      child.once('close', (code, signal) => resolve({ code, signal }))
    })
    child.stdin.end(stdin)
    const exit = await closed
    assert.equal(exit.signal, null, `The owned Python process must close without forced termination; frames=${JSON.stringify(frames)}, stdout=${Buffer.concat(output).toString('utf8')}, stderr=${Buffer.concat(errors).toString('utf8')}`)
    return { code: exit.code, stdout: Buffer.concat(output).toString('utf8'),
      stderr: Buffer.concat(errors).toString('utf8'), frames }
  } finally {
    clearTimeout(timeout)
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
    await rm(dataRoot, { recursive: true, force: true })
  }
}

test('the real fixed fd5 grants and releases while its idle reader can close cleanly', async () => {
  const source = [
    'import faulthandler',
    'faulthandler.dump_traceback_later(3)',
    'from gpu_admission import GpuAdmissionClient, configure_gpu_client, gpu_lease, using_desktop_gpu',
    'client = GpuAdmissionClient.from_inherited_fd5()',
    'configure_gpu_client(client)',
    'assert using_desktop_gpu()',
    'with gpu_lease("image-generation"):',
    '    print("granted", flush=True)',
    'client.close()',
    'assert not client._reader.is_alive()',
    'assert client._stream.closed and client._write_stream.closed',
    'print("closed", flush=True)',
  ].join('\n')
  const result = await runPython(['-u', '-c', source])
  assert.equal(result.code, 0, result.stderr)
  assert.deepEqual(result.stdout.trim().split(/\r?\n/u), ['granted', 'closed'])
  assert.deepEqual(result.frames.map(frame => frame.action), ['acquire', 'release'])
  assert.equal(result.frames[0].owner, 'image-generation')
  assert.equal(result.frames[0].id, result.frames[1].id)
})

test('real fd5 assembles fragmented grants without awaiting missing bytes inside ReadFile', async () => {
  const source = [
    'from gpu_admission import GpuAdmissionClient',
    'client = GpuAdmissionClient.from_inherited_fd5()',
    'lease = client.acquire("ollama", timeout_seconds=2)',
    'lease.release()',
    'client.close()',
    'assert not client._reader.is_alive()',
    'assert client._stream.closed and client._write_stream.closed',
  ].join('\n')
  const result = await runPython(['-u', '-c', source], { grantMode: 'fragmented' })
  assert.equal(result.code, 0, result.stderr)
  assert.deepEqual(result.frames.map(frame => frame.action), ['acquire', 'release'])
})

test('an unfinished real fd5 grant can time out and close without a fake native release', async () => {
  const source = [
    'from gpu_admission import GpuAdmissionClient, GpuAdmissionError',
    'client = GpuAdmissionClient.from_inherited_fd5()',
    'try:',
    '    client.acquire("image-generation", timeout_seconds=0.05)',
    'except GpuAdmissionError:',
    '    pass',
    'else:',
    '    raise AssertionError("A truncated grant cannot start inference")',
    'client.close()',
    'assert not client._reader.is_alive()',
    'assert client._stream.closed and client._write_stream.closed',
  ].join('\n')
  const result = await runPython(['-u', '-c', source], { grantMode: 'partial' })
  assert.equal(result.code, 0, result.stderr)
  assert.deepEqual(result.frames.map(frame => frame.action), ['acquire', 'cancel'])
})

test('an oversized native fd5 frame closes admission while the waiter remains ungranted', async () => {
  const source = [
    'from gpu_admission import GpuAdmissionClient, GpuAdmissionError',
    'client = GpuAdmissionClient.from_inherited_fd5()',
    'try:',
    '    client.acquire("gpt-sovits", timeout_seconds=2)',
    'except GpuAdmissionError:',
    '    pass',
    'else:',
    '    raise AssertionError("An invalid grant cannot start inference")',
    'client.close()',
    'assert client.closed and not client._reader.is_alive()',
    'assert client._stream.closed and client._write_stream.closed',
  ].join('\n')
  const result = await runPython(['-u', '-c', source], { grantMode: 'oversized' })
  assert.equal(result.code, 0, result.stderr)
  assert.deepEqual(result.frames.map(frame => frame.action), ['acquire'])
})

test('real desktop startup handshakes and shuts down with fd5 before loading any model', async () => {
  const request = (id, method, params) => JSON.stringify({ protocol: { name: 'elysia.desktop', version: 1 },
    type: 'request', id, method, params })
  const stdin = `${request('hello', 'handshake', { client: { name: 'elysia-electron', version: '0.1.0' },
    sessionToken: 'a'.repeat(64) })}\n${request('stop', 'shutdown', {})}\n`
  const result = await runPython(['-u', path.join(PROJECT, 'desktop_backend.py')], { stdin })
  assert.equal(result.code, 0, result.stderr)
  const output = result.stdout.trim().split(/\r?\n/u).map(line => JSON.parse(line))
  assert.equal(output.find(frame => frame.id === 'hello').ok, true)
  assert.deepEqual(output.find(frame => frame.id === 'stop').result, { stopped: true })
  assert.deepEqual(result.frames, [])
})

test('a promised but absent fd5 fails closed before the real backend exposes requests', async () => {
  const result = await runPython(['-u', path.join(PROJECT, 'desktop_backend.py')], { broker: false })
  assert.equal(result.stdout, '')
  assert.deepEqual(result.frames, [])
  assert.match(result.stderr, /Desktop GPU admission channel is unavailable/u)
})

test('real broker mode reports missing speech assets without requesting idle GPU residency', async () => {
  const source = [
    'from pathlib import Path',
    'from io import BytesIO',
    'import os',
    'from desktop_speech import DesktopSpeechConfig, DesktopSpeechCoordinator',
    'from desktop_protocol import AudioChannelWriter',
    'from gpu_admission import GpuAdmissionClient, configure_gpu_client',
    'client = GpuAdmissionClient.from_inherited_fd5()',
    'configure_gpu_client(client)',
    'root = Path(os.environ["ELYSIA_DATA_ROOT"])',
    'config = DesktopSpeechConfig(root/"runtime", root/"worker.py", root/"catalog.json", root/"assets",',
    '    "default", "neutral", 100, True, 42, 1.0)',
    'speech = DesktopSpeechCoordinator(config, AudioChannelWriter(BytesIO()), lambda *_: None)',
    'speech.start()',
    'assert speech.wait_until_settled(2.0)',
    'assert speech.get_status().state == "unavailable"',
    'speech.shutdown()',
    'client.close()',
    'print("unavailable-without-gpu", flush=True)',
  ].join('\n')
  const result = await runPython(['-u', '-c', source])
  assert.equal(result.code, 0, result.stderr)
  assert.equal(result.stdout.trim(), 'unavailable-without-gpu')
  assert.deepEqual(result.frames, [])
})
