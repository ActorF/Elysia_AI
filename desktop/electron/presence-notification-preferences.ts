/**
 * Persist bounded Electron-only notification intent and reminder cadence.
 *
 * The strict document lives below Electron's user-data directory. Renderer
 * state excludes the private last-handled timestamp, which exists only to
 * prevent reminder bursts across restarts and never records engagement.
 */

import { randomUUID } from 'node:crypto'
import {
  mkdir,
  open,
  rename,
  rm,
  type FileHandle,
} from 'node:fs/promises'
import path from 'node:path'

import {
  type PresenceNotificationRuntime,
  type PresenceNotificationState,
  type PresenceReminderFrequency,
  isPresenceReminderFrequency,
  parseUpdatePresenceNotificationRequest,
} from './presence-notification-contracts.js'

/** Current on-disk schema for Main-owned notification preferences. */
export const PRESENCE_NOTIFICATION_PREFERENCES_SCHEMA_VERSION = 1

const PRESENCE_NOTIFICATION_MAX_PREFERENCE_BYTES = 16 * 1024
const PRESENCE_NOTIFICATION_LOAD_WARNING = (
  'Notification preferences could not be loaded safely. All notifications remain off.'
)
const PRESENCE_NOTIFICATION_RUNTIMES: ReadonlySet<string> = new Set([
  'available',
  'unsupported',
  'failed',
])

interface StoredPresenceNotificationDocument {
  readonly schemaVersion: typeof PRESENCE_NOTIFICATION_PREFERENCES_SCHEMA_VERSION
  readonly revision: number
  readonly updatedAt: string | null
  readonly completionNotifications: boolean
  readonly reminderFrequency: PresenceReminderFrequency
  readonly lastReminderHandledAt: string | null
}

interface ReadPresenceNotificationDocument {
  readonly document: StoredPresenceNotificationDocument
  readonly warning: string | null
}

/** Main-only result with public state plus the private cadence anchor. */
export interface LoadedPresenceNotificationPreferences {
  readonly state: PresenceNotificationState
  readonly lastReminderHandledAt: string | null
}

/** Replace operation used by the atomic writer and deterministic tests. */
export type PresenceNotificationReplaceFile = (
  sourcePath: string,
  targetPath: string,
) => Promise<void>

/** Inject deterministic time and atomic-replace behavior into persistence. */
export interface PresenceNotificationPreferencesRepositoryOptions {
  /** Produce canonical timestamps for successful preference and cadence writes. */
  readonly now?: () => Date
  /** Replace the destination after its complete temporary file is synchronized. */
  readonly replaceFile?: PresenceNotificationReplaceFile
}

/** Report an optimistic-concurrency mismatch without exposing native paths. */
export class PresenceNotificationPreferencesConflictError extends Error {}

/** Report an invalid preference document or repository construction request. */
export class PresenceNotificationPreferencesValidationError extends Error {}

/** Report a persistence failure without exposing native paths to renderers. */
export class PresenceNotificationPreferencesStorageError extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactFields(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Reflect.ownKeys(value)
  return actual.length === expected.length
    && expected.every((field) => actual.includes(field))
}

function isCanonicalTimestamp(value: unknown): value is string | null {
  if (value === null) {
    return true
  }
  if (typeof value !== 'string') {
    return false
  }
  const parsed = new Date(value)
  return Number.isFinite(parsed.getTime()) && parsed.toISOString() === value
}

