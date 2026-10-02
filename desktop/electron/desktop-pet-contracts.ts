/**
 * Define the closed Electron-only contracts for the optional desktop pet.
 *
 * These values never enter the Python desktop protocol. Electron Main owns
 * persistence and native-window lifecycle, while the dedicated pet preload
 * exposes only native actions and one opaque selected-model URL to the
 * isolated pet renderer.
 */

/** Persisted user intent for the optional desktop-pet window. */
export type DesktopPetMode = 'disabled' | 'hidden' | 'visible'

/** Renderer-safe outcome of scanning one Main-owned external model folder. */
export type DesktopPetLibraryStatus =
  | 'not-configured'
  | 'scanning'
  | 'ready'
  | 'empty'
  | 'unavailable'
  | 'invalid'

/** One externally discovered model without its private filesystem identity. */
export interface DesktopPetModelSummary {
  readonly id: string
  readonly displayName: string
}

/** Renderer-safe coordinates needed to load the current external model. */
export interface DesktopPetModelBootstrap {
  readonly modelId: string
  readonly manifestUrl: string
}

/** Current native-window outcome, which is never persisted as user intent. */
export type DesktopPetRuntimeState =
  | 'absent'
  | 'loading'
  | 'visible'
  | 'failed'

/** Renderer-safe desktop-pet preference and native lifecycle snapshot. */
export interface DesktopPetState {
  readonly revision: number
  readonly updatedAt: string | null
  readonly mode: DesktopPetMode
  readonly runtime: DesktopPetRuntimeState
  readonly warning: string | null
  readonly libraryStatus: DesktopPetLibraryStatus
  readonly folderName: string | null
  readonly models: readonly DesktopPetModelSummary[]
  readonly selectedModelId: string | null
}

/** Replace desktop-pet user intent using optimistic revision control. */
export interface UpdateDesktopPetRequest {
  readonly expectedRevision: number
  readonly mode: DesktopPetMode
  /** Replace the selected external model, or preserve it when omitted. */
  readonly modelId?: string | null
}

/**
 * Minimal capability surface exposed only inside the sandboxed pet renderer.
 *
 * The pet receives no Backend, filesystem, settings, raw IPC, or arbitrary
 * native-window capabilities. Main revalidates the sender for every call.
 */
export interface DesktopPetApi {
  /** Announce that the isolated pet document is ready to be revealed. */
  ready(): Promise<void>
  /** Report an initialization or runtime failure so Main can destroy the pet. */
  failed(): Promise<void>
  /** Persistently hide the pet without disabling the user's opt-in. */
  hide(): Promise<void>
  /** Reveal and focus the ordinary main Chat window. */
  openMainChat(): Promise<void>
  /** Read the opaque manifest URL for the model selected by Settings. */
  getModelBootstrap(): Promise<DesktopPetModelBootstrap>
}

const DESKTOP_PET_MODES: ReadonlySet<string> = new Set([
  'disabled',
  'hidden',
  'visible',
])

const DESKTOP_PET_MODEL_ID_PATTERN = /^model_[0-9a-f]{32}$/u

/** Narrow an unknown value to the complete persisted desktop-pet mode set. */
export function isDesktopPetMode(value: unknown): value is DesktopPetMode {
  return typeof value === 'string' && DESKTOP_PET_MODES.has(value)
}

/** Accept only opaque identifiers minted by the bounded Main-process scan. */
export function isDesktopPetModelId(value: unknown): value is string {
  return typeof value === 'string'
    && DESKTOP_PET_MODEL_ID_PATTERN.test(value)
}

/**
 * Revalidate one renderer-originated update before it reaches persistence.
 *
 * Exact fields prevent a future or compromised renderer from smuggling native
 * bounds, paths, animation selectors, or other capabilities into Main.
 */
export function parseUpdateDesktopPetRequest(
  value: unknown,
): UpdateDesktopPetRequest {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Desktop pet update is invalid.')
  }
  const fields = Reflect.ownKeys(value)
  if (
    (fields.length !== 2 && fields.length !== 3)
    || !fields.includes('expectedRevision')
    || !fields.includes('mode')
    || (fields.length === 3 && !fields.includes('modelId'))
  ) {
    throw new Error('Desktop pet update has invalid fields.')
  }
  const request = value as Record<string, unknown>
  if (
    !Number.isSafeInteger(request.expectedRevision)
    || (request.expectedRevision as number) < 0
  ) {
    throw new Error('Desktop pet revision is invalid.')
  }
  if (!isDesktopPetMode(request.mode)) {
    throw new Error('Desktop pet mode is invalid.')
  }
  if (
    Object.hasOwn(request, 'modelId')
    && request.modelId !== null
    && !isDesktopPetModelId(request.modelId)
  ) {
    throw new Error('Desktop pet model selection is invalid.')
  }
  if (Object.hasOwn(request, 'modelId')) {
    return Object.freeze({
      expectedRevision: request.expectedRevision as number,
      mode: request.mode,
      modelId: request.modelId as string | null,
    })
  }
  return Object.freeze({
    expectedRevision: request.expectedRevision as number,
    mode: request.mode,
  })
}
