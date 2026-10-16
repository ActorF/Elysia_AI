/** Verify artifact binding, replay rejection, bounded waits, and Main preview URLs. */

import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { setTimeout as delay } from 'node:timers/promises'
import test from 'node:test'

import {
  ImagePreviewOwner,
  ImagePreviewOwnerError,
} from '../dist-electron/image-preview-owner.js'

const ARTIFACT = `gallery_${'a'.repeat(32)}`

function source(counter = 0, overrides = {}) {
  const token = Buffer.alloc(32, 0x6a)
  token.writeBigUInt64BE(BigInt(counter), 24)
  const pngBytes = Buffer.from(`reader-verified-png-${counter}`)
  const metadata = {
    kind: 'image.gallery.open',
    artifactId: ARTIFACT,
    previewToken: token.toString('hex'),
    byteLength: pngBytes.length,
    width: 1024,
    height: 1024,
    sha256: createHash('sha256').update(pngBytes).digest('hex'),
    mediaType: 'image/png',
    ...overrides,
  }
  return {
    metadata,
    frame: {
      previewToken: metadata.previewToken,
      byteLength: metadata.byteLength,
      width: metadata.width,
      height: metadata.height,
      sha256: metadata.sha256,
      mediaType: metadata.mediaType,
      pngBytes,
    },
  }
}

test('an artifact-bound URL is independent of the source token and one-use', async () => {
  const owner = new ImagePreviewOwner()
  const { metadata, frame } = source()
  const pending = owner.expect(ARTIFACT, metadata)
  owner.accept(frame)
  const preview = await pending

  assert.deepEqual(Object.keys(preview).sort(), [
    'artifactId', 'expiresAt', 'height', 'url', 'width',
  ])
  assert.match(preview.url, /^elysia-image:\/\/preview\/[0-9a-f]{64}$/u)
  assert.equal(preview.url.includes(metadata.previewToken), false)
  assert.equal(preview.artifactId, ARTIFACT)
  assert.equal(preview.width, 1024)
  assert.equal(preview.height, 1024)
  assert.equal(Object.isFrozen(preview), true)
  assert.deepEqual(owner.readUrl(preview.url), {
    bytes: frame.pngBytes,
    mediaType: 'image/png',
  })
  assert.equal(owner.readUrl(preview.url), null)
  owner.close()
})

test('one frame may arrive before its response and its bytes are copied', async () => {
  const owner = new ImagePreviewOwner()
  const { metadata, frame } = source()
  const original = Buffer.from(frame.pngBytes)
  owner.accept(frame)
  frame.pngBytes.fill(0)
  const preview = await owner.expect(ARTIFACT, metadata)

  assert.deepEqual(owner.readUrl(preview.url)?.bytes, original)
  owner.close()
})

test('trusted native export consumes correlation without issuing a renderer URL', async () => {
  const owner = new ImagePreviewOwner()
  const { metadata, frame } = source()
  const pending = owner.takeVerified(ARTIFACT, metadata)
  owner.accept(frame)
  const verified = await pending
  const original = Buffer.from(verified.bytes)
  frame.pngBytes.fill(0)

  assert.deepEqual(verified.bytes, original)
  assert.equal(verified.artifactId, ARTIFACT)
  assert.equal(verified.mediaType, 'image/png')
  assert.equal('url' in verified, false)
  assert.equal('previewToken' in verified, false)
  owner.close()
})

for (const [field, replacement] of [
  ['byteLength', 1],
  ['sha256', 'f'.repeat(64)],
  ['width', 512],
  ['height', 512],
  ['mediaType', 'image/jpeg'],
]) {
  test(`a mismatched frame ${field} poisons correlation and revokes URLs`, async () => {
    const failures = []
    const owner = new ImagePreviewOwner({ onFailure: (failure) => failures.push(failure) })
    const first = source()
    owner.accept(first.frame)
    const preview = await owner.expect(ARTIFACT, first.metadata)
    const next = source(1)
    const pending = owner.expect(ARTIFACT, next.metadata)
    const rejected = assert.rejects(pending, ImagePreviewOwnerError)

    assert.throws(
      () => owner.accept({ ...next.frame, [field]: replacement }),
      (error) => error instanceof ImagePreviewOwnerError && error.reason === 'metadata-mismatch',
    )
    await rejected
    assert.deepEqual(failures, ['metadata-mismatch'])
    assert.equal(owner.readUrl(preview.url), null)
  })
}

