/**
 * Own native ComfyUI installation selection, stable streaming hash review, and
 * a bounded Python settings worker. Renderer metadata contains logical choices
 * and provenance only; all filesystem paths stay inside trusted Main callbacks.
 */

import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { constants, type BigIntStats } from 'node:fs'
import { lstat, open, realpath } from 'node:fs/promises'
import path from 'node:path'
import type { ImageAssetImportParams, ImageLoraMetadata, ImageLoraSummary,
  ImageProfileMetadata, ImagePromptDialect } from './image-client.js'
export type { ImageProfileMetadata, ImagePromptDialect } from './image-client.js'

/** Describe one bounded canvas through a logical identifier. */
export interface ImageSettingsPreset {
  readonly presetId: string
  readonly width: number
  readonly height: number
}

/** Expose a usable model family without its private checkpoint location. */
export interface ImageSettingsProfile {
  readonly profileId: string
  readonly displayName: string
  readonly architecture: 'sdxl'
  readonly promptDialect: ImagePromptDialect
  readonly defaultSizePresetId: string
  readonly sizePresets: readonly ImageSettingsPreset[]
  readonly loras?: readonly ImageLoraSummary[]
}

/** Publish only validated readiness, logical profiles, and fixed Main messages. */
export interface ImageSettingsStatus {
  readonly configured: boolean
  readonly ready: boolean
  readonly state: 'unconfigured' | 'missing_model' | 'hash_mismatch' | 'ready' | 'invalid' | 'recovery_required'
  readonly message: string
  readonly revision: number
  readonly defaultProfileId: string | null
  readonly profiles: readonly ImageSettingsProfile[]
}

/** Present private native file identities and pins only in Main's consent dialog. */
export interface ImageInstallationReview {
  readonly metadata: ImageProfileMetadata
  readonly runtimeRoot: string
  readonly pythonExecutable: string
  readonly checkpointPath: string
  readonly pythonSha256: string
  readonly comfyuiMainSha256: string
  readonly checkpointSha256: string
}

/** Show the selected source and its digest only inside Main's native confirmation. */
export interface ImageAssetImportReview {
  readonly params: ImageAssetImportParams
  readonly sourcePath: string
  readonly sha256: string
  readonly installation: {
    readonly runtimeRoot: string
    readonly pythonExecutable: string
    readonly pythonSha256: string
    readonly comfyuiMainSha256: string
  } | null
}

/** Describe bounded incomplete state without exposing prompt text or native names. */
export interface ImageRecoveryReview {
  readonly runCount: number
  readonly fileCount: number
  readonly byteSize: number
}

/** Keep private recovery ownership receipts inside Main rather than renderer IPC. */
export interface ImageRecoveryResult {
  readonly applied: boolean
  readonly status: ImageSettingsStatus
}

/** Bind startup-owned roots and native dialogs without a renderer path surface. */
export interface ImageSettingsOptions {
  readonly projectRoot: string
  readonly dataRoot: string
  readonly pythonExecutable: string
  /** Choose a trusted ComfyUI directory through Main's native folder picker. */
  readonly chooseRuntimeRoot: (signal: AbortSignal) => Promise<string | null>
  /** Choose the runtime interpreter through Main's native executable picker. */
  readonly choosePythonExecutable: (signal: AbortSignal) => Promise<string | null>
  /** Choose one safetensors file directly beneath a checkpoints directory. */
  readonly chooseCheckpoint: (signal: AbortSignal) => Promise<string | null>
  /** Pick an adapter source; persistence derives the managed destination. */
  readonly chooseLora?: (signal: AbortSignal) => Promise<string | null>
  /** Approve the exact selected bytes and the asset's declared provenance. */
  readonly confirmAssetImport?: (review: ImageAssetImportReview, signal: AbortSignal) => Promise<boolean>
  /** Explicitly confirm paths, computed hashes, model source, and license. */
  readonly confirmInstallation: (review: ImageInstallationReview, signal: AbortSignal) => Promise<boolean>
  /** Ask native consent to move incomplete runs into fixed private quarantine. */
  readonly confirmRecovery?: (review: ImageRecoveryReview, signal: AbortSignal) => Promise<boolean>
  /** Inject a trusted worker runner for inert tests; private requests stay in Main. */
  readonly runWorker?: (request: Record<string, unknown>, signal: AbortSignal) => Promise<unknown>
  readonly timeoutMs?: number
  readonly importTimeoutMs?: number
}

