/**
 * @fileoverview Expose one validated external Live2D model to the pet session.
 *
 * Electron Main owns native paths. The isolated renderer receives only a
 * generation-scoped custom URL whose path is resolved through an exact
 * in-memory allowlist built by the bounded model scanner. A model switch drops
 * the old registry, so cached or stale renderer URLs fail closed.
 */

import path from 'node:path'

import type {
  DesktopPetModelDescriptor,
  DesktopPetModelResource,
} from './desktop-pet-model-library.js'
import type {
  DesktopPetModelBootstrap,
} from './desktop-pet-contracts.js'

/** Custom protocol reserved for a selected external desktop-pet model. */
export const LIVE2D_ASSET_SCHEME = 'elysia-pet-asset'

/** Fixed authority used by every generation-scoped model URL. */
export const LIVE2D_ASSET_HOST = 'model'

/** Browser capabilities required by `fetch` inside the isolated pet session. */
export const LIVE2D_ASSET_PRIVILEGES = Object.freeze({
  standard: true,
  secure: true,
  supportFetchAPI: true,
  corsEnabled: true,
})

/** Session partition that alone receives the external model protocol handler. */
export const LIVE2D_ASSET_DESKTOP_PET_PARTITION = 'elysia-desktop-pet'

/** Absolute response ceiling matching the scanner's largest permitted file. */
export const LIVE2D_ASSET_MAXIMUM_BYTES = 64 * 1024 * 1024

/** One exact file mapping retained only inside Electron Main. */
export interface ResolvedLive2DAsset {
  readonly absolutePath: string
  readonly sizeBytes: number
}

/**
 * Materialize one exact asset body before it crosses the custom protocol.
 *
 * Returning bytes instead of the original stream lets a registry rotation
 * revoke a request while native file I/O is still in flight. The pre-sized
 * buffer also prevents a file replaced after `lstat` from expanding beyond
 * its scanner-admitted length.
 */
export async function materializeLive2DAssetResponse(
  response: Response,
  expectedBytes: number,
  isGenerationCurrent: () => boolean,
): Promise<Uint8Array | null> {
  if (
    response.body === null
    || !Number.isSafeInteger(expectedBytes)
    || expectedBytes <= 0
    || expectedBytes > LIVE2D_ASSET_MAXIMUM_BYTES
    || !isGenerationCurrent()
  ) {
    await response.body?.cancel().catch(() => {})
    return null
  }
  const reader = response.body.getReader()
  const bytes = new Uint8Array(expectedBytes)
  let offset = 0
  try {
    while (true) {
      const result = await reader.read()
      if (result.done) {
        return offset === expectedBytes && isGenerationCurrent()
          ? bytes
          : null
      }
      if (
        result.value.byteLength === 0
        || offset + result.value.byteLength > expectedBytes
      ) {
        await reader.cancel().catch(() => {})
        return null
      }
      bytes.set(result.value, offset)
      offset += result.value.byteLength
    }
  } catch {
    return null
  } finally {
    reader.releaseLock()
  }
}

function encodePath(segments: readonly string[]): string {
  return `/${segments.map((segment) => encodeURIComponent(segment)).join('/')}`
}

function decodeCanonicalPath(pathname: string): string | null {
  const rawSegments = pathname.split('/')
  if (rawSegments[0] !== '') {
    return null
  }
  const decoded: string[] = []
  for (const rawSegment of rawSegments.slice(1)) {
    let segment: string
    try {
      segment = decodeURIComponent(rawSegment)
    } catch {
      return null
    }
    if (
      segment.length === 0
      || segment === '.'
      || segment === '..'
      || /[\p{Cc}\\/%]/u.test(segment)
    ) {
      // Slashes, backslashes, and repeated percent decoding would otherwise
      // create alternate spellings of an allowlisted native path.
      return null
    }
    decoded.push(segment)
  }
  return `/${decoded.join('/')}`
}

