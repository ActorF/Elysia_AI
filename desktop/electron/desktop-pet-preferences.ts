/**
 * Persist bounded Electron-only desktop-pet program intent.
 *
 * The repository owns a small strict JSON document below Electron's user-data
 * directory. Renderer-safe state intentionally excludes native paths and
 * process identifiers. Legacy embedded-window placement is discarded during
 * migration because the external companion program owns its own position.
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
  type DesktopPetLibraryStatus,
  type DesktopPetMode,
  type DesktopPetModelSummary,
  type DesktopPetRuntimeState,
  type DesktopPetState,
  isDesktopPetModelId,
  parseUpdateDesktopPetRequest,
} from './desktop-pet-contracts.js'

/** Current on-disk schema for the Electron-owned desktop-pet preference. */
export const DESKTOP_PET_PREFERENCES_SCHEMA_VERSION = 3

const DESKTOP_PET_MAX_PREFERENCE_BYTES = 16 * 1024
const DESKTOP_PET_MAX_LIBRARY_PATH_CODE_UNITS = 32_767
const DESKTOP_PET_LOAD_WARNING = (
  'Desktop pet preferences could not be loaded safely. The pet remains off.'
)
const DESKTOP_PET_MIGRATION_WARNING = (
  'The previous bundled Desktop Pet was removed. Choose a local companion '
  + 'program folder before turning the pet on.'
)

const DESKTOP_PET_RUNTIME_STATES: ReadonlySet<string> = new Set([
  'absent',
  'loading',
  'visible',
  'failed',
])

const DESKTOP_PET_LIBRARY_STATES: ReadonlySet<string> = new Set([
  'not-configured',
  'scanning',
  'ready',
  'empty',
  'unavailable',
  'invalid',
])

interface StoredDesktopPetDocument {
  readonly schemaVersion: typeof DESKTOP_PET_PREFERENCES_SCHEMA_VERSION
  readonly revision: number
  readonly updatedAt: string | null
  readonly mode: DesktopPetMode
  readonly libraryPath: string | null
  readonly selectedModelId: string | null
}

interface ReadDesktopPetDocument {
  readonly document: StoredDesktopPetDocument
  readonly warning: string | null
}

/** Main-only result containing public state plus its private library path. */
export interface LoadedDesktopPetPreferences {
  readonly state: DesktopPetState
  /** Absolute external folder retained only by Electron Main. */
  readonly libraryPath: string | null
}

/** Renderer-safe library facts supplied after Main completes one bounded scan. */
export interface DesktopPetLibraryPresentation {
  readonly status: DesktopPetLibraryStatus
  readonly folderName: string | null
  readonly models: readonly DesktopPetModelSummary[]
}

/** Main-private replacement for the external program folder and selection. */
export interface UpdateDesktopPetLibraryRequest {
  readonly expectedRevision: number
  readonly libraryPath: string | null
  readonly selectedModelId: string | null
}

/** Replace operation used by the atomic writer and deterministic failure tests. */
export type DesktopPetReplaceFile = (
  sourcePath: string,
  targetPath: string,
) => Promise<void>

/** Inject deterministic time and atomic-replace behavior into the repository. */
export interface DesktopPetPreferencesRepositoryOptions {
  /** Produce the timestamp recorded after a successful mode change. */
  readonly now?: () => Date
  /** Replace the destination after the complete temporary file is synchronized. */
  readonly replaceFile?: DesktopPetReplaceFile
}

/** Report an optimistic-concurrency mismatch without exposing file details. */
export class DesktopPetPreferencesConflictError extends Error {}

/** Report an invalid Main-owned preference or repository construction request. */
export class DesktopPetPreferencesValidationError extends Error {}

/** Report a persistence failure without exposing a native path to renderers. */
export class DesktopPetPreferencesStorageError extends Error {}

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

function parseLibraryPath(value: unknown): string | null {
  if (value === null) {
    return null
  }
  if (
    typeof value !== 'string'
    || value.length === 0
    || value.length > DESKTOP_PET_MAX_LIBRARY_PATH_CODE_UNITS
    || /[\0\r\n]/u.test(value)
    || !path.isAbsolute(value)
  ) {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet program folder is invalid.',
    )
  }
  return path.resolve(value)
}