/** Expose stable failure categories while withholding native errors and output. */
export class ImageSettingsError extends Error {
  /** Construct a fixed-message settings failure suitable for guarded IPC. */
  constructor(readonly reason: 'busy' | 'closed' | 'cancelled' | 'invalid-metadata' | 'unavailable' | 'timeout') {
    super('Local image settings could not be checked or saved.')
    this.name = 'ImageSettingsError'
  }
}

interface HashedFile {
  readonly path: string
  readonly sha256: string
  readonly stats: BigIntStats
  readonly parents: readonly { path: string; stats: BigIntStats }[]
}

const MAX_BYTES = 1_048_576
const PUBLIC_ID = /^[a-z][a-z0-9_-]{0,63}$/u
const DIALECTS = ['sdxl-natural', 'pony-tags', 'illustrious-tags'] as const
const PRESETS = ['square', 'portrait', 'landscape'] as const
const MESSAGES = {
  unconfigured: 'Choose and confirm a trusted ComfyUI installation.',
  missing_model: 'A configured checkpoint is missing. Install the reviewed model before generating.',
  hash_mismatch: 'A reviewed model or runtime SHA-256 no longer matches. Review the installation.',
  ready: 'The trusted local image installation is ready.',
  invalid: 'Local image settings or runtime readiness checks failed. Review the installation.',
  recovery_required: '图片临时目录有未完成任务，请确认隔离保留这些文件后恢复生成。',
} as const
const PREPARATION_MESSAGE = 'The embedded Python environment needs reviewed absolute dependency roots. '
  + 'Run scripts/prepare_image_python.py with the trusted installation paths and --apply before checking image readiness again.'
const REDIRECTOR_MESSAGE = 'Windows virtual-environment redirectors are unsupported for image generation. '
  + 'Choose an actual Python interpreter or a reviewed portable installation.'

/** Validate the exact metadata contract before showing native installation UI. */
export function parseImageProfileMetadata(value: unknown): ImageProfileMetadata {
  try {
    const raw = fields(value, ['displayName', 'profileId', 'promptDialect', 'checkpointSource',
      'checkpointLicense', 'defaultSizePresetId'])
    const profileId = publicId(raw.profileId)
    const displayName = displayLabel(raw.displayName)
    if (!DIALECTS.includes(raw.promptDialect as ImagePromptDialect)
      || !PRESETS.includes(raw.defaultSizePresetId as typeof PRESETS[number])) throw new Error()
    return Object.freeze({
      displayName, profileId,
      promptDialect: raw.promptDialect as ImagePromptDialect,
      checkpointSource: text(raw.checkpointSource, 512),
      checkpointLicense: text(raw.checkpointLicense, 512),
      defaultSizePresetId: raw.defaultSizePresetId as typeof PRESETS[number],
    })
  } catch {
    throw new ImageSettingsError('invalid-metadata')
  }
}

/** Validate a closed metadata-only import request before showing any native UI. */
export function parseImageAssetImportParams(value: unknown): ImageAssetImportParams {
  try {
    const raw = fields(value, ['kind', 'metadata'])
    if (raw.kind === 'checkpoint') return Object.freeze({ kind: 'checkpoint', metadata: parseImageProfileMetadata(raw.metadata) })
    if (raw.kind !== 'lora') throw new Error()
    const metadata = fields(raw.metadata, ['loraId', 'displayName', 'targetProfileId', 'source', 'license'])
    const checked: ImageLoraMetadata = Object.freeze({
      loraId: publicId(metadata.loraId), displayName: displayLabel(metadata.displayName),
      targetProfileId: publicId(metadata.targetProfileId), source: text(metadata.source, 512), license: text(metadata.license, 512),
    })
    return Object.freeze({ kind: 'lora', metadata: checked })
  } catch { throw new ImageSettingsError('invalid-metadata') }
}

