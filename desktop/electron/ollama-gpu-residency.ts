/**
 * Confirm that a trusted loopback Ollama daemon has released GPU model residency.
 *
 * Python process exit does not stop an external Ollama daemon. Main therefore
 * unloads observed GPU models and checks /api/ps before releasing GPU ownership.
 * All HTTP exchanges are proxy-free, bounded, and confined to one exact origin.
 */

import { request, type ClientRequest, type IncomingMessage } from 'node:http'

/** Absolute deadline shared by discovery, unload requests, and confirmation. */
export const OLLAMA_GPU_MAX_TIMEOUT_MS = 20_000

/** Maximum JSON bytes admitted from each local daemon response. */
export const OLLAMA_GPU_MAX_RESPONSE_BYTES = 1024 * 1024

/** Describe sanitized failures without server bodies, model names, or paths. */
export type OllamaGpuResidencyFailure =
  | 'cancelled'
  | 'invalid-origin'
  | 'invalid-response'
  | 'limit-exceeded'
  | 'timeout'
  | 'unavailable'

/** Identify failed external GPU cleanup without exposing native diagnostics. */
export class OllamaGpuResidencyError extends Error {
  /** Construct a stable public failure without retaining the original exception. */
  constructor(readonly reason: OllamaGpuResidencyFailure) {
    super(`Local model GPU cleanup failed: ${reason}.`)
    this.name = 'OllamaGpuResidencyError'
  }
}

/** Tighten trusted Main's deadline or polling interval without widening limits. */
export interface OllamaGpuResidencyOptions {
  readonly timeoutMs?: number
  readonly pollIntervalMs?: number
  /** Permit initial refusal only when no earlier inference or uncertainty requires proof. */
  readonly allowInitialRefusal?: boolean
}

interface LocalTarget {
  readonly hostname: string
  readonly port: number
}

class ConnectionRefused extends Error {}

const MAX_MODELS = 64
const MODEL_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\/[A-Za-z0-9][A-Za-z0-9_.-]*)*(?::[A-Za-z0-9][A-Za-z0-9_.-]*)?$/u

/**
 * Unload all observed GPU models and confirm empty GPU residency before returning.
 *
 * An initial connection refusal means no daemon is listening. Once GPU models
 * have been observed, any refusal or uncertain response fails closed instead
 * of assuming that an external GPU workload disappeared with Python.
 */
export async function quiesceOllama(
  origin: string,
  signal: AbortSignal = new AbortController().signal,
  options: OllamaGpuResidencyOptions = {},
): Promise<void> {
  const target = localTarget(origin)
  const timeoutMs = options.timeoutMs ?? OLLAMA_GPU_MAX_TIMEOUT_MS
  const pollIntervalMs = options.pollIntervalMs ?? 100
  const allowInitialRefusal = options.allowInitialRefusal ?? true
  if (
    !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > OLLAMA_GPU_MAX_TIMEOUT_MS
    || !Number.isSafeInteger(pollIntervalMs) || pollIntervalMs < 1 || pollIntervalMs > 1000
    || typeof allowInitialRefusal !== 'boolean'
  ) throw new OllamaGpuResidencyError('invalid-origin')
  const deadline = performance.now() + timeoutMs
  let running: unknown
  try {
    running = await invoke(target, '/api/ps', null, signal, deadline)
  } catch (error) {
    if (error instanceof ConnectionRefused) {
      if (allowInitialRefusal) return
      throw new OllamaGpuResidencyError('unavailable')
    }
    throw error
  }
  const unloaded = new Set<string>()
  for (;;) {
    const models = gpuModels(running)
    if (models.length === 0) return
    for (const model of models) {
      if (unloaded.has(model)) continue
      if (unloaded.size >= MAX_MODELS) throw new OllamaGpuResidencyError('limit-exceeded')
      let receipt: unknown
      try {
        receipt = await invoke(target, '/api/generate', { model, stream: false, keep_alive: 0 }, signal, deadline)
      } catch (error) {
        if (error instanceof ConnectionRefused) throw new OllamaGpuResidencyError('unavailable')
        throw error
      }
      const result = record(receipt)
      if (
        result.done !== true || result.error !== undefined
        || (result.model !== undefined && result.model !== model)
        || (result.done_reason !== undefined && result.done_reason !== 'unload')
        || (result.response !== undefined && result.response !== '')
      ) throw new OllamaGpuResidencyError('invalid-response')
      unloaded.add(model)
    }
    await pause(pollIntervalMs, signal, deadline)
    try {
      running = await invoke(target, '/api/ps', null, signal, deadline)
    } catch (error) {
      if (error instanceof ConnectionRefused) throw new OllamaGpuResidencyError('unavailable')
      throw error
    }
  }
}

function localTarget(value: string): LocalTarget {
  // Match the original authority before URL normalization can turn shorthand
  // IPv4 or alternate numeric spellings into an apparently allowed host.
  const matched = typeof value === 'string'
    ? /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(?::([1-9][0-9]{0,4}))?\/?$/u.exec(value)
    : null
  if (matched === null) throw new OllamaGpuResidencyError('invalid-origin')
  const port = matched[2] === undefined ? 80 : Number(matched[2])
  if (port < 1 || port > 65_535) throw new OllamaGpuResidencyError('invalid-origin')
  // Mapping localhost to a literal bypasses DNS and ambient proxy routing.
  return { hostname: matched[1] === '[::1]' ? '::1' : '127.0.0.1', port }
}

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new OllamaGpuResidencyError('invalid-response')
  }
  return value as Record<string, unknown>
}

