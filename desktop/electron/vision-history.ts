/**
 * Persist explicitly saved canonical Vision pixels under the managed private data root.
 *
 * Construction and listing never create directories. Each record is one bounded
 * versioned metadata/PNG envelope, published by exclusive temporary creation,
 * file fsync, and rename. OCR, model output, original paths, and Chat text are
 * never retained. BigInt identities and no-link checks detect stale paths; the
 * trusted private directory and this store's mutex exclude competing application
 * writers. These Node checks do not claim protection against a hostile local
 * process racing Windows path operations. Data-root changes require a new store.
 */

import { createHash, randomUUID } from 'node:crypto'
import { constants, type BigIntStats } from 'node:fs'
import { lstat, mkdir, open, opendir, realpath, rename, unlink } from 'node:fs/promises'
import path from 'node:path'
import { validateVisionDimensions, VISION_MAX_CANONICAL_BYTES } from './vision-protocol.js'
import { parseVisionHistoryOwner as owner, parseVisionHistoryMetadata as metadata,
  VISION_HISTORY_MAX_RECORDS as MAX_RECORDS, VISION_HISTORY_MAX_TOTAL_BYTES as MAX_TOTAL_BYTES,
  type VisionHistoryOwner, type VisionHistorySource, type VisionHistoryMetadata } from './vision-history-protocol.js'

export type { VisionHistoryOwner, VisionHistorySource, VisionHistoryMetadata } from './vision-history-protocol.js'
/** Accept only pixels already decoded and stripped of metadata by Main's canonical image service. */
export interface VisionHistoryInput {
  fileName: string
  source: VisionHistorySource
  pngBytes: Buffer
  width: number
  height: number
  sha256: string
}
/** Return detached saved pixels only to trusted Main for an explicit native re-decode and session import. */
export interface VisionHistoryRestored { metadata: VisionHistoryMetadata; pngBytes: Buffer }
/** Bound private failure categories without reflecting paths or untrusted disk contents. */
export type VisionHistoryErrorCode = 'invalid_request' | 'invalid_image' | 'limit_exceeded'
  | 'not_found' | 'owner_changed' | 'io_error'
/** Publish a fixed diagnostic code suitable for a controlled native IPC mapping. */
export class VisionHistoryError extends Error {
  /** Withhold native exception messages and stored file contents from the renderer. */
  constructor(readonly code: VisionHistoryErrorCode) { super(`Vision history failed: ${code}.`) }
}

const MAX_HEADER_BYTES = 8192
const MAX_ENVELOPE_BYTES = VISION_MAX_CANONICAL_BYTES + MAX_HEADER_BYTES + 8
const MAGIC = Buffer.from('EVH1', 'ascii')
const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
const ID = /^vision_history_[0-9a-f]{32}$/u
const FILE = /^(vision_history_[0-9a-f]{32})\.(evh|tmp)$/u
type SavedRecord = { version: 1; owner: VisionHistoryOwner; metadata: VisionHistoryMetadata }
type Stored = { name: string; details: BigIntStats; record: SavedRecord }
type Scan = { records: Stored[]; count: number; total: number }

