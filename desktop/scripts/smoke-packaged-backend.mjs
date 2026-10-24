/**
 * @fileoverview Exercise the shipped Python bridge with no developer Python or user data.
 * A loopback fixture replaces Ollama only; imports, protocol, private pipes,
 * settings recovery, persistence, and process shutdown use production code.
 */

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { BackendProcess } from '../dist-electron/backend-process.js'
import { GpuAdmissionCoordinator } from '../dist-electron/gpu-admission.js'

const desktopRoot = fileURLToPath(new URL('..', import.meta.url))
const backendRoot = path.resolve(process.argv[2] ?? path.join(desktopRoot, 'out/win-unpacked/resources/backend'))
const temporary = await mkdtemp(path.join(os.tmpdir(), 'elysia-package-离线-'))
const originalEnvironment = { ...process.env }
const events = []
// Namespaced registry models exercise every initialization consumer; validating
// only the Ollama client previously hid a conflicting document-grounding rule.
const fixtureModel = 'smoke-org/local-model:latest'
const server = createServer((request, response) => {
  response.setHeader('content-type', 'application/json')
  if (request.url === '/api/chat') {
    let body = ''
    request.on('data', chunk => { body += chunk.toString() })
    request.on('end', () => {
      const parameters = JSON.parse(body)
      const frame = { model: fixtureModel, created_at: '2026-01-01T00:00:00Z',
        message: { role: 'assistant', content: '你好，打包测试成功。' }, done: true,
        done_reason: 'stop', total_duration: 1, load_duration: 1, prompt_eval_count: 1,
        prompt_eval_duration: 1, eval_count: 1, eval_duration: 1 }
      if (parameters.stream) {
        response.setHeader('content-type', 'application/x-ndjson')
        response.write(JSON.stringify({ ...frame, done: false, message: { role: 'assistant', content: '你好，' } }) + '\n')
        response.end(JSON.stringify({ ...frame, message: { role: 'assistant', content: '打包测试成功。' } }) + '\n')
      } else response.end(JSON.stringify(frame))
    })
    return
  }
  request.resume()
  if (request.url === '/api/tags') {
    response.end(JSON.stringify({ models: [{ name: fixtureModel, model: fixtureModel,
      modified_at: '2026-01-01T00:00:00Z', size: 1, digest: 'a'.repeat(64),
      details: { format: 'gguf', family: 'qwen3', parameter_size: '1B', quantization_level: 'Q4_0' } }] }))
  } else if (request.url === '/api/show') {
    response.end(JSON.stringify({ capabilities: ['completion'], model_info: { 'general.architecture': 'qwen3' } }))
  } else {
    response.statusCode = 404
    response.end(JSON.stringify({ error: 'No inference is required for package startup.' }))
  }
})
let backend

async function waitForState(expected) {
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    const state = backend.getSnapshot()
    if (state.status === expected) return state
    if (state.status === 'error' && expected !== 'error') {
      throw new Error(`Packaged backend failed before ${expected}: ${JSON.stringify(state)}`)
    }
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  throw new Error(`Packaged backend did not reach ${expected}: ${JSON.stringify(backend.getSnapshot())}`)
}

