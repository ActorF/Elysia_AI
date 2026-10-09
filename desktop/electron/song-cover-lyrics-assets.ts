/**
 * @fileoverview Prepare private, provider-matched lyrics for a lyrics-driven SVS job.
 *
 * Electron Main owns metadata probing, the LRCLIB lookup, and fixed-name asset
 * creation. Lyrics never cross the renderer contract or native command line;
 * an offline singing worker can read them only from its already-private job
 * root. The legacy RVC voice-conversion path deliberately does not call this
 * module because it cannot consume lyrics and must not imply that it can.
 */

import { spawn } from 'node:child_process'
import { lstat, open, rm, type FileHandle } from 'node:fs/promises'
import path from 'node:path'

import {
  SongLyricsProvider,
  type SongLyricsLookupResult,
  type SongLyricsQuery,
} from './song-lyrics-provider.js'
import type { SongLyricsMetadataOverride } from './song-cover-contracts.js'

const MAX_PROBE_STDOUT_BYTES = 64 * 1024
const PROBE_TIMEOUT_MS = 10_000
const MAX_METADATA_CODE_POINTS = 256
const MIN_DURATION_SECONDS = 1
const MAX_DURATION_SECONDS = 720
const LYRICS_LRC_NAME = 'lyrics.lrc'
const LYRICS_TEXT_NAME = 'lyrics.txt'
const LYRICS_MANIFEST_NAME = 'lyrics-manifest.json'
const DEFAULT_LYRICS_PROVIDER = new SongLyricsProvider()

/** Raw, bounded metadata returned by an authenticated local FFprobe process. */
export interface SongLyricsProbedMetadata {
  readonly title: string | null
  readonly artist: string | null
  readonly durationSeconds: number
}

/** Probe one Main-private audio path without returning that path to a caller. */
export type SongLyricsMetadataProbe = (
  sourcePath: string,
  ffprobePath: string,
  signal: AbortSignal,
) => Promise<SongLyricsProbedMetadata | null>

/** Perform one provider lookup from metadata that has passed local validation. */
export type SongLyricsLookup = (
  query: SongLyricsQuery,
  signal: AbortSignal,
) => Promise<SongLyricsLookupResult>

/** Inputs owned by Main while preparing an offline SVS job root. */
export interface SongLyricsAssetPreparationInput {
  readonly jobDirectory: string
  readonly sourcePath: string
  readonly sourceName: string
  readonly ffprobePath: string
  readonly metadataOverride: SongLyricsMetadataOverride | null
  readonly signal: AbortSignal
}

/** Non-lyrical provenance persisted beside private lyrics for later auditing. */
export interface SongLyricsAssetManifest {
  readonly source: 'lrclib'
  readonly recordId: number
  readonly confidence: number
  readonly hasSyncedLyrics: boolean
}

/** A prepared private asset set; names are fixed and contain no native path. */
export interface SongLyricsAssetsReadyResult {
  readonly status: 'ready'
  readonly files: readonly (
    | typeof LYRICS_LRC_NAME
    | typeof LYRICS_TEXT_NAME
    | typeof LYRICS_MANIFEST_NAME
  )[]
  readonly manifest: SongLyricsAssetManifest
}

/** Safe refusal when metadata or provider matching is not trustworthy enough. */
export interface SongLyricsAssetsUnavailableResult {
  readonly status:
    | 'invalid-audio'
    | 'needs-metadata'
    | 'not-found'
    | 'low-confidence'
}

/** Closed result returned before a lyrics-driven worker may start. */
export type SongLyricsAssetPreparationResult = (
  SongLyricsAssetsReadyResult | SongLyricsAssetsUnavailableResult
)

interface ProbeProcessResult {
  readonly exitCode: number
  readonly stdout: Uint8Array
}

function codePointLength(value: string): number {
  return Array.from(value).length
}

function cancellationError(): Error {
  const error = new Error('Song lyrics preparation was cancelled.')
  error.name = 'AbortError'
  return error
}

function throwIfCancelled(signal: AbortSignal): void {
  if (signal.aborted) throw cancellationError()
}

function boundedMetadata(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  if (
    trimmed.length === 0
    || codePointLength(trimmed) > MAX_METADATA_CODE_POINTS
    || /[\p{Cc}\p{Cs}]/u.test(trimmed)
  ) {
    return null
  }
  return trimmed.normalize('NFC')
}

function comparable(value: string): string {
  return value
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[\p{P}\p{S}]+/gu, ' ')
    .replace(/\s+/gu, ' ')
    .trim()
}

