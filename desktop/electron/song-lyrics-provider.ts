/**
 * @fileoverview Resolve bounded, metadata-matched lyrics from LRCLIB in Electron Main.
 *
 * The provider intentionally owns its HTTPS transport instead of accepting a URL
 * from the renderer. Requests are pinned to one origin, carry no credentials, do
 * not follow redirects, and bound time, response bytes, result count, and text
 * sizes before any provider data can enter the rest of the application.
 */

import type { IncomingMessage } from 'node:http'
import { request as requestHttps } from 'node:https'

const LRCLIB_ORIGIN = 'https://lrclib.net'
const LRCLIB_HOSTNAME = 'lrclib.net'
const LRCLIB_USER_AGENT = (
  'Elysia/0.1.0 (https://github.com/ActorF/Elysia_AI)'
)
const REQUEST_TIMEOUT_MS = 8_000
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024
const MAX_RESULTS = 20
const MAX_QUERY_CODE_POINTS = 256
const MAX_METADATA_CODE_POINTS = 512
const MAX_LYRICS_CODE_POINTS = 100_000
const MAX_LYRICSFILE_CODE_POINTS = 300_000
const MAX_RETRY_AFTER_MS = 2_000
// One lookup can make an exact request and a search request, and each request
// can be retried once. This outer deadline includes every network attempt and
// retry wait so an injected or unexpectedly stuck transport cannot outlive the
// same closed policy enforced by the production HTTPS transport.
const LOOKUP_TIMEOUT_MS = (
  2 * ((2 * REQUEST_TIMEOUT_MS) + MAX_RETRY_AFTER_MS)
)
const MAX_LOOKUP_DURATION_SECONDS = 720
const MIN_CONFIDENCE = 0.82
const MIN_TITLE_SIMILARITY = 0.70
const MIN_ARTIST_SIMILARITY = 0.65
const MAX_DURATION_DIFFERENCE_SECONDS = 20
const RECORD_KEYS = Object.freeze([
  'albumName',
  'artistName',
  'duration',
  'hasWordSync',
  'id',
  'instrumental',
  'lyricsfile',
  'name',
  'plainLyrics',
  'syncedLyrics',
  'trackName',
] as const)

/** Metadata used to locate lyrics without exposing a native media path. */
export interface SongLyricsQuery {
  readonly title: string
  readonly artist: string
  readonly durationSeconds: number
}

/** Closed HTTPS request passed to an injected transport for deterministic tests. */
export interface SongLyricsHttpRequest {
  readonly origin: typeof LRCLIB_ORIGIN
  readonly method: 'GET'
  readonly path: string
  readonly headers: Readonly<Record<string, string>>
  readonly timeoutMs: number
  readonly maxResponseBytes: number
}

/** Bounded transport response consumed by the provider. */
export interface SongLyricsHttpResponse {
  readonly statusCode: number
  readonly headers: Readonly<Record<string, string | readonly string[] | undefined>>
  readonly body: Uint8Array
}

/** Execute exactly one closed LRCLIB HTTPS request. */
export type SongLyricsTransport = (
  request: SongLyricsHttpRequest,
  signal: AbortSignal,
) => Promise<SongLyricsHttpResponse>

/** Wait for a bounded rate-limit delay before the provider's sole retry. */
export type SongLyricsDelay = (
  milliseconds: number,
  signal: AbortSignal,
) => Promise<void>

/** Renderer-safe provenance for the selected LRCLIB record. */
export interface SongLyricsProviderRecord {
  readonly provider: 'lrclib'
  readonly recordId: number
  readonly trackName: string
  readonly artistName: string
  readonly albumName: string
  readonly durationSeconds: number
  readonly confidence: number
  readonly hasWordSync: boolean
}

/** A successful lyrics lookup with normalized text and bounded provenance. */
export interface SongLyricsFoundResult {
  readonly status: 'found'
  readonly plainLyrics: string | null
  readonly syncedLyrics: string | null
  readonly providerRecord: SongLyricsProviderRecord
}

