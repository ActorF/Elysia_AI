/** Validate the one renderer document allowed to invoke desktop IPC. */

import path from 'node:path'
import { fileURLToPath } from 'node:url'

export interface RendererSourcePolicy {
  appPath: string
  developmentUrl: string
  isPackaged: boolean
  platform: NodeJS.Platform
}

/** One exact packaged filename and development path accepted for a renderer. */
export interface RendererEntryPoint {
  developmentPath: string
  packagedFileName: string
}

/**
 * Check an IPC caller against one exact renderer entry point.
 *
 * Requiring the exact HTML file and development pathname prevents an iframe
 * or future secondary surface from borrowing the main renderer's IPC
 * authority merely because it shares an origin.
 */
export function isTrustedRendererEntryUrl(
  rawUrl: string,
  policy: RendererSourcePolicy,
  entryPoint: RendererEntryPoint,
): boolean {
  try {
    const url = new URL(rawUrl)
    if (
      url.username !== ''
      || url.password !== ''
      || url.search !== ''
      || url.hash !== ''
    ) {
      return false
    }

    if (policy.isPackaged) {
      if (url.protocol !== 'file:') {
        return false
      }

      const actualPath = path.normalize(fileURLToPath(url))
      const expectedPath = path.normalize(
        path.join(policy.appPath, 'dist', entryPoint.packagedFileName),
      )
      return policy.platform === 'win32'
        ? actualPath.toLowerCase() === expectedPath.toLowerCase()
        : actualPath === expectedPath
    }

    const developmentUrl = new URL(policy.developmentUrl)
    return (
      url.origin === developmentUrl.origin
      && url.pathname === entryPoint.developmentPath
    )
  } catch {
    return false
  }
}

/** Check that an IPC caller is exactly the ordinary main renderer. */
export function isTrustedRendererUrl(
  rawUrl: string,
  policy: RendererSourcePolicy,
): boolean {
  return isTrustedRendererEntryUrl(rawUrl, policy, {
    developmentPath: '/',
    packagedFileName: 'index.html',
  })
}
