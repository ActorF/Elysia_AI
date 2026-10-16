/**
 * Hold native GPU turns through Ollama residency checks and physical cleanup.
 * Failed unload confirmation retains its exact lease for a later retry; an
 * HTTP cancellation or timeout cannot manufacture free GPU memory.
 */

import { GpuAdmissionCoordinator, GpuAdmissionError, type GpuLease, type GpuOwner } from './gpu-admission.js'
import { quiesceOllama, type OllamaGpuResidencyOptions } from './ollama-gpu-residency.js'

/** Inject only trusted settings and a bounded local model-unload implementation. */
export interface NativeGpuWorkloadOptions {
  /** Read the applied Backend origin without taking GPU capacity. */
  readonly getOllamaOrigin: () => Promise<string>
  /** Confirm no Ollama model remains GPU-resident, including after abort. */
  readonly quiesce?: (origin: string, signal?: AbortSignal, options?: OllamaGpuResidencyOptions) => Promise<void>
}

/** Own native admission and retain unverifiable model residency across failures. */
export class NativeGpuWorkloads {
  private readonly quarantine = new Map<GpuLease, string>()
  private readonly origins = new WeakMap<GpuLease, string>()
  private readonly uncertainOrigins = new Set<string>()
  private recovering: Promise<void> | null = null
  private readonly quiesce: NonNullable<NativeGpuWorkloadOptions['quiesce']>

  /** Share Main's one coordinator; no process or network work starts here. */
  constructor(private readonly admission: GpuAdmissionCoordinator,
    private readonly options: NativeGpuWorkloadOptions) {
    this.quiesce = options.quiesce ?? quiesceOllama
  }

  /** Admit a physical worker only after prior Ollama residency is cleared. */
  async acquire(owner: GpuOwner, signal?: AbortSignal): Promise<GpuLease> {
    await this.recover()
    const origin = await this.options.getOllamaOrigin()
    const lease = await this.admission.acquire(owner, { signal })
    try {
      await this.prepare(owner, origin, lease.signal)
      if (lease.signal.aborted) throw new Error()
      this.origins.set(lease, origin)
      return lease
    } catch {
      // Use a fresh cleanup deadline even when the request signal is aborted.
      // The caller has not started a worker and cannot own this cleanup lease.
      this.quarantine.set(lease, origin)
      await this.recover().catch(() => {})
      throw new Error('The local GPU could not be prepared.')
    }
  }

  /**
   * Prepare a held native or Python grant using one shared residency history.
   *
   * An initial refusal is safe only for an origin with no known workload or
   * failed cleanup. Record uncertainty before awaiting HTTP so cancellation
   * after observing a model cannot be forgotten by a fresh recovery call.
   */
  async prepare(owner: GpuOwner, origin: string, signal: AbortSignal): Promise<void> {
    const allowInitialRefusal = !this.uncertainOrigins.has(origin)
    this.uncertainOrigins.add(origin)
    try {
      await this.quiesce(origin, signal, { allowInitialRefusal })
      if (signal.aborted) throw new Error()
    } catch {
      throw new Error('The local GPU could not be prepared.')
    }
    if (owner !== 'ollama') this.uncertainOrigins.delete(origin)
  }

  /**
   * Confirm a live daemon reports no GPU residency before releasing uncertainty.
   *
   * Python exit and native HTTP failure cannot prove that an external runner
   * stopped. Every post-work or quarantine retry therefore requires a positive
   * response, preserving uncertainty across repeated connection refusals.
   */
  async quiesceExternal(origin: string): Promise<void> {
    await this.quiesce(origin, undefined, { allowInitialRefusal: false })
    this.uncertainOrigins.delete(origin)
  }

  /** Run local Ollama inference and confirm unload before releasing its turn. */
  async runOllama<T>(signal: AbortSignal, operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const lease = await this.acquire('ollama', signal)
    const origin = this.origins.get(lease)!
    try {
      if (lease.signal.aborted) throw new GpuAdmissionError('cancelled')
      const result = await operation(lease.signal)
      if (lease.signal.aborted) throw new GpuAdmissionError('cancelled')
      return result
    } finally {
      this.quarantine.set(lease, origin)
      await this.recover()
    }
  }

  /** Retry retained native unloads; release only individually confirmed leases. */
  recover(): Promise<void> {
    if (this.recovering !== null) return this.recovering
    const operation = this.recoverOnce()
    this.recovering = operation
    const clear = (): void => { if (this.recovering === operation) this.recovering = null }
    void operation.then(clear, clear)
    return operation
  }

  private async recoverOnce(): Promise<void> {
    for (const [lease, origin] of this.quarantine) {
      try { await this.quiesceExternal(origin) }
      catch { throw new Error('The local GPU is still awaiting model cleanup.') }
      this.quarantine.delete(lease)
      lease.release()
    }
  }
}