function fail(code: VisionHistoryErrorCode): never { throw new VisionHistoryError(code) }
function digest(bytes: Uint8Array): string { return createHash('sha256').update(bytes).digest('hex') }
function identity(details: BigIntStats): string { return `${details.dev}:${details.ino}` }
function same(left: BigIntStats, right: BigIntStats): boolean {
  return identity(left) === identity(right) && left.size === right.size && left.mtimeNs === right.mtimeNs
    && left.ctimeNs === right.ctimeNs && right.nlink === 1n && right.isFile() && !right.isSymbolicLink()
}
function missing(error: unknown): boolean { return (error as NodeJS.ErrnoException)?.code === 'ENOENT' }
function checkedRequest<T>(operation: () => T): T {
  try { return operation() } catch (error) {
    if (error instanceof VisionHistoryError) throw error
    fail('invalid_request')
  }
}
function record(value: unknown, keys: string[]): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join(',') !== keys.sort().join(',')) fail('invalid_request')
  return value as Record<string, unknown>
}
function owns(left: VisionHistoryOwner, right: VisionHistoryOwner): boolean {
  return left.chatId === right.chatId && left.projectId === right.projectId
}
function canonical(bytes: Buffer, width: number, height: number): void {
  if (!Buffer.isBuffer(bytes) || bytes.length < 45 || bytes.length > VISION_MAX_CANONICAL_BYTES
    || !bytes.subarray(0, 8).equals(SIGNATURE)) fail('invalid_image')
  try { validateVisionDimensions(width, height) } catch { fail('invalid_image') }
  // The canonical codec verified CRC and actual pixels earlier. This distinct
  // storage check refuses every ancillary metadata chunk instead of silently
  // stripping it, and bounds framing before retaining any supplied buffer.
  let offset = 8, count = 0, data = false, end = false, endedData = false
  while (offset < bytes.length) {
    if (++count > 4096 || offset + 12 > bytes.length) fail('invalid_image')
    const length = bytes.readUInt32BE(offset), next = offset + 12 + length
    const kind = bytes.toString('ascii', offset + 4, offset + 8)
    if (next > bytes.length || !['IHDR', 'PLTE', 'tRNS', 'IDAT', 'IEND'].includes(kind)) fail('invalid_image')
    if (count === 1) {
      if (kind !== 'IHDR' || length !== 13 || bytes.readUInt32BE(offset + 8) !== width
        || bytes.readUInt32BE(offset + 12) !== height) fail('invalid_image')
    } else if (kind === 'IHDR') fail('invalid_image')
    if (kind === 'IDAT') { if (endedData) fail('invalid_image'); data = true }
    else if (data) endedData = true
    if (kind === 'IEND') { if (length !== 0 || !data || next !== bytes.length) fail('invalid_image'); end = true }
    offset = next
  }
  if (!end) fail('invalid_image')
}
function envelope(saved: SavedRecord, png: Buffer): Buffer {
  const header = Buffer.from(JSON.stringify(saved), 'utf8')
  if (header.length > MAX_HEADER_BYTES) fail('limit_exceeded')
  const prefix = Buffer.alloc(8)
  MAGIC.copy(prefix); prefix.writeUInt32BE(header.length, 4)
  return Buffer.concat([prefix, header, png])
}
function decode(bytes: Buffer, fileId: string): { saved: SavedRecord; png: Buffer } {
  if (bytes.length < 8 || !bytes.subarray(0, 4).equals(MAGIC)) fail('io_error')
  const length = bytes.readUInt32BE(4)
  if (length < 1 || length > MAX_HEADER_BYTES || 8 + length > bytes.length) fail('io_error')
  const parsed = record(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(8, 8 + length))),
    ['version', 'owner', 'metadata'])
  if (parsed.version !== 1) fail('io_error')
  const saved: SavedRecord = { version: 1, owner: owner(parsed.owner as VisionHistoryOwner), metadata: metadata(parsed.metadata) }
  const png = bytes.subarray(8 + length)
  if (saved.metadata.historyId !== fileId || saved.metadata.sizeBytes !== png.length
    || saved.metadata.sha256 !== digest(png)) fail('io_error')
  canonical(png, saved.metadata.width, saved.metadata.height)
  return { saved, png }
}

/** Own bounded opt-in plaintext image records without accessing attachments, original files, or drawing output. */
export class VisionHistoryStore {
  private root: string
  private serial: Promise<unknown> = Promise.resolve()
  private readonly directories = new Map<string, string>()

