/**
 * Own Electron Main's stable pointer to movable Python data and safe maintenance.
 *
 * The module deliberately knows nothing about renderer IPC or Python startup.
 * Main supplies trusted native paths, waits for the new Backend to become ready,
 * and then commits or rolls back the returned move transaction. Every recursive
 * walk is bounded and refuses to follow symbolic links or Windows junctions.
 */

import { createHash, randomUUID } from 'node:crypto'
import { constants } from 'node:fs'
import {
  copyFile,
  lstat,
  mkdir,
  open,
  readdir,
  realpath,
  rename,
  rm,
  rmdir,
  statfs,
  unlink,
  type FileHandle,
} from 'node:fs/promises'
import path from 'node:path'

import {
  DATA_STORAGE_BOOTSTRAP_FILE_NAME,
  DATA_STORAGE_CATEGORIES,
  DATA_STORAGE_CLEANABLE_CATEGORIES,
  DATA_STORAGE_LAYOUT_FILE_NAME,
  DATA_STORAGE_SCHEMA_VERSION,
  parseDataStorageCleanupRequest,
  type DataStorageCategory,
  type DataStorageCategoryUsage,
  type DataStorageCleanupResult,
  type DataStorageCommitResult,
  type DataStorageInitializeResult,
  type DataStorageInventory,
  type DataStorageMoveTransaction,
  type DataStorageRollbackResult,
  type DataStorageState,
} from './data-storage-contracts.js'

const MAX_METADATA_BYTES = 64 * 1024
const HASH_BUFFER_BYTES = 1024 * 1024
const DEFAULT_MAX_ENTRIES = 250_000
const DEFAULT_MAX_DEPTH = 64
const DEFAULT_MAX_SINGLE_FILE_BYTES = 16 * 1024 * 1024 * 1024
const DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024 * 1024
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu
const SHA256_PATTERN = /^[0-9a-f]{64}$/u

/** Explicit limits that keep native directory walks and hashing finite. */
export interface DataStorageScanLimits {
  readonly maxEntries: number
  readonly maxDepth: number
  readonly maxSingleFileBytes: number
  readonly maxTotalBytes: number
}

/** Construction inputs owned by Electron Main, never by a renderer. */
export interface ElectronDataStorageOptions {
  readonly userDataDirectory: string
  readonly legacyProjectRoot?: string
  /** Produce canonical timestamps for manifests and capacity snapshots. */
  readonly now?: () => Date
  readonly limits?: Partial<DataStorageScanLimits>
}

/** Reject an invalid path, document, category, or bounded-scan input. */
export class DataStorageValidationError extends Error {}

/** Reject a stale revision, token, or move transaction. */
export class DataStorageConflictError extends Error {}

/** Report native persistence failure without silently choosing another root. */
export class DataStorageStorageError extends Error {}

interface LayoutDocument {
  readonly schemaVersion: typeof DATA_STORAGE_SCHEMA_VERSION
  readonly rootId: string
  readonly createdAt: string
}

interface BootstrapDocument {
  readonly schemaVersion: typeof DATA_STORAGE_SCHEMA_VERSION
  readonly revision: number
  readonly activeDataRoot: string
  readonly rootId: string
  readonly pendingMove: DataStorageMoveTransaction | null
  readonly retainedRoots: readonly string[]
}

interface PathClassification {
  readonly known: boolean
  readonly category: DataStorageCategory | null
}

interface SnapshotEntry {
  readonly relativePath: string
  readonly kind: 'directory' | 'file'
  readonly category: DataStorageCategory | null
  readonly size: number
  readonly modifiedAtMs: number
  readonly sha256: string | null
}

interface TreeSnapshot {
  readonly entries: readonly SnapshotEntry[]
  readonly usage: ReadonlyMap<DataStorageCategory, DataStorageCategoryUsage>
  readonly totalBytes: number
  readonly fileCount: number
  readonly blockedEntries: number
  readonly truncated: boolean
  readonly unknownEntries: number
}

interface CleanupGrant {
  readonly revision: number
  readonly rootId: string
  readonly token: string
  readonly temporaryFingerprint: string
}

const operationLocks = new Map<string, Promise<void>>()

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactFields(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const fields = Reflect.ownKeys(value)
  return fields.length === expected.length
    && expected.every((field) => fields.includes(field))
}

function isCanonicalTimestamp(value: unknown): value is string {
  if (typeof value !== 'string') {
    return false
  }
  const parsed = new Date(value)
  return Number.isFinite(parsed.getTime()) && parsed.toISOString() === value
}

function isRootId(value: unknown): value is string {
  return typeof value === 'string' && UUID_PATTERN.test(value)
}

function requireAbsolutePath(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0 || !path.isAbsolute(value)) {
    throw new DataStorageValidationError(`${label} must be absolute.`)
  }
  if (value.includes('\0')) {
    throw new DataStorageValidationError(`${label} is invalid.`)
  }
  return path.resolve(value)
}

function normalizedPathKey(value: string): string {
  const normalized = path.resolve(value).replace(/[\\/]+$/u, '')
  return process.platform === 'win32' ? normalized.toLocaleLowerCase('en-US') : normalized
}

function pathsEqual(left: string, right: string): boolean {
  return normalizedPathKey(left) === normalizedPathKey(right)
}

function isPathInside(candidate: string, parent: string): boolean {
  const relative = path.relative(path.resolve(parent), path.resolve(candidate))
  return relative !== ''
    && relative !== '..'
    && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative)
}

function pathsOverlap(left: string, right: string): boolean {
  return pathsEqual(left, right)
    || isPathInside(left, right)
    || isPathInside(right, left)
}

function parseMoveTransaction(value: unknown): DataStorageMoveTransaction {
  if (
    !isRecord(value)
    || !hasExactFields(value, [
      'transactionId',
      'previousRoot',
      'previousRootId',
      'destinationRoot',
      'destinationRootId',
      'sourceFingerprint',
      'switchedRevision',
      'startedAt',
    ])
    || !isRootId(value.transactionId)
    || !isRootId(value.previousRootId)
    || !isRootId(value.destinationRootId)
    || typeof value.sourceFingerprint !== 'string'
    || !SHA256_PATTERN.test(value.sourceFingerprint)
    || !Number.isSafeInteger(value.switchedRevision)
    || (value.switchedRevision as number) < 1
    || !isCanonicalTimestamp(value.startedAt)
  ) {
    throw new DataStorageValidationError('Data move transaction is invalid.')
  }
  const previousRoot = requireAbsolutePath(value.previousRoot, 'Previous data root')
  const destinationRoot = requireAbsolutePath(
    value.destinationRoot,
    'Destination data root',
  )
  if (pathsOverlap(previousRoot, destinationRoot)) {
    throw new DataStorageValidationError('Data move roots overlap.')
  }
  return Object.freeze({
    transactionId: value.transactionId,
    previousRoot,
    previousRootId: value.previousRootId,
    destinationRoot,
    destinationRootId: value.destinationRootId,
    sourceFingerprint: value.sourceFingerprint,
    switchedRevision: value.switchedRevision as number,
    startedAt: value.startedAt,
  })
}

function parseLayoutDocument(value: unknown): LayoutDocument {
  if (
    !isRecord(value)
    || !hasExactFields(value, ['schemaVersion', 'rootId', 'createdAt'])
    || value.schemaVersion !== DATA_STORAGE_SCHEMA_VERSION
    || !isRootId(value.rootId)
    || !isCanonicalTimestamp(value.createdAt)
  ) {
    throw new DataStorageValidationError('Data root manifest is invalid.')
  }
  return Object.freeze({
    schemaVersion: DATA_STORAGE_SCHEMA_VERSION,
    rootId: value.rootId,
    createdAt: value.createdAt,
  })
}