/** A safe miss that never exposes the provider's untrusted near-match. */
export interface SongLyricsMissResult {
  readonly status: 'not-found' | 'low-confidence'
  readonly plainLyrics: null
  readonly syncedLyrics: null
  readonly providerRecord: null
}

/** Closed outcome returned by a lyrics lookup. */
export type SongLyricsLookupResult = (
  SongLyricsFoundResult | SongLyricsMissResult
)

interface LrclibRecord {
  readonly id: number
  readonly name: string
  readonly trackName: string
  readonly artistName: string
  readonly albumName: string
  readonly duration: number
  readonly instrumental: boolean
  readonly hasWordSync: boolean
  readonly plainLyrics: string | null
  readonly syncedLyrics: string | null
  readonly lyricsfile: string
}

interface ScoredRecord {
  readonly record: LrclibRecord
  readonly confidence: number
  readonly titleSimilarity: number
  readonly artistSimilarity: number
  readonly durationDifference: number
}

function codePointLength(value: string): number {
  return Array.from(value).length
}

function cancellationError(): Error {
  const error = new Error('Lyrics lookup was cancelled.')
  error.name = 'AbortError'
  return error
}

function throwIfCancelled(signal: AbortSignal): void {
  if (signal.aborted) throw cancellationError()
}

function hasDisallowedControl(value: string): boolean {
  for (const point of value) {
    const code = point.codePointAt(0) ?? 0
    if (
      (code <= 0x1f && code !== 0x09 && code !== 0x0a && code !== 0x0d)
      || code === 0x7f
    ) {
      return true
    }
  }
  return false
}

function validateQuery(value: SongLyricsQuery): SongLyricsQuery {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Lyrics lookup metadata is invalid.')
  }
  const keys = Object.keys(value).sort()
  if (
    keys.length !== 3
    || keys[0] !== 'artist'
    || keys[1] !== 'durationSeconds'
    || keys[2] !== 'title'
  ) {
    // Rejecting surplus fields keeps native paths, API keys, and other private
    // state from accidentally crossing into a provider request or result.
    throw new Error('Lyrics lookup metadata contains unsupported fields.')
  }
  for (const text of [value.title, value.artist]) {
    if (
      typeof text !== 'string'
      || text !== text.trim()
      || text.length === 0
      || codePointLength(text) > MAX_QUERY_CODE_POINTS
      || hasDisallowedControl(text)
    ) {
      throw new Error('Lyrics lookup metadata is invalid.')
    }
  }
  if (
    !Number.isFinite(value.durationSeconds)
    || value.durationSeconds < 1
    || value.durationSeconds > MAX_LOOKUP_DURATION_SECONDS
  ) {
    throw new Error('Lyrics lookup duration is invalid.')
  }
  return Object.freeze({
    title: value.title.normalize('NFC'),
    artist: value.artist.normalize('NFC'),
    durationSeconds: value.durationSeconds,
  })
}

function exactRequestPath(query: SongLyricsQuery): string {
  const parameters = new URLSearchParams({
    track_name: query.title,
    artist_name: query.artist,
    duration: String(Math.round(query.durationSeconds)),
  })
  return `/api/get?${parameters.toString()}`
}

function searchRequestPath(query: SongLyricsQuery): string {
  const parameters = new URLSearchParams({
    track_name: query.title,
    artist_name: query.artist,
  })
  return `/api/search?${parameters.toString()}`
}

function fixedRequest(path: string): SongLyricsHttpRequest {
  return Object.freeze({
    origin: LRCLIB_ORIGIN,
    method: 'GET',
    path,
    headers: Object.freeze({
      Accept: 'application/json',
      'Accept-Encoding': 'identity',
      'User-Agent': LRCLIB_USER_AGENT,
    }),
    timeoutMs: REQUEST_TIMEOUT_MS,
    maxResponseBytes: MAX_RESPONSE_BYTES,
  })
}

function copyHeaders(
  headers: Readonly<Record<string, string | readonly string[] | undefined>>,
): Readonly<Record<string, string | readonly string[] | undefined>> {
  return Object.freeze(Object.fromEntries(
    Object.entries(headers).map(([name, value]) => [
      name.toLowerCase(),
      Array.isArray(value) ? Object.freeze([...value]) : value,
    ]),
  ))
}

