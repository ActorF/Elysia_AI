/**
 * Coordinate private Song Cover jobs and non-invasive runtime admission.
 *
 * The manager owns native paths, the worker process, temporary output, and
 * long-form playback.  It publishes only the bounded state declared in
 * `song-cover-contracts.ts`, ensuring a compromised renderer cannot select a
 * process, model, output path, or arbitrary command.
 */

import { spawn, type ChildProcessByStdio } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import type { Dirent, Stats } from 'node:fs'
import {
  link,
  lstat,
  mkdir,
  open,
  readdir,
  rename,
  rm,
  type FileHandle,
} from 'node:fs/promises'
import path from 'node:path'
import type { Readable } from 'node:stream'

import type { PreloadMusicPlaybackOwner } from './music-playback-owner.js'
import {
  SongLyricsAssetPreparer,
  type SongLyricsAssetPreparationInput,
  type SongLyricsAssetPreparationResult,
} from './song-cover-lyrics-assets.js'
import {
  parseSongCoverWorkerEvent,
  parseSongLyricsMetadataOverride,
  type SongCoverEngine,
  type SongCoverErrorCode,
  type SongCoverKeyShiftSemitones,
  type SongCoverReadiness,
  type SongLyricsMetadataOverride,
  type SongCoverSourceMode,
  type SongCoverState,
} from './song-cover-contracts.js'

const WORKER_EVENT_PREFIX = 'ELYSIA_SONG_COVER '
const MAX_STDERR_CODE_POINTS = 4_000
const MAX_WORKER_STDOUT_LINE_BYTES = 64 * 1024
const MAX_WORKER_STDOUT_BYTES = 32 * 1024 * 1024
const MAX_PLAYBACK_BYTES = 64 * 1024 * 1024
const MAX_LOSSLESS_OUTPUT_BYTES = 512 * 1024 * 1024
const PRIVATE_SVS_CLEANUP_EVENT_BODY = (
  'ELYSIA_SONG_COVER_CLEANUP {"status":"complete"}'
)
// Python text output uses CRLF on Windows while injected workers and POSIX use
// LF. Keep this boundary closed by allowlisting only those two exact frames;
// trimming arbitrary bytes would let malformed cleanup output pass validation.
const PRIVATE_SVS_CLEANUP_EVENTS = Object.freeze([
  Buffer.from(`${PRIVATE_SVS_CLEANUP_EVENT_BODY}\n`, 'utf8'),
  Buffer.from(`${PRIVATE_SVS_CLEANUP_EVENT_BODY}\r\n`, 'utf8'),
])
const MAX_PRIVATE_SVS_CLEANUP_BYTES = 256
const PRIVATE_SVS_CLEANUP_TIMEOUT_MS = 60_000
const DEFAULT_JOB_TIMEOUT_MS = 2 * 60 * 60 * 1_000
const DEFAULT_PLAYBACK_SETUP_TIMEOUT_MS = 5_000
const OUTPUT_HEADER_BYTES = 64 * 1024
const WORKER_PROGRESS = Object.freeze({
  validating: Object.freeze({
    progressPercent: 5,
    message: 'Reading the selected audio',
  }),
  separating: Object.freeze({
    progressPercent: 15,
    message: 'Preparing vocals and accompaniment',
  }),
  transcribing: Object.freeze({
    progressPercent: 35,
    message: 'Transcribing the source melody and syllables',
  }),
  aligning: Object.freeze({
    progressPercent: 52,
    message: 'Aligning verified lyrics to the detected notes',
  }),
  synthesizing: Object.freeze({
    progressPercent: 58,
    message: 'Singing the verified lyrics with Elysia voice',
  }),
  converting: Object.freeze({
    progressPercent: 55,
    message: 'Converting the vocal melody to Elysia',
  }),
  mixing: Object.freeze({
    progressPercent: 90,
    message: 'Mixing Elysia with the accompaniment',
  }),
  complete: Object.freeze({
    progressPercent: 100,
    message: 'Song Cover ready',
  }),
})
const SUPPORTED_AUDIO_EXTENSIONS = new Set([
  '.aac',
  '.flac',
  '.m4a',
  '.mp3',
  '.ogg',
  '.opus',
  '.wav',
])
const WORKER_STAGE_SEQUENCES = Object.freeze({
  'legacy-svc': Object.freeze([
    'validating',
    'separating',
    'converting',
    'mixing',
    'complete',
  ] as const),
  'lyrics-svs': Object.freeze([
    'validating',
    'separating',
    'transcribing',
    'aligning',
    'synthesizing',
    'mixing',
    'complete',
  ] as const),
})
const SONG_COVER_JOB_ID_PATTERN = (
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u
)
const SUPPORTED_KEY_SHIFTS: readonly SongCoverKeyShiftSemitones[] = Object.freeze([
  -2,
  -1,
  0,
  1,
  2,
])
const PRIVATE_CLEANUP_ERROR = (
  'Private Song Cover audio could not be cleared. Close Elysia and try again.'
)
const DEFAULT_LYRICS_ASSET_PREPARER = new SongLyricsAssetPreparer()
const LYRICS_START_ERRORS = Object.freeze({
  unavailable: 'Lyrics-driven singing is not available in this build.',
  invalidAudio: 'Choose valid audio no longer than 12 minutes.',
  metadata: 'Song title and artist metadata are required for lyrics-driven singing.',
  notFound: 'No matching online lyrics were found for this song.',
  lowConfidence: 'Online lyrics did not match this song confidently enough.',
  noSync: 'No synchronized lyrics were found for this song.',
  network: 'Online lyrics could not be retrieved. Check your connection and try again.',
  preparation: 'Lyrics-driven song preparation could not be completed safely.',
})
const AVAILABLE_READINESS: SongCoverReadiness = Object.freeze({
  status: 'available',
  reason: null,
})
const PACKAGED_READINESS: SongCoverReadiness = Object.freeze({
  status: 'unavailable',
  reason: 'not-included-in-build',
})
const LOCAL_RUNTIME_READINESS: SongCoverReadiness = Object.freeze({
  status: 'unavailable',
  reason: 'local-runtime-unavailable',
})

type RemovePath = (
  target: string,
  options: { force?: boolean; recursive?: boolean },
) => Promise<void>

type TerminateProcessTree = (
  child: ChildProcessByStdio<null, Readable, Readable>,
) => Promise<boolean>

type PrepareSongLyricsAssets = (
  input: SongLyricsAssetPreparationInput,
) => Promise<SongLyricsAssetPreparationResult>

type SpawnSongCoverWorker = (
  command: string,
  arguments_: string[],
  options: {
    readonly cwd: string
    readonly env: NodeJS.ProcessEnv
    readonly shell: false
    readonly stdio: ['ignore', 'pipe', 'pipe']
    readonly windowsHide: true
  },
) => ChildProcessByStdio<null, Readable, Readable>

interface SongCoverExportOperations {
  readonly link: (existingPath: string, newPath: string) => Promise<void>
  readonly read: (
    handle: FileHandle,
    buffer: Buffer,
    offset: number,
    length: number,
    position: number,
  ) => Promise<{ readonly bytesRead: number }>
  readonly rename: (oldPath: string, newPath: string) => Promise<void>
  readonly sync: (handle: FileHandle) => Promise<void>
  readonly write: (
    handle: FileHandle,
    buffer: Buffer,
    offset: number,
    length: number,
    position: number,
  ) => Promise<{ readonly bytesWritten: number }>
}

const DEFAULT_EXPORT_OPERATIONS: SongCoverExportOperations = Object.freeze({
  link,
  read: async (
    handle: FileHandle,
    buffer: Buffer,
    offset: number,
    length: number,
    position: number,
  ) => (
    handle.read(buffer, offset, length, position)
  ),
  rename,
  sync: async (handle: FileHandle) => { await handle.sync() },
  write: async (
    handle: FileHandle,
    buffer: Buffer,
    offset: number,
    length: number,
    position: number,
  ) => (
    handle.write(buffer, offset, length, position)
  ),
})

function defaultSpawnWorker(
  command: string,
  arguments_: string[],
  options: Parameters<SpawnSongCoverWorker>[2],
): ChildProcessByStdio<null, Readable, Readable> {
  return spawn(command, arguments_, options)
}

interface OutputFileIdentity {
  readonly birthtimeMs: number
  readonly ctimeMs: number
  readonly dev: number
  readonly ino: number
  readonly mtimeMs: number
  readonly size: number
}

interface CompletedOutputIdentity {
  readonly jobId: string
  readonly mp3: OutputFileIdentity
  readonly wav: OutputFileIdentity
}

interface ActiveSongCoverJob {
  readonly id: string
  readonly directory: string
  readonly process: ChildProcessByStdio<null, Readable, Readable>
  readonly engine: SongCoverEngine
  cancelled: boolean
  deadline: ReturnType<typeof setTimeout> | null
  nextStageIndex: number
  protocolFailed: boolean
  abortSettlement: Promise<void> | null
  sawComplete: boolean
  settled: boolean
  stderr: string
  stdoutBytes: number
  stdoutLine: Buffer
  cancellation: Promise<SongCoverState> | null
  termination: Promise<boolean> | null
  terminationUncertain: boolean
}

interface SongPlaybackPreferences {
  readonly outputDeviceId: string | null
  readonly volumePercent: number
}

interface SongCoverRuntimePaths {
  readonly python: string
  readonly worker: string
  readonly svsWorker: string
  readonly ffmpeg: string
  readonly ffprobe: string
  readonly demucsSite: string
  readonly torchHome: string
  readonly rvcRoot: string
  readonly rvcModel: string
  readonly rvcIndex: string
}

interface SongCoverAccompanimentInput {
  readonly path: string
  readonly name: string
}

function outputIdentity(metadata: Stats): OutputFileIdentity {
  return Object.freeze({
    birthtimeMs: metadata.birthtimeMs,
    ctimeMs: metadata.ctimeMs,
    dev: metadata.dev,
    ino: metadata.ino,
    mtimeMs: metadata.mtimeMs,
    size: metadata.size,
  })
}

function sameOutputIdentity(
  left: OutputFileIdentity,
  right: OutputFileIdentity,
): boolean {
  return (
    left.birthtimeMs === right.birthtimeMs
    && left.ctimeMs === right.ctimeMs
    && left.dev === right.dev
    && left.ino === right.ino
    && left.mtimeMs === right.mtimeMs
    && left.size === right.size
  )
}

