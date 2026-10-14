/** Bind local OCR evidence to canonical image identity while retaining each cancelled callback's physical lease. */

import {
  parseVisionAnalysisState, parseVisionOcrReadiness, parseVisionOcrRequest, parseVisionOcrResult, visionAnalysisError,
  type VisionAnalysisErrorCode, type VisionAnalysisImage, type VisionAnalysisState, type VisionOcrLanguage,
  type VisionOcrReadiness, type VisionOcrRequest, type VisionOcrResult,
} from './vision-analysis-protocol.js'
import { parseVisionChatId } from './vision-protocol.js'
import type { CanonicalVisionImage, VisionOwner } from './vision-images.js'

/** Inject only authoritative image access and fixed local OCR operations; no renderer path or command is accepted. */
export interface VisionAnalysisDependencies {
  /** Return the live canonical owner, excluding this service's own native lease. */
  getOwner(): VisionOwner
  /** Read path-free image identity without copying original pixels during status polls. */
  getImageIdentity(chatId: string, imageId: string): VisionAnalysisImage | null
  /** Resolve one owned detached canonical PNG only after explicit recognition admission. */
  getImage(chatId: string, imageId: string): CanonicalVisionImage
  /** Probe only the existing local OCR executable and language data; settle after native I/O closes. */
  readiness(signal: AbortSignal): Promise<VisionOcrReadiness>
  /** Recognize exact canonical pixels with a closed language choice; settle after the process closes. */
  recognize(image: CanonicalVisionImage, language: VisionOcrLanguage, signal: AbortSignal): Promise<VisionOcrResult>
}

/** Sanitize service-level authority failures independently of native diagnostics or recognized text. */
export class VisionAnalysisServiceError extends Error {
  /** Attach only a closed category and fixed public message. */
  constructor(readonly code: VisionAnalysisErrorCode) { super(visionAnalysisError(code).message) }
}

interface ActiveAnalysis {
  owner: VisionOwner
  image: VisionAnalysisImage | null
  phase: 'readiness' | 'ocr'
  abort: AbortController
}

function sameOwner(left: VisionOwner, right: VisionOwner): boolean {
  return left.chatId === right.chatId && left.projectId === right.projectId && left.mode === right.mode
}

function sameImage(left: VisionAnalysisImage | null, right: VisionAnalysisImage | null): boolean {
  return left !== null && right !== null && left.imageId === right.imageId && left.sha256 === right.sha256
    && left.width === right.width && left.height === right.height
}

function failure(error: unknown): VisionAnalysisErrorCode {
  try {
    if (typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string') {
      visionAnalysisError(error.code as VisionAnalysisErrorCode)
      return error.code as VisionAnalysisErrorCode
    }
  } catch { /* An adapter's unrecognized category is not a public diagnostic. */ }
  return 'native_failed'
}

/** Maintain current scoped OCR evidence and one global callback lease across navigation, cancellation, and shutdown. */
export class VisionAnalysisController {
  private owner: VisionOwner | null = null
  private image: VisionAnalysisImage | null = null
  private readiness: VisionOcrReadiness | null = null
  private result: VisionOcrResult | null = null
  private error: VisionAnalysisErrorCode | null = null
  private active: ActiveAnalysis | null = null
  private revision = 0
  private closed = false

  /** Construct inert adapters; no readiness check, image access, recognition, or capture starts here. */
  constructor(private readonly dependencies: VisionAnalysisDependencies) {}

  /** Revoke stale evidence and request stop without clearing a physically active callback's lease. */
  synchronize(): void {
    const current = this.dependencies.getOwner()
    const invalidOwner = !current.ready || current.archived || this.closed
      || this.owner !== null && !sameOwner(this.owner, current)
    if (invalidOwner) {
      this.scrub()
      this.owner = null
      this.active?.abort.abort()
      return
    }
    if (this.image !== null) {
      let currentImage: VisionAnalysisImage | null = null
      try { currentImage = this.dependencies.getImageIdentity(current.chatId, this.image.imageId) } catch { /* Missing authority revokes evidence. */ }
      if (!sameImage(this.image, currentImage)) {
        this.image = null; this.result = null; this.error = null; this.revision += 1
        this.active?.abort.abort()
      }
    }
  }

  /** Read detached current evidence while exposing any retired operation's still-active global native capacity. */
  getState(chatId: string): VisionAnalysisState {
    const owner = this.authority(chatId, false)
    return this.snapshot(owner)
  }

  /** Report actual callback ownership after cancellation, owner changes, or shutdown. */
  hasActiveWork(): boolean { return this.active !== null }

