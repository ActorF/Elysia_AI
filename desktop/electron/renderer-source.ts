/** Validate the one renderer document allowed to invoke desktop IPC. */

import path from 'node:path'
import { fileURLToPath } from 'node:url'

export interface RendererSourcePolicy {
  appPath: string
  developmentUrl: string
  isPackaged: boolean
  platform: NodeJS.Platform
}

export function isTrustedRendererUrl(
  rawUrl: string,
  policy: RendererSourcePolicy,
): boolean {
  try {
    const url = new URL(rawUrl)

    if (policy.isPackaged) {
      if (url.protocol !== 'file:') {
        return false
      }

      const actualPath = path.normalize(fileURLToPath(url))
      const expectedPath = path.normalize(
        path.join(policy.appPath, 'dist', 'index.html'),
      )
      return policy.platform === 'win32'
        ? actualPath.toLowerCase() === expectedPath.toLowerCase()
        : actualPath === expectedPath
    }

    const developmentUrl = new URL(policy.developmentUrl)
    return (
      url.origin === developmentUrl.origin
      && url.pathname === '/'
      && url.username === ''
      && url.password === ''
    )
  } catch {
    return false
  }
}