function sameStoredFile(
  left: OutputFileIdentity,
  right: OutputFileIdentity,
): boolean {
  // Creating a hard-link or renaming a file may legitimately update ctime.
  // Device/inode plus immutable copy size and timestamps still identify the
  // staged or backed-up file without rejecting those publication operations.
  return (
    left.birthtimeMs === right.birthtimeMs
    && left.dev === right.dev
    && left.ino === right.ino
    && left.mtimeMs === right.mtimeMs
    && left.size === right.size
  )
}

function asciiEquals(bytes: Uint8Array, offset: number, value: string): boolean {
  if (offset < 0 || offset + value.length > bytes.byteLength) {
    return false
  }
  for (let index = 0; index < value.length; index += 1) {
    if (bytes[offset + index] !== value.charCodeAt(index)) {
      return false
    }
  }
  return true
}

function isCanonicalSongCoverWav(
  header: Uint8Array,
  fileSize: number,
): boolean {
  if (
    header.byteLength < 44
    || !asciiEquals(header, 0, 'RIFF')
    || !asciiEquals(header, 8, 'WAVE')
  ) {
    return false
  }
  const view = new DataView(
    header.buffer,
    header.byteOffset,
    header.byteLength,
  )
  if (view.getUint32(4, true) + 8 !== fileSize) {
    return false
  }
  let offset = 12
  let canonicalFormat = false
  let canonicalData = false
  // RIFF permits metadata chunks before audio. Walking their declared sizes
  // accepts normal FFmpeg metadata without trusting a fixed header offset,
  // while every addition is range-checked against both the prefix and file.
  while (offset + 8 <= header.byteLength) {
    const chunkBytes = view.getUint32(offset + 4, true)
    const chunkData = offset + 8
    const paddedChunkEnd = chunkData + chunkBytes + (chunkBytes & 1)
    if (paddedChunkEnd > fileSize) {
      return false
    }
    if (asciiEquals(header, offset, 'fmt ')) {
      if (chunkBytes < 16 || chunkData + 16 > header.byteLength) {
        return false
      }
      canonicalFormat = (
        view.getUint16(chunkData, true) === 1
        && view.getUint16(chunkData + 2, true) === 2
        && view.getUint32(chunkData + 4, true) === 44_100
        && view.getUint32(chunkData + 8, true) === 176_400
        && view.getUint16(chunkData + 12, true) === 4
        && view.getUint16(chunkData + 14, true) === 16
      )
    } else if (asciiEquals(header, offset, 'data')) {
      canonicalData = chunkBytes > 0 && chunkBytes % 4 === 0
      break
    }
    if (paddedChunkEnd > header.byteLength) {
      return false
    }
    offset = paddedChunkEnd
  }
  return canonicalFormat && canonicalData
}

function isCanonicalSongCoverMp3(header: Uint8Array): boolean {
  let frameOffset = 0
  if (asciiEquals(header, 0, 'ID3')) {
    if (
      header.byteLength < 10
      || header[6] >= 0x80
      || header[7] >= 0x80
      || header[8] >= 0x80
      || header[9] >= 0x80
    ) {
      return false
    }
    const tagBytes = (
      (header[6] << 21)
      | (header[7] << 14)
      | (header[8] << 7)
      | header[9]
    )
    frameOffset = 10 + tagBytes + ((header[5] & 0x10) === 0 ? 0 : 10)
  }
  if (frameOffset + 4 > header.byteLength) {
    return false
  }
  const first = header[frameOffset]
  const second = header[frameOffset + 1]
  const third = header[frameOffset + 2]
  const fourth = header[frameOffset + 3]
  const bitrateIndex = (third >> 4) & 0x0f
  return (
    first === 0xff
    && (second & 0xe0) === 0xe0
    && ((second >> 3) & 0x03) === 0x03
    && ((second >> 1) & 0x03) === 0x01
    && bitrateIndex > 0
    && bitrateIndex < 0x0f
    && ((third >> 2) & 0x03) === 0
    && ((fourth >> 6) & 0x03) !== 0x03
  )
}

/** Fixed renderer-safe failure from lyrics-driven admission in Electron Main. */
export class SongCoverStartError extends Error {
  /** Create one closed failure category with Main-owned fallback text. */
  constructor(
    readonly code: SongCoverErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'SongCoverStartError'
  }
}

/** Main-owned coordinator for generation, playback, cancellation, and export. */
export class SongCoverManager {
  private state: SongCoverState = Object.freeze({
    revision: 0,
    jobId: null,
    stage: 'idle',
    sourceName: null,
    accompanimentName: null,
    sourceMode: null,
    engine: null,
    keyShiftSemitones: null,
    progressPercent: 0,
    outputAvailable: false,
    message: null,
    errorCode: null,
    error: null,
  })
  private activeJob: ActiveSongCoverJob | null = null
  private completedOutput: CompletedOutputIdentity | null = null
  private outputJobId: string | null = null
  private playbackGeneration = 0
  private playbackLaunchJobId: string | null = null
  private shuttingDown = false
  private shutdownOperation: Promise<void> | null = null
  private launchBarrier: Promise<void> | null = null
  private launchAbortController: AbortController | null = null
  private launchCancellationRequested = false
  private launchJobId: string | null = null
  private readonly pendingJobDirectories = new Map<string, string>()
  private readonly pendingSvsPrivateJobIds = new Set<string>()
  private readonly startupJobsRoot: string
  private readonly exportOperations: SongCoverExportOperations
  private startupCleanup: Promise<boolean> | null = null
  private startupCleanupRequired = true

  /** Configure Main-owned runtime roots, callbacks, timeout, and cleanup I/O. */
  constructor(
    private readonly projectRoot: string,
    private readonly getActiveDataRoot: () => string,
    private readonly getPlaybackOwner: () => PreloadMusicPlaybackOwner | null,
    private readonly beforePlayback: () => void,
    private readonly getPlaybackPreferences: () => Promise<SongPlaybackPreferences>,
    private readonly publish: (state: SongCoverState) => void,
    private readonly jobTimeoutMs: number = DEFAULT_JOB_TIMEOUT_MS,
    private readonly removePath: RemovePath = rm,
    private readonly terminateProcessTree: TerminateProcessTree | null = null,
    private readonly playbackSetupTimeoutMs: number = (
      DEFAULT_PLAYBACK_SETUP_TIMEOUT_MS
    ),
    private readonly prepareLyricsAssets: PrepareSongLyricsAssets | null = (
      DEFAULT_LYRICS_ASSET_PREPARER.prepare.bind(DEFAULT_LYRICS_ASSET_PREPARER)
    ),
    private readonly spawnWorker: SpawnSongCoverWorker = defaultSpawnWorker,
    private readonly privateSvsCleanupTimeoutMs: number = (
      PRIVATE_SVS_CLEANUP_TIMEOUT_MS
    ),
    exportOperations: Partial<SongCoverExportOperations> = {},
  ) {
    if (!Number.isSafeInteger(jobTimeoutMs) || jobTimeoutMs <= 0) {
      throw new Error('Song Cover job timeout is invalid.')
    }
    if (
      !Number.isSafeInteger(playbackSetupTimeoutMs)
      || playbackSetupTimeoutMs <= 0
    ) {
      throw new Error('Song Cover playback setup timeout is invalid.')
    }
    if (
      !Number.isSafeInteger(privateSvsCleanupTimeoutMs)
      || privateSvsCleanupTimeoutMs <= 0
    ) {
      throw new Error('Song Cover private cleanup timeout is invalid.')
    }
    this.startupJobsRoot = path.join(
      getActiveDataRoot(),
      'audio',
      'song-covers',
    )
    this.exportOperations = Object.freeze({
      ...DEFAULT_EXPORT_OPERATIONS,
      ...exportOperations,
    })
    // Start eagerly so crash-left private audio is cleared even when the Song
    // Cover UI is never opened. The tracked lease also blocks a data-root move
    // until startup cleanup has settled.
    void this.beginStartupCleanup()
  }

  /** Return the latest immutable renderer-safe lifecycle snapshot. */
  getState(): SongCoverState {
    return this.state
  }

  /**
   * Check whether Main may offer a new cover without executing local tooling.
   *
   * The current installer deliberately omits Python workers and model runtimes,
   * so every packaged build fails closed even if unrelated files happen to be
   * present beside it. Development checks only regular core files; private WSL
   * and model integrity remains the worker's per-job responsibility so this
   * preflight never starts WSL, Python, FFmpeg, a model, or network activity.
   */
  async getReadiness(packagedBuild: boolean): Promise<SongCoverReadiness> {
    if (packagedBuild) {
      return PACKAGED_READINESS
    }
    const runtime = this.runtimePaths()
    const requiredFiles = [
      runtime.python,
      runtime.worker,
      runtime.svsWorker,
      runtime.ffmpeg,
      runtime.ffprobe,
      path.join(this.projectRoot, 'scripts', 'song_rvc_runtime.py'),
      path.join(this.projectRoot, 'scripts', 'song_svs_wsl_bridge.py'),
      path.join(this.projectRoot, 'scripts', 'song_svs_runtime.py'),
      path.join(this.projectRoot, 'scripts', 'song_lyrics_alignment.py'),
    ]
    try {
      const metadata = await Promise.all(requiredFiles.map((file) => lstat(file)))
      if (metadata.some((entry) => !entry.isFile() || entry.isSymbolicLink())) {
        return LOCAL_RUNTIME_READINESS
      }
    } catch {
      return LOCAL_RUNTIME_READINESS
    }
    return AVAILABLE_READINESS
  }

  /** Report work that should suppress maintenance and presence reminders. */
  hasActiveWork(): boolean {
    return (
      this.startupCleanup !== null
      || this.launchBarrier !== null
      || this.activeJob !== null
      || this.state.stage === 'playing'
    )
  }

  /** Report retained private artifacts that must not cross a data-root move. */
  hasManagedOutput(): boolean {
    return (
      this.startupCleanupRequired
      || this.outputJobId !== null
      || this.pendingJobDirectories.size > 0
      || this.pendingSvsPrivateJobIds.size > 0
    )
  }