/** Validate a path-free worker status and replace diagnostics with Main-owned text. */
export function parseImageSettingsStatus(value: unknown): ImageSettingsStatus {
  try {
    const raw = fields(value, ['configured', 'ready', 'state', 'message', 'revision', 'defaultProfileId', 'profiles'])
    if (typeof raw.state !== 'string' || !Object.hasOwn(MESSAGES, raw.state)
      || typeof raw.configured !== 'boolean' || typeof raw.ready !== 'boolean'
      || raw.ready !== (raw.state === 'ready') || !Number.isSafeInteger(raw.revision)
      || (raw.revision as number) < 0 || typeof raw.message !== 'string'
      || raw.message.length > 1024 || !Array.isArray(raw.profiles) || raw.profiles.length > 8) throw new Error()
    const profiles = raw.profiles.map((item): ImageSettingsProfile => {
      const profile = fields(item, ['profileId', 'displayName', 'architecture', 'promptDialect',
        'defaultSizePresetId', 'sizePresets'], ['loras'])
      if (profile.architecture !== 'sdxl' || !DIALECTS.includes(profile.promptDialect as ImagePromptDialect)
        || !Array.isArray(profile.sizePresets) || profile.sizePresets.length < 1
        || profile.sizePresets.length > 8) throw new Error()
      const sizes = profile.sizePresets.map((item): ImageSettingsPreset => {
        const size = fields(item, ['presetId', 'width', 'height'])
        if (!Number.isInteger(size.width) || !Number.isInteger(size.height)
          || (size.width as number) < 512 || (size.width as number) > 2048 || (size.width as number) % 64 !== 0
          || (size.height as number) < 512 || (size.height as number) > 2048 || (size.height as number) % 64 !== 0
          || (size.width as number) * (size.height as number) > 4_194_304) throw new Error()
        return Object.freeze({ presetId: publicId(size.presetId), width: size.width as number, height: size.height as number })
      })
      const defaultSizePresetId = publicId(profile.defaultSizePresetId)
      if (new Set(sizes.map((size) => size.presetId)).size !== sizes.length
        || !sizes.some((size) => size.presetId === defaultSizePresetId)) throw new Error()
      const loras = Object.hasOwn(profile, 'loras') ? parseLoraSummaries(profile.loras) : undefined
      return Object.freeze({ profileId: publicId(profile.profileId), displayName: displayLabel(profile.displayName),
        architecture: 'sdxl', promptDialect: profile.promptDialect as ImagePromptDialect,
        defaultSizePresetId, sizePresets: Object.freeze(sizes), ...(loras === undefined ? {} : { loras }) })
    })
    const defaultProfileId = raw.defaultProfileId === null ? null : publicId(raw.defaultProfileId)
    if (new Set(profiles.map((profile) => profile.profileId)).size !== profiles.length
      || raw.configured !== (profiles.length > 0) || raw.configured !== (defaultProfileId !== null)
      || defaultProfileId !== null && !profiles.some((profile) => profile.profileId === defaultProfileId)
      || raw.configured && raw.state === 'unconfigured' || !raw.configured
      && !['invalid', 'unconfigured', 'recovery_required'].includes(raw.state)
      || raw.configured && raw.revision === 0) throw new Error()
    const state = raw.state as ImageSettingsStatus['state']
    const message = state === 'invalid' && raw.message === PREPARATION_MESSAGE
      ? '便携 Python 需要先配置依赖路径。请按本地图片文档运行 prepare_image_python.py，再重新检查。'
      : state === 'invalid' && raw.message === REDIRECTOR_MESSAGE
        ? '请选择完整的 Python 解释器或已审查的便携安装；Windows venv 重定向器暂不支持。' : MESSAGES[state]
    return Object.freeze({ configured: raw.configured, ready: raw.ready, state, message,
      revision: raw.revision as number, defaultProfileId, profiles: Object.freeze(profiles) })
  } catch {
    throw new ImageSettingsError('unavailable')
  }
}