function parseSelectedModelId(value: unknown): string | null {
  if (value === null) {
    return null
  }
  if (!isDesktopPetModelId(value)) {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet program selection is invalid.',
    )
  }
  return value
}

function libraryFolderName(libraryPath: string): string {
  // The path stays Main-private, while its final component is presentation.
  // Remove control and bidi-format characters so a local folder cannot forge
  // the direction or neighboring labels in Settings.
  const basename = Array.from(
    path.basename(libraryPath).replace(/[\p{Cc}\p{Cf}]/gu, '').trim(),
  ).slice(0, 255).join('')
  // Filesystem roots have no basename. A neutral label keeps the renderer from
  // receiving the absolute root while still producing a usable Settings row.
  return basename.length === 0 ? 'Selected folder' : basename
}

function parseStoredDocument(value: unknown): ReadDesktopPetDocument {
  if (
    !isRecord(value)
    || !Number.isSafeInteger(value.revision)
    || (value.revision as number) < 0
    || !isCanonicalTimestamp(value.updatedAt)
  ) {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet preference document is invalid.',
    )
  }

  if (
    value.schemaVersion === 1
    && hasExactFields(value, [
      'schemaVersion',
      'revision',
      'updatedAt',
      'mode',
      'placement',
    ])
  ) {
    // The v1 model was bundled with the application. That asset and its native
    // window no longer exist, so migration drops placement and never turns on
    // an external program the user has not explicitly chosen.
    parseUpdateDesktopPetRequest({
      expectedRevision: value.revision,
      mode: value.mode,
    })
    return Object.freeze({
      document: Object.freeze({
        schemaVersion: DESKTOP_PET_PREFERENCES_SCHEMA_VERSION,
        revision: value.revision as number,
        updatedAt: value.updatedAt,
        mode: 'disabled',
        libraryPath: null,
        selectedModelId: null,
      }),
      warning: DESKTOP_PET_MIGRATION_WARNING,
    })
  }

  const isLegacyExternalDocument = value.schemaVersion === 2
    && hasExactFields(value, [
      'schemaVersion',
      'revision',
      'updatedAt',
      'mode',
      'placement',
      'libraryPath',
      'selectedModelId',
    ])
  const isCurrentDocument = (
    value.schemaVersion === DESKTOP_PET_PREFERENCES_SCHEMA_VERSION
    && hasExactFields(value, [
      'schemaVersion',
      'revision',
      'updatedAt',
      'mode',
      'libraryPath',
      'selectedModelId',
    ])
  )
  if (!isLegacyExternalDocument && !isCurrentDocument) {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet preference document is invalid.',
    )
  }
  const request = parseUpdateDesktopPetRequest({
    expectedRevision: value.revision,
    mode: value.mode,
    modelId: value.selectedModelId,
  })
  const libraryPath = parseLibraryPath(value.libraryPath)
  const selectedModelId = parseSelectedModelId(request.modelId)
  if (libraryPath === null && selectedModelId !== null) {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet program selection has no configured folder.',
    )
  }
  if (request.mode === 'visible' && selectedModelId === null) {
    throw new DesktopPetPreferencesValidationError(
      'Visible desktop pet preferences require a selected program.',
    )
  }
  return Object.freeze({
    document: Object.freeze({
      schemaVersion: DESKTOP_PET_PREFERENCES_SCHEMA_VERSION,
      revision: request.expectedRevision,
      updatedAt: value.updatedAt,
      mode: request.mode,
      libraryPath,
      selectedModelId,
    }),
    warning: null,
  })
}

function firstRunStoredDocument(): StoredDesktopPetDocument {
  return Object.freeze({
    schemaVersion: DESKTOP_PET_PREFERENCES_SCHEMA_VERSION,
    revision: 0,
    updatedAt: null,
    mode: 'disabled',
    libraryPath: null,
    selectedModelId: null,
  })
}

