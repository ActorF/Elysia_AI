/**
 * Export one Gallery PNG through a Main-owned dialog and an atomic native file
 * publication, without exposing the chosen filesystem path to Python or IPC.
 *
 * Same-directory staging keeps partial output away from the final pathname.
 * Existing destinations retain a hard-link rollback copy through replacement;
 * path identities are rechecked before publication and cleanup only removes
 * objects whose identities this operation still owns.
 */

import { createHash, randomUUID } from 'node:crypto'
import { constants, type BigIntStats } from 'node:fs'
import {
  lstat, realpath, open, link, rename, unlink, type FileHandle,
} from 'node:fs/promises'
import path from 'node:path'
import { type VerifiedImagePreview } from './image-preview-owner.js'
import {
  IMAGE_PREVIEW_MAX_EDGE_PIXELS,
  IMAGE_PREVIEW_MAX_PIXELS,
  IMAGE_PREVIEW_MAX_PNG_BYTES,
} from './image-preview-channel.js'

/** Return only the opaque Gallery identity and whether native publication won. */
export interface ImageExportResult {
  readonly artifactId: string
  readonly exported: boolean
}

/** Keep injectable file operations inside trusted Main code for failure tests. */
export interface ImageExportOperations {
  /** Complete staging bytes on the already exclusively created file handle. */
  write(handle: FileHandle, bytes: Buffer): Promise<void>
  /** Flush staged data before the final pathname can be published. */
  sync(handle: FileHandle): Promise<void>
  /** Perform a same-directory atomic replace without unlinking its destination. */
  rename(source: string, destination: string): Promise<void>
}

/** Supply native UI and a private binary fetch; neither callback accepts a path. */
export interface ImageExportOptions {
  /** Return a native dialog's chosen path, or null when the user cancels. */
  readonly chooseSavePath: (artifactId: string, signal: AbortSignal) => Promise<string | null>
  /** Obtain an artifact-bound PNG revalidated through the private image pipe. */
  readonly getVerifiedImage: (artifactId: string, signal: AbortSignal) => Promise<VerifiedImagePreview>
  readonly operations?: Partial<ImageExportOperations>
}

/** Report sanitized native export failures without reflecting selected paths. */
export class ImageExportError extends Error {
  /** Construct one stable admission or publication failure for renderer handling. */
  constructor(readonly reason: 'busy' | 'closed' | 'invalid-artifact' | 'failed') {
    super('The image could not be exported.')
    this.name = 'ImageExportError'
  }
}

interface ParentIdentity {
  readonly path: string
  readonly stats: BigIntStats
}

interface OwnedFile {
  readonly path: string
  readonly stats: BigIntStats
  readonly parents: readonly ParentIdentity[]
}

interface Destination {
  readonly path: string
  readonly stats: BigIntStats | null
  readonly parents: readonly ParentIdentity[]
}

const ARTIFACT_PATTERN = /^gallery_[0-9a-f]{32}$/u
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
const ABORTED = Symbol('native-image-export-aborted')

/**
 * Admit one native export and drain its private work during application close.
 *
 * The dialog precedes the Gallery fetch, so cancellation creates no private
 * byte transfer. Shutdown aborts pending callback waits and checks cancellation
 * before each write/publication boundary; a completed atomic publication is
 * the commit point and remains a successful user export if close follows it.
 */
export class ImageExportOwner {
  private readonly operations: ImageExportOperations
  private readonly cleanup = new Map<string, OwnedFile>()
  private active: Promise<ImageExportResult> | null = null
  private controller: AbortController | null = null
  private closed = false

  /** Connect trusted native callbacks and optional deterministic I/O adapters. */
  constructor(private readonly options: ImageExportOptions) {
    this.operations = {
      write: (handle, bytes) => handle.writeFile(bytes),
      sync: (handle) => handle.sync(),
      rename,
      ...options.operations,
    }
  }

  /**
   * Export an opaque Gallery selection, refusing concurrent dialogs or paths.
   *
   * Result objects contain no native names. Invalid IDs are rejected before
   * either callback runs, and a busy request never joins an earlier selection.
   */
  export(artifactId: string): Promise<ImageExportResult> {
    if (this.closed) return Promise.reject(new ImageExportError('closed'))
    if (typeof artifactId !== 'string' || !ARTIFACT_PATTERN.test(artifactId)) {
      return Promise.reject(new ImageExportError('invalid-artifact'))
    }
    if (this.active !== null) return Promise.reject(new ImageExportError('busy'))
    const controller = new AbortController()
    this.controller = controller
    const operation = Promise.resolve().then(() => this.exportOnce(artifactId, controller.signal))
    this.active = operation
    void operation.then(
      () => this.release(operation),
      () => this.release(operation),
    )
    return operation
  }