test('metadata for an artifact other than the requested ID is refused', async () => {
  const failures = []
  const owner = new ImagePreviewOwner({ onFailure: (failure) => failures.push(failure) })
  await assert.rejects(owner.expect(`gallery_${'b'.repeat(32)}`, source().metadata))
  assert.deepEqual(failures, ['metadata-mismatch'])
})

test('metadata is structurally validated before it may reserve a frame', async () => {
  for (const invalid of [
    { previewToken: '0'.repeat(64) },
    { byteLength: 32 * 1024 * 1024 + 1 },
    { mediaType: 'image/jpeg' },
    { width: 2049 },
    { sha256: '../private.png' },
  ]) {
    const failures = []
    const owner = new ImagePreviewOwner({ onFailure: (failure) => failures.push(failure) })
    await assert.rejects(owner.expect(ARTIFACT, { ...source().metadata, ...invalid }))
    assert.deepEqual(failures, ['invalid-metadata'])
  }
})

test('a repeated source metadata binding is rejected before its frame arrives', async () => {
  const failures = []
  const owner = new ImagePreviewOwner({ onFailure: (failure) => failures.push(failure) })
  const { metadata } = source()
  const pending = owner.expect(ARTIFACT, metadata)
  const firstRejected = assert.rejects(pending, ImagePreviewOwnerError)
  await assert.rejects(owner.expect(ARTIFACT, metadata), ImagePreviewOwnerError)
  await firstRejected
  assert.deepEqual(failures, ['replayed-token'])
})

test('source metadata and frames remain single-use after URL consumption', async () => {
  const owner = new ImagePreviewOwner()
  const { metadata, frame } = source()
  owner.accept(frame)
  const preview = await owner.expect(ARTIFACT, metadata)
  owner.readUrl(preview.url)
  await assert.rejects(
    owner.expect(ARTIFACT, metadata),
    (error) => error.reason === 'replayed-token',
  )

  const replayOwner = new ImagePreviewOwner()
  replayOwner.accept(frame)
  await replayOwner.expect(ARTIFACT, metadata)
  assert.throws(() => replayOwner.accept(frame), (error) => error.reason === 'replayed-token')
})

test('a different process nonce cannot be mixed into the same owner', async () => {
  const owner = new ImagePreviewOwner()
  const first = source()
  owner.accept(first.frame)
  await owner.expect(ARTIFACT, first.metadata)
  const other = source(1)
  const mixedFrame = { ...other.frame, previewToken: `ff${other.frame.previewToken.slice(2)}` }

  assert.throws(() => owner.accept(mixedFrame), (error) => error.reason === 'replayed-token')
})

test('metadata from a different process nonce fails before waiting for bytes', async () => {
  const owner = new ImagePreviewOwner()
  const first = source()
  owner.accept(first.frame)
  await owner.expect(ARTIFACT, first.metadata)
  const next = source(1).metadata
  await assert.rejects(
    owner.expect(ARTIFACT, { ...next, previewToken: `ff${next.previewToken.slice(2)}` }),
    (error) => error.reason === 'replayed-token',
  )
})

test('a second unmatched frame fails immediately rather than growing the queue', () => {
  const failures = []
  const owner = new ImagePreviewOwner({ onFailure: (failure) => failures.push(failure) })
  owner.accept(source().frame)
  assert.throws(() => owner.accept(source(1).frame), ImagePreviewOwnerError)
  assert.deepEqual(failures, ['unmatched-frame'])
})

test('an unmatched early frame expires and disables new requests', async () => {
  const failures = []
  const owner = new ImagePreviewOwner({
    earlyWaitMs: 5,
    onFailure: (failure) => failures.push(failure),
  })
  owner.accept(source().frame)
  await delay(20)

  assert.deepEqual(failures, ['unmatched-frame'])
  await assert.rejects(owner.expect(ARTIFACT, source().metadata), (error) => error.reason === 'closed')
})

test('a missing binary frame has a bounded wait and rejects its pending request', async () => {
  const failures = []
  const owner = new ImagePreviewOwner({
    frameWaitMs: 5,
    onFailure: (failure) => failures.push(failure),
  })
  const pending = assert.rejects(owner.expect(ARTIFACT, source().metadata), ImagePreviewOwnerError)
  await delay(20)
  await pending
  assert.deepEqual(failures, ['wait-timeout'])
})