function failClosedStoredDocument(): StoredDesktopPetDocument {
  return Object.freeze({
    schemaVersion: DESKTOP_PET_PREFERENCES_SCHEMA_VERSION,
    revision: 0,
    updatedAt: null,
    mode: 'disabled',
    libraryPath: null,
    selectedModelId: null,
  })
}

function requireRuntimeState(
  runtime: DesktopPetRuntimeState,
): DesktopPetRuntimeState {
  if (!DESKTOP_PET_RUNTIME_STATES.has(runtime)) {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet runtime state is invalid.',
    )
  }
  return runtime
}

function toLoadedPreferences(
  value: ReadDesktopPetDocument,
  runtime: DesktopPetRuntimeState,
): LoadedDesktopPetPreferences {
  const document = value.document
  return Object.freeze({
    state: Object.freeze({
      revision: document.revision,
      updatedAt: document.updatedAt,
      mode: document.mode,
      runtime: requireRuntimeState(runtime),
      warning: value.warning,
      libraryStatus: document.libraryPath === null
        ? 'not-configured'
        : 'scanning',
      folderName: document.libraryPath === null
        ? null
        : libraryFolderName(document.libraryPath),
      models: Object.freeze([]),
      selectedModelId: document.selectedModelId,
    }),
    libraryPath: document.libraryPath,
  })
}

/**
 * Merge a bounded Main scan into public state without exposing its root path.
 *
 * The private preference object is retained so later persistence continues to
 * use the canonical absolute folder chosen through Electron's native dialog.
 */
export function presentDesktopPetLibrary(
  preferences: LoadedDesktopPetPreferences,
  presentation: DesktopPetLibraryPresentation,
): LoadedDesktopPetPreferences {
  if (!DESKTOP_PET_LIBRARY_STATES.has(presentation.status)) {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet program-library status is invalid.',
    )
  }
  if (
    presentation.folderName !== null
    && (
      presentation.folderName.length === 0
      || presentation.folderName.length > 255
      || /[\0\r\n\\/]/u.test(presentation.folderName)
    )
  ) {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet program-folder name is invalid.',
    )
  }
  const seen = new Set<string>()
  const models = presentation.models.map((model) => {
    if (
      !isDesktopPetModelId(model.id)
      || seen.has(model.id)
      || typeof model.displayName !== 'string'
      || model.displayName.length === 0
      || model.displayName.length > 200
      || /[\0\r\n]/u.test(model.displayName)
    ) {
      throw new DesktopPetPreferencesValidationError(
        'Desktop pet program summary is invalid.',
      )
    }
    seen.add(model.id)
    return Object.freeze({
      id: model.id,
      displayName: model.displayName,
    })
  })
  return Object.freeze({
    state: Object.freeze({
      ...preferences.state,
      libraryStatus: presentation.status,
      folderName: presentation.folderName,
      models: Object.freeze(models),
    }),
    libraryPath: preferences.libraryPath,
  })
}

async function readBoundedUtf8(filePath: string): Promise<string | null> {
  let handle: FileHandle
  try {
    handle = await open(filePath, 'r')
  } catch (error) {
    if (
      isRecord(error)
      && error.code === 'ENOENT'
    ) {
      return null
    }
    throw new DesktopPetPreferencesStorageError(
      'Desktop pet preferences could not be read.',
    )
  }

  try {
    const metadata = await handle.stat()
    if (!metadata.isFile() || metadata.size > DESKTOP_PET_MAX_PREFERENCE_BYTES) {
      throw new DesktopPetPreferencesValidationError(
        'Desktop pet preference document is invalid.',
      )
    }
    const buffer = Buffer.alloc(DESKTOP_PET_MAX_PREFERENCE_BYTES + 1)
    let offset = 0
    // A bounded loop also detects a file that grows after stat without ever
    // allocating according to untrusted on-disk length.
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
    if (offset > DESKTOP_PET_MAX_PREFERENCE_BYTES) {
      throw new DesktopPetPreferencesValidationError(
        'Desktop pet preference document is invalid.',
      )
    }
    return buffer.subarray(0, offset).toString('utf8')
  } catch (error) {
    if (
      error instanceof DesktopPetPreferencesValidationError
      || error instanceof DesktopPetPreferencesStorageError
    ) {
      throw error
    }
    throw new DesktopPetPreferencesStorageError(
      'Desktop pet preferences could not be read.',
    )
  } finally {
    await handle.close().catch(() => {})
  }
}