try {
  // Even a polluted developer shell must not affect the installed interpreter.
  for (const key of Object.keys(process.env)) {
    if (/^(ELYSIA_|PYTHON|OLLAMA_|MODEL_NAME|TRANSCRIPTION_)/u.test(key)) delete process.env[key]
  }
  Object.assign(process.env, {
    PATH: path.join(process.env.SystemRoot ?? 'C:/Windows', 'System32'),
    PYTHONHOME: path.join(temporary, 'missing-python'),
    PYTHONPATH: path.join(temporary, 'missing-packages'),
    ELYSIA_PYTHON: path.join(temporary, 'missing-python.exe'),
    OLLAMA_HOST: 'http://127.0.0.1:1',
    MODEL_NAME: 'smoke-local:latest',
    ELYSIA_DATA_ROOT: path.join(temporary, 'data'),
  })
  const imports = execFileSync(path.join(backendRoot, 'python/python.exe'), ['-B', '-c',
    [
      'import sys,ctypes,opencc,numpy,pypdf,langgraph,langchain_ollama,faster_whisper,onnxruntime',
      'from pathlib import Path',
      'assert sys.flags.isolated',
      'assert opencc.OpenCC("t2s").convert("繁體") == "繁体"',
      'kernel = ctypes.WinDLL("kernel32", use_last_error=True)',
      'kernel.GetModuleHandleW.restype = ctypes.c_void_p',
      'kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]',
      'kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]',
      'for name in ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"):',
      '    handle = kernel.GetModuleHandleW(name)',
      '    assert handle, name',
      '    buffer = ctypes.create_unicode_buffer(32768)',
      '    assert kernel.GetModuleFileNameW(handle, buffer, len(buffer)), name',
      '    assert Path(buffer.value).resolve().parent == Path(sys.executable).resolve().parent, buffer.value',
      'print(sys.version.split()[0])',
    ].join('\n'),
  ], { cwd: temporary, env: process.env, encoding: 'utf8', windowsHide: true, timeout: 60_000 })
  assert.match(imports, /^3\.14\./u)
  backend = new BackendProcess(backendRoot, event => events.push(event), { async play() {}, cancel() {} },
    path.join(temporary, 'data'), () => false, () => false, { gpuAdmission: new GpuAdmissionCoordinator() })
  backend.start()
  const unavailable = await waitForState('error')
  assert.equal(unavailable.initializationErrorCode, 'backend.ollama_unavailable')
  assert.ok(unavailable.capabilities.length > 0, `Model absence must preserve authenticated settings recovery: ${JSON.stringify(unavailable)}`)
  const settings = await backend.getSettings()
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${server.address().port}`
  await backend.updateSettings({ expectedRevision: settings.revision,
    settings: { ...settings.settings, ollamaHost: origin, modelName: 'smoke-local:latest' } })
  await backend.stop()
  backend.start()
  const missing = await waitForState('error')
  assert.equal(missing.initializationErrorCode, 'backend.model_missing')
  const missingSettings = await backend.getSettings()
  await backend.updateSettings({ expectedRevision: missingSettings.revision,
    settings: { ...missingSettings.settings, modelName: fixtureModel } })
  await backend.stop()
  backend.start()
  const ready = await waitForState('ready')
  assert.equal(ready.modelName, fixtureModel)
  assert.ok(ready.chatId)
  const chats = await backend.listChats(false)
  assert.ok(chats)
  const generation = backend.beginChat({ chatId: ready.chatId, message: '离线安装测试', attachmentIds: [], speakReply: false })
  const deadline = Date.now() + 30_000
  let completed
  while (Date.now() < deadline) {
    const failed = events.find(event => event.type === 'chat-error' && event.requestId === generation.requestId)
    if (failed) throw new Error(failed.message)
    completed = events.find(event => event.type === 'chat-complete' && event.requestId === generation.requestId)
    if (completed) break
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  assert.equal(completed?.reply, '你好，打包测试成功。')
  assert.ok(events.some(event => event.type === 'chat-chunk' && event.requestId === generation.requestId))
  const reopened = await backend.openChat(ready.chatId)
  assert.equal(reopened.activeChat.messages.at(-1).content, completed.reply)
  const voice = await backend.getVoiceSettings()
  assert.ok(voice.transcriptionStatus)
  console.log(`Packaged backend passed: CPython ${imports.trim()}, bundled VC runtime, missing-model recovery, ready state, streamed chat persistence, voice, fd3/fd4/fd5, isolated data.`)
} finally {
  await backend?.stop()
  await new Promise(resolve => server.close(resolve))
  for (const key of Object.keys(process.env)) if (!(key in originalEnvironment)) delete process.env[key]
  Object.assign(process.env, originalEnvironment)
  // This directory was created by this smoke and never points at real user data.
  await rm(temporary, { recursive: true, force: true })
}