  /** Check only the existing local OCR installation and resolve after all actual probe callbacks settle. */
  async checkOcr(chatId: string): Promise<VisionAnalysisState> {
    const owner = this.authority(chatId, true)
    return this.perform({ owner, image: null, phase: 'readiness', abort: new AbortController() }, async (active) => {
      const readiness = parseVisionOcrReadiness(await this.dependencies.readiness(active.abort.signal))
      this.recheck(active)
      this.readiness = readiness
    })
  }

  /** Recognize one explicit immutable image and publish only evidence validated against its unchanged owner and hash. */
  async runOcr(request: VisionOcrRequest): Promise<VisionAnalysisState> {
    const parsed = parseVisionOcrRequest(request), owner = this.authority(parsed.chatId, true)
    const identity = this.dependencies.getImageIdentity(parsed.chatId, parsed.imageId)
    if (identity === null) throw new VisionAnalysisServiceError('invalid_image')
    const image = this.dependencies.getImage(parsed.chatId, parsed.imageId)
    if (!sameImage(identity, { imageId: parsed.imageId, sha256: image.sha256, width: image.width, height: image.height })) {
      throw new VisionAnalysisServiceError('owner_changed')
    }
    this.image = { ...identity }; this.result = null; this.error = null
    return this.perform({ owner, image: { ...identity }, phase: 'ocr', abort: new AbortController() }, async (active) => {
      const result = parseVisionOcrResult(await this.dependencies.recognize(image, parsed.language, active.abort.signal), identity)
      if (result.language !== parsed.language) throw new VisionAnalysisServiceError('invalid_output')
      this.recheck(active)
      this.result = result
    })
  }

  /** Request a prompt stop and return truthful busy progress rather than waiting behind the native process. */
  cancel(chatId: string): VisionAnalysisState {
    const owner = this.authority(chatId, false)
    if (this.active !== null) {
      this.active.abort.abort(); this.result = null; this.error = 'cancelled'; this.revision += 1
    }
    return this.snapshot(owner)
  }

  /** Close admission and scrub all evidence while leaving a cancelled native callback physically owned until return. */
  shutdown(): void {
    this.closed = true; this.scrub(); this.owner = null; this.active?.abort.abort()
  }

  private authority(chatId: string, admission: boolean): VisionOwner {
    parseVisionChatId(chatId)
    this.synchronize()
    const current = { ...this.dependencies.getOwner() }
    if (this.closed || !current.ready || current.archived) throw new VisionAnalysisServiceError('unavailable')
    if (current.chatId !== chatId) throw new VisionAnalysisServiceError('owner_changed')
    if (admission && (this.active !== null || current.busy)) throw new VisionAnalysisServiceError('busy')
    this.owner = current
    return current
  }

  private recheck(active: ActiveAnalysis): void {
    const current = this.dependencies.getOwner()
    if (this.closed || this.active !== active || active.abort.signal.aborted || !current.ready || current.archived
      || !sameOwner(active.owner, current)) throw new VisionAnalysisServiceError(active.abort.signal.aborted ? 'cancelled' : 'owner_changed')
    if (active.image !== null && !sameImage(active.image, this.dependencies.getImageIdentity(current.chatId, active.image.imageId))) {
      throw new VisionAnalysisServiceError('owner_changed')
    }
  }

  private async perform(active: ActiveAnalysis, operation: (active: ActiveAnalysis) => Promise<void>): Promise<VisionAnalysisState> {
    this.active = active; this.error = null; this.revision += 1
    try {
      this.recheck(active)
      await operation(active)
    } catch (error) {
      // A late old-owner error is no more publishable than its late text. Only
      // the current admitted scope may receive a fixed diagnostic category.
      const current = this.dependencies.getOwner()
      if (!this.closed && current.ready && !current.archived && sameOwner(active.owner, current)
        && (active.image === null || sameImage(active.image, this.dependencies.getImageIdentity(current.chatId, active.image.imageId)))) {
        this.error = active.abort.signal.aborted ? 'cancelled' : error instanceof TypeError ? 'invalid_output' : failure(error)
        this.result = null
      }
    } finally {
      // Only this exact returning callback releases capacity. Abort, navigation,
      // and logical error publication never claim the process already stopped.
      if (this.active === active) this.active = null
      this.revision += 1
    }
    return this.getState(active.owner.chatId)
  }

  private scrub(): void {
    if (this.image !== null || this.readiness !== null || this.result !== null || this.error !== null) this.revision += 1
    this.image = null; this.readiness = null; this.result = null; this.error = null
  }

  private snapshot(owner: VisionOwner): VisionAnalysisState {
    return parseVisionAnalysisState({ kind: 'vision.analysis', owner: { chatId: owner.chatId, projectId: owner.projectId },
      revision: this.revision, busy: this.active !== null, cancelRequested: this.active?.abort.signal.aborted ?? false,
      phase: this.active?.phase ?? null, image: this.image,
      ocr: { readiness: this.readiness, result: this.result, error: this.error === null ? null : visionAnalysisError(this.error) } })
  }
}