function looksGeneratedName(value: string): boolean {
  return (
    /^screen[ _-]?record(?:ing)?(?:[ _-]|$)/iu.test(value)
    || /^\d{4}[-_.]\d{2}[-_.]\d{2}(?:[-_ T]\d{2}){0,3}/u.test(value)
    || /^\d{8}[-_]?\d{6}$/u.test(value)
    || /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu
      .test(value)
  )
}

function filenameParts(
  sourceName: string,
): Readonly<{ left: string; right: string }> | null {
  if (
    sourceName.length === 0
    || sourceName.length > 255
    || path.basename(sourceName) !== sourceName
  ) {
    return null
  }
  const stem = path.parse(sourceName).name.normalize('NFC')
  if (looksGeneratedName(stem)) return null
  const separators = [' - ', ' – ', ' — '] as const
  const separatedMatches = separators.flatMap((separator) => {
    const parts = stem.split(separator)
    return parts.length === 2 ? [parts] : []
  })
  // Some lyric-video downloads use `Artist_Title【动态歌词_Lyrics_Video】`.
  // Treating every underscore as a separator would misidentify ordinary file
  // names, so this compatibility path accepts only that exact export shape.
  const lyricVideoMatch = /^([^_【】]+)_([^_【】]+)【(?:動態歌詞|动态歌词)_Lyrics_Video】$/iu.exec(stem)
  const matches = lyricVideoMatch === null
    ? separatedMatches
    : [...separatedMatches, [lyricVideoMatch[1], lyricVideoMatch[2]]]
  if (matches.length !== 1) return null
  const [leftValue, rightValue] = matches[0]
  const left = boundedMetadata(leftValue.trim())
  const right = boundedMetadata(rightValue.trim())
  if (
    left === null
    || right === null
    || looksGeneratedName(left)
    || looksGeneratedName(right)
  ) return null

  // Generic stem names are common after vocal separation and are not evidence
  // of a track identity. Refusing them prevents a plausible but wrong lookup.
  const generic = new Set([
    'audio',
    'instrumental',
    'mix',
    'output',
    'recording',
    'song',
    'track',
    'unknown artist',
    'vocal',
    'vocals',
  ])
  if (generic.has(comparable(left)) || generic.has(comparable(right))) {
    return null
  }
  return Object.freeze({ left, right })
}

/**
 * Combine trusted media tags with only a tag-disambiguated two-part filename.
 *
 * Untagged `A - B` is ambiguous because both Artist-Title and Title-Artist are
 * common. One trusted tag must identify either side before the other is used;
 * this avoids silently swapping fields or searching for a `vocals` stem.
 */
export function deriveSongLyricsQuery(
  metadata: SongLyricsProbedMetadata,
  sourceName: string,
): SongLyricsQuery | null {
  if (
    !Number.isFinite(metadata.durationSeconds)
    || metadata.durationSeconds < MIN_DURATION_SECONDS
    || metadata.durationSeconds > MAX_DURATION_SECONDS
  ) {
    return null
  }
  const taggedTitle = metadata.title === null
    ? null
    : boundedMetadata(metadata.title)
  const taggedArtist = metadata.artist === null
    ? null
    : boundedMetadata(metadata.artist)
  if (taggedTitle !== null && taggedArtist !== null) {
    return Object.freeze({
      title: taggedTitle,
      artist: taggedArtist,
      durationSeconds: metadata.durationSeconds,
    })
  }
  const filename = filenameParts(sourceName)
  if (filename === null) return null
  if (taggedTitle !== null && taggedArtist === null) {
    if (comparable(taggedTitle) === comparable(filename.left)) {
      return Object.freeze({
        title: taggedTitle,
        artist: filename.right,
        durationSeconds: metadata.durationSeconds,
      })
    }
    if (comparable(taggedTitle) === comparable(filename.right)) {
      return Object.freeze({
        title: taggedTitle,
        artist: filename.left,
        durationSeconds: metadata.durationSeconds,
      })
    }
  }
  if (taggedArtist !== null && taggedTitle === null) {
    if (comparable(taggedArtist) === comparable(filename.left)) {
      return Object.freeze({
        title: filename.right,
        artist: taggedArtist,
        durationSeconds: metadata.durationSeconds,
      })
    }
    if (comparable(taggedArtist) === comparable(filename.right)) {
      return Object.freeze({
        title: filename.left,
        artist: taggedArtist,
        durationSeconds: metadata.durationSeconds,
      })
    }
  }
  return null
}