function relativeResourceSegments(
  model: DesktopPetModelDescriptor,
  resource: DesktopPetModelResource,
): readonly string[] | null {
  const relative = path.relative(
    path.dirname(model.manifestPath),
    resource.absolutePath,
  )
  if (
    relative === ''
    || relative === '..'
    || relative.startsWith(`..${path.sep}`)
    || path.isAbsolute(relative)
  ) {
    return null
  }
  const segments = relative.split(path.sep)
  if (segments.some((segment) => (
    segment.length === 0
    || segment === '.'
    || segment === '..'
    || /[\p{Cc}\\/%]/u.test(segment)
  ))) {
    return null
  }
  return segments
}

/**
 * Hold the exact current-model allowlist used by the pet protocol handler.
 *
 * The generation is supplied by Main and changes whenever a folder is rescanned
 * or a model is selected. It uses ASCII hex so URLs need no hidden path state.
 */
export class Live2DAssetRegistry {
  readonly #assets: ReadonlyMap<string, ResolvedLive2DAsset>
  readonly #bootstrap: DesktopPetModelBootstrap | null

  /** Build an empty or single-model registry from scanner-validated resources. */
  constructor(
    model: DesktopPetModelDescriptor | null,
    generation: string,
  ) {
    if (!/^[0-9a-f]{16,64}$/u.test(generation)) {
      throw new Error('Live2D asset generation is invalid.')
    }
    if (model === null) {
      this.#assets = new Map()
      this.#bootstrap = null
      return
    }

    const prefix = [generation, model.id]
    const routes = new Map<string, ResolvedLive2DAsset>()
    let manifestUrl: string | null = null
    for (const resource of model.resources) {
      const relativeSegments = resource.kind === 'manifest'
        ? [path.basename(model.manifestPath)]
        : relativeResourceSegments(model, resource)
      if (relativeSegments === null) {
        throw new Error('Live2D resource is outside its model directory.')
      }
      const decodedRoute = `/${[...prefix, ...relativeSegments].join('/')}`
      if (routes.has(decodedRoute)) {
        throw new Error('Live2D model contains an ambiguous resource path.')
      }
      routes.set(decodedRoute, Object.freeze({
        absolutePath: resource.absolutePath,
        sizeBytes: resource.sizeBytes,
      }))
      if (resource.kind === 'manifest') {
        manifestUrl = `${LIVE2D_ASSET_SCHEME}://${LIVE2D_ASSET_HOST}${encodePath([
          ...prefix,
          ...relativeSegments,
        ])}`
      }
    }
    if (manifestUrl === null) {
      throw new Error('Live2D model has no validated manifest.')
    }
    this.#assets = routes
    this.#bootstrap = Object.freeze({
      modelId: model.id,
      manifestUrl,
    })
  }

  /** Return the renderer-safe selected-model URL, or null when unconfigured. */
  bootstrap(): DesktopPetModelBootstrap | null {
    return this.#bootstrap
  }

  /** Resolve only one canonical URL from the current generation's allowlist. */
  resolve(rawUrl: string): ResolvedLive2DAsset | null {
    if (
      typeof rawUrl !== 'string'
      || rawUrl.length === 0
      || rawUrl.trim() !== rawUrl
      || rawUrl.includes('?')
      || rawUrl.includes('#')
      || /[\0-\x20\x7f\\]/u.test(rawUrl)
    ) {
      return null
    }
    const authorityStart = rawUrl.indexOf('://')
    const pathStart = authorityStart < 0
      ? -1
      : rawUrl.indexOf('/', authorityStart + 3)
    const canonicalRawPath = pathStart < 0
      ? null
      : decodeCanonicalPath(rawUrl.slice(pathStart))
    if (canonicalRawPath === null) {
      return null
    }
    let parsed: URL
    try {
      parsed = new URL(rawUrl)
    } catch {
      return null
    }
    if (
      parsed.protocol !== `${LIVE2D_ASSET_SCHEME}:`
      || parsed.hostname !== LIVE2D_ASSET_HOST
      || parsed.port !== ''
      || parsed.username !== ''
      || parsed.password !== ''
      || parsed.search !== ''
      || parsed.hash !== ''
    ) {
      return null
    }
    return this.#assets.get(canonicalRawPath) ?? null
  }
}