function parseBootstrapDocument(value: unknown): BootstrapDocument {
  const legacyFields = [
    'schemaVersion',
    'revision',
    'activeDataRoot',
    'rootId',
    'pendingMove',
  ] as const
  const currentFields = [...legacyFields, 'retainedRoots'] as const
  if (
    !isRecord(value)
    || (
      !hasExactFields(value, legacyFields)
      && !hasExactFields(value, currentFields)
    )
    || value.schemaVersion !== DATA_STORAGE_SCHEMA_VERSION
    || !Number.isSafeInteger(value.revision)
    || (value.revision as number) < 0
    || !isRootId(value.rootId)
  ) {
    throw new DataStorageValidationError('Data storage bootstrap is invalid.')
  }
  const activeDataRoot = requireAbsolutePath(
    value.activeDataRoot,
    'Active data root',
  )
  const pendingMove = value.pendingMove === null
    ? null
    : parseMoveTransaction(value.pendingMove)
  const retainedRoots = value.retainedRoots === undefined
    ? Object.freeze([] as string[])
    : parseRetainedRoots(value.retainedRoots)
  if (
    pendingMove !== null
    && (
      pendingMove.switchedRevision !== value.revision
      || !pathsEqual(pendingMove.destinationRoot, activeDataRoot)
      || pendingMove.destinationRootId !== value.rootId
    )
  ) {
    throw new DataStorageValidationError('Pending data move is inconsistent.')
  }
  return Object.freeze({
    schemaVersion: DATA_STORAGE_SCHEMA_VERSION,
    revision: value.revision as number,
    activeDataRoot,
    rootId: value.rootId,
    pendingMove,
    retainedRoots,
  })
}

function parseRetainedRoots(value: unknown): readonly string[] {
  if (!Array.isArray(value) || value.length > 256) {
    throw new DataStorageValidationError('Retained data roots are invalid.')
  }
  const roots: string[] = []
  for (const candidate of value) {
    const root = requireAbsolutePath(candidate, 'Retained data root')
    if (
      root.length > 32_768
      || roots.some((existing) => pathsEqual(existing, root))
    ) {
      throw new DataStorageValidationError('Retained data roots are invalid.')
    }
    roots.push(root)
  }
  return Object.freeze(roots)
}

async function readBoundedJson(filePath: string): Promise<unknown | null> {
  let metadata
  try {
    metadata = await lstat(filePath)
  } catch (error) {
    if (isRecord(error) && error.code === 'ENOENT') {
      return null
    }
    throw new DataStorageStorageError('Data storage metadata could not be read.')
  }
  if (
    metadata.isSymbolicLink()
    || !metadata.isFile()
    || metadata.size > MAX_METADATA_BYTES
  ) {
    throw new DataStorageValidationError('Data storage metadata is invalid.')
  }
  let handle: FileHandle | null = null
  try {
    handle = await open(filePath, 'r')
    const buffer = Buffer.alloc(MAX_METADATA_BYTES + 1)
    let offset = 0
    while (offset < buffer.byteLength) {
      const result = await handle.read(
        buffer,
        offset,
        buffer.byteLength - offset,
        offset,
      )
      if (result.bytesRead === 0) {
        break
      }
      offset += result.bytesRead
    }
    if (offset > MAX_METADATA_BYTES) {
      throw new DataStorageValidationError('Data storage metadata is invalid.')
    }
    return JSON.parse(buffer.subarray(0, offset).toString('utf8')) as unknown
  } catch (error) {
    if (
      error instanceof DataStorageValidationError
      || error instanceof DataStorageStorageError
    ) {
      throw error
    }
    throw new DataStorageValidationError('Data storage metadata is invalid.')
  } finally {
    await handle?.close().catch(() => {})
  }
}

async function syncDirectoryBestEffort(directory: string): Promise<void> {
  // Directory fsync is not supported uniformly on Windows. File fsync plus an
  // atomic same-directory rename remains the strongest portable boundary.
  let handle: FileHandle | null = null
  try {
    handle = await open(directory, 'r')
    await handle.sync()
  } catch {
    // The synchronized temporary file is still complete if directory sync is
    // unavailable; callers never fall back to an in-place partial write.
  } finally {
    await handle?.close().catch(() => {})
  }
}

async function writeAtomicJson(filePath: string, value: unknown): Promise<void> {
  const directory = path.dirname(filePath)
  const temporaryPath = path.join(
    directory,
    `.${path.basename(filePath)}.${process.pid}.${randomUUID()}.tmp`,
  )
  const payload = `${JSON.stringify(value, null, 2)}\n`
  if (Buffer.byteLength(payload, 'utf8') > MAX_METADATA_BYTES) {
    throw new DataStorageStorageError('Data storage metadata is too large.')
  }
  let temporaryCreated = false
  try {
    await mkdir(directory, { recursive: true })
    const handle = await open(temporaryPath, 'wx', 0o600)
    temporaryCreated = true
    try {
      await handle.writeFile(payload, { encoding: 'utf8' })
      await handle.sync()
    } finally {
      await handle.close()
    }
    await rename(temporaryPath, filePath)
    temporaryCreated = false
    await syncDirectoryBestEffort(directory)
  } catch (error) {
    if (temporaryCreated) {
      await rm(temporaryPath, { force: true }).catch(() => {})
    }
    if (error instanceof DataStorageStorageError) {
      throw error
    }
    throw new DataStorageStorageError('Data storage metadata could not be saved.')
  }
}

async function withOperationLock<Result>(
  key: string,
  operation: () => Promise<Result>,
): Promise<Result> {
  const previous = operationLocks.get(key) ?? Promise.resolve()
  let release: (() => void) | undefined
  const current = new Promise<void>((resolve) => {
    release = resolve
  })
  operationLocks.set(key, current)
  await previous.catch(() => {})
  try {
    return await operation()
  } finally {
    release?.()
    if (operationLocks.get(key) === current) {
      operationLocks.delete(key)
    }
  }
}

function requireTimestamp(now: () => Date): string {
  try {
    const value = now()
    if (!(value instanceof Date) || !Number.isFinite(value.getTime())) {
      throw new Error('invalid timestamp')
    }
    return value.toISOString()
  } catch {
    throw new DataStorageStorageError('Data storage clock is unavailable.')
  }
}

function joinWarnings(
  ...warnings: Array<string | null | undefined>
): string | null {
  const unique = [...new Set(warnings.filter(
    (warning): warning is string => warning !== null && warning !== undefined,
  ))]
  return unique.length === 0 ? null : unique.join(' ')
}

async function pathMetadata(value: string) {
  try {
    return await lstat(value)
  } catch (error) {
    if (isRecord(error) && error.code === 'ENOENT') {
      return null
    }
    throw new DataStorageStorageError('A data storage path could not be inspected.')
  }
}

async function assertUnlinkedDirectory(directory: string): Promise<void> {
  const metadata = await pathMetadata(directory)
  if (
    metadata === null
    || metadata.isSymbolicLink()
    || !metadata.isDirectory()
  ) {
    throw new DataStorageValidationError('Data storage directory is invalid.')
  }
  try {
    const canonical = await realpath(directory)
    // A differing real path reveals a symlink or junction in the ancestor
    // chain. Refusing it prevents a selected path from escaping after review.
    if (!pathsEqual(canonical, directory)) {
      throw new DataStorageValidationError(
        'Data storage directory cannot use links or junctions.',
      )
    }
  } catch (error) {
    if (error instanceof DataStorageValidationError) {
      throw error
    }
    throw new DataStorageStorageError('Data storage directory could not be resolved.')
  }
}