function deriveSongLyricsQueries(
  metadata: SongLyricsProbedMetadata,
  sourceName: string,
): readonly SongLyricsQuery[] {
  const certain = deriveSongLyricsQuery(metadata, sourceName)
  if (certain !== null) return Object.freeze([certain])
  if (
    boundedMetadata(metadata.title) !== null
    || boundedMetadata(metadata.artist) !== null
    || !Number.isFinite(metadata.durationSeconds)
    || metadata.durationSeconds < MIN_DURATION_SECONDS
    || metadata.durationSeconds > MAX_DURATION_SECONDS
  ) {
    return Object.freeze([])
  }
  const filename = filenameParts(sourceName)
  if (filename === null) return Object.freeze([])
  const artistFirst = Object.freeze({
    title: filename.right,
    artist: filename.left,
    durationSeconds: metadata.durationSeconds,
  })
  if (comparable(filename.left) === comparable(filename.right)) {
    return Object.freeze([artistFirst])
  }
  // Both filename conventions are common. The provider's strict metadata and
  // duration scoring can disambiguate them, but two credible records remain a
  // user-confirmation case rather than an arbitrary local ordering decision.
  return Object.freeze([
    artistFirst,
    Object.freeze({
      title: filename.left,
      artist: filename.right,
      durationSeconds: metadata.durationSeconds,
    }),
  ])
}

function runProbe(
  sourcePath: string,
  ffprobePath: string,
  signal: AbortSignal,
): Promise<ProbeProcessResult> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(cancellationError())
      return
    }
    let settled = false
    let stdoutBytes = 0
    const chunks: Buffer[] = []
    let timeout: ReturnType<typeof setTimeout> | null = null
    let child: ReturnType<typeof spawn> | null = null
    const cleanup = (): void => {
      if (timeout !== null) clearTimeout(timeout)
      signal.removeEventListener('abort', handleAbort)
    }
    const finishError = (error = new Error(
      'Song lyrics metadata could not be read.',
    )): void => {
      if (settled) return
      settled = true
      cleanup()
      reject(error)
    }
    const handleAbort = (): void => {
      try {
        child?.kill('SIGKILL')
      } catch {
        // The probe may close between cancellation and the kill request.
      }
      finishError(cancellationError())
    }
    try {
      child = spawn(
        ffprobePath,
        [
          '-hide_banner',
          '-loglevel', 'error',
          '-select_streams', 'a:0',
          '-show_entries', [
            'stream=duration',
            'stream_tags=title,artist,album_artist,albumartist',
            'format=duration',
            'format_tags=title,artist,album_artist,albumartist',
          ].join(':'),
          '-of', 'json',
          sourcePath,
        ],
        {
          shell: false,
          stdio: ['ignore', 'pipe', 'ignore'],
          windowsHide: true,
        },
      )
    } catch {
      finishError()
      return
    }
    if (child.stdout === null) {
      finishError()
      return
    }
    signal.addEventListener('abort', handleAbort, { once: true })
    if (signal.aborted) {
      handleAbort()
      return
    }
    timeout = setTimeout(() => {
      try {
        child?.kill('SIGKILL')
      } catch {
        // The process may exit between the deadline and the kill request.
      }
      finishError()
    }, PROBE_TIMEOUT_MS)
    child.stdout.on('data', (chunk: Buffer | string) => {
      const bytes = typeof chunk === 'string'
        ? Buffer.from(chunk, 'utf8')
        : chunk
      stdoutBytes += bytes.byteLength
      if (stdoutBytes > MAX_PROBE_STDOUT_BYTES) {
        try {
          child?.kill('SIGKILL')
        } catch {
          // The bounded parser will fail regardless of this harmless race.
        }
        finishError()
        return
      }
      chunks.push(bytes)
    })
    child.once('error', () => { finishError() })
    child.once('close', (code) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(Object.freeze({
        exitCode: code ?? 1,
        stdout: Buffer.concat(chunks, stdoutBytes),
      }))
    })
  })
}

