/**
 * Persist bounded Electron-only desktop-pet intent and restore safe DIP bounds.
 *
 * The repository owns a small strict JSON document below Electron's user-data
 * directory. Renderer-safe state intentionally excludes native display IDs and
 * coordinates. Geometry helpers consume Electron work areas already expressed
 * in device-independent pixels and never apply display scale twice.
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
  type DesktopPetMode,
  type DesktopPetRuntimeState,
  type DesktopPetState,
  parseUpdateDesktopPetRequest,
} from './desktop-pet-contracts.js'

/** Current on-disk schema for the Electron-owned desktop-pet preference. */
export const DESKTOP_PET_PREFERENCES_SCHEMA_VERSION = 1

/** Maximum native pet content width, expressed in Electron DIP units. */
export const DESKTOP_PET_MAX_WIDTH_DIP = 420

/** Maximum native pet content height, expressed in Electron DIP units. */
export const DESKTOP_PET_MAX_HEIGHT_DIP = 560

const DESKTOP_PET_MIN_WIDTH_DIP = 160
const DESKTOP_PET_MIN_HEIGHT_DIP = 200
const DESKTOP_PET_DEFAULT_EDGE_MARGIN_DIP = 24
const DESKTOP_PET_MAX_PREFERENCE_BYTES = 16 * 1024
const DESKTOP_PET_MAX_ABSOLUTE_COORDINATE_DIP = 1_000_000
const DESKTOP_PET_LOAD_WARNING = (
  'Desktop pet preferences could not be loaded safely. The pet remains off.'
)

const DESKTOP_PET_RUNTIME_STATES: ReadonlySet<string> = new Set([
  'absent',
  'loading',
  'visible',
  'failed',
])

interface StoredDesktopPetDocument {
  readonly schemaVersion: typeof DESKTOP_PET_PREFERENCES_SCHEMA_VERSION
  readonly revision: number
  readonly updatedAt: string | null
  readonly mode: DesktopPetMode
  readonly placement: DesktopPetPlacement | null
}

interface ReadDesktopPetDocument {
  readonly document: StoredDesktopPetDocument
  readonly warning: string | null
}

/** Main-private position used to restore one fixed-size pet window. */
export interface DesktopPetPlacement {
  readonly displayId: number | null
  readonly x: number
  readonly y: number
}

/** Fixed requested pet-window size in Electron device-independent pixels. */
export interface DesktopPetWindowSize {
  readonly width: number
  readonly height: number
}

/** One Electron display work area already normalized to DIP coordinates. */
export interface DesktopPetDisplayGeometry {
  readonly id: number
  readonly primary: boolean
  readonly scaleFactor: number
  readonly workArea: DesktopPetBounds
}

