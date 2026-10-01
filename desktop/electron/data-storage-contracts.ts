/**
 * Define Main-owned contracts for locating, measuring, moving, and cleaning data.
 *
 * These values may be rendered by Settings, but filesystem selection and every
 * mutation remain Electron Main responsibilities. The closed category set keeps
 * durable user content distinct from the three explicitly reclaimable classes.
 */

/** Current schema for both the stable bootstrap pointer and root manifest. */
export const DATA_STORAGE_SCHEMA_VERSION = 1 as const

/** Stable bootstrap filename stored directly below Electron userData. */
export const DATA_STORAGE_BOOTSTRAP_FILE_NAME = 'data-storage-bootstrap.json'

/** Manifest filename proving that a directory is an Elysia-managed data root. */
export const DATA_STORAGE_LAYOUT_FILE_NAME = 'layout.json'

/** Complete, stable set of capacity-reporting categories. */
export const DATA_STORAGE_CATEGORIES = Object.freeze([
  'config',
  'chats',
  'projects',
  'memory',
  'sources',
  'indexes',
  'audio',
  'cache',
  'logs',
  'other',
] as const)

/** The only categories that the temporary-data cleanup operation may remove. */
export const DATA_STORAGE_CLEANABLE_CATEGORIES = Object.freeze([
  'audio',
  'cache',
  'logs',
] as const)

/** One exact capacity category owned by the data-storage boundary. */
export type DataStorageCategory = typeof DATA_STORAGE_CATEGORIES[number]

/** One category that may be removed without deleting durable user content. */
export type DataStorageCleanableCategory =
  typeof DATA_STORAGE_CLEANABLE_CATEGORIES[number]

/** Current Main-side activity shown while native filesystem work is in flight. */
export type DataStorageBusyPhase =
  | 'idle'
  | 'initializing'
  | 'scanning'
  | 'moving'
  | 'cleaning'

/** Exact persisted move pending acknowledgement from a newly ready Backend. */
export interface DataStorageMoveTransaction {
  readonly transactionId: string
  readonly previousRoot: string
  readonly previousRootId: string
  readonly destinationRoot: string
  readonly destinationRootId: string
  readonly sourceFingerprint: string
  readonly switchedRevision: number
  readonly startedAt: string
}

/** Stable active-root state reconstructed from the bootstrap pointer. */
export interface DataStorageState {
  readonly revision: number
  readonly rootId: string
  readonly activeDataRoot: string
  readonly pendingMove: DataStorageMoveTransaction | null
  /** Existing recovery copies retained instead of being deleted unsafely. */
  readonly retainedRoots: readonly string[]
}

/** Renderer-safe state that never exposes an internal move transaction. */
export interface DataStoragePublicState {
  readonly revision: number
  readonly rootId: string
  readonly activeDataRoot: string
  readonly movePending: boolean
  /** Existing recovery copies that require user review outside generic cleanup. */
  readonly retainedRoots: readonly string[]
}

/** Result of first-run/default-root activation and optional legacy import. */
export interface DataStorageInitializeResult {
  readonly state: DataStorageState
  readonly legacyWorkspaceCopied: boolean
  readonly warning: string | null
}

/** Bounded usage measurement for one closed data category. */
export interface DataStorageCategoryUsage {
  readonly category: DataStorageCategory
  readonly bytes: number
  readonly fileCount: number
  readonly entryCount: number
}

/** One revision-bound capacity snapshot and one-use cleanup capability. */
export interface DataStorageInventory {
  readonly revision: number
  readonly rootId: string
  readonly activeDataRoot: string
  readonly token: string
  readonly categories: readonly DataStorageCategoryUsage[]
  readonly totalBytes: number
  readonly fileCount: number
  readonly blockedEntries: number
  readonly truncated: boolean
  readonly freeBytes: number | null
  readonly reclaimableBytes: number
  readonly measuredAt: string
  readonly warning: string | null
}

/** Renderer-facing aggregate composed by Main without granting path mutation. */
export interface DataStorageViewState {
  readonly state: DataStoragePublicState
  readonly inventory: DataStorageInventory | null
  readonly busyPhase: DataStorageBusyPhase
  readonly warning: string | null
}

/** Revision- and capability-bound request for a closed cleanup category subset. */
export interface DataStorageCleanupRequest {
  readonly expectedRevision: number
  readonly token: string
  readonly categories: readonly DataStorageCleanableCategory[]
}

/** Result after a safe cleanup invalidates the consumed inventory revision. */
export interface DataStorageCleanupResult {
  readonly state: DataStorageState
  readonly reclaimedBytes: number
  readonly deletedFileCount: number
  readonly warning: string | null
}

/** Commit outcome identifying whether conservative deletion retained the old root. */
export interface DataStorageCommitResult {
  readonly state: DataStorageState
  readonly oldRootRetained: boolean
  readonly retainedRoot: string | null
  readonly warning: string | null
}

/** Rollback outcome identifying whether conservative cleanup retained the new root. */
export interface DataStorageRollbackResult {
  readonly state: DataStorageState
  readonly newRootRetained: boolean
  readonly retainedRoot: string | null
  readonly warning: string | null
}

const CATEGORY_SET: ReadonlySet<string> = new Set(DATA_STORAGE_CATEGORIES)
const CLEANABLE_CATEGORY_SET: ReadonlySet<string> = new Set(
  DATA_STORAGE_CLEANABLE_CATEGORIES,
)
const CLEANUP_TOKEN_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu

/** Return whether an unknown value belongs to the complete category set. */
export function isDataStorageCategory(
  value: unknown,
): value is DataStorageCategory {
  return typeof value === 'string' && CATEGORY_SET.has(value)
}

/** Return whether a category belongs to the closed temporary-data allowlist. */
export function isDataStorageCleanableCategory(
  value: unknown,
): value is DataStorageCleanableCategory {
  return typeof value === 'string' && CLEANABLE_CATEGORY_SET.has(value)
}

/**
 * Revalidate a cleanup capability before Main performs any deletion.
 *
 * Exact fields and a non-empty unique allowlisted category array prevent a
 * compromised renderer from smuggling durable categories or filesystem paths.
 */
export function parseDataStorageCleanupRequest(
  value: unknown,
): DataStorageCleanupRequest {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Data cleanup request is invalid.')
  }
  const fields = Reflect.ownKeys(value)
  if (
    fields.length !== 3
    || !fields.includes('expectedRevision')
    || !fields.includes('token')
    || !fields.includes('categories')
  ) {
    throw new Error('Data cleanup request has invalid fields.')
  }
  const request = value as Record<string, unknown>
  if (
    !Number.isSafeInteger(request.expectedRevision)
    || (request.expectedRevision as number) < 0
  ) {
    throw new Error('Data cleanup revision is invalid.')
  }
  if (
    typeof request.token !== 'string'
    || !CLEANUP_TOKEN_PATTERN.test(request.token)
  ) {
    throw new Error('Data cleanup token is invalid.')
  }
  if (!Array.isArray(request.categories) || request.categories.length === 0) {
    throw new Error('Data cleanup categories are invalid.')
  }
  const categories = request.categories as unknown[]
  if (
    categories.some((category) => !isDataStorageCleanableCategory(category))
    || new Set(categories).size !== categories.length
  ) {
    throw new Error('Data cleanup categories are invalid.')
  }
  return Object.freeze({
    expectedRevision: request.expectedRevision as number,
    token: request.token,
    categories: Object.freeze(
      [...categories] as DataStorageCleanableCategory[],
    ),
  })
}