function defaultTransport(
  request: SongLyricsHttpRequest,
  signal: AbortSignal,
): Promise<SongLyricsHttpResponse> {
  return new Promise((resolve, reject) => {
    let settled = false
    let responseTerminated = false
    let requestTerminated = false
    let responseStream: IncomingMessage | null = null
    let clientRequest: ReturnType<typeof requestHttps> | null = null
    let wallClockTimeout: ReturnType<typeof setTimeout> | null = null
    const cleanup = (): void => {
      if (wallClockTimeout !== null) {
        clearTimeout(wallClockTimeout)
        wallClockTimeout = null
      }
      signal.removeEventListener('abort', handleAbort)
    }
    const terminateIo = (): void => {
      if (responseStream !== null && !responseTerminated) {
        responseTerminated = true
        responseStream.destroy()
      }
      if (clientRequest !== null && !requestTerminated) {
        requestTerminated = true
        clientRequest.destroy()
      }
    }
    const rejectSafely = (error: Error): void => {
      if (settled) return
      settled = true
      cleanup()
      reject(error)
    }
    const resolveSafely = (response: SongLyricsHttpResponse): void => {
      if (settled) return
      settled = true
      cleanup()
      resolve(response)
    }
    const handleAbort = (): void => {
      terminateIo()
      rejectSafely(cancellationError())
    }
    // AbortSignal does not replay an abort to listeners registered after the
    // event, so install the listener first and then recheck the state.
    signal.addEventListener('abort', handleAbort, { once: true })
    if (signal.aborted) {
      handleAbort()
      return
    }
    wallClockTimeout = setTimeout(() => {
      terminateIo()
      rejectSafely(new Error('Lyrics provider request timed out.'))
    }, request.timeoutMs)
    try {
      clientRequest = requestHttps({
        protocol: 'https:',
        hostname: LRCLIB_HOSTNAME,
        port: 443,
        method: request.method,
        path: request.path,
        headers: request.headers,
        agent: false,
      }, (response) => {
        responseStream = response
        if (settled || signal.aborted) {
          terminateIo()
          return
        }
        const chunks: Buffer[] = []
        let responseBytes = 0
        const declaredLength = Number(response.headers['content-length'])
        if (
          Number.isFinite(declaredLength)
          && declaredLength > request.maxResponseBytes
        ) {
          terminateIo()
          rejectSafely(new Error(
            'Lyrics provider response exceeded its byte limit.',
          ))
          return
        }
        response.on('data', (chunk: Buffer | string) => {
          const bytes = typeof chunk === 'string'
            ? Buffer.from(chunk, 'utf8')
            : chunk
          responseBytes += bytes.byteLength
          if (responseBytes > request.maxResponseBytes) {
            terminateIo()
            rejectSafely(new Error(
              'Lyrics provider response exceeded its byte limit.',
            ))
            return
          }
          chunks.push(bytes)
        })
        response.once('error', () => {
          terminateIo()
          rejectSafely(new Error('Lyrics provider response failed.'))
        })
        response.once('end', () => {
          resolveSafely(Object.freeze({
            statusCode: response.statusCode ?? 0,
            headers: copyHeaders(response.headers),
            body: Buffer.concat(chunks, responseBytes),
          }))
        })
      })
      if (settled || signal.aborted) {
        terminateIo()
        return
      }
      clientRequest.setTimeout(request.timeoutMs, () => {
        terminateIo()
        rejectSafely(new Error('Lyrics provider request timed out.'))
      })
      clientRequest.once('error', () => {
        terminateIo()
        rejectSafely(new Error('Lyrics provider request failed.'))
      })
      clientRequest.end()
    } catch {
      terminateIo()
      rejectSafely(new Error('Lyrics provider request failed.'))
    }
  })
}