/** Resolved native window rectangle in Electron device-independent pixels. */
export interface DesktopPetBounds {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

/**
 * Main-only result containing renderer-safe state and private native placement.
 *
 * IPC handlers must return only ``state``. The placement exists solely so the
 * native window controller can restore a reviewed position without granting a
 * renderer any display-enumeration capability.
 */
export interface LoadedDesktopPetPreferences {
  readonly state: DesktopPetState
  readonly placement: DesktopPetPlacement | null
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

/** Report an invalid Main-owned placement or repository construction request. */
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

function isSafeCoordinate(value: unknown): value is number {
  return Number.isSafeInteger(value)
    && Math.abs(value as number) <= DESKTOP_PET_MAX_ABSOLUTE_COORDINATE_DIP
}

function isSafeDisplayId(value: unknown): value is number {
  // Electron display IDs are opaque safe integers, not signed 32-bit
  // coordinates. Windows may expose the full unsigned hash range.
  return Number.isSafeInteger(value)
}

function parsePlacement(value: unknown): DesktopPetPlacement | null {
  if (value === null) {
    return null
  }
  if (
    !isRecord(value)
    || !hasExactFields(value, ['displayId', 'x', 'y'])
    || (value.displayId !== null && !isSafeDisplayId(value.displayId))
    || !isSafeCoordinate(value.x)
    || !isSafeCoordinate(value.y)
  ) {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet placement is invalid.',
    )
  }
  return Object.freeze({
    displayId: value.displayId as number | null,
    x: value.x as number,
    y: value.y as number,
  })
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

function parseStoredDocument(value: unknown): StoredDesktopPetDocument {
  if (
    !isRecord(value)
    || !hasExactFields(value, [
      'schemaVersion',
      'revision',
      'updatedAt',
      'mode',
      'placement',
    ])
    || value.schemaVersion !== DESKTOP_PET_PREFERENCES_SCHEMA_VERSION
    || !Number.isSafeInteger(value.revision)
    || (value.revision as number) < 0
    || !isCanonicalTimestamp(value.updatedAt)
  ) {
    throw new DesktopPetPreferencesValidationError(
      'Desktop pet preference document is invalid.',
    )
  }
  const request = parseUpdateDesktopPetRequest({
    expectedRevision: value.revision,
    mode: value.mode,
  })
  return Object.freeze({
    schemaVersion: DESKTOP_PET_PREFERENCES_SCHEMA_VERSION,
    revision: request.expectedRevision,
    updatedAt: value.updatedAt,
    mode: request.mode,
    placement: parsePlacement(value.placement),
  })
}

function firstRunStoredDocument(): StoredDesktopPetDocument {
  return Object.freeze({
    schemaVersion: DESKTOP_PET_PREFERENCES_SCHEMA_VERSION,
    revision: 0,
    updatedAt: null,
    mode: 'visible',
    placement: null,
  })
}

function failClosedStoredDocument(): StoredDesktopPetDocument {
  return Object.freeze({
    schemaVersion: DESKTOP_PET_PREFERENCES_SCHEMA_VERSION,
    revision: 0,
    updatedAt: null,
    mode: 'disabled',
    placement: null,
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
    }),
    placement: document.placement,
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
): Promise<StoredDesktopPetDocument | null> {
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

function normalizeWorkArea(bounds: DesktopPetBounds): DesktopPetBounds | null {
  if (
    !Number.isFinite(bounds.x)
    || !Number.isFinite(bounds.y)
    || !Number.isFinite(bounds.width)
    || !Number.isFinite(bounds.height)
    || bounds.width <= 0
    || bounds.height <= 0
  ) {
    return null
  }
  return Object.freeze({
    x: Math.trunc(bounds.x),
    y: Math.trunc(bounds.y),
    width: Math.max(1, Math.trunc(bounds.width)),
    height: Math.max(1, Math.trunc(bounds.height)),
  })
}

function distanceSquaredToWorkArea(
  x: number,
  y: number,
  workArea: DesktopPetBounds,
): number {
  const maximumX = workArea.x + workArea.width
  const maximumY = workArea.y + workArea.height
  const deltaX = x < workArea.x
    ? workArea.x - x
    : x > maximumX
      ? x - maximumX
      : 0
  const deltaY = y < workArea.y
    ? workArea.y - y
    : y > maximumY
      ? y - maximumY
      : 0
  return (deltaX * deltaX) + (deltaY * deltaY)
}

/**
 * Resolve a bounded fixed-size pet rectangle across current display work areas.
 *
 * Electron supplies every work area in DIP, so ``scaleFactor`` is validated but
 * deliberately not multiplied into coordinates. A missing saved display uses
 * the nearest current work area; no saved placement uses the primary display.
 * Invalid display input returns ``null`` so Main can fail the optional surface
 * without risking an off-screen or unbounded native window.
 */
export function resolveDesktopPetBounds(
  placement: DesktopPetPlacement | null,
  displays: readonly DesktopPetDisplayGeometry[],
  requestedSize: DesktopPetWindowSize,
): DesktopPetBounds | null {
  const validDisplays = displays.flatMap((display) => {
    const workArea = normalizeWorkArea(display.workArea)
    if (
      workArea === null
      || !isSafeDisplayId(display.id)
      || typeof display.primary !== 'boolean'
      || !Number.isFinite(display.scaleFactor)
      || display.scaleFactor <= 0
    ) {
      return []
    }
    return [{ ...display, workArea }]
  })
  if (
    validDisplays.length === 0
    || !Number.isFinite(requestedSize.width)
    || !Number.isFinite(requestedSize.height)
    || requestedSize.width <= 0
    || requestedSize.height <= 0
  ) {
    return null
  }

  let safePlacement: DesktopPetPlacement | null
  try {
    safePlacement = parsePlacement(placement)
  } catch {
    safePlacement = null
  }
  let display = safePlacement?.displayId === null
    || safePlacement === null
    ? undefined
    : validDisplays.find((candidate) => (
        candidate.id === safePlacement?.displayId
      ))
  if (display === undefined && safePlacement !== null) {
    display = [...validDisplays].sort((left, right) => (
      distanceSquaredToWorkArea(
        safePlacement.x,
        safePlacement.y,
        left.workArea,
      ) - distanceSquaredToWorkArea(
        safePlacement.x,
        safePlacement.y,
        right.workArea,
      )
    ))[0]
  }
  display ??= validDisplays.find((candidate) => candidate.primary)
    ?? validDisplays[0]

  const width = Math.min(
    display.workArea.width,
    DESKTOP_PET_MAX_WIDTH_DIP,
    Math.max(DESKTOP_PET_MIN_WIDTH_DIP, Math.trunc(requestedSize.width)),
  )
  const height = Math.min(
    display.workArea.height,
    DESKTOP_PET_MAX_HEIGHT_DIP,
    Math.max(DESKTOP_PET_MIN_HEIGHT_DIP, Math.trunc(requestedSize.height)),
  )
  const minimumX = display.workArea.x
  const minimumY = display.workArea.y
  const maximumX = display.workArea.x + display.workArea.width - width
  const maximumY = display.workArea.y + display.workArea.height - height
  const defaultX = maximumX - Math.min(
    DESKTOP_PET_DEFAULT_EDGE_MARGIN_DIP,
    Math.max(0, maximumX - minimumX),
  )
  const defaultY = maximumY - Math.min(
    DESKTOP_PET_DEFAULT_EDGE_MARGIN_DIP,
    Math.max(0, maximumY - minimumY),
  )
  const requestedX = safePlacement?.x ?? defaultX
  const requestedY = safePlacement?.y ?? defaultY
  return Object.freeze({
    x: Math.max(minimumX, Math.min(requestedX, maximumX)),
    y: Math.max(minimumY, Math.min(requestedY, maximumY)),
    width,
    height,
  })
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

  /** Load first-run visibility, while malformed persisted state fails closed. */
  async load(
    runtime: DesktopPetRuntimeState = 'absent',
  ): Promise<LoadedDesktopPetPreferences> {
    requireRuntimeState(runtime)
    try {
      const document = await readStoredDocument(this.#filePath)
      return toLoadedPreferences({
        document: document ?? firstRunStoredDocument(),
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
        current = {
          document: await readStoredDocument(this.#filePath)
            ?? firstRunStoredDocument(),
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
      if (request.mode === current.document.mode) {
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
      })
      await writeStoredDocument(
        this.#filePath,
        next,
        this.#replaceFile,
      )
      return toLoadedPreferences({ document: next, warning: null }, runtime)
    })
  }

  /** Atomically save native placement without changing renderer CAS revision. */
  async savePlacement(
    placementValue: DesktopPetPlacement | null,
  ): Promise<void> {
    const placement = parsePlacement(placementValue)
    await withPathLock(this.#filePath, async () => {
      let current: StoredDesktopPetDocument
      try {
        current = await readStoredDocument(this.#filePath)
          ?? firstRunStoredDocument()
      } catch {
        // A drag must never overwrite a damaged preference and accidentally
        // restore a pet the user did not safely opt into.
        throw new DesktopPetPreferencesStorageError(
          'Desktop pet preferences could not be saved.',
        )
      }
      if (
        current.placement?.displayId === placement?.displayId
        && current.placement?.x === placement?.x
        && current.placement?.y === placement?.y
      ) {
        return
      }
      await writeStoredDocument(
        this.#filePath,
        Object.freeze({ ...current, placement }),
        this.#replaceFile,
      )
    })
  }
}