  /**
   * Begin one native cover job from a full song or isolated vocal/accompaniment.
   *
   * Main owns every path and accepts only a closed key adjustment plus an
   * optional bounded lookup identity; neither reaches the singing worker.
   * Lyrics-driven SVS is the default, so legacy conversion must be explicit.
   */
  async start(
    sourcePath: string,
    sourceName: string,
    keyShiftSemitones: SongCoverKeyShiftSemitones = 0,
    accompaniment: SongCoverAccompanimentInput | null = null,
    engine: SongCoverEngine = 'lyrics-svs',
    lyricsMetadataOverride: SongLyricsMetadataOverride | null = null,
  ): Promise<SongCoverState> {
    if (this.shuttingDown) {
      throw new Error('Song Cover is shutting down.')
    }
    if (this.launchBarrier !== null || this.activeJob !== null) {
      throw new Error('Wait for the current Song Cover to finish or cancel it.')
    }
    const extension = path.extname(sourceName).toLowerCase()
    if (
      sourceName.length === 0
      || sourceName.length > 255
      || path.basename(sourceName) !== sourceName
      || !SUPPORTED_AUDIO_EXTENSIONS.has(extension)
    ) {
      throw new Error('Choose a supported local audio file.')
    }
    if (!SUPPORTED_KEY_SHIFTS.includes(keyShiftSemitones)) {
      throw new Error('Song Cover key adjustment is invalid.')
    }
    if (engine !== 'lyrics-svs' && engine !== 'legacy-svc') {
      throw new Error('Song Cover engine is invalid.')
    }
    const validatedLyricsMetadataOverride = parseSongLyricsMetadataOverride(
      lyricsMetadataOverride,
    )
    if (
      validatedLyricsMetadataOverride === undefined
      || (engine === 'legacy-svc' && validatedLyricsMetadataOverride !== null)
    ) {
      throw new Error('Song Cover lyrics metadata is invalid.')
    }
    if (accompaniment !== null) {
      const accompanimentExtension = path.extname(
        accompaniment.name,
      ).toLowerCase()
      if (
        accompaniment.name.length === 0
        || accompaniment.name.length > 255
        || path.basename(accompaniment.name) !== accompaniment.name
        || !SUPPORTED_AUDIO_EXTENSIONS.has(accompanimentExtension)
        || path.resolve(accompaniment.path).toLowerCase()
          === path.resolve(sourcePath).toLowerCase()
      ) {
        throw new Error('Choose a different supported accompaniment file.')
      }
    }

    const sourceMode: SongCoverSourceMode = accompaniment === null
      ? 'song'
      : 'stems'

    const jobId = randomUUID()
    const jobsRoot = path.join(
      this.getActiveDataRoot(),
      'audio',
      'song-covers',
    )
    const jobDirectory = path.join(jobsRoot, jobId)
    let releaseLaunchBarrier!: () => void
    const launchBarrier = new Promise<void>((resolve) => {
      releaseLaunchBarrier = resolve
    })
    this.launchBarrier = launchBarrier
    const launchAbortController = new AbortController()
    this.launchAbortController = launchAbortController
    this.launchJobId = jobId
    this.launchCancellationRequested = false
    const replacingOutput = this.outputJobId !== null
    try {
      if (!this.stopPlaybackUnconditionally()) {
        throw new Error('Song playback could not be stopped safely.')
      }
      if (!replacingOutput) {
        this.replaceState({
          jobId,
          stage: 'validating',
          sourceName,
          accompanimentName: accompaniment?.name ?? null,
          sourceMode,
          engine,
          keyShiftSemitones,
          progressPercent: 1,
          outputAvailable: false,
          message: 'Preparing the selected audio',
          error: null,
        })
      }
      await this.ensureStartupCleanup()
      await this.retryPendingCleanup()
      try {
        await this.removeOutputDirectory()
      } catch {
        throw new Error(PRIVATE_CLEANUP_ERROR)
      }
      if (replacingOutput) {
        // Keep the prior result addressable until its exact directory deletion
        // succeeds. Publishing the replacement first would hide still-valid
        // audio if Windows retains a file handle or cleanup otherwise fails.
        this.replaceState({
          jobId,
          stage: 'validating',
          sourceName,
          accompanimentName: accompaniment?.name ?? null,
          sourceMode,
          engine,
          keyShiftSemitones,
          progressPercent: 1,
          outputAvailable: false,
          message: 'Preparing the selected audio',
          error: null,
        })
      }
      await mkdir(jobsRoot, { recursive: true })
      const runtime = this.runtimePaths()
      if (engine === 'lyrics-svs') {
        await mkdir(jobDirectory, { recursive: false, mode: 0o700 })
        await this.prepareLyricsDrivenAssets({
          jobDirectory,
          sourcePath,
          sourceName,
          ffprobePath: runtime.ffprobe,
          metadataOverride: validatedLyricsMetadataOverride,
          signal: launchAbortController.signal,
        })
      }
      if (this.launchCancellationRequested) {
        if (!await this.removeJobDirectory(jobId, jobDirectory)) {
          throw new Error(PRIVATE_CLEANUP_ERROR)
        }
        this.replaceState({
          stage: 'cancelled',
          progressPercent: 0,
          outputAvailable: false,
          message: 'Song Cover cancelled',
          error: null,
        })
        return this.state
      }
      if (this.shuttingDown) {
        throw new Error('Song Cover is shutting down.')
      }
      const sourceArguments = accompaniment === null
        ? ['--source-mode', 'song', '--input', sourcePath]
        : [
            '--source-mode', 'stems',
            '--vocal-input', sourcePath,
            '--accompaniment-input', accompaniment.path,
          ]
      const child = this.spawnWorker(
        runtime.python,
        [
          engine === 'lyrics-svs' ? runtime.svsWorker : runtime.worker,
          ...sourceArguments,
          '--key-shift-semitones', String(keyShiftSemitones),
          '--job-dir', jobDirectory,
          '--ffmpeg', runtime.ffmpeg,
          '--ffprobe', runtime.ffprobe,
          '--demucs-site', runtime.demucsSite,
          '--torch-home', runtime.torchHome,
          '--job-token', jobId,
          ...(engine === 'legacy-svc'
            ? [
                '--rvc-root', runtime.rvcRoot,
                '--rvc-model', runtime.rvcModel,
                '--rvc-index', runtime.rvcIndex,
              ]
            : []),
        ],
        {
          cwd: this.projectRoot,
          env: this.workerEnvironment(runtime, jobsRoot, jobDirectory),
          shell: false,
          stdio: ['ignore', 'pipe', 'pipe'],
          windowsHide: true,
        },
      )
      const job: ActiveSongCoverJob = {
        id: jobId,
        directory: jobDirectory,
        process: child,
        engine,
        cancelled: false,
        deadline: null,
        nextStageIndex: 0,
        protocolFailed: false,
        abortSettlement: null,
        sawComplete: false,
        settled: false,
        stderr: '',
        stdoutBytes: 0,
        stdoutLine: Buffer.alloc(0),
        cancellation: null,
        termination: null,
        terminationUncertain: false,
      }
      this.activeJob = job
      job.deadline = setTimeout(() => {
        void this.handleJobTimeout(job)
      }, this.jobTimeoutMs)
      child.stdout.on('data', (chunk: Buffer | string) => {
        this.consumeWorkerChunk(
          job,
          typeof chunk === 'string' ? Buffer.from(chunk, 'utf8') : chunk,
        )
      })
      child.stderr.setEncoding('utf8')
      child.stderr.on('data', (chunk: string) => {
        job.stderr = (job.stderr + chunk).slice(-MAX_STDERR_CODE_POINTS)
      })
      child.once('error', () => {
        void this.finishJob(job, 1)
      })
      child.once('close', (code) => {
        this.finishWorkerOutput(job)
        void this.finishJob(job, code ?? 1)
      })
      return this.state
    } catch (error) {
      const cleanupSucceeded = await this.removeJobDirectory(jobId, jobDirectory)
      if (
        this.launchCancellationRequested
        && !this.shuttingDown
        && cleanupSucceeded
      ) {
        this.replaceState({
          stage: 'cancelled',
          progressPercent: 0,
          outputAvailable: false,
          message: 'Song Cover cancelled',
          error: null,
        })
        return this.state
      }
      const cleanupFailed = (
        !cleanupSucceeded
        || (error instanceof Error && error.message === PRIVATE_CLEANUP_ERROR)
      )
      const failureMessage = cleanupFailed
        ? PRIVATE_CLEANUP_ERROR
        : error instanceof SongCoverStartError
          ? error.message
          : 'Song Cover could not be started. Check the local singing runtime.'
      const failureCode: SongCoverErrorCode | null = cleanupFailed
        ? null
        : error instanceof SongCoverStartError
          ? error.code
          : 'singing-runtime'
      if (!this.shuttingDown && this.state.jobId === jobId) {
        this.replaceState({
          stage: 'error',
          progressPercent: 0,
          outputAvailable: false,
          message: null,
          errorCode: failureCode,
          error: failureMessage,
        })
      }
      if (this.shuttingDown) {
        throw new Error('Song Cover is shutting down.', { cause: error })
      }
      if (error instanceof SongCoverStartError && cleanupSucceeded) {
        throw error
      }
      throw new Error(failureMessage, { cause: error })
    } finally {
      if (this.launchBarrier === launchBarrier) {
        this.launchBarrier = null
      }
      if (this.launchAbortController === launchAbortController) {
        this.launchAbortController = null
      }
      if (this.launchJobId === jobId) {
        this.launchJobId = null
        this.launchCancellationRequested = false
      }
      releaseLaunchBarrier()
    }
  }

  /** Require synchronized private lyrics before admitting the SVS worker. */
  private async prepareLyricsDrivenAssets(
    input: SongLyricsAssetPreparationInput,
  ): Promise<void> {
    if (this.prepareLyricsAssets === null) {
      throw new SongCoverStartError(
        'singing-runtime',
        LYRICS_START_ERRORS.unavailable,
      )
    }
    let result: SongLyricsAssetPreparationResult
    try {
      result = await this.prepareLyricsAssets(input)
    } catch (error) {
      if (input.signal.aborted) throw error
      if (
        error instanceof Error
        && error.message === 'Song lyrics metadata could not be read.'
      ) {
        throw new SongCoverStartError(
          'invalid-audio',
          LYRICS_START_ERRORS.invalidAudio,
        )
      }
      if (
        error instanceof Error
        && error.message === 'Online lyrics could not be retrieved safely.'
      ) {
        throw new SongCoverStartError(
          'lyrics-network',
          LYRICS_START_ERRORS.network,
        )
      }
      throw new SongCoverStartError(
        'singing-runtime',
        LYRICS_START_ERRORS.preparation,
      )
    }
    if (result.status === 'invalid-audio') {
      throw new SongCoverStartError(
        'invalid-audio',
        LYRICS_START_ERRORS.invalidAudio,
      )
    }
    if (result.status === 'needs-metadata') {
      throw new SongCoverStartError(
        'lyrics-needs-metadata',
        LYRICS_START_ERRORS.metadata,
      )
    }
    if (result.status === 'not-found') {
      throw new SongCoverStartError(
        'lyrics-no-match',
        LYRICS_START_ERRORS.notFound,
      )
    }
    if (result.status === 'low-confidence') {
      throw new SongCoverStartError(
        'lyrics-no-match',
        LYRICS_START_ERRORS.lowConfidence,
      )
    }
    if (result.status !== 'ready') {
      throw new SongCoverStartError(
        'singing-runtime',
        LYRICS_START_ERRORS.preparation,
      )
    }
    if (!result.files.includes('lyrics.lrc')) {
      throw new SongCoverStartError(
        'lyrics-no-sync',
        LYRICS_START_ERRORS.noSync,
      )
    }
  }