  /**
   * Stop admission, abandon pending UI/fetch waits, and drain owned file cleanup.
   *
   * Late native callbacks remain observed but cannot start filesystem work.
   * A cleanup failure rejects close so Main's shutdown gate can retry it.
   */
  async close(): Promise<void> {
    this.closed = true
    this.controller?.abort()
    await this.active?.catch(() => {})
    try {
      for (const file of this.cleanup.values()) await this.removeOwned(file)
    } catch {
      throw new ImageExportError('failed')
    }
  }

  private release(operation: Promise<ImageExportResult>): void {
    if (this.active === operation) {
      this.active = null
      this.controller = null
    }
  }

  private async exportOnce(artifactId: string, signal: AbortSignal): Promise<ImageExportResult> {
    let staged: OwnedFile | null = null
    let handle: FileHandle | null = null
    let exported = false
    let failed = false
    try {
      const selected = await waitCallback(() => this.options.chooseSavePath(artifactId, signal), signal)
      if (selected === null) return result(artifactId, false)
      checkAbort(signal)
      const destination = await inspectDestination(selected)
      checkAbort(signal)
      const verified = await waitCallback(() => this.options.getVerifiedImage(artifactId, signal), signal)
      checkAbort(signal)
      validateImage(artifactId, verified)
      // Own a snapshot independently of the provider before awaiting native I/O.
      const bytes = Buffer.from(verified.bytes)
      const digest = sha256(bytes)
      await verifyDestination(destination)
      checkAbort(signal)
      const stagedPath = path.join(path.dirname(destination.path), `.elysia-image-${randomUUID()}.tmp`)
      handle = await open(stagedPath,
        constants.O_CREAT | constants.O_EXCL | constants.O_RDWR | (constants.O_NOFOLLOW ?? 0), 0o600)
      const initial = await handle.stat({ bigint: true })
      if (!initial.isFile() || initial.nlink !== 1n) throw new Error('Invalid staging file.')
      staged = { path: stagedPath, stats: initial, parents: destination.parents }
      this.cleanup.set(staged.path, staged)
      checkAbort(signal)
      await this.operations.write(handle, bytes)
      checkAbort(signal)
      await this.operations.sync(handle)
      checkAbort(signal)
      const written = await handle.stat({ bigint: true })
      if (!sameFile(initial, written) || !written.isFile() || written.nlink !== 1n
        || written.size !== BigInt(bytes.length)) throw new Error('Invalid staged length.')
      // Reading the staged handle catches short/custom writes and disk-side
      // mutation instead of trusting successful write/flush return values.
      const checked = Buffer.alloc(bytes.length)
      let offset = 0
      while (offset < checked.length) {
        const read = await handle.read(checked, offset, checked.length - offset, offset)
        if (read.bytesRead <= 0) throw new Error('Incomplete staged image.')
        offset += read.bytesRead
        checkAbort(signal)
      }
      if (sha256(checked) !== digest) throw new Error('Staged image digest differs.')
      staged = { ...staged, stats: written }
      this.cleanup.set(staged.path, staged)
      await handle.close()
      handle = null
      checkAbort(signal)
      await this.publish(staged, destination, signal)
      exported = true
    } catch (error) {
      failed = error !== ABORTED
    } finally {
      await handle?.close().catch(() => { failed = true })
      if (staged !== null) {
        try { await this.removeOwned(staged) }
        catch { failed = true }
      }
    }
    if (failed) throw new ImageExportError('failed')
    return result(artifactId, exported)
  }