function defaultDelay(
  milliseconds: number,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false
    let timeout: ReturnType<typeof setTimeout> | null = null
    const cleanup = (): void => {
      if (timeout !== null) {
        clearTimeout(timeout)
        timeout = null
      }
      signal.removeEventListener('abort', handleAbort)
    }
    const rejectSafely = (error: Error): void => {
      if (settled) return
      settled = true
      cleanup()
      reject(error)
    }
    const resolveSafely = (): void => {
      if (settled) return
      settled = true
      cleanup()
      resolve()
    }
    const handleAbort = (): void => {
      rejectSafely(cancellationError())
    }
    // Register before observing state because an already-fired AbortSignal does
    // not notify a listener added during the check-to-subscribe gap.
    signal.addEventListener('abort', handleAbort, { once: true })
    if (signal.aborted) {
      handleAbort()
      return
    }
    timeout = setTimeout(resolveSafely, milliseconds)
  })
}

/**
 * Bound the complete exact/retry/search operation and bridge caller aborts.
 *
 * A child controller is required because caller-owned signals cannot be
 * aborted when the deadline expires. The promise is also rejected directly so
 * a faulty injected transport cannot suppress the externally visible bound by
 * ignoring that child signal.
 */
function withWallClockDeadline<T>(
  timeoutMs: number,
  signal: AbortSignal,
  operation: (deadlineSignal: AbortSignal) => Promise<T>,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const deadlineController = new AbortController()
    let settled = false
    let timeout: ReturnType<typeof setTimeout> | null = null
    const cleanup = (): void => {
      if (timeout !== null) {
        clearTimeout(timeout)
        timeout = null
      }
      signal.removeEventListener('abort', handleAbort)
    }
    const rejectSafely = (error: Error): void => {
      if (settled) return
      settled = true
      cleanup()
      reject(error)
    }
    const resolveSafely = (value: T): void => {
      if (settled) return
      settled = true
      cleanup()
      resolve(value)
    }
    const abortOperation = (): void => {
      if (!deadlineController.signal.aborted) deadlineController.abort()
    }
    const handleAbort = (): void => {
      abortOperation()
      rejectSafely(cancellationError())
    }
    signal.addEventListener('abort', handleAbort, { once: true })
    if (signal.aborted) {
      handleAbort()
      return
    }
    timeout = setTimeout(() => {
      abortOperation()
      rejectSafely(new Error('Lyrics lookup timed out.'))
    }, timeoutMs)
    let operationPromise: Promise<T>
    try {
      operationPromise = operation(deadlineController.signal)
    } catch {
      rejectSafely(new Error('Lyrics lookup failed.'))
      return
    }
    operationPromise.then(resolveSafely, (error: unknown) => {
      if (signal.aborted) {
        rejectSafely(cancellationError())
        return
      }
      rejectSafely(
        error instanceof Error ? error : new Error('Lyrics lookup failed.'),
      )
    })
  })
}

function headerValue(
  headers: Readonly<Record<string, string | readonly string[] | undefined>>,
  name: string,
): string | undefined {
  const requestedName = name.toLowerCase()
  for (const [headerName, value] of Object.entries(headers)) {
    if (headerName.toLowerCase() !== requestedName) continue
    if (typeof value === 'string' || value === undefined) return value
    return value[0]
  }
  return undefined
}

function retryDelayMilliseconds(response: SongLyricsHttpResponse): number {
  const retryAfter = headerValue(response.headers, 'retry-after')
  if (retryAfter === undefined || !/^\d+$/u.test(retryAfter)) {
    throw new Error('Lyrics provider rate limit did not include a safe retry.')
  }
  const milliseconds = Number(retryAfter) * 1_000
  if (!Number.isSafeInteger(milliseconds) || milliseconds > MAX_RETRY_AFTER_MS) {
    throw new Error('Lyrics provider retry delay exceeded its limit.')
  }
  return milliseconds
}