/**
 * Serialize settings checks and native guided installation with owned shutdown.
 *
 * SHA-256 is streamed from stable regular files; a later native consent dialog
 * explicitly pins those bytes. Revision comparison prevents another process's
 * newer installation from being overwritten after a long selection workflow.
 */
export class ImageSettingsManager {
  private active: Promise<unknown> | null = null
  private controller: AbortController | null = null
  private readonly timeoutMs: number
  private readonly importTimeoutMs: number
  private closed = false

  /** Accept only trusted startup roots; construction performs no installation or GPU work. */
  constructor(private readonly options: ImageSettingsOptions) {
    for (const value of [options.projectRoot, options.dataRoot, options.pythonExecutable]) nativePath(value)
    this.timeoutMs = options.timeoutMs ?? 120_000
    if (!Number.isSafeInteger(this.timeoutMs) || this.timeoutMs < 1 || this.timeoutMs > 120_000) {
      throw new ImageSettingsError('unavailable')
    }
    this.importTimeoutMs = options.importTimeoutMs ?? 600_000
    if (!Number.isSafeInteger(this.importTimeoutMs) || this.importTimeoutMs < 1 || this.importTimeoutMs > 600_000) {
      throw new ImageSettingsError('unavailable')
    }
  }

  /** Report ownership, including a terminating worker whose close event has not drained. */
  isBusy(): boolean { return this.active !== null }

  /** Hash the reviewed installation without launching ComfyUI or allocating GPU memory. */
  status(): Promise<ImageSettingsStatus> {
    return this.owned((signal) => this.invoke({ op: 'status' }, signal))
  }

  /** Run native picks, hash review, explicit consent, and revision-checked persistence. */
  configure(metadata: unknown): Promise<ImageSettingsStatus> {
    let checked: ImageProfileMetadata
    try { checked = parseImageProfileMetadata(metadata) }
    catch (error) { return Promise.reject(error) }
    return this.owned((signal) => this.configureOnce(checked, signal))
  }

  /** Review an asset, join the caller's Backend stop, then copy under the private Gallery lease. */
  importAsset(params: unknown, beforeApply: () => Promise<void> = async () => {}): Promise<ImageSettingsStatus> {
    let checked: ImageAssetImportParams
    try { checked = parseImageAssetImportParams(params) }
    catch (error) { return Promise.reject(error) }
    return this.owned(signal => this.importOnce(checked, signal, beforeApply))
  }

  /** Confirm a snapshot, stop the owned Backend, then quarantine without deleting private data. */
  recover(beforeApply: () => Promise<void>): Promise<ImageRecoveryResult> {
    return this.owned(async signal => {
      if (!this.options.confirmRecovery || typeof beforeApply !== 'function') throw new ImageSettingsError('unavailable')
      const raw = fields(await this.invokeRaw({ op: 'recovery.preview' }, signal),
        ['kind', 'snapshot', 'runCount', 'fileCount', 'byteSize'])
      if (raw.kind !== 'image.recovery.preview' || typeof raw.snapshot !== 'string' || !/^[0-9a-f]{64}$/u.test(raw.snapshot)
        || !Number.isSafeInteger(raw.runCount) || (raw.runCount as number) < 0 || (raw.runCount as number) > 32
        || !Number.isSafeInteger(raw.fileCount) || (raw.fileCount as number) < 0 || (raw.fileCount as number) > 100_000
        || !Number.isSafeInteger(raw.byteSize) || (raw.byteSize as number) < 0) throw new ImageSettingsError('unavailable')
      const review = Object.freeze({ runCount: raw.runCount as number, fileCount: raw.fileCount as number, byteSize: raw.byteSize as number })
      if (review.runCount === 0 || await callback(() => this.options.confirmRecovery!(review, signal), signal) !== true) {
        return { applied: false, status: await this.invoke({ op: 'status' }, signal) }
      }
      // Backend stop owns physical processes and must settle even if quit
      // aborts this operation. Releasing our reservation on a raced signal
      // would leave that cleanup running outside the Settings close drain.
      await beforeApply()
      checkAbort(signal)
      const receipt = fields(await this.invokeRaw({ op: 'recovery.apply', snapshot: raw.snapshot,
        trustedConfirmation: true }, signal), ['kind', 'quarantinedCount', 'status'])
      if (receipt.kind !== 'image.recovery.result' || receipt.quarantinedCount !== review.runCount) {
        throw new ImageSettingsError('unavailable')
      }
      return { applied: true, status: parseImageSettingsStatus(receipt.status) }
    })
  }