  private async publish(staged: OwnedFile, destination: Destination, signal: AbortSignal): Promise<void> {
    await verifyDestination(destination)
    await verifyOwned(staged, 1n)
    checkAbort(signal)
    if (destination.stats === null) {
      // A hard link creates the final name atomically and exclusively. A file
      // appearing after the missing-target check therefore cannot be replaced.
      await link(staged.path, destination.path)
      const published = await readRegular(destination.path, false, 2n)
      if (published === null || !sameFile(published, staged.stats)) {
        throw new Error('Invalid publication identity.')
      }
      await this.removeOwned(staged)
      return
    }

    const backupPath = path.join(path.dirname(destination.path), `.elysia-image-${randomUUID()}.bak`)
    let backup: OwnedFile | null = null
    try {
      await link(destination.path, backupPath)
      backup = { path: backupPath, stats: destination.stats, parents: destination.parents }
      const preserved = await readRegular(backupPath, false, 2n)
      if (preserved === null || !sameContentIdentity(preserved, destination.stats)) {
        throw new Error('Invalid rollback identity.')
      }
      // A backup remains outside generic temporary cleanup until replacement
      // or rollback is proven. An uncertain result must preserve original data.
      await verifyParents(destination.parents)
      const current = await readRegular(destination.path, false, 2n)
      if (current === null || !sameContentIdentity(current, destination.stats)) {
        throw new Error('Destination changed before replacement.')
      }
      await verifyOwned(staged, 1n)
      checkAbort(signal)
      // Node has no handle-relative Windows compare-and-replace primitive.
      // The final identity check detects changes during staging; atomic rename
      // preserves the old target on OS failure and the backup covers uncertain
      // completion. This does not claim protection from a hostile local actor
      // changing directory entries in the final check-to-syscall interval.
      await this.operations.rename(staged.path, destination.path)
      const published = await readRegular(destination.path, false, 1n)
      if (published === null || !sameContentIdentity(published, staged.stats)) {
        throw new Error('Invalid replacement identity.')
      }
      this.cleanup.delete(staged.path)
      await this.removeOwned(backup)
    } catch (error) {
      if (backup !== null) {
        await this.restore(backup, destination, staged)
      }
      throw error
    }
  }

  private async restore(backup: OwnedFile, destination: Destination, staged: OwnedFile): Promise<boolean> {
    try {
      await verifyParents(destination.parents)
      const current = await readRegular(destination.path, true, null)
      if (current !== null && sameContentIdentity(current, destination.stats!)) {
        await this.removeOwned(backup)
        return true
      }
      if (current !== null && !sameFile(current, staged.stats)) return false
      await verifyOwned(backup, 1n)
      await this.operations.rename(backup.path, destination.path)
      const restored = await readRegular(destination.path, false, 1n)
      return restored !== null && sameContentIdentity(restored, destination.stats!)
    } catch {
      return false
    }
  }

  private async removeOwned(file: OwnedFile): Promise<void> {
    await verifyParents(file.parents)
    const current = await readRegular(file.path, true, null)
    if (current !== null) {
      if (!sameFile(current, file.stats)) throw new Error('Cleanup identity changed.')
      await unlink(file.path)
    }
    this.cleanup.delete(file.path)
  }
}

function result(artifactId: string, exported: boolean): ImageExportResult {
  return Object.freeze({ artifactId, exported })
}

function sha256(bytes: Buffer): string {
  return createHash('sha256').update(bytes).digest('hex')
}

function checkAbort(signal: AbortSignal): void {
  if (signal.aborted) throw ABORTED
}

function waitCallback<T>(callback: () => Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(ABORTED); return }
    let settled = false
    const finish = (error: unknown, value?: T): void => {
      if (settled) return
      settled = true
      signal.removeEventListener('abort', abort)
      if (error !== null) reject(error)
      else resolve(value as T)
    }
    const abort = (): void => finish(ABORTED)
    signal.addEventListener('abort', abort, { once: true })
    void Promise.resolve().then(() => {
      checkAbort(signal)
      return callback()
    }).then((value) => finish(null, value), (error: unknown) => finish(error))
  })
}

function validateImage(artifactId: string, image: VerifiedImagePreview): void {
  // Full CRC/decompression validation occurs in Gallery and the private pipe
  // authenticates SHA-256. Check the PNG media envelope again before writing,
  // then compare the staged file's digest against these privately owned bytes.
  if (image.artifactId !== artifactId || image.mediaType !== 'image/png'
    || !Buffer.isBuffer(image.bytes) || image.bytes.length < 58
    || image.bytes.length > IMAGE_PREVIEW_MAX_PNG_BYTES
    || !Number.isInteger(image.width) || !Number.isInteger(image.height)
    || image.width < 1 || image.height < 1
    || image.width > IMAGE_PREVIEW_MAX_EDGE_PIXELS || image.height > IMAGE_PREVIEW_MAX_EDGE_PIXELS
    || image.width * image.height > IMAGE_PREVIEW_MAX_PIXELS
    || !image.bytes.subarray(0, 8).equals(PNG_SIGNATURE)
    || image.bytes.readUInt32BE(8) !== 13
    || image.bytes.toString('ascii', 12, 16) !== 'IHDR'
    || image.bytes.readUInt32BE(16) !== image.width
    || image.bytes.readUInt32BE(20) !== image.height) throw new Error('Invalid private PNG.')
}