  /** Bind a trusted managed data root without creating directories or reading saved pixels. */
  constructor(dataRoot: string) {
    if (typeof dataRoot !== 'string' || !path.isAbsolute(dataRoot) || dataRoot.startsWith('\\\\')
      || dataRoot.startsWith('//') || dataRoot.split(/[\\/]/u).includes('..')) throw new TypeError('Vision history root is invalid.')
    this.root = path.resolve(dataRoot)
  }

  /** Explicitly save only detached canonical pixels and safe provenance; refuse capacity without evicting history. */
  async save(scope: VisionHistoryOwner, input: VisionHistoryInput): Promise<VisionHistoryMetadata> {
    // Capture owner, metadata, and bytes before joining the queue: a caller's
    // mutable object must not change which Chat or pixels are persisted later.
    const prepared = checkedRequest(() => {
      const checkedOwner = owner(scope)
      record(input, ['fileName', 'source', 'pngBytes', 'width', 'height', 'sha256'])
      if (!Buffer.isBuffer(input.pngBytes) || input.pngBytes.length > VISION_MAX_CANONICAL_BYTES) fail('invalid_image')
      const png = Buffer.from(input.pngBytes)
      canonical(png, input.width, input.height)
      if (typeof input.sha256 !== 'string' || digest(png) !== input.sha256) fail('invalid_image')
      const details = metadata({ historyId: `vision_history_${randomUUID().replaceAll('-', '')}`,
        fileName: input.fileName, source: input.source, width: input.width, height: input.height,
        sizeBytes: png.length, sha256: input.sha256, savedAt: new Date().toISOString() })
      return { checkedOwner, png, details }
    })
    return this.lock(async () => {
      const { checkedOwner, png, details } = prepared
      const saved: SavedRecord = { version: 1, owner: checkedOwner, metadata: details }
      const bytes = envelope(saved, png)
      const directory = (await this.directory(true))!
      const existing = await this.scan(directory)
      if (existing.count >= MAX_RECORDS || existing.total + bytes.length > MAX_TOTAL_BYTES) fail('limit_exceeded')
      const temporary = path.join(directory, `${details.historyId}.tmp`)
      const destination = path.join(directory, `${details.historyId}.evh`)
      let written: BigIntStats | null = null
      try {
        const handle = await open(temporary, 'wx+', 0o600)
        try {
          await handle.writeFile(bytes); await handle.sync()
          written = await handle.stat({ bigint: true })
          const copied = Buffer.alloc(bytes.length)
          let offset = 0
          while (offset < copied.length) {
            const result = await handle.read(copied, offset, copied.length - offset, offset)
            if (result.bytesRead === 0) fail('io_error')
            offset += result.bytesRead
          }
          if (!written.isFile() || written.nlink !== 1n || digest(copied) !== digest(bytes)
            || !same(written, await handle.stat({ bigint: true }))) fail('io_error')
        } finally { await handle.close() }
        await this.directory(false)
        if (!same(written, await this.leaf(temporary))) fail('io_error')
        try { await lstat(destination); fail('io_error') } catch (error) { if (!missing(error)) throw error }
        await rename(temporary, destination)
        await this.syncDirectory(directory)
        await this.directory(false)
        const verified = await this.read(destination, details.historyId)
        if (identity(verified.details) !== identity(written)) fail('io_error')
        return structuredClone(verified.saved.metadata)
      } finally {
        // An interrupted save may leave a private .tmp file. It is not visible
        // history, is included in capacity, and is never automatically promoted
        // or deleted using unverified metadata from another process.
        if (written !== null) {
          try {
            await this.directory(false)
            if (same(written, await this.leaf(temporary))) await unlink(temporary)
          } catch { /* Preserve a changed or missing temporary path rather than deleting another file. */ }
        }
      }
    })
  }

