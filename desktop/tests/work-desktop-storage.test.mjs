/** Verify durable redacted desktop history and immutable review copies in isolated managed roots. */

import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { link, mkdir, mkdtemp, readFile, rename, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const url = process.env.ELYSIA_DESKTOP_STORAGE_TEST_MODULE
  ? pathToFileURL(process.env.ELYSIA_DESKTOP_STORAGE_TEST_MODULE)
  : new URL('../dist-electron/work-desktop-storage.js', import.meta.url)
const { WorkDesktopStorage } = await import(url.href)

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'elysia-desktop-store-'))
  t.after(async () => { await rm(root, { recursive: true, force: true }) })
  return { root, storage: new WorkDesktopStorage(root), directory: path.join(root, 'workspace', 'work', 'desktop') }
}

function entry(number, extra = {}) {
  return { version: 1, actionId: `desktop_action_${number.toString(16).padStart(32, '0')}`,
    timestamp: '2026-10-14T12:00:00.000Z', chatId: 'chat_work', projectId: 'project_work',
    action: 'clipboard.write', phase: 'succeeded', summary: { sizeBytes: 12, sha256: 'a'.repeat(64) }, error: null, ...extra }
}

function document(number, text = 'Exact reviewed text.') {
  const bytes = new TextEncoder().encode(text)
  return { actionId: entry(number).actionId, name: 'review.txt', bytes,
    sha256: createHash('sha256').update(bytes).digest('hex') }
}

test('Desktop history survives restart, filters exact owner and serializes concurrent bounded appends', async (t) => {
  const { root, directory, storage } = await fixture(t)
  await Promise.all(Array.from({ length: 205 }, (_, index) => storage.append(entry(index))))
  await storage.append(entry(300, { chatId: 'chat_other', projectId: null }))
  const restarted = new WorkDesktopStorage(root)
  const result = await restarted.list('chat_work', 'project_work')
  assert.equal(result.truncated, true)
  assert.equal(result.entries.length, 199)
  assert.equal(result.entries[0].actionId, entry(204).actionId)
  assert.equal((await restarted.list('chat_work', null)).entries.length, 0)
  assert.equal((await restarted.list('chat_other', null)).entries.length, 1)
  assert.equal((await readFile(path.join(directory, 'audit.json'), 'utf8')).includes('clipboardText'), false)
})

test('Audit data rejects private text, unknown actions, corrupt data and unsafe redirected folders', async (t) => {
  const { root, storage, directory } = await fixture(t)
  assert.throws(() => storage.append(entry(1, { summary: { text: 'Private clipboard text' } })), TypeError)
  assert.throws(() => storage.append(entry(1, { action: 'shell.execute' })), TypeError)
  await storage.append(entry(1))
  await writeFile(path.join(directory, 'audit.json'), JSON.stringify({ version: 1, entries: [entry(2, { privateText: 'secret' })], truncated: false }))
  await assert.rejects(storage.list('chat_work', 'project_work'))
  await rm(path.join(root, 'workspace'), { recursive: true })
  const outside = await mkdtemp(path.join(tmpdir(), 'elysia-desktop-outside-'))
  t.after(async () => { await rm(outside, { recursive: true, force: true }) })
  await symlink(outside, path.join(root, 'workspace'), process.platform === 'win32' ? 'junction' : 'dir')
  await assert.rejects(storage.append(entry(3)), /unsafe/u)
  assert.deepEqual(await new WorkDesktopStorage(outside).list('chat_work', 'project_work'), { entries: [], truncated: false })
})

test('Review copies keep exact bytes and never overwrite or accept a renderer-controlled filename/hash', async (t) => {
  const { storage, root } = await fixture(t)
  const requested = document(1)
  const copied = await storage.saveReview(requested)
  await storage.verifyReview(copied)
  assert.equal(copied.startsWith(path.join(root, 'workspace', 'work', 'desktop', 'reviews')), true)
  assert.deepEqual(new Uint8Array(await readFile(copied)), requested.bytes)
  await assert.rejects(storage.saveReview(document(1, 'Overwrite attempt')))
  assert.throws(() => storage.saveReview({ ...document(2), name: '..\\evil.exe' }), TypeError)
  assert.throws(() => storage.saveReview({ ...document(2), name: 'evil.exe' }), TypeError)
  assert.throws(() => storage.saveReview({ ...document(2), sha256: '0'.repeat(64) }), TypeError)
  assert.deepEqual(new Uint8Array(await readFile(copied)), requested.bytes)
})

test('Replacing a managed parent or changing a private review refuses its native-open verification', async (t) => {
  const { storage, directory } = await fixture(t)
  const copy = await storage.saveReview(document(1))
  await writeFile(copy, 'Changed private copy')
  await assert.rejects(storage.verifyReview(copy), /changed/u)
  const review = path.dirname(copy)
  await rename(review, `${review}-old`)
  await mkdir(review)
  await writeFile(copy, 'Exact reviewed text.')
  await assert.rejects(storage.verifyReview(copy), /identity/u)
  await rename(directory, `${directory}-old`)
  await mkdir(directory)
  await assert.rejects(storage.append(entry(2)), /identity/u)
})

test('Review capacity refuses new files instead of deleting possibly open approved copies', async (t) => {
  const { storage, directory } = await fixture(t)
  for (let index = 0; index < 20; index += 1) await storage.saveReview(document(index))
  await assert.rejects(storage.saveReview(document(21)), /capacity/u)
  assert.equal((await readFile(path.join(directory, 'reviews', entry(0).actionId, 'review.txt'), 'utf8')), 'Exact reviewed text.')
})

test('Audit linked leaf and oversized history cannot be read as trusted metadata', async (t) => {
  const { storage, directory } = await fixture(t)
  await storage.append(entry(1))
  const source = path.join(directory, 'other.json')
  await writeFile(source, '{}')
  await rm(path.join(directory, 'audit.json'))
  await link(source, path.join(directory, 'audit.json'))
  await assert.rejects(storage.list('chat_work', 'project_work'), /unsafe/u)
  await rm(path.join(directory, 'audit.json'))
  await writeFile(path.join(directory, 'audit.json'), ' '.repeat(512 * 1024 + 1))
  await assert.rejects(storage.list('chat_work', 'project_work'), /bound/u)
})