async function inspectDestination(selected: string): Promise<Destination> {
  if (typeof selected !== 'string' || !path.isAbsolute(selected)
    || [...selected].some((character) => character.charCodeAt(0) < 32) || selected.length > 32_000
    || path.normalize(selected) !== path.resolve(selected)) throw new Error('Invalid native path.')
  const resolved = path.resolve(selected)
  if (process.platform === 'win32') {
    // Alternate streams, device namespaces, UNC paths, and Win32 name aliases
    // are not ordinary local save targets and do not have these file semantics.
    if (!/^[a-z]:\\/iu.test(resolved) || resolved.startsWith('\\\\')) throw new Error('Invalid native path.')
    for (const component of resolved.slice(3).split('\\')) {
      if (/[<>:"|?*]/u.test(component) || /[. ]$/u.test(component)
        || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/iu.test(component)) {
        throw new Error('Invalid native path component.')
      }
    }
  }
  const root = path.parse(resolved).root
  const parents: ParentIdentity[] = []
  let directory = root
  for (const component of ['', ...path.relative(root, path.dirname(resolved)).split(path.sep).filter(Boolean)]) {
    if (component) directory = path.join(directory, component)
    const stats = await lstat(directory, { bigint: true })
    if (!stats.isDirectory() || stats.isSymbolicLink() || !samePath(await realpath(directory), directory)) {
      throw new Error('Invalid native parent.')
    }
    parents.push({ path: directory, stats })
  }
  return { path: resolved, parents, stats: await readRegular(resolved, true, 1n) }
}

async function verifyParents(parents: readonly ParentIdentity[]): Promise<void> {
  for (const parent of parents) {
    const current = await lstat(parent.path, { bigint: true })
    if (!current.isDirectory() || current.isSymbolicLink() || !sameFile(current, parent.stats)
      || !samePath(await realpath(parent.path), parent.path)) throw new Error('Native parent changed.')
  }
}

async function verifyDestination(destination: Destination): Promise<void> {
  await verifyParents(destination.parents)
  const current = await readRegular(destination.path, true, 1n)
  if ((current === null) !== (destination.stats === null)
    || (current !== null && destination.stats !== null && (
      !sameContentIdentity(current, destination.stats) || current.ctimeNs !== destination.stats.ctimeNs
    ))) {
    throw new Error('Native destination changed.')
  }
}

async function verifyOwned(file: OwnedFile, links: bigint): Promise<void> {
  await verifyParents(file.parents)
  const current = await readRegular(file.path, false, links)
  if (current === null || !sameContentIdentity(current, file.stats)) throw new Error('Owned export file changed.')
}

async function readRegular(target: string, missing: boolean, links: bigint | null): Promise<BigIntStats | null> {
  try {
    const stats = await lstat(target, { bigint: true })
    if (!stats.isFile() || stats.isSymbolicLink() || (links !== null && stats.nlink !== links)
      || !samePath(await realpath(target), target)) throw new Error('Invalid native file.')
    return stats
  } catch (error) {
    if (missing && (error as NodeJS.ErrnoException).code === 'ENOENT') return null
    throw error
  }
}

function samePath(first: string, second: string): boolean {
  return process.platform === 'win32'
    ? first.toLowerCase() === second.toLowerCase()
    : first === second
}

function sameFile(first: BigIntStats, second: BigIntStats): boolean {
  return first.dev === second.dev && first.ino === second.ino
}

function sameContentIdentity(first: BigIntStats, second: BigIntStats): boolean {
  // Creating a rollback hard link changes ctime/nlink without changing bytes.
  // Device/inode, size, and nanosecond mtime identify the selected file here.
  return sameFile(first, second) && first.size === second.size && first.mtimeNs === second.mtimeNs
}
