/** Persist bounded redacted desktop decisions and verified review copies under the managed data root. */

import { createHash, randomUUID } from 'node:crypto'
import { constants } from 'node:fs'
import { lstat, mkdir, open, realpath, rename, readdir, unlink } from 'node:fs/promises'
import path from 'node:path'
import {
  parseWorkDesktopAuditEntry,
  type WorkDesktopAuditEntry,
  type WorkDesktopDocument,
} from './work-desktop-tools.js'

const MAX_AUDIT_BYTES = 512 * 1024
const MAX_AUDIT_ENTRIES = 200
const MAX_REVIEW_COPIES = 20
const MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
const extensions = new Set(['.txt', '.md', '.csv', '.json', '.pdf', '.docx'])

function digest(bytes: Uint8Array): string { return createHash('sha256').update(bytes).digest('hex') }
function identity(stats: { dev: bigint; ino: bigint }): string { return JSON.stringify([stats.dev.toString(), stats.ino.toString()]) }
function unchanged(left: { dev: bigint; ino: bigint; size: bigint; mtimeNs: bigint },
  right: { dev: bigint; ino: bigint; size: bigint; mtimeNs: bigint }): boolean {
  return identity(left) === identity(right) && left.size === right.size && left.mtimeNs === right.mtimeNs
}

/** Guard private disk paths and serialize audit replacements without giving stored metadata control authority. */
export class WorkDesktopStorage {
  private readonly root: string
  private serial: Promise<unknown> = Promise.resolve()
  private readonly directories = new Map<string, string>()
  private readonly copies = new Map<string, { identity: string; sha256: string }>()

  /** Bind one already selected managed data root without creating any folders or files. */
  constructor(dataRoot: string) {
    if (!path.isAbsolute(dataRoot)) throw new TypeError('Desktop storage root must be absolute.')
    this.root = path.resolve(dataRoot)
  }

  /** Durably append one validated redacted event while retaining a bounded newest history. */
  append(entry: WorkDesktopAuditEntry): Promise<void> {
    const checked = parseWorkDesktopAuditEntry(entry)
    return this.lock(async () => {
      const directory = await this.directory()
      const current = await this.readDocument(directory)
      const entries = [...current.entries, checked]
      let truncated = current.truncated
      while (entries.length > MAX_AUDIT_ENTRIES
        || Buffer.byteLength(JSON.stringify({ version: 1, entries, truncated }), 'utf8') > MAX_AUDIT_BYTES) {
        if (entries.length <= 1) throw new Error('Desktop audit exceeds its bound.')
        entries.shift(); truncated = true
      }
      const target = path.join(directory, 'audit.json')
      const temporary = path.join(directory, `audit-${randomUUID()}.tmp`)
      const handle = await open(temporary, 'wx', 0o600)
      try {
        try {
          await handle.writeFile(JSON.stringify({ version: 1, entries, truncated }), 'utf8')
          await handle.sync()
        } finally { await handle.close() }
        await this.directory()
        await this.checkLeaf(target, true)
        await rename(temporary, target)
        await this.directory()
        await this.checkLeaf(target, false)
      } finally {
        // Only this operation's exclusively created temporary file is removed;
        // review documents remain until the user manages their retained copies.
        await unlink(temporary).catch(() => undefined)
      }
    })
  }

  /** Return newest-first metadata for exactly one Chat/Project, with no document or clipboard bytes. */
  list(chatId: string, projectId: string | null): Promise<{ entries: WorkDesktopAuditEntry[]; truncated: boolean }> {
    if (!/^chat_[A-Za-z0-9_-]{1,123}$/u.test(chatId)
      || (projectId !== null && !/^project_[A-Za-z0-9_-]{1,120}$/u.test(projectId))) throw new TypeError('Desktop audit owner is invalid.')
    return this.lock(async () => {
      const current = await this.readDocument(await this.directory())
      return { entries: current.entries.filter((entry) => entry.chatId === chatId && entry.projectId === projectId).reverse(),
        truncated: current.truncated }
    })
  }

