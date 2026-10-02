/**
 * @fileoverview Resolve the three packaged Live2D files exposed to renderers.
 *
 * The application uses a narrow custom protocol because a packaged `file:`
 * document cannot reliably fetch adjacent binary model data. This module keeps
 * that protocol from becoming a general-purpose file reader: every accepted
 * URL maps to one immutable, application-owned path below the renderer bundle.
 */

import path from 'node:path'

/** Custom protocol used only by packaged renderers for Live2D model fetches. */
export const LIVE2D_ASSET_SCHEME = 'elysia-asset'

/** Fixed authority that separates character assets from future protocols. */
export const LIVE2D_ASSET_HOST = 'character'

/** Browser capabilities required for local model fetches without CSP bypass. */
export const LIVE2D_ASSET_PRIVILEGES = Object.freeze({
  standard: true,
  secure: true,
  supportFetchAPI: true,
  corsEnabled: true,
})

/** Isolated Electron partition that also renders the packaged character. */
export const LIVE2D_ASSET_DESKTOP_PET_PARTITION = 'elysia-desktop-pet'

/** Absolute response-size ceiling applied before the renderer sees a file. */
export const LIVE2D_ASSET_MAXIMUM_BYTES = 8 * 1024 * 1024

const LIVE2D_ASSET_ROOT_SEGMENTS = [
  'dist',
  'character',
  'live2d',
  'elysia',
] as const

const ALLOWED_ASSET_PATHS = new Map<string, readonly string[]>([
  [
    '/live2d/elysia/model.model3.json',
    ['model.model3.json'],
  ],
  [
    '/live2d/elysia/model.moc3',
    ['model.moc3'],
  ],
  [
    '/live2d/elysia/textures/atlas.png',
    ['textures', 'atlas.png'],
  ],
])

function hasForbiddenRawUrlCharacter(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index)
    if (codeUnit <= 0x20 || codeUnit === 0x7f || codeUnit === 0x5c) {
      return true
    }
  }
  return false
}

/**
 * Resolve one custom-protocol request to its packaged file.
 *
 * Returns `null` for every URL outside the exact three-file allowlist. The raw
 * URL is checked before WHATWG normalization because URL parsing erases dot
 * segments; accepting the normalized result would make traversal-shaped input
 * indistinguishable from a canonical application request.
 */
export function resolveLive2DAssetPath(
  rawUrl: string,
  applicationPath: string,
): string | null {
  if (
    rawUrl === ''
    || rawUrl.trim() !== rawUrl
    || rawUrl.includes('?')
    || rawUrl.includes('#')
    || rawUrl.includes('%')
    || hasForbiddenRawUrlCharacter(rawUrl)
  ) {
    // Percent escapes are unnecessary for the ASCII-only allowlist. Rejecting
    // all of them also closes encoded slash, backslash, and dot-segment forms.
    return null
  }

  const authorityStart = rawUrl.indexOf('://')
  if (authorityStart < 0) {
    return null
  }
  const pathStart = rawUrl.indexOf('/', authorityStart + 3)
  const rawPath = pathStart < 0 ? '' : rawUrl.slice(pathStart)
  if (/(?:^|\/)\.{1,2}(?:\/|$)/u.test(rawPath)) {
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

  const relativeSegments = ALLOWED_ASSET_PATHS.get(parsed.pathname)
  if (relativeSegments === undefined) {
    return null
  }

  const assetRoot = path.resolve(applicationPath, ...LIVE2D_ASSET_ROOT_SEGMENTS)
  const candidate = path.resolve(assetRoot, ...relativeSegments)
  const relative = path.relative(assetRoot, candidate)
  if (
    relative === ''
    || relative === '..'
    || relative.startsWith(`..${path.sep}`)
    || path.isAbsolute(relative)
  ) {
    return null
  }
  return candidate
}
