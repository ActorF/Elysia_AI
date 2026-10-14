/** Exercise production Main history handlers with real temporary private storage, immutable synthetic pixels, and delayed scope races. */

import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, mkdir, access, readFile, readdir, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import { deflateSync } from 'node:zlib'
import ts from 'typescript'
import { VisionHistoryStore, VisionHistoryError } from '../dist-electron/vision-history.js'
import { VisionImageController, VisionImageError, decodeVisionCanonicalImage } from '../dist-electron/vision-images.js'
import * as protocol from '../dist-electron/vision-history-protocol.js'
import { parseVisionChatId } from '../dist-electron/vision-protocol.js'

const text = await readFile(new URL('../electron/main.ts', import.meta.url), 'utf8')
const source = ts.createSourceFile('main.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const functions = ['withVisionOwner', 'requireVisionHistoryStorage', 'safeVisionHistoryFailure', 'withVisionHistoryOwner', 'visionHistoryList', 'clearAllNativeVisionHistory']
const declarations = source.statements.filter(item => ts.isFunctionDeclaration(item) && functions.includes(item.name?.text))
assert.equal(declarations.length, functions.length)
const register = source.statements.find(item => ts.isFunctionDeclaration(item) && item.name?.text === 'registerIpcHandlers')
const channels = ['vision:get-history', 'vision:save-image', 'vision:restore-image', 'vision:remove-history', 'vision:clear-history', 'vision:clear-all-history']
const handlers = register.body.statements.filter(item => ts.isExpressionStatement(item) && ts.isCallExpression(item.expression)
  && ts.isStringLiteral(item.expression.arguments[0]) && channels.includes(item.expression.arguments[0].text))
assert.equal(handlers.length, channels.length)
// Execute only these real handlers and helpers, never Main's app/window startup.
const compiled = ts.transpileModule([...declarations, ...handlers].map(item => item.getText(source)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function chunk(kind, data) {
  const result = Buffer.alloc(data.length + 12)
  result.writeUInt32BE(data.length); result.write(kind, 4); data.copy(result, 8)
  let crc = 0xffffffff
  for (const byte of result.subarray(4, -4)) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit++) crc = crc & 1 ? 0xedb88320 ^ crc >>> 1 : crc >>> 1
  }
  result.writeUInt32BE((crc ^ 0xffffffff) >>> 0, result.length - 4)
  return result
}

const header = Buffer.alloc(13)
header.writeUInt32BE(1); header.writeUInt32BE(1, 4); header[8] = 8; header[9] = 6
const PNG = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header),
  chunk('IDAT', deflateSync(Buffer.from([0, 255, 0, 0, 255]))), chunk('IEND', Buffer.alloc(0))])
const CANONICAL = { pngBytes: PNG, width: 1, height: 1, sizeBytes: PNG.length,
  sha256: createHash('sha256').update(PNG).digest('hex'), previewDataUrl: `data:image/png;base64,${PNG.toString('base64')}` }

function deferred() {
  let resolve
  const promise = new Promise(resolve_ => { resolve = resolve_ })
  return { resolve, promise }
}

async function fixture() {
  const folder = await mkdtemp(path.join(os.tmpdir(), 'elysia-vision-history-main-'))
  await mkdir(path.join(folder, 'private-a'))
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', ready: true, archived: false, busy: false }
  const requests = new Set(), registered = new Map()
  const images = new VisionImageController({ getOwner: () => owner,
    chooseFile: async () => { throw new Error('No user picker is opened in this fixture.') },
    readFile: async () => { throw new Error('No user file is read in this fixture.') }, decode: () => CANONICAL })
  const imageId = images.importCanonical('chat_a', CANONICAL, 'synthetic.png', { kind: 'region', label: 'Display 1' }).state.items[0].imageId
  const decoded = { isEmpty: () => false, getSize: () => ({ width: 1, height: 1 }), toPNG: () => PNG, resize: () => decoded }
  const host = { ...protocol, VisionHistoryStore, VisionHistoryError, VisionImageError, decodeVisionCanonicalImage, parseVisionChatId,
    visionHistoryStorage: null, visionRequests: requests, shutdownStarted: false, dataStorageOperation: null,
    dataStorageState: { activeDataRoot: path.join(folder, 'private-a') }, nativeImage: { createFromBuffer: () => decoded },
    requireDataStorageState: () => host.dataStorageState, requireVisionController: () => images,
    requireBackend: () => ({ hydrateVisionOwner: async () => owner.ready && !owner.archived ? owner : null }),
    visionOwner: () => owner, hasActiveVisionWork: () => requests.size > 0,
    assertTrustedSender: event => { assert.equal(event.trusted, true) }, ipcMain: { handle: (channel, callback) => registered.set(channel, callback) } }
  vm.runInNewContext(compiled, host, { timeout: 1000 })
  return { folder, host, owner, images, imageId, requests,
    invoke: (channel, request) => registered.get(channel)({ trusted: true }, request),
    cleanup: async () => {
      // The named mkdtemp directory is the only recursive removal target.
      assert.equal(path.dirname(path.resolve(folder)), path.resolve(os.tmpdir()))
      assert.ok(path.basename(folder).startsWith('elysia-vision-history-main-'))
      await rm(folder, { recursive: true, force: true })
    } }
}

async function save(f) {
  return f.invoke('vision:save-image', { chatId: 'chat_a', imageId: f.imageId, confirmed: true })
}