  /** Store one exclusively named hash-verified review copy; refuse capacity instead of deleting an open copy. */
  saveReview(document: WorkDesktopDocument): Promise<string> {
    if (!/^desktop_action_[0-9a-f]{32}$/u.test(document.actionId)
      || path.basename(document.name) !== document.name || document.name !== document.name.trim()
      || /[<>:"\\/|?*]/u.test(document.name) || /\p{Surrogate}/u.test(document.name)
      || Array.from(document.name).some((character) => (character.codePointAt(0) ?? 0) < 32 || character === '\u007f')
      || !extensions.has(path.extname(document.name).toLowerCase()) || !(document.bytes instanceof Uint8Array)
      || document.bytes.byteLength > MAX_DOCUMENT_BYTES || !/^[0-9a-f]{64}$/u.test(document.sha256)
      || digest(document.bytes) !== document.sha256) throw new TypeError('Desktop review copy is invalid.')
    const detached = new Uint8Array(document.bytes)
    return this.lock(async () => {
      const desktop = await this.directory()
      const reviews = await this.childDirectory(desktop, 'reviews')
      const children = await readdir(reviews, { withFileTypes: true })
      if (children.length >= MAX_REVIEW_COPIES || children.some((entry) => !entry.isDirectory() || entry.isSymbolicLink())) {
        throw new Error('Desktop review capacity requires user cleanup.')
      }
      const review = path.join(reviews, document.actionId)
      // Duplicate action IDs never overwrite a previous review or reinterpret
      // a stored audit token as authority to change another copy.
      await mkdir(review, { mode: 0o700 })
      await this.verifyDirectory(review)
      const target = path.join(review, document.name)
      const handle = await open(target, 'wx+', 0o600)
      let copiedIdentity: string
      try {
        await handle.writeFile(detached)
        await handle.sync()
        const bytes = Buffer.alloc(detached.byteLength)
        let offset = 0
        while (offset < bytes.length) {
          const result = await handle.read(bytes, offset, bytes.length - offset, offset)
          if (result.bytesRead === 0) throw new Error('Desktop review verification failed.')
          offset += result.bytesRead
        }
        if (digest(bytes) !== document.sha256) throw new Error('Desktop review verification failed.')
        copiedIdentity = identity(await handle.stat({ bigint: true }))
      } finally { await handle.close() }
      await this.directory()
      await this.verifyDirectory(reviews)
      await this.verifyDirectory(review)
      await this.checkLeaf(target, false)
      if (identity(await lstat(target, { bigint: true })) !== copiedIdentity!) throw new Error('Desktop review identity changed.')
      this.copies.set(target, { identity: copiedIdentity!, sha256: document.sha256 })
      return target
    })
  }

  /** Recheck a freshly stored private copy immediately before its native app receives the path. */
  verifyReview(target: string): Promise<void> {
    return this.lock(async () => {
      const expected = this.copies.get(target)
      if (expected === undefined) throw new Error('Desktop review is not owned by this store.')
      const desktop = await this.directory()
      await this.verifyDirectory(path.join(desktop, 'reviews'))
      await this.verifyDirectory(path.dirname(target))
      await this.checkLeaf(target, false)
      const handle = await open(target, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
      try {
        const stats = await handle.stat({ bigint: true })
        if (identity(stats) !== expected.identity || stats.size > BigInt(MAX_DOCUMENT_BYTES) || stats.nlink !== 1n
          || !unchanged(stats, await lstat(target, { bigint: true }))) throw new Error('Desktop review identity changed.')
        const bytes = Buffer.alloc(Number(stats.size))
        let offset = 0
        while (offset < bytes.length) {
          const read = await handle.read(bytes, offset, bytes.length - offset, offset)
          if (read.bytesRead === 0) throw new Error('Desktop review content changed.')
          offset += read.bytesRead
        }
        if (digest(bytes) !== expected.sha256 || !unchanged(stats, await handle.stat({ bigint: true })) || !unchanged(stats, await lstat(target, { bigint: true }))) {
          throw new Error('Desktop review content changed.')
        }
      } finally { await handle.close() }
      await this.verifyDirectory(path.dirname(target))
    })
  }

  private lock<Result>(operation: () => Promise<Result>): Promise<Result> {
    const result = this.serial.then(operation)
    this.serial = result.catch(() => undefined)
    return result
  }

  private async directory(): Promise<string> {
    await this.verifyDirectory(this.root)
    let directory = this.root
    for (const component of ['workspace', 'work', 'desktop']) directory = await this.childDirectory(directory, component)
    return directory
  }

  private async childDirectory(parent: string, name: string): Promise<string> {
    await this.verifyDirectory(parent)
    const directory = path.join(parent, name)
    try { await mkdir(directory, { mode: 0o700 }) }
    catch (error: unknown) { if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error }
    await this.verifyDirectory(parent)
    await this.verifyDirectory(directory)
    return directory
  }

  private async verifyDirectory(directory: string): Promise<void> {
    const stats = await lstat(directory, { bigint: true })
    if (!stats.isDirectory() || stats.isSymbolicLink() || await realpath(directory) !== path.resolve(directory)) {
      throw new Error('Desktop storage directory is unsafe.')
    }
    // These managed private paths never follow a replaced parent, even if its
    // new directory has the same spelling and is itself a regular directory.
    const previous = this.directories.get(directory)
    if (previous !== undefined && previous !== identity(stats)) throw new Error('Desktop storage directory identity changed.')
    this.directories.set(directory, identity(stats))
  }

  private async checkLeaf(target: string, missing: boolean): Promise<void> {
    try {
      const stats = await lstat(target, { bigint: true })
      if (!stats.isFile() || stats.isSymbolicLink() || stats.nlink !== 1n) throw new Error('Desktop storage file is unsafe.')
    } catch (error: unknown) { if (!missing || (error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
  }

  private async readDocument(directory: string): Promise<{ entries: WorkDesktopAuditEntry[]; truncated: boolean }> {
    const target = path.join(directory, 'audit.json')
    await this.checkLeaf(target, true)
    let handle
    try { handle = await open(target, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)) }
    catch (error: unknown) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return { entries: [], truncated: false }
      throw error
    }
    try {
      const stats = await handle.stat({ bigint: true })
      if (!stats.isFile() || stats.nlink !== 1n || stats.size > BigInt(MAX_AUDIT_BYTES)) throw new Error('Desktop audit exceeds its bound.')
      if (!unchanged(stats, await lstat(target, { bigint: true }))) throw new Error('Desktop audit identity changed.')
      const encoded = Buffer.alloc(Number(stats.size))
      let offset = 0
      while (offset < encoded.length) {
        const result = await handle.read(encoded, offset, encoded.length - offset, offset)
        if (result.bytesRead === 0) throw new Error('Desktop audit changed while reading.')
        offset += result.bytesRead
      }
      const raw = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(encoded)) as Record<string, unknown>
      if (!unchanged(stats, await handle.stat({ bigint: true })) || !unchanged(stats, await lstat(target, { bigint: true }))) throw new Error('Desktop audit changed while reading.')
      await this.directory()
      if (raw === null || typeof raw !== 'object' || Array.isArray(raw) || Object.keys(raw).length !== 3
        || raw.version !== 1 || !Array.isArray(raw.entries) || raw.entries.length > MAX_AUDIT_ENTRIES
        || typeof raw.truncated !== 'boolean') throw new Error('Desktop audit is invalid.')
      return { entries: raw.entries.map(parseWorkDesktopAuditEntry), truncated: raw.truncated }
    } finally { await handle.close() }
  }
}