  /** Abort outstanding waits and drain the exact worker before Main may exit. */
  async close(): Promise<void> {
    this.closed = true
    this.controller?.abort()
    await this.active?.catch(() => {})
  }

  private owned<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
    if (this.closed) return Promise.reject(new ImageSettingsError('closed'))
    if (this.active !== null) return Promise.reject(new ImageSettingsError('busy'))
    const controller = new AbortController()
    this.controller = controller
    const pending = Promise.resolve().then(() => {
      checkAbort(controller.signal)
      return operation(controller.signal)
    }).catch((error: unknown) => {
      throw error instanceof ImageSettingsError ? error : new ImageSettingsError('unavailable')
    })
    this.active = pending
    void pending.then(() => this.release(pending), () => this.release(pending))
    return pending
  }

  private release(pending: Promise<unknown>): void {
    if (this.active === pending) { this.active = null; this.controller = null }
  }

  private async configureOnce(metadata: ImageProfileMetadata, signal: AbortSignal): Promise<ImageSettingsStatus> {
    const baseline = await this.invoke({ op: 'status' }, signal)
    const runtime = await callback(() => this.options.chooseRuntimeRoot(signal), signal)
    if (runtime === null) return baseline
    const interpreter = await callback(() => this.options.choosePythonExecutable(signal), signal)
    if (interpreter === null) return baseline
    const checkpoint = await callback(() => this.options.chooseCheckpoint(signal), signal)
    if (checkpoint === null) return baseline
    const runtimeRoot = nativePath(runtime)
    const pythonExecutable = nativePath(interpreter)
    const checkpointPath = nativePath(checkpoint)
    if (path.basename(path.dirname(checkpointPath)).toLowerCase() !== 'checkpoints'
      || path.extname(checkpointPath).toLowerCase() !== '.safetensors') throw new ImageSettingsError('unavailable')
    const deadline = performance.now() + this.timeoutMs
    const python = await hashFile(pythonExecutable, signal, deadline)
    const main = await hashFile(path.join(runtimeRoot, 'main.py'), signal, deadline)
    const model = await hashFile(checkpointPath, signal, deadline)
    const review: ImageInstallationReview = Object.freeze({ metadata, runtimeRoot, pythonExecutable,
      checkpointPath, pythonSha256: python.sha256, comfyuiMainSha256: main.sha256, checkpointSha256: model.sha256 })
    const confirmed = await callback(() => this.options.confirmInstallation(review, signal), signal)
    if (confirmed !== true) return baseline
    // Dialogs can stay open arbitrarily long. Recheck the exact reviewed file
    // identities before handing their pins to persistence, without rehashing a
    // multi-gigabyte checkpoint merely because the user read the consent text.
    for (const file of [python, main, model]) {
      await verifyParents(file.parents)
      if (!sameSnapshot(await lstat(file.path, { bigint: true }), file.stats)) {
        throw new ImageSettingsError('unavailable')
      }
      checkAbort(signal)
    }
    return this.invoke({ op: 'configure', trustedConfirmation: true,
      ...(baseline.state === 'invalid' && baseline.revision === 0 && !baseline.configured
        ? { repairInvalid: true } : {}),
      expectedRevision: baseline.revision, installation: review }, signal)
  }

  private async importOnce(params: ImageAssetImportParams, signal: AbortSignal, beforeApply: () => Promise<void>): Promise<ImageSettingsStatus> {
    if (this.options.confirmAssetImport === undefined) throw new ImageSettingsError('unavailable')
    const baseline = await this.invoke({ op: 'status' }, signal)
    const target = params.kind === 'lora'
      ? baseline.profiles.find(profile => profile.profileId === params.metadata.targetProfileId) : undefined
    if (params.kind === 'checkpoint' && baseline.profiles.some(profile => profile.profileId === params.metadata.profileId)
      || params.kind === 'lora' && (target === undefined || target.loras?.some(lora => lora.loraId === params.metadata.loraId))) {
      throw new ImageSettingsError('invalid-metadata')
    }
    const deadline = performance.now() + this.importTimeoutMs
    let installation: ImageAssetImportReview['installation'] = null
    const reviewed: HashedFile[] = []
    if (!baseline.configured) {
      if (params.kind !== 'checkpoint') throw new ImageSettingsError('invalid-metadata')
      const runtime = await callback(() => this.options.chooseRuntimeRoot(signal), signal)
      if (runtime === null) return baseline
      const interpreter = await callback(() => this.options.choosePythonExecutable(signal), signal)
      if (interpreter === null) return baseline
      const runtimeRoot = nativePath(runtime), pythonExecutable = nativePath(interpreter)
      const python = await hashFile(pythonExecutable, signal, deadline)
      const main = await hashFile(path.join(runtimeRoot, 'main.py'), signal, deadline)
      reviewed.push(python, main)
      installation = Object.freeze({ runtimeRoot, pythonExecutable,
        pythonSha256: python.sha256, comfyuiMainSha256: main.sha256 })
    }
    const source = params.kind === 'checkpoint'
      ? await callback(() => this.options.chooseCheckpoint(signal), signal)
      : this.options.chooseLora === undefined ? null : await callback(() => this.options.chooseLora!(signal), signal)
    if (source === null) return baseline
    const sourcePath = nativePath(source)
    if (path.extname(sourcePath).toLowerCase() !== '.safetensors') throw new ImageSettingsError('unavailable')
    const file = await hashFile(sourcePath, signal, deadline)
    reviewed.push(file)
    const review: ImageAssetImportReview = Object.freeze({ params, sourcePath, sha256: file.sha256, installation })
    if (await callback(() => this.options.confirmAssetImport!(review, signal), signal) !== true) return baseline
    // Only native-selected paths cross this private worker boundary. Recheck
    // every reviewed identity after consent; the worker independently copies
    // and rehashes before its revision-checked settings publication.
    for (const item of reviewed) {
      await verifyParents(item.parents)
      if (!sameSnapshot(await lstat(item.path, { bigint: true }), item.stats)) throw new ImageSettingsError('unavailable')
      checkAbort(signal)
    }
    // The running Backend holds the Gallery lease. Join its physical stop
    // before the import worker, including when quit arrives during shutdown.
    await beforeApply()
    checkAbort(signal)
    return this.invoke({ op: 'assets.import', trustedConfirmation: true, expectedRevision: baseline.revision,
      installation,
      checkpoint: params.kind === 'checkpoint' ? { sourcePath, sha256: file.sha256, metadata: params.metadata } : null,
      lora: params.kind === 'lora' ? { sourcePath, sha256: file.sha256, ...params.metadata } : null,
    }, signal, this.importTimeoutMs)
  }

  private async invoke(request: Record<string, unknown>, signal: AbortSignal, timeoutMs = this.timeoutMs): Promise<ImageSettingsStatus> {
    return parseImageSettingsStatus(await this.invokeRaw(request, signal, timeoutMs))
  }

  private async invokeRaw(request: Record<string, unknown>, signal: AbortSignal, timeoutMs = this.timeoutMs): Promise<unknown> {
    checkAbort(signal)
    const raw = this.options.runWorker === undefined
      ? await this.spawnWorker(request, signal, timeoutMs)
      : await callback(() => this.options.runWorker!(request, signal), signal)
    checkAbort(signal)
    return raw
  }

  private spawnWorker(request: Record<string, unknown>, signal: AbortSignal, timeoutMs: number): Promise<unknown> {
    const body = Buffer.from(`${JSON.stringify(request)}\n`, 'utf8')
    if (body.length > MAX_BYTES) return Promise.reject(new ImageSettingsError('unavailable'))
    return new Promise((resolve, reject) => {
      const environment: NodeJS.ProcessEnv = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
      for (const key of ['PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONINSPECT']) delete environment[key]
      const child = spawn(this.options.pythonExecutable, ['-u', '-s',
        path.join(this.options.projectRoot, 'desktop_image_settings_worker.py'), '--data-root', this.options.dataRoot],
      { cwd: this.options.projectRoot, windowsHide: true, shell: false, detached: false, env: environment, stdio: 'pipe' })
      const chunks: Buffer[] = []
      let stdoutBytes = 0
      let stderrBytes = 0
      let failure: ImageSettingsError | null = null
      const stop = (reason: 'cancelled' | 'timeout' | 'unavailable'): void => {
        failure ??= new ImageSettingsError(reason)
        if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
      }
      const abort = (): void => stop('cancelled')
      const timer = setTimeout(() => stop('timeout'), timeoutMs)
      signal.addEventListener('abort', abort, { once: true })
      child.stdout.on('data', (chunk: Buffer) => {
        stdoutBytes += chunk.length
        if (stdoutBytes > MAX_BYTES) stop('unavailable')
        else if (failure === null) chunks.push(Buffer.from(chunk))
      })
      child.stderr.on('data', (chunk: Buffer) => {
        stderrBytes += chunk.length
        if (stderrBytes > MAX_BYTES) stop('unavailable')
      })
      child.once('error', () => stop('unavailable'))
      child.stdin.once('error', () => stop('unavailable'))
      child.once('close', (code) => {
        clearTimeout(timer)
        signal.removeEventListener('abort', abort)
        if (failure !== null) { reject(failure); return }
        if (code !== 0) { reject(new ImageSettingsError('unavailable')); return }
        try {
          const encoded = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)).trim()
          if (!encoded || encoded.split(/\r?\n/u).length !== 1) throw new Error()
          resolve(JSON.parse(encoded))
        } catch { reject(new ImageSettingsError('unavailable')) }
      })
      if (signal.aborted) abort()
      else child.stdin.end(body)
    })
  }
}