test('History remains absent by default; explicit opt-in saves only owned pixels and source metadata under the private root', async () => {
  const f = await fixture()
  try {
    assert.equal((await f.invoke('vision:get-history', 'chat_a')).items.length, 0)
    await assert.rejects(access(path.join(f.host.dataStorageState.activeDataRoot, 'workspace', 'vision', 'history')))
    assert.throws(() => f.invoke('vision:save-image', { chatId: 'chat_a', imageId: f.imageId, confirmed: false }))
    assert.throws(() => f.invoke('vision:save-image', { chatId: 'chat_a', imageId: f.imageId, confirmed: true, ocr: 'private marker' }))
    const state = await save(f)
    assert.equal(state.items.length, 1); assert.deepEqual(state.owner, { chatId: 'chat_a', projectId: null })
    const directory = path.join(f.host.dataStorageState.activeDataRoot, 'workspace', 'vision', 'history')
    const names = await readdir(directory); assert.equal(names.length, 1)
    const record = await readFile(path.join(directory, names[0]))
    assert.equal(record.includes(PNG), true); assert.equal(record.includes(Buffer.from('private marker')), false)
    assert.equal(record.includes(Buffer.from('ocrExtraction')), false); assert.equal(record.includes(Buffer.from('modelInference')), false)
  } finally { await f.cleanup() }
})

test('Explicit restore re-decodes only the owned saved copy and imports a fresh session ID without reviving the former image', async () => {
  const f = await fixture()
  try {
    const historyId = (await save(f)).items[0].historyId
    f.images.clearState('chat_a')
    const restored = await f.invoke('vision:restore-image', { chatId: 'chat_a', historyId })
    assert.equal(restored.cancelled, false); assert.equal(restored.state.items.length, 1)
    assert.notEqual(restored.state.items[0].imageId, f.imageId)
    assert.deepEqual(f.images.getImageSource('chat_a', restored.state.items[0].imageId), { kind: 'region', label: 'Display 1' })
    assert.throws(() => f.images.getImage('chat_a', f.imageId))
  } finally { await f.cleanup() }
})

test('Changing Project or private data root filters old history and never silently recreates history in the new root', async () => {
  const f = await fixture()
  try {
    const historyId = (await save(f)).items[0].historyId
    f.owner.projectId = 'project_other'
    assert.equal((await f.invoke('vision:get-history', 'chat_a')).items.length, 0)
    await assert.rejects(f.invoke('vision:restore-image', { chatId: 'chat_a', historyId }))
    f.owner.projectId = null; f.host.dataStorageState.activeDataRoot = path.join(f.folder, 'private-b')
    await mkdir(f.host.dataStorageState.activeDataRoot)
    assert.equal((await f.invoke('vision:get-history', 'chat_a')).items.length, 0)
    await assert.rejects(access(path.join(f.host.dataStorageState.activeDataRoot, 'workspace', 'vision', 'history')))
    await assert.rejects(f.invoke('vision:restore-image', { chatId: 'chat_a', historyId }))
  } finally { await f.cleanup() }
})

for (const change of ['project', 'root', 'project_aba', 'disconnect_aba']) {
  test(`Delayed restore refuses ${change} before importing any old pixels into the current scope`, async () => {
    const f = await fixture()
    try {
      const historyId = (await save(f)).items[0].historyId
      f.images.clearState('chat_a')
      const entered = deferred(), release = deferred(), store = f.host.visionHistoryStorage.store
      const restore = store.restore.bind(store)
      store.restore = async (...args) => { entered.resolve(); await release.promise; return restore(...args) }
      const pending = f.invoke('vision:restore-image', { chatId: 'chat_a', historyId })
      await entered.promise
      if (change === 'project' || change === 'project_aba') {
        f.owner.projectId = 'project_other'; f.images.synchronize()
        if (change === 'project_aba') { f.owner.projectId = null; f.images.synchronize() }
      }
      if (change === 'root') f.host.dataStorageState.activeDataRoot = path.join(f.folder, 'private-b')
      if (change === 'disconnect_aba') { f.owner.ready = false; f.images.synchronize(); f.owner.ready = true; f.images.synchronize() }
      release.resolve(); await assert.rejects(pending, error => error.code === 'owner_changed')
      assert.equal(f.images.getState('chat_a').items.length, 0)
    } finally { await f.cleanup() }
  })
}

test('An admitted save retains global capacity until actual private I/O settles, including shutdown, and new cleanup is refused', async () => {
  const f = await fixture()
  try {
    await f.invoke('vision:get-history', 'chat_a')
    const store = f.host.visionHistoryStorage.store, original = store.save.bind(store), entered = deferred(), release = deferred()
    store.save = async (...args) => { entered.resolve(); await release.promise; return original(...args) }
    const pending = save(f); await entered.promise
    assert.equal(f.requests.size, 1)
    await assert.rejects(f.invoke('vision:clear-all-history', { confirmed: true }), error => error.code === 'busy')
    f.host.shutdownStarted = true; assert.equal(f.requests.size, 1)
    release.resolve(); await assert.rejects(pending, error => error.code === 'owner_changed')
    assert.equal(f.requests.size, 0); assert.equal((await store.list({ chatId: 'chat_a', projectId: null })).length, 1)
  } finally { await f.cleanup() }
})

test('Separately confirmed global history cleanup removes deleted-Chat copies without deleting current session pixels', async () => {
  const f = await fixture()
  try {
    await save(f)
    f.owner.chatId = 'chat_other'
    assert.throws(() => f.invoke('vision:clear-all-history', { confirmed: false }))
    await f.invoke('vision:clear-all-history', { confirmed: true })
    assert.equal((await f.host.visionHistoryStorage.store.list({ chatId: 'chat_a', projectId: null })).length, 0)
  } finally { await f.cleanup() }
})