function assertTransportResponse(
  response: SongLyricsHttpResponse,
  request: SongLyricsHttpRequest,
): void {
  if (
    typeof response !== 'object'
    || response === null
    || !Number.isSafeInteger(response.statusCode)
    || response.statusCode < 100
    || response.statusCode > 599
    || typeof response.headers !== 'object'
    || response.headers === null
    || !(response.body instanceof Uint8Array)
  ) {
    throw new Error('Lyrics provider returned an invalid transport response.')
  }
  if (response.body.byteLength > request.maxResponseBytes) {
    throw new Error('Lyrics provider response exceeded its byte limit.')
  }
}

function isStrictRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false
  }
  const keys = Object.keys(value).sort()
  return (
    keys.length === RECORD_KEYS.length
    && keys.every((key, index) => key === RECORD_KEYS[index])
  )
}

function boundedMetadata(value: unknown): value is string {
  return (
    typeof value === 'string'
    && value.length > 0
    && codePointLength(value) <= MAX_METADATA_CODE_POINTS
    && !hasDisallowedControl(value)
  )
}

function nullableBoundedLyrics(value: unknown): value is string | null {
  return (
    value === null
    || (
      typeof value === 'string'
      && codePointLength(value) <= MAX_LYRICS_CODE_POINTS
      && !hasDisallowedControl(value)
    )
  )
}

function parseRecord(value: unknown): LrclibRecord {
  if (
    !isStrictRecord(value)
    || !Number.isSafeInteger(value.id)
    || (value.id as number) <= 0
    || !boundedMetadata(value.name)
    || !boundedMetadata(value.trackName)
    || !boundedMetadata(value.artistName)
    || typeof value.albumName !== 'string'
    || codePointLength(value.albumName) > MAX_METADATA_CODE_POINTS
    || hasDisallowedControl(value.albumName)
    || !Number.isFinite(value.duration)
    || (value.duration as number) < 1
    || (value.duration as number) > 3_600
    || typeof value.instrumental !== 'boolean'
    || typeof value.hasWordSync !== 'boolean'
    || !nullableBoundedLyrics(value.plainLyrics)
    || !nullableBoundedLyrics(value.syncedLyrics)
    || typeof value.lyricsfile !== 'string'
    || value.lyricsfile.length === 0
    || codePointLength(value.lyricsfile) > MAX_LYRICSFILE_CODE_POINTS
    || hasDisallowedControl(value.lyricsfile)
  ) {
    throw new Error('Lyrics provider response did not match its schema.')
  }
  const record = value as unknown as LrclibRecord
  if (
    normalizeComparable(record.name) !== normalizeComparable(record.trackName)
    || (
      record.instrumental
      && (record.plainLyrics !== null || record.syncedLyrics !== null)
    )
    || (
      !record.instrumental
      && record.plainLyrics === null
      && record.syncedLyrics === null
    )
  ) {
    throw new Error('Lyrics provider response was internally inconsistent.')
  }
  return record
}

function parseJsonResponse(response: SongLyricsHttpResponse): unknown {
  const contentType = headerValue(response.headers, 'content-type')
  if (
    contentType === undefined
    || !/^application\/json(?:\s*;|$)/iu.test(contentType)
  ) {
    throw new Error('Lyrics provider response was not JSON.')
  }
  let text: string
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(response.body)
  } catch {
    throw new Error('Lyrics provider response was not valid UTF-8.')
  }
  try {
    return JSON.parse(text) as unknown
  } catch {
    throw new Error('Lyrics provider response was not valid JSON.')
  }
}

function parseRecordResponse(response: SongLyricsHttpResponse): LrclibRecord {
  return parseRecord(parseJsonResponse(response))
}

function parseRecords(response: SongLyricsHttpResponse): readonly LrclibRecord[] {
  const value = parseJsonResponse(response)
  if (!Array.isArray(value) || value.length > MAX_RESULTS) {
    throw new Error('Lyrics provider returned too many or invalid results.')
  }
  return Object.freeze(value.map(parseRecord))
}

function normalizeComparable(value: string): string {
  return value
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[\p{P}\p{S}]+/gu, ' ')
    .replace(/\s+/gu, ' ')
    .trim()
}