  /** Cancel only the active worker and discard its incomplete private output. */
  async cancel(jobId: string): Promise<SongCoverState> {
    let job = this.activeJob
    if (
      job === null
      && this.launchBarrier !== null
      && this.launchJobId === jobId
    ) {
      const pendingLaunch = this.launchBarrier
      this.launchCancellationRequested = true
      this.launchAbortController?.abort()
      await pendingLaunch
      job = this.activeJob
      if (job === null && this.state.jobId === jobId) {
        return this.state
      }
    }
    if (job === null || job.id !== jobId) {
      throw new Error('Song Cover job is no longer active.')
    }
    if (job.abortSettlement !== null) {
      await job.abortSettlement
      return this.state
    }
    if (job.cancellation !== null) {
      return job.cancellation
    }
    const cancellation = this.cancelActiveJob(job)
    job.cancellation = cancellation
    return cancellation
  }

  private async cancelActiveJob(
    job: ActiveSongCoverJob,
  ): Promise<SongCoverState> {
    job.cancelled = true
    const playbackStopped = this.stopPlaybackUnconditionally()
    this.playbackLaunchJobId = null
    this.completedOutput = null
    this.outputJobId = null
    this.clearJobDeadline(job)
    const terminated = await this.terminateJobProcess(job)
    if (!terminated) {
      job.terminationUncertain = true
      this.replaceState({
        stage: 'error',
        progressPercent: this.state.progressPercent,
        outputAvailable: false,
        message: null,
        error: 'Song Cover could not be stopped safely. Restart Elysia after checking Task Manager.',
      })
      throw new Error('Song Cover process-tree termination could not be confirmed.')
    }
    const cleanupSucceeded = await this.removeIncompleteJobArtifacts(job)
    if (this.activeJob === job) {
      this.activeJob = null
    }
    if (!playbackStopped) {
      this.replaceState({
        stage: 'error',
        progressPercent: 0,
        outputAvailable: false,
        message: null,
        error: 'Song playback could not be stopped safely. Restart Elysia.',
      })
      throw new Error('Song playback could not be stopped safely.')
    }
    if (!cleanupSucceeded) {
      this.replaceState({
        stage: 'error',
        progressPercent: 0,
        outputAvailable: false,
        message: null,
        error: PRIVATE_CLEANUP_ERROR,
      })
      throw new Error(PRIVATE_CLEANUP_ERROR)
    }
    this.replaceState({
      stage: 'cancelled',
      progressPercent: 0,
      outputAvailable: false,
      message: 'Song Cover cancelled',
      error: null,
    })
    return this.state
  }

  /** Start or restart playback of the completed private MP3 preview. */
  async play(jobId: string): Promise<SongCoverState> {
    if (
      this.outputJobId !== jobId
      || this.state.jobId !== jobId
      || !this.state.outputAvailable
      || this.completedOutput?.jobId !== jobId
    ) {
      throw new Error('Create a Song Cover before playing it.')
    }
    if (this.playbackLaunchJobId === jobId) {
      return this.state
    }
    if (this.playbackLaunchJobId !== null) {
      throw new Error('Song playback is already starting.')
    }
    const owner = this.getPlaybackOwner()
    if (owner === null) {
      throw new Error('Song playback is not available in this window.')
    }
    this.playbackLaunchJobId = jobId
    let outputReadFailed = false
    try {
      this.beforePlayback()
      if (!this.stopPlaybackUnconditionally()) {
        throw new Error('Song playback could not be stopped safely.')
      }
      const generation = ++this.playbackGeneration
      let bytes: Uint8Array
      try {
        bytes = await this.readValidatedPlayback(jobId)
      } catch {
        outputReadFailed = true
        throw new Error('Song Cover preview validation failed.')
      }
      const preferences = await this.loadPlaybackPreferences()
      if (
        generation !== this.playbackGeneration
        || this.shuttingDown
        || this.outputJobId !== jobId
      ) {
        return this.state
      }
      this.replaceState({
        stage: 'playing',
        progressPercent: 100,
        outputAvailable: true,
        message: 'Elysia is singing',
        error: null,
      })
      void owner.play(
        bytes,
        preferences.outputDeviceId,
        preferences.volumePercent,
      ).then(() => {
        if (generation === this.playbackGeneration && !this.shuttingDown) {
          this.replaceReadyState('Song Cover ready')
        }
      }).catch(() => {
        if (generation === this.playbackGeneration && !this.shuttingDown) {
          this.replaceState({
            stage: 'error',
            progressPercent: 100,
            outputAvailable: true,
            message: null,
            error: 'The Song Cover could not be played.',
          })
        }
      })
      return this.state
    } catch {
      if (outputReadFailed && this.outputJobId === jobId) {
        const outputDirectory = this.outputDirectory(jobId)
        this.completedOutput = null
        const cleanupSucceeded = await this.removeJobDirectory(
          jobId,
          outputDirectory,
        )
        if (this.outputJobId === jobId) {
          this.outputJobId = null
        }
        if (this.state.jobId === jobId && this.state.outputAvailable) {
          this.replaceState({
            stage: 'error',
            progressPercent: 0,
            outputAvailable: false,
            message: null,
            error: cleanupSucceeded
              ? 'The Song Cover preview is unavailable.'
              : PRIVATE_CLEANUP_ERROR,
          })
        }
      }
      throw new Error('The Song Cover could not be played.')
    } finally {
      if (this.playbackLaunchJobId === jobId) {
        this.playbackLaunchJobId = null
      }
    }
  }

  /** Stop only song playback while retaining the generated cover for replay. */
  stopPlayback(jobId: string): SongCoverState {
    if (this.state.jobId !== jobId || this.outputJobId !== jobId) {
      throw new Error('Song Cover job is no longer available.')
    }
    if (!this.stopPlaybackUnconditionally()) {
      throw new Error('Song playback could not be stopped safely.')
    }
    return this.state
  }

  /** Fail an active clip when its exact trusted Preload owner disconnects. */
  handlePlaybackOwnerDisconnected(): void {
    this.playbackGeneration += 1
    this.playbackLaunchJobId = null
    if (this.state.stage === 'playing') {
      this.replaceState({
        stage: 'error',
        progressPercent: 100,
        outputAvailable: this.outputJobId !== null,
        message: null,
        error: 'Song playback connection was lost. Restart Elysia before replaying it.',
      })
    }
  }

  private stopPlaybackUnconditionally(): boolean {
    this.playbackGeneration += 1
    const stopped = this.getPlaybackOwner()?.cancel() !== false
    if (!stopped) {
      if (this.state.stage === 'playing') {
        this.replaceState({
          stage: 'error',
          progressPercent: 100,
          outputAvailable: this.outputJobId !== null,
          message: null,
          error: 'Song playback connection was lost. Restart Elysia to stop it safely.',
        })
      }
      return false
    }
    if (this.state.stage === 'playing') {
      this.replaceReadyState('Song Cover ready')
    }
    return true
  }

  /** Bound Backend preference lookup after the long-running worker has closed. */
  private loadPlaybackPreferences(): Promise<SongPlaybackPreferences> {
    return new Promise((resolve, reject) => {
      let settled = false
      const finish = (
        outcome: 'rejected' | 'resolved',
        value: SongPlaybackPreferences | unknown,
      ): void => {
        if (settled) {
          return
        }
        settled = true
        clearTimeout(timeout)
        if (outcome === 'resolved') {
          resolve(value as SongPlaybackPreferences)
        } else {
          reject(value)
        }
      }
      const timeout = setTimeout(() => {
        finish(
          'rejected',
          new Error('Song Cover playback preferences timed out.'),
        )
      }, this.playbackSetupTimeoutMs)
      // Starting from a resolved Promise also captures a synchronous exception
      // from an injected/provider callback while continuing to observe any
      // operation that settles after the timeout wins.
      void Promise.resolve()
        .then(() => this.getPlaybackPreferences())
        .then(
          (preferences) => { finish('resolved', preferences) },
          (error: unknown) => { finish('rejected', error) },
        )
    })
  }

  /** Copy the completed lossless WAV to one Main-selected native destination. */
  async exportWav(jobId: string, destinationPath: string): Promise<void> {
    if (
      this.outputJobId !== jobId
      || this.state.jobId !== jobId
      || !this.state.outputAvailable
    ) {
      throw new Error('Create a Song Cover before exporting it.')
    }
    try {
      await this.copyOutputWav(jobId, destinationPath)
    } catch {
      throw new Error('The Song Cover could not be exported.')
    }
  }

  /** Release completed temporary audio before the managed audio category clears. */
  async discardOutput(): Promise<void> {
    if (this.launchBarrier !== null || this.activeJob !== null) {
      throw new Error('Wait for the active Song Cover to finish or cancel it.')
    }
    if (!this.stopPlaybackUnconditionally()) {
      throw new Error('Song playback could not be stopped safely.')
    }
    try {
      await this.ensureStartupCleanup()
      await this.retryPendingCleanup()
      await this.removeOutputDirectory()
    } catch {
      throw new Error(PRIVATE_CLEANUP_ERROR)
    }
    this.replaceState({
      jobId: null,
      sourceName: null,
      accompanimentName: null,
      sourceMode: null,
      engine: null,
      keyShiftSemitones: null,
      stage: 'idle',
      progressPercent: 0,
      outputAvailable: false,
      message: null,
      error: null,
    })
  }

  private outputDirectory(jobId: string): string {
    return path.join(
      this.getActiveDataRoot(),
      'audio',
      'song-covers',
      jobId,
    )
  }

