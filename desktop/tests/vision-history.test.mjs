/** Verify opt-in pixel-only retention, strict public DTOs, owner isolation, bounded atomic storage, and safe explicit deletion. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const moduleUrl = process.env.ELYSIA_VISION_HISTORY_TEST_MODULE
  ? pathToFileURL(process.env.ELYSIA_VISION_HISTORY_TEST_MODULE).href : new URL('../dist-electron/vision-history.js', import.meta.url).href
const { VisionHistoryStore, VisionHistoryError } = await import(moduleUrl)
const protocol = await import(new URL('./vision-history-protocol.js', moduleUrl))
const alice = { chatId: 'chat_alice', projectId: 'project_one' }
const bob = { chatId: 'chat_bob', projectId: null }
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6fS4AAAAASUVORK5CYII=', 'base64')
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex')
const input = (bytes = png) => ({ fileName: '中文图片.png', source: { kind: 'file', label: '中文图片.png' },
  pngBytes: Buffer.from(bytes), width: 1, height: 1, sha256: sha256(bytes) })
const isCode = code => error => error instanceof VisionHistoryError && error.code === code
const storagePath = root => path.join(root, 'workspace', 'vision', 'history')
const targetPath = (root, id) => path.join(storagePath(root), `${id}.evh`)

async function fixture(t) {
  const container = await fs.mkdtemp(path.join(os.tmpdir(), 'elysia-vision-history-test-'))
  const root = path.join(container, 'data')
  await fs.mkdir(root)
  const sentinel = path.join(container, 'original.png')
  await fs.writeFile(sentinel, png)
  t.after(async () => {
    assert.equal(path.dirname(container), path.resolve(os.tmpdir()))
    assert.equal(path.basename(container).startsWith('elysia-vision-history-test-'), true)
    await fs.rm(container, { recursive: true, force: true })
  })
  return { root, container, sentinel, store: new VisionHistoryStore(root) }
}

test('Default construction and every empty read or clear leave storage absent until explicit save', async t => {
  const { root, store, sentinel } = await fixture(t)
  assert.deepEqual(await store.list(alice), [])
  assert.equal(await store.clear(alice), 0)
  await store.clearAll()
  assert.deepEqual(await fs.readdir(root), [])
  assert.deepEqual(await fs.readFile(sentinel), png)
})

test('One atomic pixel-only envelope survives restart and exposes detached safe current-owner metadata', async t => {
  const { root, store } = await fixture(t)
  const saved = await store.save(alice, input())
  assert.deepEqual(Object.keys(saved).sort(), ['fileName', 'height', 'historyId', 'savedAt', 'sha256', 'sizeBytes', 'source', 'width'])
  assert.equal(saved.sha256, sha256(png)); assert.equal(saved.sizeBytes, png.length)
  assert.deepEqual(await fs.readdir(storagePath(root)), [`${saved.historyId}.evh`])
  const restarted = new VisionHistoryStore(root)
  const listed = await restarted.list(alice)
  assert.deepEqual(listed, [saved])
  const restored = await restarted.restore(alice, saved.historyId)
  assert.deepEqual(restored.pngBytes, png)
  restored.pngBytes.fill(0); listed[0].source.label = 'changed.png'
  assert.deepEqual((await restarted.restore(alice, saved.historyId)).pngBytes, png)
  assert.deepEqual(await restarted.list(alice), [saved])
  const file = await fs.readFile(targetPath(root, saved.historyId))
  const header = JSON.parse(file.subarray(8, 8 + file.readUInt32BE(4)).toString('utf8'))
  assert.deepEqual(Object.keys(header).sort(), ['metadata', 'owner', 'version'])
  assert.equal(header.version, 1); assert.deepEqual(header.owner, alice)
  assert.equal(JSON.stringify(header).includes(root), false)
  assert.equal(JSON.stringify(header).includes('analysis'), false)
})

test('Saved images remain isolated by both Chat and Project, including removal and explicit global cleanup', async t => {
  const { store, sentinel } = await fixture(t)
  const first = await store.save(alice, input())
  const second = await store.save(bob, { ...input(), fileName: 'bob.png' })
  assert.deepEqual(await store.list(alice), [first])
  assert.deepEqual(await store.list({ ...alice, projectId: null }), [])
  await assert.rejects(store.restore(bob, first.historyId), isCode('owner_changed'))
  await assert.rejects(store.remove({ ...alice, projectId: 'project_two' }, first.historyId), isCode('owner_changed'))
  assert.equal(await store.clear(alice), 1)
  assert.deepEqual(await store.list(bob), [second])
  await store.clearAll()
  assert.deepEqual(await store.list(bob), [])
  assert.deepEqual(await fs.readFile(sentinel), png)
})

test('The explicit save snapshots mutable owner, source, metadata and pixels before joining its queued disk operation', async t => {
  const { store } = await fixture(t)
  const owner = { ...alice }, selected = input()
  const earlier = store.list(alice)
  const pending = store.save(owner, selected)
  owner.chatId = bob.chatId; owner.projectId = bob.projectId
  selected.fileName = 'changed.png'; selected.source.label = 'changed.png'; selected.pngBytes.fill(0); selected.sha256 = sha256(selected.pngBytes)
  await earlier
  const saved = await pending
  assert.equal(saved.fileName, '中文图片.png'); assert.equal(saved.source.label, '中文图片.png')
  assert.deepEqual((await store.restore(alice, saved.historyId)).pngBytes, png)
  assert.deepEqual(await store.list(bob), [])
})

test('Unknown fields, original paths, unsafe labels, wrong hashes and metadata-bearing PNG never create retention files', async t => {
  const { root, store } = await fixture(t)
  for (const selected of [{ ...input(), ocr: 'secret OCR' }, { ...input(), fileName: 'C:\\private\\original.png' },
    { ...input(), source: { kind: 'file', label: '../original.png' } }, { ...input(), source: { kind: 'unknown', label: 'source' } },
    { ...input(), sha256: '0'.repeat(64) }, { ...input(), width: 2 }]) {
    await assert.rejects(store.save(alice, selected), error => error instanceof VisionHistoryError)
  }
  const textChunk = Buffer.alloc(15)
  textChunk.writeUInt32BE(3, 0); textChunk.write('tEXt', 4); textChunk.write('raw', 8)
  const metadataPng = Buffer.concat([png.subarray(0, png.length - 12), textChunk, png.subarray(png.length - 12)])
  await assert.rejects(store.save(alice, input(metadataPng)), isCode('invalid_image'))
  assert.deepEqual(await fs.readdir(root), [])
})

test('Traversal and unconfirmed public requests cannot become native file or deletion authority', async () => {
  const save = { chatId: alice.chatId, imageId: `vision_${'a'.repeat(32)}`, confirmed: true }
  assert.deepEqual(protocol.parseVisionHistorySaveRequest(save), save)
  for (const request of [{ ...save, confirmed: false }, { ...save, path: 'private' }, { ...save, projectId: 'project_other' }]) {
    assert.throws(() => protocol.parseVisionHistorySaveRequest(request), TypeError)
  }
  assert.throws(() => protocol.parseVisionHistorySelectRequest({ chatId: alice.chatId, historyId: '../secret' }), TypeError)
  assert.throws(() => protocol.parseVisionHistoryClearAllRequest({ confirmed: false }), TypeError)
  assert.throws(() => protocol.parseVisionHistoryClearAllRequest({ confirmed: true, chatId: alice.chatId }), TypeError)
  assert.deepEqual(protocol.parseVisionHistoryClearAllRequest({ confirmed: true }), { confirmed: true })
})

test('Public metadata/list validators reject binary payloads, unknown owners, duplicate IDs and oversized lists', async t => {
  const { store } = await fixture(t)
  const saved = await store.save(alice, input())
  const value = { kind: 'vision.history', owner: alice, items: [saved] }
  assert.deepEqual(protocol.parseVisionHistoryList(value), value)
  for (const invalid of [{ ...saved, pngBytes: 'secret' }, { ...saved, savedAt: 'invalid' }, { ...saved, sizeBytes: true },
    { ...saved, source: { kind: 'file', label: 'secret', path: 'C:\\private' } }, { ...saved, sha256: 'invalid' }]) {
    assert.throws(() => protocol.parseVisionHistoryMetadata(invalid), TypeError)
  }
  assert.throws(() => protocol.parseVisionHistoryList({ ...value, items: [saved, saved] }), TypeError)
  assert.throws(() => protocol.parseVisionHistoryList({ ...value, items: Array(33).fill(saved) }), TypeError)
  assert.throws(() => protocol.parseVisionHistoryList({ ...value, owner: { ...alice, root: '/private' } }), TypeError)
})

test('Hash corruption and wrong envelope framing never restore pixels; explicit global deletion still erases corrupt and crash residue', async t => {
  const { root, store, sentinel } = await fixture(t)
  const saved = await store.save(alice, input())
  const target = targetPath(root, saved.historyId)
  const changed = await fs.readFile(target)
  changed[changed.length - 15] ^= 1
  await fs.writeFile(target, changed)
  await assert.rejects(store.restore(alice, saved.historyId), isCode('io_error'))
  await assert.rejects(store.remove(alice, saved.historyId), isCode('io_error'))
  const temporary = path.join(storagePath(root), `vision_history_${'b'.repeat(32)}.tmp`)
  await fs.writeFile(temporary, Buffer.concat([Buffer.from('incomplete'), png]))
  await store.clearAll()
  assert.deepEqual(await fs.readdir(storagePath(root)), [])
  assert.deepEqual(await fs.readFile(sentinel), png)
})

test('Explicit global cleanup can erase oversized corrupt owned records without reading their payload', async t => {
  const { root, store } = await fixture(t)
  const saved = await store.save(alice, input())
  const handle = await fs.open(targetPath(root, saved.historyId), 'r+')
  try { await handle.truncate(17 * 1024 * 1024) } finally { await handle.close() }
  await assert.rejects(store.list(alice), isCode('io_error'))
  await store.clearAll()
  assert.deepEqual(await store.list(alice), [])
})

test('A hardlinked retained name is rejected before restore or global deletion without changing the outside original', async t => {
  const { root, store, sentinel } = await fixture(t)
  const saved = await store.save(alice, input())
  const target = targetPath(root, saved.historyId)
  await fs.unlink(target); await fs.link(sentinel, target)
  await assert.rejects(store.restore(alice, saved.historyId), isCode('io_error'))
  await assert.rejects(store.clearAll(), isCode('io_error'))
  assert.deepEqual(await fs.readFile(sentinel), png)
})

test('Directory junctions or symlinks in the private chain are rejected rather than writing outside the data root', async t => {
  const { root, container, store, sentinel } = await fixture(t)
  const external = path.join(container, 'external')
  await fs.mkdir(external)
  await fs.symlink(external, path.join(root, 'workspace'), process.platform === 'win32' ? 'junction' : 'dir')
  await assert.rejects(store.save(alice, input()), isCode('io_error'))
  await assert.rejects(store.list(alice), isCode('io_error'))
  assert.deepEqual(await fs.readdir(external), [])
  assert.deepEqual(await fs.readFile(sentinel), png)
})

test('A replaced pinned history directory invalidates the old store instead of following the replacement', async t => {
  const { root, store } = await fixture(t)
  await store.save(alice, input())
  const directory = storagePath(root)
  await fs.rename(directory, `${directory}-old`); await fs.mkdir(directory)
  await assert.rejects(store.list(alice), isCode('io_error'))
  await assert.rejects(store.save(alice, input()), isCode('io_error'))
  assert.deepEqual(await new VisionHistoryStore(root).list(alice), [])
})

test('Unknown private filenames refuse global cleanup before any validated record or unrelated sentinel is removed', async t => {
  const { root, store, sentinel } = await fixture(t)
  const saved = await store.save(alice, input())
  const unknown = path.join(storagePath(root), 'unrelated.txt')
  await fs.writeFile(unknown, 'keep')
  await assert.rejects(store.clearAll(), isCode('io_error'))
  assert.deepEqual(await fs.readFile(unknown, 'utf8'), 'keep')
  assert.deepEqual((await store.restore(alice, saved.historyId)).pngBytes, png)
  assert.deepEqual(await fs.readFile(sentinel), png)
})

test('Global record capacity serializes simultaneous saves and never silently evicts another owner', async t => {
  const { root, store } = await fixture(t)
  const operations = Array.from({ length: 33 }, (_, index) => store.save(index % 2 === 0 ? alice : bob, input()))
  const results = await Promise.allSettled(operations)
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 32)
  assert.equal(results[32].status, 'rejected'); assert.equal(results[32].reason.code, 'limit_exceeded')
  assert.equal((await store.list(alice)).length + (await store.list(bob)).length, 32)
  assert.equal((await fs.readdir(storagePath(root))).length, 32)
})

test('The total 128MiB envelope budget refuses an additional large canonical transport without evicting existing records', async t => {
  const { root, store } = await fixture(t)
  const saved = await store.save(alice, input())
  const firstPath = targetPath(root, saved.historyId)
  const original = await fs.readFile(firstPath)
  const savedHeader = JSON.parse(original.subarray(8, 8 + original.readUInt32BE(4)).toString('utf8'))
  await fs.unlink(firstPath)
  // The trusted codec is a separate boundary. This inert fixture expands only
  // PNG transport framing so budget checks can exercise 16MiB without images,
  // native decoding, GPU work, or reading any actual user pixels.
  const large = Buffer.alloc(16 * 1024 * 1024)
  png.subarray(0, 33).copy(large)
  large.writeUInt32BE(large.length - 57, 33); large.write('IDAT', 37)
  png.subarray(png.length - 12).copy(large, large.length - 12)
  for (let index = 0; index < 7; index += 1) {
    const historyId = `vision_history_${index.toString(16).padStart(32, '0')}`
    const header = Buffer.from(JSON.stringify({ ...savedHeader,
      metadata: { ...savedHeader.metadata, historyId, sizeBytes: large.length, sha256: sha256(large) } }), 'utf8')
    const prefix = Buffer.alloc(8); prefix.write('EVH1'); prefix.writeUInt32BE(header.length, 4)
    await fs.writeFile(targetPath(root, historyId), Buffer.concat([prefix, header, large]))
  }
  await assert.rejects(store.save(alice, input(large)), isCode('limit_exceeded'))
  assert.equal((await fs.readdir(storagePath(root))).length, 7)
  await store.clearAll()
  assert.deepEqual(await store.list(alice), [])
})

test('Private crash temporaries count toward capacity, remain invisible, and require explicit global cleanup', async t => {
  const { root, store } = await fixture(t)
  const saved = await store.save(alice, input())
  for (let index = 0; index < 32; index += 1) {
    await fs.writeFile(path.join(storagePath(root), `vision_history_${index.toString(16).padStart(32, '0')}.tmp`), 'partial pixels')
  }
  await assert.rejects(store.list(alice), isCode('limit_exceeded'))
  await store.clearAll()
  assert.deepEqual(await store.list(alice), [])
  await assert.rejects(store.restore(alice, saved.historyId), isCode('not_found'))
})

test('Global cleanup refuses more than 64 owned names before deleting any files', async t => {
  const { root, store } = await fixture(t)
  await store.save(alice, input())
  for (let index = 0; index < 64; index += 1) {
    await fs.writeFile(path.join(storagePath(root), `vision_history_${index.toString(16).padStart(32, '0')}.tmp`), 'partial')
  }
  await assert.rejects(store.clearAll(), isCode('limit_exceeded'))
  assert.equal((await fs.readdir(storagePath(root))).length, 65)
})