function bigramCounts(value: string): ReadonlyMap<string, number> {
  const points = Array.from(value)
  const counts = new Map<string, number>()
  if (points.length < 2) {
    if (points.length === 1) counts.set(points[0], 1)
    return counts
  }
  for (let index = 0; index + 1 < points.length; index += 1) {
    const pair = points[index] + points[index + 1]
    counts.set(pair, (counts.get(pair) ?? 0) + 1)
  }
  return counts
}

function similarity(left: string, right: string): number {
  const normalizedLeft = normalizeComparable(left)
  const normalizedRight = normalizeComparable(right)
  if (normalizedLeft.length === 0 || normalizedRight.length === 0) return 0
  if (normalizedLeft === normalizedRight) return 1
  const leftCounts = bigramCounts(normalizedLeft)
  const rightCounts = bigramCounts(normalizedRight)
  const leftTotal = [...leftCounts.values()].reduce((sum, count) => sum + count, 0)
  const rightTotal = [...rightCounts.values()].reduce((sum, count) => sum + count, 0)
  let intersection = 0
  for (const [pair, count] of leftCounts) {
    intersection += Math.min(count, rightCounts.get(pair) ?? 0)
  }
  const dice = leftTotal + rightTotal === 0
    ? 0
    : (2 * intersection) / (leftTotal + rightTotal)
  const containment = (
    normalizedLeft.includes(normalizedRight)
    || normalizedRight.includes(normalizedLeft)
  )
    ? 0.8 + (
        0.2
        * Math.min(normalizedLeft.length, normalizedRight.length)
        / Math.max(normalizedLeft.length, normalizedRight.length)
      )
    : 0
  return Math.max(dice, containment)
}

function durationSimilarity(difference: number): number {
  if (difference <= 2) return 1
  if (difference <= 5) return 0.85
  if (difference <= 10) return 0.5
  if (difference <= MAX_DURATION_DIFFERENCE_SECONDS) return 0.2
  return 0
}

function scoreRecord(
  record: LrclibRecord,
  query: SongLyricsQuery,
): ScoredRecord {
  const titleSimilarity = similarity(query.title, record.trackName)
  const artistSimilarity = similarity(query.artist, record.artistName)
  const durationDifference = Math.abs(query.durationSeconds - record.duration)
  const confidence = (
    (0.55 * titleSimilarity)
    + (0.35 * artistSimilarity)
    + (0.10 * durationSimilarity(durationDifference))
  )
  return {
    record,
    confidence,
    titleSimilarity,
    artistSimilarity,
    durationDifference,
  }
}

function selectBestRecord(
  records: readonly LrclibRecord[],
  query: SongLyricsQuery,
): ScoredRecord | null {
  const candidates = records
    .filter((record) => (
      !record.instrumental
      && (record.plainLyrics !== null || record.syncedLyrics !== null)
    ))
    .map((record) => scoreRecord(record, query))
    .sort((left, right) => (
      right.confidence - left.confidence
      || Number(right.record.syncedLyrics !== null)
        - Number(left.record.syncedLyrics !== null)
      || left.durationDifference - right.durationDifference
      || left.record.id - right.record.id
    ))
  return candidates[0] ?? null
}

function normalizeLyrics(value: string | null): string | null {
  if (value === null) return null
  const normalized = value
    .replace(/^\uFEFF/u, '')
    .replace(/\r\n?/gu, '\n')
    .replace(/[\u2028\u2029]/gu, '\n')
    .normalize('NFC')
    .split('\n')
    .map((line) => line.replace(/[\t ]+$/gu, ''))
    .join('\n')
    .trim()
  return normalized.length === 0 ? null : normalized
}

function miss(status: SongLyricsMissResult['status']): SongLyricsMissResult {
  return Object.freeze({
    status,
    plainLyrics: null,
    syncedLyrics: null,
    providerRecord: null,
  })
}