function fields(value: unknown, names: readonly string[], optional: readonly string[] = []): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error()
  const raw = value as Record<string, unknown>
  const allowed = new Set([...names, ...optional])
  if (Object.keys(raw).some(name => !allowed.has(name)) || names.some(name => !Object.hasOwn(raw, name))) throw new Error()
  return raw
}

function parseLoraSummaries(value: unknown): readonly ImageLoraSummary[] {
  if (!Array.isArray(value) || value.length > 64) throw new Error()
  const messages: Record<string, string> = { ready: '已就绪', missing_lora: 'LoRA 文件缺失',
    hash_mismatch: 'LoRA 文件校验不一致', unavailable: 'LoRA 暂不可用' }
  const result = value.map((item): ImageLoraSummary => {
    const raw = fields(item, ['loraId', 'displayName', 'available', 'message', 'defaultStrengthModel', 'defaultStrengthClip'])
    if (typeof raw.available !== 'boolean' || typeof raw.message !== 'string' || !Object.hasOwn(messages, raw.message)
      || raw.available !== (raw.message === 'ready') || raw.defaultStrengthModel !== 1 || raw.defaultStrengthClip !== 1) throw new Error()
    return Object.freeze({ loraId: publicId(raw.loraId), displayName: displayLabel(raw.displayName),
      available: raw.available, message: messages[raw.message]!, defaultStrengthModel: 1, defaultStrengthClip: 1 })
  })
  if (new Set(result.map(item => item.loraId)).size !== result.length) throw new Error()
  return Object.freeze(result)
}