function caseInsensitiveTag(
  tags: Record<string, unknown>,
  requestedName: string,
): unknown {
  for (const [name, value] of Object.entries(tags)) {
    if (name.toLowerCase() === requestedName) return value
  }
  return null
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function durationCandidate(value: unknown): number | null | undefined {
  const duration = Number(value)
  if (!Number.isFinite(duration)) return undefined
  return duration >= MIN_DURATION_SECONDS && duration <= MAX_DURATION_SECONDS
    ? duration
    : null
}

function metadataTags(value: unknown): Record<string, unknown> {
  return objectRecord(value) ?? {}
}

function firstMetadataTag(
  containers: readonly Record<string, unknown>[],
  names: readonly string[],
): string | null {
  for (const container of containers) {
    for (const name of names) {
      const value = boundedMetadata(caseInsensitiveTag(container, name))
      if (value !== null) return value
    }
  }
  return null
}

/**
 * Select bounded metadata from one FFprobe JSON document.
 *
 * The selected audio stream owns timing because container duration can include
 * cover art or padding. Format tags are preferred for track identity, while
 * stream tags cover containers that attach metadata directly to the audio.
 */
export function parseSongLyricsProbeDocument(
  parsed: unknown,
): SongLyricsProbedMetadata | null {
  const root = objectRecord(parsed)
  if (root === null) return null
  const format = objectRecord(root.format)
  if (format === null) return null
  const rawStreams = root.streams
  if (
    rawStreams !== undefined
    && (!Array.isArray(rawStreams) || rawStreams.length > 1)
  ) {
    return null
  }
  const stream = Array.isArray(rawStreams) && rawStreams.length === 1
    ? objectRecord(rawStreams[0])
    : null
  if (Array.isArray(rawStreams) && rawStreams.length === 1 && stream === null) {
    return null
  }
  const streamDuration = stream === null
    ? undefined
    : durationCandidate(stream.duration)
  // A numeric selected-stream duration is authoritative. Refusing an
  // out-of-policy value prevents a shorter container duration from bypassing
  // the 12-minute admission boundary before the LRCLIB lookup.
  if (streamDuration === null) return null
  const formatDuration = durationCandidate(format.duration)
  if (formatDuration === null) return null
  const durationSeconds = streamDuration ?? formatDuration
  if (durationSeconds === undefined) return null
  const formatTags = metadataTags(format.tags)
  const streamTags = metadataTags(stream?.tags)
  return Object.freeze({
    title: firstMetadataTag(
      [formatTags, streamTags],
      ['title'],
    ),
    artist: (
      firstMetadataTag([formatTags, streamTags], ['artist'])
      ?? firstMetadataTag(
        [formatTags, streamTags],
        ['album_artist', 'albumartist'],
      )
    ),
    durationSeconds,
  })
}

function parseProbeOutput(
  result: ProbeProcessResult,
): SongLyricsProbedMetadata | null {
  if (result.exitCode !== 0) return null
  let decoded: string
  try {
    decoded = new TextDecoder('utf-8', { fatal: true }).decode(result.stdout)
  } catch {
    return null
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(decoded) as unknown
  } catch {
    return null
  }
  return parseSongLyricsProbeDocument(parsed)
}

async function defaultProbe(
  sourcePath: string,
  ffprobePath: string,
  signal: AbortSignal,
): Promise<SongLyricsProbedMetadata | null> {
  return parseProbeOutput(await runProbe(sourcePath, ffprobePath, signal))
}

async function writePrivateFile(
  destination: string,
  content: string,
): Promise<void> {
  let handle: FileHandle | null = null
  try {
    // Exclusive creation prevents another local process from substituting a
    // pre-existing lyrics file between job-root allocation and this write.
    handle = await open(destination, 'wx', 0o600)
    await handle.writeFile(content, { encoding: 'utf8' })
    await handle.sync()
  } finally {
    await handle?.close()
  }
}

async function assertPrivateJobDirectory(jobDirectory: string): Promise<void> {
  if (!path.isAbsolute(jobDirectory)) {
    throw new Error('Song lyrics job directory is invalid.')
  }
  const metadata = await lstat(jobDirectory)
  if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
    throw new Error('Song lyrics job directory is invalid.')
  }
}

/**
 * Resolve lyrics online and materialize only fixed UTF-8 files in a private job.
 *
 * The provider is injectable for deterministic tests. Production uses LRCLIB;
 * misses and low-confidence candidates remain closed outcomes, while transport
 * or file-system failures throw fixed errors for the owning manager to map.
 */
export class SongLyricsAssetPreparer {
  constructor(
    private readonly lookup: SongLyricsLookup = (
      DEFAULT_LYRICS_PROVIDER.lookup.bind(DEFAULT_LYRICS_PROVIDER)
    ),
    private readonly probe: SongLyricsMetadataProbe = defaultProbe,
  ) {}