function gpuModels(value: unknown): string[] {
  const result = record(value)
  if (!Array.isArray(result.models)) throw new OllamaGpuResidencyError('invalid-response')
  if (result.models.length > MAX_MODELS) throw new OllamaGpuResidencyError('limit-exceeded')
  const names = new Set<string>()
  for (const item of result.models) {
    const model = record(item)
    if (
      typeof model.name !== 'string' || model.name.length > 256 || !MODEL_NAME.test(model.name)
      || /(?:[:_-]cloud)(?:$|[._-])/iu.test(model.name)
      || (model.model !== undefined && model.model !== model.name)
      || (model.remote_host !== undefined && model.remote_host !== null && model.remote_host !== '')
      || (model.remote_model !== undefined && model.remote_model !== null && model.remote_model !== '')
      || !Number.isSafeInteger(model.size_vram) || (model.size_vram as number) < 0
    ) throw new OllamaGpuResidencyError('invalid-response')
    if ((model.size_vram as number) > 0) names.add(model.name)
  }
  return [...names]
}

function pause(milliseconds: number, signal: AbortSignal, deadline: number): Promise<void> {
  if (signal.aborted) return Promise.reject(new OllamaGpuResidencyError('cancelled'))
  const remaining = deadline - performance.now()
  if (remaining <= 0) return Promise.reject(new OllamaGpuResidencyError('timeout'))
  return new Promise((resolve, reject) => {
    const finish = (): void => {
      signal.removeEventListener('abort', abort)
      resolve()
    }
    const abort = (): void => {
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
      reject(new OllamaGpuResidencyError('cancelled'))
    }
    const timer = setTimeout(finish, Math.min(milliseconds, remaining))
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
  })
}

function invoke(
  target: LocalTarget,
  route: '/api/ps' | '/api/generate',
  payload: Record<string, unknown> | null,
  signal: AbortSignal,
  deadline: number,
): Promise<unknown> {
  if (signal.aborted) return Promise.reject(new OllamaGpuResidencyError('cancelled'))
  if (performance.now() >= deadline) return Promise.reject(new OllamaGpuResidencyError('timeout'))
  const body = payload === null ? null : Buffer.from(JSON.stringify(payload), 'utf8')
  return new Promise((resolve, reject) => {
    let outgoing: ClientRequest | null = null
    let incoming: IncomingMessage | null = null
    let requestClosed = false
    let responseClosed = false
    let responseEnded = false
    let received = 0
    let settled = false
    let refused = false
    let failure: OllamaGpuResidencyFailure | null = null
    const bytes = Buffer.allocUnsafe(OLLAMA_GPU_MAX_RESPONSE_BYTES)
    const finish = (): void => {
      if (settled || !requestClosed || (incoming !== null && !responseClosed)) return
      settled = true
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
      if (signal.aborted) failure = 'cancelled'
      else if (performance.now() >= deadline) failure = 'timeout'
      if (failure !== null) {
        reject(refused && failure === 'unavailable' ? new ConnectionRefused() : new OllamaGpuResidencyError(failure))
        return
      }
      if (!responseEnded) { reject(new OllamaGpuResidencyError('unavailable')); return }
      try {
        resolve(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, received))))
      } catch {
        reject(new OllamaGpuResidencyError('invalid-response'))
      }
    }
    const stop = (reason: OllamaGpuResidencyFailure): void => {
      failure ??= reason
      // Wait for both close events after cancellation; a destroyed HTTP socket
      // cannot be mistaken for a completed physical external cleanup request.
      incoming?.destroy()
      if (outgoing === null) { requestClosed = true; finish() }
      else outgoing.destroy()
    }
    const abort = (): void => stop('cancelled')
    const timer = setTimeout(() => stop('timeout'), Math.max(1, deadline - performance.now()))
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) { stop('cancelled'); return }
    try {
      outgoing = request({
        ...target,
        path: route,
        method: body === null ? 'GET' : 'POST',
        agent: false,
        maxHeaderSize: 16 * 1024,
        headers: {
          Accept: 'application/json',
          'Accept-Encoding': 'identity',
          Connection: 'close',
          ...(body === null ? {} : { 'Content-Type': 'application/json', 'Content-Length': body.length }),
        },
      }, (response) => {
        incoming = response
        response.on('close', () => { responseClosed = true; finish() })
        response.on('error', () => stop('unavailable'))
        response.on('aborted', () => stop('unavailable'))
        response.on('data', (chunk: Buffer) => {
          if (failure !== null) return
          if (!Buffer.isBuffer(chunk) || received + chunk.length > bytes.length) {
            stop('limit-exceeded')
          } else {
            chunk.copy(bytes, received)
            received += chunk.length
          }
        })
        response.on('end', () => { responseEnded = true; finish() })
        const length = response.headers['content-length']
        const type = response.headers['content-type']
        const encoding = response.headers['content-encoding']
        if (length !== undefined && (!/^[0-9]+$/u.test(length) || Number(length) > bytes.length)) {
          stop('limit-exceeded')
        } else if (
          response.statusCode !== 200 || typeof type !== 'string' || !/^application\/json(?:\s*;|$)/iu.test(type)
          || (encoding !== undefined && encoding !== 'identity')
        ) {
          stop('invalid-response')
        }
      })
      outgoing.on('error', (error: NodeJS.ErrnoException) => {
        if (error.code === 'ECONNREFUSED') refused = true
        stop('unavailable')
      })
      outgoing.on('close', () => { requestClosed = true; finish() })
      outgoing.end(body ?? undefined)
    } catch {
      stop('unavailable')
    }
  })
}