function parseStoredDocument(
  value: unknown,
): StoredPresenceNotificationDocument {
  if (
    !isRecord(value)
    || !hasExactFields(value, [
      'schemaVersion',
      'revision',
      'updatedAt',
      'completionNotifications',
      'reminderFrequency',
      'lastReminderHandledAt',
    ])
    || value.schemaVersion !== PRESENCE_NOTIFICATION_PREFERENCES_SCHEMA_VERSION
    || !Number.isSafeInteger(value.revision)
    || (value.revision as number) < 0
    || !isCanonicalTimestamp(value.updatedAt)
    || !isCanonicalTimestamp(value.lastReminderHandledAt)
    || !isPresenceReminderFrequency(value.reminderFrequency)
    || typeof value.completionNotifications !== 'boolean'
    || ((value.revision === 0) !== (value.updatedAt === null))
    || (
      value.reminderFrequency === 'off'
        ? value.lastReminderHandledAt !== null
        : value.lastReminderHandledAt === null
    )
  ) {
    throw new PresenceNotificationPreferencesValidationError(
      'Presence notification preference document is invalid.',
    )
  }
  return Object.freeze({
    schemaVersion: PRESENCE_NOTIFICATION_PREFERENCES_SCHEMA_VERSION,
    revision: value.revision as number,
    updatedAt: value.updatedAt,
    completionNotifications: value.completionNotifications,
    reminderFrequency: value.reminderFrequency,
    lastReminderHandledAt: value.lastReminderHandledAt,
  })
}

function defaultStoredDocument(): StoredPresenceNotificationDocument {
  return Object.freeze({
    schemaVersion: PRESENCE_NOTIFICATION_PREFERENCES_SCHEMA_VERSION,
    revision: 0,
    updatedAt: null,
    completionNotifications: false,
    reminderFrequency: 'off',
    lastReminderHandledAt: null,
  })
}

function requireRuntime(
  runtime: PresenceNotificationRuntime,
): PresenceNotificationRuntime {
  if (!PRESENCE_NOTIFICATION_RUNTIMES.has(runtime)) {
    throw new PresenceNotificationPreferencesValidationError(
      'Presence notification runtime is invalid.',
    )
  }
  return runtime
}

function toLoadedPreferences(
  value: ReadPresenceNotificationDocument,
  runtime: PresenceNotificationRuntime,
): LoadedPresenceNotificationPreferences {
  const document = value.document
  return Object.freeze({
    state: Object.freeze({
      revision: document.revision,
      updatedAt: document.updatedAt,
      completionNotifications: document.completionNotifications,
      reminderFrequency: document.reminderFrequency,
      runtime: requireRuntime(runtime),
      warning: value.warning,
    }),
    lastReminderHandledAt: document.lastReminderHandledAt,
  })
}

async function readBoundedUtf8(filePath: string): Promise<string | null> {
  let handle: FileHandle
  try {
    handle = await open(filePath, 'r')
  } catch (error) {
    if (isRecord(error) && error.code === 'ENOENT') {
      return null
    }
    throw new PresenceNotificationPreferencesStorageError(
      'Notification preferences could not be read.',
    )
  }
  try {
    const metadata = await handle.stat()
    if (
      !metadata.isFile()
      || metadata.size > PRESENCE_NOTIFICATION_MAX_PREFERENCE_BYTES
    ) {
      throw new PresenceNotificationPreferencesValidationError(
        'Presence notification preference document is invalid.',
      )
    }
    const buffer = Buffer.alloc(PRESENCE_NOTIFICATION_MAX_PREFERENCE_BYTES + 1)
    let offset = 0
    // Bounded reads also detect files that grow after stat without allocating
    // according to an untrusted on-disk length.
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
    if (offset > PRESENCE_NOTIFICATION_MAX_PREFERENCE_BYTES) {
      throw new PresenceNotificationPreferencesValidationError(
        'Presence notification preference document is invalid.',
      )
    }
    return buffer.subarray(0, offset).toString('utf8')
  } catch (error) {
    if (
      error instanceof PresenceNotificationPreferencesValidationError
      || error instanceof PresenceNotificationPreferencesStorageError
    ) {
      throw error
    }
    throw new PresenceNotificationPreferencesStorageError(
      'Notification preferences could not be read.',
    )
  } finally {
    await handle.close().catch(() => {})
  }
}

