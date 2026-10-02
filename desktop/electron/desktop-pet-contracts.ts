/**
 * Define the closed Electron-only contracts for the optional desktop pet.
 *
 * These values never enter the Python desktop protocol. Electron Main owns
 * persistence and native-window lifecycle, while the dedicated pet preload
 * exposes only the native actions and read-only motion synchronization needed
 * by the isolated pet renderer.
 */

import type {
  CharacterPerformanceState,
} from './character-performance-contracts.js'

/** Persisted user intent for the optional desktop-pet window. */
export type DesktopPetMode = 'disabled' | 'hidden' | 'visible'

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
}

/** Replace desktop-pet user intent using optimistic revision control. */
export interface UpdateDesktopPetRequest {
  readonly expectedRevision: number
  readonly mode: DesktopPetMode
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
  /** Persistently hide the pet without disabling the user's opt-in. */
  hide(): Promise<void>
  /** Reveal and focus the ordinary main Chat window. */
  openMainChat(): Promise<void>
  /** Read Main's latest motion choice without accessing main-renderer storage. */
  getCharacterPerformanceState(): Promise<CharacterPerformanceState>
  /** Subscribe to later validated motion choices relayed by Electron Main. */
  onCharacterPerformanceStateChanged(
    listener: (state: CharacterPerformanceState) => void,
  ): () => void
}

const DESKTOP_PET_MODES: ReadonlySet<string> = new Set([
  'disabled',
  'hidden',
  'visible',
])

/** Narrow an unknown value to the complete persisted desktop-pet mode set. */
export function isDesktopPetMode(value: unknown): value is DesktopPetMode {
  return typeof value === 'string' && DESKTOP_PET_MODES.has(value)
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
    fields.length !== 2
    || !fields.includes('expectedRevision')
    || !fields.includes('mode')
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
  return Object.freeze({
    expectedRevision: request.expectedRevision as number,
    mode: request.mode,
  })
}