  /** List newest-first metadata for exactly the current owner without creating storage or returning image bytes. */
  async list(scope: VisionHistoryOwner): Promise<VisionHistoryMetadata[]> {
    const checked = checkedRequest(() => owner(scope))
    return this.lock(async () => {
      const directory = await this.directory(false)
      if (directory === null) return []
      const current = await this.scan(directory)
      return current.records.filter(item => owns(item.record.owner, checked)).map(item => structuredClone(item.record.metadata))
        .sort((left, right) => right.savedAt.localeCompare(left.savedAt) || left.historyId.localeCompare(right.historyId))
    })
  }

  /** Restore only an explicitly selected owned record; Main must re-decode pixels before constructing a session thumbnail. */
  async restore(scope: VisionHistoryOwner, historyId: string): Promise<VisionHistoryRestored> {
    const checked = checkedRequest(() => owner(scope))
    if (typeof historyId !== 'string' || !ID.test(historyId)) fail('invalid_request')
    return this.lock(async () => {
      const directory = await this.directory(false)
      if (directory === null) fail('not_found')
      const loaded = await this.read(path.join(directory, `${historyId}.evh`), historyId)
      if (!owns(loaded.saved.owner, checked)) fail('owner_changed')
      return { metadata: structuredClone(loaded.saved.metadata), pngBytes: Buffer.from(loaded.png) }
    })
  }

  /** Remove one verified current-owner record without following links or changing its original input file. */
  async remove(scope: VisionHistoryOwner, historyId: string): Promise<void> {
    const checked = checkedRequest(() => owner(scope))
    if (typeof historyId !== 'string' || !ID.test(historyId)) fail('invalid_request')
    return this.lock(async () => {
      const directory = await this.directory(false)
      if (directory === null) fail('not_found')
      const target = path.join(directory, `${historyId}.evh`)
      const loaded = await this.read(target, historyId)
      if (!owns(loaded.saved.owner, checked)) fail('owner_changed')
      await this.removeVerified(directory, target, loaded.details)
    })
  }

  /** Explicitly delete all verified records for this owner, retaining every other Chat/Project's images. */
  async clear(scope: VisionHistoryOwner): Promise<number> {
    const checked = checkedRequest(() => owner(scope))
    return this.lock(async () => {
      const directory = await this.directory(false)
      if (directory === null) return 0
      const current = await this.scan(directory)
      const selected = current.records.filter(item => owns(item.record.owner, checked))
      for (const item of selected) await this.removeVerified(directory, path.join(directory, item.name), item.details)
      return selected.length
    })
  }

  /** Delete all owned record names, including corrupt payloads and crash temporaries, after Main confirms the global scope. */
  clearAll(): Promise<void> {
    return this.lock(async () => {
      const directory = await this.directory(false)
      if (directory === null) return
      const selected: { name: string; details: BigIntStats }[] = []
      const entries = await opendir(directory)
      for await (const entry of entries) {
        if (selected.length >= MAX_RECORDS * 2) fail('limit_exceeded')
        if (!FILE.test(entry.name) || entry.isSymbolicLink() || !entry.isFile()) fail('io_error')
        selected.push({ name: entry.name, details: await this.leaf(path.join(directory, entry.name), false) })
      }
      // Preflight every owned name before deleting anything. Global confirmation
      // authorizes this private namespace, including corrupt records and crash
      // temporaries; unreadable payloads cannot prevent erasing retained pixels.
      // Unknown names and links still fail closed, without recursive cleanup.
      await this.directory(false)
      for (const item of selected) await this.removeVerified(directory, path.join(directory, item.name), item.details, false)
    })
  }