async function readStoredDocument(
  filePath: string,
): Promise<StoredPresenceNotificationDocument | null> {
  const content = await readBoundedUtf8(filePath)
  if (content === null) {
    return null
  }
  let value: unknown
  try {
    value = JSON.parse(content) as unknown
  } catch {
    throw new PresenceNotificationPreferencesValidationError(
      'Presence notification preference document is invalid.',
    )
  }
  return parseStoredDocument(value)
}

async function writeStoredDocument(
  filePath: string,
  document: StoredPresenceNotificationDocument,
  replaceFile: PresenceNotificationReplaceFile,
): Promise<void> {
  const directory = path.dirname(filePath)
  const temporaryPath = path.join(
    directory,
    `.${path.basename(filePath)}.${process.pid}.${randomUUID()}.tmp`,
  )
  const payload = `${JSON.stringify(document, null, 2)}\n`
  if (
    Buffer.byteLength(payload, 'utf8')
    > PRESENCE_NOTIFICATION_MAX_PREFERENCE_BYTES
  ) {
    throw new PresenceNotificationPreferencesStorageError(
      'Notification preferences could not be saved.',
    )
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
    await replaceFile(temporaryPath, filePath)
    temporaryCreated = false
  } catch {
    if (temporaryCreated) {
      await rm(temporaryPath, { force: true }).catch(() => {})
    }
    throw new PresenceNotificationPreferencesStorageError(
      'Notification preferences could not be saved.',
    )
  }
}

const pathLocks = new Map<string, Promise<void>>()

async function withPathLock<Result>(
  filePath: string,
  operation: () => Promise<Result>,
): Promise<Result> {
  const previous = pathLocks.get(filePath) ?? Promise.resolve()
  let release: (() => void) | undefined
  const current = new Promise<void>((resolve) => {
    release = resolve
  })
  pathLocks.set(filePath, current)
  await previous.catch(() => {})
  try {
    return await operation()
  } finally {
    release?.()
    if (pathLocks.get(filePath) === current) {
      pathLocks.delete(filePath)
    }
  }
}

function safeTimestamp(now: () => Date): string {
  try {
    const timestamp = now()
    if (!(timestamp instanceof Date) || !Number.isFinite(timestamp.getTime())) {
      throw new Error('invalid timestamp')
    }
    return timestamp.toISOString()
  } catch {
    throw new PresenceNotificationPreferencesStorageError(
      'Notification preferences could not be saved.',
    )
  }
}

/** Persist strict notification intent independently from the Python Backend. */
export class PresenceNotificationPreferencesRepository {
  readonly #filePath: string
  readonly #now: () => Date
  readonly #replaceFile: PresenceNotificationReplaceFile

  /** Create one repository for an absolute Main-owned user-data path. */
  constructor(
    filePath: string,
    options: PresenceNotificationPreferencesRepositoryOptions = {},
  ) {
    if (typeof filePath !== 'string' || !path.isAbsolute(filePath)) {
      throw new PresenceNotificationPreferencesValidationError(
        'Notification preference path must be absolute.',
      )
    }
    this.#filePath = path.resolve(filePath)
    this.#now = options.now ?? (() => new Date())
    this.#replaceFile = options.replaceFile ?? rename
  }

