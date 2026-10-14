/** Execute Main's real model-origin resolver with inert dependencies to prevent fallback servers and hydration cancellation gaps. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { VisionAnalysisServiceError } from '../dist-electron/vision-analysis.js'
import { OllamaVisionAdapter } from '../dist-electron/vision-model.js'

const text = await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8')
const source = ts.createSourceFile('main.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const declaration = source.statements.find(item => ts.isFunctionDeclaration(item) && item.name?.text === 'configuredVisionModel')
assert.ok(declaration)
// Evaluate only the trusted resolver; importing Main would start the real app.
const compiled = ts.transpileModule(declaration.getText(source) + '\nmodule.exports = configuredVisionModel;', {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function fixture(read) {
  const origins = []
  const host = { module: { exports: {} }, VisionAnalysisServiceError,
    OllamaVisionAdapter: class {
      constructor(options) { new OllamaVisionAdapter(options); origins.push(options.baseUrl) }
    }, requireBackend: () => ({ getSettings: read }) }
  vm.runInNewContext(compiled, host, { timeout: 1000 })
  return { resolve: host.module.exports, origins }
}

test('Each explicit operation constructs a fresh inert adapter at the currently applied local origin', async () => {
  let origin = 'http://127.0.0.1:12444'
  const f = fixture(async () => ({ activeSettings: { ollamaHost: origin } })), abort = new AbortController()
  const first = await f.resolve(abort.signal)
  origin = 'http://localhost:13333'
  const second = await f.resolve(abort.signal)
  assert.notEqual(first, second); assert.deepEqual(f.origins, ['http://127.0.0.1:12444', 'http://localhost:13333'])
})

test('A remote, HTTPS, or malformed applied origin fails unavailable without choosing a fallback server', async () => {
  for (const origin of ['https://localhost:11434', 'http://example.com:11434', 'http://127.0.0.1:11434/private', 'invalid']) {
    const f = fixture(async () => ({ activeSettings: { ollamaHost: origin } }))
    await assert.rejects(f.resolve(new AbortController().signal), error => error.code === 'unavailable')
    assert.equal(f.origins.length, 0)
  }
})

test('Cancellation before or during settings hydration prevents adapter construction and any subsequent HTTP', async () => {
  const abort = new AbortController(); let read = 0, release
  const f = fixture(() => { read++; return new Promise(resolve => { release = resolve }) })
  abort.abort(); await assert.rejects(f.resolve(abort.signal), error => error.code === 'cancelled'); assert.equal(read, 0)
  const active = new AbortController(), pending = f.resolve(active.signal)
  active.abort(); release({ activeSettings: { ollamaHost: 'http://127.0.0.1:11434' } })
  await assert.rejects(pending, error => error.code === 'cancelled'); assert.equal(f.origins.length, 0)
})