  private lock<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.serial.then(operation).catch(error => {
      if (error instanceof VisionHistoryError) throw error
      fail('io_error')
    })
    this.serial = result.catch(() => undefined)
    return result
  }

  private async verifyDirectory(directory: string): Promise<void> {
    let cursor = directory
    while (true) {
      const details = await lstat(cursor, { bigint: true })
      if (!details.isDirectory() || details.isSymbolicLink()) fail('io_error')
      const previous = this.directories.get(cursor)
      if (previous !== undefined && previous !== identity(details)) fail('io_error')
      this.directories.set(cursor, identity(details))
      const parent = path.dirname(cursor)
      if (parent === cursor) break
      cursor = parent
    }
  }

  private async directory(create: boolean): Promise<string | null> {
    await this.verifyDirectory(this.root)
    const normalized = await realpath(this.root)
    const before = await lstat(this.root, { bigint: true })
    await this.verifyDirectory(normalized)
    if (identity(before) !== identity(await lstat(normalized, { bigint: true }))) fail('io_error')
    this.root = normalized
    let cursor = this.root
    for (const component of ['workspace', 'vision', 'history']) {
      const child = path.join(cursor, component)
      try { await this.verifyDirectory(child) }
      catch (error) {
        if (!missing(error)) throw error
        if (!create) return null
        await this.verifyDirectory(cursor)
        try { await mkdir(child, { mode: 0o700 }) }
        catch (failure) { if ((failure as NodeJS.ErrnoException).code !== 'EEXIST') throw failure }
        await this.verifyDirectory(child)
      }
      cursor = child
    }
    return cursor
  }

  private async leaf(target: string, bounded = true): Promise<BigIntStats> {
    const details = await lstat(target, { bigint: true })
    if (!details.isFile() || details.isSymbolicLink() || details.nlink !== 1n
      || bounded && details.size > BigInt(MAX_ENVELOPE_BYTES)) fail('io_error')
    return details
  }

  private async read(target: string, historyId: string): Promise<{ saved: SavedRecord; png: Buffer; details: BigIntStats }> {
    await this.directory(false)
    let before: BigIntStats
    try { before = await this.leaf(target) } catch (error) { if (missing(error)) fail('not_found'); throw error }
    const handle = await open(target, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
    try {
      if (!same(before, await handle.stat({ bigint: true }))) fail('io_error')
      const bytes = Buffer.alloc(Number(before.size))
      let offset = 0
      while (offset < bytes.length) {
        const result = await handle.read(bytes, offset, bytes.length - offset, offset)
        if (result.bytesRead === 0) fail('io_error')
        offset += result.bytesRead
      }
      if (!same(before, await handle.stat({ bigint: true })) || !same(before, await this.leaf(target))) fail('io_error')
      await this.directory(false)
      try { return { ...decode(bytes, historyId), details: before } }
      catch { fail('io_error') }
    } finally { await handle.close() }
  }

  private async scan(directory: string): Promise<Scan> {
    const results: Scan = { records: [], count: 0, total: 0 }
    const entries = await opendir(directory)
    for await (const entry of entries) {
      if (++results.count > MAX_RECORDS) fail('limit_exceeded')
      const matched = FILE.exec(entry.name)
      if (matched === null || entry.isSymbolicLink() || !entry.isFile()) fail('io_error')
      const details = await this.leaf(path.join(directory, entry.name))
      results.total += Number(details.size)
      if (results.total > MAX_TOTAL_BYTES) fail('limit_exceeded')
      if (matched[2] === 'tmp') continue
      const loaded = await this.read(path.join(directory, entry.name), matched[1]!)
      results.records.push({ name: entry.name, details: loaded.details, record: loaded.saved })
    }
    await this.directory(false)
    return results
  }

  private async removeVerified(directory: string, target: string, expected: BigIntStats, bounded = true): Promise<void> {
    await this.directory(false)
    if (!same(expected, await this.leaf(target, bounded))) fail('io_error')
    await unlink(target)
    await this.syncDirectory(directory)
    await this.directory(false)
  }

  private async syncDirectory(directory: string): Promise<void> {
    // Node does not expose a portable Windows directory fsync. The envelope's
    // file fsync and atomic same-directory rename still prevent partial records;
    // POSIX additionally flushes the containing directory's publication entry.
    if (process.platform === 'win32') return
    const handle = await open(directory, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
    try { await handle.sync() } finally { await handle.close() }
  }
}