  /** Load renderer-safe state and the Main-private cadence anchor, failing off. */
  async load(
    runtime: PresenceNotificationRuntime = 'available',
  ): Promise<LoadedPresenceNotificationPreferences> {
    requireRuntime(runtime)
    try {
      const document = await readStoredDocument(this.#filePath)
      return toLoadedPreferences({
        document: document ?? defaultStoredDocument(),
        warning: null,
      }, runtime)
    } catch {
      return toLoadedPreferences({
        document: defaultStoredDocument(),
        warning: PRESENCE_NOTIFICATION_LOAD_WARNING,
      }, runtime)
    }
  }

  /** Validate and atomically replace both revision-aware notification choices. */
  async update(
    requestValue: unknown,
    runtime: PresenceNotificationRuntime = 'available',
  ): Promise<LoadedPresenceNotificationPreferences> {
    const request = parseUpdatePresenceNotificationRequest(requestValue)
    requireRuntime(runtime)
    return withPathLock(this.#filePath, async () => {
      let current: ReadPresenceNotificationDocument
      try {
        current = {
          document: await readStoredDocument(this.#filePath)
            ?? defaultStoredDocument(),
          warning: null,
        }
      } catch (error) {
        if (error instanceof PresenceNotificationPreferencesValidationError) {
          current = {
            document: defaultStoredDocument(),
            warning: PRESENCE_NOTIFICATION_LOAD_WARNING,
          }
        } else {
          throw error
        }
      }
      if (request.expectedRevision !== current.document.revision) {
        throw new PresenceNotificationPreferencesConflictError(
          'Notification preferences changed elsewhere. Reload before saving.',
        )
      }
      if (
        request.completionNotifications
          === current.document.completionNotifications
        && request.reminderFrequency === current.document.reminderFrequency
      ) {
        return toLoadedPreferences(current, runtime)
      }
      if (current.document.revision === Number.MAX_SAFE_INTEGER) {
        throw new PresenceNotificationPreferencesStorageError(
          'Notification preferences could not be saved.',
        )
      }
      const timestamp = safeTimestamp(this.#now)
      const frequencyChanged = request.reminderFrequency
        !== current.document.reminderFrequency
      const next = Object.freeze({
        ...current.document,
        revision: current.document.revision + 1,
        updatedAt: timestamp,
        completionNotifications: request.completionNotifications,
        reminderFrequency: request.reminderFrequency,
        lastReminderHandledAt: frequencyChanged
          ? request.reminderFrequency === 'off' ? null : timestamp
          : current.document.lastReminderHandledAt,
      })
      await writeStoredDocument(this.#filePath, next, this.#replaceFile)
      return toLoadedPreferences({ document: next, warning: null }, runtime)
    })
  }

  /**
   * Record one due reminder cycle without changing renderer CAS revision.
   *
   * Expected revision, frequency, and old anchor prevent a stale or duplicate
   * timer from consuming a newly changed schedule. Main records even a
   * foreground- or busy-suppressed cycle so leaving the active window cannot
   * cause a delayed catch-up notice.
   */
  async recordReminderHandled(
    expectedRevision: number,
    expectedFrequency: Exclude<PresenceReminderFrequency, 'off'>,
    expectedLastReminderHandledAt: string,
    runtime: PresenceNotificationRuntime = 'available',
  ): Promise<LoadedPresenceNotificationPreferences> {
    if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 0) {
      throw new PresenceNotificationPreferencesValidationError(
        'Notification preference revision is invalid.',
      )
    }
    if (expectedFrequency !== 'daily' && expectedFrequency !== 'weekly') {
      throw new PresenceNotificationPreferencesValidationError(
        'Presence reminder frequency is invalid.',
      )
    }
    if (
      typeof expectedLastReminderHandledAt !== 'string'
      || !isCanonicalTimestamp(expectedLastReminderHandledAt)
    ) {
      throw new PresenceNotificationPreferencesValidationError(
        'Presence reminder cadence anchor is invalid.',
      )
    }
    requireRuntime(runtime)
    return withPathLock(this.#filePath, async () => {
      let current: StoredPresenceNotificationDocument
      try {
        current = await readStoredDocument(this.#filePath)
          ?? defaultStoredDocument()
      } catch {
        throw new PresenceNotificationPreferencesStorageError(
          'Notification preferences could not be saved.',
        )
      }
      if (
        current.revision !== expectedRevision
        || current.reminderFrequency !== expectedFrequency
        || current.lastReminderHandledAt !== expectedLastReminderHandledAt
      ) {
        throw new PresenceNotificationPreferencesConflictError(
          'Notification preferences changed before the reminder was delivered.',
        )
      }
      const next = Object.freeze({
        ...current,
        lastReminderHandledAt: safeTimestamp(this.#now),
      })
      await writeStoredDocument(this.#filePath, next, this.#replaceFile)
      return toLoadedPreferences({ document: next, warning: null }, runtime)
    })
  }
}