function classifyDataPath(relativePath: string): PathClassification {
  const portable = relativePath.replaceAll('\\', '/')
  const segments = portable.split('/').filter(Boolean)
  if (segments.length === 0) {
    return { known: true, category: null }
  }
  if (segments.length === 1 && segments[0] === DATA_STORAGE_LAYOUT_FILE_NAME) {
    return { known: true, category: null }
  }

  const directRoots: Readonly<Record<string, DataStorageCategory>> = {
    config: 'config',
    settings: 'config',
    chats: 'chats',
    conversations: 'chats',
    projects: 'projects',
    memory: 'memory',
    migrations: 'chats',
    recovery: 'config',
    sources: 'sources',
    attachments: 'sources',
    indexes: 'indexes',
    audio: 'audio',
    cache: 'cache',
    logs: 'logs',
  }
  if (segments[0] !== 'workspace') {
    if (segments[0] === 'knowledge') {
      if (segments.length === 1) {
        return { known: true, category: null }
      }
      if (segments[1].startsWith('vectors.sqlite3')) {
        return { known: true, category: 'indexes' }
      }
      if (['project-sources', 'operations', 'export-intents'].includes(segments[1])) {
        return { known: true, category: 'sources' }
      }
      return { known: false, category: 'other' }
    }
    const category = directRoots[segments[0]]
    return category === undefined
      ? { known: false, category: 'other' }
      : { known: true, category }
  }

  if (segments.length === 1) {
    return { known: true, category: null }
  }
  if (segments[1] === 'knowledge') {
    if (segments.length === 2) {
      return { known: true, category: null }
    }
    if (segments[2].startsWith('vectors.sqlite3')) {
      return { known: true, category: 'indexes' }
    }
    if (['project-sources', 'operations', 'export-intents'].includes(segments[2])) {
      return { known: true, category: 'sources' }
    }
    return { known: false, category: 'other' }
  }
  const category = directRoots[segments[1]]
  return category === undefined
    ? { known: false, category: 'other' }
    : { known: true, category }
}

function defaultUsage(): Map<DataStorageCategory, DataStorageCategoryUsage> {
  return new Map(DATA_STORAGE_CATEGORIES.map((category) => [category, {
    category,
    bytes: 0,
    fileCount: 0,
    entryCount: 0,
  }]))
}

function addUsageEntry(
  usage: Map<DataStorageCategory, DataStorageCategoryUsage>,
  category: DataStorageCategory | null,
  kind: 'directory' | 'file',
  size: number,
): void {
  if (category === null) {
    return
  }
  const current = usage.get(category)
  if (current === undefined) {
    throw new DataStorageStorageError('Data storage category is unavailable.')
  }
  usage.set(category, {
    category,
    bytes: current.bytes + (kind === 'file' ? size : 0),
    fileCount: current.fileCount + (kind === 'file' ? 1 : 0),
    entryCount: current.entryCount + 1,
  })
}

async function hashRegularFile(
  filePath: string,
  expectedSize: number,
  limit: number,
): Promise<string> {
  let handle: FileHandle | null = null
  try {
    handle = await open(filePath, constants.O_RDONLY | constants.O_NOFOLLOW)
    const before = await handle.stat()
    if (
      !before.isFile()
      || before.size !== expectedSize
      || before.size > limit
    ) {
      throw new DataStorageValidationError('A data file changed during scanning.')
    }
    const hash = createHash('sha256')
    const buffer = Buffer.allocUnsafe(HASH_BUFFER_BYTES)
    let bytesRead = 0
    while (bytesRead < expectedSize) {
      const next = await handle.read(
        buffer,
        0,
        Math.min(buffer.byteLength, expectedSize - bytesRead),
        bytesRead,
      )
      if (next.bytesRead === 0) {
        break
      }
      hash.update(buffer.subarray(0, next.bytesRead))
      bytesRead += next.bytesRead
    }
    const after = await handle.stat()
    if (bytesRead !== expectedSize || after.size !== expectedSize) {
      throw new DataStorageValidationError('A data file changed during scanning.')
    }
    return hash.digest('hex')
  } finally {
    await handle?.close().catch(() => {})
  }
}

async function scanTree(
  root: string,
  limits: DataStorageScanLimits,
  options: { readonly hashFiles: boolean; readonly dataRoot: boolean },
): Promise<TreeSnapshot> {
  await assertUnlinkedDirectory(root)
  const usage = defaultUsage()
  const entries: SnapshotEntry[] = []
  const pending = [{ absolutePath: root, relativePath: '', depth: 0 }]
  let totalBytes = 0
  let fileCount = 0
  let visitedEntries = 0
  let blockedEntries = 0
  let unknownEntries = 0
  let truncated = false

  while (pending.length > 0 && !truncated) {
    const directory = pending.pop()
    if (directory === undefined) {
      break
    }
    let children
    try {
      children = await readdir(directory.absolutePath, { withFileTypes: true })
    } catch {
      blockedEntries += 1
      continue
    }
    children.sort((left, right) => left.name.localeCompare(right.name, 'en'))
    for (const child of children) {
      visitedEntries += 1
      if (visitedEntries > limits.maxEntries) {
        truncated = true
        break
      }
      const relativePath = directory.relativePath === ''
        ? child.name
        : path.join(directory.relativePath, child.name)
      if (
        options.dataRoot
        && relativePath === DATA_STORAGE_LAYOUT_FILE_NAME
      ) {
        continue
      }
      const absolutePath = path.join(root, relativePath)
      let metadata
      try {
        metadata = await lstat(absolutePath)
      } catch {
        blockedEntries += 1
        continue
      }
      const classification = options.dataRoot
        ? classifyDataPath(relativePath)
        : { known: true, category: 'other' as const }
      if (metadata.isSymbolicLink()) {
        // Node reports Windows junctions as symbolic links. Count but never
        // descend so capacity scans cannot escape the managed root.
        blockedEntries += 1
        continue
      }
      if (metadata.isDirectory()) {
        const nextDepth = directory.depth + 1
        if (nextDepth > limits.maxDepth) {
          truncated = true
          break
        }
        entries.push({
          relativePath,
          kind: 'directory',
          category: classification.category,
          size: 0,
          modifiedAtMs: metadata.mtimeMs,
          sha256: null,
        })
        addUsageEntry(usage, classification.category, 'directory', 0)
        if (!classification.known) {
          unknownEntries += 1
        }
        pending.push({
          absolutePath,
          relativePath,
          depth: nextDepth,
        })
        continue
      }
      if (!metadata.isFile()) {
        blockedEntries += 1
        continue
      }
      if (
        !Number.isSafeInteger(metadata.size)
        || metadata.size < 0
        || metadata.size > limits.maxSingleFileBytes
        || totalBytes > limits.maxTotalBytes - metadata.size
      ) {
        truncated = true
        break
      }
      let sha256: string | null = null
      if (options.hashFiles) {
        try {
          sha256 = await hashRegularFile(
            absolutePath,
            metadata.size,
            limits.maxSingleFileBytes,
          )
        } catch {
          blockedEntries += 1
          continue
        }
      }
      entries.push({
        relativePath,
        kind: 'file',
        category: classification.category,
        size: metadata.size,
        modifiedAtMs: metadata.mtimeMs,
        sha256,
      })
      totalBytes += metadata.size
      fileCount += 1
      addUsageEntry(usage, classification.category, 'file', metadata.size)
      if (!classification.known) {
        unknownEntries += 1
      }
    }
  }

  entries.sort((left, right) => left.relativePath.localeCompare(
    right.relativePath,
    'en',
  ))
  return Object.freeze({
    entries: Object.freeze(entries),
    usage,
    totalBytes,
    fileCount,
    blockedEntries,
    truncated,
    unknownEntries,
  })
}