async function readStoredDocument(
  filePath: string,
): Promise<ReadDesktopPetDocument | null> {
  const content = await readBoundedUtf8(filePath)
  if (content === null) {
    return null
  }
  let value: unknown
  try {
    value = JSON.parse(content) as unknown
  } catch {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet preference document is invalid.',
    )
  }
  return parseStoredDocument(value)
}

async function writeStoredDocument(
  filePath: string,
  document: StoredDesktopPetDocument,
  replaceFile: DesktopPetReplaceFile,
): Promise<void> {
  const directory = path.dirname(filePath)
  const temporaryPath = path.join(
    directory,
    `.${path.basename(filePath)}.${process.pid}.${randomUUID()}.tmp`,
  )
  const payload = `${JSON.stringify(document, null, 2)}\n`
  if (Buffer.byteLength(payload, 'utf8') > DESKTOP_PET_MAX_PREFERENCE_BYTES) {
    throw new DesktopPetPreferencesStorageError(
      'Desktop pet preferences could not be saved.',
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
    throw new DesktopPetPreferencesStorageError(
      'Desktop pet preferences could not be saved.',
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
    throw new DesktopPetPreferencesStorageError(
      'Desktop pet preferences could not be saved.',
    )
  }
}

/** Persist strict desktop-pet intent independently from the Python Backend. */
export class DesktopPetPreferencesRepository {
  readonly #filePath: string
  readonly #now: () => Date
  readonly #replaceFile: DesktopPetReplaceFile

  /** Create one repository for an absolute Main-owned user-data path. */
  constructor(
    filePath: string,
    options: DesktopPetPreferencesRepositoryOptions = {},
  ) {
    if (typeof filePath !== 'string' || !path.isAbsolute(filePath)) {
      throw new DesktopPetPreferencesValidationError(
        'Desktop pet preference path must be absolute.',
      )
    }
    this.#filePath = path.resolve(filePath)
    this.#now = options.now ?? (() => new Date())
    this.#replaceFile = options.replaceFile ?? rename
  }

  /** Load default-off intent, while malformed persisted state also fails closed. */
  async load(
    runtime: DesktopPetRuntimeState = 'absent',
  ): Promise<LoadedDesktopPetPreferences> {
    requireRuntimeState(runtime)
    try {
      const stored = await readStoredDocument(this.#filePath)
      return toLoadedPreferences(stored ?? {
        document: firstRunStoredDocument(),
        warning: null,
      }, runtime)
    } catch {
      return toLoadedPreferences({
        document: failClosedStoredDocument(),
        warning: DESKTOP_PET_LOAD_WARNING,
      }, runtime)
    }
  }

  /** Validate and atomically replace one revision-aware user mode. */
  async update(
    requestValue: unknown,
    runtime: DesktopPetRuntimeState = 'absent',
  ): Promise<LoadedDesktopPetPreferences> {
    const request = parseUpdateDesktopPetRequest(requestValue)
    requireRuntimeState(runtime)
    return withPathLock(this.#filePath, async () => {
      let current: ReadDesktopPetDocument
      try {
        current = await readStoredDocument(this.#filePath) ?? {
          document: firstRunStoredDocument(),
          warning: null,
        }
      } catch (error) {
        if (error instanceof DesktopPetPreferencesValidationError) {
          current = {
            document: failClosedStoredDocument(),
            warning: DESKTOP_PET_LOAD_WARNING,
          }
        } else {
          throw error
        }
      }
      if (request.expectedRevision !== current.document.revision) {
        throw new DesktopPetPreferencesConflictError(
          'Desktop pet preferences changed elsewhere. Reload before saving.',
        )
      }
      const selectedModelId = request.modelId === undefined
        ? current.document.selectedModelId
        : request.modelId
      if (
        selectedModelId !== null
        && current.document.libraryPath === null
      ) {
        throw new DesktopPetPreferencesValidationError(
          'Choose a Desktop Pet program folder before selecting a program.',
        )
      }
      if (request.mode === 'visible' && selectedModelId === null) {
        throw new DesktopPetPreferencesValidationError(
          'Choose a Desktop Pet program before turning it on.',
        )
      }
      if (
        request.mode === current.document.mode
        && selectedModelId === current.document.selectedModelId
      ) {
        return toLoadedPreferences(current, runtime)
      }
      if (current.document.revision === Number.MAX_SAFE_INTEGER) {
        throw new DesktopPetPreferencesStorageError(
          'Desktop pet preferences could not be saved.',
        )
      }
      const next = Object.freeze({
        ...current.document,
        revision: current.document.revision + 1,
        updatedAt: safeTimestamp(this.#now),
        mode: request.mode,
        selectedModelId,
      })
      await writeStoredDocument(
        this.#filePath,
        next,
        this.#replaceFile,
      )
      return toLoadedPreferences({ document: next, warning: null }, runtime)
    })
  }

  /**
   * Persist a Main-validated external program folder without exposing it.
   *
   * Clearing the folder or its selection also disables the pet, because a
   * running process must never outlive the program identity it was using.
   */
  async updateLibrary(
    requestValue: UpdateDesktopPetLibraryRequest,
    runtime: DesktopPetRuntimeState = 'absent',
  ): Promise<LoadedDesktopPetPreferences> {
    if (
      !isRecord(requestValue)
      || !hasExactFields(requestValue, [
        'expectedRevision',
        'libraryPath',
        'selectedModelId',
      ])
      || !Number.isSafeInteger(requestValue.expectedRevision)
      || requestValue.expectedRevision < 0
    ) {
      throw new DesktopPetPreferencesValidationError(
      'Desktop pet program-folder update is invalid.',
      )
    }
    const libraryPath = parseLibraryPath(requestValue.libraryPath)
    const selectedModelId = parseSelectedModelId(
      requestValue.selectedModelId,
    )
    if (libraryPath === null && selectedModelId !== null) {
      throw new DesktopPetPreferencesValidationError(
        'Desktop pet program selection has no configured folder.',
      )
    }
    requireRuntimeState(runtime)
    return withPathLock(this.#filePath, async () => {
      let current: ReadDesktopPetDocument
      try {
        current = await readStoredDocument(this.#filePath) ?? {
          document: firstRunStoredDocument(),
          warning: null,
        }
      } catch (error) {
        if (error instanceof DesktopPetPreferencesValidationError) {
          current = {
            document: failClosedStoredDocument(),
            warning: DESKTOP_PET_LOAD_WARNING,
          }
        } else {
          throw error
        }
      }
      if (requestValue.expectedRevision !== current.document.revision) {
        throw new DesktopPetPreferencesConflictError(
          'Desktop pet preferences changed elsewhere. Reload before saving.',
        )
      }
      if (
        libraryPath === current.document.libraryPath
        && selectedModelId === current.document.selectedModelId
      ) {
        return toLoadedPreferences(current, runtime)
      }
      if (current.document.revision === Number.MAX_SAFE_INTEGER) {
        throw new DesktopPetPreferencesStorageError(
          'Desktop pet preferences could not be saved.',
        )
      }
      const next = Object.freeze({
        ...current.document,
        revision: current.document.revision + 1,
        updatedAt: safeTimestamp(this.#now),
        mode: selectedModelId === null ? 'disabled' as const : current.document.mode,
        libraryPath,
        selectedModelId,
      })
      await writeStoredDocument(
        this.#filePath,
        next,
        this.#replaceFile,
      )
      return toLoadedPreferences({ document: next, warning: null }, runtime)
    })
  }

}