async function requestWithBoundedRetry(
  transport: SongLyricsTransport,
  delay: SongLyricsDelay,
  request: SongLyricsHttpRequest,
  signal: AbortSignal,
): Promise<SongLyricsHttpResponse> {
  let response: SongLyricsHttpResponse | null = null
  for (let attempt = 0; attempt < 2; attempt += 1) {
    throwIfCancelled(signal)
    response = await transport(request, signal)
    throwIfCancelled(signal)
    assertTransportResponse(response, request)
    if (response.statusCode !== 429 && response.statusCode !== 503) break
    if (attempt !== 0) {
      throw new Error('Lyrics provider remained unavailable after one retry.')
    }
    await delay(retryDelayMilliseconds(response), signal)
  }
  throwIfCancelled(signal)
  if (response === null) {
    throw new Error('Lyrics provider did not return a response.')
  }
  if (response.statusCode >= 300 && response.statusCode < 400) {
    throw new Error('Lyrics provider redirects are not allowed.')
  }
  return response
}

function matchedResult(
  records: readonly LrclibRecord[],
  query: SongLyricsQuery,
): SongLyricsLookupResult {
  const best = selectBestRecord(records, query)
  if (best === null) return miss('not-found')
  if (
    best.confidence < MIN_CONFIDENCE
    || best.titleSimilarity < MIN_TITLE_SIMILARITY
    || best.artistSimilarity < MIN_ARTIST_SIMILARITY
    || best.durationDifference > MAX_DURATION_DIFFERENCE_SECONDS
  ) {
    return miss('low-confidence')
  }
  const plainLyrics = normalizeLyrics(best.record.plainLyrics)
  const syncedLyrics = normalizeLyrics(best.record.syncedLyrics)
  if (plainLyrics === null && syncedLyrics === null) return miss('not-found')
  return Object.freeze({
    status: 'found',
    plainLyrics,
    syncedLyrics,
    providerRecord: Object.freeze({
      provider: 'lrclib',
      recordId: best.record.id,
      trackName: best.record.trackName.normalize('NFC'),
      artistName: best.record.artistName.normalize('NFC'),
      albumName: best.record.albumName.normalize('NFC'),
      durationSeconds: best.record.duration,
      confidence: Math.round(best.confidence * 10_000) / 10_000,
      hasWordSync: best.record.hasWordSync,
    }),
  })
}

/**
 * Resolve one track's best LRCLIB match through a fixed, bounded HTTPS policy.
 *
 * A transport and delay can be injected for tests; production defaults never
 * consult proxy environment variables, forward credentials, or follow a
 * provider redirect. Rate-limited or overloaded responses receive at most one
 * bounded retry. Exact duration matching is attempted before fuzzy search.
 */
export class SongLyricsProvider {
  constructor(
    private readonly transport: SongLyricsTransport = defaultTransport,
    private readonly delay: SongLyricsDelay = defaultDelay,
  ) {}

  /**
   * Return credible normalized lyrics within the fixed wall-clock deadline,
   * aborting transport and retry waits when the caller signal is cancelled.
   */
  async lookup(
    queryValue: SongLyricsQuery,
    signalValue?: AbortSignal,
  ): Promise<SongLyricsLookupResult> {
    const signal = signalValue ?? new AbortController().signal
    throwIfCancelled(signal)
    const query = validateQuery(queryValue)
    return withWallClockDeadline(
      LOOKUP_TIMEOUT_MS,
      signal,
      async (deadlineSignal) => {
        const exactResponse = await requestWithBoundedRetry(
          this.transport,
          this.delay,
          fixedRequest(exactRequestPath(query)),
          deadlineSignal,
        )
        if (exactResponse.statusCode === 200) {
          const exactResult = matchedResult(
            [parseRecordResponse(exactResponse)],
            query,
          )
          if (exactResult.status === 'found') return exactResult
        } else if (exactResponse.statusCode !== 404) {
          throw new Error('Lyrics provider request was unsuccessful.')
        }
        const searchResponse = await requestWithBoundedRetry(
          this.transport,
          this.delay,
          fixedRequest(searchRequestPath(query)),
          deadlineSignal,
        )
        if (searchResponse.statusCode === 404) return miss('not-found')
        if (searchResponse.statusCode !== 200) {
          throw new Error('Lyrics provider request was unsuccessful.')
        }
        return matchedResult(parseRecords(searchResponse), query)
      },
    )
  }
}
