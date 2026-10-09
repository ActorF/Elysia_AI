/**
 * Define renderer-safe readiness and lifecycle state for local Song Cover.
 *
 * Native source/output paths, model locations, process identifiers, and audio
 * bytes deliberately stay outside this contract.  React receives only a base
 * filename and bounded progress needed to explain and control the active job.
 */

/** Closed stages for the one-at-a-time local singing-cover workflow. */
export type SongCoverStage =
  | 'idle'
  | 'validating'
  | 'separating'
  | 'transcribing'
  | 'aligning'
  | 'synthesizing'
  | 'converting'
  | 'mixing'
  | 'ready'
  | 'playing'
  | 'cancelled'
  | 'error'

/** Audio sources accepted by the native Song Cover setup workflow. */
export type SongCoverSourceMode = 'song' | 'stems'

/** Explicit local singing engine; lyrics-driven SVS is the product default. */
export type SongCoverEngine = 'lyrics-svs' | 'legacy-svc'

/** Closed reasons why Main refuses to offer new Song Cover generation. */
export type SongCoverUnavailableReason =
  | 'not-included-in-build'
  | 'local-runtime-unavailable'

/**
 * Non-invasive Song Cover admission status safe for the Renderer.
 *
 * Main never includes a path, digest, dependency name, or native diagnostic;
 * the Renderer owns the fixed explanatory copy for these closed reason codes.
 */
export type SongCoverReadiness =
  | Readonly<{ status: 'available'; reason: null }>
  | Readonly<{
      status: 'unavailable'
      reason: SongCoverUnavailableReason
    }>

/** Closed Renderer-facing failure categories for Song Cover recovery guidance. */
export type SongCoverErrorCode =
  | 'invalid-audio'
  | 'lyrics-network'
  | 'lyrics-no-match'
  | 'lyrics-no-sync'
  | 'lyrics-alignment'
  | 'singing-runtime'

/**
 * Whole-song pitch adjustments supported by the reviewed local FFmpeg path.
 *
 * Shifts beyond two semitones in either direction require a higher-quality
 * pitch shifter than the bundled runtime provides, so the renderer cannot
 * request arbitrary numbers.
 */
export type SongCoverKeyShiftSemitones = -2 | -1 | 0 | 1 | 2

/** Optional user-confirmed identity used only for the online lyrics lookup. */
export interface SongLyricsMetadataOverride {
  readonly title: string
  readonly artist: string
}

/** Renderer request for native Song Cover source selection. */
export interface ChooseSongCoverRequest {
  readonly engine: SongCoverEngine
  readonly sourceMode: SongCoverSourceMode
  readonly keyShiftSemitones: SongCoverKeyShiftSemitones
  readonly lyricsMetadataOverride: SongLyricsMetadataOverride | null
}

/** Canonical Main-owned song-cover state safe for the sandboxed renderer. */
export interface SongCoverState {
  readonly revision: number
  readonly jobId: string | null
  readonly stage: SongCoverStage
  readonly sourceName: string | null
  readonly accompanimentName: string | null
  readonly sourceMode: SongCoverSourceMode | null
  readonly engine: SongCoverEngine | null
  readonly keyShiftSemitones: SongCoverKeyShiftSemitones | null
  readonly progressPercent: number
  readonly outputAvailable: boolean
  readonly message: string | null
  readonly errorCode: SongCoverErrorCode | null
  readonly error: string | null
}

const SOURCE_MODES = new Set<SongCoverSourceMode>(['song', 'stems'])
const ENGINES = new Set<SongCoverEngine>(['lyrics-svs', 'legacy-svc'])
const KEY_SHIFTS = new Set<SongCoverKeyShiftSemitones>([-2, -1, 0, 1, 2])
const UNAVAILABLE_REASONS = new Set<SongCoverUnavailableReason>([
  'not-included-in-build',
  'local-runtime-unavailable',
])
const MAX_LYRICS_METADATA_CODE_POINTS = 256

/** Parse only the exact closed readiness shape emitted by Electron Main. */
export function parseSongCoverReadiness(
  value: unknown,
): SongCoverReadiness | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }
  const record = value as Record<string, unknown>
  if (
    Object.keys(record).length !== 2
    || typeof record.status !== 'string'
    || !Object.hasOwn(record, 'reason')
  ) {
    return null
  }
  if (record.status === 'available' && record.reason === null) {
    return Object.freeze({ status: 'available', reason: null })
  }
  if (
    record.status === 'unavailable'
    && typeof record.reason === 'string'
    && UNAVAILABLE_REASONS.has(record.reason as SongCoverUnavailableReason)
  ) {
    return Object.freeze({
      status: 'unavailable',
      reason: record.reason as SongCoverUnavailableReason,
    })
  }
  return null
}