function snapshotFingerprint(
  snapshot: TreeSnapshot,
  includeOnlyTemporary = false,
): string {
  const hash = createHash('sha256')
  for (const entry of snapshot.entries) {
    if (
      includeOnlyTemporary
      && (
        entry.category === null
        || !DATA_STORAGE_CLEANABLE_CATEGORIES.includes(
          entry.category as 'audio' | 'cache' | 'logs',
        )
      )
    ) {
      continue
    }
    hash.update(entry.kind === 'directory' ? 'D\0' : 'F\0')
    hash.update(entry.relativePath)
    if (entry.kind === 'file') {
      hash.update('\0')
      hash.update(String(entry.size))
      hash.update('\0')
      hash.update(entry.sha256 ?? String(entry.modifiedAtMs))
    }
    // Directory mtimes necessarily change while recreating children; names and
    // kinds prove empty-directory preservation without producing false mismatch.
    hash.update('\n')
  }
  return hash.digest('hex')
}

async function copySnapshot(
  sourceRoot: string,
  destinationRoot: string,
  snapshot: TreeSnapshot,
): Promise<void> {
  const directories = snapshot.entries
    .filter((entry) => entry.kind === 'directory')
    .sort((left, right) => (
      left.relativePath.split(path.sep).length
      - right.relativePath.split(path.sep).length
    ))
  for (const entry of directories) {
    await mkdir(path.join(destinationRoot, entry.relativePath))
  }
  for (const entry of snapshot.entries) {
    if (entry.kind !== 'file') {
      continue
    }
    const destination = path.join(destinationRoot, entry.relativePath)
    await mkdir(path.dirname(destination), { recursive: true })
    await copyFile(
      path.join(sourceRoot, entry.relativePath),
      destination,
      constants.COPYFILE_EXCL,
    )
    const handle = await open(destination, 'r+')
    try {
      await handle.sync()
    } finally {
      await handle.close()
    }
  }
  // Persist child directory entries from the leaves upward before the staging
  // root is published. Platforms that cannot fsync directories still retain
  // the already-synchronized file contents and atomic rename boundary.
  for (const entry of [...directories].reverse()) {
    await syncDirectoryBestEffort(
      path.join(destinationRoot, entry.relativePath),
    )
  }
  await syncDirectoryBestEffort(destinationRoot)
}

async function availableBytes(root: string): Promise<number | null> {
  try {
    const details = await statfs(root, { bigint: true })
    const value = details.bavail * details.bsize
    return value > BigInt(Number.MAX_SAFE_INTEGER)
      ? Number.MAX_SAFE_INTEGER
      : Number(value)
  } catch {
    return null
  }
}

function statesMatchTransaction(
  pending: DataStorageMoveTransaction | null,
  supplied: DataStorageMoveTransaction,
): boolean {
  return pending !== null
    && pending.transactionId === supplied.transactionId
    && pending.previousRootId === supplied.previousRootId
    && pending.destinationRootId === supplied.destinationRootId
    && pending.sourceFingerprint === supplied.sourceFingerprint
    && pending.switchedRevision === supplied.switchedRevision
    && pending.startedAt === supplied.startedAt
    && pathsEqual(pending.previousRoot, supplied.previousRoot)
    && pathsEqual(pending.destinationRoot, supplied.destinationRoot)
}

function stateFromBootstrap(document: BootstrapDocument): DataStorageState {
  return Object.freeze({
    revision: document.revision,
    rootId: document.rootId,
    activeDataRoot: document.activeDataRoot,
    pendingMove: document.pendingMove,
    retainedRoots: Object.freeze([...document.retainedRoots]),
  })
}

/**
 * Manage one Electron installation's active Python data root.
 *
 * The class serializes operations within the Main process. Bootstrap revisions,
 * exact pending transactions, root manifests, and one-use scan tokens protect
 * every boundary that may also be invoked from renderer-originated intent.
 */
export class ElectronDataStorage {
  readonly #userDataDirectory: string
  readonly #bootstrapPath: string
  readonly #defaultDataRoot: string
  readonly #legacyProjectRoot: string | null
  readonly #now: () => Date
  readonly #limits: DataStorageScanLimits
  #cleanupGrant: CleanupGrant | null = null