  private async openValidatedOutput(
    filePath: string,
    maximumBytes: number,
    expected: OutputFileIdentity | null,
    format: 'mp3' | 'wav',
  ): Promise<{ handle: FileHandle; identity: OutputFileIdentity }> {
    const pathMetadata = await lstat(filePath)
    const pathIdentity = outputIdentity(pathMetadata)
    if (
      !pathMetadata.isFile()
      || pathMetadata.isSymbolicLink()
      || pathMetadata.size <= 0
      || pathMetadata.size > maximumBytes
      || (expected !== null && !sameOutputIdentity(pathIdentity, expected))
    ) {
      throw new Error('Song Cover output identity is invalid.')
    }
    const handle = await open(filePath, 'r')
    try {
      const openedMetadata = await handle.stat()
      const openedIdentity = outputIdentity(openedMetadata)
      if (
        !openedMetadata.isFile()
        || !sameOutputIdentity(pathIdentity, openedIdentity)
        || (expected !== null && !sameOutputIdentity(openedIdentity, expected))
      ) {
        throw new Error('Song Cover output changed while opening.')
      }
      const prefix = Buffer.allocUnsafe(
        Math.min(openedMetadata.size, OUTPUT_HEADER_BYTES),
      )
      const { bytesRead } = await handle.read(
        prefix,
        0,
        prefix.byteLength,
        0,
      )
      const header = prefix.subarray(0, bytesRead)
      const canonical = format === 'wav'
        ? isCanonicalSongCoverWav(header, openedMetadata.size)
        : isCanonicalSongCoverMp3(header)
      if (!canonical) {
        throw new Error('Song Cover output format is invalid.')
      }
      const finalMetadata = await lstat(filePath)
      if (!sameOutputIdentity(outputIdentity(finalMetadata), openedIdentity)) {
        throw new Error('Song Cover output changed during validation.')
      }
      return { handle, identity: openedIdentity }
    } catch (error) {
      await handle.close().catch(() => {})
      throw error
    }
  }

  private async inspectOutput(
    filePath: string,
    maximumBytes: number,
    format: 'mp3' | 'wav',
  ): Promise<OutputFileIdentity> {
    const opened = await this.openValidatedOutput(
      filePath,
      maximumBytes,
      null,
      format,
    )
    try {
      return opened.identity
    } finally {
      await opened.handle.close()
    }
  }

  private async validateCompletedOutputs(
    job: ActiveSongCoverJob,
  ): Promise<CompletedOutputIdentity> {
    const [wav, mp3] = await Promise.all([
      this.inspectOutput(
        path.join(job.directory, 'elysia-cover.wav'),
        MAX_LOSSLESS_OUTPUT_BYTES,
        'wav',
      ),
      this.inspectOutput(
        path.join(job.directory, 'elysia-cover.mp3'),
        MAX_PLAYBACK_BYTES,
        'mp3',
      ),
    ])
    return Object.freeze({ jobId: job.id, mp3, wav })
  }

  private async readValidatedPlayback(jobId: string): Promise<Uint8Array> {
    const expected = this.completedOutput
    if (expected === null || expected.jobId !== jobId) {
      throw new Error('Song Cover output identity is unavailable.')
    }
    const opened = await this.openValidatedOutput(
      path.join(this.outputDirectory(jobId), 'elysia-cover.mp3'),
      MAX_PLAYBACK_BYTES,
      expected.mp3,
      'mp3',
    )
    try {
      const bytes = await opened.handle.readFile()
      if (bytes.byteLength !== opened.identity.size) {
        throw new Error('Song Cover preview changed while reading.')
      }
      return bytes
    } finally {
      await opened.handle.close()
    }
  }

  private async copyOutputWav(
    jobId: string,
    destinationPath: string,
  ): Promise<void> {
    const expected = this.completedOutput
    if (expected === null || expected.jobId !== jobId) {
      throw new Error('Song Cover output identity is unavailable.')
    }
    const sourcePath = path.join(
      this.outputDirectory(jobId),
      'elysia-cover.wav',
    )
    const resolvedSource = path.resolve(sourcePath)
    const resolvedDestination = path.resolve(destinationPath)
    if (
      resolvedSource === resolvedDestination
      || (
        process.platform === 'win32'
        && resolvedSource.toLocaleLowerCase('en-US')
          === resolvedDestination.toLocaleLowerCase('en-US')
      )
    ) {
      throw new Error('Choose a different export destination.')
    }
    const source = await this.openValidatedOutput(
      sourcePath,
      MAX_LOSSLESS_OUTPUT_BYTES,
      expected.wav,
      'wav',
    )
    let sourceClosed = false
    let stagedHandle: FileHandle | null = null
    let stagedPath: string | null = null
    try {
      const originalDestination = await this.inspectExportDestination(
        resolvedDestination,
        source.identity,
      )
      const staged = await this.openPrivateExportFile(
        path.dirname(resolvedDestination),
      )
      stagedHandle = staged.handle
      stagedPath = staged.path
      const buffer = Buffer.allocUnsafe(1024 * 1024)
      let position = 0
      while (position < source.identity.size) {
        const requested = Math.min(
          buffer.byteLength,
          source.identity.size - position,
        )
        const { bytesRead } = await this.exportOperations.read(
          source.handle,
          buffer,
          0,
          requested,
          position,
        )
        if (bytesRead <= 0 || bytesRead > requested) {
          throw new Error('Song Cover export source ended unexpectedly.')
        }
        let written = 0
        while (written < bytesRead) {
          const result = await this.exportOperations.write(
            stagedHandle,
            buffer,
            written,
            bytesRead - written,
            position + written,
          )
          if (
            result.bytesWritten <= 0
            || result.bytesWritten > bytesRead - written
          ) {
            throw new Error('Song Cover export destination stopped accepting data.')
          }
          written += result.bytesWritten
        }
        position += bytesRead
      }
      await this.exportOperations.sync(stagedHandle)
      const [sourceHandleMetadata, sourcePathMetadata, stagedMetadata] = (
        await Promise.all([
          source.handle.stat(),
          lstat(sourcePath),
          stagedHandle.stat(),
        ])
      )
      if (
        !sameOutputIdentity(outputIdentity(sourceHandleMetadata), source.identity)
        || !sameOutputIdentity(outputIdentity(sourcePathMetadata), source.identity)
        || !stagedMetadata.isFile()
        || stagedMetadata.size !== source.identity.size
      ) {
        throw new Error('Song Cover export changed during staging.')
      }
      const stagedIdentity = outputIdentity(stagedMetadata)
      await stagedHandle.close()
      stagedHandle = null
      await source.handle.close()
      sourceClosed = true
      await this.publishExportFile(
        stagedPath,
        stagedIdentity,
        resolvedDestination,
        originalDestination,
        source.identity,
      )
    } finally {
      if (!sourceClosed) {
        await source.handle.close().catch(() => {})
      }
      if (stagedHandle !== null) {
        await stagedHandle.close().catch(() => {})
      }
      if (stagedPath !== null) {
        await rm(stagedPath, { force: true }).catch(() => {})
      }
    }
  }