function boundedLyricsMetadata(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  if (
    trimmed.length === 0
    || Array.from(trimmed).length > MAX_LYRICS_METADATA_CODE_POINTS
    || /[\p{Cc}\p{Cs}]/u.test(trimmed)
  ) {
    return null
  }
  return trimmed.normalize('NFC')
}

/** Normalize a paired lookup identity; `undefined` means malformed input. */
export function parseSongLyricsMetadataOverride(
  value: unknown,
): SongLyricsMetadataOverride | null | undefined {
  if (value === null) return null
  if (typeof value !== 'object' || Array.isArray(value)) return undefined
  const record = value as Record<string, unknown>
  if (
    record === null
    || Object.keys(record).length !== 2
    || !Object.hasOwn(record, 'title')
    || !Object.hasOwn(record, 'artist')
  ) {
    return undefined
  }
  if (
    typeof record.title === 'string'
    && typeof record.artist === 'string'
    && record.title.trim().length === 0
    && record.artist.trim().length === 0
  ) return null
  const title = boundedLyricsMetadata(record.title)
  const artist = boundedLyricsMetadata(record.artist)
  if (title === null || artist === null) return undefined
  return Object.freeze({ title, artist })
}

/** Reject extra fields and unsupported modes before opening a native picker. */
export function parseChooseSongCoverRequest(
  value: unknown,
): ChooseSongCoverRequest | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }
  const record = value as Record<string, unknown>
  const lyricsMetadataOverride = parseSongLyricsMetadataOverride(
    record.lyricsMetadataOverride,
  )
  if (
    Object.keys(record).length !== 4
    || typeof record.engine !== 'string'
    || !ENGINES.has(record.engine as SongCoverEngine)
    || typeof record.sourceMode !== 'string'
    || !SOURCE_MODES.has(record.sourceMode as SongCoverSourceMode)
    || typeof record.keyShiftSemitones !== 'number'
    || !KEY_SHIFTS.has(
      record.keyShiftSemitones as SongCoverKeyShiftSemitones,
    )
    || lyricsMetadataOverride === undefined
    || (
      record.engine === 'legacy-svc'
      && lyricsMetadataOverride !== null
    )
  ) {
    return null
  }
  return Object.freeze({
    engine: record.engine as SongCoverEngine,
    sourceMode: record.sourceMode as SongCoverSourceMode,
    keyShiftSemitones: (
      record.keyShiftSemitones as SongCoverKeyShiftSemitones
    ),
    lyricsMetadataOverride,
  })
}

/** One authenticated progress record emitted by the private Python worker. */
export interface SongCoverWorkerEvent {
  readonly stage:
    | 'validating'
    | 'separating'
    | 'transcribing'
    | 'aligning'
    | 'synthesizing'
    | 'converting'
    | 'mixing'
    | 'complete'
  readonly progressPercent: number
  readonly message: string
}

const WORKER_STAGES = new Set<SongCoverWorkerEvent['stage']>([
  'validating',
  'separating',
  'transcribing',
  'aligning',
  'synthesizing',
  'converting',
  'mixing',
  'complete',
])

/** Parse an exact bounded worker event without accepting extra fields. */
export function parseSongCoverWorkerEvent(
  value: unknown,
): SongCoverWorkerEvent | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }
  const record = value as Record<string, unknown>
  if (
    Object.keys(record).length !== 3
    || typeof record.stage !== 'string'
    || !WORKER_STAGES.has(record.stage as SongCoverWorkerEvent['stage'])
    || !Number.isSafeInteger(record.progressPercent)
    || (record.progressPercent as number) < 0
    || (record.progressPercent as number) > 100
    || typeof record.message !== 'string'
    || record.message.length === 0
    || record.message.length > 160
    || /\p{Cc}/u.test(record.message)
  ) {
    return null
  }
  return Object.freeze({
    stage: record.stage as SongCoverWorkerEvent['stage'],
    progressPercent: record.progressPercent as number,
    message: record.message,
  })
}