function text(value: unknown, maximum: number): string {
  if (typeof value !== 'string') throw new Error()
  const normalized = value.normalize('NFC').trim()
  if (!normalized || [...normalized].length > maximum || /[\p{Cc}\p{Cf}\p{Cs}]/u.test(normalized)) throw new Error()
  return normalized
}

function publicId(value: unknown): string {
  if (typeof value !== 'string' || !PUBLIC_ID.test(value)) throw new Error()
  return value
}

function displayLabel(value: unknown): string {
  const label = text(value, 80)
  if (/[\\/]|(?:^|\s)[a-z]:/iu.test(label)) throw new Error()
  return label
}

function nativePath(value: string): string {
  if (typeof value !== 'string' || !path.isAbsolute(value) || path.normalize(value) !== path.resolve(value)
    || [...value].some((character) => character.charCodeAt(0) < 32)) throw new ImageSettingsError('unavailable')
  const resolved = path.resolve(value)
  if (process.platform === 'win32') {
    if (!/^[a-z]:\\/iu.test(resolved)) throw new ImageSettingsError('unavailable')
    if (resolved.slice(3).split('\\').some((component) => /[<>:"|?*]/u.test(component)
      || /[. ]$/u.test(component))) throw new ImageSettingsError('unavailable')
  }
  return resolved
}

function checkAbort(signal: AbortSignal): void {
  if (signal.aborted) throw new ImageSettingsError('cancelled')
}

function callback<T>(operation: () => Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new ImageSettingsError('cancelled')); return }
    let settled = false
    const finish = (success: boolean, value: unknown): void => {
      if (settled) return
      settled = true
      signal.removeEventListener('abort', abort)
      if (success) resolve(value as T)
      else reject(value)
    }
    const abort = (): void => finish(false, new ImageSettingsError('cancelled'))
    signal.addEventListener('abort', abort, { once: true })
    void Promise.resolve().then(() => { checkAbort(signal); return operation() })
      .then((value) => finish(true, value), (error: unknown) => finish(false, error))
  })
}