  /**
   * Inspect an export target without altering its bytes.
   *
   * A Windows casing alias or hard link can name the managed source with a
   * different string. Existing destinations therefore stay open only long
   * enough to compare the path and handle identities before staging begins.
   */
  private async inspectExportDestination(
    destinationPath: string,
    sourceIdentity: OutputFileIdentity,
  ): Promise<OutputFileIdentity | null> {
    let pathMetadata: Stats | null = null
    try {
      pathMetadata = await lstat(destinationPath)
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
        throw error
      }
    }
    if (pathMetadata === null) {
      return null
    }
    if (pathMetadata.isSymbolicLink() || !pathMetadata.isFile()) {
      throw new Error('Song Cover export destination is invalid.')
    }
    const destination = await open(destinationPath, 'r')
    try {
      const openedMetadata = await destination.stat()
      const pathIdentity = outputIdentity(pathMetadata)
      const openedIdentity = outputIdentity(openedMetadata)
      if (
        !openedMetadata.isFile()
        || !sameOutputIdentity(pathIdentity, openedIdentity)
        || sameOutputIdentity(openedIdentity, sourceIdentity)
      ) {
        throw new Error('Song Cover export destination is invalid.')
      }
      return openedIdentity
    } finally {
      await destination.close()
    }
  }

  /** Create one unpredictable, exclusive same-directory staging file. */
  private async openPrivateExportFile(
    directory: string,
  ): Promise<{ readonly handle: FileHandle; readonly path: string }> {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const filePath = path.join(
        directory,
        `.elysia-song-cover-${randomUUID()}.tmp`,
      )
      try {
        return Object.freeze({
          handle: await open(filePath, 'wx+', 0o600),
          path: filePath,
        })
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') {
          throw error
        }
      }
    }
    throw new Error('A private Song Cover export file could not be reserved.')
  }

  /** Return one regular path identity, or null when the path is absent. */
  private async regularExportPathIdentity(
    filePath: string,
  ): Promise<OutputFileIdentity | null> {
    try {
      const metadata = await lstat(filePath)
      if (metadata.isSymbolicLink() || !metadata.isFile()) {
        throw new Error('Song Cover export path is invalid.')
      }
      return outputIdentity(metadata)
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        return null
      }
      throw error
    }
  }

  /**
   * Publish a fully closed staging file without exposing partial output.
   *
   * New destinations use an atomic, no-clobber hard-link. Existing files keep
   * a same-directory hard-link rollback lease while the platform performs one
   * atomic rename/replace. This is safe on Windows, where deleting or
   * truncating the final path before a copy would permanently lose user data.
   */
  private async publishExportFile(
    stagedPath: string,
    stagedIdentity: OutputFileIdentity,
    destinationPath: string,
    originalDestination: OutputFileIdentity | null,
    sourceIdentity: OutputFileIdentity,
  ): Promise<void> {
    const currentDestination = await this.inspectExportDestination(
      destinationPath,
      sourceIdentity,
    )
    if (
      (originalDestination === null && currentDestination !== null)
      || (
        originalDestination !== null
        && (
          currentDestination === null
          || !sameOutputIdentity(currentDestination, originalDestination)
        )
      )
    ) {
      throw new Error('Song Cover export destination changed during staging.')
    }

    if (originalDestination === null) {
      let published = false
      try {
        await this.exportOperations.link(stagedPath, destinationPath)
        published = true
        const publishedIdentity = await this.regularExportPathIdentity(
          destinationPath,
        )
        if (
          publishedIdentity === null
          || !sameStoredFile(publishedIdentity, stagedIdentity)
        ) {
          throw new Error('Song Cover export publication could not be verified.')
        }
        await rm(stagedPath, { force: true })
      } catch (error) {
        if (published) {
          const publishedIdentity = await this.regularExportPathIdentity(
            destinationPath,
          ).catch(() => null)
          if (
            publishedIdentity !== null
            && sameStoredFile(publishedIdentity, stagedIdentity)
          ) {
            await rm(destinationPath, { force: true }).catch(() => {})
          }
        }
        throw error
      }
      return
    }

    const backupPath = path.join(
      path.dirname(destinationPath),
      `.elysia-song-cover-${randomUUID()}.bak`,
    )
    let backupExists = false
    try {
      // link(2) provides the exclusive create operation for the unpredictable
      // backup name; unlike pre-creating an empty placeholder, it leaves no
      // replacement window between reservation and preservation.
      await this.exportOperations.link(destinationPath, backupPath)
      backupExists = true
      const [backupIdentity, finalDestination] = await Promise.all([
        this.regularExportPathIdentity(backupPath),
        this.inspectExportDestination(destinationPath, sourceIdentity),
      ])
      if (
        backupIdentity === null
        || finalDestination === null
        || !sameStoredFile(backupIdentity, originalDestination)
        || !sameStoredFile(finalDestination, originalDestination)
      ) {
        throw new Error('Song Cover export rollback could not be secured.')
      }

      await this.exportOperations.rename(stagedPath, destinationPath)
      const publishedIdentity = await this.regularExportPathIdentity(
        destinationPath,
      )
      if (
        publishedIdentity === null
        || !sameStoredFile(publishedIdentity, stagedIdentity)
      ) {
        throw new Error('Song Cover export publication could not be verified.')
      }
      try {
        await rm(backupPath, { force: true })
        backupExists = false
      } catch (error) {
        if (await this.rollbackExportFile(
          destinationPath,
          backupPath,
          originalDestination,
          stagedIdentity,
        )) {
          backupExists = false
        }
        throw error
      }
    } catch (error) {
      if (
        backupExists
        && await this.rollbackExportFile(
          destinationPath,
          backupPath,
          originalDestination,
          stagedIdentity,
        )
      ) {
        backupExists = false
      }
      throw error
    } finally {
      // A failed rollback deliberately preserves its private hard-link: it is
      // the only proven copy of the user's original bytes and must not be
      // removed merely to make cleanup look successful.
      if (!backupExists) {
        await rm(backupPath, { force: true }).catch(() => {})
      }
    }
  }

  /** Restore an existing destination after an uncertain publication attempt. */
  private async rollbackExportFile(
    destinationPath: string,
    backupPath: string,
    originalIdentity: OutputFileIdentity,
    stagedIdentity: OutputFileIdentity,
  ): Promise<boolean> {
    let current: OutputFileIdentity | null
    try {
      current = await this.regularExportPathIdentity(destinationPath)
    } catch {
      // An unreadable or non-regular replacement is not equivalent to a
      // missing path. Preserve the backup instead of overwriting an object
      // whose identity can no longer be proven.
      return false
    }
    if (current !== null && sameStoredFile(current, originalIdentity)) {
      try {
        await rm(backupPath, { force: true })
        return true
      } catch {
        return false
      }
    }
    if (
      current !== null
      && !sameStoredFile(current, stagedIdentity)
    ) {
      return false
    }
    try {
      await this.exportOperations.rename(backupPath, destinationPath)
      const restored = await this.regularExportPathIdentity(destinationPath)
      return restored !== null && sameStoredFile(restored, originalIdentity)
    } catch {
      return false
    }
  }

  /** Stop transient work and remove output that has not been explicitly exported. */
  shutdown(): Promise<void> {
    if (this.shutdownOperation !== null) {
      return this.shutdownOperation
    }
    this.shuttingDown = true
    // Shutdown must interrupt probe, HTTPS, or retry backoff before it waits
    // for the launch reservation to release.
    this.launchAbortController?.abort()
    const operation = this.shutdownOnce()
    this.shutdownOperation = operation
    void operation.then(
      () => {
        if (this.shutdownOperation === operation) {
          this.shutdownOperation = null
        }
      },
      () => {
        if (this.shutdownOperation === operation) {
          this.shutdownOperation = null
        }
      },
    )
    return operation
  }

  /** Perform one retryable, authoritative shutdown attempt. */
  private async shutdownOnce(): Promise<void> {
    const pendingLaunch = this.launchBarrier
    if (pendingLaunch !== null) {
      await pendingLaunch
    }
    const job = this.activeJob
    if (job !== null) {
      job.cancelled = true
      this.clearJobDeadline(job)
      // A still-live parent permits another authoritative tree-stop attempt.
      // Once that parent closes after an unconfirmed attempt, its descendants
      // can no longer be proven gone from the parent exit code alone.
      const terminated = await this.terminateJobProcess(
        job,
        job.terminationUncertain,
      )
      if (!terminated) {
        throw new Error(
          'Song Cover process-tree termination could not be confirmed.',
        )
      }
      if (this.activeJob === job) {
        this.activeJob = null
      }
      if (!await this.removeIncompleteJobArtifacts(job)) {
        throw new Error(PRIVATE_CLEANUP_ERROR)
      }
    }
    if (!this.stopPlaybackUnconditionally()) {
      throw new Error('Song playback could not be stopped safely.')
    }
    try {
      await this.ensureStartupCleanup()
      await this.retryPendingCleanup()
      await this.removeOutputDirectory()
    } catch (error) {
      throw new Error(PRIVATE_CLEANUP_ERROR, { cause: error })
    }
  }

  /** Start or retry the exact cleanup lease captured for the initial data root. */
  private beginStartupCleanup(): Promise<boolean> {
    const cleanup = this.removeStartupArtifacts()
    this.startupCleanup = cleanup
    // The cleanup resolves to a status instead of rejecting so constructor-
    // started work cannot become an unhandled Main-process rejection.
    void cleanup.then((succeeded) => {
      if (this.startupCleanup === cleanup) {
        this.startupCleanup = null
        this.startupCleanupRequired = !succeeded
      }
    })
    return cleanup
  }

  /** Require stale private artifacts to be gone before accepting new audio. */
  private async ensureStartupCleanup(): Promise<void> {
    if (!this.startupCleanupRequired) {
      return
    }
    const cleanup = this.startupCleanup ?? this.beginStartupCleanup()
    if (!await cleanup) {
      throw new Error(PRIVATE_CLEANUP_ERROR)
    }
  }

  /** Keep the lease active until startup job-root cleanup has settled. */
  private async removeStartupArtifacts(): Promise<boolean> {
    try {
      await this.removeStartupSongJobs()
      return true
    } catch {
      return false
    }
  }

  /** Recover exact crash-left WSL jobs before deleting their native tokens. */
  private async removeStartupSongJobs(): Promise<void> {
    let entries: Dirent[]
    try {
      entries = await readdir(this.startupJobsRoot, { withFileTypes: true })
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        entries = []
      } else {
        throw error
      }
    }
    for (const entry of entries) {
      if (!SONG_COVER_JOB_ID_PATTERN.test(entry.name) || !entry.isDirectory()) {
        continue
      }
      const jobDirectory = path.join(this.startupJobsRoot, entry.name)
      try {
        // lyrics-manifest.json survives every incomplete lyrics-SVS worker but
        // is removed before success.  It distinguishes crash-left SVS work
        // from completed output and RVC conversion jobs without trusting content.
        await lstat(path.join(jobDirectory, 'lyrics-manifest.json'))
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
          continue
        }
        throw error
      }
      if (!await this.removeSvsPrivateJobTracked(entry.name, jobDirectory)) {
        this.pendingJobDirectories.set(entry.name, jobDirectory)
        throw new Error(PRIVATE_CLEANUP_ERROR)
      }
    }
    await this.removePath(this.startupJobsRoot, {
      recursive: true,
      force: true,
    })
  }

  private runtimePaths(): SongCoverRuntimePaths {
    const gptRuntime = path.join(
      this.projectRoot,
      'models',
      'cache',
      'GPT-SoVITS-v2-240821',
    )
    const singingRuntime = path.join(
      this.projectRoot,
      'models',
      'cache',
      'singing-runtime',
    )
    return {
      python: path.join(gptRuntime, 'runtime', 'python.exe'),
      worker: path.join(this.projectRoot, 'scripts', 'song_cover_worker.py'),
      svsWorker: path.join(this.projectRoot, 'scripts', 'song_svs_worker.py'),
      ffmpeg: path.join(gptRuntime, 'ffmpeg.exe'),
      ffprobe: path.join(gptRuntime, 'ffprobe.exe'),
      demucsSite: path.join(singingRuntime, 'site-packages'),
      torchHome: path.join(singingRuntime, 'torch'),
      rvcRoot: path.join(
        this.projectRoot,
        'models',
        'cache',
        'rvc-v2-40k',
      ),
      rvcModel: path.join(
        this.projectRoot,
        'models',
        'weights',
        'rvc',
        'elysia-v2-40k',
        'model.pth',
      ),
      rvcIndex: path.join(
        this.projectRoot,
        'models',
        'weights',
        'rvc',
        'elysia-v2-40k',
        'model.index',
      ),
    }
  }

  private workerEnvironment(
    runtime: SongCoverRuntimePaths,
    temporaryRoot: string,
    privateCacheRoot: string,
  ): NodeJS.ProcessEnv {
    const systemRoot = process.env.SystemRoot ?? process.env.WINDIR
    const system32 = systemRoot === undefined
      ? null
      : path.join(systemRoot, 'System32')
    const searchPath = [
      path.dirname(runtime.python),
      path.dirname(runtime.ffmpeg),
      system32,
    ].filter((entry): entry is string => entry !== null)
    const environment: NodeJS.ProcessEnv = {
      HF_HUB_OFFLINE: '1',
      NUMBA_CACHE_DIR: path.join(privateCacheRoot, 'numba-cache'),
      PATH: searchPath.join(path.delimiter),
      PYTHONDONTWRITEBYTECODE: '1',
      PYTHONIOENCODING: 'utf-8',
      PYTHONNOUSERSITE: '1',
      PYTHONUTF8: '1',
      TEMP: temporaryRoot,
      TMP: temporaryRoot,
      TORCH_HOME: runtime.torchHome,
      TRANSFORMERS_OFFLINE: '1',
    }
    for (const name of [
      'CUDA_VISIBLE_DEVICES',
      'SystemDrive',
      'SystemRoot',
      'WINDIR',
    ] as const) {
      const value = process.env[name]
      if (value !== undefined) {
        environment[name] = value
      }
    }
    return environment
  }

  private consumeWorkerChunk(job: ActiveSongCoverJob, chunk: Buffer): void {
    if (this.activeJob !== job || job.protocolFailed || job.cancelled) {
      return
    }
    job.stdoutBytes += chunk.byteLength
    if (job.stdoutBytes > MAX_WORKER_STDOUT_BYTES) {
      this.rejectWorkerProtocol(job)
      return
    }
    job.stdoutLine = Buffer.concat([job.stdoutLine, chunk])
    let newline = job.stdoutLine.indexOf(0x0a)
    while (newline >= 0) {
      const rawLine = job.stdoutLine.subarray(0, newline)
      job.stdoutLine = job.stdoutLine.subarray(newline + 1)
      if (rawLine.byteLength > MAX_WORKER_STDOUT_LINE_BYTES) {
        this.rejectWorkerProtocol(job)
        return
      }
      const line = rawLine.at(-1) === 0x0d
        ? rawLine.subarray(0, rawLine.byteLength - 1)
        : rawLine
      this.consumeWorkerLine(job, line.toString('utf8'))
      if (job.protocolFailed || job.cancelled) {
        return
      }
      newline = job.stdoutLine.indexOf(0x0a)
    }
    if (job.stdoutLine.byteLength > MAX_WORKER_STDOUT_LINE_BYTES) {
      this.rejectWorkerProtocol(job)
    }
  }

  private finishWorkerOutput(job: ActiveSongCoverJob): void {
    if (
      job.stdoutLine.byteLength > 0
      && !job.protocolFailed
      && !job.cancelled
    ) {
      if (job.stdoutLine.byteLength > MAX_WORKER_STDOUT_LINE_BYTES) {
        this.rejectWorkerProtocol(job)
      } else {
        this.consumeWorkerLine(job, job.stdoutLine.toString('utf8'))
      }
    }
    job.stdoutLine = Buffer.alloc(0)
  }

  private consumeWorkerLine(job: ActiveSongCoverJob, line: string): void {
    if (this.activeJob !== job || !line.startsWith(WORKER_EVENT_PREFIX)) {
      return
    }
    let payload: unknown
    try {
      payload = JSON.parse(line.slice(WORKER_EVENT_PREFIX.length))
    } catch {
      this.rejectWorkerProtocol(job)
      return
    }
    const event = parseSongCoverWorkerEvent(payload)
    if (event === null) {
      this.rejectWorkerProtocol(job)
      return
    }
    const expectedStage = WORKER_STAGE_SEQUENCES[job.engine][job.nextStageIndex]
    const expected = WORKER_PROGRESS[event.stage]
    if (
      event.stage !== expectedStage
      || event.progressPercent !== expected.progressPercent
    ) {
      this.rejectWorkerProtocol(job)
      return
    }
    job.nextStageIndex += 1
    if (event.stage === 'complete') {
      job.sawComplete = true
      return
    }
    this.replaceState({
      stage: event.stage,
      progressPercent: event.progressPercent,
      outputAvailable: false,
      message: expected.message,
      error: null,
    })
  }

  private rejectWorkerProtocol(job: ActiveSongCoverJob): void {
    if (job.protocolFailed) {
      return
    }
    job.protocolFailed = true
    void this.abortJob(
      job,
      'The Song Cover worker returned an invalid response.',
    )
  }

  private async handleJobTimeout(job: ActiveSongCoverJob): Promise<void> {
    if (
      this.activeJob !== job
      || job.settled
      || job.cancelled
      || this.shuttingDown
    ) {
      return
    }
    await this.abortJob(
      job,
      'Song Cover took too long and was stopped.',
    )
  }

  /**
   * Coalesce protocol/timeout aborts so a simultaneous user Cancel observes
   * the winning terminal state instead of deleting and publishing twice.
   */
  private abortJob(
    job: ActiveSongCoverJob,
    failureMessage: string,
  ): Promise<void> {
    if (job.abortSettlement !== null) {
      return job.abortSettlement
    }
    if (this.activeJob !== job) {
      return Promise.resolve()
    }
    const settlement = this.abortJobOnce(job, failureMessage)
    job.abortSettlement = settlement
    return settlement
  }

  private async abortJobOnce(
    job: ActiveSongCoverJob,
    failureMessage: string,
  ): Promise<void> {
    job.cancelled = true
    this.clearJobDeadline(job)
    const terminated = await this.terminateJobProcess(job)
    if (!terminated) {
      job.terminationUncertain = true
      if (!this.shuttingDown && this.state.jobId === job.id) {
        this.replaceState({
          stage: 'error',
          progressPercent: this.state.progressPercent,
          outputAvailable: false,
          message: null,
          error: 'Song Cover could not be stopped safely. Restart Elysia after checking Task Manager.',
        })
      }
      return
    }
    const cleanupSucceeded = await this.removeIncompleteJobArtifacts(job)
    if (
      !this.shuttingDown
      && this.activeJob === job
      && this.state.jobId === job.id
    ) {
      this.replaceState({
        stage: 'error',
        progressPercent: 0,
        outputAvailable: false,
        message: null,
        errorCode: cleanupSucceeded ? 'singing-runtime' : null,
        error: cleanupSucceeded ? failureMessage : PRIVATE_CLEANUP_ERROR,
      })
    }
    if (this.activeJob === job) {
      this.activeJob = null
    }
  }

  private async finishJob(
    job: ActiveSongCoverJob,
    exitCode: number,
  ): Promise<void> {
    if (job.settled) {
      return
    }
    job.settled = true
    this.clearJobDeadline(job)
    if (this.activeJob !== job) {
      return
    }
    if (job.cancelled || this.shuttingDown) {
      return
    }
    if (exitCode !== 0 || job.protocolFailed || !job.sawComplete) {
      const cleanupSucceeded = await this.removeIncompleteJobArtifacts(job)
      if (this.activeJob !== job || job.cancelled || this.shuttingDown) {
        return
      }
      this.replaceState({
        stage: 'error',
        progressPercent: 0,
        outputAvailable: false,
        message: null,
        errorCode: cleanupSucceeded
          ? this.workerFailureCode(job)
          : null,
        error: cleanupSucceeded
          ? this.workerFailureMessage(job.stderr)
          : PRIVATE_CLEANUP_ERROR,
      })
      this.activeJob = null
      return
    }
    let completedOutput: CompletedOutputIdentity
    try {
      completedOutput = await this.validateCompletedOutputs(job)
    } catch {
      const cleanupSucceeded = await this.removeIncompleteJobArtifacts(job)
      if (this.activeJob !== job || job.cancelled || this.shuttingDown) {
        return
      }
      this.replaceState({
        stage: 'error',
        progressPercent: 0,
        outputAvailable: false,
        message: null,
        errorCode: cleanupSucceeded ? 'singing-runtime' : null,
        error: cleanupSucceeded
          ? 'The Song Cover worker returned no usable audio.'
          : PRIVATE_CLEANUP_ERROR,
      })
      this.activeJob = null
      return
    }
    if (this.activeJob !== job || job.cancelled || this.shuttingDown) {
      return
    }
    this.completedOutput = completedOutput
    this.outputJobId = job.id
    this.replaceReadyState('Song Cover ready')
    try {
      await this.play(job.id)
    } catch {
      if (
        this.activeJob === job
        && !job.cancelled
        && !this.shuttingDown
        && this.outputJobId === job.id
        && this.state.outputAvailable
      ) {
        this.replaceState({
          stage: 'error',
          progressPercent: 100,
          outputAvailable: true,
          message: null,
          error: 'The Song Cover is ready, but playback could not start.',
        })
      }
    } finally {
      if (this.activeJob === job) {
        this.activeJob = null
      }
    }
  }

  private workerFailureMessage(stderr: string): string {
    if (/CUDA out of memory|out of memory/iu.test(stderr)) {
      return 'Song Cover ran out of GPU memory. Close other GPU apps and try again.'
    }
    return 'Song Cover could not be created. Check the local singing runtime and source audio.'
  }

  private workerFailureCode(job: ActiveSongCoverJob): SongCoverErrorCode {
    return job.engine === 'lyrics-svs' && this.state.stage === 'aligning'
      ? 'lyrics-alignment'
      : 'singing-runtime'
  }

  private replaceReadyState(message: string): void {
    this.replaceState({
      stage: 'ready',
      progressPercent: 100,
      outputAvailable: true,
      message,
      error: null,
    })
  }

  private replaceState(
    next: Omit<
      SongCoverState,
      | 'revision'
      | 'jobId'
      | 'sourceName'
      | 'accompanimentName'
      | 'sourceMode'
      | 'engine'
      | 'keyShiftSemitones'
      | 'errorCode'
    > & {
      jobId?: string | null
      sourceName?: string | null
      accompanimentName?: string | null
      sourceMode?: SongCoverSourceMode | null
      engine?: SongCoverEngine | null
      keyShiftSemitones?: SongCoverKeyShiftSemitones | null
      errorCode?: SongCoverErrorCode | null
    },
  ): void {
    this.state = Object.freeze({
      revision: this.state.revision + 1,
      jobId: next.jobId === undefined ? this.state.jobId : next.jobId,
      sourceName: next.sourceName === undefined
        ? this.state.sourceName
        : next.sourceName,
      accompanimentName: next.accompanimentName === undefined
        ? this.state.accompanimentName
        : next.accompanimentName,
      sourceMode: next.sourceMode === undefined
        ? this.state.sourceMode
        : next.sourceMode,
      engine: next.engine === undefined ? this.state.engine : next.engine,
      keyShiftSemitones: next.keyShiftSemitones === undefined
        ? this.state.keyShiftSemitones
        : next.keyShiftSemitones,
      ...next,
      errorCode: next.errorCode === undefined
        ? null
        : next.errorCode,
    })
    this.publish(this.state)
  }

  private async removeOutputDirectory(): Promise<void> {
    const outputJobId = this.outputJobId
    if (outputJobId !== null) {
      await this.removePath(this.outputDirectory(outputJobId), {
        recursive: true,
        force: true,
      })
      if (this.outputJobId === outputJobId) {
        this.outputJobId = null
        this.completedOutput = null
      }
    }
  }

  /**
   * Remove both private artifact classes and remember any failed exact target.
   *
   * A terminated worker cannot be allowed to disappear from bookkeeping while
   * decoded vocals or stems remain. Failed targets stay in a bounded in-memory
   * retry set, block data-root moves, and are retried before another job starts.
   */
  private async removeIncompleteJobArtifacts(
    job: ActiveSongCoverJob,
  ): Promise<boolean> {
    if (job.engine === 'lyrics-svs') {
      // A forced Windows process-tree stop prevents the bridge's Python
      // cleanup block from running.  Remove the one matching WSL UUID first;
      // retaining the native directory on failure preserves the token needed
      // for an exact retry instead of losing track of private lyric material.
      if (!await this.removeSvsPrivateJobTracked(job.id, job.directory)) {
        this.pendingJobDirectories.set(job.id, job.directory)
        return false
      }
      return this.removeJobDirectory(job.id, job.directory)
    }
    return this.removeJobDirectory(job.id, job.directory)
  }

  private async removeSvsPrivateJobTracked(
    jobId: string,
    jobDirectory: string,
  ): Promise<boolean> {
    try {
      if (!await this.runSvsPrivateCleanup(jobId, jobDirectory)) {
        throw new Error('Private WSL cleanup did not complete.')
      }
      this.pendingSvsPrivateJobIds.delete(jobId)
      return true
    } catch {
      this.pendingSvsPrivateJobIds.add(jobId)
      return false
    }
  }

  private async removeJobDirectory(
    jobId: string,
    directory: string,
  ): Promise<boolean> {
    try {
      await this.removePath(directory, { recursive: true, force: true })
      if (this.pendingJobDirectories.get(jobId) === directory) {
        this.pendingJobDirectories.delete(jobId)
      }
      return true
    } catch {
      this.pendingJobDirectories.set(jobId, directory)
      return false
    }
  }

  /** Retry only exact failed cleanup targets before admitting new private data. */
  private async retryPendingCleanup(): Promise<void> {
    for (const jobId of [...this.pendingSvsPrivateJobIds]) {
      const directory = this.pendingJobDirectories.get(jobId)
        ?? this.outputDirectory(jobId)
      await this.removeSvsPrivateJobTracked(jobId, directory)
    }
    for (const [jobId, directory] of [...this.pendingJobDirectories]) {
      if (this.pendingSvsPrivateJobIds.has(jobId)) {
        continue
      }
      await this.removeJobDirectory(jobId, directory)
    }
    if (
      this.pendingSvsPrivateJobIds.size > 0
      || this.pendingJobDirectories.size > 0
    ) {
      throw new Error(PRIVATE_CLEANUP_ERROR)
    }
  }

  /** Run one isolated cleanup worker for the exact Main-issued SVS UUID. */
  private runSvsPrivateCleanup(
    jobId: string,
    jobDirectory: string,
  ): Promise<boolean> {
    if (!SONG_COVER_JOB_ID_PATTERN.test(jobId)) {
      return Promise.resolve(false)
    }
    const runtime = this.runtimePaths()
    return new Promise<boolean>((resolve) => {
      let settled = false
      let invalid = false
      let output = Buffer.alloc(0)
      let cleanup: ChildProcessByStdio<null, Readable, Readable>
      let deadline: ReturnType<typeof setTimeout> | null = null
      const finish = (succeeded: boolean): void => {
        if (settled) {
          return
        }
        settled = true
        if (deadline !== null) {
          clearTimeout(deadline)
        }
        resolve(succeeded)
      }
      try {
        cleanup = this.spawnWorker(
          runtime.python,
          [
            runtime.svsWorker,
            '--cleanup-private-job',
            jobId,
          ],
          {
            cwd: this.projectRoot,
            env: this.workerEnvironment(
              runtime,
              this.startupJobsRoot,
              jobDirectory,
            ),
            shell: false,
            stdio: ['ignore', 'pipe', 'pipe'],
            windowsHide: true,
          },
        )
      } catch {
        finish(false)
        return
      }
      let stopping = false
      const stopInvalidCleanup = (): void => {
        invalid = true
        if (stopping) {
          return
        }
        stopping = true
        // Settlement waits for the authoritative tree-stop attempt.  A close
        // event alone is insufficient after timeout because an orphaned WSL
        // descendant could still race the next exact-token retry.
        void this.terminateOwnedProcessTree(cleanup).then(
          () => { finish(false) },
          () => { finish(false) },
        )
      }
      cleanup.stdout.on('data', (chunk: Buffer | string) => {
        if (settled) {
          return
        }
        const bytes = typeof chunk === 'string'
          ? Buffer.from(chunk, 'utf8')
          : chunk
        output = Buffer.concat([output, bytes])
        if (output.byteLength > MAX_PRIVATE_SVS_CLEANUP_BYTES) {
          stopInvalidCleanup()
        }
      })
      // Diagnostics are intentionally discarded: this recovery boundary owns
      // one exact success frame and never publishes paths or lyric fragments.
      cleanup.stderr.resume()
      cleanup.once('error', () => {
        if (!invalid) {
          finish(false)
        }
      })
      cleanup.once('close', (code) => {
        if (invalid) {
          return
        }
        finish(
          code === 0
          && PRIVATE_SVS_CLEANUP_EVENTS.some((event) => output.equals(event)),
        )
      })
      deadline = setTimeout(() => {
        // Wait for an authoritative process-tree stop before allowing a retry;
        // otherwise the old cleanup and a new cleanup could race on one UUID.
        stopInvalidCleanup()
      }, this.privateSvsCleanupTimeoutMs)
      if (settled) {
        clearTimeout(deadline)
        deadline = null
      }
    })
  }

  private clearJobDeadline(job: ActiveSongCoverJob): void {
    if (job.deadline !== null) {
      clearTimeout(job.deadline)
      job.deadline = null
    }
  }

  private isProcessClosed(job: ActiveSongCoverJob): boolean {
    return job.process.exitCode !== null || job.process.signalCode !== null
  }

  private terminateJobProcess(
    job: ActiveSongCoverJob,
    retryUncertain = false,
  ): Promise<boolean> {
    if (job.terminationUncertain && !retryUncertain) {
      return Promise.resolve(false)
    }
    if (this.isProcessClosed(job)) {
      return Promise.resolve(!job.terminationUncertain)
    }
    if (job.termination !== null && !retryUncertain) {
      return job.termination
    }
    // Cancellation, timeout, protocol failure, and app shutdown can converge
    // on the same child.  One shared operation prevents competing taskkill
    // trees and keeps the ownership decision consistent for every caller.
    const termination = this.terminateJobProcessOnce(job, retryUncertain).then(
      (confirmed) => {
        job.terminationUncertain = !confirmed
        return confirmed
      },
      () => {
        job.terminationUncertain = true
        return false
      },
    )
    job.termination = termination
    return termination
  }

  private async terminateJobProcessOnce(
    job: ActiveSongCoverJob,
    retryUncertain: boolean,
  ): Promise<boolean> {
    if (job.terminationUncertain && !retryUncertain) {
      return false
    }
    if (this.isProcessClosed(job)) {
      return !job.terminationUncertain
    }
    if (typeof job.process.pid !== 'number') {
      // Node reports an invalid executable asynchronously. Give that spawn
      // failure time to emit `error`/`close`; calling kill before a PID exists
      // would otherwise strand a job that never created an OS process.
      if (await this.waitForProcessClose(job, 1_000)) {
        return true
      }
      if (typeof job.process.pid !== 'number') {
        return false
      }
    }
    return this.terminateOwnedProcessTree(job.process)
  }

  /** Stop one Main-owned worker tree and confirm that its parent closed. */
  private async terminateOwnedProcessTree(
    child: ChildProcessByStdio<null, Readable, Readable>,
  ): Promise<boolean> {
    if (this.isChildProcessClosed(child)) {
      return true
    }
    if (this.terminateProcessTree !== null) {
      const terminated = await this.terminateProcessTree(child)
      return terminated && await this.waitForChildProcessClose(child, 5_000)
    }
    if (process.platform === 'win32' && typeof child.pid === 'number') {
      const systemRoot = process.env.SystemRoot ?? process.env.WINDIR
      if (systemRoot === undefined || !path.isAbsolute(systemRoot)) {
        return false
      }
      const taskkillPath = path.join(systemRoot, 'System32', 'taskkill.exe')
      const taskkillSucceeded = await new Promise<boolean>((resolve) => {
        let resolved = false
        let timeout: ReturnType<typeof setTimeout> | null = null
        const finish = (result: boolean): void => {
          if (resolved) {
            return
          }
          resolved = true
          if (timeout !== null) {
            clearTimeout(timeout)
          }
          resolve(result)
        }
        let killer: ReturnType<typeof spawn>
        try {
          killer = spawn(
            taskkillPath,
            ['/pid', String(child.pid), '/t', '/f'],
            { windowsHide: true, shell: false, stdio: 'ignore' },
          )
        } catch {
          finish(false)
          return
        }
        timeout = setTimeout(() => {
          try {
            killer.kill('SIGKILL')
          } catch {
            // A taskkill process may finish between the deadline and signal.
          }
          finish(false)
        }, 5_000)
        killer.once('error', () => { finish(false) })
        killer.once('close', (code) => { finish(code === 0) })
      })
      if (!taskkillSucceeded) {
        try {
          child.kill('SIGKILL')
        } catch {
          // Retaining ownership below is safer than deleting active scratch.
        }
        await this.waitForChildProcessClose(child, 1_000)
        return false
      }
      return this.waitForChildProcessClose(child, 5_000)
    }
    try {
      child.kill('SIGTERM')
    } catch {
      return false
    }
    if (await this.waitForChildProcessClose(child, 5_000)) {
      return true
    }
    try {
      child.kill('SIGKILL')
    } catch {
      return false
    }
    return this.waitForChildProcessClose(child, 1_000)
  }

  private async waitForProcessClose(
    job: ActiveSongCoverJob,
    timeoutMs: number,
  ): Promise<boolean> {
    return this.waitForChildProcessClose(job.process, timeoutMs)
  }

  private isChildProcessClosed(
    child: ChildProcessByStdio<null, Readable, Readable>,
  ): boolean {
    return child.exitCode !== null || child.signalCode !== null
  }

  /** Wait a bounded interval for one owned child to emit its close event. */
  private async waitForChildProcessClose(
    child: ChildProcessByStdio<null, Readable, Readable>,
    timeoutMs: number,
  ): Promise<boolean> {
    if (this.isChildProcessClosed(child)) {
      return true
    }
    return new Promise<boolean>((resolve) => {
      let settled = false
      const finish = (closed: boolean): void => {
        if (settled) {
          return
        }
        settled = true
        clearTimeout(timeout)
        child.off('close', handleClose)
        resolve(closed)
      }
      const handleClose = (): void => { finish(true) }
      const timeout = setTimeout(() => { finish(false) }, timeoutMs)
      child.once('close', handleClose)
      if (this.isChildProcessClosed(child)) {
        finish(true)
      }
    })
  }
}