test('only two pending metadata responses may await private frames', async () => {
  const failures = []
  const owner = new ImagePreviewOwner({ onFailure: (failure) => failures.push(failure) })
  const first = assert.rejects(owner.expect(ARTIFACT, source().metadata), ImagePreviewOwnerError)
  const second = assert.rejects(owner.expect(ARTIFACT, source(1).metadata), ImagePreviewOwnerError)
  await assert.rejects(owner.expect(ARTIFACT, source(2).metadata), ImagePreviewOwnerError)
  await Promise.all([first, second])
  assert.deepEqual(failures, ['too-many-pending'])
})

test('the oldest unused preview is evicted when a third image is opened', async () => {
  const owner = new ImagePreviewOwner()
  const previews = []
  for (let counter = 0; counter < 3; counter += 1) {
    const { metadata, frame } = source(counter)
    owner.accept(frame)
    previews.push(await owner.expect(ARTIFACT, metadata))
  }

  assert.equal(owner.readUrl(previews[0].url), null)
  assert.ok(owner.readUrl(previews[1].url))
  assert.ok(owner.readUrl(previews[2].url))
  owner.close()
})

test('URLs expire at access time and maximum configured TTL remains sixty seconds', async () => {
  let now = 100_000
  const owner = new ImagePreviewOwner({ now: () => now, previewTtlMs: 1_000_000 })
  const { metadata, frame } = source()
  owner.accept(frame)
  const preview = await owner.expect(ARTIFACT, metadata)
  assert.equal(preview.expiresAt, 160_000)
  now = preview.expiresAt
  assert.equal(owner.readUrl(preview.url), null)
  owner.close()
})

test('URL lookup rejects altered scheme, authority, escapes, query, and fragment', async () => {
  const owner = new ImagePreviewOwner()
  const { metadata, frame } = source()
  owner.accept(frame)
  const preview = await owner.expect(ARTIFACT, metadata)
  for (const invalid of [
    `${preview.url}?download=1`,
    `${preview.url}#fragment`,
    preview.url.replace('preview/', 'user@preview/'),
    preview.url.replace('preview/', 'PREVIEW/'),
    preview.url.replace('elysia-image:', 'https:'),
    preview.url.replace('/preview/', '/%70review/'),
    `${preview.url}/`,
    'elysia-image://preview/' + '0'.repeat(64),
  ]) {
    assert.equal(owner.readUrl(invalid), null)
  }
  assert.ok(owner.readUrl(preview.url))
  owner.close()
})

test('renderer reset clears URLs and pending requests without resetting replay state', async () => {
  const owner = new ImagePreviewOwner()
  const first = source()
  owner.accept(first.frame)
  const preview = await owner.expect(ARTIFACT, first.metadata)
  const rejected = assert.rejects(owner.expect(ARTIFACT, source(1).metadata), ImagePreviewOwnerError)
  owner.revokeAll()
  await rejected
  assert.equal(owner.readUrl(preview.url), null)
  await assert.rejects(owner.expect(ARTIFACT, first.metadata), (error) => error.reason === 'replayed-token')
})

test('revocation after delivery cannot create a URL from a stale continuation', async () => {
  const owner = new ImagePreviewOwner()
  const { metadata, frame } = source()
  const pending = owner.expect(ARTIFACT, metadata)
  owner.accept(frame)
  owner.revokeAll()
  await assert.rejects(pending, (error) => error.reason === 'closed')
  owner.close()
})

test('closing is idempotent, rejects pending work, and releases all URLs', async () => {
  const owner = new ImagePreviewOwner()
  const first = source()
  owner.accept(first.frame)
  const preview = await owner.expect(ARTIFACT, first.metadata)
  const rejected = assert.rejects(owner.expect(ARTIFACT, source(1).metadata), ImagePreviewOwnerError)
  owner.close()
  owner.close()
  await rejected

  assert.equal(owner.readUrl(preview.url), null)
  assert.throws(() => owner.accept(source(1).frame), (error) => error.reason === 'closed')
  await assert.rejects(owner.expect(ARTIFACT, source(2).metadata), (error) => error.reason === 'closed')
})