  /** Create a manager for one absolute Electron userData directory. */
  constructor(options: ElectronDataStorageOptions) {
    if (!isRecord(options)) {
      throw new DataStorageValidationError('Data storage options are invalid.')
    }
    this.#userDataDirectory = requireAbsolutePath(
      options.userDataDirectory,
      'Electron userData directory',
    )
    this.#bootstrapPath = path.join(
      this.#userDataDirectory,
      DATA_STORAGE_BOOTSTRAP_FILE_NAME,
    )
    this.#defaultDataRoot = path.join(this.#userDataDirectory, 'data')
    this.#legacyProjectRoot = options.legacyProjectRoot === undefined
      ? null
      : requireAbsolutePath(options.legacyProjectRoot, 'Legacy project root')
    this.#now = options.now ?? (() => new Date())
    this.#limits = this.#parseLimits(options.limits ?? {})
  }

  /**
   * Activate the stored/default root and optionally copy legacy workspace data.
   *
   * Legacy import occurs only while creating the default root, copies only the
   * `workspace` subtree, verifies SHA-256 inventory, and never removes source.
   * No optional category directory, including `audio`, is created eagerly.
   */
  async initialize(): Promise<DataStorageInitializeResult> {
    return withOperationLock(this.#bootstrapPath, () => this.#initializeLocked())
  }

  /** Measure all closed categories without following links or unbounded trees. */
  async scan(): Promise<DataStorageInventory> {
    return withOperationLock(this.#bootstrapPath, async () => {
      await this.#initializeLocked()
      const document = await this.#readBootstrapRequired()
      const snapshot = await scanTree(document.activeDataRoot, this.#limits, {
        hashFiles: false,
        dataRoot: true,
      })
      const token = randomUUID()
      this.#cleanupGrant = Object.freeze({
        revision: document.revision,
        rootId: document.rootId,
        token,
        temporaryFingerprint: snapshotFingerprint(snapshot, true),
      })
      const categories = DATA_STORAGE_CATEGORIES.map((category) => (
        Object.freeze(snapshot.usage.get(category) ?? {
          category,
          bytes: 0,
          fileCount: 0,
          entryCount: 0,
        })
      ))
      const reclaimableBytes = categories
        .filter((entry) => DATA_STORAGE_CLEANABLE_CATEGORIES.includes(
          entry.category as 'audio' | 'cache' | 'logs',
        ))
        .reduce((total, entry) => total + entry.bytes, 0)
      const freeBytes = await availableBytes(document.activeDataRoot)
      const warnings: string[] = []
      if (snapshot.blockedEntries > 0) {
        warnings.push('Some linked, special, or unreadable entries were not scanned.')
      }
      if (snapshot.truncated) {
        warnings.push('The capacity scan reached its safety limit.')
      }
      if (freeBytes === null) {
        warnings.push('Free disk capacity is unavailable.')
      }
      return Object.freeze({
        revision: document.revision,
        rootId: document.rootId,
        activeDataRoot: document.activeDataRoot,
        token,
        categories: Object.freeze(categories),
        totalBytes: snapshot.totalBytes,
        fileCount: snapshot.fileCount,
        blockedEntries: snapshot.blockedEntries,
        truncated: snapshot.truncated,
        freeBytes,
        reclaimableBytes,
        measuredAt: requireTimestamp(this.#now),
        warning: warnings.length === 0 ? null : warnings.join(' '),
      })
    })
  }

  /**
   * Copy the active root into an empty Main-selected directory and switch pointer.
   *
   * The old root remains untouched. Main must start the Backend against the new
   * path and call `commitMove` only after readiness, or call `rollbackMove`.
   */
  async prepareMove(
    destinationDirectory: string,
    expectedRevision: number,
  ): Promise<DataStorageMoveTransaction> {
    return withOperationLock(this.#bootstrapPath, async () => {
      await this.#initializeLocked()
      const current = await this.#readBootstrapRequired()
      if (
        !Number.isSafeInteger(expectedRevision)
        || expectedRevision < 0
        || expectedRevision !== current.revision
      ) {
        throw new DataStorageConflictError('Data storage revision is stale.')
      }
      if (current.pendingMove !== null) {
        throw new DataStorageConflictError('A data move is already pending.')
      }
      const destination = await this.#validateEmptyDestination(
        destinationDirectory,
        current.activeDataRoot,
      )
      const sourceSnapshot = await scanTree(
        current.activeDataRoot,
        this.#limits,
        { hashFiles: true, dataRoot: true },
      )
      if (sourceSnapshot.blockedEntries > 0 || sourceSnapshot.truncated) {
        throw new DataStorageValidationError(
          'The active data root cannot be copied safely.',
        )
      }
      const sourceFingerprint = snapshotFingerprint(sourceSnapshot)

      const destinationRootId = randomUUID()
      const stage = path.join(
        path.dirname(destination),
        `.${path.basename(destination)}.elysia-stage-${randomUUID()}`,
      )
      let stageExists = false
      let destinationInstalled = false
      try {
        await mkdir(stage)
        stageExists = true
        await copySnapshot(current.activeDataRoot, stage, sourceSnapshot)
        await writeAtomicJson(path.join(stage, DATA_STORAGE_LAYOUT_FILE_NAME), {
          schemaVersion: DATA_STORAGE_SCHEMA_VERSION,
          rootId: destinationRootId,
          createdAt: requireTimestamp(this.#now),
        } satisfies LayoutDocument)
        const stagedSnapshot = await scanTree(stage, this.#limits, {
          hashFiles: true,
          dataRoot: true,
        })
        if (
          stagedSnapshot.blockedEntries > 0
          || stagedSnapshot.truncated
          || snapshotFingerprint(stagedSnapshot) !== sourceFingerprint
        ) {
          throw new DataStorageStorageError(
            'Staged data did not match its SHA-256 inventory.',
          )
        }

        // Revalidate immediately before replacing the reviewed empty directory;
        // this closes the ordinary selection-to-copy race without ever merging.
        await this.#validateEmptyDestination(destination, current.activeDataRoot)
        await rmdir(destination)
        await rename(stage, destination)
        stageExists = false
        destinationInstalled = true
        await this.#validateManagedRoot(destination, destinationRootId)

        const switchedRevision = current.revision + 1
        if (!Number.isSafeInteger(switchedRevision)) {
          throw new DataStorageStorageError('Data storage revision is exhausted.')
        }
        const transaction = Object.freeze({
          transactionId: randomUUID(),
          previousRoot: current.activeDataRoot,
          previousRootId: current.rootId,
          destinationRoot: destination,
          destinationRootId,
          sourceFingerprint,
          switchedRevision,
          startedAt: requireTimestamp(this.#now),
        })
        await this.#writeBootstrap({
          schemaVersion: DATA_STORAGE_SCHEMA_VERSION,
          revision: switchedRevision,
          activeDataRoot: destination,
          rootId: destinationRootId,
          pendingMove: transaction,
          retainedRoots: current.retainedRoots,
        })
        this.#cleanupGrant = null
        return transaction
      } catch (error) {
        let installedDestinationRetained = false
        if (stageExists) {
          await rm(stage, { force: true, recursive: true }).catch(() => {})
        }
        if (destinationInstalled) {
          try {
            const isolatedRoot = this.#retirementPath(destination)
            const journaled = this.#withRetainedCandidates(
              current,
              [destination, isolatedRoot],
            )
            await this.#writeBootstrap(journaled)
            const deletion = await this.#deleteManagedRootConservatively(
              destination,
              destinationRootId,
              sourceFingerprint,
              isolatedRoot,
            )
            await this.#finalizeRetirementJournal(
              journaled,
              [destination, isolatedRoot],
            )
            installedDestinationRetained = deletion.retained
            if (!deletion.retained) {
              // Restore the native-picker selection to its original empty shape
              // only after exact-file deletion proves no late writer appeared.
              await mkdir(destination).catch(() => {})
            }
          } catch {
            // If journaling or conservative removal is unavailable, never try
            // a broader delete and surface the recovery requirement to Main.
            installedDestinationRetained = true
          }
        }
        if (installedDestinationRetained) {
          throw new DataStorageStorageError(
            'The uncommitted destination changed and was retained for recovery.',
          )
        }
        if (
          error instanceof DataStorageValidationError
          || error instanceof DataStorageConflictError
          || error instanceof DataStorageStorageError
        ) {
          throw error
        }
        throw new DataStorageStorageError('Data root could not be moved.')
      }
    })
  }

  /**
   * Finalize an exact pending move after the new Backend reports ready.
   *
   * The previous root is deleted only when its manifest/rootId match and a
   * complete bounded scan still matches the prepared SHA-256 inventory and
   * finds no blocked or `other` entries. Otherwise it is retained so a late
   * writer can never create user data that was not copied and then delete it.
   */
  async commitMove(transactionValue: unknown): Promise<DataStorageCommitResult> {
    const transaction = parseMoveTransaction(transactionValue)
    return withOperationLock(this.#bootstrapPath, async () => {
      await this.#initializeLocked('active')
      const current = await this.#readBootstrapRequired('active')
      this.#requirePendingTransaction(current, transaction)
      await this.#validateManagedRoot(
        current.activeDataRoot,
        current.rootId,
      )

      const next = this.#withoutPendingMove(current)
      // Finalize the stable pointer before best-effort retirement. A crash or
      // write failure must never leave a pending rollback whose old root was
      // already deleted; once the new Backend is ready, it is authoritative.
      const isolatedRoot = this.#retirementPath(transaction.previousRoot)
      const journaled = this.#withRetainedCandidates(
        next,
        [transaction.previousRoot, isolatedRoot],
      )
      await this.#writeBootstrap(journaled)
      this.#cleanupGrant = null
      const deletion = await this.#deleteManagedRootConservatively(
        transaction.previousRoot,
        transaction.previousRootId,
        transaction.sourceFingerprint,
        isolatedRoot,
      )
      const finalized = await this.#finalizeRetirementJournal(
        journaled,
        [transaction.previousRoot, isolatedRoot],
      )
      return Object.freeze({
        state: stateFromBootstrap(finalized.document),
        oldRootRetained: deletion.retained,
        retainedRoot: deletion.retainedPath,
        warning: joinWarnings(deletion.warning, finalized.warning),
      })
    })
  }

  /** Restore the exact previous pointer and conservatively remove the new root. */
  async rollbackMove(
    transactionValue: unknown,
  ): Promise<DataStorageRollbackResult> {
    const transaction = parseMoveTransaction(transactionValue)
    return withOperationLock(this.#bootstrapPath, async () => {
      await this.#initializeLocked()
      const current = await this.#readBootstrapRequired()
      this.#requirePendingTransaction(current, transaction)
      await this.#validateManagedRoot(
        transaction.previousRoot,
        transaction.previousRootId,
      )
      const revision = current.revision + 1
      if (!Number.isSafeInteger(revision)) {
        throw new DataStorageStorageError('Data storage revision is exhausted.')
      }
      const restored: BootstrapDocument = Object.freeze({
        schemaVersion: DATA_STORAGE_SCHEMA_VERSION,
        revision,
        activeDataRoot: transaction.previousRoot,
        rootId: transaction.previousRootId,
        pendingMove: null,
        retainedRoots: current.retainedRoots,
      })
      // Pointer restoration happens before cleanup, so a cleanup failure cannot
      // strand the next launch on the Backend root that failed readiness.
      const isolatedRoot = this.#retirementPath(transaction.destinationRoot)
      const journaled = this.#withRetainedCandidates(
        restored,
        [transaction.destinationRoot, isolatedRoot],
      )
      await this.#writeBootstrap(journaled)
      const deletion = await this.#deleteManagedRootConservatively(
        transaction.destinationRoot,
        transaction.destinationRootId,
        transaction.sourceFingerprint,
        isolatedRoot,
      )
      const finalized = await this.#finalizeRetirementJournal(
        journaled,
        [transaction.destinationRoot, isolatedRoot],
      )
      this.#cleanupGrant = null
      return Object.freeze({
        state: stateFromBootstrap(finalized.document),
        newRootRetained: deletion.retained,
        retainedRoot: deletion.retainedPath,
        warning: joinWarnings(deletion.warning, finalized.warning),
      })
    })
  }

  /**
   * Remove only revision-bound `audio`, `cache`, and `logs` directory contents.
   *
   * The one-use token is issued by `scan` and bound to the temporary-tree
   * fingerprint. Models, `models/cache`, attachments, indexes, and every durable
   * category are unreachable through this closed deletion implementation.
   */
  async cleanup(requestValue: unknown): Promise<DataStorageCleanupResult> {
    const request = parseDataStorageCleanupRequest(requestValue)
    return withOperationLock(this.#bootstrapPath, async () => {
      await this.#initializeLocked()
      const current = await this.#readBootstrapRequired()
      if (current.pendingMove !== null) {
        throw new DataStorageConflictError('Cleanup cannot run during a data move.')
      }
      const grant = this.#cleanupGrant
      this.#cleanupGrant = null
      if (
        grant === null
        || grant.token !== request.token
        || grant.revision !== request.expectedRevision
        || grant.revision !== current.revision
        || grant.rootId !== current.rootId
      ) {
        throw new DataStorageConflictError('Data cleanup token is stale.')
      }
      const snapshot = await scanTree(current.activeDataRoot, this.#limits, {
        hashFiles: false,
        dataRoot: true,
      })
      if (
        snapshot.blockedEntries > 0
        || snapshot.truncated
        || snapshotFingerprint(snapshot, true) !== grant.temporaryFingerprint
      ) {
        throw new DataStorageConflictError(
          'Temporary data changed after it was measured.',
        )
      }
      const selected = new Set<DataStorageCategory>(request.categories)
      const reclaimedBytes = [...selected].reduce(
        (total, category) => total + (snapshot.usage.get(category)?.bytes ?? 0),
        0,
      )
      const deletedFileCount = [...selected].reduce(
        (total, category) => total + (snapshot.usage.get(category)?.fileCount ?? 0),
        0,
      )
      for (const category of request.categories) {
        for (const candidate of this.#cleanupDirectories(
          current.activeDataRoot,
          category,
        )) {
          await this.#removeAllowlistedDirectory(current.activeDataRoot, candidate)
        }
      }
      const next = this.#incrementRevision(current)
      await this.#writeBootstrap(next)
      return Object.freeze({
        state: stateFromBootstrap(next),
        reclaimedBytes,
        deletedFileCount,
        warning: null,
      })
    })
  }

  #parseLimits(value: Partial<DataStorageScanLimits>): DataStorageScanLimits {
    const limits = {
      maxEntries: value.maxEntries ?? DEFAULT_MAX_ENTRIES,
      maxDepth: value.maxDepth ?? DEFAULT_MAX_DEPTH,
      maxSingleFileBytes: value.maxSingleFileBytes
        ?? DEFAULT_MAX_SINGLE_FILE_BYTES,
      maxTotalBytes: value.maxTotalBytes ?? DEFAULT_MAX_TOTAL_BYTES,
    }
    for (const [name, limit] of Object.entries(limits)) {
      if (!Number.isSafeInteger(limit) || limit <= 0) {
        throw new DataStorageValidationError(`${name} must be a positive integer.`)
      }
    }
    return Object.freeze(limits)
  }

  async #initializeLocked(
    pendingAuthority: 'previous' | 'active' = 'previous',
  ): Promise<DataStorageInitializeResult> {
    await mkdir(this.#userDataDirectory, { recursive: true })
    await assertUnlinkedDirectory(this.#userDataDirectory)
    const stored = await readBoundedJson(this.#bootstrapPath)
    if (stored !== null) {
      const document = parseBootstrapDocument(stored)
      let retentionWarning: string | null = null
      if (document.retainedRoots.length > 0) {
        retentionWarning = await this.#inspectRetainedRoots(document)
      }
      await this.#validateBootstrapAuthority(document, pendingAuthority)
      return Object.freeze({
        state: stateFromBootstrap(document),
        legacyWorkspaceCopied: false,
        warning: retentionWarning,
      })
    }

    const existingDefault = await pathMetadata(this.#defaultDataRoot)
    if (existingDefault !== null) {
      await assertUnlinkedDirectory(this.#defaultDataRoot)
      const layout = await this.#readLayout(this.#defaultDataRoot).catch(() => null)
      if (layout !== null) {
        const adopted: BootstrapDocument = Object.freeze({
          schemaVersion: DATA_STORAGE_SCHEMA_VERSION,
          revision: 0,
          activeDataRoot: this.#defaultDataRoot,
          rootId: layout.rootId,
          pendingMove: null,
          retainedRoots: Object.freeze([]),
        })
        await this.#writeBootstrap(adopted)
        return Object.freeze({
          state: stateFromBootstrap(adopted),
          legacyWorkspaceCopied: false,
          warning: null,
        })
      }
      if ((await readdir(this.#defaultDataRoot)).length !== 0) {
        throw new DataStorageValidationError(
          'The default data directory contains unmanaged files.',
        )
      }
    }

    const stage = path.join(
      this.#userDataDirectory,
      `.data.elysia-initialize-${randomUUID()}`,
    )
    const rootId = randomUUID()
    let stageExists = false
    let copiedLegacyWorkspace = false
    try {
      await mkdir(stage)
      stageExists = true
      const legacyWorkspace = this.#legacyProjectRoot === null
        ? null
        : path.join(this.#legacyProjectRoot, 'workspace')
      if (legacyWorkspace !== null && await pathMetadata(legacyWorkspace) !== null) {
        if (pathsOverlap(legacyWorkspace, stage)) {
          throw new DataStorageValidationError('Legacy and new data roots overlap.')
        }
        const legacySnapshot = await scanTree(legacyWorkspace, this.#limits, {
          hashFiles: true,
          dataRoot: false,
        })
        if (legacySnapshot.blockedEntries > 0 || legacySnapshot.truncated) {
          throw new DataStorageValidationError(
            'Legacy workspace cannot be copied safely.',
          )
        }
        const stagedWorkspace = path.join(stage, 'workspace')
        await mkdir(stagedWorkspace)
        await copySnapshot(legacyWorkspace, stagedWorkspace, legacySnapshot)
        const copiedSnapshot = await scanTree(stagedWorkspace, this.#limits, {
          hashFiles: true,
          dataRoot: false,
        })
        if (
          copiedSnapshot.blockedEntries > 0
          || copiedSnapshot.truncated
          || snapshotFingerprint(copiedSnapshot) !== snapshotFingerprint(legacySnapshot)
        ) {
          throw new DataStorageStorageError(
            'Legacy workspace copy failed SHA-256 verification.',
          )
        }
        copiedLegacyWorkspace = true
      }
      await writeAtomicJson(path.join(stage, DATA_STORAGE_LAYOUT_FILE_NAME), {
        schemaVersion: DATA_STORAGE_SCHEMA_VERSION,
        rootId,
        createdAt: requireTimestamp(this.#now),
      } satisfies LayoutDocument)
      if (existingDefault !== null) {
        await rmdir(this.#defaultDataRoot)
      }
      await rename(stage, this.#defaultDataRoot)
      stageExists = false
      const initial: BootstrapDocument = Object.freeze({
        schemaVersion: DATA_STORAGE_SCHEMA_VERSION,
        revision: 0,
        activeDataRoot: this.#defaultDataRoot,
        rootId,
        pendingMove: null,
        retainedRoots: Object.freeze([]),
      })
      await this.#writeBootstrap(initial)
      return Object.freeze({
        state: stateFromBootstrap(initial),
        legacyWorkspaceCopied: copiedLegacyWorkspace,
        warning: null,
      })
    } catch (error) {
      if (stageExists) {
        await rm(stage, { force: true, recursive: true }).catch(() => {})
      }
      if (
        error instanceof DataStorageValidationError
        || error instanceof DataStorageConflictError
        || error instanceof DataStorageStorageError
      ) {
        throw error
      }
      throw new DataStorageStorageError('Data storage could not be initialized.')
    }
  }

  async #readBootstrapRequired(
    pendingAuthority: 'previous' | 'active' = 'previous',
  ): Promise<BootstrapDocument> {
    const value = await readBoundedJson(this.#bootstrapPath)
    if (value === null) {
      throw new DataStorageStorageError('Data storage bootstrap is missing.')
    }
    const document = parseBootstrapDocument(value)
    await this.#validateBootstrapAuthority(document, pendingAuthority)
    return document
  }

  async #writeBootstrap(document: BootstrapDocument): Promise<void> {
    await writeAtomicJson(this.#bootstrapPath, document)
  }

  async #readLayout(root: string): Promise<LayoutDocument | null> {
    const value = await readBoundedJson(
      path.join(root, DATA_STORAGE_LAYOUT_FILE_NAME),
    )
    return value === null ? null : parseLayoutDocument(value)
  }

  async #validateManagedRoot(root: string, rootId: string): Promise<void> {
    await assertUnlinkedDirectory(root)
    const layout = await this.#readLayout(root)
    if (layout === null || layout.rootId !== rootId) {
      throw new DataStorageValidationError('Managed data root identity is invalid.')
    }
  }

  async #validateBootstrapAuthority(
    document: BootstrapDocument,
    pendingAuthority: 'previous' | 'active',
  ): Promise<void> {
    if (document.pendingMove === null || pendingAuthority === 'active') {
      await this.#validateManagedRoot(document.activeDataRoot, document.rootId)
      return
    }
    // A prepared destination may disappear or be corrupted before the next
    // launch (for example, a removable volume was unplugged). The old root is
    // still authoritative until Backend readiness commits the move, so startup
    // validates that rollback anchor and lets Main restore its pointer without
    // opening the unavailable destination.
    await this.#validateManagedRoot(
      document.pendingMove.previousRoot,
      document.pendingMove.previousRootId,
    )
  }

  async #validateEmptyDestination(
    destinationValue: string,
    activeRoot: string,
  ): Promise<string> {
    const destination = requireAbsolutePath(
      destinationValue,
      'Destination data directory',
    )
    if (pathsEqual(destination, path.parse(destination).root)) {
      throw new DataStorageValidationError('Filesystem roots cannot store app data.')
    }
    if (pathsOverlap(destination, activeRoot)) {
      throw new DataStorageValidationError('Data move roots cannot overlap.')
    }
    if (
      this.#legacyProjectRoot !== null
      && pathsOverlap(destination, this.#legacyProjectRoot)
    ) {
      // Main passes the executable/resource root as the legacy import source.
      // Keeping the movable root outside that tree prevents an upgrade from
      // replacing private data and prevents data from enclosing program files.
      throw new DataStorageValidationError(
        'Data storage cannot overlap application resources.',
      )
    }
    await assertUnlinkedDirectory(destination)
    if ((await readdir(destination)).length !== 0) {
      throw new DataStorageValidationError(
        'Destination data directory must be empty.',
      )
    }
    return destination
  }

  #requirePendingTransaction(
    current: BootstrapDocument,
    transaction: DataStorageMoveTransaction,
  ): void {
    if (
      current.revision !== transaction.switchedRevision
      || !statesMatchTransaction(current.pendingMove, transaction)
    ) {
      throw new DataStorageConflictError('Data move transaction is stale.')
    }
  }

  #withoutPendingMove(current: BootstrapDocument): BootstrapDocument {
    const revision = current.revision + 1
    if (!Number.isSafeInteger(revision)) {
      throw new DataStorageStorageError('Data storage revision is exhausted.')
    }
    return Object.freeze({
      ...current,
      revision,
      pendingMove: null,
    })
  }

  #incrementRevision(current: BootstrapDocument): BootstrapDocument {
    const revision = current.revision + 1
    if (!Number.isSafeInteger(revision)) {
      throw new DataStorageStorageError('Data storage revision is exhausted.')
    }
    return Object.freeze({ ...current, revision })
  }

  #retirementPath(root: string): string {
    return path.join(
      path.dirname(root),
      `.${path.basename(root)}.elysia-retire-${randomUUID()}`,
    )
  }

  #withRetainedCandidates(
    document: BootstrapDocument,
    candidates: readonly string[],
  ): BootstrapDocument {
    const retainedRoots = [...document.retainedRoots]
    for (const candidate of candidates) {
      if (!retainedRoots.some((existing) => pathsEqual(existing, candidate))) {
        retainedRoots.push(candidate)
      }
    }
    return Object.freeze({
      ...document,
      retainedRoots: parseRetainedRoots(retainedRoots),
    })
  }

  async #inspectRetainedRoots(document: BootstrapDocument): Promise<string | null> {
    let unavailable = false
    for (const retainedRoot of document.retainedRoots) {
      try {
        if (await pathMetadata(retainedRoot) === null) {
          unavailable = true
        }
      } catch {
        unavailable = true
      }
    }
    // Missing may mean a removable or network volume is temporarily offline.
    // Only the exact transaction that just completed deletion may erase a
    // journal entry; startup keeps every path discoverable for later reconnect.
    return unavailable
      ? 'One or more recorded recovery copies are currently unavailable.'
      : null
  }

  async #finalizeRetirementJournal(
    document: BootstrapDocument,
    candidates: readonly string[],
  ): Promise<{
    readonly document: BootstrapDocument
    readonly warning: string | null
  }> {
    const preserved = document.retainedRoots.filter((retained) => (
      !candidates.some((candidate) => pathsEqual(candidate, retained))
    ))
    const stillPresent: string[] = []
    let inspectionWarning: string | null = null
    for (const candidate of candidates) {
      try {
        if (await pathMetadata(candidate) !== null) {
          stillPresent.push(candidate)
        }
      } catch {
        // A path that cannot be inspected must remain discoverable; dropping it
        // would turn a possibly intact private-data copy into an orphan.
        stillPresent.push(candidate)
        inspectionWarning = (
          'A recovery-copy path could not be inspected and remains recorded.'
        )
      }
    }
    const finalized = Object.freeze({
      ...document,
      retainedRoots: parseRetainedRoots([...preserved, ...stillPresent]),
    })
    const unchanged = finalized.retainedRoots.length === document.retainedRoots.length
      && finalized.retainedRoots.every((root, index) => (
        pathsEqual(root, document.retainedRoots[index]!)
      ))
    if (unchanged) {
      return { document: finalized, warning: inspectionWarning }
    }
    try {
      await this.#writeBootstrap(finalized)
      return { document: finalized, warning: inspectionWarning }
    } catch {
      // The pre-deletion journal already contains both possible paths. Return
      // the accurate in-memory view and retain that conservative on-disk record
      // for reconciliation on the next launch.
      return {
        document: finalized,
        warning: joinWarnings(
          inspectionWarning,
          'Recovery-copy records could not be finalized and will be checked again next launch.',
        ),
      }
    }
  }

  async #deleteManagedRootConservatively(
    root: string,
    expectedRootId: string,
    expectedFingerprint: string,
    plannedIsolationRoot?: string,
  ): Promise<{
    readonly retained: boolean
    readonly retainedPath: string | null
    readonly warning: string | null
  }> {
    let isolatedRoot: string | null = null
    try {
      const metadata = await pathMetadata(root)
      if (metadata === null) {
        return { retained: false, retainedPath: null, warning: null }
      }
      await this.#validateManagedRoot(root, expectedRootId)
      const snapshot = await scanTree(root, this.#limits, {
        hashFiles: true,
        dataRoot: true,
      })
      if (
        snapshot.blockedEntries > 0
        || snapshot.truncated
        || snapshot.unknownEntries > 0
        || snapshotFingerprint(snapshot) !== expectedFingerprint
      ) {
        return {
          retained: true,
          retainedPath: root,
          warning: 'A managed root changed or contains unknown or blocked entries and was retained.',
        }
      }
      isolatedRoot = plannedIsolationRoot ?? this.#retirementPath(root)
      // Renaming the whole root first removes it from the path that any late
      // writer selected. A second full inventory below detects writes through
      // already-open handles before a single file is unlinked.
      await rename(root, isolatedRoot)
      const isolatedSnapshot = await scanTree(isolatedRoot, this.#limits, {
        hashFiles: true,
        dataRoot: true,
      })
      if (
        isolatedSnapshot.blockedEntries > 0
        || isolatedSnapshot.truncated
        || isolatedSnapshot.unknownEntries > 0
        || snapshotFingerprint(isolatedSnapshot) !== expectedFingerprint
      ) {
        return {
          retained: true,
          retainedPath: isolatedRoot,
          warning: 'A managed root changed while being retired and was retained.',
        }
      }
      await this.#removeKnownSnapshot(
        isolatedRoot,
        isolatedSnapshot,
        expectedRootId,
      )
      if (await pathMetadata(root) !== null) {
        return {
          retained: true,
          retainedPath: root,
          warning: 'New entries appeared at a retired data path and were retained.',
        }
      }
      return { retained: false, retainedPath: null, warning: null }
    } catch {
      const isolatedMetadata = isolatedRoot === null
        ? null
        : await pathMetadata(isolatedRoot).catch(() => null)
      const originalMetadata = await pathMetadata(root).catch(() => null)
      return {
        retained: true,
        retainedPath: isolatedMetadata !== null
          ? isolatedRoot
          : (originalMetadata === null ? null : root),
        warning: 'The previous managed root could not be verified or removed and was retained.',
      }
    }
  }

  async #removeKnownSnapshot(
    root: string,
    snapshot: TreeSnapshot,
    expectedRootId: string,
  ): Promise<void> {
    // Delete only the exact regular files that were reviewed. Directories use
    // rmdir rather than recursive removal, so a late or previously unseen entry
    // makes deletion fail and remains on disk instead of being swept away.
    for (const entry of snapshot.entries) {
      if (entry.kind !== 'file') {
        continue
      }
      const filePath = path.join(root, entry.relativePath)
      const metadata = await lstat(filePath)
      if (
        metadata.isSymbolicLink()
        || !metadata.isFile()
        || metadata.size !== entry.size
      ) {
        throw new DataStorageConflictError(
          'Managed data changed before conservative removal.',
        )
      }
      const digest = await hashRegularFile(
        filePath,
        entry.size,
        this.#limits.maxSingleFileBytes,
      )
      if (digest !== entry.sha256) {
        throw new DataStorageConflictError(
          'Managed data changed before conservative removal.',
        )
      }
    }
    for (const entry of snapshot.entries) {
      if (entry.kind === 'file') {
        await unlink(path.join(root, entry.relativePath))
      }
    }
    const directories = snapshot.entries
      .filter((entry) => entry.kind === 'directory')
      .sort((left, right) => (
        right.relativePath.split(path.sep).length
        - left.relativePath.split(path.sep).length
      ))
    for (const entry of directories) {
      await rmdir(path.join(root, entry.relativePath))
    }
    const layout = await this.#readLayout(root)
    if (layout === null || layout.rootId !== expectedRootId) {
      throw new DataStorageConflictError(
        'Managed root identity changed before conservative removal.',
      )
    }
    await unlink(path.join(root, DATA_STORAGE_LAYOUT_FILE_NAME))
    await rmdir(root)
  }

  #cleanupDirectories(
    root: string,
    category: 'audio' | 'cache' | 'logs',
  ): readonly string[] {
    return Object.freeze([
      path.join(root, category),
      path.join(root, 'workspace', category),
    ])
  }

  async #removeAllowlistedDirectory(root: string, candidate: string): Promise<void> {
    if (!isPathInside(candidate, root)) {
      throw new DataStorageValidationError('Cleanup path escaped the data root.')
    }
    const metadata = await pathMetadata(candidate)
    if (metadata === null) {
      return
    }
    if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
      throw new DataStorageValidationError('Cleanup directory is invalid.')
    }
    const canonical = await realpath(candidate)
    if (!pathsEqual(canonical, candidate) || !isPathInside(canonical, root)) {
      throw new DataStorageValidationError(
        'Cleanup cannot follow links or junctions.',
      )
    }
    // Candidate paths come exclusively from the closed category allowlist. The
    // immediately preceding no-link scan and canonical containment check keep
    // recursive removal away from durable and external locations.
    await rm(candidate, { recursive: true })
  }
}