function sameFile(first: BigIntStats, second: BigIntStats): boolean {
  return first.dev === second.dev && first.ino === second.ino
}

function sameSnapshot(first: BigIntStats, second: BigIntStats): boolean {
  return first.isFile() && !first.isSymbolicLink() && first.nlink === 1n && sameFile(first, second)
    && first.size === second.size && first.mtimeNs === second.mtimeNs && first.ctimeNs === second.ctimeNs
}

async function parentSnapshots(target: string): Promise<HashedFile['parents']> {
  const root = path.parse(target).root
  const parents: { path: string; stats: BigIntStats }[] = []
  let directory = root
  for (const component of ['', ...path.relative(root, path.dirname(target)).split(path.sep).filter(Boolean)]) {
    if (component) directory = path.join(directory, component)
    const stats = await lstat(directory, { bigint: true })
    parents.push({ path: directory, stats })
  }
  await verifyParents(parents)
  return parents
}

async function verifyParents(parents: HashedFile['parents']): Promise<void> {
  for (const parent of parents) {
    const current = await lstat(parent.path, { bigint: true })
    const canonical = await realpath(parent.path)
    const sameName = process.platform === 'win32'
      ? canonical.toLowerCase() === parent.path.toLowerCase() : canonical === parent.path
    if (!current.isDirectory() || current.isSymbolicLink() || !sameFile(current, parent.stats) || !sameName) {
      throw new ImageSettingsError('unavailable')
    }
  }
}

async function hashFile(target: string, signal: AbortSignal, deadline: number): Promise<HashedFile> {
  checkAbort(signal)
  const parents = await parentSnapshots(target)
  const before = await lstat(target, { bigint: true })
  if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1n || before.size < 1n) {
    throw new ImageSettingsError('unavailable')
  }
  const handle = await open(target, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
  const hash = createHash('sha256')
  try {
    if (!sameSnapshot(await handle.stat({ bigint: true }), before)) throw new ImageSettingsError('unavailable')
    const buffer = Buffer.alloc(1024 * 1024)
    let total = 0n
    while (true) {
      checkAbort(signal)
      if (performance.now() >= deadline) throw new ImageSettingsError('timeout')
      const read = await handle.read(buffer, 0, buffer.length, null)
      if (read.bytesRead === 0) break
      total += BigInt(read.bytesRead)
      if (total > before.size) throw new ImageSettingsError('unavailable')
      hash.update(buffer.subarray(0, read.bytesRead))
    }
    if (total !== before.size || !sameSnapshot(await handle.stat({ bigint: true }), before)
      || !sameSnapshot(await lstat(target, { bigint: true }), before)) throw new ImageSettingsError('unavailable')
  } finally { await handle.close() }
  await verifyParents(parents)
  return { path: target, sha256: hash.digest('hex'), stats: before, parents }
}