  /**
   * Prepare a complete lyrics asset set or abort probing, lookup, and cleanup
   * promptly when the Main-owned launch signal is cancelled.
   */
  async prepare(
    input: SongLyricsAssetPreparationInput,
  ): Promise<SongLyricsAssetPreparationResult> {
    throwIfCancelled(input.signal)
    if (
      !path.isAbsolute(input.sourcePath)
      || !path.isAbsolute(input.ffprobePath)
      || input.sourceName.length === 0
      || input.sourceName.length > 255
      || path.basename(input.sourceName) !== input.sourceName
    ) {
      throw new Error('Song lyrics source metadata is invalid.')
    }
    try {
      await assertPrivateJobDirectory(input.jobDirectory)
    } catch {
      throw new Error('Private song lyrics job is unavailable.')
    }
    throwIfCancelled(input.signal)
    let metadata: SongLyricsProbedMetadata | null
    try {
      metadata = await this.probe(
        input.sourcePath,
        input.ffprobePath,
        input.signal,
      )
    } catch {
      throwIfCancelled(input.signal)
      throw new Error('Song lyrics metadata could not be read.')
    }
    throwIfCancelled(input.signal)
    if (metadata === null) return Object.freeze({ status: 'invalid-audio' })
    if (
      !Number.isFinite(metadata.durationSeconds)
      || metadata.durationSeconds < MIN_DURATION_SECONDS
      || metadata.durationSeconds > MAX_DURATION_SECONDS
    ) {
      return Object.freeze({ status: 'invalid-audio' })
    }
    let queries: readonly SongLyricsQuery[]
    if (input.metadataOverride === null) {
      queries = deriveSongLyricsQueries(metadata, input.sourceName)
    } else {
      const title = boundedMetadata(input.metadataOverride.title)
      const artist = boundedMetadata(input.metadataOverride.artist)
      if (title === null || artist === null) {
        throw new Error('Song lyrics source metadata is invalid.')
      }
      // Manual identity wins over tags and filenames, but duration remains
      // probe-owned so a renderer cannot weaken provider matching.
      queries = Object.freeze([Object.freeze({
        title,
        artist,
        durationSeconds: metadata.durationSeconds,
      })])
    }
    if (queries.length === 0) {
      return Object.freeze({ status: 'needs-metadata' })
    }

    const lookupResults: SongLyricsLookupResult[] = []
    try {
      for (const query of queries) {
        throwIfCancelled(input.signal)
        lookupResults.push(await this.lookup(query, input.signal))
      }
    } catch {
      throwIfCancelled(input.signal)
      throw new Error('Online lyrics could not be retrieved safely.')
    }
    const foundByRecordId = new Map<number, Extract<
      SongLyricsLookupResult,
      { status: 'found' }
    >>()
    for (const result of lookupResults) {
      if (result.status === 'found') {
        foundByRecordId.set(result.providerRecord.recordId, result)
      }
    }
    if (foundByRecordId.size > 1) {
      return Object.freeze({ status: 'needs-metadata' })
    }
    const lookup = foundByRecordId.values().next().value
    if (lookup === undefined) {
      return Object.freeze({
        status: lookupResults.some((result) => (
          result.status === 'low-confidence'
        ))
          ? 'low-confidence'
          : 'not-found',
      })
    }

    const manifest: SongLyricsAssetManifest = Object.freeze({
      source: 'lrclib',
      recordId: lookup.providerRecord.recordId,
      confidence: lookup.providerRecord.confidence,
      hasSyncedLyrics: lookup.syncedLyrics !== null,
    })
    const pending: Array<readonly [string, string]> = []
    if (lookup.syncedLyrics !== null) {
      pending.push([LYRICS_LRC_NAME, `${lookup.syncedLyrics}\n`])
    }
    if (lookup.plainLyrics !== null) {
      pending.push([LYRICS_TEXT_NAME, `${lookup.plainLyrics}\n`])
    }
    pending.push([
      LYRICS_MANIFEST_NAME,
      `${JSON.stringify(manifest)}\n`,
    ])

    const created: string[] = []
    try {
      for (const [name, content] of pending) {
        throwIfCancelled(input.signal)
        const destination = path.join(input.jobDirectory, name)
        await writePrivateFile(destination, content)
        created.push(destination)
      }
      throwIfCancelled(input.signal)
    } catch {
      await Promise.allSettled(created.map((destination) => (
        rm(destination, { force: true })
      )))
      throwIfCancelled(input.signal)
      throw new Error('Private song lyrics could not be prepared.')
    }
    return Object.freeze({
      status: 'ready',
      files: Object.freeze(pending.map(([name]) => name)) as (
        SongLyricsAssetsReadyResult['files']
      ),
      manifest,
    })
  }
}
